"""Run the actual shared formatter and account timestamp path in two client zones."""
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AccountTimeTests(unittest.TestCase):
    def test_account_reset_uses_server_timezone_in_both_client_zones(self):
        script = r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
global.window = {location: {origin: 'http://fixture'}};
global.document = {readyState: 'loading', addEventListener() {}};
global.fetch = async () => ({ok: true, json: async () => ({timezone: 'Asia/Singapore'})});
vm.runInThisContext(fs.readFileSync('web/app.js', 'utf8'));
vm.runInThisContext(fs.readFileSync('web/codex-accounts.js', 'utf8').split('export function init')[0]);
(async () => {
  await window.AgentMonitor.ensureTimezone();
  assert.equal(timestamp(1900000000, true), '2030/3/18 01:46:40 GMT+8');
  assert.equal(timestamp('2030-03-17T17:46:40Z'), '2030/3/18 01:46:40 GMT+8');
  assert.equal(timestamp(null, true), '服务端未返回');
  assert.equal(timestamp('invalid'), '时间不可用');
})().catch(e => {console.error(e); process.exitCode = 1;});
'''
        for zone in ("Asia/Singapore", "America/New_York"):
            with self.subTest(zone=zone):
                result = subprocess.run(["node", "-e", script], cwd=ROOT, env=dict(os.environ, TZ=zone),
                                        capture_output=True, text=True, timeout=8)
                self.assertEqual(result.returncode, 0, result.stderr)
