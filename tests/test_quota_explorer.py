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
    append(...children) { children.forEach(c => this.appendChild(c)); },
    remove() { this.parentNode.children = this.parentNode.children.filter(c => c !== this); },
    setAttribute(k,v) { this.attributes[k] = String(v); }, getAttribute(k) { return this.attributes[k]; },
    addEventListener(k,f) { this.listeners[k] = f; }, click() { this.listeners.click?.(); },
    querySelectorAll(selector) {
      if (selector.startsWith('.quota-observation-details')) return descendants(this).filter(c =>
        c !== this && c.className === 'quota-observation-details' && (!selector.endsWith('[open]') || c.open));
      return this.children;
    }
  };
  n.classList = {add(c) { n.className += ' '+c; }, toggle(c) { n.className += ' '+c; }};
  Object.defineProperties(n, {
    textContent: {get() { return text+n.children.map(c=>c.textContent).join(''); }, set(v) { text=String(v); n.children=[]; }},
    tBodies: {get() { return n.children.filter(c=>c.tagName==='TBODY'); }},
    cells: {get() { return n.children.filter(c=>c.tagName==='TD'); }}
  });
  return n;
}
const ids = ['quota-accounts','quota-explorer','quota-search','quota-clear','quota-window','quota-sort','quota-match-count','quota-signed-out','quota-machine-records','quota-machine-notice'];
const nodes = Object.fromEntries(ids.map(id=>['#'+id,node(id==='quota-accounts'?'table':'div')]));
const buttons = ['all','claude','codex'].map(p=> {const b=node('button'); b.dataset.quotaProvider=p; return b;});
nodes['#quota-explorer'].children=buttons;
function descendants(n) {return [n,...n.children.flatMap(descendants)];}
global.window={location:{origin:'http://fixture.test',pathname:'/fixture',search:''},history:{replaceState(){}}};
global.document={readyState:'loading',body:node(),addEventListener(){},querySelector(s){return nodes[s]||descendants(nodes['#quota-accounts']).find(n=>'#'+n.id===s)||null;},
  querySelectorAll(){return [];},createElement:node,createTextNode(t){const n=node();n.textContent=t;return n;}};
require('./web/app.js')();
const ui=window.AgentMonitor, table=nodes['#quota-accounts'];
function account(id,state='known',presence='in_use',machine='alpha') {
  return {account_id:id,account_label:id+'@example.test',account_state:state,account_plan:'pro',presence,
    machines:[machine],reading_from:machine,five_hour_used_pct:91,seven_day_used_pct:37,
    five_hour_resets_at:4102444800,seven_day_resets_at:4102444800,updated_at:'2026-01-01T00:00:00Z'};
}
// Fresh passive readings must not be called stale because active queries pause.
const passive={...account('passive'),updated_at:new Date().toISOString(),
  quota_observation:{source:'codex_session',observed_at:new Date().toISOString()}};
ui.renderQuotaAccounts({codex:{accounts:[passive],refresh_errors:[{machine:'alpha',
  reason:'Codex 配额请求失败，请检查网络',query_state:'paused',account_id:'passive',account_label:'passive@example.test',pause:{id:'a'.repeat(32),at:1}}]}});
assert(table.textContent.includes('当前读数来自会话日志'));
assert(table.textContent.includes('主动查询已暂停'));
assert(!table.textContent.includes('更新延迟'));
const recoverButton=descendants(table).find(n=>n.tagName==='BUTTON' && n.textContent==='查看当前状态与恢复');
assert(recoverButton, 'read-only recovery entry is available');
// Confirmed lack of a subscription must not look like available current quota.
const unsubscribed = {...account('unsubscribed'), quota_status:'no_subscription',
  quota_status_reason:'无有效 Claude Code 订阅（用户已确认）', plan_verification:'unverified',
  five_hour_used_pct:null, seven_day_used_pct:null, updated_at:null,
  last_observation:{five_hour_used_pct:0,seven_day_used_pct:7,observed_at:null,source:'legacy_unknown'}};
