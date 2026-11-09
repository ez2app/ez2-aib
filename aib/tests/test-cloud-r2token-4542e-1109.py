#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""tools/cloud-host/r2token.py(EU01 開 R2 token → Touch ID → 管線直送目的主機)沙盒測試,不連 Cloudflare、不開 ssh。

R1 plan 模式(沒 --apply)不過閘、不跑任何 ssh
R2 --apply:先過閘(approve 收到 bucket / host / ip)→ 兩條 ssh:第一條在憑證主機、帶 bucket + IP/32 + 兩個 R2 Item 權限;第二條在目的主機、帶 dest + remote
R3 閘沒過 ⇒ rc 2、沒開 ssh
R4 站 cloud_r2token.mint 有登記在 tools/cloud-host/r2token.py、類別 cf_token(高風險、不開時間窗)、bucket / host / ip 不截;4 語 i18n 都有那句
R5 兩條 ssh 都帶加固選項(ForwardAgent=no、ClearAllForwardings=yes、BatchMode)
R6 憑證主機那段只印 S3 憑證(sha256 過的 secret),不印 token 原值;目的主機那段寫 0600、驗 lsd / 寫 / 刪 409 / 別的 bucket 403,且 rclone 一律 --bind 那個 IP
R7 runner 回的結果若 ok=False ⇒ rc 1;結果 JSON 原樣印(本程式不碰值)
刀:K1 apply 不過閘 ⇒ R3 紅;K2 plan 也跑 ssh ⇒ R1 紅;K3 目的主機那段拿掉 --bind ⇒ R6 紅
"""
import io
import json
import shlex
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TOOL = REPO / "tools" / "cloud-host"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "src" / "bootstrap"))
from aib_knife_copy import knife_copy  # noqa: E402
import r2token  # noqa: E402

ARGS = ["mint", "ez2-cloud-eu02", "--host", "eu02-ts", "--ip", "135.181.75.90", "--name", "t-r2"]


class Fake:
    def __init__(self, approve_ok=True, result=None):
        self.approvals, self.runs, self.approve_ok = [], [], approve_ok
        self.result = result or {"ok": True, "token_id": "abc", "list_own_bucket": True, "write": True,
                                 "read_back": True, "delete_blocked_by_lock": True, "other_bucket_denied": True}

    def approve(self, a):
        self.approvals.append((a.bucket, a.host, a.ip))
        return None if self.approve_ok else "touchid_cancel"

    def runner(self, c1, c2):
        self.runs.append((c1, c2))
        return (0 if self.result.get("ok") else 1), dict(self.result)

    def run(self, argv, mod=r2token):
        out = io.StringIO()
        rc = mod.main(argv, approve_fn=self.approve, runner=self.runner, out=lambda s: out.write(s + "\n"))
        return rc, out.getvalue()


def _spec_of(c1):
    """第一條 ssh 的最後一個參數是 shlex 包過的 JSON spec。"""
    return json.loads(shlex.split(c1[-1])[0])


class R2Token(unittest.TestCase):
    def test_r1_plan_touches_nothing(self):
        f = Fake()
        rc, out = f.run(ARGS)
        self.assertEqual((rc, f.approvals, f.runs), (0, [], []), out)
        d = json.loads(out)
        self.assertEqual((d["apply"], d["plan"]["bucket"], d["plan"]["ip_condition"]), (False, "ez2-cloud-eu02", "135.181.75.90/32"))

    def test_r2_apply_gate_then_two_ssh(self):
        f = Fake()
        rc, out = f.run(ARGS + ["--apply"])
        self.assertEqual((rc, f.approvals), (0, [("ez2-cloud-eu02", "eu02-ts", "135.181.75.90")]), out)
        self.assertEqual(len(f.runs), 1)
        c1, c2 = f.runs[0]
        self.assertEqual((c1[0], c2[0]), ("ssh", "ssh"))
        self.assertIn("eu01-ts", c1); self.assertIn("eu02-ts", c2)
        spec = _spec_of(c1)
        self.assertEqual((spec["bucket"], spec["ip"], spec["name"]), ("ez2-cloud-eu02", "135.181.75.90", "t-r2"))
        self.assertEqual((spec["perm_read"], spec["perm_write"]), (r2token.PERM_ITEM_READ, r2token.PERM_ITEM_WRITE))
        self.assertEqual(shlex.split(c2[-2])[0], "/etc/ez2-cloud/backup-r2.env")
        self.assertEqual(shlex.split(c2[-1])[0], "r2")
        self.assertIn('"ok": true', out)

    def test_r3_gate_refused(self):
        f = Fake(approve_ok=False)
        rc, out = f.run(ARGS + ["--apply"])
        self.assertEqual((rc, f.runs), (2, []), out)
        self.assertIn("touchid_cancel", out)

    def test_r4_registered_in_approve_gate_and_i18n(self):
        import approve_gate as AG
        spec = AG.SITES["cloud_r2token.mint"]
        self.assertEqual((spec["mod"], spec["cat"]), ("cloud-host/r2token.py", "cf_token"))
        self.assertEqual(tuple(spec["facts"][k] for k in ("bucket", "host", "ip")), (AG.FULL, AG.FULL, AG.FULL))
        self.assertTrue(AG.CATEGORIES["cf_token"]["high_risk"])
        self.assertEqual(AG.CATEGORIES["cf_token"]["max_window"], 0)
        self.assertIn('"cloud_r2token.mint"', (TOOL / "r2token.py").read_text(encoding="utf-8"))
        for loc in ("en", "zh-TW", "ja", "ko"):
            d = json.loads((REPO / "i18n" / f"{loc}.json").read_text(encoding="utf-8"))
            self.assertIn("{bucket}", d["aib.cloud_r2token.touchid_mint"], loc)
            self.assertIn("{ip}", d["aib.cloud_r2token.touchid_mint"], loc)

    def test_r5_ssh_hardening(self):
        f = Fake()
        f.run(ARGS + ["--apply"])
        for c in f.runs[0]:
            s = " ".join(c)
            self.assertIn("ForwardAgent=no", s); self.assertIn("ClearAllForwardings=yes", s); self.assertIn("BatchMode=yes", s)

    def test_r6_remote_snippets(self):
        m, i = r2token.MINT_REMOTE, r2token.INSTALL_REMOTE
        self.assertIn("hashlib.sha256(tok[\"value\"]", m)            # secret = sha256(token 值)
        self.assertNotIn('"value": tok["value"]', m)                   # 不印原值
        self.assertIn('"request_ip": {"in": [spec["ip"] + "/32"]}', m)
        self.assertIn("com.cloudflare.edge.r2.bucket.%s_default_%s", m)
        self.assertIn("0o600", i)
        self.assertIn('"--bind", d["ip"]', i)                          # rclone 一律綁 IPv4(EU02 預設 v6 出口會 403)
        self.assertIn("status code: 409", i)                           # bucket lock 擋刪
        self.assertIn("ez2-media", i)                                  # 別的 bucket 要 403

    def test_r7_result_not_ok(self):
        f = Fake(result={"ok": False, "why": "create_failed", "errors": [{"code": 9109}]})
        rc, out = f.run(ARGS + ["--apply"])
        self.assertEqual(rc, 1)
        self.assertIn("create_failed", out)

    # ---- 刀 ----
    def _run_in(self, tree, argv, approve_ok=True):
        import importlib.util
        spec = importlib.util.spec_from_file_location("r2token_knife", Path(tree) / "tools" / "cloud-host" / "r2token.py")
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        f = Fake(approve_ok=approve_ok)
        rc, out = f.run(argv, mod=mod)
        return rc, out, f

    def test_k1_apply_without_gate(self):
        with knife_copy(REPO, "tools/cloud-host/r2token.py",
                        "    why = (approve_fn or approve)(a)\n    if why:", "    why = None\n    if why:") as tree:
            rc, out, f = self._run_in(tree, ARGS + ["--apply"], approve_ok=False)
            self.assertEqual((rc, len(f.runs)), (0, 1))   # 沒過閘也開 ssh 了 ⇒ R3 會紅

    def test_k2_plan_runs_ssh(self):
        with knife_copy(REPO, "tools/cloud-host/r2token.py",
                        "    if not a.apply:\n        out(json.dumps({\"plan\": plan(a), \"apply\": False}, ensure_ascii=False)); return 0",
                        "    if not a.apply:\n        mint(a, runner); out(json.dumps({\"plan\": plan(a), \"apply\": False}, ensure_ascii=False)); return 0") as tree:
            rc, out, f = self._run_in(tree, ARGS)
            self.assertEqual(len(f.runs), 1)              # plan 也跑了 ssh ⇒ R1 會紅

    def test_k3_install_without_bind(self):
        with knife_copy(REPO, "tools/cloud-host/r2token.py", '"--bind", d["ip"], ', "") as tree:
            text = (Path(tree) / "tools" / "cloud-host" / "r2token.py").read_text(encoding="utf-8")
            self.assertNotIn('"--bind", d["ip"]', text)   # 刀落下 ⇒ R6 會紅


if __name__ == "__main__":
    unittest.main()
