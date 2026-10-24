#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""右欄拍間少開行程(10-24 @044e0 本機效能):有人看的右欄以前每秒約 13 支 tmux 來自拍間輪詢。
1. 每拍(0.1 秒)問一次 hub 的 #{pane_activity}:tmux 沒有這個格式,永遠空,從沒生效 ⇒ 拿掉。
2. 每 3 拍問一次 hub 在哪一窗 ⇒ 每幀問窗號時同一支 tmux 順便在 hub 掛 session-window-changed 鉤子,
   切窗時 hub 自己寫訊號檔;拍間只讀檔,保底每 30 拍再問一次;鉤子掛不上就照舊每 3 拍問。
從 pane-right-tabs.sh 截出那幾段,用真的 bash(與有裝的話真的 tmux,私有 -L socket)驗。
H1 真 tmux:掛上鉤子(HUBWIN_HOOKED=1)、拿到窗號;切窗 / 關窗後訊號檔變成新窗號
H2 訊號檔路徑有 tmux 會解析的字元 / 不是絕對路徑 ⇒ 不掛鉤子、照舊拿得到窗號
H3 訊號檔寫不了(夾被刪)⇒ 切窗後 hub 那格不會被切進 view-mode 顯示錯誤
H4 鉤子在:60 拍內保底只問 2 次;訊號檔變了 ⇒ 當拍就中斷;鉤子不在:照舊每 3 拍問(20 次)
H5 set-hook 失敗(假 tmux 印窗號但 rc=1)⇒ HUB_WIN 照有、HUBWIN_HOOKED=0
H6 迴圈裡不再問 #{pane_activity};訊號基準先讀、再問窗號
刀(複本 + run_guarded):K1 訊號變了不中斷 ⇒ H4 紅;K2 有鉤子仍每 3 拍問 ⇒ H4 紅;
K3 拿掉寫檔防護 ⇒ H3 紅;K4 拿掉路徑白名單 ⇒ H2 紅;K5 掛不上也當掛上 ⇒ H5 紅。
刀一律斷言「FAIL: <目標>」且沒有 ERROR
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

SCRIPT = ROOT / "src" / "cockpit" / "pane-right-tabs.sh"
TICK = ROOT / "src" / "cockpit" / "pane-tick.sh"
BASH = os.environ.get("AIB_TEST_BASH") or shutil.which("bash") or "/bin/bash"
TMUX = shutil.which("tmux")


def probe_block():
    s = SCRIPT.read_text(encoding="utf-8")
    i = s.index("hub_active_window() {")
    j = s.index('FAST_REASON=""', i)
    return s[i:j]


def tick_block():
    s = SCRIPT.read_text(encoding="utf-8")
    i = s.index('    if [ -n "$HUB_WIN" ]; then\n      _hw_every=')
    end = "        [ \"$CURRENT_HUB_WIN\" = \"$HUB_WIN\" ] || break\n      fi\n    fi\n"
    j = s.index(end, i) + len(end)
    return s[i:j]


def loop_body():
    s = SCRIPT.read_text(encoding="utf-8")
    i = s.index("\nwhile true; do")
    return s[i:]


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.d), True)
        self.state = self.d / "cockpit"
        self.state.mkdir()

    def bash(self, prog, env_extra=None, timeout=30):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.d),
               "EZ2_COCKPIT_STATE_DIR": str(self.state)}
        env.update(env_extra or {})
        p = subprocess.run([BASH, "-c", prog], env=env, capture_output=True, text=True, timeout=timeout)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout


@unittest.skipUnless(TMUX, "沒有 tmux")
class RealTmux(Base):
    def setUp(self):
        super().setUp()
        self.sock = "rtick%d" % os.getpid()
        self.tmx = self.d / "tmx"              # 自己的 socket 夾:起伺服器與被測的 bash 都用它
        self.tmx.mkdir()
        self.tm("-f", "/dev/null", "new-session", "-d", "-s", "h", "-n", "a", "sleep 120",
                ";", "new-window", "-d", "-n", "b", "sleep 120")
        self.addCleanup(self.tm, "kill-server")

    def tm(self, *a):
        env = dict(os.environ)
        env.pop("TMUX", None)
        env["TMUX_TMPDIR"] = str(self.tmx)
        return subprocess.run([TMUX, "-L", self.sock] + list(a), env=env, capture_output=True,
                              text=True, timeout=10)

    def probe(self, state=None):
        st = state or str(self.state)
        out = self.bash(". '%s'\n%s\nhub_window_probe\n"
                        "echo \"$HUB_WIN $HUBWIN_HOOKED\"\n" % (TICK, probe_block()),
                        {"EZ2_AIHUB_SOCKET": self.sock, "EZ2_COCKPIT_STATE_DIR": st,
                         "TMUX_TMPDIR": str(self.tmx)})
        return out.split()

    def wait_sig(self, path, want, secs=3.0):
        end = time.time() + secs
        got = None
        while time.time() < end:
            try:
                got = Path(path).read_text().strip()
            except OSError:
                got = None
            if got == want:
                return got
            time.sleep(0.05)
        return got

    def test_h1_hook_signals_switch(self):
        win, hooked = self.probe()
        self.assertEqual(hooked, "1")
        self.assertTrue(win.startswith("@"), win)
        sig = self.state / ".hubwin.sig"
        b = self.tm("display", "-p", "-t", "h:b", "#{window_id}").stdout.strip()
        self.tm("select-window", "-t", "h:b")
        self.assertEqual(self.wait_sig(sig, b), b)
        self.tm("kill-window", "-t", "h:b")
        a = self.tm("display", "-p", "-t", "h:a", "#{window_id}").stdout.strip()
        self.assertEqual(self.wait_sig(sig, a), a)
        # 每幀重掛同一個索引,不會疊出好幾個鉤子
        self.probe()
        hooks = [ln for ln in self.tm("show-hooks", "-g").stdout.splitlines()
                 if "window-changed" in ln and "run-shell" in ln]
        self.assertEqual(len(hooks), 1, hooks)

    def test_h2_unsafe_path_no_hook(self):
        for st in (str(self.d / "has space"), str(self.d / "x$HOME"), str(self.d / "q'q"), "rel/dir"):
            with self.subTest(st):
                win, hooked = self.probe(st)
                self.assertEqual(hooked, "0")
                self.assertTrue(win.startswith("@"))
                hooks = [ln for ln in self.tm("show-hooks", "-g").stdout.splitlines()
                         if "window-changed" in ln and "run-shell" in ln]
                self.assertEqual(hooks, [])

    def test_h3_unwritable_sig_stays_quiet(self):
        gone = self.d / "gone"
        gone.mkdir()
        _, hooked = self.probe(str(gone))
        self.assertEqual(hooked, "1")
        gone.rmdir()
        self.tm("select-window", "-t", "h:b")
        time.sleep(0.6)
        mode = self.tm("display", "-p", "-t", "h:b", "#{pane_in_mode}").stdout.strip()
        self.assertEqual(mode, "0", "寫不了訊號檔時 hub 被切進 view-mode")