ui.renderQuotaAccounts({claude:{accounts:[unsubscribed]}});
const unsubscribedRow=table.tBodies.flatMap(b=>b.children).find(r=>r.dataset.accountId==='unsubscribed');
assert(unsubscribedRow.cells[2].textContent.includes('无有效订阅'));
assert.equal(unsubscribedRow.cells[4].textContent,'不适用');
assert(unsubscribedRow.cells[6].textContent.includes('历史配额'));
assert(unsubscribedRow.cells[6].textContent.includes('7%'));
assert(!unsubscribedRow.cells[6].textContent.includes('分钟前'));
ui.renderQuotaAccounts({claude:{accounts:[{...unsubscribed,account_id:null,account_label:null,account_state:'signed_out',
  last_observation:{five_hour_used_pct:0,seven_day_used_pct:7,five_hour_resets_at:1,seven_day_resets_at:2,observed_at:null}}]}});
assert(nodes['#quota-machine-records'].textContent.includes('已用 7%'));
assert(nodes['#quota-machine-records'].textContent.includes('观测时间未知'));
assert(!nodes['#quota-machine-records'].children[0].textContent.includes('余量偏低'));
// An account table must not turn a machine observation into an account.
for (const state of ['unstamped', 'signed_out', 'known']) {
  for (const size of [1,2]) {
    const samples=['old-a','old-b'].slice(0,size).map((machine,index)=>({...account('known-'+index,state,'in_use',machine),
      ...(state==='known'?{}:{account_id:null,account_label:null}), five_hour_used_pct:96,seven_day_used_pct:0}));
    const readings={codex:{accounts:samples,signed_out_machines:state==='signed_out'?['old-a']:[]}};
    const original=JSON.stringify(readings);
    ui.renderQuotaAccounts(readings);
    const machineRecords=nodes['#quota-machine-records'];
    if (state === 'known') {
      assert.equal(table.tBodies.flatMap(b=>b.children).filter(r=>r.dataset.accountId).length,size);
      assert(machineRecords.hidden);
      assert(nodes['#quota-machine-notice'].hidden);
      let details=table.querySelectorAll('.quota-observation-details');
      details[0].open=true;
      ui.renderQuotaAccounts(readings);
      details=table.querySelectorAll('.quota-observation-details');
      assert.deepEqual(details.map(d=>d.open),size===1?[true]:[true,false]);
    } else {
      assert(!table.textContent.includes('old-a'),'unidentified machine must leave account comparison');
      assert(!machineRecords.hidden);
      assert(machineRecords.textContent.includes('old-a'));
      assert(machineRecords.textContent.includes(state==='signed_out'?'未登录':'更新'));
      assert(machineRecords.textContent.includes('0%'),'zero observation is retained');
      assert(machineRecords.textContent.includes(ui.formatDate(samples[0].updated_at)));
      const summary=machineRecords.children[0];
      for(const value of ['96%','5h','old-a','即将用尽']) assert(summary.textContent.includes(value),value);
      machineRecords.open=true;
      ui.renderQuotaAccounts(readings);
      assert(machineRecords.open,'poll must preserve disclosure');
      assert(!nodes['#quota-signed-out'].textContent.includes('账号配额保留最后一次读数'));
      const missing={...samples[0],five_hour_used_pct:null,five_hour_resets_at:null};
      ui.renderQuotaAccounts({codex:{accounts:[missing]}});
      assert(machineRecords.textContent.includes('用量未知'));
      assert(!machineRecords.textContent.includes('天后重置'),'raw history must not acquire a live countdown');
    }
    if (state !== 'known') {
      ui.renderQuotaAccounts(readings);
      assert(!nodes['#quota-machine-notice'].hidden);
      assert(nodes['#quota-machine-notice'].textContent.includes('96%'));
      const normal = JSON.parse(JSON.stringify(readings));
      normal.codex.accounts.forEach(account => {account.five_hour_used_pct=0;account.seven_day_used_pct=null;});
      ui.renderQuotaAccounts(normal);
      assert(nodes['#quota-machine-notice'].hidden, 'normal machine evidence belongs on diagnostics');
      assert(nodes['#quota-machine-records'].textContent.includes('用量未知'));
    }
    assert.equal(JSON.stringify(readings),original,'renderer must not change snapshot');
  }
}
// Malformed remote timestamps must not interrupt other account rows.
for (const reset of [10000000000000, Number.MAX_VALUE]) {
  const damaged={...account(null,'signed_out','in_use','invalid-clock'),account_label:null,
    seven_day_resets_at:reset};
  ui.renderQuotaAccounts({codex:{accounts:[damaged,account('retained','known','remembered')]}});
  assert(nodes['#quota-machine-records'].textContent.includes('重置时间无效'));
  assert(table.textContent.includes('retained@example.test'),'bad machine timestamp must not interrupt history rendering');
}
const data={claude:{accounts:[account('alice'),account('past','known','remembered'),account('u1','unstamped','in_use','old-a'),account('u2','unstamped','in_use','old-b')],
  refresh_errors:[{machine:'alpha',reason:'fixture failure'}],signed_out_machines:['signedout']},
  codex:{accounts:[account('bob')],refresh_errors:[]}};
