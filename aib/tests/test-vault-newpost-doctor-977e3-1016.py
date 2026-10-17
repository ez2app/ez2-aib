#!/usr/bin/env python3
"""aib vault newpost / doctor(10-16 維護者裁;受 aib 授權管理,加購 obsidian-labels,授權失效就不跑)。

V1 沒授權 ⇒ 結束碼 3、什麼都不寫(子行程,暫存 HOME 沒有授權檔)
V2 newpost:收件匣 / 指定資料夾、uid 檔名與 frontmatter、routeKey 照最近的路由祖先、uid-index 與 labels 登記、
   verify 綠;同一毫秒連發兩篇不撞號;資料夾不存在 ⇒ 錯;新筆記沒有 orig_name 欄(10-17:以前寫 <uid>.md,沒意義)
V3 doctor 預設只列不改(整個暫存目錄位元組不變)
V4 doctor --fix:手建資料夾改 folderId 並登記(標題 = 原名)、手建筆記補 uid 改名、[[原名]] 連結改成 [[uid|原名]]、
   檔名是 uid 但缺 frontmatter uid ⇒ 補、索引裡已不在的資料夾 ⇒ 拿掉;先打包到庫外;修完 verify 綠
V5 --fix 沒給備份位置 / 備份放庫裡 ⇒ 擋下不寫
V6 uid 重複只列不修、ok = False
V7 updated「本機時間 + 空格」⇒ 列出、照本機時區換成 ISO(台北 00:12:16 ⇒ 前一天 16:12:16.000Z);只有日期 / 沒秒沒時區 ⇒
   只在 notices 列、不算問題;預設不改
V8 --fix 只改 updated 那一行(正文、其他欄位、只有日期的那篇位元組不變)、先打包、verify 綠、再跑 ok
刀(複本,走 run_guarded):X1 換算不照時區 ⇒ V7 紅;X2 換行時把 updated 後面的欄位丟掉 ⇒ V8 紅;
   X3 newpost 又寫 <uid>.md 當原名 ⇒ V2 紅
I1 新鍵四語都有、英文值沒有中日韓字、佔位符一致
"""
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
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

INBOX = "fld_x_group_bfecfae260f8"
WORK = "fld_x_group_08504630b093"
ROUTE = "fld_c_route_79bb4878617e"
CORE = "fld_c_group_f7d29f6a675c"


def item(fid, path, parent, level, title, kind, rk=""):
    return {"folderId": fid, "routeKey": rk, "folderPath": path, "parentFolderId": parent, "level": level,
            "title": title, "legacyPaths": [title], "status": "active", "kind": kind}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aib-vaultcli-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.v = self.tmp / "vault"
        self.bk = self.tmp / "backup"
        items = [item(INBOX, INBOX, "", 1, "00-暫存區 Inbox", "group", "folder.group.x.bfecfae2"),
                 item(WORK, WORK, "", 1, "06-工作區 Work", "group", "folder.group.x.08504630"),
                 item(CORE, CORE, "", 1, "C-Ez2 Core系統底層", "group"),
                 item(ROUTE, CORE + "/" + ROUTE, CORE, 2, "C80-系統記憶交接", "route", "core.handoff")]
        for it in items:
            (self.v / it["folderPath"]).mkdir(parents=True, exist_ok=True)
        (self.v / WORK / "3aaaa1.md").write_text(
            "---\nuid: 3aaaa1\naliases: [3aaaa1]\ntitle: \"看板\"\n---\n見 [[會議記錄]] 與 [[會議記錄|這份]],別動 [[會議]]\n",
            encoding="utf-8")
        U.write_indexes(self.v, items, {"3aaaa1": {"title": "看板"}}, [{"uid": "3aaaa1", "path": WORK + "/3aaaa1.md"}], "t0")
        self.ok_verify()

    def ok_verify(self):
        res = U.verify(self.v, cards_path=str(self.tmp / "no-cards.json"))
        self.assertTrue(res["ok"], {k: v for k, v in res.items() if isinstance(v, list) and v})

    def snapshot(self):
        return sorted((str(p.relative_to(self.tmp)), p.read_bytes() if p.is_file() else b"") for p in self.tmp.rglob("*"))

    def index(self, name):
        sub = "display" if name == "labels.json" else "index"
        return json.loads((self.v / ".ez2" / sub / name).read_text(encoding="utf-8"))

    def messy(self):
        """在 Obsidian 手動做的事:建資料夾、Ctrl+N 新筆記、被連結的筆記、缺 uid 的 uid 名筆記、直接刪掉登記過的資料夾。"""
        d = self.v / WORK / "我的新夾"
        d.mkdir()
        (d / "未命名.md").write_text("隨手記\n", encoding="utf-8")
        (self.v / WORK / "會議記錄.md").write_text("---\ntags: [會議]\n---\n週一會議\n", encoding="utf-8")
        (self.v / INBOX / "3bbbb1.md").write_text("沒有 frontmatter 的 uid 名筆記\n", encoding="utf-8")
        gone = item("fld_x_sub_ffffffffffff", WORK + "/fld_x_sub_ffffffffffff", WORK, 2, "已刪", "sub")
        fid = self.index("folder-id-index.json")["items"] + [gone]
        U.write_indexes(self.v, fid, None, None, "t1")


