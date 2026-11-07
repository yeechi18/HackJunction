// Local visual verification. Uses a fresh temporary browser profile, never a user profile.
import { chromium } from '../apps/web/node_modules/playwright/index.mjs';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.join(root, 'var', 'reports', 'ui');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
});
const results = [];
try {
  for (const [name, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
    const context = await browser.newContext({ viewport: { width, height }, locale: 'zh-CN' });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const response = await page.goto('http://127.0.0.1:3000', { waitUntil: 'networkidle' });
    await page.getByRole('heading', { level: 1 }).waitFor();
    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth > document.documentElement.clientWidth);
    await page.screenshot({ path: path.join(output, `home-${name}.png`), fullPage: true });
    const result = { viewport: name, http_status: response.status(), horizontal_overflow: overflow,
      runtime_errors: errors, passed: response.ok() && !overflow && errors.length === 0 };
    results.push(result);
    console.log(`${result.passed ? 'PASS' : 'FAIL'} homepage ${name}`);
    await context.close();
  }
} finally {
  await browser.close();
}
await writeFile(path.join(output, 'visual-smoke.json'), JSON.stringify(results, null, 2));
if (results.some(result => !result.passed)) process.exitCode = 1;
