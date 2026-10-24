#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""艦長動力室:AI 燈號(登入 / 額度)、aib power login、新手任務(契約 contracts/power.json)。

  light_of(key)        -> {light, reason, hint, reset_at, login_cmd}   給 power_status.ai_rows 組 ais
  onboarding_state()   -> {birth_done, tasks}                          給 power_status.onboarding
  main(argv)           aib power login <家> [--no-open] [--json]      起那家 CLI 自己的登入,抓它印的 https 鏈結

10-24 Andy:「過期或到上限的 ai 要變紅燈、按下去就跳出登入鏈結,預設自動開啟、沒有要能一鍵 copy」。
- 登入狀態只問各家 CLI 自己的狀態指令(claude auth status / codex login status),只取「登入著沒有」,
  帳號 / 信箱 / token 一律不讀不回。沒有狀態指令的家照實回 unknown,不猜。
- 額度只讀 usage_probe 已量好的紀錄(不另打網路):新鮮且任一窗 >= 上限 ⇒ red quota_reached + reset_at。
- login 只起 CLI 自己的流程;鏈結由它產生,我們不經手帳密 / token。完成 OAuth 一定是真人在瀏覽器裡做。
- 新手任務(課程 R / SR / SSR / UR)在 power_tasks.py:「做完」由核心實際偵測,偵測到就記下(之後不會變回未完成)。
回條 detail / reason 是契約代碼,人話只在 hint。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import seats_dir_lib  # noqa: E402

KEYS = ("claude", "codex", "grok", "copilot", "agy")
LOGIN_TTL_S = 600            # 登入狀態快取 10 分鐘(跟健康檢查同);login 成功後清掉
STATUS_TIMEOUT_S = 15
URL_WAIT_S = 20              # 等 CLI 印出鏈結最多幾秒
LOGIN_LIFETIME_S = 900       # 背景登入流程最多活 15 分鐘(裝置碼通常 15 分鐘過期)

# 各家登入指令(本人要跑的那一行,給頁面一鍵複製)與背景抓鏈結用的指令。
#   有裝置碼流程的家優先用它:CLI 印「網址 + 代碼」後輪詢,不需要本機回呼埠、也不靠 CLI 自己開瀏覽器。
LOGIN = {
    "claude": {"cmd": "claude auth login", "spawn": ["claude", "auth", "login"], "self_open": True},
    "codex": {"cmd": "codex login", "spawn": ["codex", "login", "--device-auth"]},
    "grok": {"cmd": "grok login", "spawn": ["grok", "login", "--device-auth"]},
    "copilot": {"cmd": "copilot login", "spawn": ["copilot", "login", "--device-code"]},
    "agy": {"cmd": None, "spawn": None},          # agy 沒有登入子指令:開 agy 時自己走
}

_URL_RE = re.compile(r"https://[A-Za-z0-9\-._~:/?#\[\]@!$&()*+,;=%]+")   # 只收 ASCII 網址字元:全形 / 同形字不算進鏈結
_CODE_RE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4,5})\b")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_URL_MAX = 2048


def state_dir():
    return os.path.join(seats_dir_lib.state_root()[0], "state")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            got = json.load(f)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path, obj):
    import atomic_json   # 暫存檔名帶隨機碼 + O_EXCL + 0600:不跟著別人先放的連結寫(#293 審查)
    try:
        atomic_json.write_json(path, obj)
    except (OSError, atomic_json.AtomicWriteError):
        pass


def _run(cmd, timeout=STATUS_TIMEOUT_S):
    """(rc, stdout);找不到 / 逾時 / 起不來 ⇒ (None, "")。"""
    if not shutil.which(cmd[0]):
        return None, ""
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                           errors="replace")
        return p.returncode, p.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return None, ""


# ---------- 登入狀態 ----------

def _claude_logged_in(run):
    rc, out = run(["claude", "auth", "status", "--json"])
    if rc is None:
        return None
    try:
        got = json.loads(out)
    except ValueError:
        return None
    v = got.get("loggedIn") if isinstance(got, dict) else None
    return v if isinstance(v, bool) else None      # 只取這一格;信箱 / 組織不讀


def _codex_logged_in(run):
    rc, _ = run(["codex", "login", "status"])      # rc 0 = 登入著;非 0 = 沒登入
    return None if rc is None else rc == 0


