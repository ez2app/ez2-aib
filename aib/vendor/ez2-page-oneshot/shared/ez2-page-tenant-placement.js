"use strict";

// ez2-page-tenant-placement.js — 租戶落點:形態組合規則 + 池與主機的權威映射(2026-07-26)
//
// the maintainer 定的兩條產品規則,這裡是它們的機器形式:
//
//   1. 形態組合:每個帳號一定有 ez2.page/{slug}(子資料夾,基礎)。在它之上可以再加
//      **子網域 或 私有網域,但不能兩者都有**。真的兩者都要,就開兩個獨立帳號。
//      理由不只是產品潔癖:同一份內容掛兩個權威網址會在搜尋上自我競爭,
//      分析數據被切成兩半,而「這個品牌到底叫什麼」對訪客也是模糊的。
//
//   2. 分池:方案等級決定池,池內再指派主機(自動或人工)。
//      免費用戶掛掉與付費客戶掛掉的損失差好幾個量級,混在一起等於用最貴的
//      可用性標準服務最不付錢的人,而且一個失控租戶會拖垮同台的付費客戶。
//
// 以及一條搬遷後的定位規則:**一旦移出,移出的那個形態就是主要入口**
// (primary),平台上的舊入口轉為轉址而不是消失。
//
// 職責邊界:route-contract 驗單條 route 的合法性;tier-serving-contract 管快取分層
// 與搬遷狀態機;本模組只回答兩個問題——「這個租戶的形態組合合不合法」與
// 「他現在住哪一台」。

const routeContract = require("./ez2-page-route-contract.js");

const VERSION = "2026-07-26.ez2-page-tenant-placement.v1";

// 形態。base 是與生俱來的,upgrade 兩者互斥。
const BASE_FORM = "platform_path";
const UPGRADE_FORMS = Object.freeze(["platform_subdomain", "custom_domain"]);

// 池。等級決定池,不是主機——主機是池內的實作細節,可以換。
const POOLS = Object.freeze({
  free: Object.freeze({
    // 免費只有 bio 頁(1.4KB、零 JS),所以內容預算刻意很小 ——
    // 它不是限制,是「這一池本來就不放文章站」的機器形式。
    max_content_bytes_per_host: 8 * 1024 * 1024 * 1024,
    key: "free", tier_label: "免費",
    // 高密度:一台放很多,單台故障影響面大但商業損失可承受
    max_tenants_per_host: 500,
    // 免費池不給獨立主機:那是付費才有的隔離
    isolation: "shared", movable: false
  }),
  paid: Object.freeze({
    // 政策預算,不是量到的磁碟。實際容量要另外查,兩者不可互相冒充。
    max_content_bytes_per_host: 300 * 1024 * 1024 * 1024,
    key: "paid", tier_label: "付費",
    max_tenants_per_host: 100,
    isolation: "shared", movable: true
  }),
  enterprise: Object.freeze({
    // 獨佔主機:成本是機器不是內容量,所以這一池不設共享預算。
    max_content_bytes_per_host: null,
    key: "enterprise", tier_label: "企業",
    // 企業客戶可獨佔一台:爆炸半徑=自己
    max_tenants_per_host: 1,
    isolation: "dedicated", movable: true
  })
});

const PLACEMENT_MODES = Object.freeze(["auto", "manual"]);

// ── 內容量維度(2026-07-28)──
//
// ★ 為什麼要加這一維:原本的密度只有「每台幾個租戶」,那對 bio 頁成立、對文章站失效。
//   一個有 10 萬篇文章的租戶會吃掉十個人的份,而方案階梯裡**沒有任何一個維度
//   對應資源消耗** —— 網址形態、追蹤模式、主機池三個都跟內容量無關。
//   所以一個 Pro 客戶可能比 Agency 客戶重一百倍,而他付得比較少。
//
// ★★ 下面的換算是**估算不是量測**。真實值要等第一個文章站進來才量得到,
//   在那之前引用它的地方都必須一起說這件事(與速度正本「沒量到不算通過」同源)。
const BYTES_PER_ARTICLE_ESTIMATE = Object.freeze({
  value: 320 * 1024,          // 渲染後 HTML 約 20KB + 平均兩張 AVIF 圖約 300KB
  measured: false,            // ★ 沒有量過。改成 true 之前必須附收據。
  basis: "estimate_html_20kb_plus_two_avif_images"
});

// the maintainer 2026-07-28 裁定的級距(以文章數表示,對人講的單位)。
const TENANT_ARTICLE_DESIGN_TARGET = 3000;   // 每天一篇連續十年 ≈ 3650,涵蓋絕大多數真實客戶
const TENANT_ARTICLE_SOFT_CAP = 10000;       // 超過要人工看落點,不是拒絕
const TENANT_ARTICLE_DEDICATED = 50000;      // 超過必須獨佔主機,與方案無關

