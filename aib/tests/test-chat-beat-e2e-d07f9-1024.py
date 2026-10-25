#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""綠點心跳端到端(10-24):本機假中繼站 + 兩台真轉接程式(Andy、Bob 互為好友)。

E1 名片交換後兩邊都拿到對方的心跳鑰;中繼站上各有一筆 kind 30078 d=aib-beat(可取代:同一台只留一筆),
   內文看不到席碼;兩邊都查到對方心跳(稽核 beat-in ≥ 1);world_payload 對方 online=true
E2 Bob 刪掉 Andy ⇒ Bob 換一代心跳鑰(稽核 beat rotate)
要 AIB_TEST_NOSTR_VENV(aib test 會清 AIB_* ⇒ 全套裡跳過)。
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src" / "cockpit"))
import dm_global_files as GF  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_recv_beat", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)
SB = RT.SB
_cb = importlib.util.spec_from_file_location("chat_bridge_beat_e2e", str(HERE.parent / "src" / "cockpit" / "chat_bridge.py"))
CB = importlib.util.module_from_spec(_cb)
_cb.loader.exec_module(CB)


def audit_rows(b, kind):
    out = []
    for p in sorted((b.root / "audit").glob("*.jsonl")):
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("dir") == kind:
                out.append(r)
    return out


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Beat(unittest.TestCase):
    setUp = RT.Network.setUp
    _box = RT.Network._box

    def test_e1_e2(self):
        self.box_extra = {"AIB_CHAT_PRESENCE_TICK": "1", "AIB_CHAT_BEAT_EVERY": "2", "AIB_CHAT_BEAT_POLL": "2",
                          "AIB_CHAT_PRESENCE_GAP": "1"}
        a, (x,) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        b, (y,) = self._box("Bob", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True])
        self.assertEqual(a.cli("friend", "add", b.owner).returncode, 0)
        self.assertEqual(b.cli("friend", "add", a.owner).returncode, 0)
        has_key = lambda me, other: any(o == other for o, *_r in GF.read_beat_keys(me.root))  # noqa: E731
        self.assertTrue(a.wait_for(lambda: has_key(a, b.owner) and has_key(b, a.owner), 60), "名片沒帶到心跳鑰")
        beats = lambda: [e for e in self.relay.events if e["kind"] == 30078  # noqa: E731
                         and any(t[:2] == ["d", "aib-beat"] for t in e["tags"])]
        self.assertTrue(a.wait_for(lambda: len({e["pubkey"] for e in beats()}) == 2, 40), self.relay.events[-3:])
        self.assertEqual(len(beats()), 2, "可取代事件:一台只留一筆")
        for e in beats():
            self.assertNotIn(x, e["content"])
            self.assertNotIn(y, e["content"])
        got_in = lambda box: any(int(r["result"]) >= 1 for r in audit_rows(box, "beat-in"))  # noqa: E731
        self.assertTrue(a.wait_for(lambda: got_in(a) and got_in(b), 40), (audit_rows(a, "beat-in")[-2:], audit_rows(b, "beat-in")[-2:]))
        cell = next(c for c in CB.world_payload(a.env())["cabins"] if c["owner"] == b.owner)
        self.assertTrue(cell["online"], cell)
        self.assertEqual([s["seat"] for s in cell["seats"]], [y])
        # E2:Bob 刪掉 Andy ⇒ Bob 換一代
        self.assertEqual(b.cli("friend", "remove", a.owner).returncode, 0)
        self.assertTrue(b.wait_for(lambda: any(r["result"].startswith("rotate:") for r in audit_rows(b, "beat")), 30),
                        audit_rows(b, "beat"))


if __name__ == "__main__":
    unittest.main()
