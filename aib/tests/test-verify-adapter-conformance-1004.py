#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""影子續跑「語言接頭」共用驗收(docs/SPEC-shadow-adapter-1004.md 第五節,10-04)。

每一種接頭都要過同一張表;現在先跑 Python 接頭(sitecustomize_audit)。之後加語言:
同樣的格,測試檔換成那種語言寫的(LANG 表加一欄),核心判斷一行不改。
每格都是「真的跑 aib test → 改一個東西 → 用 --why 看下一輪判什麼」,不是讀碼。

A1  讀的檔改了 ⇒ 必重跑            A2  沒讀的檔改了 ⇒ 照沿用
A3  問過不存在的檔、後來建出來 ⇒ 必重跑
A4  列過的目錄多一個檔 ⇒ 必重跑     A5  同語言子行程讀的檔改了 ⇒ 必重跑
A6  起一個沒接頭的程式 ⇒ 量不到 ⇒ 不沿用
A7  子行程被 kill -9 ⇒ 量不齊 ⇒ 不沿用
A8  連別的行程開的本機連接埠 ⇒ 外部 ⇒ 不沿用(10-08:同一個行程自己開、自己連不算外部,見 own-listener-loopback;這格改連子行程開的埠)
A9  自己寫到暫存再讀回來 ⇒ 照沿用(不會每次換鑰匙)
A10 測試改了倉內追蹤中的檔 ⇒ 紅、而且檔被還原
A11 紅的下一輪照跑                  A12 讀的檔寫著昨天、列是前天留的 ⇒ 必重跑

