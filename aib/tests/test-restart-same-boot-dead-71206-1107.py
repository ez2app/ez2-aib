#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""沒重開機、只是 tmux 被關掉的艙,aib restart 列不出來(11-07 實際發生):
12:59 所有艙一起斷,開機快照每分鐘只記在線的艙 ⇒ 下一次快照把 9320 / 29e7 洗掉,
上一次開機的快照(11-04)只有 f457 ⇒ `--restore 9320` 回「找不到艙」,只能手動照席位檔接回。

修法:socket 還在但 tmux 沒回話的艙(unreachable),從它自己的席位檔重建,列進「這次開機中途被關掉的艙」。

S1 這次開機開的席、退出原因 pid-gone ⇒ 列出來,有對話檔的走同碼復活
S2 自己收掉的席(window-gone / cli-exit / captain-close)不列;整艙都是 ⇒ 整艙不列
S3 席位是上一次開機以前開的(殘留 socket)⇒ 不列
S4 build_view 併進 dead、標 same_boot;上一次開機快照已經有的艙不重複;--restore 找得到
S5 畫面有「這次開機中途被關掉的艙」那一段
S6(#489 r1 Medium)idle_reaper 收艙(execute)前替每席寫 captain-close、已有原因的不蓋;dry-run 不寫;收過的艙不再列
S8(#489 r2 註)收完才寫：殘留還活著的席不寫;sweep 先記的 pid-gone 改成 captain-close
S7(#489 r1 Low)沒記到原因、pid 還活著(tmux 只是卡住)⇒ 不列,免得再開一份
刀(複本):K1 拿掉退出原因過濾 ⇒ S2 紅;K2 拿掉開機時間過濾 ⇒ S3 紅;K3 build_view 不併 ⇒ S4 紅;
           K4 execute 不留痕 ⇒ S6 紅;K5 拿掉 pid 判活 ⇒ S7 紅;
           K6 留痕不看 pid ⇒ S8 紅;K7 不蓋 sweep 推測的原因 ⇒ S8 紅
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
import cabin_restart as CRS  # noqa: E402
sys.path.insert(0, str(HERE))
from aib_proc_guard import run_guarded  # noqa: E402

BOOT = 1793790074
CWD = "/tmp/aib-same-boot-fixture-cwd"


class Fixture:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="sameboot-")
        self.home = os.path.join(self.root, "home")
        self.cwd = os.path.join(self.root, "work")
        os.makedirs(self.cwd)

    def seat(self, cabin, win, uuid, *, reason="pid-gone", started=BOOT + 3600, provider="claude", role="dev", pid=None):
        d = os.path.join(self.root, "instances", cabin, "seats")
        os.makedirs(d, exist_ok=True)
        rec = {"window_id": "@%d" % win, "provider": provider, "role": role, "mode": "dgrs",
               "cwd": self.cwd, "session_uuid": uuid, "started_epoch": started, "born_at": started,
               "seat_id": "s%d" % win, "exited_at_epoch": started + 100}
        if pid is not None:
            rec["pid"] = pid
        if reason is not None:
            rec["exit_reason"] = reason
        with open(os.path.join(d, "w%d.json" % win), "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        if provider == "claude":
            import re
            pd = os.path.join(self.home, ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", self.cwd))
            os.makedirs(pd, exist_ok=True)
            open(os.path.join(pd, uuid + ".jsonl"), "w").close()

    def dead(self, unreachable, live=()):
        return CRS.same_boot_dead(list(live), unreachable, state_root=self.root, boot=BOOT, home=self.home)

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


U1 = "11111111-1111-4111-8111-111111111111"
U2 = "22222222-2222-4222-8222-222222222222"
U3 = "33333333-3333-4333-8333-333333333333"


class SameBoot(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.close()

    def test_s1_pid_gone_listed_and_revivable(self):
        self.f.seat("20261106-233709-9320", 4, U1, role="root")
        self.f.seat("20261106-233709-9320", 5, U2, reason=None)
        out = self.f.dead(["20261106-233709-9320"])
        self.assertEqual(len(out), 1)
        c = out[0]
        self.assertTrue(c["same_boot"])
        self.assertEqual([s["uuid"] for s in c["seats"]], [U1, U2])
        self.assertEqual({s["plan"] for s in c["seats"]}, {"revive"})
        self.assertEqual(c["seats"][0]["window"], "4")

    def test_s2_self_closed_seats_not_listed(self):
        self.f.seat("20261107-212100-9746", 4, U1, reason="window-gone")
        self.f.seat("20261107-212100-9746", 5, U2, reason="cli-exit")
        self.f.seat("20261107-212100-9746", 6, U3, reason="captain-close")
        self.assertEqual(self.f.dead(["20261107-212100-9746"]), [])
        self.f.seat("20261107-212400-29e7", 4, U1)
        self.f.seat("20261107-212400-29e7", 5, U2, reason="window-gone")
        out = self.f.dead(["20261107-212400-29e7"])
        self.assertEqual([s["uuid"] for s in out[0]["seats"]], [U1])

    def test_s3_seats_from_earlier_boot_not_listed(self):
        self.f.seat("20261101-000000-aaaa", 4, U1, started=BOOT - 86400)
        self.assertEqual(self.f.dead(["20261101-000000-aaaa"]), [])

    def test_s4_build_view_merges_without_dup(self):
        self.f.seat("20261106-233709-9320", 4, U1)
        self.f.seat("20261104-121013-f457", 4, U2)
        prev = {"written_at": BOOT - 10, "cabins": [{"cabin": "20261104-121013-f457", "seats": [
            {"window": "4", "window_id": "@4", "code": "22222", "provider": "claude", "uuid": U2, "cwd": self.f.cwd}]}]}
        orig = (CRS.collect, CRS.previous_boot_snapshot, CRS.snapshot_staleness)
        CRS.collect = lambda **k: {"cabins": [], "unreachable": ["20261104-121013-f457", "20261106-233709-9320"]}
        CRS.previous_boot_snapshot = lambda **k: prev
        CRS.snapshot_staleness = lambda *a, **k: ""
        try:
            view = CRS.build_view(state_root=self.f.root, boot=BOOT, home=self.f.home)
        finally:
            CRS.collect, CRS.previous_boot_snapshot, CRS.snapshot_staleness = orig
        ids = [c["cabin"] for c in view["dead"]]
        self.assertEqual(ids, ["20261104-121013-f457", "20261106-233709-9320"])
        self.assertFalse(view["dead"][0].get("same_boot"))
        self.assertTrue(view["dead"][1].get("same_boot"))
        self.assertEqual(view["unreachable"], [])
        self.assertEqual([c["cabin"] for c in CRS._find(view, "9320")], ["20261106-233709-9320"])

    def test_s5_render_has_same_boot_section(self):
        self.f.seat("20261106-233709-9320", 4, U1)
        dead = self.f.dead(["20261106-233709-9320"])
        for c in dead:
            c["dead"] = True
        txt = CRS.render({"cabins": [], "dead": dead, "unreachable": [], "snapshot_note": "", "stale_note": ""})
        self.assertIn("這次開機中途被關掉的艙 1 個", txt)
        self.assertIn("aib restart --restore 9320", txt)
        self.assertNotIn("上次開機時還在", txt)


    def test_s6_reaper_marks_seats_closed(self):
        import idle_reaper as IR
        cab = "20261107-120000-beef"
        self.f.seat(cab, 4, U1, reason=None)
        self.f.seat(cab, 5, U2, reason="cli-exit")
        orig = (IR.AIB_MAIN_HOME, IR.save_pane_tails)
        IR.AIB_MAIN_HOME, IR.save_pane_tails = self.f.root, (lambda *a, **k: None)
        tg = {"kind": "inst", "instance": cab, "id": "inst:" + cab, "steps": [], "kill_pids": [], "seats": []}
        try:
            IR.execute(dict(tg), dry_run=True, grace_s=0)
            rec4 = json.load(open(os.path.join(self.f.root, "instances", cab, "seats", "w4.json")))
            self.assertNotIn("exit_reason", rec4, "dry-run 不寫")
            IR.execute(dict(tg), dry_run=False, grace_s=0)
        finally:
            IR.AIB_MAIN_HOME, IR.save_pane_tails = orig
        d = os.path.join(self.f.root, "instances", cab, "seats")
        self.assertEqual(json.load(open(os.path.join(d, "w4.json")))["exit_reason"], "captain-close")
        self.assertEqual(json.load(open(os.path.join(d, "w5.json")))["exit_reason"], "cli-exit")
        self.assertEqual(self.f.dead([cab]), [])

    def test_s8_mark_after_kill_skips_survivors(self):
        import idle_reaper as IR
        cab = "20261107-120000-dead"
        self.f.seat(cab, 4, U1, reason=None, pid=os.getpid())         # 收艙失敗、還活著
        self.f.seat(cab, 5, U2, reason="pid-gone", pid=2 ** 22 + 777)   # 別艙 sweep 先記了
        orig = (IR.AIB_MAIN_HOME, IR.save_pane_tails)
        IR.AIB_MAIN_HOME, IR.save_pane_tails = self.f.root, (lambda *a, **k: None)
        try:
            IR.execute({"kind": "inst", "instance": cab, "id": "inst:" + cab, "steps": [], "kill_pids": [],
                        "seats": []}, dry_run=False, grace_s=0)
        finally:
            IR.AIB_MAIN_HOME, IR.save_pane_tails = orig
        d = os.path.join(self.f.root, "instances", cab, "seats")
        self.assertNotIn("exit_reason", json.load(open(os.path.join(d, "w4.json"))))
        self.assertEqual(json.load(open(os.path.join(d, "w5.json")))["exit_reason"], "captain-close")

    def test_s7_no_reason_but_pid_alive_not_listed(self):
        cab = "20261107-120000-cafe"
        self.f.seat(cab, 4, U1, reason=None, pid=os.getpid())
        self.f.seat(cab, 5, U2, reason=None, pid=2 ** 22 + 12345)
        out = self.f.dead([cab])
        self.assertEqual([s["uuid"] for s in out[0]["seats"]], [U2])


class Knives(unittest.TestCase):
    def _knife(self, old, new, test):
        self._knife_file("cabin_restart.py", old, new, test)

    def _knife_file(self, name, old, new, test):
        d = tempfile.mkdtemp(prefix="sameboot-knife-")
        try:
            shutil.copytree(COCKPIT, os.path.join(d, "src", "cockpit"), ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(HERE, os.path.join(d, "tests"), ignore=shutil.ignore_patterns("__pycache__"))
            p = os.path.join(d, "src", "cockpit", name)
            s = open(p, encoding="utf-8").read()
            self.assertIn(old, s)
            open(p, "w", encoding="utf-8").write(s.replace(old, new, 1))
            env = dict(os.environ, EZ2_AIB_STATE_ROOT=d, PYTHONDONTWRITEBYTECODE="1")
            r = run_guarded([sys.executable, "-B", os.path.join(d, "tests", os.path.basename(__file__)), test], timeout=60, env=env)
            self.assertIsNone(r.tripped, "看門狗動手:" + str(r.tripped))
            self.assertNotEqual(r.returncode, 0, "刀沒咬:" + test)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_k1_no_reason_filter(self):
        self._knife('if (rec.get("exit_reason") or "") not in SAME_BOOT_DEAD_REASONS:', "if False:",
                    "SameBoot.test_s2_self_closed_seats_not_listed")

    def test_k2_no_boot_filter(self):
        self._knife('if (rec.get("started_epoch") or 0) < int(boot) - SNAPSHOT_BOOT_JITTER_S:', "if False:",
                    "SameBoot.test_s3_seats_from_earlier_boot_not_listed")

    def test_k3_build_view_not_merged(self):
        self._knife("    dead += same_boot_dead(", "    dead += [] and same_boot_dead(",
                    "SameBoot.test_s4_build_view_merges_without_dup")

    def test_k4_reaper_no_mark(self):
        self._knife_file("idle_reaper.py", "    if not dry_run:\n        mark_seats_closed(target)\n", "",
                         "SameBoot.test_s6_reaper_marks_seats_closed")

    def test_k5_no_pid_check(self):
        self._knife('if not rec.get("exit_reason") and _pid_alive(rec.get("pid")):', "if False:",
                    "SameBoot.test_s7_no_reason_but_pid_alive_not_listed")

    def test_k6_mark_ignores_pid(self):
        self._knife_file("idle_reaper.py", "if isinstance(pid, int) and pid > 1 and alive(pid):", "if False:",
                         "SameBoot.test_s8_mark_after_kill_skips_survivors")

    def test_k7_no_overwrite_sweep_reason(self):
        self._knife_file("idle_reaper.py", 'if rec.get("exit_reason") and rec.get("exit_reason") not in seat_exit.SWEEP_REASONS:',
                         'if rec.get("exit_reason"):', "SameBoot.test_s8_mark_after_kill_skips_survivors")


if __name__ == "__main__":
    unittest.main()
