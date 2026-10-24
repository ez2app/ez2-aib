#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""E8.1 降級運行能力矩陣:T0-T3 的機器判定核心(draft)。

語意正本=docs/reports/E8.1-rev2-dual-basis.md(cl3 覆核章合併版)。該正本 §5
誠實指出:「trust 級別沒有被任何閘門讀取」——本模組就是把四張表從紙上契約
變成可被咽喉呼叫的判定件。本模組是純判定庫:無 I/O、無狀態、不落帳;
咽喉接線(git hook/檔案域/CLI wrapper)屬正本開放問題 3 的 E2.8 交界,獨立派工。

編入的裁決(逐條可回溯):
  - 四級白名單「只加不減」,未列即禁(正本 §1;繼承不變式由測試看守)
  - T1→T2 三條件並列缺一不可:❶入職考通過 ❷N 次無違規交付 ❸attestation
    (決策 10「T2=三條件」;N 建議值 3,實際值艦長定且必須是具體數字)
  - 條件❸過渡條款:綁定收據含真人親手打 challenge 者視同滿足,transitional
    標記必真;批次/自動綁定不適用——覆蓋窄必須寫進實作,否則過渡變後門
    (正本 §7-2 過渡裁示+初審附註)
  - bound_unattested=T1 獨立子狀態:放行判定必帶 authority_level 原值留痕,
    獨立碼不得併回 verified(決策 10 配套 a;正本 §1.1)
  - deny 是三檔之外的第四種:無論誰批准都不合法,任何信任級別不能把 deny
    轉 allow,T3 也不行(rev2 修訂三)
  - 降級不開會:不實宣稱直降 T0(誠實問題重於能力問題);守門者故障不降級,
    標「驗證不可得」維持前級(原理 10)(正本 §2.2)
  - trust 與 role 正交:實際可執行=trust 白名單 ∩ role 職掌,取交集
    (正本 §4;role 軸=E0.5 src/identity/role_model.py)

CLI(咽喉接線試判用):
  python3 trust_tiers.py judge <tier> <action> <authority_level>
  python3 trust_tiers.py t2-check <exam_passed> <clean_deliveries> <native_attestation> [hand_challenge]
  stdout=判定 JSON 一行;rc:0=allow/eligible 4=deny/ineligible 2=用法錯
