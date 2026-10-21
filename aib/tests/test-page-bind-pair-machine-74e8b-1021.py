#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib page bind 配對碼流程也綁機器(10-21 @9f808 同意:/bind/start 收 machine、bound 時回 machine_id)。

手機遠端只認 machine_id 對上這台的那一頁;以前只有 --serial 綁的有 machine_id ⇒ 配對碼綁的會員打開遠端只看到「沒綁」。
B1 配對碼:/bind/start 帶 machine {id, name}(跟 serial 同一份 machine.json);bound 回 machine_id ⇒ 存檔、remote_link.target 認得到
B2 bound 回的 machine_id 不是這台 ⇒ 不存檔
B3 舊伺服器(還不收 machine,回 400)⇒ 不帶 machine 再要一次、照舊綁(沒有 machine_id,遠端仍是 not-bound)
B4 /bind/start 其他錯(例如 5xx)不重試、照舊報錯
B6 數字配對(@9f808 confirm_digits):兩位數照原樣印在終端機(前面的 0 留著);格式不對 / 舊伺服器沒有 ⇒ 不印
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
    def __init__(self, reject_machine=False, machine_id=None, start_error=None, digits=None):
        self.starts, self.reject, self.mid, self.err, self.digits = [], reject_machine, machine_id, start_error, digits

    def bind_start(self, seat_label, machine=None):
        self.starts.append(machine)
        if self.err:
            raise self.err
        if machine and self.reject:
            raise ApiError(400, "page_api_validation_failed")
        rec = {"pair_code": "ABCD-1234", "bind_ticket": "t" * 20, "confirm_url": "https://ez2.page/manage/aib",
               "expires_at": (NOW + timedelta(minutes=10)).isoformat()}
        if self.digits is not None:
            rec["confirm_digits"] = self.digits
        return rec

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
        with self.assertRaises(B.BindError):
            self.run_bind(FakeApi(machine_id="00000000-0000-4000-8000-000000000000"))
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

    def test_b6_confirm_digits(self):
        self.run_bind(FakeApi(machine_id=self.machine["id"], digits="07"))
        self.assertTrue(any("確認數字 07" in l for l in self.lines), self.lines)
        for bad in ("7", "077", 7, "ab"):
            self.lines.clear()
            self.run_bind(FakeApi(machine_id=self.machine["id"], digits=bad))
            self.assertFalse(any("確認數字" in l for l in self.lines), bad)
        self.lines.clear()
        self.run_bind(FakeApi(machine_id=self.machine["id"]))
        self.assertFalse(any("確認數字" in l for l in self.lines), "舊伺服器沒有 ⇒ 不印")


if __name__ == "__main__":
    unittest.main()
