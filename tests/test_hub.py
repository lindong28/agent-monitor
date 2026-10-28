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

from agent_monitor.runtime import hub
from agent_monitor.snapshots import machine_config
from agent_monitor import http_server as server
from component_fixtures import reset_collection
from agent_monitor.snapshots import sync
from agent_monitor.quota import quota_service
from agent_monitor.snapshots import generation
from parsers import RateLimits


class HubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.marker = self.root / "hub-machine"
        self.patch = mock.patch.object(hub.hub_config, "MACHINE_PATH", self.marker)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.config_path = self.root / "hub.json"
        self.config_path.write_text(json.dumps({
            "schema_version": 1, "hub": "node-d", "url": "http://node-d:39001",
            "port": 39001, "sync_interval_seconds": 120, "quota_interval_seconds": 3600,
            "machines": ["node-b", "node-c", "node-d"]}))
        config_patch = mock.patch.object(hub.hub_config, "CONFIG_PATH", self.config_path)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        for patch in (
            mock.patch('agent_monitor.runtime.hub.service_runtime.wait_for_identity'),
            mock.patch('agent_monitor.runtime.hub.service_runtime.read_health', side_effect=lambda _url: {
                'source_root': str(hub.ROOT), 'deployment_signature': hub.service_runtime.signature(hub.ROOT)}),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.machines_path = self.root / "machines.json"
        self.machines_path.write_text(json.dumps({"machines": [
            {"name": name, "ssh_host": name, "self": name == "node-b"}
            for name in ("node-b", "node-c", "node-d")], "retired_names": []}))
        reset_collection()

    def test_three_local_identities_select_same_hub_without_copying_self(self):
        self.assertIsNone(hub.origin())
        for name in ("node-b", "node-c", "node-d"):
            self.marker.write_text(name)
            with mock.patch.object(machine_config, "DEFAULT_CONFIG_PATH", self.machines_path):
                config = machine_config.load_machine_config(self.machines_path, retirement_root=None)
            self.assertEqual(config.self_machine.name, name)
            self.assertEqual(hub.origin(), "http://node-d:39001")
            self.assertEqual(hub.is_hub(), name == "node-d")
            self.assertEqual(hub.interval(), 120)
        self.marker.write_text("unknown")
        with mock.patch.object(machine_config, "DEFAULT_CONFIG_PATH", self.machines_path), self.assertRaises(ValueError):
            machine_config.load_machine_config(self.machines_path, retirement_root=None)

    def test_client_collector_does_not_depend_on_hub_or_network_transfer(self):
        self.marker.write_text("node-c")
        (self.root / "state").mkdir()
        (self.root / "state/quota-collection-at").write_text("9999999999")
        with mock.patch.object(hub, "ROOT", self.root), mock.patch("agent_monitor.accounting.rollup.run", return_value={}) as save,\
                mock.patch("agent_monitor.snapshots.sync.sync_all", side_effect=AssertionError("collector must stay local")),\
                mock.patch("agent_monitor.quota.quota_service.rate_limits", side_effect=AssertionError("Quota is not due")):
            hub.collect()
        save.assert_called_once_with()

    def test_hub_respects_pausing_aliases_and_retirement(self):
        self.marker.write_text("node-d")
        path = self.root / "machines.json"
        original = '''{"machines":[
{"name":"node-b","ssh_host":"node-b-alias","self":true},
{"name":"node-c","ssh_host":"node-c-alias","self":false},
{"name":"node-d","ssh_host":"node-d-alias","self":false}
],"retired_names":[]}'''
        path.write_text(original)
        with mock.patch.object(machine_config, "DEFAULT_CONFIG_PATH", path):
            load = lambda: machine_config.load_machine_config(path, retirement_root=None)
            self.assertEqual(load().by_name["node-c"].ssh_host, "node-c-alias")
            for name in ("node-c", "node-b"):
                paused = original.replace('{"name":"' + name, '// {"name":"' + name)
                path.write_text(paused)
                self.assertNotIn(name, load().by_name)
                self.assertEqual(load().self_machine.name, "node-d")
                path.write_text(original)
                self.assertIn(name, load().by_name)
            for name in ("node-c", "node-b"):
                result = generation.retire_machines([name], config_path=path, root=self.root / "generations")
                self.assertNotIn(name, result.by_name)
                self.assertEqual(result.self_machine.name, "node-d")
            path.write_text(original)
            with self.assertRaisesRegex(machine_config.MachineConfigError, "persistently retired"):
                machine_config.load_machine_config(path, retirement_root=self.root / "generations")
            # An illegal active declaration outside the hub subset still fails.
            cfg = {**hub.configuration(), "machines": ["node-d"]}
            with mock.patch("agent_monitor.hub_config.configuration", return_value=cfg):
                with self.assertRaisesRegex(machine_config.MachineConfigError, "persistently retired"):
                    machine_config.load_machine_config(path, retirement_root=self.root / "generations")
            path.write_text(original.replace('{"name":"node-d', '// {"name":"node-d'))
            with self.assertRaisesRegex(machine_config.MachineConfigError, "exactly one self"):
                load()

    def test_uninstall_removes_only_owned_startup_definition(self):
        dest = self.root / "Library/LaunchAgents" / (hub.WEB_LABEL + ".plist")
        dest.parent.mkdir(parents=True)
        saved = self.root / "state/usage-archive.db"
        saved.parent.mkdir()
        saved.write_bytes(b"retained")
        for code, owner, allowed in ((0, "-m\nagent_monitor.runtime.hub", True),
                                     (113, "", True),
                                     (0, "/another/checkout/hub.py", False),
                                     (1, "", False)):
            with self.subTest(code=code, owner=owner):
                dest.write_bytes(plistlib.dumps(hub.web_plist(hub.ROOT, "/usr/bin/python3")))
                with mock.patch("agent_monitor.runtime.hub.Path.home", return_value=self.root),\
                        mock.patch("agent_monitor.runtime.hub.subprocess.run", return_value=SimpleNamespace(returncode=code, stdout='arguments = {\n/usr/bin/python3\n' + owner + '\nserve\n}\n')) as run:
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
        with mock.patch("agent_monitor.runtime.hub.Path.home", return_value=self.root), mock.patch("agent_monitor.runtime.hub.subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "another checkout"):
                hub.stop_web(uninstall=True)
            run.assert_not_called()

    def test_hub_url_matches_the_served_http_port(self):
        config = hub.configuration()
        path = self.root / "hub.json"
        for url, valid in (("http://node-d:39001", True),
                           ("http://node-d:39002", False),
                           ("https://node-d:39001", False),
                           ("http://node-d", False)):
            with self.subTest(url=url):
                path.write_text(json.dumps({**config, "url": url}))
                if valid:
                    self.assertEqual(hub.configuration(path)["url"], url)
                else:
                    with self.assertRaises(ValueError):
                        hub.configuration(path)

    def test_offline_source_does_not_serialize_other_machine_exports(self):
        machines = tuple(machine_config.Machine(n, n, n == "node-d")
                         for n in ("node-b", "node-c", "node-d"))
        config = machine_config.MachineConfig(machines, frozenset())
        barrier = threading.Barrier(3, timeout=3)

        def pull(machine, **kwargs):
            barrier.wait()
            if machine.name == "node-c":
                raise OSError("offline")
            return machine.name

        with mock.patch("agent_monitor.snapshots.sync.load_machine_config", return_value=config), mock.patch("agent_monitor.snapshots.sync.sync_machine", side_effect=pull):
            result = sync.sync_all(quota_refresh=False)
        self.assertEqual(result["node-c"].error, "offline")
        self.assertEqual(result["node-b"].generation, "node-b")
        self.assertEqual(result["node-d"].generation, "node-d")

    def test_refresh_during_round_requires_a_new_round_after_the_click(self):
        admission = SimpleNamespace(records=())
        scheduled = []

        class Thread:
            def __init__(self, target, args, **kwargs):
                scheduled.append((target, args))

            def start(self):
                pass

        with mock.patch("agent_monitor.http_server.generation.generation_admission_snapshot", side_effect=lambda: contextlib.nullcontext(admission)),\
                mock.patch("agent_monitor.collection.threading.Thread", Thread),\
                mock.patch("agent_monitor.http_server.COLLECTION.automatic_due", return_value=True),\
                mock.patch("agent_monitor.http_server.hub.enabled", return_value=True),\
                mock.patch("agent_monitor.collection.sync_process.sync_all", return_value={}),\
                mock.patch("agent_monitor.http_server.COLLECTION.remember_accounts"):
            server.COLLECTION.request({})
            server.COLLECTION.request({"force": ["1"]})
            self.assertEqual(len(scheduled), 1)
            self.assertEqual(server.COLLECTION._state["refresh_requested"], 1)
            self.assertEqual(server.COLLECTION._state["refresh_completed"], 0)
            target, args = scheduled[0]
            target(*args)
            self.assertEqual(len(scheduled), 2)
            self.assertEqual(server.COLLECTION._state["refresh_completed"], 0)
            target, args = scheduled[1]
            target(*args)
            self.assertEqual(server.COLLECTION._state["refresh_completed"], 1)
            self.assertFalse(server.COLLECTION._state["running"])

    def test_service_uses_foreground_process_and_carries_ssh_agent_environment(self):
        plist = hub.web_plist(self.root, "/usr/bin/python3", ssh_auth_sock="/agent/socket", path="/bin:/usr/bin")
        self.assertEqual(plist["ProgramArguments"], ["/usr/bin/python3", "-m", "agent_monitor.runtime.hub", "serve"])
        self.assertTrue(plist["KeepAlive"])
        self.assertEqual(plist["EnvironmentVariables"]["SSH_AUTH_SOCK"], "/agent/socket")
        self.assertNotIn("StartInterval", plist)

    def test_service_does_not_configure_gateway_dependency(self):
        with mock.patch.dict(os.environ, {"LLM_GATEWAY_ROOT": "/opt/custom-gateway"}):
            plist = hub.web_plist(self.root, "/usr/bin/python3")
        self.assertNotIn("LLM_GATEWAY_ROOT", plist["EnvironmentVariables"])

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
        loaded = SimpleNamespace(returncode=0, stdout='arguments = {\n' + hub.sys.executable + '\n' + "-m\nagent_monitor.runtime.hub" + '\nserve\n}\n')
        with mock.patch("agent_monitor.runtime.hub.Path.home", return_value=self.root),\
                mock.patch("agent_monitor.hub_config.is_hub", return_value=True),\
                mock.patch.dict("os.environ", {"PATH": "/bin:/usr/bin", "SSH_AUTH_SOCK": "/var/run/com.apple.launchd.new/Listeners"}),\
                mock.patch("agent_monitor.runtime.hub.subprocess.run", return_value=loaded) as run:
            hub.install_web()
            self.assertEqual([call.args[0][1] for call in run.call_args_list], ["print", "bootout", "bootstrap"])
            self.assertNotIn("SSH_AUTH_SOCK", plistlib.loads(dest.read_bytes())["EnvironmentVariables"])
            run.reset_mock()
            hub.install_web()
            self.assertEqual([call.args[0][1] for call in run.call_args_list], ["print"])

    def test_hub_status_distinguishes_loaded_from_target_http_identity(self):
        args = [hub.sys.executable, '-m', 'agent_monitor.runtime.hub', 'serve']
        for code, health, expected in ((113, {}, 1), (0, {}, 1), (0, {'source_root': str(hub.ROOT), 'deployment_signature': hub.service_runtime.signature(hub.ROOT)}, 0)):
            with self.subTest(code=code, health=bool(health)), mock.patch('agent_monitor.runtime.hub.Path.home', return_value=self.root), mock.patch('agent_monitor.runtime.hub.subprocess.run', return_value=SimpleNamespace(returncode=code, stdout='arguments = {\n' + '\n'.join(args) + '\n}\n')), mock.patch('agent_monitor.runtime.hub.service_runtime.read_health', return_value=health):
                self.assertEqual(hub.status_web(), expected)

    def test_client_transition_removes_only_owned_hub_service(self):
        dest = self.root / 'Library/LaunchAgents' / (hub.WEB_LABEL + '.plist')
        dest.parent.mkdir(parents=True)
        for owner, allowed in ((hub.ROOT, True), (Path('/foreign'), False)):
            with self.subTest(owner=owner):
                previous = hub.web_plist(owner, hub.sys.executable)
                dest.write_bytes(plistlib.dumps(previous))
                loaded = SimpleNamespace(returncode=0, stdout='arguments = {\n' + '\n'.join(previous['ProgramArguments']) + '\n}\n')
                with mock.patch('agent_monitor.runtime.hub.Path.home', return_value=self.root), mock.patch('agent_monitor.hub_config.is_hub', return_value=False), mock.patch('agent_monitor.runtime.hub.subprocess.run', return_value=loaded) as run:
                    if allowed:
                        hub.install_web()
                        self.assertEqual([call.args[0][1] for call in run.call_args_list], ['print', 'bootout'])
                        self.assertFalse(dest.exists())
                    else:
                        with self.assertRaises(ValueError):
                            hub.install_web()
                        self.assertTrue(dest.exists())
                        run.assert_not_called()

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
                        return SimpleNamespace(returncode=0, stdout='arguments = {\n' + hub.sys.executable + '\n' + "-m\nagent_monitor.runtime.hub" + '\nserve\n}\n')
                    if argv[1] == "bootstrap" and actions.count("bootstrap") <= failures:
                        self.assertEqual(kwargs["timeout"], 15)
                        if timeout:
                            raise hub.subprocess.TimeoutExpired(argv, 15)
                        raise hub.subprocess.CalledProcessError(5, argv)
                    return SimpleNamespace(returncode=0)

                with mock.patch("agent_monitor.runtime.hub.Path.home", return_value=self.root),\
                        mock.patch("agent_monitor.hub_config.is_hub", return_value=True),\
                        mock.patch("agent_monitor.runtime.hub.subprocess.run", side_effect=launchctl),\
                        mock.patch("agent_monitor.runtime.hub.time.sleep") as sleep:
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
        for loaded_owner, allowed in (("-m\nagent_monitor.runtime.hub", True),
                                      ("/another/checkout/hub.py", False)):
            with self.subTest(owner=loaded_owner), mock.patch("agent_monitor.runtime.hub.Path.home", return_value=self.root),\
                    mock.patch("agent_monitor.runtime.hub.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout='arguments = {\n/usr/bin/python3\n' + loaded_owner + '\nserve\n}\n')) as run:
                if allowed:
                    hub.stop_web()
                    self.assertEqual(run.call_args.args[0][1], "bootout")
                else:
                    with self.assertRaisesRegex(ValueError, "loaded hub job owner"):
                        hub.stop_web()
                    self.assertEqual(run.call_count, 1)

    def test_cached_exports_keep_last_active_refresh_status_with_newer_local_readings(self):
        self.marker.write_text("node-c")
        account = SimpleNamespace(account_id="fixture-account", label="Fixture", plan="pro")
        active = RateLimits(22, 1234, 33, 2345, updated_at="2026-09-09T01:00:00+00:00")
        for timestamp in ("2026-09-09T01:00:00+00:00", "2026-09-09T01:01:00+00:00"):
            for error in (None, "Quota refresh timed out"):
                with self.subTest(timestamp=timestamp, error=error):
                    previous = quota_service.rate_limit_block(active, account)
                    previous["refresh_error"] = error
                    local = RateLimits(44, 1234, 55, 2345, updated_at=timestamp)
                    with mock.patch.object(quota_service, "ROOT", self.root),\
                            mock.patch("agent_monitor.quota.quota_service.read_cache", return_value={"claude": previous, "codex": previous}),\
                            mock.patch("agent_monitor.quota.quota_service.accounts.claude_account", return_value=account),\
                            mock.patch("agent_monitor.quota.quota_service.accounts.codex_account", return_value=account),\
                            mock.patch("agent_monitor.quota.quota_service.claude_status.load_rate_limits", return_value=local),\
                            mock.patch("agent_monitor.quota.quota_service.codex.load_rate_limits", return_value=local),\
                            mock.patch("agent_monitor.quota.quota_service.quota_refresh.load_rate_limits", side_effect=AssertionError("no active query")):
                        cached = quota_service.rate_limits(refresh=False, use_cache=True)
                        for provider in ("claude", "codex"):
                            self.assertEqual(cached[provider]["refresh_error"], error)
                            self.assertEqual(cached[provider]["updated_at"], timestamp)
                        account.account_id = "different-account"
                        changed = quota_service.rate_limits(refresh=False, use_cache=True)
                        for provider in ("claude", "codex"):
                            self.assertNotIn("refresh_error", changed[provider])
                        account.account_id = "fixture-account"

    def test_active_failure_survives_cached_exports_even_with_older_fallback_reading(self):
        self.marker.write_text("node-c")
        account = SimpleNamespace(account_id="fixture-account", label="Fixture", plan="pro")
        fresh = RateLimits(22, 1234, 33, 2345, updated_at="2026-09-09T01:00:00+00:00")
        later = RateLimits(44, 1234, 55, 2345, updated_at="2026-09-09T02:00:00+00:00")
        for timestamp in ("2026-09-09T00:00:00+00:00", "2026-09-09T01:00:00+00:00", "2026-09-09T01:01:00+00:00"):
            fallback = RateLimits(11, 1234, 22, 2345, updated_at=timestamp)
            with self.subTest(fallback_time=timestamp), mock.patch.object(quota_service, "ROOT", self.root),\
                    mock.patch("agent_monitor.quota.quota_service.accounts.claude_account", return_value=account),\
                    mock.patch("agent_monitor.quota.quota_service.accounts.codex_account", return_value=account),\
                    mock.patch("agent_monitor.quota.quota_service.claude_status.load_rate_limits", return_value=fallback) as claude_local,\
                    mock.patch("agent_monitor.quota.quota_service.codex.load_rate_limits", return_value=fallback) as codex_local,\
                    mock.patch("agent_monitor.quota.quota_service.quota_refresh.load_rate_limits", return_value=fresh) as live,\
                    mock.patch("agent_monitor.http_server.ACCOUNT_MEMORY.read", return_value=("valid", {"version": 1, "accounts": {}})),\
                    mock.patch("agent_monitor.quota.quota_view.self_machine_name", return_value="node-c"):
                quota_service.rate_limits(refresh=True, use_cache=True)
                live.side_effect = quota_service.quota_refresh.RefreshError("offline")
                quota_service.rate_limits(refresh=True, use_cache=True)
                active_calls = live.call_count
                claude_local.return_value = codex_local.return_value = later
                cached = quota_service.rate_limits(refresh=False, use_cache=True)
                admission = SimpleNamespace(admitted=[SimpleNamespace(host="node-c", meta={"rate_limits": cached})])
                result = server.QUOTA_VIEW.live_from_admission(admission)
                for provider in ("claude", "codex"):
                    self.assertEqual(result[provider]["refresh_errors"], [{"machine": "node-c", "reason": "offline"}])
                    self.assertEqual(cached[provider]["updated_at"], later.updated_at)
                self.assertEqual(live.call_count, active_calls)

    def test_hourly_quota_reading_survives_to_later_cached_exports_without_requery(self):
        self.marker.write_text("node-c")
        account = SimpleNamespace(account_id="fixture-account", label="Fixture", plan="pro")
        reading = RateLimits(22, 1234, 33, 2345, updated_at="2026-09-09T01:00:00+00:00")
        with mock.patch.object(quota_service, "ROOT", self.root),\
                mock.patch("agent_monitor.quota.quota_service.accounts.claude_account", return_value=account),\
                mock.patch("agent_monitor.quota.quota_service.accounts.codex_account", return_value=account),\
                mock.patch("agent_monitor.quota.quota_service.claude_status.load_rate_limits", return_value=None),\
                mock.patch("agent_monitor.quota.quota_service.codex.load_rate_limits", return_value=None),\
                mock.patch("agent_monitor.quota.quota_service.quota_refresh.load_rate_limits", return_value=reading) as live:
            active = quota_service.rate_limits(refresh=True, use_cache=True)
            self.assertEqual(live.call_count, 2)
            cached = quota_service.rate_limits(refresh=False, use_cache=True)
            self.assertEqual(live.call_count, 2)
            self.assertEqual(cached, active)
            account.account_id = "different-account"
            changed = quota_service.rate_limits(refresh=False, use_cache=True)
            self.assertIsNone(changed["claude"])
            self.assertIsNone(changed["codex"])


if __name__ == "__main__":
    unittest.main()
