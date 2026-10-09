#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""找某 provider 最近可用的 CLI session uuid——**分出新席的來源候選**(不是新席的 uuid)。

★★ 2026-09-24(a seat):本檔原本回的值被 aib 直接當成新席的 uuid 登記(同碼雙活事故,
   docs/DESIGN-seat-fork-unique-0924.md)。現在回的是「要從哪段對話分出」,新席一律新 uuid;
   且**活著被持有的一律跳過**(seat_liveness:跨艙登記簿 + 全機行程表)——活席的對話不是「舊」對話。
   原本第 1 順位就是「本艙活席」,正好是該排除的那一群。

優先序(每一條都先濾掉活持有):
1) 本艙活席 seats/*.json 同 provider（同 role 優先）的 session_uuid
2) 同上的 previous_session_uuid
3) seats/retired/ 同 provider 最近一筆
4) （僅 claude）~/.claude/projects/<cwd編碼>/*.jsonl 最近修改且非空

失敗印空行、rc=0（讓開席退回產新 uuid）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seat_liveness  # noqa: E402

_UUID_RE = __import__("re").compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _ok(u: object) -> str:
    if isinstance(u, str) and _UUID_RE.fullmatch(u.strip()):
        return u.strip()
    return ""


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _live(u: str, procs, aib_home, seats_dir=None) -> bool:
    # 正在讀的這個登記簿一定要在母體裡(不一定位於 aib_home/instances 底下);再加所有艙
    dirs = [str(seats_dir)] if seats_dir and Path(seats_dir).is_dir() else []
    seen = {os.path.realpath(d) for d in dirs}
    dirs += [d for d in seat_liveness.all_seat_dirs(aib_home) if os.path.realpath(d) not in seen]
    return seat_liveness.is_held(seat_liveness.holders(u, seat_dirs=dirs, procs=procs))


def from_seats(seats_dir: Path, provider: str, role: str = "", procs=None, aib_home=None) -> str:
    if not seats_dir.is_dir():
        return ""
    live = sorted(seats_dir.glob("w*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    same_role: list[str] = []
    any_role: list[str] = []
    prevs: list[str] = []
    for p in live:
        d = _load(p)
        if str(d.get("provider") or "") != provider:
            continue
        u = _ok(d.get("session_uuid"))
        pu = _ok(d.get("previous_session_uuid"))
        if u:
            if role and str(d.get("role") or "") == role:
                same_role.append(u)
            else:
                any_role.append(u)
        if pu:
            prevs.append(pu)
    for u in same_role + any_role + prevs:
        if not _live(u, procs, aib_home, seats_dir):
            return u
    retired = seats_dir / "retired"
    if retired.is_dir():
        cands = sorted(retired.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in cands:
            d = _load(p)
            if str(d.get("provider") or "") != provider:
                continue
            u = _ok(d.get("session_uuid")) or _ok(d.get("previous_session_uuid"))
            if u and not _live(u, procs, aib_home, seats_dir):
                return u
    return ""


def _claude_project_dir(cwd: str) -> Path:
    # Claude 把 cwd 編成 -Users-... 這種目錄名
    enc = cwd.replace("/", "-")
    if not enc.startswith("-"):
        enc = "-" + enc.lstrip("-")
    return Path.home() / ".claude" / "projects" / enc


def from_claude_transcripts(cwd: str, procs=None, aib_home=None) -> str:
    proj = _claude_project_dir(cwd)
    if not proj.is_dir():
        return ""
    files = sorted(proj.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        u = _ok(f.stem)
        if not u:
            continue
        try:
            if f.stat().st_size < 200:
                continue
        except OSError:
            continue
        if _live(u, procs, aib_home):
            continue
        return u
    return ""


def resolve(provider: str, seats_dir: str = "", role: str = "", cwd: str = "", aib_home: str | None = None) -> str:
    provider = (provider or "").strip()
    if not provider:
        return ""
    procs = seat_liveness.proc_table()          # 讀一次,每個候選共用
    home = aib_home if aib_home is not None else seat_liveness.default_aib_home()
    u = from_seats(Path(seats_dir), provider, role=role, procs=procs, aib_home=home) if seats_dir else ""
    if u:
        return u
    if provider == "claude" and cwd:
        return from_claude_transcripts(cwd, procs=procs, aib_home=home)
    return ""


def main(argv: list[str]) -> int:
    # usage: last_cli_session.py <provider> [seats_dir] [role] [cwd]
    provider = argv[1] if len(argv) > 1 else ""
    seats_dir = argv[2] if len(argv) > 2 else ""
    role = argv[3] if len(argv) > 3 else ""
    cwd = argv[4] if len(argv) > 4 else ""
    print(resolve(provider, seats_dir=seats_dir, role=role, cwd=cwd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
