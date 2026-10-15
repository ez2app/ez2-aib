"use strict";

// Ez2.PAGE design pack — single source of truth for bio-page tokens/blocks/themes.
// Values are transcribed verbatim from docs/design/ez2-page-frontend-mock.html (frontend v41);
// the smoke enforces conservation against that mock, so edits must land in the mock first.
// Scope: bio-page vocabulary only. Official-site scroll theater, phone demo frame and page
// document assembly belong to later lanes (D2/D3) and are intentionally absent here.

const blockContract = require("./ez2-page-block-contract.js");
const performancePolicy = require("./ez2-page-performance-policy.js");

const VERSION = "2026-07-25.ez2-page-design-pack.v1";
const SOURCE_MOCK = "docs/design/ez2-page-frontend-mock.html";
// Bio-page CSS is delivered as a bundle file (inline_styles budget stays 0); this budget must
// leave the html budget intact inside the total payload budget — audited in auditDesignPack().
const CSS_BUDGET_BYTES = 24576;

const TOKENS = Object.freeze({
  "--ink": "#1d1d1f",
  "--paper": "#ffffff",
  "--paper-2": "#f5f5f7",
  "--mute": "#6e6e73",
  "--hairline": "#d2d2d7",
  "--night": "#000000",
  "--night-2": "#0a0a0f",
  "--snow": "#f5f5f7",
  "--dim": "#86868b",
  "--brand": "#8B7CFF",
  "--glow": "#43D9C0",
  "--grad": "linear-gradient(96deg,#a18fff 0%,#7c6cff 34%,#43d9c0 100%)",
  "--font": '-apple-system,"SF Pro TC","PingFang TC","Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif',
  "--mono": '"SF Mono",ui-monospace,Menlo,monospace'
});

const KEYFRAMES = Object.freeze({
  spin: "to{transform:rotate(360deg)}",
  breathe: "0%,100%{box-shadow:0 0 0 0 rgba(124,108,255,.3)}50%{box-shadow:0 0 22px 4px rgba(124,108,255,.5)}",
  pulse: "0%{transform:scale(1);opacity:.8}70%{transform:scale(2.3);opacity:0}100%{opacity:0}",
  eqbar: "0%,100%{transform:scaleY(.3)}50%{transform:scaleY(1)}",
  shimmer: "0%{transform:translateX(-130%)}100%{transform:translateX(130%)}",
  barmove: "from{width:6%}to{width:92%}",
  hb: "0%,100%{transform:scale(1)}12%{transform:scale(1.22)}24%{transform:scale(1)}",
  flowY: "0%,10%{transform:translateY(0)}46%,58%{transform:translateY(var(--travel,-46%))}92%,100%{transform:translateY(0)}",
  // 社群圖示的呼吸光。刻意不重用 breathe:那支的顏色寫死是紫色,森林或玫瑰金
  // 的頁面上會亮出一圈不屬於它的光。這支吃主題自己宣告的 --glow,而且振幅
  // 比 breathe 小很多——CTA 是主角要被看見,社群列是配角只要活著。
  softglow: "0%,100%{box-shadow:0 0 0 0 transparent}50%{box-shadow:0 0 11px 1px color-mix(in srgb, var(--glow) 42%, transparent)}"
});

const ANIMATED_PROP_WHITELIST = Object.freeze(["transform", "opacity", "box-shadow"]);
// barmove animates width on a 3px-tall progress bar — mock-faithful, negligible reflow.
// Any NEW keyframe touching layout props must be argued into this table, not slipped past it.
const KEYFRAME_LAYOUT_EXCEPTIONS = Object.freeze({ barmove: ["width"] });

const THEMES = Object.freeze({
  nebula: Object.freeze({ kind: "dark", screen: "background:#0A0E1D;background-image:radial-gradient(300px 180px at 50% -30px, rgba(139,124,255,.3), transparent 70%),radial-gradient(240px 160px at 88% 34%, rgba(67,217,192,.12), transparent 60%);color:#EDEFFA", tokens: Object.freeze({"--glow":"#43D9C0","--grad":"linear-gradient(96deg,#a18fff 0%,#7c6cff 34%,#43d9c0 100%)"}) }),
  mono: Object.freeze({ kind: "light", screen: "background:#fbfbfd; color:#1d1d1f", tokens: Object.freeze({"--glow":"#6e6e73","--grad":"linear-gradient(96deg,#3a3a3c 0%,#1d1d1f 60%,#000000 100%)"}) }),
  dawn: Object.freeze({ kind: "dark", screen: "background:linear-gradient(170deg,#2b1a3a 0%,#4a2440 46%,#8a4a3a 100%); color:#FBEFE8", tokens: Object.freeze({"--glow":"#F3B27A","--grad":"linear-gradient(96deg,#8a4a3a 0%,#c26a4a 50%,#f3b27a 100%)"}) }),
  forest: Object.freeze({ kind: "dark", screen: "background:linear-gradient(175deg,#0c1f18 0%,#123227 60%,#1a4434 100%); color:#E7F4EC", tokens: Object.freeze({"--glow":"#8FD9B6","--grad":"linear-gradient(96deg,#1a4434 0%,#2f8a63 55%,#8fd9b6 100%)"}) }),
  rosegold: Object.freeze({ kind: "light", screen: "background:linear-gradient(168deg,#f9e8e2 0%,#f3d3c8 48%,#e9bcab 100%); color:#4a2c26", tokens: Object.freeze({"--glow":"#E4A79A","--grad":"linear-gradient(96deg,#b4756a 0%,#d99a8a 55%,#f0cfc4 100%)"}) }),
  starlight: Object.freeze({ kind: "light", screen: "background:linear-gradient(175deg,#f7f2e9 0%,#efe6d8 60%,#e7dcc8 100%); color:#3c352c", tokens: Object.freeze({"--glow":"#C9B892","--grad":"linear-gradient(96deg,#8a7a5f 0%,#b8a480 55%,#e5dcc9 100%)"}) }),
  pop: Object.freeze({ kind: "vivid", screen: "background:linear-gradient(160deg,#7b2ff7 0%,#ef476f 52%,#ffd166 100%); color:#fff", tokens: Object.freeze({"--glow":"#EF476F","--grad":"linear-gradient(96deg,#ffd166 0%,#ef476f 52%,#7b2ff7 100%)"}) }),
  ocean: Object.freeze({ kind: "dark", screen: "background:linear-gradient(172deg,#042a40 0%,#0a4d68 60%,#0f6f8c 100%); color:#e6f6fb", tokens: Object.freeze({"--glow":"#7FD8EE","--grad":"linear-gradient(96deg,#0a4d68 0%,#2fa8cf 55%,#9fe4f5 100%)"}) })
});

// 按鈕形狀。與主題正交:主題管顏色與材質,形狀管輪廓 —— 兩個維度分開,
// 店主可以要「森海配膠囊」也可以要「森海配長方」,八主題乘三形狀等於
// 二十四種長相,而程式只多了一張表。
//
// ★ 只作用在**按鈕類**元件(連結卡、動作鈕、音樂、活動、贊助),不動媒體類
//   (圖片、影片、商品縮圖)。膠囊套在 96px 高的圖片上會變成一顆藥丸,
//   那不是形狀選擇是破版。Linktree 的按鈕樣式也只管按鈕。
//
// ★ 實作用 var(--btn-r, <原值>) 而不是換掉原值:沒指定形狀時輸出與過去
//   逐位元組相同,所以這個新維度不會偷偷改動任何既有頁面的長相。
const SHAPES = Object.freeze({
  rounded: Object.freeze({ label: "圓角", tokens: Object.freeze({}) }),
  rectangle: Object.freeze({ label: "長方", tokens: Object.freeze({ "--btn-r": "4px" }) }),
  pill: Object.freeze({ label: "膠囊", tokens: Object.freeze({ "--btn-r": "999px" }) })
});
const DEFAULT_SHAPE = "rounded";
// page/aib-oneshot-0926(+avatar size/shape):頭像尺寸 / 外框形狀白名單。md=78 與原稿 .b-ava 逐位元組同(預設不輸出覆蓋);
//   lg/xl 與 rounded 才由 renderBioCss 在 BASE_RULES 之後覆寫 width/height/radius(BASE_RULES 守 mock conserve 不動)。
const AVATAR_SIZES = Object.freeze({ md: 78, lg: 112, xl: 144 });
const AVATAR_SHAPES = Object.freeze({ circle: "50%", rounded: "26%" });
const DEFAULT_AVATAR_SIZE = "md";
const DEFAULT_AVATAR_SHAPE = "circle";
function resolveAvatarAppearance(options = {}) {
  const sizeKey = options.avatar_size != null && Object.prototype.hasOwnProperty.call(AVATAR_SIZES, String(options.avatar_size))
    ? String(options.avatar_size) : DEFAULT_AVATAR_SIZE;
  const shapeKey = options.avatar_shape != null && Object.prototype.hasOwnProperty.call(AVATAR_SHAPES, String(options.avatar_shape))
    ? String(options.avatar_shape) : DEFAULT_AVATAR_SHAPE;
  // template fixed.avatar.size 是 baseline px;appearance.avatar_size 鍵勝(有給且合法時)。沒給 appearance 時用 fixed px 對回鍵,對不上就當自訂 px。
  let sizePx = AVATAR_SIZES[sizeKey];
  if (options.avatar_size == null && Number.isInteger(options.fixed_avatar_size) && options.fixed_avatar_size > 0) {
    sizePx = options.fixed_avatar_size;
    const hit = Object.keys(AVATAR_SIZES).find((k) => AVATAR_SIZES[k] === sizePx);
    return Object.freeze({ size_key: hit || null, shape_key: shapeKey, size_px: sizePx, radius: AVATAR_SHAPES[shapeKey], is_default: sizePx === AVATAR_SIZES.md && shapeKey === DEFAULT_AVATAR_SHAPE });
  }
  return Object.freeze({ size_key: sizeKey, shape_key: shapeKey, size_px: sizePx, radius: AVATAR_SHAPES[shapeKey], is_default: sizeKey === DEFAULT_AVATAR_SIZE && shapeKey === DEFAULT_AVATAR_SHAPE });
}
function avatarOverrideCss(scope, appearance) {
  if (!appearance || appearance.is_default) return "";
  const size = appearance.size_px + "px";
  const radius = appearance.radius;
  const letter = Math.round(appearance.size_px * (27 / 78));
  const overlap = Math.round(appearance.size_px / 2);
  const prefix = scope === "body" ? "body " : (scope + " ");
  return [
    prefix + ".b-ava{width:" + size + "; height:" + size + "; border-radius:" + radius + "}",
    prefix + ".b-ava .i{border-radius:" + radius + "; font-size:" + letter + "px}",
    prefix + ".b-ava::before{border-radius:" + radius + "}",
    prefix + ".b-cover + .b-ava{margin-top:-" + overlap + "px}"
  ].join("\n");
}
function validateAvatarAppearance(options = {}) {
  const errors = [];
  // v3.108 S1: empty string = clear avatar override (omit = keep). Invalid non-empty still 422.
  if (options.avatar_size != null && options.avatar_size !== "" && !Object.prototype.hasOwnProperty.call(AVATAR_SIZES, String(options.avatar_size))) errors.push("page_appearance_avatar_size_invalid");
  if (options.avatar_shape != null && options.avatar_shape !== "" && !Object.prototype.hasOwnProperty.call(AVATAR_SHAPES, String(options.avatar_shape))) errors.push("page_appearance_avatar_shape_invalid");
  return errors;
}

