#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""動力室出艦台資料(power_launch.py;契約 contracts/power.json launch_receipt / fav_receipt)。暫存狀態根。

D1 launch --list 回條欄位 = 契約;models 五家都有、claude 帶 providers.json 的預設模型
D2 人格清單:冠位人格不列;最愛在前;只回 uid / name / fav
D3 fav:不在清單 ⇒ rc 2 power_fav_unknown;加 ⇒ 最前;--off ⇒ 拿掉;重複加不重複
D4 record_launch:同一組移到最前、最多 20 筆;不認得的品牌丟 ValueError
D5 紀錄檔被亂寫(型別錯、控制字元、超長、不認得的品牌)⇒ 讀的時候濾掉、不炸
D6 aib power launch / fav 從總管 power_status.main 轉得到
D7 第二大腦清單只讀資料夾身分索引:active + 有 routeKey;退役 / 怪代號 / 重複不列;沒索引 ⇒ []
D9 model_choices:預設在前、再接之前出發用過的(去重、怪字串不列、別家不混);launch_days 記日子、最多 60 天
D10 auto:沒紀錄 ⇒ no_recent;紅 / 黃燈 ⇒ 不自動;灰燈不擋;上次的人格 / 第二大腦不在了 ⇒ persona_gone / brain_gone;怪燈號當 unknown
D8 launch --dry-run:開席 → newproj 兩步指令;怪品牌 / 模式 / 角色 / 模型(像旗標、帶分號)/ 不在清單的人格與大腦 ⇒ rc 2 bad;
   沒帶 --dry-run ⇒ rc 2 not_yet(不執行)
刀(複本):K1 不截 20 筆 ⇒ D4 紅;K2 冠位人格照列 ⇒ D2 紅;K3 模型不驗 ⇒ D8 紅;K4 黃燈照自動 ⇒ D10 紅
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
os.environ["EZ2_VAULT_DIR"] = os.path.join(TMP, "vault")
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import bindme  # noqa: E402
import power_launch as PL  # noqa: E402
import power_status as P  # noqa: E402

C = json.loads((REPO / "contracts" / "power.json").read_text(encoding="utf-8"))
PL._light = lambda brand: "unknown"          # 測試不叫真 CLI 量登入


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


def write_index(items):
    p = Path(TMP) / "vault" / ".ez2" / "index" / "folder-id-index.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")


