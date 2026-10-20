#!/usr/bin/env python3
"""aib vault mobile-license(10-20 維護者選手機方案 A):把這台的授權放一份進 <vault>/.ez2/license.json,
給手機上的 Obsidian 標題外掛自己驗章(手機沒有 aib)。

P1 受信鑰匙簽、沒綁機器的授權 ⇒ 原樣寫進 Vault(簽章照樣驗得過);再跑一次內容沒變 ⇒ changed false
P2 綁機器(devices / activation_url)、沒簽章、草稿簽章、簽章不對、這台沒授權檔 ⇒ 拒、Vault 不留檔
P3 --remove 拿掉;本來就沒有 ⇒ removed false
P4 目的地是符號連結 ⇒ 不跟、拒、連結指的檔不變
P5 CLI:有授權 ⇒ 0 + JSON;沒授權(obsidian-labels 加購)⇒ 3、不寫;綁機器 ⇒ 2
刀(複本,走 run_guarded):Z1 拿掉綁機器檢查 ⇒ P2 紅;Z2 拿掉驗章 ⇒ P2 紅;Z3 不擋符號連結 ⇒ P4 紅
I1 新鍵四語都有、英文值沒有中日韓字、佔位符一致
測試明寫 EZ2_AIB_STATE_ROOT、EZ2_AIB_HOME 與暫存 Vault,不碰真庫、不碰真授權。
"""
import base64
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
sys.path.insert(0, str(ROOT / "src"))
import aib_testlib  # noqa: E402,F401
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import uid_layout_convert as U  # noqa: E402
import vault_cli as VC  # noqa: E402
from entitlement import ed25519, keys  # noqa: E402
from entitlement.signing import canonical, verify_object, OK  # noqa: E402

SEED = bytes(range(32))
TEST_KEY = "ed25519:" + ed25519.public_key(SEED).hex()
BASE = {"tier": "all", "addons": ["obsidian-labels"], "expires_at": "2027-10-17T05:36:46+00:00", "note": "測試機"}
WORK = "fld_x_group_08504630b093"


def signed(obj):
    return dict(obj, signature="ed25519:" + base64.b64encode(ed25519.sign(SEED, canonical(obj))).decode())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aib-mobilelic-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.v = self.tmp / "vault"
        self.home = self.tmp / "aib-home"
        self.home.mkdir()
        (self.tmp / "state").mkdir()
        env = mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": str(self.tmp / "state"), "EZ2_AIB_HOME": str(self.home),
                                           "EZ2_VAULT_DIR": str(self.v)})
        env.start()
        self.addCleanup(env.stop)
        k = mock.patch.object(keys, "TRUSTED_PUBLIC_KEYS", (TEST_KEY,))
        k.start()
        self.addCleanup(k.stop)
        it = {"folderId": WORK, "routeKey": "", "folderPath": WORK, "parentFolderId": "", "level": 1, "title": "06-工作區 Work",
              "legacyPaths": ["06-工作區 Work"], "status": "active", "kind": "group"}
        (self.v / WORK).mkdir(parents=True)
        U.write_indexes(self.v, [it], {}, [], "t0")
        self.dest = self.v / ".ez2" / "license.json"

    def put(self, obj):
        (self.home / "license.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


class TestMobileLicense(Base):
    def test_P1_signed_unbound_copied_as_is(self):
        self.put(signed(BASE))
        res = VC.mobile_license(self.v)
        self.assertEqual(res, {"path": ".ez2/license.json", "changed": True, "expires_at": BASE["expires_at"]})
        got = json.loads(self.dest.read_text(encoding="utf-8"))
        self.assertEqual(got, signed(BASE))
        self.assertEqual(verify_object(got), OK, "the vault copy still verifies")
        self.assertFalse(VC.mobile_license(self.v)["changed"])

    def test_P2_refused_licenses_leave_nothing(self):
        cases = {"devices": signed(dict(BASE, devices=["d1"])),
                 "activation": signed(dict(BASE, activation_url="https://example.invalid/a")),
                 "unsigned": dict(BASE),
                 "draft": dict(BASE, signature="draft"),
                 "tampered": dict(signed(BASE), tier="single")}
        for name, obj in cases.items():
            with self.subTest(name):
                self.put(obj)
                with self.assertRaises(VC.VaultError):
                    VC.mobile_license(self.v)
                self.assertFalse(self.dest.exists(), name)
        (self.home / "license.json").unlink()
        with self.assertRaises(VC.VaultError):
            VC.mobile_license(self.v)
        self.assertFalse(self.dest.exists())

    def test_P3_remove(self):
        self.put(signed(BASE))
        VC.mobile_license(self.v)
        self.assertEqual(VC.mobile_license(self.v, remove=True), {"path": ".ez2/license.json", "removed": True})
        self.assertFalse(self.dest.exists())
        self.assertEqual(VC.mobile_license(self.v, remove=True)["removed"], False)

    def test_P4_symlink_not_followed(self):
        self.put(signed(BASE))
        target = self.tmp / "elsewhere.txt"
        target.write_text("keep", encoding="utf-8")
        self.dest.symlink_to(target)
        with self.assertRaises(VC.VaultError):
            VC.mobile_license(self.v)
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")
        self.assertTrue(self.dest.is_symlink())

    def test_P5_cli(self):
        self.put(signed(BASE))
        out = io.StringIO()
        with mock.patch.object(VC, "licensed", return_value=(True, "addon")), contextlib.redirect_stdout(out):
            rc = VC.main(["mobile-license", "--vault", str(self.v), "--json"])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out.getvalue())["changed"])
        self.dest.unlink()
        with mock.patch.object(VC, "licensed", return_value=(False, "addon-missing:obsidian-labels")), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = VC.main(["mobile-license", "--vault", str(self.v), "--json"])
        self.assertEqual(rc, 3)
        self.assertFalse(self.dest.exists())
        self.put(signed(dict(BASE, devices=["d1"])))
        with mock.patch.object(VC, "licensed", return_value=(True, "addon")), contextlib.redirect_stderr(io.StringIO()):
            rc = VC.main(["mobile-license", "--vault", str(self.v)])
        self.assertEqual(rc, 2)
        self.assertFalse(self.dest.exists())


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(ROOT, "tools/vault/vault_cli.py", needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_Z1_bound_accepted(self):
        self._knife('        if obj.get("devices") is not None or obj.get("activation_url"):\n            raise VaultError(T("err_mobile_bound"))\n',
                    "", "TestMobileLicense.test_P2_refused_licenses_leave_nothing")

    def test_Z2_signature_not_checked(self):
        self._knife(' or verify_object(obj) != OK:', ":", "TestMobileLicense.test_P2_refused_licenses_leave_nothing")

    def test_Z3_symlink_followed(self):
        self._knife('        if dest.is_symlink():\n            raise VaultError(T("err_mobile_symlink", {"path": MOBILE_LICENSE}))\n',
                    "", "TestMobileLicense.test_P4_symlink_not_followed")


class TestI18n(unittest.TestCase):
    def test_I1_four_locales(self):
        cjk = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
        new = ("help_mobile_license", "help_mobile_remove", "err_mobile_no_license", "err_mobile_unsigned", "err_mobile_bound",
               "err_mobile_symlink", "mobile_done", "mobile_removed", "mobile_absent")
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
