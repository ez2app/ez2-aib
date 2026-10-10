#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""一段 CLI 對話(session uuid)現在被誰「活著持有」——跨艙、兩個來源。

2026-09-24(a seat,事故見 docs/DESIGN-seat-fork-unique-0924.md):
同一段 claude 對話被 6 個窗用過、2 個活行程同時開著(d6a8/@4 與 98fe/@6)。
舊守衛 seat_resume_guard.owner_of 有兩個洞:只掃**本艙**登記簿;只看 pid 活不活。
本檔是「持有」的唯一判準,守衛、開席取碼、巡查都呼叫這裡,不各抄一份。

## 兩個來源(分開回報,不合併成一個數)

  registry  各艙 seats/w*.json:session_uuid 相同、無 exit_reason、pid 活著
  process   全機行程表:CLI 行程的命令列**持有**這顆 uuid(aib 外手開的
            `claude --resume X` 不會登記,只看登記簿會漏)

## 「命令列持有」的判準(一個 fork 的 argv 裡也有舊 uuid)

  持有:`--session-id X`、`--session-id=X`、`--resume X`/`-r X`/`--resume=X` 且同一條命令列
        **不帶** `--fork-session`、codex 的 `resume` 之後第一個 8-4-4-4-12 uuid
        (AIB 開的 codex 席在 resume 與 uuid 中間塞旗標;緊接的 token 不是 uuid)
  不持有:`--resume X --fork-session`(那是從 X 分出的新席,持有的是它自己的 --session-id)
  只認 CLI 行程(argv[0] 檔名是 claude/grok/codex):`grep -- --resume X` 這類觀測者
  的命令列長得跟被觀測物一樣,不能算進來。

## registry 來源的保守方向

  pid 活著、子樹裡有 CLI 持有 X                ⇒ 持有
  pid 活著、子樹裡的 CLI 明確持有**別的** uuid  ⇒ 不持有(席位檔過期或 pid 已換人)
  pid 活著、子樹裡沒有任何 CLI                  ⇒ **持有**(開席中那幾秒 CLI 還沒起;
      pid 被無關行程重用也落在這裡——寧可多擋一次,真要過用 FORCE,不可放行撞號)
  ★ 唯一例外(10-09 a seat,停電/重開機復原):席位檔的開席時間**早於這次開機** ⇒ 不持有。
      任何行程都活不過重開機,那個 pid 現在一定是別人。這不是猜,是開機時間這個硬事實;
      不加這條,重開機後 pid 被重用的席會被當成「還有人」,同碼復活被擋,只能 FORCE。
      量不到開機時間 ⇒ 不套這條(退回上面的保守規則)。行程表是注入的(尺)⇒ 也不讀真機開機時間。
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seats_dir_lib  # noqa: E402  狀態根與本艙 seats 的單一來源(先看 HUB)

CLI_NAMES = ("claude", "grok", "codex")
_RESUME_FLAGS = ("--resume", "-r")
_UUID_LENS = (8, 4, 4, 4, 12)


def _looks_like_uuid(tok: str) -> bool:
    """8-4-4-4-12 十六進位。resume 後面的旗標與 --model 的值都不是這個形狀。"""
    if not tok or "-" not in tok:
        return False
    parts = tok.split("-")
    if tuple(len(p) for p in parts) != _UUID_LENS:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in tok if c != "-")


def default_aib_home() -> str:
    return seats_dir_lib.state_root()[0]


def all_seat_dirs(aib_home: str) -> list[str]:
    """所有艙的活席登記簿(不含 retired/):本艙(單一來源導出)+ 舊主目錄 <根>/seats + 每個 instances/*/seats。

    只收落在 aib_home 底下的(尺注入假家目錄時,真機本艙不會混進來)。
    舊主目錄那一格是 dm-ctl 跨艙跳轉本來就掃的;漏掉它,舊艙的活席就看不到。
    """
    if not aib_home:
        return []
    root = os.path.realpath(aib_home)
    cands = [seats_dir_lib.seats_dir(), os.path.join(aib_home, "seats")]
    cands += sorted(glob.glob(os.path.join(aib_home, "instances", "*", "seats")))
    out, seen = [], set()
    for d in cands:
        r = os.path.realpath(d)
        if r in seen or not os.path.isdir(r) or not (r == root or r.startswith(root + os.sep)):
            continue
        seen.add(r)
        out.append(d)
    return out


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (TypeError, ValueError, ProcessLookupError, OverflowError):
        return False
    except PermissionError:
        return True
    return int(pid) > 0


