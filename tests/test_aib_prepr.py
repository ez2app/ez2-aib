# SPDX-License-Identifier: Apache-2.0
"""`aib prepr`: the pre-PR checklist (DECISION-7 A, 2026-11-08).

Every item is pass / fail / not_run with its reason; not_run is never green; a missing tool or target is reported,
not installed; the Windows cross clippy names the members it leaves out (C builds). Standard library only.
"""
import json
import os
import re
import time
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AIB = [sys.executable, str(ROOT / "bin" / "aib")]
sys.path.insert(0, str(ROOT / "aib" / "tools" / "verify"))
import prepr  # noqa: E402


def meta():
    """Workspace: core (pure), store -> libsqlite3-sys (links), daemon -> store."""
    p = lambda n, links=None: {"id": n, "name": n, "links": links}  # noqa: E731
    return {"packages": [p("core"), p("store"), p("daemon"), p("libsqlite3-sys", "sqlite3"), p("libc")],
            "workspace_members": ["core", "store", "daemon"],
            "resolve": {"nodes": [{"id": "core", "deps": [{"pkg": "libc"}]},
                                  {"id": "store", "deps": [{"pkg": "libsqlite3-sys"}]},
                                  {"id": "daemon", "deps": [{"pkg": "store"}, {"pkg": "core"}]},
                                  {"id": "libsqlite3-sys", "deps": []}, {"id": "libc", "deps": []}]}}


