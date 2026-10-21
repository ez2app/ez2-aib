#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""開放艦長室給好友 + 參觀請求(第二波 #1,10-21;NOTE-member-ux A1 / A2)。

會員痛點:互開艦長室要兩台各打 share + space 兩次 CLI 還要貼 npub。合成:
`aib chat open <好友>`(share status + space room + 送 open 通知)、`aib chat visit <好友>`(送參觀請求)、
`aib chat visit accept|reject <好友>`、`aib chat visits --json`(頁面:收到 / 送出 / 我開給誰 / 誰開給我)。
訊息 aib-type=visit,主人層級私訊,body {"op": request|open|reject, "nonce", "at"};visits.json 記帳;visit-out 待送。
這不是防線:信任等級跟現有 `aib chat space <npub> room` 一樣(本機 CLI 就能開);本人證明列下一版給 @abb89 裁。

V1 request:好友 ⇒ out=sent + visit-out 一檔 op=request;陌生 / 封鎖 ⇒ not_friend、不寫檔
V2 open_to:share → status、space → room;對方有 pending ⇒ 標 accepted、送 open 用同一 nonce、answered=True;沒 pending ⇒ 新 nonce
V3 on_incoming:好友 request ⇒ pending;一小時內再來 ⇒ quiet;open ⇒ open_from + out sent→opened;reject ⇒ out→rejected;
   陌生 ⇒ None 不記;parse 壞訊息 ⇒ None;queued 壞檔刪
