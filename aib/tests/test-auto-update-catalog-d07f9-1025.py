#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""自動更新查付費外掛落後:先看 ez2.page 目錄,只在端點沒有 / 連不到時退回 aib-plugins git。
10-25 @abb89 定案:① 只有 plugin_unsupported / plugin_unreachable 退回;目錄形狀不對 / 其他碼 ⇒ 停、不退回;② #273 A-M3 不變 ——
只列名、不自動裝、不降版;③ 目錄網址可換,狀態 / doctor / 紀錄寫實際來源;⑤ hostguard 照舊排除;⑥ 暫存狀態根 + 本機 http 假目錄伺服器。
#349 r1 裁:自動路徑只比目錄版本號、不下載不驗包(驗證留給 aib plugins install);目錄只抓一次、整輪 60 秒上限;
非 x.y.z 版號跳過、目錄這步出錯 = plugin_catalog_error(不退回、不擋核心更新);沒查完 / 目錄沒列都要顯示。

C1 目錄有較新 ⇒ 列名、一個檔都沒裝;目錄只抓一次、包一個請求都不送;付費外掛不用綁定(不讀這台憑證)
C2 目錄 404 / 502 / 連不到 / 超過整輪上限 ⇒ via fallback(只有這兩種碼退回)
C3 目錄形狀不對 / 伺服器拒絕(403)⇒ 停:via catalog、不列、problem 寫原因碼
C4 hostguard 落後 ⇒ 列排除;C5 目錄版本一樣 / 比較舊 ⇒ 不動(不降版);C6 沒裝付費外掛 ⇒ 一個請求都不送
C8 多外掛混合:較新的列、一樣的不動、目錄沒列的進 missing、1.0.0-rc1 這種版號跳過(記紀錄)不讓整輪掛;目錄還是只抓一次
C7 check() 接起來:狀態存 plugin_via / plugin_via_url / plugin_problem / plugins_missing;status / doctor / 紀錄寫實際來源;
   目錄驗證沒過 ⇒ rejected、不退回 git;403 ⇒ incomplete「這次沒查完(碼)」、不退回;404 ⇒ 退回 git;
   目錄這步拋例外 ⇒ plugin_catalog_error、核心照樣更新
