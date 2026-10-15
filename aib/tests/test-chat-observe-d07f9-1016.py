#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球對談觀測(開發文件第 20.1、20.5 節;第 1 期 1.9):稽核行、工人狀態、時鐘差、離線。

全部只連本機:假中繼站 + 本機 HTTP 小伺服器(回 Date 標頭當中繼站主機的時間)。
O1 寄一則 ⇒ 稽核 out relayed、對方 in inbox;稽核行不含內容;aib chat status 印今日寄出 / 收到
O2 工人狀態:中繼站 1/1 連線、待寄 0
O3 時鐘:主機時間比本機慢 10 秒 ⇒ 狀態標警告、status 印「請校時」;差 0 秒 ⇒ 正常
O4 中繼站全斷超過門檻 ⇒ 狀態 offline、status 印「全球對談離線」
刀(複本):K1 時鐘不警告 ⇒ O3 紅;K2 永遠不離線 ⇒ O4 紅
"""
import email.utils
import http.server
import importlib.util
import json
import sys
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import dm_global_files as GF  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_recv_t2", str(HERE / "test-chat-recv-d07f9-1016.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)
SB = RT.SB


class _DateServer(object):
    """回 HEAD / 的 Date 標頭 = 現在 + skew 秒。只綁 127.0.0.1。"""

    def __init__(self, skew):
        skew_s = skew

        class H(http.server.BaseHTTPRequestHandler):
            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def date_time_string(self, timestamp=None):
                return email.utils.formatdate(time.time() + skew_s, usegmt=True)

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Observe(unittest.TestCase):
    setUp = RT.Network.setUp
    _box = RT.Network._box
    items = RT.Network.items
    final = RT.Network.final

    def worker(self, b):
        try:
            return json.loads((b.root / "worker-status.json").read_text())
        except (OSError, ValueError):
            return {}

    def boot(self, skew=0.0, **extra):
        self.ds = _DateServer(skew)
        self.addCleanup(self.ds.close)
        self.box_extra = dict({"AIB_CHAT_CLOCK_URL": self.ds.url, "AIB_CHAT_CLOCK_EVERY": "1"}, **extra)
        return self._box("Andy", [("00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席"),
                                  ("00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")], [True, True])

    def test_o1_o2_audit_and_status(self):
        a, (x, y) = self.boot()
        rec = SB.outbox_rec("%s/6ddf/%s" % (a.owner, y), x, content="SECRET-BODY-TEXT")
        GF.send_and_wait(a.root, rec, wait=20)
        self.assertTrue(a.wait_for(lambda: self.worker(a).get("today", {}).get("received") == 1, 30), self.worker(a))
        lines = [json.loads(l) for p in (a.root / "audit").glob("*.jsonl") for l in p.read_text().splitlines()]
        dirs = {(l["dir"], l["result"]) for l in lines if l["msgid"] == rec["msgid"]}
        self.assertIn(("out", "relayed"), dirs)
        self.assertIn(("in", "inbox"), dirs)
        self.assertNotIn("SECRET-BODY-TEXT", json.dumps(lines))
        w = self.worker(a)
        self.assertEqual((w["up"], w["total"], w["pending"], w["today"]["sent"]), (1, 1, 0, 1))
        r = a.cli("status")
        self.assertIn("寄出 1", r.stdout)
        self.assertIn("1/1 連線中", r.stdout)

    def test_o3_clock_warning(self):
        a, _ = self.boot(skew=-10)
        self.assertTrue(a.wait_for(lambda: (self.worker(a).get("clock") or {}).get("offset") is not None, 30))
        c = self.worker(a)["clock"]
        self.assertTrue(c["warn"], c)
        self.assertLess(c["offset"], -8)
        self.assertIn("請校時", a.cli("status").stdout)

    def test_o3b_clock_fine(self):
        a, _ = self.boot(skew=0)
        self.assertTrue(a.wait_for(lambda: (self.worker(a).get("clock") or {}).get("offset") is not None, 30))
        self.assertFalse(self.worker(a)["clock"]["warn"])
        self.assertIn("正常", a.cli("status").stdout)

    def test_o4_offline(self):
        a, _ = self.boot(AIB_CHAT_OFFLINE_AFTER="2")
        self.relay.__exit__(None, None, None)                          # 中繼站全斷
        self.assertTrue(a.wait_for(lambda: self.worker(a).get("offline") is True, 60), self.worker(a))
        self.assertIn("全球對談離線", a.cli("status").stdout)


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_nostr.py", needle, replacement,
                        tests=(name, "test-chat-recv-d07f9-1016.py", "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k1_no_clock_warning(self):
        self._knife('"warn": off is not None and abs(off) >= CLOCK_WARN_S}', '"warn": False}', "Observe.test_o3_clock_warning")

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_never_offline(self):
        self._knife('    offline = up == 0 and now - state.get("last_up", state["started"]) >= OFFLINE_AFTER',
                    '    offline = False', "Observe.test_o4_offline")


if __name__ == "__main__":
    unittest.main()
