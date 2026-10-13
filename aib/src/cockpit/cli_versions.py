#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""開席時五家 AI CLI 的本機版本 / 最新版本,有新版就倒數 30 秒預設更新(the maintainer 2026-10-09 20:1x 直令)。

畫面:開席動畫(login_splash.py)每家 logo 名字下面多兩行「本機 x」「最新 y」;
動畫播完、接入指揮艙之前,若有新版:倒數 30 秒,時間到自動更新(s 跳過、Enter 馬上更新),更新完才接入。

每一家怎麼查、怎麼更新(10-09 在 the maintainer 的 Mac mini 上逐家實查,不是猜):
  claude   官方安裝程式(~/.local/share/claude/versions)  最新:npm view @anthropic-ai/claude-code  更新:claude update
  codex    npm 全域 @openai/codex                         最新:npm view @openai/codex            更新:npm i -g @openai/codex@latest
  grok     ~/.grok/downloads                              最新:grok update --check --json        更新:grok update
  copilot  npm 全域 @github/copilot                       最新:npm view @github/copilot          更新:npm i -g @github/copilot@latest
  agy      ~/.local/bin/agy                               最新:**量不到**(agy update 沒有只查不裝)  ⇒ 只顯示本機,不自動更新
  aib      這份 aib 自己(套件根的 VERSION)               10-12 the maintainer「也加入 aib 判斷,一併進入一樣的自動更新流程」:
           公開版(一行安裝 / pipx)  最新:公開倉 main 的 aib/VERSION   更新:aib update / pipx upgrade ez2-aib
           私有版(ez2-aib 開發倉)  最新:origin/main 的 VERSION      更新:**不自動**(正本樹只由 aib ship 快轉,the maintainer 10-09)

★ 最新版本快取 1 小時(<狀態根>/state/cli-versions.json);沒網路/找不到 ⇒ 照實寫「最新 找不到」,不擋開席。
★ 每家都顯示「最後更新」= 這台電腦上那一家最後一次安裝/更新的時間(npm 看套件資料夾、其他看程式檔的建立時間;
  npm 會把檔案修改時間壓成 1985,所以不看 mtime 看建立時間)。找不到最新版的家(agy)靠它看新舊(the maintainer 10-09 20:3x)。
★ 更新換的是整台機器的程式;別艙正在跑的席不會被打斷,下次開席才用新版 ⇒ 畫面寫出「N 席正在用舊版」。
★ 更新後本機版本沒變(例如頻道不同)⇒ 同一個最新版 1 小時內不再催,避免每開一席就倒數一次。
★ 只在真人終端開席時出現(與開席動畫同條件);AI 代開、非終端、AIB_CLI_UPDATE=0 一律不查不擋。

用法:
  cli_versions.py check [--out FILE] [--refresh]   查一次,JSON(--out 寫檔給開席動畫讀)
  cli_versions.py gate  [--in FILE] [--seconds 30] 有新版就倒數並更新(開席用;非終端直接放行)
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import seats_dir_lib  # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))      # 私有倉根;公開版 = 套件資料夾(aib/,VERSION 也在這)
PUBLIC_LATEST_URL = "https://raw.githubusercontent.com/ez2app/ez2-aib/main/aib/VERSION"


def aib_edition(repo=None):
    """這份 aib 是哪一種:"private"(ez2-aib 開發倉:有匯出工具)/ "public"(開源版)。"""
    repo = repo or REPO
    return "private" if os.path.isfile(os.path.join(repo, "tools", "oss", "export_aib.py")) else "public"


def aib_update_cmd(repo=None, prefix=None):
    """公開版怎麼更新:一行安裝的 checkout(套件上一層有 .git)⇒ aib update;pipx ⇒ pipx upgrade;私有版 / 判不出 ⇒ None。"""
    repo = repo or REPO
    if aib_edition(repo) == "private":
        return None
    if os.path.isdir(os.path.join(os.path.dirname(repo), ".git")) and os.path.isfile(os.path.join(repo, "cli.py")):
        return [sys.executable, os.path.join(repo, "cli.py"), "update"]
    if "/pipx/venvs/" in repo or "/pipx/venvs/" in (prefix or sys.prefix):
        return ["pipx", "upgrade", "ez2-aib"]
    return None


