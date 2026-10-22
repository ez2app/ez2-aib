#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib device list / lost(Andy 10-22「全照建議」;契約 contracts/page-api.json #261)。網路、Touch ID、主人簽章全換假的。

D1 聊天 + page 一起:本人證明只跳一次(視窗字含兩台、名稱清過)、聊天簽章帶 proved=True、page 撤那台;rc 0
D2 本人沒過 ⇒ 聊天沒簽、page 沒叫;rc 2、帶 hint
D3 page 失敗碼照契約:unknown / 伺服器沒這支(404 page_api_not_found、405)/ 429 / 401 / 503 / 連不上 ⇒ detail 原碼、rc 1;
   聊天照樣撤成(兩步結果分開)
D4 聊天簽章失敗 ⇒ page 照做;不認得的 kind 照列不當失敗
D5 不准撤自己:聊天 this ⇒ chat_is_self rc 2;page this ⇒ page_is_self、不打伺服器
D6 參數:都沒給 / 機器碼格式錯 / fp 格式錯 ⇒ rc 2;fp 前綴對到兩台 ⇒ page_ambiguous
D7 送出的請求照契約(urllib 層攔):GET /machines、POST /machines/{fp}/revoke 本體空、帶 bearer
D8 sign_list_change proved=True ⇒ 不跳第二次證明、密語照問;預設照舊跳證明
D9 只從電腦:遙控 OPS 沒有這支;aib 入口有接;四語都有
刀(複本):K1 拿掉本人證明那關 ⇒ D2 紅;K2 proved 預設也跳過證明 ⇒ D8 紅
"""
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (REPO / "src" / "cockpit" / "page", REPO / "src" / "cockpit", HERE):
    sys.path.insert(0, str(_p))
import device_lost as DL  # noqa: E402
from api import ApiError, PageApi, TransportError  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

FP_A = "a" * 8 + "1" * 56
FP_B = "a" * 8 + "2" * 56
FP_ME = "c" * 64
DEVS = [{"code": "qq7xk2ma", "name": "Andy‮MBP", "role": "machine", "npub": "npub1qq7xk2ma", "this": False},
        {"code": "zz9me000", "name": "這台", "role": "machine", "npub": "npub1zz9me000", "this": True}]
MACHINES = [{"fp": FP_A, "name": "舊筆電", "last_used_at": "2026-10-20T10:00:00Z", "live_tokens": 1, "this": False},
            {"fp": FP_ME, "name": "這台", "last_used_at": None, "live_tokens": 1, "this": True}]


class FakeApi:
    def __init__(self, err=None, results=None, machines=MACHINES):
        self.err, self.calls, self.machines = err, [], machines
        self.results = results if results is not None else [{"kind": "page_token", "id": "tok_1", "outcome": "revoked"}]

    def machines_list(self):
        return {"ok": True, "machines": self.machines}

    def machine_revoke(self, fp):
        self.calls.append(fp)
        if self.err:
            raise self.err
        return {"ok": True, "fp": fp, "results": self.results}


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-devlost-")
        self.addCleanup(td.cleanup)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "EZ2_AIB_LOCALE": "zh-TW"}
        self.reasons, self.signed = [], []
        orig = DL.chat_devices
        DL.chat_devices = lambda env: ([dict(d) for d in DEVS], None)
        self.addCleanup(setattr, DL, "chat_devices", orig)

    def prove(self, why=None):
        def p(reason, env):
            self.reasons.append(reason)
            return why
        return p

    def sign(self, rc=0):
        def s(env, out, reason, mutate, proved=False):
            self.signed.append((reason, proved))
            return rc, None
        return s

    def lost(self, chat="qq7xk2ma", page=FP_A[:8], api=None, why=None, sign_rc=0):
        api = api or FakeApi()
        res, rc = DL.lost(self.env, chat=chat, page=page, prove=self.prove(why), api=api, sign=self.sign(sign_rc),
                          out=io.StringIO())
        return res, rc, api


class Lost(Base):
    def test_d1_both_one_proof(self):
        res, rc, api = self.lost()
        self.assertEqual(rc, 0, res)
        self.assertEqual(len(self.reasons), 1, "本人證明只跳一次")
        self.assertIn("qq7xk2ma", self.reasons[0])
        self.assertIn(FP_A[:8], self.reasons[0])
        self.assertNotIn("‮", self.reasons[0], "名稱清過方向字元")
        self.assertEqual([p for _r, p in self.signed], [True])
        self.assertEqual(api.calls, [FP_A])
        self.assertEqual((res["chat"]["state"], res["page"]["state"]), ("revoked", "revoked"))
        self.assertEqual(res["page"]["results"][0]["outcome"], "revoked")

    def test_d2_proof_refused(self):
        for why in ("touchid_cancel", "touchid_off", "touchid_unavailable"):
            self.signed.clear()
            res, rc, api = self.lost(why=why)
            self.assertEqual((rc, res["why"], api.calls, self.signed), (2, why, [], []), why)
            self.assertTrue(res.get("hint"), why)

    def test_d3_page_errors(self):
        cases = [(ApiError(404, "page_device_unknown"), "failed", "page_unknown", "page_device_unknown"),
                 (ApiError(404, "page_api_not_found"), "unsupported", "page_unsupported", "page_device_revoke_unsupported"),
                 (ApiError(405, ""), "unsupported", "page_unsupported", "page_device_revoke_unsupported"),
                 (ApiError(429, "page_device_revoke_rate_limited"), "failed", "page_rate_limited", "page_device_revoke_rate_limited"),
                 (ApiError(401, "page_api_token_revoked"), "failed", "page_caller_dead", "page_api_token_revoked"),
                 (ApiError(503, "page_api_audit_failed"), "failed", "page_audit_failed", "page_api_audit_failed"),
                 (TransportError("down"), "failed", "page_unreachable", "transport")]
        for err, state, why, detail in cases:
            res, rc, _api = self.lost(api=FakeApi(err=err))
            self.assertEqual((rc, res["page"]["state"], res["page"]["why"], res["page"]["detail"]), (1, state, why, detail), why)
            self.assertTrue(res["page"].get("hint"), why)
            self.assertEqual(res["chat"]["state"], "revoked", "聊天那步照樣成")
        res, _rc, _api = self.lost(api=FakeApi(err=ApiError(429, "page_device_revoke_rate_limited")))
        self.assertIn("一小時", res["page"]["hint"])

    def test_d4_chat_fail_page_still(self):
        api = FakeApi(results=[{"kind": "quote_key", "id": "k1", "outcome": "revoked"},
                               {"kind": "page_token", "id": "t1", "outcome": "already_revoked"}])
        res, rc, api = self.lost(api=api, sign_rc=2)
        self.assertEqual((rc, res["chat"]["state"], res["chat"]["why"]), (1, "failed", "chat_sign_failed"))
        self.assertEqual((res["page"]["state"], [r["kind"] for r in res["page"]["results"]]),
                         ("revoked", ["quote_key", "page_token"]))

    def test_d5_not_self(self):
        res, rc, _api = self.lost(chat="zz9me000", page=None)
        self.assertEqual((rc, res["why"]), (2, "chat_is_self"))
        self.assertEqual(self.reasons, [])
        res, rc, api = self.lost(chat=None, page=FP_ME[:8])
        self.assertEqual((rc, res["page"]["why"], api.calls), (1, "page_is_self", []))

    def test_d6_args(self):
        for kw, why in ((dict(chat=None, page=None), "nothing_to_revoke"), (dict(chat="BAD!", page=None), "chat_bad_code"),
                        (dict(chat=None, page="xyz"), "page_bad_fp")):
            res, rc, _api = self.lost(**kw)
            self.assertEqual((rc, res["why"]), (2, why))
        res, rc, _api = self.lost(chat=None, page="aaaaaaaa",
                                  api=FakeApi(machines=MACHINES + [dict(MACHINES[0], fp=FP_B)]))
        self.assertEqual((rc, res["why"]), (2, "page_ambiguous"))
        self.assertEqual(self.reasons, [], "參數錯不跳證明")


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Opener:
    def __init__(self, body):
        self.seen, self.body = [], body

    def open(self, req, timeout=None):
        self.seen.append((req.get_method(), req.full_url, req.data, req.get_header("Authorization")))
        return _Resp(200, json.dumps(self.body).encode())


class Wire(unittest.TestCase):
    def test_d7_requests_per_contract(self):
        C = json.loads((REPO / "contracts" / "page-api.json").read_text(encoding="utf-8"))
        ls, rv = C["endpoints"]["machines_list"], C["endpoints"]["machine_revoke"]
        op = Opener({"ok": True, "machines": [], "results": []})
        api = PageApi(base_url="https://x", token="ast_wire", opener=op)
        api.machines_list()
        api.machine_revoke(FP_A)
        self.assertEqual(op.seen[0], (ls["method"], "https://x" + C["prefix"] + ls["path"], None, "Bearer ast_wire"))
        self.assertEqual(op.seen[1], (rv["method"], "https://x" + C["prefix"] + rv["path"].replace("{fp}", FP_A), None,
                                      "Bearer ast_wire"))


class Owner(unittest.TestCase):
    def test_d8_proved_skips_second_proof(self):
        import chat_owner_cli as OC
        calls = []
        orig = (OC.PROVE, OC.READ_PASS)
        OC.PROVE = lambda reason, env, root=None: calls.append("prove") or None
        OC.READ_PASS = lambda prompt: calls.append("pass") or "pw"
        try:
            self.assertEqual(OC._proof_then_pass("r", {}, Path("."), io.StringIO(), proved=True), "pw")
            self.assertEqual(calls, ["pass"], "proved ⇒ 不再證明、密語照問")
            calls.clear()
            self.assertEqual(OC._proof_then_pass("r", {}, Path("."), io.StringIO()), "pw")
            self.assertEqual(calls, ["prove", "pass"], "預設照舊證明")
        finally:
            OC.PROVE, OC.READ_PASS = orig

    def test_d9_wiring(self):
        import remote_link
        self.assertNotIn("device", remote_link.OPS)
        self.assertNotIn("lost", remote_link.OPS)
        self.assertIn('exec "$T_PY3" "$HERE/device_lost.py"', (REPO / "src" / "cockpit" / "aib").read_text(encoding="utf-8"))
        keys = ["aib.device." + k for k in ("help.usage", "touchid_reason", "item_chat", "item_page", "step_page_revoked",
                                             "step_page_unsupported", "hint.page_unsupported", "hint.page_rate_limited",
                                             "hint.chat_sign_failed", "hint.touchid_cancel")]
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            for k in keys:
                self.assertIn(k, d, (lang, k))


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, repl, case):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_no_proof_gate(self):
        self._knife("src/cockpit/device_lost.py", "    if why:\n        res = {\"ok\": False, \"why\": why, \"chat\"",
                    "    if False:\n        res = {\"ok\": False, \"why\": why, \"chat\"", "Lost.test_d2_proof_refused")

    def test_k2_proved_default_skips(self):
        self._knife("src/cockpit/chat_owner_cli.py", "    if not proved:\n", "    if False:\n",
                    "Owner.test_d8_proved_skips_second_proof")


if __name__ == "__main__":
    unittest.main()
