#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""核心 ↔ ez2.page 介面契約檔 contracts/page-api.json(@d07f9 10-21 夜)。

契約以前散在 PR 說明、DESIGN 檔、dm 裡,路徑寫錯要靠對話對出來(quote pop 那次)。
現在寫成一份檔,核心測試照它驗;伺服器(ez2-ai)那邊也可以讀同一份。網路全換假的,不碰真憑證。

C1 契約檔格式:前綴跟 api.API_PREFIX 一樣;每支端點都有 method / path / status / auth
C2 bind_revoke 實際送出的請求(從 urllib 那層攔)= 契約的 method + 前綴 + path、帶 bearer、本體空
C3 契約的 already_revoked 每個碼 ⇒ unbind 當成功、清本機;unsupported 每個 HTTP ⇒ detail = 契約的 core_detail、本機沒動
C4 quote-key 那幾支(planned):路徑都在同前綴下、pop 訊息前綴照契約、證書欄位含 kid / sig;
   核心還沒有這幾支的呼叫端(register / rotate 未做)⇒ 契約標 core_client 還沒有,做了再加攔請求的測試
C5 核心已有的 quote 部分 = 證書驗簽:照契約欄位 / kind / kid 格式 / sig 格式造的證書 aib_quote.cert_ok 收;
   少任一驗簽要的欄位、kind 不對、sig 帶 padding ⇒ 不收
C6 page_features:remote_link 實際送出的 = 契約 method + 前綴 + path、不帶 Authorization;只收字面 true;
   契約列的三個功能都讀得到;404 ⇒ server_not_ready、連不上 ⇒ server_unreachable
C7 遺失裝置(planned,照 @9f808 草稿;Andy 10-22 裁):GET /machines 回 fp / name / last_used_at / live_tokens / this;
   POST /machines/{fp}/revoke 本體空、每把一筆 outcome、409 self / 404 unknown / unsupported 規則、威脅模型段;核心還沒有呼叫端(0.1.41)
