#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Copilot 模型探針:把 CLI 自己的 `~/.copilot/session-store.db` 接到艦隊 `.pct` 量尺。

★ 為什麼要有這一支(09-05 量出來的):四個量尺寫端是 claude/agy/codex 的 statusline
  與 grok 的 signals 探針 —— **copilot 一個都沒有**,所以右欄永遠退回三字碼 `CPT`。
  而 copilot 跑的模型**不是**開席要求的那一個:席位檔 `model_requested=gpt-5.6-sol`、
  它的 settings.json 預設 `grok-4.6`,實際 `assistant_usage_events` 第一列是 **kimi-k3**。
  三個值三個樣,只有最後一個是 provider 觀測到的。

★ 讀什麼:`assistant_usage_events` 每一回合一列,`model` 欄是那一回合真的打到的模型。
  取 `turn_index` 最大的那列(同回合多列取 id 最大)。只讀不寫,`mode=ro`。

★ pct:用量表只有 input_tokens,沒有 context window 大小,單靠它算不出百分比。
  10-11(7bb3f,the maintainer 問 Copilot 席 CTX 為什麼是「?」)量到:新版 copilot 每次呼叫模型都在
  events.jsonl 寫一列 `session.usage_record`,`data.usage` 同時有 `inputTokens`(這次送出去的整段,
  含快取)和 `maxPromptTokens`(這個模型能收的上限)⇒ pct = inputTokens / maxPromptTokens。
  只認主對話那一列(`interactionType=conversation-agent`),模型也要跟寫進去的那個一樣;
  量不到(舊版沒有這列、換了模型還沒跑下一回合)就照舊寫 None —— `authority="unknown"`、
  `tier="unknown"`,讀端 `ctx_of` 回 `--`。★ 不編一個 pct:編出來的百分比與量到的在畫面上逐字同形。

★ 與 grok 探針同一條紀律:失敗(缺 db、缺列、壞 uuid)保持現況 —— 不刪既有 `.pct`、
  不丟例外;schema / 原子寫入直接引用 grok_ctx_probe,不抄第二份。
