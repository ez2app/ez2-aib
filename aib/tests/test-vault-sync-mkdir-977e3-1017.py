#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib vault doctor 的同步檢查、aib vault mkdir、aib memory write 登記索引(0.1.18;10-17 維護者「全處理」)。全在 tmp 沙盒。

10-17 真庫實量:aliases 沒有 title 1309 篇(Obsidian 快速切換只比對檔名與別名 ⇒ 用標題找不到);uid 筆記沒登記在
uid-index 210 篇(全是 aib memory write 寫的)。obsidian-labels 0.2.4 起標題以 frontmatter 為準,labels.json 要跟上。

S1 只列不改:沒登記 / 被搬走(索引路徑與 folder_id 過期)/ labels 標題跟 frontmatter 不同 / aliases 沒有 title 四種都列出;
   整個暫存目錄位元組不變
S2 --fix(要 --backup-dir):四種都修、筆記為準;正文一個位元組不動;區塊寫法的 aliases 換成單行且原有的都在;
   routeKey 不跟位置改;先打包;verify 綠、再跑 ok
S3 aib vault mkdir:建 uid 資料夾、登記、同名回原本那個;父夾不存在 ⇒ rc 2;沒授權 rc 3
S4 aib memory write(uid 版面)寫完就登記 uid-index 與 labels;doctor 不再報 md_not_indexed
刀(複本,走 run_guarded):X1 不補別名 ⇒ S2 紅;X2 memory write 不登記 ⇒ S4 紅;X3 搬動不改 folder_id ⇒ S2 紅
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tarfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "vault"))
sys.path.insert(0, str(ROOT / "src" / "memory"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, str(HERE / file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


VD = _load("vd_base", "test-vault-newpost-doctor-977e3-1016.py")
FRK = _load("frk_base", "test-folder-route-key-977e3-1016.py")
U, VC = VD.U, VD.VC
INBOX, WORK = VD.INBOX, VD.WORK
NOTE = "---\nuid: {uid}\naliases: {aliases}\ntitle: \"{title}\"\nrouteKey: core.inbox\nfolder_id: {fid}\n---\n{body}\n"


@mock.patch.object(VC, "licensed", return_value=(True, "addon"))
class TestSync(VD.Base):
    def setUp(self):
        super().setUp()
        U.LOC = "en"
        w = lambda folder, uid, aliases, title, fid, body: (self.v / folder / (uid + ".md")).write_text(
            NOTE.format(uid=uid, aliases=aliases, title=title, fid=fid, body=body), encoding="utf-8")
        w(INBOX, "3dddd1", '["3dddd1", "沒登記"]', "沒登記", INBOX, "正文一")
        w(WORK, "3dddd2", '["3dddd2", "搬過"]', "搬過", INBOX, "正文二")                       # 從收件匣搬到 WORK
        w(INBOX, "3dddd3", '["3dddd3", "舊標題"]', "新標題", INBOX, "正文三")                    # 屬性改了標題
        (self.v / INBOX / "3dddd4.md").write_text(
            "---\nuid: 3dddd4\naliases:\n  - 3dddd4\n  - 舊檔名-2026-07-19\ntitle: \"區塊別名\"\nfolder_id: %s\n---\n正文四\n" % INBOX,
            encoding="utf-8")
        lab = {"3aaaa1": {"title": "看板"}, "3dddd2": {"title": "搬過", "path": INBOX + "/3dddd2.md"},
               "3dddd3": {"title": "舊標題", "i18n": {"zh-TW": "舊標題", "en": None}, "path": INBOX + "/3dddd3.md"},
               "3dddd4": {"title": "區塊別名", "path": INBOX + "/3dddd4.md"}}
        uidx = [{"uid": "3aaaa1", "path": WORK + "/3aaaa1.md"}, {"uid": "3dddd2", "path": INBOX + "/3dddd2.md"},
                {"uid": "3dddd3", "path": INBOX + "/3dddd3.md"}, {"uid": "3dddd4", "path": INBOX + "/3dddd4.md"}]
        U.write_indexes(self.v, self.index("folder-id-index.json")["items"], lab, uidx, "t1")

    def test_S1_report_only(self, _lic):
        before = self.snapshot()
        p = VC.doctor(self.v)["problems"]
        self.assertEqual([e["uid"] for e in p["md_not_indexed"]], ["3dddd1"])
        st = {e["uid"]: e for e in p["md_index_stale"]}
        self.assertEqual(set(st), {"3dddd2"})
        self.assertEqual((st["3dddd2"]["path"], st["3dddd2"]["folder_id"], st["3dddd2"]["old_folder_id"]),
                         (WORK + "/3dddd2.md", WORK, INBOX))
        self.assertEqual([(e["uid"], e["title"], e["label"]) for e in p["md_title_out_of_sync"]], [("3dddd3", "新標題", "舊標題")])
        self.assertEqual(sorted(e["uid"] for e in p["md_alias_missing_title"]), ["3aaaa1", "3dddd3", "3dddd4"])
        self.assertEqual(before, self.snapshot())

    def test_S2_fix(self, _lic):
        with self.assertRaises(VC.VaultError):
            VC.doctor(self.v, fix=True, backup_dir=None)
        bodies = {u: (self.v / p).read_text(encoding="utf-8").split("\n---\n", 1)[1]
                  for u, p in (("3dddd2", WORK + "/3dddd2.md"), ("3dddd3", INBOX + "/3dddd3.md"), ("3dddd4", INBOX + "/3dddd4.md"))}
        res = VC.doctor(self.v, fix=True, backup_dir=self.bk)
        self.assertTrue(res["ok"], res.get("problems_after"))
        uidx = {i["uid"]: i for i in self.index("uid-index.json")["items"]}
        lab = self.index("labels.json")["notes"]
        self.assertEqual((uidx["3dddd1"]["path"], lab["3dddd1"]["title"]), (INBOX + "/3dddd1.md", "沒登記"))
        self.assertEqual((uidx["3dddd2"]["path"], uidx["3dddd2"]["folderId"], lab["3dddd2"]["path"]),
                         (WORK + "/3dddd2.md", WORK, WORK + "/3dddd2.md"))
        t2 = (self.v / WORK / "3dddd2.md").read_text(encoding="utf-8")
        self.assertIn("folder_id: %s\n" % WORK, t2)
        self.assertIn("routeKey: core.inbox\n", t2, "routeKey is write intent; not moved with the file")
        self.assertEqual((lab["3dddd3"]["title"], lab["3dddd3"]["i18n"]["zh-TW"], uidx["3dddd3"]["title"]), ("新標題",) * 3)
        a3 = U.fm_list(U.split_frontmatter((self.v / INBOX / "3dddd3.md").read_text(encoding="utf-8"))[0], "aliases")
        self.assertEqual(a3, ["3dddd3", "舊標題", "新標題"])
        fm4 = U.split_frontmatter((self.v / INBOX / "3dddd4.md").read_text(encoding="utf-8"))[0]
        self.assertIn('aliases: ["3dddd4", "舊檔名-2026-07-19", "區塊別名"]', fm4)
        self.assertNotIn("  - ", fm4)
        for u, p in (("3dddd2", WORK + "/3dddd2.md"), ("3dddd3", INBOX + "/3dddd3.md"), ("3dddd4", INBOX + "/3dddd4.md")):
            self.assertEqual((self.v / p).read_text(encoding="utf-8").split("\n---\n", 1)[1], bodies[u], "body untouched")
        with tarfile.open(res["backup"]) as tar:
            names = tar.getnames()
        self.assertTrue({WORK + "/3dddd2.md", INBOX + "/3dddd3.md", INBOX + "/3dddd4.md", ".ez2/index/uid-index.json"} <= set(names))
        self.ok_verify()
        self.assertTrue(VC.doctor(self.v)["ok"])

    def test_S3_mkdir(self, _lic):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(VC.main(["mkdir", "--vault", str(self.v), "--parent", WORK, "--title", "週報", "--json"]), 0)
        out = json.loads(buf.getvalue())
        self.assertTrue((self.v / out["folderPath"]).is_dir())
        self.assertRegex(out["folderId"], r"^fld_x_sub_[0-9a-f]{12}$")
        self.assertEqual(VC.mkdir(self.v, "週報", WORK)["folderId"], out["folderId"])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(VC.main(["mkdir", "--vault", str(self.v), "--parent", "fld_x_sub_ffffffffffff", "--title", "x"]), 2)
        self.ok_verify()

    def test_S3b_mkdir_needs_license(self, _lic):
        with mock.patch.object(VC, "licensed", return_value=(False, "no-license")), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(VC.main(["mkdir", "--vault", str(self.v), "--title", "x"]), 3)


@unittest.skipUnless(shutil.which("node"), "note-format runner needs node")
class TestMemoryRegisters(FRK.Base):
    def test_S4_memory_write_registers(self):
        os.environ.pop("EZ2_AI_REPO", None)
        import aib_memory as AM
        out = AM.write("note", "記憶 登記測試", "內文", route_key="product.expert.draft")
        rel = os.path.relpath(out["path"], str(self.v))
        uidx = {i["uid"]: i for i in json.loads((self.v / ".ez2/index/uid-index.json").read_text("utf-8"))["items"]}
        lab = json.loads((self.v / ".ez2/display/labels.json").read_text("utf-8"))["notes"]
        self.assertEqual((uidx[out["uid"]]["path"], uidx[out["uid"]]["routeKey"]), (rel, "product.expert.draft"))
        self.assertEqual((lab[out["uid"]]["title"], lab[out["uid"]]["path"]), (out["title"], rel))
        with mock.patch.object(VC, "licensed", return_value=(True, "addon")):
            self.assertEqual(VC._sync_scan(self.v)["md_not_indexed"], [])


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        tests = (name, "test-vault-newpost-doctor-977e3-1016.py", "test-folder-route-key-977e3-1016.py")
        with knife_copy(ROOT, rel, needle, replacement, tests=tests) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_X1_no_alias(self):
        self._knife("tools/vault/vault_cli.py", '    for e in sync["md_alias_missing_title"]:\n', '    for e in []:\n',
                    "TestSync.test_S2_fix")

    def test_X2_memory_no_register(self):
        self._knife("src/memory/aib_memory.py", "    if uid_layout:\n        _register_uid_note(", "    if False:\n        _register_uid_note(",
                    "TestMemoryRegisters.test_S4_memory_write_registers")

    def test_X3_move_keeps_folder_id(self):
        self._knife("tools/vault/vault_cli.py", 'if e["folder_id"] and e["old_folder_id"] and e["old_folder_id"] != e["folder_id"]:',
                    'if False:', "TestSync.test_S2_fix")


if __name__ == "__main__":
    unittest.main(verbosity=2)
