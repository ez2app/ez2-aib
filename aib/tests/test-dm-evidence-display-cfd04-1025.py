#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""待審區優化 D + E(10-25 Andy 裁,@abb89 派 @cfd04;F 拆去 aib-boards 另開)。

D1 證據可以是說明文字:裡面夾 github.com/ez2app 的完整 PR 網址 / 本席寄出的回信 id / 查得到的 commit / 存在的檔 ⇒ 收,
   done_ref 記查證的那一段、done_evidence 留原文;整串剛好是證據照舊(不寫 done_ref)
D2 純文字、別人的網址、「#數字」(不查證,#370 r1 M3)、別席寄的 q_ id、查不到的 commit、不存在的檔 ⇒ 照舊退;有 --why 才憑理由結
   候選超過 _EV_SCAN_MAX 就不再查(先放一堆不存在的路徑,第 5 個才是真檔 ⇒ 退)
E1 tabs 每列帶 preview(長訊讀 RELAY 檔前 200 字、短訊就是本文;控制 / C1 / 方向字元拿掉、換行壓空白);不再有 claimed_sender
   完成 / 備存不讀檔;全球來信不讀
E2 長訊檔限定(#370 r1 M2):指標指到長訊目錄外 / 捷徑指出去 / 檔名不是 RELAY-dm-*.txt / FIFO ⇒ preview None、原話一行 RELAY_UNREADABLE;
   只讀前 2048 位元組
E3 tabs --counts 只印數量:每頁則數、依狀態分開、進行中沒寫「等什麼」的則數
E4 待審框:長訊列印「📄 前 200 字」;row["wait"] 有字 ⇒ 列最前面接「⏳… · 」(L-B1);沒這些鍵照舊
E5 「等什麼」計數與畫面同一支 dm_text.wait_text(r2 L-new3);D3 證據提示寫完整 PR 網址(r2 L-new1)
刀:evidence_ref 不抽說明文字 ⇒ D1 紅;收回「#數字」⇒ D2 紅;preview 不洗控制字元 ⇒ E1 紅;拿掉 relay_file_ok ⇒ E2 紅
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "src" / "cockpit" / "render"))
import dm_queue as Q  # noqa: E402

SEAT = "abcde"
OTHER = "fghij"


def head_sha():
    r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-dmopt-")
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "EZ2_AIB_LOCALE": "zh-TW", "AIB_DM_ADDR": "100.114.152.121"}

    def item(self, text="hi", seat=SEAT, **kw):
        return Q.enqueue(seat, text, kw.pop("label", "寄件席"), env=self.env, **kw)

    def done(self, it, ev=None, why=None):
        Q.mark_read(SEAT, it["id"], "seat:" + SEAT, env=self.env, no_rev_check=True)
        return Q.mark_done(SEAT, it["id"], "seat:" + SEAT, ev, env=self.env, no_rev_check=True, why=why)

    def load(self, it):
        return Q._load_item(SEAT, it["id"], self.env)


class Evidence(Base):
    def test_d1_text_with_evidence(self):
        sent = self.item("回你", seat=OTHER, sender_seat=SEAT, sender_source="pane_pid_chain")
        f = self.tmp / "out.json"
        f.write_text("{}")
        cases = [("PR 開了 https://github.com/ez2app/ez2-aib/pull/366 ,等 CI", "pr", "https://github.com/ez2app/ez2-aib/pull/366"),
                 ("回過了(%s)" % sent["id"], "dm", sent["id"]),
                 ("結果檔在 %s 。" % f, "file", str(f))]
        sha = head_sha()
        if sha:
            cases.append(("已推 %s 到分支" % sha[:9], "commit", sha[:9]))
        for ev, kind, ref in cases:
            it = self.item()
            out = self.done(it, ev)
            self.assertTrue(out.get("ok"), (ev, out))
            got = self.load(it)
            self.assertEqual((got["done_kind"], got.get("done_ref"), got["done_evidence"]), (kind, ref, ev), ev)
        it = self.item()                                  # 整串剛好是證據:照舊,不多寫 done_ref
        self.assertTrue(self.done(it, "https://github.com/ez2app/ez2-aib/pull/1").get("ok"))
        self.assertNotIn("done_ref", self.load(it))

    def test_d2_still_refuse(self):
        other_sent = self.item("別人寄的", seat=OTHER, sender_seat="zzzzz", sender_source="pane_pid_chain")
        f = self.tmp / "real.txt"
        f.write_text("x")
        many = " ".join("/no/such/%d" % i for i in range(Q._EV_SCAN_MAX)) + " " + str(f)
        for ev in ("處理完了", "見 https://github.com/evil/x/pull/1", "回過了 %s" % other_sent["id"], "commit deadbeefdeadbeef 推了",
                   "檔在 /no/such/file.txt", "版本 v2 'x'", "issue#12 不算", "修好了，見 #348", "見第 #1 點", "PR #370", many):
            it = self.item()
            out = self.done(it, ev)
            self.assertEqual((out.get("ok"), out.get("reason")), (False, "evidence"), ev)
        it = self.item()
        self.assertTrue(self.done(it, "處理完了", why="通知類，讀完即結").get("ok"))
        self.assertEqual(self.load(it)["done_kind"], "reason")


