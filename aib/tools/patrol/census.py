#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""巡查普查器:人口現算,寫死名單/日期篩/mtime 窗不得當唯一探測器。

母體:
  (a) git worktree list --porcelain 全列
  (b) git ls-remote 未合入 main 的遠端分支;遠端有、本地沒樹,與沒開工分開報
  (c) pending 目錄現列,不是 find -mtime

高亮(窗內/窗外)只是欄,篩掉的列仍留在全列裡。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


class CensusError(Exception):
    """探測失敗,不得假裝人口是空的。"""


def _run(argv, *, cwd=None):
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as err:
        raise CensusError("無法執行 %s: %s" % (argv[0], err)) from err
    return proc


def parse_worktree_porcelain(text: str) -> list[dict]:
    entries = []
    current = None
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        if line.startswith("worktree "):
            if current is not None:
                entries.append(current)
            current = {
                "path": line[len("worktree "):],
                "head": None,
                "branch": None,
                "detached": False,
                "bare": False,
            }
        elif current is None:
            continue
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):]
        elif line == "detached":
            current["detached"] = True
        elif line == "bare":
            current["bare"] = True
    if current is not None:
        entries.append(current)
    return entries


def list_worktrees(repo: Path) -> list[dict]:
    proc = _run(["git", "-C", str(repo), "worktree", "list", "--porcelain"])
    if proc.returncode != 0:
        raise CensusError(
            "git worktree list rc=%d: %s" % (proc.returncode, proc.stderr.strip())
        )
    return parse_worktree_porcelain(proc.stdout)


def _ahead_behind(repo: Path, head: str, main_ref: str) -> tuple[int | None, int | None]:
    if not head or not main_ref:
        return None, None
    proc = _run(
        ["git", "-C", str(repo), "rev-list", "--left-right", "--count",
         "%s...%s" % (main_ref, head)]
    )
    if proc.returncode != 0:
        return None, None
    parts = proc.stdout.strip().split()
    if len(parts) != 2:
        return None, None
    try:
        behind, ahead = int(parts[0]), int(parts[1])
    except ValueError:
        return None, None
    return ahead, behind


def _dirty(path: str) -> bool | None:
    if not path or not os.path.isdir(path):
        return None
    proc = _run(["git", "-C", path, "status", "--porcelain"])
    if proc.returncode != 0:
        return None
    return bool(proc.stdout.strip())


def _branch_short(branch: str | None, detached: bool) -> str | None:
    if detached or not branch:
        return None
    prefix = "refs/heads/"
    if branch.startswith(prefix):
        return branch[len(prefix):]
    return branch


def decorate_worktrees(repo: Path, entries: list[dict], *, main_ref: str,
                       highlight_branch: str | None) -> list[dict]:
    rows = []
    for entry in entries:
        path = entry.get("path") or ""
        head = entry.get("head")
        short = _branch_short(entry.get("branch"), entry.get("detached", False))
        ahead, behind = _ahead_behind(repo, head, main_ref)
        highlighted = bool(
            highlight_branch
            and short
            and highlight_branch in short
        )
        rows.append({
            "path": path,
            "branch": short,
            "head": head,
            "ahead": ahead,
            "behind": behind,
            "dirty": _dirty(path),
            "detached": bool(entry.get("detached")),
            "bare": bool(entry.get("bare")),
            "highlighted": highlighted,
        })
    return rows


def list_remote_heads(repo: Path, remote: str) -> list[tuple[str, str]]:
    proc = _run(["git", "-C", str(repo), "ls-remote", "--heads", remote])
    if proc.returncode != 0:
        raise CensusError(
            "git ls-remote rc=%d: %s" % (proc.returncode, proc.stderr.strip())
        )
    rows = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        sha, ref = parts[0], parts[1]
        if ref.startswith("refs/heads/"):
            rows.append((ref[len("refs/heads/"):], sha))
    return rows


def _is_merged_into(repo: Path, sha: str, main_sha: str) -> bool:
    proc = _run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, main_sha]
    )
    return proc.returncode == 0


def _main_sha(repo: Path, main_ref: str) -> str:
    proc = _run(["git", "-C", str(repo), "rev-parse", main_ref])
    if proc.returncode != 0:
        raise CensusError("rev-parse %s 失敗: %s" % (main_ref, proc.stderr.strip()))
    return proc.stdout.strip()


