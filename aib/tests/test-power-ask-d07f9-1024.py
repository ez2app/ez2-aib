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
A13 codex 關工具(10-24 03:2x 實跑發現 mcp_servers={} 只合併不清空):關外掛 / apps / shell / 電腦 / 瀏覽器,列 MCP、逐個停用、
    重列確認全關;還有開著的 / 名稱怪(含引號、點、空白)/ 列不出來 / 讀不懂 ⇒ unsupported mcp_not_disabled、不叫 exec
A12 突變審第 2 輪補斷言:--max-turns 1、檢查順序(先驗輸入再看燈)、DEL / C1 / 0x1f、Q_MAX 邊界、history 截長、
    指令 12 段上限與 | && 不收、任務編號邊界、前言在最前、cards 去重、main --suggest 帶家、hint 分 model / question、stdin 讀上限
A14 第 3 輪(10-25 突變 + 稽核):codex 關看圖 / 生圖 / 多代理 / hook / 網路搜尋…並用 features list 重列確認(profile 蓋回 /
    列不出 ⇒ 不叫);MCP 清單的 enabled 不是布林 false 一律當開著要停用;mcp list rc 非 0 不信;列 MCP / features 的子行程
    也去席位環境、stdin 空、有逾時;主呼叫逾時有傳;-o 檔讀上限;claude 帶 --settings disableAllHooks、--output-format text;
    codex --color never;暫存目錄在系統暫存區;前言禁令三句都在;變體選擇符 / 點字空白去掉;run_group 逾時 / 結束砍整組
    (孫行程不留)、SIGTERM ⇒ finally 照跑(暫存目錄刪、孫行程收)
A16 #342 F1(@abb89 裁 b 加強):ANTHROPIC_* / OPENAI_* / CODEX_API_KEY 子行程拿不到;判燈用同一份環境現量
    (不讀共用快取)⇒ 拿掉後沒登入就是 login、不是綠燈卻失敗;燈號順序 yellow / red / green / unknown
A11 稽核第 1 輪(10-24 03:0x):環境用前綴去掉 TMUX* / CLAUDECODE / CLAUDE_CODE_* / AIB_SEAT*;模型尾巴換行不收;
    回答去方向 / 零寬格式字元;單獨「aib」不算指令;代理字元問題 / 非 UTF-8 輸出 / 讀檔亂碼 / 燈號函式拋例外 / 寫 stdin 拋錯 ⇒ 回條、不拋
刀(複本):K1 拿掉 --tools "" ⇒ A4 紅;K2 不看燈號 ⇒ A3 紅;K3 codex 改可寫沙盒 ⇒ A5 紅;K4 環境只濾固定名 ⇒ A11 紅;K5 模型用 match ⇒ A11 紅;K6 停用後不重列確認 ⇒ A13 紅;K7 不關外掛 ⇒ A5 紅;K8 不重列 features ⇒ A14 紅;
K9 run_group 不砍整組 ⇒ A14 紅;K10 不關網路搜尋 ⇒ A5 紅;K11 enabled 改成 is True 才算開 ⇒ A14 紅;
K12 不拿掉 API key ⇒ A16 紅;K13 判燈帶完整環境 ⇒ A16 紅
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (HERE, REPO / "src" / "cockpit"):
    sys.path.insert(0, str(_p))
TMP = tempfile.mkdtemp(prefix="aib-power-ask-test-")
os.environ["EZ2_AIB_STATE_ROOT"] = TMP
# quota() reads a separate usage directory, not EZ2_AIB_STATE_ROOT. Never read
# the developer's live quota cache while exercising the fake login probes.
_OLD_USAGE_DIR = os.environ.get("EZ2_USAGE_DIR")
os.environ["EZ2_USAGE_DIR"] = os.path.join(TMP, "usage")
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import power_ask as A  # noqa: E402
import power_crew as W  # noqa: E402
import power_status as P  # noqa: E402

C = json.loads((REPO / "contracts" / "power.json").read_text(encoding="utf-8"))
GREEN = (lambda b: "green")


# 假 codex 的 MCP(名稱, 預設開著, 外掛帶的):照 10-24 03:2x 本機實測行為 —— features.plugins=false 拿掉外掛帶的;
# -c mcp_servers.<名>.enabled=false 停用;停用一台不存在的(含加了引號的名字)⇒ rc 1「invalid transport」
MCP = (("node_repl", True, False), ("openaiDeveloperDocs", True, False), ("cua_repl", True, True),
       ("computer-use", False, False))


