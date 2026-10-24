#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib newproj --approve 的 Touch ID 路(10-17 the maintainer:「能不能變成彈出視窗」⇒ 彈窗 + Touch ID;@abb89 審)。
全在 tmp 沙盒;不跳真的 Touch ID 視窗(LAContext 一律用替身)。

T1  ask():替身 osascript 原樣印回 nonce ⇒ 過
T2  ask():印 ok 但 nonce 不對 ⇒ 不過(擋假 osascript)
T3  ask():取消 / 失敗 / 逾時(JXA 回 -9、行程逾時)/ rc≠0(沒有圖形介面)/ 讀不懂 / osascript 不在 ⇒ 全不過
T4  視窗文字照字放進 JXA:標題帶引號、換行、反斜線、</script> 都只是字(json 解回來一模一樣)
T5  真 osascript 跑組好的 JXA(只把 LAContext 換成替身):成功印回 nonce、取消回 cancel、不按 ⇒ 時間到收掉回 timeout
T6  available():不是 macOS ⇒ 沒有;AIB_TOUCHID=0 ⇒ 沒有;macOS + osascript ⇒ 有
T7  approve_constitution 走 Touch ID:過 ⇒ 卡核准、approved_via=touchid、帳本記 touchid;
    視窗文字由卡組(uid、標題、條數),呼叫端沒有參數能改字
T8  Touch ID 沒過 ⇒ rc 2、卡不動;這台沒有 Touch ID 路 ⇒ rc 2、不叫視窗(照舊只有終端機)
T9  終端機三條件全到 ⇒ 照舊走打 uid,不叫 Touch ID
T10 一次只核一件:別席正開著 Touch ID 視窗(鎖被拿著)⇒ 這次 rc 2、why=busy、不叫第二個視窗、卡不動;放掉後照常能核
T11 標題裡的方向控制 / 零寬字(U+202E 之類)進視窗前就拿掉
T12 C1 控制字元(\x80-\x9f)換空白;軟連字號 U+00AD、阿拉伯字母標記 U+061C 拿掉;\xa0 起照留
刀(複本):K1 拿掉 nonce 比對 ⇒ T2 紅;K2 不看 Touch ID 結果 ⇒ T8 紅;K3 拿掉 AIB_TOUCHID=0 ⇒ T6 紅;
         K4 拿掉 gate.held 判斷 ⇒ T10 紅;K5 _CTRL 不含 C1 ⇒ T12 紅
