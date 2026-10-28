"""Consumer regressions against Codex 0.162.1 and Claude 2.1.296 producer contracts."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from agent_monitor.accounts import claude_oauth
from agent_monitor.accounts import codex_accounts
from parsers import accounts, codex
from tests.test_account_identity import id_token


class UpstreamAccountContracts(unittest.TestCase):
    def test_missing_selected_workspace_is_not_replaced_by_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            path.write_text(json.dumps({"tokens": {"id_token": id_token({
                "https://api.openai.com/auth": {"chatgpt_account_id": "claim"}})}}))
            self.assertIsNone(accounts.codex_account(path))

    def test_claude_metadata_uses_config_directory_and_legacy_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            custom = home / "custom"
            custom.mkdir()
            def metadata(target, identity):
                target.write_text(json.dumps({"oauthAccount": {"accountUuid": identity}}))
            metadata(home / ".claude.json", "default")
            metadata(custom / ".claude.json", "custom")
            with mock.patch.dict(os.environ, {"HOME": directory, "CLAUDE_CONFIG_DIR": str(custom)}):
                self.assertEqual(accounts.claude_account().account_id, "custom")
                metadata(custom / ".config.json", "legacy")
                self.assertEqual(accounts.claude_account().account_id, "legacy")

    def test_claude_store_selection_distinguishes_unset_and_empty_override(self):
        # Lw/j$ in the fixed 2.1.296 producer; empty secure override means default.
        for secure, expected in ((None, "/synthetic/custom"), ("", "/synthetic/home/.claude"),
                                 ("/synthetic/secure", "/synthetic/secure")):
            with self.subTest(secure=secure), mock.patch.dict(os.environ, {
                    "HOME": "/synthetic/home", "USER": "synthetic-user",
                    "CLAUDE_CONFIG_DIR": "/synthetic/custom"}, clear=True):
                if secure is not None:
                    os.environ["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = secure
                location = claude_oauth.credential_store()
                self.assertEqual(location["directory"], expected)
                suffix = "" if secure == "" else "-" + hashlib.sha256(expected.encode()).hexdigest()[:8]
                self.assertEqual(location["service"], "Claude Code-credentials" + suffix)
                self.assertEqual(location["account"], "synthetic-user")

    def test_active_quota_selects_only_codex_bucket(self):
        def bucket(identifier, pct):
            return {"limitId": identifier, "primary": {"windowDurationMins": 10080, "usedPercent": pct}}
        normal, other = bucket("codex", 21), bucket("other", 91)
        for mapping, legacy, expected in (({"codex": normal, "other": other}, other, 21),
                ({"other": other}, other, None), ({}, normal, None),
                ({"codex": other}, normal, None), (None, other, None), (None, normal, 21)):
            with self.subTest(mapping=mapping, legacy=legacy):
                reading = codex_accounts.quota_reading({"accountId": "selected",
                    "rateLimitsByLimitId": mapping, "rateLimits": legacy}, "selected")
                self.assertEqual(reading["seven_day_used_pct"], expected)

    def test_passive_latest_observation_keeps_target_bucket(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            for last_id in ("other", "codex", None, "", []):
                with self.subTest(last_id=last_id):
                    rows = [{"type": "event_msg", "timestamp": "2026-10-14T00:00:00Z", "payload": {
                        "type": "token_count", "rate_limits": {"limit_id": identifier,
                            "secondary": {"window_minutes": 10080, "used_percent": pct}}}}
                        for identifier, pct in (("codex", 21), (last_id, 91))]
                    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
                    for reader in (codex._extract_rate_limits, codex._extract_latest_rate_limits):
                        value = reader(path, {})
                        self.assertEqual(value.seven_day_pct, 91 if last_id in ("codex", None) else 21)

    def test_null_snapshot_identity_still_rejected_without_cli_upgrade_advice(self):
        with self.assertRaises(codex_accounts.ActionError) as error:
            codex_accounts.quota_reading({"accountId": None}, "selected")
        self.assertIn("无法核验", str(error.exception))
        self.assertNotIn("更新 CLI", str(error.exception))


class RestrictedThreadContracts(unittest.TestCase):
    @staticmethod
    def config():
        # Shape comes from ConfigReadResponse/ConfigLayer, not a tool-exposure API.
        return {"config": {"features": dict.fromkeys(codex_accounts.RESTRICTED_FEATURES, False),
            "web_search": "disabled", "mcp_servers": {"local": {"enabled": True}}},
            "layers": [{"name": {"type": "sessionFlags"}, "config": {
                "tools": {"update_plan": {"enabled": False},
                          "experimental_request_user_input": {"enabled": False}}}}]}

    def test_disables_named_mcp_and_keeps_no_environment_contract(self):
        options, profile = codex_accounts.restricted_thread_options(self.config())
        self.assertIsNone(profile)
        self.assertEqual(options["config"]["mcp_servers"], {"local": {"enabled": False}})
        for key in ("environments", "dynamicTools", "selectedCapabilityRoots", "runtimeWorkspaceRoots"):
            self.assertEqual(options[key], [])

    def test_managed_profile_is_preserved_then_actual_sandbox_is_checked(self):
        source = self.config()
        source["config"]["default_permissions"] = "managed-readonly"
        options, profile = codex_accounts.restricted_thread_options(source)
        self.assertEqual(options["permissions"], profile)
        self.assertNotIn("sandbox", options)
        good = {"sandbox": {"type": "readOnly"}, "activePermissionProfile": {"id": profile}}
        codex_accounts.verify_restricted_thread(good, profile)
        for response in ({"sandbox": {"type": "workspaceWrite"}, "activePermissionProfile": {"id": profile}},
                         {"sandbox": {"type": "readOnly"}, "activePermissionProfile": {"id": "other"}}, {}):
            with self.subTest(response=response), self.assertRaises(codex_accounts.ActionError):
                codex_accounts.verify_restricted_thread(response, profile)

    def test_unverifiable_or_reenabled_configuration_is_rejected(self):
        for mutation in (lambda c: c.pop("layers"),
                         lambda c: c["config"]["features"].update(shell_tool=True),
                         lambda c: c["layers"][0]["config"]["tools"]["update_plan"].update(enabled=True),
                         lambda c: c["config"].update(mcp_servers=[]),
                         lambda c: c["layers"].insert(0, {"name": {"type": "mdm"},
                             "config": {"mcp_servers": {"local": {"enabled": True}}}})):
            source = self.config()
            mutation(source)
            with self.subTest(source=source), self.assertRaises(codex_accounts.ActionError):
                codex_accounts.restricted_thread_options(source)
