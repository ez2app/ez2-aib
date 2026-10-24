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
#320 第 2 輪審:
C10 移除好友後單人分享等級一起清:share F status --space room → friend remove → connect F(不帶分享)⇒ 不彈指紋、等級回預設(M1)
C11 申請沒送出 ⇒ 新加的好友撤掉;處理好(有公開席)重跑 ⇒ 真的送出(M2);非 JSON 不印「已連線」(M3)
C12 rc 4(已交轉接程式、未確認)⇒ ok true、why request_pending、文字版不說失敗(M3)
C13 --open 配 --share ⇒ 門前擋(M4);轉接程式沒開 + --open / 新好友 ⇒ bridge_off 門前擋(L1)
C14 --open 寫一半失敗 ⇒ applied 由前後等級差算(L2);寫一半後連讀都失敗 ⇒ unknown、仍是一個 JSON(L3)
C15 已是好友、對方又有申請 ⇒ 視窗不寫「加好友」(L4)
B4 改 --json 斷言代碼(M5);B9 AIB_TOUCHID=0 真機路徑不提醒(L6)
#320 第 3 輪審(@abb89 定修法):
C16 本人先設好的單人等級(比預設嚴的 off / none、或按過指紋的 status / room),connect 申請失敗撤好友後都還在
C17 移除好友只清比預設寬的:設 off 的移除再加回仍 off;設 status 的清掉
C18 撤好友時存檔丟 OSError ⇒ 仍是一個 JSON、request_failed、detail undo_failed;文字版說好友還在、先 friend remove
刀(複本):K1 --space 不寫進對象 ⇒ B1 紅;K2 不叫提醒 ⇒ B5 紅;K3 connect 不過門 ⇒ C3 紅;K4 真機不提醒 ⇒ B7 紅;
   K5 視窗用預設等級 ⇒ C5 紅;K6 移除好友不清等級 ⇒ C10 紅;K7 申請沒送出不撤好友 ⇒ C11 紅;
   K8 撤好友也清等級 ⇒ C16 紅;K9 移除好友全清 ⇒ C17 紅
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
        for argv, why in ((("share", "--space", "island"), "space_need_target"),
                          (("share", "--default", "online", "--space", "room"), "bad_space"),
                          (("share", FRIEND, "status", "--default", "online", "--space", "island"), "one_target"),
                          (("share", FRIEND), "share_need_level")):
            rc, _out = self.run_cb(*argv)
            self.assertEqual(rc, 2, argv)
            rc, out = self.run_cb(*(argv + ("--json",)))
            try:
                got = json.loads(out.strip())
            except ValueError:
                self.fail("--json 要回一個 JSON:%r" % out)
            self.assertEqual((rc, got), (2, {"v": 1, "ok": False, "why": why}), argv)
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
        self.CB._gate_pops = lambda env: True                    # CI 是 Linux:平台一律注入
        sa = sys.modules.get("seat_act") or __import__("seat_act")
        with mock.patch.object(sa, "prove", lambda reason, env: (seen.append(("prove", reason)), ("touchid", None))[1]):
            rc, out = self.run_cb("share", FRIEND, "status")
        self.assertEqual(rc, 0, out)
        self.assertEqual([k for k, _r in seen], ["notify", "prove"], "真機要先提醒再跳指紋")
        self.assertEqual(seen[0][1], seen[1][1])

    def test_b9_no_notify_when_touchid_off(self):
        self.assertFalse(self.CB._gate_pops(dict(self.env, AIB_TOUCHID="0")))
        seen = []
        self.CB.HUMAN_GATE["prove"] = None
        self.CB._gate_notify = lambda reason, env: seen.append(reason)
        rc, _out = self.run_cb("share", FRIEND, "status")
        self.env["AIB_TOUCHID"] = "0"
        rc, out = self.run_cb("share", FRIEND, "status", "--json")
        # 非 Mac 先回 touchid_unavailable(seat_act.prove 平台判斷在 AIB_TOUCHID 之前;CI 有 Linux,#329)
        self.assertEqual((rc, json.loads(out.strip())["why"]),
                         (3, "touchid_off" if sys.platform == "darwin" else "touchid_unavailable"))
        self.assertEqual(len(seen), 1 if sys.platform == "darwin" else 0, "touchid_off / 非 Mac 不准叫人去按指紋")

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
        with mock.patch.object(self.CB, "_my_published", lambda env: [{"cabin": "c1", "seat": "aaaa1"}]), \
                mock.patch.object(OG.GF, "send_and_wait", lambda *x, **k: {"stage": "held", "rc": 0, "detail": None}):
            rc, out = self.run_cb("connect", new)
        self.assertEqual(self.asked, [], "預設分享不彈指紋")
        self.assertEqual(rc, 0, out)
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

    def _published(self, stage="held", rc=0):
        sent = []

        def saw(*x, **k):
            sent.append(1)
            return {"stage": stage, "rc": rc, "detail": None}
        return sent, (mock.patch.object(self.CB, "_my_published", lambda env: [{"cabin": "c1", "seat": "aaaa1"}]),
                      mock.patch.object(OG.GF, "send_and_wait", saw))

    def test_c10_remove_forgets_levels(self):
        rc, out = self.run_cb("share", FRIEND, "status", "--space", "room")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.run_cb("friend", "remove", FRIEND)[0], 0)
        self.gate(ok=True, seat=True)                            # 席位來打:不准不按指紋就恢復 status / room
        self.asked.clear()
        sent, (p1, p2) = self._published()
        with p1, p2:
            rc, out = self.run_cb("connect", FRIEND, "--json")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.asked, [])
        got = json.loads(out.strip())
        self.assertEqual((got["share"], got["space"]), ("online", "island"), "移除後的舊單人分享被席位悄悄恢復")
        CR.add_friend(FRIEND, None, self.env, kind="blocks")      # 封鎖也清
        CR.remove_friend(FRIEND, self.env, "blocks")
        self.assertNotIn(FRIEND, CR.read_share(self.env)["per"])

    def test_c11_failed_request_retried(self):
        new = OG.NK.npub(OG.NK.pubkey_hex("%064x" % 0xcb11))
        rc, out = self.run_cb("connect", new)
        self.assertEqual(rc, 2, out)
        self.assertNotIn("已連線", out, "申請沒送到不准說已連線")
        self.assertIn("沒連上", out)
        self.assertFalse(CR.is_friend(new, self.env), "申請沒送出 ⇒ 新加的好友要撤掉")
        sent, (p1, p2) = self._published()
        with p1, p2:
            rc, out = self.run_cb("connect", new, "--json")
        self.assertEqual((rc, sent), (0, [1]), "重跑要真的補送申請:" + out)
        self.assertTrue(json.loads(out.strip())["ok"])

    def test_c12_pending_is_not_failure(self):
        new = OG.NK.npub(OG.NK.pubkey_hex("%064x" % 0xcb12))
        sent, (p1, p2) = self._published(stage="claimed", rc=4)
        with p1, p2:
            rc, out = self.run_cb("connect", new, "--json")
        got = json.loads(out.strip())
        self.assertEqual((rc, got["ok"], got["request"]["why"]), (4, True, "request_pending"))
        with p1, p2:
            rc, out = self.run_cb("connect", OG.NK.npub(OG.NK.pubkey_hex("%064x" % 0xcb13)))
        self.assertIn("還沒確認", out)
        self.assertNotIn("沒連上", out)

    def test_c13_open_conflicts_and_bridge_off(self):
        rc, out = self.run_cb("connect", self.OTHER, "--open", "--share", "off", "--json")
        self.assertEqual((rc, json.loads(out.strip())["why"]), (2, "open_or_space"))
        with mock.patch.object(OG.GF, "bridge_alive", lambda root: False):
            for argv in (("connect", FRIEND, "--open", "--json"), ("connect", self.OTHER, "--json")):
                rc, out = self.run_cb(*argv)
                self.assertEqual((rc, json.loads(out.strip())["why"]), (2, "bridge_off"), argv)
        self.assertEqual(self.asked, [], "門前就擋")

    def test_c14_open_partial(self):
        import chat_visit as CVI
        real = CR.set_space

        def boom(*x, **k):
            raise OSError("disk full")
        with mock.patch.object(CR, "set_space", boom):
            rc, out = self.run_cb("connect", FRIEND, "--open", "--json")
        got = json.loads(out.strip())
        self.assertEqual((rc, got["why"], got["applied"], got["share"]), (2, "share_failed", ["share"], "status"))
        self.assertIs(CR.set_space, real)
        with mock.patch.object(CR, "share_level", wraps=CR.share_level) as sl:
            def boom2(*x, **k):
                sl.side_effect = OSError("gone")                 # 寫一半之後連讀都壞
                raise OSError("disk full")
            with mock.patch.object(CVI, "open_to", boom2):
                rc, out = self.run_cb("connect", FRIEND, "--open", "--json")
        got = json.loads(out.strip())                            # 連讀都失敗:仍是一個 JSON
        self.assertEqual((got["why"], got["share"]), ("share_failed", "unknown"))

    def test_c16_failed_connect_keeps_own_levels(self):
        for share, space in (("off", "none"), ("status", "room")):
            x = OG.NK.npub(OG.NK.pubkey_hex("%064x" % (0xc160 + len(share))))
            rc, out = self.run_cb("share", x, share, "--space", space)       # 本人按過指紋設的(對方還不是好友)
            self.assertEqual(rc, 0, out)
            rc, out = self.run_cb("connect", x, "--json")                    # 沒公開席 ⇒ 申請失敗、撤好友
            self.assertEqual((rc, json.loads(out.strip())["why"]), (2, "request_failed"))
            self.assertEqual((CR.read_share(self.env)["per"].get(x), CR.read_space(self.env)["per"].get(x)), (share, space),
                             "申請失敗撤好友時把本人設的等級清掉了")

    def test_c17_remove_clears_only_wider(self):
        CR.set_share("off", FRIEND, self.env)
        CR.set_space("none", FRIEND, self.env)
        CR.remove_friend(FRIEND, self.env)
        CR.add_friend(FRIEND, None, self.env)
        self.assertEqual((CR.share_level(FRIEND, self.env), CR.space_level(FRIEND, self.env)), ("off", "none"),
                         "本人設嚴的,移除再加回不准變寬")
        CR.set_share("status", FRIEND, self.env)
        CR.set_space("room", FRIEND, self.env)
        CR.remove_friend(FRIEND, self.env)
        CR.add_friend(FRIEND, None, self.env)
        self.assertEqual((CR.share_level(FRIEND, self.env), CR.space_level(FRIEND, self.env)), ("online", "island"))

    def test_c18_undo_write_fails(self):
        new = OG.NK.npub(OG.NK.pubkey_hex("%064x" % 0xc180))
        with mock.patch.object(CR, "remove_friend", side_effect=OSError("disk full")):
            rc, out = self.run_cb("connect", new, "--json")
        got = json.loads(out.strip())
        self.assertEqual((rc, got["why"], got["detail"]), (2, "request_failed", "undo_failed"))
        new = OG.NK.npub(OG.NK.pubkey_hex("%064x" % 0xc181))       # 上一位已經留在名冊上,換一位
        with mock.patch.object(CR, "remove_friend", side_effect=OSError("disk full")):
            rc, out = self.run_cb("connect", new)
        self.assertEqual(rc, 2)
        self.assertIn("friend remove " + new, out, "好友其實留著 ⇒ 要叫人先移除,不能說「好友沒加、再打一次」")
        self.assertNotIn("好友沒加", out)

    def test_c15_known_with_request(self):
        OG.GF.add_request(self.root, {"owner": FRIEND, "msgid": OG.GF.new_msgid(), "display_name": "Pro", "note": ""},
                          every=0)
        rc, out = self.run_cb("connect", FRIEND, "--share", "status", "--json")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("加好友", self.asked[0], "已是好友 ⇒ 視窗不寫加好友")

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
        self._knife('    notify = HUMAN_GATE["notify"] or (_gate_notify if HUMAN_GATE["prove"] is None and _gate_pops(env) else None)\n',
                    '    notify = HUMAN_GATE["notify"]\n', "Batch.test_b7_real_path_notifies_first")

    def test_k6_remove_keeps_levels(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_roster.py", "            _forget_levels(owner, env)\n", "            pass\n",
                        tests=(name, "test-chat-open-touchid-d07f9-1021.py")) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), "Connect.test_c10_remove_forgets_levels"],
                            cwd=str(root), max_procs=60, timeout=180)
        self.assertIsNone(r.tripped)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k7_failed_request_keeps_friend(self):
        self._knife("                CR.remove_friend(owner, env, forget=False)\n",
                    "                pass\n", "Connect.test_c11_failed_request_retried")

    def test_k8_undo_forgets(self):
        self._knife("                CR.remove_friend(owner, env, forget=False)\n",
                    "                CR.remove_friend(owner, env)\n", "Connect.test_c16_failed_connect_keeps_own_levels")

    def test_k9_remove_clears_all(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_roster.py",
                        '    if lv in SHARE_LEVELS and SHARE_LEVELS.index(lv) > SHARE_LEVELS.index(rec["default"]):\n',
                        "    if lv is not None:\n", tests=(name, "test-chat-open-touchid-d07f9-1021.py")) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), "Connect.test_c17_remove_clears_only_wider"],
                            cwd=str(root), max_procs=60, timeout=180)
        self.assertIsNone(r.tripped)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k5_window_uses_default(self):
        self._knife('        cur_sp = CR.space_level(owner, env) if known else ',
                    '        cur_sp = CR.read_space(env)["default"] if known else ', "Connect.test_c5_existing_friend_shows_actual")


if __name__ == "__main__":
    unittest.main()
