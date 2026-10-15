#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球來信的國別(Andy 10-15 18:1x「全球要能判斷國別」;裁:抬頭加國別、申報 + 時區佐證、
用於顯示 / 預設回信語言 / 法規)。

C1 時區 ⇒ 國碼:查 zone.tab(測試用自帶小表,不靠系統檔);查不到回 None。
C2 申報與時區:一致 match、不一致 mismatch、沒申報或時區量不到 unknown。
C3 預設回信語言:對方沒帶語言時照國別(台港澳中 → zh-TW、日 → ja、韓 → ko、其他 → en)。
C4 抬頭國別段:一致只寫國名;不一致 / 量不到加「?」;沒申報不加段;四語;表外國碼照印。
C5 parse 讀得回國別(含「?」與表外國碼);國別段不合 ⇒ 不當全球抬頭。
C6 外框說明行:不一致 / 量不到多一句「自稱 X,時區在 Y / 量不到」;一致不加。
C7 契約 → 佇列 → 抬頭整條:inbox 帶 country/tz ⇒ queue_fields ⇒ enqueue ⇒ build_from_global。
C8 壞國碼、壞時區名不收。
K1 刀測:拿掉「不一致加 ?」⇒ C4 變紅。
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "tests"))
import dm_global_files as G  # noqa: E402
import dm_global_header as H  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402

NPUB = "npub1" + "p" * 58
ADDR = "npub1" + "q" * 58 + "/6ddf/abb89"
TAB = "# 測試用 zone.tab\nTW\t+2503+12130\tAsia/Taipei\nJP\t+353916+1394441\tAsia/Tokyo\nUS\t+404251-0740023\tAmerica/New_York\n"


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-cty-")
        self.addCleanup(td.cleanup)
        self.dir = Path(td.name)
        self.tab = self.dir / "zone.tab"
        self.tab.write_text(TAB, encoding="utf-8")
        self.paths = [str(self.tab)]