#: 順序 = 開席動畫的 logo 順序(login_splash.HOUSES)。
TOOLS = (
    {"key": "claude", "bin": "claude", "latest": ("npm", "@anthropic-ai/claude-code"), "update": ["claude", "update"],
     "pin": ["claude", "install", "{v}"]},
    {"key": "codex", "bin": "codex", "latest": ("npm", "@openai/codex"),
     "update": ["npm", "install", "-g", "@openai/codex@latest"], "pin": ["npm", "install", "-g", "@openai/codex@{v}"]},
    {"key": "grok", "bin": "grok", "latest": ("grok",), "update": ["grok", "update"],
     "pin": ["grok", "update", "--version", "{v}"]},
    {"key": "copilot", "bin": "copilot", "latest": ("npm", "@github/copilot"),
     "update": ["npm", "install", "-g", "@github/copilot@latest"], "pin": ["npm", "install", "-g", "@github/copilot@{v}"]},
    {"key": "agy", "bin": "agy", "latest": None, "update": ["agy", "update"], "pin": None},
    # gemini:不在開席動畫五家裡(那格 AGY 是 Antigravity),只在 aib cli 看/更新/退版(the maintainer 10-09 20:2x)
    {"key": "gemini", "bin": "gemini", "latest": ("npm", "@google/gemini-cli"),
     "update": ["npm", "install", "-g", "@google/gemini-cli@latest"], "pin": ["npm", "install", "-g", "@google/gemini-cli@{v}"]},
    # aib 自己(10-12):不是 logo,開席倒數照樣算它(GATE_KEYS)
    {"key": "aib", "bin": None, "latest": ("aib",), "update": aib_update_cmd(), "pin": None},
)
#: 開席動畫與倒數只管這五家(順序 = login_splash.HOUSES);其他家只在 aib cli。
SPLASH_KEYS = ("claude", "codex", "grok", "copilot", "agy")
#: 開席倒數要管的 = 五家 + aib 自己(aib 沒有 logo,倒數畫面另寫一行)。
GATE_KEYS = SPLASH_KEYS + ("aib",)
CACHE_TTL_S = 3600
FAIL_TTL_S = 600                 # 找不到(沒網路)10 分鐘內不再試,不拖慢連開好幾席
DISABLE_ENV = "AIB_CLI_UPDATE"   # =0 整段不查不擋
_VER = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _run(cmd, timeout):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return None, ""


def parse_version(text):
    m = _VER.search(text or "")
    return ".".join(m.groups()) if m else None


def newer(latest, local):
    """latest 比 local 新 ⇒ True。任一量不到 ⇒ False(不催)。"""
    if not latest or not local:
        return False
    a, b = _VER.search(latest), _VER.search(local)
    if not a or not b:
        return False
    return tuple(int(x) for x in a.groups()) > tuple(int(x) for x in b.groups())


def aib_local_version(repo=None):
    try:
        with open(os.path.join(repo or REPO, "VERSION"), encoding="utf-8") as fh:
            return parse_version(fh.read())
    except OSError:
        return None


