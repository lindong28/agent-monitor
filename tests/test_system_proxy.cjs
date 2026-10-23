'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const net = require('node:net');
const tls = require('node:tls');
const https = require('node:https');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {once} = require('node:events');
const {createBridge, candidates} = require('../proxy_runtime/bridge.cjs');

const token = 'synthetic-bridge-capability-12345';
const authorization = 'Basic ' + Buffer.from('agent-monitor:' + token).toString('base64');
async function listen(server, port = 0) { server.listen(port, '127.0.0.1'); await once(server, 'listening'); return server.address().port; }
function dispose(t, server) { t.after(() => { server.closeAllConnections?.(); server.close(); }); }
async function bridge(t, resolver, options) {
  const value = createBridge(resolver, token, options);
  const port = await listen(value.server);
  t.after(() => value.close());
  return {...value, port};
}
function get(port, url, auth = authorization) {
  return new Promise((resolve, reject) => {
    const request = http.get({host: '127.0.0.1', port, path: url, headers: {'proxy-authorization': auth}}, response => {
      let body = ''; response.on('data', chunk => body += chunk);
      response.on('end', () => resolve({status: response.statusCode, headers: response.headers, body}));
    });
    request.on('error', reject); request.setTimeout(3000, () => request.destroy(new Error('test timeout')));
  });
}
async function tunnel(port, target, scheme) {
  const socket = net.connect(port, '127.0.0.1');
  await once(socket, 'connect');
  socket.write(`CONNECT ${target} HTTP/1.1\r\nHost: ${target}\r\nProxy-Authorization: ${authorization}\r\n${scheme ? `X-Agent-Monitor-Target-Scheme: ${scheme}\r\n` : ''}\r\n`);
  const [data] = await once(socket, 'data');
  return {socket, response: data.toString()};
}

test('empty/malformed/unsupported results never become DIRECT', async t => {
  for (const value of ['', ' ', 'OTHER host:80', 'PROXY user:password@host:80']) assert.throws(() => candidates(value));
  assert.deepEqual(candidates('DIRECT'), [{kind: 'DIRECT', key: 'DIRECT'}]);
  const source = http.createServer((_req, res) => res.end('should-not-arrive'));
  const sourcePort = await listen(source); dispose(t, source);
  let seen = 0; source.on('request', () => seen++);
  const proxy = await bridge(t, async () => '');
  const response = await get(proxy.port, `http://127.0.0.1:${sourcePort}/`);
  assert.equal(response.status, 502); assert.equal(seen, 0);
  assert.equal(response.headers['x-agent-monitor-error'], 'proxy_resolution_failed');
});

test('DIRECT forwards HTTP bytes without local proxy credentials', async t => {
  let headers;
  const source = http.createServer((req, res) => { headers = req.headers; res.end(req.url); });
  const port = await listen(source); dispose(t, source);
  const urls = [];
  const proxy = await bridge(t, async url => { urls.push(url); return 'DIRECT'; });
  const response = await get(proxy.port, `http://127.0.0.1:${port}/a?b=1`);
  assert.equal(response.status, 200); assert.equal(response.body, '/a?b=1');
  assert.equal(headers['proxy-authorization'], undefined);
  assert.equal(urls[0], `http://127.0.0.1:${port}/a?b=1`);
  assert.equal((await get(proxy.port, `http://127.0.0.1:${port}/`, 'invalid')).status, 407);
});

test('operation diagnostics exclude other operations and URL secrets', async t => {
  const source = http.createServer((_req, res) => res.end('ok'));
  const port = await listen(source); dispose(t, source);
  const proxy = await bridge(t, async () => 'DIRECT');
  const auth = scope => 'Basic ' + Buffer.from(`agent-monitor.${scope}:${token}`).toString('base64');
  const first = 'a'.repeat(32), second = 'b'.repeat(32);
  await get(proxy.port, `http://127.0.0.1:${port}/private?token=SECRET`, auth(first));
  await get(proxy.port, `http://127.0.0.1:${port}/other`, auth(second));
  const report = JSON.parse((await get(proxy.port, '/status', auth(first))).body);
  assert.equal(report.events.length, 1);
  assert.equal(report.coverage, 'recent_connections_only');
  assert.equal(report.events[0].target, `http://127.0.0.1:${port}`);
  assert.ok(!JSON.stringify(report).includes('SECRET'));
  assert.equal((await get(proxy.port, '/status', auth('x'.repeat(32)))).status, 407);
});

