#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib consent:要本人同意的指令一律由 aib 跳彈窗(Andy 10-25:「之後有 ! 要同意的，一率在aib 跳出同意，不用手打，
有生物辨識的 就用生物辨識，若需要加強的，多一個打勾 打勾後，將該事件最重要的 一句話總結 顯示給他看」;
設計 pending/PLAN-approve-popup-abb89-1025.md)。名字避開 aib approve(那是 WordPress 登入第二步)。

  aib consent ask [--strong] --summary <一句話> [--seat 席碼] [--wait 秒] [--json] -- <指令…>
      把申請放進 <狀態根>/consent/inbox/<id>.json,印 id;--wait 等結果(預設不等;等完不是 ok,含逾時仍 pending ⇒ rc 1)。
  aib consent status <id> [--json]       pending / ok / rc_nonzero / denied / not_allowed / unavailable / failed /
                                         timeout / expired / invalid
  aib consent run-once                   處理 inbox 到空(一次一件);launchd 監看 inbox 時叫這一支
  aib consent install | uninstall        裝 / 拿掉 launchd(WatchPaths = inbox + 每 5 分鐘保底一次;不常駐)

席位被 Claude Code 的安全檢查擋住、連 Bash 都不能跑時：用檔案工具寫一份 JSON 到 inbox(先寫 .tmp 再改名最好),
launchd 看到就會叫 run-once ⇒ 照樣跳窗。不用再叫 Andy 在輸入框打 `!`。
  {"argv": ["aib", "now", "--seat", "abb89", "…", "2/5"], "summary": "…", "strong": false, "seat": "abb89"}

彈窗:
  一般 ⇒ 系統原生 Touch ID 視窗(touchid_approve.ask,沒 Touch ID 時系統給登入密碼欄;沒有純同意鈕)。
  strong ⇒ 先一個對話框：「自稱 @x 的席位寫的一句話(可能不準)」+「實際指令」全文 +「我看過了」勾選框;
           勾了按繼續才跳 Touch ID。SIGN 腳本另列 sha256 與前幾行。指令長到 Touch ID 視窗放不下 ⇒ 自動當 strong。

防線(同帳號 AI 威脅守則;照實寫):
  - 這是**同意的介面**,不是擋 AI 的防線:同帳號 AI 改得到這支程式、inbox、done、launchd 設定。真正擋 AI 的仍是各指令
    自己的 human_attest / Touch ID / 簽章。這裡跑出來的行程是 launchd 底下的 python,human_attest 三條件本來就不過;
    這支**不**新增任何放行旗標或環境變數,也不留「已驗過」狀態(每件各自跳窗)。done / status 也是 AI 寫得到的紀錄,
    只給席位看結果，不拿來做任何決定。
  - 顯示不出原樣就不跑:argv 有控制字 / 隱形字 / 方向字 ⇒ not_allowed(不清了再跑)。一句話總結是席位寫的、
    席碼是自稱的 ⇒ 對話框照實標明;Touch ID 視窗字只放實際指令。
  - 白名單(第一版，逐支):aib now / dm-queue / lang / plugins / theme(拒 --allow-unsigned);
    claude plugin list / update / enable / disable / uninstall / install <名@marketplace>(子指令後不收任何旗標);
    bash ~/.ez2-aib/pending/SIGN-*.sh(只准 strong;同意時鎖 sha256,跑 0700 暫存夾裡的複本並再驗)。其他 not_allowed。
    argv 是陣列、不經 shell;一律在 HOME 跑(工作目錄不由申請決定)。
  - 一次一件:拿到 touchid-approve 鎖(跟 newproj --approve / aib approve 共用)後先把申請改名搬到 running/,
    搬不動 = 別支已接手 ⇒ 跳過;同意 / 拒絕一決定就放鎖，再跑指令(跑的時候別的 Touch ID 視窗照開)。
    鎖與搬檔只防兩個視窗疊在一起、同一件被處理兩次(誤操作);AI 刪得掉。
  - 申請超過 30 分鐘沒處理 ⇒ expired(不讓很久以前的指令在開機時跳出來)。
