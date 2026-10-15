#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""「誰在吃這台機器」:取樣幾秒,列出 CPU 用最多、開最多新程式的前幾名(the maintainer 10-10)。

為什麼要有這支:10-09 Mac 變慢(fseventsd)、10-10 a server 變慢(GridPane 權限修復一檔一個 chmod),
兩次都是臨時拼一長串 ps / top / 核心追蹤才找到兇手。`top` 只看得到「現在活著」的程式,
看不到每秒開了又關的上千個短命程式 —— 而 10-10 那次的兇手正是短命程式(92%)。

量法(每一種都在輸出裡寫明用了哪一種、看不到什麼):
- CPU:取樣開始與結束各讀一次每個行程累計 CPU 秒數,相減 ÷ 取樣秒數 = 平均占幾顆核心。
  Linux 讀 /proc/<pid>/stat;Mac 讀 ps 的 time 欄。取樣期間才出生又死掉的程式看不到。
- 新程式數量:Linux 讀 /proc/stat 的 processes(核心計數,準);Mac 在頭尾各開一個 /usr/bin/true
  看 pid 前進多少(Mac 的 pid 依序發號,扣掉自己開的;遇到 pid 繞回就標量不到)。
- 誰在開短命程式:Linux 而且是 root、核心追蹤(tracefs)可寫 → 錄 sched_process_fork/exec,
  直接數「哪個父程式開了幾個、開的是什麼」,這是準的;其他情況(Mac、非 root)每 0.1 秒讀一次行程表,
  依「父程式 → 子程式」分組,活不到 0.1 秒的會漏,輸出會寫明。
- 自己(取樣程式本身、它的上層與下層)一律不算。

用法:machine_top.py [--seconds 5] [--top 8] [--json] [--no-trace]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proc_find  # noqa: E402

TRACE_ROOTS = ("/sys/kernel/tracing", "/sys/kernel/debug/tracing")


# ---------- CPU 取樣 ----------
def parse_cputime(text: str) -> float:
    """ps 的 time 欄 → 秒。Mac 'mmm:ss.cc'、Linux '[dd-]hh:mm:ss'。"""
    text = (text or "").strip()
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d) if d.isdigit() else 0
    total = 0.0
    for part in text.split(":"):
        try:
            total = total * 60 + float(part)
        except ValueError:
            return 0.0
    return total + days * 86400


def parse_ps_cpu(text: str) -> dict[int, dict]:
    """`ps -Ao pid=,ppid=,time=,comm=` → {pid: {ppid, cpu, comm}}。"""
    out: dict[int, dict] = {}
    for line in (text or "").splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        out[pid] = {"ppid": ppid, "cpu": parse_cputime(parts[2]), "comm": os.path.basename(parts[3])}
    return out


def _proc_snapshot() -> dict[int, dict]:
    tck = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    out: dict[int, dict] = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % name, encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except OSError:
            continue
        l, r = raw.find("("), raw.rfind(")")
        if l < 0 or r < 0:
            continue
        rest = raw[r + 2:].split()
        try:
            out[int(name)] = {"ppid": int(rest[1]), "cpu": (int(rest[11]) + int(rest[12])) / tck,
                              "comm": raw[l + 1:r]}
        except (IndexError, ValueError):
            continue
    return out


def snapshot() -> dict[int, dict]:
    if os.path.isdir("/proc/self") and os.path.exists("/proc/stat"):
        return _proc_snapshot()
    proc = subprocess.run(["ps", "-Ao", "pid=", "-o", "ppid=", "-o", "time=", "-o", "comm="],
                          capture_output=True, text=True, timeout=5)
    return parse_ps_cpu(proc.stdout)


