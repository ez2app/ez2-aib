#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""動力室燈號 / aib power login / 新手任務(power_login.py;契約 contracts/power.json)。CLI、用量紀錄全換假的;暫存狀態根。

L1 claude:auth status --json 的 loggedIn true ⇒ green;false ⇒ yellow login_missing + login_cmd;輸出怪 / 沒裝 ⇒ unknown
L2 codex:login status rc 0 ⇒ green;非 0 ⇒ yellow
L3 額度:新鮮紀錄任一窗 >= 上限 ⇒ red quota_reached,reset_at 取最晚;過期 / 99% / 別家 / 怪值不算;沒登入優先 yellow
L4 登入狀態快取 10 分鐘;refresh 重問;forget_login 清掉
L5 沒狀態指令的家(grok)⇒ unknown;額度到上限仍 red
G1 login:agy / 不認得 ⇒ rc 2 unsupported;已 green ⇒ rc 0 done 不起流程;拿到鏈結 ⇒ rc 0 url + code + 開瀏覽器;
   --no-open / claude ⇒ opened false;起不來 ⇒ rc 1 failed + hint 有本人指令
G2 spawn_login 真起假 CLI:去掉 ANSI 抓第一個 https 鏈結 + 裝置碼;只印 http:// ⇒ no_url;背景行程到時被砍
G3 _clean_url:控制字元截斷、尾巴標點去掉、超長不收
C1 回條欄位 = 契約;light_of 五格;power_status 接得上(ais 的燈號來自這支)
刀(複本):K1 沒登入不判 yellow ⇒ L1 紅;K2 不看新鮮度 ⇒ L3 紅(新手任務的測試在 test-power-tasks)
"""
import json
import os
import shutil
import signal
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (HERE, REPO / "src" / "cockpit"):
    sys.path.insert(0, str(_p))
TMP = tempfile.mkdtemp(prefix="aib-power-login-test-")
os.environ["EZ2_AIB_STATE_ROOT"] = TMP
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import power_login as L  # noqa: E402
import power_status as P  # noqa: E402
import usage_probe as UP  # noqa: E402

C = json.loads((REPO / "contracts" / "power.json").read_text(encoding="utf-8"))
NOW = 2_000_000_000


def fake_run(table):
    """table:指令開頭(tuple)→ (rc, out)。記下叫過哪些。"""
    calls = []

    def run(cmd, timeout=None):
        calls.append(tuple(cmd))
        for head, res in table.items():
            if tuple(cmd[:len(head)]) == head:
                return res
        return None, ""
    run.calls = calls
    return run


def rec(engine, window, pct, resets_at=None, age=10, status="ok"):
    obs = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - age))
    return {"schema": UP.SCHEMA, "engine": engine, "window": window, "tier": "shared", "status": status,
            "used_pct": pct, "resets_at": resets_at, "observed_at": obs}


def clear_state():
    shutil.rmtree(os.path.join(TMP, "state"), ignore_errors=True)


class Light(unittest.TestCase):
    def setUp(self):
        clear_state()

    def test_l1_claude(self):
        for out, want in (('{"loggedIn": true, "email": "x@y"}', "green"), ('{"loggedIn": false}', "yellow"),
                          ("not json", "unknown"), ('{"loggedIn": "yes"}', "unknown"), ("[1]", "unknown")):
            clear_state()
            r = L.light_of("claude", run=fake_run({("claude", "auth", "status"): (0, out)}), records=[], now=NOW)
            self.assertEqual(r["light"], want, out)
            self.assertEqual(set(r), {"light", "reason", "hint", "reset_at", "login_cmd"})
            self.assertNotIn("x@y", json.dumps(r))
            if want == "yellow":
                self.assertEqual((r["reason"], r["login_cmd"]), ("login_missing", "claude auth login"))
            if want == "green":
                self.assertIsNone(r["login_cmd"])
        clear_state()
        r = L.light_of("claude", run=fake_run({}), records=[], now=NOW)       # 沒裝 / 叫不起來
        self.assertEqual(r["light"], "unknown")

    def test_l2_codex(self):
        r = L.light_of("codex", run=fake_run({("codex", "login", "status"): (0, "")}), records=[], now=NOW)
        self.assertEqual(r["light"], "green")
        clear_state()
        r = L.light_of("codex", run=fake_run({("codex", "login", "status"): (1, "")}), records=[], now=NOW)
        self.assertEqual((r["light"], r["reason"], r["login_cmd"]), ("yellow", "login_missing", "codex login"))

    def test_l3_quota(self):
        ok = fake_run({("codex", "login", "status"): (0, "")})
        cases = (
            ([rec("codex", "7d", 100, NOW + 500), rec("codex", "5h", 100, NOW + 90)], ("red", NOW + 500)),
            ([rec("codex", "5h", 100, NOW + 90), rec("codex", "7d", 40, NOW + 9000)], ("red", NOW + 90)),
            ([rec("codex", "7d", 100, NOW + 500, age=UP.STALE_S + 5)], ("green", None)),     # 過期
            ([rec("codex", "7d", 99, NOW + 500)], ("green", None)),
            ([rec("claude", "7d", 100, NOW + 500)], ("green", None)),                        # 別家
            ([rec("codex", "7d", True, NOW + 500), rec("codex", "7d", 101), rec("codex", "7d", "100")], ("green", None)),
            ([rec("codex", "7d", 100, None)], ("red", None)),                                # 量不到恢復時間
            ([rec("codex", "7d", 100, NOW - 5)], ("red", None)),                             # 恢復時間已過 ⇒ 不給
            ([rec("codex", "7d", 100, NOW + 5, status="unmeasurable"), "junk", None], ("green", None)),
        )
        for recs, (light, reset) in cases:
            clear_state()
            r = L.light_of("codex", run=ok, records=recs, now=NOW)
            self.assertEqual((r["light"], r["reset_at"]), (light, reset), recs)
            if light == "red":
                self.assertEqual((r["reason"], r["login_cmd"]), ("quota_reached", "codex login"))
        clear_state()
        r = L.light_of("codex", run=fake_run({("codex", "login", "status"): (1, "")}),
                       records=[rec("codex", "7d", 100, NOW + 500)], now=NOW)
        self.assertEqual(r["light"], "yellow")                                               # 沒登入優先

    def test_l4_cache(self):
        run = fake_run({("codex", "login", "status"): (0, "")})
        L.light_of("codex", run=run, records=[], now=NOW)
        L.light_of("codex", run=run, records=[], now=NOW + 100)
        self.assertEqual(len(run.calls), 1)
        L.light_of("codex", run=run, records=[], now=NOW + L.LOGIN_TTL_S)
        self.assertEqual(len(run.calls), 2)
        L.light_of("codex", run=run, records=[], now=NOW + L.LOGIN_TTL_S + 1, refresh=True)
        self.assertEqual(len(run.calls), 3)
        L.forget_login("codex")
        L.light_of("codex", run=run, records=[], now=NOW + L.LOGIN_TTL_S + 2)
        self.assertEqual(len(run.calls), 4)
        L.light_of("codex", run=run, records=[], now=NOW - 5000)       # 時鐘倒退 ⇒ 不信快取
        self.assertEqual(len(run.calls), 5)

    def test_l5_no_probe(self):
        run = fake_run({})
        r = L.light_of("grok", run=run, records=[], now=NOW)
        self.assertEqual((r["light"], r["login_cmd"]), ("unknown", "grok login"))
        self.assertEqual(run.calls, [])
        r = L.light_of("grok", run=run, records=[rec("grok", "7d", 100, NOW + 60)], now=NOW)
        self.assertEqual((r["light"], r["reason"], r["reset_at"]), ("red", "quota_reached", NOW + 60))
        r = L.light_of("nope", run=run, records=[], now=NOW)
        self.assertEqual((r["light"], r["reason"]), ("unknown", "probe_failed"))


class Login(unittest.TestCase):
    def setUp(self):
        clear_state()

    def test_g1_flow(self):
        green = fake_run({("codex", "login", "status"): (0, "")})
        yellow = fake_run({("codex", "login", "status"): (1, "")})
        spawned, opened = [], []
        spawn = lambda k: spawned.append(k) or ("https://auth.example/device", "ABCD-EFGH", None)
        opener = lambda url: opened.append(url) or True
        for key in ("agy", "nope"):
            r, rc = L.login(key, spawn=spawn, opener=opener, run=green)
            self.assertEqual((rc, r["detail"]), (2, "power_login_unsupported"))
        r, rc = L.login("codex", spawn=spawn, opener=opener, run=green)
        self.assertEqual((rc, r["detail"], spawned), (0, "power_login_done", []))
        r, rc = L.login("codex", spawn=spawn, opener=opener, run=yellow)
        self.assertEqual((rc, r["detail"], r["url"], r["code"], r["opened"]),
                         (0, "power_login_url", "https://auth.example/device", "ABCD-EFGH", True))
        self.assertEqual(list(r), C["login_receipt"]["fields"])
        self.assertEqual(opened, ["https://auth.example/device"])
        r, rc = L.login("codex", no_open=True, spawn=spawn, opener=opener, run=yellow)
        self.assertFalse(r["opened"])
        self.assertEqual(len(opened), 1)
        r, rc = L.login("codex", force=True, spawn=spawn, opener=opener, run=green)       # 已登入照起
        self.assertEqual(r["detail"], "power_login_url")
        r, rc = L.login("claude", spawn=spawn, opener=opener,
                        run=fake_run({("claude", "auth", "status"): (0, '{"loggedIn": false}')}))
        self.assertEqual((r["detail"], r["opened"]), ("power_login_url", False))         # claude 自己開
        self.assertEqual(len(opened), 2)
        r, rc = L.login("codex", spawn=lambda k: (None, None, "no_url"), opener=opener, run=yellow)
        self.assertEqual((rc, r["detail"], r["url"]), (1, "power_login_failed", None))
        self.assertIn("codex login", r["hint"])
        self.assertIn("no_url", r["hint"])

    def _fake_cli(self, name, body):
        d = Path(tempfile.mkdtemp(prefix="aib-power-bin-", dir=TMP))
        p = d / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        return d

    def _with_path(self, d):
        old = os.environ["PATH"]
        os.environ["PATH"] = str(d) + os.pathsep + old
        self.addCleanup(os.environ.__setitem__, "PATH", old)

    def _popen(self):
        """包一層 Popen:記下自己起的行程組,測完只殺自己那一組(start_new_session ⇒ pgid = pid)。"""
        import subprocess

        def popen(*a, **kw):
            proc = subprocess.Popen(*a, **kw)
            self.addCleanup(self._reap, proc)
            return proc
        return popen

    @staticmethod
    def _reap(proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(5)
        except Exception:  # noqa: BLE001
            pass

    def test_g2_spawn_real(self):
        d = self._fake_cli("codex", "printf '\\033[1mOpen\\033[0m https://auth.openai.com/codex/device.\\n'\n"
                                    "printf 'code: WXYZ-1234\\n'\nsleep 30\n")
        self._with_path(d)
        url, code, err = L.spawn_login("codex", wait_s=10, lifetime=3, popen=self._popen())
        self.assertEqual((url, code, err), ("https://auth.openai.com/codex/device", "WXYZ-1234", None))
        log = Path(TMP) / "state" / "power-login-codex.log"
        self.assertFalse(log.exists(), "拿到鏈結 / 裝置碼後登入紀錄要刪掉")   # #293 審查:不留在狀態夾
        deadline = time.monotonic() + 15                     # 守門行程 3 秒後砍掉假 CLI
        while time.monotonic() < deadline and self._alive(d):
            time.sleep(0.3)
        self.assertFalse(self._alive(d), "背景登入流程沒有到時被砍")

        d2 = self._fake_cli("grok", "echo 'see http://insecure.example/x'\nsleep 5\n")
        self._with_path(d2)
        url, code, err = L.spawn_login("grok", wait_s=1.5, lifetime=2, popen=self._popen())
        self.assertEqual((url, err), (None, "no_url"))
        os.environ["PATH"] = "/nonexistent"
        self.assertEqual(L.spawn_login("copilot", wait_s=1)[2], "cli_missing")

    def _alive(self, d):
        import subprocess
        out = subprocess.run(["ps", "-axo", "pid,command"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            if str(d) in line:
                return True
        return False

    def test_g3_clean_url(self):
        self.assertEqual(L._clean_url("go https://a.example/x?y=1). next"), "https://a.example/x?y=1")
        self.assertEqual(L._clean_url("https://a.example/\x07evil"), "https://a.example/")
        self.assertEqual(L._clean_url("\x1b]8;;https://bad\x07https://good.example\x1b]8;;\x07"), "https://good.example")
        self.assertIsNone(L._clean_url("https://" + "a" * 3000))
        self.assertIsNone(L._clean_url("http://plain.example"))
        self.assertIsNone(L._clean_url(None))


class Contract(unittest.TestCase):
    def setUp(self):
        clear_state()

    def test_c1_status_wiring(self):
        def check(refresh=False, only=None):
            return [{"key": k, "local": "1.0", "latest": "1.0"} for k in ("claude", "codex", "grok", "copilot", "agy", "aib")]
        orig = L._run
        L._run = fake_run({("claude", "auth", "status"): (0, '{"loggedIn": false}'), ("codex", "login", "status"): (0, "")})
        try:
            got = {a["key"]: a for a in P.ai_rows(check=check)}
        finally:
            L._run = orig
        self.assertEqual(got["claude"]["light"], "yellow")
        self.assertEqual(got["codex"]["light"], "green")
        self.assertEqual(got["grok"]["light"], "unknown")
        self.assertEqual(got["aib"]["light"], "green")
        for a in got.values():
            self.assertEqual(list(a), C["ai_row"]["fields"])
        for light in ("off", "green", "yellow", "red", "unknown"):
            self.assertIn(light, C["ai_row"]["light"])
        for reason in ("login_missing", "quota_reached", "probe_failed"):
            self.assertIn(reason, C["ai_row"]["reason"])


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, case, rel="src/cockpit/power_login.py"):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_no_yellow(self):
        self._knife("    if li is False:\n", "    if False:\n", "Light.test_l1_claude")

    def test_k2_no_freshness(self):
        self._knife("if age is None or age > UP.STALE_S or pct < UP.USAGE_BLOCK_PCT:", "if pct < UP.USAGE_BLOCK_PCT:",
                    "Light.test_l3_quota")



def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
