#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""主人格(真人分身)登記 + 任何一席靜默精煉進主人格(維護者 10-16 13:1x 親裁;@abb89 派)。

維護者原話:「真人分身就算沒有被綁定，也可以在判斷真人的情況下、靜默優化」。
(1) aib persona main [show | set <uid> | clear] 寫 identity/user-main-persona.json(原本只有程式讀、沒有入口寫);
    冠位大變動保護改認登記那張。
(2) 任何一席的 memory tidy:真人親打的話(human_filter 判準,轉信不算)⇒ 草稿多一段「主人格新增」,
    寫進主人格卡,不必有席綁它;靜默 = 只補不改、不改身分、不跳窗;快照 + writes.jsonl + 收據照寫(可查可退)。
(3) 主人格沒登記 / human_filter 缺件 / 本席就綁主人格 ⇒ 收據寫原因、跳過,不報錯。

全部用暫存 EZ2_AIB_HOME 與暫存 Vault;human_filter 用假的打樁;seat_info / 重釘指紋 / 背景清理換成假的。
M1 persona_main:沒登記 show 說沒登記;set 不在登記表 rc 1、不寫檔;set 71c97a 寫檔;clear 刪檔
M2 aib persona main show|set 經 aib 分派跑得到(不是「沒有這個功能」)
M3 is_crown:沒登記 ⇒ 71c97a;登記 ov3uoz ⇒ 認 ov3uoz、71c97a 不再算
M4 草稿:登記 + 本席綁別張 + 讀得到真人原話 ⇒ 有主人格段;沒登記 / 本席就綁主人格 / 缺 human_filter ⇒ 沒有
M5 主人格段填一條(證據是真人原話)⇒ 寫進主人格卡、本席卡不動、不跳窗;收據 main_persona ok + uid + added 1;
   writes.jsonl 記 71c97a;有快照
M6 主人格段寫身分 ⇒ 草稿驗不過(什麼都沒寫)
M7 證據不在真人原話裡 ⇒ 驗不過
M8 沒登記 / 缺 human_filter ⇒ 收據 main_persona skipped + 原因;不報錯
刀(同檔變體,不碰真樹):K1 main_persona_target 退回永遠不做 ⇒ M5 紅;K2 parse_draft 不認選用段 ⇒ M5 紅;
   K3 is_crown 退回「冠位常數或登記那張都算」⇒ M3 紅;K4 主人格段會改身分(不擋)⇒ M6 紅
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]          # = aib/
for _p in (ROOT / "src" / "cockpit", ROOT / "src" / "memory", ROOT / "src" / "identity", ROOT / "tests"):
    sys.path.insert(0, str(_p))
import aib_testlib  # noqa: E402,F401
import card_registry  # noqa: E402
import memory_tidy  # noqa: E402
import persona_main  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

TIDY = ROOT / "src" / "memory" / "memory_tidy.py"
MAIN, SEATCARD = "71c97a", "ov3uoz"
SAID = "我要第一句就講能不能用，細節放後面"
CARD = """---
uid: {uid}
type: persona
---
# {name}

## 身分
- 性別:未設定

## 寫作規則(12 維)
- 表達語氣:舊的一條〔舊〕

## 紅線
- 不准刪檔
"""
_N = [0]


