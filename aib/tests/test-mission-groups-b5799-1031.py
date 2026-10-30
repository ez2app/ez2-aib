#!/usr/bin/env python3
"""任務團 G0 分組核心(SPEC-mission-team §A–§G / §J;10-29 14:11 @aeb40 定範圍:D 段拒收每條一把刀)。

G1 重放:分組後的團(組 / 組長 / 契約 / 組內任務)從 events.jsonl 重放得回、兩次一樣;沒分組的舊團 mode=single
G2 編不足拒 group.split:只有一位組長 / 沒有品管 / PM 當組長 ⇒ understaffed(K1);沒契約 v1 ⇒ no_contract(K2)
G3 跨組:組長派別組的工、在別組開單、PM 把 A 組任務派給 B 組的人、沒分組時組長派工 ⇒ cross_group(K3、K4)
G4 契約:只派給持有人(總指揮)⇒ 否則 contract_holder(K5);消費組實作單要依賴契約(K6);組長不能開契約;
   contract_rev ⇒ 契約與依賴它的已通過任務作廢回 qa(K7)
G5 授權單:分組後有效席超過一張授權單硬頂 6 ⇒ out_of_grant:seats(加人 / 拆組都擋,K8)
G6 組額度:組滿(預扣席)再加人 ⇒ over_group_budget(K9);預扣分鐘吃到預備金 15% ⇒ over_group_budget
G7 整合收據:PM / 組長不能寫品管;整合收據要含契約 + 每組一件已通過產物(K10);品管做過其中一件 ⇒ maker_is_checker(K11);
   一般任務不收整合收據、整合任務不收一般收據
G8 組長摘要:未滿 3 筆該組事件又不是卡關 ⇒ too_soon(K12);只有該組組長寫;沒 why 拒;卡關可提前
G9 組長離隊:該組已派 / 做中回待做(K13),組長空出來;PM 用 group/lead 改派(不自動升工人)
G10 合組:mode 回 single、名冊 group_id 清掉;進行中有人在做還拆 ⇒ no_diff
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))

import mission as M  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

PM, L1, L2, W1, W2, QA, MON = "aaaa1", "11111", "22222", "bbbb2", "cccc3", "dddd4", "eeee5"
T0 = 1_800_000_000
PATH = "contracts/api.md"


def by(s):
    return "seat:" + s


def plan(**kw):
    p = {"op": "split", "contract": "t1",
         "groups": [{"id": "api", "name": "API", "lead": L1, "members": [W1], "quota": {"seats": 2}},
                    {"id": "web", "name": "Web", "lead": L2, "members": [], "quota": {"seats": 1}}]}
    p.update(kw)
    return p


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-mission-g0-")
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.root), "EZ2_AIB_HOME": str(self.root), "HOME": str(self.root),
                    "EZ2_AIB_LOCALE": "en", "PATH": os.environ.get("PATH", "")}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch.object(M, "PROVE", lambda r, env: None)
        p2.start()
        self.addCleanup(p2.stop)

    def c(self, typ, who, data, task=None, now=T0):
        return M.commit(self.mid, typ, who, data, env=self.env, task=task, now=now)

    def bad(self, want, fn, *a, **k):
        with self.assertRaises(M.MissionError) as cm:
            fn(*a, **k)
        self.assertEqual(str(cm.exception), want)

    def ok(self, fn, *a, **k):
        try:
            return fn(*a, **k)
        except M.MissionError as err:
            self.fail("不該拒:%s" % err)

    def st(self):
        return M.replay(self.mid, self.env)

    def base(self, roster=((L1, "lead"), (L2, "lead"), (W1, "worker"), (QA, "qa"), (MON, "monitor")), budget=None):
        self.mid = M.new(by(PM), "Two lines", "goal", ["green"], budget=budget, env=self.env, now=T0)["id"]
        for s, r in roster:
            self.c("roster", by(PM), {"op": "add", "seat": s, "role": r})
        self.c("task_add", by(PM), {"title": "API contract", "accept": ["schema"], "deps": [], "kind": "contract",
                                    "producer": "api", "consumers": ["web"], "path": PATH})

    def grouped(self, running=True):
        self.base()
        self.ok(self.c, "group", by(PM), plan())
        self.ok(self.c, "task_add", by(L1), {"title": "api impl", "accept": ["rc 0"], "deps": [], "group": "api"})
        self.ok(self.c, "task_add", by(L2), {"title": "web impl", "accept": ["rc 0"], "deps": ["t1"], "group": "web"})
        if running:
            self.c("team", by(PM), {"move": "submit"})
            M.team(self.mid, "start", "human", env=self.env, now=T0)

    def run_task(self, tid, boss, to, head):
        code = os.urandom(6).hex()
        self.ok(self.c, "dispatch", by(boss), {"to": to, "code": code}, task=tid)
        self.ok(self.c, "report", by(to), {"code": code, "state": "doing", "note": ""}, task=tid)
        self.ok(self.c, "report", by(to), {"code": code, "state": "qa", "artifact": head, "note": ""}, task=tid)

    def mk_receipt(self, tid, head):
        return {"kind": "test", "task": tid, "accept_rev": self.st()["tasks"][tid]["accept_rev"], "artifact": head,
                "cmd": "aib test --repo . x.py", "rc": 0, "passed": 3, "total": 3}

    def pass_task(self, tid, head, qa=QA):
        return M.qa(self.mid, tid, by(qa), "pass", self.mk_receipt(tid, head), env=self.env, now=T0)

    def all_three_passed(self):
        self.grouped()
        self.run_task("t1", PM, PM, "aaa1111")
        self.pass_task("t1", "aaa1111")
        self.run_task("t2", L1, W1, "bbb2222")
        self.pass_task("t2", "bbb2222")
        self.run_task("t3", L2, L2, "ccc3333")
        self.pass_task("t3", "ccc3333")


class G1Replay(Base):
    def test_replay_grouped(self):
        self.grouped()
        a, b = self.st(), self.st()
        self.assertEqual(a, b)
        self.assertEqual(a["mode"], "grouped")
        self.assertEqual({g: (x["lead"], x["members"], x["quota"]) for g, x in a["groups"].items()},
                         {"api": (L1, [W1], {"seats": 2}), "web": (L2, [], {"seats": 1})})
        self.assertEqual({s: r.get("group_id") for s, r in a["roster"].items()},
                         {PM: None, L1: "api", L2: "web", W1: "api", QA: None, MON: None})
        self.assertEqual((a["tasks"]["t1"]["kind"], a["tasks"]["t1"]["holder"], a["tasks"]["t1"]["contract"]["rev"]), ("contract", PM, 1))
        self.assertEqual((a["tasks"]["t2"]["group"], a["tasks"]["t3"]["group"]), ("api", "web"))
        self.assertEqual(a["reserve_pct"], 15)
        v = M.view(a, now=T0)
        self.assertEqual((v["mode"], sorted(v["groups"])), ("grouped", ["api", "web"]))
        charter = json.loads((self.root / "missions" / self.mid / "charter.json").read_text(encoding="utf-8"))
        self.assertEqual((charter["mode"], sorted(charter["groups"])), ("grouped", ["api", "web"]))

    def test_single_by_default(self):
        self.base()
        self.assertEqual((self.st()["mode"], self.st()["groups"]), ("single", {}))
        self.assertNotIn("group", self.st()["tasks"]["t1"])


class G2Understaffed(Base):
    def test_split_needs_two_leads_and_qa(self):
        self.base(roster=((L1, "lead"), (W1, "worker"), (QA, "qa")))
        self.bad("understaffed", self.c, "group", by(PM), plan(groups=[
            {"id": "api", "lead": L1, "members": [], "quota": {"seats": 1}},
            {"id": "web", "lead": PM, "members": [], "quota": {"seats": 1}}]))          # PM 不兼組長
        self.bad("understaffed", self.c, "group", by(PM), plan(groups=[
            {"id": "api", "lead": L1, "members": [], "quota": {"seats": 1}},
            {"id": "web", "lead": W1, "members": [], "quota": {"seats": 1}}]))          # 工人不自動當組長
        self.assertEqual(self.st()["mode"], "single")

    def test_split_needs_qa(self):
        self.base(roster=((L1, "lead"), (L2, "lead"), (W1, "worker")))
        self.bad("understaffed", self.c, "group", by(PM), plan())

    def test_split_needs_contract(self):
        self.base()
        self.bad("no_contract", self.c, "group", by(PM), plan(contract="t9"))
        self.c("task_add", by(PM), {"title": "plain", "accept": [], "deps": []})
        self.bad("no_contract", self.c, "group", by(PM), plan(contract="t2"))
        self.bad("not_allowed", self.c, "group", by(L1), plan())
        self.ok(self.c, "group", by(PM), plan())


class G3Cross(Base):
    def test_cross_group(self):
        self.grouped()
        code = os.urandom(6).hex()
        self.bad("cross_group", self.c, "dispatch", by(L1), {"to": L2, "code": code}, task="t3")      # 派別組的工
        self.bad("cross_group", self.c, "task_add", by(L1), {"title": "x", "accept": [], "deps": ["t1"], "group": "web"})
        self.bad("cross_group", self.c, "dispatch", by(PM), {"to": L2, "code": code}, task="t2")      # A 組任務派給 B 組
        self.bad("cross_group", self.c, "dispatch", by(L1), {"to": W1, "code": code}, task="t1")      # 契約不歸組長派
        self.ok(self.c, "dispatch", by(L1), {"to": W1, "code": code}, task="t2")

    def test_lead_cannot_dispatch_before_split(self):
        self.base()
        self.c("task_add", by(PM), {"title": "plain", "accept": [], "deps": []})
        self.c("team", by(PM), {"move": "submit"})
        M.team(self.mid, "start", "human", env=self.env, now=T0)
        self.bad("cross_group", self.c, "dispatch", by(L1), {"to": W1, "code": os.urandom(6).hex()}, task="t2")
        self.bad("cross_group", self.c, "task_add", by(L1), {"title": "x", "accept": [], "deps": []})


class G4Contract(Base):
    def test_contract_only_to_holder(self):
        self.grouped()
        self.bad("contract_holder", self.c, "dispatch", by(PM), {"to": W1, "code": os.urandom(6).hex()}, task="t1")
        self.ok(self.c, "dispatch", by(PM), {"to": PM, "code": os.urandom(6).hex()}, task="t1")

    def test_consumer_must_depend_on_contract(self):
        self.grouped(running=False)
        self.bad("contract_dep_missing", self.c, "task_add", by(L2), {"title": "web 2", "accept": [], "deps": [], "group": "web"})
        self.ok(self.c, "task_add", by(L1), {"title": "api 2", "accept": [], "deps": [], "group": "api"})   # 生產方不用等
        self.bad("not_allowed", self.c, "task_add", by(L1), {"title": "c2", "accept": [], "deps": [], "group": "api",
                                                             "kind": "contract", "producer": "api", "consumers": ["web"], "path": PATH})
        self.bad("bad_group", self.c, "task_add", by(PM), {"title": "c2", "accept": [], "deps": [], "group": "api",
                                                           "kind": "contract", "producer": "api", "consumers": ["web"], "path": PATH})
        self.bad("bad_contract", self.c, "task_add", by(PM), {"title": "c2", "accept": [], "deps": [], "kind": "contract",
                                                              "producer": "api", "consumers": ["api"], "path": PATH})

    def test_contract_rev_voids_passes(self):
        self.all_three_passed()
        self.bad("not_allowed", self.c, "contract_rev", by(L1), {"path": PATH}, task="t1")
        self.ok(self.c, "contract_rev", by(PM), {"path": PATH + ".v2"}, task="t1")
        st = self.st()
        self.assertEqual(st["tasks"]["t1"]["contract"], {"producer": "api", "consumers": ["web"], "path": PATH + ".v2", "rev": 2})
        self.assertEqual({t: st["tasks"][t]["status"] for t in ("t1", "t2", "t3")}, {"t1": "qa", "t2": "passed", "t3": "qa"})
        self.assertIsNone(st["tasks"]["t3"]["receipt"])


class G5Grant(Base):
    def test_out_of_grant_on_add_after_split(self):
        self.grouped(running=False)
        self.assertEqual(M.used_seats(self.st()), 6)
        self.bad("out_of_grant:seats", self.c, "roster", by(PM), {"op": "add", "seat": W2, "role": "worker"})

    def test_out_of_grant_on_split(self):
        self.base(roster=((L1, "lead"), (L2, "lead"), (W1, "worker"), (W2, "worker"), (QA, "qa"), (MON, "monitor")))
        self.bad("out_of_grant:seats", self.c, "group", by(PM), plan())
        self.c("roster", by(PM), {"op": "remove", "seat": W2})
        self.ok(self.c, "group", by(PM), plan())


class G6Quota(Base):
    def test_group_full(self):
        self.grouped(running=False)
        self.c("roster", by(PM), {"op": "remove", "seat": MON})
        self.bad("over_group_budget", self.c, "roster", by(PM), {"op": "add", "seat": W2, "role": "worker", "group": "web"})
        self.bad("bad_group", self.c, "roster", by(PM), {"op": "add", "seat": MON, "role": "monitor", "group": "web"})

    def test_minutes_keep_reserve(self):
        self.base(budget={"minutes": 100})
        groups = plan()["groups"]
        groups[0]["quota"]["minutes"], groups[1]["quota"]["minutes"] = 50, 36       # 86 > 100 × 85%
        self.bad("over_group_budget", self.c, "group", by(PM), plan(groups=groups))
        groups[1]["quota"]["minutes"] = 35
        self.ok(self.c, "group", by(PM), plan(groups=groups))

    def test_reserve_cannot_go_below_15(self):
        """#451 r1 H2:reserve_pct 只能加大;帶 0 / 14 拒收,帶 30 照算"""
        self.base(budget={"minutes": 100})
        groups = plan()["groups"]
        groups[0]["quota"]["minutes"], groups[1]["quota"]["minutes"] = 50, 50
        for low in (0, 14):
            self.bad("bad_group", self.c, "group", by(PM), plan(groups=groups, reserve_pct=low))
        groups[0]["quota"]["minutes"], groups[1]["quota"]["minutes"] = 40, 31        # 71 > 100 × 70%
        self.bad("over_group_budget", self.c, "group", by(PM), plan(groups=groups, reserve_pct=30))
        groups[1]["quota"]["minutes"] = 30
        self.ok(self.c, "group", by(PM), plan(groups=groups, reserve_pct=30))
        self.assertEqual(self.st()["reserve_pct"], 30)

    def test_quota_seats_cover_staff(self):
        self.base()
        groups = plan()["groups"]
        groups[0]["quota"]["seats"] = 1                                            # 組長 + 1 工人 = 2 人
        self.bad("over_group_budget", self.c, "group", by(PM), plan(groups=groups))


