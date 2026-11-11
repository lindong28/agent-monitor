const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const scripts = ['charts', 'quota', 'network', 'sessions', 'collection', 'overview', 'app', 'pivot'];
function platform() {
  const timers = new Set(), cleared = [];
  const context = {
    URL, URLSearchParams, Date, Intl, AbortController, console,
    document: {readyState: 'loading', addEventListener() {},
      querySelector: () => null, querySelectorAll: () => []},
    window: {location: new URL('http://fixture/'),
      setInterval(handler, interval) {const id = {handler, interval}; timers.add(id); return id;},
      clearInterval(id) {cleared.push(id); timers.delete(id);}},
    fetch() {throw Error('bootstrap must not fetch before initialization');},
  };
  return {context, timers, cleared};
}
// Execute exactly the classic browser resources, without CommonJS or source rewriting.
for (const file of fs.readdirSync(path.join(root, 'web')).filter(name => name.endsWith('.html'))) {
  const html = fs.readFileSync(path.join(root, 'web', file), 'utf8');
  let previous = -1;
  for (const name of scripts) {
    const position = html.indexOf(`<script src="/web/${name}.js"></script>`);
    assert(position > previous, `${file}: ${name} must load after its dependencies`);
    previous = position;
  }
}
const first = platform();
vm.createContext(first.context);
for (const name of scripts) vm.runInContext(fs.readFileSync(path.join(root, 'web', name + '.js'), 'utf8'), first.context, {filename: name + '.js'});
const ui = first.context.window.AgentMonitor;
for (const name of ['initOverview', 'initSessions', 'initCollection', 'initNetwork']) assert.equal(typeof ui[name], 'function');
assert.equal(typeof first.context.window.AgentMonitorPivot.init, 'function');
const current = ui.pageScope();
let cleaned = 0, destroyed = 0;
ui.pageInterval(() => {}, 30000);
ui.pageInterval(() => {}, 1000);
ui.onPageCleanup(() => cleaned++);
first.context.window.ttWebCharts.fixture = {destroy() {destroyed++;}};
assert.equal(current(), true);
ui.endPageLifetime();
assert.equal(current(), false);
assert.equal(first.timers.size, 0);
assert.equal(first.cleared.length, 2);
assert.equal(cleaned, 1);
assert.equal(destroyed, 1);
ui.endPageLifetime();
assert.equal(cleaned, 1);
assert.equal(destroyed, 1);
assert.equal(ui.pageScope()(), true);
// Each app owns its own state even though Node caches the module factories.
const second = platform(), third = platform();
const createApp = require('../web/app.js');
const two = createApp(second.context), three = createApp(third.context);
two.quotaFilter.provider = 'codex';
two.sessionsView.rows.push({session_id: 'only-two'});
assert.equal(three.quotaFilter.provider, 'all');
assert.equal(three.sessionsView.rows.length, 0);
assert.notEqual(second.context.window.ttWebCharts, third.context.window.ttWebCharts);
// A missing dependency must fail, so this fixture cannot pass on an empty bootstrap.
const incomplete = platform();
vm.createContext(incomplete.context);
assert.throws(() => vm.runInContext(fs.readFileSync(path.join(root, 'web/app.js'), 'utf8'), incomplete.context));
console.log('browser scripts: 9 pages; lifetime: 2 timers, 2 teardowns; independent app instances: 2; missing dependency: rejected');
two.sessionsView.rows = Array.from({length:151}, (_, index) => ({session_id:`id-${index}`, title:index === 150 ? '中文 Recovery Task' : 'other'}));
two.sessionsView.search = 'ID-150';
assert.deepEqual(two.filteredSessions().map(row=>row.session_id), ['id-150']);
two.sessionsView.search = '中文 recovery';
assert.deepEqual(two.filteredSessions().map(row=>row.session_id), ['id-150']);
two.sessionsView.search = 'missing';
assert.equal(two.filteredSessions().length, 0);
two.sessionsView.search = ' ';
assert.equal(two.filteredSessions().length, 151);
const resetUi = require('../web/quota.js')({platform:second.context, fmtAbs:()=>'recorded-time'});
assert.match(resetUi.resetText(1, 2000), /预计.*已过/);
assert.doesNotMatch(resetUi.resetText(1, 2000), /已于.*重置/);
assert.match(resetUi.resetText(3, 2000), /预计/);


