"use strict";

// ez2-page-retention-promise.js — 我們對使用者承諾了什麼,以及誰在執行它(2026-07-28)
//
// ★★★ 為什麼需要它:2026-07-28 現查,每一筆同意紀錄裡都寫著
//   `unused_material_auto_delete_days: 30`,而**全 repo 沒有任何程式在刪**。
//   那個字串只出現三處:寫進去的一行,加上兩支斷言它等於 30 的 smoke。
//   **測試驗的是這個承諾寫在資料裡,沒有任何東西驗它被兌現。**
//
// ★★★ 這是「斷言驗字串不驗行為」最貴的一種:它讓一個對使用者的承諾
//   看起來被測試覆蓋著。紅燈不會出現,因為沒有人問過那個問題。
//
// ★★ 所以本模組不是「保留期設定檔」。它是**承諾登記表** ——
//   每一條對外承諾都要說得出「誰在執行」,而 `executor: null`
//   不是待填欄位,它是一個要被看見的事實:**這條承諾目前沒有人在做**。
//
// ★ 刻意不讓稽核因為 `executor: null` 就變紅:一支永遠紅的 smoke
//   會被 skip 掉,然後這件事就徹底消失了。取而代之的是:
//   **資料裡寫出去的 enforcement 必須與登記表一致** ——
//   誰想把它講成「已執行」,就必須先真的接上執行者。

const consent = require("./ez2-page-consent-contract.js");

const VERSION = "2026-07-28.ez2-page-retention-promise.v1";

const ENFORCEMENT = Object.freeze(["enforced", "declared_not_enforced"]);
const AUDIENCES = Object.freeze(["customer", "internal"]);

// 承諾登記表。★ 新增一條對外承諾,就要在這裡登記 ——
//   而登記表會逼你回答「誰執行」,那正是上一版沒有人問的問題。
const PROMISES = Object.freeze({
  social_intake_unused_material: Object.freeze({
    id: "social_intake_unused_material",
    days: 30,
    revocable: true,
    audience: "customer",
    declared_in: "shared/ez2-page-social-intake.js",
    // ★★★ null = 沒有任何程式在執行這條承諾。**這是現況,不是待辦格。**
    executor: null
  })
});

// ★ `registry` 可注入,而它存在的理由很具體:沒有它,
//   「天數來自登記表」這條斷言證明不了 —— 登記表裡就是 30,
//   把它換成硬寫的 30 兩邊照樣相等,而突變測試會安靜地活下來(實測過)。
//   注入之後,測試可以拿一份天數不同的登記表,證明輸出**跟著它動**。
function promise(id, registry) {
  const table = registry || PROMISES;
  return Object.prototype.hasOwnProperty.call(table, id) ? table[id] : null;
}

// 這條承諾現在的狀態。★ 由 `executor` **推導**,不是第二個硬寫的宣稱 ——
//   兩個各自寫死的宣稱就是兩個真相,而它們一定會在某次改動之後分歧。
function enforcementOf(id, registry) {
  const p = promise(id, registry);
  if (!p) return null;
  return p.executor ? "enforced" : "declared_not_enforced";
}

// 對外承諾了、但沒有人在執行的那些。
// ★ 這份清單就是欠帳本身。它是空的那天,這個模組才算沒事做。
function unenforcedPromises() {
  return Object.freeze(
    Object.values(PROMISES)
      .filter((p) => p.audience === "customer" && !p.executor)
      .map((p) => p.id)
      .sort()
  );
}

// 寫進紀錄裡的那一塊。★ 天數從登記表來,不在呼叫端再寫一次 30。
function retentionBlock(id, registry) {
  const p = promise(id, registry);
  if (!p) return null;
  return Object.freeze({
    unused_material_auto_delete_days: p.days,
    revocable: p.revocable,
    // ★★★ 講實話的那一格:現在沒有人在執行,紀錄裡就要這樣寫。
    enforcement: enforcementOf(id, registry)
  });
}

// 想把某條承諾接上執行者時問這裡。
//
// ★★★ 保留期還沒決定就不可以宣稱已執行 —— 那會變成
//   「有人在刪,但沒有人說過該刪什麼、留多久」。
//   這一條把承諾登記表與同意契約的保留期閘門綁在一起,
//   而不是讓兩邊各自宣稱。
function canBindExecutor(id, policy) {
  const p = promise(id);
  if (!p) return { allowed: false, reason: "promise_unknown:" + String(id) };
  if (p.executor) return { allowed: false, reason: "promise_already_bound" };
  const sweep = consent.retentionSweepDecision(policy || consent.RETENTION_POLICY);
  if (!sweep.allowed) return { allowed: false, reason: sweep.reason };
  return { allowed: true, reason: "ready_to_bind", deletion_mode: sweep.deletion_mode };
}

