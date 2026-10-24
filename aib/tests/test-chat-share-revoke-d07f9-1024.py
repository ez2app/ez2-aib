#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""空間 / 在線分享撤回(10-24 跨客戶驗收 6.7 / 5.4):以前降到 none / off 只是「不再送」,
對方手上的舊快照照算有效 7 天、在線格最多 40 分鐘。現在降級那一刻送一則撤回,對方刪掉。

P1 shared-sent 帳:記 / 拿掉 / 壞名字不收;drop_room / drop_world_owner 只動那一位
E1 本機假中繼站 + 兩台真轉接程式(Andy、Bob 互為好友):Andy 拿到 Bob 的空間快照與在線格
   ⇒ Bob 空間改 none ⇒ Andy 的快照被刪(稽核 revoke-in space)、Bob 的 shared-sent 拿掉 space
   ⇒ Bob 在線改 off ⇒ Andy 的在線格消失
   ⇒ Bob 空間再開 island ⇒ 快照立刻回來(不等一天)
   ⇒ Bob 刪掉 Andy 好友 ⇒ 快照又被刪(不是好友也要收撤回)
K1 拿掉送出端撤回 ⇒ E1 紅;K2 收件端不刪 ⇒ E1 紅
要 AIB_TEST_NOSTR_VENV(aib test 會清 AIB_* ⇒ 全套裡跳過)。
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_recv_revoke", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)
SB = RT.SB

A = NK.npub(NK.pubkey_hex("%064x" % 11))
B = NK.npub(NK.pubkey_hex("%064x" % 12))


class Pure(unittest.TestCase):
    def test_p1_ledger_and_drops(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            GF.note_shared_sent(root, A, "space", "island")
            GF.note_shared_sent(root, A, "share", "online")
            GF.note_shared_sent(root, B, "space", "room")
            GF.note_shared_sent(root, "npub1bad", "space", "island")
            GF.note_shared_sent(root, A, "bogus", "x")
            self.assertEqual(GF.read_shared_sent(root), {A: {"space": "island", "share": "online"}, B: {"space": "room"}})
            GF.note_shared_sent(root, A, "space", None)
            GF.note_shared_sent(root, B, "space", None)
            self.assertEqual(GF.read_shared_sent(root), {A: {"share": "online"}}, "空的那一位整格拿掉")
            GF.write_room(root, A, {"v": 1})
            GF.write_room(root, B, {"v": 1})
            self.assertTrue(GF.drop_room(root, A))
            self.assertFalse(GF.drop_room(root, A), "不在 ⇒ False,不炸")
            self.assertFalse(GF.drop_room(root, "../x"), "壞名字不准拿去組路徑")
            self.assertEqual(sorted(p.name for p in (root / "spaces").iterdir()), [B + ".json"])
            GF.note_world(root, A, "c1", [])
            GF.note_world(root, A, "c2", [])
            GF.note_world(root, B, "c1", [])
            GF.drop_world_owner(root, A)
            self.assertEqual(sorted(GF.read_world(root)), [B + "/c1"])


def audit_dirs(b, kind):
    import json
    out = []
    for p in sorted((b.root / "audit").glob("*.jsonl")):
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("dir") == kind:
                out.append(r.get("result"))
    return out


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Revoke(unittest.TestCase):
    setUp = RT.Network.setUp
    _box = RT.Network._box

    def test_e1_revoke(self):
        import chat_roster as CR
        self.box_extra = {"AIB_CHAT_PRESENCE_TICK": "1", "AIB_CHAT_PRESENCE_GAP": "1", "AIB_CHAT_ROOM_GAP": "1"}
        a, _x = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        b, _y = self._box("Bob", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True])
        self.assertEqual(a.cli("friend", "add", b.owner).returncode, 0)
        self.assertEqual(b.cli("friend", "add", a.owner).returncode, 0)
        snap = a.root / "spaces" / (b.owner + ".json")
        cells = lambda: [k for k, v in GF.read_world(a.root).items() if v.get("owner") == b.owner]  # noqa: E731
        self.assertTrue(a.wait_for(lambda: snap.exists() and cells(), 60), (snap.exists(), GF.read_world(a.root)))
        self.assertEqual(GF.read_shared_sent(b.root).get(a.owner), {"space": "island", "share": "online"})

        CR.set_space("none", a.owner, b.env())              # 改等級要本人 Touch ID(門在 CLI),測試直接走函式
        self.assertTrue(a.wait_for(lambda: not snap.exists(), 40), "空間改 none,對方的舊快照還在")
        self.assertIn("space", audit_dirs(a, "revoke-in"))
        self.assertTrue(b.wait_for(lambda: "space" not in (GF.read_shared_sent(b.root).get(a.owner) or {}), 20))
        self.assertTrue(cells(), "只撤空間,在線格要留著")

        CR.set_share("off", a.owner, b.env())
        self.assertTrue(a.wait_for(lambda: not cells(), 40), "在線改 off,對方還看得到在線")
        self.assertIn("share", audit_dirs(a, "revoke-in"))

        CR.set_space("island", a.owner, b.env())
        self.assertTrue(a.wait_for(snap.exists, 40), "再開分享要立刻送,不等一天")
        self.assertEqual(b.cli("friend", "remove", a.owner).returncode, 0)
        self.assertTrue(a.wait_for(lambda: not snap.exists(), 40), "刪好友後對方的舊快照還在")


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr, "紅要是斷言紅,不是炸掉:" + r.stdout + r.stderr)

    def test_k1_sender_no_revoke(self):
        self._knife("src/cockpit/chat_nostr.py",
                    'if await self._push_revoke(tgt, owner, mine_seats[0], "space", now):',
                    'if False:', "Revoke.test_e1_revoke")

    def test_k2_receiver_keeps(self):
        self._knife("src/cockpit/chat_nostr.py", "            GF.drop_room(self.root, owner)\n",
                    "            pass\n", "Revoke.test_e1_revoke")


if __name__ == "__main__":
    unittest.main()
