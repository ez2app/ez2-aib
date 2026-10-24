#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""PreToolUse(Bash) 守門：工作樹 / clone 不准開在 Mac 的 Spotlight 會掃的地方(10-24 the maintainer
「mac 會一直掃檔案建快取」→「不是提醒，是用程式必要規定」)。

由 aib-now-gate.py 的 pre-tool 呼叫(那支已由 provider-launch.sh ensure 裝在每一席，不另加 settings 條目)。
擋(回訊息、aib-now-gate 以 rc 2 交給模型)只在一行 Bash 裡有這幾種、而且目標落在家目錄底下的一般夾:
  git [-C 夾] worktree add [選項] <路徑> …
  git [-C 夾] worktree move <樹> <新路徑>
  git [-C 夾] clone [選項] <來源> [<夾>]      (沒給夾 ⇒ 目前目錄下的倉名)
相對路徑照 payload 的 cwd 與同一行前面的 `cd <夾>` 算;~ 與 $HOME 會展開，其他變數認不出 ⇒ 放行。
不是 Mac、解析失敗、任何例外 ⇒ 放行(fail-open,同 test-entry-gate:擋錯的代價比漏擋高)。
Codex / Grok 席不吃 Claude hook:交接驗證(handoff_check)那一項照樣擋。
"""
import os
import re
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "cockpit")))
import spotlight_paths  # noqa: E402

SEPARATORS = {"&&", "||", ";", "|", "&", "(", ")", "{", "}", "|&"}
WRAPPERS = {"timeout", "env", "nice", "time", "command", "exec", "nohup", "caffeinate"}
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
GIT_OPT_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
WT_ADD_ARG = {"-b", "-B", "--reason"}
CLONE_ARG = {"-b", "--branch", "-o", "--origin", "--depth", "-c", "--config", "--reference", "--reference-if-able",
             "--separate-git-dir", "--template", "-u", "--upload-pack", "-j", "--jobs", "--filter",
             "--shallow-since", "--shallow-exclude", "--bundle-uri", "--server-option"}


def _segments(cmd):
    lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=";&|()")
    lex.whitespace_split = True
    seg = []
    for tok in lex:
        if tok in SEPARATORS:
            if seg:
                yield seg
            seg = []
        else:
            seg.append(tok)
    if seg:
        yield seg


def _expand(p, cwd, home):
    if p.startswith("~"):
        p = home + p[1:] if p == "~" or p.startswith("~/") else p
    p = p.replace("${HOME}", home).replace("$HOME", home)
    if "$" in p or p.startswith("~"):
        return None
    return os.path.normpath(os.path.join(cwd, p))


def _positional(args, takes_arg):
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a == "--":
            out += args[i + 1:]
            break
        if a.startswith("-"):
            if a in takes_arg:
                i += 1
        else:
            out.append(a)
        i += 1
    return out


def targets(cmd, cwd, home):
    """這行 Bash 會在哪裡開新樹 / clone(絕對路徑清單;認不出的不列)。"""
    found = []
    for seg in _segments(cmd):
        while seg and (ASSIGN_RE.match(seg[0]) or seg[0] in WRAPPERS):
            wrapper = seg[0] in WRAPPERS
            seg = seg[1:]
            while wrapper and seg and (seg[0].startswith("-") or re.fullmatch(r"\d+(\.\d+)?[smhd]?", seg[0])):
                seg = seg[1:]   # timeout 900 / nice -n 19 的選項與秒數
        if not seg:
            continue
        if seg[0] == "cd" and len(seg) >= 2:
            nxt = _expand(seg[1], cwd, home)
            if nxt is None:
                return found
            cwd = nxt
            continue
        if os.path.basename(seg[0]) != "git":
            continue
        args, base, i = seg[1:], cwd, 0
        while i < len(args) and args[i].startswith("-"):
            if args[i] == "-C" and i + 1 < len(args):
                b = _expand(args[i + 1], base, home)
                if b is None:
                    break
                base = b
            i += 2 if args[i] in GIT_OPT_ARG else 1
        args = args[i:]
        if args[:2] == ["worktree", "add"]:
            pos = _positional(args[2:], WT_ADD_ARG)
            if pos:
                found.append(_expand(pos[0], base, home))
        elif args[:2] == ["worktree", "move"]:
            pos = _positional(args[2:], set())
            if len(pos) >= 2:
                found.append(_expand(pos[1], base, home))
        elif args[:1] == ["clone"]:
            pos = _positional(args[1:], CLONE_ARG)
            if len(pos) >= 2:
                found.append(_expand(pos[1], base, home))
            elif len(pos) == 1:
                name = re.sub(r"\.git$", "", pos[0].rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1])
                if name:
                    found.append(os.path.join(base, name))
    return [p for p in found if p]


def pre_tool(payload, home=None, platform=None):
    """要擋 ⇒ 回 {"paths": [...], "suggest": "~/wt.noindex/<名>"};放行 ⇒ None。字由 aib-now-gate 照語系組。"""
    if payload.get("tool_name") not in (None, "Bash"):
        return None
    cmd = str((payload.get("tool_input") or {}).get("command") or "")
    if not re.search(r"\bgit\b", cmd) or not re.search(r"\b(worktree|clone)\b", cmd):
        return None
    home = os.path.realpath(home or os.path.expanduser("~"))
    cwd = str(payload.get("cwd") or os.getcwd())
    bad = [p for p in targets(cmd, cwd, home) if spotlight_paths.indexed(p, home, platform)]
    if not bad:
        return None
    name = os.path.basename(bad[0].rstrip(os.sep)) or "tree"
    return {"paths": bad, "suggest": "%s/%s" % (spotlight_paths.NOINDEX_HOME, name)}
