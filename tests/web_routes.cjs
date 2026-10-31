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
    querySelector(selector) { return this.children.find(child => '#' + child.id === selector) || null; },
    querySelectorAll() { return []; },
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
      for (const attr of match[2].matchAll(/data-([\w-]+)="([^"]*)"/g)) {
        node.dataset[attr[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = attr[2];
      }
      node.parentElement = main;
      nodes.set('#' + node.id, node);
    }
    if (nodes.has('#pivot-chart')) {
      const box = element();
      box.className = 'chart-box';
      box.appendChild(nodes.get('#pivot-chart'));
      nodes.get('#pivot-chart').closest = selector => selector === '.chart-box' ? box : null;
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
    querySelectorAll: selector => selector === '[data-filter-control]'
      ? [...active.nodes.values()].filter(node => node.dataset.filterControl) : [],
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

// Read production outputs, never function bodies: the same tests tolerate local
// names/control-flow refactors but reject changes in requests and rendered data.
async function checkPivotBehavior(env, ui, enter) {
  const filters = {
    agent: 'codex', project: '/work/team/alpha & <demo>', model: 'model-a', machine: 'mac-one',
  };
  const alternatives = {agent: 'claude', project: '/work/other/beta', model: 'model-b', machine: 'mac-two'};
  env.payloads['/api/pivot-filters'] = Object.fromEntries(
    Object.keys(filters).map(name => [name, [filters[name], alternatives[name]]])
  );
  const request = () => env.requests.filter(url => url.pathname === '/api/pivot').at(-1);
  const assertFilters = expected => {
    for (const name of Object.keys(filters)) {
      assert.equal(ui.qs(`#${name}-filter`).value, expected[name] || '', `${name}: selected control`);
      assert.deepEqual(env.window.location.searchParams.getAll(name), expected[name] ? [expected[name]] : [], `${name}: URL`);
      assert.deepEqual(request().searchParams.getAll(name), expected[name] ? [expected[name]] : [], `${name}: API`);
    }
  };
  const initial = new URLSearchParams({range: 'custom', start: '2026-10-01', end: '2026-10-02', group_by: 'day,project', metric: 'total', ...filters});
  await enter('/explore?' + initial);
  assertFilters(filters);
  for (const [name, value] of Object.entries({range: 'custom', start: '2026-10-01', end: '2026-10-02', group_by: 'day,project', metric: 'total'})) {
    assert.equal(request().searchParams.get(name), value);
  }
  const selected = {...filters};
  for (const name of Object.keys(filters)) {
    ui.qs(`#${name}-filter`).value = alternatives[name];
    selected[name] = alternatives[name];
    const beforeChange = env.requests.length;
    await ui.qs(`#${name}-filter`).listeners.change();
    assert(env.requests.slice(beforeChange).some(url => url.pathname === '/api/pivot'));
    assertFilters(selected);
  }
  for (const name of Object.keys(filters)) {
    ui.qs(`#${name}-filter`).value = '';
    delete selected[name];
    await ui.qs(`#${name}-filter`).listeners.change();
    assertFilters(selected);
  }

  const rows = [
    {key: ['2026-10-01', filters.project], value: 12},
    {key: ['2026-10-02', filters.project], value: 34},
    {key: ['2026-10-01', alternatives.project], value: 7},
  ];
  env.payloads['/api/pivot'] = {dimensions: ['day', 'project'], rows};
  await enter('/explore?group_by=day,project&metric=total');
  const chart = env.window.ttWebCharts.pivot.config;
  assert.deepEqual(chart.data.datasets.map(dataset => dataset.label), ['会话目录：team/alpha & <demo>', '会话目录：other/beta']);
  assert.deepEqual(chart.data.datasets.map(dataset => dataset.fullLabel), [filters.project, alternatives.project].map(project => '会话目录：' + project));
  const tooltip = chart.options.plugins.tooltip.callbacks;
  assert.equal(tooltip.title([{dataIndex: 0}]), '2026-10-01');
  assert(tooltip.label({dataset: chart.data.datasets[0], raw: 12, dataIndex: 0}).includes(filters.project));
  // Exercise project labels on the horizontal/vertical axis as well as series.
  const pivot = env.window.AgentMonitorPivot;
  const projects = {dimensions: ['project'], rows: [
    {key: [filters.project], value: 12}, {key: ['relative-name'], value: 3},
  ]};
  pivot.renderChart(pivot.chartData(projects, 'total'), 'project', 'total');
  const projectChart = env.window.ttWebCharts.pivot.config;
  assert.deepEqual(projectChart.data.labels, ['team/alpha & <demo>', 'relative-name']);
  assert.equal(projectChart.options.plugins.tooltip.callbacks.title([{dataIndex: 0}]), filters.project);
  assert.equal(projectChart.options.plugins.tooltip.callbacks.title([{dataIndex: 1}]), 'relative-name');

  for (const [dimension, label] of [['day', '每日明细'], ['week', '每周明细'], ['month', '每月明细']]) {
    env.payloads['/api/pivot'] = {dimensions: [dimension, 'project'], rows};
    await enter(`/explore?group_by=${dimension},project&metric=total`);
    const html = ui.qs('#group-ranking').innerHTML;
    const details = [...html.matchAll(/<details\b[^>]*>([\s\S]*?)<\/details>/g)].map(match => match[1]);
    assert.equal(details.length, 2, `${dimension}: one disclosure per group`);
    assert(details[0].includes(`<summary>${label} · 2 期</summary>`));
    assert(details[1].includes(`<summary>${label} · 1 期</summary>`));
    const periods = [...details[0].matchAll(/<div class="ranking-period"><span>([^<]+)<\/span><span class="reading-amount">([^<]+)<\/span><\/div>/g)];
    assert.deepEqual(periods.map(match => match.slice(1)), [['2026-10-01', '12'], ['2026-10-02', '34']]);
    assert(html.includes('/work/team/alpha &amp; &lt;demo&gt;'), 'ranking must retain and escape the full project name');
    assert.equal(ui.qs('#ranking-count').textContent, '2 个组合');
  }
  env.payloads['/api/pivot'] = projects;
  await enter('/explore?group_by=project&metric=total');
  assert(!ui.qs('#group-ranking').innerHTML.includes('<details'), 'no time dimension means no period disclosure');

  // Refresh in place, so stale charts/data cannot hide behind a route teardown.
  env.payloads['/api/pivot'] = {dimensions: ['day', 'project'], rows};
  await enter('/explore?group_by=day,project&metric=total');
  const previous = env.window.ttWebCharts.pivot;
  env.payloads['/api/pivot'] = {dimensions: ['day', 'project'], rows: []};
  await ui.qs('#metric').listeners.change();
  assert.equal(previous.destroyed, 1);
  assert.equal(env.window.ttWebCharts.pivot, undefined);
  const canvas = ui.qs('#pivot-chart'), empty = canvas.closest('.chart-box').querySelector('#pivot-chart-empty');
  assert.equal(canvas.hidden, true);
  assert.equal(empty.hidden, false);
  assert.equal(empty.textContent, '当前范围内无记录，请检查日期与筛选条件。');
  assert.equal(ui.qs('#pivot-status').textContent, '当前日期与筛选范围内无记录');
  assert.equal(ui.qs('#group-ranking').innerHTML, '<li class="empty-state">当前范围内无记录，请检查日期与筛选条件。</li>');
  assert.equal(ui.qs('#ranking-count').textContent, '0 个组合');
  env.payloads['/api/pivot'] = {dimensions: ['day', 'project'], rows};
  await ui.qs('#metric').listeners.change();
  assert.equal(canvas.hidden, false);
  assert.equal(empty.hidden, true);
  assert.equal(ui.qs('#ranking-count').textContent, '2 个组合');
  console.log('pivot: 4 filters x 3 states; project labels on series/axis; 3 period dimensions + no-time; populated/empty/populated refresh');
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
    if (url === '/network') {
      assert(!env.requests.slice(begin).some(request => request.pathname === endpoint), 'local network view must not automatically probe the server');
      const details = ui.qs('#server-network');
      details.open = true; details.listeners.toggle?.();
      assert(!env.requests.slice(begin).some(request => request.pathname === endpoint), 'disclosure must not trigger server detection');
      await ui.qs('#refresh').listeners.click();
      await new Promise(resolve => setImmediate(resolve));
    }
    assert(env.requests.slice(begin).some(request => request.pathname === endpoint), `${url}: route did not call its API`);
    assert.equal(ui.qs(selector).textContent, reading, `${url}: route did not render`);
  }
  await checkPivotBehavior(env, ui, enter);
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
