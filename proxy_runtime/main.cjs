'use strict';
const {app, session} = require('electron');
const readline = require('node:readline');
const tls = require('node:tls');
const {createBridge} = require('./bridge.cjs');

// No BrowserWindow/profile/credential store: only an in-memory system resolver.
app.setPath('userData', process.env.AGENT_MONITOR_PROXY_DATA);
if (app.dock) app.dock.hide();
let bridge;
let closing = false;
let configuration = Promise.resolve();
const input = readline.createInterface({input: process.stdin});
function close() {
  if (closing) return;
  closing = true;
  if (bridge) bridge.close();
  app.quit();
}
input.on('close', close);
process.on('SIGTERM', close);
process.on('SIGINT', close);
process.on('uncaughtException', () => { process.stderr.write('proxy_runtime_failed\n'); close(); });
process.on('unhandledRejection', () => { process.stderr.write('proxy_runtime_failed\n'); close(); });

input.once('line', async line => {
  try {
    const initial = JSON.parse(line);
    if (typeof initial.token !== 'string' || initial.token.length < 24) throw new Error('invalid capability');
    await app.whenReady();
    if (closing) return;
    // Public trust anchors only, not browser credentials. Origin TLS remains
    // end-to-end in the calling client; this store validates HTTPS proxies.
    tls.setDefaultCACertificates([...tls.getCACertificates('default'), ...tls.getCACertificates('system')]);
    const resolver = session.fromPartition('agent-monitor-system');
    await resolver.setProxy({mode: 'system'});
    bridge = createBridge(async url => { await configuration; return resolver.resolveProxy(url); }, initial.token);
    input.on('line', message => {
      try {
        if (JSON.parse(message).op === 'invalidate') {
          configuration = configuration.then(async () => {
            await resolver.forceReloadProxyConfig(); bridge.router.invalidate();
          });
        }
      } catch { close(); }
    });
    bridge.server.listen(0, '127.0.0.1', () => {
      process.stdout.write(JSON.stringify({port: bridge.server.address().port, policy: 'chromium-system',
        electron: process.versions.electron, chromium: process.versions.chrome}) + '\n');
    });
  } catch { process.stderr.write('proxy_start_failed\n'); app.exit(1); }
});