class G10Schema(Base):
    def test_nested_shapes_rejected_not_crashed(self):
        """#451 r1 M:巢狀欄位型別錯 ⇒ bad_group(不是 TypeError);未知欄位 / 同組重複成員也拒"""
        self.base()
        def with_row(**kw):
            g = plan()["groups"]
            g[0].update(kw)
            return plan(groups=g)
        for bad in (dict(lead=[]), dict(lead={}), dict(lead=None), dict(members=[{}]), dict(members=[[]]), dict(members="x"),
                    dict(members=[W1, W1]), dict(quota=[]), dict(quota={"seats": 2, "evil": 1}), dict(name=[]), dict(evil=1)):
            with self.subTest(bad=bad):
                try:
                    self.c("group", by(PM), with_row(**bad))
                    got = "accepted"
                except Exception as err:            # TypeError / AttributeError 也要算成斷言紅,不是測試炸掉
                    got = str(err) if isinstance(err, M.MissionError) else type(err).__name__
                self.assertEqual(got, "bad_group")
        g = plan()["groups"]
        del g[1]["members"], g[1]["quota"], g[1]["name"]                         # 明訂:可省 = 只有組長 / 預設額度 / 名稱用 id
        self.ok(self.c, "group", by(PM), plan(groups=g))
        self.assertEqual(self.st()["groups"]["web"]["members"], [])
        self.assertEqual(self.st()["groups"]["web"]["name"], "web")

    def test_contract_path_under_contracts(self):
        """#451 r1 L:契約檔只准放團資料夾的 contracts/ 底下"""
        self.mid = M.new(by(PM), "Two lines", "goal", ["green"], env=self.env, now=T0)["id"]
        base = {"title": "API contract", "accept": ["schema"], "deps": [], "kind": "contract", "producer": "api", "consumers": ["web"]}
        for bad in ("/etc/passwd", "api.md", "contracts/", "contracts/../x.md", "contracts/a/../../x", "contracts/./a", "contracts//a",
                    "missions/x/contracts/api.md", "contracts/a b.md"):
            with self.subTest(bad=bad):
                self.bad("bad_contract", self.c, "task_add", by(PM), dict(base, path=bad))
        self.ok(self.c, "task_add", by(PM), dict(base, path="contracts/api/v1.md"))
        self.bad("bad_contract", self.c, "contract_rev", by(PM), {"path": "../x"}, task="t1")


