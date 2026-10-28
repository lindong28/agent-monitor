"""Compose live, remembered and applicability-aware quota views."""
import logging
import math
import time
from datetime import datetime, timezone

from agent_monitor.accounts import codex_accounts
from agent_monitor.snapshots import generation
from agent_monitor import hub_config as hub
from agent_monitor import diagnostics
from agent_monitor.quota import quota_applicability
from .account_memory import (NUMERIC_FIELDS, identity_id, account_key, parse_observed_at, quota_number, text)

logger = logging.getLogger("agent-monitor")

def remembered_rate_limit_entry(record):
    return {
        "account_id": record["account_id"],
        "account_label": record["account_label"],
        "account_plan": record["account_plan"],
        # Memory stores neither, and must not start: its record shape is
        # validated by exact set equality, so one extra field would make the
        # whole file unreadable and drop every remembered account (ADR
        # 20260822-586a). Null is also the honest value — a remembered row
        # states a past observation, not what any machine is signed into now.
        "reading_plan": None,
        "credential_plan": None,
        "account_state": "known",
        "presence": "remembered",
        "five_hour_used_pct": quota_number(record["five_hour_used_pct"]),
        "five_hour_resets_at": quota_number(record["five_hour_resets_at"]),
        "seven_day_used_pct": quota_number(record["seven_day_used_pct"]),
        "seven_day_resets_at": quota_number(record["seven_day_resets_at"]),
        "updated_at": record["observed_at"],
        "machines": [],
        # Null, and present: a remembered row has no machine behind it at all,
        # which is a different thing from a live row whose source this server is
        # too old to name. Omitting the key would conflate them at the consumer.
        "reading_from": record.get("reading_from"),
        "quota_observation": quota_applicability.observation(record.get("quota_observation")),
        "legacy_observation": record.get("legacy_observation"),
        "this_machine": None,
    }


def plan_to_show(block):
    """The plan a row displays, derived here rather than taken on trust.

    An exporter that predates the two raw fields sends neither key, and its
    `account_plan` is then the only plan available. Once a block carries the
    keys, that value is a claim by one writer among several — every machine
    runs its own exporter, at its own version, and nothing between them checks
    that it agrees with the pair beside it. Past that point the pair is the
    whole answer, including when the answer is "neither source reported one".
    """
    reading = text(block.get("reading_plan"))
    credential = text(block.get("credential_plan"))
    if reading or credential:
        return reading or credential
    if "reading_plan" in block or "credential_plan" in block:
        return None
    return text(block.get("account_plan"))


