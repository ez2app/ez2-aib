#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""Ez2 截圖在 uid 庫存到第二大腦(10-18:uid 庫沒有舊路徑 A.Ez2/00.Ez2_圖庫/截圖 那一筆 ⇒ 一律退到桌面)。
全部在暫存目錄造假庫,不碰真的 Vault。

D1 舊庫:folder-id-index 有 legacyPaths = A.Ez2/00.Ez2_圖庫/截圖 ⇒ 照舊用它(source legacy)
D2 uid 庫:routeKey storage.gallery 的圖庫底下已有「截圖」⇒ 用它(source gallery)
D3 uid 庫沒有「截圖」子夾:create=True ⇒ 用 folder_add 建(硬碟 + folder-id-index 都有、父夾 = 圖庫、legacyPaths = 舊路徑);再叫一次同一個夾、不重複建
D4 create=False(status / 開艙記設定)⇒ 不建,先指圖庫本身(gallery_no_sub),索引不動
D5 找不到 ⇒ 桌面 + why(沒有圖庫 no_gallery、沒有 vault 環境變數 no_vault_env);EZ2_AIB_SHOT_DIR 優先
D6 shoot_dir:環境沒有 vault 變數 ⇒ 用 agent.json 記的 vault;舊版 agent.json(沒記 vault)照舊用它記的 shot_dir
刀:K1 拿掉 routeKey 查找 ⇒ D2、D3 紅
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # = aib/
COCKPIT = ROOT / "src" / "cockpit"
HELPER = COCKPIT / "shot_helper.py"
_N = [0]


def _load(text=None):
    if str(COCKPIT) not in sys.path:
        sys.path.insert(0, str(COCKPIT))
    _N[0] += 1
    name = "shot_helper_dir_%d" % _N[0]
    if text is None:
        spec = importlib.util.spec_from_file_location(name, HELPER)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    mod = types.ModuleType(name)
    mod.__file__ = str(HELPER)
    sys.modules[name] = mod
    exec(compile(text, str(HELPER), "exec"), mod.__dict__)
    return mod


GROUP, GALLERY = "fld_a_group_1f3e1fee99e5", "fld_a_route_e1a974868813"


def make_uid_vault(root, with_sub=False):
    v = Path(root) / "vault"
    (v / GROUP / GALLERY).mkdir(parents=True)
    items = [
        {"folderId": GROUP, "routeKey": "folder.group.a.1f3e1fee", "folderPath": GROUP, "parentFolderId": "", "level": 1,
         "title": "A.Ez2", "legacyPaths": ["A.Ez2"], "status": "active", "kind": "group"},
        {"folderId": GALLERY, "routeKey": "storage.gallery", "folderPath": GROUP + "/" + GALLERY, "parentFolderId": GROUP,
         "level": 2, "title": "00.Ez2_圖庫", "legacyPaths": ["A.Ez2/00.Ez2_圖庫"], "status": "active", "kind": "route"},
    ]
    if with_sub:
        sub = "fld_a_sub_aaaaaaaaaaaa"
        (v / GROUP / GALLERY / sub).mkdir()
        items.append({"folderId": sub, "routeKey": "", "folderPath": GROUP + "/" + GALLERY + "/" + sub, "parentFolderId": GALLERY,
                      "level": 3, "title": "截圖", "legacyPaths": ["X/截圖"], "status": "active", "kind": "sub"})
    (v / ".ez2" / "index").mkdir(parents=True)
    (v / ".ez2" / "index" / "folder-id-index.json").write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    return v


def idx_items(v):
    return json.loads((v / ".ez2" / "index" / "folder-id-index.json").read_text(encoding="utf-8"))["items"]


