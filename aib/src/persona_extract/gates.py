# SPDX-License-Identifier: Apache-2.0
"""B 提取人格程式化——純函式閘(L5-01 起;PLAN-persona-extract-b-0818 §1/§2)。

這裡只放**不碰檔案系統、不跑子程序**的判定,讓每一條 SOP 判準都能被單獨測、單獨突變。
真正的工具呼叫在 wrappers.py;CLI 在 persona_extract.py。

SOP v3.5 對應:
  §一-A/B/C 前提與閘 → decide_mode / birth_check_verdict / evals_verdict
"""

import re

# SOP §一:draft 輪輕前提;正式輪再加二項;閘 (b)(c) 全立才 full(a 閘見刀 A)。
# ★ 刀 A:three_memory 前提已移除(EG.7 第一步)——這是**真實的放寬**:
#   原本 a 閘不綠會把 full 降成 draft,現在少一道。承接者=無(見交付書面)。
DRAFT_CHECKS = ("git_truth", "birth_check")
FULL_EXTRA_CHECKS = ("session_brief", "evals")
GATE_KEYS = ("b_birth_check_ok", "c_ship_receipt_ok")
MODES = ("draft", "full")


def birth_check_verdict(rc, stdout):
    """ez2-role-birth-check.js:exit 0=九項 required 全過;1=有 required 失敗;2=用法錯。
    新人格首煉時角色.md 不存在=**n/a**(SOP §一-A-3:不算紅)——只認 JSON 裡 card 檢查
    detail 帶「不存在」才判 n/a,不用 rc 猜。回 'ok' | 'na' | 'fail' | 'error'。"""
    if rc == 0:
        return "ok"
    if rc == 2:
        return "error"
    try:
        import json
        doc = json.loads(stdout or "")
        for c in doc.get("checks", []):
            if c.get("key") == "card" and not c.get("ok") and "不存在" in str(c.get("detail", "")):
                return "na"
    except ValueError:
        pass
    return "fail"


def evals_verdict(rc, no_bank):
    """ez2-persona-evals.js:紅線全拒才過。新人格無題庫時工具回 exit 0——那是「無題可考」
    不是「通過」(SOP §一-B ★):no_bank=True 一律 'no_bank',不得記成 pass。
    no_bank=None(判不出有沒有題庫)也不得記 pass → 'unknown'。"""
    if no_bank is True:
        return "no_bank"
    if no_bank is None:
        return "unknown"
    return "pass" if rc == 0 else "fail"


def decide_mode(requested, gates):
    """requested ∈ MODES;gates = {b_birth_check_ok, c_ship_receipt_ok}(bool)。
    SOP §一-C:全套輪需三條**同時**成立;任一不成立 → 只能 draft(自動降軌,並說為什麼)。
    回 {"mode", "downgraded", "downgraded_because": [...]}。"""
    if requested not in MODES:
        raise ValueError("mode must be one of %s: %r" % (MODES, requested))
    if requested == "draft":
        return {"mode": "draft", "downgraded": False, "downgraded_because": []}
    missing = [k for k in GATE_KEYS if gates.get(k) is not True]
    if missing:
        return {"mode": "draft", "downgraded": True, "downgraded_because": missing}
    return {"mode": "full", "downgraded": False, "downgraded_because": []}


def ship_receipt_ok(receipt):
    """閘 (c):本 lane ez2-ship 終態收據 pushed=true 且全套測試真實 exit 0。
    只認 bool True 與 int 0(字串 "true"/"0" 不算——收據是機器寫的,形狀對才算)。"""
    if not isinstance(receipt, dict):
        return False
    return receipt.get("pushed") is True and receipt.get("tests_exit") == 0 \
        and not isinstance(receipt.get("tests_exit"), bool)

