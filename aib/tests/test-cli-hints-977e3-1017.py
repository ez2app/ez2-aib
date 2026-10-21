#!/usr/bin/env python3
"""公開版用起來看不懂的三個提示(10-17 @977e3 在 6ddf 用公開版 ez2-aib 碰到)。

H1 `aib dm --help`:rc 0、印用法、不印 RESULT 行(說明不是一次送信);以前回「未知選項」、RESULT stage=abort
H2 真的未知選項照舊 rc 2、RESULT stage=abort,錯誤訊息指到 `aib dm --help`
H3 狀態列的待審框提示(四語)照著打就能跑:read 帶 --seat(dm_queue.py read 少了 --seat 是用法錯)
H4 `aib vault newpost`:收件匣登記在索引、硬碟上沒有 ⇒ 錯誤訊息帶那個資料夾的完整路徑(以前只說「資料夾不存在」)
H5 folder-add 父資料夾硬碟上沒有 ⇒ 同樣帶路徑
I1 新鍵四語都有、佔位符一致、英文值沒有中日韓字
刀(複本,走 run_guarded):K1 拿掉 --help 那條 ⇒ H1 紅;K2 newpost 退回不帶路徑的舊鍵 ⇒ H4 紅
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

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "vault"))
import aib_testlib  # noqa: E402,F401
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import uid_layout_convert as U  # noqa: E402
import vault_cli as VC  # noqa: E402

DM_CTL = ROOT / "src" / "cockpit" / "dm-ctl.sh"
LANGS = ("zh-TW", "en", "ja", "ko")
INBOX = "fld_x_group_bfecfae260f8"
CORE = "fld_c_group_f7d29f6a675c"


def item(fid, path, parent, level, title, kind, rk=""):
    return {"folderId": fid, "routeKey": rk, "folderPath": path, "parentFolderId": parent, "level": level,
            "title": title, "legacyPaths": [title], "status": "active", "kind": kind}


def i18n(lang):
    return json.loads((ROOT / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8"))


class DmHelp(unittest.TestCase):
    def run_ctl(self, *args):
        return subprocess.run(["bash", str(DM_CTL), *args], capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL)

    def test_h1_help(self):
        for flag in ("--help", "-h"):
            p = self.run_ctl(flag)
            self.assertEqual(p.returncode, 0, flag + p.stderr)
            self.assertIn("aib dm <", p.stdout, flag)
            self.assertIn("--file", p.stdout, flag)
            self.assertNotRegex(p.stdout, r"(?m)^RESULT ", flag)
            self.assertNotIn("未知選項", p.stderr, flag)

    def test_h2_unknown_option_points_to_help(self):
        p = self.run_ctl("--bogus")
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("aib dm --help", p.stderr)
        self.assertEqual(p.stdout.strip().splitlines()[-1].split()[:2], ["RESULT", "stage=abort"])


class InboxHint(unittest.TestCase):
    def test_h3_read_has_seat(self):
        for lang in LANGS:
            v = i18n(lang)["mod.aib_boards.seat_p_inbox_unread"]
            m = re.search(r"read (--\S+ \S+ ?)+", v)
            self.assertTrue(m, lang)
            self.assertIn("--seat {code}", m.group(0), lang)
        p = subprocess.run([sys.executable, str(ROOT / "src" / "cockpit" / "dm_queue.py"), "read", "--id", "q_x",
                            "--by", "seat:abcde", "--if-rev", "1"], capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL, env={k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")})
        self.assertEqual(p.returncode, 2)
        # 提示沒帶 --seat 就是這個錯;帶了才照著打得動(0.1.37 #242 起量不到席位時回 JSON 在 stdout)
        self.assertIn("--seat", p.stdout + p.stderr)


class DirMissing(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aib-clihint-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.v = self.tmp / "vault"
        items = [item(INBOX, INBOX, "", 1, "00-暫存區 Inbox", "group", "folder.group.x.bfecfae2"),
                 item(CORE, CORE, "", 1, "C-Ez2 Core系統底層", "group")]
        self.v.mkdir()
        U.write_indexes(self.v, items, {}, [], "t0")

    def test_h4_newpost_names_the_folder(self):
        with self.assertRaises(VC.VaultError) as cm:
            VC.newpost(self.v, "測試")
        msg = str(cm.exception)
        self.assertIn(str(self.v.resolve() / INBOX), msg)
        self.assertIn("aib vault doctor", msg)
        self.assertEqual(list(self.v.rglob("*.md")), [])

    def test_h5_folder_add_parent_names_the_folder(self):
        with self.assertRaises(SystemExit) as cm:
            U.folder_add(self.v, CORE, "子資料夾")
        self.assertIn(CORE, str(cm.exception.code))
        self.assertIn("aib vault doctor", str(cm.exception.code))


class Keys(unittest.TestCase):
    def test_i1_new_key_four_langs(self):
        key = "aib.vault_convert.err_dir_missing_at"
        holes = [set(re.findall(r"\{\w+\}", i18n(lang).get(key, ""))) for lang in LANGS]
        self.assertTrue(all(h == {"{path}"} for h in holes), holes)
        self.assertNotRegex(i18n("en")[key], r"[぀-ヿ㐀-鿿가-힯]")


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(ROOT, rel, needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_no_help_branch(self):
        self._knife("src/cockpit/dm-ctl.sh", "    -h|--help)\n", "    --no-such-help-flag)\n", "DmHelp.test_h1_help")

    def test_k2_newpost_old_key(self):
        self._knife("tools/vault/vault_cli.py", 'U.T("err_dir_missing_at", {"path": str(d)})', 'U.T("err_dir_missing")',
                    "DirMissing.test_h4_newpost_names_the_folder")


if __name__ == "__main__":
    unittest.main(verbosity=2)
