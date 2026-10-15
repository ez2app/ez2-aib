"use strict";
// ez2-page-qr.js — QR code 編碼器(317 第 14 項,RL-124 A37 乙:只出 SVG)
//
// 零相依、零外連:公開頁不准把訪客 IP 交給 api.qrserver 那類第三方(與商家格 G5 同一條線)。
// 範圍刻意小:byte mode(UTF-8)、糾錯等級 M、版本 1–10 自動(最長 213 位元組;頁的 canonical 網址遠低於此)、
// 遮罩八種選罰分最低的。輸出一條 <path d> 的 SVG(幾百位元組,內聯進頁面,零請求)。
//
// 正確性怎麼守(尺 tools/ez2-page-qr-smoke.js):
//   1. GF(256) 與 Reed-Solomon 生成多項式對照**公開表**(EC 7 / 10 / 16 / 18 顆的係數指數);
//   2. 格式資訊 15 位元對照公開表(M 級八種遮罩);版本資訊 18 位元對照 v7 的公開值;
//   3. 結構不變量:三個定位圖樣、分隔線、時序線、暗模組、各版本的校正圖樣中心;
//   4. 自我一致:把畫好的矩陣反遮罩、沿 zigzag 讀回的碼字 = 送進去的資料 + EC 碼字;
//   ★ 沒有跑過獨立解碼器——真正的「已知答案」是手機掃得出來(交件單登記,候 the maintainer 掃一次)。

const EC_LEVEL_M = 0;   // 格式資訊裡 M = 00
// 各版本(1–10)在 M 級的區塊結構:[總碼字, 每塊 EC 碼字, [塊數, 每塊資料碼字], [塊數, 每塊資料碼字]?]
const BLOCKS_M = Object.freeze({
  1: [26, 10, [1, 16]], 2: [44, 16, [1, 28]], 3: [70, 26, [1, 44]], 4: [100, 18, [2, 32]], 5: [134, 24, [2, 43]],
  6: [172, 16, [4, 27]], 7: [196, 18, [4, 31]], 8: [242, 22, [2, 38], [2, 39]], 9: [292, 22, [3, 36], [2, 37]], 10: [346, 26, [4, 43], [1, 44]]
});
const ALIGNMENT = Object.freeze({ 1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50] });

// ---- GF(256),原始多項式 0x11D ----
const EXP = new Uint8Array(512), LOG = new Uint8Array(256);
(() => { let x = 1; for (let i = 0; i < 255; i += 1) { EXP[i] = x; LOG[x] = i; x <<= 1; if (x & 0x100) x ^= 0x11d; } for (let i = 255; i < 512; i += 1) EXP[i] = EXP[i - 255]; })();
function gfMul(a, b) { return a === 0 || b === 0 ? 0 : EXP[LOG[a] + LOG[b]]; }
// 生成多項式 (x - α^0)(x - α^1)…(x - α^(n-1)),回係數(高次在前)
function rsGenerator(n) {
  let g = [1];
  for (let i = 0; i < n; i += 1) {
    const next = new Array(g.length + 1).fill(0);
    for (let j = 0; j < g.length; j += 1) { next[j] ^= g[j]; next[j + 1] ^= gfMul(g[j], EXP[i]); }
    g = next;
  }
  return g;
}
function rsEncode(data, ecCount) {
  const gen = rsGenerator(ecCount);
  const out = new Array(ecCount).fill(0);
  for (const byte of data) {
    const factor = byte ^ out[0];
    out.shift(); out.push(0);
    if (factor !== 0) for (let j = 0; j < ecCount; j += 1) out[j] ^= gfMul(gen[j + 1], factor);
  }
  return out;
}

