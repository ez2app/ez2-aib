#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib quote:本人原話存成可驗的 evt(PROPOSAL-gaps v3 第 7 項;@abb89 10-21 19:12 裁,設計 pending/DESIGN-aib-quote-d07f9-1021.md)。

  aib quote key init            這台建一把 Secure Enclave 簽章鑰(不跳指紋;私鑰不離開 SE)
  aib quote key status          這台的鑰指紋、有沒有伺服器證書
  aib quote add "<原話>"        跳 Touch ID,視窗字 = 原話全文;按了才用 SE 簽,存成 evt:q-…
  aib quote verify evt:q-…      只用公鑰驗,不要指紋

防線(同帳號 AI 威脅守則):
  - 簽:SE 私鑰的 accessControl = biometryCurrentSet,每次簽系統都要指紋;沒有略過旗標、不退回密碼 / PIN。
    視窗字由核心組、原話過 TA 同款清理後算碼位,超過 QUOTE_MAX 一律拒(helper 那邊也不截,超長回 reason_too_long)。
  - 驗:信任根 = ez2.page 伺服器用專用鑰簽的「帳號 + 鑰指紋 + 登錄時間」證書(TRUSTED_QUOTE_KEYS 內建公鑰)。
    本機 .sekey 裡的公鑰只拿來「先看簽章對不對」,不算根:AI 刪得掉 .sekey、能自建沒有指紋 ACL 的鑰。
    沒有伺服器簽過的證書 ⇒ unverified-root,不准顯示成已驗證。
  - 證書快取檔是本機檔,但內容是伺服器簽的,每次照驗;快取只會讓失效資訊舊,所以輸出帶 certs_at。
rc:0 = 成功 / ok;2 = 拒 / 參數錯;3 = 驗了但不是 ok。
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

QUOTE_MAX = 200              # 原話上限(清理後碼位數;@abb89 Q3)
KEY_NAME = "AIB_QUOTE"       # aib-sehelper 的鑰名(^[A-Z][A-Z0-9_]{1,63}$)
EVT_RE = re.compile(r"evt:q-[0-9a-f]{16}")      # 一律 fullmatch

# kid ⇒ 公鑰。kid = "ed25519:" + sha256(伺服器 32 位元組原始公鑰) 的 64 hex(@9f808 10-21 19:32);
#   值 = "ed25519:<原始公鑰 64 hex>"。ez2.page 簽 quote 鑰證書的專用鑰(不跟解鎖單 / 授權 / 外掛簽章共用);私鑰只在伺服器。
#   空 = 還沒有正式鑰 ⇒ 所有證書不算數 ⇒ verify 一律 unverified-root(fail closed)。改這份清單要 Andy 核。
TRUSTED_QUOTE_KEYS = {}
SIG_RE = re.compile(r"[A-Za-z0-9_-]{86}")      # 64 位元組 Ed25519 簽章,base64url 不帶 padding;一律 fullmatch($ 會放過結尾換行)


def kid_of(pub32):
    return "ed25519:" + hashlib.sha256(bytes(pub32)).hexdigest()


def _env(env):
    return dict(os.environ if env is None else env)


def state_root(env=None):
    import seats_dir_lib
    e = _env(env)
    return Path(e.get("EZ2_AIB_STATE_ROOT") or seats_dir_lib.state_root(e)[0])


def quotes_dir(env=None):
    return state_root(env) / "quotes"


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _ts(iso):
    try:
        d = datetime.datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    return d.timestamp() if d.tzinfo else None


def canonical(obj):
    from entitlement import signing
    return signing.canonical(obj)


def clean_text(text):
    """跟 touchid_approve.clean 同款(去控制字元、零寬 / 方向字元、壓成一行),但**不截**:截了本人就沒看全。"""
    import touchid_approve as TA
    return TA._CTRL.sub(" ", TA._INVISIBLE.sub("", str(text or ""))).strip()


def key_fp(pub):
    return "sha256:" + hashlib.sha256(bytes(pub)).hexdigest()


def evt_of(payload):
    return "evt:q-" + hashlib.sha256(canonical(payload)).hexdigest()[:16]


def touchid_reason(text, env=None):
    import approve_gate as AG        # 統一入口:類別 quote(高風險);原話不截,太長直接拒
    return AG.reason("aib_quote.add", {"text": text})


# ---- 這台的 SE 鑰 ----

def _sekey_path(env):
    import se_vault
    return Path(se_vault.se_dir(_env(env))) / (KEY_NAME + ".sekey")


