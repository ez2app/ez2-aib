#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""TID-2b 時間窗授權單(10-29,草稿 DRAFT-TID2-window-grants-e339d-1029;@aeb40 13:43 裁)。

假 aib-sehelper(HMAC 模擬簽 / 驗,只測核心邏輯;真 SE 的 se-pub / se-verify 在 Mac 上手動驗收)。
G1 簽一張 seat_input 1h ⇒ 存檔、該類驗過;別類 ⇒ category
G2 高風險類簽不進單;偽造含高風險類的單(helper 直接簽)⇒ 驗不過;字雜湊照核心算對 ⇒ scope:high_risk
G3 時長超過該類 max_window(outbound 24h)⇒ over_max_window;時長不在表上 ⇒ bad_duration;超過日誌涵蓋上限(seat_input 24h)
   ⇒ over_log_cover;字雜湊照核心算對、繞過 helper 直接簽的 24h 單 ⇒ scope:over_max_window / scope:over_log_cover
G4 改單上任何欄位 / 改 helper 簽的 stamp(到期)⇒ bad_sig
G5 過期 ⇒ expired
G6 換開機 ⇒ boot;換機器 ⇒ machine;量不到開機 ⇒ 簽不出(unmeasured)、驗不過
G7 單上的視窗字雜湊對不上(helper 直接簽一張類別跟字不一致的單)⇒ text_mismatch
G8 kid 跟現在 SE 鑰重算的不同 ⇒ kid_mismatch
G9 換鑰:登記的 kid 不同 ⇒ 視窗字第一行標「簽單鑰換過了(舊 → 新)」、記 rekey 事件;那張單照樣驗得過
G10 撤銷 ⇒ revoked;--rotate-key ⇒ 舊單全部驗不過(新鑰驗舊簽章 bad_sig)
G11 CLI:--extend 簽 / 列 / --revoke(JSON)
G12 視窗字由 approve_gate 登記的 site 組:類別 window_grant = 高風險;字不截(超長 ⇒ 拒)
G13 注入 touchid、沒給 window ⇒ ask() 不讀授權單(TID-2c 後只有正式路徑才開窗);import 它的只有 CLI / approve_gate / doctor
G14 sehelper:se-pub / se-verify 的公鑰從 SE 鑰本體重算(不讀 public_key 欄位)、先探 ACL(只認 notInteractive,
    簽得出來 ⇒ weak_key);grant-create = userPresence 且只准 GRANT_KEY;grantkey 簽時視窗字雜湊要等於 text_sha256
G17 #441 R2 H1:簽發時間 / 期限 / 開機識別由 helper 產生、簽進 stamp:單上帶 issued_utc / expires_utc / boot ⇒ helper 不簽
    (bad_time);時長不在表上 / 超過 7 小時 ⇒ 不簽;stamp = helper 時鐘 + 開機(呼叫端改不了);存檔的單沒有這三欄
    (假 helper 跟 Swift 同規則)
G15 到期被拉長(時長仍寫 1h)、stamp 簽章正確(繞過 helper)⇒ bad_time;單上自帶時間欄位 ⇒ corrupt
G16 鑰檔換成不帶 ACL 的鑰(helper 回 weak_key)⇒ 驗不過、列不出、簽不出
刀(複本落刀,錨要恰好一次):KW1 拿掉簽章驗 ⇒ G4 紅;KW2 拿掉到期 ⇒ G5 紅;KW3 拿掉開機 ⇒ G6 紅;
  KW4 拿掉類別比對 ⇒ G1 紅;KW5 拿掉高風險排除 ⇒ G2 紅(驗單端會改成 over_max_window,斷言分開);
  KW6 拿掉驗單端的範圍重查 ⇒ G2 紅;KW11 拿掉「到期 − 簽發 = 時長」⇒ G15 紅;KW7 拿掉 max_window ⇒ G3 紅;KW8 拿掉字雜湊比對 ⇒ G7 紅;
  KW9 拿掉 kid 比對 ⇒ G8 紅;KW10 拿掉換鑰標記 ⇒ G9 紅;KW12 拿掉撤銷 ⇒ G10 紅;
  KW13 拿掉日誌涵蓋上限 ⇒ G3 紅
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
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "cockpit"
sys.path.insert(0, str(SRC))
os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
import approve_gate as AG  # noqa: E402
import approve_grants as GR  # noqa: E402

