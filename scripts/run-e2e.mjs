// Local-only test helper: credentials go through child environment, never command arguments.
import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const environment = { ...process.env };
const credentialFile = path.join(root, 'var', 'local-admin.txt');
if (!environment.E2E_OWNER_PASSWORD && existsSync(credentialFile)) {
  const line = readFileSync(credentialFile, 'utf8').split(/\r?\n/).find(value => value.startsWith('password='));
  if (line) environment.E2E_OWNER_PASSWORD = line.slice('password='.length);
}
if (!environment.PLAYWRIGHT_CHANNEL && process.platform === 'darwin' && os.release().startsWith('22.') && existsSync('/Applications/Google Chrome.app')) {
  environment.PLAYWRIGHT_CHANNEL = 'chrome';
  console.log('Using installed Chrome with a fresh temporary profile on macOS 13.');
}
const result = spawnSync(process.execPath, [
  path.join(root, 'apps/web/node_modules/@playwright/test/cli.js'),
  'test', '--config', path.join(root, 'apps/web/playwright.config.ts'), ...process.argv.slice(2),
], { cwd: root, env: environment, stdio: 'inherit' });
if (result.error) console.error('Could not launch the project test runner. Install locked frontend dependencies first.');
process.exitCode = result.status ?? 1;
