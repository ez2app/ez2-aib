#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""proc_group (DECISION-20 PR-A): a timeout stops the whole group; EPERM is checked, not trusted; identity first.

Real-process tests start only `sh` / `sleep` children of their own and collect exactly the pids they printed.
"""
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
import proc_group as pg  # noqa: E402

POSIX = hasattr(os, "killpg")


def alive(pid):
    o = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(o) and not o.startswith("Z")


def gone_within(pid, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return False


class Own:
    """Pids a test printed itself; collected in tearDown whatever happened (never a wider kill)."""

    def __init__(self):
        self.pids = []

    def take(self, out):
        for ln in out.splitlines():
            if ln.startswith("gc "):
                self.pids.append(int(ln.split()[1]))
        return self.pids[-1] if self.pids else None

    def collect(self):
        for pid in self.pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


@unittest.skipUnless(POSIX, "process groups are POSIX")
class RealGroups(unittest.TestCase):
    def setUp(self):
        self.own = Own()

    def tearDown(self):
        self.own.collect()

    def test_a_timeout_stops_the_grandchild_too(self):
        r = pg.run_group(["/bin/sh", "-c", "/bin/sleep 60 & echo gc $!; sleep 60"], timeout=1, grace=1)
        gc = self.own.take(r.out)
        self.assertTrue(r.timed_out, r)
        self.assertIsNone(r.rc)
        self.assertEqual(r.stop.status, "stopped", r)
        self.assertTrue(gone_within(gc), "grandchild %s outlived the timeout" % gc)

    def test_a_child_that_ignores_term_gets_kill_after_the_grace(self):
        # #494 L8: TERM alone is not enough
        script = "trap '' TERM; /bin/sh -c \"trap '' TERM; sleep 60\" & echo gc $!; sleep 60"
        r = pg.run_group(["/bin/sh", "-c", script], timeout=1, grace=0.5)
        gc = self.own.take(r.out)
        self.assertEqual([s for s, _ in r.stop.signals], ["SIGTERM", "SIGKILL"], r)
        self.assertEqual(r.stop.status, "stopped", r)
        self.assertTrue(gone_within(gc), "grandchild %s ignored TERM and survived KILL" % gc)

    def test_leftovers_of_a_finished_command_are_stopped_unless_kept(self):
        r = pg.run_group(["/bin/sh", "-c", "/bin/sleep 60 & echo gc $!; echo done"], timeout=30, grace=1)
        gc = self.own.take(r.out)
        self.assertEqual(r.rc, 0)
        self.assertFalse(r.timed_out)
        self.assertIn("done", r.out)
        self.assertIsNotNone(r.stop, "the leftover sleep was not stopped")
        self.assertTrue(gone_within(gc))

        r = pg.run_group(["/bin/sh", "-c", "/bin/sleep 60 & echo gc $!"], timeout=30, leftovers="keep")
        gc = self.own.take(r.out)
        self.assertIsNone(r.stop)
        self.assertTrue(alive(gc), "leftovers=keep stopped it anyway")

    def test_a_grandchild_holding_stdout_is_not_a_false_timeout(self):
        # 043dd's scan: with a pipe, a finished command looks like a timeout while a grandchild keeps stdout open
        t = time.monotonic()
        r = pg.run_group(["/bin/sh", "-c", "/bin/sleep 20 & echo gc $!; exit 7"], timeout=10, leftovers="keep")
        self.own.take(r.out)
        self.assertLess(time.monotonic() - t, 5, r)
        self.assertEqual(r.rc, 7)
        self.assertFalse(r.timed_out)

    def test_a_group_whose_leader_was_reaped_elsewhere_gets_no_signal(self):
        # bb7dc #509: someone else waited on the leader -> its pid may be anyone's -> nothing is sent
        import tempfile
        with tempfile.TemporaryFile() as fh:
            p = subprocess.Popen(["/bin/sh", "-c", "/bin/sleep 60 & echo gc $!"], stdout=fh,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
            start = pg.memguard.start_of(p.pid)
            p.wait()
            fh.seek(0)
            gc = self.own.take(fh.read().decode())
        rep = pg._Group(p, start=start).stop(grace=0.2)
        self.assertEqual((rep.status, rep.why), ("unverified", "leader_reaped_elsewhere"), rep)
        self.assertEqual(rep.signals, [])
        self.assertTrue(alive(gc), "signalled a group whose leader we could no longer prove")

    def test_run_group_reaps_only_after_every_signal_and_hands_out_no_popen(self):
        # a63ab #509 (1): reaping is run_group's alone; there is no public stop for a caller-held Popen
        self.assertFalse(hasattr(pg, "stop_group"), "a public stop for a caller-held Popen is back")
        events = []
        real_killpg = os.killpg

        class Rec(subprocess.Popen):
            def wait(self, *a, **k):
                events.append("wait")
                return super().wait(*a, **k)

            def poll(self):
                events.append("poll")
                return super().poll()

        def kp(pgid, sig):
            events.append("sig%d" % sig)
            return real_killpg(pgid, sig)

        os.killpg = kp
        try:
            r = pg.run_group(["/bin/sh", "-c", "/bin/sleep 60 & echo gc $!; echo done"], timeout=30, grace=0.5,
                             popen=Rec)
        finally:
            os.killpg = real_killpg
        self.own.take(r.out)
        self.assertEqual(r.stop.status, "stopped", r)
        sent = [i for i, e in enumerate(events) if e.startswith("sig") and e != "sig0"]
        reaped = [i for i, e in enumerate(events) if e in ("wait", "poll")]
        self.assertTrue(sent, events)
        self.assertTrue(all(i > max(sent) for i in reaped), "reaped before the last signal: %s" % events)

    def test_a_command_that_cannot_start_says_so(self):
        r = pg.run_group(["/no/such/command"], timeout=1)
        self.assertIsNone(r.rc)
        self.assertFalse(r.timed_out)
        self.assertIn("could not start", r.error)


class LeaderStateWithoutOsWaitid(unittest.TestCase):
    """#509 CI (macOS 3.12): os.waitid exists on macOS only from Python 3.13. Without it the leader state comes from
    libc waitid through ctypes; where neither exists it is 'unknown' (= unproven), never a guess."""

    @unittest.skipUnless(sys.platform == "darwin", "the libc fallback is macOS only")
    def test_the_libc_fallback_tells_running_zombie_and_reaped_apart(self):
        p = subprocess.Popen(["/bin/sleep", "0.3"], start_new_session=True)
        try:
            self.assertEqual(pg._leader_state(p.pid, has_os_waitid=False), "running")
            end = time.monotonic() + 5
            st = "running"
            while st == "running" and time.monotonic() < end:
                time.sleep(0.05)
                st = pg._leader_state(p.pid, has_os_waitid=False)
            self.assertEqual(st, "zombie")
            self.assertEqual(pg._leader_state(p.pid, has_os_waitid=False), "zombie")  # looking twice is fine
            # WNOWAIT really left it unreaped: the one real wait still gets it
            got, status = os.waitpid(p.pid, 0)
            self.assertEqual(got, p.pid)
            self.assertTrue(os.WIFEXITED(status), status)
            p.returncode = os.WEXITSTATUS(status)
        finally:
            if p.returncode is None:
                p.wait()
        self.assertEqual(pg._leader_state(p.pid, has_os_waitid=False), "reaped")

    def _with_fake_libc(self, si_pid, rc=0, err=0):
        import ctypes

        def fake(idtype, pid, si, options):
            self.assertEqual((idtype, options), (1, 0x4 | 0x1 | 0x20))
            si[12:16] = int(si_pid).to_bytes(4, sys.byteorder, signed=True)
            ctypes.set_errno(err)
            return rc

        saved = pg._libc_waitid
        pg._libc_waitid = (ctypes, fake)
        try:
            return pg._darwin_waitid_state(4242)
        finally:
            pg._libc_waitid = saved

    def test_the_libc_result_counts_only_when_si_pid_is_the_target(self):
        import errno
        self.assertEqual(self._with_fake_libc(0), "running")
        self.assertEqual(self._with_fake_libc(4242), "zombie")
        self.assertEqual(self._with_fake_libc(4243), "unknown")  # wrong layout / garbage: unproven
        self.assertEqual(self._with_fake_libc(-1), "unknown")
        self.assertEqual(self._with_fake_libc(0, rc=-1, err=errno.ECHILD), "reaped")
        self.assertEqual(self._with_fake_libc(0, rc=-1, err=errno.EINVAL), "unknown")

    def test_without_any_waitid_the_state_is_unknown(self):
        self.assertEqual(pg._leader_state(os.getpid(), has_os_waitid=False, platform="linux"), "unknown")

    @unittest.skipUnless(sys.platform == "darwin", "the libc fallback is macOS only")
    def test_a_stop_without_os_waitid_is_verified_on_macos(self):
        real = pg._leader_state
        pg._leader_state = lambda pid: real(pid, has_os_waitid=False)
        try:
            r = pg.run_group(["/bin/sh", "-c", "trap '' TERM; sleep 30"], timeout=0.5, grace=0.3)
        finally:
            pg._leader_state = real
        self.assertTrue(r.timed_out, r)
        self.assertEqual(r.stop.status, "stopped", r)


DRIVER = r"""
import json, os, signal, sys, threading, time
sys.path.insert(0, sys.argv[1])
import proc_group as pg
mode, pidfile, tmp = sys.argv[2], sys.argv[3], sys.argv[4]
CFG = {
    # name: trap = the whole group ignores TERM (KILL needed); stuck = `ps` hangs, so the stop can only end at its
    # deadline; pokes = (signal, seconds after start); term = SIGTERM handler raising SystemExit(143)
    "int": dict(pokes=[("INT", 0.5)]),
    "int-trap": dict(trap=True, pokes=[("INT", 0.5)]),
    "stuck": dict(stuck=True, pokes=[("INT", 0.5)]),                                  # cell 3: one press only
    "stuck-int": dict(stuck=True, pokes=[("INT", 0.5), ("INT", 0.8)]),                 # cell 4
    "se-stuck": dict(term=True, stuck=True, pokes=[("TERM", 0.5)]),                    # cell 5
    "term": dict(term=True, pokes=[("TERM", 0.5)]),
    "term-int": dict(term=True, trap=True, pokes=[("TERM", 0.5), ("INT", 1.0)]),       # cell 6
    "ex-int": dict(ex_at=0.5, trap=True, pokes=[("INT", 1.0)]),                         # cell 7
    "int-term": dict(trap=True, pokes=[("INT", 0.5), ("TERM", 1.0)]),                  # cell 8
    "timeout-int": dict(trap=True, timeout=0.5, pokes=[("INT", 1.0)]),                 # cell 9
    "leftover-int": dict(leftover=True, pokes=[("INT", 1.0)]),                         # cell 10
    "timeout-stuck": dict(trap=True, stuck=True, timeout=0.3, pokes=[("INT", 0.5)]),   # cell 9: W3 stop stuck
    "leftover-stuck": dict(leftover=True, stuck=True, pokes=[("INT", 1.0)]),           # cell 10: W4 stuck
    "burst": dict(pokes=[("INT", 0.5), ("INT", 0.5), ("INT", 0.5)]),                   # cell 12
    "twice": dict(trap=True, pokes=[("INT", 0.5), ("INT", 1.0), ("INT", 1.2)]),        # cell 13
    "count": dict(term=True, trap=True, count_int=True, pokes=[("TERM", 0.5), ("INT", 1.0)]),  # cell 15
    "custom": dict(custom=True, pokes=[("INT", 0.5)]),                                 # cell 17
    "ign-stuck": dict(term=True, count_int=True, stuck=True, pokes=[("TERM", 0.5), ("INT", 0.8)]),  # cell 19
    "inherited-ign": dict(ign_int=True, timeout=1.0, pokes=[("INT", 0.3)]),           # SIGINT ignored by the caller
}[mode]
# Precondition: the caller has Python's default Ctrl-C. A shell's background job (`cmd &`, nohup) starts
# with SIGINT ignored and Python then keeps SIG_IGN (andymini 11-08: 13 cells red for that reason alone).
signal.signal(signal.SIGINT, signal.SIG_IGN if CFG.get("ign_int") else signal.default_int_handler)
grace = 0.5 if CFG.get("stuck") else 1.5
if CFG.get("stuck"):
    pg.KILL_WAIT_S = 0.2          # limit = 0.5 + 2 x 0.2 = 0.9 s
    with open(os.path.join(tmp, "ps"), "w") as f:
        f.write("#!/bin/sh\nexec sleep 30\n")
    os.chmod(os.path.join(tmp, "ps"), 0o755)
    os.environ["PATH"] = tmp + ":" + os.environ["PATH"]
