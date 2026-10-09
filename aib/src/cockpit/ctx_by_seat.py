#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""登記席位 -> /clear 後現任 session 的單一讀端。

缺陷四十一以前用 ``by-pane/<pane>.json`` 當換源鍵。pane id 只在一個
tmux server 內唯一；兩艙同號 pane 會互相覆寫，甚至把 Codex 席的模型名
換成另一艙 Claude 的模型。新鍵固定為登記 ``session_uuid`` 的末五碼：

    $EZ2_CTX_DIR/by-seat/<五碼>.json

寫者仍是該席的 statusline hook，但記錄必須同時帶 instance 與 window。
讀者只採本艙、本窗、同一登記五碼的合法記錄；任一項量不到都保留登記
session。舊 by-pane 目錄刻意不在本模組出現，資料留待後續清理。
"""

import calendar
import json
import os
import re
import stat
import time

from seat_address import uuid_tail5


SCHEMA = "ez2-ctx-by-seat-v1"
BY_SEAT_DIR = "by-seat"
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_CODE_RE = re.compile(r"[0-9a-f]{5}")
_INSTANCE_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,127}")
_WINDOW_RE = re.compile(r"@[0-9]{1,10}")
_UTC_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z")


def ctx_dir(root=None):
    return root or os.environ.get("EZ2_CTX_DIR") \
        or os.path.join(os.path.expanduser("~"), ".ez2", "ctx")


def instance_key(instance=None):
    """正規化本艙鍵；顯式值優先，其次由已綁定 socket 導出。"""
    value = instance or os.environ.get("EZ2_AIB_INSTANCE")
    if not value:
        socket = os.environ.get("EZ2_AIHUB_SOCKET") or ""
        if socket == "aihub":
            value = "main"
        elif socket.startswith("aihub-"):
            value = socket[len("aihub-"):]
    value = str(value or "").strip()
    return value if _INSTANCE_RE.fullmatch(value) else None


def seat_key(registered):
    if not isinstance(registered, str) or not _UUID_RE.fullmatch(registered):
        return None
    code = uuid_tail5(registered)
    return code if isinstance(code, str) and _CODE_RE.fullmatch(code) else None


def by_seat_path(registered, root=None):
    code = seat_key(registered)
    if code is None:
        return None
    return os.path.join(ctx_dir(root), BY_SEAT_DIR, code + ".json")


def _read_json(path):
    if not path:
        return None
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            fd = -1
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    finally:
        if fd >= 0:
            os.close(fd)
    return data if isinstance(data, dict) else None


def parse_observed_utc(text):
    if not isinstance(text, str) or not _UTC_RE.fullmatch(text):
        return None
    try:
        whole = int(calendar.timegm(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S"))) * 1000
        fraction = text[20:-1] if len(text) > 20 else ""
        return whole + int((fraction + "000")[:3] or "0")
    except (OverflowError, ValueError):
        return None


def registered_observed_ms(registered, root=None):
    if not isinstance(registered, str) or not _UUID_RE.fullmatch(registered):
        return None
    data = _read_json(os.path.join(ctx_dir(root), registered + ".pct"))
    return parse_observed_utc(data.get("observed_at")) if data else None


def live_record(registered, root=None, instance=None, window=None):
    """回 (record, note)；無法證明同艙同席時 record=None。"""
    code = seat_key(registered)
    if code is None:
        return None, "registered session uuid invalid: by-seat ignored"
    mine = instance_key(instance)
    if mine is None:
        return None, "current instance unavailable: by-seat %s ignored" % code
    if not isinstance(window, str) or not _WINDOW_RE.fullmatch(window):
        return None, "current window unavailable: by-seat %s ignored" % code
    path = by_seat_path(registered, root)
    data = _read_json(path)
    if data is None:
        return None, "by-seat %s missing or unreadable: registered session retained" % code
    if data.get("schema") != SCHEMA:
        return None, "by-seat %s schema invalid: registered session retained" % code
    if data.get("seat") != code:
        return None, "by-seat %s content seat %r mismatches: registered session retained" \
            % (code, data.get("seat"))
    if data.get("instance") != mine:
        return None, "by-seat %s belongs to instance %r, not %s: registered session retained" \
            % (code, data.get("instance"), mine)
    live_window = data.get("window")
    if not isinstance(live_window, str) or not _WINDOW_RE.fullmatch(live_window):
        return None, "by-seat %s window invalid: registered session retained" % code
    if live_window != window:
        return None, "by-seat %s belongs to window %s, not %s: registered session retained" \
            % (code, live_window, window)
    live = data.get("session_id")
    if not isinstance(live, str) or not _UUID_RE.fullmatch(live):
        return None, "by-seat %s session_id invalid: registered session retained" % code
    observed_ms = parse_observed_utc(data.get("observed_at"))
    if observed_ms is None:
        return None, "by-seat %s observed_at invalid: registered session retained" % code
    pid = data.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None, "by-seat %s pid invalid: registered session retained" % code
    out = dict(data)
    out["observed_ms"] = observed_ms
    return out, None


def bridge_present(registered, ctx_root=None, instance=None, window=None):
    """這一席**有沒有**可用的 by-seat 橋。純查詢,不改任何既有回傳。

    ★ 09-07:`rebound=False` 有兩種成因,而它們的處置**相反**:
      (a) `live == registered` —— uuid 就是現任,`.pct` 是新的 ⇒ 那個數字可信。
      (b) `rec is None` —— 拿不到橋(那一席 clear 之後沒再動過,statusline 沒重跑),
          於是退回席位檔登記的 uuid,而它的 `.pct` **凍在換對話 id 之前**。
      兩者都回 False,於是畫面上「凍住的 72%」與「真的是 72%」逐字相同。
      the maintainer 09-07 回報的正是這個:兩席 clear 兩小時,右欄還顯示 72%/68%。
    ⇒ 這一支只回答 (b) 那一問,讓讀端能把「待更新」與「真值」分開畫。
    """
    rec, _note = live_record(registered, ctx_root, instance, window)
    return rec is not None


def resolve_live_session(registered, ctx_root=None, instance=None, window=None,
                         since_ms=None):
    """回 (session_uuid, rebound, note)，失效側永遠保留 registered。"""
    rec, note = live_record(registered, ctx_root, instance, window)
    if rec is None:
        return registered, False, note
    live = rec["session_id"]
    if live == registered:
        return registered, False, None
    floor_ms = registered_observed_ms(registered, ctx_root)
    if floor_ms is None:
        floor_ms = -1
    if isinstance(since_ms, (int, float)) and since_ms > floor_ms:
        floor_ms = since_ms
    if rec["observed_ms"] <= floor_ms:
        return registered, False, (
            "by-seat %s observation is not newer: registered session retained"
            % rec["seat"])
    return live, True, (
        "session rebound by seat %s: %s -> %s"
        % (rec["seat"], registered[:8], live[:8]))
