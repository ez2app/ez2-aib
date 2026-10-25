#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat 看不到的那一半(10-25 @044e0,RULING-room-heat-local-opt 第 1 張):全機 CPU、unseen、找熱源 churn。
全在暫存狀態根 + 假 tick / 假行程表 / 假時鐘跑;U9 是唯一碰真行程表的(只看自己起的 sleep,不收任何行程)。
U1 cpu_total:沒上一次 ⇒ 當場量 0.5 秒;有上一次(1–120 秒內)⇒ 用 tick 差;tick 量不到 ⇒ unmeasured(不是 0);
   計數器倒退(重開機)⇒ 重量;tick 檔 0600、落在 EZ2_AIB_STATE_ROOT 底下
U1c 同一時間窗:每個行程的累計 CPU 跟 tick 一起存、兩次相減(審查 M1);上次漏列的「新」行程累計 > 時間窗 2 倍 ⇒ 不算;
    pid 重用照新的算;Linux steal 另列;ps time 兩種格式都讀得懂
U1f 三艙共用 tick 檔:別艙不到 1 秒前剛量過 ⇒ 直接回那一份、不睡(@ee4f8 L4);那一份被改過 ⇒ 不用
U1g Linux 行程時間讀 /proc/<pid>/stat(1/100 秒),不用只到整秒的 ps(第 2 輪 B-M);40 支 × 0.3 核 1 秒窗 ⇒ 12 核
U1d 真 tick 冒煙:讀兩次單調遞增、busy ≤ total
U2 unseen = 在忙的核 − 同窗看得到的(不小於 0);看得到的多一截 ⇒ unmeasured window_mismatch(不回假的 0);量不到 ⇒ unmeasured
U3 快照多 cpu_total / unseen_cpu,歷史多記 cpu_busy / unseen_cpu
U4 歸屬:新行程往上找「取樣開始就在」的祖先 ⇒ 組別 / 席碼;程式名取最上層新行程;沒有祖先 ⇒ other_app
U4b fork 完、exec 前先被看到(名字是父行程的)⇒ exec 後重記程式名、不重複算出生
U5 節流:60 秒內第二次 ⇒ 上次結果 + throttled(不再取樣);過 60 秒再取樣;取樣壞了 ⇒ error 且不佔節流;
   秒數夾在 1–30;不支援的平台 ⇒ unsupported_platform(不叫任何指令)
U6 另一支正在跑(鎖被拿著)⇒ busy
U7 隱私:程式名只有檔名(腳本取檔名、sh -c 只給 sh);輸出不含指令列 / 路徑 / token
U8 上次結果檔被改 ⇒ 只放回白名單欄位、名字再洗一次、組別不在表上的丟掉
U7c 審查 H1:shell 合寫旗標(-ec / -lc / -xc / -euc)、-s、-o 帶值、--、不認得的長旗標 ⇒ 只回 shell 名;
    腳本名有空白 / 不像腳本檔名 ⇒ 不收;-m 模組名要乾淨;churn 輸出與快取都不出現 inline 指令裡的字
U7e @ee4f8 E3 裁定 a–f:bash -lc / zsh -ec / sh -xc / ruby -ne / perl -lne / php -B / bash -s ARG / bash -i ⇒ 只回直譯器名;
    副檔名只收 .sh/.bash/.zsh/.py/.js、檔名 > 40 字不收;讀完 argv 那支 pid 被重用 ⇒ 不收(L3);主人欄現跑也洗(L2)
U7d 上一次被砍留下半份紀錄 ⇒ 照常重跑(審查 L1);room 夾是 symlink ⇒ error、不寫(審查 L6)
U7b E3:別的帳號(root)起的新行程歸成「system / ?」、不出名字、不讀它的 argv;throttled 帶 cached_at(E4)
U9 真行程表:取樣期間自己起 12 支 sleep ⇒ 看得到 ≥ 8 支、過半程式名是 sleep、父行程是自己(只數自己起的,不看排名)
U10 CLI:`churn --json` 走 room_heat_unseen.churn、秒數照傳;文字模式 throttled 有提示、非 ok 回 1
刀(複本 + run_guarded):K1 時間窗對不上也回數字 ⇒ U2 紅;K2 節流拿掉 ⇒ U5 紅;K3 只認單獨的 -c / -s ⇒ U7c 紅;
K5 上次漏列的長壽行程也算進 seen ⇒ U1c 紅;
K4 上次結果不洗名字 ⇒ U8 紅;K6 perl / ruby / php 的 inline 旗標不擋、K7 pid 重用不核 ⇒ U7e 紅;
K8 Linux 改回 ps(整秒)⇒ U1g 紅。刀一律斷言「FAIL: <目標>」且沒有 ERROR。
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


