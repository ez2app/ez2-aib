#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""手機遠端解鎖單(10-21 維護者:「手機版、電腦版能直接指揮 aib」「有 Touch ID、或刷臉優先，沒有使用輸入設定密碼也行」
「liff 啟用也算是一種解鎖，但一樣受密碼與倒數約束」)。

手機在 <名字>.ez2.page 用 passkey(Face ID / 指紋)、LINE(LIFF)+ 密碼、或密碼證明本人 ⇒ **ez2.page 伺服器驗** ⇒ 伺服器用專用私鑰簽
一張「解鎖 @席 N 分鐘」單子 ⇒ 經艦長室送到本機 ⇒ 這支驗簽 ⇒ 開跟 Touch ID 同一個閒置視窗(seat_act.grant_unlock)。

防線(GUIDE-same-user-ai-threat-model「簽章」那一類):
- 公鑰清單寫死在這支(已簽版本);**不從設定檔 / 環境變數讀**(那等於可換)。改清單要維護者點頭,測試釘著。
- 驗:v / kind / kid 在清單 / Ed25519 簽章(canonical JSON,去掉 sig)/ machine = 這台 machine.json / account_id = 這台綁的帳號 /
  seat 對 / 沒過期(exp - iat ≤ 5 分鐘、iat 不在未來 60 秒以外)/ nonce 沒用過 / auth 在允許清單。
- 本機總開關 remote.json:預設關;**打開要本機 Touch ID**(aib seat remote on),關一鍵。⚠ 這是本機檔、AI 改得到:只防「伺服器被拿下就能對本機下指令」
  這種情況下本人沒打開的機器;不宣稱擋得住本機 AI。
