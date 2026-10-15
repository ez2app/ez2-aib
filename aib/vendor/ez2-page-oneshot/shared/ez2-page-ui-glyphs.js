"use strict";

// Ez2.PAGE 介面圖示(UI glyphs)。與社群圖示分開的理由:
//   社群圖示代表**身份**(哪個平台),有品牌色、有平台契約、有商標邊界;
//   介面圖示代表**動作**(做什麼),永遠單色、永遠吃 currentColor、沒有品牌。
//   兩者混在一個模組裡,遲早會有人給「分享」一個品牌色。
//
// 但兩者共用同一份一致性契約,所以並排時是同一套:
//   同網格 viewBox 0 0 24 24 / 同筆畫 1.9 / 同光學留白 2.5–21.5 / 一律 currentColor。
//   (筆畫比社群圖示的 1.8 略粗:動作圖示通常獨立出現、尺寸更小,需要多一點份量。)
//
// ★ 有兩個圖示畫得出來但**功能做不出來**,清單裡標了 caveat 並附替代方案:
//   thumb_up:讚需要辨識訪客防灌票 → 需要 cookie 或指紋 → 直接違反效能優先
//     模式的「0 追蹤腳本、0 Cookie」;而且讚數是社會證明,canon 有不可造假的
//     判例,新頁面顯示 0 比不顯示更傷。
//   star:網頁**沒有辦法**用程式把自己加進瀏覽器書籤(舊的 addFavorite /
//     addPanel 各家早已移除)。只畫得出按鈕,按下去不會有事發生。
//   兩個都留在集合裡,因為後台與「平衡/完整像素」模式用得到;但公開的
//   效能優先頁面上不該出現。消費端要看 caveat 決定能不能用。

const VERSION = "2026-07-26.ez2-page-ui-glyphs.v1";
const VIEWBOX = "0 0 24 24";
const STROKE_WIDTH = 1.9;
const OPTICAL_MIN = 2.5;
const OPTICAL_MAX = 21.5;

