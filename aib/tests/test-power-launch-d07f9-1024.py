#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""動力室出艦台資料(power_launch.py;契約 contracts/power.json launch_receipt / fav_receipt)。暫存狀態根。

D1 launch --list 回條欄位 = 契約;models 五家都有、claude 帶 providers.json 的預設模型
D2 人格清單:冠位人格不列;最愛在前;只回 uid / name / fav
D3 fav:不在清單 ⇒ rc 2 power_fav_unknown;加 ⇒ 最前;--off ⇒ 拿掉;重複加不重複
D4 record_launch:同一組移到最前、最多 20 筆;不認得的品牌丟 ValueError
D5 紀錄檔被亂寫(型別錯、控制字元、超長、不認得的品牌)⇒ 讀的時候濾掉、不炸
D6 aib power launch / fav 從總管 power_status.main 轉得到
刀(複本):K1 不截 20 筆 ⇒ D4 紅;K2 冠位人格照列 ⇒ D2 紅
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (HERE, REPO / "src" / "cockpit", REPO / "src" / "identity"):
    sys.path.insert(0, str(_p))
TMP = tempfile.mkdtemp(prefix="aib-power-launch-test-")
os.environ["EZ2_AIB_STATE_ROOT"] = TMP
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import bindme  # noqa: E402
import power_launch as PL  # noqa: E402
import power_status as P  # noqa: E402

C = json.loads((REPO / "contracts" / "power.json").read_text(encoding="utf-8"))


def write_cards(cards):
    p = Path(TMP) / "identity" / "cards.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"cards": cards}, ensure_ascii=False), encoding="utf-8")


class T(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(Path(TMP) / "state", ignore_errors=True)
        write_cards({"aaa111": {"name": "乙"}, "bbb222": {"name": "甲"}, bindme.CROWN_PERSONA: {"name": "冠位"}})

    def test_d1_list(self):
        r = PL.launch_list()
        self.assertEqual(list(r), C["launch_receipt"]["fields"])
        self.assertEqual(set(r["models"]), set(PL.BRANDS))
        prov = json.loads((REPO / "src" / "cockpit" / "providers.json").read_text(encoding="utf-8"))["providers"]
        want = [p["default_requested_model"] for p in prov if p.get("id") == "claude"][0]
        self.assertEqual(r["models"]["claude"], want)

    def test_d2_personas(self):
        rows = PL.personas()
        self.assertNotIn(bindme.CROWN_PERSONA, [r["uid"] for r in rows])
        self.assertEqual([r["name"] for r in rows], ["乙", "甲"] if "乙" < "甲" else ["甲", "乙"])
        PL.set_fav("aaa111")
        rows = PL.launch_list()["personas"]
        self.assertEqual(rows[0], {"uid": "aaa111", "name": "乙", "fav": True})
        self.assertTrue(all(set(r) == {"uid", "name", "fav"} for r in rows))

    def test_d3_fav(self):
        r, rc = PL.set_fav("nope")
        self.assertEqual((rc, r["detail"]), (2, "power_fav_unknown"))
        r, rc = PL.set_fav(bindme.CROWN_PERSONA)
        self.assertEqual(rc, 2)
        PL.set_fav("aaa111")
        r, rc = PL.set_fav("bbb222")
        self.assertEqual((rc, r["favorites"]), (0, ["bbb222", "aaa111"]))
        r, rc = PL.set_fav("aaa111")
        self.assertEqual(r["favorites"], ["aaa111", "bbb222"])
        r, rc = PL.set_fav("bbb222", on=False)
        self.assertEqual((rc, r["detail"], r["favorites"]), (0, "power_fav_cleared", ["aaa111"]))
        self.assertEqual(list(r), C["fav_receipt"]["fields"])

    def test_d4_recent(self):
        for i in range(25):
            PL.record_launch("codex", "m%d" % i, "aaa111", "proj.x", now=1000 + i)
        rows = PL.recent()
        self.assertEqual(len(rows), PL.RECENT_MAX)
        self.assertEqual(len(PL._load()["recent"]), PL.RECENT_MAX)          # 檔裡也只留 20 筆
        self.assertEqual(rows[0]["model"], "m24")
        PL.record_launch("codex", "m10", "aaa111", "proj.x", now=5000)
        rows = PL.recent()
        self.assertEqual((rows[0]["model"], rows[0]["at"]), ("m10", 5000))
        self.assertEqual([r["model"] for r in rows].count("m10"), 1)
        self.assertEqual(len(rows), PL.RECENT_MAX)
        with self.assertRaises(ValueError):
            PL.record_launch("nope")

    def test_d5_tampered(self):
        p = Path(TMP) / "state" / "power-launch.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"recent": [{"brand": "evil"}, "x", {"brand": "claude", "model": "a\x1bb", "at": True,
                                                                       "persona": "p" * 500}],
                                 "favorites": ["ok1", 3, "x\ny", None]}), encoding="utf-8")
        rows = PL.recent()
        self.assertEqual(rows, [{"brand": "claude", "model": None, "persona": None, "brain": None, "at": None}])
        self.assertEqual(PL.favorites(), ["ok1"])
        p.write_text("[1,2]", encoding="utf-8")
        self.assertEqual(PL.launch_list()["recent"], [])

    def test_d6_dispatch(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = P.main(["launch", "--list", "--json"])
        self.assertEqual((rc, json.loads(buf.getvalue())["detail"]), (0, "power_launch_list"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = P.main(["fav", "nope", "--json"])
        self.assertEqual((rc, json.loads(buf.getvalue())["detail"]), (2, "power_fav_unknown"))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(P.main(["launch"]), 2)


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, case):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/power_launch.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_no_cap(self):
        self._knife('d["recent"] = ([row] + old)[:RECENT_MAX]', 'd["recent"] = ([row] + old)', "T.test_d4_recent")

    def test_k2_crown_listed(self):
        self._knife(" if _s(u) and u != crown]", " if _s(u)]", "T.test_d2_personas")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
