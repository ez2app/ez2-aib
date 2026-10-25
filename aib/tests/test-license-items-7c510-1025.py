#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""單買 / 單項訂閱 items(@abb89 10-25 23:2x 定欄位;Andy 23:2x「同時支援單買、或單訂閱使用期限」)+ aib license allowed。

I1 buy 合法、綁這台 ⇒ allowed source=buy;rent 未過期 ⇒ source=rent 帶 expires_at;rent 過期 ⇒ 不給 reason=rent-expired
I2 方案夠(free_at)⇒ source=plan,不看 items;方案不夠又沒買 ⇒ plan-too-low;沒給 free_at 又沒買 ⇒ not-owned
I3 fail-closed 逐筆:machine_id 是別台 / license_id 對不上 / kind 寫錯 / rent 缺 expires_at / buy 帶 expires_at / tenant 空 / sku 形狀不合 ⇒ 該筆當不存在
I4 整張:改 items 一個字 ⇒ 簽章驗不過 ⇒ items 空 reason=invalid;沒檔 ⇒ missing;items 不是陣列 ⇒ invalid
I5 CLI allowed --json:九欄(含 trial / left_days / was)、不含 signature;沒給 sku ⇒ rc 2
刀(複本):K1 拿掉 machine_id 比對 ⇒ I3 紅;K2 拿掉 rent 到期判斷 ⇒ I1 紅
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

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE))
from entitlement import cli, device, ed25519, items, keys, signing  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

SEED = bytes(range(32))
PUB = signing.PREFIX + ed25519.public_key(SEED).hex()
RAW = "machine-7c510"
LID = "lic-items-7c510"
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _signed(obj, seed=SEED):
    obj = dict(obj)
    obj.pop("signature", None)
    obj["signature"] = signing.PREFIX + base64.b64encode(ed25519.sign(seed, signing.canonical(obj))).decode("ascii")
    return obj


def _item(sku="elevator.lv2", kind="buy", days=None, raw=RAW, lid=LID, **over):
    it = {"sku": sku, "kind": kind, "expires_at": None if days is None else _iso(NOW + timedelta(days=days)),
          "machine_id": device.device_id(lid, raw), "tenant": "t-andy", "license_id": LID}
    it.update(over)
    return it


def _lic(plan_name="free", items_=None):
    obj = {"license_id": LID, "tier": "free", "plan": plan_name, "devices": [device.device_id(LID, RAW)]}
    if items_ is not None:
        obj["items"] = items_
    return obj


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
        p.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
        os.chmod(p, 0o600)

    def ok(self, sku, free_at=None, now=NOW):
        return items.allowed(sku, free_at, self.env, now=now, raw_machine=RAW)

    def skus(self):
        return sorted(it["sku"] for it in items.entitled_items(self.env, NOW, RAW)["items"])


class Items(Base):
    def test_i1_buy_rent(self):
        self.put(_signed(_lic("free", [_item("elevator.lv2", "buy"), _item("desk.dual", "rent", days=10)])))
        r = self.ok("elevator.lv2")
        self.assertEqual((r["allowed"], r["source"], r["expires_at"]), (True, "buy", None), r)
        r = self.ok("desk.dual")
        self.assertEqual((r["allowed"], r["source"]), (True, "rent"), r)
        self.assertTrue(r["expires_at"].startswith("2026-11-04"))
        self.assertEqual((r["left_days"], r["trial"], r["was"]), (10, False, None), r)
        r = self.ok("desk.dual", now=NOW + timedelta(days=11))
        self.assertEqual((r["allowed"], r["source"], r["reason"], r["was"]), (False, "none", "rent-expired", "rent-expired"), r)

    def test_i2_plan_path(self):
        self.put(_signed(_lic("pro", [])))
        r = self.ok("desk.dual", free_at="Plus")
        self.assertEqual((r["allowed"], r["source"], r["plan"], r["trial"], r["left_days"]), (True, "plan", "pro", False, None), r)
        r = self.ok("fishtank", free_at="ultra")
        self.assertEqual((r["allowed"], r["reason"]), (False, "plan-too-low"), r)
        r = self.ok("fishtank")
        self.assertEqual((r["allowed"], r["reason"]), (False, "not-owned"), r)
        r = self.ok("Bad SKU!", free_at="free")
        self.assertEqual((r["allowed"], r["reason"]), (False, "bad-sku"), r)

    def test_i3_fail_closed_per_item(self):
        bad = [
            _item("a.other-machine", "buy", raw="another-mac"),
            _item("b.other-license", "buy", license_id="lic-other"),
            _item("c.bad-kind", "lease"),
            _item("d.rent-no-expiry", "rent"),
            _item("e.buy-with-expiry", "buy", days=5),
            _item("f.no-tenant", "buy", tenant=""),
            {"sku": "G.UPPER", "kind": "buy", "expires_at": None, "machine_id": device.device_id(LID, RAW), "tenant": "t", "license_id": LID},
            "not-a-dict",
            _item("h.good", "buy"),
        ]
        self.put(_signed(_lic("free", bad)))
        self.assertEqual(self.skus(), ["h.good"])
        for sku in ("a.other-machine", "b.other-license", "c.bad-kind", "d.rent-no-expiry", "e.buy-with-expiry", "f.no-tenant"):
            self.assertFalse(self.ok(sku)["allowed"], sku)

    def test_i4_whole_license(self):
        self.assertEqual(items.entitled_items(self.env, NOW, RAW)["reason"], "missing")
        obj = _signed(_lic("free", [_item("x.one", "buy")]))
        obj["items"][0]["sku"] = "x.two"   # 簽完改 items 一個字
        self.put(obj)
        ent = items.entitled_items(self.env, NOW, RAW)
        self.assertEqual((ent["reason"], ent["items"]), ("invalid", []))
        self.assertEqual(self.ok("x.two")["reason"], "invalid")
        self.put(_signed(_lic("free", {"sku": "x.one"})))
        self.assertEqual(items.entitled_items(self.env, NOW, RAW)["reason"], "invalid")
        self.put(_signed(_lic("free")))
        self.assertEqual(items.entitled_items(self.env, NOW, RAW)["reason"], "none")

    def test_i5_cli(self):
        self.put(_signed(_lic("free", [_item("elevator.lv2", "buy")])))
        old = os.environ.get("EZ2_AIB_HOME")
        os.environ["EZ2_AIB_HOME"] = str(self.home)
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["allowed", "elevator.lv2", "--free-at", "plus", "--json"])
            err = io.StringIO()
            with redirect_stdout(io.StringIO()):
                sys.stderr, real = err, sys.stderr
                try:
                    rc2 = cli.main(["allowed", "--json"])
                finally:
                    sys.stderr = real
        finally:
            if old is None:
                os.environ.pop("EZ2_AIB_HOME", None)
            else:
                os.environ["EZ2_AIB_HOME"] = old
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual(set(out), {"allowed", "source", "plan", "reason", "expires_at", "trial", "left_days", "was", "sku"})
        self.assertNotIn("signature", buf.getvalue())
        self.assertIn(out["source"], ("buy", "plan", "none"))   # 真機器 id 不一定對得上測試的 RAW,只驗形狀
        self.assertEqual(rc2, 2)


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

    def test_k1_machine_check_removed(self):
        self._knife('    if did is None or raw.get("machine_id") != did:\n', "    if False:\n", "Items.test_i3_fail_closed_per_item", "a.other-machine")

    def test_k2_rent_expiry_removed(self):
        self._knife('"active": now <= exp}', '"active": True}', "Items.test_i1_buy_rent", "rent-expired")


if __name__ == "__main__":
    unittest.main()
