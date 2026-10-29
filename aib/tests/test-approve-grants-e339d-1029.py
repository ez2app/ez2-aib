#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""TID-2b 時間窗授權單(10-29,草稿 DRAFT-TID2-window-grants-e339d-1029;@aeb40 13:43 裁)。

假 aib-sehelper(HMAC 模擬簽 / 驗,只測核心邏輯;真 SE 的 se-pub / se-verify 在 Mac 上手動驗收)。
G1 簽一張 seat_input 1h ⇒ 存檔、該類驗過;別類 ⇒ category
G2 高風險類簽不進單;偽造含高風險類的單(helper 直接簽)⇒ 驗不過;字雜湊照核心算對 ⇒ scope:high_risk
G3 時長超過該類 max_window(outbound 24h)⇒ over_max_window;時長不在表上 ⇒ bad_duration;
   字雜湊照核心算對的 24h outbound 單 ⇒ scope:over_max_window
G4 改單上任何欄位(到期)⇒ bad_sig
G5 過期 ⇒ expired
G6 換開機 ⇒ boot;換機器 ⇒ machine;量不到開機 ⇒ 簽不出(unmeasured)、驗不過
G7 單上的視窗字雜湊對不上(helper 直接簽一張類別跟字不一致的單)⇒ text_mismatch
G8 kid 跟現在 SE 鑰重算的不同 ⇒ kid_mismatch
G9 換鑰:登記的 kid 不同 ⇒ 視窗字第一行標「簽單鑰換過了(舊 → 新)」、記 rekey 事件;那張單照樣驗得過
G10 撤銷 ⇒ revoked;--rotate-key ⇒ 舊單全部驗不過(新鑰驗舊簽章 bad_sig)
G11 CLI:--extend 簽 / 列 / --revoke(JSON)
G12 視窗字由 approve_gate 登記的 site 組:類別 window_grant = 高風險;字不截(超長 ⇒ 拒)
G13 ask() 行為完全不變:src / tools 只有 approve_grants 自己和 aib_approve(CLI)import 它;ask() 不讀授權單
G14 sehelper:se-pub / se-verify 的公鑰從 SE 鑰本體重算(不讀 public_key 欄位)、先探 ACL(只認 notInteractive,
    簽得出來 ⇒ weak_key);grant-create = userPresence 且只准 GRANT_KEY;grantkey 簽時視窗字雜湊要等於 text_sha256
G15 到期被拉長(時長仍寫 1h)、字雜湊正確 ⇒ bad_time
G16 鑰檔換成不帶 ACL 的鑰(helper 回 weak_key)⇒ 驗不過、列不出、簽不出
刀(複本落刀,錨要恰好一次):KW1 拿掉簽章驗 ⇒ G4 紅;KW2 拿掉到期 ⇒ G5 紅;KW3 拿掉開機 ⇒ G6 紅;
  KW4 拿掉類別比對 ⇒ G1 紅;KW5 拿掉高風險排除 ⇒ G2 紅(驗單端會改成 over_max_window,斷言分開);
  KW6 拿掉驗單端的範圍重查 ⇒ G2 紅;KW11 拿掉「到期 − 簽發 = 時長」⇒ G15 紅;KW7 拿掉 max_window ⇒ G3 紅;KW8 拿掉字雜湊比對 ⇒ G7 紅;
  KW9 拿掉 kid 比對 ⇒ G8 紅;KW10 拿掉換鑰標記 ⇒ G9 紅;KW12 拿掉撤銷 ⇒ G10 紅
