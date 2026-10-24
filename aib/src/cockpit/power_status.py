#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib power status / upgrade:艦長動力室(新艦長第一站)的資料與升級(契約 contracts/power.json)。

  aib power status [--refresh] [--json]   整頁資料一次給:AI 燈號與版本、健康檢查、自動更新、綁定、新手任務、建議安裝
  aib power upgrade <家> [--json]          升級一家 AI CLI 或 aib 自己(包 cli_versions,共用更新鎖、壞了自動裝回)
  aib power login <家> …                   燈號 / 登入 / 新手任務在 power_login.py(@d07f9);這支只轉過去

10-24 Andy「動力室準備開發」「你們二個一起開發」:總管 + 版本 / 健康 / 自動更新 / 綁定 / 清單 = @abb89;
燈號(登入 / 額度)、login、onboarding = @d07f9 的 power_login.py(light_of / onboarding_state)。它還沒在時這幾欄照實回 unknown / 空。
回條的 detail / reason 是契約代碼,不換人話;人話只在 hint。不回路徑、不回帳號。
"""
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cli_versions as CV  # noqa: E402
import seats_dir_lib  # noqa: E402

KEYS = CV.GATE_KEYS                       # claude / codex / grok / copilot / agy / aib(順序 = 開席動畫 + aib)
HEALTH_TTL_S = 600                        # 健康檢查快取 10 分鐘;--refresh 才重量
NET_TIMEOUT_S = 3
NET_TARGETS = (("net_github", "https://github.com"), ("net_pypi", "https://pypi.org"), ("net_page", "https://ez2.page"))
DISK_LOW_GB = 5


def state_dir():
    return os.path.join(seats_dir_lib.state_root()[0], "state")


def _which_version(binary, args=("--version",), timeout=10):
    """(有沒有, 版本字串或 None)。"""
    if not shutil.which(binary):
        return False, None
    rc, out = CV._run([binary] + list(args), timeout)
    if rc != 0:
        return True, None
    m = re.search(r"\d+(?:\.\d+)+[a-z]?", out or "")   # tmux -V 是「tmux 3.5a」,不是三段版號
    return True, m.group(0) if m else None


def _net_ok(url, timeout=NET_TIMEOUT_S):
    """HEAD 一下,不帶任何憑證;任何 HTTP 回應都算連得到(連線層錯 / 逾時才算不通)。"""
    import urllib.error
    import urllib.request
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "aib-power"})
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:  # noqa: BLE001 - 沒網路 / DNS / 逾時
        return False


def health(*, net=_net_ok, which=_which_version, disk=shutil.disk_usage, home=None, system=None, touchid=None):
    """[{key, ok, value, hint}];ok=None 表示這個平台不適用。"""
    system = system or platform.system()
    home = home or os.path.expanduser("~")
    rows = []

    def row(key, ok, value=None, hint=None):
        rows.append({"key": key, "ok": ok, "value": value, "hint": hint})

    has, v = which("tmux", ("-V",))
    row("tmux", has, v, None if has else ("brew install tmux" if system == "Darwin" else "install tmux"))
    py = ".".join(str(x) for x in sys.version_info[:3])
    row("python", sys.version_info >= (3, 9), py, None if sys.version_info >= (3, 9) else "Python 3.9+")
    has, v = which("node")
    row("node", has, v, None if has else "node.js (npm) — codex / copilot")
    has, v = which("git")
    row("git", has, v, None if has else "git")
    try:
        free_gb = round(disk(home).free / 1024 ** 3, 1)
        row("disk", free_gb >= DISK_LOW_GB, free_gb, None if free_gb >= DISK_LOW_GB else f"< {DISK_LOW_GB} GB")
    except OSError:
        row("disk", None, None, None)
    if system == "Darwin":
        if touchid is None:
            try:
                import touchid_approve
                touchid = touchid_approve.available()
            except Exception:  # noqa: BLE001
                touchid = False
        row("touchid", bool(touchid), None, None if touchid else "Touch ID")
    else:
        row("touchid", None, None, None)
    for key, url in NET_TARGETS:
        ok = net(url)
        row(key, ok, None, None if ok else url)
    return rows


def health_cached(refresh=False, now=None, **kw):
    now = time.time() if now is None else now
    path = os.path.join(state_dir(), "power-health.json")
    memo = CV._load(path)
    if not refresh and isinstance(memo.get("rows"), list) and now - memo.get("at", 0) < HEALTH_TTL_S:
        return memo["rows"]
    rows = health(**kw)
    CV._save(path, {"at": now, "rows": rows})
    return rows


def _login_mod():
    """@d07f9 的 power_login.py;還沒在 ⇒ None(燈號照實 unknown)。"""
    try:
        import power_login
        return power_login
    except ImportError:
        return None


def ai_rows(refresh=False, check=None, login=None):
    rows = (check or CV.check)(refresh=refresh)
    by = {r["key"]: r for r in rows}
    login = _login_mod() if login is None else login
    out = []
    for k in KEYS:
        r = by.get(k) or {}
        installed = bool(r.get("local"))
        light = {"light": "off" if not installed else "unknown", "reason": None, "hint": None, "reset_at": None,
                 "login_cmd": None}
        if installed and k != "aib":
            if login is not None:
                try:
                    got = login.light_of(k) or {}
                    light.update({f: got.get(f) for f in light if f in got})
                except Exception:  # noqa: BLE001 - 一家量壞不拖垮整頁
                    light.update(light="unknown", reason="probe_failed")
        elif k == "aib" and installed:
            light["light"] = "green"
        out.append({"key": k, "installed": installed, "local": r.get("local"), "latest": r.get("latest"),
                    "update": bool(r.get("update")), "can_update": bool(r.get("can_update")), **light})
    return out


def auto_update_state():
    """#273(aib update auto)的設定與狀態;核心還沒有 ⇒ None。"""
    try:
        import auto_update
    except ImportError:
        return None
    fn = getattr(auto_update, "power_state", None)
    try:
        return fn() if fn else None
    except Exception:  # noqa: BLE001
        return None


