# SPDX-License-Identifier: Apache-2.0
"""E2.7 血緣律機器化:誰可以送訊息。

大綱原文:send/dm 驗 sender=registry_verified 主AI;子AI 無訊息動詞,只回報 spawner。

這條規則本來只寫在交接單裡靠人記得。ACB 的歷史是「子 AI 冒用主 AI 身份發訊、
或對著不是自己 spawner 的對象回報」,而收訊端無從分辨。本模組把它變成
一個純函數 + 一個咽喉包裝:判定不看宣稱,只看身份紀錄裡的機器事實。

判定優先序(先中先判,全部 fail-closed):
  1. 輸入殘缺(缺 kind/verb)                      -> deny malformed
  2. 未知動詞                                      -> deny unknown_verb
  3. 子AI:
       report 給自己的 spawner                     -> allow report_to_spawner
       report 給別人                               -> deny report_off_lineage
       任何訊息動詞(send/dm/broadcast/reply)      -> deny sub_agent_no_messaging
       血緣不明(lineage 缺)                       -> deny lineage_missing
  4. 主AI:
       registry 未驗證                             -> deny not_registry_verified
       自報矛盾(已驗證卻仍 T0 候綁)                -> deny identity_inconsistent
       訊息動詞                                    -> allow verified_main
       report(主AI 沒有 spawner 可回報)            -> deny main_has_no_spawner
  5. 人類                                          -> allow human_principal(原理 6)
  6. 系統/收集器                                    -> deny system_no_messaging

registry_verified 只認身份紀錄裡**明寫**的那一欄。欄位不存在=未驗證(deny
registry_field_absent),不從「有心跳」「檔案存在」這類旁證推論 —— 那是 ACB
四個後門的來源。心跳新鮮度證明它還活著,不證明它是誰。

事件組裝復用 src/collectors/collector_common.py(E8.3 反重造閘:不造第二套
HLC/actor/event 組裝)。
"""

import json
import re
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "collectors"))
sys.path.insert(0, os.path.join(_HERE, "..", "ledger"))
# i18n 載入器目前住在 cockpit lane(通用件、無 cockpit 依賴,升格共用模組時
# 只需改這一行 import 路徑)
sys.path.insert(0, os.path.join(_HERE, "..", "cockpit"))
from collector_common import Hlc, clip, make_event, seal_standalone, system_actor  # noqa: E402

MESSAGING_VERBS = ("send", "dm", "broadcast", "reply")
REPORT_VERBS = ("report",)
KNOWN_VERBS = MESSAGING_VERBS + REPORT_VERBS

ALLOW = "allow"
DENY = "deny"


def reason_text(code, locale=None):
    """判定碼的人話說明(雙語,原理 11)。字典缺鍵時回 !!key!! 而非空字串,
    缺譯要看得見;i18n 不可用時退回 code 本身 —— 守門者不因翻譯壞掉而失聲。"""
    try:
        from i18n_lib import Translator
        return Translator(locale=locale).t("aib.lineage.%s" % code)
    except Exception:
        return code


def _result(decision, code, detail=""):
    return {"decision": decision, "code": code, "detail": detail}


# ---- E2.7 codex 審「高-2/高-3/高-5」(2026-08-22)----------------------------------
# 高-3:身份布林欄位原本 bool(value)——JSON 字串 "false" 會變 True(實重現:
#   registry_verified="false" 被判 verified_main)。現在只認 JSON 原生 boolean;型別不符=拒絕。
# 高-2:uuid 原本直接 os.path.join——絕對路徑會忽略 registry_dir、"../" 可逃逸。
#   現在先驗字面(安全字元、不含路徑分隔/..),再 realpath 驗結果仍在登記簿根之下。
# 高-5:紀錄直接帶 registry_verified=true 時原本略過 session_status——ended/revoked 的
#   舊格式紀錄仍可發訊。現在 session_status 在場且 != active 一律不記功。
IDENTITY_BOOL_FIELDS = ("registry_verified", "bound_unattested",
                        "formal_eligible", "native_attestation")
_UUID_SAFE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def _bool_field_error(rec, fields=IDENTITY_BOOL_FIELDS):
    """回第一個**在場但不是 JSON boolean** 的欄名;全部合格回 None。"""
    for f in fields:
        if f in rec and not isinstance(rec[f], bool):
            return f
    return None


