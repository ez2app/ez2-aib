#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""沒綁人格的席也能收全球信(10-17 fishmac 實機:新用戶第一席沒綁人格 ⇒ 沒席金鑰、名冊 0 席,publish 卻說已列進)。

U1 live_seats:沒綁人格的席 persona_uid = s-<席碼>;綁了的照舊
U2 sync_seats:替代人格發金鑰(s-<席碼>.key);那一席綁了人格 ⇒ 替代金鑰封存、改發人格的;席不活 ⇒ 替代金鑰封存
U3 指令:publish 一席還沒有金鑰 ⇒ 多一句提醒;轉接程式對過一輪(sync_keys)⇒ 名冊列出它(名字空、角色照帶)
刀(複本):K1 沒綁的照舊跳過 ⇒ U1 / U3 紅
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_send_ub", str(HERE / "test-chat-send-d07f9-1016.py"))
SB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SB)
INST = "20261017-000000-6ddf"


class Unbound(unittest.TestCase):
    def setUp(self):
        self.b = SB.Box()
        self.addCleanup(self.b.close)
        d = self.b.home / ".ez2-aib" / "instances" / INST / "seats"
        d.mkdir(parents=True, exist_ok=True)
        (d / "w4.json").write_text(json.dumps({"schema": "aib-seat/v1", "role": "root", "provider": "claude",
                                               "session_uuid": "27937c4a-33a6-46ae-af3f-1cf36237fb66"}))
        self.code = "7fb66"
        self.bound = self.b.seat(INST, "w5", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "工程席")

    def live(self):
        return {s["seat5"]: s for s in NK.live_seats(env=self.b.env())}

    def test_u1_live_seats(self):
        lv = self.live()
        self.assertEqual(lv[self.code]["persona_uid"], "s-" + self.code, "沒綁人格 ⇒ 替代人格")
        self.assertEqual((lv[self.code]["role"], lv[self.code]["name"]), ("root", None))
        self.assertEqual(lv[self.bound]["persona_uid"], "xxxx11")

    def test_u2_sync(self):
        env = self.b.env()
        acts = NK.sync_seats(list(self.live().values()), env)
        self.assertIn(("created", "s-" + self.code, self.code), acts)
        first = next(e for e in NK.read_map(env)["seats"] if e["seat5"] == self.code and e["status"] == "active")
        self.assertTrue((NK.keys_dir(env) / ("s-%s.key" % self.code)).is_file())
        self.assertEqual(NK.sync_seats(list(self.live().values()), env), [], "沒變化不再產")
        self.b.seat(INST, "w4", "27937c4a-33a6-46ae-af3f-1cf36237fb66", "yyyy22", "艦長")      # 這一席綁上人格
        acts = NK.sync_seats(list(self.live().values()), env)
        self.assertEqual(acts[:2], [("retired", "s-" + self.code, self.code), ("created", "yyyy22", self.code)])
        act = [e for e in NK.read_map(env)["seats"] if e["seat5"] == self.code and e["status"] == "active"]
        self.assertEqual(len(act), 1, "同一席只留一把在用")
        self.assertNotEqual(act[0]["npub"], first["npub"])

    def test_u3_cli(self):
        b = self.b
        w4 = b.home / ".ez2-aib" / "instances" / INST / "seats" / "w4.json"
        keep = w4.read_text()
        w4.unlink()                                          # 照 fishmac 的順序:先開通,之後才開這一席
        self.assertEqual(b.cli("on", "--no-venv", "--name", "Mac Pro 13", "--country", "TW").returncode, 0)
        w4.write_text(keep)
        r = b.cli("publish", self.code)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("⚠", r.stdout, "還沒有金鑰 ⇒ 要說清楚")
        import chat_bridge as CBR
        CBR.sync_keys(b.env())
        self.assertNotIn("⚠", b.cli("publish", self.code).stdout, "有金鑰了就不再提醒")
        out = b.cli("roster").stdout
        self.assertIn(self.code, out, out)


class Knife(unittest.TestCase):
    def test_k1_skip_unbound(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/nostr_keys.py", '        if not isinstance(pu, str):\n            pu = "s-" + code',
                        '        if not isinstance(pu, str):\n            continue', tests=(name, "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Unbound.test_u1_live_seats"], cwd=str(root),
                            max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
