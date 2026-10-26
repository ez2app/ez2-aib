#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""方案級別軸 entitlement/plan.py + aib license plan --json(10-25 the maintainer 定 Free / Plus / Pro / Ultra、23:1x 加 Go;@abb89 裁另開 plan 軸)。

P1 沒檔 ⇒ free / missing;沒寫 plan 的舊授權 ⇒ free / ok;壞 JSON ⇒ free / invalid
P2 信任公鑰簽、綁本機的 pro ⇒ pro;改一欄 / 別把鑰匙簽 ⇒ free / invalid;寫錯級別名 ⇒ free / invalid
P3 付費級沒綁機器 ⇒ free / unbound;綁別台 ⇒ free / device-not-bound
P4 過期:72h 內 grace 照級別,超過 ⇒ free / expired
P5 試用檔:主鑰或試用鑰簽、trial=true、≤7 天 ⇒ 該級別;沒 trial / 缺日期 ⇒ invalid;到期不給寬限
P6 試用鑰:超過 7 天 ⇒ invalid;簽 license.json(正式檔)⇒ invalid(試用鑰簽不出正式級別)
P7 兩份取高:正式 plus + 試用 pro ⇒ pro;正式 ultra + 試用 pro ⇒ ultra
P8 at_least:照級別比(五級含 go);寫錯名 ⇒ False
P9 CLI plan --json:只輸出六欄、不含 signature
刀(複本):K1 拿掉「沒綁不給付費級」⇒ P3 紅;K2 拿掉試用鑰天數上限 ⇒ P6 紅;K3 正式檔也認試用鑰 ⇒ P6 紅
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
from entitlement import cli, device, ed25519, keys, plan, signing  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

SEED = bytes(range(32))
TSEED = bytes(range(2, 34))
OTHER = bytes(range(1, 33))
PUB = signing.PREFIX + ed25519.public_key(SEED).hex()
TPUB = signing.PREFIX + ed25519.public_key(TSEED).hex()
RAW = "machine-7c510"
LID = "lic-plan-7c510"
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _signed(obj, seed=SEED):
    obj = dict(obj)
    obj.pop("signature", None)
    obj["signature"] = signing.PREFIX + base64.b64encode(ed25519.sign(seed, signing.canonical(obj))).decode("ascii")
    return obj


def _lic(plan_name="pro", raw=RAW, **extra):
    obj = {"license_id": LID, "tier": "free", "plan": plan_name, "devices": [device.device_id(LID, raw)]}
    obj.update(extra)
    return obj


def _trial(plan_name="pro", days=7, start=NOW - timedelta(days=1), **extra):
    obj = _lic(plan_name, trial=True, issued_at=_iso(start), expires_at=_iso(start + timedelta(days=days)))
    obj.update(extra)
    return obj


class Base(unittest.TestCase):
    def setUp(self):
        self._old = (keys.TRUSTED_PUBLIC_KEYS, keys.DRAFT_ACCEPT_ANY, keys.TRUSTED_TRIAL_KEYS)
        keys.TRUSTED_PUBLIC_KEYS, keys.DRAFT_ACCEPT_ANY, keys.TRUSTED_TRIAL_KEYS = (PUB,), False, (TPUB,)
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.env = {"EZ2_AIB_HOME": str(self.home)}

    def tearDown(self):
        keys.TRUSTED_PUBLIC_KEYS, keys.DRAFT_ACCEPT_ANY, keys.TRUSTED_TRIAL_KEYS = self._old
        self._tmp.cleanup()

    def put(self, obj, name="license.json"):
        p = self.home / name
        p.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
        os.chmod(p, 0o600)

    def cur(self, now=NOW, raw=RAW):
        return plan.current(self.env, now=now, raw_machine=raw)


