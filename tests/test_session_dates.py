"""Custom date input errors must be recoverable before any session request."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SessionDateTests(unittest.TestCase):
    def test_timer_refresh_keeps_rows_operable_and_still_reports_errors(self):
        script = r'''
const fs=require('fs'), assert=require('assert'), vm=require('vm');
const timers=[];
function node() {
  return {dataset:{},value:'',children:[],listeners:{},attrs:{},disabled:false,textContent:'',
    get innerHTML(){return this.html||'';},set innerHTML(v){this.html=v;this.children=[];},
    options:[{textContent:'全部'}],appendChild(c){this.children.push(c);return c;},
    setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,fn){(this.listeners[k]??=[]).push(fn);},checkValidity(){return true;}};
}
const filters=['#filter-machine','#filter-agent','#filter-project','#filter-model','#filter-cost-status'];
const nodes=Object.fromEntries(['#range','#session-start','#session-end','#session-dates','#sessions-body','#session-count','#page-status',...filters].map(k=>[k,node()]));
nodes['#range'].value='30d';
const location=new URL('http://fixture.test/sessions');
let pending=null, delay=false, fail=false, tokens=10;
const row=()=>({session_id:'fixture',agent_id:'codex',project:'repo',model:'M',started_at:'2026-10-19T01:00:00Z',ended_at:'2026-10-19T01:00:00Z',tokens,usage_events:1,cost_usd:null,estimated:true});
const response=()=>({ok:true,json:async()=>({sessions:[row()],filters:{}})});
const context={URL,URLSearchParams,console,Date,Intl,AbortController,setTimeout,clearTimeout,
 document:{readyState:'loading',querySelector:k=>nodes[k]||null,querySelectorAll:()=>[],addEventListener(){},createElement:node,createTextNode:text=>({textContent:text})},
 window:{location,history:{replaceState(_,__,url){location.href=new URL(url,location).href;}},addEventListener(){},setInterval(fn,ms){timers.push({fn,ms});return timers.length;}},
 fetch:async url=>{
   if(!String(url).includes('/api/sessions'))return {ok:true,json:async()=>({machines:[],syncing:false})};
   if(fail)throw new Error('fixture unavailable');
   if(delay)return new Promise(resolve=>{pending=()=>resolve(response());});
   return response();
 }};
(async()=>{
 vm.runInNewContext(fs.readFileSync('web/app.js','utf8'),context);
 await context.window.AgentMonitor.initSessions();
 const visible=()=>nodes['#sessions-body'].children.filter(n=>n.className==='session-row');
 assert.equal(visible().length,1,nodes['#page-status'].textContent);
 const refresh=timers.find(t=>t.ms===30000).fn;
 delay=true;let running=refresh();
 assert.equal(visible().length,1,'timer must retain the previous real row');
 assert(filters.every(k=>nodes[k].disabled===false),'timer must retain usable filters');
 tokens=20;pending();await running;
 assert(visible()[0].innerHTML.includes('20'));
 delay=false;fail=true;await refresh();
 assert(nodes['#page-status'].textContent.includes('fixture unavailable'));
 fail=false;await refresh();assert.equal(visible().length,1);
 delay=true;nodes['#filter-model'].value='M';nodes['#filter-model'].listeners.change[0]();
 assert.equal(visible().length,0,'explicit filter change still shows loading');
 pending();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(visible().length,1);
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_dates_do_not_query_and_correction_recovers(self):
        script = r'''
const fs=require('fs'), assert=require('assert'), vm=require('vm');
const source=fs.readFileSync('web/app.js','utf8');
async function scenario(query, expected) {
  const calls=[];
  function node() {return {dataset:{},value:'',children:[],listeners:{},hidden:false,textContent:'',innerHTML:'',options:[{textContent:'全部'}],
    appendChild(c){this.children.push(c);return c;},setAttribute(){},addEventListener(k,fn){(this.listeners[k]??=[]).push(fn);},
    checkValidity(){return this.value!== 'invalid';}};}
  const nodes=Object.fromEntries(['#range','#session-start','#session-end','#session-dates','#sessions-body','#session-count','#page-status'].map(k=>[k,node()]));
  nodes['#range'].value='30d';
  const location=new URL('http://fixture.test/sessions?'+query);
  const context={URL,URLSearchParams,console,Date,Intl,AbortController,setTimeout,clearTimeout,
    document:{readyState:'loading',querySelector:k=>nodes[k]||null,querySelectorAll:()=>[],addEventListener(){},createElement:node,createTextNode:text=>({textContent:text})},
    window:{location,history:{state:null,replaceState(_,__,url){const next=new URL(url,location);location.href=next.href;}},addEventListener(){}},
    fetch:async url=>{calls.push(String(url)); return {ok:true,json:async()=>String(url).includes('/api/sessions')?{sessions:[],filters:{}}:{machines:[],syncing:false}};}};
  vm.runInNewContext(source,context); await context.window.AgentMonitor.initSessions();
  if(expected) {
    assert.equal(calls.filter(x=>x.includes('/api/sessions')).length,0,query+' must not query an invalid window');
    assert(nodes['#page-status'].textContent.includes(expected),nodes['#page-status'].textContent);
    if(query==='range=custom') assert.equal(nodes['#session-start'].value,'','missing date must remain visibly missing');
    // Correcting the same controls recovers without a page reload.
    for(const [id,date] of [['#session-start','2026-10-01'],['#session-end','2026-10-17']]) {
      nodes[id].value=date; for(const handler of nodes[id].listeners.change||[]) await handler();
    }
    await new Promise(resolve=>setImmediate(resolve));
    assert(calls.some(x=>x.includes('/api/sessions')&&x.includes('start=2026-10-01')&&x.includes('end=2026-10-17')),JSON.stringify(calls));
    assert(nodes['#page-status'].textContent.includes('没有会话'),nodes['#page-status'].textContent);
  } else assert(calls.some(x=>x.includes('/api/sessions')),query);
}
(async()=>{
 for(const [query,error] of [
  ['range=custom','请选择起始和终止日期'],
  ['range=custom&start=2026-10-17&end=2026-10-01','起始日期不能晚于终止日期'],
  ['range=custom&start=invalid&end=2026-10-17','请选择有效日期'],
  ['range=custom&start=10000-01-01&end=10000-01-02','请选择有效日期'],
  ['range=custom&start=02026-10-01&end=02026-10-17','请选择有效日期'],
  ['range=custom&start=0000-01-01&end=2026-10-17','请选择有效日期'],
  ['range=custom&start=2026-10-01&end=2026-10-17',null], ['range=7d',null]
 ]) await scenario(query,error);
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
