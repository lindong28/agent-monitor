const {test} = require('node:test');
const assert = require('node:assert/strict');
const createNetwork = require('../web/network.js');

async function fixture() {
  const nodes = new Map(), listeners = new Map(), timers = new Map(), popups = [], cleanups = [];
  const qs = key => {
    if (!nodes.has(key)) nodes.set(key, {value: '', dataset: {}, listeners: {}, textContent: '', innerHTML: '', querySelector() {return null;}, addEventListener(k, f) {this.listeners[k] = f;}});
    return nodes.get(key);
  };
  const window = {location: {origin: 'http://hub.test'}, crypto: require('node:crypto').webcrypto,
    open(url) {const p = {url, close() {this.closed = true;}, postMessage(data, origin) {this.ack = {data, origin};}}; popups.push(p); return p;},
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
  assert.match(f.qs('#process-network-result').innerHTML, /选择进程/);
  choose(f, '42:first');
  assert.match(f.qs('#process-network-result').innerHTML, /proxy1/);
  f.qs('#process-network-select').value = '43:second'; f.qs('#process-network-select').listeners.change();
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

function choose(f, instance) {
  f.qs('#process-network-select').value = instance;
  f.qs('#process-network-select').listeners.change();
}
function reply(f, payload, type = 'agent-monitor-process-network') {
  const popup = f.popups.at(-1), nonce = new URL(popup.url).searchParams.get('nonce');
  f.listeners.get('message')({origin: 'http://127.0.0.1:39002', source: popup, data: {type, nonce, ...payload}});
}
function read(f, data = report) {
  f.qs('#process-network-refresh').listeners.click();
  reply(f, {report: structuredClone(data)});
}
const capable = () => {
  const data = structuredClone(report);
  data.processes[0].context = {cwd: '/work/project-alpha', terminal: 'ttys1', started_at: '2026-01-01T00:00:00Z', parent_pid: 1};
  data.processes[0].probe_options = {
    provider: {status: 'available', target: 'https://chatgpt.com/cdn-cgi/trace', proxy_address: 'http://proxy1:80'},
    third_party: {status: 'available', target: 'http://ip-api.com/json/?fields=status,query,countryCode,country,regionName,city', proxy_address: 'http://proxy1:80'},
  };
  return data;
};
function probe(f, kind, changes = {}) {
  f.qs(kind === 'provider' ? '#process-network-probe' : '#process-network-third-party').listeners.click();
  reply(f, {probe: {instance: '42:first', probe_kind: kind, ip: '192.0.2.1', country_code: 'SG', observed_at: 1767225600, target: kind, ...changes}}, 'agent-monitor-process-network-probe');
}

test('refresh retains complete instance despite reordered report, failures, cancellation, and popup blocking', async () => {
  const f = await fixture(); read(f); choose(f, '43:second');
  const reordered = structuredClone(report); reordered.processes.reverse(); read(f, reordered);
  assert.equal(f.qs('#process-network-select').value, '43:second');
  assert.match(f.qs('#process-network-result').innerHTML, /proxy2/);
  for (const mode of ['error', 'cancel', 'timeout', 'blocked']) {
    const before = f.qs('#process-network-result').innerHTML;
    const open = f.window.open;
    if (mode === 'blocked') f.window.open = () => null;
    f.qs('#process-network-refresh').listeners.click();
    assert.equal(f.qs('#process-network-result').innerHTML, before);
    if (mode === 'error') reply(f, {error: 'read failed'});
    if (mode === 'cancel') f.qs('#process-network-cancel').listeners.click();
    if (mode === 'timeout') f.timers.get(1)();
    f.window.open = open;
    assert.equal(f.qs('#process-network-select').value, '43:second');
    assert.equal(f.qs('#process-network-result').innerHTML, before);
    assert.match(f.qs('#process-network-status').textContent, /保留.*2026-01-01/);
  }
  const reusedPid = structuredClone(report); reusedPid.processes[1].instance = '43:new';
  read(f, reusedPid);
  assert.equal(f.qs('#process-network-select').value, '');
  assert.match(f.qs('#process-network-status').textContent, /原选进程实例已不在/);
  assert.match(f.qs('#process-network-result').innerHTML, /选择进程/);
});

test('process context is searchable and optional observation stays bounded and escaped', async () => {
  const f = await fixture(), data = capable();
  data.processes[0].proxy_observation = {status: 'observed', connections: [{host: '<target>', port: 443, chains: ['node', 'Proxy', 'GLOBAL'], rule: '<rule>', observed_at: '2026-01-01T00:00:00Z'}]};
  read(f, data);
  f.qs('#process-network-search').value = 'project-alpha'; f.qs('#process-network-search').listeners.input();
  assert.match(f.qs('#process-network-select').innerHTML, /project-alpha/);
  assert.doesNotMatch(f.qs('#process-network-select').innerHTML, /43:second/);
  choose(f, '42:first');
  const html = f.qs('#process-network-result').innerHTML;
  assert.match(html, /Mihomo/); assert.match(html, /&lt;target>/); assert.match(html, /&lt;rule>/);
  assert.match(html, /GLOBAL → Proxy → node/); assert.match(html, /不提供公网出口/);
  assert.match(html, /<details.*启动配置与 TCP/);
});

test('old connector remains read only and backend admission controls both probe actions', async () => {
  const f = await fixture(); read(f); choose(f, '42:first');
  assert.equal(f.qs('#process-network-probe').disabled, true);
  assert.match(f.qs('#process-network-probe-help').textContent, /更新并重启/);
  f.qs('#process-network-probe').listeners.click(); assert.equal(f.popups.length, 1);
  const data = capable(); data.processes[0].probe_options.provider = {status: 'blocked', target: 'https://chatgpt.com/cdn-cgi/trace', reason: '存在绕过规则', next_step: '请核对原有策略'};
  read(f, data);
  assert.equal(f.qs('#process-network-probe').disabled, true);
  assert.equal(f.qs('#process-network-third-party').disabled, false);
  assert.match(f.qs('#process-network-probe-help').textContent, /存在绕过规则.*请核对原有策略/);
  assert.match(f.qs('#process-network-third-party-help').textContent, /HTTP 非加密.*非商业/);
  probe(f, 'third_party');
  assert.equal(new URL(f.popups.at(-1).url).searchParams.get('kind'), 'third_party');
  assert.match(f.qs('#process-network-result').innerHTML, /第三方出口观测/);
});

test('receipts require instance and kind; preserve independent successes and mark receipts historical after refresh', async () => {
  const f = await fixture(); read(f, capable()); choose(f, '42:first');
  for (const changes of [{instance: '42:other'}, {probe_kind: 'third_party'}, {probe_kind: undefined}]) {
    probe(f, 'provider', changes);
    assert.match(f.qs('#process-network-status').textContent, /不匹配/);
    assert.doesNotMatch(f.qs('#process-network-result').innerHTML, /192.0.2.1/);
  }
  probe(f, 'provider'); probe(f, 'third_party', {ip: '192.0.2.2'});
  let html = f.qs('#process-network-result').innerHTML;
  assert.match(html, /192.0.2.1/); assert.match(html, /192.0.2.2/);
  probe(f, 'third_party', {error: 'failed', ip: null});
  html = f.qs('#process-network-result').innerHTML;
  assert.match(html, /192.0.2.1/); assert.match(html, /192.0.2.2/); assert.match(html, /failed/);
  read(f, capable());
  html = f.qs('#process-network-result').innerHTML;
  assert.match(html, /供应商固定目标 · 历史回执/); assert.match(html, /第三方出口观测 · 历史回执/);
  choose(f, '43:second'); assert.doesNotMatch(f.qs('#process-network-result').innerHTML, /192.0.2/);
});

test('opening server details does not request network; only explicit detection does', async () => {
  const f = await fixture();
  const server = f.qs('#server-network'); server.open = true;
  server.listeners.toggle?.(); assert.equal(f.requests(), 0);
  await f.qs('#refresh').listeners.click(); assert.equal(f.requests(), 1);
});

test('missing identity remains inspectable but cannot probe or be reselected across reports', async () => {
  const f = await fixture(), data = capable(); delete data.processes[0].instance;
  read(f, data); choose(f, 'unavailable:0');
  assert.match(f.qs('#process-network-result').innerHTML, /project-alpha/);
  assert.equal(f.qs('#process-network-probe').disabled, true);
  f.qs('#process-network-probe').listeners.click(); assert.equal(f.popups.length, 1);
  read(f, data); assert.equal(f.qs('#process-network-select').value, '');
});

test('collector failure preserves selected evidence and blocks probes until a fresh report', async () => {
  const f = await fixture(); read(f, capable()); choose(f, '42:first');
  probe(f, 'provider'); probe(f, 'third_party', {ip: '192.0.2.2'});
  read(f, {schema_version: 1, host: 'browser-machine', observed_at: '2026-01-02T00:00:00Z', processes: [], errors: [{stage: 'collector', type: 'TimeoutExpired'}]});
  assert.equal(f.qs('#process-network-select').value, '42:first');
  const html = f.qs('#process-network-result').innerHTML;
  assert.match(html, /192.0.2.1/); assert.match(html, /192.0.2.2/);
  assert.match(f.qs('#process-network-status').textContent, /采集失败.*collector.*保留/);
  assert.equal(f.qs('#process-network-probe').disabled, true);
  assert.equal(f.qs('#process-network-third-party').disabled, true);
  const count = f.popups.length; f.qs('#process-network-probe').listeners.click();
  assert.equal(f.popups.length, count);
  read(f, capable()); assert.equal(f.qs('#process-network-probe').disabled, false);
  read(f, {...report, processes: []});
  assert.equal(f.qs('#process-network-select').value, '', 'a successful empty report still clears a departed selection');
});
