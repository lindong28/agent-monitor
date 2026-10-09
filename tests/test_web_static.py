import json
import re
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WebStaticTests(unittest.TestCase):
    def test_chart_style_preserves_single_observation_and_caller_options(self):
        script = r'''
const fs = require("fs"), assert = require("assert");
global.window = { location: { origin: "http://example.test", pathname: "/", search: "" }, history: { replaceState() {} } };
global.document = { readyState: "loading", addEventListener() {} };
eval(fs.readFileSync("web/app.js", "utf8"));
const ui = window.AgentMonitor;
for (const values of [[0], [null, 8, null], [undefined, 4]]) {
  assert(ui.dataset("sample", values, 0).pointRadius > 0, "one valid observation must remain visible");
}
for (const values of [[], [null, undefined], [0, 8]]) {
  assert.strictEqual(ui.dataset("sample", values, 0).pointRadius, 0);
}
const title = () => "full project path", tick = x => `${x} tokens`;
const config = ui.chartOptions({indexAxis: "y", plugins: {legend: {display: false}, tooltip: {callbacks: {title}}}, scales: {x: {ticks: {callback: tick}}, y: {ticks: {autoSkip: false}}}});
assert.strictEqual(config.plugins.tooltip.callbacks.title, title);
assert.strictEqual(config.plugins.tooltip.enabled, true);
assert.strictEqual(config.scales.x.ticks.callback, tick);
assert.strictEqual(config.scales.y.ticks.autoSkip, false);
assert.strictEqual(config.plugins.legend.display, false);
assert.strictEqual(config.scales.x.beginAtZero, true);
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_chart_data_readings_preserve_unknown_zero_and_full_labels(self):
        script = r'''
const fs = require("fs"), assert = require("assert");
function node(tag) {
  return {tag, children: [], textContent: "", appendChild(child) {this.children.push(child); return child;},
    replaceChildren(...children) {this.children = children;}};
}
const table = node("table");
global.window = {location: {origin: "http://example.test", pathname: "/", search: ""}, history: {replaceState() {}}};
global.document = {readyState: "loading", addEventListener() {},
  querySelector(selector) {return selector === "#sample table" ? table : null;}, createElement: node};
eval(fs.readFileSync("web/app.js", "utf8").replace("window.AgentMonitor = {", "window.AgentMonitor = { chartReading, renderChartTable, categoryTick,"));
const ui = window.AgentMonitor;
assert.strictEqual(ui.chartReading(null, "USD"), "未知");
assert.strictEqual(ui.chartReading(undefined, "tokens"), "未知");
assert.strictEqual(ui.chartReading(NaN, "tokens"), "未知");
assert.strictEqual(ui.chartReading(0, "USD"), "$0.0000");
assert.strictEqual(ui.chartReading(12003, "tokens"), "12,003 tokens");
assert.strictEqual(ui.chartReading(1195.4737, "USD"), "$1,195.4737");
assert.strictEqual(ui.money(.001), "<$0.01");
assert.strictEqual(ui.money(0), "$0.00");
assert.strictEqual(ui.moneyPrecise(1e-9), "$1.00e-9");
const full = "host/owner/a-long-project-that-must-not-be-truncated-in-the-data-table";
ui.renderChartTable("sample", ["会话目录", "成本 · USD"], [[full, ui.chartReading(0, "USD")], ["missing", ui.chartReading(null, "USD")]]);
assert.strictEqual(table.children[0].children[0].children[0].scope, "col");
assert.strictEqual(table.children[1].children[0].children[0].scope, "row");
assert.strictEqual(table.children[1].children[0].children[0].textContent, full);
assert.strictEqual(table.children[1].children[1].children[1].textContent, "未知");
ui.renderChartTable("sample", ["模型", "tokens"], []);
assert.strictEqual(table.children[1].children.length, 1);
assert.strictEqual(table.children[1].children[0].children[0].colSpan, 2);
assert.strictEqual(table.children[1].children[0].children[0].textContent, "该范围暂无数据");
ui.renderChartTable("missing", [], []); // Older cached HTML has no data-table slot.
global.getComputedStyle = () => ({fontFamily: "sans-serif"});
const ctx = {save() {}, restore() {}, measureText(text) {return {width: text.length * 12};}};
for (const width of [254, 324, 1000]) {
  const scale = {chart: {width, ctx}, maxWidth: (width - 64) / 2, getLabelForValue: () => full};
  const label = ui.categoryTick.call(scale, 0);
  assert(ctx.measureText(label).width <= Math.min(220, width * .38, scale.maxWidth - 16));
  assert(label.includes("…"));
}
assert.strictEqual(ui.categoryTick.call({chart: {width: 324, ctx}, getLabelForValue: () => "短名"}, 0), "短名");
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_small_cost_ticks_and_readings_do_not_round_positive_values_to_zero(self):
        script = r'''
const fs = require("fs"), assert = require("assert");
global.window = {location: {origin: "http://example.test", pathname: "/", search: ""}, history: {replaceState() {}}};
global.document = {readyState: "loading", addEventListener() {}};
const source = fs.readFileSync("web/app.js", "utf8");
// Extract the callback actually wired to dailyCost, rather than testing an unused formatter.
const daily = source.slice(source.indexOf('chart("dailyCost"'), source.indexOf('const costMeta'));
const match = daily.match(/y: \{ ticks: \{ callback: (.*?) \} \}, x:/);
assert(match, "dailyCost currency callback exists");
const ui = eval(source.slice(0, source.lastIndexOf('})();')) + '\nreturn {chartReading, callback: (' + match[1] + ')};\n})();');
for (const values of [[0, .001, .002, .003, .004], [0, .000001, .000002, .000003], [0, 10, 20, 30]]) {
  const labels = values.map(ui.callback);
  assert.strictEqual(new Set(labels).size, values.length, `distinct ticks collapsed: ${labels}`);
  assert(labels.every(label => label.startsWith("$")));
}
for (const value of [1e-5, 1e-9, Number.MIN_VALUE]) {
  const label = ui.chartReading(value, "USD");
  assert(Number(label.replace(/^\$/, "").replace(/ USD$/, "")) > 0, `positive value rendered as zero: ${label}`);
}
assert.strictEqual(ui.chartReading(0, "USD"), "$0.0000");
assert.strictEqual(ui.chartReading(null, "USD"), "未知");
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_collapsed_account_summary_exposes_load_failure_and_recovers(self):
        script = r'''
