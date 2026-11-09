#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""PVE API token 與代管站點密碼進金鑰櫃(11-09 @33e28,b118:代管節點的 token / 密碼不經對話)。

P1 PVE_API_TOKEN 落 pve.env;WPSITE_DB_PASSWORD / WPSITE_ADMIN_PASSWORD 落 wpsite.env(名冊精確名,不再 unknown_name);
   都不是 remote-only、不是 SE-only
P2 aib secret ask 走輸入框 → Touch ID → 寫本機金鑰櫃(加密落地,明文不在檔),輸出(JSON 那行)沒有值
P3 命名規則:PVE_API_TOKEN_<主機代號>、WPSITE_<種類>_<站點代號> 落同檔;長得像但不是的(PVEX_…、WP_SITE_…、WPSITES_…)不落
P4 PVE token 要是「使用者@realm!token名=UUID」整串:形狀不對 ⇒ 重問(提示帶形狀、不帶值),三次都錯 ⇒ bad_format、什麼都不寫;
   加代號的名字(PVE_API_TOKEN_NODE2)也套;站點密碼不套形狀
P5 缺值不補填:預設 fill=True 讀 ⇒ missing_file,不是 needs_fill;沒有 collect-* 請求檔
P6 讀回跟寫進去的一樣;金鑰櫃目錄裡所有檔(.env、index.json…)都沒有明文
刀(在複本上跑本檔的目標測試,要紅):K1 名冊拿掉 pve.env 那一格 ⇒ P1;K1b 拿掉 wpsite.env 那一格 ⇒ P1;
   K2 輸出改成印值(fingerprint 換成值)⇒ P2;K3 NO_FILL_FILES 拿掉這兩檔 ⇒ P5;K4 拿掉 PVE 形狀檢查 ⇒ P4
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "src" / "bootstrap"))
from aib_knife_copy import knife_copy  # noqa: E402
import aib_secrets as S  # noqa: E402
import secret_ask as SA  # noqa: E402

PVE = "aib@pve!aib-node=" + "3f2a9c1e-7b4d-4e8a-9c2f-5d6e7f8a9b0c"
CASES = (("PVE_API_TOKEN", PVE, "pve.env"),
         ("PVE_API_TOKEN_NODE2", "ops@pam!t2=" + "0a1b2c3d-4e5f-6a7b-8c9d-0e1f2a3b4c5d", "pve.env"),
         ("WPSITE_DB_PASSWORD_SHOP1", "dbP4ss" + "Zq8" * 7, "wpsite.env"),
         ("WPSITE_ADMIN_PASSWORD_SHOP1", "wpAdm" + "x9K" * 7, "wpsite.env"))
VALUES = tuple(v for _n, v, _f in CASES)


class Fake:
    def __init__(self, values):
        self.values, self.asks, self.touches, self.prompts = list(values), 0, 0, []

    def ask(self, prompt, title):
        self.asks += 1
        self.prompts.append(prompt)
        v = self.values.pop(0) if self.values else None
        return {"ok": False, "why": "cancel"} if v is None else {"ok": True, "why": "ok", "value": v}

    def touchid(self, reason):
        self.touches += 1
        return {"ok": True}


