"""Observed session identity, separate from immutable usage facts."""

from datetime import datetime, timezone
import json


FIELDS = {"agent_id", "session_id", "parent_session_id", "relation", "title", "created_at", "project"}
RELATIONS = {"root", "child", "unknown", "conflict"}


def timestamp(value):
    try:
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif type(value) in (int, float):
            parsed = datetime.fromtimestamp(value, timezone.utc)
        else:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def codex_metadata(session_id, source=None, *, title=None, created_at=None, project=None):
    if not isinstance(session_id, str) or not session_id:
        return None
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except ValueError:
            pass
    parent, relation = None, "unknown"
    if isinstance(source, str) and source:
        relation = "root"
    elif isinstance(source, dict):
        subagent = source.get("subagent")
        spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
        if isinstance(spawn, dict):
            parent = spawn.get("parent_thread_id")
            if isinstance(parent, str) and parent:
                relation = "child"
            else:
                parent = None
    return {"agent_id": "codex", "session_id": session_id, "parent_session_id": parent,
            "relation": relation, "title": title.strip() if isinstance(title, str) and title.strip() else None,
            "created_at": timestamp(created_at), "project": project if isinstance(project, str) else None}


def validate(row):
    if not isinstance(row, dict) or not FIELDS <= set(row) or not set(row) <= FIELDS | {"account"}:
        raise ValueError("invalid session metadata fields")
    if any(not isinstance(row[key], str) or not row[key] for key in ("agent_id", "session_id")):
        raise ValueError("invalid session metadata identity")
    if row["relation"] not in RELATIONS:
        raise ValueError("invalid session relation")
    for key in ("parent_session_id", "title", "created_at", "project"):
        if row[key] is not None and not isinstance(row[key], str):
            raise ValueError("invalid session metadata value")
    if (row["relation"] == "child") != bool(row["parent_session_id"]):
        raise ValueError("invalid session parent")
    if row["created_at"] is not None and timestamp(row["created_at"]) is None:
        raise ValueError("invalid session creation time")
    account = row.get("account")
    if account is not None and (not isinstance(account, dict) or set(account) != {"name", "account_id", "email"}
                                or any(not isinstance(v, str) or not v for v in account.values())):
        raise ValueError("invalid session account")
    return dict(row)


def merge_metadata(previous=(), observed=()):
    """Keep absent sources; never choose a parent by last-writer-wins."""
    result = {}
    for item in (*previous, *observed):
        row = validate(item)
        key = (row["agent_id"], row["session_id"])
        old = result.get(key)
        if old:
            relation, parent = row["relation"], row["parent_session_id"]
            if old["relation"] == "conflict" or relation == "conflict":
                relation, parent = "conflict", None
            elif relation == "unknown":
                relation, parent = old["relation"], old["parent_session_id"]
            elif old["relation"] != "unknown" and (relation, parent) != (old["relation"], old["parent_session_id"]):
                relation, parent = "conflict", None
            row.update(relation=relation, parent_session_id=parent)
            if "account" in old:
                if "account" in row and row["account"] != old["account"]:
                    # Copied session IDs do not establish which account owns usage.
                    row["account"] = None
                else:
                    row["account"] = old["account"]
            for field in ("title", "project"):
                if row[field] is None:
                    row[field] = old[field]
            times = [t for t in (old["created_at"], row["created_at"]) if t]
            row["created_at"] = min(times) if times else None
        result[key] = row
    return list(result.values())


class SessionIndex:
    """Resolve whole ancestry before filtering; invalid chains stay separate."""

    def __init__(self, metadata=()):
        self.metadata = {(r["agent_id"], r["session_id"]): r for r in merge_metadata(observed=metadata)}
        self.resolved = {}
        for key in self.metadata:
            self.root(key)

    def root(self, key):
        if key in self.resolved:
            return self.resolved[key][0]
        path, seen, node = [], set(), key
        status = "unavailable"
        while node not in seen:
            if node in self.resolved:
                root, status = self.resolved[node]
                break
            seen.add(node)
            path.append(node)
            row = self.metadata.get(node)
            if row is None:
                status = "missing_parent" if node != key else "unavailable"
                break
            if row["relation"] == "root":
                root, status = node, "linked"
                break
            if row["relation"] != "child":
                status = row["relation"]
                break
            parent_key = (node[0], row["parent_session_id"])
            parent_row = self.metadata.get(parent_key)
            if parent_row and row.get("account") != parent_row.get("account"):
                status = "conflict"
                break
            node = parent_key
        else:
            status = "cycle"
        for member in path:
            self.resolved[member] = (root if status == "linked" else member, status)
        return self.resolved[key][0]

    def status(self, key):
        self.root(key)
        return self.resolved[key][1]

    def members(self, key):
        root = self.root(key)
        return {k for k in self.metadata if self.root(k) == root} | {root}

    def group(self, entries):
        result = {}
        for entry in entries:
            key = self.root((entry.agent_id, entry.session_id))
            result.setdefault(key, []).append(entry)
        return result
