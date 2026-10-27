"""Collection navigation and read-only projection use synthetic observations."""
import io
import json
import subprocess
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

import server

ROOT = Path(__file__).resolve().parents[1]


class CollectionPageTests(unittest.TestCase):
    def test_projection_reuses_admission_without_refresh_or_cost_queries(self):
        admitted = object()
        status = {"coverage": {"admitted": 1, "declared": 2}}
        readings = {"codex": {"accounts": [{"account_state": "unstamped", "five_hour_used_pct": 0}]}}
        with mock.patch.object(server.COLLECTION, "admission", return_value=nullcontext(admitted)), \
             mock.patch.object(server.COLLECTION, "status", return_value=status) as sync, \
             mock.patch.object(server.QUOTA_VIEW, "live_from_admission", return_value=readings) as quota, \
             mock.patch.object(server.COLLECTION, "request", side_effect=AssertionError("refresh")), \
             mock.patch.object(server.rollup, "query_pivot", side_effect=AssertionError("cost query")):
            self.assertEqual(server.ROUTES["/api/collection"]({}), {"sync": status, "rate_limits": readings})
            sync.assert_called_once_with(admission=admitted)
            quota.assert_called_once_with(admitted, sync_status=status)

    def test_cost_diagnostics_counts_records_and_respects_scope(self):
        from datetime import datetime, timezone
        from dataclasses import replace
        from types import SimpleNamespace
        from parsers import UsageEntry
        base = UsageEntry(datetime(2026, 10, 2, 0, tzinfo=timezone.utc), "s", "m", "r",
                          "model", 10, 2, 0, 0, None, "project", "claude-code")
        entries = [base, replace(base, message_id="second", message_count=8),
                   replace(base, message_id="zero", cost_usd=0),
                   replace(base, message_id="out", timestamp=datetime(2026, 10, 3, tzinfo=timezone.utc)),
                   replace(base, message_id="other", model="other")]
        query = {"range": ["custom"], "start": ["2026-10-02"], "end": ["2026-10-02"],
                 "model": ["model"], "project": ["project"], "machine": ["m1"]}
        expected = {"groups": [{"machine": "m1", "agent": "claude-code", "model": "model",
                               "reason": "unrecorded", "entries": 2}]}
        hosts = [SimpleNamespace(host="m1", meta={}), SimpleNamespace(host="m2", meta={})]
        with mock.patch.object(server.hub, "enabled", return_value=True), \
             mock.patch.object(server.generation, "generation_admission_snapshot", return_value=nullcontext(SimpleNamespace(admitted=hosts))), \
             mock.patch.object(server.statistics_snapshot, "read_admitted_usage", return_value=entries) as read, \
             mock.patch.object(server.COLLECTION, "request", side_effect=AssertionError("refresh")):
            self.assertEqual(server.cost_diagnostics_endpoint(query), expected)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(read.call_args.kwargs["query"]["cost_status"], ["unknown"])
        with mock.patch.object(server.hub, "enabled", return_value=False), \
             mock.patch.object(server.sync, "load_machine_config", return_value=SimpleNamespace(self_machine=SimpleNamespace(name="m1"))), \
             mock.patch.object(server, "load_all_entries", return_value=entries) as read:
            self.assertEqual(server.cost_diagnostics_endpoint(query), expected)
            read.assert_called_once_with(read_only=True, diagnostic=True)
            self.assertEqual(server.cost_diagnostics_endpoint({**query, "model": ["absent"]}), {"groups": []})

    def test_cost_diagnostic_links_preserve_filters_and_escape_source_values(self):
        script = r'''
const fs=require('fs'),assert=require('assert');
const nodes=Object.fromEntries(['#cost-diagnostics-status','#cost-diagnostics-body','#cost-diagnostics-table',
 '#incomplete-cost-records','#incomplete-cost-summary','#incomplete-cost-body'].map(id=>[id,{}]));
global.document={readyState:'loading',addEventListener(){},querySelector:s=>nodes[s]||null,querySelectorAll:()=>[]};
global.window={location:{origin:'http://example.test',pathname:'/collection',search:'?range=custom&start=2026-10-01&end=2026-10-02&project=a%26b&project=c',hash:''}};
require("./web/app.js")();
const am=window.AgentMonitor;
assert.deepEqual(am.costDiagnosticsQuery().getAll('project'),['a&b','c']);
am.renderCostDiagnostics({groups:[{machine:'mac',agent:'codex',model:'<model>',reason:'unknown_model',entries:1}]});
assert(nodes['#cost-diagnostics-status'].textContent.includes('1 条'));
assert(!nodes['#cost-diagnostics-body'].innerHTML.includes('<model>'));
const href=nodes['#cost-diagnostics-body'].innerHTML.match(/href="([^"]+)"/)[1].replaceAll('&amp;','&');
const link=new URL(href,window.location.origin);
assert.equal(link.searchParams.get('cost_status'),'unknown');
assert.deepEqual(link.searchParams.getAll('project'),['a&b','c']);
assert.equal(link.searchParams.get('return'),'/collection'+window.location.search+'#cost-diagnostics');
assert(am.costReading(null,{known:0,unknown:1}).includes('无已知金额'));
assert(am.costReading(null,{known:0,unknown:0}).includes('无记录'));
assert(am.costReading(0,{known:1,unknown:0}).includes('$0'));
assert(!am.costReading(1,{known:1,unknown:3}).includes('金额记录'));
const missing={machine:'mac',agent:'claude-code',model:'missing',reason:'usage_incomplete',entries:4};
const price={machine:'mac',agent:'codex',model:'unpriced',reason:'unknown_model',entries:1};
am.renderCostDiagnostics({groups:[missing,price]});
assert(nodes['#cost-diagnostics-status'].textContent.includes('5 条'));
assert(nodes['#cost-diagnostics-body'].innerHTML.includes('unpriced'));
assert(!nodes['#cost-diagnostics-body'].innerHTML.includes('missing'));
assert(nodes['#incomplete-cost-body'].innerHTML.includes('missing'));
assert(nodes['#incomplete-cost-summary'].textContent.includes('4'));
assert.equal(nodes['#incomplete-cost-records'].hidden,false);
nodes['#incomplete-cost-records'].open=true;
am.renderCostDiagnostics({groups:[missing]});
assert.equal(nodes['#cost-diagnostics-table'].hidden,true);
assert.equal(nodes['#incomplete-cost-records'].open,true);
am.renderCostDiagnostics({groups:[price]});
assert.equal(nodes['#incomplete-cost-records'].hidden,true);
assert.equal(nodes['#cost-diagnostics-table'].hidden,false);
am.renderCostDiagnostics({groups:[]});
assert(nodes['#cost-diagnostics-status'].textContent.includes('没有金额未知记录'));
assert.equal(nodes['#cost-diagnostics-table'].hidden,true);
assert.equal(nodes['#incomplete-cost-records'].hidden,true);
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_return_to_cost_diagnostics_preserves_original_source_on_click(self):
        script = r'''
