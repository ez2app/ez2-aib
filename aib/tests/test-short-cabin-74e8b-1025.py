#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""10-25 @74e8b(@abb89 裁):開席指名的艙要真的在,失敗不留空艙。

事故:`aib claude safe dev 6ddf --again`(只給艙號末 4 碼)⇒ 當成艙名「6ddf」,ensure 缺權杖 rc=4,
但 instances/6ddf/ 已被建出 ⇒ 艙號 6ddf 對到兩個艙,全艙 aib dm 6ddf/… 報 ambiguous-cabin。

C1 aib_cabin_pick_named:有權杖照名字;4 碼 hex 剛好對到一個新式艙 ⇒ 換成完整名;多個 / 對不到 / 沒權杖 ⇒ rc 2;
   權杖是捷徑不算;soft(dry-run)對不到原樣回、不拒
C2 真的跑啟動器(暫存狀態根、假 tmux):短碼對不到 / 指名不在 ⇒ rc 2、instances/ 不多任何夾、tmux 一次都沒叫;
   --instance= 同一道
刀:K1 拿掉 aib 第四參數那段呼叫 ⇒ C2 紅;K2 拿掉「多個就拒」⇒ C1 紅
"""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOLVE = ROOT / "src" / "cockpit" / "cabin_resolve.sh"
AIB = ROOT / "src" / "cockpit" / "aib"


def pick(root, want, soft="0"):
    r = subprocess.run(["bash", "-c", '. "$1"; aib_cabin_pick_named "$2" "$3" "$4"', "_", str(RESOLVE), want, str(root), soft],
                       capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout.strip(), r.stderr


def cabin(root, name, token=True):
    d = root / name
    d.mkdir(parents=True)
    if token:
        (d / "instance-token").write_text("0" * 32 + "\n")
    return d


class Pick(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "instances"
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_c1_rules(self):
        cabin(self.root, "20261014-133230-6ddf")
        cabin(self.root, "20261018-213001-2613")
        cabin(self.root, "20261020-101010-2613")
        cabin(self.root, "20261021-101010-aaaa", token=False)
        cabin(self.root, "legacy2")
        self.assertEqual(pick(self.root, "20261014-133230-6ddf")[:2], (0, "20261014-133230-6ddf"))
        self.assertEqual(pick(self.root, "legacy2")[:2], (0, "legacy2"))
        rc, out, err = pick(self.root, "6ddf")
        self.assertEqual((rc, out), (0, "20261014-133230-6ddf"))
        self.assertIn("6ddf ⇒ 20261014-133230-6ddf", err)
        rc, out, err = pick(self.root, "2613")
        self.assertEqual(rc, 2, "多個不猜")
        self.assertIn("對到 2 個艙", err)
        self.assertEqual(pick(self.root, "beef")[0], 2, "對不到")
        self.assertEqual(pick(self.root, "aaaa")[0], 2, "沒權杖的不算")
        self.assertEqual(pick(self.root, "nosuch")[0], 2, "指名不在")
        self.assertEqual(pick(self.root, "Bad!")[0], 2)
        self.assertEqual(pick(self.root, "beef", soft="1")[:2], (0, "beef"), "dry-run 不拒")
        self.assertEqual(pick(self.root, "6ddf", soft="1")[:2], (0, "20261014-133230-6ddf"))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         ["20261014-133230-6ddf", "20261018-213001-2613", "20261020-101010-2613", "20261021-101010-aaaa", "legacy2"], "解析不建任何夾")

    def test_c1_symlink_token_ignored(self):
        d = cabin(self.root, "20261014-133230-6ddf", token=False)
        other = Path(self.tmp.name) / "tok"
        other.write_text("0" * 32 + "\n")
        os.symlink(other, d / "instance-token")
        self.assertEqual(pick(self.root, "6ddf")[0], 2)
        self.assertEqual(pick(self.root, "20261014-133230-6ddf")[0], 2)


class Launcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.state = t / "state"
        self.inst = self.state / "instances"
        self.inst.mkdir(parents=True)
        cabin(self.inst, "20261014-133230-6ddf")
        cabin(self.inst, "20261018-213001-2613")
        cabin(self.inst, "20261020-101010-2613")
        self.bin = t / "bin"
        self.bin.mkdir()
        self.mark = t / "tmux-called"
        fake = self.bin / "tmux"
        fake.write_text('#!/bin/sh\necho "$@" >> "%s"\nexit 1\n' % self.mark)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.home = t / "home"
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_aib(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("EZ2_", "AIB_", "TMUX"))}
        env.update(HOME=str(self.home), EZ2_AIB_STATE_ROOT=str(self.state), PATH=f"{self.bin}:{os.environ['PATH']}",
                   AIB_NO_OPEN_ATTEMPT="0", LANG="zh_TW.UTF-8")
        return subprocess.run(["bash", str(AIB), *args], capture_output=True, text=True, timeout=60, env=env, stdin=subprocess.DEVNULL)

    def assert_refused(self, r, words):
        self.assertEqual(r.returncode, 2, r.stderr[-800:])
        self.assertIn(words, r.stderr)
        self.assertEqual(sorted(p.name for p in self.inst.iterdir()),
                         ["20261014-133230-6ddf", "20261018-213001-2613", "20261020-101010-2613"], "失敗不留空艙")
        self.assertFalse(self.mark.exists(), "擋在碰 tmux 之前")

    def test_c2_short_not_found(self):
        self.assert_refused(self.run_aib("claude", "safe", "dev", "beef", "--again"), "找不到艙 beef")

    def test_c2_short_ambiguous(self):
        self.assert_refused(self.run_aib("claude", "safe", "dev", "2613", "--again"), "對到 2 個艙")

    def test_c2_named_missing(self):
        self.assert_refused(self.run_aib("claude", "safe", "dev", "nosuch"), "找不到艙 nosuch")

    def test_c2_instance_flag(self):
        self.assert_refused(self.run_aib("claude", "safe", "dev", "--instance=beef"), "找不到艙 beef")


if __name__ == "__main__":
    unittest.main()
