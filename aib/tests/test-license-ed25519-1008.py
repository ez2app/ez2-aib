#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""授權改用真的公鑰簽章(10-08 the maintainer 同意):草稿簽章(任何非空字串都過)與 EZ2_LICENSE(完全不驗)都換成 Ed25519。

L1 ed25519 對得上 RFC 8032 的三組官方測試向量;改一個位元就不過
L2 license.json:信任公鑰簽的 ⇒ ok、tier 照檔;改任一欄 ⇒ invalid → free;別把鑰匙簽的 ⇒ invalid → free
L3 過渡開關:舊草稿的任意字串簽章,DRAFT_ACCEPT_ANY=True 照舊 ok、False 就 invalid;真簽章對不上兩種都 invalid
L4 EZ2_LICENSE(三段式 EdDSA):簽過的 advanced ⇒ 進階;改 payload ⇒ 不認;舊的沒簽章授權只在過渡期認
L5 簽發工具:keygen 的私鑰 0600、資料夾 0700、不印私鑰、不覆寫既有鑰匙;sign-entitlement / sign-plan 簽出來的驗得過
L6 enforced():過渡中或沒有信任公鑰 ⇒ False(aib_plugins.license_check_enforced 跟著照實報)
刀(複本):K1 真簽章不驗內容一律 ok ⇒ L2 紅
"""
import base64
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "bootstrap"))
sys.path.insert(0, str(ROOT / "tests"))
from entitlement import ed25519, keys, signing, tier  # noqa: E402
import license_plan  # noqa: E402

SEED = bytes(range(32))
OTHER = bytes(range(1, 33))
PUB = signing.PREFIX + ed25519.public_key(SEED).hex()
VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


def _signed(obj, seed=SEED):
    obj = dict(obj)
    obj.pop("signature", None)
    obj["signature"] = signing.PREFIX + base64.b64encode(ed25519.sign(seed, signing.canonical(obj))).decode("ascii")
    return obj


def _b64url(b):
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _jws(payload, seed=SEED):
    h = _b64url(b'{"alg":"EdDSA","typ":"JWT"}')
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    return "%s.%s.%s" % (h, p, _b64url(ed25519.sign(seed, ("%s.%s" % (h, p)).encode())))


class Keys(unittest.TestCase):
    """每格自己設信任公鑰與過渡開關,結束還原(不靠產品現在的設定)。"""

    def setUp(self):
        self._old = (keys.TRUSTED_PUBLIC_KEYS, keys.DRAFT_ACCEPT_ANY)
        keys.TRUSTED_PUBLIC_KEYS = (PUB,)
        keys.DRAFT_ACCEPT_ANY = False

    def tearDown(self):
        keys.TRUSTED_PUBLIC_KEYS, keys.DRAFT_ACCEPT_ANY = self._old


class Ed(unittest.TestCase):
    def test_l1_rfc8032_vectors(self):
        for sk, pk, m, sig in VECTORS:
            sk, pk, m, sig = map(bytes.fromhex, (sk, pk, m, sig))
            self.assertEqual(ed25519.public_key(sk), pk)
            self.assertEqual(ed25519.sign(sk, m), sig)
            self.assertTrue(ed25519.verify(pk, m, sig))
            bad = bytearray(sig)
            bad[3] ^= 1
            self.assertFalse(ed25519.verify(pk, m, bytes(bad)))
            self.assertFalse(ed25519.verify(pk, m + b"x", sig))
        self.assertFalse(ed25519.verify(b"short", b"", b"\0" * 64))


class Entitlement(Keys):
    def _tier(self, obj):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "license.json").write_text(json.dumps(obj), encoding="utf-8")
            return tier.get_entitlement({"EZ2_AIB_HOME": td})

    def test_l2_real_signature(self):
        lic = _signed({"tier": "all"})
        self.assertEqual(tier.verify_signature(lic), "ok")
        self.assertEqual(self._tier(lic)["tier"], "all")
        forged = dict(lic, tier="single", providers=["codex"])
        self.assertEqual(tier.verify_signature(forged), "invalid")
        self.assertEqual(self._tier(forged)["tier"], "free")
        other = _signed({"tier": "all"}, seed=OTHER)
        self.assertEqual(tier.verify_signature(other), "invalid")
        self.assertEqual(self._tier(other)["tier"], "free")

    def test_l3_draft_switch(self):
        draft = {"tier": "all", "signature": "anything-at-all"}
        self.assertEqual(tier.verify_signature(draft), "invalid")
        keys.DRAFT_ACCEPT_ANY = True
        self.assertEqual(tier.verify_signature(draft), "ok")
        self.assertEqual(tier.verify_signature(dict(_signed({"tier": "all"}), tier="single")), "invalid",
                         "過渡期也不准:真簽章對不上就是不過")
        self.assertEqual(tier.verify_signature({"tier": "all"}), "unavailable")


class Plan(Keys):
    def test_l4_ez2_license(self):
        good = _jws({"plan": "advanced"})
        self.assertEqual(license_plan.plan_from_blob(good), "advanced")
        h, _p, s = good.split(".")
        forged = "%s.%s.%s" % (h, _b64url(b'{"plan":"ultra"}'), s)
        self.assertIsNone(license_plan.plan_from_blob(forged))
        legacy = "x.%s.y" % _b64url(b'{"plan":"advanced"}')
        self.assertIsNone(license_plan.plan_from_blob(legacy))
        keys.DRAFT_ACCEPT_ANY = True
        self.assertEqual(license_plan.plan_from_blob(legacy), "advanced")
        self.assertIsNone(license_plan.plan_from_blob(forged), "過渡期也不准:EdDSA 簽章對不上就不認")

    def test_l6_enforced(self):
        self.assertTrue(signing.enforced())
        keys.DRAFT_ACCEPT_ANY = True
        self.assertFalse(signing.enforced())
        keys.DRAFT_ACCEPT_ANY = False
        keys.TRUSTED_PUBLIC_KEYS = ()
        self.assertFalse(signing.enforced())


class Tool(unittest.TestCase):
    # 10-09:簽發工具只在私有倉(公開版只帶驗證,export_aib 不帶 tools/license)⇒ 公開樹裡這格標「沒驗」,不是紅。私有倉照跑。
    @unittest.skipUnless((ROOT / "tools" / "license" / "aib-license.py").is_file(), "signer is private; the public tree only verifies")
    def test_l5_signer(self):
        tool = ROOT / "tools" / "license" / "aib-license.py"
        with tempfile.TemporaryDirectory() as td:
            key = Path(td) / "signing" / "license.key"
            env = dict(os.environ, AIB_LICENSE_KEY=str(key))
            run = lambda *a: subprocess.run([sys.executable, str(tool), *a], env=env, capture_output=True, text=True,
                                            stdin=subprocess.DEVNULL, timeout=60)
            p = run("keygen")
            self.assertEqual(p.returncode, 0, p.stderr)
            line = p.stdout.strip()
            self.assertRegex(line, r"^ed25519:[0-9a-f]{64}$")
            seed = key.read_bytes()
            self.assertEqual(len(seed), 32)
            self.assertNotIn(seed.hex(), p.stdout + p.stderr)
            self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(key.parent.stat().st_mode), 0o700)
            self.assertNotEqual(run("keygen").returncode, 0, "不准覆寫既有鑰匙")
            lic = Path(td) / "license.json"
            lic.write_text(json.dumps({"tier": "all"}), encoding="utf-8")
            self.assertEqual(run("sign-entitlement", str(lic)).returncode, 0)
            plan = Path(td) / "plan.json"
            plan.write_text(json.dumps({"plan": "advanced"}), encoding="utf-8")
            blob = run("sign-plan", str(plan)).stdout.strip()
            old = (keys.TRUSTED_PUBLIC_KEYS, keys.DRAFT_ACCEPT_ANY)
            try:
                keys.TRUSTED_PUBLIC_KEYS, keys.DRAFT_ACCEPT_ANY = (line,), False
                self.assertEqual(signing.verify_object(json.loads(lic.read_text(encoding="utf-8"))), "ok")
                self.assertEqual(signing.verify_jws(blob), "ok")
            finally:
                keys.TRUSTED_PUBLIC_KEYS, keys.DRAFT_ACCEPT_ANY = old


class Knives(unittest.TestCase):
    def test_k1_no_content_check_reds_l2(self):
        from aib_knife_copy import knife_copy
        name = Path(__file__).name
        with knife_copy(ROOT, "src/entitlement/signing.py",
                        "        return OK if _any_key(canonical(obj), raw, key_list) else INVALID\n",
                        "        return OK\n", tests=(name,)) as root:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            p = subprocess.run([sys.executable, str(root / "tests" / name), "Entitlement.test_l2_real_signature"], cwd=str(root),
                               env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
        self.assertIn("test_l2_real_signature", set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M)), p.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
