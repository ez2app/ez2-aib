#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""11-08 P2-9(大綱 r2):`aib seat re-register` 量不到 session uuid 時,同一個 pane 行程沿用舊 uuid、清退出欄。

Codex 的 uuid 不在指令列(seat-detect 事後從 rollout 檔量)⇒ 原地 re-register 一律量不到(rc 3)。以前會把好好的
uuid 退成 previous + pending,退出欄也留著(handoff 第 8 項判 gone;bb7dc 11-08 實例)。

抽 aib 真碼出來跑,不開 tmux:
K1 _rereg_keep_uuid:舊 pid = pane pid、舊 uuid 格式對、--session-uuid 沒給或相同 ⇒ 印舊 uuid;
   pid 不同 / 舊 pid 空 / uuid 壞 / 自報不同 ⇒ rc 1(照舊寫 pending,不猜)
K3 respawn 入口(a63ab 11-08 查 bb7dc:pane 重跑整條 LAUNCH_CMD、--session-uuid ''、pid 換了):
   AIB_RESUME_SESSION_UUID = 舊 uuid 且舊 pid 已不在 ⇒ 沿用;resume 不同 / 沒帶 / 舊 pid 還活著 ⇒ 不沿用
K2 seat_reregister 的 rc 3 分支真的走 _rereg_keep_uuid,沿用時 RESET_SESSION_IDENTITY=0
W1 沿用後寫席位檔(write_seat_record 的 Python 段):session_uuid 不變、退出五欄搬進 prior_exits
W2 不沿用(reset)時:uuid 退成 previous + pending、退出欄留在頂層(死席紀錄不被擦掉)—— 舊行為不變
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
COCKPIT = ROOT / "src" / "cockpit"
SRC = (COCKPIT / "aib").read_text(encoding="utf-8")
U = "01a1b5e7-af88-7973-891e-eab974fbb7dc"
U2 = "11111111-2222-3333-4444-555555555555"
EXIT = {"exited_at_epoch": 1794087915, "exit_reason": "cli-exit", "exit_rc": 0,
        "exit_evidence": "provider-launch returned", "exit_recorded_by": "seat_exit.py record"}


def segment(start, end):
    i = SRC.index(start)
    j = SRC.index(end, i) + len(end)
    return SRC[i:j]


KEEP_FN = segment("_rereg_keep_uuid() {\n", "\n}\n")
PYSEAT = segment("<<'PYSEAT' 2>/dev/null || write_rc=$?\n", "\nPYSEAT\n")
PYSEAT = PYSEAT[PYSEAT.index("\n") + 1:-len("PYSEAT\n")]


def dead_pid():
    p = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
    p.wait()
    return str(p.pid)   # 剛收掉的 pid:幾毫秒內不會被重用


def keep(old_pid, pane_pid, old_uuid, expect="", resume=""):
    r = subprocess.run(["bash", "-c", KEEP_FN + '\n_rereg_keep_uuid "$@"', "x", old_pid, pane_pid, old_uuid, expect,
                        resume],
                       capture_output=True, text=True, timeout=10)
    return r.returncode, r.stdout


