#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""對帳重掃逐行讀(10-29):_pool_rows_after 不再整檔 f.read()。

10-29 實踩:別棵樹同時壓縮共用池 ⇒ 本輪列數對不上 ⇒ test_entry 整檔重掃池 + 1.5GB 的 <池>.archive,
以前整份讀進記憶體再 json 解析,aib test 主程式長到 7.6GB 被 memguard 砍(rc 137,313 支全跑完卻沒結果)。
P1 從 offset 之後逐列回;行界只認 "\\n"(行尾 "\\r"、壞列、非 dict 列照舊處理;最後一列沒換行也回)
P2 給 repo ⇒ 只回行內有這個倉路徑的列;中文路徑兩種寫法(原字 / \\u 跳脫)都認得
P3 給 repo ⇒ 別倉的列不 json 解析(只解析命中的那幾列)
P4 30MB 的別倉封存檔:掃一遍 Python 配置峰值 < 4MB
P5 讀不到的檔 = 沒有列(不丟例外)
刀(行程內換回舊寫法):K1 整檔 f.read() ⇒ P4 量得到(峰值 ≥ 檔案大小)
"""
import json
import os
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "verify"))
os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
import jsonl_safe  # noqa: E402
import test_entry as te  # noqa: E402

HERE = "/Users/x/wt.noindex/樹-a"
OTHER = "/Users/x/wt.noindex/other"


def _old_rows_after(pool, offset, repo=None):
    """10-29 以前的寫法(刀 K1 用):整檔讀進來再切。"""
    try:
        with open(pool, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return []
    out = []
    for line in jsonl_safe.split_lines(data.decode("utf-8", "replace")):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _row(repo, test, pad=0):
    return {"test": test, "repo": repo, "key": "k", "time_utc": "2026-10-29T03:00:00Z", "tail": "x" * pad}


class Stream(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_p1_offset_and_line_rules(self):
        p = self.dir / "pool.jsonl"
        head = jsonl_safe.dumps(_row(HERE, "t0")) + "\n"
        body = (jsonl_safe.dumps(_row(HERE, "t1")) + "\r\n" + "{bad\n" + "[1,2]\n"
                + jsonl_safe.dumps(_row(HERE, "t\u2028x")) + "\n" + jsonl_safe.dumps(_row(HERE, "t3")))
        p.write_bytes((head + body).encode("utf-8"))
        got = [r["test"] for r in te._pool_rows_after(p, len(head.encode("utf-8")))]
        self.assertEqual(got, ["t1", "t\u2028x", "t3"])
        self.assertEqual(got, [r["test"] for r in _old_rows_after(p, len(head.encode("utf-8")))])

    def test_p2_repo_filter_both_encodings(self):
        p = self.dir / "pool.jsonl"
        p.write_text(jsonl_safe.dumps(_row(HERE, "raw")) + "\n"
                     + json.dumps(_row(HERE, "escaped")) + "\n"
                     + jsonl_safe.dumps(_row(OTHER, "other")) + "\n", encoding="utf-8")
        self.assertIn("\\u", p.read_text(encoding="utf-8").splitlines()[1])
        got = sorted(r["test"] for r in te._pool_rows_after(p, 0, HERE))
        self.assertEqual(got, ["escaped", "raw"])

    def test_p3_other_repo_rows_not_parsed(self):
        p = self.dir / "pool.jsonl"
        lines = [jsonl_safe.dumps(_row(OTHER, "o%d" % i)) for i in range(200)] + [jsonl_safe.dumps(_row(HERE, "mine"))]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        real = json.loads
        with mock.patch.object(te.json, "loads", side_effect=real) as m:
            got = [r["test"] for r in te._pool_rows_after(p, 0, HERE)]
        self.assertEqual(got, ["mine"])
        self.assertEqual(m.call_count, 1)

    def _peak(self, fn, p):
        tracemalloc.start()
        try:
            got = [r["test"] for r in fn(p, 0, HERE) if r.get("repo") == HERE]
            return got, tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    def _big_archive(self):
        p = self.dir / "pool.jsonl.archive"
        line = (jsonl_safe.dumps(_row(OTHER, "aib/tests/test-other.py", pad=280)) + "\n").encode("utf-8")
        with open(p, "wb") as f:
            for i in range(30 * 1024 * 1024 // len(line)):
                f.write(line)
                if i == 5000:
                    f.write((jsonl_safe.dumps(_row(HERE, "mine")) + "\n").encode("utf-8"))
        return p

    def test_p4_big_archive_bounded_memory(self):
        p = self._big_archive()
        got, peak = self._peak(te._pool_rows_after, p)
        self.assertEqual(got, ["mine"])
        self.assertLess(peak, 4 * 1024 * 1024, "peak %d bytes" % peak)

    def test_p5_unreadable_is_empty(self):
        self.assertEqual(list(te._pool_rows_after(self.dir / "missing.jsonl", 0, HERE)), [])
        self.assertEqual(list(te._pool_rows_after(self.dir, 0)), [])

    def test_k1_knife_old_read_is_caught(self):
        p = self._big_archive()
        got, peak = self._peak(_old_rows_after, p)
        self.assertEqual(got, ["mine"])
        self.assertGreaterEqual(peak, p.stat().st_size, "P4 的量法要量得到整檔讀")


if __name__ == "__main__":
    unittest.main()
