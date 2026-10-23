#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""動力室任務板(power_tasks.py;契約 contracts/power.json onboarding / tier_receipt / redeem_receipt)。暫存狀態根。

T1 回條欄位 = 契約;R12 / SR13 / SSR12 / UR7 共 44 條、編號不重複;每條欄位齊、reward null
T2 偵測到 ⇒ done,之後偵測不到也維持 done(席收掉不會變回未完成);偵測炸了不拖垮
T3 舊 id first_seat / bind_seat / first_handoff ⇒ 搬到 R3 / R5 / R9 並寫回檔;不認得的 id / 怪值丟掉
T4 等級開關:關 ⇒ on false 但偵測照記;不認得的等級 ⇒ rc 2 power_tier_unknown;tiers / all 的數字對得上
T5 SSR 全做完 ⇒ UR7 自動 done
T6 streak:以今天或昨天結尾的連續日;斷一天歸零;怪字串跳過
T7 detect:aib 版本比第一次看到的新 ⇒ aib_upgraded;第一次只記 first_version;版本號怪 ⇒ 不算
T8 detect:座位 / 人格 / 第二大腦 / 兩家以上在線 / 交接單(自動存的不算)/ 連續出發
T9 redeem:讀 stdin 一行、不回顯、rc 2 power_redeem_not_yet;aib power tier / redeem 從總管 power_status.main 轉得到
T10 detectable:沒有偵測器的條 = false(頁面照實標)
T11 where(SPEC §14):動力室 30 條 = R1–12、SR1–7 + 12–13、SSR2–5 + 11、UR4–7;其餘 14 條 room;tiers / all 只算 power、room 另算
刀(複本):K1 done 不記住 ⇒ T2 紅;K2 舊 id 不搬 ⇒ T3 紅;K3 關等級連偵測都停 ⇒ T4 紅;K4 艦長室的條照列動力室 ⇒ T11 紅
"""
import datetime
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
TMP = tempfile.mkdtemp(prefix="aib-power-tasks-test-")
os.environ["EZ2_AIB_STATE_ROOT"] = TMP
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import power_status as P  # noqa: E402
import power_tasks as PT  # noqa: E402

C = json.loads((REPO / "contracts" / "power.json").read_text(encoding="utf-8"))
NOW = 2_000_000_000
TODAY = datetime.date(2026, 10, 24)


def memo():
    try:
        return json.loads((Path(TMP) / "state" / "power-onboarding.json").read_text(encoding="utf-8"))
    except OSError:
        return {}


def by_id(st):
    return {t["id"]: t for t in st["tasks"]}


class Tasks(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(Path(TMP) / "state", ignore_errors=True)
        shutil.rmtree(Path(TMP) / "pending", ignore_errors=True)

    def test_t1_shape(self):
        st = PT.onboarding_state(detector=lambda r, m, t: {}, now=NOW)
        self.assertEqual(list(st), C["onboarding"]["fields"])
        ids = [t["id"] for t in st["tasks"]]
        self.assertEqual(len(ids), len(set(ids)))
        count = {k: sum(1 for t in st["tasks"] if t["tier"] == k) for k in PT.TIERS}
        self.assertEqual(count, {"R": 12, "SR": 13, "SSR": 12, "UR": 7})
        for t in st["tasks"]:
            self.assertEqual(set(t), {"id", "tier", "where", "state", "reward", "detect", "detectable", "requires"})
            self.assertIsNone(t["reward"])
            self.assertIn(t["requires"], (None, "room", "server", "skins"))
        self.assertEqual(st["all"], {"done": 0, "total": 30})
        self.assertEqual(st["room"], {"done": 0, "total": 14})

    def test_t2_sticky(self):
        st = PT.onboarding_state(detector=lambda r, m, t: {"seat_registered": True, "born": False}, now=NOW)
        self.assertEqual(by_id(st)["R3"]["state"], "done")
        self.assertEqual(by_id(st)["R2"]["state"], "todo")                 # False 不算
        self.assertEqual(memo()["done"]["R3"], NOW)
        st = PT.onboarding_state(detector=lambda r, m, t: {}, now=NOW + 99)
        self.assertEqual(by_id(st)["R3"]["state"], "done")                 # 席收掉了也還是 done
        self.assertEqual(memo()["done"]["R3"], NOW)                         # 時間不被蓋

        def boom(r, m, t):
            raise RuntimeError("x")
        st = PT.onboarding_state(detector=boom, now=NOW)
        self.assertEqual(by_id(st)["R3"]["state"], "done")

    def test_t3_legacy(self):
        p = Path(TMP) / "state" / "power-onboarding.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"done": {"first_seat": 1, "bind_seat": 2, "first_handoff": 3, "evil": 4,
                                          "R1": "x", "R2": True and 5}}), encoding="utf-8")
        st = PT.onboarding_state(detector=lambda r, m, t: {}, now=NOW)
        got = {t["id"] for t in st["tasks"] if t["state"] == "done"}
        self.assertEqual(got, {"R2", "R3", "R5", "R9"})
        self.assertEqual(memo()["done"], {"R2": 5, "R3": 1, "R5": 2, "R9": 3})   # 搬過的寫回檔
        p.write_text("[1]", encoding="utf-8")
        self.assertEqual(PT.onboarding_state(detector=lambda r, m, t: {}, now=NOW)["all"]["done"], 0)

    def test_t4_tiers(self):
        r, rc = PT.set_tier("SR", False)
        self.assertEqual((rc, r), (0, {"ok": True, "detail": "power_tier_set", "tier": "SR", "on": False}))
        self.assertEqual(list(r), C["tier_receipt"]["fields"])
        st = PT.onboarding_state(detector=lambda r, m, t: {"two_providers_online": True, "power_opened": True}, now=NOW)
        self.assertFalse(st["tiers"]["SR"]["on"])
        self.assertTrue(st["tiers"]["R"]["on"])
        self.assertEqual(by_id(st)["SR1"]["state"], "done")                # 關了偵測照記
        self.assertEqual(st["tiers"]["SR"], {"on": False, "done": 1, "total": 9})
        self.assertEqual(st["tiers"]["R"]["done"], 1)
        self.assertEqual(st["all"], {"done": 2, "total": 30})
        PT.set_tier("SR", True)
        self.assertTrue(PT.onboarding_state(detector=lambda r, m, t: {}, now=NOW)["tiers"]["SR"]["on"])
        for bad in ("X", "r", "", "R;rm"):
            r, rc = PT.set_tier(bad, True)
            self.assertEqual((rc, r["detail"], r["tier"]), (2, "power_tier_unknown", None))

    def test_t5_ur7(self):
        codes = {code: True for _i, tier, code, _r in PT.TASKS if tier == "SSR"}
        st = PT.onboarding_state(detector=lambda r, m, t: codes, now=NOW)
        self.assertEqual(by_id(st)["UR7"]["state"], "done")
        shutil.rmtree(Path(TMP) / "state")
        codes.pop("redeem_fleet")
        st = PT.onboarding_state(detector=lambda r, m, t: codes, now=NOW)
        self.assertEqual(by_id(st)["UR7"]["state"], "todo")

    def test_t10_detectable(self):
        st = PT.onboarding_state(detector=lambda r, m, t: {}, now=NOW)
        t = by_id(st)
        self.assertTrue(t["R3"]["detectable"])
        self.assertFalse(t["R8"]["detectable"])                            # dm_sent 還沒接
        self.assertFalse(t["SSR1"]["detectable"])
        self.assertEqual(t["SSR1"]["requires"], "server")
        codes = {code for _i, _t, code, _r in PT.TASKS}
        self.assertTrue(PT.DETECTABLE <= codes)

    def test_t11_where(self):
        want = (["R%d" % i for i in range(1, 13)] + ["SR%d" % i for i in (1, 2, 3, 4, 5, 6, 7, 12, 13)]
                + ["SSR%d" % i for i in (2, 3, 4, 5, 11)] + ["UR%d" % i for i in (4, 5, 6, 7)])
        st = PT.onboarding_state(detector=lambda r, m, t: {"room_opened": True, "seat_registered": True}, now=NOW)
        self.assertEqual([t["id"] for t in st["tasks"] if t["where"] == "power"], want)
        self.assertEqual(by_id(st)["SR8"]["state"], "done")                # 艦長室那條照記
        self.assertEqual(st["room"], {"done": 1, "total": 14})
        self.assertEqual(st["all"], {"done": 1, "total": 30})
        self.assertEqual(st["tiers"]["SR"]["done"], 0)
        for i in ("SSR1", "UR1"):                                          # 序號兩條歸艦長室
            self.assertEqual(by_id(st)[i]["where"], "room")


class Streak(unittest.TestCase):
    def test_t6_streak(self):
        d = lambda n: (TODAY - datetime.timedelta(days=n)).isoformat()   # noqa: E731
        self.assertEqual(PT.streak([d(0), d(1), d(2)], TODAY), 3)
        self.assertEqual(PT.streak([d(1), d(2), d(3)], TODAY), 3)         # 今天還沒出發,昨天結尾照算
        self.assertEqual(PT.streak([d(2), d(3)], TODAY), 0)               # 斷一天
        self.assertEqual(PT.streak([d(0), d(2)], TODAY), 1)
        self.assertEqual(PT.streak(["x", None, 3, d(0)], TODAY), 1)
        self.assertEqual(PT.streak([], TODAY), 0)


class Detect(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(Path(TMP) / "pending", ignore_errors=True)
        shutil.rmtree(Path(TMP) / "state", ignore_errors=True)
        shutil.rmtree(Path(TMP) / "power-room", ignore_errors=True)
        self._saved = (PT._rows, PT._launch_days, PT._aib_version)
        PT._rows = lambda root: []
        PT._launch_days = lambda: []
        PT._aib_version = lambda: None

    def tearDown(self):
        PT._rows, PT._launch_days, PT._aib_version = self._saved

    def test_t7_upgraded(self):
        m = {}
        PT._aib_version = lambda: "0.1.43"
        self.assertNotIn("aib_upgraded", PT.detect(TMP, m, TODAY))
        self.assertEqual(m["first_version"], "0.1.43")
        self.assertNotIn("aib_upgraded", PT.detect(TMP, m, TODAY))       # 同版不算
        PT._aib_version = lambda: "0.1.44"
        self.assertTrue(PT.detect(TMP, m, TODAY)["aib_upgraded"])
        self.assertEqual(m["first_version"], "0.1.43")                    # 第一次看到的不被蓋
        PT._aib_version = lambda: "0.1.x"
        self.assertNotIn("aib_upgraded", PT.detect(TMP, m, TODAY))
        PT._aib_version = lambda: "0.1.44"                               # 經過 onboarding_state:first_version 落檔
        shutil.rmtree(Path(TMP) / "state", ignore_errors=True)
        PT.onboarding_state(now=NOW, today=TODAY)
        self.assertEqual(memo()["first_version"], "0.1.44")

    def test_t8_detect(self):
        self.assertEqual(PT.detect(TMP, {}, TODAY), {})
        PT._rows = lambda root: [{"provider": "claude", "persona_uid": "a", "problem": "ok", "brain_bound": True},
                                 {"provider": "codex", "problem": "no_persona"}]
        PT._launch_days = lambda: [(TODAY - datetime.timedelta(days=n)).isoformat() for n in range(7)]
        pend = Path(TMP) / "pending"
        pend.mkdir(parents=True, exist_ok=True)
        (pend / "HANDOFF-d07f9-AUTO-1024.md").write_text("x", encoding="utf-8")
        got = PT.detect(TMP, {}, TODAY)
        for k in ("seat_registered", "persona_bound", "brain_bound", "two_providers_online", "launch_recorded",
                  "launch_streak_3", "launch_streak_7"):
            self.assertIs(got.get(k), True, k)
        self.assertNotIn("handoff_receipt", got)                         # 自動存的不算
        self.assertNotIn("launch_streak_30", got)
        (pend / "HANDOFF-d07f9-1024-1448.md").write_text("x", encoding="utf-8")
        (Path(TMP) / "power-room").mkdir()
        (Path(TMP) / "power-room" / "index.html").write_text("x", encoding="utf-8")
        got = PT.detect(TMP, {}, TODAY)
        self.assertTrue(got["handoff_receipt"])
        self.assertTrue(got["power_opened"])
        PT._rows = lambda root: [{"provider": "claude", "problem": "ok"}, {"provider": "claude"}]
        got = PT.detect(TMP, {}, TODAY)
        self.assertNotIn("two_providers_online", got)                    # 同一家兩席不算
        self.assertNotIn("brain_bound", got)
        self.assertTrue(set(got) <= PT.DETECTABLE)


class Redeem(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(Path(TMP) / "state", ignore_errors=True)

    def test_t9_redeem(self):
        seen = []
        r, rc = PT.redeem(read=lambda: seen.append(1) or "SERIAL-ABC-123\n")
        self.assertEqual((rc, r), (2, {"ok": False, "detail": "power_redeem_not_yet", "item": None, "hint": None}))
        self.assertEqual(seen, [1])
        self.assertEqual(list(r), C["redeem_receipt"]["fields"])
        self.assertNotIn("SERIAL", json.dumps(r))
        self.assertFalse((Path(TMP) / "state").exists())                 # 序號不落檔

        def boom():
            raise OSError("closed")
        self.assertEqual(PT.redeem(read=boom)[1], 2)
        old = sys.stdin
        sys.stdin = io.StringIO("SERIAL-XYZ\n")
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = P.main(["redeem", "--json"])
        finally:
            sys.stdin = old
        self.assertEqual((rc, json.loads(buf.getvalue())["detail"]), (2, "power_redeem_not_yet"))
        self.assertNotIn("SERIAL", buf.getvalue())
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = P.main(["tier", "UR", "off", "--json"])
        self.assertEqual((rc, json.loads(buf.getvalue())), (0, {"ok": True, "detail": "power_tier_set", "tier": "UR",
                                                                 "on": False}))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(P.main(["tier", "UR", "maybe"]), 2)
            self.assertEqual(P.main(["tier", "ZZ", "on", "--json"]), 2)


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, case):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/power_tasks.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_not_sticky(self):
        self._knife('seen = memo.get("done") if isinstance(memo.get("done"), dict) else {}', "seen = {}",
                    "Tasks.test_t2_sticky")

    def test_k2_no_legacy(self):
        self._knife("seen = {LEGACY.get(k, k): v for k, v in seen.items() if LEGACY.get(k, k) in TASK_IDS",
                    "seen = {k: v for k, v in seen.items() if k in TASK_IDS", "Tasks.test_t3_legacy")

    def test_k3_off_stops_detect(self):
        self._knife("        if ok is True and tid and tid not in seen:",
                    "        if ok is True and tid and tid not in seen and (memo.get('tiers') or {}).get(tid.rstrip('0123456789')) is not False:",
                    "Tasks.test_t4_tiers")

    def test_k4_room_in_power(self):
        self._knife('"where": "room" if tid in IN_ROOM else "power"', '"where": "power"', "Tasks.test_t11_where")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
