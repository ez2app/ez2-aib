#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""暫存路徑統一正規化成 <TMP>(043dd 11-09;Andy 18:1x 准,a63ab 派、a9136 審)。

helper 在 aib/tools/verify/tmp_paths.py,測試從 aib_tmp_root 匯入同一組名字;row_export 的 _roots / _sub_root 改成用它。
只在自己建的暫存資料夾裡做事,不碰工作樹、不碰 ~/.ez2-aib。

T1 forms:abspath + realpath、長的在前、不重複;空字串回空。
T2 symlink 根:給的是連結,文字裡是 realpath ⇒ 也換成 <TMP>(每個平台都咬得到 realpath)。
T3 macOS:tempfile 給的 /var/folders/… 和 resolve 後的 /private/var/… 兩種都換(只在 macOS 跑)。
T4 邊界:根後面接英數 . _ - 不換(<根>x 不是 <根>);接 / 或結尾才換;反斜線也算邊界。
T5 長的先換:自己的資料夾在系統暫存根底下 ⇒ 換成 <TMP>/… 而不是 <TMP>/<隨機名>/…。
T6 不留狀態:前一次呼叫帶的根,下一次不會再換(#570 a9136 N1)。
T7 row_export 的 _roots / _sub_root 就是 helper(行為一字不差)。
T8 aib_tmp_root 匯出同一組名字。
T9 dm_spec 不再有模組層級 TMP_ROOTS,每次呼叫自己帶 roots(#570 a9136 N1);含暫存路徑的短字串記 <str>。
刀(複本 + run_guarded):K1 拿掉 realpath ⇒ T2 紅(macOS 另外 T3 紅);K2 改成短的先 ⇒ T5 紅;K3 拿掉邊界 ⇒ T4 紅。
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "verify"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

import aib_tmp_root  # noqa: E402
import row_export  # noqa: E402
import tmp_paths as TP  # noqa: E402


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="aib-tmp-paths-")
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_t1_forms(self):
        f = TP.forms(self.d)
        self.assertIn(os.path.abspath(self.d), f)
        self.assertIn(os.path.realpath(self.d), f)
        self.assertEqual(f, sorted(set(f), key=len, reverse=True))
        self.assertEqual(TP.forms(""), [])

    def test_t2_symlink_root_realpath_is_normalized(self):
        real = os.path.join(self.d, "real")
        os.mkdir(real)
        link = os.path.join(self.d, "link")
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError) as e:      # Windows 沒權限建連結
            self.skipTest("cannot create a symlink here: %s" % e)
        text = "saw %s/x and %s/y" % (os.path.realpath(real), link)
        self.assertEqual(TP.norm_tmp(text, link), "saw <TMP>/x and <TMP>/y")

    @unittest.skipUnless(sys.platform == "darwin", "macOS only: /var/folders vs /private/var/folders")
    def test_t3_macos_var_folders_both_forms(self):
        given = self.d                                     # /var/folders/…
        resolved = os.path.realpath(self.d)                # /private/var/folders/…
        self.assertNotEqual(given, resolved)
        out = TP.norm_tmp("%s/a %s/b" % (given, resolved), self.d)
        self.assertEqual(out, "<TMP>/a <TMP>/b")

    def test_t4_boundary(self):
        r = os.path.realpath(self.d)
        self.assertEqual(TP.sub_root(r + "x/y", [r], "<TMP>"), r + "x/y")
        self.assertEqual(TP.sub_root(r + "_y " + r + ".z", [r], "<TMP>"), r + "_y " + r + ".z")
        self.assertEqual(TP.sub_root(r + "/y", [r], "<TMP>"), "<TMP>/y")
        self.assertEqual(TP.sub_root("at " + r, [r], "<TMP>"), "at <TMP>")
        self.assertEqual(TP.sub_root("C:\\T\\x", ["C:\\T"], "<TMP>"), "<TMP>\\x")

    def test_t5_own_dir_before_system_root(self):
        sub = os.path.join(self.d, "run-1234abcd")
        os.mkdir(sub)
        out = TP.norm_tmp(os.path.realpath(sub) + "/evidence.txt", sub)
        self.assertEqual(out, "<TMP>/evidence.txt")

    def test_t6_no_state_between_calls(self):
        other = tempfile.mkdtemp(prefix="aib-tmp-other-")
        self.addCleanup(shutil.rmtree, other, True)
        TP.norm_tmp("x", self.d)
        out = TP.norm_tmp(os.path.realpath(self.d) + "/f", other)
        self.assertEqual(out, "<TMP>/%s/f" % os.path.basename(self.d))   # 只剩系統暫存根被換

    def test_t7_row_export_uses_the_helper(self):
        self.assertIs(row_export._roots, TP.forms)
        self.assertIs(row_export._sub_root, TP.sub_root)

    def test_t8_aib_tmp_root_exports(self):
        self.assertIs(aib_tmp_root.norm_tmp, TP.norm_tmp)
        self.assertIs(aib_tmp_root.tmp_forms, TP.forms)
        self.assertEqual(aib_tmp_root.TMP_TAG, "<TMP>")

    def test_t9_dm_spec_takes_roots_per_call(self):
        import dm_spec
        self.assertFalse(hasattr(dm_spec, "TMP_ROOTS"), "dm_spec 又有模組層級的 TMP_ROOTS(#570 N1)")
        roots = TP.forms(self.d)
        short = os.path.join(os.path.realpath(self.d), "e.txt")     # Linux 上常常不到 40 字
        self.assertEqual(dm_spec._norm(short, None, roots), "<str>")
        self.assertEqual(dm_spec._norm("看過了", None, roots), "看過了")


REL = "tools/verify/tmp_paths.py"
KNIVES = [
    ("K1-no-realpath", REL,
     "    for p in (os.path.abspath(path), os.path.realpath(path)):\n",
     "    for p in (os.path.abspath(path),):\n",
     "T.test_t2_symlink_root_realpath_is_normalized"),
    ("K2-short-first", REL,
     "    return sorted(out, key=len, reverse=True)\n\n\ndef sub_root",
     "    return sorted(out, key=len)\n\n\ndef sub_root",
     "T.test_t5_own_dir_before_system_root"),
    ("K3-no-boundary", REL,
     '_BOUND = r"(?=/|$|[^A-Za-z0-9._-])"',
     '_BOUND = r""',
     "T.test_t4_boundary"),
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
                if "skipped" in out and "FAIL: " not in out:
                    self.skipTest("%s: target skipped on this platform\n%s" % (tag, out))
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
