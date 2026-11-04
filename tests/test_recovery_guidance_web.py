"""User decisions on the production recovery panel; synthetic state, no provider I/O."""
import subprocess
import unittest
from pathlib import Path


class RecoveryGuidanceTests(unittest.TestCase):
    def test_state_explanations_and_read_only_handoff(self):
        script = r'''
const assert = require('node:assert/strict');
function node(tag) {
  let text = '';
  return {tag, dataset: {}, children: [], listeners: {}, disabled: false, hidden: false,
    set textContent(v) { text=String(v); this.children=[]; },
    get textContent() { return text+this.children.map(n=>n.textContent).join('\n'); },
    attrs: {}, setAttribute(k,v) {this.attrs[k]=v;}, append(...nodes) {nodes.forEach(n=>this.appendChild(n));},
    appendChild(n) {n.remove?.(); n.parentNode=this; this.children.push(n);return n;},
    remove() {if(this.parentNode) this.parentNode.children=this.parentNode.children.filter(n=>n!==this); this.parentNode=null;},
    replaceChildren(...nodes) {text='';this.children=nodes;},
    addEventListener(k, f) {this.listeners[k]=f;},
    click() {return this.listeners.click?.();}, focus() {}, select() {this.selected=true;}
  };
}
const all = n => [n, ...n.children.flatMap(all)];
const base = {machine:'fixture-host',observed_at:'2026-10-28T00:00:00Z',active:null,
  account:{account_id:'fixture-id',account_label:'fixture@example.test',source:'default'},
  pause:{id:'fixture-pause',failure:{stage:'quota_read',rpc_code:-32603,detail:'fixture error'}}};
let value, fail=false, calls=[];
const ui = require('./web/quota.js')({platform:{document:{createElement:node,dispatchEvent() {}},
  window:{AgentMonitor:{onPageCleanup() {}},navigator:{}},fetch:async(url,opts)=>{
    calls.push([url,opts?.method || 'GET']);
    if(fail) throw Error('fixture state unavailable');
    return {ok:true,json:async()=>value};
  }},formatDate:String});
async function render(changes={}, error=false) {
  value={...base,...changes};fail=error;
  const p=ui.createRecoveryPanel();
  await new Promise(r=>setImmediate(r));
  return p;
}
(async()=>{
  for (const [change, expected] of [
    [{pause:{...base.pause,probe_attempt:{id:'used'}}},/本次.*已使用/],
    [{active:{id:'running'}},/正在|未结束|未闭合/],
    [{account:{account_label:'fixture@example.test'}},/身份.*确认|身份.*缺失/],
    [{pause:{id:'unreadable'}},/记录.*无法读取|记录.*不可读/]
  ]) {
    const p=await render(change);
    assert.match(p.textContent,expected);
    assert.match(p.textContent,/更新状态|更新进度/);
    assert.equal(all(p).find(n=>n.attrs?.['data-recovery-action']==='probe').disabled,true);
  }
  let p=await render();
  const technical=all(p).find(n=>n.tag==='details' && n.className==='recovery-technical');
  assert(technical && !technical.open, 'technical evidence must be secondary by default');
  const visibleText=n => n.hidden ? '' : n.tag==='details' && !n.open ? n.children[0]?.textContent || '' : n.children.length ? n.children.map(visibleText).join('\n') : n.textContent;
  assert(!visibleText(p).includes('fixture-id'), 'workspace identifiers must not dominate the recovery task');
  assert.equal(all(p).filter(n=>n.tag==='button' && n.className.includes('btn-primary') && !n.hidden).length,1,'one recommended action');

  assert.match(visibleText(p),/配额读取失败，需要一次受控检查/);
  assert.match(visibleText(p),/检查只查询一次配额/);
  assert.match(p.textContent,/不会.*发送|不.*重发/);
  const copy=all(p).find(n=>n.tag==='button' && n.textContent==='复制诊断信息');
  assert(copy);
  const before=calls.length;
  await copy.click();
  assert.equal(calls.length,before,'handoff must only use already-read information');
  const report=all(p).find(n=>n.tag==='textarea');
  assert(report && !report.hidden && report.selected,'clipboard-unavailable fallback is selectable');
  assert.match(report.value,/fixture-host/);
  assert.match(report.value,/-32603/);
  assert.doesNotMatch(report.value,/原始原因|原始失败/);
  assert.match(report.value,/512/);
  assert.match(report.value,/公开/);
  p=await render({pause:{...base.pause,failure:{detail:'request failed ([url redacted])'}}});
  assert.match(p.textContent,/占位符|隐藏/);
  assert.match(p.textContent,/无法.*还原|不能.*还原/);
  assert.match(p.textContent,/不会.*重试|不.*重试/);
  p=await render({pause:{...base.pause,failure:{detail:'current pause ([url redacted])'}},
    last_recovery:{ok:false,failure:{detail:'different recovery record'}}});
  assert.match(p.textContent,/无法.*还原|不能.*还原/);
  await all(p).find(n=>n.tag==='button' && /复制诊断/.test(n.textContent)).click();
  assert.match(all(p).find(n=>n.tag==='textarea').value,/无法.*还原|不能.*还原/);
  p=await render({pause:{...base.pause,failure:{detail:'request failed (https://example.test/usage?workspace=ordinary)'}}});
  assert.match(p.textContent,/https:\/\/example.test\/usage\?workspace=ordinary/);
  p=await render({},true);
  assert.match(p.textContent,/无法确认恢复状态/);
  assert.equal(all(p).find(n=>n.attrs?.['data-recovery-action']==='probe').disabled,true);
  p=await render({pause:null});
  assert.match(p.textContent,/当前没有通道暂停/);
  assert(all(p).find(n=>n.attrs?.['data-recovery-action']==='continue').hidden, 'remote consumer cannot resume local sending');
  assert.match(p.textContent,/发送/);
  assert(calls.every(([,method])=>method==='GET'),'viewing instructions never probes or sends');

  value={...base};fail=false;
  const provider={key:'codex',label:'Codex'}, failure={machine:base.machine,reason:'fixture pause',query_state:'paused',pause:base.pause};
  const body=ui.quotaFailureDetails(provider,[failure]);
  const source=body.quotaDiagnostics.entries[0];
  assert.match(source.note.textContent,/上次配额采集已暂停/);
  assert(!source.note.textContent.includes(failure.reason));
  assert.equal(source.sourceText.textContent,failure.reason);
  assert(!source.sourceText.parentNode.open,'raw collection error is closed by default');
  // Several observations share one machine-wide pause, not independent failures.
  const own = {machine:'alpha',account_id:'workspace-a',account_label:'a@example.test',
    query_state:'paused',pause:{id:'pause-a'},reason:'stage=quota_read; kind=rpc_error',
    failure:{stage:'quota_read',kind:'rpc_error'}};
  const affected = {...own,account_id:'workspace-b',account_label:'b@example.test',
    account_profile:'profile-b',failure:null};
  const second = {...own,machine:'beta',pause:{id:'pause-b'}};
  const grouped = ui.quotaFailureDetails(provider,[own,{...own,account_profile:'profile-a'},affected,second]);
  assert.match(grouped.quotaDiagnostics.summary.textContent,/2 台暂停/);
  assert.match(grouped.quotaDiagnostics.summary.textContent,/涉及 2 个账号/);
  assert.doesNotMatch(grouped.quotaDiagnostics.summary.textContent,/读取失败 [234]/);
  assert.equal(grouped.quotaDiagnostics.entries.length,2,'one card per machine pause');
  assert.equal(all(grouped).filter(n=>n.tag==='button' && n.textContent==='查看当前状态与恢复').length,2);
  const alpha = grouped.quotaDiagnostics.entries.find(e=>e.machine==='alpha');
  assert.match(alpha.entry.textContent,/a@example.test/);
  assert.match(alpha.entry.textContent,/b@example.test/);
  assert.match(alpha.entry.textContent,/受.*暂停影响/);
  assert.match(alpha.entry.textContent,/profile-a/);
  assert.match(alpha.entry.textContent,/profile-b/);
  assert.equal(alpha.accounts.children.length,2,'native and managed observations of the same identity are one account');
  value={...base,machine:'alpha',pause:{id:'pause-a',failure:own.failure}};
  await all(alpha.entry).find(n=>n.tag==='button').click(); await new Promise(r=>setImmediate(r));
  const alphaPanel=alpha.panel, readCount=calls.length;
  ui.quotaFailureDetails(provider,[second,affected,own],grouped);
  assert.strictEqual(grouped.quotaDiagnostics.entries.find(e=>e.machine==='alpha').panel,alphaPanel);
  assert.equal(calls.length,readCount,'reordering and source aliases do not cause remote polling');
  const changed={...affected,account_id:'workspace-c',account_label:'c@example.test'};
  ui.quotaFailureDetails(provider,[second,changed],grouped);
  assert.doesNotMatch(alpha.accounts.textContent,/a@example.test|b@example.test/);
  assert.match(alpha.accounts.textContent,/c@example.test/);
  const partial=ui.quotaFailureDetails(provider,[{...own,account_id:null,account_label:null},second]);
  assert.match(partial.quotaDiagnostics.summary.textContent,/另有账号身份未确认/);
  for (const label of [42,{email:'invalid'},null]) {
    const malformed=ui.quotaFailureDetails(provider,[{...own,account_label:label}]);
    assert.match(malformed.quotaDiagnostics.summary.textContent,/账号身份未确认/);
  }
  const shared=ui.quotaFailureDetails(provider,[{...own,query_state:'failed'},affected]);
  assert.equal(shared.quotaDiagnostics.entries.length,1,'failed trigger and affected accounts share one pause card');
  assert.match(shared.quotaDiagnostics.summary.textContent,/1 台暂停/);
  assert.match(shared.quotaDiagnostics.entries[0].accounts.textContent,/查询未完成/);
  assert.match(shared.quotaDiagnostics.entries[0].accounts.textContent,/受暂停影响/);
  for (const labels of [['old@example.test','new@example.test'],[null,null]]) {
    const claude=ui.quotaFailureDetails({key:'claude',label:'Claude'},[
      {...own,account_label:labels[0]}, {...second,account_label:labels[1]}]);
    assert.match(claude.quotaDiagnostics.summary.textContent,/涉及 1 个账号/);
    assert.doesNotMatch(claude.quotaDiagnostics.summary.textContent,/身份未确认/);
  }
  const classified = ui.quotaFailureDetails(provider, [
    {machine:'macbook',reason:'登录账号已变化，等待统计同步确认',query_state:'paused'},
    {machine:'macmini',reason:'配额读取失败，stage=quota_read，kind=rpc_error',query_state:'paused',
      failure:{stage:'quota_read',kind:'rpc_error'}},
    {machine:'macstudio',reason:'凭据失败，upstream_code=token_revoked，http_status=401',query_state:'paused',
      failure:{upstream_code:'token_revoked',http_status:401}},
  ]);
  assert.match(classified.quotaDiagnostics.summary.textContent,/3 台暂停；账号身份未确认/);
  assert.doesNotMatch(classified.quotaDiagnostics.summary.textContent,/读取失败 \d/);
  assert.match(classified.quotaDiagnostics.entries[0].note.textContent,/账号身份变化.*确认这台机器当前登录的是目标账号/);
  assert.match(classified.quotaDiagnostics.entries[1].note.textContent,/配额读取失败，原因未核实.*目标机器的 Agent Monitor 路由桥/);
  assert.match(classified.quotaDiagnostics.entries[2].note.textContent,/上游返回 401\/token_revoked，原因未核实.*目标机器的 Agent Monitor 路由桥/);
  p=await render({pause:{...base.pause,failure:{stage:'quota_read',upstream_code:'token_revoked',http_status:401,detail:'invalidated oauth token'}}});
  assert.match(visibleText(p),/上游认证响应需要核对/);
  assert.match(visibleText(p),/不能区分凭据、权限或服务端原因/);
  assert.match(visibleText(p),/通过目标机器的 Agent Monitor 路由桥只检查一次/);
  assert.equal(all(p).find(n=>n.attrs?.['data-recovery-action']==='probe').hidden,false);
  assert.match(p.textContent,/页面不能替你修复该机器的账号或部署/);
  p=await render({pause:{...base.pause,failure:{stage:'quota_read',http_status:401,detail:'upstream refused'}}});
  assert.match(visibleText(p),/上游返回 HTTP 401，原因未核实/);
  assert.doesNotMatch(visibleText(p),/token_revoked/);
  p=await render({pause:{...base.pause,failure:{stage:'quota_read',upstream_code:'token_revoked',detail:'upstream refused'}}});
  assert.match(visibleText(p),/上游返回 token_revoked，原因未核实/);
  assert.doesNotMatch(visibleText(p),/HTTP 401/);
  p=await render({pause:{id:'new-pause'},last_recovery:{ok:false,request:{pause_id:'old-pause',machine:'fixture-host',account_id:'fixture-id'},failure:{upstream_code:'token_revoked',http_status:401}}});
  assert.doesNotMatch(visibleText(p),/上游认证响应需要核对/);
  assert.match(visibleText(p),/发送已暂停，需要先检查配额/);
  const only401=ui.quotaFailureDetails(provider,[{machine:'only-401',reason:'上游拒绝，http_status=401',query_state:'paused',failure:{http_status:401}}]);
  assert.match(only401.quotaDiagnostics.entries[0].note.textContent,/上游返回 HTTP 401，原因未核实/);
  assert.doesNotMatch(only401.quotaDiagnostics.entries[0].note.textContent,/token_revoked/);
  const onlyToken=ui.quotaFailureDetails(provider,[{machine:'only-token',reason:'上游拒绝，upstream_code=token_revoked',query_state:'paused',failure:{upstream_code:'token_revoked'}}]);
  assert.match(onlyToken.quotaDiagnostics.entries[0].note.textContent,/上游返回 token_revoked，原因未核实/);
  assert.doesNotMatch(onlyToken.quotaDiagnostics.entries[0].note.textContent,/HTTP 401/);
  value={...base,pause:{...base.pause,failure:{stage:'quota_read',kind:'rpc_error',detail:'target read failed'}}};
  p=ui.createRecoveryPanel('fixture-host',{sourceFailure:{machine:'fixture-host',reason:'登录账号已变化，等待统计同步确认',query_state:'paused',failure:{}}});
  await new Promise(r=>setImmediate(r));
  assert.match(visibleText(p),/恢复状态来源不一致，需要核对/);
  assert.match(visibleText(p),/页面汇总显示“账号身份变化”，目标机器状态显示“配额读取失败，原因未核实”/);
  assert.match(visibleText(p),/目标机器的 Agent Monitor 路由桥只检查一次/);
  assert.equal(all(p).find(n=>n.attrs?.['data-recovery-action']==='probe').hidden,false);
  body.quotaDiagnostics.details.open=true;
  await all(body).find(n=>n.tag==='button' && n.textContent==='查看当前状态与恢复').click();
  await new Promise(r=>setImmediate(r));
  const originalPanel=body.quotaDiagnostics.entries[0].panel, readsBefore=calls.length;
  assert.strictEqual(ui.quotaFailureDetails(provider,[failure],body),body);
  assert.strictEqual(body.quotaDiagnostics.entries[0].panel,originalPanel);
  assert(body.quotaDiagnostics.details.open,'snapshot refresh preserves the open disclosure');
  assert.equal(calls.length,readsBefore,'unchanged snapshots must not poll status again');
  ui.quotaFailureDetails(provider,[{...failure,pause:{...base.pause,id:'new'}}],body);
  await new Promise(r=>setImmediate(r));
  assert.equal(calls.length,readsBefore+1,'changed pause refreshes the existing card');
  value={...base,pause:null};
  ui.quotaFailureDetails(provider,[],body); await new Promise(r=>setImmediate(r));
  assert.strictEqual(body.quotaDiagnostics.entries[0].panel,originalPanel,'removing a failure retains the result being read');
  assert.match(body.quotaDiagnostics.summary.textContent,/最近恢复记录/);
  assert.match(originalPanel.textContent,/当前没有通道暂停/);
  assert(!body.quotaDiagnostics.entries[0].entry.dataset.codexPauseId);
  ui.quotaFailureDetails(provider,[],body);
  assert.equal(calls.length,readsBefore+2,'retained history must not create recurring remote reads');
  const action=(p,key)=>all(p).find(n=>n.attrs?.['data-recovery-action']===key);
  const settle=()=>new Promise(r=>setImmediate(r));
  for (const scenario of ['matched','missing','still-paused']) {
    let current={...base}, posts=0, continued=0;
    const recovery=require('./web/quota.js')({platform:{document:{createElement:node,dispatchEvent(){}},
      window:{AgentMonitor:{onPageCleanup(){},previewUpstream:async()=>"fixture-confirmation"},confirm:()=>true},fetch:async(url,options)=>{
        if(options?.method==='POST') {
          posts++;
          current={...base,pause:scenario==='still-paused'?base.pause:null,
            last_recovery:scenario==='matched'?{ok:true,request:{pause_id:base.pause.id}}:null};
          throw Error('lost response');
        }
        return {ok:true,json:async()=>current};
      }},formatDate:String});
    p=recovery.createRecoveryPanel('self',{onContinue:()=>continued++}); await settle();
    await action(p,'probe').click();
    await action(p,'read').click();
    if(scenario==='still-paused') {
      assert.match(p.textContent,/本次检查结果尚未确认/);
      assert(action(p,'continue').hidden); assert(action(p,'probe').disabled);
    } else {
      assert.match(p.textContent,/当前没有通道暂停/);
      assert(!action(p,'continue').hidden); await action(p,'continue').click(); assert.equal(continued,1);
      if(scenario==='missing') assert.match(p.textContent,/未取得该次结果记录/);
    }
    assert.equal(posts,1,'neither state reconciliation nor continuation repeats a probe');
  }
  let reads=0, finish;
  const delayed=require('./web/quota.js')({platform:{document:{createElement:node,dispatchEvent(){}},
    window:{AgentMonitor:{onPageCleanup(){}}},fetch:async()=>{
      reads++;
      if(reads===1) await new Promise(resolve=>finish=resolve);
      return {ok:true,json:async()=>({...base,pause:reads===1?base.pause:null})};
    }},formatDate:String});
  p=delayed.createRecoveryPanel('self',{onContinue(){}});
  p.refresh(); finish(); await settle(); await settle();
  assert.equal(reads,2,'a state transition during a read must schedule one follow-up read');
  assert.match(p.textContent,/当前没有通道暂停/);

})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result = subprocess.run(['node', '-e', script], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
