"""Read-only UI contracts, with isolated synthetic records."""
import dataclasses
from datetime import date, datetime, timezone
from types import SimpleNamespace
from contextlib import nullcontext

import rollup
from parsers import UsageEntry
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import server

ROOT = Path(__file__).resolve().parents[1]

class ScopeTests(unittest.TestCase):
    def test_guide_routes_serve_independent_pages_for_get_and_head(self):
        import io
        chapters = {"sources": "数据来源", "collection": "采集状态", "setup": "首次接入", "questions": "查找证据"}
        for chapter, title in chapters.items():
            for send_body in (True, False):
                with self.subTest(chapter=chapter, send_body=send_body):
                    handler = object.__new__(server.Handler)
                    handler.path = f"/guide/{chapter}?range=7d"
                    handler.headers = {}
                    handler.wfile = io.BytesIO()
                    handler.send_response = mock.Mock()
                    handler.send_header = mock.Mock()
                    handler.end_headers = mock.Mock()
                    handler.send_error = mock.Mock(side_effect=AssertionError("unexpected HTTP error"))
                    handler._handle_request(send_body=send_body)
                    handler.send_response.assert_called_once_with(200)
                    headers = dict(call.args for call in handler.send_header.call_args_list)
                    self.assertEqual(headers["Cache-Control"], "no-store")
                    self.assertGreater(int(headers["Content-Length"]), 0)
                    body = handler.wfile.getvalue().decode()
                    if send_body:
                        self.assertIn(f'<h1 class="page-title">{title}</h1>', body)
                        for other in chapters:
                            self.assertEqual(f'id="{other}"' in body, other == chapter)
                        self.assertNotIn("guide-toc", body)
                    else:
                        self.assertEqual(body, "")

    def test_session_list_negotiates_lossless_compression(self):
        import gzip
        import io
        payload = {"sessions": [{"session_id": "fixture-" + str(i), "tokens": i} for i in range(4000)],
                   "filters": {"machine": ["fixture"]}}
        for accepted, compressed in [("gzip, deflate, br", True), ("gzip;q=0.5", True),
                                     ("gzip;q=0", False), ("identity", False), ("", False)]:
            handler = object.__new__(server.Handler)
            handler.path = "/api/sessions?range=30d&with_filters=1"
            handler.headers = {"Accept-Encoding": accepted}
            handler.wfile = io.BytesIO()
            handler.send_response = mock.Mock()
            handler.send_header = mock.Mock()
            handler.end_headers = mock.Mock()
            with mock.patch.dict(server.ROUTES, {"/api/sessions": lambda query: payload}):
                handler._handle_request()
            headers = dict(call.args for call in handler.send_header.call_args_list)
            data = handler.wfile.getvalue()
            self.assertEqual(headers.get("Content-Encoding") == "gzip", compressed)
            self.assertEqual(headers.get("Vary"), "Accept-Encoding")
            self.assertEqual(int(headers["Content-Length"]), len(data))
            self.assertEqual(json.loads(gzip.decompress(data) if compressed else data), payload)
            if compressed:
                self.assertLess(len(data), len(json.dumps(payload).encode()) / 3)

    def test_network_identity_survives_success_cache_and_failure(self):
        with mock.patch.dict(server._NETWORK_CACHE, data=None, ts=0), \
                mock.patch.object(server.network_policy, "endpoint", return_value="http://agent-monitor:synthetic-test-capability@127.0.0.1:9"), \
                mock.patch("server.socket.gethostname", return_value="fixture-host"), \
                mock.patch("server.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=json.dumps({"verdict":"low"}))
            for query in ({"force":["1"]}, {}):
                self.assertEqual(server.network(query)["checked_host"], "fixture-host")
            run.side_effect = FileNotFoundError()
            server._NETWORK_CACHE["data"] = None
            failed = server.network({"force":["1"]})
            self.assertEqual(failed["checked_host"], "fixture-host")
            self.assertFalse(failed["installed"])

    def test_large_session_detail_negotiates_lossless_compression(self):
        import gzip
        import io
        payload = {"meta": None, "entries": [{"model": "fixture-model", "input_tokens": i} for i in range(3000)]}
        for accepted, compressed in [("gzip, deflate, br", True), ("gzip;q=0.5", True), ("gzip;q=0", False), ("identity", False), ("", False)]:
            handler = object.__new__(server.Handler)
            handler.path = "/api/session/fixture"
            handler.headers = {"Accept-Encoding": accepted}
            handler.wfile = io.BytesIO()
            handler.send_response = mock.Mock()
            handler.send_header = mock.Mock()
            handler.end_headers = mock.Mock()
            with mock.patch("server.hub.enabled", return_value=False), mock.patch("server.session_detail", return_value=payload):
                handler._handle_request()
            headers = dict(call.args for call in handler.send_header.call_args_list)
            data = handler.wfile.getvalue()
            self.assertEqual(headers.get("Content-Encoding") == "gzip", compressed)
            self.assertEqual(int(headers["Content-Length"]), len(data))
            self.assertEqual(json.loads(gzip.decompress(data) if compressed else data), payload)
            if compressed:
                self.assertLess(len(data), len(json.dumps(payload).encode()) / 2)
            self.assertEqual(headers.get("Vary"), "Accept-Encoding")



class QueryTests(unittest.TestCase):
    def test_session_filters_match_events_and_keep_all_detail(self):
        entry = UsageEntry(datetime(2026, 1, 1, 16, tzinfo=timezone.utc), "s1", "m1", "r1", "A", 10, 2, 0, 0, 1., "repo", "codex")
        records = [entry, dataclasses.replace(entry, model="B", message_id="m2", request_id="r2", cost_usd=2., input_tokens=20),
                   dataclasses.replace(entry, session_id="other", timestamp=datetime(2026, 1, 2, 16, tzinfo=timezone.utc), model="B")]
        query = {"range":["custom"], "start":["2026-01-02"], "end":["2026-01-02"], "model":["B"], "with_filters":["1"]}
        with mock.patch("server.hub.enabled", return_value=False), mock.patch("server.load_all_entries", return_value=records):
            result = server.sessions_endpoint(query)
            self.assertEqual(result["filters"]["model"], ["A", "B"])
            self.assertEqual([(r["session_id"], r["cost_usd"], r["tokens"]) for r in result["sessions"]], [("s1", 2., 22)])
            self.assertEqual(len(server.session_detail("s1")["entries"]), 2)
            self.assertEqual(server.sessions_endpoint({**query, "model":["missing"]})["sessions"], [])
            self.assertEqual(len(server.sessions_endpoint({"range":["all"]})), 2)
        admission = SimpleNamespace(admitted=[SimpleNamespace(host="machine-a"), SimpleNamespace(host="machine-b")])
        with mock.patch("server.hub.enabled", return_value=True), mock.patch("server.COLLECTION.request"), mock.patch("server.generation.generation_admission_snapshot", return_value=nullcontext(admission)), mock.patch.object(server._CONVERSATION_CACHE, "read", return_value=None), mock.patch("server.statistics_snapshot.read_admitted_usage", return_value=records):
            result = server.sessions_endpoint({**query, "machine":["machine-b"]})
            self.assertEqual([r["machine"] for r in result["sessions"]], ["machine-b"])
            self.assertEqual(result["filters"]["machine"], ["machine-a", "machine-b"])
        for start, end in [("", ""), ("2026-01-03", "2026-01-02")]:
            with self.assertRaises(ValueError):
                server._session_window({"range":["custom"],"start":[start],"end":[end]}, datetime.now(timezone.utc))

    def test_unknown_cost_filter_matches_entries_and_keeps_full_detail(self):
        entry = UsageEntry(datetime(2026, 1, 2, tzinfo=timezone.utc), "mixed", "a", "a", "M", 10, 2, 0, 0, None, "repo", "codex")
        records = [entry, dataclasses.replace(entry, message_id="b", request_id="b", cost_usd=0., input_tokens=20),
                   dataclasses.replace(entry, session_id="priced", message_id="c", request_id="c", cost_usd=3.)]
        query = {"range": ["custom"], "start": ["2026-01-02"], "end": ["2026-01-02"], "cost_status": ["unknown"], "with_filters": ["1"]}
        with mock.patch("server.hub.enabled", return_value=False), mock.patch("server.load_all_entries", return_value=records):
            result = server.sessions_endpoint(query)
            self.assertEqual([(r["session_id"], r["tokens"], r["cost_usd"]) for r in result["sessions"]], [("mixed", 12, None)])
            self.assertEqual(len(server.session_detail("mixed")["entries"]), 2)
            self.assertEqual(len(server.sessions_endpoint({**query, "cost_status": [""]})["sessions"]), 2)
            self.assertEqual(server.sessions_endpoint({**query, "model": ["missing"]})["sessions"], [])
        current = SimpleNamespace(host="fixture")
        with mock.patch("server.hub.enabled", return_value=True), mock.patch("server.COLLECTION.request"), mock.patch("server.generation.generation_admission_snapshot", return_value=nullcontext(SimpleNamespace(admitted=[current]))), mock.patch.object(server._CONVERSATION_CACHE, "read", return_value=None), mock.patch("server.statistics_snapshot.read_admitted_usage", return_value=records):
            self.assertEqual(server.sessions_endpoint(query)["sessions"][0]["tokens"], 12)
            self.assertEqual(len(server.session_detail("mixed", "fixture")["entries"]), 2)
        with self.assertRaises(ValueError):
            server.sessions_endpoint({"cost_status": ["bad"]})

    def test_session_project_drilldown_uses_source_aliases_without_mutating_entries(self):
        from project_alias import make_alias_proof
        raw = "/Users/fixture/no-remote-project"
        entry = UsageEntry(datetime(2026, 1, 1, tzinfo=timezone.utc), "s1", "m1", "r1", "A", 10, 2, 0, 0, 1., raw, "codex")
        proof = make_alias_proof(source_path=raw, canonical_project="example/repo", observed_at="2025-01-01T00:00:00Z", repository_identity="repo-v1:" + "a" * 64)
        for aliases, canonical in [([], "~/no-remote-project"), ([proof], "~/no-remote-project")]:
            current = SimpleNamespace(host="machine-a", meta={"aliases": aliases})
            admission = SimpleNamespace(admitted=[current])
            with mock.patch("server.hub.enabled", return_value=True), mock.patch("server.COLLECTION.request"), mock.patch("server.generation.generation_admission_snapshot", return_value=nullcontext(admission)), mock.patch.object(server._CONVERSATION_CACHE, "read", return_value=None), mock.patch("server.statistics_snapshot.read_admitted_usage", return_value=[entry]) as read:
                for selected in [raw, canonical, "missing"]:
                    result = server.sessions_endpoint({"range":["all"], "project":[selected], "with_filters":["1"]})
                    self.assertEqual(len(result["sessions"]), 0 if selected == "missing" else 1)
                    self.assertEqual(result["filters"]["project"], [canonical])
                server.sessions_endpoint({"range":["all"], "project":[canonical]})
                self.assertNotIn("project", read.call_args.kwargs["query"])
                self.assertEqual(entry.project, raw)

    def test_equal_complete_calendar_windows_and_unknown_delta(self):
        summary = {"known_cost_usd": 0., "entries":1, "unknown_cost_entries":0}
        now = datetime(2026, 1, 1, 20, tzinfo=timezone.utc) # Jan 2 in the bucket timezone
        with mock.patch("server.rollup.period_summary", return_value=summary) as read:
            result = server._period_comparison("7d", now)
            self.assertEqual([call.args[0] for call in read.call_args_list], [(date(2025,12,26),date(2026,1,1)), (date(2025,12,19),date(2025,12,25))])
            self.assertEqual(result["known_cost_delta_usd"], 0.)
            self.assertIsNone(result["coverage_complete"])
        with mock.patch("server.rollup.period_summary", side_effect=[summary,{**summary,"known_cost_usd":None}]):
            self.assertIsNone(server._period_comparison("30d", now)["known_cost_delta_usd"])
        with mock.patch("server.rollup.period_summary") as read:
            self.assertFalse(server._period_comparison("all", now)["available"])
            read.assert_not_called()

    def test_period_cost_distinguishes_empty_unknown_zero_and_mixed(self):
        base = dict(entry_count=1, cost_known_count=1, cost_usd=0., input_tokens=10,output_tokens=2,cache_read_tokens=0,cache_creation_tokens=0,_totals_source="usage",_usage_schema=1)
        cases = [([],None,0), ([{**base,"cost_known_count":0}],None,1), ([base],0.,0), ([base,{**base,"cost_known_count":0}],0.,1), ([{**base,"cost_usd":3}],3.,0)]
        for rows, cost, unknown in cases:
            with self.subTest(rows=rows), mock.patch("rollup._daily_rows", return_value=rows):
                result = rollup.period_summary((date(2026,1,1),date(2026,1,2)))
                self.assertEqual(result["known_cost_usd"],cost)
                self.assertEqual(result["unknown_cost_entries"],unknown)
                self.assertEqual(result["tokens"], 12*len(rows) if rows else None)

    def test_quota_sort_uses_final_reading_and_keeps_unknown_last(self):
        script = r'''const fs=require('fs'),assert=require('assert');
global.window={location:{search:'',origin:'http://fixture',pathname:'/'}};
global.document={readyState:'loading',addEventListener(){}};
require("./web/app.js")();
const ui=window.AgentMonitor,provider={key:'codex'},now=1000;
const records=[{account_id:'unknown',five_hour_used_pct:null}, {account_id:'zero',five_hour_used_pct:0}, {account_id:'full',five_hour_used_pct:100}, {account_id:'updated',account_label:'a@test',account_state:'known',five_hour_used_pct:5,updated_at:'2026-01-01'}];
ui.updateCodexQuotaReadings([{account_id:'updated',email:'a@test',last_quota:{observed_at:'2026-01-02',five_hour_used_pct:99}}]);
ui.quotaFilter.sort='remaining';
assert.deepEqual(ui.sortQuotaAccounts(provider,records,now).map(r=>r.account_id),['full','updated','zero','unknown']);
ui.quotaFilter.sort='remaining_desc';
assert.deepEqual(ui.sortQuotaAccounts(provider,records,now).map(r=>r.account_id),['zero','updated','full','unknown']);
assert.deepEqual(ui.sortQuotaAccounts(provider,[{account_id:'invalid',five_hour_used_pct:101},...records],now).map(r=>r.account_id),['zero','updated','full','invalid','unknown']);
ui.quotaFilter.sort='reset';
assert.deepEqual(ui.sortQuotaAccounts(provider,[{account_id:'past',five_hour_resets_at:999},{account_id:'later',five_hour_resets_at:1100},{account_id:'soon',five_hour_resets_at:1001}],now).map(r=>r.account_id),['soon','later','past']);
ui.quotaFilter.window='seven_day';ui.quotaFilter.sort='remaining';
assert.deepEqual(ui.sortQuotaAccounts(provider,[{account_id:'a',seven_day_used_pct:2},{account_id:'b',seven_day_used_pct:80}]).map(r=>r.account_id),['b','a']);
ui.quotaFilter.sort='default';assert.deepEqual(ui.sortQuotaAccounts(provider,records),records);
'''
        result = subprocess.run(["node","-e",script],cwd=ROOT,capture_output=True,text=True,timeout=8)
        self.assertEqual(result.returncode,0,result.stderr)
