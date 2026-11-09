#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""主機 root 密碼進名冊 + 輸入框的「產生並複製」鈕(11-09 @4542e;Andy 親打「放行 ID-2」後要設 EU02 的 root@pam 密碼,
視窗要有「隨機產生合規的按鈕 … 也要有一個複製鈕」)。

R1 HOST_ROOT_PASSWORD 在名冊(EXACT_FILES,不是靠前綴猜),落 host-root.env;加主機代號(HOST_ROOT_PASSWORD_EU02)同檔;
   只准 --remote-only、缺值不補填、不是 SE-only
R2 只有 HOST_ROOT_ 開頭落這檔:HOST_X、HOSTGUARD_TOKEN、PVE_ROOT_PASSWORD_EU02(ebb36 #545 L1 說要另開一格 ⇒ 就是這格,不是 pve.env)都不落
R3 沒帶 --remote-only ⇒ remote_only_name,不叫輸入框、不碰 ssh;帶了但沒 --push ⇒ remote_only_needs_push
R4 --generate:JXA 多一顆「產生並複製」鈕(arc4random 產 GEN_LEN ≥ 24 字、寫剪貼簿、值放回欄位);沒帶就沒有;
   collect 把 generate 傳進 dialog;回傳的值一樣只認 nonce 對得上的那行
刀(在複本上跑本檔,要紅):K1 名冊拿掉 host-root.env 那格 ⇒ R1;K2 REMOTE_ONLY_FILES 拿掉 host-root.env ⇒ R1 / R3;
   K3 NO_SEGMENT_PREFIX_FILES 拿掉 host-root.env ⇒ R2(HOST_X 會落進來);K4 build_script 不看 generate ⇒ R4
"""
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "src" / "bootstrap"))
import aib_secrets as S  # noqa: E402
import secret_ask as SA  # noqa: E402

NAME = "HOST_ROOT_PASSWORD_EU02"


class HostRoot(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-secret-hostroot-")
        root = Path(self.td.name)
        self.env = {"HOME": str(root), "EZ2_AIB_STATE_ROOT": str(root / "state"), "EZ2_AIB_SECRETS_DIR": str(root / "secrets"),
                    "EZ2_AIB_LOCALE": "zh-TW"}
        self._old = {k: os.environ.get(k) for k in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_SECRETS_DIR", "EZ2_AIB_LOCALE")}
        os.environ.update({k: self.env[k] for k in self._old})

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.td.cleanup()

    def test_r1_in_roster_remote_only(self):
        self.assertEqual(S.EXACT_FILES.get("HOST_ROOT_PASSWORD"), "host-root.env")
        for n in ("HOST_ROOT_PASSWORD", NAME, "HOST_ROOT_PASSWORD_SIN02"):
            self.assertEqual(S.file_for_name(n), "host-root.env", n)
            self.assertTrue(S.valid_name(n))
            self.assertTrue(S.is_remote_only(n), n)
            self.assertTrue(S.no_fill(n), n)
            self.assertNotIn(n, S.SE_ONLY_NAMES)

    def test_r2_only_host_root_prefix(self):
        for n in ("HOST_X", "HOSTGUARD_TOKEN", "HOST_PASSWORD", "PVE_ROOT_PASSWORD_EU02", "ROOT_PASSWORD"):
            self.assertIsNone(S.file_for_name(n), n)

    def test_r3_refuses_local_store(self):
        calls = []

        def ask(prompt, title, **kw):
            calls.append("ask")
            return {"ok": True, "why": "ok", "value": "x" * 28}

        out = io.StringIO()
        rc = SA.ask_cmd([NAME], env=dict(self.env), platform="darwin", ask=ask, touchid=lambda r: {"ok": True},
                        touchid_available=lambda: True, out=out, ssh_run=lambda *a, **k: self.fail("不該碰 ssh"))
        res = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual((rc, res["ok"], res["why"], calls), (2, False, "remote_only_name", []))
        out = io.StringIO()
        rc = SA.ask_cmd([NAME, "--remote-only"], env=dict(self.env), platform="darwin", ask=ask, touchid=lambda r: {"ok": True},
                        touchid_available=lambda: True, out=out, ssh_run=lambda *a, **k: self.fail("不該碰 ssh"))
        res = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual((rc, res["why"], calls), (2, "remote_only_needs_push", []))
        self.assertFalse((Path(self.env["EZ2_AIB_SECRETS_DIR"]) / "host-root.env").exists())

    def test_r4_generate_button(self):
        plain = SA.build_script("p", "t", "n" * 32)
        gen = SA.build_script("p", "t", "n" * 32, generate=True)
        self.assertNotIn("arc4random", plain)
        self.assertNotIn(json.dumps("產生並複製"), plain)
        self.assertIn("arc4random_uniform", gen)
        self.assertIn("setTheClipboardTo", gen)
        self.assertIn(json.dumps("產生並複製"), gen)
        self.assertGreaterEqual(SA.GEN_LEN, 24)
        self.assertIn("out.length < %d" % SA.GEN_LEN, gen)
        for frag in ("__PROMPT__", "__TITLE__", "__OK__", "__CANCEL__", "__GEN__", "__GEN_HINT__", "__GEN_LEN__", "__NONCE__", "__WAIT__"):
            self.assertNotIn(frag, gen, frag)
        # collect 把 generate 一路傳到 dialog;值只認 nonce 對得上的那行
        seen = {}

        def run(cmd, input=None, **kw):
            seen["gen"] = "arc4random_uniform" in input
            nonce = re.search(r'nonce: "([0-9a-f]{32})"', input).group(1)
            return type("R", (), {"stdout": json.dumps({"ok": True, "nonce": nonce, "value": "Ab3!" * 7}), "returncode": 0})()
        old = SA.dialog
        try:
            SA.dialog = lambda p, t, **kw: old(p, t, run=run, **kw)
            value, why = SA.collect(NAME, "eu02", "darwin", generate=True)
        finally:
            SA.dialog = old
        self.assertEqual((value, why, seen["gen"]), ("Ab3!" * 7, None, True))
        bad = SA.dialog("p", "t", run=lambda cmd, input=None, **kw: type("R", (), {"stdout": json.dumps({"ok": True, "nonce": "0" * 32, "value": "x"}), "returncode": 0})(), generate=True)
        self.assertEqual(bad["why"], "nonce_mismatch")

    def test_r5_clipboard_cleared_after_generate(self):
        """產生的密碼進了剪貼簿(同帳號 AI 也讀得到)⇒ 視窗一關就 pbcopy 清空;清不掉回 False,不影響結果。"""
        calls = []

        def run(cmd, input=None, **kw):
            calls.append((cmd, input))
            return type("R", (), {"returncode": 0})()
        self.assertTrue(SA.clear_clipboard(run=run))
        self.assertEqual(calls, [(["pbcopy"], b"")])
        self.assertFalse(SA.clear_clipboard(run=lambda *a, **k: (_ for _ in ()).throw(OSError("no pbcopy"))))
        # ask_cmd with --generate: the dialog closes (here: value refused three times ⇒ bad_format) and the
        # clipboard is cleared right after the dialog, before any later refusal
        out = io.StringIO()
        rc = SA.ask_cmd(["PVE_API_TOKEN", "--generate"], env=dict(self.env), platform="darwin", ask=lambda p, t, **kw: {"ok": True, "why": "ok", "value": "x" * 28},
                        touchid=lambda r: {"ok": True}, touchid_available=lambda: True, out=out, ssh_run=lambda *a, **k: self.fail("不該碰 ssh"))
        res = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual((rc, res["why"]), (2, "bad_format"))
        self.assertIn("clipboard_cleared", res)


if __name__ == "__main__":
    unittest.main(verbosity=2)
