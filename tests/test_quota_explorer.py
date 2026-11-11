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
    querySelector(selector) { return selector === '.quota-sort-icon' ? this.children[0] : null; },
    setAttribute(k,v) { this.attributes[k] = String(v); }, getAttribute(k) { return this.attributes[k]; },
    addEventListener(k,f) { this.listeners[k] = f; }, click() { this.listeners.click?.(); },
    focus() { document.activeElement=this; this.parentNode?.listeners.focusin?.(); },
    querySelectorAll(selector) {
      if (selector === 'button[data-quota-sort]') return descendants(this).filter(c => c.tagName === 'BUTTON' && c.dataset.quotaSort);
      if (selector === '.quota-reset-credit-count') return descendants(this).filter(c => c.className === 'quota-reset-credit-count');
      if (selector.startsWith('.quota-observation-details')) return descendants(this).filter(c =>
        c !== this && c.className.split(/\s+/).includes('quota-observation-details') && (!selector.endsWith('[open]') || c.open));
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
const ids = ['quota-accounts','quota-explorer','quota-search','quota-clear','quota-window','quota-window-control','quota-sort','quota-match-count','quota-signed-out','quota-machine-records','quota-machine-notice'];
const nodes = Object.fromEntries(ids.map(id=>['#'+id,node(id==='quota-accounts'?'table':'div')]));
const buttons = ['all','claude','codex'].map(p=> {const b=node('button'); b.dataset.quotaProvider=p; return b;});
nodes['#quota-explorer'].children=buttons;
function descendants(n) {return [n,...n.children.flatMap(descendants)];}
global.window={location:{origin:'http://fixture.test',pathname:'/fixture',search:''},history:{replaceState(){}}};
global.document={readyState:'loading',body:node(),addEventListener(){},querySelector(s){return nodes[s]||descendants(nodes['#quota-accounts']).find(n=>'#'+n.id===s)||null;},
  querySelectorAll(){return [];},createElement:node,createTextNode(t){const n=node();n.textContent=t;return n;}};
require('./web/app.js')();
const ui=window.AgentMonitor, table=nodes['#quota-accounts'];
const head=node('thead');
for (const match of fs.readFileSync('./web/index.html','utf8').matchAll(/<button[^>]*data-quota-sort="([^"]+)"(?: data-quota-window="([^"]+)")?[^>]*>/g)) {
  const th=node('th'), button=node('button');
  button.dataset.quotaSort=match[1];
  if(match[2]) button.dataset.quotaWindow=match[2];
  button.appendChild(node('span'));
  th.appendChild(button); head.appendChild(th);
}
table.appendChild(head);
const headers=head.children.map(th=>th.children[0]);
assert.equal(headers.length,5);
function account(id,state='known',presence='in_use',machine='alpha') {
  return {account_id:id,account_label:id+'@example.test',account_state:state,account_plan:'pro',presence,
    machines:[machine],reading_from:machine,five_hour_used_pct:91,seven_day_used_pct:37,
    five_hour_resets_at:4102444800,seven_day_resets_at:4102444800,updated_at:'2026-01-01T00:00:00Z'};
}
// The selected action reading carries its own plan to the final cell.
for (const [value, label, detail] of [
  [null, '未知', '尚未取得 reset 额度记录'],
  [{available_count:0,credits:[]}, '0 次', 'reset 额度：0 次'],
  [{available_count:3,credits:[{expires_at:1900000000},{expires_at:null},{}]}, '3 次', '到期时间未知'],
  [{available_count:3,credits:[{expires_at:null}]}, '3 次', '仅返回 1 / 3 笔明细'],
  [{available_count:2,credits:null}, '2 次', '服务端未返回到期明细'],
]) {
  ui.renderQuotaAccounts({codex:{accounts:[{...account('reset-credit'),reset_credits:value}]}});
  const row=table.tBodies.flatMap(b=>b.children).find(r=>r.dataset.accountId==='reset-credit');
  const cell=row.cells[7],button=cell.children[0],popup=cell.children[1];
  assert.equal(button.textContent,label);
  assert(popup.textContent.includes(detail));
  assert.equal(popup.hidden,true);
  button.click();
  assert.equal(popup.hidden,false,'touch/click fallback opens details');
  cell.listeners.keydown({key:'Escape'});
  assert.equal(popup.hidden,true);
}
const closedCreditRow=table.tBodies.flatMap(b=>b.children).find(r=>r.dataset.accountId==='reset-credit');
const closedCreditButton=closedCreditRow.cells[7].children[0];
closedCreditButton.focus();
assert.equal(closedCreditRow.cells[7].children[1].hidden,false);
closedCreditRow.cells[7].listeners.keydown({key:'Escape'});
ui.renderQuotaAccounts({codex:{accounts:[account('reset-credit')]}});
const restoredCreditRow=table.tBodies.flatMap(b=>b.children).find(r=>r.dataset.accountId==='reset-credit');
assert.equal(document.activeElement,restoredCreditRow.cells[7].children[0]);
assert.equal(restoredCreditRow.cells[7].children[1].hidden,true,'dismissed focused detail stays closed after redraw');
document.activeElement=null;
const missingPlan={...account('plan'), account_plan:null, reading_plan:null, credential_plan:null, plan_verification:'unverified'};
ui.updateCodexQuotaReadings([{account_id:'plan',email:'plan@example.test',last_quota:{observed_at:'2026-02-01T00:00:00Z',plan:'promax',seven_day_used_pct:42}}]);
const displayed=ui.quotaDisplayReading({key:'codex'},missingPlan);
assert.equal(displayed.account_plan,'promax');
assert.deepEqual(displayed.quota_observation,{source:'active_query',observed_at:'2026-02-01T00:00:00Z'});
assert.equal(ui.quotaPlanCell(displayed,{key:'codex'}).children[0].textContent,'Pro (Max)');
ui.renderQuotaAccounts({codex:{accounts:[missingPlan]}});
assert(table.textContent.includes('Pro (Max)'));
assert(!descendants(table).some(n => n.className.includes('quota-plan-details')));
const planDetails = descendants(table).find(n => n.className === 'quota-observation-details');
assert.equal(descendants(table).filter(n => n.className === 'quota-observation-details').length, 1);
planDetails.open = true;
ui.renderQuotaAccounts({codex:{accounts:[missingPlan]}});
assert(descendants(table).find(n => n.dataset.observationKey === planDetails.dataset.observationKey).open);
assert(descendants(table).find(n => n.className === 'quota-observation-details').open);
assert(!table.textContent.includes('套餐未核实'));
assert(table.textContent.includes('配额与套餐来自此次账号操作查询'));
ui.quotaFilter.search='Pro (Max)';
assert(ui.quotaMatches({key:'codex'},missingPlan));
ui.quotaFilter.search='';
assert.equal(ui.quotaDisplayReading({key:'codex'},{...missingPlan,updated_at:'2026-03-01T00:00:00Z'}).account_plan,null);
assert.equal(ui.quotaDisplayReading({key:'codex'},{...missingPlan,account_label:'other@example.test'}).account_plan,null);
ui.updateCodexQuotaReadings([]);

