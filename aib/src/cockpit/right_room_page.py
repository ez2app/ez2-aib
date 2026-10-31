#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""右欄第 4 頁「艦長艙」(Andy 10-30 21:1x;規格 SPEC-right-tab-captain-room-f5fb8-1030,工單 #446)。

Andy 原話:「在 1.AI 2.AI用量 3.離艙 新增一個 4.艦長艙,在第 4 個裡面第一個是開啟遊戲化艦長艙,
之後有些簡易設定可以放在裡面」。v1 只做:第一列開啟艦長室 + 三列唯讀設定(介面模式 / 外掛版本 / 本機艙址,值跟 aib room status 走、每台自己的)。

用法(畫面層只讀快取,慢的在背景跑):
  right_room_page.py render --width W --row-offset N   第 4 頁的字;順便寫 ai-rowmap.json(第一列 = room-open)
  right_room_page.py produce                           量一次寫快取(.right-page-room.json);render 過期時自己在背景叫
  right_room_page.py open                              點第一列 / 按 Enter:3 秒內不重開;沒裝外掛不開

★ 每一格壞掉都只顯示「—」:外掛沒裝、aib room status 失敗、快取 JSON 壞 —— 不讓右欄迴圈死(rc 一律 0)。
★ 開艦長室走 aib 入口(`bash <cockpit>/aib room open`,同 mouse-ctl 叫 aib 的寫法),不自己拼外掛路徑。
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if os.path.join(HERE, "render") not in sys.path:
    sys.path.append(os.path.join(HERE, "render"))

CACHE_NAME = ".right-page-room.json"
LOCK_NAME = ".right-page-room.lock"
OPEN_MARK = ".room-open.at"         # 上次真的叫 aib room open 的時刻(防連點)
TTL_S = 10                          # 快取 10 秒刷新一次(規格第 4 點)
DEBOUNCE_S = 3                      # 3 秒內再點不重開(規格第 2 點)
OPEN_ACTION = "room-open"
DASH = "—"
SIDE = 2


def _state_dir():
    return os.environ.get("EZ2_COCKPIT_STATE_DIR") or ""


def _path(name):
    d = _state_dir()
    return os.path.join(d, name) if d else None


def T(key, **params):
    try:
        import i18n_lib
        return i18n_lib.t("aib.room_tab." + key, params, locale=i18n_lib.resolve_locale())
    except Exception:  # noqa: BLE001
        return "aib.room_tab." + key


def _vw(s):
    try:
        from render_lib import visible_width
        return visible_width(s)
    except Exception:  # noqa: BLE001
        import unicodedata
        return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _clip(s, w):
    if _vw(s) <= w:
        return s
    out = ""
    for ch in s:
        if _vw(out + ch) > max(1, w - 1):
            return out + "…"
        out += ch
    return out


# ── 量值(每個都吞例外,量不到回 None)───────────────────────────────────────────────

def plugin_info():
    """回 (installed, version)。installed 用 aib_ui.room_installed(同 aib 入口的判法);
    version 取提供 room 指令那個外掛的 plugin.json version。"""
    try:
        import aib_ui
        installed = bool(aib_ui.room_installed())
    except Exception:  # noqa: BLE001
        return False, None
    if not installed:
        return False, None
    version = None
    try:
        sys.path.insert(0, os.path.join(HERE, "..", "..", "tools", "verify"))
        import aib_plugins
        from pathlib import Path
        _script, name, _why = aib_plugins.find_command("room")
        m = aib_plugins.discover(Path.home(), home_only=True).get(name) or {}
        v = m.get("version")
        version = v if isinstance(v, str) and v else None
    except Exception:  # noqa: BLE001
        version = None
    return True, version


def ui_mode():
    try:
        import aib_ui
        m = aib_ui.get_mode()
        return m if m in ("room", "terminal") else None
    except Exception:  # noqa: BLE001
        return None


def room_url(run=subprocess.run):
    """aib room status 回的網址;沒在跑 / 失敗 ⇒ None。只收 http://127.0.0.1 / localhost 開頭的(不印別的字串到畫面)。"""
    try:
        import aib_ui
        st = aib_ui.room_status(run)
    except Exception:  # noqa: BLE001
        return None
    url = st.get("url") if isinstance(st, dict) and st.get("alive") else None
    if not isinstance(url, str):
        return None
    if not (url.startswith("http://127.0.0.1:") or url.startswith("http://localhost:")) or len(url) > 64:
        return None
    return url


