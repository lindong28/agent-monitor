"""Account actions explain provider pauses without issuing recovery requests."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AccountPauseWebTests(unittest.TestCase):
    def run_js(self, script):
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_quota_refusal_and_unattempted_reason_stay_distinct(self):
        self.run_js(r'''const assert = require('assert');
const {quotaFailureExplanation} = require('./web/codex-accounts.js');
const failed = {id:'a',email:'a@example.test',operation:{refresh_only:true,stage:'failed',diagnostics:{rpc_failure:{http_status:401,upstream_code:'token_expired'}}}};
assert.match(quotaFailureExplanation(failed),/401\/token_expired.*原因尚未核实/);
assert.doesNotMatch(quotaFailureExplanation(failed),/认证已过期|token_revoked/);
const held = {...failed,account_quota_pause:{id:'p'}};
assert.match(quotaFailureExplanation(held),/其他未受限账号可继续查询/);
assert.match(quotaFailureExplanation(held,{source:{profile_id:'b'}}),/共享暂停仍在生效/);
assert.doesNotMatch(quotaFailureExplanation(held,{source:{profile_id:'b'}}),/其他.*可继续查询/);
const skipped = {operation:{refresh_only:true,stage:'failed',failure:{category:'locallyPaused'}}};
assert.match(quotaFailureExplanation(skipped,{source:{profile_id:'a'}},[failed]),/未发出请求.*a@example.test.*共享暂停/);
assert.match(quotaFailureExplanation(skipped),/上次未发出请求.*当前无共享暂停/);
assert.doesNotMatch(quotaFailureExplanation(skipped),/处理暂停后/);
assert.match(quotaFailureExplanation({...skipped,account_quota_pause:{id:'p'}}),/本账号仍有配额暂停/);
assert.doesNotMatch(quotaFailureExplanation(failed),/本次共享暂停/);
assert.equal(quotaFailureExplanation({operation:{refresh_only:true,stage:'succeeded'}}),'');
''')

    def test_batch_explains_pause_and_other_disabled_states(self):
        self.run_js(r'''
const assert = require("assert");
global.document = {createElement: () => ({})};
global.window = {AgentMonitor: {formatDate: x => x, latestCodexQuotaReading: () => null}};
const {renderAccountBatch} = require("./web/codex-accounts.js");
const receipt = {};
const controls = {root: {querySelector: id => id === "#codex-send-receipt" ? receipt : null}, batchHelp: {replaceChildren() {}}};
for (const key of ["batchStart", "batchNext", "batchRetry", "batchStatus", "blockedReason"]) controls[key] = {};
const state = {accounts: [{id: "a", eligible: true, busy: false, has_credentials: true}], selectedIds: new Set(["a"]),
  batch: {id: "b", created_at: "2026-10-26T06:10:09Z", items: [{profile_id: "a", operation: {message_status: "succeeded"}}]},
  upstreamPause: {id: "pause-a"}, busy: false};
const render = () => renderAccountBatch(state, controls);
render();
assert.equal(controls.batchNext.disabled, true);
assert.equal(controls.blockedReason.hidden, true, "recovery card owns the pause explanation");
assert.match(controls.batchStatus.textContent, /暂停/);
assert.doesNotMatch(controls.batchStatus.textContent, /发送成功/);
assert.match(receipt.textContent, /发送成功 1\/1/);
assert.match(receipt.textContent, /2026-10-26/);
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
state.busy = false;
state.batch.items[0].operation.message_status = "not_sent";
state.accounts[0].restrictions = {quota: {reason: "quota"}};
state.accounts[0].has_credentials = true;
render();
assert.match(controls.blockedReason.textContent, /1.*额度.*刷新配额/);
assert.equal(controls.batchRetry.disabled, false);
state.selectedIds.clear(); render();
assert.match(controls.blockedReason.textContent, /选择/);
state.accounts[0].has_credentials = false; render();
assert.match(controls.blockedReason.textContent, /先完成登录或处理其他限制/);
state.accounts[0].has_credentials = true;
state.accounts[0].restrictions.unknown = true; render();
assert.match(controls.blockedReason.textContent, /先完成登录或处理其他限制/);
state.accounts[0].restrictions = {}; render();
assert.doesNotMatch(controls.blockedReason.textContent, /额度/);
state.batch = null; state.busy = false; state.upstreamPause = {id: "pause-b"}; render();
assert.equal(controls.batchStart.disabled, true);
assert.match(controls.batchStatus.textContent, /暂停/);
state.upstreamPause = null; state.accounts[0].login_required = true; render();
state.selectedIds.add("a"); render();
assert.equal(controls.batchStart.disabled, true);
assert.match(controls.blockedReason.textContent, /单独登录/);
''')

    def test_pause_fallback_preserves_context_and_local_recovery_entry(self):
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
assert.match(controls.pauseSummary.textContent, /暂停/);
assert.match(controls.pauseDetail.textContent, /已有结果保留/);
assert.doesNotMatch(controls.pauseDetail.textContent, /原始失败/);
assert.match(controls.pauseDetail.textContent, /2026-10-26/);
assert.equal(controls.pauseRecovery.disabled, false);
for (const wrong of [null, {...target, pause: {id: "old"}}, {...target, account_id: ""}]) {
  render(pause, wrong);
  assert.equal(controls.pauseRecovery.disabled, false, "local status remains readable even when overview is stale");
  assert.match(controls.pauseSummary.textContent, /核对原因后继续/);
}
render({...pause, id: "unreadable"}, null);
assert.equal(controls.pauseRecovery.disabled, false, "local status remains readable even when overview is stale");
assert.match(controls.pauseSummary.textContent, /核对原因后继续/);
render(pause, {...target, pause: {id: "p1", probe_attempt: {id: "used"}}});
assert.equal(controls.pauseRecovery.disabled, false, "used probe still has a readable recovery record");
render(null, null);
assert.equal(controls.pausePanel.hidden, true);
''')

    def test_account_pause_guidance_distinguishes_local_enable_from_auth_recovery(self):
        self.run_js(r'''
const assert = require("assert");
const {accountQuotaPauseGuidance, accountToggleNotice} = require("./web/codex-accounts.js");
const tokenRevoked = {account_quota_pause: {failure: {stage: "quota_read", kind: "rpc_error", http_status: 401, upstream_code: "token_revoked"}}};
assert.match(accountQuotaPauseGuidance({...tokenRevoked, disabled: false}), /本机账号已启用/);
assert.match(accountQuotaPauseGuidance({...tokenRevoked, disabled: false}), /按面板当前提示/);
assert.match(accountQuotaPauseGuidance({...tokenRevoked, disabled: true}), /先重新启用/);
assert.match(accountQuotaPauseGuidance({account_quota_pause: {failure: {stage: "quota_read", kind: "rpc_error", http_status: 500}}}), /按面板当前提示/);
assert.match(accountToggleNotice({...tokenRevoked,disabled:false}), /恢复此账号的额度查询/);
assert.doesNotMatch(accountToggleNotice({disabled:false}), /配额暂停|登录修复/);
assert.match(accountToggleNotice({disabled:true}), /已停用/);
''')
