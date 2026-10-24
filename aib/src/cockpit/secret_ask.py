#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib secret ask NAME:彈「遮字輸入框」請本人貼金鑰,加密寫進金鑰櫃(維護者 10-18:「設計一個未來輸入 key 的功能,
彈出來一個加密對話框,可以輸入」)。

    aib secret ask ANTHROPIC_API_KEY
    aib secret ask ANTHROPIC_API_KEY --check                         先跟供應商驗過 key 能用才寫
    aib secret ask ANTHROPIC_API_KEY --push eu01:/srv/app/.secrets/anthropic.env --owner app   另外送一份到伺服器
    aib secret ask ANTHROPIC_API_KEY --push eu01:/srv/app/.secrets/anthropic.env --remote-only  只送伺服器、本機不存
        (伺服器專用的 key 不該佔本機同名那一格:那格是整台 Mac 的 key,claude-apikey 之類會拿去用)

流程:輸入框(字被遮住)→ 格式檢查(錯了最多再問 2 次)→ [--check] → Touch ID / 登入密碼 → 加密寫進
~/.ez2-aib/secrets(enc.v1.,同 put_secret)→ [--push] 經 ssh、走 stdin 寫到遠端(umask 077、600、先寫暫存再換名)。

規矩:
  - 值只在記憶體與管線(osascript 的 stdout、ssh 的 stdin)裡走;不進 argv、不印、不寫 log。輸出只有名字、檔名、
    指紋(sha256 前 8 碼)。
  - 名字要是金鑰櫃認得的(aib_secrets.file_for_name);視窗文字只由 aib 組,不吃呼叫端的整句。
  - Mac:寫入前一定過 Touch ID(chat_human 同一套;AIB_TOUCHID=0 只會關掉 ⇒ 不寫,不退成免驗)。AI 可以叫出輸入框、
    甚至模擬打字,但按不了指紋 ⇒ 換不掉本人的 key。
  - 不是 Mac:沒有視窗也沒有 Touch ID ⇒ 只在真終端機用 getpass 遮字輸入;不是終端機 ⇒ 拒。
  - --push 的遠端是明文 NAME=value(遠端沒有 wrap.key),權限 600;owner 只收帳號名格式,路徑只收絕對路徑安全字元。
"""
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(os.path.dirname(HERE), "bootstrap")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OSASCRIPT = "/usr/bin/osascript"
WAIT_S = 300
TRIES = 3
MAX_LEN = 4096
# 認得格式的才擋;其他名字只擋空值、換行、過長
PREFIX = {"ANTHROPIC_API_KEY": "sk-ant-"}
CHECKS = {"ANTHROPIC_API_KEY": ("https://api.anthropic.com/v1/models",
                                lambda v: {"x-api-key": v, "anthropic-version": "2023-06-01"})}
_HOST = re.compile(r"^[A-Za-z0-9._@-]{1,128}$")
_PATH = re.compile(r"^/[A-Za-z0-9._/-]{1,255}$")
_OWNER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_HOSTNAME = re.compile(r"^[A-Za-z0-9._:%-]{1,253}$")   # resolve() 解出的 hostname(含 IPv6)
# 叫不出視窗的 AppleScript 錯誤碼(10-21 @ee4f8:AI 席的 shell 跑 secret ask 只回 error,本人不知道要自己 ! 跑)。
# -1713 = 不准跟使用者互動、-1743 = 沒授權送 Apple 事件、-10827 = 不在圖形登入工作階段。只認這幾個;其他照舊 error,不猜。
NO_GUI_CODES = ("-1713", "-1743", "-10827")

JXA = r"""
var app = Application.currentApplication();
app.includeStandardAdditions = true;
var out;
try {
  app.activate();
  var r = app.displayDialog(__PROMPT__, {withTitle: __TITLE__, defaultAnswer: "", hiddenAnswer: true,
    buttons: [__CANCEL__, __OK__], defaultButton: __OK__, cancelButton: __CANCEL__, givingUpAfter: __WAIT__});
  out = r.gaveUp ? {ok: false, why: "timeout"} : {ok: true, nonce: __NONCE__, value: r.textReturned};
} catch (e) { out = {ok: false, why: (String(e).indexOf("-128") >= 0 || String(e).indexOf("cancel") >= 0) ? "cancel" : "error",
                     code: (e && e.errorNumber !== undefined) ? String(e.errorNumber) : ""}; }
