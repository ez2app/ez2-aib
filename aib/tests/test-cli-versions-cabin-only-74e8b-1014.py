#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""開席時只查「這一艙有席的那幾家」的版本(10-14 公開版新用戶走查第 10 條延伸)。

以前:開一席 claude,開席動畫的版本列照樣對五家(加 gemini)跑 `<cli> --version`;agy / gemini 一跑就在 ~/.gemini 留檔。
V1 check(only={...}):只對 only 裡的家(加 aib 自己)查本機 / 最新;其他家不呼叫
V2 `cli_versions.py check --provider claude`:艙裡還有一席活的 agy ⇒ 只跑 claude 與 agy;codex / grok / copilot / gemini 一支都不跑
V3 沒查的家在開席動畫上不寫「沒裝」(那是說謊:只是沒查)
V4 開席(src/cockpit/aib)兩處呼叫 cli_versions 都帶 --provider(背景查、接入前倒數)
V5 `aib cli status`(人自己要看)照舊五家都查
刀(複本):K1 check 不看 only ⇒ V1、V2 紅;K2 不讀艙裡的席 ⇒ V2 紅;K3 沒查的家照寫「沒裝」⇒ V3 紅
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COCKPIT = ROOT / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(ROOT / "tests"))
from aib_tmp_root import aib_tmp_root  # noqa: E402

CLIV = COCKPIT / "cli_versions.py"
STUBS = ("claude", "codex", "grok", "copilot", "agy", "gemini", "npm")


class CabinOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-cliver-", dir=aib_tmp_root())
        self.home = os.path.join(self.tmp, "home")
        self.cabin = os.path.join(self.home, ".ez2-aib", "instances", "t1")
        os.makedirs(os.path.join(self.cabin, "seats"))
        self.bin = os.path.join(self.tmp, "bin")
        self.log = os.path.join(self.tmp, "ran")
        os.makedirs(self.bin)
        for name in STUBS:   # 每一支假 CLI 被叫到就記一行
            p = Path(self.bin, name)
            p.write_text('#!/bin/sh\necho "%s $*" >> %s\necho "%s 1.2.3"\n' % (name, self.log, name), encoding="utf-8")
            p.chmod(0o755)
        py = os.path.dirname(sys.executable)
        self.env = {"HOME": self.home, "PATH": "%s:%s:/usr/bin:/bin" % (self.bin, py), "LANG": "zh_TW.UTF-8",
                    "EZ2_AIB_HOME": os.path.join(self.home, ".ez2-aib"), "EZ2_AIB_INSTANCE_HOME": self.cabin,
                    "EZ2_AIB_STATE_ROOT": os.path.join(self.home, ".ez2-aib"), "TMPDIR": self.tmp + "/"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seat(self, name, provider, exited=False):
        rec = {"provider": provider, "role": "dev"}
        if exited:
            rec["exited_at_epoch"] = int(time.time())
        Path(self.cabin, "seats", name + ".json").write_text(json.dumps(rec), encoding="utf-8")

    def ran(self):
        try:
            return sorted({l.split()[0] for l in Path(self.log).read_text(encoding="utf-8").splitlines() if l})
        except OSError:
            return []

    def run_cliv(self, *a):
        p = subprocess.run([sys.executable, str(CLIV), *a], env=self.env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout + p.stderr

    def test_v1_check_only(self):
        import cli_versions as cv
        asked = []
        rows = cv.check(only={"claude"}, cache=os.path.join(self.tmp, "c.json"),
                        local_fn=lambda t: asked.append(("local", t["key"])) or "1.0.0",
                        latest_fn=lambda t: asked.append(("latest", t["key"])) or "1.0.0",
                        installed_fn=lambda t: None)
        self.assertEqual(sorted({k for _w, k in asked}), ["aib", "claude"])
        self.assertEqual([r["key"] for r in rows], [t["key"] for t in cv.TOOLS])   # 每家照樣有一列
        self.assertTrue(all(r.get("skipped") for r in rows if r["key"] not in ("claude", "aib")))

    def test_v2_cli_runs_only_cabin_tools(self):
        self.seat("w1", "agy")
        self.seat("w2", "codex", exited=True)
        out_file = os.path.join(self.tmp, "out.json")
        rc, out = self.run_cliv("check", "--provider", "claude", "--out", out_file)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.ran(), ["agy", "claude", "npm"], out)   # npm = claude 的最新版查詢
        self.assertFalse(os.path.exists(os.path.join(self.home, ".gemini")))

    def test_v3_skipped_not_called_missing(self):
        import cli_versions as cv
        rows = [{"key": k, "local": None, "latest": None, "update": False, "can_update": False, "nag": False,
                 "installed_at": None, "skipped": True} for k in cv.SPLASH_KEYS]
        text = json.dumps(cv.version_rows(rows), ensure_ascii=False)
        self.assertNotIn("沒裝", text)

    def test_v4_launcher_passes_provider(self):
        src = (COCKPIT / "aib").read_text(encoding="utf-8")
        calls = [l for l in src.splitlines() if 'cli_versions.py" check' in l or 'cli_versions.py" gate' in l]
        self.assertEqual(len(calls), 2, calls)
        for line in calls:
            self.assertIn('--provider "$PROVIDER"', line)

    def test_v5_status_still_all(self):
        rc, out = self.run_cliv("status")
        self.assertEqual(rc, 0, out)
        for name in ("claude", "codex", "grok", "copilot", "agy"):
            self.assertIn(name, self.ran(), out)


if __name__ == "__main__":
    unittest.main()
