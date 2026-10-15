#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""第 41 項的尺:交接單六格缺一必紅;p7 那份要解得出來;接自誰從它算。

每條判準各一正一負(還原刀=拿掉那一格,必紅)。治具自建,不讀活的 pending/。
★ p7 實例:本尺帶一份 p7 的**凍結副本**當治具(檔尾 P7_SNAPSHOT),不讀 ~/.ez2-aib/pending/ ——
  活檔會被改寫,尺讀活檔就等於量「今天那份長什麼樣」而不是「解析器會不會退化」。

★ 已知盲區(已裁定先不解):標題層的「等誰」抓的是整節散文、不切筆,
  機器分不出「等條件」與「等真人」——只有固定層(圍欄)才分得開。本尺對標題層只釘「有沒有那一格」,
  不釘「分不分得出等誰」;p8 起交接單改用固定層,標題層只為舊單存在。
"""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(os.environ.get("AIB_HANDOFF_FIELDS_ROOT", pathlib.Path(__file__).resolve().parents[1]))
TOOL = REPO / "tools" / "dispatch" / "handoff_fields.py"
I18N = os.environ.get("AIB_HANDOFF_FIELDS_I18N_DIR")

FIXED_OK = """# 交接 root@abcde 第一棒 → 第二棒(艙 z999)
```handoff-fields
接自: root@abcde 第一棒 → 第二棒
主線 sha: 0123abcd(= 本地 main)
在飛候選: 89abcdef ~/worktrees/x v0.0.1
等誰: tag ← 發版 pid 1 ; the maintainer ← some-decision-id
可砍樹: x(DONE 後) ; 不可砍: y
三記憶: 席位 ~/m/context.md ; Claude a.md ; 人格 b.md
監視: log pending/R.log ; 看 尾行「已原子推送」 ; DONE ff-only ; STOP 洗殼單跑
```
散文隨便寫。
"""


def run(path, *flags):
    env = dict(os.environ, PYTHON_COLORS="0")
    if I18N:
        env["AIB_HANDOFF_FIELDS_I18N_DIR"] = I18N
    p = subprocess.run([sys.executable, str(TOOL), str(path), *flags], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, env=env)
    return p.returncode, p.stdout, p.stderr


class HandoffFieldsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="hf-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)   # 10-08:aib test 點名這支留暫存資料夾

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def w(self, name, text):
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_fixed_all_six_rc0_and_from_fence(self):
        p = self.w("HANDOFF-root-abcde-z999-0912-p1.md", FIXED_OK)
        rc, out, err = run(p, "--json")
        self.assertEqual(rc, 0, err)
        d = json.loads(out)
        self.assertEqual(d["layer"], "fixed")
        self.assertEqual(d["missing"], [])
        self.assertEqual(d["from_seat"], "abcde")
        self.assertEqual(d["from"]["source"], "fence")
        self.assertEqual(d["from"]["baton"], 1)   # 「第一棒 → 第二棒」交出的是第一棒;整數不是字串
        self.assertEqual(d["fields"]["主線 sha"][0]["line"], 4)

    def test_knife_each_missing_key_is_red_and_named(self):
        for key in ["主線 sha", "在飛候選", "等誰", "可砍樹", "三記憶", "監視"]:
            txt = "\n".join(ln for ln in FIXED_OK.splitlines() if not ln.startswith(key + ":")) + "\n"
            p = self.w("HANDOFF-root-abcde-z999-0912-p1.md", txt)
            rc, out, err = run(p, "--json")
            self.assertEqual(rc, 3, key)
            self.assertEqual(json.loads(out)["missing"], [key])
            self.assertIn(key, err)

    def test_from_seat_falls_back_to_filename(self):
        txt = FIXED_OK.replace("接自: root@abcde 第一棒 → 第二棒\n", "")
        p = self.w("HANDOFF-pm-fedcb-z999-0912-p3.md", txt)
        rc, out, _ = run(p, "--json")
        d = json.loads(out)
        self.assertEqual((rc, d["from_seat"], d["from"]["source"], d["from"]["baton"]), (0, "fedcb", "filename", 3))

    def test_from_seat_unmeasured_not_guessed(self):
        txt = FIXED_OK.replace("接自: root@abcde 第一棒 → 第二棒\n", "").replace("# 交接 root@abcde", "# 交接")
        p = self.w("anything.md", txt)
        rc, out, _ = run(p, "--json")
        d = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertIsNone(d["from_seat"])
        self.assertEqual(d["from"]["source"], "unmeasured")
        self.assertEqual(d["from"]["baton"], 1)   # 棒次與席碼各自獨立:標題有「第一棒 →」就取得到,席碼仍 null
        p2 = self.w("anything.md", txt.replace("第一棒 → 第二棒", "接棒"))
        self.assertIsNone(json.loads(run(p2, "--json")[1])["from"]["baton"])  # 真的沒棒次 ⇒ null,不是 0 不是 ""

    def test_p7_snapshot_legacy_layer_all_six(self):
        p = self.w("HANDOFF-root-fedcb-a801-0912-p7.md", P7_SNAPSHOT)
        rc, out, err = run(p, "--json")
        self.assertEqual(rc, 0, err)
        d = json.loads(out)
        self.assertEqual(d["layer"], "legacy")
        self.assertEqual(d["missing"], [])
        self.assertEqual(d["from_seat"], "fedcb")
        self.assertEqual(d["from"]["baton"], 7)   # 檔名 p7 優先;標題「第五棒」是人寫的棒次語意,不拿來蓋檔名
        self.assertEqual(d["fields"]["主線 sha"][0]["value"], "5ef078ac")
        self.assertEqual(d["fields"]["在飛候選"][0]["value"], "6cbee150")
        self.assertIn("RELEASE-0592-0912.log", d["fields"]["監視"][0]["value"])

    def test_p7_knife_drop_monitor_section_is_red(self):
        txt = "\n".join(ln for ln in P7_SNAPSHOT.splitlines() if "DONE 的樣子" not in ln)
        p = self.w("HANDOFF-root-fedcb-a801-0912-p7.md", txt)
        rc, out, _ = run(p, "--json")
        self.assertEqual(rc, 3)
        self.assertEqual(json.loads(out)["missing"], ["監視"])

    def test_strict_fixed_rejects_legacy_only(self):
        p = self.w("HANDOFF-root-fedcb-a801-0912-p7.md", P7_SNAPSHOT)
        rc, _, err = run(p, "--strict-fixed")
        self.assertEqual(rc, 4)
        self.assertIn("rc=4", err)
        p2 = self.w("HANDOFF-root-abcde-z999-0912-p1.md", FIXED_OK)
        self.assertEqual(run(p2, "--strict-fixed")[0], 0)

    def test_not_a_handoff_rc2_and_missing_file_rc2(self):
        p = self.w("notes.md", "# 隨便\n一些字\n")
        self.assertEqual(run(p)[0], 2)
        self.assertEqual(run(self.tmp / "nope.md")[0], 2)

    def test_human_output_has_no_missing_i18n_keys(self):
        p = self.w("HANDOFF-root-abcde-z999-0912-p1.md", FIXED_OK)
        rc, out, _ = run(p)
        self.assertEqual(rc, 0)
        self.assertNotIn("!!", out)
        self.assertIn("(第 4 行)", out)


P7_SNAPSHOT = r'''# 交接 root@fedcb 第五棒 → 第六棒(艙 a801;2026-09-12 02:1x)—— 機器可讀版(第 41 項的第一個實例)
艙變數:略

## 0. 主線(02:4x 第五棒交棒)
- origin/main = 本地 main = **5ef078ac**(今晚 8 批)
- 本地 tag v0.59.0/0.59.1 不推;VERSION 0.59.2-alpha.1

## 1. 在飛(接手第一件事)
- **發版 v0.59.2-alpha.1 第四次起 02:09**:pid 78285,log `pending/RELEASE-0592-0912.log`,候選 **6cbee150**,凍結上限 5400。
- **DONE 的樣子**:log 尾「已原子推送 main、tag 與 refs/notes/commits」;之後 ff-only。
- **STOP 的樣子**:log 裡 ✘ 行;修法放另開的工作樹。

## 2. 等推(tag 落地後走 live)
- `~/worktrees/aib-docs-fz-0912` 75c4b25a + 未提交。

## 4. 等 the maintainer(待決簿)
mac01-python-setup-strategy・帳單走向

## 7. 可砍工作樹(cherry 0 且乾淨才砍)
發版後:aib-release-0592。不可砍:aib-dmq-global-0912。

## 8. 三記憶
席位 ~/.ez2-aib/memory/fedcb/context.md;Claude root-seat-fedcb-0910-p1.md;人格 PERSONA-ADDENDUM-fedcb-0911.md
'''

if __name__ == "__main__":
    unittest.main()
