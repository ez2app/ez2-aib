#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""艙門不放一次性的接回 / 分出旗標進 tmux 伺服器(10-24 @abb89,右鍵另開 AI 實測)。

事故:艙重開時 cabin_restart 帶 AIB_REVIVE_SESSION_UUID=<舊席> 跑 aib,那一次剛好生出 tmux 伺服器,
值就留在伺服器全域環境;之後每個 run-shell(右鍵另開 AI)都想原地復活那一席,活著就被擋。
這裡只問 identity-env.sh 的 aib_env_door_names:這幾個名字不准出現,一般的 EZ2_ / AIB_ 照帶。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IDENTITY_ENV = REPO / "src" / "cockpit" / "identity-env.sh"

ONESHOT = ("AIB_REVIVE_SESSION_UUID", "AIB_RESUME_SESSION_UUID", "AIB_RESUME_UUID",
           "AIB_RESUME_FORCE", "AIB_FORK_FROM_SESSION_UUID")


def door_names(extra_env):
    with tempfile.TemporaryDirectory() as tmp:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tmp,
               "EZ2_AIB_STATE_ROOT": tmp, "EZ2_AIB_HOME": tmp}
        env.update(extra_env)
        script = 'source "$1"; aib_env_door_names'
        r = subprocess.run(["bash", "-c", script, "bash", str(IDENTITY_ENV)], env=env,
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise AssertionError("identity-env.sh 跑不起來:%s" % r.stderr)
        return set(r.stdout.split())


class DoorOneshot(unittest.TestCase):
    def test_oneshot_resume_vars_do_not_pass(self):
        uuid = "3cc3991a-9491-4590-ab95-aa3762f74e8b"
        env = {n: (uuid if n.endswith("UUID") and n != "AIB_RESUME_UUID" else "1") for n in ONESHOT}
        names = door_names(env)
        for n in ONESHOT:
            self.assertNotIn(n, names, n)

    def test_ordinary_cabin_vars_still_pass(self):
        names = door_names({"EZ2_VAULT_DIR": "/tmp/v", "AIB_QUIET": "1",
                            "AIB_REVIVE_SESSION_UUID": "x"})
        self.assertIn("EZ2_VAULT_DIR", names)   # EZ2_AIB_INSTANCE 本來就洗(開艙時另外明寫)
        self.assertIn("AIB_QUIET", names)
        self.assertNotIn("AIB_REVIVE_SESSION_UUID", names)

    def test_seat_env_still_carries_resume_explicitly(self):
        # 新席要接回時靠 AIB_SEAT_ENV 明寫帶過去,不靠伺服器環境 —— 這條線還在才可以擋門
        aib = (REPO / "src" / "cockpit" / "aib").read_text(encoding="utf-8")
        self.assertIn("AIB_RESUME_UUID=%q AIB_RESUME_SESSION_UUID=%q", aib)


if __name__ == "__main__":
    unittest.main()
