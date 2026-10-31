#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""交接重寫不再連環作廢(10-31 T15,@557fe 一天重跑 5 次;SPEC-dm-triage-auto v2 §6 T15)。

連鎖:收據標「不完整」(少交代一封未讀)⇒ 收據時刻被當本輪起點 ⇒ 剛做的整理 / 總結變「本輪沒做」
⇒ 重整理、重收集 ⇒ 收集又抓到新信 ⇒ 又不完整。

R1 最新收據不完整、前一張完整 ⇒ 本輪起點 = 前一張完整收據(不是最新那張)
R2 最新收據完整 ⇒ 起點 = 它(行為不變)
R3 收據全都不完整 ⇒ 起點退回交接記的輪起點(rounds.jsonl)
R4 收據之後做的整理仍算本輪;不完整收據之前做的整理也還算(不再被它推掉)
U1 收集時列的未讀,寫第三步時已讀 / 已結 / 不見 ⇒ 不再要求第二段交代;還沒讀的照要求
U2 讀佇列出錯 ⇒ 照收集時的清單要求(不放水)
U3 走 _incomplete:已讀那封不再列「沒交代」
"""
import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401

sys.path.insert(0, str(HERE.parent / "src" / "cockpit"))
import aib_handoff  # noqa: E402
import aib_summary  # noqa: E402

CODE = "t15aa"
T0 = 1_800_000_000


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        td = os.path.realpath(self.tmp.name)
        p = mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": td, "EZ2_AIB_HOME": td})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        aib_handoff.handoff_dir().mkdir(parents=True, exist_ok=True)

    def receipt(self, ts, incomplete=()):
        rec = {"seat": CODE, "ts": ts, "incomplete": list(incomplete)}
        with open(aib_handoff.receipts_log(CODE), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        aib_handoff.receipt_path(CODE).write_text(json.dumps(rec), encoding="utf-8")

    def round(self, ts):
        aib_handoff.record_round(CODE, "test", ts=ts)


class RoundStart(Base):
    def test_R1_incomplete_latest_falls_back_to_last_complete(self):
        self.round(T0)
        self.receipt(T0 + 100)
        self.receipt(T0 + 200, ["待審未讀 q_x 第二段沒交代"])
        self.assertEqual(aib_summary.round_start(CODE), T0 + 100)

    def test_R2_complete_latest_unchanged(self):
        self.round(T0)
        self.receipt(T0 + 100, ["x"])
        self.receipt(T0 + 200)
        self.assertEqual(aib_summary.round_start(CODE), T0 + 200)

    def test_R3_all_incomplete_uses_round(self):
        self.round(T0)
        self.receipt(T0 + 100, ["x"])
        self.receipt(T0 + 200, ["y"])
        self.assertEqual(aib_summary.round_start(CODE), T0)

    def test_R4_tidy_before_incomplete_receipt_still_counts(self):
        import memory_tidy
        self.round(T0)
        tidy_at = datetime.datetime.fromtimestamp(T0 + 150).isoformat()
        self.receipt(T0 + 200, ["待審未讀 q_x 第二段沒交代"])
        with mock.patch.object(memory_tidy, "last_receipt", return_value={"at": tidy_at, "status": "ok"}):
            self.assertEqual(aib_summary.tidy_this_round(CODE).get("at"), tidy_at)
            self.receipt(T0 + 300)                       # 補好之後的完整收據 ⇒ 那次整理就算上一輪的了
            self.assertEqual(aib_summary.tidy_this_round(CODE), {})


def _facts(*ids):
    return {"seat": {"code": CODE}, "unread": [{"id": i, "from": "@zzzzz", "summary": "s"} for i in ids]}


ITEMS = {
    "q_read00000000000a": {"status": "read", "read_at": T0},
    "q_done00000000000b": {"status": "done", "read_at": T0},
    "q_live00000000000c": {"status": "delivered"},
    "q_queu00000000000d": {"status": "queued"},
}


def fake_load(seat, item_id, env=None, include_deleted=False):
    return ITEMS.get(item_id)


class Unread(Base):
    def test_U1_handled_mail_not_required(self):
        import dm_queue
        f = _facts("q_read00000000000a", "q_done00000000000b", "q_gone00000000000e",
                   "q_live00000000000c", "q_queu00000000000d")
        with mock.patch.object(dm_queue, "_load_item", side_effect=fake_load):
            got = aib_handoff._unread_still_live(f)
        self.assertEqual([u["id"] for u in got["unread"]], ["q_live00000000000c", "q_queu00000000000d"])
        self.assertEqual(len(f["unread"]), 5)            # 原本的 facts 不改

    def test_U2_queue_error_keeps_list(self):
        import dm_queue
        f = _facts("q_read00000000000a", "q_live00000000000c")
        with mock.patch.object(dm_queue, "_load_item", side_effect=OSError("boom")):
            got = aib_handoff._unread_still_live(f)
        self.assertEqual([u["id"] for u in got["unread"]], ["q_read00000000000a", "q_live00000000000c"])

    def test_U3_incomplete_skips_read_mail(self):
        import dm_queue
        f = _facts("q_read00000000000a", "q_live00000000000c")
        raw = {"s1": "", "s2": "", "s3": ""}
        with mock.patch.object(dm_queue, "_load_item", side_effect=fake_load):
            out = aib_handoff._incomplete(raw, f, {"tidy": "ok"})
        joined = "\n".join(out)
        self.assertIn("q_live00000000000c", joined)
        self.assertNotIn("q_read00000000000a", joined)


if __name__ == "__main__":
    unittest.main()