calls = []
if CFG.get("term"):
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(143))
if CFG.get("count_int"):
    signal.signal(signal.SIGINT, lambda s, f: calls.append(s))      # a handler that returns
if CFG.get("custom"):
    def my_int(s, f):
        raise KeyboardInterrupt("mine")
    def my_term(s, f):
        sys.exit(9)
    signal.signal(signal.SIGINT, my_int)
    signal.signal(signal.SIGTERM, my_term)
before = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))
if "ex_at" in CFG:
    real = pg._exited_or_poll
    t_ex = time.monotonic() + CFG["ex_at"]
    def boom(p):
        if time.monotonic() >= t_ex:
            raise RuntimeError("boom")
        return real(p)
    pg._exited_or_poll = boom
first = {}
def poke(sig, at):
    def go():
        time.sleep(at)
        first.setdefault("t", time.monotonic())
        os.kill(os.getpid(), signal.SIGINT if sig == "INT" else signal.SIGTERM)
    threading.Thread(target=go, daemon=True).start()
trap = "trap '' TERM; " if CFG.get("trap") else ""
if CFG.get("leftover"):   # the leader exits at once, its grandchild ignores TERM: the leftovers stop takes the grace
    cmd = ["/bin/sh", "-c", "(trap '' TERM; exec sleep 60) & echo $! > " + pidfile + "; sleep 0.7"]
