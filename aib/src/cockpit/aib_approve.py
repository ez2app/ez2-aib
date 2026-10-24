#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib approve:WordPress 登入第二步 = 本人在這台 Mac 按 Touch ID 核准(Andy 10-24「機房連動我們 aib 的生物辨識」;
規格 pending/SPEC-aib-approve-wp-2fa-ee4f8-1024.md v1,@abb89 審 ①–⑥)。

  aib approve key init|status|pem      這台建一把 SE 簽章鑰(不跳指紋)/ kid / 給站用的公鑰 PEM(pem 要先過一次指紋)
  aib approve site add <站> [--url U]  登記要輪詢的站;機房讀取 token 從 stdin 讀(不進 argv)
  aib approve site list
  aib approve pending [--json]         從各站拉待核准的登入(Mac 不開埠,只往外拉)
  aib approve yes <nonce> [--json]     重新向站上拉那筆 ⇒ Touch ID(視窗字由核心組)⇒ SE 簽 ⇒ 送回站上
  aib approve no <nonce> [--json]      拒絕(不用指紋)
  aib approve history [--json]         最近 20 筆

站上的介面(WP 端 ez2-aib-approve mu):
  GET  <url>/wp-json/ez2-aib/v1/pending   Authorization: Bearer <機房 token>
       ⇒ {"v":1,"items":[{"payload":"<站上組好的 canonical JSON 字串>","code":"ABCD"}]}
       code = base32(SHA256("ez2-aib-approve-code|" + nonce)) 前 4 字:兩邊各自算,核心只認自己算的(站上給的對不上 ⇒ 整筆不收)
  POST <url>/wp-json/ez2-aib/v1/approve   {"nonce","kid","sig"}   sig = ES256 raw r||s,base64url 不帶 padding
  POST <url>/wp-json/ez2-aib/v1/deny      {"nonce"}(帶機房 token)

防線(同帳號 AI 威脅守則):
  - 簽的是**站上給的 payload 原始位元組**;核心只解析來顯示、檢查,不重組 ⇒ 視窗上看到的 = 簽下去的 = 站上存的。
    解析後的 canonical 跟原字串不同(多空白、重複鍵、鍵亂序)⇒ 拒,免得顯示一份、簽另一份。
  - yes 一律**重新向站上拉**那筆,不信本機快取(AI 改得到);payload 的 site 要等於登記的站、kid 要等於這台的鑰。
  - SE 私鑰 accessControl = biometryCurrentSet,每次簽都要指紋;取消 / 逾時 / 沒 SE ⇒ 拒,不退回別的方式。
  - 視窗字 `短碼 ABCD · 核准 <站> 的 <帳號> 登入 · IP …`:短碼與站名在最前。短碼由簽下去的 nonce 算(不是站上 / 中繼給的字),
    換不了;帳號名只收 WordPress 嚴格字元 [A-Za-z0-9 _.@-](不清理後再簽)。
  - 只連 https、不跟轉址(轉址會把機房 token 帶去別的主機);登記的網址主機要等於站名。
  - key pem:發布前先用這把鑰簽一次再驗。只擋得住「.sekey 被簡單換掉、而且沒人同時叫出指紋視窗」;
    **有跳指紋不代表鑰受保護**(同帳號 AI 可以自己換鑰,再趁那一刻叫真的 helper 跳一個字一樣的視窗)。
  - 本機的限速(30 秒 3 次)、機房 token、sites.json 都是 AI 能改的 ⇒ 只防誤操作 / 外人掃描,不是防線。
  - 殘餘風險(規格 §8 第 3 點,照實):
    · 平常:換掉 aib-sehelper 或家目錄裡的核心 = 能改視窗字,騙一次指紋換一張只對站上真的存在、未用的 nonce 有效、
      120 秒、綁站 / 帳號 / IP 的核准單。
    · **發布那一刻最重**:那時 Mac 若已被同帳號 AI 動過(換鑰 / 換 helper / 假視窗),發出去的就是它的鑰,之後每次
      核准都不用指紋,直到換鑰。第一階段只能靠「在確定乾淨的時候發布、發完在站外記下 kid」;
      規格 §8-4 的「發布綁目的地、走釘死的 SSH 寫入」**還沒做**:現在 key pem 只印 PEM,要本人自己帶到主機
      (root 寫 /etc/ez2-aib/approve-keys/<站>.json)。第二階段:helper 放 root 擁有路徑 + Developer ID 簽、
      在另一台裝置(LIFF)核對 kid。