// caveat:這個圖示代表的動作在零腳本公開頁上做不到。null = 沒有問題。
const GLYPHS = Object.freeze({
  arrow: Object.freeze({ label: "前往", caveat: null, body: '<path d="M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5"/>' }),
  outbound: Object.freeze({ label: "另開", caveat: null, body: '<path d="M7.5 16.5L16.5 7.5M9.2 7.5h7.3v7.3"/>' }),
  chevron: Object.freeze({ label: "展開", caveat: null, body: '<path d="M9.5 5.5l6.5 6.5-6.5 6.5"/>' }),
  // 分享:三個節點兩條線。零腳本做得到 —— 各平台的分享網址都是純連結。
  share: Object.freeze({ label: "分享", caveat: null, body: '<circle cx="17.5" cy="6" r="2.7"/><circle cx="6.5" cy="12" r="2.7"/><circle cx="17.5" cy="18" r="2.7"/><path d="M9 10.7l6-3.4M9 13.3l6 3.4"/>' }),
  // 存成聯絡人(vCard)。★這是名片頁真正的終點,而且零腳本:一條 .vcf 連結而已。
  contact: Object.freeze({ label: "存成聯絡人", caveat: null, body: '<circle cx="9.5" cy="9" r="3.2"/><path d="M3.8 19.2c.9-2.9 3-4.4 5.7-4.4s4.8 1.5 5.7 4.4"/><path d="M17.5 8.2v5M20 10.7h-5"/>' }),
  qr: Object.freeze({ label: "QR 碼", caveat: null, body: '<rect x="3.5" y="3.5" width="6.5" height="6.5" rx="1.4"/><rect x="14" y="3.5" width="6.5" height="6.5" rx="1.4"/><rect x="3.5" y="14" width="6.5" height="6.5" rx="1.4"/><path d="M14 14h3v3M20.5 17.5v3M17 20.5h-3"/>' }),
  copy: Object.freeze({ label: "複製", caveat: "需要腳本;效能優先的公開頁不可用。建議改用分享(各平台分享網址是純連結,零腳本做得到)", body: '<rect x="8.5" y="8.5" width="12" height="12" rx="2.6"/><path d="M15.5 5.2a2.6 2.6 0 0 0-2.6-2.6H6.1A2.6 2.6 0 0 0 3.5 5.2v6.8a2.6 2.6 0 0 0 2.6 2.6"/>' }),
  download: Object.freeze({ label: "下載", caveat: null, body: '<path d="M12 3.5v11M7.5 10.2l4.5 4.3 4.5-4.3M4.5 19.5h15"/>' }),
  play: Object.freeze({ label: "播放", caveat: null, body: '<circle cx="12" cy="12" r="8.8"/><path d="M10 8.6v6.8l5.6-3.4z"/>' }),
  heart: Object.freeze({ label: "支持", caveat: null, body: '<path d="M12 20.2S3.5 15.4 3.5 9.6a4.6 4.6 0 0 1 8.5-2.5 4.6 4.6 0 0 1 8.5 2.5c0 5.8-8.5 10.6-8.5 10.6z"/>' }),
  bag: Object.freeze({ label: "商品", caveat: null, body: '<path d="M4.6 7.8h14.8l-1.2 11.7H5.8z"/><path d="M8.8 10.4V7.2a3.2 3.2 0 0 1 6.4 0v3.2"/>' }),
  calendar: Object.freeze({ label: "活動", caveat: null, body: '<rect x="3.5" y="5.2" width="17" height="15.3" rx="2.8"/><path d="M3.5 10h17M8.2 3.5v3.4M15.8 3.5v3.4"/>' }),
  pin: Object.freeze({ label: "地點", caveat: null, body: '<path d="M12 20.8s6.5-5.6 6.5-10.3a6.5 6.5 0 0 0-13 0c0 4.7 6.5 10.3 6.5 10.3z"/><circle cx="12" cy="10.3" r="2.5"/>' }),
  mail: Object.freeze({ label: "電子報", caveat: null, body: '<rect x="3" y="5.2" width="18" height="13.6" rx="2.8"/><path d="M3.9 7.6L12 13l8.1-5.4"/>' }),
  clock: Object.freeze({ label: "時間", caveat: null, body: '<circle cx="12" cy="12" r="8.8"/><path d="M12 6.8V12l3.6 2.1"/>' }),
  // ★ 以下兩個畫得出來但功能做不出來,見檔頭。
  thumb_up: Object.freeze({
    label: "讚",
    caveat: "讚需要辨識訪客防灌票(cookie 或指紋),違反效能優先的 0 Cookie;讚數是社會證明,不可造假也不宜顯示 0。建議改用分享",
    body: '<path d="M7.2 20.2V10.4M3.5 12.2v6.2a1.8 1.8 0 0 0 1.8 1.8h11.4a2 2 0 0 0 2-1.6l1.2-6.2a1.8 1.8 0 0 0-1.8-2.2h-4.5V6.4a2.6 2.6 0 0 0-2.6-2.6l-3.2 6.6"/>'
  }),
  // 書簽與星星是不同的東西:書簽是「這一頁我要留著」,星星是「這是精選」。
  // 語意不同所以分開畫。但兩者都撞到同一堵牆 —— 網頁加不了瀏覽器書籤。
  bookmark: Object.freeze({
    label: "書簽",
    caveat: "網頁無法用程式加入瀏覽器書籤(舊 API 各家已移除),按下去不會有事發生。建議改用存成聯絡人 vCard —— 存進通訊錄比存進書籤更持久,而且零腳本做得到",
    body: '<path d="M6.6 3.6h10.8a1.4 1.4 0 0 1 1.4 1.4v15.4L12 16.3l-6.8 4.1V5a1.4 1.4 0 0 1 1.4-1.4z"/>'
  }),
  star: Object.freeze({
    label: "加入最愛",
    caveat: "網頁無法用程式加入瀏覽器書籤(舊 API 各家已移除),按下去不會有事發生。建議改用存成聯絡人 vCard",
    body: '<path d="M12 3.6l2.7 5.6 6 .9-4.35 4.3 1.05 6-5.4-2.9-5.4 2.9 1.05-6L3.3 10.1l6-.9z"/>'
  })
});

const ACTIONABLE = Object.freeze(Object.keys(GLYPHS).filter((key) => !GLYPHS[key].caveat));

