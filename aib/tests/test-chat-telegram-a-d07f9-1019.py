#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""Telegram 互串第一步(開發文件第 39 節)。全程本機假 Telegram 伺服器,不打 api.telegram.org。

T1 api_base 只收本機假伺服器(其他一律回正式站;不讓設定把金鑰送去別處);壞金鑰不建;錯誤訊息不帶金鑰
T2 收:私聊文字 ⇒ 艦長席待審(im_room)、external_im、im 欄;群組 / 機器人 / 非文字跳過;封鎖丟掉;限量
T3 只給艦長室:放行(requeue)與排空投遞都拒;收件席不准自己標讀;分組、待審提示、hold 徽章都不算它
T4 poll_once:關著不拉;開著拉完 offset 往前;輪詢中被封鎖照新的
T5 寄:沒傳過 / 封鎖 / 空的不寄;本人證明沒過不寄;過了打到假伺服器 sendMessage
T6 金鑰不進錯誤訊息與 audit
刀(複本):K1 收件不待審 ⇒ T2 紅;K2 拿掉放行擋 ⇒ T3 紅;K3 api_base 什麼都收 ⇒ T1 紅
"""
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(HERE))
import chat_telegram as TG  # noqa: E402
import dm_queue as Q  # noqa: E402
import dm_triage as TR  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

TOKEN = "123456789:" + "A" * 35
SEAT = "cc203"
UID = "70001"


class FakeTelegram(object):
    """假 Bot API:/bot<token>/getUpdates 回排好的 updates;/sendMessage 記下來;fail=True 一律 500。"""

    def __init__(self):
        self.updates, self.sent, self.calls, self.fail = [], [], [], False
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                tok, _, method = self.path.lstrip("/").partition("/")
                outer.calls.append((tok, method, body))
                if outer.fail or tok != "bot" + TOKEN:
                    self.send_response(500 if outer.fail else 401)
                    self.end_headers()
                    return
                if method == "getUpdates":
                    res = [u for u in outer.updates if u["update_id"] >= int(body.get("offset") or 0)]
                elif method == "sendMessage":
                    outer.sent.append(body)
                    res = {"message_id": len(outer.sent)}
                else:
                    res = None
                data = json.dumps({"ok": True, "result": res}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def upd(n, uid=UID, text="你好", ctype="private", is_bot=False, first="Bob"):
    msg = {"message_id": n, "chat": {"id": int(uid), "type": ctype},
           "from": {"id": int(uid), "is_bot": is_bot, "first_name": first}}
    if text is not None:
        msg["text"] = text
    return {"update_id": n, "message": msg}


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-tg-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "AIB_DM_ADDR": "100.114.152.121",
                    "EZ2_AIB_LOCALE": "zh-TW"}
        self.root = self.td / "bridge"
        self.root.mkdir()
        TG.set_on(self.root, True, SEAT)
        self.tg = FakeTelegram()
        self.addCleanup(self.tg.close)
        self.api = TG.Api(TOKEN, self.tg.url)

    def handle(self, u):
        st = TG.load_state(self.root)
        r = TG.handle_update(self.root, st, u, env=self.env)
        TG.save_state(self.root, st)
        return r

    def item(self, r):
        return Q._load_item(SEAT, r.split(":", 1)[1], self.env)


class Api(Base):
    def test_t1_api_base_and_token(self):
        self.assertEqual(TG.api_base({"AIB_TELEGRAM_API": self.tg.url}), self.tg.url)
        for bad in ("https://evil.example", "http://10.0.0.5:80", "http://127.0.0.1.evil:80", "", None):
            self.assertEqual(TG.api_base({"AIB_TELEGRAM_API": bad}), TG.API_DEFAULT, repr(bad))
        for bad in ("", "abc", "123:short", None):
            with self.assertRaises(TG.TgError):
                TG.Api(bad, self.tg.url)

    def test_t6_token_not_leaked(self):
        self.tg.fail = True
        with self.assertRaises(TG.TgError) as cm:
            self.api.call("getMe")
        self.assertNotIn(TOKEN.split(":")[1], str(cm.exception))
        TG.send(self.root, UID, "x", self.api)                     # 沒傳過 ⇒ 不打;打一筆會失敗的
        TG.set_relation(self.root, UID, "friend")
        self.assertFalse(TG.send(self.root, UID, "x", self.api)["ok"])
        audit = "".join(p.read_text() for p in (self.root / "audit").glob("*.jsonl"))
        self.assertNotIn(TOKEN.split(":")[1], audit)
        self.assertNotIn('"x"', audit, "audit 不記內容")


class Recv(Base):
    def test_t2_receive(self):
        r = self.handle(upd(1, text="測試請回話"))
        self.assertTrue(r.startswith("queued_room:"), r)
        it = self.item(r)
        self.assertEqual((it["status"], it["hold_why"], it["sender_kind"], it["sender_origin"]),
                         (Q.HOLD_STATUS, Q.IM_ROOM_WHY, "external_im", "telegram:" + UID))
        self.assertEqual(it["im"], {"net": "telegram", "id": UID, "shown": "Bob", "relation": "stranger"})
        self.assertNotIn("global", it)
        self.assertEqual(self.handle(upd(2, ctype="group")), "skip:not_private")
        self.assertEqual(self.handle(upd(3, is_bot=True)), "skip:not_private")
        self.assertEqual(self.handle(upd(4, text=None)), "skip:not_text")
        TG.set_relation(self.root, UID, "blocked")
        self.assertEqual(self.handle(upd(5)), "blocked")
        TG.set_relation(self.root, UID, "friend")
        got = [self.handle(upd(10 + i)) for i in range(TG.RATE_USER_PER_MIN + 2)]
        self.assertEqual(got.count("rate"), 3, "同一位每分鐘 20 則(第 1 則已算)")

    def test_t3_room_only(self):
        it = self.item(self.handle(upd(1)))
        self.assertIsNone(Q.requeue(SEAT, it["id"], env=self.env, actor="human:andy"), "放行要拒")
        res = Q._transition(SEAT, it["id"], "read", "seat:" + SEAT, env=self.env)
        self.assertFalse(res["ok"], "收件席不准自己標讀")
        self.assertIsNone(Q.next_to_drain(SEAT, env=self.env))
        rows = Q.tabs_payload(SEAT, env=self.env)["tabs"]["waiting"]["rows"]
        self.assertEqual([r["id"] for r in rows], [it["id"]], "人看的待審框有")
        self.assertEqual(rows[0]["im"]["contact_key"], "telegram:" + UID)
        self.assertEqual(TR.eligible_rows(rows), [], "分組不算")
        self.assertEqual(Q._hold_ids_now(SEAT, self.env, int(time.time())), [], "待審提示不算")
        b = Q.brief_payload(SEAT, env=self.env)
        self.assertEqual((b["hold"], b["im_hold"]), (0, 1))
        res = Q._transition(SEAT, it["id"], "deleted", "human:andy", env=self.env, why="seen")
        self.assertTrue(res["ok"], "主人在艦長室看完可以收掉")

    def test_t3b_forged_kind_downgraded(self):
        it = Q.enqueue(SEAT, "x", "Telegram:Bob", sender_kind="external_im", sender_origin="nostr:npub1x", env=self.env,
                       im_info={"net": "telegram", "id": UID, "shown": "Bob"})
        self.assertEqual(it["sender_kind"], "unknown")
        self.assertNotIn("im", it)
        it = Q.enqueue(SEAT, "x", "Telegram:Bob", sender_kind="external_im", sender_origin="telegram:999", env=self.env,
                       im_info={"net": "telegram", "id": UID, "shown": "Bob"})
        self.assertNotIn("im", it, "im.id 要跟來源一致")


class Poll(Base):
    def test_t4_poll(self):
        self.tg.updates = [upd(41), upd(42, text="第二則")]
        TG.set_on(self.root, False)
        self.assertEqual(TG.poll_once(self.root, self.api, env=self.env, timeout=0), [])
        self.assertEqual(self.tg.calls, [], "關著不打")
        TG.set_on(self.root, True, SEAT)
        out = TG.poll_once(self.root, self.api, env=self.env, timeout=0)
        self.assertEqual(len(out), 2)
        self.assertEqual(TG.load_state(self.root)["offset"], 43)
        self.assertEqual(TG.poll_once(self.root, self.api, env=self.env, timeout=0), [], "offset 往前,不重收")
        self.assertEqual(TG.load_state(self.root)["contacts"][UID]["shown"], "Bob")


class Send(Base):
    def test_t5_send(self):
        self.assertEqual(TG.send(self.root, UID, "hi", self.api)["detail"], "unknown_contact")
        self.handle(upd(1))
        self.assertEqual(TG.send(self.root, UID, " ", self.api)["detail"], "empty")
        res = TG.send_as_human(self.root, UID, "hi", self.api, self.env, prove=lambda reason, env: "touchid_cancel")
        self.assertEqual((res["ok"], res["detail"]), (False, "touchid_cancel"))
        self.assertEqual(self.tg.sent, [], "本人證明沒過不寄")
        res = TG.send_as_human(self.root, UID, "收到", self.api, self.env, prove=lambda reason, env: None)
        self.assertEqual((res["ok"], res["stage"]), (True, "sent"), res)
        self.assertEqual(self.tg.sent, [{"chat_id": int(UID), "text": "收到"}])
        TG.set_relation(self.root, UID, "blocked")
        self.assertEqual(TG.send(self.root, UID, "hi", self.api)["detail"], "blocked")


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=200)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_not_held(self):
        self._knife("src/cockpit/chat_telegram.py", "status=DQ.HOLD_STATUS, hold_why=DQ.IM_ROOM_WHY", "status=\"queued\"",
                    "Recv.test_t2_receive")

    def test_k2_release_allowed(self):
        self._knife("src/cockpit/dm_queue.py", '            return refuse("im_room_only")', "            pass",
                    "Recv.test_t3_room_only")

    def test_k3_any_api_host(self):
        self._knife("src/cockpit/chat_telegram.py", "return v if LOCAL_API_RE.fullmatch(v) else API_DEFAULT",
                    "return v or API_DEFAULT", "Api.test_t1_api_base_and_token")


if __name__ == "__main__":
    unittest.main()
