#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""熱度尺與截圖小幫手帶進開源版(10-15 維護者:「最好在這一版全補上」)。

F1 tools/heat/rulers.py 與 src/shot/(hotkey.swift + editor/ 6 檔)都在,每支帶 SPDX 授權行
F2 新檔脫敏:沒有維護者本名、家目錄絕對路徑、私倉名、真席碼
R1 machine_status.rulers() 找得到本樹的尺;三格都不再說「尺還沒合入」;macOS 上 load1 / heavy 不再是 unmeasured
B1 CLT 26 的 SwiftBridging 重複定義:第一次編失敗且 stderr 有那句 ⇒ 帶 -vfsoverlay 重編一次 ⇒ built
B2 不是那句的編譯失敗 ⇒ 不重試,回 build_failed
B3 overlay 把錯誤訊息裡那支 bridging.modulemap 指到一個空檔
B4 build.json 記 macOS 版本與 minos;換系統版本(或 minos 高於本機)⇒ 不用快取、重編
刀(手動):拿掉 build() 的重試 ⇒ B1、B3 紅;拿掉 macOS 版本比對 ⇒ B4 紅
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # = aib/
COCKPIT = ROOT / "src" / "cockpit"
RULERS = ROOT / "tools" / "heat" / "rulers.py"
SHOT = ROOT / "src" / "shot"
EDITOR_FILES = ("editor.html", "editor.js", "ez2-shot.js", "icons.js", "thumb.js", "tokens.css")
NEW_FILES = [RULERS, ROOT / "tests" / "test-heat-rulers.py", SHOT / "hotkey.swift"] + \
    [SHOT / "editor" / n for n in EDITOR_FILES]

BRIDGING = "/Library/Developer/CommandLineTools/usr/include/swift/bridging.modulemap"
BRIDGING_ERR = (
    "/Library/Developer/CommandLineTools/usr/include/swift/module.modulemap:13:8: "
    "error: redefinition of module 'SwiftBridging'\n"
    "13 | module SwiftBridging {\n"
    "/Library/Developer/CommandLineTools/usr/include/swift/bridging.modulemap:13:8: "
    "note: previously defined here\n"
)


def _load_shot_helper():
    if str(COCKPIT) not in sys.path:
        sys.path.insert(0, str(COCKPIT))
    spec = importlib.util.spec_from_file_location("shot_helper_t", COCKPIT / "shot_helper.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class FilesTest(unittest.TestCase):
    def test_f1_files_present_with_spdx(self):
        for path in NEW_FILES:
            self.assertTrue(path.is_file(), "缺檔:%s" % path.relative_to(ROOT))
            head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:3])
            self.assertIn("SPDX-License-Identifier: Apache-2.0", head, path.name)

    def test_f2_sanitized(self):
        bad = re.compile(r"Andy|andyimkt|showhu|/Users/|ez2-ai-b|worktrees/|AndyMini"
                         r"|\b(?:a6b2e|5d3eb|be4f6|83dc3|1cba7|b226a|f6995|87dec|acdb8|1107e)\b")
        for path in NEW_FILES:
            if not path.is_file():
                self.fail("缺檔:%s" % path.name)
            for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                m = bad.search(line)
                self.assertIsNone(m, "%s:%d 還有 %r" % (path.name, no, m and m.group(0)))


class MachineStatusRulersTest(unittest.TestCase):
    def test_r1_cells_measured_when_rulers_in_tree(self):
        code = (
            "import json, sys; sys.path.insert(0, %r)\n"
            "import machine_status as M\n"
            "mod, why = M.rulers()\n"
            "out = {'mod': mod is not None, 'why': why,\n"
            "       'not_in_tree': M._rulers_why('not_in_tree', {'env': M.HEAT_RULERS_ENV}),\n"
            "       'load1': M.load1(), 'heavy': M.heavy_work(), 'cpu': M.cpu_temp()}\n"
            "print(json.dumps(out, ensure_ascii=False, default=str))\n" % str(COCKPIT))
        env = {k: v for k, v in os.environ.items() if k != "EZ2_HEAT_RULERS"}
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env=env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        doc = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertTrue(doc["mod"], doc["why"])
        for key in ("load1", "heavy", "cpu"):
            self.assertNotEqual(doc[key]["detail"], doc["not_in_tree"], doc[key])
        if sys.platform == "darwin":
            # 尺的 uptime / ps 剖法是照 macOS 的輸出寫的;Linux 的 uptime 是「load average: a, b, c」
            self.assertNotEqual(doc["load1"]["state"], "unmeasured", doc["load1"])
            self.assertNotEqual(doc["heavy"]["state"], "unmeasured", doc["heavy"])


