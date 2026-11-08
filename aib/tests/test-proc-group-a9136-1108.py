#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""proc_group (DECISION-20 PR-A): a timeout stops the whole group; EPERM is checked, not trusted; identity first.

Real-process tests start only `sh` / `sleep` children of their own and collect exactly the pids they printed.
"""
import os
import signal
import subprocess
import sys
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
mode, pidfile = sys.argv[2], sys.argv[3]
if mode in ("term", "term-int"):
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(143))
if mode == "stuck":
    # a stop that never finishes; the limit is grace 0.5 + 2 x 0.2 = 0.9 s
    pg.KILL_WAIT_S = 0.2
    pg._Group.stop = lambda self, grace: time.sleep(30) or pg.StopReport("stopped")
def poke(sig, at):
    def go():
        time.sleep(at)
        os.kill(os.getpid(), sig)
    threading.Thread(target=go, daemon=True).start()
# the grandchild writes its pid, the leader waits; with "trap" the whole group ignores TERM (KILL needed)
trap = "trap '' TERM; " if mode in ("twice", "term-int", "int-trap") else ""
cmd = ["/bin/sh", "-c", trap + "sleep 60 & echo $! > " + pidfile + "; wait"]
if mode == "twice":
    poke(signal.SIGINT, 0.5)
    poke(signal.SIGINT, 1.0)   # during the 1.5 s grace of the stop the first one started
elif mode == "term-int":
    poke(signal.SIGTERM, 0.5)  # the SIGTERM handler exits ...
    poke(signal.SIGINT, 1.0)   # ... and Ctrl-C comes while that exit is stopping the group (bb7dc #520)
elif mode == "stuck":
    poke(signal.SIGINT, 0.5)
    poke(signal.SIGINT, 0.8)   # pressed once during the stuck stop, never again: must go through by itself
else:
    poke(signal.SIGTERM if mode == "term" else signal.SIGINT, 0.5)
got = {"exc": None}
t0 = time.monotonic()
try:
    pg.run_group(cmd, timeout=30, grace=0.5 if mode == "stuck" else 1.5)
except BaseException as e:
    rep = getattr(e, "proc_group_stop", None)
    got = {"exc": e.__class__.__name__, "code": getattr(e, "code", None),
           "status": rep.status if rep else None, "why": rep.why if rep else None,
           "held": getattr(e, "proc_group_held", None),
           "context": e.__context__.__class__.__name__ if e.__context__ is not None else None,
           "context_code": getattr(e.__context__, "code", None),
           "sigint_restored": signal.getsignal(signal.SIGINT) is signal.default_int_handler,
           "elapsed": time.monotonic() - t0, "limit": pg.defer_limit_s(0.5 if mode == "stuck" else 1.5)}
print(json.dumps(got))
"""


