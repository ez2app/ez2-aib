"use strict";

// Ez2.PAGE 社群圖示(the maintainer 2026-07-26 拍板:線條與品牌兩套,預設線條)。
//
// ★ 為什麼每個平台是兩條幾何而不是一條(2026-07-26 判例,勿再回頭合併):
//   第一版試過「一組幾何、兩種上色」——同一條 path,line 描邊、brand 填色。
//   模組測試 99/99 全綠,截圖一看有一半是壞的:描邊用的幾何(外框 + 內圓)
//   填色後外框被填滿、內部細節整個消失,IG 變成一個粉紅方塊;而填色用的
//   剪影描邊後只剩輪廓,Facebook 的 f 糊成一團。
//   **這是幾何本身的性質,不是實作錯誤**:線條圖示靠筆畫傳遞資訊,
//   填色圖示靠剪影傳遞資訊,同一條路徑不可能兩邊都對。
//   Apple 的 SF Symbols 也是這樣做的——`.fill` 變體不是同一條 path。
//
// ★ 那「風格一致」靠什麼守?靠三條機器檢查得動的規則,不是靠幾何相同:
//   1. 同網格:viewBox 一律 0 0 24 24
//   2. 同光學重量:line 一律 stroke-width 1.8 + fill:none;brand 一律填色 + 無描邊
//   3. 同視覺留白:所有絕對座標落在 2.5–21.5 的光學邊界內
//   (真正讓一排圖示看起來像一套的是光學重量,不是幾何——第一版幾何逐字
//    相同卻散成一盤,就是反證。)
//
// 為什麼不用各平台的官方 logo 檔:外部資產違反 bio 頁零外源鐵律
// (CSP default-src 'none'),內嵌成 data URI 會吃掉體積預算,而各家 logo 的
// 長寬比與留白互不相同,並排會參差。自繪同型的識別圖示屬於指稱性使用
// (說明「這條連結通往哪裡」)。
// ★ 這不是法律意見:brand 模式對外開放前應逐家覆核品牌規範,
//   中國平台(微信/抖音/小紅書)另有各自規定。
//
// 邊界:零外源、零 script;line 模式一律 currentColor,所以八個主題換皮時
// 圖示自動跟著主題文字色走。legacy(舊頁面搬家)不在顯示集:它是匯入來源,
// 不是社群身份——畫在公開頁上等於告訴訪客「我從別的平台搬來的」。

const VERSION = "2026-07-26.ez2-page-social-icons.v2";
const MODES = Object.freeze(["line", "brand"]);
const DEFAULT_MODE = "line";
const VIEWBOX = "0 0 24 24";
const STROKE_WIDTH = 1.8;
// 光學邊界:所有圖示的絕對座標都要落在這裡面,一排擺起來才會對齊。
const OPTICAL_MIN = 2.5;
const OPTICAL_MAX = 21.5;

