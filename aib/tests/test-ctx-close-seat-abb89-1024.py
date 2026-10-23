#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""右鍵「關閉這一席」(10-24 @abb89,右鍵 14 家實測第 7 項;Andy「依照你的建議開始修到全好」)。

以前要關一席只能瞄準那一列尾巴的 [X] / (x) 小格;右鍵選單沒有關閉。
現在右鍵那一列多一項「關閉這一席」,點了彈的是跟點 [X] **同一個**確認選單(取消排第一、
真關只在 --release-confirmed、軟關照樣帶 --soft-since)—— 不另開一條關法、不放寬誰能被關:
只有那一列本來就有 [X] / (x) 才出現。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COCKPIT = REPO / "src" / "cockpit"
MOUSE = COCKPIT / "mouse"
sys.path.insert(0, str(MOUSE))

import hit_test  # noqa: E402

IDLE = {"line": 3, "window": "@35", "kill": {"start": 20, "end": 23, "soft": True},
        "detail": {"kind": "seat", "label": "dev1@4fc4f", "engine": "codex", "context": "", "status": "💤"}}
HARD = {"line": 5, "window": "@37", "kill": {"start": 20, "end": 23},
        "detail": {"kind": "seat", "label": "dev3@bbbbb", "engine": "codex", "context": "", "status": "💤"}}
BUSY = {"line": 4, "window": "@36",
        "detail": {"kind": "seat", "label": "dev2@aaaaa", "engine": "claude", "context": "", "status": "忙"}}


class HitClose(unittest.TestCase):
    def test_same_action_as_clicking_x(self):
        for row in (IDLE, HARD):
            want = hit_test.hit_ai_rows([row], row["line"], 21)
            self.assertEqual(want.get("action"), "kill-idle")
            self.assertEqual(hit_test.hit_ai_close([row], row["line"]), want)    # 整列都算,x 不必瞄準

    def test_soft_flag_carried(self):
        self.assertIs(hit_test.hit_ai_close([IDLE], 3)["soft"], True)
        self.assertIs(hit_test.hit_ai_close([HARD], 5)["soft"], False)

    def test_no_x_no_close(self):
        self.assertEqual(hit_test.hit_ai_close([BUSY], 4), hit_test.PASSTHROUGH)
        self.assertEqual(hit_test.hit_ai_close([IDLE], 9), hit_test.PASSTHROUGH)
        self.assertEqual(hit_test.hit_ai_close([dict(IDLE, kill_pending=True)], 3), hit_test.PASSTHROUGH)
        self.assertEqual(hit_test.hit_ai_close([IDLE], "x"), hit_test.PASSTHROUGH)
        # 審查 r1(M2/M3):kill 不是 dict、window 空 ⇒ 不給
        for bad in (True, "yes", 1, [20, 23]):
            self.assertEqual(hit_test.hit_ai_close([dict(IDLE, kill=bad)], 3), hit_test.PASSTHROUGH, bad)
        self.assertEqual(hit_test.hit_ai_close([dict(IDLE, window="")], 3), hit_test.PASSTHROUGH)

    def test_soft_must_be_exactly_true(self):
        for v in ("yes", 1, "true"):
            row = dict(IDLE, kill={"start": 20, "end": 23, "soft": v})
            self.assertIs(hit_test.hit_ai_close([row], 3)["soft"], False, v)

    def test_label_is_sanitised(self):
        row = dict(IDLE, detail=dict(IDLE["detail"], label="dev1\x1b[31m\nX"))
        label = hit_test.hit_ai_close([row], 3)["label"]
        self.assertNotIn("\x1b", label)
        self.assertNotIn("\n", label)

    def test_close_intent_only_for_ai_list(self):
        st = Path(tempfile.mkdtemp())
        try:
            (st / "ai-rowmap.json").write_text(json.dumps([IDLE], ensure_ascii=False), encoding="utf-8")
            for role, want in (("ai-list", "kill-idle"), ("tabs", hit_test.PASSTHROUGH.get("action"))):
                r = subprocess.run([sys.executable, str(MOUSE / "hit_test.py"), "--role", role, "--x", "5", "--y", "3",
                                    "--intent", "close", "--state-dir", str(st)],
                                   capture_output=True, text=True, timeout=30)
                self.assertEqual(json.loads(r.stdout).get("action"), want, role)
        finally:
            import shutil
            shutil.rmtree(st, ignore_errors=True)


