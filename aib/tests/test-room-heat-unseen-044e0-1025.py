#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat 看不到的那一半(10-25 @044e0,RULING-room-heat-local-opt 第 1 張):全機 CPU、unseen、找熱源 churn。
全在暫存狀態根 + 假 tick / 假行程表 / 假時鐘跑;U9 是唯一碰真行程表的(只看自己起的 sleep,不收任何行程)。
U1 cpu_total:沒上一次 ⇒ 當場量 0.5 秒;有上一次(1–120 秒內)⇒ 用 tick 差;tick 量不到 ⇒ unmeasured(不是 0);
   計數器倒退(重開機)⇒ 重量;tick 檔 0600、落在 EZ2_AIB_STATE_ROOT 底下
U2 unseen = 在忙的核 − ps 看得到的合計(不小於 0);ps 量不到 / CPU 量不到 ⇒ unmeasured
U3 快照多 cpu_total / unseen_cpu,歷史多記 cpu_busy / unseen_cpu
U4 歸屬:新行程往上找「取樣開始就在」的祖先 ⇒ 組別 / 席碼;程式名取最上層新行程;沒有祖先 ⇒ other_app
U5 節流:60 秒內第二次 ⇒ 上次結果 + throttled(不再取樣);過 60 秒再取樣;取樣壞了 ⇒ error 且不佔節流;
   秒數夾在 1–30;不支援的平台 ⇒ unsupported_platform(不叫任何指令)
