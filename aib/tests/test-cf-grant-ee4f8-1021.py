#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib cf grant:每次動 Cloudflare 先 Touch ID,再現開短命、限網域、限權限、鎖 IP 的 token(維護者 10-21)。

G1 參數:權限只收白名單短名;ttl 只收 1–60 分鐘;壞 zone / 壞變數名 / 壞 --ip ⇒ rc 2,沒碰 Cloudflare、沒叫 Touch ID
G2 IP 鎖:連線 IP + 目的主機對 Cloudflare 的出口 IP(v4 + v6)+ --ip;主機名解不出 IP ⇒ no_ip_lock、rc 2
G3 正路:POST body 有 not_before / expires_on(= 現在 + ttl)、condition request.ip、zone 權限只給那個 zone、帳號權限只給那個帳號、
   權限群組 id 對;值只走 ssh stdin(VAR=value),輸出 / 稽核都沒有值;輸出帶 token_id、指紋、ip_lock
G4 Touch ID 沒過 / AIB_TOUCHID=0 / 不是 Mac ⇒ 沒 POST;稽核記 refused
G5 送不到目的主機 ⇒ 立刻 DELETE 剛建的 token、why push_failed_revoked;DELETE 也失敗 ⇒ rc 5、輸出 token_id
G6 --dry-run:查 zone / 權限群組、印計畫;不叫 Touch ID、不 POST、不 ssh 寫
G7 母鑰:金鑰櫃沒有 CF_TOKEN_MINTER ⇒ no_minter + 提示;母鑰讀不到 zone ⇒ zone_unreadable + 提示(要另加 Zone Read)
G8 金鑰櫃認得 CF_ / CLOUDFLARE_ 名字(PROPOSAL-gaps E);從金鑰櫃讀母鑰走 enc.v1. 解密
G9 list 不印值;revoke 要 Touch ID,沒過不 DELETE;壞 token id ⇒ rc 2
G10 secret ask 叫不出視窗(-1713 / -1743 / -10827)⇒ why no_gui + 提示自己用 ! 跑;其他錯誤照舊 error
刀(複本):K1 拿掉 Touch ID 關卡 ⇒ G4 紅;K2 不放 IP 鎖 ⇒ G3 紅;K3 送不到不撤 ⇒ G5 紅;K4 不設 expires_on ⇒ G3 紅
"""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "src" / "bootstrap"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import cf_grant as CG  # noqa: E402
import secret_ask as SA  # noqa: E402

TOKEN = "cfTok_" + "Qw7" * 14            # 假的新 token 值
MINTER = "cfMint_" + "Zp4" * 12          # 假的母鑰
ZID, AID, NEW_ID = "z" * 32, "a" * 32, "1" * 32
GROUPS = [{"id": "g-" + n.replace(" ", "-").lower(), "name": n} for n, _s in CG.PERMS.values()]
GOUT = "user root\nhostname 203.0.113.7\nport 22\nproxycommand none\nproxyjump none\n"
TRACE = "fl=1\nip=203.0.113.7\nts=1\n---\nfl=1\nip=2001:db8::7\nts=1\n"
NOW = 1792540800                          # 2026-10-21T00:00:00Z


class Resp:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class CF:
    """假 Cloudflare:記下每一個請求,照 method + path 回。"""

    def __init__(self, zones=True, delete_ok=True, create_ok=True):
        self.calls, self.zones, self.delete_ok, self.create_ok = [], zones, delete_ok, create_ok

    def __call__(self, req, timeout=None):
        body = json.loads(req.data.decode()) if req.data else None
        path = req.full_url[len(CG.API):]
        self.calls.append((req.get_method(), path, body, req.get_header("Authorization")))
        m = req.get_method()
        if m == "GET" and path.startswith("/zones?name="):
            return Resp({"success": True, "result": [{"id": ZID, "account": {"id": AID}}] if self.zones else []})
        if m == "GET" and path == "/user/tokens/permission_groups":
            return Resp({"success": True, "result": GROUPS})
        if m == "POST" and path == "/user/tokens":
            return Resp({"success": self.create_ok, "result": {"id": NEW_ID, "value": TOKEN}})
        if m == "DELETE":
            return Resp({"success": self.delete_ok, "result": {"id": path.rsplit("/", 1)[1]}})
        if m == "GET" and path.startswith("/user/tokens?"):
            return Resp({"success": True, "result": [{"id": NEW_ID, "name": "aib-grant x", "status": "active", "value": TOKEN,
                                                     "policies": [{"permission_groups": [{"name": "DNS Write"}]}]}]})
        raise AssertionError("unexpected %s %s" % (m, path))

    def methods(self):
        return [c[0] + " " + c[1].split("?")[0] for c in self.calls]


def sshfake(calls, push_rc=0, trace=TRACE, gout=GOUT):
    def run(cmd, input=None, **kw):
        if cmd[:2] == ["ssh", "-G"]:
            return type("R", (), {"returncode": 0, "stdout": gout})()
        if "cdn-cgi/trace" in cmd[-1]:
            calls.append(("trace", cmd, input))
            return type("R", (), {"returncode": 0, "stdout": trace})()
        calls.append(("push", cmd, input))
        return type("R", (), {"returncode": push_rc, "stdout": ""})()
    return run


class Touch:
    def __init__(self, ok=True, avail=True):
        self.ok, self.avail, self.n, self.reason = ok, avail, 0, None

    def __call__(self, reason):
        self.n += 1
        self.reason = reason
        return {"ok": self.ok, "why": "ok" if self.ok else "cancel"}


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-cf-grant-")
        root = Path(self.td.name)
        self.state = root / "state"
        self.env = {"HOME": str(root), "EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_AIB_SECRETS_DIR": str(root / "secrets"),
                    "EZ2_AIB_LOCALE": "zh-TW", "AIB_SEAT_CODE": "ee4f8"}
        os.environ.update({"EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_AIB_SECRETS_DIR": str(root / "secrets")})

    def tearDown(self):
        self.td.cleanup()

    def grant(self, argv, cf=None, touch=None, ssh=None, platform="darwin", env=None, minter=lambda: (MINTER, None)):
        self.cf, self.touch, self.ssh_calls = cf or CF(), touch or Touch(), []
        out, err = io.StringIO(), io.StringIO()
        rc = CG.grant_cmd(argv, env=dict(self.env, **(env or {})), platform=platform, touchid=self.touch,
                          touchid_available=lambda: self.touch.avail, opener=self.cf, ssh_run=ssh or sshfake(self.ssh_calls),
                          getaddr=lambda h, p: [(0, 0, 0, "", ("198.51.100.9", 0))], minter=minter, now=NOW, out=out, err=err)
        text = out.getvalue()
        self.err = err.getvalue()
        for blob in (text, self.err, self.audit_text()):
            self.assertNotIn(TOKEN, blob)
            self.assertNotIn(MINTER, blob)
        return rc, json.loads(text.strip().splitlines()[-1])

    def audit_text(self):
        p = self.state / "audit" / "cf-grant.jsonl"
        return p.read_text() if p.is_file() else ""

    def audit_rows(self):
        return [json.loads(ln) for ln in self.audit_text().splitlines() if ln.strip()]

    def posts(self):
        return [c for c in self.cf.calls if c[0] == "POST"]


class CfGrant(Base):
    OK = ["ez2.app", "dns-write", "--push", "eu01:/etc/ez2-cloud/grant.env"]

    def test_g1_args(self):
        for argv, why in ((["ez2.app", "dns-admin", "--push", "eu01:/a/b"], "bad_perm"),
                          (["ez2.app", "", "--push", "eu01:/a/b"], "no_perm"),
                          (["ez2.app", "dns-write", "--push", "eu01:/a/b", "--ttl", "0m"], "bad_ttl"),
                          (["ez2.app", "dns-write", "--push", "eu01:/a/b", "--ttl", "61m"], "bad_ttl"),
                          (["ez2.app", "dns-write", "--push", "eu01:/a/b", "--ttl", "2h"], "bad_ttl"),
                          (["ez2..app", "dns-write", "--push", "eu01:/a/b"], "bad_zone"),
                          (["ez2.app", "dns-write", "--push", "eu01:/a/b", "--var", "x;rm"], "bad_var"),
                          (["ez2.app", "dns-write", "--push", "eu01:rel/path"], "bad_push"),
                          (["ez2.app", "dns-write", "--push", "eu01:/a/b", "--ip", "not-an-ip"], "bad_ip")):
            rc, res = self.grant(argv)
            self.assertEqual((rc, res["why"]), (2, why), argv)
            self.assertEqual(self.cf.calls, [], argv)
            self.assertEqual(self.touch.n, 0, argv)
        self.assertEqual(CG.parse_perms("cache-purge,dns-write,dns-write"), ("dns-write", "cache-purge"))
        self.assertEqual(CG.parse_ttl("60"), 60)
        self.assertEqual(self.audit_rows(), [])          # 參數錯不算核准

    def test_g2_ip_lock(self):
        rc, res = self.grant(self.OK + ["--ip", "192.0.2.0/24"])
        self.assertEqual(rc, 0, res)
        self.assertEqual(res["ip_lock"], ["192.0.2.0/24", "2001:db8::7/128", "203.0.113.7/32"])
        # 連線 IP 是主機名、解不出來 ⇒ 拒
        g = GOUT.replace("203.0.113.7", "eu01.example")
        self.cf, out = CF(), io.StringIO()
        rc = CG.grant_cmd(self.OK, env=self.env, platform="darwin", touchid=Touch(), touchid_available=lambda: True,
                          opener=self.cf, ssh_run=sshfake([], gout=g), getaddr=lambda h, p: (_ for _ in ()).throw(OSError()),
                          minter=lambda: (MINTER, None), now=NOW, out=out, err=io.StringIO())
        self.assertEqual((rc, json.loads(out.getvalue())["why"]), (2, "no_ip_lock"))
        self.assertEqual(self.cf.calls, [])
        # 主機名解得出來 ⇒ 用解出的 IP;出口問不到 ⇒ 只鎖連線 IP
        rc, res = self.grant(self.OK, ssh=sshfake([], trace="", gout=g))
        self.assertEqual(res["ip_lock"], ["198.51.100.9/32"])

    def test_g3_happy(self):
        rc, res = self.grant(self.OK + ["--ttl", "15m", "--var", "CF_DNS_TOKEN", "--why", "切換 DNS"])
        self.assertEqual(rc, 0, res)
        self.assertEqual(self.cf.methods(), ["GET /zones", "GET /user/tokens/permission_groups", "POST /user/tokens"])
        self.assertTrue(all(c[3] == "Bearer " + MINTER for c in self.cf.calls))
        body = self.posts()[0][2]
        self.assertEqual(body["not_before"], "2026-10-21T00:00:00Z")
        self.assertEqual(body["expires_on"], "2026-10-21T00:15:00Z")
        self.assertEqual(body["condition"], {"request.ip": {"in": ["2001:db8::7/128", "203.0.113.7/32"]}})
        self.assertEqual(body["policies"], [{"effect": "allow", "permission_groups": [{"id": "g-dns-write"}],
                                             "resources": {"com.cloudflare.api.account.zone." + ZID: "*"}}])
        self.assertTrue(body["name"].startswith("aib-grant ez2.app dns-write eu01 "))
        push = [c for c in self.ssh_calls if c[0] == "push"]
        self.assertEqual(len(push), 1)
        self.assertEqual(push[0][2], "CF_DNS_TOKEN=%s\n" % TOKEN)            # 值只在 stdin
        self.assertNotIn(TOKEN, " ".join(push[0][1]))
        self.assertIn("ProxyCommand=none", push[0][1])
        self.assertEqual((res["token_id"], res["fingerprint"], res["expires_on"]), (NEW_ID, CG.fingerprint(TOKEN), "2026-10-21T00:15:00Z"))
        self.assertIn("203.0.113.7", self.touch.reason)                       # 視窗印真的連的那台
        self.assertIn("dns-write", self.touch.reason)
        row = self.audit_rows()[-1]
        self.assertEqual((row["result"], row["token_id"], row["seat"], row["zone"], row["ttl"]), ("ok", NEW_ID, "ee4f8", "ez2.app", 15))
        # 帳號層權限只給那個帳號
        rc, res = self.grant(["ez2.app", "zone-read,workers-write", "--push", "eu01:/etc/x.env"])
        pol = self.posts()[0][2]["policies"]
        self.assertEqual(pol[0]["resources"], {"com.cloudflare.api.account.zone." + ZID: "*"})
        self.assertEqual(pol[1], {"effect": "allow", "permission_groups": [{"id": "g-workers-scripts-write"}],
                                  "resources": {"com.cloudflare.api.account." + AID: "*"}})

    def test_g4_touchid_gate(self):
        for kw, why in (({"touch": Touch(ok=False)}, "touchid_cancel"),
                        ({"env": {"AIB_TOUCHID": "0"}}, "touchid_off"),
                        ({"touch": Touch(avail=False)}, "touchid_unavailable"),
                        ({"platform": "linux"}, "touchid_unavailable")):
            rc, res = self.grant(self.OK, **kw)
            self.assertEqual((rc, res["why"]), (2, why), kw)
            self.assertEqual(self.posts(), [], kw)
            self.assertFalse([c for c in self.ssh_calls if c[0] == "push"], kw)
        self.assertEqual([r["result"] for r in self.audit_rows()], ["refused"] * 4)

    def test_g5_push_failed_revokes(self):
        rc, res = self.grant(self.OK, ssh=sshfake([], push_rc=1))
        self.assertEqual((rc, res["why"], res["ok"]), (2, "push_failed_revoked", False))
        self.assertEqual(self.cf.methods()[-1], "DELETE /user/tokens/" + NEW_ID)
        rc, res = self.grant(self.OK, cf=CF(delete_ok=False), ssh=sshfake([], push_rc=1))
        self.assertEqual((rc, res["why"], res["token_id"]), (5, "push_failed_revoke_failed", NEW_ID))

    def test_g6_dry_run(self):
        rc, res = self.grant(self.OK + ["--dry-run"])
        self.assertEqual((rc, res["ok"], res["dry_run"]), (0, True, True))
        self.assertEqual(self.cf.methods(), ["GET /zones", "GET /user/tokens/permission_groups"])
        self.assertEqual(self.touch.n, 0)
        self.assertFalse([c for c in self.ssh_calls if c[0] == "push"])
        self.assertEqual(self.audit_rows(), [])

    def test_g7_minter(self):
        rc, res = self.grant(self.OK, minter=lambda: (None, "no_minter"))
        self.assertEqual((rc, res["why"]), (2, "no_minter"))
        self.assertIn("aib secret ask CF_TOKEN_MINTER", self.err)
        self.assertEqual(self.cf.calls, [])
        rc, res = self.grant(self.OK, cf=CF(zones=False))
        self.assertEqual((rc, res["why"]), (2, "zone_unreadable"))
        self.assertIn("Zone Read", self.err)
        self.assertEqual(self.touch.n, 0)

    def test_g8_vault_names(self):
        import aib_secrets as S
        import secret_collect as SC
        for n in ("CF_TOKEN_MINTER", "CF_WORKER_TOKEN", "CLOUDFLARE_API_TOKEN"):
            self.assertEqual(S.file_for_name(n), "cloudflare.env", n)
        SC.put_secret("CF_TOKEN_MINTER", MINTER, env=self.env)
        raw = (Path(self.env["EZ2_AIB_SECRETS_DIR"]) / "cloudflare.env").read_text()
        self.assertNotIn(MINTER, raw)
        self.assertEqual(CG.read_minter(self.env), (MINTER, None))
        empty = dict(self.env, EZ2_AIB_SECRETS_DIR=str(Path(self.td.name) / "none"))
        self.assertEqual(CG.read_minter(empty), (None, "no_minter"))

    def test_g9_list_revoke(self):
        cf, out = CF(), io.StringIO()
        self.assertEqual(CG.list_cmd([], env=self.env, opener=cf, minter=lambda: (MINTER, None), out=out), 0)
        self.assertNotIn(TOKEN, out.getvalue())
        self.assertEqual(json.loads(out.getvalue())["tokens"][0]["id"], NEW_ID[:8])
        for ok, rc_want in ((False, 2), (True, 0)):
            cf, out, t = CF(), io.StringIO(), Touch(ok=ok)
            rc = CG.revoke_cmd([NEW_ID], env=self.env, platform="darwin", touchid=t, touchid_available=lambda: True,
                               opener=cf, minter=lambda: (MINTER, None), now=NOW, out=out)
            self.assertEqual(rc, rc_want)
            self.assertEqual(cf.methods(), ["DELETE /user/tokens/" + NEW_ID] if ok else [])
        out = io.StringIO()
        self.assertEqual(CG.revoke_cmd(["../x"], env=self.env, minter=lambda: (MINTER, None), out=out), 2)
        self.assertEqual([r["action"] for r in self.audit_rows()], ["revoke", "revoke"])

    def test_g10_secret_ask_no_gui(self):
        def runner(stdout, stderr="", rc=0):
            return lambda cmd, input=None, **kw: type("R", (), {"stdout": stdout, "stderr": stderr, "returncode": rc})()
        self.assertEqual(SA.dialog("p", "t", run=runner(json.dumps({"ok": False, "why": "error", "code": "-1713"})))["why"], "no_gui")
        self.assertEqual(SA.dialog("p", "t", run=runner(json.dumps({"ok": False, "why": "error", "code": "-2700"})))["why"], "error")
        self.assertEqual(SA.dialog("p", "t", run=runner("", "execution error: Not authorized (-1743)", 1))["why"], "no_gui")
        self.assertEqual(SA.dialog("p", "t", run=runner("", "boom", 1))["why"], "unreadable")
        out, err = io.StringIO(), io.StringIO()
        rc = SA.ask_cmd(["CF_WORKER_TOKEN"], env=self.env, platform="darwin", ask=lambda p, t: {"ok": False, "why": "no_gui"},
                        touchid=Touch(), touchid_available=lambda: True, out=out, err=err)
        self.assertEqual((rc, json.loads(out.getvalue())["why"]), (2, "no_gui"))
        self.assertIn("!", err.getvalue())


class Knives(unittest.TestCase):
    def _red(self, needle, repl, test):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/cf_grant.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "CfGrant." + test], cwd=str(root), max_procs=60, timeout=300)
        self.assertNotEqual(r.returncode, 0, "刀落了測試還綠:" + test)

    def test_k1_touchid_skipped(self):
        self._red('    return None if res.get("ok") is True else "touchid_"', '    return None\n    return None if res.get("ok") is True else "touchid_"',
                  "test_g4_touchid_gate")

    def test_k2_no_ip_lock(self):
        self._red('            "condition": {"request.ip": {"in": list(ips)}}}', "            }", "test_g3_happy")

    def test_k3_no_revoke(self):
        self._red('                api(opener, key, "DELETE", "/user/tokens/%s" % res["token_id"])\n', "", "test_g5_push_failed_revokes")

    def test_k4_no_expiry(self):
        self._red('"not_before": _iso(now), "expires_on": _iso(now + ttl * 60),', '"not_before": _iso(now),', "test_g3_happy")


if __name__ == "__main__":
    unittest.main()
