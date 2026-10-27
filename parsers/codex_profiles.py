"""Read the portable Codex account registry; never load provider credentials."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
import uuid


@dataclass(frozen=True)
class SessionLinks:
    excluded: frozenset = frozenset()
    unavailable: bool = False
    errors: tuple = ()

    def excludes(self, session_id):
        return self.unavailable or session_id in self.excluded or (bool(self.excluded) and not session_id)

    @property
    def signature(self):
        return self.unavailable, tuple(sorted(self.excluded))

    def apply(self, rows):
        return [dict(row, account=None) if row["agent_id"] == "codex" and self.excludes(row["session_id"])
                else row for row in rows]

    def accepts_cached_quota(self, block):
        if not block or not (self.unavailable or self.excluded):
            return True
        observation = block.get("quota_observation") or {}
        if observation.get("source") == "active_query":
            return True
        sid = observation.get("session_id")
        return bool(sid) and not self.excludes(sid)


def session_links(root=None, source_errors=None):
    """Read one registry snapshot per collection; names are selections, not billing facts."""
    root = Path(root) if root is not None else Path.home() / ".local/share/codex-accounts/session-links"
    excluded, errors, unavailable = set(), [], False

    def error(path):
        errors.append({"path": str(path), "stage": "session_links",
                       "error": "Codex 会话账号记录无效或不可读；受影响会话归属及日志额度未知"})

    try:
        if root.is_symlink():
            raise ValueError("symlink registry")
        paths = sorted(root.iterdir())
    except FileNotFoundError:
        paths = []
    except (OSError, ValueError):
        paths = []
        unavailable = True
        error(root)
    for path in paths:
        # Atomic writers may leave non-JSON staging files; only UUID.json is a record.
        if path.suffix != ".json":
            continue
        sid = path.stem
        try:
            if str(uuid.UUID(sid)) != sid:
                raise ValueError("noncanonical UUID")
        except ValueError:
            unavailable = True
            error(path)
            continue
        try:
            if path.is_symlink():
                raise ValueError("symlink record")
            value = json.loads(path.read_text())
            names = value.get("account_names")
            if (type(value.get("schema_version")) is not int or value["schema_version"] != 1
                    or not isinstance(names, list) or not names
                    or any(not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", name) for name in names)
                    or len(set(names)) != len(names)
                    or value.get("storage_account") not in names or value.get("last_selected_account") not in names):
                raise ValueError("invalid session link")
            if len(names) > 1:
                excluded.add(sid)
        except (OSError, ValueError, TypeError, AttributeError):
            excluded.add(sid)
            error(path)
    if source_errors is not None:
        source_errors.extend(errors)
    return SessionLinks(frozenset(excluded), unavailable, tuple(errors))


@dataclass(frozen=True)
class Profile:
    home: Path
    account: dict | None = None


def discover(root=None, source_errors=None):
    root = Path(root) if root is not None else Path.home() / ".local/share/codex-accounts"
    result = []
    try:
        folders = sorted(root.iterdir())
    except FileNotFoundError:
        return result
    except OSError:
        if source_errors is not None:
            source_errors.append({"path": str(root), "stage": "account_profile", "error": "Managed Codex registry unavailable"})
        return result
    for folder in folders:
        path = folder / "profile.json"
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text())
            if (folder.is_symlink() or path.is_symlink() or (folder / "codex").is_symlink()
                    or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", folder.name)
                    or value.get("schema_version") != 1 or value.get("name") != folder.name):
                raise ValueError("invalid managed profile")
            identity = value.get("identity")
            account = None
            if identity is not None:
                if not isinstance(identity, dict) or any(not isinstance(identity.get(k), str) or not identity[k]
                                                        for k in ("account_id", "email", "subject")):
                    raise ValueError("invalid managed account identity")
                account = {"name": folder.name, "account_id": identity["account_id"], "email": identity["email"]}
            result.append(Profile(folder / "codex", account))
        except (OSError, ValueError, TypeError, AttributeError):
            if source_errors is not None:
                source_errors.append({"path": str(path), "stage": "account_profile", "error": "Invalid or unreadable managed Codex profile"})
    return result


def sources(source_errors=None):
    from . import codex
    # Keep the existing constants patchable by isolated parser/collector tests.
    result = [(Path(codex.SESSIONS_DIR), Path(codex.STATE_DB), None)]
    for profile in discover(source_errors=source_errors):
        for child in ("sessions", "archived_sessions"):
            result.append((profile.home / child, profile.home / "state_5.sqlite", profile.account))
    return result