PROBES = {"claude": _claude_logged_in, "codex": _codex_logged_in}


def logged_in(key, *, run=None, refresh=False, now=None):
    """True / False / None(這一家沒狀態指令或量不到)。結果快取 LOGIN_TTL_S。"""
    probe = PROBES.get(key)
    if probe is None:
        return None
    now = time.time() if now is None else now
    path = os.path.join(state_dir(), "power-login.json")
    memo = _load(path)
    row = memo.get(key) if isinstance(memo.get(key), dict) else {}
    at = row.get("at")
    if not refresh and isinstance(at, (int, float)) and 0 <= now - at < LOGIN_TTL_S \
            and row.get("v") in (True, False, None):
        return row.get("v")
    try:
        v = probe(run or _run)
    except Exception:  # noqa: BLE001 - 狀態指令輸出怪 ⇒ 量不到
        v = None
    memo[key] = {"at": now, "v": v}
    _save(path, memo)
    return v


def forget_login(key):
    path = os.path.join(state_dir(), "power-login.json")
    memo = _load(path)
    if memo.pop(key, None) is not None:
        _save(path, memo)


# ---------- 額度 ----------

def quota(key, *, records=None, now=None):
    """(到上限沒有, reset_at)。只看新鮮(<= STALE_S)且量到的紀錄;任一窗 >= 上限就算到上限。

    reset_at 取到上限的窗裡最晚恢復的那個(全部窗都恢復才真的能用);量不到時間給 None。
    """
    try:
        import usage_probe as UP
    except ImportError:
        return False, None
    now = time.time() if now is None else now
    try:
        recs = UP.load() if records is None else records
    except Exception:  # noqa: BLE001
        return False, None
    hit, reset = False, None
    for rec in recs or []:
        if not isinstance(rec, dict) or rec.get("engine") != key or rec.get("status") != "ok":
            continue
        pct = rec.get("used_pct")
        if isinstance(pct, bool) or not isinstance(pct, int) or not 0 <= pct <= 100:
            continue
        try:
            age = UP._observed_age_s(rec, now)
        except Exception:  # noqa: BLE001
            age = None
        if age is None or age > UP.STALE_S or pct < UP.USAGE_BLOCK_PCT:
            continue
        hit = True
        ra = rec.get("resets_at")
        if isinstance(ra, (int, float)) and not isinstance(ra, bool) and ra > now:
            reset = int(ra) if reset is None else max(reset, int(ra))
    return hit, reset


# ---------- 燈號 ----------

def light_of(key, *, run=None, records=None, now=None, refresh=False):
    """{light, reason, hint, reset_at, login_cmd}。裝沒裝由總管判(這裡只管裝了的家)。

    順序:沒登入 ⇒ yellow;額度到上限 ⇒ red;登入著 ⇒ green;量不到登入 ⇒ unknown(額度到上限仍給 red)。
    """
    if key not in KEYS:
        return {"light": "unknown", "reason": "probe_failed", "hint": None, "reset_at": None, "login_cmd": None}
    cmd = LOGIN[key]["cmd"]
    li = logged_in(key, run=run, refresh=refresh, now=now)
    if li is False:
        return {"light": "yellow", "reason": "login_missing", "hint": cmd, "reset_at": None, "login_cmd": cmd}
    hit, reset = quota(key, records=records, now=now)
    if hit:
        return {"light": "red", "reason": "quota_reached", "hint": None, "reset_at": reset, "login_cmd": cmd}
    if li is True:
        return {"light": "green", "reason": None, "hint": None, "reset_at": None, "login_cmd": None}
    return {"light": "unknown", "reason": None, "hint": None, "reset_at": None, "login_cmd": cmd}


# ---------- 新手任務 ----------
# 課程(R / SR / SSR / UR)與偵測在 power_tasks.py;這裡只留總管要的介面。

def onboarding_state(**kw):
    import power_tasks
    return power_tasks.onboarding_state(**kw)


# ---------- aib power login ----------

def _clean_url(s):
    s = _ANSI_RE.sub("", s or "")
    m = _URL_RE.search(s)
    if not m:
        return None
    url = m.group(0).rstrip(".,;:)]}")
    return url if len(url) <= _URL_MAX else None


