# SPDX-License-Identifier: Apache-2.0
"""Run a command in its own process group and, on a timeout, stop the whole group (DECISION-20, 11-08).

    from proc_group import run_group      # the only public entry point
    r = run_group(["aib", "test", "--changed"], timeout=900, cwd=repo)
    r.rc          # exit code; None = timed out or could not start (r.error says which)
    r.out         # combined stdout + stderr (text)
    r.stop        # StopReport when anything was stopped: status "stopped" | "unverified", survivors, why

Rules (DECISION-20 + ADR 0007):
- The command starts in a NEW SESSION (its own process group). Only that group is ever signalled.
- Timeout: TERM to the group, wait `grace`, then KILL, then verify.
- Identity: the leader is NOT reaped until the stop is finished (os.waitid WNOWAIT only looks), so its pid — and
  with it the group number — cannot be reused by anyone else while we signal. Before each signal the leader's
  identity is proven again; when it cannot be proven NOTHING is sent and the report is `unverified`:
    start time not recorded at start           -> leader_start_unknown
    leader already reaped by someone else      -> leader_reaped_elsewhere (its pid may belong to anyone now)
    running, start time unreadable or changed  -> leader_identity_changed
  A leader that exited but is not reaped (zombie) is still ours: nobody can reuse its pid.
- Reaping belongs to run_group alone: it creates the Popen, never hands it out, and waits on it only after the
  stop is finished. There is deliberately no public "stop this Popen" call — a caller holding the Popen could
  wait on it between our zombie check and killpg (bb7dc / a63ab #509). Once identity is lost at any point of a
  stop, the result is `unverified` with that reason, whatever a later listing shows.
- macOS answers killpg with EPERM (not ESRCH) when only zombies are left in a group. EPERM is "to be checked",
  never "done" and never "failed": the group's members are listed again; only zombies left = stopped, a live
  member or a listing that fails = unverified (with the pids).
- Interrupted while waiting (Ctrl-C, a SIGTERM handler that raises, any BaseException): the group is stopped and
  the leader reaped first, then the SAME exception is raised again with the StopReport as `exc.proc_group_stop`
  and one line on stderr. SIGINT / SIGTERM arriving during that stop are held (main thread only) and delivered
  to the previous handler afterwards, so a second Ctrl-C cannot cut the stop short (1ced7 / a63ab, 11-08).
- Output goes to a temporary file, not a pipe: a grandchild that inherited stdout cannot make a finished command
  look like a timeout (the "false timeout" in 043dd's scan). `output="inherit"` sends it straight to the caller's
  stdout / stderr instead (live; still no pipe).
- After a NORMAL exit, members still running in the group are stopped too (`leftovers="stop"`, the default) and
  reported, so a finished `cargo test` / `aib test` leaves nothing holding the machine. A caller that starts a
  daemon on purpose passes `leftovers="keep"`.
- Windows has no process groups here: only the direct child is killed and the report says `unverified`
  (`windows_direct_child_only`); after a normal exit leftovers cannot be listed, so `leftovers="stop"` reports
  `unverified` / `windows_leftovers_not_checked`. Never claimed as a whole-tree stop. (UNVERIFIED: no Windows machine.)
"""
import errno
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memguard  # noqa: E402  (proc_info / start_of: pid identity, shared with aib test)

DEFAULT_GRACE_S = 5.0
KILL_WAIT_S = 2.0
POLL_S = 0.05
IDENTITY_RETRIES = 10  # x POLL_S: how long an exiting leader may stay unreadable before identity is unproven
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


def _os_waitid_state(pid):
    try:
        return "running" if os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None else "zombie"
    except ChildProcessError:
        return "reaped"


_libc_waitid = None


def _darwin_waitid_state(pid):
    """Python before 3.13 has no os.waitid on macOS (#509 CI, macOS 3.12): the same libc call through ctypes.
    <sys/wait.h>: P_PID 1, WNOHANG 0x1, WEXITED 0x4, WNOWAIT 0x20; siginfo_t is 104 bytes, si_pid at offset 12
    (si_pid 0 = no state change = still running). Anything unexpected = 'unknown' (= unproven)."""
    global _libc_waitid
    try:
        if _libc_waitid is None:
            import ctypes
            import ctypes.util
            libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
            f = libc.waitid
            f.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
            f.restype = ctypes.c_int
            _libc_waitid = (ctypes, f)
        ctypes, f = _libc_waitid
        si = ctypes.create_string_buffer(104)
        if f(1, pid, si, 0x4 | 0x1 | 0x20) != 0:
            return "reaped" if ctypes.get_errno() == errno.ECHILD else "unknown"
        got = int.from_bytes(si.raw[12:16], sys.byteorder, signed=True)
        if got == 0:
            return "running"
        return "zombie" if got == pid else "unknown"
    except Exception:  # noqa: BLE001  no libc / no symbol: unproven, never "fine"
        return "unknown"


