#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球對談席金鑰與主人金鑰(開發文件第 5.3、5.4 節;22.1 PR-6 = 第 1 期 1.2)。

全部在沙盒 HOME(EZ2_AIB_SECRETS_DIR、AIB_NOSTR_MAP 指到暫存目錄)。
V1 數學:BIP-340 測試向量(私鑰 3、向量 1)的 x-only 公鑰;NIP-19 的 npub / nsec 向量;bech32 錯一碼被擋
V2 落檔:資料夾 0700、檔案 0600;內容是 enc.v1. 信封、沒有私鑰 hex;load_secret 解得回、公鑰對得上對照表的 npub;
   換檔名(AAD)解不開;已有的金鑰檔不覆蓋
V3 主人金鑰:產一次;再叫一次 npub 不變;owner.key 在但對照表沒有 ⇒ 不重產、報錯
V4 跟著人格走:同人格換席(舊席不在活席清單)⇒ 主金鑰交給新席、npub 不變、不多產一把
V5 同人格兩個活席:先綁的留主金鑰、後來的另發 <uid>-<seat5>.key;後來那席下線 ⇒ 那把封存(搬 retired/、標 retired),主金鑰不動
V6 人格暫時沒有活席:主金鑰不封存;retire_persona ⇒ 主金鑰與第二把都封存、檔還在 retired/
V7 不收壞輸入:persona_uid / seat5 不合形的活席略過,檔名拼不出 ../
L1 洩漏:整個沙盒(金鑰檔以外)與對照表 grep 不到 nsec1、私鑰 hex
刀(複本):K1 落檔改寫明文 ⇒ V2 紅;K2 同人格兩活席共用主金鑰 ⇒ V5 紅
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import nostr_keys as K  # noqa: E402

UID_A = "aaaa11"
UID_B = "bbbb22"


