#!/usr/bin/env python3
"""思維桌核心 aib meeting(10-29,SPEC-meeting-table-f5fb8-1029 做法 1–2 + 02:5x;@f5fb8 13:45 定門檻)。

A1 開會:主持一定在出席名單且只有一位;議題必填;保險絲超出範圍拒;目錄 0700、檔 0600
A2 已定門檻 = ≥1 附議 + ≥1 證據 + 沒有未解反對:只有附議沒證據 ⇒ open;補證據卡 ⇒ settled(K1、K2)
A3 自己附議 / 反對自己的主張拒;同一席對同一張只表態一次;品管只能出 question / evidence(K3)
A4 反對 ⇒ 開分岔 f1、狀態 forked;第二個反對併進同一個分岔
A5 分岔:主持挑 2–3 席(品管不能入選);三方都寫了主持才能定預設;預設要附證據(K4);view 帶這個分岔的反對卡字
A6 30 秒:倒數沒滿 tick 不動;滿了 tick 由 system 記「預設採用」;裁成主張 ⇒ 反對作廢可已定;裁成反對 ⇒ rejected(K5)
A7 離開模式:預設一出就算數(不等 30 秒);本人 pick 隨時改選
A8 本人:沒 ✋ 介入不能出卡;介入後可以;便條只有本人、記來源 human;暫停時不能出卡
A9 保險絲:每席 claim 數到 rounds 拒;字數超過拒(不截);時間到 tick 自動散會、散會後只剩 link
A10 散會一頁:結論只收已定;分岔 / 沒定的列未決;handoff 只帶已定且還沒對到任務的卡;倒數中的分岔散會時照預設合回
A11 link 只有已定的卡;task_ids 合併排序
A12 eid:同 eid 同內容回原結果、不同內容 eid_conflict;重放:改過的事件(假的 system 預設採用)讀不起來;斷尾當沒寫
A13 next:輪到還沒出卡的席;列出等挑人 / 等三方 / 等預設的分岔
A14 CLI --json 走得通;四語鍵一致、沒有 !!
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

import meeting as G  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

HOST, A, B, QA = "aaaa1", "bbbb2", "cccc3", "dddd4"
T0 = 1_900_000_000


def by(s):
    return "seat:" + s


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-meeting-")
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.root), "EZ2_AIB_HOME": str(self.root), "HOME": str(self.root),
                    "EZ2_AIB_LOCALE": "en", "PATH": os.environ.get("PATH", "")}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)

    def mk(self, fuses=None, now=T0):
        attend = {HOST: {"role": "host", "brand": "claude"}, A: {"role": "member", "brand": "codex"},
                  B: {"role": "member", "brand": "grok"}, QA: {"role": "qa", "brand": "codex"}}
        self.gid = G.new(by(HOST), "Login page", attend, HOST, fuses=fuses, env=self.env, now=now)["id"]
        return self.gid

    def c(self, typ, who, data, card=None, eid=None, now=T0 + 1):
        return G.commit(self.gid, typ, who, data, env=self.env, card=card, eid=eid, now=now)

    def card(self, who, kind, text="x", target=None, evidence=None, now=T0 + 1, eid=None):
        d = {"kind": kind, "text": text, "evidence": evidence or []}
        if target:
            d["target"] = target
        return self.c("card", who, d, now=now, eid=eid)["card"]

    def st(self):
        return G.replay(self.gid, self.env)

    def state(self, cid):
        st = self.st()
        return G.claim_state(st, st["cards"][cid])

    def bad(self, want, fn, *a, **k):
        with self.assertRaises(G.MeetingError) as cm:
            fn(*a, **k)
        got = str(cm.exception)
        self.assertEqual(got.split(":")[0] if not want.startswith("corrupt") else got[:len(want)], want)

    def ok(self, fn, *a, **k):
        try:
            return fn(*a, **k)
        except G.MeetingError as err:
            self.fail("不該拒:%s" % err)

    def forked(self):
        """c1 主持主張(帶證據)、A 附議、B 反對 ⇒ f1。"""
        self.mk()
        c1 = self.card(by(HOST), "claim", "passkey first", evidence=["pending/x.md"])
        self.card(by(A), "second", target=c1)
        self.card(by(B), "object", "old users stuck", target=c1)
        return c1

    def to_default(self, choice="claim", now=T0 + 10):
        self.c("fork", by(HOST), {"op": "panel", "fork": "f1", "seats": [A, B]})
        self.c("fork", by(A), {"op": "view", "fork": "f1", "text": "keep password fallback"})
        self.c("fork", by(B), {"op": "view", "fork": "f1", "text": "ok with fallback"})
        self.c("fork", by(HOST), {"op": "default", "fork": "f1", "choice": choice, "why": "fallback",
                                  "evidence": ["pending/y.md"]}, now=now)


class Create(Base):
    def test_a1_create(self):
        attend = {HOST: {"role": "host"}, A: {"role": "member"}}
        self.bad("bad_host", G.new, by(HOST), "t", attend, A, env=self.env)
        self.bad("bad_host", G.new, by(HOST), "t", {HOST: {"role": "host"}, A: {"role": "host"}}, HOST, env=self.env)
        self.bad("no_topic", G.new, by(HOST), "  ", attend, HOST, env=self.env)
        self.bad("bad_fuse", G.new, by(HOST), "t", attend, HOST, fuses={"rounds": 99}, env=self.env)
        self.bad("bad_attend", G.new, by(HOST), "t", {"XYZ": {"role": "host"}}, "XYZ", env=self.env)
        gid = self.ok(G.new, by(HOST), "t", attend, HOST, env=self.env)["id"]
        d = G.root(self.env) / gid
        self.assertEqual(oct(d.stat().st_mode & 0o777), "0o700")
        for f in ("events.jsonl", "meeting.json", "cards.jsonl", "summary.json"):
            self.assertEqual(oct((d / f).stat().st_mode & 0o777), "0o600", f)
        self.assertEqual(G.replay(gid, self.env)["fuses"], {"rounds": 3, "minutes": 30, "chars": 400})


class Settle(Base):
    def test_a2_threshold(self):
        self.mk()
        c1 = self.card(by(HOST), "claim", "p")
        self.assertEqual(self.state(c1), "untouched")
        self.card(by(A), "second", target=c1)
        self.assertEqual(self.state(c1), "open")                     # 附議了但沒證據 ⇒ 不算已定
        self.card(by(QA), "evidence", "ci run", target=c1, evidence=["https://ci/1"])
        self.assertEqual(self.state(c1), "settled")
        c5 = self.card(by(A), "claim", "q", evidence=["pending/z.md"])
        self.assertEqual(self.state(c5), "untouched")                # 有證據沒附議 ⇒ 不算
        self.card(by(B), "second", target=c5)
        self.assertEqual(self.state(c5), "settled")
        self.assertEqual(G.view(self.st(), T0 + 2)["consensus_pct"], 100)

    def test_a3_who_can_react(self):
        self.mk()
        c1 = self.card(by(HOST), "claim", "p", evidence=["r"])
        self.bad("own_claim", self.card, by(HOST), "second", target=c1)
        self.bad("own_claim", self.card, by(HOST), "object", "no", target=c1)
        self.card(by(A), "second", target=c1)
        self.bad("already_reacted", self.card, by(A), "object", "no", target=c1)
        self.bad("qa_side_only", self.card, by(QA), "second", target=c1)
        self.bad("qa_side_only", self.card, by(QA), "claim", "mine")
        self.ok(self.card, by(QA), "question", "mobile?", target=c1)
        self.bad("bad_evidence", self.card, by(QA), "evidence", "none", target=c1)
        self.bad("bad_target", self.card, by(A), "second", target="c99")
        self.bad("not_allowed", self.card, "seat:fffff", "claim", "outsider")


class Fork(Base):
    def test_a4_object_opens_fork(self):
        c1 = self.forked()
        self.assertEqual(self.state(c1), "forked")
        st = self.st()
        self.assertEqual((list(st["forks"]), st["forks"]["f1"]["objections"]), (["f1"], ["c3"]))
        self.card(by(QA), "question", "why?", target=c1)
        self.mk_extra = self.c("card", by(B), {"kind": "claim", "text": "other"})
        st = self.st()
        self.assertEqual(len(st["forks"]), 1)

    def test_a5_fork_rules(self):
        self.forked()
        self.bad("not_allowed", self.c, "fork", by(A), {"op": "panel", "fork": "f1", "seats": [A, B]})
        self.bad("bad_panel", self.c, "fork", by(HOST), {"op": "panel", "fork": "f1", "seats": [A, QA]})
        self.bad("bad_panel", self.c, "fork", by(HOST), {"op": "panel", "fork": "f1", "seats": [A]})
        self.bad("bad_panel", self.c, "fork", by(HOST), {"op": "panel", "fork": "f1", "seats": [A, B, HOST, "eeee5"]})
        self.ok(self.c, "fork", by(HOST), {"op": "panel", "fork": "f1", "seats": [A, B]})
        self.bad("not_allowed", self.c, "fork", by(HOST), {"op": "view", "fork": "f1", "text": "me"})
        self.ok(self.c, "fork", by(A), {"op": "view", "fork": "f1", "text": "fallback"})
        dflt = {"op": "default", "fork": "f1", "choice": "claim", "why": "w", "evidence": ["r"]}
        self.bad("views_missing", self.c, "fork", by(HOST), dflt)
        self.ok(self.c, "fork", by(B), {"op": "view", "fork": "f1", "text": "agree"})
        self.bad("bad_evidence", self.c, "fork", by(HOST), dict(dflt, evidence=[]))
        self.bad("not_allowed", self.c, "fork", by(A), dflt)
        self.ok(self.c, "fork", by(HOST), dflt)
        v = G.view(self.st(), T0 + 5)["forks"][0]
        self.assertEqual((v["decide_in"], v["same_brand"]), (26, False))
        self.assertEqual(v["object_cards"], [{"id": "c3", "by": by(B), "text": "old users stuck", "why": ""}])

    def test_a6_countdown(self):
        c1 = self.forked()
        self.to_default("claim", now=T0 + 10)
        self.assertEqual(G.tick(self.gid, self.env, now=T0 + 39)["wrote"], 0)
        self.assertEqual(self.state(c1), "forked")
        self.assertEqual(G.tick(self.gid, self.env, now=T0 + 40)["wrote"], 1)
        st = self.st()
        self.assertEqual((st["forks"]["f1"]["status"], st["forks"]["f1"]["adopted"]), ("resolved", "default"))
        self.assertEqual(G.claim_state(st, st["cards"][c1]), "settled")   # 反對被裁掉 ⇒ 附議 + 證據就已定
        ev = [json.loads(x) for x in (G.gdir(self.gid, self.env) / "events.jsonl").read_text().splitlines()][-1]
        self.assertEqual((ev["by"], ev["type"]), ("system", "fork_expire"))
        # 裁成反對 ⇒ rejected
        c2 = self.forked()
        self.to_default("object", now=T0 + 10)
        G.tick(self.gid, self.env, now=T0 + 41)
        self.assertEqual(self.state(c2), "rejected")
        self.bad("bad_target", self.card, by(B), "second", target=c2)


class Human(Base):
    def test_a7_away_and_pick(self):
        c1 = self.forked()
        self.bad("not_allowed", self.c, "table", by(HOST), {"move": "away", "on": True})
        self.ok(self.c, "table", "human", {"move": "away", "on": True})
        self.to_default("object", now=T0 + 10)
        st = self.st()
        self.assertEqual((st["forks"]["f1"]["status"], st["forks"]["f1"]["adopted"]), ("resolved", "default_away"))
        self.assertEqual(G.claim_state(st, st["cards"][c1]), "rejected")
        c2 = self.forked()
        self.bad("not_allowed", self.c, "fork", by(HOST), {"op": "pick", "fork": "f1", "choice": "claim"})
        self.ok(self.c, "fork", "human", {"op": "pick", "fork": "f1", "choice": "claim"})
        st = self.st()
        self.assertEqual((st["forks"]["f1"]["adopted"], G.claim_state(st, st["cards"][c2])), ("human", "settled"))
        self.bad("fork_resolved", self.c, "fork", "human", {"op": "pick", "fork": "f1", "choice": "object"})

    def test_a8_human_actions(self):
        self.mk()
        self.bad("not_allowed", self.card, "human", "claim", "mine")
        self.ok(self.c, "table", "human", {"move": "intervene", "on": True})
        c1 = self.ok(self.card, "human", "claim", "mine", evidence=["shot.png"])
        self.bad("own_claim", self.card, "human", "second", target=c1)
        self.ok(self.card, by(A), "second", target=c1)
        self.assertEqual(self.state(c1), "settled")
        self.bad("not_allowed", self.c, "note", by(A), {"text": "fake human"})
        self.ok(self.c, "note", "human", {"text": "look at this", "ref": ["shot.png"]})
        self.assertEqual(self.st()["notes"][0]["source"], "human")
        self.ok(self.c, "table", "human", {"move": "pause"})
        self.bad("paused", self.card, by(A), "claim", "during pause")
        self.ok(self.c, "note", "human", {"text": "still ok"})
        self.bad("not_allowed", self.c, "table", by(A), {"move": "resume"})
        self.ok(self.c, "table", by(HOST), {"move": "resume"})
        self.ok(self.card, by(A), "claim", "after")


class Fuse(Base):
    def test_a9_fuses(self):
        self.mk(fuses={"rounds": 2, "minutes": 5, "chars": 40})
        self.card(by(A), "claim", "one")
        self.card(by(A), "claim", "two")
        self.bad("rounds_used", self.card, by(A), "claim", "three")
        self.bad("too_long", self.card, by(B), "claim", "y" * 41)
        self.assertEqual(G.tick(self.gid, self.env, now=T0 + 299)["wrote"], 0)
        self.assertEqual(G.tick(self.gid, self.env, now=T0 + 300)["wrote"], 1)
        st = self.st()
        self.assertEqual((st["status"], st["closed_by"]), ("closed", "timeout"))
        self.bad("closed", self.card, by(B), "claim", "late", now=T0 + 301)
        self.bad("closed", self.c, "note", "human", {"text": "late"}, now=T0 + 301)


class Summary(Base):
    def test_a10_summary(self):
        c1 = self.forked()
        c4 = self.card(by(A), "claim", "add audit log", evidence=["pending/a.md"])
        self.card(by(B), "second", target=c4)
        c6 = self.card(by(B), "claim", "nobody cares")
        self.to_default("claim", now=T0 + 10)
        self.c("table", by(HOST), {"move": "close"}, now=T0 + 12)       # 還在倒數 ⇒ 照預設合回
        s = G.summary(self.st(), T0 + 13)
        self.assertEqual(sorted(x["card"] for x in s["conclusions"]), sorted([c1, c4]))
        self.assertEqual([x["card"] for x in s["divergence"]["undecided"]], [c6])
        self.assertEqual(s["divergence"]["resolved"][0]["adopted"], "default_on_close")
        self.assertEqual(sorted(t["card"] for t in s["handoff"]["tasks"]), sorted([c1, c4]))
        self.assertEqual(s["needs_you"], [])
        disk = json.loads((G.gdir(self.gid, self.env) / "summary.json").read_text())
        self.assertEqual(sorted(x["card"] for x in disk["conclusions"]), sorted([c1, c4]))

    def test_a11_link(self):
        self.mk()
        c1 = self.card(by(HOST), "claim", "p", evidence=["r"])
        c2 = self.card(by(A), "claim", "q")
        self.bad("not_settled", self.c, "link", by(HOST), {"task_ids": ["t1"]}, card=c1)
        self.card(by(A), "second", target=c1)
        self.bad("not_allowed", self.c, "link", by(A), {"task_ids": ["t1"]}, card=c1)
        self.bad("bad_task", self.c, "link", by(HOST), {"task_ids": ["x1"]}, card=c1)
        self.ok(self.c, "link", by(HOST), {"task_ids": ["t3", "t1"]}, card=c1)
        self.c("table", by(HOST), {"move": "close"})
        self.ok(self.c, "link", "human", {"task_ids": ["t10", "t1"]}, card=c1)   # 散會後還能寫回
        self.assertEqual(self.st()["cards"][c1]["task_ids"], ["t1", "t3", "t10"])
        self.bad("not_settled", self.c, "link", by(HOST), {"task_ids": ["t2"]}, card=c2)
        self.assertIsNone(G.summary(self.st())["handoff"]["tasks"] or None)    # 對到任務的不再出現在草稿


class Replay(Base):
    def test_a12_eid_and_replay(self):
        self.mk()
        r1 = self.c("card", by(A), {"kind": "claim", "text": "p"}, eid="eid-aaaa-1")
        r2 = self.c("card", by(A), {"kind": "claim", "text": "p"}, eid="eid-aaaa-1")
        self.assertEqual((r2["duplicate"], r2["card"]), (True, r1["card"]))
        self.bad("eid_conflict", self.c, "card", by(A), {"kind": "claim", "text": "other"}, eid="eid-aaaa-1")
        p = G.gdir(self.gid, self.env) / "events.jsonl"
        good = p.read_bytes()
        p.write_bytes(good + b'{"v": 1, "seq": 3, "eid": "x_00000000')          # 斷尾 ⇒ 當沒寫
        self.assertEqual(self.st()["seq"], 2)
        self.ok(self.card, by(B), "claim", "after torn")
        self.assertEqual(self.st()["seq"], 3)
        fake = {"v": 1, "seq": 4, "eid": "x_1111111111111111", "req": None, "at": T0 + 2, "by": "system",
                "type": "fork_expire", "data": {"fork": "f1"}}
        p.write_bytes(p.read_bytes() + (json.dumps(fake) + "\n").encode())
        self.bad("corrupt:4", self.st)
        # 只錯在「誰寫的」:時間到了的散會,卻是一般席寫的 system 事件 ⇒ 讀不起來
        fake2 = dict(fake, seq=3, by="seat:" + A, type="timeout", data={}, at=T0 + 1800)
        p.write_bytes(good + (json.dumps(fake2) + "\n").encode())
        self.bad("corrupt:3", self.st)
        p.write_bytes(good + (json.dumps(dict(fake2, by="system")) + "\n").encode())
        self.assertEqual(self.st()["status"], "closed")                     # 同一筆換成 system 就合法(刀要紅在上面)


class Next(Base):
    def test_a13_next(self):
        self.forked()
        n = G.next_up(self.st(), T0 + 2)
        self.assertEqual((n["turn"], n["need_panel"]), ([A, B], ["f1"]))
        self.c("fork", by(HOST), {"op": "panel", "fork": "f1", "seats": [A, B]})
        self.c("fork", by(A), {"op": "view", "fork": "f1", "text": "v"})
        n = G.next_up(self.st(), T0 + 2)
        self.assertEqual(n["need_views"], [{"fork": "f1", "seats": [B]}])
        self.c("fork", by(B), {"op": "view", "fork": "f1", "text": "v"})
        self.assertEqual(G.next_up(self.st(), T0 + 2)["need_default"], ["f1"])


class Cli(Base):
    def run_cli(self, *argv):
        r = subprocess.run([sys.executable, "-B", str(ROOT / "src" / "cockpit" / "meeting.py")] + list(argv),
                           capture_output=True, text=True, env=dict(os.environ, **self.env), timeout=60,
                           stdin=subprocess.DEVNULL)
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])

    def test_a14_cli_and_dict(self):
        rc, out = self.run_cli("new", "--json", "--by", by(HOST), "--topic", "t", "--host", HOST,
                               "--attend", A + "::codex", "--attend", QA + ":qa")
        self.assertEqual(rc, 0, out)
        gid = out["id"]
        rc, out = self.run_cli("card", gid, "--json", "--by", by(HOST), "--kind", "claim", "--text", "p",
                               "--evidence", "r")
        self.assertEqual((rc, out["card"], out["claim_state"]), (0, "c1", "untouched"))
        rc, out = self.run_cli("card", gid, "--json", "--by", by(A), "--kind", "second", "--target", "c1")
        self.assertEqual((rc, out["claim_state"]), (0, "settled"))
        rc, out = self.run_cli("card", gid, "--json", "--by", by(QA), "--kind", "second", "--target", "c1")
        self.assertEqual((rc, out["error"]), (2, "qa_side_only"))
        rc, out = self.run_cli("table", gid, "close", "--json", "--by", "human")
        self.assertEqual((rc, out["status"]), (0, "closed"))
        rc, out = self.run_cli("summary", gid, "--json")
        self.assertEqual((rc, [c["card"] for c in out["conclusions"]]), (0, ["c1"]))
        keys = []
        for lang in ("en", "ja", "ko", "zh-TW"):
            d = json.loads((ROOT / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            ks = sorted(k for k in d if k.startswith("aib.meeting."))
            self.assertTrue(ks and all(d[k] and "!!" not in d[k] for k in ks), lang)
            keys.append(ks)
        self.assertTrue(all(k == keys[0] for k in keys))


KNIVES = [
    ("K1 不看證據", 'if seconds and ev:', 'if seconds:', "Settle.test_a2_threshold"),
    ("K2 不看反對", '    if any(k["kind"] == "object" and k["id"] not in overruled for k in kids):\n        return "forked"\n',
     '', "Fork.test_a4_object_opens_fork"),
    ("K3 品管能附議", 'if role == "qa" and kind not in QA_KINDS:', 'if False:', "Settle.test_a3_who_can_react"),
    ("K4 不等三方", 'if not f["panel"] or set(f["views"]) != set(f["panel"]):', 'if not f["panel"]:',
     "Fork.test_a5_fork_rules"),
    ("K5 倒數不寫", 'evs.append(("fork_expire", {"fork": f["id"]}))', 'pass', "Fork.test_a6_countdown"),
    ("K6 離開模式也等", '                _resolve(st, f, choice, "default_away", at)   # 離開模式不等 30 秒',
     '                pass', "Human.test_a7_away_and_pick"),
    ("K7 本人不介入也能出卡", 'if role is None or (role == "human" and not st["intervene"]):', 'if role is None:',
     "Human.test_a8_human_actions"),
    ("K8 沒有輪數上限", 'if mine >= st["fuses"]["rounds"]:', 'if False:', "Fuse.test_a9_fuses"),
    ("K9 散會不合回倒數", '            _resolve(st, f, f["default"]["choice"], "default_on_close", at)', '            pass',
     "Summary.test_a10_summary"),
    ("K10 沒定也能 link", 'if claim_state(st, c) != "settled":\n            raise MeetingError("not_settled")',
     'if False:\n            raise MeetingError("not_settled")', "Summary.test_a11_link"),
    ("K11 system 不驗", '    if (by == "system") != system or', '    if False or', "Replay.test_a12_eid_and_replay"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, "src/cockpit/meeting.py", needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=120)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
