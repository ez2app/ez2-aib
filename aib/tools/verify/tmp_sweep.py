#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib tmp — clean what aib and its tests left in the temp folder, and nothing else.

    aib tmp                      list what would be removed (default: nothing is deleted)
    aib tmp --apply              remove it
    aib tmp --age-hours 6        only entries untouched for at least this long (default 24)

What can be removed (all four must hold):
  1. it is aib's: inside aib's own temp root (<tmp>/aib-<user>/), or its name starts with a prefix aib and its
     tests use (aib-, aib_, and the fixture prefixes listed in PREFIXES);
  2. it belongs to you (same user id) and is not a symbolic link (links are never followed or removed);
  3. nothing in it was modified within --age-hours;
  4. no running process uses it (working directory, open file or loaded library inside it — /proc on Linux,
     lsof on macOS; if that check cannot run, nothing is removed).
Everything else in the temp folder is left alone, whatever its name.
"""
# 10-08(the maintainer:「要有一個清理 /tmp 的完整方案」)。以前「不准大範圍清 /tmp」:簽章工具(/tmp/code-sign,捷徑)、
#   安全網程式庫曾經放在那裡。這支只認 aib 自己的東西,捷徑一律跳過,有程序在用的一律跳過,檢查不了就一個都不刪。
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src" / "cockpit"))
import i18n_lib  # noqa: E402

LOC = i18n_lib.resolve_locale()
# aib 與它的測試在暫存區用的名稱開頭(10-08 盤點 /tmp:13,236 項幾乎全是這些)。只有開頭對得上才算 aib 的。
PREFIXES = ("aib-", "aib_", "aibt-", "ez2-aib-", "e320-knife-", "p2-knife", "dgrs-note-", "cabin-aq-", "taskzone-", "outline-arm",
            "host-lease-arm", "term-notes-arm", "presence-arm", "known-gaps-arm", "pending-arm", "knife-contract-arm",
            "yield-crown-arm", "hdr-arms", "ai-council-arm", "seat-label-arm", "mark-lint-arm", "fail-open-arm",
            "ship-base-dry")


def T(key, params=None):
    return i18n_lib.t("aib.tmp." + key, params, locale=LOC)


def tmp_roots():
    out = []
    for c in ("/private/tmp", "/tmp", os.environ.get("TMPDIR") or ""):
        if c and os.path.isdir(c):
            r = os.path.realpath(c)
            if r not in out:
                out.append(r)
    return out


def own_root(base):
    return os.path.join(base, "ez2-aib-tmp-%d" % os.getuid())   # 不用 aib-<數字>(那是駕駛艙艙名的格式)


def is_aib_name(name):
    return name.startswith(PREFIXES)


def newest_mtime(path):
    """The latest modification time anywhere under path (not following links)."""
    newest = os.lstat(path).st_mtime
    if os.path.isdir(path) and not os.path.islink(path):
        for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
            for n in dirnames + filenames:
                try:
                    newest = max(newest, os.lstat(os.path.join(dirpath, n)).st_mtime)
                except OSError:
                    continue
    return newest


def size_of(path):
    if not os.path.isdir(path) or os.path.islink(path):
        try:
            return os.lstat(path).st_size
        except OSError:
            return 0
    total = 0
    for dirpath, _d, filenames in os.walk(path, followlinks=False):
        for n in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, n)).st_size
            except OSError:
                continue
    return total


def paths_in_use():
    """Every path a running process has as cwd, open file or mapped library. None if this cannot be checked."""
    used = set()
    if os.path.isdir("/proc/self/fd"):
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            base = "/proc/" + pid
            try:
                used.add(os.readlink(base + "/cwd"))
            except OSError:
                pass
            try:
                for fd in os.listdir(base + "/fd"):
                    try:
                        used.add(os.readlink(base + "/fd/" + fd))
                    except OSError:
                        continue
            except OSError:
                pass
            try:
                with open(base + "/maps", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        parts = line.split(None, 5)
                        if len(parts) == 6 and parts[5].startswith("/"):
                            used.add(parts[5].strip())
            except OSError:
                pass
        return used
    # 10-11:Mac 的 lsof 在 /usr/sbin;排程/開機自動跑的 PATH 常只有 /usr/bin:/bin ⇒ which 找不到就整個不敢刪
    lsof = shutil.which("lsof") or next((p for p in ("/usr/sbin/lsof", "/usr/bin/lsof") if os.access(p, os.X_OK)), None)
    if not lsof:
        return None
    try:
        out = subprocess.run([lsof, "-n", "-P", "-Fn", "-u", str(os.getuid())], capture_output=True, text=True,
                             timeout=60, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return {line[1:] for line in out.splitlines() if line.startswith("n/")}


def candidates(age_hours, now=None, roots=None):
    """[(path, bytes)] that are aib's, yours, not links, and quiet for age_hours. In-use filtering happens in sweep()."""
    now = time.time() if now is None else now
    limit = now - age_hours * 3600
    out = []
    for base in (tmp_roots() if roots is None else roots):
        mine = own_root(base)
        entries = []
        if os.path.isdir(mine) and not os.path.islink(mine):
            entries += [os.path.join(mine, n) for n in os.listdir(mine)]
        try:
            names = os.listdir(base)
        except OSError:
            continue
        entries += [os.path.join(base, n) for n in names if is_aib_name(n) and os.path.join(base, n) != mine]
        for p in entries:
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if os.path.islink(p) or st.st_uid != os.getuid():
                continue
            try:
                if newest_mtime(p) > limit:
                    continue
            except OSError:
                continue
            out.append(p)
    return sorted(set(out))


