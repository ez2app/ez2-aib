#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""核心 ↔ ez2.page 介面契約檔 contracts/page-api.json(@d07f9 10-21 夜)。

契約以前散在 PR 說明、DESIGN 檔、dm 裡,路徑寫錯要靠對話對出來(quote pop 那次)。
現在寫成一份檔,核心測試照它驗;伺服器(ez2-ai)那邊也可以讀同一份。網路全換假的,不碰真憑證。

C1 契約檔格式:前綴跟 api.API_PREFIX 一樣;每支端點都有 method / path / status / auth
C2 bind_revoke 實際送出的請求(從 urllib 那層攔)= 契約的 method + 前綴 + path、帶 bearer、本體空
C3 契約的 already_revoked 每個碼 ⇒ unbind 當成功、清本機;unsupported 每個 HTTP ⇒ detail = 契約的 core_detail、本機沒動
C4 quote-key 那幾支(planned):路徑都在同前綴下、pop 訊息前綴照契約、證書欄位含 kid / sig
刀(複本):K1 把 bind_revoke 改回送 {} ⇒ C2 紅
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
for _p in (REPO / "src" / "cockpit" / "page", REPO / "src" / "cockpit", HERE):
    sys.path.insert(0, str(_p))
import api as A  # noqa: E402
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


class Knife(unittest.TestCase):
    def test_k1_body_back(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/page/api.py", 'return self._request("POST", "/bind/revoke")',
                        'return self._request("POST", "/bind/revoke", {})', tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Contract.test_c2_revoke_request"],
                            cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
