#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球 dm:核心與轉接程式之間的檔案契約(開發文件 v0.4 第 22.2 節,PR-3)。

核心(dm-ctl.sh、dm_queue.py:只用標準庫、不連網)與轉接程式(選用外掛,有 nostr-sdk)
**只靠四個資料夾溝通**,互不 import 對方的連網程式。這支只用標準庫,兩邊都可以 import 它,
格式規則因此只有這一份。

    outbox/<msgid>.json      核心 → 外掛  要寄的信(op=send)或 --dry-run 的解析請求(op=resolve)
    sending/<msgid>.json     外掛領件    外掛用 claim() 從 outbox 原子搬過來才開始寄(見下面「撤回」)
    receipts/<msgid>.jsonl   外掛 → 核心  寄件端回條,逐行附加
    inbox/<msgid>.json       外掛 → 核心  已解包、已驗證、已分類的來信;核心的匯入器入佇列後刪檔
    delivery/<msgid>.jsonl   核心 → 外掛  佇列狀態變化;外掛照它送回條給對方

規則:
  - 資料夾 0700、檔案 0600;單一檔用 atomic_json.write_json(暫存檔 + rename + fsync),.jsonl 加鎖逐行附加。
  - 每筆都帶 "v": 1;看不懂的版本或壞掉的檔 ⇒ 不處理、搬到 bad/。
  - msgid 是 32 碼小寫 hex,也是檔名:不合形一律拒,檔名拼不出 ../。
  - ★ 來信的身分由**核心**決定,不收外掛給的:inbox 帶 sender_kind 欄 ⇒ 整筆拒收(外掛寫 human 也進不去);
    未驗證的一律當陌生(待審、優先級降一般)。對照開發文件第 8.1 節第 4–6 步、第 17.2 節。
  - 私鑰不寫進任何一個資料夾;這支也不碰金鑰。

寄件端(dm-ctl 只叫一次 `dm_global_files.py send-and-wait`,第 14 節:不在 bash 迴圈裡反覆起 Python):
  - 轉接程式心跳檔 heartbeat.json 超過 HEARTBEAT_MAX_AGE 秒沒更新 ⇒ rc 2「全球對談沒開」,**一個字都不寫**。
  - 寫待寄檔 → 同一個行程裡等回條 → 照開發文件第 7.1 節的表回 rc。
  - ★ 撤回:逾時還沒任何回條時,刪 outbox 那份。刪得掉 = 外掛還沒領(claim 是原子 rename)⇒ 保證沒送出,rc 2;
    刪不掉 = 外掛已領走、可能已寄 ⇒ rc 4(勿重送)。「沒送出」只在證得出時才說。
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atomic_json import AtomicWriteError, MAX_RECORD_BYTES, write_json  # noqa: E402

VERSION = 1
BOXES = ("outbox", "sending", "receipts", "inbox", "delivery")
DIR_MODE = 0o700
KEEP_DAYS = 7
HEARTBEAT_MAX_AGE = 30     # 秒;轉接程式每 10 秒寫一次
WAIT_DEFAULT = 20          # 秒;第 7.1 節 AIB_DM_GLOBAL_WAIT
REPLY_CODE_LEN = 5
CONTENT_MAX = 8000          # 第 6.3 節
NAME_MAX = 32               # 第 5.8 節

MSGID_RE = re.compile(r"^[0-9a-f]{32}$")
NPUB_RE = r"npub1[02-9ac-hj-np-z]{58}"
NPUB_FULL_RE = re.compile(r"^%s$" % NPUB_RE)
_ADDR_RE = re.compile(r"^(?:(?P<owner>%s)/(?P<cabin>[0-9a-z]{1,4})/(?P<seat>[0-9a-z]{4,8})|(?P<npub>%s))$"
                      % (NPUB_RE, NPUB_RE))
_NPUB_ONLY = re.compile(r"^%s$" % NPUB_RE)
_CABIN = re.compile(r"^[0-9a-z]{1,4}$")
_SEAT = re.compile(r"^[0-9a-z]{4,8}$")

PRIORITIES = ("urgent", "important", "normal", "reminder")   # 與 dm_queue.PRIORITIES 同序
RECEIPT_STAGES = ("resolved", "relayed", "queued", "delivered", "held", "refused", "expired", "read", "failed",
                  "unconfirmed",   # 10-18:交出 24 小時還沒有對方回條(寄件人要知道可能沒收到)
                  "still_waiting")  # 10-21 回條語意:給對方本人的信還在待審(帶 days);不是終局,之後還會有 read / expired
RECEIPT_FINAL = ("delivered", "held", "refused", "expired", "failed")
DELIVERY_STATES = ("queued", "delivered", "held", "refused", "expired", "read", "still_waiting")
# 10-21 回條語意:held 的原因碼(aib-reason)。寄件端只拿來顯示(@ee4f8:不准拿來判放行 / 權限 / 過期、未知值當沒帶、不原樣印進頁面)
HELD_REASONS = ("human_reply", "stranger")
WAIT_DAYS_MAX = 30          # still_waiting 的 days 上限(收件端留 7 天;多給一點餘裕,超過的當壞值)
INBOX_CLASSES = ("own", "paired", "friend", "stranger")
# 國別(Andy 10-15 18:1x:「全球要能判斷國別」;裁:抬頭加國別、申報 + 時區佐證、用於顯示 / 預設回信語言 / 法規)
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")                      # ISO 3166-1 alpha-2,主人在名冊申報
TZ_RE = re.compile(r"^[A-Za-z0-9_+\-]+(?:/[A-Za-z0-9_+\-]+){0,2}$")   # IANA 時區名,寄件機轉接程式量的
COUNTRY_STATUSES = ("match", "mismatch", "unknown")
LANG_BY_COUNTRY = {"TW": "zh-TW", "HK": "zh-TW", "MO": "zh-TW", "CN": "zh-TW", "JP": "ja", "KR": "ko"}
# 名稱裡不准出現的字:會被看成抬頭的分隔、位址或驗證標記(第 5.8 節)
_NAME_BANNED = (">>", "/", "@", "(", ")", "\uff08", "\uff09")   # 含全形括號


class ContractError(ValueError):
    """不合契約。reason 是給人看的一句話。"""


def default_root(env=None):
    env = os.environ if env is None else env
    explicit = env.get("AIB_CHAT_BRIDGE_DIR")
    if explicit:
        return Path(explicit)
    return Path(env.get("HOME") or os.path.expanduser("~")) / ".ez2-aib" / "state" / "chat-bridge"


def ensure_dirs(root):
    root = Path(root)
    for sub in BOXES + ("bad",):
        d = root / sub
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, DIR_MODE)
    os.chmod(root, DIR_MODE)
    return root


def new_msgid():
    return uuid.uuid4().hex


# 收件端認得的 aib-type。新版加新型別時,舊版收件端不認得 ⇒ 以前會當一般信送進 AI 框
# (10-17 Pro 轉接程式還跑 0.1.16 以前的碼,Mini 的島快照 aib-type=space 進了 @d07f9 待審框)。
# 一般信:aib-type=dm,或根本不是 aib 寄件人(沒有 aib-type)。receipt 在進 on_dm 之前就分走了,不在這裡。
INBOUND_TYPES = ("dm", "friend-request", "presence", "card", "space", "offer", "visit")   # visit:參觀請求 / 開放(chat_visit)


def inbound_kind(tags):
    """rumor 標籤 ⇒ 收件分流:INBOUND_TYPES 之一,或 "unknown"(帶了這版不認得的型別 ⇒ 不投遞)。"""
    typ = tags.get("aib-type")
    if typ is None:
        return "dm"
    return typ if typ in INBOUND_TYPES else "unknown"


def _path(root, box, msgid, ext):
    if box not in BOXES:
        raise ContractError("沒有這個資料夾:%r" % (box,))
    if not isinstance(msgid, str) or not MSGID_RE.match(msgid):
        raise ContractError("msgid 要是 32 碼小寫 hex")
    return Path(root) / box / (msgid + ext)


# ── 欄位檢查 ──

def clean_display_name(name):
    """顯示名稱清理(第 5.8 節)。合格回清過的字串;不合格回 None(呼叫端退回人格名)。"""
    if not isinstance(name, str):
        return None
    s = unicodedata.normalize("NFC", name)
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] != "C").strip()
    s = re.sub(r"\s+", " ", s)
    if not s or len(s) > NAME_MAX or any(b in s for b in _NAME_BANNED):
        return None
    return s


