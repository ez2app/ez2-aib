#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""memguard(記憶體暴衝守門)+ aib test 每支測試記憶體上限(10-24 20:34 整台卡死實案)。
狀態根在暫存夾;真行程只起測試自己的子孫,只殺自己起的 pid。
G1 門檻:預設 max(4 GB, 35% 實體記憶體);環境變數可改但不低於 1 GB;aib test 上限預設 2048、0 = 關、不低於 256
G2 直譯器連續 2 拍超過門檻才收(TERM);一拍尖峰不收;非直譯器(claude)、別的 uid、自己的祖先一律不看
G3 開發分級:直譯器跑的腳本在 scratchpad / tests / 暫存根 ⇒ dev 上限;沒有 ⇒ 一般上限
G3b 相對路徑用目標行程的 cwd 解(不是守門自己的)、cwd 不知道 ⇒ 不判 dev、不是檔 ⇒ 不判 dev;
    python 合寫短選項(-Bc / -uc / -mMOD / -Wx)、--check-hash-based-pycs 帶值、`-` 與沒腳本(stdin)⇒ 內嵌
G11 multiprocessing 子行程(spawn_main / resource_tracker / --multiprocessing-fork)沿用父行程分級
G4 pid 重用:送訊號前開始時間對不上 ⇒ changed,不送任何訊號
G5 dry-run 不殺、留痕一行、紀錄檔 0600、不含整條指令列
G6 鎖:同一個狀態根只有一條守門拿得到鎖
G7 TreeCap 真行程:孫行程配置 400 MB、上限 256 ⇒ 命中的是孫行程、整棵都收掉
G9 Codex 席(node codex resume / codex exec -c)、本機 runner、node 一般腳本不收;暫存根 node 腳本 dev、python -m general
G9b node 帶值選項(--env-file / --inspect-port / --experimental-loader / --title / --input-type)的值不當腳本
G10 argv 精確讀(含空白引數、空字串引數不位移);目標行程 cwd 讀得到
G12 環境變數「²」這種 isdigit 但不是十進位 ⇒ 用預設;守門起不來 ⇒ 鎖放掉、留痕
G13 ancestors() 走到 init 或環就停
G14 TreeCap:根的開始時間對不上(pid 重用)⇒ 不收;子孫開始時間對不上 ⇒ 那支不收
G8 runner._run_one 端到端:測試配置 400 MB、AIB_TEST_MEM_CAP_MB=256 ⇒ rc 137、輸出帶 memory_cap、
   紅格有 MEMCAP:memory_cap(舊紅清單吃不掉);上限關掉 ⇒ 同一支 400 MB 照常綠
