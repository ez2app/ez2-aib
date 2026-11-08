#!/usr/bin/env python3
"""Knife test: break one line on purpose, run a command, restore the file.

    python3 -I scripts/knife.py FILE OLD NEW --expect-fail TEST_NAME [--timeout S] -- CMD...
    python3 -I scripts/knife.py --spec knives.json [--repo DIR]

Spec file (JSON), paths relative to --repo (default: the current directory):

    {"cmd": ["cargo", "test", "-q", "-p", "aib-sys", "--locked"],
     "timeout": 900,
     "knives": [{"name": "no-kill", "file": "crates/x/src/a.rs", "old": "...", "new": "...",
                 "expect_fail": "the_owner_crashing_kills_the_tree", "cmd": ["optional", "override"]}]}

Every knife ends in exactly one outcome (DECISION-18 item 3, shared by every PR from now on):

    BIT             CMD failed and the output names EXPECT_FAIL as a failed test. The only good outcome.
    SURVIVED        CMD passed, or failed without naming EXPECT_FAIL ("everything went red" does not count).
    NOT-FALLEN      The knifed code did not build, so nothing was tested.
    ANCHOR-MISSING  OLD is not in FILE exactly once; nothing was changed. Red, not skipped: a knife whose anchor
                    drifted away proves nothing (a63ab outline r2, P2-7).
    UNDECIDED       CMD timed out, or left processes behind in its process group.

Rules:
- Spec mode runs CMD once without any knife first ("noop"). It must pass, or no knife runs: a knife on a red
  baseline cannot bite anything of its own. Positive and knife results are printed separately.
- The file is restored by writing the original text back, so its mtime is new and cargo / Python rebuild it.
  Never restore with cp/mv: an old mtime makes cargo reuse the knifed build and every later run fails for the
  wrong reason. After restoring, the file must read back byte-identical, or the run stops.
- DECISION-7 13b: CMD runs in its own process group (POSIX). When CMD returns, any process still in that group is
  a leftover: the knife is UNDECIDED, the group is killed, and no further knife runs on a dirty machine.
  Not covered: processes that left the group (setsid / daemonize) and Windows, which has no process groups here;
  on Windows the run says "leftover check: unavailable".
rc: 0 every knife BIT (and noop green) / 1 anything else / 2 usage error.
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

OUTCOMES = ("BIT", "SURVIVED", "NOT-FALLEN", "ANCHOR-MISSING", "UNDECIDED")
BUILD_FAILED = re.compile(r"could not compile|^error\[E\d+\]|error: linking|LNK\d{4}|^SyntaxError:|"
                          r"^IndentationError:", re.M)
# test names that failed: cargo "test a::b ... FAILED", unittest "FAIL: name (" / "ERROR: name (", pytest "FAILED x::name"
FAILED_NAMES = (re.compile(r"^test (\S+) \.\.\. FAILED", re.M),
                re.compile(r"^(?:FAIL|ERROR): (\S+) \(", re.M),
                re.compile(r"^FAILED \S*?::(\S+)", re.M))
POSIX = os.name == "posix"


def failed_names(out):
    got = set()
    for rx in FAILED_NAMES:
        got.update(m.group(1) for m in rx.finditer(out))
    return sorted(got)


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:      # a member we cannot signal still exists
        return True


def run(cmd, cwd, timeout):
    """-> (rc or None on timeout, output, leftover: True / False / None when it cannot be checked).
    Output goes to a temp file, not a pipe: a leftover child inherits a pipe and holds it open, so reading the pipe
    would wait for the leftover instead of for CMD (Windows CI run 42436895264 hung 39 min that way)."""
    kw = {"start_new_session": True} if POSIX else {}
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as log:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            rc = None
            if POSIX:
                _kill_group(p.pid)
            else:
                p.kill()
            p.wait()
        leftover = None
        if rc is not None and POSIX:
            leftover = True
            for _ in range(20):      # give exiting members up to 2 s
                if not _group_alive(p.pid):
                    leftover = False
                    break
                time.sleep(0.1)
            if leftover:
                _kill_group(p.pid)
        log.seek(0)
        return rc, log.read(), leftover


def _kill_group(pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def judge(rc, out, expect_fail):
    # the expected test named as failed means it ran: that is a bite even if the output also quotes a build error
    # (a test about build errors prints "could not compile" itself; found by knifing this tool, 043dd 11-08)
    names = failed_names(out)
    if rc != 0 and any(expect_fail in n for n in names):
        return "BIT", "rc=%s failed=%s" % (rc, names)
    m = BUILD_FAILED.search(out)
    if m:
        return "NOT-FALLEN", "build failed: " + m.group(0)
    return "SURVIVED", "rc=%s failed=%s" % (rc, names)


def one_knife(k, repo, default_cmd, timeout):
    """-> (outcome, detail, stop). stop = the machine is not clean, or the file could not be restored."""
    path = os.path.join(repo, k["file"])
    with open(path, encoding="utf-8", newline="") as f:
        orig = f.read()
    n = orig.count(k["old"])
    if n != 1:
        return "ANCHOR-MISSING", "OLD found %d times in %s, wanted 1; nothing changed" % (n, k["file"]), False
    try:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(orig.replace(k["old"], k["new"], 1))
        rc, out, leftover = run(k.get("cmd") or default_cmd, repo, k.get("timeout", timeout))
    finally:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(orig)
    with open(path, encoding="utf-8", newline="") as f:
        if f.read() != orig:
            return "UNDECIDED", "%s did not read back identical after restore" % k["file"], True
    if rc is None:
        return "UNDECIDED", "timeout %s s" % k.get("timeout", timeout), True
    if leftover:
        return "UNDECIDED", "processes left in the process group after CMD returned (killed); stopping", True
    outcome, detail = judge(rc, out, k["expect_fail"])
    if outcome != "BIT":
        detail += " | " + " / ".join(out.strip().splitlines()[-3:])[:300]
    return outcome, detail, False


def run_spec(spec, repo):
    cmd, timeout, knives = spec.get("cmd"), spec.get("timeout", 900), spec.get("knives") or []
    if not knives or not all(k.get("cmd") or cmd for k in knives):
        print("knife: spec needs knives, and a cmd for each (top-level or per knife)")
        return 2
    for k in knives:
        missing = [f for f in ("name", "file", "old", "new", "expect_fail") if not k.get(f) and k.get(f) != ""]
        if missing or not k.get("old"):
            print("knife: knife %r is missing %s" % (k.get("name"), missing or ["old"]))
            return 2
    print("leftover check: %s" % ("process group" if POSIX else "unavailable (no process groups on this OS)"))
    if spec.get("noop", True):
        base = cmd or knives[0]["cmd"]
        rc, out, leftover = run(base, repo, timeout)
        ok = rc == 0 and not leftover
        print("NOOP %s rc=%s leftover=%s" % ("GREEN" if ok else "RED", rc, leftover))
        if not ok:
            print("  " + " / ".join(out.strip().splitlines()[-3:])[:300])
            print("RESULT noop red: no knife run")
            return 1
    counts = dict.fromkeys(OUTCOMES, 0)
    ran = 0
    for k in knives:
        outcome, detail, stop = one_knife(k, repo, cmd, timeout)
        counts[outcome] += 1
        ran += 1
        print("  %-14s %s (%s): %s" % (outcome, k["name"], k["expect_fail"], detail))
        if stop:
            break
    skipped = len(knives) - ran
    print("RESULT " + " ".join("%s=%d" % (o, counts[o]) for o in OUTCOMES) + (" NOT-RUN=%d" % skipped))
    return 0 if counts["BIT"] == len(knives) else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--spec"] or "--spec" in argv[:3]:
        ap = argparse.ArgumentParser()
        ap.add_argument("--spec", required=True)
        ap.add_argument("--repo", default=".")
        a = ap.parse_args(argv)
        try:
            with open(a.spec, encoding="utf-8") as f:
                spec = json.load(f)
        except (OSError, ValueError) as e:
            print("knife: cannot read spec %s: %s" % (a.spec, e))
            return 2
        return run_spec(spec, a.repo)
    if "--" not in argv:
        print("knife: missing `-- CMD...`")
        return 2
    cut = argv.index("--")
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--expect-fail", required=True)
    ap.add_argument("--timeout", type=float, default=900)
    a = ap.parse_args(argv[:cut])
    cmd = argv[cut + 1:]
    if not cmd:
        print("knife: missing command after --")
        return 2
    k = {"name": a.expect_fail, "file": a.file, "old": a.old, "new": a.new, "expect_fail": a.expect_fail}
    outcome, detail, _ = one_knife(k, ".", cmd, a.timeout)
    print("KNIFE %s: %s %s" % (a.expect_fail, outcome, detail))
    return 0 if outcome == "BIT" else 1


if __name__ == "__main__":
    sys.exit(main())
