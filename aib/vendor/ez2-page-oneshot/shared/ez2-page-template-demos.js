"use strict";

// Ez2.PAGE 套版示範內容 —— 275(a) v3(09-17 艦長派工;the maintainer 原話「能把原來的模板 1:1 放進來可以點選套版就好嗎」)。
//
// 唯一真相源:docs/design/ez2-page-frontend-mock.html(v41)主題頁那八張卡(<div class="page" data-page="themes">)。
// 每一套的示範標記**逐字**抄自原稿,smoke(tools/ez2-page-template-demos-smoke.js)對原稿逐字守恆;要改先改原稿。
// 只有一種改寫,而且可逆:原稿卡裡的三種行內樣式改成 class(後台頁的 CSP 是 nonce 的 <style>,style="…" 屬性進不來):
//   style="height:96px"(.b-img)      → 拿掉(設計包 .b-img 本來就是 96px,逐像素同)
//   style="opacity:.7|.8"(.b-txt)   → class op7 / op8(DEMO_RULES)
//   style="aspect-ratio:16/8"(.b-vid) → class wide(DEMO_RULES)
// smoke 用同一張對照表把原稿轉過來再比,所以這張表本身也被守著。
//
// 八套的角色(原稿):深空 創作者 / 純粹 攝影 / 曙光 講師 / 森海 店家 / 玫瑰金 美妝 / 星光 作家 / 繽紛 樂團 / 海洋 潛旅。
// 形狀不在原稿裡(原稿只有八張卡,形狀是設計包後加的第二軸):同一套示範標記套 .scr.shape-<shape> 就是那一張。

const designPack = require("./ez2-page-design-pack.js");
const blockContract = require("./ez2-page-block-contract.js");

const SOURCE_MOCK = designPack.SOURCE_MOCK;
const SOURCE_PAGE_MARKER = '<div class="page" data-page="themes">';
// 原稿行內樣式 → class 的對照表(smoke 兩邊共用)。
const INLINE_STYLE_TO_CLASS = Object.freeze([
  Object.freeze({ from: ' style="height:96px"', to: "" }),
  Object.freeze({ from: '<p class="b-txt" style="opacity:.7">', to: '<p class="b-txt op7">' }),
  Object.freeze({ from: '<p class="b-txt" style="opacity:.8">', to: '<p class="b-txt op8">' }),
  Object.freeze({ from: '<div class="b-vid" style="aspect-ratio:16/8">', to: '<div class="b-vid wide">' })
]);
const DEMO_RULES = Object.freeze([
  [".tcard .b-txt.op7", "opacity:.7"],
  [".tcard .b-txt.op8", "opacity:.8"],
  [".tcard .b-vid.wide", "aspect-ratio:16/8"]
].map(Object.freeze));

