#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""`aib`(不帶參數)預設開艦長室 + aib ui room|terminal(10-20 Andy 裁;C8 第一版)。

U1 aib ui:預設 room;set terminal / room 寫 <狀態根>/ui.json;壞值 ⇒ usage rc 2
U2 room 模式、伺服器活著、有 Chrome ⇒ 開應用程式視窗:--app=<url>、--user-data-dir 在狀態根底下(不碰平常的 Chrome)、
   --no-first-run --no-default-browser-check;Chrome > Edge > Brave;印「獨立視窗」那行,rc 0
U3 三個都沒有 ⇒ `open <url>`(預設瀏覽器)+ 印說明,rc 0
U4 伺服器沒在跑 ⇒ 叫 aib room open 起它,而且 PATH 最前面有假的 open(擋系統瀏覽器);起不來 ⇒ 印原因 rc 2
U5 外掛沒裝 ⇒ 印安裝提示 rc 2(不叫任何東西)
U6 terminal 模式 ⇒ cli.main 印說明 rc 2,不開視窗;room 模式 ⇒ cli.main 走 open_room
U7 四語字典 11 條都在
刀(複本):K1 拿掉 --user-data-dir ⇒ U2 紅(會碰使用者平常的 Chrome profile)
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

URL = "http://127.0.0.1:8770/"


class Box(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-ui-")
        self.tmp = Path(self.td.name)
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.envp = mock.patch.dict(os.environ, {"HOME": str(self.tmp), "EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_AIB_LOCALE": "zh-TW",
                                                 "AIB_UI_AIB_BIN": "/fake/aib"})
        self.envp.start()
        sys.modules.pop("aib_ui", None)
        import aib_ui
        self.UI = aib_ui
        self.runs, self.popens = [], []

    def tearDown(self):
        self.envp.stop()
        self.td.cleanup()

    def fake_run(self, alive=True, after_open=True, no_browser=False):
        state = {"alive": alive}

        def run(argv, **kw):
            self.runs.append((argv, kw))
            if argv[1:4] == ["room", "open", "--help"]:
                return subprocess.CompletedProcess(argv, 0, stdout="usage: aib room open [--cabin C] [--port P]" + (" [--no-browser]" if no_browser else ""), stderr="")
            if argv[1:3] == ["room", "status"]:
                return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"alive": state["alive"], "url": URL, "pid": 1}), stderr="")
            if argv[1:3] == ["room", "open"]:
                if "--no-browser" in argv and not no_browser:
                    return subprocess.CompletedProcess(argv, 2, stdout="", stderr="unrecognized arguments: --no-browser")
                state["alive"] = after_open
                return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return run

    def fake_popen(self, argv, **kw):
        self.popens.append((argv, kw))
        return mock.Mock(pid=4242)

    def exists_for(self, *names):
        return lambda p: any(n in p for n in names)


