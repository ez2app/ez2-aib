# SPDX-License-Identifier: Apache-2.0
"""release.yml 的「從 PyPI 升級」要先等 PyPI 把新版當最新版給出來(G24,10-17)。

剛上傳時 CDN 會有幾分鐘還把上一版當最新,沒釘版的 `pipx upgrade` / `pip install --upgrade` 就停在舊版
⇒ verify 紅「did not reach」(0.1.11、0.1.14、0.1.15 各手動重跑過)。
W1 假 pip 前兩次給舊版、第三次給新版 ⇒ 等到、rc 0
W2 一直給舊版 ⇒ 試滿 TRIES 次 rc 1、印 ::error::
W3 release.yml 那一步在升級迴圈之前先呼叫 wait_pypi_latest.sh
"""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "wait_pypi_latest.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

FAKE_PY = r'''#!/usr/bin/env bash
# 假 python:只認 `-m pip download ... -d <dir> ez2-aib`;依呼叫次數決定給哪一版
n=$(cat "$COUNT" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$COUNT"
d=""; prev=""; for a in "$@"; do [ "$prev" = "-d" ] && d=$a; prev=$a; done
if [ "$n" -ge "${NEW_AT:-999}" ]; then ver=$NEW; else ver=$OLD; fi
touch "$d/ez2_aib-$ver-py3-none-any.whl"
'''


class WaitPypiLatest(unittest.TestCase):
    def _run(self, new_at, tries=5):
        td = Path(tempfile.mkdtemp(prefix="waitpypi-"))
        py = td / "python"
        py.write_text(FAKE_PY, encoding="utf-8")
        py.chmod(0o755)
        env = {**os.environ, "PY": str(py), "TRIES": str(tries), "SLEEP": "0", "RUNNER_TEMP": str(td),
               "COUNT": str(td / "count"), "OLD": "0.1.14", "NEW": "0.1.15", "NEW_AT": str(new_at)}
        p = subprocess.run(["bash", str(SCRIPT), "0.1.15"], capture_output=True, text=True, env=env, timeout=60)
        return p, int((td / "count").read_text())

    def test_w1_waits_until_new_is_served(self):
        p, calls = self._run(new_at=3)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(calls, 3)
        self.assertIn("serves ez2-aib 0.1.15 as the newest", p.stdout)

    def test_w2_gives_up_with_error(self):
        p, calls = self._run(new_at=999, tries=4)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(calls, 4)
        self.assertIn("::error::", p.stdout)

    def test_w3_workflow_waits_before_upgrades(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        step = text.split("upgrade from the previous and the oldest release (pipx, pip from PyPI)", 1)[1]
        step = step.split("- name:", 1)[0]
        wait = step.find("wait_pypi_latest.sh")
        loop = step.find("upgrade_check.sh")
        self.assertGreater(wait, -1, "PyPI 升級那一步沒有先等 wait_pypi_latest.sh")
        self.assertLess(wait, loop)


if __name__ == "__main__":
    unittest.main()
