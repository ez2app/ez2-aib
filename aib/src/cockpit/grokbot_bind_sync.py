#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""grokbot webhook 綁定跨機同步(10-28 Andy 准,必要功能):同一個 Grok Bot agent 的門鈴設定(網址+金鑰)跟著 agent 走,
在這台 connect 好了,其他機器開這個 agent 的席也要叫得醒。

規則(Andy 選「要同步,但每次同步前都先問我」):
  - connect 成功後一定會問「要不要同步到其他機器」,列出目標;按「同步」才傳。不無聲自動傳。
  - 沒有交談終端(--stdin / stdin 不是 tty)⇒ 不傳,只印要跑的指令。
  - 目標清單:專案既有的跨主機清單 ~/.ez2-aib/user/dm-hosts.json({"IP": "SSH 主機名"},aib dm 跨主機同一份;
    AIB_DM_XHOST_HOSTS 可換),去掉本機自己的 IP;--to 指定則只用指定的。不寫死任何主機或 IP。
  - 傳送:只走使用者現有的 SSH(BatchMode,不轉發 agent / port)。網址與金鑰只走 SSH 的 stdin,
    不進命令列參數、不寫本機暫存檔、不進 log、任何輸出都不印。目標端目錄 0700、檔案 0600,先寫暫檔再原子 rename。
  - 目標已有同一 agent:內容相同 ⇒ 跳過;不同 ⇒ 列出(不印值)再另問要不要覆蓋。連不上 ⇒ 跳過,提示上線後補跑。
  - 絕不經聊天、GitHub、AIB 私訊或 relay。

    aib grokbot sync-bind --agent <agentId> [--to host1,host2] [--yes] [--overwrite]
      --yes:不問就傳(尺 / 自動化用;預設會問)。--overwrite:內容不同也覆蓋(不給 ⇒ 不同的要另問,--yes 時不覆蓋)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import grokbot_threads as gt  # noqa: E402

ENV_SSH = "AIB_GROKBOT_SSH"            # 尺用:換成假的 SSH 程式
ENV_HOSTS = "AIB_DM_XHOST_HOSTS"       # 與 dm_xhost.ssh_target 同一個覆寫
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$")   # 同 update_status.valid_host
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ForwardAgent=no",
            "-o", "ClearAllForwardings=yes", "-T"]
SSH_TIMEOUT_S = 40
RC_OK, RC_USAGE, RC_PARTIAL = 0, 2, 4

# 目標端跑的小程式(不含任何機密;機密只在 stdin 的 JSON 裡)。probe:回 missing / same / diff;write:原子寫 0600。
REMOTE_PY = r'''
import hashlib, json, os, re, sys
d = json.loads(sys.stdin.read())
a = d.get("agent") or ""
if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", a):
    print("bad"); sys.exit(3)
root = os.environ.get("EZ2_AIB_STATE_ROOT") or os.path.join(os.path.expanduser("~"), ".ez2-aib")
dd = os.path.join(root, "secrets", "grokbot-agents")
f = os.path.join(dd, a + ".env")
if sys.argv[1] == "probe":
    try:
        cur = open(f, "rb").read()
    except FileNotFoundError:
        print("missing"); sys.exit(0)
    print("same" if hashlib.sha256(cur).hexdigest() == d.get("sha") else "diff"); sys.exit(0)
os.makedirs(dd, mode=0o700, exist_ok=True)
os.chmod(dd, 0o700)
tmp = "%s.tmp.%d" % (f, os.getpid())
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(d["content"]); fh.flush(); os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, f)
except BaseException:
    try: os.unlink(tmp)
    except OSError: pass
    raise
print("ok")
'''


def T(key: str, fallback: str, **params) -> str:
    return gt._t("aib.grokbind_sync." + key, fallback, **params)


def hosts_file(env=None) -> Path:
    env = os.environ if env is None else env
    if env.get(ENV_HOSTS):
        return Path(env[ENV_HOSTS]).expanduser()
    root = env.get("EZ2_AIB_STATE_ROOT") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".ez2-aib")
    return Path(root) / "user" / "dm-hosts.json"


def _local_ips(env) -> set:
    try:
        import dm_address
        return set(dm_address.local_ips(env))
    except Exception:  # noqa: BLE001 —— 量不到本機 IP 就不排除(問的時候人看得到清單)
        return set()


