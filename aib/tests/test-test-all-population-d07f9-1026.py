#!/usr/bin/env python3
"""aib test --all 的母體(10-26 @abb89 派;0.1.59 現況):
開源 ez2-aib 倉的 aib 套件住在 aib/ 子目錄,aib test 會先把 --repo 正規化到 git 倉根 ⇒ 倉根不是 aib 套件 ⇒ --all 退回通用找法,
只撈到倉根 tests/ 的 23 支 test_*.py,還把 aib/tools/verify/test_entry.py 當測試跑(rc 2)。暫時繞法:cd aib && aib test --repo . tests/test-*.py。
修:pushgate.all_tests 認得「倉根底下的 aib/ 是 aib 套件」⇒ 回 aib/tests/test-*.py 全套;通用找法跳過 */tools/verify/*。
  P1 --repo 倉根與 --repo aib/ 的母體相同(都是 aib/tests/test-*.py,路徑帶前綴對齊),不含 tools/verify/test_entry.py
  P2 test_entry 自己那層 all_tests(倉根)= 同一份(ledger / --all 都走它)
  P3 真的跑 test_entry.py --all --dry:--repo 倉根 / aib/ 印出的支數一樣、= 母體數
  K1 刀:拿掉「倉根底下的 aib/ 是 aib 套件」那一步 ⇒ P1 紅(退回通用找法:23 支、含 test_entry.py)
"""
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]          # aib/
ROOT = REPO.parent                                   # 倉根(開源版:ez2-aib/)
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "tools" / "verify"))
from aib_proc_guard import run_guarded  # noqa: E402
import pushgate  # noqa: E402
import test_entry as TE  # noqa: E402


def expected():
    return sorted("aib/tests/" + p.name for p in (REPO / "tests").glob("test-*.py"))


class Population(unittest.TestCase):
    def test_p1_root_and_subdir_same_population(self):
        want = expected()
        self.assertGreater(len(want), 200, "母體要是 aib/tests/test-*.py 全套,不是幾支")
        root = pushgate.all_tests(str(ROOT))
        sub = pushgate.all_tests(str(REPO))
        self.assertEqual(root, want, "--repo 倉根要拿到 aib/tests/test-*.py 全套(帶 aib/ 前綴)")
        self.assertEqual(sorted("aib/" + x for x in sub), want, "--repo aib/ 的母體要跟倉根同一份")
        self.assertFalse(any("tools/verify/" in x for x in root), "tools/verify/test_entry.py 不是測試")
        self.assertFalse(any(re.search(r"/test_[a-z_]+\.py$", x) for x in root), "倉根 tests/test_*.py(unittest discover 那批)不在 --all 母體,跟推前檢查同一個母體")

    def test_p2_test_entry_layer_same(self):
        self.assertEqual(TE.all_tests(ROOT), expected())
        self.assertEqual(sorted("aib/" + x for x in TE.all_tests(REPO)), expected())

    def test_p3_dry_run_both_ways(self):
        env = dict(os.environ, EZ2_AIB_STATE_ROOT=os.environ.get("EZ2_AIB_STATE_ROOT") or str(ROOT / ".ez2-test-state"))
        counts = []
        for repo in (ROOT, REPO):
            p = subprocess.run([sys.executable, str(REPO / "tools" / "verify" / "test_entry.py"), "--repo", str(repo), "--all", "--dry"],
                               capture_output=True, text=True, env=env, timeout=120, stdin=subprocess.DEVNULL)
            m = re.search(r"只列不跑:(\d+) 支", p.stdout + p.stderr)
            self.assertIsNotNone(m, "--dry 沒印出支數:\n" + (p.stdout + p.stderr)[-800:])
            counts.append(int(m.group(1)))
        self.assertEqual(counts[0], counts[1], "兩種 --repo 寫法支數要一樣:%r" % counts)
        self.assertEqual(counts[0], len(expected()))


class Knife(unittest.TestCase):
    def test_k1_without_subdir_detection_population_collapses(self):
        """刀落在複本(照 aib_knife_copy 的規矩:不改真樹、離開時驗真樹雜湊沒變);這裡自己抄整個倉根,
        因為要驗的正是「倉根底下有 aib/」這層結構,aib_knife_copy 只抄 aib/ 那層、複本上面沒有倉根。"""
        import hashlib
        import shutil
        import tempfile
        name = Path(__file__).name
        real = REPO / "tools" / "verify" / "pushgate.py"
        real_sha = hashlib.sha256(real.read_bytes()).hexdigest()
        needle = 'sub_aib = sub.is_dir() and ship_profile.load(sub).gate_mode == "aib"'
        with tempfile.TemporaryDirectory(prefix="aib-testall-knife-") as td:
            copy = Path(td) / "repo"
            shutil.copytree(ROOT, copy, symlinks=True, ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules", ".ez2-test-state", "*.noindex"))
            layer = copy / "aib" / "tools" / "verify" / "pushgate.py"
            text = layer.read_text(encoding="utf-8")
            self.assertEqual(text.count(needle), 1, "錨不是恰好出現一次,刀不准落")
            layer.write_text(text.replace(needle, "sub_aib = False", 1), encoding="utf-8")
            r = run_guarded([sys.executable, "-B", str(copy / "aib" / "tests" / name), "Population.test_p1_root_and_subdir_same_population"],
                            cwd=str(copy / "aib"), max_procs=40, timeout=300)
        self.assertEqual(hashlib.sha256(real.read_bytes()).hexdigest(), real_sha, "真樹的 pushgate.py 被動到了")
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)
        self.assertIn("aib/ 前綴", r.stdout + r.stderr, "紅的要是 P1 那格(母體退回通用找法)")


if __name__ == "__main__":
    unittest.main()