// A disabled profile is folded without changing persisted presence/removal rights.
{
const disabled = account('disabled'), active = {...account('disabled'),account_label:'other@example.test'};
const past = account('past','known','remembered');
const disabledProfile = {id:'disabled-profile',account_id:'disabled',email:'disabled@example.test',disabled:false};
ui.updateCodexQuotaReadings([disabledProfile]);
ui.renderQuotaAccounts({codex:{accounts:[disabled,active,past]}});
ui.updateCodexQuotaReadings([{...disabledProfile,disabled:true}]);
let folded = table.tBodies.find(b=>b.id==='quota-past-accounts');
assert(folded.hidden);
assert.equal(folded.children.length,2);
const disabledRow=folded.children.find(r=>r.dataset.accountId==='disabled');
assert.equal(disabledRow.dataset.presence,'in_use');
assert(descendants(disabledRow).some(n=>n.textContent==='已停用' && n.className==='status-pill bad'));
assert(!descendants(disabledRow).some(n=>n.textContent==='移除' || n.textContent==='刷新配额'));
assert(table.tBodies.filter(b=>b!==folded).flatMap(b=>b.children).some(r=>r.dataset.accountLabel===JSON.stringify(active.account_label)));
const foldToggle=descendants(table).find(n=>n.tagName==='BUTTON' && n.attributes['aria-controls']==='quota-past-accounts');
assert.match(foldToggle.textContent,/已停用与历史账号（2）/);
foldToggle.click();
ui.updateCodexQuotaReadings([{...disabledProfile,disabled:false}]);
folded=table.tBodies.find(b=>b.id==='quota-past-accounts');
assert(!folded.hidden,'status changes preserve the disclosure');
assert.equal(folded.children.length,1);
assert(!table.textContent.includes('已停用'));
ui.updateCodexQuotaReadings([]);
}