function fail(code, details = []) { const error = new Error(code); error.code = code; error.details = details; throw error; }
function has(name) { return Object.prototype.hasOwnProperty.call(GLYPHS, String(name)); }

// 一律 currentColor:介面圖示沒有自己的顏色,它是文字的一部分。
// aria-hidden 是預設:動作的可及名稱屬於按鈕或連結,不屬於裡面那張圖。
function renderGlyph(name, options = {}) {
  const key = String(name);
  if (!has(key)) fail("UI_GLYPH_UNKNOWN", [key]);
  return '<svg viewBox="' + VIEWBOX + '" fill="none" stroke="currentColor" stroke-width="' + STROKE_WIDTH +
    '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' + GLYPHS[key].body + "</svg>";
}

function absoluteCoordinates(markup) {
  const values = [];
  for (const attr of String(markup).matchAll(/(?:^|[\s<])(?:x|y|cx|cy)="(-?[\d.]+)"/g)) values.push(Number(attr[1]));  // ★ 必須有字界:沒有的話 rx="1.4" 的圓角半徑會被當成 x 座標,
  // 判成超出光學邊界。這是我自己的稽核工具抓到的假紅,兩支模組同一顆。
  for (const rect of String(markup).matchAll(/<rect[^>]*x="(-?[\d.]+)"[^>]*y="(-?[\d.]+)"[^>]*width="([\d.]+)"[^>]*height="([\d.]+)"/g)) {
    values.push(Number(rect[1]) + Number(rect[3]), Number(rect[2]) + Number(rect[4]));
  }
  for (const seg of String(markup).matchAll(/[ML]\s*(-?[\d.]+)[ ,]+(-?[\d.]+)/g)) values.push(Number(seg[1]), Number(seg[2]));
  return values.filter((value) => Number.isFinite(value));
}

// 稽核:與社群圖示同一份一致性契約(同網格、同筆畫、同留白),外加一條自己的:
// 有 caveat 的圖示必須把替代方案寫在 caveat 裡 —— 只說「不能用」而不說「改用什麼」,
// 下一個人會直接把它用下去。
function auditGlyphSet(options = {}) {
  const mutation = String(options.test_mutation || "");
  const blockers = [];
  for (const [key, glyph] of Object.entries(GLYPHS)) {
    if (!glyph.label) blockers.push("ui_glyph_label_missing:" + key);
    const svg = renderGlyph(key);
    if (!svg.includes('viewBox="' + VIEWBOX + '"')) blockers.push("ui_glyph_viewbox_invalid:" + key);
    if (!svg.includes('stroke="currentColor"')) blockers.push("ui_glyph_not_currentcolor:" + key);
    if (!svg.includes('stroke-width="' + STROKE_WIDTH + '"')) blockers.push("ui_glyph_weight_invalid:" + key);
    if (/<script|href=|url\(|https?:|fill="#/i.test(svg)) blockers.push("ui_glyph_external_or_coloured:" + key);
    if (mutation !== "allow-out-of-bounds") {
      for (const value of absoluteCoordinates(glyph.body)) {
        if (value < OPTICAL_MIN - 0.6 || value > OPTICAL_MAX + 0.6) { blockers.push("ui_glyph_out_of_optical_bounds:" + key + ":" + value); break; }
      }
    }
    if (glyph.caveat != null && !/建議改用/.test(glyph.caveat) && mutation !== "allow-caveat-without-alternative") {
      blockers.push("ui_glyph_caveat_without_alternative:" + key);
    }
  }
  return {
    schema_version: VERSION,
    status: blockers.length ? "blocked" : "ok",
    blockers: [...new Set(blockers)].sort(),
    observed: {
      glyphs: Object.keys(GLYPHS).sort(),
      actionable_on_zero_script_page: [...ACTIONABLE].sort(),
      blocked_by_caveat: Object.keys(GLYPHS).filter((key) => GLYPHS[key].caveat),
      bounds_check: "absolute_coordinates_only"
    }
  };
}

module.exports = { VERSION, VIEWBOX, STROKE_WIDTH, OPTICAL_MIN, OPTICAL_MAX, GLYPHS, ACTIONABLE, has, renderGlyph, auditGlyphSet, absoluteCoordinates };
