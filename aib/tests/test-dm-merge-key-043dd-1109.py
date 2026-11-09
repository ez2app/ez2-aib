#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""dm 合併鍵 = PR 號 + 頭碼(7c,a63ab 11-09 06:03;dm 優化第 2 條,043dd)。

`merge_groups` 是 stage-tidy 交給 AI 的分組，也是 `done --group` 一起結案的那一組。以前只看同寄件人 +
(同 thread 或正文 ≥ 0.85 相近):「請審 #578 頭 A」和「請審 #578 頭 B」幾乎一字不差 ⇒ 併成一組，結一封就把另一個頭碼的
審查請求一起結掉。現在兩封都寫了 PR 號 + 頭碼 ⇒ 鍵一樣才合、不一樣一律不合;只有一封有鍵或都沒有 ⇒ 照舊。純函式，不碰檔案。

M1 抽鍵:pull/578 與 #578 同號;頭碼 7 碼和 8 碼同一個;信 id(q_<16 碼>)、日期、英文字不算頭碼;缺 PR 或缺頭碼 ⇒ None。
M2 同 thread、同 PR、不同頭碼 ⇒ 兩組(沒有 7c 時 thread 會合)。
M3 正文 ≥ 0.85 相近、不同頭碼 ⇒ 兩組。
M4 同 PR 同頭碼、正文不同(短頭碼 7 碼 vs 8 碼)⇒ 一組，代表是最新那封。
M5 遞移:A(頭 X)— B(沒鍵)— C(頭 Y)同 thread ⇒ A、C 不同組。
M6 不同寄件人、同鍵 ⇒ 不合(照舊)。
M7 都沒鍵 ⇒ 照舊(同 thread 合、不同 thread 不相近不合)。
M8 長訊指標信用落檔內容抽鍵;落檔讀不到 ⇒ 不參與相近合併(照舊)。
M9 交給 AI 的那一列列出組內每封原 id + rev(tidy_wake_text)。
刀(複本 + run_guarded):K1 拿掉鍵的判斷 ⇒ M2 紅;K2 合組不看鍵衝突 ⇒ M5 紅;K3 頭碼比全長 ⇒ M4 紅;
K4 頭碼前面接 _ 也算 ⇒ M1 信 id 那支紅。刀一律斷言「FAIL: <目標>」且沒有 ERROR。
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

import dm_triage as TR  # noqa: E402

A, B = "aaaaa", "bbbbb"
REQ = "請審 https://github.com/ez2app/ez2-aib/pull/578 頭 %s(平台對等 Lifecycle trait;設計 DESIGN-platform-parity.md)。本機未跑、以 CI 為準。"


def row(i, msg, sender=A, thread=None, ts=None, rev=1):
    r = {"id": "q_%016x" % i, "rev": rev, "sender_seat": sender, "sender": "@" + sender, "msg": msg,
         "ts": 1792930000 + (ts if ts is not None else i)}
    if thread:
        r["thread"] = thread
    return r


def ids(groups):
    return [[r["id"] for r in g] for g in groups]


