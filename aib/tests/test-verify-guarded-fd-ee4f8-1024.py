#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""審計鉤子不碰被 macOS 重用成 guarded fd 的號碼(10-24:一天 10 次 Python 被 EXC_GUARD SIGKILL,rc -9)。

情境:測試自己 bind 一個 socket(鉤子記下 fd)、關掉;系統函式庫(_scproxy 等)用 guarded_open_np 拿到同一個號碼;
之後 socket.connect 觸發鉤子比對「是不是連自己」。以前對那個號碼 dup / close ⇒ 核心直接砍掉行程。
G1 (macOS)重現上面的形狀:測試 rc 0、照常判外部(連的是 9 埠,不是自己)
G2 自己開的伺服器、自己連回去仍不算外部(舊行為不變,見 test-verify-own-listener-loopback-1008)
刀(複本):K1 認 socket 前又 dup 這個號碼 ⇒ G1 rc -9;K2 問完埠號改回 close(不是 detach)⇒ 自己的伺服器被關,G2 紅
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "verify"))
sys.path.insert(0, str(ROOT / "tests"))
import shadow_tracer as st  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

GUARD = '''import ctypes, os, socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); fd = s.fileno(); s.close()
libc = ctypes.CDLL(None, use_errno=True)
g = ctypes.c_uint64(0x7b0e99f18ee38e49)
libc.guarded_open_np.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint64), ctypes.c_uint, ctypes.c_int]
gfd = libc.guarded_open_np(b"/dev/null", ctypes.byref(g), 1 | 2, os.O_RDONLY | os.O_CLOEXEC)  # GUARD_CLOSE | GUARD_DUP
assert gfd == fd, (gfd, fd)
c = socket.socket(); c.settimeout(0.5)
try:
    c.connect(("127.0.0.1", 9))
except OSError:
    pass
'''
SRV = '''import http.server, threading, urllib.request
srv = http.server.HTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
body = urllib.request.urlopen("http://127.0.0.1:%d/data.txt" % srv.server_address[1]).read()
srv.shutdown(); assert body == b"D\\n"
'''


class Guarded(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name) / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "data.txt").write_text("D\n", encoding="utf-8")
        (self.repo / "tests" / "t_guard.py").write_text(GUARD, encoding="utf-8")
        (self.repo / "tests" / "t_srv.py").write_text(SRV, encoding="utf-8")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for a in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
            subprocess.run(["git", "-C", str(self.repo)] + a, check=True, capture_output=True, env=env, stdin=subprocess.DEVNULL)
        self.trace_dir = Path(self.td.name) / "traces"
        self.trace_dir.mkdir()

    def tearDown(self):
        self.td.cleanup()

    def _run(self, name):
        old = os.environ.get("EZ2_VERIFY_TRACE_DIR")
        os.environ["EZ2_VERIFY_TRACE_DIR"] = str(self.trace_dir)
        try:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            return st.run_one(self.repo, "tests/" + name, environ=env, timeout=60)
        finally:
            if old is None:
                os.environ.pop("EZ2_VERIFY_TRACE_DIR", None)
            else:
                os.environ["EZ2_VERIFY_TRACE_DIR"] = old

    @unittest.skipUnless(sys.platform == "darwin", "guarded fd 只有 macOS 有")
    def test_g1_guarded_fd_not_touched(self):
        row = self._run("t_guard.py")
        self.assertEqual(row["rc"], 0, row.get("tail"))
        self.assertTrue(row.get("external"), row["why"])

    def test_g2_own_server_still_not_external(self):
        row = self._run("t_srv.py")
        self.assertEqual(row["rc"], 0, row.get("tail"))
        self.assertFalse(row.get("external"), row["why"])


class Knives(unittest.TestCase):
    def _red(self, needle, repl, test):
        name = Path(__file__).name
        with knife_copy(ROOT, "tools/verify/sitecustomize_audit.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), test], cwd=str(root), max_procs=60, timeout=300)
        self.assertNotEqual(r.returncode, 0, "刀落了測試還綠:" + test)

    @unittest.skipUnless(sys.platform == "darwin", "guarded fd 只有 macOS 有")
    def test_k1_dup_again_reds_g1(self):
        self._red("                st = os.fstat(fd)", "                st = os.fstat(os.dup(fd))",
                  "Guarded.test_g1_guarded_fd_not_touched")

    def test_k2_close_instead_of_detach_reds_g2(self):
        self._red("                probe.detach()", "                probe.close()", "Guarded.test_g2_own_server_still_not_external")


if __name__ == "__main__":
    unittest.main()