class Box(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-nkeys-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.env = {"HOME": str(self.td / "home"), "EZ2_AIB_SECRETS_DIR": str(self.td / "home" / ".ez2-aib" / "secrets"),
                    "AIB_NOSTR_MAP": str(self.td / "home" / ".ez2-aib" / "identity" / "nostr-map.json")}

    def keyfile(self, stem):
        return K.keys_dir(self.env) / (stem + ".key")

    def entries(self):
        return K.read_map(self.env)["seats"]

    def active(self, uid=None):
        return [e for e in self.entries() if e["status"] == "active" and (uid is None or e["persona_uid"] == uid)]


class Math(unittest.TestCase):
    def test_v1_vectors(self):
        self.assertEqual(K.pubkey_hex("%064x" % 3).upper(),
                         "F9308A019258C31049344F85F89D5229B531C845836F99B08601F113BCE036F9")
        self.assertEqual(K.pubkey_hex("B7E151628AED2A6ABF7158809CF4F3C762E7160F38B4DA56A784D9045190CFEF").upper(),
                         "DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659")
        self.assertEqual(K.npub("3bf0c63fcb93463407af97a5e5ee64fa883d107ef9e558472c4eb9aaaefa459d"),
                         "npub180cvv07tjdrrgpa0j7j7tmnyl2yr6yr7l8j4s3evf6u64th6gkwsyjh6w6")
        self.assertEqual(K.bech32_encode("nsec", bytes.fromhex("67dea2ed018072d675f5415ecfaed7d2597555e202d85b3d65ea4e58d2d92ffa")),
                         "nsec1vl029mgpspedva04g90vltkh6fvh240zqtv9k0t9af8935ke9laqsnlfe5")
        good = "npub180cvv07tjdrrgpa0j7j7tmnyl2yr6yr7l8j4s3evf6u64th6gkwsyjh6w6"
        self.assertEqual(K.npub_to_hex(good), "3bf0c63fcb93463407af97a5e5ee64fa883d107ef9e558472c4eb9aaaefa459d")
        bad = good[:-1] + ("q" if good[-1] != "q" else "p")
        with self.assertRaises(ValueError):
            K.npub_to_hex(bad)
        for k in ("0" * 64, "%064x" % K.N):
            with self.assertRaises(ValueError):
                K.pubkey_hex(k)


class Store(Box):
    def test_v2_on_disk(self):
        acts = K.sync_seats([{"seat5": "abc12", "cabin": "6ddf", "persona_uid": UID_A}], self.env)
        self.assertEqual(acts, [("created", UID_A, "abc12")])
        f = self.keyfile(UID_A)
        self.assertEqual(oct(f.stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(K.keys_dir(self.env).stat().st_mode & 0o777), "0o700")
        raw = f.read_text()
        self.assertTrue(raw.startswith("enc.v1."), "金鑰檔要是加密信封")
        secret = K.load_secret(UID_A, self.env)
        self.assertRegex(secret, r"^[0-9a-f]{64}$")
        self.assertNotIn(secret, raw)
        self.assertEqual(K.npub(K.pubkey_hex(secret)), self.active(UID_A)[0]["npub"])
        shutil.copy2(f, self.keyfile("other9"))
        with self.assertRaises(Exception):
            K.load_secret("other9", self.env)                     # AAD 綁檔名
        before = f.read_bytes()
        with self.assertRaises(FileExistsError):
            K._write_key(UID_A, self.env)
        self.assertEqual(f.read_bytes(), before, "已有的金鑰檔不准被蓋")

    def test_v3_owner(self):
        n1 = K.ensure_owner(self.env)
        self.assertTrue(n1.startswith("npub1"))
        self.assertEqual(K.ensure_owner(self.env), n1)
        m = json.loads(Path(self.env["AIB_NOSTR_MAP"]).read_text())
        m["owner"] = None
        Path(self.env["AIB_NOSTR_MAP"]).write_text(json.dumps(m))
        with self.assertRaises(RuntimeError):
            K.ensure_owner(self.env)
        self.assertEqual(K.npub(K.pubkey_hex(K.load_secret("owner", self.env))), n1, "owner.key 不准被重產")


class Rules(Box):
    def test_v4_follows_persona(self):
        K.sync_seats([{"seat5": "aaaa1", "cabin": "6ddf", "persona_uid": UID_A}], self.env)
        first = self.active(UID_A)[0]["npub"]
        acts = K.sync_seats([{"seat5": "bbbb2", "cabin": "931e", "persona_uid": UID_A}], self.env)
        self.assertEqual(acts, [("moved", UID_A, "bbbb2", "aaaa1")])
        act = self.active(UID_A)
        self.assertEqual(len(act), 1)
        self.assertEqual((act[0]["seat5"], act[0]["cabin"], act[0]["npub"]), ("bbbb2", "931e", first))
        self.assertEqual(sorted(p.name for p in K.keys_dir(self.env).glob("*.key")), [UID_A + ".key"])

    def test_v5_two_live_seats(self):
        K.sync_seats([{"seat5": "aaaa1", "cabin": "6ddf", "persona_uid": UID_A}], self.env)
        main_npub = self.active(UID_A)[0]["npub"]
        live = [{"seat5": "aaaa1", "cabin": "6ddf", "persona_uid": UID_A}, {"seat5": "cccc3", "cabin": "6ddf", "persona_uid": UID_A}]
        acts = K.sync_seats(live, self.env)
        self.assertEqual(acts, [("created_extra", UID_A, "cccc3")])
        by_seat = {e["seat5"]: e for e in self.active(UID_A)}
        self.assertEqual(by_seat["aaaa1"]["npub"], main_npub)
        self.assertNotEqual(by_seat["cccc3"]["npub"], main_npub, "兩個活席不准共用一把")
        self.assertTrue(self.keyfile(UID_A + "-cccc3").is_file())
        self.assertEqual(K.sync_seats(live, self.env), [], "沒變化就不准再產")
        acts = K.sync_seats(live[:1], self.env)
        self.assertEqual(acts, [("retired", UID_A, "cccc3")])
        self.assertFalse(self.keyfile(UID_A + "-cccc3").exists())
        self.assertEqual(len(list((K.keys_dir(self.env) / "retired").glob(UID_A + "-cccc3.*.key"))), 1, "封存不刪")
        self.assertEqual([e["npub"] for e in self.active(UID_A)], [main_npub])

    def test_v6_no_live_then_retire(self):
        K.sync_seats([{"seat5": "aaaa1", "cabin": "6ddf", "persona_uid": UID_A},
                      {"seat5": "dddd4", "cabin": "6ddf", "persona_uid": UID_A}], self.env)
        self.assertEqual(K.sync_seats([], self.env), [("retired", UID_A, "dddd4")])
        self.assertEqual(len(self.active(UID_A)), 1, "人格暫時沒有活席,主金鑰不封存")
        self.assertTrue(self.keyfile(UID_A).is_file())
        self.assertEqual(K.retire_persona(UID_A, self.env), 1)
        self.assertEqual(self.active(UID_A), [])
        self.assertFalse(self.keyfile(UID_A).exists())
        self.assertEqual(len(list((K.keys_dir(self.env) / "retired").glob("*.key"))), 2)

    def test_v7_bad_input(self):
        acts = K.sync_seats([{"seat5": "../x1", "cabin": "6ddf", "persona_uid": UID_A},
                             {"seat5": "eeee5", "cabin": "6ddf", "persona_uid": "../../etc"},
                             {"seat5": "ffff6", "cabin": "6ddf", "persona_uid": UID_B}], self.env)
        self.assertEqual(acts, [("created", UID_B, "ffff6")])
        self.assertEqual(sorted(p.name for p in K.keys_dir(self.env).iterdir() if p.suffix == ".key"), [UID_B + ".key"])


class Leak(Box):
    def test_l1_no_secret_anywhere(self):
        K.ensure_owner(self.env)
        K.sync_seats([{"seat5": "aaaa1", "cabin": "6ddf", "persona_uid": UID_A},
                      {"seat5": "bbbb2", "cabin": "6ddf", "persona_uid": UID_A},
                      {"seat5": "cccc3", "cabin": "931e", "persona_uid": UID_B}], self.env)
        secrets = [K.load_secret(s, self.env) for s in ("owner", UID_A, UID_A + "-bbbb2", UID_B)]
        nsecs = [K.bech32_encode("nsec", bytes.fromhex(s)) for s in secrets]
        for p in Path(self.env["HOME"]).rglob("*"):
            if not p.is_file() or p.name == "wrap.key":
                continue
            text = p.read_bytes().decode("utf-8", "replace").lower()
            self.assertNotIn("nsec1", text, str(p))
            for s, ns in zip(secrets, nsecs):
                self.assertNotIn(s, text, "私鑰 hex 出現在 %s" % p)
                self.assertNotIn(ns, text, str(p))
        pv = json.dumps(K.public_view(self.env))
        for s in secrets:
            self.assertNotIn(s, pv)


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/nostr_keys.py", needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=180)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_plaintext_on_disk(self):
        self._knife('    env_blob = SC.encrypt_value(secret, "nostr/" + stem, str(secrets_dir(env)))\n',
                    "    env_blob = secret\n", "Store.test_v2_on_disk")

    def test_k2_share_main_key(self):
        self._knife('        elif main["seat5"] not in live_by_seat or live_by_seat[main["seat5"]]["persona_uid"] != pu:',
                    "        elif True:", "Rules.test_v5_two_live_seats")


if __name__ == "__main__":
    unittest.main()
