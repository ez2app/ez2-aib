#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""判斷「對方那台電腦是不是在睡」(10-18 Andy:Mac Pro 13 閒置 1 分鐘就睡,寄過去的信只能等它醒;「要能判斷該電腦休眠」)。
只用標準庫。

睡的那一台(自己):
- power_info():這台會不會自己睡(pmset 的 sleep 分鐘數)、有沒有接電源、有沒有被轉接程式用 caffeinate 撐著醒。
- SleepWatch:轉接程式每一輪看牆上時鐘,兩輪之間跳超過 SLEEP_GAP_S ⇒ 剛睡過一覺,記進 <root>/power.json(最近 20 段),
  醒來那一刻呼叫端立刻補送一次在線狀態(好友那邊很快就看到醒了)。
- 在線狀態裡多一欄 power(只給本來就收得到在線狀態的好友 / 我的其他機器):
  {sleeps, sleep_min, ac, kept_awake, last_wake, beat}。beat = 沒變也多久送一次。

看的那一台(好友):
- machine_state(在線狀態那一格, now) ⇒ awake / asleep / offline / unknown + since:
  在 beat 的 1.5 倍 + 2 分鐘內收到過 ⇒ awake;超過了而對方說自己會睡 ⇒ asleep(從最後一次收到算起);
  超過了而對方說自己被撐著醒(不會睡)⇒ offline(不正常:斷網、關機、轉接程式停了);沒有 power 欄(舊版)⇒ unknown。

撐著醒(aib chat awake on|off,預設 on;只在 Mac):轉接程式當班時跑 `caffeinate -s -w <自己的 pid>`。
-s 只在接電源時擋系統睡眠,用電池時照常睡、螢幕照常關;轉接程式結束 caffeinate 跟著結束。
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from atomic_json import write_json  # noqa: E402

SLEEP_GAP_S = 90
SPANS_MAX = 20
PMSET = "/usr/bin/pmset"
CAFFEINATE = "/usr/bin/caffeinate"
STATES = ("awake", "asleep", "offline", "unknown")


# ── 自己這台 ──

