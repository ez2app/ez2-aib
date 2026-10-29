#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""自動更新 d 張(10-29 @aeb40 派,@ee842):付費外掛一直 plugin_unsupported、看不出誰在等、碰上租約整輪放棄 6 小時。

D1 測試租約 / aib test 佔著 ⇒ 照樣 fetch、比版號、查外掛來源、寫狀態(have / latest / plugins_manual_behind);
   不拿租約、不換檔,結果 refused_lease / refused_tests
D2 本人 aib update / 推包 / git push 在跑 ⇒ 跟以前一樣整輪不查(不 fetch)
D3 佔著但沒東西要換 ⇒ up_to_date(不是 refused_lease,不必重試)
D4 付費外掛落後 ⇒ 紀錄多一行 plugins_manual_behind=<名>;沒落後 ⇒ 沒這行
D5 status:走 git 且碼是 plugin_unsupported ⇒ 多一行「ez2.page 還沒開外掛下載」;走目錄 / 別的碼 ⇒ 沒這行
D6 排程檔每 30 分醒(--why tick):上次 up_to_date / updated / no_change 且 6 小時內查過 ⇒ checked_recently、不 fetch、不寫紀錄;
   上次被擋(refused_lease 等)或滿 6 小時 ⇒ 照排程查(紀錄 why=schedule)
