#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib-sign-approve 也收授權申請 LICENSE-<名>-<席>-<MMDD>.json(10-30 Andy 23:5x 選 1;#449 Codex r1 兩 High 後改)。

L1 申請檔只收 plan / priv / rev 三欄;多欄、方案不在五級、rev 不是十六進位、不是 JSON、舊 .sh 名 ⇒ 跳過
L2 SIGN 腳本內文碰 aib-license.py / license.json ⇒ 拒、不彈窗(不能換前綴走發版簽章)
L3 視窗字由核心組:名稱、方案、現有到期、綁機值前 8 碼;類別 = 授權簽發(高風險、不開時間窗)
L4 按過 ⇒ 私倉工具只簽核心那份(--device = 核心算的值);換上後 plan 真的是 ultra、綁這台;舊檔有備份
L5 簽出來的內容跟視窗不同(偷改方案 / 多一件外掛 / 換綁機)⇒ 不換上
L6 簽章過不了信任公鑰 ⇒ 不換上
L7 申請檔只讀一次:plan() 之後換檔,這一輪照舊用讀到的那份
L8 取消 ⇒ 不簽;鎖被拿著 ⇒ 不簽
L9 現有授權有沒處理的欄位 / tier 不是 all ⇒ 不彈窗
刀(落下要紅):K1 拿掉內容比對 ⇒ L5;K2 拿掉 SIGN 碰授權的拒絕 ⇒ L2;K3 license_sign 改可開窗 ⇒ L3;K4 拿掉驗簽 ⇒ L6
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools" / "release"))

import approve_gate as AG  # noqa: E402
import sign_approve as SA  # noqa: E402
import touchid_approve as TA  # noqa: E402
from entitlement import device, ed25519, keys, signing  # noqa: E402

SEED = bytes(range(32))
OTHER = bytes(range(1, 33))
PUB = "ed25519:" + ed25519.public_key(SEED).hex()
MACHINE = "TEST-MACHINE-UUID-0001"
EXP = "2027-10-17T05:36:46+00:00"
REQ = {"plan": "ultra", "priv": "/priv/repo", "rev": "2ef08b6d0"}
NAME = "LICENSE-plan-ultra-aeb40-1030.json"
CUR = {"tier": "all", "addons": ["captain-room", "hostguard"], "expires_at": EXP, "note": "Andy MacBook Pro 16",
       "issued_at": "2026-10-23T13:07:36+00:00"}


def _sign(obj, seed=SEED):
    body = {k: v for k, v in obj.items() if k != "signature"}
    sig = ed25519.sign(seed, signing.canonical(body))
    import base64
    return dict(body, signature="ed25519:" + base64.b64encode(sig).decode())


class FakeRun:
    """git archive / tar 回成功;sign-entitlement 照 mutate 改內容再用 seed 簽,寫回草稿。"""

    def __init__(self, mutate=None, seed=SEED, rc=0):
        self.calls, self.mutate, self.seed, self.rc = [], mutate, seed, rc
        self.draft_seen = None

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if "sign-entitlement" in argv:
            f = Path(argv[argv.index("sign-entitlement") + 1])
            obj = json.loads(f.read_text(encoding="utf-8"))
            self.draft_seen = dict(obj)
            self.device_arg = argv[argv.index("--device") + 1]
            obj["issued_at"] = "2026-10-31T00:00:00+00:00"
            if self.mutate:
                self.mutate(obj)
            f.write_text(json.dumps(_sign(obj, self.seed)), encoding="utf-8")
            return mock.Mock(returncode=self.rc, stdout="signed\n", stderr="")
        return mock.Mock(returncode=0, stdout=b"", stderr=b"")

    def signs(self):
        return [c for c in self.calls if "sign-entitlement" in c]


