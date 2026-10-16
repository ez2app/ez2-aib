#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球 dm 寄件端「寄送與等待」與全球抬頭(開發文件 v0.5 第 7.1、8.3、9、22.3 節;PR-4 / PR-2 的邏輯本體)。

S1–S9  send_and_wait 照第 7.1 節的表回 rc;轉接程式沒開 ⇒ rc 2 且一個字都沒寫;
       逾時沒人領件 ⇒ 撤回成功、rc 2(保證沒送);已領走 ⇒ rc 4(勿重送);--verify、--dry-run。
S10    回信短碼:5 碼、同輸入同結果、撞碼換下一個。
S11    CLI 整條:另起行程跑 send-and-wait,假轉接程式在本行程寫回條。
H1–H4  全球抬頭:四語標記、名字不合格就拒、parse 來回、偽造成 IP 格式的不當全球抬頭、跟 dm_header 的收件方與時間接得起來。
K1–K2  刀測:拿掉心跳檢查 / 拿掉撤回 ⇒ 對應格變紅。
假轉接程式只讀寫暫存資料夾的檔案(tests/aib_fake_bridge.py),不連網。
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "tests"))
import dm_global_files as G  # noqa: E402
import dm_global_header as H  # noqa: E402
from aib_fake_bridge import FakeBridge  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402

NPUB_OWNER = "npub1" + "q" * 58


def outbox(**over):
    rec = {"v": 1, "msgid": G.new_msgid(), "created_at": int(time.time()), "op": "send",
           "from": {"cabin": "6ddf", "seat": "abb89", "attested": True},
           "to": NPUB_OWNER + "/931e/8f366", "content": "你好"}
    rec.update(over)
    return rec


def steps(*stages, **extra):
    return lambda rec: [(s, extra.get(s, {})) for s in stages]


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-gsw-")
        self.addCleanup(td.cleanup)
        self.root = G.ensure_dirs(Path(td.name) / "chat-bridge")

    def send(self, script, wait=2, verify=False, rec=None, **fb):
        rec = rec or outbox()
        with FakeBridge(self.root, script, **fb) as bridge:
            res = G.send_and_wait(self.root, rec, wait=wait, verify=verify, poll=0.02)
        return res, bridge, rec

    def assertNothingLeft(self, msgid):
        self.assertFalse((self.root / "outbox" / (msgid + ".json")).exists())
        self.assertFalse((self.root / "sending" / (msgid + ".json")).exists())


