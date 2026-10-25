#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""待審區一個指令收 / 結 + 等什麼 + 新一輪自動結(Andy 10-25 20:1x 裁示 A / B / C,@abb89 派工,@044e0)。
全在暫存狀態根 + 暫存 HOME 跑(行程內直接叫 dm_queue / dm_take);不送真 dm、不碰 ~/.ez2-aib。

T1 take:已送達 ⇒ 已讀、印全文、記 taken_rev;--wait 記下;長訊印落檔原文;落檔讀不到 ⇒ 不標已讀(relay_unreadable)
T2 close:沒 evidence / why ⇒ 拒;沒讀過的一步讀 + 結(稽核兩筆:read、done)且照樣印全文;
   take 之後信被改過(rev 變)⇒ rev_mismatch;已結的 ⇒ transition
T3 wait / 提醒:只有進行中能寫;別席不准;進行中逾 4 小時沒動、沒寫等什麼 ⇒ stale_wait(列 + badge);
   寫了等什麼或剛動過 ⇒ 不算;待審框那行、aib now、交接事實都帶得到
T4 新一輪自動結:同寄件席(只認量到的)+ 同 PR 較舊一輪 ⇒ 舊信 done(why=superseded_by:<新 id>);
   別張 PR / 舊信還講別張 PR / 別的寄件席 / 舊信 hold_human / 新信 hold_human / 同一輪 / 艙外轉信 / supersedes 不算 各自對;
   系統結案只認 superseded_by: 開頭的理由
T5 CLI:take / close / wait 子指令;第一行 JSON、全文另段;aib dm --closes 是 --close 的別名
T6 派工信不准用回信結(@ee4f8 / @abb89 補):原信 order / pipeline ⇒ close-check 拒(rc 1、closes_order)、
   送達後 _close_original 也不結;問意見 / 通知類照結
刀(複本 + run_guarded):K1 拿掉系統結案理由檢查 ⇒ T4 紅;K2 close 不比 taken_rev ⇒ T2 紅;
K3 落檔讀不到照樣標已讀 ⇒ T1 紅;K4 舊信講別張 PR 也結 ⇒ T4 紅;K5 寫了等什麼仍算逾時 ⇒ T3 紅;
K6 拿掉送達後那道派工檢查 ⇒ T6 紅。
#380 r1(r2 補):M1 不看 supersedes(K13 放回 explicit 路徑 ⇒ T4 紅);M2 reclass 記 kind_orig(K7 ⇒ T6 紅);
M3 take 查本人(K8 ⇒ T1 紅);M4 長訊只認 relay_file_ok(K9 ⇒ T1 紅);M5 dm-ctl 拒 --closes 用 rc 4(T6 字面契約);
L3 沒量到寄件席不自動結(K11 ⇒ T4 紅);L4 等什麼剝控制 / 零寬字元(K12 ⇒ T3 紅);L6 同一輪不結(K10 ⇒ T4 紅)。
刀一律斷言「FAIL: <目標>」且沒有 ERROR。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

import dm_queue as Q  # noqa: E402
import dm_take as TK  # noqa: E402

SEAT = "aaaaa"
ME = "seat:" + SEAT
NOW = 1792930000


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="aib-dm-take-")).resolve()
        self.addCleanup(shutil.rmtree, str(self.d), True)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.d / "state"), "HOME": str(self.d / "home"),
                    "EZ2_AIB_LOCALE": "zh-TW"}
        (self.d / "home").mkdir()
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)

    def rec(self, msg, label="agent:offhub", status="delivered", now=NOW, **kw):
        return Q.record(SEAT, msg, label, status=status, env=self.env, now=now, rc=(3 if status != "delivered" else None), **kw)

    def item(self, iid):
        return Q._load_item(SEAT, iid, self.env)

    def audit_to(self, iid):
        return [r.get("to") for r in Q.audit_rows(SEAT, self.env) if r.get("id") == iid and r.get("ok")]

    def relay(self, text, name="RELAY-dm-x.txt"):
        d = self.d / "home" / ".ez2-aib" / "pending"          # 長訊只認這個目錄裡的 RELAY-dm-*.txt(relay_file_ok)
        d.mkdir(parents=True, exist_ok=True)
        f = d / name
        f.write_text(text, encoding="utf-8")
        return "長訊已落檔(%d 位元組),請讀:%s" % (len(text.encode()), f)


