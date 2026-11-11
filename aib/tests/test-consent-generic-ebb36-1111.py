#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib consent 跑「審過一次的共用程式 + 參數檔」(計畫 PLAN-consent-run-params-ebb36-1111 (b),Python 那一半;
Rust 那一半 aib-consent-run stage / supervise 在 #661)。

G1 classify:["generic", 程式, "--params", 參數檔] 才收;不是 strong / 檔不在 / 相對路徑 / 形狀不對 ⇒ 拒
G2 ask(new_request):pin 不是 64 碼小寫十六進位 ⇒ generic_pin;審查檔不在 ⇒ generic_review;沒有 aib-consent-run ⇒
   consent_run_missing;stage 拒收 ⇒ 原因碼與行號帶回、inbox 沒檔;收了 ⇒ inbox 的申請帶 pin / review;
   別種指令帶 pin / review ⇒ generic_args_not_generic
G3 stage_program 只信對得上的輸出:rc 與 ok 不一致、雜湊 ≠ pin、複本不在暫存夾、argv 不是 bash / python3 跑複本 ⇒
   stage_output / stage_rc;拒收照它的碼
G4 decide:鎖內再 stage 一次;拒收 ⇒ not_allowed(碼照帶)、暫存夾收掉;對話框是全文版(上方申請、下方程式路徑 /
   sha256 / 審查第一行 / 參數全文 / 沒給的鍵);沒勾 ⇒ denied、暫存夾收掉;勾了 + Touch ID ⇒ 跑 stage 給的 argv、
   紀錄帶程式 / 參數雜湊 / 審查行 / 鍵名;Touch ID 句用高風險那一類(跟 SIGN 腳本同)
G5 execute:共用程式 ⇒ supervise 帶 --program-copy/--program-sha/--params-copy/--params-sha(都在 `--` 前);
   沒有 aib-consent-run ⇒ failed consent_run_missing(不退回不看雜湊、不給參數的舊路徑)
G6 CLI:--program / --params 要一起、不能再帶 `--` 後的指令
G7(有 AIB_CONSENT_RUN_TEST_BIN 才跑)真的 aib-consent-run:ask → decide → execute,程式拿到參數、改過的參數檔跑不到
刀(複本 + run_guarded):KG1 decide 不看 stage 結果 ⇒ G4 紅;KG2 supervise 不帶參數複本旗標 ⇒ G5 紅;
  KG3 stage 輸出雜湊 ≠ pin 也收 ⇒ G3 紅
