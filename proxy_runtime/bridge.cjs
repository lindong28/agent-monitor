'use strict';

// Chromium owns resolution; this bridge owns sockets, without decrypting TLS.
const http = require('node:http');
const net = require('node:net');
const tls = require('node:tls');
const dns = require('node:dns').promises;
const crypto = require('node:crypto');

class RouteError extends Error {
  constructor(kind, fallback = false) { super(kind); this.kind = kind; this.fallback = fallback; }
}

function boundedLookup(host, signal, timeout) {
  return new Promise((resolve, reject) => {
    const finish = (error, value) => {
      clearTimeout(timer); signal.removeEventListener('abort', abort);
      if (error) reject(error); else resolve(value);
    };
    const abort = () => finish(new RouteError('client_cancelled'));
    const timer = setTimeout(() => finish(new RouteError('target_dns_timeout', true)), timeout);
    if (signal.aborted) return abort();
    signal.addEventListener('abort', abort, {once: true});
    dns.lookup(host, {family: 4}).then(value => finish(null, value.address),
      () => finish(new RouteError('target_dns_failed', true)));
  });
}

function candidates(value) {
  if (typeof value !== 'string' || !value.trim()) throw new RouteError('proxy_resolution_failed');
  return value.split(';').map(part => {
    const fields = part.trim().split(/\s+/);
    if (fields.length === 1 && fields[0] === 'DIRECT') return {kind: 'DIRECT', key: 'DIRECT'};
    if (fields.length !== 2 || !['PROXY', 'HTTPS', 'SOCKS', 'SOCKS4', 'SOCKS5'].includes(fields[0])) {
      throw new RouteError('proxy_type_unsupported');
    }
    let address;
    try { address = new URL(`http://${fields[1]}`); } catch { throw new RouteError('proxy_resolution_invalid'); }
    if (!address.hostname || address.username || address.password || address.pathname !== '/' || address.search || address.hash) {
      throw new RouteError('proxy_resolution_invalid');
    }
    return {kind: fields[0], host: address.hostname.replace(/^\[|\]$/g, ''),
      port: Number(address.port || (fields[0] === 'HTTPS' ? 443 : fields[0].startsWith('SOCKS') ? 1080 : 80)), key: `${fields[0]} ${address.host}`};
  });
}

function destination(raw, tunnel) {
  let url;
  try { url = new URL(tunnel ? `https://${raw}/` : raw); } catch { throw new RouteError('invalid_target'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || !url.hostname) {
    throw new RouteError('invalid_target');
  }
  if (tunnel && (url.pathname !== '/' || url.search || url.hash)) throw new RouteError('invalid_target');
  return {url, host: url.hostname.replace(/^\[|\]$/g, ''), port: Number(url.port || (url.protocol === 'https:' ? 443 : 80))};
}

function connected(route, target, signal, timeout) {
  return new Promise((resolve, reject) => {
    const direct = route.kind === 'DIRECT';
    const secure = route.kind === 'HTTPS';
    const host = direct ? target.host : route.host;
    const socket = secure ? tls.connect({host, port: route.port, servername: net.isIP(host) ? undefined : host,
      rejectUnauthorized: true, ALPNProtocols: ['http/1.1']}) : net.connect({host, port: direct ? target.port : route.port});
    const abort = () => socket.destroy(new RouteError('client_cancelled'));
    if (signal.aborted) abort(); else signal.addEventListener('abort', abort, {once: true});
    const timer = setTimeout(() => socket.destroy(new RouteError('proxy_connect_timeout', true)), timeout);
    const failed = error => {
      cleanup();
      // Only pre-application connection errors reach this mapping. Proxy TLS
      // certificate/protocol failure is fallover-eligible in Chromium 156.
      if (error instanceof RouteError) return reject(error);
      const codes = ['ENOTFOUND', 'EAI_AGAIN', 'ECONNREFUSED', 'ECONNRESET', 'ECONNABORTED',
        'ENETUNREACH', 'EHOSTUNREACH', 'ETIMEDOUT', 'ENETDOWN', 'EPIPE'];
      // Node folds TLS 1.2 client-certificate rejection into handshake_failure;
      // unlike Chromium it exposes no certificate-request callback. Keep
      // ambiguous handshake failures terminal instead of changing exit routes.
      const clientAuth = ['ERR_SSL_TLSV13_ALERT_CERTIFICATE_REQUIRED',
        'ERR_SSL_SSLV3_ALERT_BAD_CERTIFICATE', 'ERR_SSL_SSLV3_ALERT_UNSUPPORTED_CERTIFICATE',
        'ERR_SSL_SSLV3_ALERT_CERTIFICATE_UNKNOWN'].includes(error.code);
      if (secure && clientAuth) return reject(new RouteError('proxy_auth_required'));
      const certificates = ['CERT_HAS_EXPIRED', 'CERT_NOT_YET_VALID', 'DEPTH_ZERO_SELF_SIGNED_CERT',
        'SELF_SIGNED_CERT_IN_CHAIN', 'UNABLE_TO_VERIFY_LEAF_SIGNATURE', 'UNABLE_TO_GET_ISSUER_CERT',
        'UNABLE_TO_GET_ISSUER_CERT_LOCALLY', 'CERT_REVOKED', 'ERR_TLS_CERT_ALTNAME_INVALID'];
      const protocols = ['ERR_SSL_WRONG_VERSION_NUMBER', 'ERR_SSL_TLSV1_ALERT_PROTOCOL_VERSION',
        'ERR_SSL_UNSUPPORTED_PROTOCOL', 'ERR_SSL_VERSION_TOO_LOW', 'ERR_SSL_VERSION_TOO_HIGH'];
      const proxyTls = secure && (certificates.includes(error.code) || protocols.includes(error.code));
      reject(new RouteError(secure ? 'proxy_tls_failed' : 'proxy_connect_failed', proxyTls || codes.includes(error.code)));
    };
    const cleanup = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); socket.removeListener('error', failed); };
    socket.once('error', failed);
    socket.once(secure ? 'secureConnect' : 'connect', () => { cleanup(); socket.on('error', () => {}); resolve(socket); });
  });
}

