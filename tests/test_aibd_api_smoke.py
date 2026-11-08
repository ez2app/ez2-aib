"""scripts/aibd-api-smoke.py against a fake aibd (043dd 11-08, a63ab preview.2 prep).

The fake is a tiny Python HTTP server that behaves like aibd (start-up JSON line, token file, run/aibd.json,
401 without the token, Host check), with one switch per way of being wrong. No cargo needed; the real binary
is smoked by the release workflow. POSIX only (the fake is a #! script). Bad Host is judged by exact 403 + code host_not_allowed (bb7dc #534).
"""
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("aibd_api_smoke", str(ROOT / "scripts" / "aibd-api-smoke.py"))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

FAKE = textwrap.dedent('''\
    #!{py}
    import http.server, json, os, socketserver, sys
    mode = {mode!r}
    print("fake: up", file=sys.stderr, flush=True)
    if mode == "no_start":
        print("config error", file=sys.stderr); sys.exit(2)
    data = os.environ["AIBD_DATA_DIR"]; run = os.path.join(data, "run"); os.makedirs(run)
    host, _, port = os.environ["AIBD_BIND"].rpartition(":")
    tok = "t0ken"
    def err(code):
        return {{"code": code, "message": code, "request_id": "0123456789abcdef"}}
    BAD_HOST = {{"bad_host_500": (500, err("internal")), "bad_host_404": (404, err("not_found")),
                "bad_host_401": (401, err("unauthorized")), "bad_host_421": (421, {{}}),
                "bad_host_wrong_code": (403, err("origin_not_allowed")), "bad_host_no_body": (403, None),
                "bad_host_500_right_code": (500, err("host_not_allowed"))}}
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            port = self.server.server_address[1]
            if mode != "any_host" and self.headers.get("Host") != "127.0.0.1:%d" % port:
                return self.reply(*BAD_HOST.get(mode, (403, err("host_not_allowed"))))
            if self.path == "/api/v1/health":
                return self.reply(200, {{"status": "not_ok" if mode == "health_not_ok" else "ok"}})
            if mode == "open_metrics" and self.path == "/api/v1/metrics":
                return self.reply(200, {{}})
            if self.headers.get("Authorization") != "Bearer " + tok:
                return self.reply(401, {{}} if mode == "noauth_no_code" else err("unauthorized"))
            self.reply(200, {{}})
        def reply(self, code, body):
            b = b"" if body is None else json.dumps(body).encode()
            self.send_response(code); self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b)
    class S(http.server.HTTPServer):
        def server_bind(self):          # HTTPServer.server_bind calls socket.getfqdn(): a reverse DNS lookup
            socketserver.TCPServer.server_bind(self)    # that hung > 15 s on the macOS 3.12 CI runner
            self.server_name, self.server_port = "127.0.0.1", self.server_address[1]
    s = S((host, int(port)), H)
    real = s.server_address[1]
    print("fake: bound %d" % real, file=sys.stderr, flush=True)
    open(os.path.join(run, "aibd.token"), "w").write(tok)
    pid = os.getpid() + (1 if mode == "wrong_pid" else 0)
    open(os.path.join(run, "aibd.json"), "w").write(json.dumps({{"pid": pid, "port": real}}))
    print(json.dumps({{"listening": "127.0.0.1:%d" % real, "token_file": os.path.join(run, "aibd.token")}}), flush=True)
    try:
        s.serve_forever()
    except KeyboardInterrupt:
        pass
''')


@unittest.skipUnless(os.name == "posix", "the fake aibd is a #! script")
class Smoke(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.d = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def run_smoke(self, mode):
        exe = self.d / "aibd"
        exe.write_text(FAKE.format(py=sys.executable, mode=mode), encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = smoke.main([str(exe), "--timeout", "15"])
        return rc, json.loads(buf.getvalue().strip().splitlines()[-1])

    def test_good_aibd_passes_and_is_stopped_and_cleaned(self):
        rc, r = self.run_smoke("good")
        self.assertEqual(rc, 0, r)
        self.assertTrue(r["pass"] and r["loopback"] and r["token_read"] and r["run_info_pid_is_ours"], r)
        self.assertEqual([r[k] for k in ("health_noauth", "metrics_noauth", "events_noauth", "metrics_auth",
                                         "events_auth", "metrics_wrong_token")], [200, 401, 401, 200, 200, 401])
        self.assertEqual(r["health_bad_host"], 403)
        self.assertNotIn("failed", r)
        self.assertIn(r["stopped"], ("sigint", "killed"))   # CI may start us with SIGINT ignored
        self.assertTrue(r["exited"] and r["cleaned"], r)

    def test_metrics_open_without_token_fails(self):
        rc, r = self.run_smoke("open_metrics")
        self.assertEqual(rc, 1, r)
        self.assertEqual(r["metrics_noauth"], 200)
        self.assertTrue(r["exited"], r)

    def test_any_host_accepted_fails(self):
        rc, r = self.run_smoke("any_host")
        self.assertEqual(rc, 1, r)
        self.assertEqual(r["health_bad_host"], 200)

    def test_bad_host_answered_with_anything_but_403_host_not_allowed_fails(self):
        # bb7dc #534 High: 500 / 404 / 401 / 421 / another code / no body must not pass as "the guard refused"
        for mode, status in (("bad_host_500", 500), ("bad_host_404", 404), ("bad_host_401", 401),
                             ("bad_host_421", 421), ("bad_host_wrong_code", 403), ("bad_host_no_body", 403),
                             ("bad_host_500_right_code", 500)):
            with self.subTest(mode=mode):
                rc, r = self.run_smoke(mode)
                self.assertEqual(rc, 1, r)
                self.assertEqual(r["health_bad_host"], status)
                self.assertEqual(list(r["failed"]), ["health_bad_host"], r)
                self.assertTrue(r["exited"] and r["cleaned"], r)

    def test_401_without_the_unauthorized_code_fails(self):
        rc, r = self.run_smoke("noauth_no_code")
        self.assertEqual(rc, 1, r)
        self.assertEqual(r["metrics_noauth"], 401)
        self.assertIn("metrics_noauth", r["failed"])
        self.assertIn("code None", r["failed"]["metrics_noauth"])

    def test_health_200_without_status_ok_fails(self):
        rc, r = self.run_smoke("health_not_ok")
        self.assertEqual(rc, 1, r)
        self.assertEqual(list(r["failed"]), ["health_noauth"], r)

    def test_nothing_answering_is_a_network_error_not_a_refusal(self):
        got, body, net = smoke.http_call("http://127.0.0.1:9/api/v1/health", host="evil.example", timeout=2)
        self.assertEqual((got, body), (0, None))
        self.assertTrue(net)

    def test_a_run_info_pid_that_is_not_ours_fails(self):
        rc, r = self.run_smoke("wrong_pid")
        self.assertEqual(rc, 1, r)
        self.assertFalse(r["run_info_pid_is_ours"])
        self.assertTrue(r["exited"], r)

    def test_an_aibd_that_does_not_start_fails_with_its_stderr(self):
        rc, r = self.run_smoke("no_start")
        self.assertEqual(rc, 1, r)
        self.assertFalse(r["started"])
        self.assertIn("config error", r["err_tail"])
        self.assertEqual(r["stopped"], "already_exited")

    def test_usage(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(smoke.main([]), 2)
            self.assertEqual(smoke.main([str(self.d / "missing")]), 2)
            self.assertEqual(smoke.main(["x", "--timeout"]), 2)


if __name__ == "__main__":
    unittest.main()
