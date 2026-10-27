"""Durable account observations and deletion/upsert serialization."""
import json
import logging
import math
import os
import threading
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from agent_monitor import file_io
import generation
import quota_applicability
from parsers.accounts import quota_identity

logger = logging.getLogger("agent-monitor")
VERSION = 2

UNSPECIFIED_ACCOUNT_LABEL = object()

ENTRY_FIELDS = frozenset(
    {
        "provider",
        "account_id",
        "account_label",
        "account_plan",
        "five_hour_used_pct",
        "five_hour_resets_at",
        "seven_day_used_pct",
        "seven_day_resets_at",
        "observed_at",
    }
)

NUMERIC_FIELDS = (
    "five_hour_used_pct",
    "five_hour_resets_at",
    "seven_day_used_pct",
    "seven_day_resets_at",
)


def account_memory_entry(provider, entry):
    if not isinstance(entry, dict) or entry.get("account_state") != "known":
        return None
    account_id = entry.get("account_id")
    evidence = quota_applicability.observation(entry.get("quota_observation"))
    observed_at = parse_observed_at(evidence["observed_at"] if evidence and evidence["observed_at"] else entry.get("updated_at"))
    if (
        provider not in ("claude", "codex")
        or not isinstance(account_id, str)
        or not account_id
    ):
        return None
    if observed_at is None:
        return None
    record = {
        "provider": provider,
        "account_id": account_id,
        "account_label": entry.get("account_label"),
        "account_plan": entry.get("account_plan"),
        "five_hour_used_pct": quota_number(entry.get("five_hour_used_pct")),
        "five_hour_resets_at": quota_number(entry.get("five_hour_resets_at")),
        "seven_day_used_pct": quota_number(entry.get("seven_day_used_pct")),
        "seven_day_resets_at": quota_number(entry.get("seven_day_resets_at")),
        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
    }
    evidence = quota_applicability.observation(entry.get("quota_observation"))
    if evidence and evidence["observed_at"]:
        record["quota_observation"] = evidence
        record["reading_from"] = text(entry.get("reading_from"))
    return account_key(provider, record), record


def normalized_account_memory_record(record):
    normalized = dict(record)
    for field in NUMERIC_FIELDS:
        normalized[field] = quota_number(normalized.get(field))
    return normalized


def valid_account_memory(payload):
    if not isinstance(payload, dict) or set(payload) != {"version", "accounts"}:
        return False
    if payload.get("version") not in (1, VERSION):
        return False
    accounts = payload.get("accounts")
    if not isinstance(accounts, dict):
        return False
    for key, record in accounts.items():
        if not isinstance(key, str) or not isinstance(record, dict):
            return False
        if not ENTRY_FIELDS <= set(record) or not set(record) <= ENTRY_FIELDS | {"quota_observation", "reading_from", "legacy_observation"}:
            return False
        if "quota_observation" in record and quota_applicability.observation(record["quota_observation"]) is None:
            return False
        if "reading_from" in record and record["reading_from"] is not None and not isinstance(record["reading_from"], str):
            return False
        if "legacy_observation" in record and (not isinstance(record["legacy_observation"], dict)
                or set(record["legacy_observation"]) != ENTRY_FIELDS):
            return False
        provider = record.get("provider")
        account_id = record.get("account_id")
        if provider not in ("claude", "codex"):
            return False
        if not isinstance(account_id, str) or not account_id:
            return False
        expected_key = ("%s:%s" % (provider, account_id) if payload["version"] == 1
                        else account_key(provider, record))
        if key != expected_key:
            return False
        if record.get("account_label") is not None and not isinstance(
            record["account_label"], str
        ):
            return False
        if record.get("account_plan") is not None and not isinstance(
            record["account_plan"], str
        ):
            return False
        if parse_observed_at(record.get("observed_at")) is None:
            return False
    return True


