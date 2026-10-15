#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""`shot_helper.py` —— AIB 截圖小幫手(10-09 the maintainer:「把輸入法裡的截圖加進 aib,啟用 aib 就能用快捷鍵;
aib 一啟用,輸入法的截圖就跳出來讓人手動關掉」)。

## 做什麼
  ensure      開艙時由 cockpit-full.sh 在背景叫:編譯(只在原始碼變了才編)並啟動
              src/shot/hotkey.swift(全 Mac 的 Control+Shift+S),再看輸入法截圖要不要跳視窗問。
  reedit      點縮圖:把那張圖放大進編輯器再改,存回預設送回原來那一席。
  seats       列出活著的 AI 席(存回時「送到」的下拉選單用)。
  alive       有任何活著的 AIB 艙 ⇒ rc=0;一個都沒有 ⇒ rc=1。小幫手每 30 秒問一次,rc=1 就自己結束。
  shoot       框選截圖 → 「Ez2 截圖」編輯器(src/shot/editor,從鼠鬚管安裝包 v3 搬來)→ 存回才進第二大腦。
  status      印小幫手、編譯、輸入法截圖的狀態。
  stop        停小幫手(只照狀態檔裡的 pid 停,而且先確認那個 pid 真的是小幫手)。
  ime-status / ime-off / ime-on   輸入法(鼠鬚管 liu_screenshot.lua)那組快捷鍵的狀態/關/開。

## 為什麼輸入法截圖用「旗標檔」關,不拿掉 liur.custom.yaml 那一行
那一行是 `"engine/processors/@before 0"`(插到處理清單第 0 格)。10-09 實查:拿掉之後,predictor、
predict_space_passthrough、liu_wrap_select 在組好的清單裡會整串挪一格 —— 那三個是 the maintainer 10-08 逐條調好的
Enter/空白鍵/選字行為。所以處理清單一格都不動:ime-off 只在 liu_screenshot.lua 補一行「旗標檔在就不攔鍵」
(第一次補要重開鼠鬚管一次),之後關/開都只是放/刪 ~/.ez2-aib/shot/ime-shot-off,不用重新載入。
候選列的 📷 格不經過這組快捷鍵,照常能用。

## 不做真動作的時候
不是 macOS、AIB_SHOT=0、不是真的座艙(沒有 EZ2_AIB_INSTANCE 或家目錄底下沒有那個艙 —— 測試、截幀、治具)
⇒ ensure 什麼都不做,只印原因(同 cooldown.live_cabin 的判法;10-08 測試跑右欄時真的收過這台的行程)。
`--force` 只給人手動跑。

最後一行永遠是一行 JSON:{"rc", "reason", ...}。
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import glob
import hashlib
import json
import os
import pathlib
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import i18n_lib  # noqa: E402
import dm_address  # noqa: E402
import seat_address  # noqa: E402
import composer_state  # noqa: E402
import seats_dir_lib  # noqa: E402

LOC = i18n_lib.resolve_locale()
REPO = _HERE.parents[1]
SHOT_SRC = REPO / "src" / "shot"
APP_NAME = "Ez2AIBShot.app"
BUNDLE_ID = "com.ez2.aib.shot"
EXE_NAME = "ez2-aib-shot"
LEGACY_SHOT = "A.Ez2/00.Ez2_圖庫/截圖"          # 第二大腦截圖資料夾的舊路徑(與 liu_screenshot.lua 同一個)
GUARD_MARK = "aib_shot_off"                    # liu_screenshot.lua 補過旗標判斷的記號
SQUIRREL_APP = "/Library/Input Methods/Squirrel.app"
TMUX_CANDIDATES = ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux", "/usr/bin/tmux")
INTERVAL_S = 30


def T(key, params=None):
    return i18n_lib.t(key, params, locale=LOC)


def _home(home=None):
    return pathlib.Path(home or os.path.expanduser("~"))


def shot_home(home=None):
    return _home(home) / ".ez2-aib" / "shot"


def rime_dir(home=None):
    return _home(home) / "Library" / "Rime"


def _emit(doc, msg=None):
    if msg:
        print(msg)
    print(json.dumps(doc, ensure_ascii=False))
    return int(doc.get("rc", 0))


def _run(cmd, timeout=60, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=timeout, env=env)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write_json(path, doc):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _log(home, line):
    try:
        d = shot_home(home)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "shot.log", "a", encoding="utf-8") as fh:
            fh.write("%s helper %s\n" % (time.strftime("%H:%M:%S"), line))
    except OSError:
        pass


# ---------- 真座艙判斷(同 cooldown.live_cabin)----------
def live_cabin(environ=None, home=None):
    env = os.environ if environ is None else environ
    inst = (env.get("EZ2_AIB_INSTANCE") or "").strip()
    if not inst or "/" in inst:
        return False
    return (_home(home) / ".ez2-aib" / "instances" / inst).is_dir()


# ---------- 有沒有活著的艙 ----------
def tmux_bin():
    for p in TMUX_CANDIDATES:
        if os.access(p, os.X_OK):
            return p
    return shutil.which("tmux")


def socket_dir(environ=None):
    env = os.environ if environ is None else environ
    base = env.get("TMUX_TMPDIR") or "/tmp"
    return os.path.join(base, "tmux-%d" % os.getuid())


def alive(sock_dir, tmux=None):
    """sock_dir 底下任何一個 aihub* 伺服器還在 ⇒ True。拿不到 tmux ⇒ None(探測錯,不是「沒有艙」)。"""
    tmux = tmux or tmux_bin()
    if not tmux:
        return None
    for sock in sorted(glob.glob(os.path.join(sock_dir, "aihub*"))):
        try:
            if _run([tmux, "-S", sock, "has-session"], timeout=10).returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            continue
    return False


