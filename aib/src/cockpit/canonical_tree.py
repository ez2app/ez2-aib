# SPDX-License-Identifier: Apache-2.0
"""本檔在「正本樹」裡的同一支:裝進各家 hook 設定的路徑一律用它(不寫工作樹路徑:工作樹一刪,hook 就安靜失效)。

10-15 修(公開版 git 安裝的三支 hook 永遠裝不上):舊寫法拿 `git worktree list` 第一列(倉庫頂層)直接接
`src/...`,但公開倉的程式在倉庫底下一層 `aib/`(~/ez2-aib/aib/src/...)⇒ 算出 ~/ez2-aib/src/... 不存在 ⇒ 每次開席都跳過。
現在用 `git rev-parse --show-prefix` 量出「這支在倉庫裡的相對位置」再接到正本樹 —— 私有倉(src 在頂層)、
公開倉(src 在 aib/ 底下)、pipx 安裝(不在 git 裡,本支就是正本)三種一樣對。
"""
from __future__ import annotations

import os
import subprocess


def _git(cwd, *args):
    try:
        r = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, timeout=10,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _inside_git(path):
    """往上找得到 .git(資料夾或檔案)⇒ 這棵樹在 git 裡。"""
    d = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return True
        up = os.path.dirname(d)
        if up == d:
            return False
        d = up


def canonical_path(path):
    """path(一支程式檔)在正本樹(git worktree list 第一列 = 主工作樹)裡的同一支。
    不在 git 裡(pipx / zip 下載)⇒ 回 path 本身;在 git 裡但 git 量不到(跑失敗、輸出空白)⇒ None,不猜。"""
    path = os.path.abspath(path)
    folder = os.path.dirname(path)
    if not _inside_git(folder):
        return path
    prefix = _git(folder, "rev-parse", "--show-prefix")
    listing = _git(folder, "worktree", "list", "--porcelain")
    if prefix is None or not listing:
        return None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            main = line[len("worktree "):]
            return os.path.join(main, prefix.strip(), os.path.basename(path))
    return None
