#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""dm 整理叫醒:立刻叫 / 不跳過 / 子代理接手與下一批 / 全艦隊 / 常駐 / 看板
(Andy 10-25 22:1x「如何確保所有 AI 都會觸發」→ 22:3x「更即時、不要跳過的,只要待審與進行中的數量一到,就應該立刻叫
子 ai 立刻消化,消化完就可以讓他關閉、或處理下一個」;@abb89 派工 A / B / D / E,@044e0)。
治具沿用 test-dm-hold-notify-abb89-1015.py(整棵 src 抄進暫存根、假 tmux、假咽喉),投遞由注入的 deliver 收下,
不碰工作樹、不碰 ~/.ez2-aib、不送真 dm。

F1 沒人接就再叫:叫醒後 ACK_S 內等接手;沒接 ⇒ 再叫(開頭「再叫一次」),最多 REWAKE_MAX 次後 triage_stalled、不再叫;
   有新信 ⇒ 立刻當新的一次叫、清 stalled
F2 子代理在做就不叫、做完立刻接下一批:登記有效中來新信不叫;放鎖後只剩它看過的 ⇒ 不叫;
   再來一封新的 ⇒ 立刻叫(不等 10 分鐘冷卻);只有進行中的也一樣
F3 子代理當掉沒放鎖:登記過期 ⇒ 下一拍照叫
F4 全艦隊(D):claude / codex / API(digest)三種席各 2 封待審 ⇒ 2 拍內三席都收到叫醒句;API 席是 digest 模式
F5 常駐(B):待審框那行在進行中 ≥2 時帶整理規則,1 封時不帶;stalled 時前面加「⚠ 未整理」(brief_payload.triage_stalled)
F6 看板(E):triage-status 列出叫醒時 / 現在封數、再叫幾次、stalled 排最前、誰登記著
刀(複本 + run_guarded):K1 不再叫 ⇒ F1 紅;K5 永不標 stalled ⇒ F1 紅;K2 不看登記 ⇒ F2 紅;
K2b 不看子代理看過的 ⇒ F2 紅;K3 API 席也當 agent ⇒ F4 紅;K4 常駐那句不出 ⇒ F5 紅。
刀一律斷言「FAIL: <目標>」且沒有 ERROR。
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

_spec = importlib.util.spec_from_file_location("dmhold_fixture", HERE / "test-dm-hold-notify-abb89-1015.py")
HN = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(HN)
SEAT = HN.SEAT


def setUpModule():
    HN.setUpModule()


def tearDownModule():
    HN.tearDownModule()


SETUP = (
    "def put(msg, now, seat=%r, provider='claude'):\n"
    "    return Q.record(seat, msg, '@77777', status='delivered', rc=0, env=env, now=now,\n"
    "                    sender_kind='ai', wid='@2', provider=provider, hub='aihub-t1')['id']\n"
    "sent = []\n"
    "def deliver(item):\n"
    "    if str(item.get('id', '')).startswith('triage_'):\n"
    "        sent.append({'seat': item.get('seat'), 'msg': item['msg']})\n"
    "    return 0\n"
    "def tick(now):\n"
    "    return Q.triage_tick(env=env, now=now, hold_fn=lambda s, r: (False, 'idle'), deliver_fn=deliver, force=True)\n"
    "def tri(seat=%r):\n"
    "    return Q.TR.load(Q.triage_path(seat, env)) or {}\n"
    "t = int(__import__('time').time())\n"
    "A = Q.TR.ACK_S\n"
    "import dm_triage_agent as TA\n"
) % (SEAT, SEAT)


class F1NoPickupRewake(unittest.TestCase):
    def test_rewake_then_stall_then_new_mail(self):
        out = HN.QBox(self).py(SETUP + (
            "put('第一件:請看 PR 頁面上的說明', t); put('第二件:另一個題目要你決定', t)\n"
            "tick(t + 10); n1 = len(sent)\n"
            "tick(t + 70); n2 = len(sent)\n"
            "tick(t + 10 + A); n3 = len(sent); m3 = sent[-1]['msg']\n"
            "tick(t + 10 + 2 * A); n4 = len(sent)\n"
            "tick(t + 10 + 3 * A); n5 = len(sent); st = tri()\n"
            "tick(t + 10 + 4 * A); n6 = len(sent)\n"
            "put('第三件:新的', t + 10 + 4 * A + 5); tick(t + 10 + 4 * A + 10); n7 = len(sent); st2 = tri()\n"
            "print(json.dumps({'n': [n1, n2, n3, n4, n5, n6, n7], 'second': m3,"
            " 'stalled': [st.get('triage_stalled'), st2.get('triage_stalled')], 'followups': [st.get('followups'), st2.get('followups')]}))\n"))
        self.assertEqual(out["n"], [1, 1, 2, 3, 3, 3, 4], out)
        self.assertTrue(out["second"].startswith("【再叫一次"), out["second"][:80])
        self.assertEqual(out["stalled"], [True, False], out)
        self.assertEqual(out["followups"], [2, 0], out)


