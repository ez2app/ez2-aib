#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""V1 shadow tracer: run tests, record five input classes, compute keys.

Always runs the full list. V1 never skips. Planner only prints a counterfactual.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import jsonl_safe  # 10-04:jsonl 行界只認 "\n"(見該檔)
import os
import re
import shlex
import platform
import subprocess
import tempfile
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_DEFAULT = HERE.parents[1]

sys.path.insert(0, str(HERE))
import planner as plan  # noqa: E402


def _run(cmd, cwd, env, timeout=120):
    return subprocess.run(
        cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=timeout)


def _tool_version(cmd, env, timeout=120):
    """環境指紋用的版本探測(node --version / tmux -V):程式不在 PATH 上(或不能執行、逾時)回空字串,
    由呼叫端記成 node-missing / tmux-missing。★ 10-02 雲端實際碰到:node 不在固定 PATH ⇒ _run 丟
    FileNotFoundError,整個指紋算不出來;原意一直是「沒裝 = missing」。其他錯誤照丟,不吞。"""
    # 10-04:問版本時把追蹤拿掉(Node 接頭由 NODE_OPTIONS 掛上 ⇒ 不拿掉的話 `node --version` 會往追蹤檔寫 inherit/exit,
    #   判沿用時甚至寫進 /tmp/.ez2-verify-reuse-probe)。版本字串不受影響。
    if env is not None:
        env = {k: v for k, v in env.items() if k not in ("EZ2_VERIFY_TRACE", "NODE_OPTIONS", "AIB_LDP_TRACE", "LD_PRELOAD")}
    try:
        return (_run(cmd, Path("."), env, timeout=timeout).stdout or "").strip()
    except (FileNotFoundError, PermissionError, NotADirectoryError, subprocess.TimeoutExpired):
        return ""


