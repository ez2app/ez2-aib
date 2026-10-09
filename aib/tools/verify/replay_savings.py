#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Replay real commits against the reuse pool: how much of a full run would each commit have saved?

For every commit (first-parent, newest first) the changed paths are matched against the inputs of the latest
row of each test in the pool.  A test is reusable after a commit only if
  - its latest row is green, measured and not external,
  - none of the files it read (open/handoff/probe/missing, or its own file) changed,
  - no directory it listed gained or lost an entry (added/deleted/renamed paths),
  - it has no git-state input (githead/gitwt/gitrefs change with every commit),
  - the commit did not touch the tracer's own code (that invalidates every row on every machine).
Saved share = seconds of reusable tests / seconds of all tests in the pool.  Everything else is a rerun.
This is an upper bound for "same machine, one commit later": it ignores row expiry and date-crossing.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import shadow_tracer as st  # noqa: E402

GIT_STATE_KINDS = ("githead", "gitwt", "gitrefs", "gitq")   # gitq:回放沒法在每個舊提交重跑那條查詢 ⇒ 保守當每次都重跑(低估)


def tracer_paths(repo_rel_dir="tools/verify"):
    """Repo-relative paths of the tracer code (same set as the tracer digest)."""
    out = set()
    for rel, _p in st._tracer_code_entries(HERE):
        out.add("%s/%s" % (repo_rel_dir, rel))
    return out


def latest_rows(pool_files, repo=None):
    """每支測試最新一列。repo 給了(10-05,aib test --estimate 給外部倉用)⇒ 只收這個倉的列:
    池子是全機共用的,別的倉也可能有 tests/test_x.py,混進來估出來的數字就是別人的。
    列的 repo_id(同一個倉的所有工作樹共用)對得上才收;列沒有 repo_id 就比路徑;兩個都沒有 ⇒ 不收(來源不明)。"""
    want_id = want_path = None
    if repo is not None:
        import reuse_index as ri
        want_id = ri.repo_identity(repo)
        want_path = str(Path(repo).resolve())
    rows = {}
    for f in pool_files:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not (isinstance(r, dict) and r.get("test")):
                    continue
                if repo is not None:
                    if r.get("repo_id") and want_id:
                        if str(r["repo_id"]) != str(want_id):
                            continue
                    elif not r.get("repo") or str(r["repo"]) != want_path:
                        continue
                rows[r["test"]] = r
    return rows


def is_aib_repo(repo):
    """這個倉是不是 aib 自己(追蹤器的程式碼在它裡面)。外部倉的提交改不到追蹤器 ⇒ 回放時不必把它當「全部作廢」。"""
    import reuse_index as ri
    mine = ri.repo_identity(str(HERE.parents[1]))
    theirs = ri.repo_identity(repo)
    if mine and theirs:
        return str(mine) == str(theirs)
    return Path(repo).resolve() == HERE.parents[1]


def estimate(repo, n=50, pool_dir=None):
    """aib test --estimate:拿這個倉最近 n 個提交回放它自己的紀錄。回 (summary, tests, total_s);沒有紀錄 ⇒ (None, 0, 0)。"""
    pool_dir = pool_dir or str(st.shadow_dir_default())
    rows = latest_rows(sorted(glob.glob(os.path.join(pool_dir, "*.rows.jsonl"))), repo=repo)
    if not rows:
        return None, 0, 0.0
    res, total = replay(rows, commits(repo, "HEAD", n), tracer=None if is_aib_repo(repo) else set())
    return summary(res), len(rows), total


WIDE_SHARE = 0.25          # 被四分之一以上(有紀錄的)測試讀的檔 = 「牽動很多測試的檔」


def _lockfiles():
    import reuse_index as ri
    return set(ri.LOCKFILE_NAMES)


LOCKFILES = _lockfiles()
KEYABLE_EXT = (".json",)   # 可以宣告成字典、以鍵為單位的(§15.3c 第 2 項);gettext 的 .mo 本來就自動