def _leader_state(pid, *, has_os_waitid=None, platform=None):
    """Look at our child `pid` without reaping it:
    'running' | 'zombie' (exited, not reaped: the pid is still ours) | 'reaped' (someone already waited on it:
    the pid may be reused by anyone) | 'unknown' (cannot look: identity stays unproven)."""
    if hasattr(os, "waitid") if has_os_waitid is None else has_os_waitid:
        return _os_waitid_state(pid)
    if (sys.platform if platform is None else platform) == "darwin":
        return _darwin_waitid_state(pid)
    return "unknown"


class _Group:
    """The group led by `p` (started with start_new_session=True), with the leader's identity recorded."""

    def __init__(self, p, start=None, *, killpg=None, members=_ps_group, start_of=memguard.start_of,
                 leader_state=None, sleep=time.sleep, clock=time.monotonic):
        self.p, self.pgid = p, p.pid
        # looked up at call time, so run_group's exit check and the identity check always use the same reader
        self.start_of, self.leader_state = start_of, leader_state or (lambda pid: _leader_state(pid))
        self.start = start_of(p.pid) if start is None else start
        self.killpg = killpg or (os.killpg if POSIX else None)
        self.members, self.sleep, self.clock = members, sleep, clock
        self.signals = []
        self.why = None

    def identity_problem(self):
        """None when the leader is provably the process we started; otherwise why not (nothing may be sent)."""
        if self.start is None:
            return "leader_start_unknown"
        # Order matters: read the start time FIRST, then look at the state. A leader that dies between the two
        # shows up as a zombie (ours, fine); one that is still "running" afterwards was alive when its start
        # time was read, so that reading is its own.
        # A leader in the middle of exiting is briefly unreadable (macOS proc_pidinfo fails) while waitid does
        # not report it yet: retry a few polls until it is readable or a zombie; still unreadable = unproven.
        for _ in range(IDENTITY_RETRIES):
            now = self.start_of(self.pgid)
            state = self.leader_state(self.pgid)
            if state == "reaped":
                return "leader_reaped_elsewhere"
            if state == "zombie":
                return None  # exited, not reaped by anyone: its pid (= the group number) cannot be reused
            if now is not None:
                return None if now == self.start else "leader_identity_changed"
            self.sleep(POLL_S)
        return "leader_identity_changed"  # unreadable counts as unproven, never as "fine"

    def send(self, sig):
        """'sent' | 'gone' (ESRCH) | 'check' (EPERM: verify by listing) | 'not_ours' (self.why says why).
        Identity loss is latched: once lost in this stop, nothing more is ever sent, even if a later read
        looks fine again (bb7dc #509)."""
        if self.why:
            return "not_ours"
        self.why = self.identity_problem()
        if self.why:
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
        """StopReport from a fresh listing of the group. Identity lost earlier in this stop = unverified, always:
        an empty listing of a group number that may be someone else's proves nothing."""
        if self.why:
            return StopReport("unverified", self.why, signals=self.signals)
        rows = self.members(self.pgid)
        if rows is None:
            return StopReport("unverified", "group_listing_failed", signals=self.signals)
        live = [pid for pid in _live(rows) if pid != self.pgid or self.leader_state(self.pgid) != "zombie"]
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
            if rows is not None and not [pid for pid in _live(rows)
                                         if pid != self.pgid or self.leader_state(self.pgid) != "zombie"]:
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
                return StopReport("unverified", self.why, signals=self.signals)
            if r == "gone":
                return StopReport("stopped", "", signals=self.signals)
            if r == "check":
                rep = self.verify()
                if rep.status == "stopped":
                    return rep
                continue  # live members under EPERM: try the next signal, then report
            if self.wait_empty(wait):
                return self.verify()
            # identity lost while waiting: the next send() is refused by the latch -> unverified, no KILL
        return self.verify()


