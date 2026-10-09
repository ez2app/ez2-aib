#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib major —— 大項迴圈:AIB 的預設執行方針(第 102 項;the maintainer 2026-09-13 01:5x 令「寫成程式、變成預設執行方針」)。

正本是大綱 §89.15:一個大項做完 ⇒ 總結、合入、本機最新、不停、依當時現況重排下一個。
本檔把 §89.15.1 的七步做成子指令,每一步都只呼叫既有工具(host_lease / make-cand / live-generic /
aib-release / aib dm),不重造;真人需求每件帶預設動作,沒回就照預設走,只有「無預設」那種會真的等。

    aib major status                      三步現查 + 目前大項與階段(RESULT 行給機器讀)
    aib major plan   [--outline F]        讀 §89.15.2 佇列,印下一個大項、真人需求(預設)、完成判準
    aib major dispatch --major N [--send] 讀「大項 N 派工」表,產每席一則 dm(預設只落檔;--send 才送)
    aib major gate   --major N            跑該大項的完成尺(判準欄裡 `尺:tests/xxx.py`),印 rc
    aib major prove-gate --major N        在「明知沒做完」的樹上跑那支尺,**紅**才登記它可當判準(空殼尺會一直綠)
    aib major reset-breaker [--forget-rounds]  人看過之後解除斷路器
    aib major wrapup --major N --version V [--execute]
                                          收尾清單:總結節、升版五檔、發版全套、ff 本機、三記憶(預設只印,--execute 才做)
    aib major nudge  [--send] [--reported S…] 回報期限到了沒回的席再點名一次;第二次沒回標「席位失聯」
    aib major watch  [--install] [--every-minutes N]
                                          時鐘驅動:launchd 每 N 分跑一次 run --send + nudge --send(預設只印 plist)
    aib major run    [--send] [--execute] 單輪:status → gate(目前大項)→ 綠則 wrapup 並 advance → dispatch 下一大項

機器判準紅線:給人看的字不當機器判準;每個子指令最後印 `RESULT key=value ...` 一行(無色、無標點裝飾),
消費者只讀那一行。狀態檔 $EZ2_AIB_INSTANCE_HOME/state/major-loop.json(沒艙就 ~/.ez2-aib/state/)。

表格契約(讀不到就大聲紅,不猜):
  佇列表在標題含「89.15.2」的節之下,欄位順序固定:| # | 大項 | 交出 | 真人需求(預設動作) | 完成判準 |
  派工表在標題含「大項 N 派工」的節之下,欄位:| 席 | 包 | 寫集 |;席欄要有 @五碼。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUTLINE = REPO / "docs" / "EZ2-AIB-DEV-OUTLINE.md"
QUEUE_HEAD_RE = re.compile(r"^###\s+89\.15\.2\b", re.M)
QUEUE_COLS = ("#", "大項", "交出", "真人需求(預設動作)", "完成判準")
DISPATCH_COLS = ("席", "包", "寫集")
SEAT_RE = re.compile(r"@([0-9a-f]{5})\b")
RULER_RE = re.compile(r"尺:\s*(tests/[\w./-]+\.py)")
DEFAULT_RE = re.compile(r"預設[::]\s*([^)\)]+)")
DONE_MARK = "✅"


class TableError(RuntimeError):
    pass


# ── 表格 ──────────────────────────────────────────────────────────────
def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table_after(text: str, head_re: re.Pattern, cols: tuple[str, ...], what: str) -> list[dict]:
    m = head_re.search(text)
    if not m:
        raise TableError(f"{what}:找不到節標題 {head_re.pattern}")
    lines = text[m.end():].splitlines()
    rows, header_seen = [], False
    for ln in lines:
        if ln.startswith("## ") or ln.startswith("### "):
            if header_seen:
                break
            continue
        if not ln.startswith("|"):
            if header_seen and rows:
                break
            continue
        cells = _split_row(ln)
        if not header_seen:
            if [c.strip("*") for c in cells] != list(cols):
                raise TableError(f"{what}:表頭不是 {cols},實得 {cells}")
            header_seen = True
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        if len(cells) != len(cols):
            raise TableError(f"{what}:某列欄數 {len(cells)} ≠ {len(cols)}:{ln[:80]}")
        rows.append(dict(zip(cols, cells)))
    if not header_seen:
        raise TableError(f"{what}:節下沒有表")
    return rows


