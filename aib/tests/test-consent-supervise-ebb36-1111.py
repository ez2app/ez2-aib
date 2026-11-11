#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib consent:跑的時候看得到、卡住能喊停(Andy 11-11:「可以彈出提示，同意後關閉」;
計畫 PLAN-consent-run-params-ebb36-1111,Codex + c477a 意見已收)。

S1 有 aib-consent-run ⇒ execute 交給它 supervise:argv 帶 --env(只有那幾個)、--ask-json 指 consent.py ask-stop、
   `--` 後是要跑的指令;它回的結果對到紀錄:exited rc 0 ⇒ ok、rc≠0 ⇒ rc_nonzero、stopped ⇒ stopped(誰停的照記)、
   hard_timeout ⇒ timeout;輸出不是它的格式 ⇒ failed supervisor_died;跑完收掉 running/ 的狀態檔與請停檔
S2 沒有 aib-consent-run ⇒ 照舊跑,紀錄寫 supervised: false
S3 status:跑的時候回 phase / 跑了幾秒 / 多久沒輸出 / 最後幾行(清掉看不見的字)/ log 在哪
S4 aib consent stop <id>:不在跑 / 還沒交給 aib-consent-run ⇒ not_running、不放檔;在跑 ⇒ 放 <id>.stop(只是請求)
S5 ask-stop:要勾「停止它」**而且** Touch ID 過才回 0;沒勾、Touch ID 沒過、這台沒 Touch ID、等鎖時已跑完 ⇒ 1
   (繼續跑);對話框寫「停止不會復原已做的步驟」、列指令與最後輸出;別的指紋視窗開著 ⇒ 等到上限就當繼續
S6 tail:最後 n 行、清掉看不見的字、n 有上限
S7(有 AIB_CONSENT_RUN_TEST_BIN 才跑)真的 aib-consent-run:跑完 ok、log 有輸出
刀(複本 + run_guarded):K1 ask-stop 勾了就停、不問 Touch ID ⇒ S5 紅;K2 stop 不看在不在跑 ⇒ S4 紅;
  K3 supervise 的結果不看、一律 ok ⇒ S1 紅
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


class Sup(object):
    """假的 subprocess.run:記下 argv,stdout 回一份 supervise 紀錄。"""

    def __init__(self, record, rc=0):
        self.calls, self.record, self.rc = [], record, rc

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))
        out = self.record if isinstance(self.record, str) else json.dumps(self.record)
        return subprocess.CompletedProcess(argv, self.rc, out + "\n", "")


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-consent-sup-"))
        self.home = self.td / "home"
        (self.home / ".ez2-aib" / "pending").mkdir(parents=True)
        self.exe = self.td / "aib-consent-run"
        self.exe.write_text("#!/bin/sh\nexit 0\n")
        self.exe.chmod(0o755)
        self.env = {"HOME": str(self.home), "EZ2_AIB_STATE_ROOT": str(self.td / "state"), "PATH": "/usr/bin:/bin",
                    "AIB_CONSENT_RUN": str(self.exe)}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch.object(C, "_notify", lambda *a, **k: None)
        p2.start()
        self.addCleanup(p2.stop)
        self.req = {"id": "1-abc", "argv": ["aib", "now", "x", "1/2"], "summary": "寫進度", "seat": "ebb36",
                    "strong": False}

    def running(self, rid="1-abc", state=True, tail=("one", "two\u202eowt")):
        C.running_dir(self.env).mkdir(parents=True, exist_ok=True)
        (C.running_dir(self.env) / (rid + ".json")).write_text(json.dumps(dict(self.req, id=rid)))
        if state:
            C._state_path(rid, self.env).write_text(json.dumps(
                {"id": rid, "phase": "running", "elapsed_s": 75, "quiet_s": 40, "asks": 0, "bytes": 12,
                 "tail": list(tail)}))


