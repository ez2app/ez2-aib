# SPDX-License-Identifier: Apache-2.0
"""aib dm:重送仍被擋的信不再悄悄轉人審;人審的信要讓收件席知道(10-15 稽核 G7)。

真艙實案(10-15):收件席忙,aib dm 第一輪回 rc 2(一個字都沒進框),自動重送一次仍 2 ⇒ dm-ctl 記成
hold_human(attempts=1)。hold_human 不會再被排空器領,收件席沒有任何提示 —— @abb89 漏看 5 封,兩封含維護者裁示。

本尺量四件:
  G7-1 重送輪仍 rc 2(確定零進框)⇒ 排進那一席的佇列(sort-queued,attempts=1),由排空器空閒時投;
       排空器再 rc 2 照 attempts 累計,滿 RESEND_MAX_ATTEMPTS 才 hold。rc 3/4(可能已進框)照舊 hold_human。
  G7-2 收件席有「新的」人審信、而且閒著 ⇒ triage 那一拍貼一句提示(四語);同一批只提示一次;忙就下一拍。
  G7-3 寄件端 RESULT 講清楚:then=queued / then=hold-human(+ notify=on-idle|none),rc 契約不改。
  G7-4 dm-queue 既有流程(list、read --done)不受提示狀態檔影響。

治具與 test-dm-reliability-abb89-1015.py 同一套(整棵 src 抄進暫存根、假咽喉、PATH 前放假 tmux);
另外在**抄出來那一份**把授權過渡開關打開(keys.DRAFT_ACCEPT_ANY=True),好讓佇列 admit 准 ——
只改暫存根裡的拷貝,不碰工作樹、不碰 ~/.ez2-aib、不送真 dm。
"""
import base64
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

_spec = importlib.util.spec_from_file_location("dmrel_fixture", HERE / "test-dm-reliability-abb89-1015.py")
REL = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(REL)

SEAT = "abcde"


def setUpModule():
    REL.setUpModule()
    keys = REL.TREE / "src" / "entitlement" / "keys.py"
    text = keys.read_text(encoding="utf-8")
    assert "DRAFT_ACCEPT_ANY = False" in text, "治具前提:keys.py 的過渡開關字面"
    keys.write_text(text.replace("DRAFT_ACCEPT_ANY = False", "DRAFT_ACCEPT_ANY = True", 1), encoding="utf-8")


def tearDownModule():
    REL.tearDownModule()


