#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""在不在(10-24 Andy「可以」):本人打開世界面板就刷新好友在線,不用等 15 分鐘一輪。

A1 refresh.json:request_refresh 第一次 True、30 秒內 False、過了 True;asked_at 由 note_asked 寫;壞值不讀
A2 aib chat world --refresh --json:asked=true + refresh.requested_at;馬上再跑 ⇒ asked=false;不帶 --refresh ⇒ 沒有 asked、不寫檔
A3 好友超過 1 小時沒送在線狀態(或從沒送過)⇒ world quiet 列出;剛送過的、封鎖的不列;文字版印一行
(以下要 AIB_TEST_NOSTR_VENV;chat_nostr 只在 venv 裡 import 得到。不連網:寄件 / resolve 全換成假的)
Q1 push_presence 平常:不帶 aib-ask;剛送過 ⇒ 間隔內不再送
Q2 ask_due ⇒ 清掉查詢快取(重抓名冊)、不看間隔照送、帶 aib-ask=1;送完 ask_due 清掉、refresh.json 有 asked_at
Q3 沒有公開席 ⇒ 問不了,ask_due 也清掉並記 asked_at(畫面不會一直轉)
R1 收到帶 aib-ask=1 的在線狀態(好友、驗得到)⇒ answer_now、p_last 拿掉那一位;60 秒內同一位再問 ⇒ 不再觸發
R2 沒帶 aib-ask ⇒ 不觸發;陌生人帶 aib-ask ⇒ 整份丟(不觸發)
刀(複本):K1 push_presence 不看 ask ⇒ Q2 紅;K2 回答不限流 ⇒ R1 紅
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
import importlib.util  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import chat_roster as CR  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_bridge_presence_ask", str(REPO / "src" / "cockpit" / "chat_bridge.py"))
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
import dm_global_files as GF
import nostr_keys as NK
from pathlib import Path
root, case = Path(sys.argv[2]), sys.argv[3]
OWNER = NK.npub(NK.pubkey_hex("%064x" % 0x5101))
FRIEND = NK.npub(NK.pubkey_hex("%064x" % 0x5102))
FSEAT = NK.npub(NK.pubkey_hex("%064x" % 0x5103))
seats = [] if case == "noseat" else [{"seat5": "cfd04", "cabin": "6ddf", "key": "k1", "npub": "npub1me", "status": "active"}]
CN.NK.read_map = lambda *a, **k: {"owner": {"npub": OWNER}}
CN.NK.sending_seats = lambda *a, **k: seats
CN.CR.read_profile = lambda *a, **k: {"public": ["cfd04"]}
CN.CR.read_friends = lambda env=None, kind="friends": [] if kind == "mine" else [{"owner": FRIEND}]
CN.CR.share_level = lambda *a, **k: "online"
CN.CR.space_level = lambda *a, **k: "none"
CN.CR.is_blocked = lambda o, *a, **k: False
CN.CR.is_friend = lambda o, *a, **k: o == FRIEND
CN.CR.is_mine = lambda o, *a, **k: False
CN.CP.presence_power = lambda *a, **k: None
import seat_presence as SP
SP.presence = lambda ss, **k: [{"seat": s, "state": "online"} for s in ss]
CN._keys = lambda stem: None
made = []
CN.N.nip17_make_private_msg = lambda keys, pk, body, rumor_extra_tags=(): made.append([t.to_vec() for t in rumor_extra_tags]) or "gw"
class Out: success = True
class Cli:
    async def send_event(self, gw): return Out()
    async def shutdown(self): pass
async def fake_client(relays): return Cli()
CN._client = fake_client

s = object.__new__(CN.Sender)
s.root, s.cache = root, {}
async def noop(*a, **k): return None
s._push_card = noop
s._push_room = noop
async def first_seat(o): return 0, {"seat": {"npub": FSEAT}, "dm_relays": []}
s._first_seat = first_seat
out = {}
if case in ("push", "noseat"):
    if case == "push":
        out["n1"] = asyncio.run(s.push_presence(now=1000))
        out["n2"] = asyncio.run(s.push_presence(now=1010))
    s.ask_due = True
    s.cache = {"stale": (0, None)}
    out["n3"] = asyncio.run(s.push_presence(now=1020))
    out["made"], out["ask_due"], out["refresh"], out["cache"] = made, s.ask_due, GF.read_refresh(root), list(s.cache)
else:
    r = object.__new__(CN.Receiver)
    r.root, r.sender = root, s
    async def resolve(addr): return 0, {"seat": {"npub": FSEAT}}
    s._resolve = resolve
    class Rumor:
        def content(self): return json.dumps({"v": 1, "seats": [{"seat": "754c1", "cabin": "754c", "state": "online"}]})
    who = FRIEND if case != "stranger" else NK.npub(NK.pubkey_hex("%064x" % 0x5104))
    tags = {"aib-from": who + "/754c/754c1"}
    if case != "noask":
        tags["aib-ask"] = "1"
    res = []
    for _ in range(2):
        s.p_last = {FRIEND: ("x", 1)}
        s.answer_now = False
        asyncio.run(r.on_presence(Rumor(), tags, FSEAT))
        res.append({"answer": s.answer_now, "popped": FRIEND not in s.p_last})
    out["rounds"] = res
