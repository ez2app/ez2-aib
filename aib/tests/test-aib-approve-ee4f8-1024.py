#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib approve(WordPress 登入第二步,規格 pending/SPEC-aib-approve-wp-2fa-ee4f8-1024.md v1)。helper 與站都換假的,
不跳真 Touch ID、不碰真 SE、不連網。

A1 key init ⇒ kid = p256:<16 hex>;pem 是 P-256 SPKI(站上 openssl 讀得懂的那種)
A2 pending 只收站上給得乾淨的:多 / 少欄、不是 canonical(多空白、鍵亂序、重複鍵)、別站、別把鑰、過期、
   效期 > 120 秒、帳號含控制 / 隱形字、短碼格式不對 ⇒ 不列(只算 invalid)
A3 yes:視窗字短碼與站名在最前;簽的就是站上給的原始位元組;送回 raw r||s base64url、站上用公鑰驗得過 ⇒ approved、記一筆
A4 yes 每次重新向站上拉:站上已經沒有那筆 ⇒ not_pending、helper 沒被叫(本機快取 / 紀錄不算數)
A5 取消指紋 ⇒ touchid_cancel、什麼都沒送;AIB_TOUCHID=0 / 不是 Mac ⇒ touchid_unavailable、helper 沒被叫
A6 30 秒內第 4 次 ⇒ rate_limited、helper 沒被叫
A7 站上拒(403)⇒ site_rejected rc 3;站上 500 / 連不上 ⇒ not_delivered
A8 no:帶機房 token 送 deny、不叫 helper
A9 site add:http 網址拒;token 從 stdin、檔 600;壞 sites.json 列不出東西
A10 aib approve 接上、四語都有、視窗字帶四個欄位
刀(複本):K1 不比 canonical ⇒ A2 紅;K2 不比站 ⇒ A2 紅;K3 不比 kid ⇒ A2 紅;K4 限速永遠過 ⇒ A6 紅
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
        code = over.pop("code", "ABCD")
        f.update(over)
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
            item = [i for i in self.items if json.loads(i["payload"])["nonce"] == d["nonce"]]
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

    def test_a2_pending_only_clean_items(self):
        good = self.site.add(kid=self.kid)
        f = dict(good, nonce="N" * 43)
        self.site.add(kid=self.kid, raw=json.dumps(f))  # 有空白:不是 canonical
        self.site.add(kid=self.kid, raw=signing.canonical(f).decode().replace('{"exp"', '{"v":1,"exp"', 1))  # 重複鍵
        self.site.add(kid=self.kid, raw=signing.canonical(dict(f, extra=1)).decode())
        self.site.add(kid=self.kid, site="evil.example")
        self.site.add(kid="p256:" + "0" * 16)
        self.site.add(kid=self.kid, exp=NOW - 60, iat=NOW - 100)
        self.site.add(kid=self.kid, exp=NOW + 200)  # 效期 205 秒
        self.site.add(kid=self.kid, user_login="andy‮")
        self.site.add(kid=self.kid, user_login="an\ndy")
        self.site.add(kid=self.kid, code="abcd")
        self.site.add(kid=self.kid, user_id=True)
        r, why = AA.pending(self.env, http=self.site, now=NOW)
        self.assertIsNone(why)
        self.assertEqual([i["nonce"] for i in r["items"]], [good["nonce"]])
        self.assertEqual((r["invalid"], r["errors"]), (11, {}))
        self.assertEqual(r["items"][0]["code"], "ABCD")

    def test_a3_yes_signs_site_bytes(self):
        f = self.site.add(kid=self.kid)
        res, why = self.yes(f["nonce"])
        self.assertEqual((res, why), ("approved", None))
        reason = self.se.signs()[0][self.se.signs()[0].index("--reason") + 1]
        self.assertTrue(reason.startswith("短碼 ABCD · 核准 ez2.app 的 andy 登入"), reason)
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
        self.assertEqual(self.se.signs(), [])

    def test_a6_rate_limit(self):
        f = self.site.add(kid=self.kid)
        for i in range(3):
            self.assertEqual(self.yes(f["nonce"], now=NOW + i)[0], "approved")
        self.assertEqual(self.yes(f["nonce"], now=NOW + 3)[0], "rate_limited")
        self.assertEqual(len(self.se.signs()), 3)
        self.assertEqual(self.yes(f["nonce"], now=NOW + 40)[0], "approved")

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

    def test_a9_site_add(self):
        self.assertEqual(AA.site_add("a.com", "http://a.com", "t" * 32, self.env), (None, "bad_url"))
        self.assertEqual(AA.site_add("a.com", None, "short", self.env), (None, "bad_token"))
        out = io.StringIO()
        rc = AA.main(["site", "add", "b.com", "--json"], out=out, env=self.env, stdin=io.StringIO("u" * 30 + "\n"))
        self.assertEqual(rc, 0)
        tok = self.root / "approve" / "tokens" / "b.com.token"
        self.assertEqual((tok.read_text().strip(), stat.S_IMODE(tok.stat().st_mode)), ("u" * 30, 0o600))
        self.assertEqual([r["site"] for r in AA.load_sites(self.env)], ["ez2.app", "b.com"])
        (self.root / "approve" / "sites.json").write_text('{"sites":[{"site":"x.com","url":"http://x.com"}]}')
        self.assertEqual(AA.load_sites(self.env), [])

    def test_a10_wired_and_i18n(self):
        self.assertIn('"approve"', (REPO / "src" / "cockpit" / "aib").read_text())
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            for ph in ("{code}", "{site}", "{user}", "{ip}"):
                self.assertIn(ph, d["aib.approve.touchid_reason"], lang)
            self.assertIn("aib approve", d["aib.approve.help.usage"])
            self.assertTrue(d["aib.approve.touchid_reason"].index("{code}") < d["aib.approve.touchid_reason"].index("{user}"))


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


if __name__ == "__main__":
    unittest.main()