class Take(Base):
    def test_t1_take(self):
        it = self.rec("@abb89:收到,照辦")
        out = TK.take(SEAT, it["id"], ME, wait="等 @74e8b 交頁面", env=self.env, now=NOW + 10)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["text"], "@abb89:收到,照辦")
        cur = self.item(it["id"])
        self.assertEqual(cur["status"], "read")
        self.assertEqual(cur["taken_rev"], Q.item_rev(it))
        self.assertEqual(cur["wait"]["text"], "等 @74e8b 交頁面")
        self.assertIn("close", out["next"])
        self.assertNotIn("wait", out["next"])
        body = "第一行\n第二行 #348 r1 全文"                               # 長訊 ⇒ 印落檔原文
        it2 = self.rec(self.relay(body))
        out = TK.take(SEAT, it2["id"], ME, env=self.env, now=NOW + 20)
        self.assertEqual((out["ok"], out["text"]), (True, body))
        self.assertTrue(out["relay_path"].endswith("RELAY-dm-x.txt"))
        self.assertIn("wait", out["next"])
        it3 = self.rec("長訊已落檔(10 位元組),請讀:%s" % (self.d / "home" / ".ez2-aib" / "pending" / "RELAY-dm-missing.txt"))   # 讀不到 ⇒ 不標已讀
        out = TK.take(SEAT, it3["id"], ME, env=self.env, now=NOW + 30)
        self.assertEqual(out["reason"], "relay_unreadable")
        self.assertEqual(self.item(it3["id"])["status"], "delivered")
        self.assertEqual(TK.take(SEAT, "q_" + "0" * 16, ME, env=self.env)["reason"], "not_found")
        again = TK.take(SEAT, it["id"], ME, env=self.env, now=NOW + 40)        # 已讀再 take:不改狀態、照印
        self.assertEqual((again["ok"], again["status"]), (True, "read"))
        # #380 r1 M3:已讀的信不經 mark_read,take 自己要查是收件席本人(別席 / 亂寫都不能改等什麼、touched_at)
        for who in ("seat:bbbbb", "garbage", "system"):
            bad = TK.take(SEAT, it["id"], who, wait="亂改", env=self.env, now=NOW + 50)
            self.assertEqual(bad["reason"], "actor", who)
        cur = self.item(it["id"])
        self.assertEqual((cur["wait"]["text"], cur["touched_at"]), ("等 @74e8b 交頁面", NOW + 40))
        # #380 r1 M4:指標信寫長訊目錄以外的檔(存在也一樣)⇒ 當讀不到,不讀進對話、不標已讀
        secret = self.d / "secret.txt"
        secret.write_text("本機祕密", encoding="utf-8")
        for p in (secret, self.d / "home" / ".ez2-aib" / "pending" / "note.txt"):
            if not p.exists():
                p.write_text("x", encoding="utf-8")
            it4 = self.rec("長訊已落檔(12 位元組),請讀:%s" % p)
            out = TK.take(SEAT, it4["id"], ME, env=self.env, now=NOW + 60)
            self.assertEqual(out["reason"], "relay_unreadable", p)
            self.assertNotIn("text", out)
            self.assertEqual(TK.close(SEAT, it4["id"], ME, why="x", env=self.env, now=NOW + 61)["reason"], "relay_unreadable")
            self.assertEqual(self.item(it4["id"])["status"], "delivered")


