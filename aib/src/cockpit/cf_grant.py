#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib cf grant:每次要動 Cloudflare 都先過 Touch ID,再現開一把短命、限網域、限權限、鎖目的主機 IP 的 token
(維護者 10-21:「他能做成每次調用要解鎖 Touch ID 或 liff 才能調用嗎」⇒ 先做 Touch ID 版,LIFF 第二階段)。

    aib cf grant ez2.app dns-write --push eu01:/etc/ez2-cloud/grant.env              10 分鐘、只 ez2.app 的 DNS 寫
    aib cf grant ez2.app dns-read,cache-purge --ttl 30m --push eu01:/root/cf.env --var CF_API_TOKEN --why "清快取"
    aib cf grant ez2.app dns-write --push eu01:/root/cf.env --dry-run                 只查、印計畫,不跳指紋、不建
    aib cf list                                                                       帳號裡的 token(不印值)
    aib cf revoke <token_id>                                                          Touch ID 後刪掉

流程:驗參數 → ssh -G 解出目的主機(同 secret_ask.resolve)→ 問目的主機對 Cloudflare 的出口 IP → 金鑰櫃讀母鑰
CF_TOKEN_MINTER(只有 API Tokens Write)→ 查 zone / 權限群組 id → Touch ID(視窗文字由 aib 組)→ POST /user/tokens
(expires_on、not_before、request.ip 鎖)→ 經 ssh stdin 寫 VAR=value 到目的主機(同 secret_ask.push,600)→ 記一筆稽核。

規矩:
  - 值只在記憶體與 ssh 的 stdin;不印、不進 argv、不寫 log / 稽核。輸出只有 token id、指紋(sha256 前 8 碼)。
  - 權限只收白名單短名(PERMS);ttl 只收 1–60 分鐘;一定鎖 IP,解不出 IP 就拒(沒有「不鎖」的選項)。
  - 沒有略過 Touch ID 的旗標或環境變數;不是 Mac / 關掉 / 沒過 ⇒ 不建。AIB_TOUCHID=0 只會關掉(失效往低權)。
  - 送不到目的主機 ⇒ 立刻用母鑰把剛建的 token 刪掉,不留一把沒人管的鑰匙在外面。
  - 每次核准(成功或失敗)追加一行到 <狀態根>/audit/cf-grant.jsonl(時間、席碼、zone、權限、ttl、主機、token id、結果)。