C7b 記的網址去掉帳密 / 查詢 / ;參數 / 控制字元(scp 寫法也去 @ 前);狀態檔讀回再清一次
刀(複本):K1 每種碼都退回 git ⇒ C3 紅;K2 不濾非 x.y.z 版號 ⇒ C8 紅;K3 check() 不包目錄這步 ⇒ C7 紅
"""
import hashlib
import http.server
import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (HERE, REPO / "src", REPO / "src" / "cockpit", REPO / "src" / "cockpit" / "page", REPO / "tools" / "verify"):
    sys.path.insert(0, str(_p))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import aib_ext  # noqa: E402
import api as A  # noqa: E402
import auto_update as AU  # noqa: E402
import plugin_catalog as PC  # noqa: E402

PREFIX = A.API_PREFIX


class Catalog:
    """本機 http 假目錄伺服器:routes = {路徑: (狀態碼, bytes)};沒列的 ⇒ 404 plugin_unknown。seen 記每個請求;delay 秒後才回。"""

    def __init__(self):
        self.routes, self.seen, self.delay = {}, [], 0
        owner = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                owner.seen.append(self.path)
                if owner.delay:
                    time.sleep(owner.delay)
                path = self.path[len(PREFIX):] if self.path.startswith(PREFIX) else self.path
                status, body = owner.routes.get(path, (404, b'{"error": "plugin_unknown"}'))
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass                               # 用戶端逾時先走了

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def catalogs(self):
        return [p for p in self.seen if p.endswith("/plugins/catalog")]

    def packages(self):
        return [p for p in self.seen if "/plugins/packages/" in p]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def entry(name, ver, addon="demo"):
    data = ("%s-%s" % (name, ver)).encode()
    return {"name": name, "version": ver, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
            "addon": addon, "price": None, "requires_aib": "0.1.0", "summary": None}


class Helpers:
    def installed(self, name, version="1.0.0"):
        d = self.state / "plugins" / name
        d.mkdir(parents=True)
        (d / "plugin.json").write_text(json.dumps({"name": name, "module": "m.py", "version": version}))
        (d / "m.py").write_text("VERSION = %r\n" % version)
        (d / aib_ext.MANIFEST).write_text(json.dumps({"name": name, "kind": "plugin", "version": version,
                                                      "files": aib_ext._hashes(d)}))

    def version_of(self, name):
        return json.loads((self.state / "plugins" / name / aib_ext.MANIFEST).read_text())["version"]

    def publish(self, entries):
        self.srv.routes["/plugins/catalog"] = (200, json.dumps({"ok": True, "v": 1, "plugins": entries}).encode())


class Box(Helpers, unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-autocat-")
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.state = self.tmp / "home" / ".ez2-aib"
        self.state.mkdir(parents=True)
        self.srv = Catalog()
        self.addCleanup(self.srv.close)
        self.env = {"HOME": str(self.tmp / "home"), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_AIB_HOME": str(self.state),
                    "EZ2_AIB_LOCALE": "zh-TW", "EZ2_PAGE_API_BASE": self.srv.url}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        b = mock.patch.object(PC, "binding", side_effect=AssertionError("自動路徑不該讀這台的綁定"))
        b.start()
        self.addCleanup(b.stop)

    def behind(self, excluded=("hostguard",), **kw):
        return AU.catalog_behind(dict(os.environ), excluded, **kw)


class Route(Box):
    def test_c1_listed_only_no_download(self):
        self.installed("demo-paid")
        self.publish([entry("demo-paid", "1.0.3")])
        with mock.patch.object(aib_ext, "install") as inst, mock.patch.object(aib_ext, "install_keeping") as keep, \
                mock.patch.object(PC, "fetch_signed", side_effect=AssertionError("自動路徑不下載")):
            out = self.behind()
            inst.assert_not_called()
            keep.assert_not_called()
        self.assertEqual((out["via"], out["problem"], out["missing"]), ("catalog", None, []), out)
        self.assertEqual([(b["name"], b["have"], b["want"], b["source"]) for b in out["behind"]],
                         [("demo-paid", "1.0.0", "1.0.3", "catalog")])
        self.assertEqual(self.version_of("demo-paid"), "1.0.0")
        self.assertEqual((len(self.srv.catalogs()), self.srv.packages()), (1, []))

    def test_c2_only_unsupported_unreachable_fall_back(self):
        self.installed("demo-paid")
        for status, code in ((404, "plugin_unsupported"), (502, "plugin_unreachable")):
            self.srv.routes["/plugins/catalog"] = (status, b"{}")
            out = self.behind()
            self.assertEqual((out["via"], out["problem"], out["behind"]), ("fallback", code, []), status)
        with mock.patch.dict(os.environ, {"EZ2_PAGE_API_BASE": "http://127.0.0.1:%d" % free_port()}):
            out = self.behind()
        self.assertEqual((out["via"], out["problem"]), ("fallback", "plugin_unreachable"))
        self.publish([entry("demo-paid", "1.0.3")])
        self.srv.delay = 4
        t0 = time.monotonic()
        out = self.behind(deadline_s=1)
        self.assertEqual((out["via"], out["problem"], out["behind"]), ("fallback", "plugin_unreachable", []), "超過整輪上限")
        self.assertLess(time.monotonic() - t0, 3.9)

    def test_c3_other_codes_stop(self):
        """目錄驗證沒過 / 伺服器拒絕 ⇒ 停在目錄這條:不列、不退回 git、problem 寫原因碼。"""
        self.installed("demo-paid")
        self.srv.routes["/plugins/catalog"] = (200, json.dumps({"ok": True, "v": 1, "plugins": [{"name": "../x"}]}).encode())
        out = self.behind()
        self.assertEqual((out["via"], out["problem"], out["behind"]), ("catalog", "plugin_catalog_invalid", []))
        self.srv.routes["/plugins/catalog"] = (403, b'{"error": "nope"}')
        out = self.behind()
        self.assertEqual((out["via"], out["problem"], out["behind"]), ("catalog", "plugin_refused", []))

    def test_c4_hostguard_excluded(self):
        self.installed("hostguard", "0.2.0")
        self.installed("demo-paid")
        self.publish([entry("hostguard", "0.3.0", None), entry("demo-paid", "1.0.3")])
        out = self.behind()
        self.assertEqual((out["skipped"], [b["name"] for b in out["behind"]]), (["hostguard"], ["demo-paid"]))
        self.assertEqual(self.version_of("hostguard"), "0.2.0")

    def test_c5_same_or_older_untouched(self):
        self.installed("demo-paid", "1.0.3")
        for ver in ("1.0.3", "1.0.1"):
            self.publish([entry("demo-paid", ver)])
            out = self.behind()
            self.assertEqual((out["via"], out["behind"], out["problem"]), ("catalog", [], None), ver)

    def test_c6_nothing_installed_no_requests(self):
        out = self.behind()
        self.assertEqual((out["via"], out["behind"]), ("catalog", []))
        self.assertEqual(self.srv.seen, [])

    def test_c8_mixed(self):
        self.installed("aaa")
        self.installed("bbb", "2.0.0")
        self.installed("ccc")
        self.installed("ddd", "1.0.0-rc1")
        self.installed("eee", "1.0.10")
        self.publish([entry("aaa", "1.0.2"), entry("bbb", "2.0.0"), entry("ddd", "9.0.0"), entry("eee", "1.0.9")])
        try:
            out = self.behind()
        except Exception as exc:  # noqa: BLE001
            self.fail("非 x.y.z 版號讓整輪掛了:%r" % exc)
        self.assertEqual(([b["name"] for b in out["behind"]], out["missing"], out["problem"]), (["aaa"], ["ccc"], None), out)
        self.assertEqual(len(self.srv.catalogs()), 1, "目錄只抓一次")
        self.assertIn("plugin ddd skipped: installed version is not x.y.z", AU.log_path(self.env).read_text(encoding="utf-8"))

    def test_c1b_deadline_is_hard_bound(self):
        """整輪上限 = deadline_s:api 的總期限要扣掉它自己的寬限;socket 逾時欄位 api 內部固定取 max(timeout, 60),不動它(#349 r2 L1)。"""
        self.installed("demo-paid")

        class FakeApi:
            timeout = 30

            def plugins_catalog(self):
                return {"ok": True, "v": 1, "plugins": [entry("demo-paid", "1.0.3")]}
        fake = FakeApi()
        out = self.behind(deadline_s=10, api=fake)
        self.assertEqual([b["name"] for b in out["behind"]], ["demo-paid"], out)
        self.assertEqual(fake.total_s, 10 - A.PageApi.JOIN_GRACE_S)
        self.assertEqual(fake.timeout, 30, "socket 逾時欄位不該被改(改了也沒用)")
        self.assertEqual(A.PageApi.JOIN_GRACE_S, 2)
        self.assertEqual(self.behind(deadline_s=1, api=fake)["behind"][0]["name"], "demo-paid")
        self.assertGreaterEqual(fake.total_s, 1)

    def test_c7b_recorded_url_scrubbed(self):
        self.assertEqual(AU.safe_url("https://user:secret@page.test:8443/x/;tok=1?k=tok#frag"), "https://page.test:8443/x/")
        self.assertEqual(AU.safe_url("git@github.com:ez2app/aib-plugins.git"), "github.com:ez2app/aib-plugins.git")
        self.assertEqual(AU.safe_url("tok:secret@host:x/y.git;a?b#c"), "host:x/y.git")
        self.assertEqual(AU.safe_url("/Users/me/wt.noindex/a@b/aib-plugins"), "/Users/me/wt.noindex/a@b/aib-plugins")   # 本機路徑裡的 @ 不是 scp(#349 r2 L4)
        self.assertEqual(AU.safe_url("/srv/x@y:z"), "/srv/x@y:z")
        self.assertEqual(AU.safe_url("https://a.test/\x1b[31m\u202ex"), "https://a.test/[31mx")
        self.assertEqual(AU.safe_url("https://a\x1b[31m.test"), "?")          # 主機名稱清完不成形 ⇒ ?
        self.assertEqual(AU.safe_url(""), "?")
        self.assertLessEqual(len(AU.safe_url("https://a.test/" + "x" * 500)), 200)
        with mock.patch.dict(os.environ, {"EZ2_PAGE_API_BASE": "https://u:secret@page.test/?k=tok"}):
            self.assertEqual(AU.catalog_url(), "https://page.test/")
        st, _skew = AU.clean_status({"plugin_via_url": "https://u:secret@x.test/p?k=1", "plugin_via": "catalog",
                                     "plugin_problem": "plugin_refused", "plugins_missing": ["ccc"]})
        self.assertEqual((st["plugin_via_url"], st["plugin_via"], st["plugin_problem"], st["plugins_missing"]),
                         ("https://x.test/p", "catalog", "plugin_refused", ["ccc"]))
        st, _skew = AU.clean_status({"plugin_via_url": "x" * 201})
        self.assertIsNone(st.get("plugin_via_url"))


def _load_base():
    spec = importlib.util.spec_from_file_location("au74e8b", HERE / "test-auto-update-74e8b-1023.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load_base()


class Wired(Helpers, M.Base):
    """check() 接上目錄這條:狀態、status、doctor、紀錄寫實際來源;目錄這步出錯不擋核心更新。"""

    def setUp(self):
        super().setUp()
        self.srv = Catalog()
        self.addCleanup(self.srv.close)
        self.state = Path(self.env["EZ2_AIB_STATE_ROOT"])
        self.state.mkdir(parents=True, exist_ok=True)
        self.env["EZ2_PAGE_API_BASE"] = os.environ["EZ2_PAGE_API_BASE"] = self.srv.url + "/"
        self.base = self.srv.url
        self.git_calls = []

    def check(self, step, **kw):
        kw.setdefault("catalog", AU.catalog_behind)
        return self.run_check(now=M.NOW + 7200 * step, plugin_fetch=lambda e: self.git_calls.append(1), **kw)

    def text(self):
        return "\n".join(AU.status_lines(AU.status_payload(self.env, platform="linux")))

    def test_c7_status_doctor_log_show_source(self):
        self.installed("demo-paid")
        self.installed("gone-paid")
        self.publish([entry("demo-paid", "1.0.3")])
        self.make_repos(latest=None)
        res = self.check(0)
        self.assertEqual(self.git_calls, [], "目錄拿得到就不碰 git")
        self.assertEqual((res["plugin_via"], res["plugin_via_url"], res["plugin_source"], res["plugins_manual_behind"],
                          res["plugins_missing"]), ("catalog", self.base, "ok", ["demo-paid"], ["gone-paid"]), res)
        self.assertEqual(self.version_of("demo-paid"), "1.0.0")
        p = AU.status_payload(self.env, platform="linux")
        self.assertEqual((p["plugin_via"], p["plugin_via_url"], p["plugins_missing"]), ("catalog", self.base, ["gone-paid"]))
        text = self.text()
        for want in (self.base, "aib plugins install", "gone-paid"):
            self.assertIn(want, text)
        note, line = AU.doctor_line(self.env)
        self.assertEqual(note, "note")
        self.assertIn(self.base, line)
        self.assertIn("via=catalog url=%s" % self.base, AU.log_path(self.env).read_text(encoding="utf-8"))

        self.srv.routes["/plugins/catalog"] = (200, b'{"ok": true, "v": 1, "plugins": [{"name": "../x"}]}')
        res = self.check(1)
        self.assertEqual(self.git_calls, [], "目錄驗證沒過不退回 git")
        self.assertEqual((res["plugin_source"], res["plugin_problem"], res["plugins_manual_behind"]),
                         ("rejected", "plugin_catalog_invalid", []), res)
        note, line = AU.doctor_line(self.env)
        self.assertEqual(note, "note")
        self.assertIn("plugin_catalog_invalid", line)
        self.assertIn("plugin_catalog_invalid", self.text())

        self.srv.routes["/plugins/catalog"] = (403, b'{"error": "nope"}')
        res = self.check(2)
        self.assertEqual(self.git_calls, [], "被拒不退回 git")
        self.assertEqual((res["plugin_source"], res["plugin_problem"]), ("incomplete", "plugin_refused"), res)
        self.assertIn("付費外掛這次沒查完(plugin_refused)", self.text())
        note, line = AU.doctor_line(self.env)
        self.assertEqual(note, "note")
        self.assertIn("plugin_refused", line)

        self.srv.routes["/plugins/catalog"] = (404, b"")
        res = self.check(3)
        self.assertEqual(self.git_calls, [1], "端點沒有 ⇒ 退回 git")
        self.assertEqual((res["plugin_via"], res["plugin_source"], res["plugin_problem"]), ("git", "offline", "plugin_unsupported"))
        self.assertEqual(res["plugin_via_url"], AU.safe_url(AU.PLUGIN_SOURCE))

    def test_c7c_catalog_error_core_still_updates(self):
        """目錄這步拋例外 / 已裝版號不是 x.y.z ⇒ 核心照樣更新,付費這輪記 plugin_catalog_error 或跳過(#349 r1 H1)。"""
        self.installed("odd-paid", "1.0.2-beta")
        self.publish([entry("odd-paid", "1.0.3")])
        self.make_repos(latest="0.1.2")
        res = self.check(0)
        self.assertEqual((res["result"], res["plugin_source"], res["plugins_manual_behind"]), ("updated", "ok", []), res)

    def test_c7d_catalog_raises(self):
        self.installed("demo-paid")
        self.make_repos(latest="0.1.2")

        def boom(env, excluded):
            raise ValueError("x")
        try:
            res = self.check(0, catalog=boom)
        except Exception as exc:  # noqa: BLE001
            self.fail("目錄這步出錯讓 check() 整個掛了(核心沒更新):%r" % exc)
        self.assertEqual((res["result"], res["plugin_problem"], res["plugin_source"]),
                         ("updated", "plugin_catalog_error", "incomplete"), res)
        self.assertEqual(self.git_calls, [], "出錯不退回 git")
        self.assertIn("plugin_catalog_error", self.text())
        self.assertNotIn(AU.T("status.plugin_via_catalog", url="?"), self.text(), "沒網址就不印「查的是目錄:?」(#349 r2 L2)")
        self.assertIsNone(res["plugin_via_url"])

    def test_c7e_only_missing_is_a_note(self):
        """只有「已裝、目錄沒列」⇒ doctor 也要提醒(note),不是 ok(#349 r2 L3)。"""
        self.installed("gone-paid")
        self.publish([])
        self.make_repos(latest=None)
        res = self.check(0)
        self.assertEqual((res["plugins_manual_behind"], res["plugins_missing"]), ([], ["gone-paid"]), res)
        note, line = AU.doctor_line(self.env)
        self.assertEqual(note, "note", line)
        self.assertIn("gone-paid", line)
        self.assertIn("gone-paid", self.text())

    def test_c7f_old_state_key_still_read(self):
        """0.1.59 前的狀態檔寫的是 plugins_paid_behind ⇒ 升級後 status / doctor 照讀(鍵名 0.1.60 起 plugins_manual_behind)。"""
        AU.write_status({"plugins_paid_behind": ["old-paid"]}, self.env)
        p = AU.status_payload(self.env, platform="linux")
        self.assertEqual(p["plugins_manual_behind"], ["old-paid"], p)
        self.assertIn("old-paid", self.text())
        note, line = AU.doctor_line(self.env)
        self.assertEqual((note, "old-paid" in line), ("note", True), line)


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, case, rel="src/cockpit/auto_update.py"):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl, tests=(name, "test-auto-update-74e8b-1023.py")) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k1_every_code_falls_back(self):
        self._knife('via="fallback" if code in CATALOG_FALLBACK else "catalog"', 'via="fallback"',
                    "Route.test_c3_other_codes_stop")

    def test_k2_no_version_filter(self):
        self._knife("if isinstance(v, str) and PC.VER_RE.fullmatch(v):", "if isinstance(v, str):", "Route.test_c8_mixed")

    def test_k3_check_unguarded(self):
        self._knife("cat, cat_url = _catalog_safe(catalog, env, excluded)",
                    "cat, cat_url = catalog(env, excluded), catalog_url(env)", "Wired.test_c7d_catalog_raises")


if __name__ == "__main__":
    unittest.main()