def _b64(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


class QBox(REL.Box):
    """多兩件:進階授權 + 本艙佇列開關(queue=True 才開)。"""

    def __init__(self, tc, queue=True, **kw):
        super().__init__(tc, **kw)
        # 假 tmux 多一個模式 moving_after:前 FAKE_CAP_STATIC_N 幀相同(入列前的判忙看到「閒」)、之後每幀不同
        (self.bin / "tmux").write_text(REL.FAKE_TMUX.replace(
            'if os.environ.get("FAKE_CAP_MODE", "static") == "moving":',
            'if os.environ.get("FAKE_CAP_MODE", "static") == "moving" or (os.environ.get("FAKE_CAP_MODE") == '
            '"moving_after" and n >= int(os.environ.get("FAKE_CAP_STATIC_N", "0"))):', 1))
        sec = self.aib_home / "secrets"
        sec.mkdir(mode=0o700)
        os.chmod(sec, 0o700)
        lic = "%s.%s.draftsig" % (_b64({"alg": "none"}), _b64({"plan": "advanced"}))
        f = sec / "ez2.env"
        f.write_text("EZ2_LICENSE=%s\n" % lic)
        os.chmod(f, 0o600)
        st = self.aib_home / "instances" / "t1" / "state"
        st.mkdir(parents=True, exist_ok=True)
        (st / "dm-queue-policy.json").write_text(json.dumps(
            {"schema": "aib-dm-queue-policy/v1", "enabled": bool(queue)}))

    def py(self, code, **envkw):
        """在這個沙箱的環境裡跑一段 python(import 暫存根的 dm_queue);回最後一行 JSON。"""
        head = "import sys, json, os; sys.path.insert(0, %r)\nimport dm_queue as Q\nenv = dict(os.environ)\n" % (
            str(REL.TREE / "src" / "cockpit"))
        p = subprocess.run([sys.executable, "-c", head + code], capture_output=True, text=True,
                           env=self.env(**envkw), timeout=120, cwd=str(self.dir))
        self.tc.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        lines = [l for l in p.stdout.splitlines() if l.strip()]
        return json.loads(lines[-1]) if lines else None

    def dq(self, *args, stdin=None):
        return subprocess.run([sys.executable, str(REL.TREE / "src" / "cockpit" / "dm_queue.py")] + list(args),
                              input=stdin, capture_output=True, text=True, env=self.env(), timeout=60)

    def held(self):
        return [i for i in self.queue_items() if i.get("status") == "hold_human"]


def result_kv(line):
    parts = REL.result_fields(line)
    return dict(p.split("=", 1) for p in parts if "=" in p)


# ── G7-1:重送仍 2 ⇒ 排進佇列,不轉人審 ───────────────────────────────────────────────

class G7ResendBlockedGoesToQueue(unittest.TestCase):

    def test_resend_still_rc2_is_sort_queued_not_hold(self):
        b = QBox(self)
        b.rc_seq("4 4")                       # 兩輪都被擋(引擎 rc 4 = BIND 或卡稿),兩幀相同 ⇒ 確定零進框
        p = b.run(["@abcde", "hello g7 queue"], FAKE_CAP_MODE="static", AIB_DM_AUTO_RESEND="1")
        self.assertEqual(len(b.exec_calls()), 2, p.stdout + p.stderr)
        self.assertEqual(b.held(), [], "確定零進框的信不得轉人審:\n" + p.stdout + p.stderr)
        q = [i for i in b.queue_items() if i.get("status") == "queued"]
        self.assertEqual(len(q), 1, p.stdout + p.stderr)
        self.assertEqual(q[0].get("attempts"), 1, "重送那一次要算進 attempts")
        self.assertEqual(p.returncode, 0, "排進佇列 = 受理(stage=sort-queued 的既有契約)\n" + p.stdout + p.stderr)
        kv = result_kv(REL.last_line(p))
        self.assertEqual(kv.get("stage"), "sort-queued", REL.last_line(p))
        self.assertEqual(kv.get("id"), q[0]["id"], REL.last_line(p))
        self.assertEqual(kv.get("then"), "queued", REL.last_line(p))
        self.assertEqual(kv.get("resend"), "1", REL.last_line(p))
        self.assertEqual(REL.rc_contract(p)["stage"], "sort-queued", p.stdout)

    def test_drainer_counts_on_from_attempts_one_and_holds_at_max(self):
        b = QBox(self)
        b.rc_seq("4 4")
        b.run(["@abcde", "hello g7 drain"], FAKE_CAP_MODE="static", AIB_DM_AUTO_RESEND="1")
        out = b.py(
            "res = []\n"
            "for _ in range(4):\n"
            "    Q.drain_once(env=env, hold_fn=lambda it: (False, 'idle'), deliver_fn=lambda it: 2)\n"
            "    it = [i for i in Q._all_items(%r, env)][0]\n"
            "    res.append([it.get('status'), it.get('attempts'), it.get('hold_why') or ''])\n"
            "print(json.dumps(res))\n" % SEAT)
        self.assertEqual(out[0][:2], ["queued", 2], out)
        self.assertEqual(out[1][0], "hold_human", "滿 RESEND_MAX_ATTEMPTS(3)才轉人審:%r" % out)
        self.assertIn("3", out[1][2], "轉人審的理由要記第 3 次:%r" % out)

    def test_queue_off_still_hold_and_says_no_notice(self):
        b = QBox(self, queue=False)
        b.rc_seq("4 4")
        p = b.run(["@abcde", "hello g7 off"], FAKE_CAP_MODE="static", AIB_DM_AUTO_RESEND="1")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        h = b.held()
        self.assertEqual(len(h), 1, p.stdout + p.stderr)
        self.assertEqual(h[0].get("attempts"), 1)
        kv = result_kv(REL.last_line(p))
        self.assertEqual(kv.get("stage"), "blocked", REL.last_line(p))
        self.assertEqual(kv.get("then"), "hold-human", REL.last_line(p))
        self.assertEqual(kv.get("notify"), "none", "本艙沒開佇列 ⇒ 不會有提示,不得說會:" + REL.last_line(p))

    def test_rc4_unconfirmed_stays_hold_human_even_with_queue(self):
        b = QBox(self)
        b.rc_seq("4")
        p = b.run(["@abcde", "hello g7 rc4"], FAKE_CAP_MODE="moving_after", FAKE_CAP_STATIC_N="2",
                  AIB_DM_AUTO_RESEND="1")
        self.assertEqual(p.returncode, 4, p.stdout + p.stderr)
        self.assertEqual(len(b.held()), 1, "rc 4 可能已進框,照舊轉人審")
        self.assertEqual([i for i in b.queue_items() if i.get("status") == "queued"], [])
        kv = result_kv(REL.last_line(p))
        self.assertEqual(kv.get("then"), "hold-human", REL.last_line(p))
        self.assertEqual(kv.get("notify"), "on-idle", REL.last_line(p))
        self.assertNotIn("resend", kv)

    def test_first_round_rc2_without_resend_unchanged(self):
        """沒開自動重送(dm-ctl 直接叫):第一輪 rc 2 照舊不記、不入列,RESULT 不多鍵。"""
        b = QBox(self)
        b.rc_seq("4")
        p = b.run(["@abcde", "hello g7 plain"], FAKE_CAP_MODE="static")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertEqual(b.queue_items(), [])
        kv = result_kv(REL.last_line(p))
        self.assertNotIn("then", kv, REL.last_line(p))


# ── G7-2:人審要讓收件席知道 ──────────────────────────────────────────────────────────

HOLD_SETUP = (
    "def hold(msg, now):\n"
    "    return Q.record(%r, msg, '@77777', status=Q.HOLD_STATUS, rc=3, env=env, now=now,\n"
    "                    sender_kind='ai', wid='@2', provider='claude', hub='aihub-t1')['id']\n"
    "sent = []\n"
    "def deliver(item):\n"
    "    sent.append({'id': item['id'], 'msg': item['msg'],\n"
    "                 'proof': Q.sort_deliver_proof(item['seat'], item['id'], env),\n"
    "                 'wake_id': Q._dmctl_env(item, env).get('AIB_DM_SORT_WAKE_ID')})\n"
    "    return RC[0]\n"
    "RC = [0]\n"
    "BUSY = [False]\n"
    "def hold_fn(seat, row):\n"
    "    return (BUSY[0], 'busy' if BUSY[0] else 'idle')\n"
    "def tick(now):\n"
    "    return Q.triage_tick(env=env, now=now, hold_fn=hold_fn, deliver_fn=deliver, force=True)\n"
) % SEAT


class G7HoldNotice(unittest.TestCase):

    def test_new_hold_notified_once_when_idle(self):
        b = QBox(self)
        out = b.py(HOLD_SETUP + (
            "t = int(__import__('time').time())\n"
            "hold('m1', t); hold('m2', t)\n"
            "tick(t + 10)\n"
            "n1 = len(sent)\n"
            "tick(t + 200)\n"
            "n2 = len(sent)\n"
            "hold('m3', t + 300)\n"
            "tick(t + 400)\n"
            "print(json.dumps({'n1': n1, 'n2': n2, 'sent': sent}))\n"))
        self.assertEqual(out["n1"], 1, "有新的人審信、席閒著 ⇒ 貼一句:%r" % out)
        self.assertEqual(out["n2"], 1, "同一批不提示第二次:%r" % out)
        self.assertEqual(len(out["sent"]), 2, "新增一封人審 ⇒ 再提示一次:%r" % out)
        first = out["sent"][0]
        self.assertIn("aib dm-queue list --seat abcde", first["msg"])
        self.assertIn("2", first["msg"])
        self.assertIn("3", out["sent"][1]["msg"])
        self.assertTrue(first["proof"], "投遞當下要有排空器模式的證據(不然 dm-ctl 照一般 dm 送、會入列 / 記帳)")
        self.assertEqual(first["wake_id"], first["id"])
        self.assertTrue(first["id"].startswith("holdnote_"), first)

    def test_busy_seat_waits_then_notified(self):
        b = QBox(self)
        out = b.py(HOLD_SETUP + (
            "t = int(__import__('time').time())\n"
            "hold('m1', t)\n"
            "BUSY[0] = True\n"
            "tick(t + 10)\n"
            "n1 = len(sent)\n"
            "BUSY[0] = False\n"
            "tick(t + 100)\n"
            "print(json.dumps({'n1': n1, 'n2': len(sent)}))\n"))
        self.assertEqual(out, {"n1": 0, "n2": 1})

    def test_rc2_notice_retried_next_tick(self):
        b = QBox(self)
        out = b.py(HOLD_SETUP + (
            "t = int(__import__('time').time())\n"
            "hold('m1', t)\n"
            "RC[0] = 2\n"
            "tick(t + 10)\n"
            "RC[0] = 0\n"
            "tick(t + 100)\n"
            "tick(t + 200)\n"
            "proof_after = Q.sort_deliver_proof(%r, sent[-1]['id'], env)\n"
            "print(json.dumps({'n': len(sent), 'proof_after': proof_after}))\n" % SEAT))
        self.assertEqual(out["n"], 2, "rc 2 = 一個字都沒進 ⇒ 下一拍再提示一次,之後不再:%r" % out)
        self.assertFalse(out["proof_after"], "寫回之後證據就失效(不得拿同一個 id 再冒排空器)")

    def test_notice_text_four_locales(self):
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            s = d.get("aib.dmqueue.hold_notice")
            self.assertTrue(s, loc)
            self.assertIn("{n}", s, loc)
            self.assertIn("aib dm-queue list --seat {seat}", s, loc)
            for k in ("aib.dmctl.resend_queued", "aib.dmctl.hold_notify_idle", "aib.dmctl.hold_notify_none"):
                self.assertTrue(d.get(k), "%s %s" % (loc, k))

    def test_notice_end_to_end_through_dmctl(self):
        """真的走 _deliver_via_dmctl → dm-ctl(假咽喉):貼進去的是那一句,而且不入列、不記一封新信。"""
        b = QBox(self)
        out = b.py(
            "t = int(__import__('time').time())\n"
            "Q.record(%r, 'm1', '@77777', status=Q.HOLD_STATUS, rc=3, env=env, now=t, sender_kind='ai',\n"
            "         wid='@2', provider='claude', hub='aihub-t1')\n"
            "res = Q.triage_tick(env=env, now=t + 10, hold_fn=lambda s, r: (False, 'idle'), force=True)\n"
            "print(json.dumps({'res': res, 'n_items': len(Q._all_items(%r, env))}))\n" % (SEAT, SEAT))
        bodies = [REL.throat_body(c) for c in b.exec_calls()]
        self.assertEqual(len(bodies), 1, "%r\n%r" % (out, b.calls()))
        self.assertIn("dm-queue list --seat abcde", bodies[0])
        self.assertEqual(out["n_items"], 1, "提示那一句不得變成佇列裡的一封信:%r" % out)


# ── G7-4:既有流程不受影響 ───────────────────────────────────────────────────────────

class G7ExistingFlows(unittest.TestCase):

    def test_list_and_read_done_with_notice_state(self):
        b = QBox(self)
        ids = b.py(HOLD_SETUP + (
            "t = int(__import__('time').time())\n"
            "h = hold('m1', t)\n"
            "d = Q.record(%r, 'delivered one', '@77777', status='delivered', rc=0, env=env, now=t,\n"
            "             sender_kind='ai')['id']\n"
            "tick(t + 10)\n"
            "print(json.dumps({'h': h, 'd': d, 'n': len(sent)}))\n" % SEAT))
        self.assertEqual(ids["n"], 1)
        p = b.dq("list", "--seat", SEAT)
        self.assertEqual(p.returncode, 0, p.stderr)
        p = b.dq("read", "--seat", SEAT, "--id", ids["d"], "--by", "seat:%s" % SEAT, "--no-rev-check",
                 "--done", "--why", "g7 test")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(json.loads(p.stdout.strip().splitlines()[-1]).get("ok"), p.stdout)
        p = b.dq("requeue", "--seat", SEAT, "--id", ids["h"], "--by", "human:andy")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