def variant(needle, replacement):
    text = TIDY.read_text(encoding="utf-8")
    assert text.count(needle) == 1, "刀的錨要恰好一處:%r" % needle[:60]
    _N[0] += 1
    mod = types.ModuleType("memory_tidy_knife_%d" % _N[0])
    mod.__file__ = str(TIDY)
    exec(compile(text.replace(needle, replacement), str(TIDY), "exec"), mod.__dict__)
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="main-persona-"))
        env = {"EZ2_AIB_HOME": str(self.tmp), "EZ2_AIB_STATE_ROOT": str(self.tmp), "EZ2_AIB_LOCALE": "zh-TW"}
        self._old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self._hf = sys.modules.get("human_filter")
        self.vault = self.tmp / "vault"
        cards = {}
        for uid, name in ((MAIN, "AndyAI分身"), (SEATCARD, "心+指揮官")):
            rel = "p/%s.md" % uid
            (self.vault / "p").mkdir(parents=True, exist_ok=True)
            (self.vault / rel).write_text(CARD.format(uid=uid, name=name), encoding="utf-8")
            cards[uid] = {"card_path_rel": rel, "display_name": name}
        (self.tmp / "identity").mkdir(parents=True, exist_ok=True)
        (self.tmp / "identity" / "cards.json").write_text(json.dumps({"cards": cards}, ensure_ascii=False), encoding="utf-8")
        self.ctx = {"code": "abcde", "seat_uid": "u", "seat_id": "s1", "model": "claude-opus-5-5",
                    "project_uid": "", "persona_uid": SEATCARD}
        self.hf_present()

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if self._hf is None:
            sys.modules.pop("human_filter", None)
        else:
            sys.modules["human_filter"] = self._hf
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hf_present(self):
        fake = types.ModuleType("human_filter")
        fake.is_human = lambda *a, **k: (True, {})
        sys.modules["human_filter"] = fake

    def hf_missing(self):
        sys.modules["human_filter"] = None

    def register(self, uid=MAIN):
        ok, _name = persona_main.set_main(uid)
        self.assertTrue(ok)

    def card(self, uid):
        return (self.vault / "p" / ("%s.md" % uid)).read_text(encoding="utf-8")

    def patches(self, mod):
        return [mock.patch.object(mod, "seat_info", return_value=dict(self.ctx)),
                mock.patch.object(mod, "human_words", return_value=[("2026-10-16 13:10", SAID)]),
                mock.patch.object(mod, "fit_facts", return_value=[]),
                mock.patch.object(mod, "fit_check", return_value={"status": "skipped", "why": "test"}),
                mock.patch.object(mod, "refingerprint", return_value=[]),
                mock.patch.object(mod, "ask_big_change", side_effect=AssertionError("靜默精煉不准跳窗")),
                mock.patch.object(mod.memory_archive, "spawn_background", return_value=None),
                mock.patch.object(card_registry, "resolve_vault", return_value=str(self.vault))]

    def tidy(self, mod, main_text=None, persona_text=None):
        ps = self.patches(mod)
        for p in ps:
            p.start()
        try:
            draft = mod.collect()
            if main_text is not None:
                draft = fill(draft, "main", main_text)
            if persona_text is not None:
                draft = fill(draft, "persona", persona_text)
            return draft, mod.apply(draft, skip_context=True)
        finally:
            for p in reversed(ps):
                p.stop()

    def draft(self, mod=memory_tidy):
        ps = self.patches(mod)
        for p in ps:
            p.start()
        try:
            return mod.collect()
        finally:
            for p in reversed(ps):
                p.stop()


def fill(draft, sec, text):
    mark = "<!-- tidy:%s -->" % sec
    assert mark in draft, "草稿沒有 %s 段" % sec
    head, rest = draft.split(mark, 1)
    return head + mark + "\n" + text + "\n" + rest


def check_m3(t, mod):
    t.register(SEATCARD)
    t.assertTrue(mod.is_crown(SEATCARD))
    t.assertFalse(mod.is_crown(MAIN))


def check_m5(t, mod):
    t.register()
    seat_before = t.card(SEATCARD)
    _d, rec = t.tidy(mod, main_text="- 表達語氣:結論先講〔證據:「%s」〕" % SAID)
    mp = rec["files"]["main_persona"]
    t.assertEqual(mp["status"], "ok", mp)
    t.assertEqual(mp["uid"], MAIN)
    t.assertEqual(mp["added"], 1)
    t.assertIn("結論先講", t.card(MAIN))
    t.assertIn("舊的一條", t.card(MAIN))                 # 只補不改
    t.assertEqual(t.card(SEATCARD), seat_before)
    writes = (t.tmp / "state" / "tidy" / "writes.jsonl").read_text(encoding="utf-8")
    t.assertIn('"uid": "%s"' % MAIN, writes)


def check_m6(t, mod):
    t.register()
    before = t.card(MAIN)
    with t.assertRaises(memory_tidy.TidyError if mod is memory_tidy else mod.TidyError):
        t.tidy(mod, main_text="身分:性別=男〔證據:「%s」〕" % SAID)
    t.assertEqual(t.card(MAIN), before)