刀(複本):K2 接頭不報網路 ⇒ A8 紅(K1「不報讀檔」的錨在共用函式深處,還沒做 —— 照實寫)
Node(NodeConformance,同一張表):NK1 不報網路 ⇒ A8 紅;NK2 子行程一律當沒掛 ⇒ A5 紅
bash(BashConformance):BK1 不報 /dev/tcp ⇒ A8 紅;BK2 ls 放回「不讀檔」⇒ A4 紅(這兩格是 10-04 這張表第一次跑 bash 時抓到的漏洞)
"""
import datetime as dt
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
_spec = importlib.util.spec_from_file_location("entry_cells", str(ROOT / "tests" / "test-aib-test-entry-0929.py"))
E = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E)

HEAD = "import os, pathlib, socket, subprocess, sys, tempfile, unittest\nR = pathlib.Path(__file__).resolve().parents[1]\n"


def _case(body):
    return HEAD + "class T(unittest.TestCase):\n    def test_x(self):\n" + "".join(
        "        " + ln + "\n" for ln in body.strip("\n").splitlines()) + "if __name__ == '__main__':\n    unittest.main()\n"


PYTHON = {
    "a1": _case("self.assertTrue((R / 'data.txt').read_text())"),
    "a2": _case("self.assertTrue((R / 'data.txt').read_text())"),
    "a3": _case("self.assertIn(os.path.exists(R / 'later.txt'), (True, False))"),
    "a4": _case("self.assertIsInstance(os.listdir(R / 'd'), list)"),
    "a5": _case("subprocess.run([sys.executable, '-c', 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text())', "
                "str(R / 'x.txt')], check=True, stdin=subprocess.DEVNULL, capture_output=True)"),
    "a6": _case("subprocess.run(['perl', '-e', 'print 1'], check=True, stdin=subprocess.DEVNULL, capture_output=True)"),
    "a7": _case("subprocess.run([sys.executable, '-c', 'import os,signal; os.kill(os.getpid(), signal.SIGKILL)'], "
                "stdin=subprocess.DEVNULL)"),
    "a8": _case("srv = subprocess.Popen([sys.executable, '-c', 'import socket; s = socket.socket(); s.bind((\"127.0.0.1\", 0)); "
                "s.listen(1); print(s.getsockname()[1], flush=True); s.accept()[0].recv(1)'], "
                "stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True)\n"
                "c = socket.create_connection(('127.0.0.1', int(srv.stdout.readline())), timeout=5); c.close(); srv.wait(timeout=10)"),
    "a9": _case("d = tempfile.mkdtemp(); p = os.path.join(d, 'w.txt')\n"
                "open(p, 'w').write('hi'); self.assertEqual(open(p).read(), 'hi')"),
    "a10": _case("(R / 'data.txt').write_text('changed by test\\n')"),
    "a11": _case("self.assertEqual(1, 2)"),
    "a12": _case("self.assertTrue((R / 'due.json').read_text())"),
}

NODE_HEAD = ("import test from 'node:test'; import assert from 'node:assert'; import fs from 'node:fs';\n"
             "import path from 'node:path'; import os from 'node:os'; import net from 'node:net';\n"
             "import cp from 'node:child_process'; import { fileURLToPath } from 'node:url';\n"
             "const R = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');\n")


def _node(body):
    return NODE_HEAD + "test('x', async () => {\n" + "".join("  " + ln + "\n" for ln in body.strip("\n").splitlines()) + "});\n"


NODE = {   # 10-04:同一張表,測試改用 node:test 寫;核心判斷一行不改
    "a1": _node("assert.ok(fs.readFileSync(path.join(R, 'data.txt'), 'utf8'));"),
    "a2": _node("assert.ok(fs.readFileSync(path.join(R, 'data.txt'), 'utf8'));"),
    "a3": _node("fs.existsSync(path.join(R, 'later.txt'));"),
    "a4": _node("assert.ok(Array.isArray(fs.readdirSync(path.join(R, 'd'))));"),
    "a5": _node("const r = cp.spawnSync(process.execPath, ['-e', \"require('fs').readFileSync(process.argv[1])\", "
                "path.join(R, 'x.txt')], { stdio: 'ignore' }); assert.equal(r.status, 0);"),
    "a6": _node("assert.equal(cp.spawnSync('perl', ['-e', 'print 1']).status, 0);"),
    "a7": _node("cp.spawnSync(process.execPath, ['-e', \"process.kill(process.pid, 'SIGKILL')\"]);"),
    # 10-09 a seat(D10b):「等伺服器結束」要在一開始就掛上。以前是連線關掉之後才 srv.on('exit') ——
    #   伺服器一收到連線結束就 process.exit,機器忙時它比外層先結束,exit 事件已經發過、外層永遠等不到,
    #   撞 300 秒時限(閘裡 691~740 秒 ≈ 85 + 2×300)。實抓 4 個卡住的 test-a8.mjs:子程式不在、沒有網路、事件迴圈空。
    "a8": _node("const srv = cp.spawn(process.execPath, ['-e', \"const s = require('net').createServer((k) => { k.on('error', () => {}); \"\n"
                "  + \"k.resume(); k.on('end', () => process.exit(0)); }); s.listen(0, '127.0.0.1', () => console.log(s.address().port));\"],\n"
                "  { stdio: ['ignore', 'pipe', 'inherit'] });\n"
                "const exited = new Promise((res) => srv.on('exit', res));\n"
                "const port = await new Promise((res) => srv.stdout.once('data', (d) => res(Number(String(d).trim()))));\n"
                "await new Promise((res) => {\n"
                "  const c = net.connect(port, '127.0.0.1', () => c.end());\n"
                "  c.on('error', () => {}); c.on('close', res);\n"
                "});\n"
                "await exited;"),
    "a9": _node("const d = fs.mkdtempSync(path.join(os.tmpdir(), 'w-')); fs.writeFileSync(path.join(d, 'w.txt'), 'hi');\n"
                "assert.equal(fs.readFileSync(path.join(d, 'w.txt'), 'utf8'), 'hi');"),
    "a10": _node("fs.writeFileSync(path.join(R, 'data.txt'), 'changed by test\\n');"),
    "a11": _node("assert.equal(1, 2);"),
    "a12": _node("assert.ok(fs.readFileSync(path.join(R, 'due.json'), 'utf8'));"),
}

BASH_HEAD = 'set -e\nR="$(cd "$(dirname "$0")/.." && pwd)"\n'
BASH = {   # 10-04:同一張表,測試用 bash 寫(bash 接頭 = bash-shim/_trace.bash,由 BASH_ENV 掛上)
    "a1": BASH_HEAD + '[ -n "$(cat "$R/data.txt")" ]\n',
    "a2": BASH_HEAD + '[ -n "$(cat "$R/data.txt")" ]\n',
    "a3": BASH_HEAD + '[ -e "$R/later.txt" ] || true\n',
    "a4": BASH_HEAD + 'ls "$R/d" >/dev/null\n',
    "a5": BASH_HEAD + 'bash "$R/tests/child.sh" "$R/x.txt"\n',
    "a6": BASH_HEAD + "perl -e 'print 1' >/dev/null\n",
    "a7": BASH_HEAD + "bash -c 'kill -9 $$' || true\n",
    "a8": BASH_HEAD + 'python3 -c "import socket,time;s=socket.socket();s.bind((\'127.0.0.1\',0));s.listen(1);'
          'open(\'$R/../port\',\'w\').write(str(s.getsockname()[1]));s.accept()" &\n'
          'for i in 1 2 3 4 5 6 7 8 9 10; do [ -s "$R/../port" ] && break; sleep 0.2; done\n'
          'exec 3<>"/dev/tcp/127.0.0.1/$(cat "$R/../port")"; exec 3>&-; wait\n',
    "a9": BASH_HEAD + 'd="$(mktemp -d)"; echo hi > "$d/w"; [ "$(cat "$d/w")" = hi ]\n',
    "a10": BASH_HEAD + 'echo "changed by test" > "$R/data.txt"\n',
    "a11": BASH_HEAD + 'exit 1\n',
    "a12": BASH_HEAD + '[ -n "$(cat "$R/due.json")" ]\n',
}



def _run_group(argv, cwd, env, timeout):
    """subprocess.run 的替身:子行程開在新的行程群組,逾時就整組砍掉再照樣丟 TimeoutExpired。
    10-09 a seat:subprocess.run(timeout=) 逾時只砍直接那一層(test_entry.py),它底下的 node(tests/test-a8.mjs)
    被系統收養成孤兒、永遠掛著 —— 10-09 推前檢查負載高時本支撞 300 秒時限,機器上累積 6 個 test-a8.mjs(最久 46 分鐘)。
    test_entry 不另開群組,所以從這裡整組砍得到;同 tools/verify/prewarm.py 的作法。"""
    import signal
    p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.communicate()
        raise
    return subprocess.CompletedProcess(argv, p.returncode, out, err)

class Conformance(unittest.TestCase):
    LANG = PYTHON
    EXT = ".py"

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.td = Path(self._t.name)
        self.fx = E._fixture(self.td)
        (self.fx / "x.txt").write_text("x\n", encoding="utf-8")
        (self.fx / "other.txt").write_text("o\n", encoding="utf-8")
        (self.fx / "d").mkdir()
        (self.fx / "d" / "one").write_text("1", encoding="utf-8")
        yesterday = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()
        (self.fx / "due.json").write_text('{"expires_at": "%s"}\n' % yesterday, encoding="utf-8")
        for k, body in self.LANG.items():
            (self.fx / "tests" / ("test-%s%s" % (k, self.EXT))).write_text(body, encoding="utf-8")
        self.commit()
        self.env = E._env(self.td)

    def tearDown(self):
        self._t.cleanup()

    def commit(self):
        genv = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.td), **E.GIT_ENV}
        for argv in (["git", "add", "-A"], ["git", "commit", "-q", "-m", "c", "--allow-empty"]):
            subprocess.run(argv, cwd=str(self.fx), env=genv, check=True, capture_output=True, stdin=subprocess.DEVNULL)

    def run_test(self, k):
        return _run_group([sys.executable, str(self.fx / E.ENTRY_REL), "tests/test-%s%s" % (k, self.EXT)], cwd=str(self.fx),
                          env=self.env, timeout=300)

    def why(self, k):
        p = _run_group([sys.executable, str(self.fx / E.ENTRY_REL), "--why", "--json", "tests/test-%s%s" % (k, self.EXT)],
                       cwd=str(self.fx), env=self.env, timeout=300)
        self.assertEqual(p.returncode, 0, p.stderr[-1500:])
        return json.loads(p.stdout)["why"][0]

    def first(self, k):
        r = self.run_test(k)
        self.assertEqual(r.returncode, 0, "%s 第一次要綠:%s" % (k, r.stdout + r.stderr[-1500:]))

    def test_a1_read_file_changed(self):
        self.first("a1")
        self.assertEqual(self.why("a1")["verdict"], "reuse", "對照:沒改就沿用")
        (self.fx / "data.txt").write_text("ok\nmore\n", encoding="utf-8")
        self.assertEqual(self.why("a1")["verdict"], "must-run")

    def test_a2_unread_file_changed(self):
        self.first("a2")
        (self.fx / "other.txt").write_text("changed\n", encoding="utf-8")
        self.assertEqual(self.why("a2")["verdict"], "reuse")

    def test_a3_missing_then_created(self):
        self.first("a3")
        (self.fx / "later.txt").write_text("now\n", encoding="utf-8")
        self.assertEqual(self.why("a3")["verdict"], "must-run")

    def test_a4_listdir_grows(self):
        self.first("a4")
        (self.fx / "d" / "two").write_text("2", encoding="utf-8")
        self.assertEqual(self.why("a4")["verdict"], "must-run")

    def test_a5_same_language_child(self):
        self.first("a5")
        self.assertEqual(self.why("a5")["verdict"], "reuse", "對照:子行程有掛接頭 ⇒ 量得到 ⇒ 沿用")
        (self.fx / "x.txt").write_text("y\n", encoding="utf-8")
        self.assertEqual(self.why("a5")["verdict"], "must-run")

    def test_a6_unhooked_program(self):
        self.first("a6")
        self.assertEqual(self.why("a6")["verdict"], "must-run")

    def test_a7_child_killed(self):
        self.first("a7")
        self.assertEqual(self.why("a7")["verdict"], "must-run")

    def test_a8_network(self):
        self.first("a8")
        got = self.why("a8")
        self.assertEqual(got["verdict"], "must-run", got)

    def test_a9_own_temp_write(self):
        self.first("a9")
        self.assertEqual(self.why("a9")["verdict"], "reuse", self.why("a9"))

    def test_a10_test_edits_tracked_file(self):
        r = self.run_test("a10")
        self.assertNotEqual(r.returncode, 0, "改了倉內追蹤中的檔 ⇒ 紅:" + r.stdout)
        self.assertEqual((self.fx / "data.txt").read_text(encoding="utf-8"), "ok\n", "檔要被還原")

    def test_a11_red_is_rerun(self):
        self.run_test("a11")
        self.assertEqual(self.why("a11")["why"], "previous-red")

    def test_a12_date_crossed(self):
        self.first("a12")
        for f in sorted((self.td / "shadow").glob("*.rows.jsonl")):
            out = []
            for ln in f.read_text(encoding="utf-8").splitlines():
                r = json.loads(ln)
                if r.get("test") == "tests/test-a12" + self.EXT:
                    t = dt.datetime.strptime(r["time_utc"], "%Y-%m-%dT%H:%M:%SZ") - dt.timedelta(hours=50)
                    r["time_utc"] = t.strftime("%Y-%m-%dT%H:%M:%SZ")
                out.append(json.dumps(r, ensure_ascii=False))
            f.write_text("\n".join(out) + "\n", encoding="utf-8")
        self.assertEqual(self.why("a12")["why"], "date-crossed")


class BashConformance(Conformance):
    """同一張表,bash 寫的測試。"""
    LANG = BASH
    EXT = ".sh"

    def setUp(self):
        super().setUp()
        (self.fx / "tests" / "child.sh").write_text('cat "$1" >/dev/null\n', encoding="utf-8")
        self.commit()


class NodeConformance(Conformance):
    """同一張表,Node 接頭(tools/verify/node-shim/audit.cjs)。"""
    LANG = NODE
    EXT = ".mjs"


class Knives(unittest.TestCase):
    def _red(self, needle, replacement, cell, rel="tools/verify/sitecustomize_audit.py", also=()):
        from aib_knife_copy import knife_copy
        name = Path(__file__).name
        with knife_copy(ROOT, rel, needle, replacement,
                        tests=(name, "test-aib-test-entry-0929.py")) as root:
            for n2, r2 in also:      # 同一個檔的第二處(刀要把兩處一起還原成修之前)
                f = root / rel
                t = f.read_text(encoding="utf-8")
                assert t.count(n2) == 1, n2
                f.write_text(t.replace(n2, r2), encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")}
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            p = subprocess.run([sys.executable, str(root / "tests" / name), cell if "." in cell else "Conformance." + cell], cwd=str(root),
                               env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
        return p, set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))

    def test_k2_no_network_event_reds_a8(self):
        p, red = self._red('            _emit({"ch": "external", "why": "network"})\n', "            pass\n",
                           "test_a8_network")
        self.assertIn("test_a8_network", red, p.stderr[-2000:])

    def test_nk1_node_no_network_event_reds_a8(self):
        # 10-08:connect 改成「自己 listen 的本機埠不算外部」,刀錨跟著換到那一行;刀的意思不變 —— 連線一律不發外部事件
        p, red = self._red("    guard(() => { if (!toOwn(args)) emit({ ch: 'external', why: 'network' }); });\n",
                           "    void 0;\n",
                           "NodeConformance.test_a8_network", rel="tools/verify/node-shim/audit.cjs")
        self.assertIn("test_a8_network", red, p.stderr[-2000:])

    def test_nk2_node_child_never_hooked_reds_a5(self):
        p, red = self._red("if (b === 'node') return String(env.NODE_OPTIONS || '').includes(__filename);",
                           "if (b === 'node') return false;",
                           "NodeConformance.test_a5_same_language_child", rel="tools/verify/node-shim/audit.cjs")
        self.assertIn("test_a5_same_language_child", red, p.stderr[-2000:])


    def test_bk1_bash_no_dev_tcp_event_reds_a8(self):
        p, red = self._red('  case $cmd in *"/dev/tcp/"*|*"/dev/udp/"*) _ez2_bt_emit', '  case $cmd in ZZ_NEVER) _ez2_bt_emit',
                           "BashConformance.test_a8_network", rel="tools/verify/bash-shim/_trace.bash")
        self.assertIn("test_a8_network", red, p.stderr[-2000:])

    def test_bk2_ls_transparent_again_reds_a4(self):
        # 修之前:ls 在「不讀檔」、不在「讀參數路徑」;bash 那條路兩份名單都會看 ⇒ 兩處一起還原
        p, red = self._red('    "echo", "printf", "mktemp", "rm", "rmdir", "mkdir", "touch", "chmod", "mv", "ln", "df",',
                           '    "echo", "printf", "mktemp", "rm", "rmdir", "mkdir", "touch", "chmod", "mv", "ln", "df", "ls",',
                           "BashConformance.test_a4_listdir_grows", rel="tools/verify/shadow_tracer.py",
                           also=(('"ls", "stat", "du", "readlink", "realpath",', '"stat", "du", "readlink", "realpath",'),))
        # ↑ 10-07:那行後面加了 "file"(platform.architecture 叫的 `file -b`),結尾從 )) 變成逗號;刀的意圖不變(拿掉 ls)
        self.assertIn("test_a4_listdir_grows", red, p.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
