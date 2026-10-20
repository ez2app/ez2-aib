#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球位址改由好友名單選(第二波 #4,10-21;NOTE-member-ux B5「全球位址太難,連我都先寫錯」)。

核心挑位址,人只選人:
- dm_global_files.default_address(root, owner):他房間快照標的艦長席(captain)> 聯絡人快照第一個公開席(first)> 沒有
- aib chat contacts --json 每位多 addr / addr_why;aib chat address <好友|名字|npub> --json 給頁面查
- aib chat send --as-human 的收件人可寫完整位址、主人 npub、或好友 / 我的機器的備註名(不分大小寫);是 npub / 名字但不能寄 ⇒ why unknown_contact,認不出形狀 ⇒ 既有的 bad_address

A1 default_address:沒資料 None;只有名冊 ⇒ first;房間有艦長席且在 seats 裡 ⇒ captain(艙照 seats);艦長席不在 seats ⇒ first;壞 npub None
A2 resolve_to:完整位址照給;主人 npub(好友)⇒ 預設位址;名字(大小寫 / 空白不計)⇒ 預設位址;陌生 npub / 封鎖 / 好友但沒名冊 ⇒ unknown_contact;
   空 / 亂字(不是位址、npub、好友名)⇒ bad_address(既有契約不變)
A3 contacts_payload:每位帶 addr / addr_why;沒名冊的 None
A4 aib chat address:--json 形狀;解不出 rc 2;文字列
A5 chat_human.send:名字能解到位址(到 no_public_seat 那一步,代表收件人過了);陌生 npub ⇒ unknown_contact;亂字 / 空 ⇒ bad_address
A6 四語字典:address / unknown_contact 鍵四種語言都有
刀(複本):K1 default_address 不看艦長席 ⇒ A1 紅;K2 resolve_to 不驗好友 ⇒ A2 紅
"""
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(HERE))
import chat_human as CH  # noqa: E402
import chat_roster as CR  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

FRIEND = NK.npub(NK.pubkey_hex("%064x" % 0x9101))
MINE = NK.npub(NK.pubkey_hex("%064x" % 0x9102))
STRANGER = NK.npub(NK.pubkey_hex("%064x" % 0x9103))
BLOCKED = NK.npub(NK.pubkey_hex("%064x" % 0x9104))
LONELY = NK.npub(NK.pubkey_hex("%064x" % 0x9105))      # 好友但沒收過名冊
SEATN = NK.npub(NK.pubkey_hex("%064x" % 0x9106))


def _load_cb():
    spec = importlib.util.spec_from_file_location("chat_bridge_addr", str(COCKPIT / "chat_bridge.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-addr-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name)
        self.root = self.td / "bridge"
        self.env = {"HOME": str(self.td), "EZ2_AIB_STATE_ROOT": str(self.td / "state"), "AIB_CHAT_BRIDGE_DIR": str(self.root),
                    "AIB_CHAT_HOME": str(self.td / "chat"), "EZ2_AIB_LOCALE": "zh-TW", "PATH": "/usr/bin:/bin"}
        GF.ensure_dirs(self.root)
        CR.add_friend(FRIEND, "Pro 13", self.env)
        CR.add_friend(LONELY, "Lonely", self.env)
        CR.add_friend(MINE, "Mini", self.env, kind="mine")
        CR.add_friend(BLOCKED, "Bad", self.env, kind="blocks")
        GF.note_peer(self.root, FRIEND, "Pro 13", "TW", seat={"cabin": "5a97", "seat": "4345c", "npub": SEATN, "name": "工程席", "role": "dev"},
                     roster_at=1000)
        GF.note_peer(self.root, FRIEND, seat={"cabin": "754c", "seat": "fdcf5", "npub": SEATN, "name": "艦長", "role": "root"}, roster_at=1000)
        GF.note_peer(self.root, MINE, "Mini", "TW", seat={"cabin": "2282", "seat": "c4366", "npub": SEATN}, roster_at=1000)
        for o in (STRANGER, BLOCKED):      # 有名冊但不是好友 / 被封鎖:位址查得到也不能寄(刀 K2 的錨)
            GF.note_peer(self.root, o, "X", "TW", seat={"cabin": "1111", "seat": "aaaaa", "npub": SEATN}, roster_at=1000)

    def room(self, owner, captain, seats):
        snap = {"v": 2, "island": {}, "people": [], "rooms": {"ship": {"name": "x", "captain": {"seat": captain, "name": "Andy"}},
                                                              "cabins": [], "seats": seats, "layout": None, "layout_omitted": False}}
        GF.write_room(self.root, owner, snap)


class Core(Base):
    def test_a1_default_address(self):
        self.assertEqual(GF.default_address(self.root, LONELY), (None, None))
        self.assertEqual(GF.default_address(self.root, "junk"), (None, None))
        self.assertEqual(GF.default_address(self.root, FRIEND), (FRIEND + "/5a97/4345c", "first"))
        self.room(FRIEND, "fdcf5", [{"seat": "4345c", "cabin": "5a97"}, {"seat": "fdcf5", "cabin": "754c"}])
        self.assertEqual(GF.default_address(self.root, FRIEND), (FRIEND + "/754c/fdcf5", "captain"))
        self.room(FRIEND, "zzzzz", [{"seat": "4345c", "cabin": "5a97"}])
        self.assertEqual(GF.default_address(self.root, FRIEND), (FRIEND + "/5a97/4345c", "first"), "艦長席不在公開席裡 ⇒ 退回第一席")
        self.room(LONELY, "aaaaa", [{"seat": "aaaaa"}])
        self.assertEqual(GF.default_address(self.root, LONELY), (LONELY + "/main/aaaaa", "captain"), "沒艙 ⇒ main")

    def test_a2_resolve_to(self):
        full = FRIEND + "/5a97/4345c"
        self.assertEqual(CH.resolve_to(full, self.env), (full, "address"))
        self.assertEqual(CH.resolve_to(" " + full + " ", self.env), (full, "address"))
        self.assertEqual(CH.resolve_to(FRIEND, self.env), (FRIEND + "/5a97/4345c", "first"))
        self.room(FRIEND, "fdcf5", [{"seat": "fdcf5", "cabin": "754c"}])
        self.assertEqual(CH.resolve_to(FRIEND, self.env), (FRIEND + "/754c/fdcf5", "captain"))
        for name in ("Pro 13", "pro 13", "  PRO 13 "):
            self.assertEqual(CH.resolve_to(name, self.env), (FRIEND + "/754c/fdcf5", "captain"), name)
        self.assertEqual(CH.resolve_to("mini", self.env), (MINE + "/2282/c4366", "first"))
        for bad in (STRANGER, BLOCKED, "Lonely", LONELY):
            self.assertEqual(CH.resolve_to(bad, self.env), (None, "unknown_contact"), bad)
        for bad in ("nobody", "Bad", "npub1junk"):          # 認不出形狀 ⇒ 既有碼 bad_address(封鎖名單的名字不算好友名)
            self.assertEqual(CH.resolve_to(bad, self.env), (None, "bad_address"), bad)
        for empty in ("", "   ", None):
            self.assertEqual(CH.resolve_to(empty, self.env), (None, "bad_address"), repr(empty))

    def test_a3_contacts_payload(self):
        CB = _load_cb()
        self.room(FRIEND, "fdcf5", [{"seat": "fdcf5", "cabin": "754c"}])
        rows = {r["owner"]: r for r in CB.contacts_payload(self.env)["contacts"]}
        self.assertEqual((rows[FRIEND]["addr"], rows[FRIEND]["addr_why"]), (FRIEND + "/754c/fdcf5", "captain"))
        self.assertEqual((rows[MINE]["addr"], rows[MINE]["addr_why"]), (MINE + "/2282/c4366", "first"))
        self.assertEqual((rows[LONELY]["addr"], rows[LONELY]["addr_why"]), (None, None))
        self.assertEqual((rows[BLOCKED]["addr"], rows[BLOCKED]["addr_why"]), (None, None))

    def test_a4_cli_address(self):
        CB = _load_cb()
        def run(*argv):
            buf = io.StringIO()
            rc = CB.main(list(argv), env=self.env, out=buf)
            return rc, buf.getvalue()
        rc, out = run("address", "Pro 13", "--json")
        self.assertEqual(rc, 0, out)
        d = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((d["owner"], d["addr"], d["why"], d["name"]), (FRIEND, FRIEND + "/5a97/4345c", "first", "Pro 13"))
        self.assertEqual([s["addr"] for s in d["seats"]], [FRIEND + "/5a97/4345c", FRIEND + "/754c/fdcf5"])
        rc, out = run("address", "nobody", "--json")
        self.assertEqual((rc, out.strip()), (2, "null"))
        rc, out = run("address", "mini")
        self.assertEqual(rc, 0, out)
        self.assertIn(MINE + "/2282/c4366", out)
        self.assertIn("Mini", out)
        rc, out = run("address", "nobody")
        self.assertEqual(rc, 2)
        self.assertIn("nobody", out)

    def test_a5_send_resolves_name(self):
        env = dict(self.env, AIB_TOUCHID="0")
        res = CH.send("Pro 13", "你好", "", "6ddf", env, platform="linux")
        self.assertEqual((res["why"], res["to"], res["to_why"]), ("no_public_seat", FRIEND + "/5a97/4345c", "first"), "收件人過了,卡在沒席可簽")
        res = CH.send("nobody", "你好", "", "6ddf", env, platform="linux")
        self.assertEqual((res["why"], res["to"]), ("bad_address", None), "舊契約不變")
        res = CH.send(STRANGER, "你好", "", "6ddf", env, platform="linux")
        self.assertEqual((res["why"], res["to"]), ("unknown_contact", None))
        res = CH.send("", "你好", "", "6ddf", env, platform="linux")
        self.assertEqual(res["why"], "bad_address")

    def test_a6_i18n(self):
        want = None
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            keys = {k for k in d if k.startswith("aib.chat.address") or k == "aib.chat.help.address" or k == "aib.chat.send_why_unknown_contact"}
            self.assertGreaterEqual(len(keys), 7, lang)
            want = want or keys
            self.assertEqual(keys, want, lang)


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_ignore_captain(self):
        self._knife("src/cockpit/dm_global_files.py", '            if isinstance(s, dict) and cap and s.get("seat") == cap:',
                    '            if False:', "Core.test_a1_default_address")

    def test_k2_no_friend_check(self):
        self._knife("src/cockpit/chat_human.py",
                    '    if CR.is_blocked(owner, env) or not (CR.is_friend(owner, env) or CR.is_mine(owner, env)):\n        return None, "unknown_contact"',
                    '    if False:\n        return None, "unknown_contact"', "Core.test_a2_resolve_to")


if __name__ == "__main__":
    unittest.main()
