#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""(x) 軟關入口按了確定要關得掉(10-24 @abb89,右鍵另開 AI 實測)。

事故:Andy 09-21 定「有 💤 就給 (x)、達門檻才給 [X]」,但只改了畫面;按 (x) → 確定後,
release-confirmed 照樣拿 300 秒門檻重驗 ⇒ 一定被拒,還誤報「該席已恢復活動」。
修法:(x) 帶 --soft-since <選單開出時間>,重驗門檻改成「選單開著的秒數」——
只擋選單開著期間又動起來的席(這道重驗本來的用意);[X] 不帶,照舊 300 秒。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT / "render"))
sys.path.insert(0, str(COCKPIT / "mouse"))

import render_lib  # noqa: E402
import hit_test  # noqa: E402

# 假探針:照 activity_probe.py 的 --threshold 語意回;把收到的參數記下來給測試看
FAKE_PROBE = r'''
import json, os, sys
a = sys.argv[1:]
open(os.environ["FAKE_PROBE_LOG"], "a").write(json.dumps(a) + "\n")
if "--sample-socket" in a:                       # 軟關的 CPU 取樣:FAKE_CPU = active / idle / unknown
    st = os.environ.get("FAKE_CPU", "unknown")
    print(json.dumps({"window": a[a.index("--window") + 1], "source": st, "killable": False, "idle_s": None}))
    sys.exit(0 if st != "unknown" else 3)
th = int(a[a.index("--threshold") + 1]) if "--threshold" in a else 300
idle = int(os.environ["FAKE_IDLE"])
ok = idle >= th
print(json.dumps({"window": a[a.index("--window") + 1], "provider": "codex", "idle_s": idle,
                  "threshold": th, "killable": ok, "source": "idle" if ok else "active"}))
sys.exit(0 if ok else 3)
'''


class SoftSpan(unittest.TestCase):
    def test_soft_label_marks_span(self):
        text = "  CDX dev1 💤 (x)"
        soft = render_lib._kill_span_at_end(text, label=render_lib.SOFT_KILL_LABEL)
        hard = render_lib._kill_span_at_end(text, label=render_lib.IDLE_KILL_LABEL)
        self.assertIs(soft.get("soft"), True)
        self.assertNotIn("soft", hard)

    def test_hit_test_carries_soft(self):
        for soft in (True, False):
            kill = {"start": 10, "end": 13}
            if soft:
                kill["soft"] = True
            rowmap = [{"line": 2, "window": "@35", "kill": kill,
                       "detail": {"label": "dev1@4fc4f", "status": "💤"}}]
            got = hit_test.hit_ai_rows(rowmap, 2, 11)
            self.assertEqual(got.get("action"), "kill-idle")
            self.assertIs(got.get("soft"), soft)


class ReleaseConfirmed(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="softx-"))
        self.ck = self.tmp / "src" / "cockpit"      # 照倉內相對位置放(mouse-ctl 往上找 VERSION)
        shutil.copytree(COCKPIT, self.ck, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(REPO / "VERSION", self.tmp / "VERSION")
        (self.ck / "activity_probe.py").write_text(FAKE_PROBE, encoding="utf-8")
        self.log = self.tmp / "probe.log"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_release(self, idle, *extra, cpu="unknown"):
        st = self.tmp / "state"
        st.mkdir(exist_ok=True)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp),
               "EZ2_AIB_STATE_ROOT": str(self.tmp), "EZ2_AIB_HOME": str(self.tmp),
               "EZ2_COCKPIT_STATE_DIR": str(st), "EZ2_PY3": sys.executable,
               "FAKE_IDLE": str(idle), "FAKE_PROBE_LOG": str(self.log), "FAKE_CPU": cpu}
        r = subprocess.run(["bash", str(self.ck / "mouse" / "mouse-ctl.sh"), "--release-confirmed",
                            "@35", "aib-test", *extra, "--dry-run"],
                           env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_soft_idle_since_menu_opened_is_released(self):
        out = self.run_release(45, "--soft-since", str(int(time.time()) - 30))
        self.assertIn("kill-window", out)
        th = [json.loads(ln) for ln in self.log.read_text().splitlines() if "--threshold" in ln][-1]
        self.assertIn("--threshold", th)
        self.assertLess(int(th[th.index("--threshold") + 1]), 300)

    def test_soft_active_after_menu_opened_is_refused(self):
        out = self.run_release(5, "--soft-since", str(int(time.time()) - 30))
        self.assertNotIn("kill-window", out)
        self.assertIn("refused", out)

    def test_soft_refused_while_cpu_busy(self):
        out = self.run_release(45, "--soft-since", str(int(time.time()) - 30), cpu="active")
        self.assertNotIn("kill-window", out)
        self.assertIn("還在跑", out)
        self.assertIn("/nonexistent/", self.log.read_text())     # dry-run 不碰真 tmux 的 socket
        out = self.run_release(45, "--soft-since", str(int(time.time()) - 30), cpu="idle")
        self.assertIn("kill-window", out)

    def test_soft_activity_in_same_second_is_refused(self):
        # 門檻至少 1 秒:選單開出同一秒內的活動(idle 0)也要擋
        out = self.run_release(0, "--soft-since", str(int(time.time())))
        self.assertNotIn("kill-window", out)

    def test_hard_x_keeps_300s_threshold(self):
        out = self.run_release(45)
        self.assertNotIn("kill-window", out)
        self.assertNotIn("--threshold", self.log.read_text())

    def test_bad_soft_since_falls_back_to_hard(self):
        for bad in ("abc", "0999999999", str(int(time.time()) + 3600)):   # 非數字 / 前導零 / 未來
            out = self.run_release(45, "--soft-since", bad)
            self.assertNotIn("kill-window", out, bad)

    def test_soft_since_without_value_keeps_next_flag(self):
        # 沒接值時不能把 --dry-run 吃掉(吃掉就走真路徑)
        out = self.run_release(45, "--soft-since")
        self.assertIn("refused", out)

    def test_confirm_menu_carries_soft_since_only_for_soft(self):
        # 審查第 1 位:選單端那一行(kill-idle 組 CONFIRM_ACTION)要有測試守
        st = self.tmp / "state"
        st.mkdir(exist_ok=True)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp),
               "EZ2_AIB_STATE_ROOT": str(self.tmp), "EZ2_COCKPIT_STATE_DIR": str(st),
               "EZ2_PY3": sys.executable}
        for soft in (True, False):
            kill = {"start": 10, "end": 13}
            if soft:
                kill["soft"] = True
            (st / "ai-rowmap.json").write_text(json.dumps([{"line": 5, "window": "@35", "kill": kill,
                                                            "detail": {"label": "dev1@4fc4f", "status": "z"}}]))
            r = subprocess.run(["bash", str(self.ck / "mouse" / "mouse-ctl.sh"), "--click", "ai-list", "11", "5",
                                "aib-test", "--role", "right", "--dry-run"],
                               env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("--release-confirmed @35", r.stdout)
            self.assertEqual("--soft-since" in r.stdout, soft, r.stdout[-400:])


if __name__ == "__main__":
    unittest.main()