G15 runner 逾時:測試留著背景孫行程拿管線 ⇒ timeout+幾秒內回 rc 124、紅格 TIMEOUT:timeout、孫行程一起收
G16 認不得的帶值選項 + 絕對路徑資料夾不判 dev;node / php 補的帶值選項(r3 M2)
G17 真行程 fd 0:管線 / 檔案 / 終端機認得出來(r3 M1)
G18 動手前重讀 argv:同名直譯器 exec 成別的腳本 ⇒ 重新分級,不照舊收(r3 L2)
G19 主行程先結束、背景行程掛到 pid 1 ⇒ 逾時收得到(測試自成行程群組,r3 M3)
G20 aib test 被 Ctrl-C(對驅動群組送 SIGINT)⇒ 正在跑的測試行程也收掉(r4 High)
G21 aib test 收到 SIGHUP(關終端機 / ssh 斷線)⇒ 同上;走 test_entry __main__ 真的那段裝訊號
G23 記憶體上限命中 + 測試先 double-fork 一個 daemon(同群組、掛到 pid 1、不拿管線)⇒ daemon 也收掉(r5 M1)
G24 測試忽略 SIGINT、對驅動群組連送兩次 SIGINT ⇒ 3 秒內測試不在(r5 M2)
G25 SIGINT 後 0.5 秒再送 SIGHUP ⇒ 3 秒內測試不在、驅動結束(r5 M2)
G22 python -Wignore / -Ximporttime 不當 -i;node -i / --interactive 不算內嵌;script_hint 標 (stdin)(r4 L-b–L-d)
刀(複本 + run_guarded):K1 TreeCap 上限判斷拿掉 ⇒ G7 紅;K2 連兩拍確認拿掉 ⇒ G2 紅;
K3 開發分級拿掉 ⇒ G3 紅;K4 開始時間不比 ⇒ G4 紅;K5 node 也套一般上限 ⇒ G9 紅;
K6 腳本之後的 -c 也當內嵌 ⇒ G3 紅;K7 相對路徑改用守門自己的 cwd ⇒ G3b 紅;K8 multiprocessing 不沿用 ⇒ G11 紅;
K9 TreeCap 不比根開始時間 ⇒ G14 紅;K10 stdin 不看 fd 0 ⇒ G3b 紅;K11 資料夾也當腳本 ⇒ G16 紅;
K12 動手前不重讀 argv ⇒ G18 紅;R4 逾時不收群組 ⇒ G19 紅;R5 拿掉 except BaseException 轉送 ⇒ G20 / G21 紅;
T1 test_entry 不接 SIGHUP ⇒ G21 紅;K13 看門執行緒不收群組 ⇒ G23 紅;R6 / R6h 寬限不包 finally ⇒ G24 / G25 紅;runner 刀:R1 逾時改回 communicate() ⇒ G15 紅;R2 拿掉固定紅格 ⇒ G8 紅;
R3 拿掉 rc 137 覆寫 ⇒ G8 紅。刀一律斷言輸出出現「FAIL: <目標>」且沒有 ERROR(紅在對的斷言)
"""
import selectors
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
sys.path.insert(0, str(ROOT / "tools" / "verify"))
os.environ.setdefault("EZ2_AIB_LOCALE", "zh-TW")

import memguard as MG  # noqa: E402

UID = os.getuid()
GB = 2 ** 30


def readline_timeout(fh, seconds=20):
    sel = selectors.DefaultSelector()
    sel.register(fh, selectors.EVENT_READ)
    try:
        if not sel.select(seconds):
            raise AssertionError("等子行程輸出逾時")
        return fh.readline()
    finally:
        sel.close()


def kill_quiet(*pairs):
    """pairs:(pid, 開始時間);開始時間還對得上才送(已回收、號碼被重用就不送 —— r3 L1)。"""
    for pid, start in pairs:
        if pid and start and MG.start_of(pid) == start:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def table(rows):
    """rows: {pid: (uid, ppid, 名字, 開始, MB)} ⇒ 假的 info()。"""
    def info(pid):
        r = rows.get(pid)
        return None if r is None else (r[0], r[1], r[2], r[3], r[4] * 2 ** 20)
    return info


class Limits(unittest.TestCase):
    def test_g1_limits(self):
        self.assertEqual(MG.limit_mb({}, total=16 * GB), int(16 * 1024 * 0.35))
        self.assertEqual(MG.limit_mb({}, total=8 * GB), 4096)
        self.assertEqual(MG.limit_mb({"EZ2_MEMGUARD_LIMIT_MB": "3000"}, total=16 * GB), 3000)
        self.assertEqual(MG.limit_mb({"EZ2_MEMGUARD_LIMIT_MB": "10"}, total=16 * GB), 1024)
        self.assertEqual(MG.test_cap_mb({}), 2048)
        self.assertEqual(MG.test_cap_mb({"AIB_TEST_MEM_CAP_MB": "0"}), 0)
        self.assertEqual(MG.test_cap_mb({"AIB_TEST_MEM_CAP_MB": "100"}), 256)
        self.assertEqual(MG.test_cap_mb({"AIB_TEST_MEM_CAP_MB": "abc"}), 2048)

    def test_g12_non_decimal_env_and_thread_failure(self):
        self.assertEqual(MG.test_cap_mb({"AIB_TEST_MEM_CAP_MB": "\u00b2"}), 2048)
        self.assertEqual(MG.limit_mb({"EZ2_MEMGUARD_LIMIT_MB": "\u00b2"}, total=8 * GB), 4096)
        self.assertEqual(MG.limit_mb({"EZ2_MEMGUARD_LIMIT_MB": "\u0663\u0660\u0660\u0660"}, total=8 * GB), 4096)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.object(MG, "Guard", side_effect=ValueError("boom")):
            MG._thread_main(False, root=tmp)
        fd = MG.try_lock(tmp)
        self.assertIsNotNone(fd, "守門起不來之後鎖沒放掉,別艙接不了手")
        os.close(fd)
        rows = (Path(tmp) / "logs" / "memguard.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(rows[-1])["action"], "mem_guard_failed")

    def test_g13_ancestors(self):
        info = table({10: (UID, 5, "a", "1", 1), 5: (UID, 1, "b", "1", 1), 1: (0, 0, "launchd", "1", 1)})
        self.assertEqual(MG.ancestors(10, info=info), {10, 5, 1})
        loop = table({20: (UID, 21, "a", "1", 1), 21: (UID, 20, "b", "1", 1)})
        self.assertEqual(MG.ancestors(20, info=loop), {20, 21})
        self.assertEqual(MG.ancestors(30, info=table({})), {30})
        self.assertIn(os.getppid(), MG.ancestors())


class GuardLogic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.killed = []

    def guard(self, rows, args=None, **kw):
        info = table(rows)
        cwds = kw.pop("cwds", None) or {}
        stdins = kw.pop("stdins", None) or {}
        g = MG.Guard(env={}, dry_run=kw.pop("dry_run", False), limit=kw.pop("limit", 5000), pids=list(rows),
                     info=info, kill=lambda p, s: self.killed.append((p, s)), sleep=lambda s: None,
                     notify_fn=lambda t: None, args_fn=lambda p: (args or {}).get(p, ["python3", "app.py"]),
                     root=self.tmp, uid=UID, dev_limit=kw.pop("dev_limit", 2000),
                     cwd_fn=lambda p: cwds.get(p), stdin_fn=lambda p: stdins.get(p))
        g.protect = kw.pop("protect", set())
        return g

    def test_g2_confirm_and_scope(self):
        rows = {100: (UID, 1, "Python", "1.0", 6000), 101: (UID, 1, "claude", "1.0", 9000),
                102: (UID + 1, 1, "python3", "1.0", 9000), 103: (UID, 1, "node", "1.0", 6000),
                104: (UID, 1, "Python", "1.0", 6000)}
        # kill 之後行程「還在」(假表不變)⇒ kill_one 一路走到 KILL;我們只看收了誰
        g = self.guard(rows, protect={104}, args={103: ["node", "/private/tmp/x/repro.js"]})
        self.assertEqual(g.tick(), [])                      # 第一拍只記一次
        self.assertEqual(self.killed, [])
        acted = g.tick()
        self.assertEqual(sorted(r["pid"] for r in acted), [100, 103])
        self.assertIn((100, signal.SIGTERM), self.killed)
        self.assertNotIn(101, [p for p, _ in self.killed])
        self.assertNotIn(102, [p for p, _ in self.killed])
        self.assertNotIn(104, [p for p, _ in self.killed])

    def test_g2_spike_then_drop_not_killed(self):
        rows = {100: (UID, 1, "Python", "1.0", 6000)}
        g = self.guard(rows)
        g.tick()
        rows[100] = (UID, 1, "Python", "1.0", 100)
        g.tick()
        rows[100] = (UID, 1, "Python", "1.0", 6000)
        self.assertEqual(g.tick(), [])
        self.assertEqual(self.killed, [])

    def test_g3_dev_tier(self):
        rows = {200: (UID, 1, "Python", "1.0", 3000), 201: (UID, 1, "Python", "1.0", 3000)}
        args = {200: ["python3", "-W", "ignore", "/x/scratchpad/revA/sparse_repro.py"], 201: ["python3", "/opt/app/server.py"]}
        g = self.guard(rows, args=args)
        g.tick()
        acted = g.tick()
        self.assertEqual([r["pid"] for r in acted], [200])
        self.assertEqual(acted[0]["tier"], "dev")
        self.assertEqual(acted[0]["limit_mb"], 2000)
        self.assertTrue(MG.is_dev(["python3", "-c", "x"]))
        self.assertFalse(MG.is_dev(["python3", "/opt/app/server.py"]))
        self.assertFalse(MG.is_dev(["python3", "/opt/app/tool.py", "-c", "x"]))     # 腳本之後的 -c 是腳本的旗標

    def test_g3b_relative_cwd_and_python_flags(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        app = Path(d, "app")
        (app / "tests").mkdir(parents=True)
        Path(app, "tests", "test_x.py").write_text("")
        Path(d, "server.py").write_text("")
        home_app = Path.home()                     # 一個不在暫存根的 cwd
        c = MG.classify
        self.assertEqual(c("python3", ["python3", "tests/test_x.py"], str(app)), "dev")
        self.assertEqual(c("python3", ["python3", "tests/test_x.py"], None), "general")     # cwd 不知道 ⇒ 不判 dev
        self.assertEqual(c("python3", ["python3", "server.py"], str(home_app)), "general")   # 守門自己在哪不重要
        self.assertEqual(c("node", ["node", "8000"], d), None)                               # 不是檔 ⇒ 不判 dev
        self.assertEqual(c("python3", ["python3", "-mhttp.server", "8000"], d), "general")
        self.assertEqual(c("python3", ["python3", "-m", "http.server", "8000"], d), "general")
        self.assertEqual(c("python3", ["python3", "--check-hash-based-pycs", "always", "/opt/app/s.py"], d), "general")
        self.assertEqual(c("python3", ["python3", "-Wignore", "/opt/app/s.py"], d), "general")
        self.assertEqual(c("python3", ["python3", "-Q", "/opt/app/s.py"], d), "general")
        for inline in (["python3", "-Bc", "x"], ["python3", "-uc", "x"], ["python3", "-c", "x"], ["node", "-pe", "1"]):
            self.assertEqual(c(inline[0], inline, d), "dev", inline)
        # 沒腳本 / `-`:看 fd 0 —— 管線 / 檔案(heredoc)才算內嵌;終端機(REPL)或讀不到不算;-i 一律不算(r3 M1)
        for argv in (["python3", "-"], ["python3"], ["python3", "-u"], ["python3", "-q"], ["node"], ["node", "-"]):
            gen = "general" if argv[0] == "python3" else None
            self.assertEqual(c(argv[0], argv, d, "pipe"), "dev", argv)
            self.assertEqual(c(argv[0], argv, d, "file"), "dev", argv)
            self.assertEqual(c(argv[0], argv, d, "tty"), gen, argv)
            self.assertEqual(c(argv[0], argv, d, None), gen, argv)
        self.assertEqual(c("python3", ["python3", "-i"], d, "pipe"), "general")
        self.assertEqual(c("python3", ["python3", "-qi"], d, "pipe"), "general")
        self.assertEqual(c("php-cgi", ["php-cgi"], d), None)                                 # php 沒腳本不算內嵌
        # 端到端:Guard 用 cwd_fn 拿目標行程的 cwd
        rows = {210: (UID, 1, "Python", "1.0", 3000), 211: (UID, 1, "Python", "1.0", 3000)}
        g = self.guard(rows, args={210: ["python3", "tests/test_x.py"], 211: ["python3", "tests/test_x.py"]},
                       cwds={210: str(app), 211: None})
        g.tick()
        self.assertEqual([r["pid"] for r in g.tick()], [210])

    def test_g16_unknown_value_options_not_dev(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        c = MG.classify
        for opt in ("--test-reporter-destination", "--cpu-prof-dir", "--heap-prof-dir", "--watch-path",
                    "--localstorage-file", "--env-file-if-exists", "--snapshot-blob", "--experimental-config-file"):
            self.assertEqual(MG.script_of("node", ["node", opt, "/tmp/out.txt", "x.js"]), ("script", "x.js"), opt)
        self.assertIsNone(c("node", ["node", "--test-reporter-destination", "/tmp/out.txt", "x.js"], d))
        self.assertIsNone(c("node", ["node", "--some-future-dir", d, "/opt/app/s.js"], d))     # 認不得 + 資料夾
        self.assertIsNone(c("php", ["php", "-t", d, "-S", "localhost:8000"], d))
        self.assertEqual(c("python3", ["python3", "--some-future-dir", d], d, None), "general")

    def test_g18_reread_argv_before_acting(self):
        rows = {800: (UID, 1, "Python", "1.0", 3000)}
        calls = []

        def args_fn(p):
            calls.append(p)
            return ["python3", "/x/scratchpad/repro.py"] if len(calls) == 1 else ["python3", "/opt/app/server.py"]
        g = self.guard(rows)
        g.args_fn = args_fn
        g.tick()
        self.assertEqual(g.tick(), [], "exec 成別的腳本之後還照舊分級收掉")
        self.assertEqual(self.killed, [])

    def test_g17_real_stdin_kind(self):
        import pty
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        import socket
        f = Path(d, "in.txt")
        f.write_text("")
        m, sl = pty.openpty()
        a, b = socket.socketpair()
        fh = open(f)
        procs = []
        try:
            for stdin in (subprocess.PIPE, fh, sl, subprocess.DEVNULL, a.fileno()):
                procs.append(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdin=stdin))
            time.sleep(0.3)
            self.assertEqual([MG.stdin_kind(p.pid) for p in procs], ["pipe", "file", "tty", None, None])
        finally:
            for p in procs:
                p.kill()
                p.wait()
                if p.stdin:
                    p.stdin.close()
            fh.close()
            a.close()
            b.close()
            os.close(m)
            os.close(sl)

    def test_g22_flag_values_and_node_interactive(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        c = MG.classify
        self.assertEqual(c("python3", ["python3", "-Wignore"], d, "pipe"), "dev")      # i 是 -W 的值
        self.assertEqual(c("python3", ["python3", "-Ximporttime"], d, "pipe"), "dev")
        self.assertEqual(c("python3", ["python3", "-qi"], d, "pipe"), "general")
        for argv in (["node", "-i"], ["node", "--interactive"]):
            self.assertIsNone(c("node", argv, d, "pipe"), argv)
        self.assertEqual(MG.script_hint(1, ["python3"], "python3"), "(stdin)")
        self.assertEqual(MG.script_hint(1, ["python3", "-i"], "python3"), "(interactive)")

    def test_g11_multiprocessing_inherits_parent(self):
        spawn = ["python3", "-c", "from multiprocessing.spawn import spawn_main; spawn_main(tracker_fd=5, pipe_handle=7)",
                 "--multiprocessing-fork"]
        tracker = ["python3", "-c", "from multiprocessing.resource_tracker import main;main(6)"]
        rows = {600: (UID, 1, "Python", "1.0", 10), 601: (UID, 600, "Python", "1.0", 3000),
                602: (UID, 600, "Python", "1.0", 3000), 610: (UID, 1, "Python", "1.0", 10),
                611: (UID, 610, "Python", "1.0", 3000)}
        args = {600: ["python3", "/opt/app/server.py"], 601: spawn, 602: tracker,
                610: ["python3", "/x/scratchpad/repro.py"], 611: spawn}
        g = self.guard(rows, args=args)
        g.tick()
        acted = g.tick()
        self.assertEqual([(r["pid"], r["tier"]) for r in acted], [(611, "dev")])
        self.assertEqual(MG.classify("python3", spawn), "inherit")
        self.assertEqual(MG.classify("python3", tracker), "inherit")

    def test_g9_live_seats_and_runner_never(self):
        """#322 r1(@abb89 High):Codex 席行程名是 node、`codex exec -c` 帶 -c;本機 runner 也是 node。都不收。"""
        rows = {500: (UID, 1, "node", "1.0", 9000), 501: (UID, 1, "node", "1.0", 9000),
                502: (UID, 1, "node", "1.0", 9000), 503: (UID, 1, "node", "1.0", 9000),
                504: (UID, 1, "node", "1.0", 3000), 505: (UID, 1, "Python", "1.0", 3000),
                506: (UID, 1, "Python", "1.0", 6000), 507: (UID, 1, "node", "1.0", 9000)}
        args = {500: ["node", "/opt/homebrew/bin/codex", "resume", "--dangerously-bypass-approvals-and-sandbox"],
                501: ["node", "/opt/homebrew/bin/codex", "exec", "-c", "web_search=live", "hi"],
                502: ["./externals/node24/bin/node", "./bin/RunnerService.js"],
                503: ["node", "/opt/app/server.js"],
                504: ["node", "/private/tmp/x/repro.js"],
                505: ["python3", "/opt/app/tool.py", "-c", "conf.json"],
                506: ["python3", "-m", "http.server"],
                507: ["node", "--env-file", ".env", "--inspect-port", "9229", "--experimental-loader", "./l.mjs",
                      "--title", "x", "--input-type", "module", "/opt/app/server.js"]}
        g = self.guard(rows, args=args)
        g.tick()
        acted = g.tick()
        self.assertEqual(sorted(r["pid"] for r in acted), [504, 506])
        self.assertEqual({r["pid"]: r["tier"] for r in acted}, {504: "dev", 506: "general"})
        self.assertEqual({r["pid"]: r["script"] for r in acted}, {504: "repro.js", 506: "-m http.server"})

    def test_g9b_node_value_options(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for opt, val in (("--env-file", ".env"), ("--inspect-port", "9229"), ("--experimental-loader", "./l.mjs"),
                         ("--title", "srv"), ("--input-type", "module")):
            Path(d, val).write_text("")
            self.assertEqual(MG.script_of("node", ["node", opt, val, "/opt/app/server.js"]),
                             ("script", "/opt/app/server.js"), opt)
            self.assertIsNone(MG.classify("node", ["node", opt, val, "/opt/app/server.js"], d), opt)

    def test_g10_real_argv(self):
        """argv 用 sysctl / /proc 精確讀(含空白的引數不被拆開)。"""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "a b", "", "z"], cwd=d)
        try:
            time.sleep(0.3)
            self.assertEqual(MG.proc_cwd(p.pid), os.path.realpath(d))          # 目標行程的 cwd,不是自己的
            a = MG.cmd_args(p.pid)
            self.assertEqual(a[1:], ["-c", "import time; time.sleep(30)", "a b", "", "z"])
            self.assertEqual(MG.classify(MG.proc_info(p.pid)[2], a), "dev")
        finally:
            p.kill()
            p.wait()

    def test_g14_treecap_pid_reuse(self):
        sent = []
        t = {700: (1, "python3", "2.0", 3000), 701: (700, "python3", "1.0", 3000)}
        info = table({700: (UID, 1, "python3", "2.0", 3000), 701: (UID, 700, "python3", "1.0", 3000)})
        tc = MG.TreeCap(700, 256, table=lambda: t, kill=lambda p, s: sent.append(p), info=info, root_start="1.0")
        self.assertIsNone(tc.check_once())
        self.assertEqual(sent, [])
        # 根對得上、快照之後子孫被換掉(開始時間不同)⇒ 只收根
        info2 = table({700: (UID, 1, "python3", "2.0", 3000), 701: (UID, 700, "python3", "9.0", 3000)})
        tc = MG.TreeCap(700, 256, table=lambda: t, kill=lambda p, s: sent.append(p), info=info2, root_start="2.0")
        self.assertIsNotNone(tc.check_once())
        self.assertEqual(sent, [700])

    def test_g4_pid_reuse(self):
        sent = []
        info = table({300: (UID, 1, "Python", "2.0", 6000)})
        r = MG.kill_one(300, "1.0", info=info, sleep=lambda s: None, kill=lambda p, s: sent.append(s), uid=UID)
        self.assertEqual(r, "mem_pid_reused")
        self.assertEqual(sent, [])

    def test_g5_dry_run_log(self):
        rows = {400: (UID, 1, "Python", "1.0", 6000)}
        g = self.guard(rows, dry_run=True, args={400: ["python3", "/x/tests/test-a.py", "--token=SECRET"]})
        g.tick()
        acted = g.tick()
        self.assertEqual(acted[0]["action"], "mem_dry_run")
        self.assertEqual(self.killed, [])
        p = Path(self.tmp) / "logs" / "memguard.jsonl"
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        text = p.read_text()
        self.assertNotIn("SECRET", text)
        self.assertEqual(json.loads(text.splitlines()[-1])["script"], "test-a.py")

    def test_g6_lock(self):
        a = MG.try_lock(self.tmp)
        self.assertIsNotNone(a)
        self.assertIsNone(MG.try_lock(self.tmp))
        os.close(a)
        b = MG.try_lock(self.tmp)
        self.assertIsNotNone(b)
        os.close(b)


