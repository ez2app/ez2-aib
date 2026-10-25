#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""待審區優化 D + E + F(10-25 Andy 裁,@abb89 派 @cfd04)。

D1 證據可以是說明文字:裡面夾 github.com/ez2app 的 PR 網址 / 「#數字」(補成本倉 PR 網址)/ 本席寄出的回信 id /
   查得到的 commit / 存在的檔 ⇒ 收,done_ref 記查證的那一段、done_evidence 留原文;整串剛好是證據照舊(不寫 done_ref)
D2 純文字、別人的網址、別席寄的 q_ id、查不到的 commit、不存在的檔 ⇒ 照舊退(evidence);有 --why 才憑理由結
E1 tabs 每列帶 preview(長訊讀 RELAY 檔前 200 字、短訊就是本文;控制 / 方向字元拿掉、換行壓空白)+ claimed_sender(正文開頭自報 @席碼)
   完成 / 備存不讀檔(preview None);全球來信不讀
E2 tabs --counts 只印數量:每頁則數、依狀態分開、進行中沒寫「等什麼」的則數
E3 待審框:長訊列印「📄 前 200 字」;寄件人是 agent:… 才補「(自報 @席碼)」;row["wait"] 有字 ⇒ 尾巴接「⏳等:…」;沒這些鍵照舊
F1 seat-info:這個對話的 uuid 對不上席位檔 ⇒ 退回 AIB_RESUME_SESSION_UUID 再試(uuid_via=resume_env);都對不上 ⇒ 照舊量不到
刀:evidence_ref 不抽說明文字 ⇒ D1 紅;preview 不洗控制字元 ⇒ E1 紅;seat-info 不試繼承的 uuid ⇒ F1 紅
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
        repo = Q._ev_repo()
        self.assertTrue(repo.startswith("ez2app/"), repo)
        sent = self.item("回你", seat=OTHER, sender_seat=SEAT, sender_source="pane_pid_chain")
        f = self.tmp / "out.json"
        f.write_text("{}")
        cases = [("PR 開了 https://github.com/ez2app/ez2-aib/pull/366 ,等 CI", "pr", "https://github.com/ez2app/ez2-aib/pull/366"),
                 ("修好了，見 #348", "pr", "https://github.com/%s/pull/348" % repo),
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
        for ev in ("處理完了", "見 https://github.com/evil/x/pull/1", "回過了 %s" % other_sent["id"], "commit deadbeefdeadbeef 推了",
                   "檔在 /no/such/file.txt", "版本 v2 'x'", "issue#12 不算"):
            it = self.item()
            out = self.done(it, ev)
            self.assertEqual((out.get("ok"), out.get("reason")), (False, "evidence"), ev)
        it = self.item()
        self.assertTrue(self.done(it, "處理完了", why="通知類，讀完即結").get("ok"))
        self.assertEqual(self.load(it)["done_kind"], "reason")


class Display(Base):
    def relay(self, text):
        p = self.tmp / "RELAY-dm-x.txt"
        p.write_text(text, encoding="utf-8")
        return "長訊已落檔(%d 位元組),請讀:%s" % (len(text.encode()), p)

    def test_e1_preview_and_claimed(self):
        body = "@abb89 → @cfd04:第一行\n第二行 \u202e反轉\x1b[31m紅\x07 " + "字" * 400
        long_it = self.item(self.relay(body), label="agent:offhub")
        short_it = self.item("@d07f9 短訊\n第二行")
        plain = self.item("沒有自報")
        rows = {r["id"]: r for r in Q.tabs_payload(SEAT, env=self.env)["tabs"]["waiting"]["rows"]}
        r = rows[long_it["id"]]
        self.assertEqual(r["claimed_sender"], "@abb89")
        self.assertTrue(r["preview"].startswith("@abb89 → @cfd04:第一行 第二行 反轉[31m紅"), r["preview"])
        self.assertEqual(len(r["preview"]), Q.PREVIEW_CHARS)
        for bad in ("\u202e", "\x1b", "\x07", "\n"):
            self.assertNotIn(bad, r["preview"])
        self.assertEqual((rows[short_it["id"]]["preview"], rows[short_it["id"]]["claimed_sender"]), ("@d07f9 短訊 第二行", "@d07f9"))
        self.assertIsNone(rows[plain["id"]]["claimed_sender"])
        self.done(Q._load_item(SEAT, long_it["id"], self.env), why="測試")
        rows = {r["id"]: r for r in Q.tabs_payload(SEAT, env=self.env)["tabs"]["done"]["rows"]}
        self.assertIsNone(rows[long_it["id"]]["preview"])          # 完成的不讀檔
        self.assertEqual(Q._preview_fields({"status": "queued", "msg": "@abb89 外人", "sender_kind": "external_seat"}),
                         {"preview": None, "claimed_sender": None})     # 全球來信(外人寫的)不讀、不認自報
        self.assertEqual(Q._preview_fields({"status": "queued", "msg": "@abb89 x", "global": {"addr": "x"}}),
                         {"preview": None, "claimed_sender": None})

    def test_e2_counts(self):
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

    def test_e3_render(self):
        import render_lib as R
        line, sender = R._pending_row_text({"preview": "前兩百字", "claimed_sender": "@abb89"}, "長訊已落檔(9 位元組),請讀:/x", "agent:offhub")
        self.assertEqual((line, sender), ("📄 前兩百字", "agent:offhub(自報 @abb89)"))
        line, sender = R._pending_row_text({"preview": "x", "claimed_sender": "@abb89"}, "短訊本文", "@74e8b(艦長室)")
        self.assertEqual((line, sender), ("短訊本文", "@74e8b(艦長室)"))     # 短訊不換;量得到的寄件人不補自報
        line, _s = R._pending_row_text({"wait": {"text": "等 @abb89 \n收 #366"}}, "本文", "@x")
        self.assertEqual(line, "本文 ⏳等:等 @abb89 收 #366")
        self.assertEqual(R._pending_row_text({}, "本文", "@x"), ("本文", "@x"))


class SeatInfo(Base):
    def test_f1_resume_uuid_fallback(self):
        seats = self.tmp / "instances" / "20261014-133230-6ddf" / "seats"
        seats.mkdir(parents=True)
        old = "e324bfde-81ac-41af-9321-e2d8f30cfd04"
        (seats / "w4.json").write_text(json.dumps({"schema": "aib-seat/v1", "session_uuid": old, "role": "root", "window_id": "@4",
                                                   "cabin_id": "20261014-133230-6ddf", "started_epoch": 1}))
        script = REPO.parent / "extensions" / "aib-boards" / "bin" / "seat-info.py"
        base = {k: v for k, v in os.environ.items() if not k.startswith(("TMUX", "AIB_", "CLAUDE_CODE_", "EZ2_"))}
        base.update({"EZ2_AIB_STATE_ROOT": str(self.tmp), "HOME": str(self.tmp), "EZ2_COCKPIT_DIR": str(REPO / "src" / "cockpit")})
        bg = "11111111-2222-4333-8444-555555555555"

        def run(extra):
            r = subprocess.run([sys.executable, str(script), bg], env=dict(base, **extra), capture_output=True, text=True, timeout=60)
            return json.loads(r.stdout.strip().splitlines()[-1])
        got = run({"AIB_RESUME_SESSION_UUID": old})
        self.assertEqual((got["code"], got.get("uuid_via")), ("cfd04", "resume_env"), got.get("err"))
        got = run({})
        self.assertIsNone(got["code"])


if __name__ == "__main__":
    unittest.main()
