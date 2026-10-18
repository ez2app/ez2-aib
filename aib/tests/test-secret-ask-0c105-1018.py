#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib secret ask:遮字輸入框貼金鑰 → Touch ID → 加密進金鑰櫃(維護者 10-18「彈出來一個加密對話框,可以輸入」)。

S1 格式:ANTHROPIC_API_KEY 要 sk-ant- 開頭;空、換行、過長一律擋;不認得格式的名字只擋空 / 換行 / 過長
S2 視窗:JXA 的字串都經 json 跳脫;只認 nonce 對得上的成功;取消 / 逾時 / 讀不懂 / nonce 不對都沒拿到值
S3 Mac 正路:輸入 → Touch ID 過 → 檔裡是 enc.v1. 密文、解得回原值;輸出只有名字、檔名、指紋,沒有值
S4 Touch ID 沒過 / AIB_TOUCHID=0 / 叫不出來 ⇒ 一個字都沒寫
S5 格式錯 ⇒ 再問(最多 3 次),第二次對就寫;3 次都錯 ⇒ bad_format、沒寫
S6 --check:供應商回 401 ⇒ check_rejected、沒寫、也沒叫 Touch ID;200 ⇒ checked
S7 --push:值只走 ssh 的 stdin,不在 argv;遠端指令 umask 077、600、暫存換名;壞主機 / 相對路徑 / 怪字元 / 壞帳號名 ⇒ rc 2
   沒輸入框;ssh 失敗 ⇒ rc 4(本機已寫)
