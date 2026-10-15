#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球 dm:核心與轉接程式之間的檔案契約(開發文件 v0.4 第 22.2 節,PR-3)。

核心(dm-ctl.sh、dm_queue.py:只用標準庫、不連網)與轉接程式(選用外掛,有 nostr-sdk)
**只靠四個資料夾溝通**,互不 import 對方的連網程式。這支只用標準庫,兩邊都可以 import 它,
格式規則因此只有這一份。

    outbox/<msgid>.json      核心 → 外掛  要寄的信(op=send)或 --dry-run 的解析請求(op=resolve)
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
"""
from __future__ import annotations

import fcntl
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
BOXES = ("outbox", "receipts", "inbox", "delivery")
DIR_MODE = 0o700
KEEP_DAYS = 7
CONTENT_MAX = 8000          # 第 6.3 節
NAME_MAX = 32               # 第 5.8 節

MSGID_RE = re.compile(r"^[0-9a-f]{32}$")
NPUB_RE = r"npub1[02-9ac-hj-np-z]{58}"
_ADDR_RE = re.compile(r"^(?:(?P<owner>%s)/(?P<cabin>[0-9a-z]{1,4})/(?P<seat>[0-9a-z]{4,8})|(?P<npub>%s))$"
                      % (NPUB_RE, NPUB_RE))
_NPUB_ONLY = re.compile(r"^%s$" % NPUB_RE)
_CABIN = re.compile(r"^[0-9a-z]{1,4}$")
_SEAT = re.compile(r"^[0-9a-z]{4,8}$")

PRIORITIES = ("urgent", "important", "normal", "reminder")   # 與 dm_queue.PRIORITIES 同序
RECEIPT_STAGES = ("resolved", "relayed", "queued", "delivered", "held", "refused", "expired", "read", "failed")
RECEIPT_FINAL = ("delivered", "held", "refused", "expired", "failed")
DELIVERY_STATES = ("queued", "delivered", "held", "refused", "expired", "read")
INBOX_CLASSES = ("own", "paired", "friend", "stranger")
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


def check_outbox(rec):
    _base(rec)
    _need(rec, "created_at", _is_int, "要是非負整數(UTC 秒)")
    _need(rec, "op", lambda v: v in ("send", "resolve"), "只能 send / resolve")
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
    _opt(rec, "priority", lambda v: v in PRIORITIES, "只能 %s" % "/".join(PRIORITIES))
    _opt(rec, "expires", _is_int, "要是非負整數(UTC 秒)")
    _opt(rec, "reply_to", lambda v: isinstance(v, str) and bool(MSGID_RE.match(v)), "要是 msgid")
    for k in ("thread", "kind", "lang"):
        _opt(rec, k, _is_str, "要是字串")
    return rec


def check_receipt(rec):
    if not isinstance(rec, dict) or rec.get("v") != VERSION:
        raise ContractError("回條版本不對")
    _need(rec, "at", _is_int, "要是非負整數(UTC 秒)")
    _need(rec, "stage", lambda v: v in RECEIPT_STAGES, "不認得的階段")
    _opt(rec, "pos", lambda v: _is_int(v) and v > 0, "要是正整數")
    _opt(rec, "detail", _is_str, "要是字串")
    return rec


def check_delivery(rec):
    if not isinstance(rec, dict) or rec.get("v") != VERSION:
        raise ContractError("投遞狀態版本不對")
    _need(rec, "at", _is_int, "要是非負整數(UTC 秒)")
    _need(rec, "state", lambda v: v in DELIVERY_STATES, "不認得的狀態")
    _opt(rec, "pos", lambda v: _is_int(v) and v > 0, "要是正整數")
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
    return rec


def queue_fields(rec):
    """inbox 一筆 ⇒ 入佇列要用的身分與狀態欄位(PR-8 的匯入器照這個寫佇列)。

    - sender_kind:主人手機(已驗證)⇒ owner_paired;其他一律 external_seat。**永遠不會是 human**。
    - 未驗證 ⇒ 不論外掛分到哪一類,一律當陌生。
    - 陌生 ⇒ hold_human(待審)、優先級降為 normal。
    - shown_name:顯示名稱 → 人格名 → npub 前 12 碼(第 5.8 節);不合格的名稱直接跳過。
    """
    check_inbox(rec)
    cls = rec["class"] if rec["verified"] else "stranger"
    prio = rec.get("priority") or "normal"
    if cls == "stranger":
        prio = "normal"
    shown = clean_display_name(rec.get("display_name")) or clean_display_name(rec.get("persona_name")) \
        or rec["from_npub"][:12]
    return {
        "sender_kind": "owner_paired" if cls == "paired" else "external_seat",
        "sender_origin": "nostr:" + rec["from_npub"],
        "sender_addr": rec.get("from_addr") or rec["from_npub"],
        "shown_name": shown,
        "verified": rec["verified"],
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
