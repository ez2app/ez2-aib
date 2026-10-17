#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""真人三條件(第 70 項 P3 第三包;OPT §六、§八 4;接口 IFACE-dm-queue-v2 §1 human_id)。

dm-ctl.sh 從 08-18(EG.5 第一刀)起**永不自產 human 標籤**:推導失敗一律落低權格。這一支是那時候
註解裡說的「第二刀」——**正面憑證**。三件同時量到才回 human,缺任一件就回 not-human 並說缺哪一件:

  1. 不在任何 hub socket 內:呼叫端沒有 TMUX/TMUX_PANE,且它的控制終端不是本機任何一個 tmux socket
     底下任何一個 pane 的 tty(逐 socket 現查;socket 死了跳過;沒有 tmux = 沒有 hub)。
  2. tty 屬登入使用者:呼叫端有控制終端(/dev/tty 打得開),那個裝置的擁有者 = 呼叫端的真實 uid 的名字;
     human_id 就是這個名字(登入帳號),不是 $USER(那是自報值)。
  3. 寄件端無 session:從呼叫端往上走 ppid 鏈,直到 login / 終端程式 / launchd,**中間每一層都必須是殼**
     (bash/zsh/sh/login)。這是白名單不是黑名單:node、claude、codex、gemini、python、tmux、script……
     任何不在白名單的一層都算「有 session」。另外環境裡帶 CLAUDECODE / CLAUDE_CODE_ENTRYPOINT /
     EZ2_AIB_AGENT* 任一個也算有 session(自報通道,誠實的 AI 自己會帶)。

★ 能力邊界(誠實寫在這裡):三件都是**這台機器上此刻的事實**,擋得住「誠實的 AI 被誤認成人」與「AI 從 hub 內
  或從另一個 CLI session 裡送」;擋不住「有人真的坐在 the maintainer 的終端打字」(那本來就是人)與「AI 把指令注進
  一個真人終端的殼裡」(那需要先拿到那個終端)。失效方向:三件任何一件量不到 ⇒ not-human(低權),
  永遠不會因為量不到而升權。

