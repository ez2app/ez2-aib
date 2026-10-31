#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""重開機恢復挑到舊快照(10-31 實際發生):同一次開機的開機時間抖 1 秒,快照寫成兩個檔
boot-1793076279.json(21:02 最後一份,只剩 5 席)與 boot-1793076280.json(10-30 20:15,20 席),
previous_boot_snapshot 照檔名數字挑大的 ⇒ restore-all 把已退席的 20 席全接回來。

J1 上一次開機分成兩個檔(差 1 秒):挑 written_at 新的那份,不是檔名數字大的
J2 這次開機的抖動檔(比現在的開機時間早 1 秒)不算「上一次」
J3 write_snapshot:這次開機已有抖動檔 ⇒ 寫回同一個檔,不另開
J4 兩次真的不同的開機(差一天):照舊挑最近那次
J5 群組裡最新的那份壞掉 ⇒ 退回同一次開機的另一份
J6 (#464 r1 M1)writer 在鎖裡才選檔:別支 writer 拿著鎖時寫出 boot-3000,放鎖後這支(boot=3001)寫回 boot-3000,不另開
J7 (#464 r1 M2)過期檢查也認抖動檔:只有剛寫的 boot-2999、現在 boot=3000 ⇒ 不警告
J8 (#464 r1 M3)written_at = 1e400 / NaN / 布林 ⇒ 當壞時間,不拋例外、退回同組有效檔
J9 (#464 r2)別支一直拿著鎖 ⇒ writer 等滿上限就回 None(這輪不寫),不卡住呼叫端
刀(複本):K1 previous_boot_snapshot 改回照檔名挑 ⇒ J1 紅;K2 write_snapshot 改回 _snap_path ⇒ J3 紅;
           K3 拿掉鎖 ⇒ J6 紅;K4 過期檢查改回精確檔名 ⇒ J7 紅;K5 改回阻塞等鎖 ⇒ J9 紅
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
import cabin_restart as CRS  # noqa: E402
sys.path.insert(0, str(HERE))
from aib_proc_guard import run_guarded  # noqa: E402

B_OLD = 1793076279
B_NOW = 1793452064


def _put(sdir, boot, written_at, codes):
    os.makedirs(sdir, exist_ok=True)
    with open(os.path.join(sdir, "boot-%d.json" % boot), "w", encoding="utf-8") as fh:
        json.dump({"schema": 1, "boot": boot, "written_at": written_at,
                   "cabins": [{"cabin": "20261028-153924-96e0", "seats": [{"code": c} for c in codes]}]}, fh)


def _codes(snap):
    return [s["code"] for c in snap["cabins"] for s in c["seats"]]


class Jitter(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="snapjit-")
        os.environ["EZ2_AIB_STATE_ROOT"] = self.root
        self.sdir = CRS.snapshot_dir(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_j1_split_previous_boot_picks_latest_written(self):
        _put(self.sdir, B_OLD + 1, 1793076900, ["b5799", "aeb40", "fd787"])   # 10-30 20:15 那份,檔名數字大
        _put(self.sdir, B_OLD, 1793451751, ["f5fb8"])                        # 10-31 21:02 最後一份
        snap = CRS.previous_boot_snapshot(state_root=self.root, boot=B_NOW)
        self.assertEqual(_codes(snap), ["f5fb8"])

    def test_j2_current_boot_jitter_file_not_previous(self):
        _put(self.sdir, B_OLD, 1793451751, ["f5fb8"])
        _put(self.sdir, B_NOW - 1, 1793452500, ["34396"])                    # 這次開機,抖了 1 秒
        snap = CRS.previous_boot_snapshot(state_root=self.root, boot=B_NOW)
        self.assertEqual(_codes(snap), ["f5fb8"])

    def test_j3_write_reuses_same_boot_file(self):
        _put(self.sdir, B_NOW - 1, 1793452500, ["34396"])
        data = {"cabins": [{"cabin": "20261031-211004-559f", "seats": [{"code": "34396"}, {"code": "d5cad"}]}]}
        path = CRS.write_snapshot(state_root=self.root, boot=B_NOW, now=1793453000, data=data, min_interval_s=0)
        self.assertEqual(os.path.basename(path), "boot-%d.json" % (B_NOW - 1))
        self.assertEqual(sorted(x for x in os.listdir(self.sdir) if x.endswith(".json")), ["boot-%d.json" % (B_NOW - 1)])

    def test_j4_distinct_boots_pick_most_recent(self):
        _put(self.sdir, B_OLD - 86400, 1792990000, ["old"])
        _put(self.sdir, B_OLD, 1793451751, ["f5fb8"])
        snap = CRS.previous_boot_snapshot(state_root=self.root, boot=B_NOW)
        self.assertEqual(_codes(snap), ["f5fb8"])

    def test_j5_broken_newest_falls_back_within_group(self):
        _put(self.sdir, B_OLD + 1, 1793076900, ["b5799"])
        _put(self.sdir, B_OLD, 1793451751, ["f5fb8"])
        with open(os.path.join(self.sdir, "boot-%d.json" % B_OLD), "w") as fh:
            fh.write("{broken")
        snap = CRS.previous_boot_snapshot(state_root=self.root, boot=B_NOW)
        self.assertEqual(_codes(snap), ["b5799"])

    def test_j6_writer_selects_inside_lock(self):
        data = {"cabins": [{"cabin": "20261031-211004-559f", "seats": [{"code": "34396"}]}]}
        out = {}
        with CRS._snap_lock(self.sdir):
            t = threading.Thread(target=lambda: out.setdefault("p", CRS.write_snapshot(state_root=self.root, boot=3001, now=5000, data=data, min_interval_s=0)))
            t.start(); time.sleep(0.3)
            self.assertTrue(t.is_alive(), "writer 要等鎖")
            _put(self.sdir, 3000, 4999, ["other"])   # 拿著鎖的那支寫了 boot-3000
        t.join(10)
        self.assertEqual(os.path.basename(out["p"]), "boot-3000.json")
        self.assertEqual(sorted(x for x in os.listdir(self.sdir) if x.endswith(".json")), ["boot-3000.json"])

    def test_j7_staleness_reads_jitter_file(self):
        _put(self.sdir, 2999, 5000, ["34396"])
        self.assertEqual(CRS.snapshot_staleness([{"cabin": "x"}], state_root=self.root, boot=3000, now=5060), "")
        self.assertIn("分鐘沒更新", CRS.snapshot_staleness([{"cabin": "x"}], state_root=self.root, boot=3000, now=5000 + 3600))

    def test_j8_non_finite_written_at(self):
        _put(self.sdir, B_OLD, 1793451751, ["f5fb8"])
        for bad in ("1e400", "NaN", "true"):
            with open(os.path.join(self.sdir, "boot-%d.json" % (B_OLD + 1)), "w") as fh:
                fh.write('{"schema": 1, "written_at": %s, "cabins": [{"cabin": "c", "seats": [{"code": "old"}]}]}' % bad)
            snap = CRS.previous_boot_snapshot(state_root=self.root, boot=B_NOW)
            self.assertEqual(_codes(snap), ["f5fb8"], bad)

    def test_j9_lock_busy_skips_round(self):
        data = {"cabins": [{"cabin": "20261031-211004-559f", "seats": [{"code": "34396"}]}]}
        out = {}
        old = CRS.SNAPSHOT_LOCK_WAIT_S
        CRS.SNAPSHOT_LOCK_WAIT_S = 0.3
        try:
            with CRS._snap_lock(self.sdir):
                t0 = time.monotonic()
                t = threading.Thread(target=lambda: out.setdefault("p", CRS.write_snapshot(state_root=self.root, boot=3001, now=5000, data=data, min_interval_s=0)), daemon=True)
                t.start(); t.join(3)
                self.assertFalse(t.is_alive(), "writer 不可無限等鎖")
                self.assertLess(time.monotonic() - t0, 2.5)
        finally:
            CRS.SNAPSHOT_LOCK_WAIT_S = old
        self.assertIsNone(out["p"])
        self.assertEqual([x for x in os.listdir(self.sdir) if x.endswith(".json")], [])


class Knives(unittest.TestCase):
    def _knife(self, old, new, test):
        d = tempfile.mkdtemp(prefix="snapjit-knife-")
        try:
            shutil.copytree(COCKPIT, os.path.join(d, "src", "cockpit"))
            shutil.copytree(HERE, os.path.join(d, "tests"), ignore=shutil.ignore_patterns("__pycache__"))
            p = os.path.join(d, "src", "cockpit", "cabin_restart.py")
            s = open(p, encoding="utf-8").read()
            self.assertIn(old, s)
            open(p, "w", encoding="utf-8").write(s.replace(old, new, 1))
            env = dict(os.environ, EZ2_AIB_STATE_ROOT=d, PYTHONDONTWRITEBYTECODE="1")
            r = run_guarded([sys.executable, "-B", os.path.join(d, "tests", os.path.basename(__file__)), test], timeout=60, env=env)
            self.assertIsNone(r.tripped, "看門狗動手:" + str(r.tripped))
            self.assertNotEqual(r.returncode, 0, "刀沒咬:" + test)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_k1_pick_by_filename(self):
        self._knife("order += [p for _w, _b, p in sorted(((_written_at(p), b, p) for b, p in group), reverse=True)]",
                    "order += [p for _b, p in sorted(group, reverse=True)]", "Jitter.test_j1_split_previous_boot_picks_latest_written")

    def test_k2_write_new_file(self):
        self._knife("path = _same_boot_path(sdir, boot)", "path = _snap_path(sdir, boot)",
                    "Jitter.test_j3_write_reuses_same_boot_file")

    def test_k3_no_lock(self):
        self._knife("        with _snap_lock(sdir):\n            return _write_snapshot_locked(", "        with contextlib.nullcontext():\n            return _write_snapshot_locked(",
                    "Jitter.test_j6_writer_selects_inside_lock")

    def test_k4_staleness_exact_name(self):
        self._knife("    cur = _current_boot_path(snapshot_dir(state_root), boot)", "    cur = _snap_path(snapshot_dir(state_root), boot) if os.path.exists(_snap_path(snapshot_dir(state_root), boot)) else None",
                    "Jitter.test_j7_staleness_reads_jitter_file")


    def test_k5_blocking_lock(self):
        self._knife("fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)", "fcntl.flock(fd, fcntl.LOCK_EX)",
                    "Jitter.test_j9_lock_busy_skips_round")


if __name__ == "__main__":
    unittest.main()
