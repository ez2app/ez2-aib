#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""艦長室網頁代表真人寄線上私訊(10-18 @74e8b:網頁寄全球信 rc 2「推不出寄件席」)。

H1 PIN:沒設 ⇒ no_pin;明碼存的 ⇒ pin_not_hashed;不到 6 位 ⇒ pin_too_short;對(pbkdf2)⇒ 過;錯 ⇒ bad_pin;
   連錯 5 次鎖 30 秒、之後每錯一次翻倍、上限 1 小時;輸對才歸零
H2 選席:這一艙公開、有金鑰的席,role 開頭 root 優先;別艙 / 沒公開 / 沒金鑰的不選;都沒有 ⇒ None
H3 指令:沒 --as-human ⇒ rc 2;PIN 只走 stdin;錯 PIN、空內文、壞位址、沒公開席都 rc 2 且一個字都沒寫進 outbox;
   PIN 對 ⇒ 待寄檔 from = 艦長席、attested、author = human(轉接程式沒開 ⇒ rc 2 bridge-off,照樣不寫)
H4 契約:outbox 的 author 只收 human / ai_draft / ai_auto;工人送出時 rumor 多一個 aib-author(要 Nostr venv)
H5 Mac(Touch ID 可用):彈視窗、PIN 不看;沒過 ⇒ 不寫;視窗文字核心組(收件人 + 內文前段、去控制字元);整台一次一窗(busy);
   AIB_TOUCHID=0 只關不開
H6 併發猜 PIN:8 個同時 ⇒ 剛好 5 次算錯、其餘被鎖(讀-判-寫上鎖)
刀(複本):K1 不驗 PIN ⇒ H3 紅;K2 拿掉遞增(固定 30 秒)⇒ H1 紅;K3 Touch ID 沒過也放行 ⇒ H5 紅
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
    """照艦長室 captain_room.pin_hash 原樣抄:scheme 是 pbkdf2_sha256(底線)、iters / salt / hex 用 $ 隔開。"""
    return "pbkdf2_sha256$%d$%s$%s" % (iters, salt, hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), iters).hex())


