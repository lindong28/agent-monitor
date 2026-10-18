"""Task transitions with isolated DOM and deferred data; no provider access."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ActionTests(unittest.TestCase):
    def test_only_same_successful_operation_collapses_batch_status(self):
        script = r'''
const fs=require('fs'),assert=require('assert');
eval(fs.readFileSync('web/codex-accounts.js','utf8').replace('export function init','function init'));
const op={id:'send-1',batch_id:'batch-1',stage:'succeeded',message_status:'succeeded',refresh_only:false};
assert.equal(sameSuccessfulBatchOperation(op,{...op}),true);
for(const changed of [{id:'query-2',refresh_only:true},{id:'send-2'},{batch_id:'older-batch'},
 {stage:'partial'},{message_status:'unknown'},{id:undefined}]) {
 assert.equal(sameSuccessfulBatchOperation({...op,...changed},op),false);
}
assert.equal(sameSuccessfulBatchOperation(op,null),false);
assert.equal(sameSuccessfulBatchOperation(null,op),false);
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_operation_details_keep_own_observation_apart_from_latest_quota(self):
        script = r'''
const fs=require('fs'),assert=require('assert');
global.document={readyState:'loading',addEventListener(){}};global.window={};
eval(fs.readFileSync('web/app.js','utf8'));
const source=fs.readFileSync('web/codex-accounts.js','utf8');
const declaration=source.split('\n').find(line=>line.includes('const latestReading ='));
const operationReading=new Function('account',declaration+';return latestReading;');
const before={observed_at:'2026-10-18T01:00:00Z',seven_day_used_pct:1};
const after={observed_at:'2026-10-18T02:00:00Z',seven_day_used_pct:2};
for(const last of [before,{observed_at:'2026-10-18T03:00:00Z',seven_day_used_pct:54}]) {
 const account={operation:{before,after},last_quota:last};
 assert.strictEqual(operationReading(account),after);
 assert.strictEqual(window.AgentMonitor.latestCodexQuotaReading(account),last===before?after:last);
 delete account.operation.after;
 assert.strictEqual(operationReading(account),before,'failed post-send query retains the successful before observation');
}
assert(source.includes('本次操作的最近观测'));
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_dates_invalidate_loaded_status_and_summary(self):
        script = r'''
const fs=require('fs'), assert=require('assert');
const source=fs.readFileSync('web/pivot.js','utf8'), nodes={};
for(const id of ['#date-error','#pivot-panel','#ranking-panel','#pivot-status','#query-summary']) nodes[id]={textContent:'old loaded result'};
nodes['#metric']={selectedOptions:[{textContent:'总 token'}]};
nodes['#start-date']=nodes['#end-date']={checkValidity:()=>true};
let dates={start:'',end:'2026-10-16'}; const dateParams=()=>dates;
const AgentMonitor={getRange:()=> 'custom',qs:id=>nodes[id]};
eval(source.slice(source.indexOf('  function validDates('),source.indexOf('  function setDimensions(')));
assert.equal(validDates(),false);assert.equal(nodes['#pivot-status'].textContent,'未加载 · 日期无效');
assert.equal(nodes['#query-summary'].textContent,'总 token · 请选择起始和终止日期。');
assert.equal(nodes['#ranking-panel'].hidden,true);
dates={start:'2026-10-17',end:'2026-10-16'};assert.equal(validDates(),false);
assert(nodes['#query-summary'].textContent.includes('不能晚于'));
dates.start='2026-10-10';assert.equal(validDates(),true);assert.equal(nodes['#date-error'].hidden,true);
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_batch_confirmation_freezes_selection_and_preserves_failure(self):
        script = r'''
const fs=require('fs'), assert=require('assert');
const source=fs.readFileSync('web/codex-accounts.js','utf8');
let submittingBatch=false, selectedIds=new Set(['a']), batch={id:'old'};
let accounts=[{id:'a',email:'a@example.test',account_id:'workspace-a'}, {id:'b',email:'b@example.test',account_id:'workspace-b'}];
const notice={textContent:''}, calls=[]; let accept=false, fail=false;
const current=()=>true, render=()=>{}, load=async()=>{notice.textContent='';};
const request=async(action,payload)=>{calls.push({action,payload}); if(fail)throw Error('selection expired');};
global.window={confirm(message){assert(message.includes('a@example.test'));assert(!message.includes('b@example.test')); selectedIds=new Set(['b']);batch={id:'new'};return accept;}};
eval(source.slice(source.indexOf('  async function performBatch('),source.indexOf('  function renderBatch(')));
(async()=>{
 await performBatch('batch-start');assert.equal(calls.length,0,'cancel sends nothing');
 selectedIds=new Set(['a']);batch={id:'old'};accept=true;
 await performBatch('batch-start');
 assert.deepEqual(calls[0],{action:'batch-start',payload:{expected_batch_id:'old',profile_ids:['a']}});
 fail=true;selectedIds=new Set(['a']);
 await performBatch('batch-start');assert(notice.textContent.includes('selection expired'));assert.equal(submittingBatch,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_navigation_resets_scroll_before_background_data_finishes(self):
        script = r'''
const fs=require('fs'), assert=require('assert');
const source=fs.readFileSync('web/app.js','utf8');
let finish, inFlightNavigation, pageGeneration=0, renderedRoute;
const waiting=new Promise(resolve=>finish=resolve), positions=[];
const clientRouteFor=()=>()=>waiting, endPageLifetime=()=>{}, applyDocument=()=>true;
const routeKey=url=>url.pathname, pageIsCurrent=()=>true;
global.window={history:{pushState(){}},scrollTo(options){positions.push(options.top);}};
global.fetch=async()=>({ok:true,text:async()=>'<main>next page</main>'});
eval(source.slice(source.indexOf('  async function renderRoute('),source.indexOf('  function initClientNavigation(')));
(async()=>{
 const navigation=renderRoute(new URL('http://fixture/explore'),{push:true});
 await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(positions,[0],'new page reading starts at its heading while background data is pending');
 finish(); await navigation;
 assert.deepEqual(positions,[0],'late data must not move a reader who already started reading');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
