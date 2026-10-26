#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""#370 審查剩下的小修(排 0.1.61)。

F1 證據網址收緊(B 輪 L-B3):PR 號只收 1–9 位數字、commit 7–40 位 hex;repo 段不能是「.」或含「..」;
   說明文字裡夾的網址前面不能接字 / 斜線 / 等號(別的網址裡夾的不算)
F2 預覽洗字(L-B5 + A 輪 L-new2):U+200D 不剝(emoji 組合字不拆開);U+00AD、U+061C 要剝
F3 原話一行讀長訊檔走 dm_relay.relay_head(A 輪 L-new4):FIFO 不卡住、只讀前 8KB
F4 render_lib 只在模組載入時找一次 dm_text.wait_text(L-B2),不每幀 import
F5 L-B4(@abb89 裁):待審預覽只讀留痕索引寫明寄給本席的長訊檔;別席的 / 沒留痕 / dry-run ⇒ 不預覽;索引後來補的列讀得到
刀:_EV_PR_IN 拿掉前置排除 ⇒ F1 紅;_PREVIEW_BAD 改回 \\u200b-\\u200f ⇒ F2 紅;original_line 改回 read_text ⇒ F3 紅(卡住被 timeout);_preview_fields 拿掉 target 檢查 ⇒ F5 紅
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "src" / "cockpit" / "render"))
import dm_queue as Q  # noqa: E402
import dm_text as DT  # noqa: E402
import render_lib as RL  # noqa: E402

PR = "https://github.com/ez2app/ez2-aib/pull/370"


class F1EvidenceUrl(unittest.TestCase):
    def test_accepts(self):
        for s in (PR, PR + "/", "https://github.com/ez2app/aib-plugins/commit/9c5c2c2"):
            self.assertTrue(Q._PR_URL_RE.fullmatch(s), s)
        self.assertEqual(Q._EV_PR_IN.search("修好了，見 %s 。" % PR).group(0), PR)

    def test_rejects(self):
        bad = ("https://github.com/ez2app/ez2-aib/pull/abc", "https://github.com/ez2app/ez2-aib/pull/1234567890",
               "https://github.com/ez2app/../pull/1", "https://github.com/ez2app/./pull/1", "https://github.com/ez2app/a..b/pull/1",
               "https://github.com/ez2app/ez2-aib/commit/9c5c2c")
        for s in bad:
            self.assertFalse(Q._PR_URL_RE.fullmatch(s), s)
            self.assertIsNone(Q._EV_PR_IN.search(s), s)
        for s in ("https://evil.example/?u=" + PR, "https://evil.example/" + PR, "x" + PR):
            self.assertIsNone(Q._EV_PR_IN.search(s), s)


class F2PreviewChars(unittest.TestCase):
    def test_zwj_kept_and_invisibles_stripped(self):
        fam = "\U0001F468\u200d\U0001F469"
        self.assertEqual(Q._PREVIEW_BAD.sub("", fam), fam)
        self.assertEqual(Q._PREVIEW_BAD.sub("", "a\u00adb\u061cc\u200bd\u202ee"), "abcde")


class F3OriginalLine(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-dmfu-")
        self.addCleanup(td.cleanup)
        self.pend = Path(td.name, "pending")
        self.pend.mkdir()
        old = os.environ.get("EZ2_AIB_HOME")
        os.environ["EZ2_AIB_HOME"] = td.name
        self.addCleanup(lambda: os.environ.__setitem__("EZ2_AIB_HOME", old) if old is not None else os.environ.pop("EZ2_AIB_HOME", None))

    def ptr(self, p):
        return "長訊已落檔(9 位元組),請讀:%s" % p

    def test_big_file_reads_head(self):
        p = self.pend / "RELAY-dm-6ddf-1026-000000-1.txt"
        p.write_text("第一行原話\n" + "字" * 20000, encoding="utf-8")
        self.assertEqual(DT.original_line(self.ptr(p)), (DT.OK, "第一行原話"))

    def test_fifo_does_not_block(self):
        p = self.pend / "RELAY-dm-6ddf-1026-000000-2.txt"
        os.mkfifo(p)
        out = []
        t = threading.Thread(target=lambda: out.append(DT.original_line(self.ptr(p))), daemon=True)
        t.start()
        t.join(5)
        self.assertFalse(t.is_alive(), "讀 FIFO 卡住了")
        self.assertEqual(out[0][0], DT.RELAY_UNREADABLE)


class F4WaitTextOnce(unittest.TestCase):
    def test_loaded_at_import(self):
        self.assertIs(RL._CORE_WAIT_TEXT, DT.wait_text)
        self.assertEqual(RL._wait_text({"wait": {"text": " 等 \n abb89 "}}), "等 abb89")


class F5PreviewOnlyMine(unittest.TestCase):
    SEAT = "abcde"

    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-dmfu5-")
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "EZ2_AIB_LOCALE": "zh-TW", "AIB_DM_ADDR": "100.114.152.121"}
        self.pend = self.tmp / ".ez2-aib" / "pending"
        self.pend.mkdir(parents=True)
        self.idx = Q.relay_index_path(self.env)
        self.idx.parent.mkdir(parents=True)

    def relay(self, n, text, target=None, dry=False):
        p = self.pend / ("RELAY-dm-6ddf-1026-000000-%d.txt" % n)
        p.write_text(text, encoding="utf-8")
        if target is not None:
            with open(self.idx, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"kind": "long", "file": str(p), "target": target, "dry_run": dry}) + "\n")
        return Q.enqueue(self.SEAT, "長訊已落檔(9 位元組),請讀:%s" % p, "寄件席", env=self.env)

    def previews(self):
        return {r["id"]: r["preview"] for r in Q.tabs_payload(self.SEAT, env=self.env)["tabs"]["waiting"]["rows"]}

    def test_only_relays_sent_to_me(self):
        mine = self.relay(1, "給我的", "@abcde")
        multi = self.relay(2, "多席有我", "6ddf/fghij,6ddf/abcde")
        other = self.relay(3, "別席的信", "6ddf/fghij")
        none = self.relay(4, "沒留痕")
        dry = self.relay(5, "沒送出", "@abcde", dry=True)
        got = self.previews()
        self.assertEqual(got[mine["id"]], "給我的")
        self.assertEqual(got[multi["id"]], "多席有我")
        for it in (other, none, dry):
            self.assertIsNone(got[it["id"]], it["msg"])
        with open(self.idx, "a", encoding="utf-8") as fh:              # 索引後來才補上 ⇒ 下一次讀新增的尾巴
            fh.write(json.dumps({"kind": "long", "file": str(self.pend / "RELAY-dm-6ddf-1026-000000-4.txt"),
                                 "target": "abcde"}) + "\n")
        self.assertEqual(self.previews()[none["id"]], "沒留痕")
        self.assertIsNone(Q._preview_fields(Q._load_item(self.SEAT, mine["id"], self.env), self.env)["preview"])   # 沒給席碼不讀


if __name__ == "__main__":
    unittest.main()
