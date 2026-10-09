#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""右鍵「與誰換位」選單的候選清單。

the maintainer 2026-09-09 15:1x:「在所有艦長名稱顯示列,如 `👑 gpt-6-astra a seat 37% 🔧4m`,
可以右鍵,執行與誰換位。」

★ 候選是**算出來的,不是寫死的**:讀右欄自己算好的 `ai-rowmap.json`,
  與 `cooldown.py::captain_seat()` 同一份正本。今天已經因為「造第二個載體」
  付過三次學費(順位表、i18n 路徑、vault 的 C90)。

★ 三條規則,每一條都對應一個會出事的形狀:
  1. **只有艦長之間能換位**(`👑`/`⚓`/`🔑`);`👔` 主管、`👤` 組員不列進來 ——
     換位換的是「合議之後誰核可」,那是艦長才有的東西。
  2. **自己不列自己**:與自己換位是零動作,而零動作的選項會被點到。
  3. **停投名單上的席不列**:the maintainer 明令不用的席不該出現在指揮權的選單裡。
     ★ 名單讀不到 ⇒ **不列任何候選**(rc=3),不是「沒有人被擋」——
       讀不到與空名單在數量上都是 0,而處置相反。
"""

import argparse
import json
import os
import pathlib
import sys
import time

ROWMAP_FRESH_SEC = 120          # 沿用 aib-hdr.py 的門檻,不另發明
CAPTAIN_MARKS = {"👑", "⚓", "🔑"}

RC_OK = 0
RC_NO_ROWMAP = 2
RC_STOPLIST_UNREADABLE = 3


def state_root():
    return pathlib.Path(os.environ.get("EZ2_AIB_HOME") or
                        (pathlib.Path.home() / ".ez2-aib"))


def read_stoplist(path=None):
    """回 (set, why)。★ 讀不到回 (None, 理由) —— 不得回空集合。"""
    p = pathlib.Path(path or os.environ.get("AIB_DM_STOPLIST") or
                     (state_root() / "state" / "dm-stoplist.txt"))
    if not p.exists():
        return set(), "名單檔不存在(沒有名單不等於沒有人該被擋,但這裡當空集合處理並說出來)"
    try:
        lines = p.read_text().splitlines()
    except OSError as e:
        return None, "名單讀不到:%s" % e
    return {l.strip() for l in lines
            if l.strip() and not l.strip().startswith("#")}, None


def candidates(rowmap_path, me=None, now=None, stoplist_path=None):
    p = pathlib.Path(rowmap_path)
    if not p.exists():
        return None, "rowmap 不存在:%s" % p
    age = (time.time() if now is None else now) - p.stat().st_mtime
    if age > ROWMAP_FRESH_SEC:
        # ★ 舊了就說量不到,不得用一頂舊的冠去決定誰能換位。
        return None, "rowmap 舊了(age=%ds > %ds)" % (age, ROWMAP_FRESH_SEC)
    try:
        rows = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        return None, "rowmap 讀不動:%s" % e

    stop, stop_why = read_stoplist(stoplist_path)
    if stop is None:
        return None, stop_why

    seen, out = set(), []
    for r in rows:
        d = r.get("detail") or {}
        mark = d.get("mark")
        label = (d.get("label") or "").lstrip("@").rstrip("*")
        if mark not in CAPTAIN_MARKS or not label:
            continue
        if label == me:                      # 自己不列自己
            continue
        if label in stop:                    # 明令不用的席不進指揮權選單
            continue
        if label in seen:                    # rowmap 有重複顯示列
            continue
        seen.add(label)
        out.append({"seat": label, "mark": mark,
                    "engine": d.get("engine"), "model": d.get("model"),
                    "is_first": mark == "👑"})
    return out, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="右鍵換位選單的候選清單")
    ap.add_argument("--rowmap", required=True)
    ap.add_argument("--me", help="被右鍵點到的那一席;自己不列自己")
    ap.add_argument("--stoplist")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rows, why = candidates(args.rowmap, me=args.me, stoplist_path=args.stoplist)
    if rows is None:
        sys.stderr.write("yield-menu: %s\n" % why)
        return RC_STOPLIST_UNREADABLE if "名單" in why else RC_NO_ROWMAP
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for r in rows:
            # 選單標籤:與席位顯示列同形,讓人一眼認得出是同一個人
            print("%s\t%s %s %s @%s" % (r["seat"], r["mark"], r["engine"] or "?",
                                        r["model"] or "量不到", r["seat"]))
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
