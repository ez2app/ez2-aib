#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""FluentSMTP(WordPress 寄信)的 SES 金鑰:aib secret ask 認得名字，但只准 --remote-only(Andy 10-24「彈出一個視窗讓我設定key」)。

F1 兩個名字落 fluentmail.env 這一類(不再 unknown_name),列在 REMOTE_ONLY_NAMES
F2 --push … --remote-only:輸入框 → Touch ID → 值只走 ssh stdin 送到遠端;本機金鑰櫃沒有這個檔
F3 沒給 --remote-only(只存本機、或本機 + 遠端)⇒ remote_only_name、rc 2,沒叫輸入框、沒叫 Touch ID、沒 ssh
F4 put_secret 直接寫也拒(remote_only),檔案沒建
刀:K1 拿掉 secret_ask 的擋 ⇒ F3 紅;K2 拿掉 put_secret 的擋 ⇒ F4 紅
"""
import io
import json
import os
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
import aib_secrets as S  # noqa: E402
import secret_ask as SA  # noqa: E402
import secret_collect as SC  # noqa: E402

KEY_ID = "AKIA" + "Q7" * 8
SECRET = "wJalr" + "Xy9" * 11
NAMES = ("FLUENTMAIL_AWS_ACCESS_KEY_ID", "FLUENTMAIL_AWS_SECRET_ACCESS_KEY")
GOUT = "user root\nhostname 203.0.113.7\nport 2222\nproxycommand none\nproxyjump none\n"


class Fake:
    def __init__(self, values):
        self.values, self.asks, self.touches = list(values), 0, 0

    def ask(self, prompt, title):
        self.asks += 1
        v = self.values.pop(0) if self.values else None
        return {"ok": False, "why": "cancel"} if v is None else {"ok": True, "why": "ok", "value": v}

    def touchid(self, reason):
        self.touches += 1
        return {"ok": True}


def sshfake(calls):
    def run(cmd, input=None, **kw):
        if cmd[:2] == ["ssh", "-G"]:
            return type("R", (), {"returncode": 0, "stdout": GOUT})()
        calls.append((cmd, input))
        return type("R", (), {"returncode": 0, "stdout": ""})()
    return run


class FluentMail(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-secret-fluentmail-")
        root = Path(self.td.name)
        self.sec = root / "secrets"
        self.env = {"HOME": str(root), "EZ2_AIB_STATE_ROOT": str(root / "state"), "EZ2_AIB_SECRETS_DIR": str(self.sec),
                    "EZ2_AIB_LOCALE": "zh-TW"}
        os.environ.update({"EZ2_AIB_STATE_ROOT": self.env["EZ2_AIB_STATE_ROOT"], "EZ2_AIB_SECRETS_DIR": str(self.sec)})

    def tearDown(self):
        self.td.cleanup()

    def run_cmd(self, argv, fake, calls=None):
        out = io.StringIO()
        rc = SA.ask_cmd(argv, env=dict(self.env), platform="darwin", ask=fake.ask, touchid=fake.touchid,
                        touchid_available=lambda: True, out=out, ssh_run=sshfake([] if calls is None else calls))
        text = out.getvalue()
        for v in (KEY_ID, SECRET):
            self.assertNotIn(v, text)
        return rc, json.loads(text.strip().splitlines()[-1])

    def local_file(self):
        return self.sec / "fluentmail.env"

    def test_f1_known_and_remote_only(self):
        for n in NAMES:
            self.assertEqual(S.file_for_name(n), "fluentmail.env", n)
            self.assertIn(n, S.REMOTE_ONLY_NAMES)

    def test_f2_remote_only_push(self):
        for n, v in zip(NAMES, (KEY_ID, SECRET)):
            calls, f = [], Fake([v])
            path = "/var/www/a.com/ez2-secrets/%s.env" % n
            rc, res = self.run_cmd([n, "--push", "eu01:" + path, "--owner", "acom", "--remote-only"], f, calls)
            self.assertEqual((rc, res["ok"], res["local"], f.asks, f.touches), (0, True, False, 1, 1))
            self.assertEqual([c[1] for c in calls], ["%s=%s\n" % (n, v)])
            self.assertTrue(all(v not in " ".join(c[0]) for c in calls))  # never in argv
        self.assertFalse(self.local_file().exists())

    def test_f3_local_refused_before_asking(self):
        for argv in ([NAMES[0]], [NAMES[1], "--push", "eu01:/var/www/a.com/x.env"]):
            calls, f = [], Fake([KEY_ID])
            rc, res = self.run_cmd(argv, f, calls)
            self.assertEqual((rc, res["why"], f.asks, f.touches, calls), (2, "remote_only_name", 0, 0, []))
        self.assertFalse(self.local_file().exists())

    def test_f4_put_secret_refused(self):
        for n in NAMES:
            with self.assertRaises(S.SecretError) as cm:
                SC.put_secret(n, KEY_ID, env=self.env)
            self.assertEqual(cm.exception.reason, "remote_only")
        self.assertFalse(self.local_file().exists())


class Knives(unittest.TestCase):
    def _red(self, rel, needle, repl, test):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "FluentMail." + test], cwd=str(root),
                            max_procs=60, timeout=300)
        self.assertNotEqual(r.returncode, 0, "刀落了測試還綠:" + test)

    def test_k1_ask_gate_dropped(self):
        self._red("src/cockpit/secret_ask.py", "    if a.name in S.REMOTE_ONLY_NAMES and not a.remote_only:",
                  "    if False:", "test_f3_local_refused_before_asking")

    def test_k2_put_gate_dropped(self):
        self._red("src/bootstrap/secret_collect.py", "    if name in S.REMOTE_ONLY_NAMES:", "    if False:",
                  "test_f4_put_secret_refused")


if __name__ == "__main__":
    unittest.main()
