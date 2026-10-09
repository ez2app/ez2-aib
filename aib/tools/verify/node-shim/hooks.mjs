// SPDX-License-Identifier: Apache-2.0
// ESM 載入鉤子(舊版 Node 沒有 registerHooks 時,audit.cjs 才用 module.register 掛上):每個 file: 模組記一筆 open,跟 require() 一樣進鑰匙。
import { appendFileSync, readFileSync, realpathSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { dirname, join, basename } from 'node:path';
import { statSync } from 'node:fs';
const TRACE = process.env.EZ2_VERIFY_TRACE;
const ESC = { [String.fromCharCode(0x2028)]: '\\u2028', [String.fromCharCode(0x2029)]: '\\u2029', [String.fromCharCode(0x85)]: '\\u0085' };
const ESC_RE = new RegExp('[' + Object.keys(ESC).join('') + ']', 'g');
function emit(o) {
  o.pid = process.pid;
  const s = JSON.stringify(o).replace(ESC_RE, c => ESC[c]);
  try { appendFileSync(TRACE, s + '\n'); } catch (e) { /* 寫不進 ⇒ 核心判量不到 */ }
}
// 10-07:import 的檔所屬的最近一個 package.json("type" / "exports" 由它決定;Node 內部讀,公開 fs 看不到)
const scopeSeen = new Set();
function pkgScope(file) {
  let d = dirname(file);
  while (d && !scopeSeen.has(d)) {
    scopeSeen.add(d);
    const pj = join(d, 'package.json');
    let isFile = false; try { isFile = statSync(pj).isFile(); } catch (e) { isFile = false; }
    if (isFile) {
      let real = pj; try { real = realpathSync(pj); } catch (e) { /* 照原路徑 */ }
      emit({ ch: 'open', path: pj, real, sha256: createHash('sha256').update(readFileSync(pj)).digest('hex'), via: 'node' });
      return;
    }
    if (basename(d) === 'node_modules') return;
    const up = dirname(d); if (up === d) return; d = up;
  }
}
export async function load(url, context, nextLoad) {
  if (TRACE && url.startsWith('file:')) {
    try {
      const p = fileURLToPath(url);
      if (existsSync(p)) {
        let real = p; try { real = realpathSync(p); } catch (e) { /* 照原路徑 */ }
        emit({ ch: 'open', path: p, real, sha256: createHash('sha256').update(readFileSync(p)).digest('hex'), via: 'node' });
        pkgScope(p);
      } else emit({ ch: 'missing', path: p });
    } catch (e) { emit({ ch: 'unmeasured', why: 'node-esm-hook-error' }); }
  }
  return nextLoad(url, context);
}