rc:yes / no 有結果(RESULTS 任一種)⇒ 0 + 一行 JSON {nonce, result};nonce 不在 pending / 參數錯 ⇒ 2(規格 §2b)。
  nonce 是 base64url,可能以「-」開頭:yes / no 會自己把它當 nonce(不必寫 --)。其他指令 0 / 2。
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
B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
FIELDS = ("exp", "iat", "ip", "kid", "nonce", "site", "typ", "user_id", "user_login", "v")
NONCE_RE = re.compile(r"[A-Za-z0-9_-]{43}")          # 32 位元組 base64url;一律 fullmatch
CODE_RE = re.compile(r"[A-Z2-7]{4}")                 # base32 前 4 字
LOGIN_RE = re.compile(r"[A-Za-z0-9 _.@-]{1,60}")     # WordPress sanitize_user(strict)收的字
SITE_RE = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?")
IP_RE = re.compile(r"[0-9A-Fa-f:.]{2,45}")
KID_RE = re.compile(r"p256:[0-9a-f]{16}")
SPKI_P256 = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")


def _loads(raw, **kw):
    """json.loads,壞 / 太深(3.9 是 RecursionError)/ 不是 UTF-8 ⇒ None(站上與本機檔都是外人能寫的)。"""
    try:
        return json.loads(raw, **kw)
    except (ValueError, RecursionError, UnicodeError, TypeError):
        return None


def _env(env):
    return dict(os.environ if env is None else env)


def state_root(env=None):
    import seats_dir_lib
    e = _env(env)
    return Path(e.get("EZ2_AIB_STATE_ROOT") or seats_dir_lib.state_root(e)[0])


def approve_dir(env=None):
    return state_root(env) / "approve"


def code_of(nonce):
    """短碼 = base32(SHA256("ez2-aib-approve-code|" + nonce)) 前 4 字(站上同算法)。由 nonce 算 ⇒ 中繼 / 假站換不了。"""
    h = hashlib.sha256(("ez2-aib-approve-code|" + nonce).encode("ascii")).digest()
    return "".join(B32[b & 31] for b in h[:4])


# ---- 這台的 SE 鑰 ----

def _sekey_path(env):
    import se_vault
    return Path(se_vault.se_dir(_env(env))) / (KEY_NAME + ".sekey")


def local_pub(env=None):
    try:
        doc = _loads(_sekey_path(env).read_bytes())
        pub = base64.b64decode(doc.get("public_key") or "", validate=True) if isinstance(doc, dict) else b""
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
    if not (0 < r < p256.N and 0 < s < p256.N):
        raise ValueError("range")
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def key_publish(env=None, run=subprocess.run, helper=None, platform=None):
    """回 (pem, why)。先用這把鑰簽一次「發布 kid …」(SE 的鑰一定跳指紋),驗過才給 PEM。
    同帳號 AI 把 .sekey 換成沒有指紋限制的鑰 ⇒ 這一步不會跳指紋:說明裡要本人「沒跳就停」。
    換掉 aib-sehelper 本身仍擋不住(殘餘風險,第二階段:root 擁有路徑 + Developer ID 簽)。"""
    import se_vault
    import touchid_approve as TA
    from entitlement import p256
    import i18n_lib
    env = _env(env)
    platform = sys.platform if platform is None else platform
    pub = local_pub(env)
    if not pub:
        return None, "no_key"
    if platform != "darwin" or str(env.get("AIB_TOUCHID", "")).strip() == "0":
        return None, "touchid_unavailable"
    kid = kid_of(pub)
    msg = ("ez2-aib-approve-publish|" + kid).encode("ascii")
    reason = i18n_lib.t("aib.approve.touchid_publish", {"kid": kid}, locale=i18n_lib.resolve_locale())
    args = ["se-sign", "--name", KEY_NAME, "--dir", se_vault.se_dir(env), "--reason", reason]
    with TA.one_at_a_time(state_root(env) / "locks" / "touchid-approve.lock") as gate:
        if not gate.held:
            return None, "busy"
        out, why = se_vault._call(args, env, msg, run, helper, platform)
    if why:
        return None, {"cancel": "touchid_cancel"}.get(why, "touchid_unavailable")
    try:
        ok = p256.verify(pub, msg, base64.b64decode(out.decode("ascii").strip(), validate=True))
    except (ValueError, UnicodeDecodeError):
        ok = False
    return (pem_of(pub), None) if ok else (None, "bad_sig")


