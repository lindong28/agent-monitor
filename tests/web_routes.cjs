// Exercise production route initializers with synthetic API data and DOM ports.
// Layout and browser interaction acceptance are separate from this Node fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const createApp = require('../web/app.js');
const createPivot = require('../web/pivot.js');
const root = path.resolve(__dirname, '..');

const pages = new Map([
  ['/', 'index.html'], ['/explore', 'explore.html'], ['/sessions', 'sessions.html'],
  ['/network', 'network.html'], ['/collection', 'collection.html'],
  ['/guide', 'guide.html'], ['/guide/sources', 'guide.html'],
  ['/guide/collection', 'guide-collection.html'], ['/guide/setup', 'guide-setup.html'],
  ['/guide/questions', 'guide-questions.html'],
]);

function element(tag = 'div') {
  const node = {
    tagName: tag.toUpperCase(), children: [], dataset: {}, style: {setProperty() {}},
    listeners: {}, className: '', hidden: false, value: '', textContent: '', innerHTML: '',
    options: [], selectedOptions: [{textContent: 'fixture'}],
    addEventListener(type, listener) { this.listeners[type] = listener; },
    setAttribute(name, value) { this[name] = String(value); },
    getAttribute(name) { return this[name] || null; },
    hasAttribute(name) { return this[name] !== undefined; },
    appendChild(child) { child.parentNode = child.parentElement = this; this.children.push(child); return child; },
    replaceChildren(...children) { this.children = []; children.forEach(child => this.appendChild(child)); },
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(child => child !== this); },
    before() {}, focus() {}, contains() { return false; }, closest() { return null; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    checkValidity() { return true; },
    getContext() { return {clearRect() {}}; },
    getBoundingClientRect() { return {height: 0}; },
  };
  node.classList = {add(...values) {node.className += ' ' + values.join(' ');}, toggle() {}};
  Object.defineProperty(node, 'tBodies', {get: () => node.children.filter(child => child.tagName === 'TBODY')});
  return node;
}

function fixture() {
  let active;
  const requests = [], errors = [], charts = [], intervals = new Set(), windowEvents = new Map(), documentEvents = new Map();
  const status = {instance_id: 'fixture-server', syncing: false, machines: [], coverage: {admitted: 0, declared: 0}};
  const overview = {today: {cost_usd: 2}, week: {cost_usd: 3}, range: {cost_usd: 4},
    cost_over_time: {columns: [], rows: []}, top_projects_week: [], model_mix_month: [],
    rate_limits: {}, sync: status};
  const payloads = {
    '/api/timezone': {timezone: 'UTC'}, '/api/overview': overview,
    '/api/collection': {sync: status, rate_limits: {}}, '/api/cost-diagnostics': {groups: []},
    '/api/sync-status': status, '/api/sessions': {sessions: [], filters: {}},
    '/api/pivot-filters': {}, '/api/pivot': {dimensions: ['day'], rows: []},
    '/api/network': {verdict: 'low', coverage: {missing: []}, public: {}, conclusions: []},
  };
  function addEvent(map, type, listener) {
    if (!map.has(type)) map.set(type, new Set());
    map.get(type).add(listener);
  }
  function page(html) {
    const nodes = new Map(), main = element('main');
    // IDs are read from the actual served HTML, so missing page controls fail.
    for (const match of html.matchAll(/<([\w-]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
      const node = element(match[1]);
      node.id = match[3];
      node.value = match[2].match(/\bvalue="([^"]*)"/)?.[1] || '';
      node.href = match[2].match(/\bhref="([^"]*)"/)?.[1];
      node.hidden = /\bhidden\b/.test(match[2]);
      node.parentElement = main;
      nodes.set('#' + node.id, node);
    }
    if (nodes.has('#range')) nodes.get('#range').value = '30d';
    if (nodes.has('#sort')) nodes.get('#sort').value = 'time';
    main.replaceWith = next => { active = next.page; };
    main.insertAdjacentHTML = (_, text) => errors.push(text);
    main.page = {nodes, main};
    return main.page;
  }
  const document = {
    readyState: 'loading', hidden: false, title: '', documentElement: {dataset: {}},
    addEventListener: (type, listener) => addEvent(documentEvents, type, listener),
    removeEventListener: (type, listener) => documentEvents.get(type)?.delete(listener),
    createElement: element, createTextNode: text => Object.assign(element('#text'), {textContent: text}),
    querySelector: selector => selector === 'main' ? active.main : active.nodes.get(selector) || null,
    querySelectorAll: () => [],
  };
  const window = {
    location: new URL('http://fixture/'),
    history: {
      pushState(_, __, url) { window.location = new URL(url, window.location); },
      replaceState(_, __, url) { window.location = new URL(url, window.location); },
    },
    addEventListener: (type, listener) => addEvent(windowEvents, type, listener),
    removeEventListener: (type, listener) => windowEvents.get(type)?.delete(listener),
    setInterval(handler, ms) {const timer = {handler, ms}; intervals.add(timer); return timer;},
    clearInterval(timer) { intervals.delete(timer); }, scrollTo() {},
    Chart: class {
      constructor(canvas, config) { this.config = config; this.destroyed = 0; charts.push(this); }
      destroy() { this.destroyed += 1; }
    },
  };
  async function fetch(raw) {
    const url = new URL(raw, window.location);
    requests.push(url);
    if (pages.has(url.pathname)) return {ok: true, text: async () => fs.readFileSync(path.join(root, 'web', pages.get(url.pathname)), 'utf8')};
    assert(Object.hasOwn(payloads, url.pathname), `unexpected API request: ${url.pathname}`);
    const value = typeof payloads[url.pathname] === 'function' ? await payloads[url.pathname](url) : payloads[url.pathname];
    return {ok: true, json: async () => value};
  }
  class DOMParser {
    parseFromString(html) { const next = page(html); return {title: 'fixture page', querySelector: selector => selector === 'main' ? next.main : null}; }
  }
  active = page(fs.readFileSync(path.join(root, 'web/index.html'), 'utf8'));
  return {window, document, fetch, DOMParser, requests, errors, charts, intervals, windowEvents, documentEvents, payloads, overview};
}

