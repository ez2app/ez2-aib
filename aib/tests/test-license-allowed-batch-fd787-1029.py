#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""批次版 aib license allowed(開艙說明 c 張:艦長室 2F 開頁問 26 件 ≈3.6 秒;草稿 DRAFT-license-allowed-batch-fd787-1029)。

E1 等價:方案 free/go/plus/pro/ultra × 單件 buy / rent 有效 / rent 過期 / trial / 沒有 × free_at(各級 / 沒給 / 寫錯)
   × 授權 正常 / 缺 / 壞 / 機器不對 ⇒ 批次那筆 == 單件 allowed()(欄位全等)
E2 一份快照:一批 26 件 ⇒ signing.verify_object 只被叫 2 次(current 一次、entitled_items 一次)
E3 壞一筆不拖累整批;重複 sku 各回一筆、順序照輸入;free_at 不是字串 ⇒ 那筆 bad-sku
E4 整包壞(不是 JSON / 沒 items / items 不是陣列)/ 超過 500 筆 / 超過 64 KB ⇒ rc 2、沒有 items
E5 CLI:--batch --json stdin → stdout,每筆十欄(單件九欄 + free_at)、不含簽章;不帶 --batch 的單件輸出不變
刀(複本):K1 allowed_many 每筆都用第一筆的 free_at ⇒ E1 紅;K2 拿掉快照(每筆重讀)⇒ E2 紅
"""
import base64
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE))
from entitlement import cli, device, ed25519, items, keys, signing  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

SEED = bytes(range(32))
PUB = signing.PREFIX + ed25519.public_key(SEED).hex()
RAW = "machine-fd787"
LID = "lic-batch-fd787"
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
PLANS = ("free", "go", "plus", "pro", "ultra")
FREE_ATS = (None, "free", "go", "Plus", "pro", "ultra", "gold", "")


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _signed(obj, seed=SEED):
    obj = dict(obj)
    obj.pop("signature", None)
    obj["signature"] = signing.PREFIX + base64.b64encode(ed25519.sign(seed, signing.canonical(obj))).decode("ascii")
    return obj


def _item(sku, kind="buy", days=None, raw=RAW):
    return {"sku": sku, "kind": kind, "expires_at": None if days is None else _iso(NOW + timedelta(days=days)),
            "machine_id": device.device_id(LID, raw), "tenant": "t-andy", "license_id": LID}


def _lic(plan_name, items_):
    return {"license_id": LID, "tier": "free", "plan": plan_name, "devices": [device.device_id(LID, RAW)], "items": items_}


ITEMS = [_item("x.buy", "buy"), _item("x.rent", "rent", days=10), _item("x.old", "rent", days=-3),
         _item("x.trial", "trial", days=5)]
SKUS = ("x.buy", "x.rent", "x.old", "x.trial", "x.none")


class Base(unittest.TestCase):
    def setUp(self):
        self._old = (keys.TRUSTED_PUBLIC_KEYS, keys.DRAFT_ACCEPT_ANY)
        keys.TRUSTED_PUBLIC_KEYS, keys.DRAFT_ACCEPT_ANY = (PUB,), False
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.env = {"EZ2_AIB_HOME": str(self.home)}

    def tearDown(self):
        keys.TRUSTED_PUBLIC_KEYS, keys.DRAFT_ACCEPT_ANY = self._old
        self._tmp.cleanup()

    def put(self, obj):
        p = self.home / "license.json"
        if obj is None:
            if p.exists():
                p.unlink()
            return
        p.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
        os.chmod(p, 0o600)

    def many(self, pairs, raw=RAW):
        return items.allowed_many(pairs, self.env, now=NOW, raw_machine=raw)

    def cli(self, argv, stdin_text):
        old = os.environ.get("EZ2_AIB_HOME")
        os.environ["EZ2_AIB_HOME"] = str(self.home)
        real_in = sys.stdin
        sys.stdin = io.TextIOWrapper(io.BytesIO(stdin_text.encode("utf-8")), encoding="utf-8")
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = cli.main(argv)
        finally:
            sys.stdin = real_in
            if old is None:
                os.environ.pop("EZ2_AIB_HOME", None)
            else:
                os.environ["EZ2_AIB_HOME"] = old
        return rc, buf.getvalue()


class Batch(Base):
    def test_e1_same_as_single(self):
        tampered = _signed(_lic("pro", ITEMS))
        tampered["plan"] = "ultra"   # 簽完改一個字
        licenses = [("plan-%s" % p, _signed(_lic(p, ITEMS)), RAW) for p in PLANS]
        licenses += [("missing", None, RAW), ("tampered", tampered, RAW),
                     ("other-machine", _signed(_lic("plus", ITEMS)), "another-mac")]
        pairs = [(s, f) for s in SKUS for f in FREE_ATS] + [("Bad SKU!", "free")]
        cells = 0
        for name, lic, raw in licenses:
            self.put(lic)
            got = self.many(pairs, raw=raw)
            self.assertEqual(len(got), len(pairs), name)
            for (sku, free_at), b in zip(pairs, got):
                one = items.allowed(sku, free_at, self.env, now=NOW, raw_machine=raw)
                self.assertEqual(b, one, "%s %s free_at=%r" % (name, sku, free_at))
                cells += 1
        self.assertEqual(cells, 8 * 41)
        # 有意義:這組格子確實跑到每一種來源與原因
        self.put(_signed(_lic("plus", ITEMS)))
        seen = {(r["source"], r["reason"]) for r in self.many(pairs)}
        for want in (("plan", "ok"), ("buy", "ok"), ("rent", "ok"), ("trial", "ok"),
                     ("none", "rent-expired"), ("none", "plan-too-low"), ("none", "not-owned"), ("none", "bad-sku")):
            self.assertIn(want, seen)

    def test_e2_one_snapshot(self):
        self.put(_signed(_lic("go", ITEMS)))
        pairs = [("x.n%02d" % i, "plus") for i in range(22)] + [(s, None) for s in SKUS[:4]]
        self.assertEqual(len(pairs), 26)
        real = signing.verify_object
        with mock.patch.object(signing, "verify_object", side_effect=real) as spy:
            got = self.many(pairs)
        self.assertEqual(spy.call_count, 2, "一批 26 件只該驗兩次簽章(current + entitled_items)")
        self.assertEqual([r["allowed"] for r in got[22:]], [True, True, False, True])
        with mock.patch.object(signing, "verify_object", side_effect=real) as spy:
            items.allowed("x.buy", None, self.env, now=NOW, raw_machine=RAW)
        self.assertEqual(spy.call_count, 2, "單件版行為不變")

    def test_e3_bad_row_and_order(self):
        self.put(_signed(_lic("free", ITEMS)))
        pairs = [("x.buy", None), ("Bad SKU!", "free"), ("x.buy", None), (None, None), ("x.rent", 5), ("x.rent", None)]
        got = self.many(pairs)
        self.assertEqual([(r["allowed"], r["reason"]) for r in got],
                         [(True, "ok"), (False, "bad-sku"), (True, "ok"), (False, "bad-sku"), (False, "bad-sku"), (True, "ok")])

    def test_e4_whole_batch_bad(self):
        self.put(_signed(_lic("free", ITEMS)))
        big = json.dumps({"items": [{"sku": "x.buy", "free_at": "free", "pad": "z" * 200}] * 400})
        cases = [("not json", "bad-input"), ("[]", "bad-input"), ('{"items": {"sku": "x.buy"}}', "bad-input"),
                 ("{}", "bad-input"), (json.dumps({"items": [{"sku": "x.buy"}] * 501}), "too-many"), (big, "too-big")]
        for text, err in cases:
            rc, out = self.cli(["allowed", "--batch", "--json"], text)
            body = json.loads(out)
            self.assertEqual((rc, body.get("ok"), body.get("error")), (2, False, err), text[:40])
            self.assertNotIn("items", body)
        rc, out = self.cli(["allowed", "--batch", "--json"], json.dumps({"items": [{"sku": "x.buy"}] * 500}))
        self.assertEqual((rc, len(json.loads(out)["items"])), (0, 500))

    def test_e5_cli(self):
        self.put(_signed(_lic("free", ITEMS)))
        req = {"items": [{"sku": "x.buy", "free_at": "plus"}, {"sku": "x.none"}, "not-a-dict"]}
        rc, out = self.cli(["allowed", "--batch", "--json"], json.dumps(req))
        body = json.loads(out)
        self.assertEqual((rc, body["ok"], len(body["items"])), (0, True, 3))
        fields = {"allowed", "source", "plan", "reason", "expires_at", "trial", "left_days", "was", "sku", "free_at"}
        for r in body["items"]:
            self.assertEqual(set(r), fields)
        self.assertEqual([(r["sku"], r["free_at"]) for r in body["items"]], [("x.buy", "plus"), ("x.none", None), (None, None)])
        self.assertEqual(body["items"][2]["reason"], "bad-sku")
        for bad in ("signature", "license_id", "machine_id"):
            self.assertNotIn(bad, out)
        rc1, single = self.cli(["allowed", "x.buy", "--free-at", "plus", "--json"], "")
        self.assertEqual(rc1, 0)
        self.assertEqual(set(json.loads(single)), fields - {"free_at"})


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, cell, expect):
        name = Path(__file__).name
        with knife_copy(REPO, "src/entitlement/items.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), cell], cwd=str(root), max_procs=20, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        text = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + text)
        self.assertIn("AssertionError", text, "紅要紅在斷言\n" + text)
        self.assertIn(expect, text, "紅的原因要是預期那條\n" + text)

    def test_k1_first_free_at_only(self):
        self._knife("        res.append(_decide(sku, free_at, now, plan_now, ent_now))\n",
                    "        res.append(_decide(sku, pairs[0][1], now, plan_now, ent_now))\n",
                    "Batch.test_e1_same_as_single", "free_at=")

    def test_k2_no_snapshot(self):
        self._knife("            if key not in memo:\n", "            if True:\n",
                    "Batch.test_e2_one_snapshot", "只該驗兩次簽章")


if __name__ == "__main__":
    unittest.main()
