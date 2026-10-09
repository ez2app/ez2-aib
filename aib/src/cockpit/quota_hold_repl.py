#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""額度用完時的保活對話席。

provider-launch 在 7d 窗確定用盡時若直接 exit 7,tmux pane 變死窗,
AIB 訊息輸入區整塊顯示 Pane is dead,the maintainer 要求「沒額度也要能對話」。
本 REPL 不起真 CLI,但保持 stdin/stdout 活著,收字並回執,讓私訊/輸入區仍可用。
"""
from __future__ import annotations

import argparse
import os
import sys
import time


def _user_home():
    # 不和艙家讀在同一個函式:家目錄不是艙變數,換艙時不能清掉。
    return os.environ.get("HOME") or ""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="quota_hold_repl")
    ap.add_argument("--provider", required=True)
    ap.add_argument("--why", default="")
    args = ap.parse_args(argv)

    provider = args.provider
    why = args.why or "額度已用完"
    banner = [
        "",
        "═" * 48,
        "AIB 額度席（可對話／排隊）",
        "引擎: %s" % provider,
        "原因: %s" % why,
        "真 CLI 未啟動（避免白工）。此窗保持活著，打字會被收下。",
        "額度回來後：右鍵重開此席即可接回真引擎。",
        "指令: /quit 結束保活",
        "═" * 48,
        "",
    ]
    sys.stdout.write("\n".join(banner))
    sys.stdout.flush()

    stamp_root = os.environ.get("EZ2_AIB_INSTANCE_HOME") or _user_home() or ""
    if stamp_root:
        try:
            path = os.path.join(stamp_root, ".ez2-aib", "quota-hold-%s.stamp" % provider)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("%s\t%s\t%d\n" % (provider, why, int(time.time())))
        except OSError:
            pass

    while True:
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            sys.stdout.write("\n[額度席] 中斷\n")
            sys.stdout.flush()
            return 0
        if line == "":
            return 0
        text = line.rstrip("\r\n")
        if text in ("/quit", "/exit", "/q"):
            sys.stdout.write("[額度席] 結束保活\n")
            sys.stdout.flush()
            return 0
        if not text.strip():
            continue
        sys.stdout.write("[待額度重置] %s\n" % text)
        sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