// Restore expansion even when disabled profiles arrive after the overview.
{
const row=account('late-disabled');
const profile={id:'late-profile',account_id:row.account_id,email:row.account_label,disabled:true};
for (const profilesFirst of [false,true]) {
  table.children=[head];
  ui.resetQuotaReadings({rememberedExpanded:true});
  if (profilesFirst) ui.updateCodexQuotaReadings([profile]);
  ui.renderQuotaAccounts({codex:{accounts:[row]}});
  if (!profilesFirst) ui.updateCodexQuotaReadings([profile]);
  assert.equal(table.tBodies.find(b=>b.id==='quota-past-accounts').hidden,false);
  descendants(table).find(n=>n.className.includes('quota-past-toggle')).click();
  ui.renderQuotaAccounts({codex:{accounts:[row]}});
  assert.equal(table.tBodies.find(b=>b.id==='quota-past-accounts').hidden,true);
}
ui.resetQuotaReadings();
table.children=[head];
}

// Sorting uses the selected window and compares providers within each group.
{
const now=1000, provider={key:'codex'};
const samples=[['full',100,1001],['unknown',null,1002],['later',0,1200],['soon',90,1100],
  ['tie-more',20,1100],['expired',0,999],['invalid',101,1003],['no-reset',0,null]]
  .map(([id,used,reset])=>({...account(id),five_hour_used_pct:used,five_hour_resets_at:reset}));
ui.quotaFilter.sort='reset';
assert.deepEqual(ui.sortQuotaAccounts(provider,samples,now).map(a=>a.account_id),
  ['full','unknown','invalid','soon','tie-more','later','expired','no-reset']);
ui.quotaFilter.sort='reset_desc';
assert.deepEqual(ui.sortQuotaAccounts(provider,samples,now).map(a=>a.account_id),
  ['later','soon','tie-more','invalid','unknown','full','expired','no-reset']);
ui.quotaFilter.window='seven_day';
assert.deepEqual(ui.sortQuotaAccounts(provider,[{...account('a'),seven_day_used_pct:100,seven_day_resets_at:1001},
  {...account('b'),seven_day_used_pct:0,seven_day_resets_at:1100}],now).map(a=>a.account_id),['b','a']);
ui.quotaFilter.window='five_hour';
ui.quotaFilter.sort='remaining_desc';
ui.renderQuotaAccounts({claude:{accounts:[{...account('claude'),five_hour_used_pct:80}]},
  codex:{accounts:[{...account('codex'),five_hour_used_pct:20}]}});
assert.deepEqual(table.tBodies.flatMap(b=>b.children).filter(r=>r.dataset.accountId).map(r=>r.dataset.accountId),['codex','claude']);
headers[0].click();
assert.equal(ui.quotaFilter.sort,'remaining');
assert.equal(headers[0].parentNode.getAttribute('aria-sort'),'descending');
assert.equal(headers[0].children[0].textContent,'↓');
assert.deepEqual(table.tBodies.flatMap(b=>b.children).filter(r=>r.dataset.accountId).map(r=>r.dataset.accountId),['claude','codex']);
headers[3].click();
assert.equal(ui.quotaFilter.window,'seven_day');
assert.equal(ui.quotaFilter.sort,'reset');
assert.equal(headers[0].parentNode.getAttribute('aria-sort'),'none');
assert.equal(headers[3].parentNode.getAttribute('aria-sort'),'ascending');
headers[3].click();
assert.equal(ui.quotaFilter.sort,'reset_desc');
assert.equal(nodes['#quota-sort'].value,'reset_desc');
assert.equal(nodes['#quota-window'].value,'seven_day');
headers[4].click();
assert.equal(ui.quotaFilter.sort,'observed');
headers[4].click();
assert.equal(ui.quotaFilter.sort,'observed_asc');
nodes['#quota-clear'].click();
assert(headers.every(b=>b.parentNode.getAttribute('aria-sort')==='none'));
}

// Current state must supersede historical collection failures without touching quota.
const currentAccount = {...account('current'), machines:['alpha','beta'], reading_from:'beta',
  updated_at:new Date().toISOString(), seven_day_used_pct:100};
const currentSnapshot = {codex:{accounts:[currentAccount],refresh_errors:['alpha','beta'].map(machine => ({
  machine, account_id:'current',account_label:'current@example.test',query_state:'paused',reason:'old pause',pause:{id:'old'}}))}};
