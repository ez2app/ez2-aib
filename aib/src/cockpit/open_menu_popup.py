#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開席選單彈窗(tmux display-popup -E 裡跑)。the maintainer 2026-09-26:「這個清單必需要有選擇,或倒數完成才能消失」。

tmux 的 display-menu 按 q / Esc / 點外面就關,關了之後本殼還在等 ⇒ 等於沒選卻看不到;逾時後它又殘留在畫面上。
所以改成彈窗跑本程式:
  · 只有「選了」或「倒數到 0」會結束(結束 = 彈窗關閉,-E)
  · Esc / q / Ctrl-C / 其他鍵一律不理(raw 模式,Ctrl-C 只是一個位元組)
  · 選法:按選項鍵、上下鍵 + Enter、滑鼠左鍵點那一行
  · 標題下即時倒數

  open_menu_popup.py <spec.json>
    spec = {"title": str, "deadline": epoch 秒, "choice_file": 路徑, "note": str,
            "items": [{"key": "1", "label": "...", "value": "1", "dim"?: bool} | {"separator": true}, ...]}
  dim=true ⇒ 該列用灰階 SGR 90 畫(已連 Grok Bot agent);選中列仍反白。
  選擇檔用 O_EXCL 建:已存在(別處先落、或逾時被佔住)就不覆寫。
  尺的注入點:AIB_OPEN_MENU_AUTOPICK=<鍵> ⇒ 不畫畫面,直接選那一鍵(沒有 tty 的治具用)。
"""
import json
import os
import re
import select
import sys
import termios
import time
import tty


def write_choice(path, value):
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(value)
    return True


def selectable(items):
    return [i for i, it in enumerate(items) if not it.get("separator")]


def fit(text, cols):
    """依顯示寬度截斷(中日韓全形算 2 格)。不截斷的話終端機自動換行 ⇒ 滑鼠列號對不上選項,
    會點 a 選到 b(09-26 the maintainer 截圖:過長的一列把最左邊擠掉)。"""
    import unicodedata
    out, w = [], 0
    for ch in text:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else (0 if unicodedata.combining(ch) else 1)
        if w + cw > cols - 1:
            out.append("…")
            break
        out.append(ch)
        w += cw
    return "".join(out)


def render(out, spec, items, sel, remaining, cols=80):
    lines = [fit(" " + spec.get("title", "").strip(), cols),
             fit(" 剩 %d 秒 · %s" % (remaining, spec.get("note", "")), cols), ""]
    rows = {}
    for i, it in enumerate(items):
        if it.get("separator"):
            lines.append(" " + "─" * 20)
            continue
        rows[len(lines) + 1] = i                      # 滑鼠列號從 1 起
        text = fit(" [%s] %s" % (it["key"], it["label"]), cols)
        # the maintainer 09-26:已連 agent 灰階(SGR 90 = bright black);選中列仍反白優先。
        if i == sel:
            text = "\x1b[7m%s\x1b[0m" % text
        elif it.get("dim"):
            text = "\x1b[90m%s\x1b[0m" % text
        lines.append(text)
    out.write("\x1b[H\x1b[2J" + "\r\n".join(lines))
    out.flush()
    return rows


MOUSE = re.compile(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])")


def main(argv):
    spec = json.load(open(argv[1], encoding="utf-8"))
    items = spec.get("items") or []
    keys = {it["key"]: it for it in items if not it.get("separator")}
    cf = spec["choice_file"]
    deadline = float(spec["deadline"])
    auto = os.environ.get("AIB_OPEN_MENU_AUTOPICK", "")
    if auto:
        if auto in keys:
            write_choice(cf, keys[auto]["value"])
        return 0
    if not sys.stdin.isatty():                        # 沒有畫面可畫:等到倒數結束(不自己選)
        while time.time() < deadline and not os.path.exists(cf):
            time.sleep(0.2)
        return 0
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    out = sys.stdout
    sel_list = selectable(items)
    sel = sel_list[0] if sel_list else -1
    try:
        tty.setraw(fd)
        out.write("\x1b[?7l\x1b[?25l\x1b[?1000h\x1b[?1006h")      # ?7l 關自動換行(雙保險)
        buf = b""
        while True:
            remaining = deadline - time.time()
            if remaining <= 0 or os.path.exists(cf):
                return 0
            try:
                cols = os.get_terminal_size(fd).columns
            except OSError:
                cols = 80
            rows = render(out, spec, items, sel, int(remaining + 0.999), cols)
            r, _, _ = select.select([fd], [], [], min(1.0, remaining))
            if not r:
                continue
            buf += os.read(fd, 256)
            while buf:
                m = MOUSE.match(buf)
                if m:
                    buf = buf[m.end():]
                    b, y = int(m.group(1)), int(m.group(3))
                    if m.group(4) == b"M" and b == 0 and y in rows:   # 左鍵按下在選項那一行
                        if write_choice(cf, items[rows[y]]["value"]):
                            return 0
                    continue
                if buf.startswith(b"\x1b[A") or buf.startswith(b"\x1b[B"):
                    if sel_list:
                        k = sel_list.index(sel) if sel in sel_list else 0
                        k = (k - 1) % len(sel_list) if buf[2:3] == b"A" else (k + 1) % len(sel_list)
                        sel = sel_list[k]
                    buf = buf[3:]
                    continue
                if buf.startswith(b"\x1b[") and len(buf) < 3:
                    break                                # 序列還沒收完
                ch, buf = buf[:1], buf[1:]
                if ch in (b"\r", b"\n") and sel >= 0:
                    if write_choice(cf, items[sel]["value"]):
                        return 0
                    continue
                try:
                    key = ch.decode("ascii")
                except UnicodeDecodeError:
                    continue
                if key in keys:
                    if write_choice(cf, keys[key]["value"]):
                        return 0
                # 其他(Esc、q、Ctrl-C…)一律不理:只有選了或倒數到 0 才結束
    finally:
        out.write("\x1b[?1006l\x1b[?1000l\x1b[?25h\x1b[?7h")
        out.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
