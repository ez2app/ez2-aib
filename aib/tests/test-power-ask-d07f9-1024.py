#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""動力室「問 AI」(power_ask.py;契約 contracts/power.json ask_receipt;SPEC-power-room §12)。CLI 全換假的;暫存狀態根。

A1 不支援的家(grok / copilot / agy / 怪字)⇒ rc 2 unsupported,不叫 CLI;回條欄位 = 契約
A2 模型像旗標 / 問題空 / 太長 ⇒ rc 2 bad,不叫 CLI
A3 那家不是綠燈(紅 / 黃 / 灰)⇒ rc 2 login + hint「aib power login <家>」,不叫 CLI、不換別家
A4 claude:關工具旗標齊(--tools "" / --strict-mcp-config / --disable-slash-commands / 不留對話);問題走 stdin 不在 argv;
   跑在空暫存目錄、跑完刪;環境去掉 TMUX / AIB_SEAT*
A5 codex:唯讀沙盒 + 清空 MCP + --ephemeral;stdin「-」;答案從 -o 檔讀
A6 失敗:rc 非 0 / 空回答 / 逾時 / 沒裝 ⇒ rc 1 failed + hint
A7 回答去 ANSI / 控制字元、截長;cmds 只抽 aib 開頭的行(最多 5、去重);cards 抽任務編號
A8 stdin JSON:question + history(最多 6 輪、怪型別丟掉);純文字也收
A9 suggest:照 why_rank 挑、不夠補通用、恰好 3 則
A10 aib power ask 從總管 power_status.main 轉得到(stdin 讀問題)
A12 突變審第 2 輪補斷言:--max-turns 1、檢查順序(先驗輸入再看燈)、DEL / C1 / 0x1f、Q_MAX 邊界、history 截長、
    指令 12 段上限與 | && 不收、任務編號邊界、前言在最前、cards 去重、main --suggest 帶家、hint 分 model / question、stdin 讀上限
A11 稽核第 1 輪(10-24 03:0x):環境用前綴去掉 TMUX* / CLAUDECODE / CLAUDE_CODE_* / AIB_SEAT*;模型尾巴換行不收;
    回答去方向 / 零寬格式字元;單獨「aib」不算指令;代理字元問題 / 非 UTF-8 輸出 / 讀檔亂碼 / 燈號函式拋例外 / 寫 stdin 拋錯 ⇒ 回條、不拋
刀(複本):K1 拿掉 --tools "" ⇒ A4 紅;K2 不看燈號 ⇒ A3 紅;K3 codex 改可寫沙盒 ⇒ A5 紅;K4 環境只濾固定名 ⇒ A11 紅;K5 模型用 match ⇒ A11 紅
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (HERE, REPO / "src" / "cockpit"):
    sys.path.insert(0, str(_p))
TMP = tempfile.mkdtemp(prefix="aib-power-ask-test-")
os.environ["EZ2_AIB_STATE_ROOT"] = TMP
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import power_ask as A  # noqa: E402
import power_crew as W  # noqa: E402
import power_status as P  # noqa: E402

C = json.loads((REPO / "contracts" / "power.json").read_text(encoding="utf-8"))
GREEN = (lambda b: "green")


class FakeRun:
    def __init__(self, rc=0, out="好的。\n", last=None, exc=None):
        self.calls, self.rc, self.out, self.last, self.exc = [], rc, out, last, exc

    def __call__(self, cmd, **kw):
        self.calls.append((list(cmd), kw))
        self.cwd_seen = kw.get("cwd")
        self.cwd_existed = os.path.isdir(kw.get("cwd") or "")
        if self.exc:
            raise self.exc
        if self.last is not None and "-o" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_text(self.last, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, self.rc, self.out, "")