class Menu(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.st = self.tmp / "st"
        self.st.mkdir()
        (self.st / "ai-rowmap.json").write_text(json.dumps([IDLE, BUSY, HARD], ensure_ascii=False), encoding="utf-8")
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.tmp), "HOME": str(self.tmp), "EZ2_COCKPIT_STATE_DIR": str(self.st),
                    "EZ2_PY3": sys.executable, "AIB_LANG": "zh-TW", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}

    def tearDown(self):
        self._tmp.cleanup()

    BASH = "/bin/bash" if os.path.exists("/bin/bash") else "bash"     # Mac 上真的用內建 3.2 跑

    def run_ctl(self, *args, dry_last=True):
        argv = [self.BASH, str(MOUSE / "mouse-ctl.sh"), *args] + (["--dry-run"] if dry_last else [])
        r = subprocess.run(argv, env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def fake_tmux(self, cabins=None):
        # 只記錄、不連真 tmux:真的叫到 tmux 就留一行(用來抓「--dry-run 被吃掉」)。
        # cabins={session: 狀態夾} ⇒ 那個 session 的艙身份查詢照實回(不記)
        b = self.tmp / "bin"
        b.mkdir(exist_ok=True)
        log = self.tmp / "tmux.log"
        cases = "".join(
            "  *'show-option -qv -t %s @aib_instance'*) echo inst-%s ;;\n"
            "  *'show-option -qv -t %s @aib_hub_socket'*) echo hub-%s ;;\n"
            "  *'show-option -qv -t %s @aib_state_dir'*) echo %s ;;\n" % (k, k, k, k, k, v)
            for k, v in (cabins or {}).items())
        (b / "tmux").write_text("#!/bin/sh\ncase \"$*\" in\n%s  *) echo \"$@\" >> %s; exit 1 ;;\nesac\n"
                                % (cases, log))
        os.chmod(b / "tmux", 0o700)
        self.env["PATH"] = "%s:%s" % (b, self.env["PATH"])
        return log

    def label(self):
        import i18n_lib
        return i18n_lib.t("aib.context.menu.close_seat", locale="zh-TW")

    def test_context_menu_has_close_only_for_rows_with_x(self):
        sys.path.insert(0, str(COCKPIT))
        out = self.run_ctl("--context", "pane", "ai-list", "5", "3", "%1", "s1")
        self.assertIn(self.label(), out)
        self.assertIn("--close-row ai-list 5 3 s1 --expect-window @35", out)   # 帶 session 與選單開出時那一席                          # 帶選單開出時那一席的窗 id
        self.assertIn("--role right", out)
        busy = self.run_ctl("--context", "pane", "ai-list", "5", "4", "%1", "s1")
        self.assertNotIn(self.label(), busy)
        self.assertNotIn("--close-row", busy)

    def test_close_row_opens_the_x_confirm_menu(self):
        out = self.run_ctl("--role", "right", "--close-row", "ai-list", "5", "3", "s1", "--expect-window", "@35")
        menu = next(l for l in out.splitlines() if "display-menu" in l)
        self.assertIn("dev1@4fc4f", menu)                                  # 標題帶身分
        self.assertLess(menu.index("release cancelled"), menu.index("--release-confirmed @35"))   # 取消排第一
        self.assertIn("--soft-since", menu)                                # 軟關語意照舊
        hard = next(l for l in self.run_ctl("--role", "right", "--close-row", "ai-list", "5", "5", "s1",
                                               "--expect-window", "@37").splitlines()
                    if "display-menu" in l)
        self.assertIn("--release-confirmed @37", hard)
        self.assertNotIn("--soft-since", hard)

    def test_close_row_on_row_without_x_does_nothing(self):
        out = self.run_ctl("--role", "right", "--close-row", "ai-list", "5", "4", "s1", "--expect-window", "@36")
        self.assertNotIn("display-menu", out)
        self.assertNotIn("release-confirmed", out)

    def test_close_row_never_runs_click_preambles(self):
        # 審查 r1 M1:標題列(y<=1)不能變成切分頁;捲軸前置也不碰
        (self.st / "scrollbar-grab").write_text("1")
        for y in ("0", "1", "abc"):
            out = self.run_ctl("--role", "right", "--close-row", "ai-list", "5", y, "s1")
            self.assertNotIn("right-tab", out, y)
            self.assertNotIn("display-menu", out, y)
        self.assertFalse((self.tmp / "right-tab").exists())
        self.assertTrue((self.st / "scrollbar-grab").exists())
        self.assertEqual(self.run_ctl("--role", "bottom", "--close-row", "ai-list", "5", "3", "s1"), "")   # 底部分頁列

    def test_close_row_skips_scrollbar_state_on_content_rows(self):
        # 審查 r2 M1:內容列也不碰捲軸狀態檔;殘留的 dragged 不能吃掉這次關席
        (self.st / "scrollbar-grab").write_text("1")
        (self.st / "scrollbar-dragged").write_text("1")
        out = self.run_ctl("--role", "right", "--close-row", "ai-list", "5", "3", "s1", "--expect-window", "@35")
        self.assertIn("--release-confirmed @35", out)
        self.assertTrue((self.st / "scrollbar-grab").exists())
        self.assertTrue((self.st / "scrollbar-dragged").exists())

    def test_close_row_refuses_when_row_changed_or_no_window_given(self):
        # 審查 r2 S1:選單開著時右欄重排,同一行換成別席 ⇒ 不彈確認
        for extra in ([], ["--expect-window", "@99"], ["--expect-window"],
                      ["--expect-window", "@3"], ["--expect-window", "@350"]):         # 不是前綴比對
            out = self.run_ctl("--role", "right", "--close-row", "ai-list", "5", "3", "s1", *extra)
            self.assertNotIn("display-menu", out, extra)

    def test_expect_window_without_value_keeps_dry_run(self):
        # 審查 r3(M5/M6):--expect-window 沒接值時不能把 --dry-run 當成窗 id 吃掉
        log = self.fake_tmux()
        self.run_ctl("--role", "right", "--close-row", "ai-list", "5", "3", "s1", "--expect-window", "--dry-run",
                     dry_last=False)
        self.assertFalse(log.exists() and log.read_text().strip())

    def test_row_number_limited_to_four_digits(self):
        # 審查 r3(M10–M12):第 12345 行就算有 [X] 也不收(座標只收 1–4 位數)
        (self.st / "ai-rowmap.json").write_text(json.dumps([dict(IDLE, line=12345), dict(IDLE, line=1234)],
                                                           ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.run_ctl("--role", "right", "--close-row", "ai-list", "5", "12345", "s1",
                                      "--expect-window", "@35"), "")
        self.assertIn("--release-confirmed @35", self.run_ctl("--role", "right", "--close-row", "ai-list", "5",
                                                             "1234", "s1", "--expect-window", "@35"))

    def test_context_reads_the_clicked_cabins_rowmap(self):
        # 審查 r3 M1:多艙時右鍵選單要讀被點那一艙的 rowmap(run-shell 環境是第一艙的)
        st_b = self.tmp / "stB"
        st_b.mkdir()
        (st_b / "ai-rowmap.json").write_text(json.dumps([dict(IDLE, window="@40")], ensure_ascii=False),
                                             encoding="utf-8")
        log = self.fake_tmux({"sB": st_b})
        self.run_ctl("--context", "pane", "ai-list", "5", "3", "%1", "sB", dry_last=False)
        menu = log.read_text()
        self.assertIn("--close-row ai-list 5 3 sB --expect-window @40", menu)
        self.assertNotIn("@35", menu)
        log.unlink()
        self.run_ctl("--context", "pane", "ai-list", "5", "3", "%1", "sX", dry_last=False)   # 取不到艙身份
        menu = log.read_text() if log.exists() else ""
        self.assertNotIn("--close-row", menu)
        # 第 4 輪審查:只是少了關閉項,選單本身與原本幾項照舊(跟 main 一樣讀 env 那一艙)
        for item in ("display-menu", "detail-card.py", "--open-window @35", "--new-ai-menu", "--theme-menu"):
            self.assertIn(item, menu, item)

    def test_three_args_without_session_keep_dry_run(self):
        # 審查 r2 A1:給了三格、沒給 session 時,--dry-run 不能被當成 session 吃掉
        log = self.fake_tmux()
        self.run_ctl("--role", "right", "--close-row", "ai-list", "5", "3", "--dry-run", dry_last=False)
        self.assertFalse(log.exists() and log.read_text().strip())

    def test_first_content_row_can_close(self):
        # 審查 r2 B5:第一個內容列(y=2)也關得掉
        (self.st / "ai-rowmap.json").write_text(json.dumps([dict(IDLE, line=2)], ensure_ascii=False), encoding="utf-8")
        out = self.run_ctl("--role", "right", "--close-row", "ai-list", "5", "2", "s1", "--expect-window", "@35")
        self.assertIn("--release-confirmed @35", out)
        self.assertIn("--close-row ai-list 5 2 s1 --expect-window @35",
                      self.run_ctl("--context", "pane", "ai-list", "5", "2", "%1", "s1"))

    def test_real_menu_carries_session_and_window(self):
        # 審查 r2 C9 / C11:真跑那份選單也有這一項,回呼帶 session(多艙不重導錯艙)與窗 id
        log = self.fake_tmux({"s1": self.st})
        self.run_ctl("--context", "pane", "ai-list", "5", "3", "%1", "s1", dry_last=False)
        self.assertIn("--close-row ai-list 5 3 s1 --expect-window @35", log.read_text())

    def test_short_args_keep_dry_run(self):
        # 審查 r1 M2:參數短少時 --dry-run 不能被當成角色 / 座標吃掉(吃掉就會去碰真 tmux)
        log = self.fake_tmux()
        for args in ([], ["ai-list"], ["ai-list", "5"]):
            self.run_ctl("--role", "right", "--close-row", *args, "--dry-run", dry_last=False)
        self.assertFalse(log.exists() and log.read_text().strip(), log.read_text() if log.exists() else "")

    def test_plain_click_unchanged(self):
        # 審查 r1 M10:--click 預設意圖仍是 click:點列中間跳窗、點 [X] 格彈確認
        mid = self.run_ctl("--role", "right", "--click", "ai-list", "5", "3", "s1")
        self.assertIn("select-window -t aihub:@35", mid)
        self.assertNotIn("display-menu", mid)
        x = self.run_ctl("--role", "right", "--click", "ai-list", "21", "3", "s1")
        self.assertIn("--release-confirmed @35", x)

    def test_no_close_item_without_window_or_on_title_rows(self):
        # 審查 r1 M13:detail 拿不到 window ⇒ 不給「關閉這一席」;非數字座標不嵌進 run-shell
        (self.st / "ai-rowmap.json").write_text(json.dumps([dict(IDLE, detail={"label": ""})], ensure_ascii=False),
                                                encoding="utf-8")
        out = self.run_ctl("--context", "pane", "ai-list", "5", "3", "%1", "s1")
        self.assertNotIn("--close-row", out)
        (self.st / "ai-rowmap.json").write_text(json.dumps([IDLE], ensure_ascii=False), encoding="utf-8")
        out = self.run_ctl("--context", "pane", "ai-list", "5' ; run-shell 'x", "3", "%1", "s1")
        close_item = out.split(self.label(), 1)[1]                         # 只看新加的那一項
        self.assertIn("--close-row ai-list 0 3 ", close_item)
        self.assertNotIn("run-shell 'x", close_item)

    def test_close_seat_label_in_every_language(self):
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8"))
            self.assertTrue(d.get("aib.context.menu.close_seat"), lang)

    def test_no_bash4_only_syntax(self):
        src = (MOUSE / "mouse-ctl.sh").read_text(encoding="utf-8")
        self.assertNotIn(";&\n", src)                                       # Mac 內建 bash 3.2 不認
        if os.path.exists("/bin/bash"):
            r = subprocess.run(["/bin/bash", "-n", str(MOUSE / "mouse-ctl.sh")], capture_output=True, text=True,
                               timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
