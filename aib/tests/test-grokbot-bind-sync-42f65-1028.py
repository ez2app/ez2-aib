#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""grokbot webhook 綁定跨機同步(10-28 Andy 准):假 ssh(在暫存「遠端 HOME」裡跑同一段遠端程式)、假主機清單,不碰真機器。

S1 沒按「同步」⇒ 不傳(連 write 都沒叫)
S2 沒有交談終端 ⇒ 不傳、不連 ssh,只印要跑的指令;connect --stdin 之後同樣不傳
S3 按了「同步」⇒ 對方檔案 0600、資料夾 0700、內容一致;任何輸出、ssh 參數都不含網址與金鑰
S4 對方內容相同 ⇒ 跳過(不寫)
S5 對方內容不同 ⇒ 列出(不印值)並另問覆蓋:不要 ⇒ 原樣保留;要 ⇒ 覆蓋;--yes 沒 --overwrite ⇒ 不覆蓋
S6 離線 ⇒ 跳過並提示上線後的指令,其他台照傳,rc 4
S7 目標清單:dm-hosts.json 的值去掉本機 IP;--to 只用指定的
S8 confirm:原生視窗按「同步」才算;按「不同步」(-128)/ 逾時都不算
"""
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COCKPIT = ROOT / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
import grokbot_bind_sync as bs  # noqa: E402
import grokbot_threads as gt  # noqa: E402

AGENT = "42f65f97-8e7c-46fe-a966-98421d79ba57"
URL = "https://api2.cursor.sh/automations/webhook/0123abcd-0123-4567-89ab-0123456789ab"
KEY = "sk_test_SECRETKEY_42f65_sync"
SECRET_BITS = (KEY, "0123abcd-0123-4567-89ab-0123456789ab")

FAKE_SSH = r'''
import json, os, subprocess, sys
a = sys.argv[1:]
i = 0
while i < len(a) and a[i].startswith("-"):
    i += 2 if a[i] == "-o" else 1
host, remote = a[i], a[-1]
open(os.environ["FAKE_SSH_LOG"], "a").write(json.dumps({"argv": sys.argv[1:], "host": host,
                                                        "mode": remote.split()[-1]}) + "\n")
if host in os.environ.get("FAKE_SSH_OFFLINE", "").split(","):
    sys.stderr.write("ssh: connect to host %s: Operation timed out\n" % host); sys.exit(255)
env = {k: v for k, v in os.environ.items() if k != "EZ2_AIB_STATE_ROOT"}
env["HOME"] = os.path.join(os.environ["FAKE_REMOTE_ROOT"], host)
os.makedirs(env["HOME"], exist_ok=True)
sys.exit(subprocess.run(["/bin/sh", "-c", remote], env=env).returncode)
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-gb-sync-"))
        self.state = self.td / "state"
        self.remote = self.td / "remote"
        self.log = self.td / "ssh.jsonl"
        ssh = self.td / "ssh"
        ssh.write_text("#!%s\n%s" % (sys.executable, FAKE_SSH), encoding="utf-8")
        ssh.chmod(0o755)
        self.local = gt.agent_wake_path(AGENT, self.state)
        self.local.parent.mkdir(parents=True)
        self.local.write_text("GROKBOT_WAKE_URL=%s\nGROKBOT_WAKE_KEY=%s\n" % (URL, KEY), encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("EZ2_", "AIB_"))}
        self.env.update({"EZ2_AIB_STATE_ROOT": str(self.state), "AIB_GROKBOT_SSH": str(ssh),
                         "FAKE_SSH_LOG": str(self.log), "FAKE_REMOTE_ROOT": str(self.remote),
                         "EZ2_AIB_LOCALE": "zh-TW", "AIB_DM_LOCAL_IPS": "100.64.0.1",
                         "PYTHONDONTWRITEBYTECODE": "1"})
        self._old = dict(os.environ)
        os.environ.clear()
        os.environ.update(self.env)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old)
        shutil.rmtree(self.td, ignore_errors=True)

    def remote_file(self, host):
        return self.remote / host / ".ez2-aib" / "secrets" / "grokbot-agents" / (AGENT + ".env")

    def put_remote(self, host, text):
        f = self.remote_file(host)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")

    def calls(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def sync(self, hosts, answers=(), **kw):
        out = io.StringIO()
        asked = []
        ans = list(answers)

        def ask_fn(q, y, n):
            asked.append((q, y, n))
            return ans.pop(0) if ans else False
        rc = bs.sync(AGENT, hosts, base=self.state, env=self.env, out=out, ask_fn=ask_fn,
                     interactive=kw.pop("interactive", True), **kw)
        return rc, out.getvalue(), asked

    def assertNoSecret(self, text):
        for s in SECRET_BITS:
            self.assertNotIn(s, text)


class Sync(Base):
    def test_s1_declined_sends_nothing(self):
        rc, out, asked = self.sync(["mini"], answers=[False])
        self.assertEqual(rc, 2)
        self.assertEqual(len(asked), 1)
        self.assertEqual((asked[0][1], asked[0][2]), ("同步", "不同步"))
        self.assertIn("mini", asked[0][0])
        self.assertEqual(self.calls(), [], "沒同意就連 ssh 都不該連")
        self.assertFalse(self.remote_file("mini").exists())
        self.assertIn("aib grokbot sync-bind --agent %s --to mini" % AGENT, out)

    def test_s2_no_tty(self):
        rc, out, asked = self.sync(["mini"], interactive=False)
        self.assertEqual((rc, asked, self.calls()), (2, [], []))
        self.assertIn("沒有交談終端", out)
        self.assertIn("aib grokbot sync-bind --agent %s --to mini" % AGENT, out)
        # 真的子行程(stdin 不是 tty)走 CLI:一樣不傳
        r = subprocess.run([sys.executable, str(COCKPIT / "grokbot_bind_sync.py"), "--agent", AGENT, "--to", "mini"],
                           env=self.env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.calls(), [])
        self.assertNoSecret(r.stdout + r.stderr)

    def test_s2b_connect_stdin_does_not_send(self):
        (self.state / "user").mkdir(parents=True)
        (self.state / "user" / "dm-hosts.json").write_text(json.dumps({"100.64.0.2": "mini"}))
        out = io.StringIO()
        rc = bs.offer_after_connect(AGENT, interactive=False, env=self.env, base=self.state, out=out)
        self.assertEqual(rc, 2)
        self.assertEqual(self.calls(), [])
        self.assertIn("aib grokbot sync-bind --agent %s --to mini" % AGENT, out.getvalue())

    def test_s3_confirmed_sends_0600(self):
        rc, out, asked = self.sync(["mini", "fishmac"], answers=[True])
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(asked), 1, "一次問一題,兩台一起")
        for h in ("mini", "fishmac"):
            f = self.remote_file(h)
            self.assertEqual(f.read_text(), self.local.read_text())
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(f.parent.stat().st_mode), 0o700)
            self.assertEqual([p.name for p in f.parent.iterdir()], [f.name], "不留暫檔")
        self.assertIn("已同步", out)
        self.assertNoSecret(out)
        self.assertNoSecret(json.dumps(self.calls()))
        self.assertNoSecret(asked[0][0])
        for c in self.calls():
            self.assertIn("BatchMode=yes", c["argv"])
            self.assertIn("ForwardAgent=no", c["argv"])

    def test_s4_same_skipped(self):
        self.put_remote("mini", self.local.read_text())
        rc, out, _ = self.sync(["mini"], answers=[True])
        self.assertEqual(rc, 0)
        self.assertIn("一樣的設定,跳過", out)
        self.assertEqual([c["mode"] for c in self.calls()], ["probe"])

    def test_s5_diff_asks_overwrite(self):
        other = "GROKBOT_WAKE_URL=%s\nGROKBOT_WAKE_KEY=otherkey\n" % URL
        self.put_remote("mini", other)
        rc, out, asked = self.sync(["mini"], answers=[True, False])
        self.assertEqual(rc, 0)
        self.assertEqual(len(asked), 2)
        self.assertEqual((asked[1][1], asked[1][2]), ("覆蓋", "不覆蓋"))
        self.assertIn("內容不同(不印值)", out)
        self.assertEqual(self.remote_file("mini").read_text(), other)
        self.assertNoSecret(out)
        self.assertNotIn("otherkey", out + asked[1][0])
        self.log.unlink()
        rc, out, asked = self.sync(["mini"], answers=[True, True])
        self.assertEqual(rc, 0)
        self.assertEqual(self.remote_file("mini").read_text(), self.local.read_text())
        self.assertEqual(stat.S_IMODE(self.remote_file("mini").stat().st_mode), 0o600)

    def test_s5b_yes_without_overwrite_keeps(self):
        other = "GROKBOT_WAKE_URL=%s\nGROKBOT_WAKE_KEY=otherkey\n" % URL
        self.put_remote("mini", other)
        rc, out, asked = self.sync(["mini"], ask=False)
        self.assertEqual((rc, asked), (0, []))
        self.assertEqual(self.remote_file("mini").read_text(), other)
        rc, out, asked = self.sync(["mini"], ask=False, overwrite=True)
        self.assertEqual(self.remote_file("mini").read_text(), self.local.read_text())

    def test_s6_offline_skipped(self):
        self.env["FAKE_SSH_OFFLINE"] = os.environ["FAKE_SSH_OFFLINE"] = "mini"
        rc, out, _ = self.sync(["mini", "fishmac"], answers=[True])
        self.assertEqual(rc, 4)
        self.assertIn("mini:連不上", out)
        self.assertIn("aib grokbot sync-bind --agent %s --to mini" % AGENT, out)
        self.assertTrue(self.remote_file("fishmac").exists())
        self.assertFalse(self.remote_file("mini").exists())

    def test_not_bound(self):
        self.local.unlink()
        rc, out, asked = self.sync(["mini"], answers=[True])
        self.assertEqual((rc, asked, self.calls()), (2, [], []))
        self.assertIn("aib bot connect grokbot --agent", out)

    def test_bad_host_rejected(self):
        rc, out, _ = self.sync(["-oProxyCommand=x"], answers=[True])
        self.assertEqual(rc, 2)
        self.assertEqual(self.calls(), [])


class Targets(Base):
    def test_s7_hosts_file_minus_local(self):
        (self.state / "user").mkdir(parents=True)
        (self.state / "user" / "dm-hosts.json").write_text(json.dumps(
            {"100.64.0.1": "self-alias", "100.64.0.2": "mini", "100.64.0.3": "fishmac", "100.64.0.4": "mini"}))
        self.assertEqual(bs.targets(self.env), ["mini", "fishmac"])
        self.assertEqual(bs.targets(self.env, ["fishmac"]), ["fishmac"])

    def test_no_hosts(self):
        self.assertEqual(bs.targets(self.env), [])
        out = io.StringIO()
        self.assertEqual(bs.offer_after_connect(AGENT, interactive=True, env=self.env, base=self.state, out=out), 0)
        self.assertIn("dm-hosts.json", out.getvalue())
        self.assertEqual(self.calls(), [])


class Confirm(Base):
    def _osa(self, body):
        f = self.td / "osascript"
        f.write_text("#!%s\nimport sys\n%s" % (sys.executable, body), encoding="utf-8")
        f.chmod(0o755)
        return dict(self.env, AIB_GROKBOT_OSASCRIPT=str(f))

    def test_s8_button(self):
        env = self._osa("print('button returned:同步, gave up:false')")
        self.assertTrue(bs.confirm("q", "同步", "不同步", env))
        env = self._osa("sys.stderr.write('execution error: User canceled. (-128)'); sys.exit(1)")
        self.assertFalse(bs.confirm("q", "同步", "不同步", env))
        env = self._osa("print('button returned:, gave up:true')")
        self.assertFalse(bs.confirm("q", "同步", "不同步", env))


if __name__ == "__main__":
    unittest.main()