D7 舊排程檔(每 6 小時、--why schedule)/ 讀不懂 ⇒ agent_stale;開席那次重裝成新的;新的不重裝
D8 四語都有新鍵 status.plugin_unsupported,且沒有 !!
D9 (#424 R1 M1)開席碰上舊排程檔、但鎖被拿著(舊排程正在查 / 跑 install.sh)⇒ 不叫 launchctl(不 bootout 砍掉在跑的工作),
   結果 busy;鎖放開後下一次開席才換。沒裝過排程照舊開席就裝
D10 (#424 R1 M2)30 分重試只給白名單 refused_lease / refused_tests / offline;failed / plugins_failed / refused_dirty /
   refused_diverged / paused / no_ref / too_soon 等 ⇒ 6 小時內 tick 一律 checked_recently(不 fetch、不重跑 install.sh)
D11 (#424 R1 L1 / L2)why.refused_tests 跟 refused_lease 同樣寫 30 分;排程檔還是舊的 ⇒ status 多 schedule_stale 與一行提示
"""
import importlib.util
import io
import json
import plistlib
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("au_base_74e8b", str(HERE / "test-auto-update-74e8b-1023.py"))
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)
AU, NOW = B.AU, B.NOW


def _log(env):
    p = AU.log_path(env)
    return p.read_text(encoding="utf-8") if p.exists() else ""


class Busy(B.Base):
    paid_repo = B.PaidSource.paid_repo        # 只借造倉的工具,不繼承那邊的測試(免得重跑一遍)

    def _fetch_spy(self):
        called = []
        return called, (lambda r, e: called.append(1) or AU.fetch(r, e))

    def test_d1_lease_held_still_checks(self):
        self.paid_repo({"demo-paid": "1.0.3"})
        self.make_repos()
        import aib_ext
        for busy in ("lease", "tests"):
            called, spy = self._fetch_spy()
            self.leases, self.updates = [], []
            with mock.patch.object(aib_ext, "signed_ok", return_value=True):
                res = self.run_check(lease_state=lambda e, n, b=busy: b, do_fetch=spy,
                                     plugin_fetch=AU.fetch_plugin_source, now=NOW + (0 if busy == "lease" else 7200))
            self.assertEqual(res["result"], "refused_" + busy, res)
            self.assertEqual(called, [1])                               # 查了
            self.assertEqual((self.leases, self.updates), ([], []))     # 沒拿租約、沒換
            self.assertEqual((res["have"], res["latest"]), ("0.1.1", "0.1.2"))
            st = AU.read_status(self.env)
            self.assertEqual((st["latest"], st["plugins_manual_behind"], st["last_result"]),
                             ("0.1.2", ["demo-paid"], "refused_" + busy))
        # 租約空了 ⇒ 下一次照常換
        res = self.run_check(now=NOW + 99999)
        self.assertEqual(res["result"], "updated", res)
        self.assertEqual(self.leases, ["acquire", "release"])

    def test_d2_own_checkout_busy_skips_fetch(self):
        self.make_repos()
        for busy in ("updating", "shipping", "push"):
            called, spy = self._fetch_spy()
            res = self.run_check(lease_state=lambda e, n, b=busy: b, do_fetch=spy)
            self.assertEqual((res["result"], called, self.updates), ("refused_" + busy, [], []))

    def test_d3_busy_but_nothing_to_swap(self):
        self.make_repos(latest=None)
        res = self.run_check(lease_state=lambda e, n: "lease")
        self.assertEqual(res["result"], "up_to_date", res)
        self.assertEqual(self.leases, [])

    def test_d4_manual_behind_logged(self):
        self.make_repos(latest=None)
        self.run_check(plugin_fetch=lambda e: None)
        self.assertNotIn("plugins_manual_behind=", _log(self.env))
        self.paid_repo({"demo-paid": "1.0.3", "other-paid": "1.0.1"})
        import aib_ext
        with mock.patch.object(aib_ext, "signed_ok", return_value=True):
            res = self.run_check(now=NOW + 7200, plugin_fetch=AU.fetch_plugin_source)
        self.assertEqual(sorted(res["plugins_manual_behind"]), ["demo-paid", "other-paid"])
        lines = [x for x in _log(self.env).splitlines() if "plugins_manual_behind=" in x]
        self.assertEqual(len(lines), 1, _log(self.env))
        self.assertIn("demo-paid", lines[0])
        self.assertIn("other-paid", lines[0])


class Hint(B.Base):
    def lines(self, **st):
        AU.write_status(st, self.env)
        return "\n".join(AU.status_lines(AU.status_payload(self.env, platform="linux", now=NOW)))

    def test_d5_unsupported_hint(self):
        hint = "還沒開外掛下載"
        self.assertIn(hint, self.lines(plugin_via="git", plugin_via_url="git@x:y.git", plugin_problem="plugin_unsupported",
                                       plugin_source="ok", last_check=int(NOW)))
        self.assertNotIn(hint, self.lines(plugin_via="git", plugin_via_url="git@x:y.git", plugin_problem="plugin_unreachable",
                                          plugin_source="ok", last_check=int(NOW)))
        self.assertNotIn(hint, self.lines(plugin_via="catalog", plugin_via_url="https://ez2.page/api", plugin_problem="",
                                          plugin_source="ok", last_check=int(NOW)))


class Tick(B.Base):
    def test_d6_tick_gate(self):
        self.make_repos(latest=None)
        called = []
        spy = lambda r, e: called.append(1) or AU.fetch(r, e)   # noqa: E731
        for settled in ("up_to_date", "updated", "no_change"):
            AU.write_status({"last_check": int(NOW - 3600), "last_result": settled}, self.env)
            before = _log(self.env)
            res = self.run_check(why="tick", do_fetch=spy)
            self.assertEqual((res["result"], called), ("checked_recently", []), settled)
            self.assertEqual(_log(self.env), before)                       # 不寫紀錄
        AU.write_status({"last_check": int(NOW - 3600), "last_result": "refused_lease"}, self.env)
        res = self.run_check(why="tick", do_fetch=spy)
        self.assertEqual((res["result"], res["why"], called), ("up_to_date", "schedule", [1]))
        self.assertIn("why=schedule result=up_to_date", _log(self.env))
        AU.write_status({"last_check": int(NOW - AU.INTERVAL_S), "last_result": "up_to_date"}, self.env)
        res = self.run_check(why="tick", do_fetch=spy)
        self.assertEqual((res["result"], called), ("up_to_date", [1, 1]))
        # 一般 schedule(手動 / 舊排程檔)不受這道閘影響
        AU.write_status({"last_check": int(NOW - 60), "last_result": "up_to_date"}, self.env)
        self.assertEqual(self.run_check(why="schedule", do_fetch=spy)["result"], "up_to_date")


class Stale(B.Base):
    def test_d7_stale_agent_reinstalled(self):
        path = AU.agent_plist_path(self.home)
        path.parent.mkdir(parents=True)
        old = dict(AU.agent_plist(self.env), StartInterval=6 * 3600)
        old["ProgramArguments"] = old["ProgramArguments"][:-1] + ["schedule"]
        path.write_bytes(plistlib.dumps(old))
        self.assertTrue(AU.agent_stale(self.home))
        path.write_text("not a plist")
        self.assertTrue(AU.agent_stale(self.home))
        path.write_bytes(plistlib.dumps(old))
        self.make_repos(latest=None)
        ctl = B.FakeCtl()
        self.run_check(why="seat-open", platform="darwin", launchctl=ctl)
        self.assertEqual([c[0] for c in ctl.calls], ["bootout", "bootstrap"])
        self.assertFalse(AU.agent_stale(self.home))
        with open(path, "rb") as fh:
            d = plistlib.load(fh)
        self.assertEqual((d["StartInterval"], d["ProgramArguments"][-1]), (AU.RETRY_S, "tick"))
        ctl2 = B.FakeCtl()
        self.run_check(why="seat-open", platform="darwin", launchctl=ctl2, now=NOW + 7200)
        self.assertEqual(ctl2.calls, [])                                   # 新的不重裝


class StaleLocked(B.Base):
    def _old_plist(self):
        path = AU.agent_plist_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        old = dict(AU.agent_plist(self.env), StartInterval=6 * 3600)
        old["ProgramArguments"] = old["ProgramArguments"][:-1] + ["schedule"]
        path.write_bytes(plistlib.dumps(old))
        return path

    def test_d9_stale_but_locked_keeps_running_job(self):
        self._old_plist()
        self.make_repos(latest=None)
        ctl = B.FakeCtl()
        with AU._lock(self.env) as got:                      # 舊排程正在跑(拿著鎖)
            self.assertTrue(got)
            res = self.run_check(why="seat-open", platform="darwin", launchctl=ctl)
        self.assertEqual((res["result"], ctl.calls), ("busy", []))
        self.assertNotIn("agent", res)
        self.assertTrue(AU.agent_stale(self.home))
        ctl2 = B.FakeCtl()
        self.run_check(why="seat-open", platform="darwin", launchctl=ctl2)
        self.assertEqual([c[0] for c in ctl2.calls], ["bootout", "bootstrap"])
        self.assertFalse(AU.agent_stale(self.home))

    def test_d9_missing_agent_still_installed_on_open(self):
        self.make_repos(latest=None)
        ctl = B.FakeCtl()
        res = self.run_check(why="seat-open", platform="darwin", launchctl=ctl)
        self.assertEqual(res.get("agent"), "installed")
        self.assertEqual([c[0] for c in ctl.calls], ["bootout", "bootstrap"])


class RetryWhitelist(B.Base):
    def test_d10_only_whitelist_retries_soon(self):
        self.make_repos(latest=None)
        called = []
        spy = lambda r, e: called.append(1) or AU.fetch(r, e)   # noqa: E731
        for last in ("failed", "plugins_failed", "refused_dirty", "refused_diverged", "refused_branch",
                     "refused_far_behind", "paused", "no_ref", "too_soon", "refused_update", "", None):
            AU.write_status({"last_check": int(NOW - 3600), "last_result": last}, self.env)
            res = self.run_check(why="tick", do_fetch=spy)
            self.assertEqual((res["result"], called), ("checked_recently", []), last)
        for last in ("refused_lease", "refused_tests", "offline"):
            called.clear()
            AU.write_status({"last_check": int(NOW - 3600), "last_result": last}, self.env)
            res = self.run_check(why="tick", do_fetch=spy)
            self.assertEqual((res["result"], called), ("up_to_date", [1]), last)
        called.clear()                                         # 滿 6 小時:不管上次是什麼都查
        AU.write_status({"last_check": int(NOW - AU.INTERVAL_S), "last_result": "failed"}, self.env)
        self.assertEqual((self.run_check(why="tick", do_fetch=spy)["result"], called), ("up_to_date", [1]))

    def test_d10_failed_install_not_rerun_hourly(self):
        AU.write_status({"last_check": int(NOW), "last_attempt": int(NOW), "last_result": "failed"}, self.env)
        self.make_repos()
        ran = []
        for k in range(1, 12):                                 # 30 分一醒,醒 11 次(5.5 小時)
            self.run_check(why="tick", now=NOW + k * AU.RETRY_S, run_update=lambda *a, **kw: ran.append(1) or (1, ""))
        self.assertEqual(ran, [])


class StaleHint(B.Base):
    def test_d11_stale_schedule_hint(self):
        path = AU.agent_plist_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        old = dict(AU.agent_plist(self.env), StartInterval=6 * 3600)
        old["ProgramArguments"] = old["ProgramArguments"][:-1] + ["schedule"]
        path.write_bytes(plistlib.dumps(old))
        p = AU.status_payload(self.env, home=self.home, platform="darwin", now=NOW)
        self.assertEqual((p["schedule"], p["schedule_stale"]), ("installed", True))
        AU.agent_plist_path(self.home).write_bytes(plistlib.dumps(AU.agent_plist(self.env)))
        p = AU.status_payload(self.env, home=self.home, platform="darwin", now=NOW)
        self.assertEqual((p["schedule"], p["schedule_stale"]), ("installed", False))
        self.assertFalse(AU.status_payload(self.env, home=self.home, platform="linux", now=NOW)["schedule_stale"])


class Dict(unittest.TestCase):
    def test_d8_four_langs(self):
        root = HERE.parent / "i18n"
        for lang in ("en", "ja", "ko", "zh-TW"):
            d = json.loads((root / (lang + ".json")).read_text(encoding="utf-8"))
            v = d.get("aib.auto_update.status.plugin_unsupported")
            self.assertTrue(v and "!!" not in v and "plugin_unsupported" in v, lang)
            self.assertIn("30", d["aib.auto_update.why.refused_lease"], lang)
            self.assertIn("30", d["aib.auto_update.why.refused_tests"], lang)
            v = d.get("aib.auto_update.status.schedule_stale")
            self.assertTrue(v and "!!" not in v and "6" in v and "30" in v, lang)


if __name__ == "__main__":
    unittest.main(verbosity=1)
