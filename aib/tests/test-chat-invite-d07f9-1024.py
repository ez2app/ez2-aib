#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""加好友邀請(10-24 Andy「全照做」清單第 11 條):A 產碼 ⇒ B invite use ⇒ A 的轉接程式自動同意、雙方互為好友。

I1 create_invite / take_invite:碼 10 碼 base32;用一次就沒;過期不收;亂碼不收;只留 20 組
I2 aib chat invite --json:沒有公開席 ⇒ rc 2;有 ⇒ addr / code / uri(aib:join?to=…&code=…)/ command
I3 aib chat invite use:格式不對 ⇒ rc 2、不加好友;整串 uri 也認得(invite use 要寄信,寄件那段在 I4 / 端到端驗)
(以下要 AIB_TEST_NOSTR_VENV;chat_nostr 只在 venv 裡 import 得到。不連網)
I4 on_request:驗得到的陌生人帶 invite:<對得上的碼> ⇒ 加成好友(source=invite)、回 delivered、不進申請清單;
   碼用過 / 亂碼 ⇒ 照舊進申請清單、回 held;封鎖的人帶對的碼 ⇒ 不加好友
10-24 晚 Andy「位址太長、照 10 碼做」⇒ 碼放中繼站(d=aib-invite:<PBKDF2 雜湊>),對方只打碼:
I5 碼的寫法:小寫、中間一個 - 或空白、0/1/8 打成 O/I/B 都認;不像碼 ⇒ None;雜湊 32 位十六進位、碼不同雜湊不同
I6 _publish_invite(換掉 _run_nostr):中繼站上已有同一個雜湊 ⇒ 換一組再發;沒 venv ⇒ 不放(碼照樣能配位址用)
I7 _resolve_invite:兩個簽章者 ⇒ clash;最新那筆撤了 / 過期 ⇒ used;簽章者不是位址主人 ⇒ wrong_signer;
   正常 ⇒ 位址 + 印對方名字(控制字元清掉);invite use <只有碼> 查不到 ⇒ rc 2、不加好友
E1 本機假中繼站 + 兩台真轉接程式:Andy invite ⇒ Bob invite use <碼(小寫)> ⇒ 兩邊互為好友;
   中繼站上那筆換成 revoked;同一組碼再用 ⇒ rc 2
#299 第 1 輪審(H1 / M1–M3 / L1–L4):
I8 碼 ⇒ PBKDF2(鹽帶建立日期)前 16 查詢、後 16 金鑰;內容加密 + HMAC 綁簽章者:換簽章者 / 改一個字 / 錯的碼 ⇒ 打不開
I9 冒名:中繼站上只有別人用自己金鑰發的同一個 d(內容亂填 / 抄正牌密文換簽章者)⇒ not_found 或只認正牌,不算 clash、不加好友
I10 產碼要本人:席位呼叫 ⇒ rc 2、Touch ID 拒 ⇒ rc 3,都不產碼(M3)
I11 昨天建的碼今天照查得到;遠端 exp 不是整數 ⇒ used 不炸(L1);撞號換組時舊碼作廢(L3);加好友失敗次數補回(L2)
刀(複本):K1 take_invite 不扣次數 ⇒ I1 紅;K2 on_request 不看碼對不對 ⇒ I4 紅;K3 不擋撞號 ⇒ I7 紅;
   K4 不驗 HMAC ⇒ I9 紅;K5 產碼不過門 ⇒ I10 紅
