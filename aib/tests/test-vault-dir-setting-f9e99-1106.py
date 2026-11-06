#!/usr/bin/env python3
"""11-06 dev@f9e99:vault 位置收斂到 route_register.configured_vault 一處,多一層 <aib 家>/vault-dir 設定檔。

背景:舊預設 ~/Desktop/Obsidian/Ez2 是空殼、已改名;沒繼承 EZ2_VAULT_DIR 的行程會安靜落到
~/.ez2-aib/vault,brain_write_plan.vault_root() 回 None(閘〇失去比對目標)。
V1 設定檔指向真資料夾 ⇒ configured/default/vault_root 三者都回它
V2 環境變數仍優先於設定檔
V3 設定檔是相對路徑或不存在的資料夾 ⇒ 不採用(configured=None,default 退 aib 預設)
V4 cockpit-env.sh 的 shell 版與 Python 版同序(設定檔生效)
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "persona_route"))
sys.path.insert(0, str(ROOT / "src" / "memory"))
import route_register as RR  # noqa: E402

KEYS = ("EZ2_VAULT_DIR", "EZ2_VAULT_ROOT", "EZ2_AIB_HOME", "HOME")


class VaultDirSetting(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in KEYS}
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.aib = self.home / ".ez2-aib"
        self.real = Path(self.tmp.name) / "真庫"
        self.aib.mkdir(parents=True)
        self.real.mkdir()
        for k in ("EZ2_VAULT_DIR", "EZ2_VAULT_ROOT"):
            os.environ.pop(k, None)
        os.environ["HOME"] = str(self.home)
        os.environ["EZ2_AIB_HOME"] = str(self.aib)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _set(self, text):
        (self.aib / "vault-dir").write_text(text, encoding="utf-8")

    def test_v1_setting_file_used(self):
        self._set(str(self.real) + "\n")
        self.assertEqual(RR.configured_vault(), str(self.real))
        self.assertEqual(RR.default_vault(), str(self.real))
        import brain_write_plan
        self.assertEqual(brain_write_plan.vault_root(), self.real)

    def test_v2_env_wins(self):
        self._set(str(self.real))
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        os.environ["EZ2_VAULT_DIR"] = str(other)
        self.assertEqual(RR.default_vault(), str(other))

    def test_v3_bad_setting_ignored(self):
        self._set("relative/path\n")
        self.assertIsNone(RR.configured_vault())
        self._set(str(self.real / "missing"))
        self.assertIsNone(RR.configured_vault())
        self.assertEqual(RR.default_vault(), str(self.aib / "vault"))

    def test_v4_shell_same_order(self):
        self._set(str(self.real) + "\n")
        src = (ROOT / "src" / "cockpit" / "cockpit-env.sh").read_text(encoding="utf-8")
        start = src.index('_aib_vault_setting=""')
        end = src.index("export EZ2_VAULT_DIR", start) + len("export EZ2_VAULT_DIR")
        script = src[start:end] + '\nprintf "%s" "$EZ2_VAULT_DIR"\n'
        env = {"HOME": str(self.home), "EZ2_AIB_HOME": str(self.aib), "PATH": os.environ.get("PATH", "")}
        out = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, str(self.real))


if __name__ == "__main__":
    unittest.main()
