#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""10-03 子 AI 審查找到的「可能把紅藏成綠」(docs/PLAN-shadow-cross-device-1002.md 第八之二之五節)。

  F1 往回找不越過必跑標記(隔離壞行留下的墓碑):[綠 K1、墓碑、別台綠 K2] ⇒ 必跑(以前:越過墓碑沿用綠 K1)
  F1c 墓碑排在前面(檔案順序不可靠時就會這樣)⇒ 一樣不往回找,必跑
  F1b 往回找碰到紅列就停:[綠 K1、紅 K3、別台綠 K2] ⇒ 必跑
  F2 停用跨主機旗標在時,從別台收進來(帶 via)但沒有 host_id 的列也不用(以前:沒有 host_id 當本機)
  F3 抽查的作廢只打綠:green_only 的作廢不會作廢同鑰匙的紅列(紅證據留著 ⇒ 以後判不穩定)
  F4 作廢時間用 UTC:兩邊都有 UTC ⇒ 比 UTC(早的作廢、晚的留);列沒有 UTC ⇒ 一律算在作廢之前(保守)
  F5 runner 收尾用「判斷當下用的那一列」,找不到就不當綠:靜態確認(不起整套執行器)
刀(10-03 實跑,逐格):拿掉「有墓碑就不往回找」⇒ F1c 紅(F1 仍綠:「碰到不能用的列就停」也擋住了);
    拿掉「碰到不能用的列就停」⇒ F1b 紅;local_only 不看 via ⇒ F2 紅;
    _void_hits 不看 green_only ⇒ F3 紅;不比 UTC ⇒ F4 紅。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "verify"))
import reuse_index as ri  # noqa: E402
import shadow_tracer as st  # noqa: E402

T = "tests/test-a.py"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="review-fixes-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        self.items, self.fp = st.recompute_input_fp(str(self.repo), ["open:./a.txt:placeholder"])
        self.env_fp = st.env_fingerprint(st.child_env(self.repo, self.tmp / "p"), repo=str(self.repo))[0]

    def row(self, host="me", env_fp=None, **kw):
        env_fp = env_fp or self.env_fp
        r = {"test": T, "rc": 0, "inputs": list(self.items), "input_fp": self.fp, "env_fp": env_fp,
             "key": st.make_key(T, self.fp, env_fp), "measured": True, "external": False, "why": [],
             "lock_fp": ri.lock_fp(str(self.repo)), "host_id": host}
        r.update(kw)
        return r

    def idx(self, *rows):
        i = ri.Index()
        for r in rows:
            i.add_row(r)
        return i


class ReviewFixes(Base):
    def test_F1_tombstone_blocks_walk_back(self):
        tomb = {"test": T, "rc": -1, "inputs": [], "measured": "unmeasured", "external": False,
                "why": ["quarantined-bad-row"]}
        i = self.idx(self.row(), tomb, self.row("fish", env_fp="other"))
        self.assertEqual(i.decide(str(self.repo), T)[0], "must-run")

    def test_F1c_tombstone_anywhere_blocks_walk_back(self):
        tomb = {"test": T, "rc": -1, "inputs": [], "measured": "unmeasured", "external": False,
                "why": ["quarantined-bad-row"]}
        i = self.idx(tomb, self.row(), self.row("fish", env_fp="other"))
        self.assertEqual(i.decide(str(self.repo), T)[0], "must-run")

    def test_F1b_red_row_stops_walk_back(self):
        i = self.idx(self.row(), self.row(env_fp="k3-env", rc=1), self.row("fish", env_fp="other"))
        self.assertEqual(i.decide(str(self.repo), T)[0], "must-run")

    def test_F2_via_rows_are_foreign_even_without_host_id(self):
        pool = self.tmp / "pool"
        pool.mkdir()
        r = self.row()
        del r["host_id"]
        r["via"] = "@fish"
        (pool / "shared-x.rows.jsonl").write_text(json.dumps(r) + "\n", encoding="utf-8")
        (pool / ri.CROSS_HOST_FLAG).write_text("{}", encoding="utf-8")
        with mock.patch.dict(os.environ, {"AIB_HOST_ID": "me"}):
            idx = ri.build_index([str(pool)])
        self.assertEqual(idx.decide(str(self.repo), T)[:2], ("must-run", "no-previous"))

    def test_F3_green_only_void_keeps_red_evidence(self):
        v = ri._parse_void({"void": {"test": T, "green_only": True}, "before": "2099-01-01 00:00:00"})
        self.assertTrue(ri._void_hits(v, self.row()))
        self.assertFalse(ri._void_hits(v, self.row(rc=1)))
        self.assertFalse(ri._void_hits(v, self.row(rc=124, timed_out=True)))

    def test_F4_utc_comparison(self):
        v = ri._parse_void({"void": {"test": T}, "before": "2026-10-02 10:30:00", "before_utc": "2026-10-02T10:30:00Z"})
        # Mac(UTC+8)18:00 本地 = UTC 10:00:作廢之前寫的 ⇒ 要作廢(以前比本地字串 "18:00" > "10:30" ⇒ 漏作廢)
        self.assertTrue(ri._void_hits(v, self.row(time="2026-10-02 18:00", time_utc="2026-10-02T10:00:00Z")))
        self.assertFalse(ri._void_hits(v, self.row(time="2026-10-02 19:00", time_utc="2026-10-02T11:00:00Z")))
        self.assertTrue(ri._void_hits(v, self.row(time="2026-10-02 23:59")), "沒有 UTC 的列要保守地算在作廢之前")
        w = ri.write_void(self.tmp / "v.rows.jsonl", {"test": T}, "t")
        self.assertRegex(w.get("before_utc", ""), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_F5_runner_uses_the_decided_row(self):
        src = (ROOT / "tools" / "verify" / "runner.py").read_text(encoding="utf-8")
        self.assertIn('prow["reused_row"]', src)
        self.assertIn('prev = item.get("reused_row") or {}', src)
        self.assertNotIn('int(prev.get("rc", 0))', src, "找不到判斷當下那一列時不准預設成綠")


if __name__ == "__main__":
    unittest.main(verbosity=2)
