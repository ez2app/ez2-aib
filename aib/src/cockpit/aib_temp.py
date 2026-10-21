#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib temp:臨時主機變更的到期提醒(PROPOSAL-gaps v2 F;@ee4f8 第十件,Andy 10-21 02:4x 准)。

測試網址、暫用的金鑰、一次性 cron、臨時開的 SFTP…… 以前靠席位記得拆,席一 /clear 或 compaction 就可能忘。
  aib temp add <主機> "<描述>" --until <YYYY-MM-DD | ISO 時間> --undo "<怎麼拆>" [--seat <席碼>]
  aib temp list [--all] [--json]       還沒拆的(--all 連拆過的),照到期排
  aib temp check [--json]              有過期沒拆的 ⇒ rc 1(健檢 / 排程用);aib doctor 也會列
  aib temp done <id> [--why "<一句>"]   拆了
這只防**忘記**,不是防線:清單是本機檔,AI 改得到;到期提醒不擋任何事。
"""
import argparse
import datetime
import json
import os
import re
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

MAX_OPEN = 50                 # 還沒拆的上限(檔案 64KB 上限內;太多就是沒在拆)
KEEP_DONE_DAYS = 30           # 拆過的留 30 天就清
LIMITS = {"host": 120, "desc": 200, "undo": 300, "why": 200}
ID_RE = re.compile(r"tmp-[0-9a-f]{6}")
SEAT_RE = re.compile(r"[0-9a-f]{5}")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")                                    # 控制字元 ⇒ 空白
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")     # 零寬 / 方向字元 ⇒ 刪掉


class TempError(Exception):
    def __init__(self, why, **extra):
        super().__init__(why)
        self.why = why
        self.extra = extra


def _env(env):
    return dict(os.environ if env is None else env)


def state_root(env=None):
    import seats_dir_lib
    e = _env(env)
    return Path(e.get("EZ2_AIB_STATE_ROOT") or seats_dir_lib.state_root(e)[0])


def store_path(env=None):
    return state_root(env) / "temp" / "changes.json"


def _now(now=None):
    return now or datetime.datetime.now().astimezone()


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _clean(field, text):
    t = _CTRL.sub(" ", _INVISIBLE.sub("", str(text or ""))).strip()
    if not t:
        raise TempError("empty_" + field)
    if len(t) > LIMITS[field]:
        raise TempError("too_long_" + field, max=LIMITS[field])
    return t


def parse_until(text, now=None):
    """YYYY-MM-DD ⇒ 當天 23:59:59(本機時區);ISO 時間要帶時區。過去的時間拒(登記當下就過期沒有意義)。"""
    now = _now(now)
    s = str(text or "").strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            d = datetime.date.fromisoformat(s)
            dt = datetime.datetime.combine(d, datetime.time(23, 59, 59)).replace(tzinfo=now.tzinfo)
        else:
            dt = datetime.datetime.fromisoformat(s)
            if dt.tzinfo is None:
                raise TempError("bad_until")
    except ValueError:
        raise TempError("bad_until")
    if dt <= now:
        raise TempError("until_past")
    return dt


def self_seat():
    """呼叫的那一席(seat_who 量到的末五碼);量不到 ⇒ ""(不猜)。"""
    try:
        import seat_who
        u = seat_who.self_seat_uuid()
    except Exception:  # noqa: BLE001
        return ""
    code = u[-5:].lower() if isinstance(u, str) and len(u) >= 5 else ""
    return code if SEAT_RE.fullmatch(code) else ""


def _load(env):
    try:
        d = json.loads(store_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = d.get("items") if isinstance(d, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _update(env, mutate):
    import atomic_json
    out = {}

    def m(rec):
        items = [i for i in rec.get("items") or [] if isinstance(i, dict)]
        out["result"] = mutate(items)
        return {"v": 1, "items": items}
    atomic_json.update_json(str(store_path(env)), m, default={"v": 1, "items": []})
    return out["result"]


def _ts(iso):
    try:
        return datetime.datetime.fromisoformat(str(iso)).timestamp()
    except (TypeError, ValueError):
        return None


def add(host, desc, until, undo, seat=None, env=None, now=None):
    now = _now(now)
    rec = {"id": "tmp-" + secrets.token_hex(3), "host": _clean("host", host), "desc": _clean("desc", desc),
           "undo": _clean("undo", undo), "until": _iso(parse_until(until, now)),
           "seat": seat if seat is not None else self_seat(), "added_at": _iso(now), "done_at": None}
    if rec["seat"] and not SEAT_RE.fullmatch(rec["seat"]):
        raise TempError("bad_seat")
    cutoff = now.timestamp() - KEEP_DONE_DAYS * 86400

    def m(items):
        items[:] = [i for i in items if not i.get("done_at") or (_ts(i["done_at"]) or 0) >= cutoff]
        if sum(1 for i in items if not i.get("done_at")) >= MAX_OPEN:
            raise TempError("too_many", max=MAX_OPEN)
        while any(i.get("id") == rec["id"] for i in items):
            rec["id"] = "tmp-" + secrets.token_hex(3)
        items.append(rec)
        return dict(rec)
    return _update(env, m)


def done(item_id, why="", by=None, env=None, now=None):
    if not ID_RE.fullmatch(str(item_id or "")):
        raise TempError("bad_id")
    why = _clean("why", why) if str(why or "").strip() else ""
    by = by if by is not None else self_seat()

    def m(items):
        for i in items:
            if i.get("id") == item_id:
                if i.get("done_at"):
                    raise TempError("already_done")
                i.update(done_at=_iso(_now(now)), done_by=by, done_why=why)
                return dict(i)
        raise TempError("not_found")
    return _update(env, m)


def listing(env=None, include_done=False, now=None):
    t = _now(now).timestamp()
    rows = []
    for i in _load(env):
        if i.get("done_at") and not include_done:
            continue
        u = _ts(i.get("until"))
        rows.append(dict(i, overdue=bool(not i.get("done_at") and u is not None and u <= t)))
    return sorted(rows, key=lambda r: (_ts(r.get("until")) or 0))


def overdue(env=None, now=None):
    return [r for r in listing(env, now=now) if r["overdue"]]


def _say(key, params=None):
    import i18n_lib
    return i18n_lib.t(key, params or {}, locale=i18n_lib.resolve_locale())


def _row(r):
    key = "aib.temp.row_overdue" if r.get("overdue") else ("aib.temp.row_done" if r.get("done_at") else "aib.temp.row")
    return _say(key, {"id": r["id"], "host": r["host"], "until": r["until"], "seat": r.get("seat") or "?",
                      "desc": r["desc"], "undo": r["undo"]})


def main(argv=None, out=None, env=None):
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="aib temp")
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("add")
    a.add_argument("host")
    a.add_argument("desc")
    a.add_argument("--until", required=True)
    a.add_argument("--undo", required=True)
    a.add_argument("--seat")
    ls = sub.add_parser("list")
    ls.add_argument("--all", action="store_true")
    ck = sub.add_parser("check")
    d = sub.add_parser("done")
    d.add_argument("id")
    d.add_argument("--why", default="")
    for p in (a, ls, ck, d):
        p.add_argument("--json", action="store_true")
    try:
        o = ap.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    if not o.cmd:
        out.write(_say("aib.temp.help.usage") + "\n")
        return 2

    def emit(d, text, rc):
        out.write((json.dumps(d, ensure_ascii=False) if o.json else text) + "\n")
        return rc
    try:
        if o.cmd == "add":
            r = add(o.host, o.desc, o.until, o.undo, seat=o.seat, env=env)
            return emit(dict(r, ok=True), _say("aib.temp.added", {"id": r["id"], "until": r["until"]}), 0)
        if o.cmd == "done":
            r = done(o.id, o.why, env=env)
            return emit(dict(r, ok=True), _say("aib.temp.done", {"id": r["id"]}), 0)
        if o.cmd == "list":
            rows = listing(env, include_done=o.all)
            return emit({"items": rows}, "\n".join(_row(r) for r in rows) or _say("aib.temp.none"), 0)
        rows = overdue(env)
        return emit({"overdue": rows}, "\n".join(_row(r) for r in rows) or _say("aib.temp.none_overdue"), 1 if rows else 0)
    except TempError as exc:
        base = next((b for b in ("empty", "too_long") if exc.why.startswith(b + "_")), exc.why)
        hint = _say("aib.temp.err." + base, dict(exc.extra, field=exc.why[len(base) + 1:]))
        return emit(dict({"ok": False, "why": exc.why, "hint": hint}, **exc.extra), hint, 2)


if __name__ == "__main__":
    sys.exit(main())
