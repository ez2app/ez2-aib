#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""交接改嚴 + aib handoff verify(src/cockpit/handoff_check.py)。

維護者要求「交接也要更新人格和專案卡」「三段不能馬虎」「要能確認上面這些一定完成」;審查定調:不硬擋、預填、只算本席。
狀態根換暫存;收據路徑、本輪起點、綁定狀態換成假的;不碰真倉以外的東西(sha 用假的查詢)。
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
for _p in ("cockpit", "memory", "identity"):
    sys.path.insert(0, str(ROOT / "src" / _p))
import aib_testlib  # noqa: E402,F401
import aib_handoff  # noqa: E402
import handoff_check as hc  # noqa: E402

FACTS = {"seat": {"code": "abcde"}, "repo": {"path": str(ROOT)},
         "worktrees": {"shown": [{"path": "/x/wt-ad-abcde", "branch": "pkg/abcde", "head": "1234567", "mine": True},
                                 {"path": "/x/wt-other", "branch": "other", "mine": False}]},
         "unmerged": {"shown": [{"branch": "pkg/abcde", "ts": 0}, {"branch": "someone/else", "ts": 0}]},
         "background": [{"pid": "4242", "cmd": "python3 menu_pick.py watch"}],
         "unread": [{"id": "q_1234abcd", "from": "@bcdef", "summary": "請 rebase"}]}


def filled(extra_s2=""):
    secs = {}
    for sec, names in hc.FIELDS.items():
        body = []
        for n in names:
            body += ["### %s" % hc.field_title(sec, n), "這一欄寫了真的內容,不是空的,%s" % n]
        secs[sec] = "\n".join(body)
    secs["s2"] += "\n" + extra_s2
    return secs


class Fields(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"EZ2_AIB_LOCALE": "zh-TW"})
        p.start()
        self.addCleanup(p.stop)

    def test_all_fields_present(self):
        self.assertEqual(hc.missing_fields(filled()), [])

    def test_missing_field_listed(self):
        secs = filled()
        secs["s1"] = secs["s1"].replace("### %s" % hc.field_title("s1", "correction"), "### 別的")
        miss = hc.missing_fields(secs)
        self.assertEqual(len(miss), 1)
        self.assertIn(hc.field_title("s1", "correction"), miss[0])

    def test_empty_field_listed(self):
        secs = filled()
        secs["s3"] = secs["s3"].replace("這一欄寫了真的內容,不是空的,next3", "")
        self.assertTrue(any(hc.field_title("s3", "next3") in m for m in hc.missing_fields(secs)))

    def test_prefill_own_only(self):
        inflight = hc.prefill("s2", "inflight", FACTS)
        self.assertTrue(any("wt-ad-abcde" in x and x.startswith("- " + hc.MACHINE) for x in inflight))
        self.assertFalse(any("wt-other" in x for x in inflight), "只算本席工作樹")
        self.assertFalse(any("someone/else" in x for x in inflight))
        self.assertIn("4242", "".join(hc.prefill("s2", "background", FACTS)))
        self.assertIn("q_1234abcd", "".join(hc.prefill("s2", "waiting", FACTS)))

    def test_field_titles_are_prompt_pieces(self):
        aib_handoff._PROMPT_PIECES[:] = []
        own = aib_handoff.own_text("### " + hc.field_title("s2", "pitfalls"))
        self.assertLess(len(own), 5, "欄位標題不算自己寫的字")


