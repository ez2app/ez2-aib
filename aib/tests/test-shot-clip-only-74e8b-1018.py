#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""Ez2 截圖編輯器「只存剪貼簿」(10-18 維護者:「截圖裡的存回,應該新增一個只存剪貼簿」)。

C1 editor.js:存回旁邊有「只存剪貼簿」鈕,送 cliponly(帶 PNG);舊的「複製到剪貼簿」圖示拿掉(只留一個);ez2-shot.js 收到 ⇒ copyPng、last-save 標 clipboard、finish 關掉
C2 shot_helper.edit_and_deliver:last-save 標 clipboard ⇒ 回 clipboard=True,不送任何席(就算編輯器開時有預設席)
C3 一般存回選了席 ⇒ 照舊送(回歸)
C4 兩支 JS 語法過得了(有 node 才驗)
刀(手動):拿掉 edit_and_deliver 的 clipboard 判斷 ⇒ C2 紅
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # = aib/
COCKPIT = ROOT / "src" / "cockpit"
EDITOR = ROOT / "src" / "shot" / "editor"


def _load_shot_helper():
    if str(COCKPIT) not in sys.path:
        sys.path.insert(0, str(COCKPIT))
    spec = importlib.util.spec_from_file_location("shot_helper_clip", COCKPIT / "shot_helper.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class P:
    def __init__(self, rc=0):
        self.returncode, self.stdout, self.stderr = rc, "", ""


class ClipOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shot-clip-"))
        self.S = _load_shot_helper()
        self.S.shot_home = lambda home=None: self.tmp
        self.S.write_seats_file = lambda **kw: [{"addr": "6ddf/0c105", "label": "x", "method": "key"}]
        self.S._log = lambda *a, **k: None
        self.sent = []
        self.S.deliver = lambda addr, path, **kw: self.sent.append((addr, path)) or {"rc": 1, "reason": "not_seen"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _editor_writes(self, doc):
        def run(argv, timeout=60, env=None):
            (self.tmp / "last-save.json").write_text(json.dumps(doc))
            return P(0)
        self.S._run = run

    def test_c1_wired(self):
        ed = (EDITOR / "editor.js").read_text(encoding="utf-8")
        self.assertIn('native({ cmd: "cliponly", png: pngB64(out) })', ed)
        self.assertIn("只存剪貼簿", ed)
        self.assertLess(ed.index("bar.appendChild(clipBtn)"), ed.index("bar.appendChild(saveBtn)"))   # 在存回旁邊(左邊)
        self.assertNotIn("copyBtn", ed)                                                                # 舊的「複製到剪貼簿」圖示拿掉(維護者:重複,留一個)
        self.assertEqual(ed.count('annoBtn("copy"'), 1)
        js = (EDITOR / "ez2-shot.js").read_text(encoding="utf-8")
        i = js.index('m.cmd === "cliponly"')
        branch = js[i:js.index("} else if", i)]
        for need in ("copyPng(m.png)", "writeClipOnly(", 'finish("clipboard")'):
            self.assertIn(need, branch)
        self.assertIn("clipboard: !!ok", js)

    def test_c2_clipboard_no_deliver(self):
        self._editor_writes({"path": "", "seat": "", "clipboard": True})
        out = self.S.edit_and_deliver("/tmp/x.png", {"shot_dir": str(self.tmp), "sock_dir": str(self.tmp)}, prefer="6ddf/0c105")
        self.assertTrue(out.get("clipboard"))
        self.assertNotIn("deliver", out)
        self.assertEqual(self.sent, [])

    def test_c3_save_still_delivers(self):
        self._editor_writes({"path": "/v/a.png", "seat": "6ddf/0c105", "png": "", "note": ""})
        out = self.S.edit_and_deliver("/tmp/x.png", {"shot_dir": str(self.tmp), "sock_dir": str(self.tmp)})
        self.assertNotIn("clipboard", out)
        self.assertEqual(self.sent, [("6ddf/0c105", "/v/a.png")])

    @unittest.skipIf(not shutil.which("node"), "沒有 node")
    def test_c4_js_syntax(self):
        for name in ("editor.js", "ez2-shot.js"):
            p = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(process.argv[1],'utf8'))", str(EDITOR / name)],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 0, name + p.stderr[-300:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