// Reader tasks: identity, machine relation, and age of the displayed observation.
const fixedNow=Date.parse('2026-10-16T12:00:00Z'), realNow=Date.now;
Date.now=()=>fixedNow;
for (const [machines, source, expected] of [
  [['alpha'], 'alpha', 'alpha'],
  [['alpha','beta'], 'beta', 'alpha · beta（配额来源）'],
  [['alpha','alpha','beta'], 'beta', 'alpha · beta（配额来源）'],
  [['alpha','alpha'], 'alpha', 'alpha'],
  [['alpha'], 'beta', 'alpha · 配额来源：beta'],
  [[], 'beta', '— · 配额来源：beta'],
  [['alpha'], null, 'alpha'],
]) {
  const sample={...account('source-context'), machines, reading_from:source, updated_at:'2026-10-16T11:57:00Z'};
  ui.renderQuotaAccounts({codex:{accounts:[sample]}});
  const row=table.tBodies.flatMap(b=>b.children).find(r=>r.dataset.accountId==='source-context');
  assert(!row.cells[0].textContent.includes('观测'), 'quota time must leave identity');
  assert.equal(row.cells[5].textContent,expected,JSON.stringify({machines,source}));
  assert.equal(row.cells[6].dataset.label,'配额更新');
  assert(descendants(row.cells[6]).some(n=>n.tagName==='SUMMARY'&&n.textContent==='3 分钟前'));
  assert(!row.cells[6].textContent.includes('配额来源未知'));
  assert.equal(descendants(row.cells[6]).filter(n=>n.tagName==='DETAILS').some(n=>n.textContent.includes('未取得读数来源机器')),source===null);
  assert(row.cells[6].textContent.includes(ui.formatDate(sample.updated_at)));
}
for (const [stamp,expected] of [
  ['2026-10-16T11:59:40Z','刚刚'], ['2026-10-16T10:00:00Z','2 小时前'],
  ['2026-10-14T12:00:00Z','2 天前'], [null,'时间未知'], ['', '时间未知'],
  ['invalid','时间未知'], ['2026-10-16T13:00:00Z','时间超前'],
]) {
  ui.renderQuotaAccounts({codex:{accounts:[{...account('age'),updated_at:stamp}]}});
  const row=table.tBodies.flatMap(b=>b.children).find(r=>r.dataset.accountId==='age');
  assert(descendants(row.cells[6]).some(n=>n.tagName==='SUMMARY'&&n.textContent===expected),expected);
}
// A signed-out reading source needs a short status, with recovery in details.
for (const [provider,label] of [['claude','Claude'],['codex','Codex']]) {
  for (const kind of ['signed-out-source','healthy-source','other-machine','remembered','account-action']) {
    const sample=account('login-context','known',kind==='remembered'?'remembered':'in_use');
    if (kind==='account-action') sample.quota_source='account_action';
    const signedOut=kind==='healthy-source'?[]:[kind==='other-machine'?'beta':'alpha'];
    ui.renderQuotaAccounts({[provider]:{accounts:[sample],signed_out_machines:signedOut}});
    const row=table.tBodies.flatMap(b=>b.children).find(r=>r.dataset.accountId==='login-context');
    const cell=row.cells[6], detail=cell.children.find(n=>n.tagName==='DETAILS');
    const inline=cell.children.filter(n=>n.tagName!=='DETAILS').map(n=>n.textContent).join('');
    const affected=kind==='signed-out-source';
    assert(!nodes['#quota-signed-out'].textContent.includes('登录已失效'));
    assert.equal(inline.includes('来源机器未登录'),affected,provider+': '+kind);
    assert(!cell.textContent.includes('登录已失效'));
    assert(!inline.includes('保留旧值'));
    if (affected) {
      assert.equal(inline,'来源机器未登录');
      for (const text of ['alpha',label,'最后一次','登录此账号','更新统计与配额'])
        assert(detail.textContent.includes(text),text);
      assert.equal(sample.five_hour_used_pct,91,'copy must not alter the reading');
    }
  }
}
Date.now=realNow;
const original=JSON.stringify(data);
const rows=()=>table.tBodies.flatMap(b=>b.children).filter(r=>r.dataset.accountId);
const count=()=>nodes['#quota-match-count'].textContent;
const search=(s)=>{nodes['#quota-search'].value=s;nodes['#quota-search'].listeners.input();};
// Identity stays concise while profile search and reading provenance remain available.
for (const [source, label] of [['codex_session','会话日志'], ['active_query','主动查询'], [null,'旧记录未注明']]) {
  const sample={...account('member'),account_profile:'work-profile',quota_observation:{source}};
  const before=JSON.stringify(sample);
  ui.renderQuotaAccounts({codex:{accounts:[sample]}});
  assert.equal(rows()[0].cells[0].textContent,'member@example.test');
  assert(rows()[0].cells[6].textContent.includes('读数来源：'+label));
  assert(rows()[0].cells[6].textContent.includes('配置名：work-profile'));
  search('work-profile');
  assert.equal(rows().length,1,'profile remains searchable independently of account label');
  search('absent-profile');
  assert.equal(rows().length,0);
  search('');
  assert.equal(JSON.stringify(sample),before);
}
// A completed Web action must replace an older reading for this member only.
const past=account('shared','known','remembered');
past.machines=[]; past.reading_from=null;
past.account_label='one@example.test'; past.seven_day_used_pct=91; past.seven_day_resets_at=1;
past.quota_observation={source:'codex_session'};
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
const originalRow=rows()[0];
ui.updateCodexQuotaReadings([{...action,busy:true,operation:{...action.operation,stage:'login'}}]);
assert(rows().includes(originalRow),'unchanged readings must preserve table rows during progress polling');
assert(rows()[0].cells[6].textContent.includes('来自账号操作中的配额查询'));
assert(!rows()[0].cells[0].textContent.includes('本页查询'));
assert(!rows()[0].cells[0].textContent.includes('最后观测值，不代表当前状态'), 'fresh action reading must not be labelled as a machine-history reading');
assert(!rows()[0].cells[2].textContent.includes('机器记录'));
const observationDetails=descendants(rows()[0].cells[6]).find(n=>n.tagName==='DETAILS');
assert(observationDetails.textContent.includes('来自账号操作中的配额查询'));
assert(observationDetails.textContent.includes('读数来源：账号操作中的配额查询'));
assert(!observationDetails.textContent.includes('读数来源：会话日志'));
assert(observationDetails.textContent.includes('套餐'));
assert.equal(rows()[0].cells[5].textContent,'—');
assert(observationDetails.textContent.includes('当前未在已纳入的机器上使用'));
assert(observationDetails.textContent.includes('独立保存的登录状态见账号操作'));
assert(!rows()[0].cells[6].children.filter(n=>n.tagName!=='DETAILS').some(n=>n.textContent.includes('查询')),
  'query provenance stays in details, not a repeated row note');
