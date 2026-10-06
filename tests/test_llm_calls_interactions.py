"""Calls UI interactions against deferred read-only API responses, without a live ledger."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
const fs = require('fs');
const assert = require('assert');
function node(tag) {
  let text = '';
  const n = { tag, children: [], listeners: {}, value: '', open: false, isConnected: true,
    appendChild(c) { this.children.push(c); return c; },
    removeChild(c) { this.children.splice(this.children.indexOf(c), 1); },
    addEventListener(k,f) { this.listeners[k] = f; },
    setAttribute(k,v) { this[k] = v; },
    showModal() { this.open = true; }, close() { this.open = false; },
    focus() { focused = this; }, click() { if (this.tag === 'a') downloads.push(this.download); },
  };
  Object.defineProperty(n,'firstChild',{get() {return n.children[0];}});
  Object.defineProperty(n,'textContent',{get() { return text+n.children.map(c=>c.textContent).join(''); },set(v) {text=String(v);n.children=[];}});
  return n;
}
let focused = null, generation = 0, range = '7d', downloads = [], requests = [];
const nodes = new Map();
const qs = k => { if (!nodes.has(k)) nodes.set(k,node(k)); return nodes.get(k); };
global.window = { location: { origin: 'http://fixture' } };
global.document = { createElement: node, querySelector: qs };
global.AgentMonitor = { qs, pageScope() {const g=generation;return ()=>g===generation;}, getRange:()=>range, integer:String };
global.fetch = url => new Promise(resolve => requests.push({url, finish(payload, ok=true) {resolve({ok,status:404,json:async()=>payload});}}));
URL.createObjectURL = () => 'blob:fixture'; URL.revokeObjectURL = () => {};
eval(fs.readFileSync('web/llm-calls.js','utf8').replace('window.AgentMonitorLLMCalls = { init };',
 'window.AgentMonitorLLMCalls = { init, state, openDetail, closeDetail, renderDetail, exportRecords, formatAmount, bindDiagnostics, renderAttempts };'));
const api = window.AgentMonitorLLMCalls;
function payload(id, attempts=[]) { return {as_of:'2026-10-06T01:00:00Z',sources:[{machine:'b',state:'available',observed_at:'2026-10-06T00:00:00Z'}],request:{logical_request_id:id,canonical_project_id:'p',machine:'b',request_outcome:'local_rejected',request_reject_reason:'no_route',attempts}}; }
'''


class CallsInteractionsTests(unittest.TestCase):
    def run_js(self, body):
        result = subprocess.run(['node', '-e', HARNESS + '\n(async()=>{' + body + '\n})().catch(e=>{console.error(e);process.exitCode=1;});'], cwd=ROOT, capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_detail_identity_late_selection_close_and_page_scope(self):
        self.run_js(r'''
const a=node('button'), b=node('button');
let first=api.openDetail({machine:'a',project:'p',logical_request_id:'same'},a);
let second=api.openDetail({machine:'b',project:'p',logical_request_id:'same'},b);
assert.equal(requests[0].url.searchParams.get('machine'),'a');
assert.equal(requests[1].url.searchParams.get('machine'),'b');
assert.equal(requests[1].url.searchParams.has('range'),false);
requests[1].finish(payload('new')); await second;
requests[0].finish(payload('old')); await first;
assert(qs('#llm-detail-content').textContent.includes('new'));
assert(!qs('#llm-detail-content').textContent.includes('old'));
let third=api.openDetail({attempt_id:'attempt'},a);api.closeDetail();
requests[2].finish(payload('closed'));await third;
assert.equal(qs('#llm-detail').open,false);assert.equal(focused,a);
assert(!qs('#llm-detail-content').textContent.includes('closed'));
let fourth=api.openDetail({attempt_id:'attempt'},b);generation++;
requests[3].finish(payload('stale-page'));await fourth;
assert(!qs('#llm-detail-content').textContent.includes('stale-page'));
''')

    def test_detail_complete_diagnostics_unknown_cost_and_small_amount(self):
        self.run_js(r'''
const content=node('div');
api.renderDetail(payload('id',[
 {attempt_no:1,attempt_timestamp:'2025-01-01T00:00:00Z',outcome:'timeout',error_class:'NetworkTimeout',http_status:504,usage_state:'not_reported',cost_state:'unknown',credential_profile_id:'profile-a',credential_source_kind:'env_assignment_name',credential_source_ref:'KEY_NAME',pricing_state:'unavailable'},
 {attempt_no:2,outcome:'success',usage:{total_tokens:3},usage_state:'reported',cost_state:'exact',cost_value:0.00004,cost_currency:'USD',pricing_source:'fixture-pricing'},
]),content);
for(const value of ['NetworkTimeout','504','KEY_NAME','profile-a','fixture-pricing','未知','未报告','完整尝试链 · 2 次','0.00004']) assert(content.textContent.includes(value),value);
assert(!api.formatAmount(0.00004,'USD').endsWith('0.00'));
assert(!api.formatAmount(1e-9,'USD').endsWith('0.00'));
assert(api.formatAmount(0,'USD').includes('0.00'));
api.bindDiagnostics();let prevented=false;qs('#llm-detail').open=true;
qs('#llm-detail').listeners.cancel({preventDefault(){prevented=true;}});
assert(prevented);assert.equal(qs('#llm-detail').open,false);
''')

    def test_export_all_matches_original_selection_and_navigation(self):
        self.run_js(r'''
api.state.requestCursor='r-cursor';api.state.attemptCursor='a-cursor';
qs('[data-filter="machine"]').value='machine-b';
qs('#llm-export-kind').value='attempts';qs('#llm-export-format').value='json';
const pending=api.exportRecords();const url=requests[0].url;
assert.equal(url.pathname,'/api/llm-calls-export');
for (const key of ['request_cursor','attempt_cursor','page_size']) assert(!url.searchParams.has(key));
assert.equal(url.searchParams.get('kind'),'attempts');assert.equal(url.searchParams.get('machine'),'machine-b');
range='30d';requests[0].finish({items:[1,2,3]});await pending;
assert(downloads[0].includes('attempts_7d_machine-machine-b'));
assert(qs('#llm-export-status').textContent.includes('当前筛选已变化'));
assert.equal(qs('#llm-export').disabled,false);
let second=api.exportRecords();generation++;requests[1].finish({items:[]});await second;
assert.equal(downloads.length,1);
''')

    def test_detail_error_keeps_panel_and_attempt_parent_uses_composite_id(self):
        self.run_js(r'''
const pending=api.openDetail({machine:'b',attempt_id:'missing'},node('button'));
requests[0].finish({error:{message:'Not found'}},false);await pending;
assert(qs('#llm-detail-content').textContent.includes('Not found'));assert(qs('#llm-detail').open);
api.renderAttempts({attempts:{items:[{machine:'b',attempt_id:{machine:'b',id:'child'},parent_request:{machine:'b',canonical_project_id:'p',logical_request_id:'same'},attempt_no:1,outcome:'success',cost_state:'unknown',latency_ms:null}],matching_count:1}});
qs('#llm-attempts-body').children[0].children[2].children[0].listeners.click();
assert.equal(requests[1].url.searchParams.get('attempt_id'),'child');
assert.equal(requests[1].url.searchParams.get('machine'),'b');
assert(!requests[1].url.searchParams.has('project'));
requests[1].finish(payload('same'));
''')
