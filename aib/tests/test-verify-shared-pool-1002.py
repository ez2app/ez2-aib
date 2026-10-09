#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""多台機器/多棵樹共用一個沿用池(docs/PLAN-shadow-cross-device-1002.md 第 2 步)。

以前只看「最新一列」,誰最後寫誰贏。共用池時兩個後果:別台(環境不同)剛寫的列蓋掉本機對得上的舊列;
兩台環境對齊、鑰匙相同時,快機的綠蓋掉慢機同鑰匙的紅 ⇒ 慢機上的紅被藏起來。
  S1 只有一列且對得上 ⇒ 照舊沿用,detail 是鑰匙前綴,row_for 取回那一列
  S2 別台的新列(環境不同)蓋在本機對得上的舊列上 ⇒ 仍沿用本機那一列(以前:env-fp-changed 必跑)
  S3 同一把鑰匙:舊列紅(慢機)、新列綠(快機)⇒ unstable-same-key 必跑(以前:沿用 ⇒ 紅被藏起來)
  S4 同一把鑰匙:舊列逾時、新列綠 ⇒ unstable-same-key
  S5 往回找到的舊列,同鑰匙在別處紅過 ⇒ 也不准沿用
  S6 最新一列紅(鑰匙和現在不同)⇒ 照舊 previous-red,不往回找(保守)
  S7 最新一列量不到/外部 ⇒ 照舊必跑,不往回找
  S8 row_for 找不到前綴 ⇒ 回最新一列
  S9 新理由在四語字典都有白話(aib test 畫面不會印出代碼)
刀:拿掉 decide 裡的往回找迴圈 ⇒ S2 紅;拿掉 _stable 的檢查 ⇒ S3、S4、S5 紅。
"""
import os
import shutil
import sys
import time
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "verify"))
import reuse_index as ri  # noqa: E402
import shadow_tracer as st  # noqa: E402

T = "tests/test-a.py"


class SharedPool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="shared-pool-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (Path(self.tmp) / "a.txt").write_text("one\n", encoding="utf-8")
        items, fp = st.recompute_input_fp(self.tmp, ["open:./a.txt:placeholder"])
        env = st.child_env(Path(self.tmp), Path(self.tmp) / "probe-trace")
        self.env_fp = st.env_fingerprint(env, repo=self.tmp)[0]
        self.items, self.fp = items, fp

    def row(self, host, env_fp=None, **kw):
        env_fp = env_fp or self.env_fp
        r = {"test": T, "rc": 0, "tail": "ok", "inputs": list(self.items), "input_fp": self.fp,
             "env_fp": env_fp, "key": st.make_key(T, self.fp, env_fp), "measured": True,
             "external": False, "why": [], "reused": False, "lock_fp": ri.lock_fp(self.tmp), "host": host,
             "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))}   # 10-04:沒有 UTC 時間的列一律必跑(row-age-unknown)
        r.update(kw)
        return r

    def idx(self, *rows):
        i = ri.Index()
        for r in rows:
            i.add_row(r)
        return i

    def test_S1_single_matching_row_is_reused(self):
        i = self.idx(self.row("mine"))
        verdict, reason, detail = i.decide(self.tmp, T)
        self.assertEqual((verdict, reason), ("reuse", "fingerprint-match"))
        self.assertEqual(i.row_for(T, detail)["host"], "mine")

    def test_S2_other_hosts_newer_row_does_not_hide_ours(self):
        i = self.idx(self.row("mine"), self.row("fish", env_fp="fish-env"))
        verdict, reason, detail = i.decide(self.tmp, T)
        self.assertEqual(verdict, "reuse", "別台的新列蓋掉本機對得上的舊列 ⇒ 白跑(reason=%s)" % reason)
        self.assertEqual(i.row_for(T, detail)["host"], "mine", "沿用的必須是鑰匙對得上的那一列")

    def test_S3_same_key_red_then_green_is_unstable(self):
        i = self.idx(self.row("slow", rc=1), self.row("fast"))
        self.assertEqual(i.decide(self.tmp, T)[:2], ("must-run", "unstable-same-key"),
                         "同條件慢機紅、快機綠,卻沿用綠 ⇒ 紅被藏起來")

    def test_S4_same_key_timeout_then_green_is_unstable(self):
        i = self.idx(self.row("slow", rc=124, timed_out=True), self.row("fast"))
        self.assertEqual(i.decide(self.tmp, T)[:2], ("must-run", "unstable-same-key"))

    def test_S5_fallback_row_with_red_twin_is_not_reused(self):
        i = self.idx(self.row("slow", rc=1), self.row("mine"), self.row("fish", env_fp="fish-env"))
        self.assertEqual(i.decide(self.tmp, T)[:2], ("must-run", "unstable-same-key"))

    def test_S6_latest_red_stays_previous_red(self):
        i = self.idx(self.row("mine"), self.row("fish", env_fp="fish-env", rc=1))
        self.assertEqual(i.decide(self.tmp, T)[:2], ("must-run", "previous-red"))

    def test_S7_latest_unmeasured_or_external_no_fallback(self):
        for kw, want in ((dict(measured="unmeasured"), "unmeasured"), (dict(external=True), "external")):
            with self.subTest(want=want):
                i = self.idx(self.row("mine"), self.row("fish", env_fp="fish-env", **kw))
                self.assertEqual(i.decide(self.tmp, T)[:2], ("must-run", want))

    def test_S8_row_for_unknown_prefix_is_latest(self):
        i = self.idx(self.row("mine"), self.row("fish", env_fp="fish-env"))
        self.assertEqual(i.row_for(T, "no-such-key")["host"], "fish")
        self.assertEqual(i.row_for(T)["host"], "fish")

    def test_S9_new_reason_has_plain_words_in_every_locale(self):
        import test_entry
        for loc in ("zh-TW", "en", "ja", "ko"):
            with self.subTest(loc=loc):
                old = test_entry.LOC
                test_entry.LOC = loc
                try:
                    self.assertNotEqual(test_entry._why_text("unstable-same-key", "x"), "unstable-same-key")
                finally:
                    test_entry.LOC = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