class Box(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-signlic-")
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_AIB_HOME": str(self.state),
                    "EZ2_AIB_LOCALE": "en", "HOME": str(self.tmp)}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        old = (keys.TRUSTED_PUBLIC_KEYS, keys.DRAFT_ACCEPT_ANY)
        keys.TRUSTED_PUBLIC_KEYS, keys.DRAFT_ACCEPT_ANY = (PUB,), False
        self.addCleanup(lambda: setattr(keys, "TRUSTED_PUBLIC_KEYS", old[0]) or setattr(keys, "DRAFT_ACCEPT_ANY", old[1]))
        self.lic = self.state / "license.json"
        self.put_current(CUR)

    def put_current(self, obj):
        self.lic.write_text(json.dumps(_sign(obj)), encoding="utf-8")
        os.chmod(self.lic, 0o600)
        self.before = self.lic.read_text(encoding="utf-8")

    def write(self, req=REQ, name=NAME):
        f = self.tmp / name
        f.write_text(req if isinstance(req, str) else json.dumps(req), encoding="utf-8")
        return f

    def job(self):
        job, why = SA.plan(self.write())
        self.assertIsNone(why)
        return job

    def run_one(self, run=None, touchid=None, job=None):
        asked = []
        run = run or FakeRun()
        ok, line = SA.one(job or self.job(), env=self.env, run=run, raw_machine=MACHINE,
                          head_fn=lambda r, b: self.fail("授權申請不該查分支頭"),
                          touchid=touchid or (lambda t: asked.append(t) or {"ok": True}))
        return ok, line, run, asked

    def test_l1_request(self):
        j = self.job()
        self.assertEqual((j["kind"], j["name"], j["plan"], j["priv"], j["rev"]),
                         ("license", "plan-ultra", "ultra", "/priv/repo", "2ef08b6d0"))
        bad = {"extra": dict(REQ, expires="2099-01-01T00:00:00+00:00"), "plan": dict(REQ, plan="platinum"),
               "rev": dict(REQ, rev="main; rm -rf"), "missing": {"plan": "ultra"}, "notjson": "{plan: ultra"}
        for k, req in bad.items():
            with self.subTest(k):
                self.assertEqual(SA.plan(self.write(req)), (None, "no_lic_vars"))
        for name in ("LICENSE-plan-ultra-aeb40-1030.sh", "LICENSE-plan-ultra-zzzzz-1030.json"):
            with self.subTest(name):
                self.assertEqual(SA.plan(self.write(name=name)), (None, "bad_name"))

    def test_l2_sign_script_touching_license_refused(self):
        body = ("#!/bin/bash\nBRANCH=release/x-0.1.0\nPIN_SHA=abc1234\n"
                "git clone git@github.com:ez2app/aib-plugins.git \"$T\"\n")
        for extra in ("python3 aib-license.py sign-entitlement x\n", "cp x ~/.ez2-aib/license.json\n"):
            with self.subTest(extra):
                f = self.write(body + extra, "SIGN-x-0.1.0-aeb40-1030.sh")
                self.assertEqual(SA.plan(f), (None, "sign_touches_license"))
        f = self.write(body, "SIGN-x-0.1.0-aeb40-1030.sh")
        self.assertIsNone(SA.plan(f)[1])

    def test_l3_window_text_and_category(self):
        ok, line, run, asked = self.run_one(touchid=None)
        r = asked[0]
        did = run.device_arg
        for part in ("plan-ultra", "ultra", EXP, did[:8], "this Mac"):
            self.assertIn(part, r)
        self.assertEqual(r.category, "license_sign")
        self.assertTrue(r.high_risk)
        self.assertEqual(AG.CATEGORIES["license_sign"], {"high_risk": True, "max_window": 0})

    def test_l4_signs_core_draft_and_installs(self):
        ok, line, run, asked = self.run_one()
        self.assertTrue(ok, line)
        self.assertEqual(len(asked), 1)
        self.assertEqual(len(run.signs()), 1)
        d = run.draft_seen
        self.assertEqual(d["plan"], "ultra")
        self.assertEqual(d["devices"], [device.device_id(d["license_id"], MACHINE)])
        self.assertEqual(run.device_arg, d["devices"][0])
        self.assertEqual({k: d[k] for k in ("tier", "addons", "expires_at", "note")},
                         {k: CUR[k] for k in ("tier", "addons", "expires_at", "note")})
        from entitlement import plan as EPLAN
        got = EPLAN.current(self.env, raw_machine=MACHINE)
        self.assertEqual((got["plan"], got["machine_bound"], got["source"]), ("ultra", True, "license"))
        self.assertEqual(len(list(self.state.glob("license.json.bak-*"))), 1)
        self.assertEqual(len(list((self.state / "pending" / "sign-logs").glob("LICENSE-plan-ultra-ultra-*.log"))), 1)

    def test_l5_signed_differs_from_shown(self):
        muts = {"plan": lambda o: o.update(plan="pro"), "addon": lambda o: o["addons"].append("extra"),
                "device": lambda o: o.update(devices=["0" * 32]), "expiry": lambda o: o.update(expires_at="2099-01-01T00:00:00+00:00")}
        for k, m in muts.items():
            with self.subTest(k):
                ok, line, _, _ = self.run_one(run=FakeRun(mutate=m))
                self.assertFalse(ok)
                self.assertIn("not_as_shown", line)
                self.assertEqual(self.lic.read_text(encoding="utf-8"), self.before)

    def test_l6_bad_signature(self):
        ok, line, _, _ = self.run_one(run=FakeRun(seed=OTHER))
        self.assertFalse(ok)
        self.assertIn("bad_signature", line)
        self.assertEqual(self.lic.read_text(encoding="utf-8"), self.before)

    def test_l7_request_read_once(self):
        f = self.write()
        job, _ = SA.plan(f)
        f.write_text(json.dumps(dict(REQ, plan="go")), encoding="utf-8")
        ok, line, run, asked = self.run_one(job=job)
        self.assertTrue(ok, line)
        self.assertEqual(run.draft_seen["plan"], "ultra")
        self.assertIn("ultra", asked[0])

    def test_l8_cancel_and_busy(self):
        ok, _, run, _ = self.run_one(touchid=lambda t: {"ok": False, "why": "cancel"})
        self.assertFalse(ok)
        self.assertEqual(run.signs(), [])
        with TA.one_at_a_time(AG.lock_path(self.state)):
            ok, _, run, _ = self.run_one()
        self.assertFalse(ok)
        self.assertEqual(run.signs(), [])
        self.assertEqual(self.lic.read_text(encoding="utf-8"), self.before)

    def test_l9_current_license_unhandled(self):
        for k, cur in {"extra": dict(CUR, activation_url="https://x"), "tier": dict(CUR, tier="single"),
                       "tz": dict(CUR, expires_at="2027-10-17T05:36:46")}.items():
            with self.subTest(k):
                self.put_current(cur)
                ok, line, run, asked = self.run_one()
                self.assertFalse(ok)
                self.assertEqual(asked, [])
                self.assertEqual(run.signs(), [])


if __name__ == "__main__":
    unittest.main()
