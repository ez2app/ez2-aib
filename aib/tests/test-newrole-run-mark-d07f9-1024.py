#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib newrole 真的新建角色卡 ⇒ 記 state/run-marks/newrole.json = {at, uid}(動力室 SSR3 讀;10-24 Andy「依照你的建議開始」)。暫存狀態根。

N1 新卡(後面綁席失敗也算,卡已建好)⇒ 檔只有 at / uid、uid = 新卡
N2 沿用既有卡 / --overwrite 覆寫既有卡 ⇒ 不記;--dry-run ⇒ 不記
N3 power_tasks.detect:有 newrole.json ⇒ newrole;只有暫存檔 ⇒ 不算
刀(複本):K1 不記 ⇒ N1 紅;K2 覆寫既有卡也記 ⇒ N2 紅
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401 —— 基因/自動綁預設關
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

ROOT = HERE.parent
SRC = ROOT / "src" / "identity" / "create_and_bind_persona.py"
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
PROMPT = "你是測試角色。\n\n## 職責\n1. 測試\n\n## 知識域\n- 測試\n\n## 紅線\n- 不寫真庫\n"


class Mark(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-nr-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self._saved = dict(os.environ)
        for k in ("EZ2_VAULT_DIR", "EZ2_VAULT_ROOT", "EZ2_ROUTE_REGISTRY", "EZ2_ROUTE_CONTRACT_SPEC", "TMUX", "TMUX_PANE",
                  "EZ2_OWNER_SIG", "EZ2_AIB_INSTANCE", "EZ2_AIB_INSTANCE_HOME"):
            os.environ.pop(k, None)
        self.state = os.path.join(self.home, ".ez2-aib")
        os.environ.update({"HOME": self.home, "EZ2_AIB_HOME": self.state, "EZ2_AIB_STATE_ROOT": self.state,
                           "EZ2_AIB_LOCALE": "zh-TW", "EZ2_EZ2AI_REPO": os.path.join(self.tmp, "no-ez2-ai"),
                           "AIB_GENES": "0", "AIB_AUTO_BIND": "0"})
        spec = importlib.util.spec_from_file_location("cabp_nr", str(SRC))
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.mark = pathlib.Path(self.state) / "state" / "run-marks" / "newrole.json"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, extra=()):
        real = self.mod.run_cmd

        def fake_run(cmd, cwd=None, extra_env=None):
            if any(str(c).endswith("bind_one.py") for c in cmd):
                return 2, "", "bind-one: 假的綁席失敗"
            return real(cmd, cwd=cwd, extra_env=extra_env)

        argv = ["aib newrole", "--name", "測試角色", "--code", "tst", "--prompt", PROMPT, "--brain", "none",
                "--actor", "dev@abcde/claude", "--seat", "6ddf/abcde"] + list(extra)
        with contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(self.mod, "resolve_seat_arg", return_value=("abcde", {"seat_id": "abcde"}, "", None)))
            st.enter_context(mock.patch.object(self.mod, "run_cmd", side_effect=fake_run))
            st.enter_context(mock.patch.object(sys, "argv", argv))
            st.enter_context(contextlib.redirect_stderr(io.StringIO()))
            st.enter_context(contextlib.redirect_stdout(io.StringIO()))
            try:
                self.mod.main()
            except SystemExit:
                pass

    def test_n1_new_card(self):
        self._run()
        got = json.loads(self.mark.read_text(encoding="utf-8"))
        self.assertEqual(sorted(got), ["at", "uid"])
        cards = list(pathlib.Path(self.home).rglob("角色.md"))
        self.assertEqual(len(cards), 1, cards)
        self.assertEqual(cards[0].parent.name, got["uid"])
        self.assertEqual(os.listdir(self.mark.parent), ["newrole.json"])

    def test_n2_existing_and_dry_run(self):
        self._run(extra=("--dry-run",))
        self.assertFalse(self.mark.exists())
        self._run()
        self.mark.unlink()
        self._run()
        self.assertFalse(self.mark.exists(), "沿用既有卡不算新增角色")
        self._run(extra=("--overwrite",))
        self.assertFalse(self.mark.exists(), "覆寫既有卡不算新增角色")

    def test_n3_detect(self):
        import power_tasks as PT
        rm = pathlib.Path(self.state) / "state" / "run-marks"
        rm.mkdir(parents=True)
        (rm / "newrole.json.1.tmp").write_text("{}")
        self.assertNotIn("newrole", PT.detect(self.state, {}, None))
        (rm / "newrole.json").write_text("{}")
        self.assertIs(PT.detect(self.state, {}, None).get("newrole"), True)
        self.assertIn("newrole", PT.DETECTABLE)


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, case):
        name = pathlib.Path(__file__).name
        with knife_copy(ROOT, "src/identity/create_and_bind_persona.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_no_mark(self):
        self._knife("            _mark_new_role(target_card_uid)\n", "            pass\n", "Mark.test_n1_new_card")

    def test_k2_mark_existing(self):
        self._knife("        if not existed_before:\n            _mark_new_role(", "        if True:\n            _mark_new_role(",
                    "Mark.test_n2_existing_and_dry_run")


if __name__ == "__main__":
    unittest.main()