def boot_epoch() -> float | None:
    """本機這次開機的 epoch 秒;量不到回 None。macOS:sysctl kern.boottime;Linux:/proc/stat btime。"""
    try:
        out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True,
                             timeout=5, stdin=subprocess.DEVNULL).stdout
        if "sec =" in out:
            return float(out.split("sec =", 1)[1].split(",", 1)[0].strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    try:
        with open("/proc/stat", encoding="utf-8") as fh:
            for ln in fh:
                if ln.startswith("btime "):
                    return float(ln.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


#: 早於這個時刻(2001-09-09)的開席時間不是真的量到的(治具的 1000、壞檔的 0),當成量不到,不套開機例外。
PLAUSIBLE_EPOCH = 1_000_000_000


def started_before_boot(seat: dict, boot) -> bool:
    """席位檔記的開席時間早於這次開機 ⇒ True(那一席不可能還活著)。量不到任一邊 ⇒ False。
    ★ 10-09 守門紅(test-dm-sender-identity-0927):治具寫 started_epoch=1000 + 自己的活 pid 當「活席」,
      第一版直接比開機時間把它判死。開席時間不像真的 ⇒ 量不到 ⇒ 退回檔頭的保守規則。"""
    if not boot:
        return False
    t = seat.get("started_epoch") or seat.get("born_at")
    try:
        t = float(t)
    except (TypeError, ValueError):
        return False
    return PLAUSIBLE_EPOCH <= t < float(boot)


def proc_table() -> list[tuple[int, int, str]]:
    """(pid, ppid, 命令列)。讀不到回 [](缺資料 ⇒ 只剩 registry 來源,且 registry 走保守方向)。"""
    try:
        out = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,command="],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    return rows


def _is_cli(cmd: str) -> bool:
    toks = cmd.split()
    return bool(toks) and os.path.basename(toks[0]) in CLI_NAMES


def held_uuids(cmd: str) -> set[str]:
    """一條 CLI 命令列持有哪些 session uuid(見檔頭判準)。非 CLI ⇒ 空集合。"""
    if not _is_cli(cmd):
        return set()
    toks = cmd.split()
    fork = "--fork-session" in toks
    held: set[str] = set()
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if t == "--session-id" and nxt:
            held.add(nxt)
        elif t.startswith("--session-id="):
            held.add(t.split("=", 1)[1])
        elif t in _RESUME_FLAGS and nxt and not fork:
            held.add(nxt)
        elif t.startswith("--resume=") and not fork:
            held.add(t.split("=", 1)[1])
        elif t == "resume" and os.path.basename(toks[0]) == "codex":
            for later in toks[i + 1:]:
                if _looks_like_uuid(later):
                    held.add(later)
                    break
    return held


def _subtree(pid: int, procs) -> list[tuple[int, int, str]]:
    kids: dict[int, list] = {}
    for row in procs:
        kids.setdefault(row[1], []).append(row)
    out, todo = [], [pid]
    me = {r[0]: r for r in procs}
    if pid in me:
        out.append(me[pid])
    seen = {pid}
    while todo:
        p = todo.pop()
        for row in kids.get(p, []):
            if row[0] not in seen:
                seen.add(row[0])
                out.append(row)
                todo.append(row[0])
    return out


def _load(p: str) -> dict:
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def registry_holders(uuid: str, seat_dirs, procs, boot=None) -> list[dict]:
    out = []
    for sd in seat_dirs:
        for f in sorted(glob.glob(os.path.join(sd, "w*.json"))):
            s = _load(f)
            if s.get("session_uuid") != uuid or s.get("exit_reason"):
                continue
            pid = s.get("pid")
            if not _pid_alive(pid):
                continue
            if started_before_boot(s, boot):
                continue            # 開席早於這次開機 ⇒ pid 已是別人(檔頭「唯一例外」)
            clis = [r for r in _subtree(int(pid), procs) if _is_cli(r[2])]
            held = set().union(*(held_uuids(r[2]) for r in clis)) if clis else set()
            if clis and uuid not in held and held:
                continue            # 子樹的 CLI 明確在跑別段對話 ⇒ 席位檔過期
            out.append({"source": "registry", "seat_file": f,
                        "cabin": s.get("cabin_id") or os.path.basename(os.path.dirname(sd)),
                        "window": s.get("window_id"), "provider": s.get("provider"),
                        "pid": int(pid), "confirmed": uuid in held})
    return out


def process_holders(uuid: str, procs) -> list[dict]:
    me = os.getpid()
    return [{"source": "process", "pid": pid, "ppid": ppid, "command": cmd[:200]}
            for pid, ppid, cmd in procs if pid != me and uuid in held_uuids(cmd)]


def holders(uuid: str, aib_home: str | None = None, seat_dirs=None, procs=None, boot=None) -> dict:
    """{"registry": [...], "process": [...]}。uuid 空 ⇒ 兩個都空。

    seat_dirs 沒給 ⇒ aib_home 底下所有艙;aib_home 也沒給 ⇒ 預設家目錄。
    procs 沒給 ⇒ 現讀 ps。(兩者都可注入,尺不讀真機)
    """
    if not uuid:
        return {"registry": [], "process": []}
    if procs is None:
        procs = proc_table()
        if boot is None:
            boot = boot_epoch()      # 只在讀真機行程表時才讀真機開機時間(注入的尺不混真機)
    if seat_dirs is None:
        seat_dirs = all_seat_dirs(aib_home if aib_home is not None else default_aib_home())
    reg = registry_holders(uuid, seat_dirs, procs, boot)
    proc = process_holders(uuid, procs)
    parent = {r[0]: r[1] for r in procs}
    reg_pids = {r["pid"] for r in reg}
    for p in proc:            # 往上追祖先:是某個登記席子樹裡的行程 ⇒ 標它屬於哪一席
        cur, hops = p["pid"], 0
        while cur and hops < 64 and cur not in reg_pids:
            cur, hops = parent.get(cur, 0), hops + 1
        p["registered_under"] = cur if cur in reg_pids else None
    return {"registry": reg, "process": proc}


def is_held(h: dict) -> bool:
    return bool(h.get("registry") or h.get("process"))


def describe(h: dict) -> str:
    """一行人話;空字串 = 沒有持有者。"""
    bits = ["%s/%s(%s,pid %s)" % (str(r.get("cabin") or "")[-4:], r.get("window"), r.get("provider"), r.get("pid"))
            for r in h.get("registry", [])]
    bits += ["行程 pid %s(未登記)" % p["pid"] for p in h.get("process", [])
             if not p.get("registered_under")]
    return "、".join(bits)


def tail5(uuid: str) -> str:
    return (uuid or "").replace("-", "")[-5:]


def tail5_taken(code: str, aib_home: str | None = None, seat_dirs=None, exclude_uuid: str = "") -> list[str]:
    """哪些**別的** uuid(含 retired)已用掉這個五碼尾。dm 位址是五碼,不同 uuid 同尾也會撞。"""
    if seat_dirs is None:
        seat_dirs = all_seat_dirs(aib_home if aib_home is not None else default_aib_home())
    found = set()
    for sd in seat_dirs:
        for f in glob.glob(os.path.join(sd, "w*.json")) + glob.glob(os.path.join(sd, "retired", "*.json")):
            u = _load(f).get("session_uuid") or ""
            if len(u) == 36 and tail5(u) == code and u != exclude_uuid:
                found.add(u)
    return sorted(found)


def successors(code: str, aib_home: str | None = None, seat_dirs=None, procs=None, depth: int = 5) -> list[str]:
    """舊碼已不在時,誰接續它:席位檔 forked_from 指向它、而且**活著**的席的五碼。

    後繼也死了就沿血緣往下(最多 depth 代)。dm 轉信用:恰好一個才轉,多個不猜。
    舊碼本身還活著就不該走到這裡(dm-ctl 先跨艙找活席,找不到才問後繼)。
    """
    code = (code or "").lstrip("@")
    if not code:
        return []
    if seat_dirs is None:
        seat_dirs = all_seat_dirs(aib_home if aib_home is not None else default_aib_home())
    if procs is None:
        procs = proc_table()
    children: dict[str, list[str]] = {}
    for sd in seat_dirs:
        for f in glob.glob(os.path.join(sd, "w*.json")) + glob.glob(os.path.join(sd, "retired", "*.json")):
            s = _load(f)
            u, parent = s.get("session_uuid") or "", s.get("forked_from") or ""
            if len(u) == 36 and len(parent) == 36 and u not in children.setdefault(tail5(parent), []):
                children[tail5(parent)].append(u)
    found: list[str] = []
    frontier, seen = [code], {code}
    for _ in range(depth):
        nxt = []
        for c in frontier:
            for u in children.get(c, []):
                t = tail5(u)
                if t in seen:
                    continue
                seen.add(t)
                if registry_holders(u, seat_dirs, procs):
                    found.append(t)
                else:
                    nxt.append(t)
        frontier = nxt
        if not frontier:
            break
    return sorted(set(found))


def duplicates(aib_home: str | None = None, seat_dirs=None, procs=None) -> list[dict]:
    """全機巡查(L4):三種發現,來源分開報,不合併成一個數。

      registry  同一段對話在登記簿上有 ≥2 個活持有(可能跨艙)
      process   同一段對話有 ≥2 個 CLI 行程持有(同一席的子孫行程只算一個;含 aib 外手開的)
      tail5     不同 uuid 撞同一個五碼尾(dm 位址是五碼;含 retired)
    """
    if seat_dirs is None:
        seat_dirs = all_seat_dirs(aib_home if aib_home is not None else default_aib_home())
    if procs is None:
        procs = proc_table()
    out: list[dict] = []
    uuids: set[str] = set()
    by_tail: dict[str, set[str]] = {}
    for sd in seat_dirs:
        for f in glob.glob(os.path.join(sd, "w*.json")):
            u = _load(f).get("session_uuid") or ""
            if len(u) == 36:
                uuids.add(u)
        for f in glob.glob(os.path.join(sd, "w*.json")) + glob.glob(os.path.join(sd, "retired", "*.json")):
            u = _load(f).get("session_uuid") or ""
            if len(u) == 36:
                by_tail.setdefault(tail5(u), set()).add(u)
    for u in sorted(uuids):
        reg = registry_holders(u, seat_dirs, procs)
        if len({r["pid"] for r in reg}) >= 2:
            out.append({"kind": "registry", "uuid": u, "code": tail5(u),
                        "describe": describe({"registry": reg, "process": []})})
    parent = {r[0]: r[1] for r in procs}
    held: dict[str, list[int]] = {}
    for pid, _pp, cmd in procs:
        for u in held_uuids(cmd):
            held.setdefault(u, []).append(pid)
    for u, pids in sorted(held.items()):
        roots = []
        for p in pids:
            cur, hops, nested = parent.get(p, 0), 0, False
            while cur and hops < 64:
                if cur in pids:
                    nested = True
                    break
                cur, hops = parent.get(cur, 0), hops + 1
            if not nested:
                roots.append(p)
        if len(roots) >= 2:
            out.append({"kind": "process", "uuid": u, "code": tail5(u), "pids": sorted(roots)})
    for t, us in sorted(by_tail.items()):
        if len(us) >= 2:
            out.append({"kind": "tail5", "code": t, "uuids": sorted(us)})
    return out


def main(argv: list[str]) -> int:
    # seat_liveness.py holders <uuid>      ⇒ JSON;rc 0 沒人持有 / 3 有人持有
    # seat_liveness.py tail5 <uuid>        ⇒ 撞尾的別顆 uuid 一行一顆;rc 0 沒撞 / 3 撞
    # seat_liveness.py successors <五碼>   ⇒ 活著的後繼五碼一行一個;rc 0 恰一個 / 3 沒有 / 4 多個
    # seat_liveness.py dups                ⇒ 全機巡查 JSON;rc 0 沒發現 / 3 有發現(L4)
    if len(argv) == 2 and argv[1] == "dups":
        found = duplicates()
        print(json.dumps(found, ensure_ascii=False))
        return 3 if found else 0
    if len(argv) != 3 or argv[1] not in ("holders", "tail5", "successors"):
        print("用法:seat_liveness.py holders|tail5 <uuid> | successors <五碼> | dups", file=sys.stderr)
        return 2
    if argv[1] == "successors":
        found = successors(argv[2])
        print("\n".join(found))
        return 0 if len(found) == 1 else (3 if not found else 4)
    if argv[1] == "holders":
        h = holders(argv[2])
        print(json.dumps(dict(h, describe=describe(h)), ensure_ascii=False))
        return 3 if is_held(h) else 0
    taken = tail5_taken(tail5(argv[2]), exclude_uuid=argv[2])
    print("\n".join(taken))
    return 3 if taken else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
