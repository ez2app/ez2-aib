# SPDX-License-Identifier: Apache-2.0
"""`aib prepr`: the pre-PR checklist (DECISION-7 A, 2026-11-08).

Every item is pass / fail / not_run with its reason; not_run is never green; a missing tool or target is reported,
not installed; the Windows cross clippy names the members it leaves out (C builds). Standard library only.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
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

    def test_files_that_aibd_ci_treats_as_rust_trigger_the_rust_checks(self):
        for f in ("migrations/0002_x.sql", "rust-toolchain.toml", "clippy.toml", "scripts/check-core-purity.sh",
                  "crates/aibd/README.md", "xtask/Cargo.toml", ".github/workflows/aibd.yml"):
            self.assertTrue(prepr.kinds([f])["rust"], f)
            self.assertFalse(prepr.kinds([f])["python"], f)
        # the list stays in step with the aibd workflow's own path filter
        wf = ROOT / ".github" / "workflows" / "aibd.yml"
        self.assertTrue(wf.exists(), wf)
        paths = {ln.strip()[3:-1] for ln in wf.read_text().splitlines() if ln.strip().startswith('- "')}
        self.assertTrue(paths)
        for p in paths:
            self.assertTrue(prepr.is_rust(p.replace("**", "x.sql")), p)

    def test_which_checks_apply(self):
        self.assertEqual(prepr.kinds(["a/b.rs"]), {"rust": True, "python": False, "docs_only": False})
        self.assertEqual(prepr.kinds(["x.py", "Cargo.lock"]), {"rust": True, "python": True, "docs_only": False})
        self.assertEqual(prepr.kinds(["README.md"]), {"rust": False, "python": False, "docs_only": True})
        self.assertEqual(prepr.kinds([])["docs_only"], False)


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
        self.assertEqual(sorted({i["item"] for i in v["items"]}), list(range(1, 10)))

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
