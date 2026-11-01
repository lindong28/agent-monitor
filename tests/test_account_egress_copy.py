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
assert.match(dialog.text, /macstudio.*Agent Monitor 路由桥/);
assert.match(status.textContent, /目标机器的 Agent Monitor 路由桥出口预览未完成/);
assert.match(status.textContent, /尚未发送配额检查请求/);
assert.doesNotMatch(status.textContent, /尚未发送账号消息/);
assert.doesNotMatch(status.textContent, /网络。。|请求。。|。；/);
'''
        result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