class Plan(Base):
    def test_p1_missing_legacy_broken(self):
        r = self.cur()
        self.assertEqual((r["plan"], r["reason"]), ("free", "missing"))
        self.put(_signed({"license_id": LID, "tier": "all"}))
        self.assertEqual((self.cur()["plan"], self.cur()["reason"]), ("free", "ok"))
        self.put("{not json")
        self.assertEqual((self.cur()["plan"], self.cur()["reason"]), ("free", "invalid"))

    def test_p2_signed_bound(self):
        self.put(_signed(_lic("Pro")))
        r = self.cur()
        self.assertEqual((r["plan"], r["reason"], r["machine_bound"], r["source"]), ("pro", "ok", True, "license"))
        bad = _signed(_lic("pro"))
        bad["plan"] = "ultra"
        self.put(bad)
        self.assertEqual((self.cur()["plan"], self.cur()["reason"]), ("free", "invalid"))
        self.put(_signed(_lic("pro"), seed=OTHER))
        self.assertEqual((self.cur()["plan"], self.cur()["reason"]), ("free", "invalid"))
        self.put(_signed(_lic("platinum")))
        self.assertEqual((self.cur()["plan"], self.cur()["reason"]), ("free", "invalid"))

    def test_p3_unbound_or_other_machine(self):
        obj = _lic("pro")
        del obj["devices"]
        self.put(_signed(obj))
        self.assertEqual((self.cur()["plan"], self.cur()["reason"]), ("free", "unbound"))
        self.put(_signed(_lic("pro", raw="another-mac")))
        self.assertEqual((self.cur()["plan"], self.cur()["reason"]), ("free", "device-not-bound"))

    def test_p4_expiry_grace(self):
        self.put(_signed(_lic("plus", expires_at=_iso(NOW - timedelta(hours=10)))))
        self.assertEqual((self.cur()["plan"], self.cur()["reason"]), ("plus", "grace"))
        self.put(_signed(_lic("plus", expires_at=_iso(NOW - timedelta(hours=73)))))
        self.assertEqual((self.cur()["plan"], self.cur()["reason"]), ("free", "expired"))

    def test_p5_trial_file(self):
        self.put(_signed(_trial("pro")), plan.TRIAL_FILENAME)
        r = self.cur()
        self.assertEqual((r["plan"], r["trial"], r["source"]), ("pro", True, "trial"))
        self.put(_signed(_trial("pro"), seed=TSEED), plan.TRIAL_FILENAME)
        self.assertEqual(self.cur()["plan"], "pro")
        no_flag = _trial("pro")
        del no_flag["trial"]
        self.put(_signed(no_flag), plan.TRIAL_FILENAME)
        self.assertEqual((self.cur()["plan"], self.cur()["reason"]), ("free", "invalid"))
        no_date = _trial("pro")
        del no_date["issued_at"]
        self.put(_signed(no_date, seed=TSEED), plan.TRIAL_FILENAME)
        self.assertEqual((self.cur()["plan"], self.cur()["reason"]), ("free", "invalid"))
        self.put(_signed(_trial("pro", start=NOW - timedelta(days=7, hours=1)), seed=TSEED), plan.TRIAL_FILENAME)
        self.assertEqual((self.cur()["plan"], self.cur()["reason"]), ("free", "expired"))

    def test_p6_trial_key_limits(self):
        self.put(_signed(_trial("ultra", days=30), seed=TSEED), plan.TRIAL_FILENAME)
        self.assertEqual((self.cur()["plan"], self.cur()["reason"]), ("free", "invalid"))
        self.put(_signed(_trial("ultra"), seed=TSEED))   # 試用鑰簽正式檔
        r = plan.evaluate(_signed(_trial("ultra"), seed=TSEED), "license", False, self.env, NOW, RAW)
        self.assertEqual((r["plan"], r["reason"]), ("free", "invalid"))
        self.assertEqual(self.cur()["plan"], "free")

    def test_p7_highest_wins(self):
        self.put(_signed(_lic("plus")))
        self.put(_signed(_trial("pro"), seed=TSEED), plan.TRIAL_FILENAME)
        self.assertEqual((self.cur()["plan"], self.cur()["source"]), ("pro", "trial"))
        self.put(_signed(_lic("ultra")))
        self.assertEqual((self.cur()["plan"], self.cur()["source"]), ("ultra", "license"))

    def test_p8_at_least(self):
        self.put(_signed(_lic("pro")))
        al = lambda n: plan.at_least(n, self.env, now=NOW, raw_machine=RAW)  # noqa: E731
        self.assertEqual([al(n) for n in ("free", "go", "plus", "pro", "ultra")], [True, True, True, True, False])
        self.assertFalse(al("platinum"))
        self.assertFalse(al(None))

    def test_p9_cli_json(self):
        self.put(_signed(_lic("plus")))
        old = os.environ.get("EZ2_AIB_HOME")
        os.environ["EZ2_AIB_HOME"] = str(self.home)
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["plan", "--json"])
        finally:
            if old is None:
                os.environ.pop("EZ2_AIB_HOME", None)
            else:
                os.environ["EZ2_AIB_HOME"] = old
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual(set(out), {"plan", "reason", "expires_at", "trial", "machine_bound", "source"})
        self.assertNotIn("signature", buf.getvalue())
        self.assertIn(out["plan"], plan.PLANS)   # 真機器 id 不一定對得上測試的 RAW,只驗形狀


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/entitlement/plan.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), cell], cwd=str(root), max_procs=20, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_unbound_gives_paid(self):
        self._knife('    if why == "unbound":\n', '    if False:\n', "Plan.test_p3_unbound_or_other_machine")

    def test_k2_trial_cap_removed(self):
        self._knife('if who == "trial" and expires - issued >', 'if False and expires - issued >', "Plan.test_p6_trial_key_limits")

    def test_k3_main_file_accepts_trial_key(self):
        self._knife('allow_trial_key=is_trial_file)', 'allow_trial_key=True)', "Plan.test_p6_trial_key_limits")


if __name__ == "__main__":
    unittest.main()
