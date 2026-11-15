// `npm run audit:ui` 的跨平台入口：自动找到装了 playwright 的 Python。
// 优先 backend/.venv（Windows / POSIX 两种布局），都没有再退回系统 Python。
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const script = path.join(here, 'regression.py');
const args = process.argv.slice(2);

const candidates = [
  path.join(root, 'backend/.venv/Scripts/python.exe'),
  path.join(root, 'backend/.venv/bin/python'),
];
const py = candidates.find((p) => existsSync(p))
  ?? (process.platform === 'win32' ? 'python' : 'python3');

const result = spawnSync(py, [script, ...args], { stdio: 'inherit' });
if (result.error) {
  console.error(`无法启动 Python (${py}): ${result.error.message}`);
  process.exit(2);
}
process.exit(result.status ?? 1);