V4 payload:incoming / outgoing / open_to(每位好友 share+space+room)/ open_from / pending 數;名字來自好友名單
V5 CLI:open --json、visit、visit accept、visits --json;轉接程式沒開 ⇒ rc 2;不是好友 ⇒ rc 2
V6 四語字典:visit / open 的鍵四種語言都有、數量一致
刀(複本):K1 open_to 不設 space ⇒ V2 紅;K2 on_incoming 不驗好友 ⇒ V3 紅
"""
import importlib.util
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(HERE))
import chat_roster as CR  # noqa: E402
import chat_visit as CV  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

FRIEND = NK.npub(NK.pubkey_hex("%064x" % 0x7101))
MINE = NK.npub(NK.pubkey_hex("%064x" % 0x7102))
STRANGER = NK.npub(NK.pubkey_hex("%064x" % 0x7103))
BLOCKED = NK.npub(NK.pubkey_hex("%064x" % 0x7104))


def _load_cb():
    spec = importlib.util.spec_from_file_location("chat_bridge_visit", str(COCKPIT / "chat_bridge.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-visit-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name)
        self.root = self.td / "bridge"
        self.env = {"HOME": str(self.td), "EZ2_AIB_STATE_ROOT": str(self.td / "state"), "AIB_CHAT_BRIDGE_DIR": str(self.root),
                    "AIB_CHAT_HOME": str(self.td / "chat"), "EZ2_AIB_LOCALE": "zh-TW", "PATH": "/usr/bin:/bin"}
        GF.ensure_dirs(self.root)
        CR.add_friend(FRIEND, "Pro 13", self.env)
        CR.add_friend(MINE, "Mini", self.env, kind="mine")
        CR.add_friend(BLOCKED, None, self.env, kind="blocks")

    def q(self):
        return [(p.name, rec["msg"]) for p, rec in CV.queued(self.root)]


class Core(Base):
    def test_v1_request(self):
        r = CV.request(self.env, self.root, FRIEND, now=1000)
        self.assertEqual((r["owner"], r["status"], r["at"]), (FRIEND, "sent", 1000))
        self.assertTrue(CV.NONCE_RE.match(r["nonce"]))
        self.assertEqual(self.q(), [(r["nonce"] + ".json", {"op": "request", "nonce": r["nonce"], "at": 1000})])
        self.assertEqual(oct(CV.path(self.env).stat().st_mode & 0o777), "0o600")
        for who in (STRANGER, BLOCKED, "junk", ""):
            with self.assertRaises(CV.VisitError, msg=who) as cm:
                CV.request(self.env, self.root, who)
            self.assertEqual(str(cm.exception), "not_friend")
        self.assertEqual(len(self.q()), 1, "陌生 / 封鎖不寫待送檔")
        CV.request(self.env, self.root, FRIEND + "/5a97/4345c", now=1001)      # 全球位址也認
        self.assertEqual(CV.read(self.env)["out"][FRIEND]["at"], 1001)

    def test_v2_open_to(self):
        self.assertEqual((CR.share_level(FRIEND, self.env), CR.space_level(FRIEND, self.env)), ("online", "island"))
        got = CV.on_incoming(self.env, FRIEND, {"op": "request", "nonce": "a" * 16, "at": 900}, now=900)
        self.assertEqual(got, "pending")
        r = CV.open_to(self.env, self.root, FRIEND, now=1000)
        self.assertEqual(r, {"owner": FRIEND, "share": "status", "space": "room", "answered": True})
        self.assertEqual((CR.share_level(FRIEND, self.env), CR.space_level(FRIEND, self.env)), ("status", "room"))
        rec = CV.read(self.env)
        self.assertEqual((rec["in"][FRIEND]["status"], rec["in"][FRIEND]["answered_at"]), ("accepted", 1000))
        self.assertEqual(self.q(), [("a" * 16 + ".json", {"op": "open", "nonce": "a" * 16, "at": 1000})], "同一 nonce 回 open")
        r2 = CV.open_to(self.env, self.root, MINE, now=1001)
        self.assertEqual((r2["answered"], r2["space"]), (False, "room"))
        names = [n for n, _m in self.q()]
        self.assertEqual(len(names), 2)
        self.assertNotIn(MINE[:4], names)
        with self.assertRaises(CV.VisitError):
            CV.open_to(self.env, self.root, STRANGER)
        self.assertEqual(CR.space_level(STRANGER, self.env), "none")
        rej = CV.reject(self.env, self.root, FRIEND, now=1002)
        self.assertEqual(rej["status"], "rejected")
        self.assertEqual([m["op"] for _n, m in self.q()].count("reject"), 1)

    def test_v3_on_incoming(self):
        self.assertEqual(CV.on_incoming(self.env, FRIEND, {"op": "request", "nonce": "b" * 16}, now=100), "pending")
        self.assertEqual(CV.on_incoming(self.env, FRIEND, {"op": "request", "nonce": "c" * 16}, now=200), "quiet")
        self.assertEqual(CV.read(self.env)["in"][FRIEND]["nonce"], "b" * 16, "限量:一小時內第二則不蓋")
        self.assertEqual(CV.on_incoming(self.env, FRIEND, {"op": "request", "nonce": "c" * 16}, now=100 + 3600), "pending")
        self.assertEqual(CV.read(self.env)["in"][FRIEND]["nonce"], "c" * 16)
        CV.request(self.env, self.root, MINE, now=300)
        self.assertEqual(CV.on_incoming(self.env, MINE, {"op": "open", "nonce": "d" * 16}, now=400), "opened")
        rec = CV.read(self.env)
        self.assertEqual((rec["out"][MINE]["status"], rec["open_from"][MINE]), ("opened", 400))
        CV.request(self.env, self.root, FRIEND, now=500)
        self.assertEqual(CV.on_incoming(self.env, FRIEND, {"op": "reject", "nonce": "e" * 16}, now=600), "rejected")
        self.assertEqual(CV.read(self.env)["out"][FRIEND]["status"], "rejected")
        for who in (STRANGER, BLOCKED, None, "x"):
            self.assertIsNone(CV.on_incoming(self.env, who, {"op": "request", "nonce": "f" * 16}), repr(who))
        self.assertNotIn(STRANGER, CV.read(self.env)["in"])
        self.assertIsNone(CV.on_incoming(self.env, FRIEND, {"op": "steal", "nonce": "f" * 16}))
        self.assertEqual(CV.parse(json.dumps({"op": "open", "nonce": "1" * 16, "at": "7"})), {"op": "open", "nonce": "1" * 16, "at": 7})
        for bad in ("{", "[]", json.dumps({"op": "open"}), json.dumps({"op": "x", "nonce": "1" * 16}), json.dumps({"op": "open", "nonce": "zz"})):
            self.assertIsNone(CV.parse(bad), bad)
        (self.root / CV.QUEUE).mkdir(exist_ok=True)
        (self.root / CV.QUEUE / "bad.json").write_text("{nope", encoding="utf-8")
        (self.root / CV.QUEUE / "bad2.json").write_text(json.dumps({"to": FRIEND, "msg": {"op": "steal"}}), encoding="utf-8")
        self.assertEqual([n for n, _m in self.q() if n.startswith("bad")], [])
        self.assertFalse((self.root / CV.QUEUE / "bad.json").exists())

    def test_v4_payload(self):
        CV.on_incoming(self.env, FRIEND, {"op": "request", "nonce": "a" * 16}, now=100)
        CV.request(self.env, self.root, MINE, now=200)
        CV.on_incoming(self.env, MINE, {"op": "open", "nonce": "b" * 16}, now=300)
        CV.open_to(self.env, self.root, MINE, now=400)
        pl = CV.payload(self.env, now=500)
        self.assertEqual((pl["v"], pl["at"], pl["pending"]), (1, 500, 1))
        self.assertEqual([(r["owner"], r["name"], r["status"]) for r in pl["incoming"]], [(FRIEND, "Pro 13", "pending")])
        self.assertEqual([(r["owner"], r["name"], r["status"]) for r in pl["outgoing"]], [(MINE, "Mini", "opened")])
        self.assertEqual(pl["open_to"][FRIEND], {"share": "online", "space": "island", "room": False})
        self.assertEqual(pl["open_to"][MINE], {"share": "status", "space": "room", "room": True})
        self.assertNotIn(STRANGER, pl["open_to"])
        self.assertNotIn(BLOCKED, pl["open_to"])
        self.assertEqual(pl["open_from"], {MINE: 300})


class Cli(Base):
    def run_cb(self, *argv):
        CB = _load_cb()
        # 10-21:open / accept / space / share 要本人 Touch ID(chat_bridge.human_gate);測試一律換成假指紋,絕不彈真視窗
        CB.HUMAN_GATE["prove"] = lambda reason, env: ("touchid", None)
        CB.HUMAN_GATE["caller_is_seat"] = lambda env: False
        buf = io.StringIO()
        rc = CB.main(list(argv), env=self.env, out=buf)
        return rc, buf.getvalue()

    def test_v5_cli(self):
        rc, out = self.run_cb("open", FRIEND, "--json")
        self.assertEqual(rc, 2, out)
        self.assertIn("沒開", out)
        GF.write_heartbeat(self.root)
        rc, out = self.run_cb("open", FRIEND, "--json")
        self.assertEqual(rc, 0, out)
        d = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((d["action"], d["result"]["share"], d["result"]["space"]), ("open", "status", "room"))
        rc, out = self.run_cb("visit", MINE)
        self.assertEqual(rc, 0, out)
        self.assertIn(MINE, out)
        rc, out = self.run_cb("visit", STRANGER)
        self.assertEqual(rc, 2, out)
        self.assertIn("好友", out)
        CV.on_incoming(self.env, MINE, {"op": "request", "nonce": "a" * 16}, now=int(time.time()))
        rc, out = self.run_cb("visit", "accept", MINE, "--json")
        self.assertEqual(rc, 0, out)
        self.assertTrue(json.loads(out.strip().splitlines()[-1])["result"]["answered"])
        rc, out = self.run_cb("visit", "reject", FRIEND)
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cb("visits", "--json")
        self.assertEqual(rc, 0, out)
        pl = json.loads(out.strip().splitlines()[-1])
        self.assertEqual({r["owner"]: r["status"] for r in pl["incoming"]}, {MINE: "accepted"})
        self.assertEqual({r["owner"]: r["status"] for r in pl["outgoing"]}, {MINE: "sent"})
        self.assertEqual(sorted(m["op"] for _n, m in self.q()), ["open", "open", "reject", "request"])   # 檔名照 nonce 排,不照時間
        rc, out = self.run_cb("visits")
        self.assertEqual(rc, 0, out)
        self.assertIn("Mini", out)
        rc, out = self.run_cb("visit", "accept")
        self.assertEqual(rc, 2)

    def test_v6_i18n_four_languages(self):
        want = None
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            keys = {k for k in d if k.startswith("aib.chat.visit") or k.startswith("aib.chat.open_") or k.startswith("aib.chat.help.visit")
                    or k == "aib.chat.help.open"}
            self.assertGreaterEqual(len(keys), 18, lang)
            if want is None:
                want = keys
            self.assertEqual(keys, want, lang)


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_open_without_space(self):
        self._knife("src/cockpit/chat_visit.py", '    CR.set_space("room", o, env)\n', "", "Core.test_v2_open_to")

    def test_k2_incoming_no_friend_check(self):
        self._knife("src/cockpit/chat_visit.py",
                    '    o = _friend(owner, env)\n    if not o or not isinstance(msg, dict) or msg.get("op") not in OPS:',
                    '    o = owner\n    if not o or not isinstance(msg, dict) or msg.get("op") not in OPS:',
                    "Core.test_v3_on_incoming")


if __name__ == "__main__":
    unittest.main()
