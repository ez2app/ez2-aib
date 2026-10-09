#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""影子續跑索引損壞 ⇒ 一律不沿用;修復工具把壞行搬走並留「必跑」墓碑(本地 AI 包 + 雲端補測,the maintainer 09-28 核准合併)。

D1 四種損壞(寫一半、缺 inputs、非物件、壞 JSON)⇒ 整份索引 degraded,decide 回 must-run
D2 乾淨索引不 degraded
D3 quarantine 預設乾跑:檔案一個位元組都不動
D4 --apply:壞行進 .bad、該測試留墓碑 ⇒ 索引恢復可用,但那支測試必跑一次(不會拿更舊的綠來沿用)
D5 壞行認不出是哪支測試 ⇒ 檔內每支測試都留墓碑(全部重跑一次)
D6 修的時候檔案被別人追加 ⇒ 不改寫,回報 changed
D7 CLI:reuse_index quarantine --rows F --apply
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "verify"))
import reuse_index as ri  # noqa: E402

GREEN_A = {"test": "tests/test-a.py", "rc": 0, "inputs": [], "input_fp": "x", "env_fp": "y", "key": "k",
           "measured": True, "external": False}
GREEN_B = dict(GREEN_A, test="tests/test-b.py")
RED_A_HALF = '{"test": "tests/test-a.py", "rc": 1, "inp'


class Damage(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.f = Path(self._t.name) / "x.rows.jsonl"

    def tearDown(self):
        self._t.cleanup()

    def write(self, *lines):
        self.f.write_text("".join((l if isinstance(l, str) else json.dumps(l)) + "\n" for l in lines),
                          encoding="utf-8")

    def test_D1_each_damage_kind_blocks_reuse(self):
        for bad in (RED_A_HALF, json.dumps({"test": "tests/test-a.py", "rc": 1}), "[1, 2]", "{not json"):
            with self.subTest(bad=bad[:20]):
                self.write(GREEN_A, bad)
                idx = ri.build_index([str(self.f)])
                self.assertTrue(idx.degraded.startswith("bad-jsonl:"), idx.degraded)
                self.assertEqual(idx.decide(self._t.name, "tests/test-b.py")[0], "must-run")

    def test_D2_clean_index_is_usable(self):
        self.write(GREEN_A, GREEN_B)
        self.assertFalse(ri.build_index([str(self.f)]).degraded)

    def test_D3_dry_run_changes_nothing(self):
        self.write(GREEN_A, RED_A_HALF)
        before = self.f.read_bytes()
        rep = ri.quarantine(self.f)
        self.assertEqual((rep["bad_lines"], rep["tombstones"], rep["applied"]), ([2], ["tests/test-a.py"], False))
        self.assertEqual(self.f.read_bytes(), before)
        self.assertFalse(Path(str(self.f) + ".bad").exists())

    def test_D4_apply_leaves_tombstone_so_older_green_is_not_reused(self):
        self.write(GREEN_A, GREEN_B, RED_A_HALF)
        rep = ri.quarantine(self.f, apply=True)
        self.assertTrue(rep["applied"])
        idx = ri.build_index([str(self.f)])
        self.assertFalse(idx.degraded, "修好後索引要能用")
        verdict, reason, _ = idx.decide(self._t.name, "tests/test-a.py")
        self.assertEqual((verdict, reason), ("must-run", "unmeasured"), "壞掉的那一行可能是最新的紅 ⇒ 必跑")
        self.assertIs(idx.latest["tests/test-b.py"]["measured"], True, "沒壞的測試不受影響")
        bad = [json.loads(l) for l in Path(str(self.f) + ".bad").read_text(encoding="utf-8").splitlines()]
        self.assertEqual((bad[0]["line"], bad[0]["raw"]), (3, RED_A_HALF), "壞行原文要留著")

    def test_D5_unreadable_test_name_tombstones_everything(self):
        self.write(GREEN_A, GREEN_B, "{garbage")
        rep = ri.quarantine(self.f, apply=True)
        self.assertTrue(rep["unknown_test_in_bad_line"])
        idx = ri.build_index([str(self.f)])
        for t in ("tests/test-a.py", "tests/test-b.py"):
            self.assertEqual(idx.decide(self._t.name, t)[:2], ("must-run", "unmeasured"), t)

    def test_D6_file_growing_during_repair_is_not_rewritten(self):
        self.write(GREEN_A, RED_A_HALF)
        real = Path.read_text

        def read_then_someone_appends(p, *a, **kw):
            out = real(p, *a, **kw)
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(GREEN_B) + "\n")
            return out
        with mock.patch.object(Path, "read_text", read_then_someone_appends):
            rep = ri.quarantine(self.f, apply=True)
        self.assertEqual((rep["changed"], rep["applied"]), (True, False))
        self.assertIn("test-b", self.f.read_text(encoding="utf-8"), "別人剛寫的那一列不能被蓋掉")

    def test_D7_cli(self):
        self.write(GREEN_A, RED_A_HALF)
        p = subprocess.run([sys.executable, str(REPO / "tools" / "verify" / "reuse_index.py"), "quarantine",
                            "--rows", str(self.f), "--apply"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(json.loads(p.stdout)[0]["applied"])


if __name__ == "__main__":
    unittest.main()
