#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""會員從 ez2.page 拿外掛與授權(10-24 Andy「#20 修正」;契約 contracts/page-api.json plugins / plugins_*)。
網路全換假的、暫存狀態根、測試自己的信任鑰匙。

D1 目錄:形狀對 ⇒ 列出(price / summary 清過);任何一筆不合契約 ⇒ plugin_catalog_invalid 整份不信;沒碼 404 ⇒ unsupported;連不上 ⇒ unreachable
D1b 讀回應有總時限(伺服器滴位元組拖不完);目錄錯誤回應怪(details 數字、極端撤銷日期)⇒ 仍是契約碼
D2 免費外掛照名稱裝:不帶憑證、路徑照目錄的 name / version 自己組;裝好 discover 找得到
D3 挑版本:沒寫挑最大;<名稱>@<版本> 照寫的
D4 下載的包大小或 sha256 跟目錄不符 ⇒ plugin_download_mismatch,一個檔都沒裝
D5 包沒簽 / 簽過又改 / 簽的是別的外掛(名稱版本跟目錄不符)⇒ plugin_rejected,沒裝
D6 防降版:目錄比已裝舊 ⇒ plugin_downgrade(沒裝);一樣 ⇒ plugin_already_current;明寫 @舊版 ⇒ 裝
D7 付費外掛:沒綁 ⇒ plugin_not_bound、包一個請求都沒送;有綁 ⇒ 帶 bearer + X-AIB-Machine;沒買 ⇒ plugin_license_none
D8 領授權:簽對 ⇒ 0600 落檔;簽錯 / 過期 / 不是物件 ⇒ plugin_license_invalid 不落檔;帶 activation_url ⇒ activation needed;沒買 ⇒ plugin_license_none
D9 名稱不合格式 ⇒ plugin_name_invalid、不連網;CLI install 給名稱走目錄、給資料夾照舊
D10 契約:伺服器碼 = plugins.codes;每個回條 detail 都在契約、四語都有 hint
D4b 解開大小 / 成員數 / pax 大標頭超過上限 ⇒ too-big-unpacked(驗簽前就擋);稀疏成員 / 壞 zip / 超長名 / bz2 ⇒ 拒收不當掉;包內成員名稱清過才進 problems
    伺服器的 aib-ext.json 不是物件 / 太深 / 怪數字 / 代理字元 ⇒ plugin_rejected;目錄 / 授權帶單獨代理字元 ⇒ 印得出來、授權不落檔
刀(複本):K1 不比 sha256 ⇒ D4 紅;K2 不比包名稱版本 ⇒ D5 紅;K3 付費包不帶憑證 ⇒ D7 紅;K4 不擋降版 ⇒ D6 紅;
    K5 不限解開大小 ⇒ D4b 紅;K6 授權不比加購 ⇒ D8 紅;K7 gzip 不限量解開 ⇒ D4b 紅(pax 大標頭);K8 收稀疏成員 ⇒ D4b 紅;K9 讀回應沒總時限 ⇒ D1b 紅
