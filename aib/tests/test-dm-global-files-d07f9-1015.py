#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球 dm 核心↔轉接程式的檔案契約(src/cockpit/dm_global_files.py;開發文件 v0.4 第 22.2 節,PR-3)。

F1–F3 outbox:寫入權限、重複、欄位檢查、位址格式。
F4    receipts / delivery:逐行附加、壞行略過、併發附加不交錯、終局判斷。
F5–F6 inbox 與入佇列欄位:身分只由核心決定 —— 外掛自帶 sender_kind 拒收、永遠不是 human、
      未驗證當陌生、陌生待審且降一般;顯示名稱(Andy 10-15 15:4x)→ 人格名 → npub 前 12 碼。
F7–F8 待處理清單(壞檔搬 bad/)與保留天數清理。
K1–K2 刀測:拿掉「外掛不能自帶 sender_kind」/「未驗證當陌生」⇒ 對應格變紅。
全程只寫暫存資料夾,不碰真的 ~/.ez2-aib,不連網。
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "tests"))
import dm_global_files as G  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402

NPUB_OWNER = "npub1" + "q" * 58
NPUB_SEAT = "npub1" + "p" * 58


def outbox(**over):
    rec = {"v": 1, "msgid": G.new_msgid(), "created_at": 1792045000, "op": "send",
           "from": {"cabin": "6ddf", "seat": "abb89", "persona_uid": "pryue0", "attested": True},
           "to": NPUB_OWNER + "/931e/8f366", "content": "你好", "priority": "normal"}
    rec.update(over)
    return rec


def inbox(**over):
    rec = {"v": 1, "msgid": G.new_msgid(), "received_at": 1792045000, "to_cabin": "931e", "to_seat": "8f366",
           "from_npub": NPUB_SEAT, "from_addr": NPUB_OWNER + "/6ddf/abb89", "display_name": "Andy",
           "persona_name": "AIB工程師", "verified": True, "class": "friend", "content": "你好",
           "priority": "urgent"}
    rec.update(over)
    return rec


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-gdm-")
        self.addCleanup(td.cleanup)
        self.root = G.ensure_dirs(Path(td.name) / "chat-bridge")


class Outbox(Base):
    def test_f1_write_modes_and_duplicate(self):
        rec = outbox()
        p = G.write_outbox(self.root, rec)
        self.assertEqual(p, self.root / "outbox" / (rec["msgid"] + ".json"))
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        for sub in G.BOXES + ("bad",):
            self.assertEqual(stat.S_IMODE((self.root / sub).stat().st_mode), 0o700, sub)
        self.assertEqual(json.loads(p.read_text(encoding="utf-8")), rec)
        with self.assertRaises(G.ContractError):
            G.write_outbox(self.root, rec)

    def test_f2_field_checks(self):
        bad = [
            dict(msgid="../../etc/passwd"), dict(msgid="ABCDEF" + "0" * 26), dict(v=2),
            dict(op="delete"), dict(content=""), dict(content="字" * (G.CONTENT_MAX + 1)),
            dict(priority="critical"), dict(created_at=-1), dict(created_at=True),
            dict(to="6ddf/abb89"), dict(to="andy@ez2.app/6ddf/abb89"), dict(to="100.64.0.1/6ddf/abb89"),
            dict(reply_to="nope"), dict(expires="tomorrow"),
        ]
        for over in bad:
            with self.assertRaises(G.ContractError, msg=over):
                G.check_outbox(outbox(**over))
        r = outbox(**{"from": {"cabin": "6ddf", "seat": "abb89"}})
        with self.assertRaises(G.ContractError):
            G.check_outbox(r)   # 少了 attested
        # --dry-run 的解析請求可以沒有內容
        r = outbox(op="resolve")
        del r["content"]
        G.check_outbox(r)
        G.check_outbox(outbox(content="字" * G.CONTENT_MAX))

    def test_f3_addresses(self):
        self.assertEqual(G.parse_address(NPUB_OWNER + "/931e/8f366"),
                         {"owner": NPUB_OWNER, "cabin": "931e", "seat": "8f366"})
        self.assertEqual(G.parse_address(NPUB_SEAT), {"seat_npub": NPUB_SEAT})
        for a in ("npub1" + "b" * 58, "npub1short/931e/8f366", NPUB_OWNER + "/931e", NPUB_OWNER + "/931E/8f366",
                  "", None, "andy@ez2.app/6ddf/abb89"):
            self.assertIsNone(G.parse_address(a), a)


