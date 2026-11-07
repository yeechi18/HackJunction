import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const lines = existsSync(path.join(root, '.env')) ? readFileSync(path.join(root, '.env'), 'utf8') : '';
const config = Object.fromEntries(lines.split(/\r?\n/).filter(line => line.trim() && !line.trim().startsWith('#')).map(line => {
  const at = line.indexOf('=');
  return [line.slice(0, at).trim(), line.slice(at + 1).trim().replace(/^(['"])(.*)\1$/, '$2')];
}));
const value = key => process.env[key] ?? config[key];
let failed = false;
function report(name, ok, note) {
  console.log(`${ok ? 'OK' : 'MISSING'} ${name}${note ? `: ${note}` : ''}`);
  failed ||= !ok;
}
report('Node.js', Number(process.versions.node.split('.')[0]) >= 22, process.version);
const uv = spawnSync('uv', ['--version'], { cwd: root, encoding: 'utf8' });
report('uv', uv.status === 0, uv.status === 0 ? uv.stdout.trim() : 'install uv and reopen your terminal');
report('backend .env', existsSync(path.join(root, '.env')), 'run node scripts/init-env.mjs if missing');
report('frontend environment', existsSync(path.join(root, 'apps/web/.env.local')));
report('MiMo key', Boolean(value('MIMO_API_KEY')), 'presence only; actual API validation is separate');
report('registration email', Boolean(value('SMTP_PASSWORD')), 'SMTP authorization code presence only; send a verification code from the register page to validate delivery');
let address;
try { address = new URL(value('DATABASE_URL')?.replace('postgresql+psycopg:', 'postgresql:')); }
catch { report('database configuration', false, 'set DATABASE_URL in .env'); }
if (address) {
  const connected = await new Promise(resolve => {
    const socket = net.createConnection({ host: address.hostname, port: Number(address.port || 5432) });
    const finish = ok => { socket.destroy(); resolve(ok); };
    socket.setTimeout(2000);
    socket.once('connect', () => finish(true));
    socket.once('timeout', () => finish(false));
    socket.once('error', () => finish(false));
  });
  report('PostgreSQL TCP', connected, 'connectivity only; company-db-init verifies login and schema');
}
for (const [name, port] of [['web', 3000], ['api', 8000]]) {
  const free = await new Promise(resolve => {
    const server = net.createServer();
    server.once('error', () => resolve(false));
    server.listen(port, '127.0.0.1', () => server.close(() => resolve(true)));
  });
  console.log(`INFO ${name} port ${port}: ${free ? 'available' : 'occupied (may be this project)'}`);
}
process.exitCode = failed ? 1 : 0;