const fs=require('fs'),assert=require('assert'),listeners={};
global.document={readyState:'loading',documentElement:{dataset:{}},querySelector:()=>null,
 querySelectorAll:()=>[],addEventListener:(kind,handler)=>{listeners[kind]=handler;}};
const source='/explore?range=all&model=M';
const collection='/collection?range=all&return='+encodeURIComponent(source)+'#cost-diagnostics';
global.window={location:{origin:'http://example.test',pathname:'/sessions',
 search:'?range=all&model=M&return='+encodeURIComponent(collection),hash:''},addEventListener(){}};
require("./web/app.js")();
window.AgentMonitor.initClientNavigation();
for(const modified of [true,false]) {
 const link={id:'sessions-diagnostics-return',href:collection,hasAttribute:()=>false};
 listeners.click({button:0,ctrlKey:modified,target:{closest:s=>s==='a[href]'?link:null},preventDefault(){}});
 assert.equal(new URL(link.href,window.location.origin).searchParams.get('return'),source);
}
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_collection_static_route_and_initializer(self):
        handler = object.__new__(server.Handler)
        handler.path = "/collection?return=%2Fsessions%3Fsession%3Dfixture"
        handler.headers = {}
        handler.wfile = io.BytesIO()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler._handle_request()
        html = handler.wfile.getvalue().decode()
        handler.send_response.assert_called_with(200)
        self.assertIn("AgentMonitor.initCollection()", html)
        self.assertIn('id="quota-machine-records"', html)
        self.assertNotIn('id="quota-accounts"', html)
        for page in ("index", "explore", "sessions", "guide", "network"):
            self.assertIn('data-nav href="/collection"', (ROOT / "web" / (page + ".html")).read_text())

    def test_source_context_preserves_dates_filters_detail_and_quota_state(self):
        script = r'''
const fs=require('fs'),assert=require('assert');
const nodes={'#quota-past-accounts':{hidden:false},'#codex-account-actions':{open:true}};
const nav={href:'/collection'};
global.document={readyState:'loading',addEventListener(){},querySelector:s=>nodes[s]||null,
 querySelectorAll:s=>s==='.quota-observation-details[open]'?[{dataset:{observationKey:'fixture-account'}}]:s.includes('a[data-nav]')?[nav]:[]};
global.window={location:{origin:'http://example.test',pathname:'/sessions',search:'?range=custom&start=2026-10-01&end=2026-10-12&model=a%2Fb&session=fixture&usage_page=2',hash:'#detail'}};
require("./web/app.js")();
const source=window.location.pathname+window.location.search+window.location.hash;
let target=new URL(window.AgentMonitor.collectionUrl(),window.location.origin);
assert.equal(target.pathname,'/collection');assert.equal(target.searchParams.get('return'),source);
Object.assign(window.location,{pathname:'/',search:'?range=7d',hash:''});
Object.assign(window.AgentMonitor.quotaFilter,{provider:'codex',search:'fixture@example.test',window:'seven_day',sort:'remaining'});
target=new URL(window.AgentMonitor.collectionUrl(),window.location.origin);
const back=new URL(target.searchParams.get('return'),window.location.origin);
assert.equal(back.searchParams.get('quota_search'),'fixture@example.test');
assert.equal(back.searchParams.get('quota_history'),'true');
assert.equal(back.searchParams.get('account_actions'),'true');
assert.deepEqual(back.searchParams.getAll('quota_observation'),['fixture-account']);
window.AgentMonitor.updateSyncOverviewLink();
assert.equal(nav.href,target.pathname+target.search,'native link includes current search and expanded state');
nodes['#quota-past-accounts'].hidden=true;
nodes['#codex-account-actions'].open=false;
window.AgentMonitor.quotaFilter.search='another-account';
window.AgentMonitor.updateSyncOverviewLink();
const updated=new URL(new URL(nav.href,window.location.origin).searchParams.get('return'),window.location.origin);
assert.equal(updated.searchParams.get('quota_search'),'another-account');
assert.equal(updated.searchParams.get('quota_history'),'false');
assert.equal(updated.searchParams.get('account_actions'),'false');
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
