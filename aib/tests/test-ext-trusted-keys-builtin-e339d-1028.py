#!/usr/bin/env python3
"""外掛信任鑰匙只認內建(10-28 @aeb40 TID-K)。

舊版 aib_ext.trusted_keys() 會讀 <狀態根>/trusted-ext-keys 加信任公鑰 ⇒ 同帳號 AI 寫一行自己的 ed25519 公鑰,
就能自簽外掛說明檔過 signed_ok(威脅模型:本機檔不能當信任底)。

K1 狀態根放 trusted-ext-keys(寫自己的公鑰)+ 用那把鑰簽說明檔 ⇒ signed_ok 不過、trusted_keys 不含它
K2 同一把鑰 mock 進內建清單 ⇒ 過(證明 K1 紅不是因為簽法錯)
K3 環境變數指 EZ2_AIB_HOME / EZ2_AIB_STATE_ROOT 都放鑰匙檔 ⇒ 一樣不過
刀:把讀檔那段放回 trusted_keys ⇒ K1 / K3 紅。
"""
import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
sys.path.insert(0, str(ROOT / "src"))

import aib_ext  # noqa: E402
from entitlement import ed25519, keys, signing  # noqa: E402

SEED = bytes(range(7, 39))
PUB = "ed25519:" + ed25519.public_key(SEED).hex()


def signed(m):
    m = dict(m)
    m["signature"] = "ed25519:" + base64.b64encode(ed25519.sign(SEED, signing.canonical(m))).decode()
    return m


MANIFEST = {"name": "probe", "kind": "plugin", "version": "0.0.1", "requires_aib": "0.1.0", "files": {}}


class Box(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-ext-keys-")
        self.addCleanup(td.cleanup)
        self.state = Path(td.name) / "home" / ".ez2-aib"
        self.state.mkdir(parents=True)
        p = mock.patch.dict(os.environ, {"HOME": str(self.state.parent), "EZ2_AIB_STATE_ROOT": str(self.state),
                                         "EZ2_AIB_HOME": str(self.state), "EZ2_AIB_LOCALE": "zh-TW"})
        p.start()
        self.addCleanup(p.stop)

    def test_k1_local_key_file_not_trusted(self):
        (self.state / "trusted-ext-keys").write_text(PUB + "\n", encoding="utf-8")
        self.assertNotIn(PUB, aib_ext.trusted_keys())
        self.assertFalse(aib_ext.signed_ok(signed(MANIFEST)), "本機 trusted-ext-keys 不能讓自簽外掛過")

    def test_k2_builtin_key_trusted(self):
        with mock.patch.object(keys, "TRUSTED_PUBLIC_KEYS", (PUB,)):
            self.assertIn(PUB, aib_ext.trusted_keys())
            self.assertTrue(aib_ext.signed_ok(signed(MANIFEST)))
            bad = signed(MANIFEST)
            bad["version"] = "0.0.2"
            self.assertFalse(aib_ext.signed_ok(bad), "簽完改欄位要不過")

    def test_k3_other_roots_not_read(self):
        other = self.state.parent / "other-root"
        other.mkdir()
        for d in (self.state, other):
            (d / "trusted-ext-keys").write_text(PUB + "\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": str(other), "EZ2_AIB_HOME": str(other)}):
            self.assertNotIn(PUB, aib_ext.trusted_keys())
            self.assertFalse(aib_ext.signed_ok(signed(MANIFEST)))


if __name__ == "__main__":
    unittest.main()
