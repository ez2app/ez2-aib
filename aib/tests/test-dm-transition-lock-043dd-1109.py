#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""dm 狀態轉移整段同鎖(#552 G1,043dd 11-09;a63ab 11:05「要有並發反例 + 刀」)。
全在暫存狀態根 + 暫存 HOME 跑;不送真 dm、不碰 ~/.ez2-aib。

T1 兩個真行程同時結同一封(讀完都先睡 0.3 秒把窗口撐開)⇒ 恰好一個 ok、另一個 transition;
   audit 只有一筆 ok 的 done;跑 3 輪。
T2 同席兩封各自結 ⇒ 兩個都 ok(鎖不擋不相干的信)。
T3 別的行程握著轉移鎖 ⇒ 逾時回 reason=lock_timeout(不丟例外,#552 r1 L3)、audit 記一筆、信不變。
T4 同執行緒重入(鎖內再叫同席 _transition)⇒ 不自鎖死。
T5 排空器 claim_next 跟席位 queued → read 搶同一封(a9136 #552 r1 M1 重現改寫,讀完睡 0.5 秒)⇒
   領信也在轉移鎖裡:領不到(信已是 read),不會出現「領走了、又被蓋回 read」;跑 3 輪。
T6 同 T5,但排空器走 claim_id(真排空器 _drain_seat_once 與「立刻送出」用的那條;a9136 #552 r2 M1')。
T7 hold_item(先寫 hold_rc 再轉 hold)跟席位 queued → read 搶(hold 那邊讀完睡 0.5 秒)⇒
   讀信回 ok 的話已讀一定還在(read_at 沒被舊整封蓋掉;#552 r2 L4);跑 3 輪。
T8 不存在的席:claim_id / claim_result / unclaim / hold_item 不建資料夾(r2 N2)。
刀(複本 + run_guarded):K1 把轉移鎖換成空的 ⇒ T1 紅;K2 claim_next 不拿轉移鎖 ⇒ T5 紅;
K3 claim_id 不拿轉移鎖 ⇒ T6 紅;K4 hold_item 不拿轉移鎖 ⇒ T7 紅。
刀一律斷言「FAIL: <目標>」且沒有 ERROR。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COCKPIT = ROOT / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

import dm_queue as Q  # noqa: E402

SEAT = "aaaaa"
ME = "seat:" + SEAT
NOW = 1792930000

# 子行程:等 go 檔出現才動手;_load_item 讀完睡 0.3 秒(沒鎖時兩邊都會讀到 read)。
CHILD = textwrap.dedent("""
    import json, os, sys, time
    sys.path.insert(0, sys.argv[1])
    import dm_queue as Q
    seat, iid, go, tag = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
    orig = Q._load_item
    def slow(*a, **k):
        it = orig(*a, **k)
        time.sleep(0.3)
        return it
    Q._load_item = slow
    end = time.time() + 20
    while not os.path.exists(go) and time.time() < end:
        time.sleep(0.005)
    r = Q._transition(seat, iid, "done", "seat:" + seat, why="race " + tag)
    print(json.dumps({"ok": r["ok"], "reason": r["reason"]}))
""")

HOLDER = textwrap.dedent("""
    import os, sys, time
    sys.path.insert(0, sys.argv[1])
    import dm_queue as Q
    with Q._seat_transition_lock(sys.argv[2]):
        open(sys.argv[3], "w").close()
        time.sleep(float(sys.argv[4]))
""")


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="aib-dm-tlock-")).resolve()
        self.addCleanup(shutil.rmtree, str(self.d), True)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.d / "state"), "HOME": str(self.d / "home"),
                    "EZ2_AIB_LOCALE": "zh-TW"}
        (self.d / "home").mkdir()
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)

    def read_item(self, msg):
        it = Q.record(SEAT, msg, "agent:offhub", status="delivered", env=self.env, now=NOW, rc=None)
        r = Q._transition(SEAT, it["id"], "read", ME, env=self.env, now=NOW)
        self.assertTrue(r["ok"], r)
        return it["id"]

    def child_env(self):
        e = dict(os.environ)
        e.update(self.env)
        return e


