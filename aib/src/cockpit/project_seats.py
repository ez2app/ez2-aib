#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""席綁哪個專案的唯一正本(docs/DESIGN-project-sweep-1012.md §2;the maintainer 10-12 15:4x 准,a seat 審)。

為什麼:「哪些席屬於這個專案」原本散在各艙席位檔的 project_uid、identity/seat-continuity/<碼>.json、
open-ack 三處,而且不一致(10-12 實測:seat-continuity 寫著本專案的 14 席全是舊席,真的在做的 a seat、a seat
反而不在裡面)。從這裡起:綁 / 解綁 / 開席接上專案時寫這一份,其他地方(專案清查 sweep、艦長室)只讀它。

鍵是 seat_id(席位檔的 seat_id,4~8 碼十六進位),**不是**席碼(session_uuid 末五碼):
  seat_id 是一席長期的身分(清對話、分出新席都不變),席碼每次分出新對話就換。10-12 實測:a seat 的 seat_id 是 91c5f、
  a seat 的是 edb4f —— seat-continuity 一直是照 seat_id 記的,拿席碼去找才會以為「他們不在」。
  要寄信、列畫面時用 codes_of(seat_id) 從各艙席位檔查現在的席碼。

檔案:<狀態根>/state/project-seats.json
  {"schema": "aib-project-seats/v1",
   "projects": {"proj:…": {"title": "…", "seats": {"<seat_id>": {"since": iso, "by": "…", "until": iso|null}}}}}
  一席同時只綁一個專案:綁新的會把舊的那筆寫上 until(留歷史,不刪)。

  project_seats.py list [--project UID] [--all]   列(--all 含已解綁的;每席附現在的席碼)
  project_seats.py of <seat_id>                    這一席現在綁哪個專案
  project_seats.py migrate [--apply]              從舊的兩處合併進來;預設只印會寫什麼;衝突只列不挑
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import glob
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SCHEMA = "aib-project-seats/v1"
_SEAT_RE = re.compile(r"^[0-9a-f]{4,8}$")       # 與 src/identity/bind_one.py SEAT_ID_RE 同形
_UID_RE = re.compile(r"^proj:[0-9a-f]{16}$")


def state_root(root=None):
    """狀態根:一律走單一真相源 seats_dir_lib.state_root(順位不變,見 seats_dir_lib.STATE_ROOT_ENV / STATE_ROOT_DEFAULT)。
    10-12 合推紅(test-seats-dir-resolution):以前自己拼環境變數 ⇒ 被認成「沒先看 HUB 的解析器」。本檔要的是**所有艙**
    (instances/*/seats 跨艙對帳),不是「這一艙」的席位目錄,所以用狀態根、不用 seats_dir。"""
    if root:
        return Path(root)
    import seats_dir_lib
    return Path(seats_dir_lib.state_root()[0])