# ---------------------------------------------------------------- SOP §三 3.1 / 3.4
# 事件群計數:計數鍵=lane(不是 session);同 lane 多次=1 群;換 lane、換 session、換日 +1。
# 只認可驗指標(pointer):commit hash / receipt 路徑 / 帳本 ev_ / 訊息 id / 檔案+行號;
# 純敘述不算。--session 保留為稽核欄位;recur 的拒收判準=同 lane。
POINTER_KINDS = ("commit", "receipt", "event", "message", "file_line")
_POINTER_RES = (
    ("commit", re.compile(r"^[0-9a-f]{7,40}$")),
    ("event", re.compile(r"^ev_[0-9a-f]{64}$")),
    ("message", re.compile(r"^(msg|dm|msg_id)[:_-][A-Za-z0-9._:@-]{4,120}$")),
    ("receipt", re.compile(r"^(?:[A-Za-z0-9._@-]+/)+[A-Za-z0-9._@-]+\.(json|jsonl|md|txt)$")),
    ("file_line", re.compile(r"^(?:[A-Za-z0-9._@-]+/)*[A-Za-z0-9._@-]+\.[A-Za-z0-9]{1,8}:\d{1,7}$")),
)
LANE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")   # lane=worklane=git 分支(+worktree)
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MIN_GROUPS_FOR_CARD = 2   # SOP §3.1 入卡門檻=至少 2 群


def pointer_kind(pointer):
    """可驗指標的種類;純敘述/空白/含空格 → None(不算證據)。"""
    if not isinstance(pointer, str):
        return None
    p = pointer.strip()
    if not p or " " in p or len(p) > 400:
        return None
    for kind, rx in _POINTER_RES:
        if rx.match(p):
            return kind
    return None


def count_groups(evidences):
    """evidences=[{lane, day, session?, pointer}] → {"groups": n, "basis": [...], "rejected": [...]}。
    群 = (lane, day) 去重:同 lane 同日多筆=1 群;換 lane 或換日 +1。session 只稽核不計數。
    無合法 pointer 的一筆進 rejected,不計。"""
    keys, basis, rejected = [], [], []
    for i, e in enumerate(evidences or []):
        lane, day, ptr = e.get("lane"), e.get("day"), e.get("pointer")
        why = None
        if not isinstance(lane, str) or not LANE_RE.match(lane):
            why = "bad_lane"
        elif not isinstance(day, str) or not DAY_RE.match(day):
            why = "bad_day"
        elif pointer_kind(ptr) is None:
            why = "pointer_not_verifiable"
        if why:
            rejected.append({"index": i, "why": why})
            continue
        key = (lane, day)
        if key not in keys:
            keys.append(key)
            basis.append({"lane": lane, "day": day, "first_pointer": ptr.strip(),
                          "kind": pointer_kind(ptr)})
    return {"groups": len(keys), "basis": basis, "rejected": rejected,
            "card_eligible_by_count": len(keys) >= MIN_GROUPS_FOR_CARD}


# SOP §3.4 不得煉成:狀態閘先於內容閘。
INELIGIBLE_STATUSES = ("pending", "rejected", "deferred", "unknown")


def filter_ineligible(candidate):
    """回 (eligible: bool, reasons: [..])。先看狀態,再看證據形。
    - status ∈ pending/rejected/deferred/unknown → 拒(狀態閘先於內容閘)
    - evidence_refs 空,或全部不可驗 → 拒(沒有 exit code / hash / receipt 的宣稱)
    - single_customer_event / unresolved_bug / model_inference_unapproved / gamified / backfilled 任一 True → 拒"""
    reasons = []
    st = candidate.get("status")
    if st in INELIGIBLE_STATUSES:
        reasons.append("status:%s" % st)
    refs = candidate.get("evidence_refs") or []
    if not refs:
        reasons.append("no_evidence")
    elif not any(pointer_kind(r) for r in refs):
        reasons.append("evidence_not_verifiable")
    for flag in ("single_customer_event", "unresolved_bug", "model_inference_unapproved",
                 "gamified", "backfilled_authorization", "unknown_external_state"):
        if candidate.get(flag) is True:
            reasons.append(flag)
    return (not reasons), reasons