def registry_record_path(uuid, registry_dir):
    """uuid → 登記簿內的檔案路徑;字面不安全或解析後逃出根目錄 → None。"""
    if not isinstance(uuid, str) or not _UUID_SAFE_RE.fullmatch(uuid) \
            or ".." in uuid or "/" in uuid or "\\" in uuid:
        return None
    root = os.path.realpath(registry_dir)
    path = os.path.realpath(os.path.join(root, "%s.json" % uuid))
    if os.path.dirname(path) != root:
        return None
    return path


def decide(request, sender):
    """純函數:回 {"decision": allow|deny, "code": 穩定碼, "detail": 補充}。

    request={"verb": ..., "target": ...}
    sender={"kind", "ai_id", "sig", "registry_verified", "trust", "lineage"}
    """
    if not isinstance(request, dict) or not isinstance(sender, dict):
        return _result(DENY, "malformed", "request/sender 必須是物件")
    verb = request.get("verb")
    target = request.get("target")
    kind = sender.get("kind")
    if not verb or not kind:
        return _result(DENY, "malformed", "缺 verb 或 sender.kind")
    if verb not in KNOWN_VERBS:
        return _result(DENY, "unknown_verb", str(verb))
    bad = _bool_field_error(sender)
    if bad:
        # 用既有碼 malformed(四語 i18n 已有),detail 指名欄位;不新發一個碼——新碼要動
        # i18n 四檔(非本席域),而「身份欄位型別不對」本來就是 sender 物件畸形。
        return _result(DENY, "malformed",
                       "identity_field_type:%s 必須是 JSON boolean,得到 %r"
                       % (bad, sender.get(bad)))

    if kind == "sub_agent":
        lineage = sender.get("lineage")
        if not lineage:
            return _result(DENY, "lineage_missing",
                           "子AI 必須帶 spawner 的 ai_id")
        if verb in REPORT_VERBS:
            if target and target == lineage:
                return _result(ALLOW, "report_to_spawner", lineage)
            return _result(DENY, "report_off_lineage",
                           "只能回報 %s" % lineage)
        return _result(DENY, "sub_agent_no_messaging", verb)

    if kind == "main":
        if "registry_verified" not in sender:
            return _result(DENY, "registry_field_absent",
                           "身份紀錄沒有 registry_verified 欄位")
        if not sender.get("registry_verified"):
            if sender.get("bound_unattested"):
                # 主管裁決(2026-08-08):T1 的語意是「我知道你是誰」,不是
                # 「我確信你是誰」;後者是 T2。registry 認得即 T1,送訊息是 T1 能力,
                # 所以放行。把 attested 塞進 T1 會讓全艦隊落回 T0 而停擺 ——
                # 那不是安全是癱瘓(原理 10:守門者不該癱瘓全域)。
                #
                # 三個附帶條件:
                #  1. 判定事件必須帶 authority_level 原值(見 decision_body)
                #  2. 本碼保留獨立,不併回 verified_main —— 否則 attested 真的
                #     上線時分不出「驗過」與「認得但沒驗過」
                #  3. 高風險動作(部署/合併/改執法設定)不在本閘授權範圍:
                #     它們不在 KNOWN_VERBS 裡,對任何人都是 unknown_verb 擋掉,
                #     授權歸 E2.8 政策 lane。訊息可讀可查可事後討論,部署不是。
                return _result(ALLOW, "bound_unattested",
                               "%s(%s)" % (sender.get("sig"),
                                           sender.get("authority_level") or "?"))
            return _result(DENY, "not_registry_verified", str(sender.get("sig")))
        if sender.get("trust") == "t0_unbound":
            return _result(DENY, "identity_inconsistent",
                           "registry 已驗證卻仍標 T0 候綁")
        if verb in REPORT_VERBS:
            return _result(DENY, "main_has_no_spawner", verb)
        return _result(ALLOW, "verified_main", str(sender.get("sig")))

    if kind == "human":
        return _result(ALLOW, "human_principal", str(sender.get("sig")))

    if kind == "system":
        return _result(DENY, "system_no_messaging", verb)

    # ★ EG.5 第一刀(艦長 a seat 08-18 23:44 裁准,a seat 越界寫入):
    #   hub 外但機器看得出是 AI 的發送者(dm-ctl.sh 第三格)。
    #   ★★ **必須放行,而且放行才是正確的**:在這一格存在之前,同一批訊息是掛
    #      kind=human 走 human_principal 進來的——本刀改的是標籤不是門禁。
    #      若這裡寫 DENY,等於順手把艦外通道切斷:那是行為變更,不是正名。
    #   ★ 這一行不是可有可無的「順手登記」:下面的 fail-closed 預設會把任何未登記的
    #     種類擋死,所以**新增行為者種類而沒改這裡 = 新格等於把那條通道打死**,
    #     而症狀是 dm rc=2,看起來像 dm 壞了,不像少登記一行。
    #     (本刀正是這樣發現它的:兩端校準 human→allow / system→deny 之後,
    #      agent_offhub→deny unknown_actor_kind。)
    #   code 刻意叫 offhub_agent_unattested 不叫 offhub_agent:放行的同時把
    #   「未經證實」寫進理由碼,收件端與帳本都讀得到它憑什麼進來。
    if kind == "agent_offhub":
        return _result(ALLOW, "offhub_agent_unattested", str(sender.get("sig")))

    # ★ 靶心修正(艦長 00:08):身份完全推導不出來的低權格。
    #   放行的理由與 agent_offhub 同一條:在它存在之前,這條流量是掛 kind=human
    #   走 human_principal 進來的——改標籤不改門禁。
    #   ★ 它與 human 的差別**只在標籤,不在權限**,這正是本刀能做到的極限:
    #     真正的差別要等人類憑證(第二刀)才做得出來。現在先讓帳本說實話。
    if kind == "unattested":
        return _result(ALLOW, "unattested_sender", str(sender.get("sig")))

    return _result(DENY, "unknown_actor_kind", str(kind))


