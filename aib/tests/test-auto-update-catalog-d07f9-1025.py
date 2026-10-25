#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""自動更新查付費外掛落後:先走 ez2.page 目錄(plugin_catalog.fetch_signed),只在端點沒有 / 連不到時退回 aib-plugins git。
10-25 @abb89 定案:① 只有 plugin_unsupported / plugin_unreachable 退回;大小 / sha256 不符、簽章驗不過、包名稱版本跟目錄不符 ⇒ 停、
不退回、不列;② #273 A-M3 不變 —— 只列名、不自動裝、不降版;③ 目錄網址可換,狀態 / doctor / 紀錄寫實際來源;⑤ hostguard 照舊排除;
⑥ 暫存狀態根 + 本機 http 假目錄伺服器、測試自己的信任鑰匙。

C1 目錄有較新、簽章驗得過 ⇒ 列名(behind)、一個檔都沒裝;付費包帶 bearer + X-AIB-Machine;via catalog
C2 目錄 404 / 連不到 / 包 500 ⇒ via fallback(只有這兩種退回)
C3 sha256 不符 / 包被改過 / 沒簽 / 包版本比目錄宣告的低 / 目錄形狀不對 ⇒ 停:不列、不退回、problem 寫原因
C3c 就算 aib_plugins 放行沒簽的包(UNSIGNED_OK),自動更新自己再驗 signed_ok ⇒ 照停
C4 hostguard 落後 ⇒ 列排除、它的包一個請求都沒送;C5 目錄版本一樣 / 比較舊 ⇒ 不動、不抓包(不降版)
C6 沒裝付費外掛 ⇒ 一個請求都不送
C7 check() 接起來:狀態存 plugin_via / plugin_via_url / plugin_problem;記的網址去掉帳密 / 參數 / 控制字元;status / doctor / 紀錄寫實際來源;驗不過 ⇒ doctor note
刀(複本):K1 拿掉自動更新自己的 signed_ok ⇒ C3c 紅;K2 包驗證失敗改成退回 git ⇒ C3 紅;K3 目錄驗證失敗改成退回 git ⇒ C3 紅
"""
import base64
import hashlib
import http.server
import importlib.util
import io
import json
import os
import shutil
import socket
import sys
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (HERE, REPO / "src", REPO / "src" / "cockpit", REPO / "src" / "cockpit" / "page", REPO / "tools" / "verify"):
    sys.path.insert(0, str(_p))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
from entitlement import ed25519, keys, signing  # noqa: E402
import aib_ext  # noqa: E402
import aib_plugins as AP  # noqa: E402
import api as A  # noqa: E402
import auto_update as AU  # noqa: E402

SEED = bytes(range(32))
PUB = "ed25519:" + ed25519.public_key(SEED).hex()
MID = "11111111-2222-3333-4444-555555555555"
PREFIX = A.API_PREFIX


def resign(m):
    m.pop("signature", None)
    m["signature"] = "ed25519:" + base64.b64encode(ed25519.sign(SEED, signing.canonical(m))).decode()
    return m


class Catalog:
    """本機 http 假目錄伺服器:routes = {路徑: (狀態碼, bytes)};沒列的 ⇒ 404 plugin_unknown。seen 記每個請求。"""

    def __init__(self):
        self.routes, self.seen = {}, []
        owner = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                owner.seen.append((self.path, dict(self.headers)))
                path = self.path[len(PREFIX):] if self.path.startswith(PREFIX) else self.path
                status, body = owner.routes.get(path, (404, b'{"error": "plugin_unknown"}'))
                self.send_response(status)
                self.send_header("Content-Type", "application/json" if body[:1] in (b"{", b"[") else "application/gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def packages_seen(self):
        return [p for p, _h in self.seen if "/plugins/packages/" in p]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Box(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-autocat-")
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.state = self.tmp / "home" / ".ez2-aib"
        self.state.mkdir(parents=True)
        (self.state / "trusted-ext-keys").write_text(PUB + "\n", encoding="utf-8")
        self.srv = Catalog()
        self.addCleanup(self.srv.close)
        self.env = {"HOME": str(self.tmp / "home"), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_AIB_HOME": str(self.state),
                    "EZ2_AIB_LOCALE": "zh-TW", "EZ2_PAGE_API_BASE": self.srv.url}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        k = mock.patch.object(keys, "TRUSTED_PUBLIC_KEYS", (PUB,))
        k.start()
        self.addCleanup(k.stop)

    def installed(self, name, version="1.0.0"):
        d = self.state / "plugins" / name
        d.mkdir(parents=True)
        (d / "plugin.json").write_text(json.dumps({"name": name, "module": "m.py", "version": version}))
        (d / "m.py").write_text("VERSION = %r\n" % version)
        (d / aib_ext.MANIFEST).write_text(json.dumps({"name": name, "kind": "plugin", "version": version,
                                                      "files": aib_ext._hashes(d)}))

    def version_of(self, name):
        return json.loads((self.state / "plugins" / name / aib_ext.MANIFEST).read_text())["version"]

    def tgz(self, name, version, sign=True, tamper=False, says=None):
        """包;says = 包自己 aib-ext.json 寫的版本(預設同 version)。"""
        d = self.tmp / ("src-%s-%s" % (name, version))
        if d.exists():
            shutil.rmtree(d)
        d.mkdir()
        (d / "plugin.json").write_text(json.dumps({"name": name, "module": "m.py", "version": says or version}))
        (d / "m.py").write_text("VERSION = %r\n" % (says or version))
        m = {"name": name, "kind": "plugin", "version": says or version, "requires_aib": "0.1.0", "files": aib_ext._hashes(d)}
        if sign:
            resign(m)
        (d / aib_ext.MANIFEST).write_text(json.dumps(m), encoding="utf-8")
        if tamper:
            (d / "m.py").write_text("print('evil')\n", encoding="utf-8")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            tf.add(d, arcname=name)
        return buf.getvalue()

    def publish(self, rows, catalog_status=200):
        """rows = [(名稱, 版本, 包 bytes, addon, 目錄改寫欄位)];目錄 + 包放上假伺服器。"""
        entries = []
        for name, ver, data, addon, over in rows:
            e = {"name": name, "version": ver, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                 "addon": addon, "price": None, "requires_aib": "0.1.0", "summary": None}
            e.update(over or {})
            entries.append(e)
            self.srv.routes["/plugins/packages/%s/%s.tgz" % (name, ver)] = (200, data)
        self.srv.routes["/plugins/catalog"] = (catalog_status, json.dumps({"ok": True, "v": 1, "plugins": entries}).encode())

    def bind(self, env):
        return A.PageApi(base_url=env["EZ2_PAGE_API_BASE"], token="tok-test"), MID

    def behind(self, excluded=("hostguard",)):
        return AU.catalog_behind(dict(os.environ), excluded, binding=self.bind)


class Route(Box):
    def test_c1_signed_newer_listed_not_installed(self):
        self.installed("demo-paid")
        self.publish([("demo-paid", "1.0.3", self.tgz("demo-paid", "1.0.3"), "demo", None)])
        with mock.patch.object(aib_ext, "install") as inst, mock.patch.object(aib_ext, "install_keeping") as keep:
            out = self.behind()
            inst.assert_not_called()
            keep.assert_not_called()
        self.assertEqual((out["via"], out["problem"], out["unsigned"]), ("catalog", None, []), out)
        self.assertEqual([(b["name"], b["have"], b["want"], b["source"]) for b in out["behind"]],
                         [("demo-paid", "1.0.0", "1.0.3", "catalog")])
        self.assertEqual(self.version_of("demo-paid"), "1.0.0")
        pk = [(p, h) for p, h in self.srv.seen if "/plugins/packages/" in p]
        self.assertEqual(len(pk), 1)
        hdr = {k.lower(): v for k, v in pk[0][1].items()}
        self.assertEqual((hdr.get("authorization"), hdr.get("x-aib-machine")), ("Bearer tok-test", MID))
        self.assertEqual(list((self.tmp).glob("aib-auto-catalog-*")), [])

    def test_c2_only_unsupported_unreachable_fall_back(self):
        self.installed("demo-paid")
        self.srv.routes["/plugins/catalog"] = (404, b"")
        out = self.behind()
        self.assertEqual((out["via"], out["problem"], out["behind"]), ("fallback", "plugin_unsupported", []))
        self.publish([("demo-paid", "1.0.3", self.tgz("demo-paid", "1.0.3"), "demo", None)])
        self.srv.routes["/plugins/packages/demo-paid/1.0.3.tgz"] = (502, b"{}")
        out = self.behind()
        self.assertEqual((out["via"], out["problem"], out["behind"]), ("fallback", "plugin_unreachable", []))
        with mock.patch.dict(os.environ, {"EZ2_PAGE_API_BASE": "http://127.0.0.1:%d" % free_port()}):
            out = AU.catalog_behind(dict(os.environ), (), binding=self.bind)
        self.assertEqual((out["via"], out["problem"]), ("fallback", "plugin_unreachable"))

    def test_c3_verification_failure_stops(self):
        """驗證沒過 ⇒ 停在目錄這條:不列、不退回 git、problem 寫原因、名字進 unsigned。"""
        self.installed("demo-paid")
        good = self.tgz("demo-paid", "1.0.3")
        cases = [
            ("plugin_download_mismatch", [("demo-paid", "1.0.3", good, "demo", {"sha256": "0" * 64})]),
            ("plugin_download_mismatch", [("demo-paid", "1.0.3", good, "demo", {"size": len(good) + 1})]),
            ("plugin_rejected", [("demo-paid", "1.0.3", self.tgz("demo-paid", "1.0.3", tamper=True), "demo", None)]),
            ("plugin_rejected", [("demo-paid", "1.0.3", self.tgz("demo-paid", "1.0.3", sign=False), "demo", None)]),
            ("plugin_rejected", [("demo-paid", "1.0.3", self.tgz("demo-paid", "1.0.3", says="1.0.2"), "demo", None)]),
        ]
        for code, rows in cases:
            self.srv.routes.clear()
            self.publish(rows)
            out = self.behind()
            self.assertEqual((out["via"], out["problem"], out["behind"], out["unsigned"]),
                             ("catalog", code, [], ["demo-paid"]), (code, rows[0][4]))
        self.srv.routes["/plugins/catalog"] = (200, json.dumps({"ok": True, "v": 1, "plugins": [{"name": "../x"}]}).encode())
        out = self.behind()
        self.assertEqual((out["via"], out["problem"], out["behind"]), ("catalog", "plugin_catalog_invalid", []))
        self.assertEqual(self.version_of("demo-paid"), "1.0.0")

    def test_c3c_own_signature_check(self):
        self.installed("demo-paid")
        self.publish([("demo-paid", "1.0.3", self.tgz("demo-paid", "1.0.3", sign=False), "demo", None)])
        with mock.patch.object(AP, "UNSIGNED_OK", True):
            out = self.behind()
        self.assertEqual((out["via"], out["problem"], out["behind"], out["unsigned"]),
                         ("catalog", "plugin_rejected", [], ["demo-paid"]), out)

    def test_c4_hostguard_excluded(self):
        self.installed("hostguard", "0.2.0")
        self.installed("demo-paid")
        self.publish([("hostguard", "0.3.0", self.tgz("hostguard", "0.3.0"), None, None),
                      ("demo-paid", "1.0.3", self.tgz("demo-paid", "1.0.3"), "demo", None)])
        out = self.behind()
        self.assertEqual((out["skipped"], [b["name"] for b in out["behind"]]), (["hostguard"], ["demo-paid"]))
        self.assertEqual(self.srv.packages_seen(), [PREFIX + "/plugins/packages/demo-paid/1.0.3.tgz"])
        self.assertEqual(self.version_of("hostguard"), "0.2.0")

    def test_c5_same_or_older_untouched(self):
        self.installed("demo-paid", "1.0.3")
        for ver in ("1.0.3", "1.0.1"):
            self.srv.routes.clear()
            self.publish([("demo-paid", ver, self.tgz("demo-paid", ver), "demo", None)])
            out = self.behind()
            self.assertEqual((out["via"], out["behind"], out["problem"]), ("catalog", [], None), ver)
        self.assertEqual(self.srv.packages_seen(), [])

    def test_c7b_recorded_url_scrubbed(self):
        self.assertEqual(AU.safe_url("https://user:secret@page.test:8443/x/?k=tok#frag"), "https://page.test:8443/x/")
        self.assertEqual(AU.safe_url("git@github.com:ez2app/aib-plugins.git"), "git@github.com:ez2app/aib-plugins.git")
        self.assertEqual(AU.safe_url("https://a.test/\x1b[31m\u202ex"), "https://a.test/[31mx")
        self.assertEqual(AU.safe_url("https://a\x1b[31m.test"), "?")          # 主機名稱清完不成形 ⇒ ?
        self.assertEqual(AU.safe_url(""), "?")
        self.assertLessEqual(len(AU.safe_url("https://a.test/" + "x" * 500)), 200)
        with mock.patch.dict(os.environ, {"EZ2_PAGE_API_BASE": "https://u:secret@page.test/?k=tok"}):
            self.assertEqual(AU.catalog_url(), "https://page.test/")
        st, _skew = AU.clean_status({"plugin_via_url": "x" * 201, "plugin_via": "catalog", "plugin_problem": "plugin_rejected"})
        self.assertEqual((st.get("plugin_via_url"), st["plugin_via"], st["plugin_problem"]), (None, "catalog", "plugin_rejected"))

    def test_c6_nothing_installed_no_requests(self):
        out = self.behind()
        self.assertEqual((out["via"], out["behind"]), ("catalog", []))
        self.assertEqual(self.srv.seen, [])


def _load_base():
    spec = importlib.util.spec_from_file_location("au74e8b", HERE / "test-auto-update-74e8b-1023.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load_base()


class Wired(M.Base):
    """check() 接上目錄這條:狀態、status、doctor、紀錄寫實際來源。"""

    def setUp(self):
        super().setUp()
        self.srv = Catalog()
        self.addCleanup(self.srv.close)
        self.state = Path(self.env["EZ2_AIB_STATE_ROOT"])
        (self.state).mkdir(parents=True, exist_ok=True)
        (self.state / "trusted-ext-keys").write_text(PUB + "\n", encoding="utf-8")
        k = mock.patch.object(keys, "TRUSTED_PUBLIC_KEYS", (PUB,))
        k.start()
        self.addCleanup(k.stop)
        self.env["EZ2_PAGE_API_BASE"] = os.environ["EZ2_PAGE_API_BASE"] = self.srv.url + "/"
        self.base = self.srv.url

    installed = Box.installed
    tgz = Box.tgz
    publish = Box.publish

    @property
    def tmp(self):
        return self.td

    def cat(self, env, excluded):
        return AU.catalog_behind(env, excluded, binding=lambda e: (A.PageApi(base_url=self.srv.url, token="t"), MID))

    def test_c7_status_doctor_log_show_source(self):
        self.installed("demo-paid")
        self.publish([("demo-paid", "1.0.3", self.tgz("demo-paid", "1.0.3"), "demo", None)])
        self.make_repos(latest=None)
        git_calls = []
        res = self.run_check(catalog=self.cat, plugin_fetch=lambda e: git_calls.append(1))
        self.assertEqual(git_calls, [], "目錄拿得到就不碰 git")
        self.assertEqual((res["plugin_via"], res["plugin_via_url"], res["plugin_source"], res["plugins_paid_behind"]),
                         ("catalog", self.base, "ok", ["demo-paid"]), res)
        self.assertEqual(self.version_of("demo-paid"), "1.0.0")
        p = AU.status_payload(self.env, platform="linux")
        self.assertEqual((p["plugin_via"], p["plugin_via_url"]), ("catalog", self.base))
        text = "\n".join(AU.status_lines(p))
        self.assertIn(self.base, text)
        self.assertIn("aib plugins install", text)
        note, line = AU.doctor_line(self.env)
        self.assertEqual(note, "note")
        self.assertIn(self.base, line)
        logtext = AU.log_path(self.env).read_text(encoding="utf-8")
        self.assertIn("via=catalog url=%s" % self.base, logtext)
        self.assertNotIn("tok", logtext)

        self.publish([("demo-paid", "1.0.4", self.tgz("demo-paid", "1.0.4"), "demo", {"sha256": "0" * 64})])
        res = self.run_check(now=M.NOW + 7200, catalog=self.cat, plugin_fetch=lambda e: git_calls.append(1))
        self.assertEqual(git_calls, [], "驗證沒過不退回 git")
        self.assertEqual((res["plugin_source"], res["plugin_problem"], res["plugins_paid_behind"], res["plugins_unsigned"]),
                         ("rejected", "plugin_download_mismatch", [], ["demo-paid"]), res)
        note, line = AU.doctor_line(self.env)
        self.assertEqual(note, "note")
        self.assertIn("plugin_download_mismatch", line)
        self.assertIn("plugin_download_mismatch", "\n".join(AU.status_lines(AU.status_payload(self.env, platform="linux"))))

        self.srv.routes["/plugins/catalog"] = (404, b"")
        res = self.run_check(now=M.NOW + 14400, catalog=self.cat, plugin_fetch=lambda e: git_calls.append(1))
        self.assertEqual(git_calls, [1], "端點沒有 ⇒ 退回 git")
        self.assertEqual((res["plugin_via"], res["plugin_source"], res["plugin_problem"]), ("git", "offline", "plugin_unsupported"))
        self.assertEqual(res["plugin_via_url"], AU.PLUGIN_SOURCE)

    def version_of(self, name):
        return json.loads((self.state / "plugins" / name / aib_ext.MANIFEST).read_text())["version"]


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, case, rel="src/cockpit/auto_update.py"):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl, tests=(name, "test-auto-update-74e8b-1023.py")) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k1_no_own_signature_check(self):
        self._knife("ok = m is not None and X.signed_ok(m)", "ok = m is not None", "Route.test_c3c_own_signature_check")

    def test_k2_package_failure_falls_back(self):
        self._knife("if code in CATALOG_FALLBACK:               # 目錄拿到了", "if True:  # 目錄拿到了",
                    "Route.test_c3_verification_failure_stops")

    def test_k3_catalog_failure_falls_back(self):
        self._knife('        if code in CATALOG_FALLBACK:\n            return dict(out, via="fallback", problem=code)\n        return dict(out, problem=code)',
                    '        return dict(out, via="fallback", problem=code)',
                    "Route.test_c3_verification_failure_stops")


if __name__ == "__main__":
    unittest.main()
