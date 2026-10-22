#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""被撤那台印「何時被誰撤」(0.1.42;契約 caller_dead / already_revoked 的 revoked_body,@9f808 10-22 15:33)。網路全換假的。

R1 401 page_api_token_revoked 帶 revoked_at + revoked_by_name ⇒ 「這台在 <本地時間> 被「名字」撤了;不是你?到 <manage_url>」
R2 revoked_by_name null ⇒ 不講被誰
R3 舊伺服器(沒帶 revoked_at)/ 不是 revoked 的碼 ⇒ 空字串;write_error_text 照舊那句
R4 名字是資料:控制 / 隱形字元去掉、截 32 字、「{url}」不會被再展開;manage_url 不是 https://ez2.page/ 開頭 / 帶換行 ⇒ 用常數
R5 四個出口都接上:chat.write_error_text、aib page 總出口(多一行)、unbind 已撤(detail 照舊原碼、hint = 這句)、
   device lost / list 的 caller_dead(hint = 這句;舊伺服器照舊後台那句)
R6 四語都有 by / at 兩鍵、佔位符齊
R7 契約兩處 revoked_body 欄位一樣;notify 通道 line 盡力而為、email 待 a
刀(複本):K1 名字不清 ⇒ R4 紅;K2 不驗 manage_url 前綴 ⇒ R4 紅;K3 總出口不印這句 ⇒ R5 紅
"""
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from contextlib import redirect_stderr
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (REPO / "src" / "cockpit" / "page", REPO / "src" / "cockpit", HERE):
    sys.path.insert(0, str(_p))
import api as A  # noqa: E402
import chat as CH  # noqa: E402
import cli as PC  # noqa: E402
import device_lost as DL  # noqa: E402
import revoked_note as RN  # noqa: E402
import store  # noqa: E402
import unbind as UB  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

C = json.loads((REPO / "contracts" / "page-api.json").read_text(encoding="utf-8"))
REC = {"assistant_token": "ast_revnote_x", "tenant_id": "tenant_ab12", "page_id": "page_cd34", "slug": "andy"}
AT = "2026-10-22T07:30:00Z"          # 台北 15:30
URL = "https://ez2.page/manage/aib"
BY = "這台在 2026-10-22 15:30 被「舊筆電」撤了;不是你?到 " + URL
ANON = "這台在 2026-10-22 15:30 被撤了;不是你?到 " + URL
OLD = "你在 ez2.page 後台解除了這個助理"


def _err(code="page_api_token_revoked", **body):
    return A.ApiError(401, code, body=dict({"error": code}, **body))


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, status, payload):
        self.status, self.payload = status, payload

    def open(self, req, timeout=None):
        raw = json.dumps(self.payload).encode()
        if self.status >= 400:
            raise urllib.error.HTTPError(req.full_url, self.status, "x", {}, io.BytesIO(raw))
        return _Resp(self.status, raw)


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-revnote-")
        self.addCleanup(td.cleanup)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "EZ2_AIB_LOCALE": "zh-TW"}
        saved = {k: os.environ.get(k) for k in ("EZ2_AIB_LOCALE", "TZ", "EZ2_AIB_STATE_ROOT")}
        os.environ.update({"EZ2_AIB_LOCALE": "zh-TW", "TZ": "Asia/Taipei", "EZ2_AIB_STATE_ROOT": td.name})
        time.tzset()

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            time.tzset()
        self.addCleanup(restore)


class Note(Base):
    def test_r1_by_name(self):
        self.assertEqual(RN.note(_err(revoked_at=AT, revoked_by_name="舊筆電", manage_url=URL)), BY)

    def test_r2_anonymous(self):
        self.assertEqual(RN.note(_err(revoked_at=AT, revoked_by_name=None, manage_url=URL)), ANON)
        self.assertEqual(RN.note(_err(revoked_at=AT, revoked_by_name="", manage_url=URL)), ANON)

    def test_r3_old_server_and_other_codes(self):
        self.assertEqual(RN.note(_err()), "")
        self.assertEqual(RN.note(_err(revoked_at="", revoked_by_name="x")), "")
        self.assertEqual(RN.note(_err("page_api_token_expired", revoked_at=AT, revoked_by_name="x")), "")
        self.assertIn(OLD, CH.write_error_text(_err()))

    def test_r4_name_and_url_are_data(self):
        n = RN.note(_err(revoked_at=AT, revoked_by_name="Andy\u202eMBP\n\u200b" + "長" * 50, manage_url=URL))
        for ch in ("\u202e", "\u200b", "\n"):
            self.assertNotIn(ch, n)
        name = n.split("「", 1)[1].split("」", 1)[0]
        self.assertLessEqual(len(name), 32)
        self.assertTrue(name.startswith("AndyMBP"), name)
        self.assertIn("「{url}」", RN.note(_err(revoked_at=AT, revoked_by_name="{url}", manage_url=URL)))
        for bad in ("https://evil.example/manage", "http://ez2.page/manage/aib", URL + "\nfake", "https://ez2.page.evil/x"):
            self.assertTrue(RN.note(_err(revoked_at=AT, revoked_by_name=None, manage_url=bad)).endswith(URL), bad)
        self.assertTrue(RN.note(_err(revoked_at="不是時間\u202e", revoked_by_name=None)).startswith("這台在 不是時間 "))


class Sites(Base):
    BODY = {"revoked_at": AT, "revoked_by_name": "舊筆電", "manage_url": URL}

    def test_r5_write_error_text(self):
        self.assertEqual(CH.write_error_text(_err(**self.BODY)), BY)

    def test_r5_page_main_exit(self):
        def boom(args, env):
            raise _err(**self.BODY)

        class P:
            def parse_args(self, argv):
                import argparse
                return argparse.Namespace(func=boom)
        orig = PC.build_parser
        PC.build_parser = lambda: P()
        self.addCleanup(setattr, PC, "build_parser", orig)
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = PC.main([], environ=self.env)
        lines = buf.getvalue().splitlines()
        self.assertEqual(rc, 1)
        self.assertIn("page_api_token_revoked", lines[0])
        self.assertEqual(lines[1], "aib page: " + BY)
        buf = io.StringIO()
        self.BODY = {}
        with redirect_stderr(buf):
            PC.main([], environ=self.env)
        self.assertEqual(len(buf.getvalue().splitlines()), 1, "舊伺服器不多印")

    def test_r5_unbind(self):
        store.save_token(dict(REC), self.env)
        api = A.PageApi(base_url="https://page.test", token=REC["assistant_token"],
                        opener=FakeOpener(401, dict({"error": "page_api_token_revoked"}, **self.BODY)))
        res, rc = UB.unbind(dict(REC), api, self.env, prove=lambda reason, env: ("touchid", None))
        self.assertEqual((rc, res["server"], res["detail"], res["hint"]), (0, "already", "page_api_token_revoked", BY))
        store.save_token(dict(REC), self.env)
        api = A.PageApi(base_url="https://page.test", token=REC["assistant_token"],
                        opener=FakeOpener(401, {"error": "page_api_token_revoked"}))
        res, rc = UB.unbind(dict(REC), api, self.env, prove=lambda reason, env: ("touchid", None))
        self.assertNotIn("hint", res)

    def test_r5_device_lost(self):
        class Api:
            def __init__(self, body):
                self.body = body

            def machines_list(self):
                raise _err(**self.body)

            def machine_revoke(self, fp):
                raise _err(**self.body)
        step = DL._page_revoke(Api(self.BODY), {"fp": "a" * 64, "this": False})
        self.assertEqual((step["why"], step["detail"], DL._hint(step)), ("page_caller_dead", "page_api_token_revoked", BY))
        old = DL._page_revoke(Api({}), {"fp": "a" * 64, "this": False})
        self.assertNotIn("revoked_note", old)
        self.assertIn("/manage/aib", DL._hint(old))
        res, rc = DL.lost(self.env, page="a" * 8, api=Api(self.BODY), prove=lambda r, e: None, out=io.StringIO())
        self.assertEqual((rc, res["why"], res["hint"]), (2, "page_caller_dead", BY))
        orig = DL.page_api
        DL.page_api = lambda env: (Api(self.BODY), None)
        self.addCleanup(setattr, DL, "page_api", orig)
        orig_chat = DL.chat_devices
        DL.chat_devices = lambda env: ([], None)
        self.addCleanup(setattr, DL, "chat_devices", orig_chat)
        res = DL.list_all(self.env)
        self.assertEqual(res["page"]["hint"], BY)
        out = io.StringIO()
        DL._print_list(res, out)
        self.assertIn("  " + BY, out.getvalue().splitlines())


class Wording(unittest.TestCase):
    def test_r6_four_locales(self):
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for key, need in (("by", ("{at}", "{name}", "{url}")), ("at", ("{at}", "{url}"))):
                text = d.get("aib.page.revoked_note." + key, "")
                for ph in need:
                    self.assertIn(ph, text, (loc, key))
            self.assertNotIn("{name}", d["aib.page.revoked_note.at"], loc)

    def test_r7_contract(self):
        E = C["endpoints"]
        a = E["bind_revoke"]["already_revoked"]["revoked_body"]
        b = E["machine_revoke"]["caller_dead"]["revoked_body"]
        self.assertEqual(a, b)
        self.assertEqual(a["fields"], ["revoked_at", "revoked_by_name", "manage_url"])
        self.assertEqual((a["code"], a["manage_url"]), ("page_api_token_revoked", RN.MANAGE_URL))
        self.assertTrue(RN.MANAGE_URL.startswith(RN.URL_PREFIX))
        n = E["machine_revoke"]["notify"]
        self.assertEqual(set(n["channels"]), {"email", "line"})
        self.assertEqual(n["channels_status"], {"line": "盡力而為", "email": "待 a(Andy 待辦)"})


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, repl, case):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_name_not_cleaned(self):
        self._knife("src/cockpit/page/revoked_note.py", 'name = _clean(name, NAME_MAX) if isinstance(name, str) else ""',
                    'name = name if isinstance(name, str) else ""', "Note.test_r4_name_and_url_are_data")

    def test_k2_url_prefix_unchecked(self):
        self._knife("src/cockpit/page/revoked_note.py", "url.startswith(URL_PREFIX) and ", "",
                    "Note.test_r4_name_and_url_are_data")

    def test_k3_main_exit_silent(self):
        self._knife("src/cockpit/page/cli.py", "        if note:\n            print(", "        if False:\n            print(",
                    "Sites.test_r5_page_main_exit")


if __name__ == "__main__":
    unittest.main()
