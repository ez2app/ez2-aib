#!/usr/bin/env python3
"""uid 庫轉換後的「新增 / 退役資料夾」(uid_layout_convert folder-add / folder-retire)— 10-16 維護者裁。

uid 庫每個資料夾都要登記在 .ez2/index/folder-id-index.json;直接 mkdir / rm 會讓 verify 變紅
(10-16 真庫:seo-data 直接建 ⇒ dirs_not_folder_id;刪資料夾 ⇒ index_paths_missing)。

A1 父夾底下新增:fld_<模組>_sub_<sha12(舊路徑)>、目錄建好、labels 有標題、verify 綠;同名再加回原本那個
A2 庫根新增 group(帶 routeKey)
A3 名稱不合法 / 父夾不存在 ⇒ 擋下、什麼都沒寫;--dry-run 不寫
R1 退役空資料夾(連子夾):先打包到庫外、索引 / labels 拿掉、目錄刪掉、verify 綠
R2 路由資料夾(或子夾含路由)⇒ 擋下
R3 有筆記沒帶 --include-notes ⇒ 擋;帶了且沒人連 ⇒ uid-index / labels.notes 一起拿掉
R4 筆記還被別處 [[uid]] / (uid.md) 連到 ⇒ 擋下
R5 有非筆記檔 ⇒ 擋下;沒給 --backup-dir ⇒ 擋下;備份放庫裡 ⇒ 擋下
R6 資料夾已經被直接刪掉 ⇒ 只清索引,verify 回綠
R7 --dry-run 什麼都不動
I1 新鍵四語都有、英文值沒有中日韓字
"""
import contextlib
import io
import json
import re
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "tools" / "vault"))
import uid_layout_convert as U  # noqa: E402

NOTE = "---\nuid: {uid}\naliases: [{uid}]\ntitle: \"{title}\"\n---\n{body}\n"


def item(fid, path, parent, level, title, kind, rk=""):
    return {"folderId": fid, "routeKey": rk, "folderPath": path, "parentFolderId": parent, "level": level,
            "title": title, "legacyPaths": [title if level == 1 else "10-看板區 Dashboards/" + title],
            "status": "active", "kind": kind}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uidfolder-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.v = self.tmp / "vault"
        self.bk = self.tmp / "backup"
        board = "fld_x_group_337e040be117"
        route = "fld_c_route_79bb4878617e"
        self.items = [
            item(board, board, "", 1, "10-看板區 Dashboards", "group", "folder.group.x.337e040b"),
            item("fld_x_sub_aaaaaaaaaaaa", board + "/fld_x_sub_aaaaaaaaaaaa", board, 2, "舊報表", "sub"),
            item("fld_x_sub_bbbbbbbbbbbb", board + "/fld_x_sub_aaaaaaaaaaaa/fld_x_sub_bbbbbbbbbbbb",
                 "fld_x_sub_aaaaaaaaaaaa", 3, "子夾", "sub"),
            item("fld_c_group_f7d29f6a675c", "fld_c_group_f7d29f6a675c", "", 1, "C-Ez2 Core系統底層", "group"),
            item(route, "fld_c_group_f7d29f6a675c/" + route, "fld_c_group_f7d29f6a675c", 2, "C80-系統記憶交接",
                 "route", "core.handoff"),
        ]
        for it in self.items:
            (self.v / it["folderPath"]).mkdir(parents=True, exist_ok=True)
        self.note("3aaaa1", board, "看板總覽", "見 [[3bbbb1]]")
        self.uids = [{"uid": "3aaaa1", "path": board + "/3aaaa1.md"}]
        U.write_indexes(self.v, json.loads(json.dumps(self.items)), {"3aaaa1": {"title": "看板總覽"}}, self.uids, "t0")

    def note(self, uid, folder, title, body=""):
        (self.v / folder / (uid + ".md")).write_text(NOTE.format(uid=uid, title=title, body=body), encoding="utf-8")

    def fid(self):
        return json.loads((self.v / ".ez2/index/folder-id-index.json").read_text(encoding="utf-8"))["items"]

    def labels(self):
        return json.loads((self.v / ".ez2/display/labels.json").read_text(encoding="utf-8"))

    def snapshot(self):
        return sorted((str(p.relative_to(self.tmp)), p.read_bytes() if p.is_file() else b"")
                      for p in self.tmp.rglob("*"))

    def verify_ok(self):
        res = U.verify(self.v, cards_path=str(self.tmp / "no-cards.json"))
        bad = {k: v for k, v in res.items() if isinstance(v, list) and v}
        self.assertTrue(res["ok"], bad)

    def refused(self, fn, *a, pattern=None, **kw):
        before = self.snapshot()
        with self.assertRaises(SystemExit) as cm:
            fn(*a, **kw)
        self.assertEqual(before, self.snapshot(), "a refused call must not write anything")
        if pattern:
            self.assertRegex(str(cm.exception.code), pattern)
        return str(cm.exception.code)


