#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""工作樹不准開在 Mac 的 Spotlight 會掃的地方(10-24 維護者「mac 會一直掃檔案建快取」→「不是提醒，是用程式必要規定」)。
Spotlight 掃家目錄底下的一般夾;點開頭的夾與 *.noindex 夾不掃。

N1 家目錄底下的一般夾 ⇒ 算;~/wt.noindex/x、~/.ez2-aib/worktrees/x、家目錄外、捷徑指到 .noindex ⇒ 不算
N2 不是 Mac ⇒ 一律不算(CI 有 Linux)
N3 交接報告：有會被掃的樹 ⇒ 多一行、數字對;全在 .noindex ⇒ 沒有這行
G1 守門認得：worktree add(-b 帶值)/ move / clone(有無目的夾、-C、cd、~ / $HOME、timeout 包著、&& 串)
G2 守門放行：目標在 .noindex / 暫存夾、認不出的變數、只是 grep / git log、不是 Mac
G3 aib-now-gate pre-tool:擋 ⇒ rc 2 + 訊息有路徑與建議;放行 rc 0;守門壞掉 ⇒ fail-open rc 0
G4 安裝：add_ours 多一條 PreToolUse(Bash)pre-tool;舊安裝(沒有那條)is_installed = False ⇒ ensure 會補裝
V1 交接驗證：本席工作樹在會被掃的地方 ⇒ 缺項(擋交接);在 .noindex ⇒ 不缺
"""
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401

ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
sys.path.insert(0, str(ROOT / "src" / "policy" / "hooks"))
import aib_handoff as H  # noqa: E402
import handoff_check as HC  # noqa: E402
import spotlight_paths as SP  # noqa: E402
import worktree_path_gate as G  # noqa: E402

_spec = importlib.util.spec_from_file_location("aib_now_gate", ROOT / "src" / "policy" / "hooks" / "aib-now-gate.py")
NG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(NG)

NOW = 1_800_000_000


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.realpath(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def p(self, *a):
        return os.path.join(self.home, *a)


class Paths(Base):
    def test_N1_paths(self):
        f = lambda path: SP.indexed(path, self.home, "darwin")
        self.assertTrue(f(self.p("wt-x")))
        self.assertTrue(f(self.p("ez2-aib-wt", "wt-y")))
        self.assertFalse(f(self.p("wt.noindex", "wt-x")))
        self.assertFalse(f(self.p(".ez2-aib", "worktrees", "x")))
        self.assertFalse(f(self.home))
        self.assertFalse(f("/private/tmp/x"))
        self.assertFalse(f(""))
        os.makedirs(self.p("wt.noindex", "real"))
        os.symlink(self.p("wt.noindex", "real"), self.p("link"))
        self.assertFalse(f(self.p("link")), "捷徑指到 .noindex 照實際位置算")

    def test_N2_not_mac(self):
        self.assertFalse(SP.indexed(self.p("wt-x"), self.home, "linux"))

    def render(self, paths):
        facts = {"schema": H.SCHEMA, "ts": NOW, "seat": {"code": "74e8b"}, "home": self.home, "platform": "darwin",
                 "worktrees": {"shown": [{"path": x, "branch": "b", "ahead": 1, "behind": 0, "dirty": 0} for x in paths],
                               "total": len(paths), "since_hours": 24}}
        return H.render_draft(facts)

    def test_N3_render(self):
        text = self.render([self.p("wt-a"), self.p("wt-b"), self.p("wt.noindex", "c")])
        self.assertIn(H.T("aib.handoff.fact.wt_indexed", n="2"), text)
        text = self.render([self.p("wt.noindex", "c")])
        self.assertNotIn(H.T("aib.handoff.fact.wt_indexed", n="1"), text)


class Gate(Base):
    def t(self, cmd, cwd=None):
        return G.targets(cmd, cwd or self.p("repo"), self.home)

    def hit(self, cmd, cwd=None, platform="darwin"):
        return G.pre_tool({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": cwd or self.p("repo")},
                          home=self.home, platform=platform)

    def test_G1_targets(self):
        self.assertEqual(self.t("git worktree add -b feat/x ~/wt-x origin/main"), [self.p("wt-x")])
        self.assertEqual(self.t("git -C ~/repo worktree add ../wt-y"), [self.p("wt-y")])
        self.assertEqual(self.t("git worktree move ~/wt-a $HOME/wt-b"), [self.p("wt-b")])
        self.assertEqual(self.t("cd ~ && git clone https://example.invalid/o/r.git"), [self.p("r")])
        self.assertEqual(self.t("git clone --depth 1 -b main git@example.invalid:o/r2.git ${HOME}/r2c"), [self.p("r2c")])
        self.assertEqual(self.t("git fetch -q && timeout 60 git worktree add ~/w3"), [self.p("w3")])
        self.assertEqual(self.t("FOO=1 git worktree add\n~/w4"), [], "換行切開的不是同一個指令")
        for cmd in ("git worktree add -b x ~/wt-x", "cd ~ && git clone https://example.invalid/r", "git worktree move a ~/b"):
            self.assertTrue(self.hit(cmd), cmd)
        h = self.hit("git worktree add ~/wt-z")
        self.assertEqual((h["paths"], h["suggest"]), ([self.p("wt-z")], "~/wt.noindex/wt-z"))

    def test_G2_allow(self):
        for cmd in ("git worktree add ~/wt.noindex/x", "git worktree add /private/tmp/claude-501/x/scratchpad/y",
                    "git clone --no-hardlinks ~/wt.noindex/a /private/tmp/q/b", "git worktree add $WT/x",
                    "grep -n worktree add notes.md", "git log --oneline -1", "git worktree list",
                    "git clone https://example.invalid/r"):
            self.assertIsNone(self.hit(cmd, cwd="/private/tmp/q"), cmd)
        self.assertIsNone(self.hit("git worktree add ~/wt-x", platform="linux"))
        self.assertIsNone(G.pre_tool({"tool_name": "Edit", "tool_input": {"command": "git worktree add ~/x"}},
                                     home=self.home, platform="darwin"))

    def run_main(self, payload):
        err = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), mock.patch.object(sys, "stderr", err):
            rc = NG.main(["pre-tool"])
        return rc, err.getvalue()

    def test_G3_now_gate(self):
        bad = {"tool_name": "Bash", "tool_input": {"command": "git worktree add ~/wt-q"}, "cwd": self.home}
        orig = G.pre_tool
        with mock.patch.object(G, "pre_tool", side_effect=lambda p: orig(p, home=self.home, platform="darwin")):
            rc, err = self.run_main(bad)
            self.assertEqual(rc, 2)
            self.assertIn(self.p("wt-q"), err)
            self.assertIn("~/wt.noindex/wt-q", err)
            rc, _ = self.run_main(dict(bad, tool_input={"command": "git worktree add ~/wt.noindex/q"}))
            self.assertEqual(rc, 0)
        with mock.patch.object(G, "pre_tool", side_effect=RuntimeError("壞了")), \
                mock.patch.object(NG, "_note", lambda *a: None):
            self.assertEqual(self.run_main(bad)[0], 0, "守門壞掉 ⇒ 放行")

    def test_G4_install(self):
        cfg = NG.add_ours({})
        pre = cfg["hooks"]["PreToolUse"]
        self.assertEqual(pre[0]["matcher"], "Bash")
        self.assertTrue(pre[0]["hooks"][0]["command"].rstrip().endswith(" pre-tool"))
        old = json.loads(json.dumps(cfg))
        del old["hooks"]["PreToolUse"]
        self.assertFalse(NG.is_installed(old, None), "舊安裝少 pre-tool ⇒ ensure 會補")
        self.assertTrue(NG.is_installed(cfg, None))


class Verify(Base):
    def test_V1_reconcile(self):
        def facts(path):
            return {"home": self.home, "platform": "darwin", "seat": {"code": "74e8b"},
                    "worktrees": {"shown": [{"path": path, "mine": True}]}}
        secs = {"s2": "- 工作樹 %s 保留\n- 工作樹 %s 保留" % (self.p("wt-a"), self.p("wt.noindex", "wt-a"))}
        miss = HC.reconcile(facts(self.p("wt-a")), dict(secs))
        self.assertIn(HC.T("aib.handoff.check.wt_indexed", path=self.p("wt-a")), miss)
        miss = HC.reconcile(facts(self.p("wt.noindex", "wt-a")), dict(secs))
        self.assertFalse([m for m in miss if "Spotlight" in m], miss)


if __name__ == "__main__":
    unittest.main()
