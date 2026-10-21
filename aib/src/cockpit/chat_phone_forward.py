#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""BB Call 轉手機(PROPOSAL-gaps 第 3 件;Andy 10-21 02:4x「做,預設關」;@ee4f8 @9f808 @74e8b @0c105 條件)。

給本人的信(待審區 human_reply / im_room)超過 N 分鐘還在 ⇒ 轉一行到主人已配對的手機:
  「艦長室有 N 封新信」—— 不帶內文、不帶寄件人名字、不帶席碼(@ee4f8:鎖屏 / 雲端都會留)。
規矩:
  - 預設關;`aib chat phone-forward on` 才開。
  - 只送「主人簽過的機器清單」裡 role=phone 的手機(chat_nostr._signed_phones;本機 paired.json 只是顯示快取)。
    沒配對 / 抓不到清單 ⇒ 不送,也不記成已轉(配對好了再轉)。
  - 同一封只轉一次;每小時最多 per_hour 則(防狂寄信把手機轟炸);一次送出涵蓋當下所有該轉的信。
  - 走 Nostr 私訊、用這台的機器金鑰簽,不經 ez2.page(@9f808)。Telegram 那條要先有「主人自己的 Telegram」欄位,另開。
  - 帶名字(include_name)還沒做:之後加,開的時候要提示「名字會留在手機鎖屏 / Telegram 雲端」。
開關是本機檔,AI 改得到;最壞是主人手機多 / 少收到「有 N 封新信」,不碰內文,所以不加 Touch ID。
"""
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

DEFAULTS = {"v": 1, "on": False, "minutes": 15, "per_hour": 3}
MINUTES_RANGE = (1, 24 * 60)
PER_HOUR_RANGE = (1, 12)
KEEP_SENT_S = 8 * 86400          # 給本人的信最長留 7 天,多留一天就清
TICK_S = 120


def path(env=None):
    import chat_bridge as CB
    return CB.chat_home(env) / "phone_forward.json"


def load(env=None):
    try:
        raw = json.loads(path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return _clean(raw)


def _clean(raw):
    """檔案內容 ⇒ 清過的設定(範圍外 / 型別不對的欄位一律回預設;開關只認 true)。"""
    rec = dict(DEFAULTS, sent={}, log=[])
    if not isinstance(raw, dict):
        return rec
    rec["on"] = raw.get("on") is True
    for k, (lo, hi) in (("minutes", MINUTES_RANGE), ("per_hour", PER_HOUR_RANGE)):
        v = raw.get(k)
        if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi:
            rec[k] = v
    if isinstance(raw.get("sent"), dict):
        rec["sent"] = {str(k): v for k, v in raw["sent"].items() if isinstance(v, (int, float))}
    if isinstance(raw.get("log"), list):
        rec["log"] = [t for t in raw["log"] if isinstance(t, (int, float))]
    return rec


def _update(env, mutate):
    import atomic_json
    p = path(env)
    p.parent.mkdir(parents=True, exist_ok=True)

    def m(raw):
        rec = _clean(raw)
        mutate(rec)
        return rec
    out = atomic_json.update_json(str(p), m, default={})
    os.chmod(str(p), 0o600)
    return out


def configure(env=None, on=None, minutes=None, per_hour=None):
    """回 (設定, why)。範圍外 ⇒ why,不寫。"""
    for v, (lo, hi), why in ((minutes, MINUTES_RANGE, "bad_minutes"), (per_hour, PER_HOUR_RANGE, "bad_per_hour")):
        if v is not None and not (lo <= v <= hi):
            return None, why

    def m(rec):
        if on is not None:
            rec["on"] = bool(on)
        if minutes is not None:
            rec["minutes"] = minutes
        if per_hour is not None:
            rec["per_hour"] = per_hour
    rec = _update(env, m)
    return {k: rec[k] for k in ("on", "minutes", "per_hour")}, None


def due(env=None, now=None, rec=None):
    """該轉的信:待審區給本人的信、沒過期、躺超過 minutes 分鐘、還沒轉過。回 queue id 清單。"""
    import dm_queue as DQ
    import room_events as RE
    now = time.time() if now is None else now
    rec = load(env) if rec is None else rec
    out = []
    for seat in DQ.all_seats(env):
        for it in DQ._all_items(seat, env=env):
            if not RE.mail_why(it) or it.get("expired") or DQ._expired(it, now):
                continue
            held = it.get("held_at") or it.get("ts")
            if not isinstance(held, (int, float)) or now - held < rec["minutes"] * 60:
                continue
            if str(it.get("id")) in rec["sent"]:
                continue
            out.append(str(it.get("id")))
    return sorted(out)


def plan(env=None, now=None):
    """這一輪要不要送 ⇒ (要送的那一行 或 None, 這次涵蓋的 queue id, why)。不寫檔。"""
    import room_events as RE
    now = time.time() if now is None else now
    rec = load(env)
    if not rec["on"]:
        return None, [], "off"
    ids = due(env, now, rec)
    if not ids:
        return None, [], "none_due"
    if sum(1 for t in rec["log"] if now - t < 3600) >= rec["per_hour"]:
        return None, ids, "cap"
    return line(RE.pending_count(env, now)), ids, None


def line(n):
    import i18n_lib
    return i18n_lib.t("aib.chat.bbcall_phone", {"n": n}, locale=i18n_lib.resolve_locale())


def commit(ids, env=None, now=None):
    """送出成功後記下:這幾封已轉、這一則算進每小時上限;太舊的順手清掉。"""
    now = time.time() if now is None else now

    def m(rec):
        for i in ids:
            rec["sent"][str(i)] = now
        rec["sent"] = {k: v for k, v in rec["sent"].items() if now - v < KEEP_SENT_S}
        rec["log"] = [t for t in rec["log"] if now - t < 3600] + [now]
    _update(env, m)


async def tick(env, now, phones_fn, send_fn):
    """常駐那邊每 TICK_S 叫一次。phones_fn() ⇒ 主人簽過的手機 npub 集合;send_fn(npub, text) ⇒ 成功 True。
    回 why:sent / off / none_due / cap / no_phone / send_failed。至少一支手機收到才記成已轉。"""
    import asyncio
    text, ids, why = await asyncio.to_thread(plan, env, now)
    if text is None:
        return why
    phones = sorted(await phones_fn() or ())
    if not phones:
        return "no_phone"
    ok = False
    for npub in phones:
        ok = bool(await send_fn(npub, text)) or ok
    if not ok:
        return "send_failed"
    await asyncio.to_thread(commit, ids, env, now)
    return "sent"


def cmd(a, env, out):
    """aib chat phone-forward on|off|status [--minutes N] [--per-hour K] [--json]"""
    import i18n_lib

    def T(key, params=None):
        return i18n_lib.t("aib.chat.phone_forward." + key, params or {}, locale=i18n_lib.resolve_locale())
    on = {"on": True, "off": False}.get(a.action)
    if a.action == "status" and a.minutes is None and a.per_hour is None:
        rec = load(env)
        res, why = {k: rec[k] for k in ("on", "minutes", "per_hour")}, None
    else:
        res, why = configure(env, on=on, minutes=a.minutes, per_hour=a.per_hour)
    if why:
        out.write((json.dumps({"ok": False, "why": why}) if a.json else T(why)) + "\n")
        return 2
    if a.json:
        out.write(json.dumps(dict(res, ok=True)) + "\n")
    else:
        out.write(T("status_on" if res["on"] else "status_off", res) + "\n")
    return 0
