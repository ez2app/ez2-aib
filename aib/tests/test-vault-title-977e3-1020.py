#!/usr/bin/env python3
"""aib vault title(10-20 對話面板,@abb89 派):Vault 路徑 ⇒ 標題路徑 + obsidian:// 連結,只讀。

T1 絕對路徑:筆記 / 巢狀資料夾 / 資料夾本身 ⇒ title_path、kind、id、obsidian 連結(含中文與空白編碼)
T2 相對路徑:Vault 根、索引沒有的段落照原名、.ez2 底下不給 obsidian 連結
T3 Vault 外:絕對路徑在外、../ 跳出去、Vault 裡的符號連結指到外面 ⇒ in_vault false,不給標題
T4 標題只取 labels:筆記檔裡寫別的 title 不採用、檔不在也照給;方向字元 / 零寬字從標題拿掉
T5 CLI:--json rc 0;--stdin;沒路徑 rc 2;超過上限 rc 2;沒授權(obsidian-labels 加購)rc 3;不寫任何檔
刀(複本,走 run_guarded):K1 不擋 ../ ⇒ T3 紅;K2 不解符號連結 ⇒ T3 紅;K3 不清隱形字 ⇒ T4 紅
I1 新鍵四語都有、英文值沒有中日韓字、佔位符一致
測試明寫 EZ2_AIB_STATE_ROOT、EZ2_AIB_HOME 與暫存 Vault,不碰真庫。
"""
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "vault"))
import aib_testlib  # noqa: E402,F401
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import uid_layout_convert as U  # noqa: E402
import vault_cli as VC  # noqa: E402

WORK = "fld_x_group_08504630b093"
SUB = "fld_x_sub_3f5ce34a8d4e"


def snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aib-vtitle-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.v = self.tmp / "My Vault"
        (self.tmp / "state").mkdir()
        (self.tmp / "home").mkdir()
        env = mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": str(self.tmp / "state"), "EZ2_AIB_HOME": str(self.tmp / "home"),
                                           "EZ2_VAULT_DIR": str(self.v)})
        env.start()
        self.addCleanup(env.stop)
        items = [{"folderId": WORK, "routeKey": "", "folderPath": WORK, "parentFolderId": "", "level": 1, "title": "06-工作區 Work",
                  "legacyPaths": [], "status": "active", "kind": "group"},
                 {"folderId": SUB, "routeKey": "", "folderPath": WORK + "/" + SUB, "parentFolderId": WORK, "level": 2,
                  "title": "專案 A", "legacyPaths": [], "status": "active", "kind": "group"}]
        (self.v / WORK / SUB).mkdir(parents=True)
        (self.v / WORK / SUB / "ab12cd.md").write_text("---\nuid: ab12cd\ntitle: 檔裡的名字\n---\n秘密正文\n", encoding="utf-8")
        notes = {"ab12cd": {"title": "會議紀錄", "path": WORK + "/" + SUB + "/ab12cd.md"},
                 "zz99yy": {"title": "還沒同步的", "path": WORK + "/zz99yy.md"}}
        U.write_indexes(self.v, items, notes, [], "t0")


