#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""一行 Bash 不准關掉真的 tmux 伺服器(11-07 維護者「開 PR 讓腳本從源頭擋掉」)。
事故：沙盒腳本靠 TMUX_TMPDIR 指沙盒夾，夾被刪後退回真預設 socket,裸 `tmux kill-server` 砍了兩艙座艙。

K1 擋:裸 kill-server(含 TMUX_TMPDIR= 前綴、env -u 包著、&& / ; 串、heredoc 寫進腳本)、事故原句、
   -L default / aihub-<艙號> / aihub-$變數、-S 真 socket 夾的 default / aihub-<艙號>、killall / pkill tmux
K2 放行:-S 沙盒檔 / 帶變數、-L 唯一名、引號裡的字、註解、grep、tmux ls / kill-session、不是 Bash 工具
K3 aib-now-gate pre-tool:擋 ⇒ rc 2 + 訊息有那段指令與原因;放行 rc 0;守門壞掉 ⇒ fail-open rc 0;
   worktree 守門跟 tmux 守門共用同一份 stdin(第二道還讀得到 payload)
K4 四語字典都有新鍵、佔位一致
"""
import importlib.util
import io
import json
import os
import pathlib
import re
import sys
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401

ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
sys.path.insert(0, str(ROOT / "src" / "policy" / "hooks"))
import tmux_kill_gate as G  # noqa: E402

_spec = importlib.util.spec_from_file_location("aib_now_gate", ROOT / "src" / "policy" / "hooks" / "aib-now-gate.py")
NG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(NG)

UID = os.getuid() if hasattr(os, "getuid") else 0
REAL = "/private/tmp/tmux-%d" % UID
HOME = "/Users/someone"


def why(cmd):
    hit = G.check(cmd, HOME)
    return hit[0] if hit else None


class Block(unittest.TestCase):
    def test_K1_block(self):
        cases = {
            "tmux kill-server": "bare",
            "TMUX_TMPDIR=/private/tmp/sbx/gone tmux kill-server": "bare",
            "env -u TMUX tmux kill-server": "bare",
            "cd /x && tmux -2 kill-server": "bare",
            # 11-07 事故原句(@043dd)
            "S=/private/tmp/q/heat1; . $S/env.sh; tmux kill-server; tmux -L aihub-$EZ2_AIB_INSTANCE kill-server": "bare",
            "tmux -L aihub-$EZ2_AIB_INSTANCE kill-server": "real_name",
            "tmux -L default kill-server": "real_name",
            "tmux -Ldefault kill-server": "real_name",
            "env -u TMUX tmux -L aihub-20261104-121013-f457 kill-server": "real_name",
            "tmux -S %s/default kill-server" % REAL: "real_socket",
            "tmux -S /tmp/tmux-%d/aihub-20261106-233709-9320 kill-server" % UID: "real_socket",
            "killall tmux": "killall",
            "pkill -x tmux": "killall",
            "timeout 30 pkill -u %d tmux" % UID: "killall",
        }
        for cmd, w in cases.items():
            self.assertEqual(why(cmd), w, cmd)

    def test_K1_heredoc_script(self):
        cmd = "cat > $S/lat.sh <<'EOF'\n#!/bin/bash\n# don't forget\ntmux kill-server\nEOF\nbash $S/lat.sh"
        self.assertEqual(why(cmd), "bare", "寫進腳本的裸 kill-server 之後會跑 ⇒ 擋")
        h = G.pre_tool({"tool_name": "Bash", "tool_input": {"command": cmd}}, home=HOME)
        self.assertEqual((h["why"], h["cmd"]), ("bare", "tmux kill-server"))

    def test_K2_allow(self):
        for cmd in (
            'for s in $SBT/t/tmux-501/*; do env -u TMUX tmux -S "$s" kill-server 2>/dev/null; done',
            "tmux -S /private/tmp/claude-501/x/scratchpad/sock kill-server",
            "tmux -S %s/aihub-probe-x kill-server" % REAL,
            "env -u TMUX tmux -L aihub-probe-a63ab-killserver kill-server",
            "tmux -L sbx0001 kill-server",
            'echo "tmux kill-server"',
            "grep -n 'kill-server' src/cockpit/*.sh",
            "tmux ls; tmux kill-session -t aib-x",
            "# tmux kill-server",
            "pkill -f 'tmux -L aihub-probe-x'",
            "git log --oneline -1",
        ):
            self.assertIsNone(why(cmd), cmd)
        self.assertIsNone(G.pre_tool({"tool_name": "Write", "tool_input": {"command": "tmux kill-server"}}))
        self.assertIsNone(G.pre_tool({"tool_name": "Bash", "tool_input": {"command": "ls"}}))


class NowGate(unittest.TestCase):
    def run_main(self, payload):
        err = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), mock.patch.object(sys, "stderr", err):
            rc = NG.main(["pre-tool"])
        return rc, err.getvalue()

    def test_K3_now_gate(self):
        bad = {"tool_name": "Bash", "tool_input": {"command": "cd /x; tmux kill-server"}, "cwd": "/x"}
        rc, err = self.run_main(bad)
        self.assertEqual(rc, 2, "第二道守門要讀得到同一份 payload")
        self.assertIn("tmux kill-server", err)
        self.assertIn("-S", err)
        rc, _ = self.run_main(dict(bad, tool_input={"command": "tmux -S /private/tmp/q/s kill-server"}))
        self.assertEqual(rc, 0)
        with mock.patch.object(G, "pre_tool", side_effect=RuntimeError("壞了")), \
                mock.patch.object(NG, "_note", lambda *a: None):
            self.assertEqual(self.run_main(bad)[0], 0, "守門壞掉 ⇒ 放行")


class Dict(unittest.TestCase):
    def test_K4_i18n(self):
        keys = ["aib.nowgate.tmux_kill"] + ["aib.nowgate.tmux_kill." + k for k in ("bare", "real_name", "real_socket", "killall")]
        ph = {}
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8"))
            for k in keys:
                self.assertIn(k, d, "%s 缺 %s" % (lang, k))
                ph.setdefault(k, set()).add(tuple(sorted(re.findall(r"\{(\w+)\}", d[k]))))
        for k, v in ph.items():
            self.assertEqual(len(v), 1, "%s 四語佔位不一致:%s" % (k, v))


if __name__ == "__main__":
    unittest.main()
