#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""席位身分查詢:這個 session 當次綁定成功的席位碼(取代舊載體的 session registry)。

the maintainer 2026-09-26 R10:「身份驗證 只要綁定席位成功,就用當次席位碼為準」。
計畫:舊載體全面退役計畫(09-26)§0-1。消費端:ez2-ai 的 session authority(G2 另一半)。

    seat_authority.py query --session-uuid U [--aib-home H]
    ⇒ stdout 一行 JSON,rc 恆 0(用法錯才非 0):
       {"source": "aib-seat-binding/v1", "status": "bound|unbound|not_current|no_seat|ambiguous",
        "code", "session_uuid", "seat_id", "persona_uid", "route_key",
        "bound_at_ms", "continuity_sha256", "seat_file", "seat_dirs_source"}

判準(只有 status=bound 才算有身分,其他一律沒有 —— fail-closed):
  1. 找得到 session_uuid 等於 U 的席位檔。母體**用位置圈**:<home>/seats/w*.json 與 <home>/instances/*/seats/w*.json
     (不含 retired/)。pending/、logs/、memory/ 裡的備份不算 —— 備份席位檔不得撤銷活席的身分(a seat 09-26 審查一般 1)。
     同一個 uuid 在母體裡出現多份 ⇒ status=ambiguous(狀態不一致,要人修),和 no_seat(這個 session 沒有席)分開。
  2. 該席的延續檔 identity/seat-continuity/<seat_id>.json 存在,且裡面的 session_uuid 就是 U(不是前一位)
  3. 延續檔有 persona_uid(人解綁過 = 空 ⇒ unbound)
席位碼 = uuid 尾五碼(與 seat_address.code_of、dm 位址同一套);**只有 bound 才回 code**(09-28 舊欄名已依 B15 退役改名),其餘回空字串,
免得消費端只讀 code 拿到一個不是身分的值。本檔唯讀,不寫任何東西。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cockpit"))
import seats_dir_lib  # noqa: E402  席位目錄單源(HUB 導出、艙衝突判定)

SOURCE = "aib-seat-binding/v1"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SEAT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def aib_home(explicit: str = "") -> Path:
    return Path(explicit or os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib"))


def tail5(uuid: str) -> str:
    return (uuid or "").replace("-", "")[-5:]


def _load(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def seat_dirs(home: Path, environ=None):
    """(要掃的 seats 目錄清單, 來源, 衝突理由)。規則交給單源 seats_dir_lib,這裡不另寫一份:
    - **有艙證據**(HUB / EZ2_AIB_INSTANCE_HOME):只掃那一艙(cabin_seats_dir_with_source);
      兩個來源指向不同艙 ⇒ 回衝突,呼叫端 fail-closed,不挑邊、不退主艙。
    - **沒有艙證據**:ez2-ai 呼叫本檔時子行程只給 PATH/HOME/USER(trustedVerificationEnv),
      本來就問不出艙 ⇒ 用 session_uuid 掃所有艙(<home>/seats + <home>/instances/*/seats)。
      這不是「退回主艙」:同 uuid 多份回 ambiguous;殘檔還要過延續檔 session_uuid 核對(not_current)。
    狀態根一律用本次的 home(--aib-home),不讓環境裡的 EZ2_AIB_STATE_ROOT 把 HUB 導到別的根。"""
    env = dict(os.environ if environ is None else environ)
    env["EZ2_AIB_STATE_ROOT"] = str(home)
    cabin, source, conflict = seats_dir_lib.cabin_seats_dir_with_source(env)
    if conflict:
        return [], source, conflict
    if cabin:
        return [cabin], source, None
    return ([str(home / "seats")] + sorted(glob.glob(str(home / "instances" / "*" / "seats"))),
            "all-cabins:no-cabin-evidence", None)


def seat_files(home: Path, environ=None) -> list[str]:
    """現役席位檔母體(retired/ 在更深一層,自然不含)。目錄怎麼選見 seat_dirs。"""
    dirs, _src, conflict = seat_dirs(home, environ)
    if conflict:
        return []
    return sorted(p for d in dirs for p in glob.glob(os.path.join(d, "w*.json")))


def find_seat(home: Path, session_uuid: str, environ=None):
    """現役席位檔裡 session_uuid 等於 U 的那一份;一份都沒有回 None;多份回 ("ambiguous", hits);
    艙來源衝突回 ("conflict", 理由)。"""
    _dirs, _src, conflict = seat_dirs(home, environ)
    if conflict:
        return ("conflict", conflict)
    hits = []
    for p in seat_files(home, environ):
        rec = _load(p)
        if isinstance(rec, dict) and rec.get("session_uuid") == session_uuid:
            hits.append((p, rec))
    return hits[0] if len(hits) == 1 else (None if not hits else ("ambiguous", hits))


def query(session_uuid: str, home: Path, environ=None) -> dict:
    out = {"source": SOURCE, "status": "no_seat", "code": "", "session_uuid": session_uuid,
           "seat_id": "", "persona_uid": "", "route_key": "", "bound_at_ms": None,
           "continuity_sha256": "", "seat_file": ""}
    _dirs, dirs_src, _conflict = seat_dirs(home, environ)
    out["seat_dirs_source"] = dirs_src
    hit = find_seat(home, session_uuid, environ)
    if hit is None:
        return out
    if hit[0] == "conflict":
        out["status"] = "ambiguous"
        out["reason"] = "艙來源衝突,不挑邊:%s" % hit[1]
        return out
    if hit[0] == "ambiguous":
        out["status"] = "ambiguous"
        out["reason"] = "同一個 session_uuid 出現在 %d 份現役席位檔,狀態不一致,不挑" % len(hit[1])
        return out
    seat_file, rec = hit
    seat_id = str(rec.get("seat_id") or "")
    out.update(seat_file=seat_file, seat_id=seat_id)
    if not SEAT_ID_RE.fullmatch(seat_id):
        out["status"] = "unbound"
        out["reason"] = "席位檔沒有合法 seat_id"
        return out
    cpath = home / "identity" / "seat-continuity" / ("%s.json" % seat_id)
    try:
        raw = cpath.read_bytes()
        cont = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        out["status"] = "unbound"
        out["reason"] = "沒有延續檔(這席從沒綁定成功過)"
        return out
    out["continuity_sha256"] = hashlib.sha256(raw).hexdigest()
    if cont.get("session_uuid") != session_uuid:
        out["status"] = "not_current"
        out["reason"] = "延續檔記的是另一個 session(%s),不是當次" % tail5(str(cont.get("session_uuid") or ""))
        return out
    if not cont.get("persona_uid"):
        out["status"] = "unbound"
        out["reason"] = "延續檔最後狀態是未綁人格"
        return out
    out.update(status="bound", code=tail5(session_uuid), persona_uid=str(cont["persona_uid"]),
               route_key=str(cont.get("brain_route_key") or ""),
               bound_at_ms=cont.get("updated_wall_ms"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="seat_authority.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("query")
    q.add_argument("--session-uuid", required=True)
    q.add_argument("--aib-home", default="")
    a = ap.parse_args(argv)
    if not UUID_RE.fullmatch(a.session_uuid):
        print(json.dumps({"source": SOURCE, "status": "no_seat", "reason": "bad_session_uuid",
                          "session_uuid": a.session_uuid}, ensure_ascii=False))
        return 0
    print(json.dumps(query(a.session_uuid, aib_home(a.aib_home)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