# ---------- 截圖存哪(與 liu_screenshot.lua 同一個資料夾;但 vault 只從環境變數讀,不猜)----------
def vault_root(environ=None):
    env = os.environ if environ is None else environ
    for var in ("EZ2_VAULT_DIR", "EZ2_VAULT_ROOT"):
        v = env.get(var)
        if v:
            return os.path.expanduser(v)
    return None


def resolve_shot_dir(environ=None, home=None):
    env = os.environ if environ is None else environ
    given = env.get("EZ2_AIB_SHOT_DIR")
    if given:
        return os.path.expanduser(given)
    vault = vault_root(env)
    if vault:
        idx = _read_json(os.path.join(vault, ".ez2", "index", "folder-id-index.json")) or {}
        for it in idx.get("items") or []:
            if isinstance(it, dict) and (it.get("legacyPaths") or [None])[0] == LEGACY_SHOT and it.get("folderPath"):
                d = os.path.join(vault, it["folderPath"])
                if os.path.isdir(d):
                    return d
        d = os.path.join(vault, LEGACY_SHOT)
        if os.path.isdir(d):
            return d
    return str(_home(home) / "Desktop")   # the maintainer 10-09:找不到對應的截圖資料夾就存桌面


# ---------- 存回時送到某一席(10-09 the maintainer:「存到 aib 的 ai 對話框裡,要能有預載圖」)----------
# 各家怎麼把圖放進輸入框(10-09 用暫時開的 CLI 實測,都不會送出):
#   claude / codex:圖放進剪貼簿再按 Control+V ⇒ 輸入框出現 [Image #1]
#   grok:Control+V 只打出「.」;用「貼上」(bracketed paste)送檔案路徑 ⇒ 自己變成 [Image #1]
#   其他(沒實測):用「貼上」送一行「📷 截圖:<路徑>」,AI 照路徑讀圖
# bot 席(…bot)窗裡跑的是轉接程式,沒有一般的輸入框 ⇒ 不列。
PASTE_KEY = ("claude", "codex")
PASTE_PATH = ("grok",)
MAIN_SOCKET = "aihub"          # 主艙(席位檔在 ~/.ez2-aib/seats/,艙位碼 main)


def method_of(provider):
    p = (provider or "").lower()
    return "key" if p in PASTE_KEY else "path" if p in PASTE_PATH else "text"


