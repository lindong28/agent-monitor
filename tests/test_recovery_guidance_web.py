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
  return {tag, children: [], listeners: {}, disabled: false, hidden: false,
    set textContent(v) { text=String(v); this.children=[]; },
    get textContent() { return text+this.children.map(n=>n.textContent).join('\n'); },
    setAttribute() {}, append(...nodes) {this.children.push(...nodes);},
    appendChild(n) {this.children.push(n);return n;},
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
    [{account:{account_label:'fixture@example.test'}},/身份.*不完整|身份.*缺失/],
    [{pause:{id:'unreadable'}},/记录.*无法读取|记录.*不可读/]
  ]) {
    const p=await render(change);
    assert.match(p.textContent,expected);
    assert.match(p.textContent,/重新读取状态/);
    assert.equal(all(p).find(n=>n.tag==='button' && /配额一次/.test(n.textContent)).disabled,true);
  }
  let p=await render();
  assert.match(p.textContent,/为什么不能发送/);
  assert.match(p.textContent,/可以检查一次/);
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
  assert.match(p.textContent,/状态未核实/);
  assert.equal(all(p).find(n=>n.tag==='button' && /配额一次/.test(n.textContent)).disabled,true);
  p=await render({pause:null});
  assert.match(p.textContent,/返回.*账号操作/);
  assert.match(p.textContent,/发送/);
  assert(calls.every(([,method])=>method==='GET'),'viewing instructions never probes or sends');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result = subprocess.run(['node', '-e', script], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
