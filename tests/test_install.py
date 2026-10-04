"""Exercise the real installer with disposable HOME/assets and a launchctl fake."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RollupInstallerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.repo = self.home / "repo"
        self.component = self.repo / "agent-monitor"
        self.component.mkdir(parents=True)
        shutil.copy2(ROOT / "install.sh", self.component / "install.sh")
        shutil.copy2(ROOT / "status.sh", self.component / "status.sh")
        (self.component / "lib").mkdir()
        shutil.copy2(ROOT / "lib/install-output.sh", self.component / "lib/install-output.sh")
        for name in ("agent-monitor", "ip-check"):
            (self.component / name).write_text("#!/bin/sh\nexit 0\n")
        vendor = self.component / "web/vendor"
        fonts = vendor / "fonts"
        fonts.mkdir(parents=True)
        (vendor / "chart.umd.min.js").write_text("fixture")
        (fonts / "LICENSE.txt").write_text("fixture")
        for name in ("ibm-plex-sans-latin-400-normal", "ibm-plex-sans-latin-500-normal",
                     "ibm-plex-sans-latin-600-normal", "ibm-plex-sans-latin-400-italic",
                     "ibm-plex-mono-latin-400-normal"):
            (fonts / (name + ".woff2")).write_bytes(b"wOF2" + b"\0" * 4 + (12).to_bytes(4, "big"))
        venv = self.component / ".venv/bin"
        venv.mkdir(parents=True)
        (venv / "python").write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "-m" ] && [ "$2" = pip ]; then echo pip >> "$HOME/runtime-calls"; exit "${FAIL_PIP:-0}"; fi\n'
            'if [ "$1" = "-c" ]; then exit 0; fi\n'
            + 'exec ' + __import__("shlex").quote(__import__("sys").executable) + ' "$@"\n'
        )
        shutil.copy2(ROOT / "requirements.txt", self.component / "requirements.txt")
        shutil.copy2(ROOT / "hub.py", self.component / "hub.py")
        shutil.copy2(ROOT / "hub.json", self.component / "hub.json")
        (venv / "python").chmod(0o755)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.executable("uname", "#!/bin/sh\necho \"${TEST_OS:-Darwin}\"\n")
        self.executable("launchctl", """#!/usr/bin/env python3
import json, os, pathlib, plistlib, sys
h = pathlib.Path.home(); loaded = h / 'loaded.json'
op = sys.argv[1]
if op == 'print':
    if not loaded.exists(): sys.exit(3)
    print('program = ' + json.loads(loaded.read_text())['ProgramArguments'][0])
    data = json.loads(loaded.read_text())
    if 'StartInterval' in data:
        print('run interval = ' + str(data['StartInterval']) + ' seconds')
elif op in ('bootstrap', 'bootout'):
    with (h / 'calls').open('a') as f: f.write(op + '\\n')
    if os.environ.get('FAIL_' + op.upper()) == '1': sys.exit(7)
    if op == 'bootstrap':
        loaded.write_text(json.dumps(plistlib.loads(pathlib.Path(sys.argv[3]).read_bytes())))
    else:
        loaded.unlink()
