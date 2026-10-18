#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""主人金鑰加密 + 配對碼 + 改清單(開發文件第 37.7 節 K2 第一包;設計 v0.3 第 14 節,@abb89 10-18 11:29 / 11:32 准)。

O1 加解:同一組密語解得回、錯密語 ⇒ bad_passphrase、改一個 byte ⇒ bad_passphrase、不到 12 字元 ⇒ short_passphrase
O2 protect:寫 owner.sealed(0600)、刪 owner.key、用密語解得回同一把主人金鑰;
   本機 chat_backup 用同一組密語解得開 ⇒ same_as_backup、owner.key 不動;已加密再跑 ⇒ sealed
O3 讀密語:不是終端 ⇒ no_tty;假終端(pty)⇒ 讀得到、回顯關掉(主控端看不到密語)
O4 配對碼:6 組 4 碼、大小寫 / 空白 / - 不計較;長度或字元不對 ⇒ bad_code
O5 配對請求:簽章者指紋對不上配對碼的(別人灌的假請求)一律不理、不算錯;本人打錯 nonce 才算,同一台第 3 次作廢(之後打對也不收);
   10 分鐘過期;同一個請求只收一次(used)
O6 改清單:加一台 seq+1、驗得過 chat_devices.parse_list;同一台兩次 ⇒ already_listed;超過 16 台 ⇒ too_many;
   用機器碼停用 ⇒ 移到 revoked;停用過的再加 ⇒ was_revoked;上次簽超過 7 天 ⇒ 要提醒延長
O7 備份:主人金鑰加密後 aib chat backup 照樣備得出來(加密的那份原樣放進去、加機器金鑰);還原到新家 ⇒ owner.sealed 回來、
   主人密語照樣解得開、沒有明文 owner.key
連 Nostr(要 AIB_TEST_NOSTR_VENV;兩台沙盒 + 本機假中繼站;本人證明與密語在行程內換成假的,不讀環境變數):
N1 A 持主人金鑰 ⇒ owner protect:清單(seq 1,A 自己標 host)與主人名冊發出、owner.key 刪掉;A 改用機器金鑰發名冊後
   lookup A 的席照樣查得到(host 的機器名冊取代舊主人名冊,不算撞名);再跑 aib chat on 主人 npub 不變(不准重產)
N2 B join A ⇒ 印配對碼;A devices add <碼> ⇒ 清單 seq 2 有 B;B 開線上對話用機器金鑰發名冊 ⇒ lookup <A>/<B 的艙>/<席> 查得到、device = B;
   沒加密就改清單 ⇒ devices_need_protect;本人證明沒過 ⇒ 什麼都沒改
N3 A devices revoke B ⇒ B 的席查不到;停用持主人金鑰那台 ⇒ is_host
N4 加密後 aib chat off、沒輸密語 ⇒ 一定提示「舊版好友還看得到」
刀(複本):K1 假請求也算錯(只比 device = 簽章者、不比指紋)⇒ O5 紅;K2 不比備份密語 ⇒ O2 紅;K3 不關回顯 ⇒ O3 紅;
   K4 加密後 ensure_owner 又重產主人金鑰(10-18 動工時抓到的洞)⇒ N1 紅