class Fake:
    """Runner answering by the first words of the command; records what ran."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, cmd, cwd, env=None, timeout=0):
        self.calls.append(cmd)
        for key, ans in self.answers.items():
            if cmd[:len(key)] == list(key):
                return ans
        return 0, ""


def rust_ok(over=None):
    a = {("cargo", "--version"): (0, "cargo 1.99.1\n"),
         ("rustup", "target"): (0, "aarch64-apple-darwin\nx86_64-pc-windows-msvc\n"),
         ("cargo", "metadata"): (0, json.dumps(meta()))}
    a.update(over or {})
    return a


def by_name(items):
    return {i["name"]: i for i in items}


class Units(unittest.TestCase):
    def test_windows_clippy_leaves_out_members_that_build_c_and_says_so(self):
        ok, excluded = prepr.windows_split(meta())
        self.assertEqual(ok, ["core"])
        self.assertEqual(excluded, {"store": ["libsqlite3-sys"], "daemon": ["libsqlite3-sys"]})
        f = Fake(rust_ok())
        w = by_name(prepr.rust_items("/r", False, f))["rust clippy (x86_64-pc-windows-msvc)"]
        self.assertEqual(w["status"], "pass")
        self.assertIn("only core", w["scope"])
        self.assertIn("daemon (libsqlite3-sys)", w["scope"])
        self.assertIn(["-p", "core"], [c[2:4] for c in f.calls if c[:2] == ["cargo", "clippy"]])

    def test_a_missing_tool_or_target_is_not_run_and_nothing_is_installed(self):
        f = Fake({("cargo", "--version"): (None, "not found: cargo")})
        items = prepr.rust_items("/r", True, f)
        self.assertEqual({i["status"] for i in items}, {"not_run"})
        f = Fake(rust_ok({("rustup", "target"): (0, "aarch64-apple-darwin\n")}))
        w = by_name(prepr.rust_items("/r", False, f))["rust clippy (x86_64-pc-windows-msvc)"]
        self.assertEqual(w["status"], "not_run")
        self.assertFalse(any("add" in c or "install" in c for c in f.calls), f.calls)

    def test_a_red_check_fails_with_its_real_exit_code(self):
        f = Fake(rust_ok({("cargo", "fmt"): (1, "Diff in src/x.rs")}))
        fmt = by_name(prepr.rust_items("/r", False, f))["rust fmt"]
        self.assertEqual((fmt["status"], fmt["rc"]), ("fail", 1))
        self.assertIn("Diff in", fmt["tail"])

    def test_tests_need_run_tests_and_cargo_test_does_not_stop_at_the_first_failure(self):
        f = Fake(rust_ok())
        self.assertEqual(by_name(prepr.rust_items("/r", False, f))["cargo test"]["status"], "not_run")
        self.assertFalse(any(c[:2] == ["cargo", "test"] for c in f.calls))
        f = Fake(rust_ok({("cargo", "test"): (101, "test result: FAILED")}))
        t = by_name(prepr.rust_items("/r", True, f))["cargo test"]
        self.assertEqual((t["status"], t["rc"]), ("fail", 101))
        self.assertIn("--no-fail-fast", t["cmd"])

    def test_aib_test_selecting_nothing_is_not_green(self):
        f = Fake({("aib",): (0, "Your changes select no tests. Nothing was run — this is not 'green'")})
        i = prepr.python_item("/r", "origin/main", True, ["aib"], f)
        self.assertEqual((i["status"], i["rc"]), ("not_run", 0))
        f = Fake({("aib",): (0, "4 passed")})
        self.assertEqual(prepr.python_item("/r", "origin/main", True, ["aib"], f)["status"], "pass")
        self.assertIn("--timeout", f.calls[-1])
        f = Fake({("aib",): (1, "1 failed")})
        self.assertEqual(prepr.python_item("/r", "origin/main", True, ["aib"], f)["status"], "fail")

    def test_aib_test_did_not_run_codes_are_not_run_not_fail(self):
        f = Fake({("aib",): (5, "machine held exclusively by bb7dc")})
        i = prepr.python_item("/r", "origin/main", True, ["aib"], f)
        self.assertEqual((i["status"], i["rc"]), ("not_run", 5))
        self.assertIn("exclusively", i["reason"])
        self.assertIn("bb7dc", i["tail"])
        f = Fake({("aib",): (4, "3 skipped")})
        i = prepr.python_item("/r", "origin/main", True, ["aib"], f)
        self.assertEqual((i["status"], i["rc"]), ("not_run", 4))
        self.assertIn("skipped", i["reason"])

    def test_a_command_that_times_out_or_cannot_start_is_not_run(self):
        rc, out = prepr.run([sys.executable, "-c", "import time; time.sleep(5)"], ".", timeout=1)
        self.assertEqual(rc, None)
        self.assertIn("timed out", out)
        f = Fake(rust_ok({("cargo", "fmt"): (None, "timed out after 1s")}))
        fmt = by_name(prepr.rust_items("/r", False, f))["rust fmt"]
        self.assertEqual(fmt["status"], "not_run")
        self.assertIn("timed out", fmt["reason"])
        f = Fake({("aib",): (None, "not found: aib")})
        self.assertEqual(prepr.python_item("/r", "origin/main", True, ["aib"], f)["status"], "not_run")

    def test_a_finished_command_with_unverified_leftovers_is_not_run_never_green(self):
        # bb7dc #509: rc 0 + leftovers that could not be shown stopped must not read as a pass
        import proc_group
        real = proc_group.run_group
        rep = proc_group.StopReport("unverified", "members_still_running", [4242])
        proc_group.run_group = lambda *a, **k: proc_group.Result(0, "all good\n", stop=rep)
        try:
            rc, out = prepr.run(["cargo", "test"], ".")
        finally:
            proc_group.run_group = real
        self.assertIsNone(rc)
        self.assertIn("unverified", out)
        self.assertIn("4242", out)
        self.assertIn("rc 0", out)
        # red first: a real failure stays a failure, with the leftover reason in the kept tail
        proc_group.run_group = lambda *a, **k: proc_group.Result(7, "x" * 5000 + "boom\n", stop=rep)
        try:
            rc7, out7 = prepr.run(["cargo", "test"], ".")
        finally:
            proc_group.run_group = real
        self.assertEqual(rc7, 7)
        f = Fake(rust_ok({("cargo", "fmt"): (rc7, out7)}))
        fmt = by_name(prepr.rust_items("/r", False, f))["rust fmt"]
        self.assertEqual((fmt["status"], fmt["rc"]), ("fail", 7))
        self.assertIn("4242", fmt["tail"])
        self.assertIn("members_still_running", fmt["tail"])
        proc_group.run_group = lambda *a, **k: proc_group.Result(
            None, "", timed_out=True, stop=proc_group.StopReport("unverified", "members_still_running", [4343]))
        try:
            rc2, out2 = prepr.run(["cargo", "test"], ".", timeout=7)
        finally:
            proc_group.run_group = real
        self.assertIsNone(rc2)
        self.assertIn("members_still_running", out2)
        self.assertIn("4343", out2)
        f = Fake(rust_ok({("cargo", "fmt"): (rc, out)}))
        fmt = by_name(prepr.rust_items("/r", False, f))["rust fmt"]
        self.assertEqual(fmt["status"], "not_run")
        self.assertIn("4242", fmt["reason"])

    @unittest.skipUnless(hasattr(os, "killpg"), "process groups are POSIX")
    def test_a_timeout_stops_the_whole_tree_not_just_the_child(self):
        td = Path(tempfile.mkdtemp(prefix="aib-prepr-tree-"))
        try:
            pidfile = td / "grandchild.pid"
            child = ("import os, subprocess, sys, time; g = subprocess.Popen([sys.executable, '-c', "
                     "'import time; time.sleep(60)']); open(sys.argv[1], 'w').write('%d %d' % (os.getpid(), g.pid)); "
                     "time.sleep(60)")
            rc, out = prepr.run([sys.executable, "-c", child, str(pidfile)], str(td), timeout=2)
            self.assertIsNone(rc)
            self.assertIn("timed out", out)
            gpid = int(pidfile.read_text().split()[1])
            gone = False
            for _ in range(60):
                try:
                    os.kill(gpid, 0)
                except ProcessLookupError:
                    gone = True
                    break
                time.sleep(0.05)
            if not gone:
                os.kill(gpid, 9)  # only the pid this test started itself
            self.assertTrue(gone, "grandchild %d outlived the timeout" % gpid)
        finally:
            # Whatever happened above, collect the two pids this test started itself (never a wider kill).
            try:
                pids = [int(x) for x in (td / "grandchild.pid").read_text().split()]
            except (OSError, ValueError):
                pids = []
            for pid in pids:
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass
            shutil.rmtree(td, ignore_errors=True)

    def test_aib_test_gets_the_long_outer_timeout_not_3600(self):
        # #494 L7: aib test stops a hung test itself; the outer limit is only a backstop
        seen = {}

        def runner(cmd, cwd, env=None, timeout=0):
            seen["timeout"] = timeout
            return 0, "ok"

        prepr.python_item("/r", "origin/main", True, ["aib"], runner)
        self.assertEqual(seen["timeout"], prepr.AIB_TEST_OUTER_TIMEOUT_S)
        self.assertGreater(prepr.AIB_TEST_OUTER_TIMEOUT_S, 3685)

    def test_glob_patterns_prepr_cannot_read_make_every_file_rust(self):
        # #494 L9: wrong in the safe direction only
        self.assertTrue(prepr.glob_re("**/README.md").match("README.md"))
        self.assertTrue(prepr.glob_re("**/README.md").match("a/b/README.md"))
        td = tempfile.mkdtemp(prefix="aib-prepr-odd-")
        try:
            wf = Path(td) / ".github" / "workflows"
            wf.mkdir(parents=True)
            for odd in ("crates/[ab]*/**", "x?.toml", "a+b", "c\\d"):
                (wf / "aibd.yml").write_text("on:\n  pull_request:\n    paths:\n      - 'crates/**'\n      - '%s'\n" % odd)
                patterns, source = prepr.rust_paths(td)
                self.assertIn("cannot read", source, odd)
                self.assertTrue(prepr.kinds(["aib/x.py"], patterns)["rust"], odd)
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_aib_test_exit_codes_match_test_entry(self):
        src = (ROOT / "aib" / "tools" / "verify" / "test_entry.py").read_text()
        m = re.search(r"^RC_EXCLUSIVE = (\d+)", src, re.M)
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)), prepr.AIB_TEST_RC_EXCLUSIVE)
        self.assertRegex(src, r"整批 rc %d" % prepr.AIB_TEST_RC_ALL_SKIPPED)

    def test_files_that_aibd_ci_treats_as_rust_trigger_the_rust_checks(self):
        # the patterns come from the repo's own aibd.yml, so a path added there (check-sys-deps.sh, #485) follows
        patterns, source = prepr.rust_paths(str(ROOT))
        self.assertEqual(source, prepr.AIBD_WORKFLOW)
        wf = (ROOT / ".github" / "workflows" / "aibd.yml").read_text()
        self.assertEqual(list(patterns), prepr.workflow_paths(wf))
        for f in ("migrations/0002_x.sql", "rust-toolchain.toml", "clippy.toml", "scripts/check-core-purity.sh",
                  "scripts/check-sys-deps.sh", "crates/aibd/README.md", "xtask/Cargo.toml",
                  ".github/workflows/aibd.yml"):
            self.assertTrue(prepr.kinds([f], patterns)["rust"], f)
            self.assertFalse(prepr.kinds([f], patterns)["python"], f)
        for p in patterns:
            self.assertTrue(prepr.is_rust(p.replace("**", "x/y.sql"), patterns), p)

    def test_the_workflow_reader_takes_pull_request_paths_globs_and_negations(self):
        wf = textwrap.dedent("""\
            name: x
            on:
              push:
                paths:
                  - "push-only/**"
              pull_request:
                types: [opened]
                paths:
                  - 'crates/**'   # quoted with single quotes
                  - "scripts/*.sh"
                  - "!crates/legacy/**"
                  - Cargo.lock
            permissions:
              contents: read
            """)
        pats = prepr.workflow_paths(wf)
        self.assertEqual(pats, ["crates/**", "scripts/*.sh", "!crates/legacy/**", "Cargo.lock"])
        self.assertTrue(prepr.is_rust("crates/a/b/c.toml", pats))
        self.assertFalse(prepr.is_rust("crates/legacy/a.toml", pats))
        self.assertTrue(prepr.is_rust("scripts/x.sh", pats))
        self.assertFalse(prepr.is_rust("scripts/sub/x.sh", pats))
        self.assertFalse(prepr.is_rust("push-only/a.txt", pats))
        self.assertTrue(prepr.is_rust("Cargo.lock", pats))
        self.assertTrue(prepr.is_rust("any/where.rs", pats))
        self.assertEqual(prepr.workflow_paths("on:\n  push:\n    paths:\n      - a\n"), [])

    def test_a_repo_without_the_workflow_uses_the_fallback_and_says_so(self):
        td = tempfile.mkdtemp(prefix="aib-prepr-nowf-")
        try:
            patterns, source = prepr.rust_paths(td)
            self.assertEqual(patterns, prepr.FALLBACK_RUST_PATHS)
            self.assertIn("fallback", source)
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_which_checks_apply(self):
        self.assertEqual(prepr.kinds(["a/b.rs"]), {"rust": True, "python": False, "docs_only": False})
        self.assertEqual(prepr.kinds(["x.py", "Cargo.lock"]), {"rust": True, "python": True, "docs_only": False})
        self.assertEqual(prepr.kinds(["README.md"]), {"rust": False, "python": False, "docs_only": True})
        self.assertEqual(prepr.kinds([])["docs_only"], False)


class TimeoutCalls(unittest.TestCase):
    """Item 10 (DECISION-20, a63ab 11-08): a NEW subprocess call with its own timeout outside proc_group warns.
    Only added lines count; the first week it is `warn` (never green, never a failure)."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-prepr-to-"))
        self.r = self.td / "r"
        self.r.mkdir()
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "-q", "-b", "main")
        self.write("old.py", "import subprocess\nsubprocess.run(['true'], timeout=5)\n\n\n")
        self.commit("base")
        self.base = self.git("rev-parse", "HEAD").strip()

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def git(self, *a):
        return subprocess.run(["git", "-C", str(self.r)] + list(a), env=self.env, check=True, capture_output=True,
                              text=True).stdout

    def write(self, name, text):
        (self.r / name).parent.mkdir(parents=True, exist_ok=True)
        (self.r / name).write_text(text)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg)

    def items(self):
        return prepr.timeout_items(str(self.r), self.base, prepr.changed_files(str(self.r), self.base))

    def item(self):
        got = self.items()
        self.assertEqual(len(got), 1, got)
        return got[0]

    def test_a_new_multi_line_call_warns_with_file_and_line(self):
        self.write("new.py", "import subprocess\nx = 1\nsubprocess.run(\n    ['true'],\n    timeout=5)\n")
        self.commit("add")
        i = self.item()
        self.assertEqual(i["status"], "warn", i)
        self.assertIn("new.py:3 subprocess.run(timeout=)", i["reason"])
        self.assertNotIn("old.py", i["reason"])

    def test_an_untouched_old_call_in_an_edited_file_is_not_reported(self):
        self.write("old.py", "import subprocess\nsubprocess.run(['true'], timeout=5)\n\nprint('edit')\n")
        i = self.item()
        self.assertEqual(i["status"], "pass", i)

    def test_an_uncommitted_call_is_found_at_its_working_copy_line(self):
        self.write("old.py", "import subprocess\n# a\n# b\nsubprocess.run(['true'], timeout=5)\n\n"
                             "subprocess.check_output(['true'], timeout=9)\n")
        i = self.item()
        self.assertEqual(i["status"], "warn", i)
        self.assertIn("old.py:6 subprocess.check_output(timeout=)", i["reason"])
        self.assertNotIn("old.py:4", i["reason"])   # the moved old call is not new

    def test_an_untracked_file_counts_every_call(self):
        self.write("tools/u.py", "import subprocess\nsubprocess.call(['true'], timeout=1)\n")
        i = self.item()
        self.assertEqual(i["status"], "warn", i)
        self.assertIn("tools/u.py:2 subprocess.call(timeout=)", i["reason"])

    def test_a_marked_call_is_left_alone(self):
        self.write("new.py", "import subprocess\nsubprocess.run(['true'],\n    timeout=5)  # proc-group: ok daemon\n")
        self.assertEqual(self.item()["status"], "pass")

    def test_wait_counts_only_on_a_process_object_and_communicate_too(self):
        self.write("ev.py", "import threading\nthreading.Event().wait(timeout=1)\n")
        self.assertEqual(self.item()["status"], "pass")
        self.write("pw.py", "import subprocess\np = subprocess.Popen(['true'])\np.wait(timeout=1)\n"
                            "p.communicate(timeout=2)\n")
        i = self.item()
        self.assertEqual(i["status"], "warn", i)
        self.assertIn("pw.py:3 .wait(timeout)", i["reason"])
        self.assertIn("pw.py:4 .communicate(timeout)", i["reason"])
        self.assertNotIn("ev.py", i["reason"])

    def test_a_marker_needs_a_real_comment_and_a_reason(self):
        self.write("m.py", "import subprocess\n"
                           "subprocess.run(['x'], timeout=5)  # proc-group: ok\n"
                           "subprocess.run(['echo', 'proc-group: ok it is'], timeout=5)\n"
                           "subprocess.run(['x'], timeout=5)  # proc-group: ok   starts a daemon on purpose\n")
        i = self.item()
        self.assertEqual(i["status"], "warn", i)
        self.assertIn("m.py:2 subprocess.run(timeout=) (marked `# proc-group: ok` without a reason)", i["reason"])
        self.assertIn("m.py:3 subprocess.run(timeout=)", i["reason"])   # the words in a string are no marker
        self.assertNotIn("m.py:4", i["reason"])

    def test_aliases_positional_timeouts_and_asyncio_are_found(self):
        """043dd #518 M1 / M2: every form below is a call with its own timeout."""
        cases = {
            "import subprocess as sp\nsp.run(['x'], timeout=5)\n": "subprocess.run(timeout=)",
            "from subprocess import run\nrun(['x'], timeout=5)\n": "subprocess.run(timeout=)",
            "from subprocess import check_output as co\nco(['x'], timeout=5)\n": "subprocess.check_output(timeout=)",
            "from subprocess import Popen as P\np = P(['x'])\np.wait(timeout=5)\n": ".wait(timeout)",
            "import subprocess\np = subprocess.Popen(['x'])\np.wait(5)\n": ".wait(timeout)",
            "import subprocess\np = subprocess.Popen(['x'])\np.communicate(None, 5)\n": ".communicate(timeout)",
            "import subprocess\nwith subprocess.Popen(['x']) as q:\n    q.wait(timeout=5)\n": ".wait(timeout)",
            "import subprocess\nclass C:\n    def f(self):\n        self.proc = subprocess.Popen(['x'])\n"
            "        self.proc.wait(timeout=5)\n": ".wait(timeout)",
            "import subprocess\nsubprocess.Popen(['x']).wait(timeout=5)\n": ".wait(timeout)",
            "import asyncio\nasync def f():\n    p = await asyncio.create_subprocess_exec('x')\n"
            "    await asyncio.wait_for(p.wait(), timeout=5)\n": "asyncio.wait_for(.wait())",
            "import asyncio as aio\nasync def f():\n    p = await aio.create_subprocess_shell('x')\n"
            "    await aio.wait_for(p.communicate(), 5)\n": "asyncio.wait_for(.communicate())",
        }
        for text, label in cases.items():
            with self.subTest(text=text):
                got = prepr.timeout_calls(text)
                self.assertIn(label, [g[2] for g in got if g[3] == "hit"], got)

    def test_what_cannot_be_told_is_unclear_never_dropped(self):
        unclear = {
            "import subprocess\nkw = {}\nsubprocess.run(['x'], **kw)\n": "subprocess.run(**kwargs): timeout unknown",
            "import subprocess\np = subprocess.Popen(['x'])\np.wait(**kw)\n": ".wait(**kwargs): timeout unknown",
            "import subprocess\nsubprocess.Popen(['x'])\ndef f(child):\n    child.wait(timeout=5)\n":
                ".wait(timeout) on child: not known to be a process",
        }
        for text, label in unclear.items():
            with self.subTest(text=text):
                got = prepr.timeout_calls(text)
                self.assertIn((label, "unclear"), [(g[2], g[3]) for g in got], got)
        # no process started in the file: a .wait(timeout) is not a question at all
        self.assertEqual(prepr.timeout_calls("import threading\nthreading.Event().wait(timeout=1)\n"), [])
        self.assertEqual(prepr.timeout_calls("import subprocess\nsubprocess.run(['x'])\n"), [])

    def test_a_hit_and_an_unchecked_file_are_reported_as_two_items(self):
        self.write("h.py", "import subprocess\nsubprocess.run(['x'], timeout=5)\n")
        self.write("bad.py", "def (:\n")
        got = self.items()
        self.assertEqual([(i["item"], i["status"]) for i in got], [(10, "warn"), (10, "not_run")], got)
        self.assertIn("h.py:2", got[0]["reason"])
        self.assertNotIn("bad.py", got[0]["reason"])
        self.assertIn("bad.py (does not parse)", got[1]["reason"])

    def test_an_unclear_call_alone_is_not_run_never_pass(self):
        self.write("u.py", "import subprocess\nkw = {}\nsubprocess.run(['x'], **kw)\n")
        i = self.item()
        self.assertEqual(i["status"], "not_run", i)
        self.assertIn("could not check: u.py:3 subprocess.run(**kwargs): timeout unknown", i["reason"])

    def test_a_file_that_does_not_parse_is_not_run_never_pass(self):
        self.write("bad.py", "def (:\n")
        i = self.item()
        self.assertEqual(i["status"], "not_run", i)
        self.assertIn("bad.py (does not parse)", i["reason"])

    def test_a_failing_git_diff_is_not_run_never_pass(self):
        self.write("new.py", "import subprocess\nsubprocess.run(['true'], timeout=5)\n")
        self.commit("add")
        i, = prepr.timeout_items(str(self.r), "no-such-ref", ["new.py"])
        self.assertEqual(i["status"], "not_run", i)
        self.assertIn("new.py (git diff failed)", i["reason"])

    def test_proc_group_itself_and_non_python_are_skipped(self):
        self.write("aib/src/cockpit/proc_group.py", "import subprocess\nsubprocess.run(['true'], timeout=5)\n")
        i = self.item()   # the shared tool itself is the one place that may call subprocess with a timeout
        self.assertEqual((i["status"], i["reason"]), ("not_run", "not applicable: no Python change"), i)
        self.assertEqual([i["status"] for i in prepr.timeout_items(str(self.r), self.base, ["a.md"])], ["not_run"])

    def test_warn_is_not_a_failure_of_the_whole_run(self):
        self.write("new.py", "import subprocess\nsubprocess.run(['true'], timeout=5)\n")
        self.assertEqual(prepr.TIMEOUT_CALLS_STATUS, "warn")
        with tempfile.TemporaryFile("w+") as out:
            old = sys.stdout
            sys.stdout = out
            try:
                rc = prepr.main(["--repo", str(self.r), "--base", self.base])
            finally:
                sys.stdout = old
            out.seek(0)
            text = out.read()
        self.assertEqual(rc, 0, text)
        self.assertIn("[WARN   ] 10", text)
        self.assertIn("1 warning(s)", text)


