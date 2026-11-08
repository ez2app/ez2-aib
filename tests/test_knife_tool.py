"""scripts/knife.py: the shared knife tool (DECISION-18 item 3, a63ab outline r2 P2-7; 043dd 11-08).

Each test builds a tiny Python project in a temp dir (a.py + test_a.py) and knifes it with
`python -m unittest`, so no cargo is needed. Covers the five outcomes, the noop gate, the restore,
the leftover check (DECISION-7 13b) and the old single-knife CLI.
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "knife.py"
spec = importlib.util.spec_from_file_location("knife_tool", str(TOOL))
knife = importlib.util.module_from_spec(spec)
spec.loader.exec_module(knife)

A = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n\n\nUNUSED = 1\n"
TEST_A = ("import unittest\nimport a\n\n\nclass T(unittest.TestCase):\n"
          "    def test_add(self):\n        self.assertEqual(a.add(2, 3), 5)\n\n"
          "    def test_sub(self):\n        self.assertEqual(a.sub(5, 3), 2)\n")
CMD = [sys.executable, "-B", "-m", "unittest", "test_a"]


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.d = Path(self.td.name)
        (self.d / "a.py").write_text(A, encoding="utf-8")
        (self.d / "test_a.py").write_text(TEST_A, encoding="utf-8")

    def tearDown(self):
        self.td.cleanup()

    def spec(self, knives, **kw):
        s = dict({"cmd": CMD, "timeout": 60, "knives": knives}, **kw)
        p = self.d / "knives.json"
        p.write_text(json.dumps(s), encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = knife.main(["--spec", str(p), "--repo", str(self.d)])
        return rc, buf.getvalue()

    def k(self, name, old, new, expect="test_add", **kw):
        return dict({"name": name, "file": "a.py", "old": old, "new": new, "expect_fail": expect}, **kw)


class Outcomes(Base):
    def test_bit(self):
        rc, out = self.spec([self.k("plus-to-minus", "return a + b", "return a - b")])
        self.assertEqual(rc, 0, out)
        self.assertIn("NOOP GREEN", out)
        self.assertRegex(out, r"BIT\s+plus-to-minus")
        self.assertIn("RESULT BIT=1 SURVIVED=0", out)

    def test_survived_when_nothing_fails(self):
        rc, out = self.spec([self.k("unused", "UNUSED = 1", "UNUSED = 2")])
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"SURVIVED\s+unused")

    def test_survived_when_a_different_test_fails(self):
        # "everything went red" or "some other test went red" is not a bite
        rc, out = self.spec([self.k("breaks-sub", "return a - b", "return a + b", expect="test_add")])
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"SURVIVED\s+breaks-sub")
        self.assertIn("test_sub", out)

    def test_not_fallen_when_the_knife_does_not_build(self):
        rc, out = self.spec([self.k("syntax", "return a + b", "return a +")])
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"NOT-FALLEN\s+syntax")

    def test_anchor_missing_is_red_and_changes_nothing(self):
        before = (self.d / "a.py").read_bytes()
        rc, out = self.spec([self.k("drifted", "return a * b", "return 0")])
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"ANCHOR-MISSING\s+drifted.*found 0 times")
        self.assertEqual((self.d / "a.py").read_bytes(), before)

    def test_anchor_twice_is_red(self):
        rc, out = self.spec([self.k("twice", "(a, b)", "(a, c)")])
        self.assertEqual(rc, 1)
        self.assertIn("found 2 times", out)

    def test_timeout_is_undecided_and_stops(self):
        rc, out = self.spec([self.k("hang", "return a + b", "import time; time.sleep(30)"),
                             self.k("next", "return a - b", "return 0", expect="test_sub")], timeout=3)
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"UNDECIDED\s+hang.*timeout")
        self.assertIn("NOT-RUN=1", out)

    def test_one_bad_knife_fails_the_whole_run(self):
        rc, out = self.spec([self.k("good", "return a + b", "return a - b"),
                             self.k("bad", "UNUSED = 1", "UNUSED = 2")])
        self.assertEqual(rc, 1)
        self.assertIn("RESULT BIT=1 SURVIVED=1", out)


class Noop(Base):
    def test_red_baseline_runs_no_knife(self):
        (self.d / "a.py").write_text(A.replace("return a + b", "return 0"), encoding="utf-8")
        rc, out = self.spec([self.k("plus-to-minus", "return 0", "return 1")])
        self.assertEqual(rc, 1)
        self.assertIn("NOOP RED", out)
        self.assertIn("no knife run", out)
        self.assertNotIn("BIT ", out)


class Review525(Base):
    """1ced7's #525 review: three ways a knife that bit nothing still said BIT / rc 0."""

    def test_h1_empty_expect_fail_is_a_usage_error(self):
        rc, out = self.spec([self.k("sub", "return a - b", "return a + b", expect="")])
        self.assertEqual(rc, 2, out)
        self.assertNotIn("BIT ", out)
        rc, out = self.spec([self.k("sub", "return a - b", "return a + b", expect=None)])
        self.assertEqual(rc, 2, out)

    def test_h1_empty_new_is_still_allowed(self):
        rc, out = self.spec([self.k("del", "return a + b", "", )])
        self.assertNotEqual(rc, 2, out)

    def test_h2_prefix_name_does_not_match(self):
        (self.d / "test_a.py").write_text(TEST_A + "\n    def test_add_many(self):\n"
                                          "        self.assertEqual(a.add(a.sub(4, 2), 3), 5)\n", encoding="utf-8")
        # knife breaks sub: test_sub and test_add_many go red, test_add stays green
        rc, out = self.spec([self.k("sub", "return a - b", "return a + b", expect="test_add")])
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"SURVIVED\s+sub")

    def test_h2_name_matching(self):
        m = knife.name_matches
        self.assertTrue(m("test_add", "test_add"))
        self.assertTrue(m("job::tests::test_add", "test_add"))
        self.assertTrue(m("test_a.T.test_add", "test_add"))
        self.assertFalse(m("test_add_many", "test_add"))
        self.assertFalse(m("job::tests::test_add_many", "test_add"))
        self.assertFalse(m("anything", ""))

    def test_h3_every_per_knife_cmd_gets_a_noop(self):
        red = [sys.executable, "-B", "-c", "import sys; print('FAIL: test_add (x)'); sys.exit(1)"]
        rc, out = self.spec([self.k("ok", "return a + b", "return a - b", cmd=CMD),
                             self.k("red-before", "return a - b", "return a + b", cmd=red)], cmd=None)
        self.assertEqual(rc, 1, out)
        self.assertIn("NOOP RED", out)
        self.assertIn("no knife run", out)
        self.assertNotIn("BIT ", out)

    def test_h3_same_cmd_runs_noop_once(self):
        rc, out = self.spec([self.k("a", "return a + b", "return a - b"),
                             self.k("b", "return a - b", "return a + b", expect="test_sub")])
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("NOOP GREEN"), 1, out)


