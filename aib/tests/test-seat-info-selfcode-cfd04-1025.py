#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib-boards seat-info:前面都認不到時退回 seat_now.self_code(#370 拆出的 F,r1 H2 修正)。

S1 背景對話:自己的對話編號對不到席位檔、沒有 TMUX ⇒ 用對話編號環境變數認出那一席(uuid_via=self_code)
S2 r1 H2:在 tmux 裡、窗號對得到子席,但環境變數繼承了 root 的對話編號 ⇒ 認成子席(窗號那條排在前面),不是 root
S3 環境變數對到的席已退席 ⇒ 不認(code None)
S4 #376 r1 Low-1:在 tmux 裡、窗號對到的席位檔沒有 session_uuid、argv 沒給對話編號,環境殘留同艙 root 的對話編號
   ⇒ 留在窗號那一席(seat_file = w7、不是 root),不走 (3)。刀:拿掉 `not out["seat_file"]` ⇒ S4 紅。
刀:拿掉 (3) ⇒ S1 紅(已驗)。S3 有兩道:self_code 本身只收沒退席的、(3) 再擋一次 retired;只拿掉 (3) 那道仍綠。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SCRIPT = REPO.parent / "extensions" / "aib-boards" / "bin" / "seat-info.py"
CABIN = "20261014-133230-6ddf"
ROOT_U = "aaaaaaaa-1111-4111-8111-1111111aaaaa"
CHILD_U = "bbbbbbbb-2222-4222-8222-2222222bbbbb"
BG_U = "11111111-2222-4333-8444-555555555555"


class SeatInfo(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-seatinfo-")
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.seats = self.tmp / "instances" / CABIN / "seats"
        self.seats.mkdir(parents=True)

    def seat(self, win, uuid, role, **kw):
        rec = {"schema": "aib-seat/v1", "session_uuid": uuid, "role": role, "window_id": win,
               "cabin_id": CABIN, "started_epoch": 1}
        rec.update(kw)
        (self.seats / ("w%s.json" % win.lstrip("@"))).write_text(json.dumps(rec))

    def run_probe(self, extra, argv=(BG_U,)):
        base = {k: v for k, v in os.environ.items() if not k.startswith(("TMUX", "AIB_", "CLAUDE_CODE_", "EZ2_", "CODEX_"))}
        base.update({"EZ2_AIB_STATE_ROOT": str(self.tmp), "HOME": str(self.tmp),
                     "EZ2_COCKPIT_DIR": str(REPO / "src" / "cockpit"), "PYTHONDONTWRITEBYTECODE": "1"})
        base.update(extra)
        r = subprocess.run([sys.executable, "-B", str(SCRIPT), *argv], env=base, capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def fake_tmux(self, win):
        p = self.tmp / "faketmux"
        p.write_text("#!/bin/sh\necho %s\n" % win)
        p.chmod(0o755)
        return str(p)

    def test_s1_background_session_env(self):
        self.seat("@4", ROOT_U, "root")
        got = self.run_probe({"CLAUDE_CODE_SESSION_ID": ROOT_U})
        self.assertEqual((got["code"], got.get("uuid_via"), got["cabin"]), ("aaaaa", "self_code", CABIN), got.get("err"))

    def test_s2_window_beats_inherited_root_uuid(self):
        self.seat("@4", ROOT_U, "root")
        self.seat("@7", CHILD_U, "dev")
        got = self.run_probe({"CLAUDE_CODE_SESSION_ID": ROOT_U, "TMUX": "/tmp/tmux-501/aihub-%s,1,0" % CABIN,
                              "TMUX_PANE": "%99", "AIB_TMUX_BIN": self.fake_tmux("@7")})
        self.assertEqual((got["code"], got["role"]), ("bbbbb", "dev"), got.get("err"))
        self.assertNotEqual(got.get("uuid_via"), "self_code")

    def test_s3_retired_not_recognised(self):
        self.seat("@4", ROOT_U, "root", retired=True)
        got = self.run_probe({"CLAUDE_CODE_SESSION_ID": ROOT_U})
        self.assertIsNone(got["code"])

    def test_s4_window_seat_without_uuid_not_overridden(self):
        self.seat("@4", ROOT_U, "root")
        self.seat("@7", None, "dev")
        got = self.run_probe({"CLAUDE_CODE_SESSION_ID": ROOT_U, "TMUX": "/tmp/tmux-501/aihub-%s,1,0" % CABIN,
                              "TMUX_PANE": "%99", "AIB_TMUX_BIN": self.fake_tmux("@7")}, argv=())
        self.assertEqual((Path(got["seat_file"]).name, got["role"]), ("w7.json", "dev"), got.get("err"))
        self.assertNotEqual(got.get("uuid_via"), "self_code")
        self.assertNotEqual(got["code"], "aaaaa")


if __name__ == "__main__":
    unittest.main()
