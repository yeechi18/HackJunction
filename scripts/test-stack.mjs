// Isolated E2E: no developer credentials, no developer DB, no reuse of running servers.
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { existsSync } from 'node:fs';
import { cp, mkdir, mkdtemp, rm, symlink, unlink } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const windows = process.platform === 'win32';
const python = path.join(root, 'apps/server/.venv', windows ? 'Scripts/python.exe' : 'bin/python');
const webSource = path.join(root, 'apps/web');
// Next's webpack development entries cannot cross Windows drives through a junction.
const temporaryRoot = windows ? path.join(root, 'var') : os.tmpdir();
const nextCli = path.join(webSource, 'node_modules/next/dist/bin/next');
const playwrightCli = path.join(webSource, 'node_modules/@playwright/test/cli.js');
const owned = [];
let temporary;
let stopping;
let interrupted = false;

function start(command, args, { env, cwd = root, capture = false } = {}) {
  if (interrupted) throw new Error('Isolated test run was interrupted.');
  const child = spawn(command, args, {
    cwd, env, shell: false, windowsHide: true, detached: !windows,
    stdio: capture ? ['ignore', 'pipe', 'pipe'] : ['ignore', 'inherit', 'inherit'],
  });
  owned.push(child);
  child.failure = null;
  child.once('error', error => { child.failure = error; });
  return child;
}

async function captured(command, args, env, label) {
  const child = start(command, args, { env, capture: true });
  let output = '';
  child.stdout.on('data', data => { output += data.toString(); });
  // Do not forward diagnostics from config/hash setup, which can involve private environment.
  child.stderr.resume();
  const code = await new Promise(resolve => {
    child.once('error', () => resolve(-1));
    child.once('close', resolve);
  });
  if (code !== 0) throw new Error(`${label} failed; check the locked backend installation and local test configuration.`);
  return output.trim();
}

async function portAvailable(port) {
  await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', () => reject(new Error(`Port ${port} is occupied or unavailable; no running process was stopped.`)));
    server.listen(port, '127.0.0.1', () => server.close(resolve));
  });
}

async function waitFor(url, process, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (interrupted) throw new Error('Isolated test run was interrupted.');
    if (process.failure || process.exitCode !== null || process.signalCode !== null) {
      throw new Error('An isolated test server exited before becoming ready.');
    }
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1500) });
      if (response.ok) return;
    } catch { /* Keep waiting for the process we just started, never reuse an existing service. */ }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error('An isolated test server did not become ready in time.');
}

async function stop(child) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
  if (windows) {
    // Only the PID returned by our own spawn, plus its descendants; never kill by image name.
    const taskkill = path.join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'taskkill.exe');
    const terminator = spawn(taskkill, ['/PID', String(child.pid), '/T', '/F'], {
      shell: false, windowsHide: true, stdio: 'ignore',
    });
    await new Promise(resolve => {
      terminator.once('error', resolve);
      terminator.once('close', resolve);
    });
    return;
  }
  try { process.kill(-child.pid, 'SIGTERM'); } catch { return; }
  await Promise.race([
    new Promise(resolve => child.once('close', resolve)),
    new Promise(resolve => setTimeout(resolve, 2500)),
  ]);
  if (child.exitCode === null && child.signalCode === null) {
    try { process.kill(-child.pid, 'SIGKILL'); } catch { /* Already stopped. */ }
  }
}

async function cleanup() {
  if (stopping) return stopping;
  stopping = (async () => {
    for (const child of [...owned].reverse()) await stop(child);
    if (temporary) {
      const relative = path.relative(temporaryRoot, path.resolve(temporary));
      if (!relative || relative.startsWith('..') || path.isAbsolute(relative)
          || !path.basename(temporary).startsWith('hackjunction-e2e-')) {
        throw new Error('Refusing to clean an unexpected temporary test directory.');
      }
      // Unlink the dependency junction first, so recursive cleanup cannot touch the source tree.
      await unlink(path.join(temporary, 'web/node_modules')).catch(() => {});
      await rm(temporary, { recursive: true, force: true });
    }
  })();
  return stopping;
}

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.once(signal, () => { interrupted = true; void cleanup(); });
}

