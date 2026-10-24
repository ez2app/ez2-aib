#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""收尾一定寫回人格與第二大腦(維護者 10-24:「必要不能跳過，一定要寫回對應的人格與第二大腦」「總結與交接都不能跳過」;
@ee4f8 實測 5 個缺口,本檔測 ①–④;verify 的紅綠在 test-handoff-check-abb89-1015)。

全部用暫存 EZ2_AIB_HOME / EZ2_AIB_STATE_ROOT,不碰真的 ~/.ez2-aib、不寫真的第二大腦(寫入一律換假的)。
W1 退路篩(#312 審查 A1 補:名字帶空格的新式抬頭、舊式抬頭;A5 補:bot 席一律不收):真人打字(含帶圖片的「[Image #3] …」)留下;系統提醒 / dm 標頭 / Stop hook / 工具結果 / isMeta /
   自我提醒排程 / 接續摘要 / 中斷提示丟掉
W2 缺 human_filter ⇒ human_words 從這一席的對話檔讀出真人原話(不再回空)
W3 交接筆記:同一輪本席同一步的上一則 ⇒ 取代;別席 / 別步 / 上一輪 / 沒有這一輪起點 ⇒ 不取代;步驟標籤只取數字
W4 總結第二大腦:取代本席同專案上一則總結(別席 / 別專案不取代);寫不進回 ok=false、不丟例外
W5 總結內文帶標題、大項、摘要、下一步;不帶證據指令
W6–W9(r2–r3):認不出抬頭 / 量不到 bot ⇒ 標讀不到;句數一律從本輪起點算(量不到 ⇒ None、重算認不出 ⇒ 讀不到);回顧 CLI 筆記寫不進 rc 2
刀:K1 退路篩永遠 True ⇒ W1 紅;K2 不看這一輪起點 ⇒ W3 紅;K3 總結不帶 supersedes ⇒ W4 紅
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]          # = aib/
for _p in (ROOT / "src" / "cockpit", ROOT / "src" / "memory", ROOT / "src" / "identity", ROOT / "tests"):
    sys.path.insert(0, str(_p))
import aib_testlib  # noqa: E402,F401
import memory_tidy  # noqa: E402
import aib_handoff  # noqa: E402
import aib_summary  # noqa: E402

HUMAN = ["為什麼這個角色的 icon 不正確?", "[Image #3] 地球要很大，鳥看他"]
MACHINE_TEXT = ["<system-reminder>x</system-reminder>",
                "IP:100.1.2.3/6ddf/某席@abcde >> IP:100.1.2.3/6ddf/我@bcdef 10-24 17:04(台北時間) 長訊已落檔",
                "Stop hook feedback:\n[x]: 狀態沒更新", "【自我提醒 · 授權】照清單推進一步", "[Request interrupted by user]",
                "This session is being continued from a previous conversation", "Another Claude session sent a message:",
                # #312 審查 A1:寄件人名字帶空格的新式抬頭、舊式抬頭 —— 只認 dm_header.parse
                "IP:100.64.0.1/3c73/Bot a seat@abc12 >> IP:100.64.0.1/3c73/未綁@d6a8f 09-26 18:41(台北時間) 你好",
                "[@abc12(某人格) > @d6a8f / 台北 10-24 12:00] 舊式抬頭的話"]
NO_TRANSCRIPT = memory_tidy.HUMAN_WORDS_NO_TRANSCRIPT


def _row(text, **extra):
    r = {"type": "user", "timestamp": "2026-10-24T09:00:00Z", "message": {"role": "user", "content": text}}
    r.update(extra)
    return json.dumps(r, ensure_ascii=False)


def transcript_lines():
    rows = [_row(t) for t in HUMAN] + [_row(t) for t in MACHINE_TEXT]
    rows.append(_row([{"type": "tool_result", "content": "ok"}]))
    rows.append(_row("看起來像真人但是 meta", isMeta=True))
    rows.append(_row("工具回來的", toolUseResult={"x": 1}))
    rows.append(json.dumps({"type": "assistant", "message": {"content": "助手的話不算"}}))
    return rows


