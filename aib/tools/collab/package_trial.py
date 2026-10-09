#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Trial-merge contrib packages in throw-away worktrees and report which ones add no new red (the maintainer 09-28 release).

For each package: detached worktree at BASE, `git merge` the package there, run
  - tests the package changed,
  - tests that mention a changed source file's basename (at most --related per package),
  - the repo-wide ratchets (stdin, i18n),
and compare with the same tests on BASE without the package. A package is "ok" when every test that is green
on BASE is still green with it (tests red on BASE too are reported but do not count against the package).
Nothing is merged into any branch; worktrees are removed afterwards.

Usage: package_trial.py --base HEAD --out FILE.json contrib/a/b [contrib/c/d ...]
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 09-29(docs/DESIGN-aib-test-entry-shadow-0929.md 第四節):跑測試一律走 aib test 的入口函式,
#   每支都先過影子續跑自檢、能沿用就沿用、實跑就留紀錄 ⇒ 試合併跑過的測試,發版時可以沿用。
#   入口用本檔所在這棵樹的 tools/verify(追蹤器與判沿用),受測的是各自的丟棄式工作樹。
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "verify"))
import test_entry  # noqa: E402

RATCHETS = ("tests/test-tests-subprocess-stdin-ratchet.py", "tests/test-tools-i18n-ratchet.py")
CARD_DIR = "docs/collab/packages/"


def sh(args, cwd=None, timeout=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)


def run_test(wt, test, timeout):
    """green / red / timeout / absent, as before; `timeout` seconds per test (runner rc 124 ⇒ "timeout")."""
    if not (Path(wt) / test).is_file():
        return "absent"
    res = test_entry.run_tests(wt, [test], timeout=timeout)
    rc = res["tests"][0]["rc"]
    if rc == 124:
        return "timeout"
    return "green" if rc == 0 else "red"


def tests_for(repo, ref, base, related):
    mb = sh(["git", "merge-base", base, ref], cwd=repo).stdout.strip()
    files = [f for f in sh(["git", "diff", "--name-only", mb, ref], cwd=repo).stdout.split() if f]
    tests = {f for f in files if re.match(r"tests/test-.*\.py$", f)}
    srcs = [f for f in files if not f.startswith(("tests/", "docs/")) and not f.startswith(CARD_DIR)]
    extra = []
    for s in srcs:
        stem = Path(s).stem if s.endswith(".py") else Path(s).name
        if len(stem) < 5:
            continue
        hits = sh(["git", "grep", "-l", "-F", stem, ref, "--", "tests/test-*.py"], cwd=repo).stdout.split()
        extra += [h.split(":", 1)[1] for h in hits]
    for t in extra:
        if len(tests) >= len({f for f in files if f.startswith("tests/")}) + related:
            break
        tests.add(t)
    return files, sorted(tests | set(RATCHETS))


def trial(repo, base, branch, related, timeout, scratch, baseline_cache):
    ref = "origin/" + branch
    files, tests = tests_for(repo, ref, base, related)
    wt = tempfile.mkdtemp(prefix="trial-", dir=scratch)
    shutil.rmtree(wt)
    res = {"branch": branch, "files": len(files), "tests": {}, "merge": "ok"}
    try:
        sh(["git", "worktree", "add", "-q", "--detach", wt, base], cwd=repo)
        m = sh(["git", "-c", "user.name=trial", "-c", "user.email=trial@local", "merge", "-q", "--no-edit",
                "--no-ff", ref], cwd=wt)
        if m.returncode != 0:
            res["merge"] = "conflict"
            return res
        for t in tests:
            res["tests"][t] = {"with": run_test(wt, t, timeout), "base": baseline_cache(t)}
    finally:
        sh(["git", "worktree", "remove", "--force", wt], cwd=repo)
    bad = [t for t, v in res["tests"].items() if v["with"] != "green" and v["base"] in ("green", "absent")]
    res["new_red"] = bad
    res["ok"] = res["merge"] == "ok" and not bad
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("branches", nargs="+")
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--out", required=True)
    ap.add_argument("--related", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    repo = sh(["git", "rev-parse", "--show-toplevel"]).stdout.strip()
    base = sh(["git", "rev-parse", a.base], cwd=repo).stdout.strip()
    scratch = tempfile.mkdtemp(prefix="pkg-trial-")
    base_wt = os.path.join(scratch, "base")
    sh(["git", "worktree", "add", "-q", "--detach", base_wt, base], cwd=repo)
    cache = {}

    def baseline(t):
        if t not in cache:
            cache[t] = run_test(base_wt, t, a.timeout) if (Path(base_wt) / t).is_file() else "absent"
        return cache[t]
    results = []
    try:
        with cf.ThreadPoolExecutor(a.jobs) as ex:
            futs = [ex.submit(trial, repo, base, b, a.related, a.timeout, scratch, baseline) for b in a.branches]
            for f in cf.as_completed(futs):
                r = f.result()
                results.append(r)
                print("%s %s new_red=%s" % ("OK " if r.get("ok") else "BAD", r["branch"], r.get("new_red", r["merge"])),
                      flush=True)
    finally:
        sh(["git", "worktree", "remove", "--force", base_wt], cwd=repo)
        shutil.rmtree(scratch, ignore_errors=True)
    Path(a.out).write_text(json.dumps({"base": base, "results": results}, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
