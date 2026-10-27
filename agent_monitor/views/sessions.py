"""Session and cost-diagnostic views with instance-owned bounded caches."""
from collections import defaultdict
from datetime import datetime
from dataclasses import replace

import generation
from agent_monitor import hub_config as hub
import statistics_snapshot
import sync
from agent_monitor.views.session_cache import SessionCache
from agent_monitor.views.conversation_cache import ConversationCache
from agent_monitor.contracts.session import SessionIndex
from agent_monitor.contracts.project import canonical_project
from .time_ranges import first, session_window
from .projections import (filter_time, filter_values, conversation_stats,
                          group_execution_sessions, session_stats, entry_json, session_sort_key)


class SessionsView:
    def __init__(self, collection, usage_source, *, generations=generation,
                 snapshots=statistics_snapshot, machines=sync, mode=hub,
                 session_cache=None, conversation_cache=None, clock=None):
        self.collection = collection
        self.usage_source = usage_source
        self.generations = generations
        self.snapshots = snapshots
        self.machines = machines
        self.mode = mode
        self.session_cache = session_cache if session_cache is not None else SessionCache()
        self.conversation_cache = (conversation_cache if conversation_cache is not None
                                   else ConversationCache(snapshots=snapshots))
        self.clock = clock or (lambda: datetime.now().astimezone())

    def list(self, query):
        cost_status = first(query, "cost_status", "")
        if cost_status not in ("", "unknown"):
            raise ValueError("Unsupported cost_status: %s" % cost_status)
        now = self.clock()
        time_range = session_window(query, now)
        sessions = []
        options = {key: set() for key in ("machine", "agent", "project", "model")}
        with_filters = first(query, "with_filters", "") == "1"

        def collect(entries, machine=None, aliases=None, *, index=None, projects=None, with_offset=False):
            source_sessions = []
            source_options = {key: set() for key in options}
            index = index if index is not None else SessionIndex(getattr(entries, "session_metadata", ()))
            # Match Explore display identities without rewriting stored usage.
            projects = projects if projects is not None else {}
            in_window = []
            offsets = {}
            for offset, entry in enumerate(entries):
                if time_range is not None and not time_range[0] <= entry.timestamp.astimezone() <= time_range[1]:
                    continue
                if entry.project not in projects:
                    projects[entry.project] = canonical_project(entry.project, aliases)
                project = projects[entry.project]
                entry = entry if project == entry.project else replace(entry, project=project)
                in_window.append(entry)
                if with_offset:
                    offsets[id(entry)] = offset
            display_query = dict(query)
            if query.get("project"):
                display_query["project"] = [canonical_project(value, aliases)
                                            for value in query["project"]]
            if with_filters:
                if machine and in_window:
                    source_options["machine"].add(machine)
                for entry in in_window:
                    source_options["agent"].add(entry.agent_id)
                    source_options["project"].add(entry.project)
                    source_options["model"].add(entry.model)
            source_options = {key: sorted(values) for key, values in source_options.items()}
            if first(query, "machine", "") not in ("", machine):
                return source_sessions, source_options, None
            visible = filter_values(in_window, display_query)
            if cost_status == "unknown":
                visible = [entry for entry in visible if entry.cost_usd is None]
            for key, items in index.group(visible).items():
                row = conversation_stats(items, key, index, aliases)
                if machine:
                    row["machine"] = machine
                source_sessions.append(row)
            return source_sessions, source_options, offsets[id(visible[0])] if with_offset and visible else None

        def merge(result):
            rows, filters = result[:2]
            sessions.extend(rows)
            for key, values in filters.items():
                options[key].update(values)

        if self.mode.enabled():
            self.collection.request(query)
            with self.generations.generation_admission_snapshot() as admission:
                for current in admission.admitted:
                    if not with_filters and first(query, "machine", "") not in ("", current.host):
                        continue
                    result = self.session_cache.get(current, query, time_range)
                    if result is None:
                        aliases = (getattr(current, "meta", None) or {}).get("aliases")
                        grouped = self.conversation_cache.read(current, query, time_range,
                            lambda entries, index, projects: collect(entries, current.host, aliases,
                                index=index, projects=projects, with_offset=True))
                        if grouped is None:
                            entries = self.snapshots.read_admitted_usage(
                                current, time_range=time_range, query=None if with_filters else
                                {key: value for key, value in query.items() if key != "project"})
                            result = collect(entries, current.host, aliases)
                            self.session_cache.put(current, query, time_range, entries, result)
                        else:
                            result, reusable = grouped
                            if reusable:
                                # Every bucket checked all candidate timestamps;
                                # no retained event objects are needed for this guard.
                                self.session_cache.put(current, query, time_range, (), result)
                    merge(result)
        else:
            merge(collect(self.usage_source.load_all_entries(), self.machines.load_machine_config().self_machine.name))
        sort = first(query, "sort", "time")
        sessions.sort(key=lambda row: session_sort_key(row, sort),
                      reverse=first(query, "order", "desc") != "asc")
        if with_filters:
            return {"sessions": sessions, "filters": {key: sorted(v for v in values if v)
                                                     for key, values in options.items()}}
        return sessions


    def detail(self, session_id, machine=None, query=None):
        # Direct Python callers retain the old all-records default; the HTTP/UI
        # passes its actual list context, including the ordinary 30-day default.
        query = {"scope": ["all"]} if query is None else query
        scope = first(query, "scope", "filtered")
        if scope not in ("all", "filtered"):
            raise ValueError("Unsupported session scope")
        if first(query, "cost_status", "") not in ("", "unknown"):
            raise ValueError("Unsupported cost_status")
        agent = first(query, "session_agent", "")
        window = None if scope == "all" else session_window(query, self.clock())
        aliases = None
        if self.mode.enabled():
            with self.generations.generation_admission_snapshot() as admission:
                selected = [current for current in admission.admitted if current.host == machine]
                if not selected:
                    return {"meta": None, "entries": [], "scope": scope}
                current = selected[0]
                entries = self.snapshots.read_admitted_usage(current, session_id=session_id, session_agent=agent,
                    time_range=window, query=None if scope == "all" else
                    {key: value for key, value in query.items() if key != "project"})
                aliases = (getattr(current, "meta", None) or {}).get("aliases")
        else:
            entries = self.usage_source.load_all_entries()
        index = SessionIndex(getattr(entries, "session_metadata", ()))
        keys = {(entry.agent_id, entry.session_id) for entry in entries if entry.session_id == session_id and (not agent or entry.agent_id == agent)}
        keys.update(key for key in index.metadata if key[1] == session_id and (not agent or key[0] == agent))
        roots = {index.root(key) for key in keys}
        if len(roots) > 1:
            raise ValueError("Session ID is ambiguous; specify session_agent")
        if not roots:
            return {"meta": None, "entries": [], "scope": scope}
        root = next(iter(roots))
        entries = [replace(entry, project=canonical_project(entry.project, aliases)) for entry in entries
                   if index.root((entry.agent_id, entry.session_id)) == root]
        if scope != "all":
            display_query = dict(query)
            if query.get("project"):
                display_query["project"] = [canonical_project(value, aliases) for value in query["project"]]
            entries = filter_values(filter_time(entries, window), display_query)
            if first(query, "cost_status", "") == "unknown":
                entries = [entry for entry in entries if entry.cost_usd is None]
        entries.sort(key=lambda entry: entry.timestamp)
        if not entries:
            return {"meta": None, "entries": [], "scope": scope}
        meta = conversation_stats(entries, root, index, aliases)
        meta["machine"] = machine
        meta["executions"] = []
        for key, items in group_execution_sessions(entries).items():
            identity = index.metadata.get(key, {})
            meta["executions"].append({**session_stats(items), "title": identity.get("title"),
                                       "parent_session_id": identity.get("parent_session_id"), "is_main": key == root})
        return {"meta": meta, "entries": [entry_json(entry) for entry in entries], "scope": scope}


    def cost_diagnostics(self, query):
        """Group unpriced observations without starting remote collection."""
        time_range = session_window(query, self.clock())
        counts = defaultdict(int)

        def collect(entries, machine, aliases=None):
            projects = [canonical_project(value, aliases) for value in query.get("project", [])]
            for entry in filter_time(entries, time_range):
                if entry.cost_usd is not None:
                    continue
                if projects and canonical_project(entry.project, aliases) not in projects:
                    continue
                if query.get("agent") and entry.agent_id not in query["agent"]:
                    continue
                if query.get("model") and entry.model not in query["model"]:
                    continue
                reason = (entry.cost_provenance or {}).get("reason") or "unrecorded"
                counts[(machine, entry.agent_id, entry.model, reason)] += 1

        if self.mode.enabled():
            with self.generations.generation_admission_snapshot() as admission:
                for current in admission.admitted:
                    if query.get("machine") and current.host not in query["machine"]:
                        continue
                    selection = {key: query[key] for key in ("agent", "model") if key in query}
                    selection["cost_status"] = ["unknown"]
                    entries = self.snapshots.read_admitted_usage(current, time_range=time_range, query=selection)
                    collect(entries, current.host, (current.meta or {}).get("aliases"))
        else:
            machine = self.machines.load_machine_config().self_machine.name
            if not query.get("machine") or machine in query["machine"]:
                collect(self.usage_source.load_all_entries(read_only=True, diagnostic=True), machine)
        return {"groups": [dict(zip(("machine", "agent", "model", "reason"), key), entries=count)
                           for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]}
