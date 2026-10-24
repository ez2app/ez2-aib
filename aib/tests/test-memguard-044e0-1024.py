#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""memguard(記憶體暴衝守門)+ aib test 每支測試記憶體上限(10-24 20:34 整台卡死實案)。
狀態根在暫存夾;真行程只起測試自己的子孫,只殺自己起的 pid。
G1 門檻:預設 max(4 GB, 35% 實體記憶體);環境變數可改但不低於 1 GB;aib test 上限預設 2048、0 = 關、不低於 256
G2 直譯器連續 2 拍超過門檻才收(TERM);一拍尖峰不收;非直譯器(claude)、別的 uid、自己的祖先一律不看
G3 開發分級:指令列有 scratchpad / tests / tmp 記號 ⇒ 用 dev 上限;沒有 ⇒ 一般上限
G4 pid 重用:送訊號前開始時間對不上 ⇒ changed,不送任何訊號
G5 dry-run 不殺、留痕一行、紀錄檔 0600、不含整條指令列
G6 鎖:同一個狀態根只有一條守門拿得到鎖
G7 TreeCap 真行程:孫行程配置 400 MB、上限 256 ⇒ 命中的是孫行程、整棵都收掉
G8 runner._run_one 端到端:測試配置 400 MB、AIB_TEST_MEM_CAP_MB=256 ⇒ rc 137、輸出帶 memory_cap;上限關掉 ⇒ 照常綠
刀(複本 + run_guarded):K1 TreeCap 上限判斷拿掉 ⇒ G7 紅;K2 連兩拍確認拿掉 ⇒ G2 紅;
K3 開發分級拿掉 ⇒ G3 紅;K4 開始時間不比 ⇒ G4 紅
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


class GuardLogic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.killed = []

    def guard(self, rows, args=None, **kw):
        info = table(rows)
        g = MG.Guard(env={}, dry_run=kw.pop("dry_run", False), limit=kw.pop("limit", 5000), pids=list(rows),
                     info=info, kill=lambda p, s: self.killed.append((p, s)), sleep=lambda s: None,
                     notify_fn=lambda t: None, args_fn=lambda p: (args or {}).get(p, ["python3", "app.py"]),
                     root=self.tmp, uid=UID, dev_limit=kw.pop("dev_limit", 2000))
        g.protect = kw.pop("protect", set())
        return g

    def test_g2_confirm_and_scope(self):
        rows = {100: (UID, 1, "Python", "1.0", 6000), 101: (UID, 1, "claude", "1.0", 9000),
                102: (UID + 1, 1, "python3", "1.0", 9000), 103: (UID, 1, "node", "1.0", 6000),
                104: (UID, 1, "Python", "1.0", 6000)}
        # kill 之後行程「還在」(假表不變)⇒ kill_one 一路走到 KILL;我們只看收了誰
        g = self.guard(rows, protect={104})
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
        args = {200: ["python3", "/x/scratchpad/revA/sparse_repro.py"], 201: ["python3", "/opt/app/server.py"]}
        g = self.guard(rows, args=args)
        g.tick()
        acted = g.tick()
        self.assertEqual([r["pid"] for r in acted], [200])
        self.assertEqual(acted[0]["tier"], "dev")
        self.assertEqual(acted[0]["limit_mb"], 2000)
        self.assertTrue(MG.is_dev(["python3", "-c", "x"]))
        self.assertFalse(MG.is_dev(["python3", "/opt/app/server.py"]))

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
        try:
            child = int(p.stdout.readline().split()[1])
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
            p.stdout.close()
            for pid in (p.pid,):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass


class RunnerCap(unittest.TestCase):
    def test_g8_run_one_memory_cap(self):
        import runner  # noqa: E402
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(repo), True)
        (repo / "tests").mkdir()
        (repo / "tests" / "test-big.py").write_text(
            "import time\nblob = b'x' * (400 * 1024 * 1024)\ntime.sleep(30)\nprint('done')\n")
        (repo / "tests" / "test-small.py").write_text("print('ok')\n")
        env = dict(os.environ)
        with mock.patch.dict(os.environ, {"AIB_TEST_MEM_CAP_MB": "256"}):
            r = runner._run_one(repo, "tests/test-big.py", env, timeout=60)
            ok = runner._run_one(repo, "tests/test-small.py", env, timeout=60)
        self.assertEqual(r["rc"], 137)
        self.assertIn("memory_cap", r["output"])
        self.assertLess(r["elapsed_s"], 25)
        self.assertEqual(ok["rc"], 0)
        with mock.patch.dict(os.environ, {"AIB_TEST_MEM_CAP_MB": "0"}):
            off = runner._run_one(repo, "tests/test-small.py", env, timeout=60)
        self.assertEqual(off["rc"], 0)


KNIVES = [
    ("K1", "        if t[big][3] < self.cap:", "        if True:", "RealTree.test_g7_treecap_real"),
    ("K2", "            if self.over[key] < CONFIRM_TICKS:", "            if False:",
     "GuardLogic.test_g2_spike_then_drop_not_killed"),
    ("K3", "            lim = self.dev_limit if is_dev(self.args[key]) else self.limit", "            lim = self.limit",
     "GuardLogic.test_g3_dev_tier"),
    ("K4", "        return bool(i) and i[0] == uid and i[3] == start", "        return bool(i)",
     "GuardLogic.test_g4_pid_reuse"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, "src/cockpit/memguard.py", needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=120)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s%s" % (tag, r.stdout, r.stderr))


if __name__ == "__main__":
    unittest.main()
