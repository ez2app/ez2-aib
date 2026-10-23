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
T13 本機真事件:seat-now / dm 寄出紀錄 / dm 佇列稽核轉 done / 記憶 ≥ 10 筆 / aib test rc 0 / 同角色兩席 / 專案 ≥ 3 / doctor 跑過 / 兩座以上的艙;
    沒有或不夠 ⇒ 不算;稽核只有 read、rc 1、9 筆記憶、2 個專案、只有 doctor 暫存檔、一座艙(另一個夾沒有 seats)⇒ 不算
T12 SPEC §15:沒出發 ⇒ SSR / UR locked、unlock = launch_recorded、on false、每條 locked、tier on 回 power_tier_locked;
    出發過(記住)⇒ 解鎖、unlock null;room_tiers 分級數字;since = done 時間
T11 where(SPEC §14):動力室 30 條 = R1–12、SR1–7 + 12–13、SSR2–5 + 11、UR4–7;其餘 14 條 room;tiers / all 只算 power、room 另算
刀(複本):K1 done 不記住 ⇒ T2 紅;K2 舊 id 不搬 ⇒ T3 紅;K3 關等級連偵測都停 ⇒ T4 紅;K4 艦長室的條照列動力室 ⇒ T11 紅;K5 不鎖 SSR / UR ⇒ T12 紅;K6 讀了就算結案 ⇒ T13 紅
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
        self.assertEqual(count, {"R": 12, "SR": 16, "SSR": 12, "UR": 7})
        for t in st["tasks"]:
            self.assertEqual(set(t), {"id", "tier", "where", "state", "since", "locked", "reward", "detect", "detectable",
                                      "requires"})
            self.assertIsNone(t["reward"])
            self.assertIn(t["requires"], (None, "room", "server", "skins", "command"))
        self.assertEqual(st["all"], {"done": 0, "total": 30})
        self.assertEqual(st["room"], {"done": 0, "total": 17})

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
        self.assertEqual(st["tiers"]["SR"], {"on": False, "done": 1, "total": 9, "locked": False, "unlock": None})
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
        self.assertFalse(t["R11"]["detectable"])                           # device_bound 還沒接
        self.assertTrue(t["R10"]["detectable"])                            # doctor_run 10-24 接上
        self.assertTrue(t["R8"]["detectable"])
        self.assertFalse(t["SSR1"]["detectable"])
        self.assertEqual(t["SSR1"]["requires"], "server")
        self.assertEqual((t["UR4"]["requires"], t["UR4"]["detectable"]), ("command", False))   # aib support 還沒做
        codes = {code for _i, _t, code, _r in PT.TASKS}
        self.assertTrue(PT.DETECTABLE <= codes)

    def test_t11_where(self):
        want = (["R%d" % i for i in range(1, 13)] + ["SR%d" % i for i in (1, 2, 3, 4, 5, 6, 7, 12, 13)]
                + ["SSR%d" % i for i in (2, 3, 4, 5, 11)] + ["UR%d" % i for i in (4, 5, 6, 7)])
        st = PT.onboarding_state(detector=lambda r, m, t: {"room_opened": True, "seat_registered": True}, now=NOW)
        self.assertEqual([t["id"] for t in st["tasks"] if t["where"] == "power"], want)
        self.assertEqual(by_id(st)["SR8"]["state"], "done")                # 艦長室那條照記
        self.assertEqual(st["room"], {"done": 1, "total": 17})
        self.assertEqual(st["all"], {"done": 1, "total": 30})
        self.assertEqual(st["tiers"]["SR"]["done"], 0)
        for i in ("SR14", "SR15", "SR16"):                                 # 地上三條:艦長室、偵測等 LINE 端
            self.assertEqual((by_id(st)[i]["where"], by_id(st)[i]["detectable"], by_id(st)[i]["requires"]),
                             ("room", False, "server"))
        for i in ("SSR1", "UR1"):                                          # 序號兩條歸艦長室
            self.assertEqual(by_id(st)[i]["where"], "room")

    def test_t12_locks(self):
        st = PT.onboarding_state(detector=lambda r, m, t: {"seat_registered": True}, now=NOW)
        self.assertEqual(list(st), C["onboarding"]["fields"])
        for t in ("SSR", "UR"):
            self.assertEqual((st["tiers"][t]["locked"], st["tiers"][t]["unlock"], st["tiers"][t]["on"]),
                             (True, "launch_recorded", False))
        self.assertEqual((st["tiers"]["R"]["locked"], st["tiers"]["SR"]["unlock"]), (False, None))
        t = by_id(st)
        self.assertEqual((t["SSR2"]["locked"], t["SSR6"]["locked"], t["UR3"]["locked"], t["SR8"]["locked"]),
                         (True, True, True, False))
        self.assertEqual((t["R3"]["since"], t["R4"]["since"]), (NOW, None))
        self.assertEqual(st["room_tiers"], {"SR": {"done": 0, "total": 7, "locked": False, "unlock": None},
                                            "SSR": {"done": 0, "total": 7, "locked": True, "unlock": "launch_recorded"},
                                            "UR": {"done": 0, "total": 3, "locked": True, "unlock": "launch_recorded"}})
        for tier in ("SSR", "UR"):
            r, rc = PT.set_tier(tier, True)
            self.assertEqual((rc, r["detail"], r["tier"]), (2, "power_tier_locked", tier))
        self.assertEqual(PT.set_tier("SR", True)[1], 0)
        PT.onboarding_state(detector=lambda r, m, t: {"launch_recorded": True}, now=NOW + 5)
        st = PT.onboarding_state(detector=lambda r, m, t: {}, now=NOW + 9)          # 記住了 ⇒ 一直開
        self.assertEqual((st["tiers"]["SSR"]["locked"], st["tiers"]["SSR"]["unlock"], st["tiers"]["SSR"]["on"]),
                         (False, None, True))
        self.assertFalse(by_id(st)["UR3"]["locked"])
        self.assertEqual(PT.set_tier("UR", False)[1], 0)


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

    def test_t13_local_events(self):
        root = Path(TMP)
        keys = ("now_written", "dm_sent", "dm_done", "memory_10", "test_green", "second_seat_same_role", "projects_3",
                "doctor_run", "new_cabin")
        for k in keys:
            self.assertNotIn(k, PT.detect(TMP, {}, TODAY))
        st = root / "state"
        (st / "seat-now").mkdir(parents=True)
        (st / "seat-now" / "x.line").write_text("x")
        (st / "aib-dm-sent-v3.tsv").write_text("")
        (st / "dm-queue" / "abc12").mkdir(parents=True)
        (st / "dm-queue" / "abc12" / "audit.jsonl").write_text(json.dumps({"from": "queued", "to": "read"}) + "\n")
        (root / "memory-index").mkdir()
        (root / "memory-index" / "notes.jsonl").write_text("{}\n" * 9)
        (root / "verify-shadow").mkdir()
        (root / "verify-shadow" / "verify-1.rows.jsonl").write_text(json.dumps({"test": "t", "rc": 1, "x": 0}) + "\n")
        (root / "projects").mkdir()
        for n in ("proj:a", "proj:b", "other"):
            (root / "projects" / n).mkdir()
        (st / "run-marks").mkdir()
        (st / "run-marks" / "doctor.json.1.tmp").write_text("{}")
        (root / "instances" / "c1" / "seats").mkdir(parents=True)
        (root / "instances" / "c2").mkdir()
        (root / "instances" / "c3.txt").write_text("")
        PT._rows = lambda r: [{"role": "dev"}, {"role": "pm"}, {"role": ""}, {"role": ""}]
        got = PT.detect(TMP, {}, TODAY)
        for k in keys:
            self.assertNotIn(k, got, k)                                    # 都差一點 ⇒ 不算
        (st / "seat-now" / "x.json").write_text("{}")
        (st / "aib-dm-sent-v3.tsv").write_text("2026-10-24\tx\n")
        with open(st / "dm-queue" / "abc12" / "audit.jsonl", "a") as f:
            f.write(json.dumps({"from": "read", "to": "done"}) + "\n")
        (root / "memory-index" / "notes.jsonl").write_text("{}\n" * 10)
        (root / "verify-shadow" / "verify-1.rows.jsonl").write_text(json.dumps({"test": "t", "rc": 0, "x": 1}) + "\n")
        (root / "projects" / "proj:c").mkdir()
        (st / "run-marks" / "doctor.json").write_text("{}")
        (root / "instances" / "c2" / "seats").mkdir()
        PT._rows = lambda r: [{"role": "dev"}, {"role": "dev"}]
        got = PT.detect(TMP, {}, TODAY)
        for k in keys:
            self.assertIs(got.get(k), True, k)
        for k in ("state", "memory-index", "verify-shadow", "projects", "instances"):
            shutil.rmtree(root / k)

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
        self.assertEqual((rc, json.loads(buf.getvalue())["detail"]), (2, "power_tier_locked"))   # 還沒出發 ⇒ 鎖著
        PT.onboarding_state(detector=lambda r, m, t: {"launch_recorded": True}, now=NOW)
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

    def test_k5_no_lock(self):
        self._knife('UNLOCK = {"SSR": "launch_recorded", "UR": "launch_recorded"}', "UNLOCK = {}", "Tasks.test_t12_locks")

    def test_k6_read_counts_as_done(self):
        self._knife("""_tail_has(p, '"to": "done"')""", """_tail_has(p, '"to": "')""", "Detect.test_t13_local_events")

    def test_k4_room_in_power(self):
        self._knife('"where": "room" if tid in IN_ROOM else "power"', '"where": "power"', "Tasks.test_t11_where")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
