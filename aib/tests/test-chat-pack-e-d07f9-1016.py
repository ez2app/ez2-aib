#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球對談改善包 E(開發文件第 27.6 節):金鑰備份與還原。只用標準庫,不連網。

B1 備份:檔 0600、沒有明文私鑰與 nsec1;檔已在不蓋;密語太短 ⇒ rc 2;沒有終端機又沒 --passphrase-stdin ⇒ rc 2(要人來)
B2 還原到新機器:主人與各席 npub 一樣、私鑰一樣;好友與我的其他機器名單也回來
B3 錯的通關密語 ⇒ rc 2「通關密語不對」,新機器上一個金鑰檔都沒寫
B4 同一位主人:刪掉一把席金鑰後還原 ⇒ 補回(merge),其他不動
B5 不同主人:不加 --replace ⇒ rc 2、本機不動;加 --replace 但全球對談開著 ⇒ rc 2;關了再 --replace ⇒ 換成備份的主人
刀(複本):K1 解密不驗 Poly1305 標籤 ⇒ B3 紅;K2 主人不同也照寫 ⇒ B5 紅
"""
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_send_pe", str(HERE / "test-chat-send-d07f9-1016.py"))
SB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SB)
PASS = "correct horse battery"
FRIEND = NK.npub(NK.pubkey_hex("%064x" % 46))
MINE = NK.npub(NK.pubkey_hex("%064x" % 47))


def run(b, *args, stdin=None):
    return subprocess.run([sys.executable, str(SB.BRIDGE)] + list(args), capture_output=True, text=True,
                          env=b.env(), input=stdin, timeout=120)


def machine(seats=(("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席"),
                   ("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席"))):
    b = SB.Box()
    for i, (uuid, uid, nm) in enumerate(seats):
        b.seat("20261016-000000-6ddf", "w%d" % (i + 2), uuid, uid, nm)
    assert run(b, "prepare", "--keys-only", "--quiet").returncode == 0
    return b


def secrets(b):
    m = NK.read_map(b.env())
    out = {"owner": NK.load_secret("owner", b.env())}
    out.update({e["key"]: NK.load_secret(e["key"], b.env()) for e in m["seats"]})
    return m, out


class Backup(unittest.TestCase):
    def setUp(self):
        self.a = machine()
        self.addCleanup(self.a.close)
        run(self.a, "friend", "add", FRIEND, "--name", "Bob")
        run(self.a, "mine", "add", MINE)
        self.file = self.a.td / "aib-chat.bak"

    def backup(self, b=None, pw=PASS):
        return run(b or self.a, "backup", str(self.file), "--passphrase-stdin", stdin=pw + "\n")

    def test_b1_backup_file(self):
        r = self.backup()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(oct(self.file.stat().st_mode & 0o777), "0o600")
        raw = self.file.read_text(encoding="ascii")
        _m, sec = secrets(self.a)
        self.assertNotIn("nsec1", raw)
        for s in sec.values():
            self.assertNotIn(s, raw, "備份檔不准有明文私鑰")
        self.assertEqual(self.backup().returncode, 2, "檔已在不准蓋")
        self.file.unlink()
        self.assertEqual(self.backup(pw="short").returncode, 2)
        r = run(self.a, "backup", str(self.file))            # 沒終端機、沒 --passphrase-stdin
        self.assertEqual(r.returncode, 2)
        self.assertIn("親手輸入", r.stdout)
        self.assertFalse(self.file.exists())

    def test_b2_restore_fresh(self):
        self.assertEqual(self.backup().returncode, 0)
        n = SB.Box()
        self.addCleanup(n.close)
        r = run(n, "restore", str(self.file), "--passphrase-stdin", stdin=PASS + "\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(secrets(n), secrets(self.a))
        self.assertEqual([f["owner"] for f in json.loads(run(n, "friends", "--json").stdout)["friends"]], [FRIEND])
        self.assertEqual([f["owner"] for f in json.loads(run(n, "mine", "--json").stdout)["mine"]], [MINE])

    def test_b3_wrong_passphrase(self):
        self.assertEqual(self.backup().returncode, 0)
        n = SB.Box()
        self.addCleanup(n.close)
        r = run(n, "restore", str(self.file), "--passphrase-stdin", stdin="wrong passphrase\n")
        self.assertEqual(r.returncode, 2)
        self.assertIn("通關密語不對", r.stdout)
        self.assertFalse(NK.keys_dir(n.env()).exists(), "錯密語不准寫任何金鑰檔")

    def test_b4_merge_same_owner(self):
        self.assertEqual(self.backup().returncode, 0)
        before = secrets(self.a)
        m = NK.read_map(self.a.env())
        victim = m["seats"][0]["key"]
        (NK.keys_dir(self.a.env()) / (victim + ".key")).unlink()
        r = run(self.a, "restore", str(self.file), "--passphrase-stdin", stdin=PASS + "\n")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("同一位主人", r.stdout)
        self.assertEqual(secrets(self.a), before)

    def test_b5_other_owner(self):
        self.assertEqual(self.backup().returncode, 0)
        o = machine(seats=(("00000000-0000-0000-0000-00000000ccc3", "zzzz33", "別台"),))
        self.addCleanup(o.close)
        mine_before = secrets(o)
        r = run(o, "restore", str(self.file), "--passphrase-stdin", stdin=PASS + "\n")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--replace", r.stdout)
        self.assertEqual(secrets(o), mine_before, "主人不同不准動本機")
        self.assertEqual(run(o, "auto").returncode, 0)                  # 開關打開
        r = run(o, "restore", str(self.file), "--replace", "--passphrase-stdin", stdin=PASS + "\n")
        self.assertEqual(r.returncode, 2)
        self.assertIn("aib chat off", r.stdout)
        run(o, "off")
        r = run(o, "restore", str(self.file), "--replace", "--passphrase-stdin", stdin=PASS + "\n")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(NK.read_map(o.env())["owner"]["npub"], NK.read_map(self.a.env())["owner"]["npub"])


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement, tests=(name, "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_no_tag_check(self):
        self._knife("src/bootstrap/secret_crypto.py", "    if not hmac.compare_digest(expected, tag):\n        raise CryptoError(\"decrypt_failed\")\n    return _chacha_xor",
                    "    return _chacha_xor", "Backup.test_b3_wrong_passphrase")

    def test_k2_overwrite_other_owner(self):
        self._knife("src/cockpit/chat_backup.py", '        if not replace:\n            raise BackupError("owner_differs")\n', "",
                    "Backup.test_b5_other_owner")


if __name__ == "__main__":
    unittest.main()
