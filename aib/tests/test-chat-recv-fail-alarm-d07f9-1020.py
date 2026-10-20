#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""收信連續失敗警報(10-20 @abb89 派、會員優化清單 E 段)。
起因:Pro 13 的 recv-state.json 超過 64 KB ⇒ 每次存檔炸、收信 session 每 5 秒重來,從 10-19 11:20 到隔天沒人發現。

R1(要 AIB_TEST_NOSTR_VENV)Receiver.run:session 一下就斷 4 次 ⇒ fail.n 4、why 例外類名、since 有;
   之後一段 session 撐過 RECV_OK_S 才結束 ⇒ 歸零;write_worker_status 把 recv_fail 寫進 worker-status.json
R2 recv_fail_line:n ≥ 3 ⇒ ⚠ 帶次數、原因、幾分鐘;n 2 ⇒ None;沒這欄(舊工人)⇒ None;why 裡的控制字元清掉
R3 四語字典都有,參數名一致
R4 aib chat status(沙盒:自己的 pid 當轉接程式、假的 worker-status.json)印出那一行 ⚠
刀(複本):K1 門檻永遠到不了 ⇒ R2 紅;K2 status 不印 ⇒ R4 紅
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import dm_global_files as GF  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_bridge", str(COCKPIT / "chat_bridge.py"))
CB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CB)

NOSTR_VENV = os.environ.get("AIB_TEST_NOSTR_VENV") or ""
VPY = str(Path(NOSTR_VENV) / "bin" / "python") if NOSTR_VENV else ""
HAVE_NOSTR = bool(VPY) and Path(VPY).exists() and subprocess.run(
    [VPY, "-c", "import nostr_sdk"], capture_output=True).returncode == 0

PROBE = r'''
import asyncio, json, os, sys, time
sys.path.insert(0, sys.argv[1]); root = sys.argv[2]
import chat_nostr as CN
CN.RECV_OK_S = 0.3
async def quick(stop, s):
    await asyncio.sleep(0.01)
CN._sleep_or_stop = quick
class Boom(Exception):
    pass
r = CN.Receiver(root, None)
r._our_seats = lambda: ({"ab": {"key": "x"}}, None)
calls = {"n": 0}
async def bad(stop, seats):
    calls["n"] += 1
    if calls["n"] >= 4:
        stop.set()
    raise Boom()
r._session = bad
asyncio.run(r.run(asyncio.Event()))
out = {"after_fail": dict(r.fail)}
async def good(stop, seats):
    await asyncio.sleep(0.4)
    stop.set()
r._session = good
asyncio.run(r.run(asyncio.Event()))
out["after_ok"] = dict(r.fail)
for _ in range(3):
    r._recv_failed("AtomicWriteError")
asyncio.run(CN.write_worker_status(root, r, {"started": time.time(), "clock": None}))
out["ws"] = json.load(open(os.path.join(root, "worker-status.json")))["recv_fail"]
print(json.dumps(out))
'''


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk 的 venv)")
class Worker(unittest.TestCase):
    def test_r1_counter(self):
        with tempfile.TemporaryDirectory(prefix="aib-recvfail-") as td:
            root = Path(td) / "bridge"
            GF.ensure_dirs(root)
            env = dict(os.environ, HOME=td, EZ2_AIB_STATE_ROOT=td)
            r = subprocess.run([VPY, "-c", PROBE, str(COCKPIT), str(root)], env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            got = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((got["after_fail"]["n"], got["after_fail"]["why"]), (4, "Boom"))
        self.assertIsInstance(got["after_fail"]["since"], int)
        self.assertEqual(got["after_ok"], {"n": 0, "since": None, "why": None})
        self.assertEqual((got["ws"]["n"], got["ws"]["why"]), (3, "AtomicWriteError"))


class Status(unittest.TestCase):
    def test_r2_line(self):
        now = 1_800_000_000
        w = {"recv_fail": {"n": 3, "since": now - 600, "why": "AtomicWriteError"}}
        line = CB.recv_fail_line(w, now=now)
        self.assertTrue(line.startswith("⚠") and "3" in line and "AtomicWriteError" in line and "10 分鐘" in line, line)
        self.assertIsNone(CB.recv_fail_line({"recv_fail": {"n": 2, "since": now - 600, "why": "x"}}, now=now))
        self.assertIsNone(CB.recv_fail_line({}, now=now))
        bad = CB.recv_fail_line({"recv_fail": {"n": 5, "since": None, "why": "x\x1b[31m\ny"}}, now=now)
        self.assertNotIn("\x1b", bad)
        self.assertNotIn("\n", bad)

    def test_r3_dict(self):
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            s = d["aib.chat.status_recv_failing"]
            self.assertTrue(all(k in s for k in ("{n}", "{why}", "{mins}")), (loc, s))

    def test_r4_status_prints(self):
        with tempfile.TemporaryDirectory(prefix="aib-recvfail-") as td:
            home = Path(td) / "home"
            home.mkdir()
            root = Path(td) / "bridge"
            GF.ensure_dirs(root)
            env = {"HOME": str(home), "AIB_CHAT_BRIDGE_DIR": str(root), "EZ2_AIB_LOCALE": "zh-TW",
                   "EZ2_AIB_STATE_ROOT": str(home / ".ez2-aib"), "PATH": os.environ.get("PATH", "")}
            CB.write_status(root, state="published", pid=os.getpid(), instance=None, since=int(time.time()), at=int(time.time()))
            w = {"v": 1, "at": int(time.time()), "pid": os.getpid(), "relays": {}, "up": 0, "total": 0, "offline": False,
                 "today": {}, "pending": 0, "clock": None, "recv_fail": {"n": 4, "since": int(time.time()) - 120, "why": "Boom"}}
            (root / "worker-status.json").write_text(json.dumps(w), encoding="utf-8")
            buf = io.StringIO()
            CB.main(["status"], env=env, out=buf)
        self.assertIn("收信連續失敗 4 次", buf.getvalue())


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_bridge.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_threshold_unreachable(self):
        self._knife("    if n < RECV_FAIL_WARN:\n", "    if True:\n", "Status.test_r2_line")

    def test_k2_status_silent(self):
        self._knife("        line = recv_fail_line(w)\n", "        line = None\n", "Status.test_r4_status_prints")


if __name__ == "__main__":
    unittest.main()