"""
import base64
import hashlib
import hmac
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "cockpit"
sys.path.insert(0, str(SRC))
os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
import approve_gate as AG  # noqa: E402
import approve_grants as GR  # noqa: E402

FAKE = r'''#!%s
import base64, hashlib, hmac, json, os, sys
a = sys.argv[1:]
sub = a[0]
o = dict(zip(a[1::2], a[2::2]))
p = os.path.join(o["--dir"], o["--name"] + ".sekey")
log = os.path.join(o["--dir"], "fake-log.jsonl")
def key():
    if not os.path.isfile(p):
        sys.stderr.write("not_found\n"); sys.exit(2)
    with open(p) as f:
        d = json.load(f)
    if d.get("weak") and sub in ("se-pub", "se-verify"):     # 真 helper:不帶 ACL 的 SE 鑰探得出來 ⇒ weak_key
        sys.stderr.write("weak_key\n"); sys.exit(6)
    return d["k"].encode()
if sub == "grant-create":
    os.makedirs(o["--dir"], exist_ok=True)
    if os.path.isfile(p):
        sys.stderr.write("exists\n"); sys.exit(2)
    with open(p, "w") as f:
        json.dump({"k": os.urandom(16).hex()}, f)
    print(json.dumps({"ok": True}))
elif sub == "se-pub":
    print(json.dumps({"ok": True, "public_key": base64.b64encode(hashlib.sha256(key()).digest()).decode()}))
elif sub == "se-sign":
    data = sys.stdin.buffer.read()
    k = key()
    if hashlib.sha256(o["--reason"].encode()).hexdigest() != json.loads(data).get("text_sha256"):   # 真 helper 同規則
        sys.stderr.write("reason_mismatch\n"); sys.exit(2)
    with open(log, "a") as f:
        f.write(json.dumps({"reason": o["--reason"]}, ensure_ascii=False) + "\n")
    print(base64.b64encode(hmac.new(k, data, "sha256").digest()).decode())
elif sub == "se-verify":
    data = sys.stdin.buffer.read()
    good = base64.b64encode(hmac.new(key(), data, "sha256").digest()).decode()
    if not hmac.compare_digest(good, o["--sig"]):
        sys.stderr.write("bad_sig\n"); sys.exit(5)
    print(json.dumps({"ok": True}))
else:
    sys.stderr.write("usage\n"); sys.exit(2)
''' % sys.executable

T0 = 1_800_000_000


class Env:
    def __init__(self):
        self.td = tempfile.mkdtemp(prefix="aib-grants-")
        self.helper = os.path.join(self.td, "fake-sehelper")
        with open(self.helper, "w") as f:
            f.write(FAKE)
        os.chmod(self.helper, 0o700)
        self.env = dict(os.environ, EZ2_AIB_STATE_ROOT=os.path.join(self.td, "state"), EZ2_AIB_LOCALE="zh-TW")
        self.t = [T0]
        self.boot = [1_799_990_000]
        self.machine = ["m" * 16]

    def ctx(self, M=GR):
        return M.Ctx(env=self.env, helper=self.helper, platform="darwin", now=lambda: self.t[0],
                     boot=lambda: self.boot[0], machine=lambda: self.machine[0])

    def sign_raw(self, M, doc, reason):
        """繞過核心檢查、直接叫 helper 簽(模擬 AI 自己拼一張單去騙指紋;reason = 視窗上給本人看的字)。"""
        c = self.ctx(M)
        out, why = c.helper_call(["se-sign", "--name", M.KEY_NAME, "--dir", M._key_dir(self.env), "--reason", reason],
                                 M.canonical(doc))
        assert why is None, why
        return {"doc": doc, "sig": out.decode().strip()}

    def reasons(self, M=GR):
        p = os.path.join(M._key_dir(self.env), "fake-log.jsonl")
        if not os.path.isfile(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(x)["reason"] for x in f]

    def close(self):
        shutil.rmtree(self.td, ignore_errors=True)


def _grant(M, e, doc):
    with open(os.path.join(M._dir(e.env), doc["nonce"] + ".json"), encoding="utf-8") as f:
        return json.load(f)


def _txt(cats, dur, rekey=None):
    return str(GR.window_reason(sorted(cats), dur, rekey, env={"EZ2_AIB_LOCALE": "zh-TW"}))


def _rehash(doc, **kw):
    """照核心重算字雜湊的偽造單(#419 R1 P1:攻擊者照 window_reason 算對雜湊)。回 (單, 視窗字)。"""
    d = dict(doc, **kw)
    t = _txt(d["cats"], d["dur"])
    return dict(d, text_sha256=hashlib.sha256(t.encode()).hexdigest()), t


# ---- 各格(收 M = 模組,刀測拿複本跑同一格) ----

def cell_g1(t, M):
    e = Env()
    try:
        doc, why = M.sign(["seat_input"], "1h", e.ctx(M))
        t.assertIsNone(why)
        g = _grant(M, e, doc)
        t.assertEqual(M.verify(g, "seat_input", e.ctx(M)), (True, None))
        t.assertEqual(M.verify(g, "outbound", e.ctx(M)), (False, "category"))
    finally:
        e.close()


def cell_g2(t, M):
    e = Env()
    try:
        t.assertEqual(M.sign(["release_sign"], "15m", e.ctx(M)), (None, "high_risk"))
        t.assertEqual(M.sign(["seat_input", "secret"], "15m", e.ctx(M)), (None, "high_risk"))
        ok, _ = M.sign(["seat_input"], "15m", e.ctx(M))
        good = _grant(M, e, ok)["doc"]
        forged = e.sign_raw(M, dict(good, cats=["release_sign", "seat_input"]), _txt(good["cats"], good["dur"]))
        t.assertFalse(M.verify(forged, "release_sign", e.ctx(M))[0])
        d, txt = _rehash(good, cats=["secret", "seat_input"])          # 字雜湊正確 ⇒ 只剩驗單端的範圍檢查
        t.assertEqual(M.verify(e.sign_raw(M, d, txt), "seat_input", e.ctx(M)), (False, "scope:high_risk"))
    finally:
        e.close()


def cell_g3(t, M):
    e = Env()
    try:
        t.assertEqual(M.sign(["outbound"], "24h", e.ctx(M)), (None, "over_max_window"))
        t.assertEqual(M.sign(["seat_input", "command"], "24h", e.ctx(M)), (None, "over_max_window"))
        t.assertEqual(M.sign(["seat_input"], "2h", e.ctx(M)), (None, "bad_duration"))
        doc, _ = M.sign(["seat_input"], "24h", e.ctx(M))
        t.assertIsNotNone(doc)
        good = _grant(M, e, doc)["doc"]
        forged = e.sign_raw(M, dict(good, cats=["outbound"]), _txt(good["cats"], good["dur"]))
        t.assertFalse(M.verify(forged, "outbound", e.ctx(M))[0])
        d, txt = _rehash(good, cats=["outbound"])                       # 24h 的 outbound,字雜湊正確
        t.assertEqual(M.verify(e.sign_raw(M, d, txt), "outbound", e.ctx(M)), (False, "scope:over_max_window"))
    finally:
        e.close()


def cell_g4(t, M):
    e = Env()
    try:
        doc, _ = M.sign(["outbound"], "15m", e.ctx(M))
        g = _grant(M, e, doc)
        g["doc"]["expires_utc"] = M._utc(T0 + 3 * 3600)
        g["doc"]["dur"] = "3h"
        t.assertEqual(M.verify(g, "outbound", e.ctx(M)), (False, "bad_sig"))
    finally:
        e.close()


def cell_g5(t, M):
    e = Env()
    try:
        doc, _ = M.sign(["command"], "30m", e.ctx(M))
        g = _grant(M, e, doc)
        e.t[0] = T0 + 1799
        t.assertEqual(M.verify(g, "command", e.ctx(M)), (True, None))
        e.t[0] = T0 + 1800
        t.assertEqual(M.verify(g, "command", e.ctx(M)), (False, "expired"))
    finally:
        e.close()


def cell_g6(t, M):
    e = Env()
    try:
        doc, _ = M.sign(["seat_input"], "3h", e.ctx(M))
        g = _grant(M, e, doc)
        e.boot[0] += 7200
        t.assertEqual(M.verify(g, "seat_input", e.ctx(M)), (False, "boot"))
        e.boot[0] -= 7200
        e.machine[0] = "n" * 16
        t.assertEqual(M.verify(g, "seat_input", e.ctx(M)), (False, "machine"))
        e.machine[0] = "m" * 16
        e.boot[0] = None
        t.assertEqual(M.verify(g, "seat_input", e.ctx(M)), (False, "boot"))
        t.assertEqual(M.sign(["seat_input"], "1h", e.ctx(M)), (None, "unmeasured"))
    finally:
        e.close()


def cell_g7(t, M):
    e = Env()
    try:
        doc, _ = M.sign(["seat_input"], "1h", e.ctx(M))
        good = _grant(M, e, doc)["doc"]
        forged = e.sign_raw(M, dict(good, cats=["outbound", "seat_input"]), _txt(good["cats"], good["dur"]))  # 本人看到的字只有開席
        t.assertEqual(M.verify(forged, "outbound", e.ctx(M)), (False, "text_mismatch"))
    finally:
        e.close()


def cell_g8(t, M):
    e = Env()
    try:
        doc, _ = M.sign(["seat_input"], "1h", e.ctx(M))
        good = _grant(M, e, doc)["doc"]
        forged = e.sign_raw(M, dict(good, kid="0" * 16), _txt(good["cats"], good["dur"]))
        t.assertEqual(M.verify(forged, "seat_input", e.ctx(M)), (False, "kid_mismatch"))
    finally:
        e.close()


def cell_g9(t, M):
    e = Env()
    try:
        d1, _ = M.sign(["seat_input"], "1h", e.ctx(M))
        t.assertFalse(e.reasons(M)[-1].startswith("⚠"))
        M._write_json(M._kid_path(e.env), {"kid": "f" * 16})
        d2, why = M.sign(["seat_input"], "1h", e.ctx(M))
        t.assertIsNone(why)
        t.assertTrue(e.reasons(M)[-1].startswith("⚠ 簽單鑰換過了(" + "f" * 16 + " → " + d2["kid"] + ")"),
                     e.reasons(M)[-1])
        with open(os.path.join(M._dir(e.env), "events.jsonl"), encoding="utf-8") as f:
            ev = [json.loads(x) for x in f]
        t.assertTrue(any(x.get("event") == "rekey" and x.get("old") == "f" * 16 for x in ev))
        t.assertEqual(M.verify(_grant(M, e, d2), "seat_input", e.ctx(M)), (True, None))
        d3, _ = M.sign(["seat_input"], "1h", e.ctx(M))
        t.assertFalse(e.reasons(M)[-1].startswith("⚠"))
    finally:
        e.close()


def cell_g10(t, M):
    e = Env()
    try:
        a, _ = M.sign(["seat_input"], "1h", e.ctx(M))
        b, _ = M.sign(["outbound"], "1h", e.ctx(M))
        t.assertEqual(M.revoke("seat_input", e.ctx(M)), 1)
        t.assertEqual(M.verify(_grant(M, e, a), "seat_input", e.ctx(M)), (False, "revoked"))
        t.assertEqual(M.verify(_grant(M, e, b), "outbound", e.ctx(M)), (True, None))
        kid, why = M.rotate_key(e.ctx(M))
        t.assertIsNone(why)
        t.assertIn(M.verify(_grant(M, e, b), "outbound", e.ctx(M)), [(False, "bad_sig"), (False, "kid_mismatch")])
        t.assertEqual(M.active(e.ctx(M)), [])
    finally:
        e.close()


def cell_g15(t, M):
    e = Env()
    try:
        doc, _ = M.sign(["command"], "1h", e.ctx(M))
        good = _grant(M, e, doc)["doc"]
        d, txt = _rehash(good, expires_utc=M._utc(T0 + 3 * 3600))      # 到期被拉長、時長仍寫 1h
        t.assertEqual(M.verify(e.sign_raw(M, d, txt), "command", e.ctx(M)), (False, "bad_time"))
    finally:
        e.close()


def cell_g16(t, M):
    e = Env()
    try:
        doc, _ = M.sign(["seat_input"], "1h", e.ctx(M))
        g = _grant(M, e, doc)
        kp = os.path.join(M._key_dir(e.env), M.KEY_NAME + ".sekey")
        with open(kp) as f:
            k = json.load(f)
        with open(kp, "w") as f:
            json.dump(dict(k, weak=True), f)                           # 換成不帶 ACL 的鑰(同一把秘密,模擬自簽)
        t.assertEqual(M.verify(g, "seat_input", e.ctx(M)), (False, "weak_key"))
        t.assertEqual(M.active(e.ctx(M)), [])
        t.assertEqual(M.sign(["seat_input"], "1h", e.ctx(M)), (None, "weak_key"))
    finally:
        e.close()


class Cells(unittest.TestCase):
    def test_g1(self): cell_g1(self, GR)
    def test_g2(self): cell_g2(self, GR)
    def test_g3(self): cell_g3(self, GR)
    def test_g4(self): cell_g4(self, GR)
    def test_g5(self): cell_g5(self, GR)
    def test_g6(self): cell_g6(self, GR)
    def test_g7(self): cell_g7(self, GR)
    def test_g8(self): cell_g8(self, GR)
    def test_g9(self): cell_g9(self, GR)
    def test_g10(self): cell_g10(self, GR)
    def test_g15(self): cell_g15(self, GR)
    def test_g16(self): cell_g16(self, GR)

    def test_g11_cli(self):
        e = Env()
        try:
            out = io.StringIO()
            self.assertEqual(GR.main(["--extend", "seat_input,command", "--for", "1h"], out=out, ctx=e.ctx()), 0)
            self.assertTrue(json.loads(out.getvalue())["ok"])
            out = io.StringIO()
            self.assertEqual(GR.main(["--extend", "secret", "--for", "1h"], out=out, ctx=e.ctx()), 2)
            self.assertEqual(json.loads(out.getvalue())["why"], "high_risk")
            out = io.StringIO()
            GR.main(["--json"], out=out, ctx=e.ctx())
            rows = json.loads(out.getvalue())
            self.assertEqual([r["cats"] for r in rows], [["command", "seat_input"]])
            self.assertEqual(rows[0]["left_s"], 3600)
            out = io.StringIO()
            GR.main(["--revoke", "all"], out=out, ctx=e.ctx())
            self.assertEqual(json.loads(out.getvalue())["revoked"], 1)
            out = io.StringIO()
            GR.main(["--json"], out=out, ctx=e.ctx())
            self.assertEqual(json.loads(out.getvalue()), [])
        finally:
            e.close()

    def test_g12_reason_site(self):
        r = GR.window_reason(["seat_input", "outbound"], "3h", env={"EZ2_AIB_LOCALE": "zh-TW"})
        self.assertIsNone(AG.refusal(r))
        self.assertEqual(r.category, "window_grant")
        self.assertTrue(r.high_risk)
        self.assertIn("3 小時", str(r))
        self.assertIn("本機所有 AI", str(r))
        self.assertTrue(AG.CATEGORIES["window_grant"]["high_risk"])
        long_ = GR.window_reason(["seat_input"], "1h", rekey=("a" * 300, "b" * 16))
        self.assertIsNone(AG.se_reason(long_))           # 整句超過 se-sign 上限 ⇒ 拒、不截

    def test_g13_ask_unchanged(self):
        users = []
        for base in (ROOT / "src", ROOT / "tools"):
            for p in base.rglob("*.py"):
                if re.search(r"^\s*(import approve_grants|from approve_grants )|import_module\(.approve_grants",
                             p.read_text(encoding="utf-8", errors="replace"), re.M):
                    users.append(p.relative_to(ROOT).as_posix())
        self.assertEqual(sorted(users), ["src/cockpit/aib_approve.py"])
        gate = (SRC / "approve_gate.py").read_text(encoding="utf-8")
        self.assertNotIn("import approve_grants", gate)
        self.assertNotIn("grants/", gate)
        called = []
        saved = {n: getattr(GR, n) for n in ("verify", "active", "load_all", "revoked")}
        try:
            for n in saved:
                setattr(GR, n, lambda *a, _n=n, **k: called.append(_n))
            seen = []
            r = AG.coerce("核准:測試")
            lock = tempfile.mkdtemp(prefix="aib-grants-lock-")
            try:
                res = AG.ask(r, lock, touchid=lambda x: seen.append(str(x)) or {"ok": False, "why": "cancel"})
            finally:
                shutil.rmtree(lock, ignore_errors=True)
        finally:
            for n, f in saved.items():
                setattr(GR, n, f)
        self.assertEqual(called, [])
        self.assertEqual(seen, ["核准:測試"])
        self.assertEqual(res["why"], "cancel")

    def test_g14_helper_source(self):
        sw = (SRC / "sehelper" / "aib-sehelper.swift").read_text(encoding="utf-8")
        body = sw[sw.index("func sePublicKey"):sw.index("func sePub(")]
        self.assertIn('dataRepresentation: try b64(doc, "se_key")', body)
        self.assertNotIn("public_key", body)
        vbody = sw[sw.index("func seVerify"):sw.index("func seSign")]
        self.assertIn("sePublicKey(", vbody)
        self.assertNotIn('doc["public_key"]', vbody)
        self.assertRegex(sw, r'presence \? \[\.privateKeyUsage, \.userPresence\]')
        self.assertIn('try seCreate(name, dir, kind: "grantkey")', sw)
        self.assertIn('guard name == grantKeyName else { throw Fail("bad_name") }', sw)
        # M1 探針:sePublicKey 先 requireOwnerACL;只認 notInteractive;簽得出來 ⇒ weak_key
        self.assertIn("try requireOwnerACL(doc)", body)
        probe = sw[sw.index("func requireOwnerACL"):sw.index("func sePublicKey")]
        self.assertIn("ctx.interactionNotAllowed = true", probe)
        self.assertIn("ns.code == LAError.notInteractive.rawValue { return }", probe)
        self.assertIn('throw Fail("weak_key", 6)', probe)
        # L2:GRANT_KEY ⇔ grantkey,其他名字只收 sekey
        load = sw[sw.index("func loadKeyDoc"):sw.index("func requireOwnerACL")]
        self.assertIn('let kind = name == grantKeyName ? "grantkey" : "sekey"', load)
        # L1:grantkey 簽時視窗字雜湊要等於單上 text_sha256
        sign = sw[sw.index("func seSign"):]
        self.assertIn('guard got == want else { throw Fail("reason_mismatch") }', sign)


# ---- 刀 ----
KNIVES = [
    ("KW1", 'if why:\n        return False, "bad_sig" if why == "bad_sig" else why\n', 'if False:\n        pass\n', cell_g4),
    ("KW2", '    if not issued <= now < expires:\n', '    if False:\n', cell_g5),
    ("KW3", '    if not boot or doc.get("boot") != int(boot):\n', '    if not boot:\n', cell_g6),
    ("KW4", '    if cat not in cats:\n        return False, "category"\n', '', cell_g1),
    ("KW5", '        if spec["high_risk"]:\n            return "high_risk"\n', '', cell_g2),
    ("KW6", '    if check_scope(cats, doc.get("dur")):\n        return False, "scope:" + check_scope(cats, doc.get("dur"))\n', '', cell_g2),
    ("KW7", '        if DURATIONS[dur] > spec["max_window"]:\n            return "over_max_window"\n', '', cell_g3),
    ("KW8", ' != doc.get("text_sha256"):\n        return False, "text_mismatch"\n', ' != doc.get("text_sha256") and False:\n        pass\n', cell_g7),
    ("KW9", '    if doc.get("kid") != kid:\n        return False, "kid_mismatch"\n', '', cell_g8),
    ("KW10", '    rekey = (old, kid) if old != kid and old is not None else None\n    r = ', '    rekey = None\n    r = ', cell_g9),
    ("KW11", '    if issued is None or expires is None or expires - issued != DURATIONS[doc["dur"]]:\n',
     '    if issued is None or expires is None:\n', cell_g15),
    ("KW12", '    if doc["nonce"] in (revoked(ctx.env) if revoked_set is None else revoked_set):\n', '    if False:\n', cell_g10),
]


class Knives(unittest.TestCase):
    def test_knives(self):
        src = (SRC / "approve_grants.py").read_text(encoding="utf-8")
        for name, anchor, repl, cell in KNIVES:
            with self.subTest(knife=name):
                self.assertEqual(src.count(anchor), 1, "%s 錨不是恰好一次" % name)
                td = tempfile.mkdtemp(prefix="aib-grants-knife-")
                try:
                    p = os.path.join(td, "approve_grants.py")
                    with open(p, "w", encoding="utf-8") as f:
                        f.write(src.replace(anchor, repl))
                    spec = importlib.util.spec_from_file_location("approve_grants_knife_" + name, p)
                    M = importlib.util.module_from_spec(spec)
                    sys.modules[spec.name] = M
                    spec.loader.exec_module(M)
                    M.window_reason = GR.window_reason       # 視窗字要從登記的那支組(複本不是登記檔)
                    with self.assertRaises(AssertionError, msg="%s 落刀後格子仍綠" % name):
                        cell(self, M)
                finally:
                    sys.modules.pop("approve_grants_knife_" + name, None)
                    shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
