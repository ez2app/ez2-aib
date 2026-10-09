#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""`aib ff <sha>` —— 主樹閘:只在正本樹、只快轉、只艦長(大綱 §88.2 第 3 條,the maintainer 09-11 15:0x 建議採納)。

## 病
09-10 艦長兩次在**工作樹**裡 `git push`/ff main,rc=0 而 origin 一個位元組沒動(記憶
push-from-worktree-is-a-silent-noop);09-08 一顆紅被推上 main,推的人不是艦長。
「非艦長席不得 commit/ff main,席位只交 sha」寫在規矩裡,但沒有任何機器在執行。

## 這支做什麼(fail-closed,每個 rc 都印得出理由)
  aib ff <sha> [--repo DIR] [--dry-run] [--no-fetch]
  1. 正本樹:`git worktree list` 第一列才是正本;在別棵樹跑 → rc=2,印正本樹路徑。
  2. 正本樹要在 main、要乾淨;sha 要解得開 → 否則 rc=2。
  3. 快轉:sha 必須含 main 現在的 HEAD → 否則 rc=1(不 merge、不 rebase、不動)。
  4. 席位:呼叫者的窗(`$TMUX_PANE` → window_id)對席位檔的 role;不在
     `EZ2_AIB_CAPTAIN_ROLES`(預設 root)→ rc=3「席位只交 sha」。
     ★ 量不到身份(沒有 TMUX_PANE / 沒有席位檔)→ **不擋**但印一行 unmeasured
     (閘只在量到的證據上限制;量不到一律放行但說得出來 —— 記憶 a-gate-that-disables-its-own-repair-path)。
     `AIB_FF_FORCE=1` 逃生門:非艦長仍快轉,但必印理由行(同發版逃生列規矩)。
  5. 做:`git merge --ff-only <sha>`;然後印**遠端/本地/領先/落後**四個數(fetch 失敗也印,標 unfetched)。
最後一行永遠是一行 JSON:{"rc","reason","tree","role","head","sha","remote","local","ahead","behind"}。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import i18n_lib  # noqa: E402

LOC = os.environ.get("EZ2_AIB_LOCALE", "zh-TW")
DEFAULT_CAPTAIN_ROLES = "root"


def T(key, params=None):
    return i18n_lib.t(key, params, locale=LOC)


def _git(repo, *args, timeout=60):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       timeout=timeout, stdin=subprocess.DEVNULL)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def canonical_tree(repo):
    """正本樹 = `git worktree list --porcelain` 的第一列;解不出回 None。"""
    rc, out, _ = _git(repo, "worktree", "list", "--porcelain")
    if rc != 0:
        return None
    for line in out.splitlines():
        if line.startswith("worktree "):
            return pathlib.Path(line[len("worktree "):]).resolve()
    return None


def is_canonical(repo):
    rc, top, _ = _git(repo, "rev-parse", "--show-toplevel")
    if rc != 0:
        return False, None, None
    top = pathlib.Path(top).resolve()
    canon = canonical_tree(repo)
    return (canon is not None and top == canon), top, canon