def load_queue(outline: Path = OUTLINE) -> list[dict]:
    text = outline.read_text(encoding="utf-8")
    rows = _table_after(text, QUEUE_HEAD_RE, QUEUE_COLS, "佇列表 §89.15.2")
    out = []
    for r in rows:
        try:
            num = int(r["#"])
        except ValueError as e:
            raise TableError(f"佇列表 # 欄不是整數:{r['#']!r}") from e
        rm = RULER_RE.search(r["完成判準"])
        dm = DEFAULT_RE.search(r["真人需求(預設動作)"])
        out.append({
            "num": num,
            "title": r["大項"].strip("*"),
            "deliver": r["交出"],
            "human": r["真人需求(預設動作)"],
            "human_default": (dm.group(1).strip() if dm else None),
            "human_blocking": ("無預設" in r["真人需求(預設動作)"]),
            "gate": r["完成判準"],
            "gate_ruler": (rm.group(1) if rm else None),
            "done": DONE_MARK in r["大項"] or DONE_MARK in r["完成判準"],
        })
    return out


def load_dispatch(major: int, outline: Path = OUTLINE) -> list[dict]:
    text = outline.read_text(encoding="utf-8")
    head = re.compile(r"^###\s+89\.15\.\d+\s+大項\s+%d\s+派工" % major, re.M)
    rows = _table_after(text, head, DISPATCH_COLS, f"大項 {major} 派工表")
    out = []
    for r in rows:
        m = SEAT_RE.search(r["席"])
        if not m:
            raise TableError(f"大項 {major} 派工表:席欄沒有 @五碼:{r['席']}")
        out.append({"seat": m.group(1), "seat_label": r["席"], "package": r["包"], "writeset": r["寫集"]})
    return out


# ── 狀態 ──────────────────────────────────────────────────────────────
def state_path() -> Path:
    home = os.environ.get("EZ2_AIB_INSTANCE_HOME") or os.path.join(os.path.expanduser("~"), ".ez2-aib")
    p = Path(home) / "state" / "major-loop.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_state() -> dict:
    p = state_path()
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"schema": "aib-major-loop/v1", "current": None, "phase": "idle", "history": [],
            "breaker": {"tripped": False, "why": None, "consecutive_red": 0, "redispatch": {}}}


def save_state(st: dict) -> None:
    state_path().write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd or REPO), capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)


def _dm(seat: str, f: Path) -> subprocess.CompletedProcess:
    """Send one dispatch file to a seat of this cabin. The `aib dm` entry now requires <cabin>/<seat>
    (the maintainer 09-28); this loop only holds seat codes of its own cabin, so it uses the dm plumbing directly,
    with the same auto-resend default the entry sets."""
    env = dict(os.environ, AIB_DM_AUTO_RESEND=os.environ.get("AIB_DM_AUTO_RESEND", "1"))
    return subprocess.run(["bash", str(REPO / "src" / "cockpit" / "dm-ctl.sh"), seat, "--file", str(f)],
                          cwd=str(REPO), capture_output=True, text=True, timeout=120,
                          stdin=subprocess.DEVNULL, env=env)


# ── 發版/驗證在飛時的硬擋(the maintainer 09-13 02:2x 令:一定不能影響發版、不能中斷發版)────
#   判準用**兩個獨立來源**,任一成立就算在飛,不互相取代:
#     (1) 租約:host_lease.py status 不是「機器空」——這是全庫唯一的機器佔用正本;
#     (2) 行程:ps 看得到 aib-release.sh / main-merge-witness.sh / live-generic.sh 在跑
#         ——租約拿不到或別人沒拿租約時(今晚實測 runsuite 那類)仍看得見。
#   在飛時:gate / prove-gate / wrapup --execute / run 的跑尺段一律不做,只印狀態;
#   dispatch 與 nudge(只送 dm、不碰機器)照走,但 --send 也會在輸出裡標明發版在飛。
IN_FLIGHT_PATTERNS = ("aib-release.sh", "main-merge-witness.sh", "live-generic.sh")


