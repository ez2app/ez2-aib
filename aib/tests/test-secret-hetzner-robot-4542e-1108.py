#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""Hetzner Robot webservice 帳密進金鑰櫃(11-08 @4542e,EU02 代管節點:aib 要能自己啟用 Rescue / 重置 / 失聯自救)。

H1 HETZNER_ROBOT_USER / HETZNER_ROBOT_PASS 落 hetzner-robot.env(不再 unknown_name / bad_name)
H2 兩個名字都不是 remote-only、不是 SE-only:aib secret ask 走輸入框 → Touch ID → 寫本機金鑰櫃(落地是加密,明文不在檔),值不進輸出
H3 帶字尾的同類名(HETZNER_ROBOT_PASS_2)也落同檔;HETZNER_ 前綴不會被別的檔吃掉
H4 值讀回來跟寫進去的一樣(secret() 走讀閘),且不在任何輸出文字裡
刀:K1 拿掉 LAYOUT 那一格 ⇒ H1 / H2 紅(unknown_name)
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
import aib_secrets as S  # noqa: E402
import secret_ask as SA  # noqa: E402

NAMES = ("HETZNER_ROBOT_USER", "HETZNER_ROBOT_PASS")
VALUES = ("#ws+Q7x9k2", "p4ss" + "Zq8" * 9)


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


class HetznerRobot(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-secret-hetzner-")
        root = Path(self.td.name)
        self.sec = root / "secrets"
        self.env = {"HOME": str(root), "EZ2_AIB_STATE_ROOT": str(root / "state"), "EZ2_AIB_SECRETS_DIR": str(self.sec),
                    "EZ2_AIB_LOCALE": "zh-TW"}
        os.environ.update({"EZ2_AIB_STATE_ROOT": self.env["EZ2_AIB_STATE_ROOT"], "EZ2_AIB_SECRETS_DIR": str(self.sec)})

    def tearDown(self):
        self.td.cleanup()

    def run_cmd(self, argv, fake):
        out = io.StringIO()
        rc = SA.ask_cmd(argv, env=dict(self.env), platform="darwin", ask=fake.ask, touchid=fake.touchid,
                        touchid_available=lambda: True, out=out,
                        ssh_run=lambda *a, **k: self.fail("不該碰 ssh"))
        text = out.getvalue()
        for v in VALUES:
            self.assertNotIn(v, text)
        return rc, json.loads(text.strip().splitlines()[-1])

    def test_h1_known_local_names(self):
        for n in NAMES:
            self.assertEqual(S.file_for_name(n), "hetzner-robot.env", n)
            self.assertTrue(S.valid_name(n))
            self.assertFalse(S.is_remote_only(n), n)
            self.assertNotIn(n, S.SE_ONLY_NAMES)

    def test_h2_ask_writes_local_vault(self):
        for n, v in zip(NAMES, VALUES):
            f = Fake([v])
            rc, res = self.run_cmd([n], f)
            self.assertEqual((rc, res["ok"], f.asks, f.touches), (0, True, 1, 1), (n, res))
        path = self.sec / "hetzner-robot.env"
        self.assertTrue(path.is_file())
        raw = path.read_bytes().decode()
        rows = S.parse_env_bytes(path.read_bytes())
        self.assertEqual(tuple(sorted(n for n in NAMES if rows.get(n))), tuple(sorted(NAMES)))
        for v in VALUES:  # 金鑰櫃落地是加密(enc.v1.…),明文不在檔裡;讀回走 H4 的 secret()
            self.assertNotIn(v, raw)

    def test_h3_suffix_and_prefix(self):
        self.assertEqual(S.file_for_name("HETZNER_ROBOT_PASS_2"), "hetzner-robot.env")
        self.assertEqual(S.file_for_name("HETZNER_ROBOT_SERVER_NUMBER"), "hetzner-robot.env")
        self.assertIsNone(S.file_for_name("HETZNERX"))

    def test_h4_read_back(self):
        for n, v in zip(NAMES, VALUES):
            self.run_cmd([n], Fake([v]))
        for n, v in zip(NAMES, VALUES):
            self.assertEqual(S.secret(n, env=self.env, fill=False), v)


if __name__ == "__main__":
    unittest.main()