def binding_state():
    """各席有沒有綁人格 / 專案 / 第二大腦:只回席碼與問題代碼,不回路徑、名稱。"""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "src", "identity"))
        import bindme
        rows = bindme.status(seats_dir_lib.state_root()[0])
    except Exception:  # noqa: BLE001 - 沒席 / 讀不到 ⇒ 空
        rows = []
    seats = [{"code": r.get("code"), "problem": r.get("problem"), "brain": bool(r.get("brain_bound"))}
             for r in rows if r.get("problem") != "bot"]
    return {"seats": len(seats), "ok": sum(1 for s in seats if s["problem"] == "ok" and s["brain"]),
            "not_ok": [s for s in seats if not (s["problem"] == "ok" and s["brain"])]}


def onboarding(login=None):
    login = _login_mod() if login is None else login
    fn = getattr(login, "onboarding_state", None) if login is not None else None
    try:
        got = fn() if fn else None
    except Exception:  # noqa: BLE001
        got = None
    return got if isinstance(got, dict) else {"birth_done": False, "tasks": []}


def catalog():
    """#20 外掛目錄還沒上線 ⇒ available false。"""
    return {"available": False, "items": []}


def status(refresh=False):
    return {"ok": True, "v": 1, "detail": "power_status", "ais": ai_rows(refresh), "health": health_cached(refresh),
            "auto_update": auto_update_state(), "binding": binding_state(), "onboarding": onboarding(),
            "catalog": catalog(), "hint": None}


# ---------- 升級 ----------

UPGRADE_RC = {"updated": 0, "already": 0, "rolled_back": 1, "failed": 1, "locked": 1}


