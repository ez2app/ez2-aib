#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""待審框有別人寄、沒結案的信 ⇒ 回合結束擋下(dm_pending_gate,掛在 aib-now-gate 的 Stop;@557fe 提案 10-30)。

D1 判準(純函式):別席寄、超過 3 分鐘、queued / delivered / 席位寄的 hold_human ⇒ 算;
   自己寄的、3 分鐘內、真人寄的 hold_human、claimed(投遞中、席位結不了)、過期、全球 / IM、量不到時刻 ⇒ 不算
D2 真佇列:1 封 ⇒ 擋、訊息有 id 與 rev;--done 結案後 ⇒ 放行;read 過(進行中頁)⇒ 不歸本支管
D3 2 封以上 ⇒ 訊息多一句「整理子代理」;超過 3 封只列 3 個 id + 「…+N」
D4 訊息不帶內文與 RELAY 路徑、指令走 aib 入口(不靠目前目錄);四語實際字串 7 封 + 進行中 ≤ 520 字(含一行可照貼的結案指令);
   進行中有信 ⇒ 擋下訊息另列進行中封數(不因它而擋)
D5 逃生門 AIB_DM_GATE_REASON ⇒ 放行並留痕;AIB_DM_GATE=0 ⇒ 關
D6 佇列有壞檔(真的壞 JSON)⇒ 放行 + 一句不含路徑的原因 + 留痕;Stop 整條 rc 0、systemMessage 有那句
S1 Stop 整條(子行程):純聊天回合也擋(rc 2、stderr 有 id);stop_hook_active ⇒ rc 0;
   不在要擋的名單 ⇒ rc 0;守門自己炸掉 ⇒ fail-open rc 0 + 留痕