class Ui(Box):
    def test_u1_mode(self):
        buf = io.StringIO()
        self.assertEqual(self.UI.get_mode(), "room")
        self.assertEqual(self.UI.main(["terminal"], out=buf), 0)
        self.assertEqual(json.loads((self.state / "ui.json").read_text())["ui"], "terminal")
        self.assertEqual(self.UI.get_mode(), "terminal")
        self.assertEqual(self.UI.main(["room"], out=buf), 0)
        self.assertEqual(self.UI.get_mode(), "room")
        self.assertEqual(self.UI.main(["bogus"], out=buf), 2)
        self.assertEqual(self.UI.main([], out=buf), 0)
        self.assertIn("艦長室", buf.getvalue())

    def test_u2_app_window(self):
        buf = io.StringIO()
        rc = self.UI.open_room(out=buf, run=self.fake_run(), popen=self.fake_popen, exists=self.exists_for("Google Chrome", "Brave"), installed=True)
        self.assertEqual(rc, 0, buf.getvalue())
        argv, kw = self.popens[-1]
        self.assertIn("Google Chrome", argv[0], "Chrome 優先")
        self.assertIn("--app=" + URL, argv)
        prof = next(a for a in argv if a.startswith("--user-data-dir="))
        self.assertTrue(prof.split("=", 1)[1].startswith(str(self.state)), prof)
        self.assertTrue(Path(prof.split("=", 1)[1]).is_dir())
        self.assertIn("--no-first-run", argv)
        self.assertIn("--no-default-browser-check", argv)
        self.assertTrue(kw.get("start_new_session"))
        self.assertIn("獨立視窗", buf.getvalue())
        self.assertFalse(any(a[0][:1] == ["open"] for a, _ in self.runs), "有 app 視窗就不開預設瀏覽器")
        self.popens.clear()
        self.UI.open_room(out=io.StringIO(), run=self.fake_run(), popen=self.fake_popen, exists=self.exists_for("Brave"), installed=True)
        self.assertIn("Brave", self.popens[-1][0][0])

    def test_u3_fallback_browser(self):
        buf = io.StringIO()
        with mock.patch.object(self.UI.sys, "platform", "darwin"):
            rc = self.UI.open_room(out=buf, run=self.fake_run(), popen=self.fake_popen, exists=lambda p: False, installed=True)
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertEqual(self.popens, [])
        self.assertTrue(any(a == ["open", URL] for a, _ in self.runs), self.runs)
        self.assertIn("預設瀏覽器", buf.getvalue())

    def test_u4_start_server(self):
        buf = io.StringIO()
        rc = self.UI.open_room(out=buf, run=self.fake_run(alive=False, after_open=True), popen=self.fake_popen,
                               exists=self.exists_for("Google Chrome"), installed=True)
        self.assertEqual(rc, 0, buf.getvalue())
        opens = [(a, k) for a, k in self.runs if a[1:3] == ["room", "open"] and a[3:4] != ["--help"]]
        self.assertEqual(len(opens), 1)
        first = opens[0][1]["env"]["PATH"].split(os.pathsep)[0]
        self.assertIn("aib-ui-noopen", first, "舊外掛:PATH 最前面要是擋系統瀏覽器的假 open")
        self.assertNotIn("--no-browser", opens[0][0], "舊外掛不認 --no-browser,不能帶")
        self.runs.clear()
        rc = self.UI.open_room(out=io.StringIO(), run=self.fake_run(alive=False, after_open=True, no_browser=True), popen=self.fake_popen,
                               exists=self.exists_for("Google Chrome"), installed=True)
        self.assertEqual(rc, 0)
        opens = [(a, k) for a, k in self.runs if a[1:3] == ["room", "open"] and a[3:4] != ["--help"]]
        self.assertEqual(opens[0][0][3:], ["--no-browser"], "新外掛(0.1.33+):帶 --no-browser、不用假 open")
        self.assertNotIn("env", opens[0][1])
        self.popens.clear()
        buf = io.StringIO()
        rc = self.UI.open_room(out=buf, run=self.fake_run(alive=False, after_open=False), popen=self.fake_popen,
                               exists=self.exists_for("Google Chrome"), installed=True)
        self.assertEqual(rc, 2)
        self.assertIn("起不來", buf.getvalue())
        self.assertEqual(self.popens, [], "起不來就不開視窗")

    def test_u5_plugin_missing(self):
        buf = io.StringIO()
        rc = self.UI.open_room(out=buf, run=self.fake_run(), popen=self.fake_popen, exists=self.exists_for("Google Chrome"), installed=False)
        self.assertEqual(rc, 2)
        self.assertIn("plugins install", buf.getvalue())
        self.assertEqual((self.runs, self.popens), ([], []))

    def test_u6_cli_entry(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("aib_cli_ui_test", str(REPO / "cli.py"))
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        self.UI.set_mode("terminal")
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["aib"]), mock.patch.object(sys, "stdout", buf), \
                mock.patch.object(self.UI, "open_room", side_effect=AssertionError("terminal 模式不准開")):
            self.assertEqual(cli.main(), 2)
        self.assertIn("aib", buf.getvalue())
        self.UI.set_mode("room")
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["aib"]), mock.patch.object(sys, "stdout", buf), \
                mock.patch.object(self.UI, "open_room", return_value=0) as m:
            self.assertEqual(cli.main(), 0)
        self.assertEqual(m.call_count, 1)
        self.assertEqual(buf.getvalue(), "", "開了艦長室就不印說明")
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["aib"]), mock.patch.object(sys, "stdout", buf), \
                mock.patch.object(self.UI, "open_room", return_value=2):
            self.assertEqual(cli.main(), 2)
        self.assertIn("aib", buf.getvalue(), "開不了就退回印說明")
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["aib", "ui"]), mock.patch.object(sys, "stdout", buf):
            self.assertEqual(cli.main(), 0)
        self.assertIn("艦長室", buf.getvalue())

    def test_u7_dict(self):
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for k in ("usage", "mode_room", "mode_terminal", "set_room", "set_terminal", "room_missing", "room_failed", "opened_app",
                      "opened_browser", "open_failed"):
                self.assertIn("aib.ui." + k, d, (loc, k))
            self.assertIn("{why}", d["aib.ui.room_failed"])
            self.assertIn("{url}", d["aib.ui.open_failed"])


class Knife(unittest.TestCase):
    def test_k1_no_profile_dir(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/aib_ui.py", '"--user-data-dir=" + str(profile), ', "", tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Ui.test_u2_app_window"], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
