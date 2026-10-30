#!/usr/bin/env python3
"""任務團資料契約 aib mission(10-29 S1,SPEC-mission-team-f5fb8-1029)。

M1 團的狀態機:草稿 → 待開始 → 進行中 ⇄ 暫停 → 叫停 → 已驗證 → 待人收下 → 已封存;非法轉移拒(K1)
M2 任務狀態機 + 角色:只有 PM 派工、只有被派的人回報、工人不能跳過品管
M3 租期 15 分:過期回待做、代號作廢、過期後的回報拒(K2);暫停不吃租期
M4 舊代號拒收:改派後舊代號的回報 stale_code(K3)
M5 「通過」只能品管收據翻:PM / 工人 / 本人都不行(K4);做過的人不能審(K5);收據綁驗收版本 + 產物(K6);
   收據要機器證據(rc 0 且全過 / CI success / 已合 / 逐項勾);verified_pct 只算 ci / pr_merged
M6 唯一 id:同 eid 重送不重做、不同內容 eid_conflict(K7);開團 / 開單重送不多開;--if-rev 不合拒
M7 重開機接得回:從 events.jsonl 重放;斷電半行當沒寫、下次寫入截掉(K8);中間被改成非法 ⇒ 讀不起來(K9)
M8 本人兩步(開始 / 收下)走 approve_gate:沒過 / 不是 human ⇒ 拒(K10);全部通過才能 verify(K11)
M10 倒數 30 秒自動開始(Andy 03:1x 裁示 7):滿 30 秒沒按停 ⇒ tick 寫 system autostart(K12 不滿 30 秒也收);
    hold 停倒數(K13);再 submit 重新倒數;system 以外不能寫 autostart
M9 改驗收:舊通過作廢回品管中;檔權限 0600 / 0700;CLI --json
M11 #414 R1(@aeb40):started_by 分得出自動 / 本人開始(K14);ci / pr_merged 收據 head 綁產物頭碼(K15);
    machine_all 旗標;偽造的過期回報 / 未到期 expire 重放讀不起來(K16、K17);未知依賴拒(K18);
    不存在的 id 不留空目錄;同 eid 重送不再彈 Touch ID(K19);收下的句子帶取消件數;快照不帶派工代號;
    本人按的暫停只有本人能恢復(K20)
M12 S2 試用回饋(@f5fb8):品管先擋角色再讀收據(K21);aib mission why 列事件鏈、不帶派工代號(K22);
    --help 寫清 --by / 席碼 / eid / 代號格式
"""
import json
import os
import subprocess
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

PM, W1, W2, QA, MON = "aaaa1", "bbbb2", "cccc3", "dddd4", "eeee5"
T0 = 1_800_000_000


def by(s):
    return "seat:" + s


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-mission-")
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.root), "EZ2_AIB_HOME": str(self.root), "HOME": str(self.root),
                    "EZ2_AIB_LOCALE": "en", "PATH": os.environ.get("PATH", "")}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        self.proved = []
        p2 = mock.patch.object(M, "PROVE", lambda r, env: self.proved.append(str(r)) or self.prove_why)
        p2.start()
        self.addCleanup(p2.stop)
        self.prove_why = None

    def c(self, typ, who, data, task=None, eid=None, now=T0):
        return M.commit(self.mid, typ, who, data, env=self.env, task=task, eid=eid, now=now)

    def bad(self, want, fn, *a, **k):
        with self.assertRaises(M.MissionError) as cm:
            fn(*a, **k)
        got = str(cm.exception)
        self.assertEqual(got.split(":")[0] if not want.startswith("corrupt") else got[:len(want)], want)

    def ok(self, fn, *a, **k):
        """該過的那一步:被拒算斷言紅(刀測要的是 FAIL 不是 ERROR)。"""
        try:
            return fn(*a, **k)
        except M.MissionError as err:
            self.fail("不該拒:%s" % err)

    def mk(self, running=True, tasks=1):
        self.mid = M.new(by(PM), "Ship it", "goal", ["green"], env=self.env, now=T0)["id"]
        for s, r in ((W1, "worker"), (W2, "worker"), (QA, "qa"), (MON, "monitor")):
            self.c("roster", by(PM), {"op": "add", "seat": s, "role": r})
        for i in range(tasks):
            self.c("task_add", by(PM), {"title": "task %d" % (i + 1), "accept": ["rc 0"], "deps": []})
        if running:
            self.c("team", by(PM), {"move": "submit"})
            M.team(self.mid, "start", "human", env=self.env, now=T0)
        return self.mid

    def dispatch(self, tid="t1", to=W1, now=T0, code=None):
        code = code or os.urandom(6).hex()
        self.c("dispatch", by(PM), {"to": to, "code": code}, task=tid, now=now)
        return code

    def report(self, state, code, tid="t1", who=W1, now=T0, artifact=None):
        return self.c("report", by(who), {"code": code, "state": state, "artifact": artifact, "note": ""}, task=tid, now=now)

    def to_qa(self, tid="t1", who=W1, now=T0, head="abc1234"):
        code = self.dispatch(tid, who, now)
        self.report("doing", code, tid, who, now)
        self.report("qa", code, tid, who, now, artifact=head)
        return code

    def receipt(self, tid="t1", kind="test", accept_rev=1, artifact="abc1234", **kw):
        rec = {"kind": kind, "task": tid, "accept_rev": accept_rev, "artifact": artifact, "cmd": "aib test --repo . x.py"}
        rec.update({"test": {"rc": 0, "passed": 5, "total": 5}, "ci": {"conclusion": "success", "head": "abc1234"},
                    "pr_merged": {"merged": True, "head": "abc1234", "url": "https://github.com/x/y/pull/1"},
                    "checklist": {"items": [{"text": "a", "ok": True}]}}[kind])
        rec.update(kw)
        return rec

    def st(self):
        return M.replay(self.mid, self.env)


