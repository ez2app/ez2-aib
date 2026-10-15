#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""所有 AI 都免費開放(維護者 10-15 22:5x 裁「所有 AI 都免費開放」,不只 Claude;@abb89 派)。

原因:src/entitlement/providers.py 的 FREE_ALLOWED_PROVIDERS = {"claude"}(09-27 舊裁)⇒ 人格來源開關
(aib persona 來源|狀態)在免費態把其他家標「不可用(免費)」、`來源 codex 開` 拒開 rc 1「付費版可開」,
狀態列尾巴印「免費態：僅 claude 可開；另有 N 家付費版可開」;跟 README「免費接入各家 AI」對不上。
開席本來就沒擋(provider-launch.sh 不看資格),這裡只改來源開關這一處判斷與它的字。
另外這幾句原本寫死中文,英文介面也印中文 ⇒ 一併走四語字典 aib.persona_sources.*。

全部用暫存 EZ2_AIB_HOME(沒有授權檔 = 免費態),不碰真的 ~/.ez2-aib。
F1 免費態(沒授權檔):allowed_providers_for_entitlement 回全部 provider(含 codex、claudebot)
F2 免費態 `來源 codex 開` ⇒ ok、rc 0、寫進 sources.json;is_source_enabled 為真
F3 免費態狀態:每家都列;沒有「付費」「不可用」「僅 … 可開」;有「所有 AI 都免費」那一句
F4 single / all / free 三種資格給的可開清單都一樣(不再因資格少幾家)
F5 四語字典 aib.persona_sources.* 四份鍵相同、佔位符相同;英文介面的狀態與回覆沒有中日韓字
F6 FREE_ALLOWED_PROVIDERS 這個「免費只限某幾家」的集合不再存在(消費者不會再拿它擋)
刀(同檔變體,不碰真樹):K1 可開清單退回只限 claude ⇒ F1 紅;K2 set_source 退回拒開 ⇒ F2 紅;
   K3 狀態那句寫死中文 ⇒ F5 紅
"""
import json
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # = aib/
SRC_DIR = ROOT / "src"
SOURCES = SRC_DIR / "entitlement" / "sources.py"
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(SRC_DIR / "cockpit"))
import entitlement  # noqa: E402
from entitlement import providers as P  # noqa: E402
from entitlement import sources as S  # noqa: E402

LOCALES = ("en", "zh-TW", "ja", "ko")
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
PH = re.compile(r"\{[a-z_]+\}")
_N = [0]


def variant(needle, replacement):
    """sources.py 換一段,以 entitlement 子模組載入(相對 import 照常)。錨要恰好一處。"""
    text = SOURCES.read_text(encoding="utf-8")
    assert text.count(needle) == 1, "刀的錨要恰好一處:%r" % needle[:60]
    _N[0] += 1
    name = "entitlement.sources_knife_%d" % _N[0]
    mod = types.ModuleType(name)
    mod.__file__ = str(SOURCES)
    mod.__package__ = "entitlement"
    sys.modules[name] = mod
    exec(compile(text.replace(needle, replacement), str(SOURCES), "exec"), mod.__dict__)
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="free-all-")
        self.env = {"EZ2_AIB_HOME": self.tmp, "EZ2_AIB_STATE_ROOT": self.tmp, "EZ2_AIB_LOCALE": "zh-TW"}
        self._old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.all_p = list(P.list_providers())

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)


def check_f1(t, mod):
    e = entitlement.get_entitlement(t.env)
    t.assertEqual(e["tier"], "free")
    got = mod.allowed_providers_for_entitlement(e)
    t.assertEqual(got, t.all_p)
    t.assertIn("codex", got)
    t.assertIn("claudebot", got)


def check_f2(t, mod):
    ok, msg, rc = mod.set_source("codex", True, env=t.env)
    t.assertTrue(ok, msg)
    t.assertEqual(rc, 0)
    obj = json.loads((Path(t.tmp) / "persona" / "sources.json").read_text(encoding="utf-8"))
    t.assertIs(obj["enabled"].get("codex"), True)
    t.assertTrue(mod.is_source_enabled("codex", env=t.env))


def check_f5_en(t, mod):
    os.environ["EZ2_AIB_LOCALE"] = "en"
    env = dict(t.env, EZ2_AIB_LOCALE="en")
    out = mod.format_status(env)
    t.assertFalse(CJK.search(out), out)
    ok, msg, _rc = mod.set_source("codex", True, env=env)
    t.assertTrue(ok)
    t.assertFalse(CJK.search(msg), msg)
    _ok, msg2, _rc2 = mod.set_source("no-such-ai", True, env=env)
    t.assertFalse(CJK.search(msg2), msg2)


class Behaviour(Base):
    def test_f1_free_allows_every_provider(self):
        check_f1(self, S)

    def test_f2_free_can_turn_on_codex(self):
        check_f2(self, S)

    def test_f3_status_has_no_paid_wording(self):
        out = S.format_status(self.env)
        for p in self.all_p:
            self.assertIn("  %s: " % p, out)
        for bad in ("付費", "不可用", "可開；另有", "免費態：僅"):
            self.assertNotIn(bad, out)
        self.assertIn("所有 AI 都免費", out)

    def test_f4_every_tier_same_list(self):
        for e in ({"tier": "free"}, {"tier": "single", "providers": ["codex"]}, {"tier": "all"}):
            self.assertEqual(S.allowed_providers_for_entitlement(e), self.all_p, e)

    def test_f5_four_locales_and_english_output(self):
        dicts = {}
        for loc in LOCALES:
            d = json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            dicts[loc] = {k: v for k, v in d.items() if k.startswith("aib.persona_sources.")}
        self.assertTrue(dicts["zh-TW"], "四語字典沒有 aib.persona_sources.*")
        for loc in LOCALES:
            self.assertEqual(sorted(dicts[loc]), sorted(dicts["zh-TW"]), loc)
            for k, v in dicts[loc].items():
                self.assertEqual(sorted(PH.findall(v)), sorted(PH.findall(dicts["zh-TW"][k])), (loc, k))
        for k, v in dicts["en"].items():
            self.assertFalse(CJK.search(v), (k, v))
        check_f5_en(self, S)

    def test_f6_no_free_only_set(self):
        self.assertFalse(hasattr(P, "FREE_ALLOWED_PROVIDERS"))
        self.assertNotIn("FREE_ALLOWED_PROVIDERS", entitlement.__all__)


class Knives(Base):
    def test_k1_free_list_back_to_claude_only(self):
        mod = variant("    return list(all_p)\n", "    return [p for p in all_p if p == 'claude']\n")
        with self.assertRaises(AssertionError):
            check_f1(self, mod)

    def test_k2_set_source_refuses_again(self):
        mod = variant("    obj = read_sources_obj(env=env)\n    enabled_map",
                      "    if enabled and name != 'claude':\n        return False, 'paid', 1\n"
                      "    obj = read_sources_obj(env=env)\n    enabled_map")
        with self.assertRaises(AssertionError):
            check_f2(self, mod)

    def test_k3_status_hardcoded_chinese(self):
        mod = variant('    lines.append(_t("aib.persona_sources.all_free", FALLBACK_ALL_FREE, env=env))\n',
                      '    lines.append(FALLBACK_ALL_FREE)\n')
        with self.assertRaises(AssertionError):
            check_f5_en(self, mod)


if __name__ == "__main__":
    unittest.main(verbosity=2)