class FakeRun:
    """假的 _run:swiftc 第一次照劇本失敗,帶 -vfsoverlay 才成功;其他工具回固定值。"""

    def __init__(self, first_err, overlay_ok=True, macos="15.7.9", minos="15.0"):
        self.first_err = first_err
        self.overlay_ok = overlay_ok
        self.macos = macos
        self.minos = minos
        self.swiftc_calls = []
        self.overlay_text = None

    def __call__(self, cmd, timeout=60, env=None):
        cmd = list(cmd)
        if "-o" in cmd and any(str(c).endswith("hotkey.swift") for c in cmd):
            self.swiftc_calls.append(cmd)
            out = cmd[cmd.index("-o") + 1]
            if "-vfsoverlay" in cmd:
                yml = cmd[cmd.index("-vfsoverlay") + 1]
                self.overlay_text = Path(yml).read_text(encoding="utf-8")
                if self.overlay_ok:
                    Path(out).write_bytes(b"\xcf\xfa\xed\xfe fake")
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                return subprocess.CompletedProcess(cmd, 1, "", "still broken")
            if self.first_err is None:
                Path(out).write_bytes(b"\xcf\xfa\xed\xfe fake")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 1, "", self.first_err)
        if cmd[:1] == ["sw_vers"]:
            return subprocess.CompletedProcess(cmd, 0, self.macos + "\n", "")
        if cmd[:1] in (["vtool"], ["otool"]):
            return subprocess.CompletedProcess(
                cmd, 0, "x:\n cmd LC_BUILD_VERSION\n platform MACOS\n    minos %s\n      sdk 26.0\n" % self.minos, "")
        if cmd[:2] == ["xcrun", "--find"]:
            return subprocess.CompletedProcess(cmd, 0, "/Library/Developer/CommandLineTools/usr/bin/swiftc\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


class BuildRetryTest(unittest.TestCase):
    def setUp(self):
        self.sh = _load_shot_helper()
        self.td = tempfile.TemporaryDirectory(prefix="aib-shot-t-")
        self.home = Path(self.td.name)
        self._orig = (self.sh._run, self.sh.swiftc_cmd)
        self.sh.swiftc_cmd = lambda: ["xcrun", "--sdk", "macosx", "swiftc"]

    def tearDown(self):
        self.sh._run, self.sh.swiftc_cmd = self._orig
        self.td.cleanup()

    def _build(self, fake):
        self.sh._run = fake
        return self.sh.build(home=self.home)

    def test_b1_bridging_error_retries_with_overlay(self):
        fake = FakeRun(BRIDGING_ERR)
        got = self._build(fake)
        self.assertEqual(got.get("reason"), "built", got)
        self.assertEqual(len(fake.swiftc_calls), 2)
        self.assertNotIn("-vfsoverlay", fake.swiftc_calls[0])
        self.assertIn("-vfsoverlay", fake.swiftc_calls[1])
        meta = json.loads((self.home / ".ez2-aib" / "shot" / "build.json").read_text(encoding="utf-8"))
        self.assertTrue(meta.get("vfsoverlay"), meta)

    def test_b2_other_failure_not_retried(self):
        fake = FakeRun("hotkey.swift:3:1: error: cannot find 'NSApp' in scope")
        got = self._build(fake)
        self.assertEqual(got.get("reason"), "build_failed", got)
        self.assertEqual(len(fake.swiftc_calls), 1)

    def test_b3_overlay_points_bridging_to_empty_file(self):
        fake = FakeRun(BRIDGING_ERR)
        self._build(fake)
        self.assertIsNotNone(fake.overlay_text, "沒有帶 -vfsoverlay 重編")
        doc = json.loads(fake.overlay_text)
        names = [r.get("name") for r in doc.get("roots", [])]
        self.assertIn(BRIDGING, names)
        ext = doc["roots"][names.index(BRIDGING)]["external-contents"]
        self.assertNotEqual(ext, BRIDGING)

    def test_b3b_overlay_retry_still_failing_is_build_failed(self):
        fake = FakeRun(BRIDGING_ERR, overlay_ok=False)
        got = self._build(fake)
        self.assertEqual(got.get("reason"), "build_failed", got)
        self.assertEqual(len(fake.swiftc_calls), 2)

    def test_b4_build_json_records_macos_and_rebuilds_on_change(self):
        fake = FakeRun(None, macos="15.7.9", minos="15.0")
        self.assertEqual(self._build(fake).get("reason"), "built")
        meta = json.loads((self.home / ".ez2-aib" / "shot" / "build.json").read_text(encoding="utf-8"))
        self.assertEqual(meta.get("macos"), "15.7.9", meta)
        self.assertEqual(meta.get("minos"), "15.0", meta)
        # 同一台、同一版:快取
        self.assertEqual(self._build(FakeRun(None, macos="15.7.9")).get("reason"), "build_cached")
        # 換一台(或系統升降版):重編
        self.assertEqual(self._build(FakeRun(None, macos="15.6")).get("reason"), "built")

    def test_b4b_minos_above_this_macos_rebuilds(self):
        self._build(FakeRun(None, macos="15.6"))
        meta_path = self.home / ".ez2-aib" / "shot" / "build.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["minos"] = "26.0"          # 別台較新系統編出來、被帶過來的
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        self.assertEqual(self._build(FakeRun(None, macos="15.6")).get("reason"), "built")


if __name__ == "__main__":
    unittest.main(verbosity=2)