class Race(Base):
    def test_edit_and_close_cannot_overwrite_each_other(self):
        iid = self.read_item("original body")
        rev = Q.item_rev(Q._load_item(SEAT, iid, self.env))
        ready = self.d / "edit-has-read"
        code = textwrap.dedent('''
            import json, sys, time
            sys.path.insert(0, sys.argv[1])
            import dm_queue as Q
            orig = Q._load_item
            calls = [0]
            def pause(*a, **k):
                item = orig(*a, **k)
                calls[0] += 1
                if calls[0] == 2:
                    open(sys.argv[4], "w").close()
                    time.sleep(1)
                return item
            Q._load_item = pause
            result = Q.edit_result(sys.argv[2], sys.argv[3], msg="updated body", if_rev=int(sys.argv[5]), no_rev_check=False)
            print(json.dumps({"ok": result["ok"], "reason": result["reason"]}))
        ''')
        p = subprocess.Popen([sys.executable, "-B", "-c", code, str(COCKPIT), SEAT, iid,
                              str(ready), str(rev)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env=self.child_env())
        self.addCleanup(lambda: p.poll() is None and p.kill())
        deadline = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(ready.exists(), "edit did not reach the read barrier")
        closed = Q._transition(SEAT, iid, "done", ME, env=self.env, why="race",
                               if_rev=rev, no_rev_check=False)
        out, err = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0, err)
        self.assertTrue(json.loads(out.strip().splitlines()[-1])["ok"])
        self.assertFalse(closed["ok"], closed)
        item = Q._load_item(SEAT, iid, self.env)
        self.assertEqual(item["msg"], "updated body")
        self.assertEqual(item["status"], "read")
        self.assertEqual(Q.item_rev(item), rev + 1)

    def test_t1_race_one_winner(self):
        for rnd in range(3):
            iid = self.read_item("通知 第 %d 輪" % rnd)
            go = self.d / ("go-%d" % rnd)
            procs = [subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(COCKPIT), SEAT, iid, str(go), t],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                      env=self.child_env())
                     for t in ("a", "b")]
            time.sleep(0.5)          # 兩個都 import 完、在等 go
            go.touch()
            outs = []
            for p in procs:
                so, se = p.communicate(timeout=60)
                self.assertEqual(p.returncode, 0, se)
                outs.append(json.loads(so.strip().splitlines()[-1]))
            oks = [o for o in outs if o["ok"]]
            self.assertEqual(len(oks), 1, "第 %d 輪:兩個行程都結成功 = 後寫蓋先寫 %r" % (rnd, outs))
            self.assertEqual([o["reason"] for o in outs if not o["ok"]], ["transition"], outs)
            done = [r for r in Q.audit_rows(SEAT, self.env)
                    if r.get("id") == iid and r.get("to") == "done" and r.get("ok")]
            self.assertEqual(len(done), 1, done)
            self.assertEqual(Q._load_item(SEAT, iid, self.env)["status"], "done")

    def test_t2_independent_items_both_ok(self):
        a, b = self.read_item("甲"), self.read_item("乙")
        self.assertTrue(Q._transition(SEAT, a, "done", ME, env=self.env, why="x")["ok"])
        self.assertTrue(Q._transition(SEAT, b, "done", ME, env=self.env, why="y")["ok"])

    def test_t3_held_lock_times_out(self):
        iid = self.read_item("丙")
        ready = self.d / "held"
        p = subprocess.Popen([sys.executable, "-B", "-c", HOLDER, str(COCKPIT), SEAT, str(ready), "3"],
                             env=self.child_env())
        self.addCleanup(p.wait, 30)
        end = time.time() + 20
        while not ready.exists() and time.time() < end:
            time.sleep(0.01)
        self.assertTrue(ready.exists())
        with mock.patch.object(Q, "TRANSITION_LOCK_TIMEOUT_S", 0.2):
            r = Q._transition(SEAT, iid, "done", ME, env=self.env, why="z")
        self.assertEqual((r["ok"], r["reason"]), (False, "lock_timeout"), r)
        self.assertTrue(r["message"])
        self.assertEqual(Q._load_item(SEAT, iid, self.env)["status"], "read")
        last = [x for x in Q.audit_rows(SEAT, self.env) if x.get("id") == iid][-1]
        self.assertEqual((last["ok"], last["reason"]), (False, "lock_timeout"))

    def test_t4_reentrant_same_thread(self):
        iid = self.read_item("丁")
        with mock.patch.object(Q, "TRANSITION_LOCK_TIMEOUT_S", 1.0):
            with Q._seat_transition_lock(SEAT, self.env):
                r = Q._transition(SEAT, iid, "done", ME, env=self.env, why="w")
        self.assertTrue(r["ok"], r)


