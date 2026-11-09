#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# aib: ci-parallel-ok  (只看自己開的子行程;字串裡有 pgrep 是 C6 的治具)
"""CI Python 全套分份並行(S4,043dd 11-09;計畫 PLAN-release-tooling 9.2 第 1 項)。
每格都在暫存假倉跑 tools/verify/ci_shard.py(子行程):假的 aib/tests/test-*.py 與 tests/test_*.py,不碰真倉測試。

C1 全綠:兩個根的名字集合逐名相等、每支剛好一次、rc 0。
C2 一支 rc 1 ⇒ 整批 rc 1、那支記紅,其他照樣跑完記綠。
C3 一支卡住 ⇒ --timeout 到就整組殺、記 rc 124、其他照跑。
C4 verify:重複 / 漏 / 同數不同名 / 多出來的根 / 中斷 都列問題(不是只比總數)。
C5 跑到一半收到 SIGTERM ⇒ 停排新的、殺跑中的、rc 1、印 interrupted;沒跑到的列 missing。
C6 串行組:內容有 pgrep / 檔頭 `# aib: ci-serial` ⇒ 串行;`# aib: ci-parallel-ok` ⇒ 並行;串行組在並行組全結束後才開始。
C7 真的並行:4 支各睡 1.5 秒、--jobs 4 ⇒ 牆鐘 < 4 秒。
C8 每支自己的 HOME(不是 runner 的 HOME,彼此不同)。
C9 ci.yml 的 smoke 格改呼叫 ci_shard.py、舊的逐支迴圈不在了。
刀(複本 + run_guarded):K1 漏測(收集少一支)⇒ C1 紅;K2 重複測 ⇒ C1 紅;K3 子程序非零記成綠 ⇒ C2 紅;
K4 收到中斷不停 ⇒ C5 紅;K5 只比總數(同數不同名放過)⇒ C4 紅。刀一律斷言「FAIL: <目標>」且沒有 ERROR。
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parent
SHARD = ROOT / "tools" / "verify" / "ci_shard.py"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "verify"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

import ci_shard as CS  # noqa: E402

OK = "import sys\nsys.exit(0)\n"
BAD = "import sys\nprint('boom')\nsys.exit(1)\n"
ROOT_OK = textwrap.dedent("""
    import unittest
    class T(unittest.TestCase):
        def test_ok(self):
            self.assertTrue(True)