"""
import json
import os
import shutil
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

PIN = "ab" * 32
PSHA = "cd" * 32
PROGRAM = "#!/bin/bash\n# aib-consent-params: SITE ARCHIVE\nset -eu\necho \"site=$SITE\"\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-consent-gen-"))
        self.addCleanup(shutil.rmtree, str(self.td), True)
        self.home = self.td / "home"
        (self.home / ".ez2-aib" / "pending").mkdir(parents=True)
        gen = self.home / ".agents" / "skills" / "ez2-x" / "tools" / "generic"
        gen.mkdir(parents=True)
        self.prog = gen / "site-remove.sh"
        self.prog.write_text(PROGRAM)
        self.params = self.home / ".ez2-aib" / "pending" / "PARAMS-x.env"
        self.params.write_text("SITE=s1\n")
        self.review = self.home / ".ez2-aib" / "pending" / "REVIEW-x.md"
        self.review.write_text("結論: 通過 …\n")
        self.exe = self.td / "aib-consent-run"
        self.exe.write_text("#!/bin/sh\nexit 0\n")
        self.exe.chmod(0o755)
        self.env = {"HOME": str(self.home), "EZ2_AIB_STATE_ROOT": str(self.td / "state"), "PATH": "/usr/bin:/bin",
                    "AIB_CONSENT_RUN": str(self.exe)}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        for name, val in (("_notify", lambda *a, **k: None), ("agent_loaded", lambda *a, **k: True)):
            q = mock.patch.object(C, name, val)
            q.start()
            self.addCleanup(q.stop)
        self.argv = ["generic", str(self.prog), "--params", str(self.params)]

    def stage_out(self, dest, **over):
        copy = str(Path(dest) / "site-remove.sh")
        out = {"ok": True,
               "program": {"real": str(self.prog), "name": "site-remove.sh", "sha256": PIN, "copy": copy,
                           "lang": "bash", "argv": ["/bin/bash", copy], "declared": ["SITE", "ARCHIVE"]},
               "params": {"sha256": PSHA, "copy": str(Path(dest) / "params.env"), "text": "SITE=s1\n",
                          "pairs": [["SITE", "s1"]], "missing": ["ARCHIVE"]},
               "review": "結論: 通過 頭: x"}
        out.update(over)
        return out


class G1Classify(Base):
    def test_g1_shape(self):
        self.assertEqual(C.classify(self.argv, True, self.env), ("shared_program", ""))
        self.assertEqual(C.classify(self.argv, False, self.env)[1], "generic_needs_strong")
        self.assertEqual(C.classify(self.argv[:3], True, self.env)[1], "generic_shape")
        self.assertEqual(C.classify(["generic", str(self.prog), "--env", str(self.params)], True, self.env)[1],
                         "generic_shape")
        self.assertEqual(C.classify(["generic", "site-remove.sh", "--params", str(self.params)], True, self.env)[1],
                         "generic_missing")
        self.assertEqual(C.classify(["generic", str(self.prog), "--params", str(self.td / "nope")], True,
                                    self.env)[1], "generic_missing")


class G2Ask(Base):
    def fake_stage(self, out):
        def f(exe, argv, pin, review, dest, env=None, run=None):
            self.staged_into = dest
            return out if not callable(out) else out(dest)
        return mock.patch.object(C, "stage_program", f)

    def inbox(self):
        d = C.inbox_dir(self.env)
        return sorted(d.iterdir()) if d.is_dir() else []

    def test_g2_checks_before_the_inbox(self):
        ask = lambda **kw: C.new_request(self.argv, "刪站 s1", strong=True, env=self.env, **kw)  # noqa: E731
        self.assertEqual(ask(pin="AB" * 32, review=str(self.review)), (None, "generic_pin"))
        self.assertEqual(ask(pin="ab" * 31, review=str(self.review)), (None, "generic_pin"))
        self.assertEqual(ask(pin=PIN, review=str(self.td / "nope.md")), (None, "generic_review"))
        self.assertEqual(ask(pin=PIN, review="REVIEW-x.md"), (None, "generic_review"))
        with mock.patch.dict(self.env, {"AIB_CONSENT_RUN": str(self.td / "missing")}):
            self.assertEqual(ask(pin=PIN, review=str(self.review)), (None, "consent_run_missing"))
        detail = {}
        with self.fake_stage({"ok": False, "why": "program_pin_mismatch", "line": 0}):
            self.assertEqual(ask(pin=PIN, review=str(self.review), detail=detail), (None, "program_pin_mismatch"))
        self.assertEqual(detail["stage"]["why"], "program_pin_mismatch")
        self.assertFalse(Path(self.staged_into).exists(), "the trial staging folder is left behind")
        self.assertEqual(self.inbox(), [])
        # pin / review on another kind of command
        self.assertEqual(C.new_request(["aib", "now", "x", "1/2"], "s", env=self.env, pin=PIN)[1],
                         "generic_args_not_generic")

    def test_g2_accepted(self):
        with self.fake_stage(lambda dest: self.stage_out(dest)):
            req, why = C.new_request(self.argv, "刪站 s1", strong=True, env=self.env, pin=PIN,
                                     review=str(self.review))
        self.assertEqual(why, "")
        files = self.inbox()
        self.assertEqual(len(files), 1)
        saved = json.loads(files[0].read_text())
        self.assertEqual((saved["pin"], saved["review"], saved["argv"]), (PIN, str(self.review), self.argv))


class G3StageOutput(Base):
    def run_with(self, out, rc=0):
        def run(cmd, **kw):
            self.cmd = cmd
            return subprocess.CompletedProcess(cmd, rc, json.dumps(out) + "\n", "")
        return run

    def test_g3_only_matching_output(self):
        dest = self.td / "stage"
        dest.mkdir(mode=0o700)
        good = self.stage_out(dest)
        r = C.stage_program(str(self.exe), self.argv, PIN, str(self.review), str(dest), self.env,
                            run=self.run_with(good))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self.cmd[:2], [str(self.exe), "stage"])
        self.assertIn("--pin", self.cmd)
        self.assertEqual(self.cmd[self.cmd.index("--dir") + 1], str(dest))
        bad = [
            (self.stage_out(dest, program=dict(good["program"], sha256="ef" * 32)), 0),
            (self.stage_out(dest, program=dict(good["program"], copy="/tmp/x.sh", argv=["/bin/bash", "/tmp/x.sh"])), 0),
            (self.stage_out(dest, program=dict(good["program"], argv=["/bin/sh", good["program"]["copy"]])), 0),
            (self.stage_out(dest, params=dict(good["params"], sha256="short")), 0),
            (self.stage_out(dest, params=dict(good["params"], copy="/tmp/params.env")), 0),
        ]
        for out, rc in bad:
            r = C.stage_program(str(self.exe), self.argv, PIN, str(self.review), str(dest), self.env,
                                run=self.run_with(out, rc))
            self.assertEqual(r, {"ok": False, "why": "stage_output"}, out)
        r = C.stage_program(str(self.exe), self.argv, PIN, str(self.review), str(dest), self.env,
                            run=self.run_with(good, 1))
        self.assertEqual(r["why"], "stage_rc:1")
        r = C.stage_program(str(self.exe), self.argv, PIN, str(self.review), str(dest), self.env,
                            run=self.run_with({"ok": False, "why": "params_key_undeclared", "line": 3}, 1))
        self.assertEqual((r["ok"], r["why"], r["line"]), (False, "params_key_undeclared", 3))


class G4Decide(Base):
    def req(self):
        import time
        return {"id": "1-abc", "argv": self.argv, "summary": "刪站 s1", "seat": "ebb36", "strong": True,
                "t": int(time.time()), "pin": PIN, "review": str(self.review)}

    def decide(self, stage, box_ok=True, touch_ok=True):
        seen = {}

        def fake_stage(exe, argv, pin, review, dest, env=None, run=None):
            seen["dest"] = dest
            return stage(dest)

        def box(view, env=None):
            seen["view"] = view
            seen["block"] = C._program_block(view["program"], env)
            return {"ok": box_ok, "why": "ok" if box_ok else "cancel"}

        def touch(reason):
            seen["reason"] = reason
            return {"ok": touch_ok}
        with mock.patch.object(C, "stage_program", fake_stage):
            out = C.decide(self.req(), env=self.env, ask=touch, ask_strong_fn=box, available=True)
        return out, seen

    def test_g4_refused_by_stage(self):
        (result, detail, real, td), seen = self.decide(
            lambda d: {"ok": False, "why": "review_not_passed", "line": 1})
        self.assertEqual((result, detail["why"], real, td), ("not_allowed", "review_not_passed", None, None))
        self.assertFalse(Path(seen["dest"]).exists())
        self.assertNotIn("view", seen, "a dialog was shown for a refused program")

    def test_g4_unchecked_box(self):
        (result, detail, real, td), seen = self.decide(lambda d: self.stage_out(d), box_ok=False)
        self.assertEqual(result, "denied")
        self.assertFalse(Path(seen["dest"]).exists())
        self.assertNotIn("reason", seen)

    def test_g4_approved(self):
        (result, detail, real, td), seen = self.decide(lambda d: self.stage_out(d))
        self.addCleanup(shutil.rmtree, str(td), True)
        self.assertIsNone(result)
        self.assertEqual(real, ["/bin/bash", str(Path(td) / "site-remove.sh")])
        self.assertEqual(detail["staged"]["program_sha"], PIN)
        self.assertEqual(detail["staged"]["params_sha"], PSHA)
        self.assertEqual(detail["program"]["keys"], ["SITE"])
        self.assertEqual(detail["program"]["review"], "結論: 通過 頭: x")
        for part in (str(self.prog), PIN, PSHA, "SITE=s1", "ARCHIVE", "結論: 通過 頭: x"):
            self.assertIn(part, seen["block"])
        import approve_gate as AG
        entry = AG._lookup(seen["reason"])
        self.assertIsNotNone(entry)
        self.assertEqual(entry[1], "consent.sign_script")    # 高風險那一類,跟 SIGN 腳本同

    def test_g4_scroll_dialog(self):
        calls = []

        def checkbox(title, body, check, env=None, run=None, wait=None, intro=None):
            calls.append((body, intro))
            return {"ok": False, "why": "cancel"}
        view = dict(self.req(), program=self.stage_out(self.td))
        with mock.patch.object(C, "ask_checkbox", checkbox):
            C.ask_strong(view, env=self.env)
        body, intro = calls[0]
        self.assertIn("SITE=s1", body)
        self.assertIn("刪站 s1", intro)


class G5Execute(Base):
    def test_g5_flags_before_the_command(self):
        rec = {"id": "1-abc", "result": "exited", "rc": 0, "asks": 0, "tail": [], "remote": "not_confirmed"}
        calls = []

        def run(argv, **kw):
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, json.dumps(rec) + "\n", "")
        staged = {"program_copy": "/s/site-remove.sh", "program_sha": PIN, "params_copy": "/s/params.env",
                  "params_sha": PSHA}
        req = {"id": "1-abc", "argv": self.argv, "summary": "刪站 s1", "seat": "ebb36", "strong": True}
        d = C.execute("1-abc", req, ["/bin/bash", "/s/site-remove.sh"], env=self.env, run=run,
                      extra={"staged": staged, "program": {"sha256": PIN}})
        self.assertEqual(d["result"], "ok", d)
        self.assertNotIn("staged", d)
        argv = calls[0]
        cut = argv.index("--")
        for flag, val in (("--program-copy", "/s/site-remove.sh"), ("--program-sha", PIN),
                          ("--params-copy", "/s/params.env"), ("--params-sha", PSHA)):
            self.assertIn(flag, argv)
            self.assertLess(argv.index(flag), cut)
            self.assertEqual(argv[argv.index(flag) + 1], val)
        self.assertEqual(argv[cut + 1:], ["/bin/bash", "/s/site-remove.sh"])

    def test_g5_no_supervisor_no_run(self):
        ran = []
        with mock.patch.dict(self.env, {"AIB_CONSENT_RUN": str(self.td / "missing")}):
            d = C.execute("1-abc", {"id": "1-abc", "argv": self.argv, "summary": "s"}, ["/bin/bash", "x"],
                          env=self.env, run=lambda *a, **k: ran.append(a),
                          extra={"staged": {"program_copy": "x", "program_sha": PIN, "params_copy": "y",
                                            "params_sha": PSHA}})
        self.assertEqual((d["result"], d["why"], ran), ("failed", "consent_run_missing", []))   # 參數只有它會給


class G6Cli(Base):
    def test_g6_flags_together(self):
        import io
        from contextlib import redirect_stdout
        for argv in (["ask", "--strong", "--summary", "s", "--program", str(self.prog)],
                     ["ask", "--strong", "--summary", "s", "--params", str(self.params)],
                     ["ask", "--strong", "--summary", "s", "--program", str(self.prog), "--params", str(self.params),
                      "--pin", PIN, "--review", str(self.review), "--", "aib", "now", "x", "1/2"]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = C.main(argv, env=self.env)
            self.assertEqual(rc, 2, argv)
            self.assertEqual(json.loads(buf.getvalue())["why"], "generic_args")


@unittest.skipUnless(os.environ.get("AIB_CONSENT_RUN_TEST_BIN"), "set AIB_CONSENT_RUN_TEST_BIN to a built aib-consent-run")
class G7Real(Base):
    def test_g7_end_to_end(self):
        import hashlib
        import time
        env = dict(self.env, AIB_CONSENT_RUN=os.environ["AIB_CONSENT_RUN_TEST_BIN"])
        os.chmod(str(self.home), 0o700)
        pin = hashlib.sha256(self.prog.read_bytes()).hexdigest()
        self.review.write_text("結論: 通過 頭: %s H/M/L/N: 0/0/0/0 範圍: 全文\n" % pin)
        req, why = C.new_request(self.argv, "刪站 s1", strong=True, env=env, pin=pin, review=str(self.review))
        self.assertEqual(why, "", req)
        req["t"] = int(time.time())
        result, detail, real, td = C.decide(req, env=env, ask=lambda r: {"ok": True},
                                            ask_strong_fn=lambda v, env=None: {"ok": True}, available=True)
        self.assertIsNone(result, detail)
        try:
            # the parameter file changed after the dialog: the run uses the copy that was shown
            self.params.write_text("SITE=other\n")
            C.running_dir(env).mkdir(parents=True, exist_ok=True)
            d = C.execute(req["id"], req, real, env=env, extra=detail)
        finally:
            shutil.rmtree(td, ignore_errors=True)
        self.assertEqual(d["result"], "ok", d)
        self.assertIn("site=s1", Path(d["log"]).read_text())


class Knives(unittest.TestCase):
    def _knife(self, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(ROOT, "src/cockpit/consent.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), cell], cwd=str(root),
                            max_procs=40, timeout=120)
        self.assertIsNone(r.tripped)
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_kg1_decide_ignores_stage(self):
        self._knife("        if not st.get(\"ok\"):\n            shutil.rmtree(td, ignore_errors=True)\n            why = st",
                    "        if False:\n            shutil.rmtree(td, ignore_errors=True)\n            why = st",
                    "G4Decide")

    def test_kg2_supervise_without_copies(self):
        self._knife("        if staged.get(\"params_copy\"):\n            out += [\"--params-copy\"",
                    "        if False:\n            out += [\"--params-copy\"", "G5Execute")

    def test_kg3_stage_sha_not_pin(self):
        self._knife("and prog[\"sha256\"] == pin", "and (prog[\"sha256\"] == pin or True)", "G3StageOutput")


if __name__ == "__main__":
    unittest.main()
