#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球來信貼進框時的抬頭(開發文件 v0.6 第 22.3 節 PR-2 接線;第 5.8、8.1 第 7–8 步、8.3 節)。

Andy 10-15 15:4x:「所有用戶要有顯示名稱,若有就預設只顯示 顯示名稱、沒有就顯示人格」。
全球來信一律先進佇列,只會從排空器的「排序投遞」貼進框 ⇒ 這裡照真的走一次 dm-ctl.sh:
入列(nostr: 來源 + global 欄)→ 排空器領件 → AIB_DM_SORT_DELIVER=1 跑 dm-ctl → 看咽喉拿到的正文。

W1 抬頭寄件方只有顯示名稱 + 全球標記(沙箱介面是英文 ⇒ Global·verified),後面接外框說明行與回信短碼,再接原文。
W2 未驗證、同名照標。
W3 本機席的排隊信照舊(不帶全球標記、不帶外框)——回歸。
W4 佇列裡的 global 欄被竄改(名字含 /)⇒ rc 2、一個字都沒送,不貼半個全球抬頭。
U1 build_from_global 單元:中文介面、不是全球來信 ⇒ ValueError。
K1 刀測:拿掉 dm-ctl 認 nostr: 來源那一行 ⇒ W1 變紅。

沙箱與假咽喉沿用 test-dm-reliability-abb89-1015.py 的 Box(假 tmux、假 aib-send.sh 記下參數),不碰真艙、不連網。
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

RELI = "test-dm-reliability-abb89-1015.py"
_spec = importlib.util.spec_from_file_location("dm_reliability_box", str(HERE / RELI))
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

NPUB = "npub1" + "p" * 58
ADDR = "npub1" + "q" * 58 + "/6ddf/abb89"
MSG = "全球你好,這是測試信"


def setUpModule():
    R.setUpModule()


def tearDownModule():
    R.tearDownModule()


def enqueue_global(b, shown="Andy", verified=True, same=False, kind="external_seat", tamper=None):
    """在沙箱佇列放一封全球來信並讓排空器領走;回 (佇列 id, 回信短碼)。"""
    code = (
        "import sys, json; sys.path.insert(0, %r)\n"
        "import dm_queue as Q, dm_global_files as G\n"
        "it = Q.enqueue('abcde', %r, %r, sender_kind=%r, sender_origin=%r,\n"
        "               global_info={'addr': %r, 'shown': %r, 'verified': %r, 'same_name': %r,\n"
        "                            'msgid': G.new_msgid()})\n"
        "Q.claim_id('abcde', it['id'], no_rev_check=True)\n"
        "print(json.dumps({'id': it['id'], 'code': (it.get('global') or {}).get('reply_code')}))\n"
    ) % (str(R.TREE / "src" / "cockpit"), MSG, shown, kind, "nostr:" + NPUB, ADDR, shown, verified, same)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=b.env(), timeout=30)
    assert p.returncode == 0, p.stdout + p.stderr
    got = json.loads(p.stdout.strip().splitlines()[-1])
    if tamper:
        f = next(b.aib_home.rglob("%s.json" % got["id"]))
        rec = json.loads(f.read_text())
        rec["global"].update(tamper)
        f.write_text(json.dumps(rec, ensure_ascii=False))
    return got["id"], got["code"]


def deliver(b, qid):
    return b.run(["@abcde", MSG], AIB_DM_SORT_DELIVER="1", AIB_DM_QUEUED_ITEM_ID=qid, AIB_DM_QUEUED_SEAT="abcde")


class Wire(unittest.TestCase):
    def test_w1_global_header_frame_body(self):
        b = R.Box(self)
        qid, code = enqueue_global(b)
        p = deliver(b, qid)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        body = R.throat_body(b.exec_calls()[0])
        self.assertTrue(body.startswith("Andy(Global·verified) >> IP:"), body[:120])
        self.assertIn("[Global message:", body)
        self.assertIn("aib dm --reply %s]" % code, body)
        self.assertTrue(body.endswith(MSG), body[-80:])
        self.assertNotIn(NPUB, body)           # 完整位址不顯示(第 5.8 節)
        self.assertNotIn("abb89", body.split(" >> ")[0])

    def test_w2_unverified_same_name(self):
        b = R.Box(self)
        qid, _ = enqueue_global(b, verified=False, same=True)
        p = deliver(b, qid)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(R.throat_body(b.exec_calls()[0]).startswith("Andy(Global·unverified·same name) >> "))

    def test_w3_local_queued_unchanged(self):
        b = R.Box(self)
        e = subprocess.run([sys.executable, str(R.TREE / "src" / "cockpit" / "dm_queue.py"), "enqueue", "--seat",
                            "abcde", "--sender-label", "@77777", "--sender-kind", "ai", "--msg", MSG],
                           capture_output=True, text=True, env=b.env(), timeout=30)
        qid = json.loads(e.stdout.strip().splitlines()[-1])["id"]
        subprocess.run([sys.executable, str(R.TREE / "src" / "cockpit" / "dm_queue.py"), "claim-id", "--seat", "abcde",
                        "--id", qid, "--no-rev-check"], capture_output=True, text=True, env=b.env(), timeout=30)
        p = deliver(b, qid)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        body = R.throat_body(b.exec_calls()[0])
        self.assertTrue(body.startswith("IP:"), body[:80])
        self.assertNotIn("Global", body)
        self.assertNotIn("aib dm --reply", body)

    def test_w4_tampered_global_sends_nothing(self):
        b = R.Box(self)
        qid, _ = enqueue_global(b, tamper={"shown": "IP:1.2.3.4/6ddf/X"})
        p = deliver(b, qid)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertEqual(b.exec_calls(), [])


class Unit(unittest.TestCase):
    def test_u1_build_from_global(self):
        import dm_global_files as G
        import dm_header as HD
        import dm_queue as Q
        with tempfile.TemporaryDirectory() as d:
            env = {"EZ2_AIB_STATE_ROOT": d, "HOME": d, "AIB_DM_ADDR": "100.114.152.121", "EZ2_AIB_LOCALE": "zh-TW"}
            it = Q.enqueue("8f366", MSG, "Andy", sender_kind="owner_paired", sender_origin="nostr:" + NPUB, env=env,
                           global_info={"addr": ADDR, "shown": "Andy", "verified": True, "msgid": G.new_msgid()})
            head, frame = HD.build_from_global("8f366", it["id"], "@8f366(第二艦長)", when=1792045800, env=env)
            self.assertTrue(head.startswith("Andy(全球·已驗證) >> IP:100.114.152.121/"), head)
            self.assertIn("第二艦長@8f366", head)
            self.assertTrue(frame.startswith("〔主人手機"))            # owner_paired 換另一句
            self.assertIn(it["global"]["reply_code"], frame)
            local = Q.enqueue("8f366", MSG, "@abb89", sender_kind="seat", sender_seat="abb89", env=env)
            with self.assertRaises(ValueError):
                HD.build_from_global("8f366", local["id"], "@8f366", env=env)


class Knife(unittest.TestCase):
    def test_k1_dm_ctl_ignores_nostr_origin(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/dm-ctl.sh", '    case "$_q_org" in nostr:*) _HDR_GLOBAL=1 ;; esac\n', "",
                        tests=(name, RELI)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Wire.test_w1_global_header_frame_body"],
                            cwd=str(root), max_procs=150, timeout=300)        # 跑 dm-ctl 的刀一律經看門狗
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
