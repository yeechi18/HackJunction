// HTTPS-only deployment smoke. Uses independent request cookie jars, never a user browser.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { request } from '../apps/web/node_modules/playwright/index.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const reportPath = path.join(root, 'var/reports/public-smoke.json');
const checks = [];
const contexts = [];
let publicUrl;
let activeCheck = 'public_demo_configuration';
let activeStage = null;
let failureName = null;
const uploadStages = [];
let owner;
let seller;
let ownerCsrf;
let sellerCsrf;

function credentials(text) {
  const result = {};
  for (const line of text.replace(/^\uFEFF/, '').split(/\r?\n/)) {
    const separator = line.indexOf('=');
    if (separator > 0) result[line.slice(0, separator)] = line.slice(separator + 1);
  }
  assert.equal(typeof result.username, 'string');
  assert.equal(typeof result.password, 'string');
  assert.ok(result.username && result.password);
  return result;
}

async function persist(passed) {
  await mkdir(path.dirname(reportPath), { recursive: true });
  await writeFile(reportPath, JSON.stringify({
    created_at: new Date().toISOString(), public_url: publicUrl ?? null,
    passed, checks, upload_stages: uploadStages, is_demo: true, publish_mode: 'simulated',
    ...(!passed ? { failed_stage: activeStage, error_name: failureName } : {}),
    scope: 'HTTPS access, production cookies, permissions, CSRF and actual image upload/download',
    full_production_approved: false,
  }, null, 2));
}

function errorName(error) {
  const name = error?.name;
  return typeof name === 'string' && /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(name) ? name : 'Error';
}

async function uploadStage(name, run) {
  activeStage = name;
  try {
    const result = await run();
    uploadStages.push({ name, passed: true });
    return result;
  } catch (error) {
    uploadStages.push({ name, passed: false, error_name: errorName(error) });
    throw error;
  }
}

async function check(name, run) {
  activeCheck = name;
  activeStage = null;
  try {
    await run();
    checks.push({ name, passed: true });
    activeStage = null;
    console.log(`PASS ${name}`);
  } catch (error) {
    failureName = errorName(error);
    checks.push({ name, passed: false, stage: activeStage, error_name: failureName });
    // Do not surface SDK request diagnostics: they can contain credentials or private links.
    throw new Error('A public deployment smoke check failed.');
  }
}

async function context(extraHTTPHeaders = {}) {
  const client = await request.newContext({
    baseURL: publicUrl, extraHTTPHeaders, timeout: 60000,
    ignoreHTTPSErrors: false,
  });
  contexts.push(client);
  return client;
}

async function secureCookie(client, name) {
  const state = await client.storageState();
  const cookie = state.cookies.find(item => item.name === name);
  assert.ok(cookie);
  assert.equal(cookie.secure, true);
  assert.equal(cookie.httpOnly, true);
  assert.equal(cookie.path, '/api/v1');
}