ui.updateCodexQuotaReadings([{id:'profile-current',account_id:'current',email:'current@example.test',quota_query_allowed:true,restrictions:{quota:{}}}]);
ui.renderQuotaAccounts(currentSnapshot);
ui.updateQuotaMachineState('alpha', {machine:'alpha',observed_at:new Date().toISOString(),pause:{id:'still-held'},active:null});
ui.updateQuotaMachineState('beta', {machine:'beta',observed_at:new Date().toISOString(),pause:null,active:null});
let currentRow = () => table.tBodies.flatMap(b=>b.children).find(r=>r.dataset.accountId==='current');
assert(!currentRow().textContent.includes('发送已暂停'));
assert.equal(descendants(currentRow()).find(n=>n.tagName==='BUTTON' && n.textContent==='刷新配额').disabled,false);
assert(!currentRow().textContent.includes('额度已用尽'));
// A quota observation alone must not become a sending restriction.
ui.updateCodexQuotaReadings([{id:'profile-current',account_id:'current',email:'current@example.test',quota_query_allowed:true,restrictions:{}}]);
assert(currentRow().textContent.includes('100%'));
assert(!currentRow().textContent.includes('发送已暂停'));
assert(!descendants(currentRow()).some(n=>n.className==='quota-scope' && n.textContent==='可查询配额'));
assert.equal(descendants(currentRow()).find(n=>n.tagName==='BUTTON' && n.textContent==='刷新配额').disabled,false);
ui.updateCodexQuotaReadings([{id:'profile-current',account_id:'current',email:'current@example.test',quota_query_allowed:true,restrictions:{quota:{}}}]);
assert(currentRow().textContent.includes('alpha 自动查询已暂停'));
assert(!currentRow().textContent.includes('beta 自动查询已暂停'));
assert(currentRow().textContent.includes('历史采集'));
assert(currentRow().textContent.includes('100%'));
assert.equal(descendants(currentRow()).find(n=>n.tagName==='BUTTON' && n.textContent==='刷新配额').disabled,false);
ui.updateQuotaMachineState('alpha', {machine:'alpha',observed_at:new Date().toISOString(),pause:null,active:null});
assert(!currentRow().textContent.includes('自动查询已暂停'));
ui.updateQuotaMachineState('beta', null);
assert(currentRow().textContent.includes('beta 暂停状态未核实'));
ui.updateCodexQuotaReadings([{id:'profile-current',account_id:'current',email:'current@example.test',quota_query_allowed:false,restrictions:{authentication:{}}}]);
assert.equal(descendants(currentRow()).find(n=>n.tagName==='BUTTON' && n.textContent==='刷新配额').disabled,true);
assert(!currentRow().textContent.includes('查询受限'));
ui.updateCodexQuotaReadings(null);
assert(currentRow().textContent.includes('查询权限未核实'));
ui.resetQuotaReadings();
ui.updateQuotaMachineState('alpha', {machine:'alpha',observed_at:new Date().toISOString(),pause:{id:'current'},active:null});

// Fresh passive readings must not be called stale because active queries pause.
const passive={...account('passive'),updated_at:new Date().toISOString(),
  quota_observation:{source:'codex_session',observed_at:new Date().toISOString()}};
ui.renderQuotaAccounts({codex:{accounts:[passive],refresh_errors:[{machine:'alpha',
  reason:'Codex 配额请求失败，请检查网络',query_state:'paused',account_id:'passive',account_label:'passive@example.test',pause:{id:'a'.repeat(32),at:1}}]}});
assert(table.textContent.includes('读数来源：会话日志'));
assert(table.textContent.includes('自动查询已暂停'));
assert(table.textContent.includes('alpha 自动查询已暂停'));
assert(!table.textContent.includes('更新延迟'));
const explorer=nodes['#quota-explorer'];
delete nodes['#quota-explorer'];
ui.renderQuotaAccounts(ui.quotaSnapshot());
const recoverButton=descendants(table).find(n=>n.tagName==='BUTTON' && n.textContent==='查看当前状态与恢复');
assert(recoverButton, 'Collection retains the read-only recovery entry');
nodes['#quota-explorer']=explorer;
ui.renderQuotaAccounts(ui.quotaSnapshot());
assert(!descendants(table).some(n=>n.tagName==='BUTTON' && n.textContent==='查看当前状态与恢复'), 'Overview omits historical diagnostic controls');

// A machine pause for another account must not be projected onto this row.
const unrelated={...account('other'),updated_at:new Date().toISOString(),
  quota_observation:{source:'codex_session',observed_at:new Date().toISOString()}};
ui.renderQuotaAccounts({codex:{accounts:[unrelated],refresh_errors:[{machine:'alpha',
  reason:'other account paused',query_state:'paused',account_id:'passive',account_label:'passive@example.test'}]}});
assert(!table.textContent.includes('主动查询已暂停'));

// A matching pause on another machine is harmless while the displayed source is healthy.
const healthySource={...account('passive','known','in_use','alpha'),reading_from:'beta',
  updated_at:new Date().toISOString(),quota_observation:{source:'codex_session',observed_at:new Date().toISOString()}};
ui.renderQuotaAccounts({codex:{accounts:[healthySource],refresh_errors:[{machine:'alpha',
  reason:'same account paused elsewhere',query_state:'paused',account_id:'passive',account_label:'passive@example.test'}]}});
assert(table.textContent.includes('alpha 自动查询已暂停'));