const DEMOS = Object.freeze({
  nebula: Object.freeze({
    title: "深空 Nebula",
    tagline: "預設 · 星雲極光。音樂/影片/AI 分身的舞台。",
    markup: [
    "<div class=\"b-ava\"><div class=\"i\">A</div></div>",
    "<div class=\"b-id\"><div class=\"nm\">深空 · 創作者<span class=\"vbadge\"><svg viewBox=\"0 0 24 24\"><path d=\"M5 13l4 4 10-10\"></path></svg></span></div><div class=\"bi\">Podcast × 專欄 × AI 導入</div></div>",
    "<div class=\"b-status\"><span class=\"pill\"><span class=\"dot\"></span>現在有空 · 回覆很快</span></div>",
    "<div class=\"b-soc\">",
    "<span class=\"s\"><svg viewBox=\"0 0 24 24\"><rect x=\"3\" y=\"3\" width=\"18\" height=\"18\" rx=\"5\"></rect><circle cx=\"12\" cy=\"12\" r=\"4\"></circle></svg></span>",
    "<span class=\"s\"><svg viewBox=\"0 0 24 24\"><rect x=\"2.5\" y=\"5\" width=\"19\" height=\"14\" rx=\"4\"></rect><path d=\"M10 9.3v5.4l4.8-2.7z\" fill=\"#cfd4ee\" stroke=\"none\"></path></svg></span>",
    "<span class=\"s\"><svg viewBox=\"0 0 24 24\"><path d=\"M21 11.5a8.4 8.4 0 0 1-8.5 8.3 9 9 0 0 1-3.8-.8L3 20l1.1-3.4a8 8 0 0 1-1.6-4.9A8.4 8.4 0 0 1 11 3.2a8.4 8.4 0 0 1 10 8.3z\"></path></svg></span>",
    "</div>",
    "<div class=\"b-cta\"><span class=\"c br\">LINE</span><span class=\"c\">預約</span><span class=\"c soon\">AI 分身</span></div>",
    "<div class=\"b-music\"><div class=\"art\"><i></i><i></i><i></i></div><div class=\"m\"><b>宇宙電台 EP.12</b><span>本週靈感 · Podcast</span><div class=\"bar\"><i></i></div></div><div class=\"pl\"><i></i></div></div>",
    "<div class=\"b-vid\"><div class=\"pl\"><i></i></div><span class=\"du\">3:24</span></div>",
    "<div class=\"b-lnk\"><span>作品集<small>2026 精選 12 件</small></span><span class=\"ar\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5\"/></svg></span></div>",
    "<div class=\"b-lnk\"><span>專欄文章<small>每週更新</small></span><span class=\"ar\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5\"/></svg></span></div>",
    "<div class=\"b-mail\"><input placeholder=\"訂閱電子報\"><span class=\"go\">訂閱</span></div>",
    "<div class=\"b-tip\"><svg viewBox=\"0 0 24 24\"><path d=\"M12 20s-7-4.6-9.2-8.8C1.2 8 3 4.8 6.4 4.8c2 0 3.4 1.1 4.1 2.3.3.5 1.7.5 2 0 .7-1.2 2.1-2.3 4.1-2.3 3.4 0 5.2 3.2 3.6 6.4C19 15.4 12 20 12 20z\"></path></svg>贊助創作</div>",
    "<div class=\"b-ft\"><span>Powered by Ez2.PAGE</span></div>"
    ].join("\n")
  }),
  mono: Object.freeze({
    title: "純粹 Mono",
    tagline: "白盒子。社群列+滿版作品圖,內容就是主角。",
    markup: [
    "<div class=\"b-ava\"><div class=\"i\">A</div></div>",
    "<div class=\"b-id\"><div class=\"nm\">純粹 · 作品集<span class=\"vbadge\"><svg viewBox=\"0 0 24 24\"><path d=\"M5 13l4 4 10-10\"></path></svg></span></div><div class=\"bi\">攝影 × 平面設計</div></div>",
    "<div class=\"b-soc\">",
    "<span class=\"s\"><svg viewBox=\"0 0 24 24\"><rect x=\"3\" y=\"3\" width=\"18\" height=\"18\" rx=\"5\"></rect><circle cx=\"12\" cy=\"12\" r=\"4\"></circle></svg></span>",
    "<span class=\"s\"><svg viewBox=\"0 0 24 24\"><rect x=\"2.5\" y=\"5\" width=\"19\" height=\"14\" rx=\"4\"></rect><path d=\"M10 9.3v5.4l4.8-2.7z\" fill=\"#5a5a5f\" stroke=\"none\"></path></svg></span>",
    "<span class=\"s\"><svg viewBox=\"0 0 24 24\"><path d=\"M4 4l16 16M20 4L4 20\"></path></svg></span>",
    "</div>",
    "<div class=\"b-img\">攝影集 2026 · 城市光影</div>",
    "<div class=\"b-lnk\"><span>展覽紀錄<small>2024-2026</small></span><span class=\"ar\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5\"/></svg></span></div>",
    "<div class=\"b-img\">商業案:品牌形象拍攝</div>",
    "<div class=\"b-lnk\"><span>接案洽詢<small>婚紗/形象/商品</small></span><span class=\"ar\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5\"/></svg></span></div>",
    "<div class=\"b-lnk\"><span>出版品《街的呼吸》</span><span class=\"ar\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5\"/></svg></span></div>",
    "<p class=\"b-txt op7\">「快門是眼睛的心跳。」</p>",
    "<div class=\"b-mail\"><input placeholder=\"展訊通知\"><span class=\"go\">訂閱</span></div>",
    "<div class=\"b-ft\"><span>Powered by Ez2.PAGE</span></div>"
    ].join("\n")
  }),
  dawn: Object.freeze({
    title: "曙光 Dawn",
    tagline: "暖色舞台。活動報名+電子報,把聽眾變學員。",
    markup: [
    "<div class=\"b-ava\"><div class=\"i\">A</div></div>",
    "<div class=\"b-id\"><div class=\"nm\">曙光 · 講師<span class=\"vbadge\"><svg viewBox=\"0 0 24 24\"><path d=\"M5 13l4 4 10-10\"></path></svg></span></div><div class=\"bi\">企業內訓 × 公開班</div></div>",
    "<div class=\"b-event\"><div class=\"d\"><b>7/30</b><span>WED</span></div><div class=\"t\"><b>實戰工作坊</b><span>台北 · 剩 6 席</span></div></div>",
    "<div class=\"b-vid wide\"><div class=\"pl\"><i></i></div><span class=\"du\">課程試看 8:12</span></div>",
    "<div class=\"b-lnk\"><span>線上課程<small>800+ 學員</small></span><span class=\"ar\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5\"/></svg></span></div>",
    "<div class=\"b-shop\">",
    "<div class=\"pd\"><div class=\"ph2\"></div><b>講義電子書</b><div class=\"pr\"><span>NT$390</span><i>購買</i></div></div>",
    "<div class=\"pd\"><div class=\"ph2\"></div><b>企業內訓</b><div class=\"pr\"><span>報價</span><i>洽詢</i></div></div>",
    "</div>",
    "<div class=\"b-lnk\"><span>企業內訓邀約</span><span class=\"ar\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5\"/></svg></span></div>",
    "<div class=\"b-lnk\"><span>學員見證</span><span class=\"ar\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5\"/></svg></span></div>",
    "<div class=\"b-mail\"><input placeholder=\"訂閱電子報\"><span class=\"go\">訂閱</span></div>",
    "<div class=\"b-ft\"><span>Powered by Ez2.PAGE</span></div>"
    ].join("\n")
  }),
  forest: Object.freeze({
    title: "森海 Forest",
    tagline: "沉穩墨綠。商品架+LINE 訂位,店家的門口。",
    markup: [
    "<div class=\"b-ava\"><div class=\"i\">A</div></div>",
    "<div class=\"b-id\"><div class=\"nm\">森海 · 店家<span class=\"vbadge\"><svg viewBox=\"0 0 24 24\"><path d=\"M5 13l4 4 10-10\"></path></svg></span></div><div class=\"bi\">自家烘焙 × 手沖教學</div></div>",
    "<div class=\"b-status\"><span class=\"pill\"><span class=\"dot\"></span>今日營業至 21:00</span></div>",
    "<div class=\"b-img\">店景 · 木質吧台</div>",
    "<div class=\"b-shop\">",
    "<div class=\"pd\"><div class=\"ph2\"></div><b>手沖組</b><div class=\"pr\"><span>NT$390</span><i>購買</i></div></div>",
    "<div class=\"pd\"><div class=\"ph2\"></div><b>掛耳包</b><div class=\"pr\"><span>NT$220</span><i>購買</i></div></div>",
    "</div>",
    "<div class=\"b-cta\"><span class=\"c br\">LINE 訂位</span><span class=\"c\">菜單</span></div>",
    "<div class=\"b-event\"><div class=\"d\"><b>8/10</b><span>SUN</span></div><div class=\"t\"><b>手沖入門課</b><span>剩 3 位</span></div></div>",
    "<div class=\"b-lnk\"><span>本月烘豆筆記</span><span class=\"ar\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5\"/></svg></span></div>",
    "<div class=\"b-mail\"><input placeholder=\"新豆到貨通知\"><span class=\"go\">訂閱</span></div>",
    "<div class=\"b-tip\"><svg viewBox=\"0 0 24 24\"><path d=\"M12 20s-7-4.6-9.2-8.8C1.2 8 3 4.8 6.4 4.8c2 0 3.4 1.1 4.1 2.3.3.5 1.7.5 2 0 .7-1.2 2.1-2.3 4.1-2.3 3.4 0 5.2 3.2 3.6 6.4C19 15.4 12 20 12 20z\"></path></svg>支持小店</div>",
    "<div class=\"b-ft\"><span>Powered by Ez2.PAGE</span></div>"
    ].join("\n")
  }),
  rosegold: Object.freeze({
    title: "玫瑰金 Rose Gold",
    tagline: "美妝、婚宴、質感手作的暖粉金。",
    markup: [
    "<div class=\"b-ava\"><div class=\"i\">A</div></div>",
    "<div class=\"b-id\"><div class=\"nm\">玫瑰金 · 美妝</div><div class=\"bi\">新娘秘書 × 彩妝講師</div></div>",
    "<div class=\"b-status\"><span class=\"pill\"><span class=\"dot\"></span>八月檔期開放中</span></div>",
    "<div class=\"b-cta\"><span class=\"c br\">LINE 預約試妝</span><span class=\"c\">IG</span></div>",
    "<div class=\"b-img\">作品:春季新娘造型</div>",
    "<div class=\"b-event\"><div class=\"d\"><b>8/03</b><span>SUN</span></div><div class=\"t\"><b>新娘早妝檔期</b><span>剩 2 檔</span></div></div>",
    "<div class=\"b-shop\">",
    "<div class=\"pd\"><div class=\"ph2\"></div><b>一對一彩妝課</b><div class=\"pr\"><span>NT$2,800</span><i>預約</i></div></div>",
    "<div class=\"pd\"><div class=\"ph2\"></div><b>婚宴全日方案</b><div class=\"pr\"><span>NT$12,800</span><i>詢問</i></div></div>",
    "</div>",
    "<div class=\"b-lnk\"><span>婚宴檔期查詢</span><span class=\"ar\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5\"/></svg></span></div>",
    "<div class=\"b-lnk\"><span>學生作品牆</span><span class=\"ar\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5\"/></svg></span></div>",
    "<div class=\"b-mail\"><input placeholder=\"檔期釋出通知\"><span class=\"go\">訂閱</span></div>",
    "<div class=\"b-ft\"><span>Powered by Ez2.PAGE</span></div>"
    ].join("\n")
  }),
  starlight: Object.freeze({
    title: "星光 Starlight",
    tagline: "作家、職人的暖銀奶油色。",
    markup: [
    "<div class=\"b-ava\"><div class=\"i\">A</div></div>",
    "<div class=\"b-id\"><div class=\"nm\">星光 · 作家</div><div class=\"bi\">小說 × 每週書評</div></div>",
    "<p class=\"b-txt op8\">「寫作是把星光裝進句子裡。」</p>",
    "<div class=\"b-img\">新書《夜航》書封</div>",
    "<div class=\"b-lnk\"><span>新書《夜航》<small>各書店連結</small></span><span class=\"ar\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5\"/></svg></span></div>",
    "<div class=\"b-music\"><div class=\"art\"><i></i><i></i><i></i></div><div class=\"m\"><b>《夜航》有聲版</b><span>試聽第一章</span><div class=\"bar\"><i></i></div></div></div>",
    "<div class=\"b-lnk\"><span>專欄與書評<small>每週四</small></span><span class=\"ar\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5\"/></svg></span></div>",
    "<div class=\"b-lnk\"><span>演講與簽書邀約</span><span class=\"ar\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5\"/></svg></span></div>",
    "<div class=\"b-mail\"><input placeholder=\"每週四,一封讀信\"><span class=\"go\">訂閱</span></div>",
    "<div class=\"b-tip\"><svg viewBox=\"0 0 24 24\"><path d=\"M12 20s-7-4.6-9.2-8.8C1.2 8 3 4.8 6.4 4.8c2 0 3.4 1.1 4.1 2.3.3.5 1.7.5 2 0 .7-1.2 2.1-2.3 4.1-2.3 3.4 0 5.2 3.2 3.6 6.4C19 15.4 12 20 12 20z\"></path></svg>支持寫作</div>",
    "<div class=\"b-ft\"><span>Powered by Ez2.PAGE</span></div>"
    ].join("\n")
  }),
  pop: Object.freeze({
    title: "繽紛 Pop",
    tagline: "樂團、表演者的高飽和舞台。",
    markup: [
    "<div class=\"b-ava\"><div class=\"i\">A</div></div>",
    "<div class=\"b-id\"><div class=\"nm\">繽紛 · 樂團</div><div class=\"bi\">Live × 巡演 × 周邊</div></div>",
    "<div class=\"b-soc\">",
    "<span class=\"s\"><svg viewBox=\"0 0 24 24\"><rect x=\"3\" y=\"3\" width=\"18\" height=\"18\" rx=\"5\"></rect><circle cx=\"12\" cy=\"12\" r=\"4\"></circle></svg></span>",
    "<span class=\"s\"><svg viewBox=\"0 0 24 24\"><rect x=\"2.5\" y=\"5\" width=\"19\" height=\"14\" rx=\"4\"></rect><path d=\"M10 9.3v5.4l4.8-2.7z\" fill=\"#fff\" stroke=\"none\"></path></svg></span>",
    "<span class=\"s\"><svg viewBox=\"0 0 24 24\"><path d=\"M4 4l16 16M20 4L4 20\"></path></svg></span>",
    "</div>",
    "<div class=\"b-music\"><div class=\"art\"><i></i><i></i><i></i></div><div class=\"m\"><b>新單曲〈星塵〉</b><span>各平台上線</span><div class=\"bar\"><i></i></div></div><div class=\"pl\"><i></i></div></div>",
    "<div class=\"b-vid wide\"><div class=\"pl\"><i></i></div><span class=\"du\">MV 3:24</span></div>",
    "<div class=\"b-event\"><div class=\"d\"><b>8/20</b><span>WED</span></div><div class=\"t\"><b>Legacy 台北場</b><span>售票中</span></div></div>",
    "<div class=\"b-lnk\"><span>巡演日程<small>五城市</small></span><span class=\"ar\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5\"/></svg></span></div>",
    "<div class=\"b-shop\">",
    "<div class=\"pd\"><div class=\"ph2\"></div><b>巡演 T</b><div class=\"pr\"><span>NT$690</span><i>購買</i></div></div>",
    "<div class=\"pd\"><div class=\"ph2\"></div><b>簽名專輯</b><div class=\"pr\"><span>NT$480</span><i>購買</i></div></div>",
    "</div>",
    "<div class=\"b-tip\"><svg viewBox=\"0 0 24 24\"><path d=\"M12 20s-7-4.6-9.2-8.8C1.2 8 3 4.8 6.4 4.8c2 0 3.4 1.1 4.1 2.3.3.5 1.7.5 2 0 .7-1.2 2.1-2.3 4.1-2.3 3.4 0 5.2 3.2 3.6 6.4C19 15.4 12 20 12 20z\"></path></svg>應援贊助</div>",
    "<div class=\"b-ft\"><span>Powered by Ez2.PAGE</span></div>"
    ].join("\n")
  }),
  ocean: Object.freeze({
    title: "海洋 Ocean",
    tagline: "潛旅、戶外教練的深海藍。",
    markup: [
    "<div class=\"b-ava\"><div class=\"i\">A</div></div>",
    "<div class=\"b-id\"><div class=\"nm\">海洋 · 潛旅<span class=\"vbadge\"><svg viewBox=\"0 0 24 24\"><path d=\"M5 13l4 4 10-10\"></path></svg></span></div><div class=\"bi\">自由潛水 × 離島團</div></div>",
    "<div class=\"b-img\">小琉球 · 七月團花絮</div>",
    "<div class=\"b-event\"><div class=\"d\"><b>8/12</b><span>SAT</span></div><div class=\"t\"><b>綠島三日團</b><span>剩 4 位</span></div></div>",
    "<div class=\"b-vid wide\"><div class=\"pl\"><i></i></div><span class=\"du\">水下花絮 2:08</span></div>",
    "<div class=\"b-shop\">",
    "<div class=\"pd\"><div class=\"ph2\"></div><b>體驗課</b><div class=\"pr\"><span>NT$2,400</span><i>報名</i></div></div>",
    "<div class=\"pd\"><div class=\"ph2\"></div><b>AIDA2 證照</b><div class=\"pr\"><span>NT$9,800</span><i>報名</i></div></div>",
    "</div>",
    "<div class=\"b-lnk\"><span>全部行程<small>八月三團</small></span><span class=\"ar\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5\"/></svg></span></div>",
    "<div class=\"b-cta\"><span class=\"c br\">LINE 詢問</span><span class=\"c\">IG</span></div>",
    "<div class=\"b-mail\"><input placeholder=\"出團通知\"><span class=\"go\">訂閱</span></div>",
    "<div class=\"b-ft\"><span>Powered by Ez2.PAGE</span></div>"
    ].join("\n")
  }),
});

