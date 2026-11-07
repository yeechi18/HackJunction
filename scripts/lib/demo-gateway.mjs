// Password gate for a temporary HTTPS tunnel. Upstreams remain on loopback only.
import http from 'node:http';
import { createHash, timingSafeEqual } from 'node:crypto';

const hopHeaders = new Set([
  'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
  'te', 'trailer', 'transfer-encoding', 'upgrade',
]);

function forwardedHeaders(headers) {
  const removed = new Set(hopHeaders);
  for (const name of String(headers.connection ?? '').split(',')) removed.add(name.trim().toLowerCase());
  return Object.fromEntries(Object.entries(headers).filter(([name]) => !removed.has(name.toLowerCase())));
}

function digest(value) {
  return createHash('sha256').update(value).digest();
}

function authenticated(header, expected) {
  const match = typeof header === 'string' && /^Basic ([A-Za-z0-9+/]+={0,2})$/i.exec(header);
  const actual = digest(match ? Buffer.from(match[1], 'base64') : Buffer.alloc(0));
  return Boolean(match) && timingSafeEqual(actual, expected);
}

function message(response, status, content, headers = {}) {
  if (response.headersSent) { response.destroy(); return; }
  response.writeHead(status, {
    'Content-Type': 'text/plain; charset=utf-8',
    'Cache-Control': 'private, no-store',
    'X-Content-Type-Options': 'nosniff',
    ...headers,
  });
  response.end(content);
}

export async function startGateway({ port = 3300, target = 'http://127.0.0.1:3200', username, password } = {}) {
  if (typeof username !== 'string' || !username.trim() || username.includes(':')
      || typeof password !== 'string' || !password.length || /[\r\n\0]/.test(username + password)) {
    throw new TypeError('A nonempty demo username and password are required.');
  }
  const destination = new URL(target);
  if (destination.protocol !== 'http:' || destination.username || destination.password
      || destination.pathname !== '/' || destination.search || destination.hash
      || !['127.0.0.1', '[::1]', 'localhost'].includes(destination.hostname)) {
    throw new TypeError('The demo upstream must be an HTTP loopback origin.');
  }
  if (destination.hostname === 'localhost') destination.hostname = '127.0.0.1';
  const expected = digest(Buffer.from(`${username}:${password}`, 'utf8'));
  const server = http.createServer((request, response) => {
    if (!authenticated(request.headers.authorization, expected)) {
      message(response, 401, 'Demo access credentials required.', {
        'WWW-Authenticate': 'Basic realm="Listing Studio Demo", charset="UTF-8"',
        Connection: 'close',
      });
      request.resume();
      return;
    }
    if (!request.url?.startsWith('/') || request.url.startsWith('//')) {
      message(response, 400, 'Invalid request path.');
      request.resume();
      return;
    }
    const headers = forwardedHeaders(request.headers);
    delete headers.authorization;
    delete headers['proxy-authorization'];
    headers.host = destination.host;
    headers['x-forwarded-proto'] = 'https';
    headers['x-forwarded-host'] = request.headers.host ?? destination.host;
    const upstream = http.request({
      hostname: destination.hostname.replace(/^\[|\]$/g, ''),
      port: destination.port || 80,
      method: request.method,
      path: request.url,
      headers,
    }, incoming => {
      const outgoing = forwardedHeaders(incoming.headers);
      outgoing['cache-control'] = 'private, no-store';
      outgoing['x-content-type-options'] = 'nosniff';
      response.writeHead(incoming.statusCode ?? 502, outgoing);
      incoming.on('error', () => response.destroy());
      incoming.pipe(response);
    });
    upstream.setTimeout(120000, () => upstream.destroy());
    upstream.on('error', () => message(response, 502, 'Demo service is temporarily unavailable.'));
    request.on('aborted', () => upstream.destroy());
    response.on('close', () => { if (!response.writableEnded) upstream.destroy(); });
    request.pipe(upstream);
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => {
      server.off('error', reject);
      resolve();
    });
  });
  return server;
}
