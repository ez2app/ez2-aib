#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""開著的艙裝外掛 / 開關待審框 ⇒ 不用重開就長出 / 收起(10-25 @abb89:Pro 開艙後才裝 pending-review,待審框沒長)。

假 run(記下每次呼叫,has-session 依名單回 0 / 1)、暫存狀態根,不碰真 tmux。
Y1 after_install("pending-review"):每個開著的艙各跑一次轉交殼 --ensure;關著的艙不跑;別的外掛名 ⇒ 不跑
Y2 跑轉交殼時只帶 EZ2_AIB_INSTANCE / EZ2_AIB_INSTANCE_HOME,拿掉 TMUX / EZ2_TMUX_SESSION / EZ2_AIHUB_SOCKET / EZ2_COCKPIT_STATE_DIR(讓 cockpit-env.sh 推導)
Y3 after_switch("pending_panel", on/off):只動這一艙(--ensure / --hide);艙沒開 ⇒ 不跑;別的開關 ⇒ 不跑
Y4 轉交殼失敗 / 逾時 / 找不到 bash ⇒ 不丟例外,回 ok False
Y5 接線:feature_policy set 實跑會叫(失敗不擋);aib plugins install <來源> 與 install <名稱> 兩條都實跑(打樁下載 / 驗簽)會叫,回條帶 live_cabins
Y6 has-session 用「=aib-<名>」完全比對;instances/ 底下短名舊夾(2 / main / 流水號)不收;主艙(main,艙家 = 狀態根)開著要收
Y7 洗艙變數用 cabin_resolve.sh --env-names-scoped 正本(權杖 / 任務夾 / 席位夾 / dm socket 都洗);每次子行程都帶 timeout
Y8 has-session 一定收起輸出(capture_output),沒開的艙不把 can't find session 印到使用者終端;艙號要 8 位-6 位-尾碼
刀:after_install 不看外掛名 ⇒ Y1 紅;sync 不拿掉 TMUX ⇒ Y2 紅;install / install_named 不接 ⇒ Y5 紅;拿掉「=」⇒ Y6 紅;拿掉 timeout ⇒ Y7 紅
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
        self.live, self.rc, self.boom, self.calls, self.kws = set(live), rc, boom, [], []

    def __call__(self, argv, env=None, **kw):
        self.calls.append((list(argv), dict(env or {})))
        self.kws.append(kw)
        if argv[:2] == ["tmux", "has-session"]:
            t = argv[-1]
            name = t[len("=aib-"):] if t.startswith("=aib-") else None   # 只認完全比對
            return subprocess.CompletedProcess(argv, 0 if name in self.live else 1, "", "")
        if argv[0] == "bash" and argv[1].endswith("cabin_resolve.sh"):
            return subprocess.CompletedProcess(argv, 1, "", "")          # 讀不到正本 ⇒ 用保底清單
        if self.boom:
            raise self.boom
        return subprocess.CompletedProcess(argv, self.rc, "", "")

    def shells(self):
        return [(a[-1], e.get("EZ2_AIB_INSTANCE"), e) for a, e in self.calls if a[0] == "bash" and a[1].endswith("pane-pending-review.sh")]


