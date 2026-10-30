#!/usr/bin/env python3
"""任務團 S4 前半:預算硬上限 + 規則監看 + aib mission watch(10-29,SPEC-mission-monitor-fd787-1029 §5 / §8)。

B1 分鐘只算 running(暫停不算,K2)
B2 80% 寫一次 warn,再 tick 不重寫(K1)
B3 100% 寫 budget/halt + team/halt(reason=budget),再 tick 不重寫
B4 預算叫停後本人直接開始 ⇒ over_budget;本人提高預算(Touch ID mission.budget)再開始才過;PM 只能調低;沒改內容拒 no_change
B4b 封存的團 tick 不寫任何事件(負載超過 12 也不寫)
B5 席數硬上限:加人超過 seats_max ⇒ over_seats;有人離開後可加
B6 額度:基準 40%、現在 64%、上限 30% ⇒ 用量 24% 寫 warn;到 70% ⇒ halt
B7 額度視窗重設:現值比基準小 ⇒ 基準換現值、從 0 累加
B8 額度量不到:不叫停、不當 0,寫一筆 anomaly/quota_unmeasured;watch 寫「量不到」
B9 重放:事件檔重開 ⇒ 狀態與門檻記號一樣、不重寫
B10 偽造:座位寫的 budget 事件 ⇒ 重放 bad_by(K3)
M1 idle 開 / 解除;M2 不誤報(暫停 / monitor / 9 分);M3 aib now 過舊 / 沒檔 / 量不到;
M4 降速回差(K4);M5 品管連退兩次(pass / amend 重新計);M6 卡關 > 15 分;M7 同一異常 100 次 tick 只寫一筆;
M8 evaluate 是純函式(不改 st、不碰檔)
W1 watch 一屏、四語鍵齊;W2 量不到不寫 0%;W3 退出碼 0 / 3 / 2、--json 欄位固定
G1 approve_gate 登記 mission.budget 為高風險類(不開時間窗)
R1 Codex r1(10-30,@b5799 接手):
  H1 超限沒 tick 也不能派工(派工當下寫叫停、拒 over_budget;同 eid 重送照回原結果);暫停中超限不能恢復;額度量不到 ⇒ 派工 / 開始 / 自動開始都拒(fail-close)
  H2 tick 寫完 budget/halt、還沒寫 team/halt 就中斷 ⇒ 下一次 tick / 派工補叫停;每個寫檔邊界都驗
  H3 調低別的維度不清額度叫停;改了額度上限、用量還在上限以上 ⇒ 開始仍拒
  M1 watch 只有異常 / 卡住也回 3
  M2 建團 / 改預算:負數、0、非整數、不認得的欄位、超過上限 ⇒ bad_budget(不靜默變不限;r2 L3 CLI 的 0 同樣拒)
R2 r2(@f5fb8 Codex 1H/1M/1L + @aeb40 L3/L5):
  High main 合法的舊建團事件(minutes 100001 / seats_max 501)升級後照讀;改預算只驗這次改的欄位
  Medium 額度量不到時先補已知超限 / 叫停記號的叫停;派工路徑、快照邊界中斷都能恢復
  Low 「已通知」(budget_marks)與「仍要擋」(budget_block)分開存:調低重新通知、只有放寬那一維才解除阻擋
  L3 CLI 建團 --minutes / --seats-max / --quota-pct 0 ⇒ bad_budget、不留空團目錄;L5 模組說明照實寫不防同帳號 AI
R3 r3(@f5fb8 Codex 1H + M1):budget_set 帶 reset:"dims" 才用新語意,沒帶的舊事件照舊版(改預算清掉記號與阻擋),
  舊版完整序列(叫停 → 調低 → 開始 → 再叫停 → 再調低)升級後照讀;不認得的旗標 ⇒ corrupt;tick 每個寫檔邊界(基準 / 記號 / 叫停 / 快照)中斷都補齊
刀 K1–K6(K5 封存不寫、K6 沒改內容拒);K7–K11 對 R1 五題;K12 對 r2 L3(CLI 的 0 不能變不限);K13–K16 對 r2 High / Medium / Low;K17 對 r3 High
"""
import copy
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))

import approve_gate as AG  # noqa: E402
import mission as M  # noqa: E402
import mission_monitor as MM  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

PM, W1, W2, QA, MON = "aaaa1", "bbbb2", "cccc3", "dddd4", "eeee5"
T0 = 1_800_000_000
MIN = 60
Q = "claude/5h/shared"


def by(s):
    return "seat:" + s


