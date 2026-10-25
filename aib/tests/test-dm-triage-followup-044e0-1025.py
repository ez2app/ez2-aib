#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""dm 整理叫醒的收口 / 全艦隊 / 常駐 / 看板(Andy 10-25 22:1x「如何確保所有 AI 都會觸發」,@abb89 派工 A / B / D / E,@044e0)。
治具沿用 test-dm-hold-notify-abb89-1015.py(整棵 src 抄進暫存根、假 tmux、假咽喉),投遞由注入的 deliver 收下,
不碰工作樹、不碰 ~/.ez2-aib、不送真 dm。

F1 收口:叫醒 15 分鐘內不再叫;滿 15 分鐘沒變少、沒整理檔 ⇒ 再叫一次(開頭「第二次提醒」);再 15 分鐘還沒動 ⇒
   triage_stalled、不再叫;只有進行中到門檻 ⇒ 不貼第二次、15 分鐘後直接 stalled(守 #374 S2)
F2 有開工就不追:叫醒後出現 pending/DMTRIAGE-<席>-*.md 或子代理登記 ⇒ 不再叫
F3 有進展就清旗子:封數變少 ⇒ 不再叫;已 stalled 之後封數變少 ⇒ triage_stalled 清掉
F4 全艦隊(D):claude / codex / API(digest)三種席各 2 封待審 ⇒ 2 拍內三席都收到叫醒句;API 席是 digest 模式
F5 常駐(B):待審框那行在進行中 ≥2 時帶整理規則,1 封時不帶;stalled 時前面加「⚠ 未整理」(brief_payload.triage_stalled)
F6 看板(E):triage-status 列出叫醒時 / 現在封數、叫了幾次、stalled 排最前
刀(複本 + run_guarded):K1 收口永不再叫 ⇒ F1 紅;K2 不看開工訊號 ⇒ F2 紅;K3 API 席也當 agent ⇒ F4 紅;
K4 常駐那句不出 ⇒ F5 紅;K2b 只有進行中也貼第二次 ⇒ F1 紅。刀一律斷言「FAIL: <目標>」且沒有 ERROR。
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
    "F = Q.TR.FOLLOWUP_S\n"
) % (SEAT, SEAT)


class F1Followup(unittest.TestCase):
    def test_rewake_then_stall(self):
        out = HN.QBox(self).py(SETUP + (
            "put('第一件:請看 PR 頁面上的說明', t); put('第二件:另一個題目要你決定', t)\n"
            "tick(t + 10); n1 = len(sent)\n"
            "tick(t + 10 + F - 1); n2 = len(sent)\n"
            "tick(t + 10 + F); n3 = len(sent)\n"
            "tick(t + 10 + 2 * F); st = tri()\n"
            "tick(t + 10 + 3 * F); n4 = len(sent)\n"
            "print(json.dumps({'n': [n1, n2, n3, n4], 'second': sent[-1]['msg'] if sent else '',"
            " 'stalled': st.get('triage_stalled'), 'followups': st.get('followups')}))\n"))
        self.assertEqual(out["n"], [1, 1, 2, 2], out)
        self.assertTrue(out["second"].startswith("【第二次提醒"), out["second"][:80])
        self.assertEqual((out["stalled"], out["followups"]), (True, 1), out)


    def test_inprog_only_stalls_without_second_paste(self):
        out = HN.QBox(self).py(SETUP + (
            "for m in ('進行中一:審查還沒回來', '進行中二:等 CI'):\n"
            "    i = put(m, t); Q.mark_read(%r, i, 'seat:%s', env=env, now=t, no_rev_check=True)\n"
            "tick(t + 10); tick(t + 10 + F); st = tri()\n"
            "print(json.dumps({'n': len(sent), 'stalled': st.get('triage_stalled')}))\n") % (SEAT, SEAT))
        self.assertEqual((out["n"], out["stalled"]), (1, True), "只有進行中 ⇒ 不貼第二次、直接標未整理:%r" % out)


class F2StartedSignal(unittest.TestCase):
    def test_dmtriage_file_or_lock_stops_followup(self):
        out = HN.QBox(self).py(SETUP + (
            "import os, dm_triage_agent as TA\n"
            "put('第一件:請看 PR 頁面上的說明', t); put('第二件:另一個題目要你決定', t)\n"
            "tick(t + 10)\n"
            "d = Q._aib_pending_dir(env); d.mkdir(parents=True, exist_ok=True)\n"
            "f = d / ('DMTRIAGE-%s-x.md'); f.write_text('整理'); os.utime(f, (t + 20, t + 20))\n"
            "tick(t + 10 + F); a = len(sent)\n"
            "put('第三件', t + 30); put('第四件', t + 30, seat='fghij')\n"
            "f.unlink(); TA.agent_lock(%r, 'agent:%s/tri-1', env=env, now=t + 2 * F + 30)\n"
            "tick(t + 2 * F + 20); b = len(sent)\n"
            "tick(t + 3 * F + 30); c = len(sent)\n"
            "print(json.dumps({'a': a, 'b': b, 'c': c}))\n") % (SEAT, SEAT, SEAT))
        self.assertEqual(out["a"], 1, "有整理檔 ⇒ 不再叫:%r" % out)
        self.assertEqual(out["b"], 2, "第三件是新信 ⇒ 照常叫:%r" % out)
        self.assertEqual(out["c"], 2, "叫醒後有子代理登記 ⇒ 不再叫:%r" % out)


