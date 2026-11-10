from agent_monitor.quota import quota_service
import base64
import contextlib
import builtins
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from agent_monitor.contracts.usage import LoadedEntries, RateLimits
from agent_monitor.ingestion import ProjectIdentityStore, UsageLoader
from agent_monitor.snapshots import exporter
from parsers import codex, codex_profiles
from agent_monitor import http_server as server
from agent_monitor.snapshots import statistics_snapshot
from agent_monitor.contracts.session import SessionIndex, merge_metadata
from agent_monitor.ingestion.usage_archive import UsageArchive


class ManagedCodexTests(unittest.TestCase):
    def setUp(self):
        real_home = Path.home()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.loader = UsageLoader(ProjectIdentityStore(self.root / "project_identity.db"))
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, {"HOME": str(self.root), "AGENT_MONITOR_EXTRA_JSONL": ""}))
        self.default = self.root / ".codex"
        self.stack.enter_context(mock.patch("parsers.accounts.CODEX_AUTH", self.default / "auth.json"))
        for target, value in (("parsers.codex.SESSIONS_DIR", str(self.default / "sessions")),
                              ("parsers.codex.STATE_DB", str(self.default / "state_5.sqlite")),
                              ("parsers.claude_status.STATUS_FILE", str(self.root / ".claude/tt-status.json")),
                              ("parsers.claude._get_claude_dirs", None)):
            self.stack.enter_context(mock.patch(target, return_value=[]) if value is None else mock.patch(target, value))
        self.registry = self.root / ".local/share/codex-accounts"
        protected = [(real_home / child).resolve() for child in (".codex", ".claude", ".local/share/codex-accounts")]

        def guarded(original):
            def call(path, *args, **kwargs):
                if isinstance(path, (str, bytes, os.PathLike)):
                    resolved = Path(os.fsdecode(path)).resolve()
                    if any(resolved == root or root in resolved.parents for root in protected):
                        raise AssertionError("test attempted real provider access")
                return original(path, *args, **kwargs)
            return call

        for module, name in ((builtins, "open"), (io, "open"), (os, "scandir"), (os, "listdir"), (sqlite3, "connect")):
            self.stack.enter_context(mock.patch.object(module, name, guarded(getattr(module, name))))
        with self.assertRaisesRegex(AssertionError, "real provider access"):
            open(real_home / ".codex" / "guard-positive-control")

    @staticmethod
    def active(reading):
        return RateLimits(reading["five_hour_used_pct"], reading["five_hour_resets_at"],
                          reading["seven_day_used_pct"], reading["seven_day_resets_at"],
                          updated_at=reading["observed_at"],
                          quota_observation={"source": "active_query", "observed_at": reading["observed_at"]})

    def profile(self, name, identity=True):
        home = self.registry / name
        home.mkdir(parents=True)
        (home / "profile.json").write_text(json.dumps({"schema_version": 1, "name": name,
            "identity": {"account_id": "shared-workspace", "email": name + "@example.test", "subject": name} if identity else None}))
        codex_home = home / "codex"
        codex_home.mkdir()
        if identity:
            claim = base64.urlsafe_b64encode(json.dumps({"email": name + "@example.test"}).encode()).decode().rstrip("=")
            (codex_home / "auth.json").write_text(json.dumps({"tokens": {"account_id": "shared-workspace", "id_token": "fixture." + claim + ".fixture"}}))
        return codex_home

    def test_automatic_managed_skip_keeps_old_reading_and_normal_cadence(self):
        from agent_monitor.network import automatic_egress as gate
        from agent_monitor.quota import quota_refresh
        from agent_monitor.runtime import hub
        from agent_monitor import hub_config
        self.profile("alpha")
        old = dict(account_profile="alpha", account_id="shared-workspace", account_label="alpha@example.test",
                   updated_at="2026-10-01T00:00:00Z", five_hour_pct=17, refresh_attempted_at="2026-10-01T00:00:00Z")
        path = self.root / "state/quota_snapshot.json"
        quota_service.save_cache(path, {"codex": {"managed_accounts": [old]}})
        batch = gate.Batch()
        with mock.patch.object(gate.Batch, "environment", side_effect=gate.Skipped("china")) as check, \
                mock.patch.object(quota_refresh.subprocess, "Popen") as spawn, \
                mock.patch.object(quota_refresh.network_policy, "endpoint", return_value="http://agent-monitor:fixture@127.0.0.1:9"), \
                mock.patch.object(hub_config, "quota_interval", return_value=300):
            first = quota_service.managed_codex_rate_limits(root=self.root, refresh=True, scheduled=True, automatic_batch=batch)[0]
            second = quota_service.managed_codex_rate_limits(root=self.root, refresh=True, scheduled=True, automatic_batch=batch)[0]
        spawn.assert_not_called(); self.assertEqual(check.call_count, 1)
        self.assertEqual(second["query_state"], "skipped")
        self.assertEqual(second["five_hour_pct"], 17)
        self.assertEqual(second["updated_at"], old["updated_at"])
        self.assertEqual(second["refresh_attempted_at"], old["refresh_attempted_at"])
        self.assertEqual(first["refresh_skip"], second["refresh_skip"])
        with mock.patch.object(hub, "native_quota_due", return_value=False), \
                mock.patch("agent_monitor.quota.quota_settings.read", return_value={"accounts": [dict(profile="alpha", account_id=old["account_id"], account_label=old["account_label"], enabled=True)]}), \
                mock.patch.object(hub_config, "quota_interval", return_value=300):
            from datetime import datetime
            checked = datetime.fromisoformat(first["refresh_skip"]["checked_at"]).timestamp()
            self.assertFalse(hub.quota_due(root=self.root, now=checked + 299))
            self.assertTrue(hub.quota_due(root=self.root, now=checked + 301))

    def test_credential_plan_survives_newer_cached_reading_and_native_home(self):
        home = self.profile("alpha")
        cached = dict(account_profile="alpha", account_id="shared-workspace",
                      account_label="alpha@example.test", updated_at="2026-11-01T00:00:00Z",
                      reading_plan=None, credential_plan=None, account_plan=None,
                      five_hour_pct=17, refresh_attempted_at="2026-11-01T00:00:00Z")
        path = self.root / "state/quota_snapshot.json"
        path.parent.mkdir()
        for plan in ("prolite", "self_serve_business_prolite", None):
            claim = {"email": "alpha@example.test", "https://api.openai.com/auth": {"chatgpt_plan_type": plan}}
            token = base64.urlsafe_b64encode(json.dumps(claim).encode()).decode().rstrip("=")
            (home / "auth.json").write_text(json.dumps({"tokens": {"account_id": "shared-workspace", "id_token": "fixture." + token + ".fixture"}}))
            for native in (False, True):
                path.write_text(json.dumps({"codex": dict(cached, managed_accounts=[cached])}))
                with mock.patch("parsers.accounts.CODEX_AUTH", home / "auth.json" if native else self.default / "auth.json"):
                    row = quota_service.managed_codex_rate_limits(root=self.root)[0]
                self.assertEqual(row["credential_plan"], plan)
                self.assertEqual(row["account_plan"], plan)
                self.assertEqual(row["five_hour_pct"], 17)
                current = SimpleNamespace(host="fixture", meta={"rate_limits": {"codex": {"managed_accounts": [row]}}})
                with mock.patch("agent_monitor.http_server.hub.enabled", return_value=False), mock.patch("agent_monitor.quota.quota_view.self_machine_name", return_value="fixture"):
                    visible = server.QUOTA_VIEW.live_from_admission(SimpleNamespace(admitted=[current]))["codex"]["accounts"][0]
                self.assertEqual(visible["account_plan"], plan)
                self.assertEqual(visible["five_hour_used_pct"], 17)
        # Neither a shared workspace nor a matching email alone admits metadata.
        for account_id, email in (("other-workspace", "alpha@example.test"), ("shared-workspace", "other@example.test")):
            claim = {"email": email, "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"}}
            token = base64.urlsafe_b64encode(json.dumps(claim).encode()).decode().rstrip("=")
            (home / "auth.json").write_text(json.dumps({"tokens": {"account_id": account_id, "id_token": "fixture." + token + ".fixture"}}))
            self.assertIsNone(quota_service.managed_codex_rate_limits(root=self.root)[0]["credential_plan"])

    def test_cache_merges_metadata_independently_from_quota_clock(self):
        old = dict(account_profile="alpha", account_id="workspace", account_label="alpha@example.test",
                   updated_at="2026-11-01T00:00:00Z", reading_plan="plus", credential_plan=None,
                   account_plan="plus", five_hour_pct=17, refresh_error=None)
        fresh_metadata = dict(old, updated_at="2026-10-01T00:00:00Z", reading_plan=None,
                              credential_plan="pro", account_plan="pro", five_hour_pct=5)
        merged = quota_service._merge_managed_accounts([old], [fresh_metadata])[0]
        self.assertEqual((merged["reading_plan"], merged["credential_plan"], merged["account_plan"], merged["five_hour_pct"]), ("plus", "pro", "plus", 17))
        path = self.root / "state/quota_snapshot.json"
        quota_service.save_cache(path, {"codex": old})
        quota_service.save_cache(path, {"codex": fresh_metadata})
        self.assertEqual(quota_service.read_cache(path)["codex"]["credential_plan"], "pro")
        legacy = dict(old, updated_at="2026-12-01T00:00:00Z")
        del legacy["credential_plan"]
        self.assertEqual(quota_service._merge_managed_accounts([fresh_metadata], [legacy])[0]["credential_plan"], "pro")
        quota_service.save_cache(path, {"codex": legacy})
        self.assertEqual(quota_service.read_cache(path)["codex"]["credential_plan"], "pro")
        cleared = dict(fresh_metadata, credential_plan=None)
        self.assertIsNone(quota_service._merge_managed_accounts([old], [cleared])[0]["credential_plan"])

    def test_retained_native_reading_does_not_cross_workspace_members(self):
        from parsers import accounts
        old = dict(account_id="workspace", account_label="other@example.test", credential_plan="plus",
                   reading_plan=None, account_plan="plus", updated_at="2099-01-01T00:00:00Z")
        with mock.patch.object(quota_service, "read_cache", return_value={"codex":old}), \
             mock.patch.object(accounts, "codex_account", return_value=accounts.Account("workspace", "alpha@example.test", "pro")), \
             mock.patch.object(accounts, "claude_account", return_value=None), \
             mock.patch("agent_monitor.quota.quota_applicability.unavailable", return_value=True), \
             mock.patch.object(codex, "load_rate_limits", return_value=None):
            result = quota_service.rate_limits(root=self.root)["codex"]
        self.assertEqual(result["account_label"], "alpha@example.test")
        self.assertEqual(result["credential_plan"], "pro")
        self.assertNotEqual(result["updated_at"], old["updated_at"])


    def session(self, home, sid, tokens, used, model="gpt-5", archived=False):
        folder = home / ("archived_sessions" if archived else "sessions")
        folder.mkdir(parents=True, exist_ok=True)
        rows = [{"type": "session_meta", "payload": {"id": sid, "cwd": "repo", "source": "cli", "model": model}},
                {"timestamp": "2026-10-01T00:00:00Z", "type": "event_msg", "payload": {"type": "token_count",
                 "info": {"total_token_usage": {"input_tokens": tokens, "output_tokens": 2, "cached_input_tokens": 0}},
                 "rate_limits": {"primary": {"used_percent": used, "window_minutes": 300, "resets_at": 4102444800}}}}]
        (folder / (sid + ".jsonl")).write_text("\n".join(map(json.dumps, rows)) + "\n")
        with sqlite3.connect(home / "state_5.sqlite") as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT, model TEXT, title TEXT)")
            conn.execute("INSERT INTO threads VALUES (?, ?, ?)", (sid, model, "Task " + model))

    def collect(self):
        archive = UsageArchive(self.root / "archive.sqlite3")
        errors = []
        entries, _ = self.loader.load_archived_entries(archive, errors, False, False)
        return LoadedEntries(entries, session_metadata=archive.session_metadata), errors

    def test_two_profiles_default_archive_models_snapshot_and_session_api(self):
        self.session(self.default, "default", 3, 5, "default-model")
        for name, tokens, used in (("alpha", 10, 21), ("beta", 30, 82)):
            self.session(self.profile(name), name, tokens, used, name + "-model", archived=name == "beta")
        entries, errors = self.collect()
        self.assertEqual(errors, [])
        self.assertEqual(sum(e.input_tokens for e in entries), 43)
        self.assertEqual({e.session_id: e.model for e in entries}, {n: n + "-model" for n in ("default", "alpha", "beta")})
        snapshot = statistics_snapshot.write_snapshot(self.root / "snapshot.db", entries)
        self.assertEqual(snapshot["version"], 5)
        loaded = LoadedEntries(snapshot["usage_entries"], session_metadata=snapshot["session_metadata"])
        views = server.create_views(usage_source=self.loader)
        with mock.patch("agent_monitor.http_server.hub.enabled", return_value=False), mock.patch.object(self.loader, "load_all_entries", return_value=loaded):
            rows = {r["session_id"]: r for r in server.sessions_endpoint({"range": ["all"]}, views=views)}
            self.assertIsNone(rows["default"]["account"])
            self.assertEqual({sid: r["title"] for sid, r in rows.items()}, {n: "Task " + n + "-model" for n in ("default", "alpha", "beta")})
            for name in ("alpha", "beta"):
                self.assertEqual(rows[name]["account"]["email"], name + "@example.test")
                detail = server.session_detail(name, query={"range": ["all"]}, views=views)
                self.assertEqual(detail["meta"]["account"], rows[name]["account"])

    def test_shared_roots_do_not_duplicate_usage_or_bind_passive_quota(self):
        self.session(self.default, "shared", 7, 91)
        self.session(self.default, "archived", 11, 92, archived=True)
        for name in ("alpha", "beta"):
            home = self.profile(name)
            home.mkdir(exist_ok=True)
            for child in ("sessions", "archived_sessions"):
                (home / child).symlink_to(self.default / child)
        self.session(self.profile("legacy"), "legacy", 13, 23)
        sources = codex_profiles.sources()
        self.assertEqual(len(sources), 4)
        self.assertEqual(len({path.resolve() for path, _, _ in sources}), 4)
        entries, errors = self.collect()
        self.assertEqual(errors, [])
        self.assertEqual(sum(e.input_tokens for e in entries), 31)
        rows = {r["session_id"]: r for r in entries.session_metadata}
        for sid in ("shared", "archived"):
            self.assertIsNone(rows[sid]["account"])
        self.assertEqual(rows["legacy"]["account"]["name"], "legacy")
        blocks = {b["account_profile"]: b for b in quota_service.managed_codex_rate_limits()}
        for name in ("alpha", "beta"):
            self.assertIsNone(blocks[name]["five_hour_pct"])
        self.assertEqual(blocks["legacy"]["five_hour_pct"], 23)

    def test_default_quota_and_cache_do_not_attribute_shared_history(self):
        sid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        self.session(self.default, sid, 7, 91)
        account = quota_service.accounts.Account("default-id", "default@example.test", None)
        with mock.patch.object(quota_service.accounts, "codex_account", return_value=account), \
             mock.patch.object(quota_service.accounts, "claude_account", return_value=None), \
             mock.patch.object(quota_service.claude_status, "load_rate_limits", return_value=None), \
             mock.patch.object(quota_service.quota_refresh, "load_rate_limits", side_effect=AssertionError("no provider")):
            native = quota_service.rate_limits(root=self.root)["codex"]
            self.assertEqual(native["five_hour_pct"], 91)
            home = self.profile("work")
            home.mkdir(exist_ok=True)
            (home / "sessions").symlink_to(self.default / "sessions")
            for mapped in (False, True):
                if mapped:
                    links = self.registry / "session-links"
                    links.mkdir()
                    (links / (sid + ".json")).write_text(json.dumps({"schema_version": 2,
                        "storage_account": "default", "last_selected_account": "work", "account_names": ["work"]}))
                with self.subTest(mapped=mapped):
                    for cached in (None, native, dict(native, quota_observation={"source": "active_query"})):
                        with mock.patch.object(quota_service, "read_cache", return_value={"codex": cached} if cached else {}):
                            block = quota_service.rate_limits(root=self.root, use_cache=True)["codex"]
                        expected = 91 if cached and cached.get("quota_observation", {}).get("source") == "active_query" else None
                        self.assertEqual(block.get("five_hour_pct"), expected)

    def test_session_link_versions_and_invalid_records(self):
        root = self.registry / "session-links"
        root.mkdir(parents=True)
        sid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        path = root / (sid + ".json")
        for version, names, invalid in ((1, ["default"], False), (1, ["alpha"], True),
                                         (2, ["alpha"], False), (2, ["alpha", "beta"], False)):
            with self.subTest(version=version, names=names):
                path.write_text(json.dumps({"schema_version": version, "storage_account": "default",
                                           "last_selected_account": names[-1], "account_names": names}))
                links = codex_profiles.session_links()
                self.assertEqual(bool(links.errors), invalid)
                self.assertEqual(links.excludes(sid), invalid or len(names) > 1)
        for value in ("not-json", "[]", json.dumps({"schema_version": 2, "storage_account": "default",
                      "last_selected_account": "beta", "account_names": ["alpha"]})):
            path.write_text(value)
            links = codex_profiles.session_links()
            self.assertTrue(links.excludes(sid))
            self.assertTrue(links.errors)

    def test_passive_quota_reaches_existing_accounts_consumer(self):
        for name, used in (("alpha", 21), ("beta", 82)):
            self.session(self.profile(name), name, 10, used)
        with mock.patch("agent_monitor.quota.quota_service.quota_refresh.load_rate_limits", side_effect=AssertionError("no active queries")):
            blocks = quota_service.managed_codex_rate_limits()
        current = SimpleNamespace(host="fixture", meta={"rate_limits": {"codex": {"signed_out": True, "managed_accounts": blocks}}})
        admission = SimpleNamespace(admitted=[current])
        with mock.patch("agent_monitor.http_server.hub.enabled", return_value=False), mock.patch("agent_monitor.quota.quota_view.self_machine_name", return_value="fixture"):
            rows = server.QUOTA_VIEW.live_from_admission(admission)["codex"]["accounts"]
        self.assertEqual({r["account_label"]: r["five_hour_used_pct"] for r in rows}, {"alpha@example.test": 21, "beta@example.test": 82})
        self.assertEqual({r["account_profile"] for r in rows}, {"alpha", "beta"})
        self.assertEqual({r["plan_verification"] for r in rows}, {"unverified"})

    def test_opt_in_active_refresh_reuses_account_bound_reader(self):
        self.session(self.profile("alpha"), "alpha", 10, 21)
        calls = []
        reading = {"observed_at": "2026-11-01T00:00:00Z", "five_hour_used_pct": 34,
                   "five_hour_resets_at": 1900000000, "seven_day_used_pct": 12,
                   "seven_day_resets_at": 1901000000}
        with mock.patch("agent_monitor.quota.quota_refresh.load_rate_limits",
                        side_effect=lambda provider, account, **kw: calls.append((kw["codex_home"], account.account_id)) or self.active(reading)):
            blocks = quota_service.managed_codex_rate_limits(refresh=True, root=self.root)
        self.assertEqual(calls, [(self.registry / "alpha" / "codex", "shared-workspace")])
        self.assertEqual(blocks[0]["five_hour_pct"], 34)
        self.assertEqual(blocks[0]["seven_day_pct"], 12)
        self.assertEqual(blocks[0]["quota_observation"]["source"], "active_query")

    def test_enrollment_binds_identity_and_disable_overrides_config(self):
        from agent_monitor.quota import quota_settings
        self.profile("alpha")
        with mock.patch("agent_monitor.hub_config.managed_quota_auto_refresh", return_value=False):
            initial = quota_settings.read(self.root)
            self.assertFalse(initial["accounts"][0]["enabled"])
            selected = [initial["accounts"][0]["key"]]
            enabled = quota_settings.change({"revision": initial["revision"], "selected": selected}, self.root)
            self.assertTrue(enabled["accounts"][0]["enabled"])
            self.profile("beta")
            current = quota_settings.read(self.root)
            self.assertEqual(sum(row["enabled"] for row in current["accounts"]), 1)
            with self.assertRaises(ValueError):
                quota_settings.change({"revision": enabled["revision"], "selected": selected}, self.root)
            changed = self.registry / "alpha/profile.json"
            value = json.loads(changed.read_text())
            value["identity"]["email"] = "new@example.test"
            changed.write_text(json.dumps(value))
            current = quota_settings.read(self.root)
            self.assertFalse(any(row["enabled"] for row in current["accounts"]))
            quota_settings.change({"revision": current["revision"], "selected": []}, self.root)
        with mock.patch("agent_monitor.hub_config.managed_quota_auto_refresh", return_value=True):
            self.assertFalse(any(row["enabled"] for row in quota_settings.read(self.root)["accounts"]))

    def test_bounded_batch_continues_without_repeating_completed_account(self):
        from datetime import datetime, timezone
        from agent_monitor.quota import quota_settings
        from agent_monitor.runtime import hub
        self.profile("alpha"); self.profile("beta")
        initial = quota_settings.read(self.root)
        quota_settings.change({"revision": initial["revision"], "selected": [r["key"] for r in initial["accounts"]]}, self.root)
        now = [0]
        reading = RateLimits(5, 4102444800, 42, 4102444800,
                            updated_at=datetime.now(timezone.utc).isoformat(), quota_observation={"source": "active_query"})
        def query(provider, account, **kwargs):
            now[0] = 70
            return reading
        with mock.patch.object(quota_service.time, "monotonic", side_effect=lambda: now[0]), \
             mock.patch("agent_monitor.quota.quota_refresh.load_rate_limits", side_effect=query) as run:
            rows = quota_service.managed_codex_rate_limits(refresh=True, scheduled=True, enrollment=True, root=self.root, deadline=100)
        self.assertEqual(run.call_count, 1)
        self.assertEqual([r["query_state"] for r in rows], ["succeeded", "pending"])
        hub.mark_quota_collected(root=self.root)
        self.assertTrue(hub.quota_due(root=self.root))
        with mock.patch("agent_monitor.quota.quota_refresh.load_rate_limits", return_value=reading) as run:
            rows = quota_service.managed_codex_rate_limits(refresh=True, scheduled=True, enrollment=True, root=self.root)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[1].label, "beta@example.test")
        self.assertTrue(all(r["seven_day_pct"] == 42 for r in rows))
        self.assertFalse(hub.quota_due(root=self.root))

    def test_failure_pauses_before_next_account_and_preserves_reading(self):
        from agent_monitor.accounts.upstream_controls import Controls
        from agent_monitor.quota import quota_refresh
        self.session(self.profile("alpha"), "alpha", 1, 19)
        self.profile("beta")
        controls = Controls(self.root / "state/upstream_controls")
        def fail(*args, **kwargs):
            lease = controls.acquire("codex")
            lease.pause("fixture", failure={"stage": "quota_read", "account_profile": "alpha"})
            lease.close()
            raise quota_refresh.RefreshError("fixture failure")
        with mock.patch.object(quota_refresh, "load_rate_limits", side_effect=fail) as run:
            rows = quota_service.managed_codex_rate_limits(refresh=True, root=self.root)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(rows[0]["five_hour_pct"], 19)
        self.assertEqual(rows[1]["query_state"], "paused")
        self.assertEqual(controls.read("codex")["pause"]["failure"]["account_profile"], "alpha")

    def test_identity_change_during_read_never_publishes_observation(self):
        home = self.profile("alpha")
        def query(*args, **kwargs):
            (home / "auth.json").unlink()
            return RateLimits(33, None, 44, None, updated_at="2099-01-01T00:00:00Z")
        with mock.patch("agent_monitor.quota.quota_refresh.load_rate_limits", side_effect=query):
            row = quota_service.managed_codex_rate_limits(refresh=True, root=self.root)[0]
        self.assertEqual(row["query_state"], "local_unavailable")
        self.assertIsNone(row["seven_day_pct"])

    def test_account_action_observation_persists_into_all_matching_sources(self):
        from parsers.accounts import Account
        cache = self.root / "state/quota_snapshot.json"
        cache.parent.mkdir()
        row = {"account_id": "workspace", "account_label": "alpha@example.test", "updated_at": "2026-01-01T00:00:00Z"}
        cache.write_text(json.dumps({"codex": {**row, "managed_accounts": [{**row, "account_profile": "alpha"},
            {**row, "account_label": "beta@example.test", "account_profile": "beta", "seven_day_pct": 12}]}}))
        quota_service.record_codex_reading(cache, Account("workspace", "alpha@example.test"),
            {"observed_at": "2026-11-03T00:00:00Z", "seven_day_used_pct": 51})
        saved = quota_service.read_cache(cache)["codex"]
        self.assertEqual(saved["seven_day_pct"], 51)
        self.assertEqual([r["seven_day_pct"] for r in saved["managed_accounts"]], [51, 12])

    def test_native_refresh_preserves_managed_cache_even_when_native_logs_rejected(self):
        cache = self.root / "state/quota_snapshot.json"
        cache.parent.mkdir()
        row = {"account_profile": "alpha", "account_id": "workspace", "account_label": "alpha@example.test",
               "seven_day_pct": 48, "updated_at": "2026-11-03T00:00:00Z", "quota_observation": {"source": "active_query"}}
        cache.write_text(json.dumps({"codex": {"account_id": "native", "managed_accounts": [row]}}))
        quota_service.save_cache(cache, {"codex": {"account_id": "native", "refresh_error": None}},
                                 session_links=codex_profiles.SessionLinks(shared_store=True))
        self.assertEqual(quota_service.read_cache(cache)["codex"]["managed_accounts"], [row])

    def test_managed_recovery_targets_own_home_and_keeps_native_reading(self):
        from agent_monitor.quota import quota_recovery, quota_refresh
        from agent_monitor.accounts.upstream_controls import Controls
        from agent_monitor.snapshots import exporter, generation
        from parsers.accounts import Account
        home = self.profile("alpha")
        cache = self.root / "state/quota_snapshot.json"
        cache.parent.mkdir()
        native = {"account_id": "native", "account_label": "native@example.test", "seven_day_pct": 78}
        cache.write_text(json.dumps({"codex": native}))
        account = Account("shared-workspace", "alpha@example.test")
        controls = Controls(self.root / "state/upstream_controls")
        lease = controls.acquire("codex")
        lease.pause("fixture", failure={"account_fingerprint": quota_refresh.account_fingerprint("codex", account), "account_profile": "alpha"})
        lease.close()
        pause_id = controls.read("codex")["pause"]["id"]
        with mock.patch.object(exporter, "ROOT", self.root), \
             mock.patch.object(generation, "self_certified_host_identity", return_value="fixture"):
            status = quota_recovery.local_status()
        self.assertEqual(status["account"]["quota_profile"], "alpha")
        self.assertEqual(status["account"]["account_label"], account.label)
        reading = RateLimits(10, 4102444800, 30, 4102444800, updated_at="2026-11-03T00:00:00Z",
                             quota_observation={"source": "active_query"})
        with mock.patch.object(quota_refresh, "load_rate_limits", return_value=reading) as run:
            quota_service.recover_codex_quota(account.label, pause_id, expected_id=account.account_id, root=self.root, profile="alpha")
        self.assertEqual(run.call_args.kwargs["codex_home"], home)
        self.assertEqual(run.call_args.kwargs["probe_pause_id"], pause_id)
        saved = quota_service.read_cache(cache)["codex"]
        self.assertEqual(saved["seven_day_pct"], 78)
        self.assertEqual(saved["managed_accounts"][0]["seven_day_pct"], 30)
        self.assertIsNone(controls.read("codex").get("pause"))

    def test_legacy_recovery_finds_original_profile_without_switching_native(self):
        from agent_monitor.quota import quota_recovery, quota_refresh
        from agent_monitor.snapshots import generation
        from agent_monitor.accounts.upstream_controls import Controls
        from parsers.accounts import Account
        home = self.profile("alpha")
        other = self.profile("beta")
        self.default.mkdir()
        native_bytes = (other / "auth.json").read_bytes()
        (self.default / "auth.json").write_bytes(native_bytes)
        account = Account("shared-workspace", "alpha@example.test")
        controls = Controls(self.root / "state/upstream_controls")
        lease = controls.acquire("codex")
        lease.pause("fixture", failure={"account_fingerprint": quota_refresh.account_fingerprint("codex", account)})
        lease.close()
        pause = controls.read("codex")["pause"]
        with mock.patch.object(exporter, "ROOT", self.root), mock.patch.object(generation, "self_certified_host_identity", return_value="fixture"):
            status = quota_recovery.local_status()
        self.assertEqual(status["account"]["quota_profile"], "alpha")
        self.assertEqual(status["account"]["account_label"], account.label)
        self.assertEqual(controls.read("codex")["pause"], pause)
        reading = RateLimits(10, 4102444800, 30, 4102444800, updated_at="2026-11-10T00:00:00Z", quota_observation={"source": "active_query"})
        with mock.patch.object(quota_refresh, "load_rate_limits", return_value=reading) as run:
            quota_service.recover_codex_quota(account.label, pause["id"], expected_id=account.account_id, root=self.root, profile="alpha")
        self.assertEqual(run.call_args.kwargs["codex_home"], home)
        self.assertIsNone(controls.read("codex").get("pause"))
        self.assertEqual((self.default / "auth.json").read_bytes(), native_bytes)
        self.assertEqual(quota_service.read_cache(self.root / "state/quota_snapshot.json")["codex"]["managed_accounts"][0]["account_label"], account.label)
        subsequent = next(row for row in quota_service.managed_codex_rate_limits(root=self.root) if row["account_profile"] == "alpha")
        self.assertEqual(subsequent["account_label"], account.label)
        self.assertEqual(subsequent["seven_day_pct"], 30)

    def test_legacy_recovery_rejects_missing_ambiguous_and_changed_sources(self):
        from agent_monitor.quota import quota_recovery, quota_refresh
        from agent_monitor.snapshots import generation
        from agent_monitor.accounts.upstream_controls import Controls
        from parsers.accounts import Account
        account = Account("shared-workspace", "alpha@example.test")
        controls = Controls(self.root / "state/upstream_controls")
        lease = controls.acquire("codex")
        lease.pause("fixture", failure={"account_fingerprint": quota_refresh.account_fingerprint("codex", account)})
        lease.close()
        pause = controls.read("codex")["pause"]
        with mock.patch.object(exporter, "ROOT", self.root), mock.patch.object(generation, "self_certified_host_identity", return_value="fixture"):
            status = quota_recovery.local_status()
            self.assertEqual(status["account"]["resolution_reason"], "missing")
            alpha = self.profile("alpha")
            beta = self.profile("beta")
            self.default.mkdir()
            (self.default / "auth.json").write_bytes((beta / "auth.json").read_bytes())
            (alpha / "auth.json").unlink()
            status = quota_recovery.local_status()
            self.assertEqual(status["account"]["resolution_reason"], "identity_changed")
            (alpha / "auth.json").write_text(json.dumps({"tokens": {"account_id": "shared-workspace", "id_token": "fixture." + base64.urlsafe_b64encode(json.dumps({"email": account.label}).encode()).decode().rstrip("=") + ".fixture"}}))
            # Two independently registered sources for the same full identity.
            (beta / "auth.json").write_bytes((alpha / "auth.json").read_bytes())
            metadata = json.loads((beta.parent / "profile.json").read_text())
            metadata["identity"]["email"] = account.label
            (beta.parent / "profile.json").write_text(json.dumps(metadata))
            status = quota_recovery.local_status()
            self.assertEqual(status["account"]["resolution_reason"], "ambiguous")
            self.assertIsNone(status["account"]["account_id"])
            with mock.patch.object(quota_refresh, "load_rate_limits") as run:
                with self.assertRaises(quota_refresh.LocalUnavailable):
                    quota_service.recover_codex_quota(account.label, pause["id"], expected_id=account.account_id, root=self.root, profile="alpha")
                # A new exact native source must not silently replace approved alpha.
                (self.default / "auth.json").write_bytes((alpha / "auth.json").read_bytes())
                with self.assertRaises(quota_refresh.LocalUnavailable):
                    quota_service.recover_codex_quota(account.label, pause["id"], expected_id=account.account_id, root=self.root, profile="alpha")
                run.assert_not_called()
        self.assertEqual(controls.read("codex")["pause"], pause)

    def test_legacy_recovery_refuses_inconsistent_profile_metadata(self):
        from agent_monitor.quota import quota_recovery, quota_refresh
        from agent_monitor.snapshots import generation
        from agent_monitor.accounts.upstream_controls import Controls
        from parsers.accounts import Account
        home = self.profile("alpha")
        metadata = json.loads((home.parent / "profile.json").read_text())
        metadata["identity"]["email"] = "different@example.test"
        (home.parent / "profile.json").write_text(json.dumps(metadata))
        account = Account("shared-workspace", "alpha@example.test")
        controls = Controls(self.root / "state/upstream_controls")
        lease = controls.acquire("codex")
        lease.pause("fixture", failure={"account_fingerprint": quota_refresh.account_fingerprint("codex", account)})
        lease.close()
        pause = controls.read("codex")["pause"]
        with mock.patch.object(exporter, "ROOT", self.root), mock.patch.object(generation, "self_certified_host_identity", return_value="fixture"):
            status = quota_recovery.local_status()
        self.assertEqual(status["account"]["resolution_reason"], "source_inconsistent")
        self.assertIsNone(status["account"]["account_id"])
        with mock.patch.object(quota_refresh, "load_rate_limits") as run:
            with self.assertRaises(quota_refresh.LocalUnavailable):
                quota_service.recover_codex_quota(account.label, pause["id"], expected_id=account.account_id, root=self.root, profile="alpha")
            run.assert_not_called()
        self.assertEqual(controls.read("codex")["pause"], pause)

    def test_recovery_rejects_another_profile_before_provider_admission(self):
        from agent_monitor.quota import quota_refresh
        from agent_monitor.accounts.upstream_controls import Controls
        from parsers.accounts import Account
        self.profile("alpha"); self.profile("beta")
        controls = Controls(self.root / "state/upstream_controls")
        lease = controls.acquire("codex")
        lease.pause("fixture", failure={"account_profile": "alpha", "account_fingerprint":
            quota_refresh.account_fingerprint("codex", Account("shared-workspace", "alpha@example.test"))})
        lease.close()
        pause_id = controls.read("codex")["pause"]["id"]
        with mock.patch.object(quota_refresh, "load_rate_limits") as run:
            with self.assertRaises(quota_refresh.LocalUnavailable):
                quota_service.recover_codex_quota("beta@example.test", pause_id, expected_id="shared-workspace", profile="beta", root=self.root)
            with self.assertRaises(quota_refresh.LocalUnavailable):
                quota_service.recover_codex_quota("alpha@example.test", "f"*32, expected_id="shared-workspace", profile="alpha", root=self.root)
            run.assert_not_called()
        self.assertEqual(controls.read("codex")["pause"]["id"], pause_id)

    def test_cleared_shared_pause_is_not_republished_for_passive_account(self):
        self.profile("alpha")
        cache = self.root / "state/quota_snapshot.json"
        cache.parent.mkdir()
        cache.write_text(json.dumps({"codex": {"managed_accounts": [{"account_profile": "alpha",
            "account_id": "shared-workspace", "account_label": "alpha@example.test", "seven_day_pct": 12,
            "updated_at": "2026-11-03T00:00:00Z", "query_state": "paused", "refresh_pause": {"id": "old"},
            "refresh_error": "本机已暂停该服务的自动请求", "quota_observation": {"source": "active_query"}}]}}))
        with mock.patch("agent_monitor.quota.quota_refresh.load_rate_limits") as run:
            row = quota_service.managed_codex_rate_limits(root=self.root)[0]
        run.assert_not_called()
        self.assertIsNone(row["refresh_pause"])
        self.assertIsNone(row["refresh_error"])
        self.assertEqual(row["query_state"], "unknown")
        self.assertEqual(row["legacy_pause_outcome"]["refresh_error"], "本机已暂停该服务的自动请求")
        self.assertEqual(row["seven_day_pct"], 12)

    def test_standalone_managed_continuation_does_not_postpone_native(self):
        from agent_monitor.runtime import hub
        from agent_monitor.snapshots import exporter
        self.profile("alpha")
        hub.mark_quota_collected(root=self.root, now=100)
        def collection(*args, **kwargs):
            return {"codex": {"managed_accounts": []}}
        with mock.patch.object(exporter, "ROOT", self.root), \
             mock.patch("agent_monitor.hub_config.quota_interval", return_value=300), \
             mock.patch.object(quota_service, "rate_limits", side_effect=collection):
            with mock.patch.object(hub.time, "time", return_value=200):
                exporter._rate_limits(refresh=True, scheduled=True)
            self.assertEqual(float((self.root / "state/quota-collection-at").read_text()), 100)
            self.assertTrue(hub.native_quota_due(root=self.root, now=400))
            with mock.patch.object(hub.time, "time", return_value=400):
                exporter._rate_limits(refresh=True, scheduled=True)
            self.assertEqual(float((self.root / "state/quota-collection-at").read_text()), 400)

    def test_pause_overlay_retains_account_local_failure_after_resume(self):
        from agent_monitor.accounts.upstream_controls import Controls
        from agent_monitor.quota import quota_refresh
        self.profile("beta")
        with mock.patch.object(quota_refresh, "load_rate_limits", side_effect=quota_refresh.LocalUnavailable("账号已有操作")):
            row = quota_service.managed_codex_rate_limits(refresh=True, root=self.root)[0]
        self.assertEqual(row["query_state"], "local_unavailable")
        controls = Controls(self.root / "state/upstream_controls")
        lease = controls.acquire("codex")
        lease.pause("fixture", failure={"account_profile": "alpha"}); lease.close()
        paused = quota_service.managed_codex_rate_limits(root=self.root)
        quota_service.save_cache(self.root / "state/quota_snapshot.json", {"codex": {"managed_accounts": paused}})
        self.assertEqual(paused[0]["query_state"], "paused")
        controls.resume("codex", controls.read("codex")["pause"]["id"])
        resumed = quota_service.managed_codex_rate_limits(root=self.root)[0]
        self.assertEqual(resumed["query_state"], "local_unavailable")
        self.assertEqual(resumed["refresh_error"], "账号已有操作")
        self.assertIsNone(resumed["refresh_pause"])

    def test_old_collector_receipt_uses_passive_fallback_without_query(self):
        self.session(self.profile("alpha"), "alpha", 10, 21)
        reading = {"observed_at": "2026-11-01T00:00:00Z", "five_hour_used_pct": 44,
                   "five_hour_resets_at": 1900000000, "seven_day_used_pct": 19,
                   "seven_day_resets_at": 1901000000}
        native = {"claude": {"account_id": None}, "codex": {"account_id": "native"}}
        with mock.patch("agent_monitor.quota.quota_refresh.load_rate_limits", return_value=self.active(reading)), \
             mock.patch.object(quota_service, "managed_codex_rate_limits", wraps=quota_service.managed_codex_rate_limits) as reader:
            result = quota_service.gui_rate_limits(
                requester=lambda **kwargs: native,
                request_error=RuntimeError,
                root=self.root,
                managed_refresh=True,
            )
        self.assertEqual(result["codex"]["managed_accounts"][0]["five_hour_pct"], 21)
        self.assertFalse(reader.call_args.kwargs.get("refresh", False))

    def test_gui_consumes_collector_managed_readings_without_another_query(self):
        self.session(self.profile("alpha"), "alpha", 10, 21)
        receipt = {"claude": {}, "codex": {"managed_accounts": [{
            "account_profile": "alpha", "account_id": "shared-workspace",
            "account_label": "alpha@example.test", "seven_day_pct": 37,
            "updated_at": "2026-11-03T00:00:00Z"}]}}
        with mock.patch("agent_monitor.quota.quota_refresh.load_rate_limits") as reader:
            result = quota_service.gui_rate_limits(requester=lambda **kw: receipt,
                request_error=TimeoutError, root=self.root, managed_refresh=True)
        self.assertEqual(reader.call_count, 0)
        self.assertEqual(result["codex"]["managed_accounts"][0]["seven_day_pct"], 37)

    def test_gui_timeout_does_not_replay_managed_query(self):
        self.session(self.profile("alpha"), "alpha", 10, 21)
        with mock.patch("agent_monitor.quota.quota_refresh.load_rate_limits") as reader, \
             mock.patch.object(quota_service.accounts, "codex_account", return_value=None), \
             mock.patch.object(quota_service.accounts, "claude_account", return_value=None):
            result = quota_service.gui_rate_limits(requester=mock.Mock(side_effect=TimeoutError("fixture")),
                request_error=TimeoutError, root=self.root, managed_refresh=True)
        self.assertEqual(reader.call_count, 0)
        self.assertEqual(result["codex"]["managed_accounts"][0]["query_state"], "unknown")

    def test_quota_merge_keeps_managed_updates_independent_of_native_timestamp(self):
        import threading
        from agent_monitor.quota import quota_sync
        manager = object.__new__(quota_sync.Manager)
        manager.condition = threading.Condition()
        old = {"account_id": "native", "account_label": "native@example.test",
               "updated_at": "2099-01-01T00:00:00Z", "seven_day_pct": 3,
               "managed_accounts": [{"account_profile": "alpha", "account_id": "shared-workspace",
                 "account_label": "alpha@example.test", "seven_day_pct": 2,
                 "updated_at": "2026-11-01T00:00:00Z"}]}
        incoming = {**old, "updated_at": "2026-11-03T00:00:00Z", "seven_day_pct": 1,
                    "managed_accounts": [{**old["managed_accounts"][0], "seven_day_pct": 7,
                      "updated_at": "2026-11-03T00:00:00Z"}]}
        manager.records = {"fixture": {"ssh_host": "fixture", "payload": {
            "source_host_identity": "fixture-id", "rate_limits": {"codex": incoming}},
            "error": None, "attempted_at": "2026-11-03T00:00:00Z"}}
        current = SimpleNamespace(host="fixture", meta={"source_host_identity": "fixture-id",
                                  "rate_limits": {"codex": old}})
        result = manager.merge(current, SimpleNamespace(ssh_host="fixture", is_self=False))["codex"]
        self.assertEqual(result["seven_day_pct"], 3)
        self.assertEqual(result["managed_accounts"][0]["seven_day_pct"], 7)

    def test_sync_plan_sources_follow_their_independent_clocks(self):
        import threading
        from agent_monitor.quota import quota_sync
        manager = object.__new__(quota_sync.Manager)
        manager.condition = threading.Condition()
        old = dict(account_id="workspace", account_label="alpha@example.test", reading_plan="plus",
                   credential_plan="pro", account_plan="plus", updated_at="2026-11-01T00:00:00Z")
        incoming = dict(old, reading_plan="prolite", credential_plan=None, updated_at="2026-11-03T00:00:00Z")
        for generated, expected in (("2026-11-02T00:00:00Z", None), ("2026-11-04T00:00:00Z", "pro")):
            manager.records = {"fixture": {"ssh_host": "fixture", "payload": {
                "source_host_identity": "fixture-id", "rate_limits": {"codex": incoming}},
                "error": None, "attempted_at": "2026-11-03T00:00:00Z"}}
            current = SimpleNamespace(host="fixture", meta={"source_host_identity": "fixture-id",
                "generated_at": generated, "rate_limits": {"codex": old}})
            row = manager.merge(current, SimpleNamespace(ssh_host="fixture", is_self=False))["codex"]
            self.assertEqual(row["reading_plan"], "prolite")
            self.assertEqual(row["credential_plan"], expected)
            self.assertEqual(row["account_plan"], "prolite")

    def test_sync_receipt_keeps_plan_with_selected_observation(self):
        import copy
        import threading
        from agent_monitor.quota import quota_sync, quota_view
        old = dict(account_id="workspace", account_label="alpha@example.test",
                   updated_at="2026-11-03T00:00:00Z", reading_plan="prolite",
                   credential_plan=None, account_plan="prolite", five_hour_pct=42)
        for stamp, expected_plan, expected_pct in (
                ("2026-11-01T00:00:00Z", "prolite", 42),
                ("2026-11-04T00:00:00Z", "plus", 7)):
            with self.subTest(stamp=stamp):
                manager = object.__new__(quota_sync.Manager)
                manager.condition = threading.Condition()
                manager.requested = 1
                manager.pending = ()
                manager.errors = {}
                manager.revision = 0
                manager.stopping = False
                manager.on_complete = None
                manager.path = self.root / "receipt.json"
                manager.running = True
                machine = SimpleNamespace(name="fixture", ssh_host="fixture", is_self=False)
                manager.machines = (machine,)
                payload = lambda row: {"source_host_identity": "fixture-id",
                                       "rate_limits": {"codex": copy.deepcopy(row)}}
                manager.records = {"fixture": {"ssh_host": "fixture", "payload": payload(old),
                    "attempted_at": old["updated_at"], "error": None}}
                incoming = dict(old, updated_at=stamp, reading_plan="plus",
                                account_plan="plus", five_hour_pct=7)
                with mock.patch.object(quota_sync, "write_message"), mock.patch.object(
                        quota_sync, "run_queries", side_effect=lambda machines, complete:
                        complete("fixture", payload(incoming), None)):
                    manager._run()
                receipt = manager.records["fixture"]["payload"]["rate_limits"]["codex"]
                self.assertEqual(receipt["reading_plan"], expected_plan)
                self.assertEqual(receipt["account_plan"], expected_plan)
                current = SimpleNamespace(host="fixture", meta={"source_host_identity": "fixture-id",
                    "generated_at": old["updated_at"], "rate_limits": {"codex": old}})
                row = manager.merge(current, machine)["codex"]
                self.assertEqual((quota_view.plan_to_show(row), row["five_hour_pct"]),
                                 (expected_plan, expected_pct))

    def test_active_managed_reading_survives_unrelated_session_link_error(self):
        self.session(self.profile("alpha"), "alpha", 10, 21)
        reading = {"observed_at": "2026-11-01T00:00:00Z", "five_hour_used_pct": 47,
                   "five_hour_resets_at": 1900000000, "seven_day_used_pct": 18,
                   "seven_day_resets_at": 1901000000}
        links = codex_profiles.SessionLinks(errors=({"error": "session link fixture error"},))
        with mock.patch("agent_monitor.quota.quota_refresh.load_rate_limits", return_value=self.active(reading)):
            block = quota_service.managed_codex_rate_limits(links, refresh=True, root=self.root)[0]
        self.assertEqual(block["query_state"], "succeeded")
        self.assertIsNone(block["refresh_error"])

    def test_managed_refresh_honors_shared_blocked_accounts(self):
        self.session(self.profile("alpha"), "alpha", 10, 21)
        cache = self.root / "state/quota_snapshot.json"
        cache.parent.mkdir()
        cache.write_text(json.dumps({"codex": {"managed_accounts": [{
            "account_profile": "alpha", "account_id": "shared-workspace", "account_label": "alpha@example.test",
            "updated_at": "2099-01-01T00:00:00Z", "refresh_pause": {"id": "old-pause", "at": "2099-01-01T00:00:00Z"},
            "five_hour_pct": 4
        }]}}))
        with mock.patch("agent_monitor.quota.quota_refresh.load_rate_limits",
                        side_effect=AssertionError("blocked profile must not query")):
            block = quota_service.managed_codex_rate_limits(
                refresh=True, root=self.root, blocked_accounts={"codex": ["*"]}
            )[0]
        self.assertEqual(block["query_state"], "blocked")
        self.assertIn("未再次查询", block["refresh_error"])
        self.assertIsNone(block["refresh_pause"])

    def test_hub_gui_refresh_honors_shared_blocked_accounts(self):
        self.session(self.profile("alpha"), "alpha", 10, 21)
        with mock.patch("agent_monitor.quota.quota_refresh.load_rate_limits",
                        side_effect=AssertionError("blocked profile must not query")):
            result = quota_service.gui_rate_limits(
                requester=lambda **kwargs: {"claude": {}, "codex": {}},
                request_error=RuntimeError,
                root=self.root,
                managed_refresh=True,
                blocked_accounts={"codex": ["*"]},
            )
        block = result["codex"]["managed_accounts"][0]
        self.assertEqual(block["query_state"], "blocked")

    def test_managed_cache_does_not_cross_profile_identity_switch(self):
        home = self.profile("alpha")
        (home.parent / "profile.json").write_text(json.dumps({"schema_version": 1, "name": "alpha",
            "identity": {"account_id": "new-workspace", "email": "new@example.test", "subject": "alpha"}}))
        cache = self.root / "state/quota_snapshot.json"
        cache.parent.mkdir()
        cache.write_text(json.dumps({"codex": {"managed_accounts": [{
            "account_profile": "alpha", "account_id": "old-workspace", "account_label": "old@example.test",
            "updated_at": "2099-01-01T00:00:00Z", "five_hour_pct": 99
        }]}}))
        block = quota_service.managed_codex_rate_limits(root=self.root)[0]
        self.assertEqual(block["account_id"], "new-workspace")
        self.assertEqual(block["account_label"], "new@example.test")
        self.assertIsNone(block["five_hour_pct"])

    def test_managed_cache_merge_preserves_nested_reading_when_native_is_older(self):
        path = self.root / "state/quota_snapshot.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"codex": {"account_id": "native", "updated_at": "2099-01-01T00:00:00Z",
            "managed_accounts": [{"account_profile": "alpha", "account_id": "old", "account_label": "old@example.test",
                                  "updated_at": "2026-01-01T00:00:00Z", "five_hour_pct": 1}]}}))
        quota_service.save_cache(path, {"codex": {"account_id": "native", "updated_at": "2026-01-01T00:00:00Z",
            "refresh_error": None,
            "managed_accounts": [{"account_profile": "alpha", "account_id": "old", "account_label": "old@example.test",
                                  "updated_at": "2098-01-01T00:00:00Z", "five_hour_pct": 88}]}})
        stored = quota_service.read_cache(path)["codex"]["managed_accounts"]
        self.assertEqual(stored[0]["five_hour_pct"], 88)

    def test_exporter_includes_profiles_without_refreshing_them(self):
        self.session(self.profile("alpha"), "alpha", 10, 21)
        with mock.patch("agent_monitor.hub_config.enabled", return_value=False), mock.patch.object(exporter, "ROOT", self.root),\
             mock.patch("agent_monitor.quota.quota_service.accounts.codex_account", return_value=None),\
             mock.patch("agent_monitor.quota.quota_service.accounts.claude_account", return_value=None),\
             mock.patch("agent_monitor.quota.quota_service.claude_status.load_rate_limits", return_value=None),\
             mock.patch("agent_monitor.quota.quota_service.quota_refresh.load_rate_limits", side_effect=AssertionError("no active queries")):
            limits = exporter._rate_limits(refresh=False)
        self.assertEqual(set(limits), {"claude", "codex"})
        self.assertEqual(limits["codex"]["managed_accounts"][0]["five_hour_pct"], 21)
        self.assertTrue(limits["codex"]["signed_out"])

    def test_default_and_managed_readings_share_one_machine_membership(self):
        older = {"account_id": "shared", "account_label": "member@example.test",
                 "updated_at": "2026-10-01T00:00:00Z", "five_hour_pct": 21}
        newer = {**older, "account_profile": "work", "five_hour_pct": 35,
                 "updated_at": "2026-10-02T00:00:00Z"}
        for hosts in (("first",), ("first", "second")):
            with self.subTest(hosts=hosts):
                admission = SimpleNamespace(admitted=[SimpleNamespace(host=host, meta={"rate_limits": {
                    "codex": {**older, "managed_accounts": [newer]}}}) for host in hosts])
                before = json.dumps([current.meta for current in admission.admitted])
                with mock.patch("agent_monitor.http_server.hub.enabled", return_value=False),\
                     mock.patch("agent_monitor.quota.quota_view.self_machine_name", return_value="first"):
                    rows = server.QUOTA_VIEW.live_from_admission(admission)["codex"]["accounts"]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["machines"], list(hosts))
                self.assertEqual(rows[0]["five_hour_used_pct"], 35)
                self.assertEqual(rows[0]["reading_from"], "first")
                self.assertEqual(json.dumps([current.meta for current in admission.admitted]), before)

    def test_version_four_snapshot_remains_readable(self):
        self.session(self.default, "default", 3, 5)
        entries, _ = self.collect()
        path = self.root / "v4.db"
        statistics_snapshot.write_snapshot(path, entries)
        with sqlite3.connect(path) as conn:
            meta = json.loads(conn.execute("SELECT payload FROM statistics_meta").fetchone()[0])
            meta["version"] = 4
            conn.execute("UPDATE statistics_meta SET payload=?", (json.dumps(meta),))
            for position, payload in conn.execute("SELECT position,payload FROM statistics_sessions").fetchall():
                row = json.loads(payload)
                row.pop("account", None)
                conn.execute("UPDATE statistics_sessions SET payload=? WHERE position=?", (json.dumps(row), position))
        loaded = statistics_snapshot.read_snapshot(path)
        self.assertEqual(loaded["version"], 4)
        self.assertNotIn("account", loaded["session_metadata"][0])

    def test_ancestry_cannot_link_distinct_accounts(self):
        from agent_monitor.contracts.session import codex_metadata
        root = codex_metadata("root", "cli")
        child = codex_metadata("child", {"subagent": {"thread_spawn": {"parent_thread_id": "root"}}})
        for row, name in ((root, "alpha"), (child, "beta")):
            row["account"] = {"name": name, "email": name + "@example.test", "account_id": "workspace"}
        index = SessionIndex([root, child])
        self.assertEqual(index.root(("codex", "child")), ("codex", "child"))
        self.assertEqual(index.status(("codex", "child")), "conflict")

    def test_invalid_profile_unknown_identity_and_duplicate_session_are_not_rebound(self):
        self.profile("unbound", identity=False)
        bad = self.profile("bad")
        (bad.parent / "profile.json").write_text('{"schema_version": 9}')
        errors = []
        self.assertEqual([p.account for p in codex_profiles.discover(source_errors=errors)], [None])
        self.assertEqual(len(errors), 1)
        self.session(self.default, "copied", 10, 21)
        self.session(self.profile("alpha"), "copied", 10, 21)
        entries, _ = self.collect()
        row = next(r for r in entries.session_metadata if r["session_id"] == "copied")
        self.assertIsNone(row["account"])
        self.assertIsNone(merge_metadata([row], [{**row, "account": {"name": "alpha", "email": "a", "account_id": "a"}}])[0]["account"])

    def test_ui_renders_the_account_identity_and_unknown_boundary(self):
        script = r'''
const fs=require('fs'),assert=require('assert');
global.document={readyState:'loading',addEventListener(){}};
global.window={location:{origin:'http://fixture.invalid'}};
require('./web/app.js')();
const ui=window.AgentMonitor;
assert.equal(ui.codexSessionAccountLabel({name:'alpha',email:'alpha@example.test'}),'账号：alpha · alpha@example.test');
assert.equal(ui.codexSessionAccountLabel(null),'账号归属未记录或冲突');
for (const profile of [undefined, 'work-profile']) {
  assert.equal(ui.quotaAccountName({account_label:'beta@example.test',account_profile:profile}),'beta@example.test');
}
assert.equal(ui.quotaAccountName({account_id:'abcdefghijk',account_profile:'work-profile'}),'账号 abcdefgh');
'''
        result = subprocess.run(["node", "-e", script], cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def activity(self, name, pid=None, **changes):
        from agent_monitor.runtime.process_identity import identity
        pid = os.getpid() if pid is None else pid
        folder = self.registry / name
        directory = folder / 'running-tasks'; directory.mkdir(exist_ok=True)
        value = dict(version=1, profile=name, account=json.loads((folder / 'profile.json').read_text())['identity'],
                     pid=pid, process_identity=identity(pid), started_at='2026-11-10T00:00:00+00:00')
        value.update(changes)
        path = directory / (str(len(list(directory.iterdir()))) + '.json')
        path.write_text(json.dumps(value))
        return path

    def test_running_activity_identity_and_stable_settings_revision(self):
        from parsers import codex_activity
        from agent_monitor.quota import quota_settings
        self.profile('alpha')
        profile = codex_profiles.discover()[0]
        initial = quota_settings.read(self.root)
        self.assertFalse(initial['follow_running'])
        record = self.activity('alpha')
        self.assertEqual(codex_activity.observe(profile)['running_tasks'], 1)
        # A duplicate record is still one process; a reused PID is not live activity.
        self.activity('alpha')
        self.activity('alpha', process_identity={'uid': os.getuid(), 'start': 'reused'})
        self.assertEqual(codex_activity.observe(profile)['running_tasks'], 1)
        current = quota_settings.read(self.root)
        self.assertEqual(initial['revision'], current['revision'])
        self.assertFalse(current['accounts'][0]['effective_enabled'])
        enabled = quota_settings.change(dict(revision=current['revision'], selected=[], follow_running=True), self.root)
        self.assertTrue(enabled['accounts'][0]['effective_enabled'])
        self.assertNotIn('_records', enabled['accounts'][0])
        # Subject changes invalidate activity even when workspace/email stay the same.
        path = self.registry / 'alpha/profile.json'
        changed = json.loads(path.read_text()); changed['identity']['subject'] = 'other'
        path.write_text(json.dumps(changed))
        observed = codex_activity.observe(profile)
        self.assertEqual(observed['running_tasks'], 0)
        self.assertIsNotNone(observed['activity_error'])
        record.write_text('{broken')
        self.assertIsNotNone(codex_activity.observe(profile)['activity_error'])

    def test_follow_running_hourly_multi_account_and_exit_before_dispatch(self):
        from datetime import datetime, timezone
        from agent_monitor.quota import quota_settings
        from agent_monitor.runtime import hub
        from parsers import codex_activity
        self.profile('alpha'); self.profile('beta')
        self.activity('alpha'); self.activity('alpha'); self.activity('beta')
        current = quota_settings.read(self.root)
        quota_settings.change(dict(revision=current['revision'], selected=[], follow_running=True), self.root)
        reading = RateLimits(12, 4102444800, 23, 4102444800,
                            updated_at=datetime.now(timezone.utc).isoformat(), quota_observation={'source': 'active_query'})
        with mock.patch('agent_monitor.hub_config.quota_interval', return_value=3600), \
             mock.patch('agent_monitor.quota.quota_refresh.load_rate_limits', return_value=reading) as query:
            quota_service.managed_codex_rate_limits(refresh=True, scheduled=True, enrollment=True, root=self.root)
            self.assertEqual(query.call_count, 2)
            quota_service.managed_codex_rate_limits(refresh=True, scheduled=True, enrollment=True, root=self.root)
            self.assertEqual(query.call_count, 2)
            cached = quota_service.read_cache(self.root / 'state/quota_snapshot.json')['codex']['managed_accounts']
            last = max(datetime.fromisoformat(row['refresh_attempted_at']).timestamp() for row in cached)
            with mock.patch.object(hub, 'native_quota_due', return_value=False):
                self.assertFalse(hub.quota_due(root=self.root, now=last + 3599))
                self.assertTrue(hub.quota_due(root=self.root, now=last + 3601))
            # Snapshot was live, but both tasks exit before their queries dispatch.
            settings = quota_settings.read(self.root, activity_records=True)
            with mock.patch.object(codex_activity, 'live', return_value=False):
                quota_service.managed_codex_rate_limits(refresh=True, enrollment=True, root=self.root, settings=settings)
            self.assertEqual(query.call_count, 2)
            # Manual selection continues even after the last task exits.
            for path in self.registry.glob('*/running-tasks/*.json'): path.unlink()
            current = quota_settings.read(self.root)
            alpha = next(row['key'] for row in current['accounts'] if row['profile'] == 'alpha')
            quota_settings.change(dict(revision=current['revision'], selected=[alpha], follow_running=True), self.root)
            quota_service.managed_codex_rate_limits(refresh=True, enrollment=True, root=self.root)
            self.assertEqual(query.call_count, 3)
            self.assertEqual(query.call_args.args[1].label, 'alpha@example.test')

    def test_native_identity_reading_deduplicates_followed_profile(self):
        from datetime import datetime, timezone
        from agent_monitor.quota import quota_settings
        from agent_monitor.runtime import hub
        self.profile('alpha'); self.activity('alpha')
        current = quota_settings.read(self.root)
        quota_settings.change(dict(revision=current['revision'], selected=[], follow_running=True), self.root)
        now = datetime.now(timezone.utc).isoformat()
        native = dict(account_id='shared-workspace', account_label='alpha@example.test', five_hour_pct=44,
                      updated_at=now, refresh_attempted_at=now, quota_observation={'source': 'active_query'})
        quota_service.save_cache(self.root / 'state/quota_snapshot.json', {'codex': native})
        with mock.patch('agent_monitor.quota.quota_refresh.load_rate_limits') as query, \
             mock.patch.object(hub, 'native_quota_due', return_value=False):
            rows = quota_service.managed_codex_rate_limits(refresh=True, scheduled=True, enrollment=True, root=self.root)
            self.assertEqual(rows[0]['five_hour_pct'], 44)
            self.assertFalse(hub.quota_due(root=self.root))
        query.assert_not_called()

    def test_http_follow_requires_exact_confirmation_and_revision(self):
        from email.message import Message
        from urllib.parse import urlsplit
        from agent_monitor.quota import quota_settings
        from agent_monitor.network.client_network import ApprovalStore
        self.profile('alpha')
        approvals = ApprovalStore()
        machine = SimpleNamespace(name='fixture', is_self=True)
        def call(payload):
            data = json.dumps(payload).encode()
            headers = Message(); headers['Content-Type'] = 'application/json'; headers['Content-Length'] = str(len(data))
            results = []
            handler = SimpleNamespace(headers=headers, rfile=io.BytesIO(data),
                                      _send_json=lambda value, **kw: results.append((kw.get('status', 200), value)))
            server.Handler._handle_quota_settings(handler, urlsplit('/api/quota-settings'), post=True)
            return results[0]
        with mock.patch.object(quota_settings, 'PROJECT_ROOT', self.root), \
             mock.patch.object(server, 'UPSTREAM_APPROVALS', approvals), \
             mock.patch.object(server.sync, 'load_machine_config', return_value=SimpleNamespace(by_name={'fixture': machine})):
            current = quota_settings.read(self.root)
            payload = dict(machine='fixture', revision=current['revision'], selected=[], follow_running=True)
            self.assertNotEqual(call(payload)[0], 200)
            static_token = approvals.issue('quota-settings', ['fixture'], current['revision'])
            self.assertNotEqual(call(dict(payload, confirmation=static_token))[0], 200)
            self.assertFalse(quota_settings.read(self.root)['follow_running'])
            token = approvals.issue('quota-settings', ['fixture', 'follow-running'], current['revision'])
            status, enabled = call(dict(payload, confirmation=token))
            self.assertEqual(status, 200)
            self.assertTrue(enabled['follow_running'])
            self.assertNotEqual(call(dict(payload, confirmation=token))[0], 200)
            # Disabling uses no upstream confirmation and leaves fixed selection unchanged.
            status, disabled = call(dict(payload, revision=enabled['revision'], follow_running=False))
            self.assertEqual(status, 200); self.assertFalse(disabled['follow_running'])

    def test_settings_disabled_during_batch_stop_undispatched_requests(self):
        from agent_monitor.quota import quota_settings
        from datetime import datetime, timezone
        self.profile('alpha'); self.profile('beta')
        self.activity('alpha'); self.activity('beta')
        reading = RateLimits(5, None, 6, None, updated_at=datetime.now(timezone.utc).isoformat(),
                            quota_observation={'source': 'active_query'})
        for follow in (False, True):
            current = quota_settings.read(self.root)
            quota_settings.change(dict(revision=current['revision'], follow_running=follow,
                selected=[] if follow else [row['key'] for row in current['accounts']]), self.root)
            def query(*args, **kwargs):
                current = quota_settings.read(self.root)
                quota_settings.change(dict(revision=current['revision'], selected=[], follow_running=False), self.root)
                return reading
            with mock.patch('agent_monitor.quota.quota_refresh.load_rate_limits', side_effect=query) as call:
                quota_service.managed_codex_rate_limits(refresh=True, enrollment=True, root=self.root)
                self.assertEqual(call.call_count, 1, 'latest setting must stop the next account')