class FP:
    """假量測。load / usage 設 None ⇒ 量不到;now_at:{席: epoch | None(沒檔) | "x"(量不到)},沒列的席 = 剛更新。"""

    def __init__(self, load=1.0, usage=None, now_at=None):
        self.load, self.usage_v, self.now_at, self.t = load, usage, dict(now_at or {}), T0

    def load1(self):
        if self.load is None:
            raise MM.Unmeasured("load_test")
        return self.load

    def seat_now(self, code):
        v = self.now_at.get(code, self.t)
        if v == "x":
            raise MM.Unmeasured("now_test")
        return v

    def usage(self):
        if self.usage_v is None:
            raise MM.Unmeasured("usage_test")
        return dict(self.usage_v)


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-mission-mon-")
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.root), "EZ2_AIB_HOME": str(self.root), "HOME": str(self.root),
                    "EZ2_AIB_LOCALE": "zh-TW", "PATH": os.environ.get("PATH", "")}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        self.proved = []
        p2 = mock.patch.object(M, "PROVE", lambda r, env: self.proved.append(str(r)) or self.prove_why)
        p2.start()
        self.addCleanup(p2.stop)
        self.prove_why = None
        self.fp = FP()
        p3 = mock.patch.object(M, "PROBE", self.fp)   # 寫入路徑(派工 / 進 running)也用同一組假量測(#427 r1 H1)
        p3.start()
        self.addCleanup(p3.stop)

    def c(self, typ, who, data, task=None, now=T0):
        return M.commit(self.mid, typ, who, data, env=self.env, task=task, now=now)

    def ok(self, fn, *a, **k):
        """該過的那一步:被拒算斷言紅(刀測要的是 FAIL 不是 ERROR)。"""
        try:
            return fn(*a, **k)
        except M.MissionError as err:
            self.fail("不該拒:%s" % err)

    def bad(self, want, fn, *a, **k):
        with self.assertRaises(M.MissionError) as cm:
            fn(*a, **k)
        self.assertEqual(str(cm.exception).split(":")[0] if not want.startswith("corrupt") else str(cm.exception)[:len(want)],
                         want)

    def mk(self, budget=None, roster=((W1, "worker"), (W2, "worker"), (QA, "qa"), (MON, "monitor")), tasks=1, running=True,
           usage=None):
        self.mid = M.new(by(PM), "S4 監看", "goal", ["green"], budget=budget or {}, env=self.env, now=T0)["id"]
        for s, r in roster:
            self.c("roster", by(PM), {"op": "add", "seat": s, "role": r})
        for i in range(tasks):
            self.c("task_add", by(PM), {"title": "task %d" % (i + 1), "accept": ["rc 0"], "deps": []})
        if (budget or {}).get("quota_pct"):          # 設了額度上限 ⇒ 開始時要量得到(#427 r1 H1 fail-close),開始那刻記基準
            self.fp.usage_v = dict(usage or {Q: 0})
        if running:
            self.c("team", by(PM), {"move": "submit"})
            M.team(self.mid, "start", "human", env=self.env, now=T0)
        return self.mid

    def tick(self, now):
        self.fp.t = now
        return self.ok(M.tick, self.mid, self.env, now=now, probe=self.fp)

    def st(self):
        return M.replay(self.mid, self.env)

    def events(self, typ=None):
        evs = [json.loads(x) for x in (M.mdir(self.mid, self.env) / "events.jsonl").read_text().splitlines()]
        return [e for e in evs if typ is None or e["type"] == typ]

    def budget_evs(self, level=None):
        return [e for e in self.events("budget") if level is None or e["data"]["level"] == level]

    def dispatch(self, tid="t1", to=W1, now=T0):
        code = os.urandom(6).hex()
        self.c("dispatch", by(PM), {"to": to, "code": code}, task=tid, now=now)
        return code

    def report(self, state, code, tid="t1", who=W1, now=T0, artifact=None):
        return self.c("report", by(who), {"code": code, "state": state, "artifact": artifact, "note": ""}, task=tid, now=now)

    def to_qa(self, tid="t1", who=W1, now=T0):
        code = self.dispatch(tid, who, now)
        self.report("doing", code, tid, who, now)
        self.report("qa", code, tid, who, now, artifact="abc1234")

    def receipt(self, tid="t1", accept_rev=1):
        return {"kind": "test", "task": tid, "accept_rev": accept_rev, "artifact": "abc1234", "cmd": "aib test x.py",
                "rc": 0, "passed": 5, "total": 5}

    def watch(self, now, as_json=True):
        out = io.StringIO()
        self.fp.t = now
        rc = MM.watch_cmd(self.mid, as_json, env=self.env, out=out, now=now, probe=self.fp)
        return rc, (json.loads(out.getvalue()) if as_json else out.getvalue())


class B1to5Budget(Base):
    def test_b1_minutes_only_running(self):
        self.mk({"minutes": 100})
        self.c("team", by(PM), {"move": "pause"}, now=T0 + 10 * MIN)
        self.c("team", by(PM), {"move": "resume"}, now=T0 + 40 * MIN)
        self.assertEqual(M.used_minutes(self.st(), T0 + 45 * MIN), 15)

    def test_b2_warn_once(self):
        self.mk({"minutes": 20})
        self.tick(T0 + 16 * MIN)
        warn = self.budget_evs("warn")
        self.assertEqual([(e["data"]["dim"], e["data"]["used"], e["data"]["cap"], e["by"]) for e in warn],
                         [("minutes", 16, 20, "system")])
        self.tick(T0 + 17 * MIN)
        self.assertEqual(len(self.budget_evs("warn")), 1)
        self.assertEqual(self.st()["status"], "running")

    def test_b3_halt_once(self):
        self.mk({"minutes": 20})
        self.tick(T0 + 20 * MIN)
        self.assertEqual([e["data"]["dim"] for e in self.budget_evs("halt")], ["minutes"])
        halt = [e for e in self.events("team") if e["data"]["move"] == "halt"]
        self.assertEqual([(e["by"], e["data"]["reason"]) for e in halt], [("system", "budget")])
        st = self.st()
        self.assertEqual((st["status"], st["halt_reason"]), ("halted", "budget"))
        self.tick(T0 + 25 * MIN)
        self.assertEqual(len(self.budget_evs()), 1)
        self.assertEqual(M.used_minutes(self.st(), T0 + 60 * MIN), 20)   # 叫停後不再算

    def test_b4_restart_needs_budget_raise(self):
        self.mk({"minutes": 20})
        self.tick(T0 + 20 * MIN)
        n = len(self.proved)
        self.bad("over_budget", M.team, self.mid, "start", "human", env=self.env, now=T0 + 21 * MIN)
        self.bad("not_allowed", M.budget_set, self.mid, by(PM), {"minutes": 60}, env=self.env, now=T0 + 21 * MIN)
        self.prove_why = "touchid_cancel"
        self.bad("touchid_cancel", M.budget_set, self.mid, "human", {"minutes": 60}, env=self.env, now=T0 + 21 * MIN)
        self.prove_why = None
        self.ok(M.budget_set, self.mid, "human", {"minutes": 60}, env=self.env, now=T0 + 21 * MIN)
        self.assertEqual(self.st()["budget"]["minutes"], 60)
        self.assertEqual(self.st()["budget_marks"], {})
        self.ok(M.team, self.mid, "start", "human", env=self.env, now=T0 + 22 * MIN)
        self.assertEqual(self.st()["status"], "running")
        self.assertTrue(any("60" in r for r in self.proved[n:]), self.proved[n:])
        # PM 調低不用按;提高(含拿掉上限)一律本人
        k = len(self.proved)
        self.ok(M.budget_set, self.mid, by(PM), {"minutes": 50}, env=self.env, now=T0 + 23 * MIN)
        self.assertEqual(len(self.proved), k)
        self.bad("not_allowed", M.budget_set, self.mid, by(PM), {"minutes": 0}, env=self.env, now=T0 + 23 * MIN)
        self.bad("not_allowed", self.c, "budget_set", by(PM), {"budget": {"minutes": 90}, "proved": True}, now=T0 + 23 * MIN)
        self.bad("not_proved", self.c, "budget_set", "human", {"budget": {"minutes": 90}}, now=T0 + 23 * MIN)
        self.bad("no_change", M.budget_set, self.mid, by(PM), {"minutes": 50}, env=self.env, now=T0 + 23 * MIN)
        self.bad("no_change", M.budget_set, self.mid, by(PM), {}, env=self.env, now=T0 + 23 * MIN)

    def test_b4b_archived_tick_writes_nothing(self):
        self.mk()
        self.to_qa()
        M.qa(self.mid, "t1", by(QA), "pass", self.receipt(), env=self.env, now=T0)
        self.c("team", by(MON), {"move": "verify"})
        self.c("team", by(PM), {"move": "handoff"})
        M.team(self.mid, "accept", "human", env=self.env, now=T0)
        n = len(self.events())
        self.fp.load = 13.0
        self.tick(T0 + 30 * MIN)
        self.assertEqual(len(self.events()), n)

    def test_b5_seats_hard_cap(self):
        self.mk({"seats_max": 3}, roster=((W1, "worker"), (W2, "worker")))
        self.bad("over_seats", self.c, "roster", by(PM), {"op": "add", "seat": QA, "role": "qa"})
        self.c("roster", by(PM), {"op": "remove", "seat": W2})
        self.ok(self.c, "roster", by(PM), {"op": "add", "seat": QA, "role": "qa"})
        self.tick(T0 + MIN)
        self.assertEqual(self.st()["status"], "running")          # 用滿不是超過 ⇒ 不叫停
        self.ok(M.budget_set, self.mid, by(PM), {"seats_max": 2}, env=self.env, now=T0 + 2 * MIN)
        self.tick(T0 + 3 * MIN)
        self.assertEqual(self.st()["halt_reason"], "budget")       # 調低到人數以下 ⇒ 叫停