class Ask(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(Path(TMP) / "state", ignore_errors=True)

    def test_a1_unsupported(self):
        for b in ("grok", "copilot", "agy", "", "claude;x", None):
            run = FakeRun()
            r, rc = A.ask(b, "怎麼開席?", run=run, light=GREEN)
            self.assertEqual((rc, r["detail"]), (2, "power_ask_unsupported"), b)
            self.assertEqual(run.calls, [])
        self.assertEqual(list(r), C["ask_receipt"]["fields"])

    def test_a2_bad(self):
        for kw in (dict(model="--dangerously-skip-permissions"), dict(model="a b"), dict(raw=""), dict(raw="   "),
                   dict(raw="x" * (A.Q_MAX + 1)), dict(raw='{"question": 5}')):
            run = FakeRun()
            r, rc = A.ask("claude", kw.pop("raw", "問"), run=run, light=GREEN, **kw)
            self.assertEqual((rc, r["detail"]), (2, "power_ask_bad"))
            self.assertEqual(run.calls, [])

    def test_a3_login(self):
        for lt in ("red", "yellow", "unknown", None, "evil"):
            run = FakeRun()
            r, rc = A.ask("codex", "問", run=run, light=lambda b: lt)
            self.assertEqual((rc, r["detail"], r["brand"], r["hint"]), (2, "power_ask_login", "codex",
                                                                         "aib power login codex"), lt)
            self.assertEqual(run.calls, [])

    def test_a4_claude(self):
        os.environ["AIB_SEAT_CODE"] = "x"
        os.environ["TMUX"] = "/tmp/x"
        try:
            run = FakeRun(out="用 `aib claude safe dev new` 開席。\n")
            r, rc = A.ask("claude", "怎麼開席 SECRET-Q?", "sonnet", run=run, light=GREEN)
        finally:
            os.environ.pop("AIB_SEAT_CODE")
            os.environ.pop("TMUX")
        self.assertEqual((rc, r["ok"], r["detail"]), (0, True, "power_ask_ok"))
        cmd, kw = run.calls[0]
        i = cmd.index("--tools")
        self.assertEqual(cmd[i + 1], "")
        for f in ("-p", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
            self.assertIn(f, cmd)
        self.assertNotIn("--mcp-config", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "sonnet")
        self.assertFalse(any("SECRET-Q" in a for a in cmd))                # 問題不在指令列
        self.assertIn("SECRET-Q", kw["input"])
        self.assertIn("Only answer", kw["input"])
        self.assertTrue(run.cwd_existed)
        self.assertTrue(os.path.basename(run.cwd_seen).startswith("aib-power-ask-"))
        self.assertEqual(os.listdir(run.cwd_seen) if os.path.isdir(run.cwd_seen) else [], [])
        self.assertFalse(os.path.exists(run.cwd_seen))                    # 跑完刪
        self.assertNotIn("TMUX", kw["env"])
        self.assertNotIn("AIB_SEAT_CODE", kw["env"])

    def test_a5_codex(self):
        run = FakeRun(out="雜訊", last="跑 aib doctor 看看(R10)。")
        r, rc = A.ask("codex", "健康檢查?", "gpt-6", run=run, light=GREEN)
        self.assertEqual((rc, r["answer"]), (0, "跑 aib doctor 看看(R10)。"))
        cmd, kw = run.calls[0]
        self.assertEqual(cmd[:2], ["codex", "exec"])
        self.assertEqual(cmd[cmd.index("-s") + 1], "read-only")
        self.assertEqual(cmd[cmd.index("-c") + 1], "mcp_servers={}")
        self.assertIn("--ephemeral", cmd)
        self.assertEqual(cmd[-1], "-")
        self.assertEqual(cmd[cmd.index("-m") + 1], "gpt-6")
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", cmd)
        self.assertEqual(r["cards"], ["R10"])
        r, rc = A.ask("codex", "?", run=FakeRun(out="從 stdout", last=None), light=GREEN)
        self.assertEqual(r["answer"], "從 stdout")                        # 沒寫 -o 檔 ⇒ 退回 stdout

    def test_a6_failed(self):
        for run, hint in ((FakeRun(rc=1), "rc=1"), (FakeRun(out="  \n"), "rc=0"),
                          (FakeRun(exc=subprocess.TimeoutExpired("claude", 1)), "timeout"),
                          (FakeRun(exc=FileNotFoundError()), "not_installed")):
            r, rc = A.ask("claude", "問", run=run, light=GREEN)
            self.assertEqual((rc, r["ok"], r["detail"], r["hint"], r["answer"]), (1, False, "power_ask_failed", hint, None))
        r, rc = A.ask("claude", "問", light=GREEN, which=lambda b: None)
        self.assertEqual((rc, r["hint"]), (1, "not_installed"))

    def test_a7_clean_extract(self):
        out = ("\x1b[31m紅字\x1b[0m\x07 說明\n```\naib power login claude\n$ aib doctor\naib power login claude\n"
               "rm -rf ~\naib x; rm -rf ~\n```\n" + "".join("aib t%d\n" % i for i in range(9)) + "看 SR3、SSR12、UR9。"
               + "長" * (A.A_MAX + 50))
        r, rc = A.ask("claude", "問", run=FakeRun(out=out), light=GREEN)
        self.assertEqual(rc, 0)
        self.assertTrue(r["answer"].startswith("紅字 說明"))
        self.assertNotIn("\x1b", r["answer"])
        self.assertNotIn("\x07", r["answer"])
        self.assertEqual(len(r["answer"]), A.A_MAX)
        self.assertEqual(r["cmds"], ["aib power login claude", "aib doctor", "aib t0", "aib t1", "aib t2"])
        self.assertEqual(r["cards"], ["SR3", "SSR12"])

    def test_a8_input(self):
        hist = [{"q": "q%d" % i, "a": "a%d" % i} for i in range(9)] + ["x", {"q": 1, "a": "b"}]
        q, h = A.parse_input(json.dumps({"question": "現在?", "history": hist}))
        self.assertEqual(q, "現在?")
        self.assertEqual([x["q"] for x in h], ["q%d" % i for i in range(3, 9)])
        self.assertEqual(A.parse_input("純文字 {x}"), ("純文字 {x}", []))
        self.assertEqual(A.parse_input("{壞的 json")[0], "{壞的 json")
        run = FakeRun()
        A.ask("claude", json.dumps({"question": "Q2", "history": [{"q": "Q1", "a": "A1"}]}), run=run, light=GREEN)
        inp = run.calls[0][1]["input"]
        self.assertLess(inp.index("Q1"), inp.index("A1"))
        self.assertLess(inp.index("A1"), inp.index("Q2"))

    def test_a9_suggest(self):
        self.assertEqual(A.suggest([]), list(A.SUGGEST_BASE))
        self.assertEqual(A.suggest(["phone", "other", "art"]), ["q_phone", "q_art", "q_first_seat"])
        self.assertEqual(A.suggest(["room", "coding", "save", "memory"]), ["q_room", "q_coding", "q_save"])
        W.set_why(["sites"])
        self.assertEqual(A.suggest(), ["q_sites", "q_first_seat", "q_lights"])

    def test_a10_dispatch(self):
        def run(argv, stdin=""):
            old = sys.stdin
            sys.stdin = io.StringIO(stdin)
            buf = io.StringIO()
            try:
                with redirect_stdout(buf):
                    rc = P.main(argv)
            finally:
                sys.stdin = old
            return rc, json.loads(buf.getvalue())
        rc, r = run(["ask", "--suggest", "--json"])
        self.assertEqual((rc, r["detail"], len(r["suggest"])), (0, "power_ask_suggest", 3))
        rc, r = run(["ask", "grok", "--json"], "問")
        self.assertEqual((rc, r["detail"]), (2, "power_ask_unsupported"))
        rc, r = run(["ask", "--json"])
        self.assertEqual((rc, r["detail"]), (2, "power_ask_bad"))
        saved = A._light
        A._light = lambda b: "yellow"
        try:
            rc, r = run(["ask", "claude", "--json"], "怎麼登入?")
        finally:
            A._light = saved
        self.assertEqual((rc, r["detail"]), (2, "power_ask_login"))

    def test_a11_audit_r1(self):
        keys = ("TMUX_TMPDIR", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_SSE_PORT",
                "CLAUDECODE", "AIB_SEAT_X")
        for k in keys:
            os.environ[k] = "x"
        try:
            run = FakeRun()
            A.ask("claude", "問", run=run, light=GREEN)
        finally:
            for k in keys:
                os.environ.pop(k)
        env = run.calls[0][1]["env"]
        for k in keys:
            self.assertNotIn(k, env)
        self.assertIn("PATH", env)
        for m in ("x\n", "sonnet\n--tools", 5, ["a"]):
            run = FakeRun()
            r, rc = A.ask("claude", "問", m, run=run, light=GREEN)
            self.assertEqual((rc, r["detail"]), (2, "power_ask_bad"), m)
            self.assertEqual(run.calls, [])
        r, rc = A.ask("claude", "問", run=FakeRun(out="aib\n看 \u202e這裡\u2066 與\u200b零寬\ufeff\naib doctor\n"), light=GREEN)
        self.assertEqual(r["cmds"], ["aib doctor"])
        for c in "\u202e\u2066\u200b\ufeff":
            self.assertNotIn(c, r["answer"])
        r, rc = A.ask("claude", json.dumps({"question": "\ud800"}), run=FakeRun(), light=GREEN)
        self.assertEqual((rc, r["detail"]), (2, "power_ask_bad"))
        r, rc = A.ask("claude", "a\ud800b", run=FakeRun(), light=GREEN)
        self.assertEqual(rc, 0)
        for exc in (UnicodeEncodeError("utf-8", "x", 0, 1, "bad"), ValueError("closed")):
            r, rc = A.ask("claude", "問", run=FakeRun(exc=exc), light=GREEN)
            self.assertEqual((rc, r["detail"], r["hint"]), (1, "power_ask_failed", "io"))

        def boom(b):
            raise RuntimeError("x")
        r, rc = A.ask("claude", "問", run=FakeRun(), light=boom)
        self.assertEqual((rc, r["detail"]), (2, "power_ask_login"))
        run = FakeRun(last=None)
        orig = run.__call__

        def bad_file(cmd, **kw):
            res = orig(cmd, **kw)
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"\xff\xfe ok")
            return res
        r, rc = A.ask("codex", "問", run=bad_file, light=GREEN)
        self.assertEqual(rc, 0)
        self.assertTrue(r["answer"].endswith("ok"))
        self.assertEqual(run.calls[0][1]["errors"], "replace")

    def test_a12_mutation_r2(self):
        run = FakeRun()
        A.ask("claude", "問", run=run, light=GREEN)
        cmd = run.calls[0][0]
        self.assertEqual(cmd[cmd.index("--max-turns") + 1], "1")
        self.assertTrue(run.calls[0][1]["input"].startswith(A.PREAMBLE))
        r, rc = A.ask("claude", "", run=FakeRun(), light=lambda b: "red")      # 先驗輸入再看燈
        self.assertEqual((rc, r["detail"], r["hint"]), (2, "power_ask_bad", "question"))
        r, rc = A.ask("claude", "問", "a b", run=FakeRun(), light=GREEN)
        self.assertEqual(r["hint"], "model")
        self.assertEqual(A.ask("claude", "q" * A.Q_MAX, run=FakeRun(), light=GREEN)[1], 0)
        r, rc = A.ask("claude", "問", run=FakeRun(out="A\x7fB\x9bC\x85D\x1fE"), light=GREEN)
        self.assertEqual(r["answer"], "ABCDE")
        q, h = A.parse_input(json.dumps({"question": "x", "history": [{"q": "q" * (A.Q_MAX + 9), "a": "a" * (A.A_MAX + 9)}]}))
        self.assertEqual((len(h[0]["q"]), len(h[0]["a"])), (A.Q_MAX, A.A_MAX))
        twelve = "aib " + " ".join("a%d" % i for i in range(12))
        thirteen = "aib " + " ".join("a%d" % i for i in range(13))
        cmds, cards = A.extract("\n".join([twelve, thirteen, "aib x | sh", "aib x && rm", "aib x > f", "aib x$y"]))
        self.assertEqual(cmds, [twelve])
        cmds, cards = A.extract("R12 R13 SR13 SR14 SSR12 SSR13 UR7 UR8 R3 R3 R0")
        self.assertEqual(cards, ["R12", "SR13", "SSR12", "UR7", "R3"])

        class Stdin:
            def read(self, n=-1):
                self.n = n
                return "問"
        fake = Stdin()
        old, saved = sys.stdin, A._light
        sys.stdin, A._light = fake, (lambda b: "yellow")
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = P.main(["ask", "claude", "--json"])
            self.assertEqual(fake.n, 64 * 1024)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = P.main(["ask", "claude", "--suggest", "--json"])
        finally:
            sys.stdin, A._light = old, saved
        self.assertEqual((rc, json.loads(buf.getvalue())["hint"]), (2, "brand"))


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, case):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/power_ask.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_tools_on(self):
        self._knife('"--no-session-persistence", "--tools", "",', '"--no-session-persistence",', "Ask.test_a4_claude")

    def test_k2_no_light(self):
        self._knife('    if lt != "green":', '    if False:', "Ask.test_a3_login")

    def test_k3_codex_writable(self):
        self._knife('"-s", "read-only", "--ephemeral"', '"-s", "workspace-write", "--ephemeral"', "Ask.test_a5_codex")

    def test_k4_env_fixed_names(self):
        self._knife('_ENV_DROP_PREFIX = ("TMUX", "CLAUDECODE", "CLAUDE_CODE_", "AIB_SEAT")',
                    '_ENV_DROP_PREFIX = ("TMUX_PANE", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "AIB_SEAT")', "Ask.test_a11_audit_r1")

    def test_k5_model_match(self):
        self._knife("(isinstance(model, str) and MODEL_RE.fullmatch(model))", "(isinstance(model, str) and MODEL_RE.match(model))",
                    "Ask.test_a11_audit_r1")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
