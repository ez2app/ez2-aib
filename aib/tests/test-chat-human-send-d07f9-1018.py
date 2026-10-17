#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""艦長室網頁代表真人寄線上私訊(10-18 @74e8b:網頁寄全球信 rc 2「推不出寄件席」)。

H1 PIN:沒設 ⇒ no_pin;明碼存的 ⇒ pin_not_hashed;對(pbkdf2)⇒ 過;錯 ⇒ bad_pin;連錯 5 次 ⇒ pin_locked、鎖完再對才過
H2 選席:這一艙公開、有金鑰的席,role 開頭 root 優先;別艙 / 沒公開 / 沒金鑰的不選;都沒有 ⇒ None
H3 指令:沒 --as-human ⇒ rc 2;PIN 只走 stdin;錯 PIN、空內文、壞位址、沒公開席都 rc 2 且一個字都沒寫進 outbox;
   PIN 對 ⇒ 待寄檔 from = 艦長席、attested、author = human(轉接程式沒開 ⇒ rc 2 bridge-off,照樣不寫)
H4 契約:outbox 的 author 只收 human / ai_draft / ai_auto;工人送出時 rumor 多一個 aib-author(要 Nostr venv)
刀(複本):K1 不驗 PIN ⇒ H3 紅
"""
import hashlib
import importlib.util
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import chat_human as CH  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_send_hs", str(HERE / "test-chat-send-d07f9-1016.py"))
SB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SB)
INST = "20261018-000000-6ddf"
TO = NK.npub(NK.pubkey_hex("%064x" % 141)) + "/cf04/5f7cf"


def pin_hash(pin, salt="00" * 16, iters=1000):
    return "pbkdf2-sha256$%d$%s$%s" % (iters, salt, hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), iters).hex())


class Human(unittest.TestCase):
    def setUp(self):
        self.b = SB.Box()
        self.addCleanup(self.b.close)
        b = self.b
        self.cap = b.seat(INST, "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "艦長")
        self.dev = b.seat(INST, "w3", "00000000-0000-0000-0000-00000000bbb2", "yyyy22", "工程席")
        self.other = b.seat("20261018-000000-9999", "w2", "00000000-0000-0000-0000-00000000ccc3", "zzzz33", "別艙")
        seats = b.home / ".ez2-aib" / "instances" / INST / "seats"
        (seats / "w2.json").write_text(json.dumps({"schema": "aib-seat/v1", "session_uuid": "00000000-0000-0000-0000-00000000aaa1",
                                                   "role": "root"}))
        self.assertEqual(b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW").returncode, 0)
        self.env = b.env()

    def set_pin(self, stored):
        p = CH.pin_config_path(self.env)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"pin": stored}))

    def send(self, pin, text="你好", to=TO, *extra):
        r = subprocess.run([sys.executable, str(SB.BRIDGE), "send", to, "--json", *extra], input=json.dumps({"pin": pin, "text": text}) + "\n",
                           capture_output=True, text=True, env=self.env, timeout=60)
        return r.returncode, (json.loads(r.stdout) if r.stdout.strip().startswith("{") else r.stdout)

    def outbox(self):
        return sorted((self.b.root / "outbox").glob("*.json")) if (self.b.root / "outbox").is_dir() else []

    def test_h1_pin(self):
        t = 1000.0
        self.assertEqual(CH.check_pin("1234", self.env, now=t), "no_pin")
        self.set_pin(pin_hash("123456"))
        self.assertIsNone(CH.check_pin("123456", self.env, now=t))
        for _ in range(4):
            self.assertEqual(CH.check_pin("000000", self.env, now=t), "bad_pin")
        self.assertEqual(CH.check_pin("000000", self.env, now=t), "bad_pin")
        self.assertEqual(CH.check_pin("123456", self.env, now=t + 1), "pin_locked", "連錯 5 次鎖 30 秒,對的也不收")
        self.assertIsNone(CH.check_pin("123456", self.env, now=t + 31))
        self.assertEqual(CH.check_pin(None, self.env, now=t + 40), "bad_pin")
        self.set_pin("4321")
        self.assertEqual(CH.check_pin("4321", self.env, now=t + 40), "pin_not_hashed", "明碼存的不認(請重設 PIN)")

    def test_h2_pick(self):
        self.assertIsNone(CH.pick_seat("6ddf", self.env), "還沒公開")
        self.b.cli("publish", self.dev)
        self.assertEqual(CH.pick_seat("6ddf", self.env), self.dev)
        self.b.cli("publish", self.cap)
        self.assertEqual(CH.pick_seat("6ddf", self.env), self.cap, "艦長席(root)優先")
        self.b.cli("publish", self.other)
        self.assertIsNone(CH.pick_seat("aaaa", self.env), "別艙的席不代簽")

    def test_h3_cli(self):
        b = self.b
        r = b.cli("send", TO)
        self.assertEqual(r.returncode, 2, "沒 --as-human 不准")
        rc, out = self.send("123456", "你好", TO, "--as-human", "--cabin", "6ddf")
        self.assertEqual((rc, out["why"]), (2, "no_pin"))
        self.set_pin(pin_hash("123456"))
        b.cli("publish", self.cap)
        for pin, text, to, why in (("999999", "你好", TO, "bad_pin"), ("123456", "  ", TO, "empty"), ("123456", "你好", "nobody", "bad_address")):
            rc, out = self.send(pin, text, to, "--as-human", "--cabin", "6ddf")
            self.assertEqual((rc, out["why"]), (2, why), why)
        b.cli("hide", self.cap)
        rc, out = self.send("123456", "你好", TO, "--as-human", "--cabin", "6ddf")
        self.assertEqual(out["why"], "no_public_seat")
        b.cli("publish", self.cap)
        self.assertEqual(self.outbox(), [], "上面那些一個字都不准寫")
        rc, out = self.send("123456", "你好", TO, "--as-human", "--cabin", "6ddf")
        self.assertEqual((rc, out["stage"], out["seat"]), (2, "bridge-off", self.cap), "轉接程式沒開 ⇒ 不寫")
        GF.write_heartbeat(b.root)
        self.env["AIB_DM_GLOBAL_WAIT"] = "0"
        proc = subprocess.Popen([sys.executable, str(SB.BRIDGE), "send", TO, "--as-human", "--cabin", "6ddf", "--json"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=dict(self.env, AIB_DM_GLOBAL_WAIT="3"))
        proc.stdin.write(json.dumps({"pin": "123456", "text": "Andy 本人寄的"}) + "\n")
        proc.stdin.close()
        got = None
        end = time.time() + 3
        while time.time() < end and not got:
            files = self.outbox()
            got = json.loads(files[0].read_text()) if files else None
            time.sleep(0.05)
        proc.wait(timeout=30)
        self.assertIsNotNone(got, "PIN 對 ⇒ 寫進待寄檔")
        self.assertEqual((got["from"]["seat"], got["from"]["attested"], got["author"], got["content"]),
                         (self.cap, True, "human", "Andy 本人寄的"))

    def test_h4_contract(self):
        base = {"v": GF.VERSION, "msgid": GF.new_msgid(), "created_at": 1, "op": "send", "to": TO, "content": "x",
                "from": {"cabin": "6ddf", "seat": "abcde", "attested": True}}
        for ok in ("human", "ai_draft", "ai_auto"):
            GF.check_outbox(dict(base, author=ok))
        with self.assertRaises(GF.ContractError):
            GF.check_outbox(dict(base, author="boss"))

    @unittest.skipUnless(SB.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_h4_rumor_tag(self):
        code = ("import sys,json;sys.path.insert(0,%r);import chat_nostr as C;"
                "t=C._rumor_tags({'msgid':'a'*32,'to':%r,'author':'human'},'x/6ddf/abcde');"
                "print(json.dumps([x.to_vec() for x in t]))") % (str(REPO / "src" / "cockpit"), TO)
        r = subprocess.run([str(Path(SB.NOSTR_VENV) / "bin" / "python"), "-c", code], capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(["aib-author", "human"], json.loads(r.stdout))


class Knife(unittest.TestCase):
    def test_k1_no_pin_check(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_human.py", "    why = check_pin(pin, env)\n", "    why = None\n",
                        tests=(name, "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Human.test_h3_cli"], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