// A machine-only failure may keep the legacy row notice when the machine has
// exactly one live account; it must not be guessed when accounts are shared.
ui.renderQuotaAccounts({codex:{accounts:[{...unrelated,machines:['alpha']}],refresh_errors:[{machine:'alpha',
  reason:'identity unavailable',query_state:'paused'}]}});
assert(table.textContent.includes('alpha 自动查询已暂停'));
const sharedMachineAccounts=[{...account('first'),machines:['alpha']},{...account('second'),machines:['alpha']}];
ui.renderQuotaAccounts({codex:{accounts:sharedMachineAccounts,refresh_errors:[{machine:'alpha',
  reason:'identity unavailable',query_state:'paused'}]}});
assert(!table.textContent.includes('主动查询已暂停'));
ui.resetQuotaReadings();
// A current managed profile with no plan source must be visibly unverified.
const managedWithoutPlan = {...account('managed-without-plan'), account_plan:null,
  reading_plan:null, credential_plan:null, plan_verification:'unverified', updated_at:new Date().toISOString()};
ui.renderQuotaAccounts({codex:{accounts:[managedWithoutPlan]}});
const managedRow=table.tBodies.flatMap(b=>b.children).find(r=>r.dataset.accountId==='managed-without-plan');
assert.equal(managedRow.cells[2].textContent,'套餐未核实');

// Confirmed lack of a subscription must not look like available current quota.
const unsubscribed = {...account('unsubscribed'), quota_status:'no_subscription',
  quota_status_reason:'无有效 Claude Code 订阅（用户已确认）', plan_verification:'unverified',
  five_hour_used_pct:null, seven_day_used_pct:null, updated_at:null,
  last_observation:{five_hour_used_pct:0,seven_day_used_pct:7,observed_at:null,source:'legacy_unknown'}};