def local_pub(env=None):
    """本機 .sekey 裡的公鑰(65 位元組);沒有 / 壞 ⇒ None。**不是信任根**,只拿來先驗簽章、算指紋。"""
    try:
        doc = json.loads(_sekey_path(env).read_text(encoding="utf-8"))
        pub = base64.b64decode(doc.get("public_key") or "", validate=True)
    except (OSError, ValueError, TypeError):
        return None
    return pub if len(pub) == 65 else None


def key_init(env=None, run=subprocess.run, helper=None, platform=None):
    """回 (結果, why)。已有鑰 ⇒ exists(不覆蓋;換鑰走 key rotate,要伺服器那兩道)。"""
    import se_vault
    env = _env(env)
    if local_pub(env):
        return None, "exists"
    out, why = se_vault._call(["se-create", "--name", KEY_NAME, "--dir", se_vault.se_dir(env)], env, b"", run, helper, platform)
    if why:
        return None, why
    pub = local_pub(env)
    if not pub:
        return None, "helper_failed"
    return {"key_fp": key_fp(pub)}, None


# ---- 伺服器證書(信任根)----

def _certs_path(env):
    return quotes_dir(env) / "certs.json"


def load_certs(env=None):
    """快取的伺服器證書 ⇒ (證書 list, 取得時間)。檔是本機的,所以每張都要 cert_ok 驗過才算。"""
    try:
        doc = json.loads(_certs_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], None
    certs = doc.get("certs") if isinstance(doc, dict) else None
    return (certs if isinstance(certs, list) else []), doc.get("fetched_at")


def cert_ok(cert, trusted=None):
    """伺服器證書驗簽:kind / v / kid 在內建清單 / ed25519(canonical 去 sig)/ 指紋 = sha256(公鑰)。"""
    from entitlement import ed25519
    trusted = TRUSTED_QUOTE_KEYS if trusted is None else trusted
    if not isinstance(cert, dict) or cert.get("v") != 1 or cert.get("kind") != "aib-quote-key":
        return False
    kid = cert.get("kid")
    if kid not in trusted:
        return False
    try:
        server_pub = bytes.fromhex(str(trusted[kid]).split(":", 1)[1])
    except (ValueError, IndexError):
        return False
    if len(server_pub) != 32 or kid_of(server_pub) != kid:
        return False
    if not SIG_RE.fullmatch(str(cert.get("sig") or "")):
        return False
    try:
        pub = base64.b64decode(cert.get("pub_x963_b64") or "", validate=True)
        sig = base64.urlsafe_b64decode(cert["sig"] + "==")
    except (ValueError, TypeError):
        return False
    if len(pub) != 65 or cert.get("key_fp") != key_fp(pub) or _ts(cert.get("registered_at")) is None:
        return False
    if cert.get("revoked_at") is not None and _ts(cert.get("revoked_at")) is None:
        return False
    body = {k: v for k, v in cert.items() if k != "sig"}
    try:
        return ed25519.verify(server_pub, canonical(body), sig)
    except (ValueError, TypeError):
        return False


# ---- add / verify ----

def _evt_path(evt, env):
    return quotes_dir(env) / (evt.split(":", 1)[1] + ".json")


def add(text, seat="", env=None, run=subprocess.run, helper=None, platform=None, now=None):
    """回 (結果, why)。why None = 簽好存好。"""
    import se_vault
    import touchid_approve as TA
    from entitlement import p256
    env = _env(env)
    platform = sys.platform if platform is None else platform
    t = clean_text(text)
    if not t:
        return None, "empty"
    if len(t) > QUOTE_MAX:
        return None, "too_long"
    if platform != "darwin":
        return None, "touchid_unavailable"
    if str(env.get("AIB_TOUCHID", "")).strip() == "0":
        return None, "touchid_off"
    pub = local_pub(env)
    if not pub:
        return None, "no_key"
    payload = {"v": 1, "text": t, "at": now or _now_iso(), "seat": str(seat or ""),
               "nonce": secrets.token_hex(8), "key_fp": key_fp(pub)}
    msg = canonical(payload)
    shown = __import__("approve_gate").se_reason(touchid_reason(t, env))
    if not shown:
        return None, "too_long"
    args = ["se-sign", "--name", KEY_NAME, "--dir", se_vault.se_dir(env), "--reason", shown]
    with TA.one_at_a_time(state_root(env) / "locks" / "touchid-approve.lock") as gate:
        if not gate.held:
            return None, "busy"
        out, why = se_vault._call(args, env, msg, run, helper, platform)
    if why:
        return None, {"cancel": "touchid_cancel", "no_biometry": "touchid_unavailable"}.get(why, why)
    try:
        sig = base64.b64decode(out.decode("ascii").strip(), validate=True)
    except (ValueError, UnicodeDecodeError):
        return None, "helper_failed"
    if not p256.verify(pub, msg, sig):
        return None, "bad_sig"
    evt = evt_of(payload)
    rec = {"v": 1, "evt": evt, "payload": payload, "sig_der_b64": base64.b64encode(sig).decode("ascii")}
    d = quotes_dir(env)
    d.mkdir(parents=True, exist_ok=True)
    p = _evt_path(evt, env)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return {"evt": evt, "key_fp": payload["key_fp"], "at": payload["at"]}, None


