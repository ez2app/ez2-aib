"use strict";

// Ez2.PAGE vCard — 把一張 bio 頁變成一張真的、存得進手機通訊錄的名片。
//
// 為什麼這件事值得做:bio 頁的本質就是數位名片,而名片的終點是**被存進通訊錄**。
// 訪客把你存下去之後,你就永久在他手機裡 —— 那比一個瀏覽器書籤有價值太多,
// 而瀏覽器書籤網頁根本做不到(程式化加書籤的 API 各家早已移除)。
// Linktree 有這個功能(Contact Details),Portaly 沒有。
//
// 為什麼零腳本做得到:一條 <a href="/{slug}.vcf"> 而已。手機點下去直接進通訊錄
// 選單,不需要一行 JavaScript、不需要 cookie —— 完全符合效能優先模式的結構保證。
//
// 版本選 3.0 不選 4.0:iOS 通訊錄與 Android 對 3.0 的支援最一致,4.0 在部分
// 系統上會被當成純文字打開。這裡不追新,追「使用者點下去真的能存」。
//
// 隱私邊界(重要):
//   vCard 只放**這一頁本來就公開的東西** —— 顯示名稱、自介、頁面網址、
//   店主自己選擇顯示的社群連結。Email 與電話不放,因為我們根本不收
//   (Email 只存不可逆雜湊)。所以這個檔案不會洩漏任何頁面上看不到的資訊。
//   ★ 這條要維持:未來若收了電話,放不放進 vCard 是**另一個同意**,不是順便。

const socialIcons = require("./ez2-page-social-icons.js");

const VERSION = "2026-07-26.ez2-page-vcard.v1";
const VCARD_VERSION = "3.0";
const CONTENT_TYPE = "text/vcard; charset=utf-8";
// RFC 2426 的折行門檻是 75 octet。中文一個字三個 byte,所以必須按位元組算,
// 而且不能把一個字從中間切開 —— 切開的結果是通訊錄顯示亂碼。
const FOLD_OCTETS = 75;

function fail(code, details = []) { const error = new Error(code); error.code = code; error.details = details; throw error; }

// vCard 的跳脫規則:反斜線、逗號、分號、換行。順序重要 —— 反斜線必須最先跳脫,
// 否則後面加進去的反斜線會被再跳脫一次。
function escapeValue(value) {
  return String(value == null ? "" : value)
    .replace(/\\/g, "\\\\")
    .replace(/\n/g, "\\n")
    .replace(/,/g, "\\,")
    .replace(/;/g, "\\;")
    // 控制字元一律剝掉。換行在上一步已轉成 \n 字面,所以這裡連 CR 與 TAB 都不留:
    // 一個裸 CR 就能在輸出裡偽造出一行新欄位(折行注入)。
    .replace(/[\u0000-\u001f\u007f]/g, "");
}

// 按位元組折行,但永遠不切開一個多位元組字元。續行以一個空白開頭(RFC 規定)。
function foldLine(line) {
  const bytes = Buffer.from(String(line), "utf8");
  if (bytes.length <= FOLD_OCTETS) return line;
  const parts = [];
  let start = 0;
  let limit = FOLD_OCTETS;
  while (start < bytes.length) {
    let end = Math.min(start + limit, bytes.length);
    // 往回退到字元邊界:UTF-8 續位元組是 10xxxxxx。
    while (end > start && end < bytes.length && (bytes[end] & 0xc0) === 0x80) end -= 1;
    parts.push(bytes.slice(start, end).toString("utf8"));
    start = end;
    limit = FOLD_OCTETS - 1; // 續行開頭那個空白也算一個 octet
  }
  return parts[0] + parts.slice(1).map((part) => "\r\n " + part).join("");
}

