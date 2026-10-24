#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib approve:WordPress 登入第二步 = 本人在這台 Mac 按 Touch ID 核准(Andy 10-24「機房連動我們 aib 的生物辨識」;
規格 pending/SPEC-aib-approve-wp-2fa-ee4f8-1024.md v1,@abb89 審 ①–⑥)。

  aib approve key init|status|pem      這台建一把 SE 簽章鑰(不跳指紋)/ kid / 給站用的公鑰 PEM
  aib approve site add <站> [--url U]  登記要輪詢的站;機房讀取 token 從 stdin 讀(不進 argv)
  aib approve site list
  aib approve pending [--json]         從各站拉待核准的登入(Mac 不開埠,只往外拉)
  aib approve yes <nonce> [--json]     重新向站上拉那筆 ⇒ Touch ID(視窗字由核心組)⇒ SE 簽 ⇒ 送回站上
  aib approve no <nonce> [--json]      拒絕(不用指紋)
  aib approve history [--json]         最近 20 筆

站上的介面(WP 端 ez2-aib-approve mu):
  GET  <url>/wp-json/ez2-aib/v1/pending   Authorization: Bearer <機房 token>
       ⇒ {"v":1,"items":[{"payload":"<站上組好的 canonical JSON 字串>","code":"ABCD"}]}
  POST <url>/wp-json/ez2-aib/v1/approve   {"nonce","kid","sig"}   sig = ES256 raw r||s,base64url 不帶 padding
  POST <url>/wp-json/ez2-aib/v1/deny      {"nonce"}(帶機房 token)

防線(同帳號 AI 威脅守則):
  - 簽的是**站上給的 payload 原始位元組**;核心只解析來顯示、檢查,不重組 ⇒ 視窗上看到的 = 簽下去的 = 站上存的。
    解析後的 canonical 跟原字串不同(多空白、重複鍵、鍵亂序)⇒ 拒,免得顯示一份、簽另一份。
  - yes 一律**重新向站上拉**那筆,不信本機快取(AI 改得到);payload 的 site 要等於登記的站、kid 要等於這台的鑰。
  - SE 私鑰 accessControl = biometryCurrentSet,每次簽都要指紋;取消 / 逾時 / 沒 SE ⇒ 拒,不退回別的方式。
  - 視窗字 `短碼 ABCD · 核准 <站> 的 <帳號> 登入 · IP …`:短碼與站名在最前;欄位含控制 / 隱形字元 ⇒ 整筆不收(不清理後再簽)。
  - 本機的限速(30 秒 3 次)、機房 token、sites.json 都是 AI 能改的 ⇒ 只防誤操作 / 外人掃描,不是防線。
  - 殘餘風險(規格 §8 第 3 點):換掉 aib-sehelper 或家目錄裡的核心 = 能改視窗字,騙一次指紋換一張只對站上真的存在、
    未用的 nonce 有效、120 秒、綁站 / 帳號 / IP 的核准單。
rc:0 = 成功;2 = 拒 / 參數錯 / 找不到;3 = 送了但站上沒放行。
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

KEY_NAME = "AIB_APPROVE"     # aib-sehelper 的鑰名
TTL_MAX = 120                # exp - iat 上限(秒)
SKEW = 30                    # 這台時鐘可以比站上快 / 慢多少
RATE_N, RATE_S = 3, 30       # 30 秒內最多 3 次 yes(本機計數:只防誤操作)
HISTORY_N = 20
PAYLOAD_MAX = 2048
HTTP_TIMEOUT = 10
RESULTS = ("approved", "site_rejected", "not_delivered", "denied", "expired", "rate_limited",
           "touchid_cancel", "touchid_unavailable")
FIELDS = ("exp", "iat", "ip", "kid", "nonce", "site", "typ", "user_id", "user_login", "v")
NONCE_RE = re.compile(r"[A-Za-z0-9_-]{43}")          # 32 位元組 base64url;一律 fullmatch
CODE_RE = re.compile(r"[A-Z2-7]{4}")                 # base32 前 4 字
SITE_RE = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?")
IP_RE = re.compile(r"[0-9A-Fa-f:.]{2,45}")
KID_RE = re.compile(r"p256:[0-9a-f]{16}")
SPKI_P256 = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")


def _env(env):
    return dict(os.environ if env is None else env)


def state_root(env=None):
    import seats_dir_lib
    e = _env(env)
    return Path(e.get("EZ2_AIB_STATE_ROOT") or seats_dir_lib.state_root(e)[0])


def approve_dir(env=None):
    return state_root(env) / "approve"


