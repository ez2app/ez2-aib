#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""Seat quota before dispatch (item 44).

Why this exists
    The dispatch table used hand-copied percentages. A hand-copied number has
    three diseases, and all three look exactly like a normal number:

    1. Used or left?  The machine stores **used** percent. "9% left" and
       "9% used" differ by a factor of ten and read the same length.
       This tool prints **both**, each with its own word.
    2. Whose quota is it?  A quota belongs to **one engine account**, not to
       one seat. This cabin's six seats are all Claude, so they share one
       quota. "This seat has 91% left" invites dispatching six people as if
       there were six quotas. This tool always spells out who shares it.
    3. When was it measured?  Probes are throttled and they fail. A
       three-hour-old number looks identical to a current one. Every cell
       carries its measurement time; over an hour is named as stale.

Not measurable is not zero
    Probe failure, CLI missing, never probed -- the reason text is printed
    verbatim. Never a zero, never a carried-over old value.
    ★ And not-measurable does **not** block anyone: `--min-remaining` returns
    non-zero only when a value was actually measured and is below the bar.
    A gate that also blocks when the probe is broken becomes
    "probe breaks -> nobody can be dispatched -> nobody can fix the probe".

All user-facing text comes from i18n/{zh-TW,en,ja,ko}.json via i18n_lib.
This file holds no second copy of any string.

Usage
    seat-usage.py                     every seat in this cabin, per engine
    seat-usage.py --for 3202b         one seat (paste into a delivery note)
    seat-usage.py --json              machine readable
    seat-usage.py --min-remaining 10  non-zero only if measured and below

rc: 0 fine (including not-measurable) / 1 measured and below / 2 usage error
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys

# ★ 順序是承重的:本樹 insert(0)、fallback append(見 render-dispatch.py 的註)。
_here = pathlib.Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
_fallback = pathlib.Path.home() / "ez2-aib" / "src" / "cockpit"
if str(_fallback) not in sys.path:
    sys.path.append(str(_fallback))
import i18n_lib  # noqa: E402

LOC = os.environ.get("EZ2_AIB_LOCALE", "zh-TW")


def T(key, params=None):
    """唯一的字串入口 —— locale 只在這裡決定一次。"""
    return i18n_lib.t(key, params, locale=LOC)


TAIPEI = datetime.timezone(datetime.timedelta(hours=8))
STALE_S = 60 * 60           # 與 usage_probe.STALE_S 同義;超過就明寫「過期」
WINDOW_ORDER = ("5h", "7d")
STALE_MARK = "  ⚠"


def _looked_up(key, fallback):
    """查字典;查不到回 fallback,而不是把 i18n 的失敗字串印到畫面上。

    ★ `i18n_lib` 查不到時回 `!!key!!`(刻意 fail-loud),**不是** key 本身。
    把判準寫成 `text == key` 會漏掉那個形狀,畫面上就出現
    `!!aib.role.xxx!!` —— 它看起來像一個奇怪的英文字,不像一個錯誤。
    同型的錯在 `src/cockpit/feature_policy.py:_t` 也犯過一次,那一次是臂抓到的。
    """
    text = T(key)
    if not isinstance(text, str) or not text:
        return fallback
    if text == key or (text.startswith("!!") and text.endswith("!!")):
        return fallback
    return text


def window_label(win):
    return _looked_up("aib.seatusage.window.%s" % win, win)


def role_label(role):
    return _looked_up("aib.role.%s" % (role or ""), role or "?")


# ------------------------------------------------------------------ 讀檔層
def seats_dir(env=None):
    e = os.environ if env is None else env
    home = (e.get("EZ2_AIB_INSTANCE_HOME") or "").strip()
    if not home:
        return None
    return pathlib.Path(os.path.expanduser(home)) / "seats"


def usage_dir(env=None):
    e = os.environ if env is None else env
    raw = e.get("EZ2_USAGE_DIR") or "~/.ez2/usage"
    return pathlib.Path(os.path.expanduser(raw))


