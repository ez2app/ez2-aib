// SPDX-License-Identifier: Apache-2.0
'use strict';
// 影子續跑的 Node 接頭(docs/SPEC-shadow-adapter-1004.md):由 NODE_OPTIONS="--require <這支>" 掛上,
// 子行程繼承 NODE_OPTIONS 就自動掛上。事件格式與 Python 接頭(sitecustomize_audit.py)同一種,核心一行不改。
// 量不到就說量不到(unmeasured);碰網路報 external;每個行程寫 inherit 與 exit。
const TRACE = process.env.EZ2_VERIFY_TRACE;
const ESC = { [String.fromCharCode(0x2028)]: '\\u2028', [String.fromCharCode(0x2029)]: '\\u2029', [String.fromCharCode(0x85)]: '\\u0085' };
const ESC_RE = new RegExp('[' + Object.keys(ESC).join('') + ']', 'g');
if (TRACE && !global.__ez2_node_audit && require('worker_threads').isMainThread) {   // 載入鉤子那條執行緒也會預載本檔:只在主執行緒掛
  global.__ez2_node_audit = true;
  const fs = require('fs');
  const path = require('path');
  const crypto = require('crypto');
  const O = {
    appendFileSync: fs.appendFileSync, readFileSync: fs.readFileSync, readdirSync: fs.readdirSync,
    statSync: fs.statSync, existsSync: fs.existsSync, realpathSync: fs.realpathSync, openSync: fs.openSync,
    writeFileSync: fs.writeFileSync, writeSync: fs.writeSync, fstatSync: fs.fstatSync,
  };
  const SELF_DIR = __dirname;
  const SKIP_NAMES = new Set(['__pycache__', '.git', '.DS_Store', '.hypothesis']);   // .hypothesis:同 shadow_tracer.TOOL_CACHE_DIRS
  let inHook = false;

  function emit(obj) {
    obj.pid = obj.pid || process.pid;
    let s = JSON.stringify(obj).replace(ESC_RE, c => ESC[c]);
    try { writeTrace(Buffer.from(s + '\n')); } catch (e) { /* 寫不進追蹤檔:核心會因缺 inherit/exit 判量不到 */ }
  }
  // 10-24(@044e0):以前每筆 appendFileSync = 開 → 寫 → 關;APFS 每次關檔重寫整塊,追蹤實際落盤放大約 76 倍。
  //   改成留一個 'a'(O_APPEND)fd、每筆 writeSync 一次;寫前比 fstat 的 dev+ino,號碼被測試重用就重開(同 Python 接頭)。
  let traceFd = null, traceId = null;
  function writeTrace(buf) {
    if (traceFd !== null) {
      try { const st = O.fstatSync(traceFd); if (st.dev + ':' + st.ino !== traceId) traceFd = null; } catch (e) { traceFd = null; }
    }
    if (traceFd === null) {
      traceFd = O.openSync(TRACE, 'a');
      const st = O.fstatSync(traceFd);
      traceId = st.dev + ':' + st.ino;
    }
    let off = 0;
    while (off < buf.length) off += O.writeSync(traceFd, buf, off, buf.length - off);
  }
  function abs(p) {
    try {
      if (p instanceof URL) p = p.pathname;
      if (Buffer.isBuffer(p)) p = p.toString();
      if (typeof p !== 'string') return null;
      return path.resolve(p);
    } catch (e) { return null; }
  }
  function own(p) { return p === TRACE || p === TRACE + '.t0' || p.startsWith(SELF_DIR + path.sep); }
  function real(p) { try { return O.realpathSync(p); } catch (e) { return p; } }
  function sha(p) { try { return crypto.createHash('sha256').update(O.readFileSync(p)).digest('hex'); } catch (e) { return null; } }
  function guard(fn) { if (inHook) return; inHook = true; try { fn(); } catch (e) { emit({ ch: 'unmeasured', why: 'node-hook-error' }); } finally { inHook = false; } }

  function recordRead(p) {
    guard(() => {
      const a = abs(p); if (!a || own(a)) return;
      if (!O.existsSync(a)) { emit({ ch: 'missing', path: a }); return; }
      let st; try { st = O.statSync(a); } catch (e) { return; }
      if (st.isDirectory()) return;
      emit({ ch: 'open', path: a, real: real(a), sha256: sha(a), via: 'node' });
    });
  }
  function recordList(p) {
    guard(() => {
      const a = abs(p); if (!a || own(a)) return;
      let names;
      try { names = O.readdirSync(a).filter(n => !SKIP_NAMES.has(n) && !/\.py[co]$/.test(n)).sort(); }
      catch (e) { emit({ ch: 'missing', path: a }); return; }
      const d = crypto.createHash('sha256').update(names.join('\n'), 'utf8').digest('hex');
      emit({ ch: 'listdir', path: a, names_sha256: d });
    });
  }
  function recordProbe(p) {
    guard(() => {
      const a = abs(p); if (!a || own(a)) return;
      let state = 'missing';
      try { const st = O.statSync(a); state = st.isDirectory() ? 'dir' : (st.isFile() ? 'file' : 'missing'); } catch (e) { /* missing */ }
      emit({ ch: 'probe', path: a, state });
    });
  }
  function recordWrite(p) { guard(() => { const a = abs(p); if (a && !own(a)) emit({ ch: 'write', path: a, real: real(a) }); }); }
  const isWriteFlag = f => typeof f === 'string' ? /[wa+]/.test(f) : (typeof f === 'number' && (f & 3) !== 0);

  // ── t0(第一個行程)與 inherit ──
  try {
    let empty = true; try { empty = O.statSync(TRACE).size === 0; } catch (e) { empty = true; }
    if (empty) {
      try { O.writeFileSync(TRACE + '.t0', ''); } catch (e) { /* 標記檔寫不了:產物規則保守 */ }
      const snap = {};
      for (const d of ['/tmp', '/private/tmp', process.env.TMPDIR || '']) {
        const dd = d.replace(/\/+$/, ''); if (!dd || snap[dd]) continue;
        try { snap[dd] = O.readdirSync(dd).sort(); } catch (e) { continue; }
        const r = real(dd); if (r !== dd && !snap[r]) snap[r] = snap[dd];
      }
      emit({ ch: 't0', ns: Date.now() * 1e6, tmp: snap });
    }
  } catch (e) { /* 記不到 t0 ⇒ 分析端不用產物規則(保守) */ }
  emit({ ch: 'inherit', via: 'node' });
  process.on('exit', () => emit({ ch: 'exit' }));

  // ── fs:同步、回呼、promises ──
  let depth = 0;   // readFileSync 內部會再叫 openSync:只記最外層那一次
  const wrap = (obj, name, before) => {
    const orig = obj[name]; if (typeof orig !== 'function') return;
    obj[name] = function (...args) {
      if (depth === 0) before(args);
      depth++;
      try { return orig.apply(this, args); } finally { depth--; }
    };
    Object.defineProperty(obj[name], 'name', { value: name });
    // 10-08(commander.js 實測 4 支假紅):原函式身上的屬性一起帶過去 —— 最要緊的是 util.promisify.custom
    //   (execFile / exec / fs.exists 都有):少了它,promisify(execFile) 解出來是字串不是 {stdout, stderr} ⇒ 觀測者改了被觀測物。
    for (const k of Reflect.ownKeys(orig)) {
      if (k === 'name' || k === 'length' || k === 'prototype') continue;
      try { Object.defineProperty(obj[name], k, Object.getOwnPropertyDescriptor(orig, k)); } catch (e) { /* 不可設定的就算了 */ }
    }
  };
  for (const n of ['readFileSync', 'readFile']) wrap(fs, n, a => { if (typeof a[0] !== 'number') recordRead(a[0]); });
  for (const n of ['readdirSync', 'readdir', 'opendirSync', 'opendir']) wrap(fs, n, a => recordList(a[0]));
  for (const n of ['statSync', 'lstatSync', 'existsSync', 'accessSync', 'stat', 'lstat', 'access', 'exists'])
    wrap(fs, n, a => recordProbe(a[0]));
  for (const n of ['openSync', 'open']) wrap(fs, n, a => { if (isWriteFlag(a[1])) recordWrite(a[0]); else recordRead(a[0]); });
  for (const n of ['writeFileSync', 'writeFile', 'appendFileSync', 'appendFile', 'createWriteStream', 'mkdirSync', 'mkdir',
                   'rmSync', 'rm', 'unlinkSync', 'unlink', 'renameSync', 'rename', 'copyFileSync', 'copyFile'])
    wrap(fs, n, a => recordWrite(a[0]));
  wrap(fs, 'createReadStream', a => recordRead(a[0]));
  const P = fs.promises;
  wrap(P, 'readFile', a => { if (typeof a[0] !== 'object' || a[0] instanceof URL || Buffer.isBuffer(a[0])) recordRead(a[0]); });
  wrap(P, 'readdir', a => recordList(a[0]));
  for (const n of ['stat', 'lstat', 'access']) wrap(P, n, a => recordProbe(a[0]));
  wrap(P, 'open', a => { if (isWriteFlag(a[1])) recordWrite(a[0]); else recordRead(a[0]); });
  for (const n of ['writeFile', 'appendFile', 'mkdir', 'rm', 'unlink', 'rename', 'copyFile']) wrap(P, n, a => recordWrite(a[0]));

  // ── 10-07(安全網抓到):Node 載入一個檔之前,會往上找最近的 package.json 看 "type"(ESM / CommonJS)、"exports" ——
  //   那是 Node 內部的 C++ 讀的,公開 fs 看不到。每載入一個模組就把它所屬的最近一個 package.json 記成輸入(同一個資料夾只找一次)。
  const pkgScopeSeen = new Set();
  function recordPkgScope(filename) {
    guard(() => {
      let d = path.dirname(abs(filename) || '');
      while (d && !pkgScopeSeen.has(d)) {
        pkgScopeSeen.add(d);
        const pj = path.join(d, 'package.json');
        let isFile = false;
        try { isFile = O.statSync(pj).isFile(); } catch (e) { isFile = false; }
        if (isFile) { inHook = false; recordRead(pj); inHook = true; return; }
        if (path.basename(d) === 'node_modules') return;
        const up = path.dirname(d); if (up === d) return; d = up;
      }
    });
  }
  // ── require():模組載入走內部 fs,公開 fs 看不到 ⇒ 掛在副檔名處理上 ──
  const Module = require('module');
  for (const ext of ['.js', '.cjs', '.json', '.node']) {
    const orig = Module._extensions[ext]; if (typeof orig !== 'function') continue;
    Module._extensions[ext] = function (m, filename) {
      if (ext === '.node') guard(() => emit({ ch: 'unmeasured', why: 'node-native-addon' }));
      recordRead(filename);
      recordPkgScope(filename);
      return orig.call(this, m, filename);
    };
  }
  // 啟動時 Node 也會讀目前工作目錄所屬的 package.json(掛上 ESM 載入器時就讀;安全網實測看到的)⇒ 一起記
  recordPkgScope(path.join(process.cwd(), '__aib_cwd__'));
  // ── import(ESM):載入鉤子在另一條執行緒,寫同一個追蹤檔 ──
  // 10-07:Node 26 對 module.register() 印 DEP0205 到 stderr ⇒ 解析 stderr 的測試全壞(c2-main-authority 實踩);
  //   有 registerHooks(同執行緒、同步)就用它,沿用上面的 recordRead;沒有才退回舊的 register + hooks.mjs
  try {
    if (typeof Module.registerHooks === 'function') {
      const { fileURLToPath } = require('url');
      Module.registerHooks({ load(url, context, nextLoad) {
        if (typeof url === 'string' && url.startsWith('file:')) recordRead(fileURLToPath(url));
        return nextLoad(url, context);
      } });
    } else if (typeof Module.register === 'function') Module.register('./hooks.mjs', require('url').pathToFileURL(__filename));
    else emit({ ch: 'unmeasured', why: 'node-no-esm-hooks' });
  } catch (e) { emit({ ch: 'unmeasured', why: 'node-esm-hook-failed' }); }

  // ── 子行程:argv + hooked(同語言、環境沒被洗掉才算掛上)──
  const cp = require('child_process');
  const here = path.dirname(SELF_DIR);
  function hookedFor(file, env) {
    env = env || process.env;
    if (String(env.EZ2_VERIFY_TRACE || '') !== TRACE) return false;
    const b = path.basename(String(file));
    if (b === 'node') return String(env.NODE_OPTIONS || '').includes(__filename);
    if (/^python[0-9.]*$/.test(b)) return String(env.PYTHONPATH || '').split(path.delimiter).includes(here);
    if (b === 'bash') return String(env.BASH_ENV || '') === path.join(here, 'bash-shim', '_trace.bash');
    return false;
  }
  function recordSpawn(file, args, opts, shell) {
    guard(() => {
      opts = opts || {};
      let argv = [String(file)].concat((args || []).map(String));
      if (shell || opts.shell) argv = ['sh', '-c', argv.join(' ')];
      const cwd = opts.cwd ? path.resolve(String(opts.cwd)) : process.cwd();
      const ev = { ch: 'argv', argv, cwd, hooked: hookedFor(argv[0], opts.env) };
      if (path.basename(argv[0]) === 'bash') ev.shell = 'bash';
      emit(ev);
    });
  }
  const norm = (args) => { // (file, [args], [opts]) 或 (file, [opts])
    const file = args[0]; let a = [], o = {};
    if (Array.isArray(args[1])) { a = args[1]; if (args[2] && typeof args[2] === 'object') o = args[2]; }
    else if (args[1] && typeof args[1] === 'object') o = args[1];
    return [file, a, o];
  };
  for (const n of ['spawn', 'spawnSync', 'execFile', 'execFileSync']) wrap(cp, n, a => { const [f, x, o] = norm(a); recordSpawn(f, x, o, false); });
  for (const n of ['exec', 'execSync']) wrap(cp, n, a => recordSpawn(a[0], [], (a[1] && typeof a[1] === 'object') ? a[1] : {}, true));
  wrap(cp, 'fork', a => { const [f, x, o] = norm(a); recordSpawn(process.execPath, [f].concat(x), Object.assign({ env: process.env }, o), false); });

  // ── 網路 / 執行緒 ──
  const net = require('net');
  const origConnect = net.Socket.prototype.connect;
  // 10-08(express 實測:70 支只沿用 8 支):測試自己開的伺服器(supertest 之類)、自己連回去 —— 不是外部。
  //   只認「同一個行程自己 listen 的」本機埠 / Unix socket 路徑;連到本機上別人開的服務(資料庫之類)照樣算外部。
  const ownPorts = new Set(), ownPaths = new Set();
  const origListen = net.Server.prototype.listen;
  const noteOwn = (srv) => guard(() => {
    const a = srv.address();
    if (a && typeof a === 'object') ownPorts.add(Number(a.port)); else if (typeof a === 'string') ownPaths.add(path.resolve(a));
  });
  net.Server.prototype.listen = function (...args) {
    guard(() => this.once('listening', () => noteOwn(this)));
    const r = origListen.apply(this, args);
    noteOwn(this);       // TCP 是同步綁好的,'listening' 事件延到下一輪才發 —— supertest 同一輪就連(express 實測)
    return r;
  };
  const LOOPBACK = new Set(['localhost', '127.0.0.1', '::1', '::ffff:127.0.0.1', '']);
  const toOwn = (args) => {
    let o = args[0];
    if (Array.isArray(o)) o = o[0];                       // 內部呼叫:正規化過的 [options, cb]
    if (o && typeof o === 'object') {
      if (o.path) return ownPaths.has(path.resolve(String(o.path)));
      return LOOPBACK.has(String(o.host == null ? 'localhost' : o.host)) && ownPorts.has(Number(o.port));
    }
    if (typeof o === 'string' && isNaN(Number(o))) return ownPaths.has(path.resolve(o));
    return LOOPBACK.has(String(args[1] == null ? 'localhost' : args[1])) && ownPorts.has(Number(o));
  };
  net.Socket.prototype.connect = function (...args) {
    guard(() => { if (!toOwn(args)) emit({ ch: 'external', why: 'network' }); });
    return origConnect.apply(this, args);
  };
  try {
    const wt = require('worker_threads');
    const W = wt.Worker;
    wt.Worker = class extends W { constructor(...a) { guard(() => emit({ ch: 'unmeasured', why: 'node-worker-thread' })); super(...a); } };
  } catch (e) { /* 沒有 worker_threads */ }
}