# ---- 站 ----

def _sites_path(env):
    return approve_dir(env) / "sites.json"


def load_sites(env=None):
    try:
        doc = _loads(_sites_path(env).read_bytes())
    except OSError:
        return []
    rows = doc.get("sites") if isinstance(doc, dict) else None
    return [r for r in rows if isinstance(r, dict) and _url_ok(r.get("site"), r.get("url"))] \
        if isinstance(rows, list) else []


def _url_ok(site, url):
    """https、主機 = 站名、沒有帳密 / 埠以外的花樣。"""
    import urllib.parse
    if not isinstance(site, str) or not isinstance(url, str) or not SITE_RE.fullmatch(site):
        return False
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port  # 不在 0–65535 ⇒ ValueError
        return u.scheme == "https" and u.hostname == site and not u.username and not u.password \
            and "?" not in url and "#" not in url and u.path == "" and url == url.strip() \
            and (port is None or 0 < port)
    except ValueError:
        return False


def _write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(str(path.parent), 0o700)
    tmp = path.with_name(path.name + ".tmp")
    try:
        os.unlink(str(tmp))  # 舊的 .tmp 可能是別的權限 / 連結:不沿用
    except FileNotFoundError:
        pass
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(str(tmp), str(path))


def site_add(site, url, token, env=None):
    site = str(site or "").lower()
    if not SITE_RE.fullmatch(site) or "." not in site:
        return None, "bad_site"
    url = (url or "https://" + site).rstrip("/")
    if not _url_ok(site, url):
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
    except (OSError, ValueError):
        return ""


def _no_redirect():
    import urllib.request

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None  # 轉址一律不跟:會把機房 token 帶去別的主機

    return NoRedirect


def http_call(method, url, headers, body):
    """回 (status, bytes);連不上 ⇒ (0, b"")。只走 https(登記時就擋了 http)。"""
    import http.client
    import urllib.error
    import urllib.request
    if not url.startswith("https://"):
        return 0, b""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.build_opener(_no_redirect()).open(req, timeout=HTTP_TIMEOUT) as r:
            return r.status, r.read(65536)
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (OSError, ValueError, http.client.HTTPException):
        return 0, b""


# ---- 驗站上給的一筆 ----

