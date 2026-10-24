#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""轉接程式定期查自己的名冊(10-24 清單第 5 條:Pro 名冊同席三把,別台整份拒收四天,這台自己完全看不出來)。

C1 self_check_lines:沒查過 / 都查得到 ⇒ 沒有行;查不到 ⇒ 每席一行 ⚠(帶 why 與 hint,字典有這句);壞席碼 / 壞形狀不印
(以下要 AIB_TEST_NOSTR_VENV;chat_nostr 只在 venv 裡 import 得到。不連網:resolve 換成假的)
C2 self_check:公開兩席,一席查得到、一席回 no_seat + hint ⇒ n=2、bad 只有那一席(帶 hint);
   查得到但 npub 不是這台的 ⇒ wrong_key;沒公開席 ⇒ n=0、bad 空
刀(複本):K1 self_check 不比 npub ⇒ C2 紅
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_bridge_self_check", str(COCKPIT / "chat_bridge.py"))
CB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CB)

NOSTR_VENV = os.environ.get("AIB_TEST_NOSTR_VENV") or ""
VPY = str(Path(NOSTR_VENV) / "bin" / "python") if NOSTR_VENV else ""
HAVE_NOSTR = bool(VPY) and Path(VPY).exists() and subprocess.run(
    [VPY, "-c", "import nostr_sdk"], capture_output=True).returncode == 0

PROBE = r'''
import asyncio, json, sys
sys.path.insert(0, sys.argv[1])
import chat_nostr as CN
import nostr_keys as NK
case = sys.argv[2]
OWNER = NK.npub(NK.pubkey_hex("%064x" % 0x7101))
seats = [{"seat5": "cfd04", "cabin": "6ddf", "npub": "npub1a"}, {"seat5": "d07f9", "cabin": "6ddf", "npub": "npub1b"}]
CN.NK.read_map = lambda *a, **k: {"owner": {"npub": OWNER}}
CN.NK.sending_seats = lambda *a, **k: seats
CN.CR.read_profile = lambda *a, **k: {"public": [] if case == "none" else ["cfd04", "d07f9"]}
async def fake_resolve(addr, relays, root=None):
    seat = addr.rsplit("/", 1)[1]
    if seat == "cfd04":
        return 0, {"ok": True, "seat": {"npub": "npub1zzz" if case == "wrong" else "npub1a"}}
    return 2, {"ok": False, "why": "no_seat", "hint": "dup"}
CN.resolve = fake_resolve
print(json.dumps(asyncio.run(CN.self_check(None, now=500))))
'''


class Lines(unittest.TestCase):
    def test_c1_lines(self):
        self.assertEqual(CB.self_check_lines(None), [])
        self.assertEqual(CB.self_check_lines({"self_check": {"at": 1, "n": 2, "bad": []}}), [])
        self.assertEqual(CB.self_check_lines({"self_check": "x"}), [])
        lines = CB.self_check_lines({"self_check": {"at": 1, "n": 2, "bad": [
            {"seat": "cfd04", "why": "no_seat", "hint": "列了兩次"}, {"seat": "../x", "why": "no_seat"}, "junk"]}})
        self.assertEqual(len(lines), 1)
        self.assertIn("@cfd04", lines[0])
        self.assertIn("no_seat · 列了兩次", lines[0])
        self.assertNotIn("aib.chat.", lines[0])


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Check(unittest.TestCase):
    def probe(self, case):
        td = Path(tempfile.mkdtemp(prefix="aib-selfchk-"))
        self.addCleanup(shutil.rmtree, td, True)
        env = dict(os.environ, HOME=str(td), EZ2_AIB_STATE_ROOT=str(td / ".ez2-aib"))
        r = subprocess.run([VPY, "-c", PROBE, str(COCKPIT), case], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_c2_check(self):
        self.assertEqual(self.probe("one_bad"), {"at": 500, "n": 2, "bad": [{"seat": "d07f9", "why": "no_seat", "hint": "dup"}]})
        self.assertEqual([b["why"] for b in self.probe("wrong")["bad"]], ["wrong_key", "no_seat"])
        self.assertEqual(self.probe("none"), {"at": 500, "n": 0, "bad": []})


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
class Knife(unittest.TestCase):
    def test_k1_no_npub_compare(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_nostr.py",
                        '        if rc != 0 or (res.get("seat") or {}).get("npub") != e["npub"]:\n            row = {"seat": e["seat5"]',
                        '        if rc != 0:\n            row = {"seat": e["seat5"]', tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Check"], cwd=str(root), max_procs=60, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
