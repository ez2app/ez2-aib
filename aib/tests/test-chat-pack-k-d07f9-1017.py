#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""包 K:島的擴充欄位、掛單、大小與相容(開發文件第 36 節)。核心一定跑(只用標準庫);走網路的在 test-chat-e2e-world。

P1 欄位:terrain.grid(代碼、次數、格數 = w×h)、look(id 規則、#rrggbb 存小寫)、pets ≤ 24、offers ≤ 20(id 重複只留一筆)、
   建築 ≤ 200 / 擺設 ≤ 500 只取前面;problems 列出超量與壞格子
P2 相容:有包 K 欄位才標 v3;decode 收 v2 / v3、收件端再驗一次島(壞 look / 壞格子丟掉);rle 不被 200 字截斷
P3 最壞情況:200 棟帶 look + 500 擺設 + 64×64 亂數地形 + 20 掛單 + 24 寵物 ⇒ 壓後 ≤ 40 KB,收得回來
P4 賣方:成交扣量、超賣擋(not_enough / gone)、改價(price_changed)、同一 nonce 冪等、不是掛單機(not_host)、
   限速(第 11 筆回一次 rate、之後靜默)、壞單(bad)
P5 買方:下單記 pending + 放進 offer-out;回覆 ok / reject;逾時 expired;逾時後到的 ok 記 late、reject 不改
B1 指令:offer host on|off、offer list(sold / left)、space check(超量 / 壞格子 rc 1、合格 rc 0)、space --json 的 last_error、
   offers <好友>、offer buy(轉接程式沒開 rc 2;開著 ⇒ 帳 pending + 佇列)
刀(複本):K1 不扣已成交 ⇒ P4 紅;K2 收件端不再驗島 ⇒ P2 紅
"""
import importlib.util
import json
import os
import random
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import chat_offers as CO  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402
import room_snapshot as RS  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_send_pk", str(HERE / "test-chat-send-d07f9-1016.py"))
SB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SB)
FRIEND = NK.npub(NK.pubkey_hex("%064x" % 131))
OTHER = NK.npub(NK.pubkey_hex("%064x" % 132))
OID, OID2 = "0a1b2c3d", "deadbeef"


def nonce(i):
    return "%016x" % i


def rle(cells):
    out, i = [], 0
    while i < len(cells):
        j = i
        while j < len(cells) and cells[j] == cells[i] and j - i < 4096:
            j += 1
        out.append(cells[i] + (str(j - i) if j - i > 1 else ""))
        i = j
    return "".join(out)


class Pure(unittest.TestCase):
    def test_p1_fields(self):
        self.assertEqual(RS.clean_grid({"v": 1, "rle": "g4000s96"}, 64, 64), {"v": 1, "rle": "g4000s96"})
        for bad in ({"v": 1, "rle": "g4095"}, {"v": 1, "rle": "x4096"}, {"v": 1, "rle": "g04096"}, {"v": 2, "rle": "g4096"},
                    {"v": 1, "rle": "g4097"}, {"v": 1, "rle": ""}, "g4096", {"v": 1, "rle": "g9999"}):
            self.assertIsNone(RS.clean_grid(bad, 64, 64), bad)
        lk = RS.clean_look({"base": "cn", "wall": "Stone", "roof": "eave", "sign": "food",
                            "c": {"wall": "#AABBCC", "roof": "red", "accent": "#00ff00"}})
        self.assertEqual(lk, {"base": "cn", "roof": "eave", "sign": "food", "c": {"wall": "#aabbcc", "accent": "#00ff00"}})
        for bad in ({"base": "中國"}, {"wall": "stone"}, "cn", None):
            self.assertIsNone(RS.clean_look(bad), bad)
        isl = RS.clean_island({
            "size": {"w": 4, "h": 2}, "terrain": {"sea": True, "seed": 3, "grid": {"v": 1, "rle": "g7"}},
            "pets": [{"kind": "cat", "lv": 3}, {"kind": "Cat", "lv": 3}, {"kind": "dog", "lv": 0}] + [{"kind": "koi", "lv": 1}] * 30,
            "buildings": [{"cabin": "6ddf", "x": 1, "y": 1, "look": {"base": "tw", "c": {"wall": "#123456"}}},
                          {"cabin": "6ddf", "x": 2, "y": 1, "look": {"base": "!"}}] + [{"cabin": "6ddf", "x": 0, "y": 0}] * 250,
            "decor": [{"kind": "tree"}] * 600,
            "offers": [{"id": OID, "item": "plank", "n": 5, "unit": 3, "at": 1}, {"id": OID, "item": "brick", "n": 1, "unit": 1},
                       {"id": "zz", "item": "plank", "n": 1, "unit": 1}, {"id": OID2, "item": "plank", "n": 1000, "unit": 1}]})
        self.assertEqual(isl["terrain"], {"sea": True, "seed": 3}, "格數不對 ⇒ grid 當沒有,其他地形照留")
        self.assertEqual(isl["pets"][:2], [{"kind": "cat", "lv": 3}, {"kind": "koi", "lv": 1}])
        self.assertLessEqual(len(isl["pets"]), RS.PETS_MAX)
        self.assertEqual(isl["buildings"][0]["look"], {"base": "tw", "c": {"wall": "#123456"}})
        self.assertNotIn("look", isl["buildings"][1], "base 不合 ⇒ 整個 look 當沒有")
        self.assertEqual((len(isl["buildings"]), len(isl["decor"])), (RS.BUILDINGS_MAX, RS.DECOR_MAX), "超過只取前面")
        self.assertEqual(isl["offers"], [{"id": OID, "item": "plank", "n": 5, "unit": 3, "at": 1}])
        self.assertNotIn("offers", RS.clean_island({"offers": isl["offers"]}, with_offers=False))
        probs = {(p["field"], p["why"]) for p in RS.problems({"size": {"w": 4, "h": 2}, "terrain": {"grid": {"v": 1, "rle": "g7"}},
                                                               "decor": [1] * 501, "buildings": [1] * 201, "pets": [1] * 25,
                                                               "offers": [1] * 21})}
        self.assertEqual(probs, {("decor", "too_many"), ("buildings", "too_many"), ("pets", "too_many"), ("offers", "too_many"),
                                 ("terrain.grid", "bad")})
        self.assertEqual(RS.problems({"size": {"w": 4, "h": 2}, "terrain": {"grid": {"v": 1, "rle": "g8"}}}), [])

    def test_p2_compat(self):
        long_rle = "gs" * 2048
        self.assertEqual(RS.scrub({"terrain": {"grid": {"rle": long_rle}}})["terrain"]["grid"]["rle"], long_rle, "rle 不被 200 字截斷")
        self.assertEqual(len(RS.scrub({"name": "x" * 500})["name"]), RS.STR_MAX)
        plain = RS.clean_island({"name": "島"})
        self.assertFalse(RS.has_k_fields(plain))
        self.assertTrue(RS.has_k_fields(RS.clean_island({"pets": [{"kind": "cat", "lv": 1}]})))
        self.assertTrue(RS.has_k_fields(RS.clean_island({"buildings": [{"cabin": "a", "x": 0, "y": 0, "look": {"base": "cn"}}]})))
        snap = {"v": 3, "people": [], "rooms": None,
                "island": {"size": {"w": 4, "h": 2}, "terrain": {"grid": {"v": 1, "rle": "g9"}},
                           "buildings": [{"cabin": "a", "x": 0, "y": 0, "look": {"base": "BAD"}}], "pets": [{"kind": "x y", "lv": 1}]}}
        got = RS.decode(RS.encode(snap))
        self.assertIsNotNone(got, "v3 收得進來")
        self.assertEqual((got["island"]["terrain"], "look" in got["island"]["buildings"][0], got["island"]["pets"]), ({}, False, []),
                         "收件端再驗一次島")
        old = RS.decode(RS.encode({"v": 2, "people": [], "rooms": None, "island": {"decor": [], "decor_omitted": True}}))
        self.assertTrue(old["island"]["decor_omitted"], "0.1.16 以前寄件端的標記照留")
        self.assertIsNone(RS.decode(RS.encode({"v": 4, "people": []})), "不認得的版本不收")

    def test_p3_worst_case(self):
        rnd = random.Random(1)
        ids = lambda xs: rnd.choice(xs)
        isl = {"name": "最壞", "style": "cn", "size": {"w": 64, "h": 64},
               "terrain": {"sea": True, "seed": 1, "grid": {"v": 1, "rle": rle([ids("gswdrtfh") for _ in range(4096)])}},
               "buildings": [{"cabin": "6ddf", "x": rnd.randrange(64), "y": rnd.randrange(64), "kind": "hall", "lv": rnd.randrange(1, 99),
                              "look": {"base": ids(["cn", "jp", "tw"]), "wall": ids(["stone", "brick"]), "roof": ids(["gable", "eave"]),
                                       "sign": "food", "c": {k: "#%06x" % rnd.randrange(1 << 24) for k in ("wall", "roof", "accent")}},
                              "building": {"start": 1, "done": 2}} for _ in range(200)],
               "decor": [{"kind": ids(["tree", "rock", "lamp"]), "x": rnd.randrange(64), "y": rnd.randrange(64)} for _ in range(500)],
               "pets": [{"kind": "cat", "lv": rnd.randrange(1, 99)} for _ in range(24)],
               "offers": [{"id": "%08x" % i, "item": "plank", "n": 999, "unit": 10 ** 6, "at": 1} for i in range(20)]}
        self.assertGreater(len(json.dumps(isl).encode()), RS.RAW_MAX, "原文超過舊的 64 KB(包 K 前會被整份丟掉)")
        clean = RS.clean_island(RS.scrub(isl))
        self.assertIn("grid", clean["terrain"])
        snap = {"v": 3, "island": clean, "people": [{"seat": "%05x" % i, "name": "席"} for i in range(30)], "rooms": None}
        wire, full, over = RS.encode_info(snap)
        self.assertIsNotNone(wire, full)
        self.assertLessEqual(len(wire), RS.WIRE_MAX)
        got = RS.decode(wire)
        self.assertEqual((len(got["island"]["buildings"]), len(got["island"]["decor"]), got["island"]["terrain"]["grid"]),
                         (200, 500, clean["terrain"]["grid"]))


class Ledger(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.env = {"AIB_CHAT_HOME": str(Path(self.td.name) / "chat"), "HOME": self.td.name}
        Path(self.env["AIB_CHAT_HOME"]).mkdir()
        self.root = Path(self.td.name) / "bridge"
        (Path(self.env["AIB_CHAT_HOME"]) / "island.json").write_text(json.dumps(
            {"offers": [{"id": OID, "item": "plank", "n": 3, "unit": 5}, {"id": OID2, "item": "brick", "n": 1, "unit": 9}]}))
        RS.set_offers_host(True, self.env)

    def buy(self, peer, i, oid=OID, n=1, unit=5, now=None):
        return CO.seller_decide(self.env, peer, {"op": "buy", "id": oid, "n": n, "unit": unit, "nonce": nonce(i)}, now=now)

    def test_p4_seller(self):
        t = 1000
        r = self.buy(FRIEND, 1, n=2, now=t)
        self.assertEqual((r["op"], r["n"], r["item"]), ("ok", 2, "plank"))
        self.assertEqual(self.buy(OTHER, 2, n=2, now=t)["why"], "not_enough", "只剩 1 個,不准超賣")
        self.assertEqual(self.buy(OTHER, 3, n=1, unit=4, now=t)["why"], "price_changed")
        self.assertEqual(self.buy(OTHER, 4, n=1, now=t)["op"], "ok")
        self.assertEqual(self.buy(OTHER, 5, n=1, now=t)["why"], "gone", "賣完")
        self.assertEqual(self.buy(FRIEND, 1, n=2, now=t + 1), r, "同一 nonce 回同一結果,不再扣")
        self.assertEqual(self.buy(OTHER, 6, oid="ffffffff", now=t)["why"], "gone")
        self.assertEqual(CO.seller_decide(self.env, OTHER, {"op": "buy", "id": OID2, "n": 0, "unit": 9, "nonce": nonce(7)}, now=t)["why"],
                         "bad")
        lst = {o["id"]: o for o in CO.offer_list(self.env, now=t)["offers"]}
        self.assertEqual((lst[OID]["sold"], lst[OID]["left"], lst[OID2]["left"]), (3, 0, 1))
        RS.set_offers_host(False, self.env)
        self.assertEqual(self.buy(FRIEND, 8, oid=OID2, unit=9, now=t)["why"], "not_host", "不是掛單機不成交")
        self.assertEqual(CO.offer_list(self.env, now=t)["offers"], [], "不是掛單機沒有自己的掛單")

    def test_p4_rate(self):
        t = 2000
        got = [self.buy(FRIEND, 100 + i, oid=OID2, unit=1, now=t) for i in range(12)]
        self.assertEqual([g["why"] for g in got[:10]], ["price_changed"] * 10)
        self.assertEqual(got[10]["why"], "rate", "第 11 筆回一次 rate")
        self.assertIsNone(got[11], "之後靜默丟")
        self.assertEqual(self.buy(FRIEND, 200, oid=OID2, unit=9, now=t + 61)["op"], "ok", "過了一分鐘恢復")

    def test_p5_buyer(self):
        t = int(time.time())
        rec = CO.buyer_new(self.env, self.root, FRIEND, OID, 2, 5, item="plank", now=t)
        self.assertEqual((rec["status"], rec["total"]), ("pending", 10))
        q = CO.queued(self.root)
        self.assertEqual(q[0][1]["msg"], {"op": "buy", "id": OID, "n": 2, "unit": 5, "nonce": rec["nonce"]})
        self.assertIsNone(CO.buyer_reply(self.env, OTHER, {"op": "ok", "nonce": rec["nonce"]}, now=t), "別人回的不算")
        self.assertEqual(CO.buyer_reply(self.env, FRIEND, {"op": "ok", "nonce": rec["nonce"], "item": "plank"}, now=t)["status"], "ok")
        old = CO.buyer_new(self.env, self.root, FRIEND, OID, 1, 5, now=t - 700)
        rej = CO.buyer_new(self.env, self.root, FRIEND, OID, 1, 5, now=t)
        self.assertEqual(CO.expire(self.env, now=t), 1)
        self.assertIsNone(CO.buyer_reply(self.env, FRIEND, {"op": "reject", "nonce": old["nonce"], "why": "gone"}, now=t),
                          "逾時後的 reject 不改")
        self.assertEqual(CO.buyer_reply(self.env, FRIEND, {"op": "ok", "nonce": old["nonce"]}, now=t)["status"], "late")
        r = CO.buyer_reply(self.env, FRIEND, {"op": "reject", "nonce": rej["nonce"], "why": "weird"}, now=t)
        self.assertEqual((r["status"], r["why"]), ("reject", "bad"))
        st = {x["nonce"]: x["status"] for x in CO.offer_list(self.env, now=t)["trades"] if x["role"] == "buyer"}
        self.assertEqual(st, {rec["nonce"]: "ok", old["nonce"]: "late", rej["nonce"]: "reject"})
        self.assertEqual(oct(os.stat(CO.trades_path(self.env)).st_mode & 0o777), "0o600")


class Box(unittest.TestCase):
    def setUp(self):
        self.b = SB.Box()
        self.addCleanup(self.b.close)
        b = self.b
        b.seat("20261017-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "艦長")
        self.assertEqual(b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)

    def test_b1_cli(self):
        b = self.b
        js = lambda *a: json.loads(b.cli(*a).stdout)
        (b.chat / "island.json").write_text(json.dumps({"size": {"w": 4, "h": 2}, "offers": [{"id": OID, "item": "plank", "n": 3, "unit": 5}]}))
        self.assertFalse(js("offer", "host", "off", "--json")["host"])
        self.assertEqual(js("offer", "list", "--json")["offers"], [])
        self.assertTrue(js("offer", "host", "on", "--json")["host"])
        self.assertEqual([(o["id"], o["left"]) for o in js("offer", "list", "--json")["offers"]], [(OID, 3)])
        self.assertEqual(b.cli("offer", "host", "maybe").returncode, 2)
        bad = b.chat.parent / "bad-island.json"
        bad.write_text(json.dumps({"size": {"w": 4, "h": 2}, "decor": [{"kind": "tree"}] * 501, "terrain": {"grid": {"v": 1, "rle": "g7"}}}))
        r = b.cli("space", "check", "--island", str(bad), "--json")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual({e["field"] for e in json.loads(r.stdout)["errors"]}, {"decor", "terrain.grid"})
        good = b.chat.parent / "good-island.json"
        good.write_text(json.dumps({"size": {"w": 4, "h": 2}, "terrain": {"grid": {"v": 1, "rle": "g8"}}}))
        r = b.cli("space", "check", "--island", str(good), "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(r.stdout)["ok"])
        self.assertEqual(json.loads((b.chat / "island.json").read_text())["size"], {"w": 4, "h": 2}, "check 不動真的檔")
        GF.ensure_dirs(b.root)
        self.assertIsNone(js("space", "--json")["last_error"])
        GF.write_space_status(b.root, {"at": 5, "reason": "too_big", "wire_bytes": 50000, "limit": RS.WIRE_MAX})
        self.assertEqual(js("space", "--json")["last_error"]["reason"], "too_big")
        GF.write_room(b.root, FRIEND, {"v": 3, "people": [], "rooms": None,
                                       "island": RS.clean_island({"offers": [{"id": OID2, "item": "brick", "n": 1, "unit": 9}]})})
        self.assertEqual(b.cli("offers", FRIEND, "--json").returncode, 1, "不是好友 ⇒ 看不到")
        b.cli("friend", "add", FRIEND)
        self.assertEqual([o["id"] for o in js("offers", FRIEND, "--json")["offers"]], [OID2])
        self.assertEqual(b.cli("offer", "buy", OTHER, OID2, "1", "--unit", "9").returncode, 2, "不是好友不能買")
        self.assertEqual(b.cli("offer", "buy", FRIEND, OID2, "1").returncode, 2, "要給單價")
        self.assertEqual(b.cli("offer", "buy", FRIEND, OID2, "1", "--unit", "9").returncode, 2, "轉接程式沒開 ⇒ rc 2")
        GF.write_heartbeat(b.root)
        r = b.cli("offer", "buy", FRIEND, OID2, "1", "--unit", "9", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        t = json.loads(r.stdout)["trade"]
        self.assertEqual((t["status"], t["item"], t["total"]), ("pending", "brick", 9))
        self.assertTrue((b.root / CO.QUEUE / (t["nonce"] + ".json")).is_file())


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement, tests=(name, "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_no_sold(self):
        self._knife("src/cockpit/chat_offers.py", '        left = o["n"] - sold(rows, o["id"]) if o else 0',
                    '        left = o["n"] if o else 0', "Ledger.test_p4_seller")

    def test_k2_no_reclean(self):
        self._knife("src/cockpit/room_snapshot.py", '        snap["island"] = clean_island(snap["island"])',
                    '        pass', "Pure.test_p2_compat")


if __name__ == "__main__":
    unittest.main()