class M1Team(Base):
    def test_happy_path(self):
        self.mk()
        self.assertEqual(self.st()["status"], "running")
        self.to_qa()
        M.qa(self.mid, "t1", by(QA), "pass", self.receipt(), env=self.env, now=T0)
        self.c("team", by(MON), {"move": "verify"})
        self.c("team", by(PM), {"move": "handoff"})
        M.team(self.mid, "accept", "human", env=self.env, now=T0)
        self.assertEqual(self.st()["status"], "archived")
        self.bad("archived", self.c, "team", by(PM), {"move": "reopen"})

    def test_illegal_transitions(self):
        self.mk(running=False)
        self.bad("bad_transition", self.c, "team", by(PM), {"move": "pause"})
        self.bad("bad_transition", self.c, "team", by(PM), {"move": "handoff"})
        self.bad("bad_move", self.c, "team", by(PM), {"move": "archive"})
        self.c("team", by(PM), {"move": "submit"})
        self.bad("bad_transition", self.c, "team", by(PM), {"move": "submit"})
        self.assertEqual(self.st()["status"], "ready")

    def test_roles_on_team_moves(self):
        self.mk()
        self.bad("not_allowed", self.c, "team", by(W1), {"move": "pause"})
        self.bad("not_allowed", self.c, "team", by("fffff"), {"move": "halt"})
        self.c("team", by(MON), {"move": "halt"})
        self.bad("bad_transition", self.c, "team", by(PM), {"move": "resume"})
        M.team(self.mid, "start", "human", env=self.env, now=T0)
        self.assertEqual(self.st()["status"], "running")


class M2Tasks(Base):
    def test_only_pm_dispatches_and_only_assignee_reports(self):
        self.mk()
        self.bad("not_allowed", self.c, "dispatch", by(W1), {"to": W1, "code": "a" * 12}, task="t1")
        self.bad("not_worker", self.c, "dispatch", by(PM), {"to": QA, "code": "a" * 12}, task="t1")
        code = self.dispatch()
        self.bad("not_assignee", self.report, "doing", code, who=W2)
        self.bad("bad_transition", self.report, "qa", code)        # 已派不能直接跳品管
        self.report("doing", code)
        self.report("blocked", code)
        self.report("doing", code)
        self.assertEqual(self.st()["tasks"]["t1"]["status"], "doing")

    def test_dispatch_needs_running_and_deps(self):
        self.mk(running=False, tasks=1)
        self.c("task_add", by(PM), {"title": "after", "deps": ["t1"]})
        self.bad("team_not_running", self.dispatch)
        self.c("team", by(PM), {"move": "submit"})
        M.team(self.mid, "start", "human", env=self.env, now=T0)
        self.bad("deps_open", self.dispatch, "t2")
        self.assertEqual(self.st()["tasks"]["t2"]["deps"], ["t1"])

    def test_return_then_redispatch(self):
        self.mk()
        old = self.to_qa()
        M.qa(self.mid, "t1", by(QA), "return", why="rc 1", env=self.env, now=T0)
        t = self.st()["tasks"]["t1"]
        self.assertEqual((t["status"], t["assignee"], t["code"]), ("returned", None, None))
        new = self.dispatch(to=W2)
        self.assertNotEqual(old, new)
        self.assertEqual(self.st()["tasks"]["t1"]["workers"], [W1, W2])

    def test_parent_weight_est(self):
        self.mk()
        self.c("task_add", by(PM), {"title": "child", "parent": "t1", "weight": 3})
        self.bad("bad_parent", self.c, "task_add", by(PM), {"title": "orphan", "parent": "t9"})
        self.bad("bad_weight", self.c, "task_add", by(PM), {"title": "w", "weight": 0})
        code = self.dispatch("t2")
        self.c("report", by(W1), {"code": code, "state": "doing", "est": 40}, task="t2")
        self.bad("bad_est", self.c, "report", by(W1), {"code": code, "state": "doing", "est": 140}, task="t2")
        t = self.st()["tasks"]
        self.assertEqual((t["t2"]["parent"], t["t2"]["weight"], t["t2"]["est"], t["t1"]["weight"]), ("t1", 3, 40, 1))

    def test_roster_remove_frees_task(self):
        self.mk()
        self.dispatch()
        self.c("roster", by(PM), {"op": "remove", "seat": W1})
        t = self.st()["tasks"]["t1"]
        self.assertEqual((t["status"], t["assignee"]), ("todo", None))