// ── 示範內容的契約版(給「選套版時 0 塊就種進草稿」用,艦長 09-17 第 4 點預設「種」)──
// 每一列對應上面該套示範標記裡的一個區塊,同一個順序、同一串字;連結全是 https://example.com/… 佔位
// (示範內容是給店主改的草稿,不是真的去處)。頭像/名字/狀態/社群列是註冊表的 profile 欄位,不是區塊,不種。
// 活動日期挑「星期與原稿一致」的日子(原稿 7/30 WED 在 2026 是週四):渲染器用真日曆算星期,不照抄字面。
// 影片角標原稿是「課程試看 8:12」:契約只存秒數、渲染器只印 8:12,標題走 aria-label——這是契約層的決定,不在這裡繞。
const EX = "https://example.com/";
const TW = "Asia/Taipei";
function tpe(dateIso, hh) { return dateIso + "T" + String(hh).padStart(2, "0") + ":00:00+08:00"; }   // 台北時間寫成 UTC ISO 給契約
const cta = (label, href, sublabel) => ["cta", { label, action: { type: "open_url", href: EX + href, target_id: null }, sublabel: sublabel == null ? null : sublabel }];
const soon = (label, type, targetId) => ["cta", { label, action: { type, href: null, target_id: targetId }, sublabel: null }];
const link = (label, href, sublabel) => ["link", { label, href: EX + href, rel: ["noopener"], sublabel: sublabel == null ? null : sublabel }];
const music = (title, sublabel, trackId) => ["music", { provider: "spotify", track_id: trackId, title, href: EX + "music/" + trackId, sublabel }];
const video = (title, seconds, videoId) => ["video", { provider: "youtube", video_id: videoId, title, href: "https://www.youtube.com/watch?v=" + videoId, autoplay: false, thumbnail_url: null, uploaded_at: null, description: null, duration_seconds: seconds }];
// 277-B(RL-062 乙):商品卡可帶一張圖(image_asset_id);只有森海的手沖組/掛耳包有真照片槽位,其餘 null。
//   與 image 區塊一樣,種的時候每張都解析得到才帶(demoBlocks options.images),不然公開頁會畫破圖。
const shop = (title, price, amount, button, productId, imageId = null) => ["shop", { provider: "external", product_id: productId, title, href: EX + "shop/" + productId, price_display: price, price_amount: amount, currency: "TWD", availability: "in_stock", image_asset_id: imageId, button_label: button }];
const event = (title, location, dateIso, hh, href) => ["event", { title, starts_at: new Date(tpe(dateIso, hh)).toISOString(), ends_at: null, location, href: href ? EX + href : null, timezone: TW }];
const mail = (title, listId) => ["mail", { list_id: listId, title, consent_text: "我同意收到這份電子報,可隨時取消訂閱。", button_label: "訂閱" }];
const tip = (title, slug) => ["tip", { provider: "external", href: EX + "tip/" + slug, title, suggested_amounts: [100, 300, 500], currency: "TWD" }];
const image = (alt, assetId) => ["image", { asset_id: assetId, alt, href: null }];
const text = (value) => ["text", { text: value, format: "plain" }];

