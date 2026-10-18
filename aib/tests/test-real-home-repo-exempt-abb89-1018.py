#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib test「碰到真家目錄」不把被測的倉本身算進去(10-18 @9f808 回報 0.1.22 #151 退步:
各席工作樹放在 ~/.ez2-aib/worktrees/ 底下,測試讀倉裡自己的檔被判紅)。

E1 被測倉在 ~/.ez2-aib/worktrees/x 底下:讀寫倉裡的檔不算;倉外的 ~/.ez2-aib/chat 照算
E2 倉就是 ~/.ez2-aib(或家目錄)⇒ 不豁免(不然等於關掉檢查)
E3 runner._repo_roots 帶工作樹、快照與 git 共用目錄(真路徑)
K1 刀:real_home_touches 不看 exempt ⇒ E1 紅
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "tools" / "verify"))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
import shadow_tracer as st  # noqa: E402
import runner  # noqa: E402

H = "/Users/x"
WT = H + "/.ez2-aib/worktrees/ez2-prepush-guard"
EVS = [{"ch": "open", "path": WT + "/githooks/pre-push"},
       {"ch": "open", "path": WT + "/package.json"},
       {"ch": "write", "path": WT + "/tmp/out.txt"},
       {"ch": "write", "path": H + "/.ez2-aib/chat/friends.json"}]


def check(fn):
    return fn(EVS, H, [WT])


class Exempt(unittest.TestCase):
    def test_e1_repo_under_sensitive(self):
        self.assertEqual(check(st.real_home_touches), [("write", H + "/.ez2-aib/chat/friends.json")])
        self.assertEqual(len(st.real_home_touches(EVS, H)), 4)          # 沒給 exempt ⇒ 照舊全算

    def test_e2_no_blanket_exempt(self):
        for e in (H + "/.ez2-aib", H, H + "/"):
            got = st.real_home_touches(EVS, H, [e])
            self.assertEqual(len(got), 4, e)

    def test_e3_repo_roots(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", d], check=True, stdin=subprocess.DEVNULL)
            snap = Path(d) / "snap"
            snap.mkdir()
            roots = runner._repo_roots(Path(d), snap)
            self.assertIn(os.path.realpath(d), roots)
            self.assertIn(os.path.realpath(str(snap)), roots)
            self.assertIn(os.path.realpath(os.path.join(d, ".git")), roots)

    def test_k1_knife_ignore_exempt(self):
        src = (REPO / "tools" / "verify" / "shadow_tracer.py").read_text(encoding="utf-8")
        cut = src.replace("            own.append(e)", "            pass", 1)
        self.assertNotEqual(src, cut, "刀沒落下")
        ns = {"__name__": "st_knife", "__file__": str(REPO / "tools" / "verify" / "shadow_tracer.py")}
        sys.path.insert(0, str(REPO / "tools" / "verify"))
        exec(compile(cut, "shadow_tracer_knife.py", "exec"), ns)
        self.assertNotEqual(check(ns["real_home_touches"]), [("write", H + "/.ez2-aib/chat/friends.json")],
                            "刀沒砍出紅:不看 exempt 也過了")


if __name__ == "__main__":
    unittest.main()