def release_in_flight() -> dict:
    lease_tool = REPO / "tools" / "governance" / "host_lease.py"
    lease_out = _run([sys.executable, str(lease_tool), "status"], timeout=60).stdout.strip().splitlines()
    lease_line = lease_out[0] if lease_out else "量不到"
    by_lease = not lease_line.startswith("機器空")
    ps = _run(["ps", "-axo", "command"], timeout=60).stdout
    hits = sorted({pat for pat in IN_FLIGHT_PATTERNS if pat in ps})
    return {"in_flight": bool(by_lease or hits), "by_lease": by_lease, "by_proc": hits, "lease": lease_line}


def refuse_if_in_flight(what: str) -> dict | None:
    f = release_in_flight()
    if not f["in_flight"]:
        return None
    print(f"★ 發版/驗證在飛,{what} 一律不做(不影響、不中斷發版):租約「{f['lease']}」;行程 {f['by_proc'] or '無'}")
    print(f"RESULT skipped={what} in_flight=1 by_lease={int(f['by_lease'])} by_proc={','.join(f['by_proc']) or '-'}")
    return f


# ── 斷路器 / 預算 / 寫集交集 ─────────────────────────────────────────
BREAKER_MAX_CONSECUTIVE_RED = 2      # 連續兩個大項的完成尺都紅 ⇒ 停,不再自動派
BREAKER_MAX_REDISPATCH = 3           # 同一個大項重派超過三次 ⇒ 停
DEFAULT_BUDGET_ROUNDS = 6            # 一個大項的輪數上限;超了縮包不硬撐


def breaker_state(st: dict) -> dict:
    b = st.setdefault("breaker", {"tripped": False, "why": None, "consecutive_red": 0, "redispatch": {}})
    b.setdefault("redispatch", {})
    return b


def trip_breaker(st: dict, why: str) -> None:
    b = breaker_state(st)
    b["tripped"] = True
    b["why"] = why
    st["history"].append({"t": int(time.time()), "event": "breaker-tripped", "why": why})
    save_state(st)


def writeset_of(spec: str) -> set[str]:
    """派工表「寫集」欄 → 檔案集合。用頓號/逗號/空白切;`tools/governance/x*` 這種前綴也算一個名字。
    ★ 只做字面比對:比對不出來的(例如只寫「同 95」)回空集合,而空集合**不算不相交**——
      呼叫端把它當「量不到」,一律串行,不得當成安全。"""
    parts = [x.strip() for x in re.split(r"[、,，\s]+", spec) if x.strip()]
    return {x for x in parts if "/" in x or x.endswith(".py") or x.endswith(".sh")}


def overlap_pairs(rows: list[dict]) -> list[tuple[str, str, list[str]]]:
    out = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            sa, sb = writeset_of(a["writeset"]), writeset_of(b["writeset"])
            if not sa or not sb:
                out.append((a["seat"], b["seat"], ["量不到"]))
                continue
            inter = sorted(sa & sb)
            if inter:
                out.append((a["seat"], b["seat"], inter))
    return out


# ── 七步 ──────────────────────────────────────────────────────────────
def survey() -> dict:
    """第 1 步的現查:origin/main / 本地 main / 租約 / 佇列。只讀。"""
    _run(["git", "fetch", "-q", "origin"])
    remote = _run(["git", "rev-parse", "--short", "origin/main"]).stdout.strip()
    local = _run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    ahead = _run(["git", "rev-list", "--count", "origin/main..HEAD"]).stdout.strip()
    lease_tool = REPO / "tools" / "governance" / "host_lease.py"
    lease = _run([sys.executable, str(lease_tool), "status"]).stdout.strip().splitlines()
    lease_line = lease[0] if lease else "量不到"
    return {"remote": remote, "local": local, "ahead": ahead,
            "machine_free": lease_line.startswith("機器空"), "lease": lease_line}


def next_major(queue: list[dict], st: dict) -> dict | None:
    for q in queue:
        if q["done"]:
            continue
        if st.get("current") == q["num"] and st.get("phase") == "done":
            continue
        return q
    return None


