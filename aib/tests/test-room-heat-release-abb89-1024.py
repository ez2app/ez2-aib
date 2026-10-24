#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat release:只收白名單殘留。用真的孤兒行程(雙層 fork,父行程變 1)測,只殺測試自己起的 pid;
狀態根在暫存夾。孤兒沒被收養到 1(某些容器有 subreaper)⇒ 整支跳過,不假裝驗過。
R1 孤兒測試行程 ⇒ test_orphan 候選;public 不帶開始時間;釋放後行程不在、紀錄不帶指令列、檔案 0600
R2 編號過期(行程已結束)⇒ candidate_changed;格式不對 ⇒ bad_candidate;兩種都不殺任何東西
R3 子行程有 ssh ⇒ 只列在 held(host_operation),不是候選
R4 子孫裡有一支不符判法(指令列不在暫存根)⇒ 不在 pids、釋放後還活著
R5 開著 *.lock ⇒ held(holds_lock)
R6 pid 重用:開始時間對不上 ⇒ 不送訊號、算 failed
R7 --auto 10 分鐘上限到了 ⇒ auto_limit、不殺
R8 自動化 Chrome 的 --user-data-dir 不在暫存根(真人的除錯用 Chrome)、沒有真的自動化旗標、--app= 視窗 ⇒ 不是候選
R9 路徑把戲(#319 r3 High):家目錄裡的 /tmp/、/tmp/../、暫存路徑跟測試路徑分在兩個引數、沙盒世界、
   艦長室設定檔 ⇒ 都不是候選;正控制組:暫存根底下的 tests/test-x.py ⇒ 是
R10 暫存根不讀環境變數;/ 與家目錄注入也會被丟掉
R11 --auto 額度:壞行逐行跳過、負數夾 0、讀不了 ⇒ 算滿
R12 行程在送訊號前自己結束 ⇒ 不算失敗;R13 剛起不到 MIN_AGE_S ⇒ 不是候選
R14 argv[0] 不是直譯器(tmux server、caffeinate、常駐程式)、-m / -c、node --require 的值 ⇒ 不是候選;
    Chrome 多個 / 空白分隔的 --user-data-dir、-app= ⇒ 不是
R15 --auto:紀錄寫不進去 ⇒ 不殺(log_failed);無限大 / 未來時間的紀錄 ⇒ 額度不歸零
R16 快照整合:候選不帶 starts、掃描壞了標 candidates_detail;CLI 只有總代碼時印那個代碼,不印「沒有殘留」
R17 stdout 導到檔案(nohup … > log &)⇒ held detached_output;R18 lsof 跑不了 ⇒ held lsof_unavailable
R19 dry_run 不殺、不寫紀錄;R20 一個收掉一個身分不符 ⇒ partial;R21 結果記不進帳 ⇒ 同一筆掛 log、ok 假
R22 兩個候選合計超過額度 ⇒ 第二個 auto_limit;R23(macOS)正式暫存根含系統給的使用者暫存夾
R24 lsof 跑得起來卻回空 / 沒列出這個 pid ⇒ 量不到(held lsof_unavailable),不當「沒鎖、沒導檔」
R25 輸出接終端機 / 有人讀的 pipe / FIFO ⇒ attached_output;/dev/null、關掉、讀端沒人 ⇒ 可收;Linux 用 /proc 找 pipe 對端
R26 真的 pipe:讀端開著 ⇒ attached_output,讀端關掉 ⇒ 可收
R27 Chrome --user-data-dir 帶空白(切出來那段不準)⇒ 不是候選;後面接網址照樣是
R28 另一支 --auto 拿著鎖 ⇒ auto_busy、不排隊;R29 掃描丟例外 / 回 None ⇒ scan_failed;文字快照印候選編號與不收原因
R30 自己(測試行程)不會變候選;R31 額度帳是 FIFO / symlink ⇒ 當滿額、不卡住
R32 子孫的開始時間晚於這次掃描(pid 被新行程拿走)⇒ 不在 pids
暫存根一律用 scan(roots=) 注入這支測試的暫存夾(正式程式連 TMPDIR 都不讀)。
刀(複本,要紅在指定那一格;目標格被跳過 ⇒ 刀也跳過):K1 開始時間不比 ⇒ R6 紅;K2 拿掉主機動作排除 ⇒ R3 紅;
K3 子孫不檢查暫存根 ⇒ R4 紅;K4 暫存根改回子字串比對 ⇒ R9 紅;K5 拿掉 *.lock 排除 ⇒ R5 紅;K6 argv[0] 不看直譯器 ⇒ R14 紅;
K7 不先記帳就殺 ⇒ R15 紅;K8 拿掉背景輸出判斷 ⇒ R17 紅;K9 拿掉年齡判斷 ⇒ R13 紅;K10 接著東西也當可收 ⇒ R25 紅;
K11 lsof 沒列出主行程不當量不到 ⇒ R24 紅;K12 子孫不比開始時間 ⇒ R32 紅;K13 拿掉「不收自己」⇒ R30 紅
"""
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

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
sys.path.insert(0, str(HERE))
os.environ.setdefault("EZ2_AIB_LOCALE", "zh-TW")

import room_heat_release as RR  # noqa: E402

LAUNCH = textwrap.dedent("""
    import os, subprocess, sys
    script, out = sys.argv[1], sys.argv[2]
    log = sys.argv[3] if len(sys.argv) > 3 else None
    if os.fork() == 0:
        os.setsid()
        dest = open(log, "w") if log else subprocess.DEVNULL
        p = subprocess.Popen([sys.executable, script], stdin=subprocess.DEVNULL, stdout=dest,
                             stderr=subprocess.STDOUT if log else subprocess.DEVNULL, close_fds=True)
        with open(out + ".tmp", "w") as fh:
            fh.write(str(p.pid))
        os.replace(out + ".tmp", out)
        os._exit(0)
    os.wait()
""")


class Orphans(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="heatrel-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": str(self.tmp / "state")})
        p.start()
        self.addCleanup(p.stop)
        self.mine = []                                  # (pid, start) 測試自己起的,收尾只殺這些
        self.root = os.path.realpath(str(self.tmp))
        self.roots = [self.root + "/"]

    def tearDown(self):
        me = os.getuid()
        for pid, start in self.mine:
            if RR._same(pid, start, me):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

    def track(self, pid):
        ident = RR.identity(pid)
        if ident:
            self.mine.append((pid, ident[2]))
        return ident

    def spawn(self, body, name="t", log=None):
        d = self.tmp / name / "tests"
        d.mkdir(parents=True)
        script = d / "test-fake.py"
        script.write_text(textwrap.dedent(body).replace("@DIR@", str(d)), encoding="utf-8")
        out = self.tmp / (name + ".pid")
        subprocess.run([sys.executable, "-c", LAUNCH, str(script), str(out)] + ([str(log)] if log else []),
                       check=True, timeout=30)
        for _ in range(100):
            if out.exists():
                break
            time.sleep(0.05)
        pid = int(out.read_text())
        for _ in range(100):
            ident = self.track(pid)
            if ident and ident[1] == 1:
                return pid, d
            time.sleep(0.05)
        self.skipTest("孤兒沒被收養到 pid 1(容器 subreaper?)—— 本支跳過,沒驗")

    def child_pids(self, pid, want, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            out = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True).stdout
            kids = [int(a) for a, b in (ln.split() for ln in out.splitlines() if ln.strip()) if int(b) == pid]
            if len(kids) >= want:
                for k in kids:
                    self.track(k)
                return kids
            time.sleep(0.1)
        return []

    def find(self, pid, cands):
        return next((c for c in cands if pid in c["pids"] or c["pids"][:1] == [pid]), None)

    def scan(self):
        return RR.scan(owner_of=lambda p: (None, None), lsof=lambda pids: False, min_age_s=0, roots=self.roots,
                       detached=lambda p: False)

    def kinds(self, pid, args, **kw):
        rows = [(pid, 1, os.getuid(), 1.0, 1024, "x", args)]
        kw.setdefault("roots", self.roots)
        kw.setdefault("detached", lambda p: False)
        cands, _ = RR.scan(rows=rows, owner_of=lambda p: (None, None), lsof=lambda pids: False, min_age_s=0, **kw)
        return [c["kind"] for c in cands]

    def test_r1_release_orphan(self):
        pid, _d = self.spawn("import time\ntime.sleep(300)\n")
        cands, _held = self.scan()
        c = self.find(pid, cands)
        self.assertIsNotNone(c, cands)
        self.assertEqual(c["kind"], "test_orphan")
        self.assertNotIn("starts", RR.public(c))
        r = RR.release(ids=[c["id"]], wait_s=3, scan_fn=self.scan)
        self.assertEqual([x["detail"] for x in r["results"]], ["ok"], r)
        for _ in range(40):
            if RR.identity(pid) is None:
                break
            time.sleep(0.05)
        self.assertIsNone(RR.identity(pid), "釋放回 ok 但行程還在")
        log = Path(RR.released_path())
        self.assertTrue(str(log).startswith(str(self.tmp / "state")))
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        rec = json.loads(log.read_text().splitlines()[-1])
        self.assertEqual((rec["kind"], rec["mode"], rec["result"], rec["n_pids"]), ("test_orphan", "manual", "ok", 1))
        self.assertNotIn("test-fake", log.read_text())

    def test_r2_changed_and_bad(self):
        pid, _d = self.spawn("import time\ntime.sleep(300)\n")
        c = self.find(pid, self.scan()[0])
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.3)
        r = RR.release(ids=[c["id"]], scan_fn=self.scan)
        self.assertEqual([x["detail"] for x in r["results"]], ["candidate_changed"])
        r = RR.release(ids=["../../x"])
        self.assertEqual(r["detail"], "bad_candidate")

    def test_r3_ssh_child_held(self):
        pid, d = self.spawn("""
            import subprocess, sys, time
            open("@DIR@/ssh.py", "w").write("import time\\ntime.sleep(300)\\n")
            subprocess.Popen([sys.executable, "@DIR@/ssh.py"])
            time.sleep(300)
        """)
        self.assertTrue(self.child_pids(pid, 1))
        cands, held = self.scan()
        self.assertIsNone(self.find(pid, cands))
        self.assertIn(("host_operation", pid), [(h["why"], h["pid"]) for h in held])

    def test_r4_foreign_child_survives(self):
        pid, _d = self.spawn("""
            import subprocess, time
            subprocess.Popen(["sleep", "300"])
            time.sleep(300)
        """)
        kids = self.child_pids(pid, 1)
        self.assertTrue(kids)
        c = self.find(pid, self.scan()[0])
        self.assertIsNotNone(c)
        self.assertEqual(c["pids"], [pid])
        RR.release(ids=[c["id"]], wait_s=3, scan_fn=self.scan)
        ident = RR.identity(kids[0])
        self.assertIsNotNone(ident, "不符判法的子行程被收掉了")

    @unittest.skipUnless(shutil.which("lsof"), "沒有 lsof")
    def test_r5_lock_held(self):
        pid, d = self.spawn("""
            import time
            fh = open("@DIR@/deploy.lock", "w")
            time.sleep(300)
        """)
        time.sleep(0.5)
        cands, held = RR.scan(owner_of=lambda p: (None, None), min_age_s=0, roots=self.roots)
        self.assertIsNone(self.find(pid, cands))
        self.assertIn(("holds_lock", pid), [(h["why"], h["pid"]) for h in held])
        free, _d2 = self.spawn("import time\ntime.sleep(300)\n", name="free")   # 負控制組:真 lsof、沒開鎖 ⇒ 是候選
        time.sleep(0.3)
        cands, held = RR.scan(owner_of=lambda p: (None, None), min_age_s=0, roots=self.roots)
        self.assertIsNotNone(self.find(free, cands), held)

    def test_r6_pid_reuse_guard(self):
        pid, _d = self.spawn("import time\ntime.sleep(300)\n")
        killed, failed = RR._kill_one({"pids": [pid], "starts": {str(pid): "1.000000"}}, os.getuid(), wait_s=0.5)
        self.assertEqual((killed, failed), (0, 1))
        self.assertIsNotNone(RR.identity(pid))

    def test_r7_auto_limit(self):
        pid, _d = self.spawn("import time\ntime.sleep(300)\n")
        log = Path(RR.released_path())
        log.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        log.write_text(json.dumps({"t": now - 60, "mode": "auto", "n_pids": RR.AUTO_MAX_PER_WINDOW}) + "\n")
        r = RR.release(all_safe=True, auto=True, scan_fn=lambda: ([self.find(pid, self.scan()[0])], []), now=now)
        self.assertEqual([x["detail"] for x in r["results"]], ["auto_limit"])
        self.assertIsNotNone(RR.identity(pid))

    def test_r8_human_debug_chrome_not_candidate(self):
        pid, _d = self.spawn("import time\ntime.sleep(300)\n")
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        prof = self.root + "/puppeteer_dev_profile-x"
        self.assertEqual(self.kinds(pid, chrome + " --headless --remote-debugging-port=9222 --user-data-dir=/Users/u/ChromeDev"), [])
        self.assertEqual(self.kinds(pid, chrome + " --remote-debugging-port=9222 --user-data-dir=" + prof), [])  # 沒自動化旗標
        self.assertEqual(self.kinds(pid, chrome + " --headless --app=http://x --user-data-dir=" + prof), [])
        self.assertEqual(self.kinds(pid, chrome + " --headless --remote-debugging-port=9222 --user-data-dir=" + prof),
                         ["chrome_orphan"])

    def test_r9_path_tricks(self):
        pid, d = self.spawn("import time\ntime.sleep(300)\n")
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --headless --remote-debugging-port=1"
        tmp = self.root
        bad = [
            "python3 /Users/u/x%stests/test-x.py" % self.roots[0],              # 暫存根出現在路徑中間(子字串會中)
            "python3 %s%sUsers/u/tests/test-x.py" % (self.roots[0], "../" * self.roots[0].count("/")),  # 暫存根開頭但 .. 走到 / 再出去
            "python3 /Users/u/proj/tmp/tests/test-live.py",                       # 家目錄裡剛好有一層 tmp/
            "python3 /tmp/../Users/u/aib-x/tests/test-x.py",                      # .. 走出暫存根
            "python3 --log /tmp/x.log /Users/u/aib-plugins/tests/test-x.py",      # 暫存路徑跟測試路徑分在兩個引數
            "python3 %s/aib-e2e-w1/tests/test-x.py" % tmp,                        # 沙盒世界這一版不收
            "python3 %s/aib-power-ask-x/serve.py" % tmp,                          # 暫存根底下但不是測試腳本
            chrome + " --user-data-dir=/Users/u/work/tmp/prof",
            chrome + " --user-data-dir=/tmp/../Users/u/Library/Application Support/Google/Chrome",
            chrome + " --user-data-dir=%s/st/captain-room/browser-profile" % tmp,   # 暫存狀態根下的艦長室視窗
        ]
        link = d / "out-link"
        link.symlink_to("/Users")
        bad.append("python3 %s/u/tests/test-x.py" % link)                         # symlink 走出暫存根
        for roots in (self.roots, self.roots + ["/private/tmp/"]):           # 也用含 /private/tmp 的根跑(B-L6)
            for args in bad:
                self.assertEqual(self.kinds(pid, args, roots=roots), [], (roots, args))
        self.assertEqual(self.kinds(pid, "python3 %s/tests/test-x.py" % d), ["test_orphan"])     # 正控制組

    def test_r10_roots_not_from_env(self):
        pid, _d = self.spawn("import time\ntime.sleep(300)\n")
        home = os.path.realpath(os.path.expanduser("~"))
        lib = os.path.join(home, "Library")
        with mock.patch.dict(os.environ, {"AIB_HEAT_TEMP_ROOTS": "/:" + home, "TMPDIR": lib, "TEMP": lib, "TMP": lib}):
            roots = RR.temp_roots()
            self.assertNotIn(lib.rstrip("/") + "/", roots)                 # TMPDIR 指到家目錄底下也不收
            self.assertFalse(any(r.startswith(home + "/") for r in roots), roots)
            self.assertEqual(self.kinds(pid, "python3 %s/r/tests/test-x.py" % home, roots=None), [])
        for r in ("/", home, os.path.dirname(home), "/Users"):
            self.assertNotIn(os.path.realpath(r).rstrip("/") + "/", RR.temp_roots(extra=(r,)), r)

    def test_r11_auto_quota_fails_closed(self):
        log = Path(RR.released_path())
        log.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        log.write_text(json.dumps({"t": now - 60, "mode": "auto", "n_pids": 5}) + "\n{broken\n"
                       + json.dumps({"t": now - 30, "mode": "auto", "n_pids": -1000}) + "\n"
                       + json.dumps({"t": now - 20, "mode": "auto", "n_pids": 3}) + "\n", encoding="utf-8")
        self.assertEqual(RR._auto_used(now), 8)
        log.unlink()
        log.mkdir()                                              # 讀不了 ⇒ 當作額度用完
        self.assertEqual(RR._auto_used(now), RR.AUTO_MAX_PER_WINDOW)

    def test_r12_already_gone_not_failed(self):
        pid, _d = self.spawn("import time\ntime.sleep(300)\n")
        start = RR.identity(pid)[2]
        os.kill(pid, signal.SIGKILL)
        for _ in range(40):
            if RR.identity(pid) is None:
                break
            time.sleep(0.05)
        self.assertEqual(RR._kill_one({"pids": [pid], "starts": {str(pid): start}}, os.getuid(), wait_s=0.5), (0, 0))

    def test_r14_not_interpreter_or_inline(self):
        pid, d = self.spawn("import time\ntime.sleep(300)\n")
        T = "%s/tests/test-a.py" % d
        bad = [
            "tmux -L x -f /dev/null new-session -d -s p python3 " + T,
            "caffeinate -i " + T,
            "/usr/bin/python3 -m mydaemon " + T,
            "python3 -c import_time " + T,
            "/opt/homebrew/bin/python3 /Users/x/bin/aibd --watch " + T,
            "node --require %s/tests/test-hook.js /Users/x/live-server.js" % d,
            "node -r %s/tests/test-hook.js /Users/x/live-server.js" % d,
        ]
        for args in bad:
            self.assertEqual(self.kinds(pid, args), [], args)
        for ok in ("/usr/bin/python3 -B -W ignore " + T, sys.executable + " " + T, "node %s/tests/test-b.js" % d):
            self.assertEqual(self.kinds(pid, ok), ["test_orphan"], ok)
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --headless --remote-debugging-port=1 "
        prof = self.root + "/p1"
        for args in ("--user-data-dir=%s --user-data-dir=/Users/x/realprof" % prof,
                     "--user-data-dir=%s --user-data-dir /Users/x/realprof" % prof,
                     "-app=https://x --user-data-dir=" + prof, "--app https://x --user-data-dir=" + prof):
            self.assertEqual(self.kinds(pid, chrome + args), [], args)
        self.assertEqual(self.kinds(pid, chrome + "--user-data-dir=" + prof), ["chrome_orphan"])

    def test_r15_auto_log_unwritable_no_kill(self):
        pid, _d = self.spawn("import time\ntime.sleep(300)\n")
        log = Path(RR.released_path())
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("", encoding="utf-8")
        if os.getuid() == 0:
            self.skipTest("root 身分 chmod 0400 擋不住寫入")
        os.chmod(log, 0o400)
        self.addCleanup(os.chmod, log, 0o600)
        r = RR.release(all_safe=True, auto=True, scan_fn=self.scan, wait_s=1)
        self.assertEqual([x["detail"] for x in r["results"]], ["log_failed"], r)
        self.assertIsNotNone(RR.identity(pid), "記不進帳還是殺了")
        os.chmod(log, 0o600)
        now = time.time()
        log.write_text('{"mode":"auto","t":%s,"n_pids":1e999}\n' % now
                       + json.dumps({"mode": "auto", "t": now + 3600, "n_pids": 3}) + "\n", encoding="utf-8")
        self.assertEqual(RR._auto_used(now), RR.AUTO_MAX_PER_WINDOW + 3)
        log.write_text("", encoding="utf-8")
        r = RR.release(all_safe=True, auto=True, scan_fn=self.scan, wait_s=3)
        self.assertEqual([x["detail"] for x in r["results"]], ["ok"], r)
        recs = [json.loads(x) for x in log.read_text().splitlines()]
        self.assertEqual([(x["mode"], x["result"]) for x in recs], [("auto", "pending"), ("auto_done", "ok")])
        self.assertEqual(RR._auto_used(time.time()), 1)              # 額度用預留那筆算,結果那筆不重算

    @unittest.skipUnless(shutil.which("lsof"), "沒有 lsof")
    def test_r17_detached_output_held(self):
        pid, _d = self.spawn("import time\ntime.sleep(300)\n", log=self.tmp / "bg.log")
        time.sleep(0.3)
        cands, held = RR.scan(owner_of=lambda p: (None, None), lsof=lambda pids: False, min_age_s=0, roots=self.roots)
        self.assertIsNone(self.find(pid, cands))
        self.assertIn(("detached_output", pid), [(h["why"], h["pid"]) for h in held])

    def test_r18_lsof_unavailable(self):
        pid, _d = self.spawn("import time\ntime.sleep(300)\n")
        cands, held = RR.scan(owner_of=lambda p: (None, None), lsof=lambda pids: None, min_age_s=0, roots=self.roots,
                              detached=lambda p: False)
        self.assertIsNone(self.find(pid, cands))
        self.assertIn(("lsof_unavailable", pid), [(h["why"], h["pid"]) for h in held])
        cands, held = RR.scan(owner_of=lambda p: (None, None), lsof=lambda pids: False, min_age_s=0, roots=self.roots,
                              detached=lambda p: None)
        self.assertIn(("lsof_unavailable", pid), [(h["why"], h["pid"]) for h in held])

    def test_r19_dry_run(self):
        pid, _d = self.spawn("import time\ntime.sleep(300)\n")
        c = self.find(pid, self.scan()[0])
        r = RR.release(all_safe=True, dry_run=True, auto=True, scan_fn=lambda: ([c], []))
        self.assertEqual((r["ok"], r["results"]), (True, [{"id": c["id"], "detail": "dry_run", "kind": "test_orphan", "n_pids": 1}]))
        self.assertIsNotNone(RR.identity(pid))
        self.assertFalse(Path(RR.released_path()).exists())
        b, _d2 = self.spawn("import time\ntime.sleep(300)\n", name="b")      # 試跑也要停在額度上(r6 B-L1)
        cb = self.find(b, self.scan()[0])
        log = Path(RR.released_path())
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps({"t": time.time() - 30, "mode": "auto", "n_pids": RR.AUTO_MAX_PER_WINDOW - 1}) + "\n")
        r = RR.release(all_safe=True, dry_run=True, auto=True, scan_fn=lambda: ([c, cb], []))
        self.assertEqual([x["detail"] for x in r["results"]], ["dry_run", "auto_limit"], r)

    def test_r20_partial(self):
        a, _d = self.spawn("import time\ntime.sleep(300)\n", name="a")
        b, _d2 = self.spawn("import time\ntime.sleep(300)\n", name="b")
        c = self.find(a, self.scan()[0])
        c = dict(c, pids=[a, b], starts={str(a): c["starts"][str(a)], str(b): "1.000000"})
        r = RR.release(ids=[c["id"]], scan_fn=lambda: ([c], []), wait_s=3)
        self.assertEqual((r["ok"], r["detail"], r["results"][0]["detail"]), (False, "partial", "partial"))
        self.assertNotIn("freed_cpu", r["results"][0])
        self.assertIsNotNone(RR.identity(b), "身分不符的那支被殺了")

    def test_r21_result_log_failed(self):
        pid, _d = self.spawn("import time\ntime.sleep(300)\n")
        c = self.find(pid, self.scan()[0])

        def nolog(rec, environ=None):
            raise OSError("disk full")
        with mock.patch.object(RR, "_log", nolog):
            r = RR.release(ids=[c["id"]], scan_fn=lambda: ([c], []), wait_s=3)
        self.assertEqual((r["ok"], r["detail"], len(r["results"])), (False, "log_failed", 1))
        self.assertEqual((r["results"][0]["detail"], r["results"][0]["log"]), ("ok", "log_failed"))

    def test_r22_budget_two_candidates(self):
        a, _d = self.spawn("import time\ntime.sleep(300)\n", name="a")
        b, _d2 = self.spawn("import time\ntime.sleep(300)\n", name="b")
        cands = self.scan()[0]
        ca, cb = self.find(a, cands), self.find(b, cands)
        log = Path(RR.released_path())
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps({"t": time.time() - 30, "mode": "auto", "n_pids": RR.AUTO_MAX_PER_WINDOW - 1}) + "\n")
        r = RR.release(all_safe=True, auto=True, scan_fn=lambda: ([ca, cb], []), wait_s=3)
        self.assertEqual([x["detail"] for x in r["results"]], ["ok", "auto_limit"])
        self.assertIsNotNone(RR.identity(b))

    @unittest.skipUnless(sys.platform == "darwin", "只有 macOS")
    def test_r23_darwin_user_temp_in_roots(self):
        sys_tmp = [os.path.realpath(x).rstrip("/") + "/" for x in RR._system_temp()]
        self.assertTrue(sys_tmp and sys_tmp[0].startswith("/private/var/folders/"), sys_tmp)
        home = os.path.realpath(os.path.expanduser("~")) + "/"
        if home.startswith(sys_tmp[0]):                 # 家目錄在系統暫存夾底下(測試把 HOME 指到暫存)⇒ 照設計丟掉(r6 B-L3)
            self.assertNotIn(sys_tmp[0], RR.temp_roots())
        else:
            self.assertIn(sys_tmp[0], RR.temp_roots())

    def test_r24_lsof_blank_is_unmeasured(self):
        me = os.getpid()
        self.assertIsNone(RR._holds_lock([me], run=lambda c: ""))
        self.assertIsNone(RR._holds_lock([me], run=lambda c: "p1\nn/x.lock\n"))      # 列的是別人
        self.assertIsNone(RR._detached_output(me, run=lambda c: ""))
        self.assertIsNone(RR._detached_output(me, run=lambda c: "p1\nf1\ntREG\nn/x\n"))
        self.assertIs(RR._holds_lock([me], run=lambda c: "p%d\nn/x/cwd\n" % me), False)
        self.assertIs(RR._holds_lock([me, 2 ** 22 + 7], run=lambda c: "p%d\nn/x/cwd\n" % me), False)  # 子孫已經不在
        pid, _d = self.spawn("import time\ntime.sleep(300)\n")
        cands, held = RR.scan(owner_of=lambda p: (None, None), lsof=lambda pids: RR._holds_lock(pids, run=lambda c: ""),
                              min_age_s=0, roots=self.roots, detached=lambda p: False)
        self.assertIsNone(self.find(pid, cands))
        self.assertIn(("lsof_unavailable", pid), [(h["why"], h["pid"]) for h in held])

    def test_r25_output_attached(self):
        me = os.getpid()
        det = lambda body, **k: RR._detached_output(me, run=lambda c: "p%d\n%s" % (me, body), **k)   # noqa: E731
        self.assertEqual(det("f1\ntCHR\nn/dev/ttys001\n"), "attached_output")
        self.assertEqual(det("f2\ntPIPE\nn->0xabc\n"), "attached_output")
        self.assertEqual(det("f1\ntunix\nn->0xabc\n"), "attached_output")
        self.assertEqual(det("f1\ntREG\nn/x/bg.log\n"), "detached_output")
        self.assertIs(det("f1\ntCHR\nn/dev/null\nf2\ntCHR\nn/dev/null\n"), False)
        self.assertIs(det("f1\ntPIPE\nn\nf2\ntCHR\nn/dev/null\n"), False)             # 讀端都關了
        self.assertIs(det("f0\ntCHR\nn/dev/ttys001\nf3\ntREG\nn/x\n"), False)           # 只看 1 / 2
        self.assertIs(det("fcwd\ntDIR\nn/x\n"), False)                                     # 1 / 2 都關了
        with mock.patch.object(RR.sys, "platform", "linux"):
            fifo = "f1\ntFIFO\nnpipe\n"
            self.assertEqual(det(fifo, pipe_peer=lambda p, fd, ex: True), "attached_output")
            self.assertIs(det(fifo, pipe_peer=lambda p, fd, ex: False), False)
            self.assertIsNone(det(fifo, pipe_peer=lambda p, fd, ex: None))

    @unittest.skipUnless(shutil.which("lsof"), "沒有 lsof")
    def test_r26_real_pipe(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        self.track(p.pid)
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        self.assertEqual(RR._detached_output(p.pid), "attached_output")
        p.stdout.close()
        time.sleep(0.2)
        self.assertIs(RR._detached_output(p.pid), False)

    def test_r27_chrome_user_data_dir_with_space(self):
        pid, _d = self.spawn("import time\ntime.sleep(300)\n")
        chrome = "/opt/chrome/chrome --headless --remote-debugging-port=1 --user-data-dir="
        self.assertEqual(self.kinds(pid, chrome + self.root + "/x /../../../Users/me/Library/Chrome"), [])
        self.assertEqual(self.kinds(pid, chrome + self.root + "/x y z/../../../../Users/me/Lib"), [])     # 多段空白(r7 L1)
        self.assertEqual(self.kinds(pid, chrome + self.root + "/x y /../../../../Users/me/Lib"), [])
        self.assertEqual(self.kinds(pid, chrome + self.root + "/x about:blank --no-first-run"), ["chrome_orphan"])
        self.assertEqual(self.kinds(pid, chrome + self.root + "/x about:blank"), ["chrome_orphan"])
        self.assertEqual(self.kinds(pid, chrome + self.root + "/x https://example.com/a"), ["chrome_orphan"])

    def test_r28_auto_busy(self):
        import fcntl
        guard = RR.released_path() + ".guard"
        os.makedirs(os.path.dirname(guard), exist_ok=True)
        fd = os.open(guard, os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        called = []
        t = time.monotonic()
        r = RR.release(all_safe=True, auto=True, scan_fn=lambda: called.append(1) or ([], []))
        self.assertEqual((r["ok"], r["detail"], called), (False, "auto_busy", []))
        self.assertLess(time.monotonic() - t, 5)

    def test_r29_scan_failed(self):
        import io
        import room_heat as RH

        def boom():
            raise RuntimeError("scan")
        self.assertEqual(RR.release(all_safe=True, scan_fn=boom)["detail"], "scan_failed")
        self.assertEqual(RR.release(all_safe=True, scan_fn=lambda: (None, None))["detail"], "scan_failed")
        out = io.StringIO()
        with mock.patch.object(RR, "release", lambda **k: {"ok": False, "detail": "scan_failed", "results": []}):
            RH.main(["release", "--all-safe"], out=out)
        self.assertIn(RH.T("release.scan_failed"), out.getvalue())
        out = io.StringIO()
        s = {"level": "ok", "cells": {}, "groups": {"items": []}, "candidates_detail": "scan_failed",
             "candidates": [{"id": "b" * 16, "kind": "test_orphan", "owner": "", "cpu": 1.0, "mem_mb": 2.0, "age_s": 300}],
             "held": [{"kind": "chrome_orphan", "owner": "", "pid": 42, "why": "attached_output"}]}
        RH._print_snapshot(s, out)
        text = out.getvalue()
        for want in ("b" * 16, RH.T("held.attached_output"), RH.T("snap.scan_failed"), RH.T("kind.test_orphan")):
            self.assertIn(want, text)
        self.assertNotIn("aib.heat.", text)

    def test_r30_self_not_candidate(self):
        d = self.tmp / "self" / "tests"
        d.mkdir(parents=True)
        me, real = os.getpid(), RR.identity
        self.assertIn(me, RR._protected())

        def ident(p):                                   # 讓自己看起來像孤兒(ppid 1、夠老),只剩「不收自己」那一關擋(r7 L4)
            r = real(p)
            return (r[0], 1, r[2], r[3] - 3600) if p == me and r else r
        with mock.patch.object(RR, "identity", ident), mock.patch.object(RR, "_protected", lambda: {me}):
            self.assertEqual(self.kinds(me, "python3 %s/test-x.py" % d), [])

    def test_r31_auto_ledger_not_regular(self):
        log = Path(RR.released_path())
        log.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "mkfifo"):
            os.mkfifo(str(log))
            t = time.monotonic()
            self.assertEqual(RR._auto_used(time.time()), RR.AUTO_MAX_PER_WINDOW)
            self.assertLess(time.monotonic() - t, 5)
            log.unlink()
        real = self.tmp / "real.jsonl"
        real.write_text("", encoding="utf-8")
        log.symlink_to(real)
        self.assertEqual(RR._auto_used(time.time()), RR.AUTO_MAX_PER_WINDOW)
        log.unlink()
        log.write_bytes(b'{"mode":"auto","t":1,"n_pids":0}\n\xff\xfe\n')        # 不是 UTF-8 ⇒ 當滿額,不丟例外
        self.assertEqual(RR._auto_used(time.time()), RR.AUTO_MAX_PER_WINDOW)

    def test_r32_new_child_after_scan_start(self):
        pid, d = self.spawn("import time\ntime.sleep(300)\n")
        fake = 2 ** 22 + 11
        script = "python3 %s/test-fake.py" % d
        rows = [(pid, 1, os.getuid(), 1.0, 1024, "x", script), (fake, pid, os.getuid(), 1.0, 1024, "x", script)]
        real = RR.identity

        def ident(p):
            if p == fake:
                t = time.time() + 5
                return (os.getuid(), pid, "%d.%06d" % (int(t), 0), float(int(t)))
            return real(p)
        with mock.patch.object(RR, "identity", ident):
            cands, _ = RR.scan(rows=rows, owner_of=lambda p: (None, None), lsof=lambda pids: False, min_age_s=0,
                               roots=self.roots, detached=lambda p: False)
        c = self.find(pid, cands)
        self.assertIsNotNone(c)
        self.assertEqual(c["pids"], [pid])

    def test_r13_too_young(self):
        pid, d = self.spawn("import time\ntime.sleep(300)\n")
        rows = [(pid, 1, os.getuid(), 1.0, 1024, "x", "python3 %s/tests/test-fake.py" % d)]
        cands, _ = RR.scan(rows=rows, owner_of=lambda p: (None, None), lsof=lambda pids: False, roots=self.roots,
                           detached=lambda p: False)       # 沒有 lsof 的機器也要走到年齡那一關(r6 B-L2)
        self.assertEqual(cands, [])

    def test_r16_snapshot_and_cli(self):
        import io
        import room_heat as RH
        pid, _d = self.spawn("import time\ntime.sleep(300)\n")
        sampler = lambda: ([], 0.0)                                         # noqa: E731
        fake_held = [{"kind": "test_orphan", "owner": "", "pid": 1234, "why": "holds_lock"}]
        s = RH.snapshot(sampler=sampler, record=False, scan_fn=lambda **k: (self.scan()[0], fake_held))
        self.assertEqual(s["held"], fake_held)
        c = self.find(pid, s["candidates"])
        self.assertIsNotNone(c)
        self.assertNotIn("starts", c)
        self.assertNotIn("candidates_detail", s)

        def boom(**k):
            raise RuntimeError("scan")
        s = RH.snapshot(sampler=sampler, record=False, scan_fn=boom)
        self.assertEqual((s["candidates"], s["candidates_detail"]), ([], "scan_failed"))
        out = io.StringIO()
        with mock.patch.object(RR, "release", lambda **k: {"ok": False, "detail": "log_failed", "results": []}):
            self.assertEqual(RH.main(["release", "--all-safe", "--auto"], out=out), 1)
        self.assertIn(RH.T("release.log_failed"), out.getvalue())
        self.assertNotIn("release.", out.getvalue())                        # 沒有原始鍵名
        start = RR.identity(pid)[2]                                         # 先自己結束 ⇒ candidate_changed、不報省下多少
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.3)
        gone = {"id": "a" * 16, "kind": "test_orphan", "owner": "", "pids": [pid], "starts": {str(pid): start},
                "cpu": 1.0, "mem_mb": 1.0}
        r = RR.release(ids=["a" * 16, "a" * 16], scan_fn=lambda: ([gone], []))
        self.assertEqual(r["results"], [{"id": "a" * 16, "detail": "candidate_changed", "kind": "test_orphan"}])
        self.assertEqual(r["detail"], "candidate_changed")


class Knives(unittest.TestCase):
    def _knife(self, old, new, case):
        from aib_knife_copy import knife_copy
        from aib_proc_guard import run_guarded
        name = Path(__file__).name
        with knife_copy(ROOT, "src/cockpit/room_heat_release.py", old, new, tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), case], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s" % r.tripped)
        text = r.stdout + r.stderr
        if r.returncode == 0 and "skipped" in text:     # 目標格在這台被跳過(沒 lsof / subreaper / root)⇒ 刀也沒驗(r6 B-L4)
            self.skipTest("目標格跳過:" + case)
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + text)
        self.assertIn("FAIL: " + case.rsplit(".", 1)[1], text, "紅在別的地方(不是目標斷言)\n" + text)
        self.assertNotIn("ERROR: ", text, "刀版壞掉(匯入 / 語法),不算砍出紅\n" + text)

    def test_k1_no_start_check(self):
        self._knife("        if ident[0] != me or ident[2] != c[\"starts\"][str(p)]:\n",
                    "        if ident[0] != me:\n", "Orphans.test_r6_pid_reuse_guard")

    def test_k2_no_host_check(self):
        self._knife("        if any(HOST_RE.search(by[p][6]) for p in everyone if p in by):\n",
                    "        if False:\n", "Orphans.test_r3_ssh_child_held")

    def test_k3_children_not_checked(self):
        self._knife("                            and _under(_script(by[p][6]), roots) and not room_heat.SANDBOX_RE.search(by[p][6])]\n",
                    "                            ]\n", "Orphans.test_r4_foreign_child_survives")

    def test_k6_any_argv0(self):
        self._knife("    if not toks or not INTERP_RE.match(os.path.basename(toks[0])):\n",
                    "    if not toks:\n", "Orphans.test_r14_not_interpreter_or_inline")

    def test_k7_kill_before_reserve(self):
        self._knife("        if auto:                                       # 殺之前先記一筆預留",
                    "        if False:                                      # 殺之前先記一筆預留",
                    "Orphans.test_r15_auto_log_unwritable_no_kill")

    def test_k4_substring_roots(self):
        self._knife("    return any(rp.startswith(r) for r in roots)\n",
                    "    return any(r in path for r in roots)\n", "Orphans.test_r9_path_tricks")

    @unittest.skipUnless(shutil.which("lsof"), "沒有 lsof")
    def test_k5_no_lock_check(self):
        self._knife("        lk = (lsof or _holds_lock)(everyone)\n", "        lk = False\n", "Orphans.test_r5_lock_held")

    @unittest.skipUnless(shutil.which("lsof"), "沒有 lsof")
    def test_k8_no_detached_check(self):
        self._knife("            dt = detached(pid) if detached else _detached_output(pid, exclude=everyone)\n",
                    "            dt = False\n", "Orphans.test_r17_detached_output_held")

    def test_k9_no_min_age(self):
        self._knife("        if not ident or ident[0] != me or ident[1] != 1 or max(0.0, now - ident[3]) < min_age_s:\n",
                    "        if not ident or ident[0] != me or ident[1] != 1:\n", "Orphans.test_r13_too_young")

    def test_k10_attached_counts_as_free(self):
        self._knife("        why = \"attached_output\"\n", "        pass\n", "Orphans.test_r25_output_attached")

    def test_k11_blank_lsof_trusted(self):
        self._knife("    if pids and str(pids[0]) not in got:\n", "    if False:\n", "Orphans.test_r24_lsof_blank_is_unmeasured")

    def test_k13_self_not_protected(self):
        self._knife("        if uid != me or ppid != 1 or pid in protected:\n", "        if uid != me or ppid != 1:\n",
                    "Orphans.test_r30_self_not_candidate")

    def test_k12_child_start_not_checked(self):
        self._knife(" and (p == pid or _start_s(idents[p]) < t0)]\n", "]\n", "Orphans.test_r32_new_child_after_scan_start")


if __name__ == "__main__":
    unittest.main()