純函式部分(judge)不碰系統;探針(probe_*)各自一支,尺用替身餵 judge、用真探針只量「這裡不是人」那一半。
CLI:`python3 human_attest.py [--pid N]` 印一行 JSON。
"""
import json
import os
import pwd
import re
import subprocess
import sys

SHELLS = frozenset({"bash", "zsh", "sh", "-bash", "-zsh", "-sh", "login", "dash", "fish", "-fish"})
# 走到這些就停:它們是「終端的起點」,不是 session。
TERMINALS = frozenset({"login", "launchd", "Terminal", "iTerm2", "iTerm", "WezTerm", "kitty", "alacritty",
                       "Alacritty", "Hyper", "ghostty", "sshd", "sshd-session", "tmux: server"})
SESSION_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "EZ2_AIB_AGENT", "EZ2_AIB_AGENT_ENGINE")
MAX_DEPTH = 24


def _comm(pid):
    try:
        out = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    except Exception:  # noqa: BLE001
        return ""
    return out.stdout.strip()


def _ppid(pid):
    try:
        out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    except Exception:  # noqa: BLE001
        return None
    t = out.stdout.strip()
    return int(t) if t.isdigit() else None


def _basename(comm):
    comm = (comm or "").strip()
    if comm.startswith("-"):
        return comm  # login shell 形("-zsh")保留給白名單認
    return os.path.basename(comm)


def probe_chain(pid=None, max_depth=MAX_DEPTH, comm=_comm, ppid=_ppid):
    """從 pid 往上走,回 [(pid, comm 基名)],走到 TERMINALS 或 pid 1 或走不動為止(含終點那一層)。"""
    pid = os.getppid() if pid is None else int(pid)
    chain = []
    seen = set()
    for _ in range(max_depth):
        if pid is None or pid <= 0 or pid in seen:
            break
        seen.add(pid)
        name = _basename(comm(pid))
        chain.append((pid, name))
        if name in TERMINALS or pid == 1:
            break
        pid = ppid(pid)
    return chain


def probe_tty():
    """(tty 裝置路徑, 擁有者名, 真實 uid 的名字);打不開 /dev/tty ⇒ (None, None, 名)。"""
    me = None
    try:
        me = pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        me = None
    try:
        fd = os.open("/dev/tty", os.O_RDONLY | os.O_NOCTTY)
    except OSError:
        return None, None, me
    # 10-17:macOS 的 ttyname(/dev/tty 的 fd) 回的是 "/dev/tty" 本身(root 擁有),不是真的 /dev/ttysNNN
    #   ⇒ tty_owner 在每一台 Mac 的每一個終端機都不過(the maintainer 在普通終端機核准被擋)。
    #   真裝置名從標準輸入 / 輸出 / 錯誤拿;三個都不是終端機才退回 /dev/tty 那個 fd 的名字。
    #   能打開 /dev/tty(有控制終端機)仍是前提;擋 AI 席靠的是 in_hub 與 session 兩條,不是這條。
    path = None
    try:
        for std in (0, 1, 2):
            try:
                if os.isatty(std):
                    path = os.ttyname(std)
                    break
            except OSError:
                continue
        if path is None:
            try:
                path = os.ttyname(fd)
            except OSError:
                path = None
    finally:
        os.close(fd)
    owner = None
    if path:
        try:
            owner = pwd.getpwuid(os.stat(path).st_uid).pw_name
        except (OSError, KeyError):
            owner = None
    return path, owner, me


def probe_hub_ttys(tmux_tmpdir=None):
    """本機所有 tmux socket 底下所有 pane 的 tty 集合;(集合, 掃過的 socket 數)。沒有目錄 = 空集合。"""
    base = tmux_tmpdir or os.environ.get("TMUX_TMPDIR") or "/tmp"
    d = os.path.join(base, "tmux-%d" % os.getuid())
    ttys = set()
    n = 0
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return ttys, 0
    for name in names:
        sock = os.path.join(d, name)
        try:
            out = subprocess.run(["tmux", "-S", sock, "list-panes", "-a", "-F", "#{pane_tty}"],
                                 capture_output=True, text=True, timeout=5)
        except Exception:  # noqa: BLE001
            continue
        if out.returncode != 0:
            continue
        n += 1
        for ln in out.stdout.splitlines():
            ln = ln.strip()
            if ln:
                ttys.add(ln)
    return ttys, n


def judge(chain, tty, hub_ttys, env=None):
    """純函式。chain=[(pid, comm)],tty=(path, owner, me),hub_ttys=set。回 dict:
    human(bool)、human_id、missing(缺的條件名清單;空=三件全到)、evidence。"""
    env = os.environ if env is None else env
    path, owner, me = tty
    missing = []
    evidence = {"tty": path, "tty_owner": owner, "uid_name": me,
                "chain": [c for _, c in chain], "env_markers": [k for k in SESSION_ENV if env.get(k)]}
    # 1 不在 hub 內
    in_hub = bool(env.get("TMUX") or env.get("TMUX_PANE")) or (path is not None and path in hub_ttys)
    evidence["in_hub"] = in_hub
    if in_hub:
        missing.append("in_hub")
    # 2 tty 屬登入使用者
    if not path or not owner or not me or owner != me:
        missing.append("tty_owner")
    # 3 無 session:鏈上除了自己那一層(python)與 dm-ctl 那一層之外全是殼,且走到了終點
    names = [c for _, c in chain]
    if not names:
        missing.append("chain_unreadable")
    else:
        body = names[:-1] if names[-1] in TERMINALS or chain[-1][0] == 1 else names
        reached_end = names[-1] in TERMINALS or chain[-1][0] == 1
        foreign = [n for n in body if n not in SHELLS]
        if evidence["env_markers"] or foreign or not reached_end:
            missing.append("session")
            evidence["foreign"] = foreign
            evidence["reached_end"] = reached_end
    human = not missing
    return {"human": human, "human_id": (me if human else None), "missing": missing, "evidence": evidence}


def attest(pid=None, env=None):
    """真探針 + judge。pid 預設 = 呼叫本程式的行程(dm-ctl)的父行程,由 CLI 帶進來。"""
    chain = probe_chain(pid)
    tty = probe_tty()
    hub, _n = probe_hub_ttys()
    out = judge(chain, tty, hub, env=env)
    out["hub_sockets_scanned"] = _n
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    pid = None
    if argv and argv[0] == "--pid" and len(argv) > 1 and argv[1].isdigit():
        pid = int(argv[1])
    out = attest(pid)
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0


IMPERSONATION_RE = re.compile(r"^真人[^:：\n]{0,32}[:：]")
IMPERSONATION_MARK = "AI 自稱真人:"


def rewrite_impersonation(msg):
    """非真人送的正文若以「真人<…>:」開頭 ⇒ 改成「AI 自稱真人:」照送(不擋;the maintainer 09-12 14:3x 裁)。回 (新正文, 是否改過)。"""
    m = IMPERSONATION_RE.match(msg or "")
    if not m:
        return msg, False
    return IMPERSONATION_MARK + msg[m.end():], True




def human_display_name(environ=None):
    """真人顯示名：環境變數 > git user.name > 登入帳號 >「你」。

    給 bridge 回顯與對話框左緣提示符用；不是席位名、不是人格卡。
    """
    import os
    import subprocess
    env = environ if environ is not None else os.environ
    for key in ("EZ2_AIB_HUMAN_NAME", "EZ2_HUMAN_DISPLAY_NAME"):
        val = str(env.get(key) or "").strip()
        if val:
            return val[:24]
    try:
        proc = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True, text=True, timeout=2,
        )
        val = (proc.stdout or "").strip()
        if val:
            return val[:24]
    except Exception:
        pass
    try:
        import pwd
        val = pwd.getpwuid(os.getuid()).pw_name
        if val:
            return val[:24]
    except Exception:
        pass
    return "你"

if __name__ == "__main__":
    sys.exit(main())
