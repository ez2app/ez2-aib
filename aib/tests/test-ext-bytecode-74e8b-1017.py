#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""擴充包裡的 Python 位元碼不讓 aib ext 拒裝(10-17 @d07f9 回報、@abb89 派,收 0.1.15)。

原因:extensions/aib-boards/bin/seat-info.py 會 import relay_line,有人直接跑它(艦長室伺服器就會)就在 bin/ 長出
__pycache__/*.pyc;aib ext 算雜湊時把它當成「多了的檔」⇒ 跟說明檔對不上 ⇒ 拒裝(test-ext-autoinstall-mods 在 main 直接 8 格紅)。
修法:位元碼不算進雜湊,但不默默放著 —— 列出來並清掉;複製時不帶;裝好的那份清不掉就不啟用(避免有人塞 .pyc 被當程式跑)。

用 test-ext-autoinstall-mods-74e8b-1015 的暫存 HOME + 假 claude(不碰真的 ~/.claude、~/.ez2-aib);來源是本倉 extensions/aib-boards 的複本。
B1 包裡有 __pycache__/*.pyc 與散落的 .pyc ⇒ inspect 不報「對不上」
B2 install ⇒ 印出清掉幾個、哪幾個;來源與裝好的那份都沒有位元碼
B3 來源的位元碼清不掉(唯讀之類)⇒ 照裝,但裝好的那份沒有位元碼(複製時不帶)
B4 裝好的那份被直接跑過、長了位元碼 ⇒ enable 清掉並列出,照常啟用
B5 裝好的那份位元碼清不掉 ⇒ enable 不啟用(rc 3),說清不掉哪個
B6 .pyc 是指到包外的符號連結 ⇒ 只拿掉連結,不碰包外的檔
B7 四語字典新鍵四份都有、佔位符相同
刀(同檔變體,不碰真樹):K1 雜湊照算位元碼 ⇒ B1 紅;K2 install 不清 ⇒ B2 紅;K3 複製時照帶 ⇒ B3 紅;K4 enable 不清 ⇒ B4 紅
"""
import importlib.util
import json
import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("ext_auto_1015", HERE / "test-ext-autoinstall-mods-74e8b-1015.py")
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)
aib_ext, variant, ROOT, PH = A.aib_ext, A.variant, A.ROOT, A.PH

NEW_KEYS = ("pyc_purged", "pyc_left")


def plant(pkg):
    """模擬「有人直接跑過包裡的 .py」:bin/__pycache__/ 一個 + bin/ 散落一個"""
    pc = Path(pkg) / "bin" / "__pycache__"
    pc.mkdir(parents=True, exist_ok=True)
    (pc / "relay_line.cpython-315.pyc").write_bytes(b"\x00fake")
    (Path(pkg) / "bin" / "stray.pyc").write_bytes(b"\x00fake")


def bytecode(pkg):
    return sorted(p.relative_to(pkg).as_posix() for p in Path(pkg).rglob("*") if p.name == "__pycache__" or p.suffix == ".pyc")


def check_b1(t, mod):
    pkg = t.src / "aib-boards"
    plant(pkg)
    m, probs = mod.inspect(pkg)
    t.assertEqual(probs, [])


def check_b2(t, mod):
    pkg = t.src / "aib-boards"
    plant(pkg)
    rc, out = t.run_mod(mod, "install", str(pkg))
    t.assertEqual(rc, 0, out)
    t.assertIn("清掉 2 個", out)
    t.assertIn("bin/__pycache__/", out)
    t.assertIn("bin/stray.pyc", out)
    t.assertEqual(bytecode(pkg), [])
    t.assertEqual(bytecode(t.mods / "aib-boards"), [])


def check_b3(t, mod):
    pkg = t.src / "aib-boards"
    plant(pkg)
    with mock.patch.object(mod, "purge_bytecode", lambda folder, out=None: []):   # 來源清不掉(唯讀)
        rc, out = t.run_mod(mod, "install", str(pkg))
    t.assertEqual(rc, 0, out)
    t.assertNotEqual(bytecode(pkg), [])
    t.assertEqual(bytecode(t.mods / "aib-boards"), [])


def check_b4(t, mod):
    rc, out = t.run_mod(aib_ext, "install", str(t.src / "aib-boards"))
    t.assertEqual(rc, 0, out)
    dst = t.mods / "aib-boards"
    plant(dst)
    rc, out = t.run_mod(mod, "enable", "aib-boards")
    t.assertEqual(rc, 0, out)
    t.assertIn("清掉 2 個", out)
    t.assertEqual(bytecode(dst), [])


class Bytecode(A.Base):
    def test_b1_inspect_ignores_bytecode(self):
        check_b1(self, aib_ext)

    def test_b2_install_purges_and_lists(self):
        check_b2(self, aib_ext)

    def test_b3_unremovable_source_not_copied(self):
        check_b3(self, aib_ext)

    def test_b4_enable_purges_installed_copy(self):
        check_b4(self, aib_ext)

    def test_b5_enable_refuses_when_unremovable(self):
        rc, out = self.run_mod(aib_ext, "install", str(self.src / "aib-boards"))
        self.assertEqual(rc, 0, out)
        plant(self.mods / "aib-boards")
        real_rmtree = shutil.rmtree

        def deny(p, *a, **kw):
            if Path(p).name == "__pycache__":
                raise PermissionError(p)
            return real_rmtree(p, *a, **kw)
        with mock.patch.object(aib_ext.shutil, "rmtree", deny):
            rc, out = self.run_mod(aib_ext, "enable", "aib-boards")
        self.assertEqual(rc, 3, out)
        self.assertIn("清不掉", out)
        self.assertIn("bin/__pycache__/", out)

    def test_b6_symlinked_pyc_only_link_removed(self):
        outside = self.tmp / "outside.pyc"
        outside.write_bytes(b"keep")
        link = self.src / "aib-boards" / "bin" / "evil.pyc"
        link.symlink_to(outside)
        left = aib_ext.purge_bytecode(self.src / "aib-boards", out=open(os.devnull, "w"))
        self.assertEqual(left, [])
        self.assertFalse(os.path.lexists(link))
        self.assertEqual(outside.read_bytes(), b"keep")


class I18n(unittest.TestCase):
    def test_b7_four_locales(self):
        d = {loc: json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8")) for loc in ("en", "zh-TW", "ja", "ko")}
        for k in NEW_KEYS:
            key = "aib.ext." + k
            for loc in d:
                self.assertIn(key, d[loc], (loc, key))
                self.assertEqual(sorted(PH.findall(d[loc][key])), sorted(PH.findall(d["zh-TW"][key])), (loc, key))


class Knives(A.Base):
    def test_k1_hash_counts_bytecode(self):
        mod = variant(" and not _is_bytecode(p.relative_to(folder))}", "}")
        with self.assertRaises(AssertionError):
            check_b1(self, mod)

    def test_k2_install_does_not_purge(self):
        mod = variant("    purge_bytecode(folder, out)   # 清不掉的也不會被複製過去(NO_BYTECODE)\n", "")
        with self.assertRaises(AssertionError):
            check_b2(self, mod)

    def test_k3_copy_keeps_bytecode(self):
        mod = variant("    shutil.copytree(folder, tmp, ignore=NO_BYTECODE)\n", "    shutil.copytree(folder, tmp)\n")
        with self.assertRaises(AssertionError):
            check_b3(self, mod)

    def test_k4_enable_does_not_purge(self):
        mod = variant("    if purge_bytecode(pkg, out):", "    if False:")
        with self.assertRaises(AssertionError):
            check_b4(self, mod)


if __name__ == "__main__":
    unittest.main(verbosity=2)