// 13-entry block vocabulary (skill §4;289 五起 +1 = 14,place 商家格). classes = design surface; contract_types = which
// block-contract types this entry renders (form is contract-legal, editor-blocked, unmapped).
//
// source = "profile_chrome" 的四列(頭像/名字徽章/狀態膠囊/社群列)不是區塊:它們由
// 註冊表的 profile 欄位(avatar_asset_id / display_name+identity_verified_at /
// status_text / social_links)畫在頁首,渲染器已經在畫、動畫已經掛上。
// ★ 09-02 艦長裁決:這四種**不開契約型別**——開了就是同一頁兩個頭像(dev@00000
// 15:12 勾表量到雙份)。contract_types 留空是宣告,不是待補;下面的 smoke 有牙。
const PROFILE_CHROME = "profile_chrome";
const BLOCKS = Object.freeze([
  // page/aib-oneshot-0926(+profile slot):avatar 列對 09-02「四種不開」再收回——頭像/身份可當獨立區塊 profile(payload {}),
  //   同頁恰一份由 theme-renderer 守(有 visible profile ⇒ 不在頁首 prepend,改在該顆位置畫 identityHtml);identity/status 仍不開。
  Object.freeze({ key: "avatar", source: PROFILE_CHROME, classes: Object.freeze(["b-ava"]), contract_types: Object.freeze(["profile"]), life: "spin ring + breathe halo" }),
  Object.freeze({ key: "identity", source: PROFILE_CHROME, classes: Object.freeze(["b-id", "vbadge"]), contract_types: Object.freeze([]), life: "" }),
  Object.freeze({ key: "status", source: PROFILE_CHROME, classes: Object.freeze(["b-status", "dot"]), contract_types: Object.freeze([]), life: "pulse dot" }),
  // 316 v1.1(RL-114/117 甲,A27):social 這一列對 09-02「四種不開型別」收回——社群列可當獨立區塊(契約型別 socials,payload {}),
  //   同頁圖示恰一份由 theme-renderer 守(本體有 public socials 顆 ⇒ 頁首只留存成聯絡人);identity/status 仍不開。
  Object.freeze({ key: "social", source: PROFILE_CHROME, classes: Object.freeze(["b-soc"]), contract_types: Object.freeze(["socials"]), life: "hover lift" }),
  Object.freeze({ key: "cta", classes: Object.freeze(["b-cta"]), contract_types: Object.freeze(["cta"]), life: "breathe / shimmer(soon)" }),
  Object.freeze({ key: "link", classes: Object.freeze(["b-lnk"]), contract_types: Object.freeze(["link"]), life: "" }),
  Object.freeze({ key: "music", classes: Object.freeze(["b-music"]), contract_types: Object.freeze(["music"]), life: "eqbar + barmove" }),
  Object.freeze({ key: "video", classes: Object.freeze(["b-vid"]), contract_types: Object.freeze(["video"]), life: "breathe play button" }),
  Object.freeze({ key: "shop", classes: Object.freeze(["b-shop"]), contract_types: Object.freeze(["shop"]), life: "" }),
  Object.freeze({ key: "event", classes: Object.freeze(["b-event"]), contract_types: Object.freeze(["event"]), life: "" }),
  Object.freeze({ key: "mail", classes: Object.freeze(["b-mail"]), contract_types: Object.freeze(["mail"]), life: "" }),
  Object.freeze({ key: "tip", classes: Object.freeze(["b-tip"]), contract_types: Object.freeze(["tip"]), life: "hb heartbeat" }),
  Object.freeze({ key: "content", classes: Object.freeze(["b-img", "b-txt", "b-ft"]), contract_types: Object.freeze(["text", "image"]), life: "" }),
  // 289 五(RL-092):第 14 項 = Google 商家格。不在 mock 裡(mock v41 沒有),樣式在 PLACE_RULES 另立表,所以 conserve:base 照守。
  Object.freeze({ key: "place", classes: Object.freeze(["b-place"]), contract_types: Object.freeze(["place"]), life: "" }),
  // 317 第 5 項(RL-115):第 15/16 項 = 標題(h2/h3)與分隔線。不在 mock 裡,樣式在 STRUCTURE_RULES 另立表,只在頁上有它們時輸出。
  Object.freeze({ key: "heading", classes: Object.freeze(["b-h"]), contract_types: Object.freeze(["heading"]), life: "" }),
  Object.freeze({ key: "divider", classes: Object.freeze(["b-hr"]), contract_types: Object.freeze(["divider"]), life: "" }),
  // 317 第 10 項:第 17 項 = FAQ 摺疊(原生 details)。樣式在 FAQ_RULES 另立表,只在頁上有它時輸出。
  Object.freeze({ key: "faq", classes: Object.freeze(["b-faq"]), contract_types: Object.freeze(["faq"]), life: "" }),
  // page/aib-oneshot-0926:第 25 項 = 摺疊連結夾(link_group)。樣式在 LINK_GROUP_RULES 另立表,只在頁上有它時輸出。
  Object.freeze({ key: "link_group", classes: Object.freeze(["b-link-group"]), contract_types: Object.freeze(["link_group"]), life: "" }),
  // 317 第 9 項:第 18 項 = 自家見證。樣式在 QUOTE_RULES 另立表,只在頁上有它時輸出。
  Object.freeze({ key: "testimonial", classes: Object.freeze(["b-quote"]), contract_types: Object.freeze(["testimonial"]), life: "" }),
  // 317 第 11 項:第 19 項 = 倒數(靜態天數)。樣式在 COUNTDOWN_RULES 另立表,只在頁上有它時輸出。
  Object.freeze({ key: "countdown", classes: Object.freeze(["b-count"]), contract_types: Object.freeze(["countdown"]), life: "" }),
  // 317 第 13 項:第 20 項 = 預約卡(網址卡)。樣式在 BOOK_RULES 另立表,只在頁上有它時輸出。
  Object.freeze({ key: "booking", classes: Object.freeze(["b-book"]), contract_types: Object.freeze(["booking"]), life: "" }),
  // 317 第 12 項:第 21 項 = 檔案下載(PDF)。樣式在 FILE_RULES 另立表,只在頁上有它時輸出。
  Object.freeze({ key: "file", classes: Object.freeze(["b-file"]), contract_types: Object.freeze(["file"]), life: "" }),
  // 317 第 14 項:第 22 項 = QR 分享(內聯 SVG)。樣式在 QR_RULES 另立表,只在頁上有它時輸出。
  Object.freeze({ key: "qr", classes: Object.freeze(["b-qr"]), contract_types: Object.freeze(["qr"]), life: "" }),
  // 317 第 18 項:第 23 項 = 表單(contract 早有;渲染 + 提交端點落地後 CONTRACT_CLASS_MAP.form 從 null 變 b-form)。樣式在 FORM_RULES 另立表,只在頁上有它時輸出。
  Object.freeze({ key: "form", classes: Object.freeze(["b-form"]), contract_types: Object.freeze(["form"]), life: "" }),
  // 317 第 4 項:第 24 項 = 相簿 / 輪播(gallery)。樣式在 GALLERY_RULES 另立表,只在頁上有它時輸出。
  Object.freeze({ key: "gallery", classes: Object.freeze(["b-gal"]), contract_types: Object.freeze(["gallery"]), life: "" })
]);

// 契約型別到設計 class 的對應。後六種的區塊樣式本來就在(mock 早有),
// 這裡把它們與剛落地的契約型別接起來——覆蓋稽核要求每個契約型別都有歸屬,
// 否則會出現「契約說得出口、設計畫不出來」的半套。form 在 317 第 18 項之前是 null(沒端點、CSP form-action 'none' ⇒ 明文決定不畫);
// 端點 + form-action 'self' + 蜜罐落地後改成 b-form。
const CONTRACT_CLASS_MAP = Object.freeze({
  text: "b-txt", image: "b-img", link: "b-lnk", cta: "b-cta", form: "b-form",
  music: "b-music", video: "b-vid", shop: "b-shop", event: "b-event", mail: "b-mail", tip: "b-tip", place: "b-place", heading: "b-h", divider: "b-hr", faq: "b-faq", link_group: "b-link-group", testimonial: "b-quote", countdown: "b-count", booking: "b-book", file: "b-file", qr: "b-qr", gallery: "b-gal", socials: "b-soc", profile: "b-ava"
});