"""

import json
import os
import re
import sqlite3
import sys
import time
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grok_ctx_probe as _gauge          # noqa: E402  SCHEMA / GAUGE_VERSION / _atomic_write 單源

SOURCE = "copilot_usage_events"
#: 10-01(the maintainer:copilot 換模型後右欄要跟著變):換模型那一刻 copilot 在自己的
#: `session-state/<uuid>/events.jsonl` 寫一列 `session.model_change`(`data.newModel`),
#: 而用量表要等**下一回合**才有新模型那一列。兩邊取時間較新的那一個。
SOURCE_CHANGE = "copilot_model_change"
#: 只讀事件檔尾巴這麼多位元組:換模型之後若已跑了一整回合,用量表那一列更新,
#: 尾巴讀不到那一列 model_change 也無妨(時間較新的用量列會贏)。
EVENTS_TAIL_BYTES = 256 * 1024
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_QUERY = ("SELECT model, created_at FROM assistant_usage_events "
          "WHERE session_id = ? ORDER BY turn_index DESC, id DESC LIMIT 1")


def db_path():
    return (os.environ.get("EZ2_COPILOT_SESSION_DB")
            or os.path.expanduser("~/.copilot/session-store.db"))


def latest_turn(uuid, db=None):
    """回 (model, created_at) 或 None。只讀;db 缺/鎖住/沒那張表都回 None。"""
    if not _UUID_RE.fullmatch(str(uuid or "")):
        return None
    path = db or db_path()
    if not os.path.isfile(path):
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % quote(path), uri=True, timeout=0.2)
        try:
            row = con.execute(_QUERY, (uuid,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if not row or not isinstance(row[0], str) or not row[0].strip():
        return None
    return row[0].strip(), row[1]


def state_dir():
    return (os.environ.get("EZ2_COPILOT_SESSION_STATE_DIR")
            or os.path.expanduser("~/.copilot/session-state"))


def latest_model_change(uuid, base=None):
    """回 (newModel, timestamp) 或 None。只讀 events.jsonl 尾巴,從最後一列往回找。

    ★ 10-01 現機實證:w12 copilot 席 09:17:03 從 gemini-3.8-flash 切回 kimi-k3,
      events.jsonl 當下就有 `session.model_change`;用量表最新一列仍是 09:16:32 的
      gemini-3.8-flash ⇒ 只讀用量表的右欄會一直顯示舊模型,直到下一回合。
    """
    if not _UUID_RE.fullmatch(str(uuid or "")):
        return None
    path = os.path.join(base or state_dir(), uuid, "events.jsonl")
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - EVENTS_TAIL_BYTES))
            blob = fh.read()
    except OSError:
        return None
    for raw in reversed(blob.split(b"\n")):
        if b"session.model_change" not in raw:
            continue
        try:
            event = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if not isinstance(event, dict) or event.get("type") != "session.model_change":
            continue
        data = event.get("data")
        model = data.get("newModel") if isinstance(data, dict) else None
        if isinstance(model, str) and model.strip():
            return model.strip(), event.get("timestamp")
    return None


USAGE_EVENT = "session.usage_record"
MAIN_INTERACTION = "conversation-agent"     # 主對話;子代理 / 起標題那種旁支呼叫不算這一席的用量


def latest_usage(uuid, base=None):
    """回 (inputTokens, maxPromptTokens, model, timestamp) 或 None。只讀 events.jsonl 尾巴,
    從最後一列往回找第一列主對話的 `session.usage_record`;兩個數字缺一個、上限不是正數都不算。"""
    if not _UUID_RE.fullmatch(str(uuid or "")):
        return None
    path = os.path.join(base or state_dir(), uuid, "events.jsonl")
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - EVENTS_TAIL_BYTES))
            blob = fh.read()
    except OSError:
        return None
    for raw in reversed(blob.split(b"\n")):
        if USAGE_EVENT.encode() not in raw:
            continue
        try:
            event = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if not isinstance(event, dict) or event.get("type") != USAGE_EVENT:
            continue
        data = event.get("data")
        usage = data.get("usage") if isinstance(data, dict) else None
        if not isinstance(usage, dict) or usage.get("interactionType") != MAIN_INTERACTION:
            continue
        used, cap, model = usage.get("inputTokens"), usage.get("maxPromptTokens"), usage.get("model")
        if (isinstance(used, int) and not isinstance(used, bool) and used >= 0
                and isinstance(cap, int) and not isinstance(cap, bool) and cap > 0
                and isinstance(model, str) and model.strip()):
            return used, cap, model.strip(), event.get("timestamp")
        return None                 # 最新一列主對話量不到 ⇒ 不往更舊的借
    return None


def _when(stamp):
    """ISO 時間 → 可比較字串(copilot 兩處都寫 `…T…Z`;`datetime('now')` 的空白也收)。"""
    return str(stamp or "").replace(" ", "T")[:23]


def _observed_at(created_at):
    """copilot 寫 ISO(`…Z`);解不出就用現在(UTC)—— 那是退路,不是常態。"""
    if isinstance(created_at, str):
        try:
            parsed = time.strptime(created_at[:19], "%Y-%m-%dT%H:%M:%S")
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", parsed)
        except ValueError:
            pass
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def gauge_from_turn(uuid, model, created_at, source=SOURCE, used=None, size=None):
    pct = None
    if used is not None and size:
        pct = int(round(min(100.0, max(0.0, used * 100.0 / size))))
    return {
        "schema": _gauge.SCHEMA,
        "gauge_version": _gauge.GAUGE_VERSION,
        "engine": "copilot",
        "session_id": uuid,
        "model": model,
        "pct": pct,
        "used": used if pct is not None else None,
        "size": size if pct is not None else None,
        "tier": _gauge._tier(pct),
        "authority": "provider_observed" if pct is not None else "unknown",
        "source": source,
        "observed_at": _observed_at(created_at),
    }


def refresh(uuid, ctx_dir=None, db=None, events_dir=None):
    """讀最近一回合與最近一次換模型,時間較新的那個寫進 `$EZ2_CTX_DIR/<uuid>.pct`;
    同一個模型的主對話用量量得到就一併寫 pct。回 model 或 None。"""
    turn = latest_turn(uuid, db=db)
    change = latest_model_change(uuid, base=events_dir)
    source = SOURCE
    if change is not None and (turn is None or _when(change[1]) > _when(turn[1])):
        turn, source = change, SOURCE_CHANGE
    if turn is None:
        return None
    if ctx_dir is None:
        ctx_dir = os.environ.get("EZ2_CTX_DIR") or os.path.expanduser("~/.ez2/ctx")
    if not isinstance(ctx_dir, str) or not os.path.isabs(ctx_dir):
        return None
    used = size = None
    usage = latest_usage(uuid, base=events_dir)
    if usage is not None and usage[2] == turn[0]:      # 換了模型還沒跑下一回合 ⇒ 舊模型的數字不套給新模型
        used, size = usage[0], usage[1]
    record = gauge_from_turn(uuid, turn[0], turn[1], source=source, used=used, size=size)
    dest = os.path.join(ctx_dir, uuid + ".pct")
    try:
        _gauge._atomic_write(dest, json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return record["model"]


if __name__ == "__main__":
    print(refresh(sys.argv[1]) if len(sys.argv) > 1 else "用法:copilot_ctx_probe.py <uuid>")
