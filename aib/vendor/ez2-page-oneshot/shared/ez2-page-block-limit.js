"use strict";
// 大綱 303(RL-096):方框總數上限走 133 權益模組,不寫死在編輯器裡。
//   讀得到權益且解得出方案(不 degraded)⇒ 查常數表 BLOCK_LIMITS_BY_PLAN;沒有權益資料 / 判不了(過期、無效、停權)⇒ DEFAULT_BLOCK_LIMIT(今天的硬上限 50,
//   與 133 落地前所有頁的行為逐位元組同)。表裡的數字是 RL-096 的建議值(免費 12 / 標準 24 / 專業 48;八套範本 7–9 格、合計 66),**候 the maintainer 定**;
//   任何方案都不超過硬上限。示範顆預設不計入(workspace.count_toward_block_limit → countTowardBlockLimit);九宮占格數(span)不影響——數的是顆不是格。
// 刀 block-limit-ignore-entitlement(registration test_mutation):有權益也回 DEFAULT ⇒ L1 必紅。
const planContract = require("./ez2-page-plan-contract.js");

const DEFAULT_BLOCK_LIMIT = 50;
const BLOCK_LIMITS_BY_PLAN = Object.freeze({ free: 12, starter: 24, pro: 48, business: 48, agency: 48 });

function blockLimitFor(entitlement, nowIso) {
  if (!entitlement) return DEFAULT_BLOCK_LIMIT;
  const resolved = planContract.resolveEffectivePlan(entitlement, nowIso);
  if (!resolved || resolved.degraded || !resolved.plan_id) return DEFAULT_BLOCK_LIMIT;
  const limit = BLOCK_LIMITS_BY_PLAN[resolved.plan_id];
  return Number.isInteger(limit) && limit > 0 ? Math.min(limit, DEFAULT_BLOCK_LIMIT) : DEFAULT_BLOCK_LIMIT;
}

module.exports = { DEFAULT_BLOCK_LIMIT, BLOCK_LIMITS_BY_PLAN, blockLimitFor };