def cmd_status(a) -> int:
    s = survey()
    st = load_state()
    print(f"origin/main={s['remote']} local={s['local']} ahead={s['ahead']}")
    print(f"租約:{s['lease']}")
    print(f"目前大項:{st.get('current')} 階段:{st.get('phase')}")
    print(f"RESULT remote={s['remote']} local={s['local']} ahead={s['ahead']} machine_free={int(s['machine_free'])} "
          f"current={st.get('current')} phase={st.get('phase')}")
    return 0


def cmd_plan(a) -> int:
    queue = load_queue(Path(a.outline))
    st = load_state()
    nxt = next_major(queue, st)
    for q in queue:
        mark = "✅" if q["done"] else ("▶" if nxt and q["num"] == nxt["num"] else "·")
        print(f"{mark} 大項 {q['num']} {q['title'][:40]} | 真人:{q['human'][:50]} | 尺:{q['gate_ruler'] or '(未指定)'}")
    if nxt is None:
        print("RESULT next=none")
        return 0
    if nxt["human_blocking"]:
        print(f"★ 大項 {nxt['num']} 的真人需求沒有預設動作,會真的等:{nxt['human']}")
    print(f"RESULT next={nxt['num']} human_default={'yes' if nxt['human_default'] else 'no'} "
          f"blocking={int(nxt['human_blocking'])} gate_ruler={nxt['gate_ruler'] or 'none'}")
    return 0


def render_dm(major: int, q: dict, d: dict, common: str, due: str = "") -> str:
    due_line = f"回報期限:{due} 前 dm 艦長頂 sha;到期沒回會被再點名一次,兩次沒回標「席位失聯」給人看。\n" if due else ""
    return (f"艦長 → {d['seat_label']}(aib major dispatch,大項 {major}:{q['title'][:30]})\n"
            f"包:{d['package']}\n寫集:{d['writeset']}\n完成判準(大項):{q['gate']}\n{due_line}"
            f"自我巡查:每 15 分鐘讀一次佇列與租約、回一行進度,直到交件。\n{common}\n")


def _due(minutes: int) -> tuple[str, int]:
    epoch = int(time.time()) + minutes * 60
    return time.strftime("%H:%M", time.localtime(epoch)), epoch


COMMON_RULES = ("共同規矩:自 origin/main 開自己的工作樹,寫集只准動派給你的檔;只寫碼與規格,不跑尺、不起發版、不推主線;"
                "commit 後 dm 艦長一行「<包> 頂 <sha> 寫集 N 檔」,尺時段由艦長點名(租約代拿)。")