def parse_item(item, site, kid, now=None):
    """回 (資料, why)。資料 = {payload(位元組), fields, code};why ∈ bad_item / wrong_site / wrong_kid / expired。
    過期的也回資料(why = expired),讓 yes 記得到 expired。"""
    from entitlement import signing
    if not isinstance(item, dict) or not isinstance(item.get("payload"), str):
        return None, "bad_item"
    try:
        raw = item["payload"].encode("utf-8")
    except UnicodeError:
        return None, "bad_item"
    f = _loads(raw, object_pairs_hook=_no_dup) if len(raw) <= PAYLOAD_MAX else None
    if not isinstance(f, dict) or tuple(sorted(f)) != FIELDS or signing.canonical(f) != raw:
        return None, "bad_item"  # 顯示的跟簽的要是同一份位元組
    ints = all(isinstance(f[k], int) and not isinstance(f[k], bool) for k in ("v", "user_id", "iat", "exp"))
    strs = all(isinstance(f[k], str) for k in ("typ", "site", "user_login", "ip", "nonce", "kid"))
    if not ints or not strs or f["v"] != 1 or f["typ"] != "wp-login" or f["user_id"] <= 0:
        return None, "bad_item"
    if not NONCE_RE.fullmatch(f["nonce"]) or not IP_RE.fullmatch(f["ip"]) or not LOGIN_RE.fullmatch(f["user_login"]):
        return None, "bad_item"
    code = code_of(f["nonce"])
    if item.get("code") != code:  # 站上算錯 / 被中繼改過:整筆不收(核心只用自己算的)
        return None, "bad_item"
    if f["site"] != site:
        return None, "wrong_site"
    if f["kid"] != kid:
        return None, "wrong_kid"
    if not 0 < f["exp"] - f["iat"] <= TTL_MAX:
        return None, "bad_item"
    now = time.time() if now is None else now
    if now < f["iat"] - SKEW:
        return None, "bad_item"  # 站上時鐘比這台快太多
    d = {"payload": raw, "fields": f, "code": code}
    return (d, "expired") if now > f["exp"] else (d, None)  # 站上 now > exp 就拒:這邊不給寬限,免得白按指紋


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
    doc = _loads(body)
    items = doc.get("items") if isinstance(doc, dict) and doc.get("v") == 1 else None
    if not isinstance(items, list):
        return [], 0, "bad_reply"
    good, bad = [], 0
    for it in items[:50]:
        d, why = parse_item(it, row["site"], kid, now)
        if d and not why:
            good.append(d)
        elif d:
            good.append(dict(d, expired=True))
        else:
            bad += 1
    return good, bad, None


def _public(d):
    f = d["fields"]
    return {"nonce": f["nonce"], "site": f["site"], "user_login": f["user_login"], "ip": f["ip"], "code": d["code"],
            "iat": f["iat"], "exp": f["exp"], "kid": f["kid"]}


def pending(env=None, http=None, now=None):
    env = _env(env)
    http = http or http_call
    pub = local_pub(env)
    if not pub:
        return None, "no_key"
    items, errors, bad = [], {}, 0
    for row in load_sites(env):
        good, b, err = fetch(row, env, http, kid_of(pub), now)
        items += [_public(d) for d in good if not d.get("expired")]
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
        lines = _history_path(env).read_bytes().splitlines()
    except OSError:
        return []
    out = []
    for ln in lines[-HISTORY_N:]:
        r = _loads(ln)
        if isinstance(r, dict) and r.get("result") in RESULTS:
            out.append({k: r.get(k) for k in ("nonce", "site", "user_login", "ip", "code", "at", "result")})
    return out[::-1]


def _rate_ok(env, now):
    p = approve_dir(env) / "rate.json"
    try:
        doc = _loads(p.read_bytes())
    except OSError:
        doc = None
    ts = [t for t in doc if isinstance(t, (int, float)) and not isinstance(t, bool) and 0 <= now - t < RATE_S] \
        if isinstance(doc, list) else []  # 未來的(時鐘倒退)不算
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


