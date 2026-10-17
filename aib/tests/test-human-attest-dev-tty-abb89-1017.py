#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""本人檢查不能把 "/dev/tty" 當成終端機名字(10-17 維護者在普通終端機核准專案卡被擋 tty_owner)。

macOS 上 open("/dev/tty") 的 fd,os.ttyname 回 "/dev/tty"(擁有者 root),不是真的 /dev/ttysNNN
⇒ 本人永遠過不了 tty_owner;hub 的 pane tty 也永遠比對不上(只剩 TMUX 環境變數一道防線)。
A1 ttyname 回 "/dev/tty" ⇒ 改用 stdio 的真終端機名，擁有者是本人 ⇒ 不缺 tty_owner
A2 stdio 真終端機是某個 tmux pane 的 tty ⇒ in_hub(就算 TMUX 環境變數被清掉)
A3 _stdio_tty 跳過不是終端機的 fd、跳過 "/dev/tty" 本身;都不是 ⇒ None
刀:K1 拿掉 "/dev/tty" 的改寫 ⇒ A1 紅
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "cockpit" / "human_attest.py"
sys.path.insert(0, str(SRC.parent))


def _load(path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("human_attest_under_test_%d" % id(path), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Stat:
    def __init__(self, uid):
        self.st_uid = uid


def _probe(mod, real="/dev/ttys018"):
    me = os.getuid()
    with mock.patch.object(mod.os, "open", return_value=99), \
         mock.patch.object(mod.os, "close"), \
         mock.patch.object(mod.os, "ttyname", side_effect=lambda fd: "/dev/tty" if fd == 99 else real), \
         mock.patch.object(mod.os, "isatty", side_effect=lambda fd: fd == 0), \
         mock.patch.object(mod.os, "stat", side_effect=lambda p: _Stat(0 if p == "/dev/tty" else me)):
        return mod.probe_tty()


class DevTty(unittest.TestCase):
    def setUp(self):
        self.mod = _load(SRC)

    def test_a1_real_tty_name_and_owner(self):
        path, owner, me = _probe(self.mod)
        self.assertEqual(path, "/dev/ttys018")
        self.assertEqual(owner, me)
        out = self.mod.judge([(2, "-zsh"), (1, "login")], (path, owner, me), set(), env={})
        self.assertNotIn("tty_owner", out["missing"])

    def test_a2_pane_tty_is_in_hub_without_env(self):
        tty = _probe(self.mod, real="/dev/ttys007")
        out = self.mod.judge([(2, "-zsh"), (1, "login")], tty, {"/dev/ttys007"}, env={})
        self.assertIn("in_hub", out["missing"])

    def test_a3_stdio_tty_skips(self):
        f = self.mod._stdio_tty
        self.assertEqual(f(isatty=lambda fd: fd == 1, ttyname=lambda fd: "/dev/ttys003"), "/dev/ttys003")
        self.assertIsNone(f(isatty=lambda fd: True, ttyname=lambda fd: "/dev/tty"))
        self.assertIsNone(f(isatty=lambda fd: False, ttyname=lambda fd: "/dev/ttys003"))

    def test_k1_knife_without_rewrite(self):
        src = SRC.read_text(encoding="utf-8")
        bad = src.replace('    if not path or path == "/dev/tty":\n        path = _stdio_tty() or path\n', "", 1)
        self.assertNotEqual(bad, src, "刀沒咬到:找不到改寫那兩行")
        d = Path(tempfile.mkdtemp(prefix="attest-knife-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        (d / "human_attest.py").write_text(bad, encoding="utf-8")
        mod = _load(d / "human_attest.py")
        path, owner, me = _probe(mod)
        out = mod.judge([(2, "-zsh"), (1, "login")], (path, owner, me), set(), env={})
        self.assertIn("tty_owner", out["missing"])


if __name__ == "__main__":
    unittest.main()
