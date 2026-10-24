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
H7 #302 審查:`aib power -- login` 乾淨回 rc 2(不 Traceback);_write_page 寫壞清暫存、暫存名上先放連結 ⇒ 拒寫;
   登入紀錄 O_NOFOLLOW(目標是連結 ⇒ spawn_failed、連結指的檔不被截斷)、起不了 CLI 刪掉空紀錄、
   鏈結先出裝置碼晚到也等得到
刀(PR 手跑在複本上):_write_page 失敗不清暫存 / 拿掉 O_EXCL+O_NOFOLLOW ⇒ H7 紅;登入紀錄拿掉 O_NOFOLLOW ⇒ H7 紅;
  _URL_RE 改回 [^\\s...] ⇒ H4 紅;_other 改回只去 C0 / C1 ⇒ H5 紅;power_launch._s 不看 Cf ⇒ H5 紅;
  _write_page 改回固定 .tmp + write_text ⇒ H3 紅(權限);_save 改回 open(tmp,"w") ⇒ H2 紅(權限)
(拿到鏈結就刪登入紀錄在 test-power-login-d07f9-1024.py G2)
"""
import contextlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

RLO, ZWSP, LSEP, HFILL = "\u202e", "\u200b", "\u2028", "\u3164"


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


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
        self.assertEqual(power_crew._other("a" + LSEP + "b" + HFILL + "c"), "abc")     # 行分隔 / 韓文填充字
        self.assertIsNone(power_launch._s("a" + LSEP + "b"))
        self.assertIsNone(power_launch._s(HFILL))

    def test_h6_contract_interface_only(self):
        text = (REPO / "contracts" / "power.json").read_text(encoding="utf-8")
        json.loads(text)
        for bad in ("pending/", "SPEC", "PLAN-"):
            self.assertNotIn(bad, text)

    def test_h7_review_302(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            power_status.main(["--", "login"])
        self.assertEqual(cm.exception.code, 2)
        self.assertNotIn("Traceback", err.getvalue())

        d = Path(tempfile.mkdtemp(dir=TMP))
        with self.assertRaises(UnicodeEncodeError):
            power_page._write_page(d / "index.html", "x\ud800")                       # 寫到一半壞掉
        self.assertEqual(os.listdir(d), [])
        victim = d / "victim.txt"
        victim.write_text("keep")
        target = d / "index.html"
        with mock.patch.object(power_page.os, "urandom", return_value=b"\0" * 6):
            tmp = target.with_name(".%s.%d.%s.tmp" % (target.name, os.getpid(), "00" * 6))
            os.symlink(victim, tmp)
            with self.assertRaises(OSError):
                power_page._write_page(target, "<p>hi</p>")
        self.assertEqual(victim.read_text(), "keep")
        self.assertFalse(target.exists())

        log = Path(power_login.state_dir()) / "power-login-codex.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(victim, log)
        with mock.patch.object(power_login.shutil, "which", return_value="/bin/true"):
            self.assertEqual(power_login.spawn_login("codex", popen=mock.Mock())[2], "spawn_failed")
            self.assertEqual(victim.read_text(), "keep")                               # 沒被 O_TRUNC 截斷
            log.unlink()

            def boom(*a, **kw):
                raise OSError("no")
            self.assertEqual(power_login.spawn_login("codex", popen=boom)[2], "spawn_failed")
            self.assertFalse(log.exists(), "起不了 CLI 要刪掉空的登入紀錄")

            t = [0.0]

            def popen(argv, stdout, **kw):                                              # 先只印鏈結
                os.write(stdout, b"open https://auth.example/device\n")

            def sleep(_s):
                t[0] += 0.5
                if t[0] == 1.0:                                                         # 一秒後才印裝置碼
                    with open(log, "a") as f:
                        f.write("code: ABCD-1234\n")
            got = power_login.spawn_login("codex", popen=popen, sleep=sleep, clock=lambda: t[0], wait_s=10)
            self.assertEqual(got, ("https://auth.example/device", "ABCD-1234", None))
            self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
