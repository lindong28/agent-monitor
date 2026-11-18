"""Current pause reads use the product GET contract, without provider or account I/O."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class QuotaCurrentStateTests(unittest.TestCase):
    def test_current_pause_link_opens_one_read_only_machine_card_without_history(self):
        script = r"""
const assert=require('node:assert/strict');
(async()=>{
  function element(tag) {
    let text='';
    return {tag,children:[],dataset:{},listeners:{},style:{},hidden:false,
      set textContent(v){text=String(v);this.children=[];},
      get textContent(){return text+this.children.map(c=>c.textContent).join('');},
      append(...c){this.children.push(...c);},appendChild(c){this.children.push(c);return c;},
      replaceChildren(...c){text='';this.children=c;},setAttribute(){},
      addEventListener(k,f){this.listeners[k]=f;},scrollIntoView(){},focus(){}};
  }
  const flatten=n=>[n,...n.children.flatMap(flatten)];
  const table=element('table'),location=new URL('http://fixture/'),calls=[],cleanup=[];
  let hold=false;
  const account={account_state:'known',account_id:'a',account_label:'one@example.test',
    machines:['held machine','clear'],updated_at:'2026-11-18T00:00:00Z'};
  const ui=require('./web/quota.js')({qs:s=>s==='#quota-diagnostic-table'?table:null,
    formatDate:String,fmtAbs:String,collectionUrl:hash=>'/collection?return=%2F'+hash,
    platform:{document:{createElement:element,dispatchEvent(){}},
      window:{location,AgentMonitor:{onPageCleanup:f=>cleanup.push(f)}},fetch:async(url,options)=>{
        calls.push({url,options});
        if(hold)await new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true}));
        return {ok:true,json:async()=>({machine:'held machine',
          observed_at:'2026-11-18T00:00:00Z',pause:{id:'current'},active:null,account:{}})};
      }}});
  ui.updateQuotaMachineState('held machine',{machine:'held machine',observed_at:account.updated_at,pause:{id:'current'},active:null});
  ui.updateQuotaMachineState('clear',{machine:'clear',observed_at:account.updated_at,pause:null,active:null});
  const cell=ui.quotaUpdatedCell({key:'codex'},account,false,ui.quotaRefreshNotice({key:'codex'},account));
  const links=flatten(cell).filter(n=>n.tag==='a');
  assert.equal(links.length,1);assert.match(links[0].textContent,/held machine.*恢复/);
  const target=new URL(links[0].href,'http://fixture');
  assert.equal(target.pathname,'/collection');assert.equal(target.searchParams.get('return'),'/');
  location.hash=target.hash;
  const data={codex:{accounts:[account]}};
  ui.renderQuotaDiagnostics(data);
  await new Promise(r=>setImmediate(r));
  const panel=flatten(table).find(n=>n.className==='quota-recovery-panel');
  assert(panel,'current protection must be reachable without a historical failure row');
  assert.equal(calls.length,1);assert.equal(calls[0].options.method,undefined);
  assert.equal(new URL(calls[0].url,'http://fixture').searchParams.get('machine'),'held machine');
  ui.renderQuotaDiagnostics(data);
  assert.equal(flatten(table).find(n=>n.className==='quota-recovery-panel'),panel);
  assert.equal(calls.length,1,'snapshot refresh must not restart checks or discard the card');
  hold=true;const pending=panel.refresh();await new Promise(r=>setImmediate(r));
  cleanup.forEach(f=>f());await pending;assert(calls[1].options.signal.aborted);
  console.log('current machine link; no history required; one GET; card retained; page cancellation');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_read_only_refresh_races_failures_and_page_lifetime(self):
        script = r"""
const assert = require('assert');
(async () => {
  let clock = Date.now(), calls = [], mode = 'ok', inFlight = 0, peak = 0, release;
  Date.now = () => clock;
  const tick = () => new Promise(resolve => setImmediate(resolve));
  const ui = require('./web/quota.js')({qs:()=>null, formatDate:s=>s, platform:{window:{}, document:{},
    fetch: async (url, options) => {
      assert.equal(options.method, undefined); assert(url.startsWith('/api/quota-recovery?'));
      assert.equal(options.headers['X-Agent-Monitor'],'action');
      const machine = new URL(url,'http://fixture.test').searchParams.get('machine');
      calls.push(machine); inFlight++; peak=Math.max(peak,inFlight);
      try {
        if(mode==='deferred') await new Promise((resolve,reject)=>{
          release=resolve; options.signal.addEventListener('abort',()=>reject(new Error('aborted')), {once:true});
        });
        await tick();
        if(mode==='failed') throw new Error('local status unavailable');
        return {ok:mode!=='http-error', json:async()=> mode==='incomplete' ? {machine} : {
          machine:mode==='wrong-machine'?'other':machine, observed_at:new Date(clock).toISOString(),
          pause:machine==='held'?{id:'pause-current'}:null,active:null}};
      } finally { inFlight--; }
    }}});
  const account={account_state:'known',account_id:'account-a',account_label:'a@example.test',machines:['held','clear']};
  const data={codex:{accounts:[account,{...account,machines:['clear']}]}};
  await Promise.all([ui.refreshQuotaMachineStates(data),ui.refreshQuotaMachineStates(data)]);
  assert.deepEqual(calls.sort(),['clear','held']);
  assert(ui.quotaRefreshNotice({key:'codex'},account,true).includes('held 自动查询已暂停'));
  assert(!ui.quotaRefreshNotice({key:'codex'},account,true).includes('clear'));
  await ui.refreshQuotaMachineStates(data); assert.equal(calls.length,2);
  for(const nextMode of ['failed','http-error','wrong-machine','incomplete']) {
    mode=nextMode; clock+=30001; await ui.refreshQuotaMachineStates(data);
    assert.equal(ui.quotaRefreshNotice({key:'codex'},account,true),'held 暂停状态未核实；clear 暂停状态未核实',nextMode);
  }
  mode='ok'; clock+=30001; await ui.refreshQuotaMachineStates(data);
  // An account-scoped receipt cannot clear a machine-wide hold.
  ui.updateQuotaMachineState('held',{machine:'held',observed_at:new Date(clock).toISOString(),pause_scope:'account',pause:null,active:null});
  assert(ui.quotaRefreshNotice({key:'codex'},account,true).includes('held 自动查询已暂停'));
  mode='deferred'; clock+=30001;
  const single={codex:{accounts:[{...account,machines:['clear']}]}};
  const pending=ui.refreshQuotaMachineStates(single); await tick();
  ui.updateQuotaMachineState('clear',{machine:'clear',observed_at:new Date(clock).toISOString(),pause:{id:'newer'},active:null});
  mode='ok'; release(); await pending;
  assert(ui.quotaRefreshNotice({key:'codex'},account,true).includes('clear 自动查询已暂停'));
  mode='deferred'; clock+=30001;
  const cancelled=ui.refreshQuotaMachineStates(single); await tick();
  ui.resetQuotaReadings(); await cancelled;
  assert.equal(inFlight,0); assert.equal(ui.quotaRefreshNotice({key:'codex'},account,true),null);
  mode='ok'; calls=[]; peak=0;
  await ui.refreshQuotaMachineStates({codex:{accounts:[{...account,machines:Array.from({length:11},(_,i)=>'m'+i)}]}});
  assert.equal(calls.length,11); assert.equal(new Set(calls).size,11); assert.equal(peak,8);
  function element(tag) {
    let text='';
    return {tag,children:[],listeners:{},set textContent(v){text=String(v);this.children=[];},
      get textContent(){return text+this.children.map(c=>c.textContent).join('');},
      append(...c){this.children.push(...c);},appendChild(c){this.children.push(c);return c;},
      replaceChildren(...c){this.children=c;},setAttribute(){},addEventListener(k,f){this.listeners[k]=f;}};
  }
  // Reverse completion order: an older card response/failure cannot replace a newer background state.
  for (const [oldPause,newPause,oldFails] of [[{id:'old'},null,false],[null,{id:'new'},false],[null,{id:'new'},true]]) {
    let call=0, releaseCard;
    const state=pause=>({machine:'alpha',observed_at:new Date(clock).toISOString(),pause,active:null,account:{}});
    const current=require('./web/quota.js')({qs:()=>null,formatDate:s=>s,platform:{
      document:{createElement:element,dispatchEvent(){}},window:{AgentMonitor:{onPageCleanup(){}}},
      fetch:async()=>{
        if(call++===0) {await new Promise(resolve=>releaseCard=resolve);if(oldFails)throw new Error('old failed');return {ok:true,json:async()=>state(oldPause)};}
        return {ok:true,json:async()=>state(newPause)};
      }}});
    const panel=current.createRecoveryPanel('alpha');
    await tick();
    const sample={...account,machines:['alpha']};
    await current.refreshQuotaMachineStates({codex:{accounts:[sample]}});
    const before=current.quotaRefreshNotice({key:'codex'},sample,true);
    releaseCard();await tick();await tick();
    assert.equal(current.quotaRefreshNotice({key:'codex'},sample,true),before);
    assert.equal(Boolean(before?.includes('自动查询已暂停')),Boolean(newPause));
    panel.dispose();current.resetQuotaReadings();
  }
  console.log('state reads: coalescing, 4 failure modes, both response orders, scope guards, abort and 8-worker bound');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
