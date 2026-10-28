#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開 grokbot 席前看的是「這次要開的那一個 agent」綁了沒,不是「任何一個綁過就放行」(10-28)。假資料、暫存狀態目錄。

B1 grokbot_threads.agent_bound / `bound <id>`:檔在且網址金鑰形狀對才算;別的 agent 綁了不算;設定壞了不算;id 形狀錯 rc 2
B2 open_readiness._bot_hint:環境帶了 AIB_GROKBOT_AGENT_ID ⇒ 只看它;沒帶 ⇒ 要有一個真的能用的綁定才不標(只有壞檔照樣標)
B3 選定 agent 之後(bot-thread-menu aib_grokbot_agent_preflight):bound rc 0 ⇒ 不跳視窗、直接用;rc 1 ⇒ 跳 connect;
   量不到(rc 3)⇒ 不跳。另用真的 grokbot_threads + 假 osascript 跑一次:綁了的不跳、沒綁的跳
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COCKPIT = ROOT / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(COCKPIT / "mouse"))
import grokbot_threads as gt  # noqa: E402
import open_readiness as orr  # noqa: E402

A = "aaaaaaaa-1111-2222-3333-444444444444"
B = "bbbbbbbb-1111-2222-3333-444444444444"
URL = "https://api2.cursor.sh/automations/webhook/0123abcd-0123-4567-89ab-0123456789ab"


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-gb-bound-"))
        self.state = self.td / "state"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("EZ2_", "AIB_", "TMUX"))}
        self.env.update({"EZ2_AIB_STATE_ROOT": str(self.state), "HOME": str(self.td / "home"),
                         "AIB_PATH_BIN": str(self.td / "home" / "lbin"), "EZ2_AIB_LOCALE": "zh-TW",
                         "PYTHONDONTWRITEBYTECODE": "1"})

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def bind(self, agent, url=URL, key="k_valid_123"):
        p = gt.agent_wake_path(agent, self.state)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("GROKBOT_WAKE_URL=%s\nGROKBOT_WAKE_KEY=%s\n" % (url, key))


class B1Bound(Base):
    def test_agent_bound(self):
        self.bind(A)
        self.assertTrue(gt.agent_bound(A, self.state))
        self.assertFalse(gt.agent_bound(B, self.state))
        self.bind(B, url="http://evil.example/x")
        self.assertFalse(gt.agent_bound(B, self.state))
        self.assertFalse(gt.agent_bound("../x", self.state))

    def test_cli(self):
        self.bind(A)
        run = lambda *a: subprocess.run([sys.executable, str(COCKPIT / "grokbot_threads.py"), "bound"] + list(a),  # noqa: E731
                                        env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(run(A).returncode, 0)
        r = run(B)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(r.stdout), {"agentId": B, "bound": False})
        self.assertEqual(run("bad/id").returncode, 2)


class B2Readiness(Base):
    def hint(self, **extra):
        return orr._bot_hint("grokbot", dict(self.env, **extra))

    def test_specific_agent(self):
        self.bind(A)
        self.assertEqual(self.hint(AIB_GROKBOT_AGENT_ID=A), "")
        self.assertEqual(self.hint(AIB_GROKBOT_AGENT_ID=B), "no_doorbell", "別的 agent 綁了不算")

    def test_unknown_agent_needs_a_usable_binding(self):
        self.assertEqual(self.hint(), "no_doorbell")
        self.bind(B, url="http://bad")
        self.assertEqual(self.hint(), "no_doorbell", "只有壞的設定檔不算綁過")
        self.bind(A)
        self.assertEqual(self.hint(), "")


class B3Preflight(Base):
    def _fake_here(self, bound_rc):
        here = self.td / "here"
        here.mkdir()
        log = self.td / "calls.log"
        (here / "grokbot_threads.py").write_text(
            "import sys\nopen(%r,'a').write(' '.join(sys.argv[1:])+'\\n')\n"
            "if sys.argv[1]=='bound': sys.exit(%d)\n"
            "if sys.argv[1]=='connected': print('{\"connected\": []}')\n" % (str(log), bound_rc))
        (here / "bot_launchers.py").write_text("import sys\n")
        return here, log

    def _preflight(self, here, extra=""):
        script = ('source "%s"; HERE="%s"; T_PY3="%s"; aib_bot_thread_menu(){ return 0; }; '
                  'aib_bot_thread_held_guard(){ return 0; }; _btm_t(){ echo "$1"; }; %s '
                  'AIB_GROKBOT_AGENT_ID=%s aib_grokbot_agent_preflight') % (
            COCKPIT / "bot-thread-menu.sh", here, sys.executable, extra, A)
        return subprocess.run(["bash", "-c", script], env=self.env, capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL)

    def test_rc_matrix(self):
        for rc, want_connect in ((0, False), (1, True), (3, False)):
            shutil.rmtree(self.td / "here", ignore_errors=True)
            (self.td / "calls.log").unlink(missing_ok=True)
            here, log = self._fake_here(rc)
            r = self._preflight(here)
            self.assertEqual(r.returncode, 0, r.stderr)
            calls = log.read_text().splitlines()
            self.assertIn("bound %s" % A, calls)
            self.assertEqual(any(c.startswith("connect ") for c in calls), want_connect, (rc, calls))

    def _fake_osa(self):
        b = self.td / "fbin"
        b.mkdir(exist_ok=True)
        log = self.td / "osa.log"
        for name in ("osascript", "open", "pbcopy"):
            (b / name).write_text("#!/bin/sh\necho %s >> %s\nexit 1\n" % (name, log))
            (b / name).chmod(0o755)
        return b, log

    def test_real_threads(self):
        b, log = self._fake_osa()
        extra = 'export AIB_GROKBOT_OSASCRIPT="%s/osascript" AIB_GROKBOT_OPEN="%s/open" AIB_GROKBOT_PBCOPY="%s/pbcopy"; ' % (b, b, b)
        self.bind(A)
        r = self._preflight(COCKPIT, extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(log.exists(), "綁好的 agent 不該跳視窗")
        self.assertNotIn("grok_doorbell", r.stderr.replace("grok_doorbell_", ""))
        gt.agent_wake_path(A, self.state).unlink()
        self.bind(B)          # 別的 agent 綁了,這一個沒綁 ⇒ 還是要跳
        r = self._preflight(COCKPIT, extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("osascript", log.read_text())
        self.assertIn("aib.botmenu.grok_doorbell_fail", r.stderr)


if __name__ == "__main__":
    unittest.main()
