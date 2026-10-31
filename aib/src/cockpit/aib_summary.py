#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib summary —— 給 the maintainer 看的總結(記憶整理第 5 包;docs/DESIGN-memory-tidy-1013.md §2)。

總結與交接分開:交接(aib handoff)寫給下一個 AI;總結寫給 the maintainer,用七段總回報(tools/report/aib-report.py)。
  aib summary write --file <總回報輸入.json> [--seat 艙/席]   先確認本輪整理過(aib memory tidy)→ 渲染、落回報檔、播動畫 → 總結收據
  aib summary status [--seat 艙/席]                            這一席最後一張總結收據
  aib summary template [--out <檔>]                            印一份過得了格式檢查的最小輸入(照著填,不用撞 lint 猜欄位)

總結一定要先整理:本輪(交接一輪的起點之後)沒有整理收據 ⇒ rc 2,說先跑 aib memory tidy。
(交接三步不被擋:整理 / 總結沒做只在交接檔與收據記下來;但 aib handoff verify 第 2 / 6 項會紅,一定要補。)
渲染與落檔走 tools/report/aib-reply.sh(過 report-lint、存 pending/REPORT-FULL…、tmux 播流光);那支不過 ⇒ 不寫收據。
總結也寫回第二大腦(10-24 維護者:「總結與交接都不能跳過」「一定要寫回對應的人格與第二大腦」):
  一則 note(標 aib/summary,綁本席人格與專案),取代本席上一則總結;寫不進 ⇒ 收據記 brain.ok=false、rc 2,verify 第 6 項紅。
