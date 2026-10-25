#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib consent(Andy 10-25:「之後有 ! 要同意的，一率在aib 跳出同意，不用手打，有生物辨識的 就用生物辨識，
若需要加強的，多一個打勾 打勾後，將該事件最重要的 一句話總結 顯示給他看」)。
C1 白名單:aib now/dm-queue/lang/update/plugins/theme、claude plugin 子指令、bash ~/.ez2-aib/pending/SIGN-*.sh(只准 strong);其他 not_allowed
C2 Touch ID 取消 / 沒過 ⇒ denied、指令不跑
C3 strong:沒勾「我看過了」⇒ denied、不跳 Touch ID、不跑
C4 strong 勾了 + Touch ID 過 ⇒ 跑(cwd = HOME)、記 rc、log;aib 換成這份 aib 的絕對路徑;rc≠0 ⇒ rc_nonzero
C5 這台不能跳 Touch ID(非 Mac / AIB_TOUCHID=0)⇒ unavailable、不跑
C6 席位手寫的 inbox JSON(Bash 被擋時)照樣處理;壞 JSON ⇒ invalid;處理過搬走、不重跑
C7 別的指紋視窗開著(鎖被拿走)⇒ 等到上限就收手、留在 inbox;鎖放掉後下一輪處理
C8 Touch ID 視窗字是實際指令，不是席位寫的一句話;strong 對話框兩者都列、標明「自稱」
C9 ask CLI:不在白名單 rc 2、不進 inbox;合格 ⇒ inbox 有檔、status pending
C10 指令太長、Touch ID 視窗放不下 ⇒ 就算沒要 strong 也先出對話框列全文
C11 argv 有控制字 / 隱形字 / 方向字 ⇒ not_allowed(視窗看不出原樣)
C12 SIGN:同意時鎖 sha256、跑暫存複本;同意後原檔被改 ⇒ 跑的還是同意的那份;對話框列 sha256
C13 跳窗前申請已搬到 running/(別支 run-once 拿不到同一件)
C14 跑指令時鎖已放(別的 Touch ID 視窗照開)
C15 超過 30 分鐘的申請 ⇒ expired、不跳窗
C16 壞檔名搬到 bad/;撞號(done 已有)不覆寫原紀錄;剛寫的(2 秒內)先跳過
刀(複本 + run_guarded):K1 不問 Touch ID ⇒ C2 紅;K2 白名單全放 ⇒ C1 紅;K3 strong 不看勾選 ⇒ C3 紅;
  K4 不查怪字 ⇒ C11 紅;K5 跑原檔不跑複本 ⇒ C12 紅;K6 跑的時候還拿著鎖 ⇒ C14 紅
