#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""背景程式跟著艙走;艙裡沒有 agy 席就不探 agy(10-14 公開版新用戶走查第 10 條)。

以前(新用戶開一席 claude、CLI 沒裝、席位死、關掉艙):
- open_attempt.py watch 還在跑:remain-on-exit 的死窗格 capture-pane 照樣有字 ⇒ 一直等到 600 秒逾時
- usage_probe.py --agy-print-refresh 照樣被右欄拉起:艙裡根本沒有 agy 席,卻跑 `agy -p /usage`、寫 ~/.gemini
B1 watch 對著死窗格(remain-on-exit):幾秒內回 pane_dead,不等逾時
B2 watch 途中艙的 tmux 伺服器被收掉:幾秒內結束、不留事件(艙家可能正在被刪)
B3 cabin_has_live_seat:只有 claude 席 ⇒ False;有活的 agy 席 ⇒ True;agy 席已退 ⇒ False;問不出艙 ⇒ False
B4 右欄單家模式(enable_cli={"agy"}):艙裡沒有 agy 席 ⇒ 不起背景量測;有 ⇒ 照起(對照)
B5 背景量測起跑時艙已經沒了 ⇒ 不跑 agy
B6 背景量測跑到一半艙沒了 ⇒ 連同 agy 一起在幾秒內結束
刀(複本):K1 拿掉死窗格判斷 ⇒ B1 紅;K2 拿掉 agy 席判斷 ⇒ B4 紅;K3 拿掉量測的艙守望 ⇒ B6 紅
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COCKPIT = ROOT / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(ROOT / "tests"))
from aib_tmp_root import aib_tmp_root  # noqa: E402

TMUX = shutil.which("tmux")
OPEN_ATTEMPT = COCKPIT / "open_attempt.py"
USAGE_PROBE = COCKPIT / "usage_probe.py"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-bg-life-", dir=aib_tmp_root())
        self.sock = tempfile.mkdtemp(prefix="at.", dir="/tmp")   # tmux socket 路徑有長度上限
        self.home = os.path.join(self.tmp, "home")
        self.cabin = os.path.join(self.home, ".ez2-aib", "instances", "t1")
        os.makedirs(os.path.join(self.cabin, "seats"))
        self.hub = "aihub-t1"
        self.env = dict(os.environ, HOME=self.home, TMUX_TMPDIR=self.sock, EZ2_AIHUB_SOCKET=self.hub,
                        EZ2_AIB_INSTANCE_HOME=self.cabin, EZ2_AIB_HOME=os.path.join(self.home, ".ez2-aib"),
                        EZ2_COCKPIT_STATE_DIR=os.path.join(self.cabin, "cockpit"), AIB_DM_TMUX=TMUX or "tmux")
        for k in ("TMUX", "TMUX_PANE", "EZ2_AIB_INSTANCE", "EZ2_USAGE_PROBE_LIVE", "EZ2_USAGE_PROBE_LIVE_ENGINES"):
            self.env.pop(k, None)

    def tearDown(self):
        self.kill_server()
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.sock, ignore_errors=True)

    def tmux(self, *a):
        return subprocess.run([TMUX, "-L", self.hub, *a], env=self.env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)

    def kill_server(self):
        if TMUX:
            self.tmux("kill-server")

    def seat(self, name, provider, exited=False):
        rec = {"provider": provider, "role": "dev", "window_id": "@1"}
        if exited:
            rec.update(exited_at_epoch=int(time.time()), exit_reason="cli-exit")
        Path(self.cabin, "seats", name + ".json").write_text(json.dumps(rec), encoding="utf-8")