class Reconcile(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"EZ2_AIB_LOCALE": "zh-TW"})
        p.start()
        self.addCleanup(p.stop)

    def test_all_accounted(self):
        s2 = "pid 4242 選單代按,kill 4242 停;wt-ad-abcde 進行中;分支 pkg/abcde;q_1234abcd 已處理"
        self.assertEqual(hc.reconcile(FACTS, filled(s2), sha_exists=lambda r, s: True), [])

    def test_missing_items_listed(self):
        out = hc.reconcile(FACTS, filled(""), sha_exists=lambda r, s: True)
        text = "\n".join(out)
        self.assertIn("4242", text)
        self.assertIn("wt-ad-abcde", text)
        self.assertIn("pkg/abcde", text)
        self.assertIn("q_1234abcd", text)
        self.assertNotIn("wt-other", text, "別席的不算")

    def test_fake_sha_is_warning_not_incomplete(self):
        s2 = "pid 4242 wt-ad-abcde pkg/abcde q_1234abcd;提交 deadbeef1 已推"
        warns = []
        out = hc.reconcile(FACTS, filled(s2), sha_exists=lambda r, s: s != "deadbeef1", warnings=warns)
        self.assertEqual(out, [], "sha 可能是別的倉 ⇒ 只提醒(審查 7)")
        self.assertTrue(any("deadbeef1" in x for x in warns))

    def test_uuid_first_block_not_sha(self):
        s2 = "pid 4242 wt-ad-abcde pkg/abcde q_1234abcd session 65adf29d-9925-4526-8137-d6636add475d"
        warns = []
        hc.reconcile(FACTS, filled(s2), sha_exists=lambda r, s: False, warnings=warns)
        self.assertEqual(warns, [])

    def test_prefilled_lines_do_not_count(self):
        s2 = "\n".join(hc.prefill("s2", "background", FACTS) + hc.prefill("s2", "inflight", FACTS)
                       + hc.prefill("s2", "waiting", FACTS))
        out = hc.reconcile(FACTS, filled(s2), sha_exists=lambda r, s: True)
        self.assertEqual(len(out), 4, "留著機器預填行不算交代(審查 8):%s" % out)

    def test_strip_template(self):
        body = "### %s\n- %s pid 4242 x\n我寫的判斷" % (hc.field_title("s2", "background"), hc.MACHINE)
        self.assertEqual(hc.strip_template(body).strip(), "我寫的判斷")

    def test_word_latest_not_test(self):
        secs = filled("pid 4242 wt-ad-abcde pkg/abcde q_1234abcd")
        title = "### %s" % hc.field_title("s2", "inflight")
        secs["s2"] = secs["s2"].replace(title, title + "\n- 拉了 latest 版、pytest 設定沒動")
        self.assertEqual(hc.reconcile(FACTS, secs, sha_exists=lambda r, s: True), [])

    def test_digits_only_not_sha_and_dm_id_not_sha(self):
        s2 = "pid 4242 wt-ad-abcde pkg/abcde q_1234abcd 時間 1791885793"
        out = hc.reconcile(FACTS, filled(s2), sha_exists=lambda r, s: False)
        self.assertEqual(out, [], "純數字(時間、pid)、dm id 不當 sha")

    def test_test_status_wording(self):
        base = "pid 4242 wt-ad-abcde pkg/abcde q_1234abcd"
        secs = filled(base)
        title = "### %s" % hc.field_title("s2", "inflight")
        bad = dict(secs, s2=secs["s2"].replace(title, title + "\n- 測試應該會過"))
        self.assertTrue(any("應該會過" in x for x in hc.reconcile(FACTS, bad, sha_exists=lambda r, s: True)))
        ok = dict(secs, s2=secs["s2"].replace(title, title + "\n- 測試 Ran 47 綠"))
        self.assertEqual(hc.reconcile(FACTS, ok, sha_exists=lambda r, s: True), [])


