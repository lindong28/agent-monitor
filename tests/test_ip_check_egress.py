"""出口代理的选路：探测该走哪个地址，以及这个答案从哪来。

这些用例守的是一个具体故障：agent-monitor server 是长驻进程，它在 fork 时拷贝一次
HTTP_PROXY，此后线路再切都与它无关。每条线路的端口固定且互不相同，所以启动时
是腾讯线路、之后切到 GCP，就会让它一直朝一个已经停掉的端口发请求——公网信息
永远查不到，而错误里指的还是那个早已无人监听的端口。
"""

import os
import tempfile
import threading
import unittest
from unittest import mock

import requests

from ip_check import cli


class ResolveRouteTests(unittest.TestCase):
    def test_environment_report_excludes_bridge_capability_and_proxy_secrets(self):
        bridge = 'http://agent-monitor:PRIVATE-CAPABILITY@127.0.0.1:45678'
        with mock.patch.dict(os.environ, {cli.network_policy.INTERNAL: bridge, 'HTTP_PROXY': bridge,
                'HTTPS_PROXY': 'https://private-user:PRIVATE-PASSWORD@proxy.example:443/path?token=PRIVATE-QUERY'}, clear=True):
            report = cli.get_proxy_envs()
        self.assertNotIn('HTTP_PROXY', report)
        self.assertEqual(report, {'HTTPS_PROXY': 'https://proxy.example:443'})
        self.assertNotIn('PRIVATE', str(report))

    def test_policy_does_not_use_personal_proxy_or_claim_one_exit(self):
        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://secret:fixture@127.0.0.1:1", "NO_PROXY": "*"}):
            route = cli.resolve_route()
        self.assertEqual(route["source"], "system")
        self.assertEqual(route["address"], "")
        self.assertEqual(route["probe_url"], "http://ip-api.com/json/")
        self.assertNotIn("secret", str(route))


class EgressSessionTests(unittest.TestCase):
    def test_all_routes_use_product_bridge_and_ignore_ambient_bypass(self):
        endpoint = "http://agent-monitor:fixture-capability@127.0.0.1:45678"
        with mock.patch.object(cli.network_policy, "endpoint", return_value=endpoint), \
                mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": "*"}):
            for old in ({"source": "published", "address": ""}, {"source": "invalid"}, None):
                session = cli._egress_session(old)
                self.assertFalse(session.trust_env)
                self.assertEqual(session.proxies, {"http": endpoint, "https": endpoint})

    def test_ca_bundle_survives_product_policy(self):
        with mock.patch.object(cli.network_policy, "endpoint", return_value="http://agent-monitor:fixture@127.0.0.1:45678"), \
                mock.patch.dict(os.environ, {"REQUESTS_CA_BUNDLE": "/fixture/ca.pem"}):
            self.assertEqual(cli._egress_session().verify, "/fixture/ca.pem")

    def test_stopforumspam_gets_a_larger_read_budget(self):
        """连接仍要尽快失败，但正常的慢响应不该继续卡在 6 秒。"""
        seen = {}

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"success": 1, "ip": {"appears": 0}}

        class Session:
            @staticmethod
            def get(*args, **kwargs):
                timeout = kwargs["timeout"]
                seen["timeout"] = timeout
                if not isinstance(timeout, tuple) or timeout[1] <= 6:
                    raise requests.exceptions.ReadTimeout("read timeout")
                return Response()

        with mock.patch.object(cli, "_egress_session", return_value=Session()):
            lines = cli.get_stopforumspam("203.0.113.9", {"source": "published"})

        self.assertNotIn("查询失败", lines[0])
        self.assertEqual(seen["timeout"], (6, 10))


