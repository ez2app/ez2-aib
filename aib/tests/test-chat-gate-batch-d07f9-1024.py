#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""分享一次改多項 + 指紋提示(10-24 跨客戶驗收:兩台對話那輪按了 6 次指紋;Andy 選「一次改多項」「指紋視窗加提示」)。

B1 share <好友> status --space island:只問一次指紋,視窗那句同時列在線與空間;兩項都改
B2 拒 ⇒ 兩項都沒改
B3 share --default online --space none:一次指紋,兩個預設都改
B4 門之前就擋(不彈):--space 沒對象、--default 配 --space room、好友和 --default 同時給
B5 提醒:真機路徑(沒換 prove)跳指紋前先叫提醒、帶同一句;_gate_notify 印到 stderr(stdout 的 --json 不變)
B6 四語字典:新鍵四種語言都有
C1 connect(10-24 Andy「我要一次就完成，不要多次」):對方已申請 + --open ⇒ 一次指紋就同意 + 好友 + 在線 status + 空間 room
C2 connect 沒給分享 ⇒ 不彈指紋,好友用預設(online / island);沒公開席 ⇒ 申請送不出、rc 照實回 2,好友照加
C3 connect 指紋被拒 ⇒ 什麼都沒做(好友也不加、申請還在)
C4 connect --open 配 --space ⇒ 門之前擋
B7 真機路徑(HUMAN_GATE 都沒換):先叫 _gate_notify 再叫 seat_act.prove,同一句(#320 第 1 輪審 中-3)
B8 share 寫一半失敗(set_space 丟 OSError)⇒ 不 traceback、rc 2、照實列已改的
C5 已是好友、空間已設 room,connect --share status ⇒ 視窗列 room(實際的,不是預設)、不寫「加好友」、不再送申請(中-1、L1)
C6 新好友 + --json、沒公開席 ⇒ stdout 只有一個 JSON:ok false、why request_failed、request.why no_seat(中-2)
C7 封鎖中 / 壞位址 ⇒ 門前就擋、不彈指紋;--json 回代碼(L2)
C8 connect 寫一半失敗 ⇒ why share_failed、applied 照實(L3)
C9 申請送出成功 ⇒ ok true、request {rc 0, why held}(L6)
刀(複本):K1 --space 不寫進對象 ⇒ B1 紅;K2 不叫提醒 ⇒ B5 紅;K3 connect 不過門 ⇒ C3 紅;K4 真機不提醒 ⇒ B7 紅;
   K5 視窗用預設等級 ⇒ C5 紅
"""
import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(HERE))
import chat_roster as CR  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_open_gate_b", str(HERE / "test-chat-open-touchid-d07f9-1021.py"))
OG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(OG)
FRIEND = OG.FRIEND


class Batch(OG.Base):
    def test_b1_one_prompt_two_levels(self):
        rc, out = self.run_cb("share", FRIEND, "status", "--space", "island")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.asked), 1, "只問一次")
        self.assertIn("在線", self.asked[0])
        self.assertIn("空間", self.asked[0])
        self.assertEqual((CR.share_level(FRIEND, self.env), CR.space_level(FRIEND, self.env)), ("status", "island"))
        rc, out = self.run_cb("share", FRIEND, "off", "--space", "none")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.asked), 2)
        self.assertEqual((CR.share_level(FRIEND, self.env), CR.space_level(FRIEND, self.env)), ("off", "none"))

    def test_b2_denied(self):
        self.gate(ok=False, why="timeout")
        rc, out = self.run_cb("share", FRIEND, "status", "--space", "room")
        self.assertEqual(rc, 3, out)
        self.assertEqual((CR.share_level(FRIEND, self.env), CR.space_level(FRIEND, self.env)), ("online", "island"))

    def test_b3_defaults(self):
        rc, out = self.run_cb("share", "--default", "status", "--space", "none")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.asked), 1)
        self.assertEqual((CR.read_share(self.env)["default"], CR.read_space(self.env)["default"]), ("status", "none"))

    def test_b4_rejected_before_gate(self):
        for argv in (("share", "--space", "island"), ("share", "--default", "online", "--space", "room"),
                     ("share", FRIEND, "status", "--default", "online", "--space", "island")):
            rc, _out = self.run_cb(*argv)
            self.assertEqual(rc, 2, argv)
        self.assertEqual(self.asked, [], "門之前就擋,不彈指紋")

    def test_b5_notify(self):
        seen = []
        self.CB.HUMAN_GATE["notify"] = lambda reason, env: seen.append(("notify", reason))
        orig = self.CB.HUMAN_GATE["prove"]
        self.CB.HUMAN_GATE["prove"] = lambda reason, env: (seen.append(("prove", reason)), orig(reason, env))[1]
        self.addCleanup(self.CB.HUMAN_GATE.update, {"notify": None})
        rc, out = self.run_cb("share", FRIEND, "status", "--json")
        self.assertEqual(rc, 0, out)
        self.assertEqual([k for k, _r in seen], ["notify", "prove"], "先提醒再跳指紋")
        self.assertEqual(seen[0][1], seen[1][1], "提醒那句 = 視窗那句")
        json.loads(out.strip())                                  # stdout 仍是乾淨 JSON
        self.CB.GATE_SOUND = str(self.td / "no-such.aiff")      # 測試不響
        err = io.StringIO()
        with redirect_stderr(err):
            self.CB._gate_notify("aib:測試那句", self.env)
        self.assertIn("aib:測試那句", err.getvalue())
        self.assertIn("Touch ID", err.getvalue())

    def test_b7_real_path_notifies_first(self):
        seen = []
        self.CB.HUMAN_GATE["prove"] = None                       # 真機路徑:門自己挑 _gate_notify + seat_act.prove
        self.CB._gate_notify = lambda reason, env: seen.append(("notify", reason))
        sa = sys.modules.get("seat_act") or __import__("seat_act")
        with mock.patch.object(sa, "prove", lambda reason, env: (seen.append(("prove", reason)), ("touchid", None))[1]):
            rc, out = self.run_cb("share", FRIEND, "status")
        self.assertEqual(rc, 0, out)
        self.assertEqual([k for k, _r in seen], ["notify", "prove"], "真機要先提醒再跳指紋")
        self.assertEqual(seen[0][1], seen[1][1])

    def test_b8_partial_write(self):
        with mock.patch.object(CR, "set_space", side_effect=OSError("disk full")):
            rc, out = self.run_cb("share", FRIEND, "status", "--space", "room")
            self.assertEqual(rc, 2, out)
            self.assertIn("沒存成", out)
            self.assertIn("已改的：在線", out)
            rc, out = self.run_cb("share", FRIEND, "online", "--space", "room", "--json")
        self.assertEqual(json.loads(out.strip()), {"v": 1, "ok": False, "why": "write_failed", "applied": ["share"]})

    def test_b6_i18n(self):
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            for k in ("gate_waiting", "touchid_share_space", "touchid_share_space_default", "share_space_need_target",
                      "share_space_one_target", "touchid_connect", "connect_open_or_space", "connect_share_failed",
                      "connect_done", "connect_already", "connect_now", "share_write_failed", "share_applied",
                      "share_item_share", "share_item_space"):
                self.assertTrue(d.get("aib.chat." + k), (lang, k))
            self.assertIn("{reason}", d["aib.chat.gate_waiting"], lang)


class Connect(OG.Base):
    OTHER = OG.NK.npub(OG.NK.pubkey_hex("%064x" % 0xb202))

    def setUp(self):
        super().setUp()
        OG.GF.add_request(self.root, {"owner": self.OTHER, "msgid": OG.GF.new_msgid(), "display_name": "新朋友", "note": "嗨"},
                          every=0)

    def test_c1_accept_and_open_one_prompt(self):
        rc, out = self.run_cb("connect", self.OTHER, "--open", "--json")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.asked), 1, "只問一次")
        self.assertIn("加好友", self.asked[0])
        self.assertIn(self.CB.T("share_status"), self.asked[0])
        self.assertIn(self.CB.T("space_room"), self.asked[0])
        got = json.loads(out.strip())                           # stdout 只有一個 JSON(cmd_request 的人話不准混進來)
        self.assertEqual((got["ok"], got["accepted"], got["share"], got["space"]), (True, True, "status", "room"))
        self.assertEqual(got["request"], {"rc": 0, "why": "accepted"})
        self.assertTrue(CR.is_friend(self.OTHER, self.env))
        self.assertEqual(OG.GF.read_requests(self.root), [], "申請已處理")

    def test_c2_defaults_no_prompt(self):
        new = OG.NK.npub(OG.NK.pubkey_hex("%064x" % 0xc303))
        rc, out = self.run_cb("connect", new)
        self.assertEqual(self.asked, [], "預設分享不彈指紋")
        self.assertEqual(rc, 2, out)                            # 沒公開席 ⇒ 申請送不出,照實回
        self.assertTrue(CR.is_friend(new, self.env))
        self.assertEqual((CR.share_level(new, self.env), CR.space_level(new, self.env)), ("online", "island"))

    def test_c3_denied_does_nothing(self):
        self.gate(ok=False, why="cancel")
        rc, out = self.run_cb("connect", self.OTHER, "--share", "status", "--space", "island")
        self.assertEqual(rc, 3, out)
        self.assertFalse(CR.is_friend(self.OTHER, self.env), "被拒 ⇒ 好友也不加")
        self.assertEqual(len(OG.GF.read_requests(self.root)), 1, "申請還在")

    def test_c4_open_with_space(self):
        rc, _out = self.run_cb("connect", self.OTHER, "--open", "--space", "island")
        self.assertEqual((rc, self.asked), (2, []))

    def test_c5_existing_friend_shows_actual(self):
        CR.set_space("room", FRIEND, self.env)
        sent = []
        with mock.patch.object(self.CB, "cmd_request", lambda *x, **k: sent.append(1) or 0):
            rc, out = self.run_cb("connect", FRIEND, "--share", "status", "--json")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.asked), 1)
        self.assertIn(self.CB.T("space_room"), self.asked[0], "視窗要列這位好友實際的空間等級")
        self.assertNotIn(self.CB.T("space_island"), self.asked[0])
        self.assertNotIn("加好友", self.asked[0], "已是好友 ⇒ 不寫加好友")
        self.assertEqual(sent, [], "已是好友 ⇒ 不再送申請")
        got = json.loads(out.strip())
        self.assertEqual((got["ok"], got["already_friend"], got["share"], got["space"], got["request"]["why"]),
                         (True, True, "status", "room", "already_friend"))

    def test_c6_json_request_failed(self):
        new = OG.NK.npub(OG.NK.pubkey_hex("%064x" % 0xc606))
        rc, out = self.run_cb("connect", new, "--json")
        self.assertEqual(rc, 2, out)
        got = json.loads(out.strip())                           # 整段就是一個 JSON
        self.assertEqual((got["ok"], got["why"], got["request"]), (False, "request_failed", {"rc": 2, "why": "no_seat"}))

    def test_c7_rejected_before_gate(self):
        bad = OG.NK.npub(OG.NK.pubkey_hex("%064x" % 0xc707))
        CR.add_friend(bad, None, self.env, kind="blocks")
        rc, out = self.run_cb("connect", bad, "--share", "status", "--json")
        self.assertEqual((rc, json.loads(out.strip())["why"]), (2, "blocked"))
        rc, out = self.run_cb("connect", "npub1nope", "--open", "--json")
        self.assertEqual((rc, json.loads(out.strip())["why"]), (2, "bad_friend"))
        self.assertEqual(self.asked, [], "門前就擋,不白按指紋")

    def test_c8_partial_write(self):
        with mock.patch.object(CR, "set_space", side_effect=OSError("disk full")):
            rc, out = self.run_cb("connect", self.OTHER, "--share", "status", "--space", "room", "--json")
        self.assertEqual(rc, 2, out)
        got = json.loads(out.strip())
        self.assertEqual((got["why"], got["applied"], got["share"]), ("share_failed", ["share"], "status"))

    def test_c9_request_sent(self):
        new = OG.NK.npub(OG.NK.pubkey_hex("%064x" % 0xc909))
        with mock.patch.object(self.CB, "_my_published", lambda env: [{"cabin": "c1", "seat": "aaaa1"}]), \
                mock.patch.object(OG.GF, "send_and_wait", lambda *x, **k: {"stage": "held", "rc": 0, "detail": None}):
            rc, out = self.run_cb("connect", new, "--json")
        self.assertEqual(rc, 0, out)
        got = json.loads(out.strip())
        self.assertEqual((got["ok"], got["accepted"], got["already_friend"], got["request"]),
                         (True, False, False, {"rc": 0, "why": "held"}))


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_bridge.py", needle, replacement,
                        tests=(name, "test-chat-open-touchid-d07f9-1021.py")) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=180)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k1_space_not_written(self):
        self._knife("                CR.set_space(a.space, a.who, env)\n", "                pass\n",
                    "Batch.test_b1_one_prompt_two_levels")

    def test_k3_connect_ungated(self):
        self._knife("    if share or space:                       # 改分享 = 本人的動作:全部擠進一個視窗,列改完後的實際等級\n",
                    "    if False:\n", "Connect.test_c3_denied_does_nothing")

    def test_k2_no_notify(self):
        self._knife("        notify(reason, env)\n", "        pass\n", "Batch.test_b5_notify")

    def test_k4_real_path_silent(self):
        self._knife('    notify = HUMAN_GATE["notify"] or (_gate_notify if HUMAN_GATE["prove"] is None else None)\n',
                    '    notify = HUMAN_GATE["notify"]\n', "Batch.test_b7_real_path_notifies_first")

    def test_k5_window_uses_default(self):
        self._knife('        cur_sp = CR.space_level(owner, env) if known else ',
                    '        cur_sp = CR.read_space(env)["default"] if known else ', "Connect.test_c5_existing_friend_shows_actual")


if __name__ == "__main__":
    unittest.main()
