#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""262(c) 用量收據(只記不扣)。規格 `PAGE-WRITE-API-v1.md` v2 §十三(顆 22d323ae,sha16 98f7f3d87a5c1c0a)。

§13.8 給我的四條,逐條落在這支檔裡:
1. 每次模型呼叫結束送一筆,`call.at` 用**呼叫開始**時刻,operation_key 含席碼+時刻+序號。
2. `funding` 由**綁定記錄**決定,**不准由模型名猜** —— 同一個 `claude-opus-5` 可能是 Ez2 託管
   也可能是客戶自己的訂閱。猜錯的後果是「把別人的訂閱算成可販售」(U4 那把刀)。
   ⇒ 這裡沒有任何從 `model.id` 推 funding 的路;查不到綁定就**具名拒送**,不編一個。
3. 送不出去(網路/503)本地留檔、下次同 key 重送;**永遠不要因為用量送不出去而不寫草稿**。
   ⇒ 本檔與 `chat.py` 沒有共用的 try/寫入段(U7 的刀對準的就是「兩者共用一段」)。
4. 收據裡不放對話內容、不放區塊內容、不放憑證。

★ `tier` 與 `tenant_id` **不是**可送的鍵(§13.3):等級由 PAGE 的版本化等級表定,帳戶由 token 定。
  送了會 400。這裡連組都不組進去 —— 見 `ALLOWED_TOP` / `build_receipt()`。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

import atomic_json  # noqa: F401  (spool 用逐行 append,不走 atomic_json;保留匯入只為一致的相依說明)
from seats_dir_lib import state_root

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}$")
PURPOSES = ("page_chat", "page_diff", "page_read")
FUNDINGS = ("ez2_hosted", "customer_api_key", "customer_subscription", "customer_local")
TOKEN_FIELDS = ("input_uncached", "input_cached", "output", "output_reasoning_included")
ALLOWED_TOP = ("operation_key", "call", "binding", "model", "tokens", "tools")
MAX_BYTES = 16 * 1024
MAX_TOOLS = 20


class UsageError(Exception):
    """收據組不出來或不該送。訊息是人話。"""


def _check_id(name, value):
    if not isinstance(value, str) or not ID_RE.match(value):
        raise UsageError("%s 不合規格的 ID_RE:%r" % (name, value))
    return value


def spool_path(tenant_id, page_id, environ=None):
    """送不出去時的本地留檔(規格 §13.5 建議的路徑)。"""
    _check_id("tenant_id", tenant_id)
    _check_id("page_id", page_id)
    root, _source = state_root(environ)
    return os.path.join(root, "usage", "page-%s.%s.jsonl" % (tenant_id, page_id))


