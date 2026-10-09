# SPDX-License-Identifier: Apache-2.0
"""巡查產物產生器:包 census.py,輸出帶普查欄的報表。

高亮(分支子字串 / pending mtime 窗)只進 window,不准當人口。
census 欄永遠是全列。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from census import CensusError, census as take_census
from gate import (
    CENSUS_KEY, REPORT_KIND, WINDOW_KEY, PatrolReceiptError,
    accept_patrol_receipt,
)


def build_patrol_report(
    repo,
    *,
    pending_dir=None,
    remote="origin",
    main_ref="origin/main",
    highlight_branch=None,
    highlight_pending_mtime_hours=None,
    live=None,
):
    """組一份巡查產物。live 給了就用(測試配對),沒給就現算。"""
    census_block = live if live is not None else take_census(
        Path(repo),
        pending_dir=Path(pending_dir) if pending_dir else None,
        remote=remote,
        main_ref=main_ref,
        highlight_branch=highlight_branch,
        highlight_pending_mtime_hours=highlight_pending_mtime_hours,
    )
    window_worktrees = [
        row for row in census_block.get("worktrees") or []
        if row.get("highlighted")
    ]
    window_pending = [
        row for row in census_block.get("pending") or []
        if row.get("highlighted")
    ]
    payload = {
        "kind": REPORT_KIND,
        WINDOW_KEY: {
            "worktrees": window_worktrees,
            "pending": window_pending,
        },
    }
    payload[CENSUS_KEY] = census_block
    return payload


def check_report_against_fresh_census(
    payload,
    repo,
    *,
    pending_dir=None,
    remote="origin",
    main_ref="origin/main",
    highlight_branch=None,
    highlight_pending_mtime_hours=None,
):
    """第二次現取 live,再過閘。禁止把產物自己的 census 欄當 live。"""
    live = take_census(
        Path(repo),
        pending_dir=Path(pending_dir) if pending_dir else None,
        remote=remote,
        main_ref=main_ref,
        highlight_branch=highlight_branch,
        highlight_pending_mtime_hours=highlight_pending_mtime_hours,
    )
    accept_patrol_receipt(payload, live)
    return live


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="產出帶普查欄的巡查報表")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--pending", default="")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--highlight-branch", default="")
    parser.add_argument("--highlight-pending-mtime-hours", type=float, default=None)
    args = parser.parse_args(argv)
    pending = Path(args.pending) if args.pending else None
    highlight_branch = args.highlight_branch or None
    try:
        payload = build_patrol_report(
            Path(args.repo),
            pending_dir=pending,
            remote=args.remote,
            main_ref=args.main_ref,
            highlight_branch=highlight_branch,
            highlight_pending_mtime_hours=args.highlight_pending_mtime_hours,
        )
        check_report_against_fresh_census(
            payload,
            Path(args.repo),
            pending_dir=pending,
            remote=args.remote,
            main_ref=args.main_ref,
            highlight_branch=highlight_branch,
            highlight_pending_mtime_hours=args.highlight_pending_mtime_hours,
        )
    except (CensusError, PatrolReceiptError) as err:
        sys.stderr.write("%s\n" % err)
        return 2
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