class Reader {
  constructor(socket, signal, timeout) {
    this.socket = socket; this.buffer = Buffer.alloc(0); this.pending = null; this.error = null;
    this.data = chunk => { this.buffer = Buffer.concat([this.buffer, chunk]); this.drain(); };
    this.fail = error => {
      this.error = error instanceof RouteError ? error : new RouteError('proxy_connection_closed', true);
      this.drain();
    };
    this.end = () => this.fail(new RouteError('proxy_connection_closed', true));
    this.abort = () => this.fail(new RouteError('client_cancelled'));
    this.timer = setTimeout(() => this.fail(new RouteError('proxy_handshake_timeout', true)), timeout);
    this.signal = signal;
    socket.on('data', this.data); socket.on('error', this.fail); socket.on('end', this.end);
    signal.addEventListener('abort', this.abort, {once: true});
  }
  drain() {
    if (!this.pending) return;
    const {count, resolve, reject} = this.pending;
    if (this.error) { this.pending = null; reject(this.error); }
    else if (this.buffer.length >= count) {
      const data = this.buffer.subarray(0, count); this.buffer = this.buffer.subarray(count);
      this.pending = null; resolve(data);
    }
  }
  read(count) { return new Promise((resolve, reject) => { this.pending = {count, resolve, reject}; this.drain(); }); }
  async headers() {
    const bytes = [];
    for (let n = 0; n < 16384; n++) {
      bytes.push((await this.read(1))[0]);
      if (bytes.length >= 4 && Buffer.from(bytes.slice(-4)).equals(Buffer.from('\r\n\r\n'))) return Buffer.from(bytes).toString('latin1');
    }
    throw new RouteError('proxy_response_invalid');
  }
  release() {
    clearTimeout(this.timer); this.signal.removeEventListener('abort', this.abort);
    this.socket.pause(); this.socket.removeListener('data', this.data);
    this.socket.removeListener('error', this.fail); this.socket.removeListener('end', this.end);
    if (this.buffer.length) this.socket.unshift(this.buffer);
  }
}

