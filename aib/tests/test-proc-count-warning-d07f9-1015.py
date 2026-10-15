#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""G11 行程數快滿預警(@abb89 10-15 22:56 派工、維護者准;10-15 18:4x 事故右欄自己先掛、沒人預警)。

P1 macOS:上限 = sysctl kern.maxprocperuid;支數跟 `ps -U <uid>` 對得上(量的當下會差幾支,容許 ±30)
P2 不 fork:libproc 量的時候不呼叫 subprocess;libproc 不能用且 allow_fork=False ⇒ 量不到(None),不退回 ps
P3 門檻:超過 / 未達的判斷、AIB_PROC_WARN_PCT 合法與不合法、量不到上限就不警告
P4 前幾名:固定名單 ⇒ 前 3 名與支數對、量不到名字的記「?」;實機名單照支數由多到少、加總 = 總數
P5 右欄:超過門檻 ⇒ rows() 第 0 行是警告、其餘各行與沒警告時逐行相同(只多一行,點擊位置跟著移);沒超過不佔行
P6 右欄子行程:machine_status_row.py --width 門檻 1% ⇒ 第一行是警告;預設門檻不出現
P7 巡檢:fleet_patrol scan 在艙連不上(rc 2)時,行程數兩行照樣先印出來
Z1 英文介面沒有中日韓字、沒有缺鍵;四語 aib.proc_count.* 同鍵、佔位符一致
刀(複本):K1 rows() 拿掉插警告那一行 ⇒ P5 紅;K2 allow_fork 不理 ⇒ P2 紅;K3 依 real uid 篩改成全部行程 ⇒ P1 紅
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import proc_count as PC  # noqa: E402
import machine_status_row as MSR  # noqa: E402

DARWIN = sys.platform == "darwin"
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
MISSING = re.compile(r"!!aib\.")


def _env(**extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("EZ2_", "AIB_", "TMUX")) and k not in ("PYTHONPATH", "BASH_ENV")}
    env.update({"EZ2_AIB_LOCALE": "zh-TW", "PYTHONDONTWRITEBYTECODE": "1"})
    env.update(extra)
    return env


class Measure(unittest.TestCase):
    @unittest.skipUnless(DARWIN, "macOS 才有 kern.maxprocperuid / libproc")
    def test_p1_matches_sysctl_and_ps(self):
        lim, src = PC.limit()
        want = int(subprocess.run(["sysctl", "-n", "kern.maxprocperuid"], capture_output=True, text=True).stdout)
        self.assertEqual((lim, src), (want, "sysctl"))
        rec = PC.check(top_n=0)
        ps_n = len([x for x in subprocess.run(["ps", "-U", str(os.getuid()), "-o", "pid="],
                                              capture_output=True, text=True).stdout.split() if x.strip()])
        self.assertEqual(rec["source"], "libproc")
        self.assertLessEqual(abs(rec["count"] - ps_n), 30, (rec["count"], ps_n))

    @unittest.skipUnless(DARWIN, "libproc 那條路只在 macOS")
    def test_p2_no_fork(self):
        boom = mock.Mock(side_effect=AssertionError("量的時候 fork 了"))
        with mock.patch.object(PC.subprocess, "run", boom), mock.patch.object(PC.subprocess, "Popen", boom):
            rec = PC.check(allow_fork=False)
            self.assertIsNotNone(rec["count"])
            self.assertEqual(rec["source"], "libproc")
            self.assertTrue(rec["top"])
            with mock.patch.object(PC, "_load", return_value=False):
                procs, src = PC.user_processes(allow_fork=False)
        self.assertEqual((procs, src), (None, "unknown"))
        self.assertFalse(boom.called)


class Threshold(unittest.TestCase):
    def test_p3_threshold(self):
        n = PC.check(top_n=0, lim=(10 ** 9, "test"))["count"]
        self.assertIsNotNone(n)
        hi = PC.check(top_n=0, lim=(n, "test"), threshold=0.8)
        self.assertTrue(hi["warn"])
        lo = PC.check(top_n=0, lim=(n * 10, "test"), threshold=0.8)
        self.assertFalse(lo["warn"])
        none = PC.check(top_n=0, lim=(None, "unknown"))
        self.assertFalse(none["warn"])
        self.assertIsNone(none["ratio"])
        self.assertEqual(PC.threshold_from_env({}), 0.8)
        self.assertEqual(PC.threshold_from_env({"AIB_PROC_WARN_PCT": "50"}), 0.5)
        for bad in ("0", "-3", "101", "abc", ""):
            self.assertEqual(PC.threshold_from_env({"AIB_PROC_WARN_PCT": bad}), 0.8, bad)

    def test_p4_top(self):
        fake = ["bash"] * 5 + ["zsh"] * 2 + ["aibg11probe"] * 7 + [None] * 3 + ["tmux"]
        with mock.patch.object(PC, "user_processes", return_value=(fake, "test")):
            rec = PC.check(lim=(100, "test"))
            self.assertEqual(rec["count"], len(fake))
            self.assertEqual(rec["top"], [["aibg11probe", 7], ["bash", 5], ["?", 3]])
            self.assertEqual(len(PC.check(lim=(100, "test"), top_n=10)["top"]), 5)
        live = PC.check(top_n=1000, lim=(10 ** 9, "test"))                 # 實機:有名單、照支數由多到少、加總不超過總數
        counts = [k for _n, k in live["top"]]
        self.assertTrue(counts)
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertEqual(sum(counts), live["count"])