def _guard_argv(spawn, lifetime):
    """包一層:CLI 最多活 lifetime 秒,時間到砍掉(真人沒完成 OAuth 也不會留孤兒)。"""
    code = ("import subprocess,sys\n"
            "p=subprocess.Popen(sys.argv[2:],stdin=subprocess.DEVNULL)\n"
            "try:\n p.wait(int(sys.argv[1]))\n"
            "except subprocess.TimeoutExpired:\n p.kill()\n")
    return [sys.executable, "-c", code, str(int(lifetime))] + list(spawn)


def spawn_login(key, *, popen=subprocess.Popen, wait_s=URL_WAIT_S, lifetime=LOGIN_LIFETIME_S, sleep=time.sleep,
                clock=time.monotonic):
    """起 CLI 的登入流程(背景,自己一個行程組),等它印出第一個 https 鏈結。回 (url, code, 錯誤代碼)。"""
    spawn = LOGIN[key]["spawn"]
    if not shutil.which(spawn[0]):
        return None, None, "cli_missing"
    log = os.path.join(state_dir(), "power-login-%s.log" % key)
    try:
        os.makedirs(os.path.dirname(log), exist_ok=True)
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        return None, None, "spawn_failed"
    try:
        env = dict(os.environ, NO_COLOR="1")
        popen(_guard_argv(spawn, lifetime), stdin=subprocess.DEVNULL, stdout=fd, stderr=fd, env=env,
              start_new_session=True)
    except (OSError, ValueError):
        return None, None, "spawn_failed"
    finally:
        os.close(fd)
    deadline = clock() + wait_s
    while True:
        try:
            with open(log, encoding="utf-8", errors="replace") as f:
                text = f.read(65536)
        except OSError:
            text = ""
        url = _clean_url(text)
        if url:
            m = _CODE_RE.search(_ANSI_RE.sub("", text))
            _drop(log)                   # 鏈結 / 裝置碼拿到就刪:不留在狀態夾給人翻(CLI 照寫它手上的 fd)
            return url, (m.group(1) if m else None), None
        if clock() >= deadline:
            _drop(log)
            return None, None, "no_url"
        sleep(0.5)


def _drop(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def open_url(url, *, run=subprocess.run, system=None):
    """用預設瀏覽器開;開不了回 False(頁面就顯示鏈結 + 一鍵複製)。"""
    system = system or sys.platform
    opener = "open" if system == "darwin" else ("xdg-open" if system.startswith("linux") else None)
    if not opener or not shutil.which(opener):
        return False
    try:
        return run([opener, url], stdin=subprocess.DEVNULL, capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def login(key, *, no_open=False, force=False, spawn=None, opener=None, run=None):
    """回 (回條, rc)。已登入且沒到上限 ⇒ power_login_done(不起流程,除非 force)。"""
    def receipt(ok, detail, url=None, opened=False, code=None, hint=None):
        return {"ok": ok, "detail": detail, "key": key, "url": url, "code": code, "opened": opened, "hint": hint}

    if key not in KEYS or not LOGIN[key]["spawn"]:
        return receipt(False, "power_login_unsupported", hint="/".join(k for k in KEYS if LOGIN[k]["spawn"])), 2
    if not force:
        lt = light_of(key, run=run, refresh=True)
        if lt["light"] == "green":
            return receipt(True, "power_login_done"), 0
    url, code, err = (spawn or spawn_login)(key)
    if not url:
        return receipt(False, "power_login_failed", hint="%s (%s)" % (LOGIN[key]["cmd"], err or "no_url")), 1
    forget_login(key)                              # 下一次 status 重問,真人登完就轉綠
    # claude 沒有裝置碼流程,它自己會開瀏覽器(等本機回呼);我們再開一次會多一個分頁 ⇒ 不開,頁面照樣給鏈結 + 複製
    opened = False if (no_open or LOGIN[key].get("self_open")) else bool((opener or open_url)(url))
    return receipt(True, "power_login_url", url=url, opened=opened, code=code), 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(prog="aib power login")
    p.add_argument("key")
    p.add_argument("--no-open", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--json", action="store_true")
    try:
        a = p.parse_args(argv)
    except SystemExit:
        return 2
    r, rc = login(a.key, no_open=a.no_open, force=a.force)
    print(json.dumps(r, ensure_ascii=False) if a.json else json.dumps(r, ensure_ascii=False, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())