// Theme-neutral block rules, mock order preserved (cascade order matters: the life section
// re-declares .b-ava on purpose). Selectors are relative to the screen scope.
// 287-B 包二:九宮尺寸——data-span="cxr" 對 grid-column / grid-row span(九條)。★ 不進 BASE_RULES:那張表要與 mock 逐條守恆
//   (design-pack 尺 conserve:base),九宮不是 mock 裡的東西;它是版面機制,只在頁上有帶 span 的顆時才輸出(renderBioCss spans:true),
//   所以沒用九宮的頁 CSS 一個位元組都不變(editable-13 / template-demos 的「公開頁逐位元組不變」照守)。選擇器帶 .pg 壓過外殼的 .pg>*。
const SPAN_RULES = Object.freeze([
  [".pg>[data-span=\"1x1\"]", "grid-column:span 1; grid-row:span 1"],
  [".pg>[data-span=\"2x1\"]", "grid-column:span 2; grid-row:span 1"],
  [".pg>[data-span=\"3x1\"]", "grid-column:span 3; grid-row:span 1"],
  [".pg>[data-span=\"1x2\"]", "grid-column:span 1; grid-row:span 2"],
  [".pg>[data-span=\"2x2\"]", "grid-column:span 2; grid-row:span 2"],
  [".pg>[data-span=\"3x2\"]", "grid-column:span 3; grid-row:span 2"],
  [".pg>[data-span=\"1x3\"]", "grid-column:span 1; grid-row:span 3"],
  [".pg>[data-span=\"2x3\"]", "grid-column:span 2; grid-row:span 3"],
  [".pg>[data-span=\"3x3\"]", "grid-column:span 3; grid-row:span 3"]
]);
// 289 〇 / 292:來源行 .b-src(角落小字)與過期標記——另立表,只在頁上有 provenance 顆時輸出(provenance:true),BASE_RULES 守恆不動。
const PROVENANCE_RULES = Object.freeze([
  ["[data-src]", "position:relative"],
  [".b-src", "position:absolute; right:10px; bottom:3px; font-size:8.5px; line-height:1.4; opacity:.5; white-space:nowrap; pointer-events:none; font-weight:400"],
  ["[data-expired]", "opacity:.45; outline:1px dashed currentColor; outline-offset:2px"],
  ["[data-expired] .b-src", "opacity:.9"]
].map(Object.freeze));
// 289 五 place(RL-092):商家格樣式——另立表,只在頁上有 place 顆時輸出(renderBioCss place:true),BASE_RULES 守恆不動;
//   八套共用一份(顏色走 currentColor / --glow / --btn-r 跟主題走;深淺底由 287-B p3 surfaceFor 的卡面規則蓋 .b-place 一起吃)。
// 317 第 5 項:標題與分隔線——另立表,只在頁上有 heading / divider 顆時輸出(renderBioCss structure:true),BASE_RULES 守恆不動;
//   顏色走 currentColor / --glow 跟主題走。space 也是 <hr>(語意仍是分段),只是隱線留高;dots 用 letter-spacing 的三個點。
const STRUCTURE_RULES = Object.freeze([
  [".b-h", "margin:8px 0 0; font-size:18px; line-height:1.3; font-weight:700; letter-spacing:-.01em"],
  ["h3.b-h", "font-size:15px; font-weight:650; opacity:.92"],
  [".b-h[data-align=center]", "text-align:center"],
  [".b-hr", "margin:6px 0; height:1px; background:currentColor; opacity:.18"],
  [".b-hr[data-style=space]", "height:24px; background:none; opacity:1"],
  [".b-hr[data-style=dots]", "height:auto; background:none; opacity:.55; text-align:center; letter-spacing:10px; line-height:1; font-size:14px"],
  [".b-hr[data-style=dots]::before", 'content:"\\2022\\2022\\2022"; color:var(--glow)']
].map(Object.freeze));
// 317 第 6 項:連結的小圖示 .ic(20px,行內,在標題前)與縮圖 .th(56px,取代箭頭)。只在頁上有帶 icon / thumbnail 的連結時輸出。
// 317 第 7 項:重點連結。pulse 用既有 breathe(顏色走 --glow 不寫死紫);grow 是新 keyframe,只動 transform(白名單內、不觸發 layout)。
//   兩條都在 ANIM_PAUSED_RULE / REDUCED_MOTION_RULE 之下自動停。只在頁上有重點連結時輸出(含 @keyframes grow)。
const EMPHASIS_RULES = Object.freeze([
  [".b-lnk.em-pulse", "animation:breathe 4s ease-in-out infinite; --glow-pulse:var(--glow)"],
  [".b-lnk.em-grow", "animation:grow 3s ease-in-out infinite; transform-origin:center"]
].map(Object.freeze));
// RL-144 A51:商品說明 / 活動文案(標題下一段小字)。只在頁上有帶 description 的 shop / event 顆時輸出;BASE_RULES 守恆不動。
const DESCRIPTION_RULES = Object.freeze([
  [".pd .ds", "margin:2px 0 0; font-size:12.5px; line-height:1.45; opacity:.78; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden"],
  [".b-event .t .ds", "display:block; font-size:12.5px; line-height:1.45; opacity:.78; margin-top:2px"]
].map(Object.freeze));
const GROW_KEYFRAME = "@keyframes grow{0%,100%{transform:scale(1)}50%{transform:scale(1.03)}}";
const LINK_MEDIA_RULES = Object.freeze([
  [".b-lnk .ic", "display:inline-flex; width:20px; height:20px; vertical-align:-5px; margin-right:8px; flex:0 0 auto"],
  [".b-lnk .ic svg, .b-lnk .ic img", "width:100%; height:100%; display:block; object-fit:cover; border-radius:4px"],
  [".b-lnk .th", "width:56px; height:56px; flex:0 0 auto; border-radius:calc(var(--btn-r,14px) - 4px); object-fit:cover; display:block"]
].map(Object.freeze));
// 317 第 10 項:FAQ——summary 44px 高(觸控目標)、marker 換成 + / −(details[open] 切換)、答案 14px。只在頁上有 faq 顆時輸出。
const FAQ_RULES = Object.freeze([
  [".b-faq", "display:flex; flex-direction:column; gap:6px"],
  [".b-faq details", "border-radius:var(--btn-r,14px); background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.11); padding:0 14px"],
  [".b-faq summary", "min-height:44px; display:flex; align-items:center; gap:10px; cursor:pointer; font-size:14px; font-weight:650; list-style:none"],
  [".b-faq summary::-webkit-details-marker", "display:none"],
  [".b-faq summary::before", 'content:"+"; width:18px; text-align:center; color:var(--glow); font-weight:800; flex:0 0 auto'],
  [".b-faq details[open] summary::before", 'content:"\\2212"'],
  [".b-faq p", "margin:0 0 12px 28px; font-size:13.5px; line-height:1.55; opacity:.85"]
].map(Object.freeze));
// page/aib-oneshot-0926:link_group——summary 同 FAQ 觸控目標;body 連結列像 soft link 卡。只在頁上有 link_group 時輸出。
const LINK_GROUP_RULES = Object.freeze([
  [".b-link-group details", "border-radius:var(--btn-r,14px); background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.11); padding:0 14px"],
  [".b-link-group summary", "min-height:44px; display:flex; align-items:center; gap:10px; cursor:pointer; font-size:14px; font-weight:650; list-style:none"],
  [".b-link-group summary::-webkit-details-marker", "display:none"],
  [".b-link-group summary::before", 'content:"+"; width:18px; text-align:center; color:var(--glow); font-weight:800; flex:0 0 auto'],
  [".b-link-group details[open] summary::before", 'content:"\\2212"'],
  [".b-link-group .lg-list", "list-style:none; margin:0 0 12px; padding:0 0 4px; display:flex; flex-direction:column; gap:6px"],
  [".b-link-group .lg-list a", "display:flex; align-items:center; min-height:40px; padding:8px 12px; border-radius:calc(var(--btn-r,14px) - 4px); background:rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.10); font-size:13.5px; font-weight:600; text-decoration:none; color:inherit"]
].map(Object.freeze));
// 317 第 9 項:見證卡——半透明卡面、左側 3px --glow 邊、引號 ::before;頭像 40px 圓;星字 --glow。只在頁上有 testimonial 顆時輸出。
const QUOTE_RULES = Object.freeze([
  [".b-quote", "margin:0; padding:14px 16px 12px; border-radius:var(--btn-r,14px); background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.11); border-left:3px solid var(--glow); position:relative"],
  [".b-quote blockquote", 'margin:0 0 10px; font-size:14px; line-height:1.6; font-style:italic'],
  [".b-quote blockquote::before", 'content:"\\201C"; color:var(--glow); font-size:22px; line-height:0; margin-right:4px; vertical-align:-6px'],
  [".b-quote figcaption", "display:flex; align-items:center; gap:8px; font-size:12.5px; flex-wrap:wrap"],
  [".b-quote .av", "width:40px; height:40px; border-radius:50%; object-fit:cover; flex:0 0 auto"],
  [".b-quote figcaption small", "opacity:.6"],
  [".b-quote .st", "color:var(--glow); letter-spacing:1px; margin-left:auto; font-size:12px"]
].map(Object.freeze));
// 317 第 11 項:倒數——大數字 40px --glow、標題小字、單位字。只在頁上有 countdown 顆時輸出。
const COUNTDOWN_RULES = Object.freeze([
  [".b-count", "display:flex; flex-direction:column; align-items:center; gap:2px; padding:14px 16px; border-radius:var(--btn-r,14px); background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.11); text-align:center"],
  [".b-count h3", "margin:0; font-size:13px; font-weight:650; opacity:.85"],
  [".b-count .n", "font-size:40px; line-height:1.1; font-weight:800; color:var(--glow); letter-spacing:-.02em"],
  [".b-count .u", "font-size:12.5px; opacity:.75"],
  [".b-count .u.done", "font-size:14px; font-weight:650; opacity:.95"]
].map(Object.freeze));
// 317 第 13 項:預約卡——行事曆 glyph 28px、標題 + 小字、右側「預約」膠囊。只在頁上有 booking 顆時輸出。
const BOOK_RULES = Object.freeze([
  [".b-book", "display:flex; align-items:center; gap:12px; padding:12px 14px; border-radius:var(--btn-r,14px); background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.11); text-decoration:none; color:inherit; min-height:44px"],
  [".b-book .ic", "width:28px; height:28px; flex:0 0 auto; color:var(--glow)"],
  [".b-book .ic svg", "width:100%; height:100%; display:block"],
  [".b-book .t", "display:flex; flex-direction:column; gap:2px; flex:1 1 auto; min-width:0"],
  [".b-book .t b", "font-size:14px; font-weight:700"],
  [".b-book .t small", "font-size:11.5px; opacity:.7"],
  [".b-book .ar", "flex:0 0 auto; font-size:12px; font-weight:700; padding:6px 12px; border-radius:999px; background:var(--grad); color:#fff"]
].map(Object.freeze));
// 317 第 12 項:檔案下載卡——下載 glyph 24px、標題 + 小字(PDF · 大小 · 備註)。只在頁上有 file 顆時輸出。
const FILE_RULES = Object.freeze([
  [".b-file", "display:flex; align-items:center; gap:12px; padding:12px 14px; border-radius:var(--btn-r,14px); background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.11); text-decoration:none; color:inherit; min-height:44px"],
  [".b-file .ic", "width:24px; height:24px; flex:0 0 auto; color:var(--glow)"],
  [".b-file .ic svg", "width:100%; height:100%; display:block"],
  [".b-file .t", "display:flex; flex-direction:column; gap:2px; min-width:0"],
  [".b-file .t b", "font-size:14px; font-weight:700"],
  [".b-file .t small", "font-size:11.5px; opacity:.7"]
].map(Object.freeze));
// 317 第 14 項:QR——白底卡、--btn-r 圓角、最大 200px、說明小字。只在頁上有 qr 顆時輸出。
// 317 第 20 項 會員限定:公開頁的鎖卡(通關碼表單),只在頁上有 members 顆且店主設了通關碼時輸出(renderBioCss members:true);沒有的頁 CSS 一個位元組不變
const MEMBERS_RULES = Object.freeze([
  [".b-members", "margin:0; display:flex; flex-direction:column; gap:8px; padding:14px; border-radius:var(--btn-r,14px); background:rgba(255,255,255,.07); border:1px dashed rgba(255,255,255,.28)"],
  [".b-members p", "margin:0; font-size:13px; opacity:.85"],
  [".b-members label", "display:flex; flex-direction:column; gap:6px; font-size:12px; opacity:.8"],
  [".b-members input", "min-height:44px; padding:0 12px; border-radius:calc(var(--btn-r,14px) - 4px); border:1px solid rgba(255,255,255,.28); background:rgba(0,0,0,.25); color:inherit; font:inherit"],
  [".b-members button", "min-height:44px; border-radius:var(--btn-r,14px); border:0; background:var(--grad); color:#fff; font:700 14px/1 inherit; cursor:pointer"]
].map(Object.freeze));
// 317 第 4 項:相簿——carousel = 橫向 scroll-snap(零 JS,手機拇指滑、桌機拖捲),grid = 2 欄 / ≥3 張 3 欄;只在頁上有 gallery 顆時輸出。
const GALLERY_RULES = Object.freeze([
  [".b-gal", "margin:0"],
  [".b-gal ul", "list-style:none; margin:0; padding:0; display:grid; gap:8px"],
  [".b-gal li", "margin:0; min-width:0"],
  [".b-gal img", "display:block; width:100%; height:100%; object-fit:cover; border-radius:var(--btn-r,14px)"],
  [".b-gal a", "display:block"],
  ['.b-gal[data-style="carousel"] ul', "display:flex; overflow-x:auto; scroll-snap-type:x mandatory; -webkit-overflow-scrolling:touch; scrollbar-width:none; gap:8px; padding-bottom:2px"],
  ['.b-gal[data-style="carousel"] ul::-webkit-scrollbar', "display:none"],
  ['.b-gal[data-style="carousel"] li', "flex:0 0 84%; scroll-snap-align:start; aspect-ratio:4/3"],
  ['.b-gal[data-style="grid"] ul', "grid-template-columns:repeat(2,1fr)"],
  ['.b-gal[data-style="grid"][data-cols="3"] ul', "grid-template-columns:repeat(3,1fr)"],
  ['.b-gal[data-style="grid"] li', "aspect-ratio:1"]
].map(Object.freeze));
const QR_RULES = Object.freeze([
  [".b-qr", "margin:0; display:flex; flex-direction:column; align-items:center; gap:8px; padding:14px; border-radius:var(--btn-r,14px); background:#fff; border:1px solid rgba(0,0,0,.08)"],
  [".b-qr svg", "width:100%; max-width:200px; height:auto; display:block"],
  [".b-qr figcaption", "font-size:12px; color:#1d1d1f; opacity:.7"]
].map(Object.freeze));
// 317 第 18 項:表單——標題、每欄 label + 輸入框(text / email / tel / textarea / checkbox)、送出膠囊;蜜罐欄 .w 用 CSS 藏(不是 hidden 屬性,機器人會填)。只在頁上有 form 顆時輸出。
const FORM_RULES = Object.freeze([
  [".b-form", "display:flex; flex-direction:column; gap:10px; padding:14px; border-radius:var(--btn-r,14px); background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.11)"],
  [".b-form h3", "margin:0; font-size:15px; font-weight:800"],
  [".b-form label", "display:flex; flex-direction:column; gap:4px; font-size:12.5px; opacity:.9"],
  [".b-form label.ck", "flex-direction:row; align-items:center; gap:8px"],
  [".b-form input:not([type=checkbox]),.b-form textarea", "font:inherit; font-size:14px; padding:10px 12px; border-radius:10px; border:1px solid rgba(255,255,255,.18); background:rgba(0,0,0,.18); color:inherit; min-height:44px"],
  [".b-form textarea", "min-height:88px; resize:vertical"],
  [".b-form .w", "position:absolute; left:-9999px; width:1px; height:1px; overflow:hidden"],
  [".b-form button", "font:inherit; font-size:14px; font-weight:700; min-height:44px; padding:10px 16px; border:0; border-radius:999px; background:var(--grad); color:#fff; cursor:pointer"]
].map(Object.freeze));
const PLACE_RULES = Object.freeze([
  [".b-place", "display:flex; flex-direction:column; gap:10px; background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.11); border-radius:var(--btn-r,14px); padding:12px; position:relative"],
  [".b-place .map", "display:block; height:120px; border-radius:calc(var(--btn-r,14px) - 4px); overflow:hidden; background:var(--grad); position:relative"],
  [".b-place .map img", "width:100%; height:100%; object-fit:cover; display:block"],
  [".b-place .pi", "display:flex; flex-direction:column; gap:2px"],
  [".b-place .pi b", "font-size:15px; font-weight:800"],
  [".b-place .ad", "font-size:12px; opacity:.7"],
  [".b-place .rt", "font-size:12.5px; font-weight:650; display:flex; gap:8px; align-items:baseline; flex-wrap:wrap"],
  [".b-place .rt small", "font-size:9.5px; opacity:.55; font-weight:400"],
  [".b-place .hr", "list-style:none; margin:0; padding:0; font-size:11.5px; opacity:.8; display:grid; grid-template-columns:1fr 1fr; gap:2px 12px"],
  [".b-place .hr li", "display:flex; justify-content:space-between; gap:8px"],
  [".b-place .ac", "display:flex; gap:8px"],
  [".b-place .ac .c", "flex:1; text-align:center; font-size:12px; font-weight:650; border-radius:var(--btn-r,12px); padding:10px 4px; min-height:44px; display:flex; align-items:center; justify-content:center; background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.16); color:inherit; text-decoration:none"],
  [".b-place .ac .rv", "color:var(--glow)"],
  [".b-place .rv", "list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:8px"],
  [".b-place .rv li", "font-size:12px; display:grid; grid-template-columns:auto auto 1fr; gap:2px 8px; align-items:baseline"],
  [".b-place .rv li p", "grid-column:1 / -1; margin:0; opacity:.85; line-height:1.5"],
  [".b-place .rv .st", "color:var(--glow); letter-spacing:1px"],
  [".b-place .rv time", "opacity:.55; font-size:10.5px; justify-self:end"],
  [".b-place .rv-expired", "font-size:11px; opacity:.6; margin:0; outline:1px dashed currentColor; outline-offset:2px; padding:4px 6px"],
  [".b-place .pb", "font-size:9px; opacity:.5; align-self:flex-end"]
].map(Object.freeze));
// 316 v1.1 §四 3 封面:只在頁上有封面時輸出(renderBioCss cover:true)——BASE_RULES 與 mock 守恆不動、沒封面的頁 CSS 一個位元組不變(S6);
//   aspect-ratio / max-height 是每套的表值,由渲染器寫在 .b-cover 的 style 上,這裡只放形狀規則。頭像壓在封面下緣(-39px = 78/2)。
const COVER_RULES = Object.freeze([
  [".b-cover", "width:100%; overflow:hidden; border-radius:var(--btn-r,14px); margin:0 0 4px"],
  [".b-cover img", "width:100%; height:100%; object-fit:cover; display:block"],
  [".b-cover + .b-ava", "margin-top:-39px; position:relative; z-index:1"]
].map(Object.freeze));
const BASE_RULES = Object.freeze([
  [".b-ava", "width:78px; height:78px; border-radius:50%; margin:0 auto; background:var(--grad); padding:3px; flex:0 0 auto"],
  [".b-ava .i", "width:100%; height:100%; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:27px; font-weight:800; background:#141B36; color:#BFC7EF"],
  [".b-id", "text-align:center"],
  [".b-id .nm", "font-size:20px; font-weight:800"],
  [".b-id .bi", "font-size:12.5px; opacity:.66; margin-top:4px; line-height:1.6"],
  [".b-cta", "display:flex; gap:8px"],
  [".b-cta .c", "flex:1; text-align:center; font-size:12px; font-weight:650; border-radius:var(--btn-r,12px); padding:10px 4px 8px; min-height:44px; display:flex; flex-direction:column; gap:1px; justify-content:center; cursor:pointer; background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.16)"],
  [".b-cta .c small", "font-size:9.5px; opacity:.55; font-weight:400"],
  [".b-cta .c.soon", "opacity:.42"],
  [".b-lnk", "display:flex; align-items:center; justify-content:space-between; gap:10px; border-radius:var(--btn-r,14px); padding:13px 15px; font-size:13.5px; font-weight:600; min-height:44px; cursor:pointer; background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.11)"],
  [".b-lnk small", "display:block; font-size:10.5px; opacity:.55; font-weight:400; margin-top:1px"],
  // 連結卡的箭頭。原本是 ASCII "->" 兩個字元用等寬字排出來的,在一排真圖示旁邊
  // 粗細不勻、像沒做完。改成與社群圖示同一家族:同 24 網格、同吃 currentColor,
  // 所以每個主題的箭頭自動是該主題的強調色。
  [".b-lnk .ar", "color:var(--glow); display:flex; align-items:center; transition:transform .25s cubic-bezier(.2,.8,.3,1)"],
  [".b-lnk .ar svg", "width:16px; height:16px; fill:none; stroke:currentColor; stroke-width:1.9; stroke-linecap:round; stroke-linejoin:round; display:block"],
  [".b-lnk:hover .ar", "transform:translateX(2px)"],
  [".b-img", "border-radius:14px; height:96px; border:1px solid rgba(255,255,255,.1); background:linear-gradient(120deg,#1A2242,#101736 55%,#17323C); display:flex; align-items:flex-end; padding:9px 11px; font-size:10px; opacity:.85; color:#8790B6"],
  [".b-txt", "font-size:12px; opacity:.72; line-height:1.7"],
  [".b-ft", "margin-top:auto; padding-top:12px; border-top:1px solid rgba(255,255,255,.1); text-align:center; font-size:9.5px; opacity:.5; display:flex; flex-direction:column; gap:2px"],
  [".b-ava", "background:none; padding:3px; position:relative"],
  [".b-ava::before", 'content:""; position:absolute; inset:0; border-radius:50%; background:conic-gradient(from 0deg,#8B7CFF,#43D9C0,#a18fff,#8B7CFF); animation:spin 6s linear infinite, breathe 4.5s ease-in-out infinite'],
  [".b-ava .i", "position:relative; z-index:1"],
  [".b-cta .c.br", "animation:breathe 4s ease-in-out infinite"],
  [".b-cta .c.soon", "position:relative; overflow:hidden"],
  [".b-cta .c.soon::after", 'content:""; position:absolute; inset:0; background:linear-gradient(110deg,transparent 30%,rgba(255,255,255,.2) 50%,transparent 70%); animation:shimmer 2.8s ease-in-out infinite'],
  [".b-status", "display:flex; justify-content:center"],
  [".b-status .pill", "display:flex; align-items:center; gap:7px; font-size:10.5px; color:#9fe8c8; background:rgba(62,207,142,.1); border:1px solid rgba(62,207,142,.3); padding:4px 12px; border-radius:999px"],
  [".dot", "position:relative; width:7px; height:7px; border-radius:50%; background:#3ECF8E"],
  [".dot::after", 'content:""; position:absolute; inset:0; border-radius:50%; background:#3ECF8E; animation:pulse 2.2s ease-out infinite'],
  [".vbadge", "display:inline-flex; width:15px; height:15px; border-radius:50%; background:var(--grad); align-items:center; justify-content:center; vertical-align:1px; margin-left:5px"],
  [".vbadge svg", "width:9px; height:9px; stroke:#fff; stroke-width:3.2; fill:none"],
  [".b-soc", "display:flex; justify-content:center; gap:10px"],
  [".b-soc .s", "width:34px; height:34px; border-radius:50%; display:flex; align-items:center; justify-content:center; background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.14); cursor:pointer; transition:transform .25s"],
  [".b-soc .s", "animation:softglow 4.6s ease-in-out infinite"],
  [".b-soc .s:nth-child(2)", "animation-delay:.34s"],
  [".b-soc .s:nth-child(3)", "animation-delay:.68s"],
  [".b-soc .s:nth-child(4)", "animation-delay:1.02s"],
  [".b-soc .s:nth-child(5)", "animation-delay:1.36s"],
  [".b-soc .s:nth-child(6)", "animation-delay:1.7s"],
  [".b-soc .s:nth-child(7)", "animation-delay:2.04s"],
  [".b-soc .s:nth-child(8)", "animation-delay:2.38s"],
  [".b-soc .s:nth-child(9)", "animation-delay:2.72s"],
  [".b-soc .s:hover", "transform:translateY(-3px)"],
  [".b-soc svg", "width:16px; height:16px; stroke:currentColor; fill:none; stroke-width:1.8"],
  [".b-music", "display:flex; align-items:center; gap:11px; background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.11); border-radius:var(--btn-r,14px); padding:10px 12px"],
  [".b-music .art", "width:46px; height:46px; border-radius:10px; background:linear-gradient(135deg,#7c6cff,#2b2352 70%); flex:0 0 auto; display:flex; align-items:flex-end; justify-content:center; gap:2.5px; padding-bottom:8px"],
  [".b-music .art i", "display:block; width:3px; height:15px; background:rgba(255,255,255,.85); border-radius:2px; transform-origin:bottom; animation:eqbar 1.1s ease-in-out infinite"],
  [".b-music .art i:nth-child(2)", "animation-delay:.2s; height:19px"],
  [".b-music .art i:nth-child(3)", "animation-delay:.45s; height:11px"],
  [".b-music .m", "flex:1; min-width:0"],
  [".b-music .m b", "display:block; font-size:12px"],
  [".b-music .m span", "font-size:10px; opacity:.55"],
  [".b-music .bar", "margin-top:6px; height:3px; border-radius:2px; background:rgba(255,255,255,.15); overflow:hidden"],
  [".b-music .bar i", "display:block; height:100%; border-radius:2px; background:var(--grad); animation:barmove 9s linear infinite"],
  [".b-music .pl", "width:30px; height:30px; border-radius:50%; background:rgba(255,255,255,.12); display:flex; align-items:center; justify-content:center; flex:0 0 auto"],
  [".b-music .pl i", "width:0; height:0; border-style:solid; border-width:6px 0 6px 10px; border-color:transparent transparent transparent #fff; margin-left:2px; display:block"],
  [".b-vid", "position:relative; border-radius:14px; overflow:hidden; aspect-ratio:16/9; background:linear-gradient(120deg,#232a52,#141b36 55%,#1b3a42); border:1px solid rgba(255,255,255,.1); display:flex; align-items:center; justify-content:center"],
  [".b-vid .pl", "width:44px; height:44px; border-radius:50%; background:rgba(255,255,255,.14); border:1px solid rgba(255,255,255,.3); display:flex; align-items:center; justify-content:center; animation:breathe 3.5s ease-in-out infinite"],
  [".b-vid .pl i", "width:0; height:0; border-style:solid; border-width:8px 0 8px 13px; border-color:transparent transparent transparent #fff; margin-left:3px; display:block"],
  [".b-vid .du", "position:absolute; right:9px; bottom:8px; font-size:9px; background:rgba(0,0,0,.55); padding:2px 7px; border-radius:999px; color:#e8e8f0"],
  [".b-shop", "display:flex; gap:9px"],
  [".b-shop .pd", "flex:1; background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.11); border-radius:13px; padding:8px; display:flex; flex-direction:column; gap:6px"],
  [".b-shop .ph2", "aspect-ratio:1; border-radius:9px; background:linear-gradient(135deg,#2a3158,#17203f)"],
  [".b-shop .pd:last-child .ph2", "background:linear-gradient(135deg,#1d4038,#122a24)"],
  [".b-shop b", "font-size:10.5px; font-weight:650"],
  [".b-shop .pr", "display:flex; align-items:center; justify-content:space-between; font-size:10px"],
  [".b-shop .pr span", "color:#43D9C0; font-family:var(--mono)"],
  [".b-shop .pr i", "font-style:normal; font-size:9px; background:rgba(139,124,255,.2); border:1px solid rgba(139,124,255,.45); color:#c9c2ff; padding:2px 8px; border-radius:999px"],
  [".b-mail", "display:flex; gap:7px"],
  [".b-mail input", "flex:1; min-width:0; background:rgba(0,0,0,.35); border:1px solid rgba(255,255,255,.15); border-radius:11px; height:var(--tap,44px); padding:0 12px; color:#fff; font-size:11px; outline:none; font-family:var(--font)"],
  [".b-mail input::placeholder", "color:#6a7194"],
  [".b-mail .go", "height:var(--tap,44px); padding:0 14px; border-radius:11px; background:var(--grad); color:#fff; font-size:11px; font-weight:700; display:flex; align-items:center; border:none; cursor:pointer"],
  [".b-event", "display:flex; align-items:center; gap:11px; background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.11); border-radius:var(--btn-r,14px); padding:10px 12px"],
  [".b-event .d", "flex:0 0 auto; width:44px; text-align:center; background:rgba(139,124,255,.15); border:1px solid rgba(139,124,255,.4); border-radius:10px; padding:5px 0"],
  [".b-event .d b", "display:block; font-size:15px; color:#c9c2ff; font-family:var(--mono)"],
  [".b-event .d span", "font-size:8.5px; color:#8f97c2"],
  [".b-event .t", "flex:1"],
  [".b-event .t b", "display:block; font-size:12px"],
  [".b-event .t span", "font-size:10px; opacity:.55"],
  [".b-event .ar", "color:var(--glow); display:flex; align-items:center"],
  [".b-event .ar svg", "width:16px; height:16px; fill:none; stroke:currentColor; stroke-width:1.9; stroke-linecap:round; stroke-linejoin:round; display:block"],
  [".b-tip", "display:flex; align-items:center; justify-content:center; gap:8px; border-radius:var(--btn-r,14px); padding:12px; font-size:12.5px; font-weight:650; background:rgba(232,178,62,.12); border:1px solid rgba(232,178,62,.4); color:#f2cf8a; cursor:pointer"],
  [".b-tip svg", "width:15px; height:15px; stroke:#f2cf8a; fill:none; stroke-width:2; animation:hb 1.8s ease-in-out infinite"]
].map(Object.freeze));