else:
    cmd = ["/bin/sh", "-c", trap + "sleep 60 & echo $! > " + pidfile + "; wait"]
for sig, at in CFG["pokes"]:
    poke(sig, at)
t0 = time.monotonic()
got = {"exc": None}
try:
    pg.run_group(cmd, timeout=CFG.get("timeout", 30), grace=grace)
except BaseException as e:
    t1 = time.monotonic()
    rep = getattr(e, "proc_group_stop", None)
    got = {"exc": e.__class__.__name__, "code": getattr(e, "code", None), "msg": str(e),
           "status": rep.status if rep else None, "why": rep.why if rep else None,
           "held": getattr(e, "proc_group_held", None),
           "context": e.__context__.__class__.__name__ if e.__context__ is not None else None,
           "context_code": getattr(e.__context__, "code", None),
           "elapsed": t1 - t0, "since_first": t1 - first.get("t", t0), "limit": pg.defer_limit_s(grace)}
got["restored"] = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == before
time.sleep(0.5 if CFG.get("count_int") else 0)   # a late second delivery would show up here (cell 15)
got["int_calls"] = len(calls)
print(json.dumps(got))
"""


class _Interrupt(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.td = tempfile.mkdtemp(prefix="pg-int-")
        self.pidfile = os.path.join(self.td, "gc")
        self.gc = None

    def tearDown(self):
        if self.gc:
            try:
                os.kill(self.gc, signal.SIGKILL)
            except OSError:
                pass
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def drive(self, mode, rc=0):
        import json
        p = subprocess.run([sys.executable, "-c", DRIVER, str(ROOT / "src" / "cockpit"), mode, self.pidfile, self.td],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        try:
            self.gc = int(Path(self.pidfile).read_text().strip())
        except (OSError, ValueError):
            self.gc = None
        self.assertEqual(p.returncode, rc, p.stdout + p.stderr)
        if rc != 0:
            return None, p.stderr
        return json.loads(p.stdout.strip().splitlines()[-1]), p.stderr

    def within_limit(self, got):
        """Common check A (a63ab #520): from the first interrupt, the exception reaches the caller within the
        limit + 1 s, with no second press."""
        self.assertLess(got["since_first"], got["limit"] + 1.0, "past the limit: %s" % got)


@unittest.skipUnless(POSIX, "process groups are POSIX")
class Interrupted(_Interrupt):
    """MATRIX-520 (a63ab / bb7dc 11-08): one test per cell; cell numbers in the names' docstrings."""

    def test_ctrl_c_stops_the_grandchild_before_the_interrupt_leaves(self):
        """cell 1"""
        got, err = self.drive("int")
        self.assertEqual(got["exc"], "KeyboardInterrupt", got)
        self.assertEqual(got["status"], "stopped", got)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived the interrupt" % self.gc)
        self.assertIn("[proc_group] interrupted by KeyboardInterrupt: stop result for group", err)
        self.assertTrue(got["restored"], got)
        self.within_limit(got)

    def test_one_ctrl_c_with_a_group_ignoring_term_gets_out_within_the_limit(self):
        """cell 2"""
        got, _ = self.drive("int-trap")
        self.assertEqual((got["exc"], got["status"]), ("KeyboardInterrupt", "stopped"), got)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived" % self.gc)
        self.within_limit(got)

    def test_one_ctrl_c_and_a_stuck_stop_gets_out_at_the_limit(self):
        """cell 3 (bb7dc G1): one press only, `ps` hangs: the exception leaves at the limit, unverified."""
        got, err = self.drive("stuck")
        self.assertEqual(got["exc"], "KeyboardInterrupt", got)
        self.assertAlmostEqual(got["limit"], 0.9, places=6)
        self.within_limit(got)
        self.assertEqual((got["status"], got["why"]), ("unverified", "stop_cut_short_at_limit"), got)
        self.assertIn("unverified stop_cut_short_at_limit", err)   # the log never reads as a clean stop
        self.assertTrue(got["restored"], got)

    def test_a_second_ctrl_c_during_a_stuck_stop_changes_nothing(self):
        """cell 4: still out at the limit, one KeyboardInterrupt (the held one is the same interrupt)."""
        got, _ = self.drive("stuck-int")
        self.assertEqual(got["exc"], "KeyboardInterrupt", got)
        self.within_limit(got)
        self.assertEqual(got["held"], ["SIGINT"], got)

    def test_a_sigterm_exit_with_a_stuck_stop_gets_out_at_the_limit(self):
        """cell 5"""
        got, _ = self.drive("se-stuck")
        self.assertEqual((got["exc"], got["code"]), ("SystemExit", 143), got)
        self.within_limit(got)
        self.assertEqual(got["status"], "unverified", got)

    def test_a_sigterm_handler_that_exits_still_gets_the_group_stopped(self):
        got, _ = self.drive("term")
        self.assertEqual((got["exc"], got["code"]), ("SystemExit", 143), got)
        self.assertEqual(got["status"], "stopped", got)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived SIGTERM" % self.gc)

    def test_ctrl_c_after_a_sigterm_exit_started_the_stop_still_interrupts(self):
        """cell 6 (bb7dc #520): held until the group is stopped, then it interrupts; never swallowed."""
        got, err = self.drive("term-int")
        self.assertEqual(got["exc"], "KeyboardInterrupt", got)
        self.assertEqual((got["context"], got["context_code"]), ("SystemExit", 143), got)
        self.assertEqual(got["status"], "stopped", got)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived" % self.gc)
        self.assertEqual(got["held"], ["SIGINT"], got)
        self.assertIn("held during the stop: SIGINT", err)
        self.assertTrue(got["restored"], got)

    def test_ctrl_c_after_another_exception_started_the_stop_still_interrupts(self):
        """cell 7"""
        got, _ = self.drive("ex-int")
        self.assertEqual((got["exc"], got["context"]), ("KeyboardInterrupt", "RuntimeError"), got)
        self.assertEqual(got["status"], "stopped", got)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived" % self.gc)

    def test_a_plain_sigterm_during_the_stop_ends_the_process_after_the_group_is_stopped(self):
        """cell 8: SIGTERM with no handler (SIG_DFL) is held, the group is stopped, then the process ends by it."""
        got, err = self.drive("int-term", rc=-signal.SIGTERM)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived" % self.gc)
        self.assertIn("stop result for group", err)
        self.assertIn("held during the stop: SIGTERM", err)

    def test_ctrl_c_during_the_timeout_stop_still_stops_the_group(self):
        """cell 9"""
        got, _ = self.drive("timeout-int")
        self.assertEqual((got["exc"], got["status"]), ("KeyboardInterrupt", "stopped"), got)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived" % self.gc)
        self.within_limit(got)

    def test_ctrl_c_during_the_leftovers_stop_still_stops_the_group(self):
        """cell 10"""
        got, _ = self.drive("leftover-int")
        self.assertEqual(got["exc"], "KeyboardInterrupt", got)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived" % self.gc)

    def test_one_ctrl_c_during_a_stuck_timeout_stop_gets_out_within_the_limit(self):
        """cell 9 (bb7dc / a63ab #520 W3): the timeout stop is stuck (`ps` hangs); one real Ctrl-C during it.
        Out with KeyboardInterrupt within the limit of that press, no second press, never a quiet timeout."""
        got, _ = self.drive("timeout-stuck")
        self.assertEqual((got["exc"], got["status"]), ("KeyboardInterrupt", "unverified"), got)
        self.assertIn("stop_cut_short_at_limit", got["why"])
        self.assertTrue(gone_within(self.gc), "grandchild %s survived (KILL never sent?)" % self.gc)
        # no new limit after the stuck stop: out by that stop's own deadline, not a second one after it
        self.assertLess(got["since_first"], got["limit"] + 0.3, got)
        self.within_limit(got)
        self.assertTrue(got["restored"], got)

    def test_one_ctrl_c_during_a_stuck_leftovers_stop_gets_out_within_the_limit(self):
        """cell 10 (W4): the leader exits, the leftovers listing / stop is stuck; one real Ctrl-C during it."""
        got, _ = self.drive("leftover-stuck")
        self.assertEqual((got["exc"], got["status"]), ("KeyboardInterrupt", "unverified"), got)
        self.assertIn("stop_cut_short_at_limit", got["why"])
        self.assertLess(got["since_first"], got["limit"] + 0.3, got)
        self.within_limit(got)
        self.assertTrue(got["restored"], got)

    def test_ctrl_c_presses_at_the_same_instant_cannot_skip_the_stop(self):
        """cell 12 (a63ab G2): handlers only record, so no press can land between `except` and the stop."""
        got, _ = self.drive("burst")
        self.assertEqual((got["exc"], got["status"]), ("KeyboardInterrupt", "stopped"), got)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived" % self.gc)

    def test_more_ctrl_c_during_the_stop_do_not_cut_it_short(self):
        """cell 13: TERM ignored, only the KILL after the grace stops it; the extra presses are held."""
        got, _ = self.drive("twice")
        self.assertEqual((got["exc"], got["status"]), ("KeyboardInterrupt", "stopped"), got)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived the extra Ctrl-C" % self.gc)
        self.assertEqual(got["held"], ["SIGINT", "SIGINT"], got)
        self.assertTrue(got["restored"], got)

    def test_a_held_signal_reaches_the_callers_handler_exactly_once(self):
        """cell 15 (a63ab G3): a counting SIGINT handler, Ctrl-C held during a SystemExit's stop: called once,
        and nothing arrives late after the exception left."""
        got, _ = self.drive("count")
        self.assertEqual((got["exc"], got["code"]), ("SystemExit", 143), got)
        self.assertEqual(got["int_calls"], 1, got)
        self.assertEqual(got["held"], ["SIGINT"], got)

    def test_the_callers_own_handlers_are_back_afterwards(self):
        """cell 17: custom SIGINT / SIGTERM handlers; the custom SIGINT one raising is the interrupt."""
        got, _ = self.drive("custom")
        self.assertEqual((got["exc"], got["msg"], got["status"]), ("KeyboardInterrupt", "mine", "stopped"), got)
        self.assertTrue(got["restored"], got)

    def test_a_handler_that_returns_and_a_stuck_stop_still_get_out_at_the_limit(self):
        """cell 19: the held Ctrl-C goes to a handler that returns; the limit holds anyway."""
        got, _ = self.drive("ign-stuck")
        self.assertEqual((got["exc"], got["code"]), ("SystemExit", 143), got)
        self.within_limit(got)
        self.assertEqual(got["int_calls"], 1, got)

    def test_a_caller_that_ignores_sigint_is_not_interrupted(self):
        """SIGINT inherited as ignored (a background job): run_group honours it, the run ends by its own
        timeout, and the caller's SIG_IGN is back afterwards."""
        got, _ = self.drive("inherited-ign")
        self.assertIsNone(got["exc"], got)
        self.assertTrue(got["restored"], got)

    def test_the_default_limit_is_grace_plus_two_kill_waits(self):
        self.assertEqual((pg.DEFAULT_GRACE_S, pg.KILL_WAIT_S), (5.0, 2.0))
        self.assertEqual(pg.defer_limit_s(pg.DEFAULT_GRACE_S), 9.0)

    def test_the_recording_handler_never_raises(self):
        """G2: the handler installed while run_group runs only records, whatever the caller had."""
        ints = pg._Interrupts()
        ints._record(signal.SIGINT, None)
        ints._record(signal.SIGTERM, None)
        self.assertEqual(ints.pending, [signal.SIGINT, signal.SIGTERM])


@unittest.skipUnless(POSIX, "process groups are POSIX")
class InterruptedInProcess(_Interrupt):
    """Cells that need an injected exception or a fake Popen."""

    def boom_at(self, n, boom):
        calls = []
        real = pg._exited_or_poll

        def poll(p):
            calls.append(p.pid)
            if len(calls) == n:
                raise boom
            return real(p)

        pg._exited_or_poll = poll
        self.addCleanup(setattr, pg, "_exited_or_poll", real)

    def stuck_ps(self):
        with open(os.path.join(self.td, "ps"), "w") as f:
            f.write("#!/bin/sh\nexec sleep 30\n")
        os.chmod(os.path.join(self.td, "ps"), 0o755)
        old = os.environ["PATH"]
        os.environ["PATH"] = self.td + ":" + old
        self.addCleanup(os.environ.__setitem__, "PATH", old)
        kw = pg.KILL_WAIT_S
        pg.KILL_WAIT_S = 0.2
        self.addCleanup(setattr, pg, "KILL_WAIT_S", kw)

    def cmd(self, trap=False):
        return ["/bin/sh", "-c", ("trap '' TERM; " if trap else "") + "sleep 60 & echo $! > %s; wait" % self.pidfile]

    def test_any_base_exception_is_raised_again_as_the_same_object(self):
        class Boom(BaseException):
            pass

        boom = Boom()
        self.boom_at(3, boom)
        with self.assertRaises(Boom) as cm:
            pg.run_group(self.cmd(), timeout=30, grace=0.5)
        self.assertIs(cm.exception, boom)
        self.assertEqual(boom.proc_group_stop.status, "stopped", boom.proc_group_stop)
        self.gc = int(Path(self.pidfile).read_text().strip())
        self.assertTrue(gone_within(self.gc))

    def test_an_interrupt_during_the_final_reap_still_stops_the_group(self):
        """cell 11 (bb7dc #520)"""
        class Boom(BaseException):
            pass

        boom = Boom()

        class P(subprocess.Popen):
            def poll(self):   # run_group's only poll() on POSIX is the final reap
                if not getattr(self, "_boomed", False):
                    self._boomed = True
                    raise boom
                return super().poll()

        with self.assertRaises(Boom) as cm:
            pg.run_group(["/bin/sh", "-c", "sleep 60 & echo $! > %s" % self.pidfile], timeout=30, grace=0.5,
                         leftovers="keep", popen=P)
        self.assertIs(cm.exception, boom)
        self.gc = int(Path(self.pidfile).read_text().strip())
        self.assertEqual(boom.proc_group_stop.status, "stopped", boom.proc_group_stop)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived an interrupt in the reap" % self.gc)

    def test_a_reap_that_runs_out_is_reported_and_bounded(self):
        """cells 14 + 20: the leader's reap never finishes in time: out within the limit, and the report never
        claims it was reaped."""
        class Boom(BaseException):
            pass

        boom = Boom()
        self.boom_at(3, boom)

        class P(subprocess.Popen):
            def wait(self, timeout=None):
                if timeout is not None and not getattr(self, "_real", False):
                    time.sleep(timeout)
                    raise subprocess.TimeoutExpired(self.args, timeout)
                return super().wait(timeout=timeout)

        holder = {}

        def popen(*a, **k):
            holder["p"] = P(*a, **k)
            return holder["p"]

        t0 = time.monotonic()
        with self.assertRaises(Boom):
            pg.run_group(self.cmd(), timeout=30, grace=0.5, popen=popen)
        self.assertLess(time.monotonic() - t0, pg.defer_limit_s(0.5) + 1.5)
        rep = boom.proc_group_stop
        self.assertEqual(rep.status, "unverified", rep)
        self.assertIn("leader_not_reaped", rep.why)
        holder["p"]._real = True
        holder["p"].wait(timeout=5)
        self.gc = int(Path(self.pidfile).read_text().strip())

    def test_off_the_main_thread_nothing_is_held_and_the_group_is_still_stopped(self):
        """cell 16: only the main thread can swap handlers; the caller's stay untouched."""
        import threading

        class Boom(BaseException):
            pass

        boom = Boom()
        self.boom_at(3, boom)
        before = signal.getsignal(signal.SIGINT)
        out = {}

        def run():
            try:
                pg.run_group(self.cmd(), timeout=30, grace=0.5)
            except BaseException as e:  # noqa: BLE001
                out["e"] = e
                out["during"] = signal.getsignal(signal.SIGINT)

        t = threading.Thread(target=run)
        t.start()
        t.join(30)
        self.assertIs(out.get("e"), boom)
        self.assertIs(out["during"], before)
        self.assertEqual(boom.proc_group_stop.status, "stopped", boom.proc_group_stop)
        self.gc = int(Path(self.pidfile).read_text().strip())
        self.assertTrue(gone_within(self.gc))

    def test_a_start_that_breaks_after_the_fork_never_claims_nothing_started(self):
        """cell 18 (bb7dc): Popen raises after the child exists; its pid is unknown to run_group, so the report
        is unverified / start_interrupted_child_unknown, never "nothing was started"."""
        class Boom(BaseException):
            pass

        boom = Boom()
        started = {}

        def popen(*a, **k):
            started["p"] = subprocess.Popen(*a, **k)
            raise boom

        with self.assertRaises(Boom):
            pg.run_group(self.cmd(), timeout=30, grace=0.5, popen=popen)
        self.assertEqual((boom.proc_group_stop.status, boom.proc_group_stop.why),
                         ("unverified", "start_interrupted_child_unknown"))
        # clean up what really started (our own pid, from our own Popen)
        os.killpg(started["p"].pid, signal.SIGKILL)
        started["p"].wait(timeout=5)
        try:
            self.gc = int(Path(self.pidfile).read_text().strip())
        except (OSError, ValueError):
            self.gc = None

    def test_a_non_signal_exception_with_a_stuck_stop_gets_out_at_the_limit(self):
        """cell 21: no signal at all, `ps` hangs: still out at the limit, same object, unverified."""
        class Boom(BaseException):
            pass

        boom = Boom()
        self.boom_at(3, boom)
        self.stuck_ps()
        t0 = time.monotonic()
        with self.assertRaises(Boom) as cm:
            pg.run_group(self.cmd(), timeout=30, grace=0.5)
        self.assertIs(cm.exception, boom)
        self.assertLess(time.monotonic() - t0, pg.defer_limit_s(0.5) + 1.5)
        self.assertEqual((boom.proc_group_stop.status, boom.proc_group_stop.why),
                         ("unverified", "stop_cut_short_at_limit"))
        self.gc = int(Path(self.pidfile).read_text().strip())

    def test_the_final_reap_is_polled_and_a_ctrl_c_there_acts_at_once(self):
        """a63ab #520 condition 2: the reap after a normal exit is polled with check(), never a bare wait().
        The leader's reap is made to hang (poll keeps answering "running"); a Ctrl-C recorded meanwhile must
        raise KeyboardInterrupt within a few polls, and the group is still stopped."""
        class P(subprocess.Popen):
            def poll(self):
                if not getattr(self, "_real", False):
                    if not getattr(self, "_sent", False):
                        self._sent = True
                        os.kill(os.getpid(), signal.SIGINT)
                    return None
                return super().poll()

        holder = {}

        def popen(*a, **k):
            holder["p"] = P(*a, **k)
            return holder["p"]

        old = signal.signal(signal.SIGINT, signal.default_int_handler)
        self.addCleanup(signal.signal, signal.SIGINT, old)
        t0 = time.monotonic()
        with self.assertRaises(KeyboardInterrupt) as cm:
            pg.run_group(["/bin/sh", "-c", "sleep 60 & echo $! > %s" % self.pidfile], timeout=30, grace=0.5,
                         leftovers="keep", popen=popen)
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertEqual(cm.exception.proc_group_stop.status, "stopped", cm.exception.proc_group_stop)
        holder["p"]._real = True
        holder["p"].wait(timeout=5)
        self.gc = int(Path(self.pidfile).read_text().strip())
        self.assertTrue(gone_within(self.gc))

    def test_a_normal_exit_whose_reap_never_finishes_is_bounded_and_said(self):
        """condition 2: bounded even without an interrupt; never reported as an exit code."""
        class P(subprocess.Popen):
            def poll(self):
                return None if not getattr(self, "_real", False) else super().poll()

        holder = {}

        def popen(*a, **k):
            holder["p"] = P(*a, **k)
            return holder["p"]

        kw = pg.KILL_WAIT_S
        pg.KILL_WAIT_S = 0.3
        self.addCleanup(setattr, pg, "KILL_WAIT_S", kw)
        out = {}

        def run():   # in a daemon thread, so an unbounded reap fails the test instead of hanging the suite
            out["r"] = pg.run_group(["/bin/sh", "-c", "exit 0"], timeout=30, grace=0.5, leftovers="keep",
                                    popen=popen)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(5)
        self.assertFalse(t.is_alive(), "the reap after a normal exit is not bounded")
        r = out["r"]
        self.assertIsNone(r.rc)
        self.assertIn("not reaped", r.error)
        holder["p"]._real = True
        holder["p"].wait(timeout=5)
        self.gc = None

    def test_an_install_that_breaks_half_way_still_restores_what_it_changed(self):
        """bb7dc #520: install swaps SIGINT, then SIGTERM; an exception between the two must not leave the
        recorder on SIGINT."""
        class Boom(BaseException):
            pass

        real = signal.signal
        n = {"c": 0}

        def flaky(sig, h):
            if getattr(h, "__func__", None) is pg._Interrupts._record:
                n["c"] += 1
                if n["c"] == 2:
                    raise Boom()
            return real(sig, h)

        before = signal.getsignal(signal.SIGINT)
        pg.signal.signal = flaky
        try:
            with self.assertRaises(Boom):
                pg.run_group(["/bin/sh", "-c", "exit 0"], timeout=10, grace=0.5)
        finally:
            pg.signal.signal = real
        self.assertEqual(n["c"], 2)
        self.assertIs(signal.getsignal(signal.SIGINT), before)
        self.assertIsNone(pg._ACTIVE)

    def test_nested_run_group_outermost_installs_inner_reuses(self):
        """a63ab #520 condition 5: the caller's SIGTERM handler runs another run_group (a cleanup command) while
        the outer one is active. The inner one must not install its own recorder (it would pass a Ctrl-C to the
        outer recorder and keep waiting): one Ctrl-C stops the inner group at once, then the outer one, raises
        KeyboardInterrupt once, and the caller's handlers are back afterwards."""
        inner_pid = os.path.join(self.td, "inner.pid")
        seen = {}

        def on_term(sig, frame):
            seen["during"] = signal.getsignal(signal.SIGINT)
            threading.Timer(0.4, os.kill, (os.getpid(), signal.SIGINT)).start()
            pg.run_group(["/bin/sh", "-c", "sleep 60 & echo $! > %s; wait" % inner_pid], timeout=20, grace=0.5)

        old_int = signal.signal(signal.SIGINT, signal.default_int_handler)
        old_term = signal.signal(signal.SIGTERM, on_term)
        self.addCleanup(signal.signal, signal.SIGINT, old_int)
        self.addCleanup(signal.signal, signal.SIGTERM, old_term)
        threading.Timer(0.4, os.kill, (os.getpid(), signal.SIGTERM)).start()
        t0 = time.monotonic()
        with self.assertRaises(KeyboardInterrupt) as cm:
            pg.run_group(self.cmd(), timeout=30, grace=0.5)
        elapsed = time.monotonic() - t0
        self.gc = int(Path(self.pidfile).read_text().strip())
        igc = int(Path(inner_pid).read_text().strip())
        self.assertLess(elapsed, 4.0, "the inner run kept waiting after the Ctrl-C")
        self.assertEqual(cm.exception.proc_group_stop.status, "stopped", cm.exception.proc_group_stop)
        self.assertTrue(gone_within(igc), "inner grandchild %s survived" % igc)
        self.assertTrue(gone_within(self.gc), "outer grandchild %s survived" % self.gc)
        self.assertIsNot(seen["during"], signal.default_int_handler)   # the outer recorder was active
        self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)
        self.assertIs(signal.getsignal(signal.SIGTERM), on_term)
        self.assertIsNone(pg._ACTIVE)

    def test_inherit_with_a_timeout_still_stops_the_grandchild(self):
        """bb7dc #520: output="inherit" takes the same stop path; a timeout stops the group, out is ""."""
        r = pg.run_group(self.cmd(), timeout=1.0, grace=0.5, output="inherit")
        self.gc = int(Path(self.pidfile).read_text().strip())
        self.assertTrue(r.timed_out, r)
        self.assertEqual(r.out, "")
        self.assertEqual(r.stop.status, "stopped", r.stop)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived an inherit timeout" % self.gc)

    def test_an_interrupted_inherit_run_still_stops_the_group(self):
        """bb7dc #520"""
        class Boom(BaseException):
            pass

        boom = Boom()
        self.boom_at(3, boom)
        with self.assertRaises(Boom) as cm:
            pg.run_group(self.cmd(), timeout=30, grace=0.5, output="inherit")
        self.assertIs(cm.exception, boom)
        self.gc = int(Path(self.pidfile).read_text().strip())
        self.assertEqual(boom.proc_group_stop.status, "stopped", boom.proc_group_stop)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived an interrupted inherit run" % self.gc)