print(json.dumps(out))
'''


class Refresh(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-ask-"))
        self.addCleanup(shutil.rmtree, self.td, True)

    def test_a1_file(self):
        self.assertEqual(GF.read_refresh(self.td), {})
        self.assertTrue(GF.request_refresh(self.td, now=1000))
        self.assertFalse(GF.request_refresh(self.td, now=1029))
        self.assertTrue(GF.request_refresh(self.td, now=1030))
        GF.note_asked(self.td, now=1031)
        self.assertEqual(GF.read_refresh(self.td), {"requested_at": 1030, "asked_at": 1031})
        (self.td / "refresh.json").write_text(json.dumps({"v": GF.VERSION, "requested_at": "x", "asked_at": True}))
        self.assertEqual(GF.read_refresh(self.td), {})

    def cli(self, *args):
        env = dict(os.environ, HOME=str(self.td / "home"), EZ2_AIB_STATE_ROOT=str(self.td / "home" / ".ez2-aib"),
                   AIB_LANG="zh-TW")
        r = subprocess.run([sys.executable, str(REPO / "src" / "cockpit" / "chat_bridge.py")] + list(args),
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_a2_cli(self):
        root = GF.default_root({"HOME": str(self.td / "home"), "EZ2_AIB_STATE_ROOT": str(self.td / "home" / ".ez2-aib")})
        pl = json.loads(self.cli("world", "--json"))
        self.assertNotIn("asked", pl)
        self.assertEqual(pl["refresh"], {})
        self.assertFalse((Path(root) / "refresh.json").exists())
        pl = json.loads(self.cli("world", "--refresh", "--json"))
        self.assertTrue(pl["asked"])
        self.assertIsInstance(pl["refresh"]["requested_at"], int)
        self.assertFalse(json.loads(self.cli("world", "--refresh", "--json"))["asked"])
        self.assertNotIn("aib.chat.world_ask", self.cli("world", "--refresh"))     # 字典有這句(不是退回鍵名)

    def test_a3_quiet(self):
        env = {"HOME": str(self.td / "h3"), "EZ2_AIB_STATE_ROOT": str(self.td / "h3" / ".ez2-aib")}
        root = CB.bridge_root(env)
        fa, fb, fc, fd = (NK.npub(NK.pubkey_hex("%064x" % (0x5201 + i))) for i in range(4))
        for f in (fa, fb, fc):
            CR.add_friend(f, env=env)
        CR.add_friend(fd, env=env, kind="blocks")
        now = 100000
        GF.note_peer(root, fb, "Bob", "TW", None, now=now)
        GF.note_world(root, fa, "c1", [], now=now - 100)                 # 剛送過
        GF.note_world(root, fb, "c2", [], now=now - 3700)                # 一小時多沒送
        quiet = CB.world_payload(env, now=now)["quiet"]
        self.assertEqual(sorted((q["owner"], q["presence_at"]) for q in quiet), sorted([(fb, now - 3700), (fc, None)]))
        self.assertEqual(next(q for q in quiet if q["owner"] == fb)["display_name"], "Bob")


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Ask(unittest.TestCase):
    def probe(self, case, repo=REPO):
        td = Path(tempfile.mkdtemp(prefix="aib-ask-p-"))
        self.addCleanup(shutil.rmtree, td, True)
        (td / "root").mkdir()
        env = dict(os.environ, HOME=str(td / "home"), EZ2_AIB_STATE_ROOT=str(td / "home" / ".ez2-aib"))
        r = subprocess.run([VPY, "-c", PROBE, str(Path(repo) / "src" / "cockpit"), str(td / "root"), case],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_q1_q2_push(self):
        d = self.probe("push")
        self.assertEqual((d["n1"], d["n2"], d["n3"]), (1, 0, 1))
        self.assertNotIn(["aib-ask", "1"], d["made"][0])
        self.assertIn(["aib-ask", "1"], d["made"][1])
        self.assertFalse(d["ask_due"])
        self.assertEqual(d["refresh"], {"asked_at": 1020})
        self.assertEqual(d["cache"], [])                  # 重抓名冊

    def test_q3_no_seat(self):
        d = self.probe("noseat")
        self.assertEqual((d["n3"], d["made"], d["ask_due"], d["refresh"]), (0, [], False, {"asked_at": 1020}))

    def test_r1_answer_rate_limited(self):
        self.assertEqual(self.probe("ask")["rounds"], [{"answer": True, "popped": True}, {"answer": False, "popped": False}])

    def test_r2_no_ask_or_stranger(self):
        for case in ("noask", "stranger"):
            self.assertEqual(self.probe(case)["rounds"], [{"answer": False, "popped": False}] * 2, case)


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
class Knife(unittest.TestCase):
    def _run(self, anchor, repl, which):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_nostr.py", anchor, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), which], cwd=str(root), max_procs=60, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k1_ignore_ask(self):
        self._run('        ask = getattr(self, "ask_due", False)\n', "        ask = False\n", "Ask.test_q1_q2_push")

    def test_k2_no_answer_gap(self):
        self._run("            if time.time() - asked.get(owner, 0) >= ASK_ANSWER_GAP_S:\n", "            if True:\n",
                  "Ask.test_r1_answer_rate_limited")


if __name__ == "__main__":
    unittest.main()