class S1Supervised(Base):
    def test_s1_argv_and_results(self):
        rec = {"id": "1-abc", "result": "exited", "rc": 0, "signal": None, "stopped_by": None, "asks": 0,
               "tail": ["done"]}
        run = Sup(rec)
        self.running()
        d = C.execute("1-abc", self.req, ["/x/aib", "now"], env=self.env, run=run)
        self.assertEqual(d["result"], "ok")
        argv, kw = run.calls[0]
        self.assertEqual(argv[0], str(self.exe))
        self.assertEqual(argv[1], "supervise")
        self.assertEqual(argv[argv.index("--") + 1:], ["/x/aib", "now"])
        envs = [argv[i + 1] for i, a in enumerate(argv) if a == "--env"]
        self.assertEqual(sorted(e.split("=", 1)[0] for e in envs), ["EZ2_AIB_STATE_ROOT", "HOME", "PATH"])
        ask = json.loads(argv[argv.index("--ask-json") + 1])
        self.assertEqual(ask[-1], "ask-stop")
        self.assertTrue(ask[-2].endswith("consent.py"))
        self.assertEqual(argv[argv.index("--cwd") + 1], str(self.home))
        self.assertGreater(kw["timeout"], C.RUN_TIMEOUT_S)          # Python 不先殺掉監督它的那支
        self.assertFalse(C._state_path("1-abc", self.env).exists())  # 跑完收掉
        for r, want in ((dict(rec, rc=3), "rc_nonzero"),
                        (dict(rec, result="stopped", rc=None, signal=15, stopped_by="person", asks=1,
                              remote="not_confirmed"), "stopped"),
                        (dict(rec, result="hard_timeout", rc=None, stopped_by="hard_limit"), "timeout"),
                        (dict(rec, result="failed", why="spawn:NotFound"), "failed")):
            d = C.execute("1-abc", self.req, ["/x/aib"], env=self.env, run=Sup(r))
            self.assertEqual(d["result"], want, r)
        d = C.execute("1-abc", self.req, ["/x/aib"], env=self.env,
                      run=Sup(dict(rec, result="stopped", rc=None, stopped_by="person", asks=1,
                                   remote="not_confirmed")))
        self.assertEqual(d["supervised"]["stopped_by"], "person")
        self.assertEqual(d["supervised"]["remote"], "not_confirmed")
        for junk in ("not json", json.dumps({"result": "weird"}), ""):
            d = C.execute("1-abc", self.req, ["/x/aib"], env=self.env, run=Sup(junk, rc=101))
            self.assertEqual(d["result"], "failed")
            self.assertTrue(d["why"].startswith("supervisor_died"))


class S2Unsupervised(Base):
    def test_s2_without_the_binary_runs_as_before(self):
        env = dict(self.env)
        env.pop("AIB_CONSENT_RUN")
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        with mock.patch.dict(os.environ, env, clear=True):
            d = C.execute("1-abc", self.req, ["/x/aib", "now"], env=env, run=run)
        self.assertEqual(calls, [["/x/aib", "now"]])
        self.assertEqual(d["result"], "ok")
        self.assertIs(d["supervised"], False)


class S3Status(Base):
    def test_s3_running_status(self):
        self.running()
        st = C.status("1-abc", self.env)
        self.assertEqual(st["result"], "pending")
        self.assertEqual(st["phase"], "running")
        self.assertEqual((st["elapsed_s"], st["quiet_s"]), (75, 40))
        self.assertEqual(st["tail"], ["one", "twoowt"])
        self.assertTrue(st["log"].endswith("1-abc.log"))
        self.assertFalse(st["stop_requested"])
        self.running(rid="2-def", state=False)
        self.assertEqual(C.status("2-def", self.env)["phase"], "deciding")


class S4Stop(Base):
    def test_s4_stop_is_only_a_request_while_running(self):
        self.assertEqual(C.request_stop("1-abc", self.env), "not_running")
        self.running(state=False)
        self.assertEqual(C.request_stop("1-abc", self.env), "not_running")
        self.assertFalse(C._stop_path("1-abc", self.env).exists())
        self.running()
        self.assertEqual(C.request_stop("1-abc", self.env), "requested")
        self.assertTrue(C._stop_path("1-abc", self.env).exists())
        self.assertTrue(C.status("1-abc", self.env)["stop_requested"])
        self.assertEqual(C.request_stop("../x", self.env), "not_running")