def check_w1(t, flt):
    path = Path(t.tmp) / "t.jsonl"
    path.write_text("\n".join(transcript_lines()) + "\n", encoding="utf-8")
    with open(path, encoding="utf-8") as fh:
        got = [w for _ts, w in memory_tidy._human_rows(fh, "", {"human_bound": True}, flt)]
    t.assertEqual(got, HUMAN)


def entry(uid, step, seat="abcde", ms=2_000_000, kind="handoff"):
    return {"uid": uid, "kind": kind, "seat": seat, "created_ms": ms,
            "tags": ["aib/handoff"] + (["aib/handoff/step-%s" % step] if step else [])}


def check_w3(t, fn):
    es = [entry("old1", 1, ms=500_000), entry("a1", 1), entry("a2", 2), entry("b1", 1, seat="zzzzz"),
          entry("a1b", 1, ms=3_000_000), entry("nostep", None, ms=4_000_000),
          entry("other_latest", 1, seat="zzzzz", ms=3_500_000)]           # 別席同輪同步、比較新 ⇒ 不能取它(審查 A3)
    t.assertEqual(fn("abcde", "1", 1000.0, entries=es), "a1b")       # 這一輪同一步最新那則
    t.assertEqual(fn("abcde", "第 2 步", 1000.0, entries=es), "a2")   # 標籤只取數字
    t.assertEqual(fn("abcde", "3", 1000.0, entries=es), "")
    t.assertEqual(fn("abcde", "1", 0.0, entries=es), "")              # 沒有這一輪起點 ⇒ 不跨輪取代
    t.assertEqual(fn("abcde", "1", 3500.0, entries=es), "")           # 這一輪還沒寫過第 1 步(上一輪的不算)
    t.assertEqual(fn("abcde", "步", 1000.0, entries=es), "")


CTX = {"code": "abcde", "seat_uid": "u-me", "project_uid": "proj:0123456789abcdef", "model": "claude-opus-5-5"}


def check_w4(t, mod):
    es = [{"uid": "s1", "tags": ["aib/summary"], "seat": "abcde", "project_uid": CTX["project_uid"], "created_ms": 1},
          {"uid": "s2", "tags": ["aib/summary"], "seat": "abcde", "seat_uid": "u-old", "project_uid": CTX["project_uid"],
           "created_ms": 5},                                      # /clear 前那輪(seat_uid 不同)也要取代(審查 B-S5)
          {"uid": "x1", "tags": ["aib/summary"], "seat": "zzzzz", "project_uid": CTX["project_uid"], "created_ms": 9},
          {"uid": "x2", "tags": ["aib/summary"], "seat": "abcde", "project_uid": "proj:ffffffffffffffff", "created_ms": 9},
          {"uid": "x3", "tags": ["aib/tidy"], "seat": "abcde", "project_uid": CTX["project_uid"], "created_ms": 9}]
    calls = []

    def fake(kind, title, body, **kw):
        calls.append((kind, title, kw))
        return {"uid": "new1"}
    with mock.patch("aib_memory.entries", return_value=es):
        res = mod.brain_write(dict(CTX), {"title": "T"}, seat="6ddf/abcde", writer=fake)
    t.assertEqual(res, {"ok": True, "uid": "new1"})
    kind, _title, kw = calls[0]
    t.assertEqual(kind, "note")
    t.assertEqual(kw.get("supersedes"), "s2")
    t.assertEqual(kw.get("project"), CTX["project_uid"])
    t.assertIn("aib/summary", kw.get("tags"))

    def boom(*a, **k):
        raise RuntimeError("這台量不到第二大腦")
    with mock.patch("aib_memory.entries", return_value=[]):
        bad = mod.brain_write(dict(CTX), {}, writer=boom)
    t.assertFalse(bad["ok"])
    t.assertIn("量不到", bad["why"])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-writeback-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for k in ("EZ2_AIB_HOME", "EZ2_AIB_STATE_ROOT"):
            p = mock.patch.dict(os.environ, {k: self.tmp})
            p.start()
            self.addCleanup(p.stop)


