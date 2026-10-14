import json
import subprocess
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import server
import sync


class ProgressiveFreshnessTests(unittest.TestCase):
    def test_explore_queries_start_together_after_status_barrier_with_url_filters(self):
        result = self.run_browser(BROWSER_NODES + r'''
const pending = [], requested = [];
qs("#metric").selectedOptions = [{textContent: "成本"}];
const params = new URLSearchParams("range=30d&group_by=project&machine=selected");
const status = {machines: [{name: "selected", admitted: true, generation_id: "one"}], syncing: false};
global.AgentMonitor = {
  pageScope: () => () => true, qs, qsa: () => [], params: () => params,
  autoTimeDim: () => "day", getRange: () => qs("#range").value,
  integer: value => String(value),
  costCoverageText: () => "定价覆盖未知",
  bindShell() {}, watchPageData() {}, renderSyncStatus() {},
  snapshotFingerprint: s => JSON.stringify(s.machines),
  api: (path, query) => {requested.push({path, query}); return new Promise(resolve => pending.push({path, resolve}));},
};
eval(fs.readFileSync("web/pivot.js", "utf8"));
(async () => {
  const loading = window.AgentMonitorPivot.init();
  if (requested.length !== 1 || requested[0].path !== "/api/sync-status") throw Error("queries crossed status barrier");
  pending.shift().resolve(status);
  await new Promise(resolve => setImmediate(resolve));
  const first = requested.slice();
  if (first.length !== 3) throw Error("independent requests were serialized");
  for (const call of pending.splice(0)) call.resolve(call.path === "/api/pivot" ? {dimensions: ["project"], rows: []}
    : call.path === "/api/pivot-filters" ? {machine: ["selected"]} : status);
  await new Promise(resolve => setImmediate(resolve));
  for (const call of pending.splice(0)) call.resolve(status);
  await loading;
  process.stdout.write(JSON.stringify({first, selected: qs("#machine-filter").value, ready: qs("#pivot-status").textContent}));
})().catch(e => {console.error(e); process.exit(1);});
''')
        self.assertEqual([item["path"] for item in result["first"]],
                         ["/api/sync-status", "/api/pivot-filters", "/api/pivot"])
        self.assertEqual(result["first"][2]["query"]["machine"], "selected")
        self.assertEqual(result["selected"], "selected")
        self.assertEqual(result["ready"], "无数据 · 定价覆盖未知")

    def test_statistics_polling_ignores_quota_only_but_default_observes_it(self):
        result = self.run_browser(r'''
const fs = require("fs");
global.window = {location: {origin: "http://test"}};
global.document = {readyState: "loading", addEventListener() {}, querySelector() {return null;}};
global.setTimeout = fn => fn();
let replies = [];
global.fetch = async () => ({ok: true, json: async () => replies.shift()});
eval(fs.readFileSync("web/app.js", "utf8"));
function status(quota, gen, syncing) {return {instance_id: "one", syncing,
  machines: [{name: "one", admitted: true, generation_id: gen}], quota_refresh: {requested: quota}};}
(async () => {
  const counts = [];
  for (const includeQuota of [false, true]) {
    replies = [status(2, "old", true), status(2, "new", false)];
    let count = 0;
    await window.AgentMonitor.waitForSyncTerminal(status(1, "old", true), {includeQuota, onProgress: async () => count++});
    counts.push(count);
  }
  process.stdout.write(JSON.stringify(counts));
})().catch(e => {console.error(e); process.exit(1);});
''')
        self.assertEqual(result, [1, 2])

    def run_browser(self, script):
        result = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def setUp(self):
        server._reset_sync_state_for_tests()
        self.addCleanup(server._reset_sync_state_for_tests)

    def test_hub_interval_is_start_to_start_even_after_a_slow_or_failed_round(self):
        server._SYNC_STATE.update(started_at="2026-09-28T00:00:00Z", completed_at="2026-09-28T00:01:10Z")
        with mock.patch("server.hub.enabled", return_value=True), mock.patch("server.hub.interval", return_value=120):
            for seconds, expected in ((119, False), (120, True), (180, True)):
                now = datetime.fromtimestamp(1790553600 + seconds, timezone.utc)
                self.assertEqual(server._automatic_sync_due(now), expected)
            server._SYNC_STATE["running"] = True
            self.assertFalse(server._automatic_sync_due(now))

    def test_sync_reports_fast_machine_before_slow_machine_finishes(self):
        release = threading.Event()
        fast_reported = threading.Event()
        machines = [SimpleNamespace(name="fast"), SimpleNamespace(name="slow")]
        def collect(machine, **kwargs):
            if machine.name == "slow":
                self.assertTrue(release.wait(2))
            return SimpleNamespace(host=machine.name)
        def completed(name, result):
            if name == "fast":
                fast_reported.set()
        with mock.patch("sync.load_machine_config", return_value=SimpleNamespace(machines=machines)), mock.patch("sync.sync_machine", side_effect=collect):
            thread = threading.Thread(target=lambda: sync.sync_all(on_complete=completed))
            thread.start()
            try:
                self.assertTrue(fast_reported.wait(1))
                self.assertTrue(thread.is_alive())
            finally:
                release.set()
                thread.join(3)
            self.assertFalse(thread.is_alive())

    def test_statistics_progress_uses_cached_quota_and_request_stays_pending(self):
        events = []
        server._SYNC_STATE.update(running=True, round_quota_refresh=True, round_refresh=1,
                                  refresh_requested=1, pending_machines=("fast", "slow"),
                                  statistics_pending=("fast", "slow"), phase="statistics")
        def collect(*, quota_refresh, progressive, on_complete, on_statistics):
            events.append(quota_refresh)
            self.assertFalse(progressive)
            self.assertEqual(server._SYNC_STATE["refresh_completed"], 0)
            self.assertEqual(server._SYNC_STATE["phase"], "statistics")
            result = server.sync_process.Outcome(True)
            on_statistics("fast", result)
            self.assertIn("fast", server._SYNC_STATE["pending_machines"])
            self.assertNotIn("fast", server._SYNC_STATE["statistics_pending"])
            on_complete("fast", result)
            self.assertNotIn("fast", server._SYNC_STATE["pending_machines"])
            self.assertIn("slow", server._SYNC_STATE["pending_machines"])
            failed = server.sync_process.Outcome(False, "offline")
            on_complete("slow", failed)
            self.assertEqual(server._SYNC_STATE["phase"], "quota")
            self.assertEqual(server._SYNC_STATE["refresh_completed"], 0)
            return {"fast": result, "slow": failed}
        with mock.patch("server.sync_process.sync_all", side_effect=collect), mock.patch("server._remember_accounts_after_sync_publish"), mock.patch("server.hub.enabled", return_value=True):
            server._run_sync_round(("fast", "slow"))
        self.assertEqual(events, [False])
        self.assertEqual(server._SYNC_STATE["refresh_completed"], 1)
        self.assertFalse(server._SYNC_STATE["running"])
        self.assertEqual(server._SYNC_STATE["observations"]["fast"]["last_attempt_outcome"], "success")
        self.assertEqual(server._SYNC_STATE["observations"]["slow"]["last_attempt_outcome"], "failure")
        self.assertEqual(server._SYNC_STATE["observations"]["slow"]["reason"], "offline")

    def test_browser_rereads_each_new_generation_before_terminal_and_stops_on_navigation(self):
        script = r'''
const fs = require("fs");
global.window = { location: { origin: "http://test" } };
global.document = { readyState: "loading", addEventListener() {}, querySelector() { return null; } };
global.setTimeout = (fn) => { fn(); };
let replies = [];
global.fetch = async () => ({ ok: true, json: async () => replies.shift() });
eval(fs.readFileSync("web/app.js", "utf8"));
function status(id, running) { return {instance_id: "one", syncing: running,
  refresh_requested: 1, refresh_completed: running ? 0 : 1,
  machines: [{name: "fast", admitted: true, generation_id: id}]}; }
(async () => {
  const seen = [];
  replies = [status("new", true), status("new", true), status("last", false)];
  await window.AgentMonitor.waitForSyncTerminal(status("old", true), {
    onProgress: async s => seen.push([s.machines[0].generation_id, s.syncing]),
  });
  replies = [status("ignored", true)];
  await window.AgentMonitor.waitForSyncTerminal(status("old", true), {
    isCurrent: () => false, onProgress: () => { throw Error("stale page write"); },
  });
  process.stdout.write(JSON.stringify({seen, unread: replies.length}));
})().catch(e => { console.error(e); process.exit(1); });
'''
        result = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"seen": [["new", True], ["last", False]], "unread": 1})

    def test_manual_refresh_rejects_status_failure_without_claiming_completion(self):
        result = self.run_browser(r'''
const fs = require("fs");
global.window = {location: {origin: "http://test"}};
global.document = {readyState: "loading", addEventListener() {}, querySelector() {return null;}};
global.setTimeout = fn => fn();
global.fetch = async url => {
  if (url.pathname === "/api/sync-status") throw Error("network failure");
  return {ok: true, json: async () => ({instance_id: "one", syncing: true,
    refresh_requested: 1, refresh_completed: 0, machines: []})};
};
eval(fs.readFileSync("web/app.js", "utf8"));
window.AgentMonitor.refreshStatistics().then(() => {
  throw Error("incomplete refresh resolved");
}, error => process.stdout.write(JSON.stringify(error.message)));
''')
        self.assertIn("同步状态不可用", result)

    def test_sessions_follow_generations_and_clamp_a_shrinking_page(self):
        result = self.run_browser(BROWSER_NODES + r'''
let timer, count = 101, statusReads = 0;
const timeouts = [], reads = [];
window.setInterval = (fn, ms) => { timer = fn; return 1; };
global.setTimeout = (fn, ms) => {timeouts.push(ms); fn();};
global.fetch = async url => {
  let payload = {};
  if (url.pathname === "/api/sessions") {
    reads.push({query: url.search, statusReads});
    payload = Array.from({length: count}, (_, n) => ({session_id: String(n), agent_id: "codex",
      project: "/repo", model: "model", started_at: "2026-09-28T01:00:00Z",
      tokens: 1, usage_events: 1, cost_usd: 1, estimated: false}));
  }
  if (url.pathname === "/api/sync-status") {
    statusReads++;
    payload = {instance_id: "one", syncing: statusReads < 3, machines: [
      {name: "fast", admitted: true, generation_id: statusReads === 1 ? "old" : "new"}]};
  }
  return {ok: true, json: async () => payload};
};
eval(fs.readFileSync("web/app.js", "utf8"));
(async () => {
  await window.AgentMonitor.initSessions();
  nodes["#page-next"].listeners.click();
  const before = nodes["#page-status"].textContent;
  count = 1;
  await timer();
  process.stdout.write(JSON.stringify({before, after: nodes["#page-status"].textContent,
    count: nodes["#session-count"].textContent, timeouts,
    progressive: reads.some(r => r.statusReads === 2 && r.query.includes("sync=0"))}));
})().catch(e => {console.error(e); process.exit(1);});
''')
        self.assertEqual(result["timeouts"], [2000, 2000])
        self.assertTrue(result["progressive"])
        self.assertEqual(result["before"], "第 101–101 条，共 101 条")
        self.assertEqual(result["after"], "第 1–1 条，共 1 条")
        self.assertEqual(result["count"], "1 个会话")






BROWSER_NODES = r'''
const fs = require("fs");
function node() {
  const n = {children: [], listeners: {}, value: "", textContent: "", className: "", dataset: {},
    options: [{textContent: "All", cloneNode: node}],
    addEventListener(k, fn) {this.listeners[k] = fn;}, setAttribute() {},
    appendChild(child) {this.children.push(child);},
    removeChild(child) {this.children.splice(this.children.indexOf(child), 1);},
    cloneNode: node, querySelector() {return null;}, get firstChild() {return this.children[0];}};
  let html = "";
  Object.defineProperty(n, "innerHTML", {get() {return html;}, set(v) {html = v; this.children = [];}});
  return n;
}
const nodes = {};
function qs(selector) {return nodes[selector] ||= node();}
qs("#range").value = "30d";
qs("#sort").value = "time";
global.window = {location: {origin: "http://test", pathname: "/sessions", search: ""},
  history: {replaceState() {}}, addEventListener() {}};
global.document = {readyState: "loading", hidden: false, addEventListener() {},
  querySelector: qs, querySelectorAll() {return [];}, createElement: node,
  createTextNode(text) {return {textContent: text};}};
'''