rc:0 寫了;2 本輪沒整理 / 渲染沒過 / 第二大腦寫不進;3 認不出席。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for _p in (HERE, HERE.parent / "memory", HERE.parent / "identity"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def summary_dir():
    import memory_tidy
    return memory_tidy.state_root() / "state" / "summary"


def round_start(code):
    """本輪起點 = max(交接記的這一輪起點, 這一席最後一張**完整**交接收據的時刻)。
    沒有任何一輪紀錄的席(codex / grok 從沒 /clear)不能退成 0 —— 那樣史上任何一張整理收據都算本輪。
    標了不完整(incomplete 非空)的收據不算一輪的終點(10-31 T15,@557fe 一天重跑 5 次):交接補一封未讀就要重寫,
    要是不完整的收據也推進起點,剛做的整理 / 總結就變「本輪沒做」⇒ 重整理、重收集 ⇒ 收集又抓到新信 ⇒ 又不完整。"""
    try:
        import aib_handoff
        rs = aib_handoff.current_round_start(code)
        latest = aib_handoff._read_json(aib_handoff.receipt_path(code)) or {}
        if not latest.get("incomplete"):
            last = latest.get("ts") or 0.0
        else:
            last = max([float(r["ts"]) for r in aib_handoff.receipts(code) if not r.get("incomplete")] or [0.0])
        return max(float(rs or 0.0), float(last or 0.0))
    except Exception:  # noqa: BLE001
        return 0.0


def _ts(iso):
    try:
        return datetime.datetime.fromisoformat(str(iso)).timestamp()
    except (ValueError, TypeError):
        return 0.0


def tidy_this_round(code):
    """本輪的整理收據(交接一輪起點之後);沒有回 {}。"""
    import memory_tidy
    rec = memory_tidy.last_receipt(code) or {}
    return rec if rec and _ts(rec.get("at")) >= round_start(code) else {}


def summary_this_round(code):
    rec = _read(summary_dir() / ("%s.json" % code))
    return rec if rec and _ts(rec.get("at")) >= round_start(code) else {}


def _read(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# 最小輸入:七段都有、過 report-lint(葉子用 pending ⇒ 不要證據指令;反思帶「因為」⇒ 沒警告)。
# 欄位規矩在 tools/report/aib-report.py validate();改那邊的必填欄位要一起改這裡(測試會跑渲染抓)。
TEMPLATE = {
    "title": "範本回報",
    "tree": [{"id": "A", "title": "範本大項",
              "children": [{"id": "A.1", "title": "範本子項", "status": "pending", "evidence": "尚未開始,這是範本"}]}],
    "progress_text": "這是範本,還沒有開始任何工作。",
    "stars": 1,
    "progress": "auto",
    "summary": "範本摘要:尚無內容。",
    "reflection": "這一輪沒有新事,因為這只是範本。",
    "footer": {"dispatched": 0, "executed": "尚無", "human_decision": "不需回應", "waiting_update": "尚無",
               "next": "填入真實內容", "patrol_minutes": 30, "patrol": "再看一次"},
}


def template(out=""):
    text = json.dumps(TEMPLATE, ensure_ascii=False, indent=1) + "\n"
    if out:
        Path(out).write_text(text, encoding="utf-8")
    return text


def brain_body(report):
    """總回報輸入 ⇒ 第二大腦那則的內文(標題、摘要、進度、反思、等誰 / 下一步;不放證據指令)。"""
    r = report if isinstance(report, dict) else {}
    f = r.get("footer") if isinstance(r.get("footer"), dict) else {}
    rows = [("摘要", r.get("summary")), ("進度", r.get("progress_text")), ("反思", r.get("reflection")),
            ("要真人決定", f.get("human_decision")), ("等別人", f.get("waiting_update")), ("下一步", f.get("next"))]
    out = ["# %s" % str(r.get("title") or "總結")[:200]]
    for g in r.get("tree") or []:
        if not isinstance(g, dict):
            continue
        out.append("- %s" % str(g.get("title") or "")[:200])
        for c in g.get("children") or []:
            if isinstance(c, dict):
                out.append("  - [%s] %s — %s" % (c.get("status") or "-", str(c.get("title") or "")[:200],
                                                 str(c.get("evidence") or "")[:200]))
    out += ["%s:%s" % (k, str(v)[:600]) for k, v in rows if v]
    return "\n".join(out)


def previous_summary(ctx):
    """本席上一則總結(同一個專案、標 aib/summary)⇒ 這次取代它。比席碼不比 seat_uid(/clear 之後換新 uuid;審查 B-S5)。"""
    import aib_memory
    mine = [e for e in aib_memory.entries() if "aib/summary" in (e.get("tags") or [])
            and e.get("seat") == ctx.get("code") and (e.get("project_uid") or "") == (ctx.get("project_uid") or "")]
    mine.sort(key=lambda e: e.get("created_ms") or 0)
    return mine[-1]["uid"] if mine else ""


def brain_write(ctx, report, seat="", writer=None):
    try:
        import aib_memory
        rec = (writer or aib_memory.write)("note", "@%s 總結 %s" % (ctx["code"], _now()[:16].replace("T", " ")),
                                           brain_body(report), tags=["aib/summary"], seat=seat,
                                           project=ctx.get("project_uid") or "", supersedes=previous_summary(ctx),
                                           model=ctx.get("model") or "")
        return {"ok": bool(rec.get("uid")), "uid": rec.get("uid", "")}
    except Exception as exc:  # noqa: BLE001 —— 寫不進照實記,不假裝寫了
        return {"ok": False, "why": str(exc)[:300]}


def write(report_json, seat="", runner=None, writer=None):
    import memory_tidy
    ctx = memory_tidy.seat_info(seat)
    tidy = tidy_this_round(ctx["code"])
    if not tidy:
        raise memory_tidy.TidyError("這一輪還沒整理記憶;先跑 aib memory tidy collect → apply,再寫總結")
    cmd = ["bash", str(REPO / "tools" / "report" / "aib-reply.sh"), str(report_json)]
    try:
        p = (runner or (lambda c: subprocess.run(c, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=180)))(cmd)
    except (OSError, subprocess.SubprocessError) as exc:
        raise memory_tidy.TidyError("總回報渲染沒跑完(%s),沒寫收據" % exc)
    if p.returncode != 0:
        raise memory_tidy.TidyError("總回報渲染或格式檢查沒過(rc=%d),沒寫收據:%s" % (p.returncode, (p.stderr or "")[-300:]))
    m = re.search(r"回報檔[::]\s*(\S+)", (p.stdout or "") + (p.stderr or ""))
    brain = brain_write(ctx, _read(report_json), seat, writer)
    rec = {"at": _now(), "seat": ctx["code"], "model": ctx["model"], "file": m.group(1) if m else "",
           "tidy_status": tidy.get("status"), "tidy_at": tidy.get("at"), "brain": brain}
    d = summary_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / ("%s.json" % ctx["code"])).write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if not brain.get("ok"):
        raise memory_tidy.TidyError("總結渲染好了,但第二大腦寫不進(%s);修好再跑一次 aib summary write" % (brain.get("why") or "沒有 uid"))
    return rec


def main(argv=None):
    import memory_tidy
    ap = argparse.ArgumentParser(prog="aib summary")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("--file", required=True)
    w.add_argument("--seat", default="")
    s = sub.add_parser("status")
    s.add_argument("--seat", default="")
    t = sub.add_parser("template")
    t.add_argument("--out", default="")
    o = ap.parse_args(argv)
    if o.cmd == "template":
        text = template(o.out)
        if not o.out:
            sys.stdout.write(text)
        return 0
    try:
        if o.cmd == "write":
            print(json.dumps(write(o.file, o.seat), ensure_ascii=False, indent=1))
            return 0
        ctx = memory_tidy.seat_info(o.seat)
        print(json.dumps(summary_this_round(ctx["code"]) or {"status": "none"}, ensure_ascii=False, indent=1))
        return 0
    except memory_tidy.TidyError as exc:
        print("aib summary:%s" % exc, file=sys.stderr)
        return 3 if "認不出" in str(exc) else 2


if __name__ == "__main__":
    sys.exit(main())
