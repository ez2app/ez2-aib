#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""席位 pane 實際跑的指令列裡的 session uuid(工單 AS,2026-09-02)。

## 為什麼要有它

09-02 12:12 艦長 c8dea 對死格下了一條裸 `tmux respawn-pane -t %14`:tmux 拿 pane
原本的啟動指令重跑,整條不經 `aib`,席位檔的 pid 停在死值、dm 拒投 stale。
修法(艦長裁 (ii))是讓啟動指令**自己**登記——而登記要寫 session_uuid 時,
唯一可信的來源是「這個 pane 此刻真的在跑什麼」,不是舊席位檔(HANDOFF 未結案 3b:
窗號重用會給格式正確的錯答案),也不是呼叫端自報的值。

本檔只做**證據**,一個位元組都不寫:回 pane 行程樹裡的 session uuid 與它出自
哪一行指令列。寫入端仍是 `aib` 的 `write_seat_record` 一支。

## 取法(順位)

1. pane 的子孫行程 argv 裡的 `--session-id <uuid>`(claude / grok CLI 真正在跑的那個)。
2. pane 自己的指令列(`bash -c '<LAUNCH_CMD>'`)裡 `provider-launch.sh <p> <m> <r> <cwd> <uuid>`
   的第 5 參——CLI 還沒起來(自登記跑在 provider 之前)時只有這一格。
   ★ 只認**位置**不認「任何 uuid 形狀的字」:cwd 常常自己就含一個 uuid
   (scratchpad 路徑),抓第一個 uuid 會抓到目錄名。

兩格都沒有 ⇒ 「量不到」(rc 3),呼叫端要把它寫成 pending,不得沿用舊值。
呼叫端帶 `--expect U` 時,量到的值 ≠ U ⇒ rc 4:自報與實跑不符,拒絕。

純函式 `scan(rows, root_pid)` 不碰 `ps`,治具直接餵行。
"""

import argparse
import json
import re
import subprocess
import sys

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
UUID_RE = re.compile(r"^%s$" % UUID)
# `--session-id U` / `--session-id=U`;前後要是邊界,免得 `--session-id-foo` 混進來。
SESSION_ID_RE = re.compile(r"(?:^|\s)--session-id(?:=|\s+)(%s)(?=\s|$)" % UUID)
# 啟動列外層現在是 provider-run-relogin.sh，參數序與 provider-launch.sh 相同：
# 前四參 provider mode role cwd(cwd 經 %q,空白會變 `\ `)，第 5 參才是 uuid。
# 兩種檔名都認，免得包裝那層還沒叫進原啟動程式時，重生讀不到編號。
_TOK = r"(?:\\ |\S)+"
LAUNCH_RE = re.compile(
    r"(?:provider-launch|provider-run-relogin)\.sh\s+\S+\s+\S+\s+\S+\s+%s\s+(%s)(?=\s|$)"
    % (_TOK, UUID))

RC_FOUND = 0
RC_BAD_ARGS = 2
RC_NOT_FOUND = 3
RC_MISMATCH = 4


def ps_rows():
    """[(pid, ppid, command)] 全機一次;`-ww` 不截斷指令列。"""
    out = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,command="],
                         capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        rows.append((pid, ppid, parts[2] if len(parts) > 2 else ""))
    return rows


def tree(rows, root_pid):
    """root 與其全部子孫,BFS 序(root 在第 0 位)。"""
    kids = {}
    for pid, ppid, _ in rows:
        kids.setdefault(ppid, []).append(pid)
    order, todo, seen = [], [root_pid], set()
    while todo:
        pid = todo.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        order.append(pid)
        todo.extend(kids.get(pid, []))
    return order


def scan(rows, root_pid):
    """回 {"uuid","source","pid"} 或 None。順位見檔頭。"""
    cmd = {pid: command for pid, _, command in rows}
    members = tree(rows, root_pid)
    for pid in members[1:]:
        m = SESSION_ID_RE.search(cmd.get(pid, ""))
        if m:
            return {"uuid": m.group(1), "source": "descendant-argv:--session-id",
                    "pid": pid}
    m = LAUNCH_RE.search(cmd.get(root_pid, ""))
    if m:
        return {"uuid": m.group(1), "source": "pane-cmdline:provider-launch-arg5",
                "pid": root_pid}
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pane-pid", type=int, required=True)
    ap.add_argument("--expect", default="",
                    help="呼叫端自報的 uuid;量到的值不等於它就 rc 4")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.pane_pid <= 0:
        print("seat_launch_evidence: pane pid 要是正整數", file=sys.stderr)
        return RC_BAD_ARGS
    if args.expect and not UUID_RE.match(args.expect):
        print("seat_launch_evidence: --expect 不是 uuid:%s" % args.expect,
              file=sys.stderr)
        return RC_BAD_ARGS
    hit = scan(ps_rows(), args.pane_pid)
    if hit is None:
        print("seat_launch_evidence: pane %d 的行程樹裡量不到 session uuid"
              % args.pane_pid, file=sys.stderr)
        return RC_NOT_FOUND
    if args.expect and hit["uuid"] != args.expect:
        print("seat_launch_evidence: 自報 %s ≠ 實跑 %s(%s pid %d)"
              % (args.expect, hit["uuid"], hit["source"], hit["pid"]),
              file=sys.stderr)
        return RC_MISMATCH
    if args.json:
        print(json.dumps(hit, ensure_ascii=False))
    else:
        print(hit["uuid"])
    return RC_FOUND


if __name__ == "__main__":
    sys.exit(main())
