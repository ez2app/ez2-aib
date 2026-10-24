#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""遺漏回顧(docs/DESIGN-memory-tidy-1013.md §8e.7;the maintainer 10-13 17:5x)。

the maintainer:「verify 全部完成後,自動加上一句,仔細回顧本次開發4小時內,有什麼遺漏?請再適當的更新回人格與第二大腦(專案)」。

  aib handoff review collect [--seat 艙/席] [--out 檔]   程式備料:最近 4 小時 the maintainer 每句原話、提交;每句一列待歸類
  aib handoff review apply --file 檔 [--seat 艙/席]      驗:每句都有歸類(做了 + sha/檔 / 待辦 + 哪條 / 沒做 + 原因)
                                                        ⇒ 寫第二大腦一則「遺漏回顧」+ 回顧收據;之後照草稿說明跑 aib memory tidy 補人格 / 專案
verify 第 9 項看回顧收據(本輪、每句都歸類)。
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "memory", HERE.parent / "identity"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

HOURS = 4
HEAD = "<!-- review:draft seat=%s at=%s lines=%s -->"
LINE_RE = re.compile(r"^- \[(\d+)\] (.*?) ⇒ 歸類[:\uff1a](.*)$")
KIND_RE = re.compile(r"^\s*(做了|待辦|沒做)\s*[:\uff1a]?\s*(.*)$")


def receipt_path(code):
    import aib_handoff
    return aib_handoff.handoff_dir() / ("%s.review.json" % code)


def collect(seat="", now=None, words=None, commits=None):
    import memory_tidy
    ctx = memory_tidy.seat_info(seat)
    now = now or time.time()
    since = datetime.datetime.fromtimestamp(now - HOURS * 3600).astimezone().isoformat(timespec="seconds")
    words = words if words is not None else memory_tidy.human_words(ctx, since, span_rounds=True)
    if commits is None:
        commits = memory_tidy._git(".", "log", "--since", "%d hours ago" % HOURS, "--format=%h %s", "-n", "40")
    lines = ["# 遺漏回顧 @%s(最近 %d 小時)" % (ctx["code"], HOURS),
             HEAD % (ctx["code"], int(now), len(words)),
             "每一句 the maintainer 的要求在「歸類:」後面寫一種:做了 + sha 或檔 / 待辦 + 哪一條 / 沒做 + 原因。一句都不能空。",
             "", "## the maintainer 這 4 小時說的(逐句歸類)"]
    for i, (t, w) in enumerate(words, 1):
        lines.append("- [%d] %s %s ⇒ 歸類:" % (i, t, w.replace("\n", " ")[:160]))
    lines += ["", "## 答應別席的事(完成沒;沒完成寫進待辦)", "", "",
              "## 說過「之後處理」卻沒進待辦的(補進待辦)", "", "",
              "## 這 4 小時學到的(坑 / 指正 / 新規則;寫進人格或專案了沒)", "", "",
              "## 遺漏清單與補法", "", "",
              "## 提交(機器)"] + ["- " + ln for ln in (commits or "").splitlines()] + [
              "", "做完 apply 後:照「學到的」跑 aib memory tidy collect → 填 → aib memory tidy apply --add-only(人格新條目附 the maintainer 原話證據、本席列補待辦;只補不改)。"]
    return "\n".join(lines) + "\n"


def apply(text, seat="", now=None, writer=None):
    import memory_tidy
    ctx = memory_tidy.seat_info(seat)
    m = re.search(r"<!-- review:draft seat=(\S+) at=(\d+) lines=(\d+) -->", text or "")
    if not m or m.group(1) != ctx["code"]:
        raise ValueError("回顧草稿頭不見了或不是這一席的;重新 aib handoff review collect")
    rows, empty = [], []
    for ln in text.splitlines():
        lm = LINE_RE.match(ln.strip())
        if not lm:
            continue
        km = KIND_RE.match(lm.group(3))
        if not km or len(km.group(2).strip()) < 2:
            empty.append(lm.group(1))
        rows.append({"n": int(lm.group(1)), "kind": km.group(1) if km else "", "note": (km.group(2) if km else "")[:200]})
    want = list(range(1, int(m.group(3)) + 1))
    got = sorted(r["n"] for r in rows)
    if got != want:                                    # 刪掉的句子不能當沒這句(三輪審查 2)
        raise ValueError("the maintainer 的句子要 1..%d 每句都在、各一次;現在是 %s" % (len(want), got))
    if empty:
        raise ValueError("這幾句還沒歸類(做了 + sha 或檔 / 待辦 + 哪一條 / 沒做 + 原因):%s" % ", ".join(empty))
    body = text.split("## 提交(機器)")[0].strip()
    note_uid = ""
    try:
        import aib_memory
        rec = (writer or aib_memory.write)("progress", "@%s 遺漏回顧" % ctx["code"], body, tags=["aib/review"], seat=seat,
                                           project=ctx.get("project_uid") or "")
        note_uid = rec.get("uid", "")
    except Exception as exc:  # noqa: BLE001
        note_uid = "failed: %s" % str(exc)[:120]
    out = {"seat": ctx["code"], "at": time.time(), "draft_at": int(m.group(2)), "lines": len(rows),
           "by_kind": {k: sum(1 for r in rows if r["kind"] == k) for k in ("做了", "待辦", "沒做")},
           "note": note_uid, "words_source": _source_now(ctx, now)}
    p = receipt_path(ctx["code"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _source_now(ctx, now=None):
    """回顧收據的原話來源:重讀一次(找不到對話檔 ⇒ transcript_missing,不當成 0 句;#312 審查 B-M1)。"""
    import memory_tidy
    try:
        since = datetime.datetime.fromtimestamp((now or time.time()) - HOURS * 3600).astimezone().isoformat(timespec="seconds")
        memory_tidy.human_words(ctx, since, span_rounds=True)
        return memory_tidy.words_source(ctx["code"])
    except Exception:  # noqa: BLE001
        return ""


def done_this_round(code, start):
    try:
        rec = json.loads(receipt_path(code).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return rec if float(rec.get("at") or 0) >= start else {}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aib handoff review")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--seat", default="")
    c.add_argument("--out", default="")
    a = sub.add_parser("apply")
    a.add_argument("--seat", default="")
    a.add_argument("--file", required=True)
    o = ap.parse_args(argv)
    try:
        if o.cmd == "collect":
            text = collect(o.seat)
            if o.out:
                Path(o.out).write_text(text, encoding="utf-8")
                print("回顧草稿:%s(逐句歸類後跑 aib handoff review apply --file %s)" % (o.out, o.out))
            else:
                print(text)
            return 0
        rec = apply(Path(o.file).read_text(encoding="utf-8"), o.seat)
        if not rec.get("note") or str(rec.get("note")).startswith("failed"):     # 審查 B-S4:跟總結一致
            print("遺漏回顧的第二大腦筆記寫不進:%s;修好再跑一次" % (rec.get("note") or "沒有 uid"), file=sys.stderr)
            return 2
        print("遺漏回顧已寫:%d 句(%s);筆記 %s。接著照「學到的」跑 aib memory tidy collect → 填 → aib memory tidy apply --add-only(只補不改)。"
              % (rec["lines"], rec["by_kind"], rec["note"]))
        return 0
    except (ValueError, OSError) as exc:
        print("aib handoff review:%s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
