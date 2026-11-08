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
                    "Names" = the whole failed name, or its last `::` / `.` segment: test_add does not match
                    test_add_many (1ced7 #525 H2).
    SURVIVED        CMD passed, or failed without naming EXPECT_FAIL ("everything went red" does not count).
    NOT-FALLEN      The knifed code did not build, so nothing was tested.
    ANCHOR-MISSING  OLD is not in FILE exactly once; nothing was changed. Red, not skipped: a knife whose anchor
                    drifted away proves nothing (a63ab outline r2, P2-7).
    UNDECIDED       CMD timed out, or left processes behind in its process group.

Rules:
- Spec mode runs every distinct CMD (top-level and per-knife) once without any knife first ("noop"). All must
  pass, or no knife runs: a knife on a red baseline cannot bite anything of its own (1ced7 #525 H3). Positive
  and knife results are printed separately. The single-knife CLI runs the same noop first (#525 L1).
- name, file, old and expect_fail must be non-empty strings; only new may be "" (#525 H1).
- The file is restored by writing the original text back, so its mtime is new and cargo / Python rebuild it.
  Never restore with cp/mv: an old mtime makes cargo reuse the knifed build and every later run fails for the
  wrong reason. After restoring, the file must read back byte-identical, or the run stops.
- DECISION-7 13b: CMD runs in its own process group (POSIX). When CMD returns, any process still in that group is
  a leftover: the knife is UNDECIDED, the group is killed, and no further knife runs on a dirty machine. The group
  leader is a small sentinel that runs CMD and is reaped only after the last signal, so the pgid cannot have been
  reused by anyone else when it is signalled (bb7dc #525). A process list that cannot be read is UNDECIDED too.
  Not covered: processes that left the group (setsid / daemonize: a tmux server or a daemon the test starts is
  not seen; #525 L2) and Windows, which has no process groups here;
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
# libtest's closing list, printed with and without -q (`cargo test -q` prints no "test x ... FAILED" lines):
#   failures:
#       job::tests::the_owner_crashing_kills_the_tree
LIBTEST_FAILURES = re.compile(r"^failures:\r?\n((?:    \S+\r?\n?)+)", re.M)
POSIX = os.name == "posix"


def failed_names(out):
    got = set()
    for rx in FAILED_NAMES:
        got.update(m.group(1) for m in rx.finditer(out))
    for m in LIBTEST_FAILURES.finditer(out):
        got.update(line.strip() for line in m.group(1).splitlines() if line.strip())
    return sorted(got)


# POSIX: the process-group leader is this sentinel, not CMD. It starts CMD in its own group, reports CMD's rc on
# stdout, then stays until we close its stdin. So the leader is never reaped before we are done signalling the
# group: while it is alive or a zombie its pid (= the pgid) cannot be reused, and a signal to that pgid can only
# reach processes CMD left behind (bb7dc #525 High: reaping CMD first, then killpg(its pid), could hit a group
# that reused the pid). The leader is reaped last, on every path.
SENTINEL = r"""
import json, subprocess, sys
log, cmd, cwd = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3]
with open(log, "ab") as f:
    try:
        rc = subprocess.call(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    except OSError as e:
        f.write(("knife: cannot start %s: %s\n" % (cmd[0], e)).encode())
        rc = 127
sys.stdout.write("%d\n" % rc)
sys.stdout.flush()
sys.stdin.read()
"""


def group_members(pgid, exclude):
    """pids in process group `pgid` other than `exclude` (POSIX, from ps); None if ps cannot be read."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,pgid="], capture_output=True, text=True, timeout=10,
                             stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    got = []
    for line in out.splitlines():
        f = line.split()
        if len(f) == 2 and f[1] == str(pgid) and f[0] != str(exclude):
            got.append(int(f[0]))
    return got


def _read_rc(p, timeout):
    """CMD's rc from the sentinel's stdout, or None on timeout / no answer."""
    import selectors
    sel = selectors.DefaultSelector()
    sel.register(p.stdout, selectors.EVENT_READ)
    try:
        if not sel.select(timeout):
            return None
        line = p.stdout.readline()
    finally:
        sel.close()
    try:
        return int(line.strip())
    except ValueError:
        return None


def run(cmd, cwd, timeout):
    """-> (rc or None on timeout, output, leftover: True / False / None when it cannot be checked).
    Output goes to a temp file, not a pipe: a leftover child inherits a pipe and holds it open, so reading the pipe
    would wait for the leftover instead of for CMD (Windows CI run 42436895264 hung 39 min that way)."""
    fd, log = tempfile.mkstemp(prefix="knife-", suffix=".log")
    os.close(fd)
    try:
        if POSIX:
            rc, leftover = _run_posix(cmd, cwd, timeout, log)
        else:
            rc, leftover = _run_plain(cmd, cwd, timeout, log), None
        with open(log, encoding="utf-8", errors="replace") as f:
            return rc, f.read(), leftover
    finally:
        os.unlink(log)


def _run_plain(cmd, cwd, timeout, log):
    with open(log, "ab") as f:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    try:
        return p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()                 # Windows: our own handle
        p.wait()
        return None


def _run_posix(cmd, cwd, timeout, log):
    p = subprocess.Popen([sys.executable, "-I", "-c", SENTINEL, log, json.dumps(list(cmd)), cwd or "."],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    pgid = p.pid                 # start_new_session: the sentinel leads its own group
    leftover = None
    try:
        rc = _read_rc(p, timeout)
        if rc is None:
            _kill_group(pgid)    # timeout: the leader is not reaped yet, so pgid is still ours
            return None, None
        leftover = True
        for _ in range(20):      # give exiting members up to 2 s
            members = group_members(pgid, exclude=pgid)
            if members is None:
                leftover = None  # cannot tell: say so, never guess clean
                break
            if not members:
                leftover = False
                break
            time.sleep(0.1)
        if leftover:
            _kill_group(pgid)    # leader still unreaped: only CMD's leftovers (and the sentinel) are hit
        return rc, leftover
    finally:
        # every path: signal first (above), reap the leader last
        try:
            p.stdin.close()
        except OSError:
            pass
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kill_group(pgid)    # still unreaped here, so still ours
            p.wait()
        p.stdout.close()


def _kill_group(pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def name_matches(name, expect_fail):
    # whole name, or the last `::` (cargo path) / `.` segment: never a substring (test_add vs test_add_many)
    return bool(expect_fail) and (name == expect_fail or name.endswith("::" + expect_fail)
                                  or name.endswith("." + expect_fail))


def judge(rc, out, expect_fail):
    # the expected test named as failed means it ran: that is a bite even if the output also quotes a build error
    # (a test about build errors prints "could not compile" itself; found by knifing this tool, 043dd 11-08)
    names = failed_names(out)
    if rc != 0 and any(name_matches(n, expect_fail) for n in names):
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
    if leftover is None and POSIX:
        return "UNDECIDED", "could not read the process list for the leftover check; stopping", True
    outcome, detail = judge(rc, out, k["expect_fail"])
    if outcome != "BIT":
        detail += " | " + " / ".join(out.strip().splitlines()[-3:])[:300]
    return outcome, detail, False


def knife_fields_bad(k):
    bad = [f for f in ("name", "file", "old", "expect_fail") if not isinstance(k.get(f), str) or not k.get(f)]
    if not isinstance(k.get("new"), str):
        bad.append("new")
    return bad


def noop_green(cmds, repo, timeout):
    """Run each distinct CMD once with no knife; False (and say so) if any is red or leaves processes behind."""
    for c in cmds:
        rc, out, leftover = run(c, repo, timeout)
        ok = rc == 0 and (leftover is False or (leftover is None and not POSIX))
        print("NOOP %s rc=%s leftover=%s cmd=%s" % ("GREEN" if ok else "RED", rc, leftover, " ".join(c)[:120]))
        if not ok:
            print("  " + " / ".join(out.strip().splitlines()[-3:])[:300])
            print("RESULT noop red: no knife run")
            return False
    return True


def run_spec(spec, repo):
    cmd, timeout, knives = spec.get("cmd"), spec.get("timeout", 900), spec.get("knives") or []
    if not knives or not all(k.get("cmd") or cmd for k in knives):
        print("knife: spec needs knives, and a cmd for each (top-level or per knife)")
        return 2
    for k in knives:
        bad = knife_fields_bad(k)
        if bad:
            print("knife: knife %r has bad %s (must be non-empty strings; only new may be \"\")" % (k.get("name"), bad))
            return 2
    print("leftover check: %s" % ("process group" if POSIX else "unavailable (no process groups on this OS)"))
    if spec.get("noop", True):
        cmds = []
        for k in knives:
            c = k.get("cmd") or cmd
            if c not in cmds:
                cmds.append(c)
        if not noop_green(cmds, repo, timeout):
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
    # spec mode has no `-- CMD`; the single-knife CLI always does, so an OLD / NEW that reads "--spec" stays
    # single-knife (#525 L3)
    if "--" not in argv and any(x == "--spec" or x.startswith("--spec=") for x in argv):
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
    ap = argparse.ArgumentParser(allow_abbrev=False, add_help=False, usage="knife.py FILE OLD NEW --expect-fail TEST_NAME [--timeout S] -- CMD...")
    ap.add_argument("--expect-fail", required=True)
    ap.add_argument("--timeout", type=float, default=900)
    a, pos = ap.parse_known_args(argv[:cut])
    # FILE OLD NEW are taken as-is, so an OLD / NEW starting with "-" (even "--spec") works (#525 L3)
    if len(pos) != 3:
        print("knife: want FILE OLD NEW before --, got %d value(s)" % len(pos))
        return 2
    a.file, a.old, a.new = pos
    cmd = argv[cut + 1:]
    if not cmd:
        print("knife: missing command after --")
        return 2
    k = {"name": a.expect_fail, "file": a.file, "old": a.old, "new": a.new, "expect_fail": a.expect_fail}
    bad = knife_fields_bad(k)
    if bad:
        print("knife: bad %s (must be non-empty; only NEW may be \"\")" % bad)
        return 2
    if not noop_green([cmd], ".", a.timeout):
        return 1
    outcome, detail, _ = one_knife(k, ".", cmd, a.timeout)
    print("KNIFE %s: %s %s" % (a.expect_fail, outcome, detail))
    return 0 if outcome == "BIT" else 1


if __name__ == "__main__":
    sys.exit(main())
