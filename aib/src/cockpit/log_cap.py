#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""<狀態根>/logs 底下的紀錄檔設上限(the maintainer 10-10「紀錄檔清理」)。

為什麼要有這支:AIB 的紀錄檔各寫各的(每支都自己 open(...,"a")),除了 claim-gate-runs.jsonl
自己有輪替以外,沒有一支有上限。10-10 量到 ~/.ez2-aib/logs 已 15MB,idle-reaper.jsonl 每輪
每個略過的目標都寫一行,只會一直長。a server 上的 ACB 監控紀錄同一天量到 105MB、沒人在清。

做法(開艙時背景跑一次,也可以手動):
- 只看 logs/ 這一層的 *.log 與 *.jsonl;已經是封存的(.gz、.1 …)不碰。
- 超過上限(預設 8MB)的檔:改名再壓成 <檔名>.1.gz,舊的 .1.gz → .2.gz …,保留最近 keep 份(預設 3),更舊的刪掉。
  寫入端都是「開檔、附加、關檔」,改名之後下一次寫入會自己開一個新檔,不會寫進封存。
- claim-gate-runs.jsonl 跳過:它在 src/claimtruth/claim-gate-bash.py 有自己的輪替(上限+保留期+鎖),兩套不能同時管。
- 同一時間只有一個在跑(logs/.log-cap.lock),搶不到就跳過。

用法:
  log_cap.py sweep [--max-mb 8] [--keep 3] [--dry-run] [--json] [--force]
    不在真的座艙裡(沒有 EZ2_AIB_INSTANCE 或艙資料夾不在)時不動手,除非 --force。
  log_cap.py status [--json]   列出各檔大小與封存份數。
"""
from __future__ import annotations

import argparse
import fcntl
import gzip
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seats_dir_lib  # noqa: E402

DEFAULT_MAX_MB = 8
DEFAULT_KEEP = 3
OWN_ROTATION = ("claim-gate-runs.jsonl",)   # 有自己輪替的,不重複管
LIVE_RE = re.compile(r"^[^/]+\.(log|jsonl)$")


def logs_dir(environ=None) -> str:
    return os.path.join(seats_dir_lib.state_root(environ)[0], "logs")


def live_cabin(environ=None, home=None) -> bool:
    env = os.environ if environ is None else environ
    inst = (env.get("EZ2_AIB_INSTANCE") or "").strip()
    if not inst or "/" in inst:
        return False
    home = home or os.path.expanduser("~")
    return os.path.isdir(os.path.join(home, ".ez2-aib", "instances", inst))


def candidates(d: str) -> list[str]:
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out = []
    for n in names:
        if not LIVE_RE.match(n) or n in OWN_ROTATION:
            continue
        p = os.path.join(d, n)
        if os.path.isfile(p) and not os.path.islink(p):
            out.append(p)
    return out


def archives(path: str) -> list[str]:
    d, base = os.path.split(path)
    pat = re.compile(r"^%s\.(\d+)\.gz$" % re.escape(base))
    found = []
    try:
        for n in os.listdir(d):
            m = pat.match(n)
            if m:
                found.append((int(m.group(1)), os.path.join(d, n)))
    except OSError:
        pass
    return [p for _, p in sorted(found)]


def cap_one(path: str, max_bytes: int, keep: int, dry_run: bool = False) -> dict:
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return {"file": os.path.basename(path), "action": "error", "why": str(exc)}
    rec = {"file": os.path.basename(path), "bytes": size}
    if size <= max_bytes:
        rec["action"] = "ok"
        return rec
    rec["action"] = "would_rotate" if dry_run else "rotated"
    if dry_run:
        return rec
    # 先把舊封存往後推一號,超過 keep 的刪掉
    for n in range(keep, 0, -1):
        src = "%s.%d.gz" % (path, n)
        if not os.path.exists(src):
            continue
        if n >= keep:
            os.remove(src)
        else:
            os.replace(src, "%s.%d.gz" % (path, n + 1))
    staged = "%s.rotating.%d" % (path, os.getpid())
    os.replace(path, staged)          # 改名後寫入端下一次會開新檔
    with open(staged, "rb") as src_fh, gzip.open(path + ".1.gz", "wb") as dst:
        shutil.copyfileobj(src_fh, dst)
    os.remove(staged)
    rec["archive"] = os.path.basename(path) + ".1.gz"
    return rec


def sweep(d: str | None = None, max_bytes: int = DEFAULT_MAX_MB << 20, keep: int = DEFAULT_KEEP,
          dry_run: bool = False) -> dict:
    d = d or logs_dir()
    if not os.path.isdir(d):
        return {"dir": d, "files": [], "skipped": "no_dir"}
    lock_path = os.path.join(d, ".log-cap.lock")
    try:
        lock = open(lock_path, "a")
    except OSError as exc:
        return {"dir": d, "files": [], "skipped": "lock_open_failed: %s" % exc}
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"dir": d, "files": [], "skipped": "busy"}
        files = []
        for p in candidates(d):
            try:
                files.append(cap_one(p, max_bytes, max(1, keep), dry_run=dry_run))
            except OSError as exc:
                files.append({"file": os.path.basename(p), "action": "error", "why": str(exc)})
        return {"dir": d, "files": files, "max_bytes": max_bytes, "keep": keep, "dry_run": dry_run}
    finally:
        lock.close()


def status(d: str | None = None) -> dict:
    d = d or logs_dir()
    rows = []
    for p in candidates(d):
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        rows.append({"file": os.path.basename(p), "bytes": size, "archives": len(archives(p))})
    rows.sort(key=lambda r: -r["bytes"])
    return {"dir": d, "files": rows, "own_rotation": list(OWN_ROTATION)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=os.environ.get("AIB_LOGCAP_PROG") or "aib logcap")
    sub = ap.add_subparsers(dest="cmd")
    sw = sub.add_parser("sweep")
    sw.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB)
    sw.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    sw.add_argument("--dry-run", action="store_true")
    sw.add_argument("--json", action="store_true")
    sw.add_argument("--force", action="store_true", help="不在座艙裡也動手")
    st = sub.add_parser("status")
    st.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "status":
        res = status()
        if args.json:
            print(json.dumps(res, ensure_ascii=False))
        else:
            for r in res["files"]:
                print("%8.1f MB  %s%s" % (r["bytes"] / 1048576, r["file"],
                                          "(封存 %d 份)" % r["archives"] if r["archives"] else ""))
        return 0
    if args.cmd != "sweep":
        ap.print_help(sys.stderr)
        return 2
    if not args.force and not live_cabin():
        res = {"skipped": "not_live_cabin", "files": []}
    else:
        res = sweep(max_bytes=int(args.max_mb * 1048576), keep=args.keep, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        moved = [f for f in res.get("files", []) if f.get("action") in ("rotated", "would_rotate")]
        if res.get("skipped"):
            print("沒動手:%s" % res["skipped"])
        elif not moved:
            print("都在上限內,沒動")
        for f in moved:
            print("%s %s(%.1f MB)" % ("會封存" if args.dry_run else "已封存", f["file"], f["bytes"] / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())