U6 另一支正在跑(鎖被拿著)⇒ busy
U7 隱私:程式名只有檔名(腳本取檔名、sh -c 只給 sh);輸出不含指令列 / 路徑 / token
U8 上次結果檔被改 ⇒ 只放回白名單欄位、名字再洗一次、組別不在表上的丟掉
U9 真行程表:取樣期間自己起 12 支 sleep ⇒ 看得到 ≥ 8 支、程式名是 sleep
U10 CLI:`churn --json` 走 room_heat_unseen.churn、秒數照傳;文字模式 throttled 有提示、非 ok 回 1
刀(複本 + run_guarded):K1 unseen 不扣 ps ⇒ U2 紅;K2 節流拿掉 ⇒ U5 紅;K3 程式名帶整串引數 ⇒ U7 紅;
K4 上次結果不洗名字 ⇒ U8 紅。刀一律斷言「FAIL: <目標>」且沒有 ERROR。
"""
import fcntl
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
os.environ.setdefault("EZ2_AIB_LOCALE", "zh-TW")
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

import room_heat as RH  # noqa: E402
import room_heat_unseen as RU  # noqa: E402

ME = os.getuid()


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="aib-heat-unseen-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.env = {"EZ2_AIB_STATE_ROOT": self.d}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)

    def room(self, name):
        return Path(RH.state_dir()) / name


class Ticks:
    def __init__(self, seq):
        self.seq = list(seq)

    def __call__(self):
        return self.seq.pop(0) if self.seq else None


class CpuTotal(Base):
    def test_u1_cpu_total(self):
        slept = []
        r = RU.cpu_total(ticks_fn=Ticks([(100, 1000), (150, 1100)]), now=1000.0, sleep=slept.append, ncpu=10)
        self.assertEqual(slept, [0.5])
        self.assertEqual((r["state"], r["pct"], r["busy_cores"], r["window_s"], r["cores"]), ("ok", 50.0, 5.0, 0.5, 10))
        f = self.room(RU.TICKS_FILE)
        self.assertTrue(str(f).startswith(self.d))
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        slept.clear()
        r = RU.cpu_total(ticks_fn=Ticks([(170, 1200)]), now=1005.5, sleep=slept.append, ncpu=10)
        self.assertEqual(slept, [], "有 5 秒前的 tick 卻又當場量")
        self.assertEqual((r["pct"], r["busy_cores"], r["window_s"]), (20.0, 2.0, 5.0))
        r = RU.cpu_total(ticks_fn=Ticks([(5, 50), (6, 60)]), now=1010.0, sleep=slept.append, ncpu=10)
        self.assertEqual(slept, [0.5], "計數器倒退(重開機)卻拿舊 tick 算")
        self.assertEqual(r["pct"], 10.0)
        r = RU.cpu_total(ticks_fn=Ticks([]), now=1020.0, sleep=slept.append, ncpu=10)
        self.assertEqual(r, {"state": "unmeasured", "detail": "cpu_ticks_unavailable"})

    def test_u1b_stale_ticks_resampled(self):
        RU.cpu_total(ticks_fn=Ticks([(0, 0), (10, 100)]), now=0.0, sleep=lambda s: None, ncpu=4)
        slept = []
        RU.cpu_total(ticks_fn=Ticks([(20, 200), (30, 300)]), now=500.0, sleep=slept.append, ncpu=4)
        self.assertEqual(slept, [0.5], "上一次 tick 已經 8 分鐘前卻照用")

    def test_u2_unseen(self):
        ok = {"state": "ok", "busy_cores": 6.95, "cores": 10, "pct": 69.5}
        rows = [(1, 0, ME, 150.0, 1, "a", "a"), (2, 1, ME, 145.0, 1, "b", "b")]
        self.assertEqual(RU.unseen(ok, rows), {"state": "ok", "cores": 4.0, "seen_cores": 2.95})
        self.assertEqual(RU.unseen(dict(ok, busy_cores=1.0), rows)["cores"], 0.0)
        self.assertEqual(RU.unseen(ok, None), {"state": "unmeasured", "detail": "ps_failed"})
        self.assertEqual(RU.unseen({"state": "unmeasured"}, rows)["state"], "unmeasured")


ROWS = [(100, 1, ME, 50.0, 2048, "claude", "claude --resume"),
        (200, 100, ME, 20.0, 4096, "python3", "python3 x.py")]
PS = "".join("%6d %5d %5d %5.1f %6d %s\n" % (r[0], r[1], r[2], r[3], r[4], r[6]) for r in ROWS)
UC = "".join("%6d %-16s\n" % (r[0], r[5]) for r in ROWS)


class Snapshot(Base):
    def test_u3_snapshot_and_history(self):
        def sampler():
            return [{"key": "load1", "value": 3.0, "text": "3.0", "state": "ok"}], 0.01
        cpu = lambda now=None, environ=None: {"state": "ok", "cores": 10, "pct": 30.0, "busy_cores": 3.0, "window_s": 5.0}
        s = RH.snapshot(sampler=sampler, run=lambda c: UC if any("ucomm" in x for x in c) else PS,
                        owner_of=lambda pid: ("abcde", "%1"), now=time.time(), scan_fn=lambda **k: ([], []), cpu_fn=cpu)
        self.assertEqual(s["cpu_total"]["busy_cores"], 3.0)
        self.assertEqual(s["unseen_cpu"], {"state": "ok", "cores": 2.3, "seen_cores": 0.7})
        pts = RH.read_history()
        self.assertEqual((pts[-1]["cpu_busy"], pts[-1]["unseen_cpu"]), (3.0, 2.3))
        bad = lambda now=None, environ=None: (_ for _ in ()).throw(OSError("x"))
        s = RH.snapshot(sampler=sampler, run=lambda c: UC if any("ucomm" in x for x in c) else PS,
                        owner_of=lambda pid: ("abcde", "%1"), now=time.time() + 20, scan_fn=lambda **k: ([], []),
                        cpu_fn=bad)
        self.assertEqual(s["cpu_total"]["state"], "unmeasured")
        self.assertEqual(s["unseen_cpu"]["state"], "unmeasured")
        out = io.StringIO()
        RH._print_snapshot(s, out)
        self.assertIn("量不到", out.getvalue())


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def fake_fns(births):
    """base:100(claude)、1;取樣第 1 拍起冒出 births 裡的行程(pid, ppid, 名字, cpu 秒)。"""
    table = {100: (1, "claude", 1.0), 1: (0, "launchd", 1.0)}
    state = {"n": 0}

    def pids():
        state["n"] += 1
        if state["n"] == 2:
            for pid, ppid, name, cpu in births:
                table[pid] = (ppid, name, cpu)
        return list(table)
    return pids, (lambda p: table.get(p)), (lambda p: ["bash", "/Users/u/secret/pane-right-tabs.sh", "token=abc"]
                                              if table.get(p, (0, ""))[1] == "bash" else [])


BIRTHS = [(500, 100, "bash", 0.4), (501, 500, "tmux", 0.2), (502, 500, "tmux", 0.2), (600, 1, "mdworker", 0.1)]


class Churn(Base):
    def run_churn(self, now, births=BIRTHS, seconds=2, **kw):
        c = Clock()
        return RU.churn(seconds=seconds, now=now, platform="linux", fns=fake_fns(births), rows=[],
                        cls={100: ("seat", "abcde")}, clock=c, sleep=c.sleep, **kw)

    def test_u4_attribute(self):
        r = self.run_churn(1000.0)
        self.assertEqual(r["state"], "ok", r)
        self.assertEqual((r["births"], r["births_per_s"], r["cpu_cores_lower_bound"]), (4, 2.0, 0.45))
        top = {(x["group"], x["owner"], x["program"]): x for x in r["top"]}
        seat = top[("seat", "abcde", "pane-right-tabs.sh")]
        self.assertEqual((seat["count"], seat["births_per_s"], seat["cpu_cores_lower_bound"]), (3, 1.5, 0.4))
        self.assertIn(("other_app", "", "mdworker"), top)

    def test_u5_throttle(self):
        r1 = self.run_churn(1000.0)
        self.assertFalse(r1["throttled"])
        r2 = self.run_churn(1030.0, births=[])
        self.assertTrue(r2["throttled"], "60 秒內第二次沒節流")
        self.assertEqual((r2["detail"], r2["births"], r2["top"]), ("throttled", 4, r1["top"]))
        self.assertEqual(r2["retry_after_s"], 31)
        r3 = self.run_churn(1061.0, births=[])
        self.assertEqual((r3["throttled"], r3["births"]), (False, 0), "過 60 秒沒重新取樣")
        self.assertEqual(self.run_churn(2000.0, seconds=999)["seconds"], 30)
        self.assertEqual(self.run_churn(3000.0, seconds=0)["seconds"], 1)

    def test_u5b_failure_and_platform(self):
        c = Clock()
        boom = (lambda: (_ for _ in ()).throw(OSError("x")), lambda p: None, lambda p: [])
        r = RU.churn(seconds=2, now=1000.0, platform="linux", fns=boom, rows=[], cls={}, clock=c, sleep=c.sleep)
        self.assertEqual((r["state"], r["detail"]), ("error", "churn_sample_failed"))
        self.assertEqual(self.run_churn(1001.0)["state"], "ok", "取樣失敗卻佔住節流")
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("不准叫指令")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("不准叫指令")):
            r = RU.churn(seconds=2, now=1000.0, platform="win32")
        self.assertEqual(r, {"schema": "aib-heat-churn/v1", "state": "unsupported", "detail": "unsupported_platform"})

    def test_u6_busy(self):
        os.makedirs(RH.state_dir(), exist_ok=True)
        fd = os.open(str(self.room(RU.CHURN_LOCK)), os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        r = self.run_churn(1000.0)
        self.assertEqual((r["state"], r["detail"]), ("busy", "churn_running"))

    def test_u7_privacy(self):
        self.assertEqual(RU.program_of("bash", ["bash", "/Users/u/x/pane-right-tabs.sh", "a"]), "pane-right-tabs.sh")
        self.assertEqual(RU.program_of("sh", ["sh", "-c", "curl -H token=abc /Users/u"]), "sh")
        self.assertEqual(RU.program_of("python3", ["python3", "-B", "/Users/u/aib/src/cockpit/seat_now.py", "--x"]),
                         "seat_now.py")
        self.assertEqual(RU.program_of("node", ["node", "-e", "secret()"]), "node")
        self.assertEqual(RU.program_of("/usr/bin/git", None), "git")
        blob = json.dumps(self.run_churn(1000.0), ensure_ascii=False)
        for leak in ("/Users", "token", "secret", "abc\""):
            self.assertNotIn(leak, blob)

    def test_u8_cached_cleaned(self):
        os.makedirs(RH.state_dir(), exist_ok=True)
        evil = {"started_at": 990.0, "result": {"state": "ok", "t": 990.0, "seconds": 10, "births": 3,
                "births_per_s": 0.3, "cpu_cores_lower_bound": "x", "extra": "/Users/u/secret",
                "top": [{"group": "seat", "owner": "ab/../\x1b[31m", "program": "/Users/u/secret/x.sh\x1b[2J",
                         "count": 3, "births_per_s": 0.3, "cpu_cores_lower_bound": float("nan"), "cmd": "token=abc"},
                        {"group": "root_shell", "owner": "", "program": "p", "count": 1}]}}
        self.room(RU.CHURN_FILE).write_text(json.dumps(evil))
        r = self.run_churn(1000.0)
        self.assertTrue(r["throttled"])
        self.assertEqual(len(r["top"]), 1, "組別不在表上的沒丟")
        x = r["top"][0]
        self.assertNotIn("/", x["program"] + x["owner"])
        self.assertNotIn("\x1b", x["program"] + x["owner"])
        self.assertEqual(x["cpu_cores_lower_bound"], 0)
        self.assertIsNone(r["cpu_cores_lower_bound"])
        self.assertNotIn("extra", r)
        self.assertNotIn("cmd", x)


class Real(Base):
    @unittest.skipUnless(sys.platform == "darwin" or sys.platform.startswith("linux"), "只在 macOS / Linux")
    def test_u9_real_births(self):
        kids = []

        def spawn():
            time.sleep(0.3)
            for _ in range(12):
                kids.append(subprocess.Popen(["sleep", "0.4"]))
                time.sleep(0.05)
        th = threading.Thread(target=spawn)
        th.start()
        try:
            r = RU.churn(seconds=2, now=time.time(), rows=[], cls={})
        finally:
            th.join()
            for k in kids:
                k.wait(timeout=10)
        self.assertEqual(r["state"], "ok", r)
        n = sum(x["count"] for x in r["top"] if x["program"] == "sleep")
        self.assertGreaterEqual(n, 8, r["top"])


class Cli(Base):
    def test_u10_cli(self):
        got = {}

        def fake(seconds=10, **k):
            got["s"] = seconds
            return {"schema": "aib-heat-churn/v1", "state": "ok", "seconds": seconds, "births": 2, "births_per_s": 0.4,
                    "cpu_cores_lower_bound": 0.1, "throttled": True, "retry_after_s": 12,
                    "top": [{"group": "aib_core", "owner": "", "program": "pane-right-tabs.sh", "count": 2,
                             "births_per_s": 0.4, "cpu_cores_lower_bound": 0.1}]}
        with mock.patch.object(RU, "churn", fake):
            out = io.StringIO()
            self.assertEqual(RH.main(["churn", "--json", "--seconds", "5"], out=out), 0)
            self.assertEqual((got["s"], json.loads(out.getvalue())["births"]), (5, 2))
            out = io.StringIO()
            RH.main(["churn"], out=out)
            self.assertIn("12 秒後", out.getvalue())
            self.assertIn("pane-right-tabs.sh", out.getvalue())
        with mock.patch.object(RU, "churn", lambda **k: {"state": "busy", "detail": "churn_running"}):
            out = io.StringIO()
            self.assertEqual(RH.main(["churn"], out=out), 1)
            self.assertIn("正在跑", out.getvalue())


KNIVES = [
    ("K1", "src/cockpit/room_heat_unseen.py", "    seen = sum(r[3] for r in rows) / 100.0\n", "    seen = 0.0\n",
     "CpuTotal.test_u2_unseen"),
    ("K2", "src/cockpit/room_heat_unseen.py", "CHURN_THROTTLE_S = 60\n", "CHURN_THROTTLE_S = 0\n",
     "Churn.test_u5_throttle"),
    ("K3", "src/cockpit/room_heat_unseen.py", "                    return os.path.basename(a)\n",
     "                    return \" \".join(args)\n", "Churn.test_u7_privacy"),
    ("K4", "src/cockpit/room_heat_unseen.py", "                    \"program\": room_heat.safe_name(x.get(\"program\"), 40),\n",
     "                    \"program\": x.get(\"program\"),\n", "Churn.test_u8_cached_cleaned"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=120)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
