#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""bind_one 角色衝突時要給解法(10-15 @977e3 實機:換人格撞到身份層 role=bot、席位檔 dev)。

全在 tmp 狀態根,不碰真 ~/.ez2-aib。
R1 預演(preflight_bind_agent)擋下時,訊息帶 `role_model.py switch-role <agent> <席位檔的角色>` 那一行。
R2 真綁那一條(bind_agent,預演放行後 register 才撞名)也帶同一行。
R3 照那一行的指令改完身份層,同一筆綁定就過得了預演(解法是真的解法)。
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401 —— 基因/自動綁預設關

ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src" / "identity"))
import bind_one as B  # noqa: E402
import role_model as RM  # noqa: E402

SESSION = "4a87e77a-7c69-401e-a62d-f360bac977e3"
ACTOR = B.actor_of("dev@977e3/Claude")


class RoleConflictHint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-r1-")
        self.root = pathlib.Path(self.tmp, "state")
        (self.root / "ledger").mkdir(parents=True)
        self._saved = dict(os.environ)
        os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
        self.model = RM.RoleModel(state_root=str(self.root))
        self.model.register("977e3", "bot", ACTOR)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _assert_hint(self, why):
        self.assertIn("這不是重跑,是衝突", why)
        self.assertIn("role_model.py switch-role 977e3 dev", why)

    def test_R1_preflight_gives_switch_role(self):
        state, why = B.preflight_bind_agent(self.model, "977e3", "dev", SESSION, "o1q6jp")
        self.assertEqual(state, "failed")
        self._assert_hint(why)

    def test_R2_bind_agent_register_path_gives_switch_role(self):
        with mock.patch.object(B, "preflight_bind_agent", return_value=("new", "")):
            state, why = B.bind_agent(self.model, "977e3", "dev", SESSION, "o1q6jp", ACTOR)
        self.assertEqual(state, "failed")
        self._assert_hint(why)

    def test_R3_running_the_hint_resolves_it(self):
        _, why = B.preflight_bind_agent(self.model, "977e3", "dev", SESSION, "o1q6jp")
        cmd = why.split("python3 ", 1)[1].split()
        self.assertEqual(cmd[1:4], ["switch-role", "977e3", "dev"])
        r = subprocess.run([sys.executable, cmd[0], "--state-root", str(self.root), "switch-role", "977e3", "dev",
                            "--sig", "dev@977e3/Claude", "--ai-id", "claude:host:" + SESSION],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        state, why = B.preflight_bind_agent(RM.RoleModel(state_root=str(self.root)), "977e3", "dev", SESSION, "o1q6jp")
        self.assertNotIn("是衝突", why)


if __name__ == "__main__":
    unittest.main(verbosity=2)
