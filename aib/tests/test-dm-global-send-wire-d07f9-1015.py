#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""`aib dm <全球位址>` 寄出(開發文件 v0.7 第 7、9、11、12、22.3 節 PR-4 接線)。

真的跑 dm-ctl.sh:寄件人由行程鏈量到(@77777、艙 t1),全球位址交給 dm_global_files send-and-wait,
假轉接程式(tests/aib_fake_bridge.py,一條執行緒)照劇本領件、寫回條。不連網、不碰真艙。

P1 送達:rc 0、RESULT stage=delivered id=<msgid>;待寄檔的寄件席 / 艙 / 已認證 / 位址 / 正文都對;本機框一個字都沒貼。
P2 對方忙:rc 0、stage=queued、RESULT 帶 pos。
P3 轉接程式沒開:rc 2,沒有待寄檔。
P4 待審 rc 3、被拒 rc 2。
P5 --dry-run:只解析(op=resolve),rc 0。
P6 擋下:--to 群發全球位址、--retry-until、把全球抬頭當位址 ⇒ rc 2,沒有待寄檔。
P7 --reply <短碼>:照自己佇列裡那封全球來信回原寄件人,帶 reply_to;短碼查不到 rc 2。
P8 --priority:全球路線帶進待寄檔;本機路線印一行提示後照常投遞。
P9 推不出寄件席(不在任何一席的視窗裡)⇒ rc 2,沒有待寄檔(全球信要用寄件席自己的金鑰簽)。
P10 環境變數塞 _GL_OUT_ADDR 不會把本機信改走全球。
K1–K2 刀測:拿掉 _GL_OUT_ADDR 初始化 ⇒ P10 紅;拿掉禁止全球群發 ⇒ P6 紅。
"""
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(HERE))
import dm_global_files as G  # noqa: E402
from aib_fake_bridge import FakeBridge  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402

RELI = "test-dm-reliability-abb89-1015.py"
_spec = importlib.util.spec_from_file_location("dm_reliability_box", str(HERE / RELI))
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

ADDR = "npub1" + "q" * 58 + "/931e/8f366"
MSG = "全球你好"


def setUpModule():
    R.setUpModule()


def tearDownModule():
    R.tearDownModule()


def steps(*stages, **extra):
    return lambda rec: [(s, extra.get(s, {})) for s in stages]


class Base(unittest.TestCase):
    def box(self):
        b = R.Box(self, seats=((2, R.SEAT_UUID), (3, R.SENDER_UUID)), extra_windows=())
        b.panes({"%9": {"wid": "@3", "pid": os.getpid()}})     # 測試行程是 dm-ctl 的祖先 ⇒ 寄件人量得到 @77777
        b.bridge = b.dir / "chat-bridge"
        return b

    def run_dm(self, b, args, script=None, heartbeat=True, seated=True, **env):
        # AIB_DM_ADDR_DONE 清掉:真的 `aib dm` 入口不設它,位址要經 dm_address 拆(全球位址在那裡認出來)
        kw = dict(AIB_CHAT_BRIDGE_DIR=str(b.bridge), AIB_DM_GLOBAL_WAIT="2", AIB_DM_ADDR_DONE="")
        if seated:
            kw.update(TMUX="/tmp/fake-tmux-dir/%s,1,0" % R.HUB, TMUX_PANE="%9")
        kw.update(env)
        with FakeBridge(b.bridge, script or steps("relayed", "delivered"), heartbeat=heartbeat) as fb:
            p = b.run(args, **kw)
        return p, fb

    def outbox_recs(self, b, fb):
        """領走的(sending/)加還在 outbox 的。"""
        out = []
        for sub in ("sending", "outbox"):
            d = b.bridge / sub
            if d.is_dir():
                out += [json.loads(f.read_text()) for f in sorted(d.glob("*.json"))]
        return out


class Send(Base):
    def test_p1_delivered(self):
        b = self.box()
        p, fb = self.run_dm(b, [ADDR, MSG])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(R.last_line(p), r"^RESULT stage=delivered id=[0-9a-f]{32} relay=- thread=-")
        (rec,) = self.outbox_recs(b, fb)
        self.assertEqual(rec["from"], {"cabin": "t1", "seat": "77777", "attested": True})
        self.assertEqual((rec["to"], rec["content"], rec["op"]), (ADDR, MSG, "send"))
        self.assertEqual(b.exec_calls(), [])                        # 本機框一個字都沒貼

    def test_p2_queued_pos(self):
        b = self.box()
        p, _ = self.run_dm(b, [ADDR, MSG], steps("relayed", "queued", queued={"pos": 3}))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(R.last_line(p), r"^RESULT stage=queued .* reason=pos")

    def test_p3_bridge_off(self):
        b = self.box()
        p, fb = self.run_dm(b, [ADDR, MSG], heartbeat=False)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("bridge-off", R.last_line(p))
        self.assertEqual(self.outbox_recs(b, fb), [])

    def test_p4_held_refused(self):
        b = self.box()
        p, _ = self.run_dm(b, [ADDR, MSG], steps("relayed", "held"))
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        b = self.box()
        p, _ = self.run_dm(b, [ADDR, MSG], steps("refused", refused={"detail": "對方還沒開全球對談"}))
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("對方還沒開全球對談", p.stderr)

    def test_p5_dry_run(self):
        b = self.box()
        p, fb = self.run_dm(b, [ADDR, MSG, "--dry-run"], steps("resolved", resolved={"detail": "兩站"}))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        (rec,) = self.outbox_recs(b, fb)
        self.assertEqual(rec["op"], "resolve")
        self.assertNotIn("content", rec)

    def test_p6_blocked(self):
        for args in (["--to", ADDR + ",@abcde", MSG], [ADDR, MSG, "--retry-until", "60"],
                     ["Andy(全球·台灣·已驗證)", MSG]):
            b = self.box()
            p, fb = self.run_dm(b, args)
            self.assertEqual(p.returncode, 2, "%s\n%s" % (args, p.stdout + p.stderr))
            self.assertEqual(self.outbox_recs(b, fb), [], args)
            self.assertEqual(b.exec_calls(), [], args)
        b = self.box()
        p, _ = self.run_dm(b, ["--to", ADDR + ",@abcde", MSG])
        self.assertIn("全球位址不能群發", p.stderr)          # 講得出為什麼,不是別處碰巧擋下

    def test_p7_reply(self):
        b = self.box()
        code = (
            "import sys, json; sys.path.insert(0, %r)\n"
            "import dm_queue as Q, dm_global_files as G\n"
            "m = G.new_msgid()\n"
            "it = Q.enqueue('77777', 'hi', 'Bob', sender_kind='external_seat', sender_origin='nostr:npub1' + 'p' * 58,\n"
            "               global_info={'addr': %r, 'shown': 'Bob', 'verified': True, 'msgid': m})\n"
            "print(json.dumps({'code': it['global']['reply_code'], 'msgid': m}))\n"
        ) % (str(R.TREE / "src" / "cockpit"), ADDR)
        q = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=b.env(), timeout=30)
        got = json.loads(q.stdout.strip().splitlines()[-1])
        p, fb = self.run_dm(b, ["--reply", got["code"], "收到,謝謝"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        (rec,) = self.outbox_recs(b, fb)
        self.assertEqual((rec["to"], rec["reply_to"], rec["content"]), (ADDR, got["msgid"], "收到,謝謝"))
        b2 = self.box()
        p, fb = self.run_dm(b2, ["--reply", "zzzzz", "x"])
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertEqual(self.outbox_recs(b2, fb), [])
        p, _ = self.run_dm(self.box(), ["--reply", got["code"], "x", "--to", ADDR])
        self.assertEqual(p.returncode, 2)

    def test_p8_priority(self):
        b = self.box()
        p, fb = self.run_dm(b, [ADDR, MSG, "--priority", "urgent"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.outbox_recs(b, fb)[0]["priority"], "urgent")
        b = self.box()
        p, _ = self.run_dm(b, ["@abcde", MSG, "--priority", "urgent"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("--priority 目前只用在全球位址", p.stderr)
        self.assertEqual(len(b.exec_calls()), 1)
        p, _ = self.run_dm(self.box(), [ADDR, MSG, "--priority", "asap"])
        self.assertEqual(p.returncode, 2)

    def test_p9_unknown_sender_refused(self):
        b = self.box()
        p, fb = self.run_dm(b, [ADDR, MSG], seated=False)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("推不出寄件席", p.stderr)
        self.assertEqual(self.outbox_recs(b, fb), [])

    def test_p10_env_cannot_force_global(self):
        b = self.box()
        p, fb = self.run_dm(b, ["@abcde", MSG], _GL_OUT_ADDR=ADDR)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.outbox_recs(b, fb), [])
        self.assertEqual(len(b.exec_calls()), 1)


class Knife(unittest.TestCase):
    def _red(self, needle, cut, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/dm-ctl.sh", needle, cut, tests=(name, RELI)) as root:
            r = subprocess.run([sys.executable, str(root / "tests" / name), cell],
                               cwd=str(root), capture_output=True, text=True, timeout=600)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_k1_no_init(self):
        self._red('_GL_OUT_ADDR=""   # 全球位址只由下面位址段設;先清掉,環境裡帶進來的不算\n', "",
                  "Send.test_p10_env_cannot_force_global")

    def test_k2_broadcast_allowed(self):
        self._red('  case "$TO_RAW" in *npub1*) emit_json 2 resolve "global:no-broadcast"\n'
                  '    die "全球位址不能群發(--to);一次寄一位" 2 ;; esac\n', "", "Send.test_p6_blocked")


if __name__ == "__main__":
    unittest.main()
