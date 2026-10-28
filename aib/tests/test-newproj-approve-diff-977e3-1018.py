#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib newproj --approve:核准留雜湊、按指紋前列差異(10-18 977e3 照 GUIDE-same-user-ai-threat-model 自查,@abb89 排 0.1.24)。
卡檔與留底都是本機沒簽章的檔(同帳號 AI 改得到):這裡驗的是「本人按指紋前看得到差異」,不是防線。全在 tmp 沙盒,Touch ID 用替身。

D1 核准把「目標 + 紅線」的雜湊記進帳本(constitution sha256=…),留底檔內容雜湊對得上
D2 修訂:印目標增減([+] 新增、[-] 拿掉),沒變的那條不出現在增減裡
D3 卡上紅線被直接改了(AI 不走核准)⇒ 印「和上次核准不同」+ 列出多 / 少哪條;Touch ID 視窗字寫「和上次不同」
D4 上次核准沒記雜湊(0.1.24 以前)⇒ 印「無法比對」,視窗字寫「無法比對」;核完就有雜湊
D5 留底也被改成跟卡一樣 ⇒ 不被騙成「相同」:雜湊對不上 ⇒「不同」+「列不出來」
D6 印給本人看的字(標題、目標、紅線)清掉 ANSI / 方向控制 / 零寬字
D7 每次核准都印「卡檔沒簽章,核准只證明本人按過」
D8 等指紋期間紅線被改 ⇒ rc 2、卡上核准內容不寫(留底先寫了也無害:帳本沒換到卡上)
D9 Touch ID 沒過 ⇒ 帳本不多記、不留新留底
D10 留底不在:卡沒動 ⇒「相同」;卡上目標被動 ⇒「卡上內容和上次核准不同」(不說成紅線不同)
D11 入帳後才失敗(備份失敗)⇒ 留底已先寫;下一次核准照常比出「紅線相同」,不誤報
刀(複本):K1 雜湊亂填 ⇒ D1 紅;K2 不印目標增減 ⇒ D2 紅;K3 紅線永遠說相同 ⇒ D3 紅;K4 留底不驗雜湊 ⇒ D5 紅;
         K5 印之前不清字 ⇒ D6 紅;K6 不印沒簽章說明 ⇒ D7 紅;K7 不重查紅線 ⇒ D8 紅;
         K8 留底不在時一律說相同 ⇒ D10 紅;K9 不先寫留底 ⇒ D11 紅(@977e3 子代理審補)
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
import aib_testlib  # noqa: E402,F401 —— 基因/自動綁/Touch ID 預設關
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

ROOT = HERE.parent
THIS = pathlib.Path(__file__).name
FULL_OK = lambda title, intro, body, check: {"ok": True, "why": "ok"}   # 全文確認窗替身(TID-1b;不開真視窗)
PY = sys.executable
NP = ROOT / "tools" / "project" / "new_project.py"
SHA_RE = re.compile(r"; constitution sha256=([0-9a-f]{64})")   # 英文那句(一筆帳中英各記一次)


def _load_new_project():
    spec = importlib.util.spec_from_file_location("new_project_diff", str(NP))
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

    def text(self):
        return "".join(self.out)


OK = {"ok": True, "why": "ok"}


