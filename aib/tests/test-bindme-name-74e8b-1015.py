#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""bindme 自動綁不拿進度回報句當名字(10-14 Grok @25515 的人格被取成「能用 待審預設、主艙改名」;維護者 10-15 17:3x 派在開源版重做)。

原因:交接沒填建議名稱時,new_persona_name 退回「主項目」—— 那是交接第三段第一句的進度回報
(「能用:待審預設、主艙改名、精煉 Grok 接上,…」)—— 切 12 字就當人格名;專案退回主項目切 24 字;30 秒彈窗沒人按就照建。
參考:私有倉 fix/bindme-name-86ff0-1014 e56bda107(只拿來參考,這裡照開源版的碼重寫測試)。

全部用假的:彈窗、aib 指令(runner)、通知、決定卡、sleep。不跳真視窗、不綁真席。
N1 new_persona_name:不拿「主項目」;沒有名稱 ⇒ "";拿掉「能用:/不能用:/還不能:」這類回報句開頭;記憶整理「新建:<名稱>」放的「建議人格」照用;
   「新建」「沿用」這種關鍵字不是名字;撞名照舊加序號
N2 人格要新建但沒有建議名稱 ⇒ 不彈 30 秒建立窗、不建、persona how=needs_name、送事後決定卡;專案也不綁
N3 專案 unknown 且沒有建議專案名稱 ⇒ 不拿主項目當專案名、不 newproj、不彈窗、送決定卡
N4 專案 new 帶「能用:主機巡檢」⇒ 建「主機巡檢」(回報句開頭拿掉)
N5 記憶整理「新建:<名稱>」照樣能建:建議人格=名稱 ⇒ 彈窗逾時 ⇒ newrole --name <名稱>
N6 有「新人格名稱」照舊:逾時 ⇒ newrole --name <新人格名稱>
刀(同檔變體,不碰真樹):K1 new_persona_name 退回主項目 ⇒ N1 紅;K2 拿掉 needs_name 分支 ⇒ N2 紅;
   K3 專案 unknown 退回主項目 ⇒ N3 紅;K4 不拿掉回報句開頭 ⇒ N4 紅