class M3Lease(Base):
    def test_expired_lease_back_to_todo(self):
        self.mk()
        code = self.dispatch(now=T0)
        self.report("doing", code, now=T0 + 600)                    # 續租到 T0+1500
        got = M.tick(self.mid, self.env, now=T0 + 1400)
        self.assertEqual(got, [])
        got = M.tick(self.mid, self.env, now=T0 + 1501)
        self.assertEqual(got, [{"task": "t1", "from": W1}])
        t = self.st()["tasks"]["t1"]
        self.assertEqual((t["status"], t["assignee"], t["code"], t["expired_from"]), ("todo", None, None, W1))

    def test_report_after_expiry_refused_even_without_tick(self):
        self.mk()
        code = self.dispatch(now=T0)
        self.bad("stale_code", self.report, "doing", code, now=T0 + M.LEASE_S + 1)
        self.assertEqual(self.st()["tasks"]["t1"]["status"], "todo")   # 拒之前先把過期的放回待做

    def test_pause_does_not_eat_lease(self):
        self.mk()
        code = self.dispatch(now=T0)
        self.c("team", by(PM), {"move": "pause"}, now=T0 + 100)
        self.assertEqual(M.tick(self.mid, self.env, now=T0 + 5000), [])
        self.c("team", by(PM), {"move": "resume"}, now=T0 + 5000)
        self.ok(self.report, "doing", code, now=T0 + 5000 + M.LEASE_S - 1)      # R1 L2:刀要紅成 FAIL
        self.assertEqual(self.st()["tasks"]["t1"]["status"], "doing")


class M4StaleCode(Base):
    def test_old_code_refused_after_redispatch(self):
        self.mk()
        old = self.dispatch(now=T0)
        M.tick(self.mid, self.env, now=T0 + M.LEASE_S + 1)
        new = self.dispatch(to=W1, now=T0 + M.LEASE_S + 2)
        self.bad("stale_code", self.report, "doing", old, now=T0 + M.LEASE_S + 3)
        self.report("doing", new, now=T0 + M.LEASE_S + 3)

    def test_same_code_cannot_be_reused(self):
        self.mk()
        code = self.dispatch()
        M.tick(self.mid, self.env, now=T0 + M.LEASE_S + 1)
        self.bad("bad_code", self.dispatch, code=code, now=T0 + M.LEASE_S + 2)


class M5Pass(Base):
    def test_only_qa_receipt_flips_passed(self):
        self.mk()
        self.to_qa()
        for who in (by(PM), by(W1), "human", by(MON)):
            self.bad("not_allowed", M.qa, self.mid, "t1", who, "pass", self.receipt(), env=self.env, now=T0)
        self.bad("bad_transition", self.report, "passed", self.st()["tasks"]["t1"]["code"] or "x")
        self.assertEqual(self.st()["tasks"]["t1"]["status"], "qa")
        M.qa(self.mid, "t1", by(QA), "pass", self.receipt(), env=self.env, now=T0)
        t = self.st()["tasks"]["t1"]
        self.assertEqual(t["status"], "passed")
        saved = json.loads((M.mdir(self.mid, self.env) / "qa" / (t["receipt"] + ".json")).read_text())
        self.assertEqual((saved["task"], saved["accept_rev"], saved["by"]), ("t1", 1, by(QA)))

    def test_maker_cannot_check(self):
        self.mk()
        self.to_qa()
        self.c("roster", by(PM), {"op": "remove", "seat": W1})
        self.c("roster", by(PM), {"op": "add", "seat": W1, "role": "qa"})
        self.bad("maker_is_checker", M.qa, self.mid, "t1", by(W1), "pass", self.receipt(), env=self.env, now=T0)

    def test_receipt_bound_to_accept_rev_and_artifact(self):
        self.mk()
        self.to_qa(head="abc1234")
        self.bad("receipt_stale", M.qa, self.mid, "t1", by(QA), "pass", self.receipt(accept_rev=2), env=self.env, now=T0)
        self.bad("receipt_stale", M.qa, self.mid, "t1", by(QA), "pass", self.receipt(artifact="def5678"), env=self.env, now=T0)
        self.bad("receipt_stale", M.qa, self.mid, "t1", by(QA), "pass", self.receipt(tid="t2"), env=self.env, now=T0)

    def test_receipt_needs_machine_evidence(self):
        self.mk()
        self.to_qa()
        for rec in (self.receipt(rc=1), self.receipt(passed=4), self.receipt(total=0), self.receipt(rc="0"),
                    self.receipt(cmd=""), self.receipt(kind="ci", conclusion="failure"),
                    self.receipt(kind="pr_merged", merged="yes"), self.receipt(kind="checklist", items=[{"text": "a", "ok": False}]),
                    self.receipt(kind="checklist", items=[]), dict(self.receipt(), kind="trust_me")):
            with self.subTest(rec=rec):
                self.assertRaises(M.MissionError, M.qa, self.mid, "t1", by(QA), "pass", rec, env=self.env, now=T0)
        self.assertEqual(self.st()["tasks"]["t1"]["status"], "qa")

    def test_verified_pct_counts_only_machine(self):
        self.mk(tasks=2)
        self.to_qa("t1")
        M.qa(self.mid, "t1", by(QA), "pass", self.receipt("t1"), env=self.env, now=T0)
        self.to_qa("t2", W2)
        M.qa(self.mid, "t2", by(QA), "pass", self.receipt("t2", kind="ci"), env=self.env, now=T0)
        v = M.view(self.st(), now=T0)
        self.assertEqual((v["passed"], v["total"], v["verified_pct"]), (2, 2, 50))
        self.assertTrue(all("code" not in t and "codes" not in t for t in v["tasks"]))


