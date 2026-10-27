const {test, afterEach} = require('node:test');
const assert = require('node:assert/strict');
const {setImmediate: nextTurn} = require('node:timers');
const {init} = require('../web/codex-accounts.js');

const globalNames = ['window', 'document', 'location', 'fetch', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'];
const originals = Object.fromEntries(globalNames.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
afterEach(() => {
  for (const name of globalNames) {
    if (originals[name]) Object.defineProperty(globalThis, name, originals[name]);
    else delete globalThis[name];
  }
});
const turn = () => new Promise(resolve => nextTurn(resolve));
async function until(read, description) {
  for (let attempt = 0; attempt < 100; attempt++) {
    const value = read();
    if (value) return value;
    await turn();
  }
  assert.fail(description);
}

class Element {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this.dataset = {}; this.textContent = '';
    this.open = false; this.hidden = false; this.disabled = false; this.listeners = new Map();
  }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  prepend(child) { child.parent = this; this.children.unshift(child); }
  setAttribute(key, value) { this[key] = value; }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  addEventListener(event, listener) { this.listeners.set(event, listener); }
  removeEventListener(event, listener) { if (this.listeners.get(event) === listener) this.listeners.delete(event); }
  emit(event) { return this.listeners.get(event)?.(); }
  querySelector(tag) {
    for (const child of this.children) {
      if (child.tag === tag) return child;
      const nested = child.querySelector(tag); if (nested) return nested;
    }
    return null;
  }
  replaceWith(child) { const parent = this.parent; parent.children[parent.children.indexOf(this)] = child; child.parent = parent; }
  remove() { if (this.parent) { this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; } }
  insertBefore(child, before) { child.remove(); child.parent = this; this.children.splice(before ? this.children.indexOf(before) : this.children.length, 0, child); }
  showModal() { this.open = true; }
  close(value = '') { this.open = false; this.returnValue = value; this.emit('close'); }
}
function fixture({blocked = false, statusError = false, holdStatus = false, accounts = [], batch = null} = {}) {
  const nodes = new Map();
  function node(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); }
  const root = new Element('details'); root.querySelector = node;
  const body = new Element('body');
  const listeners = new Map(), timers = new Map(), intervals = new Map(), calls = [], popups = [], cleanups = [];
  let timerId = 0, resolveStatus, active = true;
  const snapshot = {accounts, batch};
  globalThis.location = {origin: 'http://hub.test'};
  globalThis.document = {hidden: false, body,
    querySelector: selector => selector === '#codex-account-actions' ? root : null,
    addEventListener() {}, removeEventListener() {}, createElement: tag => new Element(tag)};
  globalThis.window = {
    AgentMonitor: {pageScope: () => () => active, onPageCleanup: fn => cleanups.push(fn),
      pageInterval() {}, ensureTimezone: async () => {}, formatDate: value => value,
      latestCodexQuotaReading: () => null},
    addEventListener(event, listener) {listeners.set(event, listener);},
    removeEventListener(event, listener) {if (listeners.get(event) === listener) listeners.delete(event);},
    open(url) {if (blocked) return null; const popup = {url, closed: false, close() {this.closed = true;}}; popups.push(popup); return popup;},
  };
  globalThis.setTimeout = fn => {timers.set(++timerId, fn); return timerId;};
  globalThis.clearTimeout = id => timers.delete(id);
  globalThis.setInterval = fn => {intervals.set(++timerId, fn); return timerId;};
  globalThis.clearInterval = id => intervals.delete(id);
  globalThis.fetch = async (url, options) => {
    const action = String(url).split('/api/codex-accounts')[1]?.replace(/^\//, '');
    const payload = options.body ? JSON.parse(options.body) : undefined;
    // Every transport is closed over this fixture; an unexpected endpoint fails.
    assert.notEqual(action, undefined, String(url));
    let data;
    if (!action) data = snapshot;
    else {
      calls.push({action, payload});
      if (action === 'network-status') {
        if (holdStatus) await new Promise(resolve => resolveStatus = resolve);
        if (statusError) throw Error('fixture route unavailable');
        data = {connected: true, label: 'fixture-device'};
      } else if (action === 'egress-preview') data = {
        confirmation: 'receipt', checked_host: 'fixture-device',
        targets: [{host: 'chatgpt.com', ip: '192.0.2.1', country_code: 'US', observed_at: 1, retry_after_seconds: 0}],
      };
      else if (action === 'batch-start' || action === 'batch-retry') data = {};
      else assert.fail('unexpected action: ' + action);
    }
    return {ok: true, json: async () => data};
  };
  const controller = init({autoLoad: false});
  controller.update(snapshot);
  function message(overrides = {}) {
    const popup = popups.at(-1), nonce = new URL(popup.url).searchParams.get('nonce');
    listeners.get('message')?.({origin: 'http://127.0.0.1:39002', source: popup,
      data: {type: 'agent-monitor-network', nonce, connection: {id: 'route', capability: 'fixture'}}, ...overrides});
  }
  function select(ids) {
    for (const card of node('#codex-account-list').children) {
      const checkbox = card.querySelector('input');
      if (checkbox) {checkbox.checked = ids.includes(card.dataset.id); checkbox.emit('change');}
    }
  }
  async function preview(accept = false) {
    const dialog = await until(() => body.querySelector('dialog'), 'egress dialog did not open');
    await until(() => calls.some(call => call.action === 'egress-preview'), 'egress request did not run');
    await turn();
    const controls = dialog.children.at(-1);
    if (accept) {const confirm = controls.children[1]; assert.equal(confirm.disabled, false); confirm.emit('click');}
    else controls.children[0].emit('click');
    return dialog;
  }
  function cleanup() {active = false; for (const dispose of cleanups) dispose();}
  return {node, calls, popups, timers, intervals, listeners, message, select, preview, cleanup,
    connect: () => node('#codex-device-connect').emit('click'),
    batchStart: () => node('#codex-batch-start').emit('click'),
    retry: () => node('#codex-batch-retry').emit('click'),
    standalonePreview: () => node('#codex-egress-preview').emit('click'),
    completeStatus: () => resolveStatus?.(),
    error: () => node('#codex-account-notice').textContent,
  };
}
const account = id => ({id, email: id + '@example.test', account_id: 'workspace-' + id, has_credentials: true});

test('main action with no prior pairing opens synchronously and waits for validated device', async () => {
  const f = fixture(), pending = f.connect();
  assert.equal(f.popups.length, 1, 'one click must open pairing without a separate connect step');
  assert.equal(f.calls.length, 0); f.message(); await pending;
  assert.equal(f.calls[0].action, 'network-status');
  assert.equal(f.calls[0].payload.connection.id, 'route');
  assert.match(f.node('#codex-device-status').textContent, /已连接当前设备：fixture-device/);
  assert.equal(f.popups[0].closed, true); assert.equal(f.listeners.size, 0); assert.equal(f.timers.size, 0);
});
test('exact origin, source, nonce and message type gate the status request', async () => {
  const f = fixture(), pending = f.connect();
  for (const bad of [{origin: 'http://evil.test'}, {source: {}}, {data: {type: 'agent-monitor-network', nonce: 'wrong'}}, {data: {type: 'other'}}]) f.message(bad);
  assert.equal(f.calls.length, 0); f.message(); await pending; assert.equal(f.calls.length, 1);
});
test('duplicate action shares one handshake; later action and refreshed page pair again', async () => {
  const f = fixture(), first = f.connect();
  await f.connect(); assert.equal(f.popups.length, 1);
  f.message(); await first; assert.equal(f.calls.length, 1);
  const second = f.connect(); assert.equal(f.popups.length, 2); f.message(); await second;
  const fresh = fixture(), third = fresh.connect(); assert.equal(fresh.popups.length, 1); fresh.message(); await third;
});
test('popup denial reports the actionable browser phase and performs no network request', async () => {
  const f = fixture({blocked: true}); await f.connect(); assert.match(f.error(), /弹窗/);
  assert.equal(f.calls.length, 0); assert.equal(f.node('#codex-device-help').open, true);
});
test('timeout closes popup and rejects late responses, then a new action can recover', async () => {
  const f = fixture(), first = f.connect(), receive = f.listeners.get('message'), old = f.popups[0];
  [...f.timers.values()][0](); await first; assert.match(f.error(), /未收到/);
  assert.equal(old.closed, true); assert.equal(f.listeners.size, 0); assert.equal(f.intervals.size, 0);
  receive({origin: 'http://127.0.0.1:39002', source: old, data: {type: 'agent-monitor-network',
    nonce: new URL(old.url).searchParams.get('nonce'), connection: {id: 'late', capability: 'late'}}});
  assert.equal(f.calls.length, 0);
  const second = f.connect(); f.message(); await second; assert.equal(f.calls.length, 1);
});
test('closed popup and explicit cancellation release all resources without status or preview', async () => {
  for (const cancel of ['closed', 'button']) {
    const f = fixture(), pending = f.connect();
    if (cancel === 'closed') {f.popups[0].closed = true; [...f.intervals.values()][0]();}
    else f.node('#codex-device-cancel').emit('click');
    await pending; assert.match(f.error(), /未继续/);
    assert.equal(f.calls.length, 0); assert.equal(f.popups[0].closed, true); assert.equal(f.listeners.size, 0);
    assert.equal(f.timers.size, 0); assert.equal(f.intervals.size, 0);
  }
});
test('navigation during pairing or delayed server validation never returns a usable connection', async () => {
  for (const afterResponse of [false, true]) {
    const f = fixture({holdStatus: true}), pending = f.connect();
    if (afterResponse) f.message();
    f.cleanup(); await pending;
    f.completeStatus(); await turn();
    assert.equal(f.popups[0].closed, true); assert.equal(f.timers.size, 0); assert.equal(f.listeners.size, 0);
    assert.doesNotMatch(f.node('#codex-device-status').textContent, /已连接当前设备/);
    await f.connect(); assert.equal(f.popups.length, 1, 'disposed page cannot pair again');
    assert(f.calls.every(call => call.action === 'network-status'), 'navigation must not enable an account action');
  }
});
test('status validation failure and timeout fail closed and expose the failed phase', async () => {
  for (const timeout of [false, true]) {
    const f = fixture({statusError: !timeout, holdStatus: timeout}), pending = f.connect(); f.message();
    if (timeout) [...f.timers.values()][0]();
    await pending; assert.match(f.error(), /连接校验/); f.completeStatus(); await turn();
    assert.equal(f.node('#codex-device-help').open, true); assert.equal(f.popups[0].closed, true);
  }
});
test('local connector error is shown in main page without checking accounts', async () => {
  const f = fixture(), pending = f.connect(), nonce = new URL(f.popups[0].url).searchParams.get('nonce');
  f.message({data: {type: 'agent-monitor-network', nonce, error: '连接端尚未连接服务器'}});
  await pending; assert.match(f.error(), /连接端尚未连接服务器/); assert.equal(f.calls.length, 0);
});
test('batch action freezes selection, waits for pairing and preview, and submits once', async () => {
  const f = fixture({accounts: [account('a'), account('b')], batch: {id: 'old', items: []}});
  f.select(['a']);
  const pending = f.batchStart();
  f.select(['b']); await f.batchStart();
  assert.equal(f.popups.length, 1); assert.equal(f.calls.length, 0);
  f.message();
  const dialog = await f.preview(true); await pending;
  assert.deepEqual(f.calls.map(call => call.action), ['network-status', 'egress-preview', 'batch-start']);
  assert.deepEqual(f.calls[1].payload.profile_ids, ['a']);
  assert.deepEqual(f.calls[2].payload.profile_ids, ['a']); assert.equal(f.calls[2].payload.expected_batch_id, 'old');
  assert.equal(f.calls[2].payload.confirmation, 'receipt');
  const identities = dialog.children.find(child => child.tag === 'ul');
  assert.deepEqual(identities.children.map(child => child.textContent), ['a@example.test（workspace-a）']);
});
test('standalone preview and retry also pair on their own user gesture', async () => {
  for (const action of ['preview', 'retry']) {
    const f = fixture({batch: {id: 'original-batch', items: []}});
    const pending = action === 'preview' ? f.standalonePreview() : f.retry();
    assert.equal(f.popups.length, 1); assert.equal(f.calls.length, 0); f.message();
    if (action === 'preview') await f.preview();
    await pending;
    if (action === 'preview') assert.deepEqual(f.calls.map(call => call.action), ['network-status', 'egress-preview']);
    else {assert.equal(f.calls[1].action, 'batch-retry'); assert.equal(f.calls[1].payload.batch_id, 'original-batch');}
  }
});
test('failed pairing and rejected exit confirmation never submit a batch', async () => {
  for (const cancelledPreview of [false, true]) {
    const f = fixture({statusError: !cancelledPreview, accounts: [account('a')]});
    const pending = f.batchStart(); f.message();
    if (cancelledPreview) await f.preview();
    await pending;
    assert.deepEqual(f.calls.map(call => call.action), cancelledPreview ? ['network-status', 'egress-preview'] : ['network-status']);
  }
});
test('full-page navigation closes pending popup and detaches both window listeners', async () => {
  const f = fixture(), pending = f.connect(); f.listeners.get('pagehide')();
  await pending;
  assert.equal(f.popups[0].closed, true); assert.equal(f.listeners.size, 0); assert.equal(f.timers.size, 0);
  assert.doesNotMatch(f.node('#codex-device-status').textContent, /已连接当前设备/);
});
