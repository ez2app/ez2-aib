#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Antigravity(agy)模型探針:從 agy 自己的行程日誌讀「現在選的模型」,寫進艦隊 `.pct` 量尺。

★ 為什麼要有這一支(10-01 the maintainer:gemini 席換模型後右欄不變,現機量出來的):
  · agy 的 statusline 寫端 9-21 之後就沒再寫過 `.pct`(`~/.ez2/ctx` 裡 agy 最新一份是
    09-21 11:58),活席 w14/w4 兩席**根本沒有 `.pct`** ⇒ 右欄永遠畫開席要求的
    `gemini-3.1-pro-high ◌`,換成什麼模型都不會變。
  · agy 換模型(或換對話)時會**另開一個新對話 id**:w14 席位檔登記 0dac0576,
    15:21:31 換成 Gemini 3.8 Flash 的同一刻日誌寫 `Created conversation 1b886b7c`。
    以對話 id 為鍵去找的讀端,換模型後永遠找不到。
  · 每個 agy 行程寫自己一份 `~/.gemini/antigravity-cli/log/cli-<開始時間>.log`,
    每一回合都印一行
      `Propagating selected model override to backend: label="Gemini 3.8 Flash (Medium)"`
    ⇒ **找到提過席位登記 uuid 的那份日誌(同一個行程),取最後一行 label** 就是現用模型。

★ pct 寫 None(與 copilot 探針同一條紀律):日誌沒有 context 用量,不編百分比。
  若同一個 uuid 已經有一份帶實測 pct 的 `.pct`(statusline 哪天又寫了),只換掉 model,
  其餘欄位原樣保留。model 沒變就不寫(不刷新 mtime,不讓凍住的量尺看起來像新的)。
★ 失敗(找不到日誌、沒有那一行、壞 uuid)保持現況:不刪既有 `.pct`、不丟例外。
"""

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grok_ctx_probe as _gauge          # noqa: E402  SCHEMA / GAUGE_VERSION / _atomic_write 單源

SOURCE = "agy_cli_log"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_LABEL_NEEDLE = b'Propagating selected model override to backend: label="'
#: 只看最近被寫過的這麼多份日誌(活行程每幾分鐘就會寫一行,一定排在前面)。
MAX_LOGS = 8
#: 單份日誌超過這個大小就不讀(現查 23 份最大 215KB)。
MAX_LOG_BYTES = 8 * 1024 * 1024


def log_dir():
    return (os.environ.get("EZ2_AGY_LOG_DIR")
            or os.path.expanduser("~/.gemini/antigravity-cli/log"))


def _recent_logs(base):
    try:
        names = [n for n in os.listdir(base) if n.endswith(".log")]
    except OSError:
        return []
    rows = []
    for name in names:
        path = os.path.join(base, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_size <= MAX_LOG_BYTES:
            rows.append((st.st_mtime, path))
    rows.sort(reverse=True)
    return [p for _m, p in rows[:MAX_LOGS]]


def current_label(uuid, base=None):
    """回 (label, 日誌 mtime) 或 None。只讀。"""
    if not _UUID_RE.fullmatch(str(uuid or "")):
        return None
    # 只認 agy 自己的對話行(`Created conversation <uuid>` / `found conversation <uuid>`):
    # 別席的工具指令文字(例如 `cat ~/.ez2/ctx/<uuid>.pct`)也會被印進別的行程的日誌,
    # 只比 uuid 字面會認錯行程。
    keys = (b"Created conversation " + uuid.encode("ascii"),
            b"found conversation " + uuid.encode("ascii"))
    for path in _recent_logs(base or log_dir()):
        try:
            with open(path, "rb") as fh:
                blob = fh.read()
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if not any(k in blob for k in keys):
            continue
        at = blob.rfind(_LABEL_NEEDLE)
        if at < 0:
            return None
        start = at + len(_LABEL_NEEDLE)
        end = blob.find(b'"', start)
        if end < 0:
            return None
        label = blob[start:end].decode("utf-8", "replace").strip()
        return (label, mtime) if label else None
    return None


def refresh(uuid, ctx_dir=None, log_base=None):
    """寫 `$EZ2_CTX_DIR/<uuid>.pct` 的 model;回 model 或 None。"""
    found = current_label(uuid, base=log_base)
    if found is None:
        return None
    label, mtime = found
    if ctx_dir is None:
        ctx_dir = os.environ.get("EZ2_CTX_DIR") or os.path.expanduser("~/.ez2/ctx")
    if not isinstance(ctx_dir, str) or not os.path.isabs(ctx_dir):
        return None
    dest = os.path.join(ctx_dir, uuid + ".pct")
    existing = None
    try:
        with open(dest, encoding="utf-8") as fh:
            existing = json.load(fh)
    except (OSError, ValueError):
        existing = None
    if isinstance(existing, dict) and existing.get("model") == label:
        return label
    if (isinstance(existing, dict) and existing.get("source") != SOURCE
            and isinstance(existing.get("pct"), (int, float))
            and not isinstance(existing.get("pct"), bool)):
        record = dict(existing)
        record["model"] = label
        record["model_source"] = SOURCE
    else:
        record = {
            "schema": _gauge.SCHEMA,
            "gauge_version": _gauge.GAUGE_VERSION,
            "engine": "agy",
            "session_id": uuid,
            "model": label,
            "pct": None,
            "used": None,
            "size": None,
            "tier": "unknown",
            "authority": "unknown",
            "source": SOURCE,
            "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime)),
        }
    try:
        _gauge._atomic_write(dest, json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return label


if __name__ == "__main__":
    print(refresh(sys.argv[1]) if len(sys.argv) > 1 else "用法:agy_model_probe.py <uuid>")
