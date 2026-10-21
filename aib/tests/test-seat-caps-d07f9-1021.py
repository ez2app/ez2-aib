#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib seat caps(PROPOSAL-gaps v3 第 5 件):外掛照穩定 key 決定熱點顯不顯示,不看版本號。

A1 --json:{"v":1,"aib_version","caps":[排序過的 key]};這份核心一定有的 key 都在(seat.input / chat.events / seat.caps …)
A2 key 跟著程式走:檢查不過(檔不在、函式不在)⇒ key 不出現;key 名都長 <域>.<名>(小寫、點、底線)
A3 不 import 任何被檢查的模組(不會有副作用):跑完 sys.modules 沒多 seat_act / chat_bridge
A4 --write-vault:寫 <EZ2_VAULT_DIR>/.ez2/caps.json(跟 --json 同內容);沒設 EZ2_VAULT_DIR ⇒ rc 2 why=no-vault、不猜位置
A5 aib seat caps 有接上、說明四語都有;不認得的參數 rc 2
A6 版本讀 aib/VERSION(唯一來源):暫存樹只有 VERSION、沒 pyproject 也讀得到;跟真樹的 VERSION 一致
刀(複本):K1 檢查一律回 True ⇒ A2 紅
"""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(HERE))
import caps as C  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402


class Caps(unittest.TestCase):
    def run_main(self, *argv, env=None):
        buf = io.StringIO()
        rc = C.main(list(argv), out=buf, env=env or {})
        return rc, buf.getvalue()

    def test_a1_json(self):
        rc, out = self.run_main("--json")
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(d["v"], 1)
        self.assertRegex(d["aib_version"], r"^\d+\.\d+\.\d+")
        self.assertEqual(d["caps"], sorted(d["caps"]))
        for k in ("seat.caps", "seat.input", "seat.transcript", "seat.pending", "chat.events", "chat.notice.login_needed"):
            self.assertIn(k, d["caps"], k)

    def test_a2_follows_code(self):
        for k in C.CAPS:
            self.assertRegex(k, r"^[a-z]+(\.[a-z_]+)+$", k)
        orig = dict(C.CAPS)
        self.addCleanup(lambda: (C.CAPS.clear(), C.CAPS.update(orig)))
        C.CAPS["x.missing_file"] = C._has_file("no_such_file.py")
        C.CAPS["x.missing_def"] = C._has_def("seat_act.py", "no_such_function")
        C.CAPS["x.present_def"] = C._has_def("seat_act.py", "do_input")
        C.CAPS["x.missing_text"] = C._has_text("seat_panel.py", "zzz-not-here-zzz")
        caps = C.payload()["caps"]
        self.assertNotIn("x.missing_file", caps)
        self.assertNotIn("x.missing_def", caps)
        self.assertNotIn("x.missing_text", caps)
        self.assertIn("x.present_def", caps)

    def test_a3_no_imports(self):
        code = ("import sys; sys.path.insert(0, %r); import caps; caps.payload(); "
                "print(sorted(m for m in ('seat_act','chat_bridge','seat_panel','chat_nostr','dm_global_files') if m in sys.modules))"
                % str(COCKPIT))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.strip(), "[]", r.stderr)

    def test_a4_vault(self):
        rc, out = self.run_main("--write-vault", "--json", env={})
        self.assertEqual((rc, json.loads(out)["why"]), (2, "no-vault"))
        with tempfile.TemporaryDirectory() as v:
            rc, out = self.run_main("--write-vault", "--json", env={"EZ2_VAULT_DIR": v})
            self.assertEqual(rc, 0)
            f = Path(v) / ".ez2" / "caps.json"
            self.assertEqual(json.loads(f.read_text(encoding="utf-8")), json.loads(out))

    def test_a5_wired(self):
        src = (COCKPIT / "aib").read_text(encoding="utf-8")
        self.assertRegex(src, r'caps\) exec "\$\{EZ2_PY3:-python3\}" "\$HERE/caps\.py" "\$\{@:3\}"')
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            self.assertIn("  caps [--json] [--write-vault]", d["aib.seat.help.usage"], lang)
        self.assertEqual(self.run_main("--bogus")[0], 2)
        self.assertEqual(self.run_main("--help")[0], 0)

    def test_a6_version_file(self):
        self.assertEqual(C.aib_version(), (REPO / "VERSION").read_text(encoding="utf-8").strip())
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "site" / "aib"
            root.mkdir(parents=True)
            (root / "VERSION").write_text("9.8.7\n", encoding="utf-8")
            self.assertEqual(C.aib_version(root), "9.8.7")
            (root / "VERSION").unlink()
            self.assertIsNone(C.aib_version(root), "兩個都沒有 ⇒ null,不猜")


class Knife(unittest.TestCase):
    def test_k1_always_true(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/caps.py", "    return {\"v\": V, \"aib_version\": aib_version(), \"caps\": sorted(k for k, chk in CAPS.items() if chk())}",
                        "    return {\"v\": V, \"aib_version\": aib_version(), \"caps\": sorted(CAPS)}", tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Caps.test_a2_follows_code"], cwd=str(root),
                            max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
