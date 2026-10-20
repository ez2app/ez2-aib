#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""Ez2 截圖編輯器開在框選那台螢幕(10-21 維護者:「在其他延伸畫面截圖會錯誤」)。

接了延伸螢幕、在副螢幕框選 ⇒ 編輯器用 mainScreen 量尺寸再 win.center ⇒ 開到主螢幕(看不到),
縮放比率也拿主螢幕的(Retina 2x 配外接 1x ⇒ 視窗大小差一倍)。

D1 ez2-shot.js 用 pickScreen()(游標所在那台)量 visibleFrame 和 backingScaleFactor,不再直接用 mainScreen
D2 不用 win.center(只會置中在主螢幕);改 setFrameOrigin 置中在那台的 visibleFrame
D3 pickScreen 找不到游標那台 ⇒ 退回 mainScreen
D4 macOS 上真的跑一次 pickScreen:回傳的螢幕要包住游標(只有一台螢幕也要過)
D5 語法過得了(有 node 才驗)
刀(手動):把 var screen = pickScreen() 改回 mainScreen ⇒ D1 紅
"""
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # = aib/
SHOT = ROOT / "src" / "shot" / "editor" / "ez2-shot.js"


def _src():
    return SHOT.read_text(encoding="utf-8")


def _fn(src, name):
    m = re.search(r"^function " + name + r"\(\) \{\n.*?^\}\n", src, re.S | re.M)
    return m.group(0) if m else ""


class TestShotDisplay(unittest.TestCase):
    def test_d1_size_from_cursor_screen(self):
        run = _src().split("function run(", 1)[1]
        self.assertIn("var screen = pickScreen();", run)
        self.assertNotIn("$.NSScreen.mainScreen", run)
        self.assertIn("screen.backingScaleFactor", run)

    def test_d2_center_on_that_screen(self):
        run = _src().split("function run(", 1)[1]
        self.assertNotRegex(run, r"win\.center\s*;")
        self.assertIn("win.setFrameOrigin($.NSMakePoint(vis.origin.x + (vis.size.width - w) / 2, vis.origin.y + (vis.size.height - h) / 2))", run)

    def test_d3_fallback_main(self):
        fn = _fn(_src(), "pickScreen")
        self.assertTrue(fn, "pickScreen 不見了")
        self.assertIn("$.NSEvent.mouseLocation", fn)
        self.assertIn("$.NSScreen.screens", fn)
        self.assertTrue(fn.rstrip().endswith("return $.NSScreen.mainScreen;\n}") or "return $.NSScreen.mainScreen;\n}" in fn)

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("osascript"), "只在 macOS 驗")
    def test_d4_real_pick_contains_cursor(self):
        fn = _fn(_src(), "pickScreen")
        js = 'ObjC.import("AppKit");\n' + fn + (
            'var s = pickScreen(), f = s.frame, p = $.NSEvent.mouseLocation;\n'
            '(p.x >= f.origin.x && p.x <= f.origin.x + f.size.width && p.y >= f.origin.y && p.y <= f.origin.y + f.size.height) ? "in" : "out";\n')
        r = subprocess.run(["osascript", "-l", "JavaScript", "-e", js], capture_output=True, text=True, timeout=30)
        if r.returncode != 0 and not r.stdout.strip():
            self.skipTest("這台跑不了 JXA:" + r.stderr.strip()[:120])
        self.assertEqual(r.stdout.strip(), "in", r.stderr)

    @unittest.skipUnless(shutil.which("node"), "沒有 node")
    def test_d5_syntax(self):
        r = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(process.argv[1],'utf8'))", str(SHOT)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