def push_rate(repo, days=7, stats_path=None, now=None):
    """這個專案一週推幾次:有 aib ship 推前檢查紀錄就用它(最近 7 天完整跑的那幾輪,照實數,不放大);
    沒有就數最近 7 天主線(first-parent)提交數。回 (每週次數, 來源 "gates"/"commits", 實際數到的次數, 涵蓋天數)。"""
    import time as _t
    now = now or _t.time()
    try:
        sys.path.insert(0, str(HERE))
        import pushgate
        since = _t.strftime("%Y-%m-%dT%H:%M:%S", _t.localtime(now - days * 86400))
        rows = [r for r in pushgate.read_stats(repo, path=stats_path, since=since)
                if float(r.get("ran") or 0) + float(r.get("reused") or 0) >= 20 and int(r.get("rc") or 0) != 5]
    except Exception:  # noqa: BLE001
        rows = []
    if rows:
        # 不按紀錄涵蓋的天數放大(紀錄才一兩天時放大 5 倍會誇大);照實數最近 7 天推了幾次
        return float(len(rows)), "gates", len(rows), float(days)
    out = subprocess.run(["git", "-C", str(repo), "log", "--first-parent", "--since=%d days ago" % days, "--format=%h"],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.split()
    return float(len(out)), "commits", len(out), float(days)


def gate_wall_s(repo, total_cpu_s, jobs=3, stats_path=None):
    """一次完整推前檢查的牆鐘秒數:有紀錄 ⇒ 最近完整跑的中位數;沒有 ⇒ 全部測試秒數 / 分份數。"""
    try:
        import pushgate
        walls = [float(r.get("wall_s") or 0) for r in pushgate.read_stats(repo, path=stats_path)
                 if float(r.get("ran") or 0) >= 20 and int(r.get("rc") or 0) != 5 and r.get("wall_s")]
    except Exception:  # noqa: BLE001
        walls = []
    return statistics.median(walls[-10:]) if walls else total_cpu_s / max(1, jobs)


def scenarios(repo, n=50, pool_dir=None, stats_path=None, now=None):
    """10-06 the maintainer:讓客戶知道「什麼情況下推會省最多、自己要做什麼才能更常落在那種情況」。只讀紀錄、不跑測試。
    回放最近 n 個提交,每個歸成一類(先後:改到 aib 追蹤器 > 改鎖檔 > 改到牽動很多測試的檔 > 一般),各類平均省幾 %;
    牽動很多測試的檔逐一點名(被幾個提交改到、幾支測試讀、能不能宣告成字典);再算「照建議做之後」一週能省多少。
    沒有紀錄 ⇒ None。"""
    pool_dir = pool_dir or str(st.shadow_dir_default())
    rows = latest_rows(sorted(glob.glob(os.path.join(pool_dir, "*.rows.jsonl"))), repo=repo)
    if not rows:
        return None
    hist = commits(repo, "HEAD", n)
    res, total = replay(rows, hist, tracer=(None if is_aib_repo(repo) else set()))
    readers, _listers, _every, reusable = build_index(rows)
    cut = max(20, WIDE_SHARE * len(rows))
    wide_files = {p: len(ts) for p, ts in readers.items() if len(ts) >= cut}
    kinds = {k: [] for k in ("ordinary", "wide", "deps", "aib")}
    ord_tests, ord_cpu = [], []          # 一般推送:每次沿用幾支、那幾支自己跑要幾秒(加總)
    by_file = defaultdict(int)
    for (_sha, changes), r in zip(hist, res):
        touched = {p for _s, paths in changes for p in paths}
        if r["why"] == "tracer":
            kinds["aib"].append(r["saved_pct"])
        elif r["why"] == "deps":
            kinds["deps"].append(r["saved_pct"])
        elif touched & set(wide_files):
            kinds["wide"].append(r["saved_pct"])
            for p in touched & set(wide_files):
                by_file[p] += 1
        else:
            kinds["ordinary"].append(r["saved_pct"])
            ord_tests.append(len(reusable) - r["rerun_reusable"])
            ord_cpu.append(r["saved_s"])
    mean = lambda xs: round(statistics.mean(xs), 1) if xs else None  # noqa: E731
    wall = gate_wall_s(repo, total, stats_path=stats_path)
    rate, source, counted, span = push_rate(repo, stats_path=stats_path, now=now)
    now_pct = mean([x for v in kinds.values() for x in v]) or 0.0
    best = mean(kinds["ordinary"])
    # 照建議做之後:牽動很多測試的檔的改動攢起來(或宣告成字典)⇒ 那些提交改成一般;鎖檔、追蹤器的照舊
    after_n = len(kinds["ordinary"]) + len(kinds["wide"])
    after_pct = round((after_n * (best or 0.0)) / max(1, len(res)), 1)
    files = sorted(((p, c, wide_files[p], p.endswith(KEYABLE_EXT)) for p, c in by_file.items()),
                   key=lambda x: (-x[1], -x[2], x[0]))
    return {"tests": len(rows), "coverage": round(100.0 * len(reusable) / len(rows), 1), "commits": len(res),
            "kinds": {k: {"n": len(v), "pct": mean(v)} for k, v in kinds.items()},
            "best_pct": best, "now_pct": now_pct, "after_pct": after_pct, "after_n": after_n,
            "wall_s": round(wall, 1), "per_week": rate, "rate_source": source, "rate_counted": counted, "rate_span": span,
            "week_now_s": round(rate * wall * now_pct / 100.0), "week_after_s": round(rate * wall * after_pct / 100.0),
            "files": files[:5], "tests_all": len(rows), "best_tests": mean(ord_tests), "best_cpu_s": mean(ord_cpu)}


def predict(repo, n=50, pool_dir=None):
    """10-06 the maintainer:告訴用戶「在什麼動作之後會開始大幅省時間」。只讀紀錄、不跑測試。
    回 {coverage, tests, ordinary_pct, ordinary_n, wide_pct, wide_n} 或 None(沒有紀錄)。
      coverage:有可沿用紀錄的測試佔幾成(要先建好紀錄才省得到);
      ordinary:最近 n 個提交裡「沒碰到牽動很多測試的檔、也沒碰追蹤器」的那些,回放平均省幾 %;
      wide:碰到的那些平均省幾 %。"""
    pool_dir = pool_dir or str(st.shadow_dir_default())
    rows = latest_rows(sorted(glob.glob(os.path.join(pool_dir, "*.rows.jsonl"))), repo=repo)
    if not rows:
        return None
    hist = commits(repo, "HEAD", n)
    aib_self = is_aib_repo(repo)
    res, _total = replay(rows, hist, tracer=(None if aib_self else set()))
    readers, _listers, _every, reusable = build_index(rows)
    cut = max(20, WIDE_SHARE * len(rows))
    wide_files = {p for p, ts in readers.items() if len(ts) >= cut}
    ordinary, wide = [], []
    for (sha, changes), r in zip(hist, res):
        touched = {p for _st, paths in changes for p in paths}
        (wide if (touched & wide_files or r["why"] == "tracer") else ordinary).append(r["saved_pct"])
    mean = lambda xs: round(statistics.mean(xs), 1) if xs else None  # noqa: E731
    return {"coverage": round(100.0 * len(reusable) / len(rows), 1), "tests": len(rows),
            "ordinary_pct": mean(ordinary), "ordinary_n": len(ordinary), "wide_pct": mean(wide), "wide_n": len(wide)}


def build_index(rows):
    """-> (readers: path -> tests, listers: dir -> tests, every_commit: tests, reusable: tests)."""
    readers, listers, every = defaultdict(set), defaultdict(set), set()
    reusable = set()
    for t, r in rows.items():
        if r.get("rc") != 0 or r.get("external") or r.get("measured") not in (True, "measured"):
            continue
        reusable.add(t)
        readers[t].add(t)
        for item in r.get("inputs") or []:
            kind, _, rest = str(item).partition(":")
            if kind in GIT_STATE_KINDS:
                every.add(t)
                continue
            path = rest.rsplit(":", 1)[0] if kind in ("open", "handoff", "probe", "listdir") else rest
            if kind == "i18nkeys":
                kind, path = "open", rest.partition(":")[0]   # 回放只能看檔層級:改到這份字典就算要重跑(保守)
            if not path.startswith("./"):
                continue
            path = path[2:]
            if kind == "listdir":
                listers[path.rstrip("/").rstrip(".").rstrip("/")].add(t)
            elif kind in ("open", "handoff", "probe", "missing"):
                readers[path].add(t)
    return readers, listers, every, reusable


def commits(repo, rev, n):
    out = subprocess.run(["git", "-C", str(repo), "log", "--first-parent", "-%d" % n, "--name-status", "--format=@@%h", rev],
                         capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL).stdout
    res = []
    for blk in out.split("@@")[1:]:
        lines = blk.strip().splitlines()
        if not lines:
            continue
        changes = [ln.split("\t") for ln in lines[1:] if "\t" in ln]
        res.append((lines[0], [(c[0], c[1:]) for c in changes]))
    return res


def replay(rows, history, tracer=None):
    tracer = tracer_paths() if tracer is None else tracer
    readers, listers, every, reusable = build_index(rows)
    total = sum(float(r.get("duration_s") or 0) for r in rows.values()) or 1.0
    out = []
    for sha, changes in history:
        if any(p in tracer for _st, paths in changes for p in paths):
            affected = set(reusable)
            why = "tracer"
        elif any(os.path.basename(p) in LOCKFILES for _st, paths in changes for p in paths):
            affected = set(reusable)     # 10-06:鎖檔進每一支的環境指紋 ⇒ 改了全部重跑(以前當一般檔回放,高估)
            why = "deps"
        else:
            affected = set(every)
            for status, paths in changes:
                for p in paths:
                    affected |= readers.get(p, set())
                    if status[:1] in ("A", "D", "R", "C"):
                        affected |= listers.get(os.path.dirname(p), set())
            why = ""
        saved = sum(float(rows[t].get("duration_s") or 0) for t in reusable - affected)
        out.append({"commit": sha, "saved_s": round(saved, 1), "saved_pct": round(100.0 * saved / total, 1),
                    "rerun_reusable": len(affected & reusable), "why": why})
    return out, total


def summary(results):
    pct = sorted(r["saved_pct"] for r in results)
    if not pct:
        return {}
    return {"commits": len(pct), "mean_pct": round(statistics.mean(pct), 1), "median_pct": round(statistics.median(pct), 1),
            "best_pct": pct[-1], "worst_pct": pct[0], "tracer_commits": sum(1 for r in results if r["why"] == "tracer")}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Replay real commits against the reuse pool and report the share of a full run "
                                             "each commit would have saved.")
    ap.add_argument("--pool", required=True, help="pool directory (reads *.rows.jsonl)")
    ap.add_argument("--repo", default=".", help="git repository to read history from")
    ap.add_argument("--rev", default="HEAD", help="newest commit to replay")
    ap.add_argument("-n", type=int, default=300, help="number of first-parent commits")
    ap.add_argument("--json", action="store_true", help="print per-commit results as JSON")
    a = ap.parse_args(argv)
    rows = latest_rows(sorted(glob.glob(os.path.join(a.pool, "*.rows.jsonl"))), repo=a.repo)
    if not rows:
        print("no rows in pool: %s" % a.pool, file=sys.stderr)
        return 2
    res, total = replay(rows, commits(a.repo, a.rev, a.n))
    s = summary(res)
    if a.json:
        print(json.dumps({"summary": s, "total_s": round(total, 1), "commits": res}, ensure_ascii=False))
    else:
        print("tests %d, full run %.0f s, commits %d" % (len(rows), total, s.get("commits", 0)))
        print("saved per commit: mean %.1f%%, median %.1f%%, best %.1f%%, worst %.1f%%; tracer commits (0%%): %d"
              % (s["mean_pct"], s["median_pct"], s["best_pct"], s["worst_pct"], s["tracer_commits"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
