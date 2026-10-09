#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""安全網:C 層讀了檔的內容,Python 只「探測」過那條路徑 ⇒ 照樣算沒記到(10-08,mypy 實測差點把紅藏成綠)。

mypy 是 mypyc 編的:沒有快取時在 C 層讀 calc.py 的內容,Python 鉤子只記到一筆 probe(看在不在)。以前 native_unrecorded
把「任何事件提到這條路徑」都當成記過內容 ⇒ 放行 ⇒ 改了 calc.py 照樣沿用。
N1 C 層讀了、Python 只有 probe / missing / listdir ⇒ 列進沒記到的
N2 C 層讀了、Python 有 open(記了內容雜湊)⇒ 不列
N3 /proc、/sys、/dev 的虛擬檔不是輸入(is_derived);一般檔照算
N4 .mypy_cache / .ruff_cache 不在「不算輸入」的名單裡(mypy 看修改時間決定要不要讀原始碼,快取本身就是輸入)
刀(複本):K1 probe 也算記過內容 ⇒ N1 紅
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "verify"))
sys.path.insert(0, str(ROOT / "tests"))
import shadow_tracer as st  # noqa: E402


class Native(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(os.path.realpath(self.td.name)) / "repo"
        self.repo.mkdir()
        self.calc = self.repo / "calc.py"
        self.calc.write_text("X = 1\n", encoding="utf-8")
        self.ldp = Path(self.td.name) / "t.ldp"
        self.ldp.write_text("o\t100\t%s\n" % self.calc, encoding="utf-8")

    def tearDown(self):
        self.td.cleanup()

    def _events(self, *extra):
        return [{"ch": "inherit", "pid": 100}] + list(extra)

    def test_n1_probe_only_is_unrecorded(self):
        for ch in ("probe", "missing", "listdir"):
            ev = self._events({"ch": ch, "path": str(self.calc), "pid": 100, "state": "file"})
            self.assertEqual(st.native_unrecorded(ev, str(self.ldp), str(self.repo)), ["calc.py"], ch)

    def test_n2_open_is_recorded(self):
        ev = self._events({"ch": "open", "path": str(self.calc), "pid": 100, "sha256": "x"})
        self.assertEqual(st.native_unrecorded(ev, str(self.ldp), str(self.repo)), [])

    def test_n3_virtual_fs_not_input(self):
        for p in ("/proc/stat", "/proc/123/smaps", "/sys/fs/cgroup/cpu.max", "/dev/urandom"):
            self.assertTrue(st.is_derived(p), p)
        self.assertFalse(st.is_derived(str(self.calc)))

    def test_n4_mtime_caches_stay_inputs(self):
        for d in (".mypy_cache", ".ruff_cache", ".pytest_cache"):
            self.assertNotIn(d, st.DERIVED_DIRNAMES, d)
            self.assertFalse(st.is_derived("/x/%s/3.13/calc.data.json" % d), d)


class Knives(unittest.TestCase):
    def test_k1_probe_counts_reds_n1(self):
        from aib_knife_copy import knife_copy
        name = Path(__file__).name
        with knife_copy(ROOT, "tools/verify/shadow_tracer.py", '                if e.get("ch") not in ("probe", "missing", "listdir"):\n',
                        "                if True:\n", tests=(name,)) as root:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            p = subprocess.run([sys.executable, str(root / "tests" / name), "Native.test_n1_probe_only_is_unrecorded"], cwd=str(root),
                               env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
        self.assertIn("test_n1_probe_only_is_unrecorded", set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M)),
                      p.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
