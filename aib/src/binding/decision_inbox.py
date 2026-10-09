# SPDX-License-Identifier: Apache-2.0
"""E5.3 候審包進決策收件匣:核可/駁回都落 v1 receipt,AI 不得自批。

大綱行:候審 UI 進決策收件匣(核可/駁回落 receipt)。

收件匣是 L7 人層共用基建(E5.3 設計稿 §4.2):E2.6 綁定佇列是第一個租戶,
本模組讓候審萃煉包成為第二個——同一個匣、同一種 item 形狀(kind 區分),
不另起爐灶。落點沿 $EZ2_DECISION_INBOX(預設 ~/.ez2-aib/decision-inbox)。

兩條契約判準:
  1. **核可與駁回都落 receipt**——只記核可等於把駁回的歷史丟掉。
     落點=設計稿 §3 正本 ~/.ez2-persona-runtime/approval-receipts.jsonl
     (EZ2_APPROVAL_RECEIPTS_PATH 可注入),append-only 不改寫舊行;讀端三驗
     (schema/receipt_hash/expires),無效與過期具名排除不靜默。落檔後反悔
     的反向流程候 E5.1 消費端語意一起定,本模組現無此動作(具名殘餘)。
  2. **AI 不得自批**——approver 必須 human:(AI 的 role@hex/Engine sig 擋在
     正則外),且 submitter 的 sig/ai_id 出現在 approver 字串內即拒
     (防「human:pm@xxxxx/Claude」前綴偽裝)。

receipt = ez2-persona-approval-receipt **v1 全欄**(整合席裁示,設計稿 §3
第 82 行契約設計本意):binding 結構完備 + truth_ceiling 五個鍵自我聲明。
★ 那五個值原本是**寫死的 const**;PKG-CEILING-FLIP 甲件(判準 0e9fd6f7)之後
改成向 `fleet_identity/ceiling_state` 現查導出——owner source 未設定時導出的值
與舊值逐字相同(`owner_authority_source_unconfigured`),收據仍然自己說
「我還不是正式權威」。**翻的是「從寫死變成可測」,不是「從否變成是」。**

schema 正本與副本:正本=ez2-ai origin/dev:schemas/ez2-persona-approval-receipt
.schema.json(sha256=85215b0da5b27406cd1c3a64aac9f343c7f2b7bb74be6d7fcff8338b
e65675c9)。本目錄 approval-receipt.schema.json 是其**語法等價改寫副本**,
因 src/ledger/schema_validator 不支援 anyOf/format 兩關鍵字(fail-closed 拒收):
anyOf→oneOf(該處兩分支互斥,語意等價)、format:date-time→ISO8601 pattern
(加嚴,原 format 多數驗證器本就只當註記)。**一處延伸**(E5.3 接縫,規格 9daffe7
甲案):candidateBinding 多一個可選欄 write_receipt(寫入端 brain_write_plan 閘三認得的
收據,核可端簽、同一把鑰匙)——正本 schema 尚未跟上,候 ez2-ai 端 schema bump;在那之前
本副本與正本**不再逐字等價**,這句話就是那個差異的登記。
**第二處差異(PKG-CEILING-FLIP 甲件登記)**:truth_ceiling **四鍵**(owner_authority_status /
authority_level / formal_eligible / main_adapter_connected)由 `const` 放寬成 `enum`/`type`
(舊值全部留在 enum 內,`additionalProperties:false` 與 `required` 不動)。
★ **是四不是五**:`formal_writes` 維持 `const: 0` —— 它那個 const 不是型別宣告,是把
「還沒實作」釘死在機器上,本批沒有任何路徑會產出非 0 的值,放寬它等於安靜地拿掉一個保證
(判準 qa3/criteria-ceiling-flip-0827 更正四 §一)。
★ 這一處**不是語法改寫,是語意放寬**:本副本會收下正本仍會拒的新值。
正本(ez2-ai)端的 schema bump **不在本葉、也不在本席權限內**,候跨庫裁示;
在那之前這個差異就是這句話登記的東西。其餘逐字未動。寫入即 validate,
invalid 不落檔(fail-closed)。

設計稿既定取捨(照抄,不再自創):
  - §6-2 package_binding 輕量對映:聚合卡直接對映,不先升格 candidate。
    開放狀態記於此;x-binding-mode 註記落在 **item**(receipt schema
    additionalProperties:false 容不下額外欄——這是對設計稿 §6-2「receipt 內
    註記」的一處必要偏離,具名記錄)。
  - remap 語意:單筆 approved 綁改判後內容(§3 decision 欄「建議後者」)——
    改判由上游改 package 後重新進匣,本模組不另設 remap 動作。
  - HMAC 密鑰:~/.ez2-persona-runtime/.approval-hmac-key(600,首用自建);
    路徑可注入(EZ2_APPROVAL_HMAC_KEY_PATH)供密閉測試。權限過鬆=拒簽。
  - high 風險二次確認(§2.3):approve 高風險卡要輸入 yes 全字。狀態機
    not_required / pending(卡上 pending_second)/ completed;二次確認未完成
    **不發 receipt**(E5.1 消費端不會看到半授權)。駁回不需二次確認。

失敗序:組 receipt → schema validate(不過即拋,零寫入)→ 帳本事件 →
receipt 追加 → item 原子改寫。receipt 追加失敗時 item 不動,帳本多一筆
「意圖已留痕但未生效」事件——寧可帳本多痕,不可 item 與 receipt 不一致。

exit(CLI): 0 成功 · 2 申請/裁決不合規(fail-closed,一 byte 不改)
"""

