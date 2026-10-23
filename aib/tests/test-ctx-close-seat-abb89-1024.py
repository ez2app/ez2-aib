#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""右鍵「關閉這一席」(10-24 @abb89,右鍵 14 家實測第 7 項;Andy「依照你的建議開始修到全好」)。

以前要關一席只能瞄準那一列尾巴的 [X] / (x) 小格;右鍵選單沒有關閉。
現在右鍵那一列多一項「關閉這一席」,點了彈的是跟點 [X] **同一個**確認選單(取消排第一、
真關只在 --release-confirmed、軟關照樣帶 --soft-since)—— 不另開一條關法、不放寬誰能被關:
只有那一列本來就有 [X] / (x) 才出現。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COCKPIT = REPO / "src" / "cockpit"
MOUSE = COCKPIT / "mouse"
sys.path.insert(0, str(MOUSE))

import hit_test  # noqa: E402

IDLE = {"line": 3, "window": "@35", "kill": {"start": 20, "end": 23, "soft": True},
        "detail": {"kind": "seat", "label": "dev1@4fc4f", "engine": "codex", "context": "", "status": "💤"}}
HARD = {"line": 5, "window": "@37", "kill": {"start": 20, "end": 23},
        "detail": {"kind": "seat", "label": "dev3@bbbbb", "engine": "codex", "context": "", "status": "💤"}}
BUSY = {"line": 4, "window": "@36",
        "detail": {"kind": "seat", "label": "dev2@aaaaa", "engine": "claude", "context": "", "status": "忙"}}


class HitClose(unittest.TestCase):
    def test_same_action_as_clicking_x(self):
        for row in (IDLE, HARD):
            want = hit_test.hit_ai_rows([row], row["line"], 21)
            self.assertEqual(want.get("action"), "kill-idle")
            self.assertEqual(hit_test.hit_ai_close([row], row["line"]), want)    # 整列都算,x 不必瞄準

    def test_soft_flag_carried(self):
        self.assertIs(hit_test.hit_ai_close([IDLE], 3)["soft"], True)
        self.assertIs(hit_test.hit_ai_close([HARD], 5)["soft"], False)

    def test_no_x_no_close(self):
        self.assertEqual(hit_test.hit_ai_close([BUSY], 4), hit_test.PASSTHROUGH)
        self.assertEqual(hit_test.hit_ai_close([IDLE], 9), hit_test.PASSTHROUGH)
        self.assertEqual(hit_test.hit_ai_close([dict(IDLE, kill_pending=True)], 3), hit_test.PASSTHROUGH)
        self.assertEqual(hit_test.hit_ai_close([IDLE], "x"), hit_test.PASSTHROUGH)


class Menu(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.st = self.tmp / "st"
        self.st.mkdir()
        (self.st / "ai-rowmap.json").write_text(json.dumps([IDLE, BUSY, HARD], ensure_ascii=False), encoding="utf-8")
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.tmp), "HOME": str(self.tmp), "EZ2_COCKPIT_STATE_DIR": str(self.st),
                    "EZ2_PY3": sys.executable, "AIB_LANG": "zh-TW", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}

    def tearDown(self):
        self._tmp.cleanup()

    def run_ctl(self, *args):
        r = subprocess.run(["bash", str(MOUSE / "mouse-ctl.sh"), *args, "--dry-run"], env=self.env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def label(self):
        import i18n_lib
        return i18n_lib.t("aib.context.menu.close_seat", locale="zh-TW")

    def test_context_menu_has_close_only_for_rows_with_x(self):
        sys.path.insert(0, str(COCKPIT))
        out = self.run_ctl("--context", "pane", "ai-list", "5", "3", "%1", "s1")
        self.assertIn(self.label(), out)
        self.assertIn("--close-row ai-list 5 3", out)
        self.assertIn("--role right", out)
        busy = self.run_ctl("--context", "pane", "ai-list", "5", "4", "%1", "s1")
        self.assertNotIn(self.label(), busy)
        self.assertNotIn("--close-row", busy)

    def test_close_row_opens_the_x_confirm_menu(self):
        out = self.run_ctl("--role", "right", "--close-row", "ai-list", "5", "3", "s1")
        menu = next(l for l in out.splitlines() if "display-menu" in l)
        self.assertIn("dev1@4fc4f", menu)                                  # 標題帶身分
        self.assertLess(menu.index("release cancelled"), menu.index("--release-confirmed @35"))   # 取消排第一
        self.assertIn("--soft-since", menu)                                # 軟關語意照舊
        hard = next(l for l in self.run_ctl("--role", "right", "--close-row", "ai-list", "5", "5", "s1").splitlines()
                    if "display-menu" in l)
        self.assertIn("--release-confirmed @37", hard)
        self.assertNotIn("--soft-since", hard)

    def test_close_row_on_row_without_x_does_nothing(self):
        out = self.run_ctl("--role", "right", "--close-row", "ai-list", "5", "4", "s1")
        self.assertNotIn("display-menu", out)
        self.assertNotIn("release-confirmed", out)

    def test_close_seat_label_in_every_language(self):
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8"))
            self.assertTrue(d.get("aib.context.menu.close_seat"), lang)

    def test_no_bash4_only_syntax(self):
        src = (MOUSE / "mouse-ctl.sh").read_text(encoding="utf-8")
        self.assertNotIn(";&\n", src)                                       # Mac 內建 bash 3.2 不認


if __name__ == "__main__":
    unittest.main()