class Launch(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(Path(TMP) / "state", ignore_errors=True)
        shutil.rmtree(Path(TMP) / "vault", ignore_errors=True)
        write_cards({"aaa111": {"name": "乙"}})

    def test_d7_brains(self):
        self.assertEqual(PL.brains(), [])
        write_index([{"folderId": "f1", "title": "全球對談", "routeKey": "proj.global-chat"},
                     {"folderId": "f2", "title": "舊", "routeKey": "proj.old", "status": "retired"},
                     {"folderId": "f3", "title": "沒代號"},
                     {"folderId": "f4", "title": "怪", "routeKey": "../etc"},
                     {"folderId": "f5", "title": "重複", "routeKey": "proj.global-chat"}, "junk"])
        self.assertEqual(PL.brains(), [{"route_key": "proj.global-chat", "title": "全球對談"}])
        self.assertEqual(PL.launch_list()["brains"], PL.brains())

    def test_d8_plan(self):
        write_index([{"folderId": "f1", "title": "x", "routeKey": "proj.x"}])
        r, rc = PL.launch_plan("codex", "gpt-6-luna", "aaa111", "proj.x")
        self.assertEqual((rc, r["detail"]), (0, "power_launch_plan"))
        self.assertEqual(list(r), C["launch_plan_receipt"]["fields"])
        self.assertEqual(r["steps"][0]["argv"], ["aib", "codex", "safe", "dev", "new", "--model", "gpt-6-luna"])
        self.assertEqual(r["steps"][1]["argv"], ["aib", "newproj", "--seat", "<new-seat>", "--persona-uid", "aaa111",
                                                 "--folder", "proj.x"])
        r, rc = PL.launch_plan("claude")
        self.assertEqual([x["step"] for x in r["steps"]], ["open"])
        for kw, why in ((dict(brand="nope"), "brand"), (dict(brand="claude", mode="yolo"), "mode"),
                        (dict(brand="claude", role="Dev;x"), "role"), (dict(brand="claude", model="--dangerous"), "model"),
                        (dict(brand="claude", model="a;rm -rf"), "model"), (dict(brand="claude", persona="zzz"), "persona"),
                        (dict(brand="claude", brain="proj.none"), "brain")):
            r, rc = PL.launch_plan(**kw)
            self.assertEqual((rc, r["detail"], r["hint"], r["steps"]), (2, "power_launch_bad", why, []), kw)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = P.main(["launch", "claude", "--json"])
        self.assertEqual((rc, json.loads(buf.getvalue())["detail"]), (2, "power_launch_not_yet"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = P.main(["launch", "claude", "--dry-run", "--json"])
        self.assertEqual((rc, json.loads(buf.getvalue())["detail"]), (0, "power_launch_plan"))

    def test_d9_choices_days(self):
        prov = json.loads((REPO / "src" / "cockpit" / "providers.json").read_text(encoding="utf-8"))["providers"]
        want = [p["default_requested_model"] for p in prov if p.get("id") == "claude"][0]
        self.assertEqual(PL.model_choices()["claude"], [want])
        PL.record_launch("claude", "m-old", now=1000)
        PL.record_launch("claude", want, now=1001)
        PL.record_launch("codex", "gx", now=1002)
        PL.record_launch("claude", "m-new", now=1003)
        got = PL.launch_list()["model_choices"]
        self.assertEqual(got["claude"], [want, "m-new", "m-old"])
        self.assertIn("gx", got["codex"])
        self.assertNotIn("gx", got["claude"])
        self.assertEqual(set(got), set(PL.BRANDS))
        d = PL._load()
        d["recent"].insert(0, {"brand": "claude", "model": "--evil x", "at": 5})
        PL._save(d)
        self.assertNotIn("--evil x", PL.model_choices()["claude"])
        days = PL.launch_days()
        self.assertEqual(len(days), 1)
        for i in range(70):
            PL.record_launch("grok", now=86400 * (20000 + i))
        days = PL.launch_days()
        self.assertEqual(len(days), PL.DAYS_MAX)
        self.assertEqual(days, sorted(days))
        d = PL._load()
        d["days"] = ["2026-10-24", "x", 3, "2026-10-24;rm"]
        PL._save(d)
        self.assertEqual(PL.launch_days(), ["2026-10-24"])

    def test_d10_auto(self):
        write_index([{"folderId": "f1", "title": "x", "routeKey": "proj.x"}])
        self.assertEqual(PL.launch_list()["auto"], {"ok": False, "why": "no_recent", "combo": None, "light": None})
        PL.record_launch("codex", "gx", "aaa111", "proj.x", now=1000)
        lights = {}
        a = PL.launch_list(light=lambda b: lights.get(b, "green"))["auto"]
        self.assertEqual((a["ok"], a["why"], a["light"], a["combo"]["brand"]), (True, None, "green", "codex"))
        for lt, want in (("red", "light_red"), ("yellow", "light_yellow"), ("unknown", None), ("evil", None)):
            lights["codex"] = lt
            a = PL.launch_list(light=lambda b: lights.get(b))["auto"]
            self.assertEqual((a["ok"], a["why"]), (want is None, want), lt)
        self.assertEqual(a["light"], "unknown")
        write_cards({"zzz999": {"name": "丙"}})
        a = PL.launch_list(light=lambda b: "green")["auto"]
        self.assertEqual((a["ok"], a["why"]), (False, "persona_gone"))
        write_cards({"aaa111": {"name": "乙"}})
        write_index([])
        a = PL.launch_list(light=lambda b: "green")["auto"]
        self.assertEqual((a["ok"], a["why"]), (False, "brain_gone"))
        PL.record_launch("grok", now=2000)                               # 沒人格 / 大腦的組合不用驗
        a = PL.launch_list(light=lambda b: "unknown")["auto"]
        self.assertEqual((a["ok"], a["combo"]["brand"]), (True, "grok"))


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, case):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/power_launch.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_no_cap(self):
        self._knife('d["recent"] = ([row] + old)[:RECENT_MAX]', 'd["recent"] = ([row] + old)', "T.test_d4_recent")

    def test_k3_model_unchecked(self):
        self._knife("if model is not None and not MODEL_RE.match(model):", "if False:", "Launch.test_d8_plan")

    def test_k4_yellow_autoruns(self):
        self._knife('    if lt in ("red", "yellow"):', '    if lt == "red":', "Launch.test_d10_auto")

    def test_k2_crown_listed(self):
        self._knife(" if _s(u) and u != crown]", " if _s(u)]", "T.test_d2_personas")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
