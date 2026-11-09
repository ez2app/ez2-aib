#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""背景整理稿記涵蓋 id + rev + 時間、過時看得出來(#552 G3,043dd 11-09)。全在暫存狀態根 + 暫存 HOME 跑。

R1 整理稿每行 id@rev、有「整理那刻」;旁邊 .cover.json 記 id / rev / at。
R2 report_stale:沒動 ⇒ 空;信改過(rev 變)⇒ 列出那一封。
R3 注入那一行:沒動 ⇒ 沒有 ⚠;結案後信又被改 ⇒ 帶「已過時」與 id。
刀(複本 + run_guarded):K1 不寫涵蓋檔 ⇒ R1 紅;K2 永遠不算過時 ⇒ R3 紅。刀一律斷言「FAIL: <目標>」且沒有 ERROR。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

import dm_queue as Q  # noqa: E402
import dm_triage_headless as H  # noqa: E402

SEAT = "aaaaa"
NOW = 1792930000


class R(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="aib-dm-cover-")).resolve()
        self.addCleanup(shutil.rmtree, str(self.d), True)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.d / "state"), "HOME": str(self.d / "home"),
                    "EZ2_AIB_LOCALE": "zh-TW"}
        (self.d / "home").mkdir()
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        self.a = Q.record(SEAT, "通知甲", "agent:offhub", status="delivered", env=self.env, now=NOW)["id"]
        self.b = Q.record(SEAT, "通知乙", "agent:offhub", status="delivered", env=self.env, now=NOW)["id"]
        self.ra = Q.item_rev(Q._load_item(SEAT, self.a, self.env))
        self.rb = Q.item_rev(Q._load_item(SEAT, self.b, self.env))
        self.decisions = [{"id": self.a, "action": "close", "evidence": "x", "why": ""},
                          {"id": self.b, "action": "close", "evidence": "y", "why": ""}]
        self.results = [{"id": self.a, "action": "close", "applied": True, "rev": self.ra, "reason": "ack"},
                        {"id": self.b, "action": "close", "applied": True, "rev": self.rb, "reason": "ack"}]

    def report(self):
        return H._write_report(SEAT, "agent:%s/headless-t" % SEAT, self.decisions, self.results, self.env, NOW, "claude")

    def bump(self, iid):
        it = Q._load_item(SEAT, iid, self.env)
        it["rev"] = Q.item_rev(it) + 1
        Q._write_item(SEAT, it, self.env)

    def test_r1_report_lines_and_cover(self):
        p = self.report()
        text = Path(p).read_text(encoding="utf-8")
        self.assertIn("- %s@%d | close" % (self.a, self.ra), text)
        self.assertIn("整理那刻", text)
        self.assertTrue(H.cover_path(p).is_file(), "整理稿旁沒有涵蓋檔")
        cov = json.loads(H.cover_path(p).read_text(encoding="utf-8"))
        self.assertEqual(cov["schema"], H.COVER_SCHEMA)
        self.assertEqual(cov["at"], NOW)
        self.assertEqual({(i["id"], i["rev"]) for i in cov["items"]}, {(self.a, self.ra), (self.b, self.rb)})

    def test_r2_report_stale(self):
        p = self.report()
        self.assertEqual(H.report_stale(p, self.env)["stale"], [])
        self.bump(self.b)
        r = H.report_stale(p, self.env)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["stale"], [self.b])

    def test_r3_notice_warns_when_stale(self):
        p = self.report()
        H._write_notice(SEAT, p, self.decisions, self.results, self.env, NOW, False)
        line = H.take_notice(SEAT, self.env)
        self.assertIn(self.a, line)
        self.assertNotIn("⚠", line)
        H._write_notice(SEAT, p, self.decisions, self.results, self.env, NOW, False)
        self.bump(self.a)
        line = H.take_notice(SEAT, self.env)
        self.assertIn("已過時", line)
        self.assertIn("⚠ 其中 1 封", line)


KNIVES = [
    ("K1 不寫涵蓋檔", "src/cockpit/dm_triage_headless.py",
     "    _atomic_json(cover_path(path), {",
     "    (lambda *a, **k: None)(cover_path(path), {",
     "R.test_r1_report_lines_and_cover"),
    ("K2 永遠不算過時", "src/cockpit/dm_triage_headless.py",
     '        if cur is None or Q.item_rev(cur) != i["rev"]:',
     "        if False:",
     "R.test_r3_notice_warns_when_stale"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=180)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
