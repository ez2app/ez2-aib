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
   K5 刪快照不比寄出時間 ⇒ O1 紅;K6 在線撤回不刪 ⇒ O3 紅;K8 陌生撤回不先擋 ⇒ S1 紅;K9 退避指數不封頂 ⇒ S3 紅;
   K10 寄出時間夾成收到時間 ⇒ O5 紅;K11 撤回帳用寄出時間清 ⇒ O7 紅;K12 撤回不會過期 ⇒ O8 紅
O5 寄件端時鐘快 1 小時 + 亂序跨秒(撤回先到、撤回前寄的舊快照 / 在線後到)⇒ 不復活(第 3 輪審,@abb89 定案:
   同一位寄件人之間只比原始寄出時間,不夾值;收到時間只決定多久不再擋人)
O7 寄件端時鐘慢 40 天:撤回帳不會一寫就被清,舊快照照丟
O8 未來時間的撤回只擋 2 天 10 分(看收到時間);過了 ⇒ 重開的分享照收;同一個時鐘的未來快照接更晚的撤回照刪
O6 手上那份過期(收到超過 7 天)不拿來擋新收的快照
S1 陌生主人的撤回(手上沒他的資料、不是好友)⇒ 不查名冊、不寫 revoked.json(M2)
S2 壞帳(值不是數字 / 列不是 dict / 整檔不是 dict)不炸;記帳失敗也先刪(L1、M2)
S3 退避 n=2000 不溢位、封頂 1 小時(M3)
E1、K1、K2 要 AIB_TEST_NOSTR_VENV(真轉接程式 + 假中繼站;aib test 會清 AIB_* ⇒ 全套裡跳過,要帶著另跑)。
O / F / S 與 K3–K12 只餵收件端真程式:有 venv 用 venv,沒有就用下面的假 nostr_sdk 模組照跑(第 3 輪審 L1)。
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
            GF.note_revoked(root, A, "space", 300, now=400)
            GF.note_revoked(root, A, "space", 250, now=400)
            self.assertEqual(GF.read_revoked(root, A, now=400), {"space": 300}, "只往大記")
            self.assertEqual(GF.read_revoked(root, A, now=400 + GF.REVOKE_BACKFILL_S), {}, "收到超過 2 天 10 分不再擋")
            now = 10 ** 9
            for i in range(GF.REVOKED_MAX + 5):                   # 筆數上限:留最新的(第 2 輪審 M2)
                GF.note_revoked(root, NK.npub(NK.pubkey_hex("%064x" % (1000 + i))), "share", now + i, now=now + i)
            per = __import__("json").loads((root / "revoked.json").read_text())["per"]
            self.assertEqual(len(per), GF.REVOKED_MAX)
            self.assertNotIn(NK.npub(NK.pubkey_hex("%064x" % 1000)), per, "最舊的要先清")
            GF.note_revoked(root, B, "space", now + GF.REVOKED_KEEP_S + 10 ** 4, now=now + GF.REVOKED_KEEP_S + 10 ** 4)
            per = __import__("json").loads((root / "revoked.json").read_text())["per"]
            self.assertEqual(list(per), [B], "30 天前的清掉")


