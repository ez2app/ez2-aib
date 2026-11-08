# SPDX-License-Identifier: Apache-2.0
"""11-08 PROPOSAL-12:機器租約分 long / short 兩隊(host_lease.py run-short)。

全部在暫存 HOME / 租約落點跑(AIB_HOST_LEASE_FILE 只搬落點),不碰真租約。
熱讀數用 AIB_HOST_LEASE_HEAT_SAMPLE(治具讀數檔,租約 / 留痕會標 source=fixture),不讀這台機器現在多熱 ——
不然測試結果跟著機器溫度變。Chrome 上限用行程內載入模組再換常數。
"""
import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "aib" / "tools" / "governance" / "host_lease.py"
TEST_ENTRY_DIR = ROOT / "aib" / "tools" / "verify"


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # zombie 也算死(macOS ps 狀態 Z)
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


class Lanes(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-lanes-"))
        self.home = self.td / "home"
        self.home.mkdir()
        self.state = self.td / "state-root"
        self.state.mkdir()
        self.lease = self.td / "lease.json"
        self.heat = self.td / "heat.json"
        self.set_heat(throttle=5.0, load1=2.0, cpu_c=50.0)
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home), "EZ2_AIB_LOCALE": "en",
                    "AIB_HOST_LEASE_FILE": str(self.lease),
                    "AIB_HOST_LEASE_HEAT_SAMPLE": str(self.heat),
                    "EZ2_AIB_STATE_ROOT": str(self.state)}
        self.procs = []
        self.tree_a = self.td / "tree-a"
        self.tree_b = self.td / "tree-b"
        for t in (self.tree_a, self.tree_b):
            t.mkdir()
            subprocess.run(["git", "init", "-q", str(t)], check=True, env=dict(self.env, GIT_CONFIG_GLOBAL="/dev/null"))

    def set_heat(self, **kw):
        self.heat.write_text(json.dumps(kw))

    def tearDown(self):
        for p in self.procs:        # 只收自己起的
            if p.poll() is None:
                p.kill()
                p.wait()
        shutil.rmtree(self.td, ignore_errors=True)

    def run_(self, args, cwd=None, env=None, timeout=60):
        return subprocess.run([sys.executable, "-B", str(TOOL)] + args, env=env or self.env, cwd=str(cwd or self.tree_b),
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout)

    def short(self, cmd, est="30", extra=(), **kw):
        return self.run_(["run-short", "--owner", "t", "--what", "knife", "--est-s", est, "--no-browser",
                          "--no-fixed-port", *extra, "--"] + cmd, **kw)

    def hold_long(self, cwd=None, chrome=False):
        args = ["acquire", "--owner", "f457", "--what", "ez2-ai push", "--run-id", "L1", "--pid", str(os.getpid()),
                "--ttl", "600"] + (["--chrome"] if chrome else [])
        r = self.run_(args, cwd=cwd or self.tree_a)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def ledger(self):
        p = Path(str(self.lease) + ".log")
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    # ── 正向:long 佔用時 short 拿得到,子行程 nice 10、帶 run id,跑完放掉,宣告與併跑進留痕 ──
    def test_short_runs_alongside_long_with_nice_and_declaration(self):
        self.hold_long()
        probe = "import os,json;print(json.dumps({'nice':os.nice(0),'rid':os.environ.get('AIB_LEASE_SHORT_RUN_ID')}))"
        r = self.short([sys.executable, "-c", probe])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = json.loads([x for x in r.stdout.splitlines() if x.startswith("{")][0])
        self.assertGreaterEqual(got["nice"], 10)
        self.assertTrue(got["rid"])
        self.assertIn("concurrent", r.stdout)
        self.assertFalse(Path(str(self.lease) + ".short.json").exists())
        rows = [x for x in self.ledger() if x.get("lane") == "short"]
        acq = [x for x in rows if x["event"] == "acquire"][0]
        self.assertEqual(acq["declared"]["state_root"], os.path.realpath(self.state))
        self.assertTrue(acq["declared"]["no_browser"] and acq["declared"]["no_fixed_port"])
        self.assertEqual(acq["concurrent_with"]["run_id"], "L1")
        rel = [x for x in rows if x["event"] == "release"][0]
        self.assertEqual(rel["rc"], 0)
        self.assertTrue(rel["concurrent"])
        # long 那把沒被動到
        self.assertEqual(json.loads(self.lease.read_text())["run_id"], "L1")

    def test_red_while_concurrent_says_rerun(self):
        self.hold_long()
        r = self.short([sys.executable, "-c", "raise SystemExit(3)"])
        self.assertEqual(r.returncode, 3)
        self.assertIn("red while concurrent (exit 3)", r.stdout)

    def test_long_still_acquirable_while_short_runs_and_status_shows_both(self):
        p = subprocess.Popen([sys.executable, "-B", str(TOOL), "run-short", "--owner", "t", "--what", "sleeper",
                              "--est-s", "30", "--no-browser", "--no-fixed-port", "--", "sleep", "20"],
                             env=self.env, cwd=str(self.tree_b), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL)
        self.procs.append(p)
        sp = Path(str(self.lease) + ".short.json")
        t0 = time.time()
        while not sp.exists() and time.time() - t0 < 15:
            time.sleep(0.1)
        self.assertTrue(sp.exists())
        self.hold_long()
        st = self.run_(["status", "--json"])
        d = json.loads(st.stdout)
        self.assertEqual(d["state"], "held")
        self.assertEqual(d["short"]["state"], "held")
        self.assertEqual(d["short"]["lease"]["what"], "sleeper")
        # 兩隊各自一次 1 份:第二個 short 被拒,留痕 short_busy
        r = self.short(["true"])
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("short lane held by t", r.stdout)
        self.assertTrue(any(x.get("why") == "short_busy" for x in self.ledger() if x["event"] == "denied"))
        p.send_signal(signal.SIGTERM)       # run-short 收到 TERM ⇒ 收自己那組、放鎖
        p.wait(timeout=30)
        self.assertFalse(sp.exists())

    # ── 逾時降級:只收自己那一組(含孫行程),rc 6,留痕 short_overrun ──
    def test_overrun_kills_own_group_and_downgrades(self):
        pids = self.td / "pids"
        script = ("import os,subprocess,time,sys;c=subprocess.Popen(['sleep','60']);"
                  "open(sys.argv[1],'w').write('%d %d'%(os.getpid(),c.pid));time.sleep(60)")
        outsider = subprocess.Popen(["sleep", "60"])     # 不在那一組:不能被收
        self.procs.append(outsider)
        t0 = time.time()
        r = self.short([sys.executable, "-c", script, str(pids)], est="2")
        took = time.time() - t0
        self.assertEqual(r.returncode, 6, r.stdout + r.stderr)
        self.assertLess(took, 30)
        self.assertIn("downgraded", r.stdout)
        a, b = (int(x) for x in pids.read_text().split())
        for pid in (a, b):
            t1 = time.time()
            while alive(pid) and time.time() - t1 < 5:
                time.sleep(0.1)
            self.assertFalse(alive(pid), pid)
        self.assertTrue(alive(outsider.pid))
        ev = [x["event"] for x in self.ledger() if x.get("lane") == "short"]
        self.assertIn("short_overrun", ev)
        self.assertFalse(Path(str(self.lease) + ".short.json").exists())

    # ── 熱保護(定案 C + bb7dc 兩條)──
    def denied_why(self, r):
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        return [x for x in self.ledger() if x["event"] == "denied" and x.get("lane") == "short"][-1]["why"]

    def test_throttle_threshold_is_40(self):
        self.set_heat(throttle=39.9, load1=2.0, cpu_c=50.0)
        r = self.short(["true"], extra=("--run-id", "a"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("39.9", r.stdout)              # 拿到時印當下節流 %
        acq = [x for x in self.ledger() if x.get("lane") == "short" and x["event"] == "acquire"][-1]
        self.assertEqual(acq["heat"]["throttle"], 39.9)
        self.assertEqual(acq["heat"]["source"], "fixture")
        self.set_heat(throttle=40.0, load1=2.0, cpu_c=50.0)
        r = self.short(["true"])
        self.assertEqual(self.denied_why(r), "throttle")
        self.assertIn("40.0", r.stdout)

    def test_unmeasured_throttle_or_load_refuses(self):
        self.set_heat(load1=2.0, cpu_c=50.0)             # 沒有 throttle
        self.assertEqual(self.denied_why(self.short(["true"])), "throttle_unmeasured")
        self.set_heat(throttle=5.0, cpu_c=50.0)          # 沒有 load1
        self.assertEqual(self.denied_why(self.short(["true"])), "load1_unmeasured")
        self.heat.write_text("{broken")
        self.assertEqual(self.denied_why(self.short(["true"])), "load1_unmeasured")

    def test_cpu_critical_and_load1_refuse(self):
        self.set_heat(throttle=5.0, load1=2.0, cpu_c=90.0)
        self.assertEqual(self.denied_why(self.short(["true"])), "cpu_critical")
        self.set_heat(throttle=5.0, load1=12.5, cpu_c=50.0)
        self.assertEqual(self.denied_why(self.short(["true"])), "load1")

    def test_cpu_temp_unmeasured_does_not_block(self):
        self.set_heat(throttle=5.0, load1=2.0)           # 沒有 cpu_c:只記、不擋(定案沒列)
        r = self.short(["true"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_recovery_needs_five_cool_minutes(self):
        t0 = 2_000_000_000
        self.set_heat(throttle=55.0, load1=2.0, cpu_c=50.0)
        r = self.run_(["--now", str(t0), "run-short", "--owner", "t", "--what", "k", "--no-browser",
                       "--no-fixed-port", "--", "true"])
        self.assertEqual(self.denied_why(r), "throttle")
        self.set_heat(throttle=5.0, load1=2.0, cpu_c=50.0)
        for dt, want in ((60, 4), (299, 4), (300, 0)):
            r = self.run_(["--now", str(t0 + dt), "run-short", "--owner", "t", "--what", "k", "--no-browser",
                           "--no-fixed-port", "--", "true"])
            self.assertEqual(r.returncode, want, (dt, r.stdout + r.stderr))
            if want == 4:
                self.assertIn("cooling_down", r.stdout)

    # ── 宣告:三件少一件就 rc 2,不拿租約 ──
    def test_declarations_required(self):
        r = self.run_(["run-short", "--owner", "t", "--what", "k", "--no-browser", "--", "true"])
        self.assertEqual(r.returncode, 2)
        r = self.run_(["run-short", "--owner", "t", "--what", "k", "--no-fixed-port", "--", "true"])
        self.assertEqual(r.returncode, 2)
        r = self.short(["true"], est="301")
        self.assertEqual(r.returncode, 2)
        env = dict(self.env)
        env.pop("EZ2_AIB_STATE_ROOT")
        r = self.short(["true"], env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("state_root_missing", r.stderr)
        real = self.home / ".ez2-aib" / "sub"
        real.mkdir(parents=True)
        r = self.short(["true"], env=dict(self.env, EZ2_AIB_STATE_ROOT=str(real)))
        self.assertEqual(r.returncode, 2)
        self.assertIn("state_root_real", r.stderr)
        self.assertFalse(Path(str(self.lease) + ".short.json").exists())
        self.assertFalse([x for x in self.ledger() if x.get("lane") == "short"])

    # ── 同一棵樹:long 租約在這棵樹 ⇒ 不發;別棵樹照發 ──
    def test_same_tree_as_long_refused(self):
        self.hold_long(cwd=self.tree_a)
        r = self.short(["true"], cwd=self.tree_a)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("same_tree_long", r.stdout)
        r = self.short(["true"], cwd=self.tree_b)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # ── 持有者死了 ⇒ 接管並留痕 ──
    def test_dead_short_holder_taken_over(self):
        dead = subprocess.Popen(["true"])
        dead.wait()
        now = int(time.time())
        Path(str(self.lease) + ".short.json").write_text(json.dumps({
            "schema": "aib-host-lease-short/v1", "lane": "short", "owner": "ghost", "what": "x", "run_id": "G",
            "pid": dead.pid, "pid_start": "Thu Jan  1 00:00:00 1970", "host": socket.gethostname(),
            "acquired_epoch": now, "expiry_epoch": now + 300}))
        r = self.short(["true"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        tk = [x for x in self.ledger() if x.get("lane") == "short" and x["event"] == "takeover"]
        self.assertEqual(tk[0]["prev_run_id"], "G")

    # ── aib test 認得 run-short:祖先 + run id 對上才算 ──
    def test_aib_test_sees_short_inherited_only_under_run_short(self):
        probe = ("import sys;sys.path.insert(0,sys.argv[1]);import test_entry as t;"
                 "print('SHORT=%s' % t.short_inherited())")
        r = self.short([sys.executable, "-B", "-c", probe, str(TEST_ENTRY_DIR)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("SHORT=True", r.stdout)
        # short 租約活著、run id 也抄對了,但呼叫者不在 run-short 底下(不是它的子孫)⇒ 不算
        p = subprocess.Popen([sys.executable, "-B", str(TOOL), "run-short", "--owner", "t", "--what", "holder",
                              "--run-id", "short-held", "--est-s", "30", "--no-browser", "--no-fixed-port", "--",
                              "sleep", "20"], env=self.env, cwd=str(self.tree_b), stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        self.procs.append(p)
        sp = Path(str(self.lease) + ".short.json")
        t0 = time.time()
        while not sp.exists() and time.time() - t0 < 15:
            time.sleep(0.1)
        self.assertTrue(sp.exists())
        env = dict(self.env, AIB_LEASE_SHORT_RUN_ID="short-held")
        r = subprocess.run([sys.executable, "-B", "-c", probe, str(TEST_ENTRY_DIR)], env=env, capture_output=True,
                           text=True, cwd=str(self.tree_b), stdin=subprocess.DEVNULL, timeout=60)
        self.assertIn("SHORT=False", r.stdout, r.stdout + r.stderr)
        # 沒有 short 租約時也不算
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=30)
        r = subprocess.run([sys.executable, "-B", "-c", probe, str(TEST_ENTRY_DIR)], env=env, capture_output=True,
                           text=True, cwd=str(self.tree_b), stdin=subprocess.DEVNULL, timeout=60)
        self.assertIn("SHORT=False", r.stdout, r.stdout + r.stderr)


class GateInProcess(unittest.TestCase):
    """load1 / Chrome 上限:行程內換掉量測與常數。"""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-lanes-ip-"))
        self._env = dict(os.environ)
        os.environ.update({"AIB_HOST_LEASE_FILE": str(self.td / "lease.json"), "EZ2_AIB_LOCALE": "en",
                           "AIB_HOST_LEASE_HEAT_SAMPLE": str(self.td / "heat.json"),
                           "EZ2_AIB_STATE_ROOT": str(self.td / "state")})
        spec = importlib.util.spec_from_file_location("host_lease_lanes_t", TOOL)
        self.hl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.hl)
        self.path = self.td / "lease.json"
        (self.td / "heat.json").write_text(json.dumps({"throttle": 5.0, "load1": 1.0, "cpu_c": 50.0}))

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.td, ignore_errors=True)

    def test_cpu_crit_matches_cooldown(self):
        sys.path.insert(0, str(ROOT / "aib" / "src" / "cockpit"))
        try:
            import cooldown
        finally:
            sys.path.pop(0)
        self.assertEqual(self.hl.SHORT_CPU_CRIT_C, cooldown.CPU_CRIT_C)

    def test_live_measure_shape(self):
        """不用治具時現量一次:欄位都在、來源 live;讀不到就是 None(不猜)。值本身不斷言(跟機器溫度走)。"""
        env = {k: v for k, v in os.environ.items() if k != "AIB_HOST_LEASE_HEAT_SAMPLE"}
        h = self.hl.measure_heat(env)
        self.assertEqual(h["source"], "live")
        self.assertEqual(set(h), {"load1", "throttle", "cpu_c", "throttle_why", "source"})
        self.assertTrue(h["throttle"] is not None or h["throttle_why"])

    def test_long_chrome_cap(self):
        now = time.time()
        self.path.write_text(json.dumps({"schema": self.hl.SCHEMA, "owner": "f457", "what": "push", "run_id": "L",
                                         "pid": os.getpid(), "pid_start": self.hl.proc_start(os.getpid()),
                                         "host": socket.gethostname(), "cwd": "/nonexistent", "chrome": True,
                                         "acquired_epoch": int(now), "expiry_epoch": int(now + 600)}))
        why, extra = self.hl.short_gate(self.path, now, str(self.td))
        self.assertIsNone(why)                       # 預設上限 1:發
        self.assertEqual(extra["long"]["run_id"], "L")
        self.hl.SHORT_CAP_WHEN_CHROME = 0
        why, _ = self.hl.short_gate(self.path, now, str(self.td))
        self.assertEqual(why, "long_chrome")


if __name__ == "__main__":
    unittest.main()
