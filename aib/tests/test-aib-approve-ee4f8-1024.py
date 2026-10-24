#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib approve(WordPress 登入第二步,規格 pending/SPEC-aib-approve-wp-2fa-ee4f8-1024.md v1)。helper 與站都換假的,
不跳真 Touch ID、不碰真 SE、不連網。

A1 key init ⇒ kid = p256:<16 hex>;pem 是 P-256 SPKI,而且要先過一次指紋(簽「發布 kid」,驗得過才給);
   沒指紋 / 取消 / helper 回的簽章不是這把鑰 ⇒ 不給 PEM
A2 pending 只收站上給得乾淨的:多 / 少欄、不是 canonical、別站、別把鑰、過期、效期 > 120(= 120 收)、iat 在未來、
   帳號不是 WordPress 嚴格字元或 > 60 字、短碼跟 nonce 算的不同、v / typ / user_id 不對、nonce / ip 不是字串、
   payload 不是合法 UTF-8 ⇒ 不列(只算 invalid),也不會讓整個指令當掉
A3 yes:視窗字短碼(由 nonce 算)與站名在最前;簽的就是站上給的原始位元組;送回 raw r||s base64url、站上用公鑰驗得過 ⇒ approved、記一筆
A4 yes 每次重新向站上拉:站上已經沒有那筆 ⇒ not_pending、helper 沒被叫(本機快取 / 紀錄不算數)
A5 取消指紋 ⇒ touchid_cancel、什麼都沒送;AIB_TOUCHID=0 / 不是 Mac / 另一個指紋視窗開著 ⇒ touchid_unavailable、
   helper 沒被叫;helper 回別把鑰的簽章 ⇒ 不送;過期的那筆 ⇒ expired;CLI 有結果一律 rc 0、找不到 rc 2
A6 30 秒內第 4 次 ⇒ rate_limited、helper 沒被叫、記進 history;時鐘倒退留下的未來時間不算
A7 站上拒(403)或 200 但 ok 不是 true ⇒ site_rejected;站上 500 / 連不上 ⇒ not_delivered(CLI 都 rc 0)
A8 no:帶機房 token 送 deny、不叫 helper
A9 site add:http 網址 / 主機跟站名不同 / 帶路徑拒;token 從 stdin、檔 600(舊 .tmp 是 644 也不沿用);
   壞 sites.json({"sites":5})列不出東西、不當掉;http_call 只走 https
A11 http_call 不跟轉址(轉址會把機房 token 帶去別的主機);不是 https 根本不連
A12 外人能寫的東西都不讓指令當掉:站上回很深的 JSON / 壞 UTF-8、本機 token / history / rate / .sekey 壞掉;
    短碼固定向量("N"*43 ⇒ 7TUV,跟 WP 端同);過了 exp 一秒就 expired(不寬限);no 遇到過期 ⇒ expired;
    nonce 以「-」開頭也收得下;yes / no 不帶 --json 也是一行 JSON
A10 aib approve 接上、四語都有、視窗字帶四個欄位
刀(複本):K1 不比 canonical ⇒ A2 紅;K2 不比站 ⇒ A2 紅;K3 不比 kid ⇒ A2 紅;K4 限速永遠過 ⇒ A6 紅;
   K5 短碼用站上給的 ⇒ A2 紅;K6 簽完不驗 ⇒ A5 紅;K7 pem 不先過指紋 ⇒ A1 紅
