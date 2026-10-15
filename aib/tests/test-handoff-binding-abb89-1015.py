#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib handoff 觸發綁定:交接一開始就確認有沒有人格/專案;缺了在第三步寫完交接檔時
擬綁定提案(與 aib bindme 調查同格式,`aib bindme apply <sid> --me <席碼> --accept` 可套)。不擋交接。
10-13 記憶整理第 2 包(維護者確認「同意自動綁」):開關開(AIB_AUTO_BIND / config/auto-bind.json,
預設開)⇒ 調查檔寫好後背景跑 `bindme auto`(找到就綁、沒有就 30 秒彈窗後建),交接檔與收據不等它;
沙盒預設把開關關掉(Sandbox setUp),要驗自動綁的格自己開。

沙盒借 test-handoff-flow-abb89-1015.py 的 Sandbox(暫存家目錄、席 zz9q/abcde、three_steps)。
"""
import hashlib
import importlib.util
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("handoff_flow", str(ROOT / "tests" / "test-handoff-flow-abb89-1015.py"))
_hc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_hc)
H = _hc.H
fill = _hc.fill


class HandoffBinding(_hc.Sandbox):
    def _identity(self, persona=True, project=True):
        """把席 abcde 綁上人格/專案(寫投影檔與席位檔,與正式流程讀的是同一批檔)。"""
        idn = self.home / "identity"
        idn.mkdir(parents=True, exist_ok=True)
        (idn / "cards.json").write_text(json.dumps({"cards": {"fbw36w": {"card_ref": "P10/AIB工具開發官"}}}),
                                        encoding="utf-8")
        disp = {"abcde": {"persona_uid": "fbw36w", "brain_binding_status": "bound"}} if persona else {}
        (idn / "seat-display.json").write_text(json.dumps({"display": disp}), encoding="utf-8")
        proj = self.home / "projects" / "proj:1111111111111111"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "card.json").write_text(json.dumps({"project_uid": "proj:1111111111111111",
                                                    "title": "AIB 指令與技能開發"}), encoding="utf-8")
        if project:
            rec = json.loads((self.seats / "w2.json").read_text(encoding="utf-8"))
            rec.update(project_uid="proj:1111111111111111", project_title="AIB 指令與技能開發")
            (self.seats / "w2.json").write_text(json.dumps(rec), encoding="utf-8")

    def _round(self, s3):
        draft = self.td / "draft.md"
        p = self.aib("collect", "--seat", "zz9q/abcde", "--out", str(draft))
        self.assertEqual(p.returncode, 0, p.stderr)
        draft.write_text(fill(draft.read_text(encoding="utf-8"), s1="建議 R1", s2="不知道 R2", s3=s3), encoding="utf-8")
        last = None
        for step in ("1", "2", "3"):
            last = self.aib("write", "--step", step, "--file", str(draft), "--seat", "zz9q/abcde")
            self.assertEqual(last.returncode, 0, last.stdout + last.stderr)
        rcpt = json.loads(H.receipt_path("abcde").read_text(encoding="utf-8"))
        return draft.read_text(encoding="utf-8"), last.stdout, rcpt

    def test_collect_shows_binding_gap_at_the_start(self):
        facts = H.collect(self.seat())
        self.assertEqual(facts["binding"]["problem"], "none")
        draft = H.render_draft(facts)
        self.assertIn("建議人格: <名稱>", draft, "交接一開始就要看到缺綁定與怎麼指定")

    def test_step3_appends_proposal_and_receipt_still_matches(self):
        self._identity(persona=False, project=False)
        draft, out, rcpt = self._round("重來 R3\n建議專案: AIB 指令與技能開發")
        body = Path(rcpt["file"]).read_text(encoding="utf-8")
        self.assertIn("## 綁定提案(", body)
        self.assertIn("aib bindme apply handoff-", body, "沒綁成(選單跳不出來)⇒ 要留「同意就跑」那一行")
        self.assertEqual(hashlib.sha256(Path(rcpt["file"]).read_bytes()).hexdigest(), rcpt["sha"],
                         "提案要在寫檔前接上,收據雜湊才會一致")
        self.assertIn("綁定提案:", out)
        sid = body.split("aib bindme apply ", 1)[1].split()[0]
        bm = H._bindme()
        rec = json.loads(Path(bm.survey_path(str(self.home), sid)).read_text(encoding="utf-8"))
        row = rec["proposals"][0]
        self.assertEqual(row["project"]["uid"], "proj:1111111111111111", row)
        self.assertEqual(row["persona"]["how"], "draft_new", "沒點名、也沒有延續檔 ⇒ 擬新卡,不綁")
        calls = []
        bm.apply(str(self.home), sid, "a", accept=True, runner=lambda a: calls.append(a) or (0, "ok"))
        self.assertEqual(calls, [], "沒有人格就不綁專案、新卡要人取名 ⇒ apply 什麼都不做")

    def test_countdown_runs_before_write_and_outcome_is_recorded(self):
        """倒數在寫交接檔之前跑,檔裡記的是實際結果;綁自己這一席不寄接手包。"""
        self._identity(persona=False, project=False)
        idn = self.home / "identity"
        calls = []
        def fake_apply(root, sid, actor, **kw):
            calls.append(kw)
            return [{"code": "abcde", "done": [{"kind": "persona", "rc": 0}, {"kind": "project", "rc": 0}],
                     "skipped": ["選單:30 秒沒選,照預設綁"]}]
        text = "主項目: x\n建議人格: AIB工具開發官\n建議專案: AIB 指令與技能開發\n"
        prop = H.binding_proposal(self.seat(), text, time.time(), menu_apply=fake_apply)
        self.assertTrue(calls and calls[0]["ask"] is True and calls[0]["deliver"] is False, calls)
        self.assertIn("人格、專案", prop["block"])
        self.assertIn("30 秒沒選,照預設綁", prop["block"])
        self.assertNotIn("同意就跑", prop["block"], "已經綁了就不再寫「同意就跑」(會誤導)")
        self.assertIn("它挑的既有卡", prop["block"], "判斷要印白話,不印 ask_existing 這種代碼")

    def test_auto_switch_on_spawns_background_bind(self):
        """暫存狀態根 + 開關開 ⇒ 真的走自動綁那條路:調查檔先寫、背景 bindme auto 起來、交接檔給 repick 那行。"""
        self._identity(persona=False, project=False)
        bm = H._bindme()
        spawned = []
        text = "主項目: x\n建議人格: AIB工具開發官\n建議專案: AIB 指令與技能開發\n"
        with mock.patch.dict(os.environ, {"AIB_AUTO_BIND": "1"}), \
                mock.patch.object(bm, "spawn_auto", side_effect=lambda root, sid, code: spawned.append((root, sid, code)) or 4242):
            prop = H.binding_proposal(self.seat(), text, time.time())
        self.assertEqual(len(spawned), 1, prop)
        root, sid, code = spawned[0]
        self.assertEqual(code, "abcde")
        self.assertTrue(Path(bm.survey_path(root, sid)).is_file(), "背景要讀的調查檔要先寫好")
        self.assertIn("aib bindme repick --me abcde", prop["block"])
        self.assertNotIn("--accept", prop["block"], "背景在綁時不給手動 apply(會重複綁)")

    def test_auto_switch_off_keeps_proposal_only(self):
        self._identity(persona=False, project=False)
        bm = H._bindme()
        with mock.patch.object(bm, "spawn_auto", side_effect=AssertionError("開關關了不准起背景")):
            prop = H.binding_proposal(self.seat(), "主項目: x\n", time.time())
        self.assertIn("自動綁定沒開", prop["block"])

    def test_bound_seat_gets_no_proposal(self):
        self._identity(persona=True, project=True)
        draft, out, rcpt = self._round("重來 R3")
        # 只看提案自己的段標題:機器事實段會印分支最新提交的標題,那裡可能剛好有「綁定提案」四個字(10-11 實踩)
        self.assertNotIn("## 綁定提案(", Path(rcpt["file"]).read_text(encoding="utf-8"))
        self.assertNotIn("綁定提案:", out)

    def test_binding_check_failure_never_blocks_handoff(self):
        """綁定檢查壞了:交接照樣寫完、收據照樣出,只是沒有提案。"""
        with mock.patch.object(H, "_bindme", side_effect=RuntimeError("boom")):
            facts = H.collect(self.seat())
            self.assertIn("unmeasured", facts["binding"])
            H.record_collect("abcde", facts)
            text = fill(H.render_draft(facts), s1="建議 R1", s2="不知道 R2", s3="重來 R3")
            for step in (1, 2, 3):
                res = H.write(self.seat(), text, step)
        self.assertTrue(res.get("receipt"))
        self.assertNotIn("binding_proposal", res)


if __name__ == "__main__":
    unittest.main()
