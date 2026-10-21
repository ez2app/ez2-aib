#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""開放艦長室 / 改分享等級要本人 Touch ID(10-21 @abb89 審 #209 裁:open 與 visit accept 接 seat_act.prove,
既有 aib chat space <npub> room 與 share 升級成同一道門,不然 open 有門、space 沒門等於沒門;visit(送請求)與 reject 不用)。

門 = chat_bridge.human_gate:呼叫者是 aib 席位 ⇒ 拒(rc 2);Touch ID 關掉 / 不能用 / 取消 / 逾時 / 忙線 ⇒ 拒(rc 3),不退 PIN。
只擋「寫」;讀(share --json、space --json、space check、visits)不擋。
誠實講:share.json 是本機檔,同帳號 AI 直接改檔一樣過;這道門擋的是「AI 用 CLI 自己開」與「頁面被 AI 代按」,不是終極防線。

G1 open / visit accept:指紋過 ⇒ 做(space 變 room、送 open);取消 ⇒ rc 3、什麼都沒改、沒待送檔;--json ⇒ {"ok": false, "why"}
G2 席位呼叫(TMUX / 對話 id 環境變數)⇒ rc 2 不彈指紋、不改
G3 space <npub> room / space --default island / share <npub> status / share --default:指紋過 ⇒ 改;拒 ⇒ 不改;
   --default room 在門之前就 rc 2(不彈);缺等級 rc 2(不彈)