try {
  if (!existsSync(python) || !existsSync(nextCli) || !existsSync(playwrightCli)) {
    throw new Error('Install the locked Python/web dependencies and Playwright browser before starting this test stack.');
  }
  await Promise.all([portAvailable(8100), portAvailable(3100)]);
  const testEnvironment = {
    ...process.env, APP_ENV: 'test', MIMO_MODE: 'fake', MIMO_API_KEY: '',
    PUBLISH_MODE: 'simulated', PYTHONUTF8: '1', NEXT_TELEMETRY_DISABLED: '1',
    LANGSMITH_TRACING: 'false', LANGCHAIN_TRACING_V2: 'false',
  };
  // Read only the configured DB targets through the existing settings parser. Captured URLs are
  // passed to child environments and never printed. No .env is modified; local-admin.txt is never read.
  const configuration = JSON.parse(await captured(python, ['-c', [
    'import json', 'from company.config import Settings', 'from sqlalchemy.engine import make_url',
    's=Settings()', 'd=make_url(s.database_url.get_secret_value())',
    't=make_url(s.test_database_url.get_secret_value())',
    "assert t.database and t.database.endswith('_test'), 'Use a separate _test database'",
    "assert d.database != t.database, 'Development and E2E database names must differ'",
    "print(json.dumps({'test_url':s.test_database_url.get_secret_value()}))",
  ].join('\n')], testEnvironment, 'Test database isolation check'));
  if (process.argv.includes('--check')) {
    console.log('Isolation prerequisites passed: test DB differs from development; ports 8100/3100 are free.');
  } else {
    const password = randomBytes(24).toString('base64url');
    const ownerHash = await captured(python, ['-c',
      "import os; from pwdlib import PasswordHash; print(PasswordHash.recommended().hash(os.environ['STACK_OWNER_PASSWORD']))"],
      { ...testEnvironment, STACK_OWNER_PASSWORD: password }, 'Temporary owner setup');
    await mkdir(temporaryRoot, { recursive: true });
    temporary = await mkdtemp(path.join(temporaryRoot, 'hackjunction-e2e-'));
    const isolatedWeb = path.join(temporary, 'web');
    const excluded = new Set(['node_modules', '.next', 'test-results', 'playwright-report']);
    await cp(webSource, isolatedWeb, {
      recursive: true,
      filter: source => {
        const relative = path.relative(webSource, source);
        const parts = relative.split(path.sep);
        return !parts.some(part => excluded.has(part) || part.startsWith('.env') || part.endsWith('.tsbuildinfo'));
      },
    });
    await symlink(path.join(webSource, 'node_modules'), path.join(isolatedWeb, 'node_modules'), windows ? 'junction' : 'dir');
    const environment = {
      ...testEnvironment, DATABASE_URL: configuration.test_url, TEST_DATABASE_URL: configuration.test_url,
      MAIL_MODE: 'test', SMTP_PASSWORD: '', MAIL_TEST_DIR: path.join(temporary, 'mail'),
      E2E_MAIL_DIR: path.join(temporary, 'mail'),
      PUBLIC_APP_URL: 'http://127.0.0.1:3100', API_ORIGIN: 'http://127.0.0.1:8100',
      E2E_BASE_URL: 'http://127.0.0.1:3100', E2E_OWNER_PASSWORD: password,
      E2E_REFERENCE_IMAGE: path.join(root, 'fixtures/demo/reference.png'),
      E2E_ARTIFACT_DIR: path.join(root, 'var/reports/ui-e2e'),
      ADMIN_PASSWORD_HASH: ownerHash, SESSION_SECRET: randomBytes(48).toString('base64url'),
      ASSET_DIR: path.join(temporary, 'assets'), STORAGE_BACKEND: 'local',
    };
    if (!environment.PLAYWRIGHT_CHANNEL && process.platform === 'darwin'
        && os.release().startsWith('22.') && existsSync('/Applications/Google Chrome.app')) {
      environment.PLAYWRIGHT_CHANNEL = 'chrome';
    }
    console.log('Initializing only the configured test database; developer config and credentials are unchanged.');
    await captured(python, ['-m', 'company.cli.init_db'], environment, 'Test database initialization');
    const api = start(python, ['-m', 'company.cli.serve', '--host', '127.0.0.1', '--port', '8100'], { env: environment });
    await waitFor('http://127.0.0.1:8100/api/v1/health/ready', api, 30000);
    start(python, ['-m', 'company.worker'], { env: environment });
    // A copied web source has its own .next directory/lock. Installed modules are linked, not copied.
    const web = start(process.execPath, [nextCli, 'dev', '--webpack', '--hostname', '127.0.0.1', '--port', '3100'], {
      env: environment, cwd: isolatedWeb,
    });
    await waitFor('http://127.0.0.1:3100', web);
    const extra = process.argv.slice(2).filter(argument => argument !== '--check');
    const runner = start(process.execPath, [playwrightCli, 'test', '--config', path.join(isolatedWeb, 'playwright.config.ts'), ...extra], {
      env: environment, cwd: isolatedWeb,
    });
    const result = await new Promise(resolve => {
      runner.once('error', () => resolve(1));
      runner.once('close', code => resolve(code ?? 1));
    });
    if (result !== 0) throw new Error('Isolated Playwright checks failed. No developer service was stopped.');
    console.log('Isolated Playwright checks passed using offline fixtures; no live MiMo call was made.');
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = interrupted ? 130 : 1;
} finally {
  await cleanup();
}