class F2AgentThenNextBatch(unittest.TestCase):
    def test_lock_holds_then_next_batch_immediately(self):
        out = HN.QBox(self).py(SETUP + (
            "put('第一件:請看 PR 頁面上的說明', t); put('第二件:另一個題目要你決定', t)\n"
            "tick(t + 10)\n"
            "TA.agent_lock(%r, 'agent:%s/tri-1', env=env, now=t + 20)\n"
            "put('第三件', t + 30); tick(t + 40); a = len(sent)\n"
            "r = TA.agent_lock(%r, 'agent:%s/tri-1', env=env, now=t + 50, release=True)\n"
            "tick(t + 60); b = len(sent)\n"
            "put('第四件', t + 70); tick(t + 80); c = len(sent)\n"
            "print(json.dumps({'a': a, 'b': b, 'c': c, 'left': r.get('left')}))\n") % (SEAT, SEAT, SEAT, SEAT))
        self.assertEqual(out["a"], 1, "子代理登記中 ⇒ 新信也不另叫(它做完會接著看):%r" % out)
        self.assertEqual(len(out["left"]), 3, out)
        self.assertEqual(out["b"], 1, "放鎖後只剩它看過、留給本席的 ⇒ 不叫:%r" % out)
        self.assertEqual(out["c"], 2, "再來新信 ⇒ 立刻叫(70 秒前才叫過也叫):%r" % out)

    def test_inprog_only_next_batch(self):
        out = HN.QBox(self).py(SETUP + (
            "def rd(m, w):\n"
            "    i = put(m, w); Q.mark_read(%r, i, 'seat:%s', env=env, now=w, no_rev_check=True)\n"
            "rd('進行中一:審查還沒回來', t); rd('進行中二:等 CI', t)\n"
            "tick(t + 10); TA.agent_lock(%r, 'agent:%s/tri-1', env=env, now=t + 20)\n"
            "TA.agent_lock(%r, 'agent:%s/tri-1', env=env, now=t + 30, release=True)\n"
            "tick(t + 3600); a = len(sent)\n"
            "rd('進行中三:新的一件', t + 3700); tick(t + 3710); b = len(sent)\n"
            "print(json.dumps({'a': a, 'b': b}))\n") % ((SEAT, SEAT) * 3))
        self.assertEqual((out["a"], out["b"]), (1, 2), "看過的進行中不重叫;多一件立刻叫:%r" % out)


class F3LockExpires(unittest.TestCase):
    def test_expired_lock_wakes(self):
        out = HN.QBox(self).py(SETUP + (
            "put('第一件:請看 PR 頁面上的說明', t); put('第二件:另一個題目要你決定', t)\n"
            "tick(t + 10); TA.agent_lock(%r, 'agent:%s/tri-1', env=env, now=t + 20)\n"
            "tick(t + 20 + TA.AGENT_LOCK_TTL_S - 1); a = len(sent)\n"
            "tick(t + 20 + TA.AGENT_LOCK_TTL_S); b = len(sent)\n"
            "print(json.dumps({'a': a, 'b': b}))\n") % (SEAT, SEAT))
        self.assertEqual((out["a"], out["b"]), (1, 2), out)


class F4Fleet(unittest.TestCase):
    def test_three_kinds_all_woken(self):
        out = HN.QBox(self).py(SETUP + (
            "kinds = {%r: 'claude', 'fghij': 'codex', 'klmno': 'deepseek'}\n"
            "for s, p in kinds.items():\n"
            "    put('第一件:請看 PR 頁面上的說明', t, seat=s, provider=p); put('第二件:另一個題目', t, seat=s, provider=p)\n"
            "tick(t + 10); tick(t + 70)\n"
            "got = {}\n"
            "for x in sent: got.setdefault(x['seat'], x['msg'])\n"
            "modes = {s: tri(s).get('woke_mode') for s in kinds}\n"
            "print(json.dumps({'seats': sorted(got), 'modes': modes}))\n") % SEAT)
        self.assertEqual(out["seats"], sorted([SEAT, "fghij", "klmno"]), "三種席都要叫到:%r" % out)
        self.assertEqual(out["modes"], {SEAT: "agent", "fghij": "agent", "klmno": "digest"}, out)