ui.renderQuotaAccounts({claude:{accounts:[unsubscribed]}});
const unsubscribedRow=table.tBodies.flatMap(b=>b.children).find(r=>r.dataset.accountId==='unsubscribed');
assert(unsubscribedRow.cells[2].textContent.includes('无有效订阅'));
assert.equal(unsubscribedRow.cells[5].textContent,'不适用');
assert(unsubscribedRow.cells[9].textContent.includes('历史配额'));
assert(unsubscribedRow.cells[9].textContent.includes('7%'));
assert(!unsubscribedRow.cells[9].textContent.includes('分钟前'));
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
      let details=table.querySelectorAll('.quota-observation-details').filter(d=>d.className==='quota-observation-details');
      details[0].open=true;
      ui.renderQuotaAccounts(readings);
      details=table.querySelectorAll('.quota-observation-details').filter(d=>d.className==='quota-observation-details');
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
  [['alpha','beta'], 'beta', 'alpha · beta'],
  [['alpha','alpha','beta'], 'beta', 'alpha · beta'],
  [['alpha','alpha'], 'alpha', 'alpha'],
  [['alpha'], 'beta', 'alpha'],
  [[], 'beta', '—'],
  [['alpha'], null, 'alpha'],
]) {
  const sample={...account('source-context'), machines, reading_from:source, updated_at:'2026-10-16T11:57:00Z'};
  ui.renderQuotaAccounts({codex:{accounts:[sample]}});
  const row=table.tBodies.flatMap(b=>b.children).find(r=>r.dataset.accountId==='source-context');
  assert(!row.cells[0].textContent.includes('观测'), 'quota time must leave identity');
  assert.equal(row.cells[8].textContent,expected,JSON.stringify({machines,source}));
  assert.equal(row.cells[9].dataset.label,'配额更新');
  if (source) assert(row.cells[9].textContent.includes('来源机器：'+source));
  assert(descendants(row.cells[9]).some(n=>n.tagName==='SUMMARY'&&n.textContent==='3 分钟前'));
  assert(!row.cells[9].textContent.includes('配额来源未知'));
  assert.equal(descendants(row.cells[9]).filter(n=>n.tagName==='DETAILS').some(n=>n.textContent.includes('未取得读数来源机器')),source===null);
  assert(row.cells[9].textContent.includes(ui.formatDate(sample.updated_at)));
}
for (const [stamp,expected] of [
  ['2026-10-16T11:59:40Z','刚刚'], ['2026-10-16T10:00:00Z','2 小时前'],
  ['2026-10-14T12:00:00Z','2 天前'], [null,'时间未知'], ['', '时间未知'],
  ['invalid','时间未知'], ['2026-10-16T13:00:00Z','时间超前'],
]) {
  ui.renderQuotaAccounts({codex:{accounts:[{...account('age'),updated_at:stamp}]}});
  const row=table.tBodies.flatMap(b=>b.children).find(r=>r.dataset.accountId==='age');
  assert(descendants(row.cells[9]).some(n=>n.tagName==='SUMMARY'&&n.textContent===expected),expected);
}
// A signed-out reading source needs a short status, with recovery in details.
for (const [provider,label] of [['claude','Claude'],['codex','Codex']]) {
  for (const kind of ['signed-out-source','healthy-source','other-machine','remembered','account-action']) {
    const sample=account('login-context','known',kind==='remembered'?'remembered':'in_use');
    if (kind==='account-action') sample.quota_source='account_action';
    const signedOut=kind==='healthy-source'?[]:[kind==='other-machine'?'beta':'alpha'];
    ui.renderQuotaAccounts({[provider]:{accounts:[sample],signed_out_machines:signedOut}});
    const row=table.tBodies.flatMap(b=>b.children).find(r=>r.dataset.accountId==='login-context');
    const cell=row.cells[9], detail=cell.children.find(n=>n.tagName==='DETAILS');
    const inline=cell.children.filter(n=>n.tagName!=='DETAILS').map(n=>n.textContent).join('');
    const affected=kind==='signed-out-source';
    assert(!nodes['#quota-signed-out'].textContent.includes('登录已失效'));
    assert.equal(inline.includes('来源机器未登录'),affected,provider+': '+kind);
    assert(!cell.textContent.includes('登录已失效'));
    assert(!inline.includes('保留旧值'));
    assert(!inline.includes('读数可能过期'));
    if (kind==='account-action') {
      assert(!inline.includes('账号操作读数'));
      assert(detail.textContent.includes('读数来源：账号操作中的配额查询'));
    }
    if (affected) {
      assert(inline.endsWith('来源机器未登录'));
      assert(!inline.includes('读数过期'));
      for (const text of ['alpha',label,'最后一次','登录此账号','更新统计与配额'])
        assert(detail.textContent.includes(text),text);
      assert.equal(sample.five_hour_used_pct,91,'copy must not alter the reading');
    }
  }
}
const staleAction = {...account('stale-action'), updated_at:'2026-10-14T12:00:00Z', quota_source:'account_action'};
ui.renderQuotaAccounts({codex:{accounts:[staleAction]}});
const staleRow=table.tBodies.flatMap(b=>b.children).find(r=>r.dataset.accountId==='stale-action');
assert(staleRow.cells[9].textContent.includes('读数来源：账号操作中的配额查询'));
assert(!staleRow.cells[9].textContent.includes('读数过期'));
assert(staleRow.cells[9].textContent.includes('读数已超过更新时限'));
assert(!staleRow.cells[9].textContent.includes('读数可能过期'));
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
  assert(rows()[0].cells[9].textContent.includes('读数来源：'+label));
  assert(rows()[0].cells[9].textContent.includes('配置名：work-profile'));
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
assert(rows()[0].cells[6].textContent.includes('6 天后重置'), 'fresh action reset must reach quota table');
assert(rows()[0].cells[5].textContent.includes('3%'));
const originalRow=rows()[0];
ui.updateCodexQuotaReadings([{...action,busy:true,operation:{...action.operation,stage:'login'}}]);
assert(rows().includes(originalRow),'unchanged readings must preserve table rows during progress polling');
assert(rows()[0].cells[9].textContent.includes('来自此次账号操作查询'));
assert(!rows()[0].cells[0].textContent.includes('本页查询'));
assert(!rows()[0].cells[0].textContent.includes('最后观测值，不代表当前状态'), 'fresh action reading must not be labelled as a machine-history reading');
assert(!rows()[0].cells[2].textContent.includes('机器记录'));
const observationDetails=descendants(rows()[0].cells[9]).find(n=>n.tagName==='DETAILS');
assert(observationDetails.textContent.includes('来自此次账号操作查询'));
assert(observationDetails.textContent.includes('读数来源：账号操作中的配额查询'));
assert(!observationDetails.textContent.includes('读数来源：会话日志'));
assert(observationDetails.textContent.includes('套餐'));
assert.equal(rows()[0].cells[8].textContent,'—');
assert(observationDetails.textContent.includes('当前未在已纳入的机器上使用'));
assert(observationDetails.textContent.includes('独立保存的登录状态见账号操作'));
assert(!rows()[0].cells[9].children.filter(n=>n.tagName!=='DETAILS').some(n=>n.textContent.includes('查询')),
  'query provenance stays in details, not a repeated row note');
