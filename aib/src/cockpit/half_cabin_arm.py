#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""半生艙臂(大綱 §88.2 第 6 條,2026-09-11):只讀不殺,逐艙報四個判準與結論.

    python3 src/cockpit/half_cabin_arm.py [--socket-dir D] [--instances-dir D] [--json]

為什麼要有這一支:`20260911-003603-b257` 開到一半(2 格、無 AI 子行程、無外層 session、無 seats 目錄),
與 09-08 11:00 那批死 socket 是同一種失敗重複出現;看板上它們與健康艙同形,直到有人逐個 `list-windows`.
判準來自 `pending/CABIN-INVENTORY-0911.md` 第 12 行量到的四件,逐字:

  1. hub socket 權限:健康艙 `srwxrwx---`(0770);半生/死艙 `srw-rw----`(0660).**只看權限位不看檔型**,
     治具可用 mkfifo 注入;socket 不存在也算 bad(missing).
  2. 外層 session:default socket 上要有 `aib-<id>`(`tmux -S <socket-dir>/default list-sessions`).
  3. `<instances>/<id>/seats/` 目錄存在.
  4. hub 裡有 AI 子行程:`tmux -S <hub socket> list-panes -a -F '#{pane_pid}'` 的任一 pane pid 在 `ps -axo pid,ppid,comm`
     裡有子行程.hub server 打不到(list-panes 非零)⇒ 這一格記 server-dead.

結論:server-dead ⇒ **dead**(socket 檔留著、server 沒了,要 rm 不是 kill);四格全 ok ⇒ **healthy**;其餘 ⇒ **half-born**.
母體 = socket 目錄裡的 `aihub-<id>` ∪ default socket 上的 `aib-<id>` session;instances/ 底下沒有 socket 也沒有 session 的目錄
不在本臂的母體(那是「垃圾目錄稽核」,inventory 疑點 3,另開).

★ tmux 一律用 `-S <路徑>` 不用 `-L`:`-L` 只認固定的 socket 根,`--socket-dir` 就注不進去(治具也就量不到).
★ 全程只讀:不建檔、不殺行程、不動 socket.收艙是 `aib idle` 的事.
rc:0 = 母體全部 healthy / 1 = 有 half-born 或 dead / 2 = socket 目錄不存在(量不到不是「全健康」).
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import i18n_lib  # noqa: E402
import seats_dir_lib  # noqa: E402

LOC = i18n_lib.resolve_locale()
HUB_PREFIX = "aihub-"
OUTER_PREFIX = "aib-"
HEALTHY_MODE = 0o770
OK, BAD = "ok", "bad"
HEALTHY, HALF, DEAD = "healthy", "half-born", "dead"


def T(key, params=None):
    return i18n_lib.t(key, params, locale=LOC)


class InputError(Exception):
    """rc=2:量不到,不是全健康."""


def default_socket_dir() -> Path:
    base = os.environ.get("TMUX_TMPDIR") or "/tmp"
    return Path(os.path.realpath(os.path.join(base, "tmux-%d" % os.getuid())))


def default_instances_dir() -> Path:
    root, _src = seats_dir_lib.state_root()
    return Path(root) / "instances"


def tmux(sock: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux", "-S", str(sock), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        stdin=subprocess.DEVNULL, check=False, timeout=30,
    )


def perm_string(mode: int) -> str:
    return stat.filemode(mode)


def check_socket_perm(sock: Path) -> tuple[str, str]:
    try:
        st = os.lstat(sock)
    except OSError:
        return BAD, "missing"
    bits = stat.S_IMODE(st.st_mode)
    shown = perm_string(st.st_mode)
    return (OK if bits == HEALTHY_MODE else BAD), shown


def outer_sessions(socket_dir: Path) -> list[str] | None:
    proc = tmux(socket_dir / "default", "list-sessions", "-F", "#{session_name}")
    if proc.returncode != 0:
        return None
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def check_outer_session(cabin: str, sessions: list[str] | None) -> tuple[str, str]:
    if sessions is None:
        return BAD, "unmeasurable"
    name = OUTER_PREFIX + cabin
    return (OK if name in sessions else BAD), name


def check_seats_dir(instances: Path, cabin: str) -> tuple[str, str]:
    path = instances / cabin / "seats"
    return (OK if path.is_dir() else BAD), str(path)


