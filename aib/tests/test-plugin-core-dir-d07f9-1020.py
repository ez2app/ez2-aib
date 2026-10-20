#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib 跑外掛子指令時把自己的核心資料夾帶成 AIB_CORE_DIR(10-20 Pro 13:git 原始碼裝的 aib 不是 import 得到的套件,
captain-room 的 room.py 找不到核心 ⇒ `aib room` 報「cannot find the aib this plugin belongs to」)。

沙盒 HOME 裝一支假外掛 probe(commands: aibprobe),它照 room.py 的找法:AIB_CORE_DIR 底下要有 src/cockpit/cabin_restart.py。
C1 原始碼裝(這個倉的 cli.py):外掛拿到的 AIB_CORE_DIR = 倉的 aib 資料夾,找得到核心
C2 裝在別處(仿 pip:套件資料夾複製到 site/aib):AIB_CORE_DIR = 那份套件資料夾,不是倉
C3 環境裡留著別處的 AIB_CORE_DIR / EZ2_AIB_ROOT ⇒ 蓋掉，用正在跑的這個 aib
    (EZ2_AIB_ROOT:外掛再叫的 seat-info.py 靠它找駕駛艙模組;10-20 Pro 13 從 ssh 起的艦長室少了它 ⇒ 一席都列不出來)
刀(複本):K1 拿掉那一行 ⇒ C1 紅
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

PROBE = r'''
import json, os
core = os.environ.get("AIB_CORE_DIR")
ok = bool(core) and os.path.isfile(os.path.join(core, "src/cockpit/cabin_restart.py"))
print(json.dumps({"core": core, "ok": ok, "root": os.environ.get("EZ2_AIB_ROOT")}))
'''


class CoreDir(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="aib-coredir-")
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.home = self.tmp / "home"
        plug = self.home / ".ez2-aib" / "plugins" / "probe"
        plug.mkdir(parents=True)
        (plug / "plugin.json").write_text(json.dumps({"name": "probe", "version": "0.0.1", "module": "probe.py",
                                                      "commands": {"aibprobe": "probe.py"}}))
        (plug / "probe.py").write_text(PROBE)

    def run_aib(self, pkg_parent, extra_env=None):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home), "LANG": "en_US.UTF-8",
               "EZ2_AIB_STATE_ROOT": str(self.tmp / "state")}
        env.update(extra_env or {})
        code = "import sys; sys.path.insert(0, sys.argv[1]); sys.argv = ['aib', 'aibprobe']; from aib.cli import main; main()"
        p = subprocess.run([sys.executable, "-I", "-c", code, str(pkg_parent)], capture_output=True, text=True,
                           timeout=60, env=env, stdin=subprocess.DEVNULL, cwd=str(self.tmp))
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return json.loads(p.stdout.strip().splitlines()[-1])

    def checkout_parent(self):
        """sys.path 要的是「裝著 aib 資料夾的那層」;刀測複本的樹根不叫 aib ⇒ 一律接一個 aib 捷徑(cli.py 用 realpath,認得回樹根)。"""
        par = self.tmp / "src-parent"
        par.mkdir(exist_ok=True)
        if not (par / "aib").exists():
            (par / "aib").symlink_to(ROOT, target_is_directory=True)
        return par

    def test_c1_source_checkout(self):
        got = self.run_aib(self.checkout_parent())
        self.assertEqual(Path(got["core"]).resolve(), ROOT.resolve())
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["root"], got["core"], "seat-info 用的 EZ2_AIB_ROOT 也要一起帶")

    def test_c2_installed_elsewhere(self):
        site = self.tmp / "site" / "aib"
        for rel in ("__init__.py", "cli.py", "tools/verify/aib_plugins.py", "src/cockpit/cabin_restart.py"):
            (site / rel).parent.mkdir(parents=True, exist_ok=True)
            src = ROOT / rel
            if src.is_file():
                shutil.copy(src, site / rel)
            else:
                (site / rel).write_text("")
        got = self.run_aib(site.parent)
        self.assertEqual(Path(got["core"]).resolve(), site.resolve())
        self.assertTrue(got["ok"], got)

    def test_c3_stale_env_overridden(self):
        got = self.run_aib(self.checkout_parent(), {"AIB_CORE_DIR": str(self.tmp / "old-aib"),
                                                     "EZ2_AIB_ROOT": str(self.tmp / "old-aib")})
        self.assertEqual(Path(got["core"]).resolve(), ROOT.resolve())
        self.assertEqual(Path(got["root"]).resolve(), ROOT.resolve())


class Knife(unittest.TestCase):
    def test_k1_no_core_dir(self):
        name = Path(__file__).name
        with knife_copy(ROOT, "cli.py", '            os.environ["AIB_CORE_DIR"] = HERE\n', "",
                        tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "CoreDir.test_c1_source_checkout"],
                            cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