def decision_draft(request, sender, result):
    """判定的**草稿**形狀:kind/actor/body/causation,不含 event_id/hlc/integrity。

    Ledger.append 只收草稿(自帶 event_id 會被拒),所以入帳走這條;
    要獨立輸出一筆完整事件(收集器風格)才走 decision_event。
    """
    return {
        "kind": "system.note",
        "actor": system_actor("lineage-gate"),
        "body": decision_body(request, sender, result),
        "causation": {"causation_id": None, "correlation_id": "E2.7"},
    }


def record_decision(ledger_path, request, sender, result):
    """把判定寫進帳本。回入帳事件;寫不進去回 None 並在 stderr 出聲。

    刻意不讓寫入失敗改變判定結果:閘門可擋,回饋器永不擋(原理 10)。
    但「沒記錄成功」必須看得見 —— 靜默失敗會讓越權嘗試查無此事。
    """
    try:
        from ledger import Ledger
        return Ledger(ledger_path, node_id="lineage-gate").append(
            decision_draft(request, sender, result))
    except Exception as err:  # noqa: BLE001 - 回饋器不得因任何理由擋住判定
        print("[warn] 判定未能入帳(判定結果不受影響):%s" % err, file=sys.stderr)
        return None


def decision_event(request, sender, result, hlc=None, wall_ms=None):
    """把一次判定組成獨立事件(收集器風格,自帶 event_id)。

    HLC 用 collector_common 的回填型發配器:wall_ms 由呼叫端給(判定當下的時間),
    測試給固定值即可重現同一顆 event_id。"""
    if wall_ms is None:
        wall_ms = int(time.time() * 1000)
    stamp = (hlc or Hlc("lineage-gate")).stamp(wall_ms)
    return seal_standalone(
        make_event("system.note", stamp, system_actor("lineage-gate"),
                   decision_body(request, sender, result),
                   correlation_id="E2.7"),
        stamp)


def decision_body(request, sender, result):
    """事件 body:主體是**被判定的身份**,不是守門者自己。

    主管裁決附帶條件 1:放行 bound_unattested 時,summary 必須帶 authority_level
    原值 —— 收件端與事後稽核都要看得見「這封的發送者權威未經證實」。
    不隱藏不確定性,是原理 1 的正面用法。
    """
    sig = sender.get("sig", "?")
    verb = request.get("verb", "?")
    target = request.get("target", "-")
    authority = sender.get("authority_level")
    suffix = ("|authority_level=%s" % authority) if authority else ""
    return {
        "summary": {
            "zh-TW": clip("血緣律判定 %s:%s %s -> %s(%s:%s)%s" % (
                result["decision"], sig, verb, target, result["code"],
                reason_text(result["code"], "zh-TW"), suffix)),
            "en": clip("lineage gate %s: %s %s -> %s (%s: %s)%s" % (
                result["decision"], sig, verb, target, result["code"],
                reason_text(result["code"], "en"), suffix)),
        },
        "subject": {"kind": "identity", "id": str(sender.get("ai_id", "?"))},
    }


