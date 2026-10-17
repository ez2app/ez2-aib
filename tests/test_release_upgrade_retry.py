# SPDX-License-Identifier: Apache-2.0
"""從 PyPI 升級要重試(G24 後續,10-17 0.1.16 實測)。

wait_pypi_latest.sh 看到新版之後 4 秒,另一個 pip 請求仍拿到舊版(CDN 各節點不同步)
⇒ upgrade_check.sh 的 pip / pipx 從 PyPI 升級要自己重試,不能只靠前面等一次。
U1 假 pip 前兩次升級還是舊版、第三次才新版 ⇒ rc 0、印 retrying
U2 一直舊版 ⇒ 試滿 UPGRADE_TRIES 次 rc 1、印 did not reach
U3 帶本機 wheel ⇒ 不重試(只升一次)
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "upgrade_check.sh"

# 假 python:`-m venv <dir>` 建一個 <dir>/bin/python(就是這支自己);`-m pip install … --upgrade` 數一次升級;
# `-c …importlib…` 印目前版本(升級次數 >= NEW_AT 才是新版);`update_cmd.py` 回 pip 的更新指令。
FAKE = r'''#!/usr/bin/env bash
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then mkdir -p "$3/bin"; cp "$0" "$3/bin/python"; exit 0; fi
case "$*" in
  *update_cmd.py*) echo "pip install --upgrade ez2-aib"; exit 0 ;;
  *"pip install"*"--upgrade"*) n=$(cat "$COUNT" 2>/dev/null || echo 0); echo $((n+1)) > "$COUNT"; exit 0 ;;
  *"pip install"*) exit 0 ;;
  *importlib*) n=$(cat "$COUNT" 2>/dev/null || echo 0); if [ "$n" -ge "$NEW_AT" ]; then echo "$NEW"; else echo "$OLD"; fi; exit 0 ;;
esac
exit 0
'''


class UpgradeRetry(unittest.TestCase):
    def _run(self, new_at, tries=5, wheel=None):
        td = Path(tempfile.mkdtemp(prefix="upretry-"))
        bindir = td / "bin"
        bindir.mkdir()
        py = bindir / "python"
        py.write_text(FAKE, encoding="utf-8")
        py.chmod(0o755)
        env = {**os.environ, "PATH": "%s:%s" % (bindir, os.environ.get("PATH", "")), "RUNNER_TEMP": str(td),
               "COUNT": str(td / "count"), "OLD": "0.1.15", "NEW": "0.1.16", "NEW_AT": str(new_at),
               "UPGRADE_TRIES": str(tries), "UPGRADE_SLEEP": "0"}
        args = ["bash", str(SCRIPT), "pip", "0.1.15", "0.1.16"] + ([wheel] if wheel else [])
        p = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)
        calls = int((td / "count").read_text()) if (td / "count").exists() else 0
        return p, calls

    def test_u1_retries_until_new(self):
        p, calls = self._run(new_at=3)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(calls, 3)
        self.assertIn("retrying", p.stdout)
        self.assertIn("ok: pip 0.1.15 -> 0.1.16", p.stdout)

    def test_u2_gives_up(self):
        p, calls = self._run(new_at=999, tries=4)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(calls, 4)
        self.assertIn("did not reach 0.1.16", p.stdout)

    def test_u3_wheel_no_retry(self):
        p, calls = self._run(new_at=999, tries=4, wheel="/tmp/ez2_aib-0.1.16-py3-none-any.whl")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
