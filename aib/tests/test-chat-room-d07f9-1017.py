#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""房間快照:走進好友的艦長室(開發文件第 34 節;包 J)。核心一定跑(只用標準庫);走進艦長室的端到端在 test-chat-e2e-world。

J1 scrub:任何層級的 ctx / unread / ip / host / hostname / content / body / msg 都拿掉;字串清控制字元與長度
J2 encode / decode:壓縮後超過上限 ⇒ 拿掉 layout、標 layout_omitted;decode 壞資料 ⇒ None;解壓炸彈(超過 64 KB)⇒ None
J3 build:只列公開的席;席名 / emoji / AI 家 / 角色由核心補(room.json 自報的 name / role 不採用);
   room.json 帶的禁止欄位不進快照
J4 分享等級:room 只能逐位打開(--default room ⇒ rc 2);room 對陌生人一樣是 off
J5 aib chat room:不是好友 ⇒ rc 1;好友有快照 ⇒ 列艦名與席
刀(複本):K1 不濾禁止欄位 ⇒ J1 紅;K2 build 不看公開 ⇒ J3 紅
"""
import base64
import importlib.util
import json
import sys
import time
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402
import room_snapshot as RS  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_send_pj", str(HERE / "test-chat-send-d07f9-1016.py"))
SB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SB)
FRIEND = NK.npub(NK.pubkey_hex("%064x" % 121))
OTHER = NK.npub(NK.pubkey_hex("%064x" % 122))


class Pure(unittest.TestCase):
    def test_j1_scrub(self):
        x = RS.scrub({"ship": {"name": "號\x1b[31m", "IP": "1.2.3.4"}, "cabins": [{"host": "mbp", "floors": [{"id": "1F"}]}],
                      "seats": {"a": {"ctx": 9, "Unread": 2, "lv": 3, "deep": {"content": "秘密", "ok": 1}}},
                      "msg": "x", "body": "y", "hostname": "z", "layout": {"t": "a" * 500}})
        blob = json.dumps(x, ensure_ascii=False)
        for bad in ('"IP"', '"host"', '"ctx"', '"Unread"', '"content"', '"msg"', '"body"', '"hostname"', "秘密", "\x1b"):
            self.assertNotIn(bad, blob, bad)
        self.assertEqual(x["seats"]["a"]["lv"], 3)
        self.assertEqual(len(x["layout"]["t"]), RS.STR_MAX)

    def test_j2_encode_decode(self):
        import os
        small = {"v": 1, "seats": [{"seat": "abcde"}], "layout": {"a": 1}, "layout_omitted": False}
        self.assertEqual(RS.decode(RS.encode(small))["layout"], {"a": 1})
        big = dict(small, layout={"k": [os.urandom(8).hex() for _ in range(6000)]})
        got = RS.decode(RS.encode(big))
        self.assertEqual((got["layout"], got["layout_omitted"]), (None, True))
        self.assertIsNone(RS.decode("not base64!!"))
        self.assertIsNone(RS.decode(base64.b64encode(zlib.compress(b'{"v":2}')).decode()))
        bomb = base64.b64encode(zlib.compress(json.dumps({"v": 1, "seats": [], "x": "0" * (RS.RAW_MAX + 10)}).encode())).decode()
        self.assertIsNone(RS.decode(bomb), "解開超過 64 KB 的不收")


class Box(unittest.TestCase):
    def setUp(self):
        self.b = SB.Box()
        self.addCleanup(self.b.close)
        b = self.b
        self.codes = [b.seat("20261017-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "艦長"),
                      b.seat("20261017-000000-6ddf", "w3", "00000000-0000-0000-0000-00000000bbb2", "yyyy22", "工程席")]
        seats = b.home / ".ez2-aib" / "instances" / "20261017-000000-6ddf" / "seats"
        for w, role, prov in (("w2", "root", "claude"), ("w3", "dev", "codex")):
            rec = json.loads((seats / ("%s.json" % w)).read_text())
            rec.update(role=role, provider=prov)
            (seats / ("%s.json" % w)).write_text(json.dumps(rec))
        ident = b.home / ".ez2-aib" / "identity"
        (ident / "cards.json").write_text(json.dumps({"cards": {"xxxx11": {"emoji": "👑"}, "yyyy22": {"emoji": "🛠️"}}}))
        self.assertEqual(b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
        b.cli("publish", self.codes[0])                       # 只公開艦長席

    def test_j3_build(self):
        (self.b.chat / "room.json").write_text(json.dumps({
            "v": 1, "ship": {"name": "Ez2 號", "ip": "10.0.0.1",
                             "captain": {"seat": self.codes[0], "name": "Andy", "avatar": "🧑‍✈️", "ip": "x"}},
            "seats": {self.codes[0]: {"floor": "3F", "lv": 9, "stars": 5, "name": "假名", "role": "假角色", "ctx": 90},
                      self.codes[1]: {"floor": "1F", "lv": 1}}}))
        snap = RS.build(self.b.env())
        self.assertEqual([s["seat"] for s in snap["seats"]], [self.codes[0]], "沒公開的席不准出現")
        s = snap["seats"][0]
        self.assertEqual((s["name"], s["role"], s["provider"], s["emoji"], s["floor"], s["lv"]),
                         ("艦長", "root", "claude", "👑", "3F", 9), "名字角色由核心補,不信 room.json 自報")
        self.assertEqual(snap["ship"]["captain"], {"seat": self.codes[0], "name": "Andy", "avatar": "🧑‍✈️"})
        (self.b.chat / "room.json").write_text(json.dumps({"v": 1, "ship": {"captain": {"seat": self.codes[1], "name": "X"}}}))
        self.assertNotIn("seat", RS.build(self.b.env())["ship"]["captain"], "艦長格指到沒公開的席 ⇒ 不帶席碼")
        blob = json.dumps(snap, ensure_ascii=False)
        self.assertNotIn("10.0.0.1", blob)
        self.assertNotIn('"ctx"', blob)

    def test_j4_share_room(self):
        b = self.b
        b.cli("friend", "add", FRIEND)
        self.assertEqual(b.cli("share", "--default", "room").returncode, 2, "room 不准當預設")
        self.assertEqual(b.cli("share", FRIEND, "room").returncode, 0)
        self.assertEqual(json.loads(b.cli("share", "--json").stdout)["peers"][FRIEND], "room")
        import chat_roster as CR
        self.assertEqual(CR.share_level(OTHER, b.env()), "off")

    def test_j5_room_cli(self):
        b = self.b
        GF.ensure_dirs(b.root)
        snap = {"v": 1, "ship": {"name": "Bob 號"}, "cabins": [], "seats": [{"seat": "bbbb2", "emoji": "🧭", "name": "Bob 艦長"}],
                "layout": None, "layout_omitted": False}
        GF.write_room(b.root, FRIEND, snap)
        self.assertEqual(b.cli("room", FRIEND, "--json").returncode, 1, "不是好友 ⇒ 看不到")
        b.cli("friend", "add", FRIEND)
        r = b.cli("room", FRIEND)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Bob 號", r.stdout)
        self.assertIn("🧭", r.stdout)
        pl = json.loads(b.cli("room", FRIEND, "--json").stdout)
        self.assertTrue(pl["fresh"])
        rec = json.loads((b.root / "rooms" / (FRIEND + ".json")).read_text())
        rec["at"] = int(time.time()) - 8 * 86400
        (b.root / "rooms" / (FRIEND + ".json")).write_text(json.dumps(rec))
        self.assertFalse(json.loads(b.cli("room", FRIEND, "--json").stdout)["fresh"], "7 天沒更新 ⇒ 不新鮮")


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/room_snapshot.py", needle, replacement,
                        tests=(name, "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_no_filter(self):
        self._knife(" if str(k).lower() not in FORBIDDEN}", "}", "Pure.test_j1_scrub")

    def test_k2_not_only_published(self):
        self._knife(' if s["seat5"] in pub and s["seat5"] in active]', ' if s["seat5"] in active]', "Box.test_j3_build")


if __name__ == "__main__":
    unittest.main()
