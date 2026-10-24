#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""缺 human_filter 不再靜默(維護者 10-16 13:0x 親裁;@abb89 派)。

原因:memory_tidy.human_words 要 import human_filter(aib/src/persona-collect/),開源倉從來沒有這組(只在私倉);
import 失敗被 except 吞掉回 [] ⇒ 真人原話永遠空、人格段永遠「這輪沒觀察到」,收據與巡檢都看不出是缺件。
維護者裁:human_filter 等匯出、不手搬;先做「不再靜默」。

全部用暫存 EZ2_AIB_HOME,不碰真的 ~/.ez2-aib;seat_info / 背景清理換成假的。
H1 缺 human_filter ⇒ human_words_status() 回 unavailable(缺 human_filter);human_words 照舊回 []
H2 有 human_filter(假模組)⇒ human_words_status() 回 ok
H3 collect + apply ⇒ 收據 human_words = unavailable(缺 human_filter);人格段 why 寫缺件,不再是「這輪沒觀察到」
H4 機器事實那行寫「讀不到(缺 human_filter)」,不是「沒有」(不讓模型以為真人沒說話)
H5 巡檢:收據寫 unavailable ⇒ fleet_patrol.human_words_unavailable_line 回一行提醒;收據 ok / 沒收據 ⇒ ""
H6 (expectedFailure,等匯出)開源包內 import human_filter 成功 —— 匯出補進來後這支會變「意外通過」,提醒拿掉標記
10-24 改(維護者:「必要不能跳過，一定要寫回對應的人格與第二大腦」):缺 human_filter 不再是「讀不到」,改用對話檔退路篩
   ⇒ 狀態 fallback(缺 human_filter,改用對話檔退路篩);H1/H3/H4/H5 照新規矩改,退路篩本身在 test-handoff-writeback-abb89-1024。
刀(同檔變體,不碰真樹):K1 human_words_status 退回永遠 ok ⇒ H1 紅;K2 收據不寫 human_words ⇒ H3 紅;
   K3 巡檢那行退回永遠空 ⇒ H5 紅
