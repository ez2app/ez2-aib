#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""dm_queue read / done 好用一點(PROPOSAL-gaps v3 第 6 件 + B;@cc203 @977e3 @ee0bb「done 要撞五次參數錯才過」)。

D1 --seat / --by 不帶 ⇒ 用呼叫的那一席(seat_who 量到的);量不到 ⇒ rc 2、訊息給一整行例子(不猜)
D2 --by 不帶 ⇒ seat:<--seat>
D3 沒帶 --if-rev 照舊拒(艦長 07:06 裁 fail-closed,不自動帶現值),但回 "try":一整行可直接複製的正確指令(帶現在的 rev);
   照它跑就過
D4 done 沒證據 ⇒ "try" 帶 --why 範例
D5 證據認 github.com/ez2app 的 PR / commit 網址(kind=pr);別的網址不認
D6 dm-ctl 在 cron 的最小 PATH 找不到 tmux ⇒ 補上 Homebrew 位置;已經找得到 / 備援位置也沒有 ⇒ PATH 不動
刀(複本):K1 --by 不補預設 ⇒ D2 紅
"""
import io
import json
import shlex
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(HERE))
import dm_queue as Q  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

SEAT = "abcde"
PR = "https://github.com/ez2app/ez2-aib/pull/233"


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-dmcli-")
        self.addCleanup(td.cleanup)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "EZ2_AIB_LOCALE": "zh-TW", "AIB_DM_ADDR": "100.114.152.121"}
        self.me = SEAT
        orig = Q.self_seat_code
        Q.self_seat_code = lambda: self.me
        self.addCleanup(setattr, Q, "self_seat_code", orig)

    def item(self, text="hi"):
        return Q.enqueue(SEAT, text, "寄件席", env=self.env)

    def cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = Q.main(list(argv), env=self.env)
        return rc, json.loads(buf.getvalue().strip().splitlines()[-1])

    def status(self, iid):
        return Q._load_item(SEAT, iid, self.env)["status"]


class Cli(Base):
    def test_d1_seat_default(self):
        it = self.item()
        rc, out = self.cli("read", "--id", it["id"], "--if-rev", str(Q.item_rev(it)))
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.status(it["id"]), "read")
        self.me = None
        it = self.item()
        rc, out = self.cli("read", "--id", it["id"], "--if-rev", "1")
        self.assertEqual((rc, out["reason"]), (2, "usage"))
        self.assertIn("--seat <席碼> --id %s" % it["id"], out["message"])
        self.assertEqual(self.status(it["id"]), "queued", "量不到不猜")

    def test_d2_by_default(self):
        it = self.item()
        rc, out = self.cli("read", "--seat", SEAT, "--id", it["id"], "--if-rev", str(Q.item_rev(it)))
        self.assertEqual(rc, 0, out)
        audit = [json.loads(l) for l in (Path(self.env["EZ2_AIB_STATE_ROOT"]).rglob("audit.jsonl")).__next__().read_text().splitlines()]
        self.assertIn("seat:" + SEAT, json.dumps(audit[-1], ensure_ascii=False))

    def test_d3_no_rev_try(self):
        it = self.item()
        rc, out = self.cli("read", "--id", it["id"])
        self.assertEqual((rc, out["reason"]), (6, "no_rev"))
        self.assertEqual(self.status(it["id"]), "queued", "照舊 fail-closed")
        argv = shlex.split(out["try"])
        self.assertEqual(argv[:3], ["python3", "dm_queue.py", "read"])
        self.assertIn("--if-rev %d" % Q.item_rev(it), out["try"])
        rc, out = self.cli(*argv[2:])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.status(it["id"]), "read")

    def test_d4_done_why_try(self):
        it = self.item()
        rc, out = self.cli("read", "--id", it["id"], "--if-rev", str(Q.item_rev(it)))
        rc, out = self.cli("done", "--id", it["id"], "--if-rev", str(Q.item_rev(Q._load_item(SEAT, it["id"], self.env))))
        self.assertEqual(out["reason"], "evidence")
        self.assertIn("--why", out["try"])
        argv = shlex.split(out["try"].replace("<一句結案理由>", "回過了"))
        rc, out = self.cli(*argv[2:])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.status(it["id"]), "done")

    def test_d5_pr_evidence(self):
        self.assertEqual(Q.evidence_kind(SEAT, PR, self.env), "pr")
        self.assertEqual(Q.evidence_kind(SEAT, "https://github.com/ez2app/aib-plugins/commit/9c5c2c2", self.env), "pr")
        for bad in ("https://github.com/evil/x/pull/1", "https://example.com/pull/1", "http://github.com/ez2app/a/pull/1",
                    PR + "?x=1"):
            self.assertIsNone(Q.evidence_kind(SEAT, bad, self.env), bad)
        it = self.item()
        rc, out = self.cli("read", "--done", "--id", it["id"], "--if-rev", str(Q.item_rev(it)), "--evidence", PR)
        self.assertEqual(rc, 0, out)
        self.assertEqual(Q._load_item(SEAT, it["id"], self.env).get("done_kind"), "pr")


class CronPath(unittest.TestCase):
    def stanza(self):
        src = (REPO / "src" / "cockpit" / "dm-ctl.sh").read_text(encoding="utf-8")
        i = src.index("if ! command -v tmux >/dev/null 2>&1; then")
        return src[i:src.index("\nfi\n", i) + 4]

    def run_sh(self, path, dirs):
        import subprocess
        r = subprocess.run(["/bin/bash", "-c", self.stanza() + 'printf "%s|%s" "$PATH" "$(command -v tmux || true)"'],
                           env={"PATH": path, "AIB_TMUX_FALLBACK_DIRS": dirs}, capture_output=True, text=True, timeout=30)
        return r.stdout.split("|")

    def test_d6_cron_path(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            fake = Path(b) / "tmux"
            fake.write_text("#!/bin/sh\nexit 0\n")
            fake.chmod(0o755)
            path, found = self.run_sh("/usr/bin:/bin", "%s %s" % (a, b))
            self.assertEqual((path, found), ("%s:/usr/bin:/bin" % b, str(fake)), "補上有 tmux 的那個")
            path, found = self.run_sh("/usr/bin:/bin", a)
            self.assertEqual(path, "/usr/bin:/bin", "備援也沒有 ⇒ 不動")
            path, found = self.run_sh("%s:/usr/bin:/bin" % b, a)
            self.assertEqual(path, "%s:/usr/bin:/bin" % b, "已經找得到 ⇒ 不動")


class Knife(unittest.TestCase):
    def test_k1_no_by_default(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/dm_queue.py", '        if not args.by:\n            args.by = "seat:" + args.seat\n', "",
                        tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Cli.test_d2_by_default"], cwd=str(root),
                            max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
