import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { startGateway } from './demo-gateway.mjs';

async function listen(server) {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return `http://127.0.0.1:${server.address().port}`;
}

async function close(server) {
  await new Promise(resolve => server.close(resolve));
}

const credentials = { username: 'demo', password: 'unit-test-password-only' };
const authorization = `Basic ${Buffer.from(`${credentials.username}:${credentials.password}`).toString('base64')}`;

test('every path and method is gated before the upstream receives a request', async () => {
  let reached = 0;
  const upstream = http.createServer((request, response) => { reached += 1; response.end('unexpected'); });
  const target = await listen(upstream);
  const gateway = await startGateway({ ...credentials, port: 0, target });
  const base = `http://127.0.0.1:${gateway.address().port}`;
  try {
    for (const [method, url, headers] of [
      ['POST', '/api/v1/orders', {}],
      ['GET', '/_next/static/private.js', {}],
      ['GET', '/', { Authorization: 'Basic ZGVtbzp3cm9uZw==' }],
    ]) {
      const result = await fetch(base + url, { method, headers, ...(method === 'POST' ? { body: '{}' } : {}) });
      assert.equal(result.status, 401);
      assert.match(result.headers.get('www-authenticate'), /Basic realm="Listing Studio Demo"/);
      assert.equal(result.headers.get('cache-control'), 'private, no-store');
      await result.text();
    }
    assert.equal(reached, 0);
    assert.equal(gateway.address().address, '127.0.0.1');
  } finally { await close(gateway); await close(upstream); }
});

test('authorized streaming preserves business headers and Secure cookies, but strips gate credentials', async () => {
  let received;
  const upstream = http.createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    received = { method: request.method, path: request.url, headers: request.headers, body: Buffer.concat(chunks).toString() };
    response.writeHead(202, { 'Set-Cookie': ['seller_session=fixture; Path=/api/v1; HttpOnly; Secure; SameSite=Lax'], 'Content-Type': 'application/json', 'Cache-Control': 'public, max-age=300' });
    response.end('{"status":"queued"}');
  });
  const target = await listen(upstream);
  const gateway = await startGateway({ ...credentials, port: 0, target });
  try {
    const result = await fetch(`http://127.0.0.1:${gateway.address().port}/api/v1/orders?revision=1`, {
      method: 'POST', body: '{"is_demo":true}', headers: {
        Authorization: authorization, 'Proxy-Authorization': 'must-not-leave-gate',
        Cookie: 'seller_session=fixture', Origin: 'https://fixture.trycloudflare.com',
        'X-CSRF-Token': 'fixture-csrf', 'X-Forwarded-Proto': 'http', 'Content-Type': 'application/json',
      },
    });
    assert.equal(result.status, 202);
    assert.deepEqual(await result.json(), { status: 'queued' });
    assert.equal(received.path, '/api/v1/orders?revision=1');
    assert.equal(received.method, 'POST');
    assert.equal(received.body, '{"is_demo":true}');
    assert.equal(received.headers.authorization, undefined);
    assert.equal(received.headers['proxy-authorization'], undefined);
    assert.equal(received.headers.cookie, 'seller_session=fixture');
    assert.equal(received.headers.origin, 'https://fixture.trycloudflare.com');
    assert.equal(received.headers['x-csrf-token'], 'fixture-csrf');
    assert.equal(received.headers['x-forwarded-proto'], 'https');
    assert.match(result.headers.get('set-cookie'), /HttpOnly; Secure; SameSite=Lax/);
    assert.equal(result.headers.get('cache-control'), 'private, no-store');
  } finally { await close(gateway); await close(upstream); }
});

test('invalid setup is rejected without listening', async () => {
  await assert.rejects(startGateway({ username: '', password: 'fixture' }), /nonempty/);
  await assert.rejects(startGateway({ ...credentials, target: 'http://example.com' }), /loopback/);
  await assert.rejects(startGateway({ ...credentials, target: 'https://127.0.0.1' }), /loopback/);
  await assert.rejects(startGateway({ ...credentials, target: 'http://127.0.0.1/private' }), /loopback/);
});