PROBE = r'''
import asyncio, json, sys, time
sys.path.insert(0, sys.argv[1])
import chat_nostr as CN
import chat_roster as CR
import dm_global_files as GF
import nostr_keys as NK
import room_snapshot as RS
from pathlib import Path
root, ops = Path(sys.argv[2]), json.loads(sys.argv[3])
NOW = int(time.time())
tm = lambda x: NOW + int(x) if isinstance(x, str) else x          # "+5" / "-100" = 相對現在;數字 = 絕對(過去)
rel = lambda v: v - NOW if isinstance(v, int) and v > 10 ** 9 else v
B = NK.npub(NK.pubkey_hex("%064x" % 12))
B_SEAT = NK.npub(NK.pubkey_hex("%064x" % 0x1201))
EVE_SEAT = NK.npub(NK.pubkey_hex("%064x" % 0xe0e1))
STRANGER = NK.npub(NK.pubkey_hex("%064x" % 0x5555))
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
    calls = 0
    async def _resolve(self, addr):
        Sender.calls += 1
        return (0, {"seat": {"npub": B_SEAT, "seat": "bbbb2"}}) if addr == B_ADDR else (2, {})

r = object.__new__(CN.Receiver)
r.root, r.sender, r.heard = root, Sender(), {B}
snap = RS.encode({"v": 2, "people": []})
out = {}
for op in ops:
    kind = op[0]
    frm = EVE_SEAT if op[-1] == "eve" else B_SEAT
    if kind == "room":
        asyncio.run(r.on_room(Rumor(tm(op[1]), snap), {"aib-from": B_ADDR}, frm))
    elif kind == "presence":
        body = json.dumps({"v": 1, "seats": [{"seat": "bbbb2", "cabin": "c1", "state": "online"}]})
        asyncio.run(r.on_presence(Rumor(tm(op[1]), body), {"aib-from": B_ADDR}, frm))
    elif kind == "seed-room":                                   # 手上已有一份:收到時間 op[1]、寄出時間 op[2]
        GF.write_room(root, B, {"v": 2, "people": []}, now=tm(op[1]), sent_at=tm(op[2]))
    elif kind == "seed-revoked":                                # 壞帳
        (root / "revoked.json").write_text(json.dumps(op[1]))
    elif kind == "seed-rv":                                     # 手上已有一筆撤回:種類、寄出時間、收到時間
        GF.note_revoked(root, B, op[1], tm(op[2]), now=tm(op[3]))
    elif kind == "sleep":
        time.sleep(op[1])
    elif kind == "break-ledger":
        def boom(*a, **k):
            raise OSError("ledger full")
        GF.note_revoked = boom
    elif kind == "wait":
        out["wait"] = [CN.revoke_wait(n) for n in op[1]]
    elif kind == "rv-stranger":
        tags = {"aib-from": STRANGER + "/c1/eeee5", "aib-revoke": "1"}
        asyncio.run(r.on_room(Rumor(tm(op[1]), "{}"), tags, EVE_SEAT))
    else:
        tags = {"aib-from": B_ADDR, "aib-revoke": "1"}
        rv = Rumor(tm(op[1]), json.dumps({"v": 1, "revoke": True}))
        asyncio.run(r.on_room(rv, tags, frm) if kind == "rv-space" else r.on_presence(rv, tags, frm))
room = GF.read_room(root, B)
out.update({"room": room and rel(room.get("sent_at")),
            "world": sorted(rel(v.get("sent_at")) for v in GF.read_world(root).values() if v.get("owner") == B),
            "resolves": Sender.calls, "ledger": (root / "revoked.json").exists()})
print(json.dumps(out))
'''
NOSTR_VENV = os.environ.get("AIB_TEST_NOSTR_VENV") or ""
VPY = str(Path(NOSTR_VENV) / "bin" / "python") if NOSTR_VENV else ""
FAKE_NOSTR = '''
class _Any:
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _Any()

    def __getattr__(self, name):
        return _Any()

    def __mro_entries__(self, bases):
        return (object,)


def __getattr__(name):
    return _Any()
'''


