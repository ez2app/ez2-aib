#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球對談:問一次 + 好友制(維護者 10-16 12:1x 裁示好友制與已讀字樣;14:3x 改裁「預設開不問」⇒「問一次」)。

核心(一定跑,只用標準庫):
A1 沒有終端機(安裝程式、背景)⇒ 不問、不上線、不記
A2 終端機答 y ⇒ 寫開關檔(asked);不設名字;答過就不再問
A3 答不要(直接 Enter)⇒ 留 off-by-owner.json(同 aib chat off),之後不再問也不打開;aib chat on 拿掉記號
A4 逾時沒答 ⇒ 不上線、不記;AIB_CHAT_AUTO=0 或 AIB_CHAT_PREINSTALL=0 ⇒ 不問
A5 cockpit-full.sh 在「有開才開 sys.chat 窗」之前跑 auto,而且輸出沒導走(問得到人)
F1 好友名單:主人 npub 或全球位址都收(位址取主人)、同人再加 = 改名、加自己 / 看不懂 ⇒ rc 2;檔案 0600;移除
F2 status --json 帶好友數與「主人關過」;拒收原因碼 ⇒ 寄件端一句話
連 Nostr(要 AIB_TEST_NOSTR_VENV;兩個沙盒 = 兩位主人,本機假中繼站):
N1 Bob 把 Andy 加為好友 ⇒ Andy 寄的進 Bob 佇列(queued,class friend);讀了 ⇒ Andy 收到「已讀」
N2 好友移除 ⇒ 之後寄的拒收(rc 2、原因「只收好友」),佇列沒多
刀(複本):K1 auto 不看 off 記號 ⇒ A3 紅;K2 好友比對永遠不中 ⇒ N1 紅;K3 陌生不拒收(退回待審)⇒ N2 紅
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

_spec = importlib.util.spec_from_file_location("chat_recv_f", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)
SB = RT.SB
OTHER = NK.npub(NK.pubkey_hex("%064x" % 41))


