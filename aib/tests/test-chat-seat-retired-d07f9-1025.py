#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""寄到已下架的席(10-25 一鍵對話 5b;@abb89 裁:不自動改寄,回 seat_retired + 一鍵改寄)。
起因:Mac Pro 13 從 1803/9538b 換到 0c1b/ae6a1,寄舊席一直卡住、也沒人說該寄哪。

R1 retired_next:房間快照的艦長席還在名冊 ⇒ 用它;艦長席也搬了 ⇒ 名冊第一席;跟原位址一樣 / 什麼都不知道 ⇒ None
R2 _with_retired:refused + no_seat ⇒ next / hint_code=seat_retired / hint(含 aib dm <next>);detail 照舊 no_seat(艦長室認這個字)
R3 其他結果(delivered / 別的拒收碼 / resolve)不加鍵;找不到改寄位址 ⇒ 不加鍵
R4 頁面本人寄件(chat_human)把 next / hint_code / hint 帶出去
(轉接程式那一半:名冊沒這席 ⇒ 先更新快照再寫回條,在 test-chat-send S4 連假中繼站測)
刀(複本):K1 艦長席不比對名冊 ⇒ R1 紅
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(HERE))
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

OWNER = NK.npub(NK.pubkey_hex("%064x" % 0xa6a1))
DEAD = OWNER + "/1803/9538b"
LIVE = OWNER + "/0c1b/ae6a1"


class Box(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-retired-")
        self.root = Path(self.td.name) / "bridge"
        GF.ensure_dirs(self.root)

    def tearDown(self):
        self.td.cleanup()

    def roster(self, *seats, at=100):
        GF.note_peer(self.root, OWNER, "驗收B2", "TW", None, roster_at=at,
                     claims={"roster_seats": [{"cabin": c, "seat": s, "npub": OWNER, "name": None, "role": None}
                                              for c, s in seats]})

    def room(self, cabin, captain):
        (self.root / "spaces").mkdir(exist_ok=True)
        (self.root / "spaces" / (OWNER + ".json")).write_text(json.dumps({"at": 1, "snap": {"rooms": {
            "ship": {"captain": {"seat": captain}}, "seats": [{"seat": captain, "cabin": cabin}]}}}), encoding="utf-8")


class Core(Box):
    def test_r1_next(self):
        try:
            self.assertIsNone(GF.retired_next(self.root, OWNER, DEAD), "什麼都不知道")
            self.roster(("0c1b", "aaaa1"), ("0c1b", "ae6a1"))
            self.assertEqual(GF.retired_next(self.root, OWNER, DEAD), OWNER + "/0c1b/aaaa1", "沒房間快照 ⇒ 名冊第一席")
            self.room("0c1b", "ae6a1")
            self.assertEqual(GF.retired_next(self.root, OWNER, DEAD), LIVE, "艦長席還在名冊 ⇒ 用它")
            self.room("1803", "9538b")                       # 房間快照比名冊舊:艦長席本身也搬了
            self.assertEqual(GF.retired_next(self.root, OWNER, DEAD), OWNER + "/0c1b/aaaa1")
            self.roster(("1803", "9538b"), at=200)
            self.assertIsNone(GF.retired_next(self.root, OWNER, DEAD), "跟原位址一樣不建議")
        except Exception as exc:  # noqa: BLE001  刀砍出來的要是 AssertionError
            self.fail("%s: %s" % (type(exc).__name__, exc))

    def test_r2_hint_keeps_detail(self):
        self.roster(("0c1b", "ae6a1"))
        res = GF._with_retired(self.root, {"op": "send", "to": DEAD},
                               {"rc": 2, "stage": "refused", "pos": None, "detail": "no_seat", "msgid": "m"})
        self.assertEqual((res["detail"], res["rc"], res["stage"]), ("no_seat", 2, "refused"))
        self.assertEqual((res["next"], res["hint_code"]), (LIVE, "seat_retired"))
        self.assertIn("aib dm " + LIVE, res["hint"])
        self.assertIn("沒有寄出", res["hint"])

    def test_r3_untouched(self):
        self.roster(("0c1b", "ae6a1"))
        for rec, res in (({"op": "send", "to": DEAD}, {"stage": "delivered", "detail": ""}),
                         ({"op": "send", "to": DEAD}, {"stage": "refused", "detail": "unattested"}),
                         ({"op": "resolve", "to": DEAD}, {"stage": "refused", "detail": "no_seat"}),
                         ({"op": "send", "to": "npub1xyz"}, {"stage": "refused", "detail": "no_seat"})):
            out = GF._with_retired(self.root, rec, dict(res))
            self.assertNotIn("hint_code", out, (rec, res))
        other = NK.npub(NK.pubkey_hex("%064x" % 0xa6a2))
        out = GF._with_retired(self.root, {"op": "send", "to": other + "/x1/bbbbb"}, {"stage": "refused", "detail": "no_seat"})
        self.assertNotIn("next", out, "不知道那位的名冊 ⇒ 不建議")

    def test_r4_page_send_passes_hint(self):
        import chat_human as CH
        fake = {"rc": 2, "stage": "refused", "pos": None, "detail": "no_seat", "msgid": GF.new_msgid(),
                "next": LIVE, "hint_code": "seat_retired", "hint": "x"}
        env = {"AIB_CHAT_BRIDGE_DIR": str(self.root), "HOME": self.td.name}
        with mock.patch.object(CH, "resolve_to", lambda to, e: (to, "address")), \
                mock.patch.object(CH, "pick_seat", lambda c, e: "aaaa1"), \
                mock.patch.object(CH, "prove_human", lambda *a: ("touchid", None)), \
                mock.patch.object(GF, "send_and_wait", lambda *a, **k: dict(fake)):
            out = CH.send(DEAD, "嗨", None, "c1", env=env)
        self.assertEqual((out["next"], out["hint_code"], out["detail"], out["stage"]), (LIVE, "seat_retired", "no_seat", "refused"))
        fake.pop("next"), fake.pop("hint_code"), fake.pop("hint")
        with mock.patch.object(CH, "resolve_to", lambda to, e: (to, "address")), \
                mock.patch.object(CH, "pick_seat", lambda c, e: "aaaa1"), \
                mock.patch.object(CH, "prove_human", lambda *a: ("touchid", None)), \
                mock.patch.object(GF, "send_and_wait", lambda *a, **k: dict(fake)):
            out = CH.send(DEAD, "嗨", None, "c1", env=env)
        self.assertNotIn("next", out)


class Knife(unittest.TestCase):
    def test_k1_captain_not_checked(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/dm_global_files.py",
                        '    if addr and why == "captain" and addr.split("/", 1)[1] not in live:\n',
                        "    if False:\n", tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), "Core.test_r1_next"], cwd=str(root),
                            max_procs=40, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
