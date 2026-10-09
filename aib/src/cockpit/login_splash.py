#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開席像素動畫:logo 由左而右一顆顆出現,下方 Ez2 進度與句子跟著推進。

五家都亮完才進入「聯結 / 接入」。艙若先就緒,父行程刪旗標後本程序仍把剩下
的顆依原速跑完再結束,不殺到一半,也不另外再睡滿三秒。
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time

DRAWN = 0
STEP_S = 0.36
# 五家都是 5 列同寬。自動換行關掉,避免方塊字把畫面疊高。
# Claude/Codex/Copilot:本機 JetBrainsMono Nerd Font 官方標
# (U+EC82/U+EC81/U+EC1E,與右欄徽章同一套)掃成五列色塊。
# xAI:就是一個 X。AGY:`agy` 進艙五列。顏色只走 256 色。
AGY_ROW = (220, 208, 77, 39, 33)
NF_CLAUDE = "\uec82"
NF_CODEX = "\uec81"
NF_COPILOT = "\uec1e"
HOUSES = (
    ("Claude", 208, (
        "    █▄ ▄▄ ▄▄  ",
        "  █▄██▄████▀  ",
        "    █████████ ",
        "  ▄▀███████▄  ",
        "    ▀ █▀ ▀▀   ",
    )),
    ("OpenAI", 36, (
        "   ▄▀▀██▄▄▄▄  ",
        " ▄██ █▄▄█▀▄██ ",
        "▀▄ ███  ███ ▀▄",
        " ██▀▄█▀▀█ ██▀ ",
        "  ▀▀▀▀██▄▄▀   ",
    )),
    ("xAI", 15, (
        "██▀        ▀██",
        " ▀██      ██▀ ",
        "   ▀██████▀   ",
        " ▄██      ██▄ ",
        "██▄        ▄██",
    )),
    ("Copilot", 33, (
        "  ▄████████▄  ",
        "  █  ▄██▄  █  ",
        "▄██▀▀▀▀▀▀▀▀██▄",
        "███  █  █  ███",
        "  ▀▀██████▀▀  ",
    )),
    ("Antigravity", AGY_ROW, (
        "      ▄▀▀▄    ",
        "     ▀▀▀▀▀▀   ",
        "    ▀▀▀▀▀▀▀▀  ",
        "   ▄▀▀    ▀▀▄ ",
        "  ▄▀▀      ▀▀▄",
    )),
)
LABELS = (
    NF_CLAUDE + " Claude",
    NF_CODEX + " OpenAI",
    " xAI",
    NF_COPILOT + " Copilot",
    "AGY",
)
ART_ROWS = 5
TEXTS = (
    "正在迎入 Claude…",
    "正在迎入 OpenAI…",
    "正在迎入 xAI…",
    "正在迎入 Copilot…",
    "正在迎入 Antigravity…",
)
READY = "準備迎入五家 AI…"
N = len(HOUSES)


def c(n, s):
    return "\033[38;5;%dm%s\033[0m" % (n, s)


def colorize(color, s, row=0):
    """單色整段上色。AGY 傳五個 256 色=一列一色。"""
    if isinstance(color, int):
        return c(color, s)
    if color and isinstance(color[0], int):
        return c(color[row % len(color)], s)
    pal = color[row] if row < len(color) else color[0]
    parts = []
    i = 0
    for ch in s:
        if ch == " ":
            parts.append(" ")
        else:
            parts.append(c(pal[i % len(pal)], ch))
            i += 1
    return "".join(parts)


def paint(lines):
    global DRAWN
    err = sys.stderr
    if DRAWN:
        err.write("\033[%dA\033[J" % DRAWN)
    err.write("\n".join(lines) + "\n")
    err.flush()
    DRAWN = len(lines)


def cleanup(*_a):
    global DRAWN
    err = sys.stderr
    if DRAWN:
        err.write("\033[%dA\033[J" % DRAWN)
        DRAWN = 0
    err.write("\033[?7h\033[?25h")
    err.flush()
    os._exit(0)


def still(flag):
    return os.path.isfile(flag)


def nap(sec):
    """一顆的停留。旗標沒了也要把這一拍睡完,否則剩下的 logo 會一次閃出來。"""
    end = time.monotonic() + sec
    while time.monotonic() < end:
        time.sleep(min(0.04, end - time.monotonic()))


def house_w(art):
    return max(len(line) for line in art)


def house_row(color, art, r, w):
    line = art[r] if r < len(art) else " " * w
    return colorize(color, line.ljust(w), r)


def logos_w():
    return sum(house_w(art) for _n, _c, art in HOUSES) + 2 * (N - 1)


def center(s, width):
    if len(s) >= width:
        return s
    left = (width - len(s)) // 2
    return " " * left + s + " " * (width - len(s) - left)


def center_colored(plain, width, colored):
    if len(plain) >= width:
        return colored
    left = (width - len(plain)) // 2
    return " " * left + colored


def progress(shown):
    """五格各跟上方一顆 logo 同寬,亮幾顆就填幾格。"""
    parts = []
    for i, (_n, _c, art) in enumerate(HOUSES):
        w = house_w(art)
        if i < shown:
            parts.append(c(75, "█" * w))
        else:
            parts.append(c(245, "░" * w))
    return "  ".join(parts) + c(245, "  Ez2 %d/%d" % (shown, N))


def frame(shown, linked):
    rows = []
    for r in range(ART_ROWS):
        cells = []
        for i, (_name, color, art) in enumerate(HOUSES):
            w = house_w(art)
            if i < shown:
                cells.append(house_row(color, art, r, w))
            else:
                cells.append(" " * w)
        rows.append("  ".join(cells))
    lab = []
    for i, (label, (_n, color, art)) in enumerate(zip(LABELS, HOUSES)):
        w = house_w(art)
        if i < shown:
            lab.append(colorize(color, center(label, w)))
        else:
            lab.append(" " * w)
    rows.append("  ".join(lab))
    rows.append(progress(shown))
    if linked:
        bar = "──●────●────●────●──"
        rows.append(center_colored(bar, logos_w(), c(75, bar)))
        rows.append(center_colored(
            "Ez2 聯結完成", logos_w(),
            c(75, "Ez2") + " " + c(245, "聯結完成")))
        rows.append(c(245, "正在接入指揮艙畫面…"))
    elif shown == 0:
        rows.append(c(245, READY))
    else:
        rows.append(c(245, TEXTS[shown - 1]))
    return rows


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--flag", required=True, help="父行程刪掉此檔=序列跑完就結束")
    args = p.parse_args(argv)
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)
    sys.stderr.write("\033[?25l\033[?7l")
    sys.stderr.flush()

    paint(frame(0, False))
    for shown in range(1, N + 1):
        nap(STEP_S)
        paint(frame(shown, False))
    nap(STEP_S)
    paint(frame(N, True))
    while still(args.flag):
        time.sleep(0.05)
    cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