class Times:
    def __init__(self, seq):
        self.seq = list(seq)

    def __call__(self):
        return self.seq.pop(0) if self.seq else None


class CpuTotal(Base):
    def test_u1_cpu_total(self):
        slept = []
        nothing = Times([])
        r = RU.cpu_total(ticks_fn=Ticks([(100, 1000), (150, 1100)]), now=1000.0, sleep=slept.append, ncpu=10,
                         times_fn=nothing)
        self.assertEqual(slept, [1.0])
        self.assertEqual((r["state"], r["pct"], r["busy_cores"], r["window_s"], r["cores"]), ("ok", 50.0, 5.0, 1.0, 10))
        f = self.room(RU.TICKS_FILE)
        self.assertTrue(str(f).startswith(self.d))
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        slept.clear()
        r = RU.cpu_total(ticks_fn=Ticks([(170, 1200)]), now=1006.0, sleep=slept.append, ncpu=10, times_fn=nothing)
        self.assertEqual(slept, [], "有 5 秒前的 tick 卻又當場量")
        self.assertEqual((r["pct"], r["busy_cores"], r["window_s"]), (20.0, 2.0, 5.0))
        self.assertNotIn("seen_cores", r, "沒有行程時間卻給了 seen_cores")
        r = RU.cpu_total(ticks_fn=Ticks([(5, 50), (6, 60)]), now=1010.0, sleep=slept.append, ncpu=10, times_fn=nothing)
        self.assertEqual(slept, [1.0], "計數器倒退(重開機)卻拿舊 tick 算")
        self.assertEqual(r["pct"], 10.0)
        r = RU.cpu_total(ticks_fn=Ticks([]), now=1020.0, sleep=slept.append, ncpu=10, times_fn=nothing)
        self.assertEqual(r, {"state": "unmeasured", "detail": "cpu_ticks_unavailable"})

    def test_u1b_stale_ticks_resampled(self):
        RU.cpu_total(ticks_fn=Ticks([(0, 0), (10, 100)]), now=0.0, sleep=lambda s: None, ncpu=4, times_fn=Times([]))
        slept = []
        RU.cpu_total(ticks_fn=Ticks([(20, 200), (30, 300)]), now=500.0, sleep=slept.append, ncpu=4, times_fn=Times([]))
        self.assertEqual(slept, [1.0], "上一次 tick 已經 8 分鐘前卻照用")

    def test_u1c_same_window_seen_and_steal(self):
        # 第一次(當場量 1 秒):1 號行程用 0.5 秒 ⇒ 0.5 核;第二次(5 秒後):1 號再用 5 秒、3 號新開用 1 秒、
        # 9 號「新的」卻已累計 400 秒(上次 ps 漏列)⇒ 不算
        r = RU.cpu_total(ticks_fn=Ticks([(0, 0, 0), (40, 100, 5)]), now=0.0, sleep=lambda s: None, ncpu=10,
                         times_fn=Times([{1: 100.0, 2: 50.0}, {1: 100.5, 2: 50.0}]))
        self.assertEqual((r["seen_cores"], r["steal_pct"]), (0.5, 5.0))
        r = RU.cpu_total(ticks_fn=Ticks([(140, 200, 5)]), now=6.0, sleep=lambda s: None, ncpu=10,
                         times_fn=Times([{1: 105.5, 2: 50.0, 3: 1.0, 9: 400.0}]))
        self.assertEqual((r["busy_cores"], r["window_s"], r["seen_cores"], r["steal_pct"]), (10.0, 5.0, 1.2, 0.0))
        self.assertEqual(RU.seen_cores({1: 10.0}, {1: 2.0}, 5.0), 0.4, "pid 重用(數字變小)沒照新的算")
        self.assertEqual(RU._ps_seconds("83:01.80"), 4981.8)
        self.assertEqual(RU._ps_seconds("2-01:00:05"), 2 * 86400 + 3605)
        self.assertIsNone(RU._ps_seconds("x:y"))
        self.assertEqual(RU.proc_times(run=lambda c: "  1 83:01.80\n 22 0:00.10\n bad line here\n", platform="darwin"),
                         {1: 4981.8, 22: 0.1}, "macOS ps time 格式(Linux 改讀 /proc,見 U1g)")

    def test_u1d_real_ticks_smoke(self):
        if not (sys.platform == "darwin" or sys.platform.startswith("linux")):
            self.skipTest("只在 macOS / Linux")
        a = RU.read_ticks()
        time.sleep(1.2)                                   # macOS tick 約每秒更新
        b = RU.read_ticks()
        self.assertTrue(a and b, "量不到 tick")
        self.assertTrue(b[0] >= a[0] and b[1] > a[1], (a, b))
        self.assertTrue(a[0] <= a[1] and b[0] <= b[1], (a, b))

    def test_u1e_tick_boundary_waits_once_more(self):
        slept = []
        r = RU.cpu_total(ticks_fn=Ticks([(10, 100), (10, 100), (20, 200)]), now=0.0, sleep=slept.append, ncpu=2,
                         times_fn=Times([]))
        self.assertEqual((slept, r["state"], r["window_s"]), ([1.0, 0.5], "ok", 1.5))

    def test_u1f_fresh_result_shared(self):
        first = RU.cpu_total(ticks_fn=Ticks([(100, 1000), (150, 1100)]), now=1000.0, sleep=lambda s: None, ncpu=10,
                             times_fn=Times([]))
        slept = []
        r = RU.cpu_total(ticks_fn=Ticks([]), now=1001.4, sleep=slept.append, ncpu=10, times_fn=Times([]))
        self.assertEqual((slept, r), ([], first), "別艙 0.4 秒前剛量過,又當場睡 1 秒重量")
        f = self.room(RU.TICKS_FILE)
        d = json.loads(f.read_text())
        d["last"] = {"state": "ok", "cores": 10, "pct": "x; rm", "busy_cores": 5, "window_s": 1}
        f.write_text(json.dumps(d))
        r = RU.cpu_total(ticks_fn=Ticks([]), now=1001.5, sleep=slept.append, ncpu=10, times_fn=Times([]))
        self.assertEqual(r["state"], "unmeasured", "tick 檔裡被改過的上一份結果照用")

    def test_u1g_linux_proc_times_subsecond(self):
        root = Path(self.d) / "proc"
        clk = float(os.sysconf("SC_CLK_TCK") or 100)

        def write(cpu_by_pid):
            for pid, sec in cpu_by_pid.items():
                (root / str(pid)).mkdir(parents=True, exist_ok=True)
                ticks = int(round(sec * clk))
                rest = ["S", "1"] + ["0"] * 9 + [str(ticks), "0"] + ["0"] * 30
                (root / str(pid) / "stat").write_text("%d (worker (x)) %s\n" % (pid, " ".join(rest)))
        (root / "self").mkdir(parents=True)
        write({1000 + k: 10.0 for k in range(40)})
        called = []
        a = RU.proc_times(run=lambda cmd: called.append(cmd) or "", platform="linux", proc_root=str(root))
        write({1000 + k: 10.3 for k in range(40)})
        b = RU.proc_times(run=lambda cmd: called.append(cmd) or "", platform="linux", proc_root=str(root))
        self.assertEqual(called, [], "Linux 還在叫 ps(只到整秒)")
        self.assertAlmostEqual(RU.seen_cores(a, b, 1.0), 12.0, places=1)
        self.assertIsNone(RU.proc_times(platform="linux", proc_root=str(root / "nope")))

    def test_u2_unseen(self):
        ok = {"state": "ok", "busy_cores": 6.95, "cores": 10, "pct": 69.5, "seen_cores": 2.95}
        self.assertEqual(RU.unseen(ok), {"state": "ok", "cores": 4.0, "seen_cores": 2.95})
        self.assertEqual(RU.unseen(dict(ok, busy_cores=2.9))["cores"], 0.0, "差一點點(四捨五入)該夾 0")
        self.assertEqual(RU.unseen(dict(ok, busy_cores=1.0)),
                         {"state": "unmeasured", "detail": "window_mismatch", "seen_cores": 2.95},
                         "看得到的比在忙的多一截卻回 0(假數字)")
        no_seen = dict(ok)
        no_seen.pop("seen_cores")
        self.assertEqual(RU.unseen(no_seen), {"state": "unmeasured", "detail": "ps_failed"})
        self.assertEqual(RU.unseen({"state": "unmeasured"})["state"], "unmeasured")


