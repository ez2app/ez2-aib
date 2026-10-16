#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球對談包 F(開發文件第 29 節):好友申請、封鎖、聯絡人快照、艦長室通訊錄 / 收件匣的介面。

核心(一定跑):
C1 封鎖:block add 順手移出好友;封鎖中 friend add ⇒ rc 2;block remove 後才加得回來
C2 aib chat contacts --json:五份名單 + peers 快照合成;relation 六種、seats 帶 addr、順序 request_in 在最前;不含內文
C3 request accept / reject:accept ⇒ 加好友(source request)+ delivery delivered;reject ⇒ delivery refused(reason rejected);
   --silent ⇒ 不寫 delivery;不存在的申請 ⇒ rc 2
C4 佇列列的 global 欄:contact_key = global:<主人 npub>、relation 照匯入的分類
連 Nostr(要 AIB_TEST_NOSTR_VENV;本機假中繼站):
N1 A 送申請(附一句話)⇒ rc 3 等對方同意;B 的 requests 有 A(名冊上的名字);B 同意 ⇒ A 的通訊錄從 request_out 變 friend;
   A 寄 dm ⇒ queued;B 的申請不進 AI 佇列
N2 B 封鎖 A ⇒ A 寄的信靜默丟:沒有回條(rc 4)、B 的佇列與拒收紀錄都沒有
N3 B 拒絕 ⇒ A 的通訊錄 request_out 狀態 rejected
刀(複本):K1 收件不看封鎖 ⇒ N2 紅;K2 申請不記 ⇒ N1 紅;K3 列不帶 contact_key ⇒ C4 紅
"""
import importlib.util
import json
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_recv_pf", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)
SB = RT.SB
P = [NK.npub(NK.pubkey_hex("%064x" % (60 + i))) for i in range(8)]


def js(r):
    return json.loads(r.stdout)


class Core(unittest.TestCase):
    def setUp(self):
        self.b = SB.Box()
        self.addCleanup(self.b.close)
        self.b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")
        self.assertEqual(self.b.cli("prepare", "--keys-only", "--quiet").returncode, 0)
        GF.ensure_dirs(self.b.root)

    def test_c1_block(self):
        b = self.b
        b.cli("friend", "add", P[0])
        self.assertEqual(b.cli("block", "add", P[0]).returncode, 0)
        self.assertEqual(js(b.cli("friends", "--json"))["friends"], [], "封鎖要順手移出好友")
        r = b.cli("friend", "add", P[0])
        self.assertEqual(r.returncode, 2)
        self.assertIn("block remove", r.stdout)
        self.assertEqual([x["owner"] for x in js(b.cli("block", "--json"))["blocks"]], [P[0]])
        self.assertEqual(b.cli("block", "remove", P[0]).returncode, 0)
        self.assertEqual(b.cli("friend", "add", P[0]).returncode, 0)

    def test_c2_contacts(self):
        b, root = self.b, self.b.root
        b.cli("friend", "add", P[0], "--name", "Bob")
        b.cli("mine", "add", P[1])
        b.cli("block", "add", P[2])
        import chat_roster as CR
        CR.add_friend(P[3], "Req", b.env(), source="request")
        GF.add_request(root, {"owner": P[4], "display_name": "Cara", "country": "JP", "note": "hi", "msgid": "c" * 32})
        (root / "refused.json").write_text(json.dumps({"v": 1, "refused": [
            {"who": P[5], "owner": True, "count": 2, "reason": "not_friend", "last_at": int(time.time()), "display_name": "Dan"}]}))
        GF.note_peer(root, P[0], "Bobby", "TW", {"cabin": "931e", "seat": "bbbb2", "npub": P[6], "name": "收件席", "role": "dev"},
                     direction="in")
        pl = js(b.cli("contacts", "--json"))
        rel = {c["owner"]: c["relation"] for c in pl["contacts"]}
        self.assertEqual(rel, {P[0]: "friend", P[1]: "mine", P[2]: "blocked", P[3]: "request_out", P[4]: "request_in",
                               P[5]: "refused"})
        self.assertEqual(pl["contacts"][0]["relation"], "request_in")
        bob = next(c for c in pl["contacts"] if c["owner"] == P[0])
        self.assertEqual((bob["key"], bob["label"], bob["display_name"], bob["n_in"]), ("global:" + P[0], "Bob", "Bobby", 1))
        self.assertEqual(bob["seats"][0]["addr"], "%s/931e/bbbb2" % P[0])
        cara = next(c for c in pl["contacts"] if c["owner"] == P[4])
        self.assertEqual((cara["display_name"], cara["request"]["note"]), ("Cara", "hi"))
        self.assertEqual(next(c for c in pl["contacts"] if c["owner"] == P[5])["n_refused"], 2)
        self.assertEqual(next(c for c in pl["contacts"] if c["owner"] == P[3])["request"]["state"], "pending")
        GF.note_peer(root, P[3], request="accepted")
        self.assertEqual({c["owner"]: c["relation"] for c in js(b.cli("contacts", "--json"))["contacts"]}[P[3]], "friend")
        self.assertIn("🤝", b.cli("contacts").stdout)

    def test_c3_accept_reject(self):
        b, root = self.b, self.b.root
        for i, m in ((4, "a"), (5, "b"), (6, "d")):
            GF.add_request(root, {"owner": P[i], "display_name": "X%d" % i, "note": "", "msgid": m * 32})
        self.assertEqual(b.cli("request", "accept", P[4]).returncode, 0)
        f = js(b.cli("friends", "--json"))["friends"]
        self.assertEqual([(x["owner"], x.get("source")) for x in f], [(P[4], "accepted")])
        self.assertEqual([r["state"] for r in GF.read_delivery(root, "a" * 32)], ["delivered"])
        self.assertEqual(b.cli("request", "reject", P[5]).returncode, 0)
        self.assertEqual([(r["state"], r.get("reason")) for r in GF.read_delivery(root, "b" * 32)], [("refused", "rejected")])
        self.assertEqual(b.cli("request", "reject", P[6], "--silent").returncode, 0)
        self.assertEqual(GF.read_delivery(root, "d" * 32), [])
        self.assertEqual(GF.read_requests(root), [])
        self.assertEqual(b.cli("request", "accept", P[7]).returncode, 2)
        # 申請紀錄壞了(msgid 不合,@74e8b 22:50 回報)⇒ 照樣同意、白話說通知不到,不丟例外
        GF.add_request(root, {"owner": P[7], "display_name": "壞", "note": "", "msgid": "not-a-msgid"})
        r = b.cli("request", "accept", P[7])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("沒辦法通知對方", r.stdout)
        self.assertNotIn("Error", r.stdout + r.stderr)
        self.assertIn(P[7], [x["owner"] for x in js(b.cli("friends", "--json"))["friends"]])


class Row(RT.CoreBox):
    def test_c4_row_contact_key(self):
        owner = NK.npub(NK.pubkey_hex("%064x" % 22))
        rec = RT.inbox_rec(self.seat, verified=True, cls="friend", addr="%s/931e/bbbbb" % owner)
        iid = self.imp(rec)[0]["id"]
        r = self.cli("list", "--seat", self.seat, "--tab", "all")
        row = next(x for x in json.loads(r.stdout) if x["id"] == iid)
        self.assertEqual((row["global"]["contact_key"], row["global"]["relation"]), ("global:" + owner, "friend"))


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    box_extra = {"AIB_CHAT_REFUSE_EVERY": "0"}
    setUp = RT.Network.setUp
    _box = RT.Network._box
    items = RT.Network.items

    def pair(self):
        a, (x,) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        b, (y,) = self._box("Bob", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True])
        return a, x, b, y

    def rel(self, box, owner):
        return next((c for c in js(box.cli("contacts", "--json"))["contacts"] if c["owner"] == owner), None)

    def test_n1_request_accept_then_talk(self):
        a, x, b, y = self.pair()
        r = a.cli("request", "%s/6ddf/%s" % (b.owner, y), "--note", "你好,我是 Andy")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("等對方同意", r.stdout)
        req = GF.read_requests(b.root)
        self.assertEqual([(q["owner"], q["display_name"], q["note"]) for q in req], [(a.owner, "Andy", "你好,我是 Andy")])
        self.assertEqual(self.items(b, y), [], "申請不准進 AI 佇列")
        self.assertEqual(self.rel(b, a.owner)["relation"], "request_in")
        self.assertEqual(self.rel(a, b.owner)["relation"], "request_out")
        self.assertEqual(b.cli("request", "accept", a.owner).returncode, 0)
        self.assertTrue(a.wait_for(lambda: (self.rel(a, b.owner) or {}).get("relation") == "friend", 30), self.rel(a, b.owner))
        res = GF.send_and_wait(a.root, SB.outbox_rec("%s/6ddf/%s" % (b.owner, y), x), wait=20)
        self.assertEqual((res["rc"], res["stage"]), (0, "queued"), res)
        self.assertEqual(self.rel(b, a.owner)["relation"], "friend")
        self.assertTrue(self.rel(b, a.owner)["seats"], "收到驗得到的申請 / 信 ⇒ 快照有對方的席")

    def test_n2_block_silent(self):
        a, x, b, y = self.pair()
        b.cli("block", "add", a.owner)
        res = GF.send_and_wait(a.root, SB.outbox_rec("%s/6ddf/%s" % (b.owner, y), x), wait=6)
        self.assertEqual((res["rc"], res["stage"]), (4, "relayed"), res)
        time.sleep(2)
        self.assertEqual(self.items(b, y), [])
        self.assertFalse((b.root / "refused.json").exists(), "封鎖的不記拒收")
        self.assertIn('"blocked"', "".join(p.read_text() for p in (b.root / "audit").glob("*.jsonl")))

    def test_n3_reject(self):
        a, x, b, y = self.pair()
        self.assertEqual(a.cli("request", "%s/6ddf/%s" % (b.owner, y)).returncode, 3)
        self.assertEqual(b.cli("request", "reject", a.owner).returncode, 0)
        self.assertTrue(a.wait_for(lambda: ((self.rel(a, b.owner) or {}).get("request") or {}).get("state") == "rejected", 30),
                        self.rel(a, b.owner))


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k1_ignore_block(self):
        self._knife("src/cockpit/chat_nostr.py", "        if CR.is_blocked(who) or CR.is_blocked(from_npub):",
                    "        if False:", "Network.test_n2_block_silent")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_request_not_stored(self):
        self._knife("src/cockpit/chat_nostr.py", "        if not GF.add_request(self.root, row, every=REFUSE_EVERY_S):",
                    "        if True:", "Network.test_n1_request_accept_then_talk")

    def test_k3_row_without_contact_key(self):
        self._knife("src/cockpit/dm_queue.py", '        row["global"]["contact_key"] = "global:" + owner if owner else None\n',
                    "", "Row.test_c4_row_contact_key")


if __name__ == "__main__":
    unittest.main()