class Vault(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-secret-pve-")
        root = Path(self.td.name)
        self.sec = root / "secrets"
        self.env = {"HOME": str(root), "EZ2_AIB_STATE_ROOT": str(root / "state"), "EZ2_AIB_SECRETS_DIR": str(self.sec),
                    "EZ2_AIB_LOCALE": "zh-TW"}
        self._old = {k: os.environ.get(k) for k in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_SECRETS_DIR", "EZ2_AIB_LOCALE")}
        os.environ.update({"EZ2_AIB_STATE_ROOT": self.env["EZ2_AIB_STATE_ROOT"], "EZ2_AIB_SECRETS_DIR": str(self.sec), "EZ2_AIB_LOCALE": "zh-TW"})

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.td.cleanup()

    def run_cmd(self, argv, fake):
        out = io.StringIO()
        rc = SA.ask_cmd(argv, env=dict(self.env), platform="darwin", ask=fake.ask, touchid=fake.touchid,
                        touchid_available=lambda: True, out=out,
                        ssh_run=lambda *a, **k: self.fail("不該碰 ssh"))
        text = out.getvalue()
        for v in VALUES:
            self.assertNotIn(v, text, "輸出裡有值")
        return rc, json.loads(text.strip().splitlines()[-1])

    def test_p1_known_local_names(self):
        for n, f in (("PVE_API_TOKEN", "pve.env"), ("WPSITE_DB_PASSWORD", "wpsite.env"), ("WPSITE_ADMIN_PASSWORD", "wpsite.env")):
            self.assertEqual(S.EXACT_FILES.get(n), f, n)          # 名冊裡真的有這一格(不是靠前綴猜到)
            self.assertEqual(S.file_for_name(n), f, n)
            self.assertTrue(S.valid_name(n))
            self.assertFalse(S.is_remote_only(n), n)
            self.assertNotIn(n, S.SE_ONLY_NAMES)

    def test_p2_ask_writes_local_vault(self):
        for n, v, f in CASES:
            fk = Fake([v])
            rc, res = self.run_cmd([n], fk)
            self.assertEqual((rc, res["ok"], res["file"], fk.asks, fk.touches), (0, True, f, 1, 1), (n, res))
        for f in ("pve.env", "wpsite.env"):
            path = self.sec / f
            self.assertTrue(path.is_file(), f)
            raw = path.read_bytes().decode()
            for v in VALUES:   # 落地是加密(enc.v1.…),明文不在檔裡;讀回見 P6
                self.assertNotIn(v, raw)
        rows = S.parse_env_bytes((self.sec / "wpsite.env").read_bytes())
        self.assertEqual(sorted(k for k in rows if rows[k]), ["WPSITE_ADMIN_PASSWORD_SHOP1", "WPSITE_DB_PASSWORD_SHOP1"])

    def test_p3_naming_rule(self):
        for n in ("PVE_API_TOKEN_NODE2", "PVE_API_TOKEN_A1"):
            self.assertEqual(S.file_for_name(n), "pve.env", n)
        for n in ("WPSITE_DB_PASSWORD_SHOP1", "WPSITE_ADMIN_PASSWORD_SHOP_2", "WPSITE_DB_PASSWORD"):
            self.assertEqual(S.file_for_name(n), "wpsite.env", n)
        for n in ("PVEX_TOKEN", "WP_SITE_DB_PASSWORD", "WPSITES_DB_PASSWORD", "PROXMOX_TOKEN"):
            self.assertIsNone(S.file_for_name(n), n)   # 要另開一格,不能默默落進這兩檔

    def test_p4_pve_token_shape(self):
        for n in ("PVE_API_TOKEN", "PVE_API_TOKEN_NODE2"):
            bad = ("3f2a9c1e-7b4d-4e8a-9c2f-5d6e7f8a9b0c", "PVEAPIToken=" + PVE, PVE + "x")
            fk = Fake(list(bad))
            rc, res = self.run_cmd([n], fk)
            self.assertEqual((rc, res["ok"], res["why"], fk.asks, fk.touches), (2, False, "bad_format", 3, 0), (n, res))
            self.assertFalse((self.sec / "pve.env").exists(), "形狀錯還寫了")
            for p in fk.prompts[1:]:
                self.assertIn("user@realm!tokenid=UUID", p)
                for b in bad:
                    self.assertNotIn(b, p, "重問的提示帶了值")
            self.assertEqual(SA.check_format(n, PVE), None)
        self.assertIsNone(SA.check_format("WPSITE_DB_PASSWORD_SHOP1", "any thing!@#"), "站點密碼不該套形狀")

    def test_p5_missing_value_never_requests_fill(self):
        for n in ("PVE_API_TOKEN", "PVE_API_TOKEN_NODE2", "WPSITE_DB_PASSWORD_SHOP1", "WPSITE_ADMIN_PASSWORD_SHOP1"):
            self.assertTrue(S.no_fill(n), n)
            with self.assertRaises(S.SecretError) as cm:
                S.secret(n, env=self.env)          # 預設 fill=True,名冊要擋
            self.assertEqual(cm.exception.reason, S.MISSING_FILE, n)
        leaked = [str(p) for p in Path(self.td.name).rglob("collect-*") if p.is_file()]
        self.assertEqual(leaked, [], "缺值還是發了補填請求")

    def test_p6_read_back_and_no_plaintext(self):
        for n, v, _f in CASES:
            self.run_cmd([n], Fake([v]))
        for n, v, _f in CASES:
            self.assertEqual(S.secret(n, env=self.env, fill=False), v, n)
        for p in Path(self.td.name).rglob("*"):
            if p.is_file():
                raw = p.read_bytes()
                for v in VALUES:
                    self.assertNotIn(v.encode(), raw, "%s 有明文" % p)


def _block(start, end):
    s = (REPO / "src" / "bootstrap" / "aib_secrets.py").read_text(encoding="utf-8")
    i = s.index(start)
    j = s.index(end, i) + len(end)
    return s[i:j]


KNIVES = (
    ("K1", "src/bootstrap/aib_secrets.py", lambda: _block('    ("pve.env", "pve", "type",', '"pve-api", False),)),\n'), "",
     "Vault.test_p1_known_local_names"),
    ("K1b", "src/bootstrap/aib_secrets.py", lambda: _block('    ("wpsite.env", "wpsite", "type",', '"site-creds", False))),\n'), "",
     "Vault.test_p1_known_local_names"),
    ("K2", "src/cockpit/secret_ask.py", lambda: '        res["fingerprint"] = fingerprint(value)\n', '        res["fingerprint"] = value\n',
     "Vault.test_p2_ask_writes_local_vault"),
    ("K3", "src/bootstrap/aib_secrets.py", lambda: '"hetzner-robot.env", "pve.env", "wpsite.env")', '"hetzner-robot.env")',
     "Vault.test_p5_missing_value_never_requests_fill"),
    ("K4", "src/cockpit/secret_ask.py", lambda: '    if shape and not shape[0].fullmatch(value):\n', '    if False:\n',
     "Vault.test_p4_pve_token_shape"),
)


class Knives(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(REPO, rel, needle(), repl, tests=(name,)) as tree:
                    r = subprocess.run([sys.executable, "-B", str(tree / "tests" / name), target], cwd=str(tree),
                                       capture_output=True, text=True, timeout=120)
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
