#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib cf grant:每次動 Cloudflare 先 Touch ID,再現開短命、限網域、限權限、鎖 IP 的 token(維護者 10-21)。
母鑰只封在 Secure Enclave(se_vault);測試用假的 unseal(不叫真的 SE / Touch ID)、假 Cloudflare、假 ssh。
狀態根一律指到暫存目錄:EZ2_AIB_STATE_ROOT / EZ2_AIB_SECRETS_DIR(setUp)。

G1 參數:權限只收白名單短名;ttl 只收 1–10 分鐘;壞 zone / 壞變數名 / 壞 --ip ⇒ rc 2,沒碰 Cloudflare、沒叫解封
G2 IP 鎖:連線 IP + 目的主機對 Cloudflare 的出口 IP(v4 + v6)+ --ip;主機名解不出 IP ⇒ no_ip_lock、rc 2
G3 正路:POST body 有 not_before / expires_on(= 現在 + ttl)、condition request.ip、zone 權限只給那個 zone、帳號權限只給那個帳號、
   權限群組 id 對;值只走 ssh stdin(VAR=value),輸出 / 稽核都沒有值;輸出帶 token_id、指紋、ip_lock
G4 解封沒過(cancel / 沒生物辨識)/ AIB_TOUCHID=0 / 不是 Mac ⇒ 沒打任何 Cloudflare API、沒 POST;稽核記 refused
G5 送不到目的主機 ⇒ 立刻 DELETE 剛建的 token、why push_failed_revoked;DELETE 也失敗 ⇒ rc 5、輸出 token_id
G6 --dry-run:只驗參數與 IP 鎖、印計畫;不解封、不打 API、不 ssh 寫
G7 母鑰:SE 沒封過 ⇒ no_minter + 提示;母鑰讀不到 zone ⇒ zone_unreadable + 提示(要另加 Zone Read)
G8 金鑰櫃認得 CF_ / CLOUDFLARE_ 名字(PROPOSAL-gaps E);CF_TOKEN_MINTER 金鑰櫃拒寫(se_only);secret ask 那個名字改走 SE 封存、
   拒 --push / --remote-only;SE_ONLY 兩份清單一致
G9 list / revoke 都經解封;revoke 解封沒過不 DELETE;壞 token id ⇒ rc 2
G10 secret ask 叫不出視窗(-1713 / -1743 / -10827)⇒ why no_gui + 提示自己用 ! 跑;其他錯誤照舊 error
G11 金鑰櫃檔案裡有 CF_TOKEN_MINTER ⇒ grant / list / revoke 一律拒(minter_in_file)、不解封、不打 API
G12 解封失敗 ⇒ 不退回任何別處拿母鑰(就算金鑰櫃、環境變數裡有一份)
刀(複本):K1 解封失敗也照走 ⇒ G4 紅;K2 不放 IP 鎖 ⇒ G3 紅;K3 送不到不撤 ⇒ G5 紅;K4 不設 expires_on ⇒ G3 紅;
          K5 拿掉「檔裡有母鑰就拒」⇒ G11 紅;K6 ttl 上限放回 60 ⇒ G1 紅
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


