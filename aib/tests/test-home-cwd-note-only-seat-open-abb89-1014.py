#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""「席位目錄由呼叫者 cwd 改為專案目錄(家目錄不開席)」只在真的開席時印。

災情(10-14 公開版走查):這行是「在家目錄開席 ⇒ 席位改開在專案目錄」的提示,但寫在 aib 入口最前面,
任何子指令只要 cwd 是家目錄、stdin 不是終端機(AI 叫的指令都是)就印 ⇒ aib dm、bindme、now… 每一次都多一行看不懂的話。
修法:改目錄照舊在原處做(後面開席要用),提示移到「確定要開席」(PROVIDER/MODE/ROLE 解析完)之後才印。

C1 非開席指令在家目錄跑 ⇒ 不印;C2 開席 --dry-run 在家目錄跑 ⇒ 照印(對照:提示本身沒拿掉)。
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
AIB = REPO / "src" / "cockpit" / "aib"
NOTE = "家目錄不開席"


class HomeCwdNote(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("EZ2_", "AIB_", "TMUX")) and k not in ("CLAUDECODE",)}
        self.env.update({"HOME": str(self.home), "LANG": "zh_TW.UTF-8", "EZ2_AIB_LOCALE": "zh-TW"})

    def tearDown(self):
        self._tmp.cleanup()

    def run_aib(self, *args):
        return subprocess.run(["bash", str(AIB), *args], cwd=str(self.home), capture_output=True, text=True,
                              timeout=120, env=self.env, stdin=subprocess.DEVNULL)

    def test_C1_non_seat_command_from_home_has_no_note(self):
        for args in (("bindme", "status"), ("now", "--help")):
            p = self.run_aib(*args)
            self.assertNotIn(NOTE, p.stderr + p.stdout, "%s 不是開席,不該印家目錄那行" % " ".join(args))

    def test_C2_seat_open_dry_run_from_home_still_notes(self):
        p = self.run_aib("claude", "safe", "測試角色", "--dry-run")
        self.assertIn(NOTE, p.stderr, "開席時提示照印:%r" % (p.stderr[-400:],))


if __name__ == "__main__":
    unittest.main()