import hashlib
import hmac as hmac_mod
import json
import os
import re
import stat
import sys
import uuid
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))   # landing(gate) 的 path 以此為根,不看 cwd
sys.path.insert(0, os.path.join(_HERE, "..", "ledger"))
sys.path.insert(0, os.path.join(_HERE, "..", "collectors"))

from ledger import Ledger  # noqa: E402
from schema_validator import SchemaValidator, SchemaValidationError  # noqa: E402
from collector_common import clip  # noqa: E402

from bind_queue import inbox_dir, _write_atomic, default_ledger_path  # noqa: E402 — 同匣同慣例,不重造

KIND = "review_package"
STATUS_PENDING = "pending"
STATUS_PENDING_SECOND = "pending_second"   # high 卡首確認已收,候 yes 全字
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUSES = (STATUS_PENDING, STATUS_PENDING_SECOND,
            STATUS_APPROVED, STATUS_REJECTED)

SCHEMA_PATH = os.path.join(_HERE, "approval-receipt.schema.json")
SCHEMA_VERSION = "2026-07-20.ez2-persona-approval-receipt.v1"
RECEIPT_KIND = "persona_owner_approval"

KEY_ENV = "EZ2_APPROVAL_HMAC_KEY_PATH"
DEFAULT_KEY_PATH = "~/.ez2-persona-runtime/.approval-hmac-key"
# receipts 落點=設計稿 §3 正本(一輪審中5:偏離自己的設計 artifact,E5.1 依
# 設計路徑會讀不到)。env 可注入供密閉測試。
RECEIPTS_ENV = "EZ2_APPROVAL_RECEIPTS_PATH"
DEFAULT_RECEIPTS_PATH = "~/.ez2-persona-runtime/approval-receipts.jsonl"
EXPIRES_DAYS = 90                      # 對齊 candidate privacy retention 上限

_PKG_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
# item_id 白名單先驗再拼路徑(一輪審中3,E0.5 同課:../ 穿越)
_ITEM_ID_RE = re.compile(r"^review-[a-z0-9][a-z0-9.-]{0,63}$")
# candidateUid 形狀(schema $defs;二輪審中6:candidate_uid=personaEventId)
_CAND_UID_RE = re.compile(r"^cand_[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}"
                          r"-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SIG_RE = re.compile(r"^[a-z0-9-]{1,32}@[0-9a-f]{4,8}/[A-Za-z0-9._-]{1,32}$")
_HUMAN_RE = re.compile(r"^human:[^\s]{1,64}$")
_PERSONA_UID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

_validator = None


class InboxError(ValueError):
    """申請或裁決不合規 —— fail-closed,不進匣、不改狀態、不落 receipt。"""


def _get_validator():
    global _validator
    if _validator is None:
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            _validator = SchemaValidator(json.load(fh))
    return _validator


def _sha(text):
    if isinstance(text, str):
        text = text.encode("utf-8")
    return hashlib.sha256(text).hexdigest()


def _canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _uid(prefix):
    return "%s_%s" % (prefix, uuid.uuid4())


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000.0, timezone.utc).isoformat()


def _now_ms(now_ms):
    if now_ms is not None:
        return int(now_ms)
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def receipts_path():
    return os.path.expanduser(os.environ.get(RECEIPTS_ENV)
                              or DEFAULT_RECEIPTS_PATH)


def _item_path(base, item_id):
    if not _ITEM_ID_RE.match(str(item_id or "")):
        raise InboxError("item_id 不合法(白名單 regex 先驗再拼路徑):%r"
                         % (item_id,))
    return os.path.join(inbox_dir(base), "%s.json" % item_id)


def content_hash_of(package):
    """候審包**內容**指紋:canonical JSON sha256。

    排除進匣時生成的揮發性 uid(package_uid/candidate_uid)——那是標籤不是
    內容;同一份訊號重投會重生 uid,若入指紋,冪等(同 id 同內容)就永遠
    不成立。標籤竄改不改變語意,內容竄改(訊號/風險/摘要)必被抓。
    """
    body = {k: v for k, v in package.items() if k != "package_uid"}
    if "candidates" in body:
        body["candidates"] = [
            {k: v for k, v in c.items() if k != "candidate_uid"}
            for c in body["candidates"]]
    return _sha(_canon(body))


# -- HMAC 密鑰 ---------------------------------------------------------------

def key_path():
    return os.path.expanduser(os.environ.get(KEY_ENV) or DEFAULT_KEY_PATH)