def list_unmerged_remote_branches(
    repo: Path,
    *,
    remote: str,
    main_ref: str,
    worktrees: list[dict],
) -> dict:
    """未合入 main 的遠端分支。有本地樹 / 沒本地樹分開,不可混成一格。"""
    try:
        heads = list_remote_heads(repo, remote)
    except CensusError:
        return {
            "with_local_tree": [],
            "without_local_tree": [],
            "remote_missing": True,
        }
    main_sha = _main_sha(repo, main_ref)
    local_branches = {
        row["branch"] for row in worktrees if row.get("branch")
    }
    local_heads = {
        row["head"] for row in worktrees if row.get("head")
    }
    with_tree = []
    without_tree = []
    for name, sha in heads:
        if name == "main":
            continue
        if _is_merged_into(repo, sha, main_sha):
            continue
        item = {"branch": name, "head": sha}
        if name in local_branches or sha in local_heads:
            with_tree.append(item)
        else:
            without_tree.append(item)
    return {
        "with_local_tree": with_tree,
        "without_local_tree": without_tree,
        "remote_missing": False,
    }


def list_pending(pending_dir: Path, *, highlight_mtime_hours: float | None) -> list[dict]:
    """目錄現列。mtime 窗只影響 highlighted,不得把列拿掉。"""
    if not pending_dir.is_dir():
        raise CensusError("pending 不是目錄: %s" % pending_dir)
    cutoff = None
    if highlight_mtime_hours is not None:
        cutoff = time.time() - (highlight_mtime_hours * 3600.0)
    rows = []
    for entry in sorted(pending_dir.iterdir(), key=lambda p: p.name):
        try:
            info = entry.lstat()
        except OSError:
            continue
        mtime = info.st_mtime
        highlighted = True if cutoff is None else mtime >= cutoff
        rows.append({
            "name": entry.name,
            "path": str(entry),
            "mtime": mtime,
            "is_dir": entry.is_dir(),
            "highlighted": highlighted,
        })
    return rows


def census(
    repo: Path,
    *,
    pending_dir: Path | None = None,
    remote: str = "origin",
    main_ref: str = "origin/main",
    highlight_branch: str | None = None,
    highlight_pending_mtime_hours: float | None = None,
) -> dict:
    repo = Path(repo).resolve()
    worktrees = decorate_worktrees(
        repo, list_worktrees(repo),
        main_ref=main_ref,
        highlight_branch=highlight_branch,
    )
    remote_unmerged = list_unmerged_remote_branches(
        repo, remote=remote, main_ref=main_ref, worktrees=worktrees,
    )
    pending = []
    if pending_dir is not None:
        pending = list_pending(
            Path(pending_dir),
            highlight_mtime_hours=highlight_pending_mtime_hours,
        )
    return {
        "repo": str(repo),
        "main_ref": main_ref,
        "worktrees": worktrees,
        "remote_unmerged": remote_unmerged,
        "pending": pending,
        "highlight": {
            "branch_substr": highlight_branch,
            "pending_mtime_hours": highlight_pending_mtime_hours,
        },
    }


def scan_by_dispatch_names(worktrees: list[dict], names: list[str]) -> list[dict]:
    """寫死路徑/名字名單。只給臂當負控,不是普查。"""
    needles = tuple(names)
    found = []
    for row in worktrees:
        path = row.get("path") or ""
        branch = row.get("branch") or ""
        if any(needle in path or needle in branch for needle in needles):
            found.append(row)
    return found


def scan_pending_by_mtime(rows: list[dict], *, max_age_hours: float) -> list[dict]:
    """mtime 窗當人口。只給臂當負控,不是普查。"""
    cutoff = time.time() - (max_age_hours * 3600.0)
    return [row for row in rows if row.get("mtime", 0) >= cutoff]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="巡查普查:人口現算")
    parser.add_argument("--repo", default=".", help="git 倉庫路徑")
    parser.add_argument("--pending", default="", help="pending 目錄;空則不掃")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--highlight-branch", default="",
                        help="分支名含子字串則 highlighted;列仍全留")
    parser.add_argument("--highlight-pending-mtime-hours", type=float, default=None)
    args = parser.parse_args(argv)
    pending = Path(args.pending) if args.pending else None
    highlight_branch = args.highlight_branch or None
    try:
        result = census(
            Path(args.repo),
            pending_dir=pending,
            remote=args.remote,
            main_ref=args.main_ref,
            highlight_branch=highlight_branch,
            highlight_pending_mtime_hours=args.highlight_pending_mtime_hours,
        )
    except CensusError as err:
        sys.stderr.write("%s\n" % err)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