class G7Integration(Base):
    def integ(self, checks):
        return {"kind": "integration", "task": "t4", "accept_rev": 1, "artifact": "ddd4444", "cmd": "make integrate",
                "contract": "t1", "checks": checks}

    def checks(self, *tids):
        return [{"task": t, "cmd": "aib test --repo . %s" % t, "ok": True} for t in tids]

    def ready(self):
        self.all_three_passed()
        self.c("task_add", by(PM), {"title": "integrate", "accept": ["both sides"], "deps": ["t2", "t3"], "kind": "integration"})
        self.run_task("t4", PM, L2, "ddd4444")

    def test_integration_receipt(self):
        self.ready()
        self.bad("not_allowed", M.qa, self.mid, "t4", by(PM), "pass", self.integ(self.checks("t1", "t2", "t3")), env=self.env, now=T0)
        self.bad("not_allowed", M.qa, self.mid, "t4", by(L1), "pass", self.integ(self.checks("t1", "t2", "t3")), env=self.env, now=T0)
        self.bad("receipt_not_green", M.qa, self.mid, "t4", by(QA), "pass", self.integ(self.checks("t1", "t2")), env=self.env, now=T0)
        self.bad("receipt_not_green", M.qa, self.mid, "t4", by(QA), "pass", self.integ(self.checks("t2", "t3")), env=self.env, now=T0)
        self.bad("bad_receipt", M.qa, self.mid, "t4", by(QA), "pass", self.mk_receipt("t4", "ddd4444"), env=self.env, now=T0)
        self.ok(M.qa, self.mid, "t4", by(QA), "pass", self.integ(self.checks("t1", "t2", "t3")), env=self.env, now=T0)
        st = self.st()
        self.assertEqual(st["tasks"]["t4"]["status"], "passed")
        self.assertEqual(st["receipts"][st["tasks"]["t4"]["receipt"]]["contract"], "t1")

    def test_checker_who_made_a_side(self):
        self.ready()
        self.c("roster", by(PM), {"op": "remove", "seat": W1})                      # 做過 t2 的人改當品管
        self.c("roster", by(PM), {"op": "add", "seat": W1, "role": "qa"})
        self.bad("maker_is_checker", M.qa, self.mid, "t4", by(W1), "pass", self.integ(self.checks("t1", "t2", "t3")), env=self.env, now=T0)

    def passed_t4(self):
        self.ready()
        self.ok(M.qa, self.mid, "t4", by(QA), "pass", self.integ(self.checks("t1", "t2", "t3")), env=self.env, now=T0)

    def test_contract_rev_voids_integration(self):
        """#451 r1 H1:契約改版 ⇒ 間接依賴的整合單也回品管,重驗 t1 / t3 不能直接讓整團 verified"""
        self.passed_t4()
        rec = self.st()["receipts"][self.st()["tasks"]["t4"]["receipt"]]
        self.assertEqual((rec["contract_rev"], [c["accept_rev"] for c in rec["checks"]]), (1, [1, 1, 1]))
        self.ok(self.c, "contract_rev", by(PM), {"path": PATH + ".v2"}, task="t1")
        st = self.st()
        self.assertEqual({t: st["tasks"][t]["status"] for t in ("t1", "t2", "t3", "t4")},
                         {"t1": "qa", "t2": "passed", "t3": "qa", "t4": "qa"})
        self.assertIsNone(st["tasks"]["t4"]["receipt"])

    def test_verify_refuses_stale_integration_receipt(self):
        """#451 r1 H1:整合收據綁被檢查任務的版本;t3 改驗收重驗後,舊整合收據不能讓整團 verified"""
        self.passed_t4()
        self.ok(self.c, "amend", by(PM), {"accept": ["rc 0", "a11y"]}, task="t3")
        self.pass_task("t3", "ccc3333")
        st = self.st()
        self.assertEqual({t: st["tasks"][t]["status"] for t in ("t1", "t2", "t3", "t4")}, dict.fromkeys(("t1", "t2", "t3", "t4"), "passed"))
        self.bad("receipt_stale", M.team, self.mid, "verify", by(PM), env=self.env, now=T0)
        self.ok(self.c, "amend", by(PM), {"accept": ["both sides", "rerun"]}, task="t4")
        self.ok(M.qa, self.mid, "t4", by(QA), "pass", dict(self.integ(self.checks("t1", "t2", "t3")), accept_rev=2), env=self.env, now=T0)
        self.ok(M.team, self.mid, "verify", by(PM), env=self.env, now=T0)

    def test_plain_task_refuses_integration_receipt(self):
        self.grouped()
        self.run_task("t1", PM, PM, "aaa1111")
        rec = {"kind": "integration", "task": "t1", "accept_rev": 1, "artifact": "aaa1111", "cmd": "x", "contract": "t1", "checks": []}
        self.bad("bad_receipt", M.qa, self.mid, "t1", by(QA), "pass", rec, env=self.env, now=T0)


