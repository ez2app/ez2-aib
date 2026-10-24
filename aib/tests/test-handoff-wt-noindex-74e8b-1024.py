#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""交接機器事實「工作樹在 Spotlight 會掃的地方」(10-24 維護者「mac 會一直掃檔案建快取」)。
Mac 的 Spotlight 掃家目錄底下的樹,測試一跑就重掃;點開頭的夾與 *.noindex 夾不掃。

N1 家目錄底下的一般夾 ⇒ 算;~/wt.noindex/x、~/.ez2-aib/worktrees/x、家目錄外 ⇒ 不算
N2 不是 Mac ⇒ 一律不算(CI 有 Linux)
N3 交接報告:有會被掃的樹 ⇒ 多一行、數字對;全在 .noindex ⇒ 沒有這行
"""
import os
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401

ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
import aib_handoff as H  # noqa: E402

NOW = 1_800_000_000


class Indexed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.realpath(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def p(self, *a):
        return os.path.join(self.home, *a)

    def test_N1_paths(self):
        f = lambda path: H._spotlight_indexed(path, self.home, "darwin")
        self.assertTrue(f(self.p("wt-x")))
        self.assertTrue(f(self.p("ez2-aib-wt", "wt-y")))
        self.assertFalse(f(self.p("wt.noindex", "wt-x")))
        self.assertFalse(f(self.p(".ez2-aib", "worktrees", "x")))
        self.assertFalse(f(self.home))
        self.assertFalse(f("/private/tmp/x"))
        self.assertFalse(f(""))

    def test_N2_not_mac(self):
        self.assertFalse(H._spotlight_indexed(self.p("wt-x"), self.home, "linux"))

    def render(self, paths):
        facts = {"schema": H.SCHEMA, "ts": NOW, "seat": {"code": "74e8b"}, "home": self.home, "platform": "darwin",
                 "worktrees": {"shown": [{"path": x, "branch": "b", "ahead": 1, "behind": 0, "dirty": 0} for x in paths],
                               "total": len(paths), "since_hours": 24}}
        return H.render_draft(facts)

    def test_N3_render(self):
        warn = H.T("aib.handoff.fact.wt_indexed", n="2")
        text = self.render([self.p("wt-a"), self.p("wt-b"), self.p("wt.noindex", "c")])
        self.assertIn(warn, text)
        self.assertNotIn(H.T("aib.handoff.fact.wt_indexed", n="3"), text)
        text = self.render([self.p("wt.noindex", "c")])
        self.assertNotIn("wt.noindex/<", text.split("handoff:facts")[1])


if __name__ == "__main__":
    unittest.main()