測試跑法(exit code 紀律):python3 tests/test-trust-tiers.py >log 2>&1; echo $?
"""
import json
import sys

TIERS = ("t0_unbound", "t1_bound", "t2_examined", "t3_trusted")

# 各級「新增」能力(正本 §1 表逐列編碼;capabilities_of() 做繼承聯集)。
# 動作鍵是本模組的正典詞彙;咽喉接線時以這些鍵對應實際指令。
TIER_GRANTS = {
    "t0_unbound": frozenset({
        "read_repo",            # 全域唯讀
        "read_origin_refs",     # git show origin/x:path
        "run_readonly",         # 測試/lint/build/grep
        "write_assigned_dir",   # 僅限指派者給的單一目錄+報告檔
        "report_to_spawner",    # 只能回報 spawner
    }),
    "t1_bound": frozenset({
        "write_own_worktree",
        "create_worktree",
        "git_commit",           # 僅自己 worktree 的分支
        "git_push_branch",      # feature 分支,非保護分支
        "send_message",         # 受 E2.7 主AI限定
        "claim_task",
    }),
    "t2_examined": frozenset({
        "open_pr",
        "review_same_vendor",
    }),
    "t3_trusted": frozenset({
        "merge_main",           # 仍受 maker≠checker 約束
        "change_enforcement",   # .github/、hooks、政策檔
        "deploy",               # 逐筆落帳+人類收據
        "review_cross_vendor",
    }),
}

# deny 第四通道(rev2 修訂三):「無論誰批准都不合法」的動作。
# 不能降級成 escalate——escalate 意味存在可批准它的人。
DENY_ACTIONS = frozenset({
    "vault_role_file_direct_write",  # 合法路只有 writeback-gateway
})

# authority_level 判定碼:封閉字彙(審高3:未知碼 fail-closed,不得靜默通過)。
# verified 子集合另列;bound_unattested 是獨立碼,刻意不在 verified 中——
# 碼一旦併回 verified,attested 上線時分不出誰是誰。
AUTHORITY_LEVELS = frozenset({"verified_main", "bound_unattested"})
VERIFIED_AUTHORITY_CODES = frozenset({"verified_main"})

# 需 verified 權威的動作(rev2 §1.1:unattested 提合併/部署/改執法=否)。
# 白名單管「這一級可不可以」,本集合管「這個權威狀態可不可以」——兩閘都過才放行。
VERIFIED_REQUIRED_ACTIONS = frozenset({
    "open_pr", "merge_main", "deploy", "change_enforcement",
})

# T1→T2 的 N(無違規交付次數)。rev2 建議 3=跨越至少一次完整
# 初審-返工-複審循環;調整=改這一個常數+落帳留痕,不得散落。
N_CLEAN_DELIVERIES_REQUIRED = 3


class TrustTierError(Exception):
    """判定輸入不合形狀:fail-closed,呼叫端不得把 raise 當 allow。"""


def capabilities_of(tier):
    """T(n) 繼承 T(n-1) 全部能力(只加不減)。未知級別=fail-closed。"""
    if tier not in TIERS:
        raise TrustTierError("unknown tier (fail-closed): %r" % (tier,))
    caps = set()
    for name in TIERS[: TIERS.index(tier) + 1]:
        caps |= TIER_GRANTS[name]
    return frozenset(caps)


def is_verified_authority(authority_level):
    """獨立碼紀律:只有封閉集合內的碼算 verified,bound_unattested 永不算。"""
    return authority_level in VERIFIED_AUTHORITY_CODES


def judge(tier, action, authority_level, stale=False):
    """單筆動作判定。回傳的 dict 就是落帳事件的 body 素材。

    authority_level 是必填:對 bound_unattested 放行時,判定事件必須帶
    原值留痕(決策 10 配套 a)——「放行時才想起來要留痕」做不到,所以
    一律進判定、一律原封寫回。stale=降級表「驗證不可得」軌的旗標
    (三輪審中1:stale 的 T3 不得照常 deploy——維持前級是不冤枉你,
    不是當作驗證還活著);高危四動作在 stale 下一律拒,心跳恢復即解除。
    判定順序(審低1:形狀驗證最先,未知 tier/authority 不得產出任何
    正式判定):tier 形狀 -> authority 封閉字彙 -> deny 通道 -> 白名單
    -> verified 權威閘(審高3)-> stale 閘。
    """
    if tier not in TIERS:
        raise TrustTierError("unknown tier (fail-closed): %r" % (tier,))
    if authority_level not in AUTHORITY_LEVELS:
        raise TrustTierError(
            "unknown authority_level (closed vocabulary, fail-closed): %r"
            % (authority_level,))
    if not isinstance(action, str) or not action:
        # 二輪審低2:list 等不可雜湊型別原本在集合測試拋原生 TypeError。
        # 型別統一 fail-closed;未知「字串」動作仍走未列即禁(矩陣語意),
        # 不升級成 raise——那是矩陣詞彙的封閉性,由白名單承載。
        raise TrustTierError(
            "action must be a non-empty string (fail-closed): %r" % (action,))
    if not isinstance(stale, bool):
        raise TrustTierError("stale must be a strict bool (no truthy strings)")
    if action in DENY_ACTIONS:
        return {
            "action": action, "tier": tier, "allowed": False,
            "channel": "deny",
            "authority_level": authority_level, "stale": stale,
            "reason": "deny channel: unlawful regardless of approver; "
                      "no tier converts deny to allow (T3 included)",
        }
    unattested = not is_verified_authority(authority_level)
    allowed = action in capabilities_of(tier)
    if allowed and unattested and action in VERIFIED_REQUIRED_ACTIONS:
        return {
            "action": action, "tier": tier, "allowed": False,
            "channel": "verified_gate",
            "authority_level": authority_level,
            "unattested": True, "stale": stale,
            "reason": "action requires verified authority; %s is denied "
                      "regardless of tier (rev2 s1.1)" % authority_level,
        }
    if allowed and stale and action in VERIFIED_REQUIRED_ACTIONS:
        return {
            "action": action, "tier": tier, "allowed": False,
            "channel": "stale_gate",
            "authority_level": authority_level,
            "unattested": unattested, "stale": True,
            "reason": "stale trust (verification unavailable) may not run "
                      "high-risk actions; clears when heartbeat resumes",
        }
    return {
        "action": action, "tier": tier, "allowed": allowed,
        "channel": "whitelist",
        "authority_level": authority_level,
        "unattested": unattested, "stale": stale,
        "reason": "granted by tier whitelist" if allowed
                  else "not in whitelist (unlisted means forbidden)",
    }


def attestation_satisfied(native_attestation, human_typed_challenge=False):
    """T2 條件❸判定,含過渡條款。

    參數名寫死 human_typed_challenge:過渡條款只覆蓋「真人親手打 challenge」
    的綁定收據;批次/自動綁定的呼叫端沒有誠實的方式把它填 True。
    ★這是過渡語義:收斂條件=EH 軸真人同意通道落地供原生 attestation
    (正本 §7-2「原生支援上線即收斂,過渡條款自動失效」)。拔過渡=
    呼叫端停傳 human_typed_challenge 即可,判定核心不需改——測試裡有
    「過渡關閉時缺❸必拒」負例看守這一點。transitional 標記必真,事件
    可據此清點過渡期放行名單。

    輸入嚴格 bool(審高4):字串 "false" 為真值,靜默收下=條件❸被
    字串繞過,fail-closed raise。
    """
    if not isinstance(native_attestation, bool) or \
            not isinstance(human_typed_challenge, bool):
        raise TrustTierError(
            "attestation inputs must be strict bools (no truthy strings)")
    if native_attestation:
        return {"satisfied": True, "transitional": False}
    if human_typed_challenge:
        return {"satisfied": True, "transitional": True,
                "note": "transitional: human-typed BIND challenge stands in "
                        "for native attestation; narrow coverage by design"}
    return {"satisfied": False, "transitional": False}


_ATTESTATION_FIELDS = frozenset({"satisfied", "transitional", "note"})


def _validate_attestation(att):
    """attestation 結果驗形狀(審高4):封閉欄位+嚴格 bool,
    {"satisfied": True} 這種手捏 dict 不得冒充 attestation_satisfied() 產物。"""
    if not isinstance(att, dict):
        raise TrustTierError(
            "attestation must come from attestation_satisfied()")
    if not set(att) <= _ATTESTATION_FIELDS or \
            "satisfied" not in att or "transitional" not in att:
        raise TrustTierError(
            "attestation shape invalid (closed fields: satisfied/transitional/note)")
    if not isinstance(att["satisfied"], bool) or \
            not isinstance(att["transitional"], bool):
        raise TrustTierError("attestation flags must be strict bools")
    if "note" in att and not isinstance(att["note"], str):
        raise TrustTierError("attestation note must be a string")


def t2_eligibility(current_tier, exam_passed, clean_deliveries, attestation,
                   n_required=N_CLEAN_DELIVERIES_REQUIRED):
    """T1→T2 三條件並列,缺一不得升級(決策 10)。

    ★二輪審高2:升級路徑=逐級,只有 t1_bound 可候升 T2——T0 連三條件
    都不該被評,先綁定再說;未知級別 fail-closed。
    attestation 收 attestation_satisfied() 的回傳(不收裸 bool——過渡與否
    必須跟著結果走,否則 transitional 資訊在升級事件裡斷鏈)。
    """
    if current_tier not in TIERS:
        raise TrustTierError(
            "unknown tier (fail-closed): %r" % (current_tier,))
    if isinstance(n_required, bool) or not isinstance(n_required, int) \
            or n_required < 1:
        raise TrustTierError(
            "n_required must be a concrete positive integer (no vibes)")
    if not isinstance(exam_passed, bool):
        raise TrustTierError(
            "exam_passed must be a strict bool (no truthy strings)")
    _validate_attestation(attestation)
    if isinstance(clean_deliveries, bool) or not isinstance(clean_deliveries, int):
        raise TrustTierError("clean_deliveries must be an integer count")
    missing = []
    if current_tier != "t1_bound":
        missing.append("current_tier(%s)!=t1_bound: upgrade path is stepwise"
                       % current_tier)
    if not exam_passed:
        missing.append("exam_golden_pass")
    if clean_deliveries < n_required:
        missing.append("clean_deliveries(%d/%d)"
                       % (max(clean_deliveries, 0), n_required))
    if not attestation["satisfied"]:
        missing.append("attestation")
    return {
        "eligible": not missing,
        "missing": missing,
        "from_tier": current_tier,
        "n_required": n_required,
        "transitional_attestation": bool(attestation.get("transitional")),
    }


# 降級表(正本 §2.2 逐列)。one_step=降一級;to_t0=直降;none=不降;
# t1_only_to_t0=正本只定義 T1→T0(二輪審中2:T2/T3 心跳斷失走 stale 軌,
# 「全降 T0 更安全」屬矩陣修訂,走報告留痕不由實作擅定)。
_DOWNGRADE_RULES = {
    "heartbeat_lost":        {"drop": "t1_only_to_t0", "auto_recoverable": True,
                              "requires_reexam": False, "requires_human_review": False},
    "identity_check_failed": {"drop": "to_t0", "auto_recoverable": True,
                              "requires_reexam": False, "requires_human_review": False},
    "whitelist_violation":   {"drop": "one_step", "auto_recoverable": False,
                              "requires_reexam": True, "requires_human_review": False},
    "ghost_claim":           {"drop": "one_step", "auto_recoverable": False,
                              "requires_reexam": True, "requires_human_review": False},
    "false_claim":           {"drop": "to_t0", "auto_recoverable": False,
                              "requires_reexam": True, "requires_human_review": True},
    "gatekeeper_failure":    {"drop": "none", "auto_recoverable": True,
                              "requires_reexam": False, "requires_human_review": False},
}


def downgrade(current_tier, trigger):
    """降級判定:不需開會,即刻生效;守門者故障不降級標陳舊(原理 10)。

    回傳含 needs_evidence:違規類降級必須掛 evidence_refs,否則降級本身
    就是無證據宣稱(正本 §6)——本庫不落帳,旗標由落帳端強制。
    """
    if current_tier not in TIERS:
        raise TrustTierError("unknown tier (fail-closed): %r" % (current_tier,))
    if trigger not in _DOWNGRADE_RULES:
        raise TrustTierError("unknown downgrade trigger: %r" % (trigger,))
    rule = _DOWNGRADE_RULES[trigger]
    index = TIERS.index(current_tier)
    drop = rule["drop"]
    if drop == "t1_only_to_t0":
        # 正本 §2.2 心跳列只寫 T1→T0;其他級別心跳斷失=驗證不可得,走 stale 軌
        drop = "to_t0" if current_tier == "t1_bound" else "none"
    if drop == "none":
        new_tier, stale = current_tier, True   # 驗證不可得:維持前級+標陳舊
    elif drop == "to_t0":
        new_tier, stale = TIERS[0], False
    else:  # one_step;T0 已是地板
        new_tier, stale = TIERS[max(index - 1, 0)], False
    violation = rule["drop"] != "none" and not rule["auto_recoverable"]
    return {
        "trigger": trigger, "from": current_tier, "to": new_tier,
        "stale": stale, "needs_evidence": violation,
        "auto_recoverable": rule["auto_recoverable"],
        "requires_reexam": rule["requires_reexam"],
        "requires_human_review": rule["requires_human_review"],
    }


def effective_actions(tier, role_duties, authority_level, stale=False):
    """trust 與 role 正交(正本 §4):實際可執行=白名單 ∩ 職掌,取交集。

    role 軸的正本在 E0.5(src/identity/role_model.py);本函式不解釋 role,
    只收「該 role 的職掌動作集合」。★二輪審高4:交集逐 action 走 judge(),
    deny 通道、verified 權威閘與 stale 閘一體適用——否則這支 API 就是
    繞閘後門;白名單∩職掌只是 judge 裡的其中一層,不是全部。
    """
    return frozenset(
        action for action in role_duties
        if judge(tier, action, authority_level, stale=stale)["allowed"])


def _cli_bool(raw):
    """審低2:布林字面值只收 true|false;拼錯不得靜默當 false。"""
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise ValueError("boolean literal must be 'true' or 'false': %r" % (raw,))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(argv) == 4 and argv[0] == "judge":
            out = judge(argv[1], argv[2], argv[3])
            ok = out["allowed"]
        elif argv[0:1] == ["t2-check"] and len(argv) in (5, 6):
            att = attestation_satisfied(
                _cli_bool(argv[4]),
                _cli_bool(argv[5]) if len(argv) == 6 else False)
            out = t2_eligibility(argv[1], _cli_bool(argv[2]), int(argv[3]), att)
            ok = out["eligible"]
        else:
            print("usage: trust_tiers.py judge <tier> <action> <authority_level>"
                  " | t2-check <tier> <exam:true|false> <clean_n>"
                  " <native:true|false> [hand_challenge:true|false]",
                  file=sys.stderr)
            return 2
    except (TrustTierError, ValueError) as err:
        print("trust-tiers: %s" % err, file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
