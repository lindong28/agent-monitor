"""Exercise the production renderer with synthetic quota records, without provider I/O."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class QuotaExplorerTests(unittest.TestCase):
    def test_filters_notices_polling_and_removal_keep_full_snapshot(self):
        script = r'''
const fs = require('fs'), assert = require('assert');
function node(tag = 'div') {
  let text = '';
  const n = { tagName: tag.toUpperCase(), children: [], dataset: {}, attributes: {},
    className: '', hidden: false, value: '', listeners: {}, style: {setProperty() {}},
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
    remove() { this.parentNode.children = this.parentNode.children.filter(c => c !== this); },
    setAttribute(k,v) { this.attributes[k] = String(v); }, getAttribute(k) { return this.attributes[k]; },
    addEventListener(k,f) { this.listeners[k] = f; }, click() { this.listeners.click?.(); },
    querySelectorAll() { return this.children; }
  };
  n.classList = {add(c) { n.className += ' '+c; }, toggle(c) { n.className += ' '+c; }};
  Object.defineProperties(n, {
    textContent: {get() { return text+n.children.map(c=>c.textContent).join(''); }, set(v) { text=String(v); n.children=[]; }},
    tBodies: {get() { return n.children.filter(c=>c.tagName==='TBODY'); }},
    cells: {get() { return n.children.filter(c=>c.tagName==='TD'); }}
  });
  return n;
}
const ids = ['quota-accounts','quota-explorer','quota-search','quota-clear','quota-match-count','quota-signed-out'];
const nodes = Object.fromEntries(ids.map(id=>['#'+id,node(id==='quota-accounts'?'table':'div')]));
const buttons = ['all','claude','codex'].map(p=> {const b=node('button'); b.dataset.quotaProvider=p; return b;});
nodes['#quota-explorer'].children=buttons;
function descendants(n) {return [n,...n.children.flatMap(descendants)];}
global.window={location:{origin:'http://fixture.test',pathname:'/fixture',search:''},history:{replaceState(){}}};
global.document={readyState:'loading',body:node(),addEventListener(){},querySelector(s){return nodes[s]||descendants(nodes['#quota-accounts']).find(n=>'#'+n.id===s)||null;},
  querySelectorAll(){return [];},createElement:node,createTextNode(t){const n=node();n.textContent=t;return n;}};
eval(fs.readFileSync('web/app.js','utf8').replace('window.AgentMonitor = {','window.AgentMonitor = { renderQuotaAccounts, removeRenderedAccount, snapshot: () => renderedQuotaRateLimits,'));
const ui=window.AgentMonitor, table=nodes['#quota-accounts'];
function account(id,state='known',presence='in_use',machine='alpha') {
  return {account_id:id,account_label:id+'@example.test',account_state:state,account_plan:'pro',presence,
    machines:[machine],reading_from:machine,five_hour_used_pct:91,seven_day_used_pct:37,
    five_hour_resets_at:4102444800,seven_day_resets_at:4102444800,updated_at:'2026-01-01T00:00:00Z'};
}
const data={claude:{accounts:[account('alice'),account('past','known','remembered'),account('u1','unstamped','in_use','old-a'),account('u2','unstamped','in_use','old-b')],
  refresh_errors:[{machine:'alpha',reason:'fixture failure'}],signed_out_machines:['signedout']},
  codex:{accounts:[account('bob')],refresh_errors:[]}};
const original=JSON.stringify(data);
const rows=()=>table.tBodies.flatMap(b=>b.children).filter(r=>r.dataset.accountId);
const count=()=>nodes['#quota-match-count'].textContent;
const search=(s)=>{nodes['#quota-search'].value=s;nodes['#quota-search'].listeners.input();};
// A completed Web action must replace an older reading for this member only.
const past=account('shared','known','remembered');
past.account_label='one@example.test'; past.seven_day_used_pct=91; past.seven_day_resets_at=1;
const other={...past,account_label:'two@example.test'};
const otherWorkspace={...past,account_id:'other-workspace'};
const projection={codex:{accounts:[past,other,otherWorkspace],refresh_errors:[{machine:'alpha',reason:'fixture collection failure'}]}};
const frozen=JSON.stringify(projection);
const observed=new Date().toISOString(), future=Math.floor(Date.now()/1000)+6*86400;
const action={account_id:'shared',email:'ONE@example.test',operation:{after:{observed_at:observed,seven_day_used_pct:3,seven_day_resets_at:future}}};
ui.renderQuotaAccounts(projection);
ui.updateCodexQuotaReadings?.([action]);
assert(rows()[0].cells[4].textContent.includes('6 天后重置'), 'fresh action reset must reach quota table');
assert(rows()[0].cells[4].textContent.includes('3%'));
const originalButton=descendants(rows()[0]).find(n=>n.className.includes('codex-account-select'));
ui.updateCodexQuotaReadings([{...action,busy:true,operation:{...action.operation,stage:'login'}}]);
assert(descendants(table).includes(originalButton),'unchanged readings must preserve table controls during progress polling');
assert(rows()[0].cells[6].textContent.includes('本页查询'));
assert(rows()[0].cells[1].textContent.includes('机器记录'));
assert.equal(rows()[0].dataset.observedAt,past.updated_at,'deletion CAS stays on machine observation');
assert(rows()[1].cells[4].textContent.includes('91%'),'same workspace different member stays separate');
assert(rows()[2].cells[4].textContent.includes('91%'),'same email different workspace stays separate');
assert(rows()[1].cells[4].textContent.includes('上次重置时间已过'));
assert.equal(JSON.stringify(projection),frozen,'projection must not mutate machine input');
assert(table.textContent.includes('fixture collection failure'));
// A newer machine observation and an equal timestamp both beat Web observations.
for (const newer of [observed,'2099-01-01T00:00:00Z']) {
  ui.renderQuotaAccounts({codex:{accounts:[{...past,updated_at:newer,seven_day_used_pct:42}]}});
  assert(rows()[0].cells[4].textContent.includes('42%'));
  assert(!rows()[0].cells[6].textContent.includes('本页查询'));
}
ui.renderQuotaAccounts(projection);
ui.updateCodexQuotaReadings([ {...action,operation:{after:{observed_at:'invalid',seven_day_used_pct:8}},batch_result:action.operation} ]);
assert(rows()[0].cells[4].textContent.includes('3%'),'retained batch observation survives an unreadable current observation');
ui.updateCodexQuotaReadings([{...action,last_quota:action.operation.after,operation:{stage:'failed',quota_status:'not_read'}}]);
assert(rows()[0].cells[4].textContent.includes('3%'),'failed refresh must retain successful post-send reading');
assert(rows()[0].cells[6].textContent.includes('本页查询'));
assert.equal(ui.latestCodexQuotaReading({last_quota:action.operation.after}),action.operation.after,'cards share retained quota selection');
ui.updateCodexQuotaReadings([ {...action,operation:{after:{observed_at:observed,seven_day_used_pct:null,seven_day_resets_at:null}}} ]);
assert(rows()[0].cells[4].textContent.includes('重置时间未知'));
assert(!rows()[0].cells[4].textContent.includes('91%'),'unknown new reading never borrows old percentage');
ui.updateCodexQuotaReadings([]);
assert(rows()[0].cells[4].textContent.includes('91%'),'page cleanup can clear observations');
descendants(table).find(n=>n.className.includes('quota-past-toggle')).click();
assert(!document.querySelector('#quota-past-accounts').hidden);
ui.updateCodexQuotaReadings([action]);
assert(!document.querySelector('#quota-past-accounts').hidden,'new action observation preserves historical disclosure');
descendants(table).find(n=>n.className.includes('quota-past-toggle')).click();
ui.updateCodexQuotaReadings([]);
ui.renderQuotaAccounts(data);
assert(count().includes('匹配 5 / 5')); assert(count().includes('在用账号 2')); assert(count().includes('历史账号 1')); assert(count().includes('机器记录 2'));
assert(table.tBodies.some(b=>b.className==='quota-unknown'));
assert(table.tBodies.find(b=>b.className==='quota-history').hidden);
buttons[2].click();
assert.equal(rows().length,1); assert.equal(rows()[0].dataset.accountId,'bob');
assert(table.textContent.includes('fixture failure')); assert(!nodes['#quota-signed-out'].hidden);
assert(table.textContent.includes('登录 / 发消息')); assert(table.textContent.includes('37%'));
ui.renderQuotaAccounts(data); assert.equal(rows().length,1); assert.equal(buttons[2].attributes['aria-pressed'],'true');
search('nobody'); assert(table.textContent.includes('没有匹配的配额记录')); assert(table.textContent.includes('fixture failure'));
nodes['#quota-clear'].click(); assert.equal(nodes['#quota-search'].value,''); assert(count().includes('匹配 5 / 5'));
search('PRO alice'); assert.equal(rows().length,1); assert.equal(rows()[0].dataset.accountId,'alice');
search('old-b'); assert(count().includes('匹配 1 / 5')); assert(count().includes('在用账号 0')); assert(count().includes('机器记录 1'));
search('past'); assert(table.textContent.includes('移除')); assert.strictEqual(ui.snapshot(),data);
// The delete request can finish after the user hides its row with a filter.
search('bob'); assert.equal(rows().length,1);
ui.removeRenderedAccount('claude','past','2026-01-01T00:00:00Z');
nodes['#quota-clear'].click(); assert(count().includes('匹配 4 / 4')); assert(table.textContent.includes('bob@example.test')); assert(table.textContent.includes('alice@example.test'));
assert.equal(JSON.stringify(data),original);
// A client-side route return replaces controls while retaining module state.
search('bob');
nodes['#quota-explorer']=node();
const replacementButtons=['all','claude','codex'].map(p=>{const b=node('button');b.dataset.quotaProvider=p;return b;});
nodes['#quota-explorer'].children=replacementButtons;
nodes['#quota-search']=node(); nodes['#quota-clear']=node();
ui.renderQuotaAccounts(data);
assert.equal(nodes['#quota-search'].value,'bob');
assert.equal(rows().length,1);
replacementButtons[1].click(); assert.equal(rows().length,0);
nodes['#quota-clear'].click(); assert(count().includes('匹配 5 / 5'));
ui.renderQuotaAccounts({claude:{accounts:[],unavailable_reason:'fixture unavailable'},codex:{accounts:[]}});
assert(table.textContent.includes('fixture unavailable')); assert(!table.textContent.includes('没有匹配'));
replacementButtons[2].click();
assert(table.textContent.includes('fixture unavailable'));
console.log('quota explorer: filters, counters, notices, polling, full snapshot removal, unavailable passed');
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