assert(!rows()[0].cells[1].textContent.includes('历史'),'provider cell describes only provider');
assert(!rows()[0].cells[9].textContent.includes('读数较旧'));
Date.now=()=>Date.parse(observed)+3*86400000;
ui.renderQuotaAccounts(projection);
assert(!rows()[0].cells[9].textContent.includes('读数较旧'));
assert(descendants(rows()[0].cells[9]).some(n=>n.tagName==='SUMMARY'&&n.textContent.includes('天前')), 'observation age remains visible');
Date.now=realNow;
ui.renderQuotaAccounts(projection);
assert.equal(rows()[0].dataset.observedAt,past.updated_at,'deletion CAS stays on machine observation');
assert(rows()[1].cells[5].textContent.includes('91%'),'same workspace different member stays separate');
assert(rows()[2].cells[5].textContent.includes('91%'),'same email different workspace stays separate');
assert(rows()[1].cells[6].textContent.includes('上次重置时间已过'));
assert.equal(JSON.stringify(projection),frozen,'projection must not mutate machine input');
assert(!table.textContent.includes('fixture collection failure'));
delete nodes['#quota-explorer'];
ui.renderQuotaAccounts(projection);
assert(table.textContent.includes('fixture collection failure'), 'historical diagnostics remain on Collection');
nodes['#quota-explorer']=explorer;
// A newer machine observation and an equal timestamp both beat Web observations.
for (const newer of [observed,'2099-01-01T00:00:00Z']) {
  ui.renderQuotaAccounts({codex:{accounts:[{...past,updated_at:newer,seven_day_used_pct:42}]}});
  assert(rows()[0].cells[5].textContent.includes('42%'));
  assert(!rows()[0].cells[9].textContent.includes('来自此次账号操作查询'));
  assert(!rows()[0].cells[9].textContent.includes('历史读数，不代表当前状态'));
  assert(descendants(rows()[0].cells[9]).some(n=>n.tagName==='DETAILS'&&n.textContent.includes('历史保存未保留来源机器')));
  assert(rows()[0].cells[9].textContent.includes('当前未在已纳入的机器上使用'));
}
ui.renderQuotaAccounts(projection);
ui.updateCodexQuotaReadings([ {...action,operation:{after:{observed_at:'invalid',seven_day_used_pct:8}},batch_result:action.operation} ]);
assert(rows()[0].cells[5].textContent.includes('3%'),'retained batch observation survives an unreadable current observation');
ui.updateCodexQuotaReadings([{...action,last_quota:action.operation.after,operation:{stage:'failed',quota_status:'not_read'}}]);
assert(rows()[0].cells[5].textContent.includes('3%'),'failed refresh must retain successful post-send reading');
assert(rows()[0].cells[9].textContent.includes('来自此次账号操作查询'));
assert.equal(ui.latestCodexQuotaReading({last_quota:action.operation.after}),action.operation.after,'cards share retained quota selection');
const before={observed_at:new Date(Date.parse(observed)+1000).toISOString(),seven_day_used_pct:100,seven_day_resets_at:null};
for (const source of ['operation','batch_result']) {
  const candidate={...action,last_quota:action.operation.after,[source]:{stage:'failed',before}};
  ui.updateCodexQuotaReadings([candidate]);
  assert.equal(ui.latestCodexQuotaReading(candidate),before,'cards select newer before observation');
  assert(rows()[0].cells[5].textContent.includes('100%'),'before reaches quota table');
  assert(rows()[0].cells[6].textContent.includes('重置时间未知'),'new null reset is preserved');
}
ui.updateCodexQuotaReadings([ {...action,operation:{after:{observed_at:observed,seven_day_used_pct:null,seven_day_resets_at:null}}} ]);
assert.equal(rows()[0].cells[5].textContent,'未知');
assert.equal(rows()[0].cells[5].children[0].attributes['aria-label'],'用量未知');
assert.equal(descendants(rows()[0]).filter(n=>n.className.includes('quota-account-observation')).length,0);
assert(rows()[0].cells[9].textContent.includes(ui.formatDate(observed)), 'exact observation time is available in update details');
assert(!rows()[0].cells[5].textContent.includes('91%'),'unknown new reading never borrows old percentage');
ui.updateCodexQuotaReadings([]);
assert(rows()[0].cells[5].textContent.includes('91%'),'page cleanup can clear observations');
descendants(table).find(n=>n.className.includes('quota-past-toggle')).click();
assert(!document.querySelector('#quota-past-accounts').hidden);
ui.updateCodexQuotaReadings([action]);
assert(!document.querySelector('#quota-past-accounts').hidden,'new action observation preserves historical disclosure');
descendants(table).find(n=>n.className.includes('quota-past-toggle')).click();
ui.updateCodexQuotaReadings([]);
ui.renderQuotaAccounts(data);
assert(!count().includes('匹配')); assert.equal(count(),'2 个当前账号'); assert(!count().includes('机器记录'));
assert(!table.tBodies.some(b=>b.className==='quota-unknown')); assert(nodes['#quota-machine-records'].textContent.includes('old-b'));
assert(table.tBodies.find(b=>b.className==='quota-history').hidden);
buttons[2].click();
assert.equal(rows().length,1); assert.equal(rows()[0].dataset.accountId,'bob');
assert(!table.textContent.includes('fixture failure')); assert(nodes['#quota-signed-out'].hidden, 'unrelated signed-out machines belong in diagnostics');
assert(!table.textContent.includes('账号操作')); assert(!table.textContent.includes('查询配额')); assert(table.textContent.includes('37%'));
ui.renderQuotaAccounts(data); assert.equal(rows().length,1); assert.equal(buttons[2].attributes['aria-pressed'],'true');
search('nobody'); assert(table.textContent.includes('没有匹配的账号')); assert(!table.textContent.includes('fixture failure'));
nodes['#quota-clear'].click(); assert.equal(nodes['#quota-search'].value,''); assert(!count().includes('匹配'));
search('PRO alice'); assert.equal(rows().length,1); assert.equal(rows()[0].dataset.accountId,'alice');
search('old-b'); assert(count().includes('匹配 0 / 3')); assert(!count().includes('当前账号')); assert(nodes['#quota-machine-records'].textContent.includes('old-b'));
search('past'); assert(table.textContent.includes('移除')); assert.strictEqual(ui.quotaSnapshot(),data);
// The delete request can finish after the user hides its row with a filter.
search('bob'); assert.equal(rows().length,1);
nodes['#quota-window'].value='seven_day'; nodes['#quota-window'].listeners.change();
nodes['#quota-sort'].value='remaining_desc'; nodes['#quota-sort'].listeners.change();
assert.equal(nodes['#quota-clear'].hidden,false);
ui.removeRenderedAccount('claude','past','2026-01-01T00:00:00Z');
nodes['#quota-clear'].click(); assert(!count().includes('匹配')); assert(table.textContent.includes('bob@example.test')); assert(table.textContent.includes('alice@example.test'));
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
nodes['#quota-clear'].click(); assert(!count().includes('匹配'));
ui.renderQuotaAccounts({claude:{accounts:[],unavailable_reason:'fixture unavailable'},codex:{accounts:[]}});
assert(table.textContent.includes('fixture unavailable')); assert(!table.textContent.includes('没有匹配'));
replacementButtons[2].click();
assert(table.textContent.includes('fixture unavailable'));
console.log('quota explorer: filters, counters, notices, polling, full snapshot removal, unavailable passed');