def _plain(s, limit):
    """欄位要原本就乾淨:含控制 / 隱形字元、超長 ⇒ None(不清理後再用:清理過的就不是站上存、要簽的那份)。"""
    import touchid_approve as TA
    if not isinstance(s, str) or not s or len(s) > limit:
        return None
    return s if not TA._CTRL.search(s) and not TA._INVISIBLE.search(s) else None


# ---- 這台的 SE 鑰 ----

def _sekey_path(env):
    import se_vault
    return Path(se_vault.se_dir(_env(env))) / (KEY_NAME + ".sekey")


def local_pub(env=None):
    try:
        doc = json.loads(_sekey_path(env).read_text(encoding="utf-8"))
        pub = base64.b64decode(doc.get("public_key") or "", validate=True)
    except (OSError, ValueError, TypeError):
        return None
    return pub if len(pub) == 65 and pub[0] == 4 else None


def kid_of(pub):
    return "p256:" + hashlib.sha256(bytes(pub)).hexdigest()[:16]


def pem_of(pub):
    b = base64.b64encode(SPKI_P256 + bytes(pub)).decode("ascii")
    return "-----BEGIN PUBLIC KEY-----\n" + "\n".join(b[i:i + 64] for i in range(0, len(b), 64)) + "\n-----END PUBLIC KEY-----\n"


def key_init(env=None, run=subprocess.run, helper=None, platform=None):
    import se_vault
    env = _env(env)
    if local_pub(env):
        return None, "exists"
    _, why = se_vault._call(["se-create", "--name", KEY_NAME, "--dir", se_vault.se_dir(env)], env, b"", run, helper, platform)
    if why:
        return None, why
    pub = local_pub(env)
    return ({"kid": kid_of(pub)}, None) if pub else (None, "helper_failed")


