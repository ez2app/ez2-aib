#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""A6(2026-09-24,大綱 §101.4 第 3 波):dm 送進 codexbot 席時,收據要說實話。

queue 模式的席收到 dm 後只是把它排進桌面共用佇列,要等桌面持有者閒下來才執行;桌面關著就無限等。
以前 dm-ctl 貼進 pane 成功就回 delivered,寄件者以為對方已經在處理。這支給 dm-ctl 問:「這席的橋現在是什麼模式?」
答案來自橋自己寫的 <state root>/bridge/<席 session uuid>/status.json 的 mode 欄(橋每次 save 都寫)。
用法:python3 codexbot_receipt.py <session_uuid>  ⇒ 印 queue|writer|(空=不是橋、或讀不到)。永遠 rc 0。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def bridge_mode(session_uuid: str, base=None) -> str:
    """回 status.json 的 mode(queue|writer);沒有橋狀態檔、讀不到、沒 mode 欄 ⇒ ''(量不到不當 queue,也不當 writer)。"""
    if not session_uuid:
        return ""
    base = Path(base or os.environ.get("EZ2_AIB_STATE_ROOT") or os.environ.get("EZ2_AIB_HOME") or Path.home() / ".ez2-aib")
    try:
        data = json.loads((base / "bridge" / session_uuid / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    mode = data.get("mode") if isinstance(data, dict) else None
    return mode if mode in ("queue", "writer") else ""


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    print(bridge_mode(argv[0] if argv else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
