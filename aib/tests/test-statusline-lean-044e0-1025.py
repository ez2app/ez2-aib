#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Claude 狀態列瘦身(10-25 @044e0):舊載體的身份 / 標題 / 收據 / 背景刷新整段拿掉,畫面兩行不變。
全在 tmp HOME;seat_now 與 CTX 儀表用假的(印固定字、記下被怎麼叫),tmux 用 HOME/.local/bin 的假檔。
L1  兩行照舊:圖示 + CTX 那一行(席位碼與等級插在模型名後)在上、📌 那一行在下
L2  session_id:只出現一次 ⇒ bash 取;一次都沒有 ⇒ 空;出現兩次(巢狀 + 頂層)⇒ 交給 python 取頂層
L3  艙內:CTX 那一行沒有「CTX <數字>%」⇒ 不印,只印 📌;有就照印
L4  by-seat 橋:第一次寫;對應沒變 ⇒ 第二次不重寫(inode / mtime 不變);換對話 id ⇒ 重寫
L5  舊的 --refresh 入口:立刻結束、不輸出、不叫 seat_now
L6  腳本裡沒有舊載體的字樣,也不再寫 ui-state(標題快取 / 量測紀錄)
刀(複本 + run_guarded):K1 拿掉「只出現一次」判斷 ⇒ L2 紅;K2 橋每次都重寫 ⇒ L4 紅;
K3 拿掉 --refresh 早退 ⇒ L5 紅;K4 拿掉艙內隱藏 ⇒ L3 紅。刀一律斷言「FAIL: <目標>」且沒有 ERROR
"""
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
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

ROOT = HERE.parent
REL = "deploy/statusline/claude/statusline-ez2-v215.sh"
SCRIPT = ROOT / REL
BASH = os.environ.get("AIB_TEST_BASH") or "/bin/bash"
NODE = shutil.which("node")
SID = "11111111-2222-4333-8444-5555555044e0"   # 末五碼 = 假 seat_now 回的席位碼
SID2 = "66666666-7777-4888-9999-aaaaaaaaaaaa"


@unittest.skipUnless(NODE and os.path.exists("/usr/bin/python3"), "要 node 與 /usr/bin/python3")
class Lean(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.d), True)
        self.home = self.d / "home"
        self.claude = self.home / ".claude"
        self.claude.mkdir(parents=True)
        shutil.copy(str(SCRIPT), str(self.claude / "statusline-ez2-v215.sh"))
        self.ctx_text = "Opus 5.5 | 💧 Ez2 CTX 40%"
        self.write_ctx()
        # 假 seat_now:印「席位碼 \x1f 📌行(含收到的 --session)\x1f 圖示 \x1f 等級」,並記下被叫
        self.aib = self.d / "aib"
        (self.aib / "src" / "cockpit").mkdir(parents=True)
        self.calls = self.d / "seat_now.calls"
        (self.aib / "src" / "cockpit" / "seat_now.py").write_text(
            "import sys\nopen(%r,'a').write(' '.join(sys.argv[1:])+'\\n')\n"
            "s=sys.argv[sys.argv.index('--session')+1] if '--session' in sys.argv else ''\n"
            "sys.stdout.write('044e0\\x1fNOW sid=%%s\\x1fLOGO\\x1f5⭐' %% s)\n" % str(self.calls))
        # 假 tmux:display-message 回 @5
        b = self.home / ".local" / "bin"
        b.mkdir(parents=True)
        (b / "tmux").write_text("#!/bin/sh\necho @5\n")
        (b / "tmux").chmod(0o755)
        # 艙家:seats/w5.json 登記 SID;ctx 根:<sid>.pct
        self.inst = self.home / ".ez2-aib" / "instances" / "cab1"
        (self.inst / "seats").mkdir(parents=True)
        self.ctx = self.d / "ctx"
        self.ctx.mkdir()
        self.register(SID)
        self.pct(SID)

    def write_ctx(self):
        (self.claude / "statusline-ez2-ctx.js").write_text(
            "process.stdin.resume();process.stdin.on('data',()=>{});"
            "process.stdin.on('end',()=>process.stdout.write(%s));" % json.dumps(self.ctx_text))

    def register(self, sid):
        (self.inst / "seats" / "w5.json").write_text(json.dumps(
            {"schema": "aib-seat/v1", "window_id": "@5", "session_uuid": sid}))

    def pct(self, sid):
        (self.ctx / (sid + ".pct")).write_text(json.dumps(
            {"session_id": sid, "observed_at": "2026-10-25T00:00:00Z", "pct": 40}))

    def run_sl(self, payload=None, args=(), cabin=False, tmux=False):
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin:" + os.path.dirname(NODE),
               "EZ2_AIB_ROOT": str(self.aib), "EZ2_CTX_DIR": str(self.ctx)}
        if tmux:
            env.update(TMUX="/tmp/x/aihub-cab1,1,0", TMUX_PANE="%9")
        if cabin:
            env["EZ2_AIB_INSTANCE"] = "cab1"
        if payload is None:
            payload = json.dumps({"session_id": SID, "model": {"display_name": "Opus 5.5"}})
        p = subprocess.run([BASH, str(self.claude / "statusline-ez2-v215.sh")] + list(args),
                           input=payload.encode("utf-8"), env=env, capture_output=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.decode("utf-8")

    def test_l1_two_lines(self):
        out = self.run_sl()
        self.assertEqual(out, "LOGO  Opus 5.5 | @044e0 5⭐ | 💧 Ez2 CTX 40%\nNOW sid=" + SID)

    def test_l2_session_id_parse(self):
        self.assertTrue(self.run_sl(tmux=True).endswith("sid=" + SID))
        self.assertTrue(self.run_sl(json.dumps({"model": {}}), tmux=True).endswith("sid="))
        dup = '{"workspace":{"session_id":"%s"},"session_id":"%s"}' % (SID2, SID)
        self.assertTrue(self.run_sl(dup, tmux=True).endswith("sid=" + SID), "巢狀的 session_id 搶走了頂層的")
        bad = json.dumps({"session_id": "x/../y"})
        # 值不是單純字串 ⇒ 交 python 解(與舊版同:原值先交 seat_now,之後才過濾掉)
        self.assertTrue(self.run_sl(bad, tmux=True).endswith("sid=x/../y"))

    def test_l3_cabin_hides_unmeasured(self):
        self.assertEqual(self.run_sl(cabin=True).count("\n"), 1)
        self.ctx_text = "Opus 5.5 | 💧 Ez2 CTX ?"
        self.write_ctx()
        self.assertEqual(self.run_sl(cabin=True), "NOW sid=" + SID)
        self.assertEqual(self.run_sl().count("\n"), 1, "艙外照印")

    def test_l4_bridge_written_once(self):
        f = self.ctx / "by-seat" / "044e0.json"
        self.run_sl(tmux=True)
        rec = json.loads(f.read_text())
        self.assertEqual((rec["seat"], rec["window"], rec["session_id"], rec["instance"]),
                         ("044e0", "@5", SID, "cab1"))
        st = f.stat()
        os.utime(str(f), ns=(st.st_atime_ns, st.st_mtime_ns - 10 ** 9))
        before = f.stat()
        self.run_sl(tmux=True)
        after = f.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns), "對應沒變卻重寫")
        self.pct(SID2)
        self.run_sl(json.dumps({"session_id": SID2}), tmux=True)
        self.assertEqual(json.loads(f.read_text())["session_id"], SID2)

    def test_l5_refresh_entry_is_noop(self):
        out = self.run_sl(args=("--refresh", SID), tmux=True)
        self.assertEqual(out, "")
        self.assertFalse(self.calls.exists(), "--refresh 還在跑 seat_now")

    def test_l6_no_legacy(self):
        s = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("acb", s.lower())
        self.assertNotIn("ui-state", s)
        self.run_sl(tmux=True)
        self.assertFalse((self.home / ".ez2-aib" / "ui-state").exists())


KNIVES = [
    ("K1", "    *'\"session_id\"'*) _sl_one=0 ;;\n", "", "Lean.test_l2_session_id_parse"),
    ("K2", '  [ "$_ez2_bridge_same" = 1 ] || /usr/bin/python3', '  /usr/bin/python3', "Lean.test_l4_bridge_written_once"),
    ("K3", '[ "${1:-}" = "--refresh" ] && exit 0\n', "", "Lean.test_l5_refresh_entry_is_noop"),
    ("K4", '  if [ "$_hide" = "1" ]; then\n', '  if false; then\n', "Lean.test_l3_cabin_hides_unmeasured"),
]


class Knife(unittest.TestCase):
    @unittest.skipUnless(NODE and os.path.exists("/usr/bin/python3"), "要 node 與 /usr/bin/python3")
    def test_knives(self):
        name = pathlib.Path(__file__).name
        for tag, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, REL, needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=120)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
