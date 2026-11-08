"""scripts/aibd-sessions-smoke.py (1ced7 11-09, a63ab PLAN-preview2-usable-today).

A fake aibd (python http server; prints the same start-up line, token file and run/aibd.json) in three modes:
  absent  POST /api/v1/sessions -> 404        => SKIP, rc 0; with --require rc 1
  good    DELETE kills the process group      => pass, rc 0, no survivors
  leaky   DELETE kills only the session pid   => rc 1, the grandchild is reported and then killed by the smoke
  unconfirmed  tree is gone but aibd answers stop_unconfirmed / tree_not_clean => rc 1 (believe aibd's own report)
POSIX only (the fake uses process groups); Windows is covered by the release job running the real aibd.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts" / "aibd-sessions-smoke.py"

FAKE = textwrap.dedent('''\
    #!%(py)s
    import json, os, signal, subprocess, sys, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    MODE = os.environ["FAKE_MODE"]
    data = os.environ["AIBD_DATA_DIR"]; run = os.path.join(data, "run"); os.makedirs(run)
    tok = os.path.join(run, "token"); open(tok, "w").write("t0k")
    json.dump({"pid": os.getpid()}, open(os.path.join(run, "aibd.json"), "w"))
    S = {}
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def send(self, code, body=None):
            raw = json.dumps(body).encode() if body is not None else b""
            self.send_response(code); self.send_header("Content-Length", str(len(raw))); self.end_headers()
            self.wfile.write(raw)
        def authed(self):
            return self.headers.get("Authorization") == "Bearer t0k"
        def do_POST(self):
            if MODE == "absent": return self.send(404, {"error": "not_found"})
            if not self.authed(): return self.send(401)
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            p = subprocess.Popen(body["argv"], cwd=body.get("cwd"), start_new_session=True)
            S["s1"] = p
            self.send(201, {"id": "s1", "pid": p.pid, "started_at": 0})
        def do_GET(self):
            if not self.authed(): return self.send(401)
            p = S.get(self.path.rsplit("/", 1)[-1])
            if not p: return self.send(404)
            self.send(200, {"state": "running" if p.poll() is None else "killed"})
        def do_DELETE(self):
            p = S.get(self.path.rsplit("/", 1)[-1])
            if MODE in ("good", "unconfirmed"): os.killpg(p.pid, signal.SIGKILL)
            else: os.kill(p.pid, signal.SIGKILL)
            p.wait()
            if MODE == "unconfirmed":
                return self.send(200, {"state": "stop_unconfirmed", "stop": {"status": "tree_not_clean", "survivors": [1]}})
            self.send(200, {"state": "killed", "stop": {"status": "unverified", "survivors": []}})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    print(json.dumps({"listening": "127.0.0.1:%%d" %% srv.server_address[1], "token_file": tok}), flush=True)
    signal.signal(signal.SIGINT, lambda *a: threading.Thread(target=srv.shutdown).start())
    srv.serve_forever()
''') % {"py": sys.executable}


@unittest.skipUnless(os.name == "posix", "fake aibd uses process groups")
class SessionsSmoke(unittest.TestCase):
    def run_smoke(self, mode, *extra):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "aibd"
            fake.write_text(FAKE, encoding="utf-8")
            fake.chmod(0o755)
            r = subprocess.run([sys.executable, "-I", str(SMOKE), str(fake), "--timeout", "15"] + list(extra),
                               env=dict(os.environ, FAKE_MODE=mode), capture_output=True, text=True, timeout=90)
        line = (r.stdout.strip().splitlines() or [""])[-1]
        res = json.loads(line[line.index("{"):]) if "{" in line else {}
        return r.returncode, res, r.stdout + r.stderr

    def test_absent_is_a_named_skip(self):
        rc, res, log = self.run_smoke("absent")
        self.assertEqual((rc, res.get("sessions"), res.get("skipped")), (0, "absent", True), log)
        self.assertIn("SKIP", log)

    def test_absent_with_require_is_red(self):
        rc, res, log = self.run_smoke("absent", "--require")
        self.assertEqual((rc, res.get("sessions")), (1, "absent"), log)

    def test_good_tree_kill_passes(self):
        rc, res, log = self.run_smoke("good")
        self.assertEqual(rc, 0, log)
        self.assertEqual(res.get("survivors"), [], log)
        ab = res.get("alive_before", {})
        self.assertIn(str(res.get("grandchild_pid")), ab, "孫程序要量到\n" + log)
        self.assertIn(str(res.get("session_pid")), ab, log)
        self.assertGreaterEqual(len(ab), 2, "child + grandchild 都要量到\n" + log)
        self.assertTrue(all(res["alive_before"].values()), log)

    def test_leaky_kill_is_red_and_the_survivor_is_cleaned(self):
        rc, res, log = self.run_smoke("leaky")
        self.assertEqual(rc, 1, log)
        self.assertTrue(res.get("survivors"), "孫程序殘留要被抓到\n" + log)
        self.assertEqual(sorted(res.get("killed_leftovers", [])), sorted(res["survivors"]), log)
        for p in res["survivors"]:
            out = subprocess.run(["ps", "-o", "stat=", "-p", str(p)], capture_output=True, text=True).stdout.strip()
            self.assertTrue(out == "" or out.startswith("Z"), "smoke 收掉的殘留 %d 還活著:%r" % (p, out))

    def test_aibd_reporting_a_survivor_is_red(self):
        rc, res, log = self.run_smoke("unconfirmed")
        self.assertEqual((rc, res.get("delete_state"), res.get("stop_status")), (1, "stop_unconfirmed", "tree_not_clean"), log)

    def test_usage(self):
        r = subprocess.run([sys.executable, "-I", str(SMOKE)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)
        r = subprocess.run([sys.executable, "-I", str(SMOKE), "/no/such/aibd"], capture_output=True, text=True,
                           timeout=30)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
