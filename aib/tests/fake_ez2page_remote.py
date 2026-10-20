#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""假的 ez2.page 遠端端點(只給測試 / 本機試玩;不是正式伺服器)。

照核心跟 ez2.page 定好的介面(10-21 @9f808 / @74e8b):
  手機端(同源、cookie 換成這裡的假 session):
    GET  /api/v1/remote/machines        → {machines:[{fp,name}]}
    GET  /api/v1/remote/session         → {csrf_token, reauth_required}
    POST /api/v1/remote/unlock-grant    {machine, seat, minutes, nonce}                     → {grant, sig}
    POST /api/v1/remote/answer-grant    {machine, seat, ask_id, choice, text_sha256?, nonce} → {grant, sig}
    POST /api/v1/remote/cmd             {machine, op, seat, …}                              → {id}
    GET  /api/v1/remote/result?id=      → 200 {done:true, result} / 202 {done:false}
  本機端(Bearer = 綁定的助理憑證):
    GET  /api/v1/remote/next?machine=   → 200 {id, op, seat, …} / 204
    POST /api/v1/remote/reply           {id, machine, result}
  靜態(可選):/remote/phone.html、/remote/phone.js、/office/themes/<主題>/<檔>(page_dir 給了才開)

寫入要 x-ez2-csrf;簽單用測試種子(正式鑰只在 ez2.page,本機程式的公鑰清單是空的 ⇒ 這裡簽的單子正式程式一律拒)。
"""
import base64
import http.server
import json
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
from entitlement import ed25519, signing  # noqa: E402

TEST_SEED = bytes(range(32))
TEST_KID = "fake-ez2page-1021"
PREFIX = "/api/v1/remote"


def test_keys():
    return {TEST_KID: "ed25519:" + ed25519.public_key(TEST_SEED).hex()}


class Fake:
    def __init__(self, machine_fp, account_id, token, auth="passkey", poll_s=1.0, page_dir=None):
        self.fp, self.acct, self.token, self.auth, self.poll_s = machine_fp, account_id, token, auth, poll_s
        self.page_dir = Path(page_dir) if page_dir else None
        self.csrf = uuid.uuid4().hex
        self.queue, self.results, self.cv = [], {}, threading.Condition()
        self.log = []

    def sign(self, body):
        g = dict(body, v=1, kid=TEST_KID, account_id=self.acct, auth=self.auth, iat=int(time.time()))
        g["exp"] = g["iat"] + (120 if g["kind"] == "seat-answer" else 120)
        return {"grant": g, "sig": base64.urlsafe_b64encode(ed25519.sign(TEST_SEED, signing.canonical(g))).decode().rstrip("=")}

    def handler(self):
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, d=None):
                body = b"" if d is None else json.dumps(d, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("cache-control", "private, no-store")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("content-length") or 0)
                try:
                    return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
                except ValueError:
                    return {}

            def _bearer(self):
                return self.headers.get("authorization") == "Bearer " + fake.token

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                fake.log.append(("GET", u.path))
                if u.path == PREFIX + "/machines":
                    return self._json(200, {"machines": [{"fp": fake.fp, "name": "test-mac"}]})
                if u.path == PREFIX + "/session":
                    return self._json(200, {"csrf_token": fake.csrf, "reauth_required": False})
                if u.path == PREFIX + "/result":
                    with fake.cv:
                        fake.cv.wait_for(lambda: fake.results.get(q.get("id")) is not None, timeout=fake.poll_s)
                        r = fake.results.get(q.get("id"))
                    return self._json(200, {"done": True, "result": r}) if r is not None else self._json(202, {"done": False})
                if u.path == PREFIX + "/next":
                    if not self._bearer():
                        return self._json(401, {"code": "unauthorized"})
                    if q.get("machine") != fake.fp:
                        return self._json(403, {"code": "wrong-machine"})
                    with fake.cv:
                        fake.cv.wait_for(lambda: fake.queue, timeout=fake.poll_s)
                        msg = fake.queue.pop(0) if fake.queue else None
                    return self._json(200, msg) if msg else self._json(204)
                return self._static(u.path)

            def _static(self, path):
                m = re.fullmatch(r"/remote/(phone\.html|phone\.js)|/office/themes/(nordic|glass|loft)/(ph_[a-z0-9_]+\.(?:png|json))", path)
                if not m or not fake.page_dir:
                    return self._json(404, {"code": "not-found"})
                fp = fake.page_dir / ("remote/" + m.group(1) if m.group(1) else "office/themes/%s/%s" % (m.group(2), m.group(3)))
                try:
                    body = fp.read_bytes()
                except OSError:
                    return self._json(404, {"code": "not-found"})
                ct = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".png": "image/png"}.get(fp.suffix, "application/json")
                self.send_response(200)
                self.send_header("content-type", ct)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                u = urlparse(self.path)
                b = self._body()
                fake.log.append(("POST", u.path, b.get("op")))
                if u.path == PREFIX + "/reply":
                    if not self._bearer():
                        return self._json(401, {"code": "unauthorized"})
                    with fake.cv:
                        fake.results[str(b.get("id"))] = b.get("result") or {"ok": False, "why": "empty"}
                        fake.cv.notify_all()
                    return self._json(200, {"ok": True})
                if self.headers.get("x-ez2-csrf") != fake.csrf:
                    return self._json(403, {"code": "csrf"})
                if b.get("machine") != fake.fp:
                    return self._json(403, {"code": "wrong-machine"})
                if u.path == PREFIX + "/unlock-grant":
                    return self._json(200, fake.sign({"kind": "seat-unlock", "machine": b["machine"], "seat": b.get("seat"),
                                                     "minutes": int(b.get("minutes") or 10), "nonce": b.get("nonce")}))
                if u.path == PREFIX + "/answer-grant":
                    g = {"kind": "seat-answer", "machine": b["machine"], "seat": b.get("seat"), "ask_id": b.get("ask_id"),
                         "choice": b.get("choice"), "nonce": b.get("nonce")}
                    if b.get("text_sha256"):
                        g["text_sha256"] = b["text_sha256"]
                    return self._json(200, fake.sign(g))
                if u.path == PREFIX + "/cmd":
                    mid = "c" + uuid.uuid4().hex[:16]
                    msg = {k: v for k, v in b.items() if k != "machine"}
                    msg["id"] = mid
                    with fake.cv:
                        fake.queue.append(msg)
                        fake.cv.notify_all()
                    return self._json(200, {"id": mid})
                return self._json(404, {"code": "not-found"})
        return H

    def serve(self, host="127.0.0.1", port=0):
        srv = http.server.ThreadingHTTPServer((host, port), self.handler())
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv
