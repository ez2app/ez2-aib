#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""收信 session 先建接收器、再訂閱(10-30 @aeb40)。
起因:main 全套 test-chat-phone-pair N1「候選沒進來」不穩定紅(單跑三次紅兩次,v0.1.63 也一樣)。
站在 REQ 一到就回放已存的信;nostr-sdk 的通知是廣播,回放時還沒建 notifications() ⇒ 那幾則直接丟,
工人 on_wrap 都沒進、received 0。真機上 = 離線期間寄來的信補收不到。

O1(要 AIB_TEST_NOSTR_VENV)Receiver._session 對假 client:subscribe() 當下就把一則已存的信廣播給「已經建好」的
   接收器(跟 nostr-sdk 一樣,之後才建的收不到)⇒ on_wrap 要收到那一則
刀(複本):K1 換回「先訂閱、後建接收器」⇒ O1 紅
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

NOSTR_VENV = os.environ.get("AIB_TEST_NOSTR_VENV") or ""
VPY = str(Path(NOSTR_VENV) / "bin" / "python") if NOSTR_VENV else ""
HAVE_NOSTR = bool(VPY) and Path(VPY).exists() and subprocess.run(
    [VPY, "-c", "import nostr_sdk"], capture_output=True).returncode == 0

PROBE = r'''
import asyncio, json, sys
sys.path.insert(0, sys.argv[1])
import nostr_sdk as N
import chat_nostr as CN

class Note(object):
    def __init__(self, ev):
        self.event = ev
    def is_NEW_EVENT(self):
        return True

class Stream(object):
    def __init__(self):
        self.q = []
    async def next(self):
        while not self.q:
            await asyncio.sleep(0.05)
        return self.q.pop(0)

class FakeClient(object):
    """廣播:subscribe() 時只送給「當下已建好」的接收器(nostr-sdk 的 broadcast 也是這樣)。"""
    def __init__(self):
        self.streams = []
    def notifications(self):
        s = Stream()
        self.streams.append(s)
        return s
    async def subscribe(self, target):
        for s in self.streams:
            s.q.append(Note("stored-wrap"))
    async def shutdown(self):
        pass

async def fake_client(relays):
    return FakeClient()

CN._client = fake_client
CN.CR.relays = lambda: ["ws://127.0.0.1:1"]
r = CN.Receiver(sys.argv[2], None)
got = []
stop = asyncio.Event()
async def on_wrap(ev, seats):
    got.append(ev)
    stop.set()
r.on_wrap = on_wrap
seat = N.Keys.generate().public_key().to_hex()

async def main():
    try:
        await asyncio.wait_for(r._session(stop, {seat: {"key": "x", "seat5": "aaaaa"}}), 5)
    except asyncio.TimeoutError:
        pass
asyncio.run(main())
print(json.dumps({"got": got}))
'''


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk 的 venv)")
class Order(unittest.TestCase):
    def test_o1_backfill_not_dropped(self):
        import json
        with tempfile.TemporaryDirectory(prefix="aib-recvorder-") as td:
            root = Path(td) / "bridge"
            root.mkdir()
            env = dict(os.environ, HOME=td, EZ2_AIB_STATE_ROOT=td)
            r = subprocess.run([VPY, "-c", PROBE, str(COCKPIT), str(root)], env=env, capture_output=True, text=True,
                               timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            got = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(got["got"], ["stored-wrap"], "訂閱當下回放的信沒收到(接收器建太晚)")


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk 的 venv)")
class Knife(unittest.TestCase):
    def test_k1_subscribe_before_stream(self):
        name = Path(__file__).name
        needle = "            stream = c.notifications()\n            await c.subscribe(N.ReqTarget.auto([flt]))\n"
        repl = "            await c.subscribe(N.ReqTarget.auto([flt]))\n            stream = c.notifications()\n"
        with knife_copy(REPO, "src/cockpit/chat_nostr.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Order.test_o1_backfill_not_dropped"],
                            cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
