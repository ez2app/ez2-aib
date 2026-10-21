#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib seat remote status --json 的 remote_ready / remote_why(@74e8b 10-21 聯動表第 1 項;伺服器 ready 路徑等 @9f808 定案)。
網路、本機連線全換假的;EZ2_AIB_STATE_ROOT 指暫存。

R1 沒綁 ⇒ remote_why not_bound、不打伺服器(server_ready None)、帶 hint
R2 綁了、連線沒在跑 ⇒ link_stopped、不打伺服器
R3 綁了 + 連線在跑 + 伺服器 ready ⇒ remote_ready true、remote_why None、沒 hint
R4 伺服器 404 / 500 / ready:false / 回應不是 JSON ⇒ server_not_ready;連不上 ⇒ server_unreachable
R5 只讀:status 跑完暫存目錄一個檔都沒多(不建 machine.json、不建 pid 檔)
R6 CLI aib seat remote status --json 帶這幾個欄位;四語都有 hint
刀(複本):K1 拿掉「綁了沒」那一關 ⇒ R1 紅
"""
import io
import json
import os
import sys
import tempfile
import types
import unittest
import urllib.error
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


class Resp:
    def __init__(self, status, body):
        self.status, self.body = status, body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.body


class Opener:
    def __init__(self, result):
        self.result, self.calls = result, []

    def open(self, req, timeout=None):
        self.calls.append((req.full_url, req.get_method(), req.get_header("Authorization"), timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


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
        def server_ready():
            self.server_calls.append(1)
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
        self.assertEqual(self.server_calls, [1])
        res = self.status(running=True, ready=(False, "server_not_ready"))
        self.assertEqual([res[k] for k in KEYS], [False, "server_not_ready", True, False])

    def test_r4_server_ready_parsing(self):
        cases = [
            (Resp(200, b'{"ready": true}'), (True, None)),
            (Resp(200, b'{"ready": "yes"}'), (False, "server_not_ready")),
            (Resp(200, b'{"ready": false}'), (False, "server_not_ready")),
            (Resp(200, b"<html>"), (False, "server_not_ready")),
            (urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b"")), (False, "server_not_ready")),
            (urllib.error.HTTPError("u", 500, "x", {}, io.BytesIO(b"")), (False, "server_not_ready")),
            (urllib.error.URLError("down"), (False, "server_unreachable")),
            (OSError("reset"), (False, "server_unreachable")),
        ]
        for result, want in cases:
            op = Opener(result)
            self.assertEqual(RL.server_ready(base="http://page.test", opener=op), want, result)
            url, method, auth, timeout = op.calls[0]
            self.assertEqual((url, method, auth), ("http://page.test" + RL.READY_PATH, "GET", None), "公開那支、不帶憑證")
            self.assertLessEqual(timeout, 5)

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
