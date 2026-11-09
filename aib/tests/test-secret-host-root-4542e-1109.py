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
        self.assertNotIn("setTheClipboardTo", gen, "一般剪貼簿會同步到其他 Apple 裝置、進剪貼簿歷史(1ced7 M1')")
        # 1ced7 M1':只留本機 + concealed / transient;拿掉任一個這裡要紅
        self.assertIn("NSPasteboardContentsCurrentHostOnly", gen)
        self.assertIn("org.nspasteboard.ConcealedType", gen)
        self.assertIn("org.nspasteboard.TransientType", gen)
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
        """產生的密碼進了剪貼簿 ⇒ 視窗關掉時:JXA 自己比對剪貼簿 == 最後一次產生值才清(L8)、沒按過產生就不碰(L9)、
        視窗沒正常回來(中斷)才由 Python 保守清一次(L5;有值先比對 L4)。測試注入假的清除,不碰真剪貼簿(L6);斷言呼叫次數與值(L7)。"""
        calls = []

        def fake(content):
            def run(cmd, input=None, **kw):
                calls.append((cmd, input))
                return type("R", (), {"returncode": 0, "stdout": content.encode() if cmd == ["pbpaste"] else b""})()
            return run
        self.assertTrue(SA.clear_clipboard(run=fake("whatever")))          # no value: clear unconditionally
        self.assertEqual(calls, [(["pbcopy"], b"")])
        calls.clear()
        self.assertTrue(SA.clear_clipboard("Ab3!" * 7, run=fake("Ab3!" * 7 + "\n")))   # same value: compare, clear
        self.assertEqual(calls, [(["pbpaste"], None), (["pbcopy"], b"")])
        calls.clear()
        self.assertFalse(SA.clear_clipboard("Ab3!" * 7, run=fake("shopping list")))     # the person copied something else
        self.assertEqual(calls, [(["pbpaste"], None)])
        self.assertFalse(SA.clear_clipboard(run=lambda *a, **k: (_ for _ in ()).throw(OSError("no pbcopy"))))

        # ask_cmd paths, with an injected clearer (never the real clipboard)
        def harness(argv, ask):
            cleared = []
            out = io.StringIO()
            rc = SA.ask_cmd(argv, env=dict(self.env), platform="darwin", ask=ask, touchid=lambda r: {"ok": True},
                            touchid_available=lambda: True, out=out, ssh_run=lambda *a, **k: self.fail("不該碰 ssh"),
                            clipboard=lambda v: cleared.append(v) or True)
            return rc, json.loads(out.getvalue().strip().splitlines()[-1]), cleared
        # 1ced7 L8 / L9:視窗正常回來 ⇒ 清不清由 JXA 比對決定(generated / cleared 旗標),Python 不再碰剪貼簿
        # generated + JXA cleared, then refused later (bad shape) → "cleared", no pbcopy from Python
        rc, res, cleared = harness(["PVE_API_TOKEN", "--generate"],
                                   lambda p, t, **kw: {"ok": True, "why": "ok", "value": "x" * 28, "generated": True, "cleared": True})
        self.assertEqual((rc, res["why"], res["clipboard"], res["clipboard_cleared"], cleared), (2, "bad_format", "cleared", True, []))
        # L8:generated, but the clipboard was no longer the generated value (person copied something else) → "kept", untouched
        rc, res, cleared = harness(["PVE_API_TOKEN", "--generate"],
                                   lambda p, t, **kw: {"ok": True, "why": "ok", "value": "x" * 28, "generated": True, "cleared": False})
        self.assertEqual((res["clipboard"], res["clipboard_cleared"], cleared), ("kept", False, []))
        # L9:cancelled without ever pressing generate → the person's own clipboard is left alone
        rc, res, cleared = harness(["PVE_API_TOKEN", "--generate"], lambda p, t, **kw: {"ok": False, "why": "cancel", "generated": False})
        self.assertEqual((rc, res["clipboard"], res["clipboard_cleared"], cleared), (2, "not_generated", False, []))
        # cancelled after generating: JXA compared and cleared → "cleared"
        rc, res, cleared = harness(["PVE_API_TOKEN", "--generate"],
                                   lambda p, t, **kw: {"ok": False, "why": "cancel", "generated": True, "cleared": True})
        self.assertEqual((res["clipboard"], res["clipboard_cleared"], cleared), ("cleared", True, []))
        # 1ced7 #610 M1:a result WITHOUT the flags is not "the window came back" — Python timeout,
        # osascript dead, unreadable output — so the conservative clear must run (value unknown)
        for stub in ({"ok": False, "why": "cancel"}, {"ok": False, "why": "timeout"}, {"ok": False, "why": "osascript_failed"}, {"ok": False, "why": "unreadable"}):
            rc, res, cleared = harness(["PVE_API_TOKEN", "--generate"], lambda p, t, _s=stub, **kw: dict(_s))
            self.assertEqual((res["clipboard"], cleared), ("cleared", [None]), str(stub))
        # the real dialog() on a subprocess timeout returns no flags either (REPRO-610-m1)
        import subprocess as _sp
        r = SA.dialog("p", "t", run=lambda cmd, input=None, **kw: (_ for _ in ()).throw(_sp.TimeoutExpired(cmd, 1)), generate=True)
        self.assertEqual(r["why"], "timeout"); self.assertNotIn("generated", r)
        # 1ced7 #610 L1:cleared follows the LAST round that pressed generate; a later round that did not
        # press it leaves the verdict alone (round 1 generated + cleared, bad value → round 2 cancel)
        seq = iter([{"ok": True, "why": "ok", "value": "short", "generated": True, "cleared": True},
                    {"ok": False, "why": "cancel", "generated": False, "cleared": False}])
        rc, res, cleared = harness(["PVE_API_TOKEN", "--generate"], lambda p, t, **kw: next(seq))
        self.assertEqual((res["why"], res["clipboard"], cleared), ("cancel", "cleared", []))
        seq2 = iter([{"ok": True, "why": "ok", "value": "short", "generated": True, "cleared": True},
                     {"ok": False, "why": "cancel", "generated": True, "cleared": False}])
        rc, res, cleared = harness(["PVE_API_TOKEN", "--generate"], lambda p, t, **kw: next(seq2))
        self.assertEqual((res["clipboard"], cleared), ("kept", []))
        # dialog() passes the JXA flags through on every parsed path
        mk = lambda d: (lambda cmd, input=None, **kw: type("R", (), {"stdout": json.dumps(d), "returncode": 0, "stderr": ""})())  # noqa: E731
        r = SA.dialog("p", "t", run=mk({"ok": False, "why": "cancel", "generated": True, "cleared": True}), generate=True)
        self.assertEqual((r["why"], r["generated"], r["cleared"]), ("cancel", True, True))
        r = SA.dialog("p", "t", run=mk({"ok": True, "nonce": "no", "value": "x", "generated": True, "cleared": False}), generate=True)
        self.assertEqual((r["why"], r["generated"], r["cleared"]), ("nonce_mismatch", True, False))
        # the JXA does the compare itself and reports both flags; the generated value never leaves the script
        gen = SA.build_script("p", "t", "n" * 32, generate=True)
        for needle in ("function settleClipboard(last)", "ObjC.unwrap(cur) === last", "pb.clearContents()",
                       "out.generated = (last !== null)", "out.cleared = settleClipboard(last)", "last = def;"):
            self.assertIn(needle, gen)
        self.assertNotIn("settleClipboard", SA.build_script("p", "t", "n" * 32))   # plain dialog: no clipboard code at all
        # interrupted inside the dialog: the window never came back → the finally still clears once (L5), value unknown
        def boom(p, t, **kw):
            raise KeyboardInterrupt
        cleared2 = []
        with self.assertRaises(KeyboardInterrupt):
            SA.ask_cmd(["PVE_API_TOKEN", "--generate"], env=dict(self.env), platform="darwin", ask=boom, touchid=lambda r: {"ok": True},
                       touchid_available=lambda: True, out=io.StringIO(), ssh_run=lambda *a, **k: self.fail("ssh"),
                       clipboard=lambda v: cleared2.append(v) or True)
        self.assertEqual(cleared2, [None])
        # not macOS: --generate is refused up front, nothing opened, nothing cleared (L2)
        out = io.StringIO()
        rc = SA.ask_cmd(["PVE_API_TOKEN", "--generate"], env=dict(self.env), platform="linux", ask=lambda p, t, **kw: self.fail("不該開視窗"),
                        touchid=lambda r: {"ok": True}, touchid_available=lambda: True, out=out, ssh_run=lambda *a, **k: self.fail("ssh"),
                        clipboard=lambda v: self.fail("不該清"))
        self.assertEqual((rc, json.loads(out.getvalue().strip().splitlines()[-1])["why"]), (2, "generate_needs_mac"))

    def test_r6_gen_really_runs(self):
        """1ced7 L3:gen() 不只比字串 —— 有 osascript 的機器真的跑它 20 次:長度 GEN_LEN、四類各至少一個、沒有 l I O 0 1、
        沒有引號 / 空白 / 反斜線(chpasswd 與 SQL 都安全),20 次不重複。沒有 osascript(Linux CI)就具名 skip。"""
        if not os.path.exists(SA.OSASCRIPT):
            self.skipTest("no osascript on this platform")
        import subprocess
        src = SA.JXA_GEN_FN.replace("__GEN_LEN__", str(SA.GEN_LEN)) + "\nvar r = []; for (var k = 0; k < 20; k++) r.push(gen()); JSON.stringify(r);"
        r = subprocess.run([SA.OSASCRIPT, "-l", "JavaScript", "-"], input=src, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        pws = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(len(set(pws)), 20)
        for pw in pws:
            self.assertEqual(len(pw), SA.GEN_LEN, pw)
            self.assertTrue(any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw) and any(c in "-_.!@#%^*+=" for c in pw), pw)
            self.assertFalse(set(pw) & set("lIO01'\"\\ \t\n"), pw)
            self.assertIsNone(SA.check_format("HOST_ROOT_PASSWORD_EU02", pw))


if __name__ == "__main__":
    unittest.main(verbosity=2)