// line = 線條幾何(描邊用,開放內部空間);brand = 剪影幾何(填色用,evenodd 鏤空)。
const ICONS = Object.freeze({
  ig: Object.freeze({
    label: "Instagram", brand: "#E1306C",
    line: '<rect x="3" y="3" width="18" height="18" rx="5.2"/><circle cx="12" cy="12" r="4.1"/><circle cx="17.3" cy="6.7" r="1.15"/>',
    fill: '<path fill-rule="evenodd" d="M8.2 2.5h7.6c3.15 0 5.7 2.55 5.7 5.7v7.6c0 3.15-2.55 5.7-5.7 5.7H8.2c-3.15 0-5.7-2.55-5.7-5.7V8.2c0-3.15 2.55-5.7 5.7-5.7zm0 2.2c-1.93 0-3.5 1.57-3.5 3.5v7.6c0 1.93 1.57 3.5 3.5 3.5h7.6c1.93 0 3.5-1.57 3.5-3.5V8.2c0-1.93-1.57-3.5-3.5-3.5zM12 7.2a4.8 4.8 0 1 1 0 9.6 4.8 4.8 0 0 1 0-9.6zm0 2.2a2.6 2.6 0 1 0 0 5.2 2.6 2.6 0 0 0 0-5.2zm5.1-3.8a1.3 1.3 0 1 1 0 2.6 1.3 1.3 0 0 1 0-2.6z"/>'
  }),
  line: Object.freeze({
    label: "LINE", brand: "#06C755",
    line: '<path d="M21 10.6c0-4-4-7.2-9-7.2s-9 3.2-9 7.2c0 3.6 3.2 6.6 7.5 7.1.3.1.7.2.8.5.1.3 0 .7 0 1l-.1.8c0 .3-.2 1 .9.6 1.1-.5 6-3.5 8.2-6 1.5-1.6 1.7-3.2 1.7-4z"/>',
    fill: '<path d="M12 3.2c-4.97 0-9 3.19-9 7.13 0 3.53 3.2 6.49 7.52 7.05.29.06.69.19.79.44.09.22.06.57.03.79l-.13.77c-.04.23-.18.9.79.49.97-.41 5.24-3.09 7.15-5.29 1.32-1.45 1.85-2.92 1.85-4.25 0-3.94-4.03-7.13-9-7.13z"/>'
  }),
  fb: Object.freeze({
    label: "Facebook", brand: "#0866FF",
    line: '<circle cx="12" cy="12" r="9.3"/><path d="M14.9 8.3h-1.3c-.85 0-1.35.5-1.35 1.35v1.85h2.55l-.4 2.6h-2.15v6.9M9.6 11.5h2.35"/>',
    fill: '<path fill-rule="evenodd" d="M12 2.7a9.3 9.3 0 1 1 0 18.6 9.3 9.3 0 0 1 0-18.6zm1.1 18.2v-6.6h2.2l.42-2.6H13.1v-1.7c0-.75.37-1.48 1.55-1.48h1.17V6.3s-1.06-.18-2.07-.18c-2.11 0-3.49 1.28-3.49 3.6v2.03H7.94v2.6h2.32v6.6z"/>'
  }),
  youtube: Object.freeze({
    label: "YouTube", brand: "#FF0000",
    line: '<rect x="2.9" y="5.2" width="18.2" height="13.6" rx="4"/><path d="M10.3 9.1v5.8l5.1-2.9z"/>',
    fill: '<path fill-rule="evenodd" d="M8.4 4.6h7.2c2.2 0 3.4.2 4.2.9.85.75 1.2 1.95 1.2 4.1v4.8c0 2.15-.35 3.35-1.2 4.1-.8.7-2 .9-4.2.9H8.4c-2.2 0-3.4-.2-4.2-.9-.85-.75-1.2-1.95-1.2-4.1V9.6c0-2.15.35-3.35 1.2-4.1.8-.7 2-.9 4.2-.9zm1.9 4.3v6.2l5.4-3.1z"/>'
  }),
  threads: Object.freeze({
    // ★ 品牌色接近黑或白的平台,在對應底色上會整個消失(深空底畫 #101010
    // = 黑底黑圖)。Threads 自己的品牌規範就是淺底用黑、深底用白,所以
    // brand_on_dark 不是我們發明的變通,是照它的規範走。
    label: "Threads", brand: "#101010", brand_on_dark: "#FFFFFF",
    line: '<circle cx="12" cy="12" r="9.3"/><path d="M16.1 11.6c-.15-2.65-1.7-4.15-4.05-4.15-1.4 0-2.5.55-3.2 1.6M16.1 11.6c.75.45 1.05 1.1.5 1.8-.65.8-1.65 1.25-2.9 1.3-1.75.08-3.05-.75-3.1-2-.05-1.15 1.05-1.9 2.85-2 1.05-.06 2 .04 2.65.2"/>',
    fill: '<path fill-rule="evenodd" d="M12 2.7a9.3 9.3 0 1 1 0 18.6 9.3 9.3 0 0 1 0-18.6zm.15 3.5c-1.62 0-2.97.69-3.81 1.96l1.5 1.03c.62-.94 1.6-1.14 2.31-1.14h.03c.88 0 1.55.27 1.98.77.32.36.52.87.63 1.51a11.4 11.4 0 0 0-2.2-.09c-2.56.14-4.21 1.64-4.1 3.72.06 1.06.58 1.96 1.48 2.56.75.5 1.73.74 2.75.69 1.35-.07 2.4-.59 3.14-1.53.64-1 .1-1.49-.4-1.83a3.1 3.1 0 0 0-.27-.13c-.15-2.98-1.78-4.7-4.5-4.72zm.3 5.55c.63 0 1.27.06 1.89.19-.21 2.68-1.47 3.05-2.59 3.1-1.13.06-2.3-.44-2.35-1.5-.04-.78.56-1.66 2.43-1.77.21-.01.42-.02.62-.02z"/>'
  }),
  douyin: Object.freeze({
    label: "抖音", brand: "#FE2C55",
    line: '<path d="M16.6 3.4c.5 2 1.9 3.4 3.8 3.6M16.6 3.4v10c0 4.6-4.4 7.3-8 5.2-3.1-1.8-3.3-6.2-.4-8.3 1.1-.8 2.5-1.1 3.8-.8M20.4 7v2.6c-1.5.1-2.9-.3-4.2-1.1"/>',
    fill: '<path d="M17.4 3.2c.48 2.05 1.9 3.44 3.85 3.63v2.6c-1.5.08-2.9-.31-4.2-1.1v5.9c0 4.6-4.4 7.3-8 5.2-3.1-1.8-3.3-6.2-.4-8.3 1.1-.8 2.5-1.1 3.8-.8v2.8c-1.7-.5-3.1.9-2.6 2.4.4 1.3 2 1.8 3.1 1 .5-.4.8-1 .8-1.7V3.2z"/>'
  }),
  xhs: Object.freeze({
    label: "小紅書", brand: "#FF2442",
    line: '<rect x="2.9" y="4.9" width="18.2" height="14.2" rx="3.6"/><path d="M7.1 9.4v5.2M7.1 12h2.5M9.6 9.4v5.2M12.9 9.4v5.2h2.3M17.3 9.4v5.2"/>',
    // 16px 下內部字塊會糊掉,所以剪影只留一個粗筆畫的開口方框 —— 細節在
    // 這個尺寸傳遞不了資訊,只會變成噪點。旁邊一律有平台名可讀。
    fill: '<path fill-rule="evenodd" d="M7.6 3.6h8.8c2.3 0 3.4.4 4.2 1.2.8.8 1.2 1.9 1.2 4.2v6c0 2.3-.4 3.4-1.2 4.2-.8.8-1.9 1.2-4.2 1.2H7.6c-2.3 0-3.4-.4-4.2-1.2-.8-.8-1.2-1.9-1.2-4.2V9c0-2.3.4-3.4 1.2-4.2.8-.8 1.9-1.2 4.2-1.2zm-.5 5.1v6.6h2.3v-2.5h1.9v2.5h2.3V8.7h-2.3v2.2H9.4V8.7zm9.1 0h-2.3v6.6h4.3v-2.1h-2z"/>'
  }),
  whatsapp: Object.freeze({
    label: "WhatsApp", brand: "#25D366",
    line: '<path d="M21 11.5a8.4 8.4 0 0 1-8.5 8.3 9 9 0 0 1-3.8-.8L3 20.5l1.5-4.4a8 8 0 0 1-1.4-4.6A8.4 8.4 0 0 1 11.5 3 8.4 8.4 0 0 1 21 11.5z"/><path d="M9.4 8.3c.2 0 .4 0 .5.4l.7 1.6c.1.2 0 .4-.1.5l-.5.6c-.1.2-.2.3 0 .6a7 7 0 0 0 2.9 2.4c.3.1.5.1.6-.1l.6-.7c.2-.2.3-.2.5-.1l1.6.8c.3.1.3.3.3.5 0 .8-.7 1.5-1.5 1.6-1.7.1-4-1.3-5.3-3.1-1-1.4-1.2-2.6-.9-3.5.2-.7.9-1.5 1.6-1.5z"/>',
    fill: '<path fill-rule="evenodd" d="M12.05 3a8.5 8.5 0 0 0-7.2 13l-1.35 4.05 4.25-1.1a8.5 8.5 0 1 0 4.3-15.95zM9.5 8.1c.2 0 .42.02.6.42l.72 1.65c.1.22.03.44-.1.6l-.5.6c-.14.18-.2.3-.03.58a7 7 0 0 0 2.9 2.4c.3.13.5.12.63-.07l.6-.7c.18-.2.33-.2.53-.12l1.6.8c.28.14.32.3.3.5-.03.8-.72 1.5-1.5 1.6-1.7.12-4-1.3-5.3-3.1-1-1.4-1.2-2.6-.9-3.5.2-.7.9-1.5 1.6-1.5z"/>'
  }),
  wechat: Object.freeze({
    label: "微信", brand: "#07C160",
    line: '<path d="M15.6 8.9c-.55-2.95-3.35-5.2-6.75-5.2-3.75 0-6.8 2.75-6.8 6.15 0 1.95 1 3.7 2.55 4.85l-.65 2.2 2.5-1.25c.65.2 1.35.3 2.1.3"/><path d="M21.5 14.4c0-2.75-2.6-4.95-5.8-4.95s-5.8 2.2-5.8 4.95 2.6 4.95 5.8 4.95c.7 0 1.4-.1 2.05-.3l2.15 1.1-.6-1.9c1.35-.9 2.2-2.3 2.2-3.85z"/>',
    fill: '<path d="M8.85 3.6c3.65 0 6.65 2.4 6.9 5.45h-.05c-3.4 0-6.15 2.4-6.15 5.35 0 .3.03.6.08.88-.24.02-.49.03-.73.03-.85 0-1.65-.11-2.4-.31l-2.6 1.3.65-2.2C2.9 13 2 11.35 2 9.5 2 6.24 5.06 3.6 8.85 3.6zm-2.4 3.05a.95.95 0 1 0 0 1.9.95.95 0 0 0 0-1.9zm4.1 0a.95.95 0 1 0 0 1.9.95.95 0 0 0 0-1.9z"/><path d="M15.9 9.9c3.35 0 6.1 2.3 6.1 5.15 0 1.6-.86 3.02-2.2 3.98l.55 1.85-2.2-1.1c-.7.2-1.45.32-2.25.32-3.35 0-6.1-2.3-6.1-5.05s2.75-5.15 6.1-5.15zm-1.95 2.8a.85.85 0 1 0 0 1.7.85.85 0 0 0 0-1.7zm3.85 0a.85.85 0 1 0 0 1.7.85.85 0 0 0 0-1.7z"/>'
  })
,
  // RL-140(2026-09-18):x / tiktok 各自一顆(289 (e) 取回的作者頁要進 socials)。兩家的品牌色都是黑,照 Threads 的例子宣告深底用白。
  x: Object.freeze({
    label: "X", brand: "#000000", brand_on_dark: "#FFFFFF",
    line: '<path d="M5 4.5h3.2L19 19.5h-3.2z"/><path d="M18.4 4.5l-5.3 6.2M5.6 19.5l5.4-6.3"/>',
    fill: '<path fill-rule="evenodd" d="M4.2 3.6h4.4l4.1 5.6 4.8-5.6h2.3l-6.1 7 6.6 9.2h-4.4l-4.4-6.1-5.3 6.1H3.9l6.6-7.6zm3 1.6 9.6 13.4h1.5L8.7 5.2z"/>'
  }),
  tiktok: Object.freeze({
    // 與 douyin 同一個產品標誌(brand-logos 也是同一條 path),差在品牌色:TikTok 國際版走黑白,抖音走紅。
    label: "TikTok", brand: "#000000", brand_on_dark: "#FFFFFF",
    line: '<path d="M16.6 3.4c.5 2 1.9 3.4 3.8 3.6M16.6 3.4v10c0 4.6-4.4 7.3-8 5.2-3.1-1.8-3.3-6.2-.4-8.3 1.1-.8 2.5-1.1 3.8-.8M20.4 7v2.6c-1.5.1-2.9-.3-4.2-1.1"/>',
    fill: '<path d="M17.4 3.2c.48 2.05 1.9 3.44 3.85 3.63v2.6c-1.5.08-2.9-.31-4.2-1.1v5.9c0 4.6-4.4 7.3-8 5.2-3.1-1.8-3.3-6.2-.4-8.3 1.1-.8 2.5-1.1 3.8-.8v2.8c-1.7-.5-3.1.9-2.6 2.4.4 1.3 2 1.8 3.1 1 .5-.4.8-1 .8-1.7V3.2z"/>'
  })
});

