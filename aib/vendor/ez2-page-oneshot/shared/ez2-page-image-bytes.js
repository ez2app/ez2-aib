"use strict";

// ez2-page-image-bytes.js — 上傳圖片的位元組層(288 / 295,2026-09-17)
//
// 三件事,全部只看位元組、不解碼像素、不帶相依:
//   1. sniff:魔數定型別(webp / png / jpg),副檔名一個字都不信——`.webp` 裡裝 exe 的檔要在這裡死。
//   2. dimensions:從標頭讀寬高(png IHDR、jpg SOFn、webp VP8/VP8L/VP8X)。
//   3. stripMetadata(295):把 EXIF / XMP / IPTC / 文字註解整段拿掉——上傳的圖不該把手機型號、GPS、拍攝時間一起發布。
//      jpg 拿 APP1(Exif、XMP)、APP13(Photoshop/IPTC)、COM;png 拿 eXIf / tEXt / zTXt / iTXt;webp 拿 EXIF / XMP 塊並清 VP8X 旗標、重算 RIFF 大小。
//      ICC(jpg APP2 / png iCCP / webp ICCP)刻意留著:那是色彩,不是隱私。
//
// 每個函式對「不是那種型別」的輸入回 null / 原樣,不丟;丟例外的是呼叫端的事。

const PNG_SIG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const TYPES = Object.freeze({ webp: "image/webp", png: "image/png", jpg: "image/jpeg" });

function sniff(bytes) {
  if (!Buffer.isBuffer(bytes) || bytes.length < 12) return null;
  if (bytes.slice(0, 4).toString("latin1") === "RIFF" && bytes.slice(8, 12).toString("latin1") === "WEBP") return "webp";
  if (bytes.slice(0, 8).equals(PNG_SIG)) return "png";
  if (bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return "jpg";
  return null;
}

// ---- 寬高 ----
function dimensions(bytes, type = sniff(bytes)) {
  try {
    if (type === "png") {
      if (bytes.length < 24 || bytes.slice(12, 16).toString("latin1") !== "IHDR") return null;
      return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
    }
    if (type === "jpg") {
      let at = 2;
      while (at + 4 <= bytes.length) {
        if (bytes[at] !== 0xff) { at += 1; continue; }
        const marker = bytes[at + 1];
        if (marker === 0xd8 || (marker >= 0xd0 && marker <= 0xd7) || marker === 0x01 || marker === 0xff) { at += 2; continue; }
        if (marker === 0xd9 || marker === 0xda) return null;
        const length = bytes.readUInt16BE(at + 2);
        // SOF0–SOF15 除了 DHT(C4)、JPG(C8)、DAC(CC)
        if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
          if (at + 9 > bytes.length) return null;
          return { width: bytes.readUInt16BE(at + 7), height: bytes.readUInt16BE(at + 5) };
        }
        at += 2 + length;
      }
      return null;
    }
    if (type === "webp") {
      const chunk = bytes.slice(12, 16).toString("latin1");
      const data = 20;
      if (chunk === "VP8X") {
        if (bytes.length < 30) return null;
        return { width: 1 + bytes.readUIntLE(data + 4, 3), height: 1 + bytes.readUIntLE(data + 7, 3) };
      }
      if (chunk === "VP8 ") {
        if (bytes.length < data + 10 || bytes[data + 3] !== 0x9d || bytes[data + 4] !== 0x01 || bytes[data + 5] !== 0x2a) return null;
        return { width: bytes.readUInt16LE(data + 6) & 0x3fff, height: bytes.readUInt16LE(data + 8) & 0x3fff };
      }
      if (chunk === "VP8L") {
        if (bytes.length < data + 5 || bytes[data] !== 0x2f) return null;
        const b = bytes.readUInt32LE(data + 1);
        return { width: 1 + (b & 0x3fff), height: 1 + ((b >>> 14) & 0x3fff) };
      }
    }
  } catch (_) { return null; }
  return null;
}

// ---- 中繼資料 ----
const JPG_STRIP = new Set([0xe1, 0xed, 0xfe]);   // APP1(Exif/XMP)、APP13(IPTC)、COM
const PNG_STRIP = new Set(["eXIf", "tEXt", "zTXt", "iTXt"]);
const WEBP_STRIP = new Set(["EXIF", "XMP "]);

