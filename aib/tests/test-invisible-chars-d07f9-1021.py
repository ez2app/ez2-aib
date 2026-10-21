#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""原始碼不准夾隱形字元(@d07f9 10-21 夜;#245 帶了一個零寬字元進去,審查跟 aib test 都沒擋)。

零寬 / 方向控制字元在審查畫面上看不見,夾在字串或正規表達式裡會悄悄改語意(Trojan Source 那一類)。
要用就寫跳脫(\\u202e 這種),不要放字面字元。

I1 git 追蹤的 .py/.js/.json/.md/.sh/.swift 沒有字面的 U+200B–200F、U+202A–202E、U+2060–2069、U+FEFF
   (白名單除外,鍵 = 路徑 + 碼位,每筆寫原因;目前空);命中列 檔:行:碼位
I2 git ls-files 列不出檔 ⇒ 紅(複製到暫存跑會空過,10-20 #55 踩過)
刀(複本):K1 在產品檔塞一個 U+202E ⇒ I1 紅
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

EXTS = (".py", ".js", ".json", ".md", ".sh", ".swift")
RANGES = ((0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x2069), (0xFEFF, 0xFEFF))

# 白名單 (路徑, 碼位) ⇒ 原因。@abb89 裁示 L4:既有命中同一張 PR 改成跳脫,白名單只留真的非字面不可的
ALLOW = {}


def _bad(cp):
    return any(lo <= cp <= hi for lo, hi in RANGES)


def tracked():
    top = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True).stdout.strip()
    if not top:
        return None, []
    out = subprocess.run(["git", "-C", top, "ls-files", "-z"], capture_output=True, text=True).stdout
    return Path(top), [f for f in out.split("\0") if f]


def scan(top, files):
    hits = []
    for rel in files:
        if not rel.endswith(EXTS):
            continue
        try:
            text = (top / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        key = rel[4:] if rel.startswith("aib/") else rel
        for n, line in enumerate(text.split("\n"), 1):
            for ch in line:
                cp = ord(ch)
                if _bad(cp) and (key, cp) not in ALLOW:
                    hits.append("%s:%d:U+%04X" % (rel, n, cp))
    return hits


class Invisible(unittest.TestCase):
    def test_i2_git_lists_files(self):
        top, files = tracked()
        self.assertTrue(top and files, "git ls-files 沒列出檔:要在有 git 的樹跑,不然 I1 會空過")

    def test_i1_no_literal_invisible(self):
        top, files = tracked()
        self.assertTrue(files, "git ls-files 沒列出檔")
        hits = scan(top, files)
        self.assertEqual(hits, [], "有字面隱形字元,改寫成跳脫(\\uXXXX):\n" + "\n".join(hits[:50]))


class Knife(unittest.TestCase):
    def test_k1_planted_rlo(self):
        name = Path(__file__).name
        anchor = "# SPDX-License-Identifier: Apache-2.0\n"
        with knife_copy(REPO, "src/cockpit/dm_queue.py", anchor, anchor + "# x" + chr(0x202E) + "y\n",
                        tests=(name,)) as root:
            env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="k", GIT_AUTHOR_EMAIL="k@k",
                       GIT_COMMITTER_NAME="k", GIT_COMMITTER_EMAIL="k@k")
            for cmd in (["git", "init", "-q"], ["git", "add", "-A"]):
                subprocess.run(cmd, cwd=str(root), env=env, check=True, capture_output=True)
            r = run_guarded([sys.executable, str(root / "tests" / name), "Invisible.test_i1_no_literal_invisible"],
                            cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("dm_queue.py:3:U+202E", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
