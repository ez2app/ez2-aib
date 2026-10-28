#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib test 大批排隊等滿上限 ⇒ 不跑(10-29 @f5fb8 / @ee842 實踩)。全部治具在暫存目錄,不碰真租約、真池。

以前等滿 LEASE_WAIT_MAX_S 就「不拿租約照跑」,跟租約持有人同時跑(load 又上去),租約失去序列化意義。
現在:等滿還沒輪到 ⇒ 回 RC_EXCLUSIVE(沒跑,下游 crosscheck / pushgate 當「沒驗」),印「請重排」;--wait-max 可改上限。

G1 big_batch_lease:一直被別人拿著、上限 0 ⇒ 試過一輪就回 gave_up,沒有 acquire 也沒照跑
G2 main:big_batch_lease 回 gave_up ⇒ rc = RC_EXCLUSIVE、不進 _main_run(刀:把 gave_up 從擋下名單拿掉 ⇒ 紅)
G3 --wait-max 傳進 big_batch_lease;負數當 0;沒給 ⇒ None(照環境變數 / 預設)
G4 字:四語都講「沒跑」並教 --wait-max;舊的「照跑」註記鍵已拿掉
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "verify"))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeb40-lease-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": str(Path(self.tmp) / "state"),
                                           "AIB_HOST_LEASE_FILE": str(Path(self.tmp) / "host-lease.json"),
                                           "EZ2_VERIFY_SHADOW_DIR": str(Path(self.tmp) / "shadow")})
        env.start()
        self.addCleanup(env.stop)
        (Path(self.tmp) / "shadow").mkdir()
        import test_entry
        self.te = test_entry
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test-x.py").write_text("print(1)\n", encoding="utf-8")


class G1Lease(_Base):
    def test_g1_held_forever_cap0_gives_up_without_acquire(self):
        held = ("held", {"owner": "@other", "what": "aib test 300", "expiry_at": "05:06"}, None)
        tool_calls, said = [], []

        def tool(*args, env=None):
            tool_calls.append(args[:2])
            return 0, ""
        with mock.patch.object(self.te, "_lease_now", return_value=held), \
                mock.patch.object(self.te, "_lease_tool", side_effect=tool), \
                mock.patch.object(self.te, "lease_ttl", return_value=600), \
                mock.patch.object(self.te, "_holder_is_ancestor", return_value=False), \
                mock.patch.object(self.te, "exclusive_block", return_value=None):
            r = self.te.big_batch_lease(999, self.repo, said.append, sleep=lambda s: None, force=True, wait_max=0)
        self.assertEqual(r["why"], "gave_up")
        self.assertIsNone(r["run_id"])
        self.assertNotIn("acquire", [c[0] for c in tool_calls], "被拿著時不該去搶")
        self.assertIn(("queue", "remove"), tool_calls, "排過隊要退出名單")
        self.assertTrue(any("--wait-max" in s for s in said), said)


class G2Main(_Base):
    def _main(self, why, argv=()):
        ran = []
        with mock.patch.object(self.te.reuse_index, "auto_compact", return_value=[]), \
                mock.patch.object(self.te, "_lease_now", return_value=(None, None)), \
                mock.patch.object(self.te, "_ci_or_fixture_home", return_value=True), \
                mock.patch.object(self.te, "exclusive_block", return_value=None), \
                mock.patch.object(self.te, "big_batch_lease",
                                  return_value={"why": why, "run_id": None, "waited": 3600}) as bbl, \
                mock.patch.object(self.te, "release_batch_lease"), \
                mock.patch.object(self.te, "_main_run", side_effect=lambda *a, **k: ran.append(1) or 0), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            rc = self.te.main(["--repo", str(self.repo)] + list(argv) + ["tests/test-x.py"])
        return rc, ran, bbl

    def test_g2_gave_up_does_not_run(self):
        rc, ran, _ = self._main("gave_up")
        self.assertEqual(rc, self.te.RC_EXCLUSIVE, "等滿上限要回『沒跑』")
        self.assertEqual(ran, [], "等滿上限不准不拿租約照跑")

    def test_g2_big_still_runs(self):
        rc, ran, _ = self._main("big")
        self.assertEqual((rc, ran), (0, [1]))

    def test_g3_wait_max_passed(self):
        _, _, bbl = self._main("big", ["--wait-max", "7200"])
        self.assertEqual(bbl.call_args.kwargs.get("wait_max"), 7200)
        _, _, bbl = self._main("big", ["--wait-max", "-5"])
        self.assertEqual(bbl.call_args.kwargs.get("wait_max"), 0)
        _, _, bbl = self._main("big")
        self.assertIsNone(bbl.call_args.kwargs.get("wait_max"))


class G4Words(unittest.TestCase):
    def test_g4_four_languages(self):
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8"))
            msg = d["aib.test.lease_queue_gave_up"]
            self.assertIn("--wait-max", msg, lang)
            self.assertIn("{waited}", msg, lang)
            self.assertNotIn("aib.test.lease_queue_gave_up_note", d, lang)
            self.assertIn("--wait-max", d["aib.test.usage"], lang)


if __name__ == "__main__":
    unittest.main()
