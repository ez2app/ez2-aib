#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""外掛子指令要知道核心在哪(10-20 Pro 13:從原始碼 bin/aib 跑,`aib room` 報「cannot find the aib this plugin belongs to」)。

bin/aib 只在自己的行程把倉根放進 sys.path;外掛是 execv 起的新行程,import 不到 aib ⇒ 外掛的 core_dir() 拿不到。
C1 從原始碼跑外掛子指令 ⇒ 外掛看到 AIB_CORE_DIR = 核心套件資料夾(裡面有 src/cockpit)。
C2 呼叫端自己設了 AIB_CORE_DIR ⇒ 照它的,不蓋掉。
刀:拿掉 cli.py 那行 setdefault ⇒ C1 紅。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT.parent / "bin" / "aib"
PROBE = 'import json, os\nprint(json.dumps({"core": os.environ.get("AIB_CORE_DIR")}))\n'


class PluginCoreDir(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        home = Path(self._td.name)
        d = home / ".ez2-aib" / "plugins" / "probe-core"
        d.mkdir(parents=True)
        (d / "plugin.json").write_text(json.dumps({"name": "probe-core", "module": "probe.py", "commands": {"probe-core-dir": "probe.py"}}), encoding="utf-8")
        (d / "probe.py").write_text(PROBE, encoding="utf-8")
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "EZ2_AIB_HOME": str(home / ".ez2-aib"), "LANG": "en_US.UTF-8"}

    def run_probe(self, **extra):
        p = subprocess.run([sys.executable, str(BIN), "probe-core-dir"], capture_output=True, text=True, timeout=60,
                           env=dict(self.env, **extra), stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        return json.loads(p.stdout.strip().splitlines()[-1])["core"]

    def test_C1_source_run_tells_plugin_core(self):
        core = self.run_probe()
        self.assertEqual(Path(core).resolve(), ROOT.resolve())
        self.assertTrue((Path(core) / "src" / "cockpit" / "cabin_restart.py").is_file())

    def test_C2_caller_value_wins(self):
        self.assertEqual(self.run_probe(AIB_CORE_DIR="/opt/elsewhere"), "/opt/elsewhere")


if __name__ == "__main__":
    unittest.main()