class RightColumn(unittest.TestCase):
    def _rows(self, pct):
        td = Path(tempfile.mkdtemp(prefix="aib-g11-"))
        self.addCleanup(shutil.rmtree, td, True)
        cache = td / "machine.json"
        cache.write_text(json.dumps({"cells": [{"key": "mem", "value": 50.0, "state": "ok"}], "level": {"load_level": "ok", "thermal_level": "ok"},
                                     "at": "2026-10-15T23:00:00"}))
        env = {"AIB_PROC_WARN_PCT": pct} if pct else {}
        fake_lim = 10 * len(PC.user_processes(names=False, allow_fork=False)[0] or [1])  # 用量 10%:預設門檻不警告、1% 會警告
        with mock.patch.dict(os.environ, env), mock.patch.object(MSR, "spawn_refresh", lambda *a, **k: None), \
                mock.patch.object(MSR, "composer_notice_lines", lambda w: []), \
                mock.patch.object(PC, "limit", lambda: (fake_lim, "test")):  # CI 的 Linux 沒有 RLIMIT_NPROC(無上限 ⇒ 照設計不預警),上限給假的
            if not pct:
                os.environ.pop("AIB_PROC_WARN_PCT", None)
            return MSR.rows(44, ttl=10 ** 9, path=str(cache))

    def test_p5_rows_top_line(self):
        base = self._rows(None)
        warned = self._rows("1")
        self.assertNotIn(MSR.MEASURING, base, "要走讀得到快取的那條排版路線,不是「量測中」")
        self.assertEqual(len(warned), len(base) + 1)
        self.assertIn("行程快滿", warned[0])
        self.assertEqual(warned[1:], base, "警告以外的行要逐行相同")
        self.assertFalse(any("行程快滿" in x for x in base))

    def test_p6_subprocess(self):
        if PC.limit()[0] is None:
            self.skipTest("這台量不到行程上限(Linux 無 RLIMIT_NPROC),照設計不預警;子行程沒法換假上限")
        r = subprocess.run([sys.executable, str(COCKPIT / "machine_status_row.py"), "--width", "44"],
                           capture_output=True, text=True, env=_env(AIB_PROC_WARN_PCT="1"), timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("行程快滿", r.stdout.splitlines()[0])
        r = subprocess.run([sys.executable, str(COCKPIT / "machine_status_row.py"), "--width", "44"],
                           capture_output=True, text=True, env=_env(), timeout=60)
        self.assertNotIn("行程快滿", r.stdout)


class Patrol(unittest.TestCase):
    def test_p7_patrol_prints_first(self):
        r = subprocess.run([sys.executable, str(COCKPIT / "fleet_patrol.py"), "scan", "--hub", "aihub-nosuchg11%d" % os.getpid()],
                           capture_output=True, text=True, env=_env(), timeout=120)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertTrue(lines and lines[0].startswith(("行程數", "⚠ 行程數")), r.stdout)
        self.assertIn("行程數最多的指令", r.stdout)


class Lang(unittest.TestCase):
    def test_z1_english_and_parity(self):
        out = ""
        # --top 0:行程名是使用者的資料(可能本來就是中文 App 名),只驗介面字
        for args in ([str(COCKPIT / "proc_count.py"), "--top", "0"], [str(COCKPIT / "proc_count.py"), "--top", "0", "--threshold", "0.01"],
                     [str(COCKPIT / "proc_count.py"), "--top", "0", "--warn-line", "--threshold", "0.01"]):
            r = subprocess.run([sys.executable] + args, capture_output=True, text=True, env=_env(EZ2_AIB_LOCALE="en"), timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            out += r.stdout
        self.assertIn("Processes", out)
        self.assertIsNone(CJK.search(out), out)
        self.assertIsNone(MISSING.search(out), out)
        dicts = {lg: json.loads((REPO / "i18n" / ("%s.json" % lg)).read_text(encoding="utf-8"))
                 for lg in ("zh-TW", "en", "ja", "ko")}
        keys = {lg: sorted(k for k in d if k.startswith("aib.proc_count.")) for lg, d in dicts.items()}
        self.assertTrue(keys["zh-TW"])
        for lg in ("en", "ja", "ko"):
            self.assertEqual(keys[lg], keys["zh-TW"], lg)
            for k in keys["zh-TW"]:
                self.assertEqual(sorted(re.findall(r"\{\w+\}", dicts[lg][k])),
                                 sorted(re.findall(r"\{\w+\}", dicts["zh-TW"][k])), "%s %s" % (lg, k))


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=180)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_no_top_line(self):
        self._knife("src/cockpit/machine_status_row.py", "    lines[:0] = proc_warn_lines(width)\n", "",
                    "RightColumn.test_p5_rows_top_line")

    @unittest.skipUnless(DARWIN, "P2 只在 macOS 跑")
    def test_k2_fork_anyway(self):
        self._knife("src/cockpit/proc_count.py", "got = _ps_procs(uid) if allow_fork else None", "got = _ps_procs(uid)",
                    "Measure.test_p2_no_fork")

    @unittest.skipUnless(DARWIN, "P1 只在 macOS 跑")
    def test_k3_all_users(self):
        self._knife("src/cockpit/proc_count.py", "PROC_RUID_ONLY = 5 ", "PROC_RUID_ONLY = 1 ", "Measure.test_p1_matches_sysctl_and_ps")


if __name__ == "__main__":
    unittest.main()
