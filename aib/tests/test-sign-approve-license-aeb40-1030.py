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
L9 現有授權有沒處理的欄位 / tier 不是 all / 簽章過不了(本機竄改)⇒ 不彈窗、不承接
L10 簽發器在簽的途中動了現有授權 ⇒ 蓋回簽前那份、不換上;簽發器的 HOME / 狀態根指暫存
L11 授權簽發類別不碰時間窗:連按兩次 = 兩次指紋
刀(落下要紅):K1 拿掉內容比對 ⇒ L5;K2 拿掉 SIGN 碰授權的拒絕 ⇒ L2;K3 license_sign 改可開窗 ⇒ L3 / L11;K4 拿掉驗簽 ⇒ L6;
     K5 拿掉舊授權驗簽 ⇒ L9;K6 拿掉簽後比對現有授權 ⇒ L10
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

    def __init__(self, mutate=None, seed=SEED, rc=0, side=None):
        self.calls, self.mutate, self.seed, self.rc, self.side = [], mutate, seed, rc, side
        self.draft_seen = self.sign_env = None

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if "sign-entitlement" in argv:
            f = Path(argv[argv.index("sign-entitlement") + 1])
            obj = json.loads(f.read_text(encoding="utf-8"))
            self.draft_seen = dict(obj)
            self.device_arg = argv[argv.index("--device") + 1]
            self.sign_env = kw.get("env")
            if self.side:
                self.side()
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

    def put_current(self, obj, seed=SEED):
        self.lic.write_text(json.dumps(_sign(obj, seed) if seed else obj), encoding="utf-8")
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
        for part in ("plan-ultra", "ultra", EXP, did[:8], "this Mac", "captain-room, hostguard"):
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
        tampered = _sign(CUR)
        tampered["addons"] = tampered["addons"] + ["pixel-studio"]       # 本機改了 addons、簽章對不上
        for k, cur, seed in (("tampered", tampered, None), ("unsigned", dict(CUR), None), ("other_key", CUR, OTHER)):
            with self.subTest(k):
                self.put_current(cur, seed)
                ok, line, run, asked = self.run_one()
                self.assertFalse(ok)
                self.assertEqual(asked, [])
                self.assertEqual(run.signs(), [])


    def test_l10_signer_touches_current_license(self):
        evil = b'{"tier": "all", "plan": "ultra", "signature": "x"}'
        ok, line, run, _ = self.run_one(run=FakeRun(side=lambda: self.lic.write_bytes(evil)))
        self.assertFalse(ok)
        self.assertIn("current_touched", line)
        self.assertEqual(self.lic.read_text(encoding="utf-8"), self.before)
        e = run.sign_env
        self.assertNotEqual(e["HOME"], str(self.tmp))
        self.assertNotEqual(e["EZ2_AIB_HOME"], str(self.state))
        self.assertNotIn("EZ2_AIB_LOCALE", e)

    def test_l12_restore_safe_and_checked(self):
        evil = b'{"tier": "all", "plan": "ultra", "signature": "x"}'
        for n in range(4):                                      # 舊做法的可預測名預放符號連結:不該被用到
            (self.state / (".license.json.restore.%d" % (os.getpid() + n))).symlink_to(self.tmp / "nowhere")
        ok, line, run, _ = self.run_one(run=FakeRun(side=lambda: self.lic.write_bytes(evil)))
        self.assertFalse(ok)
        self.assertIn("current_touched", line)
        self.assertEqual(self.lic.read_text(encoding="utf-8"), self.before)
        self.assertFalse(self.lic.is_symlink())
        self.assertEqual(self.lic.stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(x.name for x in self.state.iterdir() if ".restore." in x.name and not x.is_symlink()), [])
        real = os.replace
        with mock.patch.object(SA.os, "replace", side_effect=lambda a, b: (_ for _ in ()).throw(OSError("ro"))
                               if ".restore." in str(a) else real(a, b)):
            ok, line, run, _ = self.run_one(run=FakeRun(side=lambda: self.lic.write_bytes(evil)))
        self.assertFalse(ok)
        self.assertIn("restore_failed", line)                   # 還原失敗不吞:要明講
        self.put_current(CUR)
        with mock.patch.object(SA.os, "replace", side_effect=lambda a, b: None if ".restore." in str(a) else real(a, b)):
            ok, line, run, _ = self.run_one(run=FakeRun(side=lambda: self.lic.write_bytes(evil)))
        self.assertFalse(ok)
        self.assertIn("restore_failed", line)                   # 換檔回報成功但檔沒變:靠覆讀抓到
        self.assertEqual([x.name for x in self.state.iterdir() if ".restore." in x.name and not x.is_symlink()], [])
        self.assertEqual([x.name for x in self.state.iterdir() if ".restore." in x.name and not x.is_symlink()], [])

    def test_l13_baseline_is_verified_bytes(self):
        from entitlement import store
        real = store.read_license_obj
        swapped = _sign(dict(CUR, note="swapped"))
        def swap(env=None):                                     # 基準位元組讀完、驗簽讀取前被換成另一份有效授權
            self.lic.write_text(json.dumps(swapped), encoding="utf-8")
            return real(env=env)
        with mock.patch.object(store, "read_license_obj", side_effect=swap):
            ok, line, run, asked = self.run_one()
        self.assertFalse(ok)
        self.assertEqual(asked, [])
        self.assertEqual(run.signs(), [])

    def test_l14_swapped_before_window(self):
        swapped = json.dumps(_sign(dict(CUR, note="swapped")))
        from entitlement import device as D
        real = D.device_id
        def swap(*a, **k):                                      # 驗簽之後、彈窗之前被換
            self.lic.write_text(swapped, encoding="utf-8")
            return real(*a, **k)
        with mock.patch.object(D, "device_id", side_effect=swap):
            ok, line, run, asked = self.run_one()
        self.assertFalse(ok)
        self.assertEqual(asked, [])
        self.assertEqual(run.signs(), [])
        self.assertEqual(self.lic.read_text(encoding="utf-8"), swapped)

    def test_l15_swapped_while_waiting_touchid(self):
        swapped = json.dumps(_sign(dict(CUR, note="swapped")))
        def finger(t):                                          # 等指紋期間被換
            self.lic.write_text(swapped, encoding="utf-8")
            return {"ok": True}
        ok, line, run, asked = self.run_one(touchid=finger)
        self.assertFalse(ok)
        self.assertEqual(run.signs(), [])
        self.assertEqual(run.calls, [])                          # 連私倉都不碰
        self.assertEqual(self.lic.read_text(encoding="utf-8"), swapped)

    def test_l11_never_uses_time_window(self):
        class EvilWindow:
            def __init__(self):
                self.touched = []

            def __getattr__(self, name):
                if name.startswith("__"):
                    raise AttributeError(name)
                self.touched.append(name)
                raise AssertionError("window." + name)
        _, _, run, asked = self.run_one(touchid=lambda t: {"ok": False, "why": "cancel"})
        r, evil, auths = None, EvilWindow(), []
        r = SA.AG.reason("release.license_approve", {"name": "x", "plan": "ultra", "addons": "-", "expires": EXP,
                                                     "device": "abcd1234"}, env=self.env)
        for _ in range(2):
            res = AG.ask(r, self.state, touchid=lambda t: auths.append(t) or {"ok": True}, window=evil)
            self.assertTrue(res["ok"], res)
        self.assertEqual(len(auths), 2)
        self.assertEqual(evil.touched, [])
        self.assertFalse(AG.window_open(r))


if __name__ == "__main__":
    unittest.main()