def verify(evt, env=None, certs=None, certs_at=None, trusted=None):
    """回結果 dict:status ∈ ok / bad-sig / unverified-root / revoked / before-register / not-found / bad-evt。
    只有 ok 代表「伺服器登錄過的那把鑰、按指紋簽的、這段字一字未改」。"""
    from entitlement import p256
    env = _env(env)
    res = {"evt": evt, "status": "not-found"}
    if not EVT_RE.fullmatch(str(evt or "")):
        res["status"] = "bad-evt"
        return res
    try:
        rec = json.loads(_evt_path(evt, env).read_text(encoding="utf-8"))
        payload = rec["payload"]
        sig = base64.b64decode(rec["sig_der_b64"], validate=True)
    except (OSError, ValueError, KeyError, TypeError):
        return res
    if not isinstance(payload, dict) or evt_of(payload) != evt or rec.get("evt") != evt:
        res["status"] = "bad-evt"
        return res
    res.update(text=payload.get("text"), at=payload.get("at"), key_fp=payload.get("key_fp"))
    if certs is None:
        certs, certs_at = load_certs(env)
    res["certs_at"] = certs_at
    msg = canonical(payload)
    good = [c for c in certs if cert_ok(c, trusted) and c.get("key_fp") == payload.get("key_fp")]
    if not good:
        pub = local_pub(env)
        local_ok = bool(pub) and key_fp(pub) == payload.get("key_fp") and p256.verify(pub, msg, sig)
        res["status"] = "unverified-root" if local_ok else "bad-sig"
        return res
    cert = good[0]
    if not p256.verify(base64.b64decode(cert["pub_x963_b64"]), msg, sig):
        res["status"] = "bad-sig"
        return res
    at = _ts(payload.get("at"))
    revoked = [_ts(c.get("revoked_at")) for c in good if c.get("revoked_at") is not None]
    if at is None or at < _ts(cert["registered_at"]):
        res["status"] = "before-register"
    elif revoked and at >= min(revoked):
        res["status"] = "revoked"
    else:
        res["status"] = "ok"
    res["account_id"] = cert.get("account_id")
    return res


def key_status(env=None):
    pub = local_pub(env)
    certs, at = load_certs(env)
    fp = key_fp(pub) if pub else None
    mine = [c for c in certs if fp and c.get("key_fp") == fp and cert_ok(c)]
    return {"key_fp": fp, "registered": bool(mine), "certs_at": at, "trusted_keys": len(TRUSTED_QUOTE_KEYS)}


def _say(key, params=None):
    import i18n_lib
    return i18n_lib.t(key, params or {}, locale=i18n_lib.resolve_locale())


def main(argv=None, out=None, env=None):
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="aib quote")
    sub = ap.add_subparsers(dest="cmd")
    k = sub.add_parser("key")
    k.add_argument("action", choices=("init", "status"))
    a = sub.add_parser("add")
    a.add_argument("text")
    a.add_argument("--seat", default="")
    v = sub.add_parser("verify")
    v.add_argument("evt")
    for p in (k, a, v):
        p.add_argument("--json", action="store_true")
    try:
        o = ap.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    if not o.cmd:
        out.write(_say("aib.quote.help.usage") + "\n")
        return 2

    def emit(d, rc):
        out.write((json.dumps(d, ensure_ascii=False) if o.json else
                   "\n".join("%s: %s" % (kk, vv) for kk, vv in d.items())) + "\n")
        return rc

    if o.cmd == "key" and o.action == "status":
        return emit(key_status(env), 0)
    if o.cmd == "key":
        r, why = key_init(env)
        return emit(dict(r, ok=True) if r else {"ok": False, "why": why}, 0 if r else 2)
    if o.cmd == "add":
        r, why = add(o.text, o.seat, env)
        if why:
            d = {"ok": False, "why": why}
            if why in ("too_long", "no_key"):
                d["hint"] = _say("aib.quote.hint." + why, {"max": QUOTE_MAX})
            return emit(d, 2)
        return emit(dict(r, ok=True), 0)
    r = verify(o.evt, env)
    return emit(r, 0 if r["status"] == "ok" else 3)


if __name__ == "__main__":
    sys.exit(main())