"""
import json
import os
import shutil
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
import memory_tidy  # noqa: E402
import fleet_patrol  # noqa: E402

TIDY = ROOT / "src" / "memory" / "memory_tidy.py"
PATROL = ROOT / "src" / "cockpit" / "fleet_patrol.py"
CTX = {"code": "abcde", "seat_uid": "u", "seat_id": "s1", "model": "claude-opus-5-5", "project_uid": ""}
_N = [0]


def variant(path, needle, replacement):
    """整個模組換一段重新載入(錨要恰好一處);不碰真樹。"""
    text = path.read_text(encoding="utf-8")
    assert text.count(needle) == 1, "刀的錨要恰好一處:%r" % needle[:60]
    _N[0] += 1
    mod = types.ModuleType("%s_knife_%d" % (path.stem, _N[0]))
    mod.__file__ = str(path)
    exec(compile(text.replace(needle, replacement), str(path), "exec"), mod.__dict__)
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hf-silent-")
        env = {"EZ2_AIB_HOME": self.tmp, "EZ2_AIB_STATE_ROOT": self.tmp}
        self._old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self._mods = {k: sys.modules.get(k) for k in ("human_filter",)}

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for k, v in self._mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def missing(self):
        sys.modules["human_filter"] = None                  # None ⇒ import 一定 ImportError(就算哪天匯出了也測得到缺件路徑)

    def present(self):
        fake = types.ModuleType("human_filter")
        fake.is_human = lambda *a, **k: True
        sys.modules["human_filter"] = fake


def check_h1(t, mod):
    t.missing()
    t.assertEqual(mod.human_words_status(), "fallback(缺 human_filter,改用對話檔退路篩)")
    t.assertEqual(mod.human_words(dict(CTX), ""), [])       # 沒有對話檔 ⇒ 空(不是例外)


def run_tidy(t, mod):
    """collect → 原樣 apply(席位記憶段可空、人格與筆記留空)⇒ 回收據。"""
    with mock.patch.object(mod, "seat_info", return_value=dict(CTX)), \
            mock.patch.object(mod, "fit_facts", return_value=[]), \
            mock.patch.object(mod, "fit_check", return_value={"status": "skipped", "why": "test"}), \
            mock.patch.object(mod.memory_archive, "spawn_background", return_value=None):
        draft = mod.collect()
        return mod.apply(draft, skip_context=True)


def check_h3(t, mod):
    t.missing()
    rec = run_tidy(t, mod)
    t.assertEqual(rec.get("human_words"), "fallback(缺 human_filter,改用對話檔退路篩)")
    on_disk = json.loads((Path(t.tmp) / "state" / "tidy" / "abcde.json").read_text(encoding="utf-8"))
    t.assertEqual(on_disk.get("human_words"), "fallback(缺 human_filter,改用對話檔退路篩)")
    t.assertEqual(on_disk.get("human_words_count"), 0)      # verify 第 2 項靠這個判「真人沒講話」


def write_receipt(t, value):
    d = Path(t.tmp) / "state" / "tidy"
    d.mkdir(parents=True, exist_ok=True)
    rec = {"status": "ok", "at": "2026-10-16T13:00:00+08:00", "seat": "abcde", "files": {}}
    if value is not None:
        rec["human_words"] = value
    (d / "abcde.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")


def check_h5(t, pmod):
    write_receipt(t, "unavailable(缺 human_filter)")
    line = pmod.human_words_unavailable_line("abcde")
    t.assertIn("human_filter", line)


class Behaviour(Base):
    def test_h1_missing_is_reported(self):
        check_h1(self, memory_tidy)

    def test_h2_present_is_ok(self):
        self.present()
        self.assertEqual(memory_tidy.human_words_status(), "ok")

    def test_h3_receipt_says_unavailable(self):
        check_h3(self, memory_tidy)

    def test_h4_facts_line_says_unavailable(self):
        self.missing()                                      # 缺件 ⇒ 退路篩是真的來源,空就是「沒有」
        self.assertTrue(memory_tidy.human_words_line([]).endswith("沒有"))
        with mock.patch.object(memory_tidy, "human_words_status", return_value=memory_tidy.HUMAN_WORDS_UNAVAILABLE):
            line = memory_tidy.human_words_line([])
        self.assertIn("讀不到", line)
        self.assertIn("human_filter", line)
        self.present()
        self.assertTrue(memory_tidy.human_words_line([]).endswith("沒有"))

    def test_h5_patrol_lists_it(self):
        check_h5(self, fleet_patrol)
        write_receipt(self, "fallback(缺 human_filter,改用對話檔退路篩)")
        self.assertIn("退路篩", fleet_patrol.human_words_unavailable_line("abcde"))
        write_receipt(self, "ok")
        self.assertEqual(fleet_patrol.human_words_unavailable_line("abcde"), "")
        write_receipt(self, None)
        self.assertEqual(fleet_patrol.human_words_unavailable_line("abcde"), "")
        self.assertEqual(fleet_patrol.human_words_unavailable_line("zzzzz"), "")
        self.assertEqual(fleet_patrol.human_words_unavailable_line(""), "")


class Export(unittest.TestCase):
    @unittest.expectedFailure
    def test_h6_oss_package_has_human_filter(self):
        """等私倉匯出(維護者 10-16 13:0x:不手搬)。補進來後這支「意外通過」⇒ 拿掉 expectedFailure。"""
        sys.modules.pop("human_filter", None)
        pc = ROOT / "src" / "persona-collect"
        sys.path.insert(0, str(pc))
        try:
            import human_filter  # noqa: F401
        finally:
            sys.path.remove(str(pc))


class Knives(Base):
    def test_k1_status_always_ok(self):
        mod = variant(TIDY, '    return "ok" if _human_filter() is not None else HUMAN_WORDS_FALLBACK\n',
                      '    return "ok"\n')
        with self.assertRaises(AssertionError):
            check_h1(self, mod)

    def test_k2_receipt_without_human_words(self):
        mod = variant(TIDY, '    rec["human_words"] = hw\n', '')
        with self.assertRaises(AssertionError):
            check_h3(self, mod)

    def test_k3_patrol_line_always_empty(self):
        mod = variant(PATROL, '    if not hw.startswith("unavailable"):\n',
                      '    if True:\n')
        with self.assertRaises(AssertionError):
            check_h5(self, mod)


if __name__ == "__main__":
    unittest.main(verbosity=2)
