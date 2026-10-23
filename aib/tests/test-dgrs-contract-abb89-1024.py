#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""每一家的 dgrs 旗標:要嘛明確對到那家 CLI 的旗標,要嘛明寫「沒有」(10-24 右鍵 14 家實測第 4 項)。

10-24 實測:grok / agy / copilot 先前都落在 provider-launch.sh 的 `*)` plain 啟動 —— 窗名寫 dgrs,實際每個工具都要人按,
沒有任何讀數會紅。這支把「哪一家帶什麼旗標」釘成契約,新加一家沒決定就紅。

D1 原生 CLI 五家(claude/codex/agy/copilot/grok):真跑 provider-launch.sh(PATH 上放假 CLI 記 argv),dgrs 帶到表上的旗標
D2 同五家 safe:只准 SAFE_OK 裡的旗標(--yolo、--allow-all-tools… 一律算偷渡)
D3 providers.json 裡可開席的每一家(alias-only 除外)都在本檔兩張表之一:EXPECTED_DGRS(原生)或 NO_DGRS(自家橋接,寫原因)
D4 provider-launch.sh 的白名單 = 兩張表的聯集(多一家、少一家都紅)
D4b 每一家 id 都過白名單(bogus mode 拿到 invalid mode,不是 unsupported;不靠 regex)
D5 EXPECTED_DGRS 每一家在 `case "$PROVIDER:$MODE"` 有自己的 `<id>:dgrs)`,不靠 `*)`
D6 NO_DGRS 每一家在找 CLI(BIN=)之前就 exec 自家橋接(不會被當成原生 CLI 起)
D7 真實呼叫形狀:帶 session uuid、預設首回合提示(不設 AIB_NO_ONBOARD)、claude/grok 接回(AIB_RESUME_UUID)、
   claude 分出新席(帶第一句)/ 分出後重生、copilot 接回、codex 套組關、codex 接回,dgrs 旗標都還在
D8 codex 換帳後重組參數(_fresh_login_args)照 dgrs 帶回旗標
D9 openrouter / typesafe / openrouterjev 帶型號又 AIB_NO_EFFORT=1 也開得起來(以前 PY3 沒設值,set -u 當場退)
刀(PR 手跑在複本上,不在本檔):K1 拿掉 grok:dgrs ⇒ D1、D5 紅;K2 providers.json 加一家原生沒進表 ⇒ D3 紅;K3 白名單多一家 ⇒ D4 紅;
  K4 copilot 旗標清空 ⇒ D1 紅;K5 帶 session 時 ARGS= 洗掉旗標 ⇒ D7 紅;K6 拿掉橋接分支的 PY3= ⇒ D9 紅;
  K7 agy / codex 首回合、copilot 接回、claude 分出 / 分出帶第一句 / 重生用 ARGS= 洗掉旗標 ⇒ D7 紅
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
# safe 只准出現這些旗標;其他任何 -- 開頭(--yolo、--allow-all-tools、--permission-mode…)都算偷渡
SAFE_OK = {"--model", "--effort", "--no-daemon", "--session-id", "--resume"}
UUID = "0f0e0d0c-0b0a-4908-8706-050403020100"

FAKE_CLI = """#!/bin/sh
[ "$1" = --help ] && { echo "  --no-daemon"; exit 0; }
for a in "$@"; do printf '%s\\n' "$a"; done > "$ARGV_DIR/$(basename "$0")"
"""
FAKE_PY = """#!/bin/sh
printf '%s\\n' "$*" > "$ARGV_DIR/py"
"""


def launch_src():
    return LAUNCH.read_text(encoding="utf-8")


