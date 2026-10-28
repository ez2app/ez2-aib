#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib plugins install <來源> 與 aib license install <檔>(10-20 @abb89 派、會員優化清單 #9)。
起因:10-20 Pro 13 裝 captain-room 要手動 git archive + 搬資料夾,授權檔要手動 ssh + install -m 600。

外掛(驗法一律走 aib_ext.inspect:信任鑰匙簽章 + 檔案雜湊 + requires_aib;只收 kind = plugin):
I1 資料夾 ⇒ 裝到 <狀態根>/plugins/<名>,discover 找得到,印授權狀態
I2 .tgz / .zip(頂層多一層資料夾也行)⇒ 同上;再裝一次 = 換掉
I3 簽過之後改了一個檔 ⇒ rc 3、一個檔都不留
I4 沒簽章 ⇒ rc 3
I5 https / 127.0.0.1 網址 ⇒ 下載後同上;http://別處 ⇒ 拒;不是 plugin 的種類 ⇒ rc 3
I6 壓縮檔裡有 ../ 或絕對路徑 ⇒ rc 2、狀態根外一個檔都沒寫
授權:
L1 簽對、沒過期 ⇒ 寫到 <home>/license.json、0600、印 tier / addons / 到期,不印簽章
L2 原本有檔 ⇒ 先備份 .bak-*,只留最近 2 份
L3 簽錯 ⇒ rc 3 原檔不動;過期 ⇒ rc 3;不是 JSON ⇒ rc 2
刀(複本):K1 外掛不驗簽章 ⇒ I4 紅;K2 授權不驗簽章 ⇒ L3 紅
"""
import base64
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "tools" / "verify"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
from entitlement import cli as ECLI, ed25519, keys, signing  # noqa: E402
import aib_ext  # noqa: E402
import aib_plugins as AP  # noqa: E402

SEED = bytes(range(32))
PUB = "ed25519:" + ed25519.public_key(SEED).hex()


def resign(m):
    m.pop("signature", None)
    m["signature"] = "ed25519:" + base64.b64encode(ed25519.sign(SEED, signing.canonical(m))).decode()
    return m


class Box(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-plugin-install-")
        self.tmp = Path(self.td.name)
        self.home = self.tmp / "home"
        self.state = self.home / ".ez2-aib"
        self.state.mkdir(parents=True)
        self.keyp = mock.patch.object(keys, "TRUSTED_PUBLIC_KEYS", (PUB,))
        self.keyp.start()
        self.envp = mock.patch.dict(os.environ, {"HOME": str(self.home), "EZ2_AIB_STATE_ROOT": str(self.state),
                                                 "EZ2_AIB_HOME": str(self.state), "EZ2_AIB_LOCALE": "zh-TW"})
        self.envp.start()

    def tearDown(self):
        self.keyp.stop()
        self.envp.stop()
        self.td.cleanup()

    def plugin_dir(self, name="probe", sign=True, kind="plugin"):
        d = self.tmp / ("src-" + name)
        if d.exists():
            shutil.rmtree(d)
        d.mkdir()
        (d / "plugin.json").write_text(json.dumps({"name": name, "version": "0.0.1", "module": "probe.py",
                                                   "requires_addon": "probe"}), encoding="utf-8")
        (d / "probe.py").write_text("print('hi')\n", encoding="utf-8")
        m = {"name": name, "kind": kind, "version": "0.0.1", "requires_aib": "0.1.0", "files": aib_ext._hashes(d)}
        if sign:
            resign(m)
        (d / aib_ext.MANIFEST).write_text(json.dumps(m), encoding="utf-8")
        return d

    def run_install(self, src):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = AP.main(["install", str(src), "--json"])
        line = buf.getvalue().strip().splitlines()[-1] if buf.getvalue().strip() else "{}"
        return rc, json.loads(line)

    def installed(self, name="probe"):
        return self.state / "plugins" / name


class Plugins(Box):
    def test_i1_folder(self):
        rc, res = self.run_install(self.plugin_dir())
        self.assertEqual(rc, 0, res)
        self.assertTrue((self.installed() / "plugin.json").is_file())
        self.assertEqual(res["name"], "probe")
        self.assertIn("probe", AP.discover(REPO, home_only=True))
        self.assertEqual((res["addon"], res["licensed"]), ("probe", False), "沙盒沒授權要照實說")

    def test_i2_archives(self):
        d = self.plugin_dir()
        tgz = self.tmp / "probe.tgz"
        with tarfile.open(tgz, "w:gz") as tf:
            tf.add(d, arcname="captain-probe")           # 頂層多一層
        rc, res = self.run_install(tgz)
        self.assertEqual(rc, 0, res)
        self.assertTrue((self.installed() / "probe.py").is_file())
        (d / "probe.py").write_text("print('v2')\n", encoding="utf-8")
        m = json.loads((d / aib_ext.MANIFEST).read_text())
        m["files"] = aib_ext._hashes(d)
        (d / aib_ext.MANIFEST).write_text(json.dumps(resign(m)))
        z = self.tmp / "probe.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for p in d.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(d).as_posix())
        rc, res = self.run_install(z)
        self.assertEqual(rc, 0, res)
        self.assertIn("v2", (self.installed() / "probe.py").read_text(), "再裝一次 = 換掉")

    def test_i3_tampered(self):
        d = self.plugin_dir()
        (d / "probe.py").write_text("import os\n", encoding="utf-8")
        rc, res = self.run_install(d)
        self.assertEqual(rc, 3, res)
        self.assertFalse(self.installed().exists())

    def test_i4_unsigned(self):
        rc, res = self.run_install(self.plugin_dir(sign=False))
        self.assertEqual(rc, 3, res)
        self.assertFalse(self.installed().exists())

    def test_i5_url_and_kind(self):
        d = self.plugin_dir()
        site = self.tmp / "site"
        site.mkdir()
        with tarfile.open(site / "probe.tgz", "w:gz") as tf:
            tf.add(d, arcname="probe")
        handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(site), **k)  # noqa: E731
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            rc, res = self.run_install("http://127.0.0.1:%d/probe.tgz" % srv.server_address[1])
            self.assertEqual(rc, 0, res)
            self.assertTrue((self.installed() / "probe.py").is_file())
            rc, res = self.run_install("http://example.invalid/probe.tgz")
            self.assertEqual((rc, res["error"]), (2, "https-only"))
        finally:
            srv.shutdown()
        rc, res = self.run_install(self.plugin_dir(name="theme1", kind="theme"))
        self.assertEqual(rc, 3, res)
        self.assertFalse((self.state / "plugins" / "theme1").exists())

    def test_i6_unsafe_archive(self):
        d = self.plugin_dir()
        bad = self.tmp / "bad.tgz"
        with tarfile.open(bad, "w:gz") as tf:
            tf.add(d, arcname="probe")
            info = tarfile.TarInfo("../escape.txt")
            info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))
        rc, res = self.run_install(bad)
        self.assertEqual(rc, 2, res)
        self.assertTrue(res["error"].startswith("unsafe-path"), res)
        self.assertFalse(self.installed().exists())
        self.assertFalse(list(self.tmp.glob("escape.txt")) or list(self.tmp.parent.glob("escape.txt")))


class License(Box):
    def lic(self, **over):
        obj = {"tier": "all", "addons": ["captain-room", "test-guard"], "expires_at": "2099-01-01T00:00:00+00:00",
               "note": "box", "issued_at": "2026-10-20T00:00:00+00:00"}
        obj.update(over)
        return resign(obj)

    def run_lic(self, obj, raw=None):
        f = self.tmp / "lic-in.json"
        f.write_text(raw if raw is not None else json.dumps(obj), encoding="utf-8")
        buf = io.StringIO()
        with mock.patch.object(keys, "TRUSTED_PUBLIC_KEYS", (PUB,)), redirect_stdout(buf):
            rc = ECLI.main(["install", str(f)])
        return rc, json.loads(buf.getvalue().strip().splitlines()[-1])

    def test_l1_install(self):
        rc, res = self.run_lic(self.lic())
        self.assertEqual(rc, 0, res)
        dest = self.state / "license.json"
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
        self.assertEqual((res["tier"], res["addons"], res["backup"]), ("all", ["captain-room", "test-guard"], None))
        self.assertNotIn("signature", json.dumps(res))
        with mock.patch.object(keys, "TRUSTED_PUBLIC_KEYS", (PUB,)):
            self.assertEqual(AP.licensed({"requires_addon": "captain-room"})[0], True)

    def test_l2_backups(self):
        for i in range(4):
            rc, res = self.run_lic(self.lic(note="v%d" % i))
            self.assertEqual(rc, 0, res)
            time.sleep(1.05)
        baks = sorted(self.state.glob("license.json.bak-*"))
        self.assertEqual(len(baks), 2, baks)
        self.assertTrue(res["backup"] and Path(res["backup"]).is_file())
        self.assertEqual(json.loads((self.state / "license.json").read_text())["note"], "v3")

    def test_l3_rejects(self):
        rc, _ = self.run_lic(self.lic(note="good"))
        self.assertEqual(rc, 0)
        bad = self.lic(note="evil")
        bad["signature"] = "ed25519:" + base64.b64encode(b"\0" * 64).decode()
        rc, res = self.run_lic(bad)
        self.assertEqual((rc, res["error"]), (3, "signature-invalid"))
        self.assertEqual(json.loads((self.state / "license.json").read_text())["note"], "good", "原檔不動")
        rc, res = self.run_lic(self.lic(expires_at="2020-01-01T00:00:00+00:00"))
        self.assertEqual((rc, res["error"]), (3, "expired"))
        rc, res = self.run_lic(None, raw="not json")
        self.assertEqual(rc, 2)


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_plugin_unsigned_accepted(self):
        self._knife("tools/verify/aib_plugins.py", "UNSIGNED_OK = False", "UNSIGNED_OK = True", "Plugins.test_i4_unsigned")

    def test_k2_license_unverified(self):
        self._knife("src/entitlement/cli.py", "    if ver != signing.OK:\n", "    if False:\n", "License.test_l3_rejects")


if __name__ == "__main__":
    unittest.main()