class Sync(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-pendsync-")
        self.root = Path(self.td.name) / ".ez2-aib"
        for inst in ("20261014-133230-6ddf", "20261020-000000-aaaa", "20261021-000000-bbbb", "2", "main", "13"):
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
                            for a, e in f.calls if a[0] == "bash" and a[1].endswith("pane-pending-review.sh")))
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

    def _plugins(self):
        sys.path.insert(0, str(REPO / "tools" / "verify"))
        sys.modules.pop("aib_plugins", None)
        import aib_plugins
        return aib_plugins

    def test_y5_install_paths_call_sync(self):
        AP = self._plugins()
        import aib_ext
        import ext_kinds
        import plugin_catalog as PC
        folder = Path(self.td.name) / "pkg"
        folder.mkdir()
        seen = []
        stub = lambda name: seen.append(name) or [{"cabin": "x", "ok": True, "rc": 0}]
        common = [mock.patch.object(self.S, "after_install", stub), mock.patch.object(aib_ext, "install", lambda *a, **k: 0),
                  mock.patch.object(aib_ext, "target", lambda kind, n: Path("/x") / n)]
        for c in common:
            c.start()
        try:
            with mock.patch.object(AP, "_fetch", lambda src, work: (str(folder), None)), \
                 mock.patch.object(aib_ext, "inspect", lambda f, **k: ({"name": "pending-review", "version": "1.0.0", "kind": "plugin"}, [])), \
                 mock.patch.object(ext_kinds, "kind_of_label", lambda s: "plugin"), mock.patch("sys.stdout"):
                self.assertEqual(AP.install("/somewhere", as_json=True), 0)
            with mock.patch.object(PC, "fetch_signed", lambda *a, **k: (str(folder), {"version": "1.0.0"}, None)), \
                 mock.patch.object(PC, "installed_version", lambda n: None), mock.patch.object(PC, "requires_addon", lambda f: None):
                res, rc = AP.install_named("pending-review")
        finally:
            for c in common:
                c.stop()
        self.assertEqual(seen, ["pending-review", "pending-review"])
        self.assertEqual((rc, res["detail"], res["live_cabins"]), (0, "plugin_installed", [{"cabin": "x", "ok": True, "rc": 0}]))

    def test_y6_exact_match_and_primary(self):
        f = Fake(live={"2", "main", "13"})   # 短名舊夾的 session 也開著 ⇒ 只能收主艙(艙家 = 狀態根)
        self.S.after_install("pending-review", self.env, f)
        self.assertEqual([(i, e["EZ2_AIB_INSTANCE_HOME"]) for _a, i, e in f.shells()], [("main", str(self.root))])
        self.assertTrue(all(a[-1].startswith("=aib-") for a, _e in f.calls if a[:2] == ["tmux", "has-session"]))
        env = dict(self.env, EZ2_AIB_INSTANCE_HOME=str(self.root / "instances" / "2"))
        self.assertEqual(self.S.after_switch("pending_panel", True, env, Fake(live={"2"})), [])

    def test_y7_wash_canon_and_timeout(self):
        names = self.S.wash_names()   # 真跑 cabin_resolve.sh
        for k in ("EZ2_AIB_INSTANCE_TOKEN", "EZ2_TASKS_DIR", "EZ2_SEATS_DIR", "EZ2_AIB_PANES", "AIB_DM_SOCKET", "TMUX", "TMUX_PANE",
                  "EZ2_TMUX_SESSION", "EZ2_COCKPIT_STATE_DIR"):
            self.assertIn(k, names)
        self.assertNotIn("EZ2_AIB_STATE_ROOT", names)   # 狀態根是全機的,不洗
        f = Fake(live={"20261014-133230-6ddf"})
        env = dict(self.env, EZ2_AIB_INSTANCE_TOKEN="other-cabin-token", EZ2_TASKS_DIR="/other/tasks")
        self.S.after_install("pending-review", env, f)
        (_a, _i, e), = f.shells()
        self.assertNotIn("EZ2_AIB_INSTANCE_TOKEN", e)
        self.assertNotIn("EZ2_TASKS_DIR", e)
        self.assertTrue(f.kws and all(0 < (k.get("timeout") or 0) <= 20 for k in f.kws), f.kws)

    def test_y8_quiet_and_strict_id(self):
        f = Fake(live=set())
        self.S.after_install("pending-review", self.env, f)
        hs = [k for (a, _e), k in zip(f.calls, f.kws) if a[:2] == ["tmux", "has-session"]]
        self.assertTrue(hs and all(k.get("capture_output") is True for k in hs), hs)
        self.assertTrue(self.S.is_cabin_id("20261014-133230-6ddf"))
        for bad in ("a-b-c", "2", "main", "2026-1014-6ddf", "20261014-133230-6ddf-x"):
            self.assertFalse(self.S.is_cabin_id(bad), bad)

    def test_y5_feature_policy_wiring(self):
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
