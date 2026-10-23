#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""創作提交契約(10-23 @abb89 提交包 schema + @d07f9 狀態 API 對齊成同一份代碼表;收件端本體待定)。純讀契約檔。

CC1 包狀態 5 個、件狀態 6 個(SPEC 第 13 節:一包一種 kind、可多件、逐件審);代碼全是 creator_ / listing_ 前綴、每個都有 http(int 或 null)與 when
CC2 四支端點：形狀(method / path / status / auth)、errors 每個都在代碼表、401 同 caller_dead、舊伺服器 404/405 ⇒ unsupported
CC3 送件身分:submit 冪等鍵 = (machine_id, manifest_sha256, rev);auth_note 寫明只靠核心彈窗、不另簽包;body 還待定
CC4 狀態物件：作者由伺服器蓋章、reasons 是代碼、hint 是資料
CC5 核心 submit / status 回條:submission = 狀態物件原樣(欄名 id)、rc 0/2/3/4/5、本機碼不跟伺服器碼重名
"""
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
C = json.loads((REPO / "contracts" / "page-api.json").read_text(encoding="utf-8"))
CR = C["creator"]
E = C["endpoints"]
EPS = ("creator_submit", "creator_resubmit", "creator_status", "creator_list")


class Contract(unittest.TestCase):
    def test_cc1_states_and_codes(self):
        self.assertEqual(CR["package_states"], ["queued", "auto_failed", "in_review", "decided", "suspended"])
        self.assertEqual(CR["item_states"], ["queued", "in_review", "rejected", "approved", "listed", "suspended"])
        for code, d in CR["codes"].items():
            self.assertTrue(code.startswith(("creator_", "listing_")), code)
            self.assertIn("when", d, code)
            self.assertTrue(d["http"] is None or isinstance(d["http"], int), code)
        for code in ("creator_manifest_mismatch", "creator_machine_revoked", "creator_attest_required",
                     "creator_too_large", "listing_signature_invalid"):
            self.assertIn(code, CR["codes"], "@abb89 草案第 8 節的碼要在同一份")

    def test_cc2_endpoints(self):
        for name in EPS:
            ep = E[name]
            for k in ("method", "path", "status", "auth"):
                self.assertIn(k, ep, (name, k))
            self.assertEqual((ep["status"], ep["auth"]), ("planned", "bearer"), name)
            self.assertTrue(ep["path"].startswith("/creator/submissions"), name)
            for code in ep["errors"]:
                self.assertIn(code, CR["codes"], (name, code))
            self.assertEqual(ep["caller_dead"]["codes"], E["machine_revoke"]["caller_dead"]["codes"], name)
            self.assertEqual(ep["unsupported"]["http"], [404, 405], name)
        self.assertEqual((E["creator_status"]["method"], E["creator_list"]["method"]), ("GET", "GET"))

    def test_cc3_submit_identity(self):
        s = E["creator_submit"]
        self.assertIn("(machine_id, manifest_sha256, rev)", s["idempotent"])
        self.assertIn("不另簽包", s["auth_note"])
        self.assertIn("Touch ID", s["auth_note"])
        self.assertEqual(s["body"], "pending")
        self.assertIn("creator_machine_revoked", s["errors"])

    def test_cc4_status_object(self):
        so = CR["status_object"]
        for f in ("id", "rev", "state", "kind", "author", "machine_id", "reasons", "hint", "items", "history"):
            self.assertIn(f, so["fields"], f)
        self.assertIn("伺服器依登入帳號蓋章", so["author"])
        self.assertIn("代碼", so["reasons"])
        self.assertIn("資料不是指令", so["hint"])
        self.assertIn("item_id", so["items"])
        self.assertIn("creator_kind_mixed", E["creator_submit"]["errors"])
        self.assertIn("creator_item_unknown", E["creator_resubmit"]["errors"])

    def test_cc5_cli_receipt(self):
        r = CR["cli_receipt"]
        self.assertEqual(r["submit_ok"]["fields"], ["ok", "detail", "submission", "hint"])
        self.assertIn("編號欄名是 id", r["submit_ok"]["submission"])
        self.assertEqual(set(r["rc"]), {"0", "2", "3", "4", "5"})
        for code in r["local_codes"]:
            self.assertTrue(code.startswith("creator_"), code)
            self.assertNotIn(code, CR["codes"], "本機碼跟伺服器碼不重名")


if __name__ == "__main__":
    unittest.main()