def sweep(age_hours=24.0, apply=False, roots=None, in_use=None):
    """回 {"removed": [...], "kept_in_use": [...], "bytes": n, "checked": bool}。用中的檢查做不到 ⇒ 一個都不刪。"""
    used = paths_in_use() if in_use is None else in_use
    if used is None:
        return {"removed": [], "kept_in_use": [], "bytes": 0, "checked": False}
    removed, kept, total = [], [], 0
    for p in candidates(age_hours, roots=roots):
        prefix = p.rstrip("/") + "/"
        if any(u == p or u.startswith(prefix) for u in used):
            kept.append(p)
            continue
        n = size_of(p)
        if apply:
            try:
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p)
                else:
                    os.unlink(p)
            except OSError:
                kept.append(p)
                continue
        removed.append(p)
        total += n
    return {"removed": removed, "kept_in_use": kept, "bytes": total, "checked": True}


# 10-08(the maintainer:「徹底查清,不要讓 tmp 問題再發生」):測試留下的不只檔案,還有還在跑的程式(兩組 tmux 駕駛艙跑了 13 小時、
#   吃掉約 8 GB,推前檢查兩次被系統砍)。aib test 已經在每支結束時收;這裡收「不經 aib test 直接跑」或更早留下的。
#   判斷:環境變數裡指向某支測試的暫存資料夾 / HOME / 追蹤檔,而那個路徑已經不在 ⇒ 那支測試早就結束了,它是孤兒。
ORPHAN_VARS = ("AIB_TEST_TMPDIR", "EZ2_VERIFY_TRACE", "HOME")


def _env_of(pid):
    try:
        with open("/proc/%d/environ" % pid, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    out = {}
    for item in raw.split(b"\0"):
        k, sep, v = item.partition(b"=")
        if sep:
            out[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
    return out


def _proc_envs():
    """{pid: 環境變數};Linux 讀 /proc,macOS 讀 ps -E(只看得到自己的)。讀不了 ⇒ None。"""
    me = os.getpid()
    if os.path.isdir("/proc/self"):
        out = {}
        for n in os.listdir("/proc"):
            if n.isdigit() and int(n) != me:
                try:
                    if os.stat("/proc/" + n).st_uid != os.getuid():
                        continue
                except OSError:
                    continue
                env = _env_of(int(n))
                if env is not None:
                    out[int(n)] = env
        return out
    try:
        text = subprocess.run(["ps", "-E", "-ww", "-o", "pid=,command="], capture_output=True, text=True, errors="replace",
                              stdin=subprocess.DEVNULL, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0].isdigit() and int(parts[0]) != me:
            out[int(parts[0])] = dict(x.split("=", 1) for x in parts[1:] if "=" in x)
    return out


def _test_path(var, value, roots):
    """這個環境變數值是不是「某支測試專屬、而且在暫存區裡」的路徑。"""
    if not value or not value.startswith("/"):
        return False
    real = os.path.realpath(os.path.dirname(value)) + "/" + os.path.basename(value)
    if not any(real.startswith(r.rstrip("/") + "/") for r in roots):
        return False
    if var == "HOME":
        return os.path.basename(value).startswith("aib-test-home-")
    if var == "EZ2_VERIFY_TRACE":
        return "/aib-" in real or os.path.basename(value).startswith("trace-")
    return True


def orphans(roots=None, envs=None):
    """[(pid, 原因)]:環境裡指向已經不存在的測試暫存資料夾 / HOME / 追蹤檔的、你自己的程序。查不了 ⇒ None。"""
    roots = tmp_roots() if roots is None else roots
    envs = _proc_envs() if envs is None else envs
    if envs is None:
        return None
    out = []
    for pid, env in sorted(envs.items()):
        for var in ORPHAN_VARS:
            v = env.get(var, "")
            if _test_path(var, v, roots) and not os.path.lexists(v):
                out.append((pid, var))
                break
    return out


def kill_orphans(found, grace_s=2.0):
    import signal
    for pid, _why in found:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline and any(_alive(p) for p, _w in found):
        time.sleep(0.1)
    for pid, _why in found:
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return str(n)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aib tmp", description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--age-hours", type=float, default=24.0)
    a = ap.parse_args(argv)
    found = orphans()
    if found:
        if a.apply:
            kill_orphans(found)
        print(T("orphans_killed" if a.apply else "orphans_found", {"n": len(found)}))
    r = sweep(a.age_hours, apply=a.apply)
    if not r["checked"]:
        print(T("cannot_check"))
        return 3
    key = "removed" if a.apply else "would_remove"
    print(T(key, {"n": len(r["removed"]), "bytes": human(r["bytes"]), "hours": "%g" % a.age_hours}))
    if r["kept_in_use"]:
        print(T("kept_in_use", {"n": len(r["kept_in_use"])}))
    if not a.apply and r["removed"]:
        print(T("hint_apply"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
