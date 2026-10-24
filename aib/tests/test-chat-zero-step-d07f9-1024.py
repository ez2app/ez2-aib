#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""開通後不用再做別的(10-24 Andy「全照做」清單第 9 條)。開艙仍問一次(10-16 裁示),答「要」之後自動補齊。

Z1 default_name:macOS 用「電腦名稱」(scutil);不合格(含 >> / 太長截 32 字)⇒ 退主機名;其他平台用主機名
Z2 default_country:AppleLocale zh_TW / en_US@rg=gbzzzz / zh_Hant_TW;非 Mac 看 LANG;查不到 ⇒ None
Z3 zero_step:沒名字 / 國別 ⇒ 補上;公開艦長席(role root),不公開其他席;已經設過的名字、國別、公開清單不動
Z4 國別查不到 ⇒ 不公開任何席、印「要給顯示名稱與國別」(不上線半套)
Z5 aib chat auto 答「要」(假終端機)⇒ 問句帶要公開的名字;開關檔、名字、國別、艦長席都好了
(Z5 不裝 nostr-sdk:venv 先連到測試 venv 或放假的 ok;不連網)
刀(複本):K1 zero_step 公開所有席(不看 role)⇒ Z3 紅
"""
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import chat_roster as CR  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_bridge_zero_step", str(COCKPIT / "chat_bridge.py"))
CB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CB)


def fake_run(answers):
    def run(cmd, **_k):
        out = answers.get(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0 if out is not None else 1, stdout=(out or "") + "\n", stderr="")
    return run


class Defaults(unittest.TestCase):
    def test_z1_name(self):
        self.assertEqual(CB.default_name(run=fake_run({"ComputerName": "Andy 的 MacBook Pro"}), platform="darwin"),
                         "Andy 的 MacBook Pro")
        self.assertEqual(len(CB.default_name(run=fake_run({"ComputerName": "x" * 50}), platform="darwin")), 32)
        host = socket.gethostname().split(".")[0]
        self.assertEqual(CB.default_name(run=fake_run({"ComputerName": "a>>b"}), platform="darwin"),
                         CR.GF.clean_display_name(host[:32]))
        self.assertEqual(CB.default_name(run=fake_run({}), platform="linux"), CR.GF.clean_display_name(host[:32]))

    def test_z2_country(self):
        mac = lambda loc: CB.default_country({}, run=fake_run({"AppleLocale": loc}), platform="darwin")  # noqa: E731
        self.assertEqual([mac("zh_TW"), mac("en_US@rg=gbzzzz"), mac("zh_Hant_TW"), mac("ja_JP")], ["TW", "GB", "TW", "JP"])
        self.assertIsNone(CB.default_country({}, run=fake_run({}), platform="darwin"))
        self.assertEqual(CB.default_country({"LANG": "ko_KR.UTF-8"}, run=fake_run({}), platform="linux"), "KR")
        self.assertIsNone(CB.default_country({"LANG": "C.UTF-8"}, run=fake_run({}), platform="linux"))


class Zero(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-zero-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.env = {"HOME": str(self.td), "EZ2_AIB_STATE_ROOT": str(self.td / ".ez2-aib")}
        d = self.td / ".ez2-aib" / "instances" / "20261024-120000-6ddf" / "seats"
        d.mkdir(parents=True)
        for code, role in (("cfd04", "root"), ("d07f9", "dev")):
            (d / ("w%s.json" % code)).write_text(json.dumps({"schema": "aib-seat/v1", "role": role,
                                                             "session_uuid": "00000000-0000-0000-0000-0000000" + code}))
        orig = (CB.default_name, CB.default_country, CB.venv_state)
        self.addCleanup(lambda: (setattr(CB, "default_name", orig[0]), setattr(CB, "default_country", orig[1]),
                                 setattr(CB, "venv_state", orig[2])))
        CB.default_name = lambda env=None: "Mac Pro 13"
        CB.default_country = lambda env=None: "TW"
        CB.venv_state = lambda env=None: "ok"

    def run_zero(self):
        import io
        buf = io.StringIO()
        CB.zero_step(self.env, buf)
        return buf.getvalue()

    def test_z3_fill_and_publish_captain(self):
        out = self.run_zero()
        prof = CR.read_profile(self.env)
        self.assertEqual((prof["display_name"], prof["country"], prof["public"]), ("Mac Pro 13", "TW", ["cfd04"]))
        self.assertIn("@cfd04", out)
        CR.set_profile("Andy", "JP", self.env)
        CR.set_public("cfd04", False, self.env)
        CR.set_public("d07f9", True, self.env)
        self.run_zero()
        prof = CR.read_profile(self.env)
        self.assertEqual((prof["display_name"], prof["country"], prof["public"]), ("Andy", "JP", ["d07f9"]))

    def test_z4_no_country(self):
        CB.default_country = lambda env=None: None
        out = self.run_zero()
        prof = CR.read_profile(self.env) or {}
        self.assertEqual(prof.get("public") or [], [])
        self.assertIsNone(prof.get("country"))
        self.assertIn("aib chat on --name", out)

    def test_z5_auto_tty(self):
        import pty
        import select
        import time
        (self.td / ".ez2-aib" / "chat" / "venv" / "bin").mkdir(parents=True)
        env = dict(os.environ, **self.env, AIB_LANG="zh-TW", AIB_CHAT_ASK_TIMEOUT="10", PYTHONPATH=str(self.td / "shim"))
        shim = self.td / "shim"
        shim.mkdir()
        (shim / "sitecustomize.py").write_text(
            "import sys\nsys.path.insert(0, %r)\nimport chat_bridge as CB\n"
            "CB.default_name = lambda env=None: 'Mac Pro 13'\nCB.default_country = lambda env=None: 'TW'\n"
            "CB.venv_state = lambda env=None: 'ok'\n" % str(COCKPIT))
        runner = self.td / "run.py"
        runner.write_text("import sys, chat_bridge\nsys.exit(chat_bridge.main(sys.argv[1:]))\n")
        m, s_ = pty.openpty()
        p = subprocess.Popen([sys.executable, str(runner), "auto"], stdin=s_, stdout=s_, stderr=s_, env=env, close_fds=True)
        os.close(s_)
        buf, sent, end = b"", False, time.time() + 20
        while time.time() < end:
            r, _w, _x = select.select([m], [], [], 0.2)
            if r:
                try:
                    chunk = os.read(m, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                if not sent and b"[y/N]" in buf:
                    os.write(m, b"y\n")
                    sent = True
            elif p.poll() is not None:
                break
        p.wait(timeout=10)
        os.close(m)
        text = buf.decode("utf-8", "replace")
        self.assertEqual(p.returncode, 0, text)
        self.assertIn("Mac Pro 13", text.split("[y/N]")[0])
        self.assertTrue(CB.is_enabled(self.env))
        prof = CR.read_profile(self.env)
        self.assertEqual((prof["display_name"], prof["country"], prof["public"]), ("Mac Pro 13", "TW", ["cfd04"]))


class Knife(unittest.TestCase):
    def test_k1_publish_all(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_bridge.py", '            if s.get("role") == "root":\n',
                        "            if True:\n", tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Zero.test_z3_fill_and_publish_captain"],
                            cwd=str(root), max_procs=60, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