"""
import json
import os
import subprocess
import sys
import tempfile
import time
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

AIB_ABS = str(ROOT / "src" / "cockpit" / "aib")


class Ran(object):
    def __init__(self, rc=0, on_run=None):
        self.calls, self.kw, self.rc, self.on_run = [], [], rc, on_run

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        self.kw.append(kw)
        if self.on_run:
            self.on_run(argv)
        if kw.get("stdout") is not None and hasattr(kw["stdout"], "write"):
            kw["stdout"].write("hello\n")
        return subprocess.CompletedProcess(argv, self.rc, "", "")


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
            self.strong_seen = req
            return {"ok": ok, "why": "ok" if ok else "not_checked"}
        return f

    def req(self, argv, strong=False, summary="寫進度"):
        r, why = C.new_request(argv, summary, strong=strong, seat="abb89", env=self.env)
        self.assertIsNotNone(r, why)
        return r

    def age_inbox(self):
        t = time.time() - 10
        for p in C.inbox_dir(self.env).iterdir():
            os.utime(p, (t, t))

    def drain(self, **kw):
        self.age_inbox()
        kw.setdefault("ask", self.touch(True))
        kw.setdefault("available", True)
        return C.run_once(self.env, run=kw.pop("run", self.run_), sleep=lambda s: None, **kw)

    def one(self, r, **kw):
        self.drain(**kw)
        return C.status(r["id"], self.env)

    def sign(self, name="SIGN-x-0.1.0-abb89-1025.sh", text="echo hi\n"):
        p = self.home / ".ez2-aib" / "pending" / name
        p.write_text(text, encoding="utf-8")
        return str(p)


class Whitelist(Base):
    def test_c1_whitelist(self):
        ok = [(["aib", "now", "--seat", "abb89", "x", "1/2"], False), (["aib", "dm-queue", "list"], False),
              (["claude", "plugin", "list"], False), (["claude", "plugin", "install", "aib-boards@ez2"], False),
              (["bash", self.sign()], True)]
        for argv, strong in ok:
            self.assertTrue(C.classify(argv, strong, self.env)[0], argv)
        outside = self.td / "SIGN-evil-0.1.0.sh"
        outside.write_text("x", encoding="utf-8")
        bad = [(["aib", "consent", "ask"], True), (["aib"], True), (["aib", "--x"], True),
               (["aib", "test", "x"], True), (["aib", "knife"], True), (["aib", "ship"], True),
               (["aib", "ext", "install", "x"], True), (["aib", "seat", "open"], True), (["aib", "dm", "x"], True),
               (["aib", "6ddf"], True), (["aib", "plugins", "install", "--allow-unsigned", "x"], True),
               (["aib", "update", "--allow-unsigned=1"], True),
               (["claude", "--dangerously-skip-permissions"], True), (["claude", "-p", "x"], True),
               (["claude", "plugin", "list", "--dangerously-skip-permissions"], True),
               (["claude", "plugin", "install", "./local-dir"], True), (["claude", "plugin", "install"], True),
               (["claude", "plugin", "marketplace", "add", "x"], True),
               (["bash", self.sign()], False),
               (["bash", str(outside)], True), (["bash", self.sign("notsign.sh")], True),
               (["bash", "-c", "rm -rf /"], True), (["sh", "-c", "x"], True), (["rm", "-rf", "/"], True),
               (["aib", "now\x00x"], True), ([], True), ("aib now", True),
               (["aib", "now", "x" * 1600], True)]
        for argv, strong in bad:
            self.assertIsNone(C.classify(argv, strong, self.env)[0], argv)

    def test_c11_bad_chars(self):
        for s in ("a\nb", "a\rb", "a\x1bb", "a\u200bb", "a\u202eb", "a\u2066b", "a\ufeffb", "a\x85b", "a\u2028b"):
            kind, why = C.classify(["aib", "now", s], True, self.env)
            self.assertIsNone(kind, repr(s))
            self.assertEqual(why, "argv_chars", repr(s))
        self.assertTrue(C.classify(["aib", "now", "中文 進度 ✓"], False, self.env)[0])


class Process(Base):
    def test_c2_touchid_denied_not_run(self):
        r = self.req(["aib", "now", "x", "1/2"])
        d = self.one(r, ask=self.touch(False))
        self.assertEqual(d["result"], "denied")
        self.assertEqual(self.run_.calls, [])

    def test_c3_strong_unchecked(self):
        r = self.req(["aib", "now", "x", "1/2"], strong=True)
        d = self.one(r, ask_strong_fn=self.strong(False))
        self.assertEqual(d["result"], "denied")
        self.assertEqual(self.run_.calls, [])
        self.assertEqual([a for a in self.asked if not a.startswith("STRONG:")], [], "沒勾就不該跳 Touch ID")

    def test_c4_strong_ok_runs(self):
        r = self.req(["aib", "now", "x", "1/2"], strong=True, summary="更新進度到 1/2")
        d = self.one(r, ask_strong_fn=self.strong(True))
        self.assertEqual(d["result"], "ok")
        self.assertEqual(d["rc"], 0)
        self.assertEqual(self.run_.calls[0], [AIB_ABS, "now", "x", "1/2"])
        self.assertEqual(self.run_.kw[0]["cwd"], str(self.home))
        self.assertIn("hello", Path(d["log"]).read_text(encoding="utf-8"))
        r2 = self.req(["aib", "now", "y", "2/2"])
        d2 = self.one(r2, run=Ran(rc=3))
        self.assertEqual((d2["result"], d2["rc"]), ("rc_nonzero", 3))

    def test_c5_unavailable(self):
        r = self.req(["aib", "now", "x", "1/2"])
        d = self.one(r, available=False)
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
        self.assertIn(json.dumps("自稱")[1:-1], captured["script"])

    def test_c10_long_command_forces_strong(self):
        r = self.req(["aib", "now", "x" * 200, "1/2"])
        d = self.one(r, ask_strong_fn=self.strong(False))
        self.assertEqual(d["result"], "denied")
        self.assertTrue(any(a.startswith("STRONG:") for a in self.asked))
        self.assertEqual(self.run_.calls, [])

    def test_c12_sign_pinned_copy(self):
        path = self.sign(text="echo approved\n")
        r = self.req(["bash", path], strong=True, summary="簽 x")
        ran = {}

        def on_run(argv):
            ran["argv"] = list(argv)
            ran["body"] = Path(argv[1]).read_text(encoding="utf-8")

        def touch_then_tamper(reason):
            Path(path).write_text("echo EVIL\n", encoding="utf-8")   # 同意之後、跑之前被改
            return {"ok": True, "why": "ok"}
        d = self.one(r, run=Ran(on_run=on_run), ask=touch_then_tamper, ask_strong_fn=self.strong(True))
        self.assertEqual(d["result"], "ok")
        self.assertEqual(ran["argv"][0], "/bin/bash")
        self.assertNotEqual(ran["argv"][1], path)
        self.assertEqual(ran["body"], "echo approved\n")
        self.assertFalse(Path(ran["argv"][1]).exists(), "暫存複本跑完要刪")
        self.assertEqual(len(self.strong_seen["sign"]["sha256"]), 64)
        self.assertIn("echo approved", self.strong_seen["sign"]["head"])
        self.assertEqual(d["sha256"], self.strong_seen["sign"]["sha256"])

    def test_c15_expired(self):
        r = self.req(["aib", "now", "x", "1/2"])
        p = C.inbox_dir(self.env) / (r["id"] + ".json")
        old = json.loads(p.read_text(encoding="utf-8"))
        old["t"] = int(time.time()) - C.EXPIRE_S - 5
        p.write_text(json.dumps(old), encoding="utf-8")
        d = self.one(r)
        self.assertEqual(d["result"], "expired")
        self.assertEqual(self.asked, [])
        self.assertEqual(self.run_.calls, [])


class Inbox(Base):
    def test_c6_handwritten_inbox(self):
        inbox = C.inbox_dir(self.env)
        inbox.mkdir(parents=True)
        (inbox / "hand-1.json").write_text(json.dumps({"argv": ["aib", "now", "x", "1/2"], "summary": "手寫的",
                                                       "seat": "abb89"}), encoding="utf-8")
        (inbox / "hand-2.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(self.drain(), 2)
        self.assertEqual(C.status("hand-1", self.env)["result"], "ok")
        self.assertEqual(C.status("hand-2", self.env)["result"], "invalid")
        self.assertEqual(list(inbox.glob("*.json")), [])
        self.assertEqual(list(C.running_dir(self.env).glob("*.json")), [])
        self.assertEqual(self.drain(), 0)
        self.assertEqual(len(self.run_.calls), 1)

    def test_c7_lock_held(self):
        r = self.req(["aib", "now", "x", "1/2"])
        self.age_inbox()
        slept = []
        with TA.one_at_a_time(C.state_root(self.env) / "locks" / "touchid-approve.lock") as g:
            self.assertTrue(g.held)
            n = C.run_once(self.env, run=self.run_, sleep=slept.append, retry_s=15, ask=self.touch(True),
                           available=True)
        self.assertEqual(n, 0)
        self.assertEqual(sum(slept), 15)
        self.assertEqual(C.status(r["id"], self.env)["result"], "pending")
        self.assertEqual(self.run_.calls, [])
        self.assertEqual(self.drain(), 1)
        self.assertEqual(C.status(r["id"], self.env)["result"], "ok")

    def test_c13_moved_to_running_before_popup(self):
        r = self.req(["aib", "now", "x", "1/2"])
        seen = {}

        def ask(reason):
            seen["inbox"] = (C.inbox_dir(self.env) / (r["id"] + ".json")).exists()
            seen["running"] = (C.running_dir(self.env) / (r["id"] + ".json")).exists()
            return {"ok": True, "why": "ok"}
        self.one(r, ask=ask)
        self.assertEqual(seen, {"inbox": False, "running": True})

    def test_c14_lock_released_while_running(self):
        r = self.req(["aib", "now", "x", "1/2"])
        seen = {}

        def on_run(argv):
            with TA.one_at_a_time(C.state_root(self.env) / "locks" / "touchid-approve.lock") as g:
                seen["free"] = g.held
        d = self.one(r, run=Ran(on_run=on_run))
        self.assertEqual(d["result"], "ok")
        self.assertTrue(seen["free"], "跑指令時鎖還拿著 ⇒ 別的 Touch ID 視窗開不了")

    def test_c16_bad_names_dup_fresh(self):
        inbox = C.inbox_dir(self.env)
        inbox.mkdir(parents=True)
        (inbox / "bad name!.json").write_text("{}", encoding="utf-8")
        (inbox / "noext").write_text("{}", encoding="utf-8")
        r = self.req(["aib", "now", "x", "1/2"])
        self.drain()
        self.assertEqual(C.status(r["id"], self.env)["result"], "ok")
        self.assertEqual(sorted(os.listdir(inbox)), [])
        self.assertEqual(len(os.listdir(C.bad_dir(self.env))), 2)
        # 撞號:同一個 id 再寫一次 ⇒ 不覆寫 done、搬 bad
        (inbox / (r["id"] + ".json")).write_text(json.dumps({"argv": ["aib", "now", "evil", "9/9"], "summary": "x",
                                                            "t": int(time.time())}), encoding="utf-8")
        self.drain()
        self.assertEqual(C.status(r["id"], self.env)["argv"], ["aib", "now", "x", "1/2"])
        self.assertEqual(len(os.listdir(C.bad_dir(self.env))), 3)
        self.assertEqual(len(self.run_.calls), 1)
        # 剛寫的先跳過
        r2 = self.req(["aib", "now", "y", "2/2"])
        self.assertEqual(C.run_once(self.env, run=self.run_, sleep=lambda s: None, ask=self.touch(True),
                                    available=True), 0)
        self.assertEqual(C.status(r2["id"], self.env)["result"], "pending")


class Cli(Base):
    def test_c9_ask_cli(self):
        self.assertEqual(C.main(["ask", "--summary", "x", "--", "rm", "-rf", "/"], env=self.env), 2)
        self.assertEqual(list(C.inbox_dir(self.env).glob("*.json")) if C.inbox_dir(self.env).exists() else [], [])
        self.assertEqual(C.main(["ask", "--summary", "寫進度", "--seat", "abb89", "--", "aib", "now", "x", "1/2"],
                                env=self.env), 0)
        files = list(C.inbox_dir(self.env).glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(C.status(files[0].stem, self.env)["result"], "pending")

    def test_install_guards(self):
        self.assertEqual(C.install(self.env, home=str(self.home), platform="darwin", dev_tree="/x",
                                   launchctl=lambda *a: 0), "dev_tree")
        self.assertEqual(C.install(self.env, home=str(self.home), platform="darwin", dev_tree=False,
                                   launchctl=lambda *a: 0 if a[0] == "unload" else 1), "failed")
        args = C.agent_plist(self.env)["ProgramArguments"]
        self.assertEqual((args[0], args[2:]), ("/bin/bash", ["consent", "run-once"]))
        self.assertEqual(C.agent_plist(self.env)["StartInterval"], 300)


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
        self._knife("    a = ask(touchid_reason(view, env))\n", "    a = {\"ok\": True}\n",
                    "Process.test_c2_touchid_denied_not_run")

    def test_k2_whitelist_open(self):
        self._knife('    if not isinstance(argv, list) or not argv or len(argv) > MAX_ARGV:\n',
                    '    return "aib", ""\n    if not isinstance(argv, list) or not argv or len(argv) > MAX_ARGV:\n',
                    "Whitelist.test_c1_whitelist")

    def test_k3_strong_ignores_check(self):
        self._knife('        if not s.get("ok"):\n            return "denied", {"why": s.get("why")}, None, None\n',
                    "", "Process.test_c3_strong_unchecked")

    def test_k4_no_char_check(self):
        self._knife("and not _BAD_CHAR.search(a) ", "", "Whitelist.test_c11_bad_chars")

    def test_k5_run_original(self):
        self._knife('["/bin/bash", str(copy)]', '["/bin/bash", argv[1]]', "Process.test_c12_sign_pinned_copy")

    def test_k6_lock_kept(self):
        # 跑的時候另開一個 fd 把同一把鎖拿著(等於沒放鎖就跑)
        self._knife("                execute(rid, req, real, env=env, run=run, extra=detail)\n",
                    "                _kf = open(str(state_root(env) / \"locks\" / \"touchid-approve.lock\"), \"a\"); "
                    "__import__(\"fcntl\").flock(_kf, 2); execute(rid, req, real, env=env, run=run, extra=detail); "
                    "_kf.close()\n", "Inbox.test_c14_lock_released_while_running")


if __name__ == "__main__":
    unittest.main()
