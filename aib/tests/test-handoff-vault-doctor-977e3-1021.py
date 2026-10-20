#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib handoff collect 附上 Vault 只讀檢查的數字(10-21 維護者准 @977e3:真庫會慢慢漂移,沒人定期看)。

V1 沒設 EZ2_VAULT_DIR ⇒ skipped no_vault,不跑子行程
V2 不是 uid 庫 ⇒ skipped no_vault,不起子行程
V3 uid 庫但沒有 obsidian-labels 授權(真子行程,隔離 EZ2_AIB_HOME,rc 3)⇒ skipped not_licensed
V4 doctor 回問題(假子行程)⇒ 只留 {類別: 篇數},值都是整數、不帶路徑;rc 0 沒問題 ⇒ ok
V5 子行程逾時 / 壞 JSON / 其他 rc ⇒ error,不崩
V6 render_draft:機器事實多一行 Vault 檢查,三種狀態字樣;facts 沒 vault ⇒ 不出那行
V7 接線:_collect 有呼叫 vault_doctor_brief()(字面),且只讀(不帶 --fix)
刀:K1 回傳整份清單(len(v) → v)⇒ V4 紅;K2 不認 rc 3 ⇒ V3 紅
測試明寫 EZ2_AIB_STATE_ROOT、EZ2_AIB_HOME 與暫存 Vault,不碰真庫、不碰真授權。
"""
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
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src" / "bootstrap"))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
sys.path.insert(0, str(ROOT / "tools" / "vault"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import aib_handoff as H  # noqa: E402
import uid_layout_convert as U  # noqa: E402

WORK = "fld_x_group_08504630b093"


def done(rc, out=""):
    return subprocess.CompletedProcess([], rc, out, "")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aib-hovault-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for d in ("state", "home"):
            (self.tmp / d).mkdir()
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.tmp / "home"),
                    "EZ2_AIB_STATE_ROOT": str(self.tmp / "state"), "EZ2_AIB_HOME": str(self.tmp / "home")}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)


class TestBrief(Base):
    def test_V1_no_vault_env(self):
        with mock.patch.object(H.subprocess, "run") as run:
            self.assertEqual(H.vault_doctor_brief(self.env), {"skipped": "no_vault"})
        run.assert_not_called()

    def test_V2_not_uid_vault(self):
        (self.tmp / "plain").mkdir()
        with mock.patch.object(H.subprocess, "run") as run:
            self.assertEqual(H.vault_doctor_brief(dict(self.env, EZ2_VAULT_DIR=str(self.tmp / "plain"))), {"skipped": "no_vault"})
        run.assert_not_called()

    def uid_vault(self):
        v = self.tmp / "vault"
        (v / WORK).mkdir(parents=True, exist_ok=True)
        U.write_indexes(v, [{"folderId": WORK, "routeKey": "", "folderPath": WORK, "parentFolderId": "", "level": 1,
                             "title": "W", "legacyPaths": [], "status": "active", "kind": "group"}], {}, [], "t0")
        return v

    def test_V3_unlicensed(self):
        v = self.uid_vault()
        self.assertEqual(H.vault_doctor_brief(dict(self.env, EZ2_VAULT_DIR=str(v))), {"skipped": "not_licensed"})

    def test_V4_counts_only(self):
        doc = {"ok": False, "problems": {"md_alias_missing_title": ["a/1.md", "b/2.md"], "md_no_uid": [], "dirs_not_folder_id": ["秘密夾"]}}
        env = dict(self.env, EZ2_VAULT_DIR=str(self.uid_vault()))
        with mock.patch.object(H.subprocess, "run", return_value=done(1, json.dumps(doc))) as run:
            got = H.vault_doctor_brief(env)
        self.assertEqual(got, {"ok": False, "problems": {"md_alias_missing_title": 2, "dirs_not_folder_id": 1}})
        self.assertTrue(all(isinstance(n, int) for n in got["problems"].values()))
        self.assertNotIn("--fix", run.call_args[0][0])
        with mock.patch.object(H.subprocess, "run", return_value=done(0, json.dumps({"ok": True, "problems": {"md_no_uid": []}}))):
            self.assertEqual(H.vault_doctor_brief(env), {"ok": True, "problems": {}})

    def test_V5_errors(self):
        env = dict(self.env, EZ2_VAULT_DIR=str(self.uid_vault()))
        with mock.patch.object(H.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertEqual(H.vault_doctor_brief(env), {"error": "TimeoutExpired"})
        with mock.patch.object(H.subprocess, "run", return_value=done(0, "not json")):
            self.assertEqual(H.vault_doctor_brief(env), {"error": "bad-json"})
        with mock.patch.object(H.subprocess, "run", return_value=done(9)):
            self.assertEqual(H.vault_doctor_brief(env), {"error": "rc=9"})


class TestRender(unittest.TestCase):
    def facts(self, vault=None):
        f = {"seat": {"code": "977e3"}, "iso": "x", "ts": 0, "unmeasured": [], "background": []}
        if vault is not None:
            f["vault"] = vault
        return f

    def test_V6_render(self):
        label = H.T("aib.handoff.fact.vault")
        found = H.render_draft(self.facts({"ok": False, "problems": {"md_alias_missing_title": 456}}))
        self.assertIn("- %s: %s" % (label, H.T("aib.handoff.fact.vault_found", detail="md_alias_missing_title 456")), found)
        self.assertIn(H.T("aib.handoff.fact.vault_ok"), H.render_draft(self.facts({"ok": True, "problems": {}})))
        self.assertIn(H.T("aib.handoff.fact.vault_skip", why="not_licensed"), H.render_draft(self.facts({"skipped": "not_licensed"})))
        self.assertNotIn(label, H.render_draft(self.facts()))

    def test_V7_wired(self):
        src = (ROOT / "src" / "cockpit" / "aib_handoff.py").read_text(encoding="utf-8")
        body = src[src.index("def _collect("):src.index("def _etime_s(")]
        self.assertIn('facts["vault"] = vault_doctor_brief()', body)


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(ROOT, "src/cockpit/aib_handoff.py", needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_K1_paths_leak(self):
        self._knife("probs = {k: len(v) for k, v", "probs = {k: v for k, v", "TestBrief.test_V4_counts_only")

    def test_K2_rc3_not_recognised(self):
        self._knife("    if p.returncode == 3:\n        return {\"skipped\": \"not_licensed\"}\n", "", "TestBrief.test_V3_unlicensed")


if __name__ == "__main__":
    unittest.main()
