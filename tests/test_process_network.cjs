const {test} = require('node:test');
const assert = require('node:assert/strict');
const createNetwork = require('../web/network.js');

async function fixture() {
  const nodes = new Map(), listeners = new Map(), timers = new Map(), popups = [], cleanups = [];
  const qs = key => {
    if (!nodes.has(key)) nodes.set(key, {value: '0', dataset: {}, listeners: {}, textContent: '', innerHTML: '', addEventListener(k, f) {this.listeners[k] = f;}});
    return nodes.get(key);
  };
  const window = {location: {origin: 'http://hub.test'}, crypto: require('node:crypto').webcrypto,
    open(url) {const p = {url, close() {this.closed = true;}}; popups.push(p); return p;},
    addEventListener(k, f) {listeners.set(k, f);}, removeEventListener(k) {listeners.delete(k);},
    setTimeout(fn) {timers.set(1, fn); return 1;}, clearTimeout(id) {timers.delete(id);}};
  let requests = 0;
  const view = createNetwork({platform: {window, document: {}}, qs, updateNavLinks() {},
    api() {requests++; throw Error('server must not be read automatically');}, withRefresh: (_, f) => f(),
    formatDate: s => s, escapeHtml: s => String(s).replaceAll('<','&lt;'), onPageCleanup: f => cleanups.push(f)});
  await view.initNetwork();
  return {qs, window, listeners, timers, popups, cleanups, requests: () => requests};
}
const report = {schema_version: 1, host: 'browser-machine', observed_at: '2026-01-01T00:00:00Z', processes: [
  {pid: 42, agent: 'codex', instance: '42:first', environment_status: 'startup_snapshot', environment: {HTTPS_PROXY: {address: 'http://proxy1:80'}}, connections_status: 'observed', connections: [{local: '127.0.0.1:9', peer: '127.0.0.1:80', state: 'ESTABLISHED'}]},
  {pid: 43, agent: 'claude', instance: '43:second', environment_status: 'startup_snapshot', environment: {HTTPS_PROXY: {address: 'http://proxy2:81'}}, connections_status: 'observed', connections: []},
]};

test('local result requires origin, popup identity and nonce; process selection never calls Hub', async () => {
  const f = await fixture();
  f.qs('#process-network-refresh').listeners.click();
  const popup = f.popups[0], nonce = new URL(popup.url).searchParams.get('nonce');
  const event = {origin: 'http://127.0.0.1:39002', source: popup, data: {type: 'agent-monitor-process-network', nonce, report}};
  const receive = f.listeners.get('message');
  for (const invalid of [{...event, origin:'http://evil.test'}, {...event, source: {}}, {...event, data:{...event.data, nonce:'wrong'}}]) {
    receive(invalid); assert.equal(f.qs('#process-network-result').innerHTML, '');
  }
  receive(event);
  assert.match(f.qs('#process-network-status').textContent, /browser-machine/);
  assert.match(f.qs('#process-network-result').innerHTML, /proxy1/);
  f.qs('#process-network-select').value = '1'; f.qs('#process-network-select').listeners.change();
  assert.match(f.qs('#process-network-result').innerHTML, /proxy2/);
  assert.doesNotMatch(f.qs('#process-network-result').innerHTML, /proxy1/);
  assert.equal(f.requests(), 0); assert.equal(f.listeners.has('message'), false);
});
test('timeout, cancellation and SPA departure remove pending report and do not fall back', async () => {
  for (const mode of ['timeout','cancel','leave']) {
    const f = await fixture(); f.qs('#process-network-refresh').listeners.click();
    if (mode === 'timeout') f.timers.get(1)();
    if (mode === 'cancel') f.qs('#process-network-cancel').listeners.click();
    if (mode === 'leave') f.cleanups.forEach(fn => fn());
    assert.equal(f.listeners.has('message'), false); assert.equal(f.timers.size, 0);
    assert.equal(f.popups[0].closed, true); assert.equal(f.requests(), 0);
    assert.equal(f.qs('#process-network-select').disabled, true);
  }
});
