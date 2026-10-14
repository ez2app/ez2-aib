#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib dm 跨主機帶出真實寄件人(the maintainer 10-14 20:5x「先修這個」)。

災情:A 機的席寄 B 機,只能 SSH 進 B 機跑 aib dm ⇒ 抬頭 `IP:<B 機自己>/…/未認證@unknown`。
修法(dm_xhost.py 檔頭):寄件機自己推導身分 ⇒ ssh 收件機 aib dm --xhost-ident <token> <艙>/<席>;
收件機驗(root sshd 底下、對方不是本機、寄件方過白名單)才換抬頭,位址用 sshd 量到的對方 IP。

U1–U6 單元;X1–X6 走 dm-ctl.sh(假 ssh 記下參數與 stdin、假 tmux 不可達,不碰真艙也不連網)。
收件端「驗過、真的換抬頭」那條要真的 root sshd,單元用假行程表(U4),整條在兩台真機上實測(交件附抬頭原文)。
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "tests"))
import aib_cabin_wash as _cabin  # noqa: E402

CTL = REPO / "src" / "cockpit" / "dm-ctl.sh"
INST = "20260928-101010-ab12"
FAKE_TMUX = """#!/bin/bash
printf '%s\\n' "$*" >> "$DM_CALLS"
exit 1
"""
# 假 ssh:最後一個參數是 `true` ⇒ 連線試探;其他 ⇒ 遠端指令(stdin 是正文)。
FAKE_SSH = """#!/bin/bash
printf '%s\\n' "$*" >> "$SSH_CALLS"
last="${@: -1}"
if [ "$last" = "true" ]; then exit "${SSH_PROBE_RC:-0}"; fi
printf '%s' "$last" > "$SSH_CMD"
cat > "$SSH_STDIN"
[ "${SSH_CMD_RC:-0}" = "255" ] || [ -n "${SSH_NO_RESULT:-}" ] || echo "RESULT stage=delivered id=q_0000000000000000 relay=- thread=-"
exit "${SSH_CMD_RC:-0}"
"""


def _xh():
    import dm_xhost
    return dm_xhost


def chain(*rows):
    """假行程表:rows = (pid, ppid, uid, comm)…"""
    t = {r[0]: (r[1], r[2], r[3]) for r in rows}
    return lambda pid: t.get(pid)


MINI_SSHD = chain((300, 200, 501, "zsh"), (200, 100, 501, "sshd-session: userb@notty"),
                  (100, 1, 0, "sshd-session: userb [priv]"))


