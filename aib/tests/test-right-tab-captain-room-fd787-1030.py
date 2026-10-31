#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""右欄第 4 頁「艦長艙」v1(Andy 10-30 21:1x;工單 #446,規格 SPEC-right-tab-captain-room-f5fb8-1030)。
狀態根、外掛家目錄、HOME 全是暫存;aib 換成替身(AIB_UI_AIB_BIN 給 room status、AIB_ROOM_TAB_AIB 給 room open)。

C1 分頁列四格:起訖欄檔有 room、接在 left 後面;單幀畫第 4 頁 ⇒ 四個字都在、第 4 格反白;右欄窄 ⇒ 第 4 格用短字
C2 有裝外掛:第一列「開啟遊戲化艦長艙」,點擊表第一列是 room-open;版本取 plugin.json;網址取 aib room status
C3 開啟:替身 aib room open 只被叫 1 次(3 秒內再點 = debounced);過了 3 秒再點 ⇒ 再開一次
C4 沒裝外掛:列字是「沒裝」、點擊表沒有 room-open、open 回 not-installed 且替身 0 次;版本 / 網址顯示「—」
C5 aib room status 失敗 / 快取 JSON 壞 / 欄位型別錯 ⇒ 顯示「—」或「未啟動」,rc 0,不印替身吐出來的怪字
C6 滑鼠:點第 4 格 ⇒ right-tab=4;hit_test 第一列 ⇒ room-open;mouse-ctl 乾跑 ⇒ 叫 right_room_page.py open
C7 鍵盤:{mod}+Shift+E(pane-tab-toggle.sh right)四頁輪切 2→1→3→4→2;左欄照舊 0↔1
C8 四語鍵齊:新鍵四語都有、不空、佔位符一致
C9 Enter:背景讀鍵收到 Enter ⇒ .right-enter 變;別的鍵不變;讀鍵那支不拿著繪製鎖(fd 9)
刀(複本 + run_guarded):K1 拿掉防連點 ⇒ C3 紅;K2 拿掉沒裝判斷 ⇒ C4 紅;K3 少一語 ⇒ C8 紅
"""
import json
import os
import re
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

COCKPIT = ROOT / "src" / "cockpit"
PAGE = COCKPIT / "right_room_page.py"
TABS = COCKPIT / "pane-right-tabs.sh"
BASH = os.environ.get("AIB_TEST_BASH") or "/bin/bash"
LOCALES = ("zh-TW", "en", "ja", "ko")
NEW_KEYS = ("aib.tabs.right_room", "aib.tabs.right_room_short", "aib.room_tab.open", "aib.room_tab.not_installed",
            "aib.room_tab.ui_mode", "aib.room_tab.hint_ui", "aib.room_tab.version", "aib.room_tab.url",
            "aib.room_tab.url_off")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TEST_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
# 分頁列(pane-right-tabs.sh)與切頁(pane-tab-toggle.sh → state-transition.sh)都靠 flock 拿鎖。
# GitHub 的 macOS runner 沒裝 flock(本機與會員機由 homebrew 裝)⇒ 跑到這兩支的格在那裡改成具名跳過,
# Ubuntu CI 與本機照常實測;不在測試裡偽造 flock,免得量到的不是真鎖。(#454 CI macos-3.12 紅 10-31)
FLOCK = shutil.which("flock", path=TEST_PATH)
NO_FLOCK = "這台測試 PATH 上沒有 flock(GitHub macOS runner);分頁列 / 切頁腳本要它拿鎖,Ubuntu CI 與本機照跑"


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="aib-room-tab-"))
        self.addCleanup(shutil.rmtree, str(self.d), True)
        self.home = self.d / "home"
        self.aibhome = self.d / "aibhome"
        self.state = self.d / "state"
        self.cockpit = self.d / "cockpit"
        for p in (self.home, self.aibhome, self.state, self.cockpit):
            p.mkdir()
        self.calls = self.d / "open-calls.txt"
        self.fake_open = self.d / "fake-aib-open"
        self.fake_open.write_text("#!/bin/sh\necho \"$*\" >> %s\n" % self.calls, encoding="utf-8")
        self.fake_open.chmod(0o755)
        self.set_status('{"alive": true, "url": "http://127.0.0.1:8770/"}')

    def set_status(self, line, rc=0):
        f = self.d / "fake-aib-status"
        f.write_text("#!/bin/sh\nprintf '%%s\\n' '%s'\nexit %d\n" % (line.replace("'", ""), rc), encoding="utf-8")
        f.chmod(0o755)

    def install_plugin(self, version="0.1.70"):
        d = self.aibhome / "plugins" / "captain-room"
        d.mkdir(parents=True)
        (d / "room.py").write_text("print('room')\n", encoding="utf-8")
        (d / "plugin.json").write_text(json.dumps({"name": "captain-room", "module": "captain_room", "version": version,
                                                   "commands": {"room": "room.py"}}), encoding="utf-8")

    def env(self, **extra):
        e = {"PATH": TEST_PATH, "HOME": str(self.home),
             "EZ2_AIB_HOME": str(self.aibhome), "EZ2_AIB_STATE_ROOT": str(self.state),
             "EZ2_COCKPIT_STATE_DIR": str(self.cockpit), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
             "EZ2_AIB_LOCALE": "zh-TW", "AIB_UI_AIB_BIN": str(self.d / "fake-aib-status"),
             "AIB_ROOM_TAB_AIB": str(self.fake_open), "PYTHONDONTWRITEBYTECODE": "1"}
        e.update(extra)
        return e

    def page(self, *args, **extra):
        r = subprocess.run([sys.executable, "-B", str(PAGE)] + list(args), env=self.env(**extra), capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def frame(self, tab, width=44):
        if not FLOCK:
            self.skipTest(NO_FLOCK)
        (self.cockpit / "right-tab").write_text("%s\n" % tab, encoding="utf-8")
        r = subprocess.run([BASH, str(TABS)], env=self.env(EZ2_ONESHOT="1", EZ2_PANE_W=str(width)),
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def rowmap(self):
        return json.loads((self.cockpit / "ai-rowmap.json").read_text(encoding="utf-8"))

    def n_calls(self):
        try:
            return len(self.calls.read_text(encoding="utf-8").splitlines())
        except OSError:
            return 0

    def produce(self):
        self.page("produce")


def tr(locale, key):
    return json.loads((ROOT / "i18n" / ("%s.json" % locale)).read_text(encoding="utf-8"))[key]


class AibCmdRoute(Base):
    """10-31 Andy 點右欄第 4 頁第一列「開啟艦長艙」沒開:bash 啟動器的 room 只認同資料夾的 captain_room.py(公開版沒有)⇒ 印「還在開發中」。
    開室要走使用者打 `aib` 的那條(PATH 上的 aib / 套件入口 cli.py),bash 啟動器只是最後退路。"""

    def aib_cmd(self, path, with_fake=False):
        e = self.env(PATH=path)
        e.pop("AIB_ROOM_TAB_AIB", None)
        code = "import json, sys; sys.path.insert(0, %r); import right_room_page as m; print(json.dumps(m.aib_cmd()))" % str(COCKPIT)
        r = subprocess.run([sys.executable, "-B", "-c", code], env=e, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip())

    def test_d1_path_aib_wins(self):
        bindir = self.d / "bin"
        bindir.mkdir()
        fake = bindir / "aib"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o755)
        self.assertEqual(self.aib_cmd("%s:%s" % (bindir, TEST_PATH)), [str(fake)], "PATH 上的 aib 優先(跟 aib_ui._aib_bin 同)")

    def test_d2_cli_entry_when_no_path_aib(self):
        empty = self.d / "empty-bin"
        empty.mkdir()
        cmd = self.aib_cmd(str(empty))
        self.assertEqual(cmd, [sys.executable, str(ROOT / "cli.py")], "沒 PATH aib ⇒ 走套件入口 cli.py(會把 room 交給外掛),不是 bash 啟動器")
        self.assertNotEqual(cmd[0], "bash")

    def test_d3_bash_launcher_cannot_open_room(self):
        """刀 / 根因紀錄:公開版 bash 啟動器的 `room open` 只會說還在開發中(rc 2),所以不能當預設路徑。"""
        if (COCKPIT / "captain_room.py").is_file():
            self.skipTest("這棵樹有核心版 captain_room.py(私有倉),bash 啟動器開得了")
        e = self.env()
        r = subprocess.run([BASH, str(COCKPIT / "aib"), "room", "open", "--no-browser"], env=e, capture_output=True,
                           text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 2, (r.stdout, r.stderr))
        self.assertIn("room", r.stdout + r.stderr)

    def test_d4_knife_old_route(self):
        """拿掉 PATH / cli.py 兩層 ⇒ 退回 bash 啟動器 ⇒ d2 必紅(刀要真咬)。"""
        src = PAGE.read_text(encoding="utf-8")
        cut = src.replace("    found = shutil.which(\"aib\")\n    if found:\n        return [found]\n", "")
        cut = cut.replace("    if os.path.isfile(cli):\n        return [sys.executable, cli]\n", "")
        self.assertNotEqual(cut, src)
        kd = self.d / "knife"
        kd.mkdir()
        (kd / "right_room_page.py").write_text(cut, encoding="utf-8")
        for f in ("i18n_lib.py",):
            if (COCKPIT / f).is_file():
                shutil.copy(str(COCKPIT / f), str(kd / f))
        e = self.env(PATH=str(self.d / "empty-bin-k"))
        (self.d / "empty-bin-k").mkdir()
        e.pop("AIB_ROOM_TAB_AIB", None)
        code = "import json, sys; sys.path.insert(0, %r); import right_room_page as m; print(json.dumps(m.aib_cmd()))" % str(kd)
        r = subprocess.run([sys.executable, "-B", "-c", code], env=e, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout.strip())[0], "bash", "刀版退回 bash 啟動器 ⇒ 正式版的 d2 斷言在刀版會紅")


class TabBar(Base):
    def test_c1_four_spans_and_bar(self):
        out = self.frame(4)
        spans = json.loads((self.cockpit / "right-tab-spans.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(spans), ["ai", "left", "room", "usage"])
        self.assertEqual(spans["room"][0], spans["left"][1])          # 第 4 格接在第 3 格後面
        self.assertGreater(spans["room"][1], spans["room"][0])
        bar = out.splitlines()[1]
        for k in ("aib.tabs.right_ai", "aib.tabs.right_usage", "aib.tabs.right_leftover", "aib.tabs.right_room"):
            self.assertIn(tr("zh-TW", k), bar)
        self.assertIn("\x1b[1;7m %s \x1b[0m" % tr("zh-TW", "aib.tabs.right_room"), bar)
        # 其他三頁也畫第 4 格(沒反白)
        for t in ("2", "1", "3"):
            self.assertIn(tr("zh-TW", "aib.tabs.right_room"), self.frame(t).splitlines()[1])

    def test_c1_narrow_uses_short_label(self):
        bar = ANSI.sub("", self.frame(4, width=26).splitlines()[1])
        self.assertIn(tr("zh-TW", "aib.tabs.right_room_short"), bar)
        self.assertNotIn(tr("zh-TW", "aib.tabs.right_room"), bar)


class Installed(Base):
    def setUp(self):
        super().setUp()
        self.install_plugin("0.1.70")

    def test_c2_rows_and_rowmap(self):
        self.produce()
        out = self.page("render", "--width", "44", "--row-offset", "2")
        lines = out.splitlines()
        self.assertEqual(lines[0], "")
        self.assertIn(tr("zh-TW", "aib.room_tab.open"), lines[1])
        text = "\n".join(lines)
        self.assertIn("0.1.70", text)
        self.assertIn("http://127.0.0.1:8770/", text)
        self.assertIn("room", text)
        rm = self.rowmap()
        self.assertEqual(rm[0]["line"], 3)                              # 分頁列 y=1、空行 y=2、第一列 y=3
        self.assertEqual(rm[0]["links"][0]["action"], "room-open")

    def test_c2_full_frame(self):
        self.produce()
        out = ANSI.sub("", self.frame(4))
        self.assertIn(tr("zh-TW", "aib.room_tab.open"), out)

    def test_c3_debounce(self):
        self.assertEqual(self.page("open").strip(), "opened")
        self.assertEqual(self.page("open").strip(), "debounced")
        for _ in range(50):
            if self.n_calls() >= 1:
                break
            time.sleep(0.05)
        time.sleep(0.2)
        self.assertEqual(self.n_calls(), 1)
        self.assertEqual(self.calls.read_text(encoding="utf-8").split(), ["room", "open"])
        mark = self.cockpit / ".room-open.at"
        old = time.time() - 5
        os.utime(mark, (old, old))                                    # 過了 3 秒
        self.assertEqual(self.page("open").strip(), "opened")
        for _ in range(50):
            if self.n_calls() >= 2:
                break
            time.sleep(0.05)
        self.assertEqual(self.n_calls(), 2)

    def test_c3_opening_marker(self):
        self.produce()
        self.page("open")
        out = self.page("render", "--width", "44")
        self.assertIn(tr("zh-TW", "aib.room_tab.open") + " …", out)

    def test_c5_status_fails(self):
        self.set_status("not json at all", rc=1)
        self.produce()
        out = self.page("render", "--width", "44")
        self.assertIn(tr("zh-TW", "aib.room_tab.url_off"), out)
        self.set_status('{"alive": true, "url": "javascript:alert(1)"}')
        self.produce()
        out = self.page("render", "--width", "44")
        self.assertNotIn("javascript", out)
        self.assertIn(tr("zh-TW", "aib.room_tab.url_off"), out)


class NotInstalled(Base):
    def test_c4_not_installed(self):
        self.produce()
        out = self.page("render", "--width", "60")
        self.assertIn(tr("zh-TW", "aib.room_tab.not_installed"), out)
        self.assertNotIn(tr("zh-TW", "aib.room_tab.open"), out)
        self.assertEqual(self.rowmap(), [])
        self.assertEqual(self.page("open").strip(), "not-installed")
        time.sleep(0.2)
        self.assertEqual(self.n_calls(), 0)
        self.assertIn(tr("zh-TW", "aib.room_tab.version").replace("{value}", "—"), out)
        self.assertIn(tr("zh-TW", "aib.room_tab.url").replace("{value}", "—"), out)

    def test_c5_broken_cache(self):
        for bad in ("{not json", "[1, 2]", json.dumps({"at": "x", "installed": "yes", "version": 3, "url": ["a"],
                                                        "ui": {"x": 1}})):
            (self.cockpit / ".right-page-room.json").write_text(bad, encoding="utf-8")
            out = self.page("render", "--width", "44")
            self.assertIn("—", out)
        out = ANSI.sub("", self.frame(4))
        self.assertIn(tr("zh-TW", "aib.room_tab.not_installed")[:6], out)


class Mouse(Base):
    def test_c6_click_room_tab(self):
        self.frame(4)
        spans = json.loads((self.cockpit / "right-tab-spans.json").read_text(encoding="utf-8"))
        x = spans["room"][0] + 2
        (self.cockpit / "right-tab").write_text("2\n", encoding="utf-8")
        r = subprocess.run([BASH, str(COCKPIT / "mouse" / "mouse-ctl.sh"), "--click", "ai-list", str(x), "1",
                            "nosession", "--role", "right", "--dry-run"], env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertIn("+ write right-tab=4", r.stdout + r.stderr)

    def test_c6_hit_test_room_open(self):
        rm = [{"line": 3, "links": [{"action": "room-open", "start": 0, "end": 40}]}]
        (self.cockpit / "ai-rowmap.json").write_text(json.dumps(rm), encoding="utf-8")
        r = subprocess.run([sys.executable, "-B", str(COCKPIT / "mouse" / "hit_test.py"), "--role", "ai-list", "--x", "5",
                            "--y", "3", "--state-dir", str(self.cockpit)], env=self.env(), capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(json.loads(r.stdout)["action"], "room-open", r.stderr)

    def test_c6_mouse_ctl_dry_runs_open(self):
        rm = [{"line": 3, "links": [{"action": "room-open", "start": 0, "end": 40}]}]
        (self.cockpit / "ai-rowmap.json").write_text(json.dumps(rm), encoding="utf-8")
        (self.cockpit / "right-tab").write_text("4\n", encoding="utf-8")
        r = subprocess.run([BASH, str(COCKPIT / "mouse" / "mouse-ctl.sh"), "--click", "ai-list", "5", "3",
                            "nosession", "--role", "right", "--dry-run"], env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertIn("right_room_page.py open", r.stdout + r.stderr)
        self.assertEqual(self.n_calls(), 0)                            # 乾跑不真的開


class Keys(Base):
    def toggle(self, side):
        if not FLOCK:
            self.skipTest(NO_FLOCK)
        subprocess.run([BASH, str(COCKPIT / "pane-tab-toggle.sh"), side], env=self.env(), capture_output=True,
                       text=True, timeout=30, check=True)
        return (self.cockpit / ("%s-tab" % side)).read_text(encoding="utf-8").strip()

    def test_c7_right_cycles_four(self):
        (self.cockpit / "right-tab").write_text("2\n", encoding="utf-8")
        self.assertEqual([self.toggle("right") for _ in range(4)], ["1", "3", "4", "2"])

    def test_c7_left_unchanged(self):
        (self.cockpit / "left-tab").write_text("0\n", encoding="utf-8")
        self.assertEqual([self.toggle("left") for _ in range(2)], ["1", "0"])


class Lang(unittest.TestCase):
    def test_c8_four_locales(self):
        dicts = {loc: json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8")) for loc in LOCALES}
        for k in NEW_KEYS:
            ph = None
            for loc in LOCALES:
                self.assertTrue(k in dicts[loc], "%s 缺 %s" % (loc, k))   # 不用 assertIn:失敗時會印整本字典
                v = dicts[loc][k]
                self.assertTrue(isinstance(v, str) and v.strip(), "%s %s 是空的" % (loc, k))
                got = sorted(re.findall(r"\{(\w+)\}", v))
                if ph is None:
                    ph = got
                self.assertEqual(got, ph, "%s %s 佔位符不一致" % (loc, k))


def reader_block():
    s = TABS.read_text(encoding="utf-8")
    i = s.index('ROOM_ENTER_F="$EZ2_COCKPIT_STATE_DIR/.right-enter"')
    j = s.index('ROOM_ENTER_SEEN=""', i)
    return s[i:j]


@unittest.skipUnless(sys.platform == "darwin" or shutil.which("lsof"), "要 lsof 看 fd")
class Enter(Base):
    def test_c9_enter_reader(self):
        import pty
        import select
        lock = self.d / "lk"
        prog = ('exec 9>%s\nEZ2_ONESHOT=0\n%s\nsleep 30\n' % (lock, reader_block()))
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(BASH, [BASH, "-c", prog], self.env())
        try:
            enter = self.cockpit / ".right-enter"
            pidf = self.cockpit / ".right-enter.pid"

            def wait_for(cond, t=5.0):
                end = time.time() + t
                while time.time() < end:
                    if cond():
                        return True
                    r, _, _ = select.select([fd], [], [], 0.05)
                    if r:
                        try:
                            os.read(fd, 4096)
                        except OSError:
                            pass
                return cond()

            self.assertTrue(wait_for(pidf.exists), "讀鍵那支沒起")
            rpid = pidf.read_text(encoding="utf-8").strip()
            os.write(fd, b"a")
            time.sleep(0.4)
            self.assertFalse(enter.exists(), "別的鍵也算 Enter")
            os.write(fd, b"\r")
            self.assertTrue(wait_for(enter.exists), "Enter 沒寫檔")
            first = enter.read_text(encoding="utf-8")
            os.write(fd, b"\r")
            self.assertTrue(wait_for(lambda: enter.read_text(encoding="utf-8") != first), "第二次 Enter 沒變")
            lsof = shutil.which("lsof") or "/usr/sbin/lsof"
            r = subprocess.run([lsof, "-p", rpid], capture_output=True, text=True, timeout=30)
            self.assertNotIn(str(lock), r.stdout, "讀鍵那支拿著繪製鎖(fd 9)")
        finally:
            for p in (pid,):
                try:
                    os.killpg(p, 9)
                except OSError:
                    pass
            try:
                rp = int((self.cockpit / ".right-enter.pid").read_text(encoding="utf-8").strip())
                os.kill(rp, 9)
            except (OSError, ValueError):
                pass
            try:
                os.waitpid(pid, 0)
            except OSError:
                pass
            os.close(fd)


KNIVES = [
    ("K1", "src/cockpit/right_room_page.py", "        if _opening(now):\n            return \"debounced\"\n", "",
     "Installed.test_c3_debounce"),
    ("K2", "src/cockpit/right_room_page.py",
     "    if not (plugin_info()[0] if installed is None else installed):\n        return \"not-installed\"\n", "",
     "NotInstalled.test_c4_not_installed"),
    ("K3", "i18n/ko.json", '  "aib.room_tab.hint_ui": "변경: aib ui room|terminal",\n', "", "Lang.test_c8_four_locales"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=60, timeout=180)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