def cmd_dispatch(a) -> int:
    queue = load_queue(Path(a.outline))
    q = next((x for x in queue if x["num"] == a.major), None)
    if q is None:
        print(f"RESULT error=no_such_major major={a.major}")
        return 2
    rows = load_dispatch(a.major, Path(a.outline))
    st0 = load_state()
    b = breaker_state(st0)
    if b["tripped"] and not a.force:
        print(f"★ 斷路器已跳:{b['why']} —— 要人看過再 `aib major reset-breaker`(或本次 --force)")
        st0["phase"] = "blocked-breaker"; save_state(st0)
        print(f"RESULT major={a.major} dispatched=0 breaker=tripped")
        return 7
    n = b["redispatch"].get(str(a.major), 0)
    if n >= BREAKER_MAX_REDISPATCH and not a.force:
        trip_breaker(st0, f"大項 {a.major} 重派 {n} 次仍未完成")
        print(f"RESULT major={a.major} dispatched=0 breaker=redispatch_limit n={n}")
        return 7
    laps = overlap_pairs(rows)
    if laps:
        for s1, s2, inter in laps:
            print(f"★ 寫集相交 {s1} × {s2}:{'、'.join(inter[:4])} ⇒ 這兩席不得同時派(串行或拆包)")
        if not a.allow_overlap:
            st0["current"] = a.major; st0["phase"] = "blocked-overlap"; save_state(st0)
            print(f"RESULT major={a.major} dispatched=0 overlap={len(laps)}")
            return 8
    outdir = Path(a.outdir) if a.outdir else state_path().parent / f"major-{a.major}-dm"
    outdir.mkdir(parents=True, exist_ok=True)
    sent = 0
    due_hm, due_epoch = _due(a.due_minutes)
    for d in rows:
        f = outdir / f"{d['seat']}.txt"
        f.write_text(render_dm(a.major, q, d, COMMON_RULES, due_hm), encoding="utf-8")
        if a.send:
            r = _dm(d["seat"], f)
            stage = re.findall(r'"stage":\s*"([a-z-]+)"', r.stdout + r.stderr)
            print(f"  {d['seat']} rc={r.returncode} stage={stage[-1] if stage else 'unknown'}")
            sent += int(r.returncode == 0)
        else:
            print(f"  {d['seat']} → {f}")
    st = load_state()
    bb = breaker_state(st)
    bb["redispatch"][str(a.major)] = bb["redispatch"].get(str(a.major), 0) + 1
    st["budget"] = {"major": a.major, "rounds": bb["redispatch"][str(a.major)], "max_rounds": a.budget_rounds}
    st["current"] = a.major
    st["phase"] = "dispatched" if a.send else "planned"
    st["history"].append({"t": int(time.time()), "major": a.major, "event": st["phase"], "seats": [d["seat"] for d in rows]})
    st["due"] = {d["seat"]: {"epoch": due_epoch, "nudges": 0, "reported": False} for d in rows}
    save_state(st)
    rounds = bb["redispatch"][str(a.major)]
    if rounds > a.budget_rounds:
        print(f"★ 預算:大項 {a.major} 已第 {rounds} 輪(上限 {a.budget_rounds})—— 縮包或降級目標,不要硬撐")
    print(f"RESULT major={a.major} seats={len(rows)} sent={sent} mode={'send' if a.send else 'dry-run'} "
          f"due={due_hm} rounds={rounds} over_budget={int(rounds > a.budget_rounds)} overlap={len(laps)} outdir={outdir}")
    return 0


def cmd_gate(a) -> int:
    save_state(load_state())          # 確保狀態檔存在:早退時消費者仍讀得到「上一次是什麼狀態」
    if not getattr(a, "ignore_in_flight", False) and refuse_if_in_flight("gate"):
        return 9
    queue = load_queue(Path(a.outline))
    q = next((x for x in queue if x["num"] == a.major), None)
    if q is None:
        print(f"RESULT error=no_such_major major={a.major}")
        return 2
    if not q["gate_ruler"]:
        print(f"大項 {a.major} 的完成判準沒有指定尺(`尺:tests/xxx.py`),量不到 ⇒ 不算完成")
        print(f"RESULT major={a.major} gate=unmeasured rc=-1")
        return 3
    ruler = Path(a.repo) / q["gate_ruler"]
    if not ruler.is_file():
        print(f"RESULT major={a.major} gate=missing_ruler rc=-1 ruler={q['gate_ruler']}")
        return 3
    env = {k: v for k, v in os.environ.items() if k not in ("AIB_QUIET",)}
    env["PYTHON_COLORS"] = "0"
    r = subprocess.run([sys.executable, str(ruler)], cwd=a.repo, capture_output=True, text=True,
                       timeout=a.timeout, env=env, stdin=subprocess.DEVNULL)
    tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
    print(f"尺 {q['gate_ruler']} rc={r.returncode}:{tail[0][:120]}")
    st = load_state()
    bb = breaker_state(st)
    proven = (st.get("gate_proven") or {}).get(str(a.major)) is True
    if r.returncode == 0:
        bb["consecutive_red"] = 0
        st["current"] = a.major
        st["phase"] = "gate-green" if proven else "gate-green-unproven"
        save_state(st)
        if not proven:
            print(f"★ 這支尺從沒被證明過「會紅」(空殼尺會一直綠)⇒ 綠**不算完成**;"
                  f"先 `aib major prove-gate --major {a.major}` 在明知沒做完的樹上跑一次拿到紅,再回來")
            print(f"RESULT major={a.major} gate=green_unproven rc=0 proven=0")
            return 2
    else:
        bb["consecutive_red"] += 1
        st["phase"] = "gate-red"
        if bb["consecutive_red"] >= BREAKER_MAX_CONSECUTIVE_RED:
            trip_breaker(st, f"完成尺連續 {bb['consecutive_red']} 次紅(最近:大項 {a.major})")
        save_state(st)
    print(f"RESULT major={a.major} gate={'green' if r.returncode == 0 else 'red'} rc={r.returncode} proven={int(proven)}")
    return 0 if r.returncode == 0 else 1


