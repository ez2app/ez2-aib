#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""席位人格綁定投影(E6.4 缺口):把身份層投影成 pyramid 讀的 bindings 檔。

## 它補的洞

`pyramid.py` 的 L3 分流要一份 bindings 檔:

    {"schema_version": "...", "bindings": {"flt2": {"persona_uid": ...,
                                                    "route_key": ...}}}

而 `pyramid.py` 只有 `load_bindings()`——**只讀,沒有寫**。repo 裡也沒有
任何東西會產生那個檔。所以 L3 的判定「席位無人格綁定 -> hold_for_review」
今天對每一個席位都成立,不是因為沒綁,是因為**那個檔從來沒有被誰負責過**。

★ **但正解不是找一個人來手寫它。** 手寫檔會跟席位檔的 `session_uuid` 一樣
下場:欄位在、沒有機制維護、久了有值比沒值更糟(因為讀的人以為它是真的)。
所以本模組把它做成**投影**:輸入全部是既有的真相源,輸出隨時可重生,
而且可以 `--check` 比對現有檔有沒有漂移。**投影檔不該被編輯,只該被重生。**

## 推導鏈(每一段都可能斷,斷了要說在哪一段斷的)

    席位檔 seats/*.json          seat_id + session_uuid
      └─> identity/sessions/     bind_session 落的檔 -> active.agent_uid
            └─> identity/agents/ register+bind_persona 落的檔 -> persona_card
                  └─> 卡片登記表 card_ref -> persona_uid + route_key
                      (v1:card_ref 路徑字串 key;**v2**(L2-01,card_registry.py 由角色庫
                       唯讀生成)key=卡 uid 六碼,route_key=default_route_key 可空,
                       card_ref 只做舊值查表橋;席位自選大腦 agent.brain_route_key 優先)
                        └─> 路由契約 route_key 必須是契約內、aiWritable、非敏感
                              └─> bindings[seat_id]

**任何一段斷掉,那個席位進 `unresolved` 並附斷點代碼,不會從輸出裡安靜消失。**
「查不到」與「查得到但不合格」是兩種不同的 unresolved 代碼——今天這條線上
已經有人因為把兩者表現成同一個東西而修錯地方。

## 反重造(對齊 E8.3)

三個判準一律**引用正主,不抄一份**:

    bindings 檔形狀   pyramid.BINDINGS_SCHEMA
    身份檔檔名規則     role_model._safe_name
    route_key 是否合法 route_register.check_against_contract

抄一份會產生第二真相源,而它們會分岔——本模組存在的理由正是不要那個。

## 執行情境:全部顯式,不繼承

狀態根、卡片登記表、路由契約三個輸入**都必須顯式指定或顯式退回具名預設**,
解析結果會印在報告的 `sources` 段。★ 這條是刻意的:同一支工具在不同 shell
下讀到不同的東西,而兩邊都「成功」,是這個艦隊 08-15 一天內發作五次的病。

## CLI(真實 exit code)

    project  0=產出成功  2=來源/參數錯誤
    check    0=現有檔與投影一致  1=有漂移  2=無法判定(來源壞/契約讀不到)

★ `check` 的 1 與 2 刻意分開:**「證據顯示不一致」與「查不到證據」都該擋,
但處置相反**——前者去修資料,後者去修環境。
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
for _p in ("memory", "identity", "persona_route", "ledger"):
    sys.path.insert(0, str(_SRC / _p))

from pyramid import BINDINGS_SCHEMA, PYRAMID_VERSION, _SEAT_RE  # noqa: E402
from role_model import _safe_name  # noqa: E402
from route_register import check_against_contract, load_contract, contract_digest, default_contract_spec  # noqa: E402
from card_registry import SCHEMA_VERSION as CARDS_V2_VERSION, resolve_card, UID_RE, has_unsafe_chars  # noqa: E402
from schema_validator import SchemaValidator, SchemaValidationError  # noqa: E402
import seat_address  # noqa: E402

# 卡片登記表:card_ref -> persona_uid + route_key。
# 值域刻意收緊到 pyramid 已經在驗的那組(persona_uid<=128、route_key 形狀),
# 不放寬——role_model 的 CARD_RE 是 \S{1,256},那是非空檢查不是格式檢查。
CARD_REGISTRY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "cards"],
    "properties": {
        "schema_version": {"const": PYRAMID_VERSION},
        "cards": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "additionalProperties": False,
                "required": ["persona_uid", "route_key", "display_name"],
                "properties": {
                    "persona_uid": {"type": "string", "minLength": 1,
                                    "maxLength": 128},
                    "route_key": {"type": "string", "minLength": 1,
                                  "maxLength": 128,
                                  "pattern": "^[a-z][a-z0-9._-]*$"},
                    # 顯示名與 persona_uid 刻意分開:uid 是身份,顯示名是畫面。
                    # 右欄第二行要的是「主管」不是「pm-main」。合成一欄的話,
                    # 改顯示名會動到身份,而那是兩件不同的事。
                    # required=缺了就紅:半填的登記表要吵,不要安靜顯示不出來。
                    "display_name": {"type": "string", "minLength": 1,
                                     "maxLength": 24},
                },
            },
        },
    },
}

# 斷點代碼。分兩族,因為處置相反:
#   missing_*   查不到東西(要去補資料/跑註冊)
#   invalid_*   查得到但不合格(要去修那筆資料本身)
REASON_SEAT_ID_SHAPE = "invalid_seat_id_shape"
REASON_NO_SESSION_UUID = "missing_session_uuid_in_seat_file"
REASON_NO_SESSION_RECORD = "missing_session_record"
REASON_SESSION_UNREADABLE = "invalid_session_record"
REASON_NO_AGENT_RECORD = "missing_agent_record"
REASON_AGENT_UNREADABLE = "invalid_agent_record"
REASON_NO_PERSONA_CARD = "missing_persona_card_binding"
REASON_CARD_NOT_REGISTERED = "missing_card_registry_entry"
REASON_ROUTE_REFUSED = "invalid_route_key"
REASON_DUPLICATE_SEAT = "invalid_duplicate_seat_id"
# 人格綁了、大腦沒綁(v2 卡 default_route_key=null 且席位未選):不進 bindings
# (pyramid.route L3 需要 route_key),進 display(右欄第二行有人格、第三行 [未綁]),
# 並列在 unresolved 讓人看得到「這席記憶還沒有落點」。
REASON_NO_BRAIN_ROUTE = "missing_brain_route"

# D14/P14:投影掉落報告只描述 before/current 之間的集合變化。它不是身份
# 修復器,也不能把「全體保留關閉」的政策擴寫成某一席的關閉原因。
DROP_REPORT_SCHEMA = "aib-projection-drop-report/v1"
DROP_EVIDENCE_SCHEMA = "aib-projection-drop-evidence/v1"
DROP_POLICY_PRESERVE_CLOSED = "preserve_closed"
DROP_REASON_CLOSED = "closed_by_human_order"
DROP_REASON_UNKNOWN = "unknown"
DROP_ZERO_EFFECTS = {
    "seat_records_created": 0,
    "agent_records_created": 0,
    "session_records_created": 0,
    "continuity_records_created": 0,
    "binding_records_created": 0,
    "live_mutations": 0,
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ProjectionError(Exception):
    """來源不可用或參數不合法 —— fail-closed,呼叫端一律回 rc=2。"""


def _validate(instance, schema, label):
    try:
        SchemaValidator(schema).validate(instance)
    except SchemaValidationError as err:
        raise ProjectionError("%s rejected: %s" % (label, err))


def _read_json(path, label):
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as err:
        raise ProjectionError("%s unreadable (%s): %s" % (label, path, err))


def _canonical_json_bytes(value, label):
    """D14 source digest 的唯一編碼:排序、緊縮、UTF-8、尾端一個 LF。"""
    try:
        text = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ) + "\n"
    except (TypeError, ValueError) as err:
        raise ProjectionError("%s is not canonical JSON: %s" % (label, err))
    return text.encode("utf-8")


def _canonical_json_digest(value, label):
    return hashlib.sha256(_canonical_json_bytes(value, label)).hexdigest()


def _source_field(source, key, label):
    if not isinstance(source, dict):
        raise ProjectionError("%s source must be an object" % label)
    value = source.get(key)
    if value is None:
        raise ProjectionError("%s source missing %s" % (label, key))
    return value


def _validate_source_digest(source, document, label):
    path = _source_field(source, "path", label)
    supplied = _source_field(source, "sha256", label)
    if not isinstance(path, str) or not path:
        raise ProjectionError("%s source path must be a non-empty string" % label)
    if not isinstance(supplied, str) or not _SHA256_RE.fullmatch(supplied):
        raise ProjectionError("%s source sha256 must be 64 lowercase hex" % label)
    actual = _canonical_json_digest(document, "%s document" % label)
    if supplied != actual:
        raise ProjectionError(
            "%s source digest mismatch: declared=%s actual=%s"
            % (label, supplied, actual))


def _validate_drop_evidence(evidence):
    if not isinstance(evidence, dict) \
            or set(evidence) != {"schema", "records"}:
        raise ProjectionError("drop evidence top level is not closed")
    if evidence.get("schema") != DROP_EVIDENCE_SCHEMA:
        raise ProjectionError("drop evidence schema rejected")
    records = evidence.get("records")
    if not isinstance(records, list):
        raise ProjectionError("drop evidence records must be an array")
    seen_ids = set()
    required = {
        "evidence_id", "binding_keys", "reason", "source_kind",
        "source_ref", "source_sha256",
    }
    for index, record in enumerate(records):
        label = "drop evidence record[%d]" % index
        if not isinstance(record, dict) or set(record) != required:
            raise ProjectionError("%s is not a closed record" % label)
        evidence_id = record["evidence_id"]
        if not isinstance(evidence_id, str) or not evidence_id:
            raise ProjectionError("%s evidence_id is empty" % label)
        if evidence_id in seen_ids:
            raise ProjectionError("drop evidence_id is duplicated: %s" % evidence_id)
        seen_ids.add(evidence_id)
        binding_keys = record["binding_keys"]
        if not isinstance(binding_keys, list) or not binding_keys \
                or any(not isinstance(code, str) or not code
                       for code in binding_keys) \
                or len(set(binding_keys)) != len(binding_keys):
            raise ProjectionError("%s binding_keys are invalid" % label)
        for key in ("reason", "source_kind", "source_ref"):
            if not isinstance(record[key], str) or not record[key]:
                raise ProjectionError("%s %s is empty" % (label, key))
        if not isinstance(record["source_sha256"], str) \
                or not _SHA256_RE.fullmatch(record["source_sha256"]):
            raise ProjectionError("%s source_sha256 is invalid" % label)


def _drop_source_row(kind, ref, sha256, match):
    return {"kind": kind, "ref": ref, "sha256": sha256, "match": match}


def build_drop_report(*, before, current, evidence, before_source,
                      current_source, evidence_source,
                      policy=DROP_POLICY_PRESERVE_CLOSED):
    """Build a report-only exact projection diff; never reads or writes state.

    A general ``preserve_closed`` order is policy, not per-seat evidence. Only
    one exact ``human_order_receipt`` record can establish a known reason. No
    match, a conflict, or another evidence family degrades to ``unknown``.
    """
    if policy != DROP_POLICY_PRESERVE_CLOSED:
        raise ProjectionError("drop policy must be preserve_closed")
    _validate(before, BINDINGS_SCHEMA, "drop before bindings")
    _validate(current, BINDINGS_SCHEMA, "drop current bindings")
    _validate_drop_evidence(evidence)
    _validate_source_digest(before_source, before, "before")
    _validate_source_digest(current_source, current, "current")
    _validate_source_digest(evidence_source, evidence, "evidence")

    capture_phase = _source_field(before_source, "capture_phase", "before")
    captured_at = _source_field(before_source, "captured_at_ms", "before")
    observed_at = _source_field(current_source, "observed_at_ms", "current")
    if capture_phase != "before_reprojection":
        raise ProjectionError("before source was not captured before reprojection")
    if isinstance(captured_at, bool) or not isinstance(captured_at, int) \
            or isinstance(observed_at, bool) or not isinstance(observed_at, int):
        raise ProjectionError("drop source timestamps must be integer milliseconds")
    if captured_at < 0 or observed_at < 0:
        raise ProjectionError("drop source timestamps must be non-negative")
    if captured_at > observed_at:
        raise ProjectionError("before source timestamp is later than current")

    before_keys = set(before["bindings"])
    current_keys = set(current["bindings"])
    kept = sorted(before_keys & current_keys)
    dropped_keys = sorted(before_keys - current_keys)
    added = sorted(current_keys - before_keys)
    rows = []
    for binding_key in dropped_keys:
        common = [
            _drop_source_row(
                "before_projection", before_source["path"],
                before_source["sha256"], "member"),
            _drop_source_row(
                "current_projection", current_source["path"],
                current_source["sha256"], "absent"),
        ]
        closure_claims = [
            record for record in evidence["records"]
            if binding_key in record["binding_keys"]
            and record["reason"] == DROP_REASON_CLOSED
        ]
        exact = [
            record for record in closure_claims
            if record["source_kind"] == "human_order_receipt"
        ]
        if len(closure_claims) == 1 and len(exact) == 1:
            record = closure_claims[0]
            reason = DROP_REASON_CLOSED
            common.append(_drop_source_row(
                record["source_kind"], record["source_ref"],
                record["source_sha256"], "exact"))
        else:
            reason = DROP_REASON_UNKNOWN
            common.append(_drop_source_row(
                "closure_evidence_scan", evidence_source["path"],
                evidence_source["sha256"],
                "conflict" if len(closure_claims) > 1 else "none"))
        rows.append({
            "binding_key": binding_key,
            "reason": reason,
            "source_evidence": common,
        })

    return {
        "schema": DROP_REPORT_SCHEMA,
        "policy": DROP_POLICY_PRESERVE_CLOSED,
        "before": {
            "path": before_source["path"],
            "sha256": before_source["sha256"],
            "captured_at_ms": captured_at,
            "capture_phase": capture_phase,
            "seat_count": len(before_keys),
        },
        "current": {
            "path": current_source["path"],
            "sha256": current_source["sha256"],
            "observed_at_ms": observed_at,
            "seat_count": len(current_keys),
        },
        "counts": {
            "before": len(before_keys),
            "current": len(current_keys),
            "kept": len(kept),
            "dropped": len(dropped_keys),
            "added": len(added),
        },
        "kept": kept,
        "added": added,
        "dropped": rows,
        "effect_scope": "report_only",
        "forbidden_effect_counts": dict(DROP_ZERO_EFFECTS),
    }


def resolve_state_root(explicit=None):
    """狀態根解析:顯式 > EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib。

    ★ 這個順序抄自 `src/bootstrap/aib_init.py:22` 的正本,**刻意比
    `role_model.DEFAULT_STATE_ROOT` 多認一個 `EZ2_AIB_STATE_ROOT`**。
    role_model 只認 EZ2_AIB_HOME,而 doctor 認 EZ2_AIB_STATE_ROOT——
    兩套規則不一致是既有缺口(見報告),本模組選擇與 doctor/init 一致,
    並且**把解析結果印在報告裡**,好讓不一致當場可見而不是靜默分岔。

    ★ **這個選擇是暫定的,候 the maintainer 裁決。** 08-15 另一席量過:認 EZ2_AIB_HOME
    的 19 支、認 EZ2_AIB_STATE_ROOT 的 2 支,並建議全艦隊收斂成前者。
    若那個決定落下來是「只認 HOME」,本函式要跟著改——**而它會改變身份檔
    建在哪,所以不是換個變數名而已**。真正的修法是全 repo 一套規則,不是
    每支各自處理。
    """
    if explicit:
        return Path(explicit), "explicit"
    for var in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        value = os.environ.get(var)
        if value:
            return Path(value), "env:%s" % var
    return Path(os.path.expanduser("~/.ez2-aib")), "default:~/.ez2-aib"


def load_card_registry(path):
    """卡片登記表。**缺檔=錯誤,不是空表**——空表會讓每個席位都變成
    「沒登記」,那和「登記表根本沒給」長得一樣,但處置完全不同。"""
    if not path:
        raise ProjectionError("card registry path is required (--cards)")
    file_path = Path(path)
    if not file_path.is_file():
        raise ProjectionError("card registry not found: %s" % file_path)
    data = _read_json(file_path, "card registry")
    if isinstance(data, dict) and data.get("schema_version") == CARDS_V2_VERSION:
        return _normalize_cards_v2(data)
    _validate(data, CARD_REGISTRY_SCHEMA, "card registry")
    return data["cards"]



def _persona_created_legend_ready():
    """回 (就緒?, 原因)。**鍵名與判準都向顯示層拿**,這裡不複製任何字面。

    ★ 這一段刻意 fail-closed:import 不到、字典讀不到、鍵不存在,三者都回未就緒。
      理由與本檔別處同一條——**「我看不到規則」不可以被當成「規則說可以」**。
    """
    import os as _os, sys as _sys
    _root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    for _p in (_os.path.join(_root, "cockpit"), _os.path.join(_root, "cockpit", "render")):
        if _p not in _sys.path:
            _sys.path.insert(0, _p)
    try:
        from render_lib import persona_created_legend_ready  # noqa: E402
        from i18n_lib import Translator  # noqa: E402
    except Exception as exc:                     # 讀不到判準 = 擋
        return False, "讀不到圖例判準:%r" % (exc,)
    try:
        tr = Translator()
    except Exception as exc:
        return False, "i18n 載入失敗:%r" % (exc,)
    if persona_created_legend_ready(tr):
        return True, "圖例鍵已就緒"
    from render_lib import PERSONA_CREATED_LEGEND_KEY as _k
    return False, "i18n 缺 %s" % _k

def _normalize_cards_v2(data):
    """cards.json v2(card_registry.py 產)→ 與 v1 同形的內部表,多幾欄:
    key=uid;persona_uid=uid;route_key=default_route_key(**可為 None**);
    display_name;persona_sha;card_ref(舊 18 席 persona_card 路徑值的查表橋)。
    ★ 形狀在這裡驗,不信任檔案:它是投影產物,但「投影產物不會壞」不是前提。"""
    cards = data.get("cards")
    if not isinstance(cards, dict):
        raise ProjectionError("card registry v2 rejected: cards 不是物件")
    out = {}
    legacy_refs = {}
    for uid, entry in cards.items():
        if not isinstance(entry, dict) or not UID_RE.fullmatch(str(uid)) \
                or entry.get("uid") != uid:
            raise ProjectionError("card registry v2 rejected: 條目 %r 的 key/uid 不合" % (uid,))
        name = entry.get("display_name")
        sha = entry.get("persona_sha")
        rk = entry.get("default_route_key")
        if not isinstance(name, str) or not (1 <= len(name) <= 24) or has_unsafe_chars(name):
            raise ProjectionError("card registry v2 rejected: %s display_name 不合(空/過長/帶控制或注入字元)" % uid)
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ProjectionError("card registry v2 rejected: %s persona_sha 不是 sha256" % uid)
        if rk is not None and (not isinstance(rk, str) or not rk):
            raise ProjectionError("card registry v2 rejected: %s default_route_key 不合" % uid)
        legacy_ref = entry.get("card_ref")
        if legacy_ref is not None:
            if not isinstance(legacy_ref, str) or not legacy_ref or has_unsafe_chars(legacy_ref):
                raise ProjectionError("card registry v2 rejected: %s card_ref 不合" % uid)
            if legacy_ref in legacy_refs:
                raise ProjectionError(
                    "card registry v2 rejected: card_ref %r 同時指向 %s 與 %s"
                    % (legacy_ref, legacy_refs[legacy_ref], uid))
            legacy_refs[legacy_ref] = uid
        # EH.4:`source` 帶過來就好,本函式**不做預設值**。
        #   ★ 沒有這一欄時回 None,**絕不回 "human"**:那會把「我不知道這張卡
        #     是誰寫的」講成「這是人寫的」,而機器生的薄卡正是要被認出來的那個。
        #     預設值就是這一族缺陷的形狀——它讓「上游沒給」看起來像「上游說了」。
        _src = entry.get("source")
        if _src == "created":
            # ★ EH.4 寫端守衛(艦長 23:18 裁,與顯示面同批不可分批):
            #   `~` 這個標記在圖例接上之前,對沒讀過規格的人是看不懂的。
            #   所以在圖例那一格就位之前,**這條路徑直接拒絕執行**,一個字都不寫。
            #   為什麼不是測試:測試要有人跑才響。今晚三次證明「閘門響了沒人在聽」
            #   ——寫端守衛在那件事真的發生的當下響,不需要有人在聽。
            #   fail-closed:連判準都讀不到(import 失敗/字典壞)也是擋,不是放行。
            _ready, _why = _persona_created_legend_ready()
            if not _ready:
                raise ProjectionError(
                    "card registry rejected: %s 帶 source=created,但畫面上那個標記"
                    "還沒有圖例可以解釋它(%s)。圖例接上前不投影機器新建的卡。" % (uid, _why))
        if _src is not None and _src not in ("human", "auto", "created"):
            raise ProjectionError(
                "card registry v2 rejected: %s source 不在 human/auto/created" % uid)
        out[uid] = {"persona_uid": uid, "route_key": rk, "display_name": name,
                    "persona_sha": sha, "card_ref": legacy_ref,
                    "source": _src}
    return out


def hub_seats_dir(state_root):
    """本艙(HUB)自己的席位目錄;HUB 說不出是哪一艙就回 None。(EH.2)

    ★ 這支是**投影**,掃全部艙是它的職責,所以這裡**不縮範圍**——
      它與其他 13 支解析器不同:那些是「挑一個目錄用」,挑錯就拿到
      **格式完全正確的錯答案**;投影是「全部都要」,漏掉才是錯。
    ★ 那 HUB 在這裡有什麼用?**讓「本艙的席位目錄不存在」看得見。**
      沒有這一步時,本艙目錄消失只會讓投影少幾筆,而少幾筆與「這艙還沒開席」
      長得一模一樣——又是一個失效值等於正常值的形狀。
    ★ 刻意**綁在 state_root 底下推導,不用 `expanduser("~")`**:
      參考實作(`seat_onboard.seats_dir`)從家目錄推,那對它是對的(它要找活艙);
      在這裡會讓一支被注入 `--state-root` 的測試**吃到真實家目錄的席位檔**,
      治具與真相之間就少一道牆。**同一段推導在兩個地方有兩個正確答案。**
    """
    hub = os.environ.get("AIB_DM_SOCKET") or os.environ.get("EZ2_AIHUB_SOCKET")
    if not hub or not hub.startswith("aihub-"):
        return None
    inst = hub[len("aihub-"):]
    if not inst:
        return None
    return Path(state_root) / "instances" / inst / "seats"


def discover_seat_files(state_root, hub_seats=None):
    """席位檔:狀態根下 seats/ 與 instances/*/seats/ 兩處都算。

    回 [(來源路徑, 內容)],**讀不動的檔照樣回報**(內容=None),
    因為「有一個席位檔我讀不到」跟「沒有那個席位」是兩件事。

    `hub_seats`(EH.2):本艙目錄,由**呼叫端**算好傳進來(本函式不讀環境,
    維持 `project()` 那條「純函數」的承諾)。它一定已經在 instances/* 掃描
    範圍內,傳進來只是為了**去重後仍保證它被掃到**;重複掃同一個檔會讓
    同一個 seat_id 被判成 DUPLICATE_SEAT,那是自己製造的假紅。
    """
    found = []
    roots = [Path(state_root) / "seats"]
    instances = Path(state_root) / "instances"
    if instances.is_dir():
        roots.extend(sorted(p / "seats" for p in instances.iterdir()
                            if p.is_dir()))
    if hub_seats is not None:
        roots.append(Path(hub_seats))
    seen_roots = set()
    deduped = []
    for r in roots:                    # 去重:同一個目錄只掃一次
        key = os.path.realpath(str(r))
        if key in seen_roots:
            continue
        seen_roots.add(key)
        deduped.append(r)
    roots = deduped
    for seats_dir in roots:
        if not seats_dir.is_dir():
            continue
        for path in sorted(seats_dir.glob("*.json")):
            try:
                with open(str(path), "r", encoding="utf-8") as fh:
                    found.append((path, json.load(fh)))
            except (OSError, ValueError):
                found.append((path, None))
    return found


# 顯示投影:給指揮艙右欄三行制用。刻意**不做截斷**——18 欄是那個面板的
# 事,換一個寬度就不同;投影出全文,截斷留給畫的人。
DISPLAY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "display"],
    "properties": {
        "schema_version": {"const": PYRAMID_VERSION},
        "display": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "additionalProperties": False,
                "required": ["display_name", "persona_uid", "route_key",
                             "folder", "brain_binding_status",
                             "brain_status_text"],
                "properties": {
                    "display_name": {"type": "string", "minLength": 1,
                                     "maxLength": 24},
                    "persona_uid": {"type": "string", "minLength": 1,
                                    "maxLength": 128},
                    # null=人格綁了但大腦沒綁(v2);v1 卡永遠有 route。
                    "route_key": {"type": ["string", "null"], "minLength": 1,
                                  "maxLength": 128},
                    "folder": {"type": ["string", "null"], "maxLength": 512},
                    # D13:display 的存在只代表人格可顯示，不能再拿它暗示
                    # 第二大腦是否已綁。兩欄把機器狀態與人讀文字分開保存。
                    "brain_binding_status": {"enum": ["bound", "unbound"]},
                    "brain_status_text": {"enum": ["大腦已綁", "大腦未綁"]},
                    # 以下四欄 v2 才有意義(v1 表沒有 sha):
                    #   persona_sha        登記表上這張卡現在的指紋
                    #   bound_persona_sha  身份層綁定當時釘的指紋(D-2 寫;沒釘=null)
                    #   persona_drift      兩者皆有且不同=卡已更新(右欄提醒)
                    #   persona_ref_legacy persona_card 是舊路徑值、靠 card_ref 橋接到的
                    "persona_sha": {"type": ["string", "null"], "maxLength": 64},
                    "bound_persona_sha": {"type": ["string", "null"], "maxLength": 64},
                    "persona_drift": {"type": "boolean"},
                    "persona_ref_legacy": {"type": "boolean"},
                    # EH.4:這張卡是誰生的。human=人寫、auto=機器綁既有卡、
                    #   created=**機器新建的薄卡**。★ 這一欄**可以不存在**
                    #   (舊投影沒有),而「不存在」的語意是「未標示」,
                    #   **不是 human**——顯示層必須照這個語意畫。
                    "persona_source": {"type": ["string", "null"],
                                       "enum": ["human", "auto", "created", None]},
                },
            },
        },
    },
}


def _folder_of(route_key, contract):
    """route_key -> 契約裡的資料夾(右欄第三行的 📁)。查不到回 None,不編。"""
    for route in (contract or {}).get("routes", []):
        if route.get("routeKey") == route_key:
            return route.get("folder")
    return None


def _unresolved(seat_id, source, reason, detail):
    return {"seat_id": seat_id, "source": str(source), "reason": reason,
            "detail": detail}


def _binding_key(record, seat_id):
    """`bindings.json` 的鍵。**來源是 `seat_address.code_of`,不是 `seat_id`。**

    ★ EH.17 3b-i(艦長 07:28)。為什麼改的是**產生端**而不是遷資料:
      這個檔是投影產物(`brain_write_plan.py:432` 逐字要求先跑 `project`),
      **只遷資料的話,下一次投影會把它靜默寫回舊鍵** —— 而那一次不報錯、沒有紅燈。

    ★ 沒有 uuid 時**補位發舊碼並出聲**,與 `seat_address.label_of` 同款,而且用
      **同一個** `LEGACY_MARK` / `kind=emit` 計數器 —— 不是另寫一份。
      理由是那個計數器就是翻到階段 4 的閘:`code_of` 保持嚴格單源(拿不到就是 None),
      而補位發生在呼叫端並且**被數到**。
    ★ 反過來也要成立:**有 uuid 的席必須安靜**。它若也出聲,那個計數永遠不歸零,
      翻階段的條件會被自己的相容性擋住(判準 B3 守這一格)。
    """
    code = seat_address.code_of(record)
    if code:
        return code
    sys.stderr.write("%s %s seat=%s uuid_code=-\n"
                     % (seat_address.LEGACY_MARK, seat_address.LEGACY_KIND_EMIT, seat_id))
    return seat_id


def project(state_root, cards, contract, seat_files=None):
    """核心投影(純函數:輸入全部由呼叫端給,不自己去讀環境)。

    回 {"bindings": {...}, "unresolved": [...]}。
    **每一個席位檔都會在兩個清單其中之一出現,不會兩邊都沒有。**
    """
    root = Path(state_root)
    sessions_dir = root / "identity" / "sessions"
    agents_dir = root / "identity" / "agents"
    if seat_files is None:
        seat_files = discover_seat_files(root)

    bindings, display, unresolved, seen = {}, {}, [], {}
    for path, record in seat_files:
        if record is None:
            unresolved.append(_unresolved(None, path, REASON_SESSION_UNREADABLE,
                                          "席位檔讀不出來或不是 JSON"))
            continue
        seat_id = record.get("seat_id")
        if not seat_id or not _SEAT_RE.match(str(seat_id)):
            unresolved.append(_unresolved(
                seat_id, path, REASON_SEAT_ID_SHAPE,
                "seat_id 不符 pyramid 的 %s" % _SEAT_RE.pattern))
            continue
        seat_id = str(seat_id)
        if seat_id in seen:
            unresolved.append(_unresolved(
                seat_id, path, REASON_DUPLICATE_SEAT,
                "同一個 seat_id 出現在兩個席位檔:先前見於 %s" % seen[seat_id]))
            continue
        seen[seat_id] = str(path)

        # 1. 席位檔 -> session_uuid。key 不存在與值為 None 都算缺,
        #    但訊息要說清楚是「這個欄位從來沒被寫過」還是「寫了空的」。
        if "session_uuid" not in record:
            unresolved.append(_unresolved(
                seat_id, path, REASON_NO_SESSION_UUID,
                "席位檔沒有 session_uuid 這個欄位(key 不存在,不是值為空)"))
            continue
        session_uuid = record.get("session_uuid")
        if not session_uuid:
            unresolved.append(_unresolved(
                seat_id, path, REASON_NO_SESSION_UUID,
                "session_uuid 欄位存在但是空的(值為 %r)" % (session_uuid,)))
            continue

        # 2. session_uuid -> agent_uid
        session_path = sessions_dir / _safe_name(str(session_uuid))
        if not session_path.is_file():
            unresolved.append(_unresolved(
                seat_id, path, REASON_NO_SESSION_RECORD,
                "session 未綁身份:%s 不存在(先跑 role_model bind-session)"
                % session_path))
            continue
        try:
            session = _read_json(session_path, "session record")
        except ProjectionError as err:
            unresolved.append(_unresolved(seat_id, path,
                                          REASON_SESSION_UNREADABLE, str(err)))
            continue
        agent_uid = (session.get("active") or {}).get("agent_uid")
        if not agent_uid:
            unresolved.append(_unresolved(
                seat_id, path, REASON_SESSION_UNREADABLE,
                "session 檔沒有 active.agent_uid"))
            continue

        # 3. agent_uid -> persona_card
        agent_path = agents_dir / _safe_name(str(agent_uid))
        if not agent_path.is_file():
            unresolved.append(_unresolved(
                seat_id, path, REASON_NO_AGENT_RECORD,
                "身份未註冊:%s 不存在" % agent_path))
            continue
        try:
            agent = _read_json(agent_path, "agent record")
        except ProjectionError as err:
            unresolved.append(_unresolved(seat_id, path,
                                          REASON_AGENT_UNREADABLE, str(err)))
            continue
        card_ref = agent.get("persona_card")
        if not card_ref:
            unresolved.append(_unresolved(
                seat_id, path, REASON_NO_PERSONA_CARD,
                "身份存在但沒綁人格卡(persona_card=%r)" % (card_ref,)))
            continue

        # 4. persona_card -> 登記表(先 uid,再舊 card_ref 橋;v1 表沒有 card_ref 欄
        #    所以只會走第一步)
        entry, legacy = resolve_card(cards, card_ref)
        if entry is None:
            unresolved.append(_unresolved(
                seat_id, path, REASON_CARD_NOT_REGISTERED,
                "人格卡 %r 不在卡片登記表裡" % (card_ref,)))
            continue

        # ★ EH.17 3b-i:鍵**整圈只算一次**。算兩次的話,無碼席會出聲兩次,
        #   而那個 kind=emit 計數就是翻階段 4 的閘 —— 重複計數會讓它永遠到不了 0。
        #   bindings 與 display **共用同一個 key**:兩份在同一圈產出,
        #   鍵不同就只能靠隱含映射 join,而那種失效是靜默的(某席在畫面上少一格)。
        bkey = _binding_key(record, seat_id)

        # 5. route:席位自選的大腦(D-2 寫在身份檔 brain_route_key)優先,其次卡的
        #    預設;兩者都沒有=人格綁了、大腦沒綁 → 進 display 不進 bindings。
        #    有 route 就必須過路由契約。契約讀不到=拒絕,不是放行。
        route_key = agent.get("brain_route_key") or entry.get("route_key")
        if route_key is not None:
            ok, code, detail = check_against_contract(route_key, contract)
            if not ok:
                unresolved.append(_unresolved(
                    seat_id, path, REASON_ROUTE_REFUSED,
                    "route_key %s 未過契約(%s):%s"
                    % (route_key, code, detail)))
                continue
            bindings[bkey] = {"persona_uid": entry["persona_uid"],
                              "route_key": route_key}
        else:
            unresolved.append(_unresolved(
                seat_id, path, REASON_NO_BRAIN_ROUTE,
                "人格 %s 已綁,大腦未綁(卡無預設、席位未選)" % entry["persona_uid"]))

        # ★ 顯示用的那一份**在同一圈裡**產出,不是事後再跑一次。
        #   分兩次跑就會有「兩份輸出對不上」的可能;同一圈產出的兩份
        #   結構上不可能分歧。
        registry_sha = entry.get("persona_sha")
        bound_sha = agent.get("persona_sha")
        display[bkey] = {
            "display_name": entry["display_name"],
            "persona_uid": entry["persona_uid"],
            "route_key": route_key,
            "folder": _folder_of(route_key, contract) if route_key else None,
            "brain_binding_status": "bound" if route_key else "unbound",
            "brain_status_text": "大腦已綁" if route_key else "大腦未綁",
            "persona_sha": registry_sha,
            "bound_persona_sha": bound_sha if isinstance(bound_sha, str) else None,
            "persona_drift": bool(registry_sha and isinstance(bound_sha, str)
                                  and bound_sha != registry_sha),
            "persona_ref_legacy": bool(legacy),
            # EH.4:只有登記表真的說了才寫這一欄。沒說就不寫——
            #   schema 允許缺,而缺的語意是「未標示」不是「人寫的」。
            "persona_source": entry.get("source"),
        }

    return {"bindings": bindings, "display": display, "unresolved": unresolved}


def bindings_document(bindings):
    """包成 pyramid 收得下的檔。**用 pyramid 自己的 schema 驗過才回**——
    投影出來卻餵不進去,是最容易發生也最難發現的一種假成功。"""
    doc = {"schema_version": PYRAMID_VERSION, "bindings": bindings}
    _validate(doc, BINDINGS_SCHEMA, "projected bindings document")
    return doc


def display_document(display):
    """包成指揮艙右欄收得下的檔(同一圈投影的第二個輸出)。"""
    doc = {"schema_version": PYRAMID_VERSION, "display": display}
    _validate(doc, DISPLAY_SCHEMA, "projected display document")
    return doc


def write_display(path, display, sources=None):
    """顯示投影落檔,與 bindings 同樣留收據(同一條紀律,不給第二份待遇)。"""
    return _write_doc(path, display_document(display), len(display), sources)


def provenance_path(bindings_path):
    """投影檔的收據落點(同名 + .provenance.json)。"""
    p = Path(bindings_path)
    return p.with_name(p.name + ".provenance.json")


def write_bindings(path, bindings, sources=None):
    """原子落檔(tmp+rename):rename 前失敗=舊檔完好。

    ★ 同時落一份**收據**(`<檔名>.provenance.json`),記下寫出去那一刻的
    內容 sha256 與來源。理由:模組說明寫著「投影檔不該被編輯,只該被重生」
    ——**那句話在補上這份收據之前,只是一條沒有守衛的規則。**

    收據不是第三個真相源:它不參與推導,只回答「這個檔是不是我產的」。
    有了它,`check` 才分得出兩種完全不同的漂移:

        上游變了   檔案內容 = 我上次寫的,但重投影出來不一樣 → 重生即可
        有人手改   檔案內容 ≠ 我上次寫的                     → 有人動了產物,要查

    **兩種的 rc 都是 1(都該擋),但處置相反**——與本模組其他地方同一條紀律。

    ★★ **界線,寫在這裡免得下一個人把 `check` 綠讀成「投影正確」**:
    收據由**寫檔的同一個行程**產生,所以它抓得到「外部有人手改」,
    **抓不到「投影器自己產錯」**——那種情況下檔案與收據會一致地錯,`check`
    回綠。同理,卡片登記表填錯人,投影會忠實地把錯的綁定產出來,三態全綠。

        check 綠 = 這個檔沒被動過,而且重投影結果相同
        check 綠 ≠ 這些綁定是對的

    後者要靠登記表本身被審過、以及路由契約的裁決,**不是靠收據**。
    `tests/test-binding-projection.py` 有一支測試把這條界線釘成機器事實,
    那支測試轉紅代表有人擴大了收據的宣稱範圍,要回來重讀這一段。
    """
    return _write_doc(path, bindings_document(bindings), len(bindings),
                      sources)


def _write_doc(path, doc, count, sources):
    """原子落檔 + 收據。兩種投影共用同一支,所以待遇不可能不一致。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    tmp = target.with_name(target.name + ".tmp.%d" % os.getpid())
    try:
        with open(str(tmp), "w", encoding="utf-8") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(target))
    finally:
        if tmp.exists():
            try:
                os.unlink(str(tmp))
            except OSError:
                pass
    receipt = {
        "record": "binding-projection-provenance-v1",
        "bindings_sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest(),
        "seat_count": count,
        "sources": sources or {},
    }
    prov = provenance_path(target)
    with open(str(prov), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True,
                            indent=1) + "\n")
    return doc


def _write_drop_report(path, report):
    """單一 report 用 tmp+replace；不宣稱其他輸出也在同一個交易裡。

    ``project`` 還可能寫 bindings/display/provenance；後段 I/O 失敗時,
    前面已完成的單檔 replace 可以留下。本函式不承諾跨檔回復。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    tmp = target.with_name(target.name + ".tmp.%d" % os.getpid())
    try:
        with open(str(tmp), "w", encoding="utf-8") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(target))
    finally:
        if tmp.exists():
            try:
                os.unlink(str(tmp))
            except OSError:
                pass
    return report


def classify_drift(bindings_path, drift):
    """分辨漂移種類。回 (kind, detail)。

    ★ 沒有收據時**不猜**:回 `unknown_no_provenance`,而不是預設成「上游變了」。
    「查不到證據」與「證據顯示某件事」在本模組一律分開,這裡也不例外。
    """
    prov = provenance_path(bindings_path)
    if not prov.is_file():
        return "unknown_no_provenance", "找不到收據 %s,無法判斷這個檔是不是投影產的" % prov
    try:
        receipt = _read_json(prov, "provenance")
    except ProjectionError as err:
        return "unknown_no_provenance", str(err)
    try:
        with open(str(bindings_path), "rb") as fh:
            actual = hashlib.sha256(fh.read()).hexdigest()
    except OSError as err:
        return "unknown_no_provenance", str(err)
    if actual != receipt.get("bindings_sha256"):
        return ("edited_by_hand",
                "檔案內容與收據不符——這個檔在產生之後被改過,不是上游變動")
    if drift:
        return ("upstream_changed",
                "檔案仍是上次投影的產物,但重投影結果不同——重生即可")
    return "clean", "檔案與收據、與重投影結果三者一致"


def diff_bindings(current, projected):
    """比對現有檔與投影。回 [(seat, 現值, 投影值), ...],空=一致。"""
    drift = []
    for seat in sorted(set(current) | set(projected)):
        have, want = current.get(seat), projected.get(seat)
        if have != want:
            drift.append({"seat": seat, "in_file": have, "projected": want})
    return drift


def _digest(path):
    """檔案內容 sha256。讀不到回 None(不猜、不當成空字串)。"""
    if not path:
        return None
    try:
        with open(str(path), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def _sources_block(root, how, cards_path, contract_path, contract):
    """把三個輸入的解析結果攤開。★ 執行情境顯式化的那一半:
    工具說得出自己讀了哪裡,別人才驗得了它讀對沒有。

    ★★ 路由契約附 sha256,理由是實測出來的:08-15 全機有 68 份同名契約
    (每個 worktree 一份),而 `EZ2_ROUTE_REGISTRY` 指的那份有 40 條 route、
    `ez2-ai` 正本樹那份只有 32 條。**同一條指令、不同 shell,會對同一個
    route_key 給出相反的裁決,而兩邊都「成功」。** 只記路徑不夠——路徑一樣
    但 worktree 在別的分支上時,內容仍然不同。摘要才答得出「你用的是哪一份」。
    """
    return {
        "state_root": {"path": str(root), "resolved_by": how},
        "card_registry": {"path": str(cards_path),
                          "sha256": _digest(cards_path)},
        "route_contract": {
            "path": contract_path or default_contract_spec(),
            "loaded": contract is not None,
            "route_count": len(contract.get("routes", [])) if contract else 0,
            # git: 規格(艦外 08-17 裁:D lane 讀契約走 git show main:…)
            # 與檔案路徑都由 contract_digest 算,同一條規則。
            "sha256": contract_digest(contract_path),
        },
    }


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


_UI_TR = {}


def _ui(key, **params):
    """畫面字走四語鍵(語系盤查,10-14);每次照 i18n_lib.resolve_locale() 取(長駐程式切語系也跟得上),讀不到印鍵名。"""
    try:
        _cockpit = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cockpit")
        if _cockpit not in sys.path:
            sys.path.insert(0, _cockpit)
        import i18n_lib
        loc = i18n_lib.resolve_locale()
        tr = _UI_TR.get(loc)
        if tr is None:
            tr = _UI_TR[loc] = i18n_lib.Translator(loc)
        return tr.t(key, params or None)
    except Exception:  # noqa: BLE001 —— 印不出字不能讓程式停
        return key


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="binding-projection",
        description="把身份層投影成 pyramid 的 bindings 檔(投影不是手寫檔)")
    parser.add_argument("command", choices=["project", "check", "validate"])
    parser.add_argument("--state-root", default=None,
                        help="狀態根;預設 EZ2_AIB_STATE_ROOT>EZ2_AIB_HOME>~/.ez2-aib")
    parser.add_argument("--cards", required=True, help="卡片登記表路徑(必要)")
    parser.add_argument("--contract", default=None,
                        help="路由契約路徑;預設走 route_register.default_contract_spec(SPEC > ez2-ai main > REGISTRY)")
    parser.add_argument("--out", default=None,
                        help="project:bindings 輸出路徑(不給只印到 stdout)")
    parser.add_argument("--display-out", default=None,
                        help="project:顯示投影輸出路徑(指揮艙右欄用)")
    parser.add_argument("--bindings", default=None,
                        help="check:要比對的現有 bindings 檔")
    parser.add_argument("--drop-report", default=None,
                        help="project/check:D14 掉落報告輸出路徑")
    parser.add_argument("--drop-evidence", default=None,
                        help="project/check:D14 逐席理由 evidence manifest")
    parser.add_argument(
        "--drop-policy", default=None,
        choices=[DROP_POLICY_PRESERVE_CLOSED],
        help="project/check:D14 政策,目前只接受 preserve_closed",
    )
    args = parser.parse_args(argv)
    # 10-15:沒給 --contract ⇒ 這裡解一次、之後全程用同一個值(以前 load_contract 讀 REGISTRY、別處讀 SPEC,兩份不同版本)
    args.contract = args.contract or default_contract_spec()

    drop_flags = (
        bool(args.drop_report), bool(args.drop_evidence),
        args.drop_policy is not None,
    )
    drop_requested = all(drop_flags)
    if any(drop_flags) and not drop_requested:
        print("binding-projection: " + _ui("aib.bindproj.drop_group"), file=sys.stderr)
        return 2
    if drop_requested and args.command not in ("project", "check"):
        print("binding-projection: " + _ui("aib.bindproj.drop_scope"), file=sys.stderr)
        return 2

    root, how = resolve_state_root(args.state_root)
    try:
        cards = load_card_registry(args.cards)
    except ProjectionError as err:
        print("binding-projection: %s" % err, file=sys.stderr)
        return 2

    if args.command == "validate":
        # ★ 給填表的人用:不碰狀態根、不需要有席位,只回答「這張表寫對了嗎」。
        #   route_key 仍要過契約——**填一條契約裡沒有的路由,現在就要紅**,
        #   不要等到某天投影跑出來才發現整批席位都 unresolved。
        contract = load_contract(args.contract)
        if contract is None:
            print("binding-projection: " + _ui("aib.bindproj.contract_unreadable"), file=sys.stderr)
            return 2
        problems = []
        for card_ref, entry in sorted(cards.items()):
            if entry["route_key"] is None:
                continue  # v2 無預設大腦不是錯,是「待選」
            ok, code, detail = check_against_contract(entry["route_key"],
                                                      contract)
            if not ok:
                problems.append({"card": card_ref,
                                 "route_key": entry["route_key"],
                                 "code": code, "detail": detail})
        sys.stdout.write(_dump({
            "cards": len(cards), "problems": problems,
            "route_contract": {
                "path": args.contract,
                "sha256": contract_digest(args.contract),
                "route_count": len(contract.get("routes", [])),
            },
        }))
        return 1 if problems else 0

    contract = load_contract(args.contract)
    if contract is None:
        # 契約讀不到不是「沒人反對就通過」,是查不到證據 -> rc=2。
        print("binding-projection: " + _ui("aib.bindproj.contract_refuse"), file=sys.stderr)
        return 2

    # D14:先讀 before 與理由 manifest,再做當次投影。這不是形式上的
    # timestamp:若把這段搬到 project() 後面,同一支程式就再也證不出自己
    # 捕捉的是覆寫前那份輸出。
    drop_before_doc = None
    drop_evidence_doc = None
    drop_before_path = None
    drop_captured_at_ms = None
    if drop_requested:
        if args.command == "project" and not args.out:
            print("binding-projection: " + _ui("aib.bindproj.need_out_before"), file=sys.stderr)
            return 2
        if args.command == "check" and not args.bindings:
            print("binding-projection: " + _ui("aib.bindproj.need_bindings_before"), file=sys.stderr)
            return 2
        drop_before_path = Path(
            args.out if args.command == "project" else args.bindings)
        if not drop_before_path.is_file():
            print("binding-projection: " + _ui("aib.bindproj.before_missing", path=drop_before_path), file=sys.stderr)
            return 2

        # 每個角色路徑都必須不同。否則後段一次正常 replace 就可能把
        # before 或 evidence 自己抹掉,而報告仍會看起來成功。
        path_roles = [
            ("before", str(drop_before_path)),
            ("drop-report", str(args.drop_report)),
            ("drop-evidence", str(args.drop_evidence)),
            ("cards", str(args.cards)),
            ("bindings-provenance", str(provenance_path(drop_before_path))),
        ]
        contract_ref = args.contract
        if contract_ref and not str(contract_ref).startswith("git:"):
            path_roles.append(("contract", str(contract_ref)))
        if args.command == "project":
            if args.display_out:
                path_roles.extend([
                    ("display", str(args.display_out)),
                    ("display-provenance",
                     str(provenance_path(args.display_out))),
                ])
        seen_paths = {}
        for role, value in path_roles:
            key = os.path.realpath(os.path.abspath(value))
            if key in seen_paths:
                print("binding-projection: " + _ui("aib.bindproj.path_clash", role=role, value=value, path=seen_paths[key]), file=sys.stderr)
                return 2
            seen_paths[key] = role

        try:
            drop_before_doc = _read_json(drop_before_path, "D14 before bindings")
            _validate(drop_before_doc, BINDINGS_SCHEMA, "D14 before bindings")
            drop_evidence_doc = _read_json(
                args.drop_evidence, "D14 drop evidence")
            _validate_drop_evidence(drop_evidence_doc)
        except ProjectionError as err:
            print("binding-projection: %s" % err, file=sys.stderr)
            return 2
        drop_captured_at_ms = time.time_ns() // 1000000

    # EH.2:本艙目錄算在呼叫端,並在它不存在時**明說**——投影少幾筆與
    #   「這艙還沒開席」在輸出上長得一樣,不講就沒有人會發現。
    _hub_seats = hub_seats_dir(root)
    if _hub_seats is not None and not _hub_seats.is_dir():
        print("binding-projection: " + _ui("aib.bindproj.no_seats_dir", value=os.environ.get("AIB_DM_SOCKET") or os.environ.get("EZ2_AIHUB_SOCKET"), path=_hub_seats), file=sys.stderr)
    try:
        result = project(root, cards, contract,
                         seat_files=discover_seat_files(root, _hub_seats))
    except ProjectionError as err:
        print("binding-projection: %s" % err, file=sys.stderr)
        return 2

    # ★ 卡片表裡**沒有被任何席位用到**的條目,推導鏈根本走不到,所以它的
    #   route_key 不會被檢查。後果:真人把 core.refine 打成 core.refined,
    #   拿到的是 rc=0 加一個空的 bindings——而「空是因為沒有席位」與
    #   「空是因為 route 名打錯」處置相反。所以整張表一律先過一次契約。
    #   (審查意見,08-15;真人打錯字的機率遠高於其他失敗模式。)
    card_problems = []
    for card_ref, entry in sorted(cards.items()):
        if entry["route_key"] is None:
            continue  # v2 無預設大腦不是錯,是「待選」
        ok, code, detail = check_against_contract(entry["route_key"], contract)
        if not ok:
            card_problems.append({"card": card_ref,
                                  "route_key": entry["route_key"],
                                  "code": code, "detail": detail})
    result["card_registry_problems"] = card_problems
    for problem in card_problems:
        print("binding-projection: " + _ui("aib.bindproj.route_key_bad", name=problem["card"], value=problem["route_key"], code=problem["code"]), file=sys.stderr)

    result["sources"] = _sources_block(root, how, args.cards, args.contract,
                                       contract)

    drop_report = None
    if drop_requested:
        try:
            projected_doc = bindings_document(result["bindings"])
            observed_at_ms = max(
                time.time_ns() // 1000000, drop_captured_at_ms)
            before_source = {
                "path": str(drop_before_path),
                "sha256": _canonical_json_digest(
                    drop_before_doc, "D14 before bindings"),
                "captured_at_ms": drop_captured_at_ms,
                "capture_phase": "before_reprojection",
            }
            current_source = {
                "path": (str(args.out) if args.command == "project"
                         else "computed://binding-projection/check"),
                "sha256": _canonical_json_digest(
                    projected_doc, "D14 current bindings"),
                "observed_at_ms": observed_at_ms,
            }
            evidence_source = {
                "path": str(args.drop_evidence),
                "sha256": _canonical_json_digest(
                    drop_evidence_doc, "D14 drop evidence"),
            }
            drop_report = build_drop_report(
                before=drop_before_doc,
                current=projected_doc,
                evidence=drop_evidence_doc,
                before_source=before_source,
                current_source=current_source,
                evidence_source=evidence_source,
                policy=args.drop_policy,
            )
        except ProjectionError as err:
            print("binding-projection: %s" % err, file=sys.stderr)
            return 2

    if args.command == "project":
        try:
            if args.out:
                write_bindings(args.out, result["bindings"],
                               sources=result["sources"])
                result["written_to"] = str(args.out)
                result["provenance"] = str(provenance_path(args.out))
            if args.display_out:
                write_display(args.display_out, result["display"],
                              sources=result["sources"])
                result["display_written_to"] = str(args.display_out)
            if not args.out and not args.display_out:
                bindings_document(result["bindings"])  # 驗形狀但不落檔
                display_document(result["display"])
            if drop_report is not None:
                _write_drop_report(args.drop_report, drop_report)
                result["drop_report_written_to"] = str(args.drop_report)
                result["drop_policy"] = args.drop_policy
        except (ProjectionError, OSError) as err:
            print("binding-projection: %s" % err, file=sys.stderr)
            return 2
        sys.stdout.write(_dump(result))
        return 0

    # check
    if not args.bindings:
        print("binding-projection: " + _ui("aib.bindproj.check_need_bindings"), file=sys.stderr)
        return 2
    current_path = Path(args.bindings)
    if not current_path.is_file():
        print("binding-projection: " + _ui("aib.bindproj.bindings_missing", path=current_path), file=sys.stderr)
        return 2
    if drop_requested:
        current_doc = drop_before_doc
    else:
        try:
            current_doc = _read_json(current_path, "existing bindings")
            _validate(current_doc, BINDINGS_SCHEMA, "existing bindings")
        except ProjectionError as err:
            print("binding-projection: %s" % err, file=sys.stderr)
            return 2

    drift = diff_bindings(current_doc["bindings"], result["bindings"])
    kind, detail = classify_drift(current_path, drift)
    result["drift"] = drift
    result["drift_kind"] = {"kind": kind, "detail": detail}
    if drop_report is not None:
        try:
            _write_drop_report(args.drop_report, drop_report)
        except OSError as err:
            print("binding-projection: D14 report write failed:%s" % err,
                  file=sys.stderr)
            return 2
        result["drop_report_written_to"] = str(args.drop_report)
        result["drop_policy"] = args.drop_policy
    sys.stdout.write(_dump(result))
    # ★ 手改即使沒有造成語意漂移也算不合格:「不該被編輯」是規則本身,
    #   不是「只要結果一樣就沒關係」。
    return 1 if (drift or kind == "edited_by_hand") else 0


if __name__ == "__main__":
    sys.exit(main())