def mcp_list(cmd, servers):
    opts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"]
    rows = [(n, on) for n, on, plugin in servers if not (plugin and "features.plugins=false" in opts)]
    names = {n for n, _ in rows}
    for o in opts:
        if o.startswith("mcp_servers.") and o.endswith(".enabled=false"):
            name = o[len("mcp_servers."):-len(".enabled=false")]
            if name not in names:
                return subprocess.CompletedProcess(cmd, 1, "", "invalid transport")
            rows = [(n, False if n == name else on) for n, on in rows]
    return subprocess.CompletedProcess(cmd, 0, json.dumps([{"name": n, "enabled": on} for n, on in rows]), "")


def features_list(cmd, stuck=()):
    """假 codex features list:LOCK_FEATURES + unified_exec,帶了 -c features.<k>=false 就 false;stuck 的照 true(profile 蓋回)。"""
    opts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"]
    rows = ["%-40s stable             %s" % (k, "false" if "features.%s=false" % k in opts and k not in stuck else "true")
            for k in A.LOCK_FEATURES + ("unified_exec",)]
    return subprocess.CompletedProcess(cmd, 0, "\n".join(rows) + "\n", "")


class FakeRun:
    def __init__(self, rc=0, out="好的。\n", last=None, exc=None, mcp=MCP, stuck=()):
        self.calls, self.rc, self.out, self.last, self.exc, self.mcp, self.mcp_calls = [], rc, out, last, exc, mcp, []
        self.stuck, self.feat_calls = stuck, []

    def __call__(self, cmd, **kw):
        if cmd[:1] == ["codex"] and cmd[-3:] == ["mcp", "list", "--json"]:
            self.mcp_calls.append((list(cmd), kw))
            return mcp_list(cmd, self.mcp)
        if cmd[:1] == ["codex"] and cmd[-2:] == ["features", "list"]:
            self.feat_calls.append((list(cmd), kw))
            return features_list(cmd, self.stuck)
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
        shutil.rmtree(Path(TMP) / "usage", ignore_errors=True)

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
        opts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"]
        for f in ("features.plugins=false", "features.apps=false", "features.shell_tool=false",
                  "features.computer_use=false", "features.browser_use=false", "web_search=disabled",
                  "features.view_image=false", "features.image_generation=false", "features.multi_agent=false",
                  "features.hooks=false", "mcp_servers.node_repl.enabled=false",
                  "mcp_servers.openaiDeveloperDocs.enabled=false"):
            self.assertIn(f, opts)
        self.assertEqual(len(run.feat_calls), 1)                           # features 也重列確認，用的是實際帶的那組
        fc = run.feat_calls[0][0]
        self.assertEqual([fc[i + 1] for i, a in enumerate(fc) if a == "-c"], opts)
        self.assertNotIn("mcp_servers.cua_repl.enabled=false", opts)       # 外掛帶的關外掛就沒了，不另停(會 invalid transport)
        self.assertEqual(len(run.mcp_calls), 2)                            # 列 → 停用 → 重列確認
        checked = run.mcp_calls[1][0]
        self.assertEqual([checked[i + 1] for i, a in enumerate(checked) if a == "-c"], opts)   # 重列確認的就是實際帶的那組
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
            if "-o" in cmd:
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

    def test_a13_codex_lockdown(self):
        run = FakeRun()
        A.ask("codex", "問", run=run, light=GREEN)
        self.assertEqual(len(run.calls), 1)
        for servers, why in (((("we.ird", True, False),), "dot"), (((' "q"', True, False),), "quote"),
                             (((("x" * 65), True, False),), "long")):
            run = FakeRun(mcp=servers)
            r, rc = A.ask("codex", "問", run=run, light=GREEN)
            self.assertEqual((rc, r["detail"], r["hint"]), (2, "power_ask_unsupported", "mcp_not_disabled"), why)
            self.assertEqual(run.calls, [], why)

        class Stubborn(FakeRun):                                            # 停用了還說開著 ⇒ 不叫
            def __call__(self, cmd, **kw):
                if cmd[-3:] == ["mcp", "list", "--json"]:
                    self.mcp_calls.append(cmd)
                    return subprocess.CompletedProcess(cmd, 0, json.dumps([{"name": "node_repl", "enabled": True}]), "")
                return super().__call__(cmd, **kw)
        for run in (Stubborn(),):
            r, rc = A.ask("codex", "問", run=run, light=GREEN)
            self.assertEqual((rc, r["hint"], run.calls), (2, "mcp_not_disabled", []))
        for out in ("not json", "{}", "[1]", ""):
            class Odd(FakeRun):
                def __call__(self, cmd, _out=out, **kw):
                    if cmd[-3:] == ["mcp", "list", "--json"]:
                        return subprocess.CompletedProcess(cmd, 0, _out, "")
                    return super().__call__(cmd, **kw)
            run = Odd()
            r, rc = A.ask("codex", "問", run=run, light=GREEN)
            self.assertEqual((rc, r["hint"], run.calls), (2, "mcp_not_disabled", []), out)
        run = FakeRun(mcp=())                                               # 本來就沒有 MCP ⇒ 照叫
        self.assertEqual(A.ask("codex", "問", run=run, light=GREEN)[1], 0)
        self.assertEqual(A.codex_lockdown(lambda *a, **k: (_ for _ in ()).throw(OSError("x"))), None)

    def test_a14_round3(self):
        # features 重列:profile 蓋回一個 / rc 非 0 / 讀不懂 / 少列一個 ⇒ 不叫
        for stuck in (("view_image",), ("shell_tool",), ("hooks",)):
            run = FakeRun(stuck=stuck)
            r, rc = A.ask("codex", "問", run=run, light=GREEN)
            self.assertEqual((rc, r["detail"], r["hint"], run.calls), (2, "power_ask_unsupported", "mcp_not_disabled", []),
                             stuck)
        for res in ((1, "view_image stable false\n"), (0, ""), (0, "garbage"),
                    (0, "\n".join("%s s false" % k for k in A.LOCK_FEATURES[:-1]))):
            class F(FakeRun):
                def __call__(self, cmd, _res=res, **kw):
                    if cmd[-2:] == ["features", "list"]:
                        return subprocess.CompletedProcess(cmd, _res[0], _res[1], "")
                    return super().__call__(cmd, **kw)
            run = F()
            self.assertEqual((A.ask("codex", "問", run=run, light=GREEN)[1], run.calls), (2, []), res)
        # M4:enabled 不是布林 false(沒這欄 / "true" / 1 / "false" 字串)⇒ 當開著、要停用
        for row in ({"name": "x1"}, {"name": "x2", "enabled": "true"}, {"name": "x3", "enabled": 1},
                    {"name": "x4", "enabled": "false"}):
            self.assertEqual(A._mcp_enabled([], lambda c, _r=row, **k: subprocess.CompletedProcess(c, 0, json.dumps([_r]), "")),
                             [row["name"]])
        # M5:mcp list rc 非 0 就算 stdout 是合法 JSON 也不信
        run = FakeRun()
        bad = lambda c, **k: (run.mcp_calls.append((c, k)), subprocess.CompletedProcess(c, 1, "[]", ""))[1]
        self.assertIsNone(A.codex_lockdown(bad))
        # M7 / M8 / M9:列 MCP / features 的子行程也去席位環境、stdin 空、有逾時
        keys = ("TMUX", "AIB_SEAT_X", "CLAUDE_CODE_SSE_PORT")
        for k in keys:
            os.environ[k] = "x"
        try:
            run = FakeRun()
            A.ask("codex", "問", run=run, light=GREEN)
        finally:
            for k in keys:
                os.environ.pop(k)
        self.assertEqual(len(run.calls), 1)
        for c, kw in run.mcp_calls + run.feat_calls:
            for k in keys:
                self.assertNotIn(k, kw["env"], c)
            self.assertIs(kw["stdin"], subprocess.DEVNULL)
            self.assertEqual(kw["timeout"], A.MCP_LIST_TIMEOUT_S)
        # M20:主呼叫逾時有傳到;M19:暫存目錄在系統暫存區、不在狀態根
        run = FakeRun()
        A.ask("claude", "問", run=run, light=GREEN, timeout=7)
        self.assertEqual(run.calls[0][1]["timeout"], 7)
        run = FakeRun()
        A.ask("claude", "問", run=run, light=GREEN)
        self.assertEqual(run.calls[0][1]["timeout"], A.TIMEOUT_S)
        self.assertEqual(os.path.dirname(run.cwd_seen), tempfile.gettempdir())
        self.assertFalse(run.cwd_seen.startswith(os.environ["EZ2_AIB_STATE_ROOT"]))
        # M10 / M11:前言三句禁令都在;M17 / M18 / F5:旗標
        inp = run.calls[0][1]["input"]
        for s in ("Never ask for or repeat passwords, keys, tokens", "cannot run commands", "never claim you did"):
            self.assertIn(s, inp)
        cmd = run.calls[0][0]
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "text")
        self.assertEqual(json.loads(cmd[cmd.index("--settings") + 1]), {"disableAllHooks": True})
        run = FakeRun()
        A.ask("codex", "問", run=run, light=GREEN)
        cmd = run.calls[0][0]
        self.assertEqual(cmd[cmd.index("--color") + 1], "never")
        # M14:-o 檔讀上限
        sizes = []
        real_open = open

        def spy_open(*a, **k):
            f = real_open(*a, **k)
            if str(a[0]).endswith("last.txt"):
                orig = f.read
                f.read = lambda n=-1: (sizes.append(n), orig(n))[1]
            return f
        A.open = spy_open                                                  # 只換 power_ask 模組裡的 open(aib test 的讀檔追蹤不算)
        try:
            r, rc = A.ask("codex", "問", run=FakeRun(last="x" * (A.A_MAX * 10)), light=GREEN)
        finally:
            del A.open
        self.assertEqual((sizes, len(r["answer"])), ([A.A_MAX * 4], A.A_MAX))
        # F4:變體選擇符 / 點字空白 / 蒙古文變體選擇符去掉
        r, rc = A.ask("claude", "問", run=FakeRun(out="a\ufe0fb\U000e0100c\u2800d\u180be"), light=GREEN)
        self.assertEqual(r["answer"], "abcde")

    def test_a16_same_env_no_api_key(self):
        keys = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL",
                "CODEX_API_KEY")
        seen = []
        state = {"logged": True}

        def probe(cmd, **kw):
            seen.append((list(cmd), kw))
            if cmd[:3] == ["claude", "auth", "status"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"loggedIn": state["logged"]}), "")
            return subprocess.CompletedProcess(cmd, 0 if state["logged"] else 1, "", "")
        saved = A.run_group
        A.run_group = probe
        for k in keys:
            os.environ[k] = "sk-test"
        try:
            for brand in ("claude", "codex"):
                seen.clear()
                run = FakeRun()
                r, rc = A.ask(brand, "問", run=run)                           # light 用預設 ⇒ 現量
                self.assertEqual(rc, 0, brand)
                probe_env = seen[0][1]["env"]
                self.assertIs(seen[0][1]["stdin"], subprocess.DEVNULL)
                for k in keys:
                    self.assertNotIn(k, probe_env, brand)                     # 判燈拿不到
                    self.assertNotIn(k, run.calls[0][1]["env"], brand)        # 實際問也拿不到
                    for _c, kw in run.mcp_calls + run.feat_calls:
                        self.assertNotIn(k, kw["env"])
                self.assertEqual(probe_env, run.calls[0][1]["env"])           # 同一份環境
            state["logged"] = False
            for brand in ("claude", "codex"):
                run = FakeRun()
                r, rc = A.ask(brand, "問", run=run)
                self.assertEqual((rc, r["detail"], run.calls), (2, "power_ask_login", []), brand)
            self.assertEqual(A._light("claude"), "yellow")
        finally:
            A.run_group = saved
            for k in keys:
                os.environ.pop(k)
        A.run_group = lambda *a, **k: (_ for _ in ()).throw(OSError("x"))   # 量不到 ⇒ unknown ⇒ login
        try:
            self.assertEqual(A._light("claude"), "unknown")
            self.assertEqual(A.ask("claude", "問", run=FakeRun())[0]["detail"], "power_ask_login")
        finally:
            A.run_group = saved
        import power_login
        st = Path(power_login.state_dir()) / "power-login.json"
        self.assertFalse(st.exists(), "判燈不准寫共用快取")

    def test_a16b_exhausted_fixture_still_blocks(self):
        import usage_probe as UP
        usage = Path(os.environ["EZ2_USAGE_DIR"])
        usage.mkdir(parents=True)
        Path(UP.record_path(usage, "claude", "7d", "shared")).write_text(json.dumps({
            "schema": UP.SCHEMA, "engine": "claude", "window": "7d", "tier": "shared",
            "status": "ok", "used_pct": 100, "observed_at": time.time(),
        }), encoding="utf-8")
        saved = A.run_group
        A.run_group = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, '{"loggedIn": true}', "")
        try:
            run = FakeRun()
            result, rc = A.ask("claude", "問", run=run)
            self.assertEqual((rc, result["detail"], run.calls), (2, "power_ask_login", []))
            self.assertEqual(A._light("claude"), "red")
        finally:
            A.run_group = saved

    def test_a15_group_kill(self):
        d = Path(tempfile.mkdtemp(prefix="aib-power-ask-grp-"))
        try:
            pidf = d / "pid"
            sh = ["/bin/sh", "-c", 'sleep 300 & echo $! > "%s"; wait' % pidf]
            with self.assertRaises(subprocess.TimeoutExpired):                 # 逾時 ⇒ 孫行程也收
                A.run_group(sh, timeout=1, capture_output=True, text=True)
            self._gone(int(pidf.read_text()))
            pidf.unlink()
            r = A.run_group(["/bin/sh", "-c", 'sleep 300 >/dev/null 2>&1 & echo $! > "%s"; exit 3' % pidf],
                            capture_output=True, text=True, timeout=20)
            self.assertEqual(r.returncode, 3)                                 # 正常結束 ⇒ 留下來的孫行程也收
            self._gone(int(pidf.read_text()))
            pidf.unlink()
            tmpd = d / "work"
            code = ("import sys, os, shutil; sys.path.insert(0, %r); import power_ask as A; A.install_term_handler(); "
                    "os.makedirs(%r)\ntry:\n A.run_group(['/bin/sh', '-c', 'sleep 300 & echo $! > %s; wait'])\n"
                    "finally:\n shutil.rmtree(%r)\n") % (str(REPO / "src" / "cockpit"), str(tmpd), pidf, str(tmpd))
            p = subprocess.Popen([sys.executable, "-B", "-c", code])
            for _ in range(100):
                if pidf.exists() and pidf.read_text().strip():
                    break
                import time
                time.sleep(0.1)
            p.terminate()                                                    # 頁面關抽屜
            self.assertEqual(p.wait(20), 128 + 15)
            self._gone(int(pidf.read_text()))
            self.assertFalse(tmpd.exists())                                  # finally 照跑
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def _gone(self, pid):
        import time
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            r = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
            if r.stdout.strip().startswith("Z") or not r.stdout.strip():
                return
            time.sleep(0.1)
        os.kill(pid, 9)
        self.fail("孫行程 %d 還活著" % pid)


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

    def test_k6_no_recheck(self):
        self._knife("    return flags if _mcp_enabled(flags, run) == [] and _features_off(flags, run) else None",
                    "    return flags if _features_off(flags, run) else None", "Ask.test_a13_codex_lockdown")

    def test_k12_api_key_kept(self):
        self._knife(" and k not in _ENV_DROP_NAMES}", "}", "Ask.test_a16_same_env_no_api_key")

    def test_k13_light_full_env(self):
        self._knife("                      timeout=power_login.STATUS_TIMEOUT_S, env=_clean_env())",
                    "                      timeout=power_login.STATUS_TIMEOUT_S)", "Ask.test_a16_same_env_no_api_key")

    def test_k8_no_feature_recheck(self):
        self._knife(" and _features_off(flags, run) else None", " else None", "Ask.test_a14_round3")

    def test_k9_no_group_kill(self):
        self._knife("        _kill_group(p)\n    return subprocess.CompletedProcess", "        p.kill()\n        p.wait()\n    return subprocess.CompletedProcess",
                    "Ask.test_a15_group_kill")

    def test_k10_web_search_on(self):
        self._knife(' + ["-c", "web_search=disabled"]', "", "Ask.test_a5_codex")

    def test_k11_enabled_is_true(self):
        self._knife('x.get("enabled") is not False', 'x.get("enabled") is True', "Ask.test_a14_round3")

    def test_k7_plugins_on(self):
        self._knife('LOCK_FEATURES = ("plugins", ', 'LOCK_FEATURES = (', "Ask.test_a5_codex")

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
    if _OLD_USAGE_DIR is None:
        os.environ.pop("EZ2_USAGE_DIR", None)
    else:
        os.environ["EZ2_USAGE_DIR"] = _OLD_USAGE_DIR


if __name__ == "__main__":
    unittest.main()
