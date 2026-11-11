"""Consumer-facing egress preview copy stays scoped to the requested action."""
import subprocess
import unittest
from pathlib import Path


class AccountEgressCopyTests(unittest.TestCase):
    def test_quota_recovery_error_names_route_bridge_and_no_account_message(self):
        script = r'''
import assert from "node:assert/strict";
import fs from "node:fs";
const source = fs.readFileSync("web/account-egress.js", "utf8");
const {previewEgress} = await import("data:text/javascript," + encodeURIComponent(source));
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.listeners={}; this.open=true; this.hidden=false; this.disabled=false; this.textContent=""; }
  setAttribute() {}
  append(...nodes) { this.children.push(...nodes); }
  addEventListener(name, fn) { this.listeners[name]=fn; }
  remove() {}
  showModal() { this.open=true; }
  close() { this.open=false; }
  get text() { return this.textContent + this.children.map(child => child.text || child.textContent || "").join("\n"); }
}
const body = {children:[], append(node){this.children.push(node);}};
globalThis.document = {createElement: tag => new Element(tag), body};
const signal = new AbortController().signal;
previewEgress(() => Promise.reject(new Error("请先连接浏览器所在设备的网络。")), [], signal, {
  approval_action: "quota-recovery", approval_scope: ["macstudio", "workspace"],
});
await new Promise(resolve => setImmediate(resolve));
const dialog = body.children[0];
const status = dialog.children.find(child => child.tag === "p");
assert.match(dialog.text, /凭据与执行主机：macstudio/);
assert.doesNotMatch(dialog.text, /无法取得完整出口时不会继续/);
assert.match(status.textContent, /目标机器的 Agent Monitor 路由桥出口预览未完成/);
assert.match(status.textContent, /尚未发送配额检查请求/);
assert.doesNotMatch(status.textContent, /尚未发送账号消息/);
assert.doesNotMatch(status.textContent, /网络。。|请求。。|。；/);
previewEgress(() => Promise.reject(new Error("所选设备已断开")), [], signal, {
  approval_action: "quota-auth-recovery", approval_scope: ["credential-host", "workspace"],
  connection: {id:"device"}, recovery_request: {profile:"monitor-fixture"},
});
await new Promise(resolve => setImmediate(resolve));
const deviceStatus = body.children[1].children.find(child => child.tag === "p");
assert.match(deviceStatus.textContent, /所选设备的 Agent Monitor 路由桥出口预览未完成/);
assert.doesNotMatch(deviceStatus.textContent, /目标机器的 Agent Monitor/);
assert.match(deviceStatus.textContent, /尚未刷新认证或查询配额/);
previewEgress(async () => ({confirmation:'fixture', checked_host:'Singapore', route_change:true,
  network_device:{id:'new-connection',label:'Singapore'},
  network_context:{device:{id:'old-connection',label:'US'},preview:{targets:[{host:'chatgpt.com',ip:'192.0.2.1',country_code:'US'}]}},
  targets:[{host:'chatgpt.com',ip:null,country_code:null}]}), [], signal,
  {approval_action:'quota-recovery',recovery_request:{operation_id:'fixture'}});
await new Promise(resolve => setImmediate(resolve));
const changed = body.children[2];
assert.match(changed.text,/原任务出站设备：US.*旧|原任务出站设备：US.*old-conn/);
assert.match(changed.text,/确认改用 Singapore/);
assert.match(changed.text,/原预览：192.0.2.1.*本次预览：IP 未知/);
assert.doesNotMatch(changed.text,/无法取得完整出口时不会继续/);

'''
        result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
