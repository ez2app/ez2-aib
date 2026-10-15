#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""人格卡用 uid 建立、顯示才用標題(the maintainer 10-15:「人格與專案,都應該是用 uid 建立,而顯示才用 title」)。

N1 新卡:aib newrole 在沙盒 vault 建卡 ⇒ 資料夾 = 卡 uid、卡上有 name:、索引顯示名 = 全名(「SEO-」不被當代號剝掉)。
N2 uid 版面:沙盒 vault 已有 .ez2/display/labels.json ⇒ 新卡標題補進 folders[<uid>].title;舊版面不建這份檔。
D1 顯示名順位:labels.json > 卡上 name: > 資料夾名;舊卡(中文資料夾、沒有 name:)顯示照舊。
刀:拿掉「卡上 name: 當顯示名」⇒ D1 的 uid 資料夾卡顯示成 uid(紅)。
治具全在 tmpdir,不碰真 vault / ~/.ez2-aib。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "memory"))
import card_registry as CR  # noqa: E402

CREATE = ROOT / "src" / "identity" / "create_and_bind_persona.py"
PROMPT = "你是「測試員」。\n\n## 職責\n測試。\n\n## 紅線\n不亂寫。\n\n## 知識域\n測試。\n"


def _card(lib, folder, fm_extra=""):
    d = Path(lib) / folder
    d.mkdir(parents=True)
    uid = folder if CR.UID_RE.fullmatch(folder) else "abc%03d" % (len(list(Path(lib).iterdir())))
    (d / "角色.md").write_text('---\nuid: "%s"\n%scode: t\n---\n你是測試。\n' % (uid, fm_extra), encoding="utf-8")
    return uid


class NewCard(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.t = Path(self._tmp.name)
        (self.t / "home").mkdir()
        (self.t / "Vault").mkdir()
        (self.t / "p.md").write_text(PROMPT, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def newrole(self, name):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.t / "home"), "LANG": "en_US.UTF-8"}
        p = subprocess.run([sys.executable, str(CREATE), "--name", name, "--code", "gtest",
                            "--prompt-file", str(self.t / "p.md"), "--vault", str(self.t / "Vault")],
                           capture_output=True, text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-800:])
        out = json.loads(p.stdout[p.stdout.index("{"):])
        return out, Path(out["card_file"])

    def test_N1_new_card_folder_is_uid_and_name_is_shown_in_full(self):
        out, card = self.newrole("SEO-GEO全球測試員")
        self.assertEqual(card.parent.name, out["uid"], "新卡資料夾要是卡 uid,不是標題")
        self.assertIn('name: "SEO-GEO全球測試員"', card.read_text(encoding="utf-8"))
        cards = json.loads((self.t / "home" / ".ez2-aib" / "identity" / "cards.json").read_text(encoding="utf-8"))
        cards = cards.get("cards", cards)
        self.assertEqual(cards[out["uid"]]["display_name"], "SEO-GEO全球測試員", "全名,不剝「SEO-」")
        self.assertFalse((self.t / "Vault" / ".ez2" / "display" / "labels.json").exists(), "舊版面不建 labels.json")

    def test_N2_uid_layout_vault_gets_title_in_labels(self):
        disp = self.t / "Vault" / ".ez2" / "display"
        disp.mkdir(parents=True)
        (disp / "labels.json").write_text(json.dumps({"folders": {"fld_x": {"title": "別的"}}}), encoding="utf-8")
        out, card = self.newrole("全球對談工程師")
        labels = json.loads((disp / "labels.json").read_text(encoding="utf-8"))
        self.assertEqual(labels["folders"][out["uid"]]["title"], "全球對談工程師")
        self.assertEqual(labels["folders"]["fld_x"]["title"], "別的", "原有的標題不能被洗掉")


class Display(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        self.lib = self.vault / "10-Roles"
        self.lib.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def scan(self):
        return CR.scan(str(self.vault), "10-Roles", None)["cards"]

    def test_D1_display_order_labels_then_card_name_then_folder(self):
        u1 = _card(self.lib, "uidaa1", 'name: "卡上名字"\n')
        u2 = _card(self.lib, "uidbb2", 'name: "卡上名字二"\n')
        u3 = _card(self.lib, "PM-專案經理")
        u4 = _card(self.lib, "SEO-GEO大師", 'name: "SEO-GEO大師"\n')
        disp = self.vault / ".ez2" / "display"
        disp.mkdir(parents=True)
        (disp / "labels.json").write_text(json.dumps({"folders": {"uidbb2": {"title": "對照表名字"}}}), encoding="utf-8")
        CR._LABELS_CACHE.clear()
        cards = self.scan()
        self.assertEqual(cards[u1]["display_name"], "卡上名字", "uid 資料夾、沒有對照表 ⇒ 卡上 name:")
        self.assertEqual(cards[u2]["display_name"], "對照表名字", "uid 版面對照表優先")
        self.assertEqual(cards[u3]["display_name"], "專案經理", "舊卡照舊")
        self.assertEqual(cards[u4]["display_name"], "SEO-GEO大師", "卡上給了全名就用全名")
        self.assertEqual(cards[u1]["card_path_rel"], "10-Roles/uidaa1/角色.md")


if __name__ == "__main__":
    unittest.main()
