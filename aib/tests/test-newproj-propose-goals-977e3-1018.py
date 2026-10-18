#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib newproj --propose-goals:專案卡核准後補目標(10-18 977e3;以前核准過就只回「已核准」,目標欄永遠空)。
全在 tmp 沙盒;Touch ID 一律用替身。

P1 已核准的卡提目標 ⇒ rc 0;卡檔一個 byte 都不動;候選檔 approved=false、目標是新的、紅線沿用卡上生效的
P2 提完 --approve(Touch ID 過)⇒ 卡換上新目標、紅線不變、approved=true;按之前印出「這是修訂」
P3 卡已核准、沒有修訂在等(或提的跟生效的一模一樣)⇒ --approve 回已核准、不叫 Touch ID
P4 修訂的 Touch ID 沒過 ⇒ rc 2、卡上照舊是生效的那份(目標空、approved=true)
P5 目標空的 / 超過 12 條 / 一條超過 300 字 ⇒ rc 2、候選檔不動
P6 還沒核准過的卡提目標 ⇒ 紅線沿用候選草稿的;核准後目標跟紅線都在
P7 指令列 new_project.py --propose-goals <uid> --goal a --goal b ⇒ 印 JSON ok、goals=2、next 指 --approve
P8 新字串四語都有
P9 候選檔被改了紅線 / 刪了本人裁示再 --approve ⇒ 印出來的、視窗條數、寫進卡的,紅線與裁示都是卡上生效的(@977e3 子代理審)
P11 目標帶 ANSI 跳脫(\x1b[…)、控制字元、方向控制(U+202E)、零寬字 ⇒ 濾掉後才寫;濾完空的 ⇒ 整批拒(@abb89 審)
P10 等 Touch ID 期間又提了新目標 ⇒ 核的是按之前那份;新那份留在候選檔等下次核,不被蓋掉
刀(複本):K1 --approve 照舊只看卡已核准 ⇒ P2 紅;K2 不比目標、候選沒核准就當修訂 ⇒ P3 紅;
         K3 拿掉條數 / 字數檢查 ⇒ P5 紅;K4 修訂照收候選檔的紅線 ⇒ P9 紅;K5 核完照蓋候選檔 ⇒ P10 紅;
         K6 沒核准過的卡也拿卡當底 ⇒ P6 紅;K7 不濾字元 ⇒ P11 紅
"""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401 —— 基因/自動綁/Touch ID 預設關
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

ROOT = HERE.parent
THIS = pathlib.Path(__file__).name
PY = sys.executable
NP = ROOT / "tools" / "project" / "new_project.py"


def _load_new_project():
    spec = importlib.util.spec_from_file_location("new_project_propose", str(NP))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeIO:
    def __init__(self):
        self.out = []

    def isatty(self):
        return False

    def readline(self):
        return ""

    def write(self, s):
        self.out.append(s)

    def flush(self):
        pass


OK = {"ok": True, "why": "ok"}


class Propose(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-propose-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.tmp, "proj"))
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": self.home, "EZ2_AIB_LOCALE": "zh-TW",
                    "AIB_GENES": "0", "AIB_AUTO_BIND": "0", "AIB_TOUCHID": "0"}
        r = subprocess.run([PY, str(NP), "--entry", "newproj", "--title", "Demo"],
                           cwd=os.path.join(self.tmp, "proj"), env=self.env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.uid = json.loads(r.stdout.strip().splitlines()[-1])["project_uid"]
        self.root = pathlib.Path(self.home, ".ez2-aib")
        self.card = self.root / "projects" / self.uid / "card.json"
        self.cand = self.root / "projects" / self.uid / "candidates" / "constitution-draft.json"
        doc = json.loads(self.cand.read_text(encoding="utf-8"))
        doc["constitution"]["red_lines"] = ["never push to main"]
        self.cand.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        self._saved = dict(os.environ)
        os.environ.update({"HOME": self.home, "EZ2_AIB_STATE_ROOT": str(self.root), "EZ2_AIB_HOME": str(self.root),
                           "EZ2_AIB_LOCALE": "zh-TW"})
        self.np = _load_new_project()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _approve(self, touch=lambda reason: OK, out=None):
        asked = []
        rc = self.np.approve_constitution(self.root, self.uid, stdin=_FakeIO(), stdout=out or _FakeIO(),
                                          touchid=lambda reason: asked.append(reason) or touch(reason),
                                          touchid_available=lambda: True)
        return rc, asked

    def _const(self):
        return json.loads(self.card.read_text(encoding="utf-8"))["layers"]["constitution"]

    def _approved_card(self):
        rc, _ = self._approve()
        self.assertEqual(rc, 0)
        c = self._const()
        self.assertIs(c["approved"], True)
        self.assertEqual(c["goals"], [])
        return c

    def test_P1_propose_leaves_card_alone(self):
        self._approved_card()
        before = self.card.read_bytes()
        rc = self.np.propose_goals(self.root, self.uid, ["  goal   one ", "goal two", "goal two", ""])
        self.assertEqual(rc, 0)
        self.assertEqual(self.card.read_bytes(), before, "卡上生效的一個 byte 都不准動")
        cand = json.loads(self.cand.read_text(encoding="utf-8"))
        self.assertIs(cand["approved"], False)
        self.assertIs(cand["constitution"]["approved"], False)
        self.assertEqual(cand["constitution"]["goals"], ["goal one", "goal two"], "空白壓掉、重複去掉、空的丟掉")
        self.assertEqual(cand["constitution"]["red_lines"], ["never push to main"])

    def test_P2_approve_amendment(self):
        self._approved_card()
        self.assertEqual(self.np.propose_goals(self.root, self.uid, ["g1", "g2", "g3"]), 0)
        out = _FakeIO()
        rc, asked = self._approve(out=out)
        self.assertEqual(rc, 0)
        self.assertEqual(len(asked), 1)
        self.assertIn("目標修訂", asked[0])
        self.assertIn("目標改成 3 條", asked[0])
        self.assertIn("紅線 1 條照舊", asked[0])
        self.assertIn("這是修訂", "".join(out.out))
        c = self._const()
        self.assertEqual(c["goals"], ["g1", "g2", "g3"])
        self.assertEqual(c["red_lines"], ["never push to main"])
        self.assertIs(c["approved"], True)
        self.assertEqual(sum(1 for r in c["human_rulings"] if str(r.get("uid", "")).startswith("approve-")), 2)
        rc, asked = self._approve()
        self.assertEqual((rc, asked), (0, []), "核完再跑一次 = 已核准、不再叫視窗")

    def test_P3_nothing_pending(self):
        self._approved_card()
        rc, asked = self._approve()
        self.assertEqual((rc, asked), (0, []))
        self.assertEqual(self.np.propose_goals(self.root, self.uid, ["g1"]), 0)
        self.assertEqual(self._approve()[0], 0)
        self.assertEqual(self.np.propose_goals(self.root, self.uid, ["g1"]), 0)   # 跟生效的一模一樣
        before = self.card.read_bytes()
        rc, asked = self._approve()
        self.assertEqual((rc, asked), (0, []), "內容沒變不算修訂")
        self.assertEqual(self.card.read_bytes(), before)

    def test_P4_amendment_touchid_fail(self):
        self._approved_card()
        self.np.propose_goals(self.root, self.uid, ["g1"])
        before = self.card.read_bytes()
        rc, asked = self._approve(touch=lambda reason: {"ok": False, "why": "cancel"})
        self.assertEqual((rc, len(asked)), (2, 1))
        self.assertEqual(self.card.read_bytes(), before)
        self.assertIs(self._const()["approved"], True)
        self.assertEqual(self._const()["goals"], [])

    def test_P5_bad_goals(self):
        self._approved_card()
        before = self.cand.read_bytes()
        for goals in ([], ["  ", ""], ["g%d" % i for i in range(13)], ["字" * 301]):
            with self.subTest(n=len(goals)):
                self.assertEqual(self.np.propose_goals(self.root, self.uid, goals), 2)
                self.assertEqual(self.cand.read_bytes(), before)
        self.assertEqual(self.np.propose_goals(self.root, "proj:0000000000000000", ["g"]), 2)

    def test_P6_unapproved_card(self):
        self.assertEqual(self.np.propose_goals(self.root, self.uid, ["first goal"]), 0)
        cand = json.loads(self.cand.read_text(encoding="utf-8"))
        self.assertEqual(cand["constitution"]["red_lines"], ["never push to main"], "紅線從候選草稿來")
        rc, asked = self._approve()
        self.assertEqual((rc, len(asked)), (0, 1))
        c = self._const()
        self.assertEqual((c["goals"], c["red_lines"], c["approved"]), (["first goal"], ["never push to main"], True))

    def test_P9_candidate_tamper_ignored(self):
        self._approved_card()
        rulings = self._const()["human_rulings"]
        self.np.propose_goals(self.root, self.uid, ["g1"])
        doc = json.loads(self.cand.read_text(encoding="utf-8"))
        doc["constitution"]["red_lines"] = ["anything goes"]
        doc["constitution"]["human_rulings"] = []
        self.cand.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        out = _FakeIO()
        rc, asked = self._approve(out=out)
        self.assertEqual(rc, 0)
        shown = "".join(out.out)
        self.assertIn("never push to main", shown, "印出來的紅線是卡上生效的")
        self.assertNotIn("anything goes", shown)
        self.assertIn("紅線 1 條照舊", asked[0])
        c = self._const()
        self.assertEqual(c["goals"], ["g1"])
        self.assertEqual(c["red_lines"], ["never push to main"])
        self.assertEqual(c["human_rulings"][:len(rulings)], rulings, "本人裁示一條都不准少")

    def test_P10_newer_proposal_kept(self):
        self._approved_card()
        self.np.propose_goals(self.root, self.uid, ["first"])

        def touch(reason):
            self.assertEqual(self.np.propose_goals(self.root, self.uid, ["second"]), 0)
            return OK
        rc, _ = self._approve(touch=touch)
        self.assertEqual(rc, 0)
        self.assertEqual(self._const()["goals"], ["first"], "核的是按之前看到的那份")
        cand = json.loads(self.cand.read_text(encoding="utf-8"))
        self.assertIs(cand["approved"], False)
        self.assertEqual(cand["constitution"]["goals"], ["second"], "新提的不准被蓋掉")
        rc, asked = self._approve()
        self.assertEqual((rc, len(asked)), (0, 1))
        self.assertEqual(self._const()["goals"], ["second"])

    def test_P11_control_chars_scrubbed(self):
        self._approved_card()
        rc = self.np.propose_goals(self.root, self.uid, ["ok\x1b[2K\x1b[1Ahidden", "abc\u202edcba", "zero\u200bwidth\x07"])
        self.assertEqual(rc, 0)
        goals = json.loads(self.cand.read_text(encoding="utf-8"))["constitution"]["goals"]
        blob = "".join(goals)
        for bad in ("\x1b", "\u202e", "\u200b", "\x07"):
            self.assertNotIn(bad, blob, "控制 / 隱形字元要濾掉:%r" % bad)
        self.assertIn("hidden", blob, "字元濾掉、內容留著(看得到才核得到)")
        before = self.cand.read_bytes()
        for goals in (["\x1b\u202e\u200b"], ["good", "\x1b\x07"]):
            with self.subTest(goals=goals):
                self.assertEqual(self.np.propose_goals(self.root, self.uid, goals), 2, "濾完空的整批拒")
                self.assertEqual(self.cand.read_bytes(), before)
        out = _FakeIO()
        self._approve(out=out)
        self.assertNotIn("\x1b", "".join(out.out))

    def test_P7_cli(self):
        env = dict(self.env, EZ2_AIB_STATE_ROOT=str(self.root), EZ2_AIB_HOME=str(self.root))
        r = subprocess.run([PY, str(NP), "--propose-goals", self.uid, "--goal", "a", "--goal", "b"],
                           env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((out["ok"], out["goals"], out["approved"]), (True, 2, False))
        self.assertEqual(out["next"], "aib newproj --approve %s" % self.uid)

    def test_P8_i18n(self):
        keys = ("aib.newproj.help_propose_goals", "aib.newproj.help_goal", "aib.newproj.propose_bad_goals",
                "aib.newproj.propose_done", "aib.newproj.approve_amend_note",
                "aib.newproj.approve_touchid_reason_amend")
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8"))
            for k in keys:
                self.assertTrue(d.get(k), "%s 缺 %s" % (lang, k))


class Knife(unittest.TestCase):
    def _cut(self, needle, repl, test):
        with knife_copy(ROOT, "tools/project/new_project.py", needle, repl, tests=(THIS,)) as copy_root:
            r = run_guarded([PY, str(copy_root / "tests" / THIS), test], cwd=str(copy_root), timeout=300)
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, "刀下去 %s 應該紅:\n%s" % (test, out))
        self.assertIn("FAIL:", out, "要紅在斷言上(FAIL),不是 setUp 或匯入就倒:\n" + out)
        self.assertNotIn("ERROR:", out, "刀的複本裡出現 ERROR = 沒驗到要驗的東西:\n" + out)

    def test_K1_approve_ignores_amendment(self):
        self._cut("if amending and not _pending_amendment(card_const, cand):", "if amending:",
                  "Propose.test_P2_approve_amendment")

    def test_K2_no_content_compare(self):
        self._cut('and _goals_of(cc) != _goals_of(card_const)', "", "Propose.test_P3_nothing_pending")

    def test_K3_no_limits(self):
        self._cut("or len(clean) > GOAL_MAX or any(len(g) > GOAL_MAX_CHARS for g in clean):", ":",
                  "Propose.test_P5_bad_goals")


    def test_K4_amend_trusts_candidate_red_lines(self):
        self._cut('    if amending:                              # 修訂只換目標', '    if False:                              # 修訂只換目標',
                  "Propose.test_P9_candidate_tamper_ignored")

    def test_K5_clobber_newer_proposal(self):
        self._cut("if cand_now and not newer:", "if cand_now:", "Propose.test_P10_newer_proposal_kept")

    def test_K6_unapproved_uses_card(self):
        self._cut('base = card_const if approved or not isinstance(cand.get("constitution"), dict) else cand["constitution"]',
                  "base = card_const", "Propose.test_P6_unapproved_card")


    def test_K7_no_scrub(self):
        self._cut('scrubbed = [" ".join(TA.clean(g, 10 ** 6).split()) for g in given]',
                  'scrubbed = [" ".join(g.split()) for g in given]', "Propose.test_P11_control_chars_scrubbed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
