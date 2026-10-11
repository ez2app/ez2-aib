#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""防「互推」:同一個動作對同一個目標,短時間內做太多次就先停手(the maintainer 10-10)。

為什麼要有這支:10-10 在 a server 量到,GridPane 的權限自我修復把 5.6 萬個檔改回 644,
我們的守衛又改回 664,兩邊各自都「做對了」,合起來卻永遠來回,吃掉整台機器 92% 的開程式量。
AIB 自己也有會反覆動手的常駐程式(Codex 額度換號、降溫旗標)。它們各自每次判斷都合理,
沒人記得「我十分鐘前才對同一個目標做過一樣的事」。

用法(呼叫端):
    if flap_guard.held("codex-rotate", window_id, limit=3, window_s=1800):
        ...先停手,回報...
    else:
        ...照做...
        flap_guard.record("codex-rotate", window_id)

- 紀錄在 <狀態根>/flap/<動作>.jsonl(狀態根 = seats_dir_lib.state_root:
  EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib),一行一次動作。
- 第一次停手時寫一行到 <狀態根>/logs/flap-guard.jsonl,之後同一段時間不重複寫(停手本身不能變成洗版)。
- 檔案超過 64KB 時,只保留最近 24 小時。
- 讀寫失敗一律當「沒有紀錄」:防互推是保險,不能反過來擋住正常工作。

CLI:flap_guard.py status [--json]   列出最近 24 小時各動作、各目標的次數與停手紀錄。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seats_dir_lib  # noqa: E402

KEEP_S = 24 * 3600
PRUNE_BYTES = 64 * 1024


def _root(environ=None) -> str:
    return seats_dir_lib.state_root(environ)[0]


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(name or "unknown"))[:80] or "unknown"


def _path(action: str, environ=None) -> str:
    return os.path.join(_root(environ), "flap", _safe(action) + ".jsonl")


def _read(path: str) -> list[dict]:
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        pass
    return out


def count(action: str, target: str, window_s: float, now: float | None = None,
          environ=None) -> int:
    now = time.time() if now is None else now
    since = now - window_s
    return sum(1 for r in _read(_path(action, environ))
               if r.get("target") == str(target) and r.get("kind") != "hold"
               and float(r.get("ts") or 0) >= since)


def _append(path: str, rec: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _prune(path: str, now: float) -> None:
    try:
        if os.path.getsize(path) <= PRUNE_BYTES:
            return
    except OSError:
        return
    keep = [r for r in _read(path) if float(r.get("ts") or 0) >= now - KEEP_S]
    tmp = path + ".tmp.%d" % os.getpid()
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            for r in keep:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def record(action: str, target: str, note: str = "", now: float | None = None,
           environ=None) -> None:
    now = time.time() if now is None else now
    path = _path(action, environ)
    _append(path, {"ts": round(now, 3), "target": str(target), "note": note[:200]})
    _prune(path, now)


def held(action: str, target: str, limit: int, window_s: float, now: float | None = None,
         environ=None, why: str = "") -> bool:
    """這段時間內已做滿 limit 次 → True(該停手)。第一次停手寫一行停手紀錄。"""
    now = time.time() if now is None else now
    n = count(action, target, window_s, now=now, environ=environ)
    if n < limit:
        return False
    path = _path(action, environ)
    already = any(r.get("kind") == "hold" and r.get("target") == str(target)
                  and float(r.get("ts") or 0) >= now - window_s for r in _read(path))
    if not already:
        rec = {"ts": round(now, 3), "kind": "hold", "target": str(target),
               "count": n, "limit": limit, "window_s": window_s, "why": why[:200]}
        _append(path, rec)
        _append(os.path.join(_root(environ), "logs", "flap-guard.jsonl"), dict(rec, action=action))
    return True


def status(now: float | None = None, environ=None) -> dict:
    now = time.time() if now is None else now
    base = os.path.join(_root(environ), "flap")
    out: dict = {}
    try:
        names = sorted(os.listdir(base))
    except OSError:
        names = []
    for name in names:
        if not name.endswith(".jsonl"):
            continue
        per: dict = {}
        for r in _read(os.path.join(base, name)):
            if float(r.get("ts") or 0) < now - KEEP_S:
                continue
            t = per.setdefault(str(r.get("target")), {"count": 0, "holds": 0})
            t["holds" if r.get("kind") == "hold" else "count"] += 1
        out[name[:-len(".jsonl")]] = per
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] != "status":
        print("用法:flap_guard.py status [--json]", file=sys.stderr)
        return 2
    st = status()
    if "--json" in argv:
        print(json.dumps(st, ensure_ascii=False))
        return 0
    if not st:
        print("最近 24 小時沒有紀錄")
    for action, per in st.items():
        for target, v in per.items():
            print("%s %s:%d 次%s" % (action, target, v["count"], ",停手 %d 次" % v["holds"] if v["holds"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
