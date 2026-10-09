# SPDX-License-Identifier: Apache-2.0
"""寫入白名單閘(SOP §〇:提取輪零寫入人格資產;合法產出白名單僅二)。

所有會落盤的路徑**必須**先過 allowed(path);不在白名單=WriteRefused。
白名單只有兩個目的地(PLAN §1 〇 列):
  1. 候選/證據安全庫:$EZ2_PERSONA_CANDIDATE_ROOT(預設 ~/.ez2/persona-candidates)之下
  2. 候選包落點:docs/handoff/persona-candidates-<code>-<日期>.local.json(repo 內、不進 git)
角色.md / 個人記憶.md / kb / provenance.jsonl / 交接簡報.md / kpi-ledger.jsonl 一律拒——
就算它們碰巧在白名單目錄之下也拒(名字閘先於目錄閘)。
"""

import os
import re

FORBIDDEN_BASENAMES = ("角色.md", "個人記憶.md", "交接簡報.md", "provenance.jsonl",
                       "kpi-ledger.jsonl", "evals.json", "watchlist.jsonl")
_LOCAL_JSON = re.compile(r"^persona-candidates-[A-Za-z0-9._-]{1,64}-\d{8}\.local\.json$")


class WriteRefused(Exception):
    pass


def candidate_root():
    return os.path.abspath(os.environ.get("EZ2_PERSONA_CANDIDATE_ROOT")
                           or os.path.expanduser("~/.ez2/persona-candidates"))


def allowed(path, repo_root=None):
    """回 True 或拋 WriteRefused(帶原因)。

    ★ 艦長終審 L5-01(01:15)抓到:只 abspath 不解 symlink,候選根下放一個指向
      /x/角色.md 的連結,名字閘看到的是連結名 → 放行。修:**path 本身若是 symlink 直接拒**
      (白名單目的地沒有正當理由是連結),再以 realpath 解析後做名字閘+目錄閘——目錄閘也
      比對 realpath 的候選根,任何一段父目錄是連結指到外面同樣落網。"""
    p_abs = os.path.abspath(path)
    if os.path.islink(p_abs):
        raise WriteRefused("refuse: %s is a symlink (whitelist targets must be real files)" % p_abs)
    p = os.path.realpath(p_abs)
    for cand in (p_abs, p):
        base = os.path.basename(cand)
        if base in FORBIDDEN_BASENAMES:
            raise WriteRefused("refuse: %s is a persona asset (SOP §〇 zero-write)" % base)
    root = os.path.realpath(candidate_root())
    if p == root or p.startswith(root + os.sep):
        return True
    if repo_root:
        handoff = os.path.realpath(os.path.join(repo_root, "docs", "handoff"))
        if os.path.dirname(p) == handoff and _LOCAL_JSON.match(os.path.basename(p)):
            return True
    raise WriteRefused("refuse: %s outside write whitelist (candidate root / docs/handoff/*.local.json)" % p)


def open_for_write(path, mode="w", repo_root=None, **kw):
    """唯一合法的寫入開檔入口:過閘才 open。mode 只准 w/a/x(不准 r+ 之類繞過語意)。"""
    if mode not in ("w", "a", "x"):
        raise WriteRefused("refuse: mode %r" % mode)
    allowed(path, repo_root=repo_root)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # qa L5-01 對抗審:open 也帶 O_NOFOLLOW——allowed() 與 open 之間目錄項可被換成連結,
    # 最後一段由核心擋(與 rotation _open_checkpoints 同紀律),0o600 建檔。
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= {"w": os.O_TRUNC, "a": os.O_APPEND, "x": os.O_EXCL}[mode]
    fd = os.open(path, flags, 0o600)
    try:
        return os.fdopen(fd, mode, **kw)
    except Exception:
        os.close(fd)
        raise
