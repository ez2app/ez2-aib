#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""語言 / 說明文字 / 內部代號外洩 / 死路提示(10-15 盤查 E1 E2 E3 E6 C12 C13)。

H1 英文介面下各子指令 --help:rc 0、沒有中日韓字、沒有 !!缺鍵!!、沒有內部代號(ED.2 / ED.3 / SOP / §)
H2 aib ai / seat / genes 認得 --help 與 -h:印用法、rc 0
H3 aib memory 不加子指令:印友善用法,不是 argparse 的「the following arguments are required」
D1 sync_receipt 找不到 ssh 位址時,不叫人去跑還沒開放的 aib room
D2 子指令檔不見時(aib_delegate_subcommand)講使用者的語言,不寫死中文
M1 字典四語同鍵;沒有任何值留著維護者代位字(the maintainer / メンテナー / 維護者)
M2 bot 信箱規則、限流訊息、記憶整理狀態的字串常數裡沒有「the maintainer」
U1 新艙 --dry-run 不印假艙號「instance=2」,改印「(new cabin)」
U2 降溫「CPU 溫度量不到」走 i18n;窄欄(24 格)時第一行換短句,不被截成「…」
刀(複本):K1 aib_init.py 說明改回「ED.2 首跑建置」⇒ H1 紅;K2 ai_view.py 拿掉 --help 分支 ⇒ H2 紅;
          K3 字典 sync_receipt.no_target 改回「aib room add-host」⇒ D1 紅;K4 拿掉 dry-run 新艙分支 ⇒ U1 紅;
          K5 拿掉窄欄短句分支 ⇒ U2 紅;K6 grokbot 規則改回「the maintainer 本人」⇒ M2 紅;
          K7 拿掉 aib memory 友善用法分支 ⇒ H3 紅
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # = aib/
AIB = [sys.executable, str(ROOT.parent / "bin" / "aib")]
COCKPIT = ROOT / "src" / "cockpit"
LOCALES = ("en", "ja", "ko", "zh-TW")
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")      # 假名、漢字、諺文(不含「」這類標點)
LEAK = re.compile(r"\bED\.\d|\bSOP\b|§|the maintainer|!!aib\.")

HELP_CMDS = [
    "now --help", "restart --help", "bindme --help", "bindme status --help", "page --help", "page chat --help",
    "page import --help", "appoint --help", "cli --help", "cli update --help", "doctor --help", "memory --help",
    "memory write --help", "task --help", "task set --help", "top --help", "seat detect --help", "init --help",
    "persona --help", "persona precheck --help", "persona report --help",
    "ai --help", "ai -h", "seat --help", "seat -h", "genes --help", "genes -h",
]


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-i18n-help-"))
        (self.td / "home").mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("EZ2_", "AIB_", "TMUX")) and k not in ("PYTHONPATH", "BASH_ENV")}
        self.env.update({"HOME": str(self.td / "home"), "EZ2_AIB_LOCALE": "en", "LANG": "en_US.UTF-8",
                         "AIB_GENES": "0", "XDG_CACHE_HOME": str(self.td / "cache")})

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def aib(self, args, **extra):
        env = dict(self.env, **extra)
        p = subprocess.run(AIB + args.split(), cwd=self.td, env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=120)
        return p.returncode, p.stdout + p.stderr


class Help(_Sandbox):
    def test_h1_h2_help_english_rc0_no_leaks(self):
        bad = []
        for cmd in HELP_CMDS:
            rc, out = self.aib(cmd)
            cjk = [ln for ln in out.splitlines() if CJK.search(ln)]
            leak = LEAK.findall(out)
            if rc != 0 or cjk or leak or not out.strip():
                bad.append("aib %s: rc=%s cjk=%r leak=%r" % (cmd, rc, cjk[:2], leak[:3]))
        self.assertEqual(bad, [], "\n".join(bad))

    def test_h1_other_locales_still_translated(self):
        # 四語都要有字(不是全退回英文):ja / ko / zh-TW 的 now --help 都該出現該語系的字
        for loc in ("ja", "ko", "zh-TW"):
            rc, out = self.aib("now --help", EZ2_AIB_LOCALE=loc)
            self.assertEqual(rc, 0, out)
            self.assertTrue(CJK.search(out), "%s: %s" % (loc, out[:300]))
            self.assertNotIn("!!aib.", out)

    def test_h3_bare_memory_friendly_usage(self):
        rc, out = self.aib("memory")
        self.assertNotIn("the following arguments are required", out)
        self.assertNotIn("error:", out)
        self.assertIn("aib memory", out)
        self.assertFalse(CJK.search(out), out)