def cmd_prove_gate(a) -> int:
    """完成尺要先證明它「會紅」才准當判準(第 69 項空殼尺教訓:空殼會一直綠)。
    做法:在**明知沒做完**的狀態下跑那支尺,rc 非 0 才登記 proven;綠的話拒登。"""
    if refuse_if_in_flight("prove-gate"):
        return 9
    queue = load_queue(Path(a.outline))
    q = next((x for x in queue if x["num"] == a.major), None)
    if q is None or not q["gate_ruler"]:
        print(f"RESULT major={a.major} proven=0 reason=no_ruler")
        return 2
    ruler = Path(a.repo) / q["gate_ruler"]
    env = {k: v for k, v in os.environ.items() if k != "AIB_QUIET"}
    env["PYTHON_COLORS"] = "0"
    r = subprocess.run([sys.executable, str(ruler)], cwd=a.repo, capture_output=True, text=True,
                       timeout=a.timeout, env=env, stdin=subprocess.DEVNULL)
    st = load_state()
    if r.returncode == 0:
        print("這支尺在『明知沒做完』的樹上是綠的 ⇒ 它守不住任何東西,拒絕登記為完成判準")
        print(f"RESULT major={a.major} proven=0 rc=0")
        return 1
    st.setdefault("gate_proven", {})[str(a.major)] = True
    st["history"].append({"t": int(time.time()), "major": a.major, "event": "gate-proven", "rc": r.returncode})
    save_state(st)
    print(f"RESULT major={a.major} proven=1 rc={r.returncode}")
    return 0


def cmd_reset_breaker(a) -> int:
    st = load_state()
    b = breaker_state(st)
    was = b.get("why")
    b.update({"tripped": False, "why": None, "consecutive_red": 0})
    if a.forget_rounds:
        b["redispatch"] = {}
    st["history"].append({"t": int(time.time()), "event": "breaker-reset", "was": was})
    save_state(st)
    print(f"RESULT breaker=reset was={was or '-'}")
    return 0


VERSION_CARRIERS = ("VERSION", "README.md", "CHANGELOG.md", "src/dataguard/pins.py", "deploy/line/manifest-v1.json")


def cmd_wrapup(a) -> int:
    """第 4–6 步。預設只印清單(每一步是既有工具的一條指令);--execute 才真的跑,而且逐步印 rc。"""
    steps = [
        ("總結節", f"大綱加一節「大項 {a.major} 收尾」(上主線的顆、實測數字、教訓、真人需求結果)+ CHANGELOG 段(docs 工作樹 → ff)"),
        ("升版", f"五檔同版 {a.version}:{', '.join(VERSION_CARRIERS)}(tests/test-version.py 會擋不一致)"),
        ("發版全套", f"AIB_RELEASE_GATE_REASON=<逃生理由或空> tools/aib-release.sh --release {a.version} -F <tag 訊息檔>"),
        ("本機最新", "git -C 主樹 fetch && merge --ff-only origin/main;印 remote/local/ahead 三數,三數對才算"),
        ("三記憶", "席位記憶 ~/.ez2-aib/memory/<席>/context.md、Obsidian 交接總綱、各 AI 自己的記憶各寫一段(附大項編號與 sha)"),
    ]
    for i, (name, how) in enumerate(steps, 1):
        print(f"  {i}. {name}:{how}")
    if a.execute and refuse_if_in_flight("wrapup --execute"):
        return 9
    if a.execute:
        chk = _run([str(REPO / "tools" / "aib-release.sh"), "--check-fast"], timeout=300)
        print(f"  check-fast rc={chk.returncode}:{(chk.stdout.strip().splitlines() or [''])[-1][:100]}")
        print("  ★ 發版全套與三記憶回寫不由本檔代按:發版要人給逃生理由與 tag 訊息,記憶要作者本人寫;本檔只印清單與跑 check-fast。")
    st = load_state()
    st["current"] = a.major
    st["phase"] = "done" if a.execute else "wrapup-planned"
    st["history"].append({"t": int(time.time()), "major": a.major, "event": st["phase"], "version": a.version})
    save_state(st)
    print(f"RESULT major={a.major} version={a.version} mode={'execute' if a.execute else 'dry-run'} steps={len(steps)}")
    return 0


