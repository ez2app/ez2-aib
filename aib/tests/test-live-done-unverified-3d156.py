#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib test 即時列印:全部格跳過(沒驗)的那支不得印成「✗ 紅」(2026-10-06 a seat,Mini 推 v0.71.0 第一包被擋的根因)。

形狀:_mark_all_skipped 先把 rc 改 None、unverified 改 True,_live_done 才印;舊寫法只看 rc==0,
於是沒驗的印「✗ 紅 … rc=None」。pushgate --fail-fast 盯的正是 live_done_red 那一行 ⇒ 三份全停(EARLY_RED),
ship.py 再判「只在這次紅」拒推;總表最後雖改成「⊘ 沒驗」,但已經來不及。
量法:注入 live 記錄器;pushgate 自己的 red_line 正則對兩種行各量一次;刀:拔掉 unverified 分支的複本必紅。
"""
import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aib_tmp_root import aib_tmp_root  # noqa: E402
try:  # 10-09 D11:ship.py 會拿機器租約 ⇒ 載 ship 的測試進 24d 母體;本支也進開源版(tools/oss),那邊沒有這支治具也沒有租約
    import aib_host_lease_fixture  # noqa: E402,F401
except ImportError:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENTRY = ROOT / "tools" / "verify" / "test_entry.py"
# 10-08:test_entry 頂端就 import 同資料夾的模組(jsonl_safe…);以前靠家目錄底下剛好有一份私有倉才找得到,換到別台就紅
sys.path.insert(0, str(ENTRY.parent))
GATE = ROOT / "tools" / "verify" / "pushgate.py"
ANCHOR = '    if r.get("unverified"):\n        live("unverified", {"test": r.get("name", "?"), "what": " ".join(r.get("needs") or [])})\n        return\n'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def kinds_for(mod, row):
    seen = []
    mod._live_done(lambda kind, params: seen.append((kind, params)), row)
    return seen


UNVERIFIED = {"name": "tests/test-eye-ui-launch.py", "rc": None, "unverified": True,
              "needs": ["全部 63 格都跳過(rc=0 也不算綠)"], "elapsed_s": 0.1}


class LiveDoneUnverified(unittest.TestCase):
    def setUp(self):
        self.te = load(ENTRY, "te_live_done")

    def test_a_all_skipped_row_is_announced_as_unverified_not_red(self):
        seen = kinds_for(self.te, dict(UNVERIFIED))
        self.assertEqual([k for k, _ in seen], ["unverified"], seen)
        self.assertIn("63 格", seen[0][1]["what"])

    def test_b_green_and_red_rows_unchanged(self):
        self.assertEqual([k for k, _ in kinds_for(self.te, {"name": "t", "rc": 0, "elapsed_s": 1.0})], ["done_green"])
        self.assertEqual([k for k, _ in kinds_for(self.te, {"name": "t", "rc": 1, "elapsed_s": 1.0})], ["done_red"])

    def test_c_pushgate_fail_fast_regex_ignores_the_unverified_line_but_catches_red(self):
        pg = load(GATE, "pg_live_done")
        red_line = pg._line_re("live_done_red")
        unv = self.te.T("live_unverified", {"test": UNVERIFIED["name"], "what": UNVERIFIED["needs"][0]})
        red = self.te.T("live_done_red", {"test": "tests/x.py", "rc": 1, "elapsed": "0.3"})
        self.assertIsNone(red_line.match(unv), "沒驗那一行不得被 fail-fast 當成紅:" + unv)
        self.assertIsNotNone(red_line.match(red), "真的紅那一行仍要被抓到:" + red)

    def test_d_knife_without_the_branch_the_unverified_row_turns_red(self):
        src = ENTRY.read_text(encoding="utf-8")
        self.assertEqual(src.count(ANCHOR), 1, "刀錨要恰好一處")
        with tempfile.TemporaryDirectory(dir=aib_tmp_root()) as td:
            cut_dir = pathlib.Path(td) / "verify"
            shutil.copytree(ENTRY.parent, cut_dir)
            (cut_dir / "test_entry.py").write_text(src.replace(ANCHOR, ""), encoding="utf-8")
            cut = load(cut_dir / "test_entry.py", "te_cut")
            self.assertEqual([k for k, _ in kinds_for(cut, dict(UNVERIFIED))], ["done_red"],
                             "拔掉分支後沒驗的要退回印紅 —— 這就是 10-06 擋推的那一行")


if __name__ == "__main__":
    unittest.main(verbosity=2)