// Per-theme overrides, mock order preserved. themes lists which skins a rule belongs to;
// grouped :is(...) rules in the mock become multi-theme entries here.
const THEME_RULES = Object.freeze([
  [["mono"], ".b-ava .i", "background:#fff; color:#5a5a5f; box-shadow:inset 0 0 0 1px #e5e5ea"],
  [["dawn"], ".b-ava .i", "background:#3a2033; color:#F3D9CB"],
  [["forest"], ".b-ava .i", "background:#0e2a20; color:#BFE5CE"],
  [["rosegold"], ".b-ava .i", "background:#fff; color:#b4756a; box-shadow:inset 0 0 0 1px #eccfc4"],
  [["starlight"], ".b-ava .i", "background:#fff; color:#8a7a5f; box-shadow:inset 0 0 0 1px #e5dcc9"],
  [["pop"], ".b-ava .i", "background:rgba(255,255,255,.22); color:#fff"],
  [["ocean"], ".b-ava .i", "background:#083248; color:#9fd8e8"],
  [["rosegold", "starlight"], ".b-lnk", "background:rgba(255,255,255,.74); border-color:rgba(0,0,0,.07); box-shadow:0 1px 4px rgba(0,0,0,.05)"],
  [["rosegold", "starlight"], ".b-cta .c", "background:rgba(255,255,255,.74); border-color:rgba(0,0,0,.08); box-shadow:0 1px 4px rgba(0,0,0,.05)"],
  [["rosegold"], ".b-lnk .ar", "color:#c06a55"],
  [["starlight"], ".b-lnk .ar", "color:#8a7a5f"],
  [["rosegold", "starlight"], ".b-ft", "border-top-color:rgba(0,0,0,.08)"],
  [["pop"], ".b-lnk", "background:rgba(255,255,255,.18); border-color:rgba(255,255,255,.32)"],
  [["pop"], ".b-cta .c", "background:rgba(255,255,255,.18); border-color:rgba(255,255,255,.34)"],
  [["ocean"], ".b-lnk", "background:rgba(255,255,255,.09); border-color:rgba(255,255,255,.14)"],
  [["ocean"], ".b-cta .c", "background:rgba(255,255,255,.09); border-color:rgba(255,255,255,.16)"],
  [["rosegold"], ".b-img", "background:linear-gradient(120deg,#eccabb,#e3b3a0); color:#7c5044; border-color:rgba(0,0,0,.06)"],
  [["starlight"], ".b-img", "background:linear-gradient(120deg,#e9dfcc,#dfd2b8); color:#7c7057; border-color:rgba(0,0,0,.06)"],
  [["ocean"], ".b-img", "background:linear-gradient(120deg,#0a3d56,#0d5a74); color:#8fc8da; border-color:rgba(255,255,255,.12)"],
  [["starlight"], ".b-mail input", "background:rgba(255,255,255,.72); color:#3c352c; border-color:rgba(0,0,0,.1)"],
  [["rosegold"], ".b-status .pill", "color:#9a5b4c; background:rgba(255,255,255,.6); border-color:rgba(0,0,0,.08)"],
  [["starlight", "mono"], ".b-status .pill", "color:#177a4e; background:rgba(255,255,255,.66); border-color:rgba(23,122,78,.28)"],
  [["mono", "rosegold", "starlight"], ".b-music, .b-event, .b-shop .pd", "background:rgba(255,255,255,.75); border-color:rgba(0,0,0,.07); box-shadow:0 1px 4px rgba(0,0,0,.05)"],
  [["mono", "rosegold", "starlight"], ".b-music .bar", "background:rgba(0,0,0,.1)"],
  [["mono", "rosegold", "starlight"], ".b-music .pl", "background:rgba(0,0,0,.07)"],
  [["mono", "rosegold", "starlight"], ".b-music .pl i", "border-color:transparent transparent transparent #6b5348"],
  [["mono", "rosegold", "starlight"], ".b-event .d", "background:rgba(0,0,0,.05); border-color:rgba(0,0,0,.12)"],
  [["mono", "rosegold", "starlight"], ".b-event .d b", "color:inherit"],
  [["mono", "rosegold", "starlight"], ".b-event .d span", "color:rgba(0,0,0,.45)"],
  [["rosegold"], ".b-event .ar, .b-shop .pr span", "color:#c06a55"],
  [["starlight"], ".b-event .ar, .b-shop .pr span", "color:#8a7a5f"],
  [["mono"], ".b-event .ar, .b-shop .pr span", "color:#7c6cff"],
  [["rosegold", "starlight", "mono"], ".b-shop .pr i", "background:rgba(0,0,0,.05); border-color:rgba(0,0,0,.15); color:inherit"],
  [["rosegold", "starlight", "mono"], ".b-tip", "background:rgba(255,255,255,.6); border-color:rgba(176,126,20,.35); color:#9a6d0a"],
  [["rosegold", "starlight", "mono"], ".b-tip svg", "stroke:#b07e14"],
  [["rosegold", "mono"], ".b-mail input", "background:rgba(255,255,255,.72); color:inherit; border-color:rgba(0,0,0,.1)"],
  [["pop"], ".b-music, .b-event, .b-shop .pd", "background:rgba(255,255,255,.16); border-color:rgba(255,255,255,.3)"],
  [["pop"], ".b-event .d", "background:rgba(255,255,255,.18); border-color:rgba(255,255,255,.35)"],
  [["pop"], ".b-event .d b", "color:#fff"],
  [["pop"], ".b-event .d span", "color:rgba(255,255,255,.7)"],
  [["pop"], ".b-event .ar, .b-shop .pr span", "color:#fff"],
  [["pop"], ".b-shop .pr i", "background:rgba(255,255,255,.2); border-color:rgba(255,255,255,.4); color:#fff"],
  [["pop"], ".b-soc .s", "background:rgba(255,255,255,.18); border-color:rgba(255,255,255,.32)"],
  [["pop"], ".b-tip", "background:rgba(0,0,0,.25); border-color:rgba(255,255,255,.32); color:#fff"],
  [["pop"], ".b-tip svg", "stroke:#fff"],
  [["pop"], ".b-ft", "border-top-color:rgba(255,255,255,.25)"],
  [["mono"], ".b-cta .c", "background:#fff; border-color:#e5e5ea; box-shadow:0 1px 4px rgba(0,0,0,.05)"],
  [["mono"], ".b-lnk", "background:#fff; border-color:#e5e5ea; box-shadow:0 1px 4px rgba(0,0,0,.05)"],
  [["mono"], ".b-lnk .ar", "color:#7c6cff"],
  [["mono"], ".b-img", "background:linear-gradient(120deg,#e8e8ef,#f2f2f7 60%,#e2eeea); color:#86868b; border-color:#e5e5ea"],
  [["dawn"], ".b-img", "background:linear-gradient(120deg,#54304a,#6d3a42); color:#e8c8b8; border-color:rgba(255,255,255,.14)"],
  [["forest"], ".b-img", "background:linear-gradient(120deg,#16382b,#1e4a37); color:#a8d4bb; border-color:rgba(255,255,255,.12)"],
  [["mono"], ".b-ft", "border-top-color:#e5e5ea"],
  [["mono"], ".b-soc .s", "background:#fff; border-color:#e5e5ea"]
].map(Object.freeze));