function describe() {
  const pending = unenforcedPromises();
  if (!pending.length) return "所有對外的保留承諾都有人在執行";
  return "★★★ 對使用者承諾了但沒有人在執行:\n"
    + pending.map((id) => {
      const p = PROMISES[id];
      return "  " + id + " —— 承諾 " + p.days + " 天自動刪除,宣告在 " + p.declared_in
        + ",執行者:無";
    }).join("\n")
    + "\n  ★ 保留期未決時這是誠實的狀態;決定之後它就是欠帳。";
}

function auditRetentionPromises() {
  const errors = [];

  for (const [key, p] of Object.entries(PROMISES)) {
    if (p.id !== key) errors.push("promise_id_mismatch:" + key);
    if (!Number.isInteger(p.days) || p.days <= 0) errors.push("promise_days_invalid:" + key);
    if (!AUDIENCES.includes(p.audience)) errors.push("promise_audience_invalid:" + key);
    if (!String(p.declared_in || "").trim()) errors.push("promise_declared_in_missing:" + key);
    if (!ENFORCEMENT.includes(enforcementOf(key))) errors.push("promise_enforcement_invalid:" + key);
    // ★ 沒有執行者就不可以說自己已執行。這是本模組的中心斷言。
    if (!p.executor && enforcementOf(key) !== "declared_not_enforced") {
      errors.push("unenforced_promise_must_not_claim_enforced:" + key);
    }
  }

  // ★★ 寫進紀錄的那一塊必須與登記表一致(兩處分歧就是兩個真相)。
  const block = retentionBlock("social_intake_unused_material");
  if (!block || block.unused_material_auto_delete_days !== PROMISES.social_intake_unused_material.days) {
    errors.push("retention_block_days_out_of_sync");
  }
  if (!block || block.enforcement !== enforcementOf("social_intake_unused_material")) {
    errors.push("retention_block_enforcement_out_of_sync");
  }

  // ★★★ 輸出必須**跟著登記表動**,不是剛好等於同一個數字。
  //   沒有這一條,把 `p.days` 換成硬寫的 30 會安靜地通過。
  const alt = { alt_promise: { id: "alt_promise", days: 45, revocable: false, audience: "customer", declared_in: "x.js", executor: "tools/sweep.js" } };
  const altBlock = retentionBlock("alt_promise", alt);
  if (!altBlock || altBlock.unused_material_auto_delete_days !== 45) errors.push("retention_block_days_not_derived");
  if (!altBlock || altBlock.revocable !== false) errors.push("retention_block_revocable_not_derived");
  if (!altBlock || altBlock.enforcement !== "enforced") errors.push("retention_block_enforcement_not_derived");

  // ★★★ 保留期未決時不可以接執行者。
  const bind = canBindExecutor("social_intake_unused_material", consent.RETENTION_POLICY);
  if (bind.allowed !== false || bind.reason !== "retention_undecided") {
    errors.push("undecided_policy_must_not_allow_binding:" + bind.reason);
  }

  // ★ 決定之後要放得行 —— 否則這個閘門只是永遠說不(負向對照)。
  const decided = {
    status: "decided", keep_after_withdrawal_days: 1095, deletion_mode: "tombstone",
    decided_by: "maintainer", decided_at: "2026-07-28T00:00:00.000Z"
  };
  if (canBindExecutor("social_intake_unused_material", decided).allowed !== true) {
    errors.push("decided_policy_must_allow_binding");
  }

  // 未知的承諾不可以看起來合法。
  if (retentionBlock("nope") !== null || enforcementOf("nope") !== null) {
    errors.push("unknown_promise_must_not_produce_block");
  }

  return { ok: errors.length === 0, errors: Object.freeze(errors), version: VERSION };
}

module.exports = {
  VERSION, PROMISES, ENFORCEMENT, AUDIENCES,
  promise, enforcementOf, unenforcedPromises, retentionBlock, canBindExecutor,
  describe, auditRetentionPromises
};
