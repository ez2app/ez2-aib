#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""真人從艦長室網頁寄線上私訊(10-18 @74e8b 回報:網頁寄全球信 rc 2「推不出寄件席」)。只用標準庫。

- 網頁伺服器跟 AI 席是同一個使用者跑的程式:光帶「我是真人」任何席都能照抄 ⇒ 核心自己驗艦長室 PIN(只有真人知道)。
  PIN 只從 stdin 進來(不放參數 / 環境變數,ps 看得到);連錯 PIN_MAX_FAILS 次鎖 PIN_LOCK_S 秒。
- 用這一艙的艦長席簽(role 開頭 root、有席金鑰、在名冊公開);沒有就用這一艙第一個公開席。
- 待寄檔帶 author = human ⇒ 工人在 rumor 加 aib-author=human(開發文件第 35 節第 2 點)。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

PIN_SCHEME = "pbkdf2-sha256"          # 跟艦長室 captain_room.pin_hash 同一個格式:scheme$iters$salt$hex
PIN_MAX_FAILS = 5
PIN_LOCK_S = 30


def _state_root(env):
    return Path(env.get("EZ2_AIB_STATE_ROOT") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".ez2-aib"))


def pin_config_path(env):
    return _state_root(env) / "state" / "captain-room" / "config.json"


def _fails_path(env):
    import chat_roster as CR
    return CR._chat_home(env) / "human-pin-fails.json"


def _stored_pin(env):
    try:
        return str(json.loads(pin_config_path(env).read_text(encoding="utf-8")).get("pin") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def _matches(given, stored):
    try:
        _, iters, salt, want = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", str(given).encode("utf-8"), bytes.fromhex(salt), int(iters)).hex()
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(got, want)


def check_pin(pin, env, now=None):
    """None = 通過;否則 why:no_pin / pin_not_hashed / pin_locked / bad_pin。只認 pbkdf2 雜湊(艦長室舊版存的明碼不認,請重設)。"""
    now = time.time() if now is None else now
    stored = _stored_pin(env)
    if not stored:
        return "no_pin"
    if not stored.startswith(PIN_SCHEME + "$"):
        return "pin_not_hashed"
    fp = _fails_path(env)
    try:
        st = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    if now < float(st.get("until") or 0):
        return "pin_locked"
    if isinstance(pin, str) and pin and _matches(pin, stored):
        if st:
            fp.unlink(missing_ok=True)
        return None
    n = int(st.get("n") or 0) + 1
    st = {"n": 0, "until": now + PIN_LOCK_S} if n >= PIN_MAX_FAILS else {"n": n, "until": 0}
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(st), encoding="utf-8")
    os.chmod(str(fp), 0o600)
    return "bad_pin"


def pick_seat(cabin, env):
    """這一艙代表真人簽的席:艦長席(role 開頭 root)優先,其次這一艙第一個公開席;要有席金鑰。沒有回 None。"""
    import chat_roster as CR
    import nostr_keys as NK
    pub = set((CR.read_profile(env) or {}).get("public") or [])
    keyed = {e["seat5"] for e in NK.read_map(env)["seats"] if e.get("status") == "active"}
    live = [s for s in NK.live_seats(env=env) if s.get("cabin") == cabin and s["seat5"] in pub and s["seat5"] in keyed]
    roots = [s for s in live if str(s.get("role") or "").startswith("root")]
    pick = (roots or live or [None])[0]
    return pick["seat5"] if pick else None


def send(to, text, pin, cabin, env=None, wait=None):
    """回 {rc, stage, msgid, seat, why, detail};rc 同 aib dm(0 到 / 2 沒送出 / 3 對方待審 / 4 交出不知道到沒)。"""
    import dm_global_files as GF
    env = dict(os.environ if env is None else env)
    out = {"v": 1, "rc": 2, "stage": "abort", "msgid": None, "seat": None, "why": None, "detail": ""}
    if not GF.parse_address(to or ""):
        out["why"] = "bad_address"
        return out
    if not isinstance(text, str) or not text.strip():
        out["why"] = "empty"
        return out
    why = check_pin(pin, env)
    if why:
        out["why"] = why
        return out
    seat = pick_seat(cabin, env)
    if not seat:
        out["why"] = "no_public_seat"
        return out
    root = Path(env.get("AIB_CHAT_BRIDGE_DIR") or GF.default_root(env))
    GF.ensure_dirs(root)
    rec = {"v": GF.VERSION, "msgid": GF.new_msgid(), "created_at": int(time.time()), "op": "send",
           "from": {"cabin": cabin, "seat": seat, "attested": True}, "to": to, "content": text, "author": "human"}
    w = wait if wait is not None else int(env.get("AIB_DM_GLOBAL_WAIT") or GF.WAIT_DEFAULT)
    res = GF.send_and_wait(root, rec, wait=w)
    out.update(rc=res["rc"], stage=res["stage"], msgid=res["msgid"], seat=seat, detail=res.get("detail") or "")
    return out
