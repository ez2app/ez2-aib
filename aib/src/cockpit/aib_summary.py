#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib summary —— 給 the maintainer 看的總結(記憶整理第 5 包;docs/DESIGN-memory-tidy-1013.md §2)。

總結與交接分開:交接(aib handoff)寫給下一個 AI;總結寫給 the maintainer,用七段總回報(tools/report/aib-report.py)。
  aib summary write --file <總回報輸入.json> [--seat 艙/席]   先確認本輪整理過(aib memory tidy)→ 渲染、落回報檔、播動畫 → 總結收據
  aib summary status [--seat 艙/席]                            這一席最後一張總結收據

總結一定要先整理:本輪(交接一輪的起點之後)沒有整理收據 ⇒ rc 2,說先跑 aib memory tidy。
(交接不一樣:整理失敗或沒整理不擋交接,只在交接檔與收據記下來。)
渲染與落檔走 tools/report/aib-reply.sh(過 report-lint、存 pending/REPORT-FULL…、tmux 播流光);那支不過 ⇒ 不寫收據。
rc:0 寫了;2 本輪沒整理 / 渲染沒過;3 認不出席。
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
    """本輪起點 = max(交接記的這一輪起點, 這一席最後一張交接收據的時刻)。
    沒有任何一輪紀錄的席(codex / grok 從沒 /clear)不能退成 0 —— 那樣史上任何一張整理收據都算本輪。"""
    try:
        import aib_handoff
        rs = aib_handoff.current_round_start(code)
        last = (aib_handoff._read_json(aib_handoff.receipt_path(code)) or {}).get("ts") or 0.0
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


def write(report_json, seat="", runner=None):
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
    rec = {"at": _now(), "seat": ctx["code"], "model": ctx["model"], "file": m.group(1) if m else "",
           "tidy_status": tidy.get("status"), "tidy_at": tidy.get("at")}
    d = summary_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / ("%s.json" % ctx["code"])).write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
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
    o = ap.parse_args(argv)
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
