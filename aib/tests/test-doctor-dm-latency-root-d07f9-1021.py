#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib doctor dm_latency 一律讀 doctor 傳進來的庫(@abb89 #246 審:原本 setdefault,外面設了 EZ2_AIB_STATE_ROOT 就讀錯庫)。

L1 環境變數指 A、doctor 傳 B ⇒ 量的是 B 的佇列(訊息裡的路徑在 B 底下,不在 A)
刀(複本):K1 改回 setdefault ⇒ L1 紅
"""
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (REPO / "src" / "bootstrap", HERE):
    sys.path.insert(0, str(_p))
import aib_doctor  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402


class Root(unittest.TestCase):
    def test_l1_reads_given_root(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            env = {"EZ2_AIB_STATE_ROOT": a, "HOME": a, "EZ2_AIB_LOCALE": "zh-TW"}
            r = aib_doctor.check_dm_latency(Path(b), REPO, env=env, now=1_800_000_000)
            self.assertEqual(r["status"], aib_doctor.OK, r)
            self.assertIn(str(Path(b).resolve().name), r["detail"], r)
            self.assertNotIn(Path(a).name, r["detail"], "讀到環境變數那一份了")


class Knife(unittest.TestCase):
    def test_k1_setdefault(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/bootstrap/aib_doctor.py", '    env["EZ2_AIB_STATE_ROOT"] = str(state_root)          # 一律讀 doctor 指定的庫(外面設了別的也不蓋過;@abb89 #246 審)',   # 錨帶尾註:dm_latency 與 temp 兩行同碼(0.1.38 整合)
                        '    env.setdefault("EZ2_AIB_STATE_ROOT", str(state_root))', tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Root.test_l1_reads_given_root"], cwd=str(root),
                            max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
