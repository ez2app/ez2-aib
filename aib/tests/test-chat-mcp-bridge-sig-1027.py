#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""SPEC-aib-chat-mcp-1027 §17:檔案橋寄件來源簽章(drainer 簽、chat_mcp 驗)。只用 mktemp 與暫存金鑰。"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
from chat_mcp import core  # noqa: E402
import bridge_sign as bs  # noqa: E402
import musebot_outbox_drainer as dr  # noqa: E402

A, B = "b962/b81c8", "b962/8f067"
SESS_A = "11111111-2222-4333-8444-5555555b81c8"
SESS_B = "e32847d6-5a8e-481b-aa67-6d6dc438f067"
OK = lambda target, text: "DM 指令 rc=0:test"  # noqa: E731


class Sig(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aib-bridge-sig-test-"))
        self.state = self.tmp / "state"
        self.bridge = self.state / "bridge"
        os.environ["AIB_CHAT_MCP_BRIDGE_ROOT"] = str(self.bridge)
        os.environ["EZ2_AIB_STATE_ROOT"] = str(self.state)
        os.environ.pop(bs.KEY_ENV, None)
        self.store = core.Store(self.tmp / "home")
        self.store.bind("muse", B, [A], session_uuid=SESS_B)
        self.a = self.bridge / SESS_A
        (self.a / "outbox").mkdir(parents=True)
        (self.a / "status.json").write_text(json.dumps({"provider": "musebot", "cabin": "b962", "code": "b81c8"}))
        (self.bridge / SESS_B / "inbox").mkdir(parents=True)

    def tearDown(self):
        os.environ.pop("EZ2_AIB_STATE_ROOT", None)
        os.environ.pop(bs.KEY_ENV, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def send(self, body, frm_claim="muse"):
        p = self.a / "outbox" / ("%d-x.json" % int(time.time() * 1000))
        p.write_text(json.dumps({"schema": dr.MSG_SCHEMA, "provider": "musebot", "from": frm_claim,
                                 "to": B, "text": body, "created_epoch": int(time.time())}))
        st = json.loads((self.a / "status.json").read_text())
        self.assertEqual(dr.deliver_one(p, OK, status=st), "delivered")
        return self.a / "delivered" / p.name

    def arrive(self, header_from, body, name="1-human.json"):
        (self.bridge / SESS_B / "inbox" / name).write_text(json.dumps({
            "schema": dr.MSG_SCHEMA, "from": "human", "text": "[@%s > @8f067 / 台北 10-27 07:40] %s" % (header_from, body),
            "created_epoch": int(time.time())}))

    def senders(self):
        ad = core.BridgeAdapter(self.store, self.bridge)
        ad.sync_inbox(self.store.active_binding("muse"))
        return [r[0] for r in self.store.db.execute("SELECT from_seat FROM messages ORDER BY created")]

    def test_signed_recognised(self):
        d = self.send("你好 dot")
        rec = json.loads(d.read_text())[bs.SIG_FIELD]
        self.assertEqual((rec["from_seat"], rec["to_seat"]), (A, B))
        self.arrive("b81c8", "你好 dot")
        self.assertEqual(self.senders(), [A])

    def test_key_perms_and_autocreate(self):
        self.send("x")
        k = self.state / "secrets" / "bridge-sign.key"
        self.assertEqual(k.stat().st_mode & 0o777, 0o600)
        self.assertEqual(k.parent.stat().st_mode & 0o777, 0o700)
        os.chmod(k, 0o644)
        self.assertIsNone(bs.load_key(self.state))  # 太寬:拒用
        self.arrive("b81c8", "x")
        self.assertEqual(self.senders(), ["bridge:unknown"])

    def test_env_override(self):
        alt = self.tmp / "alt" / "k.key"
        os.environ[bs.KEY_ENV] = str(alt)
        self.send("y")
        self.assertTrue(alt.exists())
        self.assertFalse((self.state / "secrets" / "bridge-sign.key").exists())

    def test_tampered_body(self):
        self.send("原文")
        self.arrive("b81c8", "改過的內文")
        self.assertEqual(self.senders(), ["bridge:unknown"])

    def test_tampered_delivered_record_body(self):
        d = self.send("原文")
        j = json.loads(d.read_text())
        j[bs.SIG_FIELD]["body_sha256"] = bs.body_hash("偽")
        d.write_text(json.dumps(j))
        self.arrive("b81c8", "偽")
        self.assertEqual(self.senders(), ["bridge:unknown"])

    def test_tampered_sender(self):
        d = self.send("hi")
        self.arrive("49136", "hi")  # 抬頭冒充別席
        self.assertEqual(self.senders(), ["bridge:unknown"])
        j = json.loads(d.read_text())
        j[bs.SIG_FIELD]["from_seat"] = "b962/49136"  # 改簽章裡的寄件席
        d.write_text(json.dumps(j))
        self.arrive("49136", "hi", "2-human.json")
        self.assertEqual(self.senders(), ["bridge:unknown", "bridge:unknown"])

    def test_unsigned_and_legacy_still_received(self):
        self.arrive("b81c8", "無簽")
        (self.bridge / SESS_B / "inbox" / "0-old.json").write_text(json.dumps({"text": "舊信", "created_epoch": 1}))
        self.assertEqual(sorted(self.senders()), ["bridge:unknown", "bridge:unknown"])

    def test_wrong_key(self):
        self.send("hi")
        (self.state / "secrets" / "bridge-sign.key").write_text("f" * 64 + "\n")
        self.arrive("b81c8", "hi")
        self.assertEqual(self.senders(), ["bridge:unknown"])

    def test_replay_only_once(self):
        self.send("同一句")
        self.arrive("b81c8", "同一句", "1-human.json")
        self.arrive("b81c8", "同一句", "2-human.json")
        self.assertEqual(sorted(self.senders()), sorted([A, "bridge:unknown"]))

    def test_sign_failure_keeps_delivery(self):
        os.environ[bs.KEY_ENV] = "/dev/null/nope/k"
        d = self.send("仍送達")
        self.assertTrue(d.exists())
        self.assertNotIn(bs.SIG_FIELD, json.loads(d.read_text()))

    def test_constant_time_compare_used(self):
        import inspect
        self.assertIn("compare_digest", inspect.getsource(bs.verify_sig))


if __name__ == "__main__":
    unittest.main(verbosity=1)