S9 --remote-only:只送遠端、本機金鑰櫃不動;沒給 --push ⇒ rc 2 沒輸入框;遠端失敗 ⇒ rc 2
S8 不認得的名字 ⇒ rc 2;不是 Mac 又不是終端機 ⇒ no_tty
刀(複本):K1 Touch ID 沒過也寫 ⇒ S4 紅;K2 拿掉 sk-ant- 前綴檢查 ⇒ S5 紅
"""
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "src" / "bootstrap"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import secret_ask as SA  # noqa: E402
import secret_crypto as C  # noqa: E402

KEY = "sk-ant-api03-" + "Zx9" * 20
NAME = "ANTHROPIC_API_KEY"


class Fake:
    def __init__(self, values, touch=True, avail=True):
        self.values, self.touch, self.avail = list(values), touch, avail
        self.asks, self.touches, self.prompts = 0, 0, []

    def ask(self, prompt, title):
        self.asks += 1
        self.prompts.append(prompt)
        v = self.values.pop(0) if self.values else None
        return {"ok": False, "why": "cancel"} if v is None else {"ok": True, "why": "ok", "value": v}

    def touchid(self, reason):
        self.touches += 1
        self.reason = reason
        return {"ok": self.touch, "why": "ok" if self.touch else "cancel"}


class Resp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SecretAsk(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-secret-ask-")
        root = Path(self.td.name)
        self.sec = root / "secrets"
        self.env = {"HOME": str(root), "EZ2_AIB_STATE_ROOT": str(root / "state"), "EZ2_AIB_SECRETS_DIR": str(self.sec),
                    "EZ2_AIB_LOCALE": "zh-TW"}
        os.environ.update({"EZ2_AIB_STATE_ROOT": self.env["EZ2_AIB_STATE_ROOT"], "EZ2_AIB_SECRETS_DIR": str(self.sec)})

    def tearDown(self):
        self.td.cleanup()

    def run_cmd(self, argv, fake, env=None, platform="darwin", **kw):
        out = io.StringIO()
        rc = SA.ask_cmd(argv, env=dict(self.env, **(env or {})), platform=platform, ask=fake.ask, touchid=fake.touchid,
                        touchid_available=lambda: fake.avail, out=out, **kw)
        text = out.getvalue()
        self.assertNotIn(KEY, text)
        self.assertNotIn(KEY[10:30], text)
        return rc, json.loads(text.strip().splitlines()[-1])

    def stored(self):
        p = self.sec / "api-key-claude.env"
        if not p.is_file():
            return None
        rows = dict(ln.split("=", 1) for ln in p.read_text().splitlines() if "=" in ln and not ln.startswith("#"))
        return rows.get(NAME) or None

    def test_s1_format(self):
        self.assertIsNone(SA.check_format(NAME, KEY))
        self.assertEqual(SA.check_format(NAME, "sk-proj-abc"), "bad_prefix")
        self.assertEqual(SA.check_format(NAME, ""), "empty")
        self.assertEqual(SA.check_format(NAME, KEY + "\nX"), "newline")
        self.assertEqual(SA.check_format(NAME, "sk-ant-" + "a" * 5000), "too_long")
        self.assertIsNone(SA.check_format("DEEPSEEK_API_KEY", "anything-goes"))
        self.assertEqual(SA.check_format("DEEPSEEK_API_KEY", "a\rb"), "newline")

    def test_s2_dialog(self):
        def runner(reply):
            def run(cmd, input=None, **kw):
                self.assertEqual(cmd[:3], [SA.OSASCRIPT, "-l", "JavaScript"])
                self.assertIn('"say \\"hi\\"\\nnext"', input)            # 引號與換行都被跳脫
                nonce = re.search(r'nonce: "([0-9a-f]{32})"', input).group(1)
                return type("R", (), {"stdout": reply(nonce), "returncode": 0})()
            return run
        ok = SA.dialog('say "hi"\nnext', "t", run=runner(lambda n: json.dumps({"ok": True, "nonce": n, "value": " %s \n" % KEY})))
        self.assertEqual(ok, {"ok": True, "why": "ok", "value": KEY})
        self.assertEqual(SA.dialog('say "hi"\nnext', "t", run=runner(lambda n: json.dumps({"ok": False, "why": "cancel"})))["why"], "cancel")
        self.assertEqual(SA.dialog('say "hi"\nnext', "t", run=runner(lambda n: json.dumps({"ok": True, "nonce": "0" * 32, "value": KEY})))["why"], "nonce_mismatch")
        self.assertEqual(SA.dialog('say "hi"\nnext', "t", run=runner(lambda n: "ok"))["why"], "unreadable")
        self.assertNotIn("value", SA.dialog('say "hi"\nnext', "t", run=runner(lambda n: json.dumps({"ok": False, "why": "timeout"}))))

    def test_s3_mac_happy(self):
        f = Fake([KEY])
        rc, res = self.run_cmd([NAME], f)
        self.assertEqual(rc, 0, res)
        self.assertTrue(res["ok"])
        self.assertEqual(res["file"], "api-key-claude.env")
        self.assertEqual(res["fingerprint"], SA.fingerprint(KEY))
        env_val = self.stored()
        self.assertTrue(C.is_envelope(env_val))
        self.assertEqual(C.decrypt_value(env_val, NAME, self.sec), KEY)
        self.assertEqual(f.touches, 1)
        self.assertIn(NAME, f.reason)
        self.assertNotIn(KEY, f.reason)
        self.assertEqual(oct(os.stat(self.sec / "api-key-claude.env").st_mode & 0o777), "0o600")

    def test_s4_touchid_gate(self):
        for env, fake, why in (({}, Fake([KEY], touch=False), "touchid_cancel"),
                               ({"AIB_TOUCHID": "0"}, Fake([KEY]), "touchid_off"),
                               ({}, Fake([KEY], avail=False), "touchid_unavailable")):
            rc, res = self.run_cmd([NAME], fake, env=env)
            self.assertEqual((rc, res["why"], res["ok"]), (2, why, False))
            self.assertIsNone(self.stored(), why)

    def test_s5_retry(self):
        f = Fake(["sk-proj-wrong", KEY])
        rc, res = self.run_cmd([NAME], f)
        self.assertEqual((rc, f.asks), (0, 2))
        self.assertIn("sk-ant-", f.prompts[1])
        self.assertNotIn("sk-proj-wrong", f.prompts[1])
        self.assertEqual(C.decrypt_value(self.stored(), NAME, self.sec), KEY)
        self.tearDown(); self.setUp()
        f = Fake(["x1", "x2", "x3", KEY])
        rc, res = self.run_cmd([NAME], f)
        self.assertEqual((rc, res["why"], f.asks, f.touches), (2, "bad_format", 3, 0))
        self.assertIsNone(self.stored())

    def test_s6_check(self):
        import urllib.error

        def deny(req, timeout=None):
            self.assertEqual(req.get_header("X-api-key"), KEY)
            raise urllib.error.HTTPError(req.full_url, 401, "no", {}, None)
        f = Fake([KEY])
        rc, res = self.run_cmd([NAME, "--check"], f, opener=deny)
        self.assertEqual((rc, res["why"], f.touches), (2, "check_rejected", 0))
        self.assertIsNone(self.stored())
        f = Fake([KEY])
        rc, res = self.run_cmd([NAME, "--check"], f, opener=lambda req, timeout=None: Resp(200))
        self.assertEqual((rc, res["checked"]), (0, True))

    def test_s7_push(self):
        calls = []

        def ssh(cmd, input=None, **kw):
            calls.append((cmd, input))
            return type("R", (), {"returncode": 0})()
        f = Fake([KEY])
        rc, res = self.run_cmd([NAME, "--push", "eu01-ts:/home/app/daily/.secrets/anthropic.env", "--owner", "app"], f, ssh_run=ssh)
        self.assertEqual((rc, res["pushed"]), (0, "eu01-ts:/home/app/daily/.secrets/anthropic.env"))
        cmd, stdin = calls[0]
        self.assertEqual(stdin, "%s=%s\n" % (NAME, KEY))
        self.assertFalse(any(KEY in c for c in cmd))
        self.assertEqual(cmd[:6], ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--"])
        remote = cmd[-1]
        for part in ("umask 077", "chmod 600", "chown app:app", ".tmp-aib", "mv -f"):
            self.assertIn(part, remote)
        for bad in (["--push", "eu01:relative/path"], ["--push", "eu01:/a;rm -rf ~"], ["--push=-oProxyCommand=x:/a"],
                    ["--push", "eu01:/a/../etc/x"], ["--push", "eu01:/a", "--owner", "root;id"]):
            f = Fake([KEY])
            rc, res = self.run_cmd([NAME] + bad, f, ssh_run=ssh)
            self.assertEqual((rc, f.asks), (2, 0), bad)
        f = Fake([KEY])
        rc, res = self.run_cmd([NAME, "--push", "eu01:/a/b.env"], f, ssh_run=lambda *a, **k: type("R", (), {"returncode": 255})())
        self.assertEqual((rc, res["why"], res["ok"]), (4, "push_ssh_rc_255", False))
        self.assertEqual(C.decrypt_value(self.stored(), NAME, self.sec), KEY)

    def test_s8_misc(self):
        rc, res = self.run_cmd(["NOT_A_KNOWN_THING"], Fake([KEY]))
        self.assertEqual((rc, res["why"]), (2, "unknown_name"))
        rc, res = self.run_cmd([NAME], Fake([KEY]), platform="linux", isatty=lambda: False)
        self.assertEqual((rc, res["why"]), (2, "no_tty"))
        rc, res = self.run_cmd([NAME], Fake([]), platform="linux", isatty=lambda: True, getpass_fn=lambda p: KEY)
        self.assertEqual(rc, 0)
        self.assertEqual(C.decrypt_value(self.stored(), NAME, self.sec), KEY)


    def test_s9_remote_only(self):
        calls = []
        ok = lambda cmd, input=None, **kw: calls.append(input) or type("R", (), {"returncode": 0})()
        f = Fake([KEY])
        rc, res = self.run_cmd([NAME, "--push", "eu01:/a/b.env", "--remote-only"], f, ssh_run=ok)
        self.assertEqual((rc, res["pushed"], res["local"], f.touches), (0, "eu01:/a/b.env", False, 1))
        self.assertEqual(calls, ["%s=%s\n" % (NAME, KEY)])
        self.assertIsNone(self.stored())
        self.assertIn("eu01:/a/b.env", f.reason)
        f = Fake([KEY])
        rc, res = self.run_cmd([NAME, "--remote-only"], f)
        self.assertEqual((rc, res["why"], f.asks), (2, "remote_only_needs_push", 0))
        f = Fake([KEY])
        rc, res = self.run_cmd([NAME, "--push", "eu01:/a/b.env", "--remote-only"], f,
                               ssh_run=lambda *a, **k: type("R", (), {"returncode": 1})())
        self.assertEqual((rc, res["ok"]), (2, False))
        self.assertIsNone(self.stored())


class Knives(unittest.TestCase):
    def _red(self, needle, repl, test):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/secret_ask.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "SecretAsk." + test], cwd=str(root), max_procs=60, timeout=300)
        self.assertNotEqual(r.returncode, 0, "刀落了測試還綠:" + test)

    def test_k1_touchid_skipped(self):
        self._red('    return None if res.get("ok") is True else "touchid_"', '    return None\n    return None if res.get("ok") is True else "touchid_"',
                  "test_s4_touchid_gate")

    def test_k2_prefix_dropped(self):
        self._red("    if want and not value.startswith(want):\n", "    if False:\n", "test_s5_retry")


if __name__ == "__main__":
    unittest.main()