class OneRouteQerRoundTests(unittest.TestCase):
    """Shared policy metadata and independent optional-probe scheduling."""

    def test_all_probes_share_one_route_and_snapshot_records_it(self):
        seen = []
        routes = iter([
            {"address": "http://127.0.0.1:59520", "source": "published", "reason": "", "path": "/p"},
            # 若代码在中途重新解析，第二次起就会拿到这条——用户此刻切了线路。
            {"address": "http://127.0.0.1:59625", "source": "published", "reason": "", "path": "/p"},
            {"address": "http://127.0.0.1:59625", "source": "published", "reason": "", "path": "/p"},
            {"address": "http://127.0.0.1:59625", "source": "published", "reason": "", "path": "/p"},
        ])

        def fake_resolve():
            return next(routes)

        def fake_session(route=None):
            # route 为 None 意味着调用方没把整轮的选路传进来，会各读各的。
            self.assertIsNotNone(route, "出站请求必须使用整轮共享的 route")
            seen.append(route["address"])
            return requests.Session()

        def fake_public(route=None, on_retry_after=None):
            fake_session(route)
            return {"status": "success", "query": "203.0.113.9", "country": "SG",
                    "proxy": True, "hosting": False, "timezone": "Asia/Singapore"}

        def fake_risk(ip, route=None, on_retry_after=None):
            fake_session(route)
            return "", None

        def fake_spam(ip, route=None, on_retry_after=None):
            fake_session(route)
            return [""]

        with mock.patch.object(cli, "resolve_route", side_effect=fake_resolve), \
                mock.patch.object(cli, "_egress_session", side_effect=fake_session), \
                mock.patch.object(cli, "get_public_info", side_effect=fake_public), \
                mock.patch.object(cli, "get_ip_risk", side_effect=fake_risk), \
                mock.patch.object(cli, "get_stopforumspam", side_effect=fake_spam), \
                mock.patch.object(cli, "get_dns_servers", return_value=[]), \
                mock.patch.object(cli, "get_ipv6", return_value=None):
            out = cli.collect_all()

        self.assertEqual(
            set(seen), {"http://127.0.0.1:59520"},
            "本轮出现了不止一条线路：%r" % (seen,),
        )
        self.assertEqual(out["proxy_effective"]["address"], "http://127.0.0.1:59520")
        self.assertEqual(out["proxy_effective"]["source"], "published")

    def test_risk_and_spam_probes_overlap_after_public_info(self):
        """两个独立的慢查询要相互重叠，不能继续串行挤占 30 秒总预算。"""
        barrier = threading.Barrier(2)

        def fake_risk(ip, route=None, on_retry_after=None):
            barrier.wait(timeout=1)
            return "0/100 low", 0

        def fake_spam(ip, route=None, on_retry_after=None):
            barrier.wait(timeout=1)
            return ["未收录  低风险 ✓"]

        route = {
            "address": "http://127.0.0.1:59520",
            "source": "published",
            "reason": "",
            "path": "/p",
        }
        public = {
            "status": "success",
            "query": "203.0.113.9",
            "country": "SG",
            "proxy": True,
            "hosting": False,
            "timezone": "Asia/Singapore",
        }
        with mock.patch.object(cli, "resolve_route", return_value=route), \
                mock.patch.object(cli, "get_public_info", return_value=public), \
                mock.patch.object(cli, "get_ip_risk", side_effect=fake_risk), \
                mock.patch.object(cli, "get_stopforumspam", side_effect=fake_spam), \
                mock.patch.object(cli, "get_dns_servers", return_value=[]), \
                mock.patch.object(cli, "get_ipv6", return_value=None):
            out = cli.collect_all()

        self.assertEqual(out["risk"]["score"], 0)
        self.assertEqual(out["spam"]["raw_lines"], ["未收录  低风险 ✓"])
        self.assertFalse(any(e["section"] in ("risk", "spam") for e in out["errors"]))

    def test_executor_start_failure_stays_with_the_optional_probes(self):
        route = {
            "address": "http://127.0.0.1:59520",
            "source": "published",
            "reason": "",
            "path": "/p",
        }
        public = {
            "status": "success",
            "query": "203.0.113.9",
            "country": "SG",
            "proxy": True,
            "hosting": False,
            "timezone": "Asia/Singapore",
        }
        with mock.patch.object(cli, "resolve_route", return_value=route), \
                mock.patch.object(cli, "get_public_info", return_value=public), \
                mock.patch.object(cli, "get_dns_servers", return_value=[]), \
                mock.patch.object(cli, "get_ipv6", return_value=None), \
                mock.patch.object(
                    cli.concurrent.futures,
                    "ThreadPoolExecutor",
                    side_effect=RuntimeError("cannot start new thread"),
                ):
            out = cli.collect_all()

        self.assertIsNone(out["risk"])
        self.assertIsNone(out["spam"])
        self.assertEqual(
            {e["section"] for e in out["errors"] if e["section"] in ("risk", "spam")},
            {"risk", "spam"},
        )

    def test_snapshot_carries_the_path_not_just_the_address(self):
        """报告靠 path 告诉读者去哪看。漏掉它时终端看着仍然正确——因为报告的兜底字符串
        恰好等于默认路径——所以只有断言快照本身才守得住：常量一改，capture 全对而报告
        指向错的文件。
        """
        route = {"address": "http://127.0.0.1:59520", "source": "published",
                 "reason": "", "path": "/somewhere/else/current-proxy"}
        with mock.patch.object(cli, "resolve_route", return_value=route), \
                mock.patch.object(cli, "get_public_info", return_value={"status": "fail", "message": "x"}), \
                mock.patch.object(cli, "get_dns_servers", return_value=[]), \
                mock.patch.object(cli, "get_ipv6", return_value=None):
            out = cli.collect_all()
        self.assertEqual(out["proxy_effective"]["path"], "/somewhere/else/current-proxy")

    def test_snapshot_carries_reason_when_route_unusable(self):
        bad = {"address": "", "source": "invalid", "reason": "内容不是可用的代理地址：'gcp'",
               "path": "/tmp/current-proxy"}
        with mock.patch.object(cli, "resolve_route", return_value=bad), \
                mock.patch.object(cli, "get_public_info", return_value={"status": "fail", "message": "boom"}), \
                mock.patch.object(cli, "get_dns_servers", return_value=[]), \
                mock.patch.object(cli, "get_ipv6", return_value=None):
            out = cli.collect_all()
        self.assertEqual(out["proxy_effective"], bad)


if __name__ == "__main__":
    unittest.main()
