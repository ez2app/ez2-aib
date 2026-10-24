#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat(機房散熱監控區)只讀那一半:快照 / 分組 / 歷史 / 當機雷達 / 入口。
全在暫存狀態根 + 假 ps / 假量測 / 暫存當機報告夾跑:不讀真的系統報告、不寫真的 ~/.ez2-aib、不收任何行程。
H1 快照欄位齊全;量不到的格照實 unmeasured;ps 跑不了 ⇒ groups unmeasured(不是空)
H2 分組:席位(主人 = 席碼)、席位起的無頭瀏覽器、審查員暫存、aib test、別的帳號 ⇒ 系統、其他 App;輸出不帶指令列 / 路徑
H3 歷史:10 秒內不重記、只留 360 點、檔案 0600、落在 EZ2_AIB_STATE_ROOT 底下
H4 當機雷達:已知簽名 ⇒ 代碼 + 修在哪版;未知 ⇒ unknown;超過天數的不算;訊息原文與路徑不外流;資料夾不在 ⇒ unmeasured
H5 入口:`aib room heat` 由核心接(外掛蓋過 room 也一樣),`aib room` 其他子指令照舊交給外掛
刀(複本):K1 當機類別帶出訊息原文 ⇒ H4 紅;K2 拿掉入口攔截 ⇒ H5 紅
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
SEAT_UUID = "11111111-2222-3333-4444-5555555abcde"
PS = "\n".join([
    # pid ppid uid cpu rss comm args
    "  100     1 %d  1.0  2048 /usr/bin/tmux tmux -L aihub-x new" % ME,
    "  200   100 %d  5.0 40960 /opt/bin/claude claude --resume" % ME,
    "  210   200 %d 20.0 81920 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --headless --user-data-dir=/var/folders/x/T/shot" % ME,
    "  220   200 %d 30.0 10240 /usr/bin/python3 python3 /private/tmp/claude-501/s/scratchpad/r316/aib/tests/test-x.py" % ME,
    "  230   200 %d 12.5 20480 /usr/bin/python3 python3 /Users/u/wt/aib/bin/aib test tests/test-y.py" % ME,
    "  240   200 %d  2.0  4096 /bin/zsh zsh -c secret-token-abc" % ME,
    "  300     1    0 40.0 30720 /usr/libexec/spindump spindump",
    "  310     1  501 3.0 1024 /usr/sbin/otheruser otheruser",
    "  400     1 %d  8.0 51200 /Applications/Slack.app/Contents/MacOS/Slack /Applications/Slack.app/Contents/MacOS/Slack --x=/Users/u/secret" % ME,
]) + "\n"
if ME == 501:                                    # 測試機帳號剛好是 501 ⇒ 改用別的「別人」uid
    PS = PS.replace("  501 3.0", "  777 3.0")


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

    def snap(self, run=lambda cmd: PS, now=None):
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
        self.assertIn(("system", "spindump"), got)
        self.assertIn(("system", ""), got)                       # 別的帳號:只算系統,名字也不留
        self.assertIn(("other_app", "Slack"), got)
        self.assertEqual(got[("headless_browser", "abcde")]["cpu"], 20.0)
        blob = json.dumps(s, ensure_ascii=False)
        for leak in ("secret", "/Users/", "/var/folders", "scratchpad", "--headless", "/Applications/", "token"):
            self.assertNotIn(leak, blob)


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
        r = self.RH.crashes(crash_dir=str(d), now=now)
        self.assertEqual(r["state"], "ok")
        by = {c["proc"]: c for c in r["classes"]}
        self.assertEqual((by["Python"]["code"], by["Python"]["count"], by["Python"]["fixed_in"]),
                         ("aib_test_guarded_fd_dup", 3, "0.1.52"))
        self.assertIn("0.1.52", by["Python"]["hint"])
        self.assertEqual((by["node"]["code"], by["node"]["count"]), ("unknown", 1))      # 10 天前那份不算
        blob = json.dumps(r, ensure_ascii=False)
        for leak in ("secret", "/Users/", "DUP on file", "Abort trap", "msg40"):
            self.assertNotIn(leak, blob)
        self.assertEqual(self.RH.crashes(crash_dir=str(self.tmp / "nope"))["state"], "unmeasured")


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
    def _knife(self, rel, old, new, case):
        from aib_knife_copy import knife_copy
        from aib_proc_guard import run_guarded
        name = Path(__file__).name
        with knife_copy(ROOT, rel, old, new, tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s" % r.tripped)
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_crash_message_leaks(self):
        self._knife("src/cockpit/room_heat.py", '"type": c["type"], "signal": c["signal"], "count": 0, "last": ""})',
                    '"type": c["type"], "signal": c["signal"], "count": 0, "last": "", "msg40": c["msg40"]})',
                    "Crashes.test_h4_radar")

    def test_k2_no_entry(self):
        self._knife("cli.py", 'CORE_ROOM_SUBCOMMANDS = ("heat",)', 'CORE_ROOM_SUBCOMMANDS = ()',
                    "Entry.test_h5_core_takes_heat")


sys.path.insert(0, str(HERE))

if __name__ == "__main__":
    unittest.main()