class S5AskStop(Base):
    def call(self, box_ok=True, touch_ok=True, available=True, held=True):
        self.boxes, self.touches = [], []

        def box(title, body, check, env=None):
            self.boxes.append(body)
            return {"ok": box_ok, "why": "ok" if box_ok else "cancel"}

        def touch(reason):
            self.touches.append(reason)
            return {"ok": touch_ok}

        class Gate(object):
            def __init__(self):
                self.held = held

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return C.ask_stop("1-abc", "quiet", 300, 200, env=self.env, ask=touch, ask_box=box,
                          lock=lambda env=None: Gate(), available=available)

    def test_s5_stop_needs_the_box_and_touch_id(self):
        self.running()
        self.assertEqual(self.call(), 0)
        self.assertEqual(len(self.touches), 1)
        self.assertIn("aib now x 1/2", self.boxes[0])
        self.assertIn("twoowt", self.boxes[0])
        self.assertTrue("undo" in self.boxes[0] or "復原" in self.boxes[0], self.boxes[0])
        self.assertEqual(self.call(box_ok=False), 1)
        self.assertEqual(self.touches, [])                 # 沒勾就不跳 Touch ID
        self.assertEqual(self.call(touch_ok=False), 1)
        self.assertEqual(self.call(available=False), 1)
        self.assertEqual(self.boxes, [])

    def test_s5_finished_or_lock_busy_keeps_waiting(self):
        self.running(state=False)
        self.assertEqual(self.call(), 1)
        self.assertEqual(self.boxes, [])
        self.running()
        with mock.patch.object(C, "STOP_ASK_LOCK_S", 0), mock.patch.object(C.time, "sleep", lambda s: None):
            self.assertEqual(self.call(held=False), 1)
        self.assertEqual(self.boxes, [])
        self.assertEqual(C.ask_stop("../x", "quiet", 1, 1, env=self.env), 2)


class S6Tail(Base):
    def test_s6_tail(self):
        log = C.done_dir(self.env) / "1-abc.log"
        log.parent.mkdir(parents=True)
        log.write_text("".join("line %d\n" % i for i in range(300)) + "bad\u202ex\n")
        out = C.log_tail("1-abc", 3, self.env)
        self.assertEqual(out, ["line 298", "line 299", "badx"])
        self.assertEqual(len(C.log_tail("1-abc", 10 ** 6, self.env)), C.TAIL_MAX)
        self.assertIsNone(C.log_tail("nope", 3, self.env))


@unittest.skipUnless(os.environ.get("AIB_CONSENT_RUN_TEST_BIN"), "set AIB_CONSENT_RUN_TEST_BIN to a built aib-consent-run")
class S7Real(Base):
    def test_s7_real_supervisor(self):
        env = dict(self.env, AIB_CONSENT_RUN=os.environ["AIB_CONSENT_RUN_TEST_BIN"])
        self.running()
        d = C.execute("1-abc", self.req, ["/bin/sh", "-c", "echo from-the-job; exit 0"], env=env)
        self.assertEqual(d["result"], "ok", d)
        self.assertIn("from-the-job", (C.done_dir(env) / "1-abc.log").read_text())
        d = C.execute("1-abc", self.req, ["/bin/sh", "-c", "exit 4"], env=env)
        self.assertEqual((d["result"], d["rc"]), ("rc_nonzero", 4))


class Knives(unittest.TestCase):
    def _knife(self, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(ROOT, "src/cockpit/consent.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), cell], cwd=str(root),
                            max_procs=40, timeout=120)
        self.assertIsNone(r.tripped)
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k1_stop_without_touch_id(self):
        self._knife("                touched = ask(reason)\n                return 0 if touched.get(\"ok\") else 1\n",
                    "                return 0\n", "S5AskStop")

    def test_k2_stop_without_running_check(self):
        self._knife("    if not _state_path(rid, env).is_file():\n        return \"not_running\"          #",
                    "    if False:\n        return \"not_running\"          #", "S4Stop")

    def test_k3_supervise_result_ignored(self):
        self._knife("        result = _SUPERVISED[rec[\"result\"]] or (\"ok\" if rc == 0 else \"rc_nonzero\")\n",
                    "        result = \"ok\"\n", "S1Supervised")


if __name__ == "__main__":
    unittest.main()