JSON.stringify(out);
"""


def _t(key, args=None):
    import i18n_lib
    return i18n_lib.t(key, args or {}, locale=i18n_lib.resolve_locale())


def fingerprint(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def check_format(name, value):
    """回 None = 過;否則回原因代碼(不帶值)。"""
    if not isinstance(value, str) or not value:
        return "empty"
    if len(value) > MAX_LEN:
        return "too_long"
    if any(c in value for c in "\r\n\x00"):
        return "newline"
    want = PREFIX.get(name)
    if want and not value.startswith(want):
        return "bad_prefix"
    return None


def build_script(prompt, title, nonce, wait=WAIT_S):
    """字串一律 json.dumps 放進 JXA:引號、換行、反斜線都只是字。"""
    return (JXA.replace("__PROMPT__", json.dumps(prompt)).replace("__TITLE__", json.dumps(title))
               .replace("__OK__", json.dumps(_t("aib.secret.btn_ok"))).replace("__CANCEL__", json.dumps(_t("aib.secret.btn_cancel")))
               .replace("__NONCE__", json.dumps(nonce)).replace("__WAIT__", str(int(wait))))


def dialog(prompt, title, run=subprocess.run, wait=WAIT_S):
    """彈一次遮字輸入框。回 {"ok": bool, "why": str, "value"?: str}。"""
    nonce = secrets.token_hex(16)
    try:
        r = run([OSASCRIPT, "-l", "JavaScript", "-"], input=build_script(prompt, title, nonce, wait),
                capture_output=True, text=True, timeout=wait + 15)
    except subprocess.TimeoutExpired:
        return {"ok": False, "why": "timeout"}
    except OSError:
        return {"ok": False, "why": "osascript_failed"}
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    try:
        out = json.loads(lines[-1]) if lines else None
    except ValueError:
        out = None
    if not isinstance(out, dict):
        err = str(getattr(r, "stderr", "") or "")
        return {"ok": False, "why": "no_gui" if any("(%s)" % c in err for c in NO_GUI_CODES) else "unreadable"}
    if out.get("ok") is not True and out.get("why") == "error" and str(out.get("code") or "") in NO_GUI_CODES:
        return {"ok": False, "why": "no_gui"}
    if out.get("ok") is True and out.get("nonce") == nonce and isinstance(out.get("value"), str):
        return {"ok": True, "why": "ok", "value": out["value"].strip()}
    if out.get("ok") is True:
        return {"ok": False, "why": "nonce_mismatch"}
    return {"ok": False, "why": str(out.get("why") or "failed")}


def collect(name, dest, platform, ask=None, getpass_fn=None, isatty=None):
    """問到一個格式對的值。回 (value, why);value None = 沒拿到。"""
    title = _t("aib.secret.dialog_title", {"name": name})
    base = _t("aib.secret.dialog_prompt", {"name": name, "dest": dest})
    prompt = base
    for _ in range(TRIES):
        if platform == "darwin":
            res = (ask or dialog)(prompt, title)
            if not res.get("ok"):
                return None, res.get("why") or "failed"
            value = res["value"]
        else:
            if not (isatty() if isatty else sys.stdin.isatty()):
                return None, "no_tty"
            import getpass
            try:
                value = (getpass_fn or getpass.getpass)(base + " ").strip()
            except (EOFError, KeyboardInterrupt):
                return None, "cancel"
        bad = check_format(name, value)
        if bad is None:
            return value, None
        prompt = _t("aib.secret.dialog_retry", {"why": _t("aib.secret.why." + bad, {"prefix": PREFIX.get(name, "")})}) + "\n\n" + base
    return None, "bad_format"


def verify(name, value, opener=urllib.request.urlopen):
    """跟供應商驗 key。回 None = 能用;否則原因代碼。沒有對應檢查的名字 ⇒ no_check。"""
    spec = CHECKS.get(name)
    if not spec:
        return "no_check"
    url, headers = spec
    try:
        with opener(urllib.request.Request(url, headers=headers(value)), timeout=20) as r:
            return None if 200 <= r.status < 300 else "http_%d" % r.status
    except urllib.error.HTTPError as e:
        return "rejected" if e.code in (401, 403) else "http_%d" % e.code
    except (urllib.error.URLError, OSError):
        return "unreachable"


def prove_human(name, dest, env, platform, touchid=None, touchid_available=None):
    """Mac ⇒ 一定 Touch ID;非 Mac ⇒ 已經是本人在終端機打(collect 只收 tty)。回 None = 過。"""
    if platform != "darwin":
        return None
    import touchid_approve as TA
    if str(env.get("AIB_TOUCHID", "")).strip() == "0":
        return "touchid_off"
    if not (touchid_available() if touchid_available else TA.available(platform=platform, environ=env)):
        return "touchid_unavailable"
    reason = _t("aib.secret.touchid_reason", {"name": TA.clean(name, 64), "dest": TA.clean(dest, 160)})
    import seats_dir_lib
    root = seats_dir_lib.state_root(env)[0]     # 同單源順位:EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib(@abb89 審:不然鎖檔分家、一次一窗破功)
    with TA.one_at_a_time(os.path.join(root, "locks", "touchid-approve.lock")) as gate:
        res = (touchid or TA.ask)(reason) if gate.held else {"ok": False, "why": "busy"}
    return None if res.get("ok") is True else "touchid_" + str(res.get("why") or "failed").split(":")[0]


def parse_push(spec, owner):
    """'host:/abs/path' ⇒ (host, path);不合格丟 ValueError(訊息不帶值)。"""
    host, sep, path = (spec or "").partition(":")
    if not sep or not _HOST.match(host) or host.startswith("-") or not _PATH.match(path) or "/../" in path + "/" or "/./" in path + "/":
        raise ValueError("bad_push")
    if owner is not None and not _OWNER.match(owner):
        raise ValueError("bad_owner")
    return host, path


def resolve(host, run=subprocess.run):
    """ssh -G 解出別名真正會連的 user / hostname / port(@abb89 審:~/.ssh/config 同帳號的 AI 改得到,別名可被指去別台)。
    回 {"user","hostname","port"};解不出、或有 ProxyCommand / ProxyJump(連線會被轉走、視窗看不出)⇒ 丟 ValueError。"""
    try:
        r = run(["ssh", "-G", "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes", "--", host], capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError):
        raise ValueError("ssh_resolve_failed")
    if r.returncode != 0:
        raise ValueError("ssh_resolve_failed")
    kv = {}
    for ln in (r.stdout or "").splitlines():
        k, _, v = ln.strip().partition(" ")
        kv.setdefault(k.lower(), v.strip())
    if kv.get("proxycommand", "none") != "none" or kv.get("proxyjump", "none") != "none":
        raise ValueError("ssh_proxy_refused")
    hn, port, user = kv.get("hostname", ""), kv.get("port", ""), kv.get("user", "")
    if not _HOSTNAME.match(hn) or not port.isdigit() or not _OWNER.match(user or "-"):
        raise ValueError("ssh_resolve_failed")
    return {"user": user, "hostname": hn, "port": port}


def push(name, value, host, path, owner=None, run=subprocess.run, pin=None):
    """經 ssh stdin 寫 NAME=value 到遠端。pin = resolve() 的結果:用 -o 釘死 HostName / Port / User,
    Touch ID 視窗顯示的那台就是真的連的那台(解析完才改 config 也轉不走)。回 None = 成功;否則原因代碼。"""
    q, tmp = shlex.quote(path), shlex.quote(path + ".tmp-aib")
    cmd = "umask 077; mkdir -p %s && cat > %s && chmod 600 %s" % (shlex.quote(os.path.dirname(path)), tmp, tmp)
    if owner:
        cmd += " && chown %s:%s %s" % (owner, owner, tmp)
    cmd += " && mv -f %s %s" % (tmp, q)
    try:
        fixed = []
        for k, v in (("HostName", (pin or {}).get("hostname")), ("Port", (pin or {}).get("port")), ("User", (pin or {}).get("user"))):
            if v:
                fixed += ["-o", "%s=%s" % (k, v)]
        fixed += ["-o", "ProxyCommand=none", "-o", "ProxyJump=none"]    # 解析完到按指紋之間改 config 加轉接也沒用(@abb89 審)
        r = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes"] + fixed + ["--", host, cmd],
                input="%s=%s\n" % (name, value), capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return "ssh_failed"
    return None if r.returncode == 0 else "ssh_rc_%d" % r.returncode


def ask_cmd(argv, env=None, platform=None, ask=None, touchid=None, touchid_available=None,
            opener=None, ssh_run=None, getpass_fn=None, isatty=None, out=sys.stdout, err=sys.stderr, seal=None):
    import argparse
    import aib_secrets as S
    import secret_collect as SC
    env = dict(os.environ if env is None else env)
    platform = sys.platform if platform is None else platform
    ap = argparse.ArgumentParser(prog="aib secret ask")
    ap.add_argument("name")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--push")
    ap.add_argument("--owner")
    ap.add_argument("--remote-only", action="store_true")
    a = ap.parse_args(argv)
    res = {"ok": False, "name": a.name, "file": None, "fingerprint": None, "checked": False, "pushed": None, "why": None}

    def done(rc):
        out.write(json.dumps(res, ensure_ascii=False) + "\n")
        return rc
    rel = S.file_for_name(a.name)
    if rel is None:
        res["why"] = "unknown_name"
        return done(2)
    res["file"] = rel
    if a.name in S.SE_ONLY_NAMES:
        return _ask_se(a, res, done, env, platform, ask, touchid, touchid_available, getpass_fn, isatty, err, seal)
    if S.is_remote_only(a.name) and not a.remote_only:  # 先擋:不解析主機、不叫輸入框
        res["why"] = "remote_only_name"
        return done(2)
    target = pin = None
    if a.push:
        try:
            target = parse_push(a.push, a.owner)
            pin = resolve(target[0], run=ssh_run or subprocess.run)
        except ValueError as e:
            res["why"] = str(e)
            return done(2)
        where = "%s(%s@%s:%s):%s" % (target[0], pin["user"], pin["hostname"], pin["port"], target[1])
    if a.remote_only and not target:
        res["why"] = "remote_only_needs_push"
        return done(2)
    dest = where if a.remote_only else _t("aib.secret.dest_local") + (" + " + where if target else "")
    value, why = collect(a.name, dest, platform, ask=ask, getpass_fn=getpass_fn, isatty=isatty)
    if value is None:
        res["why"] = why
        if why == "no_gui":
            err.write(_t("aib.secret.no_gui_hint") + "\n")
        return done(2)
    try:
        if a.check:
            bad = verify(a.name, value, opener or urllib.request.urlopen)
            if bad:
                res["why"] = "check_" + bad
                return done(2)
            res["checked"] = True
        why = prove_human(a.name, dest, env, platform, touchid=touchid, touchid_available=touchid_available)
        if why:
            res["why"] = why
            return done(2)
        if not a.remote_only:
            try:
                SC.put_secret(a.name, value, env=env)
            except Exception as e:  # noqa: BLE001  訊息不帶值:只回類別與理由
                res["why"] = "write_failed:%s" % getattr(e, "reason", e.__class__.__name__)
                return done(2)
        res["local"] = not a.remote_only
        res["fingerprint"] = fingerprint(value)
        if target:
            bad = push(a.name, value, target[0], target[1], a.owner, run=ssh_run or subprocess.run, pin=pin)
            if bad:
                res["why"] = "push_" + bad
                return done(4 if not a.remote_only else 2)   # 4 = 本機已寫、遠端沒送到
            res["pushed"] = where
        res["ok"] = True
        return done(0)
    finally:
        value = None  # noqa: F841


def _ask_se(a, res, done, env, platform, ask, touchid, touchid_available, getpass_fn, isatty, err, seal):
    """只准放 Secure Enclave 的名字(CF_TOKEN_MINTER):輸入框 → Touch ID → SE 封存;不寫金鑰櫃檔、不推遠端
    (@abb89 審 #222:母鑰在本機檔 = 同帳號 AI 讀得到;推到伺服器 = 又放回伺服器上)。"""
    import se_vault as SV
    res["file"] = "se:" + a.name
    if a.push or a.remote_only or a.check:
        res["why"] = "se_only_local"
        err.write(_t("aib.secret.se_only_hint", {"name": a.name}) + "\n")
        return done(2)
    if platform != "darwin":
        res["why"] = "not_mac"
        return done(2)
    dest = _t("aib.secret.dest_se")
    value, why = collect(a.name, dest, platform, ask=ask, getpass_fn=getpass_fn, isatty=isatty)
    if value is None:
        res["why"] = why
        if why == "no_gui":
            err.write(_t("aib.secret.no_gui_hint") + "\n")
        return done(2)
    try:
        why = prove_human(a.name, dest, env, platform, touchid=touchid, touchid_available=touchid_available)
        if why:
            res["why"] = why
            return done(2)
        fp, why = (seal or SV.seal)(a.name, value, env=env)
        if why:
            res["why"] = "seal_" + why
            return done(2)
        res.update(ok=True, local=False, fingerprint=fp, sealed=True)
        return done(0)
    finally:
        value = None  # noqa: F841


USAGE = "usage: aib secret ask NAME [--check] [--push HOST:/abs/path [--owner USER] [--remote-only]]\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(USAGE)
        return 0 if argv else 2
    if argv[0] != "ask":
        sys.stderr.write(USAGE)
        return 2
    return ask_cmd(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
