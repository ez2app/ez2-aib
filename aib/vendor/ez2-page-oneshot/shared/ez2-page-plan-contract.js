"use strict";

// ez2-page-plan-contract.js — 方案與權益契約(Node 版,2026-07-27)
//
// 這個模組回答兩個問題,而且是唯一答案來源:
//   1. 「Starter 這個方案能做什麼」            → PLANS(規格,全平台一份)
//   2. 「這個客戶現在真的有什麼權利」          → Entitlement(事實,每客戶一筆)
//
// ★ 為什麼分成兩個:方案是規格,改了所有人一起改;權益是事實,而且**不是我們說了算**。
//   付款真相在 EZ2.APP(WordPress / Woo / RCP),Ez2.PAGE 只讀同步後的快取,
//   永不自行判定付款(30 §7、§9;21 §7)。所以本模組**沒有任何「發放權益」的函式**——
//   只有驗證與解讀。想在 PAGE 這一側憑空給權利,寫不出來是刻意的。
//
// ★ 「Node 版」的意思:21 §4.3/§4.4 的語意保留,載體換掉。原本的 blog_id /
//   table_prefix / rcp_membership_id / woo_product_id 是 WordPress Multisite 的欄位,
//   the maintainer 2026-07-27 拍板載體改 Node 之後全部作廢(canon v5 §一)。這裡改用
//   provider-neutral 的 authority_ref:PAGE 不需要知道對面是 Woo 還是別的東西,
//   只需要能把權益追回它的來源。
//
// 數值決定的來源:docs/decisions/ez2-page-plan-tier-decision-worksheet.md
//   the maintainer 2026-07-27 裁定 G-1 / G-2 / G-3 / G-5 / G-6;**G-4 的八個 limits 數值仍未定**。
//
// ★★ 未定的數值一律是 null,而 null 的意思是「未定」不是「零」也不是「無限」。
//   limitFor() 因此回三態:allowed / denied / **undecided**。
//   消費端必須明確處理 undecided,不得把它當成放行——那正是「無證據顯示 unknown,
//   不以 0 充數」那條鐵律在這一層的形狀。把未定悄悄當成放行,就是在假裝已經決定過。
//
// 職責邊界(刻意不重造):
//   - 形態組合合不合法、住哪一台   → ez2-page-tenant-placement(本模組只說「哪一階可以用哪些形態」)
//   - 單條 route 合不合法          → ez2-page-route-contract
//   - 追蹤模式的效能門檻            → ez2-page-speed-rwd-policy(本模組只說「哪一階可以選哪些模式」)
//   本模組不複製上面任何一份的判定,只引用它們的鍵名並在 audit 時比對是否漂移。

const tenantPlacement = require("./ez2-page-tenant-placement.js");
/* ★ 2026-09-05(裁定 b):本檔只用到 speed policy 的一張常數表 MODES,而 require 整支會把
   speed-rwd-policy → performance-policy → static-bundle → vcard / theme-renderer / social-icons …
   整棵樹拖進 bridge 的部署閉包(多 19 支 PAGE 渲染檔),而畫布配額那條路一次都不會執行到它們。
   ⇒ 改讀葉子模組。**不要改回直接 require 那支 speed-rwd-policy 模組** ——
   改回去之後**沒有任何行為會改變**,所以行為面的臂一格都不會紅;
   看得見它的只有 tools/ez2-page-plan-contract-closure-smoke.js。 */
const speedModes = require("./ez2-speed-modes.js");

const VERSION = "2026-07-27.ez2-page-plan-contract.v1";
const DECISION_SOURCE = "docs/decisions/ez2-page-plan-tier-decision-worksheet.md";

// 五方案。順序即階梯順序,升級路徑只能往右。
// Creator 是 05 藍圖的舊名,已由 the maintainer 裁定作廢=Starter,故不出現在這裡。
const PLAN_IDS = Object.freeze(["free", "starter", "pro", "business", "agency"]);

// 客戶可選的追蹤模式(marketing_site 是官網自己的模式,不賣給客戶)。
const CUSTOMER_TRACKING_MODES = Object.freeze(["performance_first", "balanced", "full_pixel"]);

// 畢業之後舊 route 的 308 保留期。the maintainer 2026-07-27 裁定 24 個月,並且寫進條款當賣點。
// ★ 這是一個**真的義務**:客戶已經不付錢了我們還要幫他轉址兩年。所以它是契約常數
//   不是設定值——改它等於改對外承諾,要走決策流程不是改一行。
const ROUTE_REDIRECT_RETENTION_MONTHS = 24;