def targets(env=None, to=None) -> list[str]:
    """要同步到哪幾台(ssh 主機名)。--to 給了就只用它;否則讀 dm-hosts.json 的值,去掉本機自己的 IP 那幾列。"""
    env = os.environ if env is None else env
    if to:
        out = [h.strip() for h in to if h and h.strip()]
    else:
        try:
            m = json.loads(hosts_file(env).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            m = {}
        mine = _local_ips(env) if isinstance(m, dict) and m else set()
        out = [v.strip() for k, v in (m.items() if isinstance(m, dict) else [])
               if isinstance(v, str) and v.strip() and k not in mine]
    return list(dict.fromkeys(out))


def _ssh(host: str, mode: str, payload: dict, env=None):
    """回 (rc, 第一行輸出)。rc 255 / 例外 = 連不上。機密只在 stdin。"""
    exe = (env or os.environ).get(ENV_SSH) or "ssh"
    remote = "python3 -c %s %s" % (shlex.quote(REMOTE_PY), mode)
    try:
        r = subprocess.run([exe] + SSH_OPTS + [host, remote], input=json.dumps(payload), capture_output=True,
                           text=True, timeout=SSH_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return 255, ""
    lines = (r.stdout or "").strip().splitlines()
    return r.returncode, (lines[-1].strip() if lines else "")


def _content(agent: str, base=None) -> str:
    p = gt.agent_wake_path(agent, base)
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def confirm(question: str, yes: str, no: str, env=None) -> bool:
    """問一次。macOS 先跳原生視窗(按「同步」/「覆蓋」才算);跳不出來 ⇒ 在終端機要人打那個字。其他一律算不要。"""
    env = os.environ if env is None else env
    exe = env.get(gt.ENV_OSASCRIPT) or "osascript"
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
    script = ('display dialog "%s" with title "AIB" buttons {"%s", "%s"} default button "%s" cancel button "%s" '
              'giving up after %d' % (esc(question), esc(no), esc(yes), esc(no), esc(no), gt.DIALOG_TIMEOUT_S))
    try:
        r = subprocess.run([exe, "-e", script], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=gt.DIALOG_TIMEOUT_S + 30)
        if r.returncode == 0 or "-128" in (r.stderr or ""):
            out = r.stdout or ""
            return ("button returned:" + yes) in out and "gave up:true" not in out
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        ans = input("%s\n%s" % (question, T("ask_tty", "輸入「{yes}」才執行,直接按 Enter 不做:", yes=yes)))
    except (EOFError, OSError):
        return False
    return ans.strip() == yes


def sync(agent: str, hosts: list[str], *, ask=True, overwrite=False, interactive=True, env=None, base=None,
         out=sys.stderr, ask_fn=None) -> int:
    """同步一個 agent 的綁定到 hosts。回 rc:0 全部完成或跳過、2 沒傳(用法 / 沒問到 / 沒同意)、4 有幾台沒成。"""
    env = os.environ if env is None else env
    ask_fn = ask_fn or (lambda q, y, n: confirm(q, y, n, env))
    say = lambda s: print("aib: " + s, file=out)  # noqa: E731
    if not gt.AGENT_ID_RE.match(agent or ""):
        say(T("bad_agent", "agent id 形狀不對"))
        return RC_USAGE
    content = _content(agent, base)
    url, key = gt.read_agent_wake(agent, base)
    if not content or gt.validate_wake(url, key):
        say(T("not_bound", "這台沒有 agent {agent} 能用的綁定,沒東西可同步;先跑 aib bot connect grokbot --agent {agent}",
              agent=agent))
        return RC_USAGE
    bad = [h for h in hosts if not HOST_RE.match(h)]
    if bad:
        say(T("bad_host", "主機名不合法:{host}", host=", ".join(bad)))
        return RC_USAGE
    hint = "aib grokbot sync-bind --agent %s" % agent
    if not hosts:
        say(T("no_hosts", "沒有其他機器的清單({file}),沒同步。要同步:在那個檔加一列 \"IP\": \"ssh 主機名\","
              "或跑 {cmd} --to <主機>", file=str(hosts_file(env)), cmd=hint))
        return RC_USAGE
    hint_to = "%s --to %s" % (hint, ",".join(hosts))
    say(T("targets", "agent {agent} 的門鈴設定(網址+金鑰)可以同步到這幾台:{hosts}", agent=agent, hosts=", ".join(hosts)))
    if ask:
        if not interactive:
            say(T("no_tty", "這裡沒有交談終端,不能先問你,所以沒同步。要同步請在終端機跑:{cmd}", cmd=hint_to))
            return RC_USAGE
        q = T("ask", "要把 agent {agent} 的門鈴設定(網址+金鑰)同步到:{hosts} 嗎?\n\n只走你自己的 SSH,"
              "不經聊天、GitHub、私訊或中繼站。按「同步」才傳。", agent=agent, hosts=", ".join(hosts))
        if not ask_fn(q, T("yes", "同步"), T("no", "不同步")):
            say(T("declined", "沒同步。之後要同步:{cmd}", cmd=hint_to))
            return RC_USAGE
    payload_sha = {"agent": agent, "sha": hashlib.sha256(content.encode("utf-8")).hexdigest()}
    failed = 0
    for h in hosts:
        rc, state = _ssh(h, "probe", payload_sha, env)
        if rc == 255 or state not in ("missing", "same", "diff"):
            if rc == 255:
                say(T("offline", "{host}:連不上(離線或 SSH 不通),跳過。上線後跑:{cmd}",
                      host=h, cmd="%s --to %s" % (hint, h)))
            else:
                say(T("fail", "{host}:對方回應看不懂(rc={rc}),跳過。之後跑:{cmd}", host=h, rc=rc,
                      cmd="%s --to %s" % (hint, h)))
            failed += 1
            continue
        if state == "same":
            say(T("same", "{host}:已經是一樣的設定,跳過", host=h))
            continue
        if state == "diff":
            say(T("diff", "{host}:已經有 agent {agent} 的設定,但內容不同(不印值)", host=h, agent=agent))
            ok = overwrite or (ask and interactive and ask_fn(
                T("ask_overwrite", "{host} 已經有 agent {agent} 的另一組門鈴設定(內容不同)。要用這台的覆蓋嗎?",
                  host=h, agent=agent), T("overwrite_yes", "覆蓋"), T("overwrite_no", "不覆蓋")))
            if not ok:
                say(T("kept", "{host}:沒覆蓋,保留原本的", host=h))
                continue
        rc, state = _ssh(h, "write", dict(payload_sha, content=content), env)
        if rc == 0 and state == "ok":
            say(T("done", "{host}:已同步(檔案 0600、資料夾 0700)", host=h))
        elif rc == 255:
            say(T("offline", "{host}:連不上(離線或 SSH 不通),跳過。上線後跑:{cmd}", host=h,
                  cmd="%s --to %s" % (hint, h)))
            failed += 1
        else:
            say(T("fail", "{host}:對方回應看不懂(rc={rc}),跳過。之後跑:{cmd}", host=h, rc=rc,
                  cmd="%s --to %s" % (hint, h)))
            failed += 1
    return RC_PARTIAL if failed else RC_OK


def offer_after_connect(agent: str, interactive: bool, env=None, base=None, out=sys.stderr, ask_fn=None) -> int:
    """connect 成功後的內建一步:一定會問(或在沒交談終端時只印指令),不無聲傳。"""
    env = os.environ if env is None else env
    hosts = targets(env)
    if not hosts:
        print("aib: " + T("no_hosts_after_connect", "其他機器要用這個 agent:在 {file} 列出其他機器後跑 "
                          "aib grokbot sync-bind --agent {agent}(或加 --to <主機>)",
                          file=str(hosts_file(env)), agent=agent), file=out)
        return RC_OK
    return sync(agent, hosts, ask=True, interactive=interactive, env=env, base=base, out=out, ask_fn=ask_fn)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="aib grokbot sync-bind")
    ap.add_argument("--agent", required=True)
    ap.add_argument("--to", default="", help="host1,host2(不給 ⇒ ~/.ez2-aib/user/dm-hosts.json 的其他機器)")
    ap.add_argument("--yes", action="store_true", help="不問就傳(尺 / 自動化用)")
    ap.add_argument("--overwrite", action="store_true", help="對方內容不同也覆蓋")
    args = ap.parse_args(argv)
    to = [h for h in args.to.split(",") if h.strip()] if args.to else None
    hosts = targets(None, to)
    return sync(args.agent, hosts, ask=not args.yes, overwrite=args.overwrite,
                interactive=sys.stdin.isatty())


if __name__ == "__main__":
    sys.exit(main())