// ---- 位元串 ----
function bitBuffer() {
  const bits = [];
  return { bits, put(value, length) { for (let i = length - 1; i >= 0; i -= 1) bits.push((value >>> i) & 1); } };
}
function dataCapacity(version) { const b = BLOCKS_M[version]; return b.slice(2).reduce((sum, [count, size]) => sum + count * size, 0); }
function chooseVersion(byteLength) {
  for (let v = 1; v <= 10; v += 1) {
    const bits = 4 + (v <= 9 ? 8 : 16) + byteLength * 8;
    if (bits <= dataCapacity(v) * 8) return v;
  }
  return null;
}
function encodeCodewords(bytes, version) {
  const capacity = dataCapacity(version) * 8;
  const buf = bitBuffer();
  buf.put(0b0100, 4);                         // byte mode
  buf.put(bytes.length, version <= 9 ? 8 : 16);
  for (const byte of bytes) buf.put(byte, 8);
  buf.put(0, Math.min(4, capacity - buf.bits.length));   // 終止符
  while (buf.bits.length % 8) buf.bits.push(0);
  const pads = [0xec, 0x11];
  for (let i = 0; buf.bits.length < capacity; i += 1) buf.put(pads[i % 2], 8);
  const data = [];
  for (let i = 0; i < buf.bits.length; i += 8) data.push(parseInt(buf.bits.slice(i, i + 8).join(""), 2));
  // 分塊 + RS,再交錯
  const [, ecPerBlock, ...groups] = BLOCKS_M[version];
  const blocks = [];
  let cursor = 0;
  for (const [count, size] of groups) for (let i = 0; i < count; i += 1) { const chunk = data.slice(cursor, cursor + size); cursor += size; blocks.push({ data: chunk, ec: rsEncode(chunk, ecPerBlock) }); }
  const out = [];
  const maxData = Math.max(...blocks.map((b) => b.data.length));
  for (let i = 0; i < maxData; i += 1) for (const b of blocks) if (i < b.data.length) out.push(b.data[i]);
  for (let i = 0; i < ecPerBlock; i += 1) for (const b of blocks) out.push(b.ec[i]);
  return { data, codewords: out, blocks };
}