class Ticks(Base):
    def run_ticks(self, hooked, change_at=None, n=60):
        cnt = self.d / "calls"
        sig = self.state / ".hubwin.sig"
        sig.write_text("@0\n")
        change = ('[ "$tick" = %d ] && echo @5 > "$HUBWIN_SIG"\n' % change_at) if change_at is not None else ""
        prog = (". '%s'\n%s\nhub_active_window() { echo x >> '%s'; echo @0; }\n"
                "HUB_WIN=@0\nHUBWIN_HOOKED=%d\npane_read_file HUBWIN_SIG0 \"$HUBWIN_SIG\"\n"
                "for ((tick=0; tick<%d; tick++)); do\n%s%s  done\necho \"end=$tick\"\n"
                % (TICK, probe_block(), cnt, hooked, n, change, tick_block()))
        out = self.bash(prog).strip()
        calls = len(cnt.read_text().splitlines()) if cnt.exists() else 0
        return out, calls

    def test_h4_hooked_polls_rarely(self):
        out, calls = self.run_ticks(1)
        self.assertEqual(out, "end=60")
        self.assertEqual(calls, 2)

    def test_h4_hooked_breaks_on_signal(self):
        out, calls = self.run_ticks(1, change_at=7)
        self.assertEqual(out, "end=7")
        self.assertEqual(calls, 0)

    def test_h4_unhooked_keeps_old_rate(self):
        out, calls = self.run_ticks(0)
        self.assertEqual(out, "end=60")
        self.assertEqual(calls, 20)

    def test_h5_sethook_failure_not_hooked(self):
        fake = self.d / "bin"
        fake.mkdir()
        (fake / "tmux").write_text("#!/bin/sh\necho @3\nexit 1\n")
        (fake / "tmux").chmod(0o755)
        out = self.bash(". '%s'\n%s\nhub_window_probe\necho \"$HUB_WIN $HUBWIN_HOOKED\"\n" % (TICK, probe_block()),
                        {"PATH": "%s:%s" % (fake, os.environ.get("PATH", "/usr/bin:/bin")),
                         "EZ2_AIHUB_SOCKET": "x"})
        self.assertEqual(out.split(), ["@3", "0"])

    def test_h6_static(self):
        body = loop_body()
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        self.assertNotIn("pane_activity", code)
        self.assertLess(code.index('pane_read_file HUBWIN_SIG0 "$HUBWIN_SIG"'), code.index("hub_window_probe"))
        self.assertEqual(code.count("hub_window_probe"), 1)


KNIVES = [
    ("K1", '        [ "$HUBWIN_NOW" = "$HUBWIN_SIG0" ] || break\n', "        :\n",
     "Ticks.test_h4_hooked_breaks_on_signal"),
    ("K2", '        _hw_every="$HUBWIN_SAFETY_TICKS"\n', '        _hw_every="$HUBWIN_TICKS"\n',
     "Ticks.test_h4_hooked_polls_rarely"),
    ("K3", """echo '#{window_id}' 2>/dev/null >'$HUBWIN_SIG' || :""", """echo '#{window_id}' >'$HUBWIN_SIG'""",
     "RealTmux.test_h3_unwritable_sig_stays_quiet"),
    ("K4", "    ''|[!/]*|*[!A-Za-z0-9._/+-]*) HUB_WIN", "    '') HUB_WIN", "RealTmux.test_h2_unsafe_path_no_hook"),
    ("K5", '2>/dev/null)"; then\n    HUBWIN_HOOKED=1\n  fi\n',
     '2>/dev/null)"; then\n    :\n  fi\n  HUBWIN_HOOKED=1\n', "Ticks.test_h5_sethook_failure_not_hooked"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, needle, repl, target in KNIVES:
            if target.startswith("RealTmux.") and not TMUX:
                continue
            with self.subTest(tag):
                with knife_copy(ROOT, "src/cockpit/pane-right-tabs.sh", needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=120)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
