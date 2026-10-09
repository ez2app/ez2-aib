#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""測試自己開的本機伺服器、自己連回去,不算碰外部(10-08,express 實測:70 支只沿用 8 支 → 70 支全沿用)。

只認同一個行程自己 bind / listen 的本機埠(或 Unix socket 路徑);連到本機上別人開的服務照樣算外部(不准藏紅)。
L1 Python:執行緒裡開 http.server、自己連 ⇒ 不是外部、量得到;伺服器讀的檔進鑰匙
L2 Python:連一個不是自己開的埠 ⇒ 外部
L3 Node(有 node 才跑):listen(0) 後同一輪就連(supertest 的形狀)⇒ 沒有外部事件
L4 Node:連不是自己開的埠 ⇒ 有外部事件
L5 Python 鉤子沒有為這件事多 import 模組(weakref 不在啟動時的 sys.modules 裡)
刀(複本):K1 Python 一律當外部 ⇒ L1 紅;K2 Node listen 回來不記 ⇒ L3 紅
"""
import os
import re
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

SRV = '''import http.server, threading, urllib.request
srv = http.server.HTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
body = urllib.request.urlopen("http://127.0.0.1:%d/data.txt" % srv.server_address[1]).read()
srv.shutdown(); assert body == b"D\\n"
'''
EXT = '''import socket
s = socket.socket(); s.settimeout(0.5)
try:
    s.connect(("127.0.0.1", 9))
except OSError:
    pass
'''
NODE_OWN = """const http = require('http');
const srv = http.createServer((q, r) => r.end('ok'));
srv.listen(0);
const port = srv.address().port;
http.get('http://127.0.0.1:' + port + '/', r => { r.resume(); r.on('end', () => srv.close()); });
"""
NODE_EXT = """const net = require('net');
const s = net.connect(9, '127.0.0.1'); s.on('error', () => {});
"""


class Python(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name) / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "data.txt").write_text("D\n", encoding="utf-8")
        (self.repo / "tests" / "t_srv.py").write_text(SRV, encoding="utf-8")
        (self.repo / "tests" / "t_ext.py").write_text(EXT, encoding="utf-8")
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
            row = st.run_one(self.repo, "tests/" + name, environ=env, timeout=60)
        finally:
            if old is None:
                os.environ.pop("EZ2_VERIFY_TRACE_DIR", None)
            else:
                os.environ["EZ2_VERIFY_TRACE_DIR"] = old
        self.assertEqual(row["rc"], 0, row.get("tail"))
        return row

    def test_l1_own_server_not_external(self):
        row = self._run("t_srv.py")
        self.assertFalse(row.get("external"), row["why"])
        self.assertIs(row["measured"], True, row["why"])
        self.assertTrue(any("data.txt" in str(i) for i in row["inputs"]), "伺服器讀的檔要進鑰匙")

    def test_l2_other_port_is_external(self):
        self.assertTrue(self._run("t_ext.py").get("external"))

    def test_l5_no_extra_import(self):
        p = subprocess.run([sys.executable, "-c", "import sys; print('weakref' in sys.modules)"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        if p.stdout.strip() == "True":
            self.skipTest("這支 python 啟動時本來就有 weakref,量不出差別")
        hook = (ROOT / "tools" / "verify" / "sitecustomize_audit.py").read_text(encoding="utf-8")
        self.assertNotRegex(hook, r"(?m)^import weakref|^from weakref|^import weakref as")


@unittest.skipUnless(shutil.which("node"), "沒有 node")
class Node(unittest.TestCase):
    def _trace(self, js):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "t.js"
            f.write_text(js, encoding="utf-8")
            trace = Path(td) / "tr.jsonl"
            trace.write_text("", encoding="utf-8")
            env = dict({k: v for k, v in os.environ.items() if k not in ("NODE_OPTIONS",)}, EZ2_VERIFY_TRACE=str(trace),
                       NODE_OPTIONS="--require %s" % (ROOT / "tools" / "verify" / "node-shim" / "audit.cjs"))
            p = subprocess.run(["node", str(f)], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr[-1000:])
            return trace.read_text(encoding="utf-8")

    def test_l3_own_server_no_external(self):
        self.assertNotIn('"external"', self._trace(NODE_OWN))

    def test_l4_other_port_external(self):
        self.assertIn('"external"', self._trace(NODE_EXT))


class Knives(unittest.TestCase):
    def _red(self, rel, needle, repl, cell):
        from aib_knife_copy import knife_copy
        name = Path(__file__).name
        with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            p = subprocess.run([sys.executable, str(root / "tests" / name), cell], cwd=str(root), env=env,
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
        self.assertIn(cell.split(".")[-1], set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M)), p.stderr[-2000:])

    def test_k1_python_always_external_reds_l1(self):
        self._red("tools/verify/sitecustomize_audit.py", "            if not _to_own_listener(args):\n", "            if True:\n",
                  "Python.test_l1_own_server_not_external")

    @unittest.skipUnless(shutil.which("node"), "沒有 node")
    def test_k2_node_no_sync_note_reds_l3(self):
        self._red("tools/verify/node-shim/audit.cjs", "    noteOwn(this);       // TCP", "    void 0;       // TCP",
                  "Node.test_l3_own_server_no_external")


if __name__ == "__main__":
    unittest.main()
