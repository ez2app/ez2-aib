#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""線上對話包 G(開發文件第 30 節):在線狀態分享 + 世界地圖資料(world.json)。

核心(一定跑):
G1 seat_presence:心跳新 + busy ⇒ online;心跳新 + 閒 ⇒ idle;3–30 分鐘 ⇒ away;更久 / 沒心跳 / 席不在 ⇒ asleep;
   with_status 才帶 now(清掉控制字元、80 字)與 step
G2 share:預設 online、我的其他機器預設 status、陌生 ⇒ off;share --json 把好友與我的機器的實際等級填好;壞等級 rc 2
G3 world --json:陌生 / 封鎖的格不列;40 分鐘沒更新 ⇒ fresh false、各席 unknown、不帶 now;名字角色從聯絡人快照補;帶 me
連 Nostr(要 AIB_TEST_NOSTR_VENV;本機假中繼站):
N1 互為好友 ⇒ B 的 world 有 A 的席(online、等級 online 不帶 now);A 改 status ⇒ B 看得到 📌 那一句;presence 不進佇列
N2 B 沒把 A 當好友 ⇒ A 送來的 presence 丟掉(world 空);A 對 B 等級 off ⇒ 不送
刀(複本):K1 收 presence 不看好友 ⇒ N2 紅;K2 不看等級一律帶 now ⇒ N1 紅
"""
import importlib.util
import json
import os
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
import seat_presence as SP  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_recv_pg", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)
SB = RT.SB
P = [NK.npub(NK.pubkey_hex("%064x" % (80 + i))) for i in range(6)]


def seat_now(home, code, beat_age, state="busy", item="寫世界地圖", done=2, total=5, now=None):
    now = int(time.time() if now is None else now)
    d = Path(home) / ".ez2-aib" / "state" / "seat-now"
    d.mkdir(parents=True, exist_ok=True)
    (d / (code + ".json")).write_text(json.dumps({"at": now, "code": code, "item": item, "state": state,
                                                   "steps_done": done, "steps_total": total}), encoding="utf-8")
    if beat_age is not None:
        (d / (code + ".beat")).write_text(json.dumps({"at": now - beat_age, "src": "statusline", "code": code}))


def js(r):
    return json.loads(r.stdout)


class Dict(unittest.TestCase):
    def test_g0_dicts_load(self):
        """四語字典要讀得起來(10-16 22:5x:一個空字串值讓整本字典讀不起來,所有訊息都變成鍵名)。"""
        import i18n_lib
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = i18n_lib.load_dict(lang, i18n_dir=str(REPO / "i18n"))
            self.assertIn("aib.chat.world_none", d, lang)


class Core(unittest.TestCase):
    def setUp(self):
        self.b = SB.Box()
        self.addCleanup(self.b.close)

    def test_g1_presence(self):
        root = str(self.b.home / ".ez2-aib")
        seat_now(self.b.home, "aaaa1", 10, "busy", item="做\x1b[31m事\n第二行")
        seat_now(self.b.home, "aaaa2", 10, "idle")
        seat_now(self.b.home, "aaaa3", 600)
        seat_now(self.b.home, "aaaa4", 4000)
        seat_now(self.b.home, "aaaa5", None)
        got = SP.presence(["aaaa1", "aaaa2", "aaaa3", "aaaa4", "aaaa5"], state_root=root)
        self.assertEqual([g["state"] for g in got], ["online", "idle", "away", "asleep", "asleep"])
        self.assertNotIn("now", got[0], "等級 online 不准帶狀態列")
        full = SP.presence(["aaaa1"], with_status=True, state_root=root)[0]
        self.assertEqual((full["step"], "\x1b" in (full["now"] or ""), "\n" in (full["now"] or "")), ("2/5", False, False), "控制字元與換行都要清掉")
        self.assertEqual(SP.presence(["aaaa1"], state_root=root, live={"zzzz9"})[0]["state"], "asleep")

    def test_g2_share(self):
        b = self.b
        b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")
        b.cli("prepare", "--keys-only", "--quiet")
        b.cli("friend", "add", P[0])
        b.cli("friend", "add", P[1])
        b.cli("mine", "add", P[2])
        pl = js(b.cli("share", "--json"))
        self.assertEqual((pl["default"], pl["peers"]), ("online", {P[2]: "status", P[0]: "online", P[1]: "online"}))
        # 10-21:改分享等級要本人 Touch ID(門在 CLI);行程內換掉門測輸出,子行程只讀
        import io
        import importlib.util as _ilu
        _sp = _ilu.spec_from_file_location("chat_bridge_g", str(REPO / "src" / "cockpit" / "chat_bridge.py"))
        CBg = _ilu.module_from_spec(_sp)
        _sp.loader.exec_module(CBg)
        CBg.HUMAN_GATE["prove"] = lambda reason, env: ("touchid", None)
        CBg.HUMAN_GATE["caller_is_seat"] = lambda env: False
        buf = io.StringIO()
        self.assertEqual(CBg.main(["share", P[1], "off"], env=b.env(), out=buf), 0, buf.getvalue())
        r_out = buf.getvalue()
        self.assertEqual(r_out.strip().count("\n"), 0, "改一位只回那一位:" + r_out)
        self.assertIn(P[1], r_out)
        self.assertNotIn("aib.chat.", r_out, "字典鍵沒翻到")
        self.assertEqual(CBg.main(["share", "--default", "status"], env=b.env(), out=io.StringIO()), 0)
        pl = js(b.cli("share", "--json"))
        self.assertEqual(pl["peers"], {P[2]: "status", P[0]: "status", P[1]: "off"})
        import chat_roster as CR
        self.assertEqual(CR.share_level(P[3], b.env()), "off", "陌生人什麼都不送")
        b.cli("block", "add", P[0])
        self.assertEqual(CR.share_level(P[0], b.env()), "off")
        self.assertEqual(b.cli("share", P[1], "everything").returncode, 2)
        self.assertEqual(b.cli("share", P[1]).returncode, 2)

    def test_g3_world_payload(self):
        b, root = self.b, self.b.root
        b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")
        self.assertEqual(b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
        b.cli("publish", "0aaa1")
        b.cli("friend", "add", P[0])
        b.cli("mine", "add", P[1])
        GF.ensure_dirs(root)
        now = int(time.time())
        GF.note_peer(root, P[0], "Bob", "JP", {"cabin": "931e", "seat": "bbbb2", "npub": P[4], "name": "收件席", "role": "dev"})
        GF.note_world(root, P[0], "931e", [{"seat": "bbbb2", "state": "online", "now": None, "step": None}], now=now)
        GF.note_world(root, P[1], "2282", [{"seat": "cccc3", "state": "idle", "now": "秘密工作", "step": "1/2"}],
                      now=now - 41 * 60)
        GF.note_world(root, P[3], "9999", [{"seat": "dddd4", "state": "online", "now": None, "step": None}], now=now)
        pl = js(b.cli("world", "--json"))
        cab = {c["owner"]: c for c in pl["cabins"]}
        self.assertNotIn(P[3], cab, "陌生人的格不列")
        bob = cab[P[0]]
        self.assertEqual((bob["relation"], bob["fresh"], bob["display_name"], bob["seats"][0]["name"], bob["seats"][0]["state"]),
                         ("friend", True, "Bob", "收件席", "online"))
        self.assertEqual(bob["seats"][0]["addr"], "%s/931e/bbbb2" % P[0])
        old = cab[P[1]]
        self.assertEqual((old["relation"], old["fresh"], old["seats"][0]["state"], old["seats"][0]["now"]),
                         ("mine", False, "unknown", None))
        self.assertEqual(pl["me"]["display_name"], "Andy")
        self.assertEqual([s["seat"] for s in pl["me"]["seats"]], ["0aaa1"])
        b.cli("block", "add", P[0])
        self.assertNotIn(P[0], {c["owner"] for c in js(b.cli("world", "--json"))["cabins"]})


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    box_extra = {"AIB_CHAT_PRESENCE_TICK": "1", "AIB_CHAT_PRESENCE_GAP": "1", "AIB_CHAT_PRESENCE_EVERY": "3"}
    setUp = RT.Network.setUp
    _box = RT.Network._box
    items = RT.Network.items

    def pair(self, b_friends_a=True):
        a, (x,) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        b, (y,) = self._box("Bob", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True])
        seat_now(a.home, x, 5, "busy", item="畫世界地圖")
        a.cli("friend", "add", "%s/6ddf/%s" % (b.owner, y))
        if b_friends_a:
            b.cli("friend", "add", a.owner)
        return a, x, b, y

    def world(self, b):
        return {c["owner"]: c for c in js(b.cli("world", "--json"))["cabins"]}

    def test_n1_friends_see_presence(self):
        a, x, b, y = self.pair()
        self.assertTrue(b.wait_for(lambda: a.owner in self.world(b), 40), GF.read_world(b.root))
        s = self.world(b)[a.owner]["seats"][0]
        self.assertEqual((s["seat"], s["state"], s["now"]), (x, "online", None), "等級 online 不准帶狀態列")
        import chat_roster as CR
        CR.set_share("status", b.owner, a.env())           # 10-21:改分享等級要本人 Touch ID,測試直接走函式
        self.assertTrue(b.wait_for(lambda: (self.world(b)[a.owner]["seats"][0].get("now") or "") == "畫世界地圖", 40),
                        self.world(b))
        self.assertEqual(self.items(b, y), [], "presence 不准進佇列")

    def test_n2_not_friend_dropped(self):
        a, x, b, y = self.pair(b_friends_a=False)
        time.sleep(8)
        self.assertEqual(GF.read_world(b.root), {}, "不是好友送來的 presence 要丟")
        import chat_roster as CR
        CR.set_share("off", b.owner, a.env())   # 先關再加好友:反過來的話,兩步之間 A 那一輪在線剛好送出就會被收(時序競態)
        b.cli("friend", "add", a.owner)
        time.sleep(8)
        self.assertEqual(GF.read_world(b.root), {}, "等級 off 不准送")


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k1_presence_from_anyone(self):
        self._knife("src/cockpit/chat_nostr.py",
                    "        if not owner or CR.is_blocked(owner) or not (CR.is_friend(owner) or CR.is_mine(owner)):",
                    "        if not owner:", "Network.test_n2_not_friend_dropped")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_status_always(self):
        self._knife("src/cockpit/chat_nostr.py", '            rows = SP.presence([e["seat5"] for e in mine_seats], with_status=(lvl == "status"))',
                    '            rows = SP.presence([e["seat5"] for e in mine_seats], with_status=True)',
                    "Network.test_n1_friends_see_presence")


if __name__ == "__main__":
    unittest.main()
