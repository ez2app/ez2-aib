#!/usr/bin/env python3
"""右欄 🧠 行:uid 路徑換成 labels.json 的中文名(a63ab 11-09;Andy 問為什麼畫 fld_… 路徑)。

- folder_title:路徑最後一段(folderId)查 <vault>/.ez2/display/labels.json;zh-TW 優先、再 title;
  查不到 / 沒 vault / 檔壞 ⇒ ''(畫原路徑);消毒同資料夾路徑,拒 `@` `|`。
- vault_dir 走全套件同一條規則(EZ2_VAULT_DIR 等),所以每位會員的 vault 都適用,不是只有這台。
- 畫面:中文名不是路徑 ⇒ 太長時從尾端讓位;uid 路徑照舊從前面截。

刀(KNIFE):folder_title 拿掉查表(直接 return '')⇒ test_uid_folder_gets_its_chinese_title 紅。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
sys.path.insert(0, os.path.join(SRC, "cockpit"))
sys.path.insert(0, os.path.join(SRC, "cockpit", "render"))

import seat_persona as SP  # noqa: E402

FOLDER = "fld_p_group_5d4b60295e72/fld_p_route_d229ff09b371/fld_p_sub_e58aba460624"


_MADE = []


def tmpdir(prefix):
    d = tempfile.mkdtemp(prefix=prefix)
    _MADE.append(d)
    return d


def tearDownModule():
    for d in _MADE:
        shutil.rmtree(d, ignore_errors=True)


def vault(folders):
    d = tmpdir("sp-label-")
    os.makedirs(os.path.join(d, ".ez2", "display"))
    with open(os.path.join(d, ".ez2", "display", "labels.json"), "w", encoding="utf-8") as fh:
        json.dump({"folders": folders}, fh, ensure_ascii=False)
    return d


class FolderTitle(unittest.TestCase):
    def setUp(self):
        SP._LABELS_CACHE.clear()

    def test_uid_folder_gets_its_chinese_title(self):
        v = vault({"fld_p_sub_e58aba460624": {"title": "AIB 2.0 主線核心",
                                              "i18n": {"zh-TW": "AIB 2.0 主線核心", "en": None}}})
        self.assertEqual(SP.folder_title(FOLDER, v), "AIB 2.0 主線核心")
        self.assertEqual(SP.folder_title(FOLDER + "/", v), "AIB 2.0 主線核心")

    def test_zh_tw_label_wins_then_title(self):
        v = vault({"fld_a": {"title": "Core", "i18n": {"zh-TW": "核心"}},
                   "fld_b": {"title": "只有標題", "i18n": {"zh-TW": None}}})
        self.assertEqual(SP.folder_title("x/fld_a", v), "核心")
        self.assertEqual(SP.folder_title("x/fld_b", v), "只有標題")

    def test_not_found_means_empty_so_the_path_is_drawn(self):
        v = vault({"fld_other": {"title": "別的"}})
        self.assertEqual(SP.folder_title(FOLDER, v), "")
        self.assertEqual(SP.folder_title(FOLDER, None), "")
        self.assertEqual(SP.folder_title("", v), "")
        self.assertEqual(SP.folder_title(FOLDER, tmpdir("sp-old-")), "")   # 舊版面:沒有 labels.json

    def test_a_broken_labels_file_is_not_a_crash(self):
        d = vault({})
        with open(os.path.join(d, ".ez2", "display", "labels.json"), "w") as fh:
            fh.write("{not json")
        self.assertEqual(SP.folder_title(FOLDER, d), "")
        with open(os.path.join(d, ".ez2", "display", "labels.json"), "w") as fh:
            fh.write('{"folders": ["a list"]}')
        SP._LABELS_CACHE.clear()
        self.assertEqual(SP.folder_title(FOLDER, d), "")

    def test_titles_are_cleaned_and_impersonation_refused(self):
        v = vault({"fld_c": {"title": "壞\x1b[31m字"}, "fld_at": {"title": "假的@abcde"},
                   "fld_bar": {"title": "多|一欄"}})
        self.assertNotIn("\x1b", SP.folder_title("x/fld_c", v))
        self.assertEqual(SP.folder_title("x/fld_at", v), "")
        self.assertEqual(SP.folder_title("x/fld_bar", v), "")

    def test_a_changed_labels_file_is_read_again(self):
        v = vault({"fld_a": {"title": "舊名"}})
        self.assertEqual(SP.folder_title("x/fld_a", v), "舊名")
        p = os.path.join(v, ".ez2", "display", "labels.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"folders": {"fld_a": {"title": "新名"}}}, fh, ensure_ascii=False)
        st = os.stat(p)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        self.assertEqual(SP.folder_title("x/fld_a", v), "新名")


class VaultForEveryMember(unittest.TestCase):
    def test_vault_dir_follows_ez2_vault_dir(self):
        d = tmpdir("sp-member-")
        old = os.environ.get("EZ2_VAULT_DIR")
        os.environ["EZ2_VAULT_DIR"] = d
        try:
            self.assertEqual(SP.vault_dir(), d)
        finally:
            if old is None:
                os.environ.pop("EZ2_VAULT_DIR", None)
            else:
                os.environ["EZ2_VAULT_DIR"] = old


class Drawn(unittest.TestCase):
    def rows(self, folder, is_title, width):
        import render_lib as RL
        data = {"agents": [{"name": "dev", "window": "@7", "persona_display": "核心架構師",
                            "persona_folder": folder, "persona_folder_is_title": is_title}]}
        lines, _ = RL.render_ai_list_with_map(data, width=width)
        import re
        return [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in lines if "🧠" in l]

    def test_a_title_is_cut_at_its_end_a_path_at_its_front(self):
        title = "AIB 2.0 主線核心與跨平台重構的很長名字"
        t = self.rows(title, True, 40)
        self.assertTrue(t, "no brain row drawn")
        self.assertIn("AIB 2.0", t[0])          # 開頭留著(從尾端讓位)
        p = self.rows(FOLDER, False, 40)
        self.assertIn("…", p[0])
        self.assertNotIn("fld_p_group", p[0])   # 路徑從前面截
        self.assertIn("460624", p[0])           # 尾端(認得出是哪一夾)留著


if __name__ == "__main__":
    unittest.main()
