"""scripts/aibd-sessions-smoke.py (1ced7 11-09, a63ab PLAN-preview2-usable-today).

A fake aibd (python http server; prints the same start-up line, token file and run/aibd.json). Modes:
  absent       POST /api/v1/sessions -> 404                    => SKIP, rc 0; with --require rc 1
  good         killpg; stop_unconfirmed + unverified/observed_clean (the macOS answer, #539 M2 = B) => pass
  clean        killpg; killed + clean (the Windows answer)      => pass
  leaky        kills only the session pid, still says killed + clean => rc 1 (the outside check sees the grandchild)
  unconfirmed  killpg; stop_unconfirmed + tree_not_clean, survivors [1] => rc 1 (believe aibd's own report)
  otherreason  killpg; stop_unconfirmed + unverified/lineage_unavailable => rc 1 (only observed_clean counts)
  killedunv    killpg; killed + unverified                      => rc 1 (killed needs clean)
  slowmarker   like good, but the program starts FAKE_DELAY s late => its marker comes after --marker-timeout => rc 1
               (bb7dc #537 11722db7: the wider start-up wait must not widen the marker wait)
The smoke never signals a pid itself (bb7dc #537 High 1): leftovers exit on the stop file; the test checks they did.
POSIX only (the fake uses process groups); Windows is covered by the release job running the real aibd.
"""
import importlib.util
import json
import os
import re
import time
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts" / "aibd-sessions-smoke.py"
# how long the smoke waits for the fake to print its start-up line; a busy CI runner (364 tests serial on
# macos 3.12, #537 run 42533782542) needed more than 15 s. Wider wait, same verdict rules.
START = os.environ.get("AIBD_SMOKE_TEST_TIMEOUT", "60")
MARKER = "15"  # bb7dc #537:marker 時限固定 15,不跟 AIBD_SMOKE_TEST_TIMEOUT 走


def smoke_args(fake, start=None, marker=None):
    return [sys.executable, "-I", str(SMOKE), str(fake), "--timeout", START if start is None else start,
            "--marker-timeout", MARKER if marker is None else marker]

FAKE = textwrap.dedent('''\
    #!%(py)s
    import os, sys
    if os.environ["FAKE_MODE"] == "hang":   # first thing, before the heavier imports: a busy runner is slow to start
        import time; sys.stderr.write("fake: still booting\\n"); sys.stderr.flush(); time.sleep(60)
    import json, signal, subprocess, threading
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
            argv = body["argv"]
            if MODE == "slowmarker":
                argv = ["/bin/sh", "-c", 'sleep "$FAKE_DELAY"; exec "$@"', "sh"] + argv
            p = subprocess.Popen(argv, cwd=body.get("cwd"), start_new_session=True)
            S["s1"] = p
            self.send(201, {"id": "s1", "pid": p.pid, "started_at": 0})
        def do_GET(self):
            if not self.authed(): return self.send(401)
            p = S.get(self.path.rsplit("/", 1)[-1])
            if not p: return self.send(404)
            self.send(200, {"state": "running" if p.poll() is None else "killed"})
        def do_DELETE(self):
            p = S.get(self.path.rsplit("/", 1)[-1])
            if MODE != "leaky": os.killpg(p.pid, signal.SIGKILL)
            else: os.kill(p.pid, signal.SIGKILL)
            p.wait()
            ans = {
                "good": ("stop_unconfirmed", {"status": "unverified", "reason": "observed_clean", "survivors": []}),
                "slowmarker": ("stop_unconfirmed", {"status": "unverified", "reason": "observed_clean", "survivors": []}),
                "clean": ("killed", {"status": "clean", "survivors": []}),
                "leaky": ("killed", {"status": "clean", "survivors": []}),
                "unconfirmed": ("stop_unconfirmed", {"status": "tree_not_clean", "survivors": [1]}),
                "otherreason": ("stop_unconfirmed", {"status": "unverified", "reason": "lineage_unavailable",
                                                     "survivors": []}),
                "killedunv": ("killed", {"status": "unverified", "reason": "observed_clean", "survivors": []}),
            }[MODE]
            self.send(200, {"state": ans[0], "stop": ans[1]})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    print(json.dumps({"listening": "127.0.0.1:%%d" %% srv.server_address[1], "token_file": tok}), flush=True)
    signal.signal(signal.SIGINT, lambda *a: threading.Thread(target=srv.shutdown).start())
    srv.serve_forever()
''') % {"py": sys.executable}