G4 讀不過門:share --json、space --json、space check、visits --json 都不叫 prove
G5 visit(送請求)/ reject 不過門
G6 視窗那句:帶好友備註名、經 TA.clean(控制字元拿掉、截長);改預設那句帶等級
G7 四語字典:gate_* / touchid_* 鍵四種語言都有
刀(複本):K1 cmd_visit 不過門 ⇒ G1 紅;K2 cmd_space 不過門 ⇒ G3 紅
"""
import importlib.util
import io
import json
import sys
import tempfile
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

FRIEND = NK.npub(NK.pubkey_hex("%064x" % 0xa101))


def _load_cb():
    spec = importlib.util.spec_from_file_location("chat_bridge_gate", str(COCKPIT / "chat_bridge.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-gate-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name)
        self.root = self.td / "bridge"
        self.env = {"HOME": str(self.td), "EZ2_AIB_STATE_ROOT": str(self.td / "state"), "AIB_CHAT_BRIDGE_DIR": str(self.root),
                    "AIB_CHAT_HOME": str(self.td / "chat"), "EZ2_AIB_LOCALE": "zh-TW", "PATH": "/usr/bin:/bin"}
        GF.ensure_dirs(self.root)
        GF.write_heartbeat(self.root)
        CR.add_friend(FRIEND, "Pro 13 \u202e藏", self.env)
        self.CB = _load_cb()
        self.asked, self.seat_calls = [], []
        self.gate(ok=True)

    def gate(self, ok=True, why="cancel", seat=False):
        def prove(reason, env):
            self.asked.append(reason)
            return "touchid", (None if ok else "touchid_" + why)

        def is_seat(env):
            self.seat_calls.append(1)
            return seat
        self.CB.HUMAN_GATE["prove"] = prove
        self.CB.HUMAN_GATE["caller_is_seat"] = is_seat

    def run_cb(self, *argv):
        buf = io.StringIO()
        rc = self.CB.main(list(argv), env=self.env, out=buf)
        return rc, buf.getvalue()

    def q(self):
        return [rec["msg"]["op"] for _p, rec in CV.queued(self.root)]


class Gate(Base):
    def test_g1_open_accept(self):
        self.gate(ok=False)
        rc, out = self.run_cb("open", FRIEND)
        self.assertEqual(rc, 3, out)
        self.assertIn("沒按指紋", out)
        self.assertEqual((CR.space_level(FRIEND, self.env), self.q()), ("island", []), "拒 ⇒ 什麼都沒改")
        rc, out = self.run_cb("open", FRIEND, "--json")
        self.assertEqual(rc, 3)
        self.assertEqual(json.loads(out.strip())["why"], "touchid_cancel")
        self.gate(ok=True)
        rc, out = self.run_cb("open", FRIEND, "--json")
        self.assertEqual(rc, 0, out)
        self.assertEqual((CR.space_level(FRIEND, self.env), CR.share_level(FRIEND, self.env), self.q()), ("room", "status", ["open"]))
        self.assertEqual(len(self.asked), 3, "每次都問")
        CV.on_incoming(self.env, FRIEND, {"op": "request", "nonce": "a" * 16})
        self.gate(ok=False, why="timeout")
        rc, out = self.run_cb("visit", "accept", FRIEND)
        self.assertEqual(rc, 3)
        self.assertEqual(CV.read(self.env)["in"][FRIEND]["status"], "pending")
        self.gate(ok=True)
        rc, out = self.run_cb("visit", "accept", FRIEND, "--json")
        self.assertEqual(rc, 0, out)
        self.assertEqual(CV.read(self.env)["in"][FRIEND]["status"], "accepted")

    def test_g2_seat_caller(self):
        self.gate(ok=True, seat=True)
        rc, out = self.run_cb("open", FRIEND)
        self.assertEqual(rc, 2, out)
        self.assertIn("AI 席位", out)
        self.assertEqual(self.asked, [], "席位呼叫:連指紋都不彈")
        self.assertEqual(CR.space_level(FRIEND, self.env), "island")
        rc, out = self.run_cb("space", FRIEND, "room", "--json")
        self.assertEqual((rc, json.loads(out.strip())["why"]), (2, "seat_caller"))
        rc, out = self.run_cb("share", "--default", "status")
        self.assertEqual(rc, 2)
        self.assertEqual(CR.read_share(self.env)["default"], "online")

    def test_g3_space_share_gated(self):
        with self.assertRaises(SystemExit):                 # argparse 的 choices 就擋了(子行程 rc 2),門之前、不彈
            self.run_cb("space", "--default", "room")
        self.assertEqual(self.asked, [])
        rc, out = self.run_cb("space", FRIEND)
        self.assertEqual((rc, self.asked), (2, []), "缺等級不彈")
        rc, out = self.run_cb("share", FRIEND)
        self.assertEqual((rc, self.asked), (2, []))
        self.gate(ok=False, why="busy")
        rc, out = self.run_cb("space", FRIEND, "room")
        self.assertEqual((rc, CR.space_level(FRIEND, self.env)), (3, "island"))
        rc, out = self.run_cb("share", FRIEND, "status")
        self.assertEqual((rc, CR.share_level(FRIEND, self.env)), (3, "online"))
        rc, out = self.run_cb("space", "--default", "none")
        self.assertEqual((rc, CR.read_space(self.env)["default"]), (3, "island"))
        self.gate(ok=True)
        self.assertEqual(self.run_cb("space", FRIEND, "room")[0], 0)
        self.assertEqual(self.run_cb("share", FRIEND, "status")[0], 0)
        self.assertEqual(self.run_cb("space", "--default", "none")[0], 0)
        self.assertEqual(self.run_cb("share", "--default", "off")[0], 0)
        self.assertEqual((CR.space_level(FRIEND, self.env), CR.share_level(FRIEND, self.env),
                          CR.read_space(self.env)["default"], CR.read_share(self.env)["default"]), ("room", "status", "none", "off"))
        self.gate(ok=False, why="off")
        rc, out = self.run_cb("share", FRIEND, "off")
        self.assertEqual(rc, 3)
        self.assertIn("AIB_TOUCHID=0", out)

    def test_g4_reads_not_gated(self):
        self.gate(ok=False)
        for argv in (("share", "--json"), ("space", "--json"), ("visits", "--json"), ("share",), ("space",)):
            rc, out = self.run_cb(*argv)
            self.assertEqual(rc, 0, (argv, out))
        rc, out = self.run_cb("space", "check", "--json")
        self.assertIn(rc, (0, 1))
        self.assertEqual((self.asked, self.seat_calls), ([], []), "讀不過門")

    def test_g5_request_reject_not_gated(self):
        self.gate(ok=False)
        rc, out = self.run_cb("visit", FRIEND)
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cb("visit", "reject", FRIEND)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.asked, [])
        self.assertEqual(sorted(self.q()), ["reject", "request"])

    def test_g6_reason_text(self):
        self.run_cb("open", FRIEND)
        self.assertIn("Pro 13", self.asked[-1])
        self.assertNotIn("\u202e", self.asked[-1], "方向控制字元要拿掉")
        self.assertIn("艦長室", self.asked[-1])
        self.run_cb("space", FRIEND, "room")
        self.assertIn("room", self.asked[-1])
        self.run_cb("share", "--default", "off")
        self.assertIn("全部好友", self.asked[-1])
        self.assertIn("off", self.asked[-1])
        self.assertTrue(all(len(x) < 160 for x in self.asked))

    def test_g7_i18n(self):
        want = None
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            keys = {k for k in d if k.startswith("aib.chat.gate_") or k.startswith("aib.chat.touchid_")}
            self.assertGreaterEqual(len(keys), 9, lang)
            want = want or keys
            self.assertEqual(keys, want, lang)


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_bridge.py", needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_open_no_gate(self):
        self._knife('    if action in ("open", "accept"):         # 開放艦長室 = 本人的動作', '    if False:', "Gate.test_g1_open_accept")

    def test_k2_space_no_gate(self):
        self._knife('    if a.default or a.who:                   # 開空間 = 本人的動作', '    if False:', "Gate.test_g3_space_share_gated")


if __name__ == "__main__":
    unittest.main()