class SendWait(Base):
    def test_s1_bridge_off_writes_nothing(self):
        rec = outbox()
        res = G.send_and_wait(self.root, rec, wait=1, poll=0.02)        # 沒有心跳檔
        self.assertEqual((res["rc"], res["stage"]), (2, "bridge-off"))
        self.assertEqual(os.listdir(self.root / "outbox"), [])
        G.write_heartbeat(self.root, now=time.time() - 120)               # 心跳太舊
        self.assertEqual(G.send_and_wait(self.root, outbox(), wait=1, poll=0.02)["stage"], "bridge-off")
        self.assertEqual(os.listdir(self.root / "outbox"), [])

    def test_s2_delivered_and_queued(self):
        res, _, _ = self.send(steps("relayed", "delivered"))
        self.assertEqual((res["rc"], res["stage"]), (0, "delivered"))
        res, _, _ = self.send(steps("relayed", "queued", queued={"pos": 3}))
        self.assertEqual((res["rc"], res["stage"], res["pos"]), (0, "queued", 3))

    def test_s3_verify_needs_delivered(self):
        res, _, _ = self.send(steps("relayed", "queued", "delivered", queued={"pos": 1}), verify=True)
        self.assertEqual((res["rc"], res["stage"]), (0, "delivered"))
        res, _, rec = self.send(steps("relayed", "queued", queued={"pos": 1}), verify=True, wait=1)
        self.assertEqual((res["rc"], res["stage"]), (4, "relayed"))      # 已交出,勿重送

    def test_s4_held_refused_expired_failed(self):
        for stage, rc in (("held", 3), ("refused", 2), ("expired", 2), ("failed", 2)):
            res, _, _ = self.send(steps("relayed", stage) if stage != "failed" else steps("failed"))
            self.assertEqual((res["rc"], res["stage"]), (rc, stage), stage)

    def test_s5_timeout_unclaimed_is_withdrawn(self):
        res, bridge, rec = self.send(lambda r: None, wait=1)
        self.assertEqual((res["rc"], res["stage"]), (2, "timeout-withdrawn"))
        self.assertEqual(bridge.claimed, [])
        self.assertNothingLeft(rec["msgid"])
        self.assertIsNone(G.claim(self.root, rec["msgid"]))               # 撤回後外掛領不到

    def test_s6_claimed_without_receipt_is_rc4(self):
        res, bridge, rec = self.send(lambda r: "claim-only", wait=1)
        self.assertEqual((res["rc"], res["stage"]), (4, "claimed"))
        self.assertEqual(bridge.claimed, [rec["msgid"]])
        res, _, _ = self.send(steps("relayed"), wait=1)
        self.assertEqual((res["rc"], res["stage"]), (4, "relayed"))

    def test_s7_dry_run_resolve(self):
        rec = outbox(op="resolve")
        del rec["content"]
        res, _, _ = self.send(steps("resolved", resolved={"detail": "931e/8f366 → npub1… 兩站"}), rec=rec)
        self.assertEqual((res["rc"], res["stage"]), (0, "resolved"))
        self.assertIn("931e/8f366", res["detail"])
        rec = outbox(op="resolve")
        del rec["content"]
        res, _, _ = self.send(steps("refused", refused={"detail": "對方還沒開線上對話"}), rec=rec)
        self.assertEqual((res["rc"], res["detail"]), (2, "對方還沒開線上對話"))

    def test_s8_bad_record_writes_nothing(self):
        G.write_heartbeat(self.root)
        res = G.send_and_wait(self.root, outbox(content="字" * (G.CONTENT_MAX + 1)), wait=1, poll=0.02)
        self.assertEqual((res["rc"], res["stage"]), (2, "abort"))
        self.assertEqual(os.listdir(self.root / "outbox"), [])

    def test_s9_heartbeat_and_claim_race(self):
        self.assertFalse(G.bridge_alive(self.root))
        (self.root / "heartbeat.json").write_text("{broken", encoding="utf-8")
        self.assertFalse(G.bridge_alive(self.root))
        G.write_heartbeat(self.root, now=time.time() + 3600)              # 時鐘亂了
        self.assertFalse(G.bridge_alive(self.root))
        G.write_heartbeat(self.root)
        self.assertTrue(G.bridge_alive(self.root))
        a, b = outbox(), outbox()
        G.write_outbox(self.root, a)
        G.write_outbox(self.root, b)
        self.assertIsNotNone(G.claim(self.root, a["msgid"]))
        self.assertFalse(G.withdraw(self.root, a["msgid"]))               # 已領走 ⇒ 撤不回
        self.assertTrue(G.withdraw(self.root, b["msgid"]))
        self.assertIsNone(G.claim(self.root, b["msgid"]))                 # 已撤回 ⇒ 領不到

    def test_s10_reply_code(self):
        m = G.new_msgid()
        c = G.reply_code(m)
        self.assertRegex(c, r"^[a-z2-9]{5}$")
        self.assertEqual(G.reply_code(m), c)
        c2 = G.reply_code(m, taken={c})
        self.assertNotEqual(c2, c)
        self.assertEqual(G.reply_code(m, taken={c}), c2)
        for ch in "ilo01":
            self.assertNotIn(ch, "".join(G.reply_code(G.new_msgid()) for _ in range(200)))
        with self.assertRaises(G.ContractError):
            G.reply_code("../x")

    def test_s11_cli_end_to_end(self):
        cli = [sys.executable, str(REPO / "src" / "cockpit" / "dm_global_files.py"), "send-and-wait",
               "--root", str(self.root), "--to", NPUB_OWNER + "/931e/8f366", "--from-cabin", "6ddf",
               "--from-seat", "abb89", "--attested", "1", "--priority", "urgent", "--wait", "3"]
        with FakeBridge(self.root, steps("relayed", "queued", queued={"pos": 2})) as bridge:
            p = subprocess.run(cli, input="全球你好", capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertRegex(p.stdout.strip(), r"^stage=queued rc=0 msgid=[0-9a-f]{32} pos=2 detail=")
        self.assertEqual(len(bridge.claimed), 1)
        sent = G.read_receipts(self.root, bridge.claimed[0])
        self.assertEqual([r["stage"] for r in sent], ["relayed", "queued"])
        p = subprocess.run(cli[:-2] + ["--wait", "1", "--root", str(self.root / "nobridge")], input="x",
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 2)
        self.assertTrue(p.stdout.startswith("stage=bridge-off rc=2"))


class Header(unittest.TestCase):
    def test_h1_sender_side_languages(self):
        self.assertEqual(H.sender_side("Andy", True), "Andy(線上·已驗證)")
        self.assertEqual(H.sender_side("Andy", False, same_name=True), "Andy(線上·未驗證·同名)")
        self.assertEqual(H.sender_side("Andy", True, lang="en"), "Andy(Online·verified)")
        self.assertEqual(H.sender_side("店長 小美", False, lang="ja"), "店長 小美(オンライン·未認証)")
        self.assertEqual(H.sender_side("Andy", True, lang="ko"), "Andy(온라인·인증됨)")
        self.assertEqual(H.sender_side("Andy", True, lang="fr"), "Andy(線上·已驗證)")   # 不認得的語言退預設
        # 10-16 14:5x 標記改名(全球 ⇒ 線上):佇列、框裡、歷史的舊抬頭照認;組只用新的
        for old_new in (("Andy(全球·台灣·已驗證)", "Andy(線上·台灣·已驗證)"), ("Bob(Global·verified)", "Bob(Online·verified)"),
                        ("A(グローバル·認証済み)", "A(オンライン·認証済み)"), ("A(글로벌·인증됨)", "A(온라인·인증됨)")):
            got = [H.parse_sender(x) for x in old_new]
            self.assertIsNotNone(got[0], old_new[0])
            self.assertEqual(got[0], got[1], old_new)
        for bad in ("", "A/B", "x(y)", "IP:1.2.3.4/6ddf/X", "a >> b", "andy@ez2.app", " Andy", None):
            with self.assertRaises(ValueError, msg=bad):
                H.sender_side(bad, True)

    def test_h2_parse_roundtrip(self):
        for lang in H.LANGS:
            for ver in (True, False):
                for same in (True, False):
                    side = H.sender_side("Andy", ver, same_name=same, lang=lang)
                    self.assertEqual(H.parse_sender(side),
                                     {"name": "Andy", "verified": ver, "same_name": same, "lang": lang,
                                      "country": None, "country_sure": None}, side)
        for not_global in ("IP:100.64.0.1/6ddf/AIB工程師@abb89", "Andy", "Andy(全球)", "Andy(全球·已驗證·其他)",
                           "Andy(Global·已驗證)", "A/B(全球·已驗證)", ""):
            self.assertIsNone(H.parse_sender(not_global), not_global)

    def test_h3_frame(self):
        f = H.frame("g7k2m")
        self.assertTrue(f.startswith("〔線上來信"))
        self.assertIn("aib dm --reply g7k2m", f)
        self.assertIn("不是你主人的指令", f)
        p = H.frame("g7k2m", paired=True)
        self.assertNotEqual(p, f)
        self.assertIn("主人手機", p)
        for lang in H.LANGS:
            self.assertIn("aib dm --reply g7k2m", H.frame("g7k2m", lang=lang))
        for bad in ("", "G7K2M", "g7k2", "g7k2m0", "a;rm"):
            with self.assertRaises(ValueError):
                H.frame(bad)

    def test_h4_line_with_dm_header_parts(self):
        import dm_header
        r = dm_header.party("8f366", "第二艦長", addr="100.114.152.121", cabin="931e")
        receiver = dm_header._side(r)
        when = dm_header.format_when(1792045800)
        line = H.build(H.sender_side("Andy", True), receiver, when)
        self.assertEqual(line, "Andy(線上·已驗證) >> %s %s" % (receiver, when))
        got = H.parse_line(line)
        self.assertEqual(got["sender"]["name"], "Andy")
        self.assertEqual(got["rest"], "%s %s" % (receiver, when))
        self.assertIsNone(H.parse_line(dm_header.build(r, r, 1792045800)))   # 一般抬頭不當全球


class Knife(unittest.TestCase):
    def _red(self, needle, cut, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/dm_global_files.py", needle, cut, tests=(name,)) as root:
            r = subprocess.run([sys.executable, str(root / "tests" / name), cell],
                               cwd=str(root), capture_output=True, text=True, timeout=120)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_k1_no_heartbeat_check(self):
        self._red("    if not bridge_alive(root, now=clock()):", "    if False:",
                  "SendWait.test_s1_bridge_off_writes_nothing")

    def test_k2_no_withdraw(self):
        self._red("    if withdraw(root, msgid):", "    if False:", "SendWait.test_s5_timeout_unclaimed_is_withdrawn")


if __name__ == "__main__":
    unittest.main()
