#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib quote(PROPOSAL-gaps v3 第 7 項;@abb89 10-21 19:12 裁 Q1–Q3)。helper 全換假的,不跳真 Touch ID、不碰真 SE。

Q1 add ⇒ evt:q-… 檔 600;沒有伺服器證書 ⇒ verify = unverified-root(rc 3),**不是 ok**
Q2 上限算清理後碼位:200 個中文字過、而且送進 helper 的視窗字含原話全文;201 拒 too_long、helper 沒被叫;零寬字不算字
Q3 一律拒:AIB_TOUCHID=0 / 不是 Mac / 取消 / 沒鑰 / 另一個指紋視窗在用 ⇒ 不存檔
Q4 伺服器證書:對 ⇒ ok;改字 ⇒ bad-evt;改簽章 ⇒ bad-sig;沒在內建清單的 kid ⇒ unverified-root;
   原話早於登錄 ⇒ before-register;失效後簽的 ⇒ revoked,失效前簽的照舊 ok
Q5 同帳號 AI 換掉 .sekey、用自己的鑰簽 ⇒ 永遠不是 ok(證書只認伺服器登錄過的指紋)
Q6 出貨的 TRUSTED_QUOTE_KEYS 是空的(fail closed;改要維護者點頭)
Q7 P-256 驗章:RFC 6979 A.2.5 向量對;非最短 DER、r=0、不在曲線上的公鑰 ⇒ False
Q8 aib quote 接上、四語都有;helper 的 se-sign 超長回 reason_too_long、不截
Q9 伺服器格式(@9f808 19:32):sig base64url 不帶 padding、kid = sha256(原始公鑰);格式不對 / kid 對不上 ⇒ 不算
刀(複本):K1 驗章一律 True ⇒ Q7 紅;K2 證書不驗伺服器簽章 ⇒ Q4 紅;K3 拿掉上限 ⇒ Q2 紅;K4 本機鑰驗過就當 ok ⇒ Q1 紅;
   K5 不比 kid 跟公鑰 ⇒ Q9 紅
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
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (REPO / "src" / "cockpit", REPO / "src", HERE):
    sys.path.insert(0, str(_p))
import aib_quote as AQ  # noqa: E402
from entitlement import ed25519, p256  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

SERVER_SEED = bytes(range(32))
SERVER_PUB = ed25519.public_key(SERVER_SEED)
SERVER_KID = "ed25519:" + hashlib.sha256(SERVER_PUB).hexdigest()      # @9f808:kid = sha256(原始公鑰)
TRUSTED = {SERVER_KID: "ed25519:" + SERVER_PUB.hex()}


def b64url(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


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
    """假的 aib-sehelper:se-create 寫 .sekey(帶公鑰),se-sign 用測試私鑰簽;記下每次的參數。"""

    def __init__(self, fail=None):
        self.d = secrets.randbelow(p256.N - 1) + 1
        self.calls = []
        self.fail = fail

    def __call__(self, argv, input=None, capture_output=True, timeout=None):
        self.calls.append(argv)
        sub = argv[1]
        opts = dict(zip(argv[2::2], argv[3::2]))
        if self.fail:
            return SimpleNamespace(returncode=3, stdout=b"", stderr=(self.fail + "\n").encode())
        if sub == "se-create":
            Path(opts["--dir"]).mkdir(parents=True, exist_ok=True)
            doc = {"v": 1, "name": opts["--name"], "kind": "sekey", "se_key": "x",
                   "public_key": base64.b64encode(p256_pub(self.d)).decode()}
            (Path(opts["--dir"]) / (opts["--name"] + ".sekey")).write_text(json.dumps(doc))
            return SimpleNamespace(returncode=0, stdout=b"{}", stderr=b"")
        if sub == "se-sign":
            return SimpleNamespace(returncode=0, stdout=base64.b64encode(p256_sign(self.d, input)), stderr=b"")
        return SimpleNamespace(returncode=2, stdout=b"", stderr=b"usage\n")


def cert_for(pub, registered_at, revoked_at=None, seed=SERVER_SEED, kid=SERVER_KID):
    c = {"v": 1, "kind": "aib-quote-key", "account_id": "acct-1", "key_fp": AQ.key_fp(pub),
         "pub_x963_b64": base64.b64encode(pub).decode(), "machine_label": "test-mac",
         "registered_at": registered_at, "revoked_at": revoked_at, "kid": kid}
    c["sig"] = b64url(ed25519.sign(seed, AQ.canonical(c)))
    return c


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-quote-")
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "EZ2_AIB_LOCALE": "zh-TW", "PATH": "/usr/bin:/bin"}
        self.se = FakeSE()

    def init_key(self):
        r, why = AQ.key_init(self.env, run=self.se, helper="/fake/aib-sehelper", platform="darwin")
        self.assertIsNone(why)
        return r

    def add(self, text, se=None, now=None, env=None, platform="darwin"):
        return AQ.add(text, "abcde", env or self.env, run=se or self.se, helper="/fake/aib-sehelper",
                      platform=platform, now=now)