ROWS = [(100, 1, ME, 50.0, 2048, "claude", "claude --resume"),
        (200, 100, ME, 20.0, 4096, "python3", "python3 x.py")]
PS = "".join("%6d %5d %5d %5.1f %6d %s\n" % (r[0], r[1], r[2], r[3], r[4], r[6]) for r in ROWS)
UC = "".join("%6d %-16s\n" % (r[0], r[5]) for r in ROWS)


class Snapshot(Base):
    def test_u3_snapshot_and_history(self):
        def sampler():
            return [{"key": "load1", "value": 3.0, "text": "3.0", "state": "ok"}], 0.01
        cpu = lambda now=None, environ=None: {"state": "ok", "cores": 10, "pct": 30.0, "busy_cores": 3.0, "window_s": 5.0,
                                              "seen_cores": 0.7}
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
            for b in births:
                table[b[0]] = (b[1], b[2], b[3]) + ((b[4],) if len(b) > 4 else ())
        return list(table)
    return pids, (lambda p: table.get(p)), (lambda p: ["bash", "/Users/u/secret/pane-right-tabs.sh", "token=abc"]
                                              if table.get(p, (0, ""))[1] == "bash" else [])


BIRTHS = [(500, 100, "bash", 0.4), (501, 500, "tmux", 0.2), (502, 500, "tmux", 0.2), (600, 1, "mdworker", 0.1)]
ROOT_BIRTH = [(700, 100, "secretd", 0.3, 0)]       # 別的帳號(root)起的


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
        self.assertEqual((r2["retry_after_s"], r2["cached_at"]), (31, 1000.0))
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

    def test_u4b_exec_renames(self):
        table = {100: (1, "claude", 1.0)}
        n = {"c": 0}

        def pids():
            n["c"] += 1
            if n["c"] == 2:
                table[800] = (100, "python3", 0.0)           # fork 完還沒 exec
            if n["c"] == 4:
                table[800] = (100, "bash", 0.2)              # exec 成腳本
            return list(table)
        c = Clock()
        base, known, born, cpu, prog = RU.sample(1, pids, lambda p: table.get(p),
                                                 lambda p: ["bash", "/x/pane-right-tabs.sh"], clock=c, sleep=c.sleep)
        self.assertEqual((born, prog[800], cpu[800]), ([800], "pane-right-tabs.sh", 0.2), "exec 後沒重記程式名")

    def test_u7b_other_uid_unknown(self):
        asked = []
        c = Clock()
        pids, cpu, args = fake_fns(BIRTHS + ROOT_BIRTH)
        r = RU.churn(seconds=2, now=1000.0, platform="linux", fns=(pids, cpu, lambda p: asked.append(p) or args(p)),
                     rows=[], cls={100: ("seat", "abcde")}, clock=c, sleep=c.sleep)
        top = {(x["group"], x["owner"], x["program"]): x for x in r["top"]}
        self.assertIn(("system", "", "?"), top, "E3:別的帳號的新行程沒歸成未知")
        self.assertNotIn("secretd", json.dumps(r))
        self.assertNotIn(700, asked, "E3:別的帳號的 argv 也讀了")

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

    def test_u7c_shell_flag_groups_h1(self):
        P = RU.program_of
        secret = "psql postgres://user:Passw0rd@db mydb"
        for argv in (["sh", "-ec", secret], ["bash", "-lc", secret], ["bash", "-xc", secret], ["sh", "-euc", secret],
                     ["sh", "-s", secret], ["zsh", "-o", "pipefail", "-c", secret], ["bash", "--", secret],
                     ["bash", "--command", secret], ["bash", "/x/my secret.sh"], ["bash", "/x/Passw0rd"]):
            self.assertEqual(P(argv[0], argv), argv[0], argv)
        for argv in (["sh", "-ec", "token_abc.sh"], ["bash", "-lc", "Passw0rd.py"]):   # 指令字串就算長得像腳本也不收
            self.assertEqual(P(argv[0], argv), argv[0], argv)
        self.assertEqual(P("bash", ["bash", "--rcfile", "/x/Passw0rd.sh", "-e", "/x/run.sh"]), "run.sh")
        self.assertEqual(P("bash", ["bash", "-O", "extglob", "/x/run.sh"]), "run.sh")
        self.assertEqual(P("python3", ["python3", "-m", "http.server"]), "http.server")
        self.assertEqual(P("python3", ["python3", "-m", "a;rm -rf"]), "python3")
        self.assertEqual(P("python3", ["python3", "/x/secret token.py"]), "python3")
        # churn 輸出與快取檔都不准出現
        c = Clock()
        table = {100: (1, "tmux", 1.0), 1: (0, "launchd", 1.0)}
        n = {"c": 0}

        def pids():
            n["c"] += 1
            if n["c"] == 2:
                table[900] = (100, "sh", 0.3)
            return list(table)
        r = RU.churn(seconds=2, now=1000.0, platform="linux", fns=(pids, lambda p: table.get(p),
                     lambda p: ["sh", "-ec", secret]), rows=[], cls={100: ("aib_core", "")}, clock=c, sleep=c.sleep)
        self.assertEqual([x["program"] for x in r["top"]], ["sh"])
        for blob in (json.dumps(r), self.room(RU.CHURN_FILE).read_text()):
            for leak in ("Passw0rd", "postgres", "psql"):
                self.assertNotIn(leak, blob)

    def test_u7e_e3_ruling(self):
        P = RU.program_of
        tok = "tok_9f2a1c.sh"
        for argv in (["bash", "-lc", tok], ["zsh", "-ec", tok], ["sh", "-xc", tok], ["ruby", "-ne", tok],
                     ["perl", "-lne", tok], ["perl", "-E", tok], ["php", "-B", tok], ["php", "-R", tok],
                     ["bash", "-s", tok], ["bash", "-i", tok], ["ruby", "-w", "-e", tok]):
            self.assertEqual(P(argv[0], argv), argv[0], argv)
        self.assertEqual(P("ruby", ["ruby", "/x/tool.rb"]), "ruby", "副檔名只收 .sh/.bash/.zsh/.py/.js")
        self.assertEqual(P("bash", ["bash", "/x/" + "a" * 38 + ".sh"]), "bash", "檔名 > 40 字還收")
        self.assertEqual(P("bash", ["bash", "/x/run.bash"]), "run.bash")
        self.assertEqual(P("zsh", ["zsh", "-e", "/x/run.zsh"]), "run.zsh")
        self.assertEqual(P("perl", ["perl", "-w", "/x/run.py"]), "run.py")
        # L3:讀 argv 時是 sh,讀完 pid 已被別的程式重用 ⇒ 不收 argv
        c = Clock()
        table = {100: (1, "tmux", 1.0), 1: (0, "launchd", 1.0)}
        n = {"c": 0, "a": 0}

        def pids():
            n["c"] += 1
            if n["c"] == 2:
                table[900] = (100, "sh", 0.3, os.getuid())
            return list(table)

        def args(p):
            n["a"] += 1
            table[900] = (100, "sh", 0.3, os.getuid() + 1)   # 讀 argv 的同時 pid 換成別的帳號的 sh(名字一樣)
            return ["sh", "/x/" + tok]
        base, known, born, cpu, prog = RU.sample(2, pids, lambda p: table.get(p), args, clock=c, sleep=c.sleep)
        self.assertGreater(n["a"], 0)
        self.assertNotIn(tok, json.dumps(prog), "pid 重用後還收了舊 argv")
        # L2:現跑結果的主人欄也洗
        out = RU.attribute({100}, {100: (1, "tmux"), 900: (100, "sh")}, [900], {900: 0.1}, {900: "sh"},
                           {100: ("seat", "a/b\x1b[2Jxxxxxxxxxxxx")}, 1)
        self.assertEqual(out[0]["owner"], "a?b?2Jxxxxxx")

    def test_u7f_round2_lows(self):
        P = RU.program_of
        tok = "tok_9f2a1c.sh"
        for argv in (["fish", "-C", "TOKEN=abc; " + tok], ["fish", "--init-command", tok], ["node", "--eval=x()", "/x/" + tok[:-3] + ".js"],
                     ["python3", "-m", "tok_9f2a1c"]):
            self.assertEqual(P(argv[0], argv), argv[0], argv)
        self.assertEqual(P("bash", ["bash", "-euxo", "pipefail", "/x/run.sh"]), "run.sh", "合寫吃值旗標沒連值跳過")
        self.assertEqual(P("python3", ["python3", "-m", "pytest"]), "pytest")
        self.assertEqual(P("python3", ["python3", "-m", "http.server"]), "http.server")

    def test_u7d_crashed_run_and_bad_room(self):
        os.makedirs(RH.state_dir(), exist_ok=True)
        self.room(RU.CHURN_FILE).write_text(json.dumps({"started_at": 990.0, "result": None}))   # 上一次被砍
        self.assertEqual(self.run_churn(1000.0)["state"], "ok", "上一次被砍留下的半份紀錄卡住 60 秒")
        shutil.rmtree(RH.state_dir())
        other = Path(self.d) / "elsewhere"
        other.mkdir()
        os.symlink(str(other), RH.state_dir())
        r = self.run_churn(5000.0)
        self.assertEqual((r["state"], r["detail"]), ("error", "churn_sample_failed"), "room 是 symlink 還照寫")
        self.assertEqual(list(other.iterdir()), [], "寫進 symlink 指的地方了")

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
            base, known, born, cpu, prog = RU.sample(2, *RU._platform_fns(sys.platform))
        finally:
            th.join()
            for k in kids:
                k.wait(timeout=10)
        self.assertTrue(base)
        mine = [p for p in born if p in {k.pid for k in kids}]   # 只數自己起的,不看排名(機器忙時 sleep 會被擠出前 10)
        self.assertGreaterEqual(len(mine), 8, (len(born), len(mine)))
        named = sum(1 for p in mine if prog[p] == "sleep")      # CI macOS 偶爾停在 exec 前的名字;過半認得出就算
        self.assertGreaterEqual(named * 2, len(mine), {prog[p] for p in mine})
        self.assertTrue(all(known[p][0] == os.getpid() for p in mine))


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
    ("K1", "src/cockpit/room_heat_unseen.py", "    if seen > busy + max(0.3, busy * 0.15):\n", "    if False:\n",
     "CpuTotal.test_u2_unseen"),
    ("K2", "src/cockpit/room_heat_unseen.py", "CHURN_THROTTLE_S = 60\n", "CHURN_THROTTLE_S = 0\n",
     "Churn.test_u5_throttle"),
    ("K3", "src/cockpit/room_heat_unseen.py", "            if any(ch in a[1:] for ch in \"csiC\"):     # C:fish -C(--init-command)也是指令字串(第 2 輪 Low-1)\n",
     "            if a in (\"-c\", \"-s\"):\n", "Churn.test_u7c_shell_flag_groups_h1"),
    ("K5", "src/cockpit/room_heat_unseen.py", "        elif c <= window * 2:\n", "        else:\n",
     "CpuTotal.test_u1c_same_window_seen_and_steal"),
    ("K4", "src/cockpit/room_heat_unseen.py", "                    \"program\": room_heat.safe_name(x.get(\"program\"), 40),\n",
     "                    \"program\": x.get(\"program\"),\n", "Churn.test_u8_cached_cleaned"),
    ("K6", "src/cockpit/room_heat_unseen.py", "        if _inline_flag(base, args):\n", "        if False:\n",
     "Churn.test_u7e_e3_ruling"),
    ("K7", "src/cockpit/room_heat_unseen.py", "                            args = None\n", "                            pass\n",
     "Churn.test_u7e_e3_ruling"),
    ("K8", "src/cockpit/room_heat_unseen.py", "    if (platform or sys.platform).startswith(\"linux\"):\n        return _linux_proc_times(proc_root)\n",
     "", "CpuTotal.test_u1g_linux_proc_times_subsecond"),
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