function articlesToBytes(articles) {
  const n = Number.isFinite(articles) && articles > 0 ? Math.floor(articles) : 0;
  return n * BYTES_PER_ARTICLE_ESTIMATE.value;
}

function activeRoutes(routes = []) {
  return (routes || []).filter((route) => route && route.status === "active");
}

// 形態組合檢查。這是綁定當下就要擋的門,不是事後稽核——事後才發現,
// 使用者已經對外公布了那個網址,收回的代價由他承擔。
function validateFormSet(routes = []) {
  const errors = [];
  const active = activeRoutes(routes);
  const base = active.filter((route) => route.route_type === BASE_FORM);
  const upgrades = active.filter((route) => UPGRADE_FORMS.includes(route.route_type));
  const kinds = [...new Set(upgrades.map((route) => route.route_type))];

  if (!base.length) errors.push("placement_base_folder_missing");
  if (base.length > 1) errors.push("placement_multiple_base_folders");
  if (kinds.length > 1) errors.push("placement_subdomain_and_custom_domain_conflict");
  // 同一種升級形態也只能有一個權威入口(兩個子網域指同一份內容=同樣的自我競爭)
  for (const kind of kinds) {
    if (upgrades.filter((route) => route.route_type === kind).length > 1) {
      errors.push("placement_multiple_" + kind);
    }
  }
  const primaries = active.filter((route) => route.is_primary);
  if (primaries.length !== 1) errors.push(primaries.length ? "placement_multiple_primaries" : "placement_no_primary");

  return {
    ok: errors.length === 0,
    errors: [...new Set(errors)].sort(),
    base_slug: base.length === 1 ? routeContract.normalizePath(base[0].path).replace(/^\//, "") : null,
    upgrade_form: kinds.length === 1 ? kinds[0] : null,
    // 升級形態存在時,主要入口應該是它(搬出去的就是主要的);否則是子資料夾
    expected_primary: kinds.length === 1 ? kinds[0] : BASE_FORM
  };
}

// 想新增一個升級形態時,先問能不能加。拒絕時要說得出替代路徑,
// 否則使用者只會看到「不行」而不知道該怎麼辦。
function canAddForm(routes = [], form) {
  if (form === BASE_FORM) {
    return activeRoutes(routes).some((route) => route.route_type === BASE_FORM)
      ? { ok: false, reason: "base_folder_already_exists", remedy: null }
      : { ok: true, reason: null, remedy: null };
  }
  if (!UPGRADE_FORMS.includes(form)) return { ok: false, reason: "unknown_form:" + form, remedy: null };
  const existing = validateFormSet(routes);
  if (existing.upgrade_form && existing.upgrade_form !== form) {
    return {
      ok: false,
      reason: "upgrade_form_conflict",
      conflicting_form: existing.upgrade_form,
      // 產品規則:兩者都要就開兩個獨立帳號(不是偷偷放行,也不是無解)
      remedy: "open_separate_account"
    };
  }
  if (existing.upgrade_form === form) return { ok: false, reason: "form_already_present", remedy: null };
  return { ok: true, reason: null, remedy: null };
}

// 搬遷後的主要入口規則:移出的那個形態成為 primary,平台入口轉址不刪。
// 回傳「應該長成什麼樣」,讓呼叫端拿去比對實際狀態,而不是直接改資料。
function expectedRoutesAfterMoveOut(routes = []) {
  const form = validateFormSet(routes);
  if (!form.ok) return { ok: false, errors: form.errors };
  if (!form.upgrade_form) return { ok: false, errors: ["placement_move_out_requires_upgrade_form"] };
  const active = activeRoutes(routes);
  return {
    ok: true,
    expectations: active.map((route) => ({
      hostname: routeContract.normalizeHostname(route.hostname),
      path: routeContract.normalizePath(route.path),
      // 升級形態成為唯一 active primary;平台子資料夾保留但轉址
      expected_status: route.route_type === form.upgrade_form ? "active" : "redirecting",
      expected_is_primary: route.route_type === form.upgrade_form
    }))
  };
}

// ── 池與主機的權威映射 ──
//
// 這是「訪客來了要打哪台、金流回呼轉去哪、AI 任務算誰的」的單一答案來源。
// 刻意讓它是純資料檢查:真正的儲存由控制面負責,本模組只保證形狀與規則。
// 單一租戶的內容量分級。回三態式的建議而不是布林:
// **超過軟上限不是拒絕,是「先看一眼再決定放哪台」** —— 拒絕一個十萬篇的客戶
// 是拒絕最好的案例,但把他丟進共享池是拖垮同台的兩百個人。
function classifyTenantVolume(articles) {
  const n = Number.isFinite(articles) && articles > 0 ? Math.floor(articles) : 0;
  const bytes = articlesToBytes(n);
  if (n >= TENANT_ARTICLE_DEDICATED) {
    return Object.freeze({
      band: "dedicated_required", articles: n, bytes,
      requires_pool: "enterprise", requires_human_review: true,
      reason: "content_volume_requires_dedicated_host",
      estimate_measured: BYTES_PER_ARTICLE_ESTIMATE.measured
    });
  }
  if (n >= TENANT_ARTICLE_SOFT_CAP) {
    return Object.freeze({
      band: "review_required", articles: n, bytes,
      requires_pool: null, requires_human_review: true,
      reason: "content_volume_above_soft_cap",
      estimate_measured: BYTES_PER_ARTICLE_ESTIMATE.measured
    });
  }
  return Object.freeze({
    band: "standard", articles: n, bytes,
    requires_pool: null, requires_human_review: false, reason: null,
    estimate_measured: BYTES_PER_ARTICLE_ESTIMATE.measured
  });
}

function validatePlacement(placement = {}, options = {}) {
  const errors = [];
  const pool = POOLS[String(placement.pool || "")];
  if (!pool) errors.push("placement_pool_unknown:" + String(placement.pool));
  if (!String(placement.tenant_id || "").trim()) errors.push("placement_tenant_id_missing");
  if (!String(placement.host_uid || "").trim()) errors.push("placement_host_uid_missing");
  if (!PLACEMENT_MODES.includes(String(placement.mode || ""))) errors.push("placement_mode_invalid");
  if (placement.assigned_at && Number.isNaN(Date.parse(placement.assigned_at))) errors.push("placement_assigned_at_invalid");

  // 專屬池的主機不得同時住著別人;共享池要在容量內。
  const cohabitants = Number(options.tenants_on_host);
  if (pool && Number.isFinite(cohabitants)) {
    if (pool.isolation === "dedicated" && cohabitants > 1) errors.push("placement_dedicated_host_shared");
    if (cohabitants > pool.max_tenants_per_host) errors.push("placement_host_over_capacity");
  }

  // ★ 內容量是第二個維度:兩個條件任一超過就不該再指派。
  //   只看租戶數會讓「三個大文章站塞在同一台」看起來完全合法。
  const hostBytes = Number(options.content_bytes_on_host);
  if (pool && Number.isFinite(hostBytes) && pool.max_content_bytes_per_host !== null
    && hostBytes > pool.max_content_bytes_per_host) {
    errors.push("placement_host_over_content_budget");
  }
  // 免費池不可搬遷:隔離是付費才有的東西,寫在池規則裡而不是散在流程各處
  if (pool && placement.moved_out === true && !pool.movable) errors.push("placement_pool_not_movable");

  return { ok: errors.length === 0, errors: [...new Set(errors)].sort(), pool: pool ? pool.key : null };
}

// 換池(升級/降級方案)時的落點結果。降級到免費且目前已搬出=必須先搬回來,
// 否則會出現「免費用戶佔著一台專屬主機」這種與池規則矛盾的狀態。
function planPoolChange(current = {}, nextPool) {
  const target = POOLS[String(nextPool || "")];
  if (!target) return { ok: false, errors: ["placement_pool_unknown:" + String(nextPool)] };
  const from = POOLS[String(current.pool || "")];
  if (!from) return { ok: false, errors: ["placement_pool_unknown:" + String(current.pool)] };
  const actions = [];
  if (current.moved_out === true && !target.movable) actions.push("move_back_to_shared_pool_first");
  if (from.isolation === "dedicated" && target.isolation !== "dedicated") actions.push("release_dedicated_host");
  if (target.isolation === "dedicated" && from.isolation !== "dedicated") actions.push("provision_dedicated_host");
  if (from.key !== target.key) actions.push("reassign_host_within_" + target.key + "_pool");
  return { ok: true, from: from.key, to: target.key, required_actions: actions };
}

module.exports = {
  VERSION, BASE_FORM, UPGRADE_FORMS, POOLS, PLACEMENT_MODES,
  BYTES_PER_ARTICLE_ESTIMATE, TENANT_ARTICLE_DESIGN_TARGET,
  TENANT_ARTICLE_SOFT_CAP, TENANT_ARTICLE_DEDICATED,
  articlesToBytes, classifyTenantVolume,
  validateFormSet, canAddForm, expectedRoutesAfterMoveOut,
  validatePlacement, planPoolChange
};