class Markdown(unittest.TestCase):
    """DECISION-14 #2: the PR-description summary says what the plain output says, never more."""

    def test_every_item_is_a_row_with_its_status_and_real_rc(self):
        items = [prepr.item(1, "rust fmt", "pass", "", "workspace", "cargo 1", 0, ["cargo", "fmt"]),
                 prepr.item(1, "cargo test", "not_run", "needs --run-tests (take the lease first)", "workspace"),
                 prepr.item(10, "timeout without proc_group", "warn", "a.py:3 uses | pipes\nand lines"),
                 prepr.item(2, "aib test --changed", "fail", "red", rc=1)]
        md = prepr.markdown("origin/main", ["a.py"], items, True)
        self.assertIn("**FAILED**, 1 not run, 1 warning(s) (not_run and warn are not green)", md)
        self.assertIn("| 1 | rust fmt | pass | 0 | workspace |", md)
        self.assertIn("| 1 | cargo test | not_run |  | needs --run-tests (take the lease first); workspace |", md)
        self.assertIn("| 2 | aib test --changed | fail | 1 | red |", md)
        self.assertIn("uses \\| pipes and lines |", md)   # one row: no line break, pipe escaped
        self.assertEqual(len([l for l in md.splitlines() if l.startswith("| ")]), 1 + len(items))
        self.assertIn("not_run and warn are not green.", md)
        # 1ced7 #558 M1: what did not run is in the first line, never a bare "no failures"
        first = prepr.markdown("b", None, items[:2], False).splitlines()[0]
        self.assertIn("**not all run**, 1 not run (not_run and warn are not green)", first)
        self.assertNotIn("no failures", first)
        self.assertIn("**no failures**", prepr.markdown("b", None, items[:1], False))

    def test_md_and_json_cannot_be_asked_together(self):
        with tempfile.TemporaryFile("w+") as err:
            old = sys.stderr
            sys.stderr = err
            try:
                with self.assertRaises(SystemExit) as e:
                    prepr.main(["--md", "--json"])
            finally:
                sys.stderr = old
        self.assertEqual(e.exception.code, 2)


