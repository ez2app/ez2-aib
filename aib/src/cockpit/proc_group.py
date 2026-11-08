# SPDX-License-Identifier: Apache-2.0
"""Run a command in its own process group and, on a timeout, stop the whole group (DECISION-20, 11-08).

    from proc_group import run_group
    r = run_group(["aib", "test", "--changed"], timeout=900, cwd=repo)
    r.rc          # exit code; None = timed out or could not start (r.error says which)
    r.out         # combined stdout + stderr (text)
    r.stop        # StopReport when anything was stopped: status "stopped" | "unverified", survivors, why

Rules (DECISION-20 + ADR 0007):
- The command starts in a NEW SESSION (its own process group). Only that group is ever signalled.
- Timeout: TERM to the group, wait `grace`, then KILL, then verify.
- Identity: the leader is NOT reaped until the stop is finished (os.waitid WNOWAIT only looks), so its pid — and
  with it the group number — cannot be reused by anyone else while we signal. Before each signal the leader's
  (pid, start time) is compared with what was recorded at start; a mismatch means "not ours", nothing is sent.
- macOS answers killpg with EPERM (not ESRCH) when only zombies are left in a group. EPERM is "to be checked",
  never "done" and never "failed": the group's members are listed again; only zombies left = stopped, a live
  member or a listing that fails = unverified (with the pids).
- Output goes to a temporary file, not a pipe: a grandchild that inherited stdout cannot make a finished command
  look like a timeout (the "false timeout" in 043dd's scan).
- After a NORMAL exit, members still running in the group are stopped too (`leftovers="stop"`, the default) and
  reported, so a finished `cargo test` / `aib test` leaves nothing holding the machine. A caller that starts a
  daemon on purpose passes `leftovers="keep"`.
- Windows has no process groups here: only the direct child is killed and the report says `unverified`
  (`windows_direct_child_only`). Never claimed as a whole-tree stop.
"""
import os
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memguard  # noqa: E402  (proc_info / start_of: pid identity, shared with aib test)

DEFAULT_GRACE_S = 5.0
KILL_WAIT_S = 2.0
POLL_S = 0.05
POSIX = hasattr(os, "killpg")


class StopReport:
    """What a stop did. status: stopped (verified: no live member left) | unverified (see why / survivors)."""

    def __init__(self, status, why="", survivors=(), signals=()):
        self.status, self.why, self.survivors, self.signals = status, why, list(survivors), list(signals)

    def __repr__(self):
        return "StopReport(%s, %r, survivors=%s, signals=%s)" % (self.status, self.why, self.survivors, self.signals)


class Result:
    def __init__(self, rc, out, timed_out=False, error="", stop=None):
        self.rc, self.out, self.timed_out, self.error, self.stop = rc, out, timed_out, error, stop

    def __repr__(self):
        return "Result(rc=%r, timed_out=%r, error=%r, stop=%r)" % (self.rc, self.timed_out, self.error, self.stop)


