#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""dm_midturn_hint(流程優化 7a,PostToolUse):回合中途待審 ≥2 封 ⇒ 注入一行請席位開整理子代理。

H1 算哪些(純函式):待審頁不等 3 分鐘寬限;進行中頁 read 過、沒設 wait 的也算;設了 wait / 系統讀的 / 自己寄的不算
H2 開關預設關:沒環境變數也沒名單 ⇒ 不提示;AIB_DM_MIDTURN=1 或名單有本席 / `*` ⇒ 開;=0 蓋過名單
H3 真佇列:1 封不提示;2 封提示一次(id、rev、寄件席、預覽都在);同一批不再提示;節流內換批不提示，過了節流才提示
H4 子代理的工具呼叫(payload 帶 agent_id)不提示;佇列有壞檔 ⇒ 不提示
H5 整條(子行程):輸出是 PostToolUse additionalContext 的 JSON、rc 0;守門自己炸掉 ⇒ rc 0、沒輸出
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401

ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
sys.path.insert(0, str(ROOT / "src" / "policy" / "hooks"))
import dm_queue as Q  # noqa: E402
import dm_midturn_hint as H  # noqa: E402

HOOK = ROOT / "src" / "policy" / "hooks" / "dm_midturn_hint.py"
SEAT = "abcde"
NOW = 1_800_000_000


def row(**kw):
    r = {"id": "q_1", "rev": 1, "status": "delivered", "ts": NOW - 10, "sender_seat": "fffff",
         "sender_kind": "seat", "expired_now": False, "msg": "hello", "when": "11-09 06:00:00"}
    r.update(kw)
    return r


class Rows(unittest.TestCase):
    def ids(self, waiting=(), read=()):
        tabs = {"waiting": {"rows": list(waiting)}, "read": {"rows": list(read)}}
        return [r["id"] for r in H.open_rows(tabs, SEAT, NOW)]

    def test_H1_no_grace_and_in_progress_without_wait(self):
        self.assertEqual(self.ids([row(ts=NOW - 1)]), ["q_1"])
        self.assertEqual(self.ids(read=[row(id="q_r", status="read")]), ["q_r"])
        self.assertEqual(self.ids([row(id="q_b", ts=NOW - 5)], [row(id="q_a", ts=NOW - 50, status="read")]),
                         ["q_a", "q_b"])

    def test_H1_skips(self):
        self.assertEqual(self.ids(read=[row(status="read", wait={"text": "等 CI", "at": NOW})]), [])
        self.assertEqual(self.ids(read=[row(status="read", read_by="system")]), [])
        self.assertEqual(self.ids(read=[row(status="read", sender_seat=SEAT)]), [])
        self.assertEqual(self.ids([row(sender_seat=SEAT)]), [])
        self.assertEqual(self.ids([row(status="hold_human", sender_kind="human")]), [])
        self.assertEqual(self.ids([row(expired_now=True)]), [])

    def test_H1_batch_key_depends_on_id_and_rev_not_order(self):
        a, b = row(id="q_a"), row(id="q_b")
        self.assertEqual(H.batch_key([a, b]), H.batch_key([b, a]))
        self.assertNotEqual(H.batch_key([a, b]), H.batch_key([a, row(id="q_b", rev=2)]))


class Live(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.now_dir = os.path.join(self.root, "now")
        os.makedirs(self.now_dir)
        self.env = dict(os.environ, EZ2_AIB_STATE_ROOT=self.root, AIB_LOCALE="zh-TW", AIB_DM_MIDTURN="1")

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, msg="請看這個", sender="fffff", ago=10, base=NOW):
        it = Q.enqueue(SEAT, msg, "@%s" % sender, sender_kind="seat", sender_seat=sender, env=self.env,
                       now=base - ago, status="queued")
        return it["id"] if isinstance(it, dict) else it

    def decide(self, now=NOW, payload=None, env=None):
        return H.decide(SEAT, now, env or self.env, self.now_dir, payload)

    def test_H2_off_by_default_and_switches(self):
        self.put(), self.put(sender="ggggg")
        env = dict(self.env)
        env.pop("AIB_DM_MIDTURN")
        self.assertEqual(self.decide(env=env), "")
        pathlib.Path(self.now_dir, H.SEATS_FILE).write_text("zzzzz\n%s  # 試用\n" % SEAT)
        self.assertNotEqual(self.decide(env=env), "")
        pathlib.Path(self.now_dir, H.SEATS_FILE).write_text("*\n")
        self.assertTrue(H.enabled(SEAT, env, self.now_dir))
        env["AIB_DM_MIDTURN"] = "0"
        self.assertFalse(H.enabled(SEAT, env, self.now_dir))

    def test_H3_two_hint_once_then_throttle(self):
        a = self.put(msg="第一封 " + "很長" * 80)
        self.assertEqual(self.decide(), "", "one is not enough")
        b = self.put(sender="ggggg", msg="第二封")
        t = self.decide()
        for s in (a, b, "rev=1", "@fffff", "@ggggg", "第二封", "整理子代理"):
            self.assertIn(s, t)
        self.assertNotIn("很長" * 50, t, "preview is cut")
        self.assertEqual(self.decide(now=NOW + 1), "", "same batch twice")
        c = self.put(sender="hhhhh", msg="第三封")
        self.assertEqual(self.decide(now=NOW + 30), "", "new batch inside the throttle")
        t = self.decide(now=NOW + H.MIN_GAP_S + 1)
        self.assertIn(c, t)
        self.assertEqual(self.decide(now=NOW + 10 * H.MIN_GAP_S), "", "same batch after the throttle")

    def test_H4_subagent_and_broken_queue(self):
        self.put(), self.put(sender="ggggg")
        self.assertEqual(self.decide(payload={"agent_id": "a1"}), "")
        sd = Q.seat_dir(SEAT, self.env)
        bad = None
        for p in sorted(pathlib.Path(sd).rglob("*.json")):
            if p.name.startswith("q_"):
                bad = p
                break
        if bad is None:
            self.skipTest("queue item files not found under seat dir")
        bad.write_text("{not json")
        if not Q.tabs_payload(SEAT, env=self.env, now=NOW).get("errors"):
            self.skipTest("this queue layout does not report a broken item as an error")
        self.assertEqual(self.decide(), "")

    def test_H5_whole_hook(self):
        real = int(time.time())   # the hook reads the real clock
        self.put(base=real), self.put(sender="ggggg", base=real)
        env = dict(self.env, AIB_NOW_GATE_CODE=SEAT)
        # now_dir comes from seat_now under EZ2_AIB_STATE_ROOT; the env switch is on, so it is not read
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_name": "Bash"}), env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.strip(), r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("整理子代理", out["hookSpecificOutput"]["additionalContext"])
        r = subprocess.run([sys.executable, str(HOOK)], input="{}", env=env, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual((r.returncode, r.stdout), (0, ""), "same batch: silent")
        afile = os.path.join(self.root, "afile")
        pathlib.Path(afile).write_text("x")
        bad = dict(env, EZ2_AIB_STATE_ROOT=afile)
        r = subprocess.run([sys.executable, str(HOOK)], input="not json", env=bad, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual((r.returncode, r.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
