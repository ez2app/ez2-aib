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

    # ---------- aib ledger record ----------
    def repo(self, test_body="        self.assertTrue(True)\n"):
        d = self.td / "proj"
        d.mkdir(exist_ok=True)
        (d / "test_x.py").write_text("import unittest\n\nclass T(unittest.TestCase):\n    def test_a(self):\n"
                                     + test_body + "    def test_b(self):\n        pass\n", encoding="utf-8")
        g = ["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@example.com"]
        for c in (["init", "-q"], ["add", "test_x.py"], ["commit", "-q", "-m", "x"]):
            subprocess.run(g + c, check=True, capture_output=True, env=self.env)
        sha = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True,
                             env=self.env).stdout.strip()
        return d, sha

    def record(self, tree, *cmd, kind="positive"):
        r = self.L("record", "--kind", kind, "--tree", str(tree), "--out", str(self.td / "rcpt"), "--", *cmd)
        last = (r.stdout.strip().splitlines() or [""])[-1]
        path = last.split(" ", 2)[1] if last.startswith("RECEIPT ") else None
        return r, path

    def review_at(self, sha, item="B-rec", needs=()):
        if not self.L("role", "list", "--json").stdout.strip().startswith("[{"):
            self.roles()
        extra = []
        for n in needs:
            extra += ["--needs", n]
        self.ok("add", item, "--batch", "b114", "--owner", "2240e", "--title", "rec", "--by", "seat:f1fae", *extra)
        self.ok("move", item, "--from", "queued", "--to", "running", "--by", "seat:2240e")
        self.ok("move", item, "--from", "running", "--to", "review", "--head", sha, "--by", "seat:2240e")

    def test_record_writes_a_receipt_that_verifies(self):
        d, sha = self.repo()
        r, path = self.record(d, sys.executable, "-m", "unittest", "test_x")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual((rec["sha"], rec["rc"], rec["passed"], rec["total"], rec["producer"], rec["dirty"]),
                         (sha, 0, 2, 2, "aib ledger record", False))
        self.assertTrue(Path(rec["output"]).is_file())
        self.review_at(sha, needs=("recorded",))
        r = self.L("move", "B-rec", "--from", "review", "--to", "accepted", "--by", "seat:f1fae", "--evidence", path)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_record_keeps_the_real_rc_and_counts_failures(self):
        d, sha = self.repo(test_body="        self.fail('red')\n")
        r, path = self.record(d, sys.executable, "-m", "unittest", "test_x")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual((rec["rc"], rec["passed"], rec["total"]), (1, 1, 2))
        self.review_at(sha)
        r = self.L("move", "B-rec", "--from", "review", "--to", "accepted", "--by", "seat:f1fae", "--evidence", path)
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)

    def test_hand_written_receipt_cannot_pass_as_recorded(self):
        d, sha = self.repo()
        self.review_at(sha)
        fake = self.receipt(sha=sha, producer="aib ledger record")
        r = self.L("move", "B-rec", "--from", "review", "--to", "accepted", "--by", "seat:f1fae", "--evidence", fake)
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("not in the ledger's record registry", r.stderr)

    def test_needs_recorded_refuses_a_hand_written_receipt(self):
        d, sha = self.repo()
        self.review_at(sha, needs=("recorded",))
        r = self.L("move", "B-rec", "--from", "review", "--to", "accepted", "--by", "seat:f1fae",
                   "--evidence", self.receipt(sha=sha))
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("needs recorded", r.stderr)

    def test_recorded_receipt_or_output_edited_afterwards_is_refused(self):
        d, sha = self.repo(test_body="        self.fail('red')\n")
        r, path = self.record(d, sys.executable, "-m", "unittest", "test_x")
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
        rec.update(rc=0, passed=2)
        Path(path).write_text(json.dumps(rec), encoding="utf-8")
        self.review_at(sha)
        r = self.L("move", "B-rec", "--from", "review", "--to", "accepted", "--by", "seat:f1fae", "--evidence", path)
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("receipt was changed after aib ledger record wrote it", r.stderr)
        d2 = self.td / "p2"
        r2 = self.L("record", "--tree", str(d), "--out", str(d2), "--", sys.executable, "-c", "print('3/3 ok')")
        p2 = r2.stdout.strip().splitlines()[-1].split(" ", 2)[1]
        out = Path(json.loads(Path(p2).read_text(encoding="utf-8"))["output"])
        out.write_text("3/3 ok, edited\n", encoding="utf-8")
        self.ok("move", "B-rec", "--from", "review", "--to", "queued", "--by", "seat:f1fae")
        self.ok("move", "B-rec", "--from", "queued", "--to", "running", "--by", "seat:2240e")
        self.ok("move", "B-rec", "--from", "running", "--to", "review", "--head", sha, "--by", "seat:2240e")
        r = self.L("move", "B-rec", "--from", "review", "--to", "accepted", "--by", "seat:f1fae", "--evidence", p2)
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("raw output was changed after aib ledger record wrote it", r.stderr)

    def test_record_refuses_a_dirty_tree(self):
        d, sha = self.repo()
        (d / "test_x.py").write_text("changed\n", encoding="utf-8")
        r, path = self.record(d, sys.executable, "-c", "print('1/1')")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIsNone(path)
        self.assertIn("uncommitted changes", r.stderr)
        self.assertFalse((self.td / "rcpt").exists() and any((self.td / "rcpt").iterdir()))

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

    # ---------- ebb36 r2:派工記指令、沒追蹤的檔、日期不算支數 ----------
    def test_r2_m1_receipt_must_run_the_dispatched_command(self):
        d, sha = self.repo()
        self.roles()
        want = "%s -m unittest test_x" % sys.executable
        for item in ("B-cmd", "B-fake"):
            self.ok("add", item, "--batch", "b114", "--owner", "2240e", "--title", "c", "--cmd", want,
                    "--by", "seat:f1fae")
            self.ok("move", item, "--from", "queued", "--to", "running", "--by", "seat:2240e")
            self.ok("move", item, "--from", "running", "--to", "review", "--head", sha, "--by", "seat:2240e")
        r, real = self.record(d, sys.executable, "-m", "unittest", "test_x")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.L("move", "B-cmd", "--from", "review", "--to", "accepted", "--by", "seat:f1fae", "--evidence", real)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r, fake = self.record(d, "sh", "-c", 'printf "Ran 400 tests in 0.1s\\n\\nOK\\n"')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(Path(fake).read_text(encoding="utf-8"))["passed"], 400)
        r = self.L("move", "B-fake", "--from", "review", "--to", "accepted", "--by", "seat:f1fae", "--evidence", fake)
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("is not the dispatched command", r.stderr)
        self.assertIn("cmd:", self.ok("show", "B-fake").stdout)
        self.assertIn("cmd: %s -m unittest test_x" % sys.executable, self.ok("verify", "B-cmd").stdout)

    def test_r2_l1_untracked_file_blocks_record(self):
        d, sha = self.repo()
        (d / "sitecustomize.py").write_text("import unittest\nunittest.TestCase.assertEqual = lambda *a: None\n",
                                            encoding="utf-8")
        r, path = self.record(d, sys.executable, "-m", "unittest", "test_x")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIsNone(path)

    def test_r2_l3_a_date_is_not_a_pass_count(self):
        d, sha = self.repo()
        r, path = self.record(d, sys.executable, "-c", "print('built 11/08')")
        self.assertEqual(json.loads(Path(path).read_text(encoding="utf-8"))["passed"], None)
        r, path = self.record(d, sys.executable, "-c", "print('PASS 5/5 綠'); print('built 11/08')")
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual((rec["passed"], rec["total"]), (5, 5))

    def test_read_commands_on_empty_ledger(self):
        self.assertIn("no items", self.ok("list").stdout)
        self.assertEqual(self.L("show", "nope").returncode, 3)
        self.assertEqual(self.L().returncode, 2)


if __name__ == "__main__":
    unittest.main()
