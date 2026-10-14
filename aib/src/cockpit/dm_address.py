#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib dm 的完整收件地址:艙位碼/席碼,跨主機再加 IP(the maintainer 09-28:「aib dm 一律要含 aib 艙位碼,
這樣可以絕對不撞名,然後跨艙、跨主機也只要加上 ip 即可」)。

認得四種寫法(席碼 = 席位檔 session_uuid 尾五碼,與 B11 同一把尺):
    a seat / 1857a                   舊寫法:只有席碼(仍收;AIB_DM_REQUIRE_CABIN=1 時拒)
    3c73/1857a  3c73/a seat          同一台:艙位碼/席碼
    100.64.0.1/3c73/1857a            跨主機:IP/艙位碼/席碼
    IP:100.64.0.1/3c73/a seat    直接從 dm 抬頭(dm_header.py 新式)抄下來的一方

艙位碼 = 艙 id 最後一個 `-` 之後的四碼(aib dm 既定規格,與 dm_header.local_cabin 同式;the maintainer 09-28:
不用全帶)。主艙就是它的名字(main)。
本檔只「拆地址、找艙」,艙名(hub socket)的拼法仍只住在 cabin_resolve.sh(R8)。

CLI(dm-ctl.sh 用,一行 key=value,rc 0 成功 / 2 拒):
    dm_address.py resolve <地址> <狀態根> <主艙名>
        ⇒ kind=bare seat=<碼>
        ⇒ kind=cabin seat=<碼> cabin=<艙位碼> instance=<艙 id>
        rc 2 ⇒ reason=<bad|remote|no-cabin|ambiguous-cabin> detail=<人話>
    dm_address.py lookup <席碼> <狀態根>          ⇒ 每行「socket 活著(1/0)」,活的在前
    dm_address.py format <席碼> [艙位碼] [IP]    ⇒ 印完整地址
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Iterable, Optional

SEAT_RE = r"[0-9a-z]{4,8}"
CABIN_RE = r"[0-9a-z]{1,4}"
INSTANCE_RE = r"[0-9a-z][0-9a-z-]{0,39}"
IP_RE = r"(?:\d{1,3}(?:\.\d{1,3}){3}|\[[0-9a-fA-F:]+\]|[0-9a-fA-F]*:[0-9a-fA-F:]+|localhost)"

_BARE = re.compile(r"^@?(?P<seat>%s)$" % SEAT_RE)
_CABIN = re.compile(r"^(?:(?P<ip>%s)/)?(?P<cabin>%s)/@?(?P<seat>%s)$" % (IP_RE, CABIN_RE, SEAT_RE))
_HEADER = re.compile(r"^IP:(?P<ip>[^/\s]+)/(?P<cabin>%s)/[^\s@/]*@(?P<seat>%s)$" % (CABIN_RE, SEAT_RE))
LOOPBACKS = ("127.0.0.1", "localhost", "::1", "[::1]")


def parse(target: str) -> Optional[dict]:
    """拆地址 ⇒ {seat, cabin, ip};認不出回 None。cabin/ip 沒寫就是空字串。"""
    t = (target or "").strip()
    for rx in (_HEADER, _CABIN):
        m = rx.match(t)
        if m:
            d = m.groupdict()
            return {"seat": d["seat"], "cabin": d.get("cabin") or "", "ip": (d.get("ip") or "").strip("[]")}
    m = _BARE.match(t)
    if m:
        return {"seat": m.group("seat"), "cabin": "", "ip": ""}
    return None


def cabin_code(instance: str) -> str:
    """艙 id ⇒ 艙位碼(尾四碼;與 dm_header.local_cabin 同式)。"""
    inst = (instance or "").strip()
    return inst.rsplit("-", 1)[-1][-4:] if inst else ""


def local_ips(env=None) -> set:
    """這台機器自己的位址(判斷「是不是跨主機」用)。AIB_DM_LOCAL_IPS 治具可整組指定。"""
    env = os.environ if env is None else env
    if env.get("AIB_DM_LOCAL_IPS") is not None:
        return {x for x in env["AIB_DM_LOCAL_IPS"].replace(",", " ").split() if x} | set(LOOPBACKS)
    ips = set(LOOPBACKS)
    if env.get("AIB_DM_ADDR"):
        ips.add(env["AIB_DM_ADDR"])
    # 挑法在 host_ip,與 dm_header.local_addr 同一套(10-14:tailscale 不在 PATH 時也找 App 固定安裝路徑)
    ips.update(_host_ip().all_ips(env))
    return ips


def _host_ip():
    """同目錄的 host_ip(抬頭與跨主機判斷同一套挑 IP 法);從別處 import 本檔時目錄不一定在 sys.path。"""
    try:
        import host_ip
    except ImportError:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import host_ip
    return host_ip