"""
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # = aib/
SRC = ROOT / "src" / "identity" / "bindme.py"
sys.path.insert(0, str(ROOT / "src" / "identity"))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
import bindme  # noqa: E402

REPORT = "能用:待審預設、主艙改名、精煉 Grok 接上,剩一項等決定"
_N = [0]


def variant(needle, replacement):
    """原檔換一段,以新模組名載入(__file__ 指回原檔,不經 sys.modules 快取)。錨要恰好一處。"""
    text = SRC.read_text(encoding="utf-8")
    assert text.count(needle) == 1, "刀的錨要恰好一處:%r" % needle[:60]
    _N[0] += 1
    mod = types.ModuleType("bindme_name_knife_%d" % _N[0])
    mod.__file__ = str(SRC)
    exec(compile(text.replace(needle, replacement), str(SRC), "exec"), mod.__dict__)
    return mod


class Fakes:
    def __init__(self, dialog=()):
        self.dialog_answers = list(dialog)
        self.dialogs, self.runs, self.notes, self.cards = [], [], [], []

    def dialog(self, title, msg, buttons, default, cancel, timeout):
        self.dialogs.append((msg, buttons, default))
        return self.dialog_answers.pop(0) if self.dialog_answers else (None, "no_menu")

    def text(self, title, prompt, default, timeout):
        return default

    def choose(self, title, prompt, items, timeout):
        return None, "timeout"

    def menu(self, cabin, title, items, default, timeout=30):
        return None, "no_menu"

    def runner(self, args):
        self.runs.append(args)
        return 0, "ok"

    def notifier(self, row, names):
        self.notes.append(names)

    def sender(self, addr, body):
        self.cards.append(body)
        return "delivered"

    def kw(self):
        return dict(runner=self.runner, dialog=self.dialog, text=self.text, choose=self.choose, sleep=lambda s: None,
                    notifier=self.notifier, sender=self.sender, menu=self.menu)


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-bindme-name-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.root = str(self.td / "state")
        (self.td / "state" / "identity").mkdir(parents=True)
        (self.td / "state" / "identity" / "cards.json").write_text(json.dumps({"cards": {
            "aaaaaa": {"uid": "aaaaaa", "display_name": "Bot 接線員"},
            "bbbbbb": {"uid": "bbbbbb", "display_name": "主機艦長"},
        }}, ensure_ascii=False), encoding="utf-8")
        self.draft = self.td / "s-x-persona-draft.md"
        self.draft.write_text("# 草稿\n\n名稱:(待取名)\n\n你是 AIB 的 (待取名)。\n\n## 職責\nx\n## 紅線\n(待定)\n## 知識域\n(待定)\n",
                              encoding="utf-8")

    def row(self, persona, project, fields):
        return {"code": "25515", "addr": "931e/25515", "cabin": "931e", "provider": "grok",
                "fields": fields, "persona": persona, "project": project}

    def draft_new(self):
        return {"how": "draft_new", "draft_path": str(self.draft)}

    def apply(self, m, persona, project, fields, dialog=(("照這樣建立", "timeout"), ("照這樣建立", "timeout"))):
        f = Fakes(dialog=dialog)
        res = m.auto_apply(self.root, self.row(persona, project, fields), "root@25515/Grok", **f.kw())
        return f, res


class Names(Base):
    def s_names(self, m):
        name = m.new_persona_name
        assert name({"主項目": REPORT}, {}) == "", name({"主項目": REPORT}, {})
        assert name({}, {}) == ""
        assert name({"新人格名稱": "能用:主機巡檢官"}, {}) == "主機巡檢官"
        assert name({"新人格名稱": "不能用:x"}, {}) == "x"
        assert name({"建議人格": "選單自動選"}, {}) == "選單自動選"          # 記憶整理「新建:<名稱>」
        assert name({"建議人格": "新建", "主項目": REPORT}, {}) == ""
        assert name({"建議人格": "沿用", "主項目": REPORT}, {}) == ""
        assert name({"新人格名稱": "Bot 接線員"}, {"aaaaaa": "Bot 接線員"}) == "Bot 接線員-2"
        assert name({"新人格名稱": "a/b:c" * 5}, {}) == "a b ca b ca"

    def test_n1_names(self):
        self.s_names(bindme)

    def test_k1_knife_falls_back_to_report(self):
        m = variant('base = clean_name(fields.get("新人格名稱") or sug, 12)',
                                 'base = clean_name(fields.get("新人格名稱") or sug or fields.get("主項目"), 12)')   # 錨找不到 ⇒ 這裡就報錯,不算咬到
        with self.assertRaises(AssertionError):
            self.s_names(m)


class Auto(Base):
    def s_persona_needs_name(self, m):
        f, res = self.apply(m, self.draft_new(), {"how": "new", "title": "能用:主艙改名"}, {"主項目": REPORT})
        assert f.runs == [], f.runs
        assert f.dialogs == [], f.dialogs
        assert res["decision"]["persona"]["how"] == "needs_name", res["decision"]
        assert res["decision"]["card"], res["decision"]

    def test_n2_persona_without_name_not_created(self):
        self.s_persona_needs_name(bindme)

    def test_k2_knife_without_needs_name_branch(self):
        m = variant('    elif how == "draft_new" and not new_persona_name(row.get("fields") or {}, cards):',
                                              '    elif False:')   # 錨找不到 ⇒ 這裡就報錯,不算咬到
        with self.assertRaises(AssertionError):
            self.s_persona_needs_name(m)

    def s_project_unknown(self, m):
        f, res = self.apply(m, {"how": "keep"}, {"how": "unknown"}, {"主項目": REPORT})
        assert [r for r in f.runs if r[0] == "newproj"] == [], f.runs
        assert f.dialogs == [], f.dialogs
        assert res["decision"]["card"], res["decision"]

    def test_n3_project_unknown_not_created(self):
        self.s_project_unknown(bindme)

    def test_k3_knife_project_unknown_uses_report(self):
        m = variant('    elif phow in ("ambiguous", "unknown") or (phow == "new" and not clean_name(prj.get("title"), 24)):',
                                           '    elif phow == "ambiguous":')   # 錨找不到 ⇒ 這裡就報錯,不算咬到
        with self.assertRaises(AssertionError):
            self.s_project_unknown(m)

    def s_project_new_strips_head(self, m):
        f, _res = self.apply(m, {"how": "keep"}, {"how": "new", "title": "能用:主機巡檢"}, {"主項目": REPORT})
        assert f.runs and f.runs[0][:5] == ["newproj", "--seat", "931e/25515", "--title", "主機巡檢"], f.runs

    def test_n4_project_new_title_strips_report_head(self):
        self.s_project_new_strips_head(bindme)

    def test_k4_knife_keeps_report_head(self):
        m = variant('    s = REPORT_HEAD_RE.sub("", (s or "").strip())', '    s = (s or "").strip()')   # 錨找不到 ⇒ 這裡就報錯,不算咬到
        with self.assertRaises(AssertionError):
            self.s_project_new_strips_head(m)

    def test_n5_memory_tidy_new_name_still_creates(self):
        f, res = self.apply(bindme, self.draft_new(), {"how": "keep"}, {"主項目": REPORT, "建議人格": "選單自動選"})
        persona_runs = [r for r in f.runs if r[0] == "newrole"]
        self.assertTrue(persona_runs, (f.runs, res["decision"]))
        self.assertEqual(persona_runs[0][:3], ["newrole", "--name", "選單自動選"])

    def test_n6_given_new_name_still_creates(self):
        f, res = self.apply(bindme, self.draft_new(), {"how": "keep"}, {"主項目": REPORT, "新人格名稱": "主機巡檢官"})
        persona_runs = [r for r in f.runs if r[0] == "newrole"]
        self.assertTrue(persona_runs, (f.runs, res["decision"]))
        self.assertEqual(persona_runs[0][:3], ["newrole", "--name", "主機巡檢官"])


if __name__ == "__main__":
    unittest.main()
