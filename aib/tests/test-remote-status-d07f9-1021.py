#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib seat remote status --json 的 remote_ready / remote_why(@74e8b 10-21 聯動表第 1 項;伺服器照 @abb89 裁示 L1 讀公開功能清單 GET /api/v1/page/features 的 features.remote)。
網路、本機連線全換假的;EZ2_AIB_STATE_ROOT 指暫存。

R1 沒綁 ⇒ remote_why not_bound、不打伺服器(server_ready None)、帶 hint
R2 綁了、連線沒在跑 ⇒ link_stopped、不打伺服器
R3 綁了 + 連線在跑 + 伺服器 ready ⇒ remote_ready true、remote_why None、沒 hint
R4 伺服器 404 / 500 / features.remote 不是 true / 格式不對 / 回應不是 JSON ⇒ server_not_ready;連不上 ⇒ server_unreachable
R5 只讀:status 跑完暫存目錄一個檔都沒多(不建 machine.json、不建 pid 檔;唯一會寫的是 R7 的功能清單快取)
R6 CLI aib seat remote status --json 帶這幾個欄位;四語都有 hint
R7 fetch_features 快取(doctor 共用):60 秒內吃快取、滿 60 秒重問;200 / 404 快取，連不上 / 5xx 不快取;檔 600、只存 at/status/features;壞檔 / 未來時間當沒有
刀(複本):K1 拿掉「綁了沒」那一關 ⇒ R1 紅
"""
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
for _p in (COCKPIT / "page", COCKPIT, HERE):
    sys.path.insert(0, str(_p))
import remote_link as RL  # noqa: E402
import seat_act as SA  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

MID = "6b1f3c2e-8f0a-4d5b-9c1e-2a3b4c5d6e7f"
KEYS = ("remote_ready", "remote_why", "link_running", "server_ready")


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-rstatus-")
        self.addCleanup(td.cleanup)
        self.state = Path(td.name) / "state"
        self.state.mkdir()
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.state), "HOME": td.name, "EZ2_AIB_LOCALE": "zh-TW",
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        self.server_calls = []

    def bind(self):
        d = self.state / "auth" / "page"
        d.mkdir(parents=True, exist_ok=True)
        (d / "machine.json").write_text(json.dumps({"id": MID, "name": "mac"}))
        (d / "t_ab12.p1.json").write_text(json.dumps({"tenant_id": "t_ab12", "page_id": "p1", "machine_id": MID,
                                                       "assistant_token": "tok-x"}))

    def ctl(self, running=False, ready=(True, None)):
        def server_ready(env=None):
            self.server_calls.append(env)
            return ready
        return types.SimpleNamespace(running=lambda env: 4242 if running else None, server_ready=server_ready,
                                     start_bg=lambda env: (None, "x"), stop_bg=lambda env: None)

    def status(self, **kw):
        res, rc = SA.do_remote("status", env=self.env, link_ctl=self.ctl(**kw))
        self.assertEqual(rc, 0, res)
        return res


class Status(Base):
    def test_r1_not_bound(self):
        res = self.status(running=True)
        self.assertEqual([res[k] for k in KEYS], [False, "not_bound", True, None])
        self.assertEqual(self.server_calls, [], "沒綁不打伺服器")
        self.assertIn("aib page bind", res["hint"])

    def test_r2_link_stopped(self):
        self.bind()
        res = self.status(running=False)
        self.assertEqual([res[k] for k in KEYS], [False, "link_stopped", False, None])
        self.assertEqual(self.server_calls, [])
        self.assertTrue(res["hint"])

    def test_r3_ready(self):
        self.bind()
        res = self.status(running=True)
        self.assertEqual([res[k] for k in KEYS], [True, None, True, True])
        self.assertNotIn("hint", res)
        self.assertEqual(self.server_calls, [self.env], "狀態根照傳(快取放在這台的狀態根底下)")
        res = self.status(running=True, ready=(False, "server_not_ready"))
        self.assertEqual([res[k] for k in KEYS], [False, "server_not_ready", True, False])

    def call(self, status, body=None):
        calls = []

        def call(method, url, token, body_=None, timeout=None):
            calls.append((method, url, token, timeout))
            return status, body
        call.calls = calls
        return call

    def test_r4_server_ready_parsing(self):
        good = {"features": {"remote": True, "quote": False, "unbind": False}}
        cases = [
            ((200, good), (True, None)),
            ((200, {"features": {"remote": "yes"}}), (False, "server_not_ready")),
            ((200, {"features": {"remote": False, "quote": True}}), (False, "server_not_ready")),
            ((200, {"features": {}}), (False, "server_not_ready")),
            ((200, {"features": [True]}), (False, "server_not_ready")),
            ((200, {"remote": True}), (False, "server_not_ready")),
            ((200, None), (False, "server_not_ready")),
            ((404, None), (False, "server_not_ready")),
            ((500, None), (False, "server_not_ready")),
            ((0, None), (False, "server_unreachable")),
        ]
        for n, ((status, body), want) in enumerate(cases):
            c = self.call(status, body)
            self.assertEqual(RL.server_ready(self.env, call=c, now=1000.0 + n * 100, base="http://page.test"), want, (status, body))
            method, url, token, timeout = c.calls[0]
            self.assertEqual((method, url, token), ("GET", "http://page.test" + RL.FEATURES_PATH, None), "公開那支、不帶憑證")
            self.assertLessEqual(timeout, 5)
        feats, why = RL.fetch_features(self.env, call=self.call(200, {"features": {"remote": True, "quote": 1, "unbind": False}}),
                                       now=99999.0)
        self.assertEqual((feats, why), ({"remote": True, "unbind": False}, None), "只留 bool")

    def test_r7_shared_cache(self):
        path = RL.features_cache_path(self.env)
        self.assertTrue(str(path).startswith(str(self.state)), "快取在明寫的狀態根底下")
        c = self.call(200, {"features": {"remote": True, "quote": False}})
        self.assertEqual(RL.fetch_features(self.env, call=c, now=1000.0), ({"remote": True, "quote": False}, None))
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(sorted(json.loads(path.read_text())), ["at", "features", "status"], "只存 at / status / features")
        c2 = self.call(200, {"features": {"remote": False}})
        self.assertEqual(RL.server_ready(self.env, call=c2, now=1059.0), (True, None), "60 秒內吃快取")
        self.assertEqual(c2.calls, [])
        self.assertEqual(RL.server_ready(self.env, call=c2, now=1060.0), (False, "server_not_ready"), "滿 60 秒重問")
        self.assertEqual(len(c2.calls), 1)
        c404 = self.call(404)
        self.assertEqual(RL.fetch_features(self.env, call=c404, now=2000.0), (None, "server_not_ready"))
        self.assertEqual(RL.fetch_features(self.env, call=self.call(200, {"features": {"remote": True}}), now=2030.0),
                         (None, "server_not_ready"), "404 也快取")
        down = self.call(0)
        self.assertEqual(RL.fetch_features(self.env, call=down, now=3000.0), (None, "server_unreachable"))
        self.assertEqual(json.loads(path.read_text())["at"], 2000.0, "連不上不寫快取")
        c500 = self.call(500)
        RL.fetch_features(self.env, call=c500, now=3001.0)
        again = self.call(200, {"features": {"remote": True}})
        self.assertEqual(RL.fetch_features(self.env, call=again, now=3002.0), ({"remote": True}, None), "5xx 不快取")
        path.write_text("{bad")
        self.assertEqual(RL.fetch_features(self.env, call=self.call(404), now=4000.0), (None, "server_not_ready"), "壞快取當沒有")
        path.write_text(json.dumps({"at": 5000.0, "status": 200, "features": {"remote": True}}))
        self.assertEqual(RL.fetch_features(self.env, call=self.call(0), now=4990.0), (None, "server_unreachable"), "未來時間的快取不算")

    def test_r5_read_only(self):
        def snap():
            return sorted(str(p.relative_to(self.state)) for p in self.state.rglob("*"))
        before = snap()
        self.status(running=True)
        self.assertEqual(snap(), before, "沒綁時 status 不建任何檔")
        self.bind()
        before = snap()
        self.status(running=True)
        self.status(running=False)
        self.assertEqual(snap(), before)

    def test_r6_cli_and_i18n(self):
        out = io.StringIO()
        old = dict(os.environ)
        os.environ.update(self.env)
        try:
            rc = SA.main(["remote", "status", "--json"], out=out)
        finally:
            os.environ.clear()
            os.environ.update(old)
        res = json.loads(out.getvalue())
        self.assertEqual(rc, 0)
        for k in KEYS:
            self.assertIn(k, res)
        self.assertEqual(res["remote_why"], "not_bound")
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            for why in SA.REMOTE_WHYS:
                self.assertTrue(d.get("aib.seat.panel.remote_why_" + why), (lang, why))


class Knife(unittest.TestCase):
    def test_k1_skip_bound(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/seat_act.py", '    if not bound:\n        out["remote_why"] = "not_bound"\n    elif',
                        "    if", tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Status.test_r1_not_bound"], cwd=str(root),
                            max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