def tree_sha(repo: Path) -> str:
    head = _run(["git", "-C", str(repo), "rev-parse", "HEAD"], repo, os.environ)
    if head.returncode != 0:
        raise SystemExit("tree_sha: git rev-parse failed")
    sha = head.stdout.strip()
    st = _run(["git", "-C", str(repo), "status", "--porcelain"], repo, os.environ)
    if not st.stdout.strip():
        return sha
    diff = _run(["git", "-C", str(repo), "diff", "HEAD"], repo, os.environ)
    blob = (sha + "\n" + st.stdout + (diff.stdout or "")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


VOLATILE_ENV = {
    # 每一跑都不同、且不影響結果語意的名字(路徑類由驗證腳本 mktemp 出來)
    "EZ2_VERIFY_TRACE", "AIB_I18N_KEYLOG", "AIB_KEYED_FILES", "TMPDIR", "AIB_TEST_TMPDIR", "AIB_HOOK_CACHE", "EZ2_AIB_AI_VIEW", "EZ2_AIB_PANES", "EZ2_AIB_DEV_MODE",
    "HOME", "EZ2_USAGE_DIR", "EZ2_CODEX_SESSIONS_DIR", "PWD", "OLDPWD", "SHLVL", "_",
    "TERM_SESSION_ID", "XPC_SERVICE_NAME", "SECURITYSESSIONID", "TMUX_PANE", "TMUX",
    "AIB_WITNESS_RESUME_FROM", "AIB_WITNESS_ROWS_JSONL", "AIB_WITNESS_SHADOW", "AIB_WITNESS_SHADOW_PREV",
    "AIB_WITNESS_SHADOW_REASON", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT",
    "AIB_RELEASE_STAMP_FILE", "AIB_RELEASE_LOGDIR", "AIB_SUITE_RESUME_FROM", "RELEASE_CRITERIA_SUITE_MARKER",
    "SHADOW_MARKER", "PYTHONPATH",   # PYTHONPATH 是追蹤器自己加的 <REPO>/tools/verify,不是輸入
    "EZ2_VERIFY_PY",                 # 10-08:墊片算雜湊用的那支 python 路徑(追蹤器自己加的;用哪支 python 另有指紋)
    "AIB_LDP_TRACE",   # 10-07 安全網的追蹤檔路徑(每跑都不同)
    "EZ2_VERIFY_SHADOW_DIR", "EZ2_VERIFY_TRACE_DIR",   # 96:追蹤器自己的落檔位置,不是測試的輸入(A/B 兩輪目錄不同鑰匙就不同)
    "EZ2_VERIFY_RUNNER_PID",   # 10-04:執行器 pid,每輪不同(修法 A 第二版用),不是輸入
    "BASH_ENV",   # 96b:追蹤器掛 bash 鉤子用的,路徑在 <REPO>/tools/verify/bash-shim/_trace.bash
    # 09-29 a seat(設計稿 docs/DESIGN-shadow-resume-complete-0929.md 五之二 R6):v0.69.4 第三輪以第二輪全套為上一份,
    #   預期可沿用 39 支、實際 0 —— 兩輪環境指紋整輪不同,讀測試行程實際環境查出元兇:
    #   AIB_RELEASE_FREEZE_MAX_SEC(凍結窗上限,發版腳本每輪由「最近 5 跑全套最長 × 1.5」重算:7218 → 13662)。
    #   ⇒ 每次發版指紋必變,全套結構上永遠沿用不到。SSH_AUTH_SOCK 同類(每次登入換路徑,發版推送要它)。
    #   兩者都是發版機器自己的值,不是測試的輸入;只從**指紋**排除,測試拿到的環境不變。
    "AIB_RELEASE_FREEZE_MAX_SEC", "SSH_AUTH_SOCK",
}

# ---- 96b(09-14):bash 子行程追蹤 --------------------------------------------------------------
# 鉤子 tools/verify/bash-shim/_trace.bash 由 BASH_ENV 載入(環境本來就帶、或 PATH 上的 bash 墊片掛上),兩個通道:
#   xtrace(展開後的每個命令,一行 +EZ2BT␟src␟line␟pwd␟pid␟ cmd)→ 這裡分類子命令、抓 source 與存在探測;
#   DEBUG trap(展開前的文字)→ $(<file)、cmd < file 在讀的當下雜湊成 open:(解析不了記 unmeasured)。
#   EXIT trap 把 source 過的檔與主腳本雜湊成 open:,並記 exit。
# macOS /bin/bash 3.2 沒有 BASH_XTRACEFD ⇒ 只記 inherit/exit + unmeasured bash-version-no-xtrace(整列量不到)。
BASH_XT_SEP = "\x1f"
BASH_XT_NOISE = ("case $- in", "_EZ2_BT_XON=", "set +x", "set -x", "_ez2_bt_")
BASH_BUILTINS = frozenset((
    ":", ".", "source", "alias", "bg", "bind", "break", "builtin", "caller", "cd", "command", "compgen", "complete",
    "compopt", "continue", "declare", "typeset", "dirs", "disown", "echo", "enable", "eval", "exec", "exit", "export",
    "false", "fc", "fg", "getopts", "hash", "help", "history", "jobs", "kill", "let", "local", "logout", "mapfile",
    "readarray", "popd", "printf", "pushd", "pwd", "read", "readonly", "return", "set", "shift", "shopt", "suspend",
    "test", "[", "[[", "]]", "times", "trap", "true", "type", "ulimit", "umask", "unalias", "unset", "wait",
    "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac", "function", "select",
    "in", "time", "coproc", "{", "}", "!",
))
BASH_WRAPPERS = frozenset(("command", "builtin", "exec", "nohup", "nice", "time", "timeout", "caffeinate"))
BASH_PROBE_FLAGS = frozenset(("-e", "-f", "-d", "-r", "-w", "-x", "-s", "-L", "-h", "-p", "-S", "-b", "-c", "-g", "-k", "-u", "-O", "-G", "-N"))


_EVAL_ASSIGN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=\$\{[A-Za-z_][A-Za-z0-9_]*(?::?[-=+?][^}$`<>()\\;|&'\"]*)?\}")


WHY_MAX = 200


def _dedupe_why(why):
    out, seen = [], {}
    for w in why:
        if w in seen:
            seen[w] += 1
            continue
        seen[w] = 1
        out.append(w)
    extra = 0
    if len(out) > WHY_MAX:
        extra = len(out) - WHY_MAX
        out = out[:WHY_MAX]
    if extra:
        out.append("why-truncated:%d" % extra)
    return out


def parse_xtrace_line(line):
    """`+EZ2BT␟src␟line␟pwd␟pid␟ cmd` → 事件 dict;不是這個形回 None。"""
    stripped = line.lstrip("+")
    if not stripped.startswith("EZ2BT" + BASH_XT_SEP):
        return None
    parts = stripped.split(BASH_XT_SEP, 5)
    if len(parts) < 6:
        return None
    cmd = parts[5][1:] if parts[5].startswith(" ") else parts[5]
    try:
        pid = int(parts[4])
    except ValueError:
        pid = None
    return {"ch": "bx", "src": parts[1], "line": parts[2], "cwd": parts[3], "pid": pid, "cmd": cmd,
            "depth": len(line) - len(stripped)}


def _bx_noise(ev):
    cmd = ev.get("cmd") or ""
    return str(ev.get("src") or "").endswith("/_trace.bash") or any(cmd.startswith(n) for n in BASH_XT_NOISE)


_ANSI_C = re.compile(r"\$'((?:[^'\\]|\\.)*)'")


def _decode_ansi_c(m):
    import shlex
    body = m.group(1)
    try:
        text = body.encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8", "replace")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return None
    return shlex.quote(text)


def bash_command_argv(cmd):
    """xtrace 的展開後命令 → argv list;$'…'(ANSI-C 引號,xtrace 對含換行/tab 的字串會這樣印)先解碼再拆;拆不開回 None。"""
    import shlex
    if "$'" in cmd:
        # 只換真的 $'…' 片段;像 RE='^[a-z]+$' 這種以 $' 結尾的字面不是 ANSI-C 引號,交給 shlex 照常拆
        try:
            cmd = _ANSI_C.sub(lambda m: _decode_ansi_c(m) or (_ for _ in ()).throw(ValueError()), cmd)
        except ValueError:
            return None
    try:
        return shlex.split(cmd)
    except ValueError:
        return None


# ---- 10-02:env 的參數照實拆(交接單 OPTIMIZE-witness-reuse-cbcf5-1002 二)-------------------------------
# 以前 `env -u TMUX tmux -L x …` 把 -u 的引數 TMUX 當成程式名 ⇒ 72 支記成 child-untracked:TMUX(真的是 tmux ⇒ external);
# python 側的 Popen(["env", …])更糟:env 在 CHILD_TRANSPARENT ⇒ 不管 env 後面跑什麼一律 ok(錯誤地宣稱量得到)。
# 現在:認得的選項照拆,拆完是真正的程式;認不得的(-S、-P、-a、信號類、合寫的 -iu…)⇒ 拆不開 ⇒ untracked,絕不 ok。
# 清環境(-i)或動到鉤子靠的變數(PATH 墊片、BASH_ENV、PYTHON*、EZ2_VERIFY_*)⇒ 子行程的鉤子可能掉了 ⇒ untracked。
ENV_FLAG_OPTS = frozenset(("-0", "--null", "-v", "--debug"))
ENV_HOOK_VARS = frozenset(("PATH", "BASH_ENV"))
ENV_HOOK_PREFIXES = ("PYTHON", "EZ2_VERIFY_")


def parse_env_argv(argv):
    """`env …` → dict(argv=真正的命令(可能是 []), ignore_env, unset, assign, chdir);拆不開回 None。
    認得:-i / - / --ignore-environment、-u N / -uN / --unset N / --unset=N、-C D / -CD / --chdir D / --chdir=D、
    -0/--null、-v/--debug、--,其後的 NAME=VALUE,然後才是程式。其餘任何 - 開頭的選項 ⇒ None(保守)。"""
    if not argv or os.path.basename(str(argv[0])) != "env":
        return None
    out = {"argv": [], "ignore_env": False, "unset": [], "assign": [], "assign_vals": {}, "chdir": None}
    i, n = 1, len(argv)
    while i < n:
        a = str(argv[i])
        if a == "--":
            i += 1
            break
        if a in ("-i", "-", "--ignore-environment"):
            out["ignore_env"] = True
        elif a in ENV_FLAG_OPTS:
            pass
        elif a in ("-u", "--unset", "-C", "--chdir"):
            if i + 1 >= n:
                return None
            if a in ("-u", "--unset"):
                out["unset"].append(str(argv[i + 1]))
            else:
                out["chdir"] = str(argv[i + 1])
            i += 1
        elif a.startswith("--unset="):
            out["unset"].append(a.split("=", 1)[1])
        elif a.startswith("--chdir="):
            out["chdir"] = a.split("=", 1)[1]
        elif a.startswith("-u") and len(a) > 2:
            out["unset"].append(a[2:])
        elif a.startswith("-C") and len(a) > 2:
            out["chdir"] = a[2:]
        elif a.startswith("-"):
            return None          # -S(整串再拆)、-P、-a、信號類、合寫旗標……一律當拆不開
        else:
            break
        i += 1
    while i < n and "=" in str(argv[i]) and not str(argv[i]).startswith("="):
        out["assign"].append(str(argv[i]).split("=", 1)[0])
        out["assign_vals"][str(argv[i]).split("=", 1)[0]] = str(argv[i]).split("=", 1)[1]
        i += 1
    out["argv"] = [str(x) for x in argv[i:]]
    return out


# 10-04:只影響輸出/快取、不影響 sitecustomize 載入的 PYTHON* 變數(release-dev-mode-wiring 等實踩 PYTHON_COLORS)
ENV_PYTHON_HARMLESS = frozenset(("PYTHON_COLORS", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "PYTHONIOENCODING",
                                 "PYTHONUTF8", "PYTHONHASHSEED", "PYTHONWARNINGS", "PYTHONNOUSERSITE", "PYTHONFAULTHANDLER"))


def _env_drops_hooks(parsed):
    """env 有沒有把子行程的鉤子拿掉。10-04 起看值:PATH 仍含墊片目錄、PYTHONPATH 仍含本追蹤器目錄、
    BASH_ENV 仍是本追蹤器的 _trace.bash ⇒ 沒拿掉;無害的 PYTHON* 不算。其餘(不認得的 PYTHON*、PYTHONHOME、
    任何 EZ2_VERIFY_*、unset 鉤子變數)照舊算拿掉。"""
    if parsed["ignore_env"]:
        return True
    for k in parsed["unset"]:
        if (k in ENV_HOOK_VARS or k.startswith(ENV_HOOK_PREFIXES)) and k not in ENV_PYTHON_HARMLESS:
            return True
    vals = parsed.get("assign_vals") or {}
    here = os.path.realpath(str(HERE))
    for k in parsed["assign"]:
        if not (k in ENV_HOOK_VARS or k.startswith(ENV_HOOK_PREFIXES)) or k in ENV_PYTHON_HARMLESS:
            continue
        v = vals.get(k)
        if v is None:
            return True
        if k == "PATH" and any(os.path.realpath(d) == os.path.join(here, "bash-shim") for d in v.split(":") if d):
            continue
        if k == "PYTHONPATH" and any(os.path.realpath(d) == here for d in v.split(os.pathsep) if d):
            continue
        if k == "BASH_ENV" and os.path.realpath(v) == os.path.join(here, "bash-shim", "_trace.bash"):
            continue
        return True
    return False


def _strip_wrappers(argv):
    """env [選項] [K=V…] / command / exec / nohup / nice / timeout N / time 之後的才是真正的命令;
    env 拆不開、env -i、或 env 動了鉤子變數 ⇒ 回 None(子行程的鉤子掉了或不知道跑了什麼)。"""
    return _strip_wrappers_why(argv)[0]


def _strip_wrappers_why(argv):
    """同 _strip_wrappers,另回拆不下去的原因:(argv, None) 或 (None, "env-i" / "env-unparsed" / "env-hookvar")。"""
    out = _unwrap(argv, None)
    return out[0], out[1]


# 10-05:會改變 git 讀哪個倉/哪份設定/哪個索引的環境變數。git 子行程的環境裡有它們 ⇒ argv 看起來像讀真倉或治具,
#   實際可能讀別的倉 ⇒ 不准照 argv 判量得到(之前的洞:只看 argv,不看環境)。
GIT_REPO_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_CONFIG", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_COUNT",
                "GIT_CONFIG_PARAMETERS", "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_REPLACE_REF_BASE",
                "GIT_NO_REPLACE_OBJECTS", "GIT_GRAFT_FILE", "GIT_SHALLOW_FILE")
# 值是路徑的那幾個:在治具裡跑、而且這些都指到治具 ⇒ 照舊算治具(ok);其餘(注入設定、命名空間…)只准在治具裡出現
_GIT_ENV_PATHS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                  "GIT_COMMON_DIR", "GIT_CONFIG", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_GRAFT_FILE", "GIT_SHALLOW_FILE",
                  "GIT_CEILING_DIRECTORIES")
_GIT_ENV_TAINT_RE = re.compile(r"\b(%s)(_[A-Z0-9_]+)?\b" % "|".join(GIT_REPO_ENV))


def _unwrap(argv, cwd):
    """拆掉外包程式,回 (argv, why, cwd):env -C/--chdir 會換目錄 ⇒ 之後的程式的 cwd 跟著換(git 的範圍、相對路徑靠它)。"""
    while argv:
        w = argv[0]
        if os.path.basename(str(w)) == "env":
            parsed = parse_env_argv(argv)
            if parsed is None:
                return None, "env-unparsed", cwd
            if not parsed["argv"]:
                return [], None, cwd      # 沒有命令:只印環境,不讀檔
            if parsed["ignore_env"]:
                return None, "env-i", cwd
            if _env_drops_hooks(parsed):
                return None, "env-hookvar", cwd
            if any(str(a).split("=", 1)[0] in GIT_REPO_ENV or str(a).startswith("GIT_CONFIG_") for a in parsed["assign"]):
                return None, "env-gitvar", cwd     # 10-05:env GIT_DIR=… git … ⇒ 讀的倉由環境決定

            if parsed["chdir"]:
                d = parsed["chdir"]
                cwd = d if d.startswith("/") else os.path.join(cwd or os.getcwd(), d)
            argv = parsed["argv"]
            continue
        if os.path.basename(str(w)) == "flock":
            # 10-03:flock [選項] <鎖檔> <命令…> / flock [選項] <鎖檔> -c '<命令>' / flock [選項] <fd>(只上鎖,不跑東西)。
            #   鎖檔只是拿來上鎖,內容不影響結果;要看的是它代跑的那個命令。-c 交給 sh ⇒ sh 量不到(照舊)。
            i = 1
            while i < len(argv) and str(argv[i]).startswith("-") and argv[i] not in ("-c", "--command"):
                i += 2 if argv[i] in ("-w", "--wait", "--timeout", "-E", "--conflict-exit-code") else 1
            if i >= len(argv):
                return None, "flock-unparsed", cwd
            rest = list(argv[i + 1:])
            if not rest:
                return ([], None, cwd) if str(argv[i]).isdigit() else (None, "flock-unparsed", cwd)
            if rest[0] in ("-c", "--command"):
                return ["sh", "-c"] + rest[1:], None, cwd
            argv = rest
            continue
        if w in ("timeout", "nice"):
            i = 1
            while i < len(argv) and (argv[i].startswith("-") or argv[i].replace(".", "").isdigit() or argv[i][-1:] in "smhd" and argv[i][:-1].isdigit()):
                i += 1
            argv = argv[i:]
            continue
        if w in BASH_WRAPPERS:
            argv = argv[1:]
            continue
        break
    return argv, None, cwd


TRACER_POLICY_VERSION = "v4.0-1003"   # v4.0(不用 v3.10:既有測試以字串比大小,"v3.10" < "v3.7"):暫存根裡跑測試期間才出生的檔記 produced:(不記內容)、tmux 第一版、直接執行假 tmux 也算呼叫;排除規則/正規化/鑰匙公式改了就換,舊指紋全部作廢(v3.9:追蹤器程式內容自動進環境指紋,見 tracer_code_digest;v3.8:讀 .pyc 時補記原始碼內容 —— 舊列可能缺原始碼,必須作廢;環境指紋的作業系統只取種類+架構、不取版本;git 版本與設定只進跑了 git 的測試(tool:git)(兩項皆 the maintainer 09-29);v3.7:AIB_RELEASE_FREEZE_MAX_SEC/SSH_AUTH_SOCK 不進環境指紋;v3.6:T0-1 python 存在探測 probe:)

# ---- 第 96 項(09-13)子行程政策:非 python 子行程讀了什麼,鉤子看不見 ----------------------------
# ★ 量到的事實(live40-d5f0c23c,量尺 tools/verify/measurability_report.py + 逐列追 argv):
#   40 列 measured 裡 24 列有 bash/git/tmux 等非 python 子行程,其中 11 列跑的是 `bash ./src/cockpit/*.sh`
#   而那支 .sh 不在該列任何 open: 項裡 ⇒ 改了 dm-ctl.sh,鑰匙不變,測試被沿用 —— 綠的、沒訊息、事後查不出來。
#   這正是艦長第 96 項派工第三節寫的最糟形狀:「錯誤地宣稱量得到」。
# ⇒ 政策:一個子行程只有在下列之一成立時才不算「讀了沒量到的東西」:
#   (a) python 且帶鉤子(它自己的讀取會進同一份 trace);
#   (b) 走 PATH 墊片的 cat/cp/head/tail(墊片記內容);
#   (c) 在 CHILD_TRANSPARENT 名單裡(讀的是行程表/時鐘/環境,不讀倉裡的檔);
#   (d) 在 CHILD_STDIN_FILTERS 名單裡且**沒有**路徑引數(只吃 stdin),或有路徑引數而鉤子已把那些檔記成 handoff;
#   (e) git 且**整個在暫存根裡**(cwd / -C / 每個路徑引數都在暫存根;鉤子把那些目錄整棵雜湊成 handoff)——
#       對真倉跑 git 讀的是候選的歷史與工作樹,量不到。
#   其餘(bash/sh/zsh/node/aib/*.sh/…)⇒ unmeasured(why=child-untracked:<exe>)。tmux(除 -V)⇒ external。
#   這會讓現在 40 列裡 24 列翻成 unmeasured:那不是退步,是把本來就沒量到的說成沒量到。
#   bash 子行程的追蹤(DEBUG trap 記 BASH_SOURCE)是下一項,不在 96 裡。
CHILD_SHIMMED = frozenset(("cat", "cp", "head", "tail"))
CHILD_TRANSPARENT = frozenset((
    "ps", "pgrep", "pkill", "kill", "lsof", "top", "uptime", "memory_pressure", "vm_stat", "sysctl", "sw_vers",
    "date", "sleep", "env", "printenv", "uname", "id", "whoami", "hostname", "tput", "stty", "true", "false",
    "echo", "printf", "mktemp", "rm", "rmdir", "mkdir", "touch", "chmod", "mv", "ln", "df",
    "dirname", "basename", "which", "open", "osascript", "say", "test", "[",
    "mkfifo", "clear",   # 10-03:建一個具名管道(同 mkdir/touch)、清畫面(同 tput);都不讀檔
    "chattr",            # 10-04:改檔案屬性(雲端 root 造「寫不進去」用 chattr +i,見 tests/aib_unwritable.py),同 chmod —— 不讀檔內容
    "ioreg",             # 10-04:查硬體登錄(電池溫度),與 sysctl/top 同類 —— 不讀檔、回的是即時系統狀態(依賴它數值的測試本來就不穩)
))
CHILD_STDIN_FILTERS = frozenset(("grep", "sed", "awk", "cut", "sort", "uniq", "tr", "wc", "jq", "tee", "diff", "cmp", "tar", "shasum", "sha256sum", "md5", "base64", "python-config", "comm",
                                  "openssl",    # 10-04:讀的檔都在參數上(-in/-inkey/-signature),同 shasum;隨機只影響輸出
                                  # 10-04(共用驗收 bash A4 抓到):ls/stat/du/readlink/realpath 以前算「不讀檔」,
                                  #   但輸出取決於目錄內容、檔大小、修改時間、連結指向 ⇒ 目錄多一個檔照樣沿用 = 紅藏成綠。
                                  #   改照檔引數規則:參數上的檔雜湊;真樹目錄 ⇒ 量不到(寬的只是慢)。
                                  "ls", "stat", "du", "readlink", "realpath",
                                  # 10-07(外部倉 humanize 實踩:platform.architecture() 叫 `file -b <直譯器>`):
                                  #   輸出只看參數上那支檔的內容(與系統的 magic 資料庫)⇒ 照檔引數規則雜湊
                                  "file"))
# 10-07:pytest-benchmark 載入時(pytest_addoption → utils.get_tag)為 --benchmark-autosave 的預設檔名問的 git/hg。
#   結果只當輸出檔名,不影響紅綠;鉤子確認呼叫鏈(sitecustomize_audit._probe_origin)才標 origin。命令也要一字不差。
PROBE_ORIGIN_ARGVS = {
    "pytest-benchmark-tag": frozenset(tuple(a) for a in (
        ("git", "rev-parse", "--git-dir"), ("git", "config", "--local", "remote.origin.url"),
        ("git", "describe", "--dirty", "--always", "--long", "--abbrev=40"),
        ("git", "show", "-s", '--pretty=format:"%cI"'), ("git", "show", "-s", '--pretty=format:"%aI"'),
        ("git", "rev-parse", "--abbrev-ref", "HEAD"),
        ("hg", "path", "default"), ("hg", "id", "--id", "--debug"), ("hg", "tip", "--template", '"{date|rfc3339date}"'),
        ("hg", "branch"))),
}
# 10-03:xargs 拿掉 —— 它代跑的命令與命令讀的檔都從 stdin 來,xtrace 看不到(`… | xargs grep x` 的 grep 不經過 bash)   # 10-03 加 comm:比兩個排序檔,檔引數照 grep/diff 的規則記


def _bx_filter_reads(argv, cwd, repo, handoffs):
    """10-03 漏洞:bash 裡 `grep x ./data.txt`、`sed -n 1p data.txt` 讀了倉裡的檔,以前整列判量得到、檔內容卻不在鑰匙裡
    (改了 data.txt 照樣沿用)。python 那條路的鉤子會把存在的引數記成 handoff;這裡對齊:
      引數(含 --k=v 的 v)是倉裡存在的檔 ⇒ handoff:路徑:內容雜湊(分析當下讀;沿用時重算也是現讀,同一份規則);
      是倉裡的資料夾 ⇒ 回原因字串(讀整個資料夾,量不到);"." 只在 grep 遞迴或非 grep/sed/awk/jq 時算資料夾。
    暫存根(治具)與衍生檔照舊不記。回 None = 沒問題。"""
    exe = os.path.basename(str(argv[0]))
    recursive = exe == "grep" and any(a in ("-r", "-R", "--recursive") or (a.startswith("-") and not a.startswith("--")
                                                                          and ("r" in a[1:] or "R" in a[1:])) for a in argv[1:])
    for a in argv[1:]:
        a = str(a)
        cand = a.split("=", 1)[1] if a.startswith("-") and "=" in a else (None if a.startswith("-") else a)
        if not cand or "\n" in cand:
            continue
        if cand in (".", "..") and exe in ("grep", "sed", "awk", "jq") and not recursive:
            continue
        ap = os.path.normpath(cand if cand.startswith("/") else os.path.join(cwd or os.getcwd(), cand))
        if is_ephemeral_or_derived(ap, repo) or is_derived(ap):
            continue
        if os.path.isdir(ap):
            return "%s-reads-dir" % exe
        if os.path.isfile(ap):
            digest, _missing = _sha_file_now(ap)
            if not digest:
                return "%s-read-unhashable" % exe
            handoffs.append("handoff:%s:%s" % (normalize_path(repo, ap), digest))
    return None


def _simple_sh_c(script):
    """`sh -c` 的腳本若只是一條命令加輸出導向(>>檔、>檔、2>&1、2>>檔),回那條命令的 argv;否則 None(照舊量不到)。
    保守:有 $ ` 的展開、glob、~、多條命令(; && || | &)、讀入導向(<)、子殼括號 ⇒ 一律 None。
    導向只認「緊貼」的寫法(`2>&1`、`>>x`、`2>>x`)或獨立的 `>`/`>>` 後接目標;`… 2 >>x` 的 2 是參數不是檔號。"""
    s = str(script)
    if any(c in s for c in "$`\n\\") or not s.strip():
        return None
    try:
        toks = shlex.split(s, posix=True)
    except ValueError:
        return None
    words = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in (">", ">>"):
            if i + 1 >= len(toks) or not toks[i + 1] or any(c in toks[i + 1] for c in ";|&<>()"):
                return None
            i += 2
            continue
        if re.fullmatch(r"\d?>&\d", t) or re.fullmatch(r"\d?>>?[^;|&<>()]+", t):
            i += 1
            continue
        if any(c in t for c in ";|&<>()*?[") or t.startswith("~"):
            return None
        words.append(t)
        i += 1
    return words or None


def _child_exe(argv):
    exe = os.path.basename(str(argv[0])) if argv else ""
    return exe


def _path_args(argv, cwd):
    """argv[1:] 裡看起來是路徑的引數(絕對、./、../、含 / 的相對),以 cwd 解析成絕對;不查存在。"""
    out = []
    for a in argv[1:]:
        a = str(a)
        if a.startswith("-"):
            if "=" in a:
                a = a.split("=", 1)[1]
            else:
                continue
        if a.startswith("/") or a.startswith(("./", "../")) or ("/" in a and not a.startswith("<")):
            out.append(os.path.abspath(os.path.join(cwd or os.getcwd(), a)) if not a.startswith("/") else a)
    return out


# 只讀倉(物件、refs、設定)、不碰工作樹的 git 子命令:配 --git-dir 時 cwd 不影響結果
_GIT_NO_WORKTREE = frozenset(("log", "branch", "show", "rev-parse", "rev-list", "cat-file", "for-each-ref", "show-ref",
                              "ls-tree", "tag", "config", "merge-base", "describe", "shortlog", "count-objects", "fsck"))


# ── 10-04:對真倉的唯讀歷史查詢(git log / rev-list / …)⇒ 不算量不到,改把「倉的狀態」記成輸入 githead:<雜湊> ──
#   第二輪卡點排行第一(child-untracked:git 65 支):剩下的是真的讀真倉的歷史(theme 的 `git log --format=%s -400`)。
#   結果只看 HEAD 指到的提交(與它的祖先)⇒ 輸入 = HEAD 的 sha + 倉自己的設定檔內容;換提交 ⇒ 鑰匙變 ⇒ 必重跑。
#   刻意收窄(寧可多量不到,不准錯沿用):
#     - 版本只收 HEAD / HEAD~N / HEAD^N / 7~40 位 sha,與它們的 a..b、a...b;分支名、--all、--decorate 都不收
#       (分支各機器不同,收了就要把 refs 也記進來,跨機器就比不到)
#     - 選項只收白名單裡的格式/篩選;會產生 diff 的(-p、--stat…)不收:diff 會讀工作樹的 .gitattributes
#     - status / diff / ls-files 等看工作樹、索引的子命令不在名單
_GIT_REV_RE = re.compile(r"^(HEAD|[0-9a-f]{7,40})([~^][0-9]*)*(\^\{(commit|tree|object)?\})?$")
# 10-04 晚 gitrefs:指名的分支/標籤(main、origin/main、v1.2)。只在 gitrefs 模式收(狀態另加全部 ref)
_GIT_REF_RE = re.compile(r"^(?!-)(?!.*\.\.)(?!.*\.lock(?:[~^]|$))[A-Za-z0-9][A-Za-z0-9._/-]*([~^][0-9]*)*(\^\{(commit|tree|object)?\})?$")
_GIT_HIST_SUBS = {
    "log": (frozenset(("--oneline", "--no-merges", "--merges", "--reverse", "--first-parent", "-z", "--no-color",
                       "--abbrev-commit", "--topo-order", "--date-order", "--no-decorate", "--decorate=no")),
            ("--format=", "--pretty=", "--max-count=", "--date=", "--since=", "--until=", "--after=", "--before=",
             "--author=", "--committer=", "--grep=", "--abbrev=", "--skip=")),
    "rev-list": (frozenset(("--count", "--reverse", "--first-parent", "--no-merges", "--merges", "--topo-order")),
                 ("--max-count=", "--since=", "--until=", "--skip=", "--max-parents=", "--min-parents=")),
    "shortlog": (frozenset(("-s", "-n", "-e", "-sn", "-ns", "--summary", "--numbered", "--email")), ()),
    "rev-parse": (frozenset(("--verify", "--short", "-q", "--quiet", "--git-common-dir", "--git-dir", "--absolute-git-dir",
                             "--show-toplevel", "--is-inside-work-tree")), ("--short=",)),
    # 10-04 第二批:ls-files 只收「索引裡有哪些檔」(不收 -m/-d/-o/--others 等看工作樹的);索引內容進 githead 狀態
    "ls-files": (frozenset(("-z", "--full-name", "-s", "--stage", "--cached", "-c")), ()),
    # show 只收 <版本>:<路徑>(那個提交裡那支檔的內容,不產生 diff);見 git_history_read_of_repo
    "show": (frozenset(), ()),
    "merge-base": (frozenset(("--is-ancestor",)), ()),
    # 10-04 晚(gitwt 量測後剩下的 35 支):都只讀物件,不碰工作樹;版本一樣要是 HEAD 的祖先(見 _git_rev_reachable)
    #   cat-file 只收 -e/-t/-s/-p 加一個 <版本>[:<路徑>];ls-tree 收列名單的選項 + 一個版本 + pathspec;
    #   diff-tree 只收列檔名(--name-only/--name-status,不產生內容 diff)+ 版本;show -s/--no-patch 只看提交本身
    "cat-file": (frozenset(("-e", "-t", "-s", "-p")), ()),
    "ls-tree": (frozenset(("-r", "-d", "-t", "-z", "--name-only", "--name-status", "--full-name", "--full-tree",
                           "-l", "--long", "--object-only")), ()),
    "diff-tree": (frozenset(("--root", "--no-commit-id", "--name-only", "--name-status", "-r", "-z", "-t")), ()),
}
# log 的 --diff-filter= 只篩「這個提交動了哪些檔」(物件裡的樹比樹,改名偵測看 diff.renames 設定 —— 設定在狀態裡)
_GIT_HIST_SUBS["log"] = (_GIT_HIST_SUBS["log"][0] | frozenset(("--name-only", "--name-status")),
                         _GIT_HIST_SUBS["log"][1] + ("--diff-filter=",))
# rev-parse 的路徑格式與分支名(--abbrev-ref HEAD;HEAD 指哪個分支已進狀態)
_GIT_HIST_SUBS["rev-parse"] = (_GIT_HIST_SUBS["rev-parse"][0] | frozenset(("--abbrev-ref", "--symbolic-full-name",
                                                                            "--show-prefix", "--show-cdup")),
                               _GIT_HIST_SUBS["rev-parse"][1] + ("--path-format=",))
# interpret-trailers 不讀倉,只讀 stdin(測試給的)與 trailer.* 設定(在狀態裡);給了檔名引數 ⇒ 不收
# 10-04 晚:git config 只收「讀」:--get/--get-all/--get-regexp/--list,或只給一個鍵;全部生效的設定已在狀態裡
_GIT_CONFIG_READ = frozenset(("--get", "--get-all", "--get-regexp", "--list", "-l", "-z", "--null", "--name-only",
                              "--bool", "--int", "--bool-or-int", "--local", "--global", "--system", "--worktree",
                              "--show-origin", "--show-scope", "--includes"))
_GIT_CONFIG_READ_PFX = ("--type=", "--default=")
# gitrefs 模式另外收的(列 ref 本身):tag -l、for-each-ref
_GIT_REF_LIST_OPTS = frozenset(("-l", "--list", "--sort=refname", "--sort=-refname", "--sort=version:refname",
                                "--sort=-version:refname", "--merged", "--no-merged"))
_GIT_TRAILER_OPTS = frozenset(("--parse", "--only-trailers", "--only-input", "--unfold", "--no-divider"))


# ── 10-04 全套第二輪:只卡 git 的 43 支裡,真倉上最常見的是 grep / status / diff —— 讀的是「工作樹現在的樣子」,
#   githead(只認歷史)接不住。改記 gitwt:<狀態>:HEAD+索引+設定(githead 那份)+ 整個工作樹相對 HEAD 的差異 + 未追蹤檔清單。
#   倉裡任何一個檔有動 ⇒ 狀態變 ⇒ 必重跑(粗,但不可能藏紅;只有整個倉都沒動才沿用)。
#   不收:全域選項(-c…)、會叫外部程式的(--ext-diff/--textconv)、讀倉外或被忽略檔的(--no-index/--untracked/--ignored/
#   --no-exclude-standard)、開編輯器/分頁器的(-O)、絕對路徑或 .. 的路徑引數。
_GIT_WT_SUBS = ("grep", "status", "diff")
_GIT_WT_BAD = ("--ext-diff", "--textconv", "--no-index", "--untracked", "--ignored", "--no-exclude-standard",
               "-O", "--open-files-in-pager", "--output", "--exclude-standard=")


def git_worktree_read_of_repo(argv, scope, gpaths, repo):
    """argv 是否為「對 repo 本身、只讀工作樹」的 grep/status/diff(見上方註解)。"""
    if not repo or not scope:
        return False
    try:
        real_repo = os.path.realpath(str(repo))
        if os.path.realpath(scope) != real_repo or any(os.path.realpath(p) != real_repo for p in gpaths):
            return False
    except (OSError, ValueError):
        return False
    args = [str(a) for a in argv[1:]]
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-C" and i + 1 < len(args):
            i += 2
            continue
        return False
    if i >= len(args) or args[i] not in _GIT_WT_SUBS:
        return False
    seen_dd = False
    for a in args[i + 1:]:
        if a == "--":
            seen_dd = True
            continue
        if not seen_dd and a.startswith("-"):
            if a in _GIT_WT_BAD or any(a.startswith(b.rstrip("=") + "=") for b in _GIT_WT_BAD) or a.startswith("--output="):
                return False
            continue
        if a.startswith("/") or ".." in a.split("/"):
            return False
    return True


def git_worktree_state(repo):
    """gitwt 輸入的值:githead 那份 + `git diff HEAD --binary`(工作樹與索引相對 HEAD 的全部差異)+ 未追蹤檔清單。
    量不到 ⇒ 每次不同(安全側)。"""
    try:
        def _g(*a):
            p = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=false"] + list(a), capture_output=True,
                               timeout=60, stdin=subprocess.DEVNULL)
            if p.returncode != 0:
                raise OSError(p.stderr.decode("utf-8", "replace").strip())
            return p.stdout
        h = hashlib.sha256(git_repo_state(repo).encode("utf-8"))
        h.update(b"\0diff\0" + _g("diff", "HEAD", "--binary", "--no-ext-diff", "--no-textconv"))
        h.update(b"\0untracked\0" + _g("ls-files", "--others", "--exclude-standard", "-z"))
        return h.hexdigest()
    except (OSError, ValueError, subprocess.SubprocessError):
        return "unknown-%s" % os.urandom(8).hex()


_REACH_CACHE = {}
_TRAILER_CMD_CACHE = {}
# 10-04 晚:格式字串裡會印出 ref 名字(%d %D:裝飾、%S:從哪個 ref 走到)或叫 gpg 驗簽(%G…)的,不收 ——
#   ref 不在 githead 狀態裡(各機器不同),gpg 是外部程式
_GIT_FMT_BAD = re.compile(r"%(?:[dDS]|G.)")


def _git_fmt_ok(a):
    if a.startswith(("--format=", "--pretty=")):
        return not _GIT_FMT_BAD.search(a.split("=", 1)[1])
    return a not in ("--decorate", "--source", "--show-signature")


def _git_trailer_cmd(repo):
    """設定裡有 trailer.<x>.cmd / .command ⇒ interpret-trailers 會跑外部指令 ⇒ True(不收)。查不到也當有。"""
    key = str(repo)
    if key not in _TRAILER_CMD_CACHE:
        try:
            p = subprocess.run(["git", "-C", key, "config", "--get-regexp", r"^trailer\..*\.(cmd|command)$"],
                               capture_output=True, timeout=20, stdin=subprocess.DEVNULL)
            _TRAILER_CMD_CACHE[key] = p.returncode != 1      # 1 = 沒有這種設定;0 = 有;其他 = 查不到
        except (OSError, subprocess.SubprocessError):
            _TRAILER_CMD_CACHE[key] = True
    return _TRAILER_CMD_CACHE[key]


def _git_rev_reachable(repo, rev):
    """10-04 晚:寫死的 sha 要是 HEAD 的祖先(或 HEAD 本身)才收 —— 那時結果只由 HEAD(在狀態裡)決定;
    不是祖先的物件,另一台機器上可能有、可能沒有(分支沒抓、gc 掉),同一個 HEAD 也會得到不同結果。
    HEAD / HEAD~N 一律是。查不到 ⇒ 不收(安全側)。"""
    base = re.split(r"[~^]", rev, 1)[0]
    if base == "HEAD" or not repo:
        return base == "HEAD"
    key = (str(repo), base)
    if key not in _REACH_CACHE:
        try:
            p = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", base, "HEAD"],
                               capture_output=True, timeout=20, stdin=subprocess.DEVNULL)
            _REACH_CACHE[key] = p.returncode == 0
        except (OSError, subprocess.SubprocessError):
            _REACH_CACHE[key] = False
    return _REACH_CACHE[key]


def _git_rev_ok(tok, repo=None, refs=False):
    for part in re.split(r"\.\.\.?", tok):
        part = part or "HEAD"
        if _GIT_REV_RE.match(part):
            if not _git_rev_reachable(repo, part):
                return False
        elif not (refs and _GIT_REF_RE.match(part)):
            return False                   # 指名的 ref:只在 gitrefs 模式(全部 ref 已進狀態)收
    return True


def git_history_read_of_repo(argv, scope, gpaths, repo, refs=False):
    """argv 是否為「對 repo 本身的唯讀歷史查詢」(見上方註解);是 ⇒ True(分類成 githead)。
    refs=True(10-04 晚):也收指名的分支/標籤、tag -l、for-each-ref(分類成 gitrefs,狀態另加全部 ref)。"""
    if not repo or not scope:
        return False
    try:
        real_repo = os.path.realpath(str(repo))
        if os.path.realpath(scope) != real_repo:
            return False
        if any(os.path.realpath(p) != real_repo for p in gpaths):   # -C <倉> 本身會出現在 gpaths;別的路徑一律不收
            return False
    except (OSError, ValueError):
        return False
    args = [str(a) for a in argv[1:]]
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-C" and i + 1 < len(args):
            i += 2
            continue
        return False                       # 其他全域選項(-c、--git-dir、--work-tree…)一律不收
    if i < len(args) and args[i] == "interpret-trailers":
        return bool(args[i + 1:]) and all(a in _GIT_TRAILER_OPTS for a in args[i + 1:]) and not _git_trailer_cmd(repo)
    if i < len(args) and args[i] == "config":
        pos = [a for a in args[i + 1:] if not a.startswith("-")]
        opts = [a for a in args[i + 1:] if a.startswith("-")]
        if not all(a in _GIT_CONFIG_READ or a.startswith(_GIT_CONFIG_READ_PFX) for a in opts):
            return False
        if "--list" in opts or "-l" in opts:
            return not pos
        return len(pos) == 1 or (len(pos) == 2 and bool({"--get", "--get-all", "--get-regexp"} & set(opts)))
    if refs and i < len(args) and args[i] in ("tag", "for-each-ref"):
        rest = args[i + 1:]
        if args[i] == "tag" and not ({"-l", "--list"} & set(rest)):
            return False                   # tag 不帶 -l 有參數 = 建標籤
        for a in rest:
            if a.startswith("-"):
                if not (a in _GIT_REF_LIST_OPTS or (args[i] == "for-each-ref" and a.startswith("--format=")
                                                    and _git_fmt_ok(a)) or a.startswith("--count=")):
                    return False
            elif not re.match(r"^[A-Za-z0-9*?][A-Za-z0-9._/*?-]*$", a):
                return False
        return True
    if i >= len(args) or args[i] not in _GIT_HIST_SUBS:
        return False
    exact, prefixes = _GIT_HIST_SUBS[args[i]]
    rest = args[i + 1:]
    if not all(_git_fmt_ok(a) for a in rest if a.startswith("-")):
        return False

    def _relpath_ok(path):
        return bool(path) and not path.startswith("/") and ".." not in path.split("/")

    def _obj_ok(a):
        rev, sep, path = a.partition(":")
        return _git_rev_ok(rev, repo, refs) and (not sep or _relpath_ok(path))
    if args[i] == "show":
        # 每個引數都要是 <版本>:<相對路徑>(版本照樣只收 HEAD/sha 樣式;路徑不准絕對、不准 ..)
        # 10-04 晚:或 -s/--no-patch(只看提交本身,不產生 diff)+ 格式選項 + 版本
        if not rest:
            return False
        if "-s" in rest or "--no-patch" in rest:
            revs = [a for a in rest if not a.startswith("-")]
            opts = [a for a in rest if a.startswith("-")]
            return bool(revs) and all(_git_rev_ok(a, repo, refs) for a in revs) and all(
                a in ("-s", "--no-patch") or a.startswith(("--format=", "--pretty=", "--date=")) for a in opts)
        for a in rest:
            rev, sep, path = a.partition(":")
            if not sep or not path or path.startswith("/") or ".." in path.split("/") or not _git_rev_ok(rev, repo, refs):
                return False
        return True
    if args[i] == "cat-file":
        opts = [a for a in rest if a.startswith("-")]
        objs = [a for a in rest if not a.startswith("-")]
        return len(opts) == 1 and opts[0] in exact and len(objs) == 1 and _obj_ok(objs[0])
    if args[i] in ("ls-tree", "diff-tree"):
        revs, paths, seen_dd = [], [], False
        for a in rest:
            if a == "--" and not seen_dd:
                seen_dd = True
            elif not seen_dd and a.startswith("-"):
                if a not in exact:
                    return False
            elif seen_dd or revs and args[i] == "ls-tree":
                if not _relpath_ok(a):
                    return False
                paths.append(a)
            else:
                if not _git_rev_ok(a, repo, refs):
                    return False
                revs.append(a)
        if args[i] == "diff-tree" and not ({"--name-only", "--name-status"} & set(rest)):
            return False                   # 沒有只列檔名 ⇒ 會產生內容 diff(讀 .gitattributes、外部 diff 驅動)
        return len(revs) == (1 if args[i] == "ls-tree" else len(revs)) and bool(revs) and len(revs) <= 2
    if args[i] == "ls-files":
        for a in rest:
            if a == "--":
                continue
            if a.startswith("-"):
                if a not in exact:
                    return False
            elif a.startswith("/") or ".." in a.split("/"):
                return False
        return True
    j = 0
    while j < len(rest):
        a = rest[j]
        if a == "--":
            return args[i] in ("log", "rev-list", "shortlog") and all(not p.startswith("-") and _relpath_ok(p) for p in rest[j + 1:])
        if a in exact or any(a.startswith(px) for px in prefixes):
            j += 1
            continue
        if args[i] in ("log", "rev-list") and (re.match(r"^-[0-9]+$", a) or re.match(r"^-n[0-9]+$", a)):
            j += 1
            continue
        if args[i] in ("log", "rev-list") and a == "-n" and j + 1 < len(rest) and rest[j + 1].isdigit():
            j += 2
            continue
        if a.startswith("-") or not _git_rev_ok(a, repo, refs):
            return False
        j += 1
    return True


_GIT_CLONE_OPTS = frozenset(("-q", "--quiet", "-s", "--shared", "-l", "--local", "--no-hardlinks", "-n", "--no-checkout"))


def git_ref_tokens(argv):
    """gitrefs 指令裡「指名的 ref」(不是 HEAD/sha 的那些版本字):倉根若有同名的檔/目錄,git 會改當路徑或報模糊 ⇒
    結果跟著工作樹變 ⇒ 收尾把倉根同名路徑的存在狀態記成 probe 輸入(沿用時重查)。"""
    out = set()
    args = [str(x) for x in argv[1:]]
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] == "-C" else 1
    for a in args[i + 1:]:                 # 子命令本身不算
        if a == "--":
            break                          # 之後是路徑
        if a.startswith(("-", "/")):
            continue
        for part in re.split(r"\.\.\.?", a.partition(":")[0]):
            base = re.split(r"[~^]", part, 1)[0]
            if base and not _GIT_REV_RE.match(part) and _GIT_REF_RE.match(part) and "*" not in base:
                out.add(base)
    return out


def git_clone_of_repo(argv, repo, fixture):
    """10-04 晚:`git clone [名單選項] <本倉> <治具裡的目標>` ⇒ gitrefs(結果 = 本倉的物件 + 全部 ref + HEAD,都在 gitrefs 狀態裡;
    不讀本倉的工作樹)。來源要正好是本倉(絕對路徑),目標要在治具裡;其他寫法照舊量不到。"""
    if not repo:
        return False
    args = [str(a) for a in argv[1:]]
    if not args or args[0] != "clone":
        return False
    pos = [a for a in args[1:] if not a.startswith("-")]
    opts = [a for a in args[1:] if a.startswith("-")]
    if len(pos) != 2 or not all(a in _GIT_CLONE_OPTS for a in opts) or not pos[0].startswith("/") or not pos[1].startswith("/"):
        return False
    try:
        return os.path.realpath(pos[0]) == os.path.realpath(str(repo)) and fixture(pos[1])
    except (OSError, ValueError):
        return False


def git_refs_state(repo):
    """gitrefs 輸入的值:githead 那份 + 全部 ref(名字、指到的物件、符號 ref)。分支一動、多抓一次 origin ⇒ 變 ⇒ 必重跑。"""
    try:
        p = subprocess.run(["git", "-C", str(repo), "for-each-ref", "--format=%(refname)%00%(objectname)%00%(symref)"],
                           capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            raise OSError("for-each-ref")
        h = hashlib.sha256(git_repo_state(repo).encode("utf-8"))
        h.update(b"\0refs\0" + p.stdout)
        return h.hexdigest()
    except (OSError, ValueError, subprocess.SubprocessError):
        return "unknown-%s" % os.urandom(8).hex()


# ── 10-05:git 查詢改記「那條查詢的結果」,不記整個倉的狀態 ─────────────────────────────────────────────────
#   回放量尺:githead/gitwt/gitrefs 讓 54 支測試每個提交都重跑(HEAD 一定變),平均省時上限 +4.1 點。
#   這些查詢都是唯讀(分類已只收唯讀的形),結果只由倉的內容決定 ⇒ 判沿用時把同一條再跑一次,stdout/stderr/rc 一樣
#   ⇒ 測試看到的一樣。記錄與判斷**用同一個跑法**(空的家目錄、固定語系、不帶測試的環境),顯示格式不會讓兩邊對不上。
#   不收:讀 stdin 的(interpret-trailers,結果看輸入)⇒ 照舊記整個倉的狀態。
_GITQ_HOME = None


def git_query_ok(argv):
    return not any(str(a) == "interpret-trailers" for a in argv)


def git_query_norm(argv, cwd, repo):
    root = os.path.realpath(str(repo))

    def _n(x):
        x = str(x)
        for r in {str(repo), root}:
            if x == r:
                return "<REPO>"
            if x.startswith(r + "/"):
                return "<REPO>" + x[len(r):]
        return x
    a = [os.path.basename(argv[0]) if argv and os.path.basename(str(argv[0])) == "git" else argv[0]] + [_n(x) for x in argv[1:]]
    return a, _n(cwd) if cwd else "<REPO>"


def git_query_digest(repo, argv, cwd):
    global _GITQ_HOME
    try:
        root = str(repo)
        a = [str(x).replace("<REPO>", root) for x in argv]
        c = str(cwd).replace("<REPO>", root)
        if _GITQ_HOME is None:
            import atexit
            import shutil
            _GITQ_HOME = tempfile.mkdtemp(prefix="aib-gitq-home-")
            atexit.register(shutil.rmtree, _GITQ_HOME, True)   # 10-09:以前每個程序在使用者的暫存區留一個(aib test 點名)
        env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": _GITQ_HOME, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
               "GIT_TERMINAL_PROMPT": "0", "TZ": "UTC"}
        p = subprocess.run(a, cwd=c, env=env, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        h = hashlib.sha256(b"rc=%d\0" % p.returncode)
        h.update(p.stdout)
        h.update(b"\0err\0" + p.stderr)
        return h.hexdigest()
    except (OSError, ValueError, subprocess.SubprocessError):
        return "unknown-%s" % os.urandom(8).hex()


def git_repo_state(repo):
    """githead 輸入的值:HEAD 的 sha + 倉設定檔(共用 config 與工作樹 config)的內容 + git 目錄/工作樹路徑 + 索引(ls-files -s)。
    量不到 ⇒ 每次不同(安全側:必重跑)。"""
    try:
        def _g(*a):
            p = subprocess.run(["git", "-C", str(repo)] + list(a), capture_output=True, text=True, timeout=20,
                               stdin=subprocess.DEVNULL)
            if p.returncode != 0:
                raise OSError(p.stderr.strip())
            return p.stdout.strip()
        head = _g("rev-parse", "HEAD")
        common = _g("rev-parse", "--git-common-dir")
        gdir = _g("rev-parse", "--git-dir")
        top = _g("rev-parse", "--show-toplevel")
        h = hashlib.sha256(head.encode("utf-8"))
        # 10-04 第二批:rev-parse 的路徑旗標、ls-files(索引)也收了 ⇒ 佈局路徑與索引內容也進狀態
        h.update(("\0%s\0%s\0%s" % (common, gdir, top)).encode("utf-8"))
        h.update(b"\0index\0" + _g("ls-files", "-s").encode("utf-8"))
        # 10-04 晚:HEAD 指哪個分支(rev-parse --abbrev-ref HEAD 收了)、全部生效的設定(含全域/系統:log 格式、
        #   diff.renames、trailer.* 都可能來自那裡)。用現在這個行程的環境去讀 —— 測試若換了 HOME,讀到的是本機全域設定,
        #   本機設定沒變就穩定、變了就重跑(安全側)
        sym = subprocess.run(["git", "-C", str(repo), "symbolic-ref", "-q", "HEAD"], capture_output=True, text=True,
                             timeout=20, stdin=subprocess.DEVNULL)
        h.update(("\0sym\0%d:%s" % (sym.returncode, sym.stdout.strip())).encode("utf-8"))
        h.update(b"\0cfg\0" + _g("config", "--list", "--show-origin", "-z").encode("utf-8"))
        for d in (common, gdir):
            d = d if os.path.isabs(d) else os.path.join(str(repo), d)
            for name in ("config", "config.worktree"):
                f = os.path.join(d, name)
                h.update(b"\0" + name.encode() + b"\0")
                if os.path.isfile(f):
                    with open(f, "rb") as fh:
                        h.update(fh.read())
        return h.hexdigest()
    except (OSError, ValueError, subprocess.SubprocessError):
        return "unknown-%s" % os.urandom(8).hex()



def _git_scope_and_paths(argv, cwd):
    """10-03:git 的「在哪裡跑」與「可能跳出那裡的路徑引數」。
    scope = 依序套用每個 -C(相對的對前一個解)後的目錄;沒有 -C 就是 cwd(沒有 cwd ⇒ None)。
    會被檢查的路徑只有兩種:絕對路徑(含 --opt=/abs)、含 `..` 一段的相對路徑(對 scope 解)。
    其餘相對引數(`HEAD:refs/heads/x`、`sha:path`、`a...b`、分支名 `ds/1bf70`、pathspec `src/x`)一律落在 scope 裡,
    不再被當成「對行程 cwd 解的路徑」(以前這樣把治具裡的 git 誤判成讀真庫)。scope 不在治具 ⇒ 照舊 untracked。"""
    args = [str(a) for a in argv[1:]]
    scope = cwd
    git_dir = work_tree = None
    i = 0
    while i < len(args) and args[i].startswith("-") and args[i] != "--":
        a = args[i]
        if a == "-C" and i + 1 < len(args):
            c = args[i + 1]
            base = scope or os.getcwd()
            scope = c if c.startswith("/") else os.path.abspath(os.path.join(base, c))
            i += 2
            continue
        if a in ("--git-dir", "--work-tree") and i + 1 < len(args):
            a, v = a, args[i + 1]
            i += 2
        elif a.startswith(("--git-dir=", "--work-tree=")):
            a, v = a.split("=", 1)
            i += 1
        else:
            if a in ("-c", "--namespace", "--exec-path", "--super-prefix", "--config-env") and i + 1 < len(args) and "=" not in a:
                i += 2
            else:
                i += 1
            continue
        v = v if v.startswith("/") else os.path.abspath(os.path.join(scope or os.getcwd(), v))
        if a == "--git-dir":
            git_dir = v
        else:
            work_tree = v
    sub = args[i] if i < len(args) else ""
    if git_dir:
        # --git-dir 給了:倉就是它。工作樹 = --work-tree,沒給就是 cwd ——
        #   只有不碰工作樹的指令(log/branch/show…)才只看倉;其餘照舊要 cwd(或 --work-tree)也在治具裡。
        if work_tree or sub not in _GIT_NO_WORKTREE:
            extra = work_tree or scope
        else:
            extra = None
        scope = git_dir
    elif work_tree:
        extra = work_tree
    else:
        extra = None
    # init / clone 作用在「目標目錄」,不讀 cwd 的倉 ⇒ 範圍改成目標目錄(clone 的來源若是真庫絕對路徑,下面照樣檢查)
    if i < len(args) and args[i] in ("init", "clone"):
        pos = [a for a in args[i + 1:] if not a.startswith("-")]
        if pos:
            t = pos[-1]
            scope = t if t.startswith("/") else os.path.abspath(os.path.join(scope or os.getcwd(), t))
    paths = []
    for a in args:
        cands = [a] + ([a.split("=", 1)[1]] if "=" in a else [])   # --git-dir=/x、-c core.hooksPath=/x 的值也看
        for v in cands:
            if v == "/dev/null":
                continue             # 10-04 晚:`-c core.hooksPath=/dev/null`、`config core.hooksPath /dev/null` —— 不是誰的檔
            if v.startswith("/"):
                paths.append(os.path.normpath(v))
            elif v == ".." or v.startswith("../") or "/../" in v or v.endswith("/.."):
                paths.append(os.path.normpath(os.path.join(scope or os.getcwd(), v)))
    if extra:
        paths.append(extra)          # 工作樹(--work-tree 或 --git-dir 配 cwd)也要在治具裡
    return scope, paths


def classify_child(ev, repo=None):
    """回 (kind, detail):kind ∈ {"ok", "python", "untracked", "external"}。純函式,只看 argv 事件。"""
    argv = [str(x) for x in (ev.get("argv") or [])]
    if not argv:
        return "untracked", "empty-argv"
    exe = _child_exe(argv)
    cwd = ev.get("cwd")
    if exe == "flock":
        inner, _sw_why, _cwd = _unwrap(argv, cwd)
        if inner is None:
            return "untracked", _sw_why
        if not inner:
            return "ok", "flock"
        return classify_child(dict(ev, argv=inner, cwd=_cwd), repo=repo)
    if exe == "env":
        # 10-02:env 不再整個算 ok —— 拆出真正的程式再分類;只有「沒有命令」(印環境)才是 ok。
        inner, _sw_why, _cwd = _unwrap(argv, cwd)
        if inner is None:
            return "untracked", _sw_why
        if not inner:
            return "ok", "env"
        sub = dict(ev, argv=inner, cwd=_cwd)
        if os.path.basename(inner[0]) != "bash":
            sub.pop("shell", None)
        return classify_child(sub, repo=repo)
    if exe in ("sh", "dash") and len(argv) == 3 and argv[1] == "-c":
        # 10-04:最單純的 `sh -c '<一條命令> [>>檔] [2>&1]'` 拆開看裡面那條(pending-send-now-env-r15 等實踩:
        #   `sh -c 'bash mouse-ctl.sh … >>aib-open.log 2>&1'`)。其餘寫法(; | && $ ` < glob …)照舊量不到。
        #   裡面是 bash ⇒ 要記錄端的 bash_hooked(那個環境的 bash 有沒有鉤子);沒記或 False ⇒ 量不到。
        inner = _simple_sh_c(argv[2])
        if inner:
            sub = {k: v for k, v in ev.items() if k not in ("shell", "hooked")}
            sub["argv"] = inner
            if os.path.basename(inner[0]) == "bash":
                sub["shell"] = "bash"
                sub["hooked"] = ev.get("bash_hooked") is True
            else:
                sub["hooked"] = ev.get("hooked")
            return classify_child(sub, repo=repo)
    if exe == "python" or exe.startswith("python"):
        return "python", exe
    if ev.get("shell") == "bash" or exe == "bash":
        # 96b:帶 BASH_ENV 鉤子的 bash 子行程自己會記 source/讀取/子命令;沒鉤子的照舊 untracked。
        # bash -n 只解析不執行(不載 BASH_ENV、沒有 inherit):它讀的只有那支腳本,而腳本已被 handoff 雜湊 ⇒ ok
        if "-n" in argv[1:] and "-c" not in argv[1:]:
            return "ok", "bash-n"
        return ("bash", "bash") if ev.get("hooked") else ("untracked", "bash")
    if exe in CHILD_SHIMMED:
        return "ok", exe
    if exe in CHILD_TRANSPARENT:
        return "ok", exe
    if exe in ("node", "git") and argv[1:] in (["--version"], ["-v"]):
        # 10-04:只問版本(追蹤器自己的環境指紋探測 _tool_version 就是這樣叫;不讀任何檔,版本已在環境指紋)。同 tmux -V。
        return "ok", exe
    if exe == "node":
        # 10-04 Node 接頭:掛上了(hooked)⇒ 跟 python 子行程同一套對帳(要看到它的 inherit/exit);沒掛上 ⇒ 量不到
        return ("python", "node") if ev.get("hooked") else ("untracked", "node")
    if exe == "tmux":
        return ("ok", exe) if argv[1:] == ["-V"] else ("external", "tmux")
    if exe in ("git", "hg") and tuple(argv) in PROBE_ORIGIN_ARGVS.get(str(ev.get("origin") or ""), ()):
        return "ok", str(ev.get("origin"))
    paths = _path_args(argv, cwd)
    # 「在治具裡」= 在暫存根底下**而且不在倉底下**(證詞的拋棄式工作樹本身就住在 /private/tmp,
    #  repo 給了就靠 is_ephemeral_or_derived 的「倉底下永遠是候選的輸入」把它排除)。
    def _fixture(p):
        return is_ephemeral_or_derived(p, repo)
    if exe in CHILD_STDIN_FILTERS:
        # 檔引數:鉤子在交出去當下記 handoff。目錄引數只有治具目錄會被整棵雜湊,真樹目錄 ⇒ untracked。
        if any(os.path.isdir(p) and not _fixture(p) for p in paths):
            return "untracked", exe
        return "ok", exe
    if exe == "git":
        genv = ev.get("gitenv") or {}
        scope, gpaths = _git_scope_and_paths(argv, cwd)

        # 10-03:git 的路徑先砍掉 `/.git` 之後的部分再判:is_ephemeral_or_derived 把含 `.git` 一段的路徑一律當衍生檔
        #   (= 治具),`--git-dir=/真庫/.git` 就會被放行 —— 那是讀真庫。用倉的那一層判在不在治具。
        def _gfix(p):
            return _fixture(re.split(r"/\.git(?=/|$)", p, 1)[0] or p)
        if genv:
            # 10-05:環境裡有改倉的變數(GIT_DIR…)⇒ 只有「值都知道、命令在治具、路徑類變數全部指到治具」才算 ok;其餘量不到
            #   (不知道值 = bash 起來時就帶著、或腳本自己設過 ⇒ "_unknown")
            in_fixture = bool(scope) and _gfix(scope) and all(_gfix(p) for p in gpaths)
            known = not genv.get("_unknown")
            paths_in_fixture = all(isinstance(v, str) and v and all(x == "/dev/null" or (x.startswith("/") and _gfix(x))
                                                                     for x in v.split(os.pathsep) if x)
                                   for k, v in genv.items() if k in _GIT_ENV_PATHS)   # /dev/null = 關掉那份設定,不是別的倉
            if in_fixture and known and paths_in_fixture:
                return "ok", exe
            return "untracked", "git-env"
        if scope and _gfix(scope) and all(_gfix(p) for p in gpaths):
            return "ok", exe
        if not scope and gpaths and all(_gfix(p) for p in gpaths):
            return "ok", exe
        if git_history_read_of_repo(argv, scope, gpaths, repo):
            return "githead", exe
        if git_worktree_read_of_repo(argv, scope, gpaths, repo):
            return "gitwt", exe
        if git_history_read_of_repo(argv, scope, gpaths, repo, refs=True) or git_clone_of_repo(argv, repo, _gfix):
            return "gitrefs", exe
        return "untracked", exe
    return "untracked", exe


def load_env_file(path):
    """讀 `env > 檔` 的輸出成 dict(驗證腳本把**真正要給子行程的環境**倒出來給 decide/fingerprint;
    ef56b 3.2:環境摘要要來自受測 child 的受控環境,不是事後由 parent 重組)."""
    out = {}
    try:
        for line in jsonl_safe.split_lines(Path(path).read_text(encoding="utf-8", errors="replace")):
            if "=" in line and not line.startswith(" "):
                k, _, v = line.partition("=")
                out[k] = v
    except OSError:
        return None
    return out


# ---- 10-02 雲端 claudebot(docs/PLAN-shadow-cross-device-1002.md 第 1 步):追蹤器程式本身進指紋 -------------------
# 以前代表「追蹤器是哪一版」的只有上面那個手寫字串 TRACER_POLICY_VERSION。改了追蹤器卻忘了改字串 ⇒
# 新舊兩版量出來的列被當成同一套規則的結果。單機時大家用同一份程式,看不出來;跨工作樹、跨機器時,
# 兩邊的追蹤器只要差一點,另一邊的綠就會被誤用 —— 綠的、沒訊息、事後查不出來。
# ⇒ 錄製端(子行程載入的鉤子與墊片)與解讀端(本檔:正規化、指紋公式)的**內容**一律進環境指紋。
#   只看內容不看路徑:同內容的兩棵樹、兩台機器照樣對得上。衍生檔(__pycache__、.pyc)不算。
#   判斷端(reuse_index.py)不進:它在判斷當下一律用現在的程式,不會拿舊程式的判斷去比。
# 代價:追蹤器改了,所有紀錄作廢、下一輪全部實跑一次。寧可慢,不可錯。
# 10-08:Python 檔改用語法樹算(ast.dump,不含行號)—— 只改註解、空白、排版不作廢(那些對行為百分之百沒有影響);
#   說明字串(docstring)照樣算進去(有物件會帶著它)。程式動到一個字照樣全部作廢。bash / Node 墊片照舊整份位元組。
TRACER_CODE_FILES = ("shadow_tracer.py", "sitecustomize.py", "sitecustomize_audit.py", "py-site/sitecustomize.py")
TRACER_SHIM_DIR = "bash-shim"
NODE_SHIM_DIR = "node-shim"            # 10-04:Node 接頭(docs/SPEC-shadow-adapter-1004.md);內容同樣整份進指紋
NODE_AUDIT = Path(__file__).resolve().parent / NODE_SHIM_DIR / "audit.cjs"
_TRACER_DIGEST_CACHE = {}


def _tracer_code_entries(tracer_dir):
    d = Path(tracer_dir)
    out = [(name, d / name) for name in TRACER_CODE_FILES]
    shim = d / TRACER_SHIM_DIR
    try:
        names = sorted(n for n in os.listdir(shim) if not n.startswith(".") and not n.endswith((".pyc", ".pyo")))
    except OSError:
        names = []
    out.extend(("%s/%s" % (TRACER_SHIM_DIR, n), shim / n) for n in names if (shim / n).is_file())
    nshim = d / NODE_SHIM_DIR
    try:
        nnames = sorted(n for n in os.listdir(nshim) if not n.startswith("."))
    except OSError:
        nnames = []
    out.extend(("%s/%s" % (NODE_SHIM_DIR, n), nshim / n) for n in nnames if (nshim / n).is_file())
    return out


def _code_bytes(rel, raw):
    """算指紋用的內容:.py ⇒ 語法樹(不含註解、排版、行號;含說明字串);解析不了或不是 .py ⇒ 原始位元組。"""
    if not rel.endswith(".py"):
        return raw
    try:
        import ast
        return ast.dump(ast.parse(raw), include_attributes=False).encode("utf-8")
    except (SyntaxError, ValueError):
        return raw


def tracer_code_digest(tracer_dir=None):
    """追蹤器程式內容的指紋(16 碼)。缺檔照實記成 missing(缺檔與有檔必不同指紋),不丟例外。"""
    entries = _tracer_code_entries(tracer_dir or HERE)
    stamp = []
    for rel, p in entries:
        try:
            s = p.stat()
            stamp.append((rel, s.st_mtime_ns, s.st_size, s.st_ino))
        except OSError:
            stamp.append((rel, None, None, None))
    ck = (str(Path(tracer_dir or HERE).resolve()), tuple(stamp))
    hit = _TRACER_DIGEST_CACHE.get(ck[0])
    if hit and hit[0] == ck[1]:
        return hit[1]
    h = hashlib.sha256()
    for rel, p in entries:
        h.update(rel.encode("utf-8") + b"\0")
        try:
            h.update(hashlib.sha256(_code_bytes(rel, p.read_bytes())).hexdigest().encode("ascii"))
        except OSError:
            h.update(b"missing")
        h.update(b"\n")
    digest = h.hexdigest()[:16]
    _TRACER_DIGEST_CACHE[ck[0]] = (ck[1], digest)
    return digest


def _tracer_dirs(environ):
    """這份環境實際會載入的追蹤器目錄:本檔所在處,加上 PYTHONPATH 上、BASH_ENV 指到的其他追蹤器目錄。
    合入見證在候選樹錄、在自己的樹算指紋 ⇒ 兩份都要算進去。依實際路徑去重、排序(順序不影響指紋)。"""
    dirs = {str(HERE.resolve())}
    for entry in (environ.get("PYTHONPATH") or "").split(os.pathsep):
        if entry and os.path.isfile(os.path.join(entry, "sitecustomize_audit.py")):
            dirs.add(os.path.realpath(entry))
    be = environ.get("BASH_ENV") or ""
    if be and os.path.basename(os.path.dirname(be)) == TRACER_SHIM_DIR:
        cand = os.path.dirname(os.path.dirname(be))
        if os.path.isfile(os.path.join(cand, "sitecustomize_audit.py")):
            dirs.add(os.path.realpath(cand))
    return sorted(dirs)


# ── 10-04 晚(外部倉實測 itsdangerous):測試用哪一支 python ──────────────────────────────────────────────
#   以前寫死 aib 自己的 python ⇒ 外部專案自己的虛擬環境(裝著 pytest 與專案相依)完全用不到。依序:
#   AIB_TEST_PYTHON(明講)> 倉裡的 .venv/ 或 venv/ > 已啟用的 VIRTUAL_ENV > aib 自己的 python。
#   用哪一支 + 它的版本 + 它有沒有 pytest(決定用 pytest 還是 plain_test_runner 跑)一律進環境指紋。
_PY_ID_CACHE = {}


def test_python(repo=None, environ=None):
    env = os.environ if environ is None else environ
    cands = [env.get("AIB_TEST_PYTHON")]
    if repo:
        cands += [os.path.join(str(repo), d, "bin", "python") for d in (".venv", "venv")]
    if env.get("VIRTUAL_ENV"):
        cands.append(os.path.join(env["VIRTUAL_ENV"], "bin", "python"))
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return sys.executable


def python_id(py):
    """(版本, pytest 版本或 "");問不到 ⇒ ("unknown", "")。用乾淨環境問(不帶追蹤器)。結果依檔案時間快取。"""
    try:
        st_ = os.stat(py)
        # 用「叫的路徑」當鍵,不追捷徑:venv 的 bin/python 通常是指向系統 python 的捷徑,要從那個路徑叫起來 venv 才生效
        ck = (os.path.abspath(py), st_.st_mtime_ns, st_.st_size)
    except OSError:
        return ("unknown", "")
    if ck in _PY_ID_CACHE:
        return _PY_ID_CACHE[ck]
    code = ("import sys, importlib.util as u\nv = ''\nif u.find_spec('pytest'):\n"
            "    try:\n        import importlib.metadata as m; v = m.version('pytest')\n    except Exception:\n        v = '?'\n"
            "print(sys.version.split()[0] + '|' + v)")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "EZ2_VERIFY_TRACE", "BASH_ENV", "PYTHONSTARTUP")}
    try:
        p = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=30, env=env, stdin=subprocess.DEVNULL)
        ver, _, pyt = (p.stdout or "").strip().rpartition("\n")[2].partition("|")
        out = (ver, pyt) if p.returncode == 0 and ver else ("unknown", "")
    except (OSError, subprocess.SubprocessError):
        out = ("unknown", "")
    _PY_ID_CACHE[ck] = out
    return out


def env_fingerprint(environ=None, repo=None):
    """★ ef56b A/D 反例:只摘幾個版本字串,測試讀的環境變數(REVIEW_FEATURE / PYTHONOPTIMIZE)改了鑰匙不變。
    改成雜湊**整個受控子行程環境**(名字+值),只排掉每跑必變的路徑類;環境不是驗證腳本給的就不該進來
    (witness/release 都先洗殼再設沙箱值,所以剩下的每一項都是「會影響結果的輸入」)。"""
    parts = env_parts(environ, repo)
    raw = "|".join([
        parts["policy"],
        "tracer=" + parts["tracer"],
        parts["python"],
        parts["executable"],
        parts["node"],
        parts["os"],
        parts["tmux"],
        parts["lang"], parts["lc_all"],
        "washed=" + parts["washed"],
        "env=" + parts["env_digest"],
        "test_python=" + parts["test_python"],
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest(), raw


def _env_hash(v, salt=""):
    if salt:
        return hmac.new(salt.encode("utf-8"), v.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
    return hashlib.sha256(v.encode("utf-8")).hexdigest()[:12]


def env_parts(environ=None, repo=None):
    """環境指紋的每一項原料(10-02 第 8 步準備:兩台機器各印一份、比對差在哪)。
    env 那一項給「變數名 → 正規化後值的雜湊前 12 碼」,不印值本身(值可能有金鑰);env_digest 是指紋實際用的那一個。"""
    environ = os.environ if environ is None else environ
    node = _tool_version(["node", "--version"], environ)
    tmux = _tool_version(["tmux", "-V"], environ)
    # 96:git 子行程在治具裡跑算「量得到」⇒ git 版本與全域設定(init.defaultBranch 之類會改行為)要進鑰匙;
    #   09-29 起只進跑了 git 的那些測試(inputs_from_events 的 tool:git 項,重算時現查),不再放每一支的環境指紋。
    washed = "0" if environ.get("EZ2_AIHUB_SOCKET") else "1"
    _tp = test_python(repo, environ)
    root = ""
    if repo:
        try:
            root = str(Path(repo).resolve())
        except (OSError, ValueError):
            root = ""
    # 10-02 第 8 步準備:值裡的家目錄換成 <HOME>(HOME 本身是揮發變數、不進指紋)。兩台使用者名稱不同時,
    #   PATH 之類帶家目錄的值就永遠對不上;換掉之後只剩「真的不一樣」的才不一樣。倉路徑先換(倉常在家目錄底下)。
    home = (environ.get("HOME") or "").rstrip("/")
    # 10-03 審查第 8 條:HOME 太短(/、/r)或是暫存根(/tmp)時不換,否則會大範圍誤換
    if len(home) < 4 or home in ("/tmp", "/private/tmp", "/var/tmp"):
        home = ""

    def _sub(v, prefix, tag):
        # 只換路徑邊界上的:後面接 / 或 :(PATH 的分隔)或字串結尾;/home/abc 不會被 HOME=/home/a 換成 <HOME>bc
        return re.sub(re.escape(prefix) + r"(?=/|:|$)", lambda _m: tag, v)

    def _norm(v):
        v = _sub(v, root, "<REPO>") if root else v      # 工作樹路徑不進鑰匙(跨樹要對得上)
        return _sub(v, home, "<HOME>") if home else v
    def _strip_node_hook(k, v):
        # 10-04:NODE_OPTIONS 裡追蹤器自己加的 --require <node-shim/audit.cjs> 不是輸入(別的 node 選項照記)
        # 10-07:LD_PRELOAD 裡追蹤器自己加的安全網程式庫(aib-ldp-…/libaibldp.so)同理;使用者自己的照記
        if k == "LD_PRELOAD":
            return ":".join(x for x in v.split(":") if x and "/aib-ldp-" not in x)
        if k != "NODE_OPTIONS":
            return v
        return re.sub(r"\s*--require[ =]\S*/%s/audit\.cjs" % NODE_SHIM_DIR, "", v).strip()
    kept = sorted((k, _norm(_strip_node_hook(k, v))) for k, v in environ.items()
                  if k not in VOLATILE_ENV and not k.startswith(("__CF", "LC_TERMINAL", "ITERM"))
                  and not (k in ("NODE_OPTIONS", "LD_PRELOAD") and not _strip_node_hook(k, v)))
    env_items = ["%s=%s" % kv for kv in kept]
    env_digest = hashlib.sha256("\n".join(env_items).encode("utf-8")).hexdigest()
    # 同內容的目錄指紋相同 ⇒ 用指紋去重,同一份追蹤器放在兩處不會讓指紋跟著路徑變
    tracer = "+".join(sorted({tracer_code_digest(d) for d in _tracer_dirs(environ)}))
    return {
        "policy": TRACER_POLICY_VERSION,
        "tracer": tracer,
        "python": sys.version.split()[0],
        # 10-05:記「實際是哪一支 python」,不記「從哪個路徑叫」:/usr/bin/python3 與 /usr/local/bin/python3 指到同一支,
        #   以前兩個路徑兩個指紋 ⇒ aib test(/usr/bin/python3)與推前檢查(PATH 上的 python3)互相沿用不到。
        #   只追捷徑會把 venv 的 bin/python(捷徑到系統 python)跟系統 python 當成同一支 ⇒ 再加 sys.prefix(venv 不同)。
        "executable": "%s;prefix=%s" % (_norm(os.path.realpath(sys.executable)), _norm(sys.prefix)),
        # 10-03 審查第 11 條:這份會在兩台之間交換,家目錄不原文送出
        # 10-04 晚:測試實際用的 python(可能是專案自己的虛擬環境)的版本與 pytest 版本;是不是 aib 自己那支也記
        "test_python": "%s|pytest=%s|%s" % (python_id(_tp)[0], python_id(_tp)[1] or "none",
                                            "own" if _tp == sys.executable else "project"),
        "node": node or "node-missing",
        # ★ 09-29 the maintainer「不管用戶的 OS 版本」:只取系統種類+架構(Darwin-arm64 / Linux-x86_64),不取版本號 ——
        #   以前是 platform.platform()(macOS-15.7.9-arm64-…),Fish 15.5 與這台 15.7.9 就永遠比不到。
        "os": "%s-%s" % (platform.system(), platform.machine()),
        "tmux": tmux or "tmux-missing",
        # git 版本與 ~/.gitconfig 移到 tool:git 項(只有跑了 git 的測試才有;the maintainer 09-29 選 B)
        "lang": environ.get("LANG") or "", "lc_all": environ.get("LC_ALL") or "",
        "washed": washed,
        "env_digest": env_digest,
        # 10-03 審查第 11 條:值的雜湊可加鹽(AIB_ENV_PARTS_SALT,兩台用同一個、事先口頭約定),短密碼就不能靠查表反推;
        #   沒設鹽時照舊(--as-aib-test 只剩洗過的少數變數,LANG/PATH 這類)
        "env": {k: _env_hash(v, environ.get("AIB_ENV_PARTS_SALT") or "") for k, v in kept},
    }


def diff_env_parts(a, b):
    """兩份 env_parts → 不一樣的項目清單 [{"part", "a", "b"}];env 逐個變數比(只有雜湊,不露值)。"""
    out = []
    for k in sorted(set(a) | set(b)):
        if k in ("env", "env_digest"):
            continue
        if a.get(k) != b.get(k):
            out.append({"part": k, "a": a.get(k), "b": b.get(k)})
    ea, eb = a.get("env") or {}, b.get("env") or {}
    for k in sorted(set(ea) | set(eb)):
        if ea.get(k) != eb.get(k):
            out.append({"part": "env:" + k, "a": ea.get(k, "(missing)"), "b": eb.get(k, "(missing)")})
    return out


# 10-03:bash xtrace 把含換行的字串(多行的變數值、python -c '…')照原樣印出,一個命令會佔好幾行;
#   以前第二行起全當壞行(雲端 test-dm-header-unify 一支就 506 行 trace-corrupt-line),bash 5 的機器幾乎每支量不到。
#   接回的條件:xtrace 行裡單引號沒有收尾 ⇒ 後面的行屬於同一個命令,直到收尾。
#   ★ 為什麼不會吞掉別的行程的事件:bash 每個命令 fprintf 後 fflush,一筆在 stdio 緩衝內(≥1024)就是一次 write,
#     O_APPEND 的一次 write 不會被別人插隊。所以只接「整筆 ≤ XT_JOIN_MAX 位元組」的;超過、或檔尾還沒收尾 ⇒ 壞行(量不到)。
#   10-03 由 1024 放寬到 4096:stdio 對一般檔案配的緩衝是 st_blksize(glibc 與 macOS 都是;ext4/tmpfs/APFS 為 4096 或更大),
#     bash 每個命令前緩衝是空的(上一個命令已 fflush)⇒ ≤ 4096 的一筆就是一次 write。實踩:dm-ctl.sh:1194 的
#     `python3 -c '<多行程式>' … <多行輸出>` 一筆 1132 位元組 ⇒ 舊上限判壞行,整支量不到(雲端 m45 有 14 列卡這個)。
XT_JOIN_MAX = 4096
# 10-09(the maintainer「修」):超過 XT_JOIN_MAX 的一筆(例:warroom-board.sh 的 FRAME 畫面字串,4135 位元組)以前一律壞行 ⇒
#   那支測試整支量不到、每輪重跑(8 支、每輪約 250 秒)。超過 4096 時 bash 會分好幾次 write,別的行程**可能**插進來;
#   但插進來的一定是一筆完整紀錄:JSON 事件({"ch":…,所有寫追蹤檔的鉤子都是這個開頭)或別的 xtrace 行(含 EZ2BT␟)。
#   ⇒ 超過 4096 也接,條件是整筆裡(第一行的抬頭之外)找不到這兩種記號;找到 = 可能有人插隊 = 照舊壞行(量不到,必重跑)。
#   誤判只會往「量不到」那邊偏(多行字串剛好含這兩種字樣),不會把別人的事件吞掉而判成量到了。
#   XT_JOIN_HARD_MAX:再長就不接(防病態大檔拖慢解析)。
XT_JOIN_HARD_MAX = 1 << 20
XT_FOREIGN_MARKS = ('{"ch":', "EZ2BT" + "\x1f")


def _xt_foreign_inside(block):
    """多於 XT_JOIN_MAX 的一筆:抬頭(第一個 EZ2BT␟)之後還有沒有別的紀錄的記號。"""
    head = block.find("EZ2BT\x1f")
    rest = block[head + 6:] if head >= 0 else block
    return any(m in rest for m in XT_FOREIGN_MARKS)


def _xt_open_quote(text, state=None):
    """bash xtrace 的引號狀態:None = 沒有未收尾的引號;"'" = 在 '…' 裡;"$'" = 在 $'…' 裡。"""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if state is None:
            if c == "\\":
                i += 2
                continue
            if c == "$" and text[i + 1:i + 2] == "'":
                state = "$'"
                i += 2
                continue
            if c == "'":
                state = "'"
        elif state == "'":
            if c == "'":
                state = None
        else:
            if c == "\\":
                i += 2
                continue
            if c == "'":
                state = None
        i += 1
    return state


def load_trace(path: Path):
    events = []
    if not path.is_file():
        return events
    lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.startswith("+") and "EZ2BT" in line:
            state = _xt_open_quote(line)
            size = len(line.encode("utf-8")) + 1
            while state is not None and i < len(lines) and size <= XT_JOIN_HARD_MAX:
                nxt = lines[i]
                i += 1
                line += "\n" + nxt
                size += len(nxt.encode("utf-8")) + 1
                state = _xt_open_quote(nxt, state)
            if state is not None or (size > XT_JOIN_MAX and _xt_foreign_inside(line)):
                events.append({"ch": "unmeasured", "why": "trace-corrupt-line"})
                continue
            ev = parse_xtrace_line(line)          # 96b:bash xtrace 行(不是 JSON,但不是壞行)
            events.append(ev if ev else {"ch": "unmeasured", "why": "trace-corrupt-line"})
            continue
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                # 一行是合法 JSON 但不是物件(例如一個裸字串):不是事件,當壞行 ⇒ unmeasured(B10 拿測試檔當壞 trace 抓到的)
                events.append({"ch": "unmeasured", "why": "trace-corrupt-line"})
                continue
            events.append(obj)
        except ValueError:
            # ★ ef56b E 反例:壞行以前被靜默跳過 ⇒ 半份追蹤被當「量到了」而且 inputs=[] ⇒ 錯沿用。
            #   壞行 = 追蹤不完整 = unmeasured,永不沿用。
            events.append({"ch": "unmeasured", "why": "trace-corrupt-line"})
    return events


EPHEMERAL_ROOTS = ("/private/tmp/", "/tmp/", "/var/folders/", "/private/var/folders/")
# 10-08 .hypothesis:hypothesis 每跑一次就更新自己的範例資料庫(專案根的 .hypothesis/),讀它不是讀輸入 ——
#   以前記成輸入 ⇒ 用 hypothesis 的測試永遠沿用不了(attrs 實測:什麼都沒改,3 支每次重跑)。它只在測試**失敗**時
#   記下失敗例,而紅的本來就不沿用;所以不算輸入不會把紅藏成綠。這組名字與鉤子端(sitecustomize_audit、node-shim)同一組。
# 10-08:工具自己的狀態資料夾,內容不決定測試紅綠、卻每跑一次就變 ⇒ 不算輸入。目前只有 .hypothesis(範例資料庫;attrs 實測)。
#   ★ 刻意不含 .mypy_cache / .ruff_cache:mypy 先看原始碼的修改時間與大小,沒變就直接用快取、不讀內容 ⇒ 快取本身就是
#     「上次分析結果」這個輸入;排掉它,改了原始碼也會被沿用(10-08 實測差點把紅藏成綠)。也不含 .pytest_cache(--lf 決定跑哪些)。
#   鉤子端 sitecustomize_audit.TOOL_CACHE_DIRS、node-shim 的 SKIP_NAMES 同一組(測試釘住三邊一致)。
TOOL_CACHE_DIRS = (".hypothesis",)
DERIVED_DIRNAMES = ("__pycache__", ".git") + TOOL_CACHE_DIRS
DERIVED_SUFFIXES = (".pyc", ".pyo")
DERIVED_NAMES = (".DS_Store",)


# 10-08(測試裡跑 mypy 實測):/proc、/sys、/dev 是作業系統的虛擬檔案(CPU 統計、記憶體、行程編號…),每讀一次都不同,
#   不是測試的輸入。以前記成輸入 ⇒ 用 psutil 之類量機器狀態的工具(mypy 會)每次必重跑。
VIRTUAL_FS_ROOTS = ("/proc/", "/sys/", "/dev/")


def is_coverage_data(base):
    """10-08:coverage.py / pytest-cov 每跑一次就重寫的資料檔(.coverage、.coverage.<主機>.pid<n>.<亂碼>)—— 不是輸入
    (以前:addopts 帶 --cov 的專案永遠沿用不了)。它不影響這一輪紅綠(--cov-fail-under 看的是這一輪自己的資料)。
    設定檔 .coveragerc / .coveragerc.toml 是真的輸入,不在這裡。鉤子端(sitecustomize_audit)用同一個判準。"""
    return base == ".coverage" or (base.startswith(".coverage.") and not base.startswith(".coveragerc"))


def is_ephemeral_or_derived(path, repo=None, environ=None):
    """★ 09-11 18:2x 量到的:三輪校準 112 支只沿用 8 支,84 支可量的裡幾乎每支都讀了
    (1) 測試自己在暫存根開的檔(下一輪不在了)、(2) __pycache__/.pyc 與含它的目錄名單、(3) .git/ 內部檔。
    這三類不是候選的輸入,卻讓鑰匙每輪都變。這裡是唯一的判準,記指紋與重算都走它;目錄名單雜湊也用同一組名字過濾。
    ★ 暫存根用字面加 $TMPDIR:測試會把 TMPDIR 指到自己的沙箱。"""
    if not path:
        return False
    sp = str(path)
    parts = sp.replace("\\", "/").split("/")
    in_repo = False
    if repo:
        try:
            # ★ 96 順手修(main 上 test-verify-skip-v2 test_C 在 TMPDIR=/var/folders 下紅):倉根 resolve 過、
            #   路徑沒 resolve,倉住在 symlink 底下(/var/folders → /private/var/folders、/tmp → /private/tmp)時
            #   倉內路徑被判成暫存根 ⇒ missing 被丟掉。兩邊都用 realpath 比。
            root = os.path.realpath(str(repo))
            ap = os.path.realpath(sp)
            in_repo = ap == root or ap.startswith(root + os.sep)
        except (OSError, ValueError):
            in_repo = False
    if not in_repo:
        # ★ repo 底下的檔永遠是候選的輸入(治具倉常住在暫存根裡);暫存根判準只對 repo 外的路徑。
        environ = os.environ if environ is None else environ
        tmp = environ.get("TMPDIR") or ""
        roots = EPHEMERAL_ROOTS + ((tmp.rstrip("/") + "/",) if tmp else ())
        if sp.startswith(roots):
            return True
    if any(part in DERIVED_DIRNAMES for part in parts) or sp.startswith(VIRTUAL_FS_ROOTS):
        return True
    base = parts[-1] if parts else sp
    return base.endswith(DERIVED_SUFFIXES) or base in DERIVED_NAMES or is_coverage_data(base)


_KEYLOG_NAME_RE = re.compile(r"^[0-9a-f]{16}\.txt$")


def _base_name(parts):
    return parts[-1] if parts else ""


def is_derived(path):
    """衍生檔(__pycache__/.pyc/.git/.DS_Store)—— 與暫存根無關的排除;先存在、測試只讀的暫存檔是真輸入(ef56b B 反例),
    所以 open/missing 不再用暫存根排除,只靠「測試自己寫過」分辨。"""
    if not path:
        return False
    parts = str(path).replace("\\", "/").split("/")
    if any(part in DERIVED_DIRNAMES for part in parts) or str(path).startswith(VIRTUAL_FS_ROOTS):
        return True
    # 10-07(第 10 包實踩):翻譯鍵紀錄(AIB_I18N_KEYLOG,執行器給每支一個 <紀錄池>/i18nkeys/<雜湊>.txt,每次實跑先清空)是量測自己的產物,
    #   不是測試的輸入。以前被記成 open: 輸入 ⇒ 每跑一次內容都不同 ⇒ 用到翻譯的 300 多支每次都判「讀的檔變了」必重跑(只多跑、不藏紅)。
    #   紀錄池目錄可以是任何地方(測試會給暫存池),所以認形狀:倉外絕對路徑、上一層叫 i18nkeys、檔名 = 16 碼小寫十六進位 + .txt。
    if len(parts) >= 3 and parts[0] == "" and parts[-2] == "i18nkeys" and _KEYLOG_NAME_RE.match(_base_name(parts)):
        return True
    base = parts[-1] if parts else str(path)
    return base.endswith(DERIVED_SUFFIXES) or base in DERIVED_NAMES or is_coverage_data(base)


def filter_listdir_names(names):
    """目錄名單雜湊要排掉衍生名字(鉤子與重算共用,否則兩邊的 sha 永遠對不上)."""
    return sorted(n for n in names if n not in DERIVED_DIRNAMES and n not in DERIVED_NAMES
                  and not n.endswith(DERIVED_SUFFIXES) and not is_coverage_data(n))


EPHEMERAL_PLACEHOLDER = "<tmp>"
_EPHEMERAL_RE_CACHE = {}


def _ephemeral_argv_regex(environ=None):
    """暫存根 + 緊接的一個目錄成分(mkdtemp 的隨機名)。
    /var/folders/<xx>/<long>/T/ 這種 macOS TMPDIR 形狀:根算到 T/ 為止,隨機名是 T/ 之後那一段。
    $TMPDIR 也算根(測試會把 TMPDIR 指到自己的沙箱)。"""
    environ = os.environ if environ is None else environ
    tmp = (environ.get("TMPDIR") or "").rstrip("/")
    key = tmp
    rx = _EPHEMERAL_RE_CACHE.get(key)
    if rx is None:
        roots = [r"/private/var/folders/[^/\s\x27\x22]+/[^/\s\x27\x22]+/T", r"/var/folders/[^/\s\x27\x22]+/[^/\s\x27\x22]+/T",
                 r"/private/tmp", r"/tmp"]
        if tmp:
            roots.insert(0, re.escape(tmp))
        rx = re.compile(r"(?<![A-Za-z0-9_./-])(?:%s)/[^/\s\x27\x22]+" % "|".join(roots))
        _EPHEMERAL_RE_CACHE[key] = rx
    return rx


def _repo_roots(repo):
    if not repo:
        return []
    try:
        r1 = str(Path(repo).resolve()); r2 = os.path.abspath(str(repo))
        return sorted({r1, r2}, key=len, reverse=True)
    except (OSError, ValueError):
        return []


def map_ephemeral(text, mapping, environ=None, repo=None, create=True):
    """把 text 裡「暫存根+隨機目錄名」換成角色 ID(<tmp1>、<tmp2>…,依**第一次出現的順序**編號),
    同一列的 argv 與內容項共用同一份 mapping ⇒ 兩個獨立暫存根換內容會得到不同指紋(ef56b 09-12 第 1 條)。
    倉根先換成 `.`。根下那一段若現在是**檔**(不是目錄)就逐字保留——`/private/tmp/fixed-input.txt` 不是 mkdtemp。
    create=False 時只套既有映射、不新增(內容項對 argv 的映射)。"""
    text = str(text)
    for r in _repo_roots(repo):
        if r in text:
            text = text.replace(r + "/", "./").replace(r, ".")
    rx = _ephemeral_argv_regex(environ)

    def _sub(m):
        raw = m.group(0)
        if raw in mapping:
            return mapping[raw]
        if not create:
            return raw
        try:
            if os.path.isfile(raw):
                return raw
        except (OSError, ValueError):
            pass
        mapping[raw] = "<tmp%d>" % (len(mapping) + 1)
        return mapping[raw]
    return rx.sub(_sub, text)


def normalize_argv(argv, environ=None, repo=None, mapping=None):
    """★ 大綱第 28 項(09-11/12):argv 裡的暫存根+隨機目錄名換成角色 ID,其餘逐字保留(見 map_ephemeral)。
    ★ 不抹 40 位 sha(ef56b 第 3 條:倉裡查不到的 40 位字串不等於治具 git 物件,可能是摘要參數;非 git 倉 cat-file 也會失敗)
      ⇒ 治具 sha 每輪不同 = 那類測試不沿用,是保守側;要正規化得先證明它是受控治具的物件(28c)。
    ★ 量不到的子行程(非 python、非 shim 裡的指令)本來就靠 unmeasured/external 擋,與本正規化無關。"""
    if mapping is None:
        mapping = {}
    return [map_ephemeral(a, mapping, environ=environ, repo=repo) for a in (argv or [])]


def normalize_path(repo, path):
    """repo 底下的路徑寫成 ./相對(跨工作樹才對得上);repo 外(stdlib、/opt)保留絕對。
    ★ V3 收尾(a seat 09-11)前記的是絕對路徑:同一棵樹永遠對得上、換棵樹永遠對不上,兩個方向都錯。"""
    if not repo or not path:
        return path
    try:
        root = str(Path(repo).resolve())
        ap = os.path.abspath(str(path))
    except (OSError, ValueError):
        return path
    if ap == root:
        return "./"
    if ap.startswith(root + os.sep):
        return "./" + ap[len(root) + 1:]
    return path


def _resolve_item_path(repo, path):
    if path.startswith("./") and repo:
        return os.path.join(str(Path(repo).resolve()), path[2:])
    return path


def _sha_file_now(path):
    try:
        if not os.path.isfile(path):
            return None, not os.path.exists(path)
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest(), False
    except (OSError, IOError):
        return None, True


def _probe_state(ap):
    """bash 裡 [ -f x ] 這類存在探測:只記 file/dir/missing,不讀內容(探測的語意只有在不在)。"""
    if os.path.isdir(ap):
        return "dir"
    if os.path.isfile(ap):
        return "file"
    return "missing"


def _state_item(repo, path):
    """一個路徑現在的狀態:檔 → open:路徑:sha;目錄 → dir:路徑(ef56b C 反例:缺檔變目錄以前仍記 missing ⇒ 錯沿用);不存在 → missing."""
    ap = _resolve_item_path(repo, path)
    if os.path.isdir(ap):
        return "dir:%s" % path
    digest, missing = _sha_file_now(ap)
    if missing or not digest:
        return "missing:%s" % path
    return "open:%s:%s" % (path, digest)


def git_tool_fp(environ=None):
    """git 版本 + 全域設定檔的指紋(只給「真的跑了 git」的測試進鑰匙;the maintainer 09-29 選 B)。
    記錄與重算都用發起端(執行器/證詞)的環境求值 ⇒ 同一台機器兩次一致;git 升版或換設定 ⇒ 不同 ⇒ 那些測試必重跑。"""
    environ = dict(os.environ if environ is None else environ)
    try:
        ver = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=10,
                             stdin=subprocess.DEVNULL, env=environ).stdout.strip() or "git-missing"
    except (OSError, subprocess.TimeoutExpired):
        ver = "git-missing"
    cfg = ""
    home = environ.get("HOME") or ""
    for _gc in (os.path.join(home, ".gitconfig"),
                os.path.join(environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), "git", "config")):
        _d, _missing = _sha_file_now(_gc) if home else (None, True)
        cfg += (_d or "-") + ";"
    return hashlib.sha256(("%s|gitconfig=%s" % (ver, cfg)).encode("utf-8")).hexdigest()


# ── 10-06 晚 翻譯字典以鍵為單位(開發檔 §15.3 第二步;the maintainer:讓「很多測試都會讀、又常被改的檔」縮小重跑範圍)──────────
#   倉裡的 i18n/<語系>.json 約 300 支測試會讀,幾乎每包都改 ⇒ 每包這 300 支全重跑。實際上多數測試只透過
#   src/cockpit/i18n_lib.py 的 Translator 用到其中幾個鍵(回放 48 個改過翻譯檔的提交:平均要重跑的從 303 支降到約 11 支)。
#   一份字典只在**全部**成立時才改記成「用到的那些鍵的值」(i18nkeys:路徑:鍵清單:摘要),否則照舊整檔 sha:
#     ① 這一列有鍵紀錄(i18n_lib 寫進追蹤檔的 i18nkey)且沒有 "@whole"(列舉整份、parity 檢查、外部直接拿 load_dict);
#     ② 這份字典的每一次開檔,都在同一個行程裡有一筆 Translator 自己的 i18nload(數目夠)——
#        bash/cat/jq/別的 python 碼直接讀它 ⇒ 多出沒對上的開檔 ⇒ 整檔;
#     ③ 這一列沒有別種輸入項指到它(handoff 之類);讀的當下內容 = 現在的內容。
#   摘要 = 用到的鍵的值(沒有的鍵記 null)+ 字典整份過不過得了 i18n_lib 自己的檢查(過不了 ⇒ 改用整檔 sha:
#   壞字典讓 Translator 一建就失敗,結果取決於整份)。驗字典用的是**候選樹**裡的 i18n_lib(那支檔本身也是這些測試的輸入)。
_I18N_ITEM_RE = re.compile(r"^\./(.+\.json|.+\.mo)$")   # aib 的翻譯字典、宣告過的 JSON 字典、任何專案的 gettext .mo(§15.3c)
_AIB_DICT_RE = re.compile(r"^\./i18n/[A-Za-z][A-Za-z_-]*\.json$")
_I18N_LIB_CACHE = {}


def _repo_i18n_lib(repo):
    lib = os.path.join(str(Path(repo).resolve()), "src", "cockpit", "i18n_lib.py")
    try:
        st_ = os.stat(lib)
    except OSError:
        return None
    key = (lib, st_.st_mtime_ns, st_.st_size, st_.st_ino)
    if key not in _I18N_LIB_CACHE:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("aib_i18n_lib_for_reuse_%d" % len(_I18N_LIB_CACHE), lib)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _I18N_LIB_CACHE[key] = mod
        except Exception:  # noqa: BLE001 - 載不進來 ⇒ 不縮小(安全側)
            _I18N_LIB_CACHE[key] = None
    return _I18N_LIB_CACHE[key]


def i18n_keys_digest(repo, path, keys):
    """./i18n/<語系>.json 的「用到的鍵」摘要;字典不存在/驗不過/載不進 i18n_lib ⇒ None(呼叫端照整檔處理)。"""
    ap = _resolve_item_path(repo, path)
    if not os.path.isfile(ap):
        return None
    if ap.endswith(".mo"):
        return _mo_keys_digest(ap, keys)
    lib = _repo_i18n_lib(repo) if _AIB_DICT_RE.match(path) else None
    if lib is None:
        return _json_keys_digest(ap, keys)
    try:
        data = lib.load_dict(Path(ap).stem, i18n_dir=os.path.dirname(ap), _keyed=True, _quiet=True)
    except Exception:  # noqa: BLE001 - 壞字典(或舊版 i18n_lib 沒有這些參數)
        return None
    return hashlib.sha256(json.dumps(_pick_keys(data, keys), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _pick_keys(data, keys):
    """用到的鍵 → {鍵: 值}。10-07:"@prefix:P"(i18n_lib.prefixed)= 這份字典裡所有以 P 開頭的鍵(新增、刪掉也會變)。"""
    picked = {}
    for k in sorted(set(keys)):
        if k.startswith("@prefix:"):
            p = k[len("@prefix:"):]
            picked[k] = {dk: dv for dk, dv in sorted(data.items()) if isinstance(dk, str) and dk.startswith(p)}
        else:
            picked[k] = data.get(k)
    return picked


def _json_keys_digest(ap, keys):
    """宣告過的 JSON 字典(§15.3c 第 2 項):最外層用到的鍵的整棵值。不是物件 / 解不開 ⇒ None(照整檔)。
    跟鉤子一樣用 json.loads 解整份文字(內容 sha 對得上才會被記成字典,所以這裡解的就是同一份)。"""
    try:
        with open(ap, "rb") as fh:
            data = json.loads(fh.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if type(data) is not dict:
        return None
    picked = {k: data.get(k) for k in sorted(set(keys))}
    return hashlib.sha256(json.dumps(picked, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _mo_keys_digest(ap, keys):
    """gettext .mo:用到的字串的譯文 + 檔頭(語系、複數規則、編碼都在檔頭)。讀不了 ⇒ None(照整檔)。
    鍵是字串(gettext/pgettext 的 msgid,含 context 的是 "ctx\x04msgid")或 JSON 陣列(ngettext 的 (msgid, 第幾種複數))。"""
    import gettext as _gt
    try:
        with open(ap, "rb") as fh:
            tr = _gt.GNUTranslations(fh)
    except Exception:  # noqa: BLE001 - 壞檔
        return None
    cat = dict(getattr(tr, "_catalog", {}))   # 追蹤器自己若也在追蹤下跑,換成一般 dict 再取 ⇒ 不記帳
    picked = {}
    for k in sorted(set(keys)):
        if k.startswith("["):
            try:
                kk = json.loads(k)
                key = (kk[0], int(kk[1]))
            except (ValueError, TypeError, IndexError):
                return None
        else:
            key = k
        picked[k] = dict.get(cat, key)
    head = dict.get(cat, "")
    return hashlib.sha256(json.dumps([picked, head], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _copy_keyed_only(dst, opens, loads, copies, seen):
    """10-07:字典的 shutil 複本只經翻譯函式讀 —— 複本每一次被打開(任何行程)都對得上同一行程的 i18nload,
    複本再被複製的也一樣(遞迴)。有一次對不上(測試自己 open、bash cat、讀了再改寫……)⇒ False(照舊整檔)。
    複本從沒被打開 ⇒ 它的內容沒進任何結果 ⇒ True。"""
    if dst in seen:
        return True
    seen.add(dst)
    if dst in copies.get("@written", ()):
        return False
    mine = {k: n for k, n in opens.items() if k[1] == dst}
    if not all(loads.get(k, 0) >= n for k, n in mine.items()):
        return False
    return all(_copy_keyed_only(d, opens, loads, copies, seen) for d in copies.get(dst, ()))


def i18n_narrow(items, repo, opens, loads, keys, copies=None):
    """inputs_from_events 收尾用:符合上面三條的翻譯字典 open: 項改成 i18nkeys: 項。opens/loads:{(pid, 實體路徑): 次數}。
    copies:{實體來源: {實體複本}}(10-07:刀測試常把整個 i18n/ 抄到暫存樹再在裡面跑;抄的那一下不是讀內容,
    內容是在複本裡被讀 —— 複本也只經翻譯函式讀,才算以鍵為單位,見 _copy_keyed_only)。"""
    copies = copies or {}
    if not repo or not keys or "@whole" in keys:
        return items
    kj = json.dumps(sorted(keys), ensure_ascii=True, separators=(",", ":"))
    out = []
    for it in items:
        kind, _, rest = it.partition(":")
        path, _, sha = rest.rpartition(":")
        if kind != "open" or not _I18N_ITEM_RE.match(path):
            out.append(it)
            continue
        ap = os.path.realpath(_resolve_item_path(repo, path))
        mine = {k: n for k, n in opens.items() if k[1] == ap}
        dsts = copies.get(ap, set())
        others = any(o != it and o.partition(":")[2].startswith(path + ":") for o in items)
        now, _missing = _sha_file_now(ap)
        d = i18n_keys_digest(repo, path, keys) if ((mine or dsts) and not others and now == sha
                                                  and all(loads.get(k, 0) >= n for k, n in mine.items())
                                                  and all(_copy_keyed_only(c, opens, loads, copies, set())
                                                          for c in dsts)) else None
        out.append("i18nkeys:%s:%s:%s" % (path, kj, d) if d else it)
    return out


def recompute_input_fp(repo, inputs):
    """★ 對**現在的樹**重算輸入指紋(V3 收尾補的那一刀)。
    之前 reuse_row 拿上一輪的 input_fp 直接組鑰匙,沒有任何一步去讀現在的檔 ⇒ 改了 VERSION 之後
    test-version.py 仍被判「可跳」(09-11 17:0x 實測)。這裡逐項重量:open 重讀檔 sha、listdir 重列
    名字、missing 看它現在在不在;argv 是測試自己跑了什麼,照抄。回 (items_now, digest)。"""
    items = []
    _re_map = {}   # 重算時 argv 的角色 ID 映射(已正規化的列裡是 <tmpN>,不會再被改;舊格式列依出現順序重編)
    for item in inputs or []:
        kind, _, rest = str(item).partition(":")
        _p = rest.rpartition(":")[0] if kind in ("open", "listdir") else rest
        if kind in ("open", "missing") and is_derived(_p):
            continue
        if kind == "missing" and is_ephemeral_or_derived(_resolve_item_path(repo, _p), repo):
            continue
        if kind == "listdir" and (is_derived(_p) or is_ephemeral_or_derived(_resolve_item_path(repo, _p), repo)):
            continue
        if kind == "open":
            path, _, _sha = rest.rpartition(":")
            if path.startswith("<tmp"):
                items.append(str(item))   # 96:產物/交出去的內容,檔已不在;內容 sha 逐字保留(含隨機成分 ⇒ 每輪不同 = 安全側)
            else:
                items.append(_state_item(repo, path))
        elif kind == "listdir":
            path, _, _sha = rest.rpartition(":")
            try:
                names = filter_listdir_names(os.listdir(_resolve_item_path(repo, path)))
            except OSError:
                items.append("missing:%s" % path)
                continue
            items.append("listdir:%s:%s" % (path, hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()))
        elif kind == "missing":
            items.append(str(item) if rest.startswith("<tmp") else _state_item(repo, rest))
        elif kind == "selfwrite":
            items.append(str(item))   # 上一輪自己產出的內容 hash,逐字保留(檔已不在;含隨機成分則每輪不同=安全側)
        elif kind == "handoff":
            # 96:交給子行程的檔。治具裡的(<tmpN>)已不在 ⇒ 逐字保留;真樹裡的(./x 或絕對)⇒ 現在重讀。
            path, _, _sha = rest.rpartition(":")
            if path.startswith("<tmp"):
                items.append(str(item))
            else:
                now = _state_item(repo, path)
                items.append(("handoff:%s:%s" % (path, now.rpartition(":")[2])) if now.startswith("open:") else now)
        elif kind == "handoffdir":
            items.append(str(item))   # 只有治具目錄會被整棵雜湊,已不在 ⇒ 逐字保留
        elif kind == "i18nkeys":
            # 10-06 晚:現在的字典裡、用到的那些鍵的值重算;算不出來(檔不在、驗不過)⇒ 永遠不同(安全側)
            path, _, tail = rest.partition(":")
            kj, _, _old = tail.rpartition(":")
            try:
                d = i18n_keys_digest(repo, path, json.loads(kj))
            except (ValueError, TypeError):
                d = None
            items.append("i18nkeys:%s:%s:%s" % (path, kj, d or "unknown-" + os.urandom(8).hex()))
        elif kind == "probe":
            path, _, _st = rest.rpartition(":")
            items.append("probe:%s:%s" % (path, _probe_state(_resolve_item_path(repo, path))))
        elif kind == "githead":
            items.append("githead:%s" % git_repo_state(repo))   # 10-04:現在重查(換提交 ⇒ 不同 ⇒ 必重跑)
        elif kind == "gitq":
            spec, _, _old = rest.rpartition(":")
            try:
                q = json.loads(spec)
                items.append("gitq:%s:%s" % (spec, git_query_digest(repo, q["a"], q["c"])))   # 10-05:現在重跑那條查詢
            except (ValueError, KeyError, TypeError):
                items.append("gitq:%s:unknown-%s" % (spec, os.urandom(8).hex()))
        elif kind == "gitrefs":
            items.append("gitrefs:%s" % git_refs_state(repo))   # 10-04 晚:現在重查(任何 ref 動了 ⇒ 不同 ⇒ 必重跑)
        elif kind == "gitwt":
            items.append("gitwt:%s" % git_worktree_state(repo))   # 10-04:現在重查(倉裡任何一個檔動了 ⇒ 不同 ⇒ 必重跑)
        elif kind == "tool":
            # 不照抄:現在重查(git 升版/換設定 ⇒ 不同 ⇒ 必重跑)。不認得的工具名 ⇒ 逐字保留會變成永遠相同,改成永遠不同(安全側)。
            name = rest.partition(":")[0]
            items.append("tool:git:%s" % git_tool_fp() if name == "git" else "tool:%s:unknown-%s" % (name, os.urandom(4).hex()))
        elif kind == "argv":
            try:
                items.append("argv:%s" % json.dumps(normalize_argv(json.loads(rest), repo=repo, mapping=_re_map), ensure_ascii=False))
            except (ValueError, TypeError):
                items.append(str(item))
        else:
            items.append(str(item))
    items = sorted(set(items))
    return items, hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()


def _via_repo(p, real, repo):
    """10-03:經過 symlink 讀到倉裡的檔(例:aib test 的假 HOME 底下 ez2-aib → 倉)⇒ 用倉內實體路徑記。
    內容 sha 本來就是倉內那支檔的;用捷徑路徑記的話,假 HOME 每跑亂數不同 ⇒ 鑰匙每次都變、重算時檔也不見了(hdr-line 實踩)。
    改記倉內路徑後,重算時直接重讀倉內那支檔 ⇒ 改了它照樣必重跑。"""
    if real and repo:
        r = os.path.realpath(str(repo))
        if str(real) == r or str(real).startswith(r + "/"):
            return str(real)
    return p


# ---- 10-03 產物規則(docs/handoff/cloud-1002/PROPOSAL-born-during-run-products-1003.md)----------------------
# ── 10-05 複本規則:shutil 複製讀來源,只在「複本真的被用到」時才把來源算成輸入 ─────────────────────────────
#   回放量尺(replay_savings)量到的第一大槓桿:68 支刀測試把整棵 src/tests/tools 抄進暫存樹再只跑一兩支 ⇒
#   抄的動作讀了 ~880 支測試檔 ⇒ 改任何一支測試,這 103 支都重跑(平均省時 20.6% → 上限 32%)。
#   「用到」= 這份追蹤裡有別的事件指到那個複本:open/probe/missing/handoff 的路徑(完全相同),handoffdir 或
#   argv/bash 命令裡的路徑是它所在的目錄(git -C 複本、grep -r 複本 ⇒ 底下全算用到),相對路徑用事件的 cwd 解。
#   列目錄(listdir)不算用到內容(名字清單已由來源的 listdir 記)。複本再被複製(A→B→C)時,C 用到 ⇒ B、A 都算。
#   拿不準一律算用到(安全側);追不到的子行程本來就讓整列量不到。
def _cp_norm(p):
    p = os.path.normpath(str(p))
    for pre in ("/private/tmp/", "/private/var/"):
        if p.startswith(pre):
            p = p[len("/private"):]
    return p


# 這些命令不讀檔案內容(換目錄、印字、設變數、只看在不在/名字/中繼資料):它們的引數不算「用到複本」。
#   (之後真的讀,xtrace 會印出展開後的完整路徑,照樣抓得到。)aib-seat-cwd-bc 實踩:`cd 複本/src/cockpit/../..` 讓 1702 支全算用到。
_CP_NO_CONTENT = frozenset(("cd", "pushd", "popd", "echo", "printf", "export", "local", "declare", "typeset", "readonly",
                            "unset", "[", "[[", "test", "mkdir", "dirname", "basename", "realpath", "readlink", "ls", ":",
                            "true", "false", "shift", "set", "trap", "return", "exit", "command", "type", "hash", "umask"))


_CP_WHICH = {}


def _cp_which(name):
    if name not in _CP_WHICH:
        import shutil as _sh
        _CP_WHICH[name] = bool(_sh.which(name))
    return _CP_WHICH[name]


def _cp_tokens(ev):
    toks = []
    if ev.get("ch") == "argv":
        raw = [str(x) for x in (ev.get("argv") or [])]
        if ev.get("hooked") and raw and (os.path.basename(raw[0]) in ("bash", "node")
                                         or os.path.basename(raw[0]).startswith("python")):
            # 帶鉤子的子行程自己記讀取;它被交到的腳本由父行程記成 handoff(完整路徑)⇒ 引數裡的目錄不算用到
            return toks
    elif ev.get("ch") == "bx":
        raw = [w for w in re.split(r"\s+", str(ev.get("cmd") or "")) if w]
    else:
        return toks
    while raw and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", raw[0]):
        raw = raw[1:]                  # 開頭的變數指定(X=/路徑)不是讀取
    if raw and os.path.basename(raw[0].strip("'\"")) in _CP_NO_CONTENT:
        return toks
    if ev.get("ch") == "bx" and raw:
        w0 = raw[0].strip("'\"")
        if w0 in (".", "source"):
            raw = raw[1:2]             # source 只讀它那一支(命令名「.」本身不是路徑)
        elif "/" not in w0 and not _cp_which(w0):
            return toks                # shell 函式的呼叫列:函式體裡真的讀取會各自印出展開後的完整路徑(與下面主迴圈同一個判法)
        else:
            raw = raw[1:] if "/" not in w0 else raw
    cwd = ev.get("cwd")
    for t in raw:
        for part in ([t] + ([t.split("=", 1)[1]] if "=" in t else [])):
            part = part.strip("'\"")
            if not part or part.startswith("-"):
                continue
            if part.startswith("/"):
                toks.append(_cp_norm(part))
            elif cwd:
                toks.append(_cp_norm(os.path.join(str(cwd), part)))   # 不認得的字也解:對不上任何複本就沒影響
            else:
                toks.append("rel:" + part.strip("/"))                  # 沒有 cwd ⇒ 收尾用「路徑以它結尾/含它這一段」保守比對
    return toks


def used_copies(events):
    """回「被用到的複本」(正規化路徑)集合;只看帶 copy_to 的 open 事件的複本。
    只算**那支複本複製之後**的指涉:shutil 複製前會先 stat 複本(看在不在),那不是用到內容。"""
    copy_at = {}
    copies = []
    for i, e in enumerate(events):
        if e.get("ch") == "open" and e.get("copy_to"):
            d = _cp_norm(e["copy_to"])
            copies.append((_cp_norm(e["path"]), d))
            copy_at.setdefault(d, i)
    if not copies:
        return set()
    dsts = set(copy_at)
    refs = {}       # 路徑 -> 最後一次被指到的事件位置
    dirrefs = {}
    rel = {}

    def _put(table, key, i):
        if table.get(key, -1) < i:
            table[key] = i
    for i, e in enumerate(events):
        ch = e.get("ch")
        if ch == "open" and e.get("copy_to"):
            continue
        if ch in ("open", "probe", "missing", "handoff"):
            for k in ("path", "real"):
                if e.get(k):
                    _put(refs, _cp_norm(e[k]), i)
        elif ch == "handoffdir":
            for k in ("path", "real"):
                if e.get(k):
                    _put(dirrefs, _cp_norm(e[k]), i)
        elif ch in ("argv", "bx"):
            for t in _cp_tokens(e):
                if t.startswith("rel:"):
                    if t[4:]:
                        _put(rel, t[4:], i)
                else:
                    _put(refs, t, i)
                    _put(dirrefs, t, i)

    def _hit(d):
        at = copy_at[d]
        if refs.get(d, -1) > at:
            return True
        if any(i > at and (d.endswith("/" + r) or ("/" + r + "/") in d) for r, i in rel.items()):
            return True
        parent = os.path.dirname(d)
        while parent and parent != "/":
            if dirrefs.get(parent, -1) > at:
                return True
            parent = os.path.dirname(parent)
        return False
    used = {d for d in dsts if _hit(d)}
    changed = True
    while changed:              # A→B→C:C 用到 ⇒ 讀 B 那一筆是真的讀 ⇒ B 也算用到
        changed = False
        for src, dst in copies:
            if dst in used and src in dsts and src not in used:
                used.add(src)
                changed = True
    return used


def born_during_run(ev, t0, repo=None):
    """python 開的暫存檔是不是「這次跑測試才出生的產物」:全成立才是 ——
    (1) 有 t0(測試行程起來時記的開始時間 + 各暫存根當下的名字);(2) 開檔事件帶 mtime_ns(跟過 symlink 的目標時間),
    而且不早於開始時間;(3) 檔所在的暫存目錄(暫存根底下第一層)是開始之後才建的(t0 當下不在名單裡);
    (4) 不在倉裡。bash 側的開檔沒有 mtime ⇒ 一律照舊當輸入。
    為什麼安全:產物內容只由寫它的行程讀到的東西決定,那些讀取都在同一份追蹤、都進鑰匙;
    有追不到的子行程時整支本來就 unmeasured。"""
    if not t0:
        return False
    if ev.get("via") == "bash":
        # 10-03:bash 側沒有 mtime,改看鉤子用內建 `[ f -nt <trace>.t0 ]` 記的 born(比起跑標記新)
        if ev.get("born") is not True:
            return False
    else:
        m_ns = ev.get("mtime_ns")
        ns = t0.get("ns")
        if not isinstance(m_ns, int) or not isinstance(ns, int) or m_ns < ns:
            return False
    snap = t0.get("tmp") or {}
    for p in {str(ev.get("path") or ""), str(ev.get("real") or "")} - {""}:
        if repo and (p == str(repo) or p.startswith(str(repo).rstrip("/") + "/")):
            return False
        m = _ephemeral_argv_regex().match(p)
        if not m:
            return False
        parent, name = os.path.split(m.group(0))
        if parent not in snap or name in snap[parent] or name.startswith("tmux-") or m.group(0) == p:
            return False
    return True


# ---- 10-03 tmux 第一版:「測試自己的、沒有伺服器的」tmux 呼叫算量得到 ----------------------------------------
# 真 tmux 用戶端本身不讀檔(設定檔只有伺服器讀);沒有伺服器時每個指令都只回「no server running」。
# 所以一次 tmux 呼叫的結果只取決於「那個 socket 上有沒有伺服器」。下列全部成立 ⇒ 結果固定(一定沒伺服器)⇒ 不算外部:
#   (1) socket 在測試自己的暫存目錄(這份追蹤有人在那個暫存根底下寫過檔);系統 tmux 目錄(…/tmux-<uid>)一律不算
#   (2) 整份追蹤沒有任何會開伺服器的 tmux 指令(new-session/new/start-server/start、或沒給指令 = 預設 new-session)
#   (3) TMUX 沒設(或空)、或被 env -u 拿掉
#   (4) 執行的是暫存根與倉以外的 tmux(鉤子記下解析到的路徑);測試自己寫了名叫 tmux 的檔 ⇒ 整份不用本規則
# bash 裡的呼叫:環境取那個 bash 起來時的 inherit 紀錄;同一個 pid 的 xtrace 只要動過 TMUX/TMUX_TMPDIR/PATH
#   (賦值、export、unset、hash、alias、enable)⇒ 那個 pid 的 tmux 呼叫一律不放行(起始環境已不可信)。
TMUX_SERVER_CMDS = ("new-session", "start-server")
TMUX_SERVER_ALIASES = ("new", "start")
TMUX_OPTS_WITH_ARG = "cfLST"
TMUX_OPTS_FLAGS = "2CDlNuvV"
_TMUX_TAINT_RE = re.compile(r"\bTMUX(_TMPDIR)?\b|(^|[\s;(&|])(export\s+|declare\s+\S+\s+|typeset\s+\S+\s+|local\s+|readonly\s+)?PATH\+?="
                            r"|(^|[\s;(&|])(hash|alias|enable)(\s|$)")
_TMUX_ENV_U_RE = re.compile(r"(?<=\s)(-u\s*|--unset[=\s])TMUX(?=\s)")


def tmux_split(argv):
    """tmux 的全域選項 → (opts dict, 指令與其後的 token);拆不開回 None。"""
    opts, i = {}, 1
    while i < len(argv):
        a = str(argv[i])
        if a == "--":
            i += 1
            break
        if not a.startswith("-") or a == "-":
            break
        j = 1
        while j < len(a):
            c = a[j]
            if c in TMUX_OPTS_WITH_ARG:
                val = a[j + 1:]
                if not val:
                    i += 1
                    if i >= len(argv):
                        return None
                    val = str(argv[i])
                opts[c] = val
                break
            if c not in TMUX_OPTS_FLAGS:
                return None
            opts[c] = True
            j += 1
        i += 1
    return opts, [str(x) for x in argv[i:]]


def tmux_starts_server(argv):
    """這次 tmux 呼叫可能開伺服器 ⇒ True(拆不開也當 True)。指令名用前綴比對(tmux 收唯一前綴),
    鏈起來的指令(\\;)每一段都看;保守:任何 token 長得像開伺服器的指令就算。"""
    sp = tmux_split(argv)
    if sp is None:
        return True
    opts, rest = sp
    if opts.get("V"):
        return False
    if not rest:
        return True                      # 沒給指令 = new-session
    for t in rest:
        t = t.rstrip(";")
        if t in TMUX_SERVER_ALIASES or (len(t) >= 3 and any(c.startswith(t) for c in TMUX_SERVER_CMDS)):
            return True
    return False


def _tmux_wrapper_env(argv, tt, tx):
    """包裝程式(env/flock/timeout/…)對 TMUX_TMPDIR、TMUX 的影響 → (tt, tx, why);why 非 None ⇒ 不放行。"""
    while argv:
        w = os.path.basename(str(argv[0]))
        if w == "env":
            p = parse_env_argv(argv)
            if p is None or p["ignore_env"]:
                return tt, tx, "tmux-env-unparsed"
            if any(k in ("TMUX", "TMUX_TMPDIR", "PATH") for k in p["assign"]):
                return tt, tx, "tmux-env-assign"
            if "TMUX" in p["unset"]:
                tx = None
            if "TMUX_TMPDIR" in p["unset"]:
                tt = None
            argv = p["argv"]
            continue
        if w == "tmux":
            break
        argv = argv[1:]
    return tt, tx, None


def tmux_external_reason(fake_tmux, tmux_calls, tmux_seen, written_any):
    """external:tmux 的細分原因(只用來記;判斷照 own_serverless)。照檢查順序取第一個:
    fake-mixed(假 tmux 跟真的混用)/ tmux-file-written(測試寫過名叫 tmux 的檔)/ calls-unaccounted(呼叫數對不上)/
    tmux_socket_problem 的原因(socket 不在自己的暫存、TMUX 有設、-c …)/ server-started(條件都符合,但開了伺服器)。"""
    if fake_tmux:
        return "fake-mixed"
    if any(os.path.basename(str(w)) == "tmux" for w in written_any):
        return "tmux-file-written"
    if len(tmux_seen) != tmux_calls:
        return "calls-unaccounted"
    for p, _s in tmux_seen:
        if p:
            return str(p)
    if any(s for _p, s in tmux_seen):
        return "server-started"
    return "unknown"


def tmux_socket_problem(inner, cwd, tt, tx, exe, repo, written_any, uid=None):
    """一次 tmux 呼叫不是「測試自己的、確定沒伺服器的」⇒ 回原因;是 ⇒ None(伺服器有沒有開由呼叫端看整份追蹤)。"""
    uid = os.getuid() if uid is None else uid
    if not exe or not str(exe).startswith("/"):
        return "tmux-exe-unknown"
    if is_ephemeral_or_derived(exe, repo) or (repo and str(exe).startswith(str(repo).rstrip("/") + "/")):
        return "tmux-exe-in-fixture"
    sp = tmux_split(inner)
    if sp is None:
        return "tmux-argv-unparsed"
    opts, _rest = sp
    if "c" in opts:
        return "tmux-shell-command"
    if tx:
        return "tmux-TMUX-set"
    if isinstance(opts.get("S"), str):
        sock = opts["S"] if opts["S"].startswith("/") else os.path.join(cwd or "/", opts["S"])
    else:
        name = opts.get("L") if isinstance(opts.get("L"), str) else "default"
        sock = os.path.join(tt or "/tmp", "tmux-%d" % uid, name)
    sock = os.path.normpath(sock)
    m = _ephemeral_argv_regex().match(sock)
    if not m or os.path.basename(m.group(0)).startswith("tmux-"):
        return "tmux-socket-not-own-tmp"
    if repo and os.path.realpath(sock).startswith(os.path.realpath(str(repo)) + "/"):
        return "tmux-socket-in-repo"
    root = m.group(0) + "/"
    if not any(str(w).startswith(root) for w in written_any):
        return "tmux-socket-not-own-tmp"
    return None


_LOCAL_LISTEN_HOSTS = ("127.0.0.1", "::1", "0.0.0.0", "::", "localhost", "")


def network_targets(events):
    """10-12 P17(只多記資料,不影響沿用判斷):這支測試連了哪些網路目標,每個分三種 ——
    local-own   本機,而且這支測試(任何一個行程)在聽那個位址(listen 事件;同一行程連自己的早就不算網路、不會出現)
    local-other 本機,但不是這支測試在聽(別的程式、使用者的服務)
    remote      不是本機
    unknown     判不出目的地(舊格式事件)
    回 [{"dest", "kind"}](同一個目標只列一次)。紀錄池存成 net 欄,`aib ship why` / P17 表照它分。"""
    listens = {str(e.get("addr")) for e in events if e.get("ch") == "listen" and e.get("addr")}
    ports = set()
    for a in listens:
        if not a.startswith("unix:"):
            host, _, port = a.rpartition(":")
            if host.strip("[]") in _LOCAL_LISTEN_HOSTS:
                ports.add(port)
    out, seen = [], set()
    for e in events:
        if e.get("ch") != "external" or e.get("why") != "network":
            continue
        dest = str(e.get("dest") or "?")
        if dest in seen:
            continue
        seen.add(dest)
        if "local" not in e:
            kind = "unknown"          # 舊格式 / 判不出目的地:照實寫不知道,不猜
        elif not e.get("local"):
            kind = "remote"
        elif dest in listens or (not dest.startswith("unix:") and dest.rpartition(":")[2] in ports):
            kind = "local-own"
        else:
            kind = "local-other"
        out.append({"dest": dest, "kind": kind})
    return out


def inputs_from_events(events, repo=None):
    """Channels 1-3 + 5 as sortable strings; unmeasured/external aside.
    repo 給了就把 repo 底下的路徑寫成 ./相對(normalize_path)。"""
    items = []
    unmeasured = False
    external = False
    githead_seen = False   # 10-04:有對真倉的唯讀歷史查詢 ⇒ 收尾加 githead 輸入
    gitwt_seen = False     # 10-04:有對真倉的 grep/status/diff ⇒ 收尾加 gitwt 輸入
    gitrefs_seen = False   # 10-04 晚:有指名分支/標籤的查詢、或從本倉 clone ⇒ 收尾加 gitrefs 輸入
    gitref_names = set()   # 那些指名的 ref ⇒ 倉根同名路徑的存在狀態也進鑰匙(見 git_ref_tokens)
    git_queries = set()    # 10-05:(argv json, cwd) —— 對真倉的唯讀 git 查詢,收尾各重跑一次記結果(見 git_query_digest)
    tmux_calls = 0
    why = []
    saw_inherit = False
    inherit_pids = set()
    python_children = 0
    written = set()   # (pid, 路徑):**同一個行程**自己寫過的再讀是自己的產物,不是輸入(B 反例的分辨器)
    #   ★ 96:以前不分 pid ⇒ 子行程讀父行程寫的治具也被濾掉,治具收掉後 selfwrite 也記不到 ⇒ 孤兒(live40 主形之一)。
    tmp_missing = []  # 96:暫存根下的 missing 一般是隨機探測,但**被 argv 交出去的**缺路徑是真的負向輸入(<tmp11>/nope.json)
    selfwrites = []   # 結束時記的自己產物內容 hash;只有被 argv 交出去的才進鑰匙(見收尾)
    handoffs = []     # 96:交給非 python 子行程當下的內容(檔 sha / 治具目錄整棵 sha);一律進鑰匙
    hooked_children = 0     # 96:鉤子說「這個 python 子行程的環境帶鉤子」的數量,收尾對 inherit 精確對帳
    hooked_bash = 0         # 96b:帶 BASH_ENV 鉤子的 bash 子行程數(Popen 看到的 + bash 裡再叫 bash 的)
    bash_pids = set()       # 96b:inherit 事件 via=bash 的 pid
    py_inherit_pids = set()  # 10-04 晚:inherit 事件不是 via=bash 的 pid(python / node);bash exec python 時與 bash_pids 重疊
    bash_sources = []       # 96b:(pid, 絕對路徑) xtrace 看到的 source;收尾要有同 pid 的 open 對上
    bash_opens = set()      # 96b:(pid, 絕對路徑) 鉤子在 bash 裡記的 open
    legacy_argv = False     # 舊 trace(argv 事件沒有 hooked 欄)才走舊的軟下界
    exit_pids = set()
    # ★ 第一趟:只看 argv,依出現順序把每個獨立暫存根編成 <tmp1>/<tmp2>…;第二趟內容項用同一份映射對照。
    mapping = {}
    _t0_first = next((e for e in events if e.get("ch") == "t0"), None)
    for ev in events:
        if ev.get("ch") == "argv":
            normalize_argv(ev.get("argv") or [], repo=repo, mapping=mapping)
            # 10-03:子行程的 cwd 是「這次才建的暫存根」(例:git 在治具倉裡跑,cwd 給治具、argv 沒出現它)⇒ 也編角色 ID,
            #   否則鉤子雜湊那棵治具的 handoffdir 用原路徑記、每跑都變。開始前就有的暫存根不編(保留重讀偵測,ef56b A)。
            _cw = str(ev.get("cwd") or "")
            _m = _ephemeral_argv_regex().match(_cw) if _cw else None
            if _m and _t0_first and not (repo and _cw.startswith(str(repo).rstrip("/") + "/")):
                _par, _nm = os.path.split(_m.group(0))
                if _par in (_t0_first.get("tmp") or {}) and _nm not in _t0_first["tmp"][_par] and not _nm.startswith("tmux-"):
                    map_ephemeral(_m.group(0), mapping, repo=repo, create=True)
    # 96:本 trace 裡**任何行程**寫過的路徑(不分 pid):它們是產物,路徑帶隨機名 ⇒ 用角色 ID 記(鑰匙才會跨輪相同);
    #   暫存根下沒有任何人寫過的檔(先存在的輸入)仍記**原路徑**,重算時重讀(ef56b A/B:先存在的暫存輸入要抓得到)。
    written_any = {str(ev.get("path")) for ev in events if ev.get("ch") == "write"}
    left_at_exit = {str(ev.get("path")) for ev in events if ev.get("ch") == "selfwrite"}   # 結束時還在的自寫檔
    written_any |= {str(ev.get("real")) for ev in events if ev.get("ch") == "write" and ev.get("real")}
    # 10-03 tmux 第一版:每個 bash 起來時的 tmux 環境,與「xtrace 裡動過 TMUX/TMUX_TMPDIR/PATH」的 pid
    tmux_bash_env = {e.get("pid"): e for e in events if e.get("ch") == "inherit" and e.get("via") == "bash"}
    bash_argv0s = {}           # 10-04:argv0 → 這份追蹤裡 bash inherit 的筆數(直接執行 bash 檔的對帳用)
    for _e in events:
        if _e.get("ch") == "inherit" and _e.get("via") == "bash" and _e.get("argv0"):
            bash_argv0s[str(_e["argv0"])] = bash_argv0s.get(str(_e["argv0"]), 0) + 1
    bash_direct = {}
    tmux_tainted = {e.get("pid") for e in events if e.get("ch") == "bx"
                    and _TMUX_TAINT_RE.search(_TMUX_ENV_U_RE.sub(" ", " %s " % (e.get("cmd") or "")))}
    tmux_seen = []    # (不放行的原因或 None, 會不會開伺服器)
    # 10-05:bash 起來時就帶著改 git 倉的變數、或腳本自己提過它們(設、export、命令前置指定)⇒ 這個 pid 的 git 一律當「值不知道」
    git_tainted = {e.get("pid") for e in events if e.get("ch") == "inherit" and e.get("via") == "bash" and e.get("gitenv")}
    git_tainted |= {e.get("pid") for e in events if e.get("ch") == "bx" and _GIT_ENV_TAINT_RE.search(str(e.get("cmd") or ""))}
    t0 = next((e for e in events if e.get("ch") == "t0"), None)   # 10-03 產物規則:只採用第一筆
    argv_tmp_raw = sorted(mapping, key=len, reverse=True)   # argv 交出去的暫存根(原字串)

    def _content_path(_p, _real=None):
        np = normalize_path(repo, _p)
        if is_ephemeral_or_derived(_p, repo) and (_p in written_any or (_real or _p) in written_any
                                                  or any(_p == r or _p.startswith(r + "/") for r in argv_tmp_raw)):
            return map_ephemeral(np, mapping, repo=repo, create=True)
        return np
    _used_copies = used_copies(events)
    i18n_opens, i18n_loads, i18n_keys = {}, {}, set()   # 10-06 晚 翻譯字典以鍵為單位(見 i18n_narrow)
    i18n_copies = {}                                    # 10-07:字典被 shutil 複製到哪(實體來源 -> {實體複本})
    i18n_cmade, i18n_cwrites = {}, {}                   # 10-07:{(pid, 實體複本): 複製次數} / {(pid, 實體路徑): 寫入次數}
    for ev in events:
        ch = ev.get("ch")
        if ch == "open" and str(ev.get("path") or "").endswith((".json", ".mo")):
            _src = os.path.realpath(str(ev.get("real") or ev.get("path")))
            if ev.get("copy_to"):
                _dst = os.path.realpath(str(ev["copy_to"]))
                i18n_copies.setdefault(_src, set()).add(_dst)
                _ck = (ev.get("pid"), _dst)
                i18n_cmade[_ck] = i18n_cmade.get(_ck, 0) + 1
            else:
                _k = (ev.get("pid"), _src)
                i18n_opens[_k] = i18n_opens.get(_k, 0) + 1
        elif ch == "write" and ev.get("path"):
            # 10-07:複本被寫過(改了裡面的字典)⇒ 那份複本不算「只經翻譯函式讀」(見 _copy_keyed_only)。
            #   shutil 複製本身寫一次目的檔 ⇒ 同一行程的寫入次數扣掉複製次數,多出來的才算改過
            _wk = (ev.get("pid"), os.path.realpath(str(ev.get("real") or ev.get("path"))))
            i18n_cwrites[_wk] = i18n_cwrites.get(_wk, 0) + 1
        elif ch == "i18nload":
            _k = (ev.get("pid"), os.path.realpath(str(ev.get("path"))))
            i18n_loads[_k] = i18n_loads.get(_k, 0) + 1
            continue
        elif ch == "i18nkey":
            i18n_keys.add(str(ev.get("key")))
            continue
        if ch == "open" and ev.get("copy_to") and _cp_norm(ev["copy_to"]) not in _used_copies:
            continue                 # 10-05 複本規則:抄過去卻沒人用的複本,來源不算輸入(見 used_copies)
        if ch == "write":
            written.add((ev.get("pid"), str(ev.get("path"))))
            if ev.get("real"):
                written.add((ev.get("pid"), str(ev.get("real"))))
            continue
        if ch == "inherit":
            saw_inherit = True
            if ev.get("pid") is not None:
                inherit_pids.add(ev.get("pid"))
                if ev.get("via") == "bash":
                    bash_pids.add(ev.get("pid"))
                else:
                    py_inherit_pids.add(ev.get("pid"))
        elif ch == "open":
            _p = str(ev.get("path"))
            _real = str(ev.get("real") or _p)
            if ev.get("via") == "bash":
                bash_opens.add((ev.get("pid"), _p))
            if (ev.get("pid"), _p) in written or (ev.get("pid"), _real) in written or is_derived(_p):
                continue
            if born_during_run(ev, t0, repo):
                # 產物(PROPOSAL-born-during-run-products-1003):內容由這份追蹤裡別的讀取決定 ⇒ 只記「讀了這個產物」
                #   10-03:產物路徑裡的數字串(時間戳、pid、序號,如 RELAY-dm-…-113414-29102.txt)換成 N ——
                #   那些是跑的當下才定的,不是測試的輸入;不換的話鑰匙每跑都變。只動 produced:,一般輸入的路徑照舊逐字。
                items.append("produced:%s" % re.sub(r"\d+", "N", map_ephemeral(normalize_path(repo, _p), mapping, repo=repo, create=True)))
                continue
            items.append("open:%s:%s" % (_content_path(_via_repo(_p, ev.get("real"), repo), _real), ev.get("sha256")))
        elif ch == "listdir":
            _p = _via_repo(str(ev.get("path")), ev.get("real"), repo)
            if is_derived(_p) or is_ephemeral_or_derived(_p, repo):
                continue
            _sha = ev.get("names_sha256")
            _made = ev.get("made") or []
            if _made and ev.get("names_sha256_wo_made") and isinstance(_made, list) \
                    and not any(os.path.join(str(ev.get("path")), str(n)) in left_at_exit for n in _made):
                # 10-07:名單裡有本行程新建、結束前又刪掉的檔 ⇒ 那是測試自己的暫時產物,不算目錄的輸入
                #   (結束時還在的照舊算:重算時它在,名單才對得上;先就存在的檔不會被記成新建)。
                _sha = ev.get("names_sha256_wo_made")
            items.append("listdir:%s:%s" % (normalize_path(repo, _p), _sha))
        elif ch == "missing":
            _p = str(ev.get("path"))
            if (ev.get("pid"), _p) in written or is_derived(_p):
                continue
            if is_ephemeral_or_derived(_p, repo):
                # 暫存根底下「不存在」的路徑是 tempfile 之類的隨機探測(每跑名字都不同),不是輸入;
                # repo 內與其他絕對路徑的 missing 照記(ef56b C 反例:缺檔變目錄要抓得到)。
                # 96:先收著,收尾只把被 argv 交出去的那些進鑰匙(與 selfwrite 同一套規則)。
                tmp_missing.append(map_ephemeral(normalize_path(repo, _p), mapping, repo=repo, create=False))
                continue
            items.append("missing:%s" % normalize_path(repo, _p))
        elif ch == "probe":
            # T0-1(09-14):python 側的存在探測(os.stat/lstat/access,含 os.path.exists/isfile/isdir、Path.exists),
            #   與 bash 側 `[ -f x ]` 同一種項。本 trace 裡任何行程寫過的路徑是產物不是輸入(written_any,不分 pid:
            #   父行程探子行程有沒有產出);治具(暫存根、倉外)與衍生檔不記——與 bash probe 同一條政策。
            _p = str(ev.get("path"))
            if _p in written_any or is_derived(_p) or is_ephemeral_or_derived(_p, repo):
                continue
            _st = str(ev.get("state") or "")
            if _st not in ("file", "dir", "missing"):
                unmeasured = True
                if "probe-state-unknown" not in why:
                    why.append("probe-state-unknown")
                continue
            items.append("probe:%s:%s" % (normalize_path(repo, _p), _st))
        elif ch == "selfwrite":
            # 測試自己寫的檔的最終內容,先收著;收尾只把**被 argv 交給子行程的暫存路徑底下**的那些進鑰匙
            # (ef56b B:自己寫自己讀的產物不是輸入;grok (1).3:交給子行程的內容要有指紋 ⇒ 同名不同內容必不同)。
            _p = str(ev.get("path"))
            selfwrites.append((map_ephemeral(normalize_path(repo, _p), mapping, repo=repo, create=False), ev.get("sha256")))
        elif ch == "handoff":
            _p = str(ev.get("path"))
            if is_derived(_p):
                continue
            mp = map_ephemeral(normalize_path(repo, _p), mapping, repo=repo, create=False)
            if ev.get("tree_sha256"):
                handoffs.append("handoffdir:%s:%s" % (mp, ev.get("tree_sha256")))
            else:
                handoffs.append("handoff:%s:%s" % (mp, ev.get("sha256")))
        elif ch == "exit":
            if ev.get("pid") is not None:
                exit_pids.add(ev.get("pid"))
        elif ch == "argv":
            argv = ev.get("argv") or []
            items.append("argv:%s" % json.dumps(normalize_argv(argv, repo=repo, mapping=mapping), ensure_ascii=False))
            if argv:
                kind, detail = classify_child(ev, repo=repo)
                if kind == "python":
                    python_children += 1
                    if "hooked" in ev:
                        hooked_children += 1 if ev.get("hooked") else 0
                    else:
                        legacy_argv = True
                elif kind == "bash":
                    if ev.get("via") != "shim":
                        hooked_bash += 1   # 墊片的那一筆與 Popen 的是同一個行程,只數 Popen 那一筆(bash 裡再叫 bash 只走墊片:不數)
                    if os.path.basename(str(argv[0])) == "tmux":
                        # 10-03:用完整路徑直接執行 bash 寫的假 tmux ⇒ 鉤子看 shebang 記成 shell=bash,這裡分成 bash;
                        #   它的 inherit 卻會被假 tmux 對帳算進去 ⇒ 呼叫數少算、對不上、冤枉成 external(aib-memory-0930 實踩)。
                        #   也算一次 tmux 呼叫(對帳仍要求次數一模一樣;不走「自己的沒伺服器 tmux」規則)。
                        tmux_calls += 1
                        tmux_seen.append(("tmux-fake-direct", False))
                elif kind == "untracked":
                    unmeasured = True
                    _w = "child-untracked:%s" % detail
                    if _w not in why:
                        why.append(_w)
                elif kind in ("githead", "gitwt", "gitrefs") and git_query_ok(argv):
                    git_queries.add((json.dumps([str(x) for x in argv]), str(ev.get("cwd") or "")))
                elif kind == "githead":
                    githead_seen = True
                elif kind == "gitwt":
                    gitwt_seen = True
                elif kind == "gitrefs":
                    gitrefs_seen = True
                    gitref_names.update(git_ref_tokens(argv))
                elif kind == "external" and detail == "tmux":
                    tmux_calls += 1          # 10-03:先數,收尾對帳(可能是測試自己的假 tmux)
                    _sa = [str(x) for x in argv]
                    _in, _x, _c = _unwrap(_sa, ev.get("cwd"))
                    _in = _in or _sa
                    if "tmux_tmpdir" not in ev:
                        _p = "tmux-env-unknown"
                    else:
                        _tt, _tx, _p = _tmux_wrapper_env(_sa, ev.get("tmux_tmpdir"), ev.get("tmux"))
                        _p = _p or tmux_socket_problem(_in, _c, _tt, _tx, ev.get("tmux_exe"), repo, written_any)
                    tmux_seen.append((_p, tmux_starts_server(_in)))
                elif kind == "external":
                    external = True
                    _w = "external:%s" % detail
                    if _w not in why:
                        why.append(_w)
        elif ch == "bx":
            if _bx_noise(ev):
                continue
            if re.search(r"/dev/(?:tcp|udp)/", str(ev.get("cmd") or "")):
                # 10-04(共用驗收 bash A8 抓到):bash 的 /dev/tcp 連線不經過任何子行程 ⇒ 以前完全看不到 = 碰網路照沿用
                external = True
                if "external:bash-dev-tcp" not in why:
                    why.append("external:bash-dev-tcp")
            _cwd = str(ev.get("cwd") or "")
            argv = bash_command_argv(ev.get("cmd") or "")
            if argv is None:
                unmeasured = True
                _w = "bash-cmd-unparsed:%s" % str(ev.get("cmd") or "")[:60]
                if _w not in why:
                    why.append(_w)
                continue
            # 10-04:賦值要在拆包裝(env/flock/timeout…)之前剝掉 —— 以前先拆,`EZ2_FLOCK=/usr/bin/flock` 這種「值是 flock 路徑」的
            #   單純賦值被當成 flock 命令 ⇒ flock-unparsed(pane-right-ai 等 10 支實踩)。
            while argv and re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=", str(argv[0])):
                argv = argv[1:]
            if not argv:
                continue
            _before = argv
            argv, _sw_why, _cwd = _unwrap(argv, _cwd)
            # env 只能 exec 真的程式,不能叫殼裡的函式 ⇒ 經過 env 的名字不套「找不到 = 函式」的放行
            _via_env = argv is not None and any(os.path.basename(str(x)) == "env" for x in _before[:len(_before) - len(argv)])
            if argv is None:
                unmeasured = True
                _w = "child-untracked:%s" % _sw_why
                if _w not in why:
                    why.append(_w)
                continue
            while argv and re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=", argv[0]):
                argv = argv[1:]          # 賦值(D=/x)與命令前置的 FOO=1 不是命令
            if not argv:
                continue
            w0 = argv[0]
            if w0 in (".", "source"):
                if len(argv) > 1:
                    _sp = argv[1] if argv[1].startswith("/") else os.path.join(_cwd, argv[1])
                    bash_sources.append((ev.get("pid"), os.path.normpath(_sp)))
                continue
            if w0 in ("[", "[[", "test"):
                # 存在探測:[ -f x ] / [[ -d y ]] / test -e z ⇒ 真樹的路徑記 probe:(file/dir/missing);治具的不記
                for i in range(1, len(argv) - 1):
                    if argv[i] in BASH_PROBE_FLAGS and argv[i + 1] not in ("]", "]]"):
                        _pp = argv[i + 1]
                        _ap = _pp if _pp.startswith("/") else os.path.join(_cwd, _pp)
                        if not is_ephemeral_or_derived(_ap, repo) and not is_derived(_ap):
                            items.append("probe:%s:%s" % (normalize_path(repo, os.path.normpath(_ap)), _probe_state(_ap)))
                continue
            # 10-03:只放行最窄的一種 eval —— `eval 'X=${Y:-預設}'`(把某個變數的值指定給另一個變數,間接選擇器的慣用寫法)。
            #   它不執行命令、不讀檔、不展開命令替換;Y 的值來自環境(在環境指紋裡)或前面的命令(xtrace 看得到)。其餘 eval 照舊量不到。
            if w0 == "eval" and len(argv) == 2 and _EVAL_ASSIGN_RE.fullmatch(argv[1]):
                continue
            if w0 == "eval":
                # 10-04 晚:eval 本身不算量不到 —— DEBUG trap 對 eval 裡的每一條命令照樣觸發,裡面的讀取(含 `< 檔` 重導、
                #   mapfile、source、組出來的路徑)跟寫在外面完全一樣被記(test-verify-bash-eval-1004 實測對照)。
                #   真正會逃掉的是 eval 裡關掉追蹤(trap - DEBUG、set +x …),那由 bash-debug-trap-overridden / bash-xtrace-off
                #   獨立抓,不靠這裡。(96b 起一律量不到是保守起見,當時沒驗;全套第二輪 10 支 191 秒只卡這個。)
                continue
            if w0 in BASH_BUILTINS:
                continue
            if "/" not in w0 and not _via_env:
                import shutil as _sh
                if not _sh.which(w0):
                    continue          # 找不到的名字當它是函式/別名(函式體本來就在被 source 的檔裡)
            kind, detail = classify_child({"argv": argv, "cwd": _cwd, "hooked": True, "shell": "bash" if os.path.basename(w0) == "bash" else None,
                                           "gitenv": {"_unknown": True} if ev.get("pid") in git_tainted else None}, repo=repo)
            if kind == "ok" and os.path.basename(w0) in CHILD_STDIN_FILTERS:
                _bad = _bx_filter_reads(argv, _cwd, repo, handoffs)
                if _bad:
                    kind, detail = "untracked", _bad
            if kind == "python" and w0.startswith("/") and w0 in bash_argv0s:
                # 10-08:bash 裡用絕對路徑叫一支名字叫 python3、其實是 bash 寫的替身(測試用 EZ2_PY3 指過去的假 python3,
                #   放暫存、測完就刪 ⇒ 分析時讀不到它的 #!):它以 bash 身分回報過(bash inherit 的 argv0 = 這條路徑)⇒
                #   不是 Python 子行程,走下面 bash 替身那條對帳。以前誤判成 Python ⇒ 期待 Python 的 inherit ⇒ 整列量不到。
                #   (Python Popen 直接執行替身不會誤判:鉤子當下讀得到 #!,記成 bash。)
                kind = "untracked"
            if kind == "python":
                hooked_children += 1
                python_children += 1
            elif kind == "bash":
                pass   # bash 裡再叫 bash:走墊片會再掛鉤子,inherit 會出現;不進期望數(墊片那筆也不數,避免重複)
            elif kind == "untracked" and w0.startswith("/") and w0 in bash_argv0s:
                # 10-04:bash 裡用絕對路徑直接執行一支 bash 寫的檔(測試的替身 stub-py3 之類):它自己掛鉤子、
                #   主腳本內容由它的 DEBUG trap 記(BASH_SOURCE)。收尾對帳:這條路徑被叫幾次,就要有幾筆 argv0 相同的 bash inherit。
                bash_direct[w0] = bash_direct.get(w0, 0) + 1
            elif kind == "untracked":
                unmeasured = True
                _w = "child-untracked:%s" % detail
                if _w not in why:
                    why.append(_w)
            elif kind in ("githead", "gitwt", "gitrefs") and git_query_ok(argv):
                git_queries.add((json.dumps([str(x) for x in argv]), str(_cwd or "")))
            elif kind == "githead":
                githead_seen = True
            elif kind == "gitwt":
                gitwt_seen = True
            elif kind == "gitrefs":
                gitrefs_seen = True
                gitref_names.update(git_ref_tokens(argv))
            elif kind == "external" and detail == "tmux":
                tmux_calls += 1
                _ie = tmux_bash_env.get(ev.get("pid"))
                if ev.get("pid") in tmux_tainted:
                    _p = "tmux-env-changed"
                elif _ie is None or "tmux_tmpdir" not in _ie:
                    _p = "tmux-env-unknown"
                else:
                    _tt, _tx, _p = _tmux_wrapper_env(_before, _ie.get("tmux_tmpdir"), _ie.get("tmux"))
                    _exe = argv[0] if argv[0].startswith("/") else _ie.get("tmux_exe")
                    _p = _p or tmux_socket_problem(argv, _cwd, _tt, _tx, _exe, repo, written_any)
                tmux_seen.append((_p, tmux_starts_server(argv)))
            elif kind == "external":
                external = True
                _w = "external:%s" % detail
                if _w not in why:
                    why.append(_w)
        elif ch == "unmeasured":
            unmeasured = True
            why.append(str(ev.get("why") or "unmeasured"))
        elif ch == "external":
            external = True
            why.append("external:" + str(ev.get("why") or ""))
    if not saw_inherit:
        unmeasured = True
        why.append("no-hook")
    if legacy_argv and python_children and len(inherit_pids) < 1 + min(python_children, 3):
        # 舊 trace:Parent inherit plus at least one child. Soft bound: missing child
        # inherit means sitecustomize did not load in the grandchild.
        if "python-child-no-sitecustomize" not in why:
            unmeasured = True
            why.append("python-child-no-sitecustomize")
    if hooked_bash and len(bash_pids) < hooked_bash:
        # 96b:說帶鉤子的 bash 子行程,每一個都要有自己的 inherit(via=bash);少 ⇒ 有 bash 沒被追到(例如 env 被腳本洗掉)
        unmeasured = True
        why.append("bash-child-no-inherit:%d<%d" % (len(bash_pids), hooked_bash))
    for _pid, _sp in bash_sources:
        if (_pid, _sp) not in bash_opens and (_pid, _sp.replace("/./", "/")) not in bash_opens:
            unmeasured = True
            _w = "bash-source-unhashed:%s" % _sp
            if _w not in why:
                why.append(_w)
    # 10-04 晚:以前是 inherit_pids - bash_pids —— bash `exec python …` 時同一個 pid 先有 bash、再有 python 的 inherit,
    #   相減把那個 python 也扣掉 ⇒ 少一 ⇒ 整列誤判量不到(deliver-gate 67<68、persona-extract 74<75)。改成 python 自己的集合。
    python_inherits = py_inherit_pids
    if not legacy_argv and hooked_children and len(python_inherits) < 1 + hooked_children:
        # ★ 96:精確對帳——鉤子說帶鉤子的 python 子行程,每一個都要有自己的 inherit;少一個就是有子行程沒被追到
        #   (以前的軟下界 1+min(n,3) 在 ≥3 個子行程時放過洗掉 trace 變數的那一個)。
        unmeasured = True
        why.append("python-child-no-inherit:%d<%d" % (len(python_inherits), 1 + hooked_children))
    # ★ 契約(grok/ef56b 09-11 審 28):argv 裡每一個被正規化成 <tmp>/… 的路徑,同一列要有至少一條
    #   open/listdir/missing/selfwrite 落在它底下(內容訊號);對不上 ⇒ 這列 unmeasured、不准 would-skip。
    #   否則「同名不同內容」的治具會在 argv 上長得一樣而內容不在鑰匙裡。
    # ★ 契約(grok/ef56b 09-11/12 審 28):argv 裡每一個角色 ID 路徑(含裸 <tmpN>),同一列要有至少一條
    #   open/listdir/missing/selfwrite 落在它底下;內容項用**同一份映射**對照(不是拿 placeholder 去 grep 原字串);
    #   對不上 ⇒ 這列 unmeasured、不准 would-skip。
    # 10-04:argv 項是 JSON 字串,參數裡的引號存成 \" ⇒ 路徑要在反斜線停(dm-queue-v2-70 的 JSON 參數抓成 `<tmp2>/root\`,
    #   180 秒那支因此永遠 argv-tmp-path-without-content)。真的路徑含反斜線 ⇒ 截在前面,內容對不上 ⇒ 照舊量不到(保守)。
    _ph = r"<tmp\d+>(?:/[^\s'\"\]\),;\\]*)?"
    argv_tmp_paths = []
    for it in items:
        if it.startswith("argv:"):
            for m in re.finditer(_ph, it):
                ap = m.group(0).rstrip("/")
                if ap not in argv_tmp_paths:
                    argv_tmp_paths.append(ap)
    for swp, sha in selfwrites:
        if any(swp == ap or swp.startswith(ap + "/") for ap in argv_tmp_paths):
            items.append("selfwrite:%s:%s" % (swp, sha))
    items.extend(handoffs)   # 交出去的內容一律是輸入(治具的整棵、或真樹裡被 grep/sed 讀的那支檔)
    for mp in tmp_missing:
        if any(mp == ap or mp.startswith(ap + "/") for ap in argv_tmp_paths):
            items.append("missing:%s" % mp)
    content_paths = []
    for it in items:
        if it.startswith(("open:", "listdir:", "selfwrite:", "handoff:", "handoffdir:")):
            content_paths.append(map_ephemeral(it.split(":", 1)[1].rpartition(":")[0], mapping, repo=repo, create=False))
        elif it.startswith("produced:"):
            content_paths.append(it.split(":", 1)[1])     # 產物:內容由追蹤裡的讀取決定,算「有內容」
        elif it.startswith("missing:"):
            content_paths.append(map_ephemeral(it.split(":", 1)[1], mapping, repo=repo, create=False))
    orphan = None
    for ap in argv_tmp_paths:
        if not any(cp == ap or cp.startswith(ap + "/") for cp in content_paths):
            orphan = ap
            break
    if orphan is not None:
        unmeasured = True
        why.append("argv-tmp-path-without-content:" + orphan)
    # 10-03:測試常在暫存目錄放一支假 tmux(117 支測試檔)。假的是 bash 腳本 ⇒ 鉤子會記一筆 inherit(argv0 = 它的路徑),
    #   它讀了什麼也照常記。對帳:叫了幾次 tmux,就要有幾筆「暫存目錄裡、名叫 tmux」的 bash inherit;
    #   一模一樣才當作全是假的(不算外部)。少一筆 ⇒ 至少有一次是真 tmux(或沒追到的假)⇒ 照舊 external。
    #   真 tmux 不會留 inherit(它不是 bash),所以真的混在裡面一定對不上。假 tmux 裡再叫真 tmux ⇒ 多一次呼叫、沒有 inherit ⇒ 對不上。
    if tmux_calls:
        fake_tmux = sum(1 for e in events if e.get("ch") == "inherit" and e.get("via") == "bash"
                        and os.path.basename(str(e.get("argv0") or "")) == "tmux"
                        and is_ephemeral_or_derived(str(e.get("argv0")), repo)
                        and not str(e.get("argv0")).startswith(str(repo or "\0").rstrip("/") + "/"))
        # 10-03 tmux 第一版(檔頭說明在 tmux_split 上方):沒有假 tmux、測試沒寫過名叫 tmux 的檔、每一次都是
        #   「自己的暫存 socket、TMUX 沒設、真 tmux」、整份沒有會開伺服器的指令 ⇒ 每一次都只會回 no server ⇒ 不算外部。
        own_serverless = (fake_tmux == 0 and len(tmux_seen) == tmux_calls
                          and not any(os.path.basename(w) == "tmux" for w in written_any)
                          and all(p is None and not s for p, s in tmux_seen))
        if fake_tmux != tmux_calls and not own_serverless:
            external = True
            why.append("external:tmux")
            # 10-12(a seat 同意,DESIGN-tmux-own-server-1012 甲):只多記資料 —— 是哪一條不符合「自己的、沒開伺服器的 tmux」。
            #   external:tmux 那筆照舊(既有測試與統計看它);這一筆用 external: 開頭,統計(rerun_reasons / measurability_report)
            #   自動歸到「外部」、不會被當成量不到的原因(審查第二輪);逐支分類讀紀錄池的 why。不影響沿用判斷。
            why.append("external:tmux:" + tmux_external_reason(fake_tmux, tmux_calls, tmux_seen, written_any))
    # ★ ef56b 第 6 條:os._exit / 信號 / 致命錯誤不跑 atexit ⇒ 沒有 selfwrite 也沒有 exit 紀錄;
    #   每個 inherit 的行程都要有對應的 exit 紀錄,缺 ⇒ unmeasured(完成證據不能用 rc=0 或看過 inherit 代替)。
    for _p, _n in bash_direct.items():
        # 同一條路徑也可能被 python Popen 直接執行(那邊有自己的 hooked_bash 對帳),兩邊的 inherit 不能共用
        _popen = sum(1 for e in events if e.get("ch") == "argv" and (e.get("argv") or [None])[0] == _p)
        if bash_argv0s.get(_p, 0) < _n + _popen:
            unmeasured = True
            _w = "child-untracked:%s" % os.path.basename(_p)
            if _w not in why:
                why.append(_w)
    _missing_exit = sorted(p for p in inherit_pids if p not in exit_pids)
    if _missing_exit:
        unmeasured = True
        why.append("no-exit-record:%s" % ",".join(str(p) for p in _missing_exit[:3]))
    # T0-1:同一路徑既有內容項(open:/listdir:/handoff:)就不必再留 probe:(內容 sha 已蘊含「在、而且是檔/目錄」);
    #   只留「探了但從沒讀」的那些——那才是 T0-1 要補的輸入。
    _content_only = {it.split(":", 1)[1].rpartition(":")[0] for it in items if it.startswith(("open:", "listdir:", "handoff:"))}
    if _content_only:
        items = [it for it in items if not (it.startswith("probe:") and it.split(":", 1)[1].rpartition(":")[0] in _content_only)]
    # the maintainer 09-29(選 B):git 版本與設定只進「真的跑了 git」的測試的鑰匙(以前在每一支的環境指紋裡 ⇒ git 一升全套重跑、跨機器全比不到)。
    if any(it.startswith("argv:") and _argv_runs_git(it) for it in items):
        items.append("tool:git:%s" % git_tool_fp())
    for _a, _c in sorted(git_queries):
        _na, _nc = git_query_norm(json.loads(_a), _c, repo)
        items.append("gitq:%s:%s" % (json.dumps({"a": _na, "c": _nc}, ensure_ascii=False), git_query_digest(repo, _na, _nc)))
    if githead_seen:
        items.append("githead:%s" % git_repo_state(repo))
    if gitwt_seen:
        items.append("gitwt:%s" % git_worktree_state(repo))
    if gitrefs_seen:
        items.append("gitrefs:%s" % git_refs_state(repo))
        for _n in sorted(gitref_names):
            _rp = os.path.join(str(repo), _n)
            items.append("probe:%s:%s" % (normalize_path(repo, _rp), _probe_state(_rp)))
    i18n_copies["@written"] = {k[1] for k, n in i18n_cwrites.items() if n > i18n_cmade.get(k, 0)}
    items = i18n_narrow(sorted(set(items)), repo, i18n_opens, i18n_loads, i18n_keys, i18n_copies)
    items = sorted(set(items))
    digest = hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()
    # 10-03:同一個原因只留一筆、總數設上限。雲端實量:test-verify-bash-child-96b 一列 why 有 403 萬筆 trace-corrupt-line(88MB),
    #   10 列就把沿用池撐到 965MB,每次 aib test 讀池都吃掉好幾 GB 記憶體(被系統砍成 rc 137)。why 只是說明「為什麼量不到」,
    #   判斷只看 unmeasured/external 兩個旗標,去重不會改任何判斷。
    why = _dedupe_why(why)
    return items, digest, unmeasured, external, why


def _argv_runs_git(item):
    try:
        argv = json.loads(item.partition(":")[2])
    except (ValueError, TypeError):
        return False
    if argv and os.path.basename(str(argv[0])) == "env":
        # 10-02:env … git 也是跑了 git(不認的話 git 版本不進鑰匙);env 拆不開 ⇒ 當它可能跑了 git(多一項只會多重跑)
        argv = _strip_wrappers([str(x) for x in argv])
        if argv is None:
            return True
    return bool(argv) and os.path.basename(str(argv[0])) == "git"


def make_key(test_rel, input_fp, env_fp):
    raw = "%s|%s|%s" % (test_rel, input_fp, env_fp)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def child_env(repo: Path, trace: Path, environ=None):
    environ = dict(os.environ if environ is None else environ)
    shim = str(HERE / "bash-shim")
    environ["PATH"] = shim + os.pathsep + environ.get("PATH", os.defpath)
    environ["PYTHONPATH"] = str(HERE) + os.pathsep + environ.get("PYTHONPATH", "")
    environ["EZ2_VERIFY_TRACE"] = str(trace)
    # 10-08:墊片(bash-shim/_shim)記 cat/cp/head/tail 讀的檔要起 python 算雜湊 —— 以前用 PATH 上的 python3,測試把假的
    #   python3(bash 寫的,裡面又 cat)放在 PATH 最前面 ⇒ 墊片 → 假 python3 → cat → 墊片 … 無限遞迴(實測炸出 6000 多個行程)。
    #   改用追蹤器自己這支真的 python(不算進環境指紋:VOLATILE_ENV)。
    environ["EZ2_VERIFY_PY"] = sys.executable
    environ["EZ2_VERIFY_RUNNER_PID"] = str(os.getpid())   # 10-04 修法 A 第二版:測試行程靠它認出「我是測試本身」(父行程 = 這個 pid)
    environ["BASH_ENV"] = str(HERE / "bash-shim" / "_trace.bash")   # 96b:#!/bin/bash 直接執行的腳本也掛鉤子(墊片只接得到 PATH 上的 bash)
    _no = environ.get("NODE_OPTIONS", "")
    if str(NODE_AUDIT) not in _no:      # 10-04:node(含它的子行程,繼承 NODE_OPTIONS)也掛接頭
        environ["NODE_OPTIONS"] = ("%s --require %s" % (_no, NODE_AUDIT)).strip()
    environ["PYTHONDONTWRITEBYTECODE"] = "1"
    if "AIB_HOOK_CACHE" not in environ:           # 10-08:掛鉤編譯一次、快取在真正使用者的快取資料夾(測試的 HOME 每支都換)
        _hc = hook_cache_dir()
        if _hc:
            environ["AIB_HOOK_CACHE"] = _hc
    environ["PYTHONNOUSERSITE"] = "1"
    if native_guard_on(repo, environ):       # 10-07 安全網:C 層的開檔也記(見 native_unrecorded)
        lib = ldp_library()
        if lib:
            old = environ.get("LD_PRELOAD", "")
            if lib not in old.split(":"):
                environ["LD_PRELOAD"] = lib + (":" + old if old else "")
            environ["AIB_LDP_TRACE"] = str(trace) + ".ldp"
    return environ


# ── 10-07 安全網:LD_PRELOAD 記下 C 層的開檔(試驗報告 docs/handoff/cloud-1002/SPIKE-ldpreload-1007.md)────────────────────
#   Python 鉤子靠審計事件;C 擴充自己開檔(lxml、pyarrow、libpq…)沒有事件。今天補了名單,名單外的仍看不到。
#   這裡在每個行程掛一支小程式庫記 C 層的 open/fopen/opendir/connect;跑完比:掛了 Python 鉤子的行程用 C 層讀了倉內的檔,
#   鉤子卻一筆都沒記到 ⇒ 那支量不到(native-read-unrecorded:<路徑>)。只會讓測試多跑,不會讓它錯沿用。
#   只在 Linux、這台有 C 編譯器時開;aib 自己的倉預設不開(AIB_NATIVE_GUARD=1/0 強制開關)。
def hook_cache_dir():
    """掛鉤編譯快取放哪(10-08):aib 自己的快取資料夾(真正使用者的家目錄,不是測試換掉的 HOME);建不了 ⇒ None(照舊每次編譯)。"""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    d = os.path.join(base, "ez2-aib", "hookpyc")
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
    except OSError:
        return None
    return d


LDP_SRC = HERE / "ldp-shim" / "aib_ldp.c"


def native_guard_on(repo, environ=None):
    env = os.environ if environ is None else environ
    forced = str(env.get("AIB_NATIVE_GUARD") or "")
    if forced in ("0", "1"):
        return forced == "1" and sys.platform.startswith("linux")
    if not sys.platform.startswith("linux"):
        return False
    try:
        return not (Path(repo) / "tests" / "aib_testlib.py").exists()   # aib 自己的倉
    except (OSError, TypeError):
        return False


_LDP_LIB = {}
LDP_WHY = {}          # 10-08:安全網沒開的原因(tag ⇒ 字串),給呼叫端照實印


def _ldp_dir(tag):
    """程式庫放哪:aib 自己的快取資料夾(家目錄底下)。10-08 a CI machine 實踩:/tmp 掛成不能執行的機器(伺服器、CI 常見)
    放在 /tmp ⇒ 載入失敗,每支都「量不到」、每次全部重跑,每支的錯誤輸出還多一行系統錯誤。
    路徑裡保留「/aib-ldp-」:環境指紋把它從 LD_PRELOAD 濾掉(使用者自己的照記)。"""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "ez2-aib" / ("aib-ldp-%s" % tag)


def _ldp_loads(lib):
    """真的掛一次看看:/bin/true 帶 LD_PRELOAD 跑,ld.so 抱怨就算掛不上。"""
    try:
        p = subprocess.run(["/bin/true"], env=dict(os.environ, LD_PRELOAD=str(lib)), capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        return False, "%s" % type(e).__name__
    if p.returncode != 0 or "cannot be preloaded" in (p.stderr or "") or "ld.so" in (p.stderr or ""):
        return False, (p.stderr or "rc=%d" % p.returncode).strip().splitlines()[0][:200]
    return True, ""


def ldp_library():
    """編好的安全網程式庫路徑(aib 快取資料夾底下,依原始碼雜湊共用);沒有編譯器 / 編不起來 / 掛不上 ⇒ None(安全網不開,
    原因記在 LDP_WHY[tag])。"""
    try:
        src = LDP_SRC.read_bytes()
    except OSError:
        return None
    tag = hashlib.sha256(src).hexdigest()[:12]
    if tag in _LDP_LIB:
        return _LDP_LIB[tag]
    out = _ldp_dir(tag) / "libaibldp.so"
    if not out.exists():
        import shutil
        cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if not cc:
            _LDP_LIB[tag] = None
            return None
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name("libaibldp.%d.so" % os.getpid())
            p = subprocess.run([cc, "-shared", "-fPIC", "-O2", "-o", str(tmp), str(LDP_SRC), "-ldl"], capture_output=True,
                               stdin=subprocess.DEVNULL, timeout=120)
            if p.returncode != 0:
                _LDP_LIB[tag] = None
                return None
            os.replace(str(tmp), str(out))
        except (OSError, subprocess.SubprocessError):
            _LDP_LIB[tag] = None
            return None
    ok, why = _ldp_loads(out)
    if not ok:
        LDP_WHY[tag] = why
        _LDP_LIB[tag] = None
        return None
    _LDP_LIB[tag] = str(out)
    return _LDP_LIB[tag]


def native_unrecorded(events, ldp_path, repo):
    """C 層讀了、Python 鉤子沒記的倉內檔(相對路徑,排序)。只看掛了 Python 鉤子的行程(inherit 沒有 via)與 Node 行程;
    排除衍生檔(__pycache__)、.git、追蹤器自己、測試自己寫的檔、不是檔的。安全網檔不存在 ⇒ []。"""
    try:
        fh = open(ldp_path, encoding="utf-8", errors="replace")
    except OSError:
        return []
    root = os.path.realpath(str(repo))
    py = {e.get("pid") for e in events if e.get("ch") == "inherit" and not e.get("via")}
    # 10-07:Node 行程也比(Node 接頭記 require / import / fs);它找模組時用內部 C++ 讀 node_modules 底下的 package.json,
    #   接頭看不到 —— 那部分由鎖檔把關(鎖檔變了本來就全部重跑),所以 Node 行程讀 node_modules 底下的不算
    node = {e.get("pid") for e in events if e.get("ch") == "inherit" and e.get("via") == "node"}
    seen, written = set(), set()
    for e in events:
        for k in ("path", "real"):
            if e.get(k):
                rp = os.path.realpath(str(e[k]))
                if e.get("ch") == "write":
                    written.add(rp)
                # 10-08(mypy 實測:沒有快取時 C 層讀 calc.py 的內容,Python 只記到一筆 probe):只看在不在的事件不算「記過內容」
                #   —— 以前任何事件提到這條路徑就算記過 ⇒ 安全網放行 ⇒ 改了 calc.py 照樣沿用(紅藏成綠的方向)。
                if e.get("ch") not in ("probe", "missing", "listdir"):
                    seen.add(rp)
    mine = os.path.realpath(str(HERE))
    out = set()
    with fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 3 or parts[0] != "o":
                continue
            try:
                pid = int(parts[1])
            except ValueError:
                continue
            if pid not in py and pid not in node:
                continue
            rp = os.path.realpath(parts[2])
            if not rp.startswith(root + os.sep) or rp in seen or rp in written or rp.startswith(mine + os.sep):
                continue
            rel = os.path.relpath(rp, root)
            segs = rel.split(os.sep)
            if ".git" in segs or "__pycache__" in segs or is_derived(rp) or not os.path.isfile(rp):
                continue
            if pid in node and "node_modules" in segs:
                continue
            out.add(rel.replace(os.sep, "/"))
    return sorted(out)


def _test_path(repo: Path, test_rel: str) -> Path:
    p = Path(test_rel)
    if p.is_absolute():
        return p
    return (repo / test_rel).resolve()


def run_one(repo: Path, test_rel: str, environ=None, timeout=120):
    trace_dir = Path(os.environ.get("EZ2_VERIFY_TRACE_DIR") or "/tmp")
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace = trace_dir / ("trace-%s.jsonl" % hashlib.sha256(
        test_rel.encode()).hexdigest()[:12])
    if trace.exists():
        trace.unlink()
    env = child_env(repo, trace, environ)
    cmd = [sys.executable, str(_test_path(repo, test_rel))]
    try:
        proc = _run(cmd, repo, env, timeout=timeout)
        rc = proc.returncode
        out = proc.stdout or ""
        err = proc.stderr or ""
    except subprocess.TimeoutExpired:
        rc = 124
        out, err = "", "timeout"
    events = load_trace(trace)
    items, input_fp, unmeasured, external, why = inputs_from_events(events, repo=repo)
    env_fp, _env_raw = env_fingerprint(env, repo=repo)
    key = make_key(test_rel, input_fp, env_fp)
    tail = (out[-200:] + err[-200:])
    tail = " ".join(tail.split())
    rel_l = test_rel.lower()
    if "tmux" in rel_l or "vault" in rel_l or os.environ.get("EZ2_VERIFY_EXTERNAL") == "1":
        external = True
    row = {
        "test": test_rel,
        "rc": rc,
        "tail": tail[:240],
        "key": key,
        "inputs": items,
        "input_fp": input_fp,
        "env_fp": env_fp,
        "measured": True if not unmeasured else "unmeasured",
        "external": bool(external),
        "why": why,
        "reused": False,
    }
    return row


def write_outputs(shadow_dir: Path, sha: str, rows, round_n=None):
    shadow_dir.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        jsonl_safe.dumps(r, sort_keys=True) + "\n" for r in rows)
    if round_n is not None:
        round_path = shadow_dir / ("%s.round%s.rows.jsonl" % (sha, round_n))
        round_path.write_text(payload, encoding="utf-8")
    rows_path = shadow_dir / ("%s.rows.jsonl" % sha)
    rows_path.write_text(payload, encoding="utf-8")
    summary = plan.summarize(rows)
    summary["tree_sha"] = sha
    sum_path = shadow_dir / ("%s.summary.json" % sha)
    sum_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    return rows_path, sum_path, summary


def shadow_dir_default():
    return Path(os.environ.get("EZ2_VERIFY_SHADOW_DIR")
                or (Path.home() / ".ez2-aib" / "verify-shadow"))


def reuse_row(repo, test_rel, previous, environ=None):
    """V2: skip a real run only when the previous row is measured and the key still matches."""
    if not previous or previous.get("measured") is not True:
        return None
    if previous.get("external"):
        return None
    # ★ ef56b F 反例:紅列與缺 rc 的列以前也能沿用(呼叫端還把沿用列一律算 PASS)。只有「完整、確實跑過、rc 是 0」
    #   的列才有資格被抄;紅的、缺欄的、逾時的一律回實跑(紅正是最該重跑的)。
    rc = previous.get("rc")
    if not isinstance(rc, int) or isinstance(rc, bool) or rc != 0:
        return None
    if previous.get("timed_out") or not previous.get("key") or "input_fp" not in previous:
        return None
    env = child_env(repo, Path("/tmp/.ez2-verify-skip-probe"), environ)
    env_fp, _ = env_fingerprint(env, repo=repo)
    # ★ 對現在的樹重算輸入指紋;任何一個輸入變了(檔內容、目錄名單、存在與否)就不准跳。
    _items_now, input_fp_now = recompute_input_fp(repo, previous.get("inputs") or [])
    if input_fp_now != (previous.get("input_fp") or ""):
        return None
    key = make_key(test_rel, input_fp_now, env_fp)
    if key != previous.get("key"):
        return None
    row = dict(previous)
    row["test"] = test_rel
    row["reused"] = True
    row["verdict"] = "would-skip"
    row["vs_run"] = "match"
    row["measured"] = True
    return row


def main(argv=None):
    ap = argparse.ArgumentParser(prog="shadow_tracer")
    ap.add_argument("--repo", default=str(REPO_DEFAULT))
    ap.add_argument("--run", nargs="+", default=[])
    ap.add_argument("--previous", default="")
    ap.add_argument("--round", type=int, default=None)
    ap.add_argument("--apply-skip", action="store_true",
                    help="V2: skip would-skip rows; never skip unmeasured/external")
    ap.add_argument("--env-parts", action="store_true",
                    help="print the env fingerprint ingredients as JSON (values hashed) and exit")
    ap.add_argument("--as-aib-test", action="store_true",
                    help="with --env-parts: the environment aib test really gives a test (clean env + tracer hooks), "
                         "not this shell's")
    ap.add_argument("--env-diff", nargs=2, metavar=("A_JSON", "B_JSON"),
                    help="compare two --env-parts outputs; rc 1 when they differ")
    args = ap.parse_args(argv)
    if args.env_parts:
        environ = None
        if args.as_aib_test:
            # 10-03:兩台要比的是「aib test 實際給測試的環境」,不是各自殼裡的環境(殼裡差兩百多項,大多與測試無關)
            import shutil
            import tempfile
            import runner
            home = tempfile.mkdtemp(prefix="aib-envparts-home-", dir=runner.TMP_ROOT)
            try:
                environ = child_env(Path(args.repo), Path(home) / "trace", runner.isolated_test_env(home, args.repo))
                print(json.dumps(env_parts(environ, args.repo), ensure_ascii=False, sort_keys=True))
            finally:
                shutil.rmtree(home, ignore_errors=True)
            return 0
        print(json.dumps(env_parts(None, args.repo), ensure_ascii=False, sort_keys=True))
        return 0
    if args.env_diff:
        a, b = (json.loads(Path(p).read_text(encoding="utf-8")) for p in args.env_diff)
        d = diff_env_parts(a, b)
        print(json.dumps({"same": not d, "diff": d}, ensure_ascii=False))
        return 1 if d else 0
    repo = Path(args.repo).resolve()
    sha = tree_sha(repo)
    prev_rows = plan.load_rows(args.previous) if args.previous else []
    prev_map = {r.get("test"): r for r in prev_rows}
    current = []
    for rel in args.run:
        reused = None
        if args.apply_skip:
            reused = reuse_row(repo, rel, prev_map.get(rel))
        if reused is not None:
            current.append(reused)
        else:
            current.append(run_one(repo, rel))
    planned = plan.apply_plan(current, prev_rows)
    for row in planned:
        if row.get("reused"):
            row["verdict"] = "would-skip"
            row["vs_run"] = "match"
            row["reused"] = True
    out_dir = shadow_dir_default()
    rows_path, sum_path, summary = write_outputs(
        out_dir, sha, planned, args.round)
    print(json.dumps({
        "tree_sha": sha,
        "rows": str(rows_path),
        "summary": str(sum_path),
        "counts": summary,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
