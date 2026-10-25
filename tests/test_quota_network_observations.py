"""Observed values stay distinct from unknown/current-state inference."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ip_check import cli
from parsers import claude_status
import network_report
import quota_refresh
import test_ip_check_json as network_fixtures
import test_quota_refresh as quota_fixtures


class ObservationTests(unittest.TestCase):
    def test_claude_reset_does_not_change_recorded_values_or_unknowns(self):
        for reset in (1, 4102444800, None):
            for used in (0, 73, None):
                with self.subTest(reset=reset, used=used), tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "status.json"
                    path.write_text(json.dumps({
                        "_received_at": "2026-05-19T22:41:23+00:00",
                        "_quota_observed_at": "2026-05-19T22:41:23+00:00",
                        "rate_limits": {
                            "five_hour": {"used_percentage": 41, "resets_at": reset},
                            "seven_day": {"used_percentage": used, "resets_at": reset},
                        },
                    }))
                    result = claude_status.load_rate_limits(path)
                    self.assertEqual((result.five_hour_pct, result.seven_day_pct), (41, used))
                    self.assertEqual((result.five_hour_resets_at, result.seven_day_resets_at), (reset, reset))
                    self.assertEqual(result.updated_at, "2026-05-19T22:41:23+00:00")

    def test_missing_account_identity_does_not_claim_cli_is_old(self):
        # Codex 0.162.1 GetAccountRateLimitsResponse.accountId is nullable.
        for result in ({}, {"accountId": None}):
            with self.subTest(result=result), self.assertRaises(quota_refresh.RefreshError) as caught:
                quota_fixtures.ProviderTests().codex_cli(result)
            self.assertIn("无法核验", str(caught.exception))
            self.assertNotIn("更新", str(caught.exception))
            self.assertNotIsInstance(caught.exception, quota_refresh.NotSignedIn)

    def collect(self, **overrides):
        with mock.patch.object(cli, "resolve_route", return_value={
                "address": None, "source": "unpublished", "reason": "fixture", "path": "fixture"}):
            return network_fixtures.CollectAllTests().collect(**overrides)

    def test_missing_critical_inputs_are_unknown_unless_high_is_observed(self):
        cases = (
            ("public", {"get_public_info": {"status": "fail", "message": "offline"}}),
            ("public", {"get_public_info": {**network_fixtures.PUBLIC_OK, "proxy": None}}),
            ("public", {"get_public_info": {**network_fixtures.PUBLIC_OK, "query": None}}),
            ("dns", {"get_dns_servers": []}),
            ("dns", {"get_dns_servers": ["192.168.1.1"]}),
            ("timezone", {"get_public_info": {**network_fixtures.PUBLIC_OK, "timezone": None}}),
        )
        for missing, overrides in cases:
            with self.subTest(missing=missing, overrides=overrides):
                result = self.collect(**overrides)
                self.assertEqual(result["verdict"], "unknown")
                self.assertIn(missing, result["coverage"]["missing"])
                high = self.collect(**{**overrides, "get_ipv6": "2001:db8::1"})
                self.assertEqual(high["verdict"], "high")
                self.assertIn(missing, high["coverage"]["missing"])
                self.assertIn("高风险", network_report.render(high, "fixture").splitlines()[2])

    def test_missing_risk_only_retains_grade_and_discloses_partial_coverage(self):
        for proxy in (False, True):
            with self.subTest(proxy=proxy):
                result = self.collect(get_public_info={**network_fixtures.PUBLIC_OK, "proxy": proxy, "hosting": True},
                                      get_ip_risk=("查询失败", None))
                self.assertEqual(result["verdict"], "proxy-in-use" if proxy else "low")
                self.assertEqual(result["coverage"]["missing"], ["risk"])

    def test_ipv6_and_dns_do_not_claim_unobserved_facts(self):
        for address in (None, "2001:db8::1"):
            with self.subTest(address=address):
                result = self.collect(get_ipv6=address, get_dns_servers=["192.168.1.1"])
                text = " ".join(item["text"] for item in result["conclusions"])
                rendered = network_report.render(result, "fixture")
                for output in (text, rendered):
                    self.assertNotIn("已关闭", output)
                    self.assertNotIn("真实地址已暴露", output)
                    self.assertNotIn("无国内 DNS", output)
                    self.assertIn("泄漏未核实", output)
                self.assertEqual(result["coverage"]["limitations"], ["ipv6_leak_unverified"])

    def test_direct_cli_observes_the_same_boundary(self):
        for address, public in ((None, {"status": "fail", "message": "offline"}),
                                ("2001:db8::1", network_fixtures.PUBLIC_OK),
                                (None, {**network_fixtures.PUBLIC_OK, "query": None})):
            with self.subTest(address=address), contextlib.ExitStack() as stack:
                for name, default in network_fixtures.DEFAULT_RETURNS.items():
                    value = {"get_ipv6": address, "get_public_info": public}.get(name, default)
                    stack.enter_context(mock.patch.object(cli, name, return_value=value))
                stack.enter_context(mock.patch.object(cli, "resolve_route", return_value={}))
                stack.enter_context(mock.patch.object(cli.sys, "argv", ["ip-check"]))
                output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                cli.main()
                text = output.getvalue()
                self.assertNotIn("已禁用", text)
                self.assertNotIn("暴露真实地址", text)
                self.assertIn("泄漏未核实", text)
                if address is None:
                    self.assertIn("未知", text)
                    self.assertNotIn("Claude 使用低风险", text)

    def test_missing_public_ip_does_not_trigger_reputation_requests(self):
        with mock.patch.object(cli, "_collect_risk_probes") as probes:
            result = self.collect(get_public_info={**network_fixtures.PUBLIC_OK, "query": None, "hosting": True})
        probes.assert_not_called()
        self.assertEqual(result["verdict"], "unknown")
        self.assertIn("public", result["coverage"]["missing"])

    def test_spam_requires_success_and_an_explicit_result(self):
        cases = (
            ({"success": 0, "error": "denied"}, False),
            ({"success": 1}, False),
            ({"success": 1, "ip": {}}, False),
            ({"success": 1, "ip": {"appears": 0}}, True),
            ({"success": 1, "ip": {"appears": 1, "confidence": 80, "frequency": 12}}, True),
            ({"success": 1, "ip": {"appears": 1}}, False),
        )
        for payload, valid in cases:
            with self.subTest(payload=payload):
                session = mock.Mock()
                session.get.return_value.status_code = 200
                session.get.return_value.json.return_value = payload
                with mock.patch.object(cli, "_egress_session", return_value=session):
                    lines = cli.get_stopforumspam("203.0.113.9")
                self.assertEqual("查询失败" not in " ".join(lines), valid)
                if not valid:
                    self.assertNotIn("未收录", " ".join(lines))


if __name__ == "__main__":
    unittest.main()