class Restore(Base):
    def test_file_is_restored_byte_identical_with_a_new_mtime(self):
        p = self.d / "a.py"
        before = p.read_bytes()
        old = time.time() - 3600
        os.utime(p, (old, old))
        self.spec([self.k("plus-to-minus", "return a + b", "return a - b")])
        self.assertEqual(p.read_bytes(), before)
        self.assertGreater(p.stat().st_mtime, old + 60, "restored by writing, not by cp/mv")

    def test_crlf_file_keeps_its_line_endings(self):
        p = self.d / "a.py"
        p.write_bytes(A.replace("\n", "\r\n").encode())
        before = p.read_bytes()
        self.spec([self.k("plus-to-minus", "return a + b", "return a - b")])
        self.assertEqual(p.read_bytes(), before)


@unittest.skipUnless(os.name == "posix", "process groups are POSIX only")
class Leftover(Base):
    def test_a_leftover_child_is_undecided_killed_and_stops_the_run(self):
        marker = self.d / "leftover.pid"
        new = ("import subprocess as _s\n_p = _s.Popen(['sleep', '60'])\n"
               "open(%r, 'w').write(str(_p.pid))\n\n\ndef add(a, b):" % str(marker))
        rc, out = self.spec([self.k("spawns", "def add(a, b):", new),
                             self.k("next", "return a - b", "return 0", expect="test_sub")])
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"UNDECIDED\s+spawns.*left in the process group")
        self.assertIn("NOT-RUN=1", out)
        pid = int(marker.read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            self.fail("the leftover sleep %d was not killed" % pid)

    def test_noop_with_a_leftover_is_red(self):
        (self.d / "a.py").write_text("import subprocess as _s\n_s.Popen(['sleep', '60'])\n" + A, encoding="utf-8")
        rc, out = self.spec([self.k("plus-to-minus", "return a + b", "return a - b")])
        self.assertEqual(rc, 1)
        self.assertIn("NOOP RED", out)
        self.assertIn("leftover=True", out)


class SingleCli(Base):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-I", str(TOOL)] + list(args), cwd=str(self.d),
                              capture_output=True, text=True, timeout=120)

    def test_bit_rc0(self):
        r = self.run_cli("a.py", "return a + b", "return a - b", "--expect-fail", "test_add", "--", *CMD)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("KNIFE test_add: BIT", r.stdout)

    def test_anchor_missing_rc1(self):
        r = self.run_cli("a.py", "nope", "x", "--expect-fail", "test_add", "--", *CMD)
        self.assertEqual(r.returncode, 1)
        self.assertIn("ANCHOR-MISSING", r.stdout)

    def test_usage_rc2(self):
        self.assertEqual(self.run_cli("a.py", "x", "y", "--expect-fail", "t").returncode, 2)
        bad = self.d / "bad.json"
        bad.write_text("{", encoding="utf-8")
        self.assertEqual(self.run_cli("--spec", str(bad)).returncode, 2)