rc:ask 0(已排 / 等到 ok)/ 1(等完不是 ok、含逾時仍 pending)/ 2(參數錯、不在白名單);status 0 / 2(沒這件);run-once 0。
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import touchid_approve as TA  # noqa: E402

AGENT_LABEL = "page.ez2.aib-consent"
RUN_TIMEOUT_S = 20 * 60
EXPIRE_S = 30 * 60
LOCK_RETRY_S = 300
LOCK_RETRY_STEP = 5
FRESH_S = 2
MAX_REQ_BYTES = 16 * 1024
MAX_ARGV = 40
MAX_CMD_LEN = 1500
MAX_SIGN_BYTES = 256 * 1024
SIGN_HEAD_LINES = 12
SUMMARY_MAX = 200
REASON_CMD_MAX = 120
RESULTS = ("ok", "rc_nonzero", "denied", "not_allowed", "unavailable", "failed", "timeout", "expired", "invalid")
AIB_SUBCOMMANDS = frozenset({"now", "dm-queue", "lang", "plugins", "theme"})   # update:開發樹的 aib 一律拒 ⇒ 不列
CLAUDE_PLUGIN_SUB = frozenset({"list", "update", "enable", "disable", "uninstall", "install"})
SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SIGN_RE = re.compile(r"^SIGN-[A-Za-z0-9._-]+\.sh$")
_SEAT_RE = re.compile(r"^[a-z0-9]{3,12}$")
_PLUGIN_REF_RE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$")
# 控制字(C0 / DEL / C1)、行段分隔、零寬 / 方向控制 / 軟連字號 / 填充字 / 變體選擇 / 標籤字:視窗裡看不出原樣 ⇒ 不收
# 視窗裡看不出原樣的字一律不收：控制 / 格式 / 行段分隔 / 私用 / 未指派(依 Unicode 類別，不逐字列),
# 再加上類別是 Mn / Lo 卻看不見的:變體選擇(兩段)、組字用連接字、韓文填充字、點字空白
_INVISIBLE_EXTRA = re.compile("[\u034f\u115f\u1160\u17b4\u17b5\u180b-\u180f\u2800\u3164\ufe00-\ufe0f\uffa0"
                              "\U000e0100-\U000e01ef]")
_BAD_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Co", "Cn", "Cs"})


def _has_bad_char(text):
    return bool(_INVISIBLE_EXTRA.search(text)) or any(
        unicodedata.category(ch) in _BAD_CATEGORIES or (ch != " " and unicodedata.category(ch) == "Zs") for ch in text)

STRONG_JXA = r"""
ObjC.import('AppKit');
var TITLE = __TITLE__, BODY = __BODY__, CHECK = __CHECK__, GO = __GO__, NO = __NO__, NONCE = __NONCE__;
var app = $.NSApplication.sharedApplication;
app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);
app.activateIgnoringOtherApps(true);
var a = $.NSAlert.alloc.init;
a.messageText = TITLE;
a.informativeText = BODY;
a.showsSuppressionButton = true;
a.suppressionButton.title = CHECK;
a.addButtonWithTitle(GO);
a.addButtonWithTitle(NO);
a.layout;
a.window.level = 3;   // NSFloatingWindowLevel(JXA 拿不到這個常數 ⇒ 寫數值);launchd 叫起來時不一定搶得到前景
var r = a.runModal;
var checked = Number(a.suppressionButton.state) === 1;   // JXA 回的是字串 "1"
JSON.stringify((Number(r) === 1000 && checked) ? {ok: true, nonce: NONCE} : {ok: false, go: Number(r) === 1000, checked: checked});
"""


# ---- 路徑 ----

def _env(env):
    return os.environ if env is None else env


def state_root(env=None):
    import seats_dir_lib
    e = _env(env)
    return Path(e.get("EZ2_AIB_STATE_ROOT") or seats_dir_lib.state_root(e)[0])


def consent_dir(env=None):
    return state_root(env) / "consent"


