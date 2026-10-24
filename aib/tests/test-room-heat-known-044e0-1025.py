#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat 已知熱點表(10-25 @044e0,RULING-room-heat-local-opt 第 3 張)。全在暫存狀態根 + 假行程表跑。
N1 簽名:程式檔名 + 每秒支數門檻;低於門檻 / 不在表上 ⇒ 不標
N2 status:沒排版 ⇒ unscheduled;裝的 < fixed_in(或讀不到版號)⇒ update_available;
   ≥ fixed_in:有前後 ⇒ ≤ 7 成 improved、否則 not_improved;沒有換版前紀錄 ⇒ fixed
N3 換版流程:0.1.53 記 before ⇒ 0.1.54 記 after(掉到門檻下也記;整次沒出現記 0)⇒ 已改善;數字沒降 ⇒ 沒改善;紀錄檔 0600
N4 紀錄檔被改:代碼不在表上的丟掉、數字 / 版號不合格式的變 None
N5 churn 接上:新結果的列帶 known、整份帶 known_hot;60 秒內回快取時重新標、但不再記一筆
N6 CLI 文字模式在列尾貼「已知熱點」貼紙
刀(複本 + run_guarded):K1 改善門檻放寬到 10 倍 ⇒ N3 紅;K2 不看裝的版號 ⇒ N2 紅;K3 紀錄檔不濾代碼 ⇒ N4 紅。
"""
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
os.environ.setdefault("EZ2_AIB_LOCALE", "zh-TW")
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

import room_heat as RH  # noqa: E402
import room_heat_known as RK  # noqa: E402
import room_heat_unseen as RU  # noqa: E402


def item(program, rate, group="aib_core"):
    return {"group": group, "owner": "", "program": program, "count": int(rate * 10), "births_per_s": rate,
            "cpu_cores_lower_bound": 0.1}


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="aib-heat-known-")
        self.addCleanup(shutil.rmtree, self.d, True)
        p = mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": self.d})
        p.start()
        self.addCleanup(p.stop)

    def store(self):
        return Path(RH.state_dir()) / RK.STORE_FILE


class Rules(Base):
    def test_n1_match(self):
        self.assertEqual(RK.match(item("pane-right-tabs.sh", 35)), ("right_tabs_poll", "0.1.54"))
        self.assertEqual(RK.match(item("pane-right-tabs.sh", 3)), (None, None))
        self.assertEqual(RK.match(item("statusline-ez2-v215.sh", 6.5, "other_app")), ("statusline_cost", "0.1.55"))
        self.assertEqual(RK.match(item("seat_now.py", 5, "seat")), ("statusline_cost", "0.1.55"))
        self.assertEqual(RK.match(item("log", 2)), ("cabin_guard_logshow_dup", None))
        self.assertEqual(RK.match(item("xpane-right-tabs.sh", 99)), (None, None))
        self.assertEqual(RK.match(item("git", 50)), (None, None))

    def test_n2_status(self):
        S = RK.status_of
        self.assertEqual(S(None, "0.1.60", {}), "unscheduled")
        self.assertEqual(S("0.1.54", "0.1.53", {"before": 35, "after": 4}), "update_available")
        self.assertEqual(S("0.1.54", None, {}), "update_available")
        self.assertEqual(S("0.1.54", "0.1.9", {}), "update_available", "版號要照數字比,不是照字串")
        self.assertEqual(S("0.1.54", "0.1.54", {"before": 35.0, "after": 4.0}), "improved")
        self.assertEqual(S("0.1.54", "0.1.60", {"before": 35.0, "after": 30.0}), "not_improved")
        self.assertEqual(S("0.1.54", "0.1.54", {"before": None, "after": 4.0}), "fixed")


class Flow(Base):
    def test_n3_upgrade_flow(self):
        r = RK.annotate({"top": [item("pane-right-tabs.sh", 35.0), item("git", 40)]}, installed="0.1.53", now=1.0)
        self.assertEqual(r["top"][0]["known"], {"code": "right_tabs_poll", "fixed_in": "0.1.54", "status": "update_available"})
        self.assertNotIn("known", r["top"][1])
        self.assertEqual(stat.S_IMODE(self.store().stat().st_mode), 0o600)
        r = RK.annotate({"top": [item("pane-right-tabs.sh", 4.0)]}, installed="0.1.54", now=2.0)
        self.assertEqual(r["top"][0]["known"]["status"], "improved")
        self.assertEqual(r["known_hot"], [{"code": "right_tabs_poll", "fixed_in": "0.1.54", "status": "improved",
                                           "before": 35.0, "after": 4.0}])
        self.assertEqual(r["aib"], "0.1.54")
        r = RK.annotate({"top": [item("git", 40)]}, installed="0.1.54", now=2.5, full=[item("git", 40)])
        self.assertEqual(r["known_hot"][0]["after"], 0.0, "換版後整次沒出現沒記 0")
        r = RK.annotate({"top": [item("pane-right-tabs.sh", 30.0)]}, installed="0.1.55", now=3.0)
        self.assertEqual(r["top"][0]["known"]["status"], "not_improved")
        r = RK.annotate({"top": []}, installed="0.1.55", now=4.0)
        self.assertEqual([k["status"] for k in r["known_hot"]], ["not_improved"], "這次沒出現也要列出已知的")

    def test_n4_store_cleaned(self):
        os.makedirs(RH.state_dir(), exist_ok=True)
        self.store().write_text(json.dumps({"codes": {
            "right_tabs_poll": {"before": "35", "after": -1, "before_ver": "0.1.53; rm", "after_ver": "0.1.54", "t": 1},
            "evil_code": {"before": 1, "after": 1},
            "statusline_cost": "x"}}))
        st = RK.load()
        self.assertEqual(list(st), ["right_tabs_poll"])
        self.assertEqual(st["right_tabs_poll"], {"before": None, "after": None, "t": 1.0, "before_ver": None,
                                                 "after_ver": "0.1.54"})


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def fns():
    table = {100: (1, "tmux", 1.0), 1: (0, "launchd", 1.0)}
    n = {"c": 0}

    def pids():
        n["c"] += 1
        if n["c"] == 2:
            for k in range(60):
                table[1000 + k] = (100, "bash", 0.01)
        return list(table)
    return pids, (lambda p: table.get(p)), (lambda p: ["bash", "/x/aib/scripts/pane-right-tabs.sh", "@3"])


class Churn(Base):
    def test_n5_churn_integration(self):
        def run(now):
            c = Clock()
            return RU.churn(seconds=2, now=now, platform="linux", fns=fns(), rows=[], cls={100: ("aib_core", "")},
                            clock=c, sleep=c.sleep)
        with mock.patch.object(RK, "installed_version", lambda path=None: "0.1.53"):
            r = run(1000.0)
            self.assertEqual(r["top"][0]["known"]["code"], "right_tabs_poll")
            self.assertEqual(r["known_hot"][0]["before"], 30.0)
            before = self.store().read_text()
            r2 = run(1010.0)
            self.assertTrue(r2["throttled"])
            self.assertEqual(r2["top"][0]["known"]["status"], "update_available")
            self.assertEqual(self.store().read_text(), before, "回快取時又記了一筆")


class Cli(Base):
    def test_n6_cli_sticker(self):
        fake = {"schema": "aib-heat-churn/v1", "state": "ok", "seconds": 5, "births": 9, "births_per_s": 1.8,
                "cpu_cores_lower_bound": 0.1, "throttled": False,
                "top": [dict(item("pane-right-tabs.sh", 35.0), known={"code": "right_tabs_poll", "fixed_in": "0.1.54",
                                                                      "status": "update_available"})]}
        with mock.patch.object(RU, "churn", lambda **k: fake):
            out = io.StringIO()
            RH.main(["churn"], out=out)
        self.assertIn("更新到 0.1.54 會改善", out.getvalue())


KNIVES = [
    ("K1", "src/cockpit/room_heat_known.py", "IMPROVED_RATIO = 0.7\n", "IMPROVED_RATIO = 10\n", "Flow.test_n3_upgrade_flow"),
    ("K2", "src/cockpit/room_heat_known.py", "    if iv is None or fv is None or iv < fv:\n", "    if fv is None:\n",
     "Rules.test_n2_status"),
    ("K3", "src/cockpit/room_heat_known.py", "        if code not in CODES or not isinstance(rec, dict):\n",
     "        if not isinstance(rec, dict):\n", "Flow.test_n4_store_cleaned"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=120)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