def yes(nonce, env=None, http=None, run=subprocess.run, helper=None, platform=None, now=None):
    """回 (result, why)。result ∈ RESULTS(rc 0);why 只在參數 / 找不到時(rc 2)。"""
    import se_vault
    import touchid_approve as TA
    from entitlement import p256
    env = _env(env)
    http = http or http_call
    platform = sys.platform if platform is None else platform
    if not isinstance(nonce, str) or not NONCE_RE.fullmatch(nonce):
        return None, "bad_nonce"
    pub = local_pub(env)
    if not pub:
        return None, "no_key"
    now = time.time() if now is None else now
    row, d = _find(nonce, env, http, kid_of(pub), now)  # 每次重新向站上拉:不信本機快取
    if not d:
        return None, "not_pending"
    if d.get("expired"):
        _record(env, d, "expired")
        return "expired", None
    if platform != "darwin" or str(env.get("AIB_TOUCHID", "")).strip() == "0":
        _record(env, d, "touchid_unavailable")
        return "touchid_unavailable", None
    args = ["se-sign", "--name", KEY_NAME, "--dir", se_vault.se_dir(env), "--reason", touchid_reason(d)]
    with TA.one_at_a_time(state_root(env) / "locks" / "touchid-approve.lock") as gate:
        if not gate.held:  # 另一個指紋視窗開著:這次不算、不佔限速
            _record(env, d, "touchid_unavailable")
            return "touchid_unavailable", None
        if not _rate_ok(env, now):
            _record(env, d, "rate_limited")
            return "rate_limited", None
        out, why = se_vault._call(args, env, d["payload"], run, helper, platform)
    if why:
        res = {"cancel": "touchid_cancel"}.get(why, "touchid_unavailable")
        _record(env, d, res)
        return res, None
    try:
        der = base64.b64decode(out.decode("ascii").strip(), validate=True)
        ok = p256.verify(pub, d["payload"], der)
        raw = der_to_raw(der) if ok else b""
    except (ValueError, UnicodeDecodeError, OverflowError):
        ok = False
    if not ok:  # helper 回的不是這把鑰對這份位元組的簽章:不送
        _record(env, d, "touchid_unavailable")
        return "touchid_unavailable", None
    body = json.dumps({"nonce": nonce, "kid": kid_of(pub),
                       "sig": base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")}).encode("utf-8")
    st, reply = http("POST", row["url"] + "/wp-json/ez2-aib/v1/approve", {"Content-Type": "application/json"}, body)
    doc = _loads(reply) if st == 200 else None
    good = isinstance(doc, dict) and doc.get("ok") is True
    res = "approved" if good else ("not_delivered" if st == 0 or st >= 500 else "site_rejected")
    _record(env, d, res)
    return res, None


def no(nonce, env=None, http=None, now=None):
    env = _env(env)
    http = http or http_call
    if not isinstance(nonce, str) or not NONCE_RE.fullmatch(nonce):
        return None, "bad_nonce"
    pub = local_pub(env)
    if not pub:
        return None, "no_key"
    row, d = _find(nonce, env, http, kid_of(pub), time.time() if now is None else now)
    if not d:
        return None, "not_pending"
    if d.get("expired"):
        _record(env, d, "expired")
        return "expired", None
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
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) >= 2 and argv[0] in ("yes", "no") and argv[1].startswith("-") and NONCE_RE.fullmatch(argv[1]):
        argv.insert(1, "--")  # base64url 的 nonce 可能以「-」開頭,不是選項(@74e8b 10-24)
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
            pem, why = key_publish(env)
            if why:
                return emit({"ok": False, "why": why}, 2)
            out.write(pem)
            return 0
        return emit({"kid": kid_of(pub) if pub else None}, 0)
    if o.cmd == "site":
        if o.action == "list":
            return emit(load_sites(env), 0)
        r, why = site_add(o.site, o.url, (stdin or sys.stdin).readline(), env)
        return emit(dict(r, ok=True) if r else {"ok": False, "why": why}, 0 if r else 2)
    if o.cmd == "pending":
        r, why = pending(env)
        if not r:
            return emit({"ok": False, "why": why}, 2)
        if o.json:
            return emit(r["items"], 0)
        for i in r["items"]:
            out.write("%s  %s  %s  %s  %s\n" % (i["code"], i["site"], i["user_login"], i["ip"], i["nonce"]))
        out.write("invalid: %d  errors: %s\n" % (r["invalid"], json.dumps(r["errors"], ensure_ascii=False)))
        return 0
    if o.cmd == "history":
        return emit(history(env), 0)
    fn = yes if o.cmd == "yes" else no
    res, why = fn(o.nonce, env)
    d, rc = ({"nonce": o.nonce, "ok": False, "why": why}, 2) if why else ({"nonce": o.nonce, "result": res}, 0)
    out.write(json.dumps(d, ensure_ascii=False) + "\n")  # §2b:一律一行 JSON
    return rc


if __name__ == "__main__":
    sys.exit(main())
