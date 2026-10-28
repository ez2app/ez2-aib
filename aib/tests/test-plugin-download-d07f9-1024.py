#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""會員從 ez2.page 拿外掛與授權(10-24 Andy「#20 修正」;契約 contracts/page-api.json plugins / plugins_*)。
網路全換假的、暫存狀態根、測試自己的信任鑰匙。

D1 目錄:形狀對 ⇒ 列出(price / summary 清過);任何一筆不合契約 ⇒ plugin_catalog_invalid 整份不信;沒碼 404 ⇒ unsupported;連不上 ⇒ unreachable;
    access(10-25):不在 public/member/paid、或 paid 跟 addon 對不上 ⇒ invalid;舊伺服器沒 access ⇒ 照 addon 補(paid / public)
D1b 讀回應有總時限(伺服器滴位元組拖不完);目錄錯誤回應怪(details 數字、極端撤銷日期)⇒ 仍是契約碼
D2 免費外掛照名稱裝:不帶憑證、路徑照目錄的 name / version 自己組;裝好 discover 找得到
D3 挑版本:沒寫挑最大;<名稱>@<版本> 照寫的
D4 下載的包大小或 sha256 跟目錄不符 ⇒ plugin_download_mismatch,一個檔都沒裝
D5 包沒簽 / 簽過又改 / 簽的是別的外掛(名稱版本跟目錄不符)⇒ plugin_rejected,沒裝
D6 防降版:目錄比已裝舊 ⇒ plugin_downgrade(沒裝);一樣 ⇒ plugin_already_current;明寫 @舊版 ⇒ 裝
D7b 會員外掛(access=member、addon null,10-25 Andy「綁定會員就能下載」):沒綁 ⇒ plugin_not_bound、不下載;有綁 ⇒ 帶 bearer + X-AIB-Machine,
    裝好 licensed True;scope 不足 ⇒ plugin_scope_missing
D7 付費外掛:沒綁 ⇒ plugin_not_bound、包一個請求都沒送;有綁 ⇒ 帶 bearer + X-AIB-Machine;沒買 ⇒ plugin_license_none;
    憑證沒有 plugins:read(序號綁的,10-25 @abb89)⇒ 403 page_api_scope_mismatch ⇒ plugin_scope_missing、沒裝、hint 指配對碼重綁
D8c 現有授權仍有效且加購較多 ⇒ plugin_license_fewer 不蓋;舊的過期 / 簽錯 / 讀不到 ⇒ 照換
D8 領授權:簽對 ⇒ 0600 落檔;簽錯 / 過期 / 不是物件 ⇒ plugin_license_invalid 不落檔;帶 activation_url ⇒ activation needed;沒買 ⇒ plugin_license_none;
    403 page_api_scope_mismatch ⇒ plugin_scope_missing 不落檔(別的 403 碼照舊 plugin_refused)
D9 名稱不合格式 ⇒ plugin_name_invalid、不連網;CLI install 給名稱走目錄、給資料夾照舊
D10 契約:伺服器碼 = plugins.codes;每個回條 detail 都在契約、四語都有 hint;付費包 / 授權的 scope_missing 指到 plugin_scope_missing
D4b 解開大小 / 成員數 / pax 大標頭超過上限 ⇒ too-big-unpacked(驗簽前就擋);稀疏成員 / 壞 zip / 超長名 / bz2 ⇒ 拒收不當掉;包內成員名稱清過才進 problems
    伺服器的 aib-ext.json 不是物件 / 太深 / 怪數字 / 代理字元 ⇒ plugin_rejected;目錄 / 授權帶單獨代理字元 ⇒ 印得出來、授權不落檔
刀(複本):K1 不比 sha256 ⇒ D4 紅;K2 不比包名稱版本 ⇒ D5 紅;K3 付費 / 會員包不帶憑證 ⇒ D7 / D7b 紅;K4 不擋降版 ⇒ D6 紅;
    K5 不限解開大小 ⇒ D4b 紅;K6 授權不比加購 ⇒ D8 紅;K7 gzip 不限量解開 ⇒ D4b 紅(pax 大標頭);K8 收稀疏成員 ⇒ D4b 紅;K9 讀回應沒總時限 ⇒ D1b 紅;
    K23 授權不比舊檔加購 ⇒ D8c 紅;K24 不認 scope 不足(收成 plugin_refused)⇒ D7 / D8 紅