def check_d2_d3(t, S, tmp):
    v = make_uid_vault(tmp / "d2", with_sub=True)
    info = S.shot_dir_info({"EZ2_VAULT_DIR": str(v)}, home=str(tmp))
    t.assertEqual((info["source"], info["dir"]), ("gallery", str(v / GROUP / GALLERY / "fld_a_sub_aaaaaaaaaaaa")))
    v = make_uid_vault(tmp / "d3")
    info = S.shot_dir_info({"EZ2_VAULT_DIR": str(v)}, home=str(tmp))
    t.assertEqual(info["source"], "gallery_created", info)
    t.assertTrue(os.path.isdir(info["dir"]))
    sub = [i for i in idx_items(v) if i.get("title") == "截圖"]
    t.assertEqual(len(sub), 1)
    t.assertEqual(sub[0]["parentFolderId"], GALLERY)
    t.assertEqual(str(v / sub[0]["folderPath"]), info["dir"])
    again = S.shot_dir_info({"EZ2_VAULT_DIR": str(v)}, home=str(tmp))
    t.assertEqual(again["dir"], info["dir"])                     # 建好的子夾 legacyPaths 正是舊路徑 ⇒ 之後走 legacy 也是同一個夾
    t.assertEqual(sub[0]["legacyPaths"], ["A.Ez2/00.Ez2_圖庫/截圖"])
    t.assertEqual(len([i for i in idx_items(v) if i.get("title") == "截圖"]), 1)


class ShotDirUid(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shot-dir-"))
        self.S = _load()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_d1_old_vault(self):
        v = self.tmp / "old"
        (v / "fld_x").mkdir(parents=True)
        (v / ".ez2" / "index").mkdir(parents=True)
        (v / ".ez2" / "index" / "folder-id-index.json").write_text(json.dumps({"items": [
            {"folderId": "fld_x", "folderPath": "fld_x", "legacyPaths": ["A.Ez2/00.Ez2_圖庫/截圖"]}]}), encoding="utf-8")
        info = self.S.shot_dir_info({"EZ2_VAULT_DIR": str(v)}, home=str(self.tmp))
        self.assertEqual((info["source"], info["dir"]), ("legacy", str(v / "fld_x")))

    def test_d2_d3_gallery(self):
        check_d2_d3(self, self.S, self.tmp)

    def test_d4_no_create(self):
        v = make_uid_vault(self.tmp / "d4")
        before = idx_items(v)
        info = self.S.shot_dir_info({"EZ2_VAULT_DIR": str(v)}, home=str(self.tmp), create=False)
        self.assertEqual((info["source"], info["dir"]), ("gallery_no_sub", str(v / GROUP / GALLERY)))
        self.assertEqual(idx_items(v), before)
        self.assertEqual(sorted(p.name for p in (v / GROUP / GALLERY).iterdir()), [])

    def test_d5_fallbacks(self):
        v = self.tmp / "nogal"
        (v / ".ez2" / "index").mkdir(parents=True)
        (v / ".ez2" / "index" / "folder-id-index.json").write_text(json.dumps({"items": [{"folderId": "fld_a", "folderPath": "fld_a"}]}))
        info = self.S.shot_dir_info({"EZ2_VAULT_DIR": str(v)}, home=str(self.tmp))
        self.assertEqual((info["source"], info["why"], info["dir"]), ("desktop", "no_gallery", str(self.tmp / "Desktop")))
        info = self.S.shot_dir_info({}, home=str(self.tmp))
        self.assertEqual((info["source"], info["why"]), ("desktop", "no_vault_env"))
        info = self.S.shot_dir_info({"EZ2_AIB_SHOT_DIR": "/x/y", "EZ2_VAULT_DIR": str(v)}, home=str(self.tmp))
        self.assertEqual((info["source"], info["dir"]), ("env", "/x/y"))

    def test_d6_shoot_dir_uses_saved_vault(self):
        v = make_uid_vault(self.tmp / "d6", with_sub=True)
        got = self.S.shoot_dir({"vault": str(v), "shot_dir": "/old/Desktop"}, environ={"HOME": str(self.tmp)})
        self.assertEqual(got, str(v / GROUP / GALLERY / "fld_a_sub_aaaaaaaaaaaa"))
        self.assertEqual(self.S.shoot_dir({"shot_dir": "/kept/dir"}, environ={"HOME": str(self.tmp)}), "/kept/dir")

    def test_k1_no_route_lookup(self):
        text = HELPER.read_text(encoding="utf-8")
        needle = 'it.get("routeKey") == GALLERY_ROUTE'
        self.assertEqual(text.count(needle), 1)
        S2 = _load(text.replace(needle, "False"))
        with self.assertRaises(AssertionError):
            check_d2_d3(self, S2, self.tmp / "knife")


if __name__ == "__main__":
    unittest.main(verbosity=2)