class Base(unittest.TestCase):
    def setUp(self):
        self.b = SB.Box()
        self.addCleanup(self.b.close)

    def cli(self, *args, **extra):
        return self.b.cli(*args, **extra)

    def switch(self):
        try:
            return json.loads((self.b.chat / "enabled.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def run_tty(b, args, answer, timeout=20, **extra):
    """在假終端機(pty)跑 chat_bridge;看到 [y/N] 就打 answer(None = 不回答)。回 (rc, 輸出)。"""
    import pty
    import select
    import subprocess
    m, s_ = pty.openpty()
    p = subprocess.Popen([sys.executable, str(SB.BRIDGE)] + list(args), stdin=s_, stdout=s_, stderr=s_,
                         env=b.env(**extra), close_fds=True)
    os.close(s_)
    buf, sent, end = b"", False, time.time() + timeout
    while time.time() < end:
        r, _w, _x = select.select([m], [], [], 0.2)
        if r:
            try:
                chunk = os.read(m, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            if not sent and b"[y/N]" in buf and answer is not None:
                os.write(m, (answer + "\n").encode("utf-8"))
                sent = True
        elif p.poll() is not None:
            break
    try:
        p.wait(timeout=10)
    finally:
        os.close(m)
    return p.returncode, buf.decode("utf-8", "replace")


class Auto(Base):
    def test_a1_no_terminal_no_question(self):
        r = self.cli("auto")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIsNone(self.switch(), "沒有人能回答 ⇒ 一律不上線")
        self.assertFalse((self.b.chat / "off-by-owner.json").exists(), "沒問到人 ⇒ 不記,下次再問")
        self.assertNotIn("[y/N]", r.stdout)

    def test_a2_yes_once(self):
        rc, out = run_tty(self.b, ["auto"], "y")
        self.assertEqual(rc, 0, out)
        self.assertIn("[y/N]", out)
        sw = self.switch()
        self.assertTrue(sw and sw.get("on") and sw.get("asked"), sw)
        self.assertEqual(oct((self.b.chat / "enabled.json").stat().st_mode & 0o777), "0o600")
        self.assertFalse((self.b.chat / "profile.json").exists(), "不准替主人設名字(名字會公開)")
        rc, out = run_tty(self.b, ["auto"], "n")
        self.assertNotIn("[y/N]", out, "答過就不再問")

    def test_a3_no_is_remembered(self):
        rc, out = run_tty(self.b, ["auto"], "")                  # 直接 Enter = 不要
        self.assertEqual(rc, 0, out)
        self.assertIsNone(self.switch())
        self.assertTrue((self.b.chat / "off-by-owner.json").exists(), "答不要 = 跟 aib chat off 一樣記住")
        rc, out = run_tty(self.b, ["auto"], "y")
        self.assertNotIn("[y/N]", out)
        self.assertIsNone(self.switch(), "答過不要,開艙不准再打開")
        r = self.cli("on", "--no-venv", "--name", "Andy", "--country", "TW")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.b.chat / "off-by-owner.json").exists(), "aib chat on 要拿掉記號")
        self.cli("off")
        self.assertTrue((self.b.chat / "off-by-owner.json").exists())

    def test_a4_timeout_and_opt_out(self):
        rc, out = run_tty(self.b, ["auto"], None, AIB_CHAT_ASK_TIMEOUT="1")
        self.assertEqual(rc, 0, out)
        self.assertIsNone(self.switch())
        self.assertFalse((self.b.chat / "off-by-owner.json").exists(), "逾時 = 沒答 ⇒ 不記")
        for k in ("AIB_CHAT_AUTO", "AIB_CHAT_PREINSTALL"):
            rc, out = run_tty(self.b, ["auto"], "y", **{k: "0"})
            self.assertNotIn("[y/N]", out, k)
            self.assertIsNone(self.switch(), k)

    def test_a5_cockpit_asks_before_window(self):
        cf = (COCKPIT / "cockpit-full.sh").read_text(encoding="utf-8")
        line = next(ln for ln in cf.splitlines() if 'chat_bridge.py" auto' in ln)
        import re
        self.assertIsNone(re.search(r"(^|[^2])>\s*/dev/null|(?<![|])[|](?![|])", line), "開艙問人時標準輸出不能導走:" + line)
        self.assertGreater(cf.find('chat_bridge.py" enabled'), cf.find('chat_bridge.py" auto'))


class Friends(Base):
    def setUp(self):
        super().setUp()
        self.b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")
        self.assertEqual(self.cli("prepare", "--keys-only", "--quiet").returncode, 0)
        self.me = NK.read_map(self.b.env())["owner"]["npub"]

    def friends(self):
        return json.loads(self.cli("friends", "--json").stdout)["friends"]

    def test_f1_add_remove(self):
        self.assertIn("還沒有好友", self.cli("friends").stdout)
        r = self.cli("friend", "add", OTHER, "--name", "Bob")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([(f["owner"], f["name"]) for f in self.friends()], [(OTHER, "Bob")])
        self.assertEqual(oct((self.b.chat / "friends.json").stat().st_mode & 0o777), "0o600")
        r = self.cli("friend", "add", "%s/931e/bbbb2" % OTHER, "--name", "Bobby")      # 位址 ⇒ 取主人;同人 = 改名
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual([(f["owner"], f["name"]) for f in self.friends()], [(OTHER, "Bobby")])
        for bad in (self.me, "npub1xyz", "hello", "%s/931e" % OTHER):
            r = self.cli("friend", "add", bad)
            self.assertEqual(r.returncode, 2, bad)
        self.assertEqual(len(self.friends()), 1)
        self.assertEqual(self.cli("friend", "remove", OTHER).returncode, 0)
        self.assertEqual(self.friends(), [])
        self.assertIn("沒有", self.cli("friend", "remove", OTHER).stdout)

    def test_f2_status_and_refuse_text(self):
        self.cli("friend", "add", OTHER)
        self.cli("off")
        rec = json.loads(self.cli("status", "--json").stdout)
        self.assertEqual((rec["friends"], rec["off_by_owner"]), (1, True))
        self.assertIn("好友:1 位", self.cli("status").stdout)
        rc, st, _pos, detail = GF.outcome([{"stage": "refused", "detail": "not_friend"}])
        self.assertEqual((rc, st), (2, "refused"))
        self.assertIn("好友", detail)
        self.assertIn("名冊", GF.outcome([{"stage": "refused", "detail": "unverified"}])[3])
        self.assertEqual(GF.outcome([{"stage": "refused", "detail": "其他"}])[3], "其他")


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    box_extra = {"AIB_CHAT_READ_BATCH": "2"}
    setUp = RT.Network.setUp
    _box = RT.Network._box
    items = RT.Network.items
    final = RT.Network.final

    def pair(self):
        a, (x,) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        b, (y,) = self._box("Bob", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True])
        return a, x, b, y

    def test_n1_friend_accepted_and_read(self):
        a, x, b, y = self.pair()
        r = b.cli("friend", "add", "%s/6ddf/%s" % (a.owner, x), "--name", "Andy")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rec = SB.outbox_rec("%s/6ddf/%s" % (b.owner, y), x)
        res = GF.send_and_wait(a.root, rec, wait=20)
        self.assertEqual((res["rc"], res["stage"]), (0, "queued"), res)
        it = self.items(b, y)[0]
        self.assertEqual((it["status"], it["global"]["verified"]), ("queued", True))
        for to, who in (("claimed", "system"), ("delivered", "system"), ("read", "seat:" + y)):
            self.assertTrue(self.DQ._transition(y, it["id"], to, who, env=b.qenv)["ok"], to)
        self.assertTrue(self.final(a, rec["msgid"], "read", 30), GF.read_receipts(a.root, rec["msgid"]))

    def test_n2_unfriended_refused(self):
        a, x, b, y = self.pair()
        b.cli("friend", "add", a.owner)
        self.assertEqual(GF.send_and_wait(a.root, SB.outbox_rec("%s/6ddf/%s" % (b.owner, y), x), wait=20)["rc"], 0)
        b.cli("friend", "remove", a.owner)
        res = GF.send_and_wait(a.root, SB.outbox_rec("%s/6ddf/%s" % (b.owner, y), x), wait=20)
        self.assertEqual((res["rc"], res["stage"]), (2, "refused"), res)
        self.assertIn("好友", res["detail"])
        time.sleep(2)
        self.assertEqual(len(self.items(b, y)), 1, "移出好友後寄的不准進佇列")


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_auto_ignores_off(self):
        self._knife("src/cockpit/chat_bridge.py", "    if is_enabled(env) or off_marker(env).exists():",
                    "    if is_enabled(env):", "Auto.test_a3_no_is_remembered")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_friend_never_matches(self):
        self._knife("src/cockpit/chat_roster.py", '    return bool(owner) and any(f["owner"] == owner for f in read_friends(env, kind))',
                    "    return False", "Network.test_n1_friend_accepted_and_read")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k3_stranger_not_refused(self):
        self._knife("src/cockpit/chat_nostr.py", '        if rec["class"] == "stranger":\n            # 維護者',
                    '        if False:\n            # 維護者', "Network.test_n2_unfriended_refused")


if __name__ == "__main__":
    unittest.main()
