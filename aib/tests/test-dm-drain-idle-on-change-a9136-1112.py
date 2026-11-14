#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""右欄只在佇列有變時起排空器(主機發熱第 2 條,a63ab 11-12 13:38 准)。

11-12 13:2x 實量:右欄每一幀 Popen 一支 `dm_queue.py drain-idle`,佇列空著也照跑,單次 68% CPU。

D1 排空器跑完、沒有一席有信在等 ⇒ 記號寫下 ⇒ 下一幀 drain_needed 為假(不起)
D2 之後排進一封信(rev 變)⇒ drain_needed 為真
D3 有信在等(收件席忙)⇒ 不記號 ⇒ drain_needed 照舊為真(行為不變)
D4 全球記號出現(佇列沒開)⇒ 指紋變 ⇒ drain_needed 為真
D5 佇列沒開、沒全球記號 ⇒ drain_needed 為假(同 drain_wanted)
D6 等鎖沒跑成(drain_once 提早回)⇒ 記號維持原狀(1ced7 #716 N1:不刪別支剛寫的)
D7 右欄起排空器前問的是 drain_needed(讀 pane-right-ai.sh 原文)
D8 記號滿 60 秒 ⇒ 就算佇列沒變也要起(1ced7 #716 M1:叫醒 / 保留整理 / 到期掃描靠時間,不動 rev)
刀(複本):K1 drain_needed 不比指紋 ⇒ D2 紅;K2 有信在等也記號 ⇒ D3 紅;K3 右欄改回 drain_wanted ⇒ D7 紅;
K4 拿掉 60 秒重跑 ⇒ D8 紅;K5 等鎖沒跑成也動記號 ⇒ D6 紅
全程只寫暫存狀態根,不碰真佇列。
"""
import fcntl
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(HERE))
import dm_queue as Q  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

SEAT = "4345c"


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-dic-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name)
        cabin = self.td / "instances" / "20261112-133800-a913"
        cabin.mkdir(parents=True)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.td), "HOME": str(self.td),
                    "EZ2_AIB_INSTANCE_HOME": str(cabin), "EZ2_AIB_INSTANCE": cabin.name}
        self.delivered = []

    def queue_on(self):
        # 佇列開著的艙:開關檔照 admit 讀的位置寫
        orig = Q.admit
        self.addCleanup(setattr, Q, "admit", orig)
        Q.admit = lambda env=None: (True, "test")

    def put(self, text="hi"):
        return Q.enqueue(SEAT, text, "@abb89", sender_kind="seat", sender_seat="abb89", env=self.env)

    def run_idle(self, busy=False):
        """照 drain-idle 那段:先量指紋、跑一輪、再記號。"""
        fp = Q.queue_fingerprint(self.env)
        st = {}
        sent = Q.drain_once(env=self.env, hold_fn=lambda it: (busy, "busy" if busy else "idle"),
                            deliver_fn=lambda it: self.delivered.append(it["id"]) or 0, state=st)
        Q.note_drain_idle(fp, st, env=self.env)
        return sent, st


class Gate(Base):
    def test_d1_idle_queue_marks_and_skips(self):
        self.queue_on()
        self.assertTrue(Q.drain_needed(self.env), "還沒跑過 ⇒ 要起")
        _, st = self.run_idle()
        self.assertTrue(st.get("ran"))
        self.assertFalse(st.get("waiting"))
        self.assertTrue(Q.drain_idle_mark_path(self.env).exists())
        self.assertFalse(Q.drain_needed(self.env), "沒信在等、佇列沒變 ⇒ 不起")

    def test_d2_new_mail_wakes(self):
        self.queue_on()
        self.run_idle()
        self.assertFalse(Q.drain_needed(self.env))
        self.put()
        self.assertTrue(Q.drain_needed(self.env), "排進一封信 ⇒ 要起")
        sent, _ = self.run_idle()
        self.assertEqual(len(sent), 1)
        self.assertTrue(Q.drain_needed(self.env), "剛送出一封(有信在處理)⇒ 不記號,下一幀照起")
        self.run_idle()                                   # 下一拍:沒信在等 ⇒ 記號
        self.assertFalse(Q.drain_needed(self.env))

    def test_d3_waiting_mail_keeps_running(self):
        self.queue_on()
        self.put()
        _, st = self.run_idle(busy=True)
        self.assertTrue(st.get("waiting"))
        self.assertFalse(Q.drain_idle_mark_path(self.env).exists(), "有信在等 ⇒ 不記號")
        self.assertTrue(Q.drain_needed(self.env), "有信在等 ⇒ 每幀照起(行為不變)")

    def test_d4_global_marker_changes_fingerprint(self):
        # 佇列沒開:沒全球記號時 drain_needed 為假;記號出現 ⇒ 為真
        self.assertFalse(Q.drain_needed(self.env))
        Q.note_global_pending(self.env)
        self.assertTrue(Q.drain_needed(self.env))

    def test_d5_queue_off_no_global(self):
        self.assertFalse(Q.drain_wanted(self.env))
        self.assertFalse(Q.drain_needed(self.env))

    def test_d6_lock_busy_leaves_mark_alone(self):
        self.queue_on()
        _, st = self.run_idle()                          # 先有一份別支寫好的記號
        self.assertTrue(st.get("ran"))
        mark = Q.drain_idle_mark_path(self.env)
        before = mark.read_text(encoding="utf-8")
        self.put()                                       # 佇列變了
        root = Q.queue_root(self.env)
        fd = os.open(str(root / "drain.lock"), os.O_CREAT | os.O_RDWR, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        _, st = self.run_idle()
        self.assertFalse(st.get("ran"), "等鎖 ⇒ 沒跑成")
        self.assertTrue(mark.exists(), "沒跑成 ⇒ 不刪別支的記號")
        self.assertEqual(mark.read_text(encoding="utf-8"), before, "沒跑成 ⇒ 記號維持原狀")
        self.assertTrue(Q.drain_needed(self.env), "佇列變了 ⇒ 下一幀照起")

    def test_d8_recheck_after_a_minute(self):
        self.queue_on()
        self.run_idle()
        t0 = int(time.time())
        self.assertFalse(Q.drain_needed(self.env, now=t0), "剛記號、佇列沒變 ⇒ 不起")
        self.assertFalse(Q.drain_needed(self.env, now=t0 + Q.DRAIN_IDLE_RECHECK_S - 2))
        self.assertTrue(Q.drain_needed(self.env, now=t0 + Q.DRAIN_IDLE_RECHECK_S + 1),
                        "滿 60 秒 ⇒ 要起(叫醒 / 保留整理 / 到期掃描)")
        self.assertEqual(Q.DRAIN_IDLE_RECHECK_S, Q.TRIAGE_EVERY_S, "重跑間隔 = 叫醒節流,叫醒不會比改前慢")

    def test_d7_right_pane_asks_drain_needed(self):
        src = (REPO / "src" / "cockpit" / "pane-right-ai.sh").read_text(encoding="utf-8")
        # 不用 assertIn(會把整支幾千行的殼印進錯誤訊息,刀測的子行程輸出因此超長)
        self.assertTrue("if dm_queue.drain_needed():" in src, "右欄起排空器前要問 drain_needed")
        self.assertFalse("if dm_queue.drain_wanted():" in src, "右欄不能再只問 drain_wanted")


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), cell], cwd=str(root),
                            max_procs=60, timeout=200)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_needed_ignores_fingerprint(self):
        self._knife("src/cockpit/dm_queue.py", "    return fp != queue_fingerprint(env)", "    return False",
                    "Gate.test_d2_new_mail_wakes")

    def test_k2_marks_while_waiting(self):
        self._knife("src/cockpit/dm_queue.py", '    if not state.get("waiting"):\n        try:\n            path.parent',
                    '    if True:\n        try:\n            path.parent', "Gate.test_d3_waiting_mail_keeps_running")

    def test_k4_no_recheck(self):
        self._knife("src/cockpit/dm_queue.py",
                    "    if (time.time() if now is None else now) - at >= DRAIN_IDLE_RECHECK_S:\n        return True\n",
                    "", "Gate.test_d8_recheck_after_a_minute")

    def test_k5_touch_mark_when_not_ran(self):
        self._knife("src/cockpit/dm_queue.py", '    if not state.get("ran"):\n        return False\n', "",
                    "Gate.test_d6_lock_busy_leaves_mark_alone")

    def test_k3_right_pane_back_to_wanted(self):
        self._knife("src/cockpit/pane-right-ai.sh", "    if dm_queue.drain_needed():",
                    "    if dm_queue.drain_wanted():", "Gate.test_d7_right_pane_asks_drain_needed")


if __name__ == "__main__":
    unittest.main()
