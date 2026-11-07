#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""面板「看得見才畫」(11-07 @043dd,AIB 2.0 省熱七項第 2 項;艦長 @a63ab 裁示做成 pane-tick 共用函式)。

pane_attached 只看 session 有沒有人附著 ⇒ 人在同一艙看別的窗時,戰情室窗照樣每秒整幀渲染。
pane-tick.sh 新增 pane_visible / pane_wait_if_hidden:沒人附著或所在窗不是當前窗 ⇒ 不畫;
看不見時掛 session-window-changed / client-attached / client-detached 鉤子寫訊號檔,切回來約 0.1 秒就醒。

V1 真 tmux:沒 client ⇒ 兩窗都看不見;有 client 看 a ⇒ a 看得見、b 看不見;切到 b ⇒ 反過來
V2 真 tmux:b 看不見時等待中,切到 b ⇒ 3 秒內醒來(退避設 10 秒),而且鉤子有掛上
V3 量不到一律當看得見(誤判看不見會凍畫面):tmux 失敗 / 空 / 不是數字 / window_active 不是 0、1 ⇒ rc0
V4 訊號檔路徑有 tmux 會解析的字元 / 不是絕對路徑 ⇒ 不掛鉤子、照退避等完(不會卡住)
V5 戰情室主迴圈用 pane_wait_if_hidden(第一幀照畫),不再用只看附著的 pane_wait_if_detached
刀(複本 + run_guarded):K1 量不到當看不見 ⇒ V3 紅;K2 不看 window_active ⇒ V1 紅;
K3 不掛鉤子 ⇒ V2 紅;K4 拿掉路徑白名單 ⇒ V4 紅;K5 戰情室退回 pane_wait_if_detached ⇒ V5 紅。
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

TICK = ROOT / "src" / "cockpit" / "pane-tick.sh"
WARROOM = ROOT / "src" / "cockpit" / "warroom-board.sh"
BASH = os.environ.get("AIB_TEST_BASH") or shutil.which("bash") or "/bin/bash"
TMUX = shutil.which("tmux")


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.d), True)
        self.state = self.d / "cockpit"
        self.state.mkdir()

    def bash(self, prog, env_extra=None, timeout=40):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.d),
               "EZ2_COCKPIT_STATE_DIR": str(self.state)}
        env.update(env_extra or {})
        return subprocess.run([BASH, "-c", prog], env=env, capture_output=True, text=True, timeout=timeout)