刀(複本):K1 把 bind_revoke 改回送 {} ⇒ C2 紅;K2 cert_ok 認的 kind 跟契約不同 ⇒ C5 紅;K3 FEATURES_PATH 漂掉 ⇒ C6 紅
"""
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (REPO / "src" / "cockpit" / "page", REPO / "src" / "cockpit", REPO / "src", HERE):
    sys.path.insert(0, str(_p))
import aib_quote as AQ  # noqa: E402
import api as A  # noqa: E402
import base64  # noqa: E402
import hashlib  # noqa: E402
from entitlement import ed25519  # noqa: E402
import store  # noqa: E402
import unbind as UB  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

C = json.loads((REPO / "contracts" / "page-api.json").read_text(encoding="utf-8"))
E = C["endpoints"]
REC = {"assistant_token": "ast_contract_x", "tenant_id": "tenant_ab12", "page_id": "page_cd34", "slug": "andy"}


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """在 urllib 那層攔:記下真正會送出去的請求;回固定的 HTTP 狀態 + JSON。"""

    def __init__(self, status=200, payload=None):
        self.status, self.payload, self.seen = status, payload, []

    def open(self, req, timeout=None):
        self.seen.append(req)
        raw = json.dumps(self.payload if self.payload is not None else {}).encode()
        if self.status >= 400:
            raise urllib.error.HTTPError(req.full_url, self.status, "x", {}, io.BytesIO(raw))
        return _Resp(self.status, raw)


def _api(opener):
    return A.PageApi(base_url="https://page.test", token=REC["assistant_token"], opener=opener)


class Contract(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-contract-")
        self.addCleanup(td.cleanup)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "EZ2_AIB_LOCALE": "zh-TW"}
        store.save_token(dict(REC), self.env)

    def _path(self):
        return Path(store.token_path(REC["tenant_id"], REC["page_id"], self.env)[0])

    def _unbind(self, opener):
        return UB.unbind(dict(REC), _api(opener), self.env, prove=lambda reason, env: ("touchid", None))

    def test_c1_shape(self):
        self.assertEqual(C["prefix"], A.API_PREFIX)
        for name, ep in E.items():
            for k in ("method", "path", "status", "auth"):
                self.assertIn(k, ep, (name, k))
            self.assertTrue(ep["path"].startswith("/"), name)

    def test_c2_revoke_request(self):
        ep = E["bind_revoke"]
        op = FakeOpener(ep["ok"]["http"], {f: True for f in ep["ok"]["fields"]})
        _api(op).bind_revoke()
        req = op.seen[0]
        self.assertEqual(req.get_method(), ep["method"])
        self.assertEqual(req.full_url, "https://page.test" + C["prefix"] + ep["path"])
        self.assertEqual(req.get_header("Authorization"), "Bearer " + REC["assistant_token"])
        self.assertEqual(ep["body"], "empty")
        self.assertIsNone(req.data, "契約:本體要空(帶 {} 伺服器回 400 page_api_body_invalid)")
        self.assertNotIn("machine", req.full_url)
        self.assertIs(ep["machine_id"], False)

    def test_c3_codes(self):
        ep = E["bind_revoke"]
        self.assertEqual(set(ep["already_revoked"]["codes"]), set(UB.ALREADY_REVOKED))
        for code in ep["already_revoked"]["codes"]:
            store.save_token(dict(REC), self.env)
            res, rc = self._unbind(FakeOpener(ep["already_revoked"]["http"], {"error": code}))
            self.assertEqual((rc, res["state"], res["server"]), (0, "unbound", "already"), code)
            self.assertFalse(self._path().exists(), code)
        store.save_token(dict(REC), self.env)
        self.assertEqual(ep["unsupported"]["core_detail"], UB.UNSUPPORTED)
        for http in ep["unsupported"]["http"]:
            res, rc = self._unbind(FakeOpener(http, {"error": "not_found"}))
            self.assertEqual((rc, res["detail"]), (1, ep["unsupported"]["core_detail"]), http)
            self.assertTrue(self._path().exists(), http)
        res, rc = self._unbind(FakeOpener(ep["ok"]["http"], {f: True for f in ep["ok"]["fields"]}))
        self.assertEqual((rc, res["server"]), (0, "revoked"))

    def test_c4_quote_planned(self):
        names = [n for n in E if n.startswith("quote_key_")]
        self.assertEqual(sorted(names), sorted(["quote_key_start", "quote_key_poll", "quote_key_cancel",
                                                "quote_key_certs", "quote_key_pop"]))
        for n in names:
            self.assertTrue(E[n]["path"].startswith("/quote-key/"), n)
        self.assertEqual(E["quote_key_pop"]["pop_message_prefix"], "ez2-aib-quote-pop-v1:")
        self.assertIn("confirm_digits", E["quote_key_start"]["secret_fields"])
        for f in ("kid", "sig", "key_fp", "revoked_at"):
            self.assertIn(f, E["quote_key_certs"]["cert_fields"])


class QuoteCert(unittest.TestCase):
    def _cert(self, seed=b"\x05" * 32):
        cc = E["quote_key_certs"]
        spub = ed25519.public_key(seed)
        kid = cc["kid_format"].split(":", 1)[0] + ":" + hashlib.sha256(spub).hexdigest()
        self.assertEqual(kid, AQ.kid_of(spub))
        pub = b"\x04" + bytes(range(64))
        vals = {"v": 1, "kind": cc["cert_kind"], "account_id": "acct_1", "key_fp": AQ.key_fp(pub),
                "pub_x963_b64": base64.b64encode(pub).decode(), "machine_label": "Pro",
                "registered_at": "2026-10-21T11:00:00+00:00", "revoked_at": None, "kid": kid}
        cert = {f: vals[f] for f in cc["cert_fields"] if f != "sig"}
        cert["sig"] = base64.urlsafe_b64encode(ed25519.sign(seed, AQ.canonical(cert))).decode().rstrip("=")
        self.assertEqual(sorted(cert), sorted(cc["cert_fields"]))
        return cert, {kid: "ed25519:" + spub.hex()}

    def test_c5_cert_per_contract(self):
        cert, trusted = self._cert()
        self.assertEqual(len(cert["sig"]), 86, E["quote_key_certs"]["sig_format"])
        self.assertTrue(AQ.cert_ok(cert, trusted))
        for f in ("kind", "kid", "sig", "key_fp", "pub_x963_b64", "registered_at", "v"):
            self.assertFalse(AQ.cert_ok({k: v for k, v in cert.items() if k != f}, trusted), f)
        self.assertFalse(AQ.cert_ok(dict(cert, sig=cert["sig"] + "=="), trusted), "sig 不補等號")
        self.assertFalse(AQ.cert_ok(cert), "出貨清單空 ⇒ 不收")


class Features(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-contract-feat-")
        self.addCleanup(td.cleanup)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name}

    def test_c6_features_per_contract(self):
        import remote_link as RL
        ep = E["page_features"]
        sent = []

        def call(status, d):
            def c(method, url, token, body=None, timeout=None):
                sent.append((method, url, token, body))
                return status, d
            return c
        feats = {k: True for k in ep["ok"]["features"]}
        feats["quote"] = "true"
        got, why = RL.fetch_features(self.env, call=call(200, {"features": feats}), now=1000.0, base="https://x")
        self.assertEqual(sent, [(ep["method"], "https://x" + C["prefix"] + ep["path"], None, None)])
        self.assertIsNone(why)
        self.assertNotIn("quote", got, "字串 \"true\" 不算")
        for k in ("remote", "unbind"):
            self.assertIs(got[k], True)
        self.assertEqual(RL.server_ready(self.env, call=call(500, None), now=1001.0, base="https://x"), (True, None),
                         "60 秒內走快取")
        for status, why_want in ((404, "server_not_ready"), (0, "server_unreachable")):
            td = tempfile.TemporaryDirectory(prefix="aib-contract-feat-")
            self.addCleanup(td.cleanup)
            env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name}
            self.assertEqual(RL.fetch_features(env, call=call(status, None), now=1000.0, base="https://x"),
                             (None, why_want))


class LostDevice(unittest.TestCase):
    def test_c7_machine_revoke_planned(self):
        ls, rv = E["machines_list"], E["machine_revoke"]
        self.assertEqual((ls["method"], ls["path"], rv["method"], rv["path"]),
                         ("GET", "/machines", "POST", "/machines/{fp}/revoke"))
        for f in ("fp", "name", "last_used_at", "live_tokens", "this"):
            self.assertIn(f, ls["ok"]["machines_item"])
        self.assertEqual(rv["body"], "empty")
        self.assertEqual(set(rv["ok"]["results_item"]["outcome"].split(" | ")), {"revoked", "already_revoked", "expired"})
        self.assertEqual((rv["self"]["http"], rv["self"]["codes"]), (409, ["page_device_revoke_self"]))
        self.assertEqual((rv["unknown"]["http"], rv["unknown"]["codes"]), (404, ["page_device_unknown"]))
        self.assertEqual(rv["unsupported"]["core_detail"], "page_device_revoke_unsupported")
        self.assertTrue(all(rv["threat_model"][k] for k in ("risk", "accepted", "next")))
        for ep in (ls, rv):
            self.assertEqual(ep["status"], "planned")
            self.assertIn("還沒有", ep["core_client"])


class Knife(unittest.TestCase):
    def test_k1_body_back(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/page/api.py", 'return self._request("POST", "/bind/revoke")',
                        'return self._request("POST", "/bind/revoke", {})', tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Contract.test_c2_revoke_request"],
                            cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


    def test_k2_kind_drift(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/aib_quote.py", 'cert.get("kind") != "aib-quote-key"',
                        'cert.get("kind") != "aib-quote-key-v0"', tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "QuoteCert.test_c5_cert_per_contract"],
                            cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k3_features_path_drift(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/remote_link.py", 'FEATURES_PATH = "/api/v1/page/features"',
                        'FEATURES_PATH = "/api/v1/remote/ready"', tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Features.test_c6_features_per_contract"],
                            cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