class NoSignalAfterReap(Base):
    """bb7dc #525 High: the group leader must never be reaped before the last signal to its pgid, or that pgid
    may already belong to someone else. Every killpg is checked at the moment it is sent."""

    def setUp(self):
        super().setUp()
        self.calls = []
        self.leaders = []
        real_popen, real_killpg = knife.subprocess.Popen, knife.os.killpg
        test = self

        class Tracked(real_popen):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                if kw.get("start_new_session"):
                    test.leaders.append(self)

        def killpg(pgid, sig):
            leader = [p for p in test.leaders if p.pid == pgid]
            test.calls.append((sig, bool(leader) and leader[0].returncode is None))
            return real_killpg(pgid, sig)

        knife.subprocess.Popen, knife.os.killpg = Tracked, killpg
        self.addCleanup(setattr, knife.subprocess, "Popen", real_popen)
        self.addCleanup(setattr, knife.os, "killpg", real_killpg)

    def check(self):
        self.assertTrue(self.leaders, "no group leader was started")
        for sig, unreaped in self.calls:
            self.assertTrue(unreaped, "killpg(%s) sent after the leader was reaped: %s" % (sig, self.calls))
        for p in self.leaders:
            self.assertIsNotNone(p.returncode, "leader never reaped")

    def test_leftover_path(self):
        leftover_cmd = [sys.executable, "-c",
                        "import subprocess, sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])"]
        rc, out, leftover = knife.run(leftover_cmd, str(self.d), 30)
        self.assertEqual(rc, 0, out)
        self.assertTrue(leftover)
        self.assertTrue(any(sig == knife.signal.SIGKILL for sig, _ in self.calls), self.calls)
        self.check()

    def test_timeout_path(self):
        rc, out, leftover = knife.run([sys.executable, "-c", "import time; time.sleep(30)"], str(self.d), 1)
        self.assertIsNone(rc)
        self.assertTrue(self.calls, "timeout sent nothing")
        self.check()

    def test_clean_path_sends_nothing(self):
        rc, out, leftover = knife.run([sys.executable, "-c", "print('hi')"], str(self.d), 30)
        self.assertEqual((rc, leftover), (0, False), out)
        self.assertIn("hi", out)
        self.assertEqual(self.calls, [])
        self.check()

    def test_rc_and_missing_program(self):
        rc, out, _ = knife.run([sys.executable, "-c", "import sys; sys.exit(3)"], str(self.d), 30)
        self.assertEqual(rc, 3)
        rc, out, _ = knife.run(["/no/such/program-043dd"], str(self.d), 30)
        self.assertEqual(rc, 127)
        self.assertIn("cannot start", out)
        self.check()

    def test_unreadable_process_list_is_undecided(self):
        real = knife.group_members
        knife.group_members = lambda pgid, exclude: None
        self.addCleanup(setattr, knife, "group_members", real)
        rc, out = self.spec([self.k("plus-to-minus", "return a + b", "return a - b")])
        self.assertEqual(rc, 1, out)
        self.assertIn("NOOP RED", out)


