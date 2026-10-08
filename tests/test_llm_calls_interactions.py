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
global.fetch = url => new Promise(resolve => requests.push({url, finish(payload, ok=true) {resolve({ok,status:404,json:async()=>payload,blob:async()=>new Blob([JSON.stringify(payload)],{type:'application/json'})});}}));
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

    def test_routing_summary_separates_pin_attempts_and_candidate_eligibility(self):
        self.run_js(r'''
const content=node('div');
const attempt=(route_id,attempt_no)=>({route_id,attempt_no,outcome:'timeout',route_selection_source:'fallback',dispatch_boundary:'not_crossed'});
const data=payload('routing',[attempt('r1',1),attempt('r1',2),attempt('r2',3),attempt(null,4),attempt('r2',5)]);
Object.assign(data.request,{route_selection_source:'explicit_pin',requested_route_id:'alias',resolved_route_id:'r1',caller_route_constraint:{allowed_routes:['r1','r2']},
  preselection_candidates:[
    {route_id:'r1',effectively_eligible:true,priority:9,eligibility_reasons:[]},
    {route_id:'r2',effectively_eligible:false,priority:1,eligibility_reasons:['request_incompatible','future_reason']},
    {route_id:'r3',eligibility_reasons:[]},
    {effectively_eligible:null}
  ]});
api.renderDetail(data,content);
const text=content.textContent;
for (const value of ['路由诊断','调用方指定路由','指定路由解析','首次尝试路由','首次尝试','同路由重试','切换路由','路由关系未知','符合资格','不符合资格','未知','有对应尝试','无法确认（路由标识缺失）','与本次请求配置不兼容','request_incompatible','future_reason','不是执行排名','不代表路由当前可用','not_crossed','fallback']) assert(text.includes(value),value);
assert(!text.includes('实际路由'));
// First observed attempt is independent of the pin resolution.
data.request.resolved_route_id='canonical-pin';api.renderDetail(data,content);
const summary=content.children.find(n=>n.className==='llm-routing-summary');
const summaryFields=summary.children.find(n=>n.tag==='dl').children;
assert.equal(summaryFields[7].textContent,'canonical-pin');
assert.equal(summaryFields[9].textContent,'r1');
assert.equal(summaryFields[11].textContent,'r1、r2');
// Policy rejection has no pin and no attempt; empty is distinct from absent snapshots.
const rejected=payload('reject');rejected.request.route_selection_source='policy';
rejected.request.preselection_candidates=[{route_id:'excluded',effectively_eligible:false,eligibility_reasons:['caller_route_not_allowed']}];
api.renderDetail(rejected,content);
for(const value of ['按策略选择','不适用（未指定路由）','无已记录尝试','无对应尝试','不在调用方允许的路由内','此请求没有已记录的服务商尝试']) assert(content.textContent.includes(value),value);
rejected.request.preselection_candidates=[];api.renderDetail(rejected,content);assert(content.textContent.includes('历史候选快照为空'));
delete rejected.request.preselection_candidates;api.renderDetail(rejected,content);assert(content.textContent.includes('未记录候选快照'));
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

    def test_export_does_not_turn_json_parse_failure_into_null_download(self):
        self.run_js(r'''
qs('#llm-export-kind').value='requests';qs('#llm-export-format').value='json';
const raw = new Blob(['{"items":[{"logical_request_id":"preserved"}]}'], {type:'application/json'});
let downloaded, jsonReads=0;
URL.createObjectURL = blob => { downloaded=blob; return 'blob:raw-export'; };
global.fetch = async () => ({ok:true,status:200,
  json:async()=>{jsonReads++;throw Error('JSON parse failed');}, blob:async()=>raw});
await api.exportRecords();
assert.equal(await downloaded.text(), await raw.text(), 'must not download null after HTTP 200 parse failure');
assert.strictEqual(downloaded,raw,'download the response blob without JSON serialization');
assert.equal(jsonReads,0);assert.equal(downloads.length,1);
assert.equal(qs('#llm-export').disabled,false);
''')

    def test_export_http_and_blob_failures_do_not_download_and_restore_button(self):
        self.run_js(r'''
qs('#llm-export-kind').value='requests';qs('#llm-export-format').value='json';
global.fetch=async()=>({ok:false,status:500,json:async()=>({error:{message:'ledger unavailable'}}),blob:async()=>{throw Error('must not consume error as export');}});
await api.exportRecords();
assert.equal(downloads.length,0);assert(qs('#llm-export-status').textContent.includes('ledger unavailable'));
assert.equal(qs('#llm-export').disabled,false);
global.fetch=async()=>({ok:true,status:200,blob:async()=>{throw Error('response body interrupted');}});
await api.exportRecords();
assert.equal(downloads.length,0);assert(qs('#llm-export-status').textContent.includes('response body interrupted'));
assert.equal(qs('#llm-export').disabled,false);
global.fetch=async()=>({ok:false,status:502,json:async()=>{throw Error('not JSON');}});
await api.exportRecords();
assert.equal(downloads.length,0);assert(qs('#llm-export-status').textContent.includes('502'));
assert.equal(qs('#llm-export').disabled,false);
''')

    def test_export_blob_finishing_after_navigation_or_new_export_is_ignored(self):
        self.run_js(r'''
qs('#llm-export-kind').value='requests';qs('#llm-export-format').value='json';
const bodies=[];
global.fetch=async()=>({ok:true,blob:()=>new Promise(resolve=>bodies.push(resolve))});
let pending=api.exportRecords();await Promise.resolve();
generation++;const before=qs('#llm-export-status').textContent;
bodies[0](new Blob(['{"items":[]}']));await pending;
assert.equal(downloads.length,0);assert.equal(qs('#llm-export-status').textContent,before);
const old=api.exportRecords();await Promise.resolve();
const latest=api.exportRecords();await Promise.resolve();
bodies[1](new Blob(['{"items":["old"]}']));await old;
assert.equal(downloads.length,0);assert.equal(qs('#llm-export').disabled,true);
bodies[2](new Blob(['{"items":["new"]}']));await latest;
assert.equal(downloads.length,1);assert.equal(qs('#llm-export').disabled,false);
''')