#299 第 2 輪審(M1–M3、L-a..L-g):
I12 invite use:沒公開席 / 轉接程式沒開 ⇒ 門前擋、好友沒加;寄送保證沒送出(abort / bridge-off / 逾時撤回)⇒ 撤掉新加的好友;
    held / claimed / relayed 可能已到 ⇒ 不撤;本來就是好友 ⇒ 不撤(M2);撤的時候本人設過的單人等級不清(#320 forget=False)
I13 invite use --json:每種出口 stdout 剛好一行 JSON(v / ok / why / hint)(M3);queued ⇒ ok + request_pending(r3 L1);
    每天的查詢都失敗 ⇒ lookup_failed,不是 not_found(r3 L3)
I14 對方時鐘快 10 分、昨天快過午夜建的碼都查得到(建立日期落在 now+600 / now / now-1 天 / now-1 天-600)(L-a)
I4 加好友失敗 ⇒ 次數補回、落回一般申請(held、進申請清單)(L-d);I8 MAC 非 ASCII / 長度錯 ⇒ None 不炸(M1)
刀:K6 不撤好友 ⇒ I12 紅;刀測一律比對紅的是哪一格(L-g)
"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import chat_roster as CR  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_bridge_invite", str(COCKPIT / "chat_bridge.py"))
CB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CB)

NOSTR_VENV = os.environ.get("AIB_TEST_NOSTR_VENV") or ""
VPY = str(Path(NOSTR_VENV) / "bin" / "python") if NOSTR_VENV else ""
HAVE_NOSTR = bool(VPY) and Path(VPY).exists() and subprocess.run(
    [VPY, "-c", "import nostr_sdk"], capture_output=True).returncode == 0

PROBE = r'''
import asyncio, json, sys
sys.path.insert(0, sys.argv[1])
import chat_nostr as CN
import chat_roster as CR
import dm_global_files as GF
import nostr_keys as NK
from pathlib import Path
root, case = Path(sys.argv[2]), sys.argv[3]
B = NK.npub(NK.pubkey_hex("%064x" % 0x8102))
code = GF.create_invite(root)["code"]
if case == "used":
    GF.take_invite(root, code)
if case == "blocked":
    CR.add_friend(B, kind="blocks")
if case == "roster_err":
    def boom(*a, **k):
        raise CR.RosterError("bad_name")
    CR.add_friend = boom
note = "invite:" + ("A" * 10 if case == "junk" else code)
r = object.__new__(CN.Receiver)
r.root = root
sent = []
async def send_receipt(msgid, stage, **k):
    sent.append(stage)
r.send_receipt = send_receipt
rec = {"class": "stranger", "verified": True, "content": note, "display_name": "Bob", "country": "TW",
       "from_addr": B + "/754c/bbbb2"}
peer = {"seat": {"npub": "npub1x", "seat": "bbbb2"}, "cabin": "754c", "display_name": "Bob"}
asyncio.run(r.on_request(rec, {"owner": B, "cabin": "754c", "seat": "bbbb2"}, peer, "m" * 32, "npub1x"))
fr = CR.read_friends()
print(json.dumps({"sent": sent, "friends": [(f["owner"] == B, f.get("source")) for f in fr],
                  "requests": len(GF.read_requests(root)), "left": bool(GF.take_invite(root, code))}))
'''


