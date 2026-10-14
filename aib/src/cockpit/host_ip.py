# SPDX-License-Identifier: Apache-2.0
"""這台機器的位址:dm 抬頭寫哪個 IP(preferred_ipv4)、「是不是跨主機」認哪些 IP(all_ips)—— 同一套挑法。

10-14(6ddf 走查,@abb89 派):Mini 的抬頭寫區網 192.168.1.145,大家卻用 Tailscale 100.65.183.58 連它。
原因:dm_header.local_addr 與 dm_address.local_ips 各找一次 `tailscale` 指令,只看 PATH;ssh 進來的殼 PATH 裡
沒有它 ⇒ 退回區網 IP。這裡把兩支收成一套:
  1. tailscale 在哪:PATH 上的 `tailscale` > macOS App 固定安裝路徑(/Applications/Tailscale.app/…/Tailscale)。
  2. 有 Tailscale IP 一律優先;沒有才用區網(ipconfig en0 / en1、hostname -I);都沒有 ⇒ 127.0.0.1。
AIB_TAILSCALE_APP:只搬 App 路徑的落點(治具用;不開行為旗標)。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Mapping, Optional

TAILSCALE_APP = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
LOOPBACK = "127.0.0.1"
_IPV4 = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
_LAN_PROBES = (["ipconfig", "getifaddr", "en0"], ["ipconfig", "getifaddr", "en1"], ["hostname", "-I"])


def _out(argv) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=2, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def tailscale_bin(env: Optional[Mapping] = None) -> Optional[str]:
    """tailscale 指令的路徑:PATH 上的 > App 固定安裝路徑 > None。"""
    env = os.environ if env is None else env
    found = shutil.which("tailscale", path=env.get("PATH"))
    if found:
        return found
    app = env.get("AIB_TAILSCALE_APP") or TAILSCALE_APP
    return app if os.path.isfile(app) and os.access(app, os.X_OK) else None


def tailscale_ips(env: Optional[Mapping] = None) -> list:
    """`tailscale ip` 的每一行(IPv4 與 IPv6,依它印的順序);找不到 tailscale 或沒有連上 ⇒ []。"""
    exe = tailscale_bin(env)
    return _out([exe, "ip"]).split() if exe else []


def lan_ipv4s() -> list:
    out = []
    for argv in _LAN_PROBES:
        for tok in _out(argv).split():
            if _IPV4.fullmatch(tok) and not tok.startswith("127.") and tok not in out:
                out.append(tok)
    return out


def preferred_ipv4(env: Optional[Mapping] = None) -> str:
    """抬頭寫的那一個:Tailscale IPv4 > 區網 IPv4 > 127.0.0.1。"""
    for tok in tailscale_ips(env):
        if _IPV4.fullmatch(tok) and not tok.startswith("127."):
            return tok
    lan = lan_ipv4s()
    return lan[0] if lan else LOOPBACK


def all_ips(env: Optional[Mapping] = None) -> set:
    """這台自己的全部位址(Tailscale 的 IPv4 / IPv6 + 區網探針印出的每一個,含 hostname -I 的 IPv6),判「是不是跨主機」用。"""
    ips = set(tailscale_ips(env))
    for argv in _LAN_PROBES:
        ips.update(_out(argv).split())
    return ips
