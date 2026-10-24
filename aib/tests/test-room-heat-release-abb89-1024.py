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
R8 自動化 Chrome 的 --user-data-dir 不在暫存根(真人的除錯用 Chrome)⇒ 不是候選
刀(複本):K1 開始時間不比 ⇒ R6 紅;K2 拿掉主機動作排除 ⇒ R3 紅;K3 子孫不檢查暫存根 ⇒ R4 紅
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
    if os.fork() == 0:
        os.setsid()
        p = subprocess.Popen([sys.executable, script], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
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

    def spawn(self, body, name="t"):
        d = self.tmp / name / "tests"
        d.mkdir(parents=True)
        script = d / "test-fake.py"
        script.write_text(textwrap.dedent(body).replace("@DIR@", str(d)), encoding="utf-8")
        out = self.tmp / (name + ".pid")
        subprocess.run([sys.executable, "-c", LAUNCH, str(script), str(out)], check=True, timeout=30)
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
        return RR.scan(owner_of=lambda p: (None, None), lsof=lambda pids: False)

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
        self.assertIn("host_operation", [h["why"] for h in held])

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
        cands, held = RR.scan(owner_of=lambda p: (None, None))
        self.assertIsNone(self.find(pid, cands))
        self.assertIn("holds_lock", [h["why"] for h in held])

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
        args = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=9222 --user-data-dir=/Users/u/ChromeDev"
        rows = [(pid, 1, os.getuid(), 1.0, 1024, "Google Chrome", args)]
        cands, _ = RR.scan(rows=rows, owner_of=lambda p: (None, None), lsof=lambda pids: False)
        self.assertEqual(cands, [])
        tmp_args = args.replace("/Users/u/ChromeDev", tempfile.gettempdir() + "/puppeteer_dev_profile-x")
        cands, _ = RR.scan(rows=[(pid, 1, os.getuid(), 1.0, 1024, "Google Chrome", tmp_args)],
                           owner_of=lambda p: (None, None), lsof=lambda pids: False)
        self.assertEqual([c["kind"] for c in cands], ["chrome_orphan"])


class Knives(unittest.TestCase):
    def _knife(self, old, new, case):
        from aib_knife_copy import knife_copy
        from aib_proc_guard import run_guarded
        name = Path(__file__).name
        with knife_copy(ROOT, "src/cockpit/room_heat_release.py", old, new, tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), case], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s" % r.tripped)
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_no_start_check(self):
        self._knife("    return bool(ident) and ident[0] == me and ident[2] == start\n",
                    "    return bool(ident) and ident[0] == me\n", "Orphans.test_r6_pid_reuse_guard")

    def test_k2_no_host_check(self):
        self._knife("        if any(HOST_RE.search(by[p][6]) for p in everyone if p in by):\n",
                    "        if False:\n", "Orphans.test_r3_ssh_child_held")

    def test_k3_children_not_checked(self):
        self._knife("                            and _under_temp(by[p][6], roots)]\n",
                    "                            ]\n", "Orphans.test_r4_foreign_child_survives")


if __name__ == "__main__":
    unittest.main()