def process_children() -> dict[int, list[str]] | None:
    """{ppid: [子行程 comm...]};ps 失敗回 None(量不到)."""
    # ★ 旗標寫成 `-A -o` 不寫 `-axo`:activity_probe 的 CPU 取樣用 `-axo`,而 test-idle-reaper 的
    #   ps 替身把每一次 `-axo` 都算成一次取樣(它釘的是取樣次數);本函式不是取樣,語意同、字面分開.
    proc = subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,comm="],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        stdin=subprocess.DEVNULL, check=False, timeout=30,
    )
    if proc.returncode != 0:
        return None
    kids: dict[int, list[str]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        comm = parts[2] if len(parts) > 2 else ""
        kids.setdefault(ppid, []).append(os.path.basename(comm) if comm else str(pid))
    return kids


def check_ai_child(sock: Path, kids: dict[int, list[str]] | None) -> tuple[str, str]:
    proc = tmux(sock, "list-panes", "-a", "-F", "#{pane_pid}")
    if proc.returncode != 0:
        return BAD, "server-dead"
    pane_pids = []
    for ln in proc.stdout.splitlines():
        ln = ln.strip()
        if ln.isdigit():
            pane_pids.append(int(ln))
    if kids is None:
        return BAD, "ps-unmeasurable"
    found = []
    for pid in pane_pids:
        found.extend(kids.get(pid, []))
    if found:
        return OK, ",".join(sorted(set(found)))
    return BAD, "no-child(panes=%d)" % len(pane_pids)


def conclude(row: dict) -> str:
    if row["ai_child"][1] == "server-dead":
        return DEAD
    if all(row[k][0] == OK for k in ("socket_perm", "outer_session", "seats_dir", "ai_child")):
        return HEALTHY
    return HALF


def cabin_ids(socket_dir: Path, sessions: list[str] | None) -> list[str]:
    ids = set()
    for entry in socket_dir.iterdir():
        if entry.name.startswith(HUB_PREFIX) and len(entry.name) > len(HUB_PREFIX):
            ids.add(entry.name[len(HUB_PREFIX):])
    for name in sessions or []:
        if name.startswith(OUTER_PREFIX) and len(name) > len(OUTER_PREFIX):
            ids.add(name[len(OUTER_PREFIX):])
    return sorted(ids)


def scan(socket_dir: Path, instances: Path) -> list[dict]:
    if not socket_dir.is_dir():
        raise InputError(T("aib.half_cabin.socket_dir_missing", {"path": str(socket_dir)}))
    sessions = outer_sessions(socket_dir)
    kids = process_children()
    rows = []
    for cabin in cabin_ids(socket_dir, sessions):
        sock = socket_dir / (HUB_PREFIX + cabin)
        row = {
            "id": cabin,
            "socket": str(sock),
            "socket_perm": check_socket_perm(sock),
            "outer_session": check_outer_session(cabin, sessions),
            "seats_dir": check_seats_dir(instances, cabin),
            "ai_child": check_ai_child(sock, kids),
        }
        row["verdict"] = conclude(row)
        rows.append(row)
    return rows


def json_rows(rows: list[dict]) -> list[dict]:
    """--json 那個形狀(每艙 id / socket / verdict / criteria{status,detail});給 idle_reaper 等消費者用."""
    return [
        {
            "id": r["id"], "socket": r["socket"], "verdict": r["verdict"],
            "criteria": {k: {"status": r[k][0], "detail": r[k][1]}
                         for k in ("socket_perm", "outer_session", "seats_dir", "ai_child")},
        }
        for r in rows
    ]


def health_by_id(rows: list[dict]) -> dict[str, str]:
    """{艙 id: verdict}.消費者(aib idle scan 的 cabin_health 欄)用;判準全在 scan(),這裡不重寫."""
    return {r["id"]: r["verdict"] for r in rows}


def socket_age_s(sock: Path, now: float | None = None) -> int | None:
    """socket 檔的年齡秒數(mtime 起算);lstat 不到回 None(量不到,不是 0)."""
    try:
        st = os.lstat(sock)
    except OSError:
        return None
    return int((time.time() if now is None else now) - st.st_mtime)


def render_rows(rows: list[dict]) -> str:
    lines = []
    for row in rows:
        lines.append(T("aib.half_cabin.row", {
            "id": row["id"],
            "v1": "%s(%s)" % (row["socket_perm"][0], row["socket_perm"][1]),
            "v2": "%s(%s)" % (row["outer_session"][0], row["outer_session"][1]),
            "v3": row["seats_dir"][0],
            "v4": "%s(%s)" % (row["ai_child"][0], row["ai_child"][1]),
            "status": row["verdict"],
        }))
    counts = {HEALTHY: 0, HALF: 0, DEAD: 0}
    for row in rows:
        counts[row["verdict"]] += 1
    lines.append(T("aib.half_cabin.summary", {
        "count": len(rows), "v1": counts[HEALTHY], "v2": counts[HALF], "v3": counts[DEAD]}))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="half_cabin_arm.py", description=T("aib.half_cabin.help_description"))
    ap.add_argument("--socket-dir", help=T("aib.half_cabin.help_socket_dir"))
    ap.add_argument("--instances-dir", help=T("aib.half_cabin.help_instances_dir"))
    ap.add_argument("--json", action="store_true", help=T("aib.half_cabin.help_json"))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    socket_dir = Path(args.socket_dir) if args.socket_dir else default_socket_dir()
    instances = Path(args.instances_dir) if args.instances_dir else default_instances_dir()
    try:
        rows = scan(socket_dir, instances)
    except InputError as exc:
        sys.stderr.write(T("aib.half_cabin.invalid", {"detail": str(exc)}) + "\n")
        return 2
    if args.json:
        payload = {
            "socket_dir": str(socket_dir), "instances_dir": str(instances),
            "cabins": json_rows(rows),
            "counts": {v: sum(1 for r in rows if r["verdict"] == v) for v in (HEALTHY, HALF, DEAD)},
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(render_rows(rows))
    return 0 if all(r["verdict"] == HEALTHY for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