else: sys.exit(9)
""")
        self.env = dict(os.environ, HOME=str(self.home), REPO_DIR=str(self.repo),
                        PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        INSTALL_SERVICES="0", INSTALL_LEDGER_FD="3")
        self.plist = self.home / "Library/LaunchAgents/com.agent-monitor.rollup.plist"

    def executable(self, name, content):
        p = self.bin / name
        p.write_text(content)
        p.chmod(0o755)

    def install(self, *args):
        script = 'bash "$REPO_DIR/agent-monitor/install.sh" "$@"'
        r = subprocess.run(["bash", "-c", script + ' 3>"$HOME/ledger"', "fixture", *args],
                           env=self.env, text=True, capture_output=True, timeout=20)
        self.ledger = (self.home / "ledger").read_text()
        return r

    def test_default_installs_and_repeat_reuses_loaded_schedule(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("\tagent-monitor rollup\tinstalled\t", self.ledger)
        data = plistlib.loads(self.plist.read_bytes())
        self.assertEqual(data["StartInterval"], 3600)
        self.assertEqual(data["ProgramArguments"], [str(self.component / "agent-monitor"), "refresh"])
        self.assertTrue(data["RunAtLoad"])
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("\tagent-monitor rollup\tpresent\t", self.ledger)
        self.assertEqual((self.home / "calls").read_text().splitlines(), ["bootstrap"])

    def test_linux_keeps_cli_and_reports_platform_skip(self):
        self.env["TEST_OS"] = "Linux"
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.home / ".local/bin/agent-monitor").is_symlink())
        self.assertIn("\tagent-monitor rollup\tskipped\tPlatform:", self.ledger)
        self.assertFalse(self.plist.exists())
        self.assertFalse((self.home / "calls").exists())


    def test_portable_fresh_repeat_ledger_and_runtime_are_independent(self):
        self.env["TEST_OS"] = "Linux"
        self.assertEqual(self.install().returncode, 0)
        self.assertIn("artifact\tagent-monitor CLI\tinstalled\t", self.ledger)
        self.assertIn("artifact\tip-check CLI\tinstalled\t", self.ledger)
        self.assertIn("artifact\tagent-monitor Python runtime\tupdated\t", self.ledger)
        first_calls = (self.home / "runtime-calls").read_text()
        self.assertEqual(self.install().returncode, 0)
        self.assertIn("artifact\tagent-monitor CLI\tpresent\t", self.ledger)
        self.assertIn("artifact\tip-check CLI\tpresent\t", self.ledger)
        self.assertIn("artifact\tagent-monitor Python runtime\tpresent\t", self.ledger)
        self.assertEqual((self.home / "runtime-calls").read_text(), first_calls)

    def test_staging_installs_runtime_assets_cli_without_touching_services(self):
        result = self.install("--no-services")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.home / ".local/bin/agent-monitor").is_symlink())
        self.assertIn("--no-services; existing services unchanged", self.ledger)
        self.assertFalse(self.plist.exists())
        self.assertFalse((self.home / "calls").exists())

    def test_hub_role_preserves_120_second_local_collector(self):
        result = self.install("hub", "macmini")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = plistlib.loads(self.plist.read_bytes())
        self.assertEqual(data["StartInterval"], 120)
        self.assertEqual(data["ProgramArguments"], [str(self.component / "agent-monitor"), "collect"])
        self.assertEqual((self.component / "state/hub-machine").read_text(), "macmini\n")

    def test_failed_runtime_install_stops_before_cli_and_service_changes(self):
        self.env["FAIL_PIP"] = "9"
        result = self.install("--no-services")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("agent-monitor Python runtime\tfailed", self.ledger)
        self.assertFalse((self.home / ".local/bin/agent-monitor").exists())
        self.assertFalse((self.home / "calls").exists())

    def test_bootstrap_failure_is_failed_and_retry_recovers(self):
        self.env["FAIL_BOOTSTRAP"] = "1"
        r = self.install("rollup-daemon")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("\tagent-monitor rollup\tfailed\t", self.ledger)
        del self.env["FAIL_BOOTSTRAP"]
        r = self.install("rollup-daemon")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("\tagent-monitor rollup\tupdated\t", self.ledger)

    def test_foreign_plist_and_loaded_job_are_not_taken_over(self):
        self.plist.parent.mkdir(parents=True)
        original = plistlib.dumps({"ProgramArguments": ["/other/agent-monitor", "rollup"]})
        self.plist.write_bytes(original)
        for loaded in (False, True):
            if loaded:
                (self.home / "loaded.json").write_text(json.dumps(plistlib.loads(original)))
            r = self.install("rollup-daemon")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("\tagent-monitor rollup\tfailed\t", self.ledger)
            if loaded:
                self.assertIn("loaded job ownership conflict", self.ledger)
            self.assertEqual(self.plist.read_bytes(), original)
            self.assertFalse((self.home / "calls").exists())

    def test_failed_unload_preserves_old_config_then_retry_updates(self):
        self.assertEqual(self.install("rollup-daemon").returncode, 0)
        old = self.plist.read_bytes()
        self.env.update(AGENT_MONITOR_ROLLUP_INTERVAL_SECONDS="7200", FAIL_BOOTOUT="1")
        self.assertNotEqual(self.install("rollup-daemon").returncode, 0)
        self.assertEqual(self.plist.read_bytes(), old)
        del self.env["FAIL_BOOTOUT"]
        self.assertEqual(self.install("rollup-daemon").returncode, 0)
        self.assertEqual(plistlib.loads(self.plist.read_bytes())["StartInterval"], 7200)
        r = subprocess.run(["bash", str(self.component / "status.sh"), "rollup-daemon"],
                           env=self.env, text=True, capture_output=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("interval=7200 seconds", r.stdout)


if __name__ == "__main__":
    unittest.main()
