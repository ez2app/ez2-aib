#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib power status / upgrade(10-24 Andy「動力室準備開發」;契約 contracts/power.json)。網路、CLI、更新全換假的;暫存狀態根。

C1 status 回條的欄位 = 契約 status_receipt.fields;ai_row / health_row 欄位 = 契約;ais 順序 claude/codex/grok/copilot/agy/aib
A1 沒裝 ⇒ light off;裝了、沒有 power_login ⇒ unknown;aib 裝了 ⇒ green
A2 power_login.light_of 的值照收;它丟例外 ⇒ unknown + probe_failed,其他家不受影響
H1 健康:缺 tmux / node ⇒ ok false + hint;連不上 ⇒ net_* false;非 Mac ⇒ touchid ok null;磁碟 < 5 GB ⇒ false
H2 健康快取 10 分鐘:第二次不重量;--refresh 重量
U1 upgrade:不認得的家 ⇒ rc 2 unsupported;沒裝 ⇒ rc 2 not_installed;已最新 ⇒ rc 0 already、不叫 do_update
U2 有新版 ⇒ 叫 do_update(家, 本機, 最新);updated ⇒ rc 0 power_upgraded;rolled_back / locked / failed ⇒ rc 1 各自代碼
B1 binding 只回席碼與代碼,不回路徑 / 名稱
刀(複本):K1 light_of 例外不接 ⇒ A2 紅;K2 已最新還叫 do_update ⇒ U1 紅
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))
TMP = tempfile.mkdtemp(prefix="aib-power-test-")
os.environ["EZ2_AIB_STATE_ROOT"] = TMP
import power_status as P  # noqa: E402

CONTRACT = json.loads((REPO / "contracts" / "power.json").read_text(encoding="utf-8"))


def fake_check(rows):
    def check(refresh=False, only=None):
        return [dict(key=k, local=v[0], latest=v[1], update=bool(v[0] and v[1] and v[1] > v[0]),
                     can_update=bool(v[0] and v[1] and v[1] > v[0])) for k, v in rows.items()]
    return check


ALL = {"claude": ("1.0.0", "1.0.0"), "codex": ("0.1.0", "0.2.0"), "grok": (None, "1.0.0"), "copilot": ("1.0.0", None),
       "agy": ("1.0.0", None), "aib": ("0.1.44", "0.1.44")}


class Login:
    def __init__(self, table, boom=()):
        self.table, self.boom = table, boom

    def light_of(self, key):
        if key in self.boom:
            raise RuntimeError("probe")
        return self.table.get(key, {"light": "green"})


def healthy(**kw):
    base = dict(net=lambda url: True, which=lambda b, a=("--version",): (True, "1.0"),
                disk=lambda h: type("D", (), {"free": 50 * 1024 ** 3})(), system="Darwin", touchid=True)
    base.update(kw)
    return P.health(**base)


