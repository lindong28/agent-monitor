"""Account actions explain provider pauses without issuing recovery requests."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AccountPauseWebTests(unittest.TestCase):
    def run_js(self, script):
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_batch_explains_pause_and_other_disabled_states(self):
        self.run_js(r'''
const assert = require("assert");
global.document = {createElement: () => ({})};
global.window = {AgentMonitor: {formatDate: x => x, latestCodexQuotaReading: () => null}};
const {renderAccountBatch} = require("./web/codex-accounts.js");
const controls = {root: {querySelector: () => null}, batchHelp: {replaceChildren() {}}};
for (const key of ["batchStart", "batchNext", "batchRetry", "batchStatus", "blockedReason"]) controls[key] = {};
const state = {accounts: [{id: "a", eligible: true, busy: false}], selectedIds: new Set(["a"]),
  batch: {id: "b", created_at: "2026-10-26T06:10:09Z", items: [{profile_id: "a", operation: {message_status: "succeeded"}}]},
  upstreamPause: {id: "pause-a"}, busy: false};
const render = () => renderAccountBatch(state, controls);
render();
assert.equal(controls.batchNext.disabled, true);
assert.match(controls.blockedReason.textContent, /暂停/);
assert.match(controls.batchStatus.textContent, /暂停/);
assert.match(controls.batchStatus.textContent, /发送成功 1\/1/);
state.upstreamPause = null; render();
assert.equal(controls.batchNext.disabled, false);
assert.equal(controls.blockedReason.textContent, "");
assert(!controls.batchStatus.textContent.includes("暂停"));
state.selectedIds.clear(); render();
assert.equal(controls.batchNext.disabled, true);
assert.match(controls.blockedReason.textContent, /选择/);
state.selectedIds.add("a"); state.accounts[0].busy = true; render();
assert.equal(controls.batchNext.disabled, true);
assert.match(controls.blockedReason.textContent, /操作/);
state.accounts[0].busy = false; state.busy = true; render();
assert.equal(controls.batchNext.disabled, true);
assert.match(controls.blockedReason.textContent, /提交|连接|确认/);
state.batch = null; state.busy = false; state.upstreamPause = {id: "pause-b"}; render();
assert.equal(controls.batchStart.disabled, true);
assert.match(controls.batchStatus.textContent, /暂停/);
''')

    def test_pause_panel_uses_exact_identity_and_keeps_unknown_explicit(self):
        self.run_js(r'''
const assert = require("assert");
global.document = {createElement: () => ({})};
global.window = {AgentMonitor: {formatDate: x => x}};
const {renderAccountPause} = require("./web/codex-accounts.js");
const controls = {pausePanel: {}, pauseSummary: {}, pauseDetail: {}, pauseRecovery: {}};
const pause = {id: "p1", at: 1793004570, reason: "quotaRefreshFailed", failure: {detail: "fixture connection failed"}};
const target = {machine: "fixture-hub", account_label: "fixture@example.test", account_id: "workspace", pause: {id: "p1"}};
const render = (p, t) => renderAccountPause(p, t, controls);
render(pause, target);
assert.equal(controls.pausePanel.hidden, false);
assert.match(controls.pauseSummary.textContent, /fixture-hub/);
assert.match(controls.pauseDetail.textContent, /fixture connection failed/);
assert.doesNotMatch(controls.pauseDetail.textContent, /原始失败/);
assert.match(controls.pauseDetail.textContent, /2026-10-26/);
assert.equal(controls.pauseRecovery.disabled, false);
for (const wrong of [null, {...target, pause: {id: "old"}}, {...target, account_id: ""}]) {
  render(pause, wrong);
  assert.equal(controls.pauseRecovery.disabled, false, "local status remains readable even when overview is stale");
  assert.match(controls.pauseDetail.textContent, /未.*匹配|无法.*匹配/);
}
render({...pause, id: "unreadable"}, null);
assert.equal(controls.pauseRecovery.disabled, false, "local status remains readable even when overview is stale");
assert.match(controls.pauseDetail.textContent, /无法读取/);
render(pause, {...target, pause: {id: "p1", probe_attempt: {id: "used"}}});
assert.equal(controls.pauseRecovery.disabled, false, "used probe still has a readable recovery record");
render(null, null);
assert.equal(controls.pausePanel.hidden, true);
''')