test('SOCKS4 uses local IPv4, SOCKS5 preserves literal IPv6', async t => {
  for (const kind of ['SOCKS4', 'SOCKS5']) {
    const socks = net.createServer(socket => {
      let phase = 0;
      socket.on('data', data => {
        if (kind === 'SOCKS4') {
          assert.deepEqual([...data.subarray(0, 9)], [4, 1, 1, 187, 127, 0, 0, 1, 0]);
          socket.write(Buffer.from([0, 90, 0, 0, 0, 0, 0, 0]));
        } else if (phase++ === 0) socket.write(Buffer.from([5, 0]));
        else {
          assert.equal(data[3], 4);
          assert.deepEqual([...data.subarray(4, 20)], [...Array(15).fill(0), 1]);
          socket.write(Buffer.from([5, 0, 0, 1, 127, 0, 0, 1, 0, 80]));
        }
      });
    });
    const port = await listen(socks); dispose(t, socks);
    const proxy = await bridge(t, async () => `${kind} 127.0.0.1:${port}`);
    const result = await tunnel(proxy.port, kind === 'SOCKS4' ? '127.0.0.1:443' : '[::1]:443');
    assert.match(result.response, /200/); result.socket.destroy();
  }
});

test('ordered fallback, bad-proxy demotion, same-list generation change and expiry', async t => {
  const unused = net.createServer(); const first = await listen(unused); await new Promise(resolve => unused.close(resolve));
  const received = [];
  const secondServer = http.createServer((req, res) => { received.push(['second', req.url, req.headers]); res.end('second'); });
  const second = await listen(secondServer); dispose(t, secondServer);
  let now = 1000;
  const proxy = await bridge(t, async () => `PROXY 127.0.0.1:${first}; PROXY 127.0.0.1:${second}`, {now: () => now});
  assert.equal((await get(proxy.port, 'http://unresolvable.invalid/first')).body, 'second');
  const firstServer = http.createServer((req, res) => { received.push(['first', req.url, req.headers]); res.end('first'); });
  await listen(firstServer, first); dispose(t, firstServer);
  assert.equal((await get(proxy.port, 'http://unresolvable.invalid/second')).body, 'second');
  proxy.router.invalidate();
  assert.equal((await get(proxy.port, 'http://unresolvable.invalid/third')).body, 'first');
  proxy.router.bad.set(`PROXY 127.0.0.1:${first}`, now + 300000);
  now += 300001;
  assert.equal((await get(proxy.port, 'http://unresolvable.invalid/fourth')).body, 'first');
  assert.equal(received[0][1], 'http://unresolvable.invalid/first');
  assert.ok(received.every(row => row[2]['proxy-authorization'] === undefined));
});

test('CONNECT authentication or refusal does not try another proxy or DIRECT', async t => {
  for (const code of [403, 407]) {
    let next = 0;
    let credentials;
    const rejecting = http.createServer();
    rejecting.on('connect', (req, socket) => { credentials = req.headers['proxy-authorization']; socket.end(`HTTP/1.1 ${code} Rejected\r\nContent-Length: 0\r\n\r\n`); });
    const first = await listen(rejecting); dispose(t, rejecting);
    const other = http.createServer(); other.on('connect', (_req, socket) => { next++; socket.destroy(); });
    const second = await listen(other); dispose(t, other);
    const proxy = await bridge(t, async () => `PROXY 127.0.0.1:${first}; PROXY 127.0.0.1:${second}; DIRECT`);
    const result = await tunnel(proxy.port, 'example.invalid:443');
    result.socket.destroy();
    assert.match(result.response, /502/); assert.equal(next, 0); assert.equal(credentials, undefined);
    assert.match(result.response, code === 407 ? /proxy_auth_required/ : /proxy_tunnel_refused/);
  }
});

test('CONNECT passes opaque bytes and an existing tunnel survives invalidation', async t => {
  const source = net.createServer(socket => socket.pipe(socket));
  const port = await listen(source); dispose(t, source);
  const urls = [];
  const proxy = await bridge(t, async url => { urls.push(url); return 'DIRECT'; });
  const result = await tunnel(proxy.port, `127.0.0.1:${port}`);
  t.after(() => result.socket.destroy());
  assert.match(result.response, /200 Connection Established/);
  proxy.router.invalidate();
  const payload = Buffer.from([0x16, 0x03, 0x01, 0, 0, 255, 12]);
  result.socket.write(payload);
  assert.deepEqual((await once(result.socket, 'data'))[0], payload);
  assert.equal(urls[0], `https://127.0.0.1:${port}/`);
});