"""
import importlib.util
import inspect
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
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
import touchid_approve as TA  # noqa: E402

THIS = pathlib.Path(__file__).name
PY = sys.executable
OSA = os.path.exists(TA.OSASCRIPT) and sys.platform == "darwin"
NONCE_RE = re.compile(r'NONCE = ("[0-9a-f]+")')


def _load_new_project():
    spec = importlib.util.spec_from_file_location("new_project_touchid", str(ROOT / "tools" / "project" / "new_project.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_osascript(reply=None, rc=0, raise_=None):
    """替身 subprocess.run:讀 JXA 裡的 nonce,照 reply(nonce) 印一行。"""
    seen = {}

    def run(argv, input=None, **kw):
        seen["argv"], seen["script"] = argv, input
        if raise_:
            raise raise_
        nonce = json.loads(NONCE_RE.search(input).group(1))
        out = reply(nonce) if reply else ""
        return subprocess.CompletedProcess(argv, rc, out, "")
    run.seen = seen
    return run


class Ask(unittest.TestCase):
    def test_T1_nonce_echoed_passes(self):
        run = fake_osascript(lambda n: json.dumps({"ok": True, "nonce": n}) + "\n")
        self.assertEqual(TA.ask("approve x", run=run), {"ok": True, "why": "ok"})
        self.assertEqual(run.seen["argv"][:3], ["/usr/bin/osascript", "-l", "JavaScript"])

    def test_T2_wrong_nonce_fails(self):
        run = fake_osascript(lambda n: json.dumps({"ok": True, "nonce": "0" * 32}))
        self.assertEqual(TA.ask("approve x", run=run)["ok"], False)
        run = fake_osascript(lambda n: json.dumps({"ok": True}))
        self.assertEqual(TA.ask("approve x", run=run)["ok"], False)

    def test_T3_everything_else_fails(self):
        cases = {
            "cancel": fake_osascript(lambda n: json.dumps({"ok": False, "code": "-2"})),
            "failed": fake_osascript(lambda n: json.dumps({"ok": False, "code": "-1"})),
            "timeout": fake_osascript(lambda n: json.dumps({"ok": False, "code": "-9"})),
            "osascript_rc:1": fake_osascript(lambda n: json.dumps({"ok": True, "nonce": n}), rc=1),
            "unreadable": fake_osascript(lambda n: "ok\n"),
        }
        for why, run in cases.items():
            with self.subTest(why=why):
                self.assertEqual(TA.ask("approve x", run=run), {"ok": False, "why": why})
        r = TA.ask("x", run=fake_osascript(raise_=subprocess.TimeoutExpired("osascript", 1)))
        self.assertEqual(r, {"ok": False, "why": "timeout"})
        r = TA.ask("x", run=fake_osascript(raise_=FileNotFoundError()))
        self.assertFalse(r["ok"])
        r = TA.ask("x", run=fake_osascript(lambda n: json.dumps({"ok": "true", "nonce": n})))
        self.assertFalse(r["ok"], "ok 一定要是真的 true,字串不算")


class Script(unittest.TestCase):
    def test_T4_reason_is_only_text(self):
        nasty = '核准 "x"); $.NSTask.launchedTaskWithLaunchPathArguments("/bin/sh", []); //\n\\ </script>  '
        script = TA.build_script(nasty, "ab" * 16, 5)
        lit = re.search(r"var REASON = (\"(?:[^\"\\\n]|\\.)*\"), NONCE", script)
        self.assertIsNotNone(lit, "REASON 要是一個 JSON 字串字面")
        self.assertEqual(json.loads(lit.group(1)), nasty)
        self.assertNotIn("\n", lit.group(1))
        self.assertEqual(TA.clean("a\nb\x00c" + "字" * 100, 10), "a b c字字字字…")

    def test_T11_invisible_chars_removed(self):
        self.assertEqual(TA.clean("Demo\u202egnp.exe\u200b\u2066x\ufeff"), "Demognp.exex")

    def test_T12_c1_and_soft_marks(self):
        self.assertEqual(TA.clean("a\x85b\x9bc\x80d\x9fe\u2028f"), "a b c d e f")
        self.assertEqual(TA.clean("De\u00admo\u061cx"), "Demox")
        self.assertEqual(TA.clean("é中\xa0z"), "é中\xa0z")                 # \xa0 起不是控制字元,照留

    def _real(self, ctx_js, wait=5):
        nonce = "cd" * 16
        script = TA.build_script("test", nonce, wait).replace("$.LAContext.alloc.init", ctx_js)
        self.assertIn(ctx_js, script)
        r = subprocess.run([TA.OSASCRIPT, "-l", "JavaScript", "-"], input=script, capture_output=True,
                           text=True, timeout=wait + 30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return nonce, json.loads(r.stdout.strip().splitlines()[-1])

    @unittest.skipUnless(OSA, "不是 macOS")
    def test_T5_real_jxa_with_fake_context(self):
        nonce, out = self._real("{evaluatePolicyLocalizedReasonReply: function (p, r, cb) { cb(true, null); }}")
        self.assertEqual(out, {"ok": True, "nonce": nonce})
        _n, out = self._real("{evaluatePolicyLocalizedReasonReply: function (p, r, cb) "
                             "{ cb(false, {isNil: function () { return false; }, code: -2}); }}")
        self.assertEqual(out, {"ok": False, "code": "-2"})
        _n, out = self._real("{evaluatePolicyLocalizedReasonReply: function (p, r, cb) {}, invalidate: 0}", wait=1)
        self.assertEqual(out, {"ok": False, "code": "-9"}, "不按 ⇒ 時間到收掉、算逾時")


class Available(unittest.TestCase):
    def test_T6_available(self):
        yes = lambda p: True  # noqa: E731
        self.assertFalse(TA.available("linux", {}, yes))
        self.assertFalse(TA.available("darwin", {"AIB_TOUCHID": "0"}, yes))
        self.assertFalse(TA.available("darwin", {}, lambda p: False))
        self.assertTrue(TA.available("darwin", {}, yes))
        self.assertTrue(TA.available("darwin", {"AIB_TOUCHID": "1"}, yes))


class _FakeIO:
    def __init__(self, text="", tty=False):
        self.text, self.tty, self.out = text, tty, []

    def isatty(self):
        return self.tty

    def readline(self):
        return self.text

    def write(self, s):
        self.out.append(s)

    def flush(self):
        pass


class Approve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-touchid-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.tmp, "proj"))
        env = {"PATH": os.environ.get("PATH", ""), "HOME": self.home, "EZ2_AIB_LOCALE": "zh-TW",
               "AIB_GENES": "0", "AIB_AUTO_BIND": "0", "AIB_TOUCHID": "0"}
        # 直接叫 new_project.py(= aib newproj):刀的複本只有 aib/ 這一層、沒有 bin/aib,走 bin/aib 會在 setUp 就倒(假紅)
        r = subprocess.run([PY, str(ROOT / "tools" / "project" / "new_project.py"), "--entry", "newproj", "--title", 'Demo "島"\n第二行'],
                           cwd=os.path.join(self.tmp, "proj"), env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.uid = json.loads(r.stdout.strip().splitlines()[-1])["project_uid"]
        self.root = pathlib.Path(self.home, ".ez2-aib")
        self.card = self.root / "projects" / self.uid / "card.json"
        cand = self.root / "projects" / self.uid / "candidates" / "constitution-draft.json"
        doc = json.loads(cand.read_text(encoding="utf-8"))
        doc["constitution"]["goals"] = ["ship v1", "ship v2"]
        doc["constitution"]["red_lines"] = ["never push to main"]
        cand.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        self._saved = dict(os.environ)
        os.environ.update({"HOME": self.home, "EZ2_AIB_STATE_ROOT": str(self.root), "EZ2_AIB_HOME": str(self.root),
                           "EZ2_AIB_LOCALE": "zh-TW"})
        self.np = _load_new_project()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _const(self):
        return json.loads(self.card.read_text(encoding="utf-8"))["layers"]["constitution"]

    def test_T7_touchid_pass_approves(self):
        asked = []
        out = _FakeIO()
        rc = self.np.approve_constitution(self.root, self.uid, stdin=_FakeIO(), stdout=out,
                                          touchid=lambda reason: asked.append(reason) or {"ok": True, "why": "ok"},
                                          touchid_available=lambda: True)
        self.assertEqual(rc, 0)
        self.assertEqual(len(asked), 1, "一次核准只問一次")
        reason = asked[0]
        self.assertIn(self.uid, reason)
        self.assertIn("目標 2 條", reason)
        self.assertIn("紅線 1 條", reason)
        self.assertIn('Demo "島" 第二行', reason, "標題壓成一行放進去")
        self.assertNotIn("\n", reason)
        self.assertIn("ship v2", "".join(out.out), "按之前要先把目標與紅線印出來")
        const = self._const()
        self.assertIs(const["approved"], True)
        self.assertEqual(const["approved_via"], "touchid")
        self.assertTrue(const["approved_by"].startswith("human:"))
        self.assertIn("Touch ID", const["human_rulings"][-1]["text"])
        ledger = "".join(p.read_text(encoding="utf-8") for p in self.root.rglob("*.jsonl")
                         if "ledger" in str(p) or "events" in str(p))
        self.assertIn("(touchid)", ledger)
        params = inspect.signature(self.np.approve_constitution).parameters
        self.assertFalse({"reason", "text", "message", "prompt"} & set(params), "呼叫端不能帶視窗文字")
        self.assertIn('"aib.newproj.approve_touchid_reason"', (ROOT / "tools" / "project" / "new_project.py")
                      .read_text(encoding="utf-8"))

    def test_T8_touchid_fail_or_absent_refuses(self):
        for why in ("cancel", "timeout", "nonce_mismatch", "osascript_rc:1"):
            with self.subTest(why=why):
                rc = self.np.approve_constitution(self.root, self.uid, stdin=_FakeIO(), stdout=_FakeIO(),
                                                  touchid=lambda reason, w=why: {"ok": False, "why": w},
                                                  touchid_available=lambda: True)
                self.assertEqual(rc, 2)
                self.assertIs(self._const()["approved"], False)
        rc = self.np.approve_constitution(self.root, self.uid, stdin=_FakeIO(), stdout=_FakeIO(),
                                          touchid=lambda reason: "yes", touchid_available=lambda: True)
        self.assertEqual(rc, 2, "回的不是 {ok: True} 就不算")
        called = []
        rc = self.np.approve_constitution(self.root, self.uid, stdin=_FakeIO(), stdout=_FakeIO(),
                                          touchid=lambda reason: called.append(1) or {"ok": True},
                                          touchid_available=lambda: False)
        self.assertEqual(rc, 2)
        self.assertEqual(called, [], "沒有 Touch ID 路就不叫視窗")
        self.assertIs(self._const()["approved"], False)
        # 真的 available():測試環境 AIB_TOUCHID=0 ⇒ 不叫真視窗、照舊被擋
        rc = self.np.approve_constitution(self.root, self.uid, stdin=_FakeIO(), stdout=_FakeIO())
        self.assertEqual(rc, 2)
        self.assertIs(self._const()["approved"], False)

    def test_T9_terminal_path_unchanged(self):
        called = []
        rc = self.np.approve_constitution(self.root, self.uid, human_check=lambda: (True, "tester", []),
                                          stdin=_FakeIO(self.uid + "\n", tty=True), stdout=_FakeIO(tty=True),
                                          touchid=lambda reason: called.append(1) or {"ok": True},
                                          touchid_available=lambda: True)
        self.assertEqual(rc, 0)
        self.assertEqual(called, [])
        self.assertEqual(self._const()["approved_via"], "tty")

    def test_T10_one_window_at_a_time(self):
        import fcntl
        lock = self.root / "locks" / "touchid-approve.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        called, out = [], _FakeIO()
        import contextlib
        import io
        err = io.StringIO()
        with open(lock, "a") as other, contextlib.redirect_stderr(err), contextlib.redirect_stdout(err):
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)   # 別席正在等指紋
            rc = self.np.approve_constitution(self.root, self.uid, stdin=_FakeIO(), stdout=out,
                                              touchid=lambda reason: called.append(1) or {"ok": True, "why": "ok"},
                                              touchid_available=lambda: True)
        self.assertEqual(rc, 2)
        self.assertEqual(called, [], "鎖被拿著就不跳第二個視窗")
        self.assertIn("busy", err.getvalue() + "".join(out.out))
        self.assertIs(self._const()["approved"], False)
        rc = self.np.approve_constitution(self.root, self.uid, stdin=_FakeIO(), stdout=_FakeIO(),
                                          touchid=lambda reason: called.append(1) or {"ok": True, "why": "ok"},
                                          touchid_available=lambda: True)
        self.assertEqual((rc, called), (0, [1]), "鎖放掉後照常能核")


class Knife(unittest.TestCase):
    def _cut(self, rel, needle, repl, test):
        with knife_copy(ROOT, rel, needle, repl, tests=(THIS,)) as copy_root:
            r = run_guarded([PY, str(copy_root / "tests" / THIS), test], cwd=str(copy_root), timeout=300)
        self.assertNotEqual(r.returncode, 0, "刀下去 %s 應該紅:\n%s%s" % (test, r.stdout, r.stderr))
        out = r.stdout + r.stderr
        self.assertIn("FAIL:", out, "要紅在斷言上(FAIL),不是 setUp 或匯入就倒(ERROR = 假紅):\n" + out)
        self.assertNotIn("ERROR:", out, "刀的複本裡出現 ERROR = 沒驗到要驗的東西:\n" + out)

    def test_K1_no_nonce_check(self):
        self._cut("src/cockpit/touchid_approve.py", 'if out.get("ok") is True and out.get("nonce") == nonce:',
                  'if out.get("ok") is True:', "Ask.test_T2_wrong_nonce_fails")

    def test_K2_ignore_touchid_result(self):
        self._cut("tools/project/new_project.py", 'if not (isinstance(res, dict) and res.get("ok") is True) or not human_id:',
                  "if not human_id:", "Approve.test_T8_touchid_fail_or_absent_refuses")

    def test_K3_no_off_switch(self):
        self._cut("src/cockpit/touchid_approve.py", 'if str(env.get("AIB_TOUCHID", "")).strip() == "0":',
                  "if False:", "Available.test_T6_available")

    def test_K4_no_one_at_a_time(self):
        self._cut("tools/project/new_project.py", "if gate.held else", "if True else", "Approve.test_T10_one_window_at_a_time")


    def test_K5_no_c1(self):
        self._cut("src/cockpit/touchid_approve.py", '\\\\x7f-\\\\x9f', '\\\\x7f', "Script.test_T12_c1_and_soft_marks")


if __name__ == "__main__":
    unittest.main(verbosity=2)