const fs = require("fs"), assert = require("assert");
function node() {return {dataset: {}, children: [], textContent: "", addEventListener() {}, replaceChildren(...children) {this.children = children;}};}
const nodes = Object.fromEntries(["#codex-account-list", "#codex-account-notice", "#codex-batch-start", "#codex-batch-next", "#codex-batch-retry", "#codex-batch-status", "#codex-batch-help", "#codex-account-unavailable"].map(id => [id, node()]));
nodes["#codex-batch-status"].textContent = "正在读取账号…";
const root = node(); root.querySelector = id => nodes[id];
global.document = {hidden: false, querySelector: () => root, addEventListener() {}, createElement: node};
global.window = {AgentMonitor: {pageScope: () => () => true, ensureTimezone: async () => {}, onPageCleanup() {}, pageInterval() {}, formatDate: x => x}};
let failure = true;
global.fetch = async (url, options) => {
  assert.strictEqual(options.method, "GET");
  if (failure) throw Error("test unavailable");
  return {ok: true, json: async () => ({accounts: [], unavailable_accounts: [], batch: {id: "b1", created_at: "2026-10-09T00:00:00Z", items: [{profile_id: "former", operation: {message_status: "succeeded"}}]}})};
};
let source = fs.readFileSync("web/codex-accounts.js", "utf8").replace("export function init()", "function init()");
source = source.replace("  load();\n  window.AgentMonitor.pageInterval(load, 30000);", "  window.testLoad = load;");
eval(source); init();
(async () => {
  await window.testLoad();
  assert(nodes["#codex-batch-status"].textContent.includes("读取失败"));
  assert(!nodes["#codex-batch-status"].textContent.includes("正在读取"));
  failure = false; await window.testLoad();
  const good = nodes["#codex-batch-status"].textContent;
  assert(good.includes("本轮发送成功 1/1"));
  assert(!good.includes("读取失败"));
  failure = true; await window.testLoad();
  const stale = nodes["#codex-batch-status"].textContent;
  assert(stale.includes(good), "preserve the last batch result when refresh fails");
  assert(stale.includes("刷新失败"));
  await window.testLoad();
  assert.strictEqual(nodes["#codex-batch-status"].textContent, stale, "repeated failure must not duplicate notices");
  failure = false; await window.testLoad();
  assert.strictEqual(nodes["#codex-batch-status"].textContent, good);
  assert.strictEqual(nodes["#codex-account-notice"].textContent, "");
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_account_rows_keep_snapshot_time_errors_and_disclosure_state(self):
        script = r'''
const fs = require("fs"), assert = require("assert");
class Node {
  constructor(tag = "div") {this.tag = tag; this.children = []; this.dataset = {}; this.textContent = ""; this.open = false;}
  append(...nodes) {for (const n of nodes) {n.parent = this; this.children.push(n);}}
  replaceChildren(...nodes) {this.children = []; this.append(...nodes);}
  addEventListener() {}
  querySelector(tag) {for (const n of this.children) {if (n.tag === tag) return n; const nested = n.querySelector(tag); if (nested) return nested;} return null;}
  replaceWith(n) {const p = this.parent, index = p.children.indexOf(this); p.children[index] = n; n.parent = p;}
  remove() {this.parent.children.splice(this.parent.children.indexOf(this), 1);}
  insertBefore(n, ref) {if (n.parent) n.remove(); const index = ref ? this.children.indexOf(ref) : this.children.length; this.children.splice(index, 0, n); n.parent = this;}
}
const nodes = Object.fromEntries(["#codex-account-list", "#codex-account-notice", "#codex-batch-start", "#codex-batch-next", "#codex-batch-retry", "#codex-batch-status", "#codex-batch-help", "#codex-account-unavailable"].map(id => [id, new Node()]));
const root = new Node(); root.querySelector = id => nodes[id];
global.document = {hidden: false, querySelector: () => root, addEventListener() {}, createElement: tag => new Node(tag)};
global.window = {AgentMonitor: {pageScope: () => () => true, onPageCleanup() {}, formatDate: x => x}};
let source = fs.readFileSync("web/codex-accounts.js", "utf8").replace("export function init()", "function init()");
source = source.replace("  load();\n  window.AgentMonitor.pageInterval(load, 30000);", "  window.testRender = values => {accounts = values; render();};");
eval(source); init();
const oldTime = "2026-10-08T02:00:00.000Z";
const values = [
  {id: "a", email: "a@example.test", has_credentials: true, operation: {stage: "succeeded", message_status: "succeeded", started_at: oldTime, after: {seven_day_used_pct: 8, observed_at: oldTime}}},
  {id: "b", email: "b@example.test", has_credentials: true, operation: {stage: "failed", message_status: "unknown", detail: "失败；结果未知", started_at: oldTime, after: {seven_day_used_pct: null, observed_at: oldTime}}},
];
const text = n => n.textContent + n.children.map(text).join(" ");
const visible = n => n.tag === "details" && !n.open ? text(n.children[0]) : n.textContent + n.children.map(visible).join(" ");
window.testRender(values);
const list = nodes["#codex-account-list"], card = list.children[0];
assert(visible(card).includes("上次查询 · 七天已用 8%"));
assert(visible(card).includes(oldTime), "snapshot observation time is visible without opening details");
assert(visible(list.children[1]).includes("失败；结果未知"));
assert(visible(list.children[1]).includes("七天已用 未知"));
assert(!text(list).includes("将在操作时检查有效性"), "common saved-login explanation belongs outside the repeated records");
card.querySelector("details").open = true;
window.testRender(values);
assert.strictEqual(list.children[0], card, "unchanged poll must preserve the focused card");
values[0].operation.after.seven_day_used_pct = 9;
window.testRender(values);
assert(list.children[0].querySelector("details").open, "changed result must preserve detail expansion");
assert(visible(list.children[0]).includes("七天已用 9%"));
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_batch_status_and_chart_tables_remain_keyboard_readable(self):
        from html.parser import HTMLParser

        class Tree(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack = []
                self.locations = {}

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if attrs.get("id"):
                    self.locations[attrs["id"]] = (list(self.stack), attrs)
                if tag not in {"meta", "link", "input", "br", "hr"}:
                    self.stack.append((tag, attrs))

            def handle_endtag(self, tag):
                for index in range(len(self.stack) - 1, -1, -1):
                    if self.stack[index][0] == tag:
                        del self.stack[index:]
                        break

        tree = Tree()
        tree.feed((ROOT / "web/index.html").read_text())
        parents, attrs = tree.locations["codex-batch-status"]
        self.assertEqual(parents[-1][0], "summary")
        self.assertEqual(parents[-2][1]["id"], "codex-account-actions")
        self.assertEqual(attrs["aria-live"], "polite")
        for name in ("daily-cost-data", "top-projects-data", "model-mix-data"):
            self.assertIn("chart-data-disclosure", tree.locations[name][1]["class"])

    def test_session_detail_preserves_refresh_collection_and_page_watch(self):
        script = r'''
const fs = require("fs"), assert = require("assert");
const calls = [], timers = [], listeners = [];
function node() {
  return { hidden: false, textContent: "", innerHTML: "", value: "30d",
    addEventListener(k, fn) { this[k] = fn; }, setAttribute() {}, appendChild() {} };
}
const nodes = Object.fromEntries([
  "#session-list-panel", "main h1", "#range", "#sort", "main",
  "#session-identity", "#session-copy", "#session-detail-content", "#refresh"
].map(k => [k, node()]));
global.window = {
  location: { origin: "http://example.test", pathname: "/sessions",
    search: "?session=s1&session_machine=macbook",
    href: "http://example.test/sessions?session=s1&session_machine=macbook" },
  history: { replaceState() {} },
  setInterval(fn, ms) { timers.push({fn, ms}); },
  addEventListener(k, fn) { listeners.push({k, fn}); }
};
global.document = {
  readyState: "loading", addEventListener(k) { listeners.push({k}); },
  querySelector(k) { return nodes[k] || null; }, querySelectorAll() { return []; },
  createElement() { return node(); }
};
global.fetch = async u => {
  calls.push(String(u));
  return { ok: true, json: async () => String(u).includes("/api/timezone")
    ? {timezone: "UTC"} : String(u).includes("/api/refresh")
    ? {machines: [], refresh_requested: 1, refresh_completed: 1, syncing: false}
    : {entries: []} };
};
eval(fs.readFileSync("web/app.js", "utf8"));
(async () => {
  await window.AgentMonitor.initSessions(); calls.length = 0;
  await nodes["#refresh"].click();
  assert.deepStrictEqual(calls, ["http://example.test/api/refresh",
    "http://example.test/api/session/s1?machine=macbook"]);
  assert.deepStrictEqual(timers.map(t => t.ms), [30000]);
  assert(listeners.some(t => t.k === "focus"));
  assert(listeners.some(t => t.k === "visibilitychange"));
  calls.length = 0; await timers[0].fn();
  assert.deepStrictEqual(calls, ["http://example.test/api/session/s1?machine=macbook"]);
})().catch(e => { console.error(e); process.exitCode = 1; });
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_llm_scope_follows_the_actual_response(self):
        script = r'''
const fs = require("fs");
const nodes = {};
global.window = { location: { origin: "http://example.test" } };
global.AgentMonitor = { qs(selector) { return nodes[selector] ||= {}; } };
eval(fs.readFileSync("web/llm-calls.js", "utf8").replace(
  "window.AgentMonitorLLMCalls = { init };", "window.AgentMonitorLLMCalls = { init, setTerminal };"));
const results = ["this_machine_only", "admitted_machines"].map(kind => {
  window.AgentMonitorLLMCalls.setTerminal({scope: {kind}, sources: [], ledger: {state: "missing"},
    requests: {matching_count: 0}, attempts: {matching_count: 0}});
  return [nodes["#llm-scope"].textContent, nodes["#llm-scope-detail"].textContent];
});
process.stdout.write(JSON.stringify(results));
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        local, hub = json.loads(result.stdout)
        self.assertEqual(local[0], "仅本机")
        self.assertIn("不含其它机器", local[1])
        self.assertEqual(hub[0], "全部已纳入机器")
        self.assertIn("最后一次采集的快照", hub[1])
        html = (ROOT / "web/llm-calls.html").read_text()
        for name in ("llm-scope", "llm-scope-detail"):
            self.assertIn('id="' + name + '"', html)

    def test_overview_shows_only_codex_weekly_quota(self):
        """Codex reports no 5h window, so no Codex row may offer a value there.

        Every row emits every column so they align; which of them a provider
        actually has is a membership test, and the cell for one it does not have
        says so rather than sitting empty like missing data.
        """
        js = (ROOT / "web" / "app.js").read_text()

        claude = self.quota_provider_config(js, "claude")
        codex = self.quota_provider_config(js, "codex")

        self.assertIn("QUOTA_WINDOW_5H", claude)
        self.assertIn("QUOTA_WINDOW_7D", claude)
        self.assertNotIn("QUOTA_WINDOW_5H", codex)
        self.assertIn("QUOTA_WINDOW_7D", codex)
        # The config constrains rendering only if the renderer reads it. Without
        # these, a row that emitted a value for every column would leave the
        # assertions above true while every Codex row grew a 5h figure.
        self.assertIn("QUOTA_COLUMNS.forEach((spec) => row.appendChild(quotaWindowCell", js)
        # "n/a" and the em dash are different facts: no such window at all, vs
        # no reading for one that exists. One glyph in two weights carries
        # neither to a screen reader nor to anyone who cannot hover a tooltip.
        self.assertIn('cell.textContent = "不适用"', js)
        self.assertIn("window.key === spec.key", js)
        self.assertIn("没有 ${spec.key} 窗口", js)

    def test_overview_spend_names_the_machines_it_is_not_counting(self):
        """Spend totals are fleet sums; a machine whose refresh failed is
        silently absent from them. The qualifier lives beside the figures, not
        only in the sync panel a section above."""
        script = r'''
const fs = require("fs");
const nodes = {
  "#sync-coverage": { textContent: "" },
  "#sync-summary": { textContent: "" },
  "#sync-machines": { innerHTML: "" },
  "#spend-coverage": { textContent: "", hidden: true },
};
global.window = { location: { origin: "http://example.test", pathname: "/", search: "" }, history: { replaceState() {} } };
global.document = {
  readyState: "loading", addEventListener() {},
  querySelector(selector) { return nodes[selector] || null; },
  querySelectorAll() { return []; },
};
eval(fs.readFileSync("web/app.js", "utf8"));
function base(name) {
  return { name, admitted: true, availability: "reachable", stale: false, syncing: false, reason: null,
    generated_at: "2026-09-14T08:15:42Z", last_attempt_outcome: "success", totals_schema_version: 2,
    generation_totals_basis: "project_independent_usage", generation_legacy_fallback_bucket_count: 0 };
}
function status(machines) {
  return { coverage: { admitted: machines.length, declared: machines.length }, syncing: false, machines };
}
const behind = Object.assign(base("macbook"), { availability: "unreachable", stale: true, reason: "Export refused" });
const excluded = Object.assign(base("dgx0023"), { admitted: false, availability: "never", generated_at: null });
window.AgentMonitor.renderSyncStatus(status([behind, base("macmini"), excluded]));
const shown = { text: nodes["#spend-coverage"].textContent, hidden: nodes["#spend-coverage"].hidden };
// Stale after a server restart: reachable is unknown and nothing has failed,
// so the note must not claim a refresh failure.
const restarted = Object.assign(base("macmini"), { stale: true, availability: "unknown", last_attempt_outcome: "unknown_since_restart" });
window.AgentMonitor.renderSyncStatus(status([restarted]));
const staleOnly = { text: nodes["#spend-coverage"].textContent, hidden: nodes["#spend-coverage"].hidden };
window.AgentMonitor.renderSyncStatus(status([base("macbook"), base("macmini")]));
const healthy = { text: nodes["#spend-coverage"].textContent, hidden: nodes["#spend-coverage"].hidden };
process.stdout.write(JSON.stringify({ shown, staleOnly, healthy }));
'''
        self.assertIn('id="spend-coverage"', (ROOT / "web" / "index.html").read_text())
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["shown"]["hidden"])
        self.assertIn("macbook", payload["shown"]["text"])
        self.assertIn("2026", payload["shown"]["text"])
        self.assertIn("可能低于实际花费", payload["shown"]["text"])
        self.assertFalse(payload["staleOnly"]["hidden"])
        self.assertIn("macmini", payload["staleOnly"]["text"])
        self.assertNotIn("失败", payload["staleOnly"]["text"])
        # Only admitted machines are missing from a total; an excluded one never
        # contributed, and the coverage count already says so.
        self.assertNotIn("dgx0023", payload["shown"]["text"])
        self.assertNotIn("macmini", payload["shown"]["text"])
        self.assertTrue(payload["healthy"]["hidden"])
        self.assertEqual(payload["healthy"]["text"], "")

    def test_chart_box_says_so_when_the_chart_library_did_not_load(self):
        """The production reading was three empty panels and no message when
        /web/vendor/chart.umd.min.js returned 404. Empty must not read as
        "nothing spent"."""
        script = r'''
const fs = require("fs");
// The box already holds Explore's hidden no-data element, which shares the
// .chart-empty-state class; the library note must still be added beside it.
const box = {
  children: [{ id: "pivot-chart-empty", className: "empty-state chart-empty-state", hidden: true }],
  querySelector(sel) { return this.children.find((c) => (" " + c.className + " ").includes(" " + sel.slice(1) + " ")) || null; },
  appendChild(n) { this.children.push(n); },
};
const canvas = { parentElement: box };
global.window = { location: { origin: "http://example.test", pathname: "/", search: "" }, history: { replaceState() {} } };
global.document = {
  readyState: "loading", addEventListener() {},
  querySelector(selector) { return selector === "#daily-cost-chart" ? canvas : null; },
  querySelectorAll() { return []; },
  createElement(tag) { return { tag, className: "", textContent: "" }; },
};
eval(fs.readFileSync("web/app.js", "utf8"));
window.AgentMonitor.chart("dailyCost", "daily-cost-chart", { type: "line" });
window.AgentMonitor.chart("dailyCost", "daily-cost-chart", { type: "line" });
process.stdout.write(JSON.stringify(box.children));
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        children = json.loads(result.stdout)
        self.assertEqual(len(children), 2, "one note per box, not one per render")
        self.assertIn("chart.umd.min.js", children[1]["textContent"])
        self.assertIn("chart-library-missing", children[1]["className"])

    def test_overview_range_card_names_the_window_it_reports(self):
        """A cost figure whose window comes from a control elsewhere on the page
        has to carry that window in its own heading, and must not claim a wider
        one than the stored history covers.

        Driven through renderRangeCost rather than grepped: the label is built,
        not literal, and the coverage qualifier appears only for some payloads.
        """
        script = r'''
const fs = require("fs");

function makeNode() {
  return { textContent: "" };
}

const nodes = {
  "#range-label": makeNode(),
  "#range-cost": makeNode(),
  "#range-context": makeNode(),
};

global.window = {
  location: { origin: "http://example.test", pathname: "/", search: "" },
  history: { replaceState() {} },
};
global.document = {
  readyState: "loading",
  body: { appendChild() {} },
  addEventListener() {},
  querySelector(selector) { return nodes[selector] || null; },
  querySelectorAll() { return []; },
  createElement() { return makeNode(); },
};

const source = fs.readFileSync("web/app.js", "utf8").replace(
  "window.AgentMonitor = {",
  "window.AgentMonitor = { renderRangeCost,",
);
eval(source);

const spend = { cost_usd: 54057.3423242, tokens: 68056341922 };

function read(range, selected, coverage) {
  Object.values(nodes).forEach((node) => { node.textContent = ""; });
  window.AgentMonitor.renderRangeCost(range, selected, coverage);
  return {
    label: nodes["#range-label"].textContent,
    cost: nodes["#range-cost"].textContent,
    context: nodes["#range-context"].textContent,
  };
}

const partial = { earliest_date: "2026-04-21", partial_before_range: true };
const complete = { earliest_date: "2026-04-21", partial_before_range: false };

process.stdout.write(JSON.stringify({
  thirtyDays: read(spend, "30d", complete),
  allHistory: read(spend, "all", complete),
  allHistoryPartial: read(spend, "all", partial),
  noCoverage: read(spend, "30d", null),
  // What `_rollup_coverage` returns on a host that has never rolled up:
  // a full dict that knows nothing. The zeros are what such a host reports.
  emptyRollup: read({ cost_usd: 0, tokens: 0 }, "all",
    { earliest_date: null, range_start: null, partial_before_range: false }),
  // Positively partial but with no date to name — no current producer emits
  // this, but the branch must not read it as "covered".
  partialNoDate: read(spend, "all", { partial_before_range: true, earliest_date: null }),
  legacyPayload: read(undefined, "30d", complete),
}));
'''
        html = (ROOT / "web" / "index.html").read_text()
        # The stub below answers to these three selector strings whatever the
        # markup says, so the behavioural half of this test passes even if an id
        # is renamed. Pin them here, as #quota-accounts already is.
        for element_id in ("range-label", "range-cost", "range-context"):
            self.assertIn(f'id="{element_id}"', html)
        self.assertNotIn('id="today-tokens"', html)
        self.assertNotIn('id="week-tokens"', html)
        self.assertIn('id="week-context"', html)

        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)

        # The heading, not the toolbar, says which window the figure covers.
        self.assertEqual(payload["thirtyDays"]["label"], "近 30 天成本")
        self.assertEqual(payload["allHistory"]["label"], "全部历史成本")
        self.assertEqual(payload["thirtyDays"]["cost"], "$54,057.34")

        # "All history" is the widest claim on the page and the one this host
        # cannot honour: the rollup starts at a collection date. The qualifier
        # travels with the figure rather than living a screen below it.
        self.assertEqual(payload["allHistory"]["context"], "")
        self.assertEqual(payload["allHistoryPartial"]["context"], "自 2026-04-21 起")
        self.assertNotIn("tokens", payload["allHistoryPartial"]["context"])
        # Silence about coverage is not a report of full coverage. The branch
        # keys on what coverage said, not on whether the object arrived — the
        # producer returns a full dict even when it knows nothing.
        self.assertEqual(payload["noCoverage"]["context"], "覆盖范围未知")
        self.assertEqual(payload["partialNoDate"]["context"], "覆盖范围未知")
        self.assertEqual(payload["thirtyDays"]["context"], "")

        # An empty rollup DB is the case that matters most, because its numbers
        # are zeros: "we collected nothing" must not render as "you spent
        # nothing ever" under the widest heading the page can show.
        self.assertEqual(payload["emptyRollup"]["label"], "全部历史成本")
        self.assertEqual(payload["emptyRollup"]["cost"], "$0.00")
        self.assertEqual(payload["emptyRollup"]["context"], "覆盖范围未知")

        # A server that predates the field must not leave a live-looking heading
        # over a zero or a stale figure.
        self.assertEqual(payload["legacyPayload"]["cost"], "—")
        self.assertEqual(payload["legacyPayload"]["context"], "此服务未提供")

        js = (ROOT / "web" / "app.js").read_text()
        self.assertIn('自周一 00:00 · GMT+8', js)

    def test_overview_quota_is_a_table_with_a_column_per_window(self):
        """Alignment across rows is what the reader compares on. A table gives
        it by construction; the card layout it replaced needed fixed CSS slots
        to simulate it, and got it wrong by 260px before that."""
        html = (ROOT / "web" / "index.html").read_text()
        js = (ROOT / "web" / "app.js").read_text()
        prose = " ".join(html.split())

        self.assertIn('<table id="quota-accounts"', prose)
        header = re.search(r"<thead>\s*<tr>(.*?)</tr>\s*</thead>", prose).group(1)
        self.assertEqual(
            re.findall(r">([^<]+)</th>", header),
            ["服务商", "套餐", "账号", "5h 已用", "7d 已用", "机器", "更新于"],
        )
        # Two files decide one thing. The header row is static markup; the cells
        # under it are emitted in QUOTA_COLUMNS order. Reorder one and every
        # figure lands under the wrong heading with nothing failing — the values
        # are all still there, just relabelled.
        columns = re.search(r"const QUOTA_COLUMNS = \[([^\]]+)\]", js).group(1)
        self.assertEqual(
            [name.strip() for name in columns.split(",") if name.strip()],
            ["QUOTA_WINDOW_5H", "QUOTA_WINDOW_7D"],
        )
        self.assertIn("renderQuotaAccounts", js)
        # A value belongs under the header of the window it came from. The
        # collapsed group's highest-usage pill was placed in the second cell
        # regardless of which window produced it, so a 5h figure could sit under
        # "7d used" — in a table the column is the claim.
        self.assertIn("worst.spec === spec", js)
        # The one thing the numbers cannot say for themselves: rows are per
        # account and must not be added up.
        self.assertIn("配额按账号计量，不跨账号求和。", prose)

    def test_overview_quota_reads_as_consumption(self):
        """The section states what has been used directly, without making the
        reader invert a remaining percentage before comparing accounts."""
        html = (ROOT / "web" / "index.html").read_text()
        js = (ROOT / "web" / "app.js").read_text()
        prose = " ".join(html.split())

        self.assertIn("配额用量", prose)
        self.assertNotIn("剩余", prose)
        # The figure in the cell is the rounded source usage, not its inverse.
        self.assertIn("`${used}%`", js)
        self.assertIn("const used = Math.round(usedPct)", js)
        # The bar must run the same way as the number beside it. Filling with
        # what remains would pair a long bar with a small used percentage.
        self.assertIn("quotaMeter(used)", js)

    def test_overview_quota_flags_low_headroom_in_words_not_only_colour(self):
        """A row the reader must act on has to survive for a reader who cannot
        see the colour it is drawn in."""
        js = (ROOT / "web" / "app.js").read_text()

        self.assertIn("余量偏低", js)
        self.assertIn("即将用尽", js)
        self.assertIn("QUOTA_BAND_CRITICAL", js)
        self.assertIn("QUOTA_BAND_LOW", js)

    def test_the_plan_cell_states_a_disagreement_and_stays_silent_otherwise(self):
        """Three states, and only one of them is ever asserted on the page.

        The row's plan and its quota figures come from one event; the
        credential file is a separate clock. One event, not one
        observation — a figure whose window has since reset is rewritten
        to zero while the plan keeps what the event reported. When the two plans disagree the cell says so
        — in words, in the cell, not only in a hover — because otherwise the
        row reads as one observation and the reader has no way to tell.

        Having nothing to compare against renders the same as agreement — the
        page asserts nothing in either case, and putting a permanent caveat on
        every Claude row (whose reading never carries a plan) would be noise,
        not information. What must not collapse is the payload: the two states
        stay tellable apart there, which `test_account_identity` pins. Here
        the fixtures only have to prove the marker itself does not misfire on
        any of them — signed out, an unreadable credential file, an API-key
        machine, a remembered row, an exporter older than the field.

        The comparison reads the two raw plans, never the derived one: with
        `account_plan` on the left the credential-only case would compare the
        credential against itself and could never disagree, which looks
        identical to working correctly.
        """
        script = r'''
const fs = require("fs");

function makeNode(tagName = "div") {
  let ownText = "";
  const node = {
    tagName: String(tagName).toUpperCase(),
    className: "",
    dataset: {},
    attributes: {},
    children: [],
    style: { values: {}, setProperty(name, value) { this.values[name] = String(value); } },
    listeners: {},
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return this.attributes[name] || null; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
  };
  node.classList = {
    add(...names) {
      const values = new Set(node.className.split(/\s+/).filter(Boolean));
      names.forEach((name) => values.add(name));
      node.className = Array.from(values).join(" ");
    },
    contains(name) { return node.className.split(/\s+/).includes(name); },
  };
  Object.defineProperty(node, "textContent", {
    get() { return ownText + node.children.map((child) => child.textContent || "").join(""); },
    set(value) { ownText = String(value); node.children = []; },
  });
  return node;
}

function descendants(node) {
  return [node].concat(node.children.flatMap((child) => descendants(child)));
}

global.window = {
  location: { origin: "http://example.test", pathname: "/", search: "" },
  history: { replaceState() {} },
};
global.document = {
  readyState: "loading",
  body: makeNode("body"),
  addEventListener() {},
  querySelector() { return null; },
  querySelectorAll() { return []; },
  createElement(tagName) { return makeNode(tagName); },
  createTextNode(text) { const node = makeNode("#text"); node.textContent = text; return node; },
};

const source = fs.readFileSync("web/app.js", "utf8").replace(
  "window.AgentMonitor = {",
  "window.AgentMonitor = { quotaPlanCell,",
);
eval(source);

function reading(account) {
  const cell = window.AgentMonitor.quotaPlanCell(account);
  const marker = descendants(cell)
    .filter((node) => node.className.split(/\s+/).includes("quota-plan-mismatch"))[0];
  return {
    text: cell.textContent,
    marker: marker ? { text: marker.textContent, title: marker.title || "" } : null,
  };
}

const known = {
  account_id: "acct-1",
  account_label: "a@b.c",
  account_state: "known",
  presence: "in_use",
};

process.stdout.write(JSON.stringify({
  disagree: reading({
    ...known, account_plan: "pro", reading_plan: "pro", credential_plan: "prolite",
  }),
  agree: reading({
    ...known, account_plan: "pro", reading_plan: "pro", credential_plan: "pro",
  }),
  // Only the credential has a plan, and the derived value copies it. Comparing
  // account_plan against the credential here would compare "prolite" with
  // itself — silent for the wrong reason, and indistinguishable from working.
  credentialOnly: reading({
    ...known, account_plan: "prolite", reading_plan: null, credential_plan: "prolite",
  }),
  readingOnly: reading({
    ...known, account_plan: "pro", reading_plan: "pro", credential_plan: null,
  }),
  // An older exporter sends neither raw field, and its account_plan still
  // carries the old meaning — the credential plan. Using "pro" here would
  // pin nothing: it is the value the new semantics produce.
  olderExporter: reading({ ...known, account_plan: "prolite" }),
  // Both of these mirror what the exporter can actually emit. Signed out means
  // no account object, so there is no credential plan to carry; unstamped means
  // a block old enough to have no account key at all, which cannot have these
  // fields either. Fixtures that gave them a credential plan would have pinned
  // the account_state guard against a shape no producer reaches.
  signedOut: reading({
    ...known,
    account_state: "signed_out",
    account_plan: "pro",
    reading_plan: "pro",
    credential_plan: null,
  }),
  unstamped: reading({ ...known, account_state: "unstamped", account_plan: "prolite" }),
  remembered: reading({
    ...known,
    presence: "remembered",
    account_plan: "pro",
    reading_plan: null,
    credential_plan: null,
  }),
  planless: reading({ ...known, account_plan: null, credential_plan: "prolite" }),
  nonString: reading({
    ...known, account_plan: "pro", reading_plan: "pro", credential_plan: 7,
  }),
}));
'''
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)

        # Disagreement is stated, with both values, and both are named in
        # readable text rather than left to the reader to infer from a badge.
        disagree = payload["disagree"]
        self.assertIsNotNone(disagree["marker"])
        visible = disagree["marker"]["text"]
        # The claim itself, not two values left side by side for the reader to
        # draw the conclusion from — and in the cell, because a title is not a
        # channel on touch or from the keyboard.
        self.assertIn("不一致", visible)
        # Both sources named where they are read, so the visible text stands on
        # its own: which value came from the reading, which from the machine.
        self.assertIn("Pro Lite", visible)
        self.assertIn("Pro", visible)
        self.assertIn("读数", visible)
        self.assertIn("凭据", visible)
        # The claim is that they differ, not that one of them is stale: which
        # is older is not knowable from the pair, and the sample that prompted
        # this had the reading newer than the credential file.
        for word in ("早于", "过期", "陈旧", "stale"):
            self.assertNotIn(word, disagree["marker"]["title"])

        # Every other state is silent — and silence here is not a claim of
        # agreement, which is exactly why none of them may raise the marker.
        for case in (
            "agree",
            "credentialOnly",
            "readingOnly",
            "olderExporter",
            "signedOut",
            "unstamped",
            "remembered",
            "nonString",
        ):
            with self.subTest(case=case):
                self.assertIsNone(payload[case]["marker"])

        # No plan at all keeps the existing placeholder (ux-contract G4c);
        # a credential-side plan does not get promoted into the empty slot.
        self.assertEqual(payload["planless"]["text"], "—")

    def test_remembered_quota_rows_are_historical_without_changing_live_alerts(self):
        """A frozen reading is historical context, not a present-tense alarm.

        The same remembered account exercises both sides of its own reset time.
        Live rows are the negative control: their provider/plan badges, stale
        warning and usage warning remain exactly where the existing UI put them.
        """
        script = r'''
const fs = require("fs");

function makeNode(tagName = "div") {
  let ownText = "";
  const node = {
    tagName: String(tagName).toUpperCase(),
    className: "",
    dataset: {},
    attributes: {},
    children: [],
    style: {
      values: {},
      setProperty(name, value) { this.values[name] = String(value); },
    },
    listeners: {},
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return this.attributes[name] || null; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
  };
  node.classList = {
    add(...names) {
      const values = new Set(node.className.split(/\s+/).filter(Boolean));
      names.forEach((name) => values.add(name));
      node.className = Array.from(values).join(" ");
    },
    contains(name) { return node.className.split(/\s+/).includes(name); },
  };
  Object.defineProperty(node, "textContent", {
    get() { return ownText + node.children.map((child) => child.textContent || "").join(""); },
    set(value) { ownText = String(value); node.children = []; },
  });
  Object.defineProperty(node, "cells", {
    get() { return node.children.filter((child) => child.tagName === "TD"); },
  });
  return node;
}

function descendants(node) {
  return [node].concat(node.children.flatMap((child) => descendants(child)));
}

function byClass(node, name) {
  return descendants(node).filter((item) => item.className.split(/\s+/).includes(name));
}

global.window = {
  location: { origin: "http://example.test", pathname: "/", search: "" },
  history: { replaceState() {} },
};
global.document = {
  readyState: "loading",
  body: makeNode("body"),
  addEventListener() {},
  querySelector() { return null; },
  querySelectorAll() { return []; },
  createElement(tagName) { return makeNode(tagName); },
  createTextNode(text) { const node = makeNode("#text"); node.textContent = text; return node; },
};

const source = fs.readFileSync("web/app.js", "utf8").replace(
  "window.AgentMonitor = {",
  "window.AgentMonitor = { quotaAccountRow, quotaWindowCell, resetText,",
);
eval(source);

const now = 2000000000;
Date.now = () => now * 1000;
const futureReset = now + 3600;
const pastReset = now - 3600;
const provider = {
  key: "claude",
  label: "Claude",
  pill: "agent-claude-code",
  windows: [
    { key: "7d", used: "seven_day_used_pct", reset: "seven_day_resets_at" },
    { key: "5h", used: "five_hour_used_pct", reset: "five_hour_resets_at" },
  ],
};
const base = {
  account_id: "historical-account",
  account_label: "history@example.test",
  account_plan: "default_claude_max_20x",
  account_state: "known",
  presence: "remembered",
  // Deliberately unequal, and both still above the "almost out" threshold: with
  // one figure in both windows every assertion below reads the same whichever
  // column each landed in, so the fixture that looks like it guards placement
  // guards nothing. The two values are what make the column order observable.
  five_hour_used_pct: 96,
  five_hour_resets_at: futureReset,
  seven_day_used_pct: 91,
  seven_day_resets_at: futureReset,
  updated_at: "2026-08-20T07:53:00Z",
  machines: [],
  this_machine: null,
};

function reading(row) {
  const windows = row.cells.slice(3, 5);
  return {
    cellCount: row.cells.length,
    rowClass: row.className,
    providerText: row.cells[0].textContent,
    providerPillClass: byClass(row.cells[0], "pill")[0]?.className || "",
    planText: row.cells[1].textContent,
    accountText: row.cells[2].textContent,
    warningTexts: byClass(row, "status-pill")
      .map((node) => node.textContent)
      .filter((text) => text === "余量偏低" || text === "即将用尽"),
    staleWarningCount: descendants(row)
      .filter((node) => node.textContent === "可能早于一次登录变更").length,
    historyMarkerCount: descendants(row).filter((node) => node.textContent === "已登出").length,
    historyNoteCount: descendants(row)
      .filter((node) => node.textContent === "最后观测值，不代表当前状态").length,
    observedTexts: byClass(row, "quota-window-observed").map((node) => node.textContent),
    amounts: windows.map((cell) => byClass(cell, "quota-window-value")[0]?.textContent || null),
    fills: windows.map((cell) => byClass(cell, "quota-meter-fill")[0]?.style.values["--quota-fill"] || null),
    resets: windows.map((cell) => {
      const reset = byClass(cell, "quota-window-reset")[0];
      return reset ? { text: reset.textContent, title: reset.title || "" } : null;
    }),
  };
}

function windowReading(cell) {
  const reset = byClass(cell, "quota-window-reset")[0];
  return {
    warningTexts: byClass(cell, "status-pill")
      .map((node) => node.textContent)
      .filter((text) => text === "余量偏低" || text === "即将用尽"),
    observedTexts: byClass(cell, "quota-window-observed").map((node) => node.textContent),
    amount: byClass(cell, "quota-window-value")[0]?.textContent || null,
    fill: byClass(cell, "quota-meter-fill")[0]?.style.values["--quota-fill"] || null,
    reset: reset ? { text: reset.textContent, title: reset.title || "" } : null,
  };
}

const rememberedFuture = reading(window.AgentMonitor.quotaAccountRow(provider, base));
const rememberedPast = reading(window.AgentMonitor.quotaAccountRow(provider, {
  ...base,
  five_hour_resets_at: pastReset,
  seven_day_resets_at: pastReset,
}));
const rememberedUnknown = reading(window.AgentMonitor.quotaAccountRow(provider, {
  ...base,
  five_hour_resets_at: null,
  seven_day_resets_at: null,
}));
const livePast = reading(window.AgentMonitor.quotaAccountRow(provider, {
  ...base,
  presence: "in_use",
  machines: ["macbook"],
  this_machine: "macbook",
  five_hour_resets_at: pastReset,
  seven_day_resets_at: pastReset,
}));
const planless = reading(window.AgentMonitor.quotaAccountRow(provider, {
  ...base,
  presence: "in_use",
  account_plan: null,
  updated_at: "2099-01-01T00:00:00Z",
}));

let boundaryClockReads = 0;
Date.now = () => {
  boundaryClockReads += 1;
  return boundaryClockReads === 1 ? now * 1000 - 1 : now * 1000 + 1;
};
const rememberedBoundary = windowReading(window.AgentMonitor.quotaWindowCell(
  provider,
  provider.windows[0],
  { ...base, seven_day_resets_at: now },
));
Date.now = () => now * 1000;

process.stdout.write(JSON.stringify({
  rememberedFuture,
  rememberedPast,
  rememberedUnknown,
  rememberedBoundary,
  boundaryClockReads,
  livePast,
  planless,
  futureTitle: window.AgentMonitor.resetText(futureReset),
  pastTitle: window.AgentMonitor.resetText(pastReset),
  boundaryTitle: window.AgentMonitor.resetText(now, now * 1000 - 1),
}));
'''
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        future = payload["rememberedFuture"]
        past = payload["rememberedPast"]
        unknown = payload["rememberedUnknown"]
        boundary = payload["rememberedBoundary"]
        live = payload["livePast"]

        self.maxDiff = None
        self.assertEqual(
            {
                "F1 unknown reset": {
                    "warnings": unknown["warningTexts"],
                    "reset_texts": [reset["text"] for reset in unknown["resets"]],
                    "observed_count": len(unknown["observedTexts"]),
                },
                "F2 boundary reset": {
                    "warnings": boundary["warningTexts"],
                    "reset_text": boundary["reset"]["text"],
                    "observed_count": len(boundary["observedTexts"]),
                    "clock_reads": payload["boundaryClockReads"],
                },
            },
            {
                "F1 unknown reset": {
                    "warnings": [],
                    "reset_texts": ["重置时间未知", "重置时间未知"],
                    "observed_count": 2,
                },
                "F2 boundary reset": {
                    "warnings": ["即将用尽"],
                    "reset_text": "1 分钟后重置",
                    "observed_count": 0,
                    "clock_reads": 1,
                },
            },
        )

        self.assertEqual(future["cellCount"], 7)
        self.assertEqual(future["providerText"], "Claude已登出")
        self.assertIn("agent-claude-code", future["providerPillClass"])
        self.assertEqual(future["planText"], "Max 20×")
        self.assertIn("history@example.test", future["accountText"])
        self.assertEqual(payload["planless"]["planText"], "—")

        for remembered in (future, past, unknown):
            self.assertIn("remembered", remembered["rowClass"])
            self.assertIn("stale", remembered["rowClass"])
            self.assertEqual(remembered["historyMarkerCount"], 1)
            self.assertEqual(remembered["historyNoteCount"], 1)
            self.assertEqual(remembered["staleWarningCount"], 0)
            # 5h first, then 7d — the fixture's two windows carry different
            # values, so this pair fails if the columns are ever swapped without
            # the header being swapped with them.
            self.assertEqual(remembered["amounts"], ["96%", "91%"])
            self.assertEqual(remembered["fills"], ["0.96", "0.91"])

        self.assertEqual(future["warningTexts"], ["即将用尽", "即将用尽"])
        self.assertTrue(all(reset["text"].endswith("后重置") for reset in future["resets"]))
        self.assertEqual(
            [reset["title"] for reset in future["resets"]],
            [payload["futureTitle"], payload["futureTitle"]],
        )

        self.assertEqual(past["warningTexts"], [])
        self.assertEqual([reset["text"] for reset in past["resets"]], ["上次重置时间已过", "上次重置时间已过"])
        self.assertEqual(
            [reset["title"] for reset in past["resets"]],
            [payload["pastTitle"], payload["pastTitle"]],
        )
        self.assertEqual(len(past["observedTexts"]), 2)
        self.assertTrue(all(text.startswith("观测于 ") for text in past["observedTexts"]))

        self.assertEqual(unknown["warningTexts"], [])
        self.assertEqual([reset["text"] for reset in unknown["resets"]], ["重置时间未知"] * 2)
        self.assertEqual([reset["title"] for reset in unknown["resets"]], [""] * 2)
        self.assertEqual(len(unknown["observedTexts"]), 2)
        self.assertTrue(all(text.startswith("观测于 ") for text in unknown["observedTexts"]))

        self.assertEqual(boundary["warningTexts"], ["即将用尽"])
        self.assertEqual(
            boundary["reset"],
            {"text": "1 分钟后重置", "title": payload["boundaryTitle"]},
        )
        self.assertEqual(boundary["observedTexts"], [])
        self.assertEqual(payload["boundaryClockReads"], 1)

        self.assertNotIn("remembered", live["rowClass"])
        self.assertIn("stale", live["rowClass"])
        self.assertEqual(live["providerText"], "Claude")
        self.assertEqual(live["planText"], "Max 20×")
        self.assertEqual(live["warningTexts"], ["即将用尽", "即将用尽"])
        self.assertEqual(live["staleWarningCount"], 1)
        self.assertEqual(live["historyMarkerCount"], 0)
        self.assertEqual(live["historyNoteCount"], 0)
        self.assertEqual(live["observedTexts"], [])

        css = (ROOT / "web" / "styles.css").read_text()
        self.assertIn(".quota-row.remembered", css)
        self.assertIn(".quota-history-note", css)

    def test_overview_renders_all_live_accounts_before_any_remembered_account(self):
        """Rendered live rows include a collapsed unattributed group."""
        script = r'''
const fs = require("fs");

function makeNode(tagName = "div") {
  let ownText = "";
  const node = {
    tagName: String(tagName).toUpperCase(),
    className: "",
    dataset: {},
    attributes: {},
    children: [],
    parentNode: null,
    style: { setProperty() {} },
    listeners: {},
    hidden: false,
    appendChild(child) {
      child.parentNode = this;
      this.children.push(child);
      return child;
    },
    remove() {
      if (this.parentNode) {
        this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
        this.parentNode = null;
      }
    },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return this.attributes[name] || null; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    click() { if (this.listeners.click) this.listeners.click(); },
  };
  node.classList = {
    add(...names) {
      const values = new Set(node.className.split(/\s+/).filter(Boolean));
      names.forEach((name) => values.add(name));
      node.className = Array.from(values).join(" ");
    },
    toggle(name) {
      const values = new Set(node.className.split(/\s+/).filter(Boolean));
      const enabled = !values.has(name);
      enabled ? values.add(name) : values.delete(name);
      node.className = Array.from(values).join(" ");
      return enabled;
    },
  };
  Object.defineProperty(node, "textContent", {
    get() { return ownText + node.children.map((child) => child.textContent || "").join(""); },
    set(value) { ownText = String(value); node.children = []; },
  });
  Object.defineProperty(node, "tBodies", {
    get() { return node.children.filter((child) => child.tagName === "TBODY"); },
  });
  Object.defineProperty(node, "cells", {
    get() { return node.children.filter((child) => child.tagName === "TD"); },
  });
  return node;
}

const table = makeNode("table");
global.window = {
  location: { origin: "http://example.test", pathname: "/", search: "" },
  history: { replaceState() {} },
};
global.document = {
  readyState: "loading",
  body: makeNode("body"),
  addEventListener() {},
  querySelector(selector) { return selector === "#quota-accounts" ? table : null; },
  querySelectorAll() { return []; },
  createElement(tagName) { return makeNode(tagName); },
  createTextNode(text) { const node = makeNode("#text"); node.textContent = text; return node; },
};

const source = fs.readFileSync("web/app.js", "utf8").replace(
  "window.AgentMonitor = {",
  "window.AgentMonitor = { renderQuotaAccounts, removeRenderedAccount,",
);
eval(source);

function account(id, state, machines, presence) {
  const row = {
    account_id: id,
    account_label: id ? `${id}@example.test` : null,
    account_plan: null,
    account_state: state,
    five_hour_used_pct: 10,
    five_hour_resets_at: 4102444800,
    seven_day_used_pct: 20,
    seven_day_resets_at: 4102444800,
    updated_at: "2099-01-01T00:00:00Z",
    machines,
    this_machine: null,
  };
  if (presence !== undefined) row.presence = presence;
  return row;
}

function renderedRows() {
  return table.tBodies.flatMap((body) => body.children
    .filter((row) => row.tagName === "TR")
    .map((row) => ({
      bodyClass: body.className,
      bodyHidden: body.hidden,
      bodyText: body.textContent,
      presence: row.dataset.presence || null,
      accountId: row.dataset.accountId || null,
      columnTotal: row.cells.reduce((total, cell) => total + (cell.colSpan || 1), 0),
    })));
}

window.AgentMonitor.renderQuotaAccounts({
  claude: {
    accounts: [
      account(null, "unstamped", ["legacy-a"], "in_use"),
      account(null, "signed_out", ["legacy-b"], "in_use"),
      account("remembered-claude", "known", [], "remembered"),
    ],
    unavailable_reason: null,
  },
  codex: {
    accounts: [account("live-codex", "known", ["macbook"], "in_use")],
    unavailable_reason: null,
  },
});
const mixed = renderedRows();
const historyToggle = table.tBodies
  .flatMap((body) => body.children)
  .flatMap((row) => row.children)
  .flatMap((cell) => cell.children)
  .find((node) => node.tagName === "BUTTON" && node.textContent.includes("历史账号"));
const historyBody = table.tBodies.find((body) => body.className.includes("quota-history"));
const collapsedHistory = {
  text: historyToggle && historyToggle.textContent,
  expanded: historyToggle && historyToggle.getAttribute("aria-expanded"),
  hidden: historyBody && historyBody.hidden,
};
if (historyToggle) historyToggle.click();
const expandedHistory = {
  expanded: historyToggle && historyToggle.getAttribute("aria-expanded"),
  hidden: historyBody && historyBody.hidden,
};
window.AgentMonitor.removeRenderedAccount(
  "claude",
  "remembered-claude",
  "2099-01-01T00:00:00Z",
);
const afterLastHistoryRemoval = {
  summaryBodies: table.tBodies.filter((body) => body.className === "quota-past-summary").length,
  historyBodies: table.tBodies.filter((body) => body.className === "quota-history").length,
  rememberedRows: renderedRows().filter((row) => row.presence === "remembered").length,
};

window.AgentMonitor.renderQuotaAccounts({
  claude: {
    accounts: [
      account("remembered-first", "known", [], "remembered"),
      account("remembered-second", "known", [], "remembered"),
    ],
    unavailable_reason: "missing Claude",
  },
  codex: { accounts: [], unavailable_reason: "missing Codex" },
});
const partialToggle = table.tBodies
  .flatMap((body) => body.children)
  .flatMap((row) => row.children)
  .flatMap((cell) => cell.children)
  .find((node) => node.tagName === "BUTTON" && node.textContent.includes("历史账号"));
if (partialToggle) partialToggle.click();
window.AgentMonitor.removeRenderedAccount(
  "claude",
  "remembered-first",
  "2099-01-01T00:00:00Z",
);
const remainingToggle = table.tBodies
  .flatMap((body) => body.children)
  .flatMap((row) => row.children)
  .flatMap((cell) => cell.children)
  .find((node) => node.tagName === "BUTTON" && node.textContent.includes("历史账号"));
const remainingHistory = table.tBodies.find((body) => body.className === "quota-history");
const afterPartialHistoryRemoval = {
  text: remainingToggle && remainingToggle.textContent,
  expanded: remainingToggle && remainingToggle.getAttribute("aria-expanded"),
  hidden: remainingHistory && remainingHistory.hidden,
  rememberedRows: renderedRows().filter((row) => row.presence === "remembered").length,
};

window.AgentMonitor.renderQuotaAccounts({
  claude: {
    accounts: [account("old-server-claude", "known", ["macbook"])],
    unavailable_reason: null,
  },
  codex: {
    accounts: [account(null, "signed_out", ["macmini"])],
    unavailable_reason: null,
  },
});
const missingPresence = renderedRows();

window.AgentMonitor.renderQuotaAccounts({
  claude: { accounts: [], unavailable_reason: "missing Claude" },
  codex: { accounts: [], unavailable_reason: "missing Codex" },
});
const unavailable = renderedRows();

window.AgentMonitor.renderQuotaAccounts({
  claude: {
    accounts: [account("remembered-only", "known", [], "remembered")],
    unavailable_reason: "missing Claude",
  },
  codex: { accounts: [], unavailable_reason: "missing Codex" },
});
const onlyRemembered = renderedRows();
window.AgentMonitor.removeRenderedAccount(
  "claude",
  "remembered-only",
  "2099-01-01T00:00:00Z",
);
const afterOnlyRememberedRemoval = renderedRows();

process.stdout.write(JSON.stringify({
  mixed,
  collapsedHistory,
  expandedHistory,
  afterLastHistoryRemoval,
  afterPartialHistoryRemoval,
  missingPresence,
  unavailable,
  onlyRemembered,
  afterOnlyRememberedRemoval,
}));
'''
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)

        mixed = payload["mixed"]
        ordered_presence = [row["presence"] for row in mixed if row["presence"]]
        with self.subTest("collapsed unattributed live group"):
            self.assertEqual(
                ordered_presence,
                ["in_use", "in_use", "in_use", "remembered"],
            )
            collapsed = [row for row in mixed if "quota-unknown" in row["bodyClass"]]
            self.assertEqual(len(collapsed), 3)
            self.assertEqual(
                [row["presence"] for row in collapsed],
                [None, "in_use", "in_use"],
            )
            self.assertEqual(payload["collapsedHistory"], {
                "text": "历史账号（1）",
                "expanded": "false",
                "hidden": True,
            })
            self.assertEqual(payload["expandedHistory"], {
                "expanded": "true",
                "hidden": False,
            })
            self.assertEqual(payload["afterLastHistoryRemoval"], {
                "summaryBodies": 0,
                "historyBodies": 0,
                "rememberedRows": 0,
            })
            self.assertEqual(payload["afterPartialHistoryRemoval"], {
                "text": "历史账号（1）",
                "expanded": "true",
                "hidden": False,
                "rememberedRows": 1,
            })
        with self.subTest("old server payload without presence"):
            self.assertEqual(
                [row["presence"] for row in payload["missingPresence"]],
                ["in_use", "in_use"],
            )
        history_rows = [
            row for row in payload["onlyRemembered"]
            if row["presence"] == "remembered"
        ]
        self.assertEqual(len(history_rows), 1)
        self.assertTrue(history_rows[0]["bodyHidden"])
        self.assertTrue(any(
            "历史账号（1）" in row["bodyText"]
            for row in payload["onlyRemembered"]
        ))
        self.assertEqual(
            [
                row["bodyText"]
                for row in payload["afterOnlyRememberedRemoval"]
            ],
            [
                "Claude不可用missing Claude",
                "Codex不可用missing Codex",
            ],
        )
        for state in (
            payload["mixed"],
            payload["missingPresence"],
            payload["unavailable"],
            payload["onlyRemembered"],
        ):
            with self.subTest("every rendered row spans seven columns"):
                self.assertTrue(state)
                self.assertEqual({row["columnTotal"] for row in state}, {7})

    def test_only_remembered_rows_offer_confirmed_removal(self):
        script = r'''
const fs = require("fs");

function makeNode(tagName = "div") {
  let ownText = "";
  const node = {
    tagName: String(tagName).toUpperCase(),
    className: "",
    dataset: {},
    children: [],
    parentNode: null,
    style: { setProperty() {} },
    listeners: {},
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
    remove() {
      if (this.parentNode) {
        this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
        this.parentNode = null;
      }
    },
    setAttribute(name, value) { this[name] = String(value); },
    addEventListener(name, callback) { this.listeners[name] = callback; },
  };
  node.classList = { add(...names) { node.className += ` ${names.join(" ")}`; } };
  Object.defineProperty(node, "textContent", {
    get() { return ownText + node.children.map((child) => child.textContent || "").join(""); },
    set(value) { ownText = String(value); node.children = []; },
  });
  Object.defineProperty(node, "tBodies", {
    get() { return node.children.filter((child) => child.tagName === "TBODY"); },
  });
  return node;
}

function findButtons(node) {
  return (node.tagName === "BUTTON" ? [node] : []).concat(
    node.children.flatMap((child) => findButtons(child))
  );
}

const confirmMessages = [];
const confirmResults = [false, true, true, true];
const fetchCalls = [];
const quotaTable = makeNode("table");
let finishRemoval;
global.window = {
  location: { origin: "http://example.test", pathname: "/", search: "" },
  history: { replaceState() {} },
  confirm(message) { confirmMessages.push(message); return confirmResults.shift(); },
  alert() {},
};
global.fetch = (url, options) => {
  fetchCalls.push({ url: String(url), options });
  return new Promise((resolve) => {
    finishRemoval = () => resolve({
      ok: true,
      status: 200,
      async json() {
        return { account_label: "old@example.test", observed_at: "2026-08-20T07:53:00Z" };
      },
    });
  });
};
global.document = {
  readyState: "loading",
  body: makeNode("body"),
  addEventListener() {},
  querySelector(selector) { return selector === "#quota-accounts" ? quotaTable : null; },
  querySelectorAll() { return []; },
  createElement(tagName) { return makeNode(tagName); },
  createTextNode(text) { const node = makeNode("#text"); node.textContent = text; return node; },
};

const source = fs.readFileSync("web/app.js", "utf8").replace(
  "window.AgentMonitor = {",
  "window.AgentMonitor = { quotaAccountRow, formatDate,",
);
eval(source);

const account = {
  account_id: "old-account",
  account_label: "old@example.test",
  account_plan: "pro",
  account_state: "known",
  presence: "remembered",
  five_hour_used_pct: 12,
  five_hour_resets_at: 4102444800,
  seven_day_used_pct: 24,
  seven_day_resets_at: 4102444800,
  updated_at: "2026-08-20T07:53:00Z",
  machines: [],
  this_machine: null,
};
const provider = { key: "claude", label: "Claude", pill: "agent-claude-code", windows: [
  { key: "7d", used: "seven_day_used_pct", reset: "seven_day_resets_at" },
  { key: "5h", used: "five_hour_used_pct", reset: "five_hour_resets_at" },
] };

(async () => {
  const body = makeNode("tbody");
  quotaTable.appendChild(body);
  const remembered = window.AgentMonitor.quotaAccountRow(provider, account);
  body.appendChild(remembered);
  const buttons = findButtons(remembered);
  const live = window.AgentMonitor.quotaAccountRow(provider, { ...account, presence: "in_use" });

  await buttons[0].listeners.click();
  const afterCancel = { connected: remembered.parentNode === body, fetchCount: fetchCalls.length };
  const removal = buttons[0].listeners.click();
  await Promise.resolve();
  body.remove();
  const replacementBody = makeNode("tbody");
  const replacement = window.AgentMonitor.quotaAccountRow(provider, account);
  replacementBody.appendChild(replacement);
  quotaTable.appendChild(replacementBody);
  finishRemoval();
  await removal;

  const nextBody = makeNode("tbody");
  const nextRemembered = window.AgentMonitor.quotaAccountRow(provider, account);
  nextBody.appendChild(nextRemembered);
  quotaTable.appendChild(nextBody);
  const nextButtons = findButtons(nextRemembered);
  const liveRemoval = nextButtons[0].listeners.click();
  await Promise.resolve();
  nextBody.remove();
  const liveReplacementBody = makeNode("tbody");
  const liveReplacement = window.AgentMonitor.quotaAccountRow(
    provider,
    { ...account, presence: "in_use" },
  );
  liveReplacementBody.appendChild(liveReplacement);
  quotaTable.appendChild(liveReplacementBody);
  finishRemoval();
  await liveRemoval;

  const versionBody = makeNode("tbody");
  const versionRemembered = window.AgentMonitor.quotaAccountRow(provider, account);
  versionBody.appendChild(versionRemembered);
  quotaTable.appendChild(versionBody);
  const versionButtons = findButtons(versionRemembered);
  const versionRemoval = versionButtons[0].listeners.click();
  await Promise.resolve();
  versionBody.remove();
  const newerBody = makeNode("tbody");
  const newerRemembered = window.AgentMonitor.quotaAccountRow(
    provider,
    { ...account, updated_at: "2026-08-20T09:53:00Z" },
  );
  newerBody.appendChild(newerRemembered);
  quotaTable.appendChild(newerBody);
  finishRemoval();
  await versionRemoval;

  process.stdout.write(JSON.stringify({
    buttonCount: buttons.length,
    liveButtonCount: findButtons(live).length,
    ariaLabel: buttons[0]["aria-label"],
    confirmMessages,
    formattedObservation: window.AgentMonitor.formatDate(account.updated_at),
    afterCancel,
    afterConfirm: {
      originalConnected: body.parentNode === quotaTable && remembered.parentNode === body,
      replacementConnected: replacementBody.parentNode === quotaTable && replacement.parentNode === replacementBody,
      fetchCount: fetchCalls.length,
    },
    afterLiveRerender: {
      replacementConnected: liveReplacementBody.parentNode === quotaTable && liveReplacement.parentNode === liveReplacementBody,
      buttonCount: findButtons(liveReplacement).length,
      fetchCount: fetchCalls.length,
    },
    afterNewerRerender: {
      replacementConnected: newerBody.parentNode === quotaTable && newerRemembered.parentNode === newerBody,
      fetchCount: fetchCalls.length,
    },
    request: fetchCalls[0],
  }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)

        self.assertEqual(payload["buttonCount"], 1)
        self.assertEqual(payload["liveButtonCount"], 0)
        self.assertIn("old@example.test", payload["ariaLabel"])
        self.assertEqual(payload["afterCancel"], {"connected": True, "fetchCount": 0})
        self.assertEqual(
            payload["afterConfirm"],
            {
                "originalConnected": False,
                "replacementConnected": False,
                "fetchCount": 3,
            },
        )
        self.assertEqual(
            payload["afterLiveRerender"],
            {
                "replacementConnected": True,
                "buttonCount": 0,
                "fetchCount": 3,
            },
        )
        self.assertEqual(
            payload["afterNewerRerender"],
            {"replacementConnected": True, "fetchCount": 3},
        )
        for message in payload["confirmMessages"]:
            self.assertIn("old@example.test", message)
            self.assertIn(payload["formattedObservation"], message)
            self.assertIn("7d 已用 24%", message)
            self.assertIn("不可恢复", message)
        self.assertEqual(
            payload["request"]["url"],
            "http://example.test/api/account-memory/remove",
        )
        self.assertEqual(payload["request"]["options"]["method"], "POST")
        self.assertEqual(
            payload["request"]["options"]["headers"]["Content-Type"],
            "application/json",
        )
        self.assertEqual(
            json.loads(payload["request"]["options"]["body"]),
            {
                "provider": "claude",
                "account_id": "old-account",
                "account_label": "old@example.test",
                "observed_at": "2026-08-20T07:53:00Z",
            },
        )

    def test_overview_quota_reset_times_are_durations_with_no_zero_case(self):
        """A clock time makes the reader subtract, and is ambiguous the moment
        the reset is not today. Durations answer the question directly — but
        must never round the nearly-there case down to a bare "0h"."""
        js = (ROOT / "web" / "app.js").read_text()

        # The invariant, not the expression that currently carries it: a floor of
        # one minute, and no clock-time formatting left to be ambiguous across a
        # day boundary. Pinning the whole template would fail a rewrite that is
        # still correct.
        self.assertIn("Math.max(1,", js)
        self.assertNotIn("sameServerDay", js)
        self.assertNotIn("fmtClock", js)

    def test_overview_collapsed_unknown_group_still_raises_high_usage(self):
        """Collapsing rows that have no account must not silence them: a machine
        at 96% used is still at 96% used, and 'the fleet is behind on updates' is
        exactly when it has no account stamp."""
        js = (ROOT / "web" / "app.js").read_text()

        self.assertIn("quotaWorstUsage", js)
        self.assertIn("${worst.machine} 已用 ${worst.used}%", js)
        # Each state names its own machines: one needs agent-monitor updated, the other
        # needs someone to sign in, and one lumped sentence serves neither.
        self.assertIn("更新 ${unstamped.join", js)
        self.assertIn('${signedOut.join("、")} 未登录', js)

    @staticmethod
    def quota_provider_config(js, key):
        """The QUOTA_PROVIDERS entry for one provider, as source text."""
        start = js.index('key: "%s"' % key)
        return js[start : js.index("]", start)]

    def test_overview_keeps_the_latest_successful_response(self):
        js = (ROOT / "web" / "app.js").read_text()

        self.assertIn("overviewGeneration", js)
        self.assertIn("renderedOverviewGeneration", js)
        self.assertIn("generation < renderedOverviewGeneration", js)
        self.assertIn("overview-load-error", js)

    def test_new_javascript_removes_the_visible_legacy_codex_card(self):
        js = (ROOT / "web" / "app.js").read_text()

        self.assertIn('qs("#codex-five-hour")', js)
        self.assertIn('closest(".kpi-card")', js)

    def test_console_primary_content_precedes_low_frequency_operations(self):
        from html.parser import HTMLParser
        class Controls(HTMLParser):
            def __init__(self):
                super().__init__()
                self.advanced = False
                self.primary = []
                self.collapsed = []
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "details" and attrs.get("id") in {"codex-account-actions", "llm-filter-disclosure"}:
                    self.collapsed.append("open" not in attrs)
                    self.advanced = attrs.get("id") == "llm-filter-disclosure"
                if "data-filter" in attrs and not self.advanced:
                    self.primary.append(attrs["data-filter"])
            def handle_endtag(self, tag):
                if tag == "details":
                    self.advanced = False
        overview = (ROOT / "web/index.html").read_text()
        self.assertLess(overview.index('id="top-projects-chart"'), overview.index('id="codex-account-actions"'))
        self.assertLess(overview.index('id="model-mix-chart"'), overview.index('id="codex-account-actions"'))
        parser = Controls()
        parser.feed(overview)
        parser.feed((ROOT / "web/llm-calls.html").read_text())
        self.assertEqual(parser.collapsed, [True, True])
        self.assertEqual(parser.primary, ["machine", "project", "logical_model", "request_outcome"])

    def test_overview_side_panel_links_preserve_selected_range(self):
        html = (ROOT / "web" / "index.html").read_text()

        self.assertIn('href="/explore?x=project&group=none&metric=cost"', html)
        self.assertIn('href="/explore?x=model&group=agent&metric=total"', html)
        # Named individually rather than counted. A bare count pinned the two
        # panel links only as long as nothing else on the page needed the same
        # treatment; the brand link now does, because it is a routed link to `/`
        # and without it clicking the logo silently reset the selected range.
        for link in (
            'data-preserve-range href="/explore?x=project&group=none&metric=cost"',
            'data-preserve-range href="/explore?x=model&group=agent&metric=total"',
            '<a class="brand" data-preserve-range href="/">',
        ):
            self.assertIn(link, html)

    def test_sessions_retention_note_describes_mixed_source_retention(self):
        html = (ROOT / "web" / "sessions.html").read_text()

        self.assertIn("已采集的会话统计不随本地日志清理而丢失", html)
        self.assertIn("采集前就删掉的历史找不回来", html)

    def test_sessions_table_has_an_immediate_loading_state(self):
        html = " ".join((ROOT / "web" / "sessions.html").read_text().split())

        self.assertIn('<tbody id="sessions-body" aria-busy="true">', html)
        self.assertIn('data-session-state="loading"', html)
        self.assertIn("加载中…", html)
        self.assertIn('<button id="page-prev" type="button" disabled>', html)
        self.assertIn('<button id="page-next" type="button" disabled>', html)

    def test_sessions_loader_distinguishes_loading_empty_error_and_retry(self):
        js = (ROOT / "web" / "app.js").read_text()

        self.assertIn('renderSessionState("loading")', js)
        self.assertIn('renderSessionState("empty"', js)
        self.assertIn('renderSessionState("error"', js)
        self.assertIn("isValidSessionsPayload(data)", js)
        self.assertIn("generation !== sessionsGeneration", js)
        self.assertIn('sessionsView.status !== "ready"', js)
        self.assertIn("setSessionFiltersDisabled(true)", js)
        self.assertIn('retry.textContent = "重试"', js)
        self.assertIn('retry.addEventListener("click", load)', js)
        self.assertIn('tbody.setAttribute("aria-busy", state === "loading" ? "true" : "false")', js)

    def test_sessions_endpoint_keeps_live_raw_entries_as_its_source(self):
        server = (ROOT / "server.py").read_text()
        endpoint = server[server.index("def sessions_endpoint") : server.index("def session_detail")]

        self.assertIn("load_all_entries()", endpoint)
        self.assertNotIn("rollup.", endpoint)

    def test_g2_explore_has_four_single_select_filter_controls(self):
        html = (ROOT / "web" / "explore.html").read_text()

        for name, label, all_label in [
            ("agent", "Agent 类型", "全部 Agent"),
            ("project", "会话目录", "全部会话目录"),
            ("model", "模型", "全部模型"),
            ("machine", "机器", "全部机器"),
        ]:
            with self.subTest(name=name):
                self.assertIn(f'<label for="{name}-filter">{label}</label>', html)
                self.assertIn(f'id="{name}-filter"', html)
                self.assertIn(f'data-filter-control="{name}"', html)
                self.assertIn(f'aria-label="{label}"', html)
                self.assertIn(f'<option value="">{all_label}</option>', html)
        self.assertNotIn("multiple", html)
        self.assertNotIn("size=\"2\"", html)
        self.assertNotIn("size=\"4\"", html)
        self.assertNotIn("clear-filters", html)
        self.assertNotIn("filter-summary", html)
        self.assertNotIn('data-filter-control="machine"', (ROOT / "web" / "index.html").read_text())

    def test_pivot_js_wires_filter_controls_to_url_and_api(self):
        js = (ROOT / "web" / "pivot.js").read_text()

        self.assertIn('filterNames = ["agent", "project", "model", "machine"]', js)
        self.assertIn("applyFilterQuery()", js)
        self.assertIn("syncFilterQuery()", js)
        self.assertIn("selectedFilters()", js)
        self.assertIn('query.get(name)', js)
        self.assertIn('url.searchParams.set(name, value)', js)
        self.assertNotIn('query.getAll(name)', js)
        self.assertNotIn('url.searchParams.append(name, value)', js)
        self.assertIn("Object.assign({ group_by, metric, range", js)

    def test_g4_status_surface_and_refresh_terminal_polling_are_present(self):
        overview = (ROOT / "web" / "index.html").read_text()
        explore = (ROOT / "web" / "explore.html").read_text()
        app = (ROOT / "web" / "app.js").read_text()

        for html in (overview, explore):
            self.assertIn('aria-label="机器同步状态"', html)
            self.assertIn('id="sync-coverage"', html)
            self.assertIn('id="sync-machines"', html)
        for token in (
            "coverageElement.textContent = `已纳入 ${coverage.admitted}/${coverage.declared} 台`",
            'statusChip("bad", "未纳入")',
            'statusChip("warn", "刷新失败")',
            'statusChip("warn", "数据过期")',
            'statusChip("warn", "未检查")',
            'statusChip("ok", "可用")',
            "用的是上次的数据",
            "waitForSyncTerminal",
        ):
            self.assertIn(token, app)

    def test_g7_network_is_local_and_sessions_explain_admitted_scope(self):
        for filename in ("network.html",):
            html = (ROOT / "web" / filename).read_text()
            with self.subTest(filename=filename):
                self.assertIn("本机", html)
                self.assertIn("只针对本机", html)
        sessions = (ROOT / "web" / "sessions.html").read_text()
        self.assertIn("全部已纳入机器", sessions)
        self.assertIn('id="filter-machine"', sessions)

    def test_pivot_js_shortens_project_labels_but_keeps_full_title(self):
        js = (ROOT / "web" / "pivot.js").read_text()

        self.assertIn("displayLabel(value, dim)", js)
        self.assertIn("projectDisplayLabel(value)", js)
        self.assertIn("fullLabel", js)
        self.assertIn("escapeHtml(label)", js)

    def test_explore_explains_codex_cost_estimates(self):
        html = (ROOT / "web" / "explore.html").read_text()

        self.assertIn("Codex 成本按已识别模型的费率估算，不代表实际账单", html)
        self.assertIn("GLM-5.1/5.2 按 bundled GLM-5 定价推算", html)

    def test_pivot_js_renders_explicit_empty_states(self):
        js = (ROOT / "web" / "pivot.js").read_text()

        self.assertIn("hasNoPivotData(data)", js)
        self.assertIn("renderEmptyChart()", js)
        self.assertIn("renderRanking(data, metric)", js)
        self.assertIn("无数据", js)
        self.assertIn('class="empty-state"', js)

    def test_usage_event_labels_do_not_call_codex_events_messages(self):
        sessions = (ROOT / "web" / "sessions.html").read_text()
        explore = (ROOT / "web" / "explore.html").read_text()
        app = (ROOT / "web" / "app.js").read_text()

        self.assertIn("用量条目", sessions)
        self.assertIn('<option value="messages">用量条目</option>', explore)
        self.assertIn('const numberFields = ["tokens", "usage_events"]', app)
        self.assertIn("integer(row.usage_events)", app)
        self.assertNotIn("integer(row.messages)", app)

    def test_pivot_ranking_preserves_period_details_without_a_table(self):
        js = (ROOT / "web" / "pivot.js").read_text()

        self.assertIn('class="ranking-periods"', js)
        self.assertIn("row.periods.map", js)
        self.assertNotIn("pivot-table", (ROOT / "web" / "explore.html").read_text())


class ClientNavigationPrerequisitesTests(unittest.TestCase):
    """Every page must carry the code every other page needs.

    Client navigation swaps `<main>` without reloading the document, so a
    `<script>` arriving inside the swapped markup never executes: whatever a
    later tab needs has to be present from the first load. Nothing else enforces
    this, and the failure is quiet — `clientRouteFor` matches on pathname alone,
    so a page missing a script still claims the navigation, tears down the
    current page, pushes the URL, swaps the markup, and only then throws. The
    fallback to a real navigation has already been passed by that point, so the
    reader is left on a rendered page with no data and a URL that says it
    arrived.
    """

    PAGES = ("index.html", "explore.html", "sessions.html", "llm-calls.html", "network.html")
    REQUIRED = ("/web/app.js", "/web/pivot.js", "/web/llm-calls.js", "/web/vendor/chart.umd.min.js")

    def test_every_page_loads_every_route_handler_it_might_have_to_run(self):
        for name in self.PAGES:
            html = (ROOT / "web" / name).read_text(encoding="utf-8")
            for script in self.REQUIRED:
                with self.subTest(page=name, script=script):
                    self.assertIn(f'<script src="{script}"></script>', html)

    def test_the_route_table_names_only_handlers_those_scripts_define(self):
        """The other half: a route whose handler nothing defines.

        Pinning the script set is only half the contract — a route added to the
        table without a matching global fails the same quiet way.
        """
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        table = app.split("const CLIENT_ROUTES", 1)[1].split("]);", 1)[0]
        defined = {
            "initOverview": app,
            "initSessions": app,
            "initNetwork": app,
            "window.AgentMonitorPivot": (ROOT / "web" / "pivot.js").read_text(encoding="utf-8"),
            "window.AgentMonitorLLMCalls": (ROOT / "web" / "llm-calls.js").read_text(encoding="utf-8"),
        }
        for handler, source in defined.items():
            if handler in table:
                with self.subTest(handler=handler):
                    bare = handler.rsplit(".", 1)[-1]
                    self.assertTrue(
                        f"window.{bare} =" in source
                        or f"function {bare}(" in source
                        or f"{bare}," in source,
                        f"{handler} is routed but not defined by its script",
                    )
        # And the table covers exactly the five served paths, so a page that
        # exists cannot silently fall back to a full reload.
        for path in ('["/"', '["/explore"', '["/sessions"', '["/llm-calls"', '["/network"'):
            self.assertIn(path, table)


class SignedOutMachinesReadAsStateNotFailureTests(unittest.TestCase):
    """The reader is told why a machine is absent, without being told to fix it.

    Two things have to be true at once, and each is the other's failure mode.
    Saying nothing leaves "macmini is not in the Claude row" and "macmini's
    Claude number failed to arrive" looking identical. Saying it in the table,
    beside accounts that have quota, puts a machine with nothing to report in
    the same visual class as one that does — and the columns it would leave
    empty are the reason the table exists.
    """

    def render_with(self, claude_signed_out, codex_signed_out, refresh_errors=(), claude_accounts=(), recover=False):
        script = r'''