const DEMO_BLOCKS = Object.freeze({
  nebula: [
    cta("LINE", "line"), cta("預約", "book"), soon("AI 分身", "open_ai", "ai_twin_demo"),
    music("宇宙電台 EP.12", "本週靈感 · Podcast", "demo_ep12"),
    video("3:24", 204, "demo_intro"),
    link("作品集", "portfolio", "2026 精選 12 件"), link("專欄文章", "column", "每週更新"),
    mail("訂閱電子報", "demo_weekly"), tip("贊助創作", "creator")
  ],
  mono: [
    image("攝影集 2026 · 城市光影", "demo_photo_city"), link("展覽紀錄", "exhibitions", "2024-2026"),
    image("商業案:品牌形象拍攝", "demo_photo_brand"), link("接案洽詢", "hire", "婚紗/形象/商品"),
    link("出版品《街的呼吸》", "book"), text("「快門是眼睛的心跳。」"), mail("展訊通知", "demo_exhibit")
  ],
  dawn: [
    event("實戰工作坊", "台北 · 剩 6 席", "2025-07-30", 10, "workshop"),
    video("課程試看", 492, "demo_course_trailer"),
    link("線上課程", "course", "800+ 學員"),
    shop("講義電子書", "NT$390", 390, "購買", "demo_handbook"), shop("企業內訓", "報價", null, "洽詢", "demo_corporate"),
    link("企業內訓邀約", "corporate"), link("學員見證", "reviews"), mail("訂閱電子報", "demo_weekly")
  ],
  forest: [
    image("店景 · 木質吧台", "demo_shop_bar"),
    shop("手沖組", "NT$390", 390, "購買", "demo_pourover", "demo_shop_pourover"), shop("掛耳包", "NT$220", 220, "購買", "demo_dripbag", "demo_shop_dripbag"),
    cta("LINE 訂位", "line"), cta("菜單", "menu"),
    event("手沖入門課", "剩 3 位", "2025-08-10", 14, "class"),
    link("本月烘豆筆記", "roasting"), mail("新豆到貨通知", "demo_newbeans"), tip("支持小店", "shop")
  ],
  rosegold: [
    cta("LINE 預約試妝", "line"), cta("IG", "instagram"),
    image("作品:春季新娘造型", "demo_bride_spring"),
    event("新娘早妝檔期", "剩 2 檔", "2025-08-03", 5, "booking"),
    shop("一對一彩妝課", "NT$2,800", 2800, "預約", "demo_makeup_class"), shop("婚宴全日方案", "NT$12,800", 12800, "詢問", "demo_wedding_day"),
    link("婚宴檔期查詢", "schedule"), link("學生作品牆", "students"), mail("檔期釋出通知", "demo_slots")
  ],
  starlight: [
    text("「寫作是把星光裝進句子裡。」"), image("新書《夜航》書封", "demo_book_cover"),
    link("新書《夜航》", "book", "各書店連結"), music("《夜航》有聲版", "試聽第一章", "demo_audiobook"),
    link("專欄與書評", "column", "每週四"), link("演講與簽書邀約", "talks"),
    mail("每週四,一封讀信", "demo_letter"), tip("支持寫作", "writer")
  ],
  pop: [
    music("新單曲〈星塵〉", "各平台上線", "demo_single"), video("MV", 204, "demo_mv"),
    event("Legacy 台北場", "售票中", "2025-08-20", 19, "tickets"),
    link("巡演日程", "tour", "五城市"),
    shop("巡演 T", "NT$690", 690, "購買", "demo_tour_tee"), shop("簽名專輯", "NT$480", 480, "購買", "demo_signed_album"),
    tip("應援贊助", "band")
  ],
  ocean: [
    image("小琉球 · 七月團花絮", "demo_trip_july"),
    event("綠島三日團", "剩 4 位", "2023-08-12", 8, "trip"),
    video("水下花絮", 128, "demo_underwater"),
    shop("體驗課", "NT$2,400", 2400, "報名", "demo_trial"), shop("AIDA2 證照", "NT$9,800", 9800, "報名", "demo_aida2"),
    link("全部行程", "trips", "八月三團"),
    cta("LINE 詢問", "line"), cta("IG", "instagram"),
    mail("出團通知", "demo_departures")
  ]
});