def path_of(root=None):
    return state_root(root) / "state" / "project-seats.json"


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def read(root=None):
    try:
        d = json.loads(path_of(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": SCHEMA, "projects": {}}
    if not isinstance(d, dict) or not isinstance(d.get("projects"), dict):
        return {"schema": SCHEMA, "projects": {}}
    return d


@contextlib.contextmanager
def _locked(root=None):
    p = path_of(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(str(p) + ".lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _write(d, root=None):
    p = path_of(root)
    fd, tmp = tempfile.mkstemp(prefix=".project-seats-", dir=str(p.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, p)


def _valid(uid, seat):
    return isinstance(uid, str) and bool(_UID_RE.match(uid)) and isinstance(seat, str) and bool(_SEAT_RE.match(seat))


def bind(uid, seat, title="", by="", root=None, at=None):
    """把 seat 綁到 uid;原本綁在別的專案 ⇒ 那筆寫 until。已經綁在同一個 ⇒ 不動(回 False)。格式不對 ⇒ ValueError。"""
    if not _valid(uid, seat):
        raise ValueError("project_seats.bind: bad uid/seat %r %r" % (uid, seat))
    at = at or _now()
    with _locked(root):
        d = read(root)
        changed = False
        for puid, proj in d["projects"].items():
            rec = (proj.get("seats") or {}).get(seat)
            if rec and not rec.get("until"):
                if puid == uid:
                    if title and proj.get("title") != title:
                        proj["title"] = title
                        _write(d, root)
                    return False
                rec["until"] = at
                changed = True
        proj = d["projects"].setdefault(uid, {"title": title, "seats": {}})
        if title:
            proj["title"] = title
        proj.setdefault("seats", {})[seat] = {"since": at, "by": by, "until": None}
        _write(d, root)
        return True


def bind_quietly(uid, seat, title="", by="", root=None):
    """給寫入點用:正本寫不進去不擋原本的事(綁專案、開席照常),只回 False。"""
    try:
        return bind(uid, seat, title=title, by=by, root=root)
    except (OSError, ValueError):
        return False


def unbind_quietly(seat, by="", root=None):
    try:
        return unbind(seat, by=by, root=root)
    except (OSError, ValueError):
        return False


def unbind(seat, by="", root=None, at=None):
    """把 seat 現在的綁定寫上 until;沒綁 ⇒ False。"""
    if not (isinstance(seat, str) and _SEAT_RE.match(seat)):
        raise ValueError("project_seats.unbind: bad seat %r" % (seat,))
    at = at or _now()
    with _locked(root):
        d = read(root)
        hit = False
        for proj in d["projects"].values():
            rec = (proj.get("seats") or {}).get(seat)
            if rec and not rec.get("until"):
                rec["until"] = at
                rec["unbound_by"] = by
                hit = True
        if hit:
            _write(d, root)
        return hit


def project_of(seat, root=None):
    for uid, proj in read(root)["projects"].items():
        rec = (proj.get("seats") or {}).get(seat)
        if rec and not rec.get("until"):
            return uid
    return None


def seats_of(uid, root=None, include_past=False):
    proj = read(root)["projects"].get(uid) or {}
    out = []
    for seat, rec in sorted((proj.get("seats") or {}).items()):
        if include_past or not rec.get("until"):
            out.append(dict(rec, seat=seat))
    return out


# ── 遷移:從舊的兩處合併 ────────────────────────────────────────────────────────────────────────
def _old_bindings(root=None):
    """回 [(seat_id, uid, title, source, mtime)]。來源:各艙席位檔(project_uid + seat_id)、seat-continuity/<seat_id>.json。"""
    r = state_root(root)
    out = []
    for f in glob.glob(str(r / "instances" / "*" / "seats" / "*.json")):
        try:
            rec = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or not rec.get("project_uid"):
            continue
        seat = str(rec.get("seat_id") or "")
        if _SEAT_RE.match(seat):
            out.append((seat, str(rec["project_uid"]), str(rec.get("project_title") or ""),
                        "seat:" + os.path.relpath(f, str(r)), os.path.getmtime(f)))
    for f in glob.glob(str(r / "identity" / "seat-continuity" / "*.json")):
        try:
            rec = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        seat = Path(f).stem
        if isinstance(rec, dict) and rec.get("project_uid") and _SEAT_RE.match(seat):
            out.append((seat, str(rec["project_uid"]), str(rec.get("project_title") or ""),
                        "continuity:" + seat, os.path.getmtime(f)))
    return out


def migrate(root=None, apply=False):
    """回 {"bind": [(seat, uid, title, sources)], "conflicts": [(seat, {uid: [sources]})], "skipped": n}。
    同一席碼在舊資料裡綁到不同專案 ⇒ 衝突,只列不挑;正本裡已經有的不動。"""
    by_seat = {}
    for seat, uid, title, src, mt in _old_bindings(root):
        if not _valid(uid, seat):
            continue
        by_seat.setdefault(seat, {}).setdefault(uid, {"title": title, "sources": [], "mtime": 0})
        e = by_seat[seat][uid]
        e["sources"].append(src)
        e["mtime"] = max(e["mtime"], mt)
        e["title"] = e["title"] or title
    cur = read(root)
    plan, conflicts, skipped = [], [], 0
    for seat, uids in sorted(by_seat.items()):
        if any(seat in (p.get("seats") or {}) for p in cur["projects"].values()):
            skipped += 1
            continue
        if len(uids) > 1:
            conflicts.append((seat, {u: e["sources"] for u, e in uids.items()}))
            continue
        uid, e = next(iter(uids.items()))
        plan.append((seat, uid, e["title"], e["sources"]))
    if apply:
        for seat, uid, title, srcs in plan:
            bind(uid, seat, title=title, by="migrate:" + srcs[0].split(":", 1)[0], root=root)
    return {"bind": plan, "conflicts": conflicts, "skipped": skipped}


def codes_of(seat_id, root=None):
    """seat_id → 各艙席位檔裡現在用這個身分的席碼(session_uuid 末五碼,唯一取法 seat_address.uuid_tail5),新的在前。"""
    import seat_address
    hits = []
    base = state_root(root) / "instances" / "*" / "seats"
    # 收掉的席(seat_retire 搬進 seats/retired/)也要算:離艙席最該對帳的就是它們(審查)
    for f in glob.glob(str(base / "*.json")) + glob.glob(str(base / "retired" / "*.json")):
        try:
            rec = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("seat_id") == seat_id:
            c = seat_address.code_of(rec)
            if c:
                hits.append((os.path.getmtime(f), c))
    out = []
    for _m, c in sorted(hits, reverse=True):
        if c not in out:
            out.append(c)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="project_seats.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("--project", default=None); p.add_argument("--all", action="store_true")
    p = sub.add_parser("of"); p.add_argument("seat")
    p = sub.add_parser("migrate"); p.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "of":
        uid = project_of(a.seat)
        print(uid or "")
        return 0 if uid else 1
    if a.cmd == "list":
        d = read()
        for uid, proj in sorted(d["projects"].items()):
            if a.project and uid != a.project:
                continue
            seats = seats_of(uid, include_past=a.all)
            print("%s  %s  %d 席" % (uid, proj.get("title") or "", len(seats)))
            for s in seats:
                codes = codes_of(s["seat"])
                print("  %s  席碼 %s  since %s%s  (%s)" % (s["seat"], ",".join("@" + c for c in codes[:3]) or "-", s.get("since"),
                                                          ("  until " + s["until"]) if s.get("until") else "", s.get("by") or ""))
        return 0
    res = migrate(apply=a.apply)
    print(("已寫入" if a.apply else "試跑(加 --apply 才寫)") + ":綁 %d 席、衝突 %d 席、正本已有 %d 席"
          % (len(res["bind"]), len(res["conflicts"]), res["skipped"]))
    for seat, uid, title, srcs in res["bind"]:
        print("  綁 %s → %s %s(出處 %d:%s)" % (seat, uid, title, len(srcs), srcs[0]))
    for seat, m in res["conflicts"]:
        print("  ⚠ 衝突 %s:%s" % (seat, ";".join("%s ← %s" % (u, ",".join(s[:2])) for u, s in m.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
