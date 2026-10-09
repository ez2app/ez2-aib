#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""API 席(deepseek / nvidia / kiosapi / openrouter / teamo / teamoclaude)的模型寫端。

★ 為什麼要有這一支(10-01 the maintainer:nvidia 席 `/model` 換模型後右欄不變):
  API 席的現用模型**只活在 REPL 行程的一個區域變數裡**(`/model <id>` 只改那個變數),
  沒有任何檔案記它,`~/.ez2/ctx` 也從來沒有 API 席的 `.pct` ⇒ 右欄永遠畫開席要求的
  模型加 `◌`,換成什麼都不會變。
  這些 REPL 自己就是送請求的那一端:它手上的模型就是下一回合真的送出去的模型。
  所以在**開 REPL 時**與**每次 `/model` 切換時**把它寫進同一把量尺。
  ★ 開 REPL 時也要寫:同一個 session 重開時若只在切換時寫,上一個行程切過的模型
    會一直留在 `.pct`,畫面顯示一個這個行程根本沒在用的模型。

★ pct 寫 None(與 copilot 探針同一條紀律):這裡沒有 context 用量,不編百分比。
★ 失敗一律吞掉:畫面資料寫不進去不能讓 REPL 掛掉。
"""

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grok_ctx_probe as _gauge          # noqa: E402  SCHEMA / GAUGE_VERSION / _atomic_write 單源

SOURCE = "api_cli_selected"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def record(session_id, model, engine, ctx_dir=None):
    """把這一席現在送出去的模型寫進 `$EZ2_CTX_DIR/<session_id>.pct`;回 True/False。"""
    if not _UUID_RE.fullmatch(str(session_id or "")):
        return False
    if not isinstance(model, str) or not model.strip():
        return False
    if ctx_dir is None:
        ctx_dir = os.environ.get("EZ2_CTX_DIR") or os.path.expanduser("~/.ez2/ctx")
    if not isinstance(ctx_dir, str) or not os.path.isabs(ctx_dir):
        return False
    row = {
        "schema": _gauge.SCHEMA,
        "gauge_version": _gauge.GAUGE_VERSION,
        "engine": str(engine or "api"),
        "session_id": session_id,
        "model": model.strip(),
        "pct": None,
        "used": None,
        "size": None,
        "tier": "unknown",
        "authority": "unknown",
        "source": SOURCE,
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        _gauge._atomic_write(os.path.join(ctx_dir, session_id + ".pct"),
                             json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return False
    return True
