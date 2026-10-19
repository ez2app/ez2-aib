#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""收件去重紀錄(recv-state.json)不能長過 atomic_json 64 KB 上限(10-20 實機:Pro 13 收得兇,一週內的去重
紀錄 984 筆全長鍵 = 65,527 bytes ⇒ 每次存檔 AtomicWriteError ⇒ 收件整條斷線 5 秒重來,中繼站永遠 0/3)。

L1 去重鍵只存短鍵(e:/m: + 16 碼);seen / mark 用全長鍵照樣認得
L2 舊版全長鍵的檔讀進來就換成短鍵,原本記過的照樣算看過
L3 一週內收 5,000 筆也存得進去(≤ SEEN_MAX 筆、檔案 < 64 KB);丟的是最舊的,最新的還在
刀(複本):K1 不設上限 ⇒ L3 紅(存檔丟 AtomicWriteError)
要 AIB_TEST_NOSTR_VENV(chat_nostr 要 nostr-sdk);沒有就跳過。
"""
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
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

NOSTR_VENV = os.environ.get("AIB_TEST_NOSTR_VENV") or ""
VPY = str(Path(NOSTR_VENV) / "bin" / "python") if NOSTR_VENV else ""
HAVE_NOSTR = bool(VPY) and Path(VPY).exists() and subprocess.run(
    [VPY, "-c", "import nostr_sdk"], capture_output=True).returncode == 0

PROBE = r'''
import hashlib, json, os, sys, time
sys.path.insert(0, sys.argv[1])
import chat_nostr as CN
root, case = sys.argv[2], sys.argv[3]
full = lambda i: "e:" + hashlib.sha256(str(i).encode()).hexdigest()
out = {}
if case == "l1":
    L = CN.Ledger(root); L.mark(full(1)); L.mark("m:" + "ab" * 16); L.save()
    st = json.load(open(os.path.join(root, "recv-state.json")))
    L2 = CN.Ledger(root)
    out = {"keys": sorted(st["seen"]), "seen": [L2.seen(full(1)), L2.seen("m:" + "ab" * 16), L2.seen(full(2))]}
elif case == "l2":
    now = int(time.time())
    json.dump({"seen": {full(i): now for i in range(1000)}, "last_at": now}, open(os.path.join(root, "recv-state.json"), "w"))
    L = CN.Ledger(root); L.save()
    out = {"all_seen": all(L.seen(full(i)) for i in range(1000)), "n": len(L.st["seen"]),
           "bytes": os.path.getsize(os.path.join(root, "recv-state.json"))}
elif case == "l3":
    L = CN.Ledger(root); now = int(time.time())
    for i in range(5000):
        L.st["seen"][L._k(full(i))] = now - 5000 + i
    L.save()
    L2 = CN.Ledger(root)
    out = {"n": len(L2.st["seen"]), "max": CN.SEEN_MAX, "bytes": os.path.getsize(os.path.join(root, "recv-state.json")),
           "newest": L2.seen(full(4999)), "oldest": L2.seen(full(0))}
print(json.dumps(out))
'''


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Ledger(unittest.TestCase):
    def run_case(self, case):
        with tempfile.TemporaryDirectory(prefix="aib-ledger-") as td:
            env = dict(os.environ, HOME=td, EZ2_AIB_STATE_ROOT=td)
            r = subprocess.run([VPY, "-c", PROBE, str(REPO / "src" / "cockpit"), td, case], env=env,
                               capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_l1_short_keys(self):
        got = self.run_case("l1")
        self.assertEqual([len(k) for k in got["keys"]], [18, 18])
        self.assertEqual(got["seen"], [True, True, False])

    def test_l2_migrate_full_keys(self):
        got = self.run_case("l2")
        self.assertTrue(got["all_seen"])
        self.assertEqual(got["n"], 1000)
        self.assertLess(got["bytes"], 64 * 1024)

    def test_l3_cap(self):
        got = self.run_case("l3")
        self.assertEqual(got["n"], got["max"])
        self.assertLess(got["bytes"], 64 * 1024)
        self.assertEqual((got["newest"], got["oldest"]), (True, False))


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
class Knife(unittest.TestCase):
    def test_k1_no_cap(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_nostr.py", "        if len(seen) > SEEN_MAX:", "        if False:",
                        tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Ledger.test_l3_cap"], cwd=str(root),
                            max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