// 275(a) v3(09-17 艦長派工,the maintainer 原話「把原來的模板 1:1 放進來可以點選套版」):套版頁的卡要**逐像素**
// 是原稿主題頁那張卡。卡的殼(.phone 手機框、.tcard 縮小版)照 mock 逐字抄,smoke 對 v41 逐字守恆。
// ★ 只給後台套版頁用;客戶 bio 頁不畫手機框,renderBioCss 一個位元組都不動。
const CARD_RULES = Object.freeze([
  [".phone", "width:300px; border-radius:44px; padding:10px; background:#111; border:1px solid rgba(255,255,255,.16); box-shadow:0 40px 90px rgba(0,0,0,.55), 0 0 120px rgba(124,108,255,.18); margin:0 auto; text-align:left; position:relative;"],
  [".phone .scr", "border-radius:35px; overflow:hidden; min-height:560px; display:flex; flex-direction:column; gap:14px; padding:34px 18px 16px; position:relative"],
  [".tcard", "text-align:center"],
  [".tcard .phone", "width:100%; max-width:250px; padding:8px; border-radius:34px; box-shadow:0 20px 50px rgba(0,0,0,.16)"],
  [".tcard .phone .scr", "height:472px; min-height:0; padding:26px 14px 14px; border-radius:27px; gap:10px; overflow-y:auto; scrollbar-width:none; overscroll-behavior:contain; -webkit-overflow-scrolling:touch;"],
  [".tcard .phone .scr::-webkit-scrollbar", "display:none"],
  [".tcard .phone .scr > *", "flex:0 0 auto"],
  [".tcard .phone .b-ava", "width:56px; height:56px"],
  [".tcard .phone .b-ava .i", "font-size:19px"],
  [".tcard .phone .b-id .nm", "font-size:15px"],
  [".tcard .phone .b-lnk", "padding:10px 12px; font-size:11.5px; min-height:38px"],
  [".tcard .phone .b-cta .c", "min-height:36px; font-size:10px; padding:7px 3px 5px"],
  [".tcard .hh4", "margin-top:18px; font-size:16px; font-weight:700"],
  [".tcard p", "font-size:12.5px; color:var(--mute)"]
].map(Object.freeze));