// ---- 矩陣 ----
function newMatrix(size) { return { size, modules: Array.from({ length: size }, () => new Int8Array(size).fill(-1)), reserved: Array.from({ length: size }, () => new Uint8Array(size)) }; }
function set(m, r, c, v, reserve = true) { m.modules[r][c] = v ? 1 : 0; if (reserve) m.reserved[r][c] = 1; }
function placeFinder(m, r0, c0) {
  for (let r = -1; r <= 7; r += 1) for (let c = -1; c <= 7; c += 1) {
    const rr = r0 + r, cc = c0 + c;
    if (rr < 0 || cc < 0 || rr >= m.size || cc >= m.size) continue;
    const inner = r >= 0 && r <= 6 && c >= 0 && c <= 6;
    const dark = inner && ((r === 0 || r === 6 || c === 0 || c === 6) || (r >= 2 && r <= 4 && c >= 2 && c <= 4));
    set(m, rr, cc, dark);
  }
}
function placeAlignment(m, version) {
  const centers = ALIGNMENT[version];
  for (const r of centers) for (const c of centers) {
    if ((r === 6 && c === 6) || (r === 6 && c === centers[centers.length - 1]) || (c === 6 && r === centers[centers.length - 1])) continue;   // 與定位圖樣重疊的不畫
    for (let dr = -2; dr <= 2; dr += 1) for (let dc = -2; dc <= 2; dc += 1) set(m, r + dr, c + dc, Math.max(Math.abs(dr), Math.abs(dc)) !== 1);
  }
}
function placeTiming(m) { for (let i = 8; i < m.size - 8; i += 1) { set(m, 6, i, i % 2 === 0); set(m, i, 6, i % 2 === 0); } }
function reserveFormat(m) {
  for (let i = 0; i <= 8; i += 1) { if (i !== 6) { m.reserved[8][i] = 1; m.reserved[i][8] = 1; } }
  for (let i = 0; i < 8; i += 1) { m.reserved[8][m.size - 1 - i] = 1; m.reserved[m.size - 1 - i][8] = 1; }
  set(m, m.size - 8, 8, true);   // 暗模組
}
function reserveVersion(m, version) {
  if (version < 7) return;
  for (let i = 0; i < 6; i += 1) for (let j = 0; j < 3; j += 1) { m.reserved[i][m.size - 11 + j] = 1; m.reserved[m.size - 11 + j][i] = 1; }
}
// BCH:格式 (15,5) 生成 0x537、遮罩 0x5412;版本 (18,6) 生成 0x1F25
function bch(value, polyBits, generator, totalBits) {
  let v = value << (totalBits - polyBits);
  for (let i = totalBits - 1; i >= totalBits - polyBits; i -= 1) if ((v >>> i) & 1) v ^= generator << (i - (totalBits - polyBits));
  return (value << (totalBits - polyBits)) | v;
}
function formatBits(mask) { return bch((EC_LEVEL_M << 3) | mask, 5, 0x537, 15) ^ 0x5412; }
function versionBits(version) { return bch(version, 6, 0x1f25, 18); }
function writeFormat(m, mask) {
  const bits = formatBits(mask);
  for (let i = 0; i < 15; i += 1) {
    const bit = (bits >>> i) & 1;
    // 左上:列 8 從左到右(跳過 col 6)/ 欄 8 從下到上(跳過 row 6)
    if (i < 6) set(m, 8, i, bit); else if (i < 8) set(m, 8, i + 1, bit); else if (i === 8) set(m, 7, 8, bit); else set(m, 14 - i, 8, bit);
    // 右上 + 左下
    // 左下欄 8 由下往上 7 個(bit 0–6),右上列 8 從 size-8 到 size-1 8 個(bit 7–14);(size-8, 8) 是暗模組,不歸格式資訊
    if (i < 7) set(m, m.size - 1 - i, 8, bit); else set(m, 8, m.size - 15 + i, bit);
  }
}
function writeVersion(m, version) {
  if (version < 7) return;
  const bits = versionBits(version);
  for (let i = 0; i < 18; i += 1) { const bit = (bits >>> i) & 1; const a = Math.floor(i / 3), b = i % 3; set(m, a, m.size - 11 + b, bit); set(m, m.size - 11 + b, a, bit); }
}
function maskBit(mask, r, c) {
  switch (mask) {
    case 0: return (r + c) % 2 === 0;
    case 1: return r % 2 === 0;
    case 2: return c % 3 === 0;
    case 3: return (r + c) % 3 === 0;
    case 4: return (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0;
    case 5: return ((r * c) % 2) + ((r * c) % 3) === 0;
    case 6: return (((r * c) % 2) + ((r * c) % 3)) % 2 === 0;
    default: return (((r + c) % 2) + ((r * c) % 3)) % 2 === 0;
  }
}
// zigzag 走位:從右下往上,兩欄一組,跳過欄 6
function walk(size) {
  const cells = [];
  let up = true;
  for (let col = size - 1; col > 0; col -= 2) {
    if (col === 6) col -= 1;
    for (let k = 0; k < size; k += 1) {
      const row = up ? size - 1 - k : k;
      cells.push([row, col]); cells.push([row, col - 1]);
    }
    up = !up;
  }
  return cells;
}
function placeData(m, codewords, mask) {
  const bits = [];
  for (const cw of codewords) for (let i = 7; i >= 0; i -= 1) bits.push((cw >>> i) & 1);
  let cursor = 0;
  for (const [r, c] of walk(m.size)) {
    if (m.reserved[r][c]) continue;
    const bit = cursor < bits.length ? bits[cursor] : 0;
    cursor += 1;
    m.modules[r][c] = maskBit(mask, r, c) ? bit ^ 1 : bit;
  }
}
function readData(m, mask, count) {
  const bits = [];
  for (const [r, c] of walk(m.size)) {
    if (m.reserved[r][c]) continue;
    const raw = m.modules[r][c];
    bits.push(maskBit(mask, r, c) ? raw ^ 1 : raw);
    if (bits.length >= count * 8) break;
  }
  const out = [];
  for (let i = 0; i < count; i += 1) out.push(parseInt(bits.slice(i * 8, i * 8 + 8).join(""), 2));
  return out;
}
function penalty(m) {
  const s = m.size, g = m.modules;
  let score = 0;
  // 規則 1:同色連續 ≥5
  for (let pass = 0; pass < 2; pass += 1) for (let i = 0; i < s; i += 1) {
    let run = 1;
    for (let j = 1; j < s; j += 1) {
      const a = pass ? g[j][i] : g[i][j], b = pass ? g[j - 1][i] : g[i][j - 1];
      if (a === b) { run += 1; if (run === 5) score += 3; else if (run > 5) score += 1; } else run = 1;
    }
  }
  // 規則 2:2×2 同色
  for (let r = 0; r < s - 1; r += 1) for (let c = 0; c < s - 1; c += 1) if (g[r][c] === g[r][c + 1] && g[r][c] === g[r + 1][c] && g[r][c] === g[r + 1][c + 1]) score += 3;
  // 規則 3:1011101 帶四白
  const pattern = [1, 0, 1, 1, 1, 0, 1];
  const hits = (get) => { let n = 0; for (let i = 0; i <= s - 11; i += 1) { const a = pattern.every((p, k) => get(i + 4 + k) === p) && [0, 1, 2, 3].every((k) => get(i + k) === 0); const b = pattern.every((p, k) => get(i + k) === p) && [7, 8, 9, 10].every((k) => get(i + k) === 0); if (a) n += 1; if (b) n += 1; } return n; };
  for (let i = 0; i < s; i += 1) { score += 40 * hits((k) => g[i][k]); score += 40 * hits((k) => g[k][i]); }
  // 規則 4:暗模組比例
  let dark = 0; for (let r = 0; r < s; r += 1) for (let c = 0; c < s; c += 1) dark += g[r][c];
  const ratio = (dark * 100) / (s * s);
  score += 10 * Math.min(Math.abs(Math.floor(ratio / 5) * 5 - 50) / 5, Math.abs(Math.ceil(ratio / 5) * 5 - 50) / 5);
  return score;
}

function encode(text, options = {}) {
  const bytes = Buffer.from(String(text), "utf8");
  const version = chooseVersion(bytes.length);
  if (!version) { const error = new Error("QR_TOO_LONG"); error.code = "QR_TOO_LONG"; throw error; }
  const { codewords, data } = encodeCodewords(bytes, version);
  const size = 17 + version * 4;
  const build = (mask) => {
    const m = newMatrix(size);
    placeFinder(m, 0, 0); placeFinder(m, 0, size - 7); placeFinder(m, size - 7, 0);
    placeAlignment(m, version); placeTiming(m); reserveFormat(m); reserveVersion(m, version);
    writeFormat(m, mask); writeVersion(m, version);
    placeData(m, codewords, mask);
    return m;
  };
  let best = null;
  const fixed = options.mask;
  for (let mask = 0; mask < 8; mask += 1) {
    if (Number.isInteger(fixed) && mask !== fixed) continue;
    const m = build(mask);
    const score = String(options.test_mutation || "") === "qr-mask-fixed" ? (mask === 0 ? 0 : 1) : penalty(m);   // 刀:不選最低罰分,永遠遮罩 0
    if (!best || score < best.score) best = { mask, score, matrix: m };
  }
  return { version, size, mask: best.mask, modules: best.matrix.modules, reserved: best.matrix.reserved, codewords, data, bytes: bytes.length };
}
// SVG:每列的連續暗模組合成一段矩形,一條 path;quiet zone 4 模組;viewBox 用模組單位,尺寸交給 CSS
function toSvg(qr, options = {}) {
  const quiet = 4, total = qr.size + quiet * 2;
  const parts = [];
  for (let r = 0; r < qr.size; r += 1) {
    let c = 0;
    while (c < qr.size) {
      if (qr.modules[r][c] !== 1) { c += 1; continue; }
      let end = c; while (end < qr.size && qr.modules[r][end] === 1) end += 1;
      parts.push("M" + (c + quiet) + " " + (r + quiet) + "h" + (end - c) + "v1h-" + (end - c) + "z");
      c = end;
    }
  }
  const label = String(options.aria_label || "QR code");
  return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + total + " " + total + '" shape-rendering="crispEdges" role="img" aria-label="' + label.replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]) + '"' + (options.data_url ? ' data-url="' + String(options.data_url).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]) + '"' : "") + '><rect width="100%" height="100%" fill="#fff"/><path fill="#000" d="' + parts.join("") + '"/></svg>';
}

module.exports = { ALIGNMENT, BLOCKS_M, encode, formatBits, penalty, readData, rsGenerator, rsEncode, toSvg, versionBits, walk, LOG, EXP };
