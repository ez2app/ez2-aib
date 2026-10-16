#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib test --changed 在缺 consumer_scope 的機器上要講清楚、不能崩(10-16 @977e3 回報)。

開源版沒有 consumer_scope.py(私倉缺件，等匯出)。改到的檔不是產品檔(例如 docs/、i18n/)時,
select() 會略過 ④、直接走 ③,舊寫法 import 跟 except cs.Unmeasurable 在同一個 try
⇒ 模組不在時先撞 UnboundLocalError,aib test --changed 印一串 traceback、一支都沒跑。
M1 缺 consumer_scope + 只改非產品檔 ⇒ 丟 Unmeasurable(訊息寫明 consumer_scope import),不是 UnboundLocalError
M2 指令列跑 select_changed.py ⇒ rc 3、stdout 是 {"unmeasurable": …},stderr 沒有 Traceback
刀(複本):K1 把 ③ 的 import 放回同一個 try ⇒ M1 紅
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "verify" / "select_changed.py"
sys.path.insert(0, str(TOOL.parent))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))   # 刀的複本不在原樹,i18n_lib 要從這裡找
os.environ["EZ2_AIB_LOCALE"] = "zh-TW"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def _tiny_repo():
    d = Path(tempfile.mkdtemp(prefix="selchg-"))
    (d / "docs").mkdir()
    (d / "docs" / "x.md").write_text("hello\n", encoding="utf-8")
    (d / "tests").mkdir()
    (d / "tests" / "test-x.py").write_text("# reads docs/x.md\nprint('ok')\n", encoding="utf-8")
    _git(d, "init", "-q")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "init")
    return d


def _load(path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("select_changed_under_test_%d" % id(path), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MissingConsumerScope(unittest.TestCase):
    def setUp(self):
        self.repo = _tiny_repo()
        self.addCleanup(shutil.rmtree, self.repo, True)
        self._saved = sys.modules.get("consumer_scope", "<absent>")
        sys.modules["consumer_scope"] = None          # import 一定失敗(跟開源版缺檔一樣)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved == "<absent>":
            sys.modules.pop("consumer_scope", None)
        else:
            sys.modules["consumer_scope"] = self._saved

    def _check(self, mod):
        with self.assertRaises(mod.Unmeasurable) as cm:
            mod.select(str(self.repo), ["docs/x.md"])
        self.assertIn("consumer_scope import", str(cm.exception))

    def test_m1_unmeasurable_not_unbound(self):
        self._check(_load(TOOL))

    def test_m2_cli_rc3_no_traceback(self):
        d = Path(tempfile.mkdtemp(prefix="selchg-cli-"))
        self.addCleanup(shutil.rmtree, d, True)
        tool = TOOL
        stub = d / "sitecustomize.py"     # 子行程裡也讓 consumer_scope 匯入失敗(本機若有正本也一樣)
        stub.write_text("import sys\nsys.modules['consumer_scope'] = None\n", encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(d) + os.pathsep + str(TOOL.parent)}
        p = subprocess.run([sys.executable, str(tool), "--repo", str(self.repo), "--changed", "-"],
                           input="docs/x.md\n", capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(p.returncode, 3, p.stderr[-800:])
        self.assertNotIn("Traceback", p.stderr)
        self.assertIn("consumer_scope import", json.loads(p.stdout.strip().splitlines()[-1])["unmeasurable"])

    def test_k1_knife_import_back_in_same_try(self):
        src = TOOL.read_text(encoding="utf-8")
        fixed = ("        try:\n"
                 "            import consumer_scope as cs\n"
                 "        except Exception as err:  # noqa: BLE001\n"
                 "            raise Unmeasurable(\"consumer_scope import: %s: %s\"\n"
                 "                               % (type(err).__name__, err))\n"
                 "        try:\n"
                 "            hits3 = cs.import_consumers(")
        old = ("        try:\n"
               "            import consumer_scope as cs\n"
               "            hits3 = cs.import_consumers(")
        bad = src.replace(fixed, old, 1)
        self.assertNotEqual(bad, src, "刀沒咬到:找不到 ③ 那段 import")
        d = Path(tempfile.mkdtemp(prefix="selchg-knife-"))
        self.addCleanup(shutil.rmtree, d, True)
        (d / "select_changed.py").write_text(bad, encoding="utf-8")
        mod = _load(d / "select_changed.py")
        with self.assertRaises(UnboundLocalError):
            mod.select(str(self.repo), ["docs/x.md"])


if __name__ == "__main__":
    unittest.main()