// Exercise the shared recovery consumer with scoped and legacy HTTP contracts.
(async () => {
  function element(tag) {
    let text = '';
    return {tag, children: [], attrs: {}, listeners: {}, hidden: false, disabled: false,
      set textContent(value) {text = String(value); this.children = [];},
      get textContent() {return text + this.children.map(child => child.textContent).join('\n');},
      setAttribute(key, value) {this.attrs[key] = value;},
      append(...children) {this.children.push(...children);},
      appendChild(child) {this.children.push(child); return child;},
      replaceChildren(...children) {text = ''; this.children = children;},
      addEventListener(key, handler) {this.listeners[key] = handler;},
      click() {return this.listeners.click?.();},
    };
  }
  const flatten = node => [node, ...node.children.flatMap(flatten)];
  const action = (panel, name) => flatten(panel).find(node => node.attrs['data-recovery-action'] === name);
  const tick = () => new Promise(resolve => setImmediate(resolve));
  const calls = [], approvals = [];
  let saved = 0;
  let events = 0;
  const profile = 'monitor-00000000-0000-4000-8000-000000000001';
  let value = {machine: 'self', observed_at: '2026-11-07T00:00:00Z', active: null,
    pause_scope: 'account', pause: {id: 'pause-a', failure: {
      stage: 'quota_read', kind: 'rpc_error', http_status: 401, upstream_code: 'token_revoked'}},
    account: {account_id: 'workspace-a', account_label: 'a@example.test', quota_profile: profile,
      source: 'managed', auth_recovery_available: true}};
  const ui = require('../web/quota.js')({platform: {
    document: {createElement: element, dispatchEvent() {events++;}},
    window: {AgentMonitor: {onPageCleanup() {}, previewUpstream: async (targets, options) => {
      approvals.push({targets, options}); return 'fixture-confirmation';
    }}},
    fetch: async (url, options = {}) => {
      calls.push({url, options});
      return {ok: true, status: 200, json: async () => options.method === 'POST' ? {ok: true} : value};
    },
  }, formatDate: String});
  const panel = ui.createRecoveryPanel('self', {profile, pauseScope: 'account', getConnection: async () => ({id:'fixture',capability:'fixture'}), onSaved: () => {saved++;}});
  await tick();
  const query = new URL(calls[0].url, 'http://fixture').searchParams;
  assert.equal(query.get('profile'), profile);
  assert.equal(query.get('pause_scope'), 'account');
  assert.equal(action(panel, 'probe').textContent, '刷新认证并检查配额');
  await action(panel, 'probe').click();
  const recovery = calls.find(call => call.options.method === 'POST');
  assert.equal(JSON.parse(recovery.options.body).pause_scope, 'account');
  assert.deepEqual(approvals[0].options.approval_scope,
    ['self', 'workspace-a', 'a@example.test', profile, 'renew_auth', 'account']);
  assert.equal(events, 2, 'status refresh emits the existing owning-view update event');
  value = {...value, account: {...value.account, auth_recovery_available: false,
    local_commit: {operation_id: 'operation-a', pause_id: 'pause-a'}},
    pause: {id: 'pause-a', failure: {stage: 'recovery_commit'}}};
  await panel.refresh();
  assert.equal(action(panel, 'commit').hidden, false);
  await action(panel, 'commit').click();
  const commit = calls.find(call => call.url === '/api/codex-accounts/commit-recovery');
  assert.equal(JSON.parse(commit.options.body).pause_scope, 'account');
  assert.equal(approvals.length, 1, 'local save needs no upstream preview');
  assert.equal(saved, 2, "successful recovery and local commit both refresh saved quota consumers");
  const managedState = value;
  const managedCalls = [...calls];
  for (const source of ['default', 'configured']) {
    calls.length = 0;
    value = {...managedState, pause_scope: undefined, pause: {id: 'pause-native', failure: {
      stage: 'quota_read', kind: 'rpc_error', http_status: 401, upstream_code: 'token_expired'}}, account: {
      account_id: 'native-workspace', account_label: 'native@example.test', source,
      quota_profile: '', auth_recovery_available: true, source_digest: 'a'.repeat(64)}};
    const native = ui.createRecoveryPanel('self');
    await tick();
    assert.equal(action(native, 'probe').textContent, '刷新认证并检查配额');
    await action(native, 'probe').click();
    const posted = JSON.parse(calls.find(call => call.options.method === 'POST').options.body);
    assert.equal(posted.source_digest, 'a'.repeat(64));
    assert.equal(posted.mode, 'renew_auth');
    assert.deepEqual(approvals.at(-1).options.approval_scope,
      ['self', 'native-workspace', 'native@example.test', 'native', 'renew_auth', 'a'.repeat(64)]);
  }
  value = managedState;
  calls.splice(0, calls.length, ...managedCalls);
  value = {...value, pause: {id:'local-pause'}, shared_pause: {id:'f'.repeat(32)},
    account: {...value.account, blocked_reason:'shared protection', account_quota_pause:{id:'local-pause'}}};
  await panel.refresh();
  assert.equal(action(panel, 'probe').disabled, true);
  assert.equal(action(panel, 'help').hidden, false);
  await action(panel, 'help').click();
  assert.match(panel.textContent, /先由维护者检查/);
  assert(panel.textContent.includes('agent-monitor upstream-holds resume codex --pause-id '+'f'.repeat(32)));
  const postsBeforeHelp = calls.filter(call => call.options.method === 'POST').length;
  assert.equal(postsBeforeHelp, 2, 'guidance never acknowledges or queries automatically');
  value = {...value, pause:null, shared_pause:null, account:{...value.account, blocked_reason:null, account_quota_pause:null}};
  await panel.refresh();
  assert.match(panel.children[0].textContent, /此账号当前没有配额暂停/);
  value = {...value, account: {...value.account, quota_profile: 'monitor-other'}};
  await panel.refresh();
  assert.equal(action(panel, 'probe').disabled, true, 'wrong target must fail closed');
  assert.match(panel.textContent, /所选对象不一致/);
  delete value.pause_scope;
  const legacy = ui.createRecoveryPanel('self');
  await tick();
  assert.equal(new URL(calls.at(-1).url, 'http://fixture').searchParams.has('pause_scope'), false);
  value = {...value, pause: {id:'f'.repeat(32)}, account:{...value.account, blocked_reason:'account hold', account_quota_pause:{id:'local-pause'}}};
  await legacy.refresh();
  assert.equal(action(legacy, 'help').hidden, false, 'global panel explains the same layered condition');
  for (const [reason, expected] of [['missing', /未找到.*原账号/], ['identity_changed', /当前登录.*不同/], ['ambiguous', /多个.*来源/], ['source_inconsistent', /配置与凭据身份不一致/]]) {
    value = {...value, pause:{id:'f'.repeat(32)}, account:{account_id:null, account_label:null, resolution_reason:reason}};
    await legacy.refresh();
    assert.match(legacy.textContent, expected);
    assert.equal(action(legacy, 'probe').hidden, true);
    assert.match(legacy.textContent, /已有用量/);
  }
  value = {...value, pause:null, account:{account_id:'current', account_label:'current@example.test'},
    last_recovery:{ok:true, observed_at:'2026-11-10T00:00:00Z',
      request:{machine:'self', account_id:'original', account_label:'original@example.test'}}};
  await legacy.refresh();
  assert.match(legacy.children[3].textContent, /original@example.test/);
  assert.doesNotMatch(legacy.textContent, /检查对象：self · current@example.test/);
  value = {...value, pause:{id:'new-pause'}, last_recovery:{...value.last_recovery, ok:false}};
  await legacy.refresh();
  assert.match(legacy.textContent, /检查对象：self · current@example.test/);
  assert.match(legacy.children[3].textContent, /original@example.test/);
  value = {...value, pause:null, last_recovery:{ok:true, observed_at:'2026-11-10T00:00:00Z'}};
  await legacy.refresh();
  assert.match(legacy.textContent, /检查对象未记录/);
  panel.dispose(); legacy.dispose();

  global.document = {createElement:element};
  global.window = {AgentMonitor:{formatDate:String,latestCodexQuotaReading:()=>null}};
  const {renderAccountBatch} = require('../web/codex-accounts.js');
  const controls = {root:{querySelector:()=>null},batchHelp:{replaceChildren(){}}};
  for (const key of ['batchStart','batchNext','batchRetry','batchStatus','blockedReason']) controls[key]={};
  const state = {accounts:[{id:'paused',email:'a@example.test',eligible:true,has_credentials:true,account_quota_pause:{id:'p'}}],
    selectedIds:new Set(['paused']),batch:null,busy:false,upstreamPause:null};
  renderAccountBatch(state,controls);
  assert.equal(controls.batchStart.disabled,true);
  assert.match(controls.blockedReason.textContent,/修复登录/);
  state.batch={id:'b',created_at:'2026-11-07T00:00:00Z',items:[{profile_id:'paused',operation:{message_status:'not_sent'}}]};
  renderAccountBatch(state,controls);
  assert.equal(controls.batchRetry.disabled,true);
  state.accounts[0].account_quota_pause=null;
  renderAccountBatch(state,controls);
  assert.equal(controls.batchStart.disabled,false);
  assert.equal(controls.batchRetry.disabled,false);
  console.log('recovery consumer: account/global selection; auth confirmation binding; local commit; wrong-target rejection');
})().catch(error => {console.error(error); process.exitCode = 1;});
