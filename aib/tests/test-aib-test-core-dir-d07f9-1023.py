#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib test 對外掛倉保留 AIB_CORE_DIR(10-23 @74e8b 回報 aib-plugins captain-room/tests 38/135 紅 No module named
'seat_address' / 'roles':乾淨環境把 AIB_CORE_DIR 一起洗掉,外掛測試找不到核心;@abb89 派)。暫存狀態根、治具倉。

D1 不是核心倉:沒設 ⇒ 帶正在跑的核心(runner.ROOT);設了絕對路徑資料夾 ⇒ 照抄;相對路徑 / 不存在 / 是檔案 ⇒ 改帶 ROOT
D2 核心倉(倉根 = aib/ 或上一層):照舊不帶,就算環境有設
D3 其他 AIB_* 照舊不放行(AIB_CHAT_HOME、AIB_SEAT_CODE、AIB_TOUCHID、AIB_CORE_DIRX…)
D4 入口(治具倉 = 外掛倉的樣子):測試靠 AIB_CORE_DIR 匯入核心模組 ⇒ 綠;AIB_CHAT_HOME 進不去
刀(複本):K1 不帶 AIB_CORE_DIR ⇒ D4 紅
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "verify"))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import runner  # noqa: E402

_spec = importlib.util.spec_from_file_location("aib_test_entry_cd", str(HERE / "test-aib-test-entry-0929.py"))
TE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(TE)

OTHERS = {"AIB_CHAT_HOME": "/real/chat", "AIB_SEAT_CODE": "zzzzz", "AIB_TOUCHID": "0", "AIB_CORE_DIRX": "/x"}

PLUGIN_TEST = '''import os, sys, unittest
class T(unittest.TestCase):
    def test_core(self):
        core = os.environ.get("AIB_CORE_DIR")
        self.assertTrue(core, "沒有 AIB_CORE_DIR")
        sys.path.insert(0, os.path.join(core, "src", "cockpit"))
        import fake_core_mod
        self.assertEqual(fake_core_mod.WHO, "core")
        self.assertNotIn("AIB_CHAT_HOME", os.environ)
        self.assertNotIn("AIB_SEAT_CODE", os.environ)
if __name__ == "__main__":
    unittest.main()
'''


def _env_with(extra, home, repo):
    old = dict(os.environ)
    try:
        for k in ("AIB_CORE_DIR",) + tuple(OTHERS):
            os.environ.pop(k, None)
        os.environ.update(extra)
        return runner.clean_test_env(home, repo)
    finally:
        os.environ.clear()
        os.environ.update(old)


class Pure(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-coredir-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name).resolve()
        self.plug = self.td / "aib-plugins"
        (self.plug / "captain-room" / "tests").mkdir(parents=True)
        self.core = self.td / "core"
        (self.core / "src" / "cockpit").mkdir(parents=True)
        self.home = str(self.td / "h")

    def test_d1_plugin_tree(self):
        env = _env_with({}, self.home, self.plug)
        self.assertEqual(env.get("AIB_CORE_DIR"), str(runner.ROOT))
        env = _env_with({"AIB_CORE_DIR": str(self.core)}, self.home, self.plug)
        self.assertEqual(env.get("AIB_CORE_DIR"), str(self.core))
        f = self.td / "file"
        f.write_text("x")
        for bad in ("relative/aib", str(self.td / "missing"), str(f), ""):
            env = _env_with({"AIB_CORE_DIR": bad}, self.home, self.plug)
            self.assertEqual(env.get("AIB_CORE_DIR"), str(runner.ROOT), bad)

    def test_d2_core_tree(self):
        top = self.td / "ez2-aib"
        for root, marker in ((self.core, self.core), (top, top / "aib")):
            (marker / "src" / "cockpit").mkdir(parents=True, exist_ok=True)
            (marker / "src" / "cockpit" / "aib").write_text("#!/bin/sh\n")
            env = _env_with({"AIB_CORE_DIR": str(self.plug)}, self.home, root)
            self.assertNotIn("AIB_CORE_DIR", env, root)
        self.assertNotIn("AIB_CORE_DIR", _env_with({}, self.home, REPO), "本倉自己照舊不帶")

    def test_d3_other_aib_vars_blocked(self):
        for repo in (self.plug, REPO):
            env = _env_with(dict(OTHERS, AIB_CORE_DIR=str(self.core)), self.home, repo)
            for k in OTHERS:
                self.assertNotIn(k, env, (repo, k))
            passthru = ("AIB_TEST_PYTHON", "AIB_TEST_NOSTR_VENV")   # runner.py 本來就照抄的(跑全套時外層會帶 AIB_TEST_NOSTR_VENV)
            self.assertEqual(sorted(k for k in env if k.startswith("AIB_") and k not in passthru),
                             [] if repo == REPO else ["AIB_CORE_DIR"], repo)


class Entry(unittest.TestCase):
    def test_d4_entry_plugin_repo(self):
        with tempfile.TemporaryDirectory(prefix="aib-coredir-e-") as td:
            td = Path(td)
            fx = TE._fixture(td)
            (fx / "src" / "cockpit" / "fake_core_mod.py").write_text('WHO = "core"\n', encoding="utf-8")
            (fx / "tests" / "test-plugin.py").write_text(PLUGIN_TEST, encoding="utf-8")
            genv = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(td), **TE.GIT_ENV}
            for argv in (["git", "add", "-A"], ["git", "commit", "-q", "-m", "plugin"]):
                subprocess.run(argv, cwd=str(fx), env=genv, check=True, capture_output=True)
            env = TE._env(td, EZ2_AIB_STATE_ROOT=str(td / "state"), **OTHERS)
            env.pop("AIB_CORE_DIR", None)
            p = subprocess.run([sys.executable, str(fx / TE.ENTRY_REL), "--fresh", "tests/test-plugin.py"],
                               cwd=str(fx), env=env, capture_output=True, text=True, timeout=600)
            out = p.stdout + p.stderr
            self.assertEqual(p.returncode, 0, out)
            line = next((ln for ln in out.splitlines() if "tests/test-plugin" in ln and "└" in ln), "")
            self.assertIn("綠", line, out)


class Knife(unittest.TestCase):
    def test_k1_core_dir_dropped(self):
        name = Path(__file__).name
        with knife_copy(REPO, "tools/verify/runner.py", "    if not _is_core_tree(root):\n", "    if False:\n",
                        tests=(name, "test-aib-test-entry-0929.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Entry.test_d4_entry_plugin_repo"],
                            cwd=str(root), max_procs=200, timeout=900)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
