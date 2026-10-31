#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""待審整理那一拍「為什麼不叫」要留痕(10-30 @aeb40:一席 32 封、叫醒紀錄停在兩天前,查不到原因)。

Y1 純函式(兩個檔都 0600):原因第一次出現 ⇒ 寫一列;同一原因再一拍 ⇒ 不寫;換原因 ⇒ 再寫;上一拍有、這拍沒列 ⇒ 記「-」(回到門檻下)
Y2 holdnote 列不算;紀錄超過上限 ⇒ 只留後半
Y4 同因再一拍 ⇒ observed_at 前進、since 不動(同因兩天 ≠ 兩天沒掃)
Y5 較舊的拍子(now < observed_at)⇒ 不蓋最新狀態、不寫 .jsonl
Y6 檔是合法 JSON 但項目型別壞 ⇒ 壞項丟掉、這拍重建
Y7 別的行程持鎖不放 ⇒ 這一拍不留痕、限時內回來(回 None),不寫檔;放鎖後下一拍照寫(Codex r2 Medium)
Y8 時間欄位壞(陣列 / 物件 / 浮點 1e100 / 比現在晚太多)⇒ 當壞項重建,正常拍照寫(Codex r2 Low)
Y9 從沒到過門檻的席 ⇒ 檔裡沒有那一席(不是「-」);到過又回門檻下才記「-」(Codex r2 M2 補測)
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
        self.assertEqual(self.last()["aaaaa"], {"why": Q.TRIAGE_WHY_IDLE, "since": NOW + 180, "observed_at": NOW + 240})
        root = Q.queue_root(self.env)
        for name in (Q.TRIAGE_WHY_LAST, Q.TRIAGE_WHY_LOG):
            self.assertEqual((root / name).stat().st_mode & 0o777, 0o600, name)

    def test_Y4_observed_at_moves_since_stays(self):
        self.log([{"seat": "aaaaa", "woke": False, "why": "load-high"}])
        self.log([{"seat": "aaaaa", "woke": False, "why": "load-high"}], NOW + 3600)
        self.assertEqual(self.last()["aaaaa"], {"why": "load-high", "since": NOW, "observed_at": NOW + 3600})

    def test_Y5_older_tick_does_not_overwrite(self):
        self.log([{"seat": "aaaaa", "woke": False, "why": "busy"}], NOW + 120)
        self.assertEqual(self.log([{"seat": "aaaaa", "woke": False, "why": "load-high"}], NOW + 60), [])
        self.assertEqual(self.last()["aaaaa"]["why"], "busy")
        self.assertEqual(self.last()["aaaaa"]["since"], NOW + 120)
        self.assertEqual([l["why"] for l in self.lines()], ["busy"])

    def test_Y6_bad_entries_rebuilt(self):
        (Q.queue_root(self.env) / Q.TRIAGE_WHY_LAST).write_text(
            json.dumps({"aaaaa": "bad", "bbbbb": {"why": 3}, "ccccc": [1]}), encoding="utf-8")
        got = self.log([{"seat": "aaaaa", "woke": False, "why": "load-high"}])
        self.assertEqual([(c["seat"], c["why"]) for c in got], [("aaaaa", "load-high")])
        self.assertEqual(set(self.last()), {"aaaaa"})

    def test_Y7_lock_held_skips_tick(self):
        import fcntl
        import threading
        import time as _t
        lock_p = Q.queue_root(self.env) / "triage-why.lock"
        holder = os.open(str(lock_p), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(holder, fcntl.LOCK_EX)
        out = {}

        def run():
            t0 = _t.monotonic()
            out["r"] = self.log([{"seat": "aaaaa", "woke": False, "why": "load-high"}])
            out["dt"] = _t.monotonic() - t0
        th = threading.Thread(target=run, daemon=True)
        try:
            th.start()
            th.join(timeout=5)
            self.assertFalse(th.is_alive(), "持鎖時留痕卡住沒回來")
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            os.close(holder)
        th.join(timeout=5)
        self.assertIsNone(out.get("r"))
        self.assertLess(out["dt"], 3)
        self.assertFalse((Q.queue_root(self.env) / Q.TRIAGE_WHY_LAST).exists())
        self.assertEqual(self.lines(), [])
        got = self.log([{"seat": "aaaaa", "woke": False, "why": "load-high"}], NOW + 60)
        self.assertEqual([c["why"] for c in got], ["load-high"])

    def test_Y8_bad_time_fields_rebuilt(self):
        root = Q.queue_root(self.env)
        (root / Q.TRIAGE_WHY_LAST).write_text(json.dumps({
            "aaaaa": {"why": "busy", "since": [], "observed_at": {}},
            "bbbbb": {"why": "busy", "since": 1, "observed_at": 1e100},
            "ccccc": {"why": "busy", "since": NOW, "observed_at": NOW + 10 * 86400},
            "ddddd": {"why": "busy", "since": True, "observed_at": NOW}}), encoding="utf-8")
        rows = [{"seat": s, "woke": False, "why": "busy"} for s in ("aaaaa", "bbbbb", "ccccc", "ddddd")]
        got = self.log(rows, NOW + 60)
        self.assertEqual(sorted(c["seat"] for c in got), ["aaaaa", "bbbbb", "ccccc", "ddddd"])
        for s in ("aaaaa", "bbbbb", "ccccc", "ddddd"):
            self.assertEqual(self.last()[s], {"why": "busy", "since": NOW + 60, "observed_at": NOW + 60}, s)

    def test_Y9_never_at_threshold_not_recorded(self):
        self.log([{"seat": "aaaaa", "woke": False, "why": "load-high"}])
        self.log([{"seat": "aaaaa", "woke": False, "why": "load-high"}], NOW + 60)
        self.assertNotIn("bbbbb", self.last())
        self.log([], NOW + 120)
        self.assertEqual(self.last()["aaaaa"]["why"], Q.TRIAGE_WHY_IDLE)
        self.assertNotIn("bbbbb", self.last())

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