// limits 的鍵沿用 21 §4.3,**不改名**(改名會讓兩邊對不起來)。
// ★ 2026-09-05 N18-2:這裡原本寫的是「八個鍵……**不新增**不改名」。新增三個畫布容量軸之後
//   那半句已經不成立,所以就地改掉 —— **留著一句已經不成立的規矩比沒有規矩更糟**:
//   下一棒會照它拒絕一個合法的變更,而它讀起來跟真的一樣。
//   「不改名」仍然有效(它的理由是兩邊對不起來);「不新增」由艦長 2026-09-05 裁定解除。
//   新增的正當程序 = **同時**改 LIMIT_KEYS 與 limits() 預設(auditPlanContract 兩個方向都會擋),
//   並到 tools/ez2-page-plan-contract-smoke.js 的「鍵一個都不准少」那格逐名加上去。
// 已決定「不設上限」的表示法。它不是 Infinity(不能序列化)也不是 null(那是未定)。
const UNLIMITED = "unlimited";

const LIMIT_KEYS = Object.freeze([
  "pages", "blocks_per_page", "monthly_views",
  "quest_blocks", "product_blocks", "meeting_blocks",
  "custom_domain", "remove_branding",
  // 畫布容量軸(N18-2,2026-09-05)。只收**存量**型:數得出「現在有幾個」就能擋,
  // 不需要餘額帳本。用量型(share_views / exports)不在這裡 —— 它們要的計量器還沒接上。
  "canvas_count", "guests_per_canvas", "nested_depth",
  // canvas_versions(2026-09-05 第四軸):每張畫布保留幾版。
  // ★ 它的上限**不是**這份合約說了算 —— 真正在擋的是
  //   shared/ez2-meeting-version-retention.js 的 CANVAS_VERSIONS_KEEP(畫布與總結共用的單一常數)。
  //   方案值只能 **≤** 那個常數:填得比它大,客戶看到的數字與實際 FIFO 會分岔,
  //   而在那一格出現之前,方案表這一側不會有任何東西紅。
  //   守它的格在 tools/ez2-page-plan-contract-smoke.js(不在本檔:合約不該相依 meeting 模組)。
  "canvas_versions"
]);

// ★ UNLIMITED 與 null 是兩件不同的事,這個區別是本模組存在的理由之一:
//   null      = 還沒決定(候 the maintainer)
//   UNLIMITED = 已經決定了,而且決定的內容是「不設上限」
//   把後者寫成 null 會讓一個已完成的決定看起來像欠債;把前者寫成 UNLIMITED
//   則是把沒決定的事偷偷當成決定過。兩種錯都會在半年後被當成事實引用。
//
// null = 未定(候 the maintainer),數字/布林/UNLIMITED = 已定。
// ★ remove_branding 是 null:the maintainer 裁定 G-1/2/3/5/6 時**沒有裁定 G-4**,
//    先前這裡填的 Pro 起 true 是 AI 的提案被誤當成決定,2026-07-27 更正。
//    custom_domain 則不同——它由 G-2「自有網域 Pro 起」直接導出,是真的已定。
// quest/product/meeting 三項刻意是 0 而不是 null:那三種區塊語彙 repo 裡還不存在
// (見 ez2-page-capability-inventory.md §三),所以 0 是**已知的事實**不是未決定。
// 等語彙做出來,要重開決策單改這三列,不可以在這裡順手改。
function limits(overrides) {
  return Object.freeze(Object.assign({
    pages: null, blocks_per_page: null,
    // ★ monthly_views = **每月免費額度**,不是餘額。額度是規格(這個方案給多少),
    //   餘額是事實(這個租戶現在還剩多少)。餘額有兩個桶——每月重置的免費額度、
    //   以及星塵充填後永不過期的部分——它跟 entitlement 同類,是租戶狀態不是方案規格,
    //   **所以餘額帳本不住在這張表裡,是另一個還沒建的東西**。
    //   兌換率:1 星塵 = 5 萬 views(the maintainer 2026-07-28)。
    monthly_views: null,
    quest_blocks: 0, product_blocks: 0, meeting_blocks: 0,
    custom_domain: false, remove_branding: null,
    // ★ 這四個畫布容量軸原本全是 null(=未決定)。09-06 01:0x 裁掉其中三個,**第四個仍是 null**:
    //   b0590 派、the maintainer 01:01 授權「第一版只賣 free 與 pro 兩階」。逐階的值寫在下面各 PLANS 條目,
    //   不寫在這個預設裡 —— 預設留 null 的用意沒變:**沒有人裁過的軸要看得出來它沒被裁過**。
    //   ★ nested_depth **刻意不動**,而且這一句是有出處的:09-05 我曾把一句自己標成弱讀數的話
    //     寫進建議欄,艦長照那句裁了這一軸,事後撤回。⇒ 它回到未決定,今天的裁定也沒有提到它。
    //     要動它需要一張新的決策單,不可以在這裡順手補一個看起來合理的數字。
    canvas_count: null, guests_per_canvas: null, nested_depth: null, canvas_versions: null
  }, overrides));
}

