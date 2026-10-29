#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib newproj --approve 全文確認窗 + 內容碼(10-28 @aeb40 TID-1b;來源 @b5799:艦長室按鈕叫 --approve,不在終端機)。
威脅模型照實寫:全文窗只防按錯(同帳號 AI 點得到 UI);擋人的是下一步的 Touch ID,
Touch ID 字裡的條數 + 修訂增減 + 內容碼讓本人跟全文窗對得上。卡檔、候選檔都是本機沒簽章的檔。全在 tmp 沙盒,視窗全用替身。

F1 第一次核准:全文窗拿到目標 / 紅線全文(長的那條不截)、標「第一次核准」;全文窗、Touch ID 字、JSON 是同一個內容碼
F2 修訂:全文窗列與生效版的差別([+] 新增、[-] 拿掉、[~] 改字);Touch ID 字帶「修訂 +a −b」
F3 全文窗取消 / 沒勾 / 逾時 / 叫不出 ⇒ 不彈 Touch ID、卡與帳本不動;代碼 declined / timeout / unavailable
F4 按指紋期間目標被改(內容碼對不上)⇒ content_changed、卡與帳本不動
F5 Touch ID 取消 / 逾時 / 沒有生物辨識 ⇒ declined / timeout / unavailable;沒有 Touch ID 路 ⇒ unavailable
F6 非終端呼叫:不跑終端 attest(不退回終端那條)
F7 Touch ID 字放不下 ⇒ reason_refused,全文窗也不彈(不截)
F8 預設全文窗在 AIB_TOUCHID=0 ⇒ 不叫 osascript、回 unavailable
F9 全文窗 + 指紋同一把整台鎖(全文窗開著時別人拿不到鎖)
F10 全文窗腳本:可捲動唯讀文字框、全文經 json.dumps 放進去;Mac 上 osacompile 編得過
F11 CLI:非終端、沒有 Touch ID 路 ⇒ stdout 最後一行 JSON why=unavailable
F12 最壞情況(標題 40 字、兩位數條數)四語都放得進 Touch ID 上限
F13 顯示的 = 寫進去的:目標夾零寬 / 方向控制、目標寫成 dict、第一次核准的紅線夾控制字 ⇒ content_unclean,不彈窗、卡不動(#409 R1 Medium)
F14 修訂:等指紋期間卡上生效的目標被改 ⇒ content_changed(差別是對著生效版列的;R1 L3)
F15 全文窗腳本一次替換:全文裡有 __NONCE__ / __GO__ 也只是字(R1 L1)
F16 指紋過了但量不到登入名 ⇒ failed,不說成本人取消(R1 L2)
刀(複本):K1 拿掉內容碼比對 ⇒ F4 紅;K2 拿掉修訂差別(改字)⇒ F2 紅;K3 全文窗沒過照樣彈指紋 ⇒ F3 紅;
         K4 Touch ID 字不帶內容碼 ⇒ F1 紅;K5 拿掉 content_unclean ⇒ F13 紅;K6 鎖內不比生效目標 ⇒ F14 紅;
         K7 腳本改回依序替換 ⇒ F15 紅
"""
import fcntl
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
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401 —— 基因/自動綁/Touch ID 預設關
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

ROOT = HERE.parent
THIS = pathlib.Path(__file__).name
PY = sys.executable
NP = ROOT / "tools" / "project" / "new_project.py"
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
import approve_gate as AG  # noqa: E402
import consent  # noqa: E402

OK = {"ok": True, "why": "ok"}
SHA_RE = re.compile(r"; constitution sha256=([0-9a-f]{64})")
LONG_GOAL = "長目標" + "很長的一段說明" * 38          # 約 270 字:Touch ID 放不下,全文窗要完整


def _load_new_project():
    spec = importlib.util.spec_from_file_location("new_project_fulltext", str(NP))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod         # approve_gate 認「sys.modules 裡那支模組」才給登記的類別(10-29 TID-2a)
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

    def text(self):
        return "".join(self.out)

    def last_json(self):
        return json.loads(self.text().strip().splitlines()[-1])


class Full(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-apfull-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.tmp, "proj"))
        env = {"PATH": os.environ.get("PATH", ""), "HOME": self.home, "EZ2_AIB_LOCALE": "zh-TW",
               "AIB_GENES": "0", "AIB_AUTO_BIND": "0", "AIB_TOUCHID": "0"}
        self.env = env
        r = subprocess.run([PY, str(NP), "--entry", "newproj", "--title", "Demo"],
                           cwd=os.path.join(self.tmp, "proj"), env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.uid = json.loads(r.stdout.strip().splitlines()[-1])["project_uid"]
        self.root = pathlib.Path(self.home, ".ez2-aib")
        self.card = self.root / "projects" / self.uid / "card.json"
        self.cand = self.root / "projects" / self.uid / "candidates" / "constitution-draft.json"
        self._set_cand(["a", "b", LONG_GOAL], ["never push to main"])
        self._saved = dict(os.environ)
        os.environ.update({"HOME": self.home, "EZ2_AIB_STATE_ROOT": str(self.root), "EZ2_AIB_HOME": str(self.root),
                           "EZ2_AIB_LOCALE": "zh-TW"})
        self.np = _load_new_project()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _set_cand(self, goals, red=None):
        doc = json.loads(self.cand.read_text(encoding="utf-8"))
        doc["constitution"]["goals"] = goals
        if red is not None:
            doc["constitution"]["red_lines"] = red
        self.cand.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    def _approve(self, touch=lambda r: OK, full=lambda *a: OK, available=True, human_check=None):
        asked, shown, out = [], [], _FakeIO()
        rc = self.np.approve_constitution(
            self.root, self.uid, stdin=_FakeIO(), stdout=out, human_check=human_check,
            touchid=lambda r: asked.append(r) or touch(r), touchid_available=lambda: available,
            fulltext=lambda title, intro, body, check: shown.append((title, intro, body, check)) or full(title, intro, body, check))
        return rc, asked, shown, out

    def _const(self):
        return json.loads(self.card.read_text(encoding="utf-8"))["layers"]["constitution"]

    def _ledger_shas(self):
        text = "".join(p.read_text(encoding="utf-8") for p in self.root.rglob("*.jsonl") if "ledger" in str(p))
        return SHA_RE.findall(text)

    def _unchanged(self, before, shas):
        self.assertEqual(self.card.read_bytes(), before, "卡不動")
        self.assertEqual(self._ledger_shas(), shas, "帳本不多記")

    def test_F1_first_approval_full_text_and_code(self):
        rc, asked, shown, out = self._approve()
        self.assertEqual(rc, 0, out.text())
        self.assertEqual((len(shown), len(asked)), (1, 1))
        title, intro, body, check = shown[0]
        for part in ("a", "b", LONG_GOAL, "never push to main", "第一次核准"):
            self.assertIn(part, body)
        res = out.last_json()
        code = res["content_code"]
        self.assertEqual((res["ok"], res["why"], res["approved"], res["project_uid"]), (True, None, True, self.uid))
        self.assertRegex(code, r"^[0-9a-f]{8}$")
        self.assertEqual(code, self._ledger_shas()[0][:8], "內容碼 = 帳本那份雜湊的前 8 碼")
        self.assertIn(code, intro)
        self.assertIn(code, body)
        self.assertIn("內容碼 " + code, asked[0])
        self.assertIn("目標 3 條", asked[0])
        self.assertIn("紅線 1 條", asked[0])
        self.assertIn("第一次核准", asked[0])
        self.assertNotIn(LONG_GOAL, asked[0], "Touch ID 只放摘要,全文在全文窗")
        self.assertLessEqual(len(asked[0]), AG.LA_MAX)
        self.assertEqual(asked[0].category, "project_ruling")
        self.assertTrue(asked[0].high_risk)

    def test_F2_amend_lists_diff(self):
        self._set_cand(["a", "b"])
        self.assertEqual(self._approve()[0], 0)
        self.assertEqual(self.np.propose_goals(self.root, self.uid, ["a", "B2", "c"]), 0)
        rc, asked, shown, out = self._approve()
        self.assertEqual(rc, 0, out.text())
        body = shown[0][2]
        self.assertIn("這是修訂", body)
        self.assertIn("    [~] b → B2", body, "改字列成一條")
        self.assertIn("    [+] c", body)
        self.assertNotIn("    [-] b", body)
        self.assertNotIn("    [+] B2", body)
        self.assertIn("修訂 +2 −1", asked[0])
        self.assertIn("目標 3 條", asked[0])
        self.assertIn(out.last_json()["content_code"], asked[0])

    def test_F3_fulltext_not_passed(self):
        before, shas = self.card.read_bytes(), self._ledger_shas()
        for why, code in (("cancel", "declined"), ("not_checked", "declined"), ("timeout", "timeout"),
                          ("unavailable", "unavailable"), ("osascript_failed", "unavailable"), ("unreadable", "unavailable"),
                          ("nonce_mismatch", "failed")):
            with self.subTest(why=why):
                rc, asked, shown, out = self._approve(full=lambda *a, w=why: {"ok": False, "why": w})
                self.assertEqual(rc, 2)
                self.assertEqual(len(shown), 1)
                self.assertEqual(asked, [], "全文窗沒過就不彈指紋")
                self.assertEqual(out.last_json()["why"], code)
                self._unchanged(before, shas)
        rc, asked, _, out = self._approve(full=lambda *a: "yes")
        self.assertEqual((rc, asked, out.last_json()["why"]), (2, [], "declined"), "回的不是 {ok: True} 就不算")
        self._unchanged(before, shas)

    def test_F4_content_changed_during_touchid(self):
        before, shas = self.card.read_bytes(), self._ledger_shas()

        def touch(r):
            self._set_cand(["a", "b", "sneaky"])          # 看完全文之後、寫卡之前被改
            return OK
        rc, asked, _, out = self._approve(touch=touch)
        self.assertEqual(rc, 2)
        self.assertEqual(out.last_json()["why"], "content_changed")
        self.assertIs(self._const()["approved"], False)
        self._unchanged(before, shas)

    def test_F5_touchid_outcomes(self):
        before, shas = self.card.read_bytes(), self._ledger_shas()
        for why, code in (("cancel", "declined"), ("system_cancel", "declined"), ("timeout", "timeout"),
                          ("no_biometry", "unavailable"), ("lockout", "unavailable"), ("osascript_rc:1", "unavailable"),
                          ("busy", "busy")):
            with self.subTest(why=why):
                rc, _, _, out = self._approve(touch=lambda r, w=why: {"ok": False, "why": w})
                self.assertEqual((rc, out.last_json()["why"]), (2, code))
                self._unchanged(before, shas)
        rc, asked, shown, out = self._approve(available=False)
        self.assertEqual((rc, asked, shown, out.last_json()["why"]), (2, [], [], "unavailable"))
        self._unchanged(before, shas)

    def test_F6_no_terminal_attest_when_not_tty(self):
        called = []
        rc, _, _, out = self._approve(available=False, human_check=lambda: called.append(1) or (True, "tester", []))
        self.assertEqual(called, [], "不是終端機就不跑終端 attest")
        self.assertEqual((rc, out.last_json()["why"]), (2, "unavailable"))
        self.assertIs(self._const()["approved"], False)

    def test_F7_reason_too_long_refused(self):
        with mock.patch.object(AG, "LA_MAX", 20):
            rc, asked, shown, out = self._approve()
        self.assertEqual((rc, asked, shown), (2, [], []), "放不下就拒,全文窗也不彈")
        self.assertEqual(out.last_json()["why"], "reason_refused")
        self.assertIs(self._const()["approved"], False)

    def test_F8_default_window_off(self):
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("不該叫 osascript")):
            got = self.np._fulltext_window("t", "i", "b", "c")
        self.assertEqual(got, {"ok": False, "why": "unavailable"})
        out = _FakeIO()
        rc = self.np.approve_constitution(self.root, self.uid, stdin=_FakeIO(), stdout=out,
                                          touchid=lambda r: OK, touchid_available=lambda: True)
        self.assertEqual((rc, out.last_json()["why"]), (2, "unavailable"))
        self.assertIs(self._const()["approved"], False)

    def test_F9_fulltext_holds_machine_lock(self):
        tried = []

        def full(*a):
            with open(AG.lock_path(self.root), "a") as f:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    tried.append("got")
                    fcntl.flock(f, fcntl.LOCK_UN)
                except BlockingIOError:
                    tried.append("busy")
            return OK
        self.assertEqual(self._approve(full=full)[0], 0)
        self.assertEqual(tried, ["busy"], "全文窗開著時整台鎖在我們手上")

    def test_F10_scroll_script(self):
        body = '目標\n"引號" \\ 反斜線\n' + LONG_GOAL
        s = consent.build_strong_script("T", body, "chk", "Go", "No", "n0nce", intro="看完再按")
        self.assertIn("NSScrollView", s)
        self.assertIn("tv.editable = false", s)
        self.assertIn(json.dumps(body), s)
        self.assertIn(json.dumps("看完再按"), s)
        self.assertNotIn("__BODY__", s)
        self.assertNotIn("__INTRO__", s)
        plain = consent.build_strong_script("T", "b", "chk", "Go", "No", "n0nce")
        self.assertNotIn("NSScrollView", plain, "consent --strong 原本那個窗不變")
        if sys.platform == "darwin" and shutil.which("osacompile"):
            src = pathlib.Path(self.tmp, "full.js")
            src.write_text(s, encoding="utf-8")
            r = subprocess.run(["osacompile", "-l", "JavaScript", "-o", str(pathlib.Path(self.tmp, "full.scpt")), str(src)],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_F11_cli_json(self):
        env = dict(self.env, EZ2_AIB_STATE_ROOT=str(self.root), EZ2_AIB_HOME=str(self.root))
        r = subprocess.run([PY, str(NP), "--approve=" + self.uid], env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        res = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((res["ok"], res["why"], res["approved"], res["project_uid"]), (False, "unavailable", False, self.uid))
        self.assertIs(self._const()["approved"], False)

    def test_F12_worst_case_fits_every_locale(self):
        """標題截到 40、條數 / 增減兩位數、紅線字最長那個:四語整句都放得進 Touch ID(不然長標題的專案永遠核不了)。"""
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8"))
            rl = max((d["aib.newproj.approve_rl_word_" + k] for k in ("new", "same", "changed", "unknown")), key=len)
            p = {"title": "\u5b57" * 40, "goals": 12, "red_lines": 99, "add": 12, "rm": 12, "rl": rl, "code": "0" * 8}
            for key in ("aib.newproj.approve_touchid_reason", "aib.newproj.approve_touchid_reason_amend"):
                with self.subTest(lang=lang, key=key):
                    self.assertLessEqual(len(d[key].format(**p)), AG.LA_MAX)
                    self.assertIn("{code}", d[key])

    def test_F13_shown_equals_written(self):
        zw, rlo, esc = chr(0x200B), chr(0x202E), chr(0x1B)
        cases = (("zero-width goal", ["a", "b" + zw + "c"], None),
                 ("bidi goal", ["a" + rlo + "b"], None),
                 ("dict goal", [{"text": "a", "x": "hidden"}], None),
                 ("double space", ["a  b"], None),
                 ("first red line", ["a"], ["ok" + esc + "[2Khidden"]))
        for name, goals, red in cases:
            with self.subTest(name):
                self._set_cand(goals, red if red is not None else ["never push to main"])
                before, shas = self.card.read_bytes(), self._ledger_shas()
                rc, asked, shown, out = self._approve()
                self.assertEqual((rc, asked, shown), (2, [], []), "不彈窗")
                self.assertEqual(out.last_json()["why"], "content_unclean")
                self._unchanged(before, shas)
        self._set_cand(["a", "b"], ["never push to main"])
        self.assertEqual(self._approve()[0], 0, "乾淨的照常核")

    def test_F14_effective_goals_moved_during_touchid(self):
        self._set_cand(["a", "b"])
        self.assertEqual(self._approve()[0], 0)
        self.assertEqual(self.np.propose_goals(self.root, self.uid, ["a", "c"]), 0)

        def touch(r):
            raw = json.loads(self.card.read_text(encoding="utf-8"))
            raw["layers"]["constitution"]["goals"] = ["zzz"]       # 生效版被直接改(本人看到的差別是對著 a, b 列的)
            self.card.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            return OK
        rc, _, _, out = self._approve(touch=touch)
        self.assertEqual((rc, out.last_json()["why"]), (2, "content_changed"))
        self.assertEqual(self._const()["goals"], ["zzz"], "核准內容沒寫上去")

    def test_F15_script_single_pass(self):
        body = "看 __NONCE__ 跟 __GO__ 還有 __CHECK__"
        s = consent.build_strong_script("T", body, "chk", "Go", "No", "n0nce", intro="i")
        self.assertIn(json.dumps(body), s, "全文原樣放進去")
        self.assertEqual(s.count(json.dumps("n0nce")), 1, "nonce 只在它自己的位置")

    def test_F16_ok_without_login_name(self):
        with mock.patch.object(self.np, "_login_name", lambda: None):
            rc, _, _, out = self._approve()
        self.assertEqual((rc, out.last_json()["why"]), (2, "failed"))
        self.assertIs(self._const()["approved"], False)


class Knife(unittest.TestCase):
    def _cut(self, rel, needle, repl, test):
        with knife_copy(ROOT, rel, needle, repl, tests=(THIS,)) as copy_root:
            r = run_guarded([PY, str(copy_root / "tests" / THIS), test], cwd=str(copy_root), timeout=300)
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, "刀下去 %s 應該紅:\n%s" % (test, out))
        self.assertIn("FAIL:", out, "要紅在斷言上(FAIL),不是 setUp 或匯入就倒:\n" + out)
        self.assertNotIn("ERROR:", out, "刀的複本裡出現 ERROR = 沒驗到要驗的東西:\n" + out)

    def test_K1_no_content_compare(self):
        self._cut("tools/project/new_project.py", "or _const_sha(fresh) != sha:", ":",
                  "Full.test_F4_content_changed_during_touchid")

    def test_K2_no_amend_diff(self):
        self._cut("tools/project/new_project.py", '["    [~] %s → %s" % ab for ab in changed]', "[]",
                  "Full.test_F2_amend_lists_diff")

    def test_K3_fulltext_result_ignored(self):
        self._cut("src/cockpit/approve_gate.py", 'if not (isinstance(pre, dict) and pre.get("ok") is True):', "if False:",
                  "Full.test_F3_fulltext_not_passed")

    def test_K5_no_unclean_check(self):
        self._cut("tools/project/new_project.py", "    if _unclean(const):", "    if False:",
                  "Full.test_F13_shown_equals_written")

    def test_K6_no_effective_goals_check(self):
        self._cut("tools/project/new_project.py", "moved = amending and _goals_of(live) != _goals_of(card_const)",
                  "moved = False", "Full.test_F14_effective_goals_moved_during_touchid")

    def test_K7_sequential_replace(self):
        self._cut("src/cockpit/consent.py",
                  'return re.sub("|".join(re.escape(k) for k in rep), lambda m: json.dumps(str(rep[m.group(0)])), s)',
                  "for k, v in rep.items():\n        s = s.replace(k, json.dumps(str(v)))\n    return s",
                  "Full.test_F15_script_single_pass")

    def test_K4_touchid_without_code(self):
        self._cut("tools/project/new_project.py", '"title": card.get("title") or uid, "code": code,', '"title": card.get("title") or uid, "code": "",',
                  "Full.test_F1_first_approval_full_text_and_code")


if __name__ == "__main__":
    unittest.main(verbosity=2)