class M6Ids(Base):
    def test_same_eid_not_repeated(self):
        self.mk()
        a = self.c("task_add", by(PM), {"title": "x", "accept": [], "deps": []}, eid="add-x-0001")
        b = self.ok(self.c, "task_add", by(PM), {"title": "x", "accept": [], "deps": []}, eid="add-x-0001")
        self.assertEqual((a["duplicate"], b["duplicate"], a["task"], b["task"]), (False, True, "t2", "t2"))
        self.assertEqual(len(self.st()["tasks"]), 2)
        self.bad("eid_conflict", self.c, "task_add", by(PM), {"title": "y", "accept": [], "deps": []}, eid="add-x-0001")

    def test_new_mission_resend(self):
        a = M.new(by(PM), "Team", eid="new-team-0001", env=self.env, now=T0)
        b = M.new(by(PM), "Team", eid="new-team-0001", env=self.env, now=T0)
        self.assertEqual((a["id"], b["duplicate"]), (b["id"], True))
        self.assertEqual(len(M.list_ids(self.env)), 1)
        self.bad("eid_conflict", M.new, by(PM), "Other", eid="new-team-0001", env=self.env, now=T0)

    def test_dispatch_resend_returns_same_code(self):
        self.mk()
        out = []
        for _ in range(2):
            buf = _Buf()
            rc = M.run(["task", self.mid, "dispatch", "t1", "--to", W1, "--by", by(PM), "--eid", "dsp-t1-0001", "--json"],
                       env=self.env, out=buf)
            self.assertEqual(rc, 0)
            out.append(json.loads(buf.text))
        self.assertEqual(out[0]["code"], out[1]["code"])
        self.assertTrue(out[1]["duplicate"])
        self.assertEqual(self.st()["tasks"]["t1"]["rev"], 2)

    def test_if_rev(self):
        self.mk()
        self.bad("stale_rev", self.c, "dispatch", by(PM), {"to": W1, "code": "a" * 12, "if_rev": 7}, task="t1")
        self.c("dispatch", by(PM), {"to": W1, "code": "a" * 12, "if_rev": 1}, task="t1")


class _Buf:
    def __init__(self):
        self.text = ""

    def write(self, s):
        self.text += s


class M7Replay(Base):
    def test_restart_replays(self):
        self.mk()
        code = self.dispatch()
        before = M.view(self.st(), now=T0)
        import importlib
        fresh = importlib.reload(M)                               # 「重開機」:新的行程只看檔
        after = fresh.view(fresh.replay(self.mid, self.env), now=T0)
        self.assertEqual(before, after)
        fresh.commit(self.mid, "report", by(W1), {"code": code, "state": "doing"}, env=self.env, task="t1", now=T0)

    def test_torn_tail_ignored_and_truncated(self):
        self.mk()
        p = M.mdir(self.mid, self.env) / "events.jsonl"
        with open(p, "ab") as f:
            f.write(b'{"v": 1, "seq": 99, "half')
        n = self.ok(self.st)["seq"]
        self.ok(self.dispatch)
        lines = p.read_bytes().splitlines()
        self.assertTrue(all(json.loads(x) for x in lines))
        self.assertEqual(json.loads(lines[-1])["seq"], n + 1)

    def test_tampered_event_unreadable(self):
        self.mk()
        p = M.mdir(self.mid, self.env) / "events.jsonl"
        lines = p.read_text().splitlines()
        ev = {"v": 1, "seq": len(lines) + 1, "eid": "e_forged000001", "req": None, "at": T0, "by": by(PM), "type": "qa",
              "task": "t1", "data": {"verdict": "pass", "receipt": {}, "receipt_id": "r_forged000001"}}
        p.write_text("\n".join(lines + [json.dumps(ev)]) + "\n")
        self.bad("corrupt:%d" % ev["seq"], M.replay, self.mid, self.env)
        self.bad("corrupt", self.dispatch)

    def test_snapshots_and_modes(self):
        self.mk()
        d = M.mdir(self.mid, self.env)
        self.assertEqual(oct(d.stat().st_mode & 0o777), "0o700")
        for name in ("events.jsonl", "charter.json", "roster.json", "budget.json", "tasks.jsonl"):
            self.assertEqual(oct((d / name).stat().st_mode & 0o777), "0o600", name)
        tasks = [json.loads(x) for x in (d / "tasks.jsonl").read_text().splitlines()]
        self.assertEqual([t["id"] for t in tasks], ["t1"])
        self.assertEqual(json.loads((d / "charter.json").read_text())["status"], "running")


