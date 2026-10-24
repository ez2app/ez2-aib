#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat(機房散熱監控區)只讀那一半:快照 / 分組 / 歷史 / 當機雷達 / 入口。
全在暫存狀態根 + 假 ps / 假量測 / 暫存當機報告夾跑:不讀真的系統報告、不寫真的 ~/.ez2-aib、不收任何行程。
H1 快照欄位齊全;量不到的格照實 unmeasured;ps 跑不了 ⇒ groups unmeasured(不是空)
H2 分組:席位(主人 = 席碼)、席位起的無頭瀏覽器、審查員暫存、aib test、別的帳號 ⇒ 系統、其他 App;輸出不帶指令列 / 路徑
H3 歷史:10 秒內不重記、只留 360 點、檔案 0600、落在 EZ2_AIB_STATE_ROOT 底下
H4 當機雷達:已知簽名 ⇒ 代碼 + 修在哪版;未知 ⇒ unknown;超過天數的不算;訊息原文與路徑不外流;資料夾不在 ⇒ unmeasured
H5 入口:`aib room heat` 由核心接(外掛蓋過 room 也一樣),`aib room` 其他子指令照舊交給外掛
刀(複本):K1 當機類別帶出訊息原文 ⇒ H4 紅;K2 拿掉入口攔截 ⇒ H5 紅;K3 拿掉名字清洗 ⇒ H4 紅;K4 開檔會阻塞(FIFO 卡住)⇒ H3b 紅
"""
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
os.environ.setdefault("EZ2_AIB_LOCALE", "zh-TW")

ME = os.getuid()
OTHER = 777 if ME != 777 else 778
SEAT_UUID = "11111111-2222-3333-4444-5555555abcde"
# 真機格式:args 那次 + ucomm 那次(程式名只有執行檔名、最多 16 字、可含空白)
ROWS = [
    # pid ppid uid cpu rss ucomm args
    (100, 1, ME, 1.0, 2048, "tmux", "tmux -L aihub-x new"),
    (200, 100, ME, 5.0, 40960, "claude", "claude --resume"),
    (210, 200, ME, 20.0, 81920, "Google Chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --headless --user-data-dir=/var/folders/x/T/shot"),
    (211, 210, ME, 15.0, 61440, "Google Chrome He", "/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer) --type=renderer --headless"),
    (220, 200, ME, 30.0, 10240, "python3", "python3 /private/tmp/claude-501/s/scratchpad/r316/aib/tests/test-x.py"),
    (230, 200, ME, 12.5, 20480, "python3", "python3 /Users/u/wt/aib/bin/aib test tests/test-y.py"),
    (240, 200, ME, 2.0, 4096, "zsh", "zsh -c secret-token-abc"),
    (250, 1, ME, 3.0, 4096, "mdworker_shared", "/System/Library/Frameworks/CoreServices.framework/mdworker_shared -s mdworker"),
    (260, 1, ME, 1.0, 2048, "x", "/opt/honewei-agent/bin/x --serve"),
    (300, 1, 0, 40.0, 30720, "spindump", "/usr/sbin/spindump"),
    (305, 1, 0, 9.0, 2048, "customtool", "/opt/clientname-tool/customtool --sudo"),
    (310, 1, OTHER, 3.0, 1024, "otheruser", "/usr/sbin/otheruser"),
    (400, 1, ME, 8.0, 51200, "Slack", "/Applications/Slack.app/Contents/MacOS/Slack --x=/Users/u/secret"),
]
PS_ARGS = "".join("%6d %5d %5d %5.1f %6d %s\n" % (r[0], r[1], r[2], r[3], r[4], r[6]) for r in ROWS)
PS_UCOMM = "".join("%6d %-16s\n" % (r[0], r[5]) for r in ROWS)


def fake_ps(cmd):
    return PS_UCOMM if any("ucomm" in c for c in cmd) else PS_ARGS


def fake_sampler():
    cells = [
        {"key": "load1", "label": "負載", "value": 13.5, "text": "13.5", "state": "warn"},
        {"key": "mem", "label": "記憶體", "value": 55, "text": "free 55%", "state": "ok"},
        {"key": "swap", "label": "swap", "value": 1962.0, "text": "1962M", "state": "warn"},
        {"key": "cpu_temp", "label": "CPU 溫度", "value": None, "text": "量不到", "state": "unmeasured"},
        {"key": "kernel_task", "label": "熱節流", "value": None, "text": "量不到", "state": "unmeasured"},
    ]
    return cells, 0.12


def owner_of(pid):
    return ("abcde", "%1") if pid == 200 else (None, None)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aib-heat-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": str(self.tmp / "state"), "HOME": str(self.tmp / "home")})
        p.start()
        self.addCleanup(p.stop)
        import room_heat
        self.RH = room_heat

    def snap(self, run=fake_ps, now=None):
        return self.RH.snapshot(sampler=fake_sampler, run=run, owner_of=owner_of, now=now)


class Snapshot(Base):
    def test_h1_fields_and_unmeasured(self):
        s = self.snap(now=1_792_814_400.0)
        for k in ("schema", "t", "level", "cells", "groups", "candidates"):
            self.assertIn(k, s)
        self.assertEqual(s["cells"]["cpu_temp"]["state"], "unmeasured")
        self.assertIsNone(s["cells"]["cpu_temp"]["value"])
        self.assertEqual(s["groups"]["state"], "ok")

        def boom(cmd):
            raise OSError("no ps")
        s2 = self.snap(run=boom, now=1_792_814_500.0)
        self.assertEqual((s2["groups"]["state"], s2["groups"]["detail"], s2["groups"]["items"]), ("unmeasured", "ps_failed", []))

    def test_h2_groups_and_no_cmdline(self):
        s = self.snap(now=1_792_814_400.0)
        got = {(g["group"], g["owner"]): g for g in s["groups"]["items"]}
        self.assertIn(("seat", "abcde"), got)
        self.assertIn(("headless_browser", "abcde"), got)
        self.assertIn(("reviewer", "abcde"), got)
        self.assertIn(("aib_test", "abcde"), got)
        self.assertIn(("system", "spindump"), got)               # root:系統白名單裡的名字才留
        self.assertIn(("system", ""), got)                       # 別的帳號 / root 的其他程式:名字不留
        self.assertIn(("system", "mdworker_shared"), got)        # 自己帳號的系統行程
        self.assertIn(("other_app", "Slack"), got)
        self.assertIn(("other_app", "x"), got)                   # 只有執行檔名,不帶資料夾
        self.assertEqual(got[("headless_browser", "abcde")]["cpu"], 35.0)     # 主行程 + renderer helper
        self.assertEqual(got[("headless_browser", "abcde")]["count"], 2)
        blob = json.dumps(s, ensure_ascii=False)
        for leak in ("secret", "/Users/", "/var/folders", "scratchpad", "--headless", "/Applications/", "token",
                     "honewei", "clientname", "customtool", "otheruser", "/"):
            self.assertNotIn(leak, blob.replace("aib-heat/v1", ""))

    def test_h2b_measure_failure_keeps_groups(self):
        def boom():
            raise RuntimeError("sensor")
        s = self.RH.snapshot(sampler=boom, run=fake_ps, owner_of=owner_of, now=1_792_814_400.0)
        self.assertEqual((s["level"], s["groups"]["state"]), ("unmeasured", "ok"))


class History(Base):
    def test_h3_ring_buffer(self):
        t0 = 1_792_814_400.0
        self.assertTrue(self.snap(now=t0)["history_recorded"])
        self.assertFalse(self.snap(now=t0 + 5)["history_recorded"])     # 10 秒內不重記
        m = {"level": "ok", "cells": {}}
        for i in range(1, 400):
            self.RH.record_history(m, now=t0 + 10 * i)
        pts = self.RH.read_history()
        self.assertEqual(len(pts), self.RH.HISTORY_MAX)
        self.assertEqual(pts[-1]["t"], t0 + 3990)
        p = Path(self.RH.history_path())
        self.assertTrue(str(p).startswith(str(self.tmp / "state")))
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(pts[0]["load1"], None)
        self.assertEqual(self.RH.read_history()[0]["level"], "ok")

    def test_h3b_hostile_history_file(self):
        p = Path(self.RH.history_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        now = 1_792_814_400.0
        p.write_text(json.dumps({"points": [{"t": now - 20, "cmd": "/Users/x/secret --token=abc", "load1": "9",
                                             "level": "\x1b[2J"}, {"t": 1e18}]}), encoding="utf-8")
        pts = self.RH.read_history(now=now)
        self.assertEqual(pts, [{"t": now - 20, "level": None, "temp": None, "load1": None, "mem_free": None, "swap_mb": None}])
        self.assertTrue(self.RH.record_history({"level": "ok", "cells": {}}, now=now))    # 未來的點不會卡住歷史
        p.unlink()
        os.mkfifo(str(p))                                        # 換成 FIFO:不卡住
        t0 = time.monotonic()
        self.assertEqual(self.RH.read_history(now=now), [])
        self.assertLess(time.monotonic() - t0, 2)
        self.assertIn("history_recorded", self.snap(now=now + 30))
        self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode), 0o700)

    def test_h3c_clock_back(self):
        t0 = 1_792_814_400.0
        self.RH.record_history({"level": "ok", "cells": {}}, now=t0)
        self.assertTrue(self.RH.record_history({"level": "ok", "cells": {}}, now=t0 - 3600))
        self.assertEqual([p["t"] for p in self.RH.read_history(now=t0 - 3600)], [t0 - 3600])


def write_ips(d, name, app, proc, typ, sig, msg, mtime, parent="zsh"):
    meta = {"app_name": app, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S.00 +0800", time.localtime(mtime)),
            "bug_type": "309", "name": app}
    body = {"procName": proc, "procPath": "/Users/u/secret/" + proc, "parentProc": parent,
            "exception": {"type": typ, "signal": sig, "message": msg, "codes": "0x1"},
            "threads": [{"frames": [{"symbol": "secret_frame_in_stack"}]}]}
    p = Path(d) / name
    p.write_text(json.dumps(meta) + "\n" + json.dumps(body, indent=2), encoding="utf-8")
    os.utime(p, (mtime, mtime))


class Crashes(Base):
    def test_h4_radar(self):
        d = self.tmp / "reports"
        d.mkdir()
        now = time.time()
        for i in range(3):
            write_ips(d, "Python-%d.ips" % i, "Python", "Python", "EXC_GUARD", "SIGKILL",
                      " DUP on file descriptor 3 (guarded with 0x85)", now - 60 * i, parent="Python")
        write_ips(d, "node-1.ips", "node", "node", "EXC_CRASH", "SIGABRT", "Abort trap at /Users/u/secret/x.js", now - 30)
        write_ips(d, "old-1.ips", "node", "node", "EXC_CRASH", "SIGABRT", "old", now - 10 * 86400)
        (d / "broken.ips").write_text("not json\n{", encoding="utf-8")
        evil_p = d / "evil.ips"                                  # 原始控制字元直接寫進檔(不經 json 跳脫)
        evil_p.write_text(json.dumps({"app_name": "A", "bug_type": "309", "timestamp": "2026-10-24 10:00:00.00 +0800"})
                          + '\n{"procName" : "A\x1b]0;PWN\x07\x1b[2Jevil",\n "parentProc" : "p\x1b[31mq",\n'
                          ' "exception" : {"type":"EXC\x1b[8m","signal":"SIGKILL (Code Signature Invalid)","message":"m"}}',
                          encoding="utf-8")
        os.utime(evil_p, (now - 40, now - 40))
        jet = d / "JetsamEvent-1.ips"
        jet.write_text(json.dumps({"bug_type": "298", "timestamp": "2026"}) + "\n{}", encoding="utf-8")
        os.mkfifo(str(d / "fifo.ips"))                           # FIFO:不卡住
        t0 = time.monotonic()
        r = self.RH.crashes(crash_dir=str(d), now=now)
        self.assertLess(time.monotonic() - t0, 5)
        self.assertEqual(r["state"], "ok")
        by = {c["proc"]: c for c in r["classes"]}
        self.assertEqual((by["Python"]["code"], by["Python"]["count"], by["Python"]["fixed_in"]),
                         ("aib_test_guarded_fd_dup", 3, "0.1.52"))
        self.assertIn("0.1.52", by["Python"]["hint"])
        self.assertEqual((by["node"]["code"], by["node"]["count"]), ("unknown", 1))      # 10 天前那份不算
        evil = next(c for c in r["classes"] if c["type"] == "EXC")          # procName 帶斜線 ⇒ 只剩檔名
        self.assertEqual((evil["type"], evil["signal"]), ("EXC", "SIGKILL"))
        self.assertNotIn("", [c["proc"] for c in r["classes"]])            # Jetsam 之類不算
        for c in r["classes"]:                                             # 每個字串欄位都不准有控制字元
            for k, v in c.items():
                if isinstance(v, str):
                    self.assertNotRegex(v, r"[\x00-\x1f\x7f-\x9f]", (k, v))
        blob = json.dumps(r, ensure_ascii=False)
        for leak in ("secret", "/Users/", "DUP on file", "Abort trap", "msg40", "\x1b", "\x07", "PWN]"):
            self.assertNotIn(leak, blob)
        out = io.StringIO()
        with mock.patch.object(self.RH, "CRASH_DIR", str(d)):
            self.RH.main(["crashes"], out=out)
        self.assertNotRegex(out.getvalue(), r"[\x00-\x08\x0b-\x1f\x7f]")
        self.assertEqual(self.RH.crashes(crash_dir=str(self.tmp / "nope"))["state"], "unmeasured")

    def test_h4b_file_vanishes(self):
        d = self.tmp / "reports2"
        d.mkdir()
        now = time.time()
        write_ips(d, "Python-a.ips", "Python", "Python", "EXC_GUARD", "SIGKILL", " DUP on file descriptor 3", now)
        write_ips(d, "gone.ips", "node", "node", "EXC_CRASH", "SIGABRT", "x", now)
        real = os.lstat

        def flaky(p, *a, **k):
            if str(p).endswith("gone.ips"):
                raise FileNotFoundError(2, "gone", str(p))
            return real(p, *a, **k)
        with mock.patch.object(os, "lstat", flaky):
            r = self.RH.crashes(crash_dir=str(d), now=now)
        self.assertEqual((r["state"], [c["proc"] for c in r["classes"]]), ("ok", ["Python"]))


def _exit():
    raise SystemExit(0)                                # execv 不會回來


class Entry(Base):
    def run_cli(self, argv):
        import importlib.util                          # 照檔案路徑載(刀的複本資料夾不叫 aib,import aib.cli 會載到別棵)
        spec = importlib.util.spec_from_file_location("aib_cli_under_test", str(ROOT / "cli.py"))
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        calls = []
        plugin = types.ModuleType("aib_plugins")
        plugin.find_command = lambda c: (calls.append(c) or ("/nonexistent/room.py", "captain-room", ""))
        plugin.overrides_builtin = lambda p: True
        with mock.patch.dict(sys.modules, {"aib_plugins": plugin}), mock.patch.object(sys, "argv", ["aib"] + argv), \
                mock.patch.object(os, "execv", side_effect=lambda *a: calls.append(("execv", a[1][1:3])) or _exit()), \
                mock.patch.object(self.RH, "main", side_effect=lambda a: calls.append(("heat", list(a))) or 0):
            sys.modules.pop("room_heat", None)
            sys.modules["room_heat"] = self.RH
            try:
                cli.main()
            except SystemExit:
                pass
        return calls

    def test_h5_core_takes_heat(self):
        calls = self.run_cli(["room", "heat", "--json"])
        self.assertEqual(calls, [("heat", ["--json"])])
        calls = self.run_cli(["room", "status"])
        self.assertIn("room", calls)                           # 其他子指令照舊問外掛
        self.assertNotIn(("heat", ["status"]), calls)


class Knives(unittest.TestCase):
    def _knife(self, rel, old, new, case, timeout=300, hang_is_red=False):
        from aib_knife_copy import knife_copy
        from aib_proc_guard import run_guarded
        name = Path(__file__).name
        with knife_copy(ROOT, rel, old, new, tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=timeout)
        if hang_is_red and r.tripped == "timeout":
            return                                     # 這把刀的紅就是卡住
        self.assertIsNone(r.tripped, "刀測自己失控了:%s" % r.tripped)
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_crash_message_leaks(self):
        self._knife("src/cockpit/room_heat.py", '"type": c["type"], "signal": c["signal"], "count": 0, "last": ""})',
                    '"type": c["type"], "signal": c["signal"], "count": 0, "last": "", "msg40": c["msg40"]})',
                    "Crashes.test_h4_radar")

    def test_k3_no_name_scrub(self):
        self._knife("src/cockpit/room_heat.py", 'return NAME_BAD_RE.sub("?", CTRL_RE.sub("", str(s or "")))[:n]',
                    'return str(s or "")[:n]', "Crashes.test_h4_radar")

    def test_k4_blocking_open(self):
        self._knife("src/cockpit/room_heat.py", 'fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))',
                    'fd = os.open(path, os.O_RDONLY)', "History.test_h3b_hostile_history_file", timeout=30,
                    hang_is_red=True)

    def test_k2_no_entry(self):
        self._knife("cli.py", 'CORE_ROOM_SUBCOMMANDS = ("heat",)', 'CORE_ROOM_SUBCOMMANDS = ()',
                    "Entry.test_h5_core_takes_heat")


sys.path.insert(0, str(HERE))

if __name__ == "__main__":
    unittest.main()
