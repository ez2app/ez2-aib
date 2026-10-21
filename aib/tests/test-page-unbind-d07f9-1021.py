#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib page unbind(@abb89 10-21 派工 0.1.38)。伺服器、Touch ID 全換假的;EZ2_AIB_STATE_ROOT 指暫存,不碰真憑證。

U1 本人過 + 伺服器撤銷成功 ⇒ 本機憑證刪掉、回 unbound / server=revoked;Touch ID 視窗字含帳號名(清過)
U2 Touch ID 沒過(cancel / off / unavailable)⇒ 伺服器沒被叫、本機沒動、rc 2、帶 hint
U3 伺服器拒(別的碼)⇒ 本機沒動、rc 1、detail = 伺服器原碼(不換人話)、帶 manage_url;連不上 ⇒ detail transport
U4 伺服器說早就撤銷 / 過期 / 不認得這把 ⇒ 當成功、清本機、server=already;不認得的多一行 hint
U5 原子刪除:改名後還沒刪就斷 ⇒ 憑證已不算綁(list_tokens 看不到);重跑先清掉殘留
U6 沒綁 ⇒ not_bound rc 0、不跳 Touch ID;CLI --json 給艦長室卡片;四語都有
U7 伺服器還沒有撤銷這支(404 / 405)⇒ detail page_unbind_unsupported、本機沒動、hint 指後台
U8 契約:POST /bind/revoke、body 空、帶 bearer;新 hint 四語都有
刀(複本):K1 先刪本機再叫伺服器 ⇒ U3 紅;K2 不等 Touch ID 就撤銷 ⇒ U2 紅
"""
import io
import json
import os
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
from api import ApiError, TransportError  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

REC = {"assistant_token": "ast_test_xyz", "tenant_id": "tenant_ab12", "page_id": "page_cd34", "slug": "andy\u202eevil"}


class FakeApi:
    def __init__(self, err=None):
        self.err = err
        self.calls = 0

    def bind_revoke(self):
        self.calls += 1
        if self.err:
            raise self.err
        return {"revoked": True}


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-unbind-")
        self.addCleanup(td.cleanup)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "EZ2_AIB_LOCALE": "zh-TW"}
        self.reasons = []
        store.save_token(dict(REC), self.env)

    def prove(self, why=None):
        def p(reason, env):
            self.reasons.append(reason)
            return "touchid", why
        return p

    def path(self):
        return Path(store.token_path(REC["tenant_id"], REC["page_id"], self.env)[0])

    def run_unbind(self, api=None, why=None, record=REC):
        return UB.unbind(dict(record) if record else None, api or FakeApi(), self.env, prove=self.prove(why))


class Unbind(Base):
    def test_u1_ok(self):
        api = FakeApi()
        res, rc = self.run_unbind(api)
        self.assertEqual(rc, 0, res)
        self.assertEqual((res["state"], res["server"], res["local"]), ("unbound", "revoked", "removed"))
        self.assertEqual(api.calls, 1)
        self.assertFalse(self.path().exists())
        self.assertEqual(store.list_tokens(self.env), [])
        self.assertIn("andy", self.reasons[0])
        self.assertNotIn("\u202e", self.reasons[0], "視窗字清過方向字元")

    def test_u2_touchid_refused(self):
        for why in ("touchid_cancel", "touchid_off", "touchid_unavailable"):
            api = FakeApi()
            res, rc = self.run_unbind(api, why=why)
            self.assertEqual((rc, res["why"], api.calls), (2, why, 0), why)
            self.assertTrue(res.get("hint"), why)
            self.assertTrue(self.path().exists(), why)

    def test_u3_server_fails_keep_local(self):
        res, rc = self.run_unbind(FakeApi(ApiError(403, "page_api_forbidden")))
        self.assertEqual((rc, res["why"], res["detail"]), (1, "server_refused", "page_api_forbidden"))
        self.assertEqual(res["manage_url"], UB.MANAGE_URL)
        self.assertTrue(self.path().exists())
        res, rc = self.run_unbind(FakeApi(TransportError("down")))
        self.assertEqual((rc, res["why"], res["detail"]), (1, "server_unreachable", "transport"))
        self.assertTrue(self.path().exists())
        self.assertTrue(res.get("hint"))

    def test_u7_server_unsupported(self):
        for err in (ApiError(404, "page_api_not_found"), ApiError(405, ""), ApiError(404, "")):
            res, rc = self.run_unbind(FakeApi(err))
            self.assertEqual((rc, res["why"], res["detail"]), (1, "server_unsupported", "page_unbind_unsupported"))
            self.assertTrue(self.path().exists())
            self.assertIn("ez2.page/manage/aib", res["hint"])

    def test_u4_already_revoked(self):
        for code in ("page_api_token_revoked", "page_api_token_expired", "page_api_token_invalid"):
            store.save_token(dict(REC), self.env)
            res, rc = self.run_unbind(FakeApi(ApiError(401, code)))
            self.assertEqual((rc, res["server"], res["local"], res["detail"]), (0, "already", "removed", code))
            self.assertFalse(self.path().exists())
            self.assertEqual(bool(res.get("hint")), code == "page_api_token_invalid", code)

    def test_u8_contract_empty_body(self):
        import api as A
        seen = []
        a = A.PageApi.__new__(A.PageApi)
        a._request = lambda method, path, body=None, auth=True: seen.append((method, path, body, auth)) or {"ok": True}
        a.bind_revoke()
        self.assertEqual(seen, [("POST", "/bind/revoke", None, True)], "body 要空:帶 {} 伺服器回 400")
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            for k in ("aib.page.unbind_hint.server_unsupported", "aib.page.unbind_note.page_api_token_invalid"):
                self.assertIn(k, d, (lang, k))

    def test_u5_atomic_leftover(self):
        orig = os.unlink
        state = {"n": 0}

        def boom(p, *a, **k):
            if Path(p).name.startswith(UB.LEFTOVER_PREFIX) and state["n"] == 0:
                state["n"] += 1
                raise KeyboardInterrupt("斷電")
            return orig(p, *a, **k)
        UB.os.unlink = boom
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.run_unbind()
        finally:
            UB.os.unlink = orig
        self.assertFalse(self.path().exists())
        self.assertEqual(store.list_tokens(self.env), [], "改名後就不算綁")
        left = list(self.path().parent.glob(UB.LEFTOVER_PREFIX + "*"))
        self.assertEqual(len(left), 1)
        res, rc = self.run_unbind(record=None)
        self.assertEqual((rc, res["state"], res["swept"]), (0, "not_bound", 1))
        self.assertEqual(list(self.path().parent.glob(UB.LEFTOVER_PREFIX + "*")), [])

    def test_u6_not_bound_and_cli(self):
        self.path().unlink()
        res, rc = self.run_unbind(record=None)
        self.assertEqual((rc, res["state"]), (0, "not_bound"))
        self.assertEqual(self.reasons, [], "沒綁不跳 Touch ID")
        import cli
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["unbind", "--json"], environ=self.env)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue())["state"], "not_bound")
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            for k in ("aib.page.help.unbind", "aib.page.touchid_unbind", "aib.page.unbind_unbound",
                      "aib.page.unbind_not_bound", "aib.page.unbind_hint.server_refused",
                      "aib.page.unbind_hint.server_unreachable", "aib.page.unbind_hint.touchid_cancel"):
                self.assertIn(k, d, (lang, k))


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, case):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/page/unbind.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_local_first(self):
        self._knife('    server, note = "revoked", None\n', '    server, note = "revoked", None\n    remove_local(record, e)\n',
                    "Unbind.test_u3_server_fails_keep_local")

    def test_k2_no_wait_touchid(self):
        self._knife("    if why:\n        return _fail(why, 2, state=\"bound\", slug=slug)\n", "",
                    "Unbind.test_u2_touchid_refused")


if __name__ == "__main__":
    unittest.main()
