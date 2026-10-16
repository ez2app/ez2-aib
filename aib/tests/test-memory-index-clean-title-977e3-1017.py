#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib memory write:索引記的標題跟檔內 frontmatter 一致(10-17 @0c105 觀察、@abb89 裁)。全在 tmp 沙盒。

上游 note-format 正本 cleanTitle 會拿掉檔名不安全字(: / # 等;全形「：」NFKC 後也是半形冒號)——那是正本規則,不改副本。
以前索引記的是原字 ⇒ 「開席紀錄:LINE…」索引有冒號、檔內沒有。現在索引記檔內那一份。

T1 標題含 : # / ⇒ 檔內 title、索引 title、回傳 title 三者相同,且是清洗後的
T2 沒有要清的字 ⇒ 原樣
刀(複本,走 run_guarded):X1 runner 不回 title ⇒ T1 紅
"""
import json
import os
import re
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "memory"))
import aib_testlib  # noqa: E402,F401
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

HAVE_NODE = shutil.which("node") is not None


@unittest.skipUnless(HAVE_NODE, "沒有 node(note-format runner 要 node)")
class TestCleanTitle(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="aib-memtitle-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        for k in ("EZ2_VAULT_ROOT", "EZ2_AI_REPO", "EZ2_ROUTE_REGISTRY", "EZ2_ROUTE_CONTRACT", "TMUX", "TMUX_PANE"):
            os.environ.pop(k, None)
        home = self.tmp / "home"
        home.mkdir()
        self.v = self.tmp / "vault"
        self.v.mkdir()
        os.environ.update({"HOME": str(home), "EZ2_VAULT_DIR": str(self.v), "EZ2_AIB_LOCALE": "en",
                           "EZ2_AIB_HOME": str(home / ".ez2-aib"), "EZ2_AIB_STATE_ROOT": str(home / ".ez2-aib"),
                           "EZ2_EZ2AI_REPO": str(self.tmp / "no-repo")})
        import aib_memory as AM
        self.AM = AM

    def write(self, title):
        out = self.AM.write("note", title, "內文", route_key="core.handoff")
        text = Path(out["path"]).read_text(encoding="utf-8")
        in_file = re.search(r'(?m)^title: "?(.*?)"?$', text).group(1)
        rec = json.loads(self.AM.index_path().read_text(encoding="utf-8").splitlines()[-1])
        return in_file, rec["title"], out["title"]

    def test_T1_index_matches_file(self):
        in_file, indexed, returned = self.write("開席紀錄:LINE #1 a/b")
        self.assertNotIn(":", in_file)
        self.assertEqual(in_file, "開席紀錄LINE 1 ab")
        self.assertEqual((indexed, returned), (in_file, in_file))

    def test_T2_plain_title_unchanged(self):
        self.assertEqual(self.write("LINE 選單 換版"), ("LINE 選單 換版",) * 3)


@unittest.skipUnless(HAVE_NODE, "沒有 node")
class Knife(unittest.TestCase):
    def test_X1_runner_without_title(self):
        name = Path(__file__).name
        with knife_copy(ROOT, "src/memory/note_format_runner.js", "      title: built.record.frontmatter.title,", "",
                        tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "TestCleanTitle.test_T1_index_matches_file"],
                            cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