def cmd_run(a) -> int:
    """單輪:status → 目前大項 gate → 綠則 wrapup(dry/execute)並 advance → dispatch 下一大項(dry/--send)。"""
    s = survey()
    st = load_state()
    b = breaker_state(st)
    if b["tripped"]:
        print(f"★ 斷路器已跳,本輪什麼都不派:{b['why']}")
        print("RESULT breaker=tripped dispatched=0")
        return 7
    queue = load_queue(Path(a.outline))
    cur = st.get("current")
    print(f"現查:origin/main={s['remote']} 機器{'空' if s['machine_free'] else '被佔'};目前大項 {cur} 階段 {st.get('phase')}")
    fly = release_in_flight()
    if fly["in_flight"]:
        print(f"★ 發版/驗證在飛,本輪只看不動(不跑完成尺、不派工):租約「{fly['lease']}」;行程 {fly['by_proc'] or '無'}")
        print(f"RESULT in_flight=1 dispatched=0 current={cur} phase={st.get('phase')}")
        return 0
    if cur is not None and st.get("phase") not in ("done",):
        q = next((x for x in queue if x["num"] == cur), None)
        if q and q["gate_ruler"] and s["machine_free"]:
            ga = argparse.Namespace(outline=a.outline, major=cur, repo=str(REPO), timeout=600)
            if cmd_gate(ga) == 0:
                wa = argparse.Namespace(major=cur, version=a.version or "<待填>", execute=a.execute)
                cmd_wrapup(wa)
                st = load_state()
        elif q and not s["machine_free"]:
            print("機器被佔,本輪不跑完成尺(同一台機器不並行驗證)")
    nxt = next_major(queue, load_state())
    if nxt is None:
        print("RESULT next=none")
        return 0
    if nxt["human_blocking"]:
        print(f"★ 下一大項 {nxt['num']} 沒有預設動作的真人需求,停在這裡等人:{nxt['human']}")
        print(f"RESULT next={nxt['num']} blocked_on_human=1")
        return 4
    da = argparse.Namespace(outline=a.outline, major=nxt["num"], send=a.send, outdir=None, due_minutes=90,
                            force=False, allow_overlap=False, budget_rounds=DEFAULT_BUDGET_ROUNDS)
    return cmd_dispatch(da)


def cmd_nudge(a) -> int:
    """回報期限到了沒回的席:再點名一次;第二次仍沒回 ⇒ 標「席位失聯」給人看(不無限等)。
    「有回報」由呼叫端用 `aib major nudge --reported <seat>` 登記(dm 內容機器不讀,由艦長/巡查判)。"""
    st = load_state()
    due = st.get("due") or {}
    now = int(time.time())
    if a.reported:
        for s in a.reported:
            if s in due:
                due[s]["reported"] = True
        st["due"] = due; save_state(st)
        print(f"RESULT reported={','.join(a.reported)}")
        return 0
    late, lost, nudged = [], [], 0
    for seat, info in due.items():
        if info.get("reported") or now < info["epoch"]:
            continue
        if info["nudges"] >= 1:
            lost.append(seat); continue
        late.append(seat)
        info["nudges"] += 1
        info["epoch"] = now + a.grace_minutes * 60
        if a.send:
            msg = f"艦長(aib major nudge):大項 {st.get('current')} 的包回報期限已過,請在 {a.grace_minutes} 分內 dm 頂 sha 或一行進度;再沒回會標席位失聯。\n"
            f = state_path().parent / f"nudge-{seat}.txt"; f.write_text(msg, encoding="utf-8")
            r = _dm(seat, f)
            nudged += int(r.returncode == 0)
    st["due"] = due; save_state(st)
    for s in lost:
        print(f"★ 席位失聯:{s}(兩次期限都沒回)——要人看")
    print(f"RESULT late={','.join(late) or '-'} nudged={nudged} lost={','.join(lost) or '-'} mode={'send' if a.send else 'dry-run'}")
    return 0 if not lost else 6


