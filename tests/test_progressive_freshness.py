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

    def test_statistics_are_published_before_quota_and_request_stays_pending(self):
        events = []
        server._SYNC_STATE.update(running=True, round_quota_refresh=True, round_refresh=1,
                                  refresh_requested=1, pending_machines=("fast", "slow"), phase="statistics")
        def collect(*, quota_refresh, on_complete):
            events.append(quota_refresh)
            self.assertEqual(server._SYNC_STATE["refresh_completed"], 0)
            self.assertEqual(server._SYNC_STATE["phase"], "quota" if quota_refresh else "statistics")
            result = sync.SyncResult(generation=mock.Mock())
            on_complete("fast", result)
            self.assertNotIn("fast", server._SYNC_STATE["pending_machines"])
            self.assertIn("slow", server._SYNC_STATE["pending_machines"])
            failed = sync.SyncResult(error="offline")
            on_complete("slow", failed)
            return {"fast": result, "slow": failed}
        with mock.patch("server.sync.sync_all", side_effect=collect), mock.patch("server._remember_accounts_after_sync_publish"), mock.patch("server.hub.enabled", return_value=True):
            server._run_sync_round(("fast", "slow"))
        self.assertEqual(events, [False, True])
        self.assertEqual(server._SYNC_STATE["refresh_completed"], 1)
        self.assertFalse(server._SYNC_STATE["running"])
        self.assertEqual(server._SYNC_STATE["observations"]["slow"]["last_attempt_outcome"], "failure")

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

    def test_old_background_sync_cannot_discard_new_range_options(self):
        result = self.run_browser(BROWSER_NODES + r'''
let watcher, reload, range = "all", syncing = false, syncOptions, finishSync, finishPage;
const params = new URLSearchParams("project=old");
const turn = () => new Promise(resolve => setImmediate(resolve));
function calls(count) {
  const empty = {items: [], matching_count: count, next_cursor: null};
  return {ledger: {state: "available"}, requests: empty, attempts: empty,
    request_summary: {}, attempt_summary: {}, range: {value: range},
    cost_summary: {monetary_subtotals: []}, request_selection: {}};
}
function page(project, count) {return {filters: {request_dimensions: {projects: [project]},
  attempt_dimensions: {}}, calls: calls(count)};}
global.AgentMonitor = {qs, pageScope: () => () => true, getRange: () => range,
  params: () => params, setParam: (k,v) => v ? params.set(k,v) : params.delete(k),
  integer: String, bindShell(fn) {reload = fn;}, renderSyncStatus() {},
  watchPageData(fn) {watcher = fn;},
  waitForSyncTerminal(status, options) {syncOptions=options;
    return new Promise(resolve => {finishSync=resolve;});}};
global.fetch = async url => {
  let payload;
  if (url.pathname === "/api/sync-status") payload={syncing};
  else if (url.pathname === "/api/llm-calls-page") {
    payload = range === "all" ? page("old",1) : await new Promise(resolve => {finishPage=resolve;});
  } else payload = calls(range === "all" ? 1 : 0);
  return {ok:true,json:async()=>payload};
};
eval(fs.readFileSync("web/llm-calls.js","utf8"));
(async()=>{
  await window.AgentMonitorLLMCalls.init();
  syncing=true; const background=watcher(); await turn();
  range="7d"; const foreground=reload(); await turn();
  const oldObserverCurrent=syncOptions.isCurrent();
  if (oldObserverCurrent) await syncOptions.onProgress();
  syncing=false; finishPage(page("new",1)); await foreground;
  finishSync({syncing:false}); await background;
  process.stdout.write(JSON.stringify({oldObserverCurrent, project:params.get("project"),
    options:nodes["#llm-project"].children.slice(1).map(n=>n.value),
    count:nodes["#llm-request-count"].textContent}));
})().catch(e=>{console.error(e);process.exit(1);});
''')
        self.assertEqual(result, {"oldObserverCurrent": False, "project": None,
                                  "options": ["new"], "count": "1"})

    def test_selection_during_range_load_reloads_options_and_blocks_old_paging(self):
        for action, order in (("clear", "new-first"), ("filter", "new-first"),
                              ("clear", "old-first"), ("filter", "old-first")):
            with self.subTest(action=action, order=order):
                result = self.run_browser(BROWSER_NODES + "const [action,order]=" + json.dumps([action, order]) + ";" + r'''
let watcher, reload, range="all";
const pending=[], params=new URLSearchParams("project=old");
const tick=()=>new Promise(r=>setImmediate(r));
function page(project) {
  const rows={items:[],matching_count:1,next_cursor:"old-cursor"};
  return {filters:{request_dimensions:{projects:[project]},attempt_dimensions:{}},
    calls:{ledger:{state:"available"},requests:rows,attempts:rows,
      request_summary:{},attempt_summary:{},range:{value:range},
      cost_summary:{monetary_subtotals:[]},request_selection:{}}};
}
global.AgentMonitor={qs,pageScope:()=>()=>true,getRange:()=>range,params:()=>params,
  setParam:(k,v)=>v?params.set(k,v):params.delete(k),integer:String,
  bindShell(fn){reload=fn;},renderSyncStatus(){},watchPageData(fn){watcher=fn;}};
global.fetch=async url=>{
  let payload={syncing:false};
  if(url.pathname==="/api/llm-calls-page") payload=range==="all"?page("old"):
    await new Promise(resolve=>pending.push({resolve,query:url.search}));
  else if(url.pathname==="/api/llm-calls") throw Error("lost options via calls-only load");
  return {ok:true,json:async()=>payload};
};
eval(fs.readFileSync("web/llm-calls.js","utf8"));
(async()=>{
  await window.AgentMonitorLLMCalls.init();range="7d";
  const first=reload();await tick();
  const pagerDisabled=nodes["#llm-requests-next"].disabled;
  nodes["#llm-requests-next"].listeners.click();
  if(action==="clear") nodes["#llm-clear-filters"].listeners.click();
  else {const control=nodes['[data-filter="project"]'];control.value="new";control.listeners.change();}
  await tick();
  if(pending.length!==2) throw Error("selection did not replace pending page");
  if(order==="new-first") {
    pending[1].resolve(page("new"));await tick();
    pending[0].resolve(page("stale"));await first;await tick();
  } else {
    pending[0].resolve(page("stale"));await first;await tick();
    await watcher();
    pending[1].resolve(page("new"));await tick();
  }
  process.stdout.write(JSON.stringify({pagerDisabled,project:params.get("project"),
    query:pending[1].query,options:nodes["#llm-project"].children.slice(1).map(n=>n.value),
    count:nodes["#llm-request-count"].textContent}));
})().catch(e=>{console.error(e);process.exit(1);});
''')
                self.assertTrue(result["pagerDisabled"])
                self.assertEqual(result["options"], ["new"])
                self.assertEqual(result["count"], "1")
                self.assertEqual(result["project"], "new" if action == "filter" else None)
                self.assertEqual("project=new" in result["query"], action == "filter")

    def test_llm_initial_and_background_reads_follow_progress(self):
        result = self.run_browser(BROWSER_NODES + r'''
let watcher, polls = 0, running = true;
const calls = [];
global.AgentMonitor = {qs, pageScope: () => () => true, getRange: () => "30d",
  params: () => new URLSearchParams(), integer: String, bindShell() {},
  renderSyncStatus() {}, watchPageData(fn) {watcher = fn;},
  async waitForSyncTerminal(status, options) {
    polls++; await options.onProgress(); running = false; return {syncing: false};
  }};
global.fetch = async url => {
  let payload = {request_dimensions: {}, attempt_dimensions: {}};
  if (url.pathname === "/api/sync-status") payload = {syncing: running};
  if (["/api/llm-calls", "/api/llm-calls-page"].includes(url.pathname)) {
    calls.push(running);
    const empty = {items: [], matching_count: 0, next_cursor: null};
    payload = {ledger: {state: "not_collected"}, requests: empty, attempts: empty,
      request_summary: {}, attempt_summary: {}, range: {value: "30d"},
      cost_summary: {monetary_subtotals: []}, request_selection: {}};
    if (url.pathname === "/api/llm-calls-page") payload = {
      filters: {request_dimensions: {}, attempt_dimensions: {}}, calls: payload};
  }
  return {ok: true, json: async () => payload};
};
eval(fs.readFileSync("web/llm-calls.js", "utf8"));
(async () => {
  await window.AgentMonitorLLMCalls.init();
  running = true; await watcher();
  process.stdout.write(JSON.stringify({polls, calls}));
})().catch(e => {console.error(e); process.exit(1);});
''')
        self.assertEqual(result, {"polls": 2, "calls": [True, True, False, True, True, False]})


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
