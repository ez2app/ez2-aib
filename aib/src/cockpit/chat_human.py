#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""真人從艦長室網頁寄線上私訊(10-18 @74e8b 回報:網頁寄全球信 rc 2「推不出寄件席」)。只用標準庫。

- 網頁伺服器跟 AI 席是同一個使用者跑的程式:光帶「我是真人」任何席都能照抄 ⇒ 核心自己要本人證明。
- **Mac 一律走 Touch ID**(維護者 10-18 03:1x 裁):每次寄都彈 Touch ID / 登入密碼視窗(touchid_approve),
  視窗文字只由核心組「代你寄給 <對象>:<內文前 40 字>」、nonce、整台一次一窗;AI 按不到。
  AIB_TOUCHID=0 只會關掉這條路(退回 PIN),沒有任何打開或略過的開關。
- **非 Mac 才退回 PIN**:PIN 只防誤操作,擋不住同一使用者的程式(讀得到艦長室的 PIN 雜湊可離線猜,也刪得掉錯誤計數檔)。
  PIN 只從 stdin 進來(不放參數 / 環境變數,ps 看得到);讀-判-寫整段在 fcntl 鎖底下(併發猜不會繞過遞增鎖)。
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

PIN_SCHEME = "pbkdf2_sha256"          # 跟艦長室 captain_room.PIN_SCHEME 一字不差(底線;10-18 寫成連字號,真 PIN 全被擋):scheme$iters$salt$hex
PIN_MAX_FAILS = 5            # 連錯這麼多次開始鎖
PIN_LOCK_S = 30              # 第一次鎖 30 秒,之後每錯一次翻倍(@abb89 10-18 01:46:固定 30 秒一天可猜約 1.4 萬次)
PIN_LOCK_MAX_S = 3600        # 上限 1 小時;只有輸對才歸零,計數落檔(重開程式不歸零)
PIN_MIN_LEN = 6


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


def _locked_fails(env):
    """錯誤計數檔的讀-判-寫整段上鎖(@abb89 10-18:不鎖的話,同時送很多次猜測會各自讀到舊的次數、繞過遞增)。"""
    import contextlib
    import fcntl

    @contextlib.contextmanager
    def _cm():
        lp = _fails_path(env).with_suffix(".lock")
        lp.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lp), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    return _cm()


def check_pin(pin, env, now=None):
    with _locked_fails(env):
        return _check_pin(pin, env, now)


def _check_pin(pin, env, now=None):
    """None = 通過;否則 why:no_pin / pin_not_hashed / pin_too_short / pin_locked / bad_pin。
    只認 pbkdf2 雜湊(艦長室舊版存的明碼不認,請重設);輸入的 PIN 至少 PIN_MIN_LEN 位(短的請用 aib room set-pin 換長的)。
    連錯 PIN_MAX_FAILS 次起鎖 PIN_LOCK_S 秒,之後每錯一次翻倍、上限 PIN_LOCK_MAX_S;輸對才歸零。"""
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
    if not isinstance(pin, str) or len(pin) < PIN_MIN_LEN:
        return "pin_too_short"
    if _matches(pin, stored):
        if st:
            fp.unlink(missing_ok=True)
        return None
    n = int(st.get("n") or 0) + 1
    lock = min(PIN_LOCK_MAX_S, PIN_LOCK_S * 2 ** (n - PIN_MAX_FAILS)) if n >= PIN_MAX_FAILS else 0
    st = {"n": n, "until": now + lock if lock else 0}
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


def _who(to, env):
    """視窗上顯示的收件人:好友備註名 > 名冊名 > 主人 npub 縮寫。"""
    import chat_roster as CR
    owner = CR.friend_owner(to) or ""
    f = next((x for x in CR.read_friends(env) + CR.read_friends(env, "mine") if x["owner"] == owner), None)
    return (f or {}).get("name") or (owner[:9] + "…" + owner[-4:] if owner else to)


def touchid_reason(to, text, env):
    """Touch ID 視窗那一句:只由核心組,資料先過 touchid_approve.clean(去控制字元、零寬字、截長)。"""
    import touchid_approve as TA
    import i18n_lib
    return i18n_lib.t("aib.chat.send_touchid_reason", {"who": TA.clean(_who(to, env), 32), "text": TA.clean(text, 40)},
                      locale=i18n_lib.resolve_locale())


def prove_human(to, text, pin, env, touchid=None, touchid_available=None):
    """本人證明:Mac(Touch ID 可用)⇒ 彈視窗,PIN 不看;否則 PIN。回 (via, why);why None = 過。"""
    import touchid_approve as TA
    can = touchid_available() if touchid_available else TA.available(environ=env)
    if can:
        root = _state_root(env)
        with TA.one_at_a_time(root / "locks" / "touchid-approve.lock") as gate:
            res = (touchid or TA.ask)(touchid_reason(to, text, env)) if gate.held else {"ok": False, "why": "busy"}
        return "touchid", None if res.get("ok") is True else "touchid_" + str(res.get("why") or "failed").split(":")[0]
    return "pin", check_pin(pin, env)


def send(to, text, pin, cabin, env=None, wait=None, touchid=None, touchid_available=None):
    """回 {rc, stage, msgid, seat, via, why, detail};rc 同 aib dm(0 到 / 2 沒送出 / 3 對方待審 / 4 交出不知道到沒)。
    touchid / touchid_available 只給測試換掉(真用時就是 touchid_approve 那一套)。"""
    import dm_global_files as GF
    env = dict(os.environ if env is None else env)
    out = {"v": 1, "rc": 2, "stage": "abort", "msgid": None, "seat": None, "via": None, "why": None, "detail": ""}
    if not GF.parse_address(to or ""):
        out["why"] = "bad_address"
        return out
    if not isinstance(text, str) or not text.strip():
        out["why"] = "empty"
        return out
    seat = pick_seat(cabin, env)                         # 先看有沒有席可代簽,沒有就不必叫人按指紋
    if not seat:
        out["why"] = "no_public_seat"
        return out
    out["via"], why = prove_human(to, text, pin, env, touchid, touchid_available)
    if why:
        out["why"] = why
        return out
    root = Path(env.get("AIB_CHAT_BRIDGE_DIR") or GF.default_root(env))
    GF.ensure_dirs(root)
    rec = {"v": GF.VERSION, "msgid": GF.new_msgid(), "created_at": int(time.time()), "op": "send",
           "from": {"cabin": cabin, "seat": seat, "attested": True}, "to": to, "content": text, "author": "human"}
    w = wait if wait is not None else int(env.get("AIB_DM_GLOBAL_WAIT") or GF.WAIT_DEFAULT)
    res = GF.send_and_wait(root, rec, wait=w)
    out.update(rc=res["rc"], stage=res["stage"], msgid=res["msgid"], seat=seat, detail=res.get("detail") or "")
    return out
