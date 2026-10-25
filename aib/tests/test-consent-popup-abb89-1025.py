#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib consent(Andy 10-25:「之後有 ! 要同意的，一率在aib 跳出同意，不用手打，有生物辨識的 就用生物辨識，
若需要加強的，多一個打勾 打勾後，將該事件最重要的 一句話總結 顯示給他看」)。
C1 白名單:aib <子指令>(consent 除外)/ claude plugin / bash ~/.ez2-aib/pending/SIGN-*.sh(只准 strong);其他 not_allowed
C2 Touch ID 取消 / 沒過 ⇒ denied、指令不跑
C3 strong:沒勾「我看過了」⇒ denied、不跳 Touch ID、不跑
C4 strong 勾了 + Touch ID 過 ⇒ 跑、記 rc、log;aib 換成這份 aib 的絕對路徑
C5 這台不能跳 Touch ID(非 Mac / AIB_TOUCHID=0)⇒ unavailable、不跑
C6 席位手寫的 inbox JSON(Bash 被擋時)照樣處理;壞 JSON ⇒ invalid;處理過搬到 done、不重跑
C7 別的指紋視窗開著(鎖被拿走)⇒ 這輪不處理、留在 inbox
C8 Touch ID 視窗字是實際指令，不是席位寫的一句話;strong 對話框兩者都列、標明席位寫的
C10 指令太長、Touch ID 視窗放不下 ⇒ 就算沒要 strong 也先出對話框列全文
C9 ask CLI:不在白名單 rc 2、不進 inbox;合格 ⇒ inbox 有檔、status pending
刀(複本 + run_guarded):K1 不問 Touch ID ⇒ C2 紅;K2 白名單全放 ⇒ C1 紅;K3 strong 不看勾選 ⇒ C3 紅
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

import consent as C  # noqa: E402
import touchid_approve as TA  # noqa: E402


class Ran(object):
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if kw.get("stdout") is not None and hasattr(kw["stdout"], "write"):
            kw["stdout"].write("hello\n")
        return subprocess.CompletedProcess(argv, 0, "", "")


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-consent-"))
        self.home = self.td / "home"
        (self.home / ".ez2-aib" / "pending").mkdir(parents=True)
        self.env = {"HOME": str(self.home), "EZ2_AIB_STATE_ROOT": str(self.td / "state"), "PATH": "/usr/bin:/bin"}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        self.run_ = Ran()
        self.asked = []
        p2 = mock.patch.object(C, "_notify", lambda *a, **k: None)     # 測試不跳真的系統通知
        p2.start()
        self.addCleanup(p2.stop)

    def touch(self, ok=True):
        def ask(reason):
            self.asked.append(reason)
            return {"ok": ok, "why": "ok" if ok else "cancel"}
        return ask

    def strong(self, ok=True):
        def f(req, env=None):
            self.asked.append("STRONG:" + req["summary"])
            return {"ok": ok, "why": "ok" if ok else "not_checked"}
        return f

    def req(self, argv, strong=False, summary="寫進度"):
        r, why = C.new_request(argv, summary, strong=strong, seat="abb89", cwd=str(self.td), env=self.env)
        self.assertIsNotNone(r, why)
        return r

    def sign(self, name="SIGN-x-0.1.0-abb89-1025.sh"):
        p = self.home / ".ez2-aib" / "pending" / name
        p.write_text("echo hi\n", encoding="utf-8")
        return str(p)


class Whitelist(Base):
    def test_c1_whitelist(self):
        ok = [(["aib", "now", "--seat", "abb89", "x", "1/2"], False), (["claude", "plugin", "list"], False),
              (["bash", self.sign()], True)]
        for argv, strong in ok:
            self.assertTrue(C.classify(argv, strong, self.env)[0], argv)
        outside = self.td / "SIGN-evil-0.1.0.sh"
        outside.write_text("x", encoding="utf-8")
        bad = [(["aib", "consent", "ask"], True), (["aib"], True), (["aib", "--x"], True),
               (["claude", "--dangerously-skip-permissions"], True), (["bash", self.sign()], False),
               (["bash", str(outside)], True), (["bash", self.sign("notsign.sh")], True),
               (["bash", "-c", "rm -rf /"], True), (["sh", "-c", "x"], True), (["rm", "-rf", "/"], True),
               (["aib", "now\x00x"], True), ([], True), ("aib now", True)]
        for argv, strong in bad:
            self.assertIsNone(C.classify(argv, strong, self.env)[0], argv)