class Output(unittest.TestCase):
    @unittest.skipUnless(POSIX, "uses /bin/sh")
    def test_inherit_writes_live_to_the_callers_streams_and_returns_no_out(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import proc_group as pg; "
                "r = pg.run_group(['/bin/sh', '-c', 'echo live-out; echo live-err >&2'], timeout=10, "
                "output='inherit'); print('rc=%s out=%r' % (r.rc, r.out))")
        p = subprocess.run([sys.executable, "-c", code, str(ROOT / "src" / "cockpit")], capture_output=True,
                           text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.splitlines(), ["live-out", "rc=0 out=''"])
        self.assertEqual(p.stderr.strip(), "live-err")

    @unittest.skipUnless(POSIX, "uses /bin/sh")
    def test_capture_stays_the_default(self):
        r = pg.run_group(["/bin/sh", "-c", "echo hi; echo there >&2"], timeout=10)
        self.assertEqual((r.rc, sorted(r.out.split())), (0, ["hi", "there"]))

    def test_output_must_be_capture_or_inherit(self):
        with self.assertRaises(ValueError):
            pg.run_group(["true"], timeout=1, output="pipe")


class FakeP:
    pid = 424242

    def __init__(self):
        self.killed = False

    def kill(self):
        self.killed = True


class Fakes(unittest.TestCase):
    """EPERM, identity and Windows paths without touching real processes (pid 424242 is never signalled)."""

    def group(self, killpg, rows, start_now=100, start=100, state="running"):
        calls = []

        def kp(pgid, sig):
            calls.append(sig)
            return killpg(pgid, sig)

        g = pg._Group(FakeP(), start=start, killpg=kp, members=lambda pgid: rows,
                      start_of=lambda pid: start_now, leader_state=lambda pid: state, sleep=lambda s: None)
        return g, calls

    @staticmethod
    def ok(pgid, sig):
        return None

    @staticmethod
    def eperm(pgid, sig):
        raise PermissionError(1, "Operation not permitted")

    def test_eperm_with_only_zombies_left_is_stopped(self):
        g, calls = self.group(self.eperm, [(424242, "Z"), (424243, "Z+")])
        rep = g.stop(grace=0.01)
        self.assertEqual(rep.status, "stopped", rep)
        self.assertEqual(calls[:1], [signal.SIGTERM])

    def test_eperm_with_a_live_member_is_unverified_never_done(self):
        g, calls = self.group(self.eperm, [(424242, "Z"), (424250, "S")])
        rep = g.stop(grace=0.01)
        self.assertEqual(rep.status, "unverified", rep)
        self.assertEqual(rep.survivors, [424250])
        self.assertIn(signal.SIGKILL, calls, "EPERM with a live member must still try KILL")

    def test_eperm_with_a_failed_listing_is_unverified(self):
        g, _ = self.group(self.eperm, None)
        rep = g.stop(grace=0.01)
        self.assertEqual((rep.status, rep.why), ("unverified", "group_listing_failed"))

    def test_a_leader_with_another_start_time_is_never_signalled(self):
        g, calls = self.group(lambda pgid, sig: None, [(424242, "S")], start_now=999)
        rep = g.stop(grace=0.01)
        self.assertEqual((rep.status, rep.why), ("unverified", "leader_identity_changed"))
        self.assertEqual(calls, [], "signalled a group whose leader is not the process we started")

    def test_no_recorded_start_time_means_nothing_is_sent(self):
        # bb7dc #509: start_of() unreadable when the group was created -> identity never recorded, never proven
        g, calls = self.group(self.ok, [(424242, "S")], start=None, start_now=None)
        self.assertIsNone(g.start)
        rep = g.stop(grace=0.01)
        self.assertEqual((rep.status, rep.why), ("unverified", "leader_start_unknown"))
        self.assertEqual(calls, [])

    def test_an_unreadable_start_time_of_a_running_leader_means_nothing_is_sent(self):
        g, calls = self.group(self.ok, [(424242, "S")], start_now=None)
        rep = g.stop(grace=0.01)
        self.assertEqual((rep.status, rep.why), ("unverified", "leader_identity_changed"))
        self.assertEqual(calls, [])

    def test_a_leader_reaped_elsewhere_is_never_signalled(self):
        # its pid (= the group number) may already be someone else's
        g, calls = self.group(self.ok, [(424242, "S")], start_now=100, state="reaped")
        rep = g.stop(grace=0.01)
        self.assertEqual((rep.status, rep.why), ("unverified", "leader_reaped_elsewhere"))
        self.assertEqual(calls, [])

    def test_an_unreaped_zombie_leader_is_still_ours(self):
        # normal exit, leftovers in the group: the zombie keeps the pid ours even though its start is unreadable
        g, calls = self.group(lambda pgid, sig: None, [(424242, "Z")], start_now=None, state="zombie")
        rep = g.stop(grace=0.01)
        self.assertEqual(rep.status, "stopped", rep)
        self.assertEqual(calls[:1], [signal.SIGTERM])

    def test_identity_lost_while_waiting_after_term_is_unverified_even_if_the_listing_is_empty(self):
        # bb7dc #509 (2): TERM sent, then the leader is reaped elsewhere during the grace; the empty listing
        # that follows must not turn into "stopped"
        states = iter(["running", "reaped"])
        g, calls = self.group(self.ok, [], start_now=100)
        g.leader_state = lambda pid: next(states, "reaped")
        rep = g.stop(grace=0.01)
        self.assertEqual((rep.status, rep.why), ("unverified", "leader_reaped_elsewhere"), rep)
        self.assertEqual(calls, [signal.SIGTERM])

    def test_a_leader_dying_between_the_two_identity_reads_is_still_ours(self):
        # Mac Pro 13 flake 2/6: after TERM the leader died between "running?" and "start time?"; the start read
        # came back None and was taken as "identity changed". Start first, state second.
        order = []
        g, calls = self.group(lambda pgid, sig: None, [(424242, "Z")], start_now=None)
        g.start_of = lambda pid: order.append("start") or None
        g.leader_state = lambda pid: order.append("state") or "zombie"
        rep = g.stop(grace=0.01)
        self.assertEqual(rep.status, "stopped", rep)
        self.assertEqual(order[:2], ["start", "state"])

    def test_an_exiting_leader_briefly_unreadable_is_retried_not_misjudged(self):
        # local flake 2/10: right after TERM the leader is unreadable for a moment, then a zombie
        seq = iter([None, None, None])
        states = iter(["running", "running", "zombie"])
        g, calls = self.group(lambda pgid, sig: None, [(424242, "Z")])
        g.start_of = lambda pid: next(seq, None)
        g.leader_state = lambda pid: next(states, "zombie")
        rep = g.stop(grace=0.01)
        self.assertEqual(rep.status, "stopped", rep)

    def test_identity_lost_once_stays_lost_and_nothing_more_is_sent(self):
        # bb7dc #509 (3): lost on the probe after TERM, readable again before KILL -> still lost, no KILL
        states = iter(["running", "reaped"])
        g, calls = self.group(self.ok, [(424250, "S")], start_now=100)
        g.leader_state = lambda pid: next(states, "running")
        rep = g.stop(grace=0.01)
        self.assertEqual((rep.status, rep.why), ("unverified", "leader_reaped_elsewhere"), rep)
        self.assertEqual(calls, [signal.SIGTERM], "signalled again after identity was lost")
        # the latch itself: identity reads fine now, still nothing goes out
        self.assertIsNone(g.identity_problem())
        self.assertEqual(g.send(signal.SIGKILL), "not_ours")
        self.assertEqual(calls, [signal.SIGTERM])

    def test_esrch_means_stopped(self):
        def esrch(pgid, sig):
            raise ProcessLookupError(3, "No such process")
        g, _ = self.group(esrch, [])
        self.assertEqual(g.stop(grace=0.01).status, "stopped")

    def test_without_process_groups_only_the_child_is_killed_and_it_says_so(self):
        p = FakeP()
        real = pg.POSIX
        pg.POSIX = False
        try:
            rep = pg._Group(p, start=100, start_of=lambda pid: 100).stop()
        finally:
            pg.POSIX = real
        self.assertTrue(p.killed)
        self.assertEqual((rep.status, rep.why), ("unverified", "windows_direct_child_only"))

    def test_without_process_groups_a_normal_exit_says_leftovers_were_not_checked(self):
        real = pg.POSIX
        pg.POSIX = False
        try:
            r = pg.run_group([sys.executable, "-c", "print('hi')"], timeout=30)
            kept = pg.run_group([sys.executable, "-c", "print('hi')"], timeout=30, leftovers="keep")
        finally:
            pg.POSIX = real
        self.assertEqual(r.rc, 0)
        self.assertEqual((r.stop.status, r.stop.why), ("unverified", "windows_leftovers_not_checked"))
        self.assertIsNone(kept.stop)

    def test_leftovers_must_be_stop_or_keep(self):
        with self.assertRaises(ValueError):
            pg.run_group(["true"], timeout=1, leftovers="maybe")


if __name__ == "__main__":
    unittest.main()
