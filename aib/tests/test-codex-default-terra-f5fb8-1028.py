#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Codex 席預設型號改 Terra(10-28 Andy:「所有的 gpt 改 GPT-6.1 Terra」)。
全在 tmp:模型清單是 tmp 裡的假 models_cache.json,不讀真 ~/.codex。
T1 清單有 Terra 也有 Luna ⇒ latest_default_slug 挑版本最大的 Terra(gpt-6.1-terra,不是 gpt-6-luna)。
T2 清單沒有 Terra ⇒ 退回最新 Luna;清單讀不到 ⇒ 空字串。
T3 藏起來的 Terra(visibility=hide)不算。
T4 previous_default_slug:Terra 退同家族上一版;最舊的 Terra 退最新 Luna;Luna 最舊 / 不在清單 / 非預設家族 ⇒ 空字串。
T5 登記簿後備型號(providers.json / rosters/default.json 的 codex)是 gpt-6.1-terra。
刀:K1 FAMILIES 改回只有 luna ⇒ T1 紅;K2 previous 不跨家族 ⇒ T4 紅。
"""
import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
COCKPIT = HERE.parent / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))

import codex_luna_model as M  # noqa: E402


def _cache(tmp: str, models: list) -> str:
    path = pathlib.Path(tmp) / "models_cache.json"
    path.write_text(json.dumps({"models": models}), encoding="utf-8")
    return str(path)


LISTED = [
    {"slug": "gpt-6.1-sol", "visibility": "list"},
    {"slug": "gpt-6-astra", "visibility": "list"},
    {"slug": "gpt-6.1-terra", "visibility": "list"},
    {"slug": "gpt-6-luna", "visibility": "list"},
    {"slug": "gpt-5.6-terra", "visibility": "list"},
    {"slug": "gpt-5.6-luna", "visibility": "list"},
]


class CodexDefaultTerra(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_t1_latest_prefers_terra(self):
        self.assertEqual(M.latest_default_slug(_cache(self.tmp, LISTED)), "gpt-6.1-terra")

    def test_t2_falls_back_to_luna(self):
        only_luna = [m for m in LISTED if not m["slug"].endswith("-terra")]
        self.assertEqual(M.latest_default_slug(_cache(self.tmp, only_luna)), "gpt-6-luna")
        self.assertEqual(M.latest_default_slug(str(pathlib.Path(self.tmp) / "missing.json")), "")

    def test_t3_hidden_terra_ignored(self):
        hidden = [dict(m, visibility="hide") if m["slug"].endswith("-terra") else m for m in LISTED]
        self.assertEqual(M.latest_default_slug(_cache(self.tmp, hidden)), "gpt-6-luna")

    def test_t4_previous_steps_down_then_across(self):
        cache = _cache(self.tmp, LISTED)
        self.assertEqual(M.previous_default_slug("gpt-6.1-terra", cache), "gpt-5.6-terra")
        self.assertEqual(M.previous_default_slug("gpt-5.6-terra", cache), "gpt-6-luna")
        self.assertEqual(M.previous_default_slug("gpt-6-luna", cache), "gpt-5.6-luna")
        self.assertEqual(M.previous_default_slug("gpt-5.6-luna", cache), "")
        self.assertEqual(M.previous_default_slug("gpt-9-terra", cache), "")
        self.assertEqual(M.previous_default_slug("gpt-6.1-sol", cache), "")

    def test_t5_registry_fallback_is_terra(self):
        providers = json.loads((COCKPIT / "providers.json").read_text(encoding="utf-8"))
        codex = [p for p in providers["providers"] if p.get("id") == "codex"]
        self.assertEqual(codex[0]["default_requested_model"], "gpt-6.1-terra")
        roster = json.loads((COCKPIT / "rosters" / "default.json").read_text(encoding="utf-8"))
        models = {json.dumps(r, ensure_ascii=False) for r in roster.get("seats", roster.get("roster", []))
                  if isinstance(r, dict) and r.get("provider") == "codex"}
        self.assertTrue(models)
        self.assertTrue(all('"gpt-6.1-terra"' in m for m in models))


if __name__ == "__main__":
    unittest.main()