"""
import base64
import hashlib
import io
import json
import os
import secrets
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (REPO / "src" / "cockpit", REPO / "src", HERE):
    sys.path.insert(0, str(_p))
import aib_approve as AA  # noqa: E402
from entitlement import p256, signing  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

NOW = 1792830000


def _der_int(v):
    b = v.to_bytes(33, "big").lstrip(b"\0")
    b = b if b[0] < 0x80 else b"\0" + b
    return b"\x02" + bytes([len(b)]) + b


def p256_sign(d, msg):
    e = int.from_bytes(hashlib.sha256(msg).digest(), "big")
    while True:
        k = secrets.randbelow(p256.N - 1) + 1
        r = p256._mul2(k, p256.G, 0, p256.G)[0] % p256.N
        s = pow(k, -1, p256.N) * (e + r * d) % p256.N
        if r and s:
            body = _der_int(r) + _der_int(s)
            return b"\x30" + bytes([len(body)]) + body


def p256_pub(d):
    x, y = p256._mul2(d, p256.G, 0, p256.G)
    return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")


class FakeSE:
    def __init__(self, fail=None):
        self.d = secrets.randbelow(p256.N - 1) + 1
        self.calls, self.inputs, self.fail = [], [], fail

    def __call__(self, argv, input=None, capture_output=True, timeout=None):
        self.calls.append(argv)
        sub = argv[1]
        opts = dict(zip(argv[2::2], argv[3::2]))
        if sub == "se-create":
            Path(opts["--dir"]).mkdir(parents=True, exist_ok=True)
            doc = {"v": 1, "name": opts["--name"], "kind": "sekey", "se_key": "x",
                   "public_key": base64.b64encode(p256_pub(self.d)).decode()}
            (Path(opts["--dir"]) / (opts["--name"] + ".sekey")).write_text(json.dumps(doc))
            return SimpleNamespace(returncode=0, stdout=b"{}", stderr=b"")
        if self.fail:
            return SimpleNamespace(returncode=3, stdout=b"", stderr=(self.fail + "\n").encode())
        self.inputs.append(input)
        return SimpleNamespace(returncode=0, stdout=base64.b64encode(p256_sign(self.d, input)), stderr=b"")

    def signs(self):
        return [c for c in self.calls if c[1] == "se-sign"]


class FakeSite:
    """假的 WP 端:存 nonce ⇒ payload 位元組;approve 用公鑰驗 raw r||s。"""

    def __init__(self, pub=None, approve_status=200):
        self.items, self.posts, self.pub, self.approve_status = [], [], pub, approve_status

    def add(self, **over):
        f = {"v": 1, "typ": "wp-login", "site": "ez2.app", "user_id": 7, "user_login": "andy", "ip": "203.0.113.7",
             "nonce": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("="),
             "iat": NOW - 5, "exp": NOW + 100, "kid": over.pop("kid")}
        raw = over.pop("raw", None)
        code = over.pop("code", None)
        f.update(over)
        code = AA.code_of(f["nonce"]) if code is None and isinstance(f["nonce"], str) else code
        self.items.append({"payload": raw if raw is not None else signing.canonical(f).decode(), "code": code})
        return f

    def __call__(self, method, url, headers, body):
        self.posts.append((method, url, headers, body))
        if url.endswith("/pending"):
            if headers.get("Authorization") != "Bearer " + "t" * 32:
                return 401, b"{}"
            return 200, json.dumps({"v": 1, "items": self.items}).encode()
        if url.endswith("/approve"):
            d = json.loads(body)
            raw = base64.urlsafe_b64decode(d["sig"] + "==")
            r, s = int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
            der = b"\x30" + bytes([len(_der_int(r) + _der_int(s))]) + _der_int(r) + _der_int(s)
            item = [i for i in self.items if i["payload"].startswith("{") and json.loads(i["payload"])["nonce"] == d["nonce"]]
            ok = bool(item) and p256.verify(self.pub, item[0]["payload"].encode(), der)
            if self.approve_status != 200:
                return self.approve_status, b"{}"
            return (200, b'{"ok":true}') if ok else (403, b'{"ok":false}')
        if url.endswith("/deny"):
            return 200, b'{"ok":true}'
        return 404, b""


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-approve-")
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "EZ2_AIB_LOCALE": "zh-TW", "PATH": "/usr/bin:/bin"}
        os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
        import unittest.mock as um
        import urllib.request
        guard = um.patch.object(urllib.request, "build_opener", side_effect=AssertionError("測試不准連外"))
        guard.start()
        self.addCleanup(guard.stop)
        self.se = FakeSE()
        r, why = AA.key_init(self.env, run=self.se, helper="/fake/aib-sehelper", platform="darwin")
        self.assertIsNone(why)
        self.kid = r["kid"]
        self.site = FakeSite(pub=p256_pub(self.se.d))
        AA.site_add("ez2.app", None, "t" * 32, self.env)

    def yes(self, nonce, se=None, env=None, platform="darwin", now=NOW):
        return AA.yes(nonce, env or self.env, http=self.site, run=se or self.se, helper="/fake/aib-sehelper",
                      platform=platform, now=now)


class Approve(Base):
    def test_a1_key_and_pem(self):
        self.assertRegex(self.kid, r"^p256:[0-9a-f]{16}$")
        pem = AA.pem_of(AA.local_pub(self.env))
        der = base64.b64decode("".join(pem.strip().splitlines()[1:-1]))
        self.assertEqual(der[:26], AA.SPKI_P256)
        self.assertEqual(der[26:], p256_pub(self.se.d))
        self.assertEqual(AA.key_init(self.env, run=self.se, helper="/x", platform="darwin"), (None, "exists"))
        got, why = AA.key_publish(self.env, run=self.se, helper="/fake/aib-sehelper", platform="darwin")
        self.assertEqual((got, why), (pem, None))
        sign = self.se.signs()[-1]
        self.assertIn(self.kid, sign[sign.index("--reason") + 1])
        self.assertEqual(self.se.inputs[-1], ("ez2-aib-approve-publish|" + self.kid).encode())
        for se, want in ((FakeSE(fail="cancel"), "touchid_cancel"), (FakeSE(), "bad_sig")):
            self.assertEqual(AA.key_publish(self.env, run=se, helper="/x", platform="darwin"), (None, want))
        self.assertEqual(AA.key_publish(dict(self.env, AIB_TOUCHID="0"), run=self.se, helper="/x", platform="darwin"),
                         (None, "touchid_unavailable"))

    def test_a2_pending_only_clean_items(self):
        good = self.site.add(kid=self.kid)
        f = dict(good, nonce="N" * 43)
        nc = AA.code_of(f["nonce"])  # 短碼對得上:只靠 canonical 那關擋
        self.site.add(kid=self.kid, raw=json.dumps(f), code=nc)  # 有空白:不是 canonical
        self.site.add(kid=self.kid, raw=signing.canonical(f).decode().replace('{"exp"', '{"v":1,"exp"', 1), code=nc)  # 重複鍵
        self.site.add(kid=self.kid, raw=signing.canonical(dict(f, extra=1)).decode(), code=nc)
        self.site.add(kid=self.kid, site="evil.example")
        self.site.add(kid="p256:" + "0" * 16)
        self.site.add(kid=self.kid, exp=NOW - 60, iat=NOW - 100)
        self.site.add(kid=self.kid, exp=NOW + 200)  # 效期 205 秒
        self.site.add(kid=self.kid, user_login="andy\u202e")
        self.site.add(kid=self.kid, user_login="an\ndy")
        self.site.add(kid=self.kid, user_login="\u0430ndy")  # 西里爾 а
        self.site.add(kid=self.kid, user_login="a" * 61)
        self.site.add(kid=self.kid, code="abcd")
        self.site.add(kid=self.kid, code="AAAA" if AA.code_of(good["nonce"]) != "AAAA" else "BBBB")
        self.site.add(kid=self.kid, user_id=True)
        self.site.add(kid=self.kid, user_id=0)
        self.site.add(kid=self.kid, v=2)
        self.site.add(kid=self.kid, typ="wp-reset")
        self.site.add(kid=self.kid, nonce="N" * 43 + "\n")
        self.site.add(kid=self.kid, nonce=int("9" * 43), code="AAAA")
        self.site.add(kid=self.kid, ip=12)
        self.site.add(kid=self.kid, iat=NOW + 60, exp=NOW + 120)  # 站上時鐘快太多
        self.site.add(kid=self.kid, iat=NOW - 1, exp=NOW + 120)   # 121 秒
        edge = self.site.add(kid=self.kid, iat=NOW - 20, exp=NOW + 100)  # 剛好 120:要收
        self.site.items.append({"payload": "\ud800", "code": "ABCD"})  # 不是合法 UTF-8:不能讓整個指令當
        r, why = AA.pending(self.env, http=self.site, now=NOW)
        self.assertIsNone(why)
        self.assertEqual([i["nonce"] for i in r["items"]], [good["nonce"], edge["nonce"]])
        self.assertEqual((r["invalid"], r["errors"]), (22, {}))  # 過期那筆不算壞:yes 要記得到 expired
        self.assertEqual(r["items"][0]["code"], AA.code_of(good["nonce"]))

    def test_a3_yes_signs_site_bytes(self):
        f = self.site.add(kid=self.kid)
        res, why = self.yes(f["nonce"])
        self.assertEqual((res, why), ("approved", None))
        reason = self.se.signs()[0][self.se.signs()[0].index("--reason") + 1]
        self.assertTrue(reason.startswith("短碼 %s · 核准 ez2.app 的 andy 登入" % AA.code_of(f["nonce"])), reason)
        self.assertIn("203.0.113.7", reason)
        self.assertEqual(self.se.inputs, [self.site.items[0]["payload"].encode()])
        post = json.loads(self.site.posts[-1][3])
        self.assertEqual((post["nonce"], post["kid"]), (f["nonce"], self.kid))
        self.assertRegex(post["sig"], r"^[A-Za-z0-9_-]{86}$")
        self.assertEqual(AA.history(self.env)[0]["result"], "approved")
        self.assertEqual(stat.S_IMODE((self.root / "approve" / "history.jsonl").stat().st_mode), 0o600)

    def test_a4_yes_refetches(self):
        f = self.site.add(kid=self.kid)
        AA.pending(self.env, http=self.site, now=NOW)
        self.site.items = []  # 站上已經沒有(用掉 / 過期 / 被拒)
        self.assertEqual(self.yes(f["nonce"]), (None, "not_pending"))
        self.assertEqual(self.se.signs(), [])
        self.assertEqual(self.yes("x"), (None, "bad_nonce"))

    def test_a5_cancel_and_unavailable(self):
        f = self.site.add(kid=self.kid)
        cancel = FakeSE(fail="cancel")
        res, _ = self.yes(f["nonce"], se=cancel)
        self.assertEqual(res, "touchid_cancel")
        self.assertFalse([p for p in self.site.posts if p[1].endswith("/approve")])
        for kw in ({"env": dict(self.env, AIB_TOUCHID="0")}, {"platform": "linux"}):
            res, _ = self.yes(f["nonce"], **kw)
            self.assertEqual(res, "touchid_unavailable")
        import touchid_approve as TA
        with TA.one_at_a_time(self.root / "locks" / "touchid-approve.lock") as g:
            self.assertTrue(g.held)
            self.assertEqual(self.yes(f["nonce"]), ("touchid_unavailable", None))
        self.assertEqual(self.se.signs(), [])
        other = FakeSE()  # helper 回的不是這把鑰的簽章
        self.assertEqual(self.yes(f["nonce"], se=other), ("touchid_unavailable", None))
        self.assertFalse([p for p in self.site.posts if p[1].endswith("/approve")])
        old = self.site.add(kid=self.kid, iat=NOW - 200, exp=NOW - 90)
        self.assertEqual(self.yes(old["nonce"]), ("expired", None))
        self.assertEqual(AA.history(self.env)[0]["result"], "expired")
        out = io.StringIO()
        import unittest.mock as um
        with um.patch.object(AA, "http_call", self.site):  # 測試不連外
            self.assertEqual(AA.main(["yes", "N" * 43, "--json"], out=out, env=self.env), 2)
        self.assertEqual(json.loads(out.getvalue())["why"], "not_pending")

    def test_a6_rate_limit(self):
        f = self.site.add(kid=self.kid)
        for i in range(3):
            self.assertEqual(self.yes(f["nonce"], now=NOW + i)[0], "approved")
        self.assertEqual(self.yes(f["nonce"], now=NOW + 3)[0], "rate_limited")
        self.assertEqual(self.yes(f["nonce"], now=NOW + 29)[0], "rate_limited")  # 視窗 30 秒(NOW 那筆還在)
        self.assertEqual(len(self.se.signs()), 3)
        self.assertEqual(AA.history(self.env)[0]["result"], "rate_limited")
        self.assertEqual(self.yes(f["nonce"], now=NOW + 40)[0], "approved")
        (self.root / "approve" / "rate.json").write_text(json.dumps([NOW + 9999] * 5))  # 時鐘倒退
        self.assertEqual(self.yes(f["nonce"], now=NOW + 80)[0], "approved")

    def test_a7_site_rejects_or_unreachable(self):
        f = self.site.add(kid=self.kid)
        self.site.pub = p256_pub(1234567)  # 站上存的公鑰不是這把
        self.assertEqual(self.yes(f["nonce"])[0], "site_rejected")
        out = io.StringIO()
        for i, st in enumerate((500, 0)):
            self.site.approve_status = st
            self.assertEqual(self.yes(f["nonce"], now=NOW + 40 * (i + 1))[0], "not_delivered")
        self.assertEqual(AA.main(["history", "--json"], out=out, env=self.env), 0)
        self.assertEqual([h["result"] for h in json.loads(out.getvalue())],
                         ["not_delivered", "not_delivered", "site_rejected"])

    def test_a8_no(self):
        f = self.site.add(kid=self.kid)
        self.assertEqual(AA.no(f["nonce"], self.env, http=self.site, now=NOW), ("denied", None))
        deny = [p for p in self.site.posts if p[1].endswith("/deny")]
        self.assertEqual(deny[0][2]["Authorization"], "Bearer " + "t" * 32)
        self.assertEqual(self.se.signs(), [])
        g = self.site.add(kid=self.kid)
        site = self.site
        res = AA.no(g["nonce"], self.env, http=lambda m, u, h, b: (500, b"") if u.endswith("/deny") else site(m, u, h, b),
                    now=NOW)
        self.assertEqual(res, ("not_delivered", None))
        out = io.StringIO()
        h = self.site.add(kid=self.kid)
        import unittest.mock as um
        with um.patch.object(AA, "http_call", self.site), um.patch.object(AA.time, "time", return_value=NOW):
            self.assertEqual(AA.main(["no", h["nonce"], "--json"], out=out, env=self.env), 0)
        self.assertEqual(json.loads(out.getvalue())["result"], "denied")

    def test_a9_site_add(self):
        self.assertEqual(AA.site_add("a.com", "http://a.com", "t" * 32, self.env), (None, "bad_url"))
        self.assertEqual(AA.site_add("a.com", None, "short", self.env), (None, "bad_token"))
        for url in ("https://evil.example", "https://a.com/x", "https://u:p@a.com", "https://a.com.evil.example"):
            self.assertEqual(AA.site_add("a.com", url, "t" * 32, self.env), (None, "bad_url"), url)
        tmp = self.root / "approve" / "tokens" / "b.com.token.tmp"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text("old")
        os.chmod(str(tmp), 0o644)
        out = io.StringIO()
        rc = AA.main(["site", "add", "b.com", "--json"], out=out, env=self.env, stdin=io.StringIO("u" * 30 + "\n"))
        self.assertEqual(rc, 0)
        tok = self.root / "approve" / "tokens" / "b.com.token"
        self.assertEqual((tok.read_text().strip(), stat.S_IMODE(tok.stat().st_mode)), ("u" * 30, 0o600))
        self.assertEqual([r["site"] for r in AA.load_sites(self.env)], ["ez2.app", "b.com"])
        for bad in ('{"sites":[{"site":"x.com","url":"http://x.com"}]}', '{"sites":5}', '{"sites":true}', "[]"):
            (self.root / "approve" / "sites.json").write_text(bad)
            self.assertEqual(AA.load_sites(self.env), [], bad)
        self.assertEqual(AA.http_call("GET", "http://127.0.0.1:9/x", {}, None), (0, b""))  # 只走 https

    def test_a11_no_redirect(self):
        # 不開真的伺服器(本機 socket 會撞到稽核鉤子 guarded fd 的舊問題,#305 修):直接驗處理器與 http_call 用的是它
        import unittest.mock as um
        import urllib.request
        h = AA._no_redirect()()
        req = urllib.request.Request("https://ez2.app/wp-json/ez2-aib/v1/pending", headers={"Authorization": "Bearer x"})
        self.assertIsNone(h.redirect_request(req, None, 302, "Found", {}, "http://evil.example/next"))
        used = []

        class Stop(Exception):
            pass

        def opener(*handlers):
            used.extend(handlers)
            raise Stop()

        with um.patch.object(urllib.request, "build_opener", opener):
            with self.assertRaises(Stop):
                AA.http_call("GET", "https://ez2.app/x", {}, None)
        self.assertEqual(len(used), 1)
        self.assertTrue(issubclass(used[0], urllib.request.HTTPRedirectHandler))
        self.assertIsNone(used[0]().redirect_request(req, None, 301, "Moved", {}, "https://other.example/"))

    def test_a10_wired_and_i18n(self):
        self.assertIn('"approve"', (REPO / "src" / "cockpit" / "aib").read_text())
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            r = d["aib.approve.touchid_reason"]
            for ph in ("{code}", "{site}", "{user}", "{ip}"):
                self.assertIn(ph, r, lang)
            self.assertLess(r.index("{code}"), r.index("{user}"))
            self.assertIn("{kid}", d["aib.approve.touchid_publish"])
            self.assertIn("aib approve", d["aib.approve.help.usage"])

    def test_a12_hostile_inputs(self):
        import unittest.mock as um
        self.assertEqual(AA.code_of("N" * 43), "7TUV")
        f = self.site.add(kid=self.kid)
        deep = b"[" * 60000
        bad_site = lambda m, u, h, b: (200, deep) if u.endswith("/pending") else self.site(m, u, h, b)  # noqa: E731
        r, why = AA.pending(self.env, http=bad_site, now=NOW)
        self.assertEqual((r["items"], r["errors"]), ([], {"ez2.app": "bad_reply"}))
        self.site.items.append({"payload": "[" * 2000, "code": "AAAA"})
        r, _ = AA.pending(self.env, http=self.site, now=NOW)
        self.assertEqual(([i["nonce"] for i in r["items"]], r["invalid"]), ([f["nonce"]], 1))
        ok_false = lambda m, u, h, b: (200, b'{"ok":false}') if u.endswith("/approve") else self.site(m, u, h, b)  # noqa
        self.assertEqual(AA.yes(f["nonce"], self.env, http=ok_false, run=self.se, helper="/x", platform="darwin",
                                now=NOW), ("site_rejected", None))
        deep_reply = lambda m, u, h, b: (200, deep) if u.endswith("/approve") else self.site(m, u, h, b)  # noqa
        self.assertEqual(AA.yes(f["nonce"], self.env, http=deep_reply, run=self.se, helper="/x", platform="darwin",
                                now=NOW + 1), ("site_rejected", None))
        a = self.root / "approve"
        (a / "rate.json").write_bytes(deep)
        (a / "history.jsonl").write_bytes(b"\xff\n" + deep + b"\n")
        self.assertEqual(AA.history(self.env), [])
        self.assertEqual(self.yes(f["nonce"], now=NOW + 2)[0], "approved")
        (a / "tokens" / "ez2.app.token").write_bytes(b"\xff\xfe")
        r, _ = AA.pending(self.env, http=self.site, now=NOW)
        self.assertEqual(r["errors"], {"ez2.app": "no_token"})
        AA.site_add("ez2.app", None, "t" * 32, self.env)
        # 過了 exp 一秒:不寬限
        g = self.site.add(kid=self.kid, iat=NOW - 50, exp=NOW + 10)
        self.assertEqual(self.yes(g["nonce"], now=NOW + 11), ("expired", None))
        self.assertEqual(AA.no(g["nonce"], self.env, http=self.site, now=NOW + 11), ("expired", None))
        self.assertFalse([p for p in self.site.posts if p[1].endswith("/deny")])
        # 「-」開頭的 nonce;yes / no 不帶 --json 也是一行 JSON
        dash = "-" + "A" * 42
        self.site.add(kid=self.kid, nonce=dash)
        out = io.StringIO()
        with um.patch.object(AA, "http_call", self.site), um.patch.object(AA.time, "time", return_value=NOW):
            self.assertEqual(AA.main(["no", dash], out=out, env=self.env), 0)
        self.assertEqual(json.loads(out.getvalue()), {"nonce": dash, "result": "denied"})
        for url in ("https://ez2.app?", "https://ez2.app/?", "https://ez2.app:99999", "https://ez2.app#x"):
            self.assertEqual(AA.site_add("ez2.app", url, "t" * 32, self.env), (None, "bad_url"), url)
        (self.root / "se").mkdir(exist_ok=True)
        import se_vault
        Path(se_vault.se_dir(self.env), AA.KEY_NAME + ".sekey").write_bytes(deep)
        self.assertIsNone(AA.local_pub(self.env))

    def test_a13_http_only_https(self):
        import unittest.mock as um
        import urllib.request
        with um.patch.object(urllib.request, "build_opener", side_effect=AssertionError("不該連")):
            self.assertEqual(AA.http_call("GET", "http://ez2.app/x", {}, None), (0, b""))

class Knives(unittest.TestCase):
    def _red(self, needle, repl, test):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/aib_approve.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Approve." + test], cwd=str(root),
                            max_procs=60, timeout=300)
        self.assertNotEqual(r.returncode, 0, "刀落了測試還綠:" + test)

    def test_k1_no_canonical(self):
        self._red(" or signing.canonical(f) != raw:", ":", "test_a2_pending_only_clean_items")

    def test_k2_no_site(self):
        self._red('    if f["site"] != site:', "    if False:", "test_a2_pending_only_clean_items")

    def test_k3_no_kid(self):
        self._red('    if f["kid"] != kid:', "    if False:", "test_a2_pending_only_clean_items")

    def test_k4_no_rate(self):
        self._red("    if len(ts) >= RATE_N:", "    if False:", "test_a6_rate_limit")

    def test_k5_site_code(self):
        self._red('    if item.get("code") != code:', "    if False:", "test_a2_pending_only_clean_items")

    def test_k6_no_verify(self):
        self._red('        ok = p256.verify(pub, d["payload"], der)', "        ok = True", "test_a5_cancel_and_unavailable")

    def test_k7_pem_without_touch(self):
        self._red('    return (pem_of(pub), None) if ok else (None, "bad_sig")', "    return pem_of(pub), None",
                  "test_a1_key_and_pem")

    def test_k8_follows_redirect(self):
        self._red("            return None  # 轉址一律不跟", "            return super().redirect_request(*a, **k)",
                  "test_a11_no_redirect")


if __name__ == "__main__":
    unittest.main()