class M8Human(Base):
    def test_start_needs_touchid(self):
        self.mk(running=False)
        self.c("team", by(PM), {"move": "submit"})
        self.bad("not_allowed", M.team, self.mid, "start", by(PM), env=self.env, now=T0)
        self.prove_why = "touchid_cancel"
        self.bad("touchid_cancel", M.team, self.mid, "start", "human", env=self.env, now=T0)
        self.bad("not_proved", self.c, "team", "human", {"move": "start"})
        self.assertEqual(self.st()["status"], "ready")
        self.prove_why = None
        M.team(self.mid, "start", "human", env=self.env, now=T0)
        self.assertIn("Ship it", self.proved[-1])
        self.assertIn(self.mid, self.proved[-1])

    def test_gate_reason_registered(self):
        import approve_gate as AG
        self.assertEqual(AG.SITES["mission.start"]["mod"], "cockpit/mission.py")
        self.assertTrue(AG.CATEGORIES[AG.SITES["mission.accept"]["cat"]]["high_risk"])

    def test_non_mac_refuses(self):
        self.mk(running=False)
        self.c("team", by(PM), {"move": "submit"})
        with mock.patch.object(M, "PROVE", None), mock.patch("sys.platform", "linux"):
            self.bad("touchid_unavailable", M.team, self.mid, "start", "human", env=self.env, now=T0)

    def test_verify_needs_all_passed(self):
        self.mk(tasks=2)
        self.to_qa("t1")
        M.qa(self.mid, "t1", by(QA), "pass", self.receipt("t1"), env=self.env, now=T0)
        self.bad("not_all_passed", self.c, "team", by(PM), {"move": "verify"})
        self.c("cancel", by(PM), {}, task="t2")
        self.c("team", by(PM), {"move": "verify"})
        self.assertEqual(self.st()["status"], "verified")


class M9Amend(Base):
    def test_amend_voids_pass(self):
        self.mk()
        self.to_qa()
        M.qa(self.mid, "t1", by(QA), "pass", self.receipt(), env=self.env, now=T0)
        self.c("amend", by(PM), {"accept": ["rc 0", "screenshot"]}, task="t1")
        t = self.st()["tasks"]["t1"]
        self.assertEqual((t["status"], t["accept_rev"], t["receipt"]), ("qa", 2, None))
        self.bad("receipt_stale", M.qa, self.mid, "t1", by(QA), "pass", self.receipt(), env=self.env, now=T0)
        M.qa(self.mid, "t1", by(QA), "pass", self.receipt(accept_rev=2), env=self.env, now=T0)

    def test_cli_json(self):
        env = dict(self.env)
        cli = [sys.executable, "-B", str(ROOT / "src" / "cockpit" / "mission.py")]
        r = subprocess.run(cli + ["new", "--title", "CLI team", "--by", by(PM), "--json"], env=env, capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        mid = json.loads(r.stdout)["id"]
        r = subprocess.run(cli + ["task", mid, "add", "--title", "one", "--by", by(PM), "--json"], env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(r.stdout)["task"], "t1")
        r = subprocess.run(cli + ["team", mid, "start", "--by", by(PM), "--json"], env=env, capture_output=True,
                           text=True, timeout=30)
        self.assertEqual((r.returncode, json.loads(r.stdout)), (2, {"ok": False, "error": "not_allowed"}))
        r = subprocess.run(cli + ["show", mid, "--json"], env=env, capture_output=True, text=True, timeout=30)
        v = json.loads(r.stdout)
        self.assertEqual((v["status"], v["tasks"][0]["status"]), ("draft", "todo"))
        r = subprocess.run(cli + ["list"], env=env, capture_output=True, text=True, timeout=30)
        self.assertIn("Draft", r.stdout)
        r = subprocess.run(cli + ["show", "m_000000000000"], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)
        self.assertIn("Task team command refused", r.stdout)


class M10Autostart(Base):
    def ready(self):
        self.mk(running=False)
        self.c("team", by(PM), {"move": "submit"}, now=T0)

    def test_autostart_after_30s(self):
        self.ready()
        M.tick(self.mid, self.env, now=T0 + 29)
        self.assertEqual(self.st()["status"], "ready")
        self.assertEqual(M.view(self.st(), now=T0 + 10)["autostart_in"], 20)
        M.tick(self.mid, self.env, now=T0 + 30)
        st = self.st()
        self.assertEqual(st["status"], "running")
        evs = [json.loads(x) for x in (M.mdir(self.mid, self.env) / "events.jsonl").read_text().splitlines()]
        ev = [e for e in evs if e["type"] == "team"][-1]           # S4 起 tick 之後還會寫監看事件
        self.assertEqual((ev["by"], ev["data"]["move"]), ("system", "autostart"))
        self.assertEqual(self.proved, [])                          # 自動開始不彈窗

    def test_any_write_after_30s_autostarts_first(self):
        self.ready()
        self.dispatch(now=T0 + 31)                                 # 派工前先把到期的倒數走完
        self.assertEqual(self.st()["status"], "running")

    def test_hold_stops_countdown_and_resubmit_restarts(self):
        self.ready()
        self.c("team", by(PM), {"move": "hold"}, now=T0 + 10)
        self.ok(M.tick, self.mid, self.env, now=T0 + 500)
        self.assertEqual(self.st()["status"], "ready")
        self.assertIsNone(M.view(self.st(), now=T0 + 500)["autostart_in"])
        self.c("team", by(PM), {"move": "submit"}, now=T0 + 600)
        M.tick(self.mid, self.env, now=T0 + 620)
        self.assertEqual(self.st()["status"], "ready")
        M.tick(self.mid, self.env, now=T0 + 630)
        self.assertEqual(self.st()["status"], "running")

    def test_only_system_and_only_when_due(self):
        self.ready()
        self.bad("bad_by", self.c, "team", by(PM), {"move": "autostart"}, now=T0 + 5)
        self.bad("bad_by", self.c, "team", "system", {"move": "pause"}, now=T0 + 1)

    def test_early_autostart_unreadable(self):
        self.ready()
        p = M.mdir(self.mid, self.env) / "events.jsonl"
        lines = p.read_text().splitlines()
        ev = {"v": 1, "seq": len(lines) + 1, "eid": "x_forged000001", "req": None, "at": T0 + 5, "by": "system",
              "type": "team", "data": {"move": "autostart"}}
        p.write_text("\n".join(lines + [json.dumps(ev)]) + "\n")
        self.bad("corrupt:%d" % ev["seq"], M.replay, self.mid, self.env)


class M11R1(Base):
    def forge(self, ev):
        p = M.mdir(self.mid, self.env) / "events.jsonl"
        lines = p.read_text().splitlines()
        ev = dict({"v": 1, "seq": len(lines) + 1, "req": None}, **ev)
        p.write_text("\n".join(lines + [json.dumps(ev)]) + "\n")
        return ev["seq"]

    def test_m1_started_by(self):
        self.mk(running=False)
        self.c("team", by(PM), {"move": "submit"}, now=T0)
        M.tick(self.mid, self.env, now=T0 + 30)
        st = self.st()
        self.assertEqual((st.get("started_by"), st.get("started_at")), ("autostart", T0 + 30))
        self.assertEqual(M.view(st, now=T0)["started_by"], "autostart")
        ch = json.loads((M.mdir(self.mid, self.env) / "charter.json").read_text())
        self.assertEqual((ch["started_by"], ch["started_at"]), ("autostart", T0 + 30))
        self.c("team", by(PM), {"move": "halt"}, now=T0 + 40)
        M.team(self.mid, "start", "human", env=self.env, now=T0 + 50)
        self.assertEqual((self.st()["started_by"], self.st()["started_at"]), ("human", T0 + 50))

    def test_m2_ci_head_bound_to_artifact(self):
        self.mk(tasks=3)
        self.to_qa("t1", head="abc1234")
        for rec in (self.receipt(kind="ci", head="fffffff"), self.receipt(kind="pr_merged", head="fffffff")):
            with self.subTest(rec=rec["kind"]):
                self.bad("receipt_stale", M.qa, self.mid, "t1", by(QA), "pass", rec, env=self.env, now=T0)
        self.ok(M.qa, self.mid, "t1", by(QA), "pass", self.receipt(kind="ci", head="abc1234def0"), env=self.env, now=T0)
        self.to_qa("t2", W2, head=None)                                 # 回報沒帶產物 ⇒ 只能 test / checklist
        self.bad("receipt_stale", M.qa, self.mid, "t2", by(QA), "pass", self.receipt("t2", kind="ci", artifact=None),
                 env=self.env, now=T0)
        self.ok(M.qa, self.mid, "t2", by(QA), "pass", self.receipt("t2", artifact=None), env=self.env, now=T0)

    def test_m3_machine_all(self):
        self.mk()
        self.to_qa()
        M.qa(self.mid, "t1", by(QA), "pass", self.receipt(kind="checklist"), env=self.env, now=T0)
        self.ok(self.c, "team", by(PM), {"move": "verify"})                # 純 checklist 的團照樣收得了
        v = M.view(self.st(), now=T0)
        self.assertEqual((v["status"], v["verified_pct"], v["machine_all"]), ("verified", 0, False))
        self.assertFalse(json.loads((M.mdir(self.mid, self.env) / "charter.json").read_text())["machine_all"])
        self.c("team", by(PM), {"move": "reopen"})
        self.c("amend", by(PM), {"accept": ["ci"]}, task="t1")
        M.qa(self.mid, "t1", by(QA), "pass", self.receipt(kind="ci", accept_rev=2), env=self.env, now=T0)
        self.assertTrue(M.view(self.st(), now=T0)["machine_all"])

    def test_l1_forged_report_after_lease_unreadable(self):
        self.mk()
        code = self.dispatch(now=T0)
        seq = self.forge({"eid": "e_forged000002", "at": T0 + M.LEASE_S + 5, "by": by(W1), "type": "report", "task": "t1",
                          "data": {"code": code, "state": "doing", "note": ""}})
        self.bad("corrupt:%d" % seq, M.replay, self.mid, self.env)

    def test_l1_forged_early_expire_unreadable(self):
        self.mk()
        self.dispatch(now=T0)
        seq = self.forge({"eid": "x_forged000003", "at": T0 + 10, "by": "system", "type": "expire", "task": "t1", "data": {}})
        self.bad("corrupt:%d" % seq, M.replay, self.mid, self.env)

    def test_l3_unknown_dep_refused(self):
        self.mk(running=False)
        self.bad("bad_dep", self.c, "task_add", by(PM), {"title": "x", "deps": ["t9"]})
        for bad in ([["t1"]], [1], "t1", {"t1": 1}):            # R2 N2:型別不對也是 bad_dep,不炸
            self.bad("bad_dep", self.c, "task_add", by(PM), {"title": "x", "deps": bad})
        self.assertEqual(len(self.st()["tasks"]), 1)

    def test_l4_unknown_id_leaves_no_dir(self):
        mid = "m_" + "0" * 12
        self.bad("no_mission", M.commit, mid, "team", by(PM), {"move": "submit"}, env=self.env, now=T0)
        self.bad("no_mission", M.tick, mid, self.env, now=T0)
        self.assertFalse(M.mdir(mid, self.env).exists())

    def test_l5_resend_does_not_ask_again(self):
        self.mk(running=False)
        self.c("team", by(PM), {"move": "submit"})
        a = M.team(self.mid, "start", "human", env=self.env, eid="start-0000001", now=T0)
        b = self.ok(M.team, self.mid, "start", "human", env=self.env, eid="start-0000001", now=T0 + 1)
        self.assertEqual((a["duplicate"], b["duplicate"], len(self.proved)), (False, True, 1))

    def test_l7_accept_shows_cancelled(self):
        self.mk(tasks=2)
        self.to_qa("t1")
        M.qa(self.mid, "t1", by(QA), "pass", self.receipt(), env=self.env, now=T0)
        self.c("cancel", by(PM), {}, task="t2")
        self.c("team", by(PM), {"move": "verify"})
        self.c("team", by(PM), {"move": "handoff"})
        M.team(self.mid, "accept", "human", env=self.env, now=T0)
        self.assertIn("1 tasks, 1 cancelled", self.proved[-1])

    def test_l8_snapshot_has_no_codes(self):
        self.mk()
        self.dispatch()
        raw = (M.mdir(self.mid, self.env) / "tasks.jsonl").read_text()
        self.assertNotIn('"code"', raw)
        self.assertNotIn('"codes"', raw)

    def test_l9_human_pause_only_human_resumes(self):
        self.mk()
        self.c("team", "human", {"move": "pause"})
        self.bad("not_allowed", self.c, "team", by(PM), {"move": "resume"})
        self.ok(self.c, "team", "human", {"move": "resume"})
        self.c("team", by(MON), {"move": "pause"})
        self.ok(self.c, "team", by(PM), {"move": "resume"})


class M12S2(Base):
    def cli(self, *argv):
        buf = _Buf()
        rc = M.run(list(argv) + ["--json"], env=self.env, out=buf)
        return rc, json.loads(buf.text)

    def test_qa_role_before_receipt(self):
        self.mk()
        self.to_qa()
        gone = str(self.root / "no-such-receipt.json")
        self.assertEqual(self.cli("task", self.mid, "qa", "t1", "--by", by(PM), "--pass", "--receipt", gone),
                         (2, {"ok": False, "error": "not_allowed"}))
        self.assertEqual(self.cli("task", self.mid, "qa", "t1", "--by", by(PM), "--pass"),
                         (2, {"ok": False, "error": "not_allowed"}))
        self.assertEqual(self.cli("task", self.mid, "qa", "t1", "--by", by(QA), "--pass", "--receipt", gone),
                         (2, {"ok": False, "error": "bad_receipt"}))
        self.assertEqual(self.cli("task", "m_" + "0" * 12, "qa", "t1", "--by", by(QA), "--pass", "--receipt", gone),
                         (2, {"ok": False, "error": "no_mission"}))

    def test_why_chain(self):
        self.mk()
        code = self.to_qa()
        M.qa(self.mid, "t1", by(QA), "pass", self.receipt(), env=self.env, now=T0)
        rc, body = self.cli("why", self.mid, "t1")
        self.assertEqual((rc, body["ok"], body["task"], body["status"]), (0, True, "t1", "passed"))
        self.assertEqual([e["type"] for e in body["events"]], ["task_add", "dispatch", "report", "report", "qa"])
        self.assertEqual([e["by"] for e in body["events"]][1:], [by(PM), by(W1), by(W1), by(QA)])
        self.assertEqual(body["events"][-1]["data"]["receipt"]["kind"], "test")
        self.assertNotIn(code, json.dumps(body))
        self.assertNotIn('"code"', json.dumps(body))
        for argv, want in ((("why", self.mid, "t9"), "no_task"), (("why", self.mid, "x1"), "bad_task"),
                           (("why", "m_" + "0" * 12, "t1"), "no_mission")):
            with self.subTest(argv=argv):
                self.assertEqual(self.cli(*argv), (2, {"ok": False, "error": want}))

    def test_help_explains_formats(self):
        subs = M._parser()._subparsers._group_actions[0].choices
        task, roster = subs["task"].format_help(), subs["roster"].format_help()
        for want in ("seat:ee842", "human", "8", "64"):
            self.assertIn(want, task)
        self.assertIn("ee842", roster)
        top = M._parser().format_help()
        self.assertIn("why", top)
        self.assertIn("system clock", top)
        self.assertFalse(any(o.startswith("--now") for sp in subs.values() for act in sp._actions for o in act.option_strings))


KNIVES = [
    ("K1", '''        if st["status"] not in frm:
            raise MissionError("bad_transition")''', "", "M1Team.test_illegal_transitions"),
    ("K2", '''        if t["status"] in LEASED and (t.get("lease_until") or 0) < now:''', '''        if False:''',
     "M3Lease.test_expired_lease_back_to_todo"),
    ("K3", '''            if d.get("code") != t.get("code"):''', '''            if False:''', "M4StaleCode.test_old_code_refused_after_redispatch"),
    ("K4", '''            _need(st, by, ("qa",))''', '''            _need(st, by, ("qa", "pm", "worker", "human", "monitor"))''',
     "M5Pass.test_only_qa_receipt_flips_passed"),
    ("K5", '''            if by[5:] in t["workers"]:''', '''            if False:''', "M5Pass.test_maker_cannot_check"),
    ("K6", '''    if rec.get("task") != tid or rec.get("accept_rev") != t["accept_rev"]:''', '''    if rec.get("task") != tid:''',
     "M5Pass.test_receipt_bound_to_accept_rev_and_artifact"),
    ("K7", '''        if eid and eid in st["eids"]:
            if st["eids"][eid]["req"] != req:''', '''        if False:
            if st["eids"][eid]["req"] != req:''', "M6Ids.test_same_eid_not_repeated"),
    ("K8", '''                torn = pos                     # 斷電寫一半的最後一行:當沒寫
                break''', '''                pass''', "M7Replay.test_torn_tail_ignored_and_truncated"),
    ("K9", '''            raise MissionError("corrupt:%s:%s" % (ev.get("seq"), err))
    return st, torn, evs''', '''            st["seq"] = ev.get("seq") or st["seq"]
    return st, torn, evs''', "M7Replay.test_tampered_event_unreadable"),
    ("K10", '''    if move in HUMAN_SITES:''', '''    if False:''', "M8Human.test_start_needs_touchid"),
    ("K11", '''            if not live or any(t["status"] != "passed" for t in live):''', '''            if not live:''',
     "M8Human.test_verify_needs_all_passed"),
    ("K12", '''        if d["move"] == "autostart" and (st.get("hold") or at - int(st.get("ready_at") or at) < AUTOSTART_S):''',
     '''        if d["move"] == "autostart" and st.get("hold"):''', "M10Autostart.test_early_autostart_unreadable"),
    ("K13", '''    if st["status"] == "ready" and not st.get("hold") and now - int(st.get("ready_at") or now) >= AUTOSTART_S:''',
     '''    if st["status"] == "ready" and now - int(st.get("ready_at") or now) >= AUTOSTART_S:''',
     "M10Autostart.test_hold_stops_countdown_and_resubmit_restarts"),
    ("K14", '''            st.update(started_by="autostart" if d["move"] == "autostart" else "human", started_at=at)   # #414 R1 M1''',
     '''            st.update(started_by="human", started_at=at)''', "M11R1.test_m1_started_by"),
    ("K15", '''        if not art or not HEAD_RE.match(head) or not (head.startswith(art) or art.startswith(head)):''',
     '''        if False:''', "M11R1.test_m2_ci_head_bound_to_artifact"),
    ("K16", '''            if t["status"] not in REPORT_MOVES or (t.get("lease_until") or 0) < at:''',
     '''            if t["status"] not in REPORT_MOVES:''', "M11R1.test_l1_forged_report_after_lease_unreadable"),
    ("K17", '''            if st["status"] != "running" or t["status"] not in LEASED or (t.get("lease_until") or 0) >= at:''',
     '''            if st["status"] != "running" or t["status"] not in LEASED:''', "M11R1.test_l1_forged_early_expire_unreadable"),
    ("K18", '''        if not isinstance(deps, list) or any(not isinstance(x, str) or x not in st["tasks"] for x in deps):''', '''        if False:''', "M11R1.test_l3_unknown_dep_refused"),
    ("K19", '''        if eid and eid in st["eids"]:              # 重送''', '''        if False:              # 重送''',
     "M11R1.test_l5_resend_does_not_ask_again"),
    ("K20", '''        if d["move"] == "resume" and st.get("paused_by") == "human" and by != "human":''',
     '''        if False:''', "M11R1.test_l9_human_pause_only_human_resumes"),
    ("K21", '''        _need(st, by, ("qa",))
        if a.verdict == "pass" and not a.receipt:''', '''        if a.verdict == "pass" and not a.receipt:''', "M12S2.test_qa_role_before_receipt"),
    ("K22", '''if k not in ("code", "receipt")}''', '''if k not in ("receipt",)}''', "M12S2.test_why_chain"),
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
