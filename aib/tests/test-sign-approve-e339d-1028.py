#!/usr/bin/env python3
"""發版簽核搬進倉(tools/release/sign_approve.py;10-28 @aeb40 TID-1 第 3 項)。

S1 讀 SIGN 腳本:檔名 + BRANCH / PIN_SHA / 倉庫;缺一樣、PIN 不是十六進位 ⇒ 跳過
S2 分支頭不是 PIN ⇒ 不彈窗、不跑
S3 按過 ⇒ 跑的是暫存複本(不是原檔)、紀錄寫在狀態根 pending/sign-logs、rc 0 = ✓
S4 取消 / 鎖被別人拿著 ⇒ 不跑
S5 視窗字由核心組:外掛、版號、頭碼都在;類別 = 發版簽章(高風險)
S6 薄殼:建 / 不變 / 舊的獨立版先備份;只 exec 倉內這一支
S7 不是 Mac / AIB_TOUCHID=0 ⇒ rc 2,不讀腳本
S8 aib init 會寫薄殼(寫進給的狀態根,不碰真 ~/.ez2-aib)
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
sys.path.insert(0, str(ROOT / "tools" / "release"))
sys.path.insert(0, str(ROOT / "src" / "bootstrap"))

import approve_gate as AG  # noqa: E402
import sign_approve as SA  # noqa: E402
import touchid_approve as TA  # noqa: E402

PIN = "abc1234"
SCRIPT = """#!/bin/bash
BRANCH="${BRANCH:-release/demo-0.1.2}"
PIN_SHA="%s"
VER="0.1.2"
git clone git@github.com:ez2app/aib-plugins.git "$T"
echo signed
""" % PIN


class FakeRun:
    def __init__(self, rc=0, out="all good\n"):
        self.calls, self.rc, self.out = [], rc, out

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if argv[0] == "bash":
            self.ran_text = Path(argv[1]).read_text(encoding="utf-8")
        return mock.Mock(returncode=self.rc if argv[0] == "bash" else 0, stdout=self.out, stderr="")

    def bash(self):
        return [c for c in self.calls if c[0] == "bash"]


class Box(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-signapp-")
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_AIB_HOME": str(self.state),
                    "EZ2_AIB_LOCALE": "en", "HOME": str(self.tmp)}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        self.script = self.tmp / "SIGN-demo-0.1.2-abb89-1028.sh"
        self.script.write_text(SCRIPT, encoding="utf-8")

    def job(self):
        job, why = SA.plan(self.script)
        self.assertIsNone(why)
        return job

    def test_s1_plan(self):
        j = self.job()
        self.assertEqual((j["plugin"], j["ver"], j["branch"], j["pin"], j["repo"]),
                         ("demo", "0.1.2", "release/demo-0.1.2", PIN, "ez2app/aib-plugins"))
        bad = self.tmp / "notsign.sh"
        bad.write_text(SCRIPT)
        self.assertEqual(SA.plan(bad), (None, "bad_name"))
        nov = self.tmp / "SIGN-demo-0.1.2-x.sh"
        nov.write_text("echo hi\n")
        self.assertEqual(SA.plan(nov), (None, "no_vars"))
        badpin = self.tmp / "SIGN-demo-0.1.3-x.sh"
        badpin.write_text(SCRIPT.replace(PIN, "not-hex!"))
        self.assertEqual(SA.plan(badpin), (None, "no_vars"))

    def test_s2_head_mismatch(self):
        run, asked = FakeRun(), []
        ok, line = SA.one(self.job(), env=self.env, run=run, head_fn=lambda r, b: "fff0000" + "0" * 33,
                          touchid=lambda t: asked.append(t) or {"ok": True})
        self.assertFalse(ok)
        self.assertEqual(asked, [])
        self.assertEqual(run.bash(), [])

    def test_s3_runs_copy_and_logs(self):
        run, asked = FakeRun(), []
        ok, line = SA.one(self.job(), env=self.env, run=run, head_fn=lambda r, b: PIN + "0" * 33,
                          touchid=lambda t: asked.append(t) or {"ok": True})
        self.assertTrue(ok, line)
        self.assertEqual(len(run.bash()), 1)
        self.assertNotEqual(Path(run.bash()[0][1]), self.script, "要跑暫存複本")
        self.assertEqual(run.ran_text, SCRIPT)
        logs = list((self.state / "pending" / "sign-logs").glob("demo-0.1.2-*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("all good", logs[0].read_text())
        self.assertFalse(Path(run.bash()[0][1]).exists(), "暫存複本跑完要清掉")

    def test_s4_cancel_and_busy(self):
        run = FakeRun()
        ok, _ = SA.one(self.job(), env=self.env, run=run, head_fn=lambda r, b: PIN,
                       touchid=lambda t: {"ok": False, "why": "cancel"})
        self.assertFalse(ok)
        with TA.one_at_a_time(AG.lock_path(self.state)):
            ok, line = SA.one(self.job(), env=self.env, run=run, head_fn=lambda r, b: PIN, touchid=lambda t: {"ok": True})
        self.assertFalse(ok)
        self.assertEqual(run.bash(), [])

    def test_s5_window_text_and_category(self):
        asked = []
        SA.one(self.job(), env=self.env, run=FakeRun(), head_fn=lambda r, b: PIN,
               touchid=lambda t: asked.append(t) or {"ok": False, "why": "cancel"})
        r = asked[0]
        for part in ("demo", "0.1.2", PIN):
            self.assertIn(part, r)
        self.assertEqual(r.category, "release_sign")
        self.assertTrue(r.high_risk)

    def test_s6_shim(self):
        bindir = self.state / "bin"
        bindir.mkdir()
        old = bindir / SA.SHIM_NAME
        old.write_text("#!/usr/bin/env python3\nprint('standalone')\n")
        self.assertEqual(SA.ensure_shim(self.state), "updated")
        self.assertEqual(len(list(bindir.glob(SA.SHIM_NAME + ".bak-*"))), 1)
        body = old.read_text()
        self.assertIn(SA.SHIM_MARK, body)
        self.assertIn('exec /usr/bin/env python3 "%s" "$@"' % SA.HERE, body)
        self.assertTrue(os.access(old, os.X_OK))
        self.assertEqual(SA.ensure_shim(self.state), "unchanged")
        other = self.tmp / "s2"
        self.assertEqual(SA.ensure_shim(other), "created")

    def test_s6b_shim_temp_not_followed(self):
        """暫存檔位置先被放了符號連結 ⇒ 不跟著寫到連結指的檔(@aeb40 #404 R1 L3)。"""
        bindir = self.state / "bin"
        bindir.mkdir()
        victim = self.tmp / "victim.txt"
        victim.write_text("keep\n")
        (bindir / (".%s.tmp" % SA.SHIM_NAME)).symlink_to(victim)
        (bindir / (".%s.tmp-%d" % (SA.SHIM_NAME, os.getpid() + 1))).symlink_to(victim)   # 別的 pid 留下的(舊版名)
        self.assertEqual(SA.ensure_shim(self.state), "created")
        self.assertEqual(victim.read_text(), "keep\n")
        self.assertIn(SA.SHIM_MARK, (bindir / SA.SHIM_NAME).read_text())
        self.assertFalse((bindir / SA.SHIM_NAME).is_symlink())
        self.assertEqual(sorted(p.name for p in bindir.iterdir()), [SA.SHIM_NAME], "暫存殘留都清掉(@aeb40 #404 R2 Low)")

    def test_s6c_shim_temp_race_refused(self):
        """拿掉殘留之後、開檔之前又被放回符號連結(競態):O_EXCL|O_NOFOLLOW ⇒ 開檔失敗、不寫到連結指的檔。"""
        bindir = self.state / "bin"
        bindir.mkdir()
        victim = self.tmp / "victim.txt"
        victim.write_text("keep\n")
        (bindir / (".%s.tmp" % SA.SHIM_NAME)).symlink_to(victim)
        with mock.patch.object(SA.os, "unlink", lambda p: None):
            self.assertEqual(SA.ensure_shim(self.state), "refused", "開不了暫存檔 ⇒ 回拒,不丟例外")
        self.assertEqual(victim.read_text(), "keep\n")
        self.assertFalse((bindir / SA.SHIM_NAME).exists())

    def test_s7_not_mac(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = SA.main([str(self.script)], env=self.env, platform="linux")
        self.assertEqual(rc, 2)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(SA.main([str(self.script)], env=dict(self.env, AIB_TOUCHID="0"), platform="darwin",
                                     available=lambda: True), 2)

    def test_s8_aib_init_writes_shim(self):
        import aib_init
        lines = aib_init._ensure_sign_shim(str(self.state))
        self.assertTrue((self.state / "bin" / SA.SHIM_NAME).is_file())
        self.assertEqual(len(lines), 1)
        self.assertEqual(aib_init._ensure_sign_shim(str(self.state)), [])


if __name__ == "__main__":
    unittest.main()