class Behaviour(Base):
    def test_w1_fallback_filter(self):
        check_w1(self, memory_tidy._FallbackFilter)

    def test_w1b_bot_seat_never_human(self):
        ok, meta = memory_tidy._FallbackFilter.is_human({"human_bound": False}, {"text": HUMAN[0], "raw_refs": {}})
        self.assertEqual((ok, meta.get("why")), (False, "bot_seat"))

    def test_w2_human_words_without_human_filter(self):
        path = Path(self.tmp) / "seat.jsonl"
        path.write_text("\n".join(transcript_lines()) + "\n", encoding="utf-8")
        with mock.patch.dict(sys.modules, {"human_filter": None}), \
                mock.patch.object(aib_handoff, "rounds", return_value=[]), \
                mock.patch("seat_fork.find_transcript", return_value=path):
            self.assertEqual(memory_tidy.human_words_status(), memory_tidy.HUMAN_WORDS_FALLBACK)
            got = [w for _ts, w in memory_tidy.human_words({"code": "abcde", "seat_uid": "u"}, "")]
        self.assertEqual(got, HUMAN)
        self.assertEqual(memory_tidy.words_source("abcde"), memory_tidy.HUMAN_WORDS_FALLBACK)

    def test_w2b_no_transcript_is_not_silence(self):
        """找不到對話檔 ≠ 真人沒講話(#312 審查 B-M1):來源標 transcript_missing,human_words_usable 不放行。"""
        with mock.patch.dict(sys.modules, {"human_filter": None}), \
                mock.patch.object(aib_handoff, "rounds", return_value=[]), \
                mock.patch("seat_fork.find_transcript", return_value=None):
            self.assertEqual(memory_tidy.human_words({"code": "nnnnn", "seat_uid": "u"}, ""), [])
        self.assertEqual(memory_tidy.words_source("nnnnn"), NO_TRANSCRIPT)
        self.assertFalse(memory_tidy.human_words_usable(NO_TRANSCRIPT))

    def test_w3_handoff_supersedes_same_step_this_round(self):
        check_w3(self, aib_handoff._prev_step_note)
        self.assertEqual(aib_handoff._step_tag("2"), "aib/handoff/step-2")
        self.assertEqual(aib_handoff._step_tag("第 3 步"), "aib/handoff/step-3")
        self.assertEqual(aib_handoff._step_tag(""), "")

    def test_w3b_brain_write_passes_supersedes_and_step_tag(self):
        seen = {}

        def fake(kind, title, body, **kw):
            seen.update(kw)
            return {"uid": "h9", "path": "/v/h9.md"}
        with mock.patch.object(aib_handoff, "current_round_start", return_value=1000.0), \
                mock.patch("aib_memory.entries", return_value=[entry("a1", 1)]), \
                mock.patch("aib_memory.write", side_effect=fake):
            res = aib_handoff._brain_write({"code": "abcde"}, "1", "內文", "6ddf/abcde")
        self.assertTrue(res["ok"])
        self.assertEqual(seen.get("supersedes"), "a1")
        self.assertIn("aib/handoff/step-1", seen.get("tags"))

    def test_w4_summary_brain(self):
        check_w4(self, aib_summary)

    def test_w5_summary_body(self):
        body = aib_summary.brain_body({"title": "0.1.52 整合", "summary": "五張收進", "tree": [
            {"title": "發版", "children": [{"id": "A.1", "title": "#311", "status": "in_progress", "evidence": "CI 中",
                                           "evidence_command": "gh pr checks 311"}]}],
            "footer": {"next": "合 main"}})
        for s in ("0.1.52 整合", "五張收進", "發版", "#311", "合 main"):
            self.assertIn(s, body)
        self.assertNotIn("gh pr checks", body)


    def test_w6_dm_header_missing_is_unreadable(self):
        """退路篩認不出抬頭(dm_header 匯入不了)⇒ 一句都不收,來源標讀不到(不被當真人沉默;#312 r2 A-S2)。"""
        path = Path(self.tmp) / "seat.jsonl"
        path.write_text("\n".join(transcript_lines()) + "\n", encoding="utf-8")
        with mock.patch.dict(sys.modules, {"human_filter": None, "dm_header": None}), \
                mock.patch.object(aib_handoff, "rounds", return_value=[]), \
                mock.patch("seat_fork.find_transcript", return_value=path):
            self.assertEqual(memory_tidy.human_words({"code": "hhhhh", "seat_uid": "u"}, ""), [])
        self.assertEqual(memory_tidy.words_source("hhhhh"), NO_TRANSCRIPT)

    def test_w6b_filter_raising_is_unreadable(self):
        """認抬頭時 dm_header.parse 丟例外、或正式判準 is_human 丟例外 ⇒ 一句不收、標讀不到(#312 r4 A-1 / A-2)。"""
        path = Path(self.tmp) / "seat.jsonl"
        path.write_text("\n".join(transcript_lines()) + "\n", encoding="utf-8")
        boom = mock.Mock(side_effect=RuntimeError("x"))
        broken_dm = type(sys)("dm_header")
        broken_dm.parse = boom
        broken_hf = type(sys)("human_filter")
        broken_hf.is_human = boom
        for code, mods in (("ppppp", {"human_filter": None, "dm_header": broken_dm}), ("qqqqq", {"human_filter": broken_hf})):
            with mock.patch.dict(sys.modules, mods), \
                    mock.patch.object(aib_handoff, "rounds", return_value=[]), \
                    mock.patch("seat_fork.find_transcript", return_value=path):
                self.assertEqual(memory_tidy.human_words({"code": code, "seat_uid": "u"}, ""), [], code)
            self.assertEqual(memory_tidy.words_source(code), NO_TRANSCRIPT, code)

    def _collect(self, since, rs0, words_by_since, src_after=None):
        """跑 collect(外部全換假的)回 collect.json。words_by_since(since) ⇒ 句數;src_after = 第二次讀之後的來源。"""
        calls = []

        def fake_words(ctx, s="", span_rounds=False):
            calls.append(s)
            if len(calls) > 1 and src_after:
                memory_tidy._LAST_SOURCE[ctx["code"]] = src_after
            elif len(calls) == 1:
                memory_tidy._LAST_SOURCE[ctx["code"]] = memory_tidy.HUMAN_WORDS_FALLBACK
            return [("t", "w")] * words_by_since(s)
        tdir = Path(self.tmp) / "tidy"
        tdir.mkdir(exist_ok=True)
        with mock.patch.object(memory_tidy, "seat_info", return_value={"code": "rrrrr"}), \
                mock.patch.object(memory_tidy, "facts", return_value="f"), \
                mock.patch.object(memory_tidy, "render_draft", return_value="d"), \
                mock.patch.object(memory_tidy, "persona_card", return_value=None), \
                mock.patch.object(memory_tidy, "main_persona_target", return_value=(None, "")), \
                mock.patch.object(memory_tidy, "evidence_since", return_value=since), \
                mock.patch.object(memory_tidy, "human_words", side_effect=fake_words), \
                mock.patch.object(memory_tidy, "tidy_dir", return_value=tdir), \
                mock.patch.object(aib_handoff, "current_round_start", return_value=rs0):
            memory_tidy.collect("rrrrr")
        return json.loads((tdir / "rrrrr.collect.json").read_text(encoding="utf-8"))

    def test_w7_round_count_from_round_start(self):
        """收據的句數一律從這一輪起點算(#312 r2 B-H2、r3 A-1 / B-1 / B-2)。"""
        noon = 1_792_814_400.0                                   # 2026-10-24 12:00 +08
        early = lambda s: 3 if s <= "2026-10-24T12:00:00+08:00" else 0     # 真人 12:00 後講了 3 句,13:00 後沒講
        # 上一張收據在本輪中(13:00):只算 13:00 後 = 0 句 ⇒ 重算本輪 = 3
        col = self._collect("2026-10-24T13:00:00+08:00", noon, early)
        self.assertEqual((col["human_words"], col["human_words_round_count"]), ([], 3))
        # 上一張收據在上一輪(11:00、那時講 2 句):本輪其實 0 句 ⇒ 0,不把上一輪尾巴算進來
        col = self._collect("2026-10-24T11:00:00+08:00", noon, lambda s: 2 if s <= "2026-10-24T11:30" else 0)
        self.assertEqual(col["human_words_round_count"], 0)
        # 量不到本輪起點 ⇒ None(verify 不當沉默)
        col = self._collect("2026-10-24T13:00:00+08:00", 0.0, early)
        self.assertIsNone(col["human_words_round_count"])
        # 重算時認不出(標讀不到)⇒ 來源用較差的那個,不還原成第一次的 fallback
        col = self._collect("2026-10-24T13:00:00+08:00", noon, lambda s: 0, src_after=NO_TRANSCRIPT)
        self.assertEqual(col["human_words_status"], NO_TRANSCRIPT)
        self.assertEqual(memory_tidy.words_source("rrrrr"), NO_TRANSCRIPT)

    def test_w7b_apply_receipt_uses_round_count(self):
        """apply 收據的 human_words_count = collect 的 human_words_round_count;舊 collect 沒這欄 ⇒ None(r3 A-2、B-3)。"""
        src = Path(memory_tidy.__file__).read_text(encoding="utf-8")
        line = [l for l in src.splitlines() if l.strip().startswith('rec["human_words_count"]')]
        self.assertEqual(len(line), 1, line)
        self.assertIn('col.get("human_words_round_count")', line[0])
        self.assertNotIn("len(", line[0])

    def test_w8_bot_check_unmeasured_is_unreadable(self):
        """退路篩量不到是不是 bot 席 ⇒ 不收、標讀不到(r2 B-R5)。"""
        path = Path(self.tmp) / "seat.jsonl"
        path.write_text("\n".join(transcript_lines()) + "\n", encoding="utf-8")
        with mock.patch.dict(sys.modules, {"human_filter": None}), \
                mock.patch.object(aib_handoff, "rounds", return_value=[]), \
                mock.patch.object(aib_handoff, "binding_state", side_effect=OSError("x")), \
                mock.patch("seat_fork.find_transcript", return_value=path):
            self.assertEqual(memory_tidy.human_words({"code": "bbbbb", "seat_uid": "u"}, ""), [])
        self.assertEqual(memory_tidy.words_source("bbbbb"), NO_TRANSCRIPT)

    def test_w9_review_cli_rc2_when_note_fails(self):
        """遺漏回顧筆記寫不進 ⇒ CLI rc 2(r2 A-S4)。"""
        import handoff_review
        f = Path(self.tmp) / "r.md"
        f.write_text("x", encoding="utf-8")
        for note in ("", "failed: no vault"):
            with mock.patch.object(handoff_review, "apply", return_value={"note": note, "lines": 0, "by_kind": {}}):
                self.assertEqual(handoff_review.main(["apply", "--file", str(f)]), 2, note)
        with mock.patch.object(handoff_review, "apply", return_value={"note": "3abc", "lines": 1, "by_kind": {}}):
            self.assertEqual(handoff_review.main(["apply", "--file", str(f)]), 0)


class Knives(Base):
    def test_k1_filter_always_true(self):
        class AlwaysTrue:
            @staticmethod
            def is_human(*_a, **_k):
                return True, {}
        with self.assertRaises(AssertionError):
            check_w1(self, AlwaysTrue)

    def test_k2_ignores_round_start(self):
        def no_since(code, step_label, since, entries=None):
            return aib_handoff._prev_step_note(code, step_label, 1.0, entries=entries)
        with self.assertRaises(AssertionError):
            check_w3(self, no_since)

    def test_k3_summary_without_supersedes(self):
        class NoSup:
            brain_write = staticmethod(lambda ctx, report, seat="", writer=None: aib_summary.brain_write(
                ctx, report, seat, writer=lambda k, t, b, **kw: writer(k, t, b, **dict(kw, supersedes=""))))
        with self.assertRaises(AssertionError):
            check_w4(self, NoSup)


if __name__ == "__main__":
    unittest.main(verbosity=2)