def upgrade(key, *, out=sys.stderr, check=None, do_update=None, self_update=None):
    """回 (回條, rc)。只升級官方版本(cli_versions 的指令),不加本人證明。"""
    if key not in KEYS:
        return {"ok": False, "detail": "power_upgrade_unsupported", "key": key, "old": None, "new": None,
                "hint": "/".join(KEYS)}, 2
    rows = {r["key"]: r for r in (check or CV.check)(refresh=True, only=(key,))}
    r = rows.get(key) or {}
    old, latest = r.get("local"), r.get("latest")
    if not old:
        return {"ok": False, "detail": "power_upgrade_not_installed", "key": key, "old": None, "new": None, "hint": None}, 2
    tool = CV._tool(key)
    if not tool.get("update"):
        return {"ok": False, "detail": "power_upgrade_unsupported", "key": key, "old": old, "new": None,
                "hint": "aib update"}, 2
    if not tool.get("latest"):   # agy:量不到最新,跑它自己的 update(已最新就不動)
        rc = (self_update or CV.update_self_checking)(key, out=out)
        new = CV.local_version(tool)
        det = "power_upgrade_failed" if rc else ("power_upgraded" if new != old else "power_upgrade_already")
        return {"ok": rc == 0, "detail": det, "key": key, "old": old, "new": new, "hint": None}, (1 if rc else 0)
    if not CV.newer(latest, old):
        return {"ok": True, "detail": "power_upgrade_already", "key": key, "old": old, "new": old, "hint": None}, 0
    res = (do_update or CV.do_update)(key, old, latest, out=out)
    new = CV.local_version(tool)
    det = {"updated": "power_upgraded", "already": "power_upgrade_already", "rolled_back": "power_upgrade_rolled_back",
           "locked": "power_upgrade_locked"}.get(res, "power_upgrade_failed")
    return {"ok": UPGRADE_RC.get(res, 1) == 0, "detail": det, "key": key, "old": old, "new": new, "hint": None}, UPGRADE_RC.get(res, 1)


def _emit(r, as_json):
    if as_json:
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(json.dumps(r, ensure_ascii=False, indent=1))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "login":
        login = _login_mod()
        if login is None or not hasattr(login, "main"):
            _emit({"ok": False, "detail": "power_login_unsupported", "key": argv[1] if len(argv) > 1 else None,
                   "url": None, "opened": False, "hint": None}, "--json" in argv)
            return 2
        return login.main(argv[1:])
    if argv and argv[0] in ("launch", "fav"):           # 出艦台資料(@d07f9 power_launch.py)
        import power_launch
        return power_launch.main(argv)
    if argv and argv[0] == "ask":                        # 問 AI 抽屜:只回答、不動手(@d07f9 power_ask.py)
        import power_ask
        return power_ask.main(argv)
    if argv and argv[0] == "why":                        # 艦員檔案:為什麼用 AIB 排名(@d07f9 power_crew.py)
        import power_crew
        return power_crew.main(argv)
    if argv and argv[0] in ("tier", "redeem"):          # 任務板等級開關 / 序號兌換(@d07f9 power_tasks.py)
        import power_tasks
        return power_tasks.main(argv)
    p = argparse.ArgumentParser(prog="aib power")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--refresh", action="store_true")
    s.add_argument("--json", action="store_true")
    u = sub.add_parser("upgrade")
    u.add_argument("key")
    u.add_argument("--json", action="store_true")
    for name in ("login", "launch", "fav", "ask", "why", "tier", "redeem"):   # 上面先分派走;這裡只為 --help 列全(#293 審查)
        sub.add_parser(name, add_help=False)
    a = p.parse_args(argv)
    if a.cmd not in ("status", "upgrade"):               # 只為 --help 列的空殼(例如 `aib power -- login`)
        p.error("invalid choice: %r" % a.cmd)
    if a.cmd == "status":
        _emit(status(a.refresh), a.json)
        return 0
    r, rc = upgrade(a.key, out=sys.stderr)
    _emit(r, a.json)
    return rc


if __name__ == "__main__":
    sys.exit(main())
