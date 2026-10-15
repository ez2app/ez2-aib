"use strict";
// 316(RL-106 乙):八套套版的格子配置表——**唯一真相源**,給設計填,不是佔位。
//   { <theme>: { background: null | {preset} | {custom:{c1,c2,dir}}, spans: [ null | {cols,rows} × DEMO_BLOCKS[theme].length ] } }
//   spans 與 template-demos.DEMO_BLOCKS[theme] 一一對應(圖顆不種時按「第 k 個非 image 顆」對位,不按索引);
//   cta / shop 的位置**必須 null**(渲染器不畫它們的 span,287-B 退化);null = 滿寬(今天的樣子)。
//   v1:八套 background null(= 各自 screen,原稿 v41 沒有第二層背景)、spans 全 null(原稿八張卡是單欄;A23 候裁)。
//   改格子只改這張表,不改碼;尺 R6 釘「表長度 = DEMO_BLOCKS 長度、cta/shop 位置 null、每格形狀合契約」。
const templateDemos = require("./ez2-page-template-demos.js");

// 316 v1.1(RL-114/117):每套多一個 fixed = 三固定格「頭像 / 社群 / 封面」的格子值(表是給設計填的,改格子只改表):
//   avatar.size 直徑 px(v1.1 八套 78 = 原稿 .b-ava);socials.placement header|block(block ⇒ 整套重來時在示範序列第 index 位種一顆
//   socials 區塊;A26 裁乙:八套 header、index null);cover.aspect 只准 "3:1"|"2:1"|"16:9"(封面只准橫幅,pop 原稿 4:3 不准 ⇒ 2:1),
//   max_height 240–480。尺 R6' 逐套釘「三鍵齊、值在字面集合裡」。
const COVER_ASPECTS = Object.freeze(["3:1", "2:1", "16:9"]);
const SOCIALS_PLACEMENTS = Object.freeze(["header", "block"]);
const DEFAULT_FIXED = Object.freeze({ avatar: Object.freeze({ size: 78 }), socials: Object.freeze({ placement: "header", index: null }), cover: Object.freeze({ aspect: "3:1", max_height: 360 }) });
const FIXED_V11 = Object.freeze({
  nebula: { cover: { aspect: "3:1", max_height: 360 } },
  mono: { cover: { aspect: "2:1", max_height: 400 } },
  dawn: { cover: { aspect: "3:1", max_height: 360 } },
  forest: { cover: { aspect: "16:9", max_height: 420 } },
  rosegold: { cover: { aspect: "2:1", max_height: 400 } },
  starlight: { cover: { aspect: "3:1", max_height: 360 } },
  pop: { cover: { aspect: "2:1", max_height: 400 } },
  ocean: { cover: { aspect: "16:9", max_height: 420 } }
});
const TEMPLATE_LAYOUT = Object.freeze(Object.fromEntries(Object.keys(templateDemos.DEMO_BLOCKS).map((theme) => [theme, Object.freeze({
  background: null,
  spans: Object.freeze(templateDemos.DEMO_BLOCKS[theme].map(() => null)),
  fixed: Object.freeze({
    avatar: Object.freeze({ size: 78 }),
    socials: Object.freeze({ placement: "header", index: null }),
    cover: Object.freeze(Object.assign({}, DEFAULT_FIXED.cover, (FIXED_V11[theme] || {}).cover || {}))
  })
})])));

// 三處讀表都走這裡(渲染封面 CSS、applyTemplate 種 socials、套版頁預覽卡);缺 theme 回預設 78 / header / 3:1-360。
//   test_mutation "layout-socials-block":forest 的 socials 改成 block / index 1(S7 用來走「種 socials 顆」那條路;v1.1 八套都是 header)。
function fixedFor(theme, options = {}) {
  const layout = TEMPLATE_LAYOUT[theme];
  const fixed = layout ? layout.fixed : DEFAULT_FIXED;
  if (String(options.test_mutation || "") === "layout-socials-block" && theme === "forest") {
    return Object.freeze({ avatar: fixed.avatar, socials: Object.freeze({ placement: "block", index: 1 }), cover: fixed.cover });
  }
  return fixed;
}
function coverAspectRatio(aspect) {
  const [w, h] = String(aspect).split(":").map(Number);
  return Number.isFinite(w) && Number.isFinite(h) && h > 0 ? w + "/" + h : "3/1";
}

// 示範顆序列(帶/不帶圖)對上表:回與 blocks 等長的 span 陣列。不帶圖時,表裡 image 位置跳過,其餘照原序對位。
function spansFor(theme, blocks) {
  const layout = TEMPLATE_LAYOUT[theme];
  if (!layout) return blocks.map(() => null);
  const kinds = templateDemos.DEMO_BLOCKS[theme].map(([type]) => type);
  const out = [];
  let cursor = 0;
  for (const block of blocks) {
    while (cursor < kinds.length && kinds[cursor] !== block.block_type) cursor += 1;
    out.push(cursor < kinds.length ? layout.spans[cursor] : null);
    cursor += 1;
  }
  return out;
}

module.exports = { TEMPLATE_LAYOUT, COVER_ASPECTS, SOCIALS_PLACEMENTS, DEFAULT_FIXED, spansFor, fixedFor, coverAspectRatio };
