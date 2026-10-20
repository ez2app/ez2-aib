#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""手機遠端指揮的本機連線(aib seat remote link,10-21 維護者:「手機版、電腦版能直接指揮 aib」)。

(GUIDE 檢查表:防線仍是 remote_grant 的伺服器簽章;這支只轉送，伺服器回的是資料 ⇒ 只認四種 op)
L1 假中繼一輪:unlock(帶單子)→ input → stop → unlocked 全過、Mac 上一次 Touch ID 都沒彈、每則都回條(id 對上)
L6 transcript:視窗開著才給、遮敏感字;沒開 ⇒ locked
L2 視窗沒開:input / stop 回 locked 不彈 Touch ID;unlock 沒帶單子 ⇒ grant-required;怪 op / 怪席碼 ⇒ 拒
L3 總開關關 ⇒ 不連;跑到一半關掉 ⇒ 停;401 ⇒ unauthorized 停;連不上 ⇒ 退避
L4 真的 HTTP(本機假伺服器):Bearer 在標頭、不在網址;204 = 沒事;回應太大 ⇒ 當沒收到
L5 target():沒綁 ⇒ not-bound;綁到這台 ⇒ 拿到憑證與 machine
刀(複本):K1 do_input 拿掉 window_only ⇒ L2 紅
"""
import base64
import http.server
import importlib.util
import json
import os
import sys
import threading
import time
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
from entitlement import ed25519, signing  # noqa: E402

_spec = importlib.util.spec_from_file_location("seat_act_base_1020", HERE / "test-seat-act-d07f9-1020.py")
BASE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BASE)
CODE, PROMPT = BASE.CODE, BASE.PROMPT

SEED = bytes(range(32))
KEYS = {"t1": "ed25519:" + ed25519.public_key(SEED).hex()}
MID, ACCT = "6f1c1d1e-0000-4000-8000-000000000001", "t_lfu0327"
FP = __import__("hashlib").sha256(("ez2-aib-machine-v1:" + MID).encode()).hexdigest()


def grant(**kw):
    now = int(time.time())
    g = {"v": 1, "kind": "seat-unlock", "kid": "t1", "machine": FP, "seat": CODE, "minutes": 10, "nonce": base64.urlsafe_b64encode(os.urandom(18)).decode(),
         "iat": now, "exp": now + 120, "account_id": ACCT, "auth": "passkey"}
    g.update(kw)
    return {"grant": g, "sig": base64.urlsafe_b64encode(ed25519.sign(SEED, signing.canonical(g))).decode().rstrip("=")}


class Relay:
    def __init__(self, *msgs, statuses=None):
        self.q, self.replies, self.calls, self.statuses = list(msgs), [], [], list(statuses or [])

    def __call__(self, method, url, token, body=None):
        self.calls.append((method, url, token))
        if method == "POST":
            self.replies.append(body)
            return 200, {"ok": True}
        if self.statuses:
            return self.statuses.pop(0), None
        return (200, self.q.pop(0)) if self.q else (204, None)


class Link(BASE.Box):
    def setUp(self):
        super().setUp()
        import remote_grant
        import remote_link
        self.RG, self.RL = remote_grant, remote_link
        self.RG.set_remote(True)
        SA, t = self.SA, self.tid()
        self.fake = types.SimpleNamespace(
            do_unlock=lambda c, m, env=None, grant=None: SA.do_unlock(c, m, env=env, grant=grant, keys=KEYS, machine=(FP, {ACCT}), touchid=t),
            do_input=lambda c, x, env=None, window_only=False: SA.do_input(c, x, env=env, screen=PROMPT, window_only=window_only, touchid=t,
                                                                         touchid_available=lambda: True, platform="darwin"),
            do_stop=lambda c, hard=False, env=None, window_only=False: SA.do_stop(c, hard=hard, env=env, window_only=window_only, touchid=t,
                                                                                touchid_available=lambda: True, platform="darwin"),
            do_unlocked=SA.do_unlocked)

    def run_link(self, relay, n):
        out = None
        for _ in range(n):
            out = self.RL.link("tok-secret", FP, base="http://relay.test", call=relay, once=True, act=self.fake, sleep=lambda s: None)
        return out

    def test_l1_round(self):
        r = Relay({"id": "m-unlock-01", "op": "unlock", "seat": CODE, "grant": grant()},
                  {"id": "m-input-002", "op": "input", "seat": CODE, "text": "跑一下測試"},
                  {"id": "m-stop-0003", "op": "stop", "seat": CODE},
                  {"id": "m-left-0004", "op": "unlocked", "seat": CODE})
        self.run_link(r, 4)
        res = {x["id"]: x["result"] for x in r.replies}
        self.assertEqual(list(res), ["m-unlock-01", "m-input-002", "m-stop-0003", "m-left-0004"])
        self.assertTrue(all(v.get("ok") for v in res.values()), res)
        self.assertEqual((res["m-unlock-01"]["via"], res["m-input-002"]["via"], res["m-stop-0003"]["via"]), ("remote-grant", "unlock-window", "unlock-window"))
        self.assertGreater(res["m-left-0004"]["unlock_left_s"], 590)
        self.assertEqual(self.asked, [], "遠端過來的一次 Touch ID 都不彈")
        self.assertTrue(all(x["machine"] == FP for x in r.replies))
        self.assertIn("跑一下測試", "\n".join(self.keys()))

    def test_l2_locked(self):
        r = Relay({"id": "m-input-001", "op": "input", "seat": CODE, "text": "嗨"},
                  {"id": "m-stop-0002", "op": "stop", "seat": CODE},
                  {"id": "m-unlock-03", "op": "unlock", "seat": CODE},
                  {"id": "m-bad-00004", "op": "rm -rf", "seat": CODE},
                  {"id": "m-bad-00005", "op": "input", "seat": "../x", "text": "嗨"})
        self.run_link(r, 5)
        why = [x["result"].get("why") for x in r.replies]
        self.assertEqual(why, ["locked", "locked", "grant-required", "bad-op", "bad-seat"])
        self.assertEqual(self.asked, [], "視窗沒開也不在 Mac 上彈 Touch ID")
        self.assertEqual(self.keys(), [], "什麼都沒送進席位")

    def test_l6_transcript(self):
        r = Relay({"id": "m-read-0001", "op": "transcript", "seat": CODE})
        self.run_link(r, 1)
        self.assertEqual(r.replies[0]["result"]["why"], "locked", "視窗沒開不給看")
        r = Relay({"id": "m-unlock-02", "op": "unlock", "seat": CODE, "grant": grant()},
                  {"id": "m-read-0003", "op": "transcript", "seat": CODE, "offset": 0})
        self.run_link(r, 2)
        res = r.replies[1]["result"]
        self.assertNotEqual(res.get("why"), "locked", res)
        self.assertIn("events", res, res)

    def test_l3_switch_and_errors(self):
        self.RG.set_remote(False)
        r = Relay()
        out, rc = self.RL.link("tok", MID, base="http://relay.test", call=r, once=True, act=self.fake)
        self.assertEqual((rc, out["why"], r.calls), (3, "remote-off", []))
        self.RG.set_remote(True)
        slept = []
        r = Relay(statuses=[0, 502, 0])
        n = {"i": 0}

        def call(*a, **kw):
            n["i"] += 1
            if n["i"] == 4:
                self.RG.set_remote(False)
            return r(*a, **kw)
        out, rc = self.RL.link("tok", MID, base="http://relay.test", call=call, act=self.fake, sleep=slept.append)
        self.assertEqual((rc, out["why"]), (3, "remote-off"), "跑到一半關掉 ⇒ 停")
        self.assertEqual(slept, [2, 4, 8], "連不上 ⇒ 退避")
        self.RG.set_remote(True)
        r = Relay(statuses=[401])
        out, rc = self.RL.link("tok", MID, base="http://relay.test", call=r, act=self.fake)
        self.assertEqual((rc, out["why"]), (3, "unauthorized"))

    def test_l4_real_http(self):
        seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                seen.append((self.path, self.headers.get("authorization")))
                if "big" in self.path:
                    body = b'{"x":"' + b"y" * (200 * 1024) + b'"}'
                    self.send_response(200)
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(204)
                self.end_headers()
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = "http://127.0.0.1:%d" % srv.server_address[1]
            self.assertEqual(self.RL.http("GET", base + "/api/v1/remote/next?machine=" + MID, "tok-secret", timeout=5), (204, None))
            self.assertEqual(seen[0], ("/api/v1/remote/next?machine=" + MID, "Bearer tok-secret"))
            self.assertNotIn("tok-secret", seen[0][0], "憑證不在網址")
            self.assertEqual(self.RL.http("GET", base + "/big", "t", timeout=5), (200, None), "太大 ⇒ 當沒收到")
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(self.RL.http("GET", "http://127.0.0.1:9/x", "t", timeout=2), (0, None))
        self.assertEqual(self.RL.BASE, "https://ez2.page", "正式網址寫死")

    def test_l5_target(self):
        self.assertEqual(self.RL.target(), (None, "not-bound"))
        d = self.state / "auth" / "page"
        d.mkdir(parents=True)
        (d / "machine.json").write_text(json.dumps({"id": MID, "name": "mac"}))
        (d / "t_lfu0327.p1.json").write_text(json.dumps({"tenant_id": ACCT, "page_id": "p1", "machine_id": MID, "assistant_token": "tok-x"}))
        self.assertEqual(self.RL.target(), (("tok-x", FP), None))
        self.RG.set_remote(False)
        res, rc = self.RL.main_link(once=True, out=open(os.devnull, "w"))
        self.assertEqual((rc, res["why"]), (3, "remote-off"), "關著就不連(測試不碰正式站)")


class Knife(unittest.TestCase):
    def test_k1_no_window_only(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/seat_act.py", '    elif window_only:\n        out["why"] = "locked"\n        return out, 3\n    else:',
                        "    else:", tests=(name, "test-seat-act-d07f9-1020.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Link.test_l2_locked"], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