class G8Summary(Base):
    def test_summary_rhythm(self):
        self.grouped()
        s = {"op": "summary", "group": "api", "why": "api half done"}
        self.assertEqual(self.st()["groups"]["api"]["since_summary"], 1)            # 開單那筆也算
        self.bad("too_soon", self.c, "group", by(L1), s)
        code = os.urandom(6).hex()
        self.c("dispatch", by(L1), {"to": W1, "code": code}, task="t2")
        self.bad("too_soon", self.c, "group", by(L1), s)
        self.c("report", by(W1), {"code": code, "state": "doing", "note": ""}, task="t2")
        self.bad("cross_group", self.c, "group", by(L2), s)
        self.bad("not_allowed", self.c, "group", by(W1), s)
        self.bad("no_why", self.c, "group", by(L1), dict(s, why=""))
        self.ok(self.c, "group", by(L1), s)
        self.assertEqual(self.st()["groups"]["api"]["since_summary"], 0)
        self.c("report", by(W1), {"code": code, "state": "blocked", "note": "stuck"}, task="t2")
        self.bad("too_soon", self.c, "group", by(L1), s)
        self.ok(self.c, "group", by(L1), dict(s, stuck=True))                        # 卡關可提前


class G9LeadLeaves(Base):
    def test_lead_leaves(self):
        self.grouped()
        code = os.urandom(6).hex()
        self.c("dispatch", by(L1), {"to": W1, "code": code}, task="t2")
        self.c("roster", by(PM), {"op": "remove", "seat": L1})
        st = self.st()
        self.assertEqual((st["tasks"]["t2"]["status"], st["tasks"]["t2"]["assignee"]), ("todo", None))
        self.assertIsNone(st["groups"]["api"]["lead"])
        self.bad("understaffed", self.c, "group", by(PM), {"op": "lead", "group": "api", "seat": W1})   # 不自動升工人
        self.c("roster", by(PM), {"op": "add", "seat": L1, "role": "lead"})
        self.ok(self.c, "group", by(PM), {"op": "lead", "group": "api", "seat": L1})
        self.assertEqual(self.st()["groups"]["api"]["lead"], L1)


