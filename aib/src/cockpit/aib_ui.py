#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""`aib`(不帶參數)預設開艦長室 + `aib ui room|terminal`(10-20 Andy 裁;SPEC-chat-panel 第 0、5 節 C8)。

- 設定在 <狀態根>/ui.json {"ui": "room"|"terminal"},預設 room;`aib ui terminal` 之後 `aib` 回到印說明(傳統駕駛艙)。
- room:先 `aib room status`(艦長室外掛);沒在跑 ⇒ `aib room open`把伺服器起來(帶 --no-browser 不開系統瀏覽器,
  因為 Andy 裁「不帶網址」);然後開**應用程式視窗**:有 Chrome / Edge / Brave ⇒ `--app=http://127.0.0.1:<埠>/`
  + 獨立 profile(<狀態根>/captain-room/browser-profile,不碰使用者平常的瀏覽器)+ --no-first-run --no-default-browser-check;
  都沒有 ⇒ 退回系統預設瀏覽器(有網址列)並印一行說明。
- 艦長室外掛沒裝 / 起不來 ⇒ 印原因、退回印說明(rc 2),不會卡住。
第二版原生 WKWebView(視窗圖示用 @570a9 的 appicon)另開。
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

UI_MODES = ("room", "terminal")
BROWSERS = (("chrome", "Google Chrome.app/Contents/MacOS/Google Chrome"),
            ("edge", "Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            ("brave", "Brave Browser.app/Contents/MacOS/Brave Browser"))
APP_DIRS = ("/Applications", os.path.expanduser("~/Applications"))


def T(key, **params):
    import i18n_lib
    return i18n_lib.t("aib.ui." + key, params, locale=i18n_lib.resolve_locale())


def state_root(env=None):
    import seats_dir_lib
    e = os.environ if env is None else env
    return Path(e.get("EZ2_AIB_STATE_ROOT") or seats_dir_lib.state_root()[0])


def ui_path(env=None):
    return state_root(env) / "ui.json"


def get_mode(env=None):
    try:
        m = json.loads(ui_path(env).read_text(encoding="utf-8")).get("ui")
        return m if m in UI_MODES else "room"
    except (OSError, ValueError, AttributeError):
        return "room"


def set_mode(mode, env=None):
    from atomic_json import write_json
    if mode not in UI_MODES:
        raise ValueError(mode)
    p = ui_path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json(str(p), {"v": 1, "ui": mode})
    return mode


def room_installed():
    """艦長室外掛有沒有裝(tools/verify/aib_plugins.find_command("room"),同 aib 入口的判法)。"""
    try:
        sys.path.insert(0, os.path.join(HERE, "..", "..", "tools", "verify"))
        import aib_plugins
        script, _name, _why = aib_plugins.find_command("room")
        return script is not None
    except Exception:  # noqa: BLE001
        return False


def _aib_bin():
    return os.environ.get("AIB_UI_AIB_BIN") or shutil.which("aib") or "aib"


def room_status(run=subprocess.run):
    try:
        r = run([_aib_bin(), "room", "status"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        d = json.loads((r.stdout or "").strip().splitlines()[-1])
        return d if isinstance(d, dict) else {}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return {}


def room_has_no_browser(run=subprocess.run):
    """外掛 0.1.33 起 aib room open 有 --no-browser(@74e8b 10-20);舊外掛不認這個旗標會 rc 2 ⇒ 先看 --help。"""
    try:
        r = run([_aib_bin(), "room", "open", "--help"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        return "--no-browser" in ((r.stdout or "") + (r.stderr or ""))
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_room(run=subprocess.run):
    """回 (url 或 None, why)。沒在跑就叫 aib room open --no-browser 起伺服器(外掛 0.1.33 起有這個旗標;兩台 10-21 都已 0.1.34)。
    第二波 #7:拿掉 #202 用假 open 擋系統瀏覽器的臨時措施;外掛太舊 ⇒ 不起、回 room-too-old(人話叫他換外掛),不再偷偷開瀏覽器。"""
    st = room_status(run)
    if st.get("alive") and st.get("url"):
        return st["url"], None
    if not room_has_no_browser(run):
        return None, "room-too-old"
    try:
        run([_aib_bin(), "room", "open", "--no-browser"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None, "room-start-failed"
    st = room_status(run)
    if st.get("alive") and st.get("url"):
        return st["url"], None
    return None, "room-not-running"


def find_browser(exists=os.path.exists):
    for name, rel in BROWSERS:
        for d in APP_DIRS:
            p = os.path.join(d, rel)
            if exists(p):
                return name, p
    return None, None


def open_app_window(url, env=None, popen=subprocess.Popen, exists=os.path.exists, run=subprocess.run):
    """回 (how, detail):app = 應用程式視窗;browser = 退回預設瀏覽器;none = 都開不了。"""
    name, binary = find_browser(exists)
    if binary:
        profile = state_root(env) / "captain-room" / "browser-profile"
        profile.mkdir(parents=True, exist_ok=True)
        argv = [binary, "--app=" + url, "--user-data-dir=" + str(profile), "--no-first-run", "--no-default-browser-check"]
        try:
            popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return "app", name
        except OSError:
            pass
    if sys.platform == "darwin":
        try:
            run(["open", url], stdin=subprocess.DEVNULL, timeout=20)
            return "browser", url
        except (OSError, subprocess.SubprocessError):
            pass
    return "none", url


def open_room(out=None, env=None, run=subprocess.run, popen=subprocess.Popen, exists=os.path.exists, installed=None):
    """`aib`(room 模式)的主流程。回 rc:0 開了;2 退回終端機(呼叫端印說明)。"""
    out = sys.stdout if out is None else out
    if not (room_installed() if installed is None else installed):
        out.write(T("room_missing") + "\n")
        return 2
    url, why = ensure_room(run)
    if not url:
        out.write(T("room_too_old" if why == "room-too-old" else "room_failed", why=why) + "\n")
        return 2
    how, detail = open_app_window(url, env, popen=popen, exists=exists, run=run)
    if how == "app":
        out.write(T("opened_app", browser=detail) + "\n")
        return 0
    if how == "browser":
        out.write(T("opened_browser") + "\n")
        return 0
    out.write(T("open_failed", url=url) + "\n")
    return 2


def main(argv=None, out=None):
    """aib ui [room|terminal]:看 / 設。"""
    out = sys.stdout if out is None else out       # 呼叫時才取 sys.stdout(cli 測試會換掉它)
    a = list(sys.argv[1:] if argv is None else argv)
    if a and a[0] in UI_MODES:
        set_mode(a[0])
        out.write(T("set_" + a[0]) + "\n")
        return 0
    if a:
        out.write(T("usage") + "\n")
        return 2
    out.write(T("mode_" + get_mode()) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