// Refresh binds workspace and email, independent of table filters; no management link.
ui.quotaFilter.search=''; ui.quotaFilter.provider='all';
ui.updateCodexQuotaReadings([
 {id:'profile-one',account_id:'one',email:'same@example.test',quota_query_allowed:true},
 {id:'profile-two',account_id:'two',email:'same@example.test',quota_query_allowed:true},
 {id:'profile-wrong-email',account_id:'three',email:'different@example.test',quota_query_allowed:true},
]);
ui.renderQuotaAccounts({codex:{accounts:['one','two','three'].map(id=>({...account(id),account_label:'same@example.test'}))}});
const actionButtons=descendants(table).filter(n=>n.tagName==='BUTTON' && n.textContent==='刷新配额');
assert.equal(actionButtons.length,2,'no action for email mismatch');
assert(!table.textContent.includes('管理账号'));
assert(!table.textContent.includes('查询受限，请查看账号'));
// Pending clicks survive a table rerender and page teardown aborts the operation.
let finishRefresh, refreshCalls=[];
window.AgentMonitor.refreshCodexAccount=(profile,signal)=>{
 refreshCalls.push({profile,signal});
 return new Promise(resolve=>{finishRefresh=resolve;});
};
actionButtons[0].click(); actionButtons[0].click();
assert.equal(refreshCalls.length,1,'a stale button cannot submit twice');
assert.equal(refreshCalls[0].profile.id,'profile-one');
assert(descendants(table).filter(n=>n.tagName==='BUTTON' && n.textContent==='刷新配额')[0].disabled);
const pendingSignal=refreshCalls[0].signal;
const preservedSnapshot=ui.quotaSnapshot();
ui.resetQuotaReadings();
assert(pendingSignal.aborted,'leaving the page cancels the action');
finishRefresh({submitted:false,snapshot:{accounts:[]}});
ui.renderQuotaAccounts(preservedSnapshot);
for (const [sort,hidden] of [['default',true],['remaining',false],['reset',false],['remaining_desc',false],['observed',true]]) {
 ui.quotaFilter.sort=sort;ui.renderQuotaAccounts(ui.quotaSnapshot());
 assert.equal(nodes['#quota-window-control'].hidden,hidden,sort);
}
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
