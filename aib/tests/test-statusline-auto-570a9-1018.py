#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""狀態列跟著 aib 自動換新(10-17 the maintainer「以後都應該自動更新」;@abb89 18:35 交辦五點)。全在 tmp HOME。

S1  家目錄是 aib 發過的舊版 ⇒ --auto 換成這一版、先備份
S2  使用者改過的 ⇒ 不動、印一行手動換法、rc 0
S3  AIB_STATUSLINE_AUTO=0 ⇒ 舊版也不動、一個位元組都不寫
S4  重跑不重複:第二次零寫入、不多備份;codex 的 hooks.json / config.toml 一個位元組都不變
S5  沒在用的那家不新建(只有 claude ⇒ 不建 ~/.codex);在用的那家缺的那支會補上;空的 HOME 什麼都不建
S6  aib init 結尾會跑;安裝器失敗只警告,init 照樣成功
S7  KNOWN-SHAS.json 收了每支來源現在這一版(改腳本沒重產清單 ⇒ 紅,下一版就認不出這一版)
刀(複本):K1 不認指紋、差的都換 ⇒ S2 紅;K2 拿掉 AIB_STATUSLINE_AUTO=0 ⇒ S3 紅;K3 沒在用的也建 ⇒ S5 紅
"""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

ROOT = HERE.parent
THIS = pathlib.Path(__file__).name
PY = sys.executable
TOOL = ROOT / "tools" / "statusline-install.sh"
V215 = "deploy/statusline/claude/statusline-ez2-v215.sh"


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def tree(home):
    """{相對路徑: (sha, mtime_ns)}"""
    out = {}
    for p in pathlib.Path(home).rglob("*"):
        if p.is_file():
            out[str(p.relative_to(home))] = (sha(p), p.stat().st_mtime_ns)
    return out


def old_v215():
    """v215.sh 上一版(git 歷史裡的,一定在 KNOWN-SHAS.json);拿不到 ⇒ None。"""
    try:
        log = subprocess.run(["git", "-C", str(ROOT), "log", "--format=%H", "--", V215],
                             capture_output=True, text=True, timeout=30).stdout.split()
        if len(log) < 2:
            return None
        top = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-prefix"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        r = subprocess.run(["git", "-C", str(ROOT), "show", "%s:%s%s" % (log[1], top, V215)],
                           capture_output=True, timeout=30)
        return r.stdout if r.returncode == 0 and r.stdout else None
    except (OSError, subprocess.TimeoutExpired):
        return None


class Auto(unittest.TestCase):
    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp(prefix="aib-slauto-"))

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def run_tool(self, *args, auto="1", tool=TOOL):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home), "AIB_STATUSLINE_AUTO": auto}
        return subprocess.run(["bash", str(tool)] + list(args), env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=120)

    def install_claude(self):
        """先用一般模式裝一次,再刪掉 codex / agy 那幾家 ⇒ 這台只有 claude 在用。"""
        self.home.joinpath(".claude").mkdir()
        r = self.run_tool()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for d in (".codex", ".gemini", ".ez2"):
            shutil.rmtree(self.home / d, ignore_errors=True)

    def test_S1_old_version_replaced_with_backup(self):
        old = old_v215()
        if old is None:
            self.skipTest("拿不到 git 歷史裡的舊版")
        self.install_claude()
        dest = self.home / ".claude" / "statusline-ez2-v215.sh"
        dest.write_bytes(old)
        r = self.run_tool("--auto")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sha(dest), sha(ROOT / V215), "舊版要換成這一版")
        self.assertEqual(len(list(dest.parent.glob(dest.name + ".ez2-backup-*"))), 1, "換前要備份")
        self.assertIn("1 支已更新", r.stdout)

    def test_S2_user_modified_left_alone(self):
        self.install_claude()
        dest = self.home / ".claude" / "statusline-ez2-ctx.js"
        dest.write_text(dest.read_text(encoding="utf-8") + "\n// 我自己改的\n", encoding="utf-8")
        mine = sha(dest)
        r = self.run_tool("--auto")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sha(dest), mine, "改過的不能動")
        self.assertEqual(list(dest.parent.glob(dest.name + ".ez2-backup-*")), [])
        lines = [ln for ln in r.stdout.splitlines() if "statusline-ez2-ctx.js" in ln]
        self.assertEqual(len(lines), 1, r.stdout)
        self.assertIn("statusline-install.sh", lines[0], "要印手動換法")

    def test_S3_switch_off_touches_nothing(self):
        old = old_v215()
        self.install_claude()
        dest = self.home / ".claude" / "statusline-ez2-v215.sh"
        if old is not None:
            dest.write_bytes(old)
        else:
            dest.unlink()
        before = tree(self.home)
        r = self.run_tool("--auto", auto="0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(tree(self.home), before, "關了就一個位元組都不寫")
        self.assertIn("AIB_STATUSLINE_AUTO=0", r.stdout)

    def test_S4_rerun_writes_nothing_and_codex_trust_untouched(self):
        for d in (".claude", ".codex"):
            self.home.joinpath(d).mkdir()
        hooks = self.home / ".codex" / "hooks.json"
        cfg = self.home / ".codex" / "config.toml"
        hooks.write_text('{"hooks": []}\n', encoding="utf-8")
        cfg.write_text('[hooks.state.x]\ntrusted_hash = "abc"\n', encoding="utf-8")
        self.assertEqual(self.run_tool().returncode, 0)          # 先照一般模式裝好兩家
        (self.home / ".claude" / "ez2-ctx-core.js").unlink()      # 讓自動模式第一次真的有事做
        r = self.run_tool("--auto")
        self.assertIn("1 支已更新", r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        first = tree(self.home)
        r = self.run_tool("--auto")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(tree(self.home), first, "第二次零寫入")
        self.assertEqual(r.stdout.strip(), "", "沒事就不出聲")
        self.assertEqual(hooks.read_text(encoding="utf-8"), '{"hooks": []}\n')
        self.assertEqual(cfg.read_text(encoding="utf-8"), '[hooks.state.x]\ntrusted_hash = "abc"\n')

    def test_S5_unused_engine_not_created(self):
        self.install_claude()
        (self.home / ".claude" / "ez2-ctx-fleet.js").unlink()     # 在用的那家缺一支 ⇒ 補
        r = self.run_tool("--auto")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.home / ".claude" / "ez2-ctx-fleet.js").is_file(), "在用的那家要補")
        self.assertFalse((self.home / ".codex").exists(), "沒在用的那家不建")
        self.assertFalse((self.home / ".gemini").exists())
        empty = pathlib.Path(tempfile.mkdtemp(prefix="aib-slauto-empty-"))
        try:
            r = subprocess.run(["bash", str(TOOL), "--auto"], env={"PATH": os.environ.get("PATH", ""), "HOME": str(empty)},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(list(empty.rglob("*")), [], "空的 HOME 什麼都不建")
        finally:
            shutil.rmtree(empty, ignore_errors=True)

    def test_S6_init_runs_it_and_failure_only_warns(self):
        old = old_v215()
        if old is None:
            self.skipTest("拿不到 git 歷史裡的舊版")
        self.install_claude()
        dest = self.home / ".claude" / "statusline-ez2-v215.sh"
        dest.write_bytes(old)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home), "AIB_STATUSLINE_AUTO": "1",
               "AIB_GENES": "0", "AIB_AUTO_BIND": "0", "AIB_AUTO_DISTILL": "0", "AIB_EXT_AUTO": "0",
               "EZ2_AIB_LOCALE": "zh-TW"}
        init = ROOT / "src" / "bootstrap" / "aib_init.py"
        st = str(self.home / ".ez2-aib")
        r = subprocess.run([PY, str(init), "--state-root", st, "--json"], env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sha(dest), sha(ROOT / V215), "aib init 結尾會換掉舊版")
        dest.write_bytes(old)
        os.chmod(self.home / ".claude", 0o500)                   # 目錄不可寫 ⇒ 安裝器 rc 4
        try:
            r = subprocess.run([PY, str(init), "--state-root", st, "--json"], env=env, capture_output=True,
                               text=True, stdin=subprocess.DEVNULL, timeout=180)
        finally:
            os.chmod(self.home / ".claude", 0o700)
        self.assertEqual(r.returncode, 0, "安裝器失敗不能讓 init 失敗:\n" + r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertTrue(any("statusline-install.sh" in ln for ln in out["statusline"]), out["statusline"])

    def test_S7_known_shas_cover_current(self):
        r = subprocess.run([PY, str(ROOT / "tools" / "statusline-known-shas.py"), "--check"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)


class Knife(unittest.TestCase):
    def _cut(self, needle, repl, test):
        with knife_copy(ROOT, "tools/statusline-install.sh", needle, repl, tests=(THIS,)) as copy_root:
            r = run_guarded([PY, str(copy_root / "tests" / THIS), test], cwd=str(copy_root), timeout=300)
        self.assertNotEqual(r.returncode, 0, "刀下去 %s 應該紅:\n%s%s" % (test, r.stdout, r.stderr))

    def test_K1_replace_everything(self):
        self._cut('or have in (known.get(it["src"]) or []):', "or True:", "Auto.test_S2_user_modified_left_alone")

    def test_K2_no_off_switch(self):
        self._cut('[ "${AIB_STATUSLINE_AUTO:-1}" = "0" ]', "false", "Auto.test_S3_switch_off_touches_nothing")

    def test_K3_create_unused(self):
        self._cut('st = "go" if it["engine"] in in_use else "unused"', 'st = "go"', "Auto.test_S5_unused_engine_not_created")


if __name__ == "__main__":
    unittest.main(verbosity=2)
