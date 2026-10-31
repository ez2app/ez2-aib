#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""交接重寫不再連環作廢(10-31 T15,@557fe 一天重跑 5 次;SPEC-dm-triage-auto v2 §6 T15)。

連鎖:收據標「不完整」(少交代一封未讀)⇒ 收據時刻被當本輪起點 ⇒ 剛做的整理 / 總結變「本輪沒做」
⇒ 重整理、重收集 ⇒ 收集又抓到新信 ⇒ 又不完整。

R1 最新收據不完整、前一張完整 ⇒ 本輪起點 = 前一張完整收據(不是最新那張)
R2 最新收據完整 ⇒ 起點 = 它(行為不變)
R3 收據全都不完整 ⇒ 起點退回交接記的輪起點(rounds.jsonl)
R4 收據之後做的整理仍算本輪;不完整收據之前做的整理也還算(不再被它推掉)
U1 收集時列的未讀,寫第三步時已讀 / 已結 / 已收 / 確定不見 ⇒ 不再要求第二段交代;還沒讀的照要求
U2 讀佇列出錯 ⇒ 照收集時的清單要求(不放水);U2b 真的讀取器遇壞檔(吞錯回 json)⇒ 照要求
U2c 讀檔層注入 PermissionError / EIO(讀取器吞成 missing)⇒ 檔還在就照要求(#465 Codex r2)
U4 claimed / hold_human / 讀檔出錯 ⇒ 照要求(不代表讀過;#465 Codex r1 M2)
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
    "q_clai00000000000f": {"status": "claimed"},
    "q_hold00000000000g": {"status": "hold_human"},
    "q_arch00000000000h": {"status": "archived"},
}


def fake_raw(seat, item_id, env=None):
    if item_id == "q_badj00000000000i":
        return None, "json"
    it = ITEMS.get(item_id)
    return (it, "ok") if it else (None, "missing")


class Unread(Base):
    def ids(self, got):
        return [u["id"] for u in got["unread"]]

    def test_U1_handled_mail_not_required(self):
        import dm_queue
        f = _facts("q_read00000000000a", "q_done00000000000b", "q_gone00000000000e", "q_arch00000000000h",
                   "q_live00000000000c", "q_queu00000000000d")
        with mock.patch.object(dm_queue, "_load_item_raw", side_effect=fake_raw):
            got = aib_handoff._unread_still_live(f)
        self.assertEqual(self.ids(got), ["q_live00000000000c", "q_queu00000000000d"])
        self.assertEqual(len(f["unread"]), 6)            # 原本的 facts 不改

    def test_U2_queue_error_keeps_list(self):
        import dm_queue
        f = _facts("q_read00000000000a", "q_live00000000000c")
        with mock.patch.object(dm_queue, "_load_item_raw", side_effect=OSError("boom")):
            got = aib_handoff._unread_still_live(f)
        self.assertEqual(self.ids(got), ["q_read00000000000a", "q_live00000000000c"])

    def test_U2b_real_reader_bad_file_kept(self):
        import dm_queue
        iid = "q_badf0000000000aa"
        p = dm_queue.item_path(CODE, iid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json", encoding="utf-8")       # 真的讀取器會吞掉錯、回 (None, "json")
        got = aib_handoff._unread_still_live(_facts(iid, "q_gone0000000000bb"))
        self.assertEqual(self.ids(got), [iid], "壞檔 ≠ 不見;確定不存在的才拿掉")

    def test_U2c_read_error_in_reader_not_missing(self):
        """錯誤注入在讀檔那層(Path.read_text):_load_item_raw 會把 PermissionError / EIO 吞成 missing,仍要照要求。"""
        import dm_queue
        import errno
        iid = "q_perm0000000000cc"
        p = dm_queue.item_path(CODE, iid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")
        real = Path.read_text
        for exc in (PermissionError(errno.EACCES, "denied"), OSError(errno.EIO, "io")):
            def boom(self_, *a, _exc=exc, **k):
                if str(self_) == str(p):
                    raise _exc
                return real(self_, *a, **k)
            with mock.patch.object(Path, "read_text", boom):
                self.assertEqual(dm_queue._load_item_raw(CODE, iid)[1], "missing")   # 讀取器確實吞成 missing
                got = aib_handoff._unread_still_live(_facts(iid, "q_gone0000000000dd"))
            self.assertEqual(self.ids(got), [iid], repr(exc))

    def test_U4_claimed_and_hold_still_required(self):
        import dm_queue
        f = _facts("q_clai00000000000f", "q_hold00000000000g", "q_badj00000000000i")
        with mock.patch.object(dm_queue, "_load_item_raw", side_effect=fake_raw):
            got = aib_handoff._unread_still_live(f)
        self.assertEqual(self.ids(got), ["q_clai00000000000f", "q_hold00000000000g", "q_badj00000000000i"])

    def test_U3_incomplete_skips_read_mail(self):
        import dm_queue
        f = _facts("q_read00000000000a", "q_live00000000000c")
        raw = {"s1": "", "s2": "", "s3": ""}
        with mock.patch.object(dm_queue, "_load_item_raw", side_effect=fake_raw):
            out = aib_handoff._incomplete(raw, f, {"tidy": "ok"})
        joined = "\n".join(out)
        self.assertIn("q_live00000000000c", joined)
        self.assertNotIn("q_read00000000000a", joined)


if __name__ == "__main__":
    unittest.main()