def cabins(state_root: str, primary: str) -> list:
    """本機所有艙 id:主艙 + <狀態根>/instances/ 底下每一艙。"""
    out = [primary] if primary else []
    inst_root = os.path.join(state_root, "instances")
    if os.path.isdir(inst_root):
        for name in sorted(os.listdir(inst_root)):
            if os.path.isdir(os.path.join(inst_root, name)) and re.fullmatch(INSTANCE_RE, name):
                out.append(name)
    return out


def match_cabin(code: str, known: Iterable[str]) -> list:
    """艙位碼(四碼)⇒ 符合的艙 id。"""
    return [c for c in known if code == cabin_code(c)]


def resolve(target: str, state_root: str, primary: str, env=None) -> tuple:
    """回 (rc, dict)。rc 0 = 可投;rc 2 = 拒(一個字都沒送)。"""
    p = parse(target)
    if p is None:
        return 2, {"reason": "bad", "detail": "認不出地址:%s(寫法:艙位碼/席碼,跨主機 IP/艙位碼/席碼)" % target}
    if p["ip"] and p["ip"] not in local_ips(env):
        return 2, {"reason": "remote", "seat": p["seat"], "cabin": p["cabin"], "ip": p["ip"],
                   "detail": "%s 不是這台機器。aib dm 還不會直送別台;先用 aib plaza dm 轉,"
                             "或到那台機器上送。" % p["ip"]}
    if not p["cabin"]:
        return 0, {"kind": "bare", "seat": p["seat"]}
    hits = match_cabin(p["cabin"], cabins(state_root, primary))
    if not hits:
        return 2, {"reason": "no-cabin", "seat": p["seat"], "cabin": p["cabin"],
                   "detail": "這台機器沒有艙位碼 %s 的艙" % p["cabin"]}
    if len(hits) > 1:
        return 2, {"reason": "ambiguous-cabin", "seat": p["seat"], "cabin": p["cabin"],
                   "detail": "艙位碼 %s 在這台機器對到多個艙(%s),不猜;請把其中一艙關掉或改號" % (p["cabin"], " ".join(hits))}
    return 0, {"kind": "cabin", "seat": p["seat"], "cabin": p["cabin"], "instance": hits[0]}


def lookup(seat: str, state_root: str) -> list:
    """席碼 ⇒ 這台機器上登記過它的席位檔 [(socket, 活著?)],活的在前。給「你要寫艙位碼」那句建議用。

    只回 socket;socket ⇒ 艙 id 由 cabin_resolve.sh 的 aib_cabin_instance_from_hub 算(R8:艙名拼法不在這裡)。"""
    import glob
    import json
    code = (seat or "").strip().lstrip("@")
    if not re.fullmatch(SEAT_RE, code):
        return []
    dirs = [os.path.join(state_root, "seats")] + sorted(glob.glob(os.path.join(state_root, "instances", "*", "seats")))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import seat_address as sa
    except Exception:
        return []
    out = {}
    for d in dirs:
        for path in glob.glob(os.path.join(d, "w*.json")):
            try:
                rec = json.load(open(path, encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(rec, dict) or sa.code_of(rec) != code:
                continue
            sock = str(rec.get("socket") or "")
            if not re.fullmatch(r"[0-9a-z-]{1,64}", sock):
                continue
            live = False
            try:
                pid = int(rec.get("pid") or 0)
                if pid > 0:
                    os.kill(pid, 0)
                    live = True
            except (OSError, TypeError, ValueError):
                pass
            out[sock] = out.get(sock, False) or live
    return sorted(out.items(), key=lambda kv: (not kv[1], kv[0]))


def format_address(seat: str, cabin: str = "", ip: str = "") -> str:
    seat = (seat or "").lstrip("@")
    if not re.fullmatch(SEAT_RE, seat):
        raise ValueError("席碼不對:%r" % seat)
    if not cabin:
        return "@" + seat
    return ("%s/%s/%s" % (ip, cabin, seat)) if ip else ("%s/%s" % (cabin, seat))


def _kv(d: dict) -> str:
    return " ".join("%s=%s" % (k, str(v).replace(" ", " ")) for k, v in d.items())


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 4 and argv[0] == "resolve":
        rc, d = resolve(argv[1], argv[2], argv[3])
        print(_kv(d))
        return rc
    if len(argv) == 3 and argv[0] == "lookup":
        for sock, live in lookup(argv[1], argv[2]):
            print("%s %d" % (sock, 1 if live else 0))
        return 0
    if 2 <= len(argv) <= 4 and argv[0] == "format":
        try:
            print(format_address(*argv[1:]))
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        return 0
    print(__doc__.split("CLI")[1] if "CLI" in __doc__ else __doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