FAKE = r'''#!%s
import base64, hashlib, hmac, json, os, sys, time
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
    k = key()
    if not os.path.isfile(os.path.join(o["--dir"], "fake-old-helper")):   # 旗標檔(helper 只拿乾淨 env,#432 R1 H1)
        print(json.dumps({"ok": True, "owner_acl": "probed", "public_key": base64.b64encode(hashlib.sha256(k).digest()).decode()}))
    else:
        print(json.dumps({"ok": True, "public_key": base64.b64encode(hashlib.sha256(k).digest()).decode()}))
elif sub == "se-sign":
    data = sys.stdin.buffer.read()
    k = key()
    if hashlib.sha256(o["--reason"].encode()).hexdigest() != json.loads(data).get("text_sha256"):   # 真 helper 同規則
        sys.stderr.write("reason_mismatch\n"); sys.exit(2)
    with open(log, "a") as f:
        f.write(json.dumps({"reason": o["--reason"]}, ensure_ascii=False) + "\n")
    if o["--name"] != "GRANT_KEY":
        print(base64.b64encode(hmac.new(k, data, "sha256").digest()).decode()); sys.exit(0)
    # 真 helper 同規則(#441 R2 H1):簽發時間 / 期限 / 開機由 helper 產生(時鐘 = --dir/fake-clock、開機 = fake-boot)
    d = json.loads(data)
    secs = {"15m": 900, "30m": 1800, "1h": 3600, "3h": 10800, "24h": 86400}.get(d.get("dur"))
    if any(x in d for x in ("issued_utc", "expires_utc", "boot")) or not secs or secs > 7 * 3600:
        sys.stderr.write("bad_time\n"); sys.exit(2)
    def fake(n, dflt):                   # 沒寫檔(正式路徑的假系統)⇒ 系統時鐘 / 假系統的開機;寫了但量不到 ⇒ 不簽
        q = os.path.join(o["--dir"], n)
        if not os.path.isfile(q):
            return dflt
        with open(q) as f:
            return int(f.read())
    try:
        now, boot = fake("fake-clock", int(time.time())), fake("fake-boot", 1799990000)
    except ValueError:
        sys.stderr.write("bad_time\n"); sys.exit(2)
    st = "\naib-grant-stamp v1 issued=%%d expires=%%d boot=%%d" %% (now, now + secs, boot)
    sig = base64.b64encode(hmac.new(k, data + st.encode(), "sha256").digest()).decode()
    print(json.dumps({"sig": sig, "issued": now, "expires": now + secs, "boot": boot}))
elif sub == "se-verify":
    data = sys.stdin.buffer.read()
    good = base64.b64encode(hmac.new(key(), data, "sha256").digest()).decode()
    if not hmac.compare_digest(good, o["--sig"]):
        sys.stderr.write("bad_sig\n"); sys.exit(5)
    print(json.dumps({"ok": True, "owner_acl": "probed"}))
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
        self.clock(M)
        return M.Ctx(env=self.env, helper=self.helper, platform="darwin", now=lambda: self.t[0],
                     boot=lambda: self.boot[0], machine=lambda: self.machine[0], lock_screen=self._lock)

    def clock(self, M=GR):
        """假 helper 的時鐘 / 開機 = 測試的(寫在 --dir 裡;真 helper 用系統時鐘 + kern.boottime)。"""
        d = M._key_dir(self.env)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "fake-clock"), "w") as f:
            f.write(str(int(self.t[0])))
        with open(os.path.join(d, "fake-boot"), "w") as f:
            f.write(str(self.boot[0] or ""))

    def _lock(self):
        """假鎖屏(不碰真螢幕):記一筆,子類別可接到假系統日誌。"""
        self.lock_calls = getattr(self, "lock_calls", []) + [int(self.t[0])]
        return "locked"

    def sign_bypass(self, M, doc, stamp=None):
        """不經 helper、直接用假鑰算 HMAC(模擬「helper 的時間規則不存在」時驗單端還擋不擋得住)。stamp 預設照時長。"""
        now = int(self.t[0])
        stamp = stamp or {"issued": now, "expires": now + M.DURATIONS[doc["dur"]], "boot": self.boot[0]}
        with open(os.path.join(M._key_dir(self.env), M.KEY_NAME + ".sekey")) as f:
            k = json.load(f)["k"].encode()
        sig = base64.b64encode(hmac.new(k, M._signed_bytes(doc, stamp), "sha256").digest()).decode()
        return {"doc": doc, "sig": sig, "stamp": stamp}

    def sign_raw(self, M, doc, reason):
        """繞過核心檢查、直接叫 helper 簽(模擬 AI 自己拼一張單去騙指紋;reason = 視窗上給本人看的字)。"""
        c = self.ctx(M)
        out, why = c.helper_call(["se-sign", "--name", M.KEY_NAME, "--dir", M._key_dir(self.env), "--reason", reason],
                                 M.canonical(doc))
        assert why is None, why
        got = json.loads(out.decode())
        return {"doc": doc, "sig": got.pop("sig"), "stamp": got}

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
        t.assertEqual(M.sign(["seat_input"], "24h", e.ctx(M)), (None, "over_log_cover"))   # #441:上限 = 日誌涵蓋範圍
        doc, _ = M.sign(["seat_input"], "3h", e.ctx(M))
        t.assertIsNotNone(doc)
        good = _grant(M, e, doc)["doc"]
        forged = e.sign_raw(M, dict(good, cats=["outbound"]), _txt(good["cats"], good["dur"]))
        t.assertFalse(M.verify(forged, "outbound", e.ctx(M))[0])
        # 24h 的單:helper 不簽(bad_time)⇒ 驗單端再擋一次
        long_ = dict(good, dur="24h")
        d, txt = _rehash(dict(long_, cats=["outbound"]))
        t.assertEqual(M.verify(e.sign_bypass(M, d), "outbound", e.ctx(M)), (False, "scope:over_max_window"))
        d, txt = _rehash(long_)
        t.assertEqual(M.verify(e.sign_bypass(M, d), "seat_input", e.ctx(M)), (False, "scope:over_log_cover"))
    finally:
        e.close()


def cell_g17(t, M):
    """#441 R2 H1:時間 / 期限 / 開機由 helper 產生並簽進 stamp。呼叫端在單上自帶這三欄(想指定未來起算)⇒ helper 不簽;
    時長不在表上 / 超過 7 小時 ⇒ 不簽;簽出來的 stamp = helper 的時鐘與開機,存檔的單本身沒有這三欄。"""
    e = Env()
    try:
        doc, _ = M.sign(["seat_input"], "1h", e.ctx(M))
        g = _grant(M, e, doc)
        good = g["doc"]
        t.assertFalse(any(k in good for k in ("issued_utc", "expires_utc", "boot")))
        t.assertEqual(g["stamp"], {"issued": T0, "expires": T0 + 3600, "boot": e.boot[0]})
        t.assertEqual((doc["issued_utc"], doc["boot"]), (M._utc(T0), e.boot[0]))     # 回給呼叫端的是 helper 簽的
        txt = _txt(good["cats"], good["dur"])
        now = int(e.t[0])
        for extra in ({"issued_utc": M._utc(now + 4)}, {"issued_utc": M._utc(now + 1), "expires_utc": M._utc(now + 3601)},
                      {"expires_utc": M._utc(now + 3600)}, {"boot": e.boot[0]}, {"dur": "24h"}, {"dur": "2h"}):
            d = dict(good, nonce="f" * 32, **extra)
            out, why = e.ctx(M).helper_call(["se-sign", "--name", M.KEY_NAME, "--dir", M._key_dir(e.env), "--reason", txt],
                                            M.canonical(d))
            t.assertEqual(why, "bad_time", extra)
        t.assertEqual(M.WINDOW_CAP_S, 7 * 3600)                       # = helper 的 grantMaxWindow(G14 對 Swift 原始碼)
    finally:
        e.close()


def cell_g4(t, M):
    e = Env()
    try:
        doc, _ = M.sign(["outbound"], "15m", e.ctx(M))
        g = _grant(M, e, doc)
        g["stamp"]["expires"] = T0 + 3 * 3600                     # 改 helper 簽的 stamp
        t.assertEqual(M.verify(g, "outbound", e.ctx(M)), (False, "bad_sig"))
        g["doc"]["dur"] = "3h"
        t.assertEqual(M.verify(g, "outbound", e.ctx(M)), (False, "bad_sig"))
        g = _grant(M, e, doc)
        g["stamp"]["issued"] += 4                                  # 起算往後挪(躲鎖屏)⇒ 簽章不符
        g["stamp"]["expires"] += 4
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
        st = {"issued": T0, "expires": T0 + 3 * 3600, "boot": e.boot[0]}     # 到期被拉長、時長仍寫 1h(繞過 helper 簽)
        t.assertEqual(M.verify(e.sign_bypass(M, good, st), "command", e.ctx(M)), (False, "bad_time"))
        d, _ = _rehash(good, issued_utc=M._utc(T0 + 4))                    # 單上自帶時間欄位 ⇒ 不認
        t.assertEqual(M.verify(e.sign_bypass(M, d), "command", e.ctx(M)), (False, "corrupt"))
        t.assertEqual(M.verify(dict(_grant(M, e, doc), stamp=None), "command", e.ctx(M)), (False, "corrupt"))
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
    def test_g17(self): cell_g17(self, GR)

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
            with mock.patch.object(AG, "_default_window", lambda: None):   # 不看這台有沒有裝固定路徑 helper
                GR.main(["--json"], out=out, ctx=e.ctx())
            rows = json.loads(out.getvalue())
            self.assertEqual([r["cats"] for r in rows], [["command", "seat_input"]])
            self.assertEqual(rows[0]["left_s"], 3600)
            self.assertEqual(rows[0]["auto_approve"], "off")              # #428 R2:正式路徑時間窗關著
            out = io.StringIO()
            with mock.patch.object(AG, "_default_window", lambda: None):
                GR.main([], out=out, ctx=e.ctx())
            self.assertIn("auto_approve:off", out.getvalue())
            out = io.StringIO()
            GR.main(["--revoke", "all"], out=out, ctx=e.ctx())
            got = json.loads(out.getvalue())
            self.assertEqual((got["revoked"], got["lock"]), (1, "locked"))   # 撤銷 = 鎖屏(假鎖屏)
            self.assertEqual(len(e.lock_calls), 1)
            self.assertTrue(got["hint"])
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
        self.assertEqual(sorted(users), ["src/bootstrap/aib_doctor.py", "src/cockpit/aib_approve.py",
                                         "src/cockpit/approve_gate.py"])
        gate = (SRC / "approve_gate.py").read_text(encoding="utf-8")
        self.assertEqual(gate.count("import approve_grants"), 1)          # 系統錨定驗單第 3 張:只在 _default_window 裡載
        body = gate[gate.index("def _default_window("):gate.index("def ask(")]
        self.assertIn("    import approve_grants\n", body)
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
        self.assertIn('\\"owner_acl\\": \\"probed\\"', sw[sw.index("func sePub("):sw.index("func seVerify")])
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
        probe = sw[sw.index("func probeOnce"):sw.index("func sePublicKey")]
        self.assertIn("try probeOnce(doc, password: pw)", sw[sw.index("func requireOwnerACL"):sw.index("func probeOnce")])
        self.assertIn("ctx.setCredential(pw, type: .applicationPassword)", probe)
        self.assertIn("ctx.interactionNotAllowed = true", probe)
        self.assertIn("ns.code == LAError.notInteractive.rawValue { return }", probe)
        self.assertIn('throw Fail("weak_key", 6)', probe)
        # L2:GRANT_KEY ⇔ grantkey,其他名字只收 sekey
        load = sw[sw.index("func loadKeyDoc"):sw.index("func requireOwnerACL")]
        self.assertIn('let kind = name == grantKeyName ? "grantkey" : "sekey"', load)
        # L1:grantkey 簽時視窗字雜湊要等於單上 text_sha256
        sign = sw[sw.index("func seSign"):]
        self.assertIn('guard got == want else { throw Fail("reason_mismatch") }', sign)
        # #441 R2 H1:grantkey 的簽發時間 / 期限 / 開機由 helper 產生、簽進 stamp;呼叫端帶了就不簽;上限 / 時長表跟 Python 同
        self.assertIn('guard obj["issued_utc"] == nil, obj["expires_utc"] == nil, obj["boot"] == nil else { throw Fail("bad_time") }', sign)
        self.assertIn("secs <= grantMaxWindow", sign)
        self.assertIn("let issued = Int64(Date().timeIntervalSince1970)", sign)
        self.assertIn("guard let boot = bootEpoch() else", sign)
        self.assertIn('sysctlbyname("kern.boottime"', sw)
        self.assertIn('let stamp = "\\naib-grant-stamp v1 issued=\\(issued) expires=\\(issued + Int64(secs)) boot=\\(boot)"', sign)
        self.assertEqual(GR.STAMP_FMT, "\naib-grant-stamp v1 issued=%d expires=%d boot=%d")
        self.assertIn("k.signature(for: data + Data(stamp.utf8))", sign)
        self.assertNotIn("grantIssuedAhead", sw)
        self.assertIn("let grantMaxWindow = 7 * 3600 ", sw)
        m = re.search(r'let grantDurations = \[(.*?)\]', sw)
        self.assertEqual(dict((k, eval(v)) for k, v in re.findall(r'"(\w+)": ([\d *]+)', m.group(1))), GR.DURATIONS)
        self.assertEqual(GR.WINDOW_CAP_S, 7 * 3600)


# ---- 刀 ----
KNIVES = [
    ("KW1", 'if why:\n        return False, "bad_sig" if why == "bad_sig" else why\n', 'if False:\n        pass\n', cell_g4),
    ("KW2", '    if not issued <= now < expires:\n', '    if False:\n', cell_g5),
    ("KW3", '    if not boot or stamp["boot"] != int(boot):\n', '    if not boot:\n', cell_g6),
    ("KW4", '    if cat not in cats:\n        return False, "category"\n', '', cell_g1),
    ("KW5", '        if spec["high_risk"]:\n            return "high_risk"\n', '', cell_g2),
    ("KW6", '    if check_scope(cats, doc.get("dur")):\n        return False, "scope:" + check_scope(cats, doc.get("dur"))\n', '', cell_g2),
    ("KW7", '        if DURATIONS[dur] > spec["max_window"]:\n            return "over_max_window"\n', '', cell_g3),
    ("KW13", '    if DURATIONS[dur] > WINDOW_CAP_S:\n        return "over_log_cover"\n', '', cell_g3),
    ("KW8", ' != doc.get("text_sha256"):\n        return False, "text_mismatch"\n', ' != doc.get("text_sha256") and False:\n        pass\n', cell_g7),
    ("KW9", '    if doc.get("kid") != kid:\n        return False, "kid_mismatch"\n', '', cell_g8),
    ("KW10", '    rekey = (old, kid) if old != kid and old is not None else None\n    r = ', '    rekey = None\n    r = ', cell_g9),
    ("KW11", '    if expires - issued != DURATIONS[doc["dur"]]:\n        return False, "bad_time"\n', '', cell_g15),
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