try {
  const state = JSON.parse(await readFile(path.join(root, 'var/public-demo/state.json'), 'utf8'));
  const url = new URL(state.url);
  assert.equal(state.running, true);
  assert.equal(url.protocol, 'https:');
  assert.match(url.hostname, /^[a-z0-9-]+\.trycloudflare\.com$/);
  assert.equal(url.username + url.password + url.search + url.hash, '');
  assert.equal(url.pathname, '/');
  publicUrl = url.origin;
  const entry = credentials(await readFile(path.join(root, 'var/public-access.txt'), 'utf8'));
  const admin = credentials(await readFile(path.join(root, 'var/local-admin.txt'), 'utf8'));
  const headers = {
    Authorization: `Basic ${Buffer.from(`${entry.username}:${entry.password}`).toString('base64')}`,
    Origin: publicUrl,
  };
  const anonymous = await context();
  const visitor = await context(headers);
  seller = await context(headers);
  owner = await context(headers);
  console.log(`Public demo: ${publicUrl}`);

  await check('gateway_rejects_requests_without_entry_credentials', async () => {
    const response = await anonymous.get('/');
    assert.equal(response.status(), 401);
    assert.match(response.headers()['www-authenticate'], /^Basic /);
  });
  await check('authenticated_public_home_and_live_simulation_readiness', async () => {
    const response = await visitor.get('/');
    assert.equal(response.status(), 200);
    assert.match(await response.text(), /出海工坊/);
    const ready = await visitor.get('/api/v1/health/ready');
    assert.equal(ready.status(), 200);
    const health = await ready.json();
    assert.equal(health.model_mode, 'live');
    assert.equal(health.publish_mode, 'simulated');
  });
  await check('entry_password_does_not_grant_owner_access', async () => {
    assert.equal((await visitor.get('/api/v1/internal/orders')).status(), 401);
  });
  await check('owner_login_uses_secure_http_only_cookie_and_scoped_list', async () => {
    const response = await owner.post('/api/v1/internal/auth/login', { data: admin });
    assert.equal(response.status(), 200);
    ownerCsrf = (await response.json()).csrf_token;
    assert.equal(typeof ownerCsrf, 'string');
    await secureCookie(owner, 'owner_session');
    assert.equal((await owner.get('/api/v1/internal/orders')).status(), 200);
  });

  let orderId;
  let linkToken;
  await check('fictional_intake_private_link_uses_the_public_origin', async () => {
    const response = await seller.post('/api/v1/orders', { data: {
      seller_name: '公网权限验收虚构卖家',
      product_name: `Fictional HTTPS Desk Cable Clips ${Date.now()}`,
      is_demo: true,
      notes: '虚构公网验收，仅测试缺尺寸获客与访问控制，不批准生产或发布。',
      facts: [
        { key: 'material', value: 'silicone', confirmed: true },
        { key: 'pack_quantity', value: '6', unit: 'pieces', confirmed: true },
        { key: 'use_cases', value: 'Organizing desk cables', confirmed: true },
      ],
    } });
    assert.equal(response.status(), 201);
    const created = await response.json();
    orderId = created.id;
    const privateUrl = new URL(created.private_url);
    assert.equal(privateUrl.origin, publicUrl);
    assert.equal(privateUrl.pathname, `/o/${orderId}`);
    linkToken = new URLSearchParams(privateUrl.hash.slice(1)).get('token');
    assert.ok(linkToken && linkToken.length >= 20);
  });
  await check('seller_exchange_uses_secure_cookie_and_reads_own_order', async () => {
    const response = await seller.post('/api/v1/order-access/exchange', {
      data: { order_id: orderId, token: linkToken },
    });
    assert.equal(response.status(), 200);
    sellerCsrf = (await response.json()).csrf_token;
    assert.equal(typeof sellerCsrf, 'string');
    await secureCookie(seller, 'seller_session');
    const order = await seller.get(`/api/v1/orders/${orderId}`);
    assert.equal(order.status(), 200);
    assert.equal((await order.json()).id, orderId);
  });
  await check('entry_only_visitor_cannot_read_private_order_or_assets', async () => {
    assert.equal((await visitor.get(`/api/v1/orders/${orderId}`)).status(), 401);
    assert.equal((await visitor.get(`/api/v1/orders/${orderId}/assets`)).status(), 401);
  });
  await check('wrong_csrf_is_rejected_on_authenticated_write', async () => {
    const response = await seller.post(`/api/v1/orders/${orderId}/messages`, {
      headers: { 'X-CSRF-Token': 'deliberately-invalid-csrf' },
      data: { expected_revision: 1, content: 'This write must be rejected.' },
    });
    assert.equal(response.status(), 403);
  });
  await check('correct_csrf_uploads_a_real_image_with_private_download', async () => {
    const uploaded = await uploadStage('uploaded_response', async () => {
      const response = await seller.post(`/api/v1/orders/${orderId}/assets`, {
        headers: { 'X-CSRF-Token': sellerCsrf },
        multipart: { file: {
          name: 'fictional-reference.png', mimeType: 'image/png',
          buffer: await readFile(path.join(root, 'fixtures/demo/reference.png')),
        } },
      });
      assert.equal(response.status(), 201);
      return response;
    });
    const expectedPath = await uploadStage('metadata_shape', async () => {
      const asset = await uploaded.json();
      assert.ok(asset && typeof asset === 'object');
      assert.equal(typeof asset.id, 'string');
      assert.match(asset.id, /^[0-9a-f-]{36}$/);
      const value = `/api/v1/orders/${orderId}/assets/${asset.id}`;
      assert.equal(asset.url, value);
      return value;
    });
    await uploadStage('seller_download', async () => {
      const downloaded = await seller.get(expectedPath);
      assert.equal(downloaded.status(), 200);
      assert.equal(downloaded.headers()['content-type'], 'image/png');
      const bytes = await downloaded.body();
      assert.deepEqual([...bytes.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
    });
    await uploadStage('visitor_deny', async () => {
      assert.equal((await visitor.get(expectedPath)).status(), 401);
    });
    await uploadStage('owner_download', async () => {
      assert.equal((await owner.get(expectedPath)).status(), 200);
    });
  });
  await persist(true);
} catch (error) {
  failureName ??= errorName(error);
  if (!checks.some(item => item.name === activeCheck && item.passed === false)) checks.push({ name: activeCheck, passed: false });
  await persist(false).catch(() => {});
  console.error(`FAIL ${activeCheck}${activeStage ? ` / ${activeStage}` : ''} (${failureName})`);
  process.exitCode = 1;
} finally {
  if (ownerCsrf) await owner.post('/api/v1/internal/auth/logout', { headers: { 'X-CSRF-Token': ownerCsrf }, timeout: 10000 }).catch(() => {});
  if (sellerCsrf) await seller.post('/api/v1/order-access/logout', { headers: { 'X-CSRF-Token': sellerCsrf }, timeout: 10000 }).catch(() => {});
  for (const client of contexts) await client.dispose();
}
