#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""把停在「輸入歷史」的 Claude 輸入框退回空框(10-14 交接單 K8;@977e3 21:05 真的碰到)。

    composer_history.py leave --socket S --pane P [--provider claude] [--tmux BIN] [--max 60]
      rc 0 = 不在歷史裡(本來就不是,或已退出);印 history=no / history=left downs=N
      rc 3 = 畫面在動(有人正在操作那一席),一個鍵都沒按;印 history=busy
      rc 4 = 按到上限還在歷史裡;印 history=stuck downs=N 與原因

為什麼:同一目錄的 Claude 席共用輸入歷史。按了 ↑ 之後框頂線是 `─── History n/m ───`,框裡是別席的舊訊息,
aib dm 會把它當草稿擋下;以前只能自己 tmux send-keys Down 按 40 次。
怎麼退:**只按 ↓**(歷史裡往新的方向走,不會送出、不會改字),每按一次重讀畫面,框頂線不再寫 History 就停。
次數不能從 n/m 算:多行的那一筆每一行都要按一次(現量 Claude Code v2.1.299:99/100 那筆按到第 3 次才回空框)。
不按的情況:按之前先讀兩次畫面,兩次不同 = 有人在操作 ⇒ 一個鍵都不按,交給擋下訊息說原因。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import composer_state  # noqa: E402

STILL_GAP_S = 0.4      # 按之前兩次讀畫面的間隔
AFTER_KEY_S = 0.25     # 每按一次 ↓ 之後等畫面重畫
MAX_DOWNS = 60         # 交接單記的手動解法是按 40 次;留一點餘裕


def leave(capture, send_down, provider="claude", max_downs=MAX_DOWNS, sleep=time.sleep):
    """→ (rc, 說明)。capture() 回畫面(capture-pane -e)或 None;send_down() 按一次 ↓。"""
    first = capture()
    if first is None:
        return 4, "history=unknown 讀不到畫面"
    if not composer_state.looks_history(first, provider=provider):
        return 0, "history=no"
    sleep(STILL_GAP_S)
    if capture() != first:
        return 3, "history=busy 畫面在動(有人正在操作那一席),沒有按任何鍵"
    for n in range(1, max_downs + 1):
        send_down()
        sleep(AFTER_KEY_S)
        now = capture()
        if now is not None and not composer_state.looks_history(now, provider=provider):
            return 0, "history=left downs=%d" % n
    return 4, "history=stuck downs=%d 按了 %d 次 ↓ 框頂線還是 History n/m" % (max_downs, max_downs)


def _tmux_fns(tmux, socket, pane):
    env = dict(os.environ)
    env.pop("TMUX", None)

    def capture():
        p = subprocess.run([tmux, "-L", socket, "capture-pane", "-e", "-p", "-t", pane], capture_output=True,
                           text=True, env=env, stdin=subprocess.DEVNULL)
        return p.stdout if p.returncode == 0 else None

    def send_down():
        subprocess.run([tmux, "-L", socket, "send-keys", "-t", pane, "Down"], capture_output=True, env=env,
                       stdin=subprocess.DEVNULL)
    return capture, send_down


def main(argv=None):
    ap = argparse.ArgumentParser(prog="composer_history.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lv = sub.add_parser("leave")
    lv.add_argument("--socket", required=True)
    lv.add_argument("--pane", required=True)
    lv.add_argument("--provider", default="claude")
    lv.add_argument("--tmux", default="tmux")
    lv.add_argument("--max", type=int, default=MAX_DOWNS)
    a = ap.parse_args(argv)
    capture, send_down = _tmux_fns(a.tmux, a.socket, a.pane)
    rc, why = leave(capture, send_down, provider=a.provider or "claude", max_downs=max(1, a.max))
    print(why)
    return rc


if __name__ == "__main__":
    sys.exit(main())
