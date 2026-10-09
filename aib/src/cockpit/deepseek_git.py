#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""DeepSeek 席的受限 git(the maintainer 09-27 22:3x「如何讓他們也能跟你們一樣的開發工作?」)。

只在「自己的 worktree」裡、只對「自己的分支」動作:
  status / diff / add <明確相對路徑> / commit -m / push origin HEAD:<自己的分支>
一律拒:main/master/release/*/integrate/* 分支、add -A 或 `.`、reset、checkout、rebase、merge、tag、force push、
專案外路徑、.git 與 secrets。任何一條不合就回一句「git 未執行:原因」,不拋例外。
自己的分支 = ds/* 或 feat/ds-*(由開席端建 worktree 時命名;見 PLAN-deepseek-dev-seat-1857a-5e128-0927.md)。
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

OWN_BRANCH_RE = re.compile(r"^(ds/|feat/ds-)[A-Za-z0-9._/-]{1,100}$")
FORBIDDEN_BRANCH_RE = re.compile(r"^(main|master|HEAD|release/.*|integrate/.*)$")
ACTIONS = ("status", "diff", "add", "commit", "push")
OUT_MAX = 6000
MSG_MAX = 2000


def _run(root: Path, argv, timeout=120):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_EDITOR="true")
    # r1 H1(codex):pathspec magic(:(top,glob)** 等)可繞過「明確路徑」⇒ 一律字面路徑模式
    try:
        r = subprocess.run(["git", "--literal-pathspecs", "-C", str(root)] + list(argv), capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", "%s: %s" % (type(exc).__name__, exc)
    return r.returncode, r.stdout or "", r.stderr or ""


def current_branch(root: Path) -> str:
    rc, out, _ = _run(root, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=15)
    return out.strip() if rc == 0 else ""


def check_scope(root: Path, worktree: str | None) -> str:
    """回空字串 = 可以動;否則回拒絕原因。"""
    if not worktree:
        return "這一席沒有自己的 worktree(AIB_DEEPSEEK_WORKTREE 沒設),git 一律不開。"
    try:
        if Path(worktree).resolve() != root.resolve():
            return "目前目錄不是這一席的 worktree。"
    except OSError:
        return "worktree 路徑讀不到。"
    br = current_branch(root)
    if not br or FORBIDDEN_BRANCH_RE.match(br) or not OWN_BRANCH_RE.match(br):
        return "目前分支 %r 不是這一席自己的分支(要 ds/* 或 feat/ds-*)。" % br
    return ""


def norm_part(part) -> str:
    """r3 補(a seat 點名的新繞法):Unicode 相容正規化(全形、合成字)+ casefold + 去尾端空白與點
    (Windows/SMB 會把 `secrets.`、`secrets ` 當成 `secrets`)。"""
    import unicodedata
    s = unicodedata.normalize("NFKC", str(part)).casefold()
    return s.rstrip(" .\t")


def forbidden_part(parts) -> bool:
    """r2 H2/M2 + r3:.git、secrets、.env* 的任何變形都擋。"""
    for part in parts:
        low = norm_part(part)
        if low in (".git", "secrets") or low.startswith(".env"):
            return True
    return False


def _clean_paths(root: Path, paths):
    if not isinstance(paths, list) or not paths:
        raise ValueError("add 要給明確的相對路徑清單(不准 -A 或 .)。")
    out = []
    for raw in paths[:50]:
        if not isinstance(raw, str) or not raw.strip() or raw.strip() in (".", "-A", "--all", "*") or raw.startswith("-"):
            raise ValueError("路徑 %r 不合法(不准 -A、. 或旗標)。" % raw)
        if raw.startswith(":") or any(ch in raw for ch in "*?[]\\") or "\n" in raw:
            raise ValueError("路徑 %r 不合法(不准 git pathspec 特殊寫法或萬用字元)。" % raw)
        p = (root / raw).resolve()
        if p == root or root not in p.parents:
            raise ValueError("拒絕專案外路徑 %r。" % raw)
        rel = p.relative_to(root)
        if forbidden_part(Path(raw).parts) or forbidden_part(rel.parts):
            raise ValueError("拒絕 .git、secrets 與 .env(不分大小寫)。")
        if p.is_dir():
            raise ValueError("路徑 %r 是目錄;add 只收單一檔案(目錄會遞迴帶進未追蹤檔)。" % raw)
        try:
            if p.is_file() and os.stat(p).st_nlink > 1:
                raise ValueError("路徑 %r 是硬連結(可能指到秘密檔),不收。" % raw)
        except OSError:
            raise ValueError("路徑 %r 讀不到檔案資訊。" % raw)
        tracked = _run(root, ["ls-files", "--error-unmatch", "--", str(rel)], timeout=15)[0] == 0
        if not (p.is_file() or tracked):
            raise ValueError("路徑 %r 不是存在的檔案(刪檔要是已追蹤的檔)。" % raw)
        out.append(str(rel))
    return out


def execute(root: Path, worktree: str | None, args: dict) -> str:
    action = str(args.get("git_action") or "").strip()
    if action not in ACTIONS:
        return "git 未執行:git_action 只可為 %s。" % "、".join(ACTIONS)
    why = check_scope(root, worktree)
    if why:
        return "git 未執行:" + why
    try:
        if action == "status":
            rc, out, err = _run(root, ["status", "--short", "--branch"])
        elif action == "diff":
            rc, out, err = _run(root, ["diff", "--stat"] + (["--cached"] if args.get("cached") else []))
            if rc == 0:
                rc2, full, _ = _run(root, ["diff"] + (["--cached"] if args.get("cached") else []))
                out = out + "\n" + full
        elif action == "add":
            rc, out, err = _run(root, ["add", "--"] + _clean_paths(root, args.get("paths")))
            # r2 H2:add 完再掃一次暫存區;有禁區檔就撤出暫存並回拒絕(防任何沒想到的繞法)
            _, staged, _ = _run(root, ["diff", "--cached", "--name-only", "-z"], timeout=30)
            bad = [n for n in staged.split("\0") if n and forbidden_part(Path(n).parts)]
            if bad:
                _run(root, ["restore", "--staged", "--"] + bad, timeout=30)
                return "git 未執行:暫存區出現禁區檔 %s,已撤出暫存。" % ", ".join(bad[:5])
        elif action == "commit":
            msg = str(args.get("message") or "").strip()
            if not msg:
                return "git 未執行:commit 要給 message。"
            rc, out, err = _run(root, ["commit", "-m", msg[:MSG_MAX]], timeout=300)
        else:  # push:只推自己的分支,不帶 force
            br = current_branch(root)
            rc, out, err = _run(root, ["push", "origin", "HEAD:refs/heads/%s" % br], timeout=600)
    except ValueError as exc:
        return "git 未執行:%s" % exc
    text = (out + ("\n" + err if err.strip() else "")).strip() or "(沒有輸出)"
    return "git %s rc=%d:\n%s" % (action, rc, text[-OUT_MAX:])
