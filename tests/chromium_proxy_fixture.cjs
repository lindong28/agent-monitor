'use strict';
// Real Chromium reference, isolated sessions, local PAC/proxies, no provider.
const {app, session} = require('electron');
const http = require('node:http');
const net = require('node:net');
const https = require('node:https');
const tls = require('node:tls');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const assert = require('node:assert/strict');
const {once} = require('node:events');
const {createBridge} = require('../proxy_runtime/bridge.cjs');
app.setPath('userData', process.env.AGENT_MONITOR_PROXY_DATA);
if (app.dock) app.dock.hide();
const servers = [];
let bridge;
async function listen(server) {
  servers.push(server); server.listen(0, '127.0.0.1'); await once(server, 'listening'); return server.address().port;
}
async function run() {
  await app.whenReady();
  const arrivals = [];
  const proxy = name => http.createServer((req, res) => {
    arrivals.push({name, url: req.url, auth: req.headers['proxy-authorization']});
    res.end(name);
  });
  const first = await listen(proxy('first')), second = await listen(proxy('second'));
  const source = await listen(http.createServer((_req, res) => res.end('direct')));
  const resolver = session.fromPartition('fixture-resolver');
  const reference = session.fromPartition('fixture-reference');
  bridge = createBridge(url => resolver.resolveProxy(url), 'fixture-bridge-token-long-enough');
  const port = await listen(bridge.server);
  async function configure(configuration) {
    await reference.closeAllConnections();
    await Promise.all([resolver.setProxy(configuration), reference.setProxy(configuration)]);
    bridge.router.invalidate();
  }
  const viaBridge = url => new Promise((resolve, reject) => {
    const req = http.get({host:'127.0.0.1', port, path:url, headers:{
      'proxy-authorization':'Basic ' + Buffer.from('agent-monitor:fixture-bridge-token-long-enough').toString('base64')
    }}, res => {let data=''; res.on('data', x=>data+=x); res.on('end',()=>resolve(data));});
    req.on('error',reject); req.setTimeout(5000,()=>req.destroy(new Error('fixture timeout')));
  });
  async function compare(url, expected) {
    assert.equal(await (await reference.fetch(url)).text(), expected);
    assert.equal(await viaBridge(url), expected);
  }
  await configure({mode:'fixed_servers',proxyRules:`http=127.0.0.1:${first};https=127.0.0.1:${second}`,proxyBypassRules:'*.bypass.invalid;<local>'});
  await compare('http://fixture.invalid/fixed', 'first');
  await compare(`http://127.0.0.1:${source}/loopback`, 'direct');
  assert.equal(await resolver.resolveProxy('https://fixture.invalid/path?secret=removed'), `PROXY 127.0.0.1:${second}`);
  assert.equal(await resolver.resolveProxy('http://example.bypass.invalid/'), 'DIRECT');
  let pac = `function FindProxyForURL(url,host) { if (url.indexOf('/second?key=value') >= 0) return 'PROXY 127.0.0.1:${second}'; return 'PROXY 127.0.0.1:${first}; PROXY 127.0.0.1:${second}'; }`;
  const pacPort = await listen(http.createServer((_req,res)=>{res.setHeader('content-type','application/x-ns-proxy-autoconfig');res.end(pac);}));
  await configure({mode:'pac_script',pacScript:`http://127.0.0.1:${pacPort}/proxy.pac`});
  await compare('http://fixture.invalid/first', 'first');
  await compare('http://fixture.invalid/second?key=value', 'second');
  assert.equal((await resolver.resolveProxy('https://fixture.invalid/second?key=value')).replace(/;\s*/g,';'), `PROXY 127.0.0.1:${first};PROXY 127.0.0.1:${second}`);
  servers[0].closeAllConnections?.();
  await new Promise(resolve=>servers[0].close(resolve));
  await reference.closeAllConnections();
  await compare('http://fixture.invalid/fallback', 'second');
  pac = `function FindProxyForURL(url,host) { return 'PROXY 127.0.0.1:${second}'; }`;
  await Promise.all([resolver.forceReloadProxyConfig(),reference.forceReloadProxyConfig()]);
  bridge.router.invalidate();
  await compare('http://fixture.invalid/reloaded', 'second');
  const badTls = await listen(net.createServer(socket => {
    socket.once('data', () => socket.end(Buffer.from([21, 3, 3, 0, 2, 2, 70])));
    socket.on('error', () => {});
  }));
  pac = `function FindProxyForURL() { return 'HTTPS 127.0.0.1:${badTls}; PROXY 127.0.0.1:${second}'; }`;
  await configure({mode:'pac_script',pacScript:`http://127.0.0.1:${pacPort}/tls.pac`});
  await compare('http://fixture.invalid/tls-protocol-fallback', 'second');
  const key = path.join(process.env.AGENT_MONITOR_PROXY_DATA, 'fixture-key.pem');
  const certificate = path.join(process.env.AGENT_MONITOR_PROXY_DATA, 'fixture-cert.pem');
  execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', certificate,
    '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost'], {stdio:'ignore',timeout:10000});
  const cert = fs.readFileSync(certificate);
  tls.setDefaultCACertificates([...tls.getCACertificates('default'), cert]);
  reference.setCertificateVerifyProc((_request, callback) => callback(0));
  app.on('select-client-certificate', (event, _webContents, _url, _certificates, callback) => {
    event.preventDefault(); callback();
  });
  for (const version of ['TLSv1.2', 'TLSv1.3']) {
    const authProxy = await listen(https.createServer({key:fs.readFileSync(key), cert, ca:cert,
      requestCert:true, rejectUnauthorized:true, minVersion:version, maxVersion:version}, (_req,res)=>res.end('unexpected')));
    pac = `function FindProxyForURL() { return 'HTTPS 127.0.0.1:${authProxy}; PROXY 127.0.0.1:${second}'; }`;
    await configure({mode:'pac_script',pacScript:`http://127.0.0.1:${pacPort}/${version}.pac`});
    const before = arrivals.length, url = `http://fixture.invalid/client-auth-${version}`;
    await assert.rejects(reference.fetch(url));
    assert.equal(arrivals.length, before, 'Chromium must not change exit on client authentication failure');
    assert.notEqual(await viaBridge(url), 'second');
    assert.equal(arrivals.length, before, 'bridge must not change exit on client authentication failure');
  }
  assert.ok(arrivals.every(item=>item.auth===undefined));
  console.log(JSON.stringify({ok:true,comparisons:9,chromium:process.versions.chrome,proxyRequests:arrivals.length}));
}
const deadline = setTimeout(()=>{console.error('fixture_timeout');app.exit(1);},20000);
run().then(()=>{
  clearTimeout(deadline); bridge.close();
  for(const server of servers){server.closeAllConnections?.();server.close();}
  app.quit();
}).catch(error=>{console.error(error.stack);app.exit(1);});