class TestFolderAdd(Base):
    def test_A1_add_under_parent(self):
        self.verify_ok()
        out = U.folder_add(self.v, "fld_x_group_337e040be117", "seo-data")
        want = "fld_x_sub_%s" % U.sha12("10-看板區 Dashboards/seo-data")
        self.assertEqual(out["folderId"], want)
        self.assertTrue(out["created"])
        self.assertTrue((self.v / "fld_x_group_337e040be117" / want).is_dir())
        rec = next(i for i in self.fid() if i["folderId"] == want)
        self.assertEqual((rec["parentFolderId"], rec["level"], rec["kind"], rec["title"]),
                         ("fld_x_group_337e040be117", 2, "sub", "seo-data"))
        self.assertEqual(self.labels()["folders"][want]["title"], "seo-data")
        self.assertEqual(self.labels()["notes"], {"3aaaa1": {"title": "看板總覽"}})   # 筆記標籤沿用
        self.verify_ok()
        again = U.folder_add(self.v, "fld_x_group_337e040be117", "seo-data")
        self.assertEqual((again["folderId"], again["created"]), (want, False))
        self.assertEqual(sum(1 for i in self.fid() if i["folderId"] == want), 1)

    def test_A2_add_at_root(self):
        out = U.folder_add(self.v, "", "11-新分區 New")
        fid = "fld_x_group_%s" % U.sha12("11-新分區 New")
        self.assertEqual(out["folderPath"], fid)
        rec = next(i for i in self.fid() if i["folderId"] == fid)
        self.assertEqual((rec["level"], rec["kind"], rec["routeKey"]), (1, "group", "folder.group.x.%s" % U.sha12("11-新分區 New")[:8]))
        self.verify_ok()

    def test_A3_bad_name_unknown_parent_dry_run(self):
        for bad in ("", "  ", "a/b", "a\\b", ".hidden", ".."):
            self.refused(U.folder_add, self.v, "fld_x_group_337e040be117", bad)
        self.refused(U.folder_add, self.v, "fld_x_sub_ffffffffffff", "x", pattern="fld_x_sub_ffffffffffff")
        before = self.snapshot()
        out = U.folder_add(self.v, "fld_x_group_337e040be117", "試跑", dry_run=True)
        self.assertFalse(out["created"])
        self.assertEqual(before, self.snapshot())


