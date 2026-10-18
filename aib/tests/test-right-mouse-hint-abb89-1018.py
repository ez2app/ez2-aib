#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""右欄 AI 清單最底的滑鼠提示(10-18 Andy 准:終端機「允許滑鼠回報」被 ⌘R 關掉時,點擊全被終端機接走、艙裡偵測不到)。

H1 四語都有 mouse_hint1 / mouse_hint2,且一行塞得進右欄(可見寬 + 前導空白 ≤ 右欄寬 - 1,不被截成 …)
H2 提示加在 render_ai_list_with_map 之後、raw_lines 之前,只往後加 —— 前面每一列的行號(rowmap)不變
H3 只在 macOS 顯示
K1 刀:提示改成插在最前面 ⇒ H2 紅
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "cockpit" / "render"))
from render_lib import DEFAULT_PANE_W, visible_width  # noqa: E402

SH = ROOT / "src" / "cockpit" / "pane-right-ai.sh"


def appended_after_render(src):
    """提示那段必須在 render_ai_list_with_map(...) 之後、raw_lines 之前,而且是 list(lines) + [...](往後加)。"""
    i = src.find("lines, rowmap = render_ai_list_with_map(render_data, width=width)")
    j = src.find("raw_lines, raw_rowmap = list(lines), list(rowmap)", i)
    if i < 0 or j < 0:
        return False
    seg = src[i:j]
    return "mouse_hint1" in seg and re.search(r"lines = list\(lines\) \+ \[", seg) is not None


class MouseHint(unittest.TestCase):
    def test_h1_i18n_fits(self):
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for k in ("aib.rightai.mouse_hint1", "aib.rightai.mouse_hint2"):
                self.assertIn(k, d, (loc, k))
                self.assertLessEqual(visible_width(" " + d[k]), DEFAULT_PANE_W - 1, (loc, k, d[k]))

    def test_h2_appended_only(self):
        self.assertTrue(appended_after_render(SH.read_text(encoding="utf-8")))

    def test_h3_darwin_only(self):
        src = SH.read_text(encoding="utf-8")
        seg = src[src.find("lines, rowmap = render_ai_list_with_map(render_data, width=width)"):src.find("raw_lines, raw_rowmap = list(lines), list(rowmap)")]
        self.assertIn("if sys.platform == 'darwin':", seg)

    def test_k1_knife_prepend(self):
        src = SH.read_text(encoding="utf-8")
        cut = src.replace("lines = list(lines) + ['', ' \\033[2m'", "lines = ['', ' \\033[2m'", 1)
        self.assertNotEqual(src, cut, "刀沒落下")
        cut = cut.replace("'\\033[0m']\n    raw_lines", "'\\033[0m'] + list(lines)\n    raw_lines", 1)
        self.assertFalse(appended_after_render(cut), "刀沒砍出紅:插在前面也過了")


if __name__ == "__main__":
    unittest.main()