def _pmset(*args, run=subprocess.run):
    try:
        r = run([PMSET] + list(args), capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def parse_sleep_min(text):
    """`pmset -g` 的 ` sleep  1 (sleep prevented by …)` ⇒ 1;0 = 不自己睡;讀不到 ⇒ None。"""
    m = re.search(r"^\s*sleep\s+(\d+)", text or "", re.M)
    return int(m.group(1)) if m else None


def parse_ac(text):
    """`pmset -g batt` 第一行 `Now drawing from 'AC Power'` ⇒ True;Battery ⇒ False;讀不到 ⇒ None。"""
    if "'AC Power'" in (text or ""):
        return True
    if "'Battery Power'" in (text or ""):
        return False
    return None


def keep_path(root):
    return Path(root) / "power-keep.json"


def kept_awake(root):
    """轉接程式的 caffeinate 還活著嗎。"""
    try:
        pid = int(json.loads(keep_path(root).read_text(encoding="utf-8")).get("pid") or 0)
    except (OSError, ValueError, AttributeError, TypeError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def power_info(root, platform=None, pmset=None):
    """這台的電源與睡眠設定。非 Mac ⇒ sleeps None(不知道,不亂講)。pmset 只給測試換掉。"""
    platform = sys.platform if platform is None else platform
    if platform != "darwin":
        return {"sleeps": None, "sleep_min": None, "ac": None, "kept_awake": False}
    get = pmset or _pmset
    sleep_min, ac = parse_sleep_min(get("-g")), parse_ac(get("-g", "batt"))
    keep = kept_awake(root)
    sleeps = None if sleep_min is None else (sleep_min > 0 and not (keep and ac is not False))
    return {"sleeps": sleeps, "sleep_min": sleep_min, "ac": ac, "kept_awake": keep}


def power_path(root):
    return Path(root) / "power.json"


def read_power(root):
    try:
        rec = json.loads(power_path(root).read_text(encoding="utf-8"))
        return rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):
        return {}


class SleepWatch(object):
    """每一輪 tick(now);兩輪之間牆上時鐘跳超過 gap ⇒ 記一段睡眠、回 True(呼叫端立刻補送在線狀態)。"""

    def __init__(self, root, gap=SLEEP_GAP_S, clock=time.time):
        self.root, self.gap, self.clock = Path(root), gap, clock
        self.last = None

    def tick(self, now=None):
        now = self.clock() if now is None else now
        woke = self.last is not None and now - self.last > self.gap
        if woke:
            rec = read_power(self.root)
            spans = [s for s in rec.get("spans") or [] if isinstance(s, dict)][-(SPANS_MAX - 1):]
            spans.append({"from": int(self.last), "to": int(now)})
            write_json(str(power_path(self.root)), {"v": 1, "spans": spans, "last_wake": int(now)})
        self.last = now
        return woke


def presence_power(root, beat, platform=None, pmset=None):
    """在線狀態裡那一欄。"""
    info = power_info(root, platform=platform, pmset=pmset)
    info["last_wake"] = read_power(root).get("last_wake")
    info["beat"] = int(beat)
    return info


def clean_power(p):
    """收到別人的 power 欄 ⇒ 只留認得的欄位與型別;不合格 ⇒ None。"""
    if not isinstance(p, dict):
        return None
    out = {}
    for k in ("sleeps", "ac", "kept_awake"):
        v = p.get(k)
        out[k] = v if isinstance(v, bool) or v is None else None
    for k, lo, hi in (("sleep_min", 0, 10000), ("last_wake", 0, 1 << 34), ("beat", 1, 86400)):
        v = p.get(k)
        out[k] = v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None
    return out


# ── 看別人 ──

def machine_state(cell, now):
    """world.json 的一格(一位主人的一個艙)⇒ {state, since, sleeps, sleep_min, ac, kept_awake, last_wake}。"""
    at = int((cell or {}).get("presence_at") or 0)
    p = clean_power((cell or {}).get("power")) or {}
    beat = p.get("beat") or 900
    out = {"state": "unknown", "since": at or None}
    out.update({k: p.get(k) for k in ("sleeps", "sleep_min", "ac", "kept_awake", "last_wake")})
    if not at:
        return out
    if now - at <= beat * 1.5 + 120:
        out["state"] = "awake"
        out["since"] = p.get("last_wake") or None
    elif p.get("sleeps") is True:
        out["state"] = "asleep"
    elif p.get("sleeps") is False:
        out["state"] = "offline"
    return out


def owner_state(cells, now):
    """同一位主人的幾個艙(可能在不同台)⇒ 有一台醒著就算 awake;否則取最近一次收到的那台。"""
    states = [machine_state(c, now) for c in cells or []]
    if not states:
        return {"state": "unknown", "since": None}
    awake = [s for s in states if s["state"] == "awake"]
    if awake:
        return awake[0]
    return max(states, key=lambda s: s.get("since") or 0)


# ── 撐著醒(轉接程式當班時)──

def awake_setting_path(chat_home):
    return Path(chat_home) / "awake.json"


def awake_wanted(chat_home):
    """預設 on(Andy 10-18「全做」);aib chat awake off 才關。"""
    try:
        return json.loads(awake_setting_path(chat_home).read_text(encoding="utf-8")).get("on") is not False
    except (OSError, ValueError, AttributeError):
        return True


def set_awake(chat_home, on):
    p = awake_setting_path(chat_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json(str(p), {"v": 1, "on": bool(on), "at": int(time.time())})


def start_keep_awake(root, chat_home, platform=None, popen=subprocess.Popen):
    """Mac + 設定 on ⇒ 起 caffeinate -s -w <自己>,記 pid;回 Popen 或 None。"""
    platform = sys.platform if platform is None else platform
    if platform != "darwin" or not awake_wanted(chat_home) or not os.path.exists(CAFFEINATE):
        try:
            keep_path(root).unlink()
        except FileNotFoundError:
            pass
        return None
    try:
        p = popen([CAFFEINATE, "-s", "-w", str(os.getpid())], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL)
    except OSError:
        return None
    write_json(str(keep_path(root)), {"v": 1, "pid": p.pid, "at": int(time.time())})
    return p


def stop_keep_awake(root, proc):
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    try:
        keep_path(root).unlink()
    except FileNotFoundError:
        pass