def read_seats(env=None):
    """回 [{seat, seat_id, window_id, role, provider}],照窗號排。"""
    d = seats_dir(env)
    if d is None or not d.is_dir():
        return []
    out = []
    for path in sorted(d.glob("w*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        uuid = str(rec.get("session_uuid") or "")
        out.append({
            "seat": uuid[-5:] if uuid else "",
            "seat_id": str(rec.get("seat_id") or ""),
            "window_id": str(rec.get("window_id") or ""),
            "role": str(rec.get("role") or ""),
            "provider": str(rec.get("provider") or "").strip().lower(),
        })
    out.sort(key=lambda r: int(r["window_id"].lstrip("@") or 0)
             if r["window_id"].lstrip("@").isdigit() else 999)
    return out


def read_records(env=None):
    """回 {(engine, window): record}。只收 tier=shared 的共用配額那一份。"""
    d = usage_dir(env)
    out = {}
    if not d.is_dir():
        return out
    for path in sorted(d.glob("*-shared.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        eng, win = rec.get("engine"), rec.get("window")
        if not eng or not win:
            continue
        rec["_path"] = str(path)
        out[(str(eng), str(win))] = rec
    return out


# ------------------------------------------------------------------ 純函數
def _parse_z(raw):
    if not isinstance(raw, str):
        return None
    try:
        return datetime.datetime.strptime(
            raw, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return None


def _ago_text(observed_at, now):
    ts = _parse_z(observed_at)
    if ts is None:
        return T("aib.seatusage.ago_unknown"), True
    age = max(0.0, now - ts)
    if age < 90:
        return T("aib.seatusage.ago_just"), False
    if age < STALE_S:
        return T("aib.seatusage.ago_minutes", {"n": int(age // 60)}), False
    return T("aib.seatusage.ago_stale", {"h": "%.1f" % (age / 3600.0)}), True


def _when(epoch):
    if not isinstance(epoch, (int, float)) or epoch <= 0:
        return T("aib.seatusage.reset_unknown")
    t = datetime.datetime.fromtimestamp(float(epoch), TAIPEI)
    return T("aib.seatusage.reset_at", {"when": t.strftime("%m-%d %H:%M")})


def window_line(rec, now):
    """一個視窗一行。**已用與還剩兩個都寫出來**,不要只給一個數字。"""
    label = window_label(str(rec.get("window") or ""))
    ago, stale = _ago_text(rec.get("observed_at"), now)
    if rec.get("status") != "ok" or rec.get("used_pct") is None:
        reason = str(rec.get("reason") or "")
        return T("aib.seatusage.unmeasurable",
                 {"label": label, "reason": reason, "ago": ago}), None
    used = int(rec["used_pct"])
    left = 100 - used
    return T("aib.seatusage.measured", {
        "label": label, "used": used, "left": left,
        "resets": _when(rec.get("resets_at")), "ago": ago,
        "mark": STALE_MARK if stale else ""}), left


def engine_block(engine, seats, records, now, env=None):
    """一個引擎一塊:先講這份額度是誰在共用,再逐個視窗。"""
    # 角色字樣本身就帶括號(「主管(PM)」),所以這裡不再加一層括號 ——
    # 「a seat(主管(PM))」讀起來像漏了字。
    who = "\u3001".join(
        "@%s %s" % (s["seat"] or s["seat_id"], role_label(s["role"]))
        for s in seats)
    lines = [T("aib.seatusage.pool",
               {"engine": engine, "n": len(seats), "who": who})]
    lows = []
    seen = False
    for win in WINDOW_ORDER:
        rec = records.get((engine, win))
        if rec is None:
            continue
        seen = True
        text, left = window_line(rec, now)
        lines.append("  " + text)
        if left is not None:
            lows.append((win, left))
        src = str(rec.get("source") or "")
        if src:
            lines.append("    " + T("aib.seatusage.source", {"path": src}))
    if not seen:
        lines.append("  " + T("aib.seatusage.no_record",
                              {"dir": str(usage_dir(env))}))
    return lines, lows


def report(seats, records, now, only=None, env=None):
    """回 (行, 最低剩餘)。最低剩餘是 None 代表**一格都沒量到**,不是 0。"""
    rows = [s for s in seats if (not only or s["seat"] == only
                                 or s["seat_id"] == only)]
    if only and not rows:
        return [T("aib.seatusage.no_seat", {"seat": only})], None
    engines = {}
    for s in rows:
        engines.setdefault(s["provider"] or T("aib.seatusage.unknown_engine"),
                           []).append(s)
    lines = [T("aib.seatusage.head"), ""]
    lowest = None
    for engine in sorted(engines):
        block, lows = engine_block(engine, engines[engine], records, now,
                                   env=env)
        lines.extend(block)
        lines.append("")
        for _win, left in lows:
            lowest = left if lowest is None else min(lowest, left)
    lines.append(T("aib.seatusage.tail"))
    return lines, lowest


# --------------------------------------------------------------------- CLI
def main(argv=None):
    p = argparse.ArgumentParser(
        prog="seat-usage", description=T("aib.seatusage.cli_desc"))
    p.add_argument("--for", dest="only", default=None,
                   help=T("aib.seatusage.cli_for"))
    p.add_argument("--json", action="store_true",
                   help=T("aib.seatusage.cli_json"))
    p.add_argument("--min-remaining", type=int, default=None,
                   help=T("aib.seatusage.cli_min"))
    args = p.parse_args(argv)

    seats = read_seats()
    if not seats:
        print(T("aib.seatusage.no_seat_files"), file=sys.stderr)
        return 2
    records = read_records()
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    lines, lowest = report(seats, records, now, only=args.only)

    if args.json:
        print(json.dumps({
            "seats": seats,
            "records": {"%s-%s" % k: v for k, v in sorted(records.items())},
            "lowest_remaining_pct": lowest,
            "measured_at": datetime.datetime.now(TAIPEI).isoformat(
                timespec="seconds"),
        }, ensure_ascii=False, indent=2))
    else:
        print("\n".join(lines))

    if args.min_remaining is None:
        return 0
    if lowest is None:
        # ★ 一格都沒量到 ⇒ 放行。門檻只在量到的證據上生效。
        print(T("aib.seatusage.threshold_inactive",
                {"n": args.min_remaining}), file=sys.stderr)
        return 0
    if lowest < args.min_remaining:
        print(T("aib.seatusage.threshold_below",
                {"low": lowest, "n": args.min_remaining}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
