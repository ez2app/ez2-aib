#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib ship watch:開發時在背景先跑可能受影響的測試(10-06,開發檔 §5.2)。

★ 為什麼不在你的工作目錄跑:aib test 跑完會「把測試改過的被追蹤檔還原」(防測試弄髒樹);你正在改的檔會被當成
  測試弄髒的而還原 —— 等於蓋掉你的編輯。所以一律在**鏡像樹**跑:~/.ez2-aib/watch/<倉識別>/tree
  (同一個倉的 git worktree,分離 HEAD),每次跑之前把你工作目錄的內容(HEAD + 改過的 + 新增的 − 刪掉的)抄過去。
  你的工作目錄只讀,不寫。
★ 跑出來的紀錄進同一個紀錄池,鑰匙是「讀到的檔的內容」,不綁目錄 ⇒ 之後 aib ship package 時內容沒再變就沿用。
★ 跑的就是推前檢查(pushgate,--fail-fast、1 份,不搶你的 CPU);機器忙(1 分鐘負載超過核心數)就先不跑。
★ 別的專案用 pip install -e 的:測試永遠載入你的工作目錄,鏡像樹測不到鏡像裡的程式 ⇒ 推前檢查的探針會擋下(沒驗),
  這裡照實說「這個專案不能背景預跑」,不會給你假的綠。

用法:aib ship watch [--interval 5] [--quiet 10] [--once]
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def git(repo, *a, check=True):
    p = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if check and p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(a), p.stderr.strip()))
    return p.stdout


def _lines0(out):
    return [x for x in out.split("\0") if x]


def working_state(repo):
    """(HEAD, {改過或新增的路徑: 內容雜湊}, {刪掉的路徑})。只看被追蹤的與沒被忽略的新檔。"""
    head = git(repo, "rev-parse", "HEAD").strip()
    changed = set(_lines0(git(repo, "diff", "--name-only", "-z", "HEAD")))
    changed |= set(_lines0(git(repo, "ls-files", "--others", "--exclude-standard", "-z")))
    files, gone = {}, set()
    for rel in sorted(changed):
        p = Path(repo) / rel
        if p.is_file() or p.is_symlink():
            try:
                data = os.readlink(p).encode() if p.is_symlink() else p.read_bytes()
            except OSError:
                continue
            files[rel] = hashlib.sha256(data).hexdigest()
        elif not p.exists():
            gone.add(rel)
    return head, files, gone


def signature(state):
    head, files, gone = state
    h = hashlib.sha256(head.encode())
    for rel in sorted(files):
        h.update(("\0f%s\0%s" % (rel, files[rel])).encode())
    for rel in sorted(gone):
        h.update(("\0d%s" % rel).encode())
    return h.hexdigest()


def mirror_dir(repo, home=None):
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import reuse_index
    rid = reuse_index.repo_identity(str(repo)) or hashlib.sha256(str(Path(repo).resolve()).encode()).hexdigest()[:16]
    return Path(home or Path.home()) / ".ez2-aib" / "watch" / rid / "tree"


def sync_mirror(repo, mirror, state):
    """鏡像樹 = 你的工作目錄的內容。只寫鏡像樹,不寫你的工作目錄。"""
    head, files, gone = state
    mirror = Path(mirror)
    if not (mirror / ".git").exists():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        git(repo, "worktree", "add", "-f", "-q", "--detach", str(mirror), head)
    git(mirror, "checkout", "-q", "-f", "--detach", head)
    git(mirror, "clean", "-fdq")
    for rel in files:
        src, dst = Path(repo) / rel, mirror / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_symlink() or dst.exists():
            dst.unlink()
        if src.is_symlink():
            os.symlink(os.readlink(src), dst)
        else:
            shutil.copy2(src, dst)
    for rel in gone:
        p = mirror / rel
        if p.is_file() or p.is_symlink():
            p.unlink()


def busy(max_load=None):
    try:
        return os.getloadavg()[0] > (max_load or os.cpu_count() or 1)
    except OSError:
        return False


def run_gate(mirror, mode):
    cmd = [sys.executable, str(ROOT / "tools" / "verify" / "pushgate.py"), "--repo", str(mirror), "--jobs", "1",
           "--fail-fast"] + (["--mode", "generic"] if mode != "aib" else [])
    return subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)


def foreign(mirror):
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import pushgate
    return pushgate.probe_foreign(mirror)


def once(repo, mode, home=None, gate=None, say=print, T=None, probe=None):
    """跑一次:同步鏡像樹 → (別的專案先探:測試載入的是不是鏡像裡的程式)→ 推前檢查(背景那種)。
    回推前檢查的結束碼;忙 ⇒ None;這個專案不能背景預跑 ⇒ 4(沒驗)。"""
    T = T or (lambda k, p=None: k)
    if busy():
        say(T("watch_busy"))
        return None
    state = working_state(repo)
    mirror = mirror_dir(repo, home)
    sync_mirror(repo, mirror, state)
    if mode != "aib" and (probe or foreign)(mirror):
        say(T("watch_not_here"))
        return 4
    p = (gate or run_gate)(mirror, mode)
    for ln in [ln for ln in (p.stdout or "").splitlines() if ln.strip()][-3:]:
        say("  " + ln)
    return p.returncode


class Settle:
    """「內容停下來 quiet 秒才跑一輪;跑過的同一份內容不再跑」的判斷(抽出來好測;loop 每 interval 秒問一次)。"""

    def __init__(self, quiet):
        self.quiet = quiet
        self.seen_sig, self.seen_at, self.done_sig = None, 0.0, None

    def due(self, sig, now):
        if sig != self.seen_sig:                  # 內容又變了 ⇒ 重新起算
            self.seen_sig, self.seen_at = sig, now
            return False
        return sig != self.done_sig and now - self.seen_at >= self.quiet

    def done(self, sig):
        self.done_sig = sig


def loop(repo, mode, interval=5.0, quiet=10.0, home=None, T=None, say=print, clock=time.monotonic, sleep=time.sleep,
         run=None, rounds=None):
    """存檔停下來 quiet 秒才跑;跑完同一個內容不再跑;機器忙(once 回 None)就下一次再試。Ctrl-C 結束。
    rounds:只問幾次就結束(測試用);run:替換 once(測試用)。"""
    T = T or (lambda k, p=None: k)
    run = run or (lambda: once(repo, mode, home=home, T=T, say=say))
    st = Settle(quiet)
    say(T("watch_start", {"path": str(mirror_dir(repo, home))}))
    n = 0
    while rounds is None or n < rounds:
        n += 1
        sig = signature(working_state(repo))
        if st.due(sig, clock()):
            say(T("watch_run", {"when": time.strftime("%H:%M:%S")}))
            if run() is not None:
                st.done(sig)
        sleep(interval)
