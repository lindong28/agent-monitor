"""Network observations must retain their actual scope and refresh provenance."""
import contextlib
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from agent_monitor.views.network import NetworkReport
from ip_check import cli
from tests.test_ip_check_json import DEFAULT_RETURNS, PUBLIC_PROXY_OK

ROOT = Path(__file__).resolve().parents[1]


class NetworkSemanticsTests(unittest.TestCase):
    def collect_with_spam_response(self, response):
        with contextlib.ExitStack() as stack:
            for name, value in DEFAULT_RETURNS.items():
                if name != "get_stopforumspam":
                    stack.enter_context(mock.patch.object(cli, name, return_value=PUBLIC_PROXY_OK if name == "get_public_info" else value))
            session = stack.enter_context(mock.patch.object(cli, "_egress_session"))
            session.return_value.get.return_value = response
            return cli.collect_all()["spam"]

    def test_spam_confidence_is_percent_and_absence_does_not_invent_zero(self):
        for confidence in (0, 84.25, 0.00001):
            with self.subTest(confidence=confidence):
                response = mock.Mock(status_code=200, headers={})
                response.json.return_value = {"success": 1, "ip": {
                    "appears": 1, "confidence": confidence, "frequency": 2, "lastseen": "2026-10-01"}}
                parsed = self.collect_with_spam_response(response)
                self.assertEqual(parsed["score"], confidence)
                self.assertEqual(parsed["frequency"], 2)
                self.assertIsNone(parsed.get("level"))
                self.assertIn("%", parsed["raw_lines"][0])
        response.json.return_value = {"success": 1, "ip": {"appears": 0}}
        parsed = self.collect_with_spam_response(response)
        self.assertIsNone(parsed.get("score"))
        self.assertNotIn("低风险", " ".join(parsed["raw_lines"]))

    def test_spam_special_frequency_keeps_provider_value_without_claiming_reports(self):
        for frequency in (254, 255):
            response = mock.Mock(status_code=200, headers={})
            response.json.return_value = {"success": 1, "ip": {"appears": 1,
                "confidence": 99.5, "frequency": frequency, "lastseen": "2026-10-30"}}
            parsed = self.collect_with_spam_response(response)
            self.assertEqual(parsed["frequency"], frequency)
            self.assertEqual(parsed["last_seen"], "2026-10-30")
            raw = " ".join(parsed["raw_lines"])
            if frequency == 255:
                self.assertIn("可能为黑名单占位", raw)
                self.assertNotIn("举报 255 次", raw)
                self.assertNotIn("最近举报 2026-10-30", raw)
            else:
                self.assertIn("举报 254 次", raw)
                self.assertIn("最近举报 2026-10-30", raw)

    def test_read_identifies_probe_cache_and_extended_cooldown(self):
        for delay, ttl in ((0, 60), (180, 180)):
            with self.subTest(delay=delay):
                now = [100.0]
                run = mock.Mock(return_value=mock.Mock(returncode=0, stdout=json.dumps({
                    "timestamp": "2026-10-30T00:00:00Z", "retry_after_seconds": delay})))
                report = NetworkReport(runner=run, clock=lambda: now[0],
                                       child_environment=lambda env: {}, hostname=lambda: "fixture")
                first = report.read()
                self.assertEqual(first["refresh"], {"reused": False, "retry_in_seconds": ttl})
                now[0] += 10
                second = report.read()
                self.assertEqual(second["refresh"], {"reused": True, "retry_in_seconds": ttl - 10})
                self.assertEqual(first["timestamp"], second["timestamp"])
                self.assertNotIn("refresh", report.report())
                run.assert_called_once()
                now[0] += ttl - 10
                self.assertFalse(report.read()["refresh"]["reused"])
                self.assertEqual(run.call_count, 2)

    def test_dns_address_labels_do_not_invent_upstream_location(self):
        addresses = ["100.100.100.100", "fd7a:115c:a1e0::53", "192.168.1.1", "127.0.0.53", "1.1.1.1", "223.5.5.5"]
        entries = cli._dns_entries(addresses)
        for entry in entries[:2]:
            self.assertIn("Tailscale", entry["label"])
            self.assertIsNone(entry["country"])
        self.assertIn("内网", entries[2]["label"])
        self.assertNotIn("路由器", entries[2]["label"])
        self.assertIn("回环", entries[3]["label"])
        self.assertEqual([e["country"] for e in entries[4:]], ["US", "CN"])
        self.assertEqual([e["country"] for e in cli._dns_entries(["9.9.9.9", "149.112.112.112"])], ["CH", "CH"])

    def test_provider_skip_flags_and_confidence_have_distinct_meanings(self):
        script = r'''
const assert=require('node:assert/strict');
const nodes=Object.fromEntries(['risk','local','timezone','banner','verdict','updated','refresh-status'].map(x=>['#network-'+x,{}]));
const ui=require('./web/network.js')({platform:{window:{},document:{}},qs:s=>nodes[s],escapeHtml:s=>String(s).replaceAll('<','&lt;'),formatDate:String});
const render=data=>{ui.renderRiskNetwork(data);return nodes['#network-risk'].innerHTML};
let html=render({public:{ok:true,ip:'192.0.2.1',proxy:false,hosting:false},risk:null,spam:null});
assert.equal((html.match(/未查询：/g)||[]).length,2);
assert(!html.includes('未取得读数'));
html=render({public:{ok:false},risk:null,spam:null});
assert(html.includes('未取得可用公网 IP'));
html=render({public:{ok:true,ip:'192.0.2.1',proxy:false,hosting:false},risk:{marked_proxy:true,score:0,level:'low'},spam:{score:84,level:'high',frequency:2,raw_lines:['84/100 high 举报 2 次']}});
assert(html.includes('标记为代理</div><div class="value"><span class="status-pill ok">否'));
assert(html.includes('滥用置信度'));
assert(html.includes('84%'));
assert(html.includes('供应商返回文本'));
assert(!html.includes('垃圾评分'));
html=render({public:{ok:true,ip:'192.0.2.1',proxy:false,hosting:true},risk:{score:10,type:'Hosting',display:'10/100 low 已标记为代理'},spam:{score:99.5,frequency:255,last_seen:'2026-10-30'}});
assert(html.includes('已标记为代理')); assert(html.includes('供应商读数'));
assert(html.includes('标记为代理</div><div class="value"><span class="status-pill ok">否'));
assert(html.includes('255 可能是供应商黑名单占位值'));
assert(html.includes('供应商时间')); assert(!html.includes('最近举报</div>'));
assert(!html.includes('举报次数</div>'));
html=render({public:{ok:true,ip:'192.0.2.1',proxy:true,hosting:false},risk:null,spam:{raw_lines:['未收录']},errors:[{section:'risk',message:'fixture timeout'}]});
assert(html.includes('fixture timeout')); assert(html.includes('未收录')); assert(!html.includes('未查询：'));
ui.renderNetworkBanner({verdict:'high',timestamp:'observation',coverage:{missing:['dns']},refresh:{reused:true,retry_in_seconds:50},conclusions:[{level:'bad'}]});
assert(nodes['#network-refresh-status'].textContent.includes('复用'));
assert(nodes['#network-updated'].textContent.includes('观测于'));
ui.renderNetworkBanner({verdict:'low',coverage:{missing:[]},refresh:{reused:false,retry_in_seconds:60}});
assert(nodes['#network-refresh-status'].textContent.includes('新检测'));
ui.renderNetworkBanner({verdict:'unknown',error:'fixture failure'});
assert(!nodes['#network-refresh-status'].textContent.includes('取得新检测结果'));
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
