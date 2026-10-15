#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""專案卡的資料夾:卡上只存 route_key,給人看的名字每次現解(10-15 @977e3)。全在 tmp 沙盒,不碰真 Vault。

F1  aib newproj --folder core.handoff(只建卡):card.json 有 route_key、沒有 folder 欄(契約字面存進卡,Vault 轉 uid 版面後就過期)。
F2  uid 版面:顯示名從 .ez2/display/labels.json 現解,不是卡上舊的契約字面。
F3  舊卡記過 folder、路由解不出名字 ⇒ 退回舊卡記的最後一段。
"""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401 —— 基因/自動綁預設關

ROOT = HERE.parent
PY = sys.executable
BIN_AIB = ROOT.parent / "bin" / "aib"


def _load_new_project():
    spec = importlib.util.spec_from_file_location("new_project_f", str(ROOT / "tools" / "project" / "new_project.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-f-")
        self.home = os.path.join(self.tmp, "home")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.home)
        os.makedirs(self.vault)
        os.makedirs(os.path.join(self.tmp, "proj"))
        self.root = pathlib.Path(self.home, ".ez2-aib")
        self._saved = dict(os.environ)
        for k in ("EZ2_VAULT_ROOT", "EZ2_ROUTE_CONTRACT"):
            os.environ.pop(k, None)
        os.environ.update({"HOME": self.home, "EZ2_VAULT_DIR": self.vault, "EZ2_AIB_STATE_ROOT": str(self.root),
                           "EZ2_AIB_HOME": str(self.root), "EZ2_AIB_LOCALE": "zh-TW"})

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def uid_layout(self, title):
        ez2 = pathlib.Path(self.vault, ".ez2")
        (ez2 / "index").mkdir(parents=True)
        (ez2 / "display").mkdir(parents=True)
        (ez2 / "migration-state.json").write_text(json.dumps({"mode": "uid_layout"}), encoding="utf-8")
        (ez2 / "index" / "route-key-index.json").write_text(json.dumps({"items": [
            {"routeKey": "core.handoff", "folderId": "fld_h1", "folderPath": "fld_h1"}]}), encoding="utf-8")
        (ez2 / "display" / "labels.json").write_text(json.dumps({"folders": {"fld_h1": {"title": title}}},
                                                                ensure_ascii=False), encoding="utf-8")


class F1CardStoresRouteKeyOnly(_Sandbox):
    def test_card_has_no_folder_literal(self):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": self.home, "EZ2_VAULT_DIR": self.vault,
               "EZ2_AIB_LOCALE": "zh-TW", "AIB_GENES": "0", "AIB_AUTO_BIND": "0"}
        r = subprocess.run([PY, str(BIN_AIB), "newproj", "--title", "Demo", "--folder", "core.handoff"],
                           cwd=os.path.join(self.tmp, "proj"), env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        card = json.loads((self.root / "projects" / out["project_uid"] / "card.json").read_text(encoding="utf-8"))
        self.assertEqual(card.get("route_key"), "core.handoff")
        self.assertNotIn("folder", card, "卡上不存契約字面")
        self.assertTrue(out.get("folder"), "輸出還是要給人看資料夾名")


class F2UidLayoutLabel(_Sandbox):
    def test_label_from_vault(self):
        self.uid_layout("系統記憶交接")
        np = _load_new_project()
        self.assertEqual(np._folder_label("core.handoff", "C80-舊名/交接"), "系統記憶交接")


class F3OldCardFallback(_Sandbox):
    def test_stored_folder_when_route_unresolved(self):
        np = _load_new_project()
        self.assertEqual(np._folder_label("no.such.route", "A/B-舊夾"), "B-舊夾")
        self.assertEqual(np._folder_label("no.such.route", ""), "no.such.route")
        self.assertEqual(np._folder_label("", "A/B"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
