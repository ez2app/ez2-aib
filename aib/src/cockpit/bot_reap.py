#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib bot reap —— 收掉「行程早就死了、status.json 還寫著活著」的橋接席(10-07 各 bot 比對,root a seat)。

真機 10-07:3 隻 grokbot 卡在 closing(18 小時、2 天),1 隻 codexbot 卡在 connected,pid 都不在。
它們會被 bot 狀態表算成活席,右欄(改之前)也不會亮。這支只做一件事:pid 確定不在的,把 status.json 標成
state=stopped、health=down、last_error_kind=dead,記下 reaped_epoch。不刪任何檔、不碰信箱、不碰鎖檔。

    aib bot reap            只列出會收哪幾隻(預設,不改)
    aib bot reap --apply    真的改
pid 欄沒有或量不到(權限)的不算死,不碰。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def state_root() -> Path:
    for key in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        v = os.environ.get(key)
        if v:
            return Path(v).expanduser()
    return Path.home() / ".ez2-aib"


def _dead(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def find_dead(base: Path | None = None) -> list[tuple[Path, dict]]:
    out = []
    for st in sorted((base or state_root()).glob("bridge/*/status.json")):
        try:
            d = json.loads(st.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("state") != "stopped" and _dead(d.get("pid")):
            out.append((st, d))
    return out


def reap(st: Path, d: dict, now: float | None = None) -> None:
    d = dict(d)
    d.update({"state": "stopped", "health": "down", "last_error_kind": "dead",
              "last_error": "行程 %s 已不在,由 aib bot reap 收掉(原狀態 %s)" % (d.get("pid"), d.get("state")),
              "reaped_epoch": int(now or time.time())})
    tmp = st.with_name("status.json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(st)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="aib bot reap", description="收掉行程已死、狀態還寫活著的橋接席(只改 status.json)")
    ap.add_argument("--apply", action="store_true", help="真的改(預設只列出)")
    a = ap.parse_args(argv)
    dead = find_dead()
    if not dead:
        print("沒有要收的:每一隻沒停的橋,行程都還在(或量不到)。")
        return 0
    now = time.time()
    for st, d in dead:
        age = int((now - st.stat().st_mtime) // 3600)
        print("%s %s @%s 狀態=%s pid=%s 已死(狀態檔 %d 小時沒動)" % (
            "收掉" if a.apply else "會收", d.get("provider"), d.get("code") or st.parent.name[:8], d.get("state"),
            d.get("pid"), age))
        if a.apply:
            reap(st, d, now)
    if not a.apply:
        print("以上 %d 隻只是列出,沒改;要改請加 --apply(只改 status.json,不刪檔)" % len(dead))
    return 0


if __name__ == "__main__":
    sys.exit(main())