class F5Resident(unittest.TestCase):
    def test_brief_line_carries_rule(self):
        out = HN.QBox(self).py(SETUP + (
            "import inbox_brief_hook as IB\n"
            "i1 = put('第一件', t)\n"
            "Q.mark_read(%r, i1, 'seat:%s', env=env, now=t + 1, no_rev_check=True)\n"
            "one = IB.brief_line(%r, env=env, now=t + 2)\n"
            "i2 = put('第二件', t + 3)\n"
            "Q.mark_read(%r, i2, 'seat:%s', env=env, now=t + 4, no_rev_check=True)\n"
            "two = IB.brief_line(%r, env=env, now=t + 5)\n"
            "print(json.dumps({'one': one, 'two': two}))\n") % (SEAT, SEAT, SEAT, SEAT, SEAT, SEAT))
        self.assertNotIn("整理子代理", out["one"])
        self.assertIn("🧹 待審 0 / 進行中 2 ⇒ 開 1 個整理子代理", out["two"])
        self.assertNotIn("未整理", out["two"])

    def test_brief_line_marks_stalled(self):
        out = HN.QBox(self).py(SETUP + (
            "import inbox_brief_hook as IB\n"
            "put('第一件:請看 PR 頁面上的說明', t); put('第二件:另一個題目要你決定', t)\n"
            "for k in range(4): tick(t + 10 + k * A)\n"
            "print(json.dumps({'line': IB.brief_line(%r, env=env, now=t + 10 + 3 * A),"
            " 'b': Q.brief_payload(%r, env=env, now=t + 10 + 3 * A).get('triage_stalled')}))\n") % (SEAT, SEAT))
        self.assertTrue(out["b"], out)
        self.assertIn("⚠ 未整理(叫醒過還沒動)🧹 待審 2", out["line"])


class F6Board(unittest.TestCase):
    def test_status_lists_stalled_first(self):
        out = HN.QBox(self).py(SETUP + (
            "put('第一件:請看 PR 頁面上的說明', t); put('第二件:另一個題目要你決定', t)\n"
            "put('別席一', t, seat='fghij'); put('別席二', t, seat='fghij')\n"
            "tick(t + 10); TA.agent_lock('fghij', 'agent:fghij/tri-1', env=env, now=t + 20)\n"
            "for k in range(1, 4): tick(t + 10 + k * A)\n"
            "print(json.dumps(TA.status(env=env, now=t + 10 + 3 * A)))\n"))
        rows = {r["seat"]: r for r in out["seats"]}
        self.assertEqual(out["seats"][0]["seat"], SEAT, out)
        self.assertEqual((rows[SEAT]["stalled"], rows[SEAT]["followups"], rows[SEAT]["live_at_wake"]), (True, 2, 2))
        self.assertEqual((rows["fghij"]["stalled"], rows["fghij"]["agent"]), (False, "agent:fghij/tri-1"))
        self.assertEqual(out["stalled"], 1)


KNIVES = [
    ("K1", "src/cockpit/dm_triage.py", '    return True, "rewake", True\n', '    return False, "rewake", False\n',
     "F1NoPickupRewake.test_rewake_then_stall_then_new_mail"),
    ("K5", "src/cockpit/dm_triage.py", '    if int(rec.get("followups") or 0) >= REWAKE_MAX:\n', "    if False:\n",
     "F1NoPickupRewake.test_rewake_then_stall_then_new_mail"),
    ("K2", "src/cockpit/dm_triage.py", '    if live:\n        return False, "agent-working", False\n',
     '    if False:\n        return False, "agent-working", False\n', "F2AgentThenNextBatch.test_lock_holds_then_next_batch_immediately"),
    ("K2b", "src/cockpit/dm_triage.py", "    if not cur - handled:\n", "    if not cur:\n",
     "F2AgentThenNextBatch.test_lock_holds_then_next_batch_immediately"),
    ("K3", "src/cockpit/dm_queue.py", "        return \"digest\"\n    return \"agent\"\n",
     "        return \"agent\"\n    return \"agent\"\n", "F4Fleet.test_three_kinds_all_woken"),
    ("K4", "src/cockpit/dm_triage.py", "    if int(unread_n or 0) <= TRIAGE_MIN_UNREAD and int(inprog_n or 0) < INPROG_MIN:\n        return \"\"\n",
     "    return \"\"\n", "F5Resident.test_brief_line_carries_rule"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name, "test-dm-hold-notify-abb89-1015.py",
                                                                "test-dm-reliability-abb89-1015.py")) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=60, timeout=300)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