def run_group(argv, *, timeout, cwd=None, env=None, grace=DEFAULT_GRACE_S, leftovers="stop", stdin=None,
              output="capture", popen=subprocess.Popen):
    """Run `argv` in a new session; see the module doc. `timeout` None = no limit (still stops leftovers).

    output="capture" (default): stdout + stderr go to a temporary file and come back as `r.out`.
    output="inherit": they go straight to the caller's stdout / stderr (live, no pipe, so a grandchild holding
    them cannot fake a timeout either); `r.out` is "".
    Interrupted while waiting (KeyboardInterrupt, a SIGTERM handler raising SystemExit, any BaseException):
    the group is stopped and the leader reaped FIRST, then the same exception is raised again, carrying the
    StopReport as `exc.proc_group_stop`; one line on stderr says what was stopped (1ced7 / a63ab #509 gap)."""
    if leftovers not in ("stop", "keep"):
        raise ValueError("leftovers must be 'stop' or 'keep'")
    if output not in ("capture", "inherit"):
        raise ValueError("output must be 'capture' or 'inherit'")
    out_fh = tempfile.TemporaryFile() if output == "capture" else None
    try:
        io = {"stdout": out_fh, "stderr": subprocess.STDOUT} if out_fh is not None else {}
        try:
            p = popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL if stdin is None else stdin,
                      start_new_session=True, **io)
        except (OSError, ValueError) as e:
            return Result(None, "", error="could not start %s: %s" % (argv[0] if argv else "?", e))
        g = None
        try:
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
            elif leftovers == "stop":
                # Windows: no process group to list, so leftovers are not checked at all — say so, never imply clean
                stop = StopReport("unverified", "windows_leftovers_not_checked")
        except BaseException as e:
            _stop_then_reraise(p, g, grace, e)
        try:  # the first and only reap, after every signal has been sent
            p.wait(timeout=KILL_WAIT_S if timed_out else None)
        except subprocess.TimeoutExpired:
            pass
        out = ""
        if out_fh is not None:
            out_fh.seek(0)
            out = out_fh.read().decode("utf-8", errors="replace")
        if timed_out:
            return Result(None, out, timed_out=True, error="timed out after %ss" % timeout, stop=stop)
        return Result(p.returncode, out, stop=stop)
    finally:
        if out_fh is not None:
            out_fh.close()


# Signals deferred while an interrupted run stops its group: a second Ctrl-C (or a SIGTERM) must not cut the
# stop short and leave the group behind. Only the main thread can swap handlers; elsewhere nothing is deferred.
_DEFERRED = tuple(getattr(signal, n) for n in ("SIGINT", "SIGTERM") if hasattr(signal, n))


def _stop_then_reraise(p, g, grace, exc):
    pending = []
    saved = {}
    if threading.current_thread() is threading.main_thread():
        for sig in _DEFERRED:
            try:
                saved[sig] = signal.signal(sig, lambda s, f: pending.append(s))
            except (ValueError, OSError):
                pass
    try:
        try:
            rep = (g if g is not None else _Group(p)).stop(grace)
        except Exception as e:  # noqa: BLE001  the stop itself broke: say so, the original exception still wins
            rep = StopReport("unverified", "stop_failed_after_interrupt: %s" % e.__class__.__name__)
        try:
            p.wait(timeout=KILL_WAIT_S)
        except subprocess.TimeoutExpired:
            pass
        try:
            exc.proc_group_stop = rep
        except (AttributeError, TypeError):
            pass
        try:
            sys.stderr.write("[proc_group] interrupted by %s: stopped group %s: %s %s%s\n" % (
                exc.__class__.__name__, p.pid, rep.status, rep.why,
                ", still running: %s" % rep.survivors if rep.survivors else ""))
        except Exception:  # noqa: BLE001  a closed stderr must not hide the original exception
            pass
    finally:
        for sig, h in saved.items():
            try:
                signal.signal(sig, h)
            except (ValueError, OSError):
                pass
    # A signal that came in during the stop is delivered now, to the handler that was in place before.
    for sig in pending:
        h = saved.get(sig)
        if h is signal.SIG_DFL:
            signal.raise_signal(sig)       # e.g. a plain SIGTERM: the process ends, as it would have
        elif callable(h) and h is not signal.default_int_handler:
            h(sig, None)                   # the caller's own handler (it may raise; the original is its context)
        # SIG_IGN, or Python's default Ctrl-C handler: the exception raised below already is that interrupt
    raise exc


def _exited_or_poll(p):
    """Leader exited? On POSIX look without reaping (waitid WNOWAIT, os or libc); poll() only where neither
    exists (Windows, or a POSIX without waitid): poll() reaps, and a reaped leader is unproven afterwards."""
    if POSIX:
        st = _leader_state(p.pid)
        if st != "unknown":
            return st != "running"
    return p.poll() is not None