class Close(Base):
    def test_t2_close(self):
        it = self.rec("@abb89:#300 收了")
        self.assertEqual(TK.close(SEAT, it["id"], ME, env=self.env)["reason"], "evidence")
        self.assertEqual(self.item(it["id"])["status"], "delivered")
        out = TK.close(SEAT, it["id"], ME, why="通知,照辦", env=self.env, now=NOW + 5)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["text"], "@abb89:#300 收了")                 # 一步讀 + 結也要印全文
        self.assertEqual(self.item(it["id"])["status"], "done")
        self.assertEqual(self.audit_to(it["id"])[-2:], ["read", "done"])   # 內部仍分兩筆
        self.assertEqual(TK.close(SEAT, it["id"], ME, why="再一次", env=self.env)["reason"], "transition")
        it2 = self.rec("@abb89:請改 A")
        TK.take(SEAT, it2["id"], ME, env=self.env, now=NOW + 10)
        raw = self.item(it2["id"])                                          # 寄件端改了內容(rev+1)
        raw["msg"], raw["rev"] = "@abb89:請改 B", Q.item_rev(raw) + 1
        Q._write_item(SEAT, raw, self.env)
        out = TK.close(SEAT, it2["id"], ME, why="做完", env=self.env, now=NOW + 20)
        self.assertEqual(out["reason"], "rev_mismatch", out)
        self.assertEqual(self.item(it2["id"])["status"], "read")
        TK.take(SEAT, it2["id"], ME, env=self.env, now=NOW + 30)            # 重新 take 看過新內容 ⇒ 可以結
        self.assertTrue(TK.close(SEAT, it2["id"], ME, why="做完", env=self.env, now=NOW + 40)["ok"])
        it3 = self.rec("@abb89:另一件")
        bad = TK.close(SEAT, it3["id"], "seat:bbbbb", why="代結", env=self.env)
        self.assertEqual(bad["reason"], "actor")
        self.assertEqual(self.item(it3["id"])["status"], "delivered")


class Wait(Base):
    def test_t3_wait_and_stale(self):
        it = self.rec("@abb89:#400 要你做 X")
        self.assertEqual(TK.set_wait(SEAT, it["id"], ME, "等 #363", env=self.env)["reason"], "transition")
        TK.take(SEAT, it["id"], ME, env=self.env, now=NOW)
        self.assertEqual(TK.set_wait(SEAT, it["id"], "seat:bbbbb", "x", env=self.env)["reason"], "actor")
        later = NOW + Q.STALE_WAIT_S + 60
        t = Q.tabs_payload(SEAT, env=self.env, now=later)
        row = [r for r in t["tabs"]["read"]["rows"] if r["id"] == it["id"]][0]
        self.assertTrue(row.get("stale_wait"))
        self.assertEqual(t["badges"]["stale_wait"], 1)
        import inbox_brief_hook as H
        self.assertIn("⏳", H.brief_line(SEAT, env=self.env, now=later))
        import seat_now
        self.assertIn("close", seat_now.dm_stale_nudge(SEAT, env=self.env, now=later))
        self.assertTrue(TK.set_wait(SEAT, it["id"], ME, "等 @abb89 合 #363", env=self.env, now=later)["ok"])
        t = Q.tabs_payload(SEAT, env=self.env, now=later + Q.STALE_WAIT_S * 3)   # 寫了等什麼 ⇒ 不再提醒
        row = [r for r in t["tabs"]["read"]["rows"] if r["id"] == it["id"]][0]
        self.assertEqual((row.get("stale_wait"), row["wait"]["text"], t["badges"]["stale_wait"]), (None, "等 @abb89 合 #363", 0))
        self.assertEqual(seat_now.dm_stale_nudge(SEAT, env=self.env, now=later + Q.STALE_WAIT_S * 3), "")
        it2 = self.rec("@abb89:#401")
        TK.take(SEAT, it2["id"], ME, env=self.env, now=NOW)
        self.assertEqual(Q.tabs_payload(SEAT, env=self.env, now=NOW + 60)["badges"]["stale_wait"], 0)   # 剛動過
        TK.set_wait(SEAT, it["id"], ME, "", env=self.env, now=later)       # 清掉 ⇒ 4 小時後又會提醒
        self.assertIsNone(self.item(it["id"])["wait"])
        # #380 r1 L4:控制字元 / 零寬 / 方向控制字元剝掉(會貼進提醒行與交接事實)
        dirty = "\u202e等\u200b @abb89\x07\n回\u2066覆\ufeff\x1b[31m"
        self.assertEqual(TK.set_wait(SEAT, it["id"], ME, dirty, env=self.env, now=later)["wait"], "等 @abb89 回覆 [31m")
        self.assertIsNone(TK.set_wait(SEAT, it["id"], ME, "\u200b\u202e\x00", env=self.env, now=later)["wait"])


