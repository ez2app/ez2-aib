#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""用字樣找行程,但不把「自己」算進去(the maintainer 10-10「查程式時排除自己」)。

為什麼要有這支:`pgrep -f 字樣`、`ps | grep 字樣`、`pkill -f 字樣` 會比對整條指令列,
而**查詢指令本身的指令列就含那個字樣**(`bash -c '... pgrep -f "find /var/www ..." ...'`)。
10-10 在 a server 追權限風暴時,ssh 進去那條 shell 被當成「來源」,追了三輪才發現是自己;
同一天稍早截圖縮圖也在自己的畫面上抓到佔位字。`pkill -f` 中了更糟:殺到自己的上層 shell。

規則:
- 排除自己、整條上層(一路到 pid 1)、自己的所有下層。只排 $$ 會漏 tmux 與席位那幾層。
- grep / rg / ugrep / pgrep / pkill 本身不算命中(它們只是在找)。
- 只比對到自己時明講「只比對到自己」,不回「找到 1 個」,也不回「找不到」——這兩種都會把人帶錯方向。
- kill 只殺同一個使用者的行程,而且永遠不殺自己那條樹。

用法:
  proc_find.py find <字樣> [--comm] [--json]   找;--comm 只比對程式名(不看參數)
  proc_find.py kill <字樣> [--comm] [--signal TERM] [--dry-run] [--json]
rc:find 有命中 0、沒有 1;kill 有殺到(或 --dry-run 有對象)0、沒有 1;用法錯 2。
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field

SEARCHERS = frozenset({"grep", "egrep", "fgrep", "rg", "ugrep", "pgrep", "pkill"})


@dataclass
class Proc:
    pid: int
    ppid: int
    uid: int
    command: str
    comm: str = field(default="")


def _comm_of(command: str) -> str:
    head = command.split(" ", 1)[0] if command else ""
    return os.path.basename(head)


def parse_ps(text: str) -> list[Proc]:
    """`ps -Ao pid=,ppid=,uid=,command=` 的輸出 → Proc。壞行略過。"""
    rows: list[Proc] = []
    for line in (text or "").splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 3:
            continue
        try:
            pid, ppid, uid = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        command = parts[3] if len(parts) > 3 else ""
        rows.append(Proc(pid, ppid, uid, command, _comm_of(command)))
    return rows


def read_rows() -> list[Proc]:
    proc = subprocess.run(
        ["ps", "-Ao", "pid=", "-o", "ppid=", "-o", "uid=", "-o", "command="],
        capture_output=True, text=True, timeout=5,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError("ps 失敗 rc=%s" % proc.returncode)
    return parse_ps(proc.stdout)


def own_tree(rows: list[Proc], me: int) -> set[int]:
    """自己 + 整條上層 + 所有下層。"""
    by_pid = {p.pid: p for p in rows}
    out: set[int] = set()
    cur = me
    while cur and cur not in out:
        out.add(cur)
        node = by_pid.get(cur)
        if node is None or node.ppid == cur:
            break
        cur = node.ppid
    kids: dict[int, list[int]] = {}
    for p in rows:
        kids.setdefault(p.ppid, []).append(p.pid)
    stack = [me]
    seen = {me}
    while stack:
        for k in kids.get(stack.pop(), []):
            if k not in seen:
                seen.add(k)
                out.add(k)
                stack.append(k)
    return out


def find(pattern: str, rows: list[Proc] | None = None, me: int | None = None,
         comm_only: bool = False) -> tuple[list[Proc], list[Proc]]:
    """回 (命中, 只是自己那條樹裡比對到的)。"""
    if not pattern:
        return [], []
    rows = read_rows() if rows is None else rows
    me = os.getpid() if me is None else me
    mine = own_tree(rows, me)
    hits: list[Proc] = []
    self_hits: list[Proc] = []
    for p in rows:
        text = p.comm if comm_only else p.command
        if pattern not in text:
            continue
        if p.pid in mine:
            self_hits.append(p)
            continue
        if p.comm in SEARCHERS:
            continue
        hits.append(p)
    return hits, self_hits


def summary(hits: list[Proc], self_hits: list[Proc]) -> str:
    if hits:
        return "找到 %d 個" % len(hits)
    if self_hits:
        return "只比對到自己(查詢指令本身含這個字樣),其他行程沒有"
    return "找不到"


def kill(pattern: str, sig: int = signal.SIGTERM, dry_run: bool = False,
         rows: list[Proc] | None = None, me: int | None = None, comm_only: bool = False,
         uid: int | None = None, killer=os.kill) -> dict:
    hits, self_hits = find(pattern, rows=rows, me=me, comm_only=comm_only)
    uid = os.getuid() if uid is None else uid
    targets = [p for p in hits if p.uid == uid]
    skipped_other_user = [p.pid for p in hits if p.uid != uid]
    killed: list[int] = []
    errors: dict[int, str] = {}
    for p in targets:
        if dry_run:
            continue
        try:
            killer(p.pid, sig)
            killed.append(p.pid)
        except ProcessLookupError:
            errors[p.pid] = "已經不在"
        except OSError as exc:
            errors[p.pid] = str(exc)
    return {
        "pattern": pattern,
        "targets": [p.pid for p in targets],
        "killed": killed,
        "dry_run": dry_run,
        "skipped_self": [p.pid for p in self_hits],
        "skipped_other_user": skipped_other_user,
        "errors": errors,
        "line": summary(hits, self_hits),
    }


def _row(p: Proc) -> dict:
    return {"pid": p.pid, "ppid": p.ppid, "uid": p.uid, "comm": p.comm, "command": p.command[:300]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=os.environ.get("AIB_PROC_PROG") or "aib proc")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("find", "kill"):
        sp = sub.add_parser(name)
        sp.add_argument("pattern")
        sp.add_argument("--comm", action="store_true", help="只比對程式名")
        sp.add_argument("--json", action="store_true")
        if name == "kill":
            sp.add_argument("--signal", default="TERM")
            sp.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help(sys.stderr)
        return 2
    try:
        rows = read_rows()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print("aib proc: 讀不到行程表:%s" % exc, file=sys.stderr)
        return 2
    if args.cmd == "find":
        hits, self_hits = find(args.pattern, rows=rows, comm_only=args.comm)
        if args.json:
            print(json.dumps({"hits": [_row(p) for p in hits],
                              "self_hits": [_row(p) for p in self_hits],
                              "line": summary(hits, self_hits)}, ensure_ascii=False))
        else:
            print(summary(hits, self_hits))
            for p in hits:
                print("  %d (上層 %d) %s" % (p.pid, p.ppid, p.command[:160]))
        return 0 if hits else 1
    name = args.signal.upper()
    sig = getattr(signal, name if name.startswith("SIG") else "SIG" + name, None)
    if not isinstance(sig, signal.Signals):
        print("aib proc: 不認得的訊號 %s" % args.signal, file=sys.stderr)
        return 2
    res = kill(args.pattern, sig=sig, dry_run=args.dry_run, rows=rows, comm_only=args.comm)
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        verb = "會停掉" if args.dry_run else "已停掉"
        print("%s;%s %d 個:%s" % (res["line"], verb, len(res["targets"] if args.dry_run else res["killed"]),
                                  " ".join(map(str, res["targets"] if args.dry_run else res["killed"]))))
    return 0 if (res["targets"] if args.dry_run else res["killed"]) else 1


if __name__ == "__main__":
    sys.exit(main())
