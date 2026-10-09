#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""E5.2 產生器:從**版控裡的正文**抽出候選規則。它**不評分,也產不出能進 vault 的東西**。

規格 `docs/reports/SPEC-distill-golden-sample-0821.md`;契約 `src/memory/distill_contract.py`。

## 〇、這支檔最重要的性質

**它產出的每一則,`distill_contract` 都會拒收。** 那不是缺陷,是設計:
`landing`(這條規則該變成哪一道閘)**機械上產不出來** —— 它要的是
「什麼東西當初會擋住這件事」,那是判斷不是抽取。
於是這條鏈在物理上長這樣:

    產生器 → 候選(無 landing)→ **人**補 landing → 契約驗 → brain_write_plan 收據 → vault
                                    ↑ 這一格拿不掉

規格 §五 逐字:「蒸餾是必經的閘,不是可選的加速」。
★ 把那句話做成**機器上成立**的,靠的不是紀律,是**產生器故意產不出合格品**。

## 一、來源:版控裡的 commit 訊息(**不是**那 739 筆)

規格 §一 實測 `~/.ez2-persona-runtime/claude-conv-distill-staging.jsonl` 739 行
**沒有一個欄位裝內容**(最長值 36 字元,是標籤字串),所以它不能當來源。

本支讀的是 `git log` 的訊息正文。這是**我在規格沉默處做的決定**,理由三條:
1. **它是正文**,而且是這支艦隊真的在寫下教訓的地方(近 200 顆裡 236 行 ★ 句);
2. **每一則都釘得到一顆 sha** —— `evidence` 因此是**可機器複驗**的,不是自報的;
3. 沒有隱私面,而且任何人都能用同一個 rev-range 重跑。

★ **對話逐字稿刻意不收**:它有隱私面,而且釘不到 sha ——
  「這句話出自哪裡」會退回成一句自報。要收它需要另外的授權與另一套判準。

## 二、不做的事(每一條都是刻意的)

- **不評分、不排序、不挑「比較好的」**:規格 §四「評分不由作者做」,
  而這支是作者寫的。它只抽,順序照 `git log` 的順序。
- **不改寫原句**:抽出來的是逐字的原文。改寫等於在抽取階段偷偷做判斷。
- **不去重「語意相近」的**:只去**逐字完全相同**的(那是機械事實)。
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

STAR = "★"
# 這一支**不得**讀那 739 筆。路徑寫成常數,好讓釘子機械地檢查它沒有被用。
FORBIDDEN_SOURCE = "claude-conv-distill-staging.jsonl"
_WS = re.compile(r"\s+")


def _git(args, repo="."):
    return subprocess.run(["git", "-C", repo] + args, capture_output=True,
                          text=True, check=True).stdout


def _blocks(body):
    """把訊息切成「★ 開頭那一行 + 它的續行」若干塊。

    ★ 續行到空行或下一個 ★ 為止 —— 這批訊息的教訓常常跨兩三行,
      只取第一行會把「為什麼」那半截掉,而截掉的那半正是規格 §二 要的欄位。
    """
    out, cur = [], None
    for line in (body or "").splitlines():
        s = line.strip()
        if s.startswith(STAR):
            if cur:
                out.append(cur)
            cur = [s]
        elif cur is not None:
            if not s:
                out.append(cur)
                cur = None
            else:
                cur.append(s)
    if cur:
        out.append(cur)
    return [" ".join(b) for b in out]


def candidates(rev_range, repo=".", limit=None):
    # ★ 分隔符用 **git 的 %x 逃脫**,不要在 argv 裡放真的 NUL:
    #   subprocess 對 argv 裡的 \x00 直接 ValueError("embedded null byte"),
    #   而錯誤訊息指向 fork_exec,離「是我的 format 字串」很遠。
    fmt = "%H%x1f%s%x1f%B%x1e"
    args = ["log", "--no-merges", "--format=" + fmt, rev_range]
    if limit:
        args.insert(1, "-n%d" % limit)
    raw = _git(args, repo=repo)
    seen, out = set(), []
    for rec in raw.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        parts = rec.split("\x1f")
        if len(parts) < 3:
            continue
        sha, subject, body = parts[0].strip(), parts[1], parts[2]
        for text in _blocks(body):
            norm = _WS.sub(" ", text).strip()
            if len(norm) < 24:          # 太短的錨不成句,與契約的 min_quote 同一個理由
                continue
            if norm in seen:            # 只去**逐字**重複,不做語意判斷
                continue
            seen.add(norm)
            out.append({
                "id": hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12],
                "rule_text": norm,
                # ★ 三段裡的另外兩段**刻意留空**:機械上產不出來,
                #   留空才會被契約擋住,而那正是這支檔存在的理由。
                "why": None,
                "landing": None,
                "evidence": {"kind": "commit-message", "sha": sha,
                             "subject": subject},
                "stars": len(norm) - len(norm.lstrip(STAR)),
            })
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("rev_range")
    ap.add_argument("--repo", default=".")
    ap.add_argument("-n", "--limit", type=int, default=None)
    ap.add_argument("--out", default="-")
    args = ap.parse_args(argv)
    cands = candidates(args.rev_range, repo=args.repo, limit=args.limit)
    lines = "".join(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n"
                    for c in cands)
    if args.out == "-":
        sys.stdout.write(lines)
    else:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(lines)
    sys.stderr.write("候選 %d 則;**每一則都會被 distill_contract 拒收**"
                     "(缺 why 與 landing),那是設計不是缺陷。\n" % len(cands))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