class Supersede(Base):
    def frm(self, msg, who="abb89", now=NOW, status="delivered", **kw):
        """量到的寄件席(#380 r1 L3:只有量到的才參與新一輪自動結)。"""
        return self.rec(msg, label="@" + who, status=status, now=now, sender_seat=who,
                        sender_source="pane_pid_chain", **kw)

    def test_t4_new_round_closes_old(self):
        r1 = self.frm("#348 r1 雙審:要修 M1 M2")
        other = self.frm("#345 r1 要修")
        mixed = self.frm("#349 r1 要修;另外 #351 也請看")
        stranger = self.frm("#348 r1 我的意見", who="cfd04")              # 別的寄件席
        held = self.frm("#348 r1 補充", status=Q.HOLD_STATUS)
        same = self.frm("#346 r2 先看這個")
        TK.take(SEAT, r1["id"], ME, env=self.env, now=NOW + 1)
        r2 = self.frm("#348 r2 乾淨", now=NOW + 100)
        old = self.item(r1["id"])
        self.assertEqual(old["status"], "done")
        self.assertEqual(old["done_why"], Q.SUPERSEDE_DONE_PREFIX + r2["id"])
        for keep in (other, stranger):
            self.assertEqual(self.item(keep["id"])["status"], "delivered", keep["msg"])
        self.assertEqual(self.item(held["id"])["status"], Q.HOLD_STATUS)    # hold_human 不在自動範圍
        self.frm("#349 r2 乾淨", now=NOW + 110)                             # 舊信還講 #351 ⇒ 不結
        self.assertEqual(self.item(mixed["id"])["status"], "delivered")
        self.frm("#345 r2 乾淨", status=Q.HOLD_STATUS, now=NOW + 120)        # 新信 hold ⇒ 不觸發
        self.assertEqual(self.item(other["id"])["status"], "delivered")
        self.frm("#346 r2 補一句", now=NOW + 125)                            # #380 r1 L6:同一輪不結(KC2 的尺)
        self.assertEqual(self.item(same["id"])["status"], "delivered")
        # #380 r1 L3:艙外轉信(沒量到寄件席)就算開頭寫 @abb89 也不自動結
        o1 = self.rec("@abb89:#360 r1 要修")
        self.rec("@abb89:#360 r2 乾淨", now=NOW + 130)
        self.assertEqual(self.item(o1["id"])["status"], "delivered")
        # #380 r1 M1:不看 supersedes(upsert 的廣播取代鏈);排隊 / 直送同一條鏈結果一樣 = 都不結
        a = self.frm("沒有輪數的派工", kind="order", thread="abb89/t-1")
        new = self.frm("新版派工", kind="order", thread="abb89/t-1", now=NOW + 140)
        self.assertEqual(self.item(new["id"])["supersedes"], a["id"])       # 直送那條:取代鏈照掛
        raw = dict(self.item(new["id"]))
        self.assertEqual(Q._close_superseded(raw, env=self.env, now=NOW + 141), [])   # 排隊那條(supersedes 先寫好)
        self.assertEqual(self.item(a["id"])["status"], "delivered")
        z = self.frm("隨便一封")
        bad = Q._transition(SEAT, z["id"], "done", "system", env=self.env, why="我想結", if_rev=Q.item_rev(z),
                            no_rev_check=False)
        self.assertEqual(bad["reason"], "actor")                            # 系統結案只認 superseded_by:
        self.assertEqual(self.item(z["id"])["status"], "delivered")