class Quote(Base):
    def test_q1_add_then_unverified_root(self):
        self.init_key()
        r, why = self.add("這版先不上正式站")
        self.assertIsNone(why)
        self.assertRegex(r["evt"], r"^evt:q-[0-9a-f]{16}$")
        f = self.root / "quotes" / (r["evt"].split(":", 1)[1] + ".json")
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        v = AQ.verify(r["evt"], self.env)
        self.assertEqual(v["status"], "unverified-root", "沒有伺服器證書不准說已驗證")
        buf = io.StringIO()
        self.assertEqual(AQ.main(["verify", r["evt"], "--json"], out=buf, env=self.env), 3)

    def test_q2_limit_codepoints_full_reason(self):
        self.init_key()
        text = "字" * 200
        r, why = self.add(text)
        self.assertIsNone(why)
        reason = self.se.calls[-1][self.se.calls[-1].index("--reason") + 1]
        self.assertIn(text, reason, "視窗字要含原話全文")
        self.assertLessEqual(len(reason), 320, "要塞得進 helper 的 maxSignReason")
        n = len(self.se.calls)
        self.assertEqual(self.add("字" * 201), (None, "too_long"))
        self.assertEqual(len(self.se.calls), n, "超長不叫 helper")
        r, why = self.add("​".join("字" * 200))
        self.assertIsNone(why, "零寬字清掉後不算字")

    def test_q3_fail_closed(self):
        self.assertEqual(self.add("x"), (None, "no_key"))
        self.init_key()
        n = len(self.se.calls)
        self.assertEqual(self.add("x", env=dict(self.env, AIB_TOUCHID="0")), (None, "touchid_off"))
        self.assertEqual(self.add("x", platform="linux"), (None, "touchid_unavailable"))
        self.assertEqual(len(self.se.calls), n)
        self.assertEqual(self.add("x", se=FakeSE(fail="cancel")), (None, "touchid_cancel"))
        self.assertEqual(self.add("x", se=FakeSE(fail="no_biometry")), (None, "touchid_unavailable"))
        import touchid_approve as TA
        with TA.one_at_a_time(self.root / "locks" / "touchid-approve.lock"):
            self.assertEqual(self.add("x"), (None, "busy"))
        self.assertFalse(list((self.root / "quotes").glob("q-*.json")) if (self.root / "quotes").exists() else [])

    def _signed(self, now):
        r, why = self.add("上線前要先問我", now=now)
        self.assertIsNone(why)
        return r["evt"]

    def test_q4_server_certs(self):
        self.init_key()
        pub = AQ.local_pub(self.env)
        evt = self._signed("2026-10-21T12:00:00+00:00")
        good = [cert_for(pub, "2026-10-21T11:00:00+00:00")]
        self.assertEqual(AQ.verify(evt, self.env, certs=good, trusted=TRUSTED)["status"], "ok")
        other = hashlib.sha256(ed25519.public_key(b"\x07" * 32)).hexdigest()
        bad_kid = [cert_for(pub, "2026-10-21T11:00:00+00:00", seed=b"\x07" * 32, kid="ed25519:" + other)]
        self.assertEqual(AQ.verify(evt, self.env, certs=bad_kid, trusted=TRUSTED)["status"], "unverified-root")
        forged = dict(good[0], registered_at="2026-10-01T00:00:00+00:00")
        self.assertEqual(AQ.verify(evt, self.env, certs=[forged], trusted=TRUSTED)["status"], "unverified-root")
        late = [cert_for(pub, "2026-10-21T13:00:00+00:00")]
        self.assertEqual(AQ.verify(evt, self.env, certs=late, trusted=TRUSTED)["status"], "before-register")
        revoke = cert_for(pub, "2026-10-21T11:00:00+00:00", revoked_at="2026-10-21T12:30:00+00:00")
        self.assertEqual(AQ.verify(evt, self.env, certs=good + [revoke], trusted=TRUSTED)["status"], "ok", "失效前簽的照舊驗")
        evt2 = self._signed("2026-10-21T13:00:00+00:00")
        self.assertEqual(AQ.verify(evt2, self.env, certs=good + [revoke], trusted=TRUSTED)["status"], "revoked")
        f = self.root / "quotes" / (evt.split(":", 1)[1] + ".json")
        rec = json.loads(f.read_text())
        f.write_text(json.dumps(dict(rec, payload=dict(rec["payload"], text="上線前不用問我"))))
        self.assertEqual(AQ.verify(evt, self.env, certs=good, trusted=TRUSTED)["status"], "bad-evt")
        sig = bytearray(base64.b64decode(rec["sig_der_b64"]))
        sig[-1] ^= 1
        f.write_text(json.dumps(dict(rec, sig_der_b64=base64.b64encode(bytes(sig)).decode())))
        self.assertEqual(AQ.verify(evt, self.env, certs=good, trusted=TRUSTED)["status"], "bad-sig")
        self.assertEqual(AQ.verify("evt:q-0000000000000000", self.env)["status"], "not-found")

    def test_q9_server_wire_format(self):
        """@9f808 伺服器格式:sig = base64url 不帶 padding;kid = sha256(原始公鑰);kid 跟清單公鑰對不上 / 標準 base64 / 帶 = ⇒ 不算。"""
        self.init_key()
        pub = AQ.local_pub(self.env)
        evt = self._signed("2026-10-21T12:00:00+00:00")
        c = cert_for(pub, "2026-10-21T11:00:00+00:00")
        self.assertNotIn("=", c["sig"])
        self.assertTrue(AQ.cert_ok(c, TRUSTED))
        self.assertEqual(AQ.kid_of(SERVER_PUB), SERVER_KID)
        raw = base64.urlsafe_b64decode(c["sig"] + "==")
        self.assertFalse(AQ.cert_ok(dict(c, sig=base64.urlsafe_b64encode(raw).decode()), TRUSTED), "帶 padding 不收")
        std = base64.b64encode(raw).decode().rstrip("=")
        if std != c["sig"]:
            self.assertFalse(AQ.cert_ok(dict(c, sig=std), TRUSTED), "標準 base64 字元不收")
        alias = "ed25519:" + "ab" * 32
        renamed = cert_for(pub, "2026-10-21T11:00:00+00:00", kid=alias)          # 真伺服器簽的,但 kid 不是 sha256(公鑰)
        self.assertFalse(AQ.cert_ok(renamed, {alias: "ed25519:" + SERVER_PUB.hex()}), "kid 跟清單公鑰的 sha256 對不上")
        self.assertEqual(AQ.verify(evt, self.env, certs=[c], trusted=TRUSTED)["status"], "ok")

    def test_q5_ai_swaps_key(self):
        self.init_key()
        real = AQ.local_pub(self.env)
        certs = [cert_for(real, "2026-10-21T11:00:00+00:00")]
        (self.root / "secrets" / "se" / "AIB_QUOTE.sekey").unlink()
        ai = FakeSE()
        AQ.key_init(self.env, run=ai, helper="/fake/aib-sehelper", platform="darwin")
        r, why = self.add("AI 自己說:可以直接上線", se=ai, now="2026-10-21T12:00:00+00:00")
        self.assertIsNone(why)
        v = AQ.verify(r["evt"], self.env, certs=certs, trusted=TRUSTED)
        self.assertNotEqual(v["status"], "ok")
        self.assertEqual(v["status"], "unverified-root")

    def test_q6_shipped_keys_empty(self):
        self.assertEqual(AQ.TRUSTED_QUOTE_KEYS, {})
        self.init_key()
        pub = AQ.local_pub(self.env)
        evt = self._signed("2026-10-21T12:00:00+00:00")
        self.assertEqual(AQ.verify(evt, self.env, certs=[cert_for(pub, "2026-10-21T11:00:00+00:00")])["status"],
                         "unverified-root", "內建清單空 ⇒ 證書不算數")

    def test_q7_p256_vectors(self):
        x = 0x60FED4BA255A9D31C961EB74C6356D68C049B8923B61FA6CE669622E60F29FB6
        y = 0x7903FE1008B8BC99A41AE9E95628BC64F2F1B20C2D7E9F5177A3C294D4462299
        r = 0xEFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716
        s = 0xF7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8
        pub = b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")
        body = _der_int(r) + _der_int(s)
        der = b"\x30" + bytes([len(body)]) + body
        self.assertTrue(p256.verify(pub, b"sample", der))
        self.assertFalse(p256.verify(pub, b"sample!", der))
        padded = b"\x02" + bytes([body[1] + 1]) + b"\0" + body[2:]
        self.assertFalse(p256.verify(pub, b"sample", b"\x30" + bytes([len(padded)]) + padded), "非最短 DER")
        zero = _der_int(1)[:2] + b"\0" + _der_int(s)
        self.assertFalse(p256.verify(pub, b"sample", b"\x30" + bytes([len(zero)]) + zero))
        self.assertFalse(p256.verify(pub[:-1] + bytes([pub[-1] ^ 1]), b"sample", der), "不在曲線上")
        d = secrets.randbelow(p256.N - 1) + 1
        self.assertTrue(p256.verify(p256_pub(d), b"m", p256_sign(d, b"m")))

    def test_q8_wired(self):
        src = (REPO / "src" / "cockpit" / "aib").read_text(encoding="utf-8")
        self.assertIn('exec "$T_PY3" "$HERE/aib_quote.py" "$@"', src)
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            for k in ("aib.quote.touchid_reason", "aib.quote.help.usage", "aib.quote.hint.too_long", "aib.quote.hint.no_key"):
                self.assertIn(k, d, (lang, k))
            self.assertIn("{text}", d["aib.quote.touchid_reason"])
        sw = (REPO / "src" / "cockpit" / "sehelper" / "aib-sehelper.swift").read_text(encoding="utf-8")
        self.assertIn('guard cleanFull(reason).count <= maxSignReason else { throw Fail("reason_too_long") }', sw)
        self.assertIn("context(reason, limit: maxSignReason)", sw)
        self.assertEqual(AQ.main(["--bogus"], out=io.StringIO(), env=self.env if hasattr(self, "env") else {}), 2)


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, repl, case):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_verify_always_true(self):
        self._knife("src/entitlement/p256.py", '    """pub:65 位元組 X9.63;msg:原文(這裡做 SHA-256);sig:DER。對 ⇒ True。"""\n',
                    '    """pub:65 位元組 X9.63;msg:原文(這裡做 SHA-256);sig:DER。對 ⇒ True。"""\n    return True\n',
                    "Quote.test_q7_p256_vectors")

    def test_k2_no_server_sig_check(self):
        self._knife("src/cockpit/aib_quote.py", "        return ed25519.verify(server_pub, canonical(body), sig)\n",
                    "        return True\n", "Quote.test_q4_server_certs")

    def test_k3_no_limit(self):
        self._knife("src/cockpit/aib_quote.py", "    if len(t) > QUOTE_MAX:\n        return None, \"too_long\"\n", "",
                    "Quote.test_q2_limit_codepoints_full_reason")

    def test_k5_no_kid_pub_match(self):
        self._knife("src/cockpit/aib_quote.py", "    if len(server_pub) != 32 or kid_of(server_pub) != kid:\n",
                    "    if len(server_pub) != 32:\n", "Quote.test_q9_server_wire_format")

    def test_k4_local_key_counts(self):
        self._knife("src/cockpit/aib_quote.py", 'res["status"] = "unverified-root" if local_ok else "bad-sig"',
                    'res["status"] = "ok" if local_ok else "bad-sig"', "Quote.test_q1_add_then_unverified_root")


if __name__ == "__main__":
    unittest.main()