const ANIM_PAUSED_RULE = ".anim-paused *{animation-play-state:paused !important}";
const REDUCED_MOTION_RULE = "@media (prefers-reduced-motion: reduce){*{transition:none !important; animation:none !important}}";

// ---- 287-B 包二 (d) 背景(RL-080):八套預設底色 + 自訂兩色漸層(六向)---------------------------------------------------
// appearance.background 可選欄:{preset:"<theme>"} 或 {custom:{c1,c2,dir}};缺席 = 當前 theme 自己的 screen(今天的行為,零遷移)。
//   preset 只換底不換套(color 與版面仍是當前 theme);custom 的字色由亮度判(inkFor),三處 render 與尺都呼叫它,不各自算。
const BACKGROUND_DIRS = Object.freeze(["to bottom", "to top", "to right", "to left", "135deg", "45deg"]);

// 影片封面層:只在頁上有 video 時輸出(否則 editable-13 C2 公開 CSS 會漂)。
const VIDEO_POSTER_RULES = Object.freeze([
  [".b-vid .poster", "position:absolute; inset:0; width:100%; height:100%; object-fit:cover; display:block"],
  [".b-vid .ph", "position:absolute; inset:0; background:linear-gradient(120deg,#2a335f,#141b36 55%,#1b3a42)"],
  [".b-vid .pl", "position:relative; z-index:1"],
  [".b-vid .du", "z-index:1"]
].map(Object.freeze));
const HEX_RE = /^#[0-9a-f]{6}$/;
const INK_DARK = "#1d1d1f";
const INK_LIGHT = "#f2f4ff";
function validateBackground(background) {
  if (background === undefined || background === null) return [];
  const bad = ["page_appearance_background_invalid"];
  if (!background || typeof background !== "object" || Array.isArray(background)) return bad;
  const keys = Object.keys(background);
  if (keys.length !== 1) return bad;
  if (keys[0] === "preset") return typeof background.preset === "string" && THEMES[background.preset] ? [] : bad;
  if (keys[0] === "custom") {
    const c = background.custom;
    if (!c || typeof c !== "object" || Array.isArray(c) || Object.keys(c).length !== 3) return bad;
    return HEX_RE.test(String(c.c1)) && HEX_RE.test(String(c.c2)) && BACKGROUND_DIRS.includes(c.dir) ? [] : bad;
  }
  return bad;
}
// WCAG 相對亮度(sRGB 線性化),兩色平均 ≥ .5 ⇒ 深字;< .5 ⇒ 淺字。
function relativeLuminance(hex) {
  const n = parseInt(String(hex).slice(1), 16);
  const chan = (v) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  return 0.2126 * chan((n >> 16) & 255) + 0.7152 * chan((n >> 8) & 255) + 0.0722 * chan(n & 255);
}
function inkFor(c1, c2, options = {}) {
  if (String(options.test_mutation || "") === "ink-always-light") return INK_LIGHT;   // 刀 ⇒ D1 必紅
  return (relativeLuminance(c1) + relativeLuminance(c2)) / 2 >= 0.5 ? INK_DARK : INK_LIGHT;
}
// 287-B 包三(RL-102 第三點):卡面材質與字色同一個亮度判準——淺底(inkFor 給深字)時 BASE_RULES 那組白色半透明卡面
// 會被底吃掉,換成深色半透明;深底回 null = 一個位元組都不多輸出(既有頁 CSS 逐位元組不變)。preset 走該 theme 自己的材質,不經這裡。
const SURFACE_DARK = Object.freeze({ bg: "rgba(0,0,0,.05)", border: "rgba(0,0,0,.1)" });
function surfaceFor(c1, c2, options = {}) {
  if (String(options.test_mutation || "") === "surface-always-light") return null;    // 刀 ⇒ D1 必紅(淺底卡面仍是白色半透明)
  return inkFor(c1, c2, options) === INK_DARK ? SURFACE_DARK : null;
}
// 卡面那幾條:與 BASE_RULES / THEME_RULES 同 scope 同特異性,排在 THEME_RULES 之後才會勝;只在 surfaceFor 非 null 時輸出。
const SURFACE_RULES = Object.freeze([
  // 289 五:.b-place 與它的動作鈕也是卡面(自訂淺底時同一套材質)
  [".b-lnk, .b-cta .c, .b-music, .b-event, .b-shop .pd, .b-soc .s, .b-place, .b-place .ac .c", "background:var(--surface-bg); border-color:var(--surface-bd); box-shadow:none"],
  [".b-music .bar, .b-music .pl", "background:var(--surface-bd)"],
  [".b-ft", "border-top-color:var(--surface-bd)"]
].map(Object.freeze));
function surfaceCss(background, scope, options = {}) {
  if (validateBackground(background).length || !background || !background.custom) return "";
  const surface = surfaceFor(background.custom.c1, background.custom.c2, options);
  if (!surface) return "";
  return [scope + "{--surface-bg:" + surface.bg + ";--surface-bd:" + surface.border + "}"]
    .concat(SURFACE_RULES.map(([selector, body]) => prefixSelectors(scope, selector) + "{" + body + "}")).join("\n");
}
// 底色那幾條 declaration(screen 裡不是 color 的那些)。
function screenBackgroundDecls(theme) {
  return String(THEMES[theme].screen).split(";").map((d) => d.trim()).filter((d) => d && !/^color\s*:/.test(d)).join(";");
}
function backgroundCss(background, scope, options = {}) {
  if (validateBackground(background).length || !background) return "";
  if (background.preset) return scope + "{" + screenBackgroundDecls(background.preset) + "}";
  const c = background.custom;
  return scope + "{background:linear-gradient(" + c.dir + ", " + c.c1 + ", " + c.c2 + ");color:" + inkFor(c.c1, c.c2, options) + "}";
}
// 287-B 包二:手機 ≤500 版心單欄、九宮 span 全部 auto 照順序排(與上面九條同一份,scope 由 renderBioCss 帶)。
function spanMobileRule(scope) { return "@media (max-width: 500px){" + scope + " .pg{grid-template-columns:1fr}" + scope + " .pg>[data-span]{grid-column:auto; grid-row:auto}}"; }

function fail(code, details = []) { const error = new Error(code); error.code = code; error.details = details; throw error; }

function validateScope(scope) {
  if (typeof scope !== "string" || !/^(?:body|\.[A-Za-z][\w-]*)$/.test(scope)) fail("PAGE_DESIGN_SCOPE_INVALID");
  return scope;
}

function prefixSelectors(scope, selector) {
  return selector.split(",").map((part) => scope + " " + part.trim()).join(", ");
}