class Order(unittest.TestCase):
    def probe(self, *ops):
        td = Path(tempfile.mkdtemp(prefix="aib-revoke-p-"))
        self.addCleanup(shutil.rmtree, td, True)
        (td / "root").mkdir()
        env = dict(os.environ, HOME=str(td / "home"), EZ2_AIB_STATE_ROOT=str(td / "home" / ".ez2-aib"))
        py = VPY
        if not SB.HAVE_NOSTR:                    # 沒 venv:假 nostr_sdk(收件端這幾條路不碰它),照樣跑真程式
            (td / "fake").mkdir()
            (td / "fake" / "nostr_sdk.py").write_text(FAKE_NOSTR)
            env["PYTHONPATH"], py = str(td / "fake"), sys.executable
        r = subprocess.run([py, "-B", "-c", PROBE, str(REPO / "src" / "cockpit"), str(td / "root"), json.dumps(ops)],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_o1_old_revoke_late(self):
        self.assertEqual(self.probe(["room", "-200"], ["rv-space", "-300"])["room"], -200, "舊撤回晚到把重開的分享刪了")
        self.assertIsNone(self.probe(["room", "-300"], ["rv-space", "-200"])["room"], "照順序的撤回要刪")

    def test_o2_old_snapshot_late(self):
        self.assertIsNone(self.probe(["rv-space", "-200"], ["room", "-300"])["room"], "撤回前寄的舊快照又寫回來")
        self.assertIsNone(self.probe(["rv-space", "-200"], ["room", "-200"])["room"], "同一秒算撤回贏")
        self.assertEqual(self.probe(["rv-space", "-200"], ["room", "-100"])["room"], -100, "撤回後重開的分享要收")

    def test_o3_presence(self):
        self.assertEqual(self.probe(["rv-share", "-200"], ["presence", "-300"])["world"], [], "撤回前寄的舊在線又寫回來")
        self.assertEqual(self.probe(["presence", "-100"], ["rv-share", "-200"])["world"], [-100], "舊撤回晚到刪了新在線")
        self.assertEqual(self.probe(["presence", "-300"], ["rv-share", "-200"])["world"], [], "在線撤回要刪")

    def test_o4_older_snapshot_does_not_overwrite(self):
        self.assertEqual(self.probe(["room", "-100"], ["room", "-300"])["room"], -100)
        self.assertEqual(self.probe(["presence", "-100"], ["presence", "-300"])["world"], [-100])

    def test_f1_forged_revoke(self):
        got = self.probe(["room", "-300"], ["presence", "-300"], ["rv-space", "-200", "eve"], ["rv-share", "-200", "eve"])
        self.assertEqual((got["room"], got["world"]), (-300, [-300]), "第三方席冒 Bob 的位址就能刪 Andy 手上的資料")

    def test_o5_fast_clock_out_of_order(self):
        got = self.probe(["rv-space", "+3600"], ["sleep", 1.2], ["room", "+3500"])
        self.assertIsNone(got["room"], "Bob 快 1 小時:撤回前寄的舊快照跨秒晚到又復活")
        got = self.probe(["rv-share", "+3600"], ["sleep", 1.2], ["presence", "+3500"])
        self.assertEqual(got["world"], [], "在線同理")
        self.assertEqual(self.probe(["rv-space", "+3600"], ["room", "+3700"])["room"], 3700, "撤回後寄的照收")

    def test_o7_slow_clock(self):
        got = self.probe(["rv-space", "-3456000"], ["sleep", 1.2], ["room", "-3456100"])
        self.assertEqual((got["room"], got["ledger"]), (None, True), "Bob 慢 40 天:撤回帳一寫就被清、舊快照被收下")

    def test_o8_future_revoke_expires(self):
        late = "-%d" % (2 * 86400 + 601)
        self.assertEqual(self.probe(["seed-rv", "space", "+1000000", late], ["room", "+5"])["room"], 5,
                         "未來時間的撤回收到超過 2 天 10 分還在擋重開")
        self.assertIsNone(self.probe(["seed-rv", "space", "+1000000", "-100"], ["room", "+5"])["room"], "2 天內照擋")
        self.assertIsNone(self.probe(["room", "+1000000"], ["rv-space", "+1000100"])["room"], "同一個時鐘:更晚的撤回照刪")

    def test_o6_stale_copy_does_not_block(self):
        got = self.probe(["seed-room", "-700000", "-100"], ["room", "-200"])
        self.assertEqual(got["room"], -200, "手上那份已過期(超過 7 天),不准拿來擋新收的")
        self.assertEqual(self.probe(["seed-room", "-10", "-100"], ["room", "-200"])["room"], -100, "新鮮的照擋舊的")

    def test_s1_stranger_revoke_cheap(self):
        got = self.probe(["rv-stranger", "+0"], ["rv-stranger", "+1"])
        self.assertEqual((got["resolves"], got["ledger"]), (0, False), "陌生人的撤回要在查名冊前就丟、不寫帳")
        got = self.probe(["rv-space", "+0"])
        self.assertEqual((got["resolves"], got["ledger"]), (1, True), "好友的撤回照記(之後補收的舊快照要丟)")

    def test_s2_bad_ledger(self):
        for bad in ({"per": {"B": "str"}}, {"per": "x"}, [1], {"per": {"B": {"space": "abc", "share": None}}}):
            bad = json.loads(json.dumps(bad).replace('"B"', json.dumps(B)))
            got = self.probe(["seed-revoked", bad], ["room", "+0"], ["presence", "+0"], ["rv-space", "+1"])
            self.assertEqual((got["room"], got["world"]), (None, [0]), bad)
        got = self.probe(["room", "+0"], ["break-ledger"], ["rv-space", "+1"])
        self.assertIsNone(got["room"], "記帳失敗也要先刪")

    def test_s3_backoff_no_overflow(self):
        got = self.probe(["wait", [0, 1, 2, 1025, 2000]])["wait"]
        self.assertEqual(got[:2], [0, 120])
        self.assertEqual(got[3:], [3600, 3600], "n 很大不准丟例外、要封頂 1 小時")


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


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr, "紅要是斷言紅,不是炸掉:" + r.stdout + r.stderr)

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(E1 要真轉接程式)")
    def test_k1_sender_no_revoke(self):
        self._knife("src/cockpit/chat_nostr.py",
                    'if await self._push_revoke(tgt, owner, mine_seats[0], "space", now):',
                    'if False:', "Revoke.test_e1_revoke")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(E1 要真轉接程式)")
    def test_k2_receiver_keeps(self):
        self._knife("src/cockpit/chat_nostr.py", "            GF.drop_room(self.root, owner, upto=at)\n",
                    "            pass\n", "Revoke.test_e1_revoke")

    def test_k3_no_npub_check(self):
        self._knife("src/cockpit/chat_nostr.py",
                    '        if rc != 0 or res["seat"]["npub"] != from_npub:\n            return\n        at = sent_at(rumor)',
                    '        if rc != 0:\n            return\n        at = sent_at(rumor)', "Order.test_f1_forged_revoke")

    def test_k4_room_ignores_revoked(self):
        self._knife("src/cockpit/chat_nostr.py",
                    'if sent <= GF.read_revoked(self.root, owner).get("space", 0) or ',
                    "if ", "Order.test_o2_old_snapshot_late")

    def test_k5_drop_ignores_time(self):
        self._knife("src/cockpit/dm_global_files.py",
                    "        if rec and int_or0(rec.get(\"sent_at\")) > upto and now - int_or0(rec.get(\"at\")) < ROOM_FRESH_S:\n",
                    "        if False:\n", "Order.test_o1_old_revoke_late")

    def test_k10_clamp_to_now(self):
        self._knife("src/cockpit/chat_nostr.py", "    return rumor.created_at().as_secs()\n",
                    "    t, now = rumor.created_at().as_secs(), int(time.time())\n    return now if t > now + 600 else t\n",
                    "Order.test_o5_fast_clock_out_of_order")

    def test_k11_purge_by_sent_time(self):
        self._knife("src/cockpit/dm_global_files.py", 'int_or0(kv[1].get(k + "_at")) for k in ("space", "share")',
                    'int_or0(kv[1].get(k)) for k in ("space", "share")', "Order.test_o7_slow_clock")

    def test_k12_revoke_never_expires(self):
        self._knife("src/cockpit/dm_global_files.py",
                    '            if k in row and now - int_or0(row.get(k + "_at")) < REVOKE_BACKFILL_S}',
                    "            if k in row}", "Order.test_o8_future_revoke_expires")

    def test_k8_no_cheap_check(self):
        self._knife("src/cockpit/chat_nostr.py", "        if not held and not (CR.is_friend(owner) or CR.is_mine(owner)):\n",
                    "        if False:\n", "Order.test_s1_stranger_revoke_cheap")

    def test_k9_no_exponent_cap(self):
        self._knife("src/cockpit/chat_nostr.py", "2 ** min(n - 1, 20)", "2 ** (n - 1)", "Order.test_s3_backoff_no_overflow")

    def test_k6_presence_revoke_keeps(self):
        self._knife("src/cockpit/chat_nostr.py", "            GF.drop_world_owner(self.root, owner, upto=at)\n",
                    "            pass\n", "Order.test_o3_presence")


if __name__ == "__main__":
    unittest.main()