class Behaviour(Base):
    def test_m1_persona_main_cli(self):
        self.assertEqual(persona_main.get_main(), "")
        self.assertEqual(persona_main.main(["set", "nosuch"]), 1)
        self.assertFalse(persona_main.main_path().exists())
        self.assertEqual(persona_main.main(["set", MAIN]), 0)
        d = json.loads(persona_main.main_path().read_text(encoding="utf-8"))
        self.assertEqual(d["persona_uid"], MAIN)
        self.assertEqual(memory_tidy.main_persona_uid(), MAIN)
        self.assertEqual(persona_main.main(["clear"]), 0)
        self.assertFalse(persona_main.main_path().exists())
        self.assertEqual(persona_main.main(["bogus", "x", "y"]), 2)

    def test_m2_dispatch(self):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.tmp), "LANG": "en_US.UTF-8",
               "EZ2_AIB_HOME": str(self.tmp), "EZ2_AIB_STATE_ROOT": str(self.tmp), "EZ2_AIB_LOCALE": "zh-TW"}
        aib = str(ROOT / "src" / "cockpit" / "aib")
        r = run_guarded(["bash", aib, "persona", "main", "set", MAIN], timeout=60, env=env, stdin=subprocess.DEVNULL)
        self.assertIsNone(r.tripped)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = run_guarded(["bash", aib, "persona", "main", "show"], timeout=60, env=env, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AndyAI分身", r.stdout)
        self.assertIn(MAIN, r.stdout)

    def test_m3_crown_follows_registration(self):
        self.assertTrue(memory_tidy.is_crown(MAIN))           # 沒登記 ⇒ 冠位常數
        check_m3(self, memory_tidy)

    def test_m4_draft_section_only_when_applicable(self):
        self.assertNotIn("<!-- tidy:main -->", self.draft())    # 沒登記
        self.register()
        self.assertIn("<!-- tidy:main -->", self.draft())
        self.hf_missing()
        self.assertNotIn("<!-- tidy:main -->", self.draft())    # 缺 human_filter
        self.hf_present()
        self.ctx["persona_uid"] = MAIN
        self.assertNotIn("<!-- tidy:main -->", self.draft())    # 本席就綁主人格

    def test_m5_silent_distill_into_main(self):
        check_m5(self, memory_tidy)
        snaps = [p for p in self.tmp.rglob("*") if p.is_file() and MAIN in p.name and p.parent.name != "p"]
        self.assertTrue(snaps, "主人格卡寫之前要有快照(可退)")

    def test_m6_main_section_no_identity(self):
        check_m6(self, memory_tidy)

    def test_m7_evidence_must_be_human_words(self):
        self.register()
        with self.assertRaises(memory_tidy.TidyError):
            self.tidy(memory_tidy, main_text="- 表達語氣:結論先講〔證據:「這句真人沒有說過喔喔喔」〕")

    def test_m8_skips_with_reason(self):
        _d, rec = self.tidy(memory_tidy)
        self.assertEqual(rec["files"]["main_persona"]["status"], "skipped")
        self.assertIn("沒登記", rec["files"]["main_persona"]["why"])
        self.register()
        self.hf_missing()
        _d, rec = self.tidy(memory_tidy)
        self.assertEqual(rec["files"]["main_persona"]["status"], "skipped")
        self.assertIn("human_filter", rec["files"]["main_persona"]["why"])


class Knives(Base):
    def test_k1_target_never(self):
        mod = variant('    pc = persona_card({"persona_uid": uid})\n    if not pc:\n',
                      '    pc = None\n    if not pc:\n')
        with self.assertRaises(Exception):
            check_m5(self, mod)

    def test_k2_parse_draft_rejects_optional(self):
        mod = variant('    if sorted(n for n in names if n not in OPTIONAL_SECTIONS) != sorted(SECTIONS)',
                      '    if sorted(names) != sorted(SECTIONS)')
        with self.assertRaises(Exception):
            check_m5(self, mod)

    def test_k3_crown_old_rule(self):
        mod = variant('    return uid == (main_persona_uid() or CROWN_PERSONA)\n',
                      '    return uid == CROWN_PERSONA or (bool(main_persona_uid()) and uid == main_persona_uid())\n')
        with self.assertRaises(AssertionError):
            check_m3(self, mod)

    def test_k4_identity_allowed(self):
        mod = variant('    if mident:\n        errs.append("主人格那段不寫身分(只補 12 維)")\n', '')
        with self.assertRaises(AssertionError):
            check_m6(self, mod)


if __name__ == "__main__":
    unittest.main(verbosity=2)