class Lines(Base):
    def test_f4_receipts_append_read_final(self):
        mid = G.new_msgid()
        self.assertEqual(G.read_receipts(self.root, mid), [])
        G.append_receipt(self.root, mid, {"v": 1, "at": 1, "stage": "relayed"})
        G.append_receipt(self.root, mid, {"v": 1, "at": 2, "stage": "queued", "pos": 3})
        self.assertIsNone(G.final_receipt(G.read_receipts(self.root, mid)))
        p = self.root / "receipts" / (mid + ".jsonl")
        with open(p, "ab") as f:
            f.write(b'{"broken\n{"v":1,"at":3,"stage":"teleported"}\n')
        G.append_receipt(self.root, mid, {"v": 1, "at": 4, "stage": "delivered"})
        got = G.read_receipts(self.root, mid)
        self.assertEqual([r["stage"] for r in got], ["relayed", "queued", "delivered"])
        self.assertEqual(G.final_receipt(got)["stage"], "delivered")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        with self.assertRaises(G.ContractError):
            G.append_receipt(self.root, mid, {"v": 1, "at": 5, "stage": "teleported"})
        with self.assertRaises(G.ContractError):
            G.append_delivery(self.root, mid, {"v": 1, "at": 5, "state": "relayed"})
        G.append_delivery(self.root, mid, {"v": 1, "at": 5, "state": "read"})
        self.assertEqual([r["state"] for r in G.read_delivery(self.root, mid)], ["read"])

    def test_f4b_concurrent_appends_do_not_interleave(self):
        mid = G.new_msgid()
        detail = "x" * 3000

        def work(i):
            for j in range(25):
                G.append_receipt(self.root, mid, {"v": 1, "at": i * 100 + j, "stage": "relayed", "detail": detail})

        ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(len(G.read_receipts(self.root, mid)), 200)


class Inbox(Base):
    def test_f5_sender_kind_from_plugin_refused(self):
        for kind in ("human", "external_seat", "seat"):
            with self.assertRaises(G.ContractError) as cm:
                G.check_inbox(inbox(sender_kind=kind))
            self.assertIn("sender_kind", str(cm.exception))
        with self.assertRaises(G.ContractError):
            G.write_inbox(self.root, inbox(sender_kind="human"))
        self.assertEqual(os.listdir(self.root / "inbox"), [])

    def test_f5b_queue_fields(self):
        q = G.queue_fields(inbox())
        self.assertEqual((q["sender_kind"], q["status"], q["priority"], q["class"]),
                         ("external_seat", "queued", "urgent", "friend"))
        self.assertEqual(q["sender_origin"], "nostr:" + NPUB_SEAT)
        self.assertEqual(q["sender_addr"], NPUB_OWNER + "/6ddf/abb89")
        self.assertEqual(q["shown_name"], "Andy")
        self.assertEqual(G.queue_fields(inbox(**{"class": "paired"}))["sender_kind"], "owner_paired")
        st = G.queue_fields(inbox(**{"class": "stranger"}))
        self.assertEqual((st["status"], st["priority"]), ("hold_human", "normal"))

    def test_f5c_unverified_is_stranger(self):
        for cls in G.INBOX_CLASSES:
            q = G.queue_fields(inbox(verified=False, **{"class": cls}))
            self.assertEqual((q["class"], q["status"], q["priority"], q["sender_kind"]),
                             ("stranger", "hold_human", "normal", "external_seat"), cls)

    def test_f5d_never_human(self):
        for cls in G.INBOX_CLASSES:
            for ver in (True, False):
                for prio in G.PRIORITIES:
                    q = G.queue_fields(inbox(verified=ver, priority=prio, **{"class": cls}))
                    self.assertIn(q["sender_kind"], ("external_seat", "owner_paired"))

    def test_f6_display_name(self):
        ok = {"Andy": "Andy", "  安迪  ": "安迪", "Andy​\nLin": "AndyLin", "a  b": "a b", "店長 小美": "店長 小美"}
        for raw, want in ok.items():
            self.assertEqual(G.clean_display_name(raw), want, raw)
        for raw in ("", "   ", "x" * 33, "IP:100.114.152.121/6ddf/AIB工程師", "Andy >> 你", "andy@ez2.app",
                    "Andy(全球·已驗證)", "Andy(全球·已驗證)", None, 42):
            self.assertIsNone(G.clean_display_name(raw), raw)
        # 顯示名稱 → 人格名 → npub 前 12 碼
        self.assertEqual(G.queue_fields(inbox(display_name="IP:1.2.3.4/6ddf/X"))["shown_name"], "AIB工程師")
        self.assertEqual(G.queue_fields(inbox(display_name=None))["shown_name"], "AIB工程師")
        r = inbox()
        del r["display_name"], r["persona_name"]
        self.assertEqual(G.queue_fields(r)["shown_name"], NPUB_SEAT[:12])