READER = textwrap.dedent("""
    import json, sys, time
    sys.path.insert(0, sys.argv[1])
    import dm_queue as Q
    seat, iid, mark = sys.argv[2], sys.argv[3], sys.argv[4]
    orig = Q._load_item
    n = [0]
    def slow(*a, **k):
        it = orig(*a, **k)
        if a[1] == iid:
            n[0] += 1
            if n[0] == 2:          # 第 1 次是 rev_check 的,第 2 次是 _transition_locked 要改的那份
                open(mark, "w").close()
                time.sleep(0.5)
        return it
    Q._load_item = slow
    r = Q._transition(seat, iid, "read", "seat:" + seat)
    print(json.dumps({"ok": r["ok"], "reason": r["reason"]}))
""")

CLAIMER = textwrap.dedent("""
    import json, os, sys, time
    sys.path.insert(0, sys.argv[1])
    import dm_queue as Q
    end = time.time() + 20
    while not os.path.exists(sys.argv[3]) and time.time() < end:
        time.sleep(0.005)
    if sys.argv[4] == "id":
        it = Q.claim_id(sys.argv[2], sys.argv[5])
    else:
        it = Q.claim_next(sys.argv[2])
    print(json.dumps({"claimed": (it or {}).get("id")}))
""")

# hold 那邊:第一次讀到這封就放 mark、睡 0.5 秒(撐開「讀 → 寫 hold_rc」的窗口),再寫回、轉 hold。
HOLDER2 = textwrap.dedent("""
    import json, sys, time
    sys.path.insert(0, sys.argv[1])
    import dm_queue as Q
    seat, iid, mark = sys.argv[2], sys.argv[3], sys.argv[4]
    orig = Q._load_item
    n = [0]
    def slow(*a, **k):
        it = orig(*a, **k)
        if a[1] == iid:
            n[0] += 1
            if n[0] == 1:
                open(mark, "w").close()
                time.sleep(0.5)
        return it
    Q._load_item = slow
    it = Q.hold_item(seat, iid, "test", rc=3)
    print(json.dumps({"held": bool(it)}))
""")

READER2 = textwrap.dedent("""
    import json, os, sys, time
    sys.path.insert(0, sys.argv[1])
    import dm_queue as Q
    seat, iid, mark = sys.argv[2], sys.argv[3], sys.argv[4]
    end = time.time() + 20
    while not os.path.exists(mark) and time.time() < end:
        time.sleep(0.005)
    r = Q._transition(seat, iid, "read", "seat:" + seat)
    print(json.dumps({"ok": r["ok"], "reason": r["reason"]}))
""")