@unittest.skipUnless(TMUX, "沒有 tmux")
class RealTmux(Base):
    def setUp(self):
        super().setUp()
        self.sock = "pvis%d" % os.getpid()
        self.tmx = self.d / "t"                # 自己的 socket 夾(路徑要短:socket 路徑有長度上限)
        self.tmx.mkdir()
        self.tm("-f", "/dev/null", "new-session", "-d", "-s", "h", "-n", "a", "-x", "80", "-y", "20",
                "sleep 120", ";", "new-window", "-d", "-n", "b", "sleep 120")
        self.addCleanup(self.tm, "kill-server")
        self.pa = self.tm("display", "-p", "-t", "h:a", "#{pane_id}").stdout.strip()
        self.pb = self.tm("display", "-p", "-t", "h:b", "#{pane_id}").stdout.strip()
        self.client = None

    def env(self):
        e = dict(os.environ)
        e.pop("TMUX", None)
        e["TMUX_TMPDIR"] = str(self.tmx)
        return e

    def tm(self, *a):
        return subprocess.run([TMUX, "-L", self.sock] + list(a), env=self.env(), capture_output=True,
                              text=True, timeout=10)

    def attach(self):
        # control-mode client 算一個附著的 client;stdin 留著不關它就一直掛著
        self.client = subprocess.Popen([TMUX, "-L", self.sock, "-C", "attach", "-t", "h"], env=self.env(),
                                       stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
        self.addCleanup(self._detach)
        end = time.time() + 5
        while time.time() < end:
            if self.tm("display", "-p", "-t", "h", "#{session_attached}").stdout.strip() not in ("", "0"):
                return
            time.sleep(0.05)
        self.fail("control-mode client 沒附著上")

    def _detach(self):
        if self.client:
            if self.client.poll() is None:
                self.client.kill()
                self.client.wait(5)
            self.client.stdin.close()

    def tick_env(self, pane, extra=None):
        # 被測的 bash 要打到同一台私有 tmux:用 TMUX 環境變數指 socket(tmux 從 TMUX 取 socket 路徑)
        sock_path = self.tm("display", "-p", "#{socket_path}").stdout.strip()
        e = {"TMUX": "%s,0,0" % sock_path, "TMUX_PANE": pane, "TMUX_TMPDIR": str(self.tmx)}
        e.update(extra or {})
        return e

    def visible(self, pane):
        return self.bash(". '%s'\npane_visible" % TICK, self.tick_env(pane)).returncode

    def test_v1_window_active_matters(self):
        self.assertEqual((self.visible(self.pa), self.visible(self.pb)), (1, 1), "沒 client 兩窗都該看不見")
        self.attach()
        self.assertEqual((self.visible(self.pa), self.visible(self.pb)), (0, 1), "看 a 時")
        self.tm("select-window", "-t", "h:b")
        self.assertEqual((self.visible(self.pa), self.visible(self.pb)), (1, 0), "切到 b 後")

    def test_v2_hook_wakes_on_switch(self):
        self.attach()
        prog = (". '%s'\npane_tick_init\nt0=$SECONDS\npane_wait_if_hidden; rc=$?\n"
                "echo \"rc=$rc hooked=$PANE_VIS_HOOKED waited=$((SECONDS - t0))\"\n" % TICK)
        env = self.tick_env(self.pb, {"EZ2_PANE_DETACHED_TICK": "10", "EZ2_PANE_DETACHED_TICK_MAX": "10"})
        full = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.d),
                "EZ2_COCKPIT_STATE_DIR": str(self.state)}
        full.update(env)
        p = subprocess.Popen([BASH, "-c", prog], env=full, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
        time.sleep(1.0)
        t0 = time.time()
        self.tm("select-window", "-t", "h:b")
        out, err = p.communicate(timeout=30)
        took = time.time() - t0
        self.assertIn("rc=0", out, err)
        self.assertIn("hooked=1", out, out + err)
        self.assertLess(took, 3.0, "切回來 %.1f 秒才醒(退避 10 秒;鉤子應約 0.1 秒叫醒)\n%s" % (took, out))

    def test_v4_unsafe_path_no_hook(self):
        self.attach()
        for st in (str(self.d / "has space"), str(self.d / "q'q"), "rel/dir"):
            with self.subTest(st):
                if st.startswith("/"):
                    Path(st).mkdir(exist_ok=True)
                prog = (". '%s'\npane_tick_init\npane_wait_if_hidden; rc=$?\n"
                        "echo \"rc=$rc hooked=$PANE_VIS_HOOKED\"\n" % TICK)
                p = self.bash(prog, self.tick_env(self.pb, {
                    "EZ2_COCKPIT_STATE_DIR": st, "EZ2_PANE_DETACHED_TICK": "1",
                    "EZ2_PANE_DETACHED_TICK_MAX": "1"}))
                self.assertIn("rc=0 hooked=0", p.stdout, p.stdout + p.stderr)
        hooks = self.tm("show-hooks", "-t", "h").stdout
        self.assertNotIn("panevis", hooks, hooks)


class Fallback(Base):
    """假 tmux:各種量不到的輸出 ⇒ pane_visible 都要回 rc0(照畫)。"""

    def fake(self, body):
        b = self.d / "bin"
        b.mkdir(exist_ok=True)
        f = b / "tmux"
        f.write_text("#!/bin/bash\n" + body + "\n")
        f.chmod(0o755)
        return {"PATH": "%s:/usr/bin:/bin" % b, "TMUX_PANE": "%9"}

    def test_v3_unknown_means_visible(self):
        cases = {
            "tmux 失敗": "exit 1",
            "空輸出": "exit 0",
            "不是數字": "echo 'x y'",
            "只有一個值": "echo 1",
            "window_active 怪值": "echo '1 2'",
            "附著數怪值": "echo 'a 1'",
        }
        for name, body in cases.items():
            with self.subTest(name):
                p = self.bash(". '%s'\npane_visible" % TICK, self.fake(body))
                self.assertEqual(p.returncode, 0, "%s 被當成看不見(會凍住畫面)" % name)
        # 對照:量得到的兩種看不見
        for name, body in {"沒人附著": "echo '0 1'", "不是當前窗": "echo '1 0'"}.items():
            with self.subTest(name):
                p = self.bash(". '%s'\npane_visible" % TICK, self.fake(body))
                self.assertEqual(p.returncode, 1, name)
        p = self.bash(". '%s'\npane_visible" % TICK, self.fake("echo '2 1'"))
        self.assertEqual(p.returncode, 0, "有人看當前窗")


class Static(unittest.TestCase):
    def test_v5_warroom_uses_hidden(self):
        s = WARROOM.read_text(encoding="utf-8")
        i = s.index("\nwhile true; do")
        loop = s[i:]
        self.assertIn('if [ "$FIRST_PAINT" != "1" ] && pane_wait_if_hidden; then continue; fi', loop)
        self.assertNotIn("pane_wait_if_detached", loop)


KNIVES = [
    ("K1", '  a="$(tmux display-message -p -t "$TMUX_PANE" \'#{session_attached} #{window_active}\' 2>/dev/null)" || return 0\n',
     '  a="$(tmux display-message -p -t "$TMUX_PANE" \'#{session_attached} #{window_active}\' 2>/dev/null)" || return 1\n',
     "src/cockpit/pane-tick.sh", "Fallback.test_v3_unknown_means_visible"),
    ("K2", '  [ "$w" = "0" ] && return 1\n  return 0\n}', '  return 0\n}',
     "src/cockpit/pane-tick.sh", "RealTmux.test_v1_window_active_matters"),
    ("K3", '    pane_vis_hook                     # 每次', '    :                     # 每次',
     "src/cockpit/pane-tick.sh", "RealTmux.test_v2_hook_wakes_on_switch"),
    ("K4", "    ''|[!/]*|*[!A-Za-z0-9._/+-]*) return 0 ;;\n  esac\n  local cmd=", "    '') return 0 ;;\n  esac\n  local cmd=",
     "src/cockpit/pane-tick.sh", "RealTmux.test_v4_unsafe_path_no_hook"),
    ("K5", '  if [ "$FIRST_PAINT" != "1" ] && pane_wait_if_hidden; then continue; fi\n',
     "  pane_wait_if_detached && continue\n",
     "src/cockpit/warroom-board.sh", "Static.test_v5_warroom_uses_hidden"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, needle, repl, rel, target in KNIVES:
            if target.startswith("RealTmux.") and not TMUX:
                continue
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=120)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
