#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""開著的艙裝外掛 / 開關待審框 ⇒ 不用重開就長出 / 收起(10-25 @abb89:Pro 開艙後才裝 pending-review,待審框沒長)。

假 run(記下每次呼叫,has-session 依名單回 0 / 1)、暫存狀態根,不碰真 tmux。
Y1 after_install("pending-review"):每個開著的艙各跑一次轉交殼 --ensure;關著的艙不跑;別的外掛名 ⇒ 不跑
Y2 跑轉交殼時只帶 EZ2_AIB_INSTANCE / EZ2_AIB_INSTANCE_HOME,拿掉 TMUX / EZ2_TMUX_SESSION / EZ2_AIHUB_SOCKET / EZ2_COCKPIT_STATE_DIR(讓 cockpit-env.sh 推導)
Y3 after_switch("pending_panel", on/off):只動這一艙(--ensure / --hide);艙沒開 ⇒ 不跑;別的開關 ⇒ 不跑
Y4 轉交殼失敗 / 逾時 / 找不到 bash ⇒ 不丟例外,回 ok False
Y5 接線:feature_policy set 與 aib plugins install 都會叫(失敗不擋;原始碼位置 + feature_policy 實跑)
刀:after_install 不看外掛名 ⇒ Y1 紅;sync 不拿掉 TMUX ⇒ Y2 紅
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))


class Fake:
    def __init__(self, live, rc=0, boom=None):
        self.live, self.rc, self.boom, self.calls = set(live), rc, boom, []

    def __call__(self, argv, env=None, **kw):
        self.calls.append((list(argv), dict(env or {})))
        if argv[:2] == ["tmux", "has-session"]:
            return subprocess.CompletedProcess(argv, 0 if argv[-1][len("aib-"):] in self.live else 1, "", "")
        if self.boom:
            raise self.boom
        return subprocess.CompletedProcess(argv, self.rc, "", "")

    def shells(self):
        return [(a[-1], e.get("EZ2_AIB_INSTANCE"), e) for a, e in self.calls if a[0] == "bash"]


class Sync(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-pendsync-")
        self.root = Path(self.td.name) / ".ez2-aib"
        for inst in ("20261014-133230-6ddf", "20261020-000000-aaaa", "20261021-000000-bbbb"):
            (self.root / "instances" / inst).mkdir(parents=True)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.root), "HOME": self.td.name, "PATH": os.environ.get("PATH", ""),
                    "TMUX": "/private/tmp/tmux-501/aihub-x,1,2", "TMUX_PANE": "%9", "EZ2_TMUX_SESSION": "aib-wrong",
                    "EZ2_AIHUB_SOCKET": "aihub-wrong", "EZ2_COCKPIT_STATE_DIR": "/wrong"}
        for m in ("pending_pane_sync", "feature_policy"):
            sys.modules.pop(m, None)
        import pending_pane_sync
        self.S = pending_pane_sync

    def tearDown(self):
        self.td.cleanup()

    def test_y1_install_live_only(self):
        f = Fake(live={"20261014-133230-6ddf", "20261021-000000-bbbb"})
        res = self.S.after_install("pending-review", self.env, f)
        self.assertEqual([(a, i) for a, i, _e in f.shells()], [("--ensure", "20261014-133230-6ddf"), ("--ensure", "20261021-000000-bbbb")])
        self.assertTrue(all(r["ok"] for r in res) and len(res) == 2, res)
        self.assertTrue(all(a[1] == str(self.S.SHELL) and a[1].endswith("src/cockpit/pane-pending-review.sh")
                            for a, e in f.calls if a[0] == "bash"))
        f2 = Fake(live={"20261014-133230-6ddf"})
        self.assertEqual(self.S.after_install("captain-room", self.env, f2), [])
        self.assertEqual(f2.calls, [])

    def test_y2_env_derived(self):
        f = Fake(live={"20261014-133230-6ddf"})
        self.S.after_install("pending-review", self.env, f)
        (_a, _i, e), = f.shells()
        self.assertEqual(e["EZ2_AIB_INSTANCE_HOME"], str(self.root / "instances" / "20261014-133230-6ddf"))
        for k in ("TMUX", "TMUX_PANE", "EZ2_TMUX_SESSION", "EZ2_AIHUB_SOCKET", "EZ2_COCKPIT_STATE_DIR"):
            self.assertNotIn(k, e)
        self.assertTrue(all("TMUX" not in env for a, env in f.calls if a[0] == "tmux"))   # 查外層 session 不能讀內層 socket

    def test_y3_switch_this_cabin(self):
        env = dict(self.env, EZ2_AIB_INSTANCE_HOME=str(self.root / "instances" / "20261014-133230-6ddf"))
        f = Fake(live={"20261014-133230-6ddf", "20261021-000000-bbbb"})
        self.S.after_switch("pending_panel", True, env, f)
        self.S.after_switch("pending_panel", False, env, f)
        self.assertEqual([(a, i) for a, i, _e in f.shells()], [("--ensure", "20261014-133230-6ddf"), ("--hide", "20261014-133230-6ddf")])
        f2 = Fake(live=set())
        self.assertEqual(self.S.after_switch("pending_panel", True, env, f2), [])
        self.assertEqual(f2.shells(), [])
        f3 = Fake(live={"20261014-133230-6ddf"})
        self.assertEqual(self.S.after_switch("task_zone", True, env, f3), [])
        self.assertEqual(f3.calls, [])
        self.assertEqual(self.S.after_switch("pending_panel", True, self.env, Fake(live={"20261014-133230-6ddf"})), [])   # 沒艙家

    def test_y4_failures_never_raise(self):
        for f in (Fake(live={"20261014-133230-6ddf"}, rc=2), Fake(live={"20261014-133230-6ddf"}, boom=subprocess.TimeoutExpired("bash", 20)),
                  Fake(live={"20261014-133230-6ddf"}, boom=OSError("no bash"))):
            res = self.S.after_install("pending-review", self.env, f)
            self.assertEqual([r["ok"] for r in res], [False], res)

    def test_y5_wiring(self):
        src = (REPO / "src" / "cockpit" / "feature_policy.py").read_text(encoding="utf-8")
        self.assertIn("pending_pane_sync.after_switch(args.feature", src)
        src = (REPO / "tools" / "verify" / "aib_plugins.py").read_text(encoding="utf-8")
        self.assertIn('pending_pane_sync.after_install(m["name"])', src)
        home = self.root / "instances" / "20261014-133230-6ddf"
        env = {k: v for k, v in self.env.items()}
        env["EZ2_AIB_INSTANCE_HOME"] = str(home)
        seen = []
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(self.S, "after_switch", lambda *a, **k: seen.append(a) or []):
            import feature_policy
            with mock.patch("sys.stdout"):
                rc = feature_policy._cli(["set", "pending_panel", "on"])
        self.assertEqual((rc, seen), (0, [("pending_panel", True)]))
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(self.S, "after_switch", side_effect=RuntimeError("x")):
            with mock.patch("sys.stdout"):
                self.assertEqual(feature_policy._cli(["set", "pending_panel", "off"]), 0)   # 失敗不擋開關


if __name__ == "__main__":
    unittest.main()