const PLANS = Object.freeze({
  free: Object.freeze({
    id: "free", label: "Free", rank: 0,
    pool: "free",
    forms: Object.freeze(["platform_path"]),
    tracking_modes: Object.freeze(["performance_first"]),
    // Free 只有 bio 頁;小創作者的 bio 一個月大約幾百到三千次,1 萬很寬。
    limits: limits({ custom_domain: false, monthly_views: 10000, canvas_count: 5, guests_per_canvas: 5, canvas_versions: 10 })
    /* free 畫布 5(不是 3):the maintainer 01:01 授權 b0590 建議,b0590 01:1x 調整 free 畫布 3→5。
       ★ 這個數字是被一支臂逼出來的,理由值得留著:先寫 3 的時候 meeting-canvas-topics-route 從全綠變 402 ——
         租戶已有 1 張、議題拆分要開 3 張,1+3=4 > 3。**「free 只給 3 張」與「議題拆分是核心流程」今天互相咬。**
       ★ b0590 裁的時候一併否掉另外兩個看起來更省事的答案,理由寫在這裡免得下一個人再提一次:
         治具改用 pro ⇒ **把產品衝突藏起來**(臂會綠,而真的 free 使用者照樣撞牆);
         拆分不計入 canvas_count ⇒ 配額就不再數真畫布,那條路一開,以後每個功能都可以主張自己不計入。
       ⇒ 改的是**方案的數字**,不是判準也不是治具:free 至少要容納「父 + 拆出來的三張」。 */
  }),
  starter: Object.freeze({
    id: "starter", label: "Starter", rank: 1,
    pool: "paid",
    // ★ 子網域從 Starter 起,而不是 21 的 07-10 附錄寫的「免費就有」。
    //   理由記在決策單:子網域是不可回收的命名空間,而且是最容易被拿去冒名的形態。
    forms: Object.freeze(["platform_path", "platform_subdomain"]),
    tracking_modes: Object.freeze(["performance_first"]),
    // 第一個有 blog 的方案。新站做起 SEO 之後約 5 千到 5 萬,10 萬讓他不會第一個月就撞牆。
    limits: limits({ custom_domain: false, monthly_views: 100000, canvas_count: 50, guests_per_canvas: 25, canvas_versions: 10 })   // 先同 pro(佔位)—— 第一版只賣 free 與 pro,starter 沒有真客戶;要賣它必須重開決策單
  }),
  pro: Object.freeze({
    id: "pro", label: "Pro", rank: 2,
    pool: "paid",
    forms: Object.freeze(["platform_path", "platform_subdomain", "custom_domain"]),
    tracking_modes: Object.freeze(["performance_first", "balanced"]),
    // 綁自有網域=認真經營。成熟的內容站或公司網站約 5 萬到 30 萬。
    limits: limits({ custom_domain: true, monthly_views: 500000, canvas_count: 50, guests_per_canvas: 25, canvas_versions: 10 })   // the maintainer 01:01 授權:第一版 pro 的實際賣點
  }),
  business: Object.freeze({
    id: "business", label: "Business", rank: 3,
    pool: "paid",
    forms: Object.freeze(["platform_path", "platform_subdomain", "custom_domain"]),
    tracking_modes: Object.freeze(["performance_first", "balanced", "full_pixel"]),
    limits: limits({ custom_domain: true, monthly_views: 2000000, canvas_count: 50, guests_per_canvas: 25, canvas_versions: 10 })   // 先同 pro(佔位)
  }),
  agency: Object.freeze({
    id: "agency", label: "Agency", rank: 4,
    pool: "enterprise",
    forms: Object.freeze(["platform_path", "platform_subdomain", "custom_domain"]),
    tracking_modes: Object.freeze(["performance_first", "balanced", "full_pixel"]),
    // ★ Agency 走 enterprise 池 = 獨佔主機,成本是**機器不是流量**。
    //   同一台不管跑 100 萬還是 1000 萬次,我們付的錢一樣,所以在這一階按 views
    //   計價邏輯不通:量大到要第二台機器時,那是加購主機不是加購 views。
    limits: limits({ custom_domain: true, monthly_views: UNLIMITED, canvas_count: 50, guests_per_canvas: 25, canvas_versions: 10 })   // 先同 pro(佔位;★ 注意 monthly_views 是 UNLIMITED 而畫布不是 —— 這兩軸今天沒有一起裁)
  })
});

