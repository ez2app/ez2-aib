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


def _git_out(repo, *args):
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=8,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (p.stdout or "").strip() if p.returncode == 0 else None


def _norm_url(u):
    """同一個遠端倉的各種寫法化成 https://<主機>/<擁有者>/<倉>(小寫);本機路徑照舊。
    認得:git@host:o/r、host:o/r(scp 寫法沒帶使用者)、ssh://[user@]host[:port]/o/r、https://…、git://…。"""
    x = u.strip().rstrip("/")
    x = x[:-4] if x.endswith(".git") else x
    if "://" in x:
        x = x.split("://", 1)[1]
        head, _, rest = x.partition("/")
        head = head.rsplit("@", 1)[-1].split(":", 1)[0]       # 去使用者、去埠號(:22、:2222 都一樣)
        x = head + "/" + rest
    elif ":" in x and not x.startswith(("/", ".", "~")) and "/" not in x.split(":", 1)[0]:
        host, _, path = x.partition(":")                       # scp 寫法:[user@]host:owner/repo
        x = host.rsplit("@", 1)[-1] + "/" + path.lstrip("/")
    else:
        return x
    return ("https://" + x).lower()


def project_key(repo):
    """M1(10-13):「這個專案」的身分 = 最舊的根提交 + 遠端網址(正規化)。同一個專案不同 clone(Mini 正本、推包用的
    ~/wt-ship-*)⇒ 同一個鍵 ⇒ 同一個沿用池。下列一律回 None(退回舊的 git 目錄身分):沒有根提交、沒有任何遠端、
    淺複製(CI 的「根」是淺複製邊界,每次 fetch 都會變 —— 審查)。遠端多個時優先 github.com 的、其次字母序第一個。
    只取最舊的一個根:別條歷史併進來不會換身分(審查)。"""
    if _git_out(repo, "rev-parse", "--is-shallow-repository") == "true":
        return None
    roots = _git_out(repo, "log", "--max-parents=0", "--format=%ct %H", "HEAD")
    urls = _git_out(repo, "config", "--get-regexp", r"^remote\..*\.url$")
    if not roots or not urls:
        return None
    root = sorted((int(t), h) for t, h in (line.split() for line in roots.splitlines() if len(line.split()) == 2))[0][1]
    norm = sorted({_norm_url(line.split(None, 1)[1]) for line in urls.splitlines() if len(line.split(None, 1)) == 2})
    if not norm:
        return None
    gh = [u for u in norm if u.startswith("https://github.com/")]
    return "%s|%s" % (root, (gh or norm)[0])


def repo_id(repo):
    """沿用池 / 收據的倉身分。M1(10-13):有 project_key ⇒ 專案身分(各 clone 共用);沒有 ⇒ legacy_repo_id。"""
    key = project_key(repo)
    if key:
        return hashlib.sha256(("project:" + key).encode("utf-8")).hexdigest()[:16]
    return legacy_repo_id(repo)


def legacy_repo_id(repo):
    """M1 之前的身分:git common dir 的路徑雜湊(同一個 clone 的工作樹共用;不同 clone 各一個)。
    合池時拿來認舊池、過渡期收舊列。"""
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