// 契約版的區塊記錄(schema 完整、位置 0..n-1、public)。id 帶 tenant 無關的前綴,呼叫端(工作區)會換成自己的 id。
// ★ 圖片區塊預設**不種**(options.images 才含):契約要 asset_id,而新頁一張圖都沒上傳,正式站的公開頁不查資產存不存在
//   (buildStaticBundle 沒帶 assets),種一個指不到檔的 asset_id 會在線上畫出破圖圖示——比少一格更傷。原稿那格本來就是
//   「店主自己的照片」的佔位,照片由編輯器上傳後自己加。
// 277-B:這一套示範裡的圖片 asset_id(種的時候要逐一解析得到才帶圖;build 工具也讀這裡,不另抄 id)。
function demoImageIds(theme) {
  if (!DEMO_BLOCKS[theme]) fail("PAGE_TEMPLATE_DEMO_UNKNOWN", [theme]);
  return DEMO_BLOCKS[theme].flatMap(([type, payload]) => type === "image" ? [payload.asset_id] : (type === "shop" && payload.image_asset_id ? [payload.image_asset_id] : []));
}

function demoBlocks(theme, scope, options = {}) {
  if (!DEMO_BLOCKS[theme]) fail("PAGE_TEMPLATE_DEMO_UNKNOWN", [theme]);
  const at = String((scope && scope.at) || "2026-01-01T00:00:00.000Z");
  const rows = DEMO_BLOCKS[theme].filter(([type]) => options.images === true || type !== "image");
  return rows.map(([type, payload], position) => ({
    schema_version: blockContract.VERSION,
    tenant_id: String(scope.tenant_id), page_id: String(scope.page_id),
    block_id: "block_tpl_" + theme + "_" + String(position).padStart(2, "0"),
    block_type: type, version: 1, position, visibility: "public",
    // 不帶圖時商品卡的 image_asset_id 也拿掉(渲染器對它不查資產存不存在,留著就是破圖)
    payload: Object.assign(JSON.parse(JSON.stringify(payload)), type === "shop" && options.images !== true ? { image_asset_id: null } : {}),
    created_at: at, updated_at: at
  }));
}

