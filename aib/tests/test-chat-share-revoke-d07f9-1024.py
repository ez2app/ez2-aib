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
   ⇒ Bob 在線再開、Bob 封鎖 Andy ⇒ 快照與在線格都消失
O1–O4 亂序(第 1 輪審 M1;NIP-59 外層時間亂數往回,補收順序跟真實先後無關;比 rumor 寄出時間):
   O1 新快照 → 舊撤回晚到 ⇒ 快照留著;O2 撤回 → 撤回前寄的舊快照晚到 ⇒ 丟掉,撤回後寄的照收
   O3 在線同理(兩個方向);O4 舊快照晚到不蓋新的
F1 偽造(M2):第三方席用自己的金鑰、aib-from 填 Bob 的位址送撤回 ⇒ Andy 手上 Bob 的快照 / 在線格還在
刀:K1 拿掉送出端撤回 ⇒ E1 紅;K2 收件端不刪 ⇒ E1 紅;K3 撤回不比 npub ⇒ F1 紅;K4 收快照不看撤回時間 ⇒ O2 紅;
   K5 刪快照不比寄出時間 ⇒ O1 紅;K6 在線撤回不刪 ⇒ O3 紅
E1 / O / F / K 要 AIB_TEST_NOSTR_VENV(aib test 會清 AIB_* ⇒ 全套裡跳過;要帶著另跑)。
"""
import importlib.util
import json
import os
import shutil
import subprocess
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
            (root / "x.json").write_text("{}")
            self.assertFalse(GF.drop_room(root, "../x"), "壞名字不准拿去組路徑")
            self.assertFalse(GF.drop_room(root, A + "\n"), "結尾換行也算壞名字")
            self.assertTrue((root / "x.json").exists(), "spaces/../x.json 被刪了")
            self.assertEqual(sorted(p.name for p in (root / "spaces").iterdir()), [B + ".json"])
            GF.note_world(root, A, "c1", [])
            GF.note_world(root, A, "c2", [])
            GF.note_world(root, B, "c1", [])
            GF.drop_world_owner(root, A)
            self.assertEqual(sorted(GF.read_world(root)), [B + "/c1"])
            GF.write_room(root, A, {"v": 1}, sent_at=200)
            self.assertFalse(GF.drop_room(root, A, upto=100), "撤回比快照舊 ⇒ 不刪")
            self.assertTrue(GF.drop_room(root, A, upto=200))
            GF.note_revoked(root, A, "space", 300)
            GF.note_revoked(root, A, "space", 250)
            self.assertEqual(GF.read_revoked(root, A), {"space": 300}, "只往大記")


PROBE = r'''
import asyncio, json, sys
sys.path.insert(0, sys.argv[1])
import chat_nostr as CN
import chat_roster as CR
import dm_global_files as GF
import nostr_keys as NK
import room_snapshot as RS
from pathlib import Path
root, ops = Path(sys.argv[2]), json.loads(sys.argv[3])
B = NK.npub(NK.pubkey_hex("%064x" % 12))
B_SEAT = NK.npub(NK.pubkey_hex("%064x" % 0x1201))
EVE_SEAT = NK.npub(NK.pubkey_hex("%064x" % 0xe0e1))
CR.add_friend(B)
B_ADDR = B + "/c1/bbbb2"

class T:
    def __init__(self, t):
        self.t = t
    def as_secs(self):
        return self.t

class Rumor:
    def __init__(self, t, content):
        self.t, self.c = t, content
    def created_at(self):
        return T(self.t)
    def content(self):
        return self.c

class Sender:
    async def _resolve(self, addr):
        return (0, {"seat": {"npub": B_SEAT, "seat": "bbbb2"}}) if addr == B_ADDR else (2, {})

r = object.__new__(CN.Receiver)
r.root, r.sender, r.heard = root, Sender(), {B}
snap = RS.encode({"v": 2, "people": []})
for op in ops:
    kind, t = op[0], op[1]
    frm = EVE_SEAT if op[-1] == "eve" else B_SEAT
    if kind == "room":
        asyncio.run(r.on_room(Rumor(t, snap), {"aib-from": B_ADDR}, frm))
    elif kind == "presence":
        body = json.dumps({"v": 1, "seats": [{"seat": "bbbb2", "cabin": "c1", "state": "online"}]})
        asyncio.run(r.on_presence(Rumor(t, body), {"aib-from": B_ADDR}, frm))
    else:
        tags = {"aib-from": B_ADDR, "aib-revoke": "1"}
        rv = Rumor(t, json.dumps({"v": 1, "revoke": True}))
        asyncio.run(r.on_room(rv, tags, frm) if kind == "rv-space" else r.on_presence(rv, tags, frm))
room = GF.read_room(root, B)
print(json.dumps({"room": room and room.get("sent_at"),
                  "world": sorted(v.get("sent_at") for v in GF.read_world(root).values() if v.get("owner") == B)}))
'''
NOSTR_VENV = os.environ.get("AIB_TEST_NOSTR_VENV") or ""
VPY = str(Path(NOSTR_VENV) / "bin" / "python") if NOSTR_VENV else ""


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Order(unittest.TestCase):
    def probe(self, *ops):
        td = Path(tempfile.mkdtemp(prefix="aib-revoke-p-"))
        self.addCleanup(shutil.rmtree, td, True)
        (td / "root").mkdir()
        env = dict(os.environ, HOME=str(td / "home"), EZ2_AIB_STATE_ROOT=str(td / "home" / ".ez2-aib"))
        r = subprocess.run([VPY, "-B", "-c", PROBE, str(REPO / "src" / "cockpit"), str(td / "root"), json.dumps(ops)],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_o1_old_revoke_late(self):
        self.assertEqual(self.probe(["room", 200], ["rv-space", 100])["room"], 200, "舊撤回晚到把重開的分享刪了")
        self.assertIsNone(self.probe(["room", 100], ["rv-space", 200])["room"], "照順序的撤回要刪")

    def test_o2_old_snapshot_late(self):
        self.assertIsNone(self.probe(["rv-space", 200], ["room", 100])["room"], "撤回前寄的舊快照又寫回來")
        self.assertIsNone(self.probe(["rv-space", 200], ["room", 200])["room"], "同一秒算撤回贏")
        self.assertEqual(self.probe(["rv-space", 200], ["room", 300])["room"], 300, "撤回後重開的分享要收")

    def test_o3_presence(self):
        self.assertEqual(self.probe(["rv-share", 200], ["presence", 100])["world"], [], "撤回前寄的舊在線又寫回來")
        self.assertEqual(self.probe(["presence", 300], ["rv-share", 200])["world"], [300], "舊撤回晚到刪了新在線")
        self.assertEqual(self.probe(["presence", 100], ["rv-share", 200])["world"], [], "在線撤回要刪")

    def test_o4_older_snapshot_does_not_overwrite(self):
        self.assertEqual(self.probe(["room", 300], ["room", 100])["room"], 300)
        self.assertEqual(self.probe(["presence", 300], ["presence", 100])["world"], [300])

    def test_f1_forged_revoke(self):
        got = self.probe(["room", 100], ["presence", 100], ["rv-space", 200, "eve"], ["rv-share", 200, "eve"])
        self.assertEqual(got, {"room": 100, "world": [100]}, "第三方席冒 Bob 的位址就能刪 Andy 手上的資料")


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

        self.assertEqual(b.cli("friend", "add", a.owner).returncode, 0)     # 封鎖(第 1 輪審 L3):重新加好友、再開在線
        CR.set_share("online", a.owner, b.env())
        self.assertTrue(a.wait_for(lambda: snap.exists() and cells(), 60), "重新加好友後沒收到快照 / 在線")
        self.assertEqual(b.cli("block", "add", a.owner).returncode, 0)
        self.assertTrue(a.wait_for(lambda: not snap.exists() and not cells(), 40),
                        "封鎖後對方還看得到:%s %s" % (snap.exists(), cells()))


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr, "紅要是斷言紅,不是炸掉:" + r.stdout + r.stderr)

    def test_k1_sender_no_revoke(self):
        self._knife("src/cockpit/chat_nostr.py",
                    'if await self._push_revoke(tgt, owner, mine_seats[0], "space", now):',
                    'if False:', "Revoke.test_e1_revoke")

    def test_k2_receiver_keeps(self):
        self._knife("src/cockpit/chat_nostr.py", "            GF.drop_room(self.root, owner, upto=at)\n",
                    "            pass\n", "Revoke.test_e1_revoke")

    def test_k3_no_npub_check(self):
        self._knife("src/cockpit/chat_nostr.py",
                    '        if rc != 0 or res["seat"]["npub"] != from_npub:\n            return\n        at = rumor',
                    '        if rc != 0:\n            return\n        at = rumor', "Order.test_f1_forged_revoke")

    def test_k4_room_ignores_revoked(self):
        self._knife("src/cockpit/chat_nostr.py",
                    'if sent <= int(GF.read_revoked(self.root, owner).get("space") or 0) or ',
                    "if ", "Order.test_o2_old_snapshot_late")

    def test_k5_drop_ignores_time(self):
        self._knife("src/cockpit/dm_global_files.py", "        if rec and int(rec.get(\"sent_at\") or 0) > upto:\n",
                    "        if False:\n", "Order.test_o1_old_revoke_late")

    def test_k6_presence_revoke_keeps(self):
        self._knife("src/cockpit/chat_nostr.py", "            GF.drop_world_owner(self.root, owner, upto=at)\n",
                    "            pass\n", "Order.test_o3_presence")


if __name__ == "__main__":
    unittest.main()
