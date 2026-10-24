#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""consumer_scope 帶進開源版(維護者 10-24:「帶進來並修正」;10-15 匯出清單列為缺件)。

全部在暫存 git 倉跑:不碰網路、不開 socket、不寫真的 ~/.ez2-aib。
C1 模組從本樹載得到,i18n 不退到別的倉(sys.path 不含 ez2-ai-b)
C2 規則 3:尺 import 到被改的產品模組 ⇒ 是消費者
C3 規則 4:改到被 source 的殼 ⇒ 往上展開到 source 它的殼
C4 select_changed 不再丟「consumer_scope import」的量不到;改產品檔選得到 import 它的測試
C5 四語字典都有 aib.cscope.* 的 19 個鍵
C6 倉裡的子專案(開源版的 aib/):select_changed --repo 子專案 + 基準 ⇒ 路徑照子專案算、挑得到;
   test_entry._subproject 從目前目錄往上找第一個有 tests/ 的資料夾,倉根 / 倉外 ⇒ 倉根
刀(複本):K1 把規則 3 的比對拿掉 ⇒ C2 紅
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # = aib/
CS = ROOT / "tools" / "governance" / "consumer_scope.py"
SEL = ROOT / "tools" / "verify" / "select_changed.py"
os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
for _p in (CS.parent, SEL.parent, ROOT / "src" / "cockpit"):
    sys.path.insert(0, str(_p))


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _repo():
    d = Path(tempfile.mkdtemp(prefix="cscope-"))
    files = {
        "src/cockpit/widget.py": "def f():\n    return 1\n",
        "src/cockpit/base-env.sh": "X=1\n",
        "src/cockpit/full.sh": ". ./base-env.sh\necho ok\n",
        "tests/test-widget.py": "import widget\nprint(widget.f())\n",
        "tests/test-other.py": "print('nothing')\n",
    }
    for rel, body in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(body, encoding="utf-8")
    _git(d, "init", "-q")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "init")
    return d


def check_c2(t, cs, repo):
    got = set(cs.import_consumers(str(repo), ["src/cockpit/widget.py"],
                                  [r for r in cs.repo_files(str(repo)) if r.startswith("tests/")]))
    t.assertIn("tests/test-widget.py", got)
    t.assertNotIn("tests/test-other.py", got)


class InTree(unittest.TestCase):
    def setUp(self):
        self.repo = _repo()
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.cs = _load(CS, "consumer_scope")
        sys.modules["consumer_scope"] = self.cs
        self.addCleanup(sys.modules.pop, "consumer_scope", None)

    def test_c1_loads_from_this_tree(self):
        self.assertEqual(Path(self.cs.__file__).resolve(), CS.resolve())
        self.assertFalse([p for p in sys.path if "ez2-ai-b" in p], sys.path)

    def test_c2_import_rule(self):
        check_c2(self, self.cs, self.repo)

    def test_c3_shell_closure_upwards(self):
        ls = self.cs.repo_files(str(self.repo))
        prods = [r for r in ls if self.cs.PRODUCT_DIR.match(r)]
        edges = self.cs.call_graph(str(self.repo), prods)
        up = set(self.cs.expand_upwards(["src/cockpit/base-env.sh"], edges))
        self.assertIn("src/cockpit/full.sh", up)
        down = set(self.cs.expand_upwards(["src/cockpit/full.sh"], edges))
        self.assertNotIn("src/cockpit/base-env.sh", down)

    def test_c4_select_changed_measures(self):
        sel = _load(SEL, "select_changed_under_test")
        try:                                       # 不讀反查帳本(那在真的狀態根)
            out = sel.select(str(self.repo), ["src/cockpit/widget.py"], rows=[], use_reverse=False)
        except sel.Unmeasurable as exc:
            self.fail("仍量不到:%s" % exc)
        picked = {r["test"]: [w["rule"] for w in r["why"]] for r in out["selected"]}
        self.assertIn("r3-import", picked.get("tests/test-widget.py", []), picked)
        self.assertNotIn("tests/test-other.py", picked)

    def test_c5_four_locales(self):
        keys = [k for k in json.loads((ROOT / "i18n" / "zh-TW.json").read_text(encoding="utf-8"))
                if k.startswith("aib.cscope.")]
        self.assertEqual(len(keys), 19)
        for loc in ("en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            self.assertEqual([k for k in keys if not d.get(k)], [], loc)


class SubProject(unittest.TestCase):
    def setUp(self):
        self.top = Path(tempfile.mkdtemp(prefix="cscope-top-"))
        self.addCleanup(shutil.rmtree, self.top, True)
        sub = _repo()                                # 做好的小倉搬進 top/aib,再在 top 建一個倉
        shutil.rmtree(sub / ".git")
        shutil.move(str(sub), str(self.top / "aib"))
        (self.top / "README.md").write_text("x\n", encoding="utf-8")
        _git(self.top, "init", "-q")
        _git(self.top, "add", "-A")
        _git(self.top, "commit", "-qm", "init")
        (self.top / "aib" / "src" / "cockpit" / "widget.py").write_text("def f():\n    return 3\n", encoding="utf-8")
        _git(self.top, "commit", "-qam", "touch")

    def test_c6_select_from_subproject(self):
        p = subprocess.run([sys.executable, str(SEL), "--repo", str(self.top / "aib"), "--print-tests", "--base", "HEAD~1"],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
                           env={**os.environ, "AIB_REVERSE_ROWS_DIR": str(self.top / "none")})
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertIn("tests/test-widget.py", p.stdout.split())

    def test_c6_subproject_lookup(self):
        te = _load(SEL.parent / "test_entry.py", "test_entry_under_test")
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        os.chdir(self.top / "aib" / "src")
        self.assertEqual(te._subproject(self.top).resolve(), (self.top / "aib").resolve())
        os.chdir(self.top)
        self.assertEqual(te._subproject(self.top), self.top)
        os.chdir(tempfile.gettempdir())
        self.assertEqual(te._subproject(self.top), self.top)


class Knives(unittest.TestCase):
    def test_k1_no_import_rule(self):
        tmp = Path(tempfile.mkdtemp(prefix="cscope-k1-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        src = CS.read_text(encoding="utf-8")
        knife = tmp / "consumer_scope.py"
        knife.write_text(src.replace("def import_consumers(", "def _orig_import_consumers(", 1)
                         + "\n\ndef import_consumers(repo, products, rulers):\n    return []\n", encoding="utf-8")
        repo = _repo()
        self.addCleanup(shutil.rmtree, repo, True)
        cs = _load(knife, "consumer_scope_knife")
        with self.assertRaises(AssertionError):
            check_c2(self, cs, repo)


if __name__ == "__main__":
    unittest.main()
