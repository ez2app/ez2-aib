#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib dm 跨主機:寄件機自己推導身分,經 SSH 投遞到收件機(the maintainer 10-14 20:5x「先修這個」)。

災情:A 機的席要寄 B 機的席,只能 SSH 進 B 機在那台跑 aib dm;寄件人是從行程祖先鏈推的,
SSH 行程沒有席 ⇒ 抬頭落 `IP:<收件機自己>/…/未認證@unknown`,看不出是誰寄的。

流程(dm-ctl.sh 兩端各一段,本檔只做「編碼、找主機、收件端驗」):
  寄件機:地址的 IP 不是本機 ⇒ dm-ctl 用 AIB_DM_XHOST_WHOAMI=1 再入自己一次,跑**同一套**寄件人推導
          (席位 pane / 行程鏈 / 背景分身 / codex 自稱 / 未認證),印出身分 token ⇒
          經 SSH 在收件機跑 aib dm --xhost-ident <token> <艙位碼>/<席碼>,正文走 stdin。
  收件機:dm-ctl 看到 --xhost-ident ⇒ 先過 verify(下面三條),過了才把抬頭寄件方換成 token 裡的人,
          位址用 sshd 量到的對方 IP(SSH_CONNECTION 第一欄),名字後面掛「(跨主機·經SSH)」。

收件端三條(任一不成立 ⇒ rc 2,一個字都沒送):
  1. 祖先鏈上有 uid 0 的 sshd / sshd-session(本機一般使用者造不出 root 行程;設環境變數假裝沒用);
  2. SSH_CONNECTION 的對方 IP 不是本機自己(SSH 連 localhost 繞回來的不算跨主機);
  3. token 的寄件方過抬頭白名單,且不是 human:(真人憑證是本機三條件量的,隔一台機器量不到)。
★ 能力邊界(寫清楚免得有人以為比實際強):能 SSH 進收件機的人本來就能在那台跑任何指令,
  token 是寄件機**自己說**的,收件機驗不到寄件機上的推導過程;這裡擋的是「本機行程設個參數就改抬頭」。
  所以權限一格不動:收件端帳本(SENDER_JSON)仍記實際貼稿的那個 SSH 行程,低權格。

CLI(dm-ctl.sh 用):
    dm_xhost.py encode <label> <origin> <origin_kind> <hub>   ⇒ token
    dm_xhost.py ssh-target <ip>                               ⇒ SSH 要連的主機名
    dm_xhost.py remote-cmd <aib 指令> <參數...>               ⇒ 給 SSH 的一行(每個參數單引號包好)
    dm_xhost.py accept <token>                                ⇒ label \\x1f origin \\x1f hub \\x1f 對方IP;rc 2 + stderr 理由
