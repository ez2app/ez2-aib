#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""動力室艦員檔案:為什麼用 AIB 排名(power_crew.py;契約 contracts/power.json crew_receipt;SPEC-power-room §13)。暫存狀態根。

W1 沒設過 ⇒ why_rank [];回條欄位 = 契約;代碼 = 定案頁 16 項
W2 set:逗號順序 = 排名、重複只留第一次;other 只在選了 other 才留、去控制字元、最多 60 字
W3 不認得的代碼 / 空 / 超過 5 項 ⇒ rc 2 power_why_bad,檔不動;sent 一律 false(伺服器沒上線)
W4 clear ⇒ 回到沒設過;檔被亂寫(型別錯、怪代碼)⇒ 讀的時候濾掉、不炸
W5 aib power why / set / clear 從總管 power_status.main 轉得到
刀(複本):K1 不去重 ⇒ W2 紅;K2 怪代碼照存 ⇒ W3 紅;K3 不擋超過 5 項 ⇒ W3 紅
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
for _p in (HERE, REPO / "src" / "cockpit"):
    sys.path.insert(0, str(_p))
TMP = tempfile.mkdtemp(prefix="aib-power-crew-test-")
os.environ["EZ2_AIB_STATE_ROOT"] = TMP
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import power_crew as W  # noqa: E402
import power_status as P  # noqa: E402

C = json.loads((REPO / "contracts" / "power.json").read_text(encoding="utf-8"))
FILE = Path(TMP) / "state" / "power-crew.json"


class Why(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(Path(TMP) / "state", ignore_errors=True)

    def test_w1_empty(self):
        r = W.why()
        self.assertEqual(r, {"ok": True, "detail": "power_why", "why_rank": [], "other": None, "at": None, "sent": False})
        self.assertEqual(list(r), C["crew_receipt"]["fields"])
        self.assertEqual(len(W.WHY), 16)
        self.assertEqual(W.WHY[-1], "other")
        for k in W.WHY:
            self.assertIn(k, C["crew_receipt"]["why_rank"])

    def test_w2_set(self):
        r, rc = W.set_why(["coding", "memory", "coding", " phone ", "geo_seo", "warroom"], other="不該留", now=1000)
        self.assertEqual((rc, r["detail"], r["why_rank"], r["other"], r["at"]),
                         (0, "power_why_set", ["coding", "memory", "phone", "geo_seo", "warroom"], None, 1000))
        self.assertIsNone(json.loads(FILE.read_text(encoding="utf-8"))["other"])
        r, rc = W.set_why(["other", "art"], other="  想\x1b[31m 養\u0085貓" + "長" * 100)
        self.assertEqual(r["why_rank"], ["other", "art"])
        self.assertTrue(r["other"].startswith("想 養貓"))                       # ANSI 整段去掉(同 power_ask._clean)
        self.assertEqual(len(r["other"]), W.OTHER_MAX)
        self.assertEqual(W.why()["why_rank"], ["other", "art"])

    def test_w3_bad(self):
        W.set_why(["room"], now=5)
        before = FILE.read_bytes()
        for items in (["room", "evil"], [], [""], ["../x"], ["ROOM"], ["memory", "coding", "erp", "video", "art", "save"]):
            r, rc = W.set_why(items)
            self.assertEqual((rc, r["ok"], r["detail"], r["why_rank"]), (2, False, "power_why_bad", ["room"]), items)
        self.assertEqual(FILE.read_bytes(), before)

    def test_w4_clear_tampered(self):
        W.set_why(["save"])
        r, rc = W.clear()
        self.assertEqual((rc, r["detail"], r["why_rank"], r["at"]), (0, "power_why_cleared", [], None))
        FILE.write_text(json.dumps({"why_rank": ["evil", "art", "art", 3, None], "other": 5, "at": True}),
                        encoding="utf-8")
        self.assertEqual(W.why(), {"ok": True, "detail": "power_why", "why_rank": ["art"], "other": None, "at": None,
                                   "sent": False})
        FILE.write_text(json.dumps({"why_rank": list(W.WHY)}), encoding="utf-8")
        self.assertEqual(W.why()["why_rank"], list(W.WHY[:5]))           # 檔被塞超過 5 項 ⇒ 只取前 5
        FILE.write_text(json.dumps({"why_rank": ["other"], "other": "x\ny"}), encoding="utf-8")
        self.assertEqual(W.why()["other"], "xy")
        FILE.write_text("[1]", encoding="utf-8")
        self.assertEqual(W.why()["why_rank"], [])

    def test_w5_dispatch(self):
        def run(argv):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = P.main(argv)
            return rc, json.loads(buf.getvalue())
        rc, r = run(["why", "set", "phone,other", "--other", "遠端", "--json"])
        self.assertEqual((rc, r["why_rank"], r["other"]), (0, ["phone", "other"], "遠端"))
        self.assertEqual(run(["why", "--json"])[1]["why_rank"], ["phone", "other"])
        self.assertEqual(run(["why", "set", "nope", "--json"])[0], 2)
        self.assertEqual(run(["why", "clear", "--json"])[1]["why_rank"], [])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(P.main(["why", "zap"]), 2)


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, case):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/power_crew.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_no_dedupe(self):
        self._knife("        if k in WHY and k not in out:", "        if k in WHY:", "Why.test_w2_set")

    def test_k2_bad_saved(self):
        self._knife("    if bad or not items or len(set(items)) > RANK_MAX:", "    if not items:", "Why.test_w3_bad")

    def test_k3_no_cap(self):
        self._knife(" or len(set(items)) > RANK_MAX:", ":", "Why.test_w3_bad")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
