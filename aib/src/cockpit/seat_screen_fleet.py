#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""每席一台電腦(S1)艦長級圖表段:fleet_charts(state_root, cabin, now) 回 aib-seat-screen/v1 的 "captain" 那一塊。

形狀照 S1-SHAPES 的 captain 列:
    shadow / seats / lease / heat / river / waterfall / chord / funnel / decide / beat / sankey
另加 "missing"(量不到的鍵名 + 原因)與 "took_ms"(每段毫秒)。

規矩(與 seat_screen.py 同一套):
- 只讀;不連網、不 fetch;子程序一律帶逾時。
- 本艙的席只看 <狀態根>/instances/<艙>/seats(死艙留著同席碼的舊席位檔,不跨艙找)。
- 席碼一律 seat_address.code_of;狀態根一律 seats_dir_lib.state_root;租約帳本路徑走 host_lease。
- 每一段各自 try/except、各自時間預算;失敗 ⇒ 那個鍵留 null、鍵名進 missing,整支不拋例外。
- 大檔逐行串流,先用便宜的位元組比對 / 正則篩掉窗外的列,才 json.loads;只開窗內改過的檔。
- 輸出只有機器鍵與數字;人看的字只照抄現有資料(dm 內文、人格卡名),本檔不寫說明文。

演算法照搬網頁示範(capdemo extract.py / extract2.py / extract4.py),不 import 示範檔。
"""
from __future__ import annotations

import ast
import collections
import concurrent.futures as cf
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import seat_address  # noqa: E402
import seats_dir_lib  # noqa: E402
import seat_screen  # noqa: E402  (repo_root / fetch_main / shadow_dir / _load 單一來源)

SCHEMA = "aib-seat-screen/v1#captain"
DEADLINE_S = 1.8          # 整塊最多等這麼久(不能拖慢效能)
SUB_TIMEOUT_S = 1.5       # 每支子程序的逾時
DECIDE_TEXT_MAX = 70      # extract.py:決定卡內文前 70 字
OTHER = "_other"          # 和弦圖「本艙以外」那一格的機器鍵

# 紅燈去向的區域鍵(extract.py area() 的同一張對照表;顯示字由畫面端對照,這裡只出機器鍵)
_AREAS = (("captain", "captain"), ("ship", "ship"), ("pushgate", "ship"), ("lease", "lease"), ("i18n", "i18n"),
          ("dm", "dm"), ("handoff", "handoff"), ("persona", "persona"), ("memory", "memory"), ("mod", "mod"),
          ("bot", "bot"), ("ci", "ci"), ("ssh", "ssh"))

_RX_EPOCH = re.compile(rb'"epoch":\s*(\d+)')
_RX_TS = re.compile(rb'"ts":\s*(\d+)')
_RX_PG = re.compile(rb'"test":\s*"([^"]+)",\s*"rc":\s*(-?\d+|null)')
_RX_PG_TIME = re.compile(rb'"time":\s*"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)"')
_RX_SENDER = re.compile(rb'"sender_seat":\s*"([0-9a-f]{5})"')


class _Budget(Exception):
    """段內迴圈超過自己的時間預算。"""


def _check(deadline):
    if time.monotonic() > deadline:
        raise _Budget("budget")


def _iso(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _run(argv, timeout=SUB_TIMEOUT_S):
    """同 seat_screen._run(逾時在等待端 kill;收尾時殺掉還活著的子程序)。"""
    return seat_screen._run(argv, timeout=timeout)


def _git(repo, *args, timeout=SUB_TIMEOUT_S):
    """同 seat_screen._git_raw(--no-optional-locks、全模組同時最多 6 支 git);回原文不 strip。"""
    rc, out = seat_screen._git_raw(repo, *args, timeout=timeout)
    return out if rc == 0 else None


def _recent(path, cut):
    try:
        return os.stat(path).st_mtime >= cut
    except OSError:
        return False


# ---------------------------------------------------------------- 本艙席位
def cabin_seats(root, cabin):
    """本艙還在的席:[(席碼, 紀錄)],照檔名排。退休 / 已退出 / 沒 session_uuid 的不算(與 seat-info 名冊同一條)。"""
    out = []
    for f in sorted(glob.glob(os.path.join(root, "instances", cabin, "seats", "w*.json"))):
        d = seat_screen._load(f)
        if not d or d.get("retired") or d.get("exited_at_epoch") or not d.get("session_uuid"):
            continue
        c = seat_address.code_of(d)
        if c:
            out.append((c, d))
    return out


def captain_code(root, cabin, seats):
    """艦長:單一來源在 seat_screen.captain_code(ai-rowmap.json 的 👑;沒有 ⇒ 唯一一席 root 家族;再沒有 ⇒ None)。"""
    return seat_screen.captain_code(root, cabin, seats)


def _dm_env(root):
    """dm_queue 讀狀態根走環境;給的狀態根跟現行不同時才換一份環境(變數名取 seats_dir_lib 的名單,不另寫)。"""
    if os.path.abspath(root) == os.path.abspath(seats_dir_lib.state_root()[0]):
        return None
    env = dict(os.environ)
    env[seats_dir_lib.STATE_ROOT_ENV[0]] = root
    return env


def _tabs(root, code):
    import dm_queue
    return dm_queue.tabs_payload(code, env=_dm_env(root))


def _idle_map(cabin):
    """窗號 → tmux 最後活動 epoch(單一來源 seat_screen.window_activity:cabin_resolve.sh --hub,不在這裡拼)。"""
    return seat_screen.window_activity(cabin)


def _ctx(rec, cabin):
    """右欄同一把(seat-info --fleet):ctx_by_seat.resolve_live_session 找 /clear 後的現任 session,再 read_machine_ctx。"""
    # src/rotation 同時有 rotation.py 與同名目錄:先試 rotation.lifecycle 會留下空的命名空間包害 lifecycle 自己的
    # `from rotation import …` 失敗,所以直接把 src/rotation 放上路徑、只 import lifecycle。
    rot = os.path.join(os.path.dirname(HERE), "rotation")
    if rot not in sys.path:
        sys.path.insert(0, rot)
    from ctx_by_seat import resolve_live_session
    from lifecycle import read_machine_ctx
    live, _rebound, _note = resolve_live_session(rec.get("session_uuid"), instance=cabin, window=rec.get("window_id"),
                                                 since_ms=(rec.get("started_epoch") or 0) * 1000 or None)
    c = read_machine_ctx(live)
    return c.get("pct") if c else None


def _persona_names(root, seats):
    """seat_id → 延續檔 persona_uid → cards.json display_name(與 seat_screen.fetch_persona 同一條路)。"""
    ident = os.path.abspath(os.path.join(HERE, "..", "identity"))
    if ident not in sys.path:
        sys.path.insert(0, ident)
    import bind_one
    cards = (seat_screen._load(os.path.join(root, "identity", "cards.json")) or {}).get("cards") or {}
    out = {}
    for c, rec in seats:
        try:
            uid = (bind_one.read_continuity(root, rec.get("seat_id")) or {}).get("persona_uid") if rec.get("seat_id") else None
        except Exception:  # noqa: BLE001 —— 一席讀壞不拖累整列
            uid = None
        out[c] = (cards.get(uid) or {}).get("display_name") if uid else None
    return out


def block_seats(root, cabin, seats, tabs, now, deadline):
    """extract2 / seat-info --fleet:每席 code/prov/name/ctx/idle_s/unread/now_state/stale。"""
    import seat_now
    try:
        act = _idle_map(cabin)
    except Exception:  # noqa: BLE001 —— tmux 量不到:idle_s 為 null,席照列
        act = {}
    names = _persona_names(root, seats)
    out = []
    for c, rec in seats:
        _check(deadline)
        try:
            ctx = _ctx(rec, cabin)
        except Exception:  # noqa: BLE001
            ctx = None
        a = act.get(rec.get("window_id") or "")
        nrec = seat_now.read(c, state_root=root) or {}
        t = tabs.get(c)
        out.append({"code": c, "prov": rec.get("provider"), "name": names.get(c), "ctx": ctx,
                    "idle_s": round(now - int(a)) if a and a.isdigit() else None,
                    "unread": (t.get("badges") or {}).get("unread") if t else None,
                    "now_state": ("idle" if nrec.get("idle") else nrec.get("state")) if nrec else None,
                    "stale": bool(seat_now.is_stale_busy(nrec, now)) if nrec else False})
    return out


def block_decide(captain, tabs):
    """extract.py:艦長席待審頁 kind=decide 的卡。"""
    if not captain or captain not in tabs:
        raise RuntimeError("no_captain")
    rows = ((tabs[captain].get("tabs") or {}).get("waiting") or {}).get("rows") or []
    return [{"since": x.get("ts"), "text": re.sub(r"\s+", " ", x.get("msg") or "")[:DECIDE_TEXT_MAX], "from": x.get("sender")}
            for x in rows if x.get("kind") == "decide"]


# ---------------------------------------------------------------- 影子帳本(shadow / river / waterfall)
def load_stats(sdir, deadline):
    """test-stats + ship-stats 全部(影子續跑從有紀錄起累積)。回 (events, ship_rows, by_feature 加總)。"""
    ev, ships, feat = [], [], collections.Counter()
    try:
        fh = open(os.path.join(sdir, "test-stats.jsonl"), "rb")
    except OSError:
        fh = None
    if fh:
        with fh:
            for i, ln in enumerate(fh):
                if not i % 256:
                    _check(deadline)
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                t = _iso(r.get("utc"))
                if t:
                    ev.append((t, "test", float(r.get("without_s") or 0), float(r.get("with_s") or 0), float(r.get("saved_s") or 0)))
    try:
        fh = open(os.path.join(sdir, "ship-stats.jsonl"), "rb")
    except OSError:
        fh = None
    if fh:
        with fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                t = _iso(r.get("utc")) or _iso(r.get("time"))
                if t is None:
                    continue
                try:
                    rc = int(r.get("rc") or 0)
                except (TypeError, ValueError):
                    rc = -1
                ships.append((t, rc))
                if not r.get("full_s") or not r.get("wall_s"):
                    continue
                b = r.get("by_feature")
                if isinstance(b, str):
                    try:
                        b = ast.literal_eval(b)
                    except (ValueError, SyntaxError):
                        b = None
                if isinstance(b, dict):
                    for k, v in b.items():
                        try:
                            feat[k] += float(v or 0)
                        except (TypeError, ValueError):
                            pass
                ev.append((t, "ship", float(r["full_s"]), float(r["wall_s"]), float(r.get("saved_s") or 0)))
    ev.sort(key=lambda e: e[0])
    return ev, ships, feat


def block_shadow(stats):
    ev = stats[0]
    return {"runs": [[int(t), round(wo, 1), round(ac, 1), round(re_, 1)] for t, _src, wo, ac, re_ in ev]}


def block_river(stats, now):
    """app.js river:7 天、6 小時一格,rc 0=green、1/5=red、其他=other;舊的在前。"""
    BIN, t0 = 6 * 3600, now - 7 * 86400
    nb = int(-(-(now - t0) // BIN))
    bins = [[int(t0 + i * BIN), 0, 0, 0] for i in range(nb)]
    for t, rc in stats[1]:
        if t <= t0 or t > now:
            continue
        i = min(nb - 1, int((t - t0) // BIN))
        bins[i][1 if rc == 0 else 2 if rc in (1, 5) else 3] += 1
    return bins


def block_waterfall(stats):
    """app.js waterfall:不靠 aib 總時 → 減沿用/續跑 → 減只跑相關 → 減切片並行(差額)→ 實際。小時。"""
    ev, _ships, F = stats
    wo = sum(e[2] for e in ev)
    ac = sum(e[3] for e in ev)
    rt = sum(e[4] for e in ev if e[1] == "test")
    reuse = rt + F.get("resume", 0) + F.get("reuse", 0) + F.get("keyed", 0)
    sel = F.get("select", 0)
    rest = max(0.0, wo - ac - reuse - sel)
    h = lambda s: round(s / 3600, 2)  # noqa: E731
    return {"without_h": h(wo), "reuse_h": h(reuse), "select_h": h(sel), "shard_h": h(rest), "actual_h": h(ac)}


# ---------------------------------------------------------------- 租約泳道
def block_lease(now, deadline):
    """extract.py:host-lease 帳本 48 小時 acquire→release/expire/takeover 配對;還開著的對照現行租約。"""
    gov = os.path.join(seat_screen.repo_root(), "tools", "governance")
    if gov not in sys.path:
        sys.path.insert(0, gov)
    import host_lease
    cut = now - 48 * 3600
    ev = []
    with open(str(host_lease.ledger_path()), "rb") as fh:
        for i, ln in enumerate(fh):
            if not i % 512:
                _check(deadline)
            m = _RX_EPOCH.search(ln)
            if not m or int(m.group(1)) <= cut:
                continue
            try:
                ev.append(json.loads(ln))
            except ValueError:
                continue
    cur, _why = host_lease.read_lease(host_lease.lease_path())
    cur_id = (cur or {}).get("run_id")
    ev.sort(key=lambda e: e.get("epoch") or 0)
    open_, lanes = {}, []
    for e in ev:
        k = e.get("run_id") or e.get("owner")
        ow = str(e.get("owner") or "")
        who = "@" + ow[len("seat:"):] if ow.startswith("seat:") else ow
        if e.get("event") == "acquire":
            open_[k] = (e["epoch"], who, str(e.get("what") or "")[:60], bool(e.get("exclusive")), _iso(e.get("expiry")))
        elif e.get("event") in ("release", "expire", "takeover") and k in open_:
            s, w, what, ex, _x = open_.pop(k)
            lanes.append({"s": s, "e": e["epoch"], "who": w, "what": what, "ex": ex})
    for k, (s, w, what, ex, exp) in open_.items():
        # 現行租約那一筆才是「還開著」(e=null);帳本漏了收尾的舊筆,收在到期時刻(不超過現在)
        end = None if (cur_id and k == cur_id) else (min(exp, now) if exp else None)
        lanes.append({"s": s, "e": end, "who": w, "what": what, "ex": ex})
    lanes.sort(key=lambda x: x["s"])
    return lanes


# ---------------------------------------------------------------- dm:熱力 / 和弦
def block_heat(root, codes, now, deadline):
    """extract.py:各席 dm 佇列 audit 事件,過去 24 小時每小時一格;舊的在前。"""
    qroot = os.path.join(root, "state", "dm-queue")
    t0 = now - 24 * 3600
    rows = {}
    for c in codes:
        row = [0] * 24
        p = os.path.join(qroot, c, "audit.jsonl")
        if _recent(p, t0):
            with open(p, "rb") as fh:
                for i, ln in enumerate(fh):
                    if not i % 1024:
                        _check(deadline)
                    m = _RX_TS.search(ln)
                    if m:
                        ts = int(m.group(1))
                        if t0 < ts <= now:
                            row[min(23, int((ts - t0) // 3600))] += 1
        rows[c] = row
    return {"codes": list(codes), "rows": rows}


def block_chord(root, codes, now, deadline):
    """extract4:7 天內寄出的信(有 sender_seat),本艙席之間 + 其他;只開 7 天內改過的 q_*.json,用正則拿寄件席。"""
    qroot = os.path.join(root, "state", "dm-queue")
    cut = now - 7 * 86400
    names = list(codes) + [OTHER]
    idx = {n: i for i, n in enumerate(names)}
    M = [[0] * len(names) for _ in names]
    for de in os.scandir(qroot):
        if not de.is_dir():
            continue
        _check(deadline)
        b = de.name if de.name in idx else OTHER
        for e in os.scandir(de.path):
            if not (e.name.startswith("q_") and e.name.endswith(".json")):
                continue
            try:
                if e.stat().st_mtime < cut:
                    continue
                with open(e.path, "rb") as fh:
                    raw = fh.read()
            except OSError:
                continue
            ms = _RX_SENDER.search(raw)
            if not ms:
                continue
            mt = _RX_TS.search(raw)
            if not mt or int(mt.group(1)) <= cut:
                continue
            a = ms.group(1).decode()
            a = a if a in idx else OTHER
            if a == b == OTHER:
                continue
            M[idx[a]][idx[b]] += 1
    return {"names": names, "m": M}


# ---------------------------------------------------------------- git:心跳 / 漏斗
def block_beat(repo, main, now):
    """extract.py:主線 72 小時每小時提交數;舊的在前。"""
    out = _git(repo, "log", main["ref"], "--since=72 hours ago", "--format=%ct")
    if out is None:
        raise RuntimeError("git_log")
    beat = [0] * 72
    t0 = now - 72 * 3600
    for c in out.split():
        i = int((int(c) - t0) // 3600)
        if 0 <= i < 72:
            beat[i] += 1
    return beat


def block_funnel(repo, main):
    """extract4 stage():每棵工作樹的分支,比 main..分支 的提交標題在不在主線 / 最新合包。
    為了省子程序:一次 git log 撈所有分支在主線以外的提交 DAG,再在 Python 裡從各分支頂端往回走。
    回 [delivered, in_batch, on_main, total](與 lead 段同序)。"""
    wt = _git(repo, "worktree", "list", "--porcelain")
    if wt is None:
        raise RuntimeError("worktree_list")
    tips = []
    for b in wt.split("\n\n"):
        mb = re.search(r"^branch refs/heads/(.+)$", b, re.M)
        mh = re.search(r"^HEAD ([0-9a-f]{40})$", b, re.M)
        if mb and mh:
            tips.append(mh.group(1))
    if not tips:
        return [0, 0, 0, 0]
    SM = set((_git(repo, "log", main["ref"], "--format=%s", "-n", "3000") or "").splitlines())
    batch = (_git(repo, "for-each-ref", "--sort=-committerdate", "--count=1", "--format=%(refname:short)",
                  "refs/heads/int/batch") or "").strip()
    SB = set((_git(repo, "log", batch, "--format=%s", "-n", "400") or "").splitlines()) if batch else set()
    dag_out = _git(repo, "log", "--format=%H%x1f%P%x1f%s", *sorted(set(tips)), "^" + main["ref"], timeout=SUB_TIMEOUT_S)
    if dag_out is None:
        raise RuntimeError("git_dag")
    dag = {}
    for ln in dag_out.splitlines():
        parts = ln.split("\x1f", 2)
        if len(parts) == 3:
            dag[parts[0]] = (parts[1].split(), parts[2])
    memo = {}

    def subjects(tip):
        if tip in memo:
            return memo[tip]
        seen, stack, subs = set(), [tip], []
        while stack:
            h = stack.pop()
            if h in seen or h not in dag:
                continue
            seen.add(h)
            ps, s = dag[h]
            if s:
                subs.append(s)
            stack.extend(ps)
        memo[tip] = subs
        return subs

    fun = [0, 0, 0, 0]          # 示範的序:有分支 / 有東西交出去 / 全進合包 / 全上主線
    for tip in tips:
        subs = subjects(tip)
        if not subs or all(s in SM for s in subs):
            st = 3
        elif all(s in SM or s in SB for s in subs):
            st = 2
        else:
            st = 1 if any(s in SM or s in SB for s in subs) else 0
        for i in range(st + 1):
            fun[i] += 1
    return [fun[1], fun[2], fun[3], fun[0]]


# ---------------------------------------------------------------- 推前檢查紅燈去向
def _area(test):
    n = os.path.basename(test).replace("test-", "").replace(".py", "")
    for k, key in _AREAS:
        if k in n:
            return key
    return n.split("-")[0] or OTHER


def block_sankey(sdir, now, deadline):
    """extract.py:batch-pushgate 72 小時,每支測試每天第一次紅 → 之後有沒有轉綠(fixed / open)。
    5MB 級大檔:先用日期位元組篩列,再用正則拿 test/rc/time,不整列 json.loads。"""
    cut = now - 72 * 3600
    days = {time.strftime("%Y-%m-%d", time.localtime(now - i * 86400)).encode() for i in range(4)}
    p = os.path.join(sdir, "batch-pushgate.jsonl")
    if not _recent(p, cut):
        return []
    by_test = collections.defaultdict(list)
    with open(p, "rb") as fh:
        for i, ln in enumerate(fh):
            if not i % 2048:
                _check(deadline)
            mt = _RX_PG_TIME.search(ln)
            if not mt or mt.group(1)[:10] not in days:
                continue
            try:
                t = time.mktime(time.strptime(mt.group(1).decode(), "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                continue
            if t <= cut:
                continue
            m = _RX_PG.search(ln)
            if m:
                test, rc = m.group(1).decode("utf-8", "replace"), m.group(2).decode()
            else:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                test, rc = r.get("test"), "null" if r.get("rc") is None else str(r.get("rc"))
            if test:
                by_test[test].append((t, rc))
    flows, seen = collections.Counter(), set()
    for test, rs in by_test.items():
        rs.sort(key=lambda x: x[0])
        for i, (t, rc) in enumerate(rs):
            if rc in ("0", "null", ""):
                continue
            day = time.strftime("%m-%d", time.localtime(t))
            if (test, day) in seen:
                continue
            seen.add((test, day))
            fixed = any(x[1] == "0" for x in rs[i + 1:])
            flows[(_area(test), day, "fixed" if fixed else "open")] += 1
    return [{"a": a, "d": d, "o": o, "n": n} for (a, d, o), n in sorted(flows.items())]


# ---------------------------------------------------------------- 總裝
KEYS = ("shadow", "seats", "lease", "heat", "river", "waterfall", "chord", "funnel", "decide", "beat", "sankey")


def fleet_charts(state_root: str, cabin: str, now: float) -> dict:
    """艦長級圖表段。任何一段失敗 ⇒ 該鍵 null、鍵名進 missing;本函式不拋例外。"""
    t_start = time.monotonic()
    deadline = t_start + DEADLINE_S
    out = {k: None for k in KEYS}
    out.update({"schema": SCHEMA, "cabin": cabin, "measured_at": int(now), "missing": [], "took_ms": {}})
    try:
        seats = cabin_seats(state_root, cabin)
    except Exception as e:  # noqa: BLE001
        seats = []
        out["missing"].append("seats:" + e.__class__.__name__)
    codes = [c for c, _ in seats]
    repo = seat_screen.repo_root()
    pool = seat_screen._Pool(max_workers=8)
    took = out["took_ms"]

    def timed(name, fn, *a):
        t = time.monotonic()
        try:
            return fn(*a)
        finally:
            took[name] = int((time.monotonic() - t) * 1000)

    def tabs_all():
        res = {}
        for c in codes:
            _check(deadline)
            try:
                res[c] = _tabs(state_root, c)
            except Exception:  # noqa: BLE001 —— 一席佇列讀壞:那席 unread 為 null
                pass
        return res

    try:
        sdir = seat_screen.shadow_dir()
    except Exception:  # noqa: BLE001
        sdir = os.path.join(state_root, "verify-shadow")
    f = {
        "_tabs": pool.submit(timed, "_tabs", tabs_all),
        "_main": pool.submit(timed, "_main", seat_screen.fetch_main, repo),
        "_stats": pool.submit(timed, "_stats", load_stats, sdir, deadline),
        "lease": pool.submit(timed, "lease", block_lease, now, deadline),
        "heat": pool.submit(timed, "heat", block_heat, state_root, codes, now, deadline),
        "chord": pool.submit(timed, "chord", block_chord, state_root, codes, now, deadline),
        "sankey": pool.submit(timed, "sankey", block_sankey, sdir, now, deadline),
    }

    def get(name, fu):
        try:
            return fu.result(timeout=max(0.0, deadline - time.monotonic()))
        except (cf.TimeoutError, subprocess.TimeoutExpired, _Budget):
            out["missing"].append(name + ":timeout")
        except Exception as e:  # noqa: BLE001
            out["missing"].append("%s:%s" % (name, (str(e) or e.__class__.__name__)[:60]))
        return None

    def put(name, fu):
        v = get(name, fu)
        if v is not None:
            out[name] = v

    tabs = get("_tabs", f["_tabs"]) or {}
    main = get("_main", f["_main"])
    f["seats"] = pool.submit(timed, "seats", block_seats, state_root, cabin, seats, tabs, now, deadline)
    f["decide"] = pool.submit(timed, "decide", block_decide, captain_code(state_root, cabin, seats), tabs)
    if main:
        f["beat"] = pool.submit(timed, "beat", block_beat, repo, main, now)
        f["funnel"] = pool.submit(timed, "funnel", block_funnel, repo, main)
    else:
        out["missing"] += ["beat:no_main_ref", "funnel:no_main_ref"]
    stats = get("_stats", f["_stats"])
    for name, fn, args in (("shadow", block_shadow, ()), ("river", block_river, (now,)), ("waterfall", block_waterfall, ())):
        if stats is None:
            out["missing"].append(name + ":no_stats")
            continue
        try:
            out[name] = fn(stats, *args)
        except Exception as e:  # noqa: BLE001
            out["missing"].append("%s:%s" % (name, e.__class__.__name__))
    for name in ("seats", "decide", "lease", "heat", "chord", "funnel", "beat", "sankey"):
        if name in f:
            put(name, f[name])
    pool.shutdown(wait=False, cancel_futures=True)
    took["total"] = int((time.monotonic() - t_start) * 1000)
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--cabin", required=True)
    ap.add_argument("--state-root", default=None)
    a = ap.parse_args(argv)
    root = a.state_root or seats_dir_lib.state_root()[0]
    print(json.dumps(fleet_charts(root, a.cabin, time.time()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    seat_screen.entry(main)      # 例外也印合法 JSON;收尾有上限地等背景工作、殺掉剩下的子程序再 os._exit