const DISPLAY_ORDER = Object.freeze(["ig", "line", "fb", "youtube", "threads", "douyin", "xhs", "whatsapp", "wechat", "x", "tiktok"]);

// 底色種類沿用設計包的主題 kind,不自創第三套詞彙。vivid(高飽和底)當深色處理。
const SURFACES = Object.freeze(["light", "dark", "vivid"]);

function fail(code, details = []) { const error = new Error(code); error.code = code; error.details = details; throw error; }
function has(platform) { return Object.prototype.hasOwnProperty.call(ICONS, String(platform)); }

// 深底用哪個顏色:平台有宣告 brand_on_dark 就用它,沒有就用原色。
// 只有品牌色接近黑或白的平台需要宣告,其餘平台的識別色在兩種底色上都站得住。
// WCAG 相對亮度。用來判斷「這個品牌色在深底上會不會消失」,不是用來判定
// 對比度合格——那需要知道實際底色,而底色由主題決定。
function relativeLuminance(hex) {
  const channel = (value) => {
    const ratio = value / 255;
    return ratio <= 0.03928 ? ratio / 12.92 : Math.pow((ratio + 0.055) / 1.055, 2.4);
  };
  const r = parseInt(String(hex).slice(1, 3), 16);
  const g = parseInt(String(hex).slice(3, 5), 16);
  const b = parseInt(String(hex).slice(5, 7), 16);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function brandColour(platform, surface) {
  const icon = ICONS[String(platform)];
  const dark = surface === "dark" || surface === "vivid";
  return dark && icon.brand_on_dark ? icon.brand_on_dark : icon.brand;
}

// 一顆自足的 <svg>,不含外部參照。
// aria:圖示旁邊一律有平台名(見渲染器),所以圖示本身對輔助技術隱藏,
// 不製造「Instagram Instagram」這種重複朗讀。
function renderIcon(platform, options = {}) {
  const key = String(platform);
  if (!has(key)) fail("SOCIAL_ICON_UNKNOWN", [key]);
  const mode = String(options.mode === undefined ? DEFAULT_MODE : options.mode);
  if (!MODES.includes(mode)) fail("SOCIAL_ICON_MODE_UNKNOWN", [mode]);
  const icon = ICONS[key];
  // surface = 這顆圖示要放在什麼底色上。來源是主題自己的 kind(dark|light|vivid),
  // 不要另外猜——設計包已經替每個主題宣告過了。
  const surface = String(options.surface === undefined ? "light" : options.surface);
  if (!SURFACES.includes(surface)) fail("SOCIAL_ICON_SURFACE_UNKNOWN", [surface]);
  const paint = mode === "brand"
    ? 'fill="' + brandColour(key, surface) + '" stroke="none"'
    : 'fill="none" stroke="currentColor" stroke-width="' + STROKE_WIDTH + '" stroke-linecap="round" stroke-linejoin="round"';
  return '<svg viewBox="' + VIEWBOX + '" ' + paint + ' aria-hidden="true" focusable="false">' +
    (mode === "brand" ? icon.fill : icon.line) + "</svg>";
}

// 絕對座標抽取:大寫指令後面的數字對(以及 rect/circle 的屬性)。
// ★ 誠實邊界:相對指令(小寫)的實際落點要累加起點才算得出來,本檢查看不到,
//   所以 observed 明寫 bounds_check 只涵蓋絕對座標。宣告出來的盲區才不會被
//   讀成「全部量過了」。
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

// 稽核:三條一致性規則的機器形式。
function auditIconSet(options = {}) {
  const mutation = String(options.test_mutation || "");
  const blockers = [];
  const keys = Object.keys(ICONS).filter((key) => mutation !== "drop-icon" || key !== "wechat");
  for (const key of keys) {
    const icon = ICONS[key];
    if (!/^#[0-9a-fA-F]{6}$/.test(icon.brand)) blockers.push("social_icon_brand_colour_invalid:" + key);
    if (!icon.label) blockers.push("social_icon_label_missing:" + key);
    if (!icon.line || !icon.fill) blockers.push("social_icon_geometry_missing:" + key);
    const line = renderIcon(key, { mode: "line" });
    const brand = renderIcon(key, { mode: "brand" });
    // 規則 1:同網格
    for (const svg of [line, brand]) {
      if (!svg.includes('viewBox="' + VIEWBOX + '"')) blockers.push("social_icon_viewbox_invalid:" + key);
      if (/<script|href=|url\(|https?:/i.test(svg)) blockers.push("social_icon_external_or_active:" + key);
    }
    // 規則 2:同光學重量
    if (!line.includes('stroke-width="' + STROKE_WIDTH + '"') || !line.includes('fill="none"') || !line.includes("currentColor")) {
      blockers.push("social_icon_line_weight_invalid:" + key);
    }
    if (!brand.includes('fill="' + icon.brand + '"') || !brand.includes('stroke="none"')) {
      blockers.push("social_icon_brand_paint_invalid:" + key);
    }
    // 規則 2b:品牌色接近黑或白的平台,必須宣告深底替代色,否則在對應底色上隱形。
    // 這條是截圖抓到的(Threads #101010 在深空底上整個不見),靜態檢查補上牙。
    const luminance = relativeLuminance(icon.brand);
    if (mutation !== "allow-invisible-brand" && luminance <= 0.12 && !icon.brand_on_dark) {
      blockers.push("social_icon_brand_invisible_on_dark:" + key);
    }
    if (icon.brand_on_dark) {
      const onDark = renderIcon(key, { mode: "brand", surface: "dark" });
      if (!onDark.includes('fill="' + icon.brand_on_dark + '"')) blockers.push("social_icon_dark_variant_unused:" + key);
    }
    // 規則 3:同視覺留白(僅絕對座標,見 absoluteCoordinates 的誠實邊界)
    if (mutation !== "allow-out-of-bounds") {
      for (const value of absoluteCoordinates(icon.line).concat(absoluteCoordinates(icon.fill))) {
        if (value < OPTICAL_MIN - 0.6 || value > OPTICAL_MAX + 0.6) {
          blockers.push("social_icon_out_of_optical_bounds:" + key + ":" + value);
          break;
        }
      }
    }
  }
  for (const key of DISPLAY_ORDER) if (!keys.includes(key)) blockers.push("social_icon_display_missing:" + key);
  if (DISPLAY_ORDER.includes("legacy")) blockers.push("social_icon_legacy_is_not_a_platform");
  return {
    schema_version: VERSION,
    status: blockers.length ? "blocked" : "ok",
    blockers: [...new Set(blockers)].sort(),
    observed: {
      modes: MODES, default_mode: DEFAULT_MODE, icons: keys.sort(), display_order: [...DISPLAY_ORDER],
      optical_bounds: [OPTICAL_MIN, OPTICAL_MAX],
      bounds_check: "absolute_coordinates_only",
      not_covered: "相對指令的實際落點、以及描邊粗細造成的視覺溢出,靜態檢查看不到;要真正確認要用截圖"
    }
  };
}

module.exports = { VERSION, MODES, SURFACES, DEFAULT_MODE, VIEWBOX, STROKE_WIDTH, OPTICAL_MIN, OPTICAL_MAX, ICONS, DISPLAY_ORDER, has, renderIcon, auditIconSet, absoluteCoordinates, brandColour, relativeLuminance };
