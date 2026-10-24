#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""右鍵選單加固(10-24 @abb89,#286 #287 審查記下排下一版的幾條)。

H1 _safe_text 拿掉雙向覆寫 / 零寬字元(選單標題照字面畫,U+202E 能把名字畫反、零寬字能讓兩席標題看起來一樣);表情符號照留
H2 hit_ai_close 跟點 [X] 同一套前提:kill 的 start < end、窗 id 是 @數字;不合就不給「關閉這一席」
H3 open_readiness 整批最多等 TIMEOUT_S 秒,超時的幾家印 ok(選單照開、行數不少)
H4 --close-row 的欄(CX)也只收 1–4 位數字(在 test-ctx-close-seat-abb89-1024.py)
刀(PR 手跑在複本上):拿掉 _INVISIBLE 或少列任一碼點 ⇒ H1 紅;拿掉 start<end / WINDOW_ID_RE ⇒ H2 紅;
  拿掉計時器、_Late 改回 Exception(被 check() 吞掉)、不還原 SIGALRM 處理器 ⇒ H3 紅
"""
from __future__ import annotations

import io
import os
import sys
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
MOUSE = REPO / "src" / "cockpit" / "mouse"
sys.path.insert(0, str(MOUSE))

import hit_test  # noqa: E402
import open_readiness  # noqa: E402

ROW = {"line": 3, "window": "@35", "kill": {"start": 20, "end": 23},
       "detail": {"kind": "seat", "label": "dev1@4fc4f", "engine": "codex", "context": "", "status": "💤"}}


class SafeText(unittest.TestCase):
    def test_h1_invisible_dropped(self):
        # 逐一寫死(不迴圈 _INVISIBLE 本身:少列一個也要紅)
        for cp in (0x200B, 0x200C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
                   0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0x2066, 0x2067, 0x2068, 0x2069, 0xFEFF):
            with self.subTest(cp=hex(cp)):
                self.assertEqual(hit_test._safe_text("dev" + chr(cp) + "1"), "dev1")

    def test_h1_emoji_and_text_kept(self):
        self.assertEqual(hit_test._safe_text("💤 閒置"), "💤 閒置")
        self.assertEqual(hit_test._safe_text("👩\u200d💻"), "👩\u200d💻")      # ZWJ 組合表情留著

    def test_h1_close_label_uses_it(self):
        row = dict(ROW, detail=dict(ROW["detail"], label="dev1\u202e@4fc4f"))
        self.assertEqual(hit_test.hit_ai_close([row], 3)["label"], "dev1@4fc4f")


class HitClose(unittest.TestCase):
    def test_h2_normal_row_still_closes(self):
        self.assertEqual(hit_test.hit_ai_close([ROW], 3)["action"], "kill-idle")

    def test_h2_x_cell_must_exist(self):
        for kill in ({"start": 23, "end": 20}, {"start": 20, "end": 20}, {"start": "a", "end": 23}, {"end": 23}, {}):
            with self.subTest(kill=kill):
                self.assertEqual(hit_test.hit_ai_close([dict(ROW, kill=kill)], 3), hit_test.PASSTHROUGH)

    def test_h2_window_must_be_tmux_id(self):
        self.assertEqual(hit_test.hit_ai_close([dict(ROW, window="@1234567890")], 3)["action"], "kill-idle")
        for w in ("35", "@", "@3a", "@35;kill-server", "%35", "@12345678901"):
            with self.subTest(window=w):
                self.assertEqual(hit_test.hit_ai_close([dict(ROW, window=w)], 3), hit_test.PASSTHROUGH)


class Readiness(unittest.TestCase):
    def test_h3_slow_check_times_out_to_ok(self):
        def check(provider, environ=None):
            if provider == "slow":
                time.sleep(5)
            return ("need_key", "DEEPSEEK_API_KEY") if provider == "deepseek" else ("ok", "")
        out = io.StringIO()
        t0 = time.monotonic()
        with mock.patch.object(open_readiness, "check", check), \
                mock.patch.dict(os.environ, {"EZ2_OPEN_READINESS_TIMEOUT": "0.3"}), redirect_stdout(out):
            rc = open_readiness.main(["deepseek", "slow", "claude"])
        self.assertLess(time.monotonic() - t0, 2)
        self.assertEqual(rc, 0)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 3, lines)
        self.assertTrue(lines[0].startswith("need_key\tDEEPSEEK_API_KEY\t"), lines)
        self.assertEqual(lines[1:], ["ok", "ok"])

    def test_h3_real_check_path_times_out(self):
        # 不換掉 check():它裡面的 except Exception 不能把逾時吞掉(審查 r1)
        def slow_row(provider, providers_json=None):
            time.sleep(2)
            return {}
        out = io.StringIO()
        t0 = time.monotonic()
        with mock.patch.object(open_readiness, "_row", slow_row), \
                mock.patch.dict(os.environ, {"EZ2_OPEN_READINESS_TIMEOUT": "0.3"}), redirect_stdout(out):
            rc = open_readiness.main(["deepseek", "nvidia", "openrouter"])
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().splitlines(), ["ok", "ok", "ok"])

    def test_h3_timer_cleared_after(self):
        sig = open_readiness.signal
        before = sig.getsignal(sig.SIGALRM)
        with mock.patch.object(open_readiness, "check", lambda p, environ=None: ("ok", "")), \
                redirect_stdout(io.StringIO()):
            open_readiness.main(["claude"])
        self.assertEqual(sig.getitimer(sig.ITIMER_REAL)[0], 0)
        self.assertIs(sig.getsignal(sig.SIGALRM), before)       # 舊處理器還原

    def test_h3_timeout_env_only_shortens(self):
        for v, want in (("0.5", 0.5), ("99", 3.0), ("0", 3.0), ("-1", 3.0), ("x", 3.0)):
            with self.subTest(v=v), mock.patch.dict(os.environ, {"EZ2_OPEN_READINESS_TIMEOUT": v}):
                self.assertEqual(open_readiness._timeout_s(), want)


if __name__ == "__main__":
    unittest.main()
