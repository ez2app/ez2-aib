#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""轉接程式登入自起(第二波 #6 之 NOTE #22;10-21 @abb89 裁:獨立 LaunchAgent 只跑 chat_bridge boot-run,不開艙不開席,
明確 install-boot 才有、uninstall-boot 可拆、艙沒開時只收信落待審)。

B1 boot_plist:Label、ProgramArguments 以 boot-run 結尾、RunAtLoad、KeepAlive、ThrottleInterval、PATH 含 Homebrew、
   環境只帶路徑類(有設的 EZ2_AIB_STATE_ROOT 帶、token / 開關不帶)、log 在 chat home
B2 install_boot:非 macOS rc 2 不寫檔;開發樹 rc 1 不寫檔(AIB_ALLOW_FOREIGN_BINDINGS=1 放行);正常 ⇒ 寫 plist 0600、
   launchctl bootout 再 bootstrap;bootstrap 失敗 rc 1;uninstall ⇒ bootout + 刪檔;沒裝過也 rc 0
B3 boot_run:開關關著 ⇒ 不叫 run、睡 BOOT_POLL_S;開著 ⇒ 叫 run,run 回來(開關關了)⇒ 睡 RESTART_GAP 再看;
   run 因 SIGTERM 回來 ⇒ 退出 0(讓 launchd 重拉 = 換新碼)
