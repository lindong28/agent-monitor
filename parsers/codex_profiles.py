"""Read the portable Codex account registry; never load provider credentials."""

from dataclasses import dataclass
import json
from pathlib import Path
import re


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