class F3Progress(unittest.TestCase):
    def test_drop_clears(self):
        out = HN.QBox(self).py(SETUP + (
            "ids = [put('第一件:請看 PR 頁面上的說明', t), put('第二件:另一個題目要你決定', t),"
            " put('第三件:還有一件', t)]\n"
            "tick(t + 10)\n"
            "Q.mark_read(%r, ids[0], 'seat:%s', env=env, now=t + 20, no_rev_check=True)\n"
            "Q.mark_done(%r, ids[0], 'seat:%s', None, env=env, now=t + 21, no_rev_check=True, why='做完')\n"
            "tick(t + 10 + F); a = len(sent)\n"
            "tick(t + 10 + 2 * F); tick(t + 10 + 3 * F); s1 = tri().get('triage_stalled')\n"
            "print(json.dumps({'a': a, 's1': s1}))\n") % (SEAT, SEAT, SEAT, SEAT))
        self.assertEqual(out["a"], 1, "封數變少 ⇒ 不再叫:%r" % out)
        self.assertFalse(out["s1"], out)

    def test_stalled_then_drop_clears_flag(self):
        out = HN.QBox(self).py(SETUP + (
            "ids = [put('第一件:請看 PR 頁面上的說明', t), put('第二件:另一個題目要你決定', t)]\n"
            "tick(t + 10); tick(t + 10 + F); tick(t + 10 + 2 * F); s0 = tri().get('triage_stalled')\n"
            "Q.mark_read(%r, ids[0], 'seat:%s', env=env, now=t + 3 * F, no_rev_check=True)\n"
            "Q.mark_done(%r, ids[0], 'seat:%s', None, env=env, now=t + 3 * F, no_rev_check=True, why='做完')\n"
            "tick(t + 3 * F + 60); s1 = tri().get('triage_stalled')\n"
            "print(json.dumps({'s0': s0, 's1': s1}))\n") % (SEAT, SEAT, SEAT, SEAT))
        self.assertEqual((out["s0"], out["s1"]), (True, False), out)


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
            "tick(t + 10); tick(t + 10 + F); tick(t + 10 + 2 * F)\n"
            "print(json.dumps({'line': IB.brief_line(%r, env=env, now=t + 10 + 2 * F),"
            " 'b': Q.brief_payload(%r, env=env, now=t + 10 + 2 * F).get('triage_stalled')}))\n") % (SEAT, SEAT))
        self.assertTrue(out["b"], out)
        self.assertIn("⚠ 未整理(叫醒過還沒動)🧹 待審 2", out["line"])


class F6Board(unittest.TestCase):
    def test_status_lists_stalled_first(self):
        out = HN.QBox(self).py(SETUP + (
            "import dm_triage_agent as TA\n"
            "put('第一件:請看 PR 頁面上的說明', t); put('第二件:另一個題目要你決定', t)\n"
            "put('別席一', t, seat='fghij'); put('別席二', t, seat='fghij')\n"
            "tick(t + 10); tick(t + 10 + F)\n"
            "x = put('別席三', t + F + 20, seat='fghij')\n"
            "tick(t + 10 + 2 * F)\n"
            "print(json.dumps(TA.status(env=env, now=t + 10 + 2 * F)))\n"))
        rows = {r["seat"]: r for r in out["seats"]}
        self.assertEqual(out["seats"][0]["seat"], SEAT, out)
        self.assertEqual((rows[SEAT]["stalled"], rows[SEAT]["followups"], rows[SEAT]["live_at_wake"]), (True, 1, 2))
        self.assertFalse(rows["fghij"]["stalled"])
        self.assertEqual(out["stalled"], 1)


KNIVES = [
    ("K1", "src/cockpit/dm_triage.py", "        return \"stall\"\n    return \"rewake\"\n",
     "        return \"stall\"\n    return None\n", "F1Followup.test_rewake_then_stall"),
    ("K2b", "src/cockpit/dm_triage.py", " or (unread_n is not None and int(unread_n) <= TRIAGE_MIN_UNREAD):",
     ":", "F1Followup.test_inprog_only_stalls_without_second_paste"),
    ("K2", "src/cockpit/dm_queue.py", "    act = TR.followup_action(rec, now, live_n, _triage_progressed(seat, rec, env), unread_n=len(unread))\n",
     "    act = TR.followup_action(rec, now, live_n, False, unread_n=len(unread))\n", "F2StartedSignal.test_dmtriage_file_or_lock_stops_followup"),
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