def cpu_top(before: dict[int, dict], after: dict[int, dict], seconds: float, skip: set[int],
            top: int) -> list[dict]:
    rows = []
    for pid, a in after.items():
        if pid in skip:
            continue
        b = before.get(pid)
        used = a["cpu"] - (b["cpu"] if b else 0.0)
        if used <= 0:
            continue
        rows.append({"pid": pid, "comm": a["comm"], "cores": round(used / max(seconds, 0.001), 3)})
    rows.sort(key=lambda r: -r["cores"])
    return rows[:top]


def new_survivors(before: dict[int, dict], after: dict[int, dict], skip: set[int], top: int) -> list[dict]:
    parents = Counter()
    for pid, a in after.items():
        if pid in before or pid in skip:
            continue
        pp = after.get(a["ppid"]) or before.get(a["ppid"]) or {}
        parents[(a["ppid"], pp.get("comm") or "?")] += 1
    return [{"ppid": k[0], "parent": k[1], "count": n} for k, n in parents.most_common(top)]


INTERPRETERS = frozenset({"bash", "sh", "zsh", "dash", "python", "python3", "Python", "node", "perl", "ruby"})


def label_of(command: str) -> str:
    """「bash → bash」看不出是誰;直譯器就補上它跑的腳本名(bash pane-right-tabs.sh)。"""
    toks = (command or "").split()
    if not toks:
        return "?"
    base = os.path.basename(toks[0].strip("()-"))
    if base.rstrip("0123456789.") in INTERPRETERS:
        for tok in toks[1:]:
            if tok.startswith("-"):
                if tok in ("-c", "-m"):
                    return "%s %s" % (base, tok)
                continue
            return "%s %s" % (base, os.path.basename(tok))
    return base


def _ps_light() -> dict[int, tuple[int, str]]:
    proc = subprocess.run(["ps", "-Ao", "pid=", "-o", "ppid=", "-o", "command="],
                          capture_output=True, text=True, timeout=5)
    out: dict[int, tuple[int, str]] = {}
    for line in proc.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            out[int(parts[0])] = (int(parts[1]), label_of(parts[2] if len(parts) == 3 else ""))
    return out


def poll_spawns(seconds: float, interval: float = 0.1, me: int | None = None) -> dict:
    """沒有核心追蹤時的退而求其次(10-10):每 interval 秒讀一次行程表,
    把取樣期間「出現過」的新行程依「父程式 → 子程式」分組。活不到 interval 的仍然看不到。
    自己開的 ps 一律不算(父程式是自己)。回 {"seen":{pid:(ppid,comm)}, "polls":次數}。"""
    me = os.getpid() if me is None else me
    first = _ps_light()
    seen: dict[int, tuple[int, str]] = {}
    names: dict[int, str] = {pid: c for pid, (_, c) in first.items()}
    polls = 1
    end = time.time() + seconds
    while True:
        left = end - time.time()
        if left <= 0:
            break
        time.sleep(min(interval, left))
        snap = _ps_light()
        polls += 1
        for pid, (ppid, comm) in snap.items():
            names.setdefault(pid, comm)
            if pid in first or ppid == me or pid == me:
                continue
            seen.setdefault(pid, (ppid, comm))
    return {"seen": seen, "names": names, "polls": polls}


def group_spawns(seen: dict[int, tuple[int, str]], names: dict[int, str], skip: set[int], top: int) -> list[dict]:
    groups = Counter()
    for pid, (ppid, comm) in seen.items():
        if pid in skip or ppid in skip:
            continue
        groups[(names.get(ppid) or "?", comm)] += 1
    return [{"parent": k[0], "child": k[1], "count": n} for k, n in groups.most_common(top)]