def _ps_group(pgid):
    """[(pid, stat)] of the processes in group `pgid`, or None when the listing itself failed."""
    try:
        o = subprocess.run(["ps", "-axo", "pid=,pgid=,stat="], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if o.returncode != 0:
        return None
    rows = []
    for ln in o.stdout.splitlines():
        f = ln.split()
        if len(f) >= 3 and f[1] == str(pgid):
            try:
                rows.append((int(f[0]), f[2]))
            except ValueError:
                return None
    return rows


def _live(rows):
    return [pid for pid, stat in rows if not stat.startswith("Z")]


def _exited(pid):
    """True once the leader has exited — without reaping it (its pid and group number stay ours)."""
    if not hasattr(os, "waitid"):
        return False
    try:
        return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    except ChildProcessError:
        return True


class _Group:
    """The group led by `p` (started with start_new_session=True), with the leader's identity recorded."""

    def __init__(self, p, start=None, *, killpg=None, members=_ps_group, start_of=memguard.start_of,
                 sleep=time.sleep, clock=time.monotonic):
        self.p, self.pgid = p, p.pid
        self.start_of = start_of
        self.start = start_of(p.pid) if start is None else start
        self.killpg = killpg or (os.killpg if POSIX else None)
        self.members, self.sleep, self.clock = members, sleep, clock
        self.signals = []

    def same_leader(self):
        """The leader is still the process we started (not reaped by us, so its pid cannot be someone else's;
        the start time is checked as well). A leader that is already a zombie has no readable start: fine."""
        if self.start is None:
            return True
        now = self.start_of(self.pgid)
        return now is None or now == self.start

    def send(self, sig):
        """'sent' | 'gone' (ESRCH) | 'check' (EPERM: verify by listing) | 'not_ours'."""
        if not self.same_leader():
            return "not_ours"
        try:
            self.killpg(self.pgid, sig)
        except ProcessLookupError:
            return "gone"
        except PermissionError:
            return "check"
        if sig != 0:  # 0 is only a probe
            self.signals.append((getattr(sig, "name", str(sig)), self.pgid))
        return "sent"

    def verify(self):
        """StopReport from a fresh listing of the group."""
        rows = self.members(self.pgid)
        if rows is None:
            return StopReport("unverified", "group_listing_failed", signals=self.signals)
        live = [pid for pid in _live(rows) if pid != self.pgid or not _exited(self.pgid)]
        if live:
            return StopReport("unverified", "members_still_running", live, self.signals)
        return StopReport("stopped", "", signals=self.signals)

    def wait_empty(self, seconds):
        """Probe with signal 0 until the group is gone or `seconds` pass. True = gone or only zombies (verified)."""
        end = self.clock() + seconds
        while True:
            r = self.send(0)
            if r == "gone":
                return True
            if r in ("check", "not_ours"):
                return self.verify().status == "stopped"
            rows = self.members(self.pgid)
            if rows is not None and not [pid for pid in _live(rows) if pid != self.pgid or not _exited(self.pgid)]:
                return True
            if self.clock() >= end:
                return False
            self.sleep(POLL_S)

    def stop(self, grace=DEFAULT_GRACE_S):
        if not POSIX or self.killpg is None:
            try:
                self.p.kill()
            except OSError:
                pass
            return StopReport("unverified", "windows_direct_child_only", signals=[("kill", self.p.pid)])
        for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, KILL_WAIT_S)):
            r = self.send(sig)
            if r == "not_ours":
                return StopReport("unverified", "leader_identity_changed", signals=self.signals)
            if r == "gone":
                return StopReport("stopped", "", signals=self.signals)
            if r == "check":
                rep = self.verify()
                if rep.status == "stopped":
                    return rep
                continue  # live members under EPERM: try the next signal, then report
            if self.wait_empty(wait):
                return self.verify()
        return self.verify()


def stop_group(p, grace=DEFAULT_GRACE_S, start=None, **kw):
    """Stop the group of `p` (started with start_new_session=True). Returns a StopReport. Never signals
    anything outside that group; never reaps `p` (the caller does, afterwards)."""
    return _Group(p, start, **kw).stop(grace)


def run_group(argv, *, timeout, cwd=None, env=None, grace=DEFAULT_GRACE_S, leftovers="stop", stdin=None,
              popen=subprocess.Popen):
    """Run `argv` in a new session; see the module doc. `timeout` None = no limit (still stops leftovers)."""
    if leftovers not in ("stop", "keep"):
        raise ValueError("leftovers must be 'stop' or 'keep'")
    out_fh = tempfile.TemporaryFile()
    try:
        try:
            p = popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL if stdin is None else stdin,
                      stdout=out_fh, stderr=subprocess.STDOUT, start_new_session=True)
        except (OSError, ValueError) as e:
            return Result(None, "", error="could not start %s: %s" % (argv[0] if argv else "?", e))
        g = _Group(p)
        timed_out, stop = False, None
        deadline = None if timeout is None else time.monotonic() + timeout
        while not _exited_or_poll(p):
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(POLL_S)
        if timed_out:
            stop = g.stop(grace)
        elif leftovers == "stop" and POSIX:
            rows = g.members(g.pgid)
            if rows is None or [pid for pid in _live(rows) if pid != g.pgid]:
                stop = g.stop(grace)
        try:
            p.wait(timeout=KILL_WAIT_S if timed_out else None)
        except subprocess.TimeoutExpired:
            pass
        out_fh.seek(0)
        out = out_fh.read().decode("utf-8", errors="replace")
        if timed_out:
            return Result(None, out, timed_out=True, error="timed out after %ss" % timeout, stop=stop)
        return Result(p.returncode, out, stop=stop)
    finally:
        out_fh.close()


def _exited_or_poll(p):
    """Leader exited? On POSIX look without reaping (waitid WNOWAIT); elsewhere poll()."""
    if POSIX and hasattr(os, "waitid"):
        return _exited(p.pid)
    return p.poll() is not None