"""
import base64
import hashlib
import http.client
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
import urllib.error
import zipfile
from contextlib import redirect_stdout
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
import plugin_catalog as PC  # noqa: E402

C = json.loads((REPO / "contracts" / "page-api.json").read_text(encoding="utf-8"))
SEED = bytes(range(32))
PUB = "ed25519:" + ed25519.public_key(SEED).hex()
MID = "11111111-2222-3333-4444-555555555555"


def resign(m):
    m.pop("signature", None)
    m["signature"] = "ed25519:" + base64.b64encode(ed25519.sign(SEED, signing.canonical(m))).decode()
    return m


class _Resp:
    def __init__(self, status, body):
        self.status, self._body, self._pos = status, body, 0

    def read(self, n=-1):                                     # 同真的回應:接著上次讀到的地方
        end = len(self._body) if n is None or n < 0 else self._pos + n
        out, self._pos = self._body[self._pos:end], min(end, len(self._body))
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeServer:
    """路徑 ⇒ (http, bytes 或 dict)。沒列的路徑 ⇒ 404 {"error": "plugin_unknown"}。"""

    def __init__(self, routes=None, boom=False, raise_exc=None):
        self.routes, self.boom, self.raise_exc, self.seen = dict(routes or {}), boom, raise_exc, []

    def open(self, req, timeout=None):
        self.seen.append(req)
        if self.boom:
            raise urllib.error.URLError("down")
        if self.raise_exc is not None:
            raise self.raise_exc
        path = req.full_url.split(C["prefix"], 1)[1]
        status, body = self.routes.get(path, (404, {"error": "plugin_unknown"}))
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(raw))
        return _Resp(status, raw)


def api(server, token=None):
    return A.PageApi(base_url="https://page.test", token=token, opener=server)


class Box(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-plugdl-")
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.state = self.tmp / "home" / ".ez2-aib"
        self.state.mkdir(parents=True)
        (self.state / "trusted-ext-keys").write_text(PUB + "\n", encoding="utf-8")
        self.env = {"HOME": str(self.tmp / "home"), "EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_AIB_HOME": str(self.state),
                    "EZ2_AIB_LOCALE": "zh-TW", "EZ2_PAGE_API_BASE": "https://page.test"}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        k = mock.patch.object(keys, "TRUSTED_PUBLIC_KEYS", (PUB,))
        k.start()
        self.addCleanup(k.stop)

    def tgz(self, name="probe", version="0.0.1", sign=True, tamper=False, kind="plugin", addon=None, extra=None, manifest_raw=None):
        d = self.tmp / ("src-%s-%s" % (name, version))
        if d.exists():
            shutil.rmtree(d)
        d.mkdir()
        pj = {"name": name, "version": version, "module": "probe.py"}
        if addon:
            pj["requires_addon"] = addon
        (d / "plugin.json").write_text(json.dumps(pj), encoding="utf-8")
        (d / "probe.py").write_text("print('hi')\n", encoding="utf-8")
        m = {"name": name, "kind": kind, "version": version, "requires_aib": "0.1.0", "files": aib_ext._hashes(d)}
        if sign:
            resign(m)
        (d / aib_ext.MANIFEST).write_text(manifest_raw if manifest_raw is not None else json.dumps(m), encoding="utf-8")
        if tamper:
            (d / "probe.py").write_text("print('evil')\n", encoding="utf-8")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            tf.add(d, arcname=name)
            for ti, body in extra or ():
                tf.addfile(ti, io.BytesIO(body) if body is not None else None)
        return buf.getvalue()

    def entry(self, data, name="probe", version="0.0.1", addon=None, **over):
        e = {"name": name, "version": version, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
             "addon": addon, "price": None, "requires_aib": "0.1.0", "summary": None}
        e.update(over)
        return e

    def server(self, entries, packages, extra=None):
        routes = {"/plugins/catalog": (200, {"ok": True, "v": 1, "plugins": entries})}
        for (n, v), data in packages.items():
            routes["/plugins/packages/%s/%s.tgz" % (n, v)] = (200, data)
        routes.update(extra or {})
        return FakeServer(routes)

    def install(self, spec, server, **kw):
        return AP.install_named(spec, env=dict(os.environ), api=api(server), **kw)

    def installed(self, name="probe"):
        return self.state / "plugins" / name


class Catalog(Box):
    def test_d1_catalog(self):
        data = self.tgz()
        good = self.entry(data, price="NT$90\x1b[31m", summary="說明\u202e" + "x" * 300)
        res, rc = AP.catalog_cmd(dict(os.environ), api(self.server([good], {})))
        self.assertEqual((rc, res["detail"]), (0, "plugin_catalog"))
        e = res["plugins"][0]
        self.assertNotIn("\x1b", e["price"])
        self.assertNotIn("\u202e", e["summary"])
        self.assertLessEqual(len(e["summary"]), 120)
        bads = [dict(good, sha256="0" * 63), dict(good, size=PC.max_pkg_bytes() + 1), dict(good, size=True),
                dict(good, name="../x"), dict(good, version="1.2"), dict(good, addon="A B"), "x"]
        for bad in bads:
            res, rc = AP.catalog_cmd(dict(os.environ), api(self.server([good, bad], {})))
            self.assertEqual((rc, res["detail"]), (3, "plugin_catalog_invalid"), bad)
        res, rc = AP.catalog_cmd(dict(os.environ), api(FakeServer({"/plugins/catalog": (404, b"")})))
        self.assertEqual((rc, res["detail"]), (3, "plugin_unsupported"))
        res, rc = AP.catalog_cmd(dict(os.environ), api(FakeServer(boom=True)))
        self.assertEqual((rc, res["detail"]), (4, "plugin_unreachable"))
        big = {"/plugins/catalog": (200, b'{"ok": true, "v": 1, "plugins": [], "pad": "' + b"x" * (1024 * 1024) + b'"}')}
        res, rc = AP.catalog_cmd(dict(os.environ), api(FakeServer(big)))
        self.assertEqual((rc, res["detail"]), (3, "plugin_catalog_invalid"), "目錄回應有上限")
        res, rc = AP.catalog_cmd(dict(os.environ), api(FakeServer(raise_exc=http.client.IncompleteRead(b"ab", 10))))
        self.assertEqual((rc, res["detail"]), (4, "plugin_unreachable"), "讀到一半斷")
        res, rc = AP.catalog_cmd(dict(os.environ), api(FakeServer({"/plugins/catalog": (200, b"[" * 60000)})))
        self.assertIn(rc, (3, 4), res)
        odd = [(403, {"error": "x", "details": 5}), (500, {"error": "x", "details": True}),
               (401, {"error": "page_api_token_revoked", "revoked_at": "0001-01-01T00:00:00+14:00"}),
               (401, {"error": "page_api_token_revoked", "revoked_at": "9999-12-31T23:59:59-14:00"}), (403, {"error": ["x"]})]
        for http_status, body in odd:                         # 伺服器怪回應讓既有程式拋別的 ⇒ 仍是契約碼
            res, rc = AP.catalog_cmd(dict(os.environ), api(FakeServer({"/plugins/catalog": (http_status, body)})))
            self.assertIn(rc, (3, 4), body)
            self.assertIn(res["detail"], set(C["plugins"]["cli_receipt"]["local_codes"]) | set(PC.CALLER_DEAD), body)
        sur = {"/plugins/catalog": (200, json.dumps({"ok": True, "v": 1, "plugins": [dict(good, summary="a\\ud800b", price="\\ud800")]})
                                    .replace("\\\\ud800", "\\ud800").encode())}
        res, rc = AP.catalog_cmd(dict(os.environ), api(FakeServer(sur)))
        self.assertEqual(rc, 0, res)
        for as_json in (True, False):
            out = io.BytesIO()
            w = io.TextIOWrapper(out, encoding="utf-8")       # 同真的 stdout:strict 編碼,代理字元會拋
            with redirect_stdout(w):
                AP._emit(res, rc, as_json=as_json)
                w.flush()
            self.assertIn(b"?", out.getvalue(), as_json)


class Slow(unittest.TestCase):
    def test_d1b_read_deadline(self):
        class Drip:
            def read1(self, n):
                return b"x"
        clock = iter(range(0, 10 ** 6))
        with mock.patch.object(A.time, "monotonic", lambda: next(clock)):
            with self.assertRaises(A.TransportError):
                A.PageApi._read_capped(Drip(), 10 ** 6, 100)    # 伺服器一次滴一個位元組:總時限到就停
        self.assertEqual(A.PageApi._read_capped(_Resp(200, b"abc"), 2, 100), b"ab")

    def test_d1c_total_deadline(self):
        """標頭階段 / 錯誤體慢滴也在總期限內收成 TransportError(第 5 輪)。"""
        import threading
        import time as _t
        gate = threading.Event()
        self.addCleanup(gate.set)

        class Hang:
            def open(self, req, timeout=None):
                gate.wait(6)                      # 標頭一直滴不完
                return _Resp(200, b"{}")

        class DripErr(urllib.error.HTTPError):
            def __init__(self):
                super().__init__("https://page.test/x", 500, "x", {}, None)

            def read1(self, n):
                _t.sleep(0.05)
                return b"x"

        class ErrOpen:
            def open(self, req, timeout=None):
                raise DripErr()
        api = A.PageApi(base_url="https://page.test", opener=Hang())
        api.total_s = 0.5
        t0 = _t.monotonic()
        with self.assertRaises(A.TransportError):
            api._get_raw("/plugins/catalog", {}, 1024)
        self.assertLess(_t.monotonic() - t0, 4.5)
        api = A.PageApi(base_url="https://page.test", opener=ErrOpen())
        api.total_s = 0.5
        t0 = _t.monotonic()
        self.assertEqual(api._get_raw("/plugins/catalog", {}, 1024), (500, b""))   # 錯誤體讀不完 ⇒ 只留狀態碼
        self.assertLess(_t.monotonic() - t0, 4.5)


class Install(Box):
    def test_d2_free_by_name(self):
        data = self.tgz()
        srv = self.server([self.entry(data)], {("probe", "0.0.1"): data})
        res, rc = self.install("probe", srv)
        self.assertEqual((rc, res["detail"], res["version"], res["addon"]), (0, "plugin_installed", "0.0.1", None), res)
        self.assertTrue((self.installed() / "probe.py").is_file())
        self.assertIn("probe", AP.discover(REPO, home_only=True))
        pkg = [r for r in srv.seen if r.full_url.endswith(".tgz")]
        self.assertEqual(pkg[0].full_url, "https://page.test" + C["prefix"] + "/plugins/packages/probe/0.0.1.tgz")
        self.assertIsNone(pkg[0].get_header("Authorization"), "免費包不帶憑證")

    def test_d3_pick_version(self):
        d1, d2 = self.tgz(version="0.0.9"), self.tgz(version="0.0.10")
        srv = self.server([self.entry(d1, version="0.0.9"), self.entry(d2, version="0.0.10")],
                          {("probe", "0.0.9"): d1, ("probe", "0.0.10"): d2})
        res, rc = self.install("probe", srv)
        self.assertEqual((rc, res["version"]), (0, "0.0.10"), "照數字比,不照字串(字串比 0.0.9 > 0.0.10)")
        res, rc = self.install("probe", self.server([self.entry(d1, version="0.0.9")], {("probe", "0.0.9"): d1}))
        self.assertEqual((rc, res["detail"]), (2, "plugin_downgrade"), "已裝 0.0.10、目錄 0.0.9 ⇒ 降版")
        res, rc = self.install("probe@0.0.9", srv)
        self.assertEqual((rc, res["version"]), (0, "0.0.9"))
        res, rc = self.install("probe@9.9.9", srv)
        self.assertEqual((rc, res["detail"]), (2, "plugin_not_in_catalog"))

    def test_d4_download_mismatch(self):
        data = self.tgz()
        other = self.tgz(version="0.0.1", tamper=False) + b"\0"
        for ent, body in ((self.entry(data, sha256="0" * 64), data), (self.entry(data), other),
                          (self.entry(data, size=len(data) - 1), data)):
            res, rc = self.install("probe", self.server([ent], {("probe", "0.0.1"): body}))
            self.assertEqual((rc, res["detail"]), (3, "plugin_download_mismatch"), ent)
            self.assertFalse(self.installed().exists())

    def test_d4b_unpack_limits(self):
        zero = tarfile.TarInfo("probe/zero.bin")
        zero.size = 5000
        bomb = self.tgz(extra=[(zero, b"\0" * 5000)])
        many = self.tgz(extra=[(tarfile.TarInfo("probe/f%d.txt" % i), b"") for i in range(30)])
        for label, data, patch in (("bytes", bomb, {"MAX_UNPACK_BYTES": 4000}), ("members", many, {"MAX_UNPACK_MEMBERS": 20})):
            with mock.patch.multiple(AP, **patch):
                res, rc = self.install("probe", self.server([self.entry(data)], {("probe", "0.0.1"): data}))
            self.assertEqual((rc, res["detail"], res["problems"]), (3, "plugin_rejected", ["too-big-unpacked"]), label)
            self.assertFalse(self.installed().exists(), label)
        pax = tarfile.TarInfo("probe/pax.txt")
        pax.pax_headers = {"comment": "x" * 50000}            # 標頭本身就大:tarfile 在 next() 裡整段讀進記憶體
        bomb2 = self.tgz(extra=[(pax, b"")])
        with mock.patch.multiple(AP, MAX_UNPACK_BYTES=20000):
            ok, why = AP._extract(bomb2, self.tmp / "x1")
        self.assertEqual((ok, why), (False, "too-big-unpacked"), "pax 大標頭也算進上限")
        (self.tmp / "x2").mkdir()
        zbad = io.BytesIO()
        with zipfile.ZipFile(zbad, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("a.txt", "hello " * 200)
        raw = bytearray(zbad.getvalue())
        raw[40:60] = b"\xff" * 20                             # 壓縮資料壞掉 ⇒ zlib.error
        long = tarfile.TarInfo("n" * 1000)                    # 名稱檢查過得了、檔案系統寫不了
        long.size = 1
        for label, blob in (("bad_zip", bytes(raw)), ("long_name", self.tgz(extra=[(long, b"x")])),
                            ("bz2", b"BZh91AY&SY" + b"\0" * 100)):
            (self.tmp / label).mkdir()
            self.assertEqual(AP._extract(blob, self.tmp / label)[0], False, label)
        sp = tarfile.TarInfo("probe/sparse.bin")
        sp.type = tarfile.GNUTYPE_SPARSE                       # 照 sparse map 寫的量可以遠大於宣告的 size
        data = self.tgz(extra=[(sp, b"")])
        res, rc = self.install("probe", self.server([self.entry(data)], {("probe", "0.0.1"): data}))
        self.assertEqual((rc, res["detail"], res["problems"]), (3, "plugin_rejected", ["not-a-file:probe/sparse.bin"]), "稀疏成員")
        link = tarfile.TarInfo("probe/\x1b[2J\u202eevil")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        data = self.tgz(extra=[(link, None)])
        res, rc = self.install("probe", self.server([self.entry(data)], {("probe", "0.0.1"): data}))
        self.assertEqual((rc, res["detail"]), (3, "plugin_rejected"))
        self.assertNotIn("\x1b", json.dumps(res["problems"], ensure_ascii=False))
        self.assertNotIn("\u202e", "".join(res["problems"]))

    def test_d5_rejected(self):
        for label, data in (("unsigned", self.tgz(sign=False)), ("tampered", self.tgz(tamper=True)),
                            ("other_plugin", self.tgz(name="hostguard")), ("other_version", self.tgz(version="0.0.2")),
                            ("not_plugin", self.tgz(kind="skill")), ("manifest_list", self.tgz(manifest_raw="[]")),
                            ("manifest_deep", self.tgz(manifest_raw="[" * 200000)),
                            ("manifest_sup2", self.tgz(manifest_raw=json.dumps({"name": "probe", "kind": "plugin", "version": "0.0.1",
                                                                                "requires_aib": "0.\u00b2", "files": {}}))),
                            ("manifest_surrogate", self.tgz(manifest_raw='{"name": "probe", "kind": "plugin\\ud800", "version": "0.0.1"}'))):
            res, rc = self.install("probe", self.server([self.entry(data)], {("probe", "0.0.1"): data}))
            self.assertEqual((rc, res["detail"]), (3, "plugin_rejected"), (label, res))
            self.assertTrue(res["problems"], label)
            self.assertFalse(self.installed().exists(), label)
            self.assertFalse((self.state / "plugins" / "hostguard").exists(), label)

    def test_d6_downgrade(self):
        d2 = self.tgz(version="0.0.2")
        res, rc = self.install("probe", self.server([self.entry(d2, version="0.0.2")], {("probe", "0.0.2"): d2}))
        self.assertEqual(rc, 0, res)
        d1 = self.tgz(version="0.0.1")
        srv = self.server([self.entry(d1)], {("probe", "0.0.1"): d1})
        res, rc = self.install("probe", srv)
        self.assertEqual((rc, res["detail"], res["current"]), (2, "plugin_downgrade", "0.0.2"), res)
        self.assertIn("0.0.1", res["hint"])
        self.assertFalse([r for r in srv.seen if r.full_url.endswith(".tgz")], "降版不下載")
        self.assertEqual(PC.installed_version("probe"), "0.0.2")
        srv2 = self.server([self.entry(d2, version="0.0.2")], {("probe", "0.0.2"): d2})
        res, rc = self.install("probe", srv2)
        self.assertEqual((rc, res["detail"]), (0, "plugin_already_current"))
        for f in C["plugins"]["cli_receipt"]["install_ok"]["fields"]:
            self.assertIn(f, res, "已是最新也照 install_ok 欄位")
        res, rc = self.install("probe@0.0.1", srv)
        self.assertEqual((rc, res["detail"], PC.installed_version("probe")), (0, "plugin_installed", "0.0.1"))

    def test_d7_paid(self):
        data = self.tgz(addon="probe")
        ent = self.entry(data, addon="probe", price="NT$90")
        srv = self.server([ent], {("probe", "0.0.1"): data})
        res, rc = self.install("probe", srv)
        self.assertEqual((rc, res["detail"]), (2, "plugin_not_bound"), "沙盒沒綁")
        self.assertFalse([r for r in srv.seen if r.full_url.endswith(".tgz")], "沒綁不下載付費包")
        paid = FakeServer({"/plugins/packages/probe/0.0.1.tgz": (200, data)})
        res, rc = self.install("probe", srv, paid_api=api(paid, token="ast_x"), machine_id=MID)
        self.assertEqual((rc, res["detail"], res["addon"], res["licensed"]), (0, "plugin_installed", "probe", False), res)
        self.assertIn("aib plugins license probe", res["hint"])
        req = paid.seen[0]
        self.assertEqual((req.get_header("Authorization"), req.get_header("X-aib-machine")), ("Bearer ast_x", MID))
        shutil.rmtree(self.installed())
        liar = self.tgz(addon="probe")
        res, rc = self.install("probe", self.server([self.entry(liar)], {("probe", "0.0.1"): liar}))
        self.assertEqual((rc, res["addon"], res["licensed"]), (0, "probe", False), "addon 照簽過的包,不照目錄")
        shutil.rmtree(self.installed())
        none = FakeServer({"/plugins/packages/probe/0.0.1.tgz": (404, {"error": "plugin_license_none"})})
        res, rc = self.install("probe", srv, paid_api=api(none, token="ast_x"), machine_id=MID)
        self.assertEqual((rc, res["detail"]), (3, "plugin_license_none"))
        self.assertFalse(self.installed().exists())


class License(Box):
    def lic(self, **over):
        obj = {"tier": "all", "addons": ["probe"], "expires_at": "2099-01-01T00:00:00+00:00", "issued_at": "2026-10-24T00:00:00+00:00"}
        obj.update(over)
        return resign(obj)

    def get(self, status, body):
        srv = FakeServer({"/plugins/licenses/probe": (status, body)})
        res, rc = PC.license_get(dict(os.environ), "probe", api=api(srv, token="ast_x"), machine_id=MID)
        return res, rc, srv

    def test_d8_license(self):
        dest = self.state / "license.json"
        res, rc, srv = self.get(200, {"ok": True, "license": self.lic()})
        self.assertEqual((rc, res["detail"], res["activation"]), (0, "plugin_license_installed", None), res)
        self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
        self.assertEqual((srv.seen[0].get_header("Authorization"), srv.seen[0].get_header("X-aib-machine")), ("Bearer ast_x", MID))
        self.assertTrue(AP.licensed({"requires_addon": "probe"})[0])
        good = dest.read_text()
        bad = self.lic(note="evil")
        bad["signature"] = "ed25519:" + base64.b64encode(b"\0" * 64).decode()
        for body in ({"ok": True, "license": bad}, {"ok": True, "license": self.lic(expires_at="2020-01-01T00:00:00+00:00")},
                     {"ok": True, "license": "x"}, {"ok": True}, {"ok": True, "license": self.lic(addons=["other"], tier="free")},
                     {"ok": True, "license": dict(self.lic(), note="\ud800")}, {"ok": True, "license": self.lic(addons=5)},
                     {"ok": True, "license": self.lic(addons=True)}):
            res, rc, _ = self.get(200, body)
            self.assertEqual((rc, res["detail"]), (3, "plugin_license_invalid"), body)
            self.assertEqual(dest.read_text(), good, "驗不過不落檔")
        res, rc, _ = self.get(200, {"ok": True, "license": self.lic(activation_url="https://ez2.page/act")})
        self.assertEqual((rc, res["activation"]), (0, "needed"))
        self.assertIn("aib license activate", res["hint"])
        for code, http in (("plugin_license_none", 404), ("plugin_license_device_limit", 409)):
            res, rc, _ = self.get(http, {"error": code})
            self.assertEqual((rc, res["detail"]), (3, code))
        res, rc, _ = self.get(400, {"error": "weird\x1b[2J"})
        self.assertEqual(res["detail"], "plugin_refused")
        self.assertNotIn("\x1b", res["hint"])
        res, rc = PC.license_get(dict(os.environ), "probe")
        self.assertEqual((rc, res["detail"]), (2, "plugin_not_bound"))


class Cli(Box):
    def test_d9_names_and_cli(self):
        srv = FakeServer()
        for spec in ("../x", "Probe", "probe@1.2", "probe@", "p", "x" * 41):
            res, rc = self.install(spec, srv)
            self.assertEqual((rc, res["detail"]), (2, "plugin_name_invalid"), spec)
        res, rc = PC.license_get(dict(os.environ), "../x", api=api(srv, token="t"), machine_id=MID)
        self.assertEqual((rc, res["detail"]), (2, "plugin_name_invalid"))
        self.assertEqual(srv.seen, [], "格式不對不連網")
        data = self.tgz()
        srv = self.server([self.entry(data)], {("probe", "0.0.1"): data})
        buf = io.StringIO()
        with mock.patch.object(A.PageApi, "__init__", lambda s, base_url=None, token=None, **k: (
                setattr(s, "base_url", "https://page.test"), setattr(s, "token", token), setattr(s, "timeout", 5),
                setattr(s, "_opener", srv))[0]), redirect_stdout(buf):
            rc = AP.main(["install", "probe", "--json"])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertEqual(json.loads(buf.getvalue().strip().splitlines()[-1])["detail"], "plugin_installed")
        for src in ("nonexist.tgz", "./nonexist", str(self.tmp / "nope")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = AP.main(["install", src, "--json"])
            self.assertEqual((rc, json.loads(buf.getvalue().strip().splitlines()[-1]).get("error")), (2, "not-found"), src)
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch("urllib.request.urlopen", side_effect=OSError("x")):
            rc = AP.main(["install", "https://example.invalid/p.tgz", "--json"])
        self.assertEqual(json.loads(buf.getvalue().strip().splitlines()[-1]).get("error"), "download-failed:OSError", "網址照舊")


class Contract(Box):
    def test_d10_contract(self):
        P = C["plugins"]
        self.assertEqual(set(PC.SERVER_CODES), set(P["codes"]))
        rec = P["cli_receipt"]
        details = set(P["codes"]) | set(rec["local_codes"]) | set(PC.CALLER_DEAD)
        for k in ("catalog_ok", "install_ok", "license_ok"):
            details |= set(rec[k]["detail"])
        self.assertFalse(set(rec["local_codes"]) & set(P["codes"]), "本機碼不跟伺服器碼重名")
        for name in ("plugins_catalog", "plugins_package", "plugins_license"):
            self.assertEqual(C["endpoints"][name]["status"], "planned")
        self.assertEqual(C["endpoints"]["plugins_license"]["headers"], ["Authorization", "X-AIB-Machine"])
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8"))
            for code in details - set(PC.CALLER_DEAD) - {"plugin_catalog"}:   # 目錄回條不帶 hint
                self.assertTrue(("aib.plugins.hint." + code) in d, (lang, code))


class Hard(Box):
    """第 5 輪突變審補的硬線測試(審查員探針改寫):路徑穿越、zip 上限、本體讀取上限、下載包怪錯誤體。"""

    def _tar_with(self, name):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            ti = tarfile.TarInfo(name)
            ti.size = 3
            tf.addfile(ti, io.BytesIO(b"pwn"))
        return buf.getvalue()

    def _zip_with(self, name):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(name, "pwn")
        return buf.getvalue()

    def test_h1_traversal(self):
        for label, blob in (("tar..", self._tar_with("probe/../../escape-t1")),
                            ("tarabs", self._tar_with(str(self.tmp / "escape-t2"))),
                            ("zip..", self._zip_with("../../escape-z1")),
                            ("zipabs", self._zip_with(str(self.tmp / "escape-z2")))):
            res, rc = self.install("probe", self.server([self.entry(blob)], {("probe", "0.0.1"): blob}))
            self.assertEqual(rc, 3, label)
            self.assertFalse(self.installed().exists(), label)
        hits = [str(p) for p in Path(self.tmp).rglob("escape-*")] + [str(p) for p in Path(tempfile.gettempdir()).glob("escape-*")]
        self.assertEqual(hits, [])
        for label, blob in (("tar..", self._tar_with("probe/../../escape-t1")), ("tarabs", self._tar_with("/escape-t2")),
                            ("zip..", self._zip_with("../../escape-z1"))):
            into = self.tmp / ("x-" + label)
            into.mkdir()
            ok, why = AP._extract(blob, into)
            self.assertEqual((ok, why.split(":")[0]), (False, "unsafe-path"), label)   # 自己的檢查先擋(3.9 沒有 data filter)

    def test_h2_zip_limits(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for i in range(3):
                z.writestr("probe/z%d" % i, b"\0" * 3000)
        (self.tmp / "zb").mkdir()
        with mock.patch.multiple(AP, MAX_UNPACK_BYTES=4000):
            self.assertEqual(AP._extract(buf.getvalue(), self.tmp / "zb"), (False, "too-big-unpacked"))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for i in range(30):
                z.writestr("probe/f%d" % i, b"")
        (self.tmp / "zm").mkdir()
        with mock.patch.multiple(AP, MAX_UNPACK_MEMBERS=20):
            self.assertEqual(AP._extract(buf.getvalue(), self.tmp / "zm"), (False, "too-big-unpacked"))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            zi = zipfile.ZipInfo("probe/link")
            zi.external_attr = 0o120777 << 16
            z.writestr(zi, "/etc/passwd")
        (self.tmp / "zs").mkdir()
        ok, why = AP._extract(buf.getvalue(), self.tmp / "zs")
        self.assertEqual((ok, why.split(":")[0]), (False, "symlink"))

    def test_h3_body_read_capped(self):
        data = self.tgz()
        srv = self.server([self.entry(data)], {("probe", "0.0.1"): data + b"\0" * (5 * 1024 * 1024)})
        got = {}
        orig = A.PageApi._get_raw

        def spy(api, path, headers, max_bytes):
            st, raw = orig(api, path, headers, max_bytes)
            got[path] = len(raw)
            return st, raw
        with mock.patch.object(A.PageApi, "_get_raw", spy):
            res, rc = self.install("probe", srv)
        self.assertEqual(rc, 3)
        self.assertLessEqual(got["/plugins/packages/probe/0.0.1.tgz"], len(data) + 1)
        big = json.dumps({"error": "x", "pad": "y" * (200 * 1024)}).encode()
        srv = self.server([self.entry(data)], {}, extra={"/plugins/packages/probe/0.0.1.tgz": (500, big)})
        got.clear()
        with mock.patch.object(A.PageApi, "_get_raw", spy):
            res, rc = self.install("probe", srv)
        self.assertLessEqual(got["/plugins/packages/probe/0.0.1.tgz"], 64 * 1024)   # 錯誤體也只讀 64 KiB
        self.assertIn(rc, (2, 3, 4))

    def test_h4_package_odd_errors(self):
        data = self.tgz()
        for st, body in ((403, {"error": "x", "details": 5}), (500, {"error": "x", "details": True}),
                         (401, {"error": "page_api_token_revoked", "revoked_at": "0001-01-01T00:00:00+14:00"}),
                         (403, {"error": ["x"]}), (200, b"")):
            srv = self.server([self.entry(data)], {}, extra={"/plugins/packages/probe/0.0.1.tgz": (st, body)})
            res, rc = self.install("probe", srv)
            self.assertIn(rc, (2, 3, 4), (st, body, res))
            self.assertFalse(self.installed().exists())


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, case, rel="tools/verify/plugin_catalog.py"):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_no_sha_check(self):
        self._knife(' or hashlib.sha256(data).hexdigest() != entry["sha256"]', "", "Install.test_d4_download_mismatch")

    def test_k2_no_catalog_match(self):
        self._knife('if not probs and (m.get("name"), m.get("version")) != (entry["name"], entry["version"]):',
                    "if False:", "Install.test_d5_rejected")

    def test_k3_paid_without_auth(self):
        self._knife('machine_id=machine_id if entry["addon"] is not None else None)', "machine_id=None)", "Install.test_d7_paid")

    def test_k4_no_downgrade_guard(self):
        self._knife('if vkey(entry["version"]) < vkey(current):', "if False:", "Install.test_d6_downgrade")

    def test_k5_no_unpack_limit(self):
        self._knife("if len(members) > MAX_UNPACK_MEMBERS or total > MAX_UNPACK_BYTES:", "if False:",
                    "Install.test_d4b_unpack_limits", rel="tools/verify/aib_plugins.py")

    def test_k6_license_without_addon(self):
        self._knife(' or addon not in [a for a in addons if isinstance(a, str)]', "",
                    "License.test_d8_license")

    def test_k7_gzip_unbounded(self):
        self._knife("data = _gunzip_bounded(data, MAX_UNPACK_BYTES)", "data = __import__('gzip').decompress(data)",
                    "Install.test_d4b_unpack_limits", rel="tools/verify/aib_plugins.py")

    def test_k8_sparse_accepted(self):
        self._knife("if not (m.isfile() or m.isdir()) or m.issparse():", "if not (m.isfile() or m.isdir()):",
                    "Install.test_d4b_unpack_limits", rel="tools/verify/aib_plugins.py")

    def test_k11_zip_unbounded(self):
        self._knife("if len(infos) > MAX_UNPACK_MEMBERS or sum(i.file_size for i in infos) > MAX_UNPACK_BYTES:",
                    "if False:", "Hard.test_h2_zip_limits", rel="tools/verify/aib_plugins.py")

    def test_k12_body_read_all(self):
        self._knife("self._read_capped(response, max_bytes + 1, max(0.0, deadline - time.monotonic()))",
                    "response.read()", "Hard.test_h3_body_read_capped", rel="src/cockpit/page/api.py")

    def test_k13_package_catch_all(self):
        self._knife('    except Exception:  # noqa: BLE001\n        return None, entry, fail("plugin_unreachable", 4)\n    if len(data)',
                    '    except ZeroDivisionError:\n        return None, entry, fail("plugin_unreachable", 4)\n    if len(data)',
                    "Hard.test_h4_package_odd_errors")

    def test_k10_no_total_deadline(self):
        self._knife("        t.join(total_s + 2)", "        t.join()", "Slow.test_d1c_total_deadline",
                    rel="src/cockpit/page/api.py")

    def test_k9_no_read_deadline(self):
        self._knife("            if time.monotonic() > deadline:\n", "            if False:\n", "Slow.test_d1b_read_deadline",
                    rel="src/cockpit/page/api.py")


if __name__ == "__main__":
    unittest.main()
