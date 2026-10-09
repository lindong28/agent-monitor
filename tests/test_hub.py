import contextlib
import json
import os
import plistlib
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import hub
import machine_config
import server
import sync
import exporter
import generation
from parsers import RateLimits


class HubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.marker = self.root / "hub-machine"
        self.patch = mock.patch.object(hub, "MACHINE_PATH", self.marker)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        server._reset_sync_state_for_tests()

    def test_three_local_identities_select_same_hub_without_copying_self(self):
        self.assertIsNone(hub.origin())
        for name in ("macbook", "macmini", "macstudio"):
            self.marker.write_text(name)
            config = machine_config.load_machine_config(retirement_root=None)
            self.assertEqual(config.self_machine.name, name)
            self.assertEqual(hub.origin(), "http://macstudio:39001")
            self.assertEqual(hub.is_hub(), name == "macstudio")
            self.assertEqual(hub.interval(), 120)
        self.marker.write_text("unknown")
        with self.assertRaises(ValueError):
            machine_config.load_machine_config(retirement_root=None)

    def test_client_collector_does_not_depend_on_hub_or_network_transfer(self):
        self.marker.write_text("macmini")
        (self.root / "state").mkdir()
        (self.root / "state/quota-collection-at").write_text("9999999999")
        with mock.patch.object(hub, "ROOT", self.root), mock.patch("rollup.run", return_value={}) as save, \
                mock.patch("sync.sync_all", side_effect=AssertionError("collector must stay local")), \
                mock.patch("exporter._rate_limits", side_effect=AssertionError("Quota is not due")):
            hub.collect()
        save.assert_called_once_with()

    def test_hub_respects_pausing_aliases_and_retirement(self):
        self.marker.write_text("macstudio")
        path = self.root / "machines.json"
        original = '''{"machines":[
{"name":"macbook","ssh_host":"book-alias","self":true},
{"name":"macmini","ssh_host":"mini-alias","self":false},
{"name":"macstudio","ssh_host":"studio-alias","self":false}
],"retired_names":[]}'''
        path.write_text(original)
        with mock.patch.object(machine_config, "DEFAULT_CONFIG_PATH", path):
            load = lambda: machine_config.load_machine_config(path, retirement_root=None)
            self.assertEqual(load().by_name["macmini"].ssh_host, "mini-alias")
            for name in ("macmini", "macbook"):
                paused = original.replace('{"name":"' + name, '// {"name":"' + name)
                path.write_text(paused)
                self.assertNotIn(name, load().by_name)
                self.assertEqual(load().self_machine.name, "macstudio")
                path.write_text(original)
                self.assertIn(name, load().by_name)
            for name in ("macmini", "macbook"):
                result = generation.retire_machines([name], config_path=path, root=self.root / "generations")
                self.assertNotIn(name, result.by_name)
                self.assertEqual(result.self_machine.name, "macstudio")
            path.write_text(original)
            with self.assertRaisesRegex(machine_config.MachineConfigError, "persistently retired"):
                machine_config.load_machine_config(path, retirement_root=self.root / "generations")
            # An illegal active declaration outside the hub subset still fails.
            cfg = {**hub.configuration(), "machines": ["macstudio"]}
            with mock.patch("hub.configuration", return_value=cfg):
                with self.assertRaisesRegex(machine_config.MachineConfigError, "persistently retired"):
                    machine_config.load_machine_config(path, retirement_root=self.root / "generations")
            path.write_text(original.replace('{"name":"macstudio', '// {"name":"macstudio'))
            with self.assertRaisesRegex(machine_config.MachineConfigError, "exactly one self"):
                load()

    def test_uninstall_removes_only_owned_startup_definition(self):
        dest = self.root / "Library/LaunchAgents" / (hub.WEB_LABEL + ".plist")
        dest.parent.mkdir(parents=True)
        saved = self.root / "state/usage-archive.db"
        saved.parent.mkdir()
        saved.write_bytes(b"retained")
        for code, owner, allowed in ((0, str(hub.ROOT / "hub.py"), True),
                                     (113, "", True),
                                     (0, "/another/checkout/hub.py", False),
                                     (1, "", False)):
            with self.subTest(code=code, owner=owner):
                dest.write_bytes(plistlib.dumps(hub.web_plist(hub.ROOT, "/usr/bin/python3")))
                with mock.patch("hub.Path.home", return_value=self.root), \
                        mock.patch("hub.subprocess.run", return_value=SimpleNamespace(returncode=code, stdout=owner)) as run:
                    if allowed:
                        hub.stop_web(uninstall=True)
                        self.assertFalse(dest.exists())
                        self.assertEqual(run.call_count, 2 if code == 0 else 1)
                    else:
                        with self.assertRaises(ValueError):
                            hub.stop_web(uninstall=True)
                        self.assertTrue(dest.exists())
                        self.assertEqual(run.call_count, 1)
                self.assertEqual(saved.read_bytes(), b"retained")
        dest.write_bytes(plistlib.dumps(hub.web_plist("/foreign", "/usr/bin/python3")))
        with mock.patch("hub.Path.home", return_value=self.root), mock.patch("hub.subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "another checkout"):
                hub.stop_web(uninstall=True)
            run.assert_not_called()

    def test_hub_url_matches_the_served_http_port(self):
        config = hub.configuration()
        path = self.root / "hub.json"
        for url, valid in (("http://macstudio:39001", True),
                           ("http://macstudio:39002", False),
                           ("https://macstudio:39001", False),
                           ("http://macstudio", False)):
            with self.subTest(url=url):
                path.write_text(json.dumps({**config, "url": url}))
                if valid:
                    self.assertEqual(hub.configuration(path)["url"], url)
                else:
                    with self.assertRaises(ValueError):
                        hub.configuration(path)

    def test_offline_source_does_not_serialize_other_machine_exports(self):
        machines = tuple(machine_config.Machine(n, n, n == "macstudio")
                         for n in ("macbook", "macmini", "macstudio"))
        config = machine_config.MachineConfig(machines, frozenset())
        barrier = threading.Barrier(3, timeout=3)

        def pull(machine, **kwargs):
            barrier.wait()
            if machine.name == "macmini":
                raise OSError("offline")
            return machine.name

        with mock.patch("sync.load_machine_config", return_value=config), mock.patch("sync.sync_machine", side_effect=pull):
            result = sync.sync_all(quota_refresh=False)
        self.assertEqual(result["macmini"].error, "offline")
        self.assertEqual(result["macbook"].generation, "macbook")
        self.assertEqual(result["macstudio"].generation, "macstudio")

    def test_refresh_during_round_requires_a_new_round_after_the_click(self):
        admission = SimpleNamespace(records=())
        scheduled = []

        class Thread:
            def __init__(self, target, args, **kwargs):
                scheduled.append((target, args))

            def start(self):
                pass

        with mock.patch("server.generation.generation_admission_snapshot", side_effect=lambda: contextlib.nullcontext(admission)), \
                mock.patch("server.threading.Thread", Thread), \
                mock.patch("server._automatic_sync_due", return_value=True), \
                mock.patch("server.hub.enabled", return_value=True), \
                mock.patch("server.sync_process.sync_all", return_value={}), \
                mock.patch("server._remember_accounts_after_sync_publish"):
            server._maybe_sync_remotes({})
            server._maybe_sync_remotes({"force": ["1"]})
            self.assertEqual(len(scheduled), 1)
            self.assertEqual(server._SYNC_STATE["refresh_requested"], 1)
            self.assertEqual(server._SYNC_STATE["refresh_completed"], 0)
            target, args = scheduled[0]
            target(*args)
            self.assertEqual(len(scheduled), 2)
            self.assertEqual(server._SYNC_STATE["refresh_completed"], 0)
            target, args = scheduled[1]
            target(*args)
            self.assertEqual(server._SYNC_STATE["refresh_completed"], 1)
            self.assertFalse(server._SYNC_STATE["running"])

    def test_service_uses_foreground_process_and_carries_ssh_agent_environment(self):
        plist = hub.web_plist(self.root, "/usr/bin/python3", ssh_auth_sock="/agent/socket", path="/bin:/usr/bin")
        self.assertEqual(plist["ProgramArguments"], ["/usr/bin/python3", str(self.root / "hub.py"), "serve"])
        self.assertTrue(plist["KeepAlive"])
        self.assertEqual(plist["EnvironmentVariables"]["SSH_AUTH_SOCK"], "/agent/socket")
        self.assertNotIn("StartInterval", plist)

    def test_service_persists_independent_gateway_location(self):
        with mock.patch.dict(os.environ, {"LLM_GATEWAY_ROOT": "/opt/custom-gateway"}):
            plist = hub.web_plist(self.root, "/usr/bin/python3")
        self.assertEqual(plist["EnvironmentVariables"]["LLM_GATEWAY_ROOT"], "/opt/custom-gateway")

    def test_service_does_not_pin_apple_login_session_sockets(self):
        for parent in ("/var/run", "/private/var/run", "/tmp", "/private/tmp"):
            for session in ("before-reboot", "after-reboot"):
                with self.subTest(parent=parent, session=session):
                    socket = parent + "/com.apple.launchd." + session + "/Listeners"
                    plist = hub.web_plist(self.root, "/usr/bin/python3", ssh_auth_sock=socket)
                    self.assertNotIn("SSH_AUTH_SOCK", plist["EnvironmentVariables"])
        for socket in ("/Users/test/.1password/agent.sock", "/tmp/custom-agent/Listeners"):
            with self.subTest(custom_socket=socket):
                plist = hub.web_plist(self.root, "/usr/bin/python3", ssh_auth_sock=socket)
                self.assertEqual(plist["EnvironmentVariables"]["SSH_AUTH_SOCK"], socket)

    def test_reinstall_migrates_old_socket_pin_and_then_reuses_loaded_job(self):
        dest = self.root / "Library/LaunchAgents" / (hub.WEB_LABEL + ".plist")
        dest.parent.mkdir(parents=True)
        previous = hub.web_plist(hub.ROOT, hub.sys.executable, path="/bin:/usr/bin")
        previous["EnvironmentVariables"]["SSH_AUTH_SOCK"] = "/var/run/com.apple.launchd.old/Listeners"
        dest.write_bytes(plistlib.dumps(previous))
        loaded = SimpleNamespace(returncode=0, stdout=str(hub.ROOT / "hub.py") + "\n")
        with mock.patch("hub.Path.home", return_value=self.root), \
                mock.patch("hub.is_hub", return_value=True), \
                mock.patch.dict("os.environ", {"PATH": "/bin:/usr/bin", "SSH_AUTH_SOCK": "/var/run/com.apple.launchd.new/Listeners"}), \
                mock.patch("hub.subprocess.run", return_value=loaded) as run:
            hub.install_web()
            self.assertEqual([call.args[0][1] for call in run.call_args_list], ["print", "bootout", "bootstrap"])
            self.assertNotIn("SSH_AUTH_SOCK", plistlib.loads(dest.read_bytes())["EnvironmentVariables"])
            run.reset_mock()
            hub.install_web()
            self.assertEqual([call.args[0][1] for call in run.call_args_list], ["print"])

    def test_bootstrap_retries_only_explicit_failures(self):
        for failures, timeout, expected_calls in ((1, False, 2), (3, False, 3), (1, True, 1)):
            with self.subTest(failures=failures, timeout=timeout):
                dest = self.root / "Library/LaunchAgents" / (hub.WEB_LABEL + ".plist")
                dest.parent.mkdir(parents=True, exist_ok=True)
                previous = hub.web_plist(hub.ROOT, hub.sys.executable)
                previous["old_fixture"] = True
                dest.write_bytes(plistlib.dumps(previous))
                actions = []

                def launchctl(argv, **kwargs):
                    actions.append(argv[1])
                    if argv[1] == "print":
                        return SimpleNamespace(returncode=0, stdout=str(hub.ROOT / "hub.py") + "\n")
                    if argv[1] == "bootstrap" and actions.count("bootstrap") <= failures:
                        self.assertEqual(kwargs["timeout"], 15)
                        if timeout:
                            raise hub.subprocess.TimeoutExpired(argv, 15)
                        raise hub.subprocess.CalledProcessError(5, argv)
                    return SimpleNamespace(returncode=0)

                with mock.patch("hub.Path.home", return_value=self.root), \
                        mock.patch("hub.is_hub", return_value=True), \
                        mock.patch("hub.subprocess.run", side_effect=launchctl), \
                        mock.patch("hub.time.sleep") as sleep:
                    if failures == 3 or timeout:
                        with self.assertRaisesRegex(RuntimeError, "Previous job was unloaded.*availability is unverified"):
                            hub.install_web()
                    else:
                        hub.install_web()
                    self.assertEqual(actions.count("bootstrap"), expected_calls)
                    self.assertEqual(actions.count("bootout"), 1)
                    self.assertEqual(sleep.call_args_list, [mock.call(1)] * (expected_calls - 1))

    def test_stop_checks_loaded_owner_as_well_as_plist(self):
        dest = self.root / "Library/LaunchAgents" / (hub.WEB_LABEL + ".plist")
        dest.parent.mkdir(parents=True)
        dest.write_bytes(plistlib.dumps(hub.web_plist(hub.ROOT, "/usr/bin/python3")))
        for loaded_owner, allowed in ((str(hub.ROOT / "hub.py"), True),
                                      ("/another/checkout/hub.py", False)):
            with self.subTest(owner=loaded_owner), mock.patch("hub.Path.home", return_value=self.root), \
                    mock.patch("hub.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=loaded_owner)) as run:
                if allowed:
                    hub.stop_web()
                    self.assertEqual(run.call_args.args[0][1], "bootout")
                else:
                    with self.assertRaisesRegex(ValueError, "loaded hub job owner"):
                        hub.stop_web()
                    self.assertEqual(run.call_count, 1)

    def test_cached_exports_keep_last_active_refresh_status_with_newer_local_readings(self):
        self.marker.write_text("macmini")
        account = SimpleNamespace(account_id="fixture-account", label="Fixture", plan="pro")
        active = RateLimits(22, 1234, 33, 2345, updated_at="2026-09-09T01:00:00+00:00")
        for timestamp in ("2026-09-09T01:00:00+00:00", "2026-09-09T01:01:00+00:00"):
            for error in (None, "Quota refresh timed out"):
                with self.subTest(timestamp=timestamp, error=error):
                    previous = exporter._rate_limit_block(active, account)
                    previous["refresh_error"] = error
                    local = RateLimits(44, 1234, 55, 2345, updated_at=timestamp)
                    with mock.patch.object(exporter, "ROOT", self.root), \
                            mock.patch("exporter._read_quota_cache", return_value={"claude": previous, "codex": previous}), \
                            mock.patch("exporter.accounts.claude_account", return_value=account), \
                            mock.patch("exporter.accounts.codex_account", return_value=account), \
                            mock.patch("exporter.claude_status.load_rate_limits", return_value=local), \
                            mock.patch("exporter.codex.load_rate_limits", return_value=local), \
                            mock.patch("exporter.quota_refresh.load_rate_limits", side_effect=AssertionError("no active query")):
                        cached = exporter._rate_limits(refresh=False)
                        for provider in ("claude", "codex"):
                            self.assertEqual(cached[provider]["refresh_error"], error)
                            self.assertEqual(cached[provider]["updated_at"], timestamp)
                        account.account_id = "different-account"
                        changed = exporter._rate_limits(refresh=False)
                        for provider in ("claude", "codex"):
                            self.assertNotIn("refresh_error", changed[provider])
                        account.account_id = "fixture-account"

    def test_active_failure_survives_cached_exports_even_with_older_fallback_reading(self):
        self.marker.write_text("macmini")
        account = SimpleNamespace(account_id="fixture-account", label="Fixture", plan="pro")
        fresh = RateLimits(22, 1234, 33, 2345, updated_at="2026-09-09T01:00:00+00:00")
        later = RateLimits(44, 1234, 55, 2345, updated_at="2026-09-09T02:00:00+00:00")
        for timestamp in ("2026-09-09T00:00:00+00:00", "2026-09-09T01:00:00+00:00", "2026-09-09T01:01:00+00:00"):
            fallback = RateLimits(11, 1234, 22, 2345, updated_at=timestamp)
            with self.subTest(fallback_time=timestamp), mock.patch.object(exporter, "ROOT", self.root), \
                    mock.patch("exporter.accounts.claude_account", return_value=account), \
                    mock.patch("exporter.accounts.codex_account", return_value=account), \
                    mock.patch("exporter.claude_status.load_rate_limits", return_value=fallback) as claude_local, \
                    mock.patch("exporter.codex.load_rate_limits", return_value=fallback) as codex_local, \
                    mock.patch("exporter.quota_refresh.load_rate_limits", return_value=fresh) as live, \
                    mock.patch("server._load_account_memory", return_value=("valid", {"version": 1, "accounts": {}})), \
                    mock.patch("server._self_machine_name", return_value="macmini"):
                exporter._rate_limits(refresh=True)
                live.side_effect = exporter.quota_refresh.RefreshError("offline")
                exporter._rate_limits(refresh=True)
                active_calls = live.call_count
                claude_local.return_value = codex_local.return_value = later
                cached = exporter._rate_limits(refresh=False)
                admission = SimpleNamespace(admitted=[SimpleNamespace(host="macmini", meta={"rate_limits": cached})])
                result = server._live_rate_limits_from_admission(admission)
                for provider in ("claude", "codex"):
                    self.assertEqual(result[provider]["refresh_errors"], [{"machine": "macmini", "reason": "offline"}])
                    self.assertEqual(cached[provider]["updated_at"], later.updated_at)
                self.assertEqual(live.call_count, active_calls)

    def test_hourly_quota_reading_survives_to_later_cached_exports_without_requery(self):
        self.marker.write_text("macmini")
        account = SimpleNamespace(account_id="fixture-account", label="Fixture", plan="pro")
        reading = RateLimits(22, 1234, 33, 2345, updated_at="2026-09-09T01:00:00+00:00")
        with mock.patch.object(exporter, "ROOT", self.root), \
                mock.patch("exporter.accounts.claude_account", return_value=account), \
                mock.patch("exporter.accounts.codex_account", return_value=account), \
                mock.patch("exporter.claude_status.load_rate_limits", return_value=None), \
                mock.patch("exporter.codex.load_rate_limits", return_value=None), \
                mock.patch("exporter.quota_refresh.load_rate_limits", return_value=reading) as live:
            active = exporter._rate_limits(refresh=True)
            self.assertEqual(live.call_count, 2)
            cached = exporter._rate_limits(refresh=False)
            self.assertEqual(live.call_count, 2)
            self.assertEqual(cached, active)
            account.account_id = "different-account"
            changed = exporter._rate_limits(refresh=False)
            self.assertIsNone(changed["claude"])
            self.assertIsNone(changed["codex"])


if __name__ == "__main__":
    unittest.main()
