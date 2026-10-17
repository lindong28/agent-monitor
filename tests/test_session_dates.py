"""Custom date input errors must be recoverable before any session request."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SessionDateTests(unittest.TestCase):
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