// 277-A(RL-057 乙,root2 規格 PAGE-277-A-demo-lifecycle-v1):「示範」是**算出來**的狀態,不存標記——區塊是示範 ⇔ 型別 + payload
//   的簽章命中 8 套 DEMO_BLOCKS 任一顆(不限目前色系:店主種完可能換色系);改過任何一個字就不是示範。
//   ★ 簽章用**鍵排序後**的 JSON:編輯器表單原樣存回會改鍵序,不能因此把沒改的顆判成「已改」。
//   ★ 兩種種法都要認:帶圖(images:true)與不帶圖(image 顆不種、shop 顆 image_asset_id 清成 null,見 demoBlocks)。
//   三處(編輯器標、發布閘數、公開頁濾)都只呼叫這裡,不各自比。
function canonicalJson(value) {
  if (Array.isArray(value)) return "[" + value.map(canonicalJson).join(",") + "]";
  if (value && typeof value === "object") return "{" + Object.keys(value).sort().map((key) => JSON.stringify(key) + ":" + canonicalJson(value[key])).join(",") + "}";
  return JSON.stringify(value === undefined ? null : value);
}
function demoSignature(block) { return String(block && block.block_type) + "\u0000" + canonicalJson(block && block.payload); }

// S1 oneshot empty scaffold: 1.5-screen creator recipe using DEMO_BLOCKS payloads + one FAQ row.
// FAQ is oneshot-only (not planted into theme DEMO_BLOCKS) so template demos smoke stays stable.
const ONESHOT_CREATOR_FAQ = Object.freeze(["faq", Object.freeze({
  open_first: false,
  items: Object.freeze([
    Object.freeze({ q: "【示範】如何合作？", a: "示範回答：請改成你的合作方式與聯絡管道。" }),
    Object.freeze({ q: "【示範】收費方式？", a: "示範回答：請改成你的方案與價格說明。" })
  ])
})]);