class Cli(Base):
    def test_t5_cli_and_closes_alias(self):
        body = "全文 A\n全文 B"
        it = self.rec(self.relay(body))
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = Q.main(["take", "--seat", SEAT, "--id", it["id"], "--wait", "等 @abb89"], env=self.env)
        self.assertEqual(rc, 0)
        lines = out.getvalue().splitlines()
        head = json.loads(lines[0])
        self.assertEqual((head["ok"], head["status"], head["wait"]), (True, "read", "等 @abb89"))
        self.assertNotIn("text", head)
        self.assertIn("全文 A", out.getvalue())
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = Q.main(["close", "--seat", SEAT, "--id", it["id"]], env=self.env)
        self.assertEqual((rc, json.loads(out.getvalue().splitlines()[0])["reason"]), (9, "evidence"))
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = Q.main(["close", "--seat", SEAT, "--id", it["id"], "--why", "照辦"], env=self.env)
        self.assertEqual(rc, 0, out.getvalue())
        it2 = self.rec("@abb89:x")
        TK.take(SEAT, it2["id"], ME, env=self.env)
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = Q.main(["wait", "--seat", SEAT, "--id", it2["id"], "等 #363 合 main"], env=self.env)
        self.assertEqual((rc, self.item(it2["id"])["wait"]["text"]), (0, "等 #363 合 main"))
        ctl = ROOT / "src" / "cockpit" / "dm-ctl.sh"
        for flag in (["--closes", "bad"], ["--closes=bad"]):               # 別名吃進同一個變數(格式檢查擋下來)
            r = subprocess.run(["bash", str(ctl), "zzzzz", "hi", *flag, "--dry-run"], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=60, env=dict(os.environ, **self.env))
            self.assertIn("--close 要原信的佇列 id", r.stdout + r.stderr, flag)


class ClosesOrder(Base):
    def test_t6_closes_refuses_order(self):
        other = "bbbbb"
        orders = {}
        for kind in ("order", "pipeline", "other", "notice"):
            orig = Q.record(SEAT, "@bbbbb:%s" % kind, "@bbbbb", kind=kind, sender_seat=other,
                            sender_source="pane_pid_chain", status="delivered", env=self.env, now=NOW)
            if kind != "notice":
                TK.take(SEAT, orig["id"], ME, env=self.env, now=NOW)
            orders[kind] = orig["id"]
        for kind, iid in orders.items():
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                rc = Q.main(["close-check", "--seat", SEAT, "--id", iid], env=self.env)
            head = json.loads(out.getvalue())
            refused = kind in ("order", "pipeline")
            self.assertEqual((rc, head["reason"]), (1, "closes_order") if refused else (0, "ok"), kind)
            if refused:
                self.assertIn("close --id %s --evidence" % iid, head["message"])
            reply = Q.record(other, "回 %s" % kind, "@aaaaa", sender_seat=SEAT, sender_source="pane_pid_chain",
                             closes=iid, status="delivered", env=self.env, now=NOW + 5)
            Q._close_original(reply, env=self.env, now=NOW + 5)
            st = self.item(iid)["status"]
            if refused:
                self.assertEqual(st, "read", kind)
            elif kind != "notice":
                self.assertEqual((st, self.item(iid)["done_evidence"]), ("done", reply["id"]), kind)
        # #380 r1 M2:派工信改分類(reclass)後仍是派工:close-check 照拒、回信送達也不結
        orig = Q.record(SEAT, "@bbbbb:再一件派工", "@bbbbb", kind="order", sender_seat=other,
                        sender_source="pane_pid_chain", status="delivered", env=self.env, now=NOW)
        TK.take(SEAT, orig["id"], ME, env=self.env, now=NOW)
        self.assertTrue(Q.reclass(SEAT, orig["id"], "other", ME, env=self.env, now=NOW + 1)["ok"])
        self.assertTrue(Q.reclass(SEAT, orig["id"], "notice", ME, env=self.env, now=NOW + 2)["ok"])   # 改兩次也記得原本
        self.assertEqual(self.item(orig["id"])["kind_orig"], "order")
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = Q.main(["close-check", "--seat", SEAT, "--id", orig["id"]], env=self.env)
        self.assertEqual((rc, json.loads(out.getvalue())["reason"]), (1, "closes_order"))
        reply = Q.record(other, "回", "@aaaaa", sender_seat=SEAT, sender_source="pane_pid_chain",
                         closes=orig["id"], status="delivered", env=self.env, now=NOW + 5)
        Q._close_original(reply, env=self.env, now=NOW + 5)
        self.assertEqual(self.item(orig["id"])["status"], "read")
        # #380 r1 M5:dm-ctl 拒 --closes 用 rc 4(2 = 可重送,--retry-until 會一直重試)
        ctl = (ROOT / "src" / "cockpit" / "dm-ctl.sh").read_text(encoding="utf-8")
        line = [ln for ln in ctl.splitlines() if "*'\"closes_order\"'*)" in ln]
        self.assertEqual(len(line), 1, line)
        self.assertTrue(line[0].rstrip().endswith('" 4 ;;'), line[0])
        with mock.patch("sys.stdout", io.StringIO()):          # 找不到原信 ⇒ 照舊放行
            self.assertEqual(Q.main(["close-check", "--seat", SEAT, "--id", "q_0000000000000000"], env=self.env), 0)


