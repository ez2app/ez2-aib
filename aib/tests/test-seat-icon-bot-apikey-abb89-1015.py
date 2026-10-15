#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""右欄組員圖示看引擎:橋接 bot 引擎戴 🤖、用 API 金鑰的引擎席戴 🔑(the maintainer 10-15:
「bot 應該有一個機器人頭的 icon、用金鑰的應該有一個 key 的」;角色名叫 bot 的 Opus 席不是 bot)。

量法:圖示表與 crew_mark 住在 pane-right-ai.sh 的內嵌 Python 裡,原樣抽出那幾段 exec(不另抄一份判準);
金鑰引擎集合與 providers.json auth=api_key 對齊(pane-right-ai 與 list_captains 兩處)。
"""
import ast
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PANE = ROOT / "src" / "cockpit" / "pane-right-ai.sh"
PROVIDERS = ROOT / "src" / "cockpit" / "providers.json"
LIST_CAPTAINS = ROOT / "src" / "cockpit" / "mouse" / "list_captains.py"


def _top_def(src, name):
    m = re.search(r"^def %s\(.*?(?=^\S)" % name, src, re.S | re.M)
    assert m, name
    return m.group(0)


def _assign(src, name):
    m = re.search(r"^%s = .*?$(?:\n[ \t]+.*?$)*" % name, src, re.M)
    assert m, name
    return m.group(0)


def _load():
    src = PANE.read_text(encoding="utf-8")
    code = "\n".join([_assign(src, "ROLE_ICONS"), _assign(src, "UNLISTED_ROLE_ICON"),
                      _assign(src, "AIBOT_PROVIDERS"), _assign(src, "AIBOT_MARK"),
                      _assign(src, "API_KEY_PROVIDERS"), _assign(src, "API_KEY_VICE_MARK"),
                      _assign(src, "API_KEY_MARK"),
                      _top_def(src, "role_family"), _top_def(src, "role_icon"), _top_def(src, "crew_mark")])
    ns = {}
    exec(compile(code, str(PANE), "exec"), ns)
    return ns


def _api_key_ids():
    d = json.loads(PROVIDERS.read_text(encoding="utf-8"))
    return {p["id"] for p in d["providers"] if p.get("auth") == "api_key"}


class SeatIcon(unittest.TestCase):
    def setUp(self):
        self.ns = _load()
        self.mark = lambda role, prov="claude", u=False, ip=False: self.ns["crew_mark"](role, prov, u, ip, "❓")

    def test_bridge_bot_engine_wears_robot_head(self):
        for prov in sorted(self.ns["AIBOT_PROVIDERS"]):
            self.assertEqual(self.mark("dev", prov), "\U0001F916", prov)

    def test_role_named_bot_on_claude_is_not_a_bot(self):
        self.assertEqual(self.mark("bot", "claude"), "\U0001F464", "角色叫 bot 的 Opus 席照角色表(未列 ⇒ 👤)")

    def test_api_key_engine_wears_key(self):
        for prov in sorted(_api_key_ids()):
            self.assertEqual(self.mark("dev", prov), "\U0001F511", prov)
            self.assertEqual(self.mark("bot", prov), "\U0001F511", "引擎排在角色圖示前:%s" % prov)

    def test_unassigned_still_wins_and_others_unchanged(self):
        self.assertEqual(self.mark("dev", "deepseek", u=True), "❓", "未編制仍最優先")
        self.assertEqual(self.mark("dev", "deepseek", u=True, ip=True), "\U0001F511", "開席中照引擎")
        self.assertEqual(self.mark("dev"), "\U0001F9DE")
        self.assertEqual(self.mark("boards"), "\U0001F464", "沒登記的角色仍是 👤")

    def test_api_key_sets_match_providers_json(self):
        ids = _api_key_ids()
        self.assertTrue(ids)
        self.assertEqual(set(self.ns["API_KEY_PROVIDERS"]), ids, "pane-right-ai 與 providers.json 分岔")
        lc = LIST_CAPTAINS.read_text(encoding="utf-8")
        m = re.search(r"^API_KEY_PROVIDERS = frozenset\((\(.*?\))\)", lc, re.M | re.S)
        self.assertEqual(set(ast.literal_eval(m.group(1))), ids, "list_captains 與 providers.json 分岔")

    def test_crew_loop_uses_crew_mark(self):
        src = PANE.read_text(encoding="utf-8")
        self.assertIn("_r[3]['mark'] = crew_mark(", src, "組員迴圈要走 crew_mark,不能另寫一份判準")


if __name__ == "__main__":
    unittest.main()