def load_sender_from_registry(uuid, registry_dir):
    """從 ACB session registry 讀身份。讀不到=None(呼叫端 fail-closed)。

    刻意不從心跳/檔案存在推論 registry_verified:那些只證明它還活著,
    不證明它是誰。只有紀錄裡明寫的那一欄算數,欄位不存在就是沒驗證。
    """
    path = registry_record_path(uuid, registry_dir)
    if path is None:
        return None                      # 字面不安全 / 逃出登記簿根 = 查無此人
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    sender = {
        "ai_id": rec.get("ai_id") or "claude:?:%s" % uuid,
        "sig": rec.get("sig") or "?",
        "kind": rec.get("sender_kind") or rec.get("kind") or "main",
        "trust": rec.get("trust"),
        "lineage": rec.get("lineage") or rec.get("spawner_ai_id"),
    }
    bad = _bool_field_error(rec)
    if bad:
        # 型別不符的布林欄位:原樣帶出,由 decide() 以 malformed(identity_field_type)拒絕
        # (不在這裡猜真假——bool("false") 就是上一版的病)。
        sender[bad] = rec[bad]
        return sender
    status = rec.get("session_status")
    if "registry_verified" in rec:
        if status is not None and status != "active":
            # 高-5:明寫 registry_verified 也不得繞過 session_status。
            sender["registry_verified"] = False
            sender["session_status"] = status
        else:
            sender["registry_verified"] = rec["registry_verified"]
    elif rec.get("binding_uid") and status == "active":
        # ACB v2 綁定的真實欄位(2026-08-08 實查 498 筆記錄:registry_verified
        # 一筆都沒有;真正存在的是 binding_uid / session_status /
        # formal_eligible / native_attestation / authority_level)。
        # 只有 ACB 自己同時標 formal_eligible 與 native_attestation 為 true,
        # 才算通過驗證;68 筆已綁記錄目前兩者皆為 false,ACB 自述
        # authority_level=same_user_registry_unattested,也就是「綁了但未經證實」。
        sender["binding_uid"] = rec["binding_uid"]
        sender["authority_level"] = rec.get("authority_level")
        sender["registry_verified"] = (rec.get("formal_eligible") is True) and \
            (rec.get("native_attestation") is True)
        sender["bound_unattested"] = not sender["registry_verified"]
    return sender


def _main(argv):
    verb = target = sender_json = uuid = registry_dir = ledger_path = None
    emit_event = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--verb" and i + 1 < len(argv):
            verb = argv[i + 1]; i += 2
        elif a == "--target" and i + 1 < len(argv):
            target = argv[i + 1]; i += 2
        elif a == "--sender-json" and i + 1 < len(argv):
            sender_json = argv[i + 1]; i += 2
        elif a == "--sender-uuid" and i + 1 < len(argv):
            uuid = argv[i + 1]; i += 2
        elif a == "--registry-dir" and i + 1 < len(argv):
            registry_dir = argv[i + 1]; i += 2
        elif a == "--ledger" and i + 1 < len(argv):
            ledger_path = argv[i + 1]; i += 2
        elif a == "--event":
            emit_event = True; i += 1
        else:
            print("usage: lineage.py --verb V [--target T] "
                  "(--sender-json JSON | --sender-uuid U --registry-dir D) "
                  "[--ledger PATH] [--event]", file=sys.stderr)
            return 2

    if sender_json:
        try:
            sender = json.loads(sender_json)
        except ValueError:
            print("sender-json 不是合法 JSON", file=sys.stderr)
            return 2
    elif uuid and registry_dir:
        sender = load_sender_from_registry(uuid, registry_dir)
        if sender is None:
            # 查不到身份 = 不放行(看不見來源就不准送,原理 10 的閘門側)
            result = _result(DENY, "identity_not_found", uuid)
            result["reason"] = reason_text(result["code"])
            print(json.dumps(result, ensure_ascii=False))
            if ledger_path:
                record_decision(ledger_path, {"verb": verb, "target": target},
                                {"ai_id": "unknown:%s" % uuid, "sig": "?",
                                 "kind": "unknown"}, result)
            return 1
    else:
        print("需要 --sender-json 或 --sender-uuid + --registry-dir", file=sys.stderr)
        return 2

    request = {"verb": verb, "target": target}
    result = decide(request, sender)
    result["reason"] = reason_text(result["code"])
    if ledger_path:
        recorded = record_decision(ledger_path, request, sender, result)
        # 有記到就把 event_id 一併回報,讓呼叫端的收據可回指帳本
        result["event_id"] = recorded["event_id"] if recorded else None
    print(json.dumps(result, ensure_ascii=False))
    if emit_event:
        print(json.dumps(decision_event(request, sender, result),
                         ensure_ascii=False, sort_keys=True))
    return 0 if result["decision"] == ALLOW else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
