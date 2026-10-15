#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""接手包渲染與 CTX 預算截斷。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(os.environ.get("AIB_SEAT_B_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(REPO / "src" / "identity"))
import handoff_pack as HP  # noqa: E402
import project_card as PC  # noqa: E402


def _fat_card():
    layers = PC.empty_four_layers()
    layers["constitution"]["red_lines"] = ["不可覆寫人格紅線"]
    layers["constitution"]["goals"] = ["目標" + str(i) for i in range(40)]
    layers["memory"]["entries"] = [
        {"text": ("記憶段落-%d-" % i) + ("詳" * 80)} for i in range(30)
    ]
    layers["work_state"]["now"] = "做頂行"
    layers["work_state"]["owners"] = ["@bcdef"]
    layers["work_state"]["next_step"] = "交件"
    layers["work_state"]["pending"] = ["待裁-%d" % i for i in range(20)]
    return PC.make_project_card(
        "proj:demo-handoff",
        title="接手示範",
        repo_fingerprints={"demo": "0123456789abcdef"},
        layers=layers,
    )


class HandoffPackArms(unittest.TestCase):
    def test_G_contains_uid_and_persona(self):
        card = _fat_card()
        text = HP.render_handoff_pack(
            card,
            persona_uid="persona:captain",
            persona_version="3",
            last_event_at="09-26 14:5x",
            ctx_budget_bytes=8000,
        )
        self.assertIn(HP.HEADER, text)
        self.assertIn("proj:demo-handoff", text)
        self.assertIn("persona:captain", text)
        self.assertIn("紅線", text)
        self.assertTrue(HP.handoff_mentions_project(text, "proj:demo-handoff"))

    def test_G_budget_truncates(self):
        card = _fat_card()
        text = HP.render_handoff_pack(card, ctx_budget_bytes=600)
        self.assertLessEqual(len(text.encode("utf-8")), 600)
        self.assertIn("CTX 預算", text)

    def test_R_invalid_card(self):
        with self.assertRaises(PC.ProjectCardError):
            HP.render_handoff_pack({"schema": "nope"})


class EntryAndKnife(unittest.TestCase):
    """到達性:seat_load.insert_handoff_pack 開席時走的就是
    project_pick → project_card.make_project_card → handoff_pack.build_handoff_for_seat。
    入口格在格內直接叫這三個生產函式(不經 _fat_card 這種 helper:閘看不見);還原刀逐格內聯。"""

    def test_E_build_handoff_for_seat_from_made_card(self):
        card = PC.make_project_card("proj:entry-0927", title="入口格", repo_fingerprints={"demo-repo": "0123abcd"})
        again = PC.validate_project_card(card)
        self.assertEqual(again["project_uid"], "proj:entry-0927")
        text = HP.build_handoff_for_seat(card, persona_uid="persona:x", last_event_at="09-27 04:2x")
        self.assertIn(HP.HEADER, text)
        self.assertIn("proj:entry-0927", text)

    def test_E_seat_load_py_apply_renders_handoff_pack(self):
        """handoff_pack 檔層的生產入口:沙盒真跑 `seat_load.py apply`(AIB_PROJECT_UID)⇒ 開場說明含接手包抬頭與專案 uid。"""
        tmp = Path(tempfile.mkdtemp(prefix="hpr-entry-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "aib").mkdir()
        env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp), "EZ2_AIB_HOME": str(tmp / "aib"),
               "CLAUDE_CONFIG_DIR": str(tmp / "claude"), "EZ2_CODEX_SESSIONS_ROOT": str(tmp / "codex-sessions"),
               "PYTHONDONTWRITEBYTECODE": "1", "AIB_PROJECT_UID": "proj:render-0927"}
        r = subprocess.run([sys.executable, str(REPO / "src" / "cockpit" / "seat_load.py"), "apply",
                            "--provider", "codex", "--from", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaa11111",
                            "--to", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbb22222", "--window", "@3",
                            "--aib-home", str(tmp / "aib")],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        out = json.loads(r.stdout)
        body = Path(out["deliver_file"]).read_text(encoding="utf-8")
        self.assertIn(HP.HEADER, body)
        self.assertIn("proj:render-0927", body)

    def test_K_knife_handoff_pack_build_returns_empty_reds_entry(self):
        """handoff_pack.py 副本:build_handoff_for_seat 一律回空 ⇒ 入口格必紅。"""
        if os.environ.get("AIB_HPR_KNIFE_CHILD") == "1":
            self.skipTest("子跑不再落刀")
        root = Path(tempfile.mkdtemp(prefix="hpr-knife-"))
        self.addCleanup(shutil.rmtree, root, True)
        shutil.copytree(REPO / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__"))
        layer = root / "src" / "identity" / "handoff_pack.py"
        text = layer.read_text(encoding="utf-8")
        anchor = '    if not project_card:\n        return ""\n    try:\n        return render_handoff_pack(\n'
        self.assertEqual(text.count(anchor), 1, "錨要恰好出現一次,否則刀沒落")
        layer.write_text(text.replace(anchor, '    if True:\n        return ""\n    try:\n        return render_handoff_pack(\n'),
                         encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(anchor), 0, "刀沒落")
        env = dict(os.environ, AIB_SEAT_B_ROOT=str(root), AIB_HPR_KNIFE_CHILD="1", PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                            "EntryAndKnife.test_E_build_handoff_for_seat_from_made_card",
                            "EntryAndKnife.test_E_seat_load_py_apply_renders_handoff_pack"],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=300)
        red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", r.stderr, re.M))
        self.assertIn("test_E_build_handoff_for_seat_from_made_card", red, r.stderr[-800:])
        self.assertIn("test_E_seat_load_py_apply_renders_handoff_pack", red, r.stderr[-800:])

    def test_K_knife_project_card_schema_reds_entry(self):
        """project_card.py 副本:make_project_card 寫錯 schema ⇒ validate 拒 ⇒ 入口格必紅。"""
        if os.environ.get("AIB_HPR_KNIFE_CHILD") == "1":
            self.skipTest("子跑不再落刀")
        root = Path(tempfile.mkdtemp(prefix="hpr-knife-"))
        self.addCleanup(shutil.rmtree, root, True)
        shutil.copytree(REPO / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__"))
        layer = root / "src" / "identity" / "project_card.py"
        text = layer.read_text(encoding="utf-8")
        anchor = '        "schema": "aib-project-card/v1",\n'
        self.assertEqual(text.count(anchor), 1, "錨要恰好出現一次,否則刀沒落")
        layer.write_text(text.replace(anchor, '        "schema": "aib-project-card/v0-knife",\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(anchor), 0, "刀沒落")
        env = dict(os.environ, AIB_SEAT_B_ROOT=str(root), AIB_HPR_KNIFE_CHILD="1", PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                            "EntryAndKnife.test_E_build_handoff_for_seat_from_made_card"],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=300)
        red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", r.stderr, re.M))
        self.assertIn("test_E_build_handoff_for_seat_from_made_card", red, r.stderr[-800:])


if __name__ == "__main__":
    unittest.main()
