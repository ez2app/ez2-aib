# SPDX-License-Identifier: Apache-2.0
"""巡查收據閘:缺普查欄、或普查人口被寫死名單縮小,都不算巡查。

§57.2 #12(b) 判過「收據拒收沒 live 讀數」會變成永不開火或門檻比洞大。
本閘的選擇:

- 不接到 pre-push / 全艦合入。沒巡查產物就全艦推不了,門檻比它所守的洞大。
- 不做成選配。選配等於沒這條。
- 開火點=一份**宣稱是巡查產物**的收據,對照**另一次現取**的 live 普查。
  live 不得是產物自己的 census 欄(同一物件差集恆空)。
- 守不了「作者有沒有看那一行」(注視)。守得到「缺欄 / 縮小人口的產物被拒」。

缺 census 鍵 → census_column_missing。
live 就是產物 census 欄(同一物件) → live_is_receipt_census。
census.worktrees 路徑集合小於 live → census_population_shrunk。
pending 同理 → pending_population_shrunk。
產物比 live 多出來的成員不違規(第一次之後現實縮了)。
"""
from __future__ import annotations

import pathlib

import argparse
import json
import os
from pathlib import Path
import sys

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from census import CensusError, census as live_census

# ★ 字串一律走既有的 i18n 正本(i18n/{zh-TW,en,ja,ko}.json + src/cockpit/i18n_lib.py)。
#   不在本檔內建第二套字串表 —— 大綱 §610 十三條地基逐字列著「i18n 是地基」。
# ★★ 順序是承重的(33f17 抓到):連續兩次 insert(0) 會讓後 insert 的排前面 ⇒ 主庫贏本樹,
#   而 i18n_lib 的字典根綁 __file__ ⇒ 會讀回主庫舊字典。本樹 insert(0)、fallback append。
_here = pathlib.Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
_fallback = pathlib.Path.home() / "ez2-aib" / "src" / "cockpit"
if str(_fallback) not in sys.path:
    sys.path.append(str(_fallback))
import i18n_lib  # noqa: E402

LOC = os.environ.get("EZ2_AIB_LOCALE", "zh-TW")


def T(key, params=None):
    """唯一的字串入口 —— locale 只在這裡決定一次。"""
    return i18n_lib.t(key, params, locale=LOC)



REPORT_KIND = "patrol_report"
CENSUS_KEY = "census"
WINDOW_KEY = "window"


class PatrolReceiptError(Exception):
    """這份產物不算巡查。"""


def _paths(rows):
    out = set()
    for row in rows or []:
        path = row.get("path")
        if path:
            out.add(os.path.realpath(str(path)))
    return out


def _names(rows):
    return {row.get("name") for row in (rows or []) if row.get("name")}


def accept_patrol_receipt(receipt, live):
    """receipt 必須帶普查欄,且人口不得小於獨立取得的 live。

    live 必須是另一次現取,不得是產物自己的 census 欄(同一物件差集恆空)。
    違規方向=產物比較少(live − rec):寫死名單 / mtime 窗縮小。
    產物比較多(rec − live)不違規:第一次現取之後有人拆樹,或第二次還沒看見。
    """
    if not isinstance(receipt, dict):
        raise PatrolReceiptError("receipt_not_object")
    if receipt.get("kind") != REPORT_KIND:
        raise PatrolReceiptError("kind_missing")
    if CENSUS_KEY not in receipt:
        raise PatrolReceiptError("census_column_missing")
    block = receipt[CENSUS_KEY]
    if not isinstance(block, dict) or "worktrees" not in block:
        raise PatrolReceiptError("census_column_missing")
    if not isinstance(live, dict) or "worktrees" not in live:
        raise PatrolReceiptError("live_census_missing")
    if live is block:
        raise PatrolReceiptError("live_is_receipt_census")

    live_paths = _paths(live["worktrees"])
    rec_paths = _paths(block.get("worktrees"))
    missing_wt = live_paths - rec_paths
    if missing_wt:
        raise PatrolReceiptError(
            "census_population_shrunk:worktrees:%d" % len(missing_wt)
        )

    live_pending = live.get("pending") or []
    if live_pending:
        if "pending" not in block:
            raise PatrolReceiptError("census_column_missing")
        missing_p = _names(live_pending) - _names(block.get("pending"))
        if missing_p:
            raise PatrolReceiptError(
                "pending_population_shrunk:%d" % len(missing_p)
            )

    window = receipt.get(WINDOW_KEY) or {}
    if window:
        extra_wt = _paths(window.get("worktrees")) - rec_paths
        if extra_wt:
            raise PatrolReceiptError("window_not_subset_of_census")
        rec_pending_names = _names(block.get("pending"))
        extra_p = _names(window.get("pending")) - rec_pending_names
        if extra_p:
            raise PatrolReceiptError("window_not_subset_of_census")
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="拒收缺普查欄或人口被縮小的巡查產物")
    parser.add_argument("--file", required=True, help="要驗的巡查產物 JSON")
    parser.add_argument("--repo", default=".", help="live 普查的倉庫")
    parser.add_argument("--pending", default="", help="live pending 目錄")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--main-ref", default="origin/main")
    args = parser.parse_args(argv)
    path = Path(args.file)
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        sys.stderr.write(T("aib.patrol.read_failed", {"err": err}))
        return 2
    pending = Path(args.pending) if args.pending else None
    try:
        live = live_census(
            Path(args.repo),
            pending_dir=pending,
            remote=args.remote,
            main_ref=args.main_ref,
        )
        accept_patrol_receipt(receipt, live)
    except (CensusError, PatrolReceiptError) as err:
        sys.stderr.write("%s\n" % err)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
