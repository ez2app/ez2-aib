#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""系統錨定驗單第 2 張:鎖屏看系統日誌(規格 SPEC-anchored-grant-verify-e339d-1029,Andy 10-29 21:1x 裁 Q2 上限 3 秒)。

approve_grants.syslog_locks 用假 Popen + 假時鐘驗(不跑真的 log show、不碰 Secure Enclave、不彈任何視窗):
S1 切段:24 小時 ⇒ 8 段並行(/usr/bin/log 絕對路徑、predicate 綁 loginwindow 路徑、env 只給 PATH、stdin 關掉、段與段接起來);
   1 小時 ⇒ 1 段;超過 24 小時 ⇒ log_span(不查);時鐘倒退 ⇒ log_clock
S2 解析:只收 processImagePath = loginwindow 且有 eventMessage 的那幾列;開頭的 Filtering… / 結尾摘要 / 別的行程略過;
   timestamp 讀不懂 ⇒ log_unreadable
S3 失敗一律當鎖過:rc ≠ 0 ⇒ log_failed;超過 3 秒 ⇒ log_timeout,沒跑完的子行程都收掉;Popen 起不來 ⇒ log_failed
S4 不是 Mac ⇒ log_not_mac,一支都不跑;Ctx 預設走 syslog_locks(帶 Ctx 的 platform)
刀(複本落刀,錨要恰好一次):KS1 拿掉 24 小時上限 ⇒ S1 紅;KS2 拿掉 rc 檢查 ⇒ S3 紅;KS3 communicate 不帶 timeout ⇒ S3 紅;
  KS4 拿掉收子行程 ⇒ S3 紅;KS5 拿掉平台檢查 ⇒ S4 紅;KS6 子行程不給乾淨 env ⇒ S1 紅。
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "cockpit"
sys.path.insert(0, str(SRC))
os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
import approve_grants as GR  # noqa: E402

NOW = 1798560000                     # 固定時間點(秒)
LW = GR.LOGINWINDOW


def _line(ts, path=LW, msg="-[LWScreenLock startUnlock:] | entered"):
    return json.dumps({"timestamp": ts, "processImagePath": path, "eventMessage": msg}).encode()


def _ts(sec):
    """秒 ⇒ log show ndjson 的 timestamp 字串(本地時區)。"""
    lt = time.localtime(sec)
    off = time.strftime("%z", lt)
    return time.strftime("%Y-%m-%d %H:%M:%S", lt) + ".123456" + off


class Fake:
    """假 Popen 工廠。outs[i] = 第 i 支的輸出;delays[i] = 它要花的秒數(推進假時鐘);rc 一律。"""

    def __init__(self, outs=None, delays=None, rc=0, boom=False):
        self.outs, self.delays, self.rc, self.boom = outs or {}, delays or {}, rc, boom
        self.clock = [100.0]
        self.procs = []

    def now(self):
        return self.clock[0]

    def __call__(self, argv, **kw):
        if self.boom:
            raise OSError("no log")
        p = _Proc(self, len(self.procs), argv, kw)
        self.procs.append(p)
        return p


class _Proc:
    def __init__(self, f, i, argv, kw):
        self.f, self.i, self.argv, self.kw = f, i, argv, kw
        self.done, self.killed, self.returncode = False, False, None

    def communicate(self, timeout=None):
        d = self.f.delays.get(self.i, 0.1)
        if timeout is not None and d > timeout:
            self.f.clock[0] += timeout
            raise subprocess.TimeoutExpired(self.argv, timeout)
        self.f.clock[0] += d
        self.done, self.returncode = True, self.f.rc
        return b"\n".join(self.f.outs.get(self.i, [])), None

    def poll(self):
        return self.returncode if self.done else None

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.returncode


def run(M, f, since, now=NOW, platform="darwin"):
    return M.syslog_locks(since, now, popen=f, platform=platform, clock=f.now)


def cell_s1(t, M):
    f = Fake()
    t.assertEqual(run(M, f, NOW - 24 * 3600), ([], None))
    t.assertEqual(len(f.procs), 8)
    for p in f.procs:
        a = p.argv
        t.assertEqual(a[:2], ["/usr/bin/log", "show"])
        t.assertEqual(a[a.index("--style") + 1], "ndjson")
        pred = a[a.index("--predicate") + 1]
        t.assertEqual(pred, M.LOCK_PREDICATE)
        t.assertIn('processImagePath == "%s"' % LW, pred)
        t.assertEqual(p.kw.get("env"), {"PATH": "/usr/bin:/bin"})
        t.assertEqual(p.kw.get("stdin"), subprocess.DEVNULL)
    starts = [p.argv[p.argv.index("--start") + 1] for p in f.procs]
    ends = [p.argv[p.argv.index("--end") + 1] for p in f.procs]
    t.assertEqual(starts[0], M._log_local(NOW - 24 * 3600))
    t.assertEqual(ends[-1], M._log_local(NOW + 1))
    for e, s in zip(ends, starts[1:]):                     # 段與段重疊 1 秒(不漏)
        t.assertGreaterEqual(e, s)
    f = Fake()
    t.assertEqual(run(M, f, NOW - 3600), ([], None))
    t.assertEqual(len(f.procs), 1)
    f = Fake()
    t.assertEqual(run(M, f, NOW - 24 * 3600 - 1), (None, "log_span"))
    t.assertEqual(f.procs, [])
    t.assertEqual(run(M, Fake(), NOW + 5), (None, "log_clock"))