class G10Merge(Base):
    def test_merge_and_no_diff(self):
        self.grouped()
        self.bad("bad_transition", self.c, "group", by(PM), plan())
        self.ok(self.c, "group", by(PM), {"op": "merge"})
        st = self.st()
        self.assertEqual(st["mode"], "single")
        self.assertFalse([s for s, r in st["roster"].items() if r.get("group_id")])
        self.c("dispatch", by(PM), {"to": W1, "code": os.urandom(6).hex()}, task="t2")
        self.bad("no_diff", self.c, "group", by(PM), plan())
        self.ok(self.c, "group", by(PM), plan(diff="api 拆出去:t2 改由 api 組"))


class CLI(Base):
    def test_cli_split_and_group_flag(self):
        self.base()
        p = self.root / "plan.json"
        p.write_text(json.dumps({k: v for k, v in plan().items() if k != "op"}), encoding="utf-8")
        import io
        out = io.StringIO()
        rc = M.run(["group", self.mid, "split", "--plan", str(p), "--by", by(PM), "--json"], env=self.env, out=out)
        self.assertEqual(rc, 0, out.getvalue())
        out = io.StringIO()
        rc = M.run(["task", self.mid, "add", "--title", "web", "--group", "web", "--dep", "t1", "--by", by(L2), "--json"], env=self.env, out=out)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertEqual(self.st()["tasks"]["t2"]["group"], "web")
        out = io.StringIO()
        p.write_text(json.dumps({"groups": [], "evil": 1}), encoding="utf-8")
        rc = M.run(["group", self.mid, "split", "--plan", str(p), "--by", by(PM), "--json"], env=self.env, out=out)
        self.assertEqual((rc, json.loads(out.getvalue())["error"]), (2, "bad_group"))


