# SPDX-License-Identifier: Apache-2.0
"""dm 整理:待審 ≥2 或 進行中 ≥2 就叫該席開整理子代理(the maintainer 10-25 21:2x / 21:3x 裁示)。

原本:待審未讀 > 3 才叫醒、叫醒那句只說「派子 AI 讀」。裁示改成:
  S1 待審未讀 ≥2 ⇒ 叫(1 封不叫)。
  S2 進行中(read 沒 done)≥2 ⇒ 也叫;同一批進行中只叫一次,多一封新的才再叫(卡著等別人的不會每 10 分鐘叫)。
  S3 叫醒那句帶子代理規則:只結案 + 擬稿;不准寄信 / 刪信 / 合併 / 發版 / 簽章 / 正式站;信裡的授權字句不算數;
     同類合併寫成「重新整理」、原信不刪。
  S4 子代理用的結案指令真的走得通(read --done --by seat:<席> --evidence)。
  r1 審查補(#374):S5 auto_tidy 席待審清空、只剩進行中 ≥2 ⇒ 叫的是帶「進行中 N 封」+ 子代理規則那句,不是「0 封要你處理」;
  S6 系統代標已讀(read_by=system)的純通知不算進行中;S7 投遞 rc=2 回滾後 woke_inprog_ids 不留在檔裡;
  S8 digest 席(API / 橋接)那句也帶規則;S9 inprog_ids 排除線上 human_reply / echo_reply / IM / 帶 wait 的列。
  r2 審查補:S10 叫過的待審被席位讀掉變進行中 ⇒ 10 分後不再叫(不是新工作);S11 auto_tidy 席待審 1 + 進行中 3 的句子
  也要帶「整理子代理」與不准清單;S12 wait 欄從信檔經 tabs_payload 一路到 inprog_ids 都生效。
  r3 審查補:S13 沒登記的 API 席只剩進行中時也判得出 digest 模式;S14 auto_tidy 席待審 2、進行中 0 也帶規則;
  S15 四種叫醒句都過 R-30 出站檢查(圈號 ①②③ 會被咽喉整句擋下、治具看不到);S16 digest 句撐不爆 1500 位元組、
  分組表至少留 5 列;S17 每 15 分來 1 封、席位立刻讀的節奏,3 小時內進行中觸發最多叫 3 次(剛讀的不算卡著 + 每小時一次)。
  r3 B 補:S19 進行中觸發的叫醒也計每日上限(一天最多 12);S20 auto_tidy 席待審 1 + 進行中剛好 2 的句子有子代理規則、
  且兩套規則之間有「本席自己」的主詞切換;S21 攤成一行(copilot 等 inline 席)後規則與橋句都還在、沒換行。

治具沿用 test-dm-hold-notify-abb89-1015.py(整棵 src 抄進暫存根、假 tmux、假咽喉),投遞由注入的 deliver 收下,
不碰工作樹、不碰 ~/.ez2-aib、不送真 dm。
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_spec = importlib.util.spec_from_file_location("dmhold_fixture", HERE / "test-dm-hold-notify-abb89-1015.py")
HN = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(HN)

SEAT = HN.SEAT


def setUpModule():
    HN.setUpModule()


def tearDownModule():
    HN.tearDownModule()


SETUP = (
    "Q.LOAD_FN = lambda: (0.0, 0.0, 0.0)\n"      # 叫醒有 load1 ≤ 12 這道閘;測試機忙不該讓斷言變
    "def put(msg, now):\n"
    "    return Q.record(%r, msg, '@77777', status='delivered', rc=0, env=env, now=now,\n"
    "                    sender_kind='ai', wid='@2', provider='claude', hub='aihub-t1')['id']\n"
    "def inprog(msg, now):\n"
    "    i = put(msg, now)\n"
    "    r = Q.mark_read(%r, i, 'seat:%s', env=env, if_rev=None, no_rev_check=True, now=now - 900)\n"   # 讀了 15 分 = 卡著
    "    assert r.get('ok'), r\n"
    "    return i\n"
    "sent = []\n"
    "def deliver(item):\n"
    "    if str(item.get('id', '')).startswith('triage_'):\n"
    "        sent.append(item['msg'])\n"
    "    return 0\n"
    "def tick(now):\n"
    "    return Q.triage_tick(env=env, now=now, hold_fn=lambda s, r: (False, 'idle'), deliver_fn=deliver, force=True)\n"
) % (SEAT, SEAT, SEAT)


class S1WaitingThreshold(unittest.TestCase):

    def test_two_unread_wakes(self):
        b = HN.QBox(self)
        out = b.py(SETUP + (
            "t = int(__import__('time').time())\n"
            "put('第一件:請看 PR 頁面上的說明', t); put('第二件:另一個題目要你決定', t)\n"
            "tick(t + 10)\n"
            "print(json.dumps({'sent': sent}))\n"))
        self.assertEqual(len(out["sent"]), 1, "待審 2 封 ⇒ 叫一次:%r" % out)

    def test_one_unread_does_not_wake(self):
        b = HN.QBox(self)
        out = b.py(SETUP + (
            "t = int(__import__('time').time())\n"
            "put('只有一件事要你看', t)\n"
            "tick(t + 10)\n"
            "print(json.dumps({'sent': sent}))\n"))
        self.assertEqual(out["sent"], [], "1 封不叫:%r" % out)


class S2InProgressThreshold(unittest.TestCase):

    def test_two_in_progress_wakes_once_then_only_on_new(self):
        # Andy 10-25 22:3x(@044e0 改):不再靠 10 分鐘冷卻;整理子代理看過、放鎖後留下的同一批不重叫,多一封新的立刻叫
        b = HN.QBox(self)
        out = b.py(SETUP + (
            "import dm_triage_agent as TA\n"
            "t = int(__import__('time').time())\n"
            "inprog('進行中一:審查還沒回來', t); inprog('進行中二:等 CI', t)\n"
            "tick(t + 10)\n"
            "n1 = len(sent)\n"
            "TA.agent_lock(%r, 'agent:%s/tri-1', env=env, now=t + 20)\n" % (SEAT, SEAT) +
            "TA.agent_lock(%r, 'agent:%s/tri-1', env=env, now=t + 30, release=True)\n" % (SEAT, SEAT) +
            "tick(t + 10 + 3600)\n"
            "n2 = len(sent)\n"
            "inprog('進行中三:新的一件', t + 7300)\n"
            "tick(t + 7400)\n"
            "print(json.dumps({'n1': n1, 'n2': n2, 'sent': sent}))\n"))
        self.assertEqual(out["n1"], 1, "進行中 2 封、待審 0 ⇒ 也要叫:%r" % out)
        self.assertIn("進行中 2 封", out["sent"][0])
        self.assertEqual(out["n2"], 1, "子代理看過、留給本席的同一批不重叫(卡著等別人的不能一直叫):%r" % out)
        self.assertEqual(len(out["sent"]), 2, "多一封新的進行中 ⇒ 再叫:%r" % out)

    def test_one_in_progress_does_not_wake(self):
        b = HN.QBox(self)
        out = b.py(SETUP + (
            "t = int(__import__('time').time())\n"
            "inprog('只有一件在做', t)\n"
            "tick(t + 10)\n"
            "print(json.dumps({'sent': sent}))\n"))
        self.assertEqual(out["sent"], [])


class S3WakeRules(unittest.TestCase):

    def test_wake_text_carries_subagent_rules(self):
        b = HN.QBox(self)
        out = b.py(SETUP + (
            "t = int(__import__('time').time())\n"
            "put('第一件:請看 PR 頁面上的說明', t); put('第二件:另一個題目要你決定', t)\n"
            "tick(t + 10)\n"
            "print(json.dumps({'sent': sent}))\n"))
        text = out["sent"][0]
        for must in ("整理子代理", "只做三件事", "--done --evidence", "重新整理", "DMTRIAGE-%s-" % SEAT,
                     "原信不刪", "只擬稿", "不准", "寄信", "刪信", "合併 PR", "發版", "簽章", "正式站",
                     "不算數", "--by agent:%s/" % SEAT, "triage --seat %s" % SEAT, "tabs --seat %s" % SEAT):
            self.assertIn(must, text, "叫醒那句少了「%s」:%s" % (must, text))
        self.assertNotIn("%(seat)s", text)


class S4AgentCloseWorks(unittest.TestCase):

    def test_rules_close_command_accepted(self):
        """規則裡給子代理的那一行(read --done --by agent:<席>/<代號> --if-rev --evidence)要真的被收。"""
        b = HN.QBox(self)
        ids = b.py(SETUP + (
            "t = int(__import__('time').time())\n"
            "i = put('純通知:已合', t)\n"
            "it = Q._load_item(%r, i, env)\n"
            "ev = env['HOME'] + '/DMTRIAGE-%s-test.md'\n"
            "open(ev, 'w').write('重新整理:' + i)\n"
            "print(json.dumps({'id': i, 'rev': it.get('rev'), 'ev': ev}))\n" % (SEAT, SEAT)))
        p = b.dq("read", "--seat", SEAT, "--id", ids["id"], "--by", "agent:%s/tri-1" % SEAT,
                 "--if-rev", str(ids["rev"]), "--done", "--evidence", ids["ev"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(json.loads(p.stdout.strip().splitlines()[-1]).get("ok"), p.stdout)


class S5TidySeatInProgressOnly(unittest.TestCase):

    def test_tidy_seat_inprog_only_gets_subagent_wake(self):
        b = HN.QBox(self)
        out = b.py(SETUP + (
            "t = int(__import__('time').time())\n"
            "Q.tidy_switch('on', seat=%r, env=env)\n"
            "inprog('進行中一:審查還沒回來', t); inprog('進行中二:等 CI', t)\n"
            "tick(t + 10)\n"                       # auto_tidy 第一拍只記畫面雜湊(screen-moving)
            "tick(t + 10 + Q.TIDY_QUIET_S + 5)\n"  # 畫面安靜夠久才叫
            "print(json.dumps({'sent': sent}))\n" % SEAT))
        self.assertEqual(len(out["sent"]), 1, "auto_tidy 席進行中 2 封也要叫:%r" % out)
        text = out["sent"][0]
        self.assertNotIn("有 0 封要你處理", text, text)
        self.assertIn("進行中 2 封", text)
        self.assertIn("整理子代理", text)
        self.assertIn("不算數", text)


class S6SystemAutoReadNotInProgress(unittest.TestCase):

    def test_system_read_notice_excluded(self):
        import importlib.util as iu
        spec = iu.spec_from_file_location("dm_triage_u", HN.REL.TREE / "src" / "cockpit" / "dm_triage.py")
        TR = iu.module_from_spec(spec); spec.loader.exec_module(TR)
        rows = [{"id": "a", "status": "read", "read_by": "system"},
                {"id": "b", "status": "read", "read_by": "seat:%s" % SEAT},
                {"id": "c", "status": "read"}]
        self.assertEqual(TR.inprog_ids(rows), ["b", "c"])

    def test_auto_read_notice_does_not_rewake(self):
        """非 tidy 席:第一拍叫醒 + 代標「到」為已讀 ⇒ 10 分後進行中多了那封「到」,不得再叫。"""
        b = HN.QBox(self)
        out = b.py(SETUP + (
            "t = int(__import__('time').time())\n"
            "inprog('卡著的一件', t)\n"
            "put('到', t); put('真的一件事要你看', t)\n"
            "tick(t + 10)\n"
            "n1 = len(sent)\n"
            "tick(t + 710)\n"
            "rows = Q.tabs_payload(%r, env=env)['tabs']['read']['rows']\n"
            "print(json.dumps({'n1': n1, 'n2': len(sent), 'read_by': sorted(str(r.get('read_by')) for r in rows)}))\n" % SEAT))
        self.assertEqual(out["n1"], 1, out)
        self.assertIn("system", out["read_by"], "治具前提:「到」要被系統代標已讀:%r" % out)
        self.assertEqual(out["n2"], 1, "代標已讀的通知不算進行中,不得再叫:%r" % out)


class S7Rc2Rollback(unittest.TestCase):

    def test_rc2_rolls_back_woke_inprog_ids(self):
        b = HN.QBox(self)
        out = b.py(SETUP.replace("    return 0\n", "    return RC[0]\nRC = [0]\n") + (
            "t = int(__import__('time').time())\n"
            "inprog('進行中一', t); inprog('進行中二', t)\n"
            "RC[0] = 2\n"
            "tick(t + 10)\n"
            "rec1 = json.load(open(str(Q.triage_path(%r, env))))\n"
            "RC[0] = 0\n"
            "tick(t + 100)\n"
            "rec2 = json.load(open(str(Q.triage_path(%r, env))))\n"
            "print(json.dumps({'n': len(sent), 'k1': 'woke_inprog_ids' in rec1, 'k2': rec2.get('woke_inprog_ids')}))\n" % (SEAT, SEAT)))
        self.assertFalse(out["k1"], "rc=2 一個字都沒進 ⇒ woke_inprog_ids 要回滾:%r" % out)
        self.assertEqual(len(out["k2"] or []), 2, "重試成功後才記下:%r" % out)
        self.assertEqual(out["n"], 2)


class S8DigestCarriesRules(unittest.TestCase):

    def test_digest_text(self):
        import importlib.util as iu
        spec = iu.spec_from_file_location("dm_triage_u2", HN.REL.TREE / "src" / "cockpit" / "dm_triage.py")
        TR = iu.module_from_spec(spec); spec.loader.exec_module(TR)
        text = TR.wake_text(SEAT, {"pending": 2}, 2, "/x/dm_queue.py", mode="digest", digest=["  q1 x", "  q2 y"], inprog=3)
        for must in ("進行中 3 封", "不算數", "重新整理", "要裁", "你自己", "--by seat:%s --if-rev" % SEAT):   # r6 L3:指令要完整
            self.assertIn(must, text, text)
        for no in ("不寄信", "只擬稿", "整理子代理"):      # r5 M1:digest 席開不了子代理,主詞是本席
            self.assertNotIn(no, text, text)
        self.assertLessEqual(len(text.encode("utf-8")), TR.WAKE_DIGEST_MAX_BYTES)


class S9InProgressExclusions(unittest.TestCase):

    def test_excluded_rows(self):
        import importlib.util as iu
        spec = iu.spec_from_file_location("dm_triage_u3", HN.REL.TREE / "src" / "cockpit" / "dm_triage.py")
        TR = iu.module_from_spec(spec); spec.loader.exec_module(TR)
        rows = [{"id": "h", "status": "read", "global": {"human_reply": True}},
                {"id": "e", "status": "read", "global": {"echo_reply": True}},
                {"id": "i", "status": "read", "im": {"chat": 1}},
                {"id": "w", "status": "read", "wait": {"text": "等 Andy", "at": 1}},
                {"id": "d", "status": "delivered"},
                {"id": "ok", "status": "read"}]
        self.assertEqual(TR.inprog_ids(rows), ["ok"])


class S10ReadOwnWakeNotNewInProgress(unittest.TestCase):

    def test_reading_the_woken_mail_does_not_rewake(self):
        b = HN.QBox(self)
        out = b.py(SETUP + (
            "t = int(__import__('time').time())\n"
            "a = put('第一件:請看 PR', t); c = put('第二件:要你決定', t)\n"
            "tick(t + 10)\n"
            "n1 = len(sent)\n"
            "for i in (a, c):\n"
            "    assert Q.mark_read(%r, i, 'seat:%s', env=env, if_rev=None, no_rev_check=True, now=t + 60).get('ok')\n"
            "tick(t + 700)\n"
            "n2 = len(sent)\n"
            "tick(t + 3700)\n"                     # r5 L1:過了每小時冷卻也不得重叫同一批
            "print(json.dumps({'n1': n1, 'n2': n2, 'n3': len(sent)}))\n" % (SEAT, SEAT)))
        self.assertEqual(out["n1"], 1, out)
        self.assertEqual(out["n2"], 1, "剛叫過的兩封被讀掉變進行中,不是新工作,不得再叫:%r" % out)
        self.assertEqual(out["n3"], 1, "一小時後同一批仍不得重叫:%r" % out)


class S11TidySeatMixedCarriesRules(unittest.TestCase):

    def test_tidy_seat_todo1_inprog3(self):
        b = HN.QBox(self)
        out = b.py(SETUP + (
            "t = int(__import__('time').time())\n"
            "Q.tidy_switch('on', seat=%r, env=env)\n"
            "inprog('進行中一', t); inprog('進行中二', t); inprog('進行中三', t)\n"
            "put('真的一件事要你看 PR 說明', t)\n"
            "tick(t + 10)\n"
            "tick(t + 10 + Q.TIDY_QUIET_S + 5)\n"
            "print(json.dumps({'sent': sent}))\n" % SEAT))
        self.assertEqual(len(out["sent"]), 1, out)
        text = out["sent"][0]
        for must in ("進行中 3 封", "整理子代理", "不算數", "stage-tidy"):
            self.assertIn(must, text, text)


class S12WaitFromItemFile(unittest.TestCase):

    def test_wait_excludes_through_tabs_payload(self):
        b = HN.QBox(self)
        out = b.py(SETUP + (
            "t = int(__import__('time').time())\n"
            "w = inprog('在等 Andy 簽章', t); k = inprog('正常進行中', t)\n"
            "it = Q._load_item(%r, w, env); it['wait'] = {'text': '等 Andy 簽章', 'at': t}; Q._write_item(%r, it, env)\n"
            "import importlib.util as iu\n"
            "sp = iu.spec_from_file_location('tr', str(__import__('pathlib').Path(Q.__file__).with_name('dm_triage.py')))\n"
            "TR = iu.module_from_spec(sp); sp.loader.exec_module(TR)\n"
            "rows = Q.tabs_payload(%r, env=env)['tabs']['read']['rows']\n"
            "print(json.dumps({'ids': TR.inprog_ids(rows), 'k': k, 'w': w}))\n" % (SEAT, SEAT, SEAT)))
        self.assertEqual(out["ids"], [out["k"]], out)


def _tr():
    import importlib.util as iu
    spec = iu.spec_from_file_location("dm_triage_u9", HN.REL.TREE / "src" / "cockpit" / "dm_triage.py")
    m = iu.module_from_spec(spec); spec.loader.exec_module(m)
    return m


class S13DigestSeatInProgressOnly(unittest.TestCase):

    def test_unregistered_api_seat_mode_digest(self):
        b = HN.QBox(self, seats=())
        out = b.py(SETUP.replace("provider='claude'", "provider='deepseek'") + (
            "t = int(__import__('time').time())\n"
            "inprog('進行中一', t); inprog('進行中二', t)\n"
            "res = tick(t + 10)\n"
            "print(json.dumps({'res': res, 'sent': sent}))\n"))
        rows = [r for r in (out["res"] or []) if isinstance(r, dict) and r.get("seat") == SEAT]
        self.assertTrue(rows, out)
        self.assertEqual(rows[0].get("mode"), "digest", "只剩進行中時 probe 要退而取 read 列,不然判不出 digest:%r" % out)
        self.assertEqual(len(out["sent"]), 1, out)


class S14TidySeatTodoTwo(unittest.TestCase):

    def test_tidy_todo2_inprog0_carries_rules(self):
        b = HN.QBox(self)
        out = b.py(SETUP + (
            "t = int(__import__('time').time())\n"
            "Q.tidy_switch('on', seat=%r, env=env)\n"
            "put('第一件:請看 PR 說明', t); put('第二件:另一題要你決定', t)\n"
            "tick(t + 10)\n"
            "tick(t + 10 + Q.TIDY_QUIET_S + 5)\n"
            "print(json.dumps({'sent': sent}))\n" % SEAT))
        self.assertEqual(len(out["sent"]), 1, out)
        for must in ("整理子代理", "不算數", "stage-tidy"):
            self.assertIn(must, out["sent"][0], out["sent"][0])


class S15WakeTextPassesOutboundLint(unittest.TestCase):

    def test_all_wake_texts_lint_clean(self):
        import importlib.util as iu
        TR = _tr()
        spec = iu.spec_from_file_location("r30u", HN.REL.TREE / "src" / "messaging" / "r30_lint.py")
        L = iu.module_from_spec(spec); spec.loader.exec_module(L)
        need = [{"id": "q_%d" % i, "rev": 1, "from": "@x", "sender_kind": "ai", "summary": "s%d" % i} for i in range(10)]
        texts = [TR.wake_text(SEAT, {"pending": 2}, 2, "/x/dm_queue.py", mode="agent", inprog=3),
                 TR.wake_text(SEAT, {"pending": 2}, 2, "/x/dm_queue.py", mode="digest", digest=["  q1 x"], inprog=3),
                 TR.tidy_wake_text(SEAT, need, 12, "/x/dm_queue.py", mode="agent", inprog=3),
                 TR.tidy_wake_text(SEAT, need, 12, "/x/dm_queue.py", mode="digest", inprog=0),
                 TR.tidy_wake_text(SEAT, need[:1], 1, "/x/dm_queue.py", mode="agent", inprog=0)]
        for t in texts:
            bad = L.lint(t)
            self.assertEqual(bad, [], "叫醒句過不了 R-30 出站檢查(咽喉會整句擋下):%r\n%s" % (bad[:3], t[:200]))


class S16DigestBudget(unittest.TestCase):

    def test_tidy_digest_keeps_rows(self):
        TR = _tr()
        need = [{"id": "q_%d" % i, "rev": 1, "from": "@x", "sender_kind": "ai", "summary": "摘要 %d" % i} for i in range(10)]
        text = TR.tidy_wake_text(SEAT, need, 12, "/x/dm_queue.py", mode="digest", inprog=3)
        self.assertLessEqual(len(text.encode("utf-8")), TR.WAKE_DIGEST_MAX_BYTES, len(text.encode("utf-8")))
        kept = [ln for ln in text.split("\n") if ln.startswith("  ")]
        self.assertGreaterEqual(len(kept), 5, text)
        self.assertIn("不算數", text)
        self.assertIn("要裁", text)
        self.assertNotIn("不寄信", text)
        self.assertNotIn("整理子代理", text)
        text2 = TR.wake_text(SEAT, {"pending": 10}, 10, "/x/dm_queue.py", mode="digest",
                             digest=["  q%d x" % i for i in range(10)], inprog=3)
        self.assertLessEqual(len(text2.encode("utf-8")), TR.WAKE_DIGEST_MAX_BYTES)
        self.assertGreaterEqual(len([ln for ln in text2.split("\n") if ln.startswith("  ")]), 5, text2)


class S17SteadyRhythmBudget(unittest.TestCase):

    def test_one_mail_per_15min_read_at_once(self):
        """每 15 分來 1 封、席位立刻讀、做 2 小時才結:3 小時內進行中觸發最多叫 3 次,不得把一天 12 次用光。"""
        b = HN.QBox(self)
        out = b.py(SETUP + (
            "t = int(__import__('time').time())\n"
            "for k in range(12):\n"
            "    i = put('工作 %%d' %% k, t + k * 900)\n"
            "    assert Q.mark_read(%r, i, 'seat:%s', env=env, if_rev=None, no_rev_check=True, now=t + k * 900 + 5).get('ok')\n"
            "    for m in range(3):\n"
            "        tick(t + k * 900 + 60 + m * 300)\n"
            "print(json.dumps({'n': len(sent)}))\n" % (SEAT, SEAT)))
        self.assertGreaterEqual(out["n"], 1, out)
        self.assertLessEqual(out["n"], 3, "3 小時叫了 %d 次,會把一天 12 次用光:%r" % (out["n"], out))


class S18JustReadIsNotStuck(unittest.TestCase):

    def test_fresh_read_not_counted_until_settled(self):
        b = HN.QBox(self)
        out = b.py(SETUP + (
            "t = int(__import__('time').time())\n"
            "for k in range(2):\n"
            "    i = put('剛讀 %%d' %% k, t)\n"
            "    assert Q.mark_read(%r, i, 'seat:%s', env=env, if_rev=None, no_rev_check=True, now=t).get('ok')\n"
            "tick(t + 10)\n"
            "n1 = len(sent)\n"
            "tick(t + 700)\n"
            "print(json.dumps({'n1': n1, 'n2': len(sent)}))\n" % (SEAT, SEAT)))
        self.assertEqual(out["n1"], 0, "席位剛讀的兩封還在動手,不算卡著、不叫:%r" % out)
        self.assertEqual(out["n2"], 1, "過了 INPROG_SETTLE_S 還沒結 ⇒ 叫一次:%r" % out)


class S19DailyCapCountsInProgressWakes(unittest.TestCase):

    def test_inprog_wakes_hit_daily_max(self):
        b = HN.QBox(self)
        out = b.py(SETUP + (
            "t = int(__import__('time').time())\n"
            "for k in range(14):\n"
            "    inprog('卡著 %d-a' % k, t + k * 3700); inprog('卡著 %d-b' % k, t + k * 3700)\n"
            "    tick(t + k * 3700 + 10)\n"
            "print(json.dumps({'n': len(sent), 'max': Q.WAKE_DAILY_MAX}))\n"))
        self.assertEqual(out["n"], out["max"], "進行中觸發的叫醒也要計每日上限:%r" % out)


class S20TidySeatExactThreshold(unittest.TestCase):

    def test_todo1_inprog2_has_rules_and_bridge(self):
        b = HN.QBox(self)
        out = b.py(SETUP + (
            "t = int(__import__('time').time())\n"
            "Q.tidy_switch('on', seat=%r, env=env)\n"
            "inprog('進行中一', t); inprog('進行中二', t)\n"
            "put('真的一件事要你看 PR 說明', t)\n"
            "tick(t + 10)\n"
            "tick(t + 10 + Q.TIDY_QUIET_S + 5)\n"
            "print(json.dumps({'sent': sent}))\n" % SEAT))
        self.assertEqual(len(out["sent"]), 1, out)
        text = out["sent"][0]
        for must in ("進行中 2 封", "整理子代理", "不算數", "由本席自己照下列規則", "stage-tidy"):
            self.assertIn(must, text, text)
        self.assertLess(text.index("不算數"), text.index("由本席自己照下列規則"), "子代理規則在前、本席規則在後")


class S21InlineFlattenKeepsRules(unittest.TestCase):

    def test_copilot_one_line(self):
        TR = _tr()
        import importlib.util as iu
        spec = iu.spec_from_file_location("dmq_u", HN.REL.TREE / "src" / "cockpit" / "dm_queue.py")
        Q = iu.module_from_spec(spec); spec.loader.exec_module(Q)
        need = [{"id": "q_%d" % i, "rev": 1, "from": "@x", "sender_kind": "ai", "summary": "s%d" % i} for i in range(3)]
        for text in (TR.wake_text(SEAT, {"pending": 2}, 2, "/x/dm_queue.py", mode="agent", inprog=3),
                     TR.tidy_wake_text(SEAT, need, 3, "/x/dm_queue.py", mode="agent", inprog=2)):
            flat = Q._one_line_for_line_reader(text, "copilot")
            self.assertNotIn("\n", flat)
            for must in ("1. 結案", "不算數", "只擬稿"):
                self.assertIn(must, flat, flat[:300])
            self.assertGreaterEqual(len(flat), len(text) - 50, "攤平不得截斷規則")


class S22DoneSomeNotNew(unittest.TestCase):

    def test_closing_one_of_three_does_not_rewake(self):
        """進行中 3 封叫過一次 → 結掉 1 封 → 一小時後集合變小不算「新的」,不得再叫(r6 L2)。"""
        b = HN.QBox(self)
        out = b.py(SETUP + (
            "t = int(__import__('time').time())\n"
            "ids = [inprog('卡著 %%d' %% k, t) for k in range(3)]\n"
            "tick(t + 10)\n"
            "n1 = len(sent)\n"
            "assert Q.mark_done(%r, ids[0], 'seat:%s', None, env=env, if_rev=None, no_rev_check=True, why='test').get('ok')\n"
            "tick(t + 3700)\n"
            "print(json.dumps({'n1': n1, 'n2': len(sent)}))\n" % (SEAT, SEAT)))
        self.assertEqual(out["n1"], 1, out)
        self.assertEqual(out["n2"], 1, "結掉幾封不是新工作,不得再叫:%r" % out)


if __name__ == "__main__":
    unittest.main()