def read_cache():
    p = _path(CACHE_NAME)
    if not p:
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def write_cache(data):
    p = _path(CACHE_NAME)
    if not p:
        return
    tmp = "%s.%d" % (p, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, p)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def produce(run=subprocess.run, now=None):
    """量一次寫快取。同時只准一支(拿不到鎖就走,不排隊)。"""
    lp = _path(LOCK_NAME)
    if not lp:
        return None
    try:
        fd = os.open(lp, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None
        installed, version = plugin_info()
        data = {"v": 1, "at": int(time.time() if now is None else now), "installed": installed,
                "version": version, "ui": ui_mode(), "url": room_url(run) if installed else None}
        write_cache(data)
        return data
    finally:
        os.close(fd)


def _spawn_produce():
    try:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "produce"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def _fresh(data, now):
    try:
        return 0 <= now - int(data.get("at")) < TTL_S
    except (TypeError, ValueError):
        return False


def _opening(now):
    p = _path(OPEN_MARK)
    try:
        return p is not None and 0 <= now - os.path.getmtime(p) < DEBOUNCE_S
    except OSError:
        return False


# ── 畫面 ──────────────────────────────────────────────────────────────────────────

def render(width, row_offset, data, now=None):
    """回 (lines, rowmap)。lines[0] 是分頁列下的空行(同其他頁);第一個內容列 = 開啟艦長室。"""
    now = time.time() if now is None else now
    width = max(20, int(width))
    inner = width - SIDE
    ind = " " * SIDE
    d = data if isinstance(data, dict) else {}
    lines, rowmap = [""], []
    if d.get("installed") is True:
        label = T("open") + (" …" if _opening(now) else "")
        text = _clip(ind + label, inner)
        rowmap.append({"line": row_offset + len(lines),
                       "links": [{"action": OPEN_ACTION, "start": 0, "end": max(_vw(text), inner)}]})
        lines.append(text)
    else:
        lines.append(_clip(ind + T("not_installed"), inner))
    lines.append("")
    ui = d.get("ui") if d.get("ui") in ("room", "terminal") else DASH
    ver = d.get("version") if isinstance(d.get("version"), str) and d.get("version") else DASH
    if d.get("installed") is not True:
        ver = DASH
    url = d.get("url") if isinstance(d.get("url"), str) and d.get("url") else None
    if url is None:
        url = T("url_off") if d.get("installed") is True else DASH
    lines.append(_clip(ind + T("ui_mode", value=ui), inner))
    lines.append(_clip(ind + "  " + T("hint_ui"), inner))
    lines.append(_clip(ind + T("version", value=ver), inner))
    lines.append(_clip(ind + T("url", value=url), inner))
    return lines, rowmap


def write_rowmap(rowmap):
    p = _path("ai-rowmap.json")
    if not p:
        return
    tmp = "%s.%d" % (p, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rowmap, fh, ensure_ascii=False)
        os.replace(tmp, p)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def cmd_render(width, row_offset, now=None, spawn=_spawn_produce):
    now = time.time() if now is None else now
    data = read_cache()
    if data is None or not _fresh(data, now):
        # 快的三項當場量(只讀檔),網址沿用舊快取;慢的 aib room status 交給背景 produce
        installed, version = plugin_info()
        old = data or {}
        data = {"v": 1, "at": old.get("at", 0), "installed": installed, "version": version, "ui": ui_mode(),
                "url": old.get("url") if installed else None}
        spawn()
    lines, rowmap = render(width, row_offset, data, now)
    write_rowmap(rowmap)
    return lines


# ── 開啟 ──────────────────────────────────────────────────────────────────────────

def aib_cmd():
    """aib 入口:AIB_ROOM_TAB_AIB(治具 / 替身)> PATH 上的 aib > 套件入口 cli.py > 同資料夾的 bash 啟動器。

    10-31 Andy 點右欄第 4 頁第一列「開啟艦長艙」沒開:bash 啟動器(src/cockpit/aib)的 `room` 只認同資料夾的 captain_room.py,
    公開版沒有那支 ⇒ 印「還在開發中」就退;把 room 交給已裝外掛的是套件入口(aib/cli.py → aib_plugins.find_command),
    所以要走使用者自己打 `aib` 時走的那條(跟 aib_ui._aib_bin 同順序),最後才退回 bash 啟動器。"""
    alt = os.environ.get("AIB_ROOM_TAB_AIB")
    if alt:
        return [alt]
    found = shutil.which("aib")
    if found:
        return [found]
    cli = os.path.normpath(os.path.join(HERE, "..", "..", "cli.py"))
    if os.path.isfile(cli):
        return [sys.executable, cli]
    return ["bash", os.path.join(HERE, "aib")]


def open_room(now=None, installed=None, popen=subprocess.Popen):
    """回 'opened' / 'debounced' / 'not-installed' / 'failed'。3 秒內第二次不重開(鎖 + 時間戳,連點兩支同時到也只開一次)。"""
    now = time.time() if now is None else now
    if not (plugin_info()[0] if installed is None else installed):
        return "not-installed"
    mark, lp = _path(OPEN_MARK), _path(LOCK_NAME + ".open")
    if not mark or not lp:
        return "failed"
    try:
        fd = os.open(lp, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        return "failed"
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if _opening(now):
            return "debounced"
        try:
            with open(mark, "w", encoding="utf-8") as fh:
                fh.write("%d\n" % int(now))
            os.utime(mark, (now, now))
        except OSError:
            return "failed"
        log = _path("aib-open.log")
        try:
            out = open(log, "a", encoding="utf-8") if log else subprocess.DEVNULL
        except OSError:
            out = subprocess.DEVNULL
        try:
            popen(aib_cmd() + ["room", "open"], stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                  start_new_session=True)
        except OSError:
            return "failed"
        finally:
            if out is not subprocess.DEVNULL:
                out.close()
        kick = _path("right-kick")
        if kick:                       # 讓右欄當拍就畫出「…」
            try:
                with open(kick, "w", encoding="utf-8") as fh:
                    fh.write("room-open-%d\n" % int(now))
            except OSError:
                pass
        return "opened"
    finally:
        os.close(fd)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="right_room_page.py")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("render")
    r.add_argument("--width", type=int, default=44)
    r.add_argument("--row-offset", type=int, default=2)
    sub.add_parser("produce")
    sub.add_parser("open")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "render":
            for ln in cmd_render(a.width, a.row_offset):
                print(ln)
        elif a.cmd == "produce":
            produce()
        elif a.cmd == "open":
            print(open_room())
    except Exception:  # noqa: BLE001 —— 右欄迴圈不能因為這一頁死掉
        if a.cmd == "render":
            print("")
            print("  " + DASH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