@unittest.skipUnless(TMUX, "needs tmux")
class Watch(Base):
    def watch(self, window, timeout):
        return subprocess.Popen([sys.executable, str(OPEN_ATTEMPT), "watch", "--home", self.cabin,
                                 "--attempt", "a1", "--window", window, "--provider", "claude",
                                 "--timeout", str(timeout), "--interval", "0.2", "--tmux-bin", TMUX],
                                env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)

    def events(self):
        out = []
        for f in Path(self.cabin).rglob("*.jsonl"):
            out += [json.loads(l).get("event") for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        return out

    def test_b1_dead_pane_ends_fast(self):
        self.assertEqual(self.tmux("new-session", "-d", "-s", "aihub", "-n", "w", "sleep 0.3").returncode, 0)
        self.tmux("set-option", "-g", "remain-on-exit", "on")
        self.tmux("respawn-pane", "-k", "-t", "aihub:w", "echo bye")
        time.sleep(0.8)
        t0 = time.monotonic()
        p = self.watch("aihub:w", 25)
        try:
            p.wait(timeout=30)
        finally:
            p.kill()
        took = time.monotonic() - t0
        self.assertLess(took, 10, "watch kept polling a dead pane for %.1fs" % took)
        self.assertIn("pane_dead", self.events())

    def test_b2_server_gone_ends_quietly(self):
        self.assertEqual(self.tmux("new-session", "-d", "-s", "aihub", "-n", "w", "sleep 100").returncode, 0)
        p = self.watch("aihub:w", 25)
        time.sleep(1.0)
        self.kill_server()
        t0 = time.monotonic()
        try:
            p.wait(timeout=30)
        finally:
            p.kill()
        self.assertLess(time.monotonic() - t0, 5)
        self.assertNotIn("pane_dead", self.events())
        self.assertNotIn("watch_timeout", self.events())


class Seats(Base):
    def test_b3_cabin_has_live_seat(self):
        import usage_probe as up
        self.seat("w1", "claude")
        self.assertFalse(up.cabin_has_live_seat("agy", environ=self.env))
        self.seat("w2", "agy", exited=True)
        self.assertFalse(up.cabin_has_live_seat("agy", environ=self.env))
        self.seat("w3", "agy")
        self.assertTrue(up.cabin_has_live_seat("agy", environ=self.env))
        env = dict(self.env)
        env.pop("EZ2_AIB_INSTANCE_HOME")
        env.pop("EZ2_AIHUB_SOCKET")
        self.assertFalse(up.cabin_has_live_seat("agy", environ=env))

    def spawned(self, with_agy):
        import usage_probe as up
        self.seat("w1", "claude")
        if with_agy:
            self.seat("w2", "agy")
        calls = []
        old_env, old_spawn = dict(os.environ), up._spawn_agy_print_refresh
        os.environ.clear()
        os.environ.update(self.env)
        up._spawn_agy_print_refresh = lambda dest, now, popen=None: calls.append(dest)
        try:
            up.refresh_all(now=time.time(), dest_dir=os.path.join(self.tmp, "usage"), enable_cli={"agy"},
                           throttle_s=0)
        finally:
            up._spawn_agy_print_refresh = old_spawn
            os.environ.clear()
            os.environ.update(old_env)
        return calls

    def test_b4_no_agy_seat_no_probe(self):
        self.assertEqual(self.spawned(with_agy=False), [])

    def test_b4_control_agy_seat_probes(self):
        self.assertEqual(len(self.spawned(with_agy=True)), 1)


@unittest.skipUnless(TMUX, "needs tmux")
class Worker(Base):
    def fake_agy(self, body):
        d = os.path.join(self.tmp, "bin")
        os.makedirs(d, exist_ok=True)
        p = Path(d, "agy")
        p.write_text("#!/bin/sh\necho started >> %s\n%s\n" % (os.path.join(self.tmp, "agy-ran"), body),
                     encoding="utf-8")
        p.chmod(0o755)
        self.env["PATH"] = d + ":" + self.env["PATH"]

    def worker(self):
        return subprocess.Popen([sys.executable, str(USAGE_PROBE), "--agy-print-refresh", "--dest",
                                 os.path.join(self.tmp, "usage")], env=self.env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def test_b5_cabin_already_gone(self):
        self.fake_agy("exit 0")
        p = self.worker()
        p.wait(timeout=60)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "agy-ran")))

    def test_b6_cabin_gone_midway(self):
        self.assertEqual(self.tmux("new-session", "-d", "-s", "aihub", "sleep 100").returncode, 0)
        self.fake_agy("sleep 60")
        p = self.worker()
        try:
            for _ in range(50):
                if os.path.exists(os.path.join(self.tmp, "agy-ran")):
                    break
                time.sleep(0.1)
            self.assertTrue(os.path.exists(os.path.join(self.tmp, "agy-ran")), "fake agy never started")
            self.kill_server()
            t0 = time.monotonic()
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pass
            self.assertIsNotNone(p.poll(), "worker still running after the cabin was gone")
            self.assertLess(time.monotonic() - t0, 8)
            left = subprocess.run(["pgrep", "-f", os.path.join(self.tmp, "bin", "agy")], capture_output=True,
                                  text=True).stdout.split()
            self.assertEqual(left, [], "agy left running")
        finally:
            try:
                os.killpg(p.pid, 9)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