GROW = textwrap.dedent("""
    import subprocess, sys, time
    if sys.argv[1] == "parent":
        c = subprocess.Popen([sys.executable, __file__, "child"])
        print("child", c.pid, flush=True)
        c.wait()
    else:
        blob = b"x" * (400 * 1024 * 1024)
        time.sleep(60)
""")


class RealTree(unittest.TestCase):
    def test_g7_treecap_real(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        f = os.path.join(d, "grow.py")
        Path(f).write_text(GROW)
        p = subprocess.Popen([sys.executable, f, "parent"], stdout=subprocess.PIPE, text=True)
        child = cstart = None
        try:
            child = int(readline_timeout(p.stdout).split()[1])
            cstart = MG.start_of(child)
            tc = MG.TreeCap(p.pid, 256, tick_s=0.1).start()
            t0 = time.time()
            while tc.hit is None and time.time() - t0 < 30:
                time.sleep(0.1)
            tc.stop()
            self.assertIsNotNone(tc.hit, "30 秒內沒抓到 400 MB 的孫行程")
            self.assertEqual(tc.hit["pid"], child)
            self.assertGreaterEqual(tc.hit["mb"], 256)
            p.wait(timeout=10)
            for _ in range(50):
                if MG.proc_info(child) is None:
                    break
                time.sleep(0.1)
            self.assertIsNone(MG.proc_info(child))
        finally:
            kill_quiet((child, cstart))
            p.kill()
            p.stdout.close()
            p.wait()


class RunnerCap(unittest.TestCase):
    def test_g8_run_one_memory_cap(self):
        import runner  # noqa: E402
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(repo), True)
        (repo / "tests").mkdir()
        (repo / "tests" / "test-big.py").write_text(
            "import os, time\nblob = b'x' * (400 * 1024 * 1024)\ntime.sleep(float(os.environ.get('BIG_SLEEP', '30')))\n"
            "print('done')\n")
        (repo / "tests" / "test-small.py").write_text("print('ok')\n")
        env = dict(os.environ)
        with mock.patch.dict(os.environ, {"AIB_TEST_MEM_CAP_MB": "256"}):
            r = runner._run_one(repo, "tests/test-big.py", env, timeout=60)
            ok = runner._run_one(repo, "tests/test-small.py", env, timeout=60)
        self.assertEqual(r["rc"], 137)
        self.assertIn("memory_cap", r["output"])
        self.assertIn(runner.MEMCAP_CELL, r["fail_cells"])
        self.assertLess(r["elapsed_s"], 25)
        self.assertEqual(ok["rc"], 0)
        self.assertEqual(ok["fail_cells"], [])
        with mock.patch.dict(os.environ, {"AIB_TEST_MEM_CAP_MB": "0"}):
            off = runner._run_one(repo, "tests/test-big.py", dict(env, BIG_SLEEP="2"), timeout=60)
        self.assertEqual(off["rc"], 0, off["output"])
        self.assertIn("done", off["output"])

    def test_g15_timeout_with_background_grandchild(self):
        import runner  # noqa: E402
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(repo), True)
        (repo / "tests").mkdir()
        (repo / "tests" / "test-bg.py").write_text(
            "import subprocess, sys, time\n"
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(40)'])\n"
            "print('grandchild', g.pid, flush=True)\n"
            "time.sleep(40)\n")
        t0 = time.monotonic()
        r = runner._run_one(repo, "tests/test-bg.py", dict(os.environ), timeout=4)
        took = time.monotonic() - t0
        gpid = None
        for ln in r["output"].splitlines():
            if ln.startswith("grandchild "):
                gpid = int(ln.split()[1])
        try:
            self.assertEqual(r["rc"], 124, r["output"])
            self.assertLess(took, 12, "有背景孫行程時逾時 %.1f 秒才回" % took)
            self.assertIn(runner.TIMEOUT_CELL, r["fail_cells"])
            self.assertIsNotNone(gpid, r["output"])
            for _ in range(30):
                if MG.proc_info(gpid) is None:
                    break
                time.sleep(0.1)
            self.assertIsNone(MG.proc_info(gpid), "逾時後孫行程沒一起收")
            print("G15 有背景孫行程的逾時:timeout=4 ⇒ %.2f 秒回" % took, file=sys.stderr)
        finally:
            kill_quiet((gpid, MG.start_of(gpid) if gpid else None))

    def test_g23_memcap_collects_detached_daemon(self):
        """上限命中:測試先 double-fork 一個 daemon(stdio 接 /dev/null、同群組、掛到 pid 1)再配 400 MB(r5 M1)。"""
        import runner  # noqa: E402
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(repo), True)
        (repo / "tests").mkdir()
        pidf = repo / "daemon.pid"
        (repo / "tests" / "test-daemon-big.py").write_text(textwrap.dedent("""
            import os, time
            if os.fork() == 0:
                if os.fork() == 0:
                    fd = os.open(os.devnull, os.O_RDWR)
                    for k in (0, 1, 2):
                        os.dup2(fd, k)
                    open(%r, "w").write(str(os.getpid()))
                    time.sleep(60)
                os._exit(0)
            os.wait()
            for _ in range(100):
                if os.path.exists(%r):
                    break
                time.sleep(0.05)
            blob = b"x" * (400 * 1024 * 1024)
            time.sleep(30)
        """ % (str(pidf), str(pidf))))
        dpid = dstart = None
        try:
            with mock.patch.dict(os.environ, {"AIB_TEST_MEM_CAP_MB": "256"}):
                r = runner._run_one(repo, "tests/test-daemon-big.py", dict(os.environ), timeout=60)
            dpid = int(pidf.read_text())
            for _ in range(30):
                if MG.proc_info(dpid) is None:
                    break
                time.sleep(0.1)
            self.assertIsNone(MG.proc_info(dpid), "上限命中後 daemon 還活著(孤兒)")
            self.assertEqual(r["rc"], 137, r["output"])
        finally:
            if dpid:
                dstart = MG.start_of(dpid)
            kill_quiet((dpid, dstart))

    def test_g19_main_exits_background_orphan_collected(self):
        """主行程先結束、背景行程掛到 pid 1 還拿著管線 ⇒ 逾時回 124,背景行程靠群組收掉(r3 M3)。"""
        import runner  # noqa: E402
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(repo), True)
        (repo / "tests").mkdir()
        (repo / "tests" / "test-orphan.py").write_text(
            "import subprocess, sys\n"
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(40)'])\n"
            "print('orphan', g.pid, flush=True)\n")
        t0 = time.monotonic()
        r = runner._run_one(repo, "tests/test-orphan.py", dict(os.environ), timeout=4)
        took = time.monotonic() - t0
        opid = None
        for ln in r["output"].splitlines():
            if ln.startswith("orphan "):
                opid = int(ln.split()[1])
        try:
            self.assertEqual(r["rc"], 124, r["output"])
            self.assertLess(took, 12)
            self.assertIsNotNone(opid, r["output"])
            for _ in range(30):
                if MG.proc_info(opid) is None:
                    break
                time.sleep(0.1)
            self.assertIsNone(MG.proc_info(opid), "主行程先結束的背景行程留成孤兒")
        finally:
            kill_quiet((opid, MG.start_of(opid) if opid else None))


