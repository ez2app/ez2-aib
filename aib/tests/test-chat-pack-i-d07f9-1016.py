#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""線上對話包 I(開發文件第 32 節,Andy 10-16 23:4x「真人驗證」「社群綁定」):宣告社群 / NIP-05,照證明自動驗。

核心(一定跑;證明頁用本機 http 假站,AIB_CHAT_VERIFY_ALLOW_LOCAL=1):
I1 aib chat social add|remove、nip05 <名@網域>|--clear:沒主人資料 ⇒ rc 2;平台 / 證明網址不合 ⇒ rc 2;
   NIP-05 進名冊;社群**不進公開名冊**(Andy 23:5x「社群只給好友看」);別人名冊裡不合格的 NIP-05 當沒有
I2 check_social:證明頁有 npub ⇒ True、沒有 ⇒ False、404 / 抓不到 ⇒ None;x 這類要登入的平台不抓 ⇒ None;非 https 不抓
   check_nip05:names[名] == 主人公鑰 ⇒ True、別人的鑰 ⇒ False、抓不到 ⇒ None
I3 aib chat contacts --json:verified(有驗過的名冊)、human、seats[].ai、nip05 與 socials 三態
I4 verify_round:只驗好友 / 我的機器 / 等我同意的申請,陌生人不抓;一天一次;每輪最多 5 項
連 Nostr(要 AIB_TEST_NOSTR_VENV;本機假中繼站):
N1 互為好友 ⇒ A 宣告的社群經名片(aib-type=card)私下送到 B,B 的聯絡人快照有;B 沒把 A 當好友 ⇒ 名片丟掉
刀(複本):K1 社群證明不比對 npub ⇒ I2 紅;K2 陌生人也抓 ⇒ I4 紅;K3 名片不看好友 ⇒ N1 紅
"""
import http.server
import importlib.util
import json
import os
import sys
import threading
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

_spec = importlib.util.spec_from_file_location("chat_send_pi", str(HERE / "test-chat-send-d07f9-1016.py"))
SB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SB)
OWN = NK.npub(NK.pubkey_hex("%064x" % 91))
OTHER = NK.npub(NK.pubkey_hex("%064x" % 92))
P = [NK.npub(NK.pubkey_hex("%064x" % (100 + i))) for i in range(10)]


class Site(object):
    """本機假證明站:/ok 有 OWN 的 npub、/bad 沒有、其他 404;/.well-known/nostr.json?name=andy ⇒ OWN 的公鑰。"""
    def __init__(self):
        hits = self.hits = []

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                hits.append(self.path)
                if self.path == "/ok":
                    body, code = "my aib owner: " + OWN, 200
                elif self.path == "/bad":
                    body, code = "nothing here", 200
                elif self.path.startswith("/.well-known/nostr.json?name=andy"):
                    body, code = json.dumps({"names": {"andy": NK.npub_to_hex(OWN)}}), 200
                else:
                    body, code = "no", 404
                self.send_response(code)
                self.end_headers()
                self.wfile.write(body.encode())
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class Base(unittest.TestCase):
    def setUp(self):
        self.site = Site()
        self.addCleanup(self.site.close)
        os.environ["AIB_CHAT_VERIFY_ALLOW_LOCAL"] = "1"
        os.environ["AIB_CHAT_NIP05_BASE"] = self.site.url
        self.addCleanup(os.environ.pop, "AIB_CHAT_VERIFY_ALLOW_LOCAL", None)
        self.addCleanup(os.environ.pop, "AIB_CHAT_NIP05_BASE", None)


class Declare(unittest.TestCase):
    def test_i1_declare(self):
        b = SB.Box()
        self.addCleanup(b.close)
        self.assertEqual(b.cli("social", "add", "github", "andy", "--proof", "https://gist.github.com/andy/1").returncode, 2)
        b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")
        self.assertEqual(b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
        self.assertEqual(b.cli("social", "add", "github", "andy", "--proof", "https://gist.github.com/andy/1").returncode, 0)
        self.assertEqual(b.cli("social", "add", "x", "@andy").returncode, 0)
        for bad in (["social", "add", "myspace", "andy"], ["social", "add", "web", "a", "--proof", "http://x.com"],
                    ["nip05", "Not An Address"], ["nip05"]):
            self.assertEqual(b.cli(*bad).returncode, 2, bad)
        self.assertEqual(b.cli("nip05", "Andy@EZ2.app").returncode, 0)
        got = json.loads(b.cli("social", "--json").stdout)
        self.assertEqual((got["nip05"], [x["p"] for x in got["socials"]]), ("andy@ez2.app", ["github", "x"]))
        b.cli("publish", "0aaa1")
        import chat_roster as CR
        content, _ = CR.build(b.env())
        self.assertEqual(content["nip05"], "andy@ez2.app")
        self.assertNotIn("socials", content, "社群只給好友看(Andy 23:5x)⇒ 不准放公開名冊")
        self.assertNotIn("andy", json.dumps(content).replace("andy@ez2.app", ""))
        content["nip05"] = "BAD"
        back = CR.parse(json.dumps(content), content["owner"])
        self.assertIsNone(back["nip05"], "別人名冊裡不合格的整欄當沒有")
        b.cli("social", "remove", "x")
        b.cli("nip05", "--clear")
        got = json.loads(b.cli("social", "--json").stdout)
        self.assertEqual((got["nip05"], [x["p"] for x in got["socials"]]), (None, ["github"]))


class Check(Base):
    def test_i2_checks(self):
        import chat_verify as CV
        u = self.site.url
        self.assertIs(CV.check_social({"p": "github", "v": "a", "proof": u + "/ok"}, OWN), True)
        self.assertIs(CV.check_social({"p": "github", "v": "a", "proof": u + "/bad"}, OWN), False)
        self.assertIs(CV.check_social({"p": "web", "v": "a", "proof": u + "/gone"}, OWN), None)
        n = len(self.site.hits)
        self.assertIs(CV.check_social({"p": "x", "v": "@a", "proof": u + "/ok"}, OWN), None)
        self.assertEqual(len(self.site.hits), n, "要登入的平台不抓")
        os.environ.pop("AIB_CHAT_VERIFY_ALLOW_LOCAL")
        self.assertIs(CV.check_social({"p": "web", "v": "a", "proof": u + "/ok"}, OWN), None, "非 https 不抓")
        os.environ["AIB_CHAT_VERIFY_ALLOW_LOCAL"] = "1"
        self.assertIs(CV.check_nip05("andy@ez2.app", OWN), True)
        self.assertIs(CV.check_nip05("andy@ez2.app", OTHER), False)
        self.assertIs(CV.check_nip05("bob@ez2.app", OWN), None)

    def test_i3_contacts_fields(self):
        b = SB.Box()
        self.addCleanup(b.close)
        b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")
        b.cli("prepare", "--keys-only", "--quiet")
        b.cli("friend", "add", OWN)
        b.cli("friend", "add", OTHER)
        GF.ensure_dirs(b.root)
        GF.note_peer(b.root, OWN, "Andy", "TW", {"cabin": "6ddf", "seat": "abcde", "npub": P[0], "name": "席", "role": "dev"},
                     roster_at=123, claims={"nip05": "andy@ez2.app", "socials": [{"p": "github", "v": "andy", "proof": "https://g/1"},
                                                                                   {"p": "x", "v": "@andy"}]})
        GF.note_check(b.root, OWN, "nip05", True)
        GF.note_check(b.root, OWN, "social:github:andy", False)
        GF.note_check(b.root, OWN, "social:x:@andy", None)
        rows = {c["owner"]: c for c in json.loads(b.cli("contacts", "--json").stdout)["contacts"]}
        a = rows[OWN]
        self.assertEqual((a["verified"], a["human"], a["seats"][0]["ai"]), (True, True, True))
        self.assertEqual((a["nip05"]["name"], a["nip05"]["verified"]), ("andy@ez2.app", True))
        self.assertEqual([(s["p"], s["verified"]) for s in a["socials"]], [("github", False), ("x", None)])
        self.assertEqual((rows[OTHER]["verified"], rows[OTHER]["nip05"], rows[OTHER]["socials"]), (False, None, []))

    def test_i4_verify_round(self):
        import chat_verify as CV
        b = SB.Box()
        self.addCleanup(b.close)
        b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")
        b.cli("prepare", "--keys-only", "--quiet")
        GF.ensure_dirs(b.root)
        u = self.site.url
        b.cli("friend", "add", OWN)
        GF.note_peer(b.root, OWN, claims={"nip05": "andy@ez2.app", "socials": [{"p": "web", "v": "w%d" % i, "proof": u + "/ok"}
                                                                             for i in range(6)]})
        GF.note_peer(b.root, OTHER, claims={"socials": [{"p": "web", "v": "s", "proof": u + "/ok"}]})   # 陌生人
        env = b.env()
        old = dict(os.environ)
        os.environ.update({k: v for k, v in env.items() if k in ("HOME", "AIB_CHAT_HOME", "AIB_CHAT_BRIDGE_DIR")})
        try:
            self.assertEqual(CV.verify_round(b.root), 5, "每輪最多 5 項")
            self.assertEqual(CV.verify_round(b.root), 2)
            self.assertEqual(CV.verify_round(b.root), 0, "一天一次")
        finally:
            os.environ.clear()
            os.environ.update(old)
        peers = GF.read_peers(b.root)
        self.assertEqual(len(peers[OWN]["checks"]), 7)
        self.assertTrue(peers[OWN]["checks"]["nip05"]["verified"])
        self.assertNotIn("checks", peers[OTHER], "陌生人不抓")


_rspec = importlib.util.spec_from_file_location("chat_recv_pi", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_rspec)
_rspec.loader.exec_module(RT)


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    box_extra = {"AIB_CHAT_PRESENCE_TICK": "1", "AIB_CHAT_PRESENCE_GAP": "1", "AIB_CHAT_PRESENCE_EVERY": "3"}
    setUp = RT.Network.setUp
    _box = RT.Network._box

    def test_n1_card_to_friends_only(self):
        a, (x,) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        b, (y,) = self._box("Bob", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True])
        c, (z,) = self._box("Cara", [("00000000-0000-0000-0000-00000000ccc3", "zzzz33", "收件席")], [True])
        self.assertEqual(a.cli("social", "add", "github", "andy", "--proof", "https://gist.github.com/andy/1").returncode, 0)
        a.cli("friend", "add", "%s/6ddf/%s" % (b.owner, y))
        a.cli("friend", "add", "%s/6ddf/%s" % (c.owner, z))
        b.cli("friend", "add", a.owner)                     # Cara 沒把 Andy 當好友
        self.assertTrue(b.wait_for(lambda: (GF.read_peers(b.root).get(a.owner) or {}).get("socials"), 40), GF.read_peers(b.root))
        self.assertEqual(GF.read_peers(b.root)[a.owner]["socials"][0]["v"], "andy")
        time.sleep(3)
        self.assertFalse((GF.read_peers(c.root).get(a.owner) or {}).get("socials"), "不是好友 ⇒ 名片要丟")


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement, tests=(name, "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_no_npub_match(self):
        self._knife("src/cockpit/chat_verify.py", "    return owner_npub in body\n", "    return True\n", "Check.test_i2_checks")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k3_card_from_anyone(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_nostr.py",
                        "        if not owner or CR.is_blocked(owner) or not (CR.is_friend(owner) or CR.is_mine(owner)):\n            return\n        rc, res = await self.sender._resolve(tags.get(\"aib-from\"))",
                        "        if not owner:\n            return\n        rc, res = await self.sender._resolve(tags.get(\"aib-from\"))",
                        tests=(name, "test-chat-send-d07f9-1016.py", "test-chat-recv-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Network.test_n1_card_to_friends_only"],
                            cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped)
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:\n" + r.stdout + r.stderr)

    def test_k2_check_strangers(self):
        self._knife("src/cockpit/chat_verify.py", "        if owner not in owners or CR.is_blocked(owner, env):",
                    "        if CR.is_blocked(owner, env):", "Check.test_i4_verify_round")


if __name__ == "__main__":
    unittest.main()