class Process(Base):
    def test_c2_touchid_denied_not_run(self):
        r = self.req(["aib", "now", "x", "1/2"])
        d = C.process(r, env=self.env, run=self.run_, ask=self.touch(False), available=True)
        self.assertEqual(d["result"], "denied")
        self.assertEqual(self.run_.calls, [])

    def test_c3_strong_unchecked(self):
        r = self.req(["aib", "now", "x", "1/2"], strong=True)
        d = C.process(r, env=self.env, run=self.run_, ask=self.touch(True), ask_strong_fn=self.strong(False),
                      available=True)
        self.assertEqual(d["result"], "denied")
        self.assertEqual(self.run_.calls, [])
        self.assertEqual([a for a in self.asked if not a.startswith("STRONG:")], [], "沒勾就不該跳 Touch ID")

    def test_c4_strong_ok_runs(self):
        r = self.req(["aib", "now", "x", "1/2"], strong=True, summary="更新進度到 1/2")
        d = C.process(r, env=self.env, run=self.run_, ask=self.touch(True), ask_strong_fn=self.strong(True),
                      available=True)
        self.assertEqual(d["result"], "ok")
        self.assertEqual(d["rc"], 0)
        self.assertEqual(self.run_.calls[0][0], str(ROOT / "src" / "cockpit" / "aib"))
        self.assertEqual(self.run_.calls[0][1:], ["now", "x", "1/2"])
        self.assertIn("hello", Path(d["log"]).read_text(encoding="utf-8"))
        self.assertEqual(C.status(r["id"], self.env)["result"], "ok")

    def test_c5_unavailable(self):
        r = self.req(["aib", "now", "x", "1/2"])
        d = C.process(r, env=self.env, run=self.run_, ask=self.touch(True), available=False)
        self.assertEqual(d["result"], "unavailable")
        self.assertEqual(self.run_.calls, [])
        self.assertFalse(TA.available(platform="linux"))
        self.assertFalse(TA.available(platform="darwin", environ={"AIB_TOUCHID": "0"}))

    def test_c8_reason_is_real_command(self):
        r = self.req(["aib", "now", "x", "1/2"], summary="只是看看天氣")
        reason = C.touchid_reason(r, self.env)
        self.assertIn("aib now x 1/2", reason)
        self.assertNotIn("天氣", reason)
        captured = {}

        def fake(argv, input=None, **kw):
            captured["script"] = input
            return subprocess.CompletedProcess(argv, 0, '{"ok": false, "go": true, "checked": false}\n', "")
        out = C.ask_strong(r, env=self.env, run=fake)
        self.assertEqual(out["why"], "not_checked")
        self.assertIn(json.dumps("天氣")[1:-1], captured["script"])
        self.assertIn("aib now x 1/2", captured["script"])
        self.assertIn("@abb89", captured["script"])


    def test_c10_long_command_forces_strong(self):
        r = self.req(["aib", "now", "x" * 200, "1/2"])
        d = C.process(r, env=self.env, run=self.run_, ask=self.touch(True), ask_strong_fn=self.strong(False),
                      available=True)
        self.assertEqual(d["result"], "denied")
        self.assertTrue(any(a.startswith("STRONG:") for a in self.asked))
        self.assertEqual(self.run_.calls, [])


class Inbox(Base):
    def test_c6_handwritten_inbox(self):
        inbox = C.inbox_dir(self.env)
        inbox.mkdir(parents=True)
        (inbox / "hand-1.json").write_text(json.dumps({"argv": ["aib", "now", "x", "1/2"], "summary": "手寫的",
                                                       "seat": "abb89"}), encoding="utf-8")
        (inbox / "hand-2.json").write_text("{broken", encoding="utf-8")
        n = C.run_once(self.env, run=self.run_, ask=self.touch(True), available=True)
        self.assertEqual(n, 2)
        self.assertEqual(C.status("hand-1", self.env)["result"], "ok")
        self.assertEqual(C.status("hand-2", self.env)["result"], "invalid")
        self.assertEqual(list(inbox.glob("*.json")), [])
        self.assertEqual(C.run_once(self.env, run=self.run_, ask=self.touch(True), available=True), 0)
        self.assertEqual(len(self.run_.calls), 1)

    def test_c7_lock_held(self):
        r = self.req(["aib", "now", "x", "1/2"])
        with TA.one_at_a_time(C.state_root(self.env) / "locks" / "touchid-approve.lock") as g:
            self.assertTrue(g.held)
            self.assertEqual(C.run_once(self.env, run=self.run_, ask=self.touch(True), available=True), 0)
        self.assertEqual(C.status(r["id"], self.env)["result"], "pending")
        self.assertEqual(self.run_.calls, [])


class Cli(Base):
    def test_c9_ask_cli(self):
        self.assertEqual(C.main(["ask", "--summary", "x", "--", "rm", "-rf", "/"], env=self.env), 2)
        self.assertEqual(list(C.inbox_dir(self.env).glob("*.json")) if C.inbox_dir(self.env).exists() else [], [])
        self.assertEqual(C.main(["ask", "--summary", "寫進度", "--seat", "abb89", "--", "aib", "now", "x", "1/2"],
                                env=self.env), 0)
        files = list(C.inbox_dir(self.env).glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(C.status(files[0].stem, self.env)["result"], "pending")


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(ROOT, "src/cockpit/consent.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), cell], cwd=str(root),
                            max_procs=40, timeout=120)
        self.assertIsNone(r.tripped)
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k1_no_touchid(self):
        self._knife("    t = ask(touchid_reason(req, env))\n", "    t = {\"ok\": True}\n",
                    "Process.test_c2_touchid_denied_not_run")

    def test_k2_whitelist_open(self):
        self._knife('    if not isinstance(argv, list) or not argv or len(argv) > MAX_ARGV:\n',
                    '    return "aib", ""\n    if not isinstance(argv, list) or not argv or len(argv) > MAX_ARGV:\n',
                    "Whitelist.test_c1_whitelist")

    def test_k3_strong_ignores_check(self):
        self._knife('        if not s.get("ok"):\n            return _finish(req, "denied", env, why=s.get("why"))\n',
                    "", "Process.test_c3_strong_unchecked")


if __name__ == "__main__":
    unittest.main()