def parse_observed_at(value):
    """Observation time, always tz-aware, so readings stay comparable.

    Codex rollouts carry tz-naive timestamps (`parsers/codex.py` normalizes them
    for its own `max()`, and `tests/test_codex_rate_limits.py` pins that they
    occur), while Claude's are offset-aware. Comparing one of each raises, and
    both comparisons here span machines — so one machine with a naive timestamp
    would take out the Overview for all of them. Naive is read as UTC, which is
    what the exporters mean by it.
    """
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def identity_id(block):
    """The account id, or None. Anything that is not a usable id reads as None.

    The generation schema validates `rate_limits` only as "an object" — nothing
    checks what is inside it — and this value is now a dict key and a rendered
    string. A machine publishing a malformed field must cost its own quota row,
    not the whole Overview payload it happens to travel in.
    """
    account_id = block.get("account_id")
    return account_id if isinstance(account_id, str) and account_id else None


def account_key(provider, block):
    return quota_identity(provider, identity_id(block), block.get("account_label"))


def quota_number(value):
    """A standard-JSON number, or None when one malformed field is unusable.

    `rate_limits` blocks intentionally have no inner wire schema. Normalizing at
    each server-side boundary keeps one machine or remembered record's bad
    field from turning the whole Overview response into JSON the browser cannot
    parse, without rejecting the other rows in the same memory file.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def text(value):
    return value if isinstance(value, str) and value else None


def parse_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class AccountMemory:
    """Own one account-memory file and its in-process deletion epochs."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.delete_epoch = 0
        self._active_upsert_epochs = defaultdict(int)
        self._deleted_at_epoch = {}

    def read(self):
        with self._lock:
            return self._load()

    def epoch_status(self):
        with self._lock:
            return {"delete_epoch": self.delete_epoch,
                    "active_upsert_epochs": dict(self._active_upsert_epochs),
                    "deleted_at_epoch": dict(self._deleted_at_epoch)}

    @contextmanager
    def upsert_epoch(self):
        with self._lock:
            epoch = self.delete_epoch
            self._active_upsert_epochs[epoch] += 1
        try:
            yield epoch
        finally:
            with self._lock:
                remaining = self._active_upsert_epochs[epoch] - 1
                if remaining:
                    self._active_upsert_epochs[epoch] = remaining
                else:
                    del self._active_upsert_epochs[epoch]
                self._prune_deletions_locked()


    def _prune_deletions_locked(self):
        if not self._active_upsert_epochs:
            self._deleted_at_epoch.clear()
            return
        oldest_active_epoch = min(self._active_upsert_epochs)
        for key, deleted_at_epoch in tuple(self._deleted_at_epoch.items()):
            if deleted_at_epoch <= oldest_active_epoch:
                del self._deleted_at_epoch[key]


    def upsert(self, rate_limits, *, expected_delete_epoch):
        candidates = []
        for provider in ("claude", "codex"):
            provider_limits = rate_limits.get(provider)
            if not isinstance(provider_limits, dict):
                continue
            for entry in provider_limits.get("accounts", ()):
                candidate = account_memory_entry(provider, entry)
                if candidate is not None:
                    candidates.append(candidate)
        if not candidates:
            return False

        with self._lock:
            state, payload = self._load()
            if state == "unreadable_or_unsupported":
                return False
            accounts = {
                key: normalized_account_memory_record(record)
                for key, record in payload["accounts"].items()
            }
            changed = False
            for key, candidate in candidates:
                if (
                    self._deleted_at_epoch.get(key, 0)
                    > expected_delete_epoch
                ):
                    continue
                existing = accounts.get(key)
                if existing is not None:
                    candidate_time = parse_observed_at(candidate["observed_at"])
                    existing_time = parse_observed_at(existing["observed_at"])
                    old = quota_applicability.observation(existing.get("quota_observation"))
                    new = quota_applicability.observation(candidate.get("quota_observation"))
                    old_trusted, new_trusted = bool(candidate["provider"] == "claude" and old and old["observed_at"]), bool(candidate["provider"] == "claude" and new and new["observed_at"])
                    if (new_trusted, candidate_time) <= (old_trusted, existing_time):
                        continue
                    if new_trusted and not old_trusted:
                        candidate["legacy_observation"] = {field: existing[field] for field in ENTRY_FIELDS}
                    elif existing.get("legacy_observation"):
                        candidate["legacy_observation"] = existing["legacy_observation"]
                accounts[key] = candidate
                changed = True
            if not changed:
                return False
            self._write(
                {
                    "version": VERSION,
                    "accounts": accounts,
                }
            )
            return True


    def remove(self, provider, account_id, observed_at, live_rate_limits, *,
                               account_label=UNSPECIFIED_ACCOUNT_LABEL):
        with self._lock:
            provider_limits = live_rate_limits.get(provider)
            entries = (
                provider_limits.get("accounts", ())
                if isinstance(provider_limits, dict)
                else ()
            )
            state, payload = self._load()
            if state == "unreadable_or_unsupported":
                return 500, {"error": "账号记录不可读或不受支持"}
            if account_label is UNSPECIFIED_ACCOUNT_LABEL:
                candidates = {
                    account_key(provider, record)
                    for record in list(payload["accounts"].values()) + list(entries)
                    if record.get("account_id") == account_id
                    and record.get("provider", provider) == provider
                }
                if len(candidates) > 1:
                    return 409, {"error": "同一工作区有多个账号，请刷新页面后按邮箱移除。"}
                key = next(iter(candidates), quota_identity(provider, account_id))
            else:
                key = quota_identity(provider, account_id, account_label)
            in_use_entries = [
                entry
                for entry in entries
                if entry.get("account_state") == "known"
                and account_key(provider, entry) == key
            ]
            in_use_machines = sorted(
                {
                    machine
                    for entry in in_use_entries
                    for machine in entry.get("machines", ())
                }
            )
            if in_use_entries:
                return 409, {
                    "error": "该账号仍在使用中：%s"
                    % ", ".join(in_use_machines),
                    "machines": in_use_machines,
                }
            unstamped_entries = [
                entry for entry in entries if entry.get("account_state") == "unstamped"
            ]
            unstamped_machines = sorted(
                {
                    machine
                    for entry in unstamped_entries
                    for machine in entry.get("machines", ())
                }
            )
            if unstamped_entries:
                return 409, {
                    "error": (
                        "无法确认账号归属，请更新这些机器上的 agent-monitor：%s"
                        % ", ".join(unstamped_machines)
                    ),
                    "machines": unstamped_machines,
                }

            accounts = dict(payload["accounts"])
            record = accounts.get(key)
            if record is None:
                return 404, {"error": "找不到该历史账号"}
            if record["observed_at"] != observed_at:
                return 409, {
                    "error": (
                        "这条历史账号在你确认之后发生了变化；"
                        "请刷新页面、看过最新读数再移除"
                    )
                }
            del accounts[key]
            self._write(
                {
                    "version": VERSION,
                    "accounts": accounts,
                }
            )
            self.delete_epoch += 1
            self._deleted_at_epoch[key] = self.delete_epoch
            self._prune_deletions_locked()
            return 200, {
                "account_label": record["account_label"],
                "observed_at": record["observed_at"],
            }


    def _load(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return "missing", {"version": VERSION, "accounts": {}}
        except OSError as exc:
            logger.warning("account memory is unreadable: %s: %s", type(exc).__name__, exc)
            return "unreadable_or_unsupported", None
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("account memory is unreadable: %s: %s", type(exc).__name__, exc)
            return "unreadable_or_unsupported", None
        if not valid_account_memory(payload):
            logger.warning("account memory has an unreadable shape or unsupported version")
            return "unreadable_or_unsupported", None
        if payload["version"] == 1:
            payload = {"version": VERSION, "accounts": {
                account_key(record["provider"], record): record
                for record in payload["accounts"].values()
            }}
        return "valid", payload


    def _write(self, payload):
        parent = self.path.parent
        parent_created = not parent.exists()
        parent.mkdir(parents=True, exist_ok=True)
        if parent_created:
            file_io.fsync_directory(parent.parent)
        temporary = parent / (".%s-%s" % (self.path.name, uuid.uuid4().hex))
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
                handle.write("\n")
                handle.flush()
            file_io.fsync_file(temporary)
            os.replace(temporary, self.path)
            file_io.fsync_directory(parent)
        finally:
            if temporary.exists():
                temporary.unlink()

