#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""tools/cloud-host/robot.py(Hetzner Robot API)沙盒測試,不連 Hetzner(#528 M2 / H1 / L1)。

R1 plan 模式(沒 --apply)不讀金鑰、不過閘、不打 API
R2 rescue --apply:先過閘(approve 被叫到,帶 action / server / fp)→ 才讀金鑰 → 送對的表單(os + authorized_key[]);密碼不在 argv / 輸出
R3 閘沒過 ⇒ rc 2、沒讀金鑰、沒打 API
R4 status / keys 唯讀:不過閘,直接讀金鑰打 GET
R5 回覆 password 欄遮罩;Authorization 標頭 / 密碼不出現在輸出
R6 HTTPError(401)路徑:rc 非 0、輸出有狀態碼、沒密碼
R7 rescue --apply 沒 --fingerprint ⇒ 拒(no_fingerprint),不過閘、不讀金鑰
R8 真的 approve() 走 approve_gate:site cloud_robot.apply 有登記在 tools/cloud-host/robot.py、類別 host_rescue 高風險
刀:K1 拿掉 mask ⇒ R5 紅;K2 apply 不過閘 ⇒ R3 紅;K3 plan 也打 API ⇒ R1 紅;K4 沒 fingerprint 也放行 ⇒ R7 紅
"""
import contextlib
import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TOOL = REPO / "tools" / "cloud-host"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "src" / "bootstrap"))
from aib_knife_copy import knife_copy  # noqa: E402
import robot  # noqa: E402

CREDS = ("#ws+abc", "S3cr3tPW")


class Resp:
    def __init__(self, st, body):
        self.status, self._b = st, json.dumps(body).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Fake:
    def __init__(self, approve_ok=True):
        self.calls, self.approvals, self.cred_reads, self.approve_ok = [], [], 0, approve_ok

    def opener(self, req, timeout=0):
        self.calls.append((req.get_method(), req.full_url, req.data.decode() if req.data else None, req.get_header("Authorization")))
        if "/boot/" in req.full_url and req.get_method() == "POST":
            return Resp(200, {"rescue": {"active": True, "password": "ROOTPW123", "os": "linux"}})
        if "/reset/" in req.full_url and req.get_method() == "POST":
            return Resp(200, {"reset": {"type": "hw"}})
        if req.full_url.endswith("/key"):
            return Resp(200, [{"key": {"name": "andy", "fingerprint": "aa:bb"}}])
        if "/server/999" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error":{"status":401,"code":"UNAUTHORIZED","password":"x"}}'))
        return Resp(200, {"server": {"server_number": 3109421}, "rescue": {"active": False}, "reset": {"type": ["hw", "sw"]}})

    def creds(self):
        self.cred_reads += 1
        return CREDS

    def approve(self, action, server, fp):
        self.approvals.append((action, server, fp))
        return None if self.approve_ok else "touchid_cancel"

    def run(self, argv, mod=robot):
        out = io.StringIO()
        rc = mod.main(argv, creds_fn=self.creds, opener=self.opener, approve_fn=self.approve, out=lambda s: out.write(s + "\n"))
        text = out.getvalue()
        for v in CREDS:
            assert v not in text, "金鑰進了輸出"
        assert "Basic " not in text, "Authorization 進了輸出"
        return rc, text


class Robot(unittest.TestCase):
    def test_r1_plan_touches_nothing(self):
        f = Fake()
        rc, out = f.run(["rescue", "3109421", "--fingerprint", "aa:bb"])
        self.assertEqual((rc, f.calls, f.approvals, f.cred_reads), (0, [], [], 0), out)
        self.assertIn('"apply": false', out)
        self.assertIn("不支援回滾", out)

    def test_r2_apply_gate_then_creds_then_post(self):
        f = Fake()
        rc, out = f.run(["rescue", "3109421", "--fingerprint", "aa:bb", "--apply"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(f.approvals, [("rescue", "3109421", "aa:bb")])
        self.assertEqual(f.cred_reads, 1)
        self.assertEqual(len(f.calls), 1)
        m, url, body, auth = f.calls[0]
        self.assertEqual((m, body), ("POST", "os=linux&authorized_key%5B%5D=aa%3Abb"))
        self.assertTrue(url.endswith("/boot/3109421/rescue"))
        self.assertNotIn(CREDS[1], url + (body or ""))

    def test_r3_gate_refused_no_creds_no_api(self):
        f = Fake(approve_ok=False)
        rc, out = f.run(["reset", "3109421", "--apply"])
        self.assertEqual((rc, f.calls, f.cred_reads), (2, [], 0), out)
        self.assertEqual(f.approvals, [("reset:hw", "3109421", None)])
        self.assertIn("touchid_cancel", out)

    def test_r4_readonly_no_gate(self):
        for argv in (["status", "3109421"], ["keys"]):
            f = Fake()
            rc, out = f.run(argv)
            self.assertEqual((rc, f.approvals), (0, []), out)
            self.assertGreaterEqual(f.cred_reads, 1)
            self.assertTrue(all(c[0] == "GET" for c in f.calls))

    def test_r5_mask(self):
        f = Fake()
        rc, out = f.run(["rescue", "3109421", "--fingerprint", "aa:bb", "--apply"])
        self.assertNotIn("ROOTPW123", out)
        self.assertIn('"password": "***"', out)

    def test_r6_http_error(self):
        f = Fake()
        rc, out = f.run(["status", "999"])
        self.assertEqual(rc, 1)
        self.assertIn("401", out)
        self.assertNotIn('"password": "x"', out)

    def test_r7_apply_needs_fingerprint(self):
        f = Fake()
        rc, out = f.run(["rescue", "3109421", "--apply"])
        self.assertEqual((rc, f.calls, f.approvals, f.cred_reads), (2, [], [], 0), out)
        self.assertIn("no_fingerprint", out)

    def test_r8_registered_in_approve_gate(self):
        import approve_gate as AG
        spec = AG.SITES["cloud_robot.apply"]
        self.assertEqual((spec["mod"], spec["cat"]), ("cloud-host/robot.py", "host_rescue"))
        self.assertTrue(AG.CATEGORIES["host_rescue"]["high_risk"])
        self.assertTrue((TOOL / "robot.py").is_file())
        self.assertIn('"cloud_robot.apply"', (TOOL / "robot.py").read_text(encoding="utf-8"))


class Knives(unittest.TestCase):
    def _run_in(self, tree, argv, approve_ok=True):
        import importlib.util
        spec = importlib.util.spec_from_file_location("robot_knife", str(Path(tree) / "tools" / "cloud-host" / "robot.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        f = Fake(approve_ok)
        out = io.StringIO()
        rc = mod.main(argv, creds_fn=f.creds, opener=f.opener, approve_fn=f.approve, out=lambda s: out.write(s + "\n"))
        return rc, out.getvalue(), f

    def test_k1_no_mask(self):
        with knife_copy(REPO, "tools/cloud-host/robot.py",
                        'return {k: ("***" if any(m in k.lower() for m in MASK_KEYS) else mask(v)) for k, v in obj.items()}',
                        'return {k: mask(v) for k, v in obj.items()}') as tree:
            rc, out, f = self._run_in(tree, ["rescue", "3109421", "--fingerprint", "aa:bb", "--apply"])
            self.assertIn("ROOTPW123", out)   # 刀下去密碼就漏了 ⇒ R5 會紅

    def test_k2_apply_without_gate(self):
        with knife_copy(REPO, "tools/cloud-host/robot.py",
                        '    why = ctx["approve"](action, a.server, ",".join(plan["authorized_key[]"]) or None)\n    if why:',
                        '    why = None\n    if why:') as tree:
            rc, out, f = self._run_in(tree, ["reset", "3109421", "--apply"], approve_ok=False)
            self.assertEqual((rc, len(f.calls)), (0, 1))   # 沒過閘也打了 API ⇒ R3 會紅

    def test_k3_plan_calls_api(self):
        with knife_copy(REPO, "tools/cloud-host/robot.py",
                        '    if not a.apply:\n        ctx["out"](json.dumps({"plan": plan, "apply": False}, ensure_ascii=False, indent=1))\n        return 0',
                        '    if False:\n        pass') as tree:
            rc, out, f = self._run_in(tree, ["rescue", "3109421", "--fingerprint", "aa:bb"])
            self.assertEqual(len(f.calls), 1)   # plan 也打了 ⇒ R1 會紅

    def test_k4_no_fingerprint_allowed(self):
        with knife_copy(REPO, "tools/cloud-host/robot.py", "    if a.apply and not fps:", "    if False:") as tree:
            rc, out, f = self._run_in(tree, ["rescue", "3109421", "--apply"])
            self.assertEqual(len(f.calls), 1)   # 沒指紋也送了 ⇒ R7 會紅


if __name__ == "__main__":
    unittest.main()
