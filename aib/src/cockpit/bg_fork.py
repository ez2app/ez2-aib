#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Claude Code「背景分身」(bg fork)的辨認 —— 單一真相源(the maintainer 10-10 17:0x 裁,a seat)。

## 這是什麼(10-10 16:3x 真機量到的,不是推的)

一席在對話裡把一句話交給 Claude Code 的**背景工作**跑,Claude Code 會把那一席的對話
fork 成一個新 session,掛在全機共用的守護程式底下,行程長這樣:

    claude daemon run --origin transient --spawned-by {"pid": <第一個用背景工作的席>}
      └ ClaudeCode.app/…/claude --bg-pty-host /tmp/cc-daemon-…/pty/<分身>.sock … --
          └ …/versions/2.1.296 --session-id <分身 uuid> --fork-session \
                --resume ~/.claude/projects/<專案>/<原席 uuid>.jsonl --reply-on-resume …

    ~/.claude/sessions/<pid>.json 寫 kind=bg;~/.claude/daemon.log 記「bg spawned <分身> (slash)」。

★ 守護程式是**第一個**用背景工作的席生的、之後全機共用 ⇒ 任何席的分身,行程祖先鏈都爬到
  那一席(10-10 真機:44ed3 的分身被 pane_pid_chain 記成 a seat)。
★ 分身不在任何 pane 裡、席位檔不該回寫它的 uuid、席碼不變(查證結論,the maintainer 10-10 17:0x 裁)。

## 本檔只回答兩個問題

1. 一條命令列是不是分身(`fork_of`:回 (分身 uuid, 原席 uuid)),或是不是承載分身的
   守護程式/宿主(`is_bg_host`)—— 祖先鏈走到這兩種行程就該**停**,不准再往上認席。
2. 全機現在有哪些分身、各從哪一席分出來(`forks_by_origin`:原席 uuid → [分身 uuid…])
   —— 活動偵測把分身的對話檔 mtime 併入原席(閒置收艙/活動燈/CTX 同一條)。

## 判準(只認命令列,不讀 sessions/<pid>.json:那個檔沒有「從誰分出來」)

- 分身:同一條命令列同時有 `--fork-session`、`--session-id <uuid>`、`--resume <X>`,
  且 argv[0] 的路徑含 `claude`(`~/.local/bin/claude` 與 `…/share/claude/versions/N` 都是);
  `<X>` 是 uuid 或以 `<uuid>.jsonl` 結尾的路徑。少任何一樣 ⇒ 不是分身(不猜)。
  ★ 觀測者(`grep -- --fork-session`、`ps`)的命令列長得像被觀測物:argv[0] 不含 claude ⇒ 排除。
- 宿主:命令列含 `--bg-pty-host`,或 argv[0] 含 `claude` 且前兩個引數是 `daemon run`。

## 拆除縫