class DeadEnds(_Sandbox):
    def test_d1_sync_receipt_no_room_hint(self):
        p = subprocess.run([sys.executable, str(ROOT / "tools" / "verify" / "sync_receipt.py"), "verify",
                            "--host", "nohost"], cwd=self.td, env=self.env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 2, out)
        self.assertIn("--ssh", out)
        self.assertNotIn("aib room", out)
        self.assertFalse(CJK.search(out), out)

    def test_d1_no_dictionary_points_to_unopened_commands(self):
        for loc in LOCALES:
            d = json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            hits = [k for k, v in d.items() if "aib room add-host" in v]
            self.assertEqual(hits, [], loc)

    def test_d2_delegate_missing_speaks_user_language(self):
        src = (COCKPIT / "aib").read_text(encoding="utf-8")
        body = src.split("aib_delegate_subcommand() {", 1)[1].split("\n}\n", 1)[0]
        self.assertFalse(CJK.search("\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))),
                         body)
        self.assertIn("aib.delegate.not_installed", body)


class Dictionary(unittest.TestCase):
    def test_m1_parity_and_no_maintainer_placeholder(self):
        dicts = {loc: json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8")) for loc in LOCALES}
        keys = set(dicts["en"])
        for loc in LOCALES:
            self.assertEqual(set(dicts[loc]) ^ keys, set(), loc)
        ph = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
        mism = [k for k in keys if len({tuple(sorted(set(ph.findall(dicts[l][k])))) for l in LOCALES}) != 1]
        self.assertEqual(mism, [], mism[:5])
        for loc, word in (("en", "the maintainer"), ("ja", "メンテナー"), ("zh-TW", "維護者")):
            hits = [k for k, v in dicts[loc].items() if word in v]
            self.assertEqual(hits, [], "%s: %s" % (loc, hits[:5]))

    def test_m2_bot_rules_and_messages_no_maintainer(self):
        files = [COCKPIT / "grokbot_mailbox.py", COCKPIT / "musebot_mailbox.py", COCKPIT / "claudebot_mailbox.py",
                 COCKPIT / "codexbot_bridge.py", ROOT / "src" / "memory" / "memory_tidy.py"]
        bad = []
        for f in files:
            tree = ast.parse(f.read_text(encoding="utf-8"))
            docs = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.body and isinstance(node.body[0], ast.Expr) and isinstance(
                            getattr(node.body[0], "value", None), ast.Constant):
                        docs.add(id(node.body[0].value))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs
                        and "the maintainer" in node.value):
                    bad.append("%s:%d %s" % (f.name, node.lineno, node.value[:60]))
        self.assertEqual(bad, [], "\n".join(bad))


class Ui(_Sandbox):
    @unittest.skipUnless(sys.platform == "darwin", "the cockpit launcher (tmux cabins) is macOS-only")
    def test_u1_dry_run_new_cabin_placeholder(self):
        rc, out = self.aib("claude safe pm new --dry-run")
        self.assertEqual(rc, 0, out[-2000:])
        tuple_line = [ln for ln in out.splitlines() if ln.startswith("aib: instance=")]
        self.assertEqual(len(tuple_line), 1, out[-2000:])
        self.assertIn("instance=(new cabin)", tuple_line[0])
        self.assertIn("stand-in", out)

    def test_u2_cooldown_and_narrow_pane(self):
        code = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import cooldown, machine_status_row as m
cells = [{"key": "load1", "state": "ok", "value": 1.2}, {"key": "cpu_temp", "state": "unmeasured", "value": None},
         {"key": "seats", "state": "unmeasured", "value": None}]
lines = m.compose(cells, level={"load_level": "ok", "thermal_level": "unknown"}, width=24)
print(json.dumps({"thermal": cooldown.thermal_state(cells), "head": lines[0]}))
"""
        p = subprocess.run([sys.executable, "-c", code, str(COCKPIT)], env=self.env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        got = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(got["thermal"][0], "unknown")
        self.assertFalse(CJK.search(" ".join(got["thermal"][1])), got)
        head = re.sub(r"\x1b\[[0-9;]*m", "", got["head"])
        self.assertNotIn("…", head)
        self.assertLessEqual(len(head), 24, head)


if __name__ == "__main__":
    unittest.main()
