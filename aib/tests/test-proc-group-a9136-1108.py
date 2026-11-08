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

    def test_a_command_that_cannot_start_says_so(self):
        r = pg.run_group(["/no/such/command"], timeout=1)
        self.assertIsNone(r.rc)
        self.assertFalse(r.timed_out)
        self.assertIn("could not start", r.error)


class FakeP:
    pid = 424242

    def __init__(self):
        self.killed = False

    def kill(self):
        self.killed = True


class Fakes(unittest.TestCase):
    """EPERM, identity and Windows paths without touching real processes (pid 424242 is never signalled)."""

    def group(self, killpg, rows, start_now=100):
        calls = []

        def kp(pgid, sig):
            calls.append(sig)
            return killpg(pgid, sig)

        g = pg._Group(FakeP(), start=100, killpg=kp, members=lambda pgid: rows,
                      start_of=lambda pid: start_now, sleep=lambda s: None)
        return g, calls

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

    def test_leftovers_must_be_stop_or_keep(self):
        with self.assertRaises(ValueError):
            pg.run_group(["true"], timeout=1, leftovers="maybe")


if __name__ == "__main__":
    unittest.main()