// 區塊底色 CSS:有任何顆帶 background 才輸出;沒有 ⇒ 空字串(公開頁 CSS 逐位元組不變)。
//   image = background-image + size(cover|contain);video = 定位殼,<video class=pg-bbg-vid> 由 theme-renderer 注入。
const BLOCK_BG_VIDEO_RULES = Object.freeze([
  [".pg-bbg-vid", "position:absolute; inset:0; width:100%; height:100%; object-fit:cover; z-index:0; pointer-events:none"],
  ["[data-bbg=video] > *:not(.pg-bbg-vid)", "position:relative; z-index:1"]
].map(Object.freeze));
function cssBgUrl(url) {
  return 'url("' + String(url).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '")';
}
function blockBackgroundCss(blocks, options = {}) {
  if (String(options.test_mutation || "") === "ignore-block-background") return "";
  const lines = [];
  let hasVideo = false;
  for (const block of blocks || []) {
    if (!block || !block.block_id || block.background === undefined) continue;
    if (blockContract.backgroundErrors(block.background).length) continue;
    const scope = '[data-block-id="' + String(block.block_id).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"]';
    const bg = block.background;
    if (bg.solid) {
      const c = bg.solid;
      lines.push(scope + "{background:" + c + ";color:" + inkFor(c, c, options) + "}");
      const surface = surfaceFor(c, c, options);
      if (surface) {
        lines.push(scope + "{--surface-bg:" + surface.bg + ";--surface-bd:" + surface.border + "}");
        for (const rule of SURFACE_RULES) lines.push(prefixSelectors(scope, rule[0]) + "{" + rule[1] + "}");
      }
    } else if (bg.custom) {
      const piece = backgroundCss(bg, scope, options);
      if (piece) lines.push(piece);
      const surf = surfaceCss(bg, scope, options);
      if (surf) lines.push(surf);
    } else if (bg.image) {
      const fit = bg.image.fit === "contain" ? "contain" : "cover";
      lines.push(scope + "{position:relative;overflow:hidden;background-image:" + cssBgUrl(bg.image.url) + ";background-size:" + fit + ";background-position:center;background-repeat:no-repeat}");
    } else if (bg.video) {
      hasVideo = true;
      const fit = bg.video.fit === "contain" ? "contain" : "cover";
      lines.push(scope + "[data-bbg=video]{position:relative;overflow:hidden}");
      lines.push(scope + " .pg-bbg-vid{object-fit:" + fit + "}");
    }
  }
  if (hasVideo) {
    for (const [selector, body] of BLOCK_BG_VIDEO_RULES) lines.push(selector + "{" + body + "}");
    lines.push("@media (prefers-reduced-motion: reduce){.pg-bbg-vid{visibility:hidden}}");
  }
  return lines.join("\n");
}

// S5:區塊 theme_slot CSS——只吃主題 token（--glow / --grad / --btn-r / --paper）；
//   有任何顆帶白名單槽才輸出；沒有 ⇒ 空字串（公開頁 CSS 逐位元組不變）。
//   色不寫死 hex；換 theme_id 自然重染。刀 ignore-theme-slot ⇒ 空。
const THEME_SLOT_RULES = Object.freeze({
  card: "[data-theme-slot=card]{background:color-mix(in srgb, var(--glow) 12%, transparent);border:1px solid color-mix(in srgb, var(--glow) 28%, transparent);border-radius:var(--btn-r,14px);padding:12px 14px}",
  elevated: "[data-theme-slot=elevated]{background:color-mix(in srgb, var(--glow) 18%, transparent);border:1px solid color-mix(in srgb, var(--glow) 36%, transparent);border-radius:var(--btn-r,14px);padding:14px 16px;box-shadow:0 8px 24px color-mix(in srgb, var(--glow) 22%, transparent)}",
  accent_soft: "[data-theme-slot=accent_soft]{background:color-mix(in srgb, var(--glow) 22%, transparent);border:1px solid color-mix(in srgb, var(--glow) 48%, transparent);border-radius:var(--btn-r,14px);padding:12px 14px}",
  inverse: "[data-theme-slot=inverse]{background:var(--grad);border:0;border-radius:var(--btn-r,14px);padding:12px 14px;color:var(--paper)}"
});
function themeSlotCss(blocks, options = {}) {
  if (String(options.test_mutation || "") === "ignore-theme-slot") return "";
  const used = new Set();
  for (const block of blocks || []) {
    if (!block) continue;
    const slot = block.theme_slot;
    if (slot === undefined || slot === null || slot === "") continue;
    const key = String(slot);
    if (THEME_SLOT_RULES[key]) used.add(key);
  }
  if (!used.size) return "";
  return blockContract.THEME_SLOTS.filter((slot) => used.has(slot)).map((slot) => THEME_SLOT_RULES[slot]).join("\n");
}

function tokensCss() {
  return ":root{" + Object.keys(TOKENS).map((name) => name + ":" + TOKENS[name]).join("; ") + "}";
}

function referencedKeyframes(css) {
  const used = new Set();
  for (const match of css.matchAll(/animation(?:-name)?\s*:\s*([^;}]+)/g)) {
    for (const nameMatch of match[1].matchAll(/(?:^|[\s,])([A-Za-z][\w-]*)/g)) {
      if (KEYFRAMES[nameMatch[1]]) used.add(nameMatch[1]);
    }
  }
  return used;
}