async function run() {
  const env = fixture();
  let redirected;
  // Pivot's existing browser DOM references resolve through these ports.
  Object.assign(globalThis, {window: env.window, document: env.document, DOMParser: env.DOMParser, Chart: env.window.Chart});
  const ui = createApp(env);
  env.window.AgentMonitorPivot = createPivot(ui);
  const enter = async (url, push = true) => {
    const previous = ui.pageScope();
    const oldTimers = [...env.intervals], oldCharts = env.charts.slice();
    if (!push) {
      env.window.location = new URL(url, env.window.location);
      env.window.location.replace = value => { redirected = value; };
    }
    assert.equal(await ui.renderRoute(new URL(url, env.window.location), {push}), true, url);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(previous(), false, `${url}: old page scope must expire`);
    for (const timer of oldTimers) assert(!env.intervals.has(timer), `${url}: previous timer survives`);
    for (const chart of oldCharts) assert.equal(chart.destroyed, 1, `${url}: chart cleanup must run once`);
    assert.deepEqual(env.errors, [], `${url}: route initializer failed`);
  };
  await enter('/?range=7d');
  assert.equal(ui.qs('#today-cost').textContent, '$2.00');
  assert.equal(ui.qs('#range-label').textContent, '近 7 天成本');
  assert.equal(env.requests.find(url => url.pathname === '/api/overview').searchParams.get('range'), '7d');
  assert.equal(env.intervals.size, 1);
  assert.equal([...env.intervals][0].ms, 30000);
  const firstOverviewTimer = [...env.intervals][0];
  const firstCharts = env.charts.slice();
  const returnUrl = '/?range=7d&quota_provider=codex&quota_search=fixture';
  await enter('/collection?range=7d&return=' + encodeURIComponent(returnUrl));
  assert.equal(ui.qs('#collection-return').href, returnUrl);
  assert.match(ui.qs('#cost-diagnostics-status').textContent, /没有金额未知记录/);
  assert.equal(env.intervals.size, 1);
  const beforeStaleTick = env.requests.length;
  await firstOverviewTimer.handler();
  assert.equal(env.requests.length, beforeStaleTick, 'departed overview timer cannot load');
  await enter(returnUrl, false);
  assert.equal(ui.quotaFilter.provider, 'codex');
  assert.equal(ui.quotaFilter.search, 'fixture');
  assert.equal(env.intervals.size, 1, 'Back must establish one fresh watcher');
  assert(env.charts.length > firstCharts.length, 'Back recreates chart instances');
  assert.equal(env.windowEvents.get('focus').size, 1);
  assert.equal(env.documentEvents.get('visibilitychange').size, 1);
  const expected = [
    ['/explore', '/api/pivot', '#pivot-status', '当前日期与筛选范围内无记录'],
    ['/sessions', '/api/sessions', '#session-count', '0 个会话'],
    ['/network', '/api/network', '#network-verdict', '未发现已知高风险信号 · 本地规则评估'],
  ];
  for (const [url, endpoint, selector, reading] of expected) {
    const begin = env.requests.length;
    await enter(url);
    assert(env.requests.slice(begin).some(request => request.pathname === endpoint), `${url}: route did not call its API`);
    assert.equal(ui.qs(selector).textContent, reading, `${url}: route did not render`);
  }
  for (const url of ['/guide/sources', '/guide/collection', '/guide/setup', '/guide/questions']) {
    const begin = env.requests.length;
    await enter(url);
    assert.deepEqual(env.requests.slice(begin).map(request => request.pathname),
      url === '/guide/collection' ? [url, '/api/sync-status'] : [url], 'only the collection guide reads status');
  }
  // Legacy route must still invoke its redirect initializer.
  await enter('/guide?range=90d', false);
  assert.equal(redirected, '/guide/sources?range=90d');
  // A response from the departed page must not render into shared element IDs.
  function hold(endpoint) {
    let release, arrived;
    const response = new Promise(resolve => { release = resolve; });
    const requested = new Promise(resolve => { arrived = resolve; });
    const original = env.payloads[endpoint];
    env.payloads[endpoint] = () => { arrived(); return response; };
    return {release, requested, restore() {env.payloads[endpoint] = original;}, original};
  }
  const lateOverview = hold('/api/overview');
  const loadingOverview = ui.renderRoute(new URL('/?range=90d', env.window.location), {push: true});
  await lateOverview.requested;
  await enter('/collection?range=90d');
  lateOverview.restore();
  lateOverview.release({...env.overview, sync: {polling_error: 'departed overview', machines: []}});
  await loadingOverview;
  assert.equal(ui.qs('#sync-summary').textContent, '使用已采集数据 · 自动更新');
  assert.equal(ui.qs('#today-cost'), null);
  const lateCollection = hold('/api/collection');
  const loadingCollection = ui.renderRoute(new URL('/collection?range=30d', env.window.location), {push: true});
  await lateCollection.requested;
  await enter('/?range=30d');
  lateCollection.restore();
  lateCollection.release({rate_limits: {}, sync: {machines: [{name: 'departed', admitted: true, stale: true}]}});
  await loadingCollection;
  assert.equal(ui.qs('#today-cost').textContent, '$2.00');
  assert.equal(ui.qs('#spend-coverage').hidden, true);
  assert.equal(ui.qs('#spend-coverage').textContent, '');
  assert.deepEqual(env.errors, []);
  for (const url of ['/not-a-route', 'http://other/collection', '/collection#cost-diagnostics']) {
    const begin = env.requests.length, scope = ui.pageScope();
    assert.equal(await ui.renderRoute(new URL(url, env.window.location), {push: true}), false);
    assert.equal(env.requests.length, begin);
    assert.equal(scope(), true, 'unhandled navigation must retain current page');
  }
  // Positive control: a missing public route handler must be observable as a
  // failed page, even though the navigation was already claimed by the shell.
  const pivot = env.window.AgentMonitorPivot, report = console.error;
  env.window.AgentMonitorPivot = {};
  try {
    console.error = () => {};
    await ui.renderRoute(new URL('/explore', env.window.location), {push: true});
    assert.equal(env.errors.length, 1);
    assert.match(env.errors[0], /页面未能完整加载/);
  } finally {
    env.window.AgentMonitorPivot = pivot;
    console.error = report;
  }
  ui.endPageLifetime();
  assert.equal(env.intervals.size, 0);
  assert.equal(env.windowEvents.get('focus').size, 0);
  assert.equal(env.documentEvents.get('visibilitychange').size, 0);
  console.log('routes: 10 paths; overview/collection reentry and 2 late responses; rejected targets: 3; 30s watchers and chart cleanup; missing-handler control: detected');
}

run().catch(error => {console.error(error); process.exitCode = 1;});