# ---------- 新程式總數 ----------
def _forks_counter() -> int | None:
    try:
        with open("/proc/stat", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("processes "):
                    return int(line.split()[1])
    except (OSError, ValueError):
        return None
    return None


def _probe_pid() -> int:
    p = subprocess.Popen(["/usr/bin/true"])
    p.wait()
    return p.pid


# ---------- 核心追蹤(Linux root) ----------
def trace_dir() -> str | None:
    if not sys.platform.startswith("linux") or os.geteuid() != 0:
        return None
    for root in TRACE_ROOTS:
        if os.path.isdir(os.path.join(root, "events", "sched", "sched_process_exec")) \
                and os.access(os.path.join(root, "instances"), os.W_OK):
            return root
    return None


def parse_trace(text: str, skip: set[int], top: int) -> dict:
    """tracefs 文字 → 誰開了幾個、開的是什麼。"""
    spawners = Counter()
    execs = Counter()
    child_parent: dict[int, str] = {}
    for line in (text or "").splitlines():
        if "sched_process_fork:" in line:
            kv = dict(tok.split("=", 1) for tok in line.split("sched_process_fork:", 1)[1].split() if "=" in tok)
            try:
                ppid = int(kv.get("pid", "0"))
                child = int(kv.get("child_pid", "0"))
            except ValueError:
                continue
            if ppid in skip or child in skip:
                continue
            name = kv.get("comm", "?")
            spawners[name] += 1
            child_parent[child] = name
        elif "sched_process_exec:" in line:
            kv = dict(tok.split("=", 1) for tok in line.split("sched_process_exec:", 1)[1].split() if "=" in tok)
            try:
                pid = int(kv.get("pid", "0"))
            except ValueError:
                continue
            if pid in skip:
                continue
            execs[kv.get("filename", "?")] += 1
    return {"spawners": [{"parent": k, "count": n} for k, n in spawners.most_common(top)],
            "execs": [{"file": k, "count": n} for k, n in execs.most_common(top)],
            "forks": sum(spawners.values()), "exec_total": sum(execs.values())}


def _trace(root: str, seconds: float) -> str:
    inst = os.path.join(root, "instances", "aib_top_%d" % os.getpid())
    os.mkdir(inst)
    try:
        with open(os.path.join(inst, "buffer_size_kb"), "w") as fh:
            fh.write("8192")
        evs = [os.path.join(inst, "events", "sched", e, "enable")
               for e in ("sched_process_fork", "sched_process_exec")]
        for e in evs:
            with open(e, "w") as fh:
                fh.write("1")
        time.sleep(seconds)
        for e in evs:
            with open(e, "w") as fh:
                fh.write("0")
        with open(os.path.join(inst, "trace"), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    finally:
        try:
            os.rmdir(inst)
        except OSError:
            pass


# ---------- 主流程 ----------
def measure(seconds: float = 5.0, top: int = 8, use_trace: bool = True) -> dict:
    me = os.getpid()
    notes: list[str] = []
    forks0 = _forks_counter()
    probe0 = _probe_pid() if forks0 is None else 0
    before = snapshot()
    t0 = time.time()
    troot = trace_dir() if use_trace else None
    traced = None
    polled = None
    if troot:
        try:
            traced = _trace(troot, seconds)
        except OSError as exc:
            notes.append(_ui("aib.top.trace_failed", error=exc))
            polled = poll_spawns(max(0.0, seconds - (time.time() - t0)), me=me)
    else:
        polled = poll_spawns(seconds, me=me)
    after = snapshot()
    elapsed = max(time.time() - t0, 0.001)
    forks1 = _forks_counter()
    probe1 = _probe_pid() if forks0 is None else 0
    try:
        rows = proc_find.read_rows()
        skip = proc_find.own_tree(rows, me)
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        skip = {me}
    if forks0 is not None and forks1 is not None:
        new_rate = round((forks1 - forks0) / elapsed, 1)
        rate_how = _ui("aib.top.how.proc_stat")
    elif probe1 > probe0 > 0:
        # 扣掉自己開的:結尾探針 1 個 + 快速取樣每輪 1 個 ps
        own = 1 + (polled["polls"] - 1 if polled else 0)
        new_rate = round(max(0, probe1 - probe0 - own) / elapsed, 1)
        rate_how = _ui("aib.top.how.pid_advance")
    else:
        new_rate = None
        rate_how = _ui("aib.top.how.rate_unknown")
    res = {
        "seconds": round(elapsed, 2),
        "cpu_top": cpu_top(before, after, elapsed, skip, top),
        "new_per_s": new_rate,
        "new_per_s_how": rate_how,
        "notes": notes,
    }
    if traced is not None:
        res["spawn"] = parse_trace(traced, skip, top)
        res["spawn_how"] = _ui("aib.top.how.trace")
    elif polled is not None:
        res["spawn"] = {"groups": group_spawns(polled["seen"], polled["names"], skip, top),
                        "polls": polled["polls"]}
        res["spawn_how"] = _ui("aib.top.how.poll", polls=polled["polls"])
    else:
        res["spawn"] = {"survivors": new_survivors(before, after, skip, top)}
        res["spawn_how"] = _ui("aib.top.how.survivors")
    return res


def render(res: dict) -> str:
    out = [_ui("aib.top.sampled", seconds="%.1f" % res["seconds"]), "", _ui("aib.top.cpu_head")]
    for r in res["cpu_top"] or []:
        out.append("  %6.2f  %s (pid %d)" % (r["cores"], r["comm"], r["pid"]))
    if not res["cpu_top"]:
        out.append("  " + _ui("aib.top.none"))
    rate = res["new_per_s"]
    out += ["", _ui("aib.top.rate", rate=_ui("aib.top.rate_unknown") if rate is None else rate,
                    how=res["new_per_s_how"]),
            _ui("aib.top.spawn_head", how=res["spawn_how"])]
    sp = res["spawn"]
    if "spawners" in sp:
        for r in sp["spawners"]:
            out.append("  %6d  %s" % (r["count"], r["parent"]))
        out.append(_ui("aib.top.execs_head"))
        for r in sp["execs"]:
            out.append("  %6d  %s" % (r["count"], r["file"]))
    elif "groups" in sp:
        for r in sp["groups"]:
            out.append("  %6d  %s → %s" % (r["count"], r["parent"], r["child"]))
        if not sp["groups"]:
            out.append("  " + _ui("aib.top.no_new_seen"))
    else:
        for r in sp["survivors"]:
            out.append("  %6d  %s (pid %d)" % (r["count"], r["parent"], r["ppid"]))
        if not sp["survivors"]:
            out.append("  " + _ui("aib.top.no_survivors"))
    for n in res.get("notes") or []:
        out.append(_ui("aib.top.note", text=n))
    return "\n".join(out)


_TR = {}


def _ui(key, **params):
    """畫面字走四語鍵;每次照 i18n_lib.resolve_locale() 取,讀不到印鍵名(不擋程式)。同一語系的字典只載一次。"""
    try:
        import i18n_lib
        loc = i18n_lib.resolve_locale()
        tr = _TR.get(loc)
        if tr is None:
            tr = _TR[loc] = i18n_lib.Translator(locale=loc)
        return tr.t(key, params or None)
    except Exception:  # noqa: BLE001
        return key


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=os.environ.get("AIB_TOP_PROG") or "aib top", description=_ui("aib.top.help.desc"))
    ap.add_argument("--seconds", type=float, default=5.0, help=_ui("aib.top.help.seconds"))
    ap.add_argument("--top", type=int, default=8, help=_ui("aib.top.help.top"))
    ap.add_argument("--json", action="store_true", help=_ui("aib.top.help.json"))
    ap.add_argument("--no-trace", action="store_true", help=_ui("aib.top.help.no_trace"))
    args = ap.parse_args(argv)
    if not 0.2 <= args.seconds <= 120:
        print(_ui("aib.top.bad_seconds"), file=sys.stderr)
        return 2
    res = measure(args.seconds, max(1, args.top), use_trace=not args.no_trace)
    print(json.dumps(res, ensure_ascii=False) if args.json else render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