def load_binding(environ=None, explicit=None):
    """綁定記錄:哪一個供應商帳號、誰在付錢。

    順位:明給的(CLI 旗標)> `<狀態根>/usage/page-bindings.json`。
    **查不到就拋錯**,不從模型名猜(§13.8 第 2 條)。
    """
    if explicit and all(explicit.get(k) for k in ("binding_id", "provider", "funding")):
        return _validated_binding(explicit)
    root, _source = state_root(environ)
    path = os.path.join(root, "usage", "page-bindings.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
    except FileNotFoundError:
        raise UsageError(
            "這台機器還沒設定「哪一個供應商帳號、誰在付錢」,所以我不送用量收據。"
            "給 --binding-id/--provider/--funding,或寫一份 %s。"
            "★ 我不會從模型名猜 funding:同一個模型可能是 Ez2 託管,也可能是你自己的訂閱" % path)
    except (OSError, ValueError) as exc:
        raise UsageError("綁定記錄讀不動(%s):%s" % (path, exc))
    if not isinstance(record, dict):
        raise UsageError("綁定記錄不是物件:%s" % path)
    return _validated_binding(record)


def _validated_binding(record):
    binding = {
        "binding_id": _check_id("binding_id", record.get("binding_id")),
        "provider": _check_id("provider", str(record.get("provider") or "")).lower(),
        "funding": record.get("funding"),
    }
    if binding["funding"] not in FUNDINGS:
        raise UsageError(
            "funding 要是 %s 之一(收到 %r);這一欄決定收據能不能販售,猜不得"
            % ("/".join(FUNDINGS), binding["funding"]))
    return binding


def build_receipt(operation_key, call_id, started_at, seat_label, purpose,
                  binding, model_id, tokens, tools=None, linked_operation_key=None):
    """組一筆收據。`started_at` 是**呼叫開始**的時刻(§13.8 第 1 條)。"""
    _check_id("operation_key", operation_key)
    _check_id("call_id", call_id)
    _check_id("model.id", model_id)
    if purpose not in PURPOSES:
        raise UsageError("purpose 要是 %s 之一(收到 %r)" % ("/".join(PURPOSES), purpose))
    if not isinstance(seat_label, str) or not seat_label.strip():
        raise UsageError("seat_label 不能空 —— 收據要看得出是哪一席花的")
    counts = {}
    for field in TOKEN_FIELDS:
        value = (tokens or {}).get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise UsageError("tokens.%s 要是 >=0 的整數(收到 %r)" % (field, value))
        counts[field] = value
    # §3.3:推理含在輸出內,不得再加收 ⇒ 這一行在客戶端就擋(U5 的同一條判準)
    if counts["output_reasoning_included"] > counts["output"]:
        raise UsageError(
            "output_reasoning_included(%d)不能大於 output(%d):推理是含在輸出裡的,不另計"
            % (counts["output_reasoning_included"], counts["output"]))
    receipt = {
        "operation_key": operation_key,
        "call": {
            "call_id": call_id,
            "at": _iso(started_at),
            "seat_label": seat_label.strip(),
            "purpose": purpose,
        },
        "binding": _validated_binding(binding),
        "model": {"id": model_id},
        "tokens": counts,
    }
    if linked_operation_key:
        receipt["call"]["linked_operation_key"] = _check_id(
            "linked_operation_key", linked_operation_key)
    if tools:
        if len(tools) > MAX_TOOLS:
            raise UsageError("tools 最多 %d 筆" % MAX_TOOLS)
        clean = []
        for tool in tools:
            kind = _check_id("tools[].kind", (tool or {}).get("kind"))
            count = (tool or {}).get("count")
            if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                raise UsageError("tools[].count 要是 >=1 的整數(收到 %r)" % (count,))
            clean.append({"kind": kind, "count": count})
        receipt["tools"] = clean
    extra = [k for k in receipt if k not in ALLOWED_TOP]
    if extra:
        raise UsageError("收據多了不該送的鍵:%s" % extra)
    blob = json.dumps(receipt, ensure_ascii=False).encode("utf-8")
    if len(blob) > MAX_BYTES:
        raise UsageError("收據 %d 位元組超過 16 KiB" % len(blob))
    return receipt


def _iso(value):
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = str(value or "").strip()
    if not text:
        raise UsageError("call.at 不能空;要用呼叫**開始**的時刻")
    return text


def send(api, receipt, tenant_id, page_id, environ=None, out=None):
    """送一筆。回 (有沒有送到, 說明)。

    **這支永遠不拋到呼叫端之外的寫入路徑**:送不出去就本地留檔、回 False,
    由呼叫端印一行。用量失敗不准影響草稿(§13.5 第 1 條)。
    """
    say = out or (lambda _line: None)
    try:
        response = api.post_usage(receipt)
    except Exception as exc:                     # 連不上、逾時、4xx、5xx 都走同一條:先留住收據
        path = spool(receipt, tenant_id, page_id, environ)
        say("用量沒送出去(%s);已留在 %s,下次用同一把 key 重送,不會重計" % (exc, path))
        return False, str(exc)
    if response.get("replayed"):
        say("用量收據先前送過(同一把 key),伺服器沒有重計")
    return True, response.get("receipt_id") or ""


def spool(receipt, tenant_id, page_id, environ=None):
    """把送不出去的收據逐行追加到本地檔。回檔案路徑。"""
    path = spool_path(tenant_id, page_id, environ)
    directory = os.path.dirname(path)
    try:
        os.makedirs(directory, exist_ok=True)
        os.chmod(directory, 0o700)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
        os.chmod(path, 0o600)
    except OSError as exc:
        raise UsageError("用量收據留不下來(%s):%s" % (path, exc))
    return path


def flush(api, tenant_id, page_id, environ=None, out=None):
    """把本地留著的收據重送。同一把 key 重送不會重計(伺服器冪等)。

    回 (送出幾筆, 還剩幾筆)。送不掉的原樣留著,**不丟**。
    """
    say = out or (lambda _line: None)
    path = spool_path(tenant_id, page_id, environ)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            rows = [line for line in handle.read().splitlines() if line.strip()]
    except FileNotFoundError:
        return 0, 0
    except OSError as exc:
        raise UsageError("留檔讀不動(%s):%s" % (path, exc))
    sent, left = 0, []
    for line in rows:
        try:
            receipt = json.loads(line)
        except ValueError:
            left.append(line)                    # 壞行留著給人看,不靜默丟掉
            continue
        try:
            api.post_usage(receipt)
            sent += 1
        except Exception as exc:
            say("重送失敗(%s),保留" % exc)
            left.append(line)
    try:
        if left:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(left) + "\n")
            os.chmod(path, 0o600)
        else:
            os.unlink(path)
    except OSError as exc:
        raise UsageError("留檔更新不了(%s):%s" % (path, exc))
    return sent, len(left)
