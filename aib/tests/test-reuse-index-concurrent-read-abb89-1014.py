#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""紀錄池「同時跑就壞」(10-14 Mini 推前檢查三份並行,池途中判壞 ⇒ 影子續跑整份全跑)。

量到的兩件(Mini verify-09759f16 那份,唯讀看的):
1. 18:05 被隔離進 .bad 的 4 列全是**合法的作廢列**({"void": …}):quarantine 只用 _parse_row 判,
   不認作廢列(build_index / compact 都先認作廢列)⇒ 把好列當壞列搬走,作廢失效。
2. 寫列(jsonl_safe.append_locked)與壓縮(compact)都拿池檔的檔鎖,讀的那邊(build_index)不拿 ⇒
   別份正在追加一列(池裡一列常有 200KB)或壓縮正在原地改寫時去讀,讀到半列 ⇒ bad-jsonl ⇒ 整份降級全跑。

R1 quarantine 認作廢列;R2 build_index 讀時拿共用鎖(別人寫完才讀);R3 quarantine 改寫整段拿獨佔鎖。
"""
import fcntl
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "verify"))
import reuse_index as RI  # noqa: E402


def row(test, rc=0, t="2026-10-14 20:00"):
    return {"test": test, "rc": rc, "inputs": [], "measured": True, "external": False, "why": [], "time": t}


VOID = {"void": {"test": "tests/test-a.py", "green_only": True}, "before": "2026-10-14 08:28:37",
        "before_utc": "2026-10-14T00:28:39Z", "reason": "tree-dirtied:x.py"}


class Pool(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.p = Path(self._tmp.name) / "verify-x.rows.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, *objs, tail=""):
        self.p.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in objs) + tail, encoding="utf-8")

    def test_R1_quarantine_keeps_void_rows(self):
        self.write(row("tests/test-a.py"), VOID, row("tests/test-b.py"))
        rep = RI.quarantine(self.p, apply=False)
        self.assertEqual(rep["bad_lines"], [], "作廢列是好列,不能搬進 .bad:%r" % rep)

    def test_R1b_quarantine_still_catches_real_damage(self):
        """對照:真的壞列照樣抓。"""
        self.write(row("tests/test-a.py"), tail='{"test": "tests/test-b.py", "rc": 0, "inp\n')
        self.assertEqual(RI.quarantine(self.p, apply=False)["bad_lines"], [2])

    def _hold_while(self, mode, fn, finish):
        """別人拿著檔鎖(mode)時跑 fn;0.4 秒後 finish() 補完再放鎖。回 fn 的結果與它花的秒數。"""
        out = {}
        with open(self.p, "ab") as lockf:
            fcntl.flock(lockf, mode)
            th = threading.Thread(target=lambda: out.update(r=fn(), t=time.time()))
            t0 = time.time()
            th.start()
            time.sleep(0.4)
            finish(lockf)
            fcntl.flock(lockf, fcntl.LOCK_UN)
            th.join(10)
        return out.get("r"), out.get("t", t0) - t0

    def test_R2_reader_waits_for_an_append_in_progress(self):
        whole = json.dumps(row("tests/test-b.py"), ensure_ascii=False) + "\n"
        self.write(row("tests/test-a.py"), tail=whole[:40])        # 別份寫到一半

        def finish(f):
            f.write(whole[40:].encode("utf-8"))
            f.flush()
        idx, waited = self._hold_while(fcntl.LOCK_EX, lambda: RI.build_index([str(self.p)]), finish)
        self.assertIsNotNone(idx)
        self.assertFalse(idx.degraded, "讀到別人寫到一半的列 ⇒ 判壞全跑:%r" % idx.degraded)
        self.assertGreaterEqual(waited, 0.3, "要等寫的人放鎖才讀")

    def test_R3_quarantine_does_not_lose_a_concurrent_append(self):
        self.write(row("tests/test-a.py"), tail='{"test": "tests/test-b.py", "rc": 0, "inp\n')
        late = json.dumps(row("tests/test-c.py"), ensure_ascii=False) + "\n"

        def finish(f):
            f.write(late.encode("utf-8"))
            f.flush()
        self._hold_while(fcntl.LOCK_EX, lambda: RI.quarantine(self.p, apply=True), finish)
        tests = [json.loads(l).get("test") for l in self.p.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertIn("tests/test-c.py", tests, "隔離改寫時別人追加的列不能丟")


if __name__ == "__main__":
    unittest.main()
