#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""釘死授權的信任公鑰清單(10-16 the maintainer 裁)。

★ 改這份清單要 the maintainer 點頭。★
  信任公鑰決定誰簽的授權、擴充、付費外掛算數:多一把 = 多一個人能發授權;少一把 = 那把簽過的東西全部驗不過。
  所以 aib/src/entitlement/keys.py 的 TRUSTED_PUBLIC_KEYS 只要跟下面 EXPECTED 不是一字不差(多一把、少一把、
  改一個字、換順序)這支就紅。真的要換鑰匙:先取得 the maintainer 同意,再同一個 PR 改 keys.py 與這裡。

目前(10-16):
  主鑰 Mac mini M4(10-09 keygen)—— 平常 ssh 到 mini 簽發;開源擴充與 aib-plugins 付費外掛都是它簽的
  備援 MacBook Pro 16(10-16 keygen)—— mini 掛掉時用;MacBook Air 13 不另產鑰

P1 清單一字不差(含順序)
P2 每一把都是合法的 Ed25519 公鑰(ed25519: + 64 個小寫 hex、能解成曲線上的點)、沒有重複
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from entitlement import ed25519, keys  # noqa: E402

EXPECTED = (
    "ed25519:90e5afdcd8a5ed6fc71a151b329c17fb6a911c19c1c4e035221accee0c953be5",   # 主鑰:Mac mini M4
    "ed25519:cb973123600961450410fc8471d833126ffcc1d97843378ddd639934faf15a3e",   # 備援:MacBook Pro 16
)


class TestTrustedKeysPinned(unittest.TestCase):
    def test_P1_exact_list(self):
        self.assertEqual(tuple(keys.TRUSTED_PUBLIC_KEYS), EXPECTED,
                         "TRUSTED_PUBLIC_KEYS 變了:改信任公鑰要 the maintainer 點頭,並在同一個 PR 更新這支測試的 EXPECTED")

    def test_P2_well_formed(self):
        self.assertEqual(len(set(keys.TRUSTED_PUBLIC_KEYS)), len(keys.TRUSTED_PUBLIC_KEYS), "重複的公鑰")
        for line in keys.TRUSTED_PUBLIC_KEYS:
            self.assertRegex(line, r"^ed25519:[0-9a-f]{64}$")
            self.assertIsNotNone(ed25519._decompress(bytes.fromhex(line.split(":", 1)[1])), line)


if __name__ == "__main__":
    unittest.main()
