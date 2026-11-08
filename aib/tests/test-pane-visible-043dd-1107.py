#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""面板「看得見才畫」(11-07 @043dd,AIB 2.0 省熱七項第 2 項;艦長 @a63ab 裁示做成 pane-tick 共用函式)。

pane_attached 只看 session 有沒有人附著 ⇒ 人在同一艙看別的窗時,戰情室窗照樣每秒整幀渲染。
pane-tick.sh:pane_visible(#{window_active_clients})/ pane_wait_if_hidden(事件序號 + 讀基準後重判)。

★ 沙盒(11-07 事故後的全席規則):所有 tmux 一律 `-S <本測試暫存夾內的 socket>`,每次呼叫前驗路徑在沙盒夾內
  且夾還在;不准用 TMUX_TMPDIR / -L(夾消失時 tmux 會退回預設 socket,清理的 kill-server 就會砍到真駕駛艙)。
  被測的 bash 拿 TMUX=<同一個 socket 路徑>(tmux 照它連,不會退回預設)。守門本身有測試 S1–S4。

V1 真 tmux:沒 client ⇒ 兩窗都看不見;client 看 a ⇒ a 見 b 不見;切到 b ⇒ 反過來
V2 等待中切到 b ⇒ 3 秒內醒(退避 10 秒),鉤子有掛
V2b 障礙:判看不見之後、掛鉤之前切回 ⇒ 不睡滿(讀基準後重判接住)
V2c 障礙:掛鉤之後、讀基準之前切回 ⇒ 不睡滿
V2d 等待中 b→a 來回切(最後狀態與基準相同)⇒ 仍醒來(事件序號,不是比內容)
V3 量不到一律當看得見:tmux 失敗 / 空 / 不是數字 ⇒ rc0
V4 訊號檔路徑有 tmux 會解析的字元 / 不是絕對路徑 ⇒ 不掛鉤子、照退避等完
V5 戰情室主迴圈用 pane_wait_if_hidden(第一幀照畫),不再用只看附著的 pane_wait_if_detached
V6 多 client / 連結窗 / grouped session 的可見性;V6b 連結窗在別的 session 被切到能叫醒;V6c grouped 那邊切窗能叫醒
V2e 真的重新附著(client-attached)⇒ 醒
V7 40 個寫入者同時 append ⇒ 事件數剛好 +40;V7b 超過 4096 位元組才歸零
V8 四種鉤子任一種那一格被別人佔 ⇒ 不掛、不蓋、狀態 hook_foreign、照退避等
V8b 障礙:兩艙都查完 show-hooks 才放行 set-hook ⇒ 內容相同(冪等),兩艙都掛上、同一次切窗兩邊都收到
V8c 一艙撤登 ⇒ 另一艙照收、撤登的不再收、共用鉤子不拔;V8d 懸空登記(艙夾已刪)⇒ 登記時清掉、鉤子不替它建檔
V8e(r5)障礙:外部在我們那支 tmux 之前插入四種任一 ⇒ 外部原值完整保留、hook_foreign(檢查與寫入同一條指令串)
V8f(r5)/x/a_b/c 與 /x/a/b_c 兩艙登記不撞名(sha256),兩邊都收到;V8g 清理改名那一刻原主重登 ⇒ 不刪
V8h 撤登只刪指向自己的;V9 4770 保留(docs/tmux-hook-indexes.md + 掃 src);V10 hook_foreign 與退回輪詢有計數
V10b 掛上後被外部蓋掉 ⇒ 等到逾時記 wake_timeout(不重查所有權);V10c 事件叫醒記 wake_event
S1–S5 沙盒守門:夾消失 / socket 指預設 / 沙盒外路徑 / 清理路徑 / 交給被測 bash 的 TMUX ⇒ 一律拒跑、tmux 一次都沒執行
刀(複本 + run_guarded):K1 量不到當看不見 ⇒ V3;K2 改回 session_attached ⇒ V1;K3 拿掉讀基準後重判 ⇒ V2c;
K4 拿掉路徑白名單 ⇒ V4;K5 戰情室退回 pane_wait_if_detached ⇒ V5;K6 鉤子改成覆寫(不是 append)⇒ V2d;
K7 鉤子內容改成各艙不同(直寫自己的訊號檔,r3 寫法)⇒ V8b;K10 拿掉讀回 ⇒ V8;K11 拿掉寫入條件 ⇒ V8e;
K12 登記名退回「/ 換 _」⇒ V8f;K13 清理不再確認 ⇒ V8g;K14 撤登不確認是自己的 ⇒ V8h;K15 不記退回輪詢 ⇒ V10;K16 不記逾時 ⇒ V10b;K17 不記事件喚醒 ⇒ V10c;
K8 守門不查夾在不在 ⇒ S1;K9 守門不擋預設 socket ⇒ S2。
(r4:一個全域廣播鉤子,固定下標 4770、內容逐字固定;各艙只登記 / 撤登 <狀態根>/state/pane-vis/reg/*.reg。
 鉤子掛全域 -g 而不是本 session:試過把它改回 -t 當刀,連結窗 / grouped 情境照樣醒,咬不到 ⇒ 不列刀;
 -g 留著是因為事件可能發生在別的 session,寧可多收一點、不會畫錯。)
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


from aib_tmux_sandbox import Sandbox, SandboxRefused  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pv"))
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
        self.sbx = Sandbox(self.d / "t", TMUX)
        (self.d / "t").mkdir()
        self.clients = []
        self.addCleanup(self.sbx.cleanup)
        self.addCleanup(self._detach_all)
        r = self.sbx.tm("-f", "/dev/null", "new-session", "-d", "-s", "h", "-n", "a", "-x", "80", "-y", "20",
                        "sleep 120", ";", "new-window", "-d", "-n", "b", "sleep 120")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.pa = self.sbx.tm("display", "-p", "-t", "h:a", "#{pane_id}").stdout.strip()
        self.pb = self.sbx.tm("display", "-p", "-t", "h:b", "#{pane_id}").stdout.strip()

    def attach(self, session="h"):
        # control-mode client 算一個附著的 client;stdin 留著不關它就一直掛著
        c = self.sbx.popen("-C", "attach", "-t", session)
        self.clients.append(c)
        end = time.time() + 5
        while time.time() < end:
            if self.sbx.tm("display", "-p", "-t", session, "#{session_attached}").stdout.strip() not in ("", "0"):
                return c
            time.sleep(0.05)
        self.fail("control-mode client 沒附著上")

    def detach(self, c):
        if c.poll() is None:
            c.kill()
            c.wait(5)
        c.stdin.close()

    def _detach_all(self):
        for c in self.clients:
            self.detach(c)

    def tick_env(self, pane, extra=None):
        # 被測的 bash 用裸 tmux,靠 TMUX 指 socket:交出去之前同樣過守門(夾在、socket 在夾內、不是預設)
        self.sbx.check()
        e = {"TMUX": "%s,0,0" % self.sbx.sock, "TMUX_PANE": pane}
        e.update(extra or {})
        return e

    def visible(self, pane):
        return self.bash(". '%s'\npane_visible" % TICK, self.tick_env(pane)).returncode

    def wait_hidden(self, pane, prelude="", action=None, at=1.0, backoff="10"):
        """在背景跑 pane_wait_if_hidden;at 秒後做 action;回(stdout, 從 action 到結束的秒數)。"""
        prog = (". '%s'\npane_tick_init\n%s\nt0=$SECONDS\npane_wait_if_hidden; rc=$?\n"
                "echo \"rc=$rc hooked=$PANE_VIS_HOOKED\"\n" % (TICK, prelude))
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.d),
               "EZ2_COCKPIT_STATE_DIR": str(self.state)}
        env.update(self.tick_env(pane, {"EZ2_PANE_DETACHED_TICK": backoff, "EZ2_PANE_DETACHED_TICK_MAX": backoff}))
        p = subprocess.Popen([BASH, "-c", prog], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(at)
        t0 = time.time()
        if action:
            action()
        out, err = p.communicate(timeout=40)
        return out + err, time.time() - t0

    def sel(self, target):
        return lambda: self.sbx.tm("select-window", "-t", target)

    def test_v1_window_active_matters(self):
        self.assertEqual((self.visible(self.pa), self.visible(self.pb)), (1, 1), "沒 client 兩窗都該看不見")
        self.attach()
        self.assertEqual((self.visible(self.pa), self.visible(self.pb)), (0, 1), "看 a 時")
        self.sbx.tm("select-window", "-t", "h:b")
        self.assertEqual((self.visible(self.pa), self.visible(self.pb)), (1, 0), "切到 b 後")

    def test_v2_hook_wakes_on_switch(self):
        self.attach()
        out, took = self.wait_hidden(self.pb, action=self.sel("h:b"))
        self.assertIn("hooked=1", out, out)
        self.assertLess(took, 3.0, "切回來 %.1f 秒才醒(退避 10 秒)\n%s" % (took, out))

    def _barrier(self, before_hook):
        """障礙:把 pane_vis_hook 包一層,在掛鉤之前(或之後、讀基準之前)切到 b。"""
        sw = "tmux select-window -t h:b"
        if before_hook:
            wrap = "%s; _orig_vis_hook" % sw
        else:
            wrap = "_orig_vis_hook; %s; sleep 0.5" % sw     # 等鉤子把序號寫完,再讓函式去讀基準
        return ('eval "$(declare -f pane_vis_hook | sed \'1s/pane_vis_hook/_orig_vis_hook/\')"\n'
                "pane_vis_hook() { %s; }\n" % wrap)

    def test_v2b_switch_before_hook(self):
        self.attach()
        out, took = self.wait_hidden(self.pb, prelude=self._barrier(True), at=0)
        self.assertIn("rc=1", out, "切回後應判看得見、不睡\n" + out)
        self.assertLess(took, 3.0, "掛鉤前切回,睡了 %.1f 秒\n%s" % (took, out))

    def test_v2c_switch_after_hook_before_baseline(self):
        self.attach()
        out, took = self.wait_hidden(self.pb, prelude=self._barrier(False), at=0)
        self.assertIn("rc=1", out, "讀基準後重判應看見\n" + out)
        self.assertLess(took, 3.0, "掛鉤後、讀基準前切回,睡了 %.1f 秒\n%s" % (took, out))

    def test_v2d_back_and_forth_still_wakes(self):
        self.attach()
        (self.state / ".panevis.ev").write_text(".")    # 已有 1 筆事件(不是空檔):醒來只能靠「變大」

        def flip():
            self.sbx.tm("select-window", "-t", "h:b", ";", "select-window", "-t", "h:a")
        out, took = self.wait_hidden(self.pb, action=flip)
        self.assertIn("rc=0", out, out)
        self.assertLess(took, 3.0, "b→a 來回切沒叫醒(%.1f 秒)\n%s" % (took, out))

    def test_v2e_reattach_wakes(self):
        # 真的 client-attached:沒人附著時等待,中途附著 ⇒ 3 秒內醒(退避 10 秒)
        out, took = self.wait_hidden(self.pa, action=lambda: self.attach())
        self.assertIn("hooked=1", out, out)
        self.assertLess(took, 4.0, "重新附著 %.1f 秒才醒\n%s" % (took, out))

    HOOKS = ("session-window-changed", "client-attached", "client-detached", "client-session-changed")
    IDX = 4770

    def slot(self, hook):
        out = self.sbx.tm("show-hooks", "-g", hook).stdout
        for ln in out.splitlines():
            if ln.startswith("%s[%d] " % (hook, self.IDX)):
                return ln.split(" ", 1)[1]
        return ""

    def test_v8_foreign_slot_not_taken(self):
        # 四種鉤子任一種那一格被別人佔 ⇒ 不掛、不蓋、狀態 hook_foreign、照退避等
        foreign = 'run-shell -b "true # 別人的"'
        for h in self.HOOKS:
            with self.subTest(h):
                for x in self.HOOKS:
                    self.sbx.tm("set-hook", "-gu", "%s[%d]" % (x, self.IDX))
                self.sbx.tm("set-hook", "-g", "%s[%d]" % (h, self.IDX), foreign)
                before = self.slot(h)
                p = self.bash(". '%s'\npane_tick_init\npane_wait_if_hidden; echo \"hooked=$PANE_VIS_HOOKED\"" % TICK,
                              self.tick_env(self.pb, {"EZ2_PANE_DETACHED_TICK": "1", "EZ2_PANE_DETACHED_TICK_MAX": "1"}))
                self.assertIn("hooked=0", p.stdout, p.stdout + p.stderr)
                self.assertEqual((self.state / ".panevis.state").read_text().strip(), "hook_foreign")
                self.assertEqual(self.slot(h), before, "別人的鉤子被蓋掉")
                for x in self.HOOKS:
                    if x != h:
                        self.assertEqual(self.slot(x), "", "被佔時不該先掛別種:%s" % x)

    def cabin(self, name):
        st = self.d / name
        st.mkdir(exist_ok=True)
        return st

    def test_v8b_two_cabins_race_idempotent(self):
        # 障礙:兩艙都查完 show-hooks(都看到空格)才放行 set-hook ⇒ 冪等:兩艙都掛上、同一次切窗兩邊都收到
        self.attach()
        bar = self.d / "bar"
        bar.mkdir()
        # r5:檢查 + 寫入在同一支 tmux(含 if-shell)⇒ 障礙包在那一支前面:兩艙都走到才一起放行
        prog = (". '%s'\n"
                "tmux() { case \"$*\" in *if-shell*) : > '%s/'$ME; n=0;"
                " while [ ! -e '%s/'$OTHER ] && [ $n -lt 100 ]; do sleep 0.05; n=$((n+1)); done ;; esac; command tmux \"$@\"; }\n"
                "pane_vis_hook; echo \"hooked=$PANE_VIS_HOOKED\"\n" % (TICK, bar, bar))
        procs = []
        for me, other in (("A", "B"), ("B", "A")):
            st = self.cabin("cab" + me)
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.d),
                   "EZ2_COCKPIT_STATE_DIR": str(st), "ME": me, "OTHER": other}
            env.update(self.tick_env(self.pb))
            procs.append(subprocess.Popen([BASH, "-c", prog], env=env, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True))
        outs = [p.communicate(timeout=30)[0] for p in procs]
        self.assertTrue((bar / "A").exists() and (bar / "B").exists(), "障礙沒走到掛鉤那一支 tmux")
        self.assertEqual([o.strip() for o in outs], ["hooked=1", "hooked=1"], outs)
        sigs = [self.d / "cabA" / ".panevis.ev", self.d / "cabB" / ".panevis.ev"]
        size0 = [f.stat().st_size for f in sigs]
        self.sbx.tm("select-window", "-t", "h:b")
        end = time.time() + 3
        while time.time() < end and any(f.stat().st_size == z for f, z in zip(sigs, size0)):
            time.sleep(0.05)
        self.assertEqual([f.stat().st_size > z for f, z in zip(sigs, size0)], [True, True], "有一艙沒收到事件")

    def test_v8c_unregister_other_still_hears(self):
        self.attach()
        for name in ("cabA", "cabB"):
            st = self.cabin(name)
            p = self.bash(". '%s'\npane_vis_hook; echo \"hooked=$PANE_VIS_HOOKED\"" % TICK,
                          self.tick_env(self.pb, {"EZ2_COCKPIT_STATE_DIR": str(st)}))
            self.assertIn("hooked=1", p.stdout, p.stdout + p.stderr)
        p = self.bash(". '%s'\npane_vis_unregister" % TICK, {"EZ2_COCKPIT_STATE_DIR": str(self.d / "cabA")})
        self.assertEqual(p.returncode, 0, p.stderr)
        a, b = self.d / "cabA" / ".panevis.ev", self.d / "cabB" / ".panevis.ev"
        a0, b0 = a.stat().st_size, b.stat().st_size
        self.sbx.tm("select-window", "-t", "h:b")
        end = time.time() + 3
        while time.time() < end and b.stat().st_size == b0:
            time.sleep(0.05)
        time.sleep(0.3)
        self.assertGreater(b.stat().st_size, b0, "撤掉 A 之後 B 收不到")
        self.assertEqual(a.stat().st_size, a0, "撤登的 A 還在收")
        self.assertNotEqual(self.slot("session-window-changed"), "", "撤登不該拔掉共用鉤子")

    def test_v8d_dangling_registration_cleaned(self):
        reg = self.d / ".ez2-aib" / "state" / "pane-vis" / "reg"
        reg.mkdir(parents=True)
        dead = reg / "_gone_.panevis.ev.reg"
        os.symlink(str(self.d / "gone" / ".panevis.ev"), str(dead))
        self.attach()
        p = self.bash(". '%s'\npane_vis_hook; echo \"hooked=$PANE_VIS_HOOKED\"" % TICK, self.tick_env(self.pb))
        self.assertIn("hooked=1", p.stdout, p.stdout + p.stderr)
        self.assertFalse(os.path.lexists(str(dead)), "懸空的登記沒清")
        self.assertFalse((self.d / "gone").exists(), "鉤子不該替懸空登記建檔")

    def _inject_before_hook_call(self, hook, content):
        """障礙:在 pane_vis_hook 那一支(含 if-shell)tmux 之前,外部程式寫入 hook 的 4770 格。"""
        return ("tmux() { case \"$*\" in *if-shell*) command tmux set-hook -g '%s[%d]' '%s' ;; esac; "
                "command tmux \"$@\"; }\n" % (hook, self.IDX, content))

    def test_v8e_foreign_inserted_before_write_survives(self):
        # 外部(不知道保留表)在我們查與寫之間插入:檢查與寫入同一條指令串 ⇒ 外部原值完整保留、hook_foreign
        foreign = "run-shell -b true"
        for h in self.HOOKS:
            with self.subTest(h):
                for x in self.HOOKS:
                    self.sbx.tm("set-hook", "-gu", "%s[%d]" % (x, self.IDX))
                p = self.bash(". '%s'\n%spane_vis_hook; echo \"hooked=$PANE_VIS_HOOKED\"" % (
                    TICK, self._inject_before_hook_call(h, foreign)), self.tick_env(self.pb))
                self.assertIn("hooked=0", p.stdout, p.stdout + p.stderr)
                self.assertEqual(self.slot(h), "run-shell -b true", "外部插入的 hook 被蓋掉了")
                self.assertEqual((self.state / ".panevis.state").read_text().strip(), "hook_foreign")

    def test_v8f_registration_names_do_not_collide(self):
        # /x/a_b/c 與 /x/a/b_c:舊的「/ 換 _」會同名;sha256 不會
        self.attach()
        dirs = [self.d / "a_b" / "c", self.d / "a" / "b_c"]
        for st in dirs:
            st.mkdir(parents=True)
            p = self.bash(". '%s'\npane_vis_hook; echo \"hooked=$PANE_VIS_HOOKED\"" % TICK,
                          self.tick_env(self.pb, {"EZ2_COCKPIT_STATE_DIR": str(st)}))
            self.assertIn("hooked=1", p.stdout, p.stdout + p.stderr)
        sigs = [st / ".panevis.ev" for st in dirs]
        z = [f.stat().st_size for f in sigs]
        self.sbx.tm("select-window", "-t", "h:b")
        end = time.time() + 3
        while time.time() < end and any(f.stat().st_size == v for f, v in zip(sigs, z)):
            time.sleep(0.05)
        self.assertEqual([f.stat().st_size > v for f, v in zip(sigs, z)], [True, True], "撞名:有一艙收不到")

    def test_v8g_gc_vs_reregister_interleave(self):
        # 障礙:清理把懸空登記改名的那一刻,原主剛好把目標建回來(登記又有效了)⇒ 不得刪掉
        reg = self.d / ".ez2-aib" / "state" / "pane-vis" / "reg"
        reg.mkdir(parents=True)
        target = self.d / "back" / ".panevis.ev"
        link = reg / ("b" * 64 + ".reg")
        os.symlink(str(target), str(link))
        prog = (". '%s'\n"
                "mv() { case \"$*\" in *%s*.gc.*) mkdir -p '%s'; : >> '%s' ;; esac; command mv \"$@\"; }\n"
                "pane_vis_hook; echo \"hooked=$PANE_VIS_HOOKED\"\n" % (TICK, link.name, target.parent, target))
        self.attach()
        p = self.bash(prog, self.tick_env(self.pb))
        self.assertIn("hooked=1", p.stdout, p.stdout + p.stderr)
        self.assertTrue(os.path.lexists(str(link)), "原主剛重登的登記被清掉了")
        self.assertTrue(link.exists(), "登記應指向有效的訊號檔")
        self.assertEqual([x.name for x in reg.iterdir() if ".gc." in x.name], [], "留下暫存名")

    def test_v8h_unregister_only_own(self):
        p = self.bash(". '%s'\npane_vis_register && echo \"$PANE_VIS_LINK\"" % TICK)
        link = Path(p.stdout.strip().splitlines()[-1])
        self.assertTrue(link.exists(), p.stdout + p.stderr)
        other = self.d / "other.ev"
        other.write_text("")
        link.unlink()
        os.symlink(str(other), str(link))        # 同名但指到別處(不是自己的)
        self.bash(". '%s'\npane_vis_unregister" % TICK)
        self.assertTrue(os.path.lexists(str(link)), "撤登刪了不是自己的登記")
        link.unlink()
        self.bash(". '%s'\npane_vis_register" % TICK)
        self.bash(". '%s'\npane_vis_unregister" % TICK)
        self.assertFalse(os.path.lexists(str(link)), "撤登沒刪自己的")

    def test_v10_fallback_counted(self):
        # 沒掛上(別人佔格)與退回輪詢都要記數
        self.sbx.tm("set-hook", "-g", "client-attached[%d]" % self.IDX, "run-shell -b true")
        for _ in range(2):
            p = self.bash(". '%s'\npane_tick_init\npane_wait_if_hidden; echo \"hooked=$PANE_VIS_HOOKED\"" % TICK,
                          self.tick_env(self.pb, {"EZ2_PANE_DETACHED_TICK": "1", "EZ2_PANE_DETACHED_TICK_MAX": "1"}))
            self.assertIn("hooked=0", p.stdout, p.stdout + p.stderr)
        def stat(name):
            f = self.state / (".panevis.stat." + name)
            return f.read_text().strip() if f.exists() else "(沒有計數檔)"
        self.assertEqual(stat("hook_foreign"), "2")
        self.assertEqual(stat("poll"), "2")

    def test_v10b_overwritten_after_hook_counts_timeout(self):
        # 同一行程:先掛上 → 隱藏中 → 外部覆蓋 4770 格 → 沒事件 → 下一輪等滿退避 ⇒ wake_timeout +1(不重查所有權)
        f = self.state / ".panevis.stat.wake_timeout"
        prog = (". '%s'\npane_tick_init\n"
                "pane_wait_if_hidden; echo \"r1=$? hooked=$PANE_VIS_HOOKED\"\n"
                "a=$(cat '%s' 2>/dev/null || echo 0)\n"
                "command tmux set-hook -g 'session-window-changed[%d]' 'run-shell -b true'\n"
                "pane_wait_if_hidden; echo \"r2=$? hooked=$PANE_VIS_HOOKED\"\n"
                "b=$(cat '%s' 2>/dev/null || echo 0)\necho \"delta=$(( b - a ))\"\n"
                % (TICK, f, self.IDX, f))
        p = self.bash(prog, self.tick_env(self.pb, {"EZ2_PANE_DETACHED_TICK": "1", "EZ2_PANE_DETACHED_TICK_MAX": "1"}))
        out = p.stdout + p.stderr
        self.assertIn("r1=0 hooked=1", out, out)
        self.assertIn("r2=0 hooked=1", out, "這一輪仍以為掛著(不重查所有權)\n" + out)
        self.assertEqual(self.slot("session-window-changed"), "run-shell -b true", out)
        self.assertIn("delta=1", out, "被蓋掉後逾時醒來沒記到\n" + out)
        g = self.state / ".panevis.stat.wake_event"
        self.assertFalse(g.exists() and g.read_text().strip() != "0", out)

    def test_v10c_event_wake_counted(self):
        self.attach()
        out, took = self.wait_hidden(self.pb, action=self.sel("h:b"))
        self.assertIn("rc=0", out, out)
        f = self.state / ".panevis.stat.wake_event"
        self.assertEqual(f.read_text().strip() if f.exists() else "(沒有計數檔)", "1")

    def test_v4_unsafe_path_no_hook(self):
        self.attach()
        for st in (str(self.d / "has space"), str(self.d / "q'q"), "rel/dir"):
            with self.subTest(st):
                if st.startswith("/"):
                    Path(st).mkdir(exist_ok=True)
                p = self.bash(". '%s'\npane_tick_init\npane_wait_if_hidden; rc=$?\n"
                              "echo \"rc=$rc hooked=$PANE_VIS_HOOKED\"\n" % TICK,
                              self.tick_env(self.pb, {"EZ2_COCKPIT_STATE_DIR": st, "EZ2_PANE_DETACHED_TICK": "1",
                                                      "EZ2_PANE_DETACHED_TICK_MAX": "1"}))
                self.assertIn("rc=0 hooked=0", p.stdout, p.stdout + p.stderr)
        hooks = self.sbx.tm("show-hooks", "-g").stdout
        self.assertNotIn(".panevis.ev", hooks, hooks)

    def test_v6_multi_client_linked_grouped(self):
        c1 = self.attach()
        self.attach()
        self.detach(c1)
        self.assertEqual(self.visible(self.pa), 0, "兩個 client、走掉一個,a 仍看得見")
        # 連結窗:b 連進 session L;client 看 L 裡的 b ⇒ b 看得見(h 那邊目前是 a)
        self.sbx.tm("new-session", "-d", "-s", "L", "-n", "x", "sleep 120")
        self.sbx.tm("link-window", "-s", "h:b", "-t", "L:9")
        self.sbx.tm("select-window", "-t", "L:9")
        self.attach("L")
        self.assertEqual(self.visible(self.pb), 0, "連結窗從 L 看得見")
        self.assertEqual(self.visible(self.pa), 0, "h 的 client 仍看 a")
        # grouped:h2 與 h 同組;只剩 h2 的 client 看 b
        for c in list(self.clients):
            self.detach(c)
        self.sbx.tm("new-session", "-d", "-s", "h2", "-t", "h")
        self.sbx.tm("select-window", "-t", "h2:b")
        self.attach("h2")
        self.assertEqual(self.visible(self.pb), 0, "grouped h2 在看 b")
        self.assertEqual(self.visible(self.pa), 1, "沒有 client 在看 a")

    def test_v6b_linked_switch_wakes(self):
        # b 連進 session L;client 只看 L。L 那邊切到連結的 b ⇒ 事件發生在 L,鉤子要是全域的才叫得醒
        self.sbx.tm("new-session", "-d", "-s", "L", "-n", "x", "sleep 120")
        self.sbx.tm("link-window", "-d", "-s", "h:b", "-t", "L:9")   # -d:不切過去,L 仍在看 x
        self.attach("L")
        self.assertEqual(self.visible(self.pb), 1, "治具:等待前 b 應看不見")
        out, took = self.wait_hidden(self.pb, action=self.sel("L:9"))
        self.assertIn("hooked=1", out, out)
        self.assertLess(took, 3.0, "連結窗在別的 session 被切到,沒叫醒(%.1f 秒)\n%s" % (took, out))

    def test_v6c_grouped_switch_wakes(self):
        self.sbx.tm("new-session", "-d", "-s", "h2", "-t", "h")
        self.sbx.tm("select-window", "-t", "h2:a")
        self.attach("h2")
        out, took = self.wait_hidden(self.pb, action=self.sel("h2:b"))
        self.assertIn("hooked=1", out, out)
        self.assertLess(took, 3.0, "grouped session 那邊切窗沒叫醒(%.1f 秒)\n%s" % (took, out))


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
        cases = {"tmux 失敗": "exit 1", "空輸出(舊 tmux 不認格式)": "exit 0", "不是數字": "echo x",
                 "兩個值": "echo '1 0'", "負號": "echo -1"}
        for name, body in cases.items():
            with self.subTest(name):
                p = self.bash(". '%s'\npane_visible" % TICK, self.fake(body))
                self.assertEqual(p.returncode, 0, "%s 被當成看不見(會凍住畫面)" % name)
        p = self.bash(". '%s'\npane_visible" % TICK, self.fake("echo 0"))
        self.assertEqual(p.returncode, 1, "0 個 client 在看 ⇒ 看不見")
        p = self.bash(". '%s'\npane_visible" % TICK, self.fake("echo 2"))
        self.assertEqual(p.returncode, 0, "2 個 client 在看")


class Bump(Base):
    def test_v7_concurrent_appends(self):
        # 鉤子做的事(printf . >>)同時 40 個寫入者 ⇒ 大小剛好 +40;等待端讀到的事件數也一樣
        sig = self.state / ".panevis.ev"
        sig.write_text(".....")
        procs = [subprocess.Popen(["sh", "-c", "printf . >> '%s'" % sig]) for _ in range(40)]
        for p in procs:
            self.assertEqual(p.wait(30), 0)
        self.assertEqual(sig.stat().st_size, 45)
        p = self.bash(". '%s'\npane_vis_count c; echo $c" % TICK)
        self.assertEqual(p.stdout.strip(), "45")

    def test_v7b_trim_when_big(self):
        sig = self.state / ".panevis.ev"
        sig.write_text("." * 5000)
        p = self.bash(". '%s'\nPANE_VIS_HOOKED=1; pane_vis_trim; pane_vis_count c; echo $c" % TICK)
        self.assertEqual(p.stdout.strip(), "0", "超過 4096 沒歸零")
        sig.write_text("." * 100)
        p = self.bash(". '%s'\nPANE_VIS_HOOKED=1; pane_vis_trim; pane_vis_count c; echo $c" % TICK)
        self.assertEqual(p.stdout.strip(), "100", "沒超過也被歸零")


class Static(unittest.TestCase):
    def test_v5_warroom_uses_hidden(self):
        s = WARROOM.read_text(encoding="utf-8")
        loop = s[s.index("\nwhile true; do"):]
        self.assertIn('if [ "$FIRST_PAINT" != "1" ] && pane_wait_if_hidden; then continue; fi', loop)
        self.assertNotIn("pane_wait_if_detached", loop)


class Reserved(unittest.TestCase):
    def test_v9_index_4770_reserved(self):
        doc = (ROOT / "docs" / "tmux-hook-indexes.md").read_text(encoding="utf-8")
        self.assertIn("| 4770 |", doc)
        tick = TICK.read_text(encoding="utf-8")
        self.assertIn("PANE_VIS_IDX=4770", tick)
        bad = []
        for f in (ROOT / "src").rglob("*"):
            if not f.is_file() or f == TICK or f.suffix not in (".sh", ".py", ""):
                continue
            try:
                txt = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for n, line in enumerate(txt.splitlines(), 1):
                if "set-hook" in line and "[4770]" in line:
                    bad.append("%s:%d" % (f.relative_to(ROOT), n))
        self.assertEqual(bad, [], "4770 是 pane-tick 的保留下標(docs/tmux-hook-indexes.md)")


class Guard(unittest.TestCase):
    """沙盒守門:拒跑時 tmux 一次都不准執行(用會記錄的假 tmux 驗)。"""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="pg"))
        self.addCleanup(shutil.rmtree, str(self.d), True)
        self.calls = self.d / "calls"
        fake = self.d / "faketmux"
        fake.write_text("#!/bin/sh\necho \"$*\" >> '%s'\n" % self.calls)
        fake.chmod(0o755)
        self.fake = str(fake)

    def ncalls(self):
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0

    def test_s1_dir_gone(self):
        sb = Sandbox(self.d / "t", self.fake)
        (self.d / "t").mkdir()
        sb.tm("ls")
        self.assertEqual(self.ncalls(), 1)
        shutil.rmtree(str(self.d / "t"))
        with self.assertRaises(SandboxRefused):
            sb.tm("kill-server")
        self.assertEqual(self.ncalls(), 1, "夾不在了還叫了 tmux")

    def test_s2_default_socket(self):
        sb = Sandbox(self.d / "t", self.fake)
        (self.d / "t").mkdir()
        for bad in ("/private/tmp/tmux-%d/default" % os.getuid(), "/tmp/tmux-%d/default" % os.getuid(),
                    str(self.d / "t" / ("tmux-%d" % os.getuid()) / "x"), str(self.d / "t" / "default")):
            with self.subTest(bad):
                sb.sock = Path(bad)
                with self.assertRaises(SandboxRefused):
                    sb.tm("kill-server")
        self.assertEqual(self.ncalls(), 0)

    def test_s3_outside_sandbox(self):
        sb = Sandbox(self.d / "t", self.fake)
        (self.d / "t").mkdir()
        for bad in (str(self.d / "elsewhere"), str(self.d / "t" / ".." / "x"), "/tmp/x"):
            with self.subTest(bad):
                sb.sock = Path(bad)
                with self.assertRaises(SandboxRefused):
                    sb.tm("kill-server")
        self.assertEqual(self.ncalls(), 0)

    def test_s5_tmux_env_guarded(self):
        # 交給被測 bash 的 TMUX 也要過守門:夾不在 ⇒ 不給
        class T(RealTmux):
            def runTest(self):
                pass
        t = T()
        t.d = self.d
        t.state = self.d
        t.sbx = Sandbox(self.d / "t", self.fake)
        with self.assertRaises(SandboxRefused):
            t.tick_env("%1")
        self.assertEqual(self.ncalls(), 0)

    def test_s4_cleanup_path(self):
        sb = Sandbox(self.d / "t", self.fake)
        (self.d / "t").mkdir()
        shutil.rmtree(str(self.d / "t"))
        sb.cleanup()                      # 不得丟例外、不得叫 tmux
        sb2 = Sandbox(self.d / "t2", self.fake)
        (self.d / "t2").mkdir()
        sb2.sock = Path("/private/tmp/tmux-%d/default" % os.getuid())
        sb2.cleanup()
        self.assertEqual(self.ncalls(), 0, "清理路徑繞過守門叫了 tmux")


KNIVES = [
    ("K1", "'#{window_active_clients}' 2>/dev/null)\" || return 0\n",
     "'#{window_active_clients}' 2>/dev/null)\" || return 1\n",
     "src/cockpit/pane-tick.sh", "Fallback.test_v3_unknown_means_visible"),
    ("K2", "'#{window_active_clients}' 2>/dev/null)\" || return 0\n",
     "'#{session_attached}' 2>/dev/null)\" || return 0\n",
     "src/cockpit/pane-tick.sh", "RealTmux.test_v1_window_active_matters"),
    ("K3", "  # 契約第 2 條:讀完基準再判一次。判看不見之後、讀基準之前的切回 / 附著在這裡接住。\n  if pane_visible; then",
     "  # 契約第 2 條:讀完基準再判一次。判看不見之後、讀基準之前的切回 / 附著在這裡接住。\n  if false; then",
     "src/cockpit/pane-tick.sh", "RealTmux.test_v2c_switch_after_hook_before_baseline"),
    ("K4", "  case \"$PANE_VIS_SIG\" in ''|[!/]*|*[!A-Za-z0-9._/+-]*) pane_vis_nohook no_hook_path; return 0 ;; esac",
     "  case \"$PANE_VIS_SIG\" in '') return 0 ;; esac",
     "src/cockpit/pane-tick.sh", "RealTmux.test_v4_unsafe_path_no_hook"),
    ("K5", '  if [ "$FIRST_PAINT" != "1" ] && pane_wait_if_hidden; then continue; fi\n',
     "  pane_wait_if_detached && continue\n",
     "src/cockpit/warroom-board.sh", "Static.test_v5_warroom_uses_hidden"),
    ("K6", '&& printf . >> \\"\\$r\\"; done 2>/dev/null; :\'"', '&& printf . > \\"\\$r\\"; done 2>/dev/null; :\'"',
     "src/cockpit/pane-tick.sh", "RealTmux.test_v2d_back_and_forth_still_wakes"),
    ("K7", '  local tgt="$PANE_VIS_REG/*.reg"', '  local tgt="$PANE_VIS_SIG"',
     "src/cockpit/pane-tick.sh", "RealTmux.test_v8b_two_cabins_race_idempotent"),
    ("K10", '  if [ "$got" != "1111" ]; then', '  if false; then',
     "src/cockpit/pane-tick.sh", "RealTmux.test_v8_foreign_slot_not_taken"),
    ("K11", '  cond="#{&&:#{!=:#{command-alias[0]},},$cond}"', '  cond="1"',
     "src/cockpit/pane-tick.sh", "RealTmux.test_v8e_foreign_inserted_before_write_survives"),
    ("K12", '  PANE_VIS_LINK="$PANE_VIS_REG/$h.reg"', '  PANE_VIS_LINK="$PANE_VIS_REG/${canon//\\//_}.reg"',
     "src/cockpit/pane-tick.sh", "RealTmux.test_v8f_registration_names_do_not_collide"),
    ("K13", '    if [ -e "$t" ]; then', '    if false; then',
     "src/cockpit/pane-tick.sh", "RealTmux.test_v8g_gc_vs_reregister_interleave"),
    ("K14", '  [ "$PANE_VIS_LINK" -ef "$PANE_VIS_SIG" ] && rm -f "$PANE_VIS_LINK" 2>/dev/null',
     '  rm -f "$PANE_VIS_LINK" 2>/dev/null',
     "src/cockpit/pane-tick.sh", "RealTmux.test_v8h_unregister_only_own"),
    ("K16", '  pane_vis_stat wake_timeout\n', '',
     "src/cockpit/pane-tick.sh", "RealTmux.test_v10b_overwritten_after_hook_counts_timeout"),
    ("K17", '{ pane_vis_stat wake_event; PANE_HIDDEN_WAIT=""; return 0; }', '{ PANE_HIDDEN_WAIT=""; return 0; }',
     "src/cockpit/pane-tick.sh", "RealTmux.test_v10c_event_wake_counted"),
    ("K15", '    pane_vis_stat poll                # 退回輪詢(登記例外):每睡一次記一筆\n', '',
     "src/cockpit/pane-tick.sh", "RealTmux.test_v10_fallback_counted"),
    ("K8", "        if not self.root.is_dir():\n            raise SandboxRefused",
     "        if False:\n            raise SandboxRefused",
     "tests/aib_tmux_sandbox.py", "Guard.test_s1_dir_gone"),
    ("K9", "        if os.path.basename(sock) == \"default\" or \"/tmux-%d/\" % os.getuid() in sock:",
     "        if False:",
     "tests/aib_tmux_sandbox.py", "Guard.test_s2_default_socket"),
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
