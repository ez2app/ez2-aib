#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib page unbind --local-only + machine.json 保護(@74e8b 10-21 需求,核心 0.1.39)。EZ2_AIB_STATE_ROOT 指暫存。

L1 --local-only:不叫伺服器、不跳 Touch ID;本機刪掉、server=skipped、帶 manage_url + hint
L2 CLI unbind --local-only --json 不建 PageApi(不碰網路)
M1 綁過(憑證帶 machine_id)但 machine.json 不見 ⇒ machine_identity 拒絕、detail page_machine_identity_missing、不生新檔
M2 舊安裝(憑證沒帶 machine_id)/ 全新機器 ⇒ 照舊建 machine.json
M3 machine.json 在 ⇒ 照舊回同一份身分
四語:help.unbind_local_only、unbind_note.local_only
刀(複本):K1 拿掉 machine.json 保護 ⇒ M1 紅;K2 local_only 也叫伺服器 ⇒ L1 紅
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (REPO / "src" / "cockpit" / "page", REPO / "src" / "cockpit", HERE):
    sys.path.insert(0, str(_p))
import store  # noqa: E402
import unbind as UB  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

REC = {"assistant_token": "ast_test_xyz", "tenant_id": "tenant_ab12", "page_id": "page_cd34", "slug": "andy",
       "machine_id": "6f1c1b0e-3c1a-4c55-9a43-0d7c2a8e1f00"}


class Api:
    def __init__(self):
        self.calls = 0

    def bind_revoke(self):
        self.calls += 1
        return {"revoked": True}


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-unbind-local-")
        self.addCleanup(td.cleanup)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "EZ2_AIB_LOCALE": "zh-TW"}

    def machine_path(self):
        return Path(store.token_dir(self.env)[0]) / "machine.json"


class LocalOnly(Base):
    def test_l1_local_only(self):
        store.save_token(dict(REC), self.env)
        api, asked = Api(), []
        res, rc = UB.unbind(dict(REC), api, self.env, prove=lambda r, e: asked.append(r) or ("touchid", None),
                            local_only=True)
        self.assertEqual((rc, res["state"], res["server"], res["local"]), (0, "unbound", "skipped", "removed"))
        self.assertEqual((api.calls, asked), (0, []), "不叫伺服器、不跳 Touch ID")
        self.assertEqual(res["manage_url"], UB.MANAGE_URL)
        self.assertIn("ez2.page/manage/aib", res["hint"])
        self.assertEqual(store.list_tokens(self.env), [])

    def test_l2_cli_no_network(self):
        store.save_token(dict(REC), self.env)
        import cli
        cli._api_for = lambda *a, **k: self.fail("--local-only 不准建 PageApi")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["unbind", "--local-only", "--json"], environ=self.env)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue())["server"], "skipped")
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            for k in ("aib.page.help.unbind_local_only", "aib.page.unbind_note.local_only"):
                self.assertIn(k, d, (lang, k))


class Machine(Base):
    def test_m1_missing_refused(self):
        store.save_token(dict(REC), self.env)
        with self.assertRaises(store.MachineIdentityMissing) as cm:
            store.machine_identity(self.env)
        self.assertEqual(cm.exception.code, "page_machine_identity_missing")
        self.assertIsInstance(cm.exception, store.StoreError)
        self.assertFalse(self.machine_path().exists(), "不自動生新身分")

    def test_m2_old_or_fresh_creates(self):
        old = dict(REC)
        old.pop("machine_id")
        store.save_token(old, self.env)
        self.assertTrue(store.machine_identity(self.env)["id"])
        self.assertTrue(self.machine_path().exists())
        td = tempfile.TemporaryDirectory(prefix="aib-unbind-fresh-")
        self.addCleanup(td.cleanup)
        env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name}
        self.assertTrue(store.machine_identity(env)["id"])

    def test_m3_existing_kept(self):
        first = store.machine_identity(self.env)
        store.save_token(dict(REC, machine_id=first["id"]), self.env)
        self.assertEqual(store.machine_identity(self.env), first)


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, repl, case):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_no_guard(self):
        self._knife("src/cockpit/page/store.py", 'if any(r.get("machine_id") for r in list_tokens(environ)):',
                    "if False:", "Machine.test_m1_missing_refused")

    def test_k2_local_calls_server(self):
        self._knife("src/cockpit/page/unbind.py", "    if local_only:\n        removed",
                    "    if local_only:\n        api.bind_revoke()\n        removed", "LocalOnly.test_l1_local_only")


if __name__ == "__main__":
    unittest.main()