class Housekeeping(Base):
    def test_f7_pending_moves_bad(self):
        good = [outbox(), outbox()]
        for r in good:
            G.write_outbox(self.root, r)
        d = self.root / "outbox"
        (d / ("%s.json" % G.new_msgid())).write_text("{not json", encoding="utf-8")
        other = outbox()
        (d / ("%s.json" % G.new_msgid())).write_text(json.dumps(other), encoding="utf-8")   # 檔名與 msgid 不符
        (d / ".tmp-half").write_text("{", encoding="utf-8")
        (d / "note.txt").write_text("x", encoding="utf-8")
        got = G.pending(self.root, "outbox")
        self.assertEqual(sorted(m for m, _, _ in got), sorted(r["msgid"] for r in good))
        self.assertEqual(len(os.listdir(self.root / "bad")), 2)
        self.assertTrue((d / ".tmp-half").exists())
        G.write_inbox(self.root, inbox())
        self.assertEqual(len(G.pending(self.root, "inbox")), 1)
        with self.assertRaises(G.ContractError):
            G.pending(self.root, "receipts")

    def test_f8_sweep(self):
        old, new = G.new_msgid(), G.new_msgid()
        for m in (old, new):
            G.append_receipt(self.root, m, {"v": 1, "at": 1, "stage": "relayed"})
        G.write_outbox(self.root, outbox(msgid=old))
        past = time.time() - 8 * 86400
        os.utime(self.root / "receipts" / (old + ".jsonl"), (past, past))
        os.utime(self.root / "outbox" / (old + ".json"), (past, past))
        self.assertEqual(G.sweep(self.root), 1)
        self.assertEqual(os.listdir(self.root / "receipts"), [new + ".jsonl"])
        self.assertTrue((self.root / "outbox" / (old + ".json")).exists())   # 待處理的不清

    def test_f9_default_root(self):
        self.assertEqual(G.default_root({"HOME": "/h"}), Path("/h/.ez2-aib/state/chat-bridge"))
        self.assertEqual(G.default_root({"HOME": "/h", "AIB_CHAT_BRIDGE_DIR": "/x"}), Path("/x"))


class Knife(unittest.TestCase):
    def _red(self, needle, cut, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/dm_global_files.py", needle, cut, tests=(name,)) as root:
            r = subprocess.run([sys.executable, str(root / "tests" / name), cell],
                               cwd=str(root), capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_k1_plugin_supplied_sender_kind(self):
        self._red('    if "sender_kind" in rec:', '    if False:', "Inbox.test_f5_sender_kind_from_plugin_refused")

    def test_k2_unverified_as_stranger(self):
        self._red('cls = rec["class"] if rec["verified"] else "stranger"', 'cls = rec["class"]',
                  "Inbox.test_f5c_unverified_is_stranger")


if __name__ == "__main__":
    unittest.main()