class SingleCliReview525(Base):
    run_cli = SingleCli.run_cli  # borrowed, not inherited: SingleCli's tests run once

    def test_l1_red_baseline_is_not_bit(self):
        (self.d / "a.py").write_text(A.replace("return a + b", "return 0"), encoding="utf-8")
        r = self.run_cli("a.py", "return 0", "return 1", "--expect-fail", "test_add", "--", *CMD)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("NOOP RED", r.stdout)
        self.assertNotIn("BIT", r.stdout.replace("NOOP", ""))

    def test_h1_empty_expect_fail_rc2(self):
        r = self.run_cli("a.py", "return a - b", "return a + b", "--expect-fail", "", "--", *CMD)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_l3_old_reading_spec_stays_single_knife(self):
        (self.d / "a.py").write_text(A + "X = '--spec'\n", encoding="utf-8")
        r = self.run_cli("a.py", "--spec", "--specX", "--expect-fail", "test_add", "--", *CMD)
        self.assertIn("KNIFE test_add:", r.stdout, r.stdout + r.stderr)


class FailedNames(unittest.TestCase):
    def test_cargo_unittest_pytest(self):
        out = ("test job::tests::the_owner_crashing_kills_the_tree ... FAILED\n"
               "test job::tests::other ... ok\n"
               "FAIL: test_add (test_a.T)\nERROR: test_sub (test_a.T.test_sub)\n"
               "FAILED tests/test_x.py::test_y - AssertionError\n")
        self.assertEqual(knife.failed_names(out), ["job::tests::the_owner_crashing_kills_the_tree", "test_add",
                                                   "test_sub", "test_y"])

    def test_cargo_quiet_failures_list(self):
        # `cargo test -q`: dots, the stdout blocks, then the indented list; no "test x ... FAILED" line
        out = ("running 3 tests\n.F.\nfailures:\n\n---- job::tests::kills_the_tree stdout ----\n"
               "thread 'job::tests::kills_the_tree' panicked at src/job.rs:9:5\n\n\n"
               "failures:\n    job::tests::kills_the_tree\n\ntest result: FAILED. 2 passed; 1 failed\n")
        self.assertEqual(knife.failed_names(out), ["job::tests::kills_the_tree"])
        self.assertEqual(knife.judge(101, out, "kills_the_tree")[0], "BIT")
        self.assertEqual(knife.judge(101, out.replace("\n", "\r\n"), "kills_the_tree")[0], "BIT")
        self.assertEqual(knife.judge(101, out, "kills")[0], "SURVIVED")

    def test_build_failure_patterns(self):
        for s in ("error: could not compile `aib-sys`", "error[E0425]: cannot find value", "LINK : fatal error LNK1104",
                  "SyntaxError: invalid syntax"):
            self.assertEqual(knife.judge(101, s, "x")[0], "NOT-FALLEN", s)

    def test_expected_failure_wins_over_a_quoted_build_error(self):
        out = "test t::reports_build_errors ... FAILED\nleft: \"error: could not compile `x`\"\n"
        self.assertEqual(knife.judge(101, out, "reports_build_errors")[0], "BIT")


if __name__ == "__main__":
    unittest.main()