// v3.109 刀04: the scaffold profile carries NO socials. PUT /profile validates socials against the icon allowlist
//   (ig/line/youtube/…, never "instagram"), the per-platform host allowlist (example.com is nobody's host) AND the
//   social-consent ledger (刀 A-28) — a fresh page has no consent rows, so any placeholder social 422s
//   (PAGE_SOCIAL_PLATFORM_UNKNOWN / PAGE_SOCIAL_LINKS_INVALID / PAGE_SOCIAL_CONSENT_MISSING) and applyOneshot stops
//   before POST /draft ⇒ the DEMO blocks never land. The empty socials block stays as the slot the owner fills.
const ONESHOT_CREATOR_PROFILE = Object.freeze({
  display_name: "創作者（示範）",
  bio: "示範自介，請改成你的一句話。",
  status_text: "示範狀態 · 請改"
});

function pickDemoRows(theme, type, limit) {
  const rows = (DEMO_BLOCKS[theme] || []).filter((row) => row[0] === type);
  return rows.slice(0, limit).map((row) => Object.freeze([row[0], Object.freeze(JSON.parse(JSON.stringify(row[1])))]));
}

/** Format-card empty scaffold: profile → 2–3 links → socials → video → mail → faq(2). Footer = page chrome. */
function oneshotCreatorScaffold(options = {}) {
  // Demo content pool is nebula (dark) / mono (light). Map any light-kind format card
  // (mono/rosegold/starlight) onto mono demos; everything else onto nebula.
  const wanted = typeof options.theme_id === "string" ? options.theme_id : "nebula";
  const theme = (designPack.THEMES[wanted] && designPack.THEMES[wanted].kind === "light") ? "mono" : "nebula";
  // Prefer nebula creator DEMO links/video/mail (通用創作者); mono falls back when theme is light card.
  const linkTheme = theme;
  let links = pickDemoRows(linkTheme, "link", 3);
  if (links.length < 2) links = pickDemoRows("nebula", "link", 3);
  if (links.length < 3) {
    const extra = pickDemoRows("mono", "link", 3).filter((row) => !links.some((l) => l[1].label === row[1].label));
    links = links.concat(extra).slice(0, 3);
  }
  let videos = pickDemoRows("nebula", "video", 1);
  let mails = pickDemoRows("nebula", "mail", 1);
  if (!videos.length) videos = pickDemoRows(theme, "video", 1);
  if (!mails.length) mails = pickDemoRows(theme, "mail", 1);
  const blocks = Object.freeze([
    Object.freeze(["profile", Object.freeze({})]),
    ...links,
    Object.freeze(["socials", Object.freeze({})]),
    ...videos,
    ...mails,
    ONESHOT_CREATOR_FAQ
  ]);
  return Object.freeze({
    theme_id: theme,
    profile: Object.freeze(JSON.parse(JSON.stringify(ONESHOT_CREATOR_PROFILE))),
    blocks
  });
}