class Diff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-apdiff-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.tmp, "proj"))
        env = {"PATH": os.environ.get("PATH", ""), "HOME": self.home, "EZ2_AIB_LOCALE": "zh-TW",
               "AIB_GENES": "0", "AIB_AUTO_BIND": "0", "AIB_TOUCHID": "0"}
        r = subprocess.run([PY, str(NP), "--entry", "newproj", "--title", "Demo"],
                           cwd=os.path.join(self.tmp, "proj"), env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.uid = json.loads(r.stdout.strip().splitlines()[-1])["project_uid"]
        self.root = pathlib.Path(self.home, ".ez2-aib")
        self.card = self.root / "projects" / self.uid / "card.json"
        self.cand = self.root / "projects" / self.uid / "candidates" / "constitution-draft.json"
        doc = json.loads(self.cand.read_text(encoding="utf-8"))
        doc["constitution"]["goals"] = ["a", "b"]
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

    def _approve(self, touch=lambda reason: OK):
        asked, out = [], _FakeIO()
        rc = self.np.approve_constitution(self.root, self.uid, stdin=_FakeIO(), stdout=out,
                                          touchid=lambda reason: asked.append(reason) or touch(reason),
                                          touchid_available=lambda: True, fulltext=FULL_OK)
        return rc, asked, out.text()

    def _card(self):
        return json.loads(self.card.read_text(encoding="utf-8"))

    def _set_card_red_lines(self, red):
        raw = self._card()
        raw["layers"]["constitution"]["red_lines"] = red
        self.card.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    def _ledger_shas(self):
        text = "".join(p.read_text(encoding="utf-8") for p in self.root.rglob("*.jsonl") if "ledger" in str(p))
        return SHA_RE.findall(text)

    def _snapshots(self):
        d = self.card.parent / "approved"
        return sorted(p.name for p in d.glob("*.json")) if d.is_dir() else []

    def _first_approval(self):
        rc, _, out = self._approve()
        self.assertEqual(rc, 0)
        return out

    def test_D1_hash_in_ledger_and_snapshot(self):
        self._first_approval()
        const = self._card()["layers"]["constitution"]
        shas = self._ledger_shas()
        self.assertEqual(len(shas), 1)
        self.assertEqual(shas[0], self.np._const_sha(const), "帳本記的就是卡上核准那份")
        snap = json.loads((self.card.parent / "approved" / (shas[0] + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(self.np._const_sha(snap), shas[0])
        self.assertEqual(snap["red_lines"], ["never push to main"])

    def test_D2_goal_diff(self):
        self._first_approval()
        self.assertEqual(self.np.propose_goals(self.root, self.uid, ["b", "c"]), 0)
        rc, _, out = self._approve()
        self.assertEqual(rc, 0)
        self.assertIn("    [+] c", out)
        self.assertIn("    [-] a", out)
        self.assertNotIn("    [+] b", out)
        self.assertNotIn("    [-] b", out)
        self.assertIn("紅線:和上次核准相同", out)

    def test_D3_red_lines_changed_behind_the_back(self):
        self._first_approval()
        self._set_card_red_lines(["anything goes"])          # AI 直接改卡,不走核准
        self.np.propose_goals(self.root, self.uid, ["z"])
        rc, asked, out = self._approve()
        self.assertEqual(rc, 0)
        self.assertIn("紅線:和上次核准不同", out)
        self.assertIn("    [+] anything goes", out)
        self.assertIn("    [-] never push to main", out)
        self.assertIn("和上次不同", asked[0], "指紋視窗裡也看得到")

    def test_D4_legacy_approval_without_hash(self):
        raw = self._card()                                    # 0.1.24 以前核的:卡上 approved、帳本沒雜湊
        raw["layers"]["constitution"].update({"approved": True, "goals": ["a"], "red_lines": ["never push to main"]})
        raw["constitution_status"] = "approved"
        self.card.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        self.np.propose_goals(self.root, self.uid, ["a", "b"])
        rc, asked, out = self._approve()
        self.assertEqual(rc, 0)
        self.assertIn("無法比對", out)
        self.assertIn("無法比對", asked[0])
        self.assertEqual(len(self._ledger_shas()), 1, "核完就有雜湊")

    def test_D5_snapshot_forged_too(self):
        self._first_approval()
        sha = self._ledger_shas()[0]
        snap_path = self.card.parent / "approved" / (sha + ".json")
        snap_path.write_text(json.dumps({"goals": ["a", "b"], "red_lines": ["anything goes"]}), encoding="utf-8")
        self._set_card_red_lines(["anything goes"])
        self.np.propose_goals(self.root, self.uid, ["z"])
        rc, asked, out = self._approve()
        self.assertEqual(rc, 0)
        self.assertNotIn("紅線:和上次核准相同", out, "留底跟卡一起被改也不能說相同")
        self.assertIn("卡上內容和上次核准不同", out)
        self.assertIn("列不出差在哪", out)
        self.assertIn("和上次不同", asked[0])

    def test_D6_printed_text_is_clean(self):
        raw = self._card()
        raw["title"] = "De\u202emo\u200b"
        self.card.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        doc = json.loads(self.cand.read_text(encoding="utf-8"))
        doc["constitution"]["red_lines"] = ["ok\x1b[2K\x1b[1Ahidden"]
        doc["constitution"]["goals"] = ["g\u202e1"]
        self.cand.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        rc, _, out = self._approve()
        self.assertEqual(rc, 0)
        for bad in ("\x1b", "\u202e", "\u200b"):
            self.assertNotIn(bad, out, repr(bad))
        self.assertIn("hidden", out, "字濾掉、內容留著")

    def test_D7_unsigned_note_every_time(self):
        out = self._first_approval()
        self.assertIn("沒簽章", out)
        self.assertIn("只證明你本人按過", out)
        self.assertIn("帳本", out, "比對的底(帳本、留底)也照實寫沒簽章")
        self.assertIn("只是提示", out)
        self.np.propose_goals(self.root, self.uid, ["z"])
        self.assertIn("沒簽章", self._approve()[2])

    def test_D8_red_lines_moved_during_touchid(self):
        self._first_approval()
        self.np.propose_goals(self.root, self.uid, ["z"])
        before, snaps = self.card.read_bytes(), self._snapshots()

        def touch(reason):
            self._set_card_red_lines(["anything goes"])
            return OK
        rc, _, _ = self._approve(touch=touch)
        self.assertEqual(rc, 2)
        self.assertEqual(self._card()["layers"]["constitution"]["goals"], ["a", "b"], "目標沒換")
        self.assertEqual(self._card()["layers"]["constitution"]["red_lines"], ["anything goes"], "卡上只有替身改的那處,核准內容沒被寫")
        self.assertNotEqual(self.card.read_bytes(), before)

    def test_D9_touchid_fail_records_nothing(self):
        self._first_approval()
        self.np.propose_goals(self.root, self.uid, ["z"])
        shas, snaps = self._ledger_shas(), self._snapshots()
        rc, _, _ = self._approve(touch=lambda reason: {"ok": False, "why": "timeout"})
        self.assertEqual(rc, 2)
        self.assertEqual(self._ledger_shas(), shas)
        self.assertEqual(self._snapshots(), snaps)


    def test_D10_snapshot_missing_falls_back_to_card_hash(self):
        self._first_approval()
        sha = self._ledger_shas()[0]
        (self.card.parent / "approved" / (sha + ".json")).unlink()
        self.np.propose_goals(self.root, self.uid, ["z"])
        rc, _, out = self._approve(touch=lambda reason: {"ok": False, "why": "cancel"})
        self.assertEqual(rc, 2)
        self.assertIn("紅線:和上次核准相同", out, "卡沒動:雜湊對得上")
        raw = self._card()
        raw["layers"]["constitution"]["goals"] = ["sneaky"]
        self.card.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        rc, asked, out = self._approve(touch=lambda reason: {"ok": False, "why": "cancel"})
        self.assertIn("卡上內容和上次核准不同", out)
        self.assertNotIn("紅線:和上次核准不同", out, "只有目標被動,不說成紅線不同")
        self.assertIn("和上次不同", asked[0])

    def test_D11_failure_after_ledger_does_not_misreport(self):
        self._first_approval()
        self.np.propose_goals(self.root, self.uid, ["z"])
        orig = self.np._archive_snapshot
        self.np._archive_snapshot = lambda *a, **k: {"status": "failed", "error": "boom"}   # 入帳之後、寫卡之前才失敗
        try:
            self.assertEqual(self._approve()[0], 2)
        finally:
            self.np._archive_snapshot = orig
        self.assertEqual(len(self._ledger_shas()), 2, "第二筆已入帳")
        rc, _, out = self._approve()
        self.assertEqual(rc, 0)
        self.assertIn("紅線:和上次核准相同", out)
        self.assertNotIn("卡上內容和上次核准不同", out)


class Knife(unittest.TestCase):
    def _cut(self, needle, repl, test):
        with knife_copy(ROOT, "tools/project/new_project.py", needle, repl, tests=(THIS,)) as copy_root:
            r = run_guarded([PY, str(copy_root / "tests" / THIS), test], cwd=str(copy_root), timeout=300)
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, "刀下去 %s 應該紅:\n%s" % (test, out))
        self.assertIn("FAIL:", out, "要紅在斷言上(FAIL),不是 setUp 或匯入就倒:\n" + out)
        self.assertNotIn("ERROR:", out, "刀的複本裡出現 ERROR = 沒驗到要驗的東西:\n" + out)

    def test_K1_bogus_hash(self):
        self._cut("    sha = _const_sha(const)   ", '    sha = "0" * 64   ', "Diff.test_D1_hash_in_ledger_and_snapshot")

    def test_K2_no_goal_diff(self):
        self._cut("show[2:2] = diff + rl_lines", "show[2:2] = rl_lines", "Diff.test_D2_goal_diff")

    def test_K3_always_same(self):
        self._cut('rl_state = "same" if not (r_add or r_rm) else "changed"', 'rl_state = "same"',
                  "Diff.test_D3_red_lines_changed_behind_the_back")

    def test_K4_snapshot_unverified(self):
        self._cut("ok = isinstance(snap, dict) and _const_sha(snap) == sha", "ok = isinstance(snap, dict)",
                  "Diff.test_D5_snapshot_forged_too")

    def test_K5_no_clean(self):
        self._cut('return " ".join(_touchid().clean(item.get("text") if isinstance(item, dict) else item, 10 ** 6).split())',
                  'return str(item.get("text") if isinstance(item, dict) else item)', "Diff.test_D6_printed_text_is_clean")

    def test_K6_no_unsigned_note(self):
        self._cut('    show.append("  " + T("aib.newproj.approve_unsigned_note"))\n', "", "Diff.test_D7_unsigned_note_every_time")

    def test_K7_no_recheck(self):
        self._cut('if (live.get("approved") is True) != amending or _const_sha(fresh) != sha:', "if False:",
                  "Diff.test_D8_red_lines_moved_during_touchid")


    def test_K8_fallback_always_same(self):
        self._cut('same = _const_sha({"goals": card_const.get("goals"), "red_lines": card_const.get("red_lines")}) == last["sha"]',
                  "same = True", "Diff.test_D10_snapshot_missing_falls_back_to_card_hash")

    def test_K9_snapshot_after_ledger(self):
        self._cut("        _write_approved_snapshot(root, uid, sha, const)\n", "",
                  "Diff.test_D11_failure_after_ledger_does_not_misreport")


if __name__ == "__main__":
    unittest.main(verbosity=2)