class TestFolderRetire(Base):
    def test_R1_retire_empty_with_children(self):
        out = U.folder_retire(self.v, "fld_x_sub_aaaaaaaaaaaa", self.bk)
        self.assertEqual(out["folders"], 2)
        self.assertFalse((self.v / "fld_x_group_337e040be117/fld_x_sub_aaaaaaaaaaaa").exists())
        ids = {i["folderId"] for i in self.fid()}
        self.assertNotIn("fld_x_sub_aaaaaaaaaaaa", ids)
        self.assertNotIn("fld_x_sub_bbbbbbbbbbbb", ids)
        self.assertNotIn("fld_x_sub_bbbbbbbbbbbb", self.labels()["folders"])
        with tarfile.open(out["backup"]) as tar:
            self.assertIn("fld_x_group_337e040be117/fld_x_sub_aaaaaaaaaaaa/fld_x_sub_bbbbbbbbbbbb", tar.getnames())
        self.verify_ok()

    def test_R2_route_refused(self):
        self.refused(U.folder_retire, self.v, "fld_c_route_79bb4878617e", self.bk, pattern="core.handoff")
        self.refused(U.folder_retire, self.v, "fld_c_group_f7d29f6a675c", self.bk, pattern="core.handoff")

    def test_R3_notes_need_flag(self):
        self.note("3bbbb2", "fld_x_group_337e040be117/fld_x_sub_aaaaaaaaaaaa", "舊報表一")
        U.write_indexes(self.v, self.fid(), {"3aaaa1": {"title": "看板總覽"}, "3bbbb2": {"title": "舊報表一"}},
                        self.uids + [{"uid": "3bbbb2", "path": "x"}], "t1")
        self.refused(U.folder_retire, self.v, "fld_x_sub_aaaaaaaaaaaa", self.bk, pattern="--include-notes")
        out = U.folder_retire(self.v, "fld_x_sub_aaaaaaaaaaaa", self.bk, include_notes=True)
        self.assertEqual(out["notes"], ["3bbbb2"])
        uidx = json.loads((self.v / ".ez2/index/uid-index.json").read_text(encoding="utf-8"))["items"]
        self.assertEqual([i["uid"] for i in uidx], ["3aaaa1"])
        self.assertEqual(set(self.labels()["notes"]), {"3aaaa1"})
        self.verify_ok()

    def test_R4_linked_notes_refused(self):
        sub = "fld_x_group_337e040be117/fld_x_sub_aaaaaaaaaaaa"
        self.note("3bbbb1", sub, "被連的")                     # 3aaaa1 正文有 [[3bbbb1]]
        self.refused(U.folder_retire, self.v, "fld_x_sub_aaaaaaaaaaaa", self.bk, include_notes=True,
                     pattern="3aaaa1.md → 3bbbb1")
        (self.v / "fld_x_group_337e040be117/3aaaa1.md").write_text(
            NOTE.format(uid="3aaaa1", title="看板總覽", body="[報表](fld_x_sub_aaaaaaaaaaaa/3bbbb1.md)"), encoding="utf-8")
        self.refused(U.folder_retire, self.v, "fld_x_sub_aaaaaaaaaaaa", self.bk, include_notes=True, pattern="3bbbb1")

    def test_R5_other_files_backup_rules(self):
        self.refused(U.folder_retire, self.v, "fld_x_sub_aaaaaaaaaaaa", None, pattern="--backup-dir")
        self.refused(U.folder_retire, self.v, "fld_x_sub_aaaaaaaaaaaa", self.v / "bk")
        (self.v / "fld_x_group_337e040be117/fld_x_sub_aaaaaaaaaaaa/data.csv").write_text("a,b\n", encoding="utf-8")
        self.refused(U.folder_retire, self.v, "fld_x_sub_aaaaaaaaaaaa", self.bk, include_notes=True, pattern="data.csv")
        self.refused(U.folder_retire, self.v, "fld_x_sub_ffffffffffff", self.bk, pattern="fld_x_sub_ffffffffffff")

    def test_R6_already_deleted_repairs_index(self):
        shutil.rmtree(self.v / "fld_x_group_337e040be117/fld_x_sub_aaaaaaaaaaaa")
        res = U.verify(self.v, cards_path=str(self.tmp / "no-cards.json"))
        self.assertFalse(res["ok"])                              # 直接刪 ⇒ 紅(10-16 真庫遇過)
        out = U.folder_retire(self.v, "fld_x_sub_aaaaaaaaaaaa", None)
        self.assertFalse(out["on_disk"])
        self.assertIsNone(out["backup"])
        self.verify_ok()

    def test_R7_dry_run(self):
        before = self.snapshot()
        out = U.folder_retire(self.v, "fld_x_sub_aaaaaaaaaaaa", self.bk, dry_run=True)
        self.assertEqual((out["folders"], out["dry_run"]), (2, True))
        self.assertEqual(before, self.snapshot())

    def test_cli_round_trip(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = U.main(["folder-add", "--vault", str(self.v), "--parent", "fld_x_group_337e040be117", "--title", "週報"])
        self.assertEqual(rc, 0)
        fid = json.loads(buf.getvalue())["folderId"]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = U.main(["folder-retire", "--vault", str(self.v), "--folder", fid, "--backup-dir", str(self.bk)])
        self.assertEqual(rc, 0)
        self.assertNotIn(fid, {i["folderId"] for i in self.fid()})
        self.verify_ok()


class TestI18n(unittest.TestCase):
    KEYS = ("err_folder_title_bad", "err_folder_unknown", "err_folder_id_taken", "err_folder_is_route",
            "err_folder_has_files", "err_folder_has_notes", "err_notes_linked", "err_backup_required",
            "help_folder_add", "help_folder_parent", "help_folder_retire", "help_folder_backup", "help_folder_include_notes")

    def test_I1_four_locales(self):
        cjk = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for k in self.KEYS:
                v = d.get("aib.vault_convert." + k)
                self.assertTrue(v, (loc, k))
                if loc == "en":
                    self.assertIsNone(cjk.search(v), (k, v))
            zh = json.loads((ROOT / "i18n" / "zh-TW.json").read_text(encoding="utf-8"))
            for k in self.KEYS:
                self.assertEqual(sorted(re.findall(r"\{\w+\}", zh["aib.vault_convert." + k])),
                                 sorted(re.findall(r"\{\w+\}", d["aib.vault_convert." + k])), (loc, k))


if __name__ == "__main__":
    unittest.main()