def account_entry(block, machines, self_machine=None, reading_from=None):
    account_id = identity_id(block)
    if account_id:
        account_state = "known"
    elif block.get("account_id") is None and "account_id" in block:
        account_state = "signed_out"
    else:
        account_state = "unstamped"
    return {
        "account_id": account_id,
        "account_label": text(block.get("account_label")),
        "account_profile": text(block.get("account_profile")),
        # Derived here, not taken from the block. The exporter derives the same
        # value, but it is one of several writers — every other machine runs its
        # own, at its own version, and nothing between them validates that a
        # published `account_plan` agrees with the pair beside it. Trusting it
        # would let a block claim a plan neither source reported, and that value
        # does not stop at the screen: it is what the account memory persists,
        # and that file cannot be rebuilt. Recomputing costs nothing and makes
        # the contradiction unrepresentable downstream.
        #
        # The block's own value is the last resort, and only for exporters older
        # than the pair — where it is the one plan on offer. That is a question
        # about whether the keys are there, not about whether they hold
        # anything: a current exporter that read neither source publishes both
        # keys as null, and falling back on falsiness would hand that block the
        # very value this recomputation exists to distrust. `account_id` two
        # fields up already draws the line this way, for the same reason.
        "account_plan": plan_to_show(block),
        # The two facts behind it, passed through unmerged so that having
        # fewer than two comparable sources stays tellable apart from two that
        # agree — the derived value alone cannot express the difference. An
        # exporter that predates ADR 20260822-586a sends neither key, so such a
        # row has zero sources, not one; either way nothing is compared, and
        # that is not the same as comparing and finding them equal.
        "reading_plan": text(block.get("reading_plan")),
        "credential_plan": text(block.get("credential_plan")),
        # Three states, not two. "Signed out" and "reported by an exporter too
        # old to stamp accounts" both leave no account, but only the second is
        # fixed by updating that machine — telling a current machine to update
        # itself sends the reader after a problem they do not have.
        "account_state": account_state,
        # The machine whose reading this row is showing, which is one of
        # `machines` and not necessarily all of them. Published because a reader
        # told "this machine stopped reporting" needs to know whether the figure
        # beside that sentence is the one that stopped, or another machine's
        # that is still advancing.
        "reading_from": reading_from,
        # `*_used_pct`, not `*_pct`: the page shows headroom, and a name that
        # does not say which direction it runs is one rename away from being
        # rendered upside down. The wire field it comes from keeps its old name
        # — that one is written by other machines and cannot be renamed without
        # a flag day.
        "five_hour_used_pct": quota_number(block.get("five_hour_pct")),
        "five_hour_resets_at": quota_number(block.get("five_hour_resets_at")),
        "seven_day_used_pct": quota_number(block.get("seven_day_pct")),
        "seven_day_resets_at": quota_number(block.get("seven_day_resets_at")),
        "updated_at": (quota_applicability.observation(block.get("quota_observation")) or {}).get("observed_at") or block.get("updated_at"),
        "quota_observation": quota_applicability.observation(block.get("quota_observation")),
        "refresh_error": text(block.get("refresh_error")),
        "machines": sorted(machines),
        # Marked so a row can say which account the session in front of the
        # reader is spending. Three e-mail addresses do not tell them that; the
        # machine list does, but only once one of the names is singled out.
        "this_machine": self_machine if self_machine in machines else None,
    }


def self_machine_name(admission):
    """Which declared machine is this one, via the config's own accessor.

    `load_machine_config` already refuses a config that does not name exactly
    one, so this does not re-derive that rule — it only tolerates an admission
    built without a config, which happens in tests.
    """
    config = getattr(admission, "config", None)
    if config is None:
        return None
    try:
        return config.self_machine.name
    except (AttributeError, StopIteration):
        return None


def quota_unavailable_reason(provider, sync_status, signed_out=(), refresh_errors=()):
    # Nobody is signed in anywhere, and nothing failed. That is a fleet that does
    # not use this provider, not a fleet waiting on a refresh — and the sentence
    # below about needing a successful refresh would send the reader to retry
    # something that is already working. Checked before the failure branches so a
    # single unrelated failure elsewhere cannot restore the misleading wording.
    if signed_out and not refresh_errors:
        return "没有机器登录 %s，没有配额可报。" % provider
    machine_errors = [
        "%s: %s" % (machine["name"], machine["reason"])
        for machine in (sync_status or {}).get("machines", ())
        if machine.get("reason")
        and machine.get("last_attempt_outcome")
        in {"failure", "cleanup_failed", "malformed_result"}
    ]
    if machine_errors:
        return (
            "最近一次同步在拿到 %s 配额数据前就失败了：%s。"
            % (provider, "；".join(machine_errors))
        )
    if (sync_status or {}).get("syncing"):
        return (
            "已纳入的数据里还没有 %s 配额，同步进行中。"
            % provider
        )
    return (
        "已纳入的数据里还没有 %s 配额，需要一次成功的刷新。"
        % provider
    )


