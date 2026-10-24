#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""記憶整理第 5 包:aib summary 與交接的本輪檢查。

總結要先整理(沒整理 rc 2);交接不擋,只記本輪整理 / 總結有沒有做。渲染換成假的(不跑 aib-reply.sh、不跳 popup)。
"""
import datetime
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
for _p in (ROOT / "src" / "cockpit", ROOT / "src" / "memory", ROOT / "src" / "identity", ROOT / "tests"):
    sys.path.insert(0, str(_p))
import aib_testlib  # noqa: E402,F401
import aib_summary  # noqa: E402
import aib_handoff  # noqa: E402
import memory_tidy  # noqa: E402

CTX = {"code": "abcde", "seat_uid": "u", "model": "claude-opus-5-5", "project_uid": ""}


def _iso(dt):
    return dt.astimezone().isoformat(timespec="seconds")


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-summary-test-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        env = mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": str(self.td), "EZ2_AIB_HOME": str(self.td)})
        env.start()
        self.addCleanup(env.stop)
        for target, val in ((memory_tidy, "seat_info"), (aib_summary, "round_start")):
            pass
        p1 = mock.patch.object(memory_tidy, "seat_info", return_value=dict(CTX))
        p2 = mock.patch.object(aib_summary, "round_start", return_value=datetime.datetime.now().timestamp() - 3600)
        p1.start(), p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        self.brain = {"ok": True, "uid": "3sum01"}         # 第二大腦另測(test-handoff-writeback-abb89-1024)
        p3 = mock.patch.object(aib_summary, "brain_write", lambda ctx, report, seat="", writer=None: dict(self.brain))
        p3.start()
        self.addCleanup(p3.stop)
        self.report = self.td / "r.json"
        self.report.write_text("{}", encoding="utf-8")

    def tidy_receipt(self, hours_ago=0.1, status="ok"):
        d = self.td / "state" / "tidy"
        d.mkdir(parents=True, exist_ok=True)
        at = _iso(datetime.datetime.now() - datetime.timedelta(hours=hours_ago))
        (d / "abcde.json").write_text(json.dumps({"status": status, "at": at}), encoding="utf-8")

    @staticmethod
    def ok_runner(cmd):
        return subprocess.CompletedProcess(cmd, 0, "回報檔:/x/REPORT-FULL-abcde.txt\n", "")


class Summary(Base):
    def test_requires_tidy_this_round(self):
        with self.assertRaises(memory_tidy.TidyError) as cm:
            aib_summary.write(self.report, runner=self.ok_runner)
        self.assertIn("先跑 aib memory tidy", str(cm.exception))
        self.tidy_receipt(hours_ago=5)                   # 上一輪的整理不算
        with self.assertRaises(memory_tidy.TidyError):
            aib_summary.write(self.report, runner=self.ok_runner)

    def test_writes_receipt(self):
        self.tidy_receipt(status="partial")
        rec = aib_summary.write(self.report, runner=self.ok_runner)
        self.assertEqual(rec["file"], "/x/REPORT-FULL-abcde.txt")
        self.assertEqual(rec["tidy_status"], "partial", "整理部分失敗也算有整理過(照實記)")
        self.assertEqual(aib_summary.summary_this_round("abcde")["file"], "/x/REPORT-FULL-abcde.txt")

    def test_render_fail_no_receipt(self):
        self.tidy_receipt()
        bad = lambda cmd: subprocess.CompletedProcess(cmd, 1, "", "lint 沒過")  # noqa: E731
        with self.assertRaises(memory_tidy.TidyError):
            aib_summary.write(self.report, runner=bad)
        self.assertEqual(aib_summary.summary_this_round("abcde"), {})

    def test_brain_write_failure_is_rc2_and_recorded(self):
        """10-24 維護者「總結…不能跳過、一定要寫回第二大腦」:寫不進 ⇒ 報錯,收據照記 brain.ok=false(verify 第 6 項紅)。"""
        self.tidy_receipt()
        self.brain = {"ok": False, "why": "這台量不到第二大腦"}
        with self.assertRaises(memory_tidy.TidyError) as cm:
            aib_summary.write(self.report, runner=self.ok_runner)
        self.assertIn("第二大腦寫不進", str(cm.exception))
        self.assertFalse(aib_summary.summary_this_round("abcde")["brain"]["ok"])

    def test_uses_aib_reply(self):
        self.tidy_receipt()
        seen = []
        aib_summary.write(self.report, runner=lambda cmd: seen.append(cmd) or self.ok_runner(cmd))
        self.assertTrue(seen[0][1].endswith("tools/report/aib-reply.sh"), seen)


class HandoffChecks(Base):
    def test_nothing_done(self):
        c = aib_handoff.round_checks("abcde")
        self.assertEqual((c["tidy"], c["summary"]), ("沒做", "沒做"))
        self.assertIn("aib memory tidy", c["block"])

    def test_both_done(self):
        self.tidy_receipt()
        aib_summary.write(self.report, runner=self.ok_runner)
        c = aib_handoff.round_checks("abcde")
        self.assertEqual((c["tidy"], c["summary"]), ("ok", "有"))
        self.assertNotIn("下一輪開頭", c["block"])

    def test_check_failure_never_blocks(self):
        with mock.patch.object(aib_summary, "tidy_this_round", side_effect=RuntimeError("boom")):
            c = aib_handoff.round_checks("abcde")
        self.assertTrue(c["tidy"].startswith("量不到"))
        self.assertEqual(c["block"], "")

    def test_step3_records_checks_in_receipt(self):
        text = (ROOT / "src" / "cockpit" / "aib_handoff.py").read_text(encoding="utf-8")
        self.assertIn('checks = round_checks(code)', text)
        self.assertIn('receipt["tidy"], receipt["summary"] = checks["tidy"], checks["summary"]', text)


# 公開版缺件(已回報、本支不修:檔案在 tools/report,不在 aib_summary):aib_summary.write 預設叫
#   tools/report/aib-reply.sh,而 tools/report/aib-report.py 的格式檢查要 tools/report/report-lint.sh;兩支都沒進公開版
#   ⇒ 真的跑 `aib summary write` 一定是 rc 2「總回報渲染或格式檢查沒過」。
RENDERER = ROOT / "tools" / "report" / "aib-reply.sh"
LINTER = ROOT / "tools" / "report" / "report-lint.sh"


class PublicRenderer(Base):
    @unittest.skipIf(RENDERER.is_file(), "渲染器在了:這格只量「缺渲染器時不寫收據」,不去真跑渲染(會跳 popup)")
    def test_missing_renderer_fails_closed_without_receipt(self):
        """預設 runner 真的去叫 aib-reply.sh:缺檔 ⇒ TidyError、沒有總結收據(不當掉、不假寫)。"""
        self.tidy_receipt()
        with self.assertRaises(memory_tidy.TidyError) as cm:
            aib_summary.write(self.report)
        self.assertIn("沒寫收據", str(cm.exception))
        self.assertEqual(aib_summary.summary_this_round("abcde"), {})

    def test_default_renderer_and_linter_are_shipped(self):
        """公開版要附渲染器與檢查器(G6;0.1.7 隨私倉最後匯出 #60 帶進來)。"""
        for p in (RENDERER, LINTER):
            self.assertTrue(p.is_file(), "公開版缺 %s" % p.relative_to(ROOT))


if __name__ == "__main__":
    unittest.main()
