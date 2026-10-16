#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球對談改善包 B(開發文件第 27.4、27.5 節):拒收回條限量、誰被拒收過。

核心(一定跑):
R0 aib chat refused 讀 refused.json:驗得到的列名字國別、驗不到的不列;有主人的附「加他」指令;status 帶最近 7 天幾位
連 Nostr(要 AIB_TEST_NOSTR_VENV;本機假中繼站):
Q1 同一陌生人連寄 3 則 ⇒ 只回 1 張拒收(第 1 則 rc 2;第 2、3 則沒有回條 ⇒ rc 4);
   refused.json 一筆、count 3、名字是名冊上的、**不含內文**;加好友後再寄 ⇒ 收下,那筆照留
刀(複本):K1 不限量 ⇒ Q1 紅;K2 記內文 ⇒ Q1 紅
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

_spec = importlib.util.spec_from_file_location("chat_recv_pb", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)
SB = RT.SB
OWNER = NK.npub(NK.pubkey_hex("%064x" % 44))
SEATK = NK.npub(NK.pubkey_hex("%064x" % 45))
SECRET = "這是一則不准落檔的內文 q1"


class Core(unittest.TestCase):
    def test_r0_list(self):
        b = SB.Box()
        self.addCleanup(b.close)
        b.root.mkdir(parents=True, exist_ok=True)
        now = int(time.time())
        rows = [{"who": OWNER, "owner": True, "display_name": "Bob", "country": "JP", "count": 2, "reason": "not_friend",
                 "first_at": now - 60, "last_at": now - 30},
                {"who": SEATK, "owner": False, "count": 1, "reason": "unverified", "first_at": now - 9 * 86400,
                 "last_at": now - 9 * 86400}]
        (b.root / "refused.json").write_text(json.dumps({"v": 1, "refused": rows}), encoding="utf-8")
        out = b.cli("refused").stdout
        self.assertIn("Bob(JP)", out)
        self.assertIn("aib chat friend add " + OWNER, out)
        self.assertIn("名字驗不到", out)
        self.assertNotIn("aib chat friend add " + SEATK, out, "只有席 npub 的不給加好友指令(好友以主人為單位)")
        self.assertEqual(len(json.loads(b.cli("refused", "--json").stdout)["refused"]), 2)
        self.assertEqual(json.loads(b.cli("status", "--json").stdout)["refused_7d"], 1)
        self.assertIn("最近 7 天被拒收:1 位", b.cli("status").stdout)
        (b.root / "refused.json").unlink()
        self.assertIn("沒有人被拒收", b.cli("refused").stdout)


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    setUp = RT.Network.setUp
    _box = RT.Network._box
    items = RT.Network.items

    def test_q1_refuse_once_and_log(self):
        a, (x,) = self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")], [True])
        b, (y,) = self._box("Bob", [("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True])
        to = "%s/6ddf/%s" % (b.owner, y)
        res = [GF.send_and_wait(a.root, SB.outbox_rec(to, x, content=SECRET), wait=8) for _ in range(3)]
        self.assertEqual([(r["rc"], r["stage"]) for r in res], [(2, "refused"), (4, "relayed"), (4, "relayed")], res)
        rows = json.loads((b.root / "refused.json").read_text(encoding="utf-8"))["refused"]
        self.assertEqual(len(rows), 1, rows)
        r = rows[0]
        self.assertEqual((r["who"], r["count"], r["display_name"], r["reason"], r["owner"]), (a.owner, 3, "Andy", "not_friend", True))
        self.assertNotIn(SECRET, (b.root / "refused.json").read_text(encoding="utf-8"), "拒收紀錄不准存內文")
        self.assertEqual(oct((b.root / "refused.json").stat().st_mode & 0o777), "0o600")
        self.assertEqual(b.cli("friend", "add", a.owner).returncode, 0)
        ok = GF.send_and_wait(a.root, SB.outbox_rec(to, x), wait=20)
        self.assertEqual((ok["rc"], ok["stage"]), (0, "queued"), ok)
        self.assertEqual(len(json.loads((b.root / "refused.json").read_text(encoding="utf-8"))["refused"]), 1)


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k1_no_rate_limit(self):
        self._knife("src/cockpit/chat_nostr.py", "            if last and time.time() - last < REFUSE_EVERY_S:",
                    "            if False:", "Network.test_q1_refuse_once_and_log")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_log_content(self):
        self._knife("src/cockpit/chat_nostr.py", '               owner=bool((parsed or {}).get("owner")))',
                    '               owner=bool((parsed or {}).get("owner")), content=rec.get("content"))',
                    "Network.test_q1_refuse_once_and_log")


if __name__ == "__main__":
    unittest.main()
