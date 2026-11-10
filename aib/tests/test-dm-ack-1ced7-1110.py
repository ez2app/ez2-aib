#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""B11 `--kind ack`:單純「收到」寄出就是已讀,不進待審、不觸發 aib-dm-gate(a63ab 11-10 D3 選 A,相容層)。

A1 record(kind=ack, status=read) ⇒ 信檔 status=read、read_by=system、read_why=ack、delivered_at 空;不進 order.json;
   audit 一行 to=read
A2 待審頁沒有它(dm_pending_gate.pending_rows 看不到);「進行中」計數不算它(系統代標);
   過 4 小時 stale_wait / wait_overdue 也不算它(#632 a9136 M1;刀拿掉 _system_read 必紅)
A3 只收一行、≤200 字、不是長訊;status=read 只給 kind=ack,ack 也只能用 read 記 ⇒ 其餘 ValueError,不寫檔
A4 retention_sweep:已讀滿 12 小時 ⇒ 備存(why=ack_12h);不滿 12 小時不動
A5 CLI:dm_queue.py record --kind ack --status read 寫成;字太長 ⇒ rc 2、JSON reason=usage
沒測到(照實寫):dm-ctl.sh 的 ack 分支要真席位解析(T_CODE),這裡沒有端到端跑;它只把同一組參數交給 A5 的 CLI。

行程內呼叫一律帶 env(狀態根在暫存夾),CLI 用子行程 + EZ2_AIB_STATE_ROOT,不碰真的 ~/.ez2-aib。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # = aib/
COCKPIT = ROOT / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(ROOT / "src" / "policy" / "hooks"))
import dm_queue as Q  # noqa: E402
import dm_pending_gate as G  # noqa: E402

SEAT = "aaaaa"
NOW = 1794310000


class Ack(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"EZ2_AIB_STATE_ROOT": self.tmp.name, "HOME": self.tmp.name, "EZ2_AIB_LOCALE": "en",
                    "PATH": os.environ.get("PATH", "")}

    def tearDown(self):
        self.tmp.cleanup()

    def ack(self, msg="收到,開工", now=NOW):
        return Q.record(SEAT, msg, "@bbbbb", status="read", env=self.env, now=now, kind="ack",
                        sender_kind="ai", sender_seat="bbbbb", sender_source="pane_pid_chain")

    def item(self, iid):
        return json.loads((Q.seat_dir(SEAT, self.env) / ("%s.json" % iid)).read_text(encoding="utf-8"))

    def test_a1_written_as_read_by_the_system(self):
        it = self.ack()
        got = self.item(it["id"])
        self.assertEqual((got["status"], got["read_by"], got["read_why"], got["kind"]),
                         ("read", "system", "ack", "ack"))
        self.assertEqual(got["read_at"], NOW)
        self.assertIsNone(got["delivered_at"])
        order = Q.order_path(SEAT, self.env)
        ids = json.loads(order.read_text(encoding="utf-8"))["ids"] if order.exists() else []
        self.assertNotIn(it["id"], ids)
        rows = [r for r in Q.audit_rows(SEAT, self.env) if r.get("id") == it["id"]]
        self.assertEqual([(r["to"], r["ok"]) for r in rows], [("read", True)])

    def test_a2_not_waiting_not_in_progress(self):
        self.ack()
        tabs = Q.tabs_payload(SEAT, env=self.env, now=NOW + 600)
        self.assertEqual(G.pending_rows(tabs["tabs"]["waiting"]["rows"], SEAT, NOW + 600), [])
        inprog = [r for r in tabs["tabs"]["read"]["rows"] if not r.get("wait") and r.get("read_by") != "system"]
        self.assertEqual(inprog, [])
        # #632 a9136 M1: past the 4-hour mark the reminders (badges / aib now / inbox hook / handoff) all read
        # stale_wait / wait_overdue; an ack must not show up in either (RULES §15.17: the message names which).
        later = NOW + 5 * 3600
        b = Q.tabs_payload(SEAT, env=self.env, now=later)["badges"]
        self.assertEqual(b["stale_wait"], 0, "stale_wait counts a system-read ack")
        self.assertEqual(b["wait_overdue"], 0, "wait_overdue counts a system-read ack")
        self.assertEqual(Q.brief_payload(SEAT, env=self.env, now=later)["stale_wait"], 0,
                         "brief stale_wait counts a system-read ack")

    def test_a3_only_a_short_line_and_only_as_read(self):
        relay = "長訊已落檔(9 位元組),請讀:%s/pending/RELAY-dm-x.txt" % self.tmp.name
        # RULES §15.17: each refusal says why (ack = the text, status = the kind / status pair)
        for bad in ("x" * 201, "第一行\n第二行", "  ", relay):
            with self.assertRaisesRegex(ValueError, "^ack$", msg=repr(bad[:20])):
                self.ack(bad)
        with self.assertRaisesRegex(ValueError, "^status$"):
            Q.record(SEAT, "收到", "@bbbbb", status="read", env=self.env, now=NOW, kind="other")
        with self.assertRaisesRegex(ValueError, "^status$"):
            Q.record(SEAT, "收到", "@bbbbb", status="delivered", env=self.env, now=NOW, kind="ack")
        self.assertEqual(Q._all_items(SEAT, env=self.env), [])

    def test_a4_archived_after_twelve_hours(self):
        old = self.ack(now=NOW)
        new = self.ack(now=NOW + 11 * 3600)
        out = Q.retention_sweep(env=self.env, now=NOW + 12 * 3600 + 5, seat=SEAT)
        self.assertEqual([(r["id"], r.get("why"), r.get("ok")) for r in out["archived"]],
                         [(old["id"], "ack_12h", True)])
        self.assertEqual(self.item(old["id"])["status"], "archived")
        self.assertEqual(self.item(old["id"])["archived_why"], "ack_12h")
        self.assertEqual(self.item(new["id"])["status"], "read")

    def test_a5_cli(self):
        env = dict(self.env)
        def run(msg):
            return subprocess.run(
                [sys.executable, "-B", str(COCKPIT / "dm_queue.py"), "record", "--seat", SEAT,
                 "--sender-label", "@bbbbb", "--status", "read", "--kind", "ack"],
                input=msg, capture_output=True, text=True, env=env, timeout=60)
        r = run("收到")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["status"], "read")
        r = run("x" * 300)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(json.loads(r.stdout)["reason"], "usage")


if __name__ == "__main__":
    unittest.main()