KNIVES = [
    ("K1", '''            raise MissionError("understaffed")      # 每組一位組長''', '''            pass      # 每組一位組長''',
     "G2Understaffed.test_split_needs_two_leads_and_qa"),
    ("K2", '''            raise MissionError("no_contract")''', '''            pass''', "G2Understaffed.test_split_needs_contract"),
    ("K3", '''            elif st["mode"] == "grouped" and t.get("group") and r.get("group_id") != t["group"]:
                raise MissionError("cross_group")''', '''            elif False:
                raise MissionError("cross_group")''', "G3Cross.test_cross_group"),
    ("K4", '''            if _role(st, by) == "lead" and (st["mode"] != "grouped" or t.get("group") is None):''',
     '''            if False:''', "G3Cross.test_lead_cannot_dispatch_before_split"),
    ("K5", '''                if to != t.get("holder") or _role(st, "seat:%s" % to) != "pm":''', '''                if False:''',
     "G4Contract.test_contract_only_to_holder"),
    ("K6", '''            if any(c not in deps for c in need):''', '''            if False:''', "G4Contract.test_consumer_must_depend_on_contract"),
    ("K7", '''        for x in _contract_affected(st, tid):''', '''        for x in [st["tasks"][tid]]:''',
     "G4Contract.test_contract_rev_voids_passes"),
    ("K7b", '''            if hit & set(x["deps"]) or rec.get("contract") in hit or hit & checked:''',
     '''            if hit & set(x["deps"]) and x.get("kind") != "integration":''', "G7Integration.test_contract_rev_voids_integration"),
    ("K7c", '''            if any(_integration_stale(st, t) for t in live):''', '''            if False:''',
     "G7Integration.test_verify_refuses_stale_integration_receipt"),
    ("K8", '''            if st["mode"] == "grouped" and used_seats(st) >= GRANT_SEATS * st.get("grants", 1):''',
     '''            if False:''', "G5Grant.test_out_of_grant_on_add_after_split"),
    ("K9", '''                if len(_group_staff(st, gid)) >= g["quota"]["seats"]:''', '''                if False:''', "G6Quota.test_group_full"),
    ("K9b", '''        if type(reserve) is not int or not RESERVE_PCT <= reserve <= 50:''',
     '''        if type(reserve) is not int or not 0 <= reserve <= 50:''', "G6Quota.test_reserve_cannot_go_below_15"),
    ("K9c", '''            raise MissionError("bad_group")         # 型別先驗''', '''            pass         # 型別先驗''',
     "G10Schema.test_nested_shapes_rejected_not_crashed"),
    ("K9d", '''    return len(parts) >= 2 and parts[0] == "contracts" and all(''', '''    return bool(path) or all(''',
     "G10Schema.test_contract_path_under_contracts"),
    ("K10", '''    if cid not in seen or not groups <= covered:''', '''    if False:''', "G7Integration.test_integration_receipt"),
    ("K11", '''        if _seat_of(by) in x["workers"]:
            raise MissionError("maker_is_checker")''', '''        if False:
            raise MissionError("maker_is_checker")''', "G7Integration.test_checker_who_made_a_side"),
    ("K12", '''        if g["since_summary"] < SUMMARY_EVERY and not stuck:''', '''        if False:''', "G8Summary.test_summary_rhythm"),
    ("K13", '''                if t["status"] in LEASED and (t.get("assignee") == seat or any(t.get("group") == g["id"] for g in led)):''',
     '''                if t["status"] in LEASED and t.get("assignee") == seat:''', "G9LeadLeaves.test_lead_leaves"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, "src/cockpit/mission.py", needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=120)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