def caller_window():
    """呼叫者的窗 id:AIB_FF_WINDOW(測試/明說)> `tmux display-message -t $TMUX_PANE`;都沒有回 None。"""
    w = os.environ.get("AIB_FF_WINDOW")
    if w:
        return w
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None
    try:
        p = subprocess.run(["tmux", "display-message", "-p", "-t", pane, "#{window_id}"],
                           capture_output=True, text=True, timeout=2, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return p.stdout.strip() or None


def default_seats_dir():
    """席位目錄走單源 seats_dir_lib(HUB 導出 > EZ2_AIB_INSTANCE_HOME > 狀態根),本檔不自己讀艙名
    (test-cabin-single-source-names / cabin-read-set 兩把尺:讀了單源名字的函式,它讀的每個名字都要在單源裡)。"""
    try:
        import seats_dir_lib
        d = seats_dir_lib.seats_dir()
    except Exception:
        return None
    return str(d) if d else None


def caller_role(seats_dir, window_id):
    """席位檔 window_id 對到的 role;對不到回 None(= 量不到,不是 dev)。"""
    if not seats_dir or not window_id:
        return None
    d = pathlib.Path(seats_dir)
    if not d.is_dir():
        return None
    for f in sorted(d.glob("w*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("window_id") == window_id:
            return doc.get("role") or None
    return None


def counts(repo):
    """回 (remote8, local8, ahead, behind);origin/main 解不開 → remote=None、數字 None。"""
    _, local, _ = _git(repo, "rev-parse", "--short=8", "HEAD")
    rc, remote, _ = _git(repo, "rev-parse", "--short=8", "origin/main")
    if rc != 0:
        return None, local, None, None
    rc, lr, _ = _git(repo, "rev-list", "--left-right", "--count", "origin/main...HEAD")
    if rc != 0:
        return remote, local, None, None
    behind, ahead = (int(x) for x in lr.split())
    return remote, local, ahead, behind


def _emit(doc, human=None):
    if human:
        print(human)
    print(json.dumps(doc, ensure_ascii=False))
    return doc["rc"]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aib ff", add_help=True)
    ap.add_argument("sha")
    ap.add_argument("--repo", default=os.getcwd())
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--seats-dir", default=None,
                    help="席位檔目錄(預設 $EZ2_AIB_INSTANCE_HOME/seats)")
    a = ap.parse_args(argv)
    repo = pathlib.Path(a.repo)
    doc = {"rc": 2, "reason": None, "tree": str(repo), "role": None, "head": None, "sha": a.sha,
           "remote": None, "local": None, "ahead": None, "behind": None}

    ok, top, canon = is_canonical(repo)
    if top is not None:
        doc["tree"] = str(top)
    if not ok:
        doc["reason"] = "not_canonical"
        return _emit(doc, T("aib.ff.not_canonical", {"tree": str(top), "canonical": str(canon)}))
    rc, branch, _ = _git(top, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0 or branch != "main":
        doc["reason"] = "not_on_main"
        return _emit(doc, T("aib.ff.not_on_main", {"cur_branch": branch}))
    rc, dirty, _ = _git(top, "status", "--porcelain")
    if rc != 0 or dirty:
        doc["reason"] = "dirty"
        return _emit(doc, T("aib.ff.dirty"))
    rc, sha, _ = _git(top, "rev-parse", "--verify", "--quiet", a.sha + "^{commit}")
    if rc != 0 or not sha:
        doc["reason"] = "bad_sha"
        return _emit(doc, T("aib.ff.bad_sha", {"sha": a.sha}))
    doc["sha"] = sha
    _, head, _ = _git(top, "rev-parse", "HEAD")
    doc["head"] = head
    rc, _, _ = _git(top, "merge-base", "--is-ancestor", head, sha)
    if rc != 0:
        doc["rc"], doc["reason"] = 1, "not_ff"
        return _emit(doc, T("aib.ff.not_ff", {"sha": sha[:8], "head": head[:8]}))

    captains = [x for x in (os.environ.get("EZ2_AIB_CAPTAIN_ROLES") or DEFAULT_CAPTAIN_ROLES).split() if x]
    seats_dir = a.seats_dir or default_seats_dir()
    window = caller_window()
    role = caller_role(seats_dir, window)
    doc["role"] = role
    notes = []
    if role is None:
        notes.append(T("aib.ff.unmeasured"))
    elif role not in captains:
        if os.environ.get("AIB_FF_FORCE") == "1":
            notes.append(T("aib.ff.forced", {"role": role}))
        else:
            doc["rc"], doc["reason"] = 3, "not_captain"
            return _emit(doc, T("aib.ff.not_captain", {"role": role, "window": window or "?",
                                                       "captains": " ".join(captains)}))
    for n in notes:
        print(n)

    if a.dry_run:
        doc["rc"], doc["reason"] = 0, "dry_run"
        return _emit(doc, T("aib.ff.dry", {"head": head[:8], "sha": sha[:8]}))
    rc, _, err = _git(top, "merge", "--ff-only", sha)
    if rc != 0:
        doc["reason"] = "merge_failed"
        return _emit(doc, err)
    fetched = True
    if not a.no_fetch:
        rc, _, _ = _git(top, "fetch", "-q", "origin", timeout=60)
        fetched = rc == 0
    remote, local, ahead, behind = counts(top)
    doc.update({"rc": 0, "reason": "ff" if fetched else "ff_unfetched", "remote": remote,
                "local": local, "ahead": ahead, "behind": behind})
    return _emit(doc, T("aib.ff.done", {"local": local, "remote": remote if remote else "unfetched",
                                       "ahead": ahead, "behind": behind}))


if __name__ == "__main__":
    sys.exit(main())