@unittest.skipUnless(os.name == "posix", "fake aibd uses process groups")
class SessionsSmoke(unittest.TestCase):
    def run_smoke(self, mode, *extra, start=START, marker=MARKER, delay="0"):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "aibd"
            fake.write_text(FAKE, encoding="utf-8")
            fake.chmod(0o755)
            r = subprocess.run(smoke_args(fake, start, marker) + list(extra), env=dict(os.environ, FAKE_MODE=mode, FAKE_DELAY=delay), capture_output=True, text=True,
                               timeout=2 * float(start) + 90)
        line = (r.stdout.strip().splitlines() or [""])[-1]
        res = json.loads(line[line.index("{"):]) if "{" in line else {}
        return r.returncode, res, r.stdout + r.stderr

    def test_a_late_marker_is_red_even_with_a_wide_start_wait(self):
        """bb7dc #537:啟動等很久(START,預設 60)也不准順便把 marker 的等待放寬;marker 晚於 --marker-timeout ⇒ 紅。"""
        rc, res, log = self.run_smoke("slowmarker", marker="1", delay="3")
        self.assertEqual((rc, res.get("started"), res.get("marker"), res.get("marker_wait_s")), (1, True, False, 1.0), log)
        self.assertGreater(float(START), 3, "這支要在啟動等待 > 延遲時才分得出兩個時限")

    def test_a_late_marker_inside_its_limit_is_still_green(self):
        """正向對照:同一個 slowmarker,marker 時限比延遲長 ⇒ 綠(證明上面那支紅是時限造成的，不是假 aibd 壞)。"""
        rc, res, log = self.run_smoke("slowmarker", marker="15", delay="1")
        self.assertEqual((rc, res.get("marker"), res.get("pass")), (0, True, True), log)

    def test_a_wide_start_wait_leaves_the_marker_limit_at_15(self):
        """a63ab 09:56:設 AIBD_SMOKE_TEST_TIMEOUT=60 時，傳給 smoke 的 marker 時限仍是 15,smoke 自己也記 15。"""
        spec = importlib.util.spec_from_file_location("sessions_smoke_test_again", __file__)
        mod = importlib.util.module_from_spec(spec)
        old = os.environ.get("AIBD_SMOKE_TEST_TIMEOUT")
        os.environ["AIBD_SMOKE_TEST_TIMEOUT"] = "60"
        try:
            spec.loader.exec_module(mod)
        finally:
            if old is None:
                os.environ.pop("AIBD_SMOKE_TEST_TIMEOUT", None)
            else:
                os.environ["AIBD_SMOKE_TEST_TIMEOUT"] = old
        args = mod.smoke_args("aibd")
        self.assertEqual((args[args.index("--timeout") + 1], args[args.index("--marker-timeout") + 1]), ("60", "15"))
        rc, res, log = self.run_smoke("good")
        self.assertEqual((rc, res.get("marker_wait_s")), (0, 15.0), log)

    def test_the_two_limits_are_separate_flags(self):
        src = SMOKE.read_text(encoding="utf-8")
        self.assertTrue("wait_for(lambda: os.path.exists(marker), marker_timeout)" in src,
                        "the marker wait must use marker_timeout, not the start-up timeout")

    def test_a_start_that_never_came_says_why(self):
        # 5 s, not 1: on a busy Mac Pro 13 (6e2af994) the fake had not written its stderr line after 1 s.
        # The fake sleeps 60 s, so the start still never comes; 5 s only gives its first line time to land.
        rc, res, log = self.run_smoke("hang", start="5")
        self.assertEqual((rc, res.get("started"), res.get("start_wait_s"), res.get("alive_at_timeout")),
                         (1, False, 5.0, True), log)
        self.assertIn("fake: still booting", res.get("err_tail") or [], log)

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

    def test_clean_windows_answer_passes(self):
        rc, res, log = self.run_smoke("clean")
        self.assertEqual((rc, res.get("delete_state"), res.get("stop_status")), (0, "killed", "clean"), log)

    def test_leaky_kill_is_red_and_the_leftover_exits_by_itself(self):
        rc, res, log = self.run_smoke("leaky")
        self.assertEqual(rc, 1, log)
        self.assertTrue(res.get("survivors"), "孫程序殘留要被外部查到(aibd 自稱 clean 也一樣)\n" + log)
        self.assertNotIn("leftovers_running", res, "停止檔建了之後殘留要自己結束\n" + log)
        for p in res["survivors"]:
            out = subprocess.run(["ps", "-o", "stat=", "-p", str(p)], capture_output=True, text=True).stdout.strip()
            self.assertTrue(out == "" or out.startswith("Z"), "殘留 %d 還活著:%r" % (p, out))

    def test_answers_the_contract_does_not_allow_are_red(self):
        for mode in ("otherreason", "killedunv"):
            with self.subTest(mode=mode):
                rc, res, log = self.run_smoke(mode)
                self.assertEqual((rc, res.get("survivors"), res.get("done_ok")), (1, [], False), log)

    def test_aibd_reporting_a_survivor_is_red(self):
        rc, res, log = self.run_smoke("unconfirmed")
        self.assertEqual((rc, res.get("delete_state"), res.get("stop_status")), (1, "stop_unconfirmed", "tree_not_clean"), log)

    def test_good_macos_answer_is_stop_unconfirmed_observed_clean(self):
        rc, res, log = self.run_smoke("good")
        self.assertEqual((rc, res.get("delete_state"), res.get("stop_status"), res.get("stop_reason")),
                         (0, "stop_unconfirmed", "unverified", "observed_clean"), log)

    def test_usage(self):
        r = subprocess.run([sys.executable, "-I", str(SMOKE)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)
        r = subprocess.run([sys.executable, "-I", str(SMOKE), "/no/such/aibd"], capture_output=True, text=True,
                           timeout=30)
        self.assertEqual(r.returncode, 2)


def load_smoke():
    spec = importlib.util.spec_from_file_location("sessions_smoke", SMOKE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class NoSignalsAndProofOfGone(unittest.TestCase):
    """bb7dc #537 High 1 / 2."""

    def test_the_script_never_signals_a_pid(self):
        src = SMOKE.read_text(encoding="utf-8")
        # aibd itself is stopped through its Popen handle (proc.send_signal / proc.kill: our own child, not a bare pid)
        for bad in ("taskkill", "killpg", "SIGKILL", "SIGTERM", "TerminateProcess"):
            self.assertNotIn(bad, src)
        self.assertEqual(re.findall(r"os\.kill\([^)]*\)", src), ["os.kill(pid, 0)"], "只准 kill(pid, 0) 探測")

    def test_windows_prototypes_are_declared(self):
        src = SMOKE.read_text(encoding="utf-8")
        for need in ("OpenProcess.restype = wintypes.HANDLE", "OpenProcess.argtypes",
                     "GetExitCodeProcess.argtypes", "use_last_error=True", "return UNKNOWN"):
            self.assertIn(need, src)

    @unittest.skipUnless(os.name == "posix", "POSIX branch")
    def test_alive_is_three_state(self):
        m = load_smoke()
        self.assertEqual(m.alive(os.getpid()), m.ALIVE)
        self.assertEqual(m.alive(0), m.UNKNOWN)
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        for _ in range(100):                      # a zombie until reaped: gone
            out = subprocess.run(["ps", "-o", "stat=", "-p", str(p.pid)], capture_output=True, text=True).stdout
            if out.strip().startswith("Z"):
                break
            time.sleep(0.05)
        self.assertEqual(m.alive(p.pid), m.GONE)
        p.wait()
        self.assertEqual(m.alive(p.pid), m.GONE)
        real_kill, real_run = m.os.kill, m.subprocess.run
        try:
            def perm(pid, sig):
                raise PermissionError
            m.os.kill = perm
            self.assertEqual(m.alive(12345), m.ALIVE, "權限不足 = 它在")
            def other(pid, sig):
                raise OSError(5, "io")
            m.os.kill = other
            self.assertEqual(m.alive(12345), m.UNKNOWN, "查不了 = unknown,不是 gone")
            m.os.kill = lambda pid, sig: None
            m.subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "")
            self.assertEqual(m.alive(12345), m.ALIVE, "kill(0) 說在、ps 失敗 ⇒ 不能算 gone")
            def boom(*a, **k):
                raise OSError("no ps")
            m.subprocess.run = boom
            self.assertEqual(m.alive(12345), m.ALIVE)
        finally:
            m.os.kill, m.subprocess.run = real_kill, real_run

    def test_done_rule(self):
        m = load_smoke()
        ok = m.done_ok
        self.assertTrue(ok("killed", "clean", None, []))
        self.assertFalse(ok("killed", "clean", None, [5]))
        self.assertFalse(ok("killed", "unverified", "observed_clean", []))
        self.assertFalse(ok("stop_unconfirmed", "unverified", "lineage_unavailable", []))
        self.assertFalse(ok("stop_unconfirmed", "tree_not_clean", None, []))
        self.assertFalse(ok("exited", "clean", None, []))
        self.assertEqual(ok("stop_unconfirmed", "unverified", "observed_clean", []), os.name != "nt",
                         "macOS / POSIX 接受 observed_clean;Windows 只認 killed + clean")


if __name__ == "__main__":
    unittest.main()