def cmd_watch(a) -> int:
    """時鐘驅動:印或安裝 launchd 排程,每 N 分鐘跑一次 `aib major run --send` + `nudge --send`。
    預設只印 plist 內容與安裝指令(--install 才寫 ~/Library/LaunchAgents 並 load);沒有它,迴圈只靠事件與艦長在線。"""
    label = "shop.ez2.aib.major-loop"
    aib = REPO / "src" / "cockpit" / "aib"
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string>
    <string>cd {REPO} && {aib} major run --send >> $HOME/.ez2-aib/logs/major-loop.log 2>&1; {aib} major nudge --send >> $HOME/.ez2-aib/logs/major-loop.log 2>&1</string>
  </array>
  <key>StartInterval</key><integer>{a.every_minutes * 60}</integer>
  <key>RunAtLoad</key><false/>
</dict></plist>
"""
    dest = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if a.install:
        dest.parent.mkdir(parents=True, exist_ok=True)
        (Path.home() / ".ez2-aib" / "logs").mkdir(parents=True, exist_ok=True)
        dest.write_text(plist, encoding="utf-8")
        r = subprocess.run(["launchctl", "load", "-w", str(dest)], capture_output=True, text=True)
        print(f"launchctl load rc={r.returncode} {r.stderr.strip()[:100]}")
        print(f"RESULT installed=1 plist={dest} every_minutes={a.every_minutes} rc={r.returncode}")
        return 0 if r.returncode == 0 else 1
    print(plist)
    print("★ 本排程每輪第一件事就是問「發版在不在飛」,在飛就只看不動;安裝與否不改變這一點。")
    print(f"安裝:aib major watch --install(寫 {dest} 並 launchctl load -w);卸載:launchctl unload -w {dest}")
    print(f"RESULT installed=0 plist={dest} every_minutes={a.every_minutes}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="aib major")
    p.add_argument("--outline", default=str(OUTLINE))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("plan")
    d = sub.add_parser("dispatch"); d.add_argument("--major", type=int, required=True); d.add_argument("--send", action="store_true"); d.add_argument("--outdir"); d.add_argument("--due-minutes", type=int, default=90); d.add_argument("--force", action="store_true"); d.add_argument("--allow-overlap", action="store_true"); d.add_argument("--budget-rounds", type=int, default=DEFAULT_BUDGET_ROUNDS)
    n = sub.add_parser("nudge"); n.add_argument("--send", action="store_true"); n.add_argument("--grace-minutes", type=int, default=20); n.add_argument("--reported", nargs="*")
    pg = sub.add_parser("prove-gate"); pg.add_argument("--major", type=int, required=True); pg.add_argument("--repo", default=str(REPO)); pg.add_argument("--timeout", type=int, default=600)
    rb = sub.add_parser("reset-breaker"); rb.add_argument("--forget-rounds", action="store_true")
    wt = sub.add_parser("watch"); wt.add_argument("--install", action="store_true"); wt.add_argument("--every-minutes", type=int, default=5)
    g = sub.add_parser("gate"); g.add_argument("--major", type=int, required=True); g.add_argument("--repo", default=str(REPO)); g.add_argument("--timeout", type=int, default=600); g.add_argument("--ignore-in-flight", action="store_true")
    w = sub.add_parser("wrapup"); w.add_argument("--major", type=int, required=True); w.add_argument("--version", required=True); w.add_argument("--execute", action="store_true")
    r = sub.add_parser("run"); r.add_argument("--send", action="store_true"); r.add_argument("--execute", action="store_true"); r.add_argument("--version")
    a = p.parse_args(argv)
    try:
        return {"status": cmd_status, "plan": cmd_plan, "dispatch": cmd_dispatch, "gate": cmd_gate,
                "wrapup": cmd_wrapup, "run": cmd_run, "nudge": cmd_nudge, "watch": cmd_watch,
                "prove-gate": cmd_prove_gate, "reset-breaker": cmd_reset_breaker}[a.cmd](a)
    except TableError as e:
        print(f"表格契約紅:{e}")
        print("RESULT error=table_contract")
        return 5


if __name__ == "__main__":
    sys.exit(main())