class Verify(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-handoff-verify-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        p = mock.patch.dict(os.environ, {"EZ2_AIB_LOCALE": "zh-TW", "EZ2_AIB_STATE_ROOT": str(self.td),
                                         "EZ2_AIB_HOME": str(self.td)})
        p.start()
        self.addCleanup(p.stop)
        import memory_tidy
        import aib_summary
        self.mt, self.sm = memory_tidy, aib_summary
        (self.td / "tidy").mkdir()
        (self.td / "tidy" / "writes.jsonl").write_text(
            json.dumps({"seat": "abcde", "at": "2026-10-13T18:00:00+08:00"}) + "\n", encoding="utf-8")
        card = self.td / "card.json"
        card.write_text(json.dumps({"layers": {"work_state": {"seats": {"86611": {"at": "2026-10-13T18:00:00+08:00"}}}}}),
                        encoding="utf-8")
        (self.td / "summary").mkdir()
        (self.td / "summary" / "abcde.json").write_text(json.dumps({"at": "2026-10-13T18:00:00+08:00", "file": "R.md",
                                                                   "brain": {"ok": True, "uid": "3abc02"}}),
                                                       encoding="utf-8")
        mem = self.td / "context.md"
        mem.write_text("x", encoding="utf-8")
        hand = self.td / "HANDOFF.md"
        hand.write_text("交接本文", encoding="utf-8")
        self.receipt = self.td / "receipt.json"
        self.receipt.write_text(json.dumps({"ts": 9e9, "file": str(hand), "sha": hashlib.sha256(hand.read_bytes()).hexdigest(),
                                            "steps": {"1": 1, "2": 2, "3": 3}, "brain": {"ok": True},
                                            "incomplete": []}), encoding="utf-8")
        self.tidy = {"at": "2026-10-13T18:00:00+08:00", "project_uid": "proj:x", "seat_id": "86611",
                     "files": {"persona": {"status": "ok"}, "card": {"status": "ok"}, "context": {"status": "ok"},
                               "note": {"status": "ok", "uid": "3abc01"}}}
        self.review = {"lines": 3, "by_kind": {"做了": 3}, "note": "3abc03", "words_source": memory_tidy.HUMAN_WORDS_FALLBACK}
        for target, attr, val in ((aib_handoff, "current_round_start", lambda c, h=None: 1000.0),
                                  (aib_handoff, "receipt_path", lambda c, h=None: self.receipt),
                                  (aib_handoff, "seat_memory_path", lambda c, h=None: mem),
                                  (aib_handoff, "binding_state", lambda c, h=None: {"problem": "ok"}),
                                  (memory_tidy, "tidy_dir", lambda: self.td / "tidy"),
                                  (memory_tidy, "card_path", lambda uid: card),
                                  (memory_tidy, "last_receipt", lambda c: self.tidy),
                                  (aib_summary, "summary_dir", lambda: self.td / "summary"),
                                  (__import__("handoff_review"), "done_this_round",
                                   lambda c, st: self.review)):
            q = mock.patch.object(target, attr, val)
            q.start()
            self.addCleanup(q.stop)

    def test_all_done(self):
        items = hc.verify("abcde")
        self.assertEqual(len(items), 9)
        self.assertTrue(all(ok for _n, ok, _d in items), items)
        txt, done = hc.render(items)
        self.assertTrue(done)
        self.assertIn("全部完成", txt)

    def test_tidy_failed_is_missing(self):
        self.tidy["files"]["card"] = {"status": "failed", "why": "鎖"}
        items = dict((n[:1], ok) for n, ok, _d in hc.verify("abcde"))
        self.assertFalse(items["1"])
        self.assertFalse(items["3"])

    def test_persona_ok_but_no_write_record(self):
        (self.td / "tidy" / "writes.jsonl").write_text("", encoding="utf-8")
        items = dict((n[:1], ok) for n, ok, _d in hc.verify("abcde"))
        self.assertFalse(items["2"], "收據說寫了但寫入紀錄沒有 ⇒ 不算")

    def test_skipped_needs_reason(self):
        self.tidy["files"]["persona"] = {"status": "skipped", "why": ""}
        items = dict((n[:1], ok) for n, ok, _d in hc.verify("abcde"))
        self.assertFalse(items["2"])

    def test_persona_skipped_only_when_human_silent(self):
        """10-24 維護者「必要不能跳過」:有原話卻 skipped / 讀不到原話 ⇒ 紅;讀得到且真人一句都沒講 ⇒ 過。"""
        self.tidy["files"]["persona"] = {"status": "skipped", "why": "這輪沒觀察到"}
        for hw, n, want in ((self.mt.HUMAN_WORDS_FALLBACK, 5, False), ("ok", 2, False),
                            (self.mt.HUMAN_WORDS_UNAVAILABLE, 0, False), ("", 0, False),
                            (self.mt.HUMAN_WORDS_FALLBACK, 0, True), ("ok", 0, True)):
            self.tidy["human_words"], self.tidy["human_words_count"] = hw, n
            self.assertEqual(dict((x[:1], ok) for x, ok, _d in hc.verify("abcde"))["2"], want, (hw, n))
        self.tidy["files"]["persona"] = {"status": "waiting", "why": "第一艦長卡大變動"}
        self.tidy["human_words_count"] = 9
        self.assertTrue(dict((x[:1], ok) for x, ok, _d in hc.verify("abcde"))["2"])

    def test_handoff_file_tampered(self):
        (self.td / "HANDOFF.md").write_text("改過", encoding="utf-8")
        items = dict((n[:1], ok) for n, ok, _d in hc.verify("abcde"))
        self.assertFalse(items["7"])

    def test_incomplete_receipt_fails_item7(self):
        rec = json.loads(self.receipt.read_text(encoding="utf-8"))
        rec["incomplete"] = ["背景程式 pid 4242 沒交代"]
        self.receipt.write_text(json.dumps(rec), encoding="utf-8")
        items = hc.verify("abcde")
        bad = [(n, d) for n, ok, d in items if not ok]
        self.assertEqual(len(bad), 1)
        self.assertIn("4242", bad[0][1])
        txt, done = hc.render(items)
        self.assertFalse(done)
        self.assertIn("1 項", txt)

    def test_summary_cannot_be_skipped(self):
        """10-24 維護者「總結與交接都不能跳過」:交接寫「沒做」不再算;總結沒寫進第二大腦也不算。"""
        rec = json.loads(self.receipt.read_text(encoding="utf-8"))
        rec["summary"] = "沒做"
        self.receipt.write_text(json.dumps(rec), encoding="utf-8")
        s = self.td / "summary" / "abcde.json"
        s.write_text(json.dumps({"at": "2026-10-13T18:00:00+08:00", "file": "R.md", "brain": {"ok": False, "why": "x"}}),
                     encoding="utf-8")
        self.assertFalse(dict((n[:1], ok) for n, ok, _d in hc.verify("abcde"))["6"])
        s.write_text(json.dumps({"at": "2026-10-13T18:00:00+08:00", "file": "R.md"}), encoding="utf-8")   # 舊收據沒 brain
        self.assertFalse(dict((n[:1], ok) for n, ok, _d in hc.verify("abcde"))["6"])
        s.unlink()
        self.assertFalse(dict((n[:1], ok) for n, ok, _d in hc.verify("abcde"))["6"], "收據寫明沒做也不算")

    def test_review_needs_note_and_real_source(self):
        """遺漏回顧:筆記沒寫進第二大腦 ⇒ 紅;0 句但讀不到原話(舊收據沒來源)⇒ 紅;0 句、讀得到 ⇒ 過。"""
        self.review.update(note="failed: no vault")
        self.assertFalse(dict((n[:1], ok) for n, ok, _d in hc.verify("abcde"))["9"])
        self.review.update(note="3abc03", lines=0, words_source=None)
        self.assertFalse(dict((n[:1], ok) for n, ok, _d in hc.verify("abcde"))["9"])
        self.review.update(words_source=self.mt.HUMAN_WORDS_FALLBACK)
        self.assertTrue(dict((n[:1], ok) for n, ok, _d in hc.verify("abcde"))["9"])

    def test_context_needs_tidy_write(self):
        self.tidy["files"]["context"] = {"status": "failed", "why": "x"}
        self.assertFalse(dict((n[:1], ok) for n, ok, _d in hc.verify("abcde"))["4"], "交接自己追加不算(審查 10)")

    def test_old_tidy_receipt_not_this_round(self):
        self.tidy["at"] = "1970-01-01T00:00:01+00:00"
        self.assertFalse(dict((n[:1], ok) for n, ok, _d in hc.verify("abcde"))["1"])

    def test_binding_proposal_in_receipt_counts(self):
        mock.patch.object(aib_handoff, "binding_state", lambda c, h=None: {"problem": "no_project"}).start()
        self.addCleanup(mock.patch.stopall)
        self.assertFalse(dict((n[:1], ok) for n, ok, _d in hc.verify("abcde"))["8"])
        rec = json.loads(self.receipt.read_text(encoding="utf-8"))
        rec["binding_proposal"] = {"sid": "x"}
        self.receipt.write_text(json.dumps(rec), encoding="utf-8")
        self.assertTrue(dict((n[:1], ok) for n, ok, _d in hc.verify("abcde"))["8"])


class AddOnly(unittest.TestCase):
    def test_row_keeps_old_now_and_unions_pending(self):
        import memory_tidy
        card = {"layers": {"work_state": {"seats": {"86611": {"now": "原本在做 A", "next_step": "",
                                                               "pending": ["舊待裁"]}}}}}
        with mock.patch.object(memory_tidy, "_read_json", return_value=card):
            row = memory_tidy.merge_row_add_only({"project_uid": "proj:x", "seat_id": "86611", "code": "abcde"},
                                                 {"now": "新的現況", "next_step": "下一步 B", "pending": ["新待裁", "舊待裁"]})
        self.assertEqual(row["now"], "原本在做 A", "只補不改:原有的現況不蓋")
        self.assertEqual(row["next_step"], "下一步 B", "原本空的才補")
        self.assertEqual(row["pending"], ["舊待裁", "新待裁"])


class Review(unittest.TestCase):
    WORDS = [("10-13 17:5x", "交接也要更新人格和專案卡"), ("10-13 17:5x", "把10%取消好了")]

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-review-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        import memory_tidy
        import handoff_review as hr
        self.hr = hr
        for target, attr, val in ((memory_tidy, "seat_info", lambda seat="": {"code": "abcde"}),
                                  (hr, "receipt_path", lambda code: self.td / "abcde.review.json")):
            q = mock.patch.object(target, attr, val)
            q.start()
            self.addCleanup(q.stop)

    def draft(self):
        return self.hr.collect(words=self.WORDS, commits="a1b2c3d 改 80%", now=1000.0)

    def test_every_line_must_be_classified(self):
        d = self.draft()
        self.assertIn("[1]", d)
        self.assertIn("[2]", d)
        with self.assertRaises(ValueError) as cm:
            self.hr.apply(d, writer=lambda *a, **k: {"uid": "x"})
        self.assertIn("1, 2", str(cm.exception))

    def test_apply_writes_note_and_receipt(self):
        d = self.draft().replace("交接也要更新人格和專案卡 ⇒ 歸類:", "交接也要更新人格和專案卡 ⇒ 歸類:做了 caf5ad68f")
        d = d.replace("把10%取消好了 ⇒ 歸類:", "把10%取消好了 ⇒ 歸類:做了 815d8cc77")
        notes = []
        rec = self.hr.apply(d, writer=lambda *a, **k: notes.append(a) or {"uid": "3rv01"})
        self.assertEqual(rec["lines"], 2)
        self.assertEqual(rec["by_kind"]["做了"], 2)
        self.assertEqual(rec["note"], "3rv01")
        self.assertNotIn("a1b2c3d", notes[0][2], "機器的提交列不進筆記")
        self.assertTrue(self.hr.done_this_round("abcde", 0))
        self.assertFalse(self.hr.done_this_round("abcde", 9e12), "舊回顧不算這一輪")

    def test_deleted_line_rejected_and_fullwidth_colon_ok(self):
        d = self.draft()
        d = d.replace("交接也要更新人格和專案卡 ⇒ 歸類:", "交接也要更新人格和專案卡 ⇒ 歸類:做了 caf5ad68f")
        gone = "\n".join(ln for ln in d.splitlines() if "[2]" not in ln)
        with self.assertRaises(ValueError) as cm:
            self.hr.apply(gone, writer=lambda *a, **k: {"uid": "x"})
        self.assertIn("1..2", str(cm.exception), "刪掉的句子不能當沒這句")
        ok = d.replace("把10%取消好了 ⇒ 歸類:", "把10%取消好了 ⇒ 歸類\uff1a做了\uff1a815d8cc77")   # 全形冒號
        self.assertEqual(self.hr.apply(ok, writer=lambda *a, **k: {"uid": "x"})["lines"], 2)

    def test_kind_without_detail_rejected(self):
        d = self.draft().replace("⇒ 歸類:", "⇒ 歸類:做了")
        with self.assertRaises(ValueError):
            self.hr.apply(d, writer=lambda *a, **k: {"uid": "x"})

    def test_render_hints_review_when_first8_done(self):
        items = [("x%d" % i, True, "") for i in range(8)] + [("9 回顧", False, "還沒做")]
        txt, done = hc.render(items)
        self.assertFalse(done)
        self.assertIn("aib handoff review collect", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
