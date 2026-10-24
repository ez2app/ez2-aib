#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib test 判紅「碰到真的家目錄」的測試(開發文件第 37.1 節;10-17 test-chat-roster 讀到 Pro 真的收信站設定、
10-18 ez2-ai 測試改寫真的 API key 檔)。AIB_TEST_NOSTR_VENV 也放行,連 Nostr 的測試不必再「直接跑」(那條路沒假 HOME)。

G1 real_home_touches:寫 / 讀敏感位置(.ez2-aib、.claude、.ez2-claude-secrets.json、.codex、.ez2-persona-runtime…)都算;
   線上對話 venv 的讀不算(寫照算);aib test 自己的沿用紀錄池(verify-shadow)不算;家目錄底下別的(例如倉庫本身)不算;
   real/path 兩個欄位都看
G2 入口(治具倉,AIB_TEST_REAL_HOME = 假的「真家目錄」):寫進去的那支 ⇒ 紅、輸出說哪個檔;只讀 venv 的 ⇒ 綠;
   檔頭有 @needs-real-home ⇒ 不檢查(綠)
G3 clean_test_env 放行 AIB_TEST_NOSTR_VENV(其他 AIB_* 照舊不放行)
刀(複本):K1 runner 不檢查 ⇒ G2 紅
"""
import importlib.util
import json
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
import shadow_tracer as st  # noqa: E402
import runner  # noqa: E402

_spec = importlib.util.spec_from_file_location("aib_test_entry_rh", str(HERE / "test-aib-test-entry-0929.py"))
TE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(TE)

WRITE_TEST = '''import os, unittest
class T(unittest.TestCase):
    def test_leak(self):
        os.makedirs(%(d)r, exist_ok=True)
        open(os.path.join(%(d)r, "friends.json"), "w").write("{}")
if __name__ == "__main__":
    unittest.main()
'''
VENV_TEST = '''import os, unittest
class T(unittest.TestCase):
    def test_venv(self):
        self.assertEqual(open(%(f)r).read(), "py")
if __name__ == "__main__":
    unittest.main()
'''


class Pure(unittest.TestCase):
    def test_g1_touches(self):
        h = "/Users/x"
        evs = [{"ch": "write", "path": h + "/.ez2-aib/chat/friends.json"},
               {"ch": "open", "path": "/private/tmp/t/.ez2-aib/chat/profile.json", "real": h + "/.ez2-aib/chat/profile.json"},
               {"ch": "open", "path": h + "/.ez2-aib/chat/venv/bin/python3"},
               {"ch": "write", "path": h + "/.ez2-aib/chat/venv/x"},
               {"ch": "open", "path": h + "/ez2-aib/aib/VERSION"},
               {"ch": "open", "path": h + "/.ez2-claude-secrets.json"},
               {"ch": "write", "path": h + "/.codex/tok"},
               {"ch": "write", "path": h + "/.ez2-aib/verify-shadow/pool.rows.jsonl"},
               {"ch": "open", "path": h + "/.ez2-aib/verify-shadow/pool.rows.jsonl"},
               {"ch": "argv", "argv": ["ls", h + "/.ssh"]}]
        self.assertEqual(st.real_home_touches(evs, h), [
            ("read", h + "/.ez2-aib/chat/profile.json"), ("read", h + "/.ez2-claude-secrets.json"),
            ("write", h + "/.codex/tok"), ("write", h + "/.ez2-aib/chat/friends.json"), ("write", h + "/.ez2-aib/chat/venv/x")])
        self.assertEqual(st.real_home_touches(evs, ""), [])

    def test_g3_env(self):
        old = dict(os.environ)
        try:
            os.environ.update({"AIB_TEST_NOSTR_VENV": "/v/venv", "AIB_CHAT_HOME": "/real/chat"})
            env = runner.clean_test_env("/tmp/h")
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(env.get("AIB_TEST_NOSTR_VENV"), "/v/venv")
        self.assertNotIn("AIB_CHAT_HOME", env, "其他 AIB_* 照舊不放行")


class Entry(unittest.TestCase):
    def test_g2_entry(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            real = td / "realhome"
            (real / ".ez2-aib" / "chat" / "venv").mkdir(parents=True)
            (real / ".ez2-aib" / "chat" / "venv" / "py").write_text("py")
            fx = TE._fixture(td)
            (fx / "tests" / "test-leak.py").write_text(WRITE_TEST % {"d": str(real / ".ez2-aib" / "chat")})
            (fx / "tests" / "test-venv.py").write_text(VENV_TEST % {"f": str(real / ".ez2-aib" / "chat" / "venv" / "py")})
            (fx / "tests" / "test-marked.py").write_text("# @needs-real-home(審查用)\n" + WRITE_TEST % {"d": str(real / ".codex")})
            genv = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(td), **TE.GIT_ENV}
            for argv in (["git", "add", "-A"], ["git", "commit", "-q", "-m", "more"]):
                subprocess.run(argv, cwd=str(fx), env=genv, check=True, capture_output=True)
            env = TE._env(td, AIB_TEST_REAL_HOME=str(real))
            p = subprocess.run([sys.executable, str(fx / TE.ENTRY_REL), "tests/test-leak.py", "tests/test-venv.py",
                                "tests/test-marked.py"], cwd=str(fx), env=env, capture_output=True, text=True, timeout=600)
            out = p.stdout + p.stderr
            self.assertNotEqual(p.returncode, 0, out)
            self.assertIn("碰到真的家目錄", out, out)
            self.assertIn("friends.json", out)
            lines = {ln.split("tests/")[1].split(".py")[0]: ln for ln in out.splitlines() if "└" in ln and "tests/test-" in ln}
            self.assertIn("紅", lines.get("test-leak", ""), out)
            self.assertIn("綠", lines.get("test-venv", ""), "只讀 venv 不算")
            self.assertIn("綠", lines.get("test-marked", ""), "@needs-real-home 不檢查")


class Knife(unittest.TestCase):
    def test_k1_no_check(self):
        name = Path(__file__).name
        with knife_copy(REPO, "tools/verify/runner.py",
                        "        if touched:                                   # 第 37.1 節",
                        "        if False:                                     # 第 37.1 節",
                        tests=(name, "test-aib-test-entry-0929.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Entry.test_g2_entry"], cwd=str(root),
                            max_procs=200, timeout=900)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
