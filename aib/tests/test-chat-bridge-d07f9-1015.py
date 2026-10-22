#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球對談轉接程式骨架(開發文件第 20 節、22.1 PR-5 = 1.1;Andy 10-15 22:5x 裁「放開源核心」)。

全部在沙盒:AIB_CHAT_HOME / AIB_CHAT_BRIDGE_DIR 指到暫存目錄,不連網(on 一律 --no-venv),tmux 用自己的 socket。
常駐的 run 都經 aib_proc_guard 起(失控只死自己那一組),測完逐支收掉。

C1 全新沙盒:status 開關關、沒在跑、沒有心跳;run 在開關關著時馬上結束 rc 0
C2 on --no-venv(不在艙裡):開關檔 0600、目錄 0700、說「不在艙裡」;enabled rc 0 / off 後 rc 1
C3 當班:刪掉殘留心跳、寫 status.json(no_keys、pid、instance)、跑幾輪都**不寫心跳** ⇒ bridge_alive False
C4 一台一支:第二支排隊不搶 status;當班被 SIGKILL ⇒ 排隊那支接手(pid 換人)
C5 off:SIGTERM 當班 ⇒ status stopped、行程結束;排隊那支看到開關關掉也結束
C6 off 不亂殺:status 裡的 pid 是別的程式(不是 chat_bridge.py run)⇒ 不送信號
C7 清舊檔:receipts 裡 8 天前的檔被清、outbox 的待寄檔不動
C8 sys.chat 窗:沒有就開(@aib_role、remain-on-exit)、有就不動、pane 死了 respawn
C9 aib chat 分派:bin/aib chat status --json 走得到 chat_bridge.py
C10 run / boot-run 決定結束後 SIGTERM 換成 SIG_IGN(Python 拆直譯器不會換回預設)⇒ off 打在收尾空檔不會變 -15
   (10-22 #263 CI:test_c5 偶發 a.returncode -15;本機 25 次紅 4 次,修後 50 次 0 紅)
Z1 英文介面 status / on / off 沒有中日韓字、沒有缺鍵;四語 aib.chat.* 同鍵、佔位符一致
刀(複本):K1 拿掉「刪殘留心跳」⇒ C3 紅;K2 拿掉 off 的「確認是轉接程式」⇒ C6 紅;K3 結束時不改 SIG_IGN ⇒ C10 紅
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
BRIDGE = COCKPIT / "chat_bridge.py"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import dm_global_files as GF  # noqa: E402

CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
MISSING = re.compile(r"!!aib\.")


class Box(object):
    """一個沙盒:自己的 HOME、chat home、bridge root。"""

    def __init__(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-chat-"))
        self.home = self.td / "home"
        self.home.mkdir()
        self.chat = self.td / "chat"
        self.root = self.td / "bridge"
        self.procs = []

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("EZ2_", "AIB_", "TMUX")) and k not in ("PYTHONPATH", "BASH_ENV")}
        env.update({"HOME": str(self.home), "AIB_CHAT_HOME": str(self.chat), "AIB_CHAT_BRIDGE_DIR": str(self.root),
                    "AIB_CHAT_TICK": "0.1", "EZ2_AIB_LOCALE": "zh-TW", "PYTHONDONTWRITEBYTECODE": "1"})
        env.update(extra)
        return env

    def cli(self, *args, **extra):
        return subprocess.run([sys.executable, str(BRIDGE)] + list(args), capture_output=True, text=True,
                              env=self.env(**extra), timeout=60)

    def start_run(self, **extra):
        p = subprocess.Popen([sys.executable, str(BRIDGE), "run"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, env=self.env(**extra), start_new_session=True)
        self.procs.append(p)
        return p

    def status(self):
        try:
            return json.loads((self.root / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def wait_for(self, cond, secs=10):
        end = time.time() + secs
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.05)
        return False

    def close(self):
        for p in self.procs:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            try:
                p.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        shutil.rmtree(self.td, ignore_errors=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.b = Box()
        self.addCleanup(self.b.close)

    def turn_on(self):
        r = self.b.cli("on", "--no-venv", "--name", "Tester", "--country", "TW")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class Basics(Base):
    def test_c1_fresh(self):
        r = self.b.cli("status", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = json.loads(r.stdout)
        self.assertFalse(rec["enabled"])
        self.assertFalse(rec["running"])
        self.assertFalse(rec["heartbeat"])
        self.assertEqual(rec["venv"], "missing")
        r = subprocess.run([sys.executable, str(BRIDGE), "run"], capture_output=True, text=True, env=self.b.env(), timeout=20)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("線上對話已關", r.stdout)

    def test_c2_on_off_switch(self):
        r = self.b.cli("on", "--no-venv", "--name", "Tester", "--country", "TW")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("不在艙裡", r.stdout)
        sw = self.b.chat / "enabled.json"
        self.assertEqual(oct(sw.stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(self.b.chat.stat().st_mode & 0o777), "0o700")
        self.assertEqual(self.b.cli("enabled").returncode, 0)
        self.assertEqual(self.b.cli("off").returncode, 0)
        self.assertFalse(sw.exists())
        self.assertEqual(self.b.cli("enabled").returncode, 1)


class Leader(Base):
    def test_c3_leader_no_heartbeat(self):
        self.turn_on()
        GF.ensure_dirs(self.b.root)
        GF.write_heartbeat(self.b.root, pid=1)             # 殘留的新鮮心跳:不刪的話寄件端會以為轉接程式在
        self.assertTrue(GF.bridge_alive(self.b.root))
        p = self.b.start_run(EZ2_AIB_INSTANCE="20261015-000000-test")
        self.assertTrue(self.b.wait_for(lambda: (self.b.status() or {}).get("pid") == p.pid), "沒當班")
        # 1.2 起:當班一上來先寫 no_keys,同一輪把主人金鑰做好就改 no_relays(中繼站還沒接)
        self.assertTrue(self.b.wait_for(lambda: (self.b.status() or {}).get("state") == "no_relays"), self.b.status())
        st = self.b.status()
        self.assertEqual(st["instance"], "20261015-000000-test")
        time.sleep(0.6)                                       # 跑幾輪
        self.assertFalse((self.b.root / "heartbeat.json").exists(), "這一版不准有心跳")
        self.assertFalse(GF.bridge_alive(self.b.root))
        self.assertEqual(oct((self.b.root / "status.json").stat().st_mode & 0o777), "0o600")

    def test_c4_one_per_machine_and_failover(self):
        self.turn_on()
        a = self.b.start_run()
        self.assertTrue(self.b.wait_for(lambda: (self.b.status() or {}).get("pid") == a.pid))
        b = self.b.start_run()
        time.sleep(0.6)
        self.assertEqual(self.b.status()["pid"], a.pid, "排隊那支不准搶")
        self.assertIsNone(b.poll(), "排隊那支要留著備援")
        os.killpg(a.pid, signal.SIGKILL)
        self.assertTrue(self.b.wait_for(lambda: (self.b.status() or {}).get("pid") == b.pid), "當班死了沒人接手")
        rec = json.loads(self.b.cli("status", "--json").stdout)
        self.assertTrue(rec["running"])
        self.assertEqual(rec["pid"], b.pid)

    def test_c5_off_stops_leader_and_standby(self):
        self.turn_on()
        a = self.b.start_run()
        self.assertTrue(self.b.wait_for(lambda: (self.b.status() or {}).get("pid") == a.pid))
        b = self.b.start_run()
        time.sleep(0.3)
        r = self.b.cli("off")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.b.wait_for(lambda: a.poll() is not None and b.poll() is not None), "off 之後還有轉接程式在跑")
        self.assertEqual(a.returncode, 0)
        self.assertEqual(b.returncode, 0)
        self.assertEqual(self.b.status()["state"], "stopped")
        self.assertFalse(json.loads(self.b.cli("status", "--json").stdout)["running"])

    def test_c6_off_never_signals_a_stranger(self):
        stranger = subprocess.Popen(["sleep", "30"], start_new_session=True)
        self.addCleanup(lambda: (stranger.poll() is None and stranger.kill(), stranger.wait()))
        GF.ensure_dirs(self.b.root)
        (self.b.root / "status.json").write_text(json.dumps({"v": 1, "state": "no_keys", "pid": stranger.pid}))
        self.assertEqual(self.b.cli("off").returncode, 0)
        time.sleep(0.3)
        self.assertIsNone(stranger.poll(), "off 把不是轉接程式的行程砍了")

    def test_c7_sweep_old_files(self):
        self.turn_on()
        GF.ensure_dirs(self.b.root)
        old = time.time() - 8 * 86400
        stale = self.b.root / "receipts" / ("a" * 32 + ".json")
        pending = self.b.root / "outbox" / ("b" * 32 + ".json")
        for f in (stale, pending):
            f.write_text("{}")
            os.utime(f, (old, old))
        p = self.b.start_run()
        self.assertTrue(self.b.wait_for(lambda: (self.b.status() or {}).get("pid") == p.pid))
        self.assertTrue(self.b.wait_for(lambda: not stale.exists()), "8 天前的回條沒清")
        self.assertTrue(pending.exists(), "待寄檔不准清")


@unittest.skipUnless(shutil.which("tmux"), "沒有 tmux")
class Window(Base):
    def setUp(self):
        super().setUp()
        self.sock = "aihub-chattest%d" % os.getpid()
        subprocess.run(["tmux", "-L", self.sock, "new-session", "-d", "-s", "aihub", "-n", "seat", "sleep 300"],
                       check=True, env=self.b.env())
        self.addCleanup(lambda: subprocess.run(["tmux", "-L", self.sock, "kill-server"], capture_output=True, env=self.b.env()))

    def tx(self, *args):
        # 跟建 session、跑 ensure-window 用同一份環境:aib test 底下 TMUX_TMPDIR 不同,混用會連到另一個 tmux(10-16 全套紅一次)
        return subprocess.run(["tmux", "-L", self.sock] + list(args), capture_output=True, text=True, env=self.b.env()).stdout.strip()

    def chat_windows(self):
        rows = self.tx("list-windows", "-t", "aihub", "-F", "#{window_id} #{@aib_role} #{pane_dead}").splitlines()
        return [r.split() for r in rows if " sys.chat" in r]

    def test_c8_ensure_window(self):
        self.turn_on()
        r = self.b.cli("ensure-window", "--socket", self.sock)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        wins = self.chat_windows()
        self.assertEqual(len(wins), 1, wins)
        wid = wins[0][0]
        self.assertEqual(self.tx("show-options", "-wqv", "-t", wid, "remain-on-exit"), "on")
        self.assertTrue(self.b.wait_for(lambda: (self.b.status() or {}).get("state") == "no_relays"), "窗裡的轉接程式沒當班")
        r = self.b.cli("ensure-window", "--socket", self.sock)
        self.assertIn("不動它", r.stdout)
        self.assertEqual(len(self.chat_windows()), 1, "重跑不准疊窗")
        self.b.cli("off")                                   # 窗裡的 run 看到開關關掉就結束 ⇒ pane 死(remain-on-exit)
        self.assertTrue(self.b.wait_for(lambda: self.chat_windows() and self.chat_windows()[0][-1] == "1"), "pane 沒死")
        self.turn_on()
        r = self.b.cli("ensure-window", "--socket", self.sock)
        self.assertIn("已重開", r.stdout)
        self.assertTrue(self.b.wait_for(lambda: self.chat_windows()[0][-1] == "0"))
        self.assertEqual(self.chat_windows()[0][0], wid, "要重開同一個窗,不是另開")
        self.b.cli("off")


class Keys(Base):
    """1.2:當班照活席發金鑰;aib chat keys 只印公開號碼。"""

    def _seat(self, inst, win, uuid, uid):
        d = self.b.home / ".ez2-aib" / "instances" / inst / "seats"
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%s.json" % win)).write_text(json.dumps({"schema": "aib-seat/v1", "session_uuid": uuid, "seat_id": uuid[-5:]}))
        disp = self.b.home / ".ez2-aib" / "identity" / "seat-display.json"
        disp.parent.mkdir(parents=True, exist_ok=True)
        cur = json.loads(disp.read_text()) if disp.exists() else {"display": {}}
        cur["display"][uuid.replace("-", "")[-5:]] = {"persona_uid": uid}
        disp.write_text(json.dumps(cur))

    def test_c10_leader_issues_seat_keys(self):
        self._seat("20261015-000000-6ddf", "w2", "00000000-0000-0000-0000-0000000aaa11", "aaaa11")
        self._seat("20261015-000000-931e", "w3", "00000000-0000-0000-0000-0000000bbb22", "bbbb22")
        self.turn_on()
        p = self.b.start_run()
        self.assertTrue(self.b.wait_for(lambda: (self.b.status() or {}).get("pid") == p.pid))
        r = self.b.cli("keys", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        pv = json.loads(r.stdout)
        self.assertTrue(pv["owner"].startswith("npub1"))
        got = sorted((e["seat5"], e["cabin"], e["persona_uid"]) for e in pv["seats"] if e["status"] == "active")
        self.assertEqual(got, [("0aaa11"[-5:], "6ddf", "aaaa11"), ("0bbb22"[-5:], "931e", "bbbb22")])
        for out in (r.stdout, self.b.cli("keys").stdout, self.b.cli("status").stdout,
                    (self.b.root / "status.json").read_text()):
            self.assertNotIn("nsec1", out)
        self.assertFalse((self.b.root / "heartbeat.json").exists(), "有金鑰也還沒有中繼站,不准有心跳")


class Dispatch(Base):
    def test_c9_aib_chat(self):
        aib = REPO.parent / "bin" / "aib"
        if not aib.exists():
            self.skipTest("這棵樹沒有 bin/aib")
        r = subprocess.run([sys.executable, str(aib), "chat", "status", "--json"], capture_output=True, text=True,
                           env=self.b.env(), timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(json.loads(r.stdout.strip().splitlines()[-1])["enabled"])


class Lang(Base):
    def test_z1_english_and_parity(self):
        outs = []
        for args in (("status",), ("on", "--no-venv", "--name", "Tester", "--country", "TW"), ("status",), ("off",), ("--help",)):
            r = self.b.cli(*args, EZ2_AIB_LOCALE="en")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            outs.append(r.stdout + r.stderr)
        text = "\n".join(outs)
        self.assertIsNone(CJK.search(text), text)
        self.assertIsNone(MISSING.search(text), text)
        dicts = {lg: json.loads((REPO / "i18n" / ("%s.json" % lg)).read_text(encoding="utf-8"))
                 for lg in ("zh-TW", "en", "ja", "ko")}
        keys = {lg: sorted(k for k in d if k.startswith("aib.chat.")) for lg, d in dicts.items()}
        self.assertTrue(keys["zh-TW"])
        for lg in ("en", "ja", "ko"):
            self.assertEqual(keys[lg], keys["zh-TW"], lg)
            for k in keys["zh-TW"]:
                self.assertEqual(sorted(re.findall(r"\{\w+\}", dicts[lg][k])),
                                 sorted(re.findall(r"\{\w+\}", dicts["zh-TW"][k])), "%s %s" % (lg, k))


class Exit(Base):
    SCRIPT = ("import sys,signal\nsys.path.insert(0, sys.argv[1])\nimport chat_bridge as CB\n"
              "if sys.argv[2] == 'boot-run':\n    CB.boot_run = lambda env=None, out=None: 0\n"
              "rc = CB.main([sys.argv[2]])\n"
              "print(rc, signal.getsignal(signal.SIGTERM) is signal.SIG_IGN, signal.getsignal(signal.SIGINT) is signal.SIG_IGN)\n")

    def test_c10_exit_ignores_late_sigterm(self):
        for cmd in ("run", "boot-run"):
            r = subprocess.run([sys.executable, "-c", self.SCRIPT, str(COCKPIT), cmd], capture_output=True, text=True,
                               env=self.b.env(), timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(r.stdout.strip().splitlines()[-1], "0 True True", cmd + ":結束後 SIGTERM / SIGINT 要是 SIG_IGN")


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_bridge.py", needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_keep_stale_heartbeat(self):
        self._knife('        (Path(root) / "heartbeat.json").unlink()\n', "        pass\n", "Leader.test_c3_leader_no_heartbeat")

    def test_k2_off_without_identity_check(self):
        self._knife("pid_alive(pid) and _pid_is_bridge(pid):", "pid_alive(pid):", "Leader.test_c6_off_never_signals_a_stranger")

    def test_k3_exit_keeps_handler(self):
        self._knife("    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n    signal.signal(signal.SIGINT, signal.SIG_IGN)\n    return rc\n",
                    "    return rc\n", "Exit.test_c10_exit_ignores_late_sigterm")


if __name__ == "__main__":
    unittest.main()
