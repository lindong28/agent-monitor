"""Usage source scanning, retained archive loading and cost enrichment."""
import errno
import hashlib
import json
import logging
import os
import stat
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from agent_monitor.contracts.calendar import BUCKET_TIMEZONE as USAGE_TIMEZONE

from agent_monitor.contracts.usage import LoadedEntries, complete_usage_observation
from parsers import claude, codex, codex_profiles
from project_paths import PROJECT_ROOT
from .project_identity import ProjectIdentityBlocked

logger = logging.getLogger(__name__)


class UsageLoader:
    """One loader's cache, backed by its explicitly supplied identity store."""

    def __init__(self, identity_store, *, cache=None):
        self.identity_store = identity_store
        self.cache = cache
        self._lock = threading.RLock()

    def load_all_entries(self, force_reload=False, read_only=False, diagnostic=False, archive_path=None):
        with self.identity_store.locked():
            with self._lock:
                from agent_monitor.ingestion.usage_archive import UsageArchive

                source_errors = []
                authority = None
                session_metadata = []
                if not (read_only or diagnostic) and self.cache is not None and not isinstance(self.cache, UsageArchive):
                    # Retain the existing injectable cache interface for callers.
                    if force_reload:
                        self.cache.clear()
                    entries = self.cache.load()
                else:
                    archive = UsageArchive(archive_path or Path(self.identity_store.path).with_name("usage_archive.sqlite3"))
                    if not (read_only or diagnostic):
                        self.cache = archive
                    entries, authority = self.load_archived_entries(
                        archive, source_errors, force_reload, read_only or diagnostic,
                    )
                    session_metadata = archive.session_metadata
                loaded = self.enrich_entries(
                    self.deduped(entries, source_errors),
                    read_only=read_only or diagnostic,
                    diagnostic=diagnostic,
                    source_errors=source_errors,
                )
                loaded.history_authoritative_from = authority
                loaded.session_metadata = session_metadata
                return loaded

    def parser_fingerprint(self):
        digest = hashlib.sha256()
        # Archive parse signatures track their actual behavior/model owners. This is
        # deliberately distinct from deployment and export source inventories.
        from . import project_identity
        from agent_monitor.contracts import usage
        for path in (Path(__file__), Path(project_identity.__file__), Path(usage.__file__),
                     PROJECT_ROOT / "parsers" / "__init__.py", Path(claude.__file__),
                     Path(codex.__file__), Path(codex_profiles.__file__)):
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def load_archived_entries(self, archive, source_errors, force_reload, read_only):
        links = codex_profiles.session_links(source_errors=source_errors)
        paths = self.usage_paths(source_errors=source_errors)
        fingerprint = self.parser_fingerprint()
        contexts = {}
        sessions = []
        roots = codex_profiles.sources(source_errors)
        # Sessions and archived_sessions in one home share their ancestry/model DB.
        homes = {}
        for root, state_db, account in roots:
            homes.setdefault(state_db, ([], account))[0].extend(path for path in paths if path.is_relative_to(root))
        for state_db, (source_paths, account) in homes.items():
            accepted, models, history, signature, digest = self.codex_source_context(source_paths, state_db, source_errors)
            rejected = set(source_paths) - set(accepted)
            paths = [path for path in paths if path not in rejected]
            for path in accepted:
                contexts[path] = (models, history, signature, digest)
            metadata = codex.session_metadata(history, models)
            for row in metadata:
                row["account"] = None if links.excludes(row["session_id"]) else account
            sessions.extend(metadata)

        def signature_for(path):
            file_stat = path.stat()
            metadata = None
            if path in contexts:
                codex_models, history, metadata_signature, model_mapping_digest = contexts[path]
                if metadata_signature is None:
                    return None
                # This signature names the mapping snapshot consumed by this
                # collection, not the live database after parsing has started.
                # The next collection reads and validates the mapping again.
                dependencies = history.signature(path)
                if dependencies is None:
                    return None
                metadata = (model_mapping_digest, dependencies)
            return json.dumps((file_stat.st_mtime_ns, file_stat.st_size, fingerprint, metadata))

        def parse(path):
            context = contexts.get(path)
            return self.parse_usage_file(path, source_errors=source_errors,
                                     codex_models=context[0] if context else None,
                                     codex_history=context[1] if context else None)

        if read_only:
            from agent_monitor.ingestion.cache import MtimeCache

            retained, authority = archive.read()
            from agent_monitor.contracts.session import merge_metadata
            archive.session_metadata = links.apply(merge_metadata(archive.session_metadata, sessions))
            entries = MtimeCache(lambda: paths, parse).load(source_errors=source_errors)
            return self.deduped(list(entries) + retained, source_errors), authority
        return archive.collect(paths, parse, signature_for, source_errors, USAGE_TIMEZONE,
                               force_reload=force_reload, session_metadata=sessions,
                               session_metadata_transform=links.apply)

    def codex_source_context(self, paths, state_db, source_errors):
        metadata_signature = None
        model_mapping_digest = None
        codex_models = {}
        if paths:
            before_errors = len(source_errors)
            try:
                metadata_signature = codex._metadata_signature(Path(state_db))
            except FileNotFoundError:
                # The existing parser permits metadata absence and falls back to
                # session/default model; absence never qualifies for cache reuse.
                pass
            except OSError as exc:
                self.append_source_error(source_errors, state_db, "metadata", exc)
            codex_models = codex._load_thread_models(
                state_db, immutable=True, source_errors=source_errors,
            )
            if len(source_errors) != before_errors:
                # Failed metadata is not evidence that every recorded model became
                # the default. Retain archived Codex events until the next good read.
                paths = []
            try:
                after = codex._metadata_signature(Path(state_db))
            except OSError:
                after = None
            if len(source_errors) != before_errors or after != metadata_signature:
                metadata_signature = None
            if metadata_signature is not None:
                # The same successfully read mapping feeds both the digest and
                # parser. Unrelated DB/WAL writes do not change parsing inputs.
                model_mapping_digest = hashlib.sha256(
                    json.dumps(codex_models, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()

        history = codex.HistoryResolver(paths)
        return paths, codex_models, history, metadata_signature, model_mapping_digest

    def deduped(self, entries, source_errors=None):
        unique = {}
        for entry in entries:
            key = entry.dedup_key
            if key not in unique:
                unique[key] = entry
            elif entry.agent_id == "claude-code":
                unique[key] = complete_usage_observation(unique[key], entry, source_errors)
        return list(unique.values())

    def enrich_entries(self, entries, read_only=False, diagnostic=False, source_errors=()):
        try:
            from agent_monitor.accounting.pricing_fetcher import with_calculated_cost; from agent_monitor.accounting.pricing_fetcher import get_pricing; from agent_monitor.accounting.pricing_fetcher import PreparedPricing
        except ImportError:
            return LoadedEntries(
                sorted(entries, key=lambda entry: entry.timestamp),
                source_errors=source_errors,
                scan_complete=not source_errors,
            )

        pricing = PreparedPricing(get_pricing(persist=not read_only))
        project_paths = sorted({entry.project for entry in entries if self.looks_like_path(entry.project)})
        projects = {}
        pending = []
        blocked = {}
        persisted_blockers = self.identity_store.list_blockers(
            status="active",
            immutable=diagnostic,
        )
        persisted_by_path = {
            item["source_path"]: item
            for item in persisted_blockers
        }
        identity_cache = {} if read_only else self.identity_store.project_cache
        for path in project_paths:
            try:
                projects[path] = self.identity_store.resolve(
                    path,
                    identity_cache,
                    persist=not read_only,
                    ignore_persisted_blocker=diagnostic,
                )
            except ProjectIdentityBlocked:
                pending.append(path)

        for path in pending:
            try:
                projects[path] = self.identity_store.resolve(
                    path,
                    identity_cache,
                    persist=not read_only,
                    ignore_persisted_blocker=diagnostic,
                )
            except ProjectIdentityBlocked as exc:
                if read_only:
                    blocked[path] = self.unpersisted_blocker_dict(exc)
                else:
                    blocked[path] = self.identity_store.record_blocker(exc)
                logger.warning("Blocking source path %s from this rollup: %s", path, exc)

        enriched = []
        unattributed = []
        blocked_paths = set(blocked) | set(persisted_by_path)
        for entry in entries:
            priced_entry = with_calculated_cost(entry, pricing=pricing)
            if entry.project in blocked_paths:
                unattributed.append(priced_entry)
                continue
            project = projects.get(entry.project, entry.project)
            enriched.append(replace(priced_entry, project=project))
        enriched.sort(key=lambda entry: entry.timestamp)
        unattributed.sort(key=lambda entry: entry.timestamp)
        blockers_by_path = dict(persisted_by_path)
        for path, blocker in blocked.items():
            if diagnostic:
                blockers_by_path[path] = blocker
            else:
                blockers_by_path.setdefault(path, blocker)
        active_blockers = [blockers_by_path[path] for path in sorted(blockers_by_path)]
        current_blockers = [blocked[path] for path in sorted(blocked)]
        authoritative_projects = None
        if persisted_blockers or current_blockers:
            excluded_projects = set()
            # Keep every candidate from both observations, including conflicting
            # candidates for the same path that the display projection coalesces.
            for blocker in persisted_blockers + current_blockers:
                source_path = blocker["source_path"]
                excluded_projects.update(filter(None, (
                    source_path,
                    blocker.get("resolved_candidate"),
                    blocker.get("resolved_project"),
                    blocker.get("pin_candidate"),
                    self.identity_store.stored_identity(source_path, immutable=diagnostic),
                )))
            authoritative_projects = self.identity_store.stored_projects(immutable=diagnostic) - excluded_projects
        return LoadedEntries(
            enriched,
            blocked_sources=active_blockers,
            persisted_blockers=persisted_blockers if diagnostic else (),
            current_blockers=current_blockers if diagnostic else (),
            unattributed_entries=unattributed,
            source_errors=source_errors,
            scan_complete=not source_errors,
            history_authoritative_projects=authoritative_projects,
        )

    def unpersisted_blocker_dict(self, blocker):
        return {
            "source_path": blocker.source_path,
            "reason": blocker.reason,
            "resolved_candidate": blocker.resolved_project,
            "resolved_project": blocker.resolved_project,
            "pin_candidate": blocker.pin_candidate,
            "first_seen": None,
            "last_seen": None,
            "status": "active",
        }

    def usage_paths(self, source_errors=None):
        paths = []
        for base_dir in claude._get_claude_dirs():
            base = Path(base_dir)
            if source_errors is not None:
                paths.extend(self.walk_jsonl_paths(base, source_errors))
            elif base.is_dir():
                paths.extend(base.rglob("*.jsonl"))
        for codex_sessions, _state_db, _account in codex_profiles.sources(source_errors):
            if source_errors is not None:
                paths.extend(self.walk_jsonl_paths(codex_sessions, source_errors))
            elif codex_sessions.is_dir():
                paths.extend(codex_sessions.rglob("*.jsonl"))
        extra = os.environ.get("AGENT_MONITOR_EXTRA_JSONL", "")
        for raw_path in extra.split(","):
            if raw_path.strip():
                paths.append(Path(raw_path.strip()))
        return paths

    def walk_jsonl_paths(self, base, source_errors):
        try:
            mode = os.stat(base).st_mode
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                self.append_source_error(source_errors, base, "scan", exc)
            return []
        if not stat.S_ISDIR(mode):
            self.append_source_error(
                source_errors,
                base,
                "scan",
                NotADirectoryError(errno.ENOTDIR, "session root is not a directory", str(base)),
            )
            return []

        paths = []

        def record_walk_error(exc):
            if exc.errno == errno.ENOENT:
                return
            self.append_source_error(
                source_errors,
                getattr(exc, "filename", None) or base,
                "scan",
                exc,
            )

        try:
            for root, _dirs, filenames in os.walk(
                base,
                onerror=record_walk_error,
                followlinks=False,
            ):
                paths.extend(
                    Path(root) / filename
                    for filename in filenames
                    if filename.endswith(".jsonl")
                )
        except OSError as exc:
            record_walk_error(exc)
        return paths

    def parse_usage_file(self, path, source_errors=None, codex_models=None, codex_history=None):
        path = Path(path)
        if codex_models is not None or self.is_codex_usage_path(path):
            if codex_models is None:
                codex_models = codex._load_thread_models(
                    next(db for root, db, _account in codex_profiles.sources() if path.is_relative_to(root)),
                    immutable=source_errors is not None,
                    source_errors=source_errors,
                )
            return codex.parse_file(
                path,
                models=codex_models,
                source_errors=source_errors,
                history=codex_history,
            )

        fallback_project = "unknown"
        for base_dir in claude._get_claude_dirs():
            base = Path(base_dir)
            try:
                path.relative_to(base)
            except ValueError:
                continue
            fallback_project = claude._extract_project_from_dir(path, base)
            break
        return claude.parse_file(
            path,
            fallback_project=fallback_project,
            source_errors=source_errors,
        )

    def is_codex_usage_path(self, path):
        return any(Path(path).is_relative_to(root) for root, _db, _account in codex_profiles.sources())

    def append_source_error(self, source_errors, path, stage, exc):
        if source_errors is None:
            return
        error = {
            "path": str(path),
            "stage": stage,
            "error": "%s: %s" % (type(exc).__name__, exc),
        }
        if error not in source_errors:
            source_errors.append(error)

    def looks_like_path(self, value):
        return isinstance(value, str) and (value.startswith("/") or value.startswith("~"))
