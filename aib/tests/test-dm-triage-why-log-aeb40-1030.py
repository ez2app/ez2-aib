#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""待審整理那一拍「為什麼不叫」要留痕(10-30 @aeb40:一席 32 封、叫醒紀錄停在兩天前,查不到原因)。

Y1 純函式:原因第一次出現 ⇒ 寫一列;同一原因再一拍 ⇒ 不寫;換原因 ⇒ 再寫;上一拍有、這拍沒列 ⇒ 記「-」(回到門檻下)
Y2 holdnote 列不算;紀錄超過上限 ⇒ 只留後半
Y3 真 triage_tick:待審到門檻、load1 太高 ⇒ triage-why.json 該席 = load-high,jsonl 一列;
   降溫後叫醒 ⇒ 換成 woke 再一列;紀錄檔壞掉 ⇒ 照樣叫醒(紀錄不得擋)
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401

ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
import dm_queue as Q  # noqa: E402

_spec = importlib.util.spec_from_file_location("dmhold_fixture", HERE / "test-dm-hold-notify-abb89-1015.py")
HN = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(HN)
SEAT = HN.SEAT
NOW = 1_800_000_000


def setUpModule():
    HN.setUpModule()


def tearDownModule():
    HN.tearDownModule()


class Pure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ, EZ2_AIB_STATE_ROOT=os.path.realpath(self.tmp.name))
        Q.queue_root(self.env).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def log(self, rows, now=NOW):
        return Q._triage_why_log(rows, self.env, now)

    def lines(self):
        p = Q.queue_root(self.env) / Q.TRIAGE_WHY_LOG
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def last(self):
        return json.loads((Q.queue_root(self.env) / Q.TRIAGE_WHY_LAST).read_text(encoding="utf-8"))

    def test_Y1_changes_only(self):
        self.assertEqual(len(self.log([{"seat": "aaaaa", "woke": False, "why": "load-high"}])), 1)
        self.assertEqual(self.log([{"seat": "aaaaa", "woke": False, "why": "load-high"}], NOW + 60), [])
        got = self.log([{"seat": "aaaaa", "woke": True, "why": "over-threshold"}], NOW + 120)
        self.assertEqual([c["why"] for c in got], ["woke"])
        got = self.log([], NOW + 180)
        self.assertEqual([c["why"] for c in got], [Q.TRIAGE_WHY_IDLE])
        self.assertEqual(self.log([], NOW + 240), [])
        self.assertEqual([l["why"] for l in self.lines()], ["load-high", "woke", Q.TRIAGE_WHY_IDLE])
        self.assertEqual(self.last()["aaaaa"], {"why": Q.TRIAGE_WHY_IDLE, "since": NOW + 180})

    def test_Y2_holdnote_ignored_and_trim(self):
        self.assertEqual(self.log([{"seat": "aaaaa", "holdnote": False, "why": "x"}]), [])
        old = Q.TRIAGE_WHY_LOG_MAX
        Q.TRIAGE_WHY_LOG_MAX = 400
        try:
            for i in range(40):
                self.log([{"seat": "aaaaa", "woke": False, "why": "w%d" % i}], NOW + i)
        finally:
            Q.TRIAGE_WHY_LOG_MAX = old
        p = Q.queue_root(self.env) / Q.TRIAGE_WHY_LOG
        self.assertLessEqual(p.stat().st_size, 400)
        self.assertEqual(self.lines()[-1]["why"], "w39")


SETUP = (
    "LOAD = [99.0]\n"
    "Q.LOAD_FN = lambda: (LOAD[0], 0.0, 0.0)\n"
    "def put(msg, now):\n"
    "    return Q.record(%r, msg, '@77777', status='delivered', rc=0, env=env, now=now,\n"
    "                    sender_kind='ai', wid='@2', provider='claude', hub='aihub-t1')['id']\n"
    "def tick(now):\n"
    "    return Q.triage_tick(env=env, now=now, hold_fn=lambda s, r: (False, 'idle'), deliver_fn=lambda it: 0, force=True)\n"
    "root = Q.queue_root(env)\n"
    "def last():\n"
    "    p = root / Q.TRIAGE_WHY_LAST\n"
    "    return json.loads(p.read_text()) if p.exists() else {}\n"
    "def n():\n"
    "    p = root / Q.TRIAGE_WHY_LOG\n"
    "    return len(p.read_text().splitlines()) if p.exists() else 0\n"
    "t = int(__import__('time').time())\n"
    "for i in range(6):\n"
    "    put('請看第 %%d 件事,要回覆' %% i, t - 600 + i)\n"
) % SEAT


class Tick(unittest.TestCase):
    def test_Y3_load_high_then_woke(self):
        out = HN.QBox(self).py(SETUP + (
            "r1 = tick(t)\n"
            "a = (last().get(%r) or {}).get('why'); na = n()\n"
            "tick(t + 61)\n"
            "nb = n()\n"
            "LOAD[0] = 0.0\n"
            "r3 = tick(t + 122)\n"
            "c = (last().get(%r) or {}).get('why'); nc = n()\n"
            "print(json.dumps({'a': a, 'na': na, 'nb': nb, 'c': c, 'nc': nc,\n"
            "                  'woke3': any(r.get('woke') for r in (r3 or []))}))\n") % (SEAT, SEAT))
        self.assertEqual(out["a"], "load-high")
        self.assertEqual(out["na"], 1)
        self.assertEqual(out["nb"], 1)              # 同一原因不再寫
        self.assertTrue(out["woke3"])
        self.assertEqual(out["c"], "woke")
        self.assertEqual(out["nc"], 2)

    def test_Y3_broken_log_does_not_block_wake(self):
        out = HN.QBox(self).py(SETUP + (
            "LOAD[0] = 0.0\n"
            "root.mkdir(parents=True, exist_ok=True)\n"
            "(root / Q.TRIAGE_WHY_LOG).mkdir()\n"       # 紀錄檔位置是資料夾 ⇒ 寫不進去
            "r = tick(t)\n"
            "print(json.dumps({'woke': any(x.get('woke') for x in (r or []))}))\n"))
        self.assertTrue(out["woke"])


if __name__ == "__main__":
    unittest.main()