// 權益狀態。pending 是「同步中尚未確認」,不是「大概有」——它不給任何權利。
const ENTITLEMENT_STATUSES = Object.freeze(["active", "pending", "expired", "suspended"]);

// 權益的來源權威。只有這一個,而且不可以在這裡新增第二個:
// 多一個來源就是多一套付款真相,那是 30 §12 明文禁止的。
const ENTITLEMENT_AUTHORITIES = Object.freeze(["EZ2.APP"]);

function plan(planId) {
  return PLANS[String(planId || "").trim().toLowerCase()] || null;
}

// 這一階可不可以用這個網址形態。拒絕時要說得出「升到哪一階才有」,
// 否則使用者只看到不行而不知道下一步——canAddForm 的既有慣例就是這樣。
function allowsForm(planId, form) {
  const p = plan(planId);
  if (!p) return { ok: false, reason: "plan_unknown:" + String(planId), upgrade_to: null };
  if (!ALL_FORMS.includes(form)) return { ok: false, reason: "form_unknown:" + String(form), upgrade_to: null };
  if (p.forms.includes(form)) return { ok: true, reason: null, upgrade_to: null };
  const upgrade = PLAN_IDS.find((id) => PLANS[id].rank > p.rank && PLANS[id].forms.includes(form)) || null;
  return { ok: false, reason: "form_not_in_plan", upgrade_to: upgrade };
}

function allowsTrackingMode(planId, mode) {
  const p = plan(planId);
  if (!p) return { ok: false, reason: "plan_unknown:" + String(planId), upgrade_to: null };
  if (!CUSTOMER_TRACKING_MODES.includes(mode)) {
    return { ok: false, reason: "tracking_mode_unknown:" + String(mode), upgrade_to: null };
  }
  if (p.tracking_modes.includes(mode)) return { ok: true, reason: null, upgrade_to: null };
  const upgrade = PLAN_IDS.find((id) => PLANS[id].rank > p.rank && PLANS[id].tracking_modes.includes(mode)) || null;
  return { ok: false, reason: "tracking_mode_not_in_plan", upgrade_to: upgrade };
}

function poolFor(planId) {
  const p = plan(planId);
  return p ? p.pool : null;
}

// ★ 三態。undecided 不是 allowed 也不是 denied——消費端必須自己決定要怎麼處理,
//   而且應該把它記下來。悄悄放行等於假裝這個數字已經決定過了。
function limitFor(planId, key, used) {
  const p = plan(planId);
  if (!p) return { state: "denied", reason: "plan_unknown:" + String(planId), value: null };
  if (!LIMIT_KEYS.includes(key)) return { state: "denied", reason: "limit_key_unknown:" + String(key), value: null };
  const value = p.limits[key];
  if (value === null) {
    return { state: "undecided", reason: "limit_pending_owner_decision", value: null, decision_source: DECISION_SOURCE };
  }
  if (value === UNLIMITED) {
    return { state: "allowed", reason: null, value: UNLIMITED, remaining: null };
  }
  if (typeof value === "boolean") {
    return value
      ? { state: "allowed", reason: null, value }
      : { state: "denied", reason: "limit_not_in_plan", value };
  }
  if (typeof used !== "number" || !Number.isFinite(used)) {
    return { state: "undecided", reason: "usage_not_supplied", value };
  }
  return used < value
    ? { state: "allowed", reason: null, value, remaining: value - used }
    : { state: "denied", reason: "limit_exceeded", value, remaining: 0 };
}