class Country(Base):
    def test_c1_tz_country(self):
        self.assertEqual(G.tz_country("Asia/Taipei", self.paths), "TW")
        self.assertEqual(G.tz_country("America/New_York", self.paths), "US")
        for tz in ("UTC", "Europe/Paris", "", None, "../etc", "Asia/Taipei;rm"):
            self.assertIsNone(G.tz_country(tz, self.paths), tz)
        self.assertIsNone(G.tz_country("Asia/Taipei", [str(self.dir / "missing.tab")]))

    def test_c2_country_check(self):
        self.assertEqual(G.country_check("TW", "Asia/Taipei", self.paths), ("match", "TW"))
        self.assertEqual(G.country_check("TW", "Asia/Tokyo", self.paths), ("mismatch", "JP"))
        self.assertEqual(G.country_check("TW", "UTC", self.paths), ("unknown", None))
        self.assertEqual(G.country_check("TW", None, self.paths), ("unknown", None))
        self.assertEqual(G.country_check(None, "Asia/Taipei", self.paths), ("unknown", "TW"))

    def test_c3_default_lang(self):
        for cc, lang in (("TW", "zh-TW"), ("HK", "zh-TW"), ("JP", "ja"), ("KR", "ko"), ("US", "en"),
                         ("BR", "en"), (None, "en")):
            self.assertEqual(G.default_lang(cc), lang, cc)

    def test_c4_header_segment(self):
        self.assertEqual(H.sender_side("Andy", True, country="TW", country_status="match"), "Andy(全球·台灣·已驗證)")
        self.assertEqual(H.sender_side("Andy", True, country="TW", country_status="mismatch"), "Andy(全球·台灣?·已驗證)")
        self.assertEqual(H.sender_side("Andy", False, country="TW", country_status="unknown", same_name=True),
                         "Andy(全球·台灣?·未驗證·同名)")
        self.assertEqual(H.sender_side("Andy", True), "Andy(全球·已驗證)")          # 沒申報不加段
        self.assertEqual(H.sender_side("Andy", True, lang="en", country="JP", country_status="match"),
                         "Andy(Global·Japan·verified)")
        self.assertEqual(H.sender_side("Andy", True, lang="ja", country="KR", country_status="match"),
                         "Andy(グローバル·韓国·認証済み)")
        self.assertEqual(H.sender_side("Andy", True, lang="ko", country="TW", country_status="match"),
                         "Andy(글로벌·대만·인증됨)")
        self.assertEqual(H.sender_side("Andy", True, country="CL", country_status="match"), "Andy(全球·CL·已驗證)")
        with self.assertRaises(ValueError):
            H.sender_side("Andy", True, country="tw", country_status="match")

    def test_c5_parse(self):
        got = H.parse_sender("Andy(全球·台灣?·已驗證·同名)")
        self.assertEqual((got["country"], got["country_sure"], got["verified"], got["same_name"]), ("TW", False, True, True))
        got = H.parse_sender("Andy(Global·United States·unverified)")
        self.assertEqual((got["country"], got["country_sure"], got["lang"]), ("US", True, "en"))
        self.assertEqual(H.parse_sender("Andy(全球·CL·已驗證)")["country"], "CL")
        self.assertIsNone(H.parse_sender("Andy(全球·火星·已驗證)"))
        self.assertIsNone(H.parse_sender("Andy(全球·台灣·台灣·已驗證)"))
        for lang in H.LANGS:
            for st in ("match", "mismatch"):
                side = H.sender_side("Andy", True, lang=lang, country="JP", country_status=st)
                p = H.parse_sender(side)
                self.assertEqual((p["country"], p["country_sure"]), ("JP", st == "match"), side)

    def test_c6_frame_note(self):
        f = H.frame("g7k2m", country="TW", country_status="mismatch", tz_country="JP")
        self.assertIn("國別:自稱台灣,寄件機時區在日本。", f)
        self.assertIn("aib dm --reply g7k2m", f)
        self.assertIn("寄件機時區量不到", H.frame("g7k2m", country="TW", country_status="unknown"))
        self.assertNotIn("國別", H.frame("g7k2m", country="TW", country_status="match", tz_country="TW"))
        self.assertNotIn("國別", H.frame("g7k2m"))
        self.assertIn("claims Taiwan; the sending machine's time zone is in Japan.",
                      H.frame("g7k2m", lang="en", country="TW", country_status="mismatch", tz_country="JP"))
        self.assertIn("自称台湾", H.frame("g7k2m", lang="ja", country="TW", country_status="unknown"))
        self.assertIn("대만(자칭)", H.frame("g7k2m", lang="ko", paired=True, country="TW", country_status="unknown"))

    def test_c7_contract_queue_header(self):
        import dm_header as HD
        import dm_queue as Q
        rec = {"v": 1, "msgid": G.new_msgid(), "received_at": 1, "to_cabin": "931e", "to_seat": "8f366",
               "from_npub": NPUB, "from_addr": ADDR, "display_name": "Andy", "verified": True, "class": "friend",
               "content": "你好", "country": "TW", "tz": "Asia/Tokyo"}
        f = G.queue_fields(rec, tab_paths=self.paths)
        self.assertEqual((f["country"], f["country_status"], f["tz_country"], f["lang"]), ("TW", "mismatch", "JP", "zh-TW"))
        self.assertEqual(G.queue_fields(dict(rec, lang="en"), tab_paths=self.paths)["lang"], "en")
        env = {"EZ2_AIB_STATE_ROOT": str(self.dir), "HOME": str(self.dir), "AIB_DM_ADDR": "100.114.152.121",
               "EZ2_AIB_LOCALE": "zh-TW"}
        it = Q.enqueue("8f366", rec["content"], f["shown_name"], sender_kind=f["sender_kind"],
                       sender_origin=f["sender_origin"], env=env,
                       global_info={"addr": f["sender_addr"], "shown": f["shown_name"], "verified": f["verified"],
                                    "same_name": f["same_name"], "msgid": rec["msgid"], "country": f["country"],
                                    "country_status": f["country_status"], "tz_country": f["tz_country"]})
        self.assertEqual(it["global"]["country"], "TW")
        head, frame = HD.build_from_global("8f366", it["id"], "@8f366(第二艦長)", when=1792045800, env=env)
        self.assertTrue(head.startswith("Andy(全球·台灣?·已驗證) >> "), head)
        self.assertIn("自稱台灣,寄件機時區在日本", frame)

    def test_c8_bad_values(self):
        base = {"v": 1, "msgid": G.new_msgid(), "received_at": 1, "to_cabin": "931e", "to_seat": "8f366",
                "from_npub": NPUB, "verified": True, "class": "friend", "content": "x"}
        for over in (dict(country="tw"), dict(country="TWN"), dict(country=886), dict(tz="Asia/Taipei;rm -rf"),
                     dict(tz="../../etc/passwd"), dict(tz="x" * 80)):
            with self.assertRaises(G.ContractError, msg=over):
                G.check_inbox(dict(base, **over))
        import dm_queue as Q
        g = {"addr": ADDR, "shown": "Andy", "verified": True, "msgid": G.new_msgid()}
        self.assertIsNotNone(Q._global_or_none(g))                       # 舊列沒有國別欄照收
        for over in (dict(country="tw"), dict(country_status="maybe"), dict(tz_country="JPN")):
            self.assertIsNone(Q._global_or_none(dict(g, **over)), over)


class Knife(unittest.TestCase):
    def test_k1_mismatch_without_question_mark(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/dm_global_header.py",
                        'tags.append(cn if country_status == "match" else cn + "?")', "tags.append(cn)",
                        tests=(name,)) as root:
            r = subprocess.run([sys.executable, str(root / "tests" / name), "Country.test_c4_header_segment"],
                               cwd=str(root), capture_output=True, text=True, timeout=120)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