"""
import importlib.util
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
import dm_pending_gate as G  # noqa: E402

GATE = ROOT / "src" / "policy" / "hooks" / "aib-now-gate.py"
_spec = importlib.util.spec_from_file_location("aib_now_gate", GATE)
NG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(NG)

SEAT = "abcde"
NOW = 1_800_000_000


def row(**kw):
    r = {"id": "q_1", "rev": 1, "status": "delivered", "ts": NOW - 600, "sender_seat": "fffff",
         "sender_kind": "seat", "expired_now": False, "msg": "hello", "when": "10-30 17:00:00"}
    r.update(kw)
    return r


class Filter(unittest.TestCase):
    def ids(self, rows):
        return [r["id"] for r in G.pending_rows(rows, SEAT, NOW)]

    def test_D1_counts(self):
        self.assertEqual(self.ids([row()]), ["q_1"])
        self.assertEqual(self.ids([row(status="queued")]), ["q_1"])
        self.assertEqual(self.ids([row(status="hold_human", sender_kind="seat")]), ["q_1"])
        self.assertEqual(self.ids([row(sender_seat=None, sender_kind="unknown")]), ["q_1"])
        self.assertEqual(self.ids([row(ts=NOW - G.GRACE_S)]), ["q_1"])

    def test_D1_skips(self):
        self.assertEqual(self.ids([row(sender_seat=SEAT)]), [])
        self.assertEqual(self.ids([row(ts=NOW - G.GRACE_S + 1)]), [])
        self.assertEqual(self.ids([row(status="hold_human", sender_kind="human")]), [])
        self.assertEqual(self.ids([row(expired_now=True)]), [])
        self.assertEqual(self.ids([row(**{"global": {"x": 1}})]), [])
        self.assertEqual(self.ids([row(im={"x": 1})]), [])
        self.assertEqual(self.ids([row(ts=None)]), [])
        self.assertEqual(self.ids([row(status="read")]), [])
        self.assertEqual(self.ids([row(status="claimed")]), [])

    def test_D1_order_oldest_first(self):
        got = self.ids([row(id="q_new", ts=NOW - 400), row(id="q_old", ts=NOW - 4000)])
        self.assertEqual(got, ["q_old", "q_new"])


class LiveBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.env = dict(os.environ, EZ2_AIB_STATE_ROOT=self.root, AIB_LOCALE="zh-TW")
        self.env.pop("AIB_DM_GATE", None)
        self.env.pop("AIB_DM_GATE_REASON", None)
        self.notes = []

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, msg="請看這個", sender="fffff", ago=600, status="queued", kind="seat"):
        it = Q.enqueue(SEAT, msg, "@%s" % sender, sender_kind=kind, sender_seat=sender, env=self.env,
                       now=NOW - ago, status=status)
        return it["id"] if isinstance(it, dict) else it

    def check(self, now=NOW):
        return G.check(SEAT, lambda k, **p: "%s %s" % (k, json.dumps(p, ensure_ascii=False)),
                       self.notes.append, now=now, env=self.env)


class Live(LiveBase):
    def test_D2_block_then_done(self):
        qid = self.put()
        block, msg = self.check()
        self.assertTrue(block)
        self.assertIn(qid, msg)
        self.assertIn("(rev 1)", msg)
        self.assertTrue(any("DMGATE BLOCK" in n for n in self.notes))
        ev = os.path.join(self.root, "evidence.txt")
        pathlib.Path(ev).write_text("x")
        Q.read_done(SEAT, qid, "seat:%s" % SEAT, ev, env=self.env, now=NOW, no_rev_check=True)
        self.assertEqual(self.check(), (False, ""))

    def test_D2_read_moves_to_in_progress(self):
        qid = self.put()
        Q.mark_read(SEAT, qid, "seat:%s" % SEAT, env=self.env, now=NOW, no_rev_check=True)
        tabs = Q.tabs_payload(SEAT, env=self.env, now=NOW)["tabs"]
        self.assertIn(qid, [r["id"] for r in tabs["read"]["rows"]])
        self.assertEqual(self.check(), (False, ""))

    def test_D2_fresh_and_self_pass(self):
        self.put(ago=30)
        self.put(sender=SEAT)
        self.assertEqual(self.check(), (False, ""))

    def test_D3_triage_and_cap(self):
        for i in range(7):
            self.put(msg="m%d" % i, ago=600 + i)
        block, msg = self.check()
        self.assertTrue(block)
        self.assertIn("aib.dmgate.triage", msg)
        self.assertIn('"n": 7', msg)
        self.assertIn("…+4", msg)
        self.assertEqual(msg.count("(rev "), G.SHOW_MAX)

    def test_D3_one_no_triage(self):
        self.put()
        self.assertNotIn("aib.dmgate.triage", self.check()[1])

    def test_D4_no_body_no_path(self):
        self.put(msg="長訊已落檔(627 位元組),請讀:/Users/x/.ez2-aib/pending/RELAY-dm-96e0-1030-150933-619.txt 祕密")
        msg = self.check()[1]
        for bad in ("RELAY-", "/Users/", "祕密", "長訊"):
            self.assertNotIn(bad, msg)

    def test_D4_real_strings_short_and_cwd_free(self):
        import i18n_lib
        for i in range(7):
            self.put(msg="m%d" % i, ago=600 + i)
        a = self.put(msg="r")
        Q.mark_read(SEAT, a, "seat:%s" % SEAT, env=self.env, now=NOW, no_rev_check=True)
        for loc in ("zh-TW", "en", "ja", "ko"):
            tr = i18n_lib.Translator(loc)
            block, msg = G.check(SEAT, lambda k, **p: tr.t(k, p or None), self.notes.append, now=NOW, env=self.env)
            self.assertTrue(block, loc)
            self.assertNotIn("aib.dmgate", msg, loc)          # 鍵都有翻到
            self.assertIn("aib dm-queue read --seat %s" % SEAT, msg, loc)
            self.assertIn("aib dm-queue list --seat %s" % SEAT, msg, loc)
            self.assertNotIn("src/cockpit", msg, loc)
            self.assertLessEqual(len(msg), 520, (loc, len(msg)))

    def test_D4_inprog_count_shown_not_gated(self):
        a = self.put(msg="a")
        Q.mark_read(SEAT, a, "seat:%s" % SEAT, env=self.env, now=NOW, no_rev_check=True)
        self.assertEqual(self.check(), (False, ""))
        self.put(msg="b")
        block, msg = self.check()
        self.assertTrue(block)
        self.assertIn("aib.dmgate.inprog", msg)
        self.assertNotIn("aib.dmgate.triage", msg)

    def test_D5_bypass(self):
        self.put()
        self.env["AIB_DM_GATE_REASON"] = "測試"
        self.assertEqual(self.check(), (False, ""))
        self.assertTrue(any("DMGATE BYPASS" in n for n in self.notes))
        self.env.pop("AIB_DM_GATE_REASON")
        self.env["AIB_DM_GATE"] = "0"
        self.assertEqual(self.check(), (False, ""))


class Degraded(LiveBase):
    def bad_file(self):
        d = os.path.join(self.root, "state", "dm-queue", SEAT)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "q_badbadbadbadbad0.json"), "w", encoding="utf-8") as f:
            f.write("{not json")

    def test_D6_bad_json_fails_open_with_reason(self):
        self.put()
        self.bad_file()
        errs = Q.tabs_payload(SEAT, env=self.env, now=NOW).get("errors") or []
        self.assertTrue(errs, "治具沒有做出壞檔")
        block, msg = self.check()
        self.assertFalse(block)
        self.assertIn("aib.dmgate.degraded", msg)
        self.assertNotIn(self.root, msg)
        self.assertTrue(any("DMGATE FAILOPEN" in n for n in self.notes))


class StopHook(unittest.TestCase):
    """aib-now-gate.py stop 整條:純聊天的 transcript ⇒ now 那道不擋,只看待審這道。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.env = dict(os.environ, EZ2_AIB_STATE_ROOT=self.root, AIB_NOW_GATE_CODE=SEAT, AIB_LOCALE="zh-TW")
        for k in ("AIB_DM_GATE", "AIB_DM_GATE_REASON", "AIB_NOW_GATE", "AIB_NOW_GATE_REASON"):
            self.env.pop(k, None)
        self.tp = os.path.join(self.root, "t.jsonl")
        with open(self.tp, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "timestamp": "2026-10-30T09:00:00Z",
                                "message": {"role": "user", "content": "你好"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "嗨"}]}}) + "\n")
        self.gate_list(SEAT)

    def tearDown(self):
        self.tmp.cleanup()

    def gate_list(self, names):
        d = os.path.join(self.root, "state", "seat-now")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, NG.GATE_SEATS), "w", encoding="utf-8") as f:
            f.write(names + "\n")

    def run_stop(self, **payload):
        payload.setdefault("transcript_path", self.tp)
        return subprocess.run([sys.executable, str(GATE), "stop"], input=json.dumps(payload), text=True,
                              capture_output=True, env=self.env, timeout=60)

    def put_old(self):
        it = Q.enqueue(SEAT, "請處理", "@fffff", sender_kind="seat", sender_seat="fffff", env=self.env,
                       now=int(time.time()) - 600)
        return it["id"] if isinstance(it, dict) else it

    def test_S1_blocks_pure_chat(self):
        qid = self.put_old()
        r = self.run_stop()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn(qid, r.stderr)
        self.assertIn("aib-dm-gate", r.stderr)

    def test_S1_empty_passes(self):
        self.assertEqual(self.run_stop().returncode, 0)

    def test_S1_stop_hook_active_passes(self):
        self.put_old()
        self.assertEqual(self.run_stop(stop_hook_active=True).returncode, 0)

    def test_S1_not_listed_passes(self):
        self.put_old()
        self.gate_list("zzzzz")
        self.assertEqual(self.run_stop().returncode, 0)

    def test_S1_degraded_shows_reason(self):
        self.put_old()
        d = os.path.join(self.root, "state", "dm-queue", SEAT)
        with open(os.path.join(d, "q_badbadbadbadbad0.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        r = self.run_stop()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("aib-dm-gate", json.loads(r.stdout.strip().splitlines()[-1])["systemMessage"])
        self.assertNotIn(self.root, r.stdout)

    def test_S1_gate_crash_fails_open(self):
        """守門自己炸掉(讀佇列例外)⇒ 放行 rc 0 並留痕;now 那道照常。"""
        import io
        from unittest import mock
        self.put_old()
        env = {k: v for k, v in self.env.items()}
        payload = json.dumps({"transcript_path": self.tp})
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(G, "check", side_effect=RuntimeError("boom")), \
                mock.patch("sys.stdin", io.StringIO(payload)):
            rc = NG.main(["stop"])
        self.assertEqual(rc, 0)
        log = pathlib.Path(self.root, "logs", "now-gate.log")
        self.assertIn("FAILOPEN dm-gate", log.read_text(encoding="utf-8") if log.exists() else "")

if __name__ == "__main__":
    unittest.main()