class B6to10Quota(Base):
    def test_b6_quota_warn_then_halt(self):
        self.mk({"quota_pct": 30}, usage={Q: 40, "claude/7d/shared": 10})
        self.tick(T0 + MIN)
        self.assertEqual(self.st()["quota_base"], {Q: 40, "claude/7d/shared": 10})
        self.fp.usage_v = {Q: 64, "claude/7d/shared": 12}
        self.tick(T0 + 2 * MIN)
        self.assertEqual([(e["data"]["used"], e["data"]["cap"]) for e in self.budget_evs("warn")], [(24, 30)])
        self.fp.usage_v = {Q: 50, "claude/7d/shared": 40}      # 7 天窗用得多 ⇒ 取大(5h 窗重設不影響)
        self.tick(T0 + 3 * MIN)
        self.assertEqual([(e["data"]["dim"], e["data"]["used"]) for e in self.budget_evs("halt")], [("quota", 30)])
        self.assertEqual(self.st()["status"], "halted")

    def test_b7_window_reset(self):
        self.mk({"quota_pct": 30}, usage={Q: 90})
        self.tick(T0 + MIN)
        self.fp.usage_v = {Q: 5}
        self.tick(T0 + 2 * MIN)
        self.assertEqual(self.st()["quota_base"], {Q: 5})
        self.assertEqual(self.budget_evs("warn"), [])
        self.fp.usage_v = {Q: 20}
        rc, w = self.watch(T0 + 3 * MIN)
        self.assertEqual(w["budget"]["quota"]["used"], 15)

    def test_b8_quota_unmeasured(self):
        self.mk({"quota_pct": 30}, usage={Q: 10})
        self.fp.usage_v = None
        for i in range(3):
            self.tick(T0 + (i + 1) * MIN)
        an = [e for e in self.events("anomaly") if e["data"]["rule"] == "quota_unmeasured"]
        self.assertEqual(len(an), 1)
        self.assertEqual(self.st()["status"], "running")
        rc, text = self.watch(T0 + 5 * MIN, as_json=False)
        line = [x for x in text.splitlines() if x.startswith("預算")][0]
        self.assertIn("量不到", line)
        self.assertNotIn("0%", line)
        self.fp.usage_v = {Q: 10}
        self.tick(T0 + 6 * MIN)
        self.assertNotIn("quota_unmeasured:quota", self.st()["anomalies"])

    def test_b9_replay_same(self):
        self.mk({"minutes": 20})
        self.tick(T0 + 16 * MIN)
        self.tick(T0 + 20 * MIN)
        a, b = self.st(), self.st()
        self.assertEqual(a["budget_marks"], {"minutes": ["warn", "halt"]})
        self.assertEqual((a["budget_marks"], a["status"], a["run_s"]), (b["budget_marks"], b["status"], b["run_s"]))
        n = len(self.events())
        self.tick(T0 + 21 * MIN)
        self.assertEqual(len(self.budget_evs()), 2)
        self.assertLessEqual(len(self.events()) - n, 0 + len([k for k in a["anomalies"]]))

    def test_b10_forged_budget_unreadable(self):
        self.mk({"minutes": 20})
        seq = self.st()["seq"]
        line = {"v": 1, "seq": seq + 1, "eid": "e_forged0001", "req": None, "at": T0 + 1, "by": by(PM), "type": "budget",
                "data": {"level": "warn", "dim": "minutes", "used": 1, "cap": 20}}
        with open(M.mdir(self.mid, self.env) / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
        with self.assertRaises(M.MissionError) as cm:
            M.replay(self.mid, self.env)
        self.assertTrue(str(cm.exception).endswith("bad_by"), str(cm.exception))


class M1to4Rules(Base):
    def anomalies(self):
        return sorted(self.st()["anomalies"])

    def test_m1_idle_open_and_end(self):
        self.mk()
        self.tick(T0 + 11 * MIN)
        self.assertEqual(self.anomalies(), ["idle:%s" % PM, "idle:%s" % W1, "idle:%s" % W2])
        code = self.dispatch(now=T0 + 12 * MIN)
        self.report("doing", code, now=T0 + 12 * MIN)
        self.tick(T0 + 13 * MIN)
        self.assertEqual(self.anomalies(), ["idle:%s" % W2])
        ends = [e["data"]["key"] for e in self.events("anomaly") if e["data"]["end"]]
        self.assertEqual(sorted(ends), [PM, W1])

    def test_m2_idle_no_false_alarm(self):
        self.mk()
        self.tick(T0 + 9 * MIN)
        self.assertEqual(self.anomalies(), [])
        self.c("team", by(PM), {"move": "pause"}, now=T0 + 9 * MIN)
        self.tick(T0 + 30 * MIN)
        self.assertEqual(self.anomalies(), [])
        self.c("team", by(PM), {"move": "resume"}, now=T0 + 30 * MIN)
        self.tick(T0 + 35 * MIN)                                   # 恢復後從恢復那刻算
        self.assertEqual(self.anomalies(), [])
        self.tick(T0 + 41 * MIN)
        self.assertNotIn("idle:%s" % MON, self.anomalies())
        self.assertNotIn("idle:%s" % QA, self.anomalies())

    def test_m3_now_stale_and_missing(self):
        self.mk()
        now = T0 + 5 * MIN
        self.fp.now_at = {W1: now - 16 * MIN, W2: None, QA: "x"}
        self.tick(now)
        self.assertEqual(self.anomalies(), ["now_missing:%s" % W2, "now_stale:%s" % W1])
        self.fp.now_at = {W1: "x", W2: None, QA: "x"}               # 量不到 ⇒ 維持原樣
        self.tick(now + MIN)
        self.assertEqual(self.anomalies(), ["now_missing:%s" % W2, "now_stale:%s" % W1])
        self.fp.now_at = {}
        self.tick(now + 2 * MIN)
        self.assertEqual(self.anomalies(), [])

    def test_m4_throttle_hysteresis(self):
        self.mk()
        seen = []
        for load in (12.5, 11.0, 13.0, 10.5, None, 9.8, 11.0):
            self.fp.load = load
            self.tick(T0 + MIN)
            seen.append(self.st()["throttle"])
        self.assertEqual(seen, [True, True, True, True, True, False, False])
        self.assertEqual([e["data"]["on"] for e in self.events("throttle")], [True, False])
        self.assertEqual(self.st()["status"], "running")          # 降速只標記,不叫停


class M5to8Rules(Base):
    def test_m5_qa_loop(self):
        self.mk()
        self.to_qa()
        M.qa(self.mid, "t1", by(QA), "return", why="red", env=self.env, now=T0)
        self.tick(T0 + MIN)
        self.assertEqual(self.st()["anomalies"], {})
        self.to_qa(now=T0 + MIN)
        M.qa(self.mid, "t1", by(QA), "return", why="red", env=self.env, now=T0 + MIN)
        self.tick(T0 + 2 * MIN)
        self.assertIn("qa_loop:t1", self.st()["anomalies"])
        self.c("amend", by(PM), {"accept": ["rc 0", "ci"]}, task="t1", now=T0 + 3 * MIN)   # 改驗收 ⇒ 重新計
        self.tick(T0 + 3 * MIN)
        self.assertNotIn("qa_loop:t1", self.st()["anomalies"])
        self.to_qa(now=T0 + 4 * MIN)
        M.qa(self.mid, "t1", by(QA), "return", why="red", env=self.env, now=T0 + 4 * MIN)
        self.tick(T0 + 5 * MIN)
        self.assertNotIn("qa_loop:t1", self.st()["anomalies"])
        self.to_qa(now=T0 + 5 * MIN)
        M.qa(self.mid, "t1", by(QA), "pass", self.receipt(accept_rev=2), env=self.env, now=T0 + 5 * MIN)
        self.assertEqual(self.st()["tasks"]["t1"]["returns"], 0)

    def test_m6_blocked_long(self):
        self.mk()
        code = self.dispatch(now=T0)
        self.report("doing", code, now=T0)
        self.report("blocked", code, now=T0 + MIN)
        self.report("blocked", code, now=T0 + 11 * MIN)            # 卡關中再回報(續租)不重算卡關起點
        self.tick(T0 + 15 * MIN)
        self.assertNotIn("blocked_long:t1", self.st()["anomalies"])
        self.tick(T0 + 17 * MIN)
        self.assertIn("blocked_long:t1", self.st()["anomalies"])
        self.report("doing", code, now=T0 + 18 * MIN)
        self.tick(T0 + 18 * MIN)
        self.assertNotIn("blocked_long:t1", self.st()["anomalies"])

    def test_m7_dedupe_cap(self):
        self.mk()
        self.fp.load = 13.0
        self.tick(T0 + 11 * MIN)
        n = len(self.events())
        for i in range(99):
            self.tick(T0 + 11 * MIN + i)
        self.assertEqual(len(self.events()), n)

    def test_m8_evaluate_pure(self):
        self.mk({"minutes": 20, "quota_pct": 30}, usage={Q: 50})
        self.fp.load, self.fp.usage_v, self.fp.now_at = 13.0, {Q: 50}, {W1: None}
        st = self.st()
        before = copy.deepcopy(st)
        with mock.patch("builtins.open", side_effect=AssertionError("碰檔")), \
                mock.patch("os.open", side_effect=AssertionError("碰檔")):
            a = MM.evaluate(st, T0 + 20 * MIN, self.fp)
            b = MM.evaluate(st, T0 + 20 * MIN, self.fp)
        self.assertEqual(a, b)
        self.assertEqual(st, before)
        kinds = [(t, d.get("level") or d.get("rule") or d.get("move") or d.get("on")) for t, d in a]
        self.assertIn(("team", "halt"), kinds)
        self.assertIn(("throttle", True), kinds)


class W1to3Watch(Base):
    def test_w1_screen_and_i18n(self):
        self.mk({"minutes": 60, "seats_max": 6, "quota_pct": 30}, usage={Q: 40})
        self.tick(T0 + MIN)
        rc, text = self.watch(T0 + 2 * MIN, as_json=False)
        lines = text.rstrip("\n").splitlines()
        self.assertEqual(len(lines), 7, text)
        for head in ("任務團", "進度", "預算", "負載", "卡關", "異常", "待人"):
            self.assertTrue(any(x.startswith(head) for x in lines), head)
        src = (ROOT / "src" / "cockpit" / "mission_monitor.py").read_text(encoding="utf-8")
        used = set(re.findall(r'(?<![\w.])T\("([a-z_]+)"[,)]', src)) | {"load", "w_hold", "w_handed"}
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8"))
            have = {k[len("aib.mission.watch."):] for k in d if k.startswith("aib.mission.watch.")}
            self.assertEqual(used - have, set(), lang)
            self.assertIn("aib.mission.touchid_budget", d, lang)

    def test_w2_unmeasured_not_zero(self):
        self.mk({"quota_pct": 30}, usage={Q: 40})
        self.fp.usage_v, self.fp.load = None, None
        rc, w = self.watch(T0 + MIN)
        self.assertEqual((w["budget"]["quota"]["used"], w["budget"]["quota"]["pct"]), (None, None))
        self.assertEqual(w["budget"]["quota"]["unmeasured"], "usage_test")
        self.assertIsNone(w["load1"])
        rc, text = self.watch(T0 + MIN, as_json=False)
        self.assertNotIn("0%", [x for x in text.splitlines() if x.startswith("預算")][0])
        self.assertIn("量不到", [x for x in text.splitlines() if x.startswith("負載")][0])

    def test_w3_exit_codes_and_fields(self):
        self.mk({"minutes": 20})
        rc, w = self.watch(T0 + MIN)
        self.assertEqual(rc, 0)
        self.assertEqual(set(w), {"v", "id", "title", "status", "run_min", "progress", "budget", "load1", "throttle",
                                  "stuck", "anomalies", "waiting", "ok"})
        self.assertEqual(set(w["budget"]), {"minutes", "seats", "quota"})
        self.tick(T0 + 20 * MIN)
        rc, w = self.watch(T0 + 21 * MIN)
        self.assertEqual(rc, 3)
        self.assertEqual(w["waiting"], [{"kind": "halted", "reason": "budget"}])
        out = io.StringIO()
        self.assertEqual(MM.watch_cmd("m_000000000000", True, env=self.env, out=out, now=T0, probe=self.fp), 2)
        self.assertEqual(json.loads(out.getvalue())["error"], "no_mission")
        self.assertEqual(M.run(["watch", self.mid, "--json"], env=self.env, out=io.StringIO()), 3)


class G1Gate(unittest.TestCase):
    def test_budget_site_high_risk(self):
        spec = AG.SITES["mission.budget"]
        self.assertEqual((spec["mod"], spec["key"]), ("cockpit/mission.py", "aib.mission.touchid_budget"))
        self.assertTrue(AG.CATEGORIES[spec["cat"]]["high_risk"])
        self.assertEqual(AG.CATEGORIES[spec["cat"]]["max_window"], 0)


class R1Codex(Base):
    """#427 Codex r1:3 High / 2 Medium 的回歸。"""

    def test_h1_dispatch_over_budget_without_tick(self):
        self.mk({"minutes": 1}, tasks=2)
        code = os.urandom(6).hex()
        first = self.ok(M.commit, self.mid, "dispatch", by(PM), {"to": W1, "code": code}, env=self.env, task="t1",
                        eid="e_dispatch_t1", now=T0 + 30)
        self.bad("over_budget", self.dispatch, "t2", W2, T0 + MIN)
        st = self.st()
        self.assertEqual((st["status"], st["halt_reason"]), ("halted", "budget"))
        self.assertEqual(st["tasks"]["t2"]["status"], "todo")
        self.assertEqual([e["data"]["dim"] for e in self.budget_evs("halt")], ["minutes"])
        again = self.ok(M.commit, self.mid, "dispatch", by(PM), {"to": W1, "code": code}, env=self.env, task="t1",
                        eid="e_dispatch_t1", now=T0 + 2 * MIN)
        self.assertEqual((again["duplicate"], again["seq"]), (True, first["seq"]), "同 eid 重送照回原結果,不當新派工")

    def test_h1_resume_over_budget(self):
        self.mk({"minutes": 1})
        self.c("team", by(PM), {"move": "pause"}, now=T0 + MIN + 5)        # 超限了還沒 tick 就暫停
        self.bad("over_budget", self.c, "team", by(PM), {"move": "resume"}, None, T0 + 2 * MIN)
        self.assertEqual(self.st()["status"], "paused")

    def test_h1_quota_unmeasured_fail_close(self):
        self.mk({"quota_pct": 30}, usage={Q: 10}, tasks=2)
        self.fp.usage_v = None
        self.bad("quota_unmeasured", self.dispatch, "t1", W1, T0 + MIN)
        self.assertEqual(self.st()["tasks"]["t1"]["status"], "todo")
        self.fp.usage_v = {Q: 12}
        self.ok(self.dispatch, "t1", W1, T0 + 2 * MIN)
        self.mid = M.new(by(PM), "第二團", "goal", ["green"], budget={"quota_pct": 30}, env=self.env, now=T0)["id"]
        self.c("task_add", by(PM), {"title": "x", "accept": ["rc 0"], "deps": []})
        self.c("team", by(PM), {"move": "submit"})
        self.fp.usage_v = None
        self.bad("quota_unmeasured", M.team, self.mid, "start", "human", env=self.env, now=T0)
        self.tick(T0 + M.AUTOSTART_S + 1)
        self.assertEqual(self.st()["status"], "ready", "量不到額度不自動開始")
        self.fp.usage_v = {Q: 5}
        self.tick(T0 + M.AUTOSTART_S + 2)
        self.assertEqual(self.st()["status"], "running")

    def test_h2_halt_mark_without_team_halt_recovers(self):
        for crash_at, via in ((1, "tick"), (1, "dispatch"), (0, "tick")):
            with self.subTest(crash_at=crash_at, via=via):
                self.mk({"minutes": 20}, tasks=1)
                real, n = M._append, []

                def flaky(d, ev, torn):
                    if ev.get("by") == "system" and ev["type"] in ("budget", "team"):
                        if len(n) == crash_at:
                            raise OSError("斷電")
                        n.append(ev["type"])
                    return real(d, ev, torn)
                with mock.patch.object(M, "_append", flaky), self.assertRaises(OSError):
                    M.tick(self.mid, self.env, now=T0 + 20 * MIN, probe=self.fp)
                st = self.st()
                self.assertEqual(st["status"], "running", "中斷時團還沒停")
                self.assertEqual(st["budget_marks"], {"minutes": ["halt"]} if crash_at else {})
                if via == "tick":
                    self.tick(T0 + 21 * MIN)
                else:
                    self.bad("over_budget", self.dispatch, "t1", W1, T0 + 21 * MIN)
                st = self.st()
                self.assertEqual((st["status"], st["halt_reason"]), ("halted", "budget"))
                self.assertEqual(len(self.budget_evs("halt")), 1, "叫停記號只一筆")
                self.assertEqual(len([e for e in self.events("team") if e["data"]["move"] == "halt"]), 1)

    def test_h3_lower_other_dim_keeps_quota_block(self):
        self.mk({"quota_pct": 30, "seats_max": 6}, usage={Q: 0})
        self.fp.usage_v = {Q: 30}
        self.tick(T0 + MIN)
        self.assertEqual((self.st()["status"], self.st()["budget_marks"].get("quota")), ("halted", ["halt"]))
        self.ok(M.budget_set, self.mid, by(PM), {"seats_max": 5}, env=self.env, now=T0 + 2 * MIN)
        self.assertEqual(self.st()["budget_marks"].get("quota"), ["halt"], "調低席數不清額度叫停")
        self.bad("over_budget", M.team, self.mid, "start", "human", env=self.env, now=T0 + 3 * MIN)
        self.ok(M.budget_set, self.mid, "human", {"quota_pct": 40}, env=self.env, now=T0 + 4 * MIN)
        self.fp.usage_v = {Q: 45}                                          # 改了上限、用量還在新上限以上
        self.bad("over_budget", M.team, self.mid, "start", "human", env=self.env, now=T0 + 5 * MIN)
        self.ok(M.budget_set, self.mid, "human", {"quota_pct": 60}, env=self.env, now=T0 + 6 * MIN)
        self.ok(M.team, self.mid, "start", "human", env=self.env, now=T0 + 7 * MIN)
        self.assertEqual(self.st()["status"], "running")

    def test_m1_watch_rc_anomaly_only(self):
        self.mk({"minutes": 600})
        self.tick(T0 + 11 * MIN)                                           # 工人 10 分沒動 ⇒ idle
        rc, w = self.watch(T0 + 12 * MIN)
        self.assertEqual(w["waiting"], [])
        self.assertTrue(w["anomalies"])
        self.assertEqual(rc, 3, "只有異常也要 3")

    def test_m2_strict_budget_input(self):
        for b in ({"quota_pct": -1}, {"minutes": -5}, {"minutes": 10 ** 400}, {"quota_pct": 101}, {"seats_max": 501},
                  {"minutes": "5"}, {"minutes": True}, {"bogus": 3}, {"minutes": 0}, ["minutes"]):
            with self.subTest(b=b):
                self.bad("bad_budget", M.new, by(PM), "x", "g", ["a"], budget=b, env=self.env, now=T0)
        self.mk({"minutes": 20})
        for ch in ({"minutes": 10 ** 6}, {"quota_pct": 101}, {"minutes": -1}, {"bogus": 1}):
            with self.subTest(ch=ch):
                self.bad("bad_budget", M.budget_set, self.mid, "human", ch, env=self.env, now=T0 + 1)
        for flag, val in (("--quota-pct", "-1"), ("--minutes", "0"), ("--seats-max", "0"), ("--quota-pct", "0")):
            with self.subTest(flag=flag, val=val):                      # r2 L3:CLI 的 0 不能靜默變不限
                out = io.StringIO()
                self.assertEqual(M.run(["new", "--title", "x", flag, val, "--by", by(PM), "--json"], env=self.env, out=out), 2)
                self.assertIn("bad_budget", out.getvalue())
        self.assertEqual(M.list_ids(self.env), [self.mid], "被拒的都沒開團")


class R2Codex(Base):
    """#427 r2(@f5fb8 Codex 1H/1M/1L + @aeb40 L3/L5)。"""

    def _legacy(self, budget):
        """照 main bde7796a 的寫法直接寫一筆建團事件(main 只認 minutes / seats_max 正整數,不設上限的上限)。"""
        mid = "m_" + os.urandom(6).hex()
        d = M.mdir(mid, self.env)
        d.mkdir(parents=True)
        ev = {"v": 1, "seq": 1, "eid": "e_legacy_0001", "req": "x", "at": T0, "by": by(PM), "type": "created",
              "data": {"title": "舊團", "goal": "", "accept": [], "pm": PM, "budget": budget}}
        (d / "events.jsonl").write_text(json.dumps(ev) + "\n", encoding="utf-8")
        return mid

    def test_high_main_events_still_replay(self):
        self.mid = self._legacy({"minutes": 100001, "seats_max": 501, "junk": -3})
        st = self.ok(M.replay, self.mid, self.env)
        self.assertEqual(st["budget"], {"minutes": 100001, "seats_max": 501}, "main 合法的舊值照讀,不當 corrupt")
        self.ok(M.budget_set, self.mid, by(PM), {"minutes": 90000}, env=self.env, now=T0 + 1)
        self.assertEqual(self.st()["budget"], {"minutes": 90000, "seats_max": 501}, "只驗這次改的欄位,舊值帶著走")
        self.bad("bad_budget", M.budget_set, self.mid, by(PM), {"seats_max": 600}, env=self.env, now=T0 + 2)

    def test_medium_unmeasured_still_halts_known_block(self):
        self.mk({"minutes": 20, "quota_pct": 30}, usage={Q: 0}, tasks=1)
        real, seen = M._append, []

        def crash_on_team(d, ev, torn):
            if ev.get("by") == "system" and ev["type"] == "team":
                raise OSError("斷電")
            seen.append(ev["type"])
            return real(d, ev, torn)
        with mock.patch.object(M, "_append", crash_on_team), self.assertRaises(OSError):
            M.tick(self.mid, self.env, now=T0 + 20 * MIN, probe=self.fp)
        self.assertEqual((self.st()["status"], self.st()["budget_block"]), ("running", {"minutes": True}))
        self.fp.usage_v = None                                             # 這時額度量不到
        self.bad("over_budget", self.dispatch, "t1", W1, T0 + 21 * MIN)
        self.assertEqual((self.st()["status"], self.st()["halt_reason"]), ("halted", "budget"), "先補叫停,不是只回量不到")
        self.mid = M.new(by(PM), "額度量不到", "g", ["a"], budget={"quota_pct": 30}, env=self.env, now=T0)["id"]
        self.c("task_add", by(PM), {"title": "x", "accept": ["rc 0"], "deps": []})
        self.c("team", by(PM), {"move": "submit"})
        self.bad("quota_unmeasured", M.team, self.mid, "start", "human", env=self.env, now=T0)

    def test_medium_dispatch_path_crash_recovers(self):
        self.mk({"minutes": 1}, tasks=1)
        real = M._append

        def crash_on_team(d, ev, torn):
            if ev.get("by") == "system" and ev["type"] == "team":
                raise OSError("斷電")
            return real(d, ev, torn)
        with mock.patch.object(M, "_append", crash_on_team), self.assertRaises(OSError):
            self.dispatch("t1", W1, T0 + MIN)                              # 派工預檢寫完 budget/halt 就斷
        self.assertEqual((self.st()["status"], self.st()["budget_block"]), ("running", {"minutes": True}))
        self.tick(T0 + 2 * MIN)
        self.assertEqual((self.st()["status"], len(self.budget_evs("halt"))), ("halted", 1))

    def test_medium_snapshot_crash_events_are_truth(self):
        self.mk({"minutes": 20}, tasks=1)
        with mock.patch.object(M, "_snapshots", side_effect=OSError("斷電")), self.assertRaises(OSError):
            M.tick(self.mid, self.env, now=T0 + 20 * MIN, probe=self.fp)
        self.assertEqual(self.st()["status"], "halted", "快照沒寫成也以事件檔為準")
        self.tick(T0 + 21 * MIN)
        self.assertEqual(len(self.budget_evs("halt")), 1)

    def test_low_notified_and_blocking_kept_apart(self):
        self.mk({"quota_pct": 30}, usage={Q: 0})
        self.fp.usage_v = {Q: 30}
        self.tick(T0 + MIN)
        st = self.st()
        self.assertEqual((st["status"], st["budget_marks"].get("quota"), st["budget_block"]), ("halted", ["halt"], {"quota": True}))
        self.ok(M.budget_set, self.mid, by(PM), {"quota_pct": 25}, env=self.env, now=T0 + 2 * MIN)   # 調低 = PM 可寫
        st = self.st()
        self.assertEqual((st["budget_marks"].get("quota"), st["budget_block"]), (None, {"quota": True}),
                         "調低:通知重計、阻擋留著")
        self.fp.usage_v = {Q: 10}                                          # 視窗重設、用量回到上限以下
        self.bad("over_budget", M.team, self.mid, "start", "human", env=self.env, now=T0 + 3 * MIN)
        self.ok(M.budget_set, self.mid, "human", {"quota_pct": 40}, env=self.env, now=T0 + 4 * MIN)
        self.assertEqual(self.st()["budget_block"], {}, "放寬那一維才解除阻擋")
        self.ok(M.team, self.mid, "start", "human", env=self.env, now=T0 + 5 * MIN)

    def test_l3_cli_zero_rejected(self):
        for flag in ("--minutes", "--seats-max", "--quota-pct"):
            with self.subTest(flag=flag):
                out = io.StringIO()
                self.assertEqual(M.run(["new", "--title", "x", flag, "0", "--by", by(PM), "--json"], env=self.env, out=out), 2)
                self.assertIn("bad_budget", out.getvalue())
        self.assertEqual(M.list_ids(self.env), [], "被拒的沒留空團目錄")

    def test_l5_threat_model_written(self):
        for mod in (M, MM):
            with self.subTest(mod=mod.__name__):
                self.assertIn("不防同帳號 AI", mod.__doc__)
                self.assertIn("budget_set by human proved", mod.__doc__)


class R3Codex(Base):
    """#427 r3(@f5fb8 Codex 1H + M1 補測):舊版完整事件序列照舊語意重放;每個寫檔邊界中斷都恢復。"""

    def _raw(self, rows):
        """照舊版(5cf99d94 以前)寫法逐筆寫事件:budget_set 沒有 reset 旗標。"""
        mid = "m_" + os.urandom(6).hex()
        d = M.mdir(mid, self.env)
        d.mkdir(parents=True)
        lines = []
        for n, (at, who, typ, data, *task) in enumerate(rows, 1):
            ev = {"v": 1, "seq": n, "eid": "e_legacy_%04d" % n, "req": "r%d" % n, "at": at, "by": who, "type": typ, "data": data}
            if task:
                ev["task"] = task[0]
            lines.append(json.dumps(ev))
        (d / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return mid

    def test_high_legacy_full_sequence_replays(self):
        """舊版合法:額度 30 叫停 → PM 調低到 25 → 用量回落 → 本人開始 ⇒ 舊版重放 running,新版也要 running。"""
        self.mid = self._raw([
            (T0, by(PM), "created", {"title": "舊團", "goal": "", "accept": [], "pm": PM, "budget": {"quota_pct": 30}}),
            (T0, by(PM), "roster", {"op": "add", "seat": W1, "role": "worker"}),
            (T0, by(PM), "task_add", {"title": "t", "accept": ["rc 0"], "deps": []}, "t1"),
            (T0, by(PM), "team", {"move": "submit"}),
            (T0, "human", "team", {"move": "start", "proved": True}),
            (T0 + 60, "system", "budget", {"level": "base", "dim": "quota", "base": {Q: 0}}),
            (T0 + 120, "system", "budget", {"level": "halt", "dim": "quota", "used": 30, "cap": 30}),
            (T0 + 120, "system", "team", {"move": "halt", "reason": "budget", "dim": "quota"}),
            (T0 + 180, by(PM), "budget_set", {"budget": {"quota_pct": 25}}),
            (T0 + 240, "human", "team", {"move": "start", "proved": True}),
            (T0 + 300, "system", "budget", {"level": "halt", "dim": "quota", "used": 26, "cap": 25}),   # 舊版:記號已清、可再寫
            (T0 + 300, "system", "team", {"move": "halt", "reason": "budget", "dim": "quota"}),
            (T0 + 360, by(PM), "budget_set", {"budget": {"quota_pct": 20}}),
        ])
        st = self.ok(M.replay, self.mid, self.env)
        self.assertEqual((st["status"], st["budget"], st["budget_marks"], st["budget_block"]),
                         ("halted", {"quota_pct": 20}, {}, {}), "舊序列照舊語意:改預算清掉記號與阻擋")
        self.assertEqual(st["seq"], 13)
        self.ok(M.budget_set, self.mid, by(PM), {"quota_pct": 15}, env=self.env, now=T0 + 400)   # 之後的新事件照新語意
        self.assertEqual(self.st()["budget"], {"quota_pct": 15})
        self.assertEqual(self.events("budget_set")[-1]["data"].get("reset"), "dims")

    def test_high_unknown_reset_flag_unreadable(self):
        self.mid = self._raw([
            (T0, by(PM), "created", {"title": "x", "goal": "", "accept": [], "pm": PM, "budget": {"minutes": 30}}),
            (T0 + 1, by(PM), "budget_set", {"budget": {"minutes": 20}, "reset": "all"}),
        ])
        self.bad("corrupt", M.replay, self.mid, self.env)

    def test_m1_every_write_boundary_recovers(self):
        """tick 一輪寫 budget/base → budget/halt → team/halt → 快照;在每個邊界中斷,下一輪都補齊、不重寫。"""
        for crash in ("base", "budget", "team", "snapshot"):
            with self.subTest(crash=crash):
                self.mk({"quota_pct": 30}, usage={Q: 0})
                self.fp.usage_v = {Q: 30, "claude/7d/shared": 5}               # 新的 (引擎, 視窗) ⇒ 先寫基準
                real_append, real_snap = M._append, M._snapshots

                def flaky_append(d, ev, torn):
                    if ev.get("by") == "system" and (ev["type"] == crash if crash == "team"
                                                     else ev["type"] == "budget" and ev["data"]["level"] ==
                                                     ("base" if crash == "base" else "halt")):
                        raise OSError("斷電")
                    return real_append(d, ev, torn)

                def flaky_snap(d, st):
                    if crash == "snapshot":
                        raise OSError("斷電")
                    return real_snap(d, st)
                with mock.patch.object(M, "_append", flaky_append), mock.patch.object(M, "_snapshots", flaky_snap), \
                        self.assertRaises(OSError):
                    M.tick(self.mid, self.env, now=T0 + MIN, probe=self.fp)
                self.tick(T0 + 2 * MIN)
                st = self.st()
                self.assertEqual((st["status"], st["halt_reason"]), ("halted", "budget"), crash)
                self.assertEqual(st["quota_base"], {Q: 0, "claude/7d/shared": 5}, crash)
                self.assertEqual(len(self.budget_evs("halt")), 1, crash)
                self.assertEqual(len([e for e in self.events("team") if e["data"]["move"] == "halt"]), 1, crash)
                self.assertEqual(len(self.budget_evs("base")), 2, "開始那筆 + 新視窗那筆,不重寫")


MON_PY, MIS_PY = "src/cockpit/mission_monitor.py", "src/cockpit/mission.py"
KNIVES = [
    ("K1", MON_PY, '''            if lv is None or "halt" in marks or lv in marks:''', '''            if lv is None:''',
     "B1to5Budget.test_b2_warn_once"),
    ("K2", MIS_PY, '''        if st["status"] == "running" and to != "running":     # 預算分鐘:只算 running 的時間''',
     '''        if False:''', "B1to5Budget.test_b1_minutes_only_running"),
    ("K3", MIS_PY, '''    system = typ in SYSTEM_TYPES or''', '''    system = typ in ("expire",) or''',
     "B6to10Quota.test_b10_forged_budget_unreadable"),
    ("K4", MON_PY, '''        elif st["throttle"] and load <= THROTTLE_OFF:''', '''        elif st["throttle"] and load <= THROTTLE_ON:''',
     "M1to4Rules.test_m4_throttle_hysteresis"),
    ("K5", MON_PY, '''    if status in (None, "archived"):''', '''    if status is None:''',
     "B1to5Budget.test_b4b_archived_tick_writes_nothing"),
    ("K6", MIS_PY, '''        if new == st["budget"]:''', '''        if False:''', "B1to5Budget.test_b4_restart_needs_budget_raise"),
    ("K7", MIS_PY, '''        if typ == "dispatch" or _to_running(st, typ, data):''', '''        if False:''',
     "R1Codex.test_h1_dispatch_over_budget_without_tick"),
    ("K8", MON_PY, '''        if status == "running" and not halting and stale:''', '''        if False:''',
     "R1Codex.test_h2_halt_mark_without_team_halt_recovers"),
    ("K9", MIS_PY, '''            if old.get(key) != new.get(key):''', '''            if True:''',
     "R1Codex.test_h3_lower_other_dim_keeps_quota_block"),
    ("K10", MON_PY, '''    return 3 if w["waiting"] or w["anomalies"] or w["stuck"] else 0''', '''    return 3 if w["waiting"] else 0''',
     "R1Codex.test_m1_watch_rc_anomaly_only"),
    ("K12", MIS_PY, '''("seats_max", a.seats_max), ("quota_pct", a.quota_pct)) if v is not None}''',
     '''("seats_max", a.seats_max), ("quota_pct", a.quota_pct)) if v}''',
     "R1Codex.test_m2_strict_budget_input"),
    ("K13", MIS_PY, '''                  budget=_budget(d.get("budget")))''', '''                  budget=_budget_input(d.get("budget")))''',
     "R2Codex.test_high_main_events_still_replay"),
    ("K14", MON_PY, '''            unmeasured = True                       # 先處理已知的超限''',
     '''            return out, "quota_unmeasured"  # 先處理已知的超限''', "R2Codex.test_medium_unmeasured_still_halts_known_block"),
    ("K15", MIS_PY, '''                if key in old and (key not in new or new[key] > old[key]):''', '''                if True:''',
     "R2Codex.test_low_notified_and_blocking_kept_apart"),
    ("K16", MON_PY, '''        stale = sorted(dim for dim, on in st["budget_block"].items() if on)''', '''        stale = []''',
     "R2Codex.test_medium_dispatch_path_crash_recovers"),
    ("K17", MIS_PY, '''        if reset is None:                           # 舊事件''', '''        if False:                           # 舊事件''',
     "R3Codex.test_high_legacy_full_sequence_replays"),
    ("K11", MIS_PY, '''        if type(v) is not int or not (0 if allow_zero else 1) <= v <= BUDGET_MAX[k]:''', '''        if type(v) is not int:''',
     "R1Codex.test_m2_strict_budget_input"),
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
