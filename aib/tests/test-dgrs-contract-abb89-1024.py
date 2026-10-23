#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""每一家的 dgrs 旗標:要嘛明確對到那家 CLI 的旗標,要嘛明寫「沒有」(10-24 右鍵 14 家實測第 4 項)。

10-24 實測:grok / agy / copilot 先前都落在 provider-launch.sh 的 `*)` plain 啟動 —— 窗名寫 dgrs,實際每個工具都要人按,
沒有任何讀數會紅。這支把「哪一家帶什麼旗標」釘成契約,新加一家沒決定就紅。

D1 原生 CLI 五家(claude/codex/agy/copilot/grok):真跑 provider-launch.sh(PATH 上放假 CLI 記 argv),dgrs 帶到表上的旗標
D2 同五家 safe:不帶任何一家的 dgrs 旗標
D3 providers.json 裡可開席的每一家(alias-only 除外)都在本檔兩張表之一:EXPECTED_DGRS(原生)或 NO_DGRS(自家橋接,寫原因)
D4 provider-launch.sh 的白名單 = 兩張表的聯集(多一家、少一家都紅)
D5 EXPECTED_DGRS 每一家在 `case "$PROVIDER:$MODE"` 有自己的 `<id>:dgrs)`,不靠 `*)`
D6 NO_DGRS 每一家在 dgrs 對照之前就 exec 自家橋接(不會走到 `*)` 被當成原生 CLI 起)
刀(複本):K1 拿掉 grok:dgrs ⇒ D1、D5 紅;K2 providers.json 加一家原生沒進表 ⇒ D3 紅;K3 白名單多一家 ⇒ D4 紅
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from aib_tmp_root import aib_tmp_root  # noqa: E402

COCKPIT = ROOT / "src" / "cockpit"
LAUNCH = COCKPIT / "provider-launch.sh"
PROVIDERS = COCKPIT / "providers.json"

# 原生 CLI:dgrs 對應的旗標(版本 = 實測 --help 的那一版)
EXPECTED_DGRS = {
    "claude": ["--dangerously-skip-permissions"],
    "codex": ["--dangerously-bypass-approvals-and-sandbox"],   # 0.158.0;--no-daemon 另由 codex_daemon_flag 判
    "grok": ["--permission-mode", "bypassPermissions"],        # 1.0.13
    "agy": ["--dangerously-skip-permissions"],                 # 1.4.6
    "copilot": ["--allow-all"],                                # 1.0.103
}
# 自家橋接 / API 金鑰終端席:沒有「審批」這回事,dgrs 不帶旗標
NO_DGRS = {
    "deepseek": "API 金鑰終端席,本倉 deepseek_cli.py",
    "nvidia": "API 金鑰終端席,本倉 nvidia_cli.py",
    "openrouter": "API 金鑰終端席,本倉 openrouter_cli.py",
    "typesafe": "API 金鑰終端席,本倉 typesafe_cli.py",
    "openrouterjev": "API 金鑰終端席,走 typesafe_cli.py",
    "sand": "桌面 App 橋接(已退役),sand_bridge.py",
    "grokbot": "桌面 App 橋接,sand_bridge.py",
    "claudebot": "桌面 App 橋接,sand_bridge.py",
    "musebot": "桌面 App 橋接,sand_bridge.py",
    "codexbot": "桌面 App 橋接,codexbot_bridge.py",
}
ALL_FLAGS = {f for flags in EXPECTED_DGRS.values() for f in flags if f.startswith("--")}

FAKE_CLI = """#!/bin/sh
[ "$1" = --help ] && { echo "  --no-daemon"; exit 0; }
printf '%s\\n' "$*" > "$ARGV_DIR/$(basename "$0")"
"""


def launch_src():
    return LAUNCH.read_text(encoding="utf-8")