def has_run(argv, flags):
    """flags 整串連續出現在 argv 裡(順序對 CLI 沒差,但 --permission-mode 與值要相鄰)。"""
    n = len(flags)
    return any(argv[i:i + n] == flags for i in range(len(argv) - n + 1))


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
                    "EZ2_PY3": sys.executable,   # 不靠 /usr/bin/python3(CI 矩陣那支、或 Mac 沒裝 CLT)
                    # 背景探針 / codex 換號看守會留孤兒;這支只看旗標,一律關。其餘寫明不靠預設值
                    "AIB_NO_BIND_PROBE": "1", "AIB_CODEX_AUTO_ROTATE_ARM": "0", "AIB_NO_ONBOARD": "1",
                    "AIB_IGNORE_USAGE": "1", "AIB_FOLDER_TRUST": "0"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def launch(self, *args, env=None):
        return subprocess.run(["bash", str(LAUNCH), *args], env={**self.env, **(env or {})}, cwd=self.proj,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)

    def run_launch(self, provider, mode, *extra, env=None):
        p = self.launch(provider, mode, "dev", self.proj, *extra, env=env)
        f = Path(self.argv, provider)
        self.assertTrue(f.exists(), f"{provider} {mode} 沒起到假 CLI rc={p.returncode}\n{p.stdout}{p.stderr}")
        argv = f.read_text(encoding="utf-8").splitlines()
        f.unlink()
        return argv

    def test_d1_dgrs_flags(self):
        for provider, flags in EXPECTED_DGRS.items():
            with self.subTest(provider=provider):
                argv = self.run_launch(provider, "dgrs")
                self.assertTrue(has_run(argv, flags), argv)

    def test_d2_safe_has_no_dgrs_flag(self):
        for provider in EXPECTED_DGRS:
            with self.subTest(provider=provider):
                argv = self.run_launch(provider, "safe")
                odd = [a for a in argv if a.startswith("-") and a.split("=")[0] not in SAFE_OK]
                self.assertFalse(odd, argv)
                self.assertNotIn("bypassPermissions", argv)

    def test_d7_real_call_shapes(self):
        for provider, flags in EXPECTED_DGRS.items():
            with self.subTest(provider=provider, shape="session"):
                argv = self.run_launch(provider, "dgrs", UUID)
                self.assertTrue(has_run(argv, flags), argv)
        for provider in ("claude", "grok"):
            with self.subTest(provider=provider, shape="resume"):
                argv = self.run_launch(provider, "dgrs", UUID, env={"AIB_RESUME_UUID": "1"})
                self.assertTrue(has_run(argv, EXPECTED_DGRS[provider]), argv)
                self.assertTrue(has_run(argv, ["--resume", UUID]), argv)
        for provider, flags in EXPECTED_DGRS.items():
            with self.subTest(provider=provider, shape="onboard"):
                argv = self.run_launch(provider, "dgrs", env={"AIB_NO_ONBOARD": "0"})
                self.assertTrue(has_run(argv, flags), argv)
                if provider == "agy":
                    self.assertIn("--prompt-interactive", argv)
        fork_from = "1f1e1d1c-1b1a-4918-8716-151413121110"
        with self.subTest(provider="claude", shape="fork"):
            # 真的分出時 aib 一定連第一句(AIB_FORK_PREAMBLE_FILE)一起帶
            pre = Path(self.tmp, "preamble.txt")
            pre.write_text("PREAMBLE-LINE", encoding="utf-8")
            argv = self.run_launch("claude", "dgrs", UUID, env={"AIB_FORK_FROM_SESSION_UUID": fork_from,
                                                                "AIB_FORK_PREAMBLE_FILE": str(pre)})
            self.assertTrue(has_run(argv, EXPECTED_DGRS["claude"]), argv)
            self.assertTrue(has_run(argv, ["--resume", fork_from, "--fork-session", "--session-id", UUID]), argv)
            self.assertEqual(argv[-1], "PREAMBLE-LINE", argv)
        with self.subTest(provider="claude", shape="fork-respawn"):
            proj = Path(self.home, ".claude", "projects", "x")
            proj.mkdir(parents=True)
            Path(proj, UUID + ".jsonl").write_text("", encoding="utf-8")
            argv = self.run_launch("claude", "dgrs", UUID, env={"AIB_FORK_FROM_SESSION_UUID": fork_from})
            self.assertTrue(has_run(argv, EXPECTED_DGRS["claude"]), argv)
            self.assertTrue(has_run(argv, ["--resume", UUID]), argv)
            self.assertNotIn("--fork-session", argv)
        for mode in ("dgrs", "safe"):
            with self.subTest(provider="copilot", shape="resume-" + mode):
                argv = self.run_launch("copilot", mode, env={"AIB_RESUME_SESSION_UUID": UUID})
                self.assertTrue(has_run(argv, ["--resume", UUID]), argv)
                if mode == "dgrs":
                    self.assertTrue(has_run(argv, EXPECTED_DGRS["copilot"]), argv)
                else:
                    self.assertFalse([a for a in argv if a.startswith("-") and a not in SAFE_OK], argv)
        with self.subTest(provider="codex", shape="pack-off"):
            argv = self.run_launch("codex", "dgrs", env={"AIB_CODEX_PACK": "0"})
            self.assertTrue(has_run(argv, EXPECTED_DGRS["codex"]), argv)
            self.assertNotIn("--model", argv)
        with self.subTest(provider="codex", shape="resume"):
            argv = self.run_launch("codex", "dgrs", env={"AIB_RESUME_SESSION_UUID": UUID})
            self.assertEqual(argv[0], "resume", argv)
            self.assertEqual(argv[-1], UUID, argv)
            self.assertTrue(has_run(argv, EXPECTED_DGRS["codex"]), argv)

    def test_d9_key_seats_start_with_model_and_no_effort(self):
        py = Path(self.bin, "fakepy")
        py.write_text(FAKE_PY, encoding="utf-8")
        py.chmod(0o755)
        for provider, cli in (("openrouter", "openrouter_cli.py"), ("typesafe", "typesafe_cli.py"),
                              ("openrouterjev", "typesafe_cli.py")):
            with self.subTest(provider=provider):
                p = self.launch(provider, "dgrs", "dev", self.proj, "", "some-model",
                                env={"AIB_NO_EFFORT": "1", "EZ2_PY3": str(py)})
                self.assertNotIn("unbound variable", p.stderr)
                f = Path(self.argv, "py")
                self.assertTrue(f.exists(), f"{provider} rc={p.returncode}\n{p.stderr}")
                self.assertIn(cli, f.read_text(encoding="utf-8"))
                f.unlink()

    def test_d4b_every_id_passes_allowlist(self):
        # 不靠 regex:白名單少一家 ⇒ 這裡拿到 unsupported,而不是下一關的 invalid mode
        for provider in list(EXPECTED_DGRS) + list(NO_DGRS):
            with self.subTest(provider=provider):
                p = self.launch(provider, "bogus", "dev", self.proj)
                self.assertEqual(p.returncode, 2)
                self.assertIn("invalid mode", p.stderr)


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
        m = re.search(r'^case\s+"\$PROVIDER"\s+in\s+([\w|\s-]+?)\)\s*;;', launch_src(), re.M)
        self.assertIsNotNone(m)
        ids = {s.strip() for s in m.group(1).split("|") if s.strip()}
        self.assertEqual(ids, set(EXPECTED_DGRS) | set(NO_DGRS))

    def test_d5_explicit_dgrs_arm(self):
        src = launch_src()
        start = src.index('case "$PROVIDER:$MODE" in')
        block = src[start:src.index("\nesac", start)]
        fallback = re.search(r"(?m)^\s*\*\)", block)
        self.assertIsNotNone(fallback)
        for provider in EXPECTED_DGRS:
            with self.subTest(provider=provider):
                m = re.search(rf"(?m)^\s*{provider}:dgrs\)", block)
                self.assertIsNotNone(m, f"{provider}:dgrs 沒有自己那一行,會落到 *)")
                self.assertLess(m.start(), fallback.start(), f"{provider}:dgrs 在 *) 後面,永遠跑不到")

    def test_d6_bridges_exec_before_cli_lookup(self):
        src = launch_src()
        lookup = src.index('BIN="$(type -P')
        for provider in NO_DGRS:
            with self.subTest(provider=provider):
                m = re.search(rf'^if \[[^\n]*"\$PROVIDER" = "{provider}"', src, re.M)
                self.assertIsNotNone(m, f"{provider} 沒有自己的橋接分支")
                self.assertLess(m.start(), lookup)
                rest = src[m.start():lookup]
                end = rest.find("\nfi\n")
                self.assertGreater(end, 0)
                self.assertRegex(rest[:end], r'(?m)^\s*exec\s', f"{provider} 分支沒有 exec")

    def test_d8_fresh_login_keeps_dgrs(self):
        src = launch_src()
        start = src.index("_fresh_login_args() {")
        fn = src[start:src.index("\n}\n", start)]
        flag = EXPECTED_DGRS["codex"][0]
        self.assertRegex(fn, r'\[ "\$MODE" = "dgrs" \]\s*;\s*then\s*\n\s*_kept\+=\(' + re.escape(flag) + r'\)')


if __name__ == "__main__":
    unittest.main()
