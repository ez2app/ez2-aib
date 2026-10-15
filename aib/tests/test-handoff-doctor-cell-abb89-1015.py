#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib doctor 多一格 —— 狀態目錄最新那份 HANDOFF 用 handoff_fields.py --strict-fixed 驗。
固定層六格齊 ⇒ ok;舊單(標題層解得出、沒圍欄)⇒ ok 但 detail 以 △ 開頭說「舊單」(doctor 沒有黃,狀態集合是固定的);
缺格 ⇒ fail 附修法;沒有交接單 ⇒ ok 說出來;解析器不在 ⇒ fail。「最新」看 mtime,印名字。入口格:CLI --json 印得出這一格。
"""
import shutil
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(os.environ.get("AIB_41B_PRODUCT_ROOT", pathlib.Path(__file__).resolve().parents[1]))
DOCTOR_DIR = pathlib.Path(os.environ.get("AIB_41B_DOCTOR_DIR", ROOT / "src" / "bootstrap"))
sys.path.insert(0, str(DOCTOR_DIR))
import aib_doctor as D  # noqa: E402

FIXED = """# 交接 root@abcde 第一棒 → 第二棒
```handoff-fields
主線 sha: 0123abcd
在飛候選: 無
等誰: the maintainer ← x
可砍樹: 無
三記憶: 席位 a ; Claude b ; 人格 c
監視: log x.log ; 看 DONE ; DONE ff ; STOP 停
```
"""
LEGACY = """# 交接 root@abcde 第一棒 → 第二棒
## 0. 主線
- origin/main = **0123abcd**
## 1. 在飛
- 候選 **89abcdef**,log x.log
- DONE 的樣子:尾行 DONE
- STOP 的樣子:✘
## 4. 等 the maintainer
x
## 7. 可砍工作樹
無
## 8. 三記憶
a;b;c
"""


class DoctorHandoffCell(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="d41b-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)   # 10-08:aib test 點名這支留暫存資料夾
        (self.tmp / "pending").mkdir()

    def w(self, name, text, age=0):
        p = self.tmp / "pending" / name
        p.write_text(text, encoding="utf-8")
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def test_fixed_is_ok_and_names_seat(self):
        self.w("HANDOFF-root-abcde-z9-0912-p2.md", FIXED)
        r = D.check_handoff_fields(self.tmp, ROOT)
        self.assertEqual(r["status"], D.OK, r)
        self.assertIn("六格齊", r["detail"]); self.assertIn("abcde", r["detail"])

    def test_legacy_is_ok_but_marked_old(self):
        self.w("HANDOFF-root-abcde-z9-0912-p1.md", LEGACY)
        r = D.check_handoff_fields(self.tmp, ROOT)
        self.assertEqual(r["status"], D.OK, r)
        self.assertTrue(r["detail"].startswith("△ 舊單"), r["detail"])

    def test_missing_field_is_fail_with_remedy(self):
        self.w("HANDOFF-root-abcde-z9-0912-p3.md", FIXED.replace("監視: log x.log ; 看 DONE ; DONE ff ; STOP 停\n", ""))
        r = D.check_handoff_fields(self.tmp, ROOT)
        self.assertEqual(r["status"], D.FAIL, r)
        self.assertIn("監視", r["detail"]); self.assertIn("--strict-fixed", r["remedy"])

    def test_latest_by_mtime_and_name_printed(self):
        self.w("HANDOFF-root-abcde-z9-0912-p1.md", LEGACY, age=600)      # 舊
        self.w("HANDOFF-root-abcde-z9-0912-p2.md", FIXED, age=0)         # 新
        r = D.check_handoff_fields(self.tmp, ROOT)
        self.assertEqual(r["status"], D.OK); self.assertIn("p2.md", r["detail"])

    def test_no_handoff_is_ok_but_said(self):
        r = D.check_handoff_fields(self.tmp, ROOT)
        self.assertEqual(r["status"], D.OK); self.assertIn("沒有交接單可驗", r["detail"])

    def test_parser_missing_is_fail(self):
        self.w("HANDOFF-root-abcde-z9-0912-p2.md", FIXED)
        r = D.check_handoff_fields(self.tmp, self.tmp)      # repo_root 指向沒有工具的地方
        self.assertEqual(r["status"], D.FAIL); self.assertIn("解析器不在", r["detail"])

    def test_entry_cli_json_prints_the_cell(self):
        self.w("HANDOFF-root-abcde-z9-0912-p2.md", FIXED)
        proc = subprocess.run([sys.executable, str(DOCTOR_DIR / "aib_doctor.py"), "--json", "--state-root", str(self.tmp),
                               "--repo-root", str(ROOT), "--provider", "zzz"],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)
        data = json.loads(proc.stdout)
        rows = [c for c in data["checks"] if c["id"] == "handoff_fields"]
        self.assertEqual(len(rows), 1, "doctor 沒有印出交接單那一格")
        self.assertEqual(rows[0]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