class Unseal:
    """假的 SE 解封:記下視窗那句話與次數;ok=False 時回 (None, why)。"""

    def __init__(self, ok=True, why="cancel"):
        self.ok, self.why, self.n, self.reason = ok, why, 0, None

    def __call__(self, reason):
        self.n += 1
        self.reason = reason
        return (MINTER, None) if self.ok else (None, self.why)


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

    def grant(self, argv, cf=None, touch=None, ssh=None, platform="darwin", env=None):
        self.cf, self.touch, self.ssh_calls = cf or CF(), touch or Unseal(), []
        out, err = io.StringIO(), io.StringIO()
        rc = CG.grant_cmd(argv, env=dict(self.env, **(env or {})), platform=platform, unseal=self.touch,
                          opener=self.cf, ssh_run=ssh or sshfake(self.ssh_calls),
                          getaddr=lambda h, p: [(0, 0, 0, "", ("198.51.100.9", 0))], now=NOW, out=out, err=err)
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
                          (["ez2.app", "dns-write", "--push", "eu01:/a/b", "--ttl", "11m"], "bad_ttl"),
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
        self.assertEqual(CG.parse_ttl("10"), 10)
        self.assertEqual(self.audit_rows(), [])          # 參數錯不算核准

    def test_g2_ip_lock(self):
        rc, res = self.grant(self.OK + ["--ip", "192.0.2.0/24"])
        self.assertEqual(rc, 0, res)
        self.assertEqual(res["ip_lock"], ["192.0.2.0/24", "2001:db8::7/128", "203.0.113.7/32"])
        # 連線 IP 是主機名、解不出來 ⇒ 拒
        g = GOUT.replace("203.0.113.7", "eu01.example")
        self.cf, out = CF(), io.StringIO()
        rc = CG.grant_cmd(self.OK, env=self.env, platform="darwin", unseal=Unseal(),
                          opener=self.cf, ssh_run=sshfake([], gout=g), getaddr=lambda h, p: (_ for _ in ()).throw(OSError()),
                          now=NOW, out=out, err=io.StringIO())
        self.assertEqual((rc, json.loads(out.getvalue())["why"]), (2, "no_ip_lock"))
        self.assertEqual(self.cf.calls, [])
        # 主機名解得出來 ⇒ 用解出的 IP;出口問不到 ⇒ 只鎖連線 IP
        rc, res = self.grant(self.OK, ssh=sshfake([], trace="", gout=g))
        self.assertEqual(res["ip_lock"], ["198.51.100.9/32"])

    def test_g3_happy(self):
        rc, res = self.grant(self.OK + ["--ttl", "5m", "--var", "CF_DNS_TOKEN", "--why", "切換 DNS"])
        self.assertEqual(rc, 0, res)
        self.assertEqual(self.cf.methods(), ["GET /zones", "GET /user/tokens/permission_groups", "POST /user/tokens"])
        self.assertTrue(all(c[3] == "Bearer " + MINTER for c in self.cf.calls))
        body = self.posts()[0][2]
        self.assertEqual(body["not_before"], "2026-10-21T00:00:00Z")
        self.assertEqual(body["expires_on"], "2026-10-21T00:05:00Z")
        self.assertEqual(body["condition"], {"request.ip": {"in": ["2001:db8::7/128", "203.0.113.7/32"]}})
        self.assertEqual(body["policies"], [{"effect": "allow", "permission_groups": [{"id": "g-dns-write"}],
                                             "resources": {"com.cloudflare.api.account.zone." + ZID: "*"}}])
        self.assertTrue(body["name"].startswith("aib-grant ez2.app dns-write eu01 "))
        push = [c for c in self.ssh_calls if c[0] == "push"]
        self.assertEqual(len(push), 1)
        self.assertEqual(push[0][2], "CF_DNS_TOKEN=%s\n" % TOKEN)            # 值只在 stdin
        self.assertNotIn(TOKEN, " ".join(push[0][1]))
        self.assertIn("ProxyCommand=none", push[0][1])
        self.assertEqual((res["token_id"], res["fingerprint"], res["expires_on"]), (NEW_ID, CG.fingerprint(TOKEN), "2026-10-21T00:05:00Z"))
        self.assertIn("203.0.113.7", self.touch.reason)                       # 視窗印真的連的那台
        self.assertIn("dns-write", self.touch.reason)
        self.assertIn("ez2.app", self.touch.reason)
        self.assertIn("5m", self.touch.reason)
        self.assertEqual(self.touch.n, 1)                                     # 只有一次核准(SE 那次),不疊兩個指紋
        row = self.audit_rows()[-1]
        self.assertEqual((row["result"], row["token_id"], row["seat"], row["zone"], row["ttl"]), ("ok", NEW_ID, "ee4f8", "ez2.app", 5))
        # 帳號層權限只給那個帳號
        rc, res = self.grant(["ez2.app", "zone-read,workers-write", "--push", "eu01:/etc/x.env"])
        pol = self.posts()[0][2]["policies"]
        self.assertEqual(pol[0]["resources"], {"com.cloudflare.api.account.zone." + ZID: "*"})
        self.assertEqual(pol[1], {"effect": "allow", "permission_groups": [{"id": "g-workers-scripts-write"}],
                                  "resources": {"com.cloudflare.api.account." + AID: "*"}})

    def test_g3b_redirect_perms(self):
        # 邊緣轉址:Single Redirect 寫只給那個 zone;Bulk Redirect / 清單只讀、只給那個帳號
        self.assertEqual(CG.parse_perms("rule-lists-read,redirect-write,bulk-redirect-read"),
                         ("redirect-write", "bulk-redirect-read", "rule-lists-read"))
        self.assertEqual({p: s for p, (_n, s) in CG.PERMS.items() if "redirect" in p or "lists" in p},
                         {"redirect-read": "zone", "redirect-write": "zone", "bulk-redirect-read": "account", "rule-lists-read": "account"})
        self.assertNotIn("Write", CG.PERMS["bulk-redirect-read"][0] + CG.PERMS["rule-lists-read"][0])
        rc, res = self.grant(["ez2.app", "redirect-write,bulk-redirect-read,rule-lists-read", "--push", "eu01:/etc/x.env"])
        self.assertEqual(rc, 0, res)
        pol = self.posts()[0][2]["policies"]
        self.assertEqual(pol[0], {"effect": "allow", "permission_groups": [{"id": "g-dynamic-url-redirects-write"}],
                                  "resources": {"com.cloudflare.api.account.zone." + ZID: "*"}})
        self.assertEqual(pol[1], {"effect": "allow",
                                  "permission_groups": [{"id": "g-mass-url-redirects-read"}, {"id": "g-account-rule-lists-read"}],
                                  "resources": {"com.cloudflare.api.account." + AID: "*"}})

    def test_g4_touchid_gate(self):
        for kw, why in (({"touch": Unseal(ok=False, why="cancel")}, "touchid_cancel"),
                        ({"touch": Unseal(ok=False, why="no_biometry")}, "touchid_unavailable"),
                        ({"env": {"AIB_TOUCHID": "0"}}, "touchid_off"),
                        ({"platform": "linux"}, "touchid_unavailable")):
            rc, res = self.grant(self.OK, **kw)
            self.assertEqual((rc, res["why"]), (2, why), kw)
            self.assertEqual(self.cf.calls, [], kw)                          # 沒母鑰 ⇒ 連 zone 都沒查
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
        self.assertEqual(self.cf.calls, [])
        self.assertEqual(self.touch.n, 0)
        self.assertFalse([c for c in self.ssh_calls if c[0] == "push"])
        self.assertEqual(self.audit_rows(), [])

    def test_g7_minter(self):
        rc, res = self.grant(self.OK, touch=Unseal(ok=False, why="not_found"))
        self.assertEqual((rc, res["why"]), (2, "no_minter"))
        self.assertIn("aib secret ask CF_TOKEN_MINTER", self.err)
        self.assertIn("Secure Enclave", self.err)
        self.assertEqual(self.cf.calls, [])
        rc, res = self.grant(self.OK, cf=CF(zones=False))
        self.assertEqual((rc, res["why"]), (2, "zone_unreadable"))
        self.assertIn("Zone Read", self.err)
        self.assertEqual(self.posts(), [])
        # 預設路徑(沒注入 unseal)= se_vault:沒封過 ⇒ no_minter,不碰 swiftc / SE
        import se_vault as SV
        self.assertEqual(SV.unseal("CF_TOKEN_MINTER", "r", env=self.env, run=lambda *a, **k: self.fail("不該跑輔助程式")),
                         (None, "not_found"))

    def test_g8_vault_names(self):
        import aib_secrets as S
        import secret_collect as SC
        import se_vault as SV
        for n in ("CF_TOKEN_MINTER", "CF_WORKER_TOKEN", "CLOUDFLARE_API_TOKEN", "CF_API_TOKEN"):
            self.assertEqual(S.file_for_name(n), "cloudflare.env", n)
        self.assertEqual(tuple(S.SE_ONLY_NAMES), tuple(SV.SE_ONLY))
        with self.assertRaises(S.SecretError) as cm:
            SC.put_secret("CF_TOKEN_MINTER", MINTER, env=self.env)
        self.assertEqual(cm.exception.reason, "se_only")
        self.assertFalse((Path(self.env["EZ2_AIB_SECRETS_DIR"]) / "cloudflare.env").exists())
        SC.put_secret("CF_API_TOKEN", TOKEN, env=self.env)                    # 一般 CF_ token 照舊進金鑰櫃(加密)
        self.assertNotIn(TOKEN, (Path(self.env["EZ2_AIB_SECRETS_DIR"]) / "cloudflare.env").read_text())
        # secret ask CF_TOKEN_MINTER ⇒ 輸入框 → 指紋 → SE 封存;不寫檔
        sealed = []
        out = io.StringIO()
        rc = SA.ask_cmd(["CF_TOKEN_MINTER"], env=self.env, platform="darwin", ask=lambda p, t: {"ok": True, "value": MINTER},
                        touchid=lambda r: {"ok": True}, touchid_available=lambda: True, out=out, err=io.StringIO(),
                        seal=lambda n, v, env=None: (sealed.append((n, v)), ("abcd1234", None))[1])
        res = json.loads(out.getvalue())
        self.assertEqual((rc, res["ok"], res["sealed"], res["file"], res["fingerprint"]), (0, True, True, "se:CF_TOKEN_MINTER", "abcd1234"))
        self.assertEqual(sealed, [("CF_TOKEN_MINTER", MINTER)])
        self.assertNotIn(MINTER, out.getvalue())
        self.assertNotIn("CF_TOKEN_MINTER", (Path(self.env["EZ2_AIB_SECRETS_DIR"]) / "cloudflare.env").read_text())
        for extra in (["--push", "eu01:/etc/x.env"], ["--push", "eu01:/etc/x.env", "--remote-only"], ["--check"]):
            out, err = io.StringIO(), io.StringIO()
            rc = SA.ask_cmd(["CF_TOKEN_MINTER"] + extra, env=self.env, platform="darwin",
                            ask=lambda p, t: self.fail("不該跳輸入框"), out=out, err=err, seal=lambda *a, **k: self.fail("不該封"))
            self.assertEqual((rc, json.loads(out.getvalue())["why"]), (2, "se_only_local"), extra)

    def test_g9_list_revoke(self):
        cf, out, u = CF(), io.StringIO(), Unseal()
        self.assertEqual(CG.list_cmd([], env=self.env, platform="darwin", unseal=u, opener=cf, out=out), 0)
        self.assertNotIn(TOKEN, out.getvalue())
        self.assertEqual(json.loads(out.getvalue())["tokens"][0]["id"], NEW_ID[:8])
        self.assertEqual(u.n, 1)
        for ok, rc_want in ((False, 2), (True, 0)):
            cf, out, u = CF(), io.StringIO(), Unseal(ok=ok)
            rc = CG.revoke_cmd([NEW_ID], env=self.env, platform="darwin", unseal=u, opener=cf, now=NOW, out=out, err=io.StringIO())
            self.assertEqual(rc, rc_want)
            self.assertEqual(cf.methods(), ["DELETE /user/tokens/" + NEW_ID] if ok else [])
            self.assertIn(NEW_ID[:8], u.reason)
        out = io.StringIO()
        self.assertEqual(CG.revoke_cmd(["../x"], env=self.env, unseal=Unseal(), out=out), 2)
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
                        touchid=lambda r: {"ok": True}, touchid_available=lambda: True, out=out, err=err)
        self.assertEqual((rc, json.loads(out.getvalue())["why"]), (2, "no_gui"))
        self.assertIn("!", err.getvalue())


    def _plant_minter_line(self):
        d = Path(self.env["EZ2_AIB_SECRETS_DIR"])
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        f = d / "cloudflare.env"
        f.write_text("CF_TOKEN_MINTER=%s\n" % MINTER)                     # 有人(或舊版)把母鑰放進本機檔
        os.chmod(f, 0o600)

    def test_g11_minter_in_file_refused(self):
        self._plant_minter_line()
        rc, res = self.grant(self.OK)
        self.assertEqual((rc, res["why"]), (2, "minter_in_file"))
        self.assertIn("aib secret ask CF_TOKEN_MINTER", self.err)
        self.assertEqual((self.touch.n, self.cf.calls), (0, []))
        cf, out, u = CF(), io.StringIO(), Unseal()
        self.assertEqual(CG.list_cmd([], env=self.env, platform="darwin", unseal=u, opener=cf, out=out, err=io.StringIO()), 2)
        self.assertEqual(CG.revoke_cmd([NEW_ID], env=self.env, platform="darwin", unseal=u, opener=cf, out=io.StringIO(),
                                       err=io.StringIO()), 2)
        self.assertEqual((u.n, cf.calls), (0, []))

    def test_g12_no_fallback(self):
        # 解封失敗時,就算環境變數 / 別處有一份母鑰也不准拿來用
        rc, res = self.grant(self.OK, touch=Unseal(ok=False, why="cancel"), env={"CF_TOKEN_MINTER": MINTER, "CF_API_TOKEN": MINTER})
        self.assertEqual((rc, res["why"]), (2, "touchid_cancel"))
        self.assertEqual(self.cf.calls, [])
        rc, res = self.grant(self.OK, touch=lambda r: (None, None))
        self.assertEqual(rc, 2)
        self.assertEqual(self.cf.calls, [])