class TestLicense(Base):
    def test_V1_no_license_refuses_and_writes_nothing(self):
        before = self.snapshot()
        home = self.tmp / "home"
        home.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("EZ2_", "AIB_", "TMUX")) and k != "PYTHONPATH"}
        env.update({"HOME": str(home), "EZ2_AIB_HOME": str(home / ".ez2-aib"), "EZ2_AIB_STATE_ROOT": str(home / ".ez2-aib"),
                    "EZ2_VAULT_DIR": str(self.v), "EZ2_AIB_LOCALE": "en", "LANG": "en_US.UTF-8",
                    "EZ2_COCKPIT_STATE_DIR": str(self.tmp / "cockpit")})
        for args in (["newpost", "--title", "x", "--json"], ["doctor", "--fix", "--backup-dir", str(self.bk), "--json"]):
            r = run_guarded([sys.executable, str(ROOT / "tools" / "vault" / "vault_cli.py"), *args], env=env, timeout=60,
                            max_procs=20, stdin=__import__("subprocess").DEVNULL)
            self.assertIsNone(r.tripped)
            self.assertEqual(r.returncode, 3, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual((out["licensed"], out["addon"]), (False, "obsidian-labels"))
        self.assertEqual(before, [x for x in self.snapshot() if not x[0].startswith(("home", "cockpit"))])


@mock.patch.object(VC, "licensed", return_value=(True, "addon"))
class TestNewpost(Base):
    def test_V2_newpost(self, _lic):
        a = VC.newpost(self.v, "第一篇  筆記", now_ms=1792123456000)
        b = VC.newpost(self.v, "", now_ms=1792123456000)                      # 同一毫秒
        self.assertNotEqual(a["uid"], b["uid"])
        self.assertEqual(a["path"], "%s/%s.md" % (INBOX, a["uid"]))
        text = (self.v / a["path"]).read_text(encoding="utf-8")
        for line in ("uid: %s" % a["uid"], 'title: "第一篇 筆記"', "routeKey: core.inbox", "folder_id: %s" % INBOX,
                     "category_uid: cat_core_inbox", "ez2fmt: 2"):
            self.assertIn(line, text)
        self.assertNotIn("orig_name", text)
        self.assertEqual(b["title"], "Untitled" if U.LOC == "en" else b["title"])
        c = VC.newpost(self.v, "交接", folder=ROUTE, body="內容", now_ms=1792123457000)
        self.assertIn("routeKey: core.handoff", (self.v / c["path"]).read_text(encoding="utf-8"))
        self.assertTrue((self.v / c["path"]).read_text(encoding="utf-8").endswith("內容\n"))
        uids = {i["uid"]: i["path"] for i in self.index("uid-index.json")["items"]}
        self.assertEqual(uids[c["uid"]], c["path"])
        self.assertEqual(self.index("labels.json")["notes"][a["uid"]]["title"], "第一篇 筆記")
        self.ok_verify()
        with self.assertRaises(VC.VaultError):
            VC.newpost(self.v, "x", folder="fld_x_sub_ffffffffffff")

    def test_V2b_cli_json(self, _lic):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = VC.main(["newpost", "--vault", str(self.v), "--title", "從按鈕來", "--json"])
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertTrue(Path(out["abs_path"]).is_file())


@mock.patch.object(VC, "licensed", return_value=(True, "addon"))
class TestDoctor(Base):
    def test_V3_report_only(self, _lic):
        self.messy()
        before = self.snapshot()
        res = VC.doctor(self.v)
        self.assertFalse(res["ok"])
        p = res["problems"]
        self.assertEqual(p["dirs_not_folder_id"], [WORK + "/我的新夾"])
        self.assertEqual(sorted(p["md_not_uid_name"]), sorted([WORK + "/會議記錄.md", WORK + "/我的新夾/未命名.md"]))
        self.assertIn(INBOX + "/3bbbb1.md", p["md_no_uid"])                      # 兩篇手建的也缺 uid,一起列
        self.assertEqual(p["index_paths_missing"], [WORK + "/fld_x_sub_ffffffffffff"])
        self.assertEqual(before, self.snapshot())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(VC.main(["doctor", "--vault", str(self.v)]), 1)

    def test_V4_fix(self, _lic):
        self.messy()
        res = VC.doctor(self.v, fix=True, backup_dir=self.bk, now_ms=1792123456000)
        self.assertTrue(res["ok"], res)
        self.ok_verify()
        fid = self.index("folder-id-index.json")["items"]
        new = next(i for i in fid if i["title"] == "我的新夾")
        self.assertRegex(new["folderId"], r"^fld_x_sub_[0-9a-f]{12}$")
        self.assertEqual((new["parentFolderId"], new["folderPath"]), (WORK, WORK + "/" + new["folderId"]))
        self.assertNotIn("fld_x_sub_ffffffffffff", {i["folderId"] for i in fid})
        notes = {f["path"]: f for f in res["fixed"] if f["kind"] == "note"}
        meet = notes[WORK + "/會議記錄.md"]
        self.assertEqual(meet["links_rewritten"], 2)
        board = (self.v / WORK / "3aaaa1.md").read_text(encoding="utf-8")
        self.assertIn("[[%s|會議記錄]]" % meet["uid"], board)
        self.assertIn("[[%s|這份]]" % meet["uid"], board)
        self.assertIn("[[會議]]", board)                                         # 不同名的不動
        mt = (self.v / meet["new_path"]).read_text(encoding="utf-8")
        self.assertIn('title: "會議記錄"', mt)
        self.assertIn('orig_name: "會議記錄.md"', mt)                              # 手建筆記的原名照留
        self.assertIn("週一會議", mt)
        self.assertRegex(mt, r"tags: \[.*會議.*\]")
        un = next(f for f in res["fixed"] if f["kind"] == "note" and f["path"].endswith("未命名.md"))
        self.assertTrue(un["new_path"].startswith(new["folderPath"] + "/"))
        self.assertIn("uid: 3bbbb1", (self.v / INBOX / "3bbbb1.md").read_text(encoding="utf-8"))
        uids = {i["uid"]: i["path"] for i in self.index("uid-index.json")["items"]}
        self.assertEqual(uids[meet["uid"]], meet["new_path"])
        with tarfile.open(res["backup"]) as tar:
            names = tar.getnames()
        self.assertIn(WORK + "/會議記錄.md", names)
        self.assertIn(".ez2/index/folder-id-index.json", names)
        self.assertEqual(VC.doctor(self.v)["ok"], True)

    def test_V5_backup_rules(self, _lic):
        self.messy()
        before = self.snapshot()
        with self.assertRaises(VC.VaultError):
            VC.doctor(self.v, fix=True, backup_dir=None)
        with self.assertRaises(VC.VaultError):
            VC.doctor(self.v, fix=True, backup_dir=self.v / "bk")
        self.assertEqual(before, self.snapshot())

    def test_V6_duplicates_reported_only(self, _lic):
        (self.v / INBOX / "3aaaa1.md").write_text("---\nuid: 3aaaa1\n---\n副本\n", encoding="utf-8")
        res = VC.doctor(self.v, fix=True, backup_dir=self.bk)
        self.assertFalse(res["ok"])
        self.assertTrue(any(x["kind"] == "uid_duplicates" for x in res["left"]))
        self.assertTrue((self.v / INBOX / "3aaaa1.md").is_file())


UPD_LOCAL = "---\nuid: 3cccc1\naliases: [\"3cccc1\", \"舊外掛寫的\"]\ntitle: \"舊外掛寫的\"\ncreated: 2026-10-07 00:08:49\nupdated: 2026-10-07 00:12:16\ntags: [a]\n---\n正文 updated: 2026-01-01 00:00:00 不是欄位\n"
UPD_DATE = "---\nuid: 3cccc2\nupdated: 2026-06-30\n---\n只有日期\n"
UPD_NOTZ = "---\nuid: 3cccc3\nupdated: \"2026-08-10T18:48\"\n---\n沒秒沒時區\n"


@mock.patch.object(VC, "licensed", return_value=(True, "test"))
class TestUpdated(Base):
    def setUp(self):
        super().setUp()
        saved = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Taipei"
        __import__("time").tzset()
        self.addCleanup(lambda: (os.environ.pop("TZ", None) if saved is None else os.environ.__setitem__("TZ", saved),
                                 __import__("time").tzset()))
        for uid, text in (("3cccc1", UPD_LOCAL), ("3cccc2", UPD_DATE), ("3cccc3", UPD_NOTZ)):
            (self.v / INBOX / (uid + ".md")).write_text(text, encoding="utf-8")
        U.write_indexes(self.v, self.index("folder-id-index.json")["items"], None,
                        self.index("uid-index.json")["items"] + [{"uid": u, "path": INBOX + "/%s.md" % u}
                                                                  for u in ("3cccc1", "3cccc2", "3cccc3")], "t2")

    def test_V7_report(self, _lic):
        before = self.snapshot()
        res = VC.doctor(self.v)
        self.assertEqual(res["problems"]["md_updated_local_time"],
                         [{"path": INBOX + "/3cccc1.md", "old": "2026-10-07 00:12:16", "new": "2026-10-06T16:12:16.000Z"}])
        self.assertFalse(res["ok"])
        self.assertEqual(sorted(e["path"] for e in res["notices"]["md_updated_ambiguous"]),
                         [INBOX + "/3cccc2.md", INBOX + "/3cccc3.md"])
        self.assertEqual(res["notices"]["timezone"], "+0800")
        self.assertEqual(before, self.snapshot())

    def test_V8_fix_only_that_line(self, _lic):
        date_only = (self.v / INBOX / "3cccc2.md").read_bytes()
        res = VC.doctor(self.v, fix=True, backup_dir=self.bk)
        self.assertTrue(res["ok"], res)
        self.assertEqual([f for f in res["fixed"] if f["kind"] == "updated"],
                         [{"kind": "updated", "path": INBOX + "/3cccc1.md", "old": "2026-10-07 00:12:16",
                           "new": "2026-10-06T16:12:16.000Z"}])
        self.assertEqual((self.v / INBOX / "3cccc1.md").read_text(encoding="utf-8"),
                         UPD_LOCAL.replace("updated: 2026-10-07 00:12:16\n", "updated: 2026-10-06T16:12:16.000Z\n", 1))
        self.assertEqual((self.v / INBOX / "3cccc2.md").read_bytes(), date_only)
        with tarfile.open(res["backup"]) as tar:
            self.assertIn(INBOX + "/3cccc1.md", tar.getnames())
            self.assertEqual(tar.extractfile(INBOX + "/3cccc1.md").read().decode("utf-8"), UPD_LOCAL)
        self.ok_verify()
        self.assertTrue(VC.doctor(self.v)["ok"])


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(ROOT, "tools/vault/vault_cli.py", needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_X1_ignores_timezone(self):
        self._knife("int(time.mktime(time.strptime(", "int(__import__('calendar').timegm(time.strptime(",
                    "TestUpdated.test_V7_report")

    def test_X3_newpost_writes_orig_name(self):
        self._knife('_fm_set(uid, title, frec["folderId"], by_id, ms, "")', '_fm_set(uid, title, frec["folderId"], by_id, ms, uid + ".md")',
                    "TestNewpost.test_V2_newpost")

    def test_X2_drops_following_fields(self):
        self._knife('"updated: " + entry["new"] + fm[m.end():]', '"updated: " + entry["new"]',
                    "TestUpdated.test_V8_fix_only_that_line")


class TestI18n(unittest.TestCase):
    def test_I1_four_locales(self):
        cjk = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
        zh = json.loads((ROOT / "i18n" / "zh-TW.json").read_text(encoding="utf-8"))
        keys = [k for k in zh if k.startswith("aib.vault_cli.")]
        self.assertGreaterEqual(len(keys), 19)
        for loc in ("en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for k in keys:
                self.assertTrue(d.get(k), (loc, k))
                self.assertEqual(sorted(re.findall(r"\{\w+\}", zh[k])), sorted(re.findall(r"\{\w+\}", d[k])), (loc, k))
                if loc == "en":
                    self.assertIsNone(cjk.search(d[k]), (k, d[k]))


if __name__ == "__main__":
    unittest.main()