function renderBioCss(options = {}) {
  const mutation = String(options.test_mutation || "");
  const theme = String(options.theme || "");
  const scope = validateScope(options.scope === undefined ? ".scr" : options.scope);
  if (!THEMES[theme]) fail("PAGE_DESIGN_THEME_UNKNOWN", [theme]);
  const parts = [tokensCss()];
  // 主題自己的強調色寫在 scope 上,蓋掉 :root 的全站預設。八個主題本來只換
  // 一張背景,按鈕與強調色全站共用同一組變數——換了主題只有底色會變,
  // 這是 the maintainer 一眼看出來的那件事。變數在 scope 裡覆蓋,元件規則一行都不用改。
  const shape = String(options.shape === undefined ? DEFAULT_SHAPE : options.shape);
  if (!SHAPES[shape]) fail("PAGE_DESIGN_SHAPE_UNKNOWN", [shape]);
  const themeTokens = THEMES[theme].tokens || {};
  // 形狀的變數與主題的變數寫在同一個 scope 區塊:兩者都是「這一頁的設定」,
  // 分成兩塊只會多一次層疊爭議。
  const shapeTokens = SHAPES[shape].tokens || {};
  const overrides = Object.entries(themeTokens).concat(Object.entries(shapeTokens))
    .map(([name, value]) => name + ":" + value + ";").join("");
  parts.push(scope + "{" + THEMES[theme].screen + ";" + overrides + "}");
  // 287-B 包二 (d):背景覆蓋緊跟在 screen 之後(同 scope、同特異性、後者勝);刀 ignore-background ⇒ D1/D2 必紅
  if (options.background && mutation !== "ignore-background") parts.push(backgroundCss(options.background, scope, { test_mutation: mutation }));
  for (const [selector, body] of BASE_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
  // page/aib-oneshot-0926(+avatar size/shape):非預設才覆寫(預設頁 CSS 與 mock BASE_RULES 逐位元組同)
  const avatarAppearance = resolveAvatarAppearance({
    avatar_size: options.avatar_size,
    avatar_shape: options.avatar_shape,
    fixed_avatar_size: options.fixed_avatar_size
  });
  const avatarCss = avatarOverrideCss(scope, avatarAppearance);
  if (avatarCss) parts.push(avatarCss);
  for (const [themes, selector, body] of THEME_RULES) {
    if (themes.includes(theme)) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
  }
  // 287-B 包三:淺底卡面材質排在 THEME_RULES 之後(同特異性後者勝);深底/preset/缺席 ⇒ 空字串不進 parts
  if (options.background && mutation !== "ignore-background") {
    const surface = surfaceCss(options.background, scope, { test_mutation: mutation });
    if (surface) parts.push(surface);
  }
  if (mutation !== "drop-anim-paused") parts.push(ANIM_PAUSED_RULE);
  if (mutation !== "drop-reduced-motion") parts.push(REDUCED_MOTION_RULE);
  // 287-B 包二:只有頁上有帶 span 的顆才輸出九宮規則(spans:true);刀 drop-span-mobile:手機不收單欄 ⇒ C2 必紅
  if (options.spans === true) {
    for (const [selector, body] of SPAN_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
    if (mutation !== "drop-span-mobile") parts.push(spanMobileRule(scope));
  }
  // 289 〇 / 292:只有頁上有取回顆(provenance:true)才輸出來源行樣式
  if (options.provenance === true) for (const [selector, body] of PROVENANCE_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
  // 289 五:只有頁上有商家格才輸出
  if (options.place === true) for (const [selector, body] of PLACE_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
  // 317 第 5 項:只有頁上有標題 / 分隔線才輸出;刀 structure-rules-always ⇒ 既有頁 CSS 逐位元組不變那格必紅
  if (options.structure === true || mutation === "structure-rules-always") for (const [selector, body] of STRUCTURE_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
  // 317 第 14 項:只有頁上有 QR 才輸出
  if (options.qr === true) for (const [selector, body] of QR_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
  if (options.gallery === true) for (const [selector, body] of GALLERY_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");   // 317 第 4 項
  // 317 第 18 項:只有頁上有表單才輸出
  if (options.form === true) for (const [selector, body] of FORM_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
  if (options.members === true) for (const [selector, body] of MEMBERS_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");   // 317 第 20 項
  // 317 第 12 項:只有頁上有檔案下載才輸出
  if (options.file === true) for (const [selector, body] of FILE_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
  if (options.video === true) for (const [selector, body] of VIDEO_POSTER_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
  // 317 第 13 項:只有頁上有預約卡才輸出
  if (options.booking === true) for (const [selector, body] of BOOK_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
  // 317 第 11 項:只有頁上有倒數才輸出
  if (options.countdown === true) for (const [selector, body] of COUNTDOWN_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
  // 317 第 9 項:只有頁上有見證才輸出
  if (options.quote === true) for (const [selector, body] of QUOTE_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
  // 317 第 10 項:只有頁上有 FAQ 才輸出
  if (options.faq === true) for (const [selector, body] of FAQ_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
  // page/aib-oneshot-0926:只有頁上有 link_group 才輸出
  if (options.link_group === true) for (const [selector, body] of LINK_GROUP_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
  // 317 第 6 項:只有頁上有帶圖示/縮圖的連結才輸出
  if (options.link_media === true) for (const [selector, body] of LINK_MEDIA_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
  // 317 第 7 項:只有頁上有重點連結才輸出(grow 的 keyframe 也一起)
  if (options.emphasis === true) { parts.push(GROW_KEYFRAME); for (const [selector, body] of EMPHASIS_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}"); }
  // RL-144 A51:只有頁上有帶說明的商品 / 活動才輸出(副本樹落檔刀把判斷改恆真 ⇒ 沒說明的頁 CSS 變了 ⇒ D2 紅)
  if (options.description === true) for (const [selector, body] of DESCRIPTION_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
  // 316 v1.1:只有頁上有封面才輸出
  if (options.cover === true) for (const [selector, body] of COVER_RULES) parts.push(prefixSelectors(scope, selector) + "{" + body + "}");
  if (mutation === "inject-external-url") parts.push(".bad{background:url(https://cdn.example/x.png)}");
  if (mutation === "animate-layout-prop") parts.push("@keyframes badgrow{from{margin-left:0}to{margin-left:12px}}\n" + scope + " .bad{animation:badgrow 1s linear infinite}");
  const bodyCss = parts.join("\n");
  const used = referencedKeyframes(bodyCss);
  const frames = [...used].sort().map((name) => "@keyframes " + name + "{" + KEYFRAMES[name] + "}").join("\n");
  return frames + "\n" + bodyCss + "\n";
}

// 套版頁一份 CSS 畫 24 張卡:基底規則掛 .scr 一次;每個主題的皮掛 .scr.<theme>(與 mock 同一種寫法,
// 不是 renderBioCss 的單主題 scope);形狀掛 .scr.shape-<shape>。零 iframe、零腳本——後台頁的 CSP 是
// nonce 的 style 標籤,示範內容裡的 style="…" 屬性進不來,所以示範標記用 class 不用行內樣式(見 template-demos)。
function renderGalleryCss(options = {}) {
  const mutation = String(options.test_mutation || "");
  // token 掛在 .tcard 不掛 :root:後台殼的 :root 自己有 --ink(=淺色字),pack 的 --ink 是深色印刷字,
  // 掛 :root 會把整個後台的字染黑。卡裡面要的 token 全在 .tcard 底下拿得到。
  const parts = [".tcard{" + Object.keys(TOKENS).map((name) => name + ":" + TOKENS[name]).join("; ") + "}"];
  for (const theme of Object.keys(THEMES)) {
    const overrides = Object.entries(THEMES[theme].tokens || {}).map(([name, value]) => name + ":" + value + ";").join("");
    parts.push(".scr." + theme + "{" + THEMES[theme].screen + ";" + overrides + "}");
  }
  for (const shape of Object.keys(SHAPES)) {
    const tokens = Object.entries(SHAPES[shape].tokens || {});
    if (tokens.length) parts.push(".scr.shape-" + shape + "{" + tokens.map(([name, value]) => name + ":" + value + ";").join("") + "}");
  }
  for (const [selector, body] of CARD_RULES) parts.push(selector + "{" + body + "}");
  for (const [selector, body] of BASE_RULES) parts.push(prefixSelectors(".scr", selector) + "{" + body + "}");
  for (const [themes, selector, body] of THEME_RULES) {
    if (mutation === "drop-theme-rules") break;
    parts.push(themes.map((theme) => prefixSelectors(".scr." + theme, selector)).join(", ") + "{" + body + "}");
  }
  parts.push(ANIM_PAUSED_RULE);
  parts.push(REDUCED_MOTION_RULE);
  const bodyCss = parts.join("\n");
  const used = referencedKeyframes(bodyCss);
  const frames = [...used].sort().map((name) => "@keyframes " + name + "{" + KEYFRAMES[name] + "}").join("\n");
  return frames + "\n" + bodyCss + "\n";
}

function keyframeBlocks(css) {
  const blocks = [];
  const pattern = /@keyframes\s+([\w-]+)\s*\{/g;
  let match;
  while ((match = pattern.exec(css))) {
    let depth = 1;
    let index = pattern.lastIndex;
    while (index < css.length && depth > 0) {
      if (css[index] === "{") depth += 1;
      else if (css[index] === "}") depth -= 1;
      index += 1;
    }
    blocks.push({ name: match[1], body: css.slice(pattern.lastIndex, index - 1) });
  }
  return blocks;
}

function auditBioCss(css) {
  const blockers = [];
  const bytes = Buffer.byteLength(String(css));
  if (bytes > CSS_BUDGET_BYTES) blockers.push("design_css_budget_exceeded");
  if (/@import\b/i.test(css)) blockers.push("design_css_import_forbidden");
  if (/url\(\s*["']?(?:https?:)?\/\//i.test(css)) blockers.push("design_external_asset_forbidden");
  if (/<\/?script/i.test(css) || /expression\s*\(/i.test(css)) blockers.push("design_css_active_content");
  if (!css.includes("@media (prefers-reduced-motion")) blockers.push("design_reduced_motion_missing");
  if (!css.includes(".anim-paused")) blockers.push("design_anim_paused_missing");
  const frames = keyframeBlocks(css);
  const defined = new Set(frames.map((frame) => frame.name));
  for (const frame of frames) {
    const allowed = new Set(ANIMATED_PROP_WHITELIST.concat(KEYFRAME_LAYOUT_EXCEPTIONS[frame.name] || []));
    for (const decl of frame.body.matchAll(/([a-zA-Z-]+)\s*:/g)) {
      if (!allowed.has(decl[1])) blockers.push("design_keyframe_layout_prop:" + frame.name + ":" + decl[1]);
    }
  }
  const bodyOnly = String(css).replace(/@keyframes\s+[\w-]+\s*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}/g, "");
  const used = new Set();
  for (const match of bodyOnly.matchAll(/animation(?:-name)?\s*:\s*([^;}]+)/g)) {
    for (const nameMatch of match[1].matchAll(/(?:^|[\s,])([A-Za-z][\w-]*)/g)) {
      const word = nameMatch[1];
      if (!/^(?:infinite|linear|ease|ease-in|ease-out|ease-in-out|alternate|both|forwards|backwards|paused|running|none|normal|reverse|steps|cubic-bezier)$/.test(word) && !/^\d/.test(word)) used.add(word);
    }
  }
  for (const name of defined) if (!used.has(name)) blockers.push("design_keyframe_orphan:" + name);
  for (const name of used) if (!defined.has(name)) blockers.push("design_keyframe_undefined:" + name);
  return {
    schema_version: VERSION,
    status: blockers.length ? "blocked" : "ok",
    blockers: [...new Set(blockers)].sort(),
    observed: { bytes, budget_bytes: CSS_BUDGET_BYTES, keyframes_defined: [...defined].sort(), keyframes_used: [...used].sort() }
  };
}

function auditDesignPack(options = {}) {
  const mutation = String(options.test_mutation || "");
  const blockers = [];
  const themeKeys = Object.keys(THEMES).filter((key) => mutation !== "drop-theme" || key !== "ocean");
  if (themeKeys.length !== 8) blockers.push("design_theme_count_invalid");
  if (BLOCKS.length !== 25) blockers.push("design_block_count_invalid");   // page/aib-oneshot-0926:→ 25(link_group)   // 317 第 4 項:→ 24(gallery)   // 317 第 18 項:→ 23(form)   // 317 第 14 項:→ 22(qr)   // 317 第 12 項:→ 21(file)   // 317 第 13 項:→ 20(booking)   // 317 第 11 項:→ 19(countdown)   // 317 第 10 項:→ 17(faq);第 9 項:→ 18(testimonial)   // 289 五:13 → 14(place);317 第 5 項:→ 16(heading / divider)
  const map = Object.assign({}, CONTRACT_CLASS_MAP);
  if (mutation === "unmap-contract-type") delete map.text;
  for (const type of blockContract.TYPES) {
    if (!(type in map)) blockers.push("design_contract_coverage_missing:" + type);
  }
  const mappedClasses = new Set(BLOCKS.flatMap((block) => block.classes));
  for (const type of Object.keys(map)) {
    if (map[type] !== null && !mappedClasses.has(map[type])) blockers.push("design_contract_class_unknown:" + type);
  }
  const budgets = performancePolicy.DEFAULT_BUDGETS;
  if (CSS_BUDGET_BYTES + budgets.html_bytes > budgets.total_payload_bytes) blockers.push("design_css_budget_overflows_payload");
  if (Object.keys(SHAPES).length !== 3) blockers.push("design_shape_count_invalid");
  const themeBytes = {};
  for (const key of themeKeys) {
    const css = renderBioCss({ theme: key });
    const audit = auditBioCss(css);
    themeBytes[key] = audit.observed.bytes;
    for (const blocker of audit.blockers) blockers.push(blocker + "@" + key);
  }
  // 每一種形狀都要真的產得出來且過同一套稽核 —— 只驗預設形狀等於沒驗形狀。
  for (const shape of Object.keys(SHAPES)) {
    const css = renderBioCss({ theme: "nebula", shape });
    for (const blocker of auditBioCss(css).blockers) blockers.push(blocker + "@shape:" + shape);
    // 預設形狀不得輸出任何形狀變數:那樣既有頁面的輸出就不是逐位元組相同了。
    const emitsToken = css.includes("--btn-r:");
    if (shape === DEFAULT_SHAPE && emitsToken) blockers.push("design_default_shape_emits_token");
    if (shape !== DEFAULT_SHAPE && !emitsToken) blockers.push("design_shape_token_missing:" + shape);
  }
  return {
    schema_version: VERSION,
    status: blockers.length ? "blocked" : "ok",
    blockers: [...new Set(blockers)].sort(),
    observed: { themes: themeKeys, shapes: Object.keys(SHAPES), blocks: BLOCKS.length, contract_types: [...blockContract.TYPES], theme_css_bytes: themeBytes }
  };
}

const TEMPLATE_THEME_ORDER = Object.freeze(["dawn", "forest", "mono", "nebula", "ocean", "pop", "rosegold", "starlight"]);
const TEMPLATE_SHAPE_ORDER = Object.freeze(["rounded", "rectangle", "pill"]);
function idFor(theme, shape) {
  const themeIndex = TEMPLATE_THEME_ORDER.indexOf(String(theme || ""));
  const shapeIndex = TEMPLATE_SHAPE_ORDER.indexOf(String(shape || ""));
  if (themeIndex < 0 || shapeIndex < 0) return null;
  return "T" + String(themeIndex * 3 + shapeIndex + 1).padStart(2, "0");
}
function parseId(id) {
  const match = /^T(\d{2})$/.exec(String(id || ""));
  if (!match) return null;
  const n = Number(match[1]);
  if (!Number.isInteger(n) || n < 1 || n > 24) return null;
  const idx = n - 1;
  return { theme: TEMPLATE_THEME_ORDER[Math.floor(idx / 3)], shape: TEMPLATE_SHAPE_ORDER[idx % 3] };
}

module.exports = {
  VERSION,
  SOURCE_MOCK,
  CSS_BUDGET_BYTES,
  TOKENS,
  KEYFRAMES,
  ANIMATED_PROP_WHITELIST,
  KEYFRAME_LAYOUT_EXCEPTIONS,
  THEMES,
  SHAPES,
  TEMPLATE_THEME_ORDER,
  TEMPLATE_SHAPE_ORDER,
  idFor,
  parseId,
  DEFAULT_SHAPE,
  AVATAR_SIZES, AVATAR_SHAPES, DEFAULT_AVATAR_SIZE, DEFAULT_AVATAR_SHAPE,
  resolveAvatarAppearance, avatarOverrideCss, validateAvatarAppearance,
  BLOCKS,
  PROFILE_CHROME,
  CONTRACT_CLASS_MAP, COVER_RULES, MEMBERS_RULES,
  BASE_RULES, SPAN_RULES, PROVENANCE_RULES, PLACE_RULES, STRUCTURE_RULES, FAQ_RULES, LINK_GROUP_RULES, QUOTE_RULES, COUNTDOWN_RULES, BOOK_RULES, FILE_RULES, VIDEO_POSTER_RULES, QR_RULES, GALLERY_RULES, FORM_RULES, LINK_MEDIA_RULES, DESCRIPTION_RULES, EMPHASIS_RULES, GROW_KEYFRAME, BACKGROUND_DIRS, validateBackground, inkFor, relativeLuminance, backgroundCss, surfaceFor, surfaceCss, SURFACE_RULES, SURFACE_DARK, blockBackgroundCss, BLOCK_BG_VIDEO_RULES, THEME_SLOT_RULES, themeSlotCss,
  THEME_RULES,
  CARD_RULES,
  ANIM_PAUSED_RULE,
  REDUCED_MOTION_RULE,
  renderBioCss,
  renderGalleryCss,
  auditBioCss,
  auditDesignPack
};