class Store(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-invite-"))
        self.addCleanup(shutil.rmtree, self.td, True)

    def test_i1_take_once(self):
        inv = GF.create_invite(self.td, now=1000)
        self.assertRegex(inv["code"], r"^[A-Z2-7]{10}$")
        self.assertEqual((inv["expires_at"], inv["uses"]), (1000 + GF.INVITE_TTL_S, 1))
        self.assertFalse(GF.take_invite(self.td, "AAAAAAAAAA", now=1001))
        self.assertFalse(GF.take_invite(self.td, "bad", now=1001))
        self.assertTrue(GF.take_invite(self.td, inv["code"], now=1001))
        self.assertFalse(GF.take_invite(self.td, inv["code"], now=1002))            # 用過了
        old = GF.create_invite(self.td, now=1000)
        self.assertFalse(GF.take_invite(self.td, old["code"], now=1000 + GF.INVITE_TTL_S))   # 過期
        again = GF.create_invite(self.td, now=1000)
        self.assertEqual(GF.take_invite(self.td, again["code"], now=1001)["created_at"], 1000, "回那一筆(推金鑰要建立時間)")
        GF.refund_invite(self.td, again["code"])
        self.assertTrue(GF.take_invite(self.td, again["code"], now=1002), "補回後能再用一次")
        GF.drop_invite(self.td, again["code"])
        GF.refund_invite(self.td, again["code"])
        self.assertFalse(GF.take_invite(self.td, again["code"], now=1003), "作廢的補不回來")
        for i in range(25):
            GF.create_invite(self.td, now=2000 + i)
        rows = json.loads((self.td / "invites.json").read_text())["invites"]
        self.assertEqual(len(rows), 20)


class Cli(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-invite-cli-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.env = {"HOME": str(self.td), "EZ2_AIB_STATE_ROOT": str(self.td / ".ez2-aib")}
        self.asked, self.seat, self.deny = [], False, None
        CB.HUMAN_GATE["prove"] = lambda reason, env: (self.asked.append(reason), ("touchid", self.deny))[1]
        CB.HUMAN_GATE["caller_is_seat"] = lambda env: self.seat
        self.addCleanup(CB.HUMAN_GATE.update, {"prove": None, "caller_is_seat": None})

    def cli(self, *args):
        buf = io.StringIO()
        rc = CB.main(list(args), env=self.env, out=buf)
        return rc, buf.getvalue()

    def test_i2_invite(self):
        self.assertEqual(self.cli("invite", "--json")[0], 2)
        owner = NK.ensure_owner(self.env)
        CR.set_profile("A", "TW", self.env)
        CR.set_public("cfd04", True, self.env)
        orig = NK.sending_seats
        self.addCleanup(setattr, NK, "sending_seats", orig)
        NK.sending_seats = lambda env=None, live=None: [{"seat5": "cfd04", "cabin": "6ddf", "npub": "npub1a", "key": "k"}]
        rc, out = self.cli("invite", "--json")
        self.assertEqual(rc, 0, out)
        d = json.loads(out)
        self.assertEqual(d["addr"], owner + "/6ddf/cfd04")
        self.assertEqual(d["uri"], "aib:join?to=%s&code=%s" % (d["addr"], d["code"]))
        self.assertEqual(d["command"], "aib chat invite use %s %s" % (d["addr"], d["code"]))
        self.assertTrue(GF.take_invite(CB.bridge_root(self.env), d["code"]))
        self.assertIsNone(d["short"], "沒 venv ⇒ 沒放中繼站,不給只打碼的指令")
        self.assertEqual(len(self.asked), 1)
        self.assertIn("邀請", self.asked[0])

    def test_i10_invite_needs_human(self):
        NK.ensure_owner(self.env)
        CR.set_profile("A", "TW", self.env)
        CR.set_public("cfd04", True, self.env)
        orig = NK.sending_seats
        self.addCleanup(setattr, NK, "sending_seats", orig)
        NK.sending_seats = lambda env=None, live=None: [{"seat5": "cfd04", "cabin": "6ddf", "npub": "npub1a", "key": "k"}]
        self.seat = True
        rc, out = self.cli("invite", "--json")
        self.assertEqual((rc, json.loads(out).get("why"), self.asked), (2, "seat_caller", []), "席位不准產碼")
        self.seat, self.deny = False, "touchid_cancel"
        rc, out = self.cli("invite", "--json")
        self.assertEqual((rc, json.loads(out).get("why")), (3, "touchid_cancel"))
        self.assertFalse((CB.bridge_root(self.env) / "invites.json").exists(), "沒過門就不准產碼")

    def test_i3_join_bad(self):
        other = NK.npub(NK.pubkey_hex("%064x" % 0x8101))
        for args in (("invite", "use", other + "/6ddf/cfd04"), ("invite", "use", other + "/6ddf/cfd04", "short"),
                     ("invite", "use", "x", "AAAAAAAAAA"), ("invite", "use", "aib:join?to=%s/6ddf/cfd04&code=bad" % other)):
            self.assertEqual(self.cli(*args)[0], 2, args)
        self.assertEqual(CR.read_friends(self.env), [])
        self.assertEqual(CB.JOIN_URI_RE.match("aib:join?to=%s/6ddf/cfd04&code=AAAAAAAAAA" % other).group(2), "AAAAAAAAAA")


A1 = NK.npub(NK.pubkey_hex("%064x" % 0x8201))
C1 = NK.npub(NK.pubkey_hex("%064x" % 0x8203))


class Short(unittest.TestCase):
    def setUp(self):
        # 查碼會連「現在 +10 分」那天一起查 ⇒ UTC 23:50 後跑,多查一天、fetches 排的順序錯位(10-29 CI ubuntu 3.14 紅過)。
        # 時鐘平移到今天 UTC 中午(照樣往前走),測試端與 chat_bridge 看同一個鐘;要測跨天的格(I14)自己再換。
        real, start = time.time, time.time()
        shift = (int(start) // 86400 * 86400 + 43200) - start
        patcher = mock.patch.object(time, "time", lambda: real() + shift)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.td = Path(tempfile.mkdtemp(prefix="aib-invite-s-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.env = {"HOME": str(self.td), "EZ2_AIB_STATE_ROOT": str(self.td / ".ez2-aib"), "EZ2_AIB_LOCALE": "zh-TW"}
        spec = importlib.util.spec_from_file_location("chat_bridge_invite_s", str(COCKPIT / "chat_bridge.py"))
        self.CB = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.CB)
        self.calls, self.fetches, self.by_hash = [], [], None
        self.addr = A1 + "/6ddf/abcde"
        self.look = {"ok": True, "owner": A1, "device": A1, "display_name": "Bob\x1b[31m", "country": "TW"}

        def fake(cmd, root, env, timeout=60, extra=(), stdin=None):
            self.calls.append(cmd)
            if cmd == "invite-fetch":
                if self.by_hash is not None:
                    return 0, {"ok": True, "hits": self.by_hash.get(extra[0], [])}
                return 0, {"ok": True, "hits": self.fetches.pop(0) if self.fetches else []}
            if cmd == "invite-publish":
                self.published = stdin
                return 0, {"ok": True}
            return 0, self.look
        self.CB._run_nostr = fake

    def body(self, author=None, at=1, day=None, code="ABCDEFGHIJ", seal_as=None, **kw):
        """中繼站上的一筆:預設是知道碼的 A1 簽的。seal_as = 用誰的名字算 HMAC(抄密文換簽章者的情境)。"""
        b = {"addr": self.addr, "exp": int(time.time()) + 600}
        b.update(kw)
        key = GF.invite_keys(code, day or GF.invite_day(time.time()))[1]
        return {"author": author or A1, "at": at, "body": GF.invite_seal(key, seal_as or author or A1, b)}

    def test_i5_code_shapes(self):
        c = GF.create_invite(self.td)["code"]
        self.assertEqual(GF.invite_norm(GF.invite_fmt(c)), c)
        self.assertEqual(GF.invite_norm(" abcde 1o8z2 "), "ABCDEIOBZ2", "小寫、空白、1/0/8")
        for bad in ("", "ABCDE-FGHI", "ABCDE-FGHIJK", "ABCDE--FGHIJ", "ABCDE-FGHI9", None, A1):
            self.assertIsNone(GF.invite_norm(bad), bad)

    def test_i8_keys_and_seal(self):
        h, k = GF.invite_keys("ABCDEFGHIJ", "20261024")
        self.assertRegex(h, r"^[0-9a-f]{32}$")
        self.assertEqual(len(k), 16)
        self.assertNotEqual(h, k.hex(), "查詢雜湊跟金鑰是不同的兩段")
        self.assertNotEqual(h, GF.invite_keys("ABCDEFGHIK", "20261024")[0])
        self.assertNotEqual(h, GF.invite_keys("ABCDEFGHIJ", "20261025")[0], "鹽帶日期")
        box = GF.invite_seal(k, A1, {"addr": self.addr, "exp": 5})
        self.assertNotIn(self.addr, json.dumps(box), "中繼站上看不到位址")
        self.assertEqual(GF.invite_open(k, A1, box), {"addr": self.addr, "exp": 5})
        self.assertIsNone(GF.invite_open(k, C1, box), "換簽章者")
        self.assertIsNone(GF.invite_open(GF.invite_keys("ABCDEFGHIK", "20261024")[1], A1, box), "錯的碼")
        bad = dict(box, c=box["c"][:-4] + ("AAAA" if box["c"][-4:] != "AAAA" else "BBBB"))
        self.assertIsNone(GF.invite_open(k, A1, bad), "改一個字")
        for junk in (None, [], {"v": 1, "addr": self.addr}, dict(box, n="!!"), dict(box, v=3),
                     dict(box, m="\u00e9" * 64), dict(box, m=box["m"][:-1]), dict(box, m=box["m"] + "0"),
                     dict(box, m=box["m"].upper())):
            self.assertIsNone(GF.invite_open(k, A1, junk), junk)

    def test_i6_publish_precheck(self):
        root = self.td / "bridge"
        GF.ensure_dirs(root)
        self.fetches = [[self.body(author=C1)]]                    # 第一組撞了
        inv, short = self.CB._publish_invite(root, self.env, self.addr)
        self.assertTrue(short)
        self.assertEqual(self.calls, ["invite-fetch", "invite-fetch", "invite-publish"], "撞了要換一組再查再發")
        h, key = GF.invite_keys(inv["code"], GF.invite_day(inv["created_at"]))
        self.assertEqual((self.published["hash"], self.published["key"]), (h, key.hex()))
        self.assertEqual(self.published["addr"], self.addr)
        rows = json.loads((root / "invites.json").read_text())["invites"]
        self.assertEqual([r["code"] for r in rows], [inv["code"]], "撞號那組要作廢,不留 24 小時")
        self.CB._run_nostr = lambda *a, **k: (None, {})
        inv, short = self.CB._publish_invite(root, self.env, self.addr)
        self.assertFalse(short, "沒 venv ⇒ 不放")
        self.assertTrue(GF.take_invite(root, inv["code"]), "碼照樣記在本機")

    def test_i7_resolve(self):
        res = lambda: self.CB._resolve_invite("ABCDEFGHIJ", self.env, io.StringIO())  # noqa: E731
        self.assertEqual(res(), (None, "not_found"))
        self.fetches = [[self.body(), self.body(author=C1, at=2)]]
        self.assertEqual(res(), (None, "clash"), "同一組碼兩個簽章者 ⇒ 不猜")
        key = GF.invite_keys("ABCDEFGHIJ", GF.invite_day(time.time()))[1]
        self.fetches = [[self.body(at=1), {"author": A1, "at": 2, "body": GF.invite_seal(key, A1, {"revoked": True})}]]
        self.assertEqual(res(), (None, "used"))
        self.fetches = [[self.body(exp="x")]]
        self.assertEqual(res(), (None, "used"), "遠端 exp 不是整數 ⇒ 不炸")
        self.fetches = [[], [self.body(day=GF.invite_day(time.time() - 86400))]]
        self.assertEqual(res(), (self.addr, None), "昨天建的碼今天照查得到")
        self.fetches = [[self.body(exp=int(time.time()) - 1)]]
        self.assertEqual(res(), (None, "used"), "過期")
        self.fetches = [[self.body()]]
        self.look = dict(self.look, owner=C1, device=C1)
        self.assertEqual(res(), (None, "wrong_signer"))
        self.look = dict(self.look, owner=A1, device=A1)
        self.fetches = [[self.body()]]
        buf = io.StringIO()
        self.assertEqual(self.CB._resolve_invite("ABCDEFGHIJ", self.env, buf), (self.addr, None))
        self.assertIn("Bob", buf.getvalue())
        self.assertNotIn("\x1b", buf.getvalue(), "對方名字要清控制字元")
        out = io.StringIO()
        self.fetches = []
        self.assertEqual(self.CB.main(["invite", "use", "abcde-fghij"], env=self.env, out=out), 2)
        self.assertIn("找不到", out.getvalue())
        self.assertEqual(CR.read_friends(self.env), [])


    def test_i9_impostor(self):
        res = lambda: self.CB._resolve_invite("ABCDEFGHIJ", self.env, io.StringIO())  # noqa: E731
        evil = {"author": C1, "at": 9, "body": {"v": 2, "n": "AAAAAAAAAAAAAAAAAAAAAA==", "c": "", "m": "0" * 64}}
        self.fetches = [[evil], []]
        self.assertEqual(res(), (None, "not_found"), "只有冒名那筆 ⇒ 查不到,不准回攻擊者位址")
        copied = self.body(author=C1, at=9, seal_as=A1)              # 抄正牌密文、換成自己簽
        self.fetches = [[self.body(), copied, evil]]
        self.assertEqual(res(), (self.addr, None), "冒名的過濾掉後再判撞號:不准拿來擋人")
        self.fetches = [[evil], []]
        out = io.StringIO()
        self.assertEqual(self.CB.main(["invite", "use", "abcde-fghij"], env=self.env, out=out), 2)
        self.assertEqual(CR.read_friends(self.env), [], "冒名 ⇒ 不加好友、不寄碼")


class Use(unittest.TestCase):
    """invite use 的出口(#299 第 2 輪審 M2 / M3 / L-a)。"""
    body = Short.body

    def setUp(self):
        Short.setUp(self)
        self.pub = [{"cabin": "6ddf", "seat": "cfd04", "addr": "x"}]
        self.CB._my_published = lambda env: self.pub
        self.alive, self.sent = True, []
        self.stage = {"rc": 0, "stage": "delivered", "pos": None, "detail": None, "msgid": "m"}
        for name, fn in (("bridge_alive", lambda root, now=None, **k: self.alive),
                         ("send_and_wait", lambda root, rec, **k: (self.sent.append(rec), dict(self.stage))[1])):
            self.addCleanup(setattr, GF, name, getattr(GF, name))
            setattr(GF, name, fn)
        NK.ensure_owner(self.env)

    def use(self, *args):
        buf = io.StringIO()
        rc = self.CB.main(["invite", "use"] + list(args), env=self.env, out=buf)
        return rc, buf.getvalue()

    def test_i12_no_half_join(self):
        self.pub = []
        self.assertEqual(self.use(self.addr, "ABCDEFGHIJ")[0], 2)
        self.pub, self.alive = [{"cabin": "6ddf", "seat": "cfd04", "addr": "x"}], False
        self.assertEqual(self.use(self.addr, "ABCDEFGHIJ")[0], 2)
        self.assertEqual((CR.read_friends(self.env), self.sent), ([], []), "門前擋:好友沒加、沒寄")
        self.alive = True
        CR.set_share("status", A1, self.env)                         # 本人先前設過的單人等級(比預設寬)
        CR.set_space("room", A1, self.env)
        for stage in GF.UNSENT_STAGES:
            self.stage = {"rc": 2, "stage": stage, "pos": None, "detail": "x", "msgid": "m"}
            self.assertEqual(self.use(self.addr, "ABCDEFGHIJ")[0], 2, stage)
            self.assertEqual(CR.read_friends(self.env), [], "保證沒送出 ⇒ 新加的好友撤掉:" + stage)
            self.assertEqual((CR.read_share(self.env)["per"].get(A1), CR.read_space(self.env)["per"].get(A1)),
                             ("status", "room"), "撤好友不准清本人設的等級(forget=False)")
        for rc, stage in ((0, "held"), (4, "claimed"), (4, "relayed")):
            self.stage = {"rc": rc, "stage": stage, "pos": None, "detail": None, "msgid": "m"}
            self.use(self.addr, "ABCDEFGHIJ")
            self.assertTrue(CR.is_friend(A1, self.env), "可能已到 ⇒ 不撤:" + stage)
            CR.remove_friend(A1, self.env)
        CR.add_friend(A1, None, self.env)
        self.stage = {"rc": 2, "stage": "abort", "pos": None, "detail": "x", "msgid": "m"}
        self.use(self.addr, "ABCDEFGHIJ")
        self.assertTrue(CR.is_friend(A1, self.env), "本來就是好友 ⇒ 不撤")

    def test_i13_json_every_exit(self):
        def one(*args):
            rc, out = self.use(*(args + ("--json",)))
            lines = out.splitlines()
            self.assertEqual(len(lines), 1, out)
            d = json.loads(lines[0])
            self.assertEqual(d["v"], 1)
            self.assertTrue(d["hint"], d)
            return rc, d["ok"], d["why"]
        self.assertEqual(one("x", "AAAAAAAAAA"), (2, False, "bad"))
        self.assertEqual(one("abcde-fghij"), (2, False, "not_found"))
        self.fetches = [[self.body(), self.body(author=C1, at=2)]]
        self.assertEqual(one("abcde-fghij"), (2, False, "clash"))
        self.pub = []
        self.assertEqual(one(self.addr, "ABCDEFGHIJ"), (2, False, "no_seat"))
        self.pub, self.alive = [{"cabin": "6ddf", "seat": "cfd04", "addr": "x"}], False
        self.assertEqual(one(self.addr, "ABCDEFGHIJ"), (2, False, "bridge_off"))
        self.alive = True
        self.assertEqual(one(self.addr, "ABCDEFGHIJ"), (0, True, "joined"))
        self.stage = {"rc": 0, "stage": "held", "pos": None, "detail": None, "msgid": "m"}
        self.assertEqual(one(self.addr, "ABCDEFGHIJ"), (0, True, "held"))
        self.stage = {"rc": 0, "stage": "queued", "pos": None, "detail": None, "msgid": "m"}
        self.assertEqual(one(self.addr, "ABCDEFGHIJ"), (0, True, "request_pending"), "queued 跟 connect rc 4 同語意(r3 L1)")
        self.stage = {"rc": 2, "stage": "timeout-withdrawn", "pos": None, "detail": "x", "msgid": "m"}
        self.assertEqual(one(self.addr, "ABCDEFGHIJ"), (2, False, "timeout-withdrawn"))
        self.CB._run_nostr = lambda cmd, *a, **k: (1, {"ok": False, "why": "timeout"})
        self.assertEqual(one("abcde-fghij"), (2, False, "lookup_failed"), "每天都查不到中繼站 ≠ 碼不在(r3 L3)")
        self.CB._run_nostr = lambda *a, **k: (None, {})
        self.assertEqual(one("abcde-fghij"), (2, False, "no_venv"))

    def test_i14_clock_skew_days(self):
        import types
        midnight = int(time.time()) // 86400 * 86400                 # UTC 午夜前後 5 分,建立日期才會跨天
        for now, when, why in ((midnight - 300, midnight - 300 + 600, "對方時鐘快 10 分、建在明天"),
                               (midnight + 300, midnight + 300 - 86400 - 600, "建在前天、我的時鐘快 10 分")):
            self.CB.time = types.SimpleNamespace(time=lambda now=now: now)
            day = GF.invite_day(when)
            self.assertNotIn(day, (GF.invite_day(now), GF.invite_day(now - 86400)), "要測到今天 / 昨天以外的日期")
            self.by_hash = {GF.invite_keys("ABCDEFGHIJ", day)[0]: [self.body(day=day, exp=now + 600)]}
            self.assertEqual(self.CB._resolve_invite("ABCDEFGHIJ", self.env, io.StringIO()), (self.addr, None), why)


_rspec = importlib.util.spec_from_file_location("chat_recv_invite", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_rspec)
_rspec.loader.exec_module(RT)


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    setUp = RT.Network.setUp
    _box = RT.Network._box

    def test_e1_code_only(self):
        a, _x = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        b, _y = self._box("Bob", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True])
        ae = a.env()                                     # 產碼要本人 Touch ID(門在 CLI)⇒ 測試直接走產碼 + 放中繼站那段
        d, short = CB._publish_invite(CB.bridge_root(ae), ae, CB._my_published(ae)[0]["addr"])
        self.assertTrue(short, d)
        code = GF.invite_fmt(d["code"]).lower()
        r = b.cli("invite", "use", code)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Andy", r.stdout)
        self.assertTrue(CR.is_friend(a.owner, b.env()))
        self.assertTrue(a.wait_for(lambda: CR.is_friend(b.owner, a.env()), 30), "Andy 那邊沒自動加 Bob")
        h, key = GF.invite_keys(d["code"], GF.invite_day(d["created_at"]))
        revoked = lambda: any(e["kind"] == 30078 and ["d", "aib-invite:" + h] in [t[:2] for t in e["tags"]]  # noqa: E731
                              and (GF.invite_open(key, NK.npub(e["pubkey"]), json.loads(e["content"])) or {}).get("revoked")
                              for e in self.relay.events)
        self.assertTrue(a.wait_for(revoked, 20), "用掉的碼要在中繼站撤掉")
        r = b.cli("invite", "use", code)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Accept(unittest.TestCase):
    def probe(self, case):
        td = Path(tempfile.mkdtemp(prefix="aib-invite-p-"))
        self.addCleanup(shutil.rmtree, td, True)
        (td / "root").mkdir()
        env = dict(os.environ, HOME=str(td / "home"), EZ2_AIB_STATE_ROOT=str(td / "home" / ".ez2-aib"))
        r = subprocess.run([VPY, "-c", PROBE, str(COCKPIT), str(td / "root"), case], capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_i4_auto_accept(self):
        self.assertEqual(self.probe("ok"), {"sent": ["delivered"], "friends": [[True, "invite"]], "requests": 0,
                                            "left": False})
        for case in ("used", "junk"):
            got = self.probe(case)
            self.assertEqual((got["sent"], got["friends"], got["requests"]), (["held"], [], 1), case)
        self.assertEqual(self.probe("blocked")["friends"], [])
        got = self.probe("roster_err")
        self.assertTrue(got["left"], "加好友失敗 ⇒ 碼的次數補回")
        self.assertEqual((got["sent"], got["requests"]), (["held"], 1), "加好友失敗 ⇒ 落回一般申請(L-d)")


class Knife(unittest.TestCase):
    def _run(self, rel, anchor, repl, which, fails=None):
        name = Path(__file__).name
        with knife_copy(REPO, rel, anchor, repl,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), which], cwd=str(root), max_procs=60, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)
        for t in fails or [which.split(".")[-1]]:          # 紅的要是那一格(3.9 印 (__main__.類),新版印 (__main__.類.格))
            self.assertIn("FAIL: %s (" % t, r.stdout + r.stderr)

    def test_k1_no_decrement(self):
        self._run("src/cockpit/dm_global_files.py", '                r["uses"] = int(r["uses"]) - 1\n', "                pass\n",
                  "Store", fails=["test_i1_take_once"])

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_any_code(self):
        self._run("src/cockpit/chat_nostr.py", "and GF.take_invite(self.root, m.group(1))\n", 'and {"created_at": 0}\n', "Accept",
                  fails=["test_i4_auto_accept"])

    def test_k4_no_mac(self):
        self._run("src/cockpit/dm_global_files.py",
                  '    if not hmac.compare_digest(_invite_mac(key, author, nonce, ct), body["m"]):\n        return None\n',
                  "", "Short.test_i9_impostor")

    def test_k5_invite_ungated(self):
        self._run("src/cockpit/chat_bridge.py", '    rc = human_gate(__import__("approve_gate").reason("chat_bridge.invite"), env, out, a.json)\n',
                  "    rc = None\n", "Cli.test_i10_invite_needs_human")

    def test_k3_no_clash_check(self):
        self._run("src/cockpit/chat_bridge.py", '    if len({x.get("author") for x in hits}) > 1:\n', "    if False:\n",
                  "Short.test_i7_resolve")


    def test_k6_no_undo(self):
        self._run("src/cockpit/chat_bridge.py", '    if new and res["stage"] in GF.UNSENT_STAGES:\n', "    if False:\n",
                  "Use.test_i12_no_half_join")


if __name__ == "__main__":
    unittest.main()