class M(unittest.TestCase):
    def test_m1_key_pr_and_head(self):
        k = TR.pr_head_key(REQ % "8a51908c")
        self.assertEqual(k, (("578",), ("8a51908",)))
        self.assertEqual(TR.pr_head_key("#578 頭 8a51908 已合"), k)
        self.assertIsNone(TR.pr_head_key("#578 已合 main"))                  # 沒頭碼
        self.assertIsNone(TR.pr_head_key("頭 8a51908c 已推"))                 # 沒 PR 號
        self.assertIsNone(TR.pr_head_key("#578 20261109 decade facade"))      # 全數字 / 全字母都不算

    def test_m1_message_id_is_not_a_head(self):
        self.assertIsNone(TR.pr_head_key("#578 請看 q_acac381dafff6490 那封"))

    def test_m2_same_thread_different_heads_not_merged(self):
        rows = [row(1, REQ % "8a51908c", thread="pr578"), row(2, REQ % "d91ddb5c", thread="pr578")]
        self.assertEqual(ids(TR.merge_groups(rows)), [[rows[0]["id"]], [rows[1]["id"]]])

    def test_m3_similar_text_different_heads_not_merged(self):
        rows = [row(1, REQ % "8a51908c"), row(2, REQ % "e32ceba1")]
        self.assertGreaterEqual(len(REQ), 40)
        self.assertEqual(len(TR.merge_groups(rows)), 2)

    def test_m4_same_pr_same_head_merged(self):
        rows = [row(1, REQ % "8a51908c"), row(2, "#578 頭 8a51908 CI 兩格綠了，可以合")]
        g = TR.merge_groups(rows)
        self.assertEqual(ids(g), [[rows[0]["id"], rows[1]["id"]]])
        self.assertEqual(g[0][-1]["id"], rows[1]["id"])                       # 代表 = 最新

    def test_m5_no_merge_through_a_keyless_message(self):
        rows = [row(1, REQ % "8a51908c", thread="t"), row(2, "上一封補充:設計檔已更新", thread="t"),
                row(3, REQ % "d91ddb5c", thread="t")]
        g = {r["id"]: n for n, grp in enumerate(TR.merge_groups(rows)) for r in grp}
        self.assertNotEqual(g[rows[0]["id"]], g[rows[2]["id"]])

    def test_m6_different_senders_same_key_not_merged(self):
        rows = [row(1, REQ % "8a51908c", sender=A), row(2, REQ % "8a51908c", sender=B)]
        self.assertEqual(len(TR.merge_groups(rows)), 2)

    def test_m7_no_key_unchanged(self):
        rows = [row(1, "機器空了，你可以跑", thread="lease"), row(2, "機器我拿了", thread="lease"),
                row(3, "今天的週報在這裡，請看第三節")]
        self.assertEqual(ids(TR.merge_groups(rows)), [[rows[0]["id"], rows[1]["id"]], [rows[2]["id"]]])

    def test_m8_relay_content_gives_the_key(self):
        rows = [row(1, "長訊見 /x/RELAY-1.txt"), row(2, "長訊見 /x/RELAY-2.txt")]
        body = {"長訊見 /x/RELAY-1.txt": REQ % "8a51908c", "長訊見 /x/RELAY-2.txt": REQ % "d91ddb5c"}
        self.assertEqual(len(TR.merge_groups(rows, relay_text=lambda m: body.get(m, False))), 2)
        same = {"長訊見 /x/RELAY-1.txt": REQ % "8a51908c", "長訊見 /x/RELAY-2.txt": "#578 8a51908c 補一句"}
        self.assertEqual(len(TR.merge_groups(rows, relay_text=lambda m: same.get(m, False))), 1)
        # 讀不到落檔 ⇒ 不參與相近合併(指標句彼此只差檔名)
        self.assertEqual(len(TR.merge_groups(rows, relay_text=lambda m: None)), 2)

    def test_m9_merged_row_lists_every_original_id_and_rev(self):
        need = [{"id": "q_3", "rev": 2, "from": "@a", "sender_kind": "seat", "summary": "請審 #578",
                 "merged": [{"id": "q_1", "rev": 1}, {"id": "q_2", "rev": 4}]}]
        text = TR.tidy_wake_text(A, need, 1, "/x/dm_queue.py")
        for want in ("q_3 rev=2", "q_1 rev=1", "q_2 rev=4"):
            self.assertIn(want, text)


REL = "src/cockpit/dm_triage.py"
KNIVES = [
    ("K1-no-key-check", REL,
     "            if keys[ia] is not None and keys[ib] is not None:\n",
     "            if False:\n",
     "M.test_m2_same_thread_different_heads_not_merged"),
    ("K2-union-ignores-key", REL,
     "if kx is not None and ky is not None and kx != ky:",
     "if False:",
     "M.test_m5_no_merge_through_a_keyless_message"),
    ("K3-full-length-head", REL,
     "HEAD_KEY_CHARS = 7 ",
     "HEAD_KEY_CHARS = 40 ",
     "M.test_m4_same_pr_same_head_merged"),
    ("K4-underscore-head", REL,
     '(?<![0-9A-Za-z_])[0-9a-f]{7,40}',
     '(?<![0-9A-Za-z])[0-9a-f]{7,40}',
     "M.test_m1_message_id_is_not_a_head"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=180)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
