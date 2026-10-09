#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""255(c) 每倉沿用池。路徑 ~/.ez2-aib/resume/<倉識別>/rows.jsonl。兩工作樹共 git-common-dir 則共池。"""
from __future__ import annotations

import fcntl
import hashlib
import json
import jsonl_safe  # 10-04:jsonl 行界只認 "\n"(見該檔)
import os
import subprocess
from pathlib import Path


def repo_id(repo):
    repo = str(repo)
    common = None
    try:
        p = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=8, stdin=subprocess.DEVNULL,
        )
        if p.returncode == 0:
            common = (p.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        common = None
    key = os.path.realpath(common or repo)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def pool_dir(repo, home=None):
    root = Path(home or os.environ.get("HOME") or str(Path.home()))
    return root / ".ez2-aib" / "resume" / repo_id(repo)


def rows_path(repo, home=None):
    return pool_dir(repo, home=home) / "rows.jsonl"


def _lock_path(repo, home=None):
    return pool_dir(repo, home=home) / "rows.lock"


def append_row(repo, row, home=None):
    d = pool_dir(repo, home=home)
    d.mkdir(parents=True, exist_ok=True)
    lockp = _lock_path(repo, home=home)
    with open(lockp, "a+", encoding="utf-8") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        with open(rows_path(repo, home=home), "a", encoding="utf-8") as rf:
            rf.write(jsonl_safe.dumps(row) + "\n")


def load_rows(repo, home=None):
    path = rows_path(repo, home=home)
    if not path.is_file():
        return []
    lockp = _lock_path(repo, home=home)
    lockp.parent.mkdir(parents=True, exist_ok=True)
    out = []
    with open(lockp, "a+", encoding="utf-8") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_SH)
        text = path.read_text(encoding="utf-8")
    for line in jsonl_safe.split_lines(text):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def last_run_durations(repo, home=None):
    """每支測試最近一次**實跑**的耗時(沿用的列不算,那不是它真正要花的時間)。

    回 {測試名: elapsed_s}。列的順序就是寫入順序,後寫的蓋前面的。
    """
    out = {}
    for row in load_rows(repo, home=home):
        if not isinstance(row, dict) or row.get("kind") != "run":
            continue
        name, el = row.get("test"), row.get("elapsed_s")
        if isinstance(name, str) and isinstance(el, (int, float)) and not isinstance(el, bool) and el >= 0:
            out[name] = float(el)
    return out