def CH_until(env):
    try:
        return float(json.loads(CH._fails_path(env).read_text()).get("until") or 0)
    except (OSError, ValueError):
        return 0


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
        self.env = dict(b.env(), AIB_TOUCHID="0")            # PIN 那條路;Touch ID 那條在 H5 用假的換掉(不准真的彈視窗)

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
        self.assertEqual(CH.check_pin("1234", self.env, now=t), "pin_too_short", "至少 6 位")
        for _ in range(5):
            self.assertEqual(CH.check_pin("000000", self.env, now=t), "bad_pin")
        self.assertEqual(CH.check_pin("123456", self.env, now=t + 1), "pin_locked", "連錯 5 次鎖 30 秒,對的也不收")
        locks, at = [], t
        for _ in range(9):                                     # 每次等鎖過了再錯一次:鎖翻倍,上限 1 小時
            at = CH_until(self.env) + 0.5
            self.assertEqual(CH.check_pin("000000", self.env, now=at), "bad_pin")
            locks.append(round(CH_until(self.env) - at))
        self.assertEqual(locks, [60, 120, 240, 480, 960, 1920, 3600, 3600, 3600], "遞增鎖、上限 1 小時")
        at = CH_until(self.env) + 0.5
        self.assertIsNone(CH.check_pin("123456", self.env, now=at), "輸對才歸零")
        self.assertEqual(CH.check_pin("000000", self.env, now=at), "bad_pin")
        self.assertEqual(CH_until(self.env), 0, "歸零後要再錯滿 5 次才鎖")
        t = at
        self.assertEqual(CH.check_pin(None, self.env, now=t + 40), "pin_too_short")
        # 用戶機上真實的存法:艦長室 200000 次、隨機 salt、scheme 底線(10-18 寫成連字號,真 PIN 全被擋)
        CH._fails_path(self.env).unlink(missing_ok=True)
        self.set_pin(pin_hash("246810", salt="ab" * 16, iters=200000))
        self.assertIsNone(CH.check_pin("246810", self.env, now=t + 50), "艦長室真實格式要過")
        self.set_pin(pin_hash("246810").replace("pbkdf2_sha256", "pbkdf2-sha256"))
        self.assertEqual(CH.check_pin("246810", self.env, now=t + 50), "pin_not_hashed", "scheme 名稱要一字不差")
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
        self.assertEqual((rc, out["why"]), (2, "no_public_seat"), "先看有沒有席可代簽")
        b.cli("publish", self.cap)
        rc, out = self.send("123456", "你好", TO, "--as-human", "--cabin", "6ddf")
        self.assertEqual((rc, out["why"], out["via"]), (2, "no_pin", "pin"))
        self.set_pin(pin_hash("123456"))
        for pin, text, to, why in (("999999", "你好", TO, "bad_pin"), ("1234", "你好", TO, "pin_too_short"), ("123456", "  ", TO, "empty"), ("123456", "你好", "nobody", "bad_address")):
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

    def test_h5_touchid(self):
        self.b.cli("publish", self.cap)
        GF.write_heartbeat(self.b.root)
        self.b.cli("friend", "add", TO, "--name", "Mac mini M4")
        asked = []

        def fake(ok, why="ok"):
            def _ask(reason):
                asked.append(reason)
                return {"ok": ok, "why": why}
            return _ask
        env = dict(self.env, AIB_DM_GLOBAL_WAIT="0")
        res = CH.send(TO, "取消的那封", "", "6ddf", env, touchid=fake(False, "cancel"), touchid_available=lambda: True)
        self.assertEqual((res["via"], res["why"], res["rc"]), ("touchid", "touchid_cancel", 2))
        self.assertEqual(self.outbox(), [], "Touch ID 沒過 ⇒ 一個字都不寫")
        self.assertIn("Mac mini M4", asked[-1])
        self.assertIn("取消的那封", asked[-1], "視窗文字由核心組:收件人 + 內文")
        long = "很長的內文" * 20 + "\u202e藏起來"
        res = CH.send(TO, long, "", "6ddf", env, touchid=fake(True), touchid_available=lambda: True)
        self.assertEqual((res["via"], res["why"], res["seat"]), ("touchid", None, self.cap), "PIN 空的也照過:Mac 不看 PIN")
        self.assertNotIn("\u202e", asked[-1], "方向控制字元要拿掉")
        self.assertLess(len(asked[-1]), 120, "內文截短")
        lock = CH._state_root(env) / "locks" / "touchid-approve.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        import fcntl
        with open(lock, "a") as other:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)        # 別席正在等指紋
            res = CH.send(TO, "疊視窗", "", "6ddf", env, touchid=fake(True), touchid_available=lambda: True)
        self.assertEqual(res["why"], "touchid_busy", "整台一次一窗")
        import touchid_approve as TA
        self.assertFalse(TA.available(environ={"AIB_TOUCHID": "0"}), "AIB_TOUCHID=0 只會關掉,退回 PIN")

    def test_h6_concurrent_guesses(self):
        self.set_pin(pin_hash("123456"))
        code = ("import sys,json;sys.path.insert(0,%r);import chat_human as CH;"
                "print(CH.check_pin('000000', json.loads(sys.argv[1]), now=1000.0))") % str(REPO / "src" / "cockpit")
        ps = [subprocess.Popen([sys.executable, "-c", code, json.dumps(self.env)], stdout=subprocess.PIPE, text=True) for _ in range(8)]
        outs = [p.communicate(timeout=60)[0].strip() for p in ps]
        self.assertEqual((outs.count("bad_pin"), outs.count("pin_locked")), (5, 3),
                         "8 個同時猜 ⇒ 剛好 5 次算錯、其餘被鎖(沒上鎖會各自讀到舊次數,多猜幾次)")
        st = json.loads(CH._fails_path(self.env).read_text())
        self.assertEqual(st["n"], 5)
        self.assertGreater(st["until"], 1000.0, "滿 5 次就鎖")

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
    def test_k2_no_escalation(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_human.py",
                        "    lock = min(PIN_LOCK_MAX_S, PIN_LOCK_S * 2 ** (n - PIN_MAX_FAILS)) if n >= PIN_MAX_FAILS else 0\n",
                        "    lock = PIN_LOCK_S if n >= PIN_MAX_FAILS else 0\n", tests=(name, "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Human.test_h1_pin"], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k3_touchid_ignored(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_human.py",
                        '        return "touchid", None if res.get("ok") is True else "touchid_" + str(res.get("why") or "failed").split(":")[0]\n',
                        '        return "touchid", None\n', tests=(name, "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Human.test_h5_touchid"], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_no_pin_check(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_human.py", '    return "pin", check_pin(pin, env)\n', '    return "pin", None\n',
                        tests=(name, "test-chat-send-d07f9-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Human.test_h3_cli"], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