def aib_latest_version(repo=None):
    """公開版:公開倉 main 的 aib/VERSION(AIB_LATEST_URL 可換);私有版:fetch 後 origin/main 的 VERSION。拿不到 ⇒ None。"""
    repo = repo or REPO
    if aib_edition(repo) == "private":
        rc, _out = _run(["git", "-C", repo, "fetch", "-q", "origin", "main"], 20)
        if rc != 0:
            return None
        rc, out = _run(["git", "-C", repo, "show", "origin/main:VERSION"], 10)
        return parse_version(out) if rc == 0 else None
    import urllib.request
    try:
        with urllib.request.urlopen(os.environ.get("AIB_LATEST_URL") or PUBLIC_LATEST_URL, timeout=5) as r:
            return parse_version(r.read(64).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - 沒網路 / 逾時 ⇒ 照實「找不到」,不擋開席
        return None


def local_version(tool):
    if tool.get("key") == "aib":
        return aib_local_version()
    if not shutil.which(tool["bin"]):
        return None
    _rc, out = _run([tool["bin"], "--version"], 15)
    return parse_version(out)


def latest_version(tool):
    src = tool["latest"]
    if not src:
        return None
    if src[0] == "npm":
        rc, out = _run(["npm", "view", src[1], "version"], 20)
        return parse_version(out) if rc == 0 else None
    if src[0] == "aib":
        return aib_latest_version()
    if src[0] == "grok":
        rc, out = _run(["grok", "update", "--check", "--json"], 20)
        try:
            return parse_version(json.loads(out.strip().splitlines()[-1]).get("latestVersion") or "")
        except (ValueError, IndexError, AttributeError):
            return None
    return None


def cache_path():
    return os.path.join(seats_dir_lib.state_root()[0], "state", "cli-versions.json")


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".cliver-", dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def installed_at(tool):
    """這一家在這台電腦最後一次安裝/更新的時間(epoch);量不到回 None。"""
    if tool.get("key") == "aib":
        try:
            st = os.stat(os.path.join(REPO, "VERSION"))
        except OSError:
            return None
        return int(getattr(st, "st_birthtime", 0) or st.st_mtime)
    w = shutil.which(tool["bin"])
    if not w:
        return None
    real = os.path.realpath(w)
    d = os.path.dirname(real)
    while d != os.path.dirname(d) and not os.path.isfile(os.path.join(d, "package.json")):
        d = os.path.dirname(d)
    target = d if os.path.isfile(os.path.join(d, "package.json")) and "node_modules" in d else real
    try:
        st = os.stat(target)
    except OSError:
        return None
    return int(getattr(st, "st_birthtime", 0) or st.st_mtime)


def check(*, refresh=False, now=None, cache=None, local_fn=local_version, latest_fn=latest_version, installed_fn=None):
    """五家一起查(平行)。回 [{"key","local","latest","update","can_update","nag","installed_at"}…],順序同 TOOLS。"""
    installed_fn = installed_fn or installed_at
    now = time.time() if now is None else now
    cache = cache_path() if cache is None else cache
    memo = _load(cache)

    def one(tool):
        k = tool["key"]
        ent = memo.get(k) or {}
        fresh = ent.get("checked_at", 0) and now - ent["checked_at"] < (CACHE_TTL_S if ent.get("latest") else FAIL_TTL_S)
        loc = local_fn(tool)
        if not tool["latest"]:
            return k, loc, None, ent      # 這一家沒有「只查不裝」的來源(agy):不查、不記
        lat = ent.get("latest") if (fresh and not refresh) else latest_fn(tool)
        if not (fresh and not refresh):
            ent = dict(ent, latest=lat, checked_at=now)
        return k, loc, lat, ent

    with ThreadPoolExecutor(max_workers=len(TOOLS)) as ex:
        rows = list(ex.map(one, TOOLS))
    out = []
    for tool, (k, loc, lat, ent) in zip(TOOLS, rows):
        memo[k] = ent
        upd = newer(lat, loc)
        quiet = ent.get("quiet_for") == lat and now < ent.get("quiet_until", 0)
        out.append({"key": k, "local": loc, "latest": lat, "update": upd,
                    "can_update": bool(tool["update"]) and upd, "nag": bool(tool["update"]) and upd and not quiet,
                    "installed_at": installed_fn(tool)})
    _save(cache, memo)
    return out


def quiet(key, latest, *, now=None, cache=None):
    """同一個最新版 1 小時內不再催(更新後本機沒變 / 真人按了跳過)。"""
    now = time.time() if now is None else now
    cache = cache_path() if cache is None else cache
    memo = _load(cache)
    ent = memo.get(key) or {}
    ent.update(quiet_for=latest, quiet_until=now + CACHE_TTL_S)
    memo[key] = ent
    _save(cache, memo)


def old_seats():
    """各家「正在跑的席」數(活艙的活席窗,同 seat_pool 的判準)。量不到回 {}。"""
    try:
        import seat_pool
        return seat_pool.occupancy().get("by_provider") or {}
    except Exception:  # noqa: BLE001 量不到就不寫那一行
        return {}


# ───────────────────────── 畫面 ─────────────────────────

def version_rows(rows):
    """給 login_splash.frame 用:三列(本機、最新、最後更新),每家一格 = [(字, 256 色)…]。
    「本機/最新」淡灰、版本號亮灰、記號另佔一格(↑ 橘 = 有新版、✓ 綠 = 已最新),兩行起點對齊由 frame 負責。"""
    tag, val = 243, 252
    loc, lat, upd_at = [], [], []
    by = {r["key"]: r for r in rows}
    for r in [by[k] for k in SPLASH_KEYS if k in by]:
        loc.append([("本機 ", tag), (r["local"] or "沒裝", val if r["local"] else 241)])
        if not r["latest"]:
            lat.append([("最新 ", tag), ("找不到", 241)])
        elif r["update"]:
            lat.append([("最新 ", tag), (r["latest"], 214), (" ↑", 214)])
        else:
            lat.append([("最新 ", tag), (r["latest"], val), (" ✓", 71)])
        t = r.get("installed_at")
        upd_at.append([("更新日 ", tag), (time.strftime("%m-%d", time.localtime(t)) if t else "找不到", val if t else 241)])
    return [loc, lat, upd_at]


def lock_path():
    return os.path.join(seats_dir_lib.state_root()[0], "state", "cli-update.lock")


@contextlib.contextmanager
def update_lock(out=sys.stderr, wait_s=900, path=None):
    """整台機器同一時間只准一個視窗在更新 AI CLI(10-09:兩個終端同時開席、同時倒數到 0,
    兩支 npm install -g 同時改同一個資料夾會把 codex 裝成半套,所有席一起開不起來)。
    拿不到就等(最多 15 分鐘),等的時候說是在等什麼;等不到就不更新,照舊進 aib。"""
    path = path or lock_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    got = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            got = True
        except OSError:
            out.write("⏳ 別的視窗正在更新 AI 程式,等它做完再繼續(最多 %d 分鐘)…\n" % (wait_s // 60))
            out.flush()
            end = time.monotonic() + wait_s
            while time.monotonic() < end and not got:
                time.sleep(1)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    got = True
                except OSError:
                    pass
        yield got
    finally:
        if got:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _tool(key):
    return {t["key"]: t for t in TOOLS}[key]


def _pin_cmd(tool, version):
    return [x.replace("{v}", version) for x in tool["pin"]] if tool.get("pin") else None


def remember_prev(key, prev, *, now=None, cache=None):
    cache = cache_path() if cache is None else cache
    memo = _load(cache)
    ent = memo.get(key) or {}
    ent.update(prev=prev, prev_at=time.time() if now is None else now)
    memo[key] = ent
    _save(cache, memo)


def hold(key, version, *, cache=None):
    """退版之後:這一版不要再催(更新的版本出來才再問)。"""
    cache = cache_path() if cache is None else cache
    memo = _load(cache)
    ent = memo.get(key) or {}
    ent.update(quiet_for=version, quiet_until=4102444800.0)   # 2100 年 = 這一版永遠不催
    memo[key] = ent
    _save(cache, memo)


def do_update(key, old, latest, *, out=sys.stderr, run=None, version_fn=None, cache=None, lock=None):
    """更新一家:拿鎖 → 重量本機(別的視窗可能剛更新好)→ 更新 → 自檢(--version 跑得起來)→ 壞了自動裝回原版。
    回 "already" / "updated" / "rolled_back" / "failed" / "locked"。"""
    tool = _tool(key)
    run = run or (lambda cmd: subprocess.run(cmd, stdin=subprocess.DEVNULL).returncode)
    version_fn = version_fn or local_version
    with (lock or update_lock(out)) as got:
        if not got:
            out.write("⚠ 等不到更新鎖,%s 這次不更新,照舊進 aib\n" % key)
            return "locked"
        cur = version_fn(tool)
        if cur and not newer(latest, cur):
            out.write("✅ %s 已經是 %s(別的視窗剛更新好)\n" % (key, cur))
            return "already"
        out.write("\n▶ 更新 %s %s → %s:%s\n" % (key, cur or old, latest, " ".join(tool["update"])))
        out.flush()
        rc = run(tool["update"])
        new_v = version_fn(tool)
        if new_v is None and (cur or old) and tool.get("pin"):
            back = cur or old
            out.write("🔴 %s 更新後連 --version 都跑不起來,自動裝回 %s:%s\n" % (key, back, " ".join(_pin_cmd(tool, back))))
            out.flush()
            rc2 = run(_pin_cmd(tool, back))
            again = version_fn(tool)
            hold(key, latest, cache=cache)
            out.write(("✅ 已裝回 %s(%s 這一版之後不再催)\n" % (again, latest)) if again == back else
                      ("🔴 裝回也失敗(rc=%s,本機現在是 %s)。手動:%s\n" % (rc2, again, " ".join(_pin_cmd(tool, back)))))
            return "rolled_back"
        if rc == 0 and new_v == latest:
            if cur or old:
                remember_prev(key, cur or old, cache=cache)
            out.write("✅ %s 已更新到 %s(要退回:aib cli rollback %s)\n" % (key, new_v, key))
            return "updated"
        out.write("⚠ %s 更新後本機是 %s(rc=%s),先照舊進 aib;同一版 1 小時內不再問\n" % (key, new_v, rc))
        quiet(key, latest, cache=cache)
        return "failed"


def update_self_checking(key, *, out=sys.stdout, run=None, version_fn=None, lock=None):
    """找不到最新版、但自己的 update 會「已是最新就不動、有新版才裝」的家(agy):直接跑它,前後版本照實寫。
    10-09 實測 `agy update`:「✓ You are already on the latest version.」rc=0。開席倒數不用這條(沒倒數就裝,違反先倒數)。"""
    tool = _tool(key)
    run = run or (lambda cmd: subprocess.run(cmd, stdin=subprocess.DEVNULL).returncode)
    version_fn = version_fn or local_version
    with (lock or update_lock(out)) as got:
        if not got:
            out.write("等不到更新鎖,%s 這次不更新\n" % key)
            return 1
        before = version_fn(tool)
        out.write("▶ %s(現在 %s):%s\n" % (key, before, " ".join(tool["update"])))
        out.flush()
        rc = run(tool["update"])
        after = version_fn(tool)
    if rc != 0 or after is None:
        out.write("🔴 %s 更新 rc=%s,現在版本 %s\n" % (key, rc, after))
        return 1
    out.write(("✅ %s 已更新 %s → %s\n" % (key, before, after)) if after != before else ("✅ %s 已是最新(%s)\n" % (key, after)))
    return 0


def rollback(key, to=None, *, out=sys.stdout, run=None, version_fn=None, cache=None, lock=None):
    """aib cli rollback <家> [--to 版本]:裝回上一次更新前的版本(或指定版本),並把現在那一版標成不再催。"""
    tool = _tool(key)
    if not tool.get("pin"):
        out.write("%s 沒有辦法指定版本安裝,退不回去。\n" % key)
        return 2
    cache = cache_path() if cache is None else cache
    to = to or (_load(cache).get(key) or {}).get("prev")
    if not to:
        out.write("沒有 %s 更新前的版本紀錄(這台還沒用 aib 更新過它)。指定版本:aib cli rollback %s --to <版本>\n" % (key, key))
        return 2
    run = run or (lambda cmd: subprocess.run(cmd, stdin=subprocess.DEVNULL).returncode)
    version_fn = version_fn or local_version
    with (lock or update_lock(out)) as got:
        if not got:
            out.write("等不到更新鎖(別的視窗正在更新),稍後再試。\n")
            return 1
        cur = version_fn(tool)
        out.write("▶ %s %s → %s:%s\n" % (key, cur, to, " ".join(_pin_cmd(tool, to))))
        out.flush()
        rc = run(_pin_cmd(tool, to))
        now_v = version_fn(tool)
    if now_v == to:
        if cur and cur != to:
            hold(key, cur, cache=cache)
        out.write("✅ %s 退回 %s。%s 這一版之後開席不再催;更新的版本出來才會再問。\n" % (key, to, cur))
        return 0
    out.write("🔴 %s 退版後本機是 %s(rc=%s),不是 %s\n" % (key, now_v, rc, to))
    return 1


def _getkey(timeout):
    r, _w, _x = select.select([sys.stdin], [], [], timeout)
    return sys.stdin.read(1) if r else None


def gate(rows, *, seconds=30, out=sys.stderr, getkey=None, run=None, sleep=time.sleep, cache=None,
         version_fn=None, lock=None):
    """有要催的新版 ⇒ 畫 logo+版本+倒數;回 "none" / "skipped" / "updated"。"""
    todo = [r for r in rows if r["nag"] and r["key"] in GATE_KEYS]
    if not todo:
        return "none"
    import login_splash as L
    names = "、".join(r["key"] for r in todo)
    busy = old_seats()
    note = ["%s 有 %d 席正在跑舊版(不會被打斷,下次開席才用新版)" % (r["key"], busy[r["key"]])
            for r in todo if busy.get(r["key"])]
    note += ["aib 本機 %s → 最新 %s(這一席已經開好,下次開席才用新版)" % (r["local"], r["latest"]) for r in todo if r["key"] == "aib"]

    def paint(tail):
        L.paint(L.frame(L.N, False, version_rows(rows))[:-1] + [L.status_line(tail, 214)]
                + [L.status_line(x) for x in note])

    if getkey is None:
        import termios
        import tty
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        getkey = _getkey
    else:
        saved = None
    choice = "update"
    try:
        for left in range(seconds, 0, -1):
            paint("有新版:%s。%2d 秒後自動更新(按 s 跳過、按 Enter 馬上更新)" % (names, left))
            t_end = time.monotonic() + 1
            k = None
            while time.monotonic() < t_end:
                k = getkey(max(0.0, t_end - time.monotonic()))
                if k is not None:
                    break
            if k in ("s", "S"):
                choice = "skip"
                break
            if k in ("\n", "\r", "u", "U"):
                break
    finally:
        if saved is not None:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)
    L.DRAWN = 0                       # 下面的輸出接在畫面之後,不要再往上擦
    if choice == "skip":
        for r in todo:
            quiet(r["key"], r["latest"], cache=cache)
        out.write("已跳過更新(同一版 1 小時內不再問)。\n")
        return "skipped"
    for r in todo:
        do_update(r["key"], r["local"], r["latest"], out=out, run=run, version_fn=version_fn, cache=cache, lock=lock)
    sleep(1.2)
    return "updated"


def _pad(text, width):
    """照終端顯示寬度補空白(中文兩格),表格才不會被「找不到」推歪。"""
    import unicodedata
    w = sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)
    return text + " " * max(0, width - w)


def status(out=sys.stdout):
    rows = check()
    memo = _load(cache_path())
    out.write("AI       本機       最新         最後更新\n")          # 中文兩格寬,表頭手排對齊
    for r in rows:
        mark = "↑ 有新版" if r["update"] else ("✓" if r["latest"] else
                                               ("找不到最新版(aib cli update agy 會自己查、有新版才裝)" if r["key"] == "agy"
                                                else "找不到最新版"))
        prev = (memo.get(r["key"]) or {}).get("prev")
        t = r.get("installed_at")
        out.write("%s %s %s %s %s%s\n" % (
            _pad(r["key"], 8), _pad(r["local"] or "沒裝", 10), _pad(r["latest"] or "找不到", 12),
            _pad(time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "找不到", 17), mark,
            ("   上一版 %s(aib cli rollback %s)" % (prev, r["key"])) if prev else ""))
    if any(r["can_update"] for r in rows):
        out.write("\n更新:aib cli update(或下次親手開席時倒數 30 秒自動更新)\n")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aib cli", description="五家 AI CLI 與 aib 自己的本機/最新版本、更新、退版")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status", help="(預設)看五家本機/最新版本")
    u = sub.add_parser("update", help="現在就更新(不倒數);不寫家名 = 有新版的全部")
    u.add_argument("keys", nargs="*")
    rb = sub.add_parser("rollback", help="退回上一次更新前的版本")
    rb.add_argument("key", choices=[t["key"] for t in TOOLS if t.get("pin")])
    rb.add_argument("--to", help="指定版本")
    c = sub.add_parser("check")
    c.add_argument("--out")
    c.add_argument("--refresh", action="store_true")
    g = sub.add_parser("gate")
    g.add_argument("--in", dest="inp")
    g.add_argument("--seconds", type=int, default=30)
    args = ap.parse_args(argv)
    if args.cmd in (None, "status"):
        return status()
    if args.cmd == "update":
        rows = check(refresh=True)
        todo = [r for r in rows if r["can_update"] and (not args.keys or r["key"] in args.keys)]
        bad = 0
        for r in todo:
            bad += do_update(r["key"], r["local"], r["latest"], out=sys.stdout) not in ("updated", "already")
        if not args.keys or "agy" in args.keys:
            bad += update_self_checking("agy", out=sys.stdout) != 0
        elif not todo:
            print("沒有要更新的(都已是最新,或找不到最新版)。")
        return 1 if bad else 0
    if args.cmd == "rollback":
        return rollback(args.key, args.to)
    if os.environ.get(DISABLE_ENV) == "0":
        return 0
    if args.cmd == "check":
        rows = check(refresh=args.refresh)
        data = json.dumps(rows, ensure_ascii=False)
        if args.out:
            tmp = args.out + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(data)
            os.replace(tmp, args.out)
        else:
            print(data)
        return 0
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        return 0
    rows = None
    if args.inp:
        end = time.monotonic() + 8          # 背景那一份還沒查完就等一下(最多 8 秒),再不行自己查
        while time.monotonic() < end and rows is None:
            try:
                with open(args.inp, encoding="utf-8") as fh:
                    rows = json.load(fh)
            except (OSError, ValueError):
                time.sleep(0.2)
    if rows is None:
        rows = check()
    gate(rows, seconds=args.seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
