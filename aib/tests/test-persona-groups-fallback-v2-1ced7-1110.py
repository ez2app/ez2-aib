#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""`aib persona groups` 的 Python 相容入口(找不到 Rust aib-persona 時才走)遇到看不懂的行要拒算。

043dd #627 M1:Rust 版寫的 evidence/v2 與 void 行,舊的 count_groups 看不懂:void 行被當壞 lane,
它作廢的那筆照樣算群;同一本帳有沒有 Rust 會得到不同群數 ⇒ 剛撤回的證據又讓特質過門檻。
G1 帳裡只有 v1 行 ⇒ rc 0,照舊計數(2 群)
G2 帳裡有 void 行 ⇒ rc 2、stderr 提示要裝 Rust 版,stdout 不印群數
G3 帳裡有 evidence/v2 行 ⇒ rc 2
刀 K1 拿掉拒算那段 ⇒ G2 紅(rc 0、印出 groups)。

在行程內呼叫 groups(),候選根、狀態根、HOME 都指到暫存夾,不碰真的 ~/.ez2 / ~/.ez2-aib。
"""
import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # = aib/
PE = ROOT / "src" / "persona_extract"


def v1(lane, day, pointer):
    return {"schema": "aib-persona-evidence/v1", "persona_code": "p1", "lane": lane, "day": day,
            "pointer": pointer, "pointer_kind": "file_line", "session": None, "note": "",
            "at_ms": 1}


class FallbackGroups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.env = {"EZ2_PERSONA_CANDIDATE_ROOT": str(t / "cand"),
                    "EZ2_AIB_STATE_ROOT": str(t / "state"), "HOME": str(t / "home"),
                    "EZ2_AIB_LOCALE": "en"}
        self.saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        (t / "cand" / "p1").mkdir(parents=True)
        self.ledger = t / "cand" / "p1" / "evidence.jsonl"
        if str(PE) not in sys.path:
            sys.path.insert(0, str(PE))
        import persona_extract
        self.pe = persona_extract

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def run_groups(self, rows):
        self.ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.pe.groups(argparse.Namespace(persona_code="p1"))
        return rc, out.getvalue(), err.getvalue()

    def test_g1_v1_only_counts_as_before(self):
        rc, out, _ = self.run_groups([v1("a", "2026-11-09", "docs/a.md:3"),
                                      v1("b", "2026-11-10", "docs/b.md:4")])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["groups"], 2)

    def test_g2_a_void_row_is_refused(self):
        rows = [v1("a", "2026-11-09", "docs/a.md:3"), v1("b", "2026-11-10", "docs/b.md:4"),
                {"schema": "aib-persona-evidence-void/v1", "void_of": "e2-0123456789ab",
                 "by": "seat:1ced7", "why": "wrong pointer"}]
        rc, out, err = self.run_groups(rows)
        self.assertEqual(rc, 2, out)
        self.assertEqual(out, "")
        self.assertIn("aib-persona-evidence-void/v1", err)

    def test_g3_a_v2_row_is_refused(self):
        rows = [v1("a", "2026-11-09", "docs/a.md:3"),
                {"schema": "aib-persona-evidence/v2", "persona_code": "p1", "lane": "b",
                 "day": "2026-11-10", "pointer": "~/x.md:1", "by": "seat:1ced7"}]
        rc, out, _ = self.run_groups(rows)
        self.assertEqual(rc, 2, out)


if __name__ == "__main__":
    unittest.main()