// 權益驗證。這裡只驗形狀與來源,不驗「他有沒有真的付錢」——那是 EZ2.APP 的事,
// 我們連問都不該問,只能接收同步結果。
function validateEntitlement(entitlement) {
  const e = entitlement || {};
  const errors = [];
  if (!String(e.entitlement_id || "").trim()) errors.push("entitlement_id_missing");
  if (!String(e.tenant_id || "").trim()) errors.push("entitlement_tenant_id_missing");
  if (!plan(e.plan_id)) errors.push("entitlement_plan_unknown:" + String(e.plan_id));
  if (!ENTITLEMENT_AUTHORITIES.includes(String(e.authority || ""))) {
    // ★ 這一條是鐵律的機器形式:權益只能來自 EZ2.APP。
    errors.push("entitlement_authority_not_allowed:" + String(e.authority));
  }
  if (!ENTITLEMENT_STATUSES.includes(String(e.status || ""))) {
    errors.push("entitlement_status_unknown:" + String(e.status));
  }
  // authority_ref 是不透明字串,PAGE 不解讀它的內部語意(可能是 order_item_id、
  // subscription id、或未來換平台之後的別的東西)。要求它存在是為了可稽核:
  // 出事的時候要能追回是哪一筆交易給的權利。
  if (!String(e.authority_ref || "").trim()) errors.push("entitlement_authority_ref_missing");
  if (!String(e.synced_at || "").trim()) errors.push("entitlement_synced_at_missing");
  // 方案版本:老客戶回舊版能力(grandfathering,對齊 ez2-tenant-segmentation 的
  // platform_segment_version 慣例)。缺了就無法證明他當初買到的是什麼。
  if (!String(e.plan_contract_version || "").trim()) errors.push("entitlement_plan_contract_version_missing");
  if (String(e.status) === "active" && !String(e.current_period_end || "").trim()) {
    errors.push("entitlement_active_requires_period_end");
  }
  return { ok: errors.length === 0, errors: Object.freeze(errors) };
}

// 從權益解出「現在實際生效的方案」。
// ★ 過期不是停權:過期退回 free(內容還在,能力縮回),停權是另一回事(page.suspended)。
//   兩者混用會讓「沒續約」與「被停權」在程式裡長得一樣,而它們的補救路徑完全不同。
function resolveEffectivePlan(entitlement, nowIso) {
  const valid = validateEntitlement(entitlement);
  if (!valid.ok) {
    return { plan_id: "free", reason: "entitlement_invalid", errors: valid.errors, degraded: true };
  }
  const status = String(entitlement.status);
  if (status === "suspended") {
    return { plan_id: null, reason: "suspended", errors: Object.freeze([]), degraded: true };
  }
  if (status !== "active") {
    return { plan_id: "free", reason: "entitlement_not_active:" + status, errors: Object.freeze([]), degraded: true };
  }
  if (nowIso) {
    const now = Date.parse(nowIso);
    const end = Date.parse(entitlement.current_period_end);
    if (Number.isFinite(now) && Number.isFinite(end) && now > end) {
      return { plan_id: "free", reason: "entitlement_period_ended", errors: Object.freeze([]), degraded: true };
    }
  }
  return { plan_id: entitlement.plan_id, reason: null, errors: Object.freeze([]), degraded: false };
}

// 降級之前要先確認的事。既有規則(tenant-placement):已搬出獨立主機的租戶
// 不可以直接降到 free,必須先搬回——否則會出現「降級成功但頁面在別台」的孤兒。
function planDowngradeChecks(fromPlanId, toPlanId, context) {
  const from = plan(fromPlanId);
  const to = plan(toPlanId);
  const ctx = context || {};
  const blockers = [];
  if (!from) blockers.push("plan_unknown:" + String(fromPlanId));
  if (!to) blockers.push("plan_unknown:" + String(toPlanId));
  if (from && to) {
    if (to.rank >= from.rank) return { ok: true, is_downgrade: false, blockers: Object.freeze([]) };
    for (const form of from.forms) {
      if (!to.forms.includes(form) && (ctx.active_forms || []).includes(form)) {
        blockers.push("active_form_not_in_target_plan:" + form);
      }
    }
    if (!to.tracking_modes.includes(String(ctx.tracking_mode || "performance_first"))) {
      blockers.push("active_tracking_mode_not_in_target_plan:" + String(ctx.tracking_mode));
    }
    const fromPool = tenantPlacement.POOLS[from.pool];
    const toPool = tenantPlacement.POOLS[to.pool];
    if (fromPool && toPool && fromPool.movable && !toPool.movable && ctx.moved_out === true) {
      blockers.push("moved_out_tenant_must_move_back_before_downgrade");
    }
  }
  return { ok: blockers.length === 0, is_downgrade: true, blockers: Object.freeze(blockers) };
}

