// Controlled policy stand-in: reuse the production bridge/transport; no provider.
const {createBridge} = require('../../proxy_runtime/bridge.cjs');
let override;
const bridge = createBridge(async url => {
  const target = new URL(url);
  if (process.argv[3] && target.port === process.argv[3]) return `PROXY 127.0.0.1:${process.argv[2]}; PROXY 127.0.0.1:${process.argv[2]}; DIRECT`;
  if (['controlled.test', 'chatgpt.com', 'auth.openai.com', 'login.openai.com'].includes(target.hostname)) return override || `PROXY 127.0.0.1:${process.argv[2]}`;
  if (target.hostname === '127.0.0.1') return 'DIRECT';
  throw new Error('fixture forbids external targets');
}, 'fixture-local-capability');
bridge.server.listen(0, '127.0.0.1', () => console.log(JSON.stringify({port: bridge.server.address().port})));
process.stdin.resume();
require('node:readline').createInterface({input: process.stdin}).on('line', line => {
  const command = JSON.parse(line);
  if (command.route) override = command.route;
  if (command.invalidate) bridge.router.invalidate();
  console.log(JSON.stringify({changed: true}));
});
process.stdin.on('end', () => { bridge.close(); process.exit(0); });