KNIVES = [
    ("K1", "src/cockpit/dm_queue.py",
     "    if to == \"done\" and cls == \"system\" and not (isinstance(why, str) and why.startswith(SUPERSEDE_DONE_PREFIX)):\n",
     "    if False:\n", "Supersede.test_t4_new_round_closes_old"),
    ("K2", "src/cockpit/dm_take.py", "        if taken is not None and int(taken) != cur:\n", "        if False:\n",
     "Close.test_t2_close"),
    ("K3", "src/cockpit/dm_take.py", "    if text is None:\n        return {\"ok\": False, \"reason\": \"relay_unreadable\", \"relay_path\": relay_path,\n"
     "                \"message\": \"長訊落檔讀不到(不存在 / 不是一般檔 / 太大),沒有標已讀\"}\n", "", "Take.test_t1_take"),
    ("K4", "src/cockpit/dm_queue.py", "    return bool(old_rounds) and prs <= set(new_rounds) and \\\n",
     "    return bool(old_rounds) and \\\n", "Supersede.test_t4_new_round_closes_old"),
    ("K5", "src/cockpit/dm_queue.py", "    if isinstance(w, dict) and str(w.get(\"text\") or \"\").strip():\n        return False\n", "",
     "Wait.test_t3_wait_and_stale"),
    ("K6", "src/cockpit/dm_queue.py", "        if is_dispatch(it):\n            return None       # 派工信",
     "        if False:\n            return None       # 派工信", "ClosesOrder.test_t6_closes_refuses_order"),
    # #380 r1 的刀
    ("K7", "src/cockpit/dm_queue.py", "for k in (\"kind\", \"kind_orig\") if item.get(k))",
     "for k in (\"kind\",) if item.get(k))", "ClosesOrder.test_t6_closes_refuses_order"),          # M2
    ("K8", "src/cockpit/dm_take.py", "    if cls != \"seat\" or ident != seat:       # #380 r1 M3",
     "    if False:       # #380 r1 M3", "Take.test_t1_take"),                                      # M3
    ("K9", "src/cockpit/dm_take.py", "    if not DR.relay_file_ok(path, env):\n        return None, path\n", "",
     "Take.test_t1_take"),                                                                         # M4
    ("K10", "src/cockpit/dm_queue.py", "all(pr in new_rounds and r < new_rounds[pr] for",
     "all(pr in new_rounds and r <= new_rounds[pr] for", "Supersede.test_t4_new_round_closes_old"),  # L6 KC2 同輪也結
    ("K11", "src/cockpit/dm_queue.py", "        return (\"seat\", item[\"sender_seat\"])\n    return None\n",
     "        return (\"seat\", item[\"sender_seat\"])\n    return (\"label\", item.get(\"sender_label\"))\n",
     "Supersede.test_t4_new_round_closes_old"),                                                    # L3
    ("K12", "src/cockpit/dm_take.py", "if ord(c) not in _INVISIBLE and unicodedata.category(c) != \"Cf\")",
     ")", "Wait.test_t3_wait_and_stale"),                                                           # L4
    ("K13", "src/cockpit/dm_queue.py", "        if not new_rounds:\n            return []\n",
     "        if not new_rounds:\n            ex = item.get(\"supersedes\")\n"
     "            r = ex and _transition(seat, ex, \"done\", \"system\", env=env, now=now, why=SUPERSEDE_DONE_PREFIX + item[\"id\"])\n"
     "            return [ex] if r and r.get(\"ok\") else []\n", "Supersede.test_t4_new_round_closes_old"),   # M1 舊的 explicit 路徑
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