def load_hmac_key():
    """讀(或首用自建)審核密鑰。權限過鬆=拒簽——鑰匙誰都能讀,簽章就不是
    簽章(設計稿 §6-3:密鑰強度與權威等級相稱,truth_ceiling 已聲明層級)。"""
    path = key_path()
    if not os.path.exists(path):
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
            os.chmod(d, 0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, os.urandom(32).hex().encode("ascii"))
        finally:
            os.close(fd)
        os.chmod(path, 0o600)
    mode = stat.S_IMODE(os.lstat(path).st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise InboxError("審核密鑰權限過鬆(%s=%s),拒簽;chmod 600 後重試"
                         % (path, oct(mode)))
    with open(path, "rb") as fh:
        key = fh.read().strip()
    if len(key) < 32:
        raise InboxError("審核密鑰過短(<32 bytes),拒簽")
    return key


# -- 候審包 -------------------------------------------------------------------

def make_package(package_id, submitter_sig, submitter_ai_id, dimension,
                 summary, persona_uid, base_persona_sha, expected_generation,
                 signals, risk_level="normal", engine=None):
    """組一筆候審包(純資料)。submitter 身份原樣入卡——自批執法要用它。

    signals:訊號列表(每筆 dict,可帶 high_risk bool)。每訊號成一筆
    candidate(§3 candidate_bindings 一訊號一筆),cand uid 此刻生成。
    """
    if not package_id or not _PKG_ID_RE.match(str(package_id)):
        raise InboxError("package_id 必須是小寫英數點連字號(≤64):%r" % package_id)
    if not submitter_sig or not _SIG_RE.match(str(submitter_sig)):
        raise InboxError("submitter_sig 形狀不合(要 role@hex/Engine):%r"
                         % submitter_sig)
    if not submitter_ai_id:
        raise InboxError("缺 submitter_ai_id")
    if not dimension or not str(dimension).strip():
        raise InboxError("缺 dimension")
    if not summary or not str(summary).strip():
        raise InboxError("缺 summary")
    if not persona_uid or not _PERSONA_UID_RE.match(str(persona_uid)):
        raise InboxError("persona_uid 形狀不合:%r" % persona_uid)
    if not base_persona_sha or not _HASH_RE.match(str(base_persona_sha)):
        raise InboxError("base_persona_sha 必須是 64 位 hex:%r" % base_persona_sha)
    try:
        expected_generation = int(expected_generation)
    except (TypeError, ValueError):
        raise InboxError("expected_generation 必須是整數")
    if expected_generation < 0:
        raise InboxError("expected_generation 不得為負")
    if risk_level not in ("normal", "high"):
        raise InboxError("risk_level 必須是 normal 或 high")
    if not isinstance(signals, list) or not signals:
        raise InboxError("signals 必須是非空列表(candidate_bindings minItems=1)")
    if len(signals) > 256:
        raise InboxError("signals 超過 256(schema maxItems)")
    candidates = []
    seen_uids = set()
    for sig in signals:
        if not isinstance(sig, dict):
            raise InboxError("每筆 signal 必須是物件:%r" % (sig,))
        # 二輪審中6:設計稿 §3 candidate_uid=personaEventId——來源在卡內
        # (staging 事件帶該欄)就用它;缺欄的合成情境 fallback 生 cand uid,
        # 此 fallback 僅供無 staging 血緣的候審包(如測試/手工卡)。
        source_uid = sig.get("personaEventId")
        if source_uid is not None:
            cuid = str(source_uid)
            if not _CAND_UID_RE.match(cuid):
                raise InboxError("personaEventId 不合 candidateUid 形狀,"
                                 "拒收:%r" % source_uid)
        else:
            cuid = _uid("cand")
        if cuid in seen_uids:
            raise InboxError("candidate_uid 重複:%s" % cuid)
        seen_uids.add(cuid)
        candidates.append({
            "candidate_uid": cuid,
            "content": sig,
            "high_risk": bool(sig.get("high_risk", risk_level == "high")),
        })
    _check_risk_consistency(risk_level, candidates)
    return {
        "package_id": str(package_id),
        "package_uid": _uid("pkg"),
        "submitter": {
            "sig": str(submitter_sig),
            "ai_id": str(submitter_ai_id),
            "engine": str(engine or submitter_sig.rsplit("/", 1)[-1]),
        },
        "dimension": str(dimension).strip(),
        "summary": str(summary).strip(),
        "persona_uid": str(persona_uid),
        "base_persona_sha": str(base_persona_sha),
        "expected_generation": expected_generation,
        "risk_level": risk_level,
        "candidates": candidates,
    }


def _check_risk_consistency(risk_level, candidates):
    """一輪審高2 的矛盾閘:package 宣稱 high 但沒有任何 candidate 標 high
    =receipt 的 risk_level 與 candidate_bindings 互相打臉,消費端看哪邊都錯。
    入口(make_package)與裁決(resolve)都驗——卡檔可被手工捏造。"""
    if risk_level == "high" and not any(c.get("high_risk")
                                        for c in candidates):
        raise InboxError("風險矛盾:package risk_level=high 但無任何 candidate "
                         "high_risk——拒發 receipt")


def effective_high(package):
    """有效風險:package 頂層 high **或**任一 candidate high(一輪審高2:
    二確觸發只看頂層=candidate 級 high 可被繞過)。"""
    return package["risk_level"] == "high" or \
        any(c.get("high_risk") for c in package.get("candidates", []))


def item_id_of(package):
    return "review-%s" % package["package_id"]


def _load_item_raw(item_id, base=None):
    """讀一張卡,回 (item, 原始 bytes)。

    一輪審中4:壞卡是 fail-closed 錯誤,不是「不存在」——把損壞當缺席會讓
    enqueue 冪等路徑靜默覆寫它。缺席回 (None, None);損壞拋 InboxError。
    一輪審高5:原始 bytes 一併回傳,receipt 的 package_file_sha256 與其他
    欄位必須同源自同一次讀取,不得裁決中途重讀檔案(TOCTOU)。
    """
    path = _item_path(base, item_id)
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        return json.loads(raw.decode("utf-8")), raw
    except (OSError, ValueError) as err:
        raise InboxError("卡檔損壞或不可讀(fail-closed,不視為不存在):%s:%s"
                         % (item_id, err))


def load_item(item_id, base=None):
    item, _raw = _load_item_raw(item_id, base)
    return item


def list_items_report(base=None, status=None):
    """列卡,回 {"items": [...], "broken": [...]}。

    二輪審中3:壞卡靜默跳過=overdue 的 high 卡可以無聲消失。列表不因壞卡
    爆掉(回饋器永不擋),但損壞要 fail-visible:計數與檔名具名回報。
    """
    d = inbox_dir(base)
    items, broken = [], []
    if not os.path.isdir(d):
        return {"items": items, "broken": broken}
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                item = json.load(fh)
        except (OSError, ValueError) as err:
            broken.append({"file": name, "error": str(err)[:120]})
            continue
        if item.get("kind") != KIND:
            continue
        if status and item.get("status") != status:
            continue
        items.append(item)
    return {"items": items, "broken": broken}


def list_items(base=None, status=None):
    """只回可讀卡。損壞明細(不得靜默)見 list_items_report。"""
    return list_items_report(base, status)["items"]


def _confirmation_mac_ok(conf, key):
    """讀端 HMAC 重驗:以本機密鑰對「存的 challenge_hash+payload_hash」重算。
    偽造者可以自算 receipt_hash(無鑰即可),但算不出這個。"""
    try:
        expect = hmac_mod.new(
            key,
            (conf.get("challenge_hash", "") + conf.get("payload_hash", ""))
            .encode("ascii"),
            hashlib.sha256).hexdigest()
    except (TypeError, UnicodeEncodeError):
        return False
    return hmac_mod.compare_digest(str(conf.get("hmac_sha256", "")), expect)


def _invariant_violation(rec):
    """語意不變式(二輪審高2 順帶):欄位各自合 schema 但互相打臉的 receipt
    不得放行——high↔二確一致、UID 對應、兩步時序。回 None=通過。"""
    confs = rec.get("confirmations") or []
    hrsc = rec.get("high_risk_second_confirmation") or {}
    steps = [c.get("step") for c in confs]
    if rec.get("decision") == "approved" and rec.get("risk_level") == "high":
        if hrsc.get("required") is not True or hrsc.get("status") != "completed":
            return "high 核可但二確狀態非 required+completed"
        if steps != ["primary", "high_risk_second"]:
            return "high 核可的確認步序不對:%s" % steps
        if (hrsc.get("first_confirmation_uid")
                != confs[0].get("confirmation_uid")
                or hrsc.get("second_confirmation_uid")
                != confs[1].get("confirmation_uid")):
            return "二確 UID 與 confirmations 不對應"
        if confs[0].get("confirmed_at", "") > confs[1].get("confirmed_at", ""):
            return "二確時序倒置(第二步早於第一步)"
    else:
        if hrsc.get("required") or hrsc.get("status") != "not_required":
            return "非 high 核可卻帶二確要求"
        if steps != ["primary"]:
            return "應單一 primary 確認,實得:%s" % steps
    return None


def load_receipts_report(now_ms=None):
    """讀 receipt 帳並逐筆驗證,回 {"valid": [...], "excluded": [...]}。

    一輪審高3 三驗:schema、receipt_hash 重算、expires。二輪審高2+中2 補
    第四驗:**HMAC 重驗**——密鑰就在本機,receipt_hash 任何人都能重算,
    HMAC 才是不可偽造的唯一支柱,讀端不驗它=偽造整張 receipt 可過前三驗;
    加語意不變式(high↔二確一致/UID 對應/時序)。無效/過期 fail-closed
    排除且**不靜默**(排除清單具名);密鑰不可用=全部排除,具名。
    非 JSON 行=帳損壞,整支拋錯(與事故登錄簿同教訓)。
    """
    path = receipts_path()
    if not os.path.isfile(path):
        return {"valid": [], "excluded": []}
    now = _now_ms(now_ms)
    try:
        key = load_hmac_key()
        key_err = None
    except InboxError as err:
        key, key_err = None, str(err)
    valid, excluded = [], []
    with open(path, encoding="utf-8") as fh:
        for index, raw in enumerate(fh):
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError as err:
                raise InboxError("receipt 帳第 %d 行無法解析:%s"
                                 % (index + 1, err))
            reason = None
            try:
                _get_validator().validate(rec)
            except SchemaValidationError as err:
                reason = "schema 不合:%s" % str(err)[:120]
            if reason is None:
                body = {k: v for k, v in rec.items() if k != "receipt_hash"}
                if rec.get("receipt_hash") != _sha(_canon(body)):
                    reason = "receipt_hash 重算不符(內容被動過或手插)"
            if reason is None:
                try:
                    exp = datetime.fromisoformat(rec["expires_at"])
                    if exp.timestamp() * 1000 <= now:
                        reason = "已過期(%s)——過期 receipt 不再授權併入" \
                                 % rec["expires_at"]
                except (ValueError, KeyError, TypeError) as err:
                    reason = "expires_at 不可解析:%s" % err
            if reason is None and key is None:
                reason = "密鑰不可用,無法重驗 HMAC(fail-closed):%s" % key_err
            if reason is None:
                for pos, conf in enumerate(rec.get("confirmations") or []):
                    if not _confirmation_mac_ok(conf, key):
                        reason = ("HMAC 重驗不符(第 %d 步)——"
                                  "不可偽造支柱失效,疑偽造" % (pos + 1))
                        break
            if reason is None:
                reason = _invariant_violation(rec)
            if reason is None:
                valid.append(rec)
            else:
                excluded.append({"line": index + 1,
                                 "receipt_uid": rec.get("receipt_uid"),
                                 "reason": reason})
    return {"valid": valid, "excluded": excluded}


def load_receipts(now_ms=None):
    """讀 receipt 帳,只回驗證通過且未過期的。排除明細見 load_receipts_report。"""
    return load_receipts_report(now_ms)["valid"]


def enqueue(package, ledger_path, base=None, now_ms=None):
    """候審包進匣並落一筆 decision.requested。

    冪等=同 id **且同 content_hash**(一輪審中4):同 id 不同內容是撞名或
    覆寫企圖,拒;壞卡由 _load_item_raw 拋錯,不會被當缺席而覆寫。
    """
    iid = item_id_of(package)
    existing = load_item(iid, base)   # 壞卡在此即拋,fail-closed
    if existing:
        if existing.get("content_hash") != content_hash_of(package):
            raise InboxError("同 id 但內容不同(%s)——收件匣不覆寫,"
                             "改包請換 package_id 重新進匣" % iid)
        return existing, False

    sub = package["submitter"]
    led = Ledger(ledger_path, node_id="decision-inbox")
    event = led.append({
        "kind": "decision.requested",
        "actor": {"ai_id": sub["ai_id"], "sig": sub["sig"],
                  "kind": "main", "engine": sub["engine"]},
        "body": {
            "summary": {
                "zh-TW": clip("候審包 %s(%s,%s)進決策收件匣候人裁決" % (
                    package["package_id"], package["dimension"],
                    package["risk_level"])),
                "en": clip("review package %s (%s, %s) queued for human decision" % (
                    package["package_id"], package["dimension"],
                    package["risk_level"])),
            },
            "subject": {"kind": "decision", "id": iid},
        },
        "causation": {"causation_id": None, "correlation_id": "E5.3"},
    }, wall_ms=now_ms)

    item = {
        "schema_version": "0.1.0-draft",
        "kind": KIND,
        "item_id": iid,
        "status": STATUS_PENDING,
        "package": package,
        "content_hash": content_hash_of(package),
        # §6-2 開放問題的落點:receipt schema additionalProperties:false
        # 容不下註記,x-binding-mode 記在卡上(具名偏離,見模組 docstring)
        "x_binding_mode": "lightweight-aggregation-card",
        "created_event_id": event["event_id"],
        "created_at_ms": event["hlc"]["wall_ms"],
        "pending_confirmation": None,
        "resolution": None,
    }
    _write_atomic(_item_path(base, iid), item)
    return item, True


# -- 裁決 ---------------------------------------------------------------------

def _memory_module(name):
    """src/memory 底下的模組(brain_write_plan / distill_contract)。延後匯入:寫入端為了讀同一把
    鑰匙會匯入本模組,兩端都延後就不繞圈;匯不到就讓它炸——不得退成「不簽/不檢查」。"""
    p = os.path.join(_HERE, "..", "memory")
    if p not in sys.path:
        sys.path.insert(0, p)
    return __import__(name)


def _fleet_identity_module(name):
    """src/fleet_identity 底下的模組(ceiling_state)。與 _memory_module 同慣例、同理由:
    延後匯入,匯不到就讓它炸——天花板讀不到不得退成「當作沒翻」。"""
    p = os.path.join(_HERE, "..", "fleet_identity")
    if p not in sys.path:
        sys.path.insert(0, p)
    return __import__(name)


def _ceiling_fields():
    return _fleet_identity_module("ceiling_state").ceiling_fields()


def CEILING_SITE(env=None, source=None, adapter=None):
    """判準 H2 的掛鉤:本處**當下宣告的**天花板狀態。有這個屬性代表本處已翻。

    ★ 它與 _build_receipt 走同一條路(同一個 ceiling_fields),所以「入口讀到的」與
    「收據上真的寫的」不會是兩個答案——兩份真相的失效形狀是不一致時沒有格子會紅。
    """
    return _fleet_identity_module("ceiling_state").ceiling_fields(
        env=env, source=source, adapter=adapter)


def _guard_distill_landing(candidates):
    """E5.2 候選(內容含 rule_text)沒有合型別 landing **不得核可**——「補不出 landing」是正當的
    退回理由,不是待辦(規格 9daffe7 §3.3)。型別判準沿用 distill_contract.check_landing,不另寫
    近似版;非 distill 包(無 rule_text)不受影響。在任何狀態改寫之前呼叫:拒=零收據、卡仍 pending。"""
    dc = _memory_module("distill_contract")
    for cand in candidates:
        content = cand.get("content")
        if not isinstance(content, dict) or "rule_text" not in content:
            continue
        landing = content.get("landing")
        if landing is None or (isinstance(landing, str) and not landing.strip()):
            raise InboxError("候選 %s 的 landing 是空的——E5.2 候選沒有 landing 不得核可;"
                             "填不出來就駁回,那是正當理由不是待辦" % cand.get("candidate_uid"))
        try:
            dc.check_landing(landing, repo_root=_REPO_ROOT)
        except dc.ContractError as err:
            raise InboxError("候選 %s 的 landing 不合型別,拒核可:%s"
                             % (cand.get("candidate_uid"), err))


def _write_receipt_for(candidate_uid, content_hash, receipt_uid, approver, maker_sig, key, ms):
    """寫入端(brain_write_plan 閘三)認得的那一份收據——核可端簽、同一把鑰匙、同一個 payload
    定義(呼叫寫入端的 receipt_payload,不重造)。

    ★ `reviewer_kind="human"` 在這裡、也**只**在這裡被寫下:它是「有人真的按了鍵」的第一手
    事實——來源是 _guard_not_self_approval 已擋過 approver 必須 human:。任何下游補上、推導、
    預設它,都是把「人核可」退回成「有人宣稱人核可過」(規格 9daffe7 §3.1 丙案否決理由;
    判準 P1e 以 AST 釘 src/ 其他層不得產這個常數)。簽章涵蓋 reviewer_kind。
    """
    wr = {
        "receipt_id": receipt_uid,
        "reviewer": approver,
        "reviewer_kind": "human",
        "maker": maker_sig,
        "approved_at_ms": ms,
        "content_sha256": content_hash,
    }
    payload = _memory_module("brain_write_plan").receipt_payload(candidate_uid, wr)
    wr["hmac"] = hmac_mod.new(key, payload, hashlib.sha256).hexdigest()
    return wr


def _guard_not_self_approval(item, approver):
    """AI 不得自批:approver 必須 human:,且 submitter 身份不得藏在其中。"""
    if not approver or not _HUMAN_RE.match(str(approver)):
        raise InboxError("approver 必須是 human:<名字>——AI 不得核可候審包,"
                         "候審的意義就是機器不確定才找人")
    sub = item["package"]["submitter"]
    for token in (sub.get("sig"), sub.get("ai_id")):
        if token and token in approver:
            raise InboxError("自批拒絕:approver 內含 submitter 身份(%s)——"
                             "human: 前綴不是偽裝通道" % token)


def _make_confirmation(step, key, item, decision, approver, ms):
    nonce = os.urandom(32).hex()
    payload_hash = _sha(_canon({
        "item_id": item["item_id"], "decision": decision,
        "approver": approver, "content_hash": item["content_hash"],
        "step": step}))
    challenge_hash = _sha(nonce + item["content_hash"])
    return {
        "confirmation_uid": _uid("cnf"),
        "step": step,
        "nonce_hash": _sha(nonce),
        "challenge_hash": challenge_hash,
        "confirmed_at": _iso(ms),
        "expires_at": _iso(ms + EXPIRES_DAYS * 86400 * 1000),
        "payload_hash": payload_hash,
        "hmac_sha256": hmac_mod.new(
            key, (challenge_hash + payload_hash).encode("ascii"),
            hashlib.sha256).hexdigest(),
    }


def verify_confirmation(conf, key, item_id, decision, approver, content_hash):
    """驗一筆確認:payload 重算要相符,HMAC 以本機密鑰重算要相符。"""
    expect_payload = _sha(_canon({
        "item_id": item_id, "decision": decision, "approver": approver,
        "content_hash": content_hash, "step": conf.get("step")}))
    if conf.get("payload_hash") != expect_payload:
        return False
    expect_mac = hmac_mod.new(
        key, (conf.get("challenge_hash", "") + expect_payload).encode("ascii"),
        hashlib.sha256).hexdigest()
    return hmac_mod.compare_digest(conf.get("hmac_sha256", ""), expect_mac)


def _authority_binding(key):
    """E5.5 adapter 未接期:以本機審核密鑰派生值填(設計稿 §3),真權威候
    adapter;truth_ceiling 同時聲明 unconfigured,收據自己說自己不是權威。"""
    fp = _sha(key)
    return {
        "source": "main_native_owner_adapter",
        "authority_receipt_sha256": _sha("local-authority-receipt:" + fp),
        "owner_subject_hash": _sha("owner-subject:" + fp),
        "key_id": "local:approval-hmac:%s" % fp[:16],
        "authority_generation": 1,
    }


def _build_receipt(item, decision_word, approver, key, ms, confirmations,
                   second_state, file_sha):
    """組 receipt。file_sha 由呼叫方自「同一次讀取」的原始 bytes 算出
    (一輪審高5:裁決中途重讀檔案=TOCTOU,欄位與 hash 必須同源)。
    risk_level 填**有效風險**(頂層或 candidate 任一 high 即 high)。"""
    pkg = item["package"]
    receipt_uid = _uid("apr")
    patch_hashes = []
    cand_bindings = []
    for cand in pkg["candidates"]:
        chash = _sha(_canon(cand["content"]))
        patch_hashes.append(chash)
        binding = {
            "candidate_uid": cand["candidate_uid"],
            "candidate_content_hash": chash,
            "exact_patch_hash": chash,   # 訊號場景無 patch(設計稿 §3)
            "high_risk": cand["high_risk"],
        }
        if decision_word == "approved":
            # E5.3 接縫:核可的候選每筆帶一份寫入端收據(駁回不帶——駁回沒有可寫的東西)
            binding["write_receipt"] = _write_receipt_for(
                cand["candidate_uid"], chash, receipt_uid, approver,
                pkg["submitter"]["sig"], key, ms)
        cand_bindings.append(binding)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "receipt_uid": receipt_uid,
        "receipt_kind": RECEIPT_KIND,
        "decision": decision_word,
        "risk_level": "high" if effective_high(pkg) else "normal",
        "authority_binding": _authority_binding(key),
        "package_binding": {
            "package_uid": pkg["package_uid"],
            "package_hash": item["content_hash"],
            "package_file_sha256": file_sha,
            "persona_uid": pkg["persona_uid"],
            "base_persona_sha": pkg["base_persona_sha"],
            "expected_generation": pkg["expected_generation"],
            "exact_patch_set_hash": _sha(_canon(sorted(patch_hashes))),
        },
        "candidate_bindings": cand_bindings,
        "confirmations": confirmations,
        "high_risk_second_confirmation": second_state,
        "issued_at": _iso(ms),
        "expires_at": _iso(ms + EXPIRES_DAYS * 86400 * 1000),
        # ★ 五個**鍵名**逐字不動(正本 schema 核對,勿改字);**值**已由寫死改成向
        #   ceiling_state 現查導出(PKG-CEILING-FLIP 甲件,判準 0e9fd6f7)。
        #   owner source 未設定時導出的值與舊值逐字相同——翻的是「從寫死變成可測」。
        "truth_ceiling": _ceiling_fields(),
    }
    receipt["receipt_hash"] = _sha(_canon(receipt))
    return receipt