class QuotaView:
    """Quota composition depends on an explicit memory and refresh service."""

    def __init__(self, memory, quota_refresh):
        self.memory = memory
        self.quota_refresh = quota_refresh

    def action_catalog(self):
        """Read the known identity directory, without refreshing or rewriting it."""
        with generation.generation_admission_snapshot() as admission:
            entries = []
            for current in admission.admitted:
                limits = current.meta.get("rate_limits")
                block = limits.get("codex") if isinstance(limits, dict) else None
                if isinstance(block, dict):
                    # Identity remains usable when no quota timestamp was observed.
                    entries.append(account_entry(block, [current.host]))
        state, payload = self.memory.read()
        if state == "unreadable_or_unsupported":
            raise codex_accounts.ActionError("历史登录账号记录无法读取，暂不能确认完整账号列表。请检查服务主机的 account_memory.json。", 503)
        if state == "valid":
            entries.extend(remembered_rate_limit_entry(record) for record in payload["accounts"].values()
                           if record["provider"] == "codex")
        return entries


    def rate_limits(self, admission=None, sync_status=None, expected_delete_epoch=None):
        if admission is None:
            with generation.generation_admission_snapshot() as loaded:
                return self.from_admission(
                    loaded,
                    sync_status=sync_status,
                    expected_delete_epoch=expected_delete_epoch,
                )
        return self.from_admission(
            admission,
            sync_status=sync_status,
            expected_delete_epoch=expected_delete_epoch,
        )


    def from_admission(self,
        admission, sync_status=None, *, expected_delete_epoch=None
    ):
        if expected_delete_epoch is None:
            with self.memory.upsert_epoch() as registered_epoch:
                result = self.live_from_admission(
                    admission, sync_status=sync_status
                )
                self.remember(
                    result, expected_delete_epoch=registered_epoch
                )
        else:
            result = self.live_from_admission(
                admission, sync_status=sync_status
            )
            self.remember(
                result, expected_delete_epoch=expected_delete_epoch
            )
        self.merge_remembered(result)
        self.apply_applicability(result)
        return result


    def live_from_admission(self, admission, sync_status=None):
        """Quota grouped by account, because quota is metered per account.

        Machines are not the unit: three machines signed into one Claude account
        report one counter three times, and picking the freshest of them is right.
        Two machines signed into different Codex accounts report two independent
        pools, and picking the freshest is a coin toss between them — the bug this
        replaces. Grouping by account gets both cases right.

        A machine whose exporter predates account stamping groups under its own key
        rather than merging with anything: an unknown account is not evidence of a
        shared one, and merging unknowns would recreate the same bug one level down.
        """
        grouped = {"claude": {}, "codex": {}}
        refresh_errors = {"claude": [], "codex": []}
        signed_out = {"claude": [], "codex": []}
        for current in admission.admitted:
            rate_limits = current.meta.get("rate_limits")
            if hub.enabled():
                config = getattr(admission, "config", None)
                machine = getattr(config, "by_name", {}).get(current.host)
                rate_limits = self.quota_refresh.merge(current, machine)
            if not isinstance(rate_limits, dict):
                rate_limits = {}
            observations = [(provider, rate_limits.get(provider)) for provider in grouped]
            managed = (rate_limits.get("codex") or {}).get("managed_accounts", []) if isinstance(rate_limits.get("codex"), dict) else []
            if isinstance(managed, list):
                observations.extend(("codex", block) for block in managed if isinstance(block, dict) and identity_id(block))
            for provider, block in observations:
                buckets = grouped[provider]
                # Not every machine signs into every provider, and one that does not
                # has nothing to fix. It is recorded as a state so the page can say
                # why its name is absent from the figures, and kept out of
                # `refresh_errors` so nothing downstream counts it as a failure.
                #
                # It suppresses the error, not the row. A machine whose sign-in
                # lapsed usually still holds a reading from before it did, and that
                # reading with its own timestamp is the only remaining cue that the
                # machine has stopped reporting. Dropping the row here would take
                # that away too, and leave no observable difference between a
                # machine that never used the provider and one that quietly fell off.
                refresh_error = None
                # An error present in the block wins over the signed-out state, and
                # is read first for that reason. The two are not supposed to appear
                # together — but a block can carry both (a cached block from a lapsed
                # sign-in, restamped with this round's error), and if the signed-out
                # branch ran first that error would be dropped on every sync round
                # for as long as the cache held. A failure that stops being reported
                # is the one outcome this whole change must not produce, so the
                # consumer resolves the contradiction toward reporting it.
                if (
                    isinstance(block, dict)
                    and block.get("signed_out") is True
                    and block.get("refresh_error") is None
                ):
                    # A logged-out legacy home does not mean the machine's managed
                    # account homes are logged out too.
                    if provider != "codex" or not managed:
                        signed_out[provider].append(current.host)
                # A missing `refresh_error` is not by itself evidence of an old
                # exporter. A current one omits the key on every passive export —
                # absent means "this export ran no active query", which the 120-second
                # statistics round legitimately is. It normally carries the key over
                # from its own quota cache, but only while the cached block names the
                # account the machine is signed into now; switch accounts and the
                # carry-over stops, correctly, because republishing the old account's
                # "query succeeded" under a new one would be a false claim. Reading
                # absence as age then sent the reader to update an exporter that was
                # already current, once per account switch, until that machine's next
                # hourly refresh.
                #
                # `signed_out` is the discriminator: it was added after
                # `refresh_error`, and every exporter that publishes it publishes the
                # error key too whenever it runs an active query. Neither key means
                # the exporter predates both and genuinely cannot confirm a refresh.
                elif not isinstance(block, dict) or (
                    "refresh_error" not in block and "signed_out" not in block
                ):
                    refresh_error = "无法确认这台机器的配额刷新已生效，请更新它的 agent-monitor exporter 后重试。"
                elif block.get("refresh_error") is not None:
                    value = block["refresh_error"]
                    refresh_error = value if isinstance(value, str) and value.strip() else "配额刷新状态无效，请更新这台机器的 agent-monitor exporter 后重试。"
                if refresh_error is not None:
                    failure_entry = {"machine": current.host, "reason": refresh_error}
                    if isinstance(block, dict) and (diagnostics.safe_failure(block.get("refresh_failure")) or block.get("refresh_pause")):
                        failure_entry.update(account_id=identity_id(block), account_label=block.get("account_label"),
                            failure=diagnostics.safe_failure(block.get("refresh_failure")),
                            pause=block.get("refresh_pause"), query_state=block.get("query_state"))
                    refresh_errors[provider].append(failure_entry)
                if not isinstance(block, dict):
                    continue
                evidence = quota_applicability.observation(block.get("quota_observation"))
                updated_at = parse_observed_at(evidence["observed_at"] if evidence and evidence["observed_at"] else block.get("updated_at"))
                if updated_at is None:
                    if not (identity_id(block) and (refresh_error or provider == "claude" or block.get("account_profile"))):
                        continue
                    # A failed quota request does not sign the current account out.
                    # Keep its presence so account memory can supply an older value.
                    updated_at = datetime.min.replace(tzinfo=timezone.utc)
                account_id = identity_id(block)
                key = account_key(provider, block) if account_id else ("unattributed", current.host)
                bucket = buckets.get(key)
                if bucket is None:
                    buckets[key] = {
                        "latest": (updated_at, block),
                        # Which machine the shown figure came from. One account is
                        # commonly signed in on several machines and the row shows
                        # the freshest of them, so "this account's reading" and "this
                        # machine's reading" are different claims — and a consumer
                        # that only has the machine *list* cannot tell which of them
                        # the number in front of the reader belongs to.
                        "latest_host": current.host,
                        "machines": [current.host],
                        "confirmations": [],
                        "failures": [],
                    }
                else:
                    if current.host not in bucket["machines"]:
                        bucket["machines"].append(current.host)
                    old_observation = quota_applicability.observation(bucket["latest"][1].get("quota_observation"))
                    new_observation = quota_applicability.observation(block.get("quota_observation"))
                    old_trusted = bool(provider == "claude" and old_observation and old_observation["observed_at"])
                    new_trusted = bool(provider == "claude" and new_observation and new_observation["observed_at"])
                    if (new_trusted, updated_at) > (old_trusted, bucket["latest"][0]):
                        bucket["latest"] = (updated_at, block)
                        bucket["latest_host"] = current.host
                bucket = buckets[key]
                note = quota_applicability.confirmation(block.get("subscription_confirmation"))
                if note:
                    bucket["confirmations"].append(note)
                if block.get("refresh_failure"):
                    bucket["failures"].append(block["refresh_failure"])

        self_machine = self_machine_name(admission)
        result = {}
        for provider, buckets in grouped.items():
            # Freshest account first; it is the one the reader is most likely using.
            ordered = sorted(buckets.values(), key=lambda b: b["latest"][0], reverse=True)
            entries = [
                account_entry(
                    bucket["latest"][1],
                    bucket["machines"],
                    self_machine,
                    reading_from=bucket["latest_host"],
                )
                for bucket in ordered
            ]
            for entry, bucket in zip(entries, ordered):
                if bucket["confirmations"]:
                    entry["subscription_confirmation"] = max(bucket["confirmations"], key=quota_applicability.confirmation_time)
                if any(failure.get("http_status") == 403 for failure in bucket["failures"] if isinstance(failure, dict)):
                    entry["quota_access_refused"] = True
            result[provider] = {
                "accounts": entries,
                "refresh_errors": refresh_errors[provider],
                # Machines that are not signed in to this provider. Published beside
                # the errors rather than inside them: the page tells the reader why a
                # machine is absent from the figures without implying there is
                # anything to fix.
                "signed_out_machines": sorted(signed_out[provider]),
                "unavailable_reason": None
                if entries
                else quota_unavailable_reason(
                    provider, sync_status, signed_out[provider], refresh_errors[provider]
                ),
            }
        return result


    def remember_published(self):
        """Remember the current admission snapshot while this sync round is still running."""
        try:
            with generation.generation_admission_snapshot() as admission:
                with self.memory.upsert_epoch() as expected_delete_epoch:
                    rate_limits = self.live_from_admission(admission)
                    self.remember(
                        rate_limits, expected_delete_epoch=expected_delete_epoch
                    )
        except Exception as exc:
            logger.warning(
                "account memory upsert after sync publish failed: %s: %s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )


    def remember(self, rate_limits, *, expected_delete_epoch):
        try:
            return self.memory.upsert(
                rate_limits, expected_delete_epoch=expected_delete_epoch
            )
        except Exception as exc:
            logger.warning(
                "account memory upsert failed: %s: %s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return False


    def merge_remembered(self, rate_limits):
        for provider in ("claude", "codex"):
            provider_limits = rate_limits.get(provider)
            if not isinstance(provider_limits, dict):
                continue
            for entry in provider_limits.get("accounts", ()):
                entry["presence"] = "in_use"

        state, payload = self.memory.read()
        if state != "valid":
            return

        remembered = sorted(
            payload["accounts"].values(),
            key=lambda record: parse_observed_at(record["observed_at"]),
            reverse=True,
        )
        for provider in ("claude", "codex"):
            provider_limits = rate_limits.get(provider)
            if not isinstance(provider_limits, dict):
                continue
            entries = provider_limits.get("accounts")
            if not isinstance(entries, list):
                continue
            # Machines whose live reading is not known to be current. A lapsed
            # sign-in belongs here alongside an outright failure: the question this
            # set answers is "may account memory supply a newer value for this
            # entry", and the answer turns on whether the machine still refreshes,
            # not on whether its inability to do so is classed as a fault.
            failed_machines = {
                failure["machine"] for failure in provider_limits.get("refresh_errors", ())
            } | set(provider_limits.get("signed_out_machines", ()))
            for entry in entries:
                record = payload["accounts"].get(account_key(provider, entry))
                if record is None or not failed_machines.intersection(entry.get("machines", ())):
                    continue
                live_time = parse_observed_at(entry.get("updated_at"))
                live_evidence = quota_applicability.observation(entry.get("quota_observation"))
                stored_evidence = quota_applicability.observation(record.get("quota_observation"))
                live_rank = (bool(provider == "claude" and live_evidence and live_evidence["observed_at"]), live_time or datetime.min.replace(tzinfo=timezone.utc))
                stored_rank = (bool(provider == "claude" and stored_evidence and stored_evidence["observed_at"]), parse_observed_at(record["observed_at"]))
                if live_rank >= stored_rank:
                    continue
                for field in NUMERIC_FIELDS:
                    entry[field] = quota_number(record[field])
                entry["updated_at"] = record["observed_at"]
                entry["quota_observation"] = stored_evidence
                if record.get("legacy_observation"):
                    entry["legacy_observation"] = record["legacy_observation"]
                # Account memory does not retain a machine source. The replaced
                # live reading's host cannot vouch for these remembered numbers.
                entry["reading_from"] = record.get("reading_from")
                # Memory has no reading-plan provenance. Keep today's credential
                # plan separate rather than attach a stale cache's plan to its quota.
                entry["reading_plan"] = None
                entry["account_plan"] = entry.get("credential_plan")
            live_account_ids = {
                account_key(provider, entry)
                for entry in entries
                if entry.get("account_state") == "known"
            }
            entries.extend(
                remembered_rate_limit_entry(record)
                for record in remembered
                if record["provider"] == provider
                and account_key(provider, record) not in live_account_ids
            )


    def apply_applicability(self, rate_limits):
        """Separate current quota from retained facts after whole-reading selection."""
        block = rate_limits.get("claude")
        if not isinstance(block, dict):
            return
        try:
            applicability_store = quota_applicability.Store(self.memory.path.parent)
            notes = applicability_store.read()
            state_error = False
        except (OSError, ValueError):
            notes, state_error = {}, True
            logger.warning("quota applicability state unreadable; current Claude quotas withheld")
        now = time.time()
        for account in block.get("accounts", ()):
            evidence = quota_applicability.observation(account.get("quota_observation"))
            observed = evidence.get("observed_at") if evidence else None
            candidates = [quota_applicability.confirmation(account.get("subscription_confirmation")),
                          notes.get("claude:" + str(account.get("account_id")))]
            candidates = [item for item in candidates if item]
            note = max(candidates, key=quota_applicability.confirmation_time) if candidates else None
            if note and not state_error:
                # Only the explicit recovery path produces restored_at. A normal
                # request already in flight when the user confirms cannot retire it.
                try:
                    note = applicability_store.adopt("claude", account["account_id"], note)
                except (OSError, ValueError):
                    state_error = True
                    logger.warning("quota applicability evidence could not be persisted")
                account["subscription_confirmation"] = note
            historical = {key: account.get(key) for key in (*NUMERIC_FIELDS, "reading_from")}
            historical.update(observed_at=observed, recorded_at=account.get("updated_at"),
                              source=evidence["source"] if evidence else "legacy_unknown",
                              account_plan=account.get("account_plan"))
            account["last_observation"] = historical
            if state_error:
                status, reason = "unverified", "订阅确认记录无法读取，当前配额未核实；请检查服务主机状态文件。"
            elif quota_applicability.unavailable(note):
                status, reason = "no_subscription", "无有效 Claude Code 订阅（用户已确认）；历史配额不适用于当前账号。"
                account["subscription_confirmation"] = note
            elif account.get("quota_access_refused") and account.get("refresh_error"):
                status, reason = "access_denied", "配额访问被拒绝（HTTP 403），原因未确认；历史读数不能代表当前额度。"
            elif not observed:
                status, reason = "unverified", "配额观测时间未核实；旧记录接收时间不能证明额度已更新。"
            elif not quota_applicability.current_window(account, now):
                status, reason = "historical", "没有当前窗口的配额读数，历史记录仅供参考。"
            else:
                status, reason = "current", None
            if status == "no_subscription":
                expected = [failure for failure in block.get("refresh_errors", ())
                            if failure.get("account_id") == account.get("account_id")
                            and (failure.get("failure") or {}).get("http_status") == 403]
                account["historical_query_failures"] = expected
                block["refresh_errors"] = [failure for failure in block.get("refresh_errors", ()) if failure not in expected]
            account["quota_status"] = status
            account["quota_status_reason"] = reason
            # A credential tier never proves a current paid entitlement.
            account["plan_verification"] = "unverified"
            account["updated_at"] = observed if status == "current" else None
            for prefix in ("five_hour", "seven_day"):
                reset = account.get(prefix + "_resets_at")
                if status != "current" or type(reset) not in (int, float) or not math.isfinite(reset) or reset <= now:
                    account[prefix + "_used_pct"] = None
                    account[prefix + "_resets_at"] = None