""")


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="aib-ci-shard-t-")).resolve()
        self.addCleanup(shutil.rmtree, str(self.d), True)
        self.repo = self.d / "repo"
        (self.repo / "aib" / "tests").mkdir(parents=True)
        (self.repo / "tests").mkdir()

    def put(self, rel, text):
        p = self.repo / rel
        p.write_text(text, encoding="utf-8")
        return p

    def run_shard(self, *extra, timeout=120):
        out = self.d / "res.json"
        r = subprocess.run([sys.executable, "-B", str(SHARD), "--repo", str(self.repo), "--results", str(out)] + list(extra),
                           capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        res = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
        return r, res


class C(Base):
    def test_c1_all_green_names_equal(self):
        for n in ("a", "b", "c"):
            self.put("aib/tests/test-%s.py" % n, OK)
        self.put("tests/test_root.py", ROOT_OK)
        r, res = self.run_shard("--jobs", "2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        names = [x["name"] for x in res["results"]]
        self.assertEqual(sorted(names), ["aib/tests/test-a.py", "aib/tests/test-b.py", "aib/tests/test-c.py",
                                         "tests/test_root.py"])
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(res["ok"], res["problems"])
        self.assertIn("aib/tests 跑到 3 / 應有 3", r.stdout)
        self.assertIn("tests 跑到 1 / 應有 1", r.stdout)

    def test_c2_red_is_red(self):
        self.put("aib/tests/test-a.py", OK)
        self.put("aib/tests/test-b.py", BAD)
        r, res = self.run_shard("--jobs", "2")
        self.assertEqual(r.returncode, 1, r.stdout)
        rc = {x["name"]: x["rc"] for x in res["results"]}
        self.assertEqual(rc, {"aib/tests/test-a.py": 0, "aib/tests/test-b.py": 1})
        self.assertIn("::error::aib/tests/test-b.py failed or hung", r.stdout)
        self.assertIn("boom", r.stdout)

    def test_c3_timeout_kills_and_records(self):
        self.put("aib/tests/test-a.py", OK)
        self.put("aib/tests/test-hang.py", "import time\ntime.sleep(60)\n")
        t0 = time.monotonic()
        r, res = self.run_shard("--jobs", "2", "--timeout", "2")
        self.assertLess(time.monotonic() - t0, 40)
        rc = {x["name"]: (x["rc"], x["timed_out"]) for x in res["results"]}
        self.assertEqual(rc["aib/tests/test-hang.py"], (124, True))
        self.assertEqual(rc["aib/tests/test-a.py"], (0, False))
        self.assertEqual(r.returncode, 1)

    def test_c4_verify_names_not_counts(self):
        exp = {"aib/tests": ["aib/tests/test-a.py", "aib/tests/test-b.py"], "tests": []}
        row = lambda n, root="aib/tests", rc=0: {"name": n, "root": root, "rc": rc}  # noqa: E731
        ok, p = CS.verify(exp, [row("aib/tests/test-a.py"), row("aib/tests/test-b.py")])
        self.assertTrue(ok, p)
        ok, p = CS.verify(exp, [row("aib/tests/test-a.py"), row("aib/tests/test-a.py"), row("aib/tests/test-b.py")])
        self.assertFalse(ok)
        self.assertTrue(any(x.startswith("duplicate") for x in p), p)
        ok, p = CS.verify(exp, [row("aib/tests/test-a.py")])
        self.assertTrue(any(x.startswith("missing") for x in p), p)
        ok, p = CS.verify(exp, [row("aib/tests/test-a.py"), row("aib/tests/test-z.py")])     # 同數不同名
        self.assertFalse(ok)
        self.assertTrue(any(x.startswith("missing") and "test-b" in x for x in p), p)
        self.assertTrue(any(x.startswith("unexpected") and "test-z" in x for x in p), p)
        ok, p = CS.verify(exp, [row("aib/tests/test-a.py"), row("aib/tests/test-b.py"), row("x/test-q.py", "x")])
        self.assertTrue(any("unexpected root" in x for x in p), p)
        ok, p = CS.verify(exp, [row("aib/tests/test-a.py"), row("aib/tests/test-b.py")], interrupted=True)
        self.assertFalse(ok)
        ok, p = CS.verify(exp, [row("aib/tests/test-a.py"), row("aib/tests/test-b.py", rc=None)])
        self.assertFalse(ok)

    def test_c5_sigterm_stops_and_fails(self):
        started = self.d / "started"
        started.mkdir()
        for i in range(6):
            self.put("aib/tests/test-s%d.py" % i,
                     "import os, time\nopen(os.path.join(%r, '%d'), 'w').write(str(os.getpid()))\ntime.sleep(30)\n"
                     % (str(started), i))
        self.addCleanup(self._kill_started, started)   # 刀 K4 那條路 ci_shard 被硬殺,它開的子行程各自成組,要自己收
        out = self.d / "res.json"
        p = subprocess.Popen([sys.executable, "-B", str(SHARD), "--repo", str(self.repo), "--jobs", "2",
                              "--results", str(out)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             stdin=subprocess.DEVNULL)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        end = time.time() + 30
        while len(os.listdir(started)) < 2 and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(len(os.listdir(started)), 2)
        p.send_signal(signal.SIGTERM)
        try:
            so, _ = p.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill()
            so, _ = p.communicate()
            self.fail("收到 SIGTERM 20 秒還沒停\n" + so[-2000:])
        self.assertEqual(p.returncode, 1, so)
        self.assertIn("interrupted", so)
        self.assertIn("missing", so)
        self.assertEqual(len(os.listdir(started)), 2, "收到中斷還在排新的")

    @staticmethod
    def _kill_started(started):
        for f in started.iterdir():
            try:
                pid = int(f.read_text() or 0)
                if pid > 1:
                    os.killpg(pid, signal.SIGKILL)
            except (OSError, ValueError):
                pass

    def test_c6_serial_group(self):
        self.put("aib/tests/test-p1.py", "import time\ntime.sleep(1)\n")
        self.put("aib/tests/test-p2.py", "import time\ntime.sleep(1)\n")
        self.put("aib/tests/test-s1.py", "# uses pgrep\nimport sys\nsys.exit(0)\n")
        self.put("aib/tests/test-s2.py", "# aib: ci-serial\nimport sys\nsys.exit(0)\n")
        self.put("aib/tests/test-ok.py", "# aib: ci-parallel-ok\n# mentions pgrep in a string only\nimport sys\nsys.exit(0)\n")
        r = subprocess.run([sys.executable, "-B", str(SHARD), "--repo", str(self.repo), "--list"],
                           capture_output=True, text=True, timeout=60)
        kinds = dict(reversed(ln.split(" ", 1)) for ln in r.stdout.split("\n") if ln)
        self.assertEqual(kinds["aib/tests/test-s1.py"], "serial")
        self.assertEqual(kinds["aib/tests/test-s2.py"], "serial")
        self.assertEqual(kinds["aib/tests/test-ok.py"], "parallel")
        self.assertEqual(kinds["aib/tests/test-p1.py"], "parallel")
        r, res = self.run_shard("--jobs", "3")
        self.assertEqual(r.returncode, 0, r.stdout)
        rows = res["results"]
        par_end = max(x["start_s"] + x["elapsed_s"] for x in rows if not x["serial"])
        ser_start = min(x["start_s"] for x in rows if x["serial"])
        self.assertGreaterEqual(ser_start + 0.05, par_end, rows)

    def test_c7_really_parallel(self):
        for i in range(4):
            self.put("aib/tests/test-w%d.py" % i, "import time\ntime.sleep(1.5)\n")
        r, res = self.run_shard("--jobs", "4")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertLess(res["wall_s"], 4.0, res)

    def test_c8_own_home(self):
        homes = self.d / "homes"
        homes.mkdir()
        for n in ("a", "b"):
            self.put("aib/tests/test-%s.py" % n,
                     "import os\nopen(os.path.join(%r, %r), 'w').write(os.environ['HOME'])\n" % (str(homes), n))
        r, _ = self.run_shard("--jobs", "2")
        self.assertEqual(r.returncode, 0, r.stdout)
        a, b = ((homes / n).read_text() for n in ("a", "b"))
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, os.path.expanduser("~"))

    def test_c9_ci_yml_wired(self):
        wf = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        smoke = wf[wf.index("\n  smoke:"):wf.index("\n  fresh-machine:")]
        self.assertIn("python aib/tools/verify/ci_shard.py --repo .", smoke)
        self.assertNotIn("for f in aib/tests/test-*.py", smoke)
        self.assertNotIn("unittest discover -s tests", smoke)


KNIVES = [
    ("K1 漏測", "tools/verify/ci_shard.py",
     '    for p in sorted((repo / "aib" / "tests").glob("test-*.py")):\n',
     '    for p in sorted((repo / "aib" / "tests").glob("test-*.py"))[:-1]:\n',
     "C.test_c1_all_green_names_equal"),
    ("K2 重複測", "tools/verify/ci_shard.py",
     "    return par, ser\n", "    return par + par[:1], ser\n",
     "C.test_c1_all_green_names_equal"),
    ("K3 非零記成綠", "tools/verify/ci_shard.py",
     "rc = p.wait(timeout=self.timeout_s)", "rc = p.wait(timeout=self.timeout_s) and 0",
     "C.test_c2_red_is_red"),
    ("K4 中斷不停", "tools/verify/ci_shard.py",
     "        interrupted.append(signum)\n        run.stop()\n", "        pass\n",
     "C.test_c5_sigterm_stops_and_fails"),
    ("K5 只比總數", "tools/verify/ci_shard.py",
     "        miss = sorted(set(names) - set(got))\n        extra = sorted(set(got) - set(names))\n",
     "        miss = extra = [] if len(got) == len(names) else [\"count\"]\n",
     "C.test_c4_verify_names_not_counts"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=60, timeout=240)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out[-3000:]))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out[-3000:]))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out[-3000:]))


if __name__ == "__main__":
    unittest.main()
