// Temporary password-protected HTTPS demo. Does not alter .env or the local dev stack.
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { createWriteStream, existsSync } from 'node:fs';
import { cp, mkdir, mkdtemp, readFile, symlink, writeFile } from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startGateway } from './lib/demo-gateway.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const windows = process.platform === 'win32';
const python = path.join(root, 'apps/server/.venv', windows ? 'Scripts/python.exe' : 'bin/python');
const webSource = path.join(root, 'apps/web');
const nextCli = path.join(webSource, 'node_modules/next/dist/bin/next');
const localTunnel = path.join(root, 'var/bin', windows ? 'cloudflared.exe' : 'cloudflared');
const tunnelBin = process.env.CLOUDFLARED_BIN ?? (existsSync(localTunnel) ? localTunnel : 'cloudflared');
const directory = path.join(root, 'var/public-demo');
const accessFile = path.join(root, 'var/public-access.txt');
const stateFile = path.join(directory, 'state.json');
const children = [];
let gateway;
let stopping;
let resolveStop;
const stopped = new Promise(resolve => { resolveStop = resolve; });

async function stop(child) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
  if (windows) {
    const killer = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { shell: false, windowsHide: true, stdio: 'ignore' });
    await new Promise(resolve => { killer.once('error', resolve); killer.once('close', resolve); });
  } else {
    try { process.kill(-child.pid, 'SIGTERM'); } catch { return; }
    await Promise.race([new Promise(resolve => child.once('close', resolve)), new Promise(resolve => setTimeout(resolve, 2000))]);
    if (child.exitCode === null && child.signalCode === null) {
      try { process.kill(-child.pid, 'SIGKILL'); } catch { /* Already stopped. */ }
    }
  }
}

async function cleanup() {
  if (stopping) return stopping;
  stopping = (async () => {
    for (const child of [...children].reverse()) await stop(child);
    if (gateway) { gateway.closeAllConnections(); await new Promise(resolve => gateway.close(resolve)); }
    try {
      const state = JSON.parse(await readFile(stateFile, 'utf8'));
      await writeFile(stateFile, JSON.stringify({ ...state, running: false }, null, 2));
    } catch { /* A run may have failed before state was written. */ }
    resolveStop();
  })();
  return stopping;
}
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => void cleanup());

function launch(name, command, args, { env = process.env, cwd = root, observe, capture = false, critical = false } = {}) {
  if (stopping) throw new Error('Demo startup was interrupted.');
  const child = spawn(command, args, { cwd, env, shell: false, windowsHide: true, detached: !windows, stdio: ['ignore', 'pipe', 'pipe'] });
  children.push(child);
  const log = createWriteStream(path.join(directory, name + '.log'), { flags: 'a', mode: 0o600 });
  child.failure = null;
  child.output = '';
  const collect = data => {
    const text = data.toString();
    if (capture) child.output += text;
    else log.write(data);
    observe?.(text);
  };
  child.stdout.on('data', collect); child.stderr.on('data', collect);
  const failed = () => {
    if (critical && !stopping) {
      process.exitCode = 1;
      console.error(`Public demo ${name} stopped. Closing this demo only.`);
      void cleanup();
    }
  };
  child.once('error', () => { child.failure = new Error(`${name} could not start; see private demo logs.`); failed(); });
  child.once('close', () => { log.end(); failed(); });
  return child;
}

async function completed(child, label) {
  const code = await new Promise(resolve => { child.once('error', () => resolve(-1)); child.once('close', resolve); });
  if (code !== 0) throw new Error(`${label} failed; see var/public-demo logs.`);
  return child.output;
}

async function available(port) {
  await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', () => reject(new Error(`Demo port ${port} is occupied; no existing process was stopped.`)));
    server.listen(port, '127.0.0.1', () => server.close(resolve));
  });
}

async function ready(url, child) {
  for (let attempt = 0; attempt < 120; attempt++) {
    if (stopping || child.failure || child.exitCode !== null || child.signalCode !== null) throw new Error('A demo service stopped during startup.');
    try { if ((await fetch(url, { signal: AbortSignal.timeout(1500) })).ok) return; } catch { /* Wait for our own service. */ }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error('The demo service did not become ready.');
}

async function publicEntryReady(url, tunnel) {
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    if (stopping || tunnel.failure || tunnel.exitCode !== null || tunnel.signalCode !== null) throw new Error('The HTTPS tunnel stopped.');
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(10000) });
      await response.body?.cancel();
      if (response.status === 401 && response.headers.get('www-authenticate')?.startsWith('Basic ')) return;
    } catch { /* A URL being issued does not mean the edge connection is ready. */ }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new Error('The tunnel URL was issued, but public HTTPS access did not become available; inspect tunnel.log.');
}