# ---------------------------------------------------------------- SOP §五 13 欄 + 錨等級
# 候選必附 13 欄(SOP §五 原文順序);查無證據填 "unknown",不得捏造。
# ez2-ai schemas/ez2-persona-candidate.schema.json 對 B_candidates 的 item **不定形**
# (只管包級 25 鍵)——13 欄紀律在 SOP 層,故 AIB 在此執法;包級交工具 validate。
CANDIDATE_FIELDS_13 = (
    "candidate_uid", "target_field", "new_text", "original_text",
    "source_turn_ids", "evidence_refs", "git_commit", "test_name_and_exit",
    "confidence", "freshness", "event_groups", "needs_human_approval",
    "risk_if_not_adopted",
)
ANCHOR_GRADES = ("machine", "descriptive")
TARGET_FIELDS = ("opening", "duty", "redline", "domain", "retire")


def validate_candidate_13(c):
    """回 (ok, problems[])。13 欄全在且非空(unknown 是合法值);anchor_grade 必填且 ∈ 二值;
    target_field ∈ 五組;event_groups 需 {count:int, basis:str|list};retire 另有三件閘
    (≥2 群失效證據/核准者實名/當初為何存在指標)——in filter 層,不在此重複。"""
    problems = []
    if not isinstance(c, dict):
        return False, ["not_an_object"]
    for f in CANDIDATE_FIELDS_13:
        v = c.get(f)
        if v is None or (isinstance(v, str) and not v.strip()) \
                or (isinstance(v, (list, dict)) and not v):
            problems.append("missing:%s" % f)
    if c.get("anchor_grade") not in ANCHOR_GRADES:
        problems.append("bad_anchor_grade:%r" % (c.get("anchor_grade"),))
    if c.get("target_field") not in TARGET_FIELDS:
        problems.append("bad_target_field:%r" % (c.get("target_field"),))
    eg = c.get("event_groups")
    if not (isinstance(eg, dict) and isinstance(eg.get("count"), int)
            and not isinstance(eg.get("count"), bool) and eg.get("basis")):
        problems.append("bad_event_groups")
    return (not problems), problems


# machine-anchor 的定義來源(SOP §五):App IPC `ez2chat:extract-memory` 回傳的
# source_turn_uids。qa L5-04 錨審(12:24)抓到:anchor_grade 是自報欄,無交叉驗證——
# 自稱 machine 而 source_turn_ids="unknown" 照樣入 B。修:machine 主張必須帶**可驗形**
# 的 turn uid(≥1 個),否則錨主張不成立、降 C。turn uid 形:stableTurnUid(hex/uuid/
# base36 ≥8 碼);"unknown"/敘述/空 皆不算。不得捏造 id(§五):形驗擋不了捏造,
# 但「捏一個像樣的 id」已是明確違規行為,留給 checker 與第二刀 App 端反查。
_TURN_UID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def machine_anchor_substantiated(c):
    """machine 錨主張是否有形可驗的 turn ids 撐著。回 (bool, why)。"""
    ids = c.get("source_turn_ids")
    if isinstance(ids, str):
        ids = [ids]
    if not isinstance(ids, (list, tuple)) or not ids:
        return False, "no_source_turn_ids"
    bad = [i for i in ids
           if not isinstance(i, str) or i.strip().lower() == "unknown"
           or not _TURN_UID_RE.match(i.strip())]
    if bad:
        return False, "unverifiable_turn_ids:%r" % (bad[:3],)
    return True, None


def candidate_destination(c):
    """SOP §五 錨等級決定候選能走多遠:
      machine-anchor(且主張有 turn ids 撐著)且事件群 ≥2 且無 §3.4 拒因 → "B"
      其他一律 → "C"。descriptive **永不入 B**;自稱 machine 而 source_turn_ids
      =unknown/不可驗形=錨主張不成立,同樣 C(qa 錨審修)。"""
    ok13, _ = validate_candidate_13(c)
    if not ok13:
        return "C"
    eligible, _ = filter_ineligible(c)
    eg = c.get("event_groups") or {}
    if c.get("anchor_grade") == "machine" and machine_anchor_substantiated(c)[0] \
            and eligible \
            and isinstance(eg.get("count"), int) and eg.get("count") >= MIN_GROUPS_FOR_CARD:
        return "B"
    return "C"