const ALL_FORMS = Object.freeze([tenantPlacement.BASE_FORM].concat(tenantPlacement.UPGRADE_FORMS));

// 自我稽核:本模組引用了三個既有真相源的鍵名。它們任何一邊改名,這裡要當場紅,
// 而不是等到線上出現「方案說可以、落點說不行」的矛盾。
// options.plans 只給測試用:餵一份被動過手腳的方案表進來,證明下面每一條檢查
// 真的在承重。沒有這個入口的話,audit 全綠只能證明「現在這份表是對的」,
// 不能證明「表壞掉時它會紅」——那兩件事差很多。
function auditPlanContract(options) {
  const table = (options && options.plans) || PLANS;
  const errors = [];
  for (const id of PLAN_IDS) {
    const p = table[id];
    if (!p) { errors.push("plan_missing:" + id); continue; }
    if (!tenantPlacement.POOLS[p.pool]) errors.push("plan_pool_not_in_placement_contract:" + id + ":" + p.pool);
    for (const form of p.forms) {
      if (!ALL_FORMS.includes(form)) errors.push("plan_form_not_in_placement_contract:" + id + ":" + form);
    }
    for (const mode of p.tracking_modes) {
      if (!speedModes.MODES[mode]) errors.push("plan_tracking_mode_not_in_speed_policy:" + id + ":" + mode);
    }
    for (const key of Object.keys(p.limits)) {
      if (!LIMIT_KEYS.includes(key)) errors.push("plan_limit_key_unknown:" + id + ":" + key);
    }
    for (const key of LIMIT_KEYS) {
      if (!(key in p.limits)) errors.push("plan_limit_key_missing:" + id + ":" + key);
    }
  }
  // 階梯必須單調:高階不可以少掉低階有的東西,否則升級會變成失去功能。
  for (let i = 1; i < PLAN_IDS.length; i += 1) {
    const lower = table[PLAN_IDS[i - 1]];
    const upper = table[PLAN_IDS[i]];
    if (!lower || !upper) continue;
    for (const form of lower.forms) {
      if (!upper.forms.includes(form)) errors.push("plan_ladder_form_regression:" + upper.id + ":" + form);
    }
    for (const mode of lower.tracking_modes) {
      if (!upper.tracking_modes.includes(mode)) errors.push("plan_ladder_tracking_regression:" + upper.id + ":" + mode);
    }
    if (upper.rank <= lower.rank) errors.push("plan_ladder_rank_not_increasing:" + upper.id);
  }
  // 客戶可選的追蹤模式必須都是速度政策認得的,而 marketing_site 不可以出現在方案裡
  // (那是官網自己的模式,賣給客戶等於承諾一個不存在的東西)。
  for (const mode of CUSTOMER_TRACKING_MODES) {
    if (!speedModes.MODES[mode]) errors.push("customer_tracking_mode_not_in_speed_policy:" + mode);
  }
  for (const id of PLAN_IDS) {
    if (table[id] && table[id].tracking_modes.includes("marketing_site")) {
      errors.push("plan_sells_marketing_site_mode:" + id);
    }
  }
  // 未定的數值要數得出來,不可以悄悄消失。
  const undecided = [];
  for (const id of PLAN_IDS) {
    if (!table[id] || !table[id].limits) continue;
    for (const key of LIMIT_KEYS) {
      if (table[id].limits[key] === null) undecided.push(id + "." + key);
    }
  }
  return {
    ok: errors.length === 0,
    errors: Object.freeze(errors),
    undecided_limits: Object.freeze(undecided),
    decision_source: DECISION_SOURCE
  };
}

module.exports = {
  VERSION,
  DECISION_SOURCE,
  PLAN_IDS,
  PLANS,
  LIMIT_KEYS,
  UNLIMITED,
  CUSTOMER_TRACKING_MODES,
  ENTITLEMENT_STATUSES,
  ENTITLEMENT_AUTHORITIES,
  ROUTE_REDIRECT_RETENTION_MONTHS,
  plan,
  allowsForm,
  allowsTrackingMode,
  poolFor,
  limitFor,
  validateEntitlement,
  resolveEffectivePlan,
  planDowngradeChecks,
  auditPlanContract
};