class TestTitle(Base):
    def test_T1_absolute(self):
        note, folder = VC.titles(self.v, [str(self.v / WORK / SUB / "ab12cd.md"), str(self.v / WORK / SUB)])
        self.assertEqual(note["title_path"], "06-工作區 Work/專案 A/會議紀錄")
        self.assertEqual((note["title"], note["kind"], note["id"], note["in_vault"]), ("會議紀錄", "note", "ab12cd", True))
        self.assertEqual(note["rel"], WORK + "/" + SUB + "/ab12cd.md")
        self.assertEqual(note["obsidian"], "obsidian://open?vault=My%20Vault&file=" + WORK + "%2F" + SUB + "%2Fab12cd.md")
        self.assertEqual((folder["title_path"], folder["kind"], folder["id"]), ("06-工作區 Work/專案 A", "folder", SUB))

    def test_T2_relative(self):
        root, odd, hidden = VC.titles(self.v, [".", WORK + "/不認得/x.md", ".ez2/display/labels.json"])
        self.assertEqual((root["kind"], root["title"], root["rel"]), ("vault", "My Vault", ""))
        self.assertEqual((odd["title_path"], odd["kind"]), ("06-工作區 Work/不認得/x.md", "other"))
        self.assertNotIn("obsidian", hidden)
        self.assertTrue(hidden["in_vault"])

    def test_T3_outside(self):
        (self.tmp / "out").mkdir()
        (self.v / WORK / "link").symlink_to(self.tmp / "out")
        got = VC.titles(self.v, [str(self.tmp / "out" / "a.md"), "../out/a.md", WORK + "/../../out", str(self.v / WORK / "link" / "a.md")])
        for g in got:
            self.assertEqual(set(g), {"path", "in_vault"}, g)
            self.assertFalse(g["in_vault"], g)

    def test_T4_labels_only_and_invisible_stripped(self):
        (self.v / WORK / SUB / "ab12cd.md").unlink()
        self.assertEqual(VC.titles(self.v, [WORK + "/" + SUB + "/ab12cd.md"])[0]["title"], "會議紀錄")
        lab = json.loads((self.v / ".ez2/display/labels.json").read_text(encoding="utf-8"))
        lab["folders"][WORK]["i18n"][U.LOC] = "06-‮工作區​ Work"
        (self.v / ".ez2/display/labels.json").write_text(json.dumps(lab, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(VC.titles(self.v, [WORK])[0]["title"], "06-工作區 Work")

    def test_T5_cli(self):
        before = snapshot(self.v)
        out = io.StringIO()
        with mock.patch.object(VC, "licensed", return_value=(True, "addon")), contextlib.redirect_stdout(out), \
                mock.patch.object(sys, "stdin", io.StringIO(WORK + "\n\n")):
            rc = VC.main(["title", str(self.v / WORK / SUB / "ab12cd.md"), "--stdin", "--json"])
        self.assertEqual(rc, 0)
        res = json.loads(out.getvalue())
        self.assertEqual(res["vault"], "My Vault")
        self.assertEqual([i["title"] for i in res["items"]], ["會議紀錄", "06-工作區 Work"])
        with mock.patch.object(VC, "licensed", return_value=(True, "addon")), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(VC.main(["title"]), 2)
            self.assertEqual(VC.main(["title"] + ["x"] * (VC.TITLE_MAX + 1)), 2)
        with mock.patch.object(VC, "licensed", return_value=(False, "addon-missing:obsidian-labels")), \
                contextlib.redirect_stdout(io.StringIO()) as o:
            self.assertEqual(VC.main(["title", WORK, "--json"]), 3)
        self.assertFalse(json.loads(o.getvalue())["licensed"])
        self.assertEqual(snapshot(self.v), before, "title is read-only")


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(ROOT, "tools/vault/vault_cli.py", needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_K1_dotdot_allowed(self):
        self._knife('            if rel == ".." or rel.startswith("../"):', "            if False:", "TestTitle.test_T3_outside")

    def test_K2_symlink_not_resolved(self):
        self._knife("Path(os.path.realpath(s))", "Path(os.path.normpath(s))", "TestTitle.test_T3_outside")

    def test_K3_invisible_kept(self):
        self._knife('INVISIBLE_RE.sub("", str(i18n.get(U.LOC) or entry.get("title") or ""))',
                    'str(i18n.get(U.LOC) or entry.get("title") or "")', "TestTitle.test_T4_labels_only_and_invisible_stripped")


class TestI18n(unittest.TestCase):
    def test_I1_four_locales(self):
        cjk = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
        new = ("help_vtitle", "help_vtitle_paths", "help_vtitle_stdin", "err_vtitle_none", "err_vtitle_too_many", "vtitle_outside")
        zh = json.loads((ROOT / "i18n" / "zh-TW.json").read_text(encoding="utf-8"))
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for k in new:
                v = d.get("aib.vault_cli." + k)
                self.assertTrue(v, (loc, k))
                self.assertEqual(sorted(re.findall(r"\{\w+\}", v)), sorted(re.findall(r"\{\w+\}", zh["aib.vault_cli." + k])), (loc, k))
                if loc == "en":
                    self.assertIsNone(cjk.search(v), k)


if __name__ == "__main__":
    unittest.main()