def _append_receipt(receipt):
    path = receipts_path()
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
        os.chmod(d, 0o700)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")


def resolve(item_id, decision, approver, ledger_path, base=None,
            reason=None, now_ms=None, second_confirmation=None):
    """人裁決一張卡。decision = approve | reject,**兩者都落 receipt**。

    高風險卡(有效風險=頂層或任一 candidate high,一輪審高2)approve 的
    二次確認**必須是獨立的第二次呼叫**(一輪審高1b:同呼叫帶 yes=二確
    形同虛設):第一次呼叫→首確認落卡、pending_second、不發 receipt;
    第二次呼叫(同 approver+second_confirmation="yes")→completed 發 receipt。
    第一次呼叫就帶 second_confirmation=拒(rc≠0)。駁回不需二次確認。

    已裁決的卡不重複裁決。反向/撤銷(落檔後反悔)流程候 E5.1 消費端語意
    一起定——本模組現**無**反向動作,不預先宣稱(一輪審8,具名殘餘)。
    回傳 (item, receipt);receipt 為 None 表示還在 pending_second。
    """
    if decision not in ("approve", "reject"):
        raise InboxError("decision 必須是 approve 或 reject")
    item, raw = _load_item_raw(item_id, base)
    if item is None:
        raise InboxError("查無此候審包:%s" % item_id)
    # 一輪審高5:卡內容與進匣指紋不符=卡被動過;receipt 全部欄位與此 hash
    # 同源自本次這一讀,裁決中途不再重讀檔案
    if item.get("content_hash") != content_hash_of(item.get("package") or {}):
        raise InboxError("卡內容與進匣指紋不符(item 疑遭竄改),"
                         "fail-closed 拒裁決:%s" % item_id)
    _check_risk_consistency(item["package"]["risk_level"],
                            item["package"]["candidates"])
    _guard_not_self_approval(item, approver)
    if item["status"] not in (STATUS_PENDING, STATUS_PENDING_SECOND):
        raise InboxError("此包已裁決過(%s),不重複裁決" % item["status"])
    if decision == "approve":
        _guard_distill_landing(item["package"]["candidates"])   # 規格 §3.3:landing 空不得核可

    ms = _now_ms(now_ms)
    key = load_hmac_key()
    decision_word = "approved" if decision == "approve" else "rejected"
    needs_second = effective_high(item["package"]) and decision == "approve"

    # 二輪審高5:裁決冪等閘——receipt 已落檔但 item 改寫失敗的重試,不得
    # 發第二張。同 package_uid+package_hash 已有有效 receipt=直接補寫 item
    # 收斂狀態,回既有 receipt。
    prior = [r for r in load_receipts_report(ms)["valid"]
             if r["package_binding"]["package_uid"]
             == item["package"]["package_uid"]
             and r["package_binding"]["package_hash"] == item["content_hash"]]
    if prior:
        receipt = prior[0]
        item["status"] = (STATUS_APPROVED if receipt["decision"] == "approved"
                          else STATUS_REJECTED)
        item["pending_confirmation"] = None
        item["resolution"] = {
            "decision": ("approve" if receipt["decision"] == "approved"
                         else "reject"),
            "approver": approver, "reason": reason,
            "event_id": None,             # 原事件已在帳本,本次僅收斂 item
            "receipt_uid": receipt["receipt_uid"],
            "resolved_at_ms": ms, "recovered": True,
        }
        _write_atomic(_item_path(base, item_id), item)
        return item, receipt

    if item["status"] == STATUS_PENDING_SECOND:
        held = item.get("pending_confirmation") or {}
        if decision == "approve":
            if held.get("approver") != approver:
                raise InboxError("二次確認必須由同一位確認者完成"
                                 "(首確認=%s)" % held.get("approver"))
            if second_confirmation != "yes":
                raise InboxError("high 風險卡需輸入 yes 全字完成二次確認")
            first = held.get("confirmation") or {}
            # 二輪審中1:不裸信卡內暫存確認——步序/HMAC/有效期重驗,
            # 竄改該欄=拒
            if first.get("step") != "primary":
                raise InboxError("暫存確認步序不對(%r),卡疑遭竄改"
                                 % first.get("step"))
            if not verify_confirmation(first, key, item_id, decision_word,
                                       approver, item["content_hash"]):
                raise InboxError("暫存首確認 HMAC 重驗不符,卡疑遭竄改,拒")
            try:
                first_exp = datetime.fromisoformat(first["expires_at"])
            except (KeyError, ValueError, TypeError):
                raise InboxError("暫存首確認 expires_at 不可解析,拒")
            if first_exp.timestamp() * 1000 <= ms:
                raise InboxError("暫存首確認已過期,請重新裁決")
            confirmations = [first,
                             _make_confirmation("high_risk_second", key, item,
                                                decision_word, approver, ms)]
            second_state = {
                "required": True, "status": "completed",
                "first_confirmation_uid":
                    held["confirmation"]["confirmation_uid"],
                "second_confirmation_uid":
                    confirmations[1]["confirmation_uid"],
            }
        else:
            # pending_second 反悔改駁回:重開單一確認(u 鍵緩衝的檔案化對映)
            confirmations = [_make_confirmation("primary", key, item,
                                                decision_word, approver, ms)]
            second_state = {"required": False, "status": "not_required",
                            "first_confirmation_uid": None,
                            "second_confirmation_uid": None}
    elif needs_second:
        # 一輪審高1b:二確必須是獨立的第二次呼叫——首呼叫夾帶確認參數
        # 等於把「再想一次」壓縮成一次,拒
        if second_confirmation is not None:
            raise InboxError("high 風險卡的二次確認必須是獨立的第二次呼叫;"
                             "同一次呼叫夾帶 second_confirmation 形同虛設,拒")
        conf = _make_confirmation("primary", key, item, decision_word,
                                  approver, ms)
        item["status"] = STATUS_PENDING_SECOND
        item["pending_confirmation"] = {"approver": approver,
                                        "confirmation": conf,
                                        "held_at_ms": ms}
        _write_atomic(_item_path(base, item_id), item)
        return item, None
    else:
        confirmations = [_make_confirmation("primary", key, item,
                                            decision_word, approver, ms)]
        second_state = {"required": False, "status": "not_required",
                        "first_confirmation_uid": None,
                        "second_confirmation_uid": None}

    receipt = _build_receipt(item, decision_word, approver, key, ms,
                             confirmations, second_state, _sha(raw))
    try:
        _get_validator().validate(receipt)
    except SchemaValidationError as err:
        raise InboxError("receipt 不合 v1 schema,拒發(零寫入):%s" % err)

    led = Ledger(ledger_path, node_id="decision-inbox")
    event = led.append({
        "kind": "decision.resolved",
        "actor": {"ai_id": approver, "sig": "human@0000/Human",
                  "kind": "human"},
        "body": {
            "summary": {
                "zh-TW": clip("候審包 %s 由 %s %s%s" % (
                    item_id, approver,
                    "核可" if decision == "approve" else "駁回",
                    ("(%s)" % reason) if reason else "")),
                "en": clip("review package %s %s by %s%s" % (
                    item_id, decision_word, approver,
                    (" (%s)" % reason) if reason else "")),
            },
            "subject": {"kind": "decision", "id": item_id},
        },
        "causation": {"causation_id": item["created_event_id"],
                      "correlation_id": "E5.3"},
    }, wall_ms=ms)

    _append_receipt(receipt)

    item["status"] = (STATUS_APPROVED if decision == "approve"
                      else STATUS_REJECTED)
    item["pending_confirmation"] = None
    item["resolution"] = {
        "decision": decision,
        "approver": approver,
        "reason": reason,
        "event_id": event["event_id"],
        "receipt_uid": receipt["receipt_uid"],
        "resolved_at_ms": event["hlc"]["wall_ms"],
    }
    _write_atomic(_item_path(base, item_id), item)
    return item, receipt


