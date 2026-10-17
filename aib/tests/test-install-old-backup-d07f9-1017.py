#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""一行安裝對新用戶 / 舊版的處理(10-17 fishmac 實機:舊版 0.1.1 → 0.1.16;Andy「有舊版先全備份,再用乾淨的環境裝」)。

I1 clean_if_far:落後 ≥ 5 個小版 ⇒ 整份打包 + 搬進 ~/aib-backup-<時間>/(含 .zprofile、claude 設定、README 還原方法),原位置空出來
I2 只差幾版 ⇒ 不動(照舊快轉);本機比正式版新(開發機從 main 裝的)⇒ 不動
I3 has_local_bin:.zprofile 已有沒註解的 export PATH=…~/.local/bin ⇒ 不再加;註解掉的不算
I4 結尾給一行能複製的開席指令(aib <家> safe root)與線上對話開通那行,不再出現 <provider> <role>
I5 擴充沒有這家 AI 的版本:說「不用裝、不影響使用」,不像少裝
"""
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
INSTALL = (ROOT / "install.sh").read_text(encoding="utf-8")


def block(start):
    """抽出 install.sh 裡 main() 內的一個函式(兩格縮排,從開頭那行到下一個單獨一行的「  }」)。"""
    lines = INSTALL.splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith(start))
    if lines[i].rstrip().endswith("}"):
        return lines[i]
    j = next(k for k in range(i + 1, len(lines)) if lines[k] == "  }")
    return "\n".join(lines[i:j + 1])


FNS = "\n".join(block(s) for s in ("  repo_ver() {", "  release_ref() {", "  _ver_num() {", "  clean_if_far() {", "  has_local_bin() {"))


def git(*a, cwd=None):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                            GIT_COMMITTER_EMAIL="t@t"))


class Old(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        t = Path(self.td.name)
        self.home = t / "home"
        self.home.mkdir()
        (self.home / ".zprofile").write_text("# mine\n")
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "settings.json").write_text("{}")
        self.origin = t / "origin"
        self.origin.mkdir()
        git("init", "-q", "-b", "main", cwd=self.origin)
        (self.origin / "aib").mkdir()

    def release(self, ver):
        (self.origin / "aib" / "VERSION").write_text(ver + "\n")
        git("add", "-A", cwd=self.origin)
        git("commit", "-q", "-m", ver, cwd=self.origin)
        git("tag", "v" + ver, cwd=self.origin)

    def run_clean(self, local_ver):
        d = self.home / "ez2-aib"
        git("clone", "-q", str(self.origin), str(d))
        git("reset", "-q", "--hard", "v" + local_ver, cwd=d)
        script = "%s\nsay() { echo \"$*\"; }\nmiss=()\nDIR=%s; DRY=0; SELF_URL=https://raw.githubusercontent.com/x/install.sh\nclean_if_far\necho \"MISS=${#miss[@]}\"\n" % (
            FNS, d)
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=dict(os.environ, HOME=str(self.home)))
        return r, d

    def test_i1_far_behind(self):
        self.release("0.1.1")
        self.release("0.1.16")
        r, d = self.run_clean("0.1.1")
        self.assertIn("MISS=0", r.stdout, r.stdout + r.stderr)
        self.assertFalse(d.exists(), "舊版搬開,原位置空出來給乾淨安裝")
        b = next(self.home.glob("aib-backup-*"))
        self.assertTrue((b / "ez2-aib-0.1.1.tgz").is_file())
        self.assertTrue((b / "ez2-aib-0.1.1" / "aib" / "VERSION").is_file())
        self.assertTrue((b / "dotfiles" / "claude-settings.json").is_file() and (b / "dotfiles" / ".zprofile").is_file())
        self.assertIn("還原", (b / "README.txt").read_text())
        self.assertIn("0.1.16", r.stdout)

    def test_i2_close_or_newer(self):
        self.release("0.1.12")
        self.release("0.1.16")
        r, d = self.run_clean("0.1.12")
        self.assertTrue(d.exists(), "只差 4 版 ⇒ 照舊快轉,不動")
        self.assertEqual(list(self.home.glob("aib-backup-*")), [])
        import shutil
        shutil.rmtree(d)
        self.release("0.2.0")
        git("tag", "-d", "v0.2.0", cwd=self.origin)       # 本機比正式版(v0.1.16)新
        d2 = self.home / "ez2-aib"
        git("clone", "-q", str(self.origin), str(d2))
        script = "%s\nsay() { echo \"$*\"; }\nmiss=()\nDIR=%s; DRY=0; SELF_URL=https://raw.githubusercontent.com/x/install.sh\nclean_if_far\n" % (FNS, d2)
        subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=dict(os.environ, HOME=str(self.home)))
        self.assertTrue(d2.exists(), "比正式版新 ⇒ 不動")

    def test_i3_has_local_bin(self):
        p = self.home / ".zprofile"
        chk = lambda: subprocess.run(["bash", "-c", "%s\nhas_local_bin %s" % (FNS, p)], env=dict(os.environ, HOME=str(self.home))).returncode
        p.write_text("# export PATH=\"$HOME/.local/bin:$PATH\"\n")
        self.assertEqual(chk(), 1, "註解掉的不算")
        for line in ('export PATH="$HOME/.local/bin:$PATH"', "export PATH=%s/.local/bin:$PATH" % self.home, "  export PATH=~/.local/bin:$PATH"):
            p.write_text(line + "\n")
            self.assertEqual(chk(), 0, line)


class Text(unittest.TestCase):
    def test_i4_next_step(self):
        self.assertNotIn("aib <provider> safe <role> 開席", INSTALL)
        self.assertIn("safe root", INSTALL)
        self.assertIn("aib chat on --name", INSTALL)
        self.assertRegex(INSTALL, r"has_local_bin \"\$PROFILE\"")

    def test_i5_ext_wording(self):
        for lang in ("zh-TW", "en", "ja", "ko"):
            v = json.loads((ROOT / "aib" / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8"))["aib.ext.eng_none"]
            self.assertTrue(v.strip().startswith("·"), lang)
            self.assertNotIn("還沒有", v)


if __name__ == "__main__":
    unittest.main()
