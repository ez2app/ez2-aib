#!/usr/bin/env python3
"""重寫索引不再拿掉角色卡資料夾的標題(10-18 @977e3 在真庫實量)。

0.1.18 `aib vault doctor --fix` 把 labels.json 的 folders["3pb70i"](「EU01防護工程師」,aib newrole 寫的)拿掉 ⇒
card_registry 的 card_ref 從「P10/EU01防護工程師」變成「P10/3pb70i」,舊綁定比對不到。原因:write_indexes 只從
folder-id-index 重寫資料夾標題,角色卡資料夾用 uid 命名、不在索引裡 ⇒ 每次重寫(doctor --fix、mkdir、reindex)都被清掉。

L1 write_indexes:不是 fld_ 開頭的資料夾標題(角色卡)留下、內容不變;fld_ 開頭但索引裡已經沒有的照舊拿掉
L2 doctor --fix(有東西要修、會重寫索引)⇒ 角色卡標題還在;card_registry 讀到的 card_ref 仍是標題
L3 aib vault mkdir(folder_add)⇒ 角色卡標題還在
刀(複本,走 run_guarded):K1 拿掉保留那段 ⇒ L1 紅
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "vault"))
sys.path.insert(0, str(ROOT / "src" / "memory"))
import aib_testlib  # noqa: E402,F401
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import uid_layout_convert as U  # noqa: E402
import vault_cli as VC  # noqa: E402

INBOX = "fld_x_group_bfecfae260f8"
LIB = "fld_p_group_5d4b60295e72"
CARD = "3pb70i"
CARD_LABEL = {"title": "EU01防護工程師"}


def item(fid, path, parent, level, title, kind, rk=""):
    return {"folderId": fid, "routeKey": rk, "folderPath": path, "parentFolderId": parent, "level": level,
            "title": title, "legacyPaths": [title], "status": "active", "kind": kind}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aib-keeplab-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.v = self.tmp / "vault"
        self.items = [item(INBOX, INBOX, "", 1, "00-暫存區 Inbox", "group", "folder.group.x.bfecfae2"),
                      item(LIB, LIB, "", 1, "P-Ez2 AI專家", "group")]
        for it in self.items:
            (self.v / it["folderPath"]).mkdir(parents=True, exist_ok=True)
        (self.v / LIB / CARD).mkdir()
        (self.v / LIB / CARD / "角色.md").write_text('---\nuid: "%s"\ncode: t\n---\n你是測試。\n' % CARD, encoding="utf-8")
        U.write_indexes(self.v, self.items, {}, [], "t0")
        lab = self.labels()
        lab["folders"][CARD] = dict(CARD_LABEL)                      # aib newrole 寫的那種
        lab["folders"]["fld_x_group_000000000000"] = {"title": "已退役"}  # 索引裡沒有的 fld_ 資料夾
        (self.v / ".ez2" / "display" / "labels.json").write_text(json.dumps(lab, ensure_ascii=False), encoding="utf-8")

    def labels(self):
        return json.loads((self.v / ".ez2" / "display" / "labels.json").read_text(encoding="utf-8"))


class Keep(Base):
    def test_l1_write_indexes_keeps_card_label(self):
        U.write_indexes(self.v, self.items, None, None, "t1")
        folders = self.labels()["folders"]
        self.assertEqual(folders.get(CARD), CARD_LABEL)
        self.assertNotIn("fld_x_group_000000000000", folders)
        self.assertEqual(folders[INBOX]["title"], "00-暫存區 Inbox")

    def test_l2_doctor_fix_keeps_card_label(self):
        (self.v / INBOX / "會議記錄.md").write_text("手建的筆記\n", encoding="utf-8")   # 給 --fix 一件要修的事
        res = VC.doctor(self.v, fix=True, backup_dir=self.tmp / "backup", now_ms=1792123456000)
        self.assertTrue(res["fixed"], res)
        self.assertEqual(self.labels()["folders"].get(CARD), CARD_LABEL)
        import card_registry as CR
        self.assertEqual(CR._label_title(self.v, CARD), "EU01防護工程師")

    def test_l3_mkdir_keeps_card_label(self):
        U.folder_add(self.v, "", "新資料夾")
        self.assertEqual(self.labels()["folders"].get(CARD), CARD_LABEL)


class Knife(unittest.TestCase):
    def test_k1_drop_keep_loop(self):
        name = Path(__file__).name
        needle = ('    for key, val in (old_labels.get("folders") or {}).items():\n'
                  '        if key not in labels["folders"] and not str(key).startswith("fld_"):\n'
                  '            labels["folders"][key] = val\n')
        with knife_copy(ROOT, "tools/vault/uid_layout_convert.py", needle, "", tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Keep.test_l1_write_indexes_keeps_card_label"],
                            cwd=str(root), max_procs=60, timeout=300)
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