// 社群連結進 vCard:用 X-SOCIALPROFILE(iOS 認得,會顯示成社群欄位)。
// TYPE 用我們自己的平台代號,而平台代號的權威是圖示模組的清單 —— 不自己另立一份。
function socialLines(links) {
  const lines = [];
  for (const link of Array.isArray(links) ? links : []) {
    if (!link || !socialIcons.has(link.platform)) continue;
    if (!/^https:\/\//.test(String(link.url || ""))) continue;
    lines.push("X-SOCIALPROFILE;TYPE=" + link.platform + ":" + escapeValue(link.url));
  }
  return lines;
}

// profile 的形狀與 theme-renderer 的 validateProfile 同源;canonical 是這一頁的正式網址。
function buildVCard(input = {}) {
  const profile = input.profile;
  const canonical = String(input.canonical || "");
  if (!profile || typeof profile !== "object" || typeof profile.display_name !== "string" || !profile.display_name.trim()) {
    fail("PAGE_VCARD_PROFILE_INVALID");
  }
  if (!/^https:\/\//.test(canonical)) fail("PAGE_VCARD_CANONICAL_INVALID");
  const name = profile.display_name.trim();
  const isOrg = profile.entity_kind === "organization";

  const lines = ["BEGIN:VCARD", "VERSION:" + VCARD_VERSION];
  // 一間店與一個人在通訊錄裡是不同的東西。entity_kind 是店主宣告的,不是我們猜的
  // —— 猜錯會讓「陳記牛肉麵」在他手機裡變成一個姓陳的人。
  if (isOrg) {
    lines.push("N:;;;;");
    lines.push("ORG:" + escapeValue(name));
    lines.push("X-ABShowAs:COMPANY");
  } else {
    // N 的五個欄位:姓;名;中間名;前綴;後綴。我們只有一個顯示名稱,
    // 不拆姓名 —— 拆中文姓名的規則因人而異,拆錯比不拆更糟。
    lines.push("N:;" + escapeValue(name) + ";;;");
  }
  lines.push("FN:" + escapeValue(name));
  if (profile.bio) lines.push("NOTE:" + escapeValue(profile.bio));
  if (profile.status_text) lines.push("TITLE:" + escapeValue(profile.status_text));
  lines.push("URL:" + escapeValue(canonical));
  lines.push(...socialLines(profile.social_links));
  // SOURCE 讓通訊錄知道這張名片從哪裡來,更新時可以回到原處。
  lines.push("SOURCE:" + escapeValue(canonical));
  // REV 由呼叫端給(頁面的 updated_at),不在這裡取現在時間 —— 那會讓同一頁
  // 每次下載都產生不同內容,快取與比對都失效。
  if (input.revised_at) lines.push("REV:" + escapeValue(String(input.revised_at).replace(/\.\d{3}/, "")));
  lines.push("END:VCARD");

  // RFC 要求 CRLF。少了它,部分 Android 通訊錄會把整份當成一行。
  return lines.map(foldLine).join("\r\n") + "\r\n";
}

// 檔名:用 slug,不用顯示名稱 —— 中文檔名在下載時各系統的處理不一致,
// 而且顯示名稱可能含空白與標點。
function fileName(slug) {
  const value = String(slug || "");
  if (!/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(value)) fail("PAGE_VCARD_SLUG_INVALID", [value]);
  return value + ".vcf";
}

// 稽核:給閘門與 mutation 演練咬的面。
function auditVCard(text, options = {}) {
  const mutation = String(options.test_mutation || "");
  const blockers = [];
  const body = String(text);
  if (!body.startsWith("BEGIN:VCARD\r\n")) blockers.push("vcard_begin_missing");
  if (!body.endsWith("END:VCARD\r\n")) blockers.push("vcard_end_missing");
  if (!body.includes("VERSION:" + VCARD_VERSION + "\r\n")) blockers.push("vcard_version_invalid");
  if (!/\r\nFN:/.test(body)) blockers.push("vcard_fn_missing");
  // 換行一律 CRLF:單獨的 LF 會讓部分 Android 通訊錄把整份當一行。
  if (mutation !== "allow-bare-lf" && /[^\r]\n/.test(body)) blockers.push("vcard_bare_lf");
  // 折行後每一行都不得超過門檻(續行的開頭空白也算)。
  for (const line of body.split("\r\n")) {
    if (Buffer.byteLength(line, "utf8") > FOLD_OCTETS) { blockers.push("vcard_line_too_long"); break; }
  }
  // 隱私:我們不收電話與 Email 原文,所以它們永遠不該出現在這裡。
  // 這條是機器化的隱私邊界,不是註解 —— 未來有人加了欄位會在這裡被擋下來。
  if (mutation !== "allow-contact-fields" && /\r\n(?:TEL|EMAIL):/i.test(body)) blockers.push("vcard_contact_field_forbidden");
  return {
    schema_version: VERSION,
    status: blockers.length ? "blocked" : "ok",
    blockers: [...new Set(blockers)].sort(),
    observed: { bytes: Buffer.byteLength(body, "utf8"), lines: body.split("\r\n").length - 1, vcard_version: VCARD_VERSION }
  };
}

module.exports = { VERSION, VCARD_VERSION, CONTENT_TYPE, FOLD_OCTETS, buildVCard, fileName, auditVCard, escapeValue, foldLine };
