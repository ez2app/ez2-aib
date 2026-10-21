#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib update 後艦長室自己換新碼重開(@abb89 10-21 派工 ②;伺服器端 aib-plugins #82 /api/reload)。

R1 沒在跑(沒 server.json / pid 不在)⇒ not-running、不送
R2 room_version 跟磁碟上外掛 version 一樣 ⇒ same、不送;沒裝外掛 ⇒ no-plugin、不送
R3 不一樣 / 舊伺服器沒帶 room_version ⇒ 送 /api/reload(url、token 照 server.json)⇒ 200 reloaded
R4 409 / 404 / 連不上 ⇒ failed + 印一行請本人重開;結束碼仍 0
R5 url 不是本機 http ⇒ 不送(token 不外流)
R6 真的起一台本機 HTTP:POST /api/reload、X-Captain-Token 帶上、沒有 Origin、body why=aib update
R7 aib update 腳本:升級成功才跑、在轉接程式 restart 之後、指到升級後那份 room_reload.py
R8 token 不出現在任何輸出;四語字典
刀(複本):K1 不比版本 ⇒ R2 紅;K2 update 腳本沒掛 ⇒ R7 紅
"""
import http.server
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
import room_reload as RR  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

TOKEN = "cap-" + "z" * 40


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-reload-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name)
        self.env = {"HOME": str(self.td), "EZ2_AIB_HOME": str(self.td / "aibhome"), "EZ2_AIB_STATE_ROOT": str(self.td / "state"),
                    "EZ2_AIB_LOCALE": "zh-TW"}
        self.posts = []

    def plugin(self, version="0.1.40"):
        d = Path(self.env["EZ2_AIB_HOME"]) / "plugins" / "captain-room"
        d.mkdir(parents=True, exist_ok=True)
        (d / "plugin.json").write_text(json.dumps({"name": "captain-room", "module": "x", "version": version,
                                                   "commands": {"room": "room.py"}}), encoding="utf-8")

    def server(self, room_version="0.1.39", url="http://127.0.0.1:8795/", pid=None):
        p = RR.server_json_path(self.env)
        p.parent.mkdir(parents=True, exist_ok=True)
        d = {"url": url, "token": TOKEN, "pid": pid or os.getpid(), "port": 8795}
        if room_version is not None:
            d["room_version"] = room_version
        p.write_text(json.dumps(d), encoding="utf-8")

    def post(self, code=200):
        def f(url, token):
            self.posts.append((url, token))
            return code
        return f

    def run_main(self, code=200, alive=None):
        buf = io.StringIO()
        rc = RR.main([], out=buf, env=self.env, post=self.post(code), alive=alive)
        return rc, buf.getvalue()


class Logic(Base):
    def test_r1_not_running(self):
        self.plugin()
        self.assertEqual(RR.reload_room(self.env, post=self.post())["result"], "not-running")
        self.server()
        self.assertEqual(RR.reload_room(self.env, post=self.post(), alive=lambda pid: False)["result"], "not-running")
        self.assertEqual(self.posts, [])

    def test_r2_same_version(self):
        self.server(room_version="0.1.40")
        self.assertEqual(RR.reload_room(self.env, post=self.post())["result"], "no-plugin")
        self.plugin("0.1.40")
        self.assertEqual(RR.reload_room(self.env, post=self.post())["result"], "same")
        self.assertEqual(self.posts, [])

    def test_r3_reload(self):
        self.plugin("0.1.40")
        self.server(room_version="0.1.39")
        rc, out = self.run_main(200)
        self.assertEqual(rc, 0)
        self.assertEqual(self.posts, [("http://127.0.0.1:8795/", TOKEN)])
        self.assertIn("0.1.39 → 0.1.40", out)
        self.server(room_version=None)            # 舊伺服器沒帶 room_version ⇒ 也送
        self.assertEqual(RR.reload_room(self.env, post=self.post(200))["result"], "reloaded")
        self.assertEqual(len(self.posts), 2)

    def test_r4_failed(self):
        self.plugin("0.1.40")
        self.server()
        for code in (409, 404, 0):
            rc, out = self.run_main(code)
            self.assertEqual(rc, 0, "升級本身成功,結束碼不變")
            self.assertIn("關掉艦長室再開", out, code)
        self.assertEqual(RR.reload_room(self.env, post=self.post(0))["why"], "unreachable")
        self.assertEqual(RR.reload_room(self.env, post=self.post(409))["why"], "http-409")

    def test_r5_non_local(self):
        self.plugin("0.1.40")
        for url in ("https://127.0.0.1:8795/", "http://evil.example/", "http://user@127.0.0.1:1/", "file:///etc/passwd"):
            self.server(url=url)
            res = RR.reload_room(self.env, post=self.post())
            self.assertEqual((res["result"], res["why"]), ("failed", "bad-server-json"), url)
        self.assertEqual(self.posts, [], "不是本機就不帶 token 出去")

    def test_r8_token_not_printed(self):
        self.plugin("0.1.40")
        self.server()
        for code in (200, 409):
            _rc, out = self.run_main(code)
            self.assertNotIn(TOKEN, out)
        buf = io.StringIO()
        RR.main(["--json"], out=buf, env=self.env, post=self.post(200))
        self.assertNotIn(TOKEN, buf.getvalue())
        self.assertEqual(json.loads(buf.getvalue())["result"], "reloaded")
        want = None
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            keys = {k for k in d if k.startswith("aib.room_reload.")}
            self.assertEqual(len(keys), 2, lang)
            for k in keys:
                self.assertIn("{new}", d[k], (lang, k))
            want = want or keys
            self.assertEqual(keys, want, lang)


class Wire(Base):
    def test_r6_real_http(self):
        seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                seen.append((self.path, self.headers.get("X-Captain-Token"), self.headers.get("Origin"), json.loads(self.rfile.read(n))))
                body = b'{"ok":true,"reloading":true}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        url = "http://127.0.0.1:%d/" % srv.server_address[1]
        self.assertEqual(RR.post_reload(url, TOKEN), 200)
        self.assertEqual(seen, [("/api/reload", TOKEN, None, {"why": "aib update"})])
        self.assertEqual(RR.post_reload("http://127.0.0.1:1/", TOKEN, timeout=1), 0, "連不上 ⇒ 0")

    def test_r7_update_script(self):
        sys.path.insert(0, str(REPO.parent))
        spec = importlib.util.spec_from_file_location("aib_cli_reload", str(REPO / "cli.py"))
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        seen = {}

        def fake_execv(prog, argv):
            seen["argv"] = argv
            seen["room"] = os.environ.get("AIB_UPDATE_ROOM")
            raise SystemExit(0)
        with mock.patch.object(cli.os, "execv", fake_execv), mock.patch.object(cli.os.path, "isdir", lambda p: True), \
                mock.patch.dict(os.environ, {}, clear=False):
            with self.assertRaises(SystemExit):
                cli._update([])
        script = seen["argv"][2]
        line = '[ "$rc" -ne 0 ] || [ ! -f "$AIB_UPDATE_ROOM" ] || "$AIB_UPDATE_PY" "$AIB_UPDATE_ROOM" >&2 || true;'
        self.assertIn(line, script)
        self.assertLess(script.index('"$AIB_UPDATE_BRIDGE" restart'), script.index(line), "轉接程式先重啟,再叫艦長室")
        self.assertLess(script.index(line), script.index('exit "$rc"'))
        self.assertTrue(seen["room"] and seen["room"].endswith("src/cockpit/room_reload.py") and os.path.isfile(seen["room"]))


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_no_version_check(self):
        self._knife("src/cockpit/room_reload.py", "    if running == disk:\n", "    if False:\n", "Logic.test_r2_same_version")

    def test_k2_update_not_wired(self):
        self._knife("cli.py", '''              '[ "$rc" -ne 0 ] || [ ! -f "$AIB_UPDATE_ROOM" ] || "$AIB_UPDATE_PY" "$AIB_UPDATE_ROOM" >&2 || true; '\n''', "",
                    "Wire.test_r7_update_script")


if __name__ == "__main__":
    unittest.main()