"""
import base64
import hashlib
import http.client
import contextlib
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
                dict(good, name="../x"), dict(good, version="1.2"), dict(good, addon="A B"), "x",
                dict(good, access="vip"), dict(good, access="paid"), dict(good, access="member", addon="probe"),
                dict(good, access="public", addon="probe"), dict(good, access=None)]
        for bad in bads:
            res, rc = AP.catalog_cmd(dict(os.environ), api(self.server([good, bad], {})))
            self.assertEqual((rc, res["detail"]), (3, "plugin_catalog_invalid"), bad)
        legacy = [good, dict(good, name="paidone", addon="probe")]
        res, rc = AP.catalog_cmd(dict(os.environ), api(self.server(legacy, {})))
        self.assertEqual([e["access"] for e in res["plugins"]], ["public", "paid"], "舊伺服器沒 access ⇒ 照 addon 補")
        res, rc = AP.catalog_cmd(dict(os.environ), api(self.server([dict(good, access="member")], {})))
        self.assertEqual((rc, res["plugins"][0]["access"]), (0, "member"))
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
        shutil.rmtree(self.installed())
        explicit = self.server([self.entry(data, access="public")], {("probe", "0.0.1"): data})
        res, rc = self.install("probe", explicit, paid_api=api(FakeServer(), token="ast_x"), machine_id=MID)
        self.assertEqual((rc, res["detail"]), (0, "plugin_installed"), res)
        pkg = [r for r in explicit.seen if r.full_url.endswith(".tgz")]
        self.assertIsNone(pkg[0].get_header("Authorization"), "目錄明寫 access=public ⇒ 有綁也不帶憑證(#365 r1 L)")

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

    def test_d7b_member(self):
        data = self.tgz()
        ent = self.entry(data, access="member")
        srv = self.server([ent], {("probe", "0.0.1"): data})
        res, rc = self.install("probe", srv)
        self.assertEqual((rc, res["detail"]), (2, "plugin_not_bound"), "沙盒沒綁")
        self.assertFalse([r for r in srv.seen if r.full_url.endswith(".tgz")], "沒綁不下載會員包")
        scope = FakeServer({"/plugins/packages/probe/0.0.1.tgz": (403, {"error": "page_api_scope_mismatch"})})
        res, rc = self.install("probe", srv, paid_api=api(scope, token="ast_x"), machine_id=MID)
        self.assertEqual((rc, res["detail"]), (3, "plugin_scope_missing"), res)
        self.assertFalse(self.installed().exists())
        member = FakeServer({"/plugins/packages/probe/0.0.1.tgz": (200, data)})
        res, rc = self.install("probe", srv, paid_api=api(member, token="ast_x"), machine_id=MID)
        self.assertEqual((rc, res["detail"], res["addon"], res["licensed"]), (0, "plugin_installed", None, True), res)
        req = member.seen[0]
        self.assertEqual((req.get_header("Authorization"), req.get_header("X-aib-machine")), ("Bearer ast_x", MID))

    def test_d7c_plan_required(self):
        # 目錄條目帶 plan、伺服器 403 plugin_plan_required details [需要的級] ⇒ 回條 detail 原碼 + plan、沒裝;值不在五級 / public 帶 plan ⇒ 整份目錄不合契約
        import aib_ext
        home = aib_ext.target("plugin", "captain-room")   # 帶 plan 的條目要銷售總開關 on 才看得到(D12);這格先開
        home.mkdir(parents=True)
        (home / aib_ext.MANIFEST).write_text(json.dumps(resign({"name": "captain-room", "kind": "plugin", "version": "0.0.1",
                                                                "requires_aib": "0.1.0", "files": {}, "settings": {"sales": "on"}})), encoding="utf-8")
        self.assertEqual(PC.sales_on(), (True, "signed"))
        data = self.tgz()
        ent = dict(self.entry(data, access="member"), plan="pro")
        srv = self.server([ent], {("probe", "0.0.1"): data})
        self.assertEqual(PC.parse_catalog({"ok": True, "v": 1, "plugins": [ent]})[0]["plan"], "pro")
        res, rc = AP.catalog_cmd(dict(os.environ), api(self.server([ent, dict(self.entry(data, access="public"), version="0.0.2")], {})))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            AP._emit(res, rc)
        lines = buf.getvalue().splitlines()
        self.assertEqual((rc, len(lines)), (0, 2), buf.getvalue())
        self.assertIn("member pro+", lines[0])   # 畫面:這一級以上含在方案內
        self.assertNotIn("+", lines[1])
        for bad in (dict(ent, plan="vip"), dict(ent, plan=3), dict(self.entry(data, access="public"), plan="free")):
            self.assertIsNone(PC.parse_catalog({"ok": True, "v": 1, "plugins": [bad]}), bad.get("plan"))
        low = FakeServer({"/plugins/packages/probe/0.0.1.tgz": (403, {"error": "plugin_plan_required", "details": ["pro"]})})
        res, rc = self.install("probe", srv, paid_api=api(low, token="ast_x"), machine_id=MID)
        self.assertEqual((rc, res["detail"], res.get("plan")), (3, "plugin_plan_required", "pro"), res)
        self.assertTrue(res.get("hint"), res)
        self.assertFalse(self.installed().exists())
        odd = FakeServer({"/plugins/packages/probe/0.0.1.tgz": (403, {"error": "plugin_plan_required", "details": ["<b>x</b>"]})})
        res, rc = self.install("probe", srv, paid_api=api(odd, token="ast_x"), machine_id=MID)
        self.assertEqual((rc, res["detail"], "plan" in res), (3, "plugin_plan_required", False), res)

    def test_d12_sales_switch(self):
        # 銷售總開關(Andy 10-25 23:5x):只認簽章內的 captain-room settings.sales;off ⇒ 帶 plan 的條目對這台不存在(目錄 / 安裝同一條路)
        import aib_ext
        data = self.tgz()
        planned = dict(self.entry(data, access="member"), plan="pro")
        plain = dict(self.entry(data, access="member"), version="0.0.2")
        srv = self.server([planned, plain], {("probe", "0.0.1"): data, ("probe", "0.0.2"): data})
        only = self.server([planned], {("probe", "0.0.1"): data})
        self.assertEqual(PC.sales_on(), (False, "missing"))
        res, rc = AP.catalog_cmd(dict(os.environ), api(srv))
        self.assertEqual((rc, [e["version"] for e in res["plugins"]]), (0, ["0.0.2"]), res)
        res, rc = self.install("probe", only)
        self.assertEqual((rc, res["detail"]), (2, "plugin_not_in_catalog"), "off ⇒ 帶 plan 的條目裝不到")
        home = aib_ext.target("plugin", "captain-room")
        home.mkdir(parents=True)
        base = {"name": "captain-room", "kind": "plugin", "version": "0.0.1", "requires_aib": "0.1.0", "files": {}}
        def put(m):
            (home / aib_ext.MANIFEST).write_text(json.dumps(m), encoding="utf-8")
        put(dict(base, settings={"sales": "on"}))   # 沒簽 ⇒ 不認
        self.assertEqual(PC.sales_on(), (False, "unsigned"))
        put(resign(dict(base, settings={"sales": "on"})))
        self.assertEqual(PC.sales_on(), (True, "signed"))
        res, rc = AP.catalog_cmd(dict(os.environ), api(srv))
        self.assertEqual(sorted(e["version"] for e in res["plugins"]), ["0.0.1", "0.0.2"], "on ⇒ 帶 plan 的條目列出")
        m = resign(dict(base, settings={"sales": "on"}))
        m["settings"]["sales"] = "off"
        put(m)   # 簽完再改 ⇒ 簽章驗不過 ⇒ off
        self.assertEqual(PC.sales_on(), (False, "unsigned"))
        put(resign(dict(base, settings={"sales": "off"})))
        self.assertEqual(PC.sales_on(), (False, "signed"))
        res, rc = AP.catalog_cmd(dict(os.environ), api(srv))
        self.assertEqual([e["version"] for e in res["plugins"]], ["0.0.2"])
        priced = dict(self.entry(data, access="paid", addon="probe"), price="NT$99")   # off ⇒ 畫面不印價錢(資料照在 --json)
        res, rc = AP.catalog_cmd(dict(os.environ), api(self.server([priced], {})))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            AP._emit(res, rc)
        self.assertNotIn("NT$99", buf.getvalue())
        self.assertEqual(buf.getvalue().split()[2], "-", buf.getvalue())   # 付費條目 off 時只印「-」
        self.assertEqual(res["plugins"][0]["price"], "NT$99")
        put(resign(dict(base, settings={"sales": "on"})))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            AP._emit(res, rc)
        self.assertIn("NT$99", buf.getvalue())
        put(resign(dict(base, settings={"sales": "yes"})))
        self.assertEqual(PC.sales_on(), (False, "unset"))
        put(resign(dict(base)))
        self.assertEqual(PC.sales_on(), (False, "unset"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            AP.main(["licensed", "farm", "--json"])
        out = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertEqual((out["sales"], out["sales_source"]), ("off", "unset"), out)

    def test_d7_paid(self):
        data = self.tgz(addon="probe")
        ent = self.entry(data, addon="probe", price="NT$90")
        srv = self.server([ent], {("probe", "0.0.1"): data})
        res, rc = self.install("probe", srv)
        self.assertEqual((rc, res["detail"]), (2, "plugin_not_bound"), "沙盒沒綁")
        self.assertFalse([r for r in srv.seen if r.full_url.endswith(".tgz")], "沒綁不下載付費包")
        shown = self.server([dict(ent, access="paid")], {})   # 目錄明寫 access=paid(不是缺鍵補值那條,#365 r1 L)
        paid = FakeServer({"/plugins/packages/probe/0.0.1.tgz": (200, data)})
        res, rc = self.install("probe", shown, paid_api=api(paid, token="ast_x"), machine_id=MID)
        self.assertEqual((rc, res["detail"]), (0, "plugin_installed"), res)
        self.assertEqual(paid.seen[0].get_header("Authorization"), "Bearer ast_x", "明寫 paid ⇒ 帶憑證")
        self.assertFalse([r for r in shown.seen if r.full_url.endswith(".tgz")], "付費包不走公開那支")
        shutil.rmtree(self.installed())
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
        scope = FakeServer({"/plugins/packages/probe/0.0.1.tgz": (403, {"error": "page_api_scope_mismatch"})})
        res, rc = self.install("probe", srv, paid_api=api(scope, token="ast_x"), machine_id=MID)
        self.assertEqual((rc, res["detail"]), (3, "plugin_scope_missing"), res)
        self.assertIn("aib page bind", res["hint"])
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

    def test_d8b_license_odd_errors(self):
        """授權端點回怪錯誤體 ⇒ rc 在契約內、不拋例外、license.json 不動(第 6 輪)。"""
        dest = self.state / "license.json"
        for st, body in ((403, {"error": "x", "details": 5}), (500, {"error": "x", "details": True}), (403, {"error": ["x"]}),
                         (401, {"error": "page_api_token_revoked", "revoked_at": "0001-01-01T00:00:00+14:00"}),
                         (200, b"not json"), (200, [1, 2])):
            res, rc, _ = self.get(st, body)
            self.assertIn(rc, (2, 3, 4), (st, body, res))
            self.assertFalse(dest.exists())

    def test_d8c_license_keeps_addons(self):
        """現有授權仍有效、加購比較多 ⇒ 不蓋(plugin_license_fewer);舊的過期 / 簽錯 ⇒ 照換(@abb89 #276 親審 Low)。"""
        dest = self.state / "license.json"
        self.assertEqual(self.get(200, {"ok": True, "license": self.lic(addons=["probe", "room", "line"])})[1], 0)
        big = dest.read_text()
        res, rc, _ = self.get(200, {"ok": True, "license": self.lic(addons=["probe", "room"])})
        self.assertEqual((rc, res["detail"]), (3, "plugin_license_fewer"))
        self.assertIn("line", res["hint"])
        self.assertNotIn("room", res["hint"].replace("aib", ""))
        self.assertEqual(dest.read_text(), big, "不蓋掉加購較多的授權")
        res, rc, _ = self.get(200, {"ok": True, "license": self.lic(addons=["line", "probe", "room", "x2"])})
        self.assertEqual(rc, 0)                                           # 多的照換
        old = json.loads(dest.read_text())                               # 舊的過期 ⇒ 照換
        old["expires_at"] = "2020-01-01T00:00:00+00:00"
        dest.write_text(json.dumps(resign(old)))
        self.assertEqual(self.get(200, {"ok": True, "license": self.lic()})[1], 0)
        old = json.loads(dest.read_text())                               # 舊的簽錯 ⇒ 照換
        old["addons"] = ["probe", "a", "b"]
        dest.write_text(json.dumps(old))
        self.assertEqual(self.get(200, {"ok": True, "license": self.lic()})[1], 0)
        dest.write_text("not json")                                      # 讀不到 ⇒ 照換
        self.assertEqual(self.get(200, {"ok": True, "license": self.lic()})[1], 0)

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
        before = dest.read_text()
        res, rc, _ = self.get(403, {"error": "page_api_scope_mismatch"})
        self.assertEqual((rc, res["detail"]), (3, "plugin_scope_missing"))
        self.assertIn("--serial", res["hint"])
        self.assertEqual(dest.read_text(), before, "scope 不足不落檔")
        res, rc, _ = self.get(403, {"error": "page_api_owner_disabled"})
        self.assertEqual(res["detail"], "plugin_refused", "別的 403 碼不當 scope 不足")
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
                setattr(s, "_opener", srv), setattr(s, "_raw_opener", srv))[0]), redirect_stdout(buf):
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
        for name in ("plugins_package", "plugins_license"):
            sm = C["endpoints"][name]["scope_missing"]
            self.assertEqual((sm["http"], sm["code"], sm["core"]), (403, PC.SCOPE_MISSING, "plugin_scope_missing"))
            self.assertTrue(C["endpoints"][name]["scope"].startswith("plugins:read"))
        self.assertIn("plugin_scope_missing", rec["local_codes"])
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

    def test_h5_redirect_not_followed(self):
        """伺服器回 302 + 巨大本體:不跟轉址、本體只讀 64 KiB(第 6 輪;urllib 預設會整個讀進記憶體再轉)。"""
        import http.server
        import threading
        seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.send_header("Content-Length", str(8 * 1024 * 1024))
                self.end_headers()
                try:
                    for _ in range(128):
                        self.wfile.write(b"\0" * 65536)
                except OSError:
                    pass

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        api = A.PageApi(base_url="http://127.0.0.1:%d" % srv.server_address[1])
        api.total_s = 10
        st, raw = api._get_raw("/plugins/catalog", {}, 1024)
        self.assertEqual(st, 302)
        self.assertLessEqual(len(raw), 64 * 1024)
        self.assertEqual([p for p in seen if p.endswith("/elsewhere")], [])

    def test_h6_link_then_write_through(self):
        """tar 先放資料夾連結、再往連結裡寫:自己的檢查先擋(not-a-file),3.9 沒有 data filter 也寫不出去(第 6 輪)。"""
        outside = self.tmp / "outside"
        outside.mkdir()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            ln = tarfile.TarInfo("probe/d")
            ln.type = tarfile.SYMTYPE
            ln.linkname = str(outside)
            tf.addfile(ln)
            f = tarfile.TarInfo("probe/d/evil")
            f.size = 3
            tf.addfile(f, io.BytesIO(b"pwn"))
        into = self.tmp / "lk"
        into.mkdir()
        self.assertEqual(AP._extract(buf.getvalue(), into), (False, "not-a-file:probe/d"))
        self.assertEqual(list(outside.iterdir()), [])

    def test_h7_gunzip_steps_bounded(self):
        """gzip 一次最多吐 1 MiB:每次 decompress 都帶 max_length(拿掉上限 ⇒ 炸彈先整包解開才判太大)。"""
        import gzip
        import zlib
        calls = []
        real = zlib.decompressobj

        class Spy:
            def __init__(self, *a):
                self.d = real(*a)

            def decompress(self, data, max_length=0):
                calls.append(max_length)
                return self.d.decompress(data, max_length)

            def __getattr__(self, name):
                return getattr(self.d, name)
        blob = gzip.compress(b"\0" * (8 << 20), 9)
        with mock.patch.object(zlib, "decompressobj", Spy):
            self.assertIsNone(AP._gunzip_bounded(blob, 2 << 20))
        self.assertTrue(calls)
        self.assertTrue(all(0 < c <= 1 << 20 for c in calls), calls[:5])

    def test_h8_hardlink_rejected(self):
        """tar 硬連結指到目標夾外的檔、再放同名一般檔:自己的檢查先擋(3.9 沒 data filter 時是唯一防線;第 7 輪)。"""
        outside = self.tmp / "outside.txt"
        outside.write_text("ORIGINAL")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            ln = tarfile.TarInfo("probe/h")
            ln.type = tarfile.LNKTYPE
            ln.linkname = str(outside)
            tf.addfile(ln)
            f = tarfile.TarInfo("probe/h")
            f.size = 3
            tf.addfile(f, io.BytesIO(b"pwn"))
        into = self.tmp / "hl"
        into.mkdir()
        self.assertEqual(AP._extract(buf.getvalue(), into), (False, "not-a-file:probe/h"))
        self.assertEqual(outside.read_text(), "ORIGINAL")

    def test_h9_truncated_gzip_returns(self):
        """截斷的 gzip(sha256 對得上目錄就會走到這裡)要很快回,不能空轉(第 7 輪)。"""
        import gzip
        import threading
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            ti = tarfile.TarInfo("probe/a")
            ti.size = 5
            tf.addfile(ti, io.BytesIO(b"hello"))
        blob = gzip.compress(buf.getvalue())[:-8]
        into = self.tmp / "tg"
        into.mkdir()
        got = []
        t = threading.Thread(target=lambda: got.append(AP._extract(blob, into)), daemon=True)
        t.start()
        t.join(5)
        self.assertFalse(t.is_alive(), "截斷 gzip 空轉")
        self.assertEqual(len(got), 1)

    def test_h10_catalog_license_body_capped(self):
        """目錄 / 授權的 200 本體也照上限讀(1 MiB / 256 KiB),不整包讀進記憶體(第 7 輪)。"""
        big = b"{" + b" " * (3 << 20) + b"}"
        got = {}
        orig = A.PageApi._get_raw

        def spy(api_, path, headers, max_bytes):
            st, raw = orig(api_, path, headers, max_bytes)
            got[path] = len(raw)
            return st, raw
        srv = FakeServer({"/plugins/catalog": (200, big), "/plugins/licenses/probe": (200, big)})
        with mock.patch.object(A.PageApi, "_get_raw", spy):
            res, rc = AP.catalog_cmd(dict(os.environ), api(srv))
            self.assertIn(rc, (2, 3, 4))
            res, rc = PC.license_get(dict(os.environ), "probe", api=api(srv, token="t"), machine_id=MID)
            self.assertIn(rc, (2, 3, 4))
        self.assertLessEqual(got["/plugins/catalog"], (1 << 20) + 1)
        self.assertLessEqual(got["/plugins/licenses/probe"], (256 << 10) + 1)

    def test_h11_server_code_5xx_is_4(self):
        """伺服器碼 + 5xx ⇒ rc 4(可重試),rc 一定在契約內(第 7 輪)。"""
        for code in ("plugin_unknown", "plugin_license_none", "plugin_license_device_limit"):
            srv = FakeServer({"/plugins/catalog": (503, {"error": code}), "/plugins/licenses/probe": (503, {"error": code})})
            res, rc = AP.catalog_cmd(dict(os.environ), api(srv))
            self.assertEqual(rc, 4, (code, res))
            res, rc = PC.license_get(dict(os.environ), "probe", api=api(srv, token="t"), machine_id=MID)
            self.assertEqual(rc, 4, (code, res))

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
        for case in ("Install.test_d7_paid", "Install.test_d7b_member"):
            self._knife("machine_id=machine_id if member else None)", "machine_id=None)", case)

    def test_k25_member_as_public(self):
        self._knife('member = entry["access"] != "public"', 'member = entry["addon"] is not None', "Install.test_d7b_member")

    def test_k26_access_unchecked(self):
        self._knife('if access not in ACCESS or (access == "paid") != (addon is not None):', "if False:", "Catalog.test_d1_catalog")

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

    def test_k14_follow_redirect(self):
        self._knife("self._raw_opener = opener or urllib.request.build_opener(_NoRedirect)",
                    "self._raw_opener = opener or urllib.request.build_opener()", "Hard.test_h5_redirect_not_followed",
                    rel="src/cockpit/page/api.py")

    def test_k15_only_sparse_rejected(self):
        self._knife("if not (m.isfile() or m.isdir()) or m.issparse():", "if m.issparse():",
                    "Hard.test_h6_link_then_write_through", rel="tools/verify/aib_plugins.py")

    def test_k16_gunzip_unbounded_step(self):
        self._knife("chunk = d.decompress(buf, 1 << 20)", "chunk = d.decompress(buf)",
                    "Hard.test_h7_gunzip_steps_bounded", rel="tools/verify/aib_plugins.py")

    def test_k17_license_catch_all(self):
        self._knife('    except Exception:  # noqa: BLE001\n        return fail("plugin_unreachable", 4)\n    obj = payload.get("license")',
                    '    except ZeroDivisionError:\n        return fail("plugin_unreachable", 4)\n    obj = payload.get("license")',
                    "License.test_d8b_license_odd_errors")

    def test_k18_hardlink_allowed(self):
        self._knife("if not (m.isfile() or m.isdir()) or m.issparse():",
                    "if not (m.isfile() or m.isdir() or m.islnk()) or m.issparse():",
                    "Hard.test_h8_hardlink_rejected", rel="tools/verify/aib_plugins.py")

    def test_k19_truncated_gzip_spins(self):
        self._knife("        if not chunk and not buf:\n            break\n", "", "Hard.test_h9_truncated_gzip_returns",
                    rel="tools/verify/aib_plugins.py")

    def test_k20_catalog_body_uncapped(self):
        self._knife('{"accept": "application/json", "user-agent": USER_AGENT}, max_bytes)',
                    '{"accept": "application/json", "user-agent": USER_AGENT}, 1 << 40)',
                    "Hard.test_h10_catalog_license_body_capped", rel="src/cockpit/page/api.py")

    def test_k21_license_body_uncapped(self):
        self._knife('headers, 256 * 1024)', 'headers, 1 << 40)', "Hard.test_h10_catalog_license_body_capped",
                    rel="src/cockpit/page/api.py")

    def test_k22_server_code_5xx_rc(self):
        self._knife("return code, 4 if http >= 500 else 3, {}", "return code, 5 if http >= 500 else 3, {}",
                    "Hard.test_h11_server_code_5xx_is_4")

    def test_k23_license_overwrites_bigger(self):
        self._knife("    if dropped:   #", "    if False:   #", "License.test_d8c_license_keeps_addons")

    def test_k24_scope_missing_unmapped(self):
        for case in ("Install.test_d7_paid", "License.test_d8_license"):
            self._knife("    if http == 403 and code == SCOPE_MISSING:", "    if False:", case)

    def test_k10_no_total_deadline(self):
        self._knife("        t.join(total_s + self.JOIN_GRACE_S)", "        t.join()", "Slow.test_d1c_total_deadline",
                    rel="src/cockpit/page/api.py")

    def test_k9_no_read_deadline(self):
        self._knife("            if time.monotonic() > deadline:\n", "            if False:\n", "Slow.test_d1b_read_deadline",
                    rel="src/cockpit/page/api.py")


if __name__ == "__main__":
    unittest.main()