def _tmux_rows(tmux, sock, fmt, *target):
    try:
        p = _run([tmux, "-S", sock] + list(target) + ["-F", fmt], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return p.stdout.splitlines() if p.returncode == 0 else []


def live_seats(home=None, sock_dir=None, tmux=None):
    """活著的 AI 席:從真的在跑的艙開始,一窗一窗對席位檔(window_id 要對得上),席碼用 seat_address.code_of。"""
    tmux = tmux or tmux_bin()
    if not tmux:
        return []
    base = _home(home) / ".ez2-aib"
    # 席位目錄一律走 seats_dir_lib(HUB 導出);不自己拼 instances/<艙>/seats —— 拼錯拿到的是「格式正確的錯身份」
    #   (seat_onboard.seats_dir 的 docstring;tests/test-seats-dir-resolution.py 守這條,10-10 實踩)
    env = {"EZ2_AIB_STATE_ROOT": str(base)} if home else os.environ
    disp = (_read_json(base / "identity" / "seat-display.json") or {}).get("display") or {}
    out = []
    for sock in sorted(glob.glob(os.path.join(sock_dir or socket_dir(), MAIN_SOCKET + "*"))):
        name = os.path.basename(sock)
        if name == MAIN_SOCKET:
            cabin = "main"
        elif name.startswith(MAIN_SOCKET + "-"):
            cabin = dm_address.cabin_code(name[len(MAIN_SOCKET) + 1:])
        else:
            continue
        seats_dir = pathlib.Path(seats_dir_lib.seats_dir(home_env=(), hub=name, environ=env))
        for line in _tmux_rows(tmux, sock, "#{window_id}\t#{window_name}\t#{pane_dead}\t#{pane_id}", "list-windows", "-a"):
            parts = line.split("\t")
            if len(parts) != 4 or parts[2] == "1":
                continue
            wid, wname, _, pane = parts
            rec = _read_json(seats_dir / ("w%s.json" % wid.lstrip("@")))
            if not isinstance(rec, dict) or rec.get("window_id") != wid:
                continue
            prov = (rec.get("provider") or "").lower()
            code = seat_address.code_of(rec)
            if not code or prov.endswith("bot"):
                continue
            persona = (disp.get(code) or {}).get("display_name")
            addr = "%s/%s" % (cabin, code)
            label = "%s/@%s %s%s" % (cabin, code, wname, (" · " + persona) if persona else "")
            out.append({"addr": addr, "label": label, "provider": prov, "method": method_of(prov),
                        "sock": sock, "wid": wid, "pane": pane})
    return out


def _paste_text(tmux, sock, pane, text):
    """用 tmux 的「貼上」送字(bracketed paste,不按 Enter)。"""
    buf = "aib-shot-%d" % os.getpid()
    p = subprocess.run([tmux, "-S", sock, "load-buffer", "-b", buf, "-"], input=text, capture_output=True,
                       text=True, timeout=10)
    if p.returncode != 0:
        return False
    return _run([tmux, "-S", sock, "paste-buffer", "-p", "-d", "-b", buf, "-t", pane], timeout=10).returncode == 0


def set_clipboard_png(png):
    script = 'set the clipboard to (read (POSIX file "%s") as «class PNGf»)' % png.replace("\\", "\\\\").replace('"', '\\"')
    try:
        return _run(["osascript", "-e", script], timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def deliver(addr, saved_path, png=None, note="", home=None, sock_dir=None, tmux=None, seats=None,
            wait_s=4.0, clipboard=set_clipboard_png):
    """把圖放進那一席的輸入框(不送出),附的那句話接在後面;放完把中間的畫面切到那一席。"""
    tmux = tmux or tmux_bin()
    seats = live_seats(home, sock_dir, tmux) if seats is None else seats
    s = next((x for x in seats if x["addr"] == addr), None)
    if not s:
        return {"rc": 1, "reason": "seat_gone", "seat": addr}
    sock, pane, m = s["sock"], s["pane"], s["method"]

    def screen():
        try:
            return _run([tmux, "-S", sock, "capture-pane", "-p", "-t", pane], timeout=10).stdout or ""
        except (OSError, subprocess.TimeoutExpired):
            return ""
    before = screen().count("[Image #")
    if m == "key":
        if not (png and clipboard(png)):
            return {"rc": 1, "reason": "clipboard_failed", "seat": addr, "method": m}
        _run([tmux, "-S", sock, "send-keys", "-t", pane, "C-v"], timeout=10)
    else:
        text = saved_path if m == "path" else T("aib.shot.deliver_text", {"path": saved_path})
        if not _paste_text(tmux, sock, pane, text):
            return {"rc": 1, "reason": "paste_failed", "seat": addr, "method": m}
    seen = False
    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(0.5)
        now = screen()
        seen = (now.count("[Image #") > before) if m in ("key", "path") else (saved_path in now)
        if seen:
            break
    if note:
        _paste_text(tmux, sock, pane, " " + note)
    _run([tmux, "-S", sock, "select-window", "-t", s["wid"]], timeout=10)
    return {"rc": 0 if seen else 1, "reason": "delivered" if seen else "not_seen", "seat": addr, "method": m}


# ---------- 縮圖:貼在 [Image #…] 那一行正上方(the maintainer 10-09:「在 [截圖] 的對應上方有真實的縮圖,點擊還可以放大進入修改」)----------
# 終端機(Terminal.app)不能在文字裡畫圖 ⇒ 另外浮一個小視窗(src/shot/editor/thumb.js),位置要算到 [Image #…] 那一行的螢幕座標。
# 四層:那一格在艙窗裡(capture-pane 找字)→ 艙的 client 在外層哪一格 → 外層 client 是哪個 Terminal 分頁 → 那個視窗在螢幕哪裡。
# 格子大小用 Terminal 的字型算(這台 12pt JetBrains Mono ⇒ 7×17 點,四周各留 10 點);
# 10-09 實測:用這個算法截出來的那一列就是目標那一列,橫向差不到一格。
IMAGE_NEEDLE = "[Image #"
TERMINAL_JS = r"""
ObjC.import("Cocoa");
var T = Application("Terminal"), out = null;
T.windows().forEach(function (w) { w.tabs().forEach(function (t) { if (t.tty() === "%s") {
  var s = t.currentSettings(), f = $.NSFont.fontWithNameSize(s.fontName(), s.fontSize());
  out = { bounds: w.bounds(), adv: f.advancementForGlyph(f.glyphWithName("M")).width,
          asc: f.ascender, desc: f.descender, lead: f.leading };
} }); });
JSON.stringify(out);"""


def cell_width(text):
    """終端機裡占幾格:全形 2 格,組合字元與 VS16 不占格。"""
    import unicodedata
    w = 0
    for ch in text:
        if unicodedata.combining(ch) or ch == "️":
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def find_needle(screen, needle, provider=None):
    """只看輸入框那一行(composer_state.find_composer_line:畫面尾端最後一個提示符行)。
    ★ 不能在整個畫面找 needle:對話內容裡也會有「[Image #1]」(送出後的那則訊息、或有人打出這幾個字),
      對到那裡位置就錯,而且送出後它還留在畫面上 ⇒ 縮圖永遠判不出「已經送出」(10-09 實踩:本席畫面就有)。
    回 (列, 前面占幾格) 或 None。"""
    raw, row = composer_state.find_composer_line(screen, provider)
    if raw is None:
        return None
    line = composer_state.strip_sgr(raw)
    if needle not in line:
        return None
    return row, cell_width(line[:line.index(needle)])


def screen_point(term, cols, rows, abs_row, abs_col):
    """Terminal 視窗資料 + 格子位置 ⇒ 那一格左上角的螢幕座標(左上原點)。由下往上算,不用猜標題列高度。"""
    import math
    b = term["bounds"]
    cw = math.floor(term["adv"])
    ch = math.ceil(term["asc"]) + math.ceil(-term["desc"]) + math.ceil(term["lead"])
    inset = (b["width"] - cols * cw) / 2.0
    return {"x": b["x"] + inset + abs_col * cw, "line_top": b["y"] + b["height"] - inset - (rows - abs_row) * ch,
            "cw": cw, "ch": ch, "win": b}


def locate(seat, tmux=None, outer_sock=None):
    """seat(live_seats 的一筆)⇒ [Image #…] 的螢幕位置;任何一層量不到就回 None(不猜)。"""
    tmux = tmux or tmux_bin()
    sock, pane = seat["sock"], seat["pane"]
    rows = _tmux_rows(tmux, sock, "#{pane_left} #{pane_top}", "display", "-p", "-t", pane)
    shot = _run([tmux, "-S", sock, "capture-pane", "-p", "-t", pane], timeout=10).stdout
    hit = find_needle(shot, IMAGE_NEEDLE, seat.get("provider"))
    clients = _tmux_rows(tmux, sock, "#{client_pid}", "list-clients")
    if not (rows and hit and clients):
        return None
    pl, pt = (int(v) for v in rows[0].split())
    ctty = tty_of(clients[0])
    osock = outer_sock or os.path.join(os.path.dirname(sock), "default")
    outer = [r.split("\t") for r in _tmux_rows(tmux, osock, "#{pane_tty}\t#{pane_left}\t#{pane_top}\t#{session_name}",
                                                 "list-panes", "-a")]
    o = next((r for r in outer if r[0] == ctty), None)
    if not o:
        return None
    oc = next((r.split("\t") for r in _tmux_rows(tmux, osock, "#{client_tty}\t#{client_width}\t#{client_height}\t#{client_session}",
                                                  "list-clients") if r.split("\t")[3] == o[3]), None)
    if not oc:
        return None
    try:
        term = json.loads(_run(["osascript", "-l", "JavaScript", "-e", TERMINAL_JS % oc[0]], timeout=20).stdout or "null")
    except (ValueError, OSError, subprocess.TimeoutExpired):
        term = None
    if not term:
        return None
    pt_ = screen_point(term, int(oc[1]), int(oc[2]), int(o[2]) + pt + hit[0], int(o[1]) + pl + hit[1])
    return pt_


def show_thumb(seat, png, saved_path, home=None, tmux=None, reedit=True):
    """在 [Image #…] 正上方浮縮圖(背景跑,不等它)。量不到位置就不浮(回 None),不改到螢幕角落去。"""
    tmux = tmux or tmux_bin()
    loc = locate(seat, tmux)
    if not loc:
        return None
    w = loc["win"]
    max_w = min(520, w["x"] + w["width"] - loc["x"] - 10)
    max_h = min(380, loc["line_top"] - w["y"] - 40)
    if max_w < 120 or max_h < 80:
        return None
    cfg = {"png": png, "title": seat["label"].split(" · ")[0],   # 只留「艙/@席 窗名」:太長會被從中間截掉(10-10 截圖)
           "x": loc["x"], "line_top": loc["line_top"], "max_w": max_w, "max_h": max_h,
           "tmux": tmux, "sock": seat["sock"], "pane": seat["pane"], "needle": IMAGE_NEEDLE, "max_s": 1800,
           "reedit": [sys.executable, str(pathlib.Path(__file__).resolve()), "reedit", saved_path, seat["addr"]] if reedit else []}
    path = shot_home(home) / ("thumb-%d.json" % int(time.time() * 1000))
    _write_json(path, cfg)
    subprocess.Popen(["/usr/bin/osascript", "-l", "JavaScript", str(SHOT_SRC / "editor" / "thumb.js"), str(path)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"x": loc["x"], "line_top": loc["line_top"], "max_w": max_w, "max_h": max_h}


def tty_of(pid):
    """行程跑在哪個終端機(/dev/ttysNNN);量不到回 ""。"""
    try:
        t = _run(["ps", "-o", "tty=", "-p", str(pid)], timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return "/dev/" + t if t and t != "??" else ""


def terminal_ttys():
    """Terminal 各視窗目前分頁的 tty,由最前面排到最後面;量不到回 []。"""
    try:
        p = _run(["osascript", "-e", 'tell application "Terminal" to get tty of selected tab of every window'], timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [t.strip() for t in (p.stdout or "").split(",") if t.strip().startswith("/dev/")] if p.returncode == 0 else []


def on_screen_addr(seats, tmux=None, ttys=None, outer_sock=None):
    """「畫面最上方」那一席:最前面的 Terminal 視窗裡,那個艙正在顯示的窗(the maintainer 10-10)。量不到回 None,不猜。"""
    tmux = tmux or tmux_bin()
    ttys = terminal_ttys() if ttys is None else ttys
    if not (tmux and ttys and seats):
        return None
    best = None
    for sock in sorted({x["sock"] for x in seats}):
        clients = _tmux_rows(tmux, sock, "#{client_pid}", "list-clients")
        if not clients:
            continue
        ctty = tty_of(clients[0])
        osock = outer_sock or os.path.join(os.path.dirname(sock), "default")
        sess = next((r.split("\t")[1] for r in _tmux_rows(tmux, osock, "#{pane_tty}\t#{session_name}", "list-panes", "-a")
                     if r.split("\t")[0] == ctty), None)
        otty = next((r.split("\t")[0] for r in _tmux_rows(tmux, osock, "#{client_tty}\t#{client_session}", "list-clients")
                     if sess and r.split("\t")[1] == sess), None)
        if otty not in ttys:
            continue
        rank = ttys.index(otty)
        wid = next((r.split()[1] for r in _tmux_rows(tmux, sock, "#{window_active} #{window_id}", "list-windows")
                    if r.split()[0] == "1"), None)
        hit = next((x["addr"] for x in seats if x["sock"] == sock and x["wid"] == wid), None)
        if hit and (best is None or rank < best[0]):
            best = (rank, hit)
    return best[1] if best else None


SCREEN_MARK = "👁 目前畫面 · "


def write_seats_file(home=None, sock_dir=None, tmux=None, prefer=None):
    """給編輯器的「送到」清單。畫面最上方那一席標「👁 目前畫面」、排第一、預設選它(the maintainer 10-10);
    畫面上沒有 AI 席才退回上次送過的那一席。prefer(點縮圖再改)優先於兩者。"""
    seats = live_seats(home, sock_dir, tmux)
    screen = on_screen_addr(seats, tmux)
    ordered = sorted(seats, key=lambda x: 0 if x["addr"] == screen else 1)
    last = prefer or screen or (_read_json(shot_home(home) / "state.json") or {}).get("last_seat") or ""
    _write_json(shot_home(home) / "seats.json",
                {"seats": [{"addr": x["addr"], "label": (SCREEN_MARK if x["addr"] == screen else "") + x["label"],
                            "method": x["method"], "on_screen": x["addr"] == screen} for x in ordered],
                 "last": last, "on_screen": screen or ""})
    return seats


def remember_seat(addr, home=None):
    path = shot_home(home) / "state.json"
    state = _read_json(path) or {}
    state["last_seat"] = addr
    _write_json(path, state)


# ---------- 編譯小幫手 ----------
def source_sha():
    return hashlib.sha256((SHOT_SRC / "hotkey.swift").read_bytes()).hexdigest()


def app_paths(home=None):
    app = shot_home(home) / APP_NAME
    return app, app / "Contents" / "MacOS" / EXE_NAME


def swiftc_bin():
    try:
        p = _run(["xcrun", "--find", "swiftc"], timeout=30)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return shutil.which("swiftc")


def swiftc_cmd():
    """編譯指令的開頭。★ 一定要經 xcrun:直接叫 swiftc 的完整路徑,在乾淨的環境(開艙、aib test)裡
    找不到 macOS SDK ⇒「unable to load standard library」(10-09 測試實踩)。"""
    if not swiftc_bin():
        return None
    return ["xcrun", "--sdk", "macosx", "swiftc"] if shutil.which("xcrun") else [swiftc_bin()]


# CLT 26(Swift 6.2)已知 bug:CommandLineTools/usr/include/swift/ 底下 module.modulemap 與
# bridging.modulemap 都定義 SwiftBridging ⇒ 連只 import Foundation 的檔都編不過。
# 不用 sudo 的解法:-vfsoverlay 把 bridging.modulemap 指到一個空檔(10-15 這台實證可編、minos 15.0)。
SWIFT_BRIDGING_DUP = "redefinition of module 'SwiftBridging'"


def bridging_modulemap(stderr):
    """重複定義的另一半(bridging.modulemap)在哪裡:先照錯誤訊息,再照 swiftc 所在的 toolchain。"""
    for m in re.finditer(r"(/[^\s:]*?/bridging\.modulemap)", stderr or ""):
        return m.group(1)
    sw = swiftc_bin()
    if sw:
        cand = pathlib.Path(sw).resolve().parent.parent / "include" / "swift" / "bridging.modulemap"
        if cand.exists():
            return str(cand)
    return None


def write_bridging_overlay(td, stderr):
    target = bridging_modulemap(stderr)
    if not target:
        return None
    empty = os.path.join(td, "empty.modulemap")
    open(empty, "w").close()
    yml = os.path.join(td, "swift-bridging-overlay.yaml")
    with open(yml, "w", encoding="utf-8") as fh:
        json.dump({"version": 0, "case-sensitive": "false", "roots": [
            {"name": target, "type": "file", "external-contents": empty}]}, fh)
    return yml


def macos_version():
    try:
        p = _run(["sw_vers", "-productVersion"], timeout=30)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def binary_minos(path):
    """執行檔的最低系統版本(LC_BUILD_VERSION 的 minos);讀不到回空字串。"""
    for cmd in (["vtool", "-show-build", str(path)], ["otool", "-l", str(path)]):
        try:
            p = _run(cmd, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        m = re.search(r"^\s*minos\s+([0-9.]+)", p.stdout or "", re.M)
        if p.returncode == 0 and m:
            return m.group(1)
    return ""


def _ver(s):
    try:
        return tuple(int(x) for x in str(s).split("."))
    except ValueError:
        return ()


def build_is_fresh(meta, sha, exe, macos):
    """快取能不能用。★ 不同機器 / 不同系統版本編的 app 不共用:較新系統編的 minos 高,
    在這台 open 回 -10825(10-15 實踩)。所以 macOS 版本要一樣,minos 也不能高過本機。"""
    if meta.get("sha") != sha or not exe.exists():
        return False
    if macos and meta.get("macos") != macos:
        return False
    minos = meta.get("minos") or ""
    if macos and minos and _ver(minos) > _ver(macos):
        return False
    return True


def build(home=None):
    """原始碼的雜湊跟上次編的一樣 ⇒ 不重編(重編會換掉簽章,系統的「螢幕錄影」權限可能要重勾)。"""
    app, exe = app_paths(home)
    meta_path = shot_home(home) / "build.json"
    sha = source_sha()
    meta = _read_json(meta_path) or {}
    macos = macos_version()
    if build_is_fresh(meta, sha, exe, macos):
        return {"rc": 0, "reason": "build_cached", "sha": sha[:12]}
    swiftc = swiftc_cmd()
    if not swiftc:
        return {"rc": 1, "reason": "no_swiftc"}
    exe.parent.mkdir(parents=True, exist_ok=True)
    overlay = False
    with tempfile.TemporaryDirectory(prefix="aib-shot-build-") as td:
        out = os.path.join(td, EXE_NAME)
        args = ["-O", "-o", out, str(SHOT_SRC / "hotkey.swift")]
        p = _run(swiftc + args, timeout=600)
        err = (p.stderr or "") + (p.stdout or "")
        if p.returncode != 0 and SWIFT_BRIDGING_DUP in err:
            yml = write_bridging_overlay(td, err)
            if yml:
                overlay = True
                p = _run(swiftc + ["-vfsoverlay", yml] + args, timeout=600)
        if p.returncode != 0 or not os.path.exists(out):
            return {"rc": 1, "reason": "build_failed", "vfsoverlay": overlay,
                    "detail": (p.stderr or p.stdout).strip()[-400:]}
        shutil.copy2(out, exe)
    with open(app / "Contents" / "Info.plist", "wb") as fh:
        plistlib.dump({
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleName": "Ez2 AIB 截圖",
            "CFBundleDisplayName": "Ez2 AIB 截圖",
            "CFBundleExecutable": EXE_NAME,
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.0",
            "CFBundleVersion": sha[:12],
            "LSUIElement": True,
            "NSScreenCaptureUsageDescription": "Ez2 AIB 截圖:按 Control+Shift+S 框選畫面",
        }, fh)
    _run(["codesign", "--force", "-s", "-", str(app)], timeout=120)
    _write_json(meta_path, {"sha": sha, "built_at": int(time.time()), "macos": macos,
                            "minos": binary_minos(exe), "vfsoverlay": overlay})
    return {"rc": 0, "reason": "built", "sha": sha[:12], "vfsoverlay": overlay}


# ---------- 小幫手在不在跑 ----------
def agent_running(home=None):
    """試著拿小幫手的鎖:拿得到 ⇒ 沒有人在跑。"""
    lock = shot_home(home) / "agent.lock"
    if not lock.exists():
        return False
    try:
        fd = os.open(str(lock), os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def agent_status(home=None):
    return _read_json(shot_home(home) / "agent-status.json") or {}


def write_agent_config(home=None, environ=None):
    doc = {
        "python": sys.executable,
        "helper": str(pathlib.Path(__file__).resolve()),
        "interval": INTERVAL_S,
        "shot_dir": resolve_shot_dir(environ, home),
        "editor_dir": str(SHOT_SRC / "editor"),
        "sock_dir": socket_dir(environ),
        "locale": LOC,
    }
    _write_json(shot_home(home) / "agent.json", doc)
    # 鼠鬚管的 📷 格補過 aib_shoot_route 之後叫這支(Squirrel 的環境沒有艙變數,所以路徑在這裡寫死成絕對路徑)
    sh = shot_home(home) / "shoot.sh"
    sh.write_text("#!/bin/sh\n# AIB 截圖(shot_helper.py ensure 寫;鼠鬚管的 📷 與 Control+Shift+S 共用同一條)\n"
                  "exec %s %s shoot\n" % (shlex.quote(doc["python"]), shlex.quote(doc["helper"])), encoding="utf-8")
    sh.chmod(0o755)
    return doc


def start_agent(home=None, wait_s=3.0):
    app, _ = app_paths(home)
    old = agent_status(home)
    before = (old.get("pid"), old.get("ts"))
    p = _run(["open", "-g", "-j", str(app)], timeout=30)
    if p.returncode != 0:
        return {"rc": 1, "reason": "start_failed", "state": "open_rc_%d" % p.returncode}
    deadline = time.time() + wait_s
    st = old
    while time.time() < deadline:      # 小幫手一起來就寫狀態檔(pid 或時間會變)
        st = agent_status(home)
        if (st.get("pid"), st.get("ts")) != before:
            break
        time.sleep(0.2)
    state = st.get("state") or "unknown"
    if state == "running":
        return {"rc": 0, "reason": "started", "pid": st.get("pid")}
    if state == "hotkey_busy":
        return {"rc": 1, "reason": "hotkey_busy"}
    return {"rc": 1, "reason": "start_failed", "state": state}


def stop_agent(home=None):
    st = agent_status(home)
    pid = st.get("pid")
    _, exe = app_paths(home)
    if not pid or not agent_running(home):
        return {"rc": 0, "reason": "not_running"}
    p = _run(["ps", "-o", "command=", "-p", str(pid)], timeout=10)
    if str(exe) not in (p.stdout or ""):
        return {"rc": 1, "reason": "pid_mismatch", "pid": pid}
    os.kill(int(pid), 15)
    return {"rc": 0, "reason": "stopped", "pid": pid}


# ---------- 輸入法(鼠鬚管)那組快捷鍵 ----------
def ime_status(home=None):
    """absent = 這台沒有輸入法截圖;on = 快捷鍵有效;off = 已交給 AIB(旗標檔在)。"""
    rd = rime_dir(home)
    lua = rd / "lua" / "liu_screenshot.lua"
    yaml = rd / "liur.custom.yaml"
    active = False
    try:
        for line in yaml.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s.startswith("#") and "lua_processor@*liu_screenshot" in s:
                active = True
    except OSError:
        pass
    if not (active and lua.exists()):
        return {"state": "absent"}
    guard = all(lua_pieces(lua.read_text(encoding="utf-8")).values())
    flag = (shot_home(home) / "ime-shot-off").exists()
    return {"state": "off" if (guard and flag) else "on", "guard": guard, "flag": flag}


GUARD_FUNC = (
    "-- AIB 截圖接手(由 AIB shot_helper.py ime-off 加入,{date}):~/.ez2-aib/shot/ime-shot-off 在 ⇒\n"
    "--   Control+Shift+S 交給 AIB 的截圖小幫手,這裡不攔鍵。恢復:刪掉那個檔(或 shot_helper.py ime-on),不用重新載入。\n"
    "local function " + GUARD_MARK + "()\n"
    "  return exists(HOME .. \"/.ez2-aib/shot/ime-shot-off\")\n"
    "end\n\n"
)
FUNC_ANCHOR = "function M.func(key, env)\n  if key:release() then return 2 end\n"
FUNC_LINE = "  if " + GUARD_MARK + "() then return 2 end   -- AIB 截圖接手\n"
INIT_ANCHOR = "function M.init(env)\n"
SHOOT_ANCHOR = "function M.shoot(env)\n"
ROUTE_MARK = "aib_shoot_route"
# the maintainer 10-09:📷 格保留,但點下去改走 AIB(同一個編輯器、同一個存放位置、存回能送到席位)。
#   shoot.sh 由 ensure 寫;不在(AIB 沒裝好)就照舊走輸入法自己的截圖。
ROUTE_BLOCK = (
    "  if " + GUARD_MARK + "() and exists(HOME .. \"/.ez2-aib/shot/shoot.sh\") then   -- " + ROUTE_MARK
    + ":📷 也交給 AIB 截圖\n"
    "    os.execute(\"/bin/sh \" .. shell_quote(HOME .. \"/.ez2-aib/shot/shoot.sh\") .. \" >/dev/null 2>&1 &\")\n"
    "    return\n"
    "  end\n"
)


def lua_pieces(text):
    """三塊各補了沒:函式定義、快捷鍵那一行、📷 改走 AIB。"""
    return {"func": "local function " + GUARD_MARK + "()" in text,
            "key": FUNC_LINE in text,
            "route": ROUTE_MARK in text}


def patch_lua(text, date):
    """缺哪塊補哪塊;回 (新內容, None) 或 (None, 原因)。要補的那塊錨點不是剛好一個 ⇒ 整份不改。"""
    have = lua_pieces(text)
    if all(have.values()):
        return text, None
    if not have["func"]:
        if text.count(INIT_ANCHOR) != 1 or "local function exists(" not in text:
            return None, "anchor_mismatch"
        text = text.replace(INIT_ANCHOR, GUARD_FUNC.format(date=date) + INIT_ANCHOR)
    if not have["key"]:
        if text.count(FUNC_ANCHOR) != 1:
            return None, "anchor_mismatch"
        text = text.replace(FUNC_ANCHOR, FUNC_ANCHOR + FUNC_LINE)
    if not have["route"]:
        if text.count(SHOOT_ANCHOR) != 1 or "local function shell_quote(" not in text:
            return None, "anchor_mismatch"
        text = text.replace(SHOOT_ANCHOR, SHOOT_ANCHOR + ROUTE_BLOCK)
    return text, None


def restart_squirrel():
    """照 pid 停(記憶 pkill-by-pid-not-name),再打開;鼠鬚管改 lua 一定要重開才會載入。"""
    p = _run(["pgrep", "-x", "Squirrel"], timeout=10)
    for pid in (p.stdout or "").split():
        try:
            os.kill(int(pid), 15)
        except (OSError, ValueError):
            pass
    time.sleep(1.0)
    return _run(["open", SQUIRREL_APP], timeout=30).returncode == 0


def ime_off(home=None, restart=True):
    st = ime_status(home)
    if st["state"] == "absent":
        return {"rc": 0, "reason": "ime_absent"}
    lua = rime_dir(home) / "lua" / "liu_screenshot.lua"
    restarted = False
    if not st["guard"]:
        old = lua.read_text(encoding="utf-8")
        new, why = patch_lua(old, datetime.date.today().strftime("%m-%d"))
        if new is None:
            return {"rc": 1, "reason": "ime_patch_failed", "detail": why}
        bak = _home(home) / ".ez2-aib" / "pending" / "backup" / ("Rime-%s-aib-shot" % time.strftime("%m%d-%H%M"))
        bak.mkdir(parents=True, exist_ok=True)
        shutil.copy2(lua, bak / "liu_screenshot.lua")
        lua.write_text(new, encoding="utf-8")
        restarted = restart_squirrel() if restart else False
    flag = shot_home(home) / "ime-shot-off"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("Control+Shift+S 交給 AIB 截圖小幫手(%s)\n" % time.strftime("%Y-%m-%d %H:%M"), encoding="utf-8")
    return {"rc": 0, "reason": "ime_off_done", "patched": not st["guard"], "restarted": restarted}


def ime_on(home=None):
    flag = shot_home(home) / "ime-shot-off"
    if flag.exists():
        flag.unlink()
    return {"rc": 0, "reason": "ime_on_done"}


def _osa_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def ask_ime_off(timeout_s=300):
    """跳視窗問。回 "off" / "later" / None(逾時、出錯 —— 下次開艙再問)。"""
    off, later = T("aib.shot.dialog_off"), T("aib.shot.dialog_later")
    script = ("display dialog %s with title %s buttons {%s, %s} default button %s giving up after %d"
              % (_osa_str(T("aib.shot.dialog_body")), _osa_str(T("aib.shot.dialog_title")),
                 _osa_str(later), _osa_str(off), _osa_str(off), timeout_s))
    try:
        p = _run(["osascript", "-e", script], timeout=timeout_s + 30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = p.stdout or ""
    if p.returncode != 0 or "gave up:true" in out:
        return None
    if "button returned:" + off in out:
        return "off"
    if "button returned:" + later in out:
        return "later"
    return None


def maybe_prompt(home=None, ask=ask_ime_off):
    st = ime_status(home)
    if st["state"] != "on":
        return {"prompt": "skip_" + st["state"]}
    state_path = shot_home(home) / "state.json"
    state = _read_json(state_path) or {}
    if state.get("ime_prompt") in ("dismissed", "done"):
        return {"prompt": "skip_" + state["ime_prompt"]}
    choice = ask()
    if choice == "off":
        r = ime_off(home)
        if r["rc"] == 0:
            state["ime_prompt"] = "done"
            _write_json(state_path, state)
        return {"prompt": "off", "ime": r}
    if choice == "later":
        state["ime_prompt"] = "dismissed"
        _write_json(state_path, state)
        return {"prompt": "later"}
    return {"prompt": "no_answer"}


# ---------- 子指令 ----------
def cmd_ensure(args):
    if sys.platform != "darwin":
        return _emit({"rc": 0, "reason": "not_macos"}, T("aib.shot.not_macos"))
    if os.environ.get("AIB_SHOT") == "0":
        return _emit({"rc": 0, "reason": "disabled"}, T("aib.shot.disabled"))
    if not args.force and not live_cabin():
        return _emit({"rc": 0, "reason": "not_cabin"}, T("aib.shot.not_cabin"))
    d = shot_home()
    d.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(d / "ensure.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return _emit({"rc": 0, "reason": "ensure_busy"})
    try:
        b = build()
        if b["rc"] != 0:
            key = "aib.shot.no_swiftc" if b["reason"] == "no_swiftc" else "aib.shot.build_failed"
            return _emit(b, T(key, {"detail": b.get("detail", "")}))
        cfg = write_agent_config()
        if agent_running():
            out = {"rc": 0, "reason": "already", "build": b["reason"]}
            msg = T("aib.shot.already")
        else:
            out = start_agent()
            out["build"] = b["reason"]
            msg = {"started": T("aib.shot.started"), "hotkey_busy": T("aib.shot.hotkey_busy")}.get(
                out["reason"], T("aib.shot.start_failed", {"state": out.get("state", "")}))
        out["shot_dir"] = cfg["shot_dir"]
        if out["rc"] == 0 and not args.no_prompt:
            out.update(maybe_prompt())
        _log(None, "ensure " + json.dumps(out, ensure_ascii=False))
        return _emit(out, msg)
    finally:
        os.close(lock_fd)


def cmd_alive(args):
    cfg = _read_json(shot_home() / "agent.json") or {}
    r = alive(args.sock_dir or cfg.get("sock_dir") or socket_dir())
    if r is None:
        print(json.dumps({"rc": 2, "reason": "no_tmux"}))
        return 2
    print(json.dumps({"rc": 0 if r else 1, "reason": "alive" if r else "no_cabin"}))
    return 0 if r else 1


def edit_and_deliver(image, cfg, prefer=None):
    """開編輯器編 image;存回時有選席位就送進那一席的輸入框,送成功就在 [Image #…] 上方浮縮圖。"""
    shot_dir = cfg.get("shot_dir") or resolve_shot_dir()
    editor = pathlib.Path(cfg.get("editor_dir") or (SHOT_SRC / "editor"))
    sock_dir = cfg.get("sock_dir") or socket_dir()
    last_save = shot_home() / "last-save.json"
    if last_save.exists():
        last_save.unlink()
    seats = write_seats_file(sock_dir=sock_dir, prefer=prefer)
    ed = _run(["/usr/bin/osascript", "-l", "JavaScript", str(editor / "ez2-shot.js"), image, shot_dir,
               str(editor)], timeout=3600)
    _log(None, "shoot editor rc=%d" % ed.returncode)
    out = {"rc": 0 if ed.returncode == 0 else 1, "reason": "edited", "editor_rc": ed.returncode}
    saved = _read_json(last_save) or {}
    if saved.get("seat"):
        r = deliver(saved["seat"], saved.get("path", ""), png=saved.get("png"), note=saved.get("note", ""),
                    sock_dir=sock_dir, seats=seats)
        if r["rc"] == 0:
            remember_seat(saved["seat"])
            seat = next((x for x in seats if x["addr"] == saved["seat"]), None)
            if seat and saved.get("png"):
                keep = shot_home() / ("thumb-%d.png" % int(time.time() * 1000))   # last.png 下一張就被蓋掉
                shutil.copy2(saved["png"], keep)
                r["thumb"] = show_thumb(seat, str(keep), saved.get("path", ""))
        _log(None, "deliver " + json.dumps(r, ensure_ascii=False))
        out["deliver"] = r
    return out


def cmd_shoot(args):
    cfg = _read_json(shot_home() / "agent.json") or {}
    fd, tmp = tempfile.mkstemp(prefix="ez2-shot-", suffix=".png")
    os.close(fd)
    os.unlink(tmp)                      # screencapture 自己建檔;取消(Esc)就不會有檔
    try:
        cap = _run(["/usr/sbin/screencapture", "-i", "-x", tmp], timeout=600)
        if cap.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            _log(None, "shoot cancelled rc=%d" % cap.returncode)
            return _emit({"rc": 0, "reason": "cancelled"})
        return _emit(edit_and_deliver(tmp, cfg))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def cmd_reedit(args):
    """點縮圖:把那張圖放大進編輯器再改;存回預設送回原來那一席(輸入框裡會多一張,舊的那張要自己刪)。"""
    cfg = _read_json(shot_home() / "agent.json") or {}
    if not os.path.exists(args.image):
        return _emit({"rc": 1, "reason": "no_image", "image": args.image})
    fd, tmp = tempfile.mkstemp(prefix="ez2-shot-re-", suffix=".png")
    os.close(fd)
    try:
        conv = _run(["sips", "-s", "format", "png", args.image, "--out", tmp], timeout=60)
        if conv.returncode != 0:
            shutil.copy2(args.image, tmp)
        return _emit(edit_and_deliver(tmp, cfg, prefer=args.seat))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def cmd_seats(args):
    seats = live_seats(sock_dir=args.sock_dir)
    return _emit({"rc": 0, "reason": "seats", "seats": [{k: x[k] for k in ("addr", "label", "provider", "method")}
                                                        for x in seats]})


def cmd_status(args):
    _, exe = app_paths()
    doc = {"rc": 0, "reason": "status", "agent_running": agent_running(), "agent": agent_status(),
           "built": exe.exists(), "build": _read_json(shot_home() / "build.json") or {},
           "ime": ime_status(), "prompt": (_read_json(shot_home() / "state.json") or {}).get("ime_prompt")}
    return _emit(doc)


def cmd_stop(args):
    r = stop_agent()
    return _emit(r, T("aib.shot.stopped") if r["reason"] == "stopped" else
                 T("aib.shot.not_running") if r["reason"] == "not_running" else None)


def cmd_ime_status(args):
    st = ime_status()
    return _emit(dict(st, rc=0, reason="ime_status"))


def cmd_ime_off(args):
    r = ime_off()
    msg = {"ime_absent": T("aib.shot.ime_absent"), "ime_off_done": T("aib.shot.ime_off_done"),
           "ime_patch_failed": T("aib.shot.ime_patch_failed", {"detail": r.get("detail", "")})}[r["reason"]]
    if r.get("restarted"):
        msg += "\n" + T("aib.shot.ime_restart")
    return _emit(r, msg)


def cmd_ime_on(args):
    return _emit(ime_on(), T("aib.shot.ime_on_done"))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="shot_helper.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("ensure")
    e.add_argument("--force", action="store_true")
    e.add_argument("--no-prompt", action="store_true")
    a = sub.add_parser("alive")
    a.add_argument("--sock-dir")
    re_ = sub.add_parser("reedit")
    re_.add_argument("image")
    re_.add_argument("seat", nargs="?")
    se = sub.add_parser("seats")
    se.add_argument("--sock-dir")
    for name in ("shoot", "status", "stop", "ime-status", "ime-off", "ime-on"):
        sub.add_parser(name)
    args = ap.parse_args(argv)
    fn = {"ensure": cmd_ensure, "alive": cmd_alive, "shoot": cmd_shoot, "seats": cmd_seats, "reedit": cmd_reedit, "status": cmd_status,
          "stop": cmd_stop,
          "ime-status": cmd_ime_status, "ime-off": cmd_ime_off, "ime-on": cmd_ime_on}[args.cmd]
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