def inbox_dir(env=None):
    return consent_dir(env) / "inbox"


def running_dir(env=None):
    return consent_dir(env) / "running"


def done_dir(env=None):
    return consent_dir(env) / "done"


def bad_dir(env=None):
    return consent_dir(env) / "bad"


def _home(env=None):
    e = _env(env)
    return Path(e.get("HOME") or os.path.expanduser("~"))


def _write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".%s.%s.tmp" % (path.name, secrets.token_hex(4)))
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _read_regular(path, limit):
    """只讀一般檔(具名管道不卡、不跟符號連結),超過 limit 回 None。"""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = os.read(fd, limit + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    return None if len(raw) > limit else raw


def _read_json(path, limit=MAX_REQ_BYTES):
    raw = _read_regular(path, limit)
    if raw is None:
        return None
    try:
        d = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


# ---- 白名單 ----

def classify(argv, strong, env=None):
    """回 (kind, 原因)。kind ∈ aib / claude_plugin / sign_script;不准 ⇒ (None, 原因碼)。"""
    if not isinstance(argv, list) or not argv or len(argv) > MAX_ARGV:
        return None, "argv_shape"
    if not all(isinstance(a, str) and a and not _has_bad_char(a) for a in argv):
        return None, "argv_chars"
    if len(display_cmd(argv)) > MAX_CMD_LEN:
        return None, "argv_too_long"
    head = argv[0]
    if head == "aib":
        if len(argv) < 2 or argv[1] not in AIB_SUBCOMMANDS:
            return None, "aib_subcommand"
        if any(a == "--allow-unsigned" or a.startswith("--allow-unsigned=") for a in argv):
            return None, "aib_allow_unsigned"
        return "aib", ""
    if head == "claude":
        if len(argv) < 3 or argv[1] != "plugin" or argv[2] not in CLAUDE_PLUGIN_SUB:
            return None, "claude_subcommand"
        if any(a.startswith("-") for a in argv[3:]):
            return None, "claude_flag"     # -y / --accept-command / --marketplace 等都能帶進別的指令 ⇒ 一律不收旗標
        if argv[2] == "install" and (len(argv) < 4 or not _PLUGIN_REF_RE.match(argv[3])):
            return None, "claude_install_ref"
        return "claude_plugin", ""
    if head == "bash":
        if len(argv) != 2:
            return None, "sign_shape"
        try:
            real = Path(argv[1]).expanduser().resolve(strict=True)
            base = (_home(env) / ".ez2-aib" / "pending").resolve(strict=True)
        except (OSError, RuntimeError):      # ~不存在的使用者 ⇒ expanduser 丟 RuntimeError
            return None, "sign_missing"
        if real.parent != base or not _SIGN_RE.match(real.name) or not real.is_file():
            return None, "sign_path"
        if not strong:
            return None, "sign_needs_strong"
        return "sign_script", ""
    return None, "not_allowed"


def _clean_summary(text):
    """席位寫的一句話：看不見的字直接拿掉(比 TA.clean 擋得寬),再壓一行截長度。"""
    text = "".join(" " if ch in "\t\r\n" else ch for ch in str(text or ""))
    return TA.clean("".join(ch for ch in text if not _has_bad_char(ch)), SUMMARY_MAX)


def display_cmd(argv):
    return " ".join(shlex.quote(a) for a in argv)


def _seat(v):
    return v if isinstance(v, str) and _SEAT_RE.match(v) else None


# ---- 申請 ----

def new_request(argv, summary, strong=False, seat=None, env=None, now=None):
    kind, why = classify(argv, strong, env)
    if not kind:
        return None, why
    summary = _clean_summary(summary)
    if not summary:
        return None, "summary_missing"
    t = int(now if now is not None else time.time())
    rid = "%d-%s" % (t, secrets.token_hex(4))
    req = {"v": 1, "id": rid, "argv": list(argv), "summary": summary, "strong": bool(strong), "seat": _seat(seat), "t": t}
    _write_atomic(inbox_dir(env) / (rid + ".json"), req)
    return req, ""


def status(rid, env=None):
    if not isinstance(rid, str) or not _ID_RE.match(rid):
        return None
    d = _read_json(done_dir(env) / (rid + ".json"), 64 * 1024)
    if d:
        return d
    for where in (inbox_dir(env), running_dir(env)):
        if (where / (rid + ".json")).is_file():
            return {"id": rid, "result": "pending"}
    return None


# ---- 彈窗 ----

def _t(key, params, fallback, env=None):
    try:
        import i18n_lib
        return i18n_lib.t(key, params, locale=i18n_lib.resolve_locale(env=env))
    except Exception:  # noqa: BLE001 — 語系包壞掉也要能跳窗
        return fallback


def _who(req):
    return "@" + req["seat"] if req.get("seat") else "?"


def build_strong_script(title, body, check, go, no, nonce):
    rep = {"__TITLE__": title, "__BODY__": body, "__CHECK__": check, "__GO__": go, "__NO__": no, "__NONCE__": nonce}
    s = STRONG_JXA
    for k, v in rep.items():
        s = s.replace(k, json.dumps(str(v)))
    return s


def ask_strong(req, env=None, run=subprocess.run, wait=TA.WAIT_S * 3):
    """加強版第一步：勾選框對話框。回 {"ok": bool, "why": str}。"""
    cmd = display_cmd(req["argv"])
    body = _t("aib.consent.strong_body", {"who": _who(req), "summary": req["summary"], "cmd": cmd},
              "Summary written by the seat calling itself %s (may be wrong):\n%s\n\nCommand that will run:\n%s"
              % (_who(req), req["summary"], cmd), env)
    if req.get("sign"):
        body += "\n\n" + _t("aib.consent.sign_extra", {"sha": req["sign"]["sha256"], "head": req["sign"]["head"]},
                            "Script sha256 %s; first lines:\n%s" % (req["sign"]["sha256"], req["sign"]["head"]), env)
    return ask_checkbox(_t("aib.consent.strong_title", {}, "AIB consent (strong)", env), body,
                        _t("aib.consent.strong_check", {}, "I have read this", env), env=env, run=run, wait=wait)


def ask_checkbox(title, body, check, env=None, run=subprocess.run, wait=TA.WAIT_S * 3):
    """勾選框對話框(核心組的字;aib approve enroll 也用)。回 {"ok": bool, "why": str}:
    勾了按繼續 = ok;取消 = cancel;沒勾就按繼續 = not_checked;逾時 / 讀不懂 / 回的 nonce 不對 ⇒ 不 ok。"""
    nonce = secrets.token_hex(16)
    script = build_strong_script(title, body, check, _t("aib.consent.go", {}, "Continue", env),
                                 _t("aib.consent.no", {}, "Cancel", env), nonce)
    try:
        r = run([TA.OSASCRIPT, "-l", "JavaScript", "-"], input=script, capture_output=True, text=True, timeout=wait)
    except subprocess.TimeoutExpired:
        return {"ok": False, "why": "timeout"}
    except OSError:
        return {"ok": False, "why": "osascript_failed"}
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    try:
        out = json.loads(lines[-1]) if lines else None
    except ValueError:
        out = None
    if r.returncode != 0 or not isinstance(out, dict):
        return {"ok": False, "why": "unreadable"}
    if out.get("ok") is True and out.get("nonce") == nonce:
        return {"ok": True, "why": "ok"}
    if out.get("ok") is True:
        return {"ok": False, "why": "nonce_mismatch"}
    return {"ok": False, "why": "not_checked" if out.get("go") else "cancel"}


REASON_MAX = 160


def _reason_full(req, env=None):
    cmd = display_cmd(req["argv"])
    return _t("aib.consent.touchid_reason", {"who": _who(req), "cmd": cmd},
              "AIB consent · %s wants to run: %s" % (_who(req), cmd), env)


def touchid_reason(req, env=None, kind=None):
    """統一入口:SIGN 腳本 = 發版簽章(高風險),其他 = 一般指令核准;類別由 classify 的結果定,申請檔給不了。"""
    import approve_gate as AG
    return AG.reason("consent.sign_script" if kind == "sign_script" else "consent.command",
                     {"who": _who(req), "cmd": display_cmd(req["argv"])}, env=env)


def _sign_info(path):
    raw = _read_regular(path, MAX_SIGN_BYTES)
    if raw is None:
        return None
    head = "\n".join(TA.clean(ln, 160) for ln in raw.decode("utf-8", "replace").splitlines()[:SIGN_HEAD_LINES])
    return {"sha256": hashlib.sha256(raw).hexdigest(), "head": head, "raw": raw}


# ---- 處理 ----

def _record(rid, result, env, req=None, **extra):
    req = req or {}
    d = {"v": 1, "id": rid, "result": result, "argv": req.get("argv"), "summary": req.get("summary"),
         "seat": req.get("seat"), "strong": req.get("strong"), "decided_at": int(time.time())}
    d.update(extra)
    _write_atomic(done_dir(env) / (rid + ".json"), d)
    return d


def _quarantine(path, env, why):
    """壞檔 / 撞號:搬到 bad/(不留在 inbox 每輪再判一次);搬不動就試著刪。"""
    dst = bad_dir(env) / ("%d-%s-%s" % (int(time.time()), secrets.token_hex(3), why))
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(path, dst)
    except OSError:
        try:
            os.unlink(path)
        except OSError:
            pass


def decide(req, env=None, ask=None, ask_strong_fn=None, available=None, now=None):
    """在鎖內：驗 + 跳窗。回 (result 或 None=可以跑, 細節 dict, 要跑的 argv, 暫存夾)。"""
    ask = ask or TA.ask
    ask_strong_fn = ask_strong_fn or ask_strong
    avail = TA.available() if available is None else available
    t = req.get("t") if type(req.get("t")) is int else None
    now = now if now is not None else time.time()
    if t is None or now - t > EXPIRE_S or t - now > 60:      # 寫成未來時間也不收
        return "expired", {}, None, None
    argv, strong = req.get("argv"), req.get("strong") is True
    kind, why = classify(argv, strong, env)
    if not kind:
        return "not_allowed", {"why": why}, None, None
    summary = _clean_summary(req.get("summary"))
    if not summary:
        return "invalid", {"why": "summary_missing"}, None, None
    view = dict(req, summary=summary, strong=strong, seat=_seat(req.get("seat")))
    if len(display_cmd(argv)) > REASON_CMD_MAX or len(_reason_full(view, env)) > REASON_MAX:
        view["strong"] = strong = True   # Touch ID 視窗放不下整句(含語系前綴)⇒ 先出對話框列全文
    sign = None
    if kind == "sign_script":
        sign = _sign_info(Path(argv[1]).expanduser().resolve())
        if not sign:
            return "failed", {"why": "sign_unreadable"}, None, None
        view["sign"] = {"sha256": sign["sha256"], "head": sign["head"]}
    if not avail:
        return "unavailable", {"why": "touchid_unavailable"}, None, None
    if strong:
        s = ask_strong_fn(view, env=env)
        if not s.get("ok"):
            return "denied", {"why": s.get("why")}, None, None
    reason = touchid_reason(view, env, kind)
    if __import__("approve_gate").se_reason(reason) is None:
        return "denied", {"why": "reason_refused"}, None, None
    a = ask(reason)
    if not a.get("ok"):
        return "denied", {"why": a.get("why")}, None, None
    if kind == "aib":
        return None, {}, [str(HERE / "aib")] + argv[1:], None
    if kind == "claude_plugin":
        exe = shutil.which("claude", path=os.pathsep.join([SAFE_PATH, str(_home(env) / ".local" / "bin")]))
        return (None, {}, [exe] + argv[1:], None) if exe else ("failed", {"why": "exe_missing"}, None, None)
    # SIGN:同意的那一份複製到 0700 暫存夾，再驗一次雜湊才跑(同意後原檔被改也跑不到改過的)
    td = tempfile.mkdtemp(prefix="aib-consent-sign-")
    try:
        os.chmod(td, 0o700)
        copy = Path(td) / Path(argv[1]).name
        copy.write_bytes(sign["raw"])
        os.chmod(copy, 0o600)
        same = hashlib.sha256(copy.read_bytes()).hexdigest() == sign["sha256"]
    except OSError:
        same = False
    if not same:
        shutil.rmtree(td, ignore_errors=True)
        return "failed", {"why": "sign_hash_changed"}, None, None
    return None, {"sha256": sign["sha256"]}, ["/bin/bash", str(copy)], td


def execute(rid, req, real, env=None, run=subprocess.run, extra=None):
    log = done_dir(env) / (rid + ".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    started = int(time.time())
    child_env = {"HOME": str(_home(env)), "PATH": os.pathsep.join([SAFE_PATH, str(_home(env) / ".local" / "bin")]),
                 "EZ2_AIB_STATE_ROOT": str(state_root(env))}
    for k in ("LANG", "EZ2_AIB_LOCALE"):
        if _env(env).get(k):
            child_env[k] = _env(env)[k]
    extra = dict(extra or {})
    try:
        with open(log, "w", encoding="utf-8") as fh:
            p = run(real, cwd=str(_home(env)), stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                    timeout=RUN_TIMEOUT_S, env=child_env)
    except subprocess.TimeoutExpired:
        return _record(rid, "timeout", env, req, started=started, log=str(log), **extra)
    except OSError as exc:
        return _record(rid, "failed", env, req, why="run_oserror:%s" % exc.__class__.__name__, log=str(log), **extra)
    rc = p.returncode
    _notify(req, rc, env)
    return _record(rid, "ok" if rc == 0 else "rc_nonzero", env, req, rc=rc, started=started,
                   ended=int(time.time()), log=str(log), **extra)


def _notify(req, rc, env=None):
    if sys.platform != "darwin":
        return
    msg = TA.clean(_t("aib.consent.notify", {"cmd": display_cmd(req["argv"]), "rc": rc},
                      "AIB notice · ran %s (rc %s)" % (display_cmd(req["argv"]), rc), env), 160)
    try:
        subprocess.run([TA.OSASCRIPT, "-e", "display notification %s with title \"AIB\"" % json.dumps(msg, ensure_ascii=False)],
                       capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def _next_inbox(env=None, skip=()):
    """inbox 裡最舊的一件:(id, 路徑) 或 (None, 路徑)=壞檔名;沒有 ⇒ None;只剩剛寫的(2 秒內)⇒ ("", None)。
    skip:這次已經搬不動的路徑(不再挑，免得空轉)。"""
    try:
        names = sorted(os.listdir(inbox_dir(env)))
    except OSError:
        return None
    best, fresh = None, False
    now = time.time()
    for name in names:
        if name.startswith("."):
            continue
        p = inbox_dir(env) / name
        if p in skip:
            continue
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if 0 <= now - st.st_mtime < FRESH_S:      # 時間在未來的檔不當剛寫的(免得永遠卡著)
            fresh = True
            continue
        rid = name[:-5] if name.endswith(".json") else None
        ok = rid is not None and _ID_RE.match(rid) and stat.S_ISREG(st.st_mode)
        key = (st.st_mtime, name)
        if best is None or key < best[0]:
            best = (key, rid if ok else None, p)
    if best is None:
        return ("", None) if fresh else None
    return best[1], best[2]


def _sweep_running(env=None, now=None):
    """running/ 裡的孤兒(處理到一半當機 / 重開機):超過過期 + 執行上限還在 ⇒ 記 failed、搬 bad/。"""
    now = now if now is not None else time.time()
    try:
        names = os.listdir(running_dir(env))
    except OSError:
        return
    for name in names:
        p = running_dir(env) / name
        rid = name[:-5] if name.endswith(".json") else ""
        req = _read_json(p) or {}
        t = req.get("t") if type(req.get("t")) is int else None
        if t is None:
            try:
                t = int(os.lstat(p).st_mtime)
            except OSError:
                continue
        if -60 <= now - t <= EXPIRE_S + RUN_TIMEOUT_S + TA.WAIT_S * 5:   # 跟 decide 一樣容許 t 晚 60 秒   # 決定最晚 t+30 分 + 兩個視窗 + 執行上限
            continue
        if _ID_RE.match(rid or "-!") and not (done_dir(env) / (rid + ".json")).exists():
            _record(rid, "failed", env, req, why="orphan_running")
        _quarantine(p, env, "orphan")


def _lock(env=None):
    return TA.one_at_a_time(state_root(env) / "locks" / "touchid-approve.lock")


def run_once(env=None, run=subprocess.run, sleep=time.sleep, retry_s=LOCK_RETRY_S, **kw):
    """處理 inbox 到空。每件：拿鎖 → 搬到 running/(搬不動 = 別支接手了)→ 跳窗決定 → 放鎖 → 跑。
    鎖被別的指紋視窗拿著 ⇒ 每 5 秒再試，最多 retry_s 秒(WatchPaths 不會因為檔還在就再叫)。回處理了幾件。"""
    n, waited, fresh_waits, skip = 0, 0, 0, set()
    _sweep_running(env)
    while True:
        nxt = _next_inbox(env, skip)
        if nxt is None:
            return n
        rid, path = nxt
        if path is None:                 # 只剩剛寫的:等它過 2 秒再掃(WatchPaths 不會再叫一次)
            if fresh_waits >= 3:
                return n
            fresh_waits += 1
            sleep(FRESH_S)
            continue
        if rid is None:
            _quarantine(path, env, "badname")
            if path.exists() or path.is_symlink():
                skip.add(path)
            n += 1
            continue
        with _lock(env) as gate:
            if not gate.held:
                if waited >= retry_s:
                    return n
                sleep(LOCK_RETRY_STEP)
                waited += LOCK_RETRY_STEP
                continue
            waited = 0
            if (done_dir(env) / (rid + ".json")).exists() or (running_dir(env) / (rid + ".json")).exists():
                rid = "%s-%s" % (rid[:50], secrets.token_hex(3))   # 同檔名重送:換新編號照常處理，不覆寫舊紀錄
            mine = running_dir(env) / (rid + ".json")
            try:
                mine.parent.mkdir(parents=True, exist_ok=True)
                os.rename(path, mine)
            except OSError:
                skip.add(path)                  # 別支接手了或搬不動:這輪不再挑
                continue
            req = _read_json(mine)
            if req is None:
                _record(rid, "invalid", env, why="unreadable")
                _quarantine(mine, env, "unreadable")
                n += 1
                continue
            if type(req.get("t")) is not int:
                req["t"] = int(os.stat(mine).st_mtime)   # 席位手寫的檔沒寫 t ⇒ 用檔案時間
            req["id"] = rid
            try:
                result, detail, real, td = decide(req, env=env, **kw)
            except Exception as exc:  # noqa: BLE001 — 不讓一件壞申請卡在 running/、status 永遠 pending
                result, detail, real, td = "failed", {"why": "decide_error:%s" % exc.__class__.__name__}, None, None
        try:
            if result is not None:
                _record(rid, result, env, req, **detail)
            else:
                execute(rid, req, real, env=env, run=run, extra=detail)
        finally:
            if td:
                shutil.rmtree(td, ignore_errors=True)
            try:
                os.unlink(mine)
            except OSError:
                pass
        n += 1


# ---- launchd ----

def agent_plist_path(home=None):
    return Path(home or os.path.expanduser("~")) / "Library" / "LaunchAgents" / (AGENT_LABEL + ".plist")


def agent_plist(env=None):
    e = _env(env)
    log = consent_dir(env) / "agent.log"
    ev = {"HOME": str(_home(env)), "PATH": os.pathsep.join([SAFE_PATH, str(_home(env) / ".local" / "bin")]),
          "EZ2_AIB_STATE_ROOT": str(state_root(env))}   # 解析後的根(EZ2_AIB_HOME 等也算)⇒ 監看與處理同一個 inbox
    for k in ("LANG", "EZ2_AIB_LOCALE"):
        if e.get(k):
            ev[k] = e[k]
    return {"Label": AGENT_LABEL, "ProgramArguments": ["/bin/bash", str(HERE / "aib"), "consent", "run-once"],
            "WatchPaths": [str(inbox_dir(env))], "StartInterval": 300, "RunAtLoad": True, "ProcessType": "Interactive",
            "EnvironmentVariables": ev, "StandardOutPath": str(log), "StandardErrorPath": str(log)}


def install(env=None, home=None, platform=None, launchctl=None, dev_tree=None):
    import plistlib
    if (platform or sys.platform) != "darwin":
        return "mac_only"
    if dev_tree is None:
        import auto_update
        dev_tree = auto_update.dev_worktree()
    if dev_tree:
        return "dev_tree"            # 開發用工作樹刪了 launchd 就壞 ⇒ 只從正本樹裝
    data = agent_plist(env)
    inbox_dir(env).mkdir(parents=True, exist_ok=True)
    path = agent_plist_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        plistlib.dump(data, fh)
    lc = launchctl or (lambda *a: subprocess.run(["launchctl"] + list(a), capture_output=True, text=True, timeout=20).returncode)
    lc("unload", str(path))
    return "installed" if lc("load", "-w", str(path)) == 0 else "failed"


def uninstall(home=None, launchctl=None):
    path = agent_plist_path(home)
    if not path.exists():
        return "absent"
    lc = launchctl or (lambda *a: subprocess.run(["launchctl"] + list(a), capture_output=True, text=True, timeout=20).returncode)
    lc("unload", "-w", str(path))
    path.unlink()
    return "removed"


# ---- CLI ----

def main(argv=None, env=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="aib consent")
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("ask")
    a.add_argument("--summary", required=True)
    a.add_argument("--strong", action="store_true")
    a.add_argument("--seat")
    a.add_argument("--wait", type=int, default=0)
    a.add_argument("--json", action="store_true")
    a.add_argument("rest", nargs=argparse.REMAINDER)
    s = sub.add_parser("status")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    sub.add_parser("run-once")
    sub.add_parser("install")
    sub.add_parser("uninstall")
    args = ap.parse_args(argv)
    if args.cmd == "ask":
        rest = args.rest[1:] if args.rest[:1] == ["--"] else args.rest
        req, why = new_request(rest, args.summary, strong=args.strong,
                               seat=args.seat or _env(env).get("EZ2_AIB_SEAT"), env=env)
        if not req:
            print(json.dumps({"result": "not_allowed", "why": why}))
            return 2
        out = {"id": req["id"], "result": "pending"}
        deadline = time.time() + max(0, min(args.wait, 3600))
        while args.wait and time.time() < deadline:
            st = status(req["id"], env)
            if st and st.get("result") != "pending":
                out = st
                break
            time.sleep(1)
        print(json.dumps(out, ensure_ascii=False) if args.json else "%s %s" % (out["id"], out["result"]))
        return 0 if out["result"] == "ok" or (out["result"] == "pending" and args.wait <= 0) else 1
    if args.cmd == "status":
        st = status(args.id, env)
        if not st:
            print(json.dumps({"id": args.id, "result": "unknown"}))
            return 2
        print(json.dumps(st, ensure_ascii=False) if args.json else "%s %s rc=%s" % (st["id"], st["result"], st.get("rc", "-")))
        return 0
    if args.cmd == "run-once":
        print(run_once(env))
        return 0
    if args.cmd == "install":
        r = install(env)
        print(r)
        return 0 if r == "installed" else 1
    if args.cmd == "uninstall":
        print(uninstall())
        return 0
    print(_t("aib.consent.help.usage", {}, "usage: aib consent ask|status|run-once|install|uninstall", env))
    return 2


if __name__ == "__main__":
    sys.exit(main())
