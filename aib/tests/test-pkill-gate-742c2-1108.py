#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""禁用 pkill -f / killall(11-08 維護者選嚴格版)。
事故 00:34:59:背景 job 跑 `pkill -f "x" -U $(id -u) -P $(pgrep …)`。macOS 的 pkill 是 BSD getopt,
遇到第一個比對字串就停止解析 ⇒ -U / 501 / -P 全變成比對字串;tmux 命令列都帶 -P ⇒ 24 個 tmux 全被砍。

P1 擋:事故原句、pkill 任何寫法(-f / -U / -P 1 / 名字;@71206 審查 High 1)、killall 任何目標、
   選項在比對字串後面(pkill / pgrep)、kill $(pgrep …)、kill `pgrep …`、pgrep … | xargs kill、
   kill -9 -1 / kill 0 / kill -- -群組(High 2)、xargs 包一層(High 3)
P2 放行:pgrep -lf 看、kill <pid>、kill -1 <pid>(-1 是 HUP)、引號裡的字(commit 訊息 / grep;Medium)、
   pgrep 的 -- 後面(Low)
P3 macOS 真機(有 pgrep 才跑):選項放在比對字串後面,比對到的程序比放在前面多 —— 證明這條守門擋的是真的坑
P4 四語字典有新鍵、佔位一致
"""
import json
import pathlib
import re
import shutil
import subprocess
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401

ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src" / "policy" / "hooks"))
import tmux_kill_gate as G  # noqa: E402


def why(cmd):
    hit = G.check(cmd, "/Users/someone")
    return hit[0] if hit else None


class Gate(unittest.TestCase):
    def test_P1_block(self):
        cases = {
            'pkill -f "footprint-default-tests.js" -U $(id -u) -P $(pgrep -f "tp104.sh" | head -1) 2>/dev/null': "opt_after_pattern",
            "pkill -f footprint-default-tests.js": "pkill",
            "pkill -lf foo": "pkill",
            "pkill -9 -f 'node server.js'": "pkill",
            "pkill -U 501 -f foo": "pkill",
            "pkill -U 501": "pkill",
            "pkill -P 1": "pkill",
            "pkill sleep": "pkill",
            "pkill -x python3": "pkill",
            "pkill -U 501 -x node": "pkill",
            "kill -9 -1": "kill_all",
            "kill 0": "kill_all",
            "kill -- -1234": "kill_all",
            "kill -s KILL -1": "kill_all",
            "xargs pkill -f x": "pkill",
            "xargs -n 1 -P 4 kill -9 -1": "kill_all",
            "killall Terminal": "killall",
            "killall -9 node": "killall",
            "killall tmux": "killall",
            "pkill -x tmux": "pkill",
            "pkill node -U 501": "opt_after_pattern",
            "pgrep -lf foo -U 501": "opt_after_pattern",
            "kill $(pgrep -f foo)": "kill_subst",
            "kill -9 `pgrep node`": "kill_subst",
            "pgrep -f foo | xargs kill -9": "kill_subst",
            "cd /x && timeout 30 pkill -f bar": "pkill",
        }
        for cmd, w in cases.items():
            self.assertEqual(why(cmd), w, cmd)

    def test_P2_allow(self):
        for cmd in ("pgrep -lf foo", 'pgrep -fl "tp104|footprint"', "pgrep -U 501 -lf foo",
                    "kill 1234", "kill -9 1234 5678", "kill -TERM 42", "kill -1 1234", "kill -l",
                    "echo 1234 | xargs kill", 'echo "pkill -f x"', "grep -n killall notes.md", "ls",
                    'git commit -m "block kill $(pgrep x)"', 'grep -n "kill \\$(pgrep" f',
                    "pgrep -f foo -- -U"):
            self.assertIsNone(why(cmd), cmd)

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("pgrep"), "BSD pgrep 才有這個坑")
    def test_P3_bsd_getopt_trap_is_real(self):
        uid = str(__import__("os").getuid())

        def n(args):
            r = subprocess.run(["pgrep"] + args, capture_output=True, text=True, timeout=10)
            return len(r.stdout.split())
        good = n(["-f", "-U", uid, "zzqq-no-such-proc-742c2"])
        bad = n(["-f", "zzqq-no-such-proc-742c2", "-U", uid])
        self.assertEqual(good, 0)
        self.assertGreater(bad, 0, "選項放後面,-U / uid 被當成比對字串")


class Dict(unittest.TestCase):
    def test_P4_i18n(self):
        keys = ["aib.nowgate.tmux_kill." + k for k in ("killall", "pkill", "opt_after_pattern", "kill_subst", "kill_all")]
        ph = {}
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8"))
            for k in keys:
                self.assertIn(k, d, "%s 缺 %s" % (lang, k))
                ph.setdefault(k, set()).add(tuple(sorted(re.findall(r"\{(\w+)\}", d[k]))))
        for k, v in ph.items():
            self.assertEqual(len(v), 1, k)


if __name__ == "__main__":
    unittest.main()