async function handshake(socket, route, target, tunnel, signal, timeout) {
  if (route.kind === 'DIRECT' || (!tunnel && ['PROXY', 'HTTPS'].includes(route.kind))) return socket;
  const reader = new Reader(socket, signal, timeout);
  try {
    if (['PROXY', 'HTTPS'].includes(route.kind)) {
      const authority = `${net.isIP(target.host) === 6 ? `[${target.host}]` : target.host}:${target.port}`;
      socket.write(`CONNECT ${authority} HTTP/1.1\r\nHost: ${authority}\r\n\r\n`);
      const headers = await reader.headers();
      const match = /^HTTP\/1\.[01] ([0-9]{3})(?: |\r)/.exec(headers);
      if (!match) throw new RouteError('proxy_response_invalid');
      if (match[1] === '407') throw new RouteError('proxy_auth_required');
      if (match[1] !== '200') throw new RouteError('proxy_tunnel_refused');
    } else if (route.kind === 'SOCKS5') {
      socket.write(Buffer.from([5, 1, 0]));
      const greeting = await reader.read(2);
      if (greeting[0] !== 5) throw new RouteError('socks_protocol_failed', true);
      if (greeting[1] !== 0) throw new RouteError('proxy_auth_required');
      // Chrome SOCKS5 resolves the target at the proxy, including ordinary DNS.
      let address;
      if (net.isIP(target.host) === 4) {
        address = Buffer.from([1, ...target.host.split('.').map(Number)]);
      } else if (net.isIP(target.host) === 6) {
        let host = target.host;
        if (host.includes('.')) {
          const at = host.lastIndexOf(':');
          const bytes = host.slice(at + 1).split('.').map(Number);
          host = host.slice(0, at + 1) + ((bytes[0] << 8) | bytes[1]).toString(16) + ':' + ((bytes[2] << 8) | bytes[3]).toString(16);
        }
        const halves = host.split('::').map(half => half ? half.split(':') : []);
        const groups = halves.length === 2 ? [...halves[0], ...Array(8 - halves[0].length - halves[1].length).fill('0'), ...halves[1]] : halves[0];
        address = Buffer.alloc(17); address[0] = 4;
        groups.forEach((group, index) => address.writeUInt16BE(parseInt(group, 16), 1 + index * 2));
      } else {
        const host = Buffer.from(target.host);
        if (!host.length || host.length > 255) throw new RouteError('invalid_target');
        address = Buffer.concat([Buffer.from([3, host.length]), host]);
      }
      socket.write(Buffer.concat([Buffer.from([5, 1, 0]), address,
        Buffer.from([target.port >> 8, target.port & 255])]));
      const reply = await reader.read(4);
      if (reply[0] !== 5 || reply[2] !== 0) throw new RouteError('socks_protocol_failed', true);
      if (reply[1] === 4) throw new RouteError('socks_target_unreachable');
      if (reply[1] !== 0) throw new RouteError('socks_connect_failed', true);
      const length = reply[3] === 1 ? 4 : reply[3] === 4 ? 16 : reply[3] === 3 ? (await reader.read(1))[0] : 0;
      if (!length) throw new RouteError('socks_protocol_failed', true);
      await reader.read(length + 2);
    } else {
      // SOCKS / SOCKS4 means SOCKS4 with local IPv4 DNS, never SOCKS4a.
      const address = await boundedLookup(target.host, signal, timeout);
      socket.write(Buffer.from([4, 1, target.port >> 8, target.port & 255, ...address.split('.').map(Number), 0]));
      const reply = await reader.read(8);
      if (reply[0] !== 0 || reply[1] !== 90) throw new RouteError('socks_connect_failed', true);
    }
    return socket;
  } finally { reader.release(); }
}

class Router {
  constructor(resolve, {timeout = 10000, now = Date.now} = {}) {
    this.resolve = resolve; this.timeout = timeout; this.now = now;
    this.bad = new Map(); this.generation = 0; this.sockets = new Set(); this.events = [];
  }
  invalidate() { this.bad.clear(); this.generation++; }
  async routes(url) {
    for (const [key, until] of this.bad) if (until <= this.now()) this.bad.delete(key);
    const list = candidates(await this.resolve(url));
    const bad = route => (this.bad.get(route.key) || 0) > this.now();
    return [...list.filter(route => !bad(route)), ...list.filter(bad)];
  }
  async connect(target, tunnel, signal, scope = 'shared') {
    let list;
    try { list = await this.routes(target.url.href); }
    catch (error) { this.record(target, {key:null}, 'proxy_resolution_failed', scope); throw error; }
    const generation = this.generation;
    let last = new RouteError('proxy_connect_failed');
    for (const route of list) {
      if (signal.aborted) throw new RouteError('client_cancelled');
      let socket;
      try {
        socket = await connected(route, target, signal, this.timeout);
        this.sockets.add(socket); socket.once('close', () => this.sockets.delete(socket));
        await handshake(socket, route, target, tunnel, signal, this.timeout);
        this.record(target, route, 'connected', scope);
        return {socket, route};
      } catch (error) {
        if (socket) socket.destroy();
        last = error instanceof RouteError ? error : new RouteError('proxy_connect_failed');
        this.record(target, route, last.kind, scope);
        if (!last.fallback || signal.aborted) throw last;
        if (generation === this.generation && route.kind !== 'DIRECT') this.bad.set(route.key, this.now() + 300000);
      }
    }
    throw last;
  }
  record(target, route, result, scope) {
    this.events.push({scope, target: target.url.origin, route: route.key, result, at: new Date().toISOString(), generation: this.generation});
    if (this.events.length > 64) this.events.shift();
  }
  close() { for (const socket of this.sockets) socket.destroy(); this.sockets.clear(); }
}