B4 _cmdline_is_bridge 認 run 與 boot-run,不認別的
B5 四語字典:install_boot / boot_* 鍵四種語言都有
刀(複本):K1 plist 沒 KeepAlive ⇒ B1 紅;K2 install 不擋開發樹 ⇒ B2 紅
"""
import importlib.util
import io
import json
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_bridge_boot", str(COCKPIT / "chat_bridge.py"))
CB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CB)


class FakeCtl:
    def __init__(self, fail_bootstrap=False):
        self.calls, self.fail = [], fail_bootstrap

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "bootstrap" and self.fail:
            return 1, "Input/output error"
        return 0, ""


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-boot-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name)
        self.env = {"HOME": str(self.td), "PATH": "/usr/bin:/bin", "EZ2_AIB_STATE_ROOT": str(self.td / "state"),
                    "AIB_CHAT_HOME": str(self.td / "chat"), "EZ2_AIB_LOCALE": "zh-TW",
                    "AIB_TOUCHID": "0", "X_CAPTAIN_TOKEN": "secret", "AIB_CHAT_TICK": "0.1"}


class Plist(Base):
    def test_b1_plist(self):
        d = CB.boot_plist(self.env, python="/py", script="/s/chat_bridge.py")
        self.assertEqual(d["Label"], CB.BOOT_LABEL)
        self.assertEqual(d["ProgramArguments"], ["/py", "/s/chat_bridge.py", "boot-run"])
        self.assertTrue(d["RunAtLoad"] and d["KeepAlive"] is True)
        self.assertEqual(d["ThrottleInterval"], CB.BOOT_THROTTLE_S)
        ev = d["EnvironmentVariables"]
        self.assertIn("/opt/homebrew/bin", ev["PATH"].split(":"))
        self.assertEqual((ev["HOME"], ev["EZ2_AIB_STATE_ROOT"], ev["AIB_CHAT_HOME"]),
                         (str(self.td), str(self.td / "state"), str(self.td / "chat")))
        for k in ("AIB_TOUCHID", "X_CAPTAIN_TOKEN", "AIB_CHAT_TICK"):
            self.assertNotIn(k, ev, k)
        self.assertEqual(d["StandardOutPath"], str(self.td / "chat" / "launchd.log"))
        self.assertEqual(d["StandardErrorPath"], d["StandardOutPath"])
        self.assertNotIn("secret", json.dumps(d))
        plistlib.dumps(d)       # 寫得出 plist


class Install(Base):
    def test_b2_install_uninstall(self):
        buf, ctl = io.StringIO(), FakeCtl()
        self.assertEqual(CB.install_boot(self.env, buf, home=self.td, platform="linux", launchctl=ctl), 2)
        self.assertFalse(CB.boot_installed(self.td))
        self.assertEqual(ctl.calls, [])
        self.assertEqual(CB.install_boot(self.env, buf, home=self.td, platform="darwin", launchctl=ctl, dev_tree="/x/wt"), 1)
        self.assertFalse(CB.boot_installed(self.td), "開發樹不裝")
        self.assertIn("/x/wt", buf.getvalue())
        env2 = dict(self.env, AIB_ALLOW_FOREIGN_BINDINGS="1")
        self.assertEqual(CB.install_boot(env2, buf, home=self.td, platform="darwin", launchctl=ctl, dev_tree="/x/wt"), 0)
        self.assertTrue(CB.boot_installed(self.td))
        p = CB.boot_plist_path(self.td)
        self.assertEqual(oct(p.stat().st_mode & 0o777), "0o600")
        with open(p, "rb") as fh:
            d = plistlib.load(fh)
        self.assertEqual(d["ProgramArguments"][-1], "boot-run")
        self.assertTrue(Path(d["StandardOutPath"]).parent.is_dir())
        self.assertEqual([c[0] for c in ctl.calls], ["bootout", "bootstrap"])
        self.assertTrue(ctl.calls[-1][1].startswith("gui/") and ctl.calls[-1][2] == str(p))
        self.assertIn(str(p), buf.getvalue())
        bad = FakeCtl(fail_bootstrap=True)
        buf = io.StringIO()
        self.assertEqual(CB.install_boot(self.env, buf, home=self.td, platform="darwin", launchctl=bad, dev_tree=""), 1)   # "" = 不是開發樹
        self.assertIn("Input/output error", buf.getvalue())
        self.assertTrue(CB.boot_kickstart(self.td, launchctl=ctl))
        self.assertEqual(ctl.calls[-1][0], "kickstart")
        buf = io.StringIO()
        self.assertEqual(CB.uninstall_boot(buf, home=self.td, launchctl=ctl), 0)
        self.assertFalse(CB.boot_installed(self.td))
        self.assertEqual(ctl.calls[-1][0], "bootout")
        self.assertEqual(CB.uninstall_boot(io.StringIO(), home=self.td, launchctl=ctl), 0, "沒裝過也 0")
        self.assertFalse(CB.boot_kickstart(self.td, launchctl=ctl), "沒裝 ⇒ 不叫")


class Loop(Base):
    def test_b3_boot_run(self):
        slept, ran = [], []
        CB._mkdir_private(CB.chat_home(self.env))
        self.assertFalse(CB.is_enabled(self.env))
        rc = CB.boot_run(self.env, out=io.StringIO(), run_fn=lambda env, out: ran.append(1), sleep=slept.append, max_loops=2)
        self.assertEqual((rc, ran, slept), (0, [], [CB.BOOT_POLL_S, CB.BOOT_POLL_S]), "開關關著:不叫 run、每 BOOT_POLL_S 看一次")
        CB.write_json(str(CB.switch_path(self.env)), {"v": CB.VERSION, "on": True, "at": 1})
        self.assertTrue(CB.is_enabled(self.env))
        slept, ran = [], []
        rc = CB.boot_run(self.env, out=io.StringIO(), run_fn=lambda env, out: ran.append(1), sleep=slept.append, max_loops=2)
        self.assertEqual((rc, ran, slept), (0, [1, 1], [CB.RESTART_GAP, CB.RESTART_GAP]), "開著:叫 run,回來隔 RESTART_GAP 再叫")

        def run_then_term(env, out):
            ran.append("t")
            CB._BOOT_TERM["got"] = True        # 像 aib chat restart 送了 SIGTERM
        slept, ran = [], []
        rc = CB.boot_run(self.env, out=io.StringIO(), run_fn=run_then_term, sleep=slept.append, max_loops=5)
        self.assertEqual((rc, ran, slept), (0, ["t"], []), "SIGTERM ⇒ 立刻退出讓 launchd 重拉")
        CB._BOOT_TERM["got"] = False

    def test_b4_cmdline(self):
        self.assertTrue(CB._cmdline_is_bridge("/py /x/chat_bridge.py run"))
        self.assertTrue(CB._cmdline_is_bridge("/py /x/chat_bridge.py boot-run"))
        self.assertFalse(CB._cmdline_is_bridge("/py /x/chat_bridge.py status"))
        self.assertFalse(CB._cmdline_is_bridge("/py /x/other.py run"))

    def test_b5_i18n(self):
        want = None
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            keys = {k for k in d if k.startswith("aib.chat.boot_") or k in ("aib.chat.help.install_boot", "aib.chat.help.uninstall_boot")}
            self.assertGreaterEqual(len(keys), 11, lang)
            want = want or keys
            self.assertEqual(keys, want, lang)


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_bridge.py", needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_no_keepalive(self):
        self._knife('"RunAtLoad": True, "KeepAlive": True,', '"RunAtLoad": True, "KeepAlive": False,', "Plist.test_b1_plist")

    def test_k2_dev_tree_not_refused(self):
        self._knife('    if dev and env.get("AIB_ALLOW_FOREIGN_BINDINGS") != "1":\n        out.write(T("boot_dev_tree", path=dev) + "\\n")\n        return 1\n',
                    "", "Install.test_b2_install_uninstall")


if __name__ == "__main__":
    unittest.main()
