#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""Node 接頭包函式時,原函式身上的屬性要一起帶過去(10-08,commander.js 實測:aib 底下 4 支假紅)。

以前 wrap() 只帶 name ⇒ execFile / exec / fs.exists 身上的 util.promisify.custom 不見 ⇒
promisify(execFile) 解出來是字串不是 {stdout, stderr} ⇒ 測試在 aib 底下變紅(觀測者改了被觀測物)。
W1 有掛接頭的 node:test 檔:promisify(execFile) 拿到 {stdout},promisify(fs.exists) 拿到 true ⇒ 綠、量得到
刀(複本):K1 不帶屬性 ⇒ W1 紅
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "verify"))
sys.path.insert(0, str(ROOT / "tests"))

JS = """import { test } from 'node:test';
import assert from 'node:assert/strict';
import util from 'node:util';
import cp from 'node:child_process';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

test('promisify keeps the shape', async () => {
  const { stdout } = await util.promisify(cp.execFile)(process.execPath, ['-e', 'process.stdout.write("x")']);
  assert.equal(stdout, 'x');
  assert.equal(await util.promisify(fs.exists)(fileURLToPath(import.meta.url)), true);
});
"""


@unittest.skipUnless(shutil.which("node"), "沒有 node")
class NodeWrap(unittest.TestCase):
    def test_w1_promisify_shape_kept(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / "tests").mkdir(parents=True)
            (repo / "tests" / "t_wrap.test.mjs").write_text(JS, encoding="utf-8")
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
            for a in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
                subprocess.run(["git", "-C", str(repo)] + a, check=True, capture_output=True, env=env, stdin=subprocess.DEVNULL)
            tenv = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE", "NODE_OPTIONS")}
            tenv.update({"HOME": str(Path(td) / "home"), "EZ2_AIB_LOCALE": "en"})
            (Path(td) / "home").mkdir()
            p = subprocess.run([sys.executable, str(ROOT / "tools" / "verify" / "test_entry.py"), "--repo", str(repo),
                                "tests/t_wrap.test.mjs"], cwd=str(repo), env=tenv, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])
            self.assertIn("1 tests: 1 green, 0 red", p.stdout)


class Knives(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "沒有 node")
    def test_k1_no_props_reds_w1(self):
        from aib_knife_copy import knife_copy
        name = Path(__file__).name
        with knife_copy(ROOT, "tools/verify/node-shim/audit.cjs", "    for (const k of Reflect.ownKeys(orig)) {\n",
                        "    for (const k of []) {\n", tests=(name,)) as root:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            p = subprocess.run([sys.executable, str(root / "tests" / name), "NodeWrap.test_w1_promisify_shape_kept"], cwd=str(root),
                               env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
        self.assertIn("test_w1_promisify_shape_kept", set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M)),
                      p.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