class T(unittest.TestCase):
    def test_c1_fields(self):
        orig, orig_h = P.ai_rows, P.health_cached
        P.health_cached = lambda refresh=False: healthy()
        P.ai_rows = lambda refresh=False: orig(refresh, check=fake_check(ALL), login=False)
        try:
            r = P.status()
        finally:
            P.ai_rows, P.health_cached = orig, orig_h
        self.assertEqual(list(r), CONTRACT["status_receipt"]["fields"])
        self.assertEqual([a["key"] for a in r["ais"]], ["claude", "codex", "grok", "copilot", "agy", "aib"])
        for a in r["ais"]:
            self.assertEqual(list(a), CONTRACT["ai_row"]["fields"])
        for h in r["health"]:
            self.assertEqual(list(h), CONTRACT["health_row"]["fields"])
        self.assertEqual(list(r["binding"]), CONTRACT["binding"]["fields"])

    def test_a1_lights_without_login(self):
        rows = {a["key"]: a for a in P.ai_rows(check=fake_check(ALL), login=False)}
        self.assertEqual(rows["grok"]["light"], "off")
        self.assertFalse(rows["grok"]["installed"])
        self.assertEqual(rows["claude"]["light"], "unknown")
        self.assertEqual(rows["aib"]["light"], "green")
        self.assertTrue(rows["codex"]["can_update"])

    def test_a2_login_values_and_failure(self):
        lg = Login({"claude": {"light": "red", "reason": "quota_reached", "reset_at": 123, "login_cmd": "claude /login"}},
                   boom=("codex",))
        rows = {a["key"]: a for a in P.ai_rows(check=fake_check(ALL), login=lg)}
        self.assertEqual((rows["claude"]["light"], rows["claude"]["reason"], rows["claude"]["reset_at"]), ("red", "quota_reached", 123))
        self.assertEqual(rows["claude"]["login_cmd"], "claude /login")
        self.assertEqual((rows["codex"]["light"], rows["codex"]["reason"]), ("unknown", "probe_failed"))
        self.assertEqual(rows["copilot"]["light"], "green")
        self.assertEqual(rows["aib"]["light"], "green")   # aib 不問 light_of

    def test_h1_health(self):
        rows = {h["key"]: h for h in healthy(which=lambda b, a=("--version",): (b not in ("tmux", "node"), "1.0"),
                                              net=lambda url: "pypi" not in url,
                                              disk=lambda h: type("D", (), {"free": 2 * 1024 ** 3})(), system="Linux")}
        self.assertFalse(rows["tmux"]["ok"])
        self.assertTrue(rows["tmux"]["hint"])
        self.assertFalse(rows["node"]["ok"])
        self.assertTrue(rows["git"]["ok"])
        self.assertFalse(rows["net_pypi"]["ok"])
        self.assertTrue(rows["net_github"]["ok"])
        self.assertIsNone(rows["touchid"]["ok"])
        self.assertFalse(rows["disk"]["ok"])

    def test_h2_cache(self):
        n = [0]

        def net(url):
            n[0] += 1
            return True
        kw = dict(net=net, which=lambda b, a=("--version",): (True, "1"), system="Linux",
                  disk=lambda h: type("D", (), {"free": 50 * 1024 ** 3})())
        P.health_cached(refresh=True, now=1000, **kw)
        first = n[0]
        P.health_cached(now=1100, **kw)
        self.assertEqual(n[0], first)
        P.health_cached(now=1100 + P.HEALTH_TTL_S, **kw)
        self.assertEqual(n[0], first * 2)
        P.health_cached(refresh=True, now=1200 + P.HEALTH_TTL_S, **kw)
        self.assertEqual(n[0], first * 3)

    def test_u1_refusals_and_already(self):
        calls = []
        do = lambda *a, **k: calls.append(a) or "updated"
        r, rc = P.upgrade("nope", check=fake_check(ALL), do_update=do)
        self.assertEqual((rc, r["detail"]), (2, "power_upgrade_unsupported"))
        r, rc = P.upgrade("grok", check=fake_check(ALL), do_update=do)
        self.assertEqual((rc, r["detail"]), (2, "power_upgrade_not_installed"))
        r, rc = P.upgrade("claude", check=fake_check(ALL), do_update=do)
        self.assertEqual((rc, r["detail"]), (0, "power_upgrade_already"))
        self.assertEqual(calls, [])

    def test_u2_update_outcomes(self):
        for res, det, want in (("updated", "power_upgraded", 0), ("rolled_back", "power_upgrade_rolled_back", 1),
                               ("locked", "power_upgrade_locked", 1), ("failed", "power_upgrade_failed", 1)):
            calls = []
            r, rc = P.upgrade("codex", check=fake_check(ALL), do_update=lambda k, o, l, out=None: calls.append((k, o, l)) or res)
            self.assertEqual(calls, [("codex", "0.1.0", "0.2.0")])
            self.assertEqual((rc, r["detail"]), (want, det))

    def test_b1_binding_no_paths(self):
        b = P.binding_state()
        self.assertEqual(set(b), {"seats", "ok", "not_ok"})
        for s in b["not_ok"]:
            self.assertEqual(set(s), {"code", "problem", "brain"})

    def test_e1_entry_dispatches_power(self):
        # aib 入口沒接到 power ⇒ 落到最後的開艙 + attach(頁面跑 status --json 會開出一座艙)(審查 r1)
        src = (REPO / "src" / "cockpit" / "aib").read_text(encoding="utf-8")
        i = src.index('if [ "${1:-}" = "power" ]; then')
        self.assertIn('  shift\n  exec "$T_PY3" "$HERE/power_status.py" "$@"', src[i:i + 200])   # 少 shift ⇒ argv[0]=power、全壞
        self.assertLess(i, src.rindex("aib_splash_stop"))                       # 擺到檔尾的開艙 + attach 之後 ⇒ 永遠到不了


def tearDownModule():
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