class KeepRule(unittest.TestCase):
    def test_k1_same_pane_keeps(self):
        self.assertEqual(keep("28123", "28123", U), (0, U))
        self.assertEqual(keep("28123", "28123", U, U), (0, U))

    def test_k1_refuses(self):
        for args, why in ((("28123", "30001", U), "pane 換了(respawn)"),
                          (("", "28123", U), "舊檔沒有 pid"),
                          (("28123", "28123", ""), "舊檔沒有 uuid"),
                          (("28123", "28123", "not-a-uuid"), "uuid 壞"),
                          (("28123", "28123", U, U2), "自報 uuid 跟舊的不同")):
            with self.subTest(why=why):
                self.assertEqual(keep(*args)[0], 1, why)

    def test_k3_respawn_resume_same_session_keeps(self):
        gone = dead_pid()
        self.assertEqual(keep(gone, "30001", U, "", U), (0, U), "respawn + --resume 同一段 ⇒ 沿用")
        self.assertEqual(keep("", "30001", U, "", U), (0, U), "舊檔沒 pid 也看 resume")
        for args, why in (((gone, "30001", U, "", U2), "resume 是別段"),
                          ((gone, "30001", U, "", ""), "沒帶 resume"),
                          ((str(os.getpid()), "30001", U, "", U), "舊行程還活著 ⇒ 兩個行程搶同碼"),
                          ((gone, "30001", U, U2, U), "自報 uuid 跟舊的不同"),
                          ((gone, "30001", "not-a-uuid", "", "not-a-uuid"), "uuid 壞")):
            with self.subTest(why=why):
                self.assertEqual(keep(*args)[0], 1, why)

    def run_branch(self, old_pid, pane_pid, old_uuid, expect="", resume=""):
        """真的跑 seat_reregister 裡 `case "$ev_rc"` 那一段(ev_rc=3),看它設了什麼。"""
        body = segment("seat_reregister() {\n", "\n}\n")
        case = body[body.index('  case "$ev_rc" in'):]
        case = case[:case.index("  esac\n") + len("  esac\n")]
        script = "\n".join([KEEP_FN, 'die() { echo "DIE $*"; exit 9; }', "ev_rc=3", 'ev=""',
                             'old_pid=%s pane_pid=%s old_uuid=%s REREG_UUID=%s kept=""' % (
                                 old_pid or '""', pane_pid, old_uuid or '""', expect or '""'),
                             'AIB_RESUME_SESSION_UUID=%s' % (resume or '""'),
                             case, 'echo "UUID=$SEAT_SESSION_UUID RESET=$RESET_SESSION_IDENTITY KEPT=$kept"'])
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout.strip()

    def test_k2_rc3_branch_keeps_only_same_pane(self):
        self.assertEqual(self.run_branch("28123", "28123", U), "UUID=%s RESET=0 KEPT=%s" % (U, U))
        self.assertEqual(self.run_branch("28123", "30001", U), "UUID= RESET=1 KEPT=")
        self.assertEqual(self.run_branch("28123", "28123", U, U2), "UUID= RESET=1 KEPT=")
        gone = dead_pid()   # a63ab 11-08 的 bb7dc 路徑:respawn、--session-uuid ''、env 帶舊 uuid
        self.assertEqual(self.run_branch(gone, "30001", U, "", U), "UUID=%s RESET=0 KEPT=%s" % (U, U))
        self.assertEqual(self.run_branch(gone, "30001", U, "", U2), "UUID= RESET=1 KEPT=")
        body = segment("seat_reregister() {\n", "\n}\n")
        self.assertIn('old_uuid="$("$T_PY3"', body)

class WriteRecord(unittest.TestCase):
    """跑 write_seat_record 的 Python 段(同一份真碼),看沿用 / 不沿用時席位檔長什麼樣。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.seats = pathlib.Path(self._tmp.name) / "seats"
        self.seats.mkdir()
        rec = {"schema": "aib-seat/v1", "seat_id": "28c22", "started_epoch": 1794071139, "trust": "self_reported",
               "window_id": "@6", "provider": "codex", "mode": "dgrs", "role": "qa", "pid": os.getpid(),
               "session_uuid": U, "session_uuid_source": "lsof:codex-rollout-meta"}
        rec.update(EXIT)
        (self.seats / "w6.json").write_text(json.dumps(rec), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, seat_uuid, reset):
        argv = [str(self.seats), "@6", "qa", "codex", "dgrs", str(os.getpid()), str(COCKPIT), seat_uuid,
                "", "gpt-6.1-terra", reset, "20261106-233709-9320", "aihub-t", ""]
        env = dict(os.environ, AIB_SEAT_REREGISTER="1")
        r = subprocess.run([sys.executable, "-", *argv], input=PYSEAT, capture_output=True, text=True,
                           env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads((self.seats / "w6.json").read_text(encoding="utf-8"))

    def test_w1_kept_uuid_clears_exit_fields(self):
        rec = self.write(U, "0")
        self.assertEqual(rec.get("session_uuid"), U)
        self.assertNotIn("session_uuid_pending", rec)
        for k in EXIT:
            self.assertNotIn(k, rec, "退出欄要搬走,不然 handoff 第 8 項判 gone")
        self.assertEqual(rec["prior_exits"][-1]["exit_reason"], "cli-exit")

    def test_w2_reset_keeps_old_behaviour(self):
        rec = self.write("", "1")
        self.assertNotIn("session_uuid", rec)
        self.assertEqual(rec.get("previous_session_uuid"), U)
        self.assertIn("session_uuid_pending", rec)
        self.assertEqual(rec.get("exit_reason"), "cli-exit", "不沿用時死席紀錄留在頂層")


if __name__ == "__main__":
    unittest.main()
