#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib doctor 列 ez2.page 開了哪些功能(Andy 10-21 夜第 4 件;@abb89 裁 L1:伺服器只有 GET /api/v1/page/features;
抓取 + 60 秒快取跟 aib seat remote status 共用 remote_link.fetch_features)。網路全換假的,狀態根指暫存。

F1 200 + {features:{名稱:bool}} ⇒ OK,列出已開 / 還沒開
F2 404 / 500 / 連不上 / 格式不對 ⇒ NOTE(不是 FAIL、不影響 doctor 結束碼)
F3 伺服器亂回的名稱(大寫、方向字元)不印出來
F4 共用快取:doctor 抓過、60 秒內 remote_link 不再打伺服器;快取寫在 doctor 指定的庫
F5 AIB_DOCTOR_NO_NET=1 ⇒ 不連網路;run_all 有接上;四語都有
F6 doctor 指的庫沒綁 ez2.page ⇒ NOTE、一次網路都不打(其他 doctor 測試 / CI 不打外網)
刀(複本):K1 伺服器還沒開當 FAIL ⇒ F2 紅;K2 拿掉沒綁就略過 ⇒ F6 紅
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (REPO / "src" / "bootstrap", REPO / "src" / "cockpit", HERE):
    sys.path.insert(0, str(_p))
import aib_doctor as D  # noqa: E402
import remote_link as RL  # noqa: E402
sys.path.insert(0, str(REPO / "src" / "cockpit" / "page"))
import store  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

T0 = 1_800_000_000.0
REC = {"assistant_token": "ast_test_xyz", "tenant_id": "tenant_ab12", "page_id": "page_cd34", "slug": "andy"}


def fake(status, body=None):
    calls = []

    def call(method, url, token, body_=None, timeout=None):
        calls.append(url)
        return status, body
    call.calls = calls
    return call


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-docfeat-")
        self.addCleanup(td.cleanup)
        self.root = Path(td.name) / "state"
        self.env = {"EZ2_AIB_STATE_ROOT": str(Path(td.name) / "other"), "HOME": td.name, "EZ2_AIB_LOCALE": "zh-TW"}
        store.save_token(dict(REC), dict(self.env, EZ2_AIB_STATE_ROOT=str(self.root)))

    def check(self, call, now=T0, env=None):
        return D.check_page_features(self.root, REPO, env=env or self.env, call=call, now=now)


class Features(Base):
    def test_f1_ok(self):
        r = self.check(fake(200, {"features": {"page_unbind": False, "quote_key": True, "remote": True}}))
        self.assertEqual(r["status"], D.OK, r)
        self.assertLess(r["detail"].index("quote_key"), r["detail"].index("page_unbind"), "已開在前、還沒開在後")

    def test_f2_not_ready(self):
        for i, (status, body) in enumerate(((404, None), (500, None), (0, None), (200, {"features": []}),
                                            (200, {"nope": 1}), (200, None))):
            r = self.check(fake(status, body), now=T0 + 1000 * i)
            self.assertEqual(r["status"], D.NOTE, (status, body, r))

    def test_f3_weird_names_hidden(self):
        r = self.check(fake(200, {"features": {"Evil\u202eName": True, "ok_one": True}}))
        self.assertEqual(r["status"], D.OK, r)
        self.assertIn("ok_one", r["detail"])
        self.assertNotIn("\u202e", r["detail"])
        self.assertNotIn("Evil", r["detail"])

    def test_f4_shared_cache(self):
        first = fake(200, {"features": {"remote": True}})
        self.check(first)
        self.assertEqual(len(first.calls), 1)
        self.assertTrue(first.calls[0].endswith("/api/v1/page/features"), first.calls)
        env = dict(self.env, EZ2_AIB_STATE_ROOT=str(self.root))
        self.assertTrue(RL.features_cache_path(env).exists(), "快取寫在 doctor 指定的庫")
        self.assertFalse(RL.features_cache_path(self.env).exists(), "沒寫到環境變數那一份")
        again = fake(500)
        self.assertEqual(RL.server_ready(env, call=again, now=T0 + 30), (True, None))
        self.assertEqual(again.calls, [], "60 秒內共用快取")

    def test_f6_unbound_no_network(self):
        with tempfile.TemporaryDirectory() as td:
            def boom(*a, **k):
                raise AssertionError("沒綁還打網路")
            env = dict(self.env, EZ2_AIB_STATE_ROOT=str(self.root))       # 環境變數那份有憑證,doctor 指的庫沒有
            r = D.check_page_features(Path(td), REPO, env=env, call=boom, now=T0)
            self.assertEqual(r["status"], D.NOTE, r)
            self.assertIn("沒綁", r["detail"])

    def test_f5_offline_wired(self):
        call = fake(200, {"features": {"x": True}})
        r = self.check(call, env=dict(self.env, AIB_DOCTOR_NO_NET="1"))
        self.assertEqual((r["status"], call.calls), (D.NOTE, []))
        src = (REPO / "src" / "bootstrap" / "aib_doctor.py").read_text(encoding="utf-8")
        self.assertIn("checks.append(check_page_features(state_root, repo_root))", src)
        self.assertIn("RL.fetch_features(", src)
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            for k in ("skipped", "not_bound", "unreachable", "no_endpoint", "ok"):
                self.assertIn("aib.doctor.check.page_features." + k, d, (lang, k))


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, case):
        name = Path(__file__).name
        with knife_copy(REPO, "src/bootstrap/aib_doctor.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_not_ready_as_fail(self):
        self._knife('        return _result("page_features", NOTE,\n                       _C("page_features.unreachable" if',
                    '        return _result("page_features", FAIL,\n                       _C("page_features.unreachable" if',
                    "Features.test_f2_not_ready")

    def test_k2_query_when_unbound(self):
        self._knife("        if not store.list_tokens(env):", "        if False:", "Features.test_f6_unbound_no_network")


if __name__ == "__main__":
    unittest.main()
