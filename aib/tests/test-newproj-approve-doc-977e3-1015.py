#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib newproj --approve 的補缺(10-15 @977e3,疊在 C11 上)。全在 tmp 沙盒:不碰真 ~/.ez2-aib。

A1  核准後卡上有人看得懂的時間 approved_at(+08:00 形,跟舊卡同形),human_rulings 多一筆核准紀錄、原有裁定保留。
A2  技能說明講清楚怎麼核准:本人在自己的終端機跑 aib newproj --approve <uid>;AI 不自己跑、不手改 approved。
"""
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401 —— 基因/自動綁預設關

ROOT = HERE.parent
PY = sys.executable
BIN_AIB = ROOT.parent / "bin" / "aib"
SKILL = ROOT / "skills" / "aib-newproj" / "SKILL.md"


def _load_new_project():
    spec = importlib.util.spec_from_file_location("new_project_a1", str(ROOT / "tools" / "project" / "new_project.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeTTY:
    def __init__(self, text=""):
        self.text, self.out = text, []

    def isatty(self):
        return True

    def readline(self):
        return self.text

    def write(self, s):
        self.out.append(s)

    def flush(self):
        pass


class A1ApprovedAtAndRuling(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-a1-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.tmp, "proj"))
        env = {"PATH": os.environ.get("PATH", ""), "HOME": self.home, "EZ2_AIB_LOCALE": "zh-TW",
               "AIB_GENES": "0", "AIB_AUTO_BIND": "0"}
        r = subprocess.run([PY, str(BIN_AIB), "newproj", "--title", "Demo"], cwd=os.path.join(self.tmp, "proj"),
                           env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.uid = json.loads(r.stdout.strip().splitlines()[-1])["project_uid"]
        self.root = pathlib.Path(self.home, ".ez2-aib")
        self.card = self.root / "projects" / self.uid / "card.json"
        cand = self.root / "projects" / self.uid / "candidates" / "constitution-draft.json"
        doc = json.loads(cand.read_text(encoding="utf-8"))
        doc["constitution"]["goals"] = ["ship v1"]
        doc["constitution"]["human_rulings"] = [{"uid": "r1", "text": "先做 A 再做 B", "at": "2026-10-15T10:00:00+08:00"}]
        cand.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        self._saved = dict(os.environ)
        os.environ.update({"HOME": self.home, "EZ2_AIB_STATE_ROOT": str(self.root), "EZ2_AIB_HOME": str(self.root),
                           "EZ2_AIB_LOCALE": "zh-TW"})

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_approved_at_and_ruling(self):
        np = _load_new_project()
        rc = np.approve_constitution(self.root, self.uid, human_check=lambda: (True, "tester", []),
                                     stdin=_FakeTTY(self.uid + "\n"), stdout=_FakeTTY())
        self.assertEqual(rc, 0)
        card = json.loads(self.card.read_text(encoding="utf-8"))
        const = card["layers"]["constitution"]
        self.assertRegex(const.get("approved_at", ""), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")
        rulings = const["human_rulings"]
        self.assertEqual(rulings[0]["uid"], "r1", "原有裁定要留著")
        self.assertTrue(rulings[-1]["uid"].startswith("approve-"))
        self.assertIn("tester", rulings[-1]["text"])
        self.assertEqual(rulings[-1]["at"], const["approved_at"])
        np.PC.validate_project_card(card)
        # 再核准一次:已核准 ⇒ 什麼都不改,不會多一筆核准紀錄
        rc = np.approve_constitution(self.root, self.uid, human_check=lambda: (True, "tester", []),
                                     stdin=_FakeTTY(self.uid + "\n"), stdout=_FakeTTY())
        self.assertEqual(rc, 0)
        again = json.loads(self.card.read_text(encoding="utf-8"))["layers"]["constitution"]["human_rulings"]
        self.assertEqual(len(again), len(rulings))


class A2SkillTellsHowToApprove(unittest.TestCase):
    def test_skill_explains_approve(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("aib newproj --approve <專案 uid>", text)
        self.assertIn("自己的終端機", text)
        self.assertRegex(text, r"不要自己跑 `aib newproj --approve`")


if __name__ == "__main__":
    unittest.main(verbosity=2)