def _need(rec, key, ok, why):
    if key not in rec or not ok(rec[key]):
        raise ContractError("%s:%s" % (key, why))


def _opt(rec, key, ok, why):
    if rec.get(key) is not None and not ok(rec[key]):
        raise ContractError("%s:%s" % (key, why))


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _is_str(v):
    return isinstance(v, str)


def _content_ok(v):
    return isinstance(v, str) and 0 < len(v) <= CONTENT_MAX


def _base(rec):
    if not isinstance(rec, dict):
        raise ContractError("不是物件")
    if rec.get("v") != VERSION:
        raise ContractError("v:看不懂的版本 %r" % (rec.get("v"),))
    _need(rec, "msgid", lambda v: isinstance(v, str) and bool(MSGID_RE.match(v)), "要是 32 碼小寫 hex")


def parse_address(addr):
    """全球位址(第 1 期,v0.4):`<主人 npub>/<艙>/<席>` 或 `npub1…`(直接指定席金鑰)。不合回 None。"""
    m = _ADDR_RE.match(addr) if isinstance(addr, str) else None
    if not m:
        return None
    if m.group("npub"):
        return {"seat_npub": m.group("npub")}
    return {"owner": m.group("owner"), "cabin": m.group("cabin"), "seat": m.group("seat")}


AUTHORS = ("human", "ai_draft", "ai_auto")


def check_outbox(rec):
    _base(rec)
    _need(rec, "created_at", _is_int, "要是非負整數(UTC 秒)")
    _need(rec, "op", lambda v: v in ("send", "resolve", "request"), "只能 send / resolve / request")
    frm = rec.get("from")
    if not isinstance(frm, dict):
        raise ContractError("from:要是物件")
    _need(frm, "cabin", lambda v: isinstance(v, str) and bool(_CABIN.match(v)), "艙位碼不合")
    _need(frm, "seat", lambda v: isinstance(v, str) and bool(_SEAT.match(v)), "席碼不合")
    _need(frm, "attested", lambda v: isinstance(v, bool), "要是 true/false")
    _opt(frm, "persona_uid", _is_str, "要是字串")
    _need(rec, "to", lambda v: parse_address(v) is not None, "不是全球位址(<主人 npub>/<艙>/<席> 或 npub1…)")
    if rec["op"] == "send":
        _need(rec, "content", _content_ok, "要有內容、最多 %d 字" % CONTENT_MAX)
    if rec["op"] == "request":   # 好友申請(第 29.4 節):一句話,可空
        _opt(rec, "content", lambda v: isinstance(v, str) and len(v) <= REQUEST_NOTE_MAX, "最多 %d 字" % REQUEST_NOTE_MAX)
    _opt(rec, "priority", lambda v: v in PRIORITIES, "只能 %s" % "/".join(PRIORITIES))
    _opt(rec, "author", lambda v: v in AUTHORS, "只能 %s" % "/".join(AUTHORS))   # 第 35 節第 2 點:本人 / AI 擬稿 / AI 自動
    _opt(rec, "expires", _is_int, "要是非負整數(UTC 秒)")
    _opt(rec, "reply_to", lambda v: isinstance(v, str) and bool(MSGID_RE.match(v)), "要是 msgid")
    for k in ("thread", "kind", "lang"):
        _opt(rec, k, _is_str, "要是字串")
    _opt(rec, "echo", lambda v: isinstance(v, bool), "要是 true/false")   # 10-24 回音小幫手回的那一句:不加好友、不補寄
    return rec


def check_receipt(rec):
    if not isinstance(rec, dict) or rec.get("v") != VERSION:
        raise ContractError("回條版本不對")
    _need(rec, "at", _is_int, "要是非負整數(UTC 秒)")
    _need(rec, "stage", lambda v: v in RECEIPT_STAGES, "不認得的階段")
    _opt(rec, "pos", lambda v: _is_int(v) and v > 0, "要是正整數")
    _opt(rec, "detail", _is_str, "要是字串")
    _opt(rec, "days", lambda v: _is_int(v) and 1 <= v <= WAIT_DAYS_MAX, "要是 1–30 的整數")
    _opt(rec, "by", lambda v: v == "human", "只能是 human")
    return rec


def check_delivery(rec):
    if not isinstance(rec, dict) or rec.get("v") != VERSION:
        raise ContractError("投遞狀態版本不對")
    _need(rec, "at", _is_int, "要是非負整數(UTC 秒)")
    _need(rec, "state", lambda v: v in DELIVERY_STATES, "不認得的狀態")
    _opt(rec, "pos", lambda v: _is_int(v) and v > 0, "要是正整數")
    _opt(rec, "reason", lambda v: v in (REFUSE_TEXT if rec.get("state") == "refused" else HELD_REASONS if rec.get("state") == "held" else ()),
         "不認得的原因碼")
    _opt(rec, "days", lambda v: _is_int(v) and 1 <= v <= WAIT_DAYS_MAX and rec.get("state") == "still_waiting", "days 只給 still_waiting、1–30")
    _opt(rec, "by", lambda v: v == "human" and rec.get("state") == "read", "by 只給 read、只能是 human")
    return rec


def check_inbox(rec):
    _base(rec)
    if "sender_kind" in rec:   # ★ 身分只由核心決定(檔頭);外掛自帶 ⇒ 整筆拒
        raise ContractError("sender_kind:外掛不能自帶寄件人種類")
    _need(rec, "received_at", _is_int, "要是非負整數(UTC 秒)")
    _need(rec, "to_cabin", lambda v: isinstance(v, str) and bool(_CABIN.match(v)), "艙位碼不合")
    _need(rec, "to_seat", lambda v: isinstance(v, str) and bool(_SEAT.match(v)), "席碼不合")
    _need(rec, "from_npub", lambda v: isinstance(v, str) and bool(_NPUB_ONLY.match(v)), "要是 npub1…")
    _opt(rec, "from_addr", lambda v: parse_address(v) is not None, "不是全球位址")
    _need(rec, "verified", lambda v: isinstance(v, bool), "要是 true/false")
    _need(rec, "class", lambda v: v in INBOX_CLASSES, "只能 %s" % "/".join(INBOX_CLASSES))
    _need(rec, "content", _content_ok, "要有內容、最多 %d 字" % CONTENT_MAX)
    _opt(rec, "priority", lambda v: v in PRIORITIES, "只能 %s" % "/".join(PRIORITIES))
    _opt(rec, "expires", _is_int, "要是非負整數(UTC 秒)")
    _opt(rec, "reply_to", lambda v: isinstance(v, str) and bool(MSGID_RE.match(v)), "要是 msgid")
    for k in ("display_name", "persona_name", "thread", "kind", "lang"):
        _opt(rec, k, _is_str, "要是字串")
    # 同名(第 5.8 節):轉接程式比對好友名冊判;只會多加警告、不會提高信任,所以讓外掛判
    _opt(rec, "name_collision", lambda v: isinstance(v, bool), "要是 true/false")
    _opt(rec, "country", lambda v: isinstance(v, str) and bool(COUNTRY_RE.match(v)), "要是兩碼大寫國碼")
    _opt(rec, "tz", lambda v: isinstance(v, str) and len(v) <= 64 and bool(TZ_RE.match(v)), "要是 IANA 時區名")
    _opt(rec, "author", lambda v: v in AUTHORS, "只能 %s" % "/".join(AUTHORS))   # 寄件方自報誰寫的(aib-author)
    return rec


# ── 國別:申報 + 時區佐證 ──

_TZ_TAB_CACHE = {}


def _zone_tab_paths():
    import zoneinfo
    return [os.path.join(d, "zone.tab") for d in zoneinfo.TZPATH]


def tz_country(tz, tab_paths=None):
    """IANA 時區名 ⇒ 兩碼國碼(查系統 zone.tab,不連網);查不到(沒有 zone.tab、UTC、別名)回 None。"""
    if not isinstance(tz, str) or not TZ_RE.match(tz):
        return None
    paths = tuple(tab_paths) if tab_paths is not None else tuple(_zone_tab_paths())
    if paths not in _TZ_TAB_CACHE:
        table = {}
        for p in paths:
            try:
                with open(p, encoding="utf-8") as fh:
                    for line in fh:
                        if line.startswith("#"):
                            continue
                        cols = line.rstrip("\n").split("\t")
                        if len(cols) >= 3 and COUNTRY_RE.match(cols[0]):
                            table.setdefault(cols[2], cols[0])
            except OSError:
                continue
            if table:
                break
        _TZ_TAB_CACHE[paths] = table
    return _TZ_TAB_CACHE[paths].get(tz)