function authorizedScope(req, expected) {
  const header = req.headers['proxy-authorization'] || '';
  if (!header.startsWith('Basic ')) return null;
  const decoded = Buffer.from(header.slice(6), 'base64').toString();
  const separator = decoded.indexOf(':');
  const user = decoded.slice(0, separator);
  const actual = Buffer.from(decoded.slice(separator + 1));
  // Product protocol: optional generated operation UUID hex, never identity.
  if (!/^agent-monitor(?:\.[a-f0-9]{32})?$/.test(user)) return null;
  if (actual.length !== expected.length || !crypto.timingSafeEqual(actual, expected)) return null;
  return user.includes('.') ? user.split('.')[1] : 'shared';
}

function cleanHeaders(input) {
  const headers = {...input};
  for (const name of (headers.connection || '').split(',')) delete headers[name.trim().toLowerCase()];
  for (const name of ['proxy-authorization', 'proxy-authenticate', 'proxy-connection', 'connection', 'keep-alive', 'upgrade']) delete headers[name];
  return headers;
}

function createBridge(resolve, token, options) {
  const router = new Router(resolve, options);
  const expected = Buffer.from(token);
  const clients = new Set();
  const server = http.createServer(async (req, res) => {
    const scope = authorizedScope(req, expected);
    if (scope === null) { res.writeHead(407); res.end(); req.resume(); return; }
    if (req.url === '/status' && req.method === 'GET') {
      res.setHeader('content-type', 'application/json');
      const events = router.events.filter(row => scope === 'shared' || row.scope === scope).map(({scope, ...row}) => row);
      res.end(JSON.stringify({policy: 'chromium-system', generation: router.generation,
        coverage: 'recent_connections_only', events})); return;
    }
    const controller = new AbortController();
    res.on('close', () => controller.abort());
    let upstream;
    try {
      const target = destination(req.url, false);
      if (target.url.protocol !== 'http:') throw new RouteError('use_connect_for_https');
      const {socket, route} = await router.connect(target, false, controller.signal, scope);
      controller.signal.addEventListener('abort', () => socket.destroy(), {once: true});
      if (controller.signal.aborted) { socket.destroy(); return; }
      const headers = cleanHeaders(req.headers); headers.host = target.url.host; headers.connection = 'close';
      const agent = new http.Agent({keepAlive: false});
      agent.createConnection = () => socket;
      upstream = http.request({method: req.method, host: target.host, port: target.port,
        path: ['PROXY', 'HTTPS'].includes(route.kind) ? target.url.href : target.url.pathname + target.url.search,
        headers, agent}, response => {
        const responseHeaders = cleanHeaders(response.headers);
        responseHeaders['x-agent-monitor-route'] = route.key;
        res.writeHead(response.statusCode, responseHeaders); response.pipe(res);
      });
      upstream.on('error', () => { if (!res.headersSent) res.writeHead(502, {'x-agent-monitor-error': 'upstream_transport_failed'}); res.end(); });
      req.on('aborted', () => upstream.destroy()); req.pipe(upstream);
    } catch (error) {
      if (!res.headersSent) res.writeHead(502, {'x-agent-monitor-error': error instanceof RouteError ? error.kind : 'proxy_internal_error'});
      res.end(); req.resume();
    }
  });
  server.on('connect', async (req, client, head) => {
    const scope = authorizedScope(req, expected);
    if (scope === null) { client.end('HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n'); return; }
    const controller = new AbortController();
    client.on('close', () => controller.abort()); client.on('error', () => controller.abort());
    try {
      const target = destination(req.url, true);
      const {socket} = await router.connect(target, true, controller.signal, scope);
      controller.signal.addEventListener('abort', () => socket.destroy(), {once: true});
      if (controller.signal.aborted) { socket.destroy(); return; }
      socket.on('error', () => client.destroy()); socket.on('close', () => client.destroy());
      client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      if (head.length) socket.write(head);
      client.pipe(socket); socket.pipe(client);
    } catch (error) {
      const kind = error instanceof RouteError ? error.kind : 'proxy_internal_error';
      client.end(`HTTP/1.1 502 Bad Gateway\r\nX-Agent-Monitor-Error: ${kind}\r\nContent-Length: 0\r\n\r\n`);
    }
  });
  server.on('connection', socket => { clients.add(socket); socket.once('close', () => clients.delete(socket)); socket.on('error', () => {}); });
  server.on('clientError', (_error, socket) => socket.destroy());
  return {server, router, close() { router.close(); for (const socket of clients) socket.destroy(); server.close(); }};
}

module.exports = {RouteError, candidates, destination, Router, createBridge};
