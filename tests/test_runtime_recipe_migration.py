"""Rebuild restores only originally loaded jobs using executable source paths."""
import copy
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest import mock

from agent_monitor.runtime import connector_service, hub, runtime_rebuild


class RuntimeRecipeMigrationTests(unittest.TestCase):
    def test_old_jobs_restore_and_rollback_without_deleted_entrypoints(self):
        cases = (
            ("com.agent-monitor.hub", "hub.py", "agent_monitor.runtime.hub", ["serve"]),
            ("com.agent-monitor.connector", "client_connector.py", "agent_monitor.network.client_connector",
             ["--server", "http://fixture:39001", "--state-file", "/fixture/status.json"]),
        )
        for label, script, module, arguments in cases:
            with self.subTest(component=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / "Library/LaunchAgents" / (label + ".plist")
                path.parent.mkdir(parents=True)
                original = {"Label": label, "ProgramArguments": [str(root / ".venv/bin/python"), str(root / script), *arguments],
                            "WorkingDirectory": str(root), "KeepAlive": True,
                            "EnvironmentVariables": {"HOME": str(root), "PATH": "/fixture/bin", "SSH_AUTH_SOCK": "/fixture/socket",
                                                     "AGENT_MONITOR_DEPLOYMENT_SIGNATURE": "old-source"}}
                path.write_bytes(plistlib.dumps(original))
                current = copy.deepcopy(original)
                bootstraps = []

                def launch(action, *args):
                    nonlocal current
                    if action == "print":
                        if not args[0].endswith("/" + label) or current is None:
                            return subprocess.CompletedProcess([], 113, "", "")
                        argv = current["ProgramArguments"]
                        output = "program = " + argv[0] + "\narguments = {\n" + "\n".join(argv) + "\n}\n"
                        return subprocess.CompletedProcess([], 0, output, "")
                    if action == "bootout":
                        current = None
                    elif action == "bootstrap":
                        current = plistlib.loads(Path(args[-1]).read_bytes())
                        bootstraps.append(copy.deepcopy(current))
                    else:
                        raise AssertionError(action)
                    return subprocess.CompletedProcess([], 0, "", "")

                with mock.patch.dict(os.environ, {"HOME": str(root)}), \
                        mock.patch.object(runtime_rebuild, "ROOT", root), \
                        mock.patch.object(connector_service, "ROOT", root), \
                        mock.patch.object(runtime_rebuild.sys, "platform", "darwin"), \
                        mock.patch.object(runtime_rebuild, "launch", side_effect=launch), \
                        mock.patch.object(runtime_rebuild.service_runtime, "wait_for_identity") as ready, \
                        mock.patch.object(runtime_rebuild, "no_runtime_users"):
                    jobs = runtime_rebuild.capture_jobs()
                    self.assertEqual(len(jobs), 1)  # Other disabled jobs must remain disabled.
                    manifest = {"root": str(root), "revision": "a" * 40, "jobs": jobs, "local_web": None}
                    self.assertEqual(plistlib.loads(path.read_bytes()), original)
                    self.assertEqual(jobs[0]["definition"], original)
                    self.assertEqual(jobs[0]["restore_definition"]["ProgramArguments"],
                                     [original["ProgramArguments"][0], "-m", module, *arguments])
                    transaction = root / ".runtime/runtime-backups/rebuild-fixture"
                    transaction.mkdir(parents=True)
                    runtime_rebuild.save(transaction, manifest, "prepared")
                    runtime_rebuild.stop_components(manifest)
                    runtime_rebuild.restore_components(manifest)
                    self.assertEqual(len(bootstraps), 1)
                    target = bootstraps[-1]
                    self.assertEqual(target["ProgramArguments"], [original["ProgramArguments"][0], "-m", module, *arguments])
                    self.assertEqual(target["EnvironmentVariables"]["SSH_AUTH_SOCK"], "/fixture/socket")
                    self.assertNotEqual(target["EnvironmentVariables"]["AGENT_MONITOR_DEPLOYMENT_SIGNATURE"], "old-source")
                    ready.assert_called_once()

                    # Rollback of an already-started replacement uses the saved
                    # original ownership and the same prepared restoration recipe.
                    (root / ".venv").mkdir()
                    (root / ".venv/new-runtime").touch()
                    (transaction / "previous-venv").mkdir()
                    (transaction / "previous-venv/original-runtime").touch()
                    import json
                    saved = json.loads((transaction / "transaction.json").read_text())
                    runtime_rebuild.rollback(transaction, saved)
                    self.assertTrue((root / ".venv/original-runtime").exists())
                    self.assertEqual(bootstraps[-1], target)
                    self.assertEqual(len(bootstraps), 2)

                    # A pre-migration transaction can still be resumed by the
                    # public --restore path, without enabling any other job.
                    legacy = copy.deepcopy(saved)
                    legacy["jobs"][0].pop("restore_definition")
                    runtime_rebuild.save(transaction, legacy, "prepared")
                    path.write_bytes(plistlib.dumps(original))
                    current = None
                    with mock.patch.object(runtime_rebuild.deploy, "preflight", return_value="a" * 40):
                        self.assertEqual(runtime_rebuild.recover(transaction.name), 0)
                    self.assertEqual(bootstraps[-1], target)
                    self.assertEqual(len(bootstraps), 3)

                    changed = dict(target, WorkingDirectory="/foreign")
                    path.write_bytes(plistlib.dumps(changed))
                    with self.assertRaisesRegex(ValueError, "definition changed"):
                        runtime_rebuild.start_job(jobs[0])
                    self.assertEqual(len(bootstraps), 3)


if __name__ == "__main__":
    unittest.main()