def country_check(declared, tz, tab_paths=None):
    """(狀態, 時區推得的國碼)。狀態:match 申報與時區一致 / mismatch 不一致 / unknown 沒申報或時區量不到。"""
    tzc = tz_country(tz, tab_paths)
    if not declared or tzc is None:
        return "unknown", tzc
    return ("match" if tzc == declared else "mismatch"), tzc


def default_lang(country):
    """對方沒帶語言時的預設回信語言(aib 四語:zh-TW / en / ja / ko;其他國家用 en)。"""
    return LANG_BY_COUNTRY.get(country or "", "en")


def queue_fields(rec, tab_paths=None):
    """inbox 一筆 ⇒ 入佇列要用的身分與狀態欄位(PR-8 的匯入器照這個寫佇列)。

    - sender_kind:主人手機(已驗證)⇒ owner_paired;其他一律 external_seat。**永遠不會是 human**。
    - 未驗證 ⇒ 不論外掛分到哪一類,一律當陌生。
    - 陌生 ⇒ hold_human(待審)、優先級降為 normal。
    - shown_name:顯示名稱 → 人格名 → npub 前 12 碼(第 5.8 節);不合格的名稱直接跳過。
    - same_name:外掛判的同名旗標;抬頭加「同名」用(PR-2)。
    - country / country_status / tz_country:申報國別與寄件機時區佐證(country_check);
      lang:對方帶的語言,沒帶就照國別給預設回信語言。
    - author:寄件方自報誰寫的(human / ai_draft / ai_auto,第 35 節第 2 點);沒帶 = None。只進外框說明行。
    """
    check_inbox(rec)
    cls = rec["class"] if rec["verified"] else "stranger"
    prio = rec.get("priority") or "normal"
    if cls == "stranger":
        prio = "normal"
    cstat, tzc = country_check(rec.get("country"), rec.get("tz"), tab_paths)
    shown = clean_display_name(rec.get("display_name")) or clean_display_name(rec.get("persona_name")) \
        or rec["from_npub"][:12]
    return {
        "sender_kind": "owner_paired" if cls == "paired" else "external_seat",
        "sender_origin": "nostr:" + rec["from_npub"],
        "sender_addr": rec.get("from_addr") or rec["from_npub"],
        "shown_name": shown,
        "verified": rec["verified"],
        "same_name": bool(rec.get("name_collision")),
        "country": rec.get("country"),
        "country_status": cstat,
        "tz_country": tzc,
        "lang": rec.get("lang") or default_lang(rec.get("country")),
        "author": rec.get("author"),
        "class": cls,
        "status": "hold_human" if cls == "stranger" else "queued",
        "priority": prio,
    }


# ── 讀寫 ──

def _move_bad(root, path):
    try:
        bad = Path(root) / "bad"
        bad.mkdir(parents=True, exist_ok=True)
        os.replace(str(path), str(bad / ("%d-%s" % (int(time.time()), path.name))))
    except OSError:
        pass


def write_outbox(root, rec):
    check_outbox(rec)
    p = _path(root, "outbox", rec["msgid"], ".json")
    if p.exists():
        raise ContractError("msgid 重複:%s" % rec["msgid"])
    write_json(str(p), rec)
    return p


def write_inbox(root, rec):
    check_inbox(rec)
    p = _path(root, "inbox", rec["msgid"], ".json")
    if p.exists():
        raise ContractError("msgid 重複:%s" % rec["msgid"])
    write_json(str(p), rec)
    return p


def _append(root, box, msgid, rec):
    p = _path(root, box, msgid, ".jsonl")
    line = (json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    if len(line) > MAX_RECORD_BYTES:
        raise AtomicWriteError("記錄 %d bytes 超過上限 %d" % (len(line), MAX_RECORD_BYTES))
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)
    return p


def append_receipt(root, msgid, rec):
    return _append(root, "receipts", msgid, check_receipt(rec))


def append_delivery(root, msgid, rec):
    return _append(root, "delivery", msgid, check_delivery(rec))


def _read_lines(root, box, msgid, check):
    p = _path(root, box, msgid, ".jsonl")
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return []
    out = []
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            out.append(check(json.loads(line.decode("utf-8"))))
        except (ValueError, ContractError):
            continue   # 壞的一行略過(寫到一半的不會有換行,也落在這裡)
    return out


def read_receipts(root, msgid):
    return _read_lines(root, "receipts", msgid, check_receipt)


def read_delivery(root, msgid):
    return _read_lines(root, "delivery", msgid, check_delivery)


def final_receipt(receipts):
    """回條裡第一個終局階段;沒有回 None(dm-ctl 照第 7.1 節的表決定 rc)。"""
    for r in receipts:
        if r["stage"] in RECEIPT_FINAL:
            return r
    return None


def pending(root, box):
    """outbox / inbox 裡待處理的檔,依 msgid 排(暫存檔與非 .json 略過);壞檔與看不懂的搬到 bad/。

    回 [(msgid, rec, path)]。處理完由呼叫端刪檔(outbox:寄出後;inbox:入佇列後)。
    """
    if box not in ("outbox", "inbox"):
        raise ContractError("只有 outbox / inbox 有待處理檔")
    check = check_outbox if box == "outbox" else check_inbox
    d = Path(root) / box
    out = []
    try:
        names = sorted(os.listdir(str(d)))
    except FileNotFoundError:
        return out
    for name in names:
        if not name.endswith(".json") or name.startswith("."):
            continue
        p = d / name
        try:
            rec = check(json.loads(p.read_text(encoding="utf-8")))
            if rec["msgid"] + ".json" != name:
                raise ContractError("檔名與 msgid 不符")
        except (OSError, ValueError, ContractError):
            _move_bad(root, p)
            continue
        out.append((rec["msgid"], rec, p))
    return out


def sweep(root, now=None, keep_days=KEEP_DAYS):
    """刪掉 receipts / delivery / bad 裡超過保留天數的檔;回刪了幾個。outbox / inbox 不在這裡清(待處理的不能丟)。"""
    now = time.time() if now is None else now
    cut = now - keep_days * 86400
    n = 0
    for sub in ("receipts", "delivery", "bad"):
        d = Path(root) / sub
        if not d.is_dir():
            continue
        for p in d.iterdir():
            try:
                if p.is_file() and p.stat().st_mtime < cut:
                    p.unlink()
                    n += 1
            except OSError:
                pass
    return n


# ── 轉接程式心跳、領件、撤回 ──

def write_heartbeat(root, now=None, pid=None):
    """轉接程式定時呼叫。"""
    rec = {"v": VERSION, "at": int(time.time() if now is None else now), "pid": os.getpid() if pid is None else pid}
    write_json(str(Path(root) / "heartbeat.json"), rec)