const fs = require("fs");

function makeNode(tagName = "div") {
  let ownText = "";
  const node = {
    tagName: String(tagName).toUpperCase(),
    className: "", dataset: {}, attributes: {}, children: [], hidden: false,
    style: { setProperty() {} },
    appendChild(child) { node.children.push(child); child.parent = node; return child; },
    removeChild(child) { node.children = node.children.filter((c) => c !== child); },
    remove() { if (node.parent) node.parent.removeChild(node); },
    setAttribute(name, value) { node.attributes[name] = String(value); },
    getAttribute(name) { return node.attributes[name] ?? null; },
    addEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  node.classList = { add() {}, toggle() { return false; } };
  Object.defineProperty(node, "textContent", {
    get() { return ownText + node.children.map((c) => c.textContent || "").join(""); },
    set(value) { ownText = String(value); node.children = []; },
  });
  Object.defineProperty(node, "tBodies", {
    get() { return node.children.filter((c) => c.tagName === "TBODY"); },
  });
  return node;
}

const table = makeNode("table");
const note = makeNode("p");
note.hidden = true;
global.window = {
  location: { origin: "http://example.test", pathname: "/", search: "" },
  history: { replaceState() {} },
};
global.document = {
  readyState: "loading",
  body: makeNode("body"),
  documentElement: makeNode("html"),
  addEventListener() {},
  querySelector(selector) {
    if (selector === "#quota-accounts") return table;
    if (selector === "#quota-signed-out") return note;
    return null;
  },
  querySelectorAll() { return []; },
  createElement(tagName) { return makeNode(tagName); },
  createTextNode(text) { const n = makeNode("#text"); n.textContent = text; return n; },
};

const source = fs.readFileSync("web/app.js", "utf8")
  .replace("window.AgentMonitor = {", "window.AgentMonitor = { renderQuotaAccounts,");
eval(source);

const payload = __PAYLOAD__;
window.AgentMonitor.renderQuotaAccounts(payload);
if (__RECOVER__) {
  payload.claude.refresh_errors = [];
  window.AgentMonitor.renderQuotaAccounts(payload);
}
function all(node) { return [node, ...node.children.flatMap(all)]; }

console.log(JSON.stringify({
  noteHidden: note.hidden,
  noteText: note.textContent,
  tableText: table.textContent,
  rows: all(table).filter(n => n.dataset.accountId).map(n => n.textContent),
  details: all(table).filter(n => n.tagName === "DETAILS").map(n => ({text: n.textContent, open: !!n.open})),
}));
'''
        payload = {
            "claude": {
                "accounts": list(claude_accounts),
                "refresh_errors": list(refresh_errors),
                "signed_out_machines": list(claude_signed_out),
                "unavailable_reason": None,
            },
            "codex": {
                "accounts": [],
                "refresh_errors": [],
                "signed_out_machines": list(codex_signed_out),
                "unavailable_reason": None,
            },
        }
        script = script.replace("__PAYLOAD__", json.dumps(payload))
        script = script.replace("__RECOVER__", json.dumps(recover))
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_the_machines_are_named_outside_the_table_and_not_as_a_failure(self):
        rendered = self.render_with(["tencent"], ["tencent", "macmini"])

        self.assertFalse(rendered["noteHidden"])
        self.assertIn("tencent", rendered["noteText"])
        self.assertIn("macmini", rendered["noteText"])
        self.assertIn("未计入上表", rendered["noteText"])
        # Nothing that reads as something to go and fix.
        for alarm in ("失败", "错误", "更新", "无法确认", "告警", "请", "需"):
            self.assertNotIn(alarm, rendered["noteText"])
        # And no row of its own: the table is for accounts that have quota.
        self.assertNotIn("tencent", rendered["tableText"])

    def test_a_lapsed_sign_in_is_not_described_as_uncounted(self):
        """The machine is named in both places, and only one sentence fits it.

        A lapsed sign-in keeps its row — that frozen reading with its own age is
        the only remaining cue the machine stopped reporting. So the note sits
        directly under a table that *is* counting the machine it names, and
        "not counted above" contradicts the row a few pixels above it.
        """
        rendered = self.render_with(
            ["macstudio"],
            [],
            claude_accounts=[
                {
                    "account_id": "acct-lapsed",
                    "account_label": "me@example.test",
                    "account_state": "known",
                    "presence": "in_use",
                    "five_hour_used_pct": 8,
                    "seven_day_used_pct": 41,
                    "five_hour_resets_at": None,
                    "seven_day_resets_at": None,
                    "updated_at": "2026-09-10T00:00:00Z",
                    "machines": ["macstudio"],
                    # This row's figure is macstudio's own, which is what makes
                    # "stops advancing" the true sentence for it.
                    "reading_from": "macstudio",
                    "this_machine": None,
                }
            ],
        )

        self.assertIn("macstudio", rendered["noteText"])
        self.assertIn("失效", rendered["noteText"])
        self.assertNotIn("未计入上表", rendered["noteText"])
        # Its row is still there — that is what makes the other wording false.
        self.assertIn("macstudio", rendered["tableText"])

    def test_a_lapsed_machine_sharing_an_account_is_not_called_frozen(self):
        """One account across several machines is the documented normal shape.

        The row shows the freshest machine's reading, so a lapsed machine that
        merely appears in that row's machine list has not frozen anything — the
        figure beside the sentence came from its still-working sibling and is
        minutes old. Classifying on list membership said otherwise; the
        discriminator is which machine the shown reading came from.
        """
        rendered = self.render_with(
            ["macstudio"],
            [],
            claude_accounts=[
                {
                    "account_id": "acct-shared",
                    "account_label": "me@example.test",
                    "account_state": "known",
                    "presence": "in_use",
                    "five_hour_used_pct": 8,
                    "seven_day_used_pct": 41,
                    "five_hour_resets_at": None,
                    "seven_day_resets_at": None,
                    "updated_at": "2026-09-15T12:00:00Z",
                    "machines": ["macbook", "macstudio"],
                    # macbook's reading is the one on screen, and macbook is fine.
                    "reading_from": "macbook",
                    "this_machine": None,
                }
            ],
        )

        self.assertIn("macstudio", rendered["noteText"])
        self.assertNotIn("失效", rendered["noteText"])
        self.assertNotIn("停在最后一次读数", rendered["noteText"])
        self.assertIn("未计入上表", rendered["noteText"])

    def test_the_note_disappears_when_every_machine_is_signed_in(self):
        """Empty must render as absent, not as an empty sentence."""
        rendered = self.render_with([], [])

        self.assertTrue(rendered["noteHidden"])
        self.assertEqual(rendered["noteText"], "")

    def test_a_real_refresh_failure_still_reaches_the_table(self):
        """Suppressing the signed-out case must not suppress actual failures."""
        rendered = self.render_with(
            ["tencent"], [], refresh_errors=[{"machine": "macmini", "reason": "timed out."}]
        )

        self.assertIn("macmini", rendered["tableText"])
        self.assertIn("timed out", rendered["tableText"])
        self.assertIn("tencent", rendered["noteText"])

    def test_quota_failure_is_scoped_to_the_displayed_account_reading(self):
        fresh = datetime.now(timezone.utc).isoformat()
        account = {"account_id": "a", "account_label": "a@example.test", "account_state": "known",
                   "presence": "in_use", "machines": ["macbook", "macstudio"],
                   "reading_from": "macstudio", "updated_at": fresh, "five_hour_used_pct": 0, "seven_day_used_pct": 20}
        failure = [{"machine": "macbook", "reason": "timed out"}]
        cases = [
            ({}, [], None),
            ({"reading_from": "macbook"}, [], "更新延迟"),
            ({"reading_from": None}, [], "更新延迟"),
            ({"updated_at": "invalid"}, [], "更新延迟"),
            ({"updated_at": (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()}, [], "更新延迟"),
            ({"five_hour_used_pct": None, "seven_day_used_pct": None}, [], "暂不可用"),
            ({}, ["macstudio"], "更新延迟"),
            ({"machines": ["other"], "reading_from": "other"}, [], None),
            ({"presence": "remembered"}, [], None),
        ]
        for changes, signed_out, expected in cases:
            with self.subTest(changes=changes, signed_out=signed_out):
                rendered = self.render_with(signed_out, [], failure, [{**account, **changes}])
                row = rendered["rows"][0]
                if expected:
                    self.assertIn(expected, row)
                    self.assertIn("可点击刷新重试", row)
                else:
                    self.assertNotIn("更新延迟", row)
                    self.assertNotIn("暂不可用", row)
                self.assertEqual(len(rendered["details"]), 1)
                self.assertFalse(rendered["details"][0]["open"])
                self.assertIn("macbook：timed out", rendered["details"][0]["text"])
                self.assertNotIn("此前读数", rendered["tableText"])

        recovered = self.render_with([], [], failure, [{**account, "reading_from": "macbook"}], recover=True)
        self.assertEqual(recovered["details"], [])
        self.assertEqual(len(recovered["rows"]), 1)
        self.assertNotIn("更新延迟", recovered["tableText"])
        self.assertNotIn("timed out", recovered["tableText"])


if __name__ == "__main__":
    unittest.main()