def der_to_raw(der):
    from entitlement import p256
    r, s = p256.parse_der(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


# ---- 站 ----

def _sites_path(env):
    return approve_dir(env) / "sites.json"


def load_sites(env=None):
    try:
        doc = json.loads(_sites_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = doc.get("sites") if isinstance(doc, dict) else None
    return [r for r in rows or [] if isinstance(r, dict) and SITE_RE.fullmatch(str(r.get("site") or ""))
            and str(r.get("url") or "").startswith("https://")]


def _write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(str(path.parent), 0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(str(tmp), str(path))


def site_add(site, url, token, env=None):
    site = str(site or "").lower()
    if not SITE_RE.fullmatch(site) or "." not in site:
        return None, "bad_site"
    url = (url or "https://" + site).rstrip("/")
    if not url.startswith("https://"):
        return None, "bad_url"
    token = (token or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", token):
        return None, "bad_token"
    rows = [r for r in load_sites(env) if r["site"] != site] + [{"site": site, "url": url}]
    _write_private(_sites_path(env), json.dumps({"v": 1, "sites": rows}, sort_keys=True) + "\n")
    _write_private(approve_dir(env) / "tokens" / (site + ".token"), token + "\n")
    return {"site": site, "url": url}, None


def _token(site, env):
    try:
        return (approve_dir(env) / "tokens" / (site + ".token")).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def http_call(method, url, headers, body):
    """回 (status, bytes);連不上 ⇒ (0, b"")。只走 https(登記時就擋了 http)。"""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.status, r.read(65536)
    except urllib.error.HTTPError as e:
        return e.code, e.read(65536) if e.fp else b""
    except (OSError, ValueError):
        return 0, b""


# ---- 驗站上給的一筆 ----

def parse_item(item, site, kid, now=None):
    """回 (資料, why)。資料 = {payload(位元組), fields, code};why ∈ bad_item / wrong_site / wrong_kid / expired。"""
    from entitlement import signing
    if not isinstance(item, dict) or not isinstance(item.get("payload"), str):
        return None, "bad_item"
    raw = item["payload"].encode("utf-8")
    if len(raw) > PAYLOAD_MAX:
        return None, "bad_item"
    try:
        f = json.loads(raw, object_pairs_hook=_no_dup)
    except ValueError:
        return None, "bad_item"
    if not isinstance(f, dict) or tuple(sorted(f)) != FIELDS or signing.canonical(f) != raw:
        return None, "bad_item"  # 顯示的跟簽的要是同一份位元組
    ints = all(isinstance(f[k], int) and not isinstance(f[k], bool) for k in ("v", "user_id", "iat", "exp"))
    if not ints or f["v"] != 1 or f["typ"] != "wp-login" or f["user_id"] <= 0:
        return None, "bad_item"
    if not NONCE_RE.fullmatch(str(f["nonce"])) or not IP_RE.fullmatch(str(f["ip"])) \
            or _plain(f["user_login"], 60) is None or not CODE_RE.fullmatch(str(item.get("code") or "")):
        return None, "bad_item"
    if f["site"] != site:
        return None, "wrong_site"
    if f["kid"] != kid:
        return None, "wrong_kid"
    now = time.time() if now is None else now
    if not 0 < f["exp"] - f["iat"] <= TTL_MAX or now > f["exp"] + SKEW or now < f["iat"] - SKEW:
        return None, "expired"
    return {"payload": raw, "fields": f, "code": item["code"]}, None


def _no_dup(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError("dup")
        d[k] = v
    return d


def fetch(row, env, http, kid, now=None):
    """一站的待核准 ⇒ (好的 list, 壞的數, 錯誤 or None)。"""
    tok = _token(row["site"], env)
    if not tok:
        return [], 0, "no_token"
    st, body = http("GET", row["url"] + "/wp-json/ez2-aib/v1/pending",
                    {"Authorization": "Bearer " + tok, "Accept": "application/json"}, None)
    if st != 200:
        return [], 0, "http_%d" % st if st else "unreachable"
    try:
        doc = json.loads(body)
        items = doc["items"] if isinstance(doc, dict) and doc.get("v") == 1 else None
    except (ValueError, KeyError, TypeError):
        items = None
    if not isinstance(items, list):
        return [], 0, "bad_reply"
    good, bad = [], 0
    for it in items[:50]:
        d, why = parse_item(it, row["site"], kid, now)
        if d:
            good.append(d)
        else:
            bad += 1
    return good, bad, None


def _public(d):
    f = d["fields"]
    return {"nonce": f["nonce"], "site": f["site"], "user_login": f["user_login"], "ip": f["ip"], "code": d["code"],
            "iat": f["iat"], "exp": f["exp"], "kid": f["kid"]}


def pending(env=None, http=http_call, now=None):
    env = _env(env)
    pub = local_pub(env)
    if not pub:
        return None, "no_key"
    items, errors, bad = [], {}, 0
    for row in load_sites(env):
        good, b, err = fetch(row, env, http, kid_of(pub), now)
        items += [_public(d) for d in good]
        bad += b
        if err:
            errors[row["site"]] = err
    return {"items": items, "invalid": bad, "errors": errors}, None


def _find(nonce, env, http, kid, now):
    for row in load_sites(env):
        good, _, _ = fetch(row, env, http, kid, now)
        for d in good:
            if d["fields"]["nonce"] == nonce:
                return row, d
    return None, None


# ---- 紀錄 / 限速 ----

def _history_path(env):
    return approve_dir(env) / "history.jsonl"


def _record(env, d, result):
    f = d["fields"] if d else {}
    rec = {"nonce": f.get("nonce", ""), "site": f.get("site", ""), "user_login": f.get("user_login", ""),
           "ip": f.get("ip", ""), "code": d["code"] if d else "", "result": result,
           "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    p = _history_path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


def history(env=None):
    try:
        lines = _history_path(env).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines[-HISTORY_N:]:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("result") in RESULTS:
            out.append({k: r.get(k) for k in ("nonce", "site", "user_login", "ip", "code", "at", "result")})
    return out[::-1]


def _rate_ok(env, now):
    p = approve_dir(env) / "rate.json"
    try:
        ts = [t for t in json.loads(p.read_text(encoding="utf-8")) if isinstance(t, (int, float)) and now - t < RATE_S]
    except (OSError, ValueError, TypeError):
        ts = []
    if len(ts) >= RATE_N:
        return False
    _write_private(p, json.dumps(ts + [now]))
    return True


# ---- yes / no ----

def touchid_reason(d):
    import i18n_lib
    f = d["fields"]
    return i18n_lib.t("aib.approve.touchid_reason", {"code": d["code"], "site": f["site"], "user": f["user_login"],
                                                      "ip": f["ip"]}, locale=i18n_lib.resolve_locale())


def yes(nonce, env=None, http=http_call, run=subprocess.run, helper=None, platform=None, now=None):
    """回 (result, why)。result ∈ RESULTS;why 只在參數 / 找不到時(rc 2)。"""
    import se_vault
    import touchid_approve as TA
    from entitlement import p256
    env = _env(env)
    platform = sys.platform if platform is None else platform
    if not NONCE_RE.fullmatch(str(nonce or "")):
        return None, "bad_nonce"
    pub = local_pub(env)
    if not pub:
        return None, "no_key"
    now = time.time() if now is None else now
    row, d = _find(nonce, env, http, kid_of(pub), now)  # 每次重新向站上拉:不信本機快取
    if not d:
        return None, "not_pending"
    if platform != "darwin" or str(env.get("AIB_TOUCHID", "")).strip() == "0":
        _record(env, d, "touchid_unavailable")
        return "touchid_unavailable", None
    if not _rate_ok(env, now):
        _record(env, d, "rate_limited")
        return "rate_limited", None
    args = ["se-sign", "--name", KEY_NAME, "--dir", se_vault.se_dir(env), "--reason", touchid_reason(d)]
    with TA.one_at_a_time(state_root(env) / "locks" / "touchid-approve.lock") as gate:
        if not gate.held:
            return None, "busy"
        out, why = se_vault._call(args, env, d["payload"], run, helper, platform)
    if why:
        res = {"cancel": "touchid_cancel"}.get(why, "touchid_unavailable")
        _record(env, d, res)
        return res, None
    try:
        der = base64.b64decode(out.decode("ascii").strip(), validate=True)
        ok = p256.verify(pub, d["payload"], der)
        raw = der_to_raw(der)
    except (ValueError, UnicodeDecodeError):
        ok = False
    if not ok:
        _record(env, d, "touchid_unavailable")
        return "touchid_unavailable", None
    body = json.dumps({"nonce": nonce, "kid": kid_of(pub),
                       "sig": base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")}).encode("utf-8")
    st, reply = http("POST", row["url"] + "/wp-json/ez2-aib/v1/approve", {"Content-Type": "application/json"}, body)
    try:
        good = st == 200 and json.loads(reply).get("ok") is True
    except (ValueError, AttributeError):
        good = False
    res = "approved" if good else ("not_delivered" if st == 0 or st >= 500 else "site_rejected")
    _record(env, d, res)
    return res, None


def no(nonce, env=None, http=http_call, now=None):
    env = _env(env)
    if not NONCE_RE.fullmatch(str(nonce or "")):
        return None, "bad_nonce"
    pub = local_pub(env)
    if not pub:
        return None, "no_key"
    row, d = _find(nonce, env, http, kid_of(pub), time.time() if now is None else now)
    if not d:
        return None, "not_pending"
    st, _ = http("POST", row["url"] + "/wp-json/ez2-aib/v1/deny",
                 {"Content-Type": "application/json", "Authorization": "Bearer " + _token(row["site"], env)},
                 json.dumps({"nonce": nonce}).encode("utf-8"))
    res = "denied" if st == 200 else "not_delivered"
    _record(env, d, res)
    return res, None


# ---- CLI ----

def _say(key, params=None):
    import i18n_lib
    return i18n_lib.t(key, params or {}, locale=i18n_lib.resolve_locale())


def main(argv=None, out=None, env=None, stdin=None):
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="aib approve")
    sub = ap.add_subparsers(dest="cmd")
    k = sub.add_parser("key")
    k.add_argument("action", choices=("init", "status", "pem"))
    s = sub.add_parser("site")
    s.add_argument("action", choices=("add", "list"))
    s.add_argument("site", nargs="?")
    s.add_argument("--url")
    p = sub.add_parser("pending")
    y = sub.add_parser("yes")
    y.add_argument("nonce")
    n = sub.add_parser("no")
    n.add_argument("nonce")
    h = sub.add_parser("history")
    for x in (k, s, p, y, n, h):
        x.add_argument("--json", action="store_true")
    try:
        o = ap.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    if not o.cmd:
        out.write(_say("aib.approve.help.usage") + "\n")
        return 2

    def emit(d, rc):
        out.write((json.dumps(d, ensure_ascii=False) if o.json or not isinstance(d, dict) else
                   "\n".join("%s: %s" % (kk, vv) for kk, vv in d.items())) + "\n")
        return rc

    if o.cmd == "key":
        if o.action == "init":
            r, why = key_init(env)
            return emit(dict(r, ok=True) if r else {"ok": False, "why": why}, 0 if r else 2)
        pub = local_pub(env)
        if o.action == "pem":
            if not pub:
                return emit({"ok": False, "why": "no_key"}, 2)
            out.write(pem_of(pub))
            return 0
        return emit({"kid": kid_of(pub) if pub else None}, 0)
    if o.cmd == "site":
        if o.action == "list":
            return emit(load_sites(env), 0)
        r, why = site_add(o.site, o.url, (stdin or sys.stdin).readline(), env)
        return emit(dict(r, ok=True) if r else {"ok": False, "why": why}, 0 if r else 2)
    if o.cmd == "pending":
        r, why = pending(env)
        return emit(r["items"] if r and o.json else (r or {"ok": False, "why": why}), 0 if r else 2)
    if o.cmd == "history":
        return emit(history(env), 0)
    fn = yes if o.cmd == "yes" else no
    res, why = fn(o.nonce, env)
    if why:
        return emit({"nonce": o.nonce, "ok": False, "why": why}, 2)
    return emit({"nonce": o.nonce, "result": res}, 0 if res in ("approved", "denied") else 3)


if __name__ == "__main__":
    sys.exit(main())