function jpgSegments(bytes) {
  // 回 [{at, marker, length(含長度欄)}],直到 SOS(之後是熵編碼資料,不再分段)。
  const out = [];
  let at = 2;
  while (at + 4 <= bytes.length) {
    if (bytes[at] !== 0xff) return null;
    const marker = bytes[at + 1];
    if (marker === 0xff) { at += 1; continue; }
    if (marker === 0xd8 || (marker >= 0xd0 && marker <= 0xd7) || marker === 0x01) { out.push({ at, marker, length: 0 }); at += 2; continue; }
    if (marker === 0xda) { out.push({ at, marker, length: bytes.length - at - 2, sos: true }); return out; }
    if (marker === 0xd9) return out;
    const length = bytes.readUInt16BE(at + 2);
    if (length < 2 || at + 2 + length > bytes.length) return null;
    out.push({ at, marker, length });
    at += 2 + length;
  }
  return out;
}

function metadataSegments(bytes, type = sniff(bytes)) {
  // 回這張圖裡「會被 strip 拿掉」的段名清單(給尺與留痕用);解析不了回 null。
  if (type === "jpg") {
    const segments = jpgSegments(bytes);
    if (!segments) return null;
    return segments.filter((s) => JPG_STRIP.has(s.marker)).map((s) => "APP" + (s.marker === 0xfe ? "COM" : String(s.marker - 0xe0)));
  }
  if (type === "png") {
    const out = [];
    let at = 8;
    while (at + 12 <= bytes.length) {
      const length = bytes.readUInt32BE(at);
      const name = bytes.slice(at + 4, at + 8).toString("latin1");
      if (PNG_STRIP.has(name)) out.push(name);
      if (name === "IEND") break;
      at += 12 + length;
    }
    return out;
  }
  if (type === "webp") {
    const out = [];
    let at = 12;
    while (at + 8 <= bytes.length) {
      const name = bytes.slice(at, at + 4).toString("latin1");
      const length = bytes.readUInt32LE(at + 4);
      if (WEBP_STRIP.has(name)) out.push(name.trim());
      at += 8 + length + (length % 2);
    }
    return out;
  }
  return null;
}

function stripMetadata(bytes, type = sniff(bytes)) {
  if (type === "jpg") {
    const segments = jpgSegments(bytes);
    if (!segments) return bytes;
    const parts = [bytes.slice(0, 2)];
    for (const s of segments) {
      if (s.sos) { parts.push(bytes.slice(s.at)); break; }
      if (JPG_STRIP.has(s.marker)) continue;
      parts.push(bytes.slice(s.at, s.at + 2 + s.length));
    }
    return Buffer.concat(parts);
  }
  if (type === "png") {
    const parts = [bytes.slice(0, 8)];
    let at = 8;
    while (at + 12 <= bytes.length) {
      const length = bytes.readUInt32BE(at);
      const name = bytes.slice(at + 4, at + 8).toString("latin1");
      const end = at + 12 + length;
      if (!PNG_STRIP.has(name)) parts.push(bytes.slice(at, Math.min(end, bytes.length)));
      at = end;
      if (name === "IEND") break;
    }
    return Buffer.concat(parts);
  }
  if (type === "webp") {
    const chunks = [];
    let at = 12;
    while (at + 8 <= bytes.length) {
      const name = bytes.slice(at, at + 4).toString("latin1");
      const length = bytes.readUInt32LE(at + 4);
      const padded = length + (length % 2);
      chunks.push({ name, body: bytes.slice(at, Math.min(at + 8 + padded, bytes.length)) });
      at += 8 + padded;
    }
    if (!chunks.some((c) => WEBP_STRIP.has(c.name))) return bytes;
    const kept = chunks.filter((c) => !WEBP_STRIP.has(c.name)).map((c) => {
      if (c.name !== "VP8X") return c.body;
      const body = Buffer.from(c.body);
      body[8] = body[8] & ~0x0c;   // 清 EXIF(0x08)與 XMP(0x04)旗標
      return body;
    });
    const payload = Buffer.concat(kept);
    const head = Buffer.alloc(12);
    head.write("RIFF", 0, "latin1"); head.writeUInt32LE(payload.length + 4, 4); head.write("WEBP", 8, "latin1");
    return Buffer.concat([head, payload]);
  }
  return bytes;
}

module.exports = { TYPES, sniff, dimensions, metadataSegments, stripMetadata };
