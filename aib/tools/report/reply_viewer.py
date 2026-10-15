#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib-reply 彈出畫面的「看回報」那一段(10-08 the maintainer):流光播完後接手同一個 popup。

- 右上角 [X 關閉]:任何時候點它(或按 q / Esc / x)就關。
- 最上方倒數 30 秒,到 0 自動關。
- 使用者一上下捲動(滾輪、↑↓、PgUp/PgDn、j/k、空白鍵)⇒ 倒數取消,畫面留著,要關按 X。
判斷邏輯(ViewerState)不碰終端,可單獨測;畫面用 curses。
用法:python3 tools/report/reply_viewer.py [回報檔] [--seconds 30]
不給回報檔 ⇒ 讀 ~/.ez2-aib/state/aib-report/latest.plain.txt(跟 aib-report.py --replay 同一份;popup 指令裡不放檔名,
檔名帶席位抬頭、可能有引號或換行)。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time
import unicodedata

_here = pathlib.Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
import i18n_lib  # noqa: E402

LOC = i18n_lib.resolve_locale()
DEFAULT_SECONDS = 30


def T(key, params=None):
    return i18n_lib.t(key, params, locale=LOC)


def cell_width(text):
    """終端欄寬:全形/寬字算 2 欄。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def clip(text, width):
    out, used = [], 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + w > width:
            break
        out.append(ch)
        used += w
    return "".join(out)


class ViewerState:
    """倒數、捲動、關閉的判斷;不碰終端。"""

    def __init__(self, lines, body_height, seconds=DEFAULT_SECONDS):
        self.lines = list(lines)
        self.body_height = max(1, body_height)
        self.top = 0
        self.remaining = float(seconds)
        self.cancelled = False
        self.closed = False

    @property
    def max_top(self):
        return max(0, len(self.lines) - self.body_height)

    def scroll(self, delta):
        """上下移動一律取消倒數(就算已經在頂/底、畫面沒動也算 —— 使用者的意思是「我要看」)。"""
        self.cancelled = True
        self.top = min(self.max_top, max(0, self.top + delta))

    def tick(self, dt):
        if self.closed or self.cancelled:
            return
        self.remaining = max(0.0, self.remaining - dt)
        if self.remaining <= 0:
            self.closed = True

    def close(self):
        self.closed = True

    def header(self, width):
        """(左邊文字, 關閉鈕文字, 關閉鈕起始欄)。關閉鈕貼右上角。"""
        if self.cancelled:
            left = T("aib.report.viewer.paused")
        else:
            left = T("aib.report.viewer.countdown", {"n": int(self.remaining + 0.999)})
        btn = T("aib.report.viewer.close")
        btn_x = max(0, width - cell_width(btn) - 1)          # 留最後一欄不寫(curses 寫最後一格會丟例外)
        return clip(left, max(0, btn_x - 1)), btn, btn_x

    def hit_close(self, x, y, width):
        if y != 0:
            return False
        _left, btn, btn_x = self.header(width)
        return btn_x <= x < btn_x + cell_width(btn)

    def visible(self):
        return self.lines[self.top:self.top + self.body_height]


def _run(stdscr, state):
    import curses
    curses.curs_set(0)
    stdscr.timeout(100)
    stdscr.keypad(True)
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    curses.mouseinterval(0)
    wheel_up = getattr(curses, "BUTTON4_PRESSED", 0)
    wheel_down = getattr(curses, "BUTTON5_PRESSED", 0x200000)
    clicks = (curses.BUTTON1_PRESSED | curses.BUTTON1_RELEASED | curses.BUTTON1_CLICKED)
    last = time.monotonic()
    while not state.closed:
        h, w = stdscr.getmaxyx()
        state.body_height = max(1, h - 2)
        stdscr.erase()
        left, btn, btn_x = state.header(w)
        try:
            stdscr.addstr(0, 0, left, curses.A_BOLD)
            stdscr.addstr(0, btn_x, btn, curses.A_REVERSE | curses.A_BOLD)
            stdscr.hline(1, 0, curses.ACS_HLINE, max(0, w - 1))
            for i, line in enumerate(state.visible()):
                stdscr.addstr(2 + i, 0, clip(line, max(0, w - 1)))
        except curses.error:
            pass
        stdscr.refresh()
        key = stdscr.getch()
        now = time.monotonic()
        state.tick(now - last)
        last = now
        if key == -1:
            continue
        if key == curses.KEY_MOUSE:
            try:
                _id, mx, my, _z, bstate = curses.getmouse()
            except curses.error:
                continue
            if bstate & wheel_up:
                state.scroll(-3)
            elif bstate & wheel_down:
                state.scroll(3)
            elif bstate & clicks and state.hit_close(mx, my, w):
                state.close()
        elif key in (curses.KEY_UP, ord("k")):
            state.scroll(-1)
        elif key in (curses.KEY_DOWN, ord("j")):
            state.scroll(1)
        elif key in (curses.KEY_PPAGE,):
            state.scroll(-state.body_height)
        elif key in (curses.KEY_NPAGE, ord(" ")):
            state.scroll(state.body_height)
        elif key in (ord("q"), ord("x"), ord("X"), 27):
            state.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="reply_viewer")
    ap.add_argument("report", nargs="?",
                    default=str(pathlib.Path.home() / ".ez2-aib" / "state" / "aib-report" / "latest.plain.txt"))
    ap.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    a = ap.parse_args(argv)
    try:
        lines = pathlib.Path(a.report).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print("reply_viewer: %s" % exc, file=sys.stderr)
        return 2
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return 0                                           # 不是終端(測試、管線)⇒ 不開畫面
    import curses
    state = ViewerState(lines, 20, a.seconds)
    mark = viewer_mark_path()
    try:
        if mark is not None:
            mark.parent.mkdir(parents=True, exist_ok=True)
            mark.write_text("%d\n" % os.getpid(), encoding="utf-8")
        curses.wrapper(_run, state)
    finally:
        if mark is not None:
            try:
                if mark.read_text(encoding="utf-8").strip() == str(os.getpid()):
                    mark.unlink()
            except OSError:
                pass
    return 0


def viewer_mark_path(environ=None):
    """「看回報畫面開著」的記號:外層 scroll-hub-wheel.sh 看到它就把滾輪改成 ↑/↓ 鍵送進來(外層不轉送滑鼠給內層)。
    popup 開在內層 hub,TMUX 指的就是那台 server;名字取 socket 檔名(aihub-<艙>)。"""
    env = os.environ if environ is None else environ
    sock = (env.get("TMUX") or "").split(",")[0]
    name = os.path.basename(sock)
    if not name.startswith("aihub-"):
        return None
    root = env.get("AIB_REPLY_VIEWER_DIR") or str(pathlib.Path.home() / ".ez2-aib" / "state" / "aib-report")
    return pathlib.Path(root) / ("viewer-open-" + name)


if __name__ == "__main__":
    sys.exit(main())