try {
  await mkdir(directory, { recursive: true });
  if (!existsSync(python) || !existsSync(nextCli)) throw new Error('Install the existing locked dependencies first.');
  await Promise.all([available(3200), available(3300), available(8200)]);
  const configProcess = launch('configuration', python, ['-c', [
    'import json', 'from company.config import Settings', 'from sqlalchemy.engine import make_url',
    's=Settings()',
    "assert s.session_secret and len(s.session_secret.get_secret_value()) >= 32",
    'assert s.admin_password_hash and s.mimo_api_key',
    "u=make_url(s.database_url.get_secret_value()).set(database='hackjunction_public_demo')",
    "assert u.host in ('127.0.0.1','localhost')",
    "print(json.dumps({'database_url':u.render_as_string(hide_password=False)}))",
  ].join('\n')], { capture: true });
  const configuration = JSON.parse(await completed(configProcess, 'Private configuration check'));
  const credentials = { username: 'demo', password: randomBytes(24).toString('base64url') };
  await writeFile(accessFile, `username=${credentials.username}\npassword=${credentials.password}\n`, { mode: 0o600 });
  gateway = await startGateway({ port: 3300, target: 'http://127.0.0.1:3200', ...credentials });
  console.log('Starting password-protected temporary HTTPS tunnel…');
  let tunnelText = '';
  let resolveUrl;
  const urlReady = new Promise(resolve => { resolveUrl = resolve; });
  const tunnelProtocol = process.env.TUNNEL_TRANSPORT_PROTOCOL ?? 'auto';
  if (!['auto', 'http2', 'quic'].includes(tunnelProtocol)) throw new Error('TUNNEL_TRANSPORT_PROTOCOL must be auto, http2 or quic.');
  const tunnel = launch('tunnel', tunnelBin, ['tunnel', '--url', 'http://127.0.0.1:3300', '--protocol', tunnelProtocol, '--no-autoupdate'], {
    critical: true,
    env: { ...process.env, ...(process.argv.includes('--us') ? { TUNNEL_REGION: 'us' } : {}) },
    observe: text => {
      tunnelText = (tunnelText + text).slice(-30000);
      const match = tunnelText.match(/https:\/\/[a-z0-9-]+\.trycloudflare\.com/);
      if (match) resolveUrl(match[0]);
    },
  });
  const publicUrl = await Promise.race([
    urlReady,
    new Promise((_, reject) => {
      const deadline = setTimeout(() => reject(new Error('Tunnel URL was not issued within 60 seconds; inspect tunnel.log.')), 60000);
      deadline.unref();
      tunnel.once('error', () => { clearTimeout(deadline); reject(new Error('Install cloudflared or set CLOUDFLARED_BIN.')); });
      tunnel.once('close', () => { clearTimeout(deadline); reject(new Error('The temporary tunnel exited before startup.')); });
    }),
  ]);
  const environment = {
    ...process.env, APP_ENV: 'production', PUBLIC_APP_URL: publicUrl,
    DATABASE_URL: configuration.database_url, MIMO_MODE: 'live', PUBLISH_MODE: 'simulated',
    SESSION_SECRET: randomBytes(48).toString('base64url'),
    ASSET_DIR: path.join(directory, 'assets'), API_ORIGIN: 'http://127.0.0.1:8200',
    PYTHONUTF8: '1', NEXT_TELEMETRY_DISABLED: '1', LANGSMITH_TRACING: 'false', LANGCHAIN_TRACING_V2: 'false',
  };
  console.log('Initializing dedicated public demo database; local developer data is unchanged…');
  await completed(launch('database-init', python, ['-m', 'company.cli.init_db'], { env: environment }), 'Demo database initialization');
  const isolatedWeb = await mkdtemp(path.join(directory, 'web-'));
  const excluded = new Set(['node_modules', '.next', 'test-results', 'playwright-report']);
  await cp(webSource, isolatedWeb, { recursive: true, filter: source => !path.relative(webSource, source).split(path.sep).some(part => excluded.has(part) || part.startsWith('.env') || part.endsWith('.tsbuildinfo')) });
  await symlink(path.join(webSource, 'node_modules'), path.join(isolatedWeb, 'node_modules'), windows ? 'junction' : 'dir');
  const webEnvironment = { ...environment, MIMO_API_KEY: '', DATABASE_URL: '', ADMIN_PASSWORD_HASH: '', SESSION_SECRET: '' };
  console.log('Building a separate production frontend…');
  await completed(launch('web-build', process.execPath, [nextCli, 'build', '--webpack'], { cwd: isolatedWeb, env: webEnvironment }), 'Demo production build');
  const api = launch('api', python, ['-m', 'company.cli.serve', '--port', '8200'], { env: environment, critical: true });
  await ready('http://127.0.0.1:8200/api/v1/health/ready', api);
  const worker = launch('worker', python, ['-m', 'company.worker'], { env: environment, critical: true });
  const web = launch('web', process.execPath, [nextCli, 'start', '--hostname', '127.0.0.1', '--port', '3200'], { cwd: isolatedWeb, env: webEnvironment, critical: true });
  await ready('http://127.0.0.1:3200', web);
  if (stopping || [tunnel, api, worker, web].some(child => child.failure || child.exitCode !== null || child.signalCode !== null)) {
    throw new Error('A public demo service stopped during startup.');
  }
  console.log('Verifying the external HTTPS password gate…');
  await publicEntryReady(publicUrl, tunnel);
  await writeFile(stateFile, JSON.stringify({
    url: publicUrl, running: true, started_at: new Date().toISOString(),
    model_mode: 'live', publish_mode: 'simulated', database: 'hackjunction_public_demo',
    ports: { gateway: 3300, web: 3200, api: 8200 }, credential_file: 'var/public-access.txt',
    supervisor_pid: process.pid, child_pids: [tunnel, api, worker, web].map(child => child.pid),
  }, null, 2));
  console.log(`Public demo ready: ${publicUrl}`);
  console.log('Entry credentials: var/public-access.txt; owner login: existing var/local-admin.txt.');
  console.log('Keep this process and computer running. Ctrl+C stops only the public demo.');
  await stopped;
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
  await cleanup();
}