"""
import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(os.path.dirname(HERE), "bootstrap")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import secret_ask as SA  # noqa: E402

API = "https://api.cloudflare.com/client/v4"
MINTER = "CF_TOKEN_MINTER"
MINTER_FILE = "cloudflare.env"
# 短名 ⇒ (Cloudflare 權限群組名, 範圍)。只開這幾種;要加新的先過防護席審。
PERMS = {
    "dns-read": ("DNS Read", "zone"),
    "dns-write": ("DNS Write", "zone"),
    "cache-purge": ("Cache Purge", "zone"),
    "zone-read": ("Zone Read", "zone"),
    "waf-write": ("Zone WAF Write", "zone"),
    "ssl-write": ("SSL and Certificates Write", "zone"),
    "routes-write": ("Workers Routes Write", "zone"),
    "workers-write": ("Workers Scripts Write", "account"),
}
TTL_MIN, TTL_MAX, TTL_DEFAULT = 1, 60, 10      # 分鐘
_ZONE = re.compile(r"^(?=.{3,253}$)[a-z0-9-]+(\.[a-z0-9-]+)+$")
_VAR = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_TOKEN_ID = re.compile(r"^[0-9a-f]{32}$")
TRACE = "https://api.cloudflare.com/cdn-cgi/trace"


class CfError(Exception):
    """Cloudflare API 失敗;訊息只帶原因代碼,不帶任何值。"""


def _t(key, args=None):
    import i18n_lib
    return i18n_lib.t(key, args or {}, locale=i18n_lib.resolve_locale())


def fingerprint(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def parse_perms(spec):
    """'dns-write,cache-purge' ⇒ 去重、照白名單順序的短名 tuple;有一個不認得就丟 ValueError。"""
    names = [p.strip() for p in (spec or "").split(",") if p.strip()]
    if not names:
        raise ValueError("no_perm")
    for n in names:
        if n not in PERMS:
            raise ValueError("bad_perm")
    return tuple(n for n in PERMS if n in names)


def parse_ttl(spec):
    """'10m' / '10' ⇒ 分鐘數;超出 1–60 或看不懂丟 ValueError。"""
    m = re.match(r"^(\d{1,3})m?$", str(spec or "").strip())
    if not m or not TTL_MIN <= int(m.group(1)) <= TTL_MAX:
        raise ValueError("bad_ttl")
    return int(m.group(1))


def _as_net(text):
    try:
        net = ipaddress.ip_network(str(text).strip(), strict=False)
    except ValueError:
        return None
    return str(net)


def host_ips(hostname, getaddr=socket.getaddrinfo):
    """resolve() 解出的 hostname ⇒ IP 清單(本身就是 IP 就直接用)。解不出回空清單。"""
    try:
        return [str(ipaddress.ip_address(hostname.strip("[]")))]
    except ValueError:
        pass
    try:
        return sorted({a[4][0] for a in getaddr(hostname, None)})
    except (OSError, UnicodeError):
        return []


def egress_ips(host, pin, run=subprocess.run):
    """問目的主機自己對 Cloudflare 的出口 IP(v4 / v6 各一次;唯讀)。主機走 IPv6 出去時只鎖連線用的 v4 會讓
    token 在那台用不了(10-21 EU01 實測:對 Cloudflare 出口是 IPv6)。問不到回空清單,呼叫端照樣用 pin 的 IP。"""
    cmd = "curl -s -m 8 -4 %s; curl -s -m 8 -6 %s" % (TRACE, TRACE)
    fixed = []
    for k, v in (("HostName", (pin or {}).get("hostname")), ("Port", (pin or {}).get("port")), ("User", (pin or {}).get("user"))):
        if v:
            fixed += ["-o", "%s=%s" % (k, v)]
    fixed += ["-o", "ProxyCommand=none", "-o", "ProxyJump=none"]
    try:
        r = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes"]
                + fixed + ["--", host, cmd], capture_output=True, text=True, timeout=40, stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError):
        return []
    out = []
    for ln in (getattr(r, "stdout", "") or "").splitlines():
        if ln.startswith("ip="):
            try:
                out.append(str(ipaddress.ip_address(ln[3:].strip())))
            except ValueError:
                pass
    return sorted(set(out))


def ip_lock(ips, extra=()):
    """IP ⇒ /32 或 /128;再加 --ip 給的 CIDR。看不懂的 --ip 丟 ValueError(不默默略過)。"""
    nets = set()
    for ip in ips:
        n = _as_net(ip)
        if n:
            nets.add(n)
    for x in extra or ():
        n = _as_net(x)
        if not n:
            raise ValueError("bad_ip")
        nets.add(n)
    return sorted(nets)


def read_minter(env):
    """從金鑰櫃讀母鑰。先看檔裡有沒有那一行(沒有就不走 secret() 的 LINE 補填);回 (value, why)。"""
    import aib_secrets as S
    try:
        rows = S.load_file(MINTER_FILE, env=env)
        if not rows.get(MINTER):
            return None, "no_minter"
        return S.secret(MINTER, env=env), None
    except S.SecretError as e:
        return None, "minter_" + str(e.reason)


def api(opener, token, method, path, body=None):
    """打 Cloudflare API。回 result;失敗丟 CfError(代碼)。token 只放 header。"""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with opener(req, timeout=30) as r:
            payload = json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raise CfError("http_%d" % e.code)
    except (urllib.error.URLError, OSError, ValueError):
        raise CfError("unreachable")
    if not payload.get("success"):
        raise CfError("api_error")
    return payload.get("result")


def prove(reason, env, platform, touchid=None, touchid_available=None):
    """Touch ID。回 None = 過;否則 touchid_* 原因。不是 Mac ⇒ 沒有這條路 ⇒ 不給過(跟 secret ask 不同:這裡沒有終端機替代)。"""
    import touchid_approve as TA
    if platform != "darwin":
        return "touchid_unavailable"
    if str(env.get("AIB_TOUCHID", "")).strip() == "0":
        return "touchid_off"
    if not (touchid_available() if touchid_available else TA.available(platform=platform, environ=env)):
        return "touchid_unavailable"
    import seats_dir_lib
    root = seats_dir_lib.state_root(env)[0]
    with TA.one_at_a_time(os.path.join(root, "locks", "touchid-approve.lock")) as gate:
        res = (touchid or TA.ask)(reason) if gate.held else {"ok": False, "why": "busy"}
    return None if res.get("ok") is True else "touchid_" + str(res.get("why") or "failed").split(":")[0]


def audit(env, row, now=None):
    """追加一行稽核(不含值)。寫不進去不擋主流程,但回 False 讓輸出標出來。"""
    import seats_dir_lib
    path = os.path.join(seats_dir_lib.state_root(env)[0], "audit", "cf-grant.jsonl")
    row = dict(row, ts=time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now if now is not None else time.time())),
               seat=env.get("AIB_SEAT_CODE") or "")
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def _clean(text, limit):
    import touchid_approve as TA
    return TA.clean(text, limit)


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def build_body(zone, zone_id, account_id, perms, group_ids, ips, host, ttl, now):
    """組 POST /user/tokens 的 body:zone 權限只給那個 zone,帳號權限只給那個帳號;not_before / expires_on;IP 鎖。"""
    zone_ids = [{"id": group_ids[PERMS[p][0]]} for p in perms if PERMS[p][1] == "zone"]
    acct_ids = [{"id": group_ids[PERMS[p][0]]} for p in perms if PERMS[p][1] == "account"]
    policies = []
    if zone_ids:
        policies.append({"effect": "allow", "permission_groups": zone_ids,
                         "resources": {"com.cloudflare.api.account.zone.%s" % zone_id: "*"}})
    if acct_ids:
        policies.append({"effect": "allow", "permission_groups": acct_ids,
                         "resources": {"com.cloudflare.api.account.%s" % account_id: "*"}})
    return {"name": "aib-grant %s %s %s %s" % (zone, ",".join(perms), host, time.strftime("%Y%m%d-%H%M", time.localtime(now))),
            "policies": policies, "not_before": _iso(now), "expires_on": _iso(now + ttl * 60),
            "condition": {"request.ip": {"in": list(ips)}}}


def grant_cmd(argv, env=None, platform=None, touchid=None, touchid_available=None, opener=None, ssh_run=None,
              getaddr=None, minter=None, now=None, out=sys.stdout, err=sys.stderr):
    import argparse
    env = dict(os.environ if env is None else env)
    platform = sys.platform if platform is None else platform
    opener = opener or urllib.request.urlopen
    run = ssh_run or subprocess.run
    ap = argparse.ArgumentParser(prog="aib cf grant")
    ap.add_argument("zone")
    ap.add_argument("perms")
    ap.add_argument("--push", required=True)
    ap.add_argument("--var", default="CF_API_TOKEN")
    ap.add_argument("--ttl", default="%dm" % TTL_DEFAULT)
    ap.add_argument("--owner")
    ap.add_argument("--why", default="")
    ap.add_argument("--ip", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    res = {"ok": False, "zone": a.zone, "perms": None, "ttl": None, "expires_on": None, "token_id": None,
           "fingerprint": None, "pushed": None, "ip_lock": None, "why": None}
    row = {"action": "grant", "zone": a.zone, "perms": a.perms, "ttl": a.ttl, "host": a.push.partition(":")[0],
           "why_given": _clean(a.why, 120)}

    def done(rc, log=True):
        if log:
            row.update(result="ok" if res["ok"] else "refused", why=res["why"], token_id=res["token_id"])
            if not audit(env, row, now):
                res["audit"] = "write_failed"
        out.write(json.dumps(res, ensure_ascii=False) + "\n")
        return rc
    # 1. 參數(這一段失敗不算核准,不記稽核)
    try:
        zone = a.zone.strip().lower()
        if not _ZONE.match(zone):
            raise ValueError("bad_zone")
        perms = parse_perms(a.perms)
        ttl = parse_ttl(a.ttl)
        if not _VAR.match(a.var):
            raise ValueError("bad_var")
        host, path = SA.parse_push(a.push, a.owner)
        pin = SA.resolve(host, run=run)
    except ValueError as e:
        res["why"] = str(e)
        return done(2, log=False)
    res.update(zone=zone, perms=list(perms), ttl="%dm" % ttl)
    row.update(zone=zone, perms=list(perms), ttl=ttl, host="%s(%s@%s:%s)" % (host, pin["user"], pin["hostname"], pin["port"]))
    # 2. IP 鎖:連線用的 IP + 那台對 Cloudflare 的出口 IP + --ip;一個都沒有就拒
    base = host_ips(pin["hostname"], getaddr=getaddr or socket.getaddrinfo)
    if not base:
        res["why"] = "no_ip_lock"
        return done(2, log=False)
    try:
        ips = ip_lock(base + egress_ips(host, pin, run=run), a.ip)
    except ValueError as e:
        res["why"] = str(e)
        return done(2, log=False)
    res["ip_lock"] = ips
    # 3. 母鑰 → zone → 權限群組 id
    if minter is not None:
        key, why = minter()
    else:
        key, why = read_minter(env)
    if not key:
        res["why"] = why or "no_minter"
        err.write(_t("aib.cf.no_minter_hint") + "\n")
        return done(2, log=False)
    try:
        try:
            zones = api(opener, key, "GET", "/zones?name=%s" % zone)
        except CfError:
            zones = None
        if not zones:
            res["why"] = "zone_unreadable"
            err.write(_t("aib.cf.zone_unreadable_hint", {"zone": zone}) + "\n")
            return done(2, log=False)
        zone_id, account_id = zones[0]["id"], zones[0]["account"]["id"]
        try:
            groups = {g["name"]: g["id"] for g in api(opener, key, "GET", "/user/tokens/permission_groups") or ()}
        except CfError as e:
            res["why"] = "perm_groups_" + str(e)
            return done(2, log=False)
        missing = [PERMS[p][0] for p in perms if PERMS[p][0] not in groups]
        if missing:
            res["why"] = "perm_group_missing"
            return done(2, log=False)
        t0 = int(now if now is not None else time.time())
        body = build_body(zone, zone_id, account_id, perms, groups, ips, host, ttl, t0)
        res["expires_on"] = body["expires_on"]
        where = "%s(%s@%s:%s):%s" % (host, pin["user"], pin["hostname"], pin["port"], path)
        if a.dry_run:
            res.update(ok=True, dry_run=True, token_name=body["name"], push=where, var=a.var)
            return done(0, log=False)
        # 4. Touch ID(視窗文字只由 aib 組)
        import touchid_approve as TA
        reason = _t("aib.cf.touchid_reason", {"zone": TA.clean(zone, 64), "perms": TA.clean(",".join(perms), 80),
                                             "ttl": "%dm" % ttl, "host": TA.clean(where, 160)})
        why = prove(reason, env, platform, touchid=touchid, touchid_available=touchid_available)
        if why:
            res["why"] = why
            return done(2)
        # 5. 建 → 送 → 送不到就撤
        try:
            made = api(opener, key, "POST", "/user/tokens", body)
        except CfError as e:
            res["why"] = "create_" + str(e)
            return done(2)
        value, res["token_id"] = made.get("value") or "", made.get("id")
        try:
            res["fingerprint"] = fingerprint(value)
            bad = SA.push(a.var, value, host, path, a.owner, run=run, pin=pin)
        finally:
            value = made = None  # noqa: F841
        if bad:
            try:
                api(opener, key, "DELETE", "/user/tokens/%s" % res["token_id"])
                res["why"] = "push_failed_revoked"
                return done(2)
            except CfError:
                res["why"] = "push_failed_revoke_failed"     # 鑰匙還在外面:輸出 token_id,請人去 aib cf revoke
                return done(5)
        res.update(ok=True, pushed=where)
        return done(0)
    finally:
        key = None  # noqa: F841


def list_cmd(argv, env=None, opener=None, minter=None, out=sys.stdout, err=sys.stderr):
    env = dict(os.environ if env is None else env)
    key, why = (minter or (lambda: read_minter(env)))()
    if not key:
        err.write(_t("aib.cf.no_minter_hint") + "\n")
        out.write(json.dumps({"ok": False, "why": why or "no_minter"}) + "\n")
        return 2
    try:
        rows = api(opener or urllib.request.urlopen, key, "GET", "/user/tokens?per_page=100") or []
    except CfError as e:
        out.write(json.dumps({"ok": False, "why": "list_" + str(e)}) + "\n")
        return 2
    finally:
        key = None  # noqa: F841
    items = [{"id": str(t.get("id", ""))[:8], "name": t.get("name"), "status": t.get("status"),
              "expires_on": t.get("expires_on"),
              "perms": sorted({g.get("name") for p in t.get("policies") or () for g in p.get("permission_groups") or ()})}
             for t in rows]
    out.write(json.dumps({"ok": True, "tokens": items}, ensure_ascii=False) + "\n")
    return 0


def revoke_cmd(argv, env=None, platform=None, touchid=None, touchid_available=None, opener=None, minter=None,
               now=None, out=sys.stdout, err=sys.stderr):
    env = dict(os.environ if env is None else env)
    platform = sys.platform if platform is None else platform
    tid = (argv[0] if argv else "").strip().lower()
    res = {"ok": False, "token_id": tid, "why": None}
    if not _TOKEN_ID.match(tid):
        res["why"] = "bad_token_id"
        out.write(json.dumps(res) + "\n")
        return 2
    key, why = (minter or (lambda: read_minter(env)))()
    if not key:
        err.write(_t("aib.cf.no_minter_hint") + "\n")
        res["why"] = why or "no_minter"
        out.write(json.dumps(res) + "\n")
        return 2
    try:
        why = prove(_t("aib.cf.revoke_reason", {"token": tid[:8]}), env, platform, touchid=touchid, touchid_available=touchid_available)
        if not why:
            try:
                api(opener or urllib.request.urlopen, key, "DELETE", "/user/tokens/%s" % tid)
                res["ok"] = True
            except CfError as e:
                why = "revoke_" + str(e)
        res["why"] = why
        audit(env, {"action": "revoke", "token_id": tid, "result": "ok" if res["ok"] else "refused", "why": why}, now)
        out.write(json.dumps(res) + "\n")
        return 0 if res["ok"] else 2
    finally:
        key = None  # noqa: F841


USAGE = ("usage: aib cf grant ZONE PERM[,PERM…] --push HOST:/abs/path [--var NAME] [--ttl 10m] [--owner USER] "
         "[--ip CIDR] [--why TEXT] [--dry-run]\n"
         "       aib cf list\n"
         "       aib cf revoke TOKEN_ID\n"
         "PERM: " + ", ".join(PERMS) + "  (ttl 1m–60m; every grant needs Touch ID; the token is IP-locked to HOST)\n")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(USAGE)
        return 0 if argv else 2
    sub, rest = argv[0], argv[1:]
    if sub == "grant":
        return grant_cmd(rest)
    if sub == "list":
        return list_cmd(rest)
    if sub == "revoke":
        return revoke_cmd(rest)
    sys.stderr.write(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