test('connector control tunnels resolve their actual HTTP or HTTPS scheme', async t => {
  const urls = [];
  const proxy = await bridge(t, async url => { urls.push(url); throw new Error('fixture resolution stops before dial'); });
  for (const scheme of ['http', 'https']) {
    const result = await tunnel(proxy.port, 'hub.invalid:443', scheme);
    assert.match(result.response, /502/); result.socket.destroy();
  }
  assert.deepEqual(urls, ['http://hub.invalid:443/', 'https://hub.invalid/']);
});

test('SOCKS5 sends the unresolved destination to the proxy', async t => {
  let name;
  const socks = net.createServer(socket => {
    let phase = 0;
    socket.on('data', data => {
      if (phase === 0) { assert.deepEqual([...data], [5, 1, 0]); phase++; socket.write(Buffer.from([5, 0])); }
      else if (phase === 1) {
        assert.equal(data[3], 3); name = data.subarray(5, 5 + data[4]).toString(); phase++;
        socket.write(Buffer.from([5, 0, 0, 1, 127, 0, 0, 1, 0, 80]));
      } else socket.write(data);
    });
  });
  const port = await listen(socks); dispose(t, socks);
  const proxy = await bridge(t, async () => `SOCKS5 127.0.0.1:${port}`);
  const result = await tunnel(proxy.port, 'never-resolve-locally.invalid:443');
  assert.match(result.response, /200/); assert.equal(name, 'never-resolve-locally.invalid');
  result.socket.write('opaque');
  assert.equal((await once(result.socket, 'data'))[0].toString(), 'opaque'); result.socket.destroy();
});

test('SOCKS target unreachable stops, and bridge shutdown closes active clients', async t => {
  let later = 0;
  const socks = net.createServer(socket => {
    let phase = 0;
    socket.on('data', () => socket.write(Buffer.from(phase++ === 0 ? [5, 0] : [5, 4, 0, 1])));
  });
  const port = await listen(socks); dispose(t, socks);
  const other = http.createServer(); other.on('connect', (_req, socket) => { later++; socket.destroy(); });
  const second = await listen(other); dispose(t, other);
  const proxy = await bridge(t, async () => `SOCKS5 127.0.0.1:${port}; PROXY 127.0.0.1:${second}`);
  const result = await tunnel(proxy.port, 'example.invalid:443'); result.socket.destroy();
  assert.match(result.response, /socks_target_unreachable/); assert.equal(later, 0);
  const client = net.connect(proxy.port, '127.0.0.1'); await once(client, 'connect');
  client.on('error', () => {});
  const closed = new Promise(resolve => client.once('close', resolve)); proxy.close(); await closed;
});

test('HTTPS proxy verifies its certificate and can fall over before sending HTTP', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-monitor-proxy-cert-'));
  t.after(() => fs.rmSync(directory, {recursive: true, force: true}));
  const key = path.join(directory, 'key.pem'), certificate = path.join(directory, 'cert.pem');
  execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', certificate,
    '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost'], {stdio: 'ignore', timeout: 10000});
  let secureRequests = 0;
  const secure = https.createServer({key: fs.readFileSync(key), cert: fs.readFileSync(certificate)}, (_req, res) => {
    secureRequests++; res.end('secure-proxy');
  });
  const first = await listen(secure); dispose(t, secure);
  const fallback = http.createServer((_req, res) => res.end('fallback'));
  const second = await listen(fallback); dispose(t, fallback);
  const proxy = await bridge(t, async () => `HTTPS 127.0.0.1:${first}; PROXY 127.0.0.1:${second}`);
  assert.equal((await get(proxy.port, 'http://example.invalid/certificate-rejected')).body, 'fallback');
  assert.equal(secureRequests, 0);
  assert.equal(proxy.router.events[0].result, 'proxy_tls_failed');
  const original = tls.getCACertificates('default');
  t.after(() => tls.setDefaultCACertificates(original));
  tls.setDefaultCACertificates([...original, fs.readFileSync(certificate)]);
  proxy.router.invalidate();
  assert.equal((await get(proxy.port, 'http://example.invalid/certificate-trusted')).body, 'secure-proxy');
  assert.equal(secureRequests, 1);
});
