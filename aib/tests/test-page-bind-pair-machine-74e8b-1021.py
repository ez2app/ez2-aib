#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib page bind 配對碼流程也綁機器(10-21 @9f808 同意:/bind/start 收 machine、bound 時回 machine_id)。

手機遠端只認 machine_id 對上這台的那一頁;以前只有 --serial 綁的有 machine_id ⇒ 配對碼綁的會員打開遠端只看到「沒綁」。
B1 配對碼:/bind/start 帶 machine {id, name}(跟 serial 同一份 machine.json);bound 回 machine_id ⇒ 存檔、remote_link.target 認得到
B2 bound 回的 machine_id 不是這台 ⇒ 不存檔
B3 舊伺服器(還不收 machine,回 400 page_api_seat_label_invalid)⇒ 不帶 machine 再要一次、照舊綁(沒有 machine_id,遠端仍是 not-bound)
B4 /bind/start 其他錯(例如 5xx)不重試、照舊報錯
B5 新伺服器嫌 machine 本身不合格(400 page_api_body_invalid)⇒ 不退回、照報錯(不靜靜綁成沒機器的)
B2 錯誤訊息要叫人去 /manage/aib 解除剛綁的那把(伺服器已發出、佔名額)
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src" / "cockpit" / "page"))
sys.path.insert(0, str(HERE.parent / "src" / "cockpit"))
sys.path.insert(0, str(HERE.parent / "src"))
import bind as B  # noqa: E402
import store  # noqa: E402
from api import ApiError  # noqa: E402

NOW = datetime(2026, 10, 21, 4, 0, tzinfo=timezone.utc)
TOKEN = "ezp_" + "A" * 43


class FakeApi:
    def __init__(self, reject_machine=False, machine_id=None, start_error=None):
        self.starts, self.reject, self.mid, self.err = [], reject_machine, machine_id, start_error

    def bind_start(self, seat_label, machine=None):
        self.starts.append(machine)
        if self.err:
            raise self.err
        if machine and self.reject:
            raise ApiError(400, "page_api_seat_label_invalid")
        return {"pair_code": "ABCD-1234", "bind_ticket": "t" * 20, "confirm_url": "https://ez2.page/manage/aib",
                "expires_at": (NOW + timedelta(minutes=10)).isoformat()}

    def bind_poll(self, ticket):
        rec = {"status": "bound", "assistant_token": TOKEN, "token_id": "tk1", "tenant_id": "t_lfu0327", "page_id": "p1", "slug": "lfu0327",
               "scope": ["draft:read", "draft:write", "assets:write"], "expires_at": (NOW + timedelta(days=30)).isoformat()}
        if self.mid:
            rec["machine_id"] = self.mid
        return rec


class Bind(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"HOME": self.tmp.name, "EZ2_AIB_HOME": self.tmp.name, "EZ2_AIB_STATE_ROOT": os.path.join(self.tmp.name, "state")}
        self.machine = store.machine_identity(self.env)
        self.lines = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_bind(self, api):
        return B.run_bind(api, "74e8b dev", self.lines.append, environ=self.env, sleep=lambda s: None, clock=lambda: NOW)

    def test_b1_pair_sends_machine(self):
        api = FakeApi(machine_id=self.machine["id"])
        rec, path = self.run_bind(api)
        self.assertEqual(api.starts, [self.machine], "跟 serial 同一份 machine.json")
        self.assertEqual(rec["machine_id"], self.machine["id"])
        self.assertTrue(os.path.exists(path))
        import remote_link
        tok, why = remote_link.target(self.env)
        self.assertIsNone(why, "配對碼綁的頁手機遠端也認得到")
        self.assertEqual(tok[0], TOKEN)

    def test_b2_wrong_machine(self):
        with self.assertRaises(B.BindError) as cm:
            self.run_bind(FakeApi(machine_id="00000000-0000-4000-8000-000000000000"))
        self.assertIn("ez2.page/manage/aib", str(cm.exception), "叫人去解除孤兒憑證")
        self.assertEqual(store.list_tokens(self.env) if hasattr(store, "list_tokens") else [], [], "不存檔")

    def test_b3_old_server(self):
        api = FakeApi(reject_machine=True)
        rec, _p = self.run_bind(api)
        self.assertEqual(api.starts, [self.machine, None], "400 ⇒ 不帶 machine 再要一次")
        self.assertNotIn("machine_id", rec)
        import remote_link
        self.assertEqual(remote_link.target(self.env), (None, "not-bound"), "沒有 machine_id ⇒ 遠端照舊 not-bound")

    def test_b4_other_error(self):
        api = FakeApi(start_error=ApiError(503, "page_api_unavailable"))
        with self.assertRaises(B.BindError):
            self.run_bind(api)
        self.assertEqual(len(api.starts), 1, "不是 400 不重試")

    def test_b5_machine_invalid_not_fallback(self):
        api = FakeApi(start_error=ApiError(400, "page_api_body_invalid", details=["machine"]))
        with self.assertRaises(B.BindError):
            self.run_bind(api)
        self.assertEqual(len(api.starts), 1, "machine 不合格 ⇒ 不退回不帶 machine")


if __name__ == "__main__":
    unittest.main()