class Launch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-dgrs-", dir=aib_tmp_root())
        self.home = os.path.join(self.tmp, "home")
        self.bin = os.path.join(self.tmp, "bin")
        self.argv = os.path.join(self.tmp, "argv")
        self.proj = os.path.join(self.tmp, "proj")
        for d in (self.home, self.bin, self.argv, self.proj):
            os.makedirs(d)
        for name in EXPECTED_DGRS:
            p = Path(self.bin, name)
            p.write_text(FAKE_CLI, encoding="utf-8")
            p.chmod(0o755)
        state = os.path.join(self.home, ".ez2-aib")
        self.env = {"HOME": self.home, "PATH": self.bin + ":/usr/bin:/bin", "TMPDIR": self.tmp + "/",
                    "LANG": "C.UTF-8", "ARGV_DIR": self.argv, "EZ2_AIB_HOME": state, "EZ2_AIB_STATE_ROOT": state,
                    "EZ2_COCKPIT_STATE_DIR": os.path.join(state, "cockpit"), "USER": os.environ.get("USER", "u"),
                    # 背景探針 / codex 換號看守會留孤兒;這支只看旗標,一律關
                    "AIB_NO_BIND_PROBE": "1", "AIB_CODEX_AUTO_ROTATE_ARM": "0", "AIB_NO_ONBOARD": "1"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_launch(self, provider, mode):
        p = subprocess.run(["bash", str(LAUNCH), provider, mode, "dev", self.proj], env=self.env, cwd=self.proj,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        f = Path(self.argv, provider)
        self.assertTrue(f.exists(), f"{provider} {mode} 沒起到假 CLI rc={p.returncode}\n{p.stdout}{p.stderr}")
        return f.read_text(encoding="utf-8").split()

    def test_d1_dgrs_flags(self):
        for provider, flags in EXPECTED_DGRS.items():
            with self.subTest(provider=provider):
                argv = self.run_launch(provider, "dgrs")
                self.assertEqual(argv[:len(flags)], flags, argv)

    def test_d2_safe_has_no_dgrs_flag(self):
        for provider in EXPECTED_DGRS:
            with self.subTest(provider=provider):
                argv = self.run_launch(provider, "safe")
                self.assertFalse(ALL_FLAGS & set(argv), argv)
                self.assertNotIn("bypassPermissions", argv)


class Contract(unittest.TestCase):
    def test_d3_every_provider_decided(self):
        rows = json.loads(PROVIDERS.read_text(encoding="utf-8"))["providers"]
        ids = {r["id"] for r in rows if r.get("status") != "alias-only"}
        undecided = ids - set(EXPECTED_DGRS) - set(NO_DGRS)
        self.assertFalse(undecided, f"這幾家沒決定 dgrs 旗標:{sorted(undecided)}(先量那家 CLI 的 --help)")
        self.assertFalse(set(EXPECTED_DGRS) & set(NO_DGRS))
        for r in rows:
            if r["id"] in EXPECTED_DGRS:
                self.assertEqual(r.get("menu_group"), "native", r["id"])

    def test_d4_allowlist_matches(self):
        m = re.search(r'^case "\$PROVIDER" in ([a-z|]+)\) ;;', launch_src(), re.M)
        self.assertIsNotNone(m)
        self.assertEqual(set(m.group(1).split("|")), set(EXPECTED_DGRS) | set(NO_DGRS))

    def test_d5_explicit_dgrs_arm(self):
        src = launch_src()
        start = src.index('case "$PROVIDER:$MODE" in')
        block = src[start:src.index("\nesac", start)]
        for provider in EXPECTED_DGRS:
            with self.subTest(provider=provider):
                self.assertRegex(block, rf"(?m)^\s*{provider}:dgrs\)", f"{provider}:dgrs 沒有自己那一行,會落到 *)")

    def test_d6_bridges_exec_before_dgrs_table(self):
        src = launch_src()
        table = src.index('case "$PROVIDER:$MODE" in')
        for provider in NO_DGRS:
            with self.subTest(provider=provider):
                m = re.search(rf'^if \[[^\n]*"\$PROVIDER" = "{provider}"', src, re.M)
                self.assertIsNotNone(m, f"{provider} 沒有自己的橋接分支")
                self.assertLess(m.start(), table)
                rest = src[m.start():table]
                self.assertRegex(rest[:rest.find("\nfi\n")], r'\bexec\b', f"{provider} 分支沒有 exec")


if __name__ == "__main__":
    unittest.main()