"""
from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import sys
from typing import Callable, Mapping, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

VIA_NOTE = "跨主機·經SSH"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,2048}={0,2}$")
HUB_RE = re.compile(r"^aihub(?:-[0-9A-Za-z-]{1,40})?$")
ORIGIN_RE = re.compile(r"^[0-9A-Za-z_.:-]{0,64}$")
SSHD_COMM_RE = re.compile(r"^(?:/usr/sbin/)?sshd(?:-session)?(?::|$|\s)")


def encode(label: str, origin: str = "", origin_kind: str = "", hub: str = "") -> str:
    raw = json.dumps({"v": 1, "label": label, "origin": origin, "kind": origin_kind, "hub": hub},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode(token: str) -> Optional[dict]:
    if not TOKEN_RE.fullmatch(token or ""):
        return None
    try:
        d = json.loads(base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(d, dict) or d.get("v") != 1:
        return None
    return {k: str(d.get(k) or "") for k in ("label", "origin", "kind", "hub")}


# ── 寄件機:IP ⇒ ssh 主機 ──
def ssh_target(ip: str, env: Optional[Mapping] = None, ssh_config: Optional[str] = None) -> str:
    """順位:~/.ez2-aib/user/dm-hosts.json {"IP": "主機"} > ~/.ssh/config 裡 HostName 等於這個 IP 的 Host 名 > IP 本身。

    要找 Host 名是因為 SSH 用「你打的名字」對 Host 區塊,打 IP 對不到 alias 上的 User / IdentityFile
    (別台帳號不同時,直接 ssh <IP> 會用本機帳號登入而失敗)。"""
    env = os.environ if env is None else env
    home = env.get("HOME") or os.path.expanduser("~")
    hosts_file = env.get("AIB_DM_XHOST_HOSTS") or os.path.join(
        env.get("EZ2_AIB_STATE_ROOT") or os.path.join(home, ".ez2-aib"), "user", "dm-hosts.json")
    try:
        with open(hosts_file, encoding="utf-8") as fh:
            m = json.load(fh)
        if isinstance(m, dict) and isinstance(m.get(ip), str) and m[ip].strip():
            return m[ip].strip()
    except (OSError, ValueError):
        pass
    cfg = ssh_config if ssh_config is not None else os.path.join(home, ".ssh", "config")
    try:
        lines = open(cfg, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return ip
    names: list = []
    for line in lines:
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or parts[0].startswith("#"):
            continue
        key, val = parts[0].lower(), parts[1].strip()
        if key == "host":
            names = [n for n in val.split() if not any(c in n for c in "*?!")]
        elif key == "hostname" and val == ip and names:
            return names[0]
    return ip


def remote_cmd(argv: list) -> str:
    return " ".join(shlex.quote(a) for a in argv)


# ── 收件機:驗 ──
def _ps_row(pid: int) -> Optional[tuple]:
    try:
        out = subprocess.run(["ps", "-o", "ppid=,uid=,comm=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=5, stdin=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    parts = out.split(None, 2)
    if len(parts) < 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), parts[2]
    except ValueError:
        return None


def under_root_sshd(pid: int, ps_row: Callable[[int], Optional[tuple]] = _ps_row, max_depth: int = 40) -> bool:
    """往上走 ppid 鏈,有一層是 uid 0 的 sshd / sshd-session ⇒ True。走到 1 或斷鏈 ⇒ False(不猜)。"""
    seen = set()
    while pid > 1 and pid not in seen and len(seen) < max_depth:
        seen.add(pid)
        row = ps_row(pid)
        if row is None:
            return False
        ppid, uid, comm = row
        if uid == 0 and SSHD_COMM_RE.match(comm.strip()):
            return True
        pid = ppid
    return False


def client_ip(env: Optional[Mapping] = None) -> str:
    env = os.environ if env is None else env
    parts = (env.get("SSH_CONNECTION") or "").split()
    return parts[0] if len(parts) == 4 else ""


def accept(token: str, *, env: Optional[Mapping] = None, pid: Optional[int] = None,
           ps_row: Callable[[int], Optional[tuple]] = _ps_row) -> tuple:
    """回 (rc, 結果)。rc 0 ⇒ {label, origin, hub, ip};rc 2 ⇒ 人話理由(一個字都沒送)。"""
    import dm_address
    import seat_address as sa
    import dm_header
    env = os.environ if env is None else env
    d = decode(token)
    if d is None:
        return 2, "跨主機身分 token 認不出來"
    ip = client_ip(env)
    if not ip:
        return 2, "--xhost-ident 只收經 SSH 進來的投遞(沒有 SSH_CONNECTION)"
    if not under_root_sshd(os.getppid() if pid is None else pid, ps_row):
        return 2, "--xhost-ident 只收經 SSH 進來的投遞(祖先鏈上沒有 root 的 sshd)"
    if ip in dm_address.local_ips(env):
        return 2, "SSH 對方 %s 是本機自己,不算跨主機" % ip
    label = d["label"].strip()
    if not label or "\n" in label or "\x1f" in label:
        return 2, "跨主機寄件方是空的"
    if label.startswith("human:"):
        return 2, "跨主機不收真人標籤(真人三條件只在寄件機本機量得到)"
    try:
        sa.assert_address_whitelist("[%s > @xhost / 台北 01-01 00:00]" % label, "跨主機抬頭")
    except Exception:
        return 2, "跨主機寄件方不合抬頭白名單:%s" % label
    if not ORIGIN_RE.fullmatch(d["origin"]):
        return 2, "跨主機寄件來源名不合格:%s" % d["origin"]
    hub = d["hub"] if HUB_RE.fullmatch(d["hub"]) else ""
    if not dm_header.party_from_label(label, d["origin"]).get("code"):
        return 2, "跨主機寄件方組不出席碼:%s" % label
    return 0, {"label": label, "origin": d["origin"], "hub": hub, "ip": ip}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["encode"] and len(args) == 5:
        print(encode(*args[1:]))
        return 0
    if args[:1] == ["ssh-target"] and len(args) == 2:
        print(ssh_target(args[1]))
        return 0
    if args[:1] == ["remote-cmd"] and len(args) >= 2:
        print(remote_cmd(args[1:]))
        return 0
    if args[:1] == ["accept"] and len(args) == 2:
        rc, got = accept(args[1])
        if rc:
            print(got, file=sys.stderr)
            return rc
        print("\x1f".join((got["label"], got["origin"], got["hub"], got["ip"])))
        return 0
    print(__doc__.split("CLI")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
