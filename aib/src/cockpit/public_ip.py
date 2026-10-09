#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""外網 IP(10-08 the maintainer 令:標題 `AIB <外網IP> | <區網IP>:[<艙位碼>]`;a seat)。

取法:先問 DNS(OpenDNS 的 myip,一個 UDP 封包、最快),不行再走 HTTPS(api.ipify.org)。各 2 秒上限。
★ 這是**對外查詢**:等於讓 OpenDNS / ipify 看到這台的外網位址(它們本來就看得到,因為是你連過去)。
  AIB_PUBLIC_IP_LOOKUP=0 整個關掉(不查、標題不印外網段);AIB_PUBLIC_IP=<位址> 直接指定(治具/特殊網路)。
★ 標題每拍起一個 python,不能每拍都上網 ⇒ 結果快取 30 分鐘在狀態目錄;過期時**背景**更新(detached),這一拍照用舊值,
  標題不會因為網路慢而卡住。取不到就不印外網段(不印假 IP)。
    public_ip.py refresh <快取檔>     背景更新用(title-beat 叫)
    public_ip.py now                   直接查一次印出來(除錯)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

TTL_S = 30 * 60
FAIL_TTL_S = 5 * 60          # 查不到也記一筆,5 分鐘內不再試(不要每拍都去敲)
IPV4_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
PRIVATE_PREFIXES = ("10.", "127.", "169.254.", "192.168.", "100.64.") + tuple("172.%d." % n for n in range(16, 32))
QUERIES = (
    ["dig", "+short", "+time=2", "+tries=1", "myip.opendns.com", "@resolver1.opendns.com"],
    ["curl", "-s", "--max-time", "2", "https://api.ipify.org"],
)


def valid_public(ip: str) -> bool:
    return bool(IPV4_RE.match(ip or "")) and all(0 <= int(p) <= 255 for p in ip.split(".")) \
        and not ip.startswith(PRIVATE_PREFIXES)


def lookup(env=None) -> str:
    env = os.environ if env is None else env
    forced = (env.get("AIB_PUBLIC_IP") or "").strip()
    if forced:
        return forced
    if env.get("AIB_PUBLIC_IP_LOOKUP") == "0":
        return ""
    for argv in QUERIES:
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=3, stdin=subprocess.DEVNULL).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        tok = (out.strip().splitlines() or [""])[-1].strip()
        if valid_public(tok):
            return tok
    return ""


def read_cache(path: str, now: float | None = None) -> tuple[str, bool]:
    """(位址, 要不要更新)。位址空 = 沒有或查不到。"""
    now = time.time() if now is None else now
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return "", True
    if not isinstance(d, dict):
        return "", True
    ip = str(d.get("ip") or "")
    age = now - float(d.get("epoch") or 0)
    return (ip if valid_public(ip) or (ip and d.get("forced")) else ""), age >= (TTL_S if ip else FAIL_TTL_S)


def write_cache(path: str, ip: str, forced: bool = False) -> None:
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"ip": ip, "epoch": time.time(), "forced": forced}, fh)
    os.replace(tmp, path)


def refresh(path: str, env=None) -> str:
    env = os.environ if env is None else env
    ip = lookup(env)
    try:
        write_cache(path, ip, forced=bool((env.get("AIB_PUBLIC_IP") or "").strip()))
    except OSError:
        pass
    return ip


def spawn_refresh(path: str) -> None:
    """背景更新(不等它);同一個快取檔 60 秒內只起一次(用 .refreshing 記號)。"""
    mark = path + ".refreshing"
    try:
        if time.time() - os.path.getmtime(mark) < 60:
            return
    except OSError:
        pass
    try:
        open(mark, "w").close()
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "refresh", path], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "refresh":
        refresh(sys.argv[2])
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "now":
        print(lookup() or "-")
        sys.exit(0)
    print("usage: public_ip.py refresh <cache-file> | now", file=sys.stderr)
    sys.exit(2)