class Unit(unittest.TestCase):
    def test_U1_token_roundtrip(self):
        X = _xh()
        tok = X.encode("@8f366(第二艦長)", "8f366", "seat", "aihub-20261013-931e")
        self.assertEqual(X.decode(tok), {"label": "@8f366(第二艦長)", "origin": "8f366", "kind": "seat",
                                         "hub": "aihub-20261013-931e"})
        for bad in ("", "not base64!", base64.urlsafe_b64encode(b'{"v":2}').decode()):
            self.assertIsNone(X.decode(bad), bad)

    def test_U2_ssh_target_uses_host_alias_for_ip(self):
        """ssh 用打的名字對 Host 區塊:打 IP 吃不到 alias 的 User(別台帳號不同)。"""
        X = _xh()
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config"
            cfg.write_text("Host *\n  ServerAliveInterval 30\nHost box-b boxb\n"
                           "  HostName 100.64.0.2\n  User userb\n", encoding="utf-8")
            env = {"HOME": d, "AIB_DM_XHOST_HOSTS": str(Path(d) / "none.json")}
            self.assertEqual(X.ssh_target("100.64.0.2", env, str(cfg)), "box-b")
            self.assertEqual(X.ssh_target("100.1.2.3", env, str(cfg)), "100.1.2.3")
            hosts = Path(d) / "hosts.json"
            hosts.write_text(json.dumps({"100.64.0.2": "mini-override"}), encoding="utf-8")
            env["AIB_DM_XHOST_HOSTS"] = str(hosts)
            self.assertEqual(X.ssh_target("100.64.0.2", env, str(cfg)), "mini-override")

    def test_U3_root_sshd_ancestor_required(self):
        X = _xh()
        self.assertTrue(X.under_root_sshd(300, MINI_SSHD))
        # 對照:同一條鏈但 sshd 不是 root(本機一般使用者自己起一支叫 sshd 的行程)⇒ 不認
        fake = chain((300, 200, 501, "zsh"), (200, 100, 501, "sshd-session: userb@notty"),
                     (100, 1, 501, "sshd-session: userb [priv]"))
        self.assertFalse(X.under_root_sshd(300, fake))
        self.assertFalse(X.under_root_sshd(300, chain((300, 1, 0, "launchd"))))
        self.assertFalse(X.under_root_sshd(300, chain()), "斷鏈不猜")

    def _accept(self, label, ip="100.64.0.1", ps=MINI_SSHD, local="100.64.0.2"):
        X = _xh()
        env = {"AIB_DM_LOCAL_IPS": local}
        if ip:
            env["SSH_CONNECTION"] = "%s 53082 100.64.0.2 22" % ip
        return X.accept(X.encode(label, "abb89", "seat", "aihub-20261014-133230-6ddf"),
                        env=env, pid=300, ps_row=ps)

    def test_U4_accept(self):
        rc, got = self._accept("@abb89(AIB工程師)")
        self.assertEqual(rc, 0, got)
        self.assertEqual(got, {"label": "@abb89(AIB工程師)", "origin": "abb89",
                               "hub": "aihub-20261014-133230-6ddf", "ip": "100.64.0.1"})
        self.assertEqual(self._accept("unattested:unknown")[0], 0, "未認證也收(照實帶過來,不升權)")
        for label, why in (("human:andy", "真人"), ("第二艦長", "白名單"), ("", "空")):
            rc, got = self._accept(label)
            self.assertEqual(rc, 2, label)
            self.assertIn(why, got)
        rc, got = self._accept("@abb89(AIB工程師)", ip="")
        self.assertEqual(rc, 2)
        self.assertIn("SSH_CONNECTION", got)
        rc, got = self._accept("@abb89(AIB工程師)", ps=chain((300, 1, 501, "zsh")))
        self.assertEqual(rc, 2)
        self.assertIn("sshd", got)
        rc, got = self._accept("@abb89(AIB工程師)", ip="100.64.0.2")
        self.assertEqual(rc, 2, "ssh 回本機不算跨主機")
        self.assertIn("本機", got)

    def test_U5_header_shows_remote_ip_and_mark_and_replies_route_back(self):
        import dm_header as HD
        import dm_address as DA
        h = HD.build_from_labels("@8f366(第二艦長)", "@cc203(心+指揮官)", s_socket="aihub-20261013-931e",
                                 t_socket="aihub-20261014-133230-6ddf", s_addr="100.64.0.2",
                                 s_note="跨主機·經SSH", env={"AIB_DM_ADDR": "100.64.0.1"}, when=0)
        self.assertTrue(h.startswith("IP:100.64.0.2/931e/第二艦長(跨主機·經SSH)@8f366 >> "
                                     "IP:100.64.0.1/6ddf/心+指揮官@cc203 "), h)
        got = HD.parse(h + " 你好")
        self.assertEqual((got["sender"]["code"], got["body"]), ("8f366", "你好"))
        self.assertEqual(DA.parse(h.split(" >> ")[0]), {"seat": "8f366", "cabin": "931e", "ip": "100.64.0.2"},
                         "抄抬頭回信要回得到寄件機")
        # 沒給 s_addr/s_note ⇒ 同機抬頭一字不變
        same = HD.build_from_labels("@8f366(第二艦長)", "@cc203(心+指揮官)", env={"AIB_DM_ADDR": "10.0.0.5"}, when=0)
        self.assertTrue(same.startswith("IP:10.0.0.5//第二艦長@8f366 >> "), same)

    def test_U6_remote_cmd_quotes_every_arg(self):
        X = _xh()
        self.assertEqual(X.remote_cmd(["aib", "dm", "--thread", "24/租約 鎖", "ab12/1857a"]),
                         "aib dm --thread '24/租約 鎖' ab12/1857a")