- 用過的 nonce 記在本機(AI 刪得掉 ⇒ 只防網路重放;單子 5 分鐘內過期把重放窗壓小)。
- 視窗開了之後跟 Touch ID 開的同一前提：本機程式也能打字(seat_act docstring 照實寫)。
"""
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

V = 1
KIND = "seat-unlock"
MAX_LIFE_S = 300
SKEW_S = 60
AUTHS = ("passkey", "line", "password")
SEAT_RE = re.compile(r"[0-9a-z]{5}")
NONCE_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")

# kid ⇒ "ed25519:<64 hex>"。ez2.page 伺服器簽解鎖單的專用鑰(不跟授權 / 收據 / 外掛簽章共用);正式鑰由維護者產、私鑰只在 ez2.page 伺服器。
#   空 = 還沒有正式鑰 ⇒ 所有單子一律拒(fail closed)。改這份清單要維護者點頭(tests/test-remote-grant-74e8b-1021.py 釘著)。
TRUSTED_GRANT_KEYS = {}


def state_root(env=None):
    import seats_dir_lib
    e = os.environ if env is None else env
    return Path(e.get("EZ2_AIB_STATE_ROOT") or seats_dir_lib.state_root()[0])


def _canonical(obj):
    from entitlement import signing
    return signing.canonical(obj)


def _b64(text):
    import base64
    s = str(text or "").strip()
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.replace("+", "-").replace("/", "_"))


def remote_path(env=None):
    return state_root(env) / "panel-unlock" / "remote.json"


def remote_cfg(env=None):
    """{on, max_min, allow}:沒檔 / 壞檔 ⇒ 關"""
    try:
        d = json.loads(remote_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"on": False, "max_min": 60, "allow": list(AUTHS)}
    allow = [a for a in (d.get("allow") or AUTHS) if a in AUTHS] if isinstance(d.get("allow"), list) else list(AUTHS)
    try:
        mx = max(1, min(60, int(d.get("max_min") or 60)))
    except (TypeError, ValueError):
        mx = 60
    return {"on": d.get("on") is True, "max_min": mx, "allow": allow}


def set_remote(on, env=None, max_min=None, allow=None):
    from atomic_json import write_json
    cur = remote_cfg(env)
    cur["on"] = bool(on)
    if max_min is not None:
        cur["max_min"] = max(1, min(60, int(max_min)))
    if allow is not None:
        cur["allow"] = [a for a in allow if a in AUTHS]
    p = remote_path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json(str(p), dict(cur, v=V, at=int(time.time())))
    os.chmod(p, 0o600)
    return cur


def this_machine(env=None):
    """(machine_id, {account_id…}):ez2.page 綁定時的 machine.json uuid、綁到這台(記錄的 machine_id 相同)的每一頁 tenant_id;沒綁 ⇒ (None, set())"""
    e = dict(os.environ if env is None else env)
    if str(HERE / "page") not in sys.path:
        sys.path.insert(0, str(HERE / "page"))
    try:
        import store
        directory, _src = store.token_dir(e)
        m = json.loads(Path(directory, "machine.json").read_text(encoding="utf-8")).get("id")
        recs = store.list_tokens(e)
    except Exception:   # 沒綁 / 讀不到 ⇒ 不接受任何單子
        return None, set()
    if not isinstance(m, str) or not m:
        return None, set()
    accts = {str(r.get("tenant_id")) for r in recs if r.get("machine_id") == m and r.get("tenant_id") not in (None, "")}
    return m, accts


def _seen_path(env=None):
    return state_root(env) / "panel-unlock" / "grant-seen.json"


def _seen(env, nonce, exp, now, mark):
    from atomic_json import write_json
    p = _seen_path(env)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        d = {k: int(v) for k, v in d.items() if int(v) > now} if isinstance(d, dict) else {}
    except (OSError, ValueError, TypeError):
        d = {}
    if nonce in d:
        return True
    if mark:
        d[nonce] = int(exp) + SKEW_S
        p.parent.mkdir(parents=True, exist_ok=True)
        write_json(str(p), d)
        os.chmod(p, 0o600)
    return False


def verify(grant, seat, env=None, now=None, keys=None, machine=None):
    """單子 dict ⇒ (minutes, None) 或 (None, why)。keys / machine 只給測試注入(正式一律用寫死的清單、這台的綁定)。"""
    from entitlement import ed25519
    t = int(now or time.time())
    if not isinstance(grant, dict):
        return None, "bad-grant"
    cfg = remote_cfg(env)
    if not cfg["on"]:
        return None, "remote-off"
    if grant.get("v") != V or grant.get("kind") != KIND:
        return None, "bad-grant"
    keyset = TRUSTED_GRANT_KEYS if keys is None else keys
    kid = grant.get("kid")
    pub = keyset.get(kid) if isinstance(kid, str) else None
    if not pub:
        return None, "unknown-key"
    body = {k: v for k, v in grant.items() if k != "sig"}
    try:
        ok = ed25519.verify(bytes.fromhex(pub.split(":", 1)[1]), _canonical(body), _b64(grant.get("sig")))
    except (ValueError, TypeError, IndexError):
        ok = False
    if not ok:
        return None, "bad-sig"
    mid, acct = machine if machine is not None else this_machine(env)
    if not mid or grant.get("machine") != mid:
        return None, "wrong-machine"
    if str(grant.get("account_id")) not in acct:
        return None, "wrong-account"
    if grant.get("seat") != seat or not isinstance(seat, str) or not SEAT_RE.fullmatch(seat):
        return None, "wrong-seat"
    try:
        iat, exp, mins = int(grant["iat"]), int(grant["exp"]), int(grant["minutes"])
    except (KeyError, TypeError, ValueError):
        return None, "bad-grant"
    if iat > t + SKEW_S or exp <= t or exp - iat > MAX_LIFE_S:
        return None, "expired"
    if grant.get("auth") not in cfg["allow"]:
        return None, "auth-not-allowed"
    nonce = grant.get("nonce")
    if not isinstance(nonce, str) or not NONCE_RE.fullmatch(nonce):
        return None, "bad-grant"
    if _seen(env, nonce, exp, t, mark=True):
        return None, "replayed"
    return max(1, min(cfg["max_min"], mins)), None
