#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib handoff collect 要數得到「這一席自己用背景殼起的長迴圈」。

病:10-05 02:17 那份交接的機器事實寫「背景掃描/測試 0 支」,實際有兩支在跑(盯 v0.70.1 標籤、排隊等租約)。
  舊規則只認命令列含 "worktrees/"(Pro 的資料夾形狀)又刻意跳過殼,而 Claude 的背景工作正是 `zsh -c '… eval …'`。
修(src/cockpit/aib_handoff.py):從席位檔記的 AI 行程往下找子孫,跑超過一分鐘的 `-c` 殼記成 kind=seat-child,
  顯示 eval 裡真正的指令;跑 collect 的那條殼鏈、Claude 的 caffeinate、不到一分鐘的不算。
格(餵假的 ps 表,直接呼叫函式;不碰真行程):
  B1 入口格:AI 行程底下一支 2 分鐘的背景殼 ⇒ 記一筆,指令是 eval 裡那段(zsh 跳脫的 '"'"' 還原成 ')。
  B2 對照:同一棵樹裡 caffeinate、10 秒的殼、跑 collect 的那條殼鏈、別人(不在 AI 行程底下)的長殼 ⇒ 都不記。
  B3 對照:席位沒記 pid / pid 不在表裡 ⇒ 什麼都不加、不崩。
  B4 etime 解析:mm:ss、hh:mm:ss、d-hh:mm:ss;讀不懂 ⇒ None。
  B5 接線:_collect 有呼叫 _seat_children(facts, procs, rec.get("pid"), me)(字面)。
刀:K1 把 SEAT_CHILD_MIN_S 改成很大 ⇒ B1 紅。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "bootstrap"))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
import aib_handoff as H  # noqa: E402

SNAP = "/bin/zsh -c source /Users/x/.claude/shell-snapshots/snapshot-zsh-1.sh 2>/dev/null || true && eval '%s' < /dev/null && pwd -P >| /tmp/cwd"
LOOP = "cd ~/demo-repo; for i in $(seq 1 144); do git ls-remote --tags github '\"'\"'refs/tags/v0.70.1'\"'\"'; sleep 300; done"


def procs():
    return {
        "100": ("1", "17:00:00", "zsh -c env claude"),                 # 席位檔記的 pid(外層殼)
        "101": ("100", "17:00:00", "/Users/x/.local/bin/claude --session-id u"),
        "200": ("101", "02:00", SNAP % LOOP),                          # 要記的背景殼
        "201": ("200", "00:50", "sleep 300"),
        "300": ("101", "05:00", "caffeinate -i -t 300"),               # Claude 自己的
        "400": ("101", "00:10", SNAP % "echo short"),                  # 不到一分鐘
        "500": ("101", "03:00", SNAP % "aib handoff collect"),          # 跑 collect 的殼鏈
        "501": ("500", "03:00", "python3 aib_handoff.py collect"),
        "900": ("1", "09:00", SNAP % "someone else's loop"),           # 不在這一席底下
    }


class SeatChildren(unittest.TestCase):
    def test_B1_entry_long_background_shell_is_listed_with_its_payload(self):
        facts = {"background": []}
        H._seat_children(facts, procs(), "100", "501")
        pids = [b["pid"] for b in facts["background"]]
        self.assertIn("200", pids, facts)
        b = [x for x in facts["background"] if x["pid"] == "200"][0]
        self.assertEqual(b["kind"], "seat-child")
        self.assertIn("for i in $(seq 1 144)", b["cmd"])
        self.assertIn("'refs/tags/v0.70.1'", b["cmd"], "zsh 跳脫沒還原: " + b["cmd"])
        self.assertNotIn("shell-snapshots", b["cmd"])

    def test_B2_control_noise_is_not_listed(self):
        facts = {"background": []}
        H._seat_children(facts, procs(), "100", "501")
        self.assertEqual([b["pid"] for b in facts["background"]], ["200"], facts)

    def test_B3_control_missing_root_adds_nothing(self):
        for root in (None, "", "777"):
            facts = {"background": []}
            H._seat_children(facts, procs(), root, "501")
            self.assertEqual(facts["background"], [], root)

    def test_B4_etime_parsing(self):
        self.assertEqual(H._etime_s("03:48"), 228)
        self.assertEqual(H._etime_s("11:55:35"), 42935)
        self.assertEqual(H._etime_s("1-02:03:04"), 93784)
        self.assertIsNone(H._etime_s("x"))

    def test_B5_collect_calls_it(self):
        text = (ROOT / "src" / "cockpit" / "aib_handoff.py").read_text(encoding="utf-8")
        self.assertIn('_seat_children(facts, procs, rec.get("pid"), me)', text)


class SeatChildrenKnife(unittest.TestCase):
    @unittest.skipIf(os.environ.get("AIB_SEATCHILD_KNIFE_INNER") == "1", "inner")
    def test_K1_huge_threshold_reds_B1(self):
        needle = "SEAT_CHILD_MIN_S = 60\n"
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "tree"
            shutil.copytree(ROOT / "src", copy / "src", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(ROOT / "i18n", copy / "i18n")
            (copy / "tests").mkdir()
            shutil.copy2(Path(__file__), copy / "tests" / Path(__file__).name)
            f = copy / "src" / "cockpit" / "aib_handoff.py"
            text = f.read_text(encoding="utf-8")
            self.assertEqual(text.count(needle), 1, "刀錨要恰好一次")
            f.write_text(text.replace(needle, "SEAT_CHILD_MIN_S = 10 ** 9\n"), encoding="utf-8")
            env = dict(os.environ, AIB_SEATCHILD_KNIFE_INNER="1")
            r = subprocess.run([sys.executable, str(copy / "tests" / Path(__file__).name), "SeatChildren"],
                               capture_output=True, text=True, env=env, timeout=120, stdin=subprocess.DEVNULL)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", r.stderr, re.M))
            self.assertIn("test_B1_entry_long_background_shell_is_listed_with_its_payload", red, r.stderr[-1500:])


if __name__ == "__main__":
    unittest.main()