def _sandbox_home(base, env):
    """家目錄換成暫存(與一台沒用過 AIB 的機器同形);指向真 ~/.ez2-aib 的導向鍵拿掉。"""
    home = Path(base) / "sandbox-home"
    home.mkdir(parents=True, exist_ok=True)
    env["HOME"], env["EZ2_AIB_HOME"] = str(home), str(home / ".ez2-aib")
    for k in ("AIB_COMPOSER_NOTICE_DIR", "EZ2_AIB_STATE_ROOT", "EZ2_AIB_INSTANCE_HOME"):
        env.pop(k, None)
    return home


class DmCtl(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        bindir = base / "bin"
        bindir.mkdir()
        for name, body in (("tmux", FAKE_TMUX), ("fake-ssh", FAKE_SSH)):
            (bindir / name).write_text(body, encoding="utf-8")
            (bindir / name).chmod(0o755)
        self.calls, self.ssh_calls = base / "calls", base / "ssh_calls"
        self.ssh_cmd, self.ssh_stdin = base / "ssh_cmd", base / "ssh_stdin"
        self.env = dict(os.environ)
        _cabin.wash_cabin_env(self.env)
        for k in ("AIB_DM_REQUIRE_CABIN", "AIB_DM_HOPPED", "AIB_DM_ADDR_DONE", "AIB_DM_XHOST_WHOAMI",
                  "SSH_CONNECTION", "SSH_CLIENT"):
            self.env.pop(k, None)
        self.home = _sandbox_home(base, self.env)
        self.root = self.home / ".ez2-aib"
        (self.root / "instances" / INST / "seats").mkdir(parents=True)
        (self.root / "seats").mkdir(parents=True)
        self.env.update({"PATH": "%s:%s" % (bindir, self.env.get("PATH", "")),
                         "AIB_DM_TMUX": str(bindir / "tmux"), "DM_CALLS": str(self.calls),
                         "AIB_DM_LOCAL_IPS": "10.0.0.5", "TMPDIR": str(base), "AIB_DM_SETTLE_SLEEP": "0",
                         "AIB_DM_XHOST_SSH": str(bindir / "fake-ssh"), "SSH_CALLS": str(self.ssh_calls),
                         "SSH_CMD": str(self.ssh_cmd), "SSH_STDIN": str(self.ssh_stdin),
                         "AIB_DM_XHOST_HOSTS": str(base / "dm-hosts.json")})
        (base / "dm-hosts.json").write_text(json.dumps({"100.1.2.3": "far-box"}), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def run_dm(self, *args, **env):
        e = dict(self.env)
        e.update(env)
        return subprocess.run(["bash", str(CTL), *args], capture_output=True, text=True,
                              timeout=60, env=e, stdin=subprocess.DEVNULL)

    def read(self, p):
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def test_X1_remote_address_goes_over_ssh_with_derived_identity(self):
        p = self.run_dm("100.1.2.3/ab12/1857a", "你好,跨主機", "--thread", "24/租約")
        self.assertEqual(p.returncode, 0, p.stderr[-600:])
        results = [l for l in p.stdout.splitlines() if l.startswith("RESULT ")]
        self.assertEqual(results, ["RESULT stage=delivered id=q_0000000000000000 relay=- thread=-"],
                         "收件機的 RESULT 行原樣帶回,本機不再多印一行")
        calls = self.read(self.ssh_calls).splitlines()
        self.assertEqual(len(calls), 2, calls)
        self.assertTrue(calls[0].endswith("far-box true"), "先試連線:%r" % calls[0])
        self.assertIn("BatchMode=yes", calls[0], "不能卡在密碼提示")
        cmd = self.read(self.ssh_cmd)
        self.assertTrue(cmd.startswith("aib dm --xhost-ident "), cmd)
        self.assertIn(" ab12/1857a", cmd)
        self.assertNotIn("100.1.2.3", cmd, "收件機收到的是它自己的 艙/席,不再帶 IP(免得又判成別台)")
        self.assertIn("--thread '24/租約'", cmd)
        self.assertEqual(self.read(self.ssh_stdin), "你好,跨主機", "正文走 stdin")
        import dm_xhost as X
        tok = cmd.split("--xhost-ident ", 1)[1].split()[0]
        ident = X.decode(tok)
        self.assertIsNotNone(ident, tok)
        self.assertTrue(ident["label"].startswith("unattested:"), "沙盒裡沒有席 ⇒ 照實寫未認證,不猜:%r" % ident)
        tm = self.read(self.calls)
        self.assertNotIn("send-keys", tm, "寄件機只讀本機艙推寄件人,不貼任何東西")
        self.assertNotIn("paste-buffer", tm)

    def test_X2_ssh_unreachable_is_rc2_nothing_sent(self):
        p = self.run_dm("100.1.2.3/ab12/1857a", "hi", SSH_PROBE_RC="255")
        self.assertEqual(p.returncode, 2, p.stderr[-400:])
        self.assertIn("一個字都沒送", p.stderr)
        self.assertEqual(self.read(self.ssh_cmd), "", "連不上就不該送出正文")
        self.assertEqual(self.read(self.calls), "")

    def test_X3_ssh_drops_midway_is_rc4_do_not_resend(self):
        p = self.run_dm("100.1.2.3/ab12/1857a", "hi", SSH_CMD_RC="255")
        self.assertEqual(p.returncode, 4, p.stderr[-400:])
        self.assertIn("RESULT stage=xhost-unknown", p.stdout.splitlines()[-1])

    def test_X4_receiver_rc_passes_through(self):
        p = self.run_dm("100.1.2.3/ab12/1857a", "hi", SSH_CMD_RC="3")
        self.assertEqual(p.returncode, 3, p.stderr[-400:])

    def test_X4b_remote_aib_missing_is_rc2(self):
        """收件機叫不起 aib(rc 127、沒有 RESULT 行)⇒ 沒進任何艙 ⇒ rc 2;叫法可寫成多個字。"""
        p = self.run_dm("100.1.2.3/ab12/1857a", "hi", SSH_CMD_RC="127", SSH_NO_RESULT="1",
                        AIB_DM_XHOST_REMOTE_AIB="bash /opt/aib/src/cockpit/aib")
        self.assertEqual(p.returncode, 2, p.stderr[-400:])
        self.assertIn("一個字都沒送", p.stderr)
        self.assertTrue(self.read(self.ssh_cmd).startswith("bash /opt/aib/src/cockpit/aib dm --xhost-ident "),
                        self.read(self.ssh_cmd))
        self.assertEqual(sum(l.startswith("RESULT ") for l in p.stdout.splitlines()), 1)

    def test_X5_receiver_refuses_ident_outside_sshd(self):
        """本機行程自己帶 --xhost-ident(或設個 SSH_CONNECTION 假裝)⇒ rc 2,一個字都沒進艙。"""
        import dm_xhost as X
        tok = X.encode("@8f366(第二艦長)", "8f366", "seat", "aihub-20261013-931e")
        for extra in ({}, {"SSH_CONNECTION": "100.9.9.9 1 10.0.0.5 22"}):
            p = self.run_dm("--xhost-ident", tok, "ab12/1857a", "hi", **extra)
            self.assertEqual(p.returncode, 2, p.stderr[-400:])
            self.assertIn("跨主機投遞拒收", p.stderr)
            self.assertEqual(self.read(self.calls), "", "拒收不該碰任何艙")

    def test_X6_remote_needs_cabin(self):
        p = self.run_dm("100.1.2.3/@1857a", "hi")
        self.assertEqual(p.returncode, 2, p.stderr[-400:])
        self.assertEqual(self.read(self.ssh_calls), "")


if __name__ == "__main__":
    unittest.main()