assert(!rows()[0].cells[1].textContent.includes('历史'),'provider cell describes only provider');
assert(!rows()[0].cells[6].textContent.includes('读数较旧'));
Date.now=()=>Date.parse(observed)+3*86400000;
ui.renderQuotaAccounts(projection);
assert(!rows()[0].cells[6].textContent.includes('读数较旧'));
assert(descendants(rows()[0].cells[6]).some(n=>n.tagName==='SUMMARY'&&n.textContent.includes('天前')), 'observation age remains visible');
Date.now=realNow;
ui.renderQuotaAccounts(projection);
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
  assert(!rows()[0].cells[6].textContent.includes('来自账号操作中的配额查询'));
  assert(!rows()[0].cells[6].textContent.includes('历史读数，不代表当前状态'));
  assert(descendants(rows()[0].cells[6]).some(n=>n.tagName==='DETAILS'&&n.textContent.includes('历史保存未保留来源机器')));
  assert(rows()[0].cells[6].textContent.includes('当前未在已纳入的机器上使用'));
}
ui.renderQuotaAccounts(projection);
ui.updateCodexQuotaReadings([ {...action,operation:{after:{observed_at:'invalid',seven_day_used_pct:8}},batch_result:action.operation} ]);
assert(rows()[0].cells[4].textContent.includes('3%'),'retained batch observation survives an unreadable current observation');
ui.updateCodexQuotaReadings([{...action,last_quota:action.operation.after,operation:{stage:'failed',quota_status:'not_read'}}]);
assert(rows()[0].cells[4].textContent.includes('3%'),'failed refresh must retain successful post-send reading');
assert(rows()[0].cells[6].textContent.includes('来自账号操作中的配额查询'));
assert.equal(ui.latestCodexQuotaReading({last_quota:action.operation.after}),action.operation.after,'cards share retained quota selection');
const before={observed_at:new Date(Date.parse(observed)+1000).toISOString(),seven_day_used_pct:100,seven_day_resets_at:null};
for (const source of ['operation','batch_result']) {
  const candidate={...action,last_quota:action.operation.after,[source]:{stage:'failed',before}};
  ui.updateCodexQuotaReadings([candidate]);
  assert.equal(ui.latestCodexQuotaReading(candidate),before,'cards select newer before observation');
  assert(rows()[0].cells[4].textContent.includes('100%'),'before reaches quota table');
  assert(rows()[0].cells[4].textContent.includes('重置时间未知'),'new null reset is preserved');
}
ui.updateCodexQuotaReadings([ {...action,operation:{after:{observed_at:observed,seven_day_used_pct:null,seven_day_resets_at:null}}} ]);
assert.equal(rows()[0].cells[4].textContent,'用量与重置时间未知');
assert.equal(descendants(rows()[0]).filter(n=>n.className.includes('quota-account-observation')).length,0);
assert(rows()[0].cells[6].textContent.includes(ui.formatDate(observed)), 'exact observation time is available in update details');
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
assert(count().includes('匹配 3 / 3')); assert(count().includes('机器关联 2')); assert(count().includes('历史账号 1')); assert(!count().includes('机器记录'));
assert(!table.tBodies.some(b=>b.className==='quota-unknown')); assert(nodes['#quota-machine-records'].textContent.includes('old-b'));
assert(table.tBodies.find(b=>b.className==='quota-history').hidden);
buttons[2].click();
assert.equal(rows().length,1); assert.equal(rows()[0].dataset.accountId,'bob');
assert(table.textContent.includes('fixture failure')); assert(nodes['#quota-signed-out'].hidden, 'unrelated signed-out machines belong in diagnostics');
assert(!table.textContent.includes('账号操作')); assert(!table.textContent.includes('查询配额')); assert(table.textContent.includes('37%'));
ui.renderQuotaAccounts(data); assert.equal(rows().length,1); assert.equal(buttons[2].attributes['aria-pressed'],'true');
search('nobody'); assert(table.textContent.includes('没有匹配的配额记录')); assert(table.textContent.includes('fixture failure'));
nodes['#quota-clear'].click(); assert.equal(nodes['#quota-search'].value,''); assert(count().includes('匹配 3 / 3'));
search('PRO alice'); assert.equal(rows().length,1); assert.equal(rows()[0].dataset.accountId,'alice');
search('old-b'); assert(count().includes('匹配 0 / 3')); assert(count().includes('机器关联 0')); assert(nodes['#quota-machine-records'].textContent.includes('old-b'));
search('past'); assert(table.textContent.includes('移除')); assert.strictEqual(ui.quotaSnapshot(),data);
// The delete request can finish after the user hides its row with a filter.
search('bob'); assert.equal(rows().length,1);
nodes['#quota-window'].value='seven_day'; nodes['#quota-window'].listeners.change();
nodes['#quota-sort'].value='remaining_desc'; nodes['#quota-sort'].listeners.change();
assert.equal(nodes['#quota-clear'].hidden,false);
ui.removeRenderedAccount('claude','past','2026-01-01T00:00:00Z');
nodes['#quota-clear'].click(); assert(count().includes('匹配 2 / 2')); assert(table.textContent.includes('bob@example.test')); assert(table.textContent.includes('alice@example.test'));
assert.equal(nodes['#quota-window'].value,'five_hour','reset restores the contracted default window');
assert.equal(nodes['#quota-sort'].value,'default','reset restores the contracted default order');
assert.equal(nodes['#quota-clear'].hidden,true,'no active filter needs no reset action');
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
nodes['#quota-clear'].click(); assert(count().includes('匹配 3 / 3'));
ui.renderQuotaAccounts({claude:{accounts:[],unavailable_reason:'fixture unavailable'},codex:{accounts:[]}});
assert(table.textContent.includes('fixture unavailable')); assert(!table.textContent.includes('没有匹配'));
replacementButtons[2].click();
assert(table.textContent.includes('fixture unavailable'));
console.log('quota explorer: filters, counters, notices, polling, full snapshot removal, unavailable passed');
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