@unittest.skipUnless(POSIX, "process groups are POSIX")
class Interrupted(unittest.TestCase):
    """1ced7 / a63ab 11-08 (#509 gap): interrupted while waiting, run_group stops the group and reaps the leader
    BEFORE the exception leaves; the same exception comes out with the StopReport on it."""

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

    def drive(self, mode):
        import json
        p = subprocess.run([sys.executable, "-c", DRIVER, str(ROOT / "src" / "cockpit"), mode, self.pidfile],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        try:
            self.gc = int(Path(self.pidfile).read_text().strip())
        except (OSError, ValueError):
            self.gc = None
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads(p.stdout.strip().splitlines()[-1]), p.stderr

    def test_ctrl_c_stops_the_grandchild_before_the_interrupt_leaves(self):
        got, err = self.drive("int")
        self.assertEqual(got["exc"], "KeyboardInterrupt", got)
        self.assertEqual(got["status"], "stopped", got)
        self.assertIsNotNone(self.gc, "the grandchild never started")
        self.assertTrue(gone_within(self.gc), "grandchild %s survived the interrupt" % self.gc)
        self.assertIn("[proc_group] interrupted by KeyboardInterrupt: stop result for group", err)
        self.assertTrue(got["sigint_restored"], got)

    def test_a_sigterm_handler_that_exits_still_gets_the_group_stopped(self):
        got, _ = self.drive("term")
        self.assertEqual((got["exc"], got["code"]), ("SystemExit", 143), got)
        self.assertEqual(got["status"], "stopped", got)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived SIGTERM" % self.gc)

    def test_a_second_ctrl_c_during_the_stop_does_not_cut_it_short(self):
        got, _ = self.drive("twice")
        self.assertEqual(got["exc"], "KeyboardInterrupt", got)
        self.assertEqual(got["status"], "stopped", got)   # TERM ignored: only the KILL after the grace stops it
        self.assertTrue(gone_within(self.gc), "grandchild %s survived the second Ctrl-C" % self.gc)
        self.assertTrue(got["sigint_restored"], got)
        self.assertEqual(got["held"], ["SIGINT"], got)

    def test_ctrl_c_after_a_sigterm_exit_started_the_stop_still_interrupts(self):
        """bb7dc #520: SIGTERM's handler raised SystemExit(143) and the stop is running when Ctrl-C comes. The
        Ctrl-C is held until the group is stopped, then still interrupts (never swallowed); the SystemExit is its
        __context__ and the report is on the KeyboardInterrupt too."""
        got, err = self.drive("term-int")
        self.assertEqual(got["exc"], "KeyboardInterrupt", got)
        self.assertEqual((got["context"], got["context_code"]), ("SystemExit", 143), got)
        self.assertEqual(got["status"], "stopped", got)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived" % self.gc)
        self.assertEqual(got["held"], ["SIGINT"], got)
        self.assertIn("held during the stop: SIGINT", err)
        self.assertTrue(got["sigint_restored"], got)

    def test_one_ctrl_c_with_a_group_ignoring_term_gets_out_within_the_limit(self):
        """a63ab #520: the group ignores TERM, the user presses Ctrl-C once: KeyboardInterrupt reaches the caller
        within the limit (grace 1.5 + 2 x 2 = 5.5 s) plus a small margin, and the grandchild is gone (KILL)."""
        got, _ = self.drive("int-trap")
        self.assertEqual(got["exc"], "KeyboardInterrupt", got)
        self.assertEqual(got["status"], "stopped", got)
        self.assertLess(got["elapsed"], 0.5 + got["limit"] + 1.0, got)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived" % self.gc)

    def test_a_stuck_stop_delivers_the_held_ctrl_c_by_itself_at_the_limit(self):
        """a63ab #520: the stop is stuck (30 s), Ctrl-C is pressed once during it and never again. At the limit
        (0.5 + 2 x 0.2 = 0.9 s after the interrupt) it goes through by itself; the report says the stop was cut."""
        got, err = self.drive("stuck")
        self.assertEqual(got["exc"], "KeyboardInterrupt", got)
        self.assertAlmostEqual(got["limit"], 0.9, places=6)
        self.assertGreater(got["elapsed"], 1.2, "delivered before the limit: %s" % got)
        self.assertLess(got["elapsed"], 2.9, "the held Ctrl-C did not go through by itself at the limit: %s" % got)
        self.assertEqual((got["status"], got["why"]), ("unverified", "stop_cut_short_at_limit"), got)
        self.assertIn("stop result for group", err)
        self.assertIn("unverified stop_cut_short_at_limit", err)   # the log never reads as a clean stop
        self.assertTrue(got["sigint_restored"], got)

    def test_an_interrupt_during_the_final_reap_still_stops_the_group(self):
        """bb7dc #520: the reap is inside the guarded block too. The leader exits and leaves a grandchild
        (leftovers kept), the interrupt lands in the reap: the group is still stopped, same object comes out."""
        class Boom(BaseException):
            pass

        boom = Boom()

        class P(subprocess.Popen):
            def wait(self, timeout=None):
                if timeout is None and not getattr(self, "_boomed", False):
                    self._boomed = True
                    raise boom
                return super().wait(timeout=timeout)

        with self.assertRaises(Boom) as cm:
            pg.run_group(["/bin/sh", "-c", "sleep 60 & echo $! > %s" % self.pidfile], timeout=30, grace=0.5,
                         leftovers="keep", popen=P)
        self.assertIs(cm.exception, boom)
        self.gc = int(Path(self.pidfile).read_text().strip())
        self.assertEqual(boom.proc_group_stop.status, "stopped", boom.proc_group_stop)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived an interrupt in the reap" % self.gc)

    def test_inherit_with_a_timeout_still_stops_the_grandchild(self):
        """bb7dc #520: output="inherit" takes the same stop path; a timeout stops the group, out is ""."""
        r = pg.run_group(["/bin/sh", "-c", "sleep 60 & echo $! > %s; wait" % self.pidfile], timeout=1.0, grace=0.5,
                         output="inherit")
        self.gc = int(Path(self.pidfile).read_text().strip())
        self.assertTrue(r.timed_out, r)
        self.assertEqual(r.out, "")
        self.assertEqual(r.stop.status, "stopped", r.stop)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived an inherit timeout" % self.gc)

    def test_an_interrupted_inherit_run_still_stops_the_group(self):
        """bb7dc #520: interrupted while waiting with output="inherit": group stopped, same object raised."""
        class Boom(BaseException):
            pass

        boom = Boom()
        calls = []
        real = pg._exited_or_poll

        def poll(p):
            calls.append(p.pid)
            if len(calls) == 3:
                raise boom
            return real(p)

        pg._exited_or_poll = poll
        try:
            with self.assertRaises(Boom) as cm:
                pg.run_group(["/bin/sh", "-c", "sleep 60 & echo $! > %s; wait" % self.pidfile], timeout=30,
                             grace=0.5, output="inherit")
        finally:
            pg._exited_or_poll = real
        self.assertIs(cm.exception, boom)
        self.gc = int(Path(self.pidfile).read_text().strip())
        self.assertEqual(boom.proc_group_stop.status, "stopped", boom.proc_group_stop)
        self.assertTrue(gone_within(self.gc), "grandchild %s survived an interrupted inherit run" % self.gc)

    def test_the_default_limit_is_grace_plus_two_kill_waits(self):
        self.assertEqual((pg.DEFAULT_GRACE_S, pg.KILL_WAIT_S), (5.0, 2.0))
        self.assertEqual(pg.defer_limit_s(pg.DEFAULT_GRACE_S), 9.0)

    def test_any_base_exception_is_raised_again_as_the_same_object(self):
        class Boom(BaseException):
            pass

        boom = Boom()
        calls = []
        real = pg._exited_or_poll

        def poll(p):
            calls.append(p.pid)
            if len(calls) == 3:
                raise boom
            return real(p)

        pg._exited_or_poll = poll
        try:
            with self.assertRaises(Boom) as cm:
                pg.run_group(["/bin/sh", "-c", "sleep 60 & echo $! > %s; wait" % self.pidfile], timeout=30,
                             grace=0.5)
        finally:
            pg._exited_or_poll = real
        self.assertIs(cm.exception, boom)
        self.assertEqual(boom.proc_group_stop.status, "stopped", boom.proc_group_stop)
        self.gc = int(Path(self.pidfile).read_text().strip())
        self.assertTrue(gone_within(self.gc))


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