DRIVER = textwrap.dedent("""
    import os, sys, textwrap
    from pathlib import Path
    sys.path[:0] = [sys.argv[3], sys.argv[4]]
    import runner, test_entry
    repo = Path(sys.argv[1])
    def main():
        runner._run_one(repo, "tests/test-hang.py", dict(os.environ), timeout=60)
        return 0
    # 執行 test_entry 真的 __main__ 那段(裝 SIGTERM / SIGHUP),只把 main 換成跑一支測試
    src = open(test_entry.__file__, encoding="utf-8").read().split('if __name__ == "__main__":', 1)[1]
    exec(textwrap.dedent(src), {"_on_term": test_entry._on_term, "main": main, "sys": sys})
""")


def interrupt_case(testcase, sig, then=None, ignore_int=False, gone_within=None):
    """起一支驅動(自己一個群組,像終端機前景群組)跑 _run_one;測試寫出 pid 後對驅動群組送 sig
    (then = 0.5 秒後再送的訊號)。回 (驅動結束碼或 None, 測試 pid 收掉了沒)。
    gone_within:只等這麼多秒看測試在不在(先判「行程還活著」,再看驅動)。"""
    repo = Path(tempfile.mkdtemp())
    testcase.addCleanup(shutil.rmtree, str(repo), True)
    (repo / "tests").mkdir()
    pidf = repo / "pid.txt"
    (repo / "tests" / "test-hang.py").write_text(
        "import os, signal, time\n" + ("signal.signal(signal.SIGINT, signal.SIG_IGN)\n" if ignore_int else "") +
        "open(%r, 'w').write(str(os.getpid()))\ntime.sleep(60)\n" % str(pidf))
    drv = repo / "driver.py"
    drv.write_text(DRIVER)
    p = subprocess.Popen([sys.executable, str(drv), str(repo), "x", str(ROOT / "tools" / "verify"),
                          str(ROOT / "src" / "cockpit")], start_new_session=True, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    tpid = tstart = None
    try:
        for _ in range(200):
            if pidf.exists() and pidf.read_text().strip():
                break
            time.sleep(0.05)
        tpid = int(pidf.read_text())
        tstart = MG.start_of(tpid)
        os.killpg(p.pid, sig)
        if then is not None:
            time.sleep(0.5)
            try:
                os.killpg(p.pid, then)
            except OSError:
                pass
        if gone_within is not None:
            end = time.monotonic() + gone_within
            while time.monotonic() < end and MG.proc_info(tpid) is not None:
                time.sleep(0.05)
            gone = MG.proc_info(tpid) is None
            try:
                rc = p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                rc = None
            return rc, gone
        try:
            rc = p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            rc = None                       # 驅動卡住(r3:SystemExit 時 Popen.__exit__ 一直等測試結束)
        for _ in range(40):
            if MG.proc_info(tpid) is None:
                break
            time.sleep(0.1)
        return rc, MG.proc_info(tpid) is None
    finally:
        kill_quiet((tpid, tstart))
        if p.poll() is None:
            p.kill()
            p.wait()


class Interrupt(unittest.TestCase):
    def test_g20_ctrl_c_collects_running_test(self):
        rc, gone = interrupt_case(self, signal.SIGINT)
        self.assertNotIn(rc, (0, None), "驅動沒結束或沒報錯")
        self.assertTrue(gone, "aib test 被 Ctrl-C 後測試行程還活著(孤兒)")

    def test_g24_double_ctrl_c_still_collects(self):
        rc, gone = interrupt_case(self, signal.SIGINT, then=signal.SIGINT, ignore_int=True, gone_within=3)
        self.assertTrue(gone, "連按兩次 Ctrl-C 後測試行程還活著(孤兒)")
        self.assertIsNotNone(rc, "驅動沒結束")

    def test_g25_ctrl_c_then_sighup_still_collects(self):
        rc, gone = interrupt_case(self, signal.SIGINT, then=signal.SIGHUP, ignore_int=True, gone_within=3)
        self.assertTrue(gone, "Ctrl-C 後接 SIGHUP,測試行程還活著(孤兒)")
        self.assertIsNotNone(rc, "驅動卡住沒結束")

    def test_g21_sighup_collects_running_test(self):
        rc, gone = interrupt_case(self, signal.SIGHUP)
        self.assertEqual(rc, 128 + signal.SIGHUP, "SIGHUP 沒走 _on_term 收尾")
        self.assertTrue(gone, "aib test 收到 SIGHUP 後測試行程還活著(孤兒)")


KNIVES = [
    ("K1", "        if t[big][3] < self.cap:", "        if True:", "RealTree.test_g7_treecap_real"),
    ("K2", "            if self.over[key] < CONFIRM_TICKS:", "            if False:",
     "GuardLogic.test_g2_spike_then_drop_not_killed"),
    ("K3", "            lim = self.dev_limit if tier == \"dev\" else self.limit", "            lim = self.limit",
     "GuardLogic.test_g3_dev_tier"),
    ("K4", "        return bool(i) and i[0] == uid and i[3] == start", "        return bool(i)",
     "GuardLogic.test_g4_pid_reuse"),
    ("K5", '    return "general" if kind == "python" else None', '    return "general"',
     "GuardLogic.test_g9_live_seats_and_runner_never"),
    ("K6", '        return "script", a\n    if kind in NO_SCRIPT_INLINE',
     '        return ("inline", None) if "-c" in args else ("script", a)\n    if kind in NO_SCRIPT_INLINE',
     "GuardLogic.test_g3_dev_tier"),
    ("K7", "        path = what if os.path.isabs(what) else (os.path.join(cwd, what) if cwd else None)",
     "        path = os.path.abspath(what)", "GuardLogic.test_g3b_relative_cwd_and_python_flags"),
    ("K8", '        if tier != "inherit":\n            return tier', '        if tier != "inherit":\n            return tier\n'
     '        return "dev"', "GuardLogic.test_g11_multiprocessing_inherits_parent"),
    ("K9", "        if self.root not in t or self.root_start is None or t[self.root][2] != self.root_start:",
     "        if self.root not in t:", "GuardLogic.test_g14_treecap_pid_reuse"),
    ("K10", '    elif how == "inline" or (how == "stdin" and stdin in ("pipe", "file")):',
     '    elif how in ("inline", "stdin"):', "GuardLogic.test_g3b_relative_cwd_and_python_flags"),
    ("K11", "        if path and ((os.path.isabs(what) and not os.path.isdir(path)) or os.path.isfile(path)):",
     "        if path and (os.path.isabs(what) or os.path.isfile(path)):", "GuardLogic.test_g16_unknown_value_options_not_dev"),
    ("K12", "            if fresh != self.args[key]:", "            if False:", "GuardLogic.test_g18_reread_argv_before_acting"),
    ("K13", "        self._killpg_locked()\n", "", "RunnerCap.test_g23_memcap_collects_detached_daemon"),
]

RUNNER_KNIVES = [
    ("R1", "                _kill_group(proc, cap)\n                out, err = _drain(proc, e)\n                proc.wait()\n",
     "                proc.kill()\n                out, err = proc.communicate()\n",
     "RunnerCap.test_g15_timeout_with_background_grandchild"),
    ("R2", "        if c and c not in cells:", "        if False:", "RunnerCap.test_g8_run_one_memory_cap"),
    ("R3", "            rc, cut = 137, MEMCAP_CELL", "            cut = MEMCAP_CELL", "RunnerCap.test_g8_run_one_memory_cap"),
    ("R4", "                _kill_group(proc, cap)\n                out, err = _drain(proc, e)\n",
     "                proc.kill()\n                out, err = _drain(proc, e)\n",
     "RunnerCap.test_g19_main_exits_background_orphan_collected"),
    ("R5", "            except BaseException:\n", "            except ZeroDivisionError:\n",
     "Interrupt.test_g20_ctrl_c_collects_running_test"),
    ("R5h", "            except BaseException:\n", "            except ZeroDivisionError:\n",
     "Interrupt.test_g21_sighup_collects_running_test"),
    ("R6", "    finally:\n        _signals_blocked(_kill_group, proc, cap)", "    except ZeroDivisionError:\n        pass\n"
     "    _kill_group(proc, cap)", "Interrupt.test_g24_double_ctrl_c_still_collects"),
    ("R6h", "    finally:\n        _signals_blocked(_kill_group, proc, cap)", "    except ZeroDivisionError:\n        pass\n"
     "    _kill_group(proc, cap)", "Interrupt.test_g25_ctrl_c_then_sighup_still_collects"),
]

ENTRY_KNIVES = [
    ("T1", "    if signal.getsignal(signal.SIGHUP) != signal.SIG_IGN:\n        signal.signal(signal.SIGHUP, _on_term)\n",
     "", "Interrupt.test_g21_sighup_collects_running_test"),
]


class Knife(unittest.TestCase):
    def knife(self, rel, tag, needle, repl, target):
        name = Path(__file__).name
        with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                            max_procs=40, timeout=180)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
        self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
        self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))

    def test_knives(self):
        for tag, needle, repl, target in KNIVES:
            with self.subTest(tag):
                self.knife("src/cockpit/memguard.py", tag, needle, repl, target)

    def test_runner_knives(self):
        for tag, needle, repl, target in RUNNER_KNIVES:
            with self.subTest(tag):
                self.knife("tools/verify/runner.py", tag, needle, repl, target)

    def test_entry_knives(self):
        for tag, needle, repl, target in ENTRY_KNIVES:
            with self.subTest(tag):
                self.knife("tools/verify/test_entry.py", tag, needle, repl, target)


if __name__ == "__main__":
    unittest.main()
