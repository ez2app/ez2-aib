# SPDX-License-Identifier: Apache-2.0
"""`aib ledger`: one cabin work ledger — states move in transactions, roles do not overlap, acceptance needs receipts
bound to the head (auto-advance v2 item B, 11-08).

Guards (each has a knife in tests/knives/work-ledger.json):
  dispatching the same item twice is refused; accepted without a receipt is refused; a receipt for another SHA is
  refused; plus: stale --from, wrong role, red rc, missing raw output, chrome receipt for another head, event-id replay.
Standard library only.
"""
import hashlib
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
HEAD = "74a815c92e0b1f3a9d6c5b4a3928170615243342"
OTHER = "5708382f4aa1bb2cc3dd4ee5ff60718293a4b5c6"


class AibLedger(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-ledger-"))
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.td), "EZ2_AIB_LOCALE": "en",
                    "AIB_LEDGER_DB": str(self.td / "ledger.sqlite"), "EZ2_AIB_STATE_ROOT": str(self.td / "state")}
        self.n = 0

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def L(self, *args):
        return subprocess.run(AIB + ["ledger"] + list(args), env=self.env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)

    def ok(self, *args):
        r = self.L(*args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def receipt(self, sha=HEAD, kind="positive", rc=0, passed=95, total=95, make_output=True, text=None, **extra):
        self.n += 1
        out = self.td / ("raw-%d.log" % self.n)
        if make_output:
            out.write_text(text if text is not None else "head %s\n足跡預設回歸:%d/%d 綠\n" % (sha[:9], passed, total or passed),
                           encoding="utf-8")
        rec = {"schema": "aib-receipt/1", "kind": kind, "sha": sha, "cmd": "node tools/footprint-default-tests.js",
               "host": "andymini", "started_at": "2026-11-08T21:27:39+08:00", "ended_at": "2026-11-08T21:39:13+08:00",
               "rc": rc, "passed": passed, "total": total, "output": str(out)}
        if make_output:
            rec["output_sha256"] = hashlib.sha256(out.read_bytes()).hexdigest()
        rec.update(extra)
        p = self.td / ("receipt-%d.json" % self.n)
        p.write_text(json.dumps(rec), encoding="utf-8")
        return str(p)

    def roles(self):
        self.ok("role", "set", "dispatch", "f1fae", "--by", "seat:f1fae")
        self.ok("role", "set", "integrate", "a0f9b", "--by", "seat:f1fae")

    def to_review(self, item="B-ledger", needs=()):
        self.roles()
        extra = []
        for n in needs:
            extra += ["--needs", n]
        self.ok("add", item, "--batch", "b114", "--owner", "2240e", "--title", "work ledger", "--by", "seat:f1fae", *extra)
        self.ok("move", item, "--from", "queued", "--to", "running", "--by", "seat:2240e")
        self.ok("move", item, "--from", "running", "--to", "review", "--head", HEAD, "--by", "seat:2240e")

    def accept(self, *evidence, item="B-ledger"):
        args = ["move", item, "--from", "review", "--to", "accepted", "--by", "seat:f1fae"]
        for e in evidence:
            args += ["--evidence", e]
        return self.L(*args)

    def state(self, item="B-ledger"):
        return json.loads(self.ok("show", item, "--json").stdout)["state"]

    # ---------- positive ----------
    def test_full_lifecycle_and_show_list(self):
        self.to_review()
        self.ok("move", "B-ledger", "--from", "review", "--to", "accepted", "--by", "seat:f1fae",
                "--evidence", self.receipt(), "--evidence", self.receipt(kind="knife", passed=8, total=8))
        self.ok("move", "B-ledger", "--from", "accepted", "--to", "integrating", "--by", "seat:a0f9b")
        self.ok("move", "B-ledger", "--from", "integrating", "--to", "deployed", "--by", "seat:a0f9b",
                "--next", "c477a watches 30 min")
        it = json.loads(self.ok("show", "B-ledger", "--json").stdout)
        self.assertEqual((it["state"], it["head"], it["owner"], it["batch"], it["rev"]),
                         ("deployed", HEAD, "2240e", "b114", 6))
        self.assertEqual(len(it["evidence"]), 2)
        self.assertEqual([h["to_state"] for h in it["history"]],
                         ["queued", "running", "review", "accepted", "integrating", "deployed"])
        rows = json.loads(self.ok("list", "--batch", "b114", "--json").stdout)
        self.assertEqual([r["id"] for r in rows], ["B-ledger"])
        self.assertIn("B-ledger", self.ok("list").stdout)
        self.assertEqual(self.ok("verify", "B-ledger").stdout.splitlines()[-1][:9], "RESULT OK")

    def test_same_event_id_twice_is_applied_once(self):
        self.to_review()
        a = self.ok("move", "B-ledger", "--from", "review", "--to", "queued", "--by", "seat:f1fae", "--event-id", "e-1")
        b = self.ok("move", "B-ledger", "--from", "review", "--to", "queued", "--by", "seat:f1fae", "--event-id", "e-1")
        self.assertNotIn("replayed", a.stdout)
        self.assertIn("replayed", b.stdout)
        it = json.loads(self.ok("show", "B-ledger", "--json").stdout)
        self.assertEqual((it["state"], it["rev"]), ("queued", 4))
        r = self.L("move", "B-ledger", "--from", "queued", "--to", "running", "--by", "seat:2240e", "--event-id", "e-1")
        self.assertEqual(r.returncode, 6, r.stdout + r.stderr)

    def test_chrome_receipt_same_head_passes(self):
        self.to_review(needs=("chrome",))
        r = self.accept(self.receipt(), self.receipt(kind="chrome", passed=323, total=323))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # ---------- the three guards f1fae asked for ----------
    def test_dispatching_the_same_item_twice_is_refused(self):
        self.roles()
        self.ok("add", "B-ledger", "--batch", "b114", "--owner", "2240e", "--title", "work ledger", "--by", "seat:f1fae")
        r = self.L("add", "B-ledger", "--batch", "b115", "--owner", "ebb36", "--title", "again", "--by", "seat:f1fae")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("already dispatched", r.stderr)
        it = json.loads(self.ok("show", "B-ledger", "--json").stdout)
        self.assertEqual((it["owner"], it["batch"], it["rev"]), ("2240e", "b114", 1))

    def test_accepted_without_receipt_is_refused(self):
        self.to_review()
        r = self.accept()
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("no receipt", r.stderr)
        self.assertEqual(self.state(), "review")

    def test_receipt_for_another_sha_is_refused(self):
        self.to_review()
        r = self.accept(self.receipt(sha=OTHER))
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("does not match head", r.stderr)
        self.assertEqual(self.state(), "review")
        self.assertEqual(json.loads(self.ok("show", "B-ledger", "--json").stdout)["evidence"], [])

    # ---------- other guards ----------
    def test_stale_from_writes_nothing(self):
        self.to_review()
        r = self.L("move", "B-ledger", "--from", "running", "--to", "review", "--head", OTHER, "--by", "seat:2240e")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertEqual(json.loads(self.ok("show", "B-ledger", "--json").stdout)["head"], HEAD)

    def test_roles_do_not_overlap(self):
        self.to_review()
        r = self.accept(self.receipt())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for seat, frm, to in (("2240e", "accepted", "integrating"), ("f1fae", "accepted", "integrating")):
            r = self.L("move", "B-ledger", "--from", frm, "--to", to, "--by", "seat:" + seat)
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.to_review(item="B-two")
        r = self.L("move", "B-two", "--from", "review", "--to", "accepted", "--by", "seat:2240e",
                   "--evidence", self.receipt())
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        r = self.L("add", "B-three", "--batch", "b114", "--owner", "2240e", "--title", "x", "--by", "seat:2240e")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        r = self.L("role", "set", "dispatch", "2240e", "--by", "seat:2240e")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)

    def test_one_seat_two_roles_must_say_which(self):
        # f1fae 11-08:派工 = f1fae、整合 = f1fae(正本),兩個 session;一席掛兩個角色 ⇒ 寫入要帶 --as
        self.ok("role", "set", "dispatch", "f1fae", "--by", "seat:f1fae")
        self.ok("role", "set", "integrate", "f1fae", "--by", "seat:f1fae")
        r = self.L("add", "B-ledger", "--batch", "b114", "--owner", "2240e", "--title", "w", "--by", "seat:f1fae")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("--as", r.stderr)
        r = self.L("add", "B-ledger", "--batch", "b114", "--owner", "2240e", "--title", "w", "--by", "seat:f1fae",
                   "--as", "integrate")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.ok("add", "B-ledger", "--batch", "b114", "--owner", "2240e", "--title", "w", "--by", "seat:f1fae",
                "--as", "dispatch")
        self.ok("move", "B-ledger", "--from", "queued", "--to", "running", "--by", "seat:2240e")
        self.ok("move", "B-ledger", "--from", "running", "--to", "review", "--head", HEAD, "--by", "seat:2240e")
        r = self.L("move", "B-ledger", "--from", "review", "--to", "accepted", "--by", "seat:f1fae", "--as",
                   "integrate", "--evidence", self.receipt())
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.ok("move", "B-ledger", "--from", "review", "--to", "accepted", "--by", "seat:f1fae", "--as", "dispatch",
                "--evidence", self.receipt())
        r = self.L("move", "B-ledger", "--from", "accepted", "--to", "integrating", "--by", "seat:f1fae",
                   "--as", "dispatch")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.ok("move", "B-ledger", "--from", "accepted", "--to", "integrating", "--by", "seat:f1fae",
                "--as", "integrate")
        r = self.L("move", "B-ledger", "--from", "integrating", "--to", "deployed", "--by", "seat:2240e",
                   "--as", "integrate")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        r = self.L("role", "set", "integrate", "a0f9b", "--by", "seat:f1fae")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        hist = json.loads(self.ok("show", "B-ledger", "--json").stdout)["history"]
        self.assertEqual([h["as"] for h in hist], ["dispatch", None, None, "dispatch", "integrate"])
        self.assertIn("by f1fae as integrate", self.ok("show", "B-ledger").stdout)

    def test_help_says_mis_dispatch_not_impersonation(self):
        out = " ".join(self.ok("--help").stdout.split())
        self.assertIn("guards against mis-dispatch, not impersonation", out)

    def test_bad_receipts_are_refused(self):
        self.to_review()
        cases = {"rc is 1": self.receipt(rc=1),
                 "passed 94 != total 95": self.receipt(passed=94, total=95),
                 "raw output file missing": self.receipt(make_output=False),
                 "output_sha256 does not match": self.receipt(output_sha256="0" * 64),
                 "ended_at is before started_at": self.receipt(ended_at="2026-11-08T20:00:00+08:00"),
                 "host missing": self.receipt(host=""),
                 "output_sha256 missing": self.receipt(output_sha256=""),
                 "total missing": self.receipt(total=None)}
        for want, path in cases.items():
            r = self.accept(path)
            self.assertEqual(r.returncode, 5, want + ": " + r.stdout + r.stderr)
            self.assertIn(want, r.stderr)
        self.assertEqual(self.state(), "review")
        r = self.L("verify", "B-ledger")
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)

    def test_needs_chrome_wants_chrome_receipt_for_same_head(self):
        self.to_review(needs=("chrome",))
        r = self.accept(self.receipt())
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        r = self.accept(self.receipt(), self.receipt(kind="chrome", sha=OTHER))
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("needs chrome", r.stderr)

    def test_not_allowed_move_and_blocker_needed(self):
        self.roles()
        self.ok("add", "B-ledger", "--batch", "b114", "--owner", "2240e", "--title", "w", "--by", "seat:f1fae")
        r = self.L("move", "B-ledger", "--from", "queued", "--to", "accepted", "--by", "seat:f1fae",
                   "--evidence", self.receipt())
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        r = self.L("move", "B-ledger", "--from", "queued", "--to", "blocked-human", "--by", "seat:f1fae")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.ok("move", "B-ledger", "--from", "queued", "--to", "blocked-human", "--by", "seat:f1fae",
                "--blocker", "Andy decides the copy")
        self.assertIn("BLOCKED: Andy decides the copy", self.ok("list").stdout)

    # ---------- ebb36 review of #527 ----------
    def test_h1_rework_with_a_new_head_can_still_be_accepted(self):
        self.to_review()
        self.ok("move", "B-ledger", "--from", "review", "--to", "running", "--by", "seat:f1fae",
                "--evidence", self.receipt())
        self.ok("move", "B-ledger", "--from", "running", "--to", "review", "--head", OTHER, "--by", "seat:2240e")
        it = json.loads(self.ok("show", "B-ledger", "--json").stdout)
        self.assertEqual((it["head"], it["evidence"]), (OTHER, []))
        self.assertEqual([x["head"] for x in it["superseded"]], [HEAD])
        r = self.accept(self.receipt(sha=OTHER))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("superseded (old head", self.ok("show", "B-ledger").stdout)

    def test_h2_owner_cannot_release_blocked_human(self):
        self.to_review()
        self.ok("move", "B-ledger", "--from", "review", "--to", "blocked-human", "--by", "seat:f1fae",
                "--blocker", "waits for Andy")
        for to in ("running", "queued"):
            r = self.L("move", "B-ledger", "--from", "blocked-human", "--to", to, "--by", "seat:2240e")
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(self.state(), "blocked-human")
        self.ok("move", "B-ledger", "--from", "blocked-human", "--to", "running", "--by", "seat:f1fae")

    def test_h3_any_file_as_raw_output_is_refused(self):
        self.to_review()
        hosts = self.td / "hosts"
        hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
        h = hashlib.sha256(hosts.read_bytes()).hexdigest()
        cases = {"raw output does not mention the head": self.receipt(make_output=False, output=str(hosts), output_sha256=h),
                 "no result line matching 95/95": self.receipt(text="head %s\nall fine\n" % HEAD[:9]),
                 "no result line matching 1/1": self.receipt(passed=1, total=1, text="head %s\n95/95\n" % HEAD[:9])}
        for want, path in cases.items():
            r = self.accept(path)
            self.assertEqual(r.returncode, 5, want + ": " + r.stdout + r.stderr)
            self.assertIn(want, r.stderr)
        r = self.accept(self.receipt(text="head %s\nRan 7 tests in 0.1s\n\nOK\n" % HEAD[:9], passed=7, total=7))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_m1_owner_cannot_accept_own_work(self):
        self.ok("role", "set", "dispatch", "f1fae", "--by", "seat:f1fae")
        self.ok("add", "B-self", "--batch", "b114", "--owner", "f1fae", "--title", "own", "--by", "seat:f1fae")
        self.ok("move", "B-self", "--from", "queued", "--to", "running", "--by", "seat:f1fae")
        self.ok("move", "B-self", "--from", "running", "--to", "review", "--head", HEAD, "--by", "seat:f1fae")
        r = self.accept(self.receipt(), item="B-self")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("cannot accept its own work", r.stderr)

    def test_m2_dispatch_must_be_set_first(self):
        r = self.L("role", "set", "integrate", "x9", "--by", "seat:x9")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("dispatch seat first", r.stderr)
        self.assertIn("no roles set", self.ok("role", "list").stdout)

    def test_m3_acceptance_keeps_receipt_hashes(self):
        self.to_review()
        rp = self.receipt()
        self.ok("move", "B-ledger", "--from", "review", "--to", "accepted", "--by", "seat:f1fae", "--evidence", rp)
        hist = json.loads(self.ok("show", "B-ledger", "--json").stdout)["history"]
        v = [h for h in hist if h["to_state"] == "accepted"][0]["verified"][0]
        rec = json.loads(Path(rp).read_text(encoding="utf-8"))
        self.assertEqual((v["receipt_sha256"], v["output_sha256"]),
                         (hashlib.sha256(Path(rp).read_bytes()).hexdigest(), rec["output_sha256"]))

    def test_m4_head_checked_against_git_and_commit_time(self):
        d = self.td / "repo"
        d.mkdir()
        g = ["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@example.com"]
        (d / "f").write_text("x\n", encoding="utf-8")
        for c in (["init", "-q"], ["add", "f"], ["commit", "-q", "-m", "x"]):
            subprocess.run(g + c, check=True, capture_output=True, env=self.env)
        sha = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True,
                             env=self.env).stdout.strip()
        self.roles()
        self.ok("add", "B-git", "--batch", "b114", "--owner", "2240e", "--title", "g", "--by", "seat:f1fae")
        self.ok("move", "B-git", "--from", "queued", "--to", "running", "--by", "seat:2240e")
        r = self.L("move", "B-git", "--from", "running", "--to", "review", "--head", OTHER, "--repo", str(d),
                   "--by", "seat:2240e")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.ok("move", "B-git", "--from", "running", "--to", "review", "--head", sha[:9], "--repo", str(d),
                "--by", "seat:2240e")
        it = json.loads(self.ok("show", "B-git", "--json").stdout)
        self.assertEqual(it["head"], sha)
        self.assertTrue(it["head_time"])
        r = self.accept(self.receipt(sha=sha, started_at="2020-01-01T00:00:00+00:00"), item="B-git")
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("started_at is before the head commit", r.stderr)

    def test_l1_knife_receipt_alone_is_not_enough(self):
        self.to_review()
        r = self.accept(self.receipt(kind="knife", passed=8, total=8))
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("no passing positive or default receipt", r.stderr)

    def test_l6_ledger_files_are_private(self):
        # -wal / -shm 只在連線開著時存在(關乾淨會刪)⇒ 同一個行程裡開著看;SQLite 照主檔權限建它們
        import importlib.util
        spec = importlib.util.spec_from_file_location("work_ledger_t", str(ROOT / "aib" / "tools" / "governance" /
                                                                          "work_ledger.py"))
        wl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wl)
        old = os.umask(0o022)
        try:
            f = self.td / "priv.sqlite"
            a = wl.connect(f)
            a.execute("INSERT INTO roles(role, seat, set_by, set_at) VALUES ('dispatch', 'x', 'x', 'x')")
            b = wl.connect(f)
            try:
                for side in ("", "-wal", "-shm"):
                    g = Path(str(f) + side)
                    self.assertTrue(g.exists(), side)
                    self.assertEqual(oct(g.stat().st_mode & 0o777), "0o600", side)
            finally:
                b.close()
                a.close()
        finally:
            os.umask(old)

    def test_read_commands_on_empty_ledger(self):
        self.assertIn("no items", self.ok("list").stdout)
        self.assertEqual(self.L("show", "nope").returncode, 3)
        self.assertEqual(self.L().returncode, 2)


if __name__ == "__main__":
    unittest.main()
