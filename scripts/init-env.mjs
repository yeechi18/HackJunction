import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
for (const [source, target] of [['.env.example', '.env'], ['apps/web/.env.local.example', 'apps/web/.env.local']]) {
  const destination = path.join(root, target);
  if (existsSync(destination)) {
    console.log(`Kept existing ${target}`);
    continue;
  }
  const template = path.join(root, source);
  if (!existsSync(template)) {
    console.error(`Missing ${source}; finish project setup first.`);
    process.exitCode = 1;
    continue;
  }
  let content = readFileSync(template, 'utf8');
  if (target === '.env') content = content.replace(/^SESSION_SECRET=$/m, `SESSION_SECRET=${randomBytes(32).toString('hex')}`);
  writeFileSync(destination, content, { encoding: 'utf8', flag: 'wx', mode: 0o600 });
  console.log(`Created ${target}; fill private values locally.`);
}