const DEMO_SIGNATURES = (() => {
  const set = new Set();
  for (const rows of Object.values(DEMO_BLOCKS)) {
    for (const [type, payload] of rows) {
      set.add(demoSignature({ block_type: type, payload }));
      if (type === "shop" && payload.image_asset_id) set.add(demoSignature({ block_type: type, payload: Object.assign({}, payload, { image_asset_id: null }) }));
    }
  }
  // Oneshot scaffold extras (FAQ). profile/socials payload {} intentionally NOT signed —
  // empty payload would mark every profile/socials block as demo.
  set.add(demoSignature({ block_type: ONESHOT_CREATOR_FAQ[0], payload: ONESHOT_CREATOR_FAQ[1] }));
  return set;
})();
function isDemoBlock(block, options = {}) {
  if (String(options.test_mutation || "") === "demo-never") return false;   // 刀 A:判準恆 false
  return DEMO_SIGNATURES.has(demoSignature(block));
}
// 只數 visibility=public 的示範顆(店主藏起來 = 決定不用,不擋發布);options.all 才全數。
function demoCount(blocks, options = {}) {
  return (Array.isArray(blocks) ? blocks : []).filter((block) => isDemoBlock(block, options) && (options.all === true || block.visibility === "public")).length;
}

/** Prefer demo blocks NOT counting toward free/starter caps (wired via workspace count_toward_block_limit). */
function countTowardBlockLimit(blocks) {
  return (Array.isArray(blocks) ? blocks : []).filter((block) => !isDemoBlock(block)).length;
}

/** Engineering default #3 hard-block helper — callers/publish gate should refuse when remaining > 0. */
function publishBlockedByDemo(blocks, options = {}) {
  const remaining = demoCount(blocks, options);
  return remaining > 0 ? { blocked: true, code: "PAGE_PUBLISH_DEMO_REMAINING", remaining } : { blocked: false, code: null, remaining: 0 };
}

function fail(code, details = []) { const error = new Error(code); error.code = code; error.details = details; throw error; }
function escapeHtml(value) { return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }

// 一張卡:原稿 .tcard > .phone > .scr.<theme>,加形狀 class。回的是片段,殼(選取表單、目前標記)由套版頁自己包。
function demoCardHtml(theme, shape, options = {}) {
  if (!DEMOS[theme]) fail("PAGE_TEMPLATE_DEMO_UNKNOWN", [theme]);
  const shapeId = shape === undefined ? designPack.DEFAULT_SHAPE : String(shape);
  if (!designPack.SHAPES[shapeId]) fail("PAGE_DESIGN_SHAPE_UNKNOWN", [shapeId]);
  const mutation = String(options.test_mutation || "");
  const shapeClass = shapeId === designPack.DEFAULT_SHAPE ? "" : " shape-" + shapeId;
  const markup = mutation === "drop-demo-blocks" ? "" : DEMOS[theme].markup;
  return '<div class="tcard"><div class="phone"><div class="scr ' + theme + shapeClass + '">' + markup + "</div></div>" +
    // 原稿的說明是 <h3 class="hh4">;後台頁 h1 之下沒有 h2,24 張卡各一個 h3 會跳號(admin-dashboard smoke 守標題層級),
    // 所以說明用 <p class="hh4">:同一條 .tcard .hh4 規則,長相逐像素同,只拿掉標題語意。
    (options.caption === false ? "" : '<p class="hh4">' + escapeHtml(DEMOS[theme].title) + "</p><p>" + escapeHtml(DEMOS[theme].tagline) + "</p>") +
    "</div>";
}

function demoRulesCss() {
  return DEMO_RULES.map(([selector, body]) => selector + "{" + body + "}").join("\n");
}

// 每一套示範用到的區塊 class(給 smoke:每一張卡都要含該套的每一種區塊)。
function demoBlockClasses(theme) {
  if (!DEMOS[theme]) fail("PAGE_TEMPLATE_DEMO_UNKNOWN", [theme]);
  const found = new Set();
  for (const match of DEMOS[theme].markup.matchAll(/class="(b-[a-z]+)/g)) found.add(match[1]);
  return [...found].sort();
}

module.exports = { SOURCE_MOCK, SOURCE_PAGE_MARKER, INLINE_STYLE_TO_CLASS, DEMO_RULES, DEMOS, DEMO_BLOCKS, DEMO_SIGNATURES, ONESHOT_CREATOR_FAQ, ONESHOT_CREATOR_PROFILE, oneshotCreatorScaffold, demoCardHtml, demoRulesCss, demoBlockClasses, demoBlocks, demoImageIds, demoSignature, isDemoBlock, demoCount, countTowardBlockLimit, publishBlockedByDemo };