def _main(argv):
    import argparse
    p = argparse.ArgumentParser(prog="decision_inbox.py", add_help=True)
    p.add_argument("action",
                   choices=["submit", "list", "approve", "reject", "receipts"])
    p.add_argument("--package-id"); p.add_argument("--submitter-sig")
    p.add_argument("--submitter-ai-id"); p.add_argument("--dimension")
    p.add_argument("--summary"); p.add_argument("--engine")
    p.add_argument("--persona-uid"); p.add_argument("--base-persona-sha")
    p.add_argument("--expected-generation", type=int, default=0)
    p.add_argument("--risk-level", default="normal")
    p.add_argument("--signals", help="JSON 陣列,每筆一訊號物件")
    p.add_argument("--item-id"); p.add_argument("--approver")
    p.add_argument("--second-confirm", dest="second_confirm")
    p.add_argument("--reason"); p.add_argument("--status")
    p.add_argument("--inbox"); p.add_argument("--ledger")
    p.add_argument("--now-ms", type=int)
    args = p.parse_args(argv)

    ledger = args.ledger or default_ledger_path()   # ED.1 (b):只設狀態根也找得到
    try:
        if args.action == "submit":
            try:
                signals = json.loads(args.signals) if args.signals else None
            except ValueError as err:
                raise InboxError("signals 不是合法 JSON:%s" % err)
            pkg = make_package(args.package_id, args.submitter_sig,
                               args.submitter_ai_id, args.dimension,
                               args.summary, args.persona_uid,
                               args.base_persona_sha,
                               args.expected_generation, signals,
                               args.risk_level, args.engine)
            item, created = enqueue(pkg, ledger, args.inbox, args.now_ms)
            item["_created"] = created
            print(json.dumps(item, ensure_ascii=False))
            return 0
        if args.action == "list":
            report = list_items_report(args.inbox, args.status)
            print(json.dumps(report, ensure_ascii=False))
            if report["broken"]:
                print("損壞卡 %d 張(fail-visible):%s"
                      % (len(report["broken"]),
                         [b["file"] for b in report["broken"]]),
                      file=sys.stderr)
            return 0
        if args.action == "receipts":
            report = load_receipts_report(args.now_ms)
            print(json.dumps(report, ensure_ascii=False))
            return 0
        item, receipt = resolve(
            args.item_id, "approve" if args.action == "approve" else "reject",
            args.approver, ledger, args.inbox, args.reason, args.now_ms,
            args.second_confirm)
        print(json.dumps({"item": item, "receipt": receipt},
                         ensure_ascii=False))
        return 0
    except (InboxError, ValueError) as err:
        print(str(err), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
