#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""PreToolUse(Bash) 守門：不准一行 Bash 關掉真的 tmux 伺服器(11-07 the maintainer「開 PR 讓腳本從源頭擋掉」)。

事故(11-07 兩次的同一件):沙盒腳本靠 TMUX_TMPDIR 指沙盒夾;夾被刪後 tmux 不報錯、退回真的預設 socket,
腳本第一行裸 `tmux kill-server` ⇒ 兩艙外層座艙畫面全斷。在席位裡跑裸 tmux 更糟:TMUX 指著內層 aihub,砍的是 AI 席。

由 aib-now-gate.py 的 pre-tool 呼叫(跟 worktree_path_gate 同一個掛點，不另加 settings 條目)。
擋(回 dict、aib-now-gate 以 rc 2 交給模型)只在一行 Bash 裡(heredoc 寫進腳本的內容也算 —— 那就是之後會跑的腳本):
  tmux … kill-server            沒帶 -S / -L ⇒ 打到 TMUX / TMUX_TMPDIR / 預設 socket,一律擋(含 TMUX_TMPDIR=… 前綴)
  tmux -L <名> … kill-server    名 = default、aihub-<艙號> 格式、或 aihub-$變數 ⇒ 擋;其他唯一名放行
  tmux -S <檔> … kill-server    檔是真 socket 夾(/tmp/tmux-<uid>)裡的 default / aihub-<艙號> ⇒ 擋;
                                帶變數的路徑放行(socket 檔不在時 tmux 報錯，不會退回預設伺服器)
  killall tmux / pkill [選項] tmux
放行：上面以外、引號裡的字(echo / grep)、註解。解析失敗、任何例外 ⇒ 放行(fail-open,同 worktree_path_gate)。
這是防手滑，不是防線(同帳號 AI 可以用 Write 寫檔再跑;見 GUIDE-same-user-ai-threat-model)。
Codex / Grok 席不吃 Claude hook。
"""
import os
import re
import shlex

SEPARATORS = {"&&", "||", ";", "|", "&", "(", ")", "{", "}", "|&", ";;"}
WRAPPERS = {"timeout", "nice", "time", "command", "exec", "nohup", "caffeinate", "sudo"}
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
REAL_NAME_RE = re.compile(r"^(default|aihub-\d{8}-\d{6}-[0-9a-z]{4})$")
TMUX_OPT_ARG = {"-S", "-L", "-f", "-c", "-T"}


def _line_tokens(line):
    try:
        lex = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
        lex.whitespace_split = True
        return list(lex)
    except ValueError:   # 引號沒配對(heredoc 裡的英文縮寫等)⇒ 退回粗切
        return re.sub(r"([;&|()])", r" \1 ", line.split("#", 1)[0]).split()


def _segments(cmd):
    for line in cmd.splitlines():
        seg = []
        for tok in _line_tokens(line):
            if tok in SEPARATORS:
                if seg:
                    yield seg
                seg = []
            else:
                seg.append(tok)
        if seg:
            yield seg


def _strip_prefix(seg):
    """拿掉 VAR=值、env [-i] [-u 名]、timeout 60 / nice -n 19 這類包裝。"""
    while seg:
        if ASSIGN_RE.match(seg[0]):
            seg = seg[1:]
        elif os.path.basename(seg[0]) == "env":
            seg = seg[1:]
            while seg and (seg[0].startswith("-") or ASSIGN_RE.match(seg[0])):
                seg = seg[2:] if seg[0] in ("-u", "--unset", "-C", "--chdir", "-S") else seg[1:]
        elif os.path.basename(seg[0]) in WRAPPERS:
            seg = seg[1:]
            while seg and (seg[0].startswith("-") or re.fullmatch(r"\d+(\.\d+)?[smhd]?", seg[0])):
                seg = seg[2:] if seg[0] in ("-n", "-s", "-k", "-u") else seg[1:]
        else:
            return seg
    return seg


def _real_dirs():
    uid = os.getuid() if hasattr(os, "getuid") else 0
    base = "/tmp/tmux-%d" % uid
    return {base, os.path.realpath(base), "/private" + base}


def _real_socket(path, home):
    if "$" in path or "`" in path:
        return False
    if path.startswith("~/") or path == "~":
        path = home + path[1:]
    d, name = os.path.split(os.path.normpath(path))
    return bool(REAL_NAME_RE.match(name)) and (d in _real_dirs() or os.path.realpath(d) in _real_dirs())


def _tmux_hit(args, home):
    sock = name = None
    i = 0
    while i < len(args) and args[i].startswith("-") and args[i] != "--":
        a = args[i]
        if a in TMUX_OPT_ARG:
            val = args[i + 1] if i + 1 < len(args) else ""
            if a == "-S":
                sock = val
            elif a == "-L":
                name = val
            i += 2
        elif a[:2] in ("-S", "-L") and len(a) > 2:   # -Lname / -S/path 黏在一起
            sock, name = (a[2:], name) if a[:2] == "-S" else (sock, a[2:])
            i += 1
        else:
            i += 1
    if "kill-server" not in args[i:]:
        return None
    if sock is not None:
        return "real_socket" if _real_socket(sock, home) else None
    if name is not None:
        if REAL_NAME_RE.match(name) or (name.startswith("aihub-") and "$" in name):
            return "real_name"
        return None
    return "bare"


def check(cmd, home=None):
    """這行 Bash 會不會關掉真的 tmux 伺服器;會 ⇒ (原因代碼, 那一段指令);不會 ⇒ None。"""
    home = home or os.path.expanduser("~")
    for seg in _segments(cmd):
        seg = _strip_prefix(seg)
        if not seg:
            continue
        prog = os.path.basename(seg[0])
        if prog == "tmux":
            why = _tmux_hit(seg[1:], home)
            if why:
                return why, " ".join(seg)
        elif prog in ("killall", "pkill"):
            if "tmux" in [a for a in seg[1:] if not a.startswith("-")]:
                return "killall", " ".join(seg)
    return None


def pre_tool(payload, home=None):
    """要擋 ⇒ {"why": 代碼, "cmd": 那一段};放行 ⇒ None。字由 aib-now-gate 照語系組。"""
    if payload.get("tool_name") not in (None, "Bash"):
        return None
    cmd = str((payload.get("tool_input") or {}).get("command") or "")
    if "tmux" not in cmd or not re.search(r"kill-server|killall|pkill", cmd):
        return None
    hit = check(cmd, home)
    if not hit:
        return None
    return {"why": hit[0], "cmd": hit[1][:200]}