`proc_rows()` 讀 `EZ2_AIB_PS_SNAPSHOT`(每行 `pid ppid command`)代替真 `ps`,臂不必真的
生出一棵行程樹;設了但檔讀不到 ⇒ 回空(等於「量不到」,不退真 ps——測試裡退真 ps 會把
真機的分身混進斷言)。`forks_by_origin(rows=None)` 現查時在行程內快取 `CACHE_S` 秒
(右欄每輪對每一席都問;一輪一次 ps 就夠)。
"""
from __future__ import annotations

import os
import re
import subprocess
import time

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_JSONL_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")
SNAPSHOT_ENV = "EZ2_AIB_PS_SNAPSHOT"
CACHE_S = 5.0
_RESUME_FLAGS = ("--resume", "-r")

_cache: dict = {"at": 0.0, "map": None}


def _is_claude_argv0(cmd: str) -> bool:
    toks = cmd.split()
    return bool(toks) and "claude" in toks[0]


def _origin_from_resume(value: str):
    """`--resume` 的值 → 原席 uuid;uuid 本身或 `<uuid>.jsonl` 結尾的路徑,其他回 None。"""
    if not value:
        return None
    if UUID_RE.match(value):
        return value
    m = _JSONL_RE.search(value)
    return m.group(1) if m else None


def fork_of(cmd: str):
    """命令列是分身 ⇒ (分身 uuid, 原席 uuid);不是 ⇒ None。"""
    if not isinstance(cmd, str) or "--fork-session" not in cmd or not _is_claude_argv0(cmd):
        return None
    toks = cmd.split()
    if "--fork-session" not in toks or "--bg-pty-host" in toks:
        return None               # 宿主的命令列在 `--` 後面帶著整條分身 argv:它是宿主,不是分身
    sid = origin = None
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if t == "--session-id" and UUID_RE.match(nxt):
            sid = nxt
        elif t.startswith("--session-id=") and UUID_RE.match(t.split("=", 1)[1]):
            sid = t.split("=", 1)[1]
        elif t in _RESUME_FLAGS:
            origin = _origin_from_resume(nxt)
        elif t.startswith("--resume="):
            origin = _origin_from_resume(t.split("=", 1)[1])
    if sid and origin and sid != origin:
        return sid, origin
    return None


def is_bg_host(cmd: str) -> bool:
    """承載分身的宿主/守護程式:祖先鏈走到這裡就停。"""
    if not isinstance(cmd, str):
        return False
    toks = cmd.split()
    if "--bg-pty-host" in toks:
        return True
    return len(toks) >= 3 and "claude" in toks[0] and toks[1] == "daemon" and toks[2] == "run"


def proc_rows(snapshot_path=None):
    """[(pid, ppid, command)]:有快照檔就讀快照,否則 `ps -axo pid=,ppid=,command=`。量不到回空。"""
    path = snapshot_path if snapshot_path is not None else os.environ.get(SNAPSHOT_ENV)
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            return []
    else:
        try:
            out = subprocess.run(["ps", "-axo", "pid=,ppid=,command="],
                                 stdin=subprocess.DEVNULL, capture_output=True,
                                 text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return []
        if out.returncode:
            return []
        text = out.stdout
    rows = []
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return rows


def rows_from_procs(procs):
    """呼叫端已經有行程表就別再 `ps` 一次(idle_reaper 的 collect_procs 字典、或 (pid, ppid, cmd) 列表)
    → 本檔的 rows。認不出形狀的項目跳過,不猜。"""
    rows = []
    items = procs.values() if isinstance(procs, dict) else (procs or ())
    for it in items:
        try:
            if isinstance(it, dict):
                rows.append((int(it["pid"]), int(it.get("ppid") or 0), str(it.get("args") or it.get("cmd") or "")))
            else:
                pid, ppid, cmd = it
                rows.append((int(pid), int(ppid), str(cmd)))
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def forks_by_origin(rows=None, now=None):
    """原席 uuid → [分身 uuid, …](順序 = 行程表順序)。`rows=None` 現查並快取 CACHE_S 秒。"""
    if rows is None:
        t = time.time() if now is None else now
        if _cache["map"] is not None and (t - _cache["at"]) < CACHE_S:
            return _cache["map"]
        rows = proc_rows()
        result = _build(rows)
        _cache["at"], _cache["map"] = t, result
        return result
    return _build(rows)


def _build(rows):
    out: dict = {}
    for _pid, _ppid, cmd in rows:
        fk = fork_of(cmd)
        if fk:
            sid, origin = fk
            lst = out.setdefault(origin, [])
            if sid not in lst:
                lst.append(sid)
    return out


def walk_to_fork(pid, read_ppid, read_cmd, max_depth=12):
    """從 pid 往祖先走:先碰到分身 ⇒ (分身 uuid, 原席 uuid);先碰到宿主/守護程式或走完 ⇒ None。
    走到宿主就停是刻意的:再往上是「第一個用背景工作的席」,不是寄件人。"""
    seen = set()
    cur = pid
    for _ in range(max_depth):
        try:
            cur_pid = int(cur)
        except (TypeError, ValueError):
            return None
        if cur_pid <= 1 or cur_pid in seen:
            return None
        seen.add(cur_pid)
        cmd = read_cmd(cur_pid) or ""
        if is_bg_host(cmd):
            return None               # 先認宿主:宿主底下不是分身本人的行程,不借分身的名字
        fk = fork_of(cmd)
        if fk:
            return fk
        cur = read_ppid(cur_pid)
    return None


if __name__ == "__main__":
    import json
    import sys
    # 人工查:`bg_fork.py` 印現在全機的分身對映;`bg_fork.py <pid>` 印那個 pid 鏈上的分身。
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        import sender_origin as _so
        print(json.dumps(walk_to_fork(int(sys.argv[1]), _so._read_ppid, _so._read_cmd)))
    else:
        print(json.dumps(forks_by_origin(), ensure_ascii=False, indent=1))