"""
import importlib.util
import json
import os
import pty
import sys
import tempfile
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
import chat_devices as DV  # noqa: E402
import chat_owner as O  # noqa: E402
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("roster_t", str(HERE / "test-chat-roster-d07f9-1015.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)

FAST = 100000                       # 測試用最低次數(真用 60 萬次)
PASS = "correct horse battery"
NOW = 1_800_000_000


def npub_of(i):
    return NK.npub(NK.pubkey_hex("%064x" % (0x5000 + i)))


OWNER = npub_of(0)


class Seal(unittest.TestCase):
    def test_o1_seal_roundtrip(self):
        sk = "%064x" % 0x77
        rec = O.seal(sk, PASS, NK.npub(NK.pubkey_hex(sk)), FAST)
        self.assertEqual(O.unseal(rec, PASS), sk)
        self.assertNotIn(sk, json.dumps(rec))
        with self.assertRaises(O.OwnerError) as cm:
            O.unseal(rec, PASS + "x")
        self.assertEqual(str(cm.exception), "bad_passphrase")
        bad = dict(rec, ct=rec["ct"][:-4] + ("AAAA" if rec["ct"][-4:] != "AAAA" else "BBBB"))
        with self.assertRaises(O.OwnerError):
            O.unseal(bad, PASS)
        with self.assertRaises(O.OwnerError) as cm:
            O.seal(sk, "short pass", "x", FAST)
        self.assertEqual(str(cm.exception), "short_passphrase")


class Protect(unittest.TestCase):
    def setUp(self):
        self.b = RT.Box()
        self.addCleanup(self.b.close)
        self.assertEqual(self.b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
        self.env = self.b.env()
        self.owner = NK.read_map(self.env)["owner"]["npub"]

    def test_o2_protect(self):
        import chat_backup as CB
        bk = self.b.td / "backup.json"
        CB.backup(bk, PASS, self.env)
        with self.assertRaises(O.OwnerError) as cm:
            O.protect(PASS, self.env, backups=[bk], iterations=FAST)
        self.assertEqual(str(cm.exception), "same_as_backup", "主人密語不准跟備份密語一樣")
        self.assertTrue((NK.keys_dir(self.env) / "owner.key").exists(), "拒了就不動原檔")
        self.assertFalse(O.is_sealed(self.env))
        before = NK.load_secret("owner", self.env)
        self.assertEqual(O.protect("another long phrase", self.env, backups=[bk], iterations=FAST), self.owner)
        self.assertFalse((NK.keys_dir(self.env) / "owner.key").exists(), "加密後包裝金鑰那份要刪")
        self.assertEqual(oct(O.sealed_path(self.env).stat().st_mode & 0o777), "0o600")
        self.assertEqual(O.open_owner("another long phrase", self.env), before)
        with self.assertRaises(O.OwnerError) as cm:
            O.protect("another long phrase", self.env, iterations=FAST)
        self.assertEqual(str(cm.exception), "sealed")


class Tty(unittest.TestCase):
    def test_o3_read_passphrase(self):
        with tempfile.NamedTemporaryFile() as f:
            with self.assertRaises(O.OwnerError) as cm:
                O.read_passphrase("密語:", tty_path=f.name)
            self.assertEqual(str(cm.exception), "no_tty")
        master, slave = pty.openpty()
        path = os.ttyname(slave)
        seen = []

        def typist():                                          # 看到提示才打字(跟真人一樣),之後收完主控端看到的東西
            import select
            end, typed = time.time() + 5, False
            while time.time() < end:
                if not select.select([master], [], [], 0.1)[0]:
                    continue
                seen.append(os.read(master, 1024))
                out = b"".join(seen)
                if not typed and "密語:".encode("utf-8") in out:
                    os.write(master, (PASS + "\n").encode("utf-8"))
                    typed = True
                elif typed and out.endswith(b"\n"):
                    return

        t = threading.Thread(target=typist)
        t.start()
        got = O.read_passphrase("密語:", tty_path=path)
        t.join(5)
        os.close(master)
        os.close(slave)
        self.assertEqual(got, PASS)
        self.assertNotIn(PASS.encode("utf-8"), b"".join(seen), "回顯要關")


class Pair(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-pair-")
        self.root = Path(self.td.name)
        self.dev = npub_of(1)
        self.nonce = O.new_nonce()
        self.code = O.pair_code(self.dev, self.nonce)

    def tearDown(self):
        self.td.cleanup()

    def req(self, signer=None, device=None, nonce=None, at=NOW):
        signer = signer or self.dev
        return signer, json.dumps(O.pair_request(OWNER, device or signer, nonce or self.nonce, "Mac mini", at=at))

    def test_o4_code(self):
        self.assertEqual(len(self.code.split("-")), 6)
        self.assertEqual(O.parse_code(self.code), (O.fingerprint(self.dev), self.nonce))
        self.assertEqual(O.parse_code(" " + self.code.upper().replace("-", " ") + " "), (O.fingerprint(self.dev), self.nonce))
        self.assertEqual(O.parse_code("配對碼:" + self.code), (O.fingerprint(self.dev), self.nonce), "連前綴一起貼也要認得")
        for bad in ("", self.code[:-1], self.code + "a", "zz" + self.code[2:], self.code[:-1] + "0"):
            with self.assertRaises(O.OwnerError, msg=bad):
                O.parse_code(bad)

    def test_o5_requests(self):
        spam = [self.req(signer=npub_of(10 + i), nonce=O.new_nonce(), at=NOW + 5) for i in range(5)]
        for _ in range(5):                                     # 灌假請求:不理、不算,真的配對碼不會被弄作廢
            with self.assertRaises(O.OwnerError) as cm:
                O.check_pair(self.root, OWNER, self.code, spam, NOW + 10)
            self.assertEqual(str(cm.exception), "no_request")
        typo = O.pair_code(self.dev, O.new_nonce())
        for i in range(2):
            with self.assertRaises(O.OwnerError) as cm:
                O.check_pair(self.root, OWNER, typo, spam + [self.req()], NOW + 10)
            self.assertEqual(str(cm.exception), "bad_code", "本人打錯 nonce 算一次")
        rec = O.check_pair(self.root, OWNER, self.code, spam + [self.req()], NOW + 10)
        self.assertEqual(rec["device"], self.dev, "錯 2 次後打對照收")
        with self.assertRaises(O.OwnerError) as cm:
            O.check_pair(self.root, OWNER, self.code, [self.req()], NOW + 11)
        self.assertEqual(str(cm.exception), "used", "同一個請求只收一次")
        dev2, n2 = npub_of(2), O.new_nonce()
        code2 = O.pair_code(dev2, n2)
        good2 = (dev2, json.dumps(O.pair_request(OWNER, dev2, n2, "Pro 13", at=NOW)))
        for i in range(3):
            with self.assertRaises(O.OwnerError) as cm:
                O.check_pair(self.root, OWNER, O.pair_code(dev2, O.new_nonce()), [good2], NOW + 10)
        self.assertEqual(str(cm.exception), "burned", "同一台錯第 3 次作廢")
        with self.assertRaises(O.OwnerError) as cm:
            O.check_pair(self.root, OWNER, code2, [good2], NOW + 10)
        self.assertEqual(str(cm.exception), "burned", "作廢之後打對也不收")
        dev3, n3 = npub_of(3), O.new_nonce()
        with self.assertRaises(O.OwnerError) as cm:
            O.check_pair(self.root, OWNER, O.pair_code(dev3, n3),
                         [(dev3, json.dumps(O.pair_request(OWNER, dev3, n3, "x", at=NOW)))], NOW + O.PAIR_TTL_S + 1)
        self.assertEqual(str(cm.exception), "expired")
        other_owner = (dev3, json.dumps(O.pair_request(npub_of(9), dev3, n3, "x", at=NOW)))
        with self.assertRaises(O.OwnerError) as cm:
            O.check_pair(self.root, OWNER, O.pair_code(dev3, n3), [other_owner], NOW + 1)
        self.assertEqual(str(cm.exception), "no_request", "給別位主人的請求不算")


class Lists(unittest.TestCase):
    def test_o6_next_list(self):
        td = tempfile.TemporaryDirectory(prefix="aib-ownlist-")
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        cur = O.read_own_list(root, OWNER)
        self.assertEqual(cur["seq"], 0)
        a = O.next_list(cur, NOW, add={"npub": npub_of(1), "name": "Mini"})
        self.assertEqual((a["seq"], a["valid_until"] - a["at"]), (1, 30 * 86400))
        self.assertEqual(DV.parse_list(json.dumps(a), OWNER, NOW)["devices"][0]["npub"], npub_of(1))
        with self.assertRaises(O.OwnerError) as cm:
            O.next_list(a, NOW, add={"npub": npub_of(1)})
        self.assertEqual(str(cm.exception), "already_listed")
        full = a
        for i in range(2, 17):
            full = O.next_list(full, NOW, add={"npub": npub_of(i)})
        with self.assertRaises(O.OwnerError) as cm:
            O.next_list(full, NOW, add={"npub": npub_of(17)})
        self.assertEqual(str(cm.exception), "too_many")
        r = O.next_list(a, NOW + 5, revoke=DV.device_code(npub_of(1)))
        self.assertEqual((r["seq"], r["devices"], [x["npub"] for x in r["revoked"]]), (2, [], [npub_of(1)]))
        DV.parse_list(json.dumps(r), OWNER, NOW + 5)
        with self.assertRaises(O.OwnerError) as cm:
            O.next_list(r, NOW, add={"npub": npub_of(1)})
        self.assertEqual(str(cm.exception), "was_revoked")
        with self.assertRaises(O.OwnerError) as cm:
            O.next_list(r, NOW, revoke="zzzz")
        self.assertEqual(str(cm.exception), "not_listed")
        O.save_own_list(root, r)
        self.assertFalse(O.extend_due(root, OWNER, NOW + 6 * 86400))
        self.assertTrue(O.extend_due(root, OWNER, NOW + 7 * 86400 + 5))


class Backup(unittest.TestCase):
    def test_o7_backup_sealed(self):
        import chat_backup as CB
        a = RT.Box()
        self.addCleanup(a.close)
        self.assertEqual(a.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
        env = a.env()
        owner = NK.read_map(env)["owner"]["npub"]
        secret = NK.load_secret("owner", env)
        NK.ensure_device(env)
        O.protect("owner phrase long", env, iterations=FAST)
        bk = a.td / "bk.json"
        CB.backup(bk, "backup phrase", env)
        self.assertNotIn(secret, bk.read_text())
        b = RT.Box()
        self.addCleanup(b.close)
        envb = b.env()
        r = CB.restore(bk, "backup phrase", envb)
        self.assertEqual((r["owner"], r["mode"]), (owner, "fresh"))
        self.assertTrue(O.is_sealed(envb))
        self.assertFalse((NK.keys_dir(envb) / "owner.key").exists(), "還原後也沒有明文主人金鑰")
        self.assertEqual(O.open_owner("owner phrase long", envb), secret)
        self.assertEqual(NK.load_secret("device", envb), NK.load_secret("device", env))


@unittest.skipUnless(RT.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    """兩台:A(持主人金鑰,艙 6ddf)、B(新機器,艙 2282)。指令在行程內跑 chat_bridge.main,本人證明 / 密語換成假的。"""

    def setUp(self):
        import io
        import chat_owner_cli as OC
        from aib_fake_relay import FakeRelay
        self.io, self.OC = io, OC
        self.relay = FakeRelay()
        self.relay.__enter__()
        self.addCleanup(self.relay.__exit__, None, None, None)
        self.a, self.b = RT.Box([self.relay.url]), RT.Box([self.relay.url])
        for x in (self.a, self.b):
            self.addCleanup(x.close)
            x.link_venv()
        self.sa = self.a.seat("20261015-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "aaaa11", "A席")
        self.sb = self.b.seat("20261015-000000-2282", "w2", "00000000-0000-0000-0000-00000000bbb2", "bbbb22", "B席")
        self.assertEqual(self.a.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
        self.a.cli("publish", self.sa)
        self.owner = NK.read_map(self.a.env())["owner"]["npub"]
        self.a.start_run()
        self.assertTrue(self.a.wait_for(lambda: self.a.status().get("state") == "published", 40), self.a.status())
        self.proof = None
        self.passes = []
        from unittest import mock
        loc = mock.patch.dict(os.environ, {"EZ2_AIB_LOCALE": "zh-TW"})
        loc.start()
        self.addCleanup(loc.stop)
        self.addCleanup(setattr, OC, "PROVE", OC.PROVE)
        self.addCleanup(setattr, OC, "READ_PASS", OC.READ_PASS)
        OC.PROVE = lambda reason, env, root=None: self.proof
        OC.READ_PASS = self._read

    def _read(self, prompt):
        if not self.passes:
            raise O.OwnerError("no_tty")
        return self.passes.pop(0)

    def run_cli(self, box, *argv):
        import chat_bridge as CBR
        buf = self.io.StringIO()
        rc = CBR.main(list(argv), env=box.env(), out=buf)
        return rc, buf.getvalue()

    def lookup(self, box, addr):
        r = box.cli("lookup", addr)
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])

    def protect(self):
        self.passes = [PASS, PASS]
        rc, txt = self.run_cli(self.a, "owner", "protect")
        self.assertEqual(rc, 0, txt)
        return txt

    def test_n1_protect(self):
        self.protect()
        env = self.a.env()
        self.assertTrue(O.is_sealed(env))
        self.assertFalse((NK.keys_dir(env) / "owner.key").exists())
        lst = O.read_own_list(self.a.root, self.owner)
        self.assertEqual((lst["seq"], lst["devices"][0].get("host")), (1, True))
        rc, rec = self.lookup(self.a, "%s/6ddf/%s" % (self.owner, self.sa))
        self.assertEqual(rc, 0, rec)
        import chat_bridge as CBR
        self.assertEqual(CBR._run_nostr("publish", self.a.root, env)[0], 0, "加密後名冊改用機器金鑰發")
        rc, rec = self.lookup(self.a, "%s/6ddf/%s" % (self.owner, self.sa))
        self.assertEqual((rc, rec.get("device")), (0, NK.read_map(env)["device"]["npub"]), rec)
        self.assertEqual(self.a.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
        self.assertEqual(NK.read_map(env)["owner"]["npub"], self.owner, "加密後再開一次不准換掉主人身分")
        self.assertFalse((NK.keys_dir(env) / "owner.key").exists(), "也不准重產一把明文主人金鑰")

    def _join_b(self):
        rc, txt = self.run_cli(self.b, "join", self.owner, "--name", "B 機")
        self.assertEqual(rc, 0, txt)
        return txt.split("\n")[0].split()[-1]

    def test_n2_join_and_add(self):
        self._join_and_add()

    def _join_and_add(self):
        code = self._join_b()
        rc, txt = self.run_cli(self.a, "devices", "add", code)
        self.assertEqual(rc, 2)
        self.assertIn("aib chat owner protect", txt, "沒加密不准改清單")
        self.protect()
        self.proof = "touchid_cancel"
        rc, txt = self.run_cli(self.a, "devices", "add", code)
        self.assertEqual(rc, 2, txt)
        self.assertEqual(O.read_own_list(self.a.root, self.owner)["seq"], 1, "本人證明沒過 ⇒ 什麼都沒改")
        self.proof = None
        self.passes = [PASS]
        rc, txt = self.run_cli(self.a, "devices", "add", code)
        self.assertEqual(rc, 0, txt)
        lst = O.read_own_list(self.a.root, self.owner)
        self.assertEqual((lst["seq"], lst["devices"][-1]["name"]), (2, "B 機"))
        self.assertEqual(self.b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
        self.b.cli("publish", self.sb)
        import chat_bridge as CBR
        CBR.publish_roster(self.b.root, self.b.env(), {}, int(time.time()))
        rc, rec = self.lookup(self.a, "%s/2282/%s" % (self.owner, self.sb))
        self.assertEqual((rc, rec.get("device")), (0, NK.read_map(self.b.env())["device"]["npub"]), rec)

    def test_n3_revoke(self):
        self._join_and_add()
        bdev = NK.read_map(self.b.env())["device"]["npub"]
        self.passes = [PASS]
        rc, txt = self.run_cli(self.a, "devices", "revoke", DV.device_code(bdev))
        self.assertEqual(rc, 0, txt)
        rc, rec = self.lookup(self.a, "%s/2282/%s" % (self.owner, self.sb))
        self.assertEqual((rc, rec.get("why")), (2, "no_seat"), rec)
        host = O.read_own_list(self.a.root, self.owner)["devices"][0]["npub"]
        rc, txt = self.run_cli(self.a, "devices", "revoke", DV.device_code(host))
        self.assertEqual(rc, 2)
        self.assertIn("不能停用", txt)

    def test_n4_off_warns(self):
        self.protect()
        self.passes = []
        rc, txt = self.run_cli(self.a, "off")
        self.assertIn("舊版好友還看得到", txt, "沒輸密語一定要提示,不能默默略過")


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_owner.py", needle, replacement,
                        tests=(name, "test-chat-roster-d07f9-1015.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=80, timeout=300,
                            env=dict(os.environ))
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_spam_counts(self):
        self._knife('        if rec.get("device") != signer or fingerprint(signer) != fp:\n',
                    '        if rec.get("device") != signer:\n', "Pair.test_o5_requests")

    def test_k3_echo_on(self):
        self._knife("        new[3] &= ~termios.ECHO\n", "        pass\n", "Tty.test_o3_read_passphrase")

    @unittest.skipUnless(RT.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(被砍的 Network 也會跳過)")
    def test_k4_owner_regenerated(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/nostr_keys.py",
                        '    if rec.get("owner") and ((keys_dir(env) / "owner.key").is_file() or (keys_dir(env) / "owner.sealed").is_file()\n'
                        '                             or rec["owner"].get("remote")):\n',
                        '    if rec.get("owner") and (keys_dir(env) / "owner.key").is_file():\n',
                        tests=(name, "test-chat-roster-d07f9-1015.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Network.test_n1_protect"], cwd=str(root),
                            max_procs=80, timeout=300, env=dict(os.environ))
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        if r.returncode == 0 and "skipped=" in (r.stdout + r.stderr):
            self.skipTest("被砍的那格跳過了,刀沒得驗")
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k2_backup_pass_allowed(self):
        self._knife("    check_not_backup_pass(passphrase, backups)\n", "", "Protect.test_o2_protect")


if __name__ == "__main__":
    unittest.main()
