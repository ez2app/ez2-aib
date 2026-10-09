#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""沿用紀錄建表變快,結果一樣(10-08:每份分份子行程都要建一次表,本機池約 2.6 → 1.9 秒 CPU)。

兩處:每行 JSON 只解析一次(以前作廢列判斷、_parse_row 各一次);同一個 inputs 字串在很多列重複 ⇒ 路徑只算一次
(同一張表兼做「只存一份」)。
L1 重複的 inputs 字串:反查表對、兩列的字串是同一個物件(只存一份)
L2 每個不同的字串只解析一次路徑(item_path 呼叫次數 = 不同字串數)
L3 壞行、作廢列照舊:壞 JSON ⇒ degraded bad-jsonl;作廢列收進 voids、不當列
刀(複本):K1 不存快取 ⇒ L2 紅
"""
import json
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
import reuse_index  # noqa: E402

SHARED = ["open:./src/a.py:" + "a" * 64, "probe:./src/b.py:missing", "missing:./src/c.py", "argv:x", "open:/usr/lib/x.py:" + "b" * 64]


def _row(test, extra=()):
    return {"test": test, "rc": 0, "measured": True, "inputs": list(SHARED) + list(extra)}


class Load(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.pool = Path(self.td.name) / "pool"
        self.pool.mkdir()

    def tearDown(self):
        self.td.cleanup()

    def _write(self, lines):
        (self.pool / "verify-x.rows.jsonl").write_text("".join(l + "\n" for l in lines), encoding="utf-8")
        return reuse_index.build_index([str(self.pool)])

    def test_l1_reverse_and_shared_strings(self):
        idx = self._write([json.dumps(_row("tests/t1.py")), json.dumps(_row("tests/t2.py", ["open:./src/d.py:" + "c" * 64]))])
        self.assertEqual(idx.reverse["src/a.py"], {"tests/t1.py", "tests/t2.py"})
        self.assertEqual(idx.reverse["src/b.py"], {"tests/t1.py", "tests/t2.py"})
        self.assertEqual(idx.reverse["src/c.py"], {"tests/t1.py", "tests/t2.py"})
        self.assertEqual(idx.reverse["src/d.py"], {"tests/t2.py"})
        self.assertNotIn("usr/lib/x.py", idx.reverse)
        self.assertIs(idx.latest["tests/t1.py"]["inputs"][0], idx.latest["tests/t2.py"]["inputs"][0])

    def test_l2_path_parsed_once_per_string(self):
        calls = []
        real = reuse_index.item_path
        reuse_index.item_path = lambda it: (calls.append(it), real(it))[1]
        try:
            self._write([json.dumps(_row("tests/t%d.py" % i)) for i in range(20)])
        finally:
            reuse_index.item_path = real
        self.assertEqual(len(calls), len(SHARED), calls[:12])

    def test_l3_bad_and_void_lines(self):
        idx = self._write([json.dumps({"void": {"test": "tests/other.py"}, "before": "2026-10-08 00:00:00"}), json.dumps(_row("tests/t1.py"))])
        self.assertEqual(len(idx.voids), 1)
        self.assertIn("tests/t1.py", idx.latest)
        self.assertFalse(idx.degraded)
        idx = self._write([json.dumps(_row("tests/t1.py")), "{not json"])
        self.assertTrue(str(idx.degraded).startswith("bad-jsonl:"), idx.degraded)
        idx = self._write([json.dumps({"test": "tests/t1.py", "inputs": "nope"})])
        self.assertTrue(str(idx.degraded).startswith("bad-jsonl:"), idx.degraded)


class Knives(unittest.TestCase):
    def test_k1_no_cache_reds_l2(self):
        from aib_knife_copy import knife_copy
        name = Path(__file__).name
        with knife_copy(ROOT, "tools/verify/reuse_index.py", "                        got = canon[x] = (sys.intern(x), ",
                        "                        got = (sys.intern(x), ", tests=(name,)) as root:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            p = subprocess.run([sys.executable, str(root / "tests" / name), "Load.test_l2_path_parsed_once_per_string"],
                               cwd=str(root), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
        self.assertIn("test_l2_path_parsed_once_per_string", set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M)),
                      p.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
