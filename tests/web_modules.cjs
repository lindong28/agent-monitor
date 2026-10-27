const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const scripts = ['charts', 'quota', 'network', 'sessions', 'app', 'pivot'];
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