class SeVault(unittest.TestCase):
    """se_vault:值走 stdin / stdout、不進 argv;輔助程式用假 runner(不叫真的 SE)。"""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-se-vault-")
        self.env = {"HOME": self.td.name, "EZ2_AIB_STATE_ROOT": str(Path(self.td.name) / "state")}

    def tearDown(self):
        self.td.cleanup()

    def test_seal_unseal_via_stdin_stdout(self):
        import se_vault as SV
        calls = []

        def run(cmd, input=None, **kw):
            calls.append((cmd, input))
            if cmd[1] == "seal":
                d = Path(cmd[cmd.index("--dir") + 1])
                d.mkdir(parents=True, exist_ok=True)
                (d / "CF_TOKEN_MINTER.sealed").write_text("{}")
                return type("R", (), {"returncode": 0, "stdout": b'{"ok": true, "fingerprint": "f00dfeed"}', "stderr": b""})()
            return type("R", (), {"returncode": 0, "stdout": MINTER.encode() + b"\n", "stderr": b""})()
        self.assertEqual(SV.seal("CF_TOKEN_MINTER", MINTER + "\n", env=self.env, run=run, helper="/x/aib-sehelper"), ("f00dfeed", None))
        self.assertEqual(SV.unseal("CF_TOKEN_MINTER", "核准", env=self.env, run=run, helper="/x/aib-sehelper"), (MINTER, None))
        for cmd, data in calls:
            self.assertNotIn(MINTER, " ".join(cmd))
        self.assertEqual(calls[0][1], MINTER.encode())
        self.assertIn("--reason", calls[1][0])
        self.assertTrue(calls[0][0][calls[0][0].index("--dir") + 1].startswith(self.env["EZ2_AIB_STATE_ROOT"]))

    def test_errors(self):
        import se_vault as SV
        self.assertEqual(SV.seal("bad name", "x", env=self.env), (None, "bad_name"))
        self.assertEqual(SV.unseal("CF_TOKEN_MINTER", "r", env=self.env), (None, "not_found"))
        d = Path(SV.se_dir(self.env))
        d.mkdir(parents=True)
        (d / "CF_TOKEN_MINTER.sealed").write_text("{}")
        cancel = lambda cmd, input=None, **kw: type("R", (), {"returncode": 3, "stdout": b"", "stderr": b"cancel\n"})()
        self.assertEqual(SV.unseal("CF_TOKEN_MINTER", "r", env=self.env, run=cancel, helper="/x"), (None, "cancel"))
        junk = lambda cmd, input=None, **kw: type("R", (), {"returncode": 1, "stdout": b"", "stderr": b"Trace: <stuff>\n"})()
        self.assertEqual(SV.unseal("CF_TOKEN_MINTER", "r", env=self.env, run=junk, helper="/x"), (None, "helper_failed"))
        self.assertEqual(SV.build_helper(self.env, platform="linux"), (None, "not_mac"))
        nos = lambda cmd, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        self.assertEqual(SV.build_helper(self.env, run=nos, platform="darwin"), (None, "no_swiftc"))

    @unittest.skipUnless(sys.platform == "darwin" and os.path.exists("/usr/bin/xcrun"), "需要 macOS + xcrun")
    def test_helper_compiles(self):
        import se_vault as SV
        import subprocess
        if subprocess.run(["xcrun", "--find", "swiftc"], capture_output=True).returncode != 0:
            self.skipTest("沒有 swiftc")
        exe, why = SV.build_helper(self.env)
        self.assertIsNone(why)
        r = subprocess.run([exe, "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("se-sign", r.stdout)
        for argv, code in ((["seal", "--name", "x", "--dir", "/tmp"], "bad_name"),
                           (["unseal", "--name", "CF_X", "--dir", "/tmp"], "no_reason"),
                           (["seal", "--name", "CF_X", "--dir", "/tmp", "--skip", "1"], "bad_arg"),
                           (["unseal", "--name", "CF_X", "--dir", str(Path(self.td.name) / "none"), "--reason", "r"], "not_found")):
            r = subprocess.run([exe] + argv, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertEqual((r.returncode != 0, r.stderr.strip()), (True, code), argv)
        self.assertEqual(SV.build_helper(self.env), (exe, None))             # 原始碼沒變 ⇒ 用快取


class Knives(unittest.TestCase):
    def _red(self, needle, repl, test):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/cf_grant.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "CfGrant." + test], cwd=str(root), max_procs=60, timeout=300)
        self.assertNotEqual(r.returncode, 0, "刀落了測試還綠:" + test)

    def test_k1_unseal_failure_ignored(self):
        self._red('    value, why = unseal(reason)\n    if not value:', '    value, why = unseal(reason)\n    value = value or "x"\n    if not value:',
                  "test_g4_touchid_gate")

    def test_k2_no_ip_lock(self):
        self._red('            "condition": {"request.ip": {"in": list(ips)}}}', "            }", "test_g3_happy")

    def test_k3_no_revoke(self):
        self._red('                api(opener, key, "DELETE", "/user/tokens/%s" % res["token_id"])\n', "", "test_g5_push_failed_revokes")

    def test_k4_no_expiry(self):
        self._red('"not_before": _iso(now), "expires_on": _iso(now + ttl * 60),', '"not_before": _iso(now),', "test_g3_happy")

    def test_k5_minter_file_allowed(self):
        self._red("        return bool(S.load_file(MINTER_FILE, env=env).get(MINTER))", "        return False", "test_g11_minter_in_file_refused")

    def test_k6_ttl_cap_raised(self):
        self._red("TTL_MIN, TTL_MAX, TTL_DEFAULT = 1, 10, 10", "TTL_MIN, TTL_MAX, TTL_DEFAULT = 1, 60, 10", "test_g1_args")


if __name__ == "__main__":
    unittest.main()
