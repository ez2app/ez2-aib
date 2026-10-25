#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""新版 Claude Code 背景工作(備用行程認領,命令列沒有 --fork-session)的活動要併回原席(10-25 Andy:
「你明明就有在運作，為什麼右方ai欄上還是 zzz … 💤49m」)。
~/.claude/sessions/<pid>.json:原席 kind=interactive + parkedJobId,分身 kind=bg + 同一個 jobId。
P1 parkedJobId ↔ jobId 配對 ⇒ forks_by_origin 有 原席 → 分身(命令列一支分身都沒有也一樣)
P2 activity_age:原席對話檔 49 分鐘沒動、分身剛寫 ⇒ 閒置秒數照分身算(不是 💤49m)
P3 形狀不對(不是 uuid、kind 不對、jobId 對不上、壞 JSON、超大檔)⇒ 不併
P4 舊的命令列分身照舊認得(兩種來源合併、不重複)
其他品牌(Andy 10-25「其他品牌的ai也要能辨識」;調查:Codex 子代理首行 session_id 就是原席 ⇒ 原本就算得到;
Antigravity / Copilot 這台沒有任何子代理資料 ⇒ 不做):
G1 grok 原席 updates.jsonl 有 subagent_spawned 沒 finished ⇒ 子代理目錄(另一個 cwd)的活動併進原席
G2 finished 了 / child id 不是 uuid ⇒ 不併
G3 尾巴(512 KiB)外的舊 spawned 不算(尾巴大小鎖住;少併是可接受方向)
F1 sessions/*.json 或 grok updates.jsonl 是具名管道 ⇒ 不卡住、跳過(r1 審查 B-M1)
刀(複本 + run_guarded):K1 rows 路徑不併 parked ⇒ P1 紅;K2 不驗 uuid ⇒ P3 紅;K3 grok 不看子代理 ⇒ G1 紅;
K4 finished 不扣掉 ⇒ G2 紅;K5 快取路徑不併 parked ⇒ P2 紅;K6 開檔會阻塞 ⇒ F1 紅
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

import bg_fork  # noqa: E402
import activity_probe  # noqa: E402
import grok_ctx_probe  # noqa: E402

ORIGIN = "f258c85d-514a-4077-8358-4145bd4abb89"
FORK = "8fed2a66-b884-41d9-bd8e-14841021ed58"
OLD_FORK = "11111111-2222-4333-8444-555555555555"
OLD_ORIGIN = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-bgpark-"))
        self.sessions = self.td / "sessions"
        self.sessions.mkdir()
        self.proj = self.td / "projects" / "-x"
        self.proj.mkdir(parents=True)
        self.snap = self.td / "ps.txt"
        self.snap.write_text("1 0 /sbin/launchd\n", encoding="utf-8")
        env = {bg_fork.SESSIONS_ENV: str(self.sessions), bg_fork.SNAPSHOT_ENV: str(self.snap)}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        bg_fork._cache["map"] = None
        self.addCleanup(lambda: bg_fork._cache.update(map=None))

    def sess(self, pid, data):
        (self.sessions / ("%d.json" % pid)).write_text(
            data if isinstance(data, str) else json.dumps(data), encoding="utf-8")

    def pair(self):
        self.sess(17631, {"pid": 17631, "sessionId": ORIGIN, "kind": "interactive", "parkedJobId": "8fed2a66"})
        self.sess(31171, {"pid": 31171, "sessionId": FORK, "kind": "bg", "jobId": "8fed2a66"})


class Parked(Base):
    def test_p1_pairs_by_job_id(self):
        self.pair()
        self.assertEqual(bg_fork.forks_by_origin(), {ORIGIN: [FORK]})
        self.assertEqual(bg_fork.forks_by_origin(rows=[]), {ORIGIN: [FORK]})

    def test_p2_activity_follows_fork(self):
        self.pair()
        now = time.time()
        o, f = self.proj / (ORIGIN + ".jsonl"), self.proj / (FORK + ".jsonl")
        o.write_text("{}\n", encoding="utf-8")
        f.write_text("{}\n", encoding="utf-8")
        os.utime(o, (now - 49 * 60, now - 49 * 60))
        os.utime(f, (now - 5, now - 5))
        age = activity_probe.activity_age("claude", ORIGIN, now=now, proj=str(self.proj))
        self.assertIsNotNone(age)
        self.assertLess(age, 60, "分身剛寫字,原席不該算閒置 49 分鐘")

    def test_p3_bad_shapes_not_merged(self):
        self.sess(1, {"sessionId": "not-a-uuid", "kind": "interactive", "parkedJobId": "j1"})
        self.sess(2, {"sessionId": FORK, "kind": "bg", "jobId": "j1"})
        self.sess(3, {"sessionId": ORIGIN, "kind": "interactive", "parkedJobId": "j2"})
        self.sess(4, {"sessionId": "../../etc", "kind": "bg", "jobId": "j2"})
        self.sess(5, {"sessionId": OLD_ORIGIN, "kind": "bg", "parkedJobId": "j3"})
        self.sess(6, {"sessionId": OLD_FORK, "kind": "interactive", "jobId": "j3"})
        self.sess(7, "{broken")
        self.sess(8, "[1, 2]")
        self.sess(9, {"sessionId": ORIGIN, "kind": "interactive", "parkedJobId": "j4"})
        self.sess(10, json.dumps({"sessionId": FORK, "kind": "bg", "jobId": "j4", "pad": "x" * 70000}))
        self.sess(11, {"sessionId": ORIGIN, "kind": "interactive", "parkedJobId": "j5"})
        self.sess(12, {"sessionId": ORIGIN, "kind": "bg", "jobId": "j5"})
        self.assertEqual(bg_fork.forks_by_origin(), {})

    def test_p4_cmdline_forks_still_work(self):
        self.pair()
        cmd = ("/Users/u/.local/share/claude/versions/2.1.296 --session-id %s --fork-session "
               "--resume /Users/u/.claude/projects/-x/%s.jsonl" % (OLD_FORK, OLD_ORIGIN))
        self.snap.write_text("500 1 %s\n" % cmd, encoding="utf-8")
        self.assertEqual(bg_fork.forks_by_origin(), {OLD_ORIGIN: [OLD_FORK], ORIGIN: [FORK]})
        self.sess(40, {"sessionId": OLD_FORK, "kind": "bg", "jobId": "j9"})
        self.sess(41, {"sessionId": OLD_ORIGIN, "kind": "interactive", "parkedJobId": "j9"})
        bg_fork._cache["map"] = None
        self.assertEqual(bg_fork.forks_by_origin()[OLD_ORIGIN], [OLD_FORK])


GROK_SEAT = "9f40c89a-5886-4964-86bf-36245109bb62"
GROK_KID = "01a08b6a-1111-4222-8333-444444444444"


class Grok(Base):
    def setUp(self):
        super().setUp()
        self.root = self.td / "grok"
        p = mock.patch.dict(os.environ, {"EZ2_GROK_SESSIONS_DIR": str(self.root)})
        p.start()
        self.addCleanup(p.stop)
        self.seat = self.root / "%2FUsers%2Fu%2Frepo" / GROK_SEAT
        self.kid = self.root / "%2FUsers%2Fu%2Fworktree" / GROK_KID
        self.seat.mkdir(parents=True)
        self.kid.mkdir(parents=True)
        self.now = time.time()
        for d, age in ((self.seat, 49 * 60), (self.kid, 5)):
            f = d / "updates.jsonl" if d is self.kid else d / "chat_history.jsonl"
            f.write_text("{}\n", encoding="utf-8")
            os.utime(f, (self.now - age, self.now - age))

    def updates(self, *events):
        lines = [json.dumps({"method": "_x.ai/session/update", "params": {"sessionId": GROK_SEAT, "update": e}})
                 for e in events]
        f = self.seat / "updates.jsonl"
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.utime(f, (self.now - 49 * 60, self.now - 49 * 60))

    def age(self):
        return activity_probe.activity_age("grok", GROK_SEAT, now=self.now)

    def test_g1_running_subagent_counts(self):
        self.updates({"sessionUpdate": "subagent_spawned", "child_session_id": GROK_KID,
                      "parent_session_id": GROK_SEAT})
        self.assertLess(self.age(), 60, "grok 子代理剛寫字,原席不該算閒置 49 分鐘")

    def test_g2_finished_or_bad_not_merged(self):
        self.updates({"sessionUpdate": "subagent_spawned", "child_session_id": GROK_KID},
                     {"sessionUpdate": "subagent_finished", "child_session_id": GROK_KID, "status": "completed"},
                     {"sessionUpdate": "subagent_spawned", "child_session_id": "../%2FUsers%2Fu%2Fworktree"})
        self.assertGreater(self.age(), 40 * 60)
        self.assertEqual(grok_ctx_probe.running_children(str(self.seat)), [])

    def test_g3_old_spawn_outside_tail(self):
        self.updates({"sessionUpdate": "subagent_spawned", "child_session_id": GROK_KID})
        f = self.seat / "updates.jsonl"
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(("{\"x\": \"%s\"}\n" % ("y" * 1000)) * 600)
        os.utime(f, (self.now - 49 * 60, self.now - 49 * 60))
        self.assertEqual(grok_ctx_probe.running_children(str(self.seat)), [])


FIFO_PROBE = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
import bg_fork, grok_ctx_probe
print(bg_fork.parked_forks(sys.argv[2]), grok_ctx_probe.running_children(sys.argv[3]))
"""


class Fifo(Base):
    def test_f1_fifo_skipped(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("no mkfifo")
        self.pair()
        os.mkfifo(str(self.sessions / "999.json"))
        g = self.td / "gseat"
        g.mkdir()
        os.mkfifo(str(g / "updates.jsonl"))
        r = subprocess.run([sys.executable, "-B", "-c", FIFO_PROBE, str(ROOT / "src" / "cockpit"),
                            str(self.sessions), str(g)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(FORK, r.stdout)
        self.assertTrue(r.stdout.strip().endswith("[]"), r.stdout)


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(ROOT, "src/cockpit/bg_fork.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), cell], cwd=str(root),
                            max_procs=40, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s" % r.tripped)
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k1_no_parked_merge(self):
        self._knife("    return _merge(_build(rows), parked_forks())\n", "    return _build(rows)\n",
                    "Parked.test_p1_pairs_by_job_id")

    def test_k2_no_uuid_check(self):
        self._knife("if not isinstance(sid, str) or not UUID_RE.match(sid):",
                    "if not isinstance(sid, str):", "Parked.test_p3_bad_shapes_not_merged")

    def test_k3_grok_ignores_children(self):
        name = Path(__file__).name
        with knife_copy(ROOT, "src/cockpit/grok_ctx_probe.py", "    for kid in running_children(folder):\n",
                        "    for kid in []:\n", tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), "Grok.test_g1_running_subagent_counts"],
                            cwd=str(root), max_procs=40, timeout=120)
        self.assertIsNone(r.tripped)
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k4_finished_not_popped(self):
        self._knife_file("src/cockpit/grok_ctx_probe.py", "            live.pop(kid, None)\n", "            pass\n",
                         "Grok.test_g2_finished_or_bad_not_merged")

    def test_k5_cached_path_no_merge(self):
        self._knife_file("src/cockpit/bg_fork.py", "        result = _merge(_build(rows), parked_forks())\n",
                         "        result = _build(rows)\n", "Parked.test_p2_activity_follows_fork")

    def test_k6_no_regular_check(self):
        # 不阻塞地開才是不卡的關鍵(S_ISREG 再把管道 / 裝置擋掉);拿掉 O_NONBLOCK ⇒ open 卡住 ⇒ F1 逾時紅
        self._knife_file("src/cockpit/bg_fork.py", "os.open(path, os.O_RDONLY | os.O_NONBLOCK)",
                         "os.open(path, os.O_RDONLY)", "Fifo.test_f1_fifo_skipped", red="TimeoutExpired")

    def _knife_file(self, rel, needle, repl, cell, red="AssertionError"):
        name = Path(__file__).name
        with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), cell], cwd=str(root),
                            max_procs=40, timeout=120)
        self.assertIsNone(r.tripped)
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn(red, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