class Display(Base):
    def relay(self, text, name="RELAY-dm-6ddf-1025-000000-1.txt", where=None):
        d = where or (self.tmp / ".ez2-aib" / "pending")
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_text(text, encoding="utf-8")
        idx = Q.relay_index_path(self.env)                                     # L-B4:寄件時的留痕列(寄給本席)
        idx.parent.mkdir(parents=True, exist_ok=True)
        with open(idx, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "long", "file": str(p), "target": "@" + SEAT}) + "\n")
        return "長訊已落檔(%d 位元組),請讀:%s" % (len(text.encode()), p)

    def rows(self, tab="waiting"):
        return {r["id"]: r for r in Q.tabs_payload(SEAT, env=self.env)["tabs"][tab]["rows"]}

    def test_e1_preview(self):
        body = "@abb89 → @cfd04:第一行\n第二行 \u202e反轉\x1b[31m紅\x07\x9b2J " + "字" * 400
        long_it = self.item(self.relay(body), label="agent:offhub")
        short_it = self.item("@d07f9 短訊\n第二行")
        rows = self.rows()
        r = rows[long_it["id"]]
        self.assertTrue(r["preview"].startswith("@abb89 → @cfd04:第一行 第二行 反轉[31m紅2J"), r["preview"])
        self.assertEqual(len(r["preview"]), Q.PREVIEW_CHARS)
        for bad in ("\u202e", "\x1b", "\x07", "\x9b", "\n"):
            self.assertNotIn(bad, r["preview"])
        self.assertNotIn("claimed_sender", r)
        self.assertEqual(rows[short_it["id"]]["preview"], "@d07f9 短訊 第二行")
        self.done(Q._load_item(SEAT, long_it["id"], self.env), why="測試")
        self.assertIsNone(self.rows("done")[long_it["id"]]["preview"])          # 完成的不讀檔
        self.assertEqual(Q._preview_fields({"status": "queued", "msg": "@abb89 外人", "sender_kind": "external_im"}), {"preview": None})
        self.assertEqual(Q._preview_fields({"status": "queued", "msg": "@abb89 x", "global": {"addr": "x"}}), {"preview": None})

    def test_e2_relay_dir_only(self):
        import dm_text as DT
        secret = self.tmp / "secret"
        secret.mkdir()
        outside = self.item(self.relay("機密內容", where=secret))                    # 目錄外
        badname = self.item(self.relay("機密內容", name="notes.txt"))                # 檔名不對
        pend = self.tmp / ".ez2-aib" / "pending"
        (pend / "RELAY-dm-6ddf-1025-000000-2.txt").symlink_to(secret / "RELAY-dm-6ddf-1025-000000-1.txt")
        link = self.item("長訊已落檔(4 位元組),請讀:%s" % (pend / "RELAY-dm-6ddf-1025-000000-2.txt"))   # 捷徑指出去
        fifo = pend / "RELAY-dm-6ddf-1025-000000-3.txt"
        os.mkfifo(fifo)
        fifo_it = self.item("長訊已落檔(4 位元組),請讀:%s" % fifo)                  # 不阻塞
        rows = self.rows()
        for it in (outside, badname, link, fifo_it):
            self.assertIsNone(rows[it["id"]]["preview"], it["msg"])
        saved = {k: os.environ.get(k) for k in ("HOME", "EZ2_AIB_HOME", "AIB_DM_RELAY_DIR")}   # original_line 讀 os.environ

        def restore():
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.addCleanup(restore)
        os.environ["HOME"] = str(self.tmp)
        os.environ.pop("EZ2_AIB_HOME", None)
        os.environ.pop("AIB_DM_RELAY_DIR", None)
        self.assertEqual(DT.original_line(outside["msg"])[0], DT.RELAY_UNREADABLE)
        good = self.relay("正常長訊", name="RELAY-dm-6ddf-1025-000000-4.txt")
        self.assertEqual(DT.original_line(good), (DT.OK, "正常長訊"))
        big = self.item(self.relay("字" * 5000, name="RELAY-dm-6ddf-1025-000000-5.txt"))
        self.assertLessEqual(len(self.rows()[big["id"]]["preview"]), Q.PREVIEW_CHARS)
        self.assertEqual(len(Q._relay_head(DR_path(big["msg"]), env=self.env).encode()), 2046)   # 2048 位元組切在字中間 ⇒ 丟掉殘字

    def test_e3_counts(self):
        a, b, c = self.item("a"), self.item("b"), self.item("c")
        Q.mark_read(SEAT, b["id"], "seat:" + SEAT, env=self.env, no_rev_check=True)
        self.done(c, why="x")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = Q.main(["tabs", "--seat", SEAT, "--counts"], env=self.env)
        out = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertEqual(rc, 0)
        self.assertEqual((out["tabs"]["waiting"], out["tabs"]["read"], out["tabs"]["done"]), (1, 1, 1))
        self.assertEqual(out["by_status"], {"queued": 1, "read": 1, "done": 1})
        self.assertEqual(out["read_without_wait"], 1)
        self.assertNotIn("rows", json.dumps(out))

    def test_e4_render(self):
        import render_lib as R
        line, sender = R._pending_row_text({"preview": "前兩百字"}, "長訊已落檔(9 位元組),請讀:/x", "agent:offhub")
        self.assertEqual((line, sender), ("📄 前兩百字", "agent:offhub"))
        self.assertEqual(R._pending_row_text({"preview": "x"}, "短訊本文", "@74e8b"), ("短訊本文", "@74e8b"))   # 短訊不換
        line, _s = R._pending_row_text({"wait": {"text": "等 @abb89 \n收 #366"}}, "本文", "@x")
        self.assertEqual(line, "⏳等 @abb89 收 #366 · 本文")   # L-B1:放最前面
        self.assertEqual(R._pending_row_text({}, "本文", "@x"), ("本文", "@x"))

    def test_e5_wait_same_rule(self):   # #370 r2 L-new3:計數跟畫面同一支判斷
        import dm_text as DT
        import render_lib as R
        waits = [{"text": "等 @abb89"}, {"text": "   "}, {"text": ""}, "字串不是 dict", {"x": 1}, None, {"text": " 收 \n#366 "}]
        rows = [{"status": "read", "wait": w} for w in waits]
        out = Q.tabs_counts({"seat": SEAT, "now": 0, "badges": {}, "errors": [], "tabs": {"read": {"n": len(rows), "rows": rows}}})
        shown = [bool(R._pending_row_text(r, "本文", "@x")[0] != "本文") for r in rows]
        self.assertEqual(shown, [bool(DT.wait_text(r)) for r in rows])
        self.assertEqual(out["read_without_wait"], shown.count(False))
        self.assertEqual(out["read_without_wait"], 5)

    def test_d3_evidence_hint(self):   # #370 r2 L-new1:提示不再叫人貼 #N
        h = Q.TRANSITION_REASON["evidence"]
        self.assertIn("完整 PR 網址", h)
        self.assertNotIn("#PR 號", h)


def DR_path(msg):
    import dm_relay
    return dm_relay.relay_path_of(msg)


if __name__ == "__main__":
    unittest.main()
