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
global.window={location:{origin:'http://fixture.test',pathname:'/fixture',search:''},history:{replaceState(){}}};
global.document={readyState:'loading',body:node(),addEventListener(){},querySelector(s){return nodes[s]||null;},
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