class ClaimRace(Base):
    def test_t5_claim_vs_read(self):
        self._claim_vs_read("next")

    def test_t6_claim_id_vs_read(self):
        self._claim_vs_read("id")

    def _claim_vs_read(self, how):
        for rnd in range(3):
            seat = ("cccc%d" if how == "next" else "dddd%d") % rnd
            iid = Q.enqueue(seat, "race %d" % rnd, "@bbbbb", sender_kind="seat", sender_seat="bbbbb",
                            sender_source="pane_pid_chain", env=self.env, now=NOW)["id"]
            mark = str(self.d / ("loaded-%d" % rnd))
            pa = subprocess.Popen([sys.executable, "-B", "-c", READER, str(COCKPIT), seat, iid, mark],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.child_env())
            pb = subprocess.Popen([sys.executable, "-B", "-c", CLAIMER, str(COCKPIT), seat, mark, how, iid],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.child_env())
            (oa, ea), (ob, eb) = pa.communicate(timeout=60), pb.communicate(timeout=60)
            self.assertEqual((pa.returncode, pb.returncode), (0, 0), ea + eb)
            a, b = json.loads(oa.strip().splitlines()[-1]), json.loads(ob.strip().splitlines()[-1])
            final = Q._load_item(seat, iid, self.env)["status"]
            self.assertTrue(a["ok"], a)
            self.assertFalse(b["claimed"] and final == "read",
                             "第 %d 輪:排空器領走了(%s),又被席位讀信蓋回 read" % (rnd, b))
            self.assertIsNone(b["claimed"], "讀信先拿到鎖,排空器應該看到 read、領不到:%r" % b)
            self.assertEqual(final, "read")

    def test_t7_hold_vs_read(self):
        for rnd in range(3):
            seat = "eeee%d" % rnd
            iid = Q.enqueue(seat, "hold race %d" % rnd, "@bbbbb", sender_kind="seat", sender_seat="bbbbb",
                            sender_source="pane_pid_chain", env=self.env, now=NOW)["id"]
            mark = str(self.d / ("hold-%d" % rnd))
            ph = subprocess.Popen([sys.executable, "-B", "-c", HOLDER2, str(COCKPIT), seat, iid, mark],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.child_env())
            pr = subprocess.Popen([sys.executable, "-B", "-c", READER2, str(COCKPIT), seat, iid, mark],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.child_env())
            (oh, eh), (orr, er) = ph.communicate(timeout=60), pr.communicate(timeout=60)
            self.assertEqual((ph.returncode, pr.returncode), (0, 0), eh + er)
            rd = json.loads(orr.strip().splitlines()[-1])
            it = Q._load_item(seat, iid, self.env)
            if rd["ok"]:
                self.assertIsNotNone(it.get("read_at"),
                                     "第 %d 輪:讀信回 ok,可是已讀被 hold_item 的舊整封蓋掉了(%s)" % (rnd, it.get("status")))

    def test_t8_missing_seat_no_dir(self):
        for fn in (lambda: Q.claim_id("zzzzz", "q_0000000000000000", env=self.env),
                   lambda: Q.claim_result("zzzzz", "q_0000000000000000", env=self.env),
                   lambda: Q.unclaim("zzzzz", "q_0000000000000000", env=self.env),
                   lambda: Q.hold_item("zzzzz", "q_0000000000000000", "x", env=self.env)):
            fn()
            self.assertFalse(Q.seat_dir("zzzzz", self.env).exists())


KNIVES = [
    ("K1 拿掉轉移鎖", "src/cockpit/dm_queue.py",
     "        with _seat_transition_lock(seat, env):\n            out = _transition_locked(",
     "        with contextlib.nullcontext():\n            out = _transition_locked(",
     "Race.test_t1_race_one_winner"),
    ("K2 claim_next 不拿轉移鎖", "src/cockpit/dm_queue.py",
     "    with _seat_transition_lock(seat, env):\n        return _claim_next_unlocked(",
     "    with contextlib.nullcontext():\n        return _claim_next_unlocked(",
     "ClaimRace.test_t5_claim_vs_read"),
    ("K3 claim_id 不拿轉移鎖", "src/cockpit/dm_queue.py",
     "    with _seat_transition_lock(seat, env):\n        return _claim_id_unlocked(",
     "    with contextlib.nullcontext():\n        return _claim_id_unlocked(",
     "ClaimRace.test_t6_claim_id_vs_read"),
    ("K4 hold_item 不拿轉移鎖", "src/cockpit/dm_queue.py",
     "    with _seat_transition_lock(seat, env):\n        return _hold_item_unlocked(",
     "    with contextlib.nullcontext():\n        return _hold_item_unlocked(",
     "ClaimRace.test_t7_hold_vs_read"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=180)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
