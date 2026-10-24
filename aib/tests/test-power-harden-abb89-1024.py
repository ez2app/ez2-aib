#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""動力室加固(10-24 @abb89,#293 三輪審查記下排下一版的幾條)。暫存狀態根。

H1 `aib power --help` 列全子指令(status / upgrade / login / launch / fav / ask / why / tier / redeem),rc 0
H2 power_crew / power_launch / power_tasks / power_login 存檔走 atomic_json:檔 0600、沒有殘留暫存檔;
   tasks / login 寫不進去照舊不拋
H3 power_page._write_page:0600、沒殘留;目標原本是連結 ⇒ 換成一般檔,連結指的檔不被改
H4 登入鏈結只收 ASCII 網址字元:全形字開頭不算、中文路徑截在前面
H5 自填字串:power_crew 的「其他」去掉方向控制 / 零寬;power_launch 紀錄字串有這些就當壞資料
H6 契約 power.json 只講介面:不引用內部規格 / 待辦路徑
刀(PR 手跑在複本上):_URL_RE 改回 [^\\s...] ⇒ H4 紅;_other 改回只去 C0 / C1 ⇒ H5 紅;power_launch._s 不看 Cf ⇒ H5 紅;
  _write_page 改回固定 .tmp + write_text ⇒ H3 紅(權限);_save 改回 open(tmp,"w") ⇒ H2 紅(權限)
(拿到鏈結就刪登入紀錄在 test-power-login-d07f9-1024.py G2)
"""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))
TMP = tempfile.mkdtemp(prefix="aib-power-harden-test-")
os.environ["EZ2_AIB_STATE_ROOT"] = TMP
import power_crew  # noqa: E402
import power_launch  # noqa: E402
import power_login  # noqa: E402
import power_page  # noqa: E402
import power_status  # noqa: E402
import power_tasks  # noqa: E402

RLO, ZWSP = "\u202e", "\u200b"


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def leftovers(d):
    return [n for n in os.listdir(d) if ".tmp" in n]


class Harden(unittest.TestCase):
    def test_h1_help_lists_all(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            power_status.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        for name in ("status", "upgrade", "login", "launch", "fav", "ask", "why", "tier", "redeem"):
            self.assertIn(name, out.getvalue())

    def test_h2_saves_private_no_leftover(self):
        power_crew.set_why(["memory", "coding"], now=1)
        p = power_crew._path()
        self.assertEqual(mode(p), 0o600)
        self.assertEqual(json.loads(Path(p).read_text())["why_rank"], ["memory", "coding"])
        power_launch.record_launch("claude", now=1)
        self.assertEqual(mode(power_launch._path()), 0o600)
        self.assertEqual(leftovers(os.path.dirname(p)), [])
        d = Path(tempfile.mkdtemp(dir=TMP))
        for mod in (power_tasks, power_login):
            f = d / ("%s.json" % mod.__name__)
            mod._save(str(f), {"a": 1})
            self.assertEqual(mode(f), 0o600)
            blocker = d / ("file-%s" % mod.__name__)
            blocker.write_text("x")
            mod._save(str(blocker / "x.json"), {"a": 1})         # 上層是檔:寫不進去照舊不拋
        self.assertEqual(leftovers(d), [])

    def test_h3_page_write(self):
        d = Path(tempfile.mkdtemp(dir=TMP))
        victim = d / "victim.txt"
        victim.write_text("keep")
        target = d / "index.html"
        os.symlink(victim, target)
        power_page._write_page(target, "<p>hi</p>")
        self.assertFalse(target.is_symlink())
        self.assertEqual(target.read_text(), "<p>hi</p>")
        self.assertEqual(mode(target), 0o600)
        self.assertEqual(victim.read_text(), "keep")
        self.assertEqual(leftovers(d), [])

    def test_h4_url_ascii_only(self):
        self.assertEqual(power_login._clean_url("go https://a.example/x?y=1&z=%20 ok"), "https://a.example/x?y=1&z=%20")
        self.assertIsNone(power_login._clean_url("https://\uff45xample.com/a"))           # 全形 e
        self.assertEqual(power_login._clean_url("https://a.example/\u8def\u5f91"), "https://a.example/")
        self.assertEqual(power_login._clean_url("https://a.example/x" + RLO + "gpj.exe"), "https://a.example/x")

    def test_h5_free_text(self):
        self.assertEqual(power_crew._other("ab" + RLO + "c" + ZWSP + "d"), "abcd")
        self.assertIsNone(power_crew._other(RLO + ZWSP))
        self.assertIsNone(power_launch._s("sonnet" + RLO))
        self.assertIsNone(power_launch._s("a" + ZWSP + "b"))
        self.assertIsNone(power_launch._s("a\x85b"))                                     # C1
        self.assertEqual(power_launch._s("opus-4"), "opus-4")

    def test_h6_contract_interface_only(self):
        text = (REPO / "contracts" / "power.json").read_text(encoding="utf-8")
        json.loads(text)
        for bad in ("pending/", "SPEC", "PLAN-"):
            self.assertNotIn(bad, text)


if __name__ == "__main__":
    unittest.main()
