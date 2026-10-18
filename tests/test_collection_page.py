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
        with mock.patch.object(server, "_query_admission", return_value=nullcontext(admitted)), \
             mock.patch.object(server, "_sync_status", return_value=status) as sync, \
             mock.patch.object(server, "_live_rate_limits_from_admission", return_value=readings) as quota, \
             mock.patch.object(server, "_maybe_sync_remotes", side_effect=AssertionError("refresh")), \
             mock.patch.object(server.rollup, "query_pivot", side_effect=AssertionError("cost query")):
            self.assertEqual(server.ROUTES["/api/collection"]({}), {"sync": status, "rate_limits": readings})
            sync.assert_called_once_with(admission=admitted)
            quota.assert_called_once_with(admitted, sync_status=status)

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
eval(fs.readFileSync('web/app.js','utf8').replace('window.AgentMonitor = {','window.AgentMonitor = { collectionUrl, currentPageUrl, quotaFilter, updateSyncOverviewLink,'));
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
