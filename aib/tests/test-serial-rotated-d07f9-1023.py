#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""遺失裝置撤銷時自動換 EZP- 序號(Andy 10-23 04:3x;契約 machine_revoke.ok.serial_rotated)。網路、Touch ID 全換假的。

S1 伺服器回 serial_rotated: true ⇒ page 那步 serial_rotated True、hint =「序號已換新」那句;終端機印在 ✓ 那行底下
S2 false / 沒這欄(舊伺服器)/ 不是 JSON true("true"、1)⇒ 當沒換、hint =「到後台重發序號」
S3 --json 的 page 那步帶 serial_rotated(bool);撤失敗 / 沒撤 page ⇒ 不印序號那句
S4 契約 ok.fields 有 serial_rotated、有說明;四語都有兩鍵
S5 503 page_device_serial_rotate_failed ⇒ why page_serial_rotate_failed(不是 page_audit_failed「撤銷已成立」)、detail 原碼、
   hint 說什麼都沒撤;別的 503 照舊 page_audit_failed
S6 aib page bind 遇 401 page_api_serial_rotated ⇒「已在遺失裝置撤銷時換新」那句(跟 serial_stale 不同句);四語
刀(複本):K1 只要 truthy 就算換 ⇒ S2 紅;K2 不印序號那句 ⇒ S1 紅;K3 換號失敗當成 503 稽核失敗 ⇒ S5 紅
"""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (REPO / "src" / "cockpit" / "page", REPO / "src" / "cockpit", HERE):
    sys.path.insert(0, str(_p))
import api as A  # noqa: E402
import bind as B  # noqa: E402
import device_lost as DL  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

C = json.loads((REPO / "contracts" / "page-api.json").read_text(encoding="utf-8"))
FP_A = "a" * 8 + "1" * 56
MACHINES = [{"fp": FP_A, "name": "舊筆電", "last_used_at": "2026-10-20T10:00:00Z", "live_tokens": 1, "this": False}]
ROTATED = "這台的 EZP- 序號已換新，舊序號不能再綁;其他台照常用，之後新綁機器請到 ez2.page 後台看新序號"
KEPT = "序號可能留在那台的終端機紀錄，請到後台重發序號"
NOTHING = "序號沒換成，這次什麼都沒撤;可以再跑一次"
BIND_ROTATED = "這個序號已在遺失裝置撤銷時換新，請登入 ez2.page 後台取新序號"
MISSING = object()


class FakeApi:
    def __init__(self, rotated=MISSING, err=None, extra=None):
        self.rotated, self.err, self.extra = rotated, err, extra or {}

    def machines_list(self):
        return {"ok": True, "machines": MACHINES}

    def machine_revoke(self, fp):
        if self.err:
            raise self.err
        res = {"ok": True, "fp": fp, "results": [{"kind": "page_token", "id": "tok_1", "outcome": "revoked"}]}
        if self.rotated is not MISSING:
            res["serial_rotated"] = self.rotated
        res.update(self.extra)
        return res


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-serial-")
        self.addCleanup(td.cleanup)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "EZ2_AIB_LOCALE": "zh-TW"}
        saved = {k: os.environ.get(k) for k in ("EZ2_AIB_LOCALE", "EZ2_AIB_STATE_ROOT")}
        os.environ.update({"EZ2_AIB_LOCALE": "zh-TW", "EZ2_AIB_STATE_ROOT": td.name})   # T() 讀行程環境、不讀 env

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)

    def lost(self, api):
        return DL.lost(self.env, page=FP_A[:8], prove=lambda r, e: None, api=api, out=io.StringIO())

    def cli(self, api, *extra):
        orig = DL.lost
        DL.lost = lambda env, **kw: orig(env, prove=lambda r, e: None, api=api, **kw)
        try:
            out = io.StringIO()
            rc = DL.main(["lost", "--page", FP_A[:8], *extra], env=self.env, out=out)
        finally:
            DL.lost = orig
        return rc, out.getvalue()


class Serial(Base):
    def test_s1_rotated(self):
        res, rc = self.lost(FakeApi(True))
        self.assertEqual(rc, 0, res)
        self.assertIs(res["page"]["serial_rotated"], True)
        self.assertEqual(res["page"]["hint"], ROTATED)
        rc, text = self.cli(FakeApi(True))
        lines = text.splitlines()
        i = next(n for n, ln in enumerate(lines) if ln.startswith("✓ ez2.page"))
        self.assertEqual(lines[i + 1], "  " + ROTATED)
        self.assertNotIn(KEPT, text)

    def test_s2_not_rotated(self):
        for v in (False, MISSING, "true", 1, None):
            res, rc = self.lost(FakeApi(v))
            self.assertEqual(rc, 0, res)
            self.assertIs(res["page"]["serial_rotated"], False, v)
            self.assertEqual(res["page"]["hint"], KEPT, v)

    def test_s3_json_and_failures(self):
        rc, text = self.cli(FakeApi(True), "--json")
        self.assertIs(json.loads(text)["page"]["serial_rotated"], True)
        rc, text = self.cli(FakeApi(), "--json")
        self.assertIs(json.loads(text)["page"]["serial_rotated"], False)
        leak = {"serial": "EZP-NEWA-NEWB-NEWC", "new_serial": "EZP-NEWA-NEWB-NEWC"}   # 契約:回應不帶新序號;帶了也不准印
        for extra in ((), ("--json",)):
            rc, text = self.cli(FakeApi(True, extra=leak), *extra)
            self.assertEqual(rc, 0)
            self.assertNotIn("NEWA", text, extra)
        res, rc = self.lost(FakeApi(True, err=A.ApiError(404, "page_device_unknown", body={"error": "page_device_unknown"})))
        self.assertEqual(rc, 1)
        self.assertNotIn("serial_rotated", res["page"])
        self.assertNotIn(res["page"].get("hint"), (ROTATED, KEPT))

    def test_s5_rotate_failed(self):
        for http in (500, 502, 504, 409):   # 契約只定 503;別的狀態碼不保證什麼都沒撤
            res, rc = self.lost(FakeApi(err=A.ApiError(http, "page_device_serial_rotate_failed")))
            self.assertEqual(res["page"]["why"], "page_refused", http)
        err = A.ApiError(503, "page_device_serial_rotate_failed", body={"error": "page_device_serial_rotate_failed"})
        res, rc = self.lost(FakeApi(True, err=err))
        self.assertEqual(rc, 1)
        self.assertEqual((res["page"]["state"], res["page"]["why"], res["page"]["detail"], res["page"]["hint"]),
                         ("failed", "page_serial_rotate_failed", "page_device_serial_rotate_failed", NOTHING))
        res, rc = self.lost(FakeApi(err=A.ApiError(503, "page_api_audit_failed", body={"error": "page_api_audit_failed"})))
        self.assertEqual(res["page"]["why"], "page_audit_failed")

    def test_s6_bind_serial_rotated(self):
        class Api:
            def __init__(self, code):
                self.code = code

            def bind_serial(self, serial, seat_label, machine):
                raise A.ApiError(401, self.code, body={"error": self.code})
        msgs = []
        for code in ("page_api_serial_rotated", "page_api_serial_stale"):
            with self.assertRaises(B.BindError) as cm:
                B.run_serial_bind(Api(code), "EZP-ABCD-EFGH-JKMN", "seat", lambda *_: None, environ=self.env)
            msgs.append(str(cm.exception))
        self.assertEqual(msgs[0], BIND_ROTATED)
        self.assertNotEqual(msgs[0], msgs[1])
        en = json.loads((REPO / "i18n" / "en.json").read_text(encoding="utf-8"))["aib.page.bind.serial_rotated"]
        os.environ["EZ2_AIB_LOCALE"] = "en"   # Base 的 cleanup 會還原
        with self.assertRaises(B.BindError) as cm:
            B.run_serial_bind(Api("page_api_serial_rotated"), "EZP-ABCD-EFGH-JKMN", "seat", lambda *_: None,
                              environ=self.env)
        self.assertEqual(str(cm.exception), en)


class Wording(unittest.TestCase):
    def test_s4_contract_and_locales(self):
        ok = C["endpoints"]["machine_revoke"]["ok"]
        self.assertIn("serial_rotated", ok["fields"])
        self.assertEqual(ok["serial_rotated"]["type"], "bool")
        self.assertIn("false", ok["serial_rotated"]["absent"])
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for key in ("device.serial_rotated", "device.serial_kept", "device.hint.page_serial_rotate_failed",
                        "page.bind.serial_rotated"):
                self.assertTrue(d.get("aib." + key, "").strip(), (loc, key))
            self.assertIn("EZP-", d["aib.device.serial_rotated"], loc)
            self.assertNotEqual(d["aib.device.serial_rotated"], d["aib.device.serial_kept"], loc)
            self.assertNotEqual(d["aib.device.hint.page_serial_rotate_failed"], d["aib.device.hint.page_audit_failed"], loc)
            self.assertNotEqual(d["aib.page.bind.serial_rotated"], d["aib.device.serial_rotated"], loc)
            self.assertNotEqual(d["aib.device.hint.page_serial_rotate_failed"], d["aib.device.hint.page_refused"], loc)
            if loc in ("en", "ko"):   # 外語沒被換成中文原句
                for key in ("device.serial_rotated", "device.serial_kept", "device.hint.page_serial_rotate_failed",
                            "page.bind.serial_rotated"):
                    self.assertFalse(any("\u4e00" <= ch <= "\u9fff" for ch in d["aib." + key]), (loc, key))

    def test_s4_contract_codes_match_core(self):
        E = C["endpoints"]
        f = E["machine_revoke"]["serial_rotate_failed"]
        self.assertEqual(f["http"], 503)
        for code in f["codes"]:
            self.assertEqual(DL._page_err(A.ApiError(f["http"], code))[0], "page_serial_rotate_failed", code)
        b = E["bind_serial"]
        self.assertEqual((b["method"], b["path"], b["auth"]), ("POST", "/bind/serial", "none"))
        self.assertEqual((b["serial_rotated"]["http"], b["serial_rotated"]["codes"]), (401, ["page_api_serial_rotated"]))


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, repl, case):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_truthy_counts(self):
        self._knife("src/cockpit/device_lost.py", '.get("serial_rotated") is True', '.get("serial_rotated")',
                    "Serial.test_s2_not_rotated")

    def test_k2_no_serial_line(self):
        self._knife("src/cockpit/device_lost.py", '        res["page"]["hint"] = T("serial_rotated"',
                    '        res["page"]["nohint"] = T("serial_rotated"', "Serial.test_s1_rotated")


    def test_k3_rotate_failed_as_audit(self):
        self._knife("src/cockpit/device_lost.py", '    if code == "page_device_serial_rotate_failed" and http == 503:\n',
                    '    if False:\n', "Serial.test_s5_rotate_failed")


if __name__ == "__main__":
    unittest.main()
