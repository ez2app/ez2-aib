#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat advice:散熱建議清單(10-25 @044e0,RULING-room-heat-local-opt 第 2 張)。

  aib room heat advice [--json]    每筆 {code, severity, owner, metric, hint};code 是契約,人話放 hint

熱源大多不是「殘留」(活席的 hook、工作樹位置、一直失敗的排程、並行測試、真人瀏覽器分頁),
白名單收不到也不該收;真人要的是「是誰、為什麼、怎麼改」。七條判法都是 10-25 普查實際用過的:

  worktree_not_noindex   家目錄最上層的 git 工作樹不在 *.noindex 下、24 小時內有寫 ⇒ Spotlight / 檔案監看一直掃
  hook_slow              活席對話紀錄尾巴的 stop_hook_summary:某支 hook 中位數 > 1 秒或接近它的 timeout
  hook_timeout_kill      同上,最近 3 次都 ≥ timeout × 0.98 ⇒ 每回合被砍、從沒跑完
  launchd_failing        使用者 LaunchAgents 末次結束碼 ≠ 0(只給 Label)
  test_parallel          同一席同時 > 1 輪 aib test,或全機 > 3 輪
  browser_tab_hot        瀏覽器內容行程(分頁)兩次取樣都 > 80%
  transcript_huge        活席對話紀錄 > 500 MB

