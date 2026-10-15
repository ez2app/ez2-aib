#!/usr/bin/env python3
"""uid 版面(the maintainer 09-26 R7/R8/R9;10-05「先寫轉換,一併完成」)—— 轉換器與 AIB 新路的釘子。

跑法:python3 tests/test-vault-uid-layout-1005.py >log 2>&1; echo $?

釘的格(合入席 10-05 01:37 要求的三格,外加舊版面對照):
  C1 轉換器:中文資料夾+筆記+角色卡的小治具轉一次 ⇒ 資料夾=fld_…、筆記=<uid>.md、labels.json 有標題、
     角色卡夾=卡片 uid 且 角色.md 一 byte 不變、既有 uid 不換號、舊檔名進 aliases。
  C2 冪等:再轉一次 ⇒ 樹的 (路徑, sha256) 集合完全相同。
  C3 增量併入:已轉好的庫旁邊多一個舊名資料夾,裡面一篇同內容筆記 ⇒ 併入、去重、0 錯。
  R1 新路:uid 版面治具上 route_register 解到索引路徵、card_registry 的角色庫位置與卡夾顯示名走索引/labels。
  R2 新路:aib_memory.write dry-run 落到 fld_…/<uid>.md;舊版面對照 <uid>-<標題>.md;刀:判斷拿掉退回舊路。
  R0 對照:同一組程式在舊版面治具上 ⇒ 解到契約 folder、顯示名=目錄名、檔名仍是 <uid>-<標題>.md。
  Z1 刀:把「uid 版面才走索引」的判斷釘死成 False ⇒ R1 必紅(在這裡用 assertNotEqual 證明判斷有牙)。
治具全部在 tmpdir,不碰真 vault / ~/.ez2-aib。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _p in ("memory", "persona_route", "identity"):
    sys.path.insert(0, str(ROOT / "src" / _p))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from aib_tmp_root import aib_tmp_root  # noqa: E402
import aib_cabin_wash as W  # noqa: E402
import route_register as RR  # noqa: E402
import card_registry as CR  # noqa: E402

TOOL = ROOT / "tools" / "vault" / "uid_layout_convert.py"
EZ2_AI_REPO = next((p for p in (Path(os.environ.get("EZ2_AI_REPO") or "/nonexistent"), Path.home() / "ez2-ai", ROOT.parent / "ez2-ai") if p.is_dir()), Path.home() / "ez2-ai")
REAL_CONTRACT = EZ2_AI_REPO / RR.DEFAULT_CONTRACT_REL


def _note_format_source_ready():
    node = shutil.which("node") or ("/opt/homebrew/bin/node" if Path("/opt/homebrew/bin/node").exists() else None)
    if not (node and REAL_CONTRACT.is_file() and EZ2_AI_REPO.is_dir()):
        return False
    for rel in ("shared/ez2-note-format.js", "shared/ez2-route-category-resolver.js",
                "samples/ez2-metadata/category-registry.valid.example.json"):
        if subprocess.run(["git", "-C", str(EZ2_AI_REPO), "cat-file", "-e", "main:%s" % rel],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL).returncode != 0:
            return False
    return True


NOTE_FORMAT_OK = _note_format_source_ready()
CONFIRM = "我已備份並同意轉成 uid 版面"
LIB = "P-Ez2 AI專家/P10-專家模板/角色庫"
C80 = "C-Ez2 Core系統底層/C80-系統記憶交接"
CONTRACT = {"routes": [
    {"routeKey": "core.handoff", "module": "C", "folder": C80, "contentType": "x", "aiReadable": True, "aiWritable": True, "dataLevel": "內部"},
    {"routeKey": "core.inbox", "module": "C", "folder": "C-Ez2 Core系統底層/C10-共用暫存", "contentType": "x", "aiReadable": True, "aiWritable": True, "dataLevel": "內部"},
    {"routeKey": "product.expert.agent_library", "module": "P", "folder": LIB, "contentType": "x", "aiReadable": True, "aiWritable": False, "dataLevel": "內部"},
]}
CARD_UID = "ab12cd"
CARD_MD = '---\nuid: "%s"\ncode: tst\nmodel: auto\nemoji: 🧪\ncategory: 測試\ncreated: 2026-10-05 00:00:00\nupdated: 2026-10-05 00:00:00\n---\n你是測試卡。\n' % CARD_UID


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _tree(vault: Path) -> set:
    out = set()
    for dp, dn, fn in os.walk(vault):
        dn[:] = [d for d in dn if not d.startswith(".")]
        for f in fn:
            p = Path(dp, f)
            out.add((str(p.relative_to(vault)), _sha(p)))
    return out


def _run(*args):
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)


def make_old_vault(root: Path):
    """舊版面治具:中文資料夾、三種筆記(有 uid、沒 uid、沒 frontmatter)、一張角色卡、一個 _ 結構夾。"""
    (root / C80).mkdir(parents=True)
    (root / "C-Ez2 Core系統底層" / "C10-共用暫存").mkdir(parents=True)
    (root / LIB / "測試卡").mkdir(parents=True)
    (root / LIB / "_子AI模版").mkdir(parents=True)
    (root / C80 / "3abc12-已有身分.md").write_text("---\nuid: 3abc12\naliases: [3abc12]\ntitle: \"已有身分\"\nrouteKey: core.handoff\n---\n正文,連到 [[3abc12]]。\n", encoding="utf-8")
    (root / C80 / "沒身分的筆記.md").write_text("---\ntitle: \"沒身分\"\n---\n舊筆記正文。\n", encoding="utf-8")
    (root / "C-Ez2 Core系統底層" / "C10-共用暫存" / "裸文.md").write_text("連 frontmatter 都沒有。\n", encoding="utf-8")
    (root / LIB / "測試卡" / "角色.md").write_text(CARD_MD, encoding="utf-8")
    (root / LIB / "_子AI模版" / "探索型.md").write_text("你是主 AI 的探索型子代理。\n", encoding="utf-8")
    (root / ".ez2" / "routes").mkdir(parents=True)
    (root / ".ez2" / "routes" / "obsidian-routes-v2.json").write_text(json.dumps({"schema_version": "x", "routes": [
        {"folderId": "fld_c_route_79bb4878617e", "parentFolderId": "", "routeKey": "core.handoff", "folderPath": C80, "level": 2, "userEditableName": True}]}, ensure_ascii=False), encoding="utf-8")


class Fixture(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(dir=aib_tmp_root())
        self.addCleanup(self._td.cleanup)
        self.t = Path(self._td.name)
        self.vault = self.t / "vault"; self.vault.mkdir()
        self.contract = self.t / "contract.json"
        self.contract.write_text(json.dumps(CONTRACT, ensure_ascii=False), encoding="utf-8")
        self.backup = self.t / "backup"
        self.plan = self.t / "plan.json"

    def convert(self, plan_path=None):
        plan_path = plan_path or self.plan
        r = _run("plan", "--vault", str(self.vault), "--contract", str(self.contract), "--out", str(plan_path))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = _run("apply", "--plan", str(plan_path), "--backup-dir", str(self.backup), "--confirm-text", CONFIRM)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(plan_path.read_text(encoding="utf-8"))


class Converter(Fixture):
    def test_C1_convert_once(self):
        make_old_vault(self.vault)
        card_sha_before = _sha(self.vault / LIB / "測試卡" / "角色.md")
        plan = self.convert()
        self.assertEqual(plan["stats"]["blockers"], 0)
        # 資料夾全是 folderId;角色庫卡夾 = 卡片 uid;_ 結構夾原名
        rk = json.loads((self.vault / ".ez2" / "index" / "route-key-index.json").read_text(encoding="utf-8"))["items"]
        c80 = next(i["folderPath"] for i in rk if i["routeKey"] == "core.handoff")
        lib = next(i["folderPath"] for i in rk if i["routeKey"] == "product.expert.agent_library")
        self.assertTrue(all(part.startswith("fld_") for part in Path(c80).parts), c80)
        self.assertTrue((self.vault / c80).is_dir())
        self.assertTrue((self.vault / lib / CARD_UID / "角色.md").is_file(), lib)
        self.assertEqual(_sha(self.vault / lib / CARD_UID / "角色.md"), card_sha_before, "角色.md 不得改一 byte")
        self.assertTrue((self.vault / lib / "_子AI模版" / "探索型.md").is_file())
        # 筆記 = <uid>.md;既有 uid 不換號;舊檔名進 aliases;沒 frontmatter 的補上
        notes = sorted(p.name for p in (self.vault / c80).glob("*.md"))
        self.assertIn("3abc12.md", notes)
        self.assertEqual(len(notes), 2)
        kept = (self.vault / c80 / "3abc12.md").read_text(encoding="utf-8")
        self.assertIn("uid: 3abc12", kept); self.assertIn("3abc12-已有身分", kept); self.assertIn("legacy_paths:", kept)
        other = next(n for n in notes if n != "3abc12.md")
        self.assertRegex(other, r"^[0-9a-z]{6}\.md$")
        inbox = next(i["folderPath"] for i in rk if i["routeKey"] == "core.inbox")
        bare = list((self.vault / inbox).glob("*.md"))
        self.assertEqual(len(bare), 1)
        text = bare[0].read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nuid: "), text[:60]); self.assertIn("連 frontmatter 都沒有", text)
        self.assertIn("routeKey: core.inbox", text)
        # labels.json 有標題(資料夾與筆記)
        labels = json.loads((self.vault / ".ez2" / "display" / "labels.json").read_text(encoding="utf-8"))
        self.assertEqual(labels["folders"][Path(c80).name]["title"], "C80-系統記憶交接")
        self.assertEqual(labels["folders"][CARD_UID]["title"], "測試卡")
        self.assertEqual(labels["notes"]["3abc12"]["title"], "已有身分")
        self.assertEqual(labels["notes"]["3abc12"]["i18n"]["zh-TW"], "已有身分")
        # routes v2(外掛第一順位讀的第三複本)folderPath 必須跟著改,舊中文路徑不得殘留
        v2 = json.loads((self.vault / ".ez2" / "routes" / "obsidian-routes-v2.json").read_text(encoding="utf-8"))
        row = next(r for r in v2["routes"] if r["routeKey"] == "core.handoff")
        self.assertEqual(row["folderPath"], c80); self.assertTrue(v2.get("uidLayout"))
        # reindex 只重寫索引,樹不動
        tree_before = _tree(self.vault)
        r = _run("reindex", "--vault", str(self.vault)); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_tree(self.vault), tree_before)
        # verify ok;狀態檔標 uid_layout;備份 tar 在庫外
        r = _run("verify", "--vault", str(self.vault))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(json.loads((self.vault / ".ez2" / "migration-state.json").read_text(encoding="utf-8"))["mode"], "uid_layout")
        self.assertTrue(list(self.backup.glob("vault-before-uid-layout-*.tgz")))

    def test_C2_idempotent(self):
        make_old_vault(self.vault)
        self.convert()
        before = _tree(self.vault)
        plan2 = self.convert(self.t / "plan2.json")
        self.assertEqual(plan2["stats"]["notes_minted"], 0)
        self.assertEqual([f for f in plan2["folders"] if f["old_rel"] != f["new_rel"]], [])
        self.assertEqual([n for n in plan2["notes"] if n["old_rel"] != n["new_rel"]], [])
        self.assertEqual(_tree(self.vault), before, "第二次轉換一個檔都不得變")

    def test_C3_merge_into_converted(self):
        make_old_vault(self.vault)
        self.convert()
        rk = json.loads((self.vault / ".ez2" / "index" / "route-key-index.json").read_text(encoding="utf-8"))["items"]
        c80 = next(i["folderPath"] for i in rk if i["routeKey"] == "core.handoff")
        # 另一台 rsync 過來的舊名資料夾:一篇與已轉好的 3abc12.md 內容完全相同、一篇新的
        (self.vault / C80).mkdir(parents=True)
        shutil.copy2(self.vault / c80 / "3abc12.md", self.vault / C80 / "3abc12.md")
        (self.vault / C80 / "3abc13-另一台寫的.md").write_text("---\nuid: 3abc13\ntitle: \"另一台寫的\"\n---\n新內容。\n", encoding="utf-8")
        n_before = len(list((self.vault / c80).glob("*.md")))
        plan3 = self.convert(self.t / "plan3.json")
        self.assertEqual(plan3["stats"]["blockers"], 0)
        self.assertFalse((self.vault / C80).exists(), "舊名夾併入後應消失")
        self.assertEqual(len(list((self.vault / c80).glob("*.md"))), n_before + 1)
        self.assertTrue((self.vault / c80 / "3abc13.md").is_file())
        receipts = sorted((self.vault / ".ez2" / "migration").glob("uid-layout-receipt-*.json"))
        rec = json.loads(receipts[-1].read_text(encoding="utf-8"))
        self.assertEqual(rec["errors"], [])
        self.assertEqual(rec.get("files_deduped", 0), 1)
        self.assertEqual(_run("verify", "--vault", str(self.vault)).returncode, 0)

    def test_C4_bad_confirm_or_blocker_writes_nothing(self):
        make_old_vault(self.vault)
        r = _run("plan", "--vault", str(self.vault), "--contract", str(self.contract), "--out", str(self.plan))
        self.assertEqual(r.returncode, 0)
        before = _tree(self.vault)
        r = _run("apply", "--plan", str(self.plan), "--backup-dir", str(self.backup), "--confirm-text", "好")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(_tree(self.vault), before)
        self.assertFalse((self.vault / ".ez2" / "migration-state.json").exists())


class Resolvers(Fixture):
    """R1 新路 / R0 對照 / Z1 刀。card_registry 直接呼叫純函數;aib_memory.write 要 node + ez2-ai 來源,沒有就跳過那一格。"""

    def setUp(self):
        super().setUp()
        self.home = self.t / "aib"; (self.home / "identity").mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"EZ2_VAULT_DIR": str(self.vault), "EZ2_AIB_LOCALE": "zh-TW"})
        env.start(); self.addCleanup(env.stop)
        W.wash_cabin_env()
        os.environ["EZ2_AIB_HOME"] = str(self.home)
        os.environ["EZ2_ROUTE_REGISTRY"] = str(self.contract)
        os.environ["EZ2_VAULT_DIR"] = str(self.vault)
        CR._LABELS_CACHE.clear()

    def _scan(self):
        contract = json.loads(self.contract.read_text(encoding="utf-8"))
        lib = CR.library_folder_of(contract, self.vault)
        return lib, CR.scan(self.vault, lib, contract)

    def test_R0_old_layout_control(self):
        make_old_vault(self.vault)
        self.assertFalse(RR.vault_is_uid_layout(str(self.vault)))
        self.assertEqual(RR.resolve_route_folder("core.handoff", contract=CONTRACT), C80)
        lib, res = self._scan()
        self.assertEqual(lib, LIB)
        self.assertEqual(res["cards"][CARD_UID]["display_name"], "測試卡")
        self.assertEqual(res["cards"][CARD_UID]["card_path_rel"], "%s/測試卡/角色.md" % LIB)

    def test_R1_uid_layout_new_path(self):
        make_old_vault(self.vault)
        self.convert()
        rk = json.loads((self.vault / ".ez2" / "index" / "route-key-index.json").read_text(encoding="utf-8"))["items"]
        c80 = next(i["folderPath"] for i in rk if i["routeKey"] == "core.handoff")
        libp = next(i["folderPath"] for i in rk if i["routeKey"] == "product.expert.agent_library")
        self.assertTrue(RR.vault_is_uid_layout(str(self.vault)))
        self.assertEqual(RR.resolve_route_folder("core.handoff", contract=CONTRACT), c80)
        self.assertNotEqual(c80, C80)
        self.assertIsNone(RR.resolve_route_folder("no.such.route", contract=CONTRACT))
        lib, res = self._scan()
        self.assertEqual(lib, libp)
        card = res["cards"][CARD_UID]
        self.assertEqual(card["display_name"], "測試卡", "卡夾名是 uid 時顯示名要從 labels.json 來")
        self.assertEqual(card["card_ref"], "P10/測試卡")
        self.assertEqual(card["card_path_rel"], "%s/%s/角色.md" % (libp, CARD_UID))

    @unittest.skipUnless(NOTE_FORMAT_OK, "needs node + route contract + note-format Git sources in ~/ez2-ai(與 test-aib-memory-0930 同判準)")
    def test_R2_memory_write_lands_in_uid_folder(self):
        """aib memory write 在 uid 版面落到索引指的資料夾、檔名 <uid>.md;舊版面對照:檔名 <uid>-<標題>.md、資料夾=契約字面;刀:判斷拿掉就退回舊路。"""
        import aib_memory as M
        os.environ["EZ2_ROUTE_REGISTRY"] = str(REAL_CONTRACT)      # 真契約(note-format 要看得到完整路由記錄)
        os.environ["EZ2_AI_REPO"] = str(EZ2_AI_REPO)
        node_dir = str(Path(shutil.which("node") or "/opt/homebrew/bin/node").parent)
        os.environ["PATH"] = node_dir + os.pathsep + os.environ.get("PATH", "")
        make_old_vault(self.vault)
        rec0 = M.write("progress", "舊版面落點", "正文。", tags=["aib/test"], seat="", dry_run=True)
        self.assertEqual(Path(rec0["path"]).parent, self.vault / C80)
        self.assertTrue(Path(rec0["path"]).name.startswith(rec0["uid"] + "-"), rec0["path"])
        self.convert()
        rk = json.loads((self.vault / ".ez2" / "index" / "route-key-index.json").read_text(encoding="utf-8"))["items"]
        c80 = next(i["folderPath"] for i in rk if i["routeKey"] == "core.handoff")
        rec = M.write("progress", "uid 版面落點", "正文。", tags=["aib/test"], seat="", dry_run=True)
        self.assertEqual(Path(rec["path"]).parent, self.vault / c80)
        self.assertEqual(Path(rec["path"]).name, rec["uid"] + ".md", rec["path"])
        with mock.patch.object(RR, "vault_is_uid_layout", return_value=False):   # 刀:判斷拿掉 ⇒ 退回舊路
            rec2 = M.write("progress", "刀", "正文。", tags=["aib/test"], seat="", dry_run=True)
        self.assertNotEqual(Path(rec2["path"]).parent, self.vault / c80)
        self.assertTrue(Path(rec2["path"]).name.startswith(rec2["uid"] + "-"))

    def test_Z1_knife_gate_removed_reds_R1(self):
        make_old_vault(self.vault)
        self.convert()
        rk = json.loads((self.vault / ".ez2" / "index" / "route-key-index.json").read_text(encoding="utf-8"))["items"]
        c80 = next(i["folderPath"] for i in rk if i["routeKey"] == "core.handoff")
        with mock.patch.object(RR, "vault_is_uid_layout", return_value=False):
            got = RR.resolve_route_folder("core.handoff", contract=CONTRACT)
            self.assertEqual(got, C80)
            self.assertNotEqual(got, c80, "判斷被拿掉就退回契約字面 —— R1 的 assertEqual 會紅,證明判斷有牙")
            CR._LABELS_CACHE.clear()
            contract = json.loads(self.contract.read_text(encoding="utf-8"))
            self.assertEqual(CR.library_folder_of(contract, self.vault), LIB, "刀下角色庫退回契約路徑(uid 版面上不存在)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
