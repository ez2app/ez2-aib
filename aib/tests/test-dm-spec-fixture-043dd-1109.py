#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""1.x dm 行為對照表(Rust 版 dm 第 0 步,043dd 11-09;Andy「照你的建議，先做第 0 步」)。

S1 現在的 dm_queue 重產一份 = 倉裡的 tests/fixtures/dm-spec/transitions-v1.json(一格都不差)。
   不一樣 ⇒ dm 行為變了:退步就修;故意改規則就 `python3 tools/verify/dm_spec.py --write` 重產、PR 寫為什麼。
S2 對照表自己說得通:TRANSITIONS 每一列(起點, 終點, 操作者類別)至少有一格准;准的格一定在 TRANSITIONS 上。
S3 rev 檢查 fail-closed:沒帶 rev ⇒ no_rev、錯 / 亂 ⇒ rev_mismatch、不在 / 墓碑 ⇒ not_found。
刀(複本 + run_guarded):K1 TRANSITIONS 刪一列 ⇒ S1 紅;K2 結案沒證據改回別的代碼 ⇒ S1 紅;
K3 系統代標已讀不查理由 ⇒ S1 紅。刀一律斷言「FAIL: <目標>」且沒有 ERROR。
"""
import importlib.util
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

_spec = importlib.util.spec_from_file_location("dm_spec", ROOT / "tools" / "verify" / "dm_spec.py")
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)

ACTOR_CLASS = {"system": "system", "seat:self": "seat", "human": "human", "human:from-seat": "human",
               "agent:self": "agent"}


class S(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = D.load(D.FIXTURE)
        cls.new = D.build()

    def test_s1_matches_fixture(self):
        shown, n = D.diff(self.old, self.new)
        self.assertEqual(n, 0, "dm 行為和對照表差 %d 格,前幾格:\n%s" % (n, "\n".join(map(str, shown))))

    def test_s2_fixture_consistent(self):
        table = {(f, t): set(cls) for f, t, cls in self.old["transitions"]}
        seen = {}
        for c in self.old["cells"]:
            if not c["ok"]:
                continue
            cls = ACTOR_CLASS.get(c["actor"])
            self.assertIn(cls, table.get((c["from"], c["to"]), set()), "表外的格被准了:%r" % c)
            seen.setdefault((c["from"], c["to"]), set()).add(cls)
        for k, cls in table.items():
            self.assertEqual(seen.get(k, set()), cls, "TRANSITIONS %r 有類別一格都沒准到" % (k,))

    def test_s3_rev_fail_closed(self):
        got = {r["case"]: r["reason"] for r in self.old["rev_cases"]}
        self.assertEqual(got, {"no_rev": "no_rev", "match": "ok", "mismatch": "rev_mismatch", "garbage": "rev_mismatch",
                               "missing": "not_found", "tombstone-no-rev-check": "not_found"})


KNIVES = [
    ("K1 TRANSITIONS 刪一列", "src/cockpit/dm_queue.py",
     '    ("queued", "claimed"): ("system",),\n', "",
     "S.test_s1_matches_fixture"),
    ("K2 結案沒證據換代碼", "src/cockpit/dm_queue.py",
     '                return refuse("evidence")', '                return refuse("transition")',
     "S.test_s1_matches_fixture"),
    ("K3 系統代標已讀不查理由", "src/cockpit/dm_queue.py",
     'if to == "read" and cls == "system" and why not in SYSTEM_READ_WHYS:',
     'if to == "read" and cls == "system" and False:',
     "S.test_s1_matches_fixture"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
                    env = dict(os.environ, AIB_DM_SPEC_FIXTURE=str(D.FIXTURE))
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=300, env=env)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out[-3000:]))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out[-3000:]))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out[-3000:]))


if __name__ == "__main__":
    unittest.main()