def bridge_alive(root, now=None, max_age=HEARTBEAT_MAX_AGE):
    """心跳檔在、版本對、而且不舊 ⇒ True。讀不到、壞掉、太舊、時間在未來超過 max_age(時鐘亂了)⇒ False。"""
    try:
        rec = json.loads((Path(root) / "heartbeat.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(rec, dict) or rec.get("v") != VERSION or not _is_int(rec.get("at")):
        return False
    now = time.time() if now is None else now
    return abs(now - rec["at"]) <= max_age


def claim(root, msgid):
    """外掛要寄之前呼叫:outbox → sending 原子搬。搬到了回 sending 的路徑;已被撤回或別人領走回 None。"""
    src = _path(root, "outbox", msgid, ".json")
    dst = _path(root, "sending", msgid, ".json")
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(str(src), str(dst))
    except FileNotFoundError:
        return None
    return dst


def withdraw(root, msgid):
    """寄件端逾時時呼叫:outbox 那份還在就刪掉,回 True(保證外掛沒領、沒寄);已被領走回 False。"""
    try:
        _path(root, "outbox", msgid, ".json").unlink()
        return True
    except FileNotFoundError:
        return False


# ── 回信短碼(第 9 節)──

_CODE_ALPHA = "abcdefghjkmnpqrstuvwxyz23456789"   # 去掉容易看錯的 i l o 0 1


def reply_code(msgid, taken=()):
    """msgid ⇒ 5 碼短碼;跟 taken 撞了就換下一段雜湊。同一個 msgid、同一個 taken 永遠得到同一個碼。"""
    if not isinstance(msgid, str) or not MSGID_RE.match(msgid):
        raise ContractError("msgid 要是 32 碼小寫 hex")
    taken = set(taken)
    n = 0
    while True:
        h = int(hashlib.sha256(("%s:%d" % (msgid, n)).encode("ascii")).hexdigest(), 16)
        code = ""
        for _ in range(REPLY_CODE_LEN):
            h, r = divmod(h, len(_CODE_ALPHA))
            code += _CODE_ALPHA[r]
        if code not in taken:
            return code
        n += 1


# ── 寄送與等待(第 7.1 節的表)──

# 對方拒收的原因碼(回條 detail;轉接程式從 aib-reason 標籤讀來)⇒ 寄件端看的一句話
REFUSE_TEXT = {
    "not_friend": "對方只收好友的信:請對方先把你的主人號碼加為好友(aib chat friend add <你的主人 npub>)",
    "unverified": "對方驗不到寄件席:這一席要先列進你的名冊(aib chat publish <席碼>)",
    "rejected": "對方拒絕了好友申請",
}
# no_seat 不翻成句子:艦長室(captain-room #17)認 detail == "no_seat" 自己講人話;翻了它就認不得(0.1.23 整合時 test-chat-send S4 抓到)
REQUEST_NOTE_MAX = 140      # 好友申請附的一句話(第 29.4 節)
# 第二波 #6(10-21):回「對方本人寫的」那封 ⇒ 對方那邊建立時就待審(human_reply),回條只回 held、detail 空,
#   寄件端只看到 stage=held 不知道對方要去哪看。補一句人話 hint;**detail 代碼不動**(艦長室 / dm-ctl 照 detail 判)。
#   判法 = 我佇列裡那封原信的 global.author == human,跟對方那邊判 sent_author 用的是同一個 aib-author 標籤。
# 10-24:對方名冊同一席列了兩次(對方換過人格、兩把都還在)⇒ 這一席整席不收;why 照回 no_seat,另給這句
DUPLICATE_SEAT_HINT = "對方的名冊把這一席列了兩次(對方換過人格、舊鑰還沒停用),暫時寄不進去;請對方升級 aib 後重開轉接程式"
HELD_HUMAN_HINT = "對方本人會在艦長室收件匣看到這封(不進對方 AI 的框);等本人看了才會有回音"


def reply_to_human(seat, msgid, env=None):
    """我(收件席)正在回的那封原信是不是對方本人寫的(佇列裡 global.author == human)。找不到那封 ⇒ False。"""
    import dm_queue
    item = dm_queue.by_msgid(seat, msgid, env)
    return bool(item) and (item.get("global") or {}).get("author") == "human"


def with_hint(res, to_human):
    """寄件結果補 hint:只在 stage=held 而且回的是本人寫的信;其他情況不加鍵。"""
    if to_human and isinstance(res, dict) and res.get("stage") == "held":
        res["hint"] = HELD_HUMAN_HINT
    return res


def outcome(receipts, op="send", verify=False):
    """回條 ⇒ (rc, stage, pos, detail) 或 None(還要再等)。

    send:delivered 0;queued 0(--verify 時不算,繼續等);held 3;refused / expired / failed 2。
    resolve(--dry-run):resolved 0;refused / failed 2。
    """
    for r in receipts:
        st = r["stage"]
        if op == "resolve":
            if st == "resolved":
                return 0, "resolved", None, r.get("detail", "")
            if st in ("refused", "failed"):
                return 2, st, None, r.get("detail", "")
            continue
        if st == "delivered":
            return 0, "delivered", None, r.get("detail", "")
        if st == "queued" and not verify:
            return 0, "queued", r.get("pos"), r.get("detail", "")
        if st == "held":
            return 3, "held", None, r.get("detail", "")
        if st in ("refused", "expired", "failed"):
            d = r.get("detail", "")
            return 2, st, None, REFUSE_TEXT.get(d, d) if st == "refused" else d
    return None


def send_and_wait(root, rec, wait=WAIT_DEFAULT, verify=False, poll=0.1, clock=time.time, sleep=time.sleep):
    """寫待寄檔並等回條。回 {rc, stage, pos, detail, msgid}。rc 2 的情況保證外掛沒有寄出這一則。"""
    msgid = rec.get("msgid") if isinstance(rec, dict) else None
    try:
        check_outbox(rec)
    except ContractError as e:
        return {"rc": 2, "stage": "abort", "pos": None, "detail": str(e), "msgid": msgid}
    if not bridge_alive(root, now=clock()):
        return {"rc": 2, "stage": "bridge-off", "pos": None, "detail": "線上對話沒開(轉接程式沒在跑)", "msgid": msgid}
    write_outbox(root, rec)
    deadline = clock() + max(0, wait)
    op = rec["op"]
    while True:
        got = read_receipts(root, msgid)
        res = outcome(got, op=op, verify=verify)
        if res is not None:
            rc, stage, pos, detail = res
            return _with_peer(root, rec, {"rc": rc, "stage": stage, "pos": pos, "detail": detail, "msgid": msgid}, clock())
        if clock() >= deadline:
            break
        sleep(poll)
    if withdraw(root, msgid):
        return {"rc": 2, "stage": "timeout-withdrawn", "pos": None,
                "detail": "%d 秒內轉接程式沒領件,已撤回,一個字都沒送" % wait, "msgid": msgid}
    relayed = any(r["stage"] == "relayed" for r in got)
    return _with_peer(root, rec, {"rc": 4, "stage": "relayed" if relayed else "claimed", "pos": None,
                                  "detail": "已交給轉接程式,還沒收到對方回條;勿重送", "msgid": msgid}, clock())


def sent_author(root, msgid):
    """寄出帳那封記的 author(human / ai_draft / ai_auto);沒這封、舊帳沒記、msgid 不像 ⇒ None。"""
    if not isinstance(msgid, str) or not MSGID_RE.match(msgid):
        return None
    try:
        a = json.loads((Path(root) / "sent" / (msgid + ".json")).read_text(encoding="utf-8")).get("author")
    except (OSError, ValueError, AttributeError):
        return None
    return a if a in AUTHORS else None


def mark_unconfirmed(root, now=None, after=86400):
    """10-18:交出(relayed)或對方只回到 queued、超過 after 還沒 delivered / read / 拒收 ⇒ 補一張 unconfirmed 回條
    (寄件人與艦長室看得到「可能沒收到」;Mac Pro 13 換艙後寄到舊席的信就是這樣一直排著)。回補了幾張。"""
    now = time.time() if now is None else now
    done = 0
    for p in sorted((Path(root) / "sent").glob("*.json"))[-2000:]:
        try:
            at = int(json.loads(p.read_text(encoding="utf-8")).get("at") or 0)
        except (OSError, ValueError, AttributeError, TypeError):
            continue
        if not at or now - at < after:
            continue
        stages = {r.get("stage") for r in read_receipts(root, p.stem)}
        if stages & {"delivered", "read", "refused", "held", "expired", "failed", "unconfirmed"} or not stages & {"relayed", "queued"}:
            continue
        append_receipt(root, p.stem, {"v": 1, "at": int(now), "stage": "unconfirmed",
                                         "detail": "queued" if "queued" in stages else "relayed"})
        done += 1
    return done

# resend/<msgid>.json(10-24 Andy「像市面上的通訊 App 一樣」):信交給中繼站(relayed)之後,對方那台還沒收到
# (沒有 queued / delivered / read / 拒收…)⇒ 留一份;對方的在線狀態一回來就補寄同一個 msgid(收件端照 msgid 去重,
# 收過的不會再進佇列)。只補一般信;最多 RESEND_MAX 次、同一則間隔 RESEND_GAP_S;留 RESEND_KEEP_S 就刪。
# 內文只存在本機這個資料夾(0600),跟 inbox / outbox 同一級。
RESEND_GAP_S = 600
RESEND_MAX = 3
RESEND_KEEP_S = 7 * 86400
RESEND_DONE = {"queued", "delivered", "read", "refused", "held", "expired", "failed"}


def keep_for_resend(root, rec, owner, now=None):
    if rec.get("op", "send") != "send" or not NPUB_FULL_RE.match(owner or "") or not MSGID_RE.match(rec.get("msgid") or ""):
        return None
    now = int(time.time() if now is None else now)
    d = Path(root) / "resend"
    d.mkdir(mode=0o700, exist_ok=True)
    p = d / (rec["msgid"] + ".json")
    write_json(str(p), {"v": 1, "rec": rec, "owner": owner, "at": now, "n": 0, "last": now})
    os.chmod(str(p), 0o600)
    return p


def resend_due(root, owner, now=None):
    """對方(owner)剛送在線狀態來 ⇒ 把還沒到的信放回 outbox。順手清掉已到 / 太舊的。回放回去的 msgid。"""
    now = int(time.time() if now is None else now)
    out = []
    for p in sorted((Path(root) / "resend").glob("*.json")):
        try:
            cur = json.loads(p.read_text(encoding="utf-8"))
            msgid = cur["rec"]["msgid"]
        except (OSError, ValueError, KeyError, TypeError):
            try:
                p.unlink()
            except OSError:
                pass
            continue
        stages = {r.get("stage") for r in read_receipts(root, msgid)}
        if stages & RESEND_DONE or now - int(cur.get("at") or 0) > RESEND_KEEP_S or int(cur.get("n") or 0) >= RESEND_MAX:
            p.unlink()
            continue
        if cur.get("owner") != owner or now - int(cur.get("last") or 0) < RESEND_GAP_S:
            continue
        if any((Path(root) / box / (msgid + ".json")).exists() for box in ("outbox", "sending")):
            continue
        try:
            write_outbox(root, cur["rec"])
        except ContractError:
            p.unlink()
            continue
        cur["n"], cur["last"] = int(cur.get("n") or 0) + 1, now
        write_json(str(p), cur)
        os.chmod(str(p), 0o600)
        out.append(msgid)
    return out


def _with_peer(root, rec, res, now):
    """10-18 Andy「要能判斷該電腦休眠」:寄件結果帶對方那台的狀態;還沒送到而對方在睡 / 離線 ⇒ detail 補一句。"""
    owner = (parse_address((rec or {}).get("to") or "") or {}).get("owner")
    if not owner or rec.get("op") == "resolve":
        return res
    try:
        import chat_power as CP
        st = CP.owner_state([c for c in read_world(root).values() if c.get("owner") == owner], now)
    except Exception:   # noqa: BLE001  看不到狀態就不補,不擋寄件
        return res
    res["peer"] = st
    if res["stage"] in ("relayed", "claimed", "queued") and st["state"] in ("asleep", "offline") and st.get("since"):
        when = time.strftime("%m-%d %H:%M", time.localtime(st["since"]))
        res["detail"] = (res["detail"] + ";" if res["detail"] else "") + (
            "對方那台電腦在睡(%s 起沒動靜),信在中繼站等,醒來才收" % when if st["state"] == "asleep"
            else "對方那台電腦離線(%s 起沒動靜,它設定成不睡:可能斷網、關機或轉接程式停了)" % when)
    return res


# 寄件人標籤(seat_address 白名單的格式):`@五碼[(暱稱)]` = 行程鏈量到的;`seat_claimed:五碼(自稱)` = 自稱(codex 等)
_LABEL_SEAT_RE = re.compile(r"^@([0-9a-z]{4,8})(?:\(|$)")
_LABEL_CLAIMED_RE = re.compile(r"^seat_claimed:([0-9a-z]{4,8})[(\uff08]")


# ── 好友申請與聯絡人快照(第 29.4、29.6 節)──
# requests.json:別人寄來、等主人同意的申請 {"v":1, "requests":[{owner, display_name, country, from_addr, note, at, msgid}]}
#   工人寫入、核心(aib chat request accept|reject)拿掉 ⇒ 兩邊都改 ⇒ 一律在 requests.lock 底下讀改寫。
# peers.json:查到 / 往來過的對方主人快照 {"v":1, "peers":{owner: {display_name, country, seats[], roster_at, seen_at,
#   last_in, last_out, n_in, n_out}}};只有工人寫。都不存內文。
PEERS_MAX = 500
PEERS_KEEP_S = 180 * 86400
REQUESTS_MAX = 200


def _locked_update(root, name, fn, default):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    p = root / name
    lk = os.open(str(root / (name + ".lock")), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            cur = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(cur, dict) or cur.get("v") != VERSION:
                cur = default()
        except (OSError, ValueError):
            cur = default()
        out = fn(cur)
        write_json(str(p), cur)
        os.chmod(str(p), 0o600)
        return out
    finally:
        fcntl.flock(lk, fcntl.LOCK_UN)
        os.close(lk)


def read_requests(root):
    try:
        cur = json.loads((Path(root) / "requests.json").read_text(encoding="utf-8"))
        rows = cur.get("requests") if isinstance(cur, dict) else None
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict) and NPUB_FULL_RE.match(str(r.get("owner") or ""))] \
        if isinstance(rows, list) else []


def add_request(root, rec, every=86400, now=None):
    """記一則申請;同一主人 every 秒內已有 ⇒ 不記、回 False(靜默,第 29.4 節)。"""
    now = int(time.time() if now is None else now)

    def fn(cur):
        rows = [r for r in cur.setdefault("requests", []) if isinstance(r, dict)]
        old = next((r for r in rows if r.get("owner") == rec["owner"]), None)
        if old and now - int(old.get("at") or 0) < every:
            return False
        rows = [r for r in rows if r.get("owner") != rec["owner"]]
        rows.insert(0, dict(rec, at=now))
        cur["requests"] = rows[:REQUESTS_MAX]
        return True
    return _locked_update(root, "requests.json", fn, lambda: {"v": VERSION, "requests": []})


# invites.json(10-24 清單第 11 條):邀請碼。對方 aib chat join <我的位址> <碼> ⇒ 好友申請內文帶 invite:<碼>,
# 我的轉接程式對得上(沒過期、還有次數)就自動同意,雙方不用再按。碼只存在本機;24 小時、一次。
# 10-24 晚 Andy「位址太長、照 10 碼做」:碼另外用名冊金鑰簽一筆 d=aib-invite:<雜湊> 放中繼站 ⇒ 對方只打碼就查得到位址。
#   中繼站只看得到雜湊(PBKDF2 20 萬次):每猜一次要先算一輪雜湊再問中繼站;3 億組同時有效時約 370 萬次猜中一組。
INVITE_RE = re.compile(r"^[A-Z2-7]{10}$")
INVITE_TTL_S = 86400
INVITE_HASH_RE = re.compile(r"^[0-9a-f]{32}$")


def invite_norm(text):
    """會員打的碼 ⇒ 10 碼大寫;容許小寫、中間一個 - 或空白、0 / 1 / 8 打成 O / I / B。不像碼 ⇒ None。"""
    t = (text or "").strip().upper()
    if t.count("-") + t.count(" ") > 1:
        return None
    t = t.replace("-", "").replace(" ", "").translate(str.maketrans({"0": "O", "1": "I", "8": "B"}))
    return t if INVITE_RE.match(t) else None


def invite_fmt(code):
    return code[:5] + "-" + code[5:]


# 中繼站上的邀請(#299 第 1 輪審 H1 / M1 / M2 / L4):碼 ⇒ PBKDF2(鹽帶建立那天的 UTC 日期)32 bytes,
#   前 16 = 查詢雜湊(d 標籤)、後 16 = 金鑰。內容用金鑰加密 + HMAC(綁簽章者),沒有碼的人發同一個 d 也過不了驗,
#   中繼站上也看不到位址 / 到期。真實威脅:用 kind 撈回當天全部雜湊離線猜碼;10 碼 50 bit、每猜一次 100 萬次 PBKDF2,
#   鹽帶日期 ⇒ 不能跨天預算。猜中只換到「跟碼的主人互加好友(預設分享)」,本人看得到、刪得掉。
INVITE_KDF_ITER = 1000000


def invite_day(ts):
    return time.strftime("%Y%m%d", time.gmtime(int(ts)))


def invite_keys(code, day):
    """⇒ (查詢雜湊 32 位十六進位, 金鑰 16 bytes)。day = 建立那天(invite_day);加入方查今天與昨天兩組。"""
    raw = hashlib.pbkdf2_hmac("sha256", code.encode("ascii"), ("aib-invite-v2:" + day).encode("ascii"), INVITE_KDF_ITER, 32)
    return raw[:16].hex(), raw[16:]


def _invite_xor(key, nonce, data):
    import hmac
    out = bytearray()
    for i in range(0, len(data), 32):
        block = hmac.new(key, b"aib-invite-ks\0" + nonce + i.to_bytes(4, "big"), hashlib.sha256).digest()
        out += bytes(x ^ y for x, y in zip(data[i:i + 32], block))
    return bytes(out)


def _invite_mac(key, author, nonce, ct):
    import hmac
    return hmac.new(key, b"aib-invite-mac\0" + author.encode("ascii") + b"\0" + nonce + ct, hashlib.sha256).hexdigest()


def invite_seal(key, author, plain):
    """邀請內容 ⇒ {"v":2,"n","c","m"}:HMAC-SHA256 計數器模式加密 + HMAC(簽章者 npub、nonce、密文)。"""
    import base64
    nonce = os.urandom(16)
    ct = _invite_xor(key, nonce, json.dumps(plain, sort_keys=True).encode("utf-8"))
    return {"v": 2, "n": base64.b64encode(nonce).decode("ascii"), "c": base64.b64encode(ct).decode("ascii"),
            "m": _invite_mac(key, author, nonce, ct)}


def invite_open(key, author, body):
    """驗 + 解 ⇒ dict;MAC 不對(沒有碼的人發的、或換了簽章者)/ 格式不對 ⇒ None。"""
    import base64
    import binascii
    import hmac
    if not isinstance(body, dict) or body.get("v") != 2 or not all(isinstance(body.get(k), str) for k in "ncm"):
        return None
    try:
        nonce, ct = base64.b64decode(body["n"], validate=True), base64.b64decode(body["c"], validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(nonce) != 16 or len(ct) > 2048 or not isinstance(author, str) or not author.isascii():
        return None
    if not hmac.compare_digest(_invite_mac(key, author, nonce, ct), body["m"]):
        return None
    try:
        plain = json.loads(_invite_xor(key, nonce, ct).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return plain if isinstance(plain, dict) else None
INVITE_NOTE_RE = re.compile(r"^invite:([A-Z2-7]{10})$")


def create_invite(root, now=None, ttl=INVITE_TTL_S, uses=1):
    """產一組邀請碼 ⇒ {code, expires_at, uses}。順手清掉過期的。"""
    import base64
    now = int(time.time() if now is None else now)
    code = base64.b32encode(os.urandom(10)).decode("ascii")[:10]
    row = {"code": code, "created_at": now, "expires_at": now + int(ttl), "uses": int(uses)}

    def fn(cur):
        rows = [r for r in cur.setdefault("invites", []) if isinstance(r, dict) and int(r.get("expires_at") or 0) > now
                and int(r.get("uses") or 0) > 0]
        cur["invites"] = rows[-19:] + [row]                 # 最多留 20 組
    _locked_update(root, "invites.json", fn, lambda: {"v": VERSION, "invites": []})
    return {k: row[k] for k in ("code", "created_at", "expires_at", "uses")}


def drop_invite(root, code):
    """作廢一組(放中繼站撞號、換一組時;沒交出去的碼不留著)。"""
    def fn(cur):
        cur["invites"] = [r for r in cur.setdefault("invites", []) if isinstance(r, dict) and r.get("code") != code]
    _locked_update(root, "invites.json", fn, lambda: {"v": VERSION, "invites": []})


def refund_invite(root, code):
    """扣了次數、後面卻沒加成好友(例:加好友丟 RosterError)⇒ 補回一次。"""
    def fn(cur):
        for r in cur.setdefault("invites", []):
            if isinstance(r, dict) and r.get("code") == code:
                r["uses"] = int(r.get("uses") or 0) + 1
    _locked_update(root, "invites.json", fn, lambda: {"v": VERSION, "invites": []})


def take_invite(root, code, now=None):
    """對得上(沒過期、還有次數)⇒ 次數減一、回那一筆(dict,算 True;要 created_at 推金鑰);其他 False。"""
    if not INVITE_RE.match(code or ""):
        return False
    now = int(time.time() if now is None else now)

    def fn(cur):
        for r in cur.setdefault("invites", []):
            if isinstance(r, dict) and r.get("code") == code and int(r.get("expires_at") or 0) > now \
                    and int(r.get("uses") or 0) > 0:
                r["uses"] = int(r["uses"]) - 1
                return dict(r, created_at=int(r.get("created_at") or int(r["expires_at"]) - INVITE_TTL_S))
        return False
    return _locked_update(root, "invites.json", fn, lambda: {"v": VERSION, "invites": []})


def pop_request(root, owner):
    """拿掉一則申請(同意 / 拒絕後),回那一筆;沒有回 None。"""
    def fn(cur):
        rows = [r for r in cur.setdefault("requests", []) if isinstance(r, dict)]
        hit = next((r for r in rows if r.get("owner") == owner), None)
        cur["requests"] = [r for r in rows if r.get("owner") != owner]
        return hit
    return _locked_update(root, "requests.json", fn, lambda: {"v": VERSION, "requests": []})


def note_check(root, owner, key, verified, now=None):
    """第 32 節:一項證明的驗證結果。key = nip05 / social:<平台>:<帳號>;verified = True / False / None(平台不能自動驗)。"""
    now = int(time.time() if now is None else now)

    def fn(cur):
        p = cur.setdefault("peers", {}).get(owner)
        if p is not None:
            p.setdefault("checks", {})[key] = {"verified": verified, "at": now}
    _locked_update(root, "peers.json", fn, lambda: {"v": VERSION, "peers": {}})


def read_peers(root):
    try:
        cur = json.loads((Path(root) / "peers.json").read_text(encoding="utf-8"))
        peers = cur.get("peers") if isinstance(cur, dict) else None
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in peers.items() if NPUB_FULL_RE.match(k) and isinstance(v, dict)} if isinstance(peers, dict) else {}


def note_peer(root, owner, display_name=None, country=None, seat=None, roster_at=None, direction=None, now=None,
              request=None, claims=None):
    """更新一位對方主人的快照。seat = {cabin, seat, npub, name, role}(名冊上的公開欄位)。direction = in / out。"""
    if not NPUB_FULL_RE.match(owner or ""):
        return
    now = int(time.time() if now is None else now)

    def fn(cur):
        peers = cur.setdefault("peers", {})
        p = peers.setdefault(owner, {"seats": [], "n_in": 0, "n_out": 0})
        fresh = isinstance(claims, dict) and isinstance(claims.get("roster_seats"), list) and roster_at \
            and int(roster_at) >= int(p.get("roster_at") or 0)
        if display_name:
            p["display_name"] = display_name
        if country:
            p["country"] = country
        if roster_at:
            p["roster_at"] = max(int(roster_at), int(p.get("roster_at") or 0))     # 舊名冊不蓋掉新的時間
        if fresh:
            # 10-18 @74e8b:拿到不比快照舊的名冊 ⇒ seats 整份換成名冊目前公開的席(不在名冊上的死席拿掉、順序照名冊)
            p["seats"] = [{k: x.get(k) for k in ("cabin", "seat", "npub", "name", "role")}
                          for x in claims["roster_seats"] if isinstance(x, dict) and x.get("seat")][:20]
        elif isinstance(seat, dict) and seat.get("seat") and not (isinstance(claims, dict) and "roster_seats" in claims):
            # 帶了名冊席位卻比快照舊 ⇒ 是舊名冊,這一席也不補(不然死席又被加回來)
            keep = {k: seat.get(k) for k in ("cabin", "seat", "npub", "name", "role")}
            p["seats"] = [x for x in p.get("seats") or [] if x.get("seat") != keep["seat"] or x.get("cabin") != keep["cabin"]]
            p["seats"].append(keep)
            p["seats"] = p["seats"][-20:]
        p["seen_at"] = now
        if isinstance(claims, dict):          # 第 32 節:對方宣告的 NIP-05(名冊)與社群(好友私下送的 card);驗的結果另存 checks
            if "socials" in claims:
                p["socials"] = claims.get("socials") or []
            if "nip05" in claims:
                p["nip05"] = claims.get("nip05")
            if claims.get("aib"):
                p["aib"] = str(claims["aib"])[:16]
        if request in ("pending", "accepted", "rejected"):   # 我送出的好友申請,對方回條推到哪(第 29.4 節)
            p["request"] = request
        if direction in ("in", "out"):
            p["last_" + direction] = now
            p["n_" + direction] = int(p.get("n_" + direction) or 0) + 1
        drop = [k for k, v in peers.items() if now - int(v.get("seen_at") or 0) > PEERS_KEEP_S]
        for k in drop:
            peers.pop(k, None)
        if len(peers) > PEERS_MAX:
            for k in sorted(peers, key=lambda k: int(peers[k].get("seen_at") or 0))[:len(peers) - PEERS_MAX]:
                peers.pop(k, None)
    _locked_update(root, "peers.json", fn, lambda: {"v": VERSION, "peers": {}})


# world.json(第 30.4 節):好友 / 我的其他機器分享來的在線狀態。只有工人寫;核心讀時判過期。
WORLD_FRESH_S = 40 * 60
BEAT_GREEN_S = 10 * 60      # 10-24 Andy 綠點:最後一筆在線狀態 / 心跳在這麼久以內 ⇒ 🟢
WORLD_STATES = ("online", "idle", "away", "asleep", "unknown")


def read_world(root):
    try:
        cur = json.loads((Path(root) / "world.json").read_text(encoding="utf-8"))
        cabins = cur.get("cabins") if isinstance(cur, dict) else None
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in cabins.items() if isinstance(v, dict)} if isinstance(cabins, dict) else {}


def note_world(root, owner, cabin, seats, now=None, power=None):
    """收到一份 presence:{owner/cabin: {owner, cabin, presence_at, seats:[{seat, state, now, step}], power}}。
    power = 對方那台的電源 / 睡眠設定(chat_power.clean_power 過的;舊版沒有 ⇒ None)。"""
    now = int(time.time() if now is None else now)
    key = "%s/%s" % (owner, cabin)

    def fn(cur):
        cab = cur.setdefault("cabins", {})
        cab[key] = {"owner": owner, "cabin": cabin, "presence_at": now, "seats": seats}
        if power:
            cab[key]["power"] = power
        for k in [k for k, v in cab.items() if now - int(v.get("presence_at") or 0) > PEERS_KEEP_S]:
            cab.pop(k, None)
    _locked_update(root, "world.json", fn, lambda: {"v": VERSION, "cabins": {}})


# refresh.json(10-24 在不在):本人打開世界面板 ⇒ 核心寫 requested_at;工人看到就在下一份在線狀態加
# aib-ask=1 向好友 / 我的其他機器問一次,寫 asked_at。對方回的是一份普通在線狀態(舊版收到 aib-ask 不認得、照常收)。
REFRESH_GAP_S = 30


def request_refresh(root, now=None, ping=None):
    """要工人刷新一次:查好友心跳 + 問還沒有心跳的舊版好友「在不在」。上次要求不到 REFRESH_GAP_S ⇒ 不重寫,回 False。
    ping = 本人點開對話框的那一位主人(10-24 Andy:「在不在」只問那一位);帶 ping 不看間隔(對方那邊同一位 60 秒只回一次)。"""
    now = int(time.time() if now is None else now)
    if ping is not None and not NPUB_FULL_RE.match(ping):
        return False

    def fn(cur):
        if not ping and now - int(cur.get("requested_at") or 0) < REFRESH_GAP_S:
            return False
        cur["requested_at"] = now
        if ping:
            cur["ping"] = ping
        else:
            cur.pop("ping", None)
        return True
    return _locked_update(root, "refresh.json", fn, lambda: {"v": VERSION})


def note_asked(root, now=None):
    now = int(time.time() if now is None else now)
    _locked_update(root, "refresh.json", lambda cur: cur.__setitem__("asked_at", now), lambda: {"v": VERSION})


def read_refresh(root):
    """⇒ {requested_at, asked_at}(沒有的鍵不列)。requested_at > asked_at = 工人還沒問。"""
    try:
        cur = json.loads((Path(root) / "refresh.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(cur, dict):
        return {}
    out = {k: cur[k] for k in ("requested_at", "asked_at") if isinstance(cur.get(k), int) and not isinstance(cur[k], bool)}
    if NPUB_FULL_RE.match(str(cur.get("ping") or "")):
        out["ping"] = cur["ping"]
    return out


# beats.json(10-24 Andy 綠點):好友名片給的心跳鑰 {keys: {主人: {心跳 npub: {key, gen, relays, at}}}};
# beat-given.json:我這一代共用鑰給過誰(有人不再是好友 / 封鎖 / 改 off ⇒ 換一代)。兩個都 0600,只有工人寫。
BEAT_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
BEAT_PER_OWNER = 10
BEAT_RELAYS_MAX = 5


def note_beat_key(root, owner, beat_npub, key_hex, gen, relays=(), now=None):
    """記一位好友(某一台)的心跳鑰。不合格 ⇒ False。同一台新一代蓋掉舊的;一位主人最多留 BEAT_PER_OWNER 台。"""
    if not (NPUB_FULL_RE.match(owner or "") and NPUB_FULL_RE.match(beat_npub or "") and BEAT_KEY_RE.match(key_hex or "")):
        return False
    if not isinstance(gen, int) or isinstance(gen, bool) or gen < 1:
        return False
    rel = [r for r in relays if isinstance(r, str) and r.startswith("wss://") and len(r) <= 200][:BEAT_RELAYS_MAX] \
        if isinstance(relays, (list, tuple)) else []
    now = int(time.time() if now is None else now)

    def fn(cur):
        mine = cur.setdefault("keys", {}).setdefault(owner, {})
        old = mine.get(beat_npub) or {}
        if int(old.get("gen") or 0) > gen:
            return False                                   # 舊名片晚到,不蓋新的
        mine[beat_npub] = {"key": key_hex, "gen": gen, "relays": rel, "at": now}
        for k in sorted(mine, key=lambda k: mine[k]["at"])[:-BEAT_PER_OWNER]:
            mine.pop(k, None)
        return True
    return _locked_update(root, "beats.json", fn, lambda: {"v": VERSION, "keys": {}})


def read_beat_keys(root):
    """⇒ [(主人, 心跳 npub, key, relays)]。"""
    try:
        cur = json.loads((Path(root) / "beats.json").read_text(encoding="utf-8"))
        keys = cur.get("keys") if isinstance(cur, dict) else None
    except (OSError, ValueError):
        return []
    out = []
    for owner, machines in (keys or {}).items() if isinstance(keys, dict) else ():
        for npub, k in (machines or {}).items() if isinstance(machines, dict) else ():
            if isinstance(k, dict) and BEAT_KEY_RE.match(str(k.get("key") or "")):
                out.append((owner, npub, k["key"], list(k.get("relays") or [])))
    return out


def drop_beat_keys(root, keep_owner):
    def fn(cur):
        ks = cur.setdefault("keys", {})
        for o in [o for o in ks if not keep_owner(o)]:
            ks.pop(o, None)
    _locked_update(root, "beats.json", fn, lambda: {"v": VERSION, "keys": {}})


def read_beat_given(root):
    try:
        cur = json.loads((Path(root) / "beat-given.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, set()
    if not isinstance(cur, dict):
        return 0, set()
    return int(cur.get("gen") or 0), {o for o in cur.get("owners") or [] if isinstance(o, str)}


def note_beat_given(root, gen, owners):
    """這一代給過的人(gen 換了 ⇒ 整份重來)。"""
    def fn(cur):
        if cur.get("gen") != gen:
            cur["gen"], cur["owners"] = gen, []
        cur["owners"] = sorted(set(cur.get("owners") or []) | set(owners))
    _locked_update(root, "beat-given.json", fn, lambda: {"v": VERSION})


# first.json(10-24 Andy 第 8 條「做，只存本機」):開通到第一封對話各步第一次的時間,只記時間不記內容、不送出去。
# on 開通 / sent 第一封送上中繼站 / delivered 第一張「對方收下」回條(queued 或 delivered)/ in 第一封收進來的信
FIRST_KEYS = ("on", "sent", "delivered", "in")


def read_first(root):
    try:
        cur = json.loads((Path(root) / "first.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(cur, dict):
        return {}
    return {k: int(cur[k]) for k in FIRST_KEYS if isinstance(cur.get(k), int) and not isinstance(cur.get(k), bool)}


def note_first(root, key, now=None):
    """只記第一次:已經有就不動(之後關掉再開也不重算)。"""
    if key not in FIRST_KEYS or key in read_first(root):
        return
    at = int(time.time() if now is None else now)

    def fn(cur):
        cur.setdefault(key, at)
    _locked_update(root, "first.json", fn, lambda: {"v": VERSION})


# echo.json(10-24 Andy 選「官方回音小幫手」):這台當回音小幫手 ⇒ 收到信只回一句,不存內容、不接 AI。
def echo_on(root):
    try:
        cur = json.loads((Path(root) / "echo.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(cur, dict) and cur.get("on") is True


def set_echo(root, on):
    p = Path(root) / "echo.json"
    if on:
        Path(root).mkdir(parents=True, exist_ok=True)
        write_json(str(p), {"v": VERSION, "on": True, "at": int(time.time())})
    else:
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def drop_world(root, keep_owner):
    """拿掉不再分享對象(好友移除、封鎖)的格;keep_owner(owner) ⇒ True 才留。"""
    def fn(cur):
        cab = cur.setdefault("cabins", {})
        for k in [k for k, v in cab.items() if not keep_owner(v.get("owner"))]:
            cab.pop(k, None)
    _locked_update(root, "world.json", fn, lambda: {"v": VERSION, "cabins": {}})


# spaces/<主人>.json(第 34 節 v2):好友分享來的空間快照(島 + 艦長室);只有工人寫,核心讀。
ROOM_FRESH_S = 7 * 86400


def write_room(root, owner, snap, now=None):
    if not NPUB_FULL_RE.match(owner or ""):
        return
    d = Path(root) / "spaces"
    d.mkdir(parents=True, exist_ok=True)
    write_json(str(d / (owner + ".json")), {"v": VERSION, "owner": owner, "at": int(time.time() if now is None else now),
                                            "snap": snap})
    os.chmod(str(d / (owner + ".json")), 0o600)


def write_space_status(root, rec):
    """space-status.json:最近一次送空間快照的結果(第 36.3 節;too_big ⇒ aib chat space --json 的 last_error)。"""
    d = Path(root)
    d.mkdir(parents=True, exist_ok=True)
    write_json(str(d / "space-status.json"), dict(rec, v=VERSION))
    os.chmod(str(d / "space-status.json"), 0o600)


def read_space_status(root):
    try:
        rec = json.loads((Path(root) / "space-status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def default_address(root, owner, env=None):
    """第二波 #4(NOTE B5「全球位址太難」):一位主人 ⇒ 給他寄信的預設位址 (addr, why)。
    why = captain(他房間快照裡標的艦長席)/ first(聯絡人快照第一個公開席)/ None(什麼都不知道 ⇒ addr None)。
    只讀本機檔、不連網;位址一律 <主人 npub>/<艙>/<席>,頁面與 aib chat send 用它,不讓人打位址。"""
    if not NPUB_FULL_RE.match(owner or ""):
        return None, None
    rec = read_room(root, owner)
    rooms = ((rec or {}).get("snap") or {}).get("rooms")
    if isinstance(rooms, dict):
        cap = ((rooms.get("ship") or {}).get("captain") or {}).get("seat") if isinstance(rooms.get("ship"), dict) else None
        for s in rooms.get("seats") or []:
            if isinstance(s, dict) and cap and s.get("seat") == cap:
                return "%s/%s/%s" % (owner, s.get("cabin") or "main", cap), "captain"
    for x in (read_peers(root).get(owner) or {}).get("seats") or []:
        if isinstance(x, dict) and x.get("seat"):
            return "%s/%s/%s" % (owner, x.get("cabin") or "main", x["seat"]), "first"
    return None, None


def read_room(root, owner):
    if not NPUB_FULL_RE.match(owner or ""):
        return None
    try:
        rec = json.loads((Path(root) / "spaces" / (owner + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and isinstance(rec.get("snap"), dict) else None


def sender_from_token(token):
    """dm-ctl 的 whoami token(dm_xhost.encode,跨主機同一套推導)⇒ {cabin, seat, attested};推不出寄件席回 None。

    全球信要用寄件席自己的金鑰簽(第 5.3 節),推不出是哪一席就不能寄 ⇒ 呼叫端回 rc 2。"""
    import dm_xhost
    d = dm_xhost.decode(token or "")
    if not d:
        return None
    m = _LABEL_SEAT_RE.match(d["label"])
    attested = m is not None
    if m is None:
        m = _LABEL_CLAIMED_RE.match(d["label"])    # 自稱席照寄,但標未認證(收件端抬頭會顯示未驗證)
    hub = d["hub"]
    if not m or not dm_xhost.HUB_RE.fullmatch(hub or ""):
        return None
    cabin = "main" if hub == "aihub" else hub.rsplit("-", 1)[-1]
    if not _CABIN.match(cabin):
        return None
    return {"cabin": cabin, "seat": m.group(1), "attested": attested}


def reply_target(seat, code, env=None):
    """收件席 + 回信短碼 ⇒ (原寄件全球位址, 原 msgid);找不到回 None。查佇列(dm_queue.by_reply_code)。"""
    import dm_queue
    item = dm_queue.by_reply_code(seat, code, env)
    if not item:
        return None
    g = item["global"]
    return g["addr"], g["msgid"]


def _cli(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="dm_global_files.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sw = sub.add_parser("send-and-wait", help="正文從 stdin 讀;印一行 key=value,rc 照第 7.1 節")
    sw.add_argument("--root", default=None)
    sw.add_argument("--op", choices=("send", "resolve"), default="send")
    sw.add_argument("--to")
    sw.add_argument("--reply-code", help="代替 --to:照外框說明上的短碼回原寄件人(第 9 節)")
    sw.add_argument("--whoami-token", help="代替 --from-*:dm-ctl 推導的寄件人 token")
    sw.add_argument("--from-cabin")
    sw.add_argument("--from-seat")
    sw.add_argument("--attested", choices=("0", "1"))
    sw.add_argument("--persona-uid")
    for k in ("priority", "thread", "kind", "lang", "reply-to"):
        sw.add_argument("--" + k)
    sw.add_argument("--expires", type=int)
    sw.add_argument("--wait", type=int, default=None)
    sw.add_argument("--verify", action="store_true")
    rc_ = sub.add_parser("reply-code")
    rc_.add_argument("msgid")
    a = ap.parse_args(argv)
    if a.cmd == "reply-code":
        print(reply_code(a.msgid))
        return 0
    def _bail(stage, detail):
        print("stage=%s rc=2 msgid=- pos=- detail=%s" % (stage, detail))
        return 2
    if a.whoami_token:
        who = sender_from_token(a.whoami_token)
        if who is None:
            return _bail("abort", "推不出寄件席(全球信要用寄件席自己的金鑰簽),一個字都沒送")
    elif a.from_cabin and a.from_seat and a.attested:
        who = {"cabin": a.from_cabin, "seat": a.from_seat, "attested": a.attested == "1"}
    else:
        return _bail("abort", "要給 --whoami-token 或 --from-cabin/--from-seat/--attested")
    to, reply_to = a.to, a.reply_to
    if a.reply_code:
        if a.to:
            return _bail("abort", "--reply 與位址不能同時給")
        hit = reply_target(who["seat"], a.reply_code)
        if hit is None:
            return _bail("abort", "回信短碼 %s 在 @%s 的佇列找不到" % (a.reply_code, who["seat"]))
        to, reply_to = hit
    if not to:
        return _bail("abort", "沒有收件位址")
    to_human = bool(reply_to) and reply_to_human(who["seat"], reply_to)
    root = Path(a.root) if a.root else default_root()
    ensure_dirs(root)
    rec = {"v": VERSION, "msgid": new_msgid(), "created_at": int(time.time()), "op": a.op,
           "from": dict(who), "to": to}
    if a.persona_uid:
        rec["from"]["persona_uid"] = a.persona_uid
    a.reply_to = reply_to
    if a.op == "send":
        rec["content"] = sys.stdin.read()
    for k in ("priority", "thread", "kind", "lang"):
        if getattr(a, k):
            rec[k] = getattr(a, k)
    if a.reply_to:
        rec["reply_to"] = a.reply_to
    if a.expires is not None:
        rec["expires"] = a.expires
    wait = a.wait if a.wait is not None else int(os.environ.get("AIB_DM_GLOBAL_WAIT") or WAIT_DEFAULT)
    res = with_hint(send_and_wait(root, rec, wait=wait, verify=a.verify), to_human)
    detail = (res["detail"] or "").replace("\n", " ")
    # hint 放在 detail 前面:dm-ctl 取 detail 是「 detail= 之後到行尾」,放後面會被吃進 detail(代碼就變了)
    print("stage=%s rc=%d msgid=%s pos=%s%s detail=%s" % (res["stage"], res["rc"], res["msgid"] or "-",
                                                          res["pos"] or "-",
                                                          (" hint=" + res["hint"]) if res.get("hint") else "", detail))
    return res["rc"]


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