D3:不進每 5 秒的快照;本子指令自己快取 60 秒。對話紀錄只 seek 尾巴(每檔 8 MB、合計上限 60 MB),
只讀 stop_hook_summary 的 durationMs 與標籤,不讀對話內容;禁止全讀(footer-gate 每回合全讀被逾時砍的教訓)。
D4:不出指令列 / cwd / 絕對路徑 / token;hook 只給 statusMessage 或腳本檔名、工作樹只給資料夾名、排程只給 Label。
D5:全部只顯示;搬工作樹 / 卸排程 / 拿 hook 都是改設定 ⇒ 決定卡 + Touch ID,不在這裡做、不進 --auto。
D2:launchd_failing 只在 macOS(其他平台列進 skipped:unsupported_platform)。
"""
from __future__ import annotations

import json
import math
import os
import plistlib
import re
import stat
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

CACHE_FILE = "advice-last.json"
CACHE_S = 60
ACTIVE_S = 30 * 60                   # 對話紀錄 30 分鐘內有寫 = 活席
TAIL_PER_FILE = 8 * 1024 * 1024
TAIL_TOTAL = 60 * 1024 * 1024       # E1 上限是每檔 60 MB、合計 200 MB;這裡更緊
LINE_MAX = 1024 * 1024
HOOK_SLOW_MS = 1000
HOOK_NEAR = 0.8                      # 中位數到 timeout 的 8 成也算慢
HOOK_KILL = 0.98
HOOK_KILL_RUN = 3
WORKTREE_RECENT_S = 24 * 3600
TEST_PER_SEAT = 1
TEST_TOTAL = 3
BROWSER_HOT = 80.0
BROWSER_GAP_S = 2.0
TRANSCRIPT_HUGE_MB = 500
SEAT_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{5})(?![0-9a-f])")
BROWSER_RE = re.compile(r"(plugin-container|Renderer|Web Content|WebContent|Isolated Web Co)", re.I)
SCRIPT_RE = re.compile(r"([\w.\-]+\.(?:py|sh|js|mjs|ts|rb|pl))\b")
CODES = ("worktree_not_noindex", "hook_slow", "hook_timeout_kill", "launchd_failing", "test_parallel",
         "browser_tab_hot", "transcript_huge")


def _T(key, **p):
    import room_heat
    return room_heat.T("advice." + key, **p)


def hint_of(code, metric):
    """E6:hint 是固定字串,只帶數字;名字(hook 標籤、Label、程式名、夾名)只放 metric,頁面用 textContent 畫。"""
    key = code + ("_removed" if code == "hook_slow" and metric.get("removed") else "")
    return _T(key, **{k: v for k, v in metric.items() if _finite(v)})


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _item(code, severity, owner="", **metric):
    return {"code": code, "severity": severity, "owner": owner, "metric": metric}


def seat_of_name(name):
    m = SEAT_RE.search(name or "")
    return m.group(1) if m else ""


# ---------- 1 工作樹 ----------

def worktrees(home, now):
    """家目錄最上層、`.git` 是檔(= 工作樹)、名字不含 .noindex。回 [(資料夾名, 最近寫入秒)]。"""
    out = []
    try:
        ents = list(os.scandir(home))
    except OSError:
        return None
    for e in ents:
        if ".noindex" in e.name or not e.is_dir(follow_symlinks=False):
            continue
        g = os.path.join(e.path, ".git")
        try:
            if not os.path.isfile(g) or os.path.islink(g):
                continue
            with open(g, encoding="utf-8", errors="replace") as fh:
                line = fh.read(4096).strip()
        except OSError:
            continue
        gitdir = line[7:].strip() if line.startswith("gitdir:") else ""
        if gitdir and not os.path.isabs(gitdir):
            gitdir = os.path.join(e.path, gitdir)
        t = 0.0
        for f in ("index", "HEAD", "logs/HEAD"):       # 工作樹有寫 ⇒ index 會動;資料夾本身的 mtime 不準
            try:
                t = max(t, os.stat(os.path.join(gitdir, f)).st_mtime)
            except OSError:
                pass
        out.append((e.name, t))
    return out


def check_worktrees(home, now):
    import room_heat
    wts = worktrees(home, now)
    if wts is None:
        return [], ["worktree_not_noindex"]
    recent = [n for n, t in wts if -60 <= now - t <= WORKTREE_RECENT_S]
    if not wts:
        return [], []
    seats = {}
    for n, _t in wts:
        s = seat_of_name(n) or "-"
        seats[s] = seats.get(s, 0) + 1
    sev = "warn" if recent else "info"
    return [_item("worktree_not_noindex", sev, count=len(wts), recent_24h=len(recent), seats=seats,
                  sample=[room_heat.safe_name(n, 48) for n in sorted(recent or [n for n, _ in wts])[:5]])], []


# ---------- 2/3/7 對話紀錄 ----------

def active_transcripts(projects_dir, now):
    """[(路徑, 大小, 席碼)]:30 分鐘內有寫的 *.jsonl;席碼 = 檔名(session uuid)末五碼。"""
    out = []
    try:
        dirs = list(os.scandir(projects_dir))
    except OSError:
        return None
    for d in dirs:
        if not d.is_dir(follow_symlinks=False):
            continue
        try:
            files = list(os.scandir(d.path))
        except OSError:
            continue
        for f in files:
            if not f.name.endswith(".jsonl") or not f.is_file(follow_symlinks=False):
                continue
            try:
                st = f.stat(follow_symlinks=False)
            except OSError:
                continue
            if -60 <= now - st.st_mtime <= ACTIVE_S:         # 剛寫的檔可能比 now 晚一點
                stem = f.name[:-6]
                out.append((f.path, st.st_size, stem[-5:].lower() if re.fullmatch(r"[0-9a-fA-F-]{36}", stem) else ""))
    return out


def read_tail(path, limit):
    """只 seek 尾巴 limit 位元組;第一行可能被切半 ⇒ 丟掉。讀不了 ⇒ ""。"""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return ""
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():     # E1:只讀自己的一般檔
            return ""
        start = max(0, st.st_size - limit)
        os.lseek(fd, start, os.SEEK_SET)
        chunks, left = [], st.st_size - start
        while left > 0:
            b = os.read(fd, min(left, 1 << 20))
            if not b:
                break
            chunks.append(b)
            left -= len(b)
        raw = b"".join(chunks).decode("utf-8", "replace")
    except OSError:
        return ""
    finally:
        os.close(fd)
    return raw.split("\n", 1)[1] if start > 0 and "\n" in raw else raw


def hook_label(cmd, labels):
    """hookInfos 的 command:有 statusMessage 就是那段字;沒有就是整串指令 ⇒ 只取腳本檔名(不外流指令列)。"""
    import room_heat
    if cmd in labels:
        return room_heat.safe_name(cmd, 48)
    scripts = SCRIPT_RE.findall(cmd or "")
    if scripts:
        return room_heat.safe_name(scripts[-1], 48)
    return room_heat.safe_name(os.path.basename((cmd or "").split()[0]) if cmd else "?", 48)


def hook_timeouts(settings_path):
    """{指令或 statusMessage: timeout 秒};Stop hook 才算。讀不到 ⇒ {}。"""
    out, labels = {}, set()
    try:
        with open(settings_path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError, RecursionError):
        return out, labels
    for m in ((d.get("hooks") or {}).get("Stop") or []) if isinstance(d, dict) else []:
        for h in (m.get("hooks") or []) if isinstance(m, dict) else []:
            if not isinstance(h, dict):
                continue
            t = h.get("timeout") if _finite(h.get("timeout")) else 60
            for k in (h.get("command"), h.get("statusMessage")):
                if isinstance(k, str) and k:
                    out[k] = t
            if isinstance(h.get("statusMessage"), str):
                labels.add(h["statusMessage"])
    return out, labels


def hook_durations(text):
    """[(command, durationMs)…] 照時間順,只看 stop_hook_summary 那一型;其他行不 parse(不讀對話內容)。
    只留最新一筆 summary 裡還有的 hook:已經拿掉的(例:10-25 footer-gate)不再因為舊紀錄被列出來。"""
    out, latest = [], set()
    for line in text.splitlines():
        if len(line) > LINE_MAX or '"stop_hook_summary"' not in line:     # E1:單行 > 1 MB 整行丟
            continue
        try:
            d = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(d, dict) or d.get("subtype") != "stop_hook_summary":
            continue
        latest = set()
        for h in d.get("hookInfos") or []:
            if isinstance(h, dict) and isinstance(h.get("command"), str):
                latest.add(h["command"])
                if _finite(h.get("durationMs")):
                    out.append((h["command"], float(h["durationMs"])))
    return [(c, ms) for c, ms in out if c in latest]


def check_transcripts(projects_dir, settings_path, now, tail_per_file=TAIL_PER_FILE, tail_total=TAIL_TOTAL):
    ts = active_transcripts(projects_dir, now)
    if ts is None:
        return [], ["hook_slow", "hook_timeout_kill", "transcript_huge"]
    timeouts, labels = hook_timeouts(settings_path)
    items, budget = [], tail_total
    for path, size, seat in sorted(ts, key=lambda x: -x[1]):
        mb = size / 1048576.0
        if mb > TRANSCRIPT_HUGE_MB:
            items.append(_item("transcript_huge", "warn", seat, mb=round(mb)))
        if budget <= 0:
            continue
        lim = min(tail_per_file, budget)
        budget -= lim
        by = {}
        for cmd, ms in hook_durations(read_tail(path, lim)):
            by.setdefault(cmd, []).append(ms)
        for cmd, ms in by.items():
            med = statistics.median(ms)
            label = hook_label(cmd, labels)
            if timeouts and cmd not in timeouts:       # 設定裡已經拿掉,這一席還照開對話時的設定在跑
                if med > HOOK_SLOW_MS:
                    items.append(_item("hook_slow", "warn", seat, label=label, median_ms=int(med),
                                       max_ms=int(max(ms)), samples=len(ms), removed=1))
                continue
            tmo = timeouts.get(cmd, 60) * 1000.0
            last = ms[-HOOK_KILL_RUN:]
            if len(last) >= HOOK_KILL_RUN and all(x >= tmo * HOOK_KILL for x in last):
                items.append(_item("hook_timeout_kill", "hot", seat, label=label, runs=len(last),
                                   timeout_ms=int(tmo), max_ms=int(max(ms))))
            elif med > HOOK_SLOW_MS or med >= tmo * HOOK_NEAR:
                items.append(_item("hook_slow", "warn", seat, label=label, median_ms=int(med),
                                   max_ms=int(max(ms)), samples=len(ms), timeout_ms=int(tmo)))
    return items, []


# ---------- 4 排程 ----------

def check_launchd(platform, agents_dir, run=None):
    """使用者 LaunchAgents 的 Label ∩ `launchctl list` 末次結束碼 ≠ 0。只給 Label。"""
    import room_heat
    if platform != "darwin":
        return [], [], ["launchd_failing"]
    labels = set()
    try:
        names = [n for n in os.listdir(agents_dir) if n.endswith(".plist")]
    except OSError:
        names = []
    for n in names:
        try:
            with open(os.path.join(agents_dir, n), "rb") as fh:
                pl = plistlib.load(fh)
            if isinstance(pl, dict) and isinstance(pl.get("Label"), str):
                labels.add(pl["Label"])
        except Exception:  # noqa: BLE001 —— 壞 plist 不擋其他條
            continue
    if not labels:
        return [], [], []
    try:
        out = (run or room_heat._run)(["launchctl", "list"])
    except Exception:  # noqa: BLE001
        return [], ["launchd_failing"], []
    items = []
    for line in (out or "").splitlines():
        f = line.split("\t") if "\t" in line else line.split()
        if len(f) < 3 or f[2] not in labels:
            continue
        pid, status = f[0].strip(), f[1].strip()
        if pid == "-" and re.fullmatch(r"-?\d+", status) and int(status) != 0:
            items.append(_item("launchd_failing", "warn", "", label=room_heat.safe_name(f[2], 64), exit_code=int(status)))
    return items, [], []


# ---------- 5/6 行程 ----------

def test_runs(rows, cls):
    """aib test 主行程(指令列含 aib test、父行程不是 aib test)⇒ [席碼(不是席位起的 ⇒ "")]。"""
    by = {r[0]: r for r in rows}
    out = []
    for pid, ppid, _uid, _cpu, _rss, _name, args in rows:
        if not re.search(r"\baib(\.py)?\s+test\b", args):
            continue
        if ppid in by and re.search(r"\baib(\.py)?\s+test\b", by[ppid][6]):
            continue
        g, owner = cls.get(pid, ("other_app", ""))
        out.append(owner if g in ("aib_test", "seat") else "")
    return out


def check_tests(rows, cls):
    runs = test_runs(rows, cls)
    items, per = [], {}
    for s in runs:
        per[s or "-"] = per.get(s or "-", 0) + 1
    for s, n in sorted(per.items()):
        if s not in ("-", "?") and n > TEST_PER_SEAT:      # 認不出席位(?)的可能是不同席,不算同一席並行
            items.append(_item("test_parallel", "warn", s, runs=n))
    if len(runs) > TEST_TOTAL:
        items.append(_item("test_parallel", "warn", "", runs=len(runs), seats=per))
    return items


def browser_hot(rows, uid):
    import room_heat
    return {(r[0], room_heat.safe_name(r[5], 32)): r[3] for r in rows
            if r[2] == uid and r[3] > BROWSER_HOT and BROWSER_RE.search(r[5] + " " + r[6].split(" --", 1)[0])}


def check_browser(rows1, rows2, uid):
    a, b = browser_hot(rows1, uid), browser_hot(rows2, uid)
    return [_item("browser_tab_hot", "warn", "", program=k[1], cpu=round(min(a[k], b[k]), 1))
            for k in sorted(set(a) & set(b))]


# ---------- 合起來 ----------

def collect(now=None, home=None, platform=None, list_procs=None, classify=None, run=None, sleep=time.sleep,
            clock=time.monotonic, uid=None):
    import room_heat
    now = time.time() if now is None else now
    home = home or os.path.expanduser("~")
    platform = platform or sys.platform
    uid = os.getuid() if uid is None else uid
    lp = list_procs or room_heat.list_procs
    t0 = clock()
    rows1 = lp()
    items, unmeasured, skipped = [], [], []
    i, u = check_worktrees(home, now)
    items += i
    unmeasured += u
    i, u = check_transcripts(os.path.join(home, ".claude", "projects"), os.path.join(home, ".claude", "settings.json"), now)
    items += i
    unmeasured += u
    i, u, s = check_launchd(platform, os.path.join(home, "Library", "LaunchAgents"), run=run)
    items += i
    unmeasured += u
    skipped += s
    if rows1 is None:
        unmeasured += ["test_parallel", "browser_tab_hot"]
    else:
        cls = (classify or room_heat.classify)(rows1)
        items += check_tests(rows1, cls)
        left = BROWSER_GAP_S - (clock() - t0)
        if left > 0:
            sleep(left)
        rows2 = lp()
        if rows2 is None:
            unmeasured.append("browser_tab_hot")
        else:
            items += check_browser(rows1, rows2, uid)
    for it in items:
        it["hint"] = hint_of(it["code"], it["metric"])
    order = {"hot": 0, "warn": 1, "info": 2}
    items.sort(key=lambda x: (order.get(x["severity"], 9), CODES.index(x["code"])))
    return {"schema": "aib-heat-advice/v1", "state": "ok", "t": round(now, 1), "items": items,
            "unmeasured": [{"code": c, "detail": "measure_failed"} for c in unmeasured],
            "skipped": [{"code": c, "detail": "unsupported_platform"} for c in skipped]}


def advice(now=None, environ=None, refresh=False, **kw):
    """60 秒快取(D3):頁面怎麼讀都最多 1 分鐘掃一次。"""
    import room_heat
    import room_heat_unseen as RU
    now = time.time() if now is None else now
    path = os.path.join(room_heat.state_dir(environ), CACHE_FILE)
    if not refresh:
        c = RU._read_json(path)
        if c and _finite(c.get("t")) and 0 <= now - c["t"] < CACHE_S and c.get("schema") == "aib-heat-advice/v1":
            c = _clean(c)
            if c is not None:
                c["cached"] = True
                return c
    r = collect(now=now, **kw)
    try:
        RU._write_json(path, r)
    except (OSError, ValueError):
        pass
    r["cached"] = False
    return r


def _clean(c):
    """快取檔同帳號可改:code 要在表上、名字再洗、hint 重翻(不信檔裡的字)。"""
    import room_heat
    items = []
    for it in c.get("items") if isinstance(c.get("items"), list) else []:
        if not isinstance(it, dict) or it.get("code") not in CODES or it.get("severity") not in ("hot", "warn", "info"):
            continue
        m = it.get("metric") if isinstance(it.get("metric"), dict) else {}
        clean = {}
        for k, v in m.items():
            k = room_heat.safe_name(k, 24)
            if _finite(v):
                clean[k] = v
            elif isinstance(v, str):
                clean[k] = room_heat.safe_name(v, 64)
            elif isinstance(v, list):
                clean[k] = [room_heat.safe_name(x, 48) for x in v[:5] if isinstance(x, str)]
            elif isinstance(v, dict):
                clean[k] = {room_heat.safe_name(a, 12): b for a, b in list(v.items())[:50] if _finite(b)}
        x = {"code": it["code"], "severity": it["severity"], "owner": room_heat.safe_name(it.get("owner"), 12),
             "metric": clean}
        x["hint"] = hint_of(x["code"], clean)
        items.append(x)
    return {"schema": "aib-heat-advice/v1", "state": "ok", "t": c["t"], "items": items,
            "unmeasured": [{"code": u["code"], "detail": "measure_failed"} for u in c.get("unmeasured") or []
                           if isinstance(u, dict) and u.get("code") in CODES],
            "skipped": [{"code": u["code"], "detail": "unsupported_platform"} for u in c.get("skipped") or []
                        if isinstance(u, dict) and u.get("code") in CODES]}