class Cli(unittest.TestCase):
    """Real `aib prepr` on a throwaway git repo: docs-only change, nothing run, every manual item listed."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-prepr-"))
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.td), "EZ2_AIB_LOCALE": "en",
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        g = lambda *a: subprocess.run(["git", "-C", str(self.td / "r")] + list(a), env=self.env, check=True,  # noqa
                                      capture_output=True)
        (self.td / "r").mkdir()
        g("init", "-q", "-b", "main")
        (self.td / "r" / "a.md").write_text("x\n")
        g("add", "a.md")
        g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "1")
        (self.td / "r" / "a.md").write_text("y\n")

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_docs_only_change(self):
        p = subprocess.run(AIB + ["prepr", "--repo", str(self.td / "r"), "--base", "HEAD", "--json"], env=self.env,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        v = json.loads(p.stdout)
        self.assertEqual(v["files"], ["a.md"])
        self.assertEqual({i["status"] for i in v["items"]}, {"not_run"})
        self.assertEqual(sorted({i["item"] for i in v["items"]}), list(range(1, 11)))

    def test_a_new_file_not_yet_added_counts_as_changed(self):
        (self.td / "r" / "new.md").write_text("z\n")
        p = subprocess.run(AIB + ["prepr", "--repo", str(self.td / "r"), "--base", "HEAD", "--json"], env=self.env,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout)["files"], ["a.md", "new.md"])

    def test_a_bad_base_fails(self):
        p = subprocess.run(AIB + ["prepr", "--repo", str(self.td / "r"), "--base", "no-such-ref"], env=self.env,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAILED", p.stdout)


if __name__ == "__main__":
    unittest.main()