def cell_s2(t, M):
    lock = NOW - 1200
    f = Fake(outs={0: [b"Filtering the log data using \"processImagePath == ...\"",
                       _line(_ts(lock)),
                       _line(_ts(lock + 1), path="/Users/x/loginwindow"),           # 別的行程(名字一樣也不算)
                       json.dumps({"count": 3, "finished": 1}).encode(), b""]})
    t.assertEqual(run(M, f, NOW - 3600), ([lock], None))
    f = Fake(outs={0: [_line("yesterday-ish")]})
    t.assertEqual(run(M, f, NOW - 3600), (None, "log_unreadable"))


def cell_s3(t, M):
    t.assertEqual(run(M, Fake(rc=1), NOW - 3600), (None, "log_failed"))
    f = Fake(delays={0: 5.0})                               # 一支就超過 3 秒
    t.assertEqual(run(M, f, NOW - 3600), (None, "log_timeout"))
    f = Fake(delays={0: 5.0})                               # 4 小時 = 2 段;第一段超時 ⇒ 兩支都收掉
    t.assertEqual(run(M, f, NOW - 4 * 3600), (None, "log_timeout"))
    t.assertEqual([p.killed for p in f.procs], [True, True])
    f = Fake(delays={i: 1.0 for i in range(8)})             # 每支 1 秒、依序累積 ⇒ 第 4 支前就到 3 秒
    t.assertEqual(run(M, f, NOW - 24 * 3600), (None, "log_timeout"))
    t.assertEqual(sum(p.killed for p in f.procs), 5)
    t.assertEqual(run(M, Fake(boom=True), NOW - 3600), (None, "log_failed"))


def cell_s4(t, M):
    f = Fake(boom=True)
    t.assertEqual(run(M, f, NOW - 3600, platform="linux"), (None, "log_not_mac"))
    ctx = M.Ctx(env=dict(os.environ), platform="linux")
    t.assertEqual(ctx.syslog(NOW - 60, NOW), (None, "log_not_mac"))
    t.assertEqual(M.lock_state(M.Ctx(env=dict(os.environ), platform="linux", screen_locked=False), NOW - 60),
                  (None, "log_not_mac"))


class Cells(unittest.TestCase):
    def test_s1(self): cell_s1(self, GR)
    def test_s2(self): cell_s2(self, GR)
    def test_s3(self): cell_s3(self, GR)
    def test_s4(self): cell_s4(self, GR)


KNIVES = [
    ("KS1", "    if n > LOG_SPANS_MAX:\n        return None, \"log_span\"\n", "", cell_s1),
    ("KS2", "            if p.returncode != 0:\n                return None, \"log_failed\"\n", "", cell_s3),
    ("KS3", "            out, _ = p.communicate(timeout=left)\n", "            out, _ = p.communicate()\n", cell_s3),
    ("KS4", "            if p.poll() is None:\n                p.kill()\n", "            if False:\n                p.kill()\n", cell_s3),
    ("KS5", "    if (platform or sys.platform) != \"darwin\":\n        return None, \"log_not_mac\"\n", "", cell_s4),
    ("KS6", "                               env={\"PATH\": \"/usr/bin:/bin\"}))\n", "                               ))\n", cell_s1),
]


class Knives(unittest.TestCase):
    def test_knives(self):
        src = (SRC / "approve_grants.py").read_text(encoding="utf-8")
        for name, anchor, repl, cell in KNIVES:
            with self.subTest(knife=name):
                self.assertEqual(src.count(anchor), 1, "%s 錨不是恰好一次" % name)
                td = tempfile.mkdtemp(prefix="aib-syslog-knife-")
                try:
                    p = os.path.join(td, "approve_grants.py")
                    with open(p, "w", encoding="utf-8") as f:
                        f.write(src.replace(anchor, repl))
                    spec = importlib.util.spec_from_file_location("aib_syslog_knife_" + name, p)
                    M = importlib.util.module_from_spec(spec)
                    sys.modules[spec.name] = M
                    spec.loader.exec_module(M)
                    with self.assertRaises(AssertionError, msg="%s 落刀後格子仍綠" % name):
                        cell(self, M)
                finally:
                    sys.modules.pop("aib_syslog_knife_" + name, None)
                    shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
