#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib consent:要本人同意的指令一律由 aib 跳彈窗(Andy 10-25:「之後有 ! 要同意的，一率在aib 跳出同意，不用手打，
有生物辨識的 就用生物辨識，若需要加強的，多一個打勾 打勾後，將該事件最重要的 一句話總結 顯示給他看」;
設計 pending/PLAN-approve-popup-abb89-1025.md)。名字避開 aib approve(那是 WordPress 登入第二步)。

  aib consent ask [--strong] --summary <一句話> [--cwd 夾] [--wait 秒] [--json] -- <指令…>
      把申請放進 <狀態根>/consent/inbox/<id>.json,印 id;--wait 等結果(預設不等)。
  aib consent status <id> [--json]       看結果(pending / ok / denied / not_allowed / unavailable / failed / timeout)
  aib consent run-once                   處理 inbox 裡的申請(一次一件、依時間先後);launchd 監看 inbox 時叫這一支
  aib consent install | uninstall        裝 / 拿掉 launchd(WatchPaths = inbox,不常駐、不輪詢)

席位被 Claude Code 的安全檢查擋住、連 Bash 都不能跑時：用檔案工具直接寫一份 JSON 到 inbox(欄位同下),
launchd 看到檔案就會叫 run-once ⇒ 照樣跳窗。不用再叫 Andy 在輸入框打 `!`。
  {"argv": ["aib", "now", "--seat", "abb89", "…", "2/5"], "summary": "…", "strong": false, "seat": "abb89", "cwd": "/…"}

彈窗:
  一般 ⇒ 系統原生 Touch ID 視窗(touchid_approve.ask,沒 Touch ID 時系統給登入密碼欄;沒有純同意鈕)。
  strong ⇒ 先一個對話框：「席位寫的一句話」+「實際指令」全文 +「我看過了」勾選框;勾了按繼續才跳 Touch ID。

防線(同帳號 AI 威脅守則;照實寫):
  - 這是**同意的介面**,不是擋 AI 的防線:同帳號 AI 改得到這支程式、inbox、launchd 設定。真正擋 AI 的仍是各指令自己的
    human_attest / Touch ID / 簽章。這裡跑出來的行程是 launchd 底下的 python,human_attest 三條件本來就不過;
    這支**不**新增任何放行旗標或環境變數,也不留「已驗過」狀態(每件各自跳窗)。
  - 一句話總結是席位寫的、可能是假的 ⇒ 視窗一律同時列實際指令，並標明「席位寫的」;Touch ID 視窗字只放實際指令。
  - 白名單(第一版):aib <子指令>(consent 除外)、claude plugin …、bash ~/.ez2-aib/pending/SIGN-*.sh(只准 strong)。
    其他一律 not_allowed,要加再問 Andy。argv 是陣列，不經 shell。
  - 一次一件(touchid-approve 鎖，跟 newproj --approve / aib approve 共用;別的視窗開著 ⇒ 這件下次再處理);
    處理過的 id 搬到 done/,不重跑。
rc:ask 0(已排)/ 2(參數錯、不在白名單);status 0 / 2(沒這件);run-once 0。
"""
import argparse
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import touchid_approve as TA  # noqa: E402

AGENT_LABEL = "page.ez2.aib-consent"
RUN_TIMEOUT_S = 20 * 60
MAX_REQ_BYTES = 16 * 1024
MAX_ARGV = 40
MAX_ARG_LEN = 2000
SUMMARY_MAX = 200
REASON_CMD_MAX = 120
RESULTS = ("ok", "denied", "not_allowed", "unavailable", "failed", "timeout", "invalid")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SIGN_RE = re.compile(r"^SIGN-[A-Za-z0-9._-]+\.sh$")
_SEAT_RE = re.compile(r"^[a-z0-9]{3,12}$")

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
var r = a.runModal;
var checked = a.suppressionButton.state == 1;
JSON.stringify((r == 1000 && checked) ? {ok: true, nonce: NONCE} : {ok: false, go: r == 1000, checked: checked});
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


def done_dir(env=None):
    return consent_dir(env) / "done"


def _write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".%s.%s.tmp" % (path.name, secrets.token_hex(4)))
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _read_json(path, limit=MAX_REQ_BYTES):
    """只讀一般檔(具名管道不卡、不跟符號連結),超過 limit 當壞。"""
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
    if len(raw) > limit:
        return None
    try:
        d = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


# ---- 白名單 ----

def _pending_dir(env=None):
    e = _env(env)
    return Path(e.get("HOME") or os.path.expanduser("~")) / ".ez2-aib" / "pending"


def classify(argv, strong, env=None):
    """回 (kind, 原因)。kind ∈ aib / claude_plugin / sign_script;不准 ⇒ (None, 原因碼)。"""
    if not isinstance(argv, list) or not argv or len(argv) > MAX_ARGV:
        return None, "argv_shape"
    if not all(isinstance(a, str) and a and len(a) <= MAX_ARG_LEN and "\x00" not in a for a in argv):
        return None, "argv_shape"
    head = argv[0]
    if head == "aib":
        if len(argv) < 2 or argv[1].startswith("-") or argv[1] == "consent":
            return None, "aib_subcommand"
        return "aib", ""
    if head == "claude":
        if len(argv) < 3 or argv[1] != "plugin":
            return None, "claude_not_plugin"
        return "claude_plugin", ""
    if head == "bash":
        if len(argv) != 2:
            return None, "sign_shape"
        p = Path(argv[1]).expanduser()
        try:
            real, base = p.resolve(strict=True), _pending_dir(env).resolve(strict=True)
        except OSError:
            return None, "sign_missing"
        if real.parent != base or not _SIGN_RE.match(real.name) or not real.is_file():
            return None, "sign_path"
        if not strong:
            return None, "sign_needs_strong"
        return "sign_script", ""
    return None, "not_allowed"


def resolve_argv(argv, kind, env=None):
    """launchd 的 PATH 很短 ⇒ 換成絕對路徑。aib = 這份 aib 自己的入口;claude 從 PATH + ~/.local/bin 找。"""
    e = _env(env)
    if kind == "aib":
        return [str(HERE / "aib")] + argv[1:]
    if kind == "claude_plugin":
        path = os.pathsep.join([e.get("PATH") or "", str(Path(e.get("HOME") or "~").expanduser() / ".local" / "bin")])
        exe = shutil.which("claude", path=path)
        return [exe] + argv[1:] if exe else None
    if kind == "sign_script":
        return ["/bin/bash", str(Path(argv[1]).expanduser().resolve())]
    return None


def display_cmd(argv):
    return " ".join(shlex.quote(a) for a in argv)


# ---- 申請 ----

def new_request(argv, summary, strong=False, seat=None, cwd=None, env=None, now=None):
    kind, why = classify(argv, strong, env)
    if not kind:
        return None, why
    summary = TA.clean(summary, SUMMARY_MAX)
    if not summary:
        return None, "summary_missing"
    rid = "%d-%s" % (int(now if now is not None else time.time()), secrets.token_hex(4))
    req = {"v": 1, "id": rid, "argv": list(argv), "summary": summary, "strong": bool(strong),
           "seat": seat if isinstance(seat, str) and _SEAT_RE.match(seat) else None,
           "cwd": cwd if isinstance(cwd, str) and os.path.isabs(cwd) else None,
           "t": int(now if now is not None else time.time())}
    _write_atomic(inbox_dir(env) / (rid + ".json"), req)
    return req, ""


def status(rid, env=None):
    if not isinstance(rid, str) or not _ID_RE.match(rid):
        return None
    d = _read_json(done_dir(env) / (rid + ".json"), 64 * 1024)
    if d:
        return d
    if (inbox_dir(env) / (rid + ".json")).is_file():
        return {"id": rid, "result": "pending"}
    return None


# ---- 彈窗 ----

def _t(key, params, fallback, env=None):
    try:
        import i18n_lib
        return i18n_lib.t(key, params, locale=i18n_lib.resolve_locale(env=env))
    except Exception:  # noqa: BLE001 — 語系包壞掉也要能跳窗
        return fallback


def build_strong_script(title, body, check, go, no, nonce):
    rep = {"__TITLE__": title, "__BODY__": body, "__CHECK__": check, "__GO__": go, "__NO__": no, "__NONCE__": nonce}
    s = STRONG_JXA
    for k, v in rep.items():
        s = s.replace(k, json.dumps(str(v)))
    return s


def ask_strong(req, env=None, run=subprocess.run, wait=TA.WAIT_S * 3):
    """加強版第一步：勾選框對話框。回 {"ok": bool, "why": str}。"""
    nonce = secrets.token_hex(16)
    who = "@" + req["seat"] if req.get("seat") else "?"
    cmd = display_cmd(req["argv"])
    body = _t("aib.consent.strong_body", {"who": who, "summary": req["summary"], "cmd": cmd},
              "Summary written by %s (may be wrong):\n%s\n\nCommand that will run:\n%s" % (who, req["summary"], cmd), env)
    script = build_strong_script(
        _t("aib.consent.strong_title", {}, "AIB consent (strong)", env), body,
        _t("aib.consent.strong_check", {}, "I have read this", env),
        _t("aib.consent.go", {}, "Continue", env), _t("aib.consent.no", {}, "Cancel", env), nonce)
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


def touchid_reason(req, env=None):
    who = "@" + req["seat"] if req.get("seat") else "?"
    cmd = TA.clean(display_cmd(req["argv"]), REASON_CMD_MAX)
    return TA.clean(_t("aib.consent.touchid_reason", {"who": who, "cmd": cmd}, "AIB consent · %s wants to run: %s" % (who, cmd),
                       env), 160)


# ---- 處理 ----

def _finish(req, result, env, **extra):
    d = {"v": 1, "id": req["id"], "result": result, "argv": req.get("argv"), "summary": req.get("summary"),
         "seat": req.get("seat"), "strong": req.get("strong"), "decided_at": int(time.time())}
    d.update(extra)
    _write_atomic(done_dir(env) / (req["id"] + ".json"), d)
    try:
        os.unlink(inbox_dir(env) / (req["id"] + ".json"))
    except OSError:
        pass
    return d


def _load_inbox(env=None):
    out = []
    try:
        names = sorted(os.listdir(inbox_dir(env)))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json") or name.startswith("."):
            continue
        rid = name[:-5]
        p = inbox_dir(env) / name
        d = _read_json(p)
        if not _ID_RE.match(rid) or d is None:
            out.append(({"id": rid if _ID_RE.match(rid) else "bad-%s" % secrets.token_hex(4)}, "invalid", p))
            continue
        d["id"] = rid                                    # 檔名才是 id(席位手寫的檔可能沒寫 / 寫錯)
        out.append((d, None, p))
    out.sort(key=lambda x: (x[0].get("t") if isinstance(x[0].get("t"), int) else 0, x[0]["id"]))
    return out


def process(req, env=None, run=subprocess.run, ask=None, ask_strong_fn=None, available=None, notify=None):
    """處理一件(呼叫端已拿到 touchid 鎖)。回 done 紀錄。"""
    ask = ask or TA.ask
    ask_strong_fn = ask_strong_fn or ask_strong
    avail = TA.available() if available is None else available
    argv, strong = req.get("argv"), req.get("strong") is True
    kind, why = classify(argv, strong, env)
    if not kind:
        return _finish(req, "not_allowed", env, why=why)
    if len(display_cmd(argv)) > REASON_CMD_MAX:
        strong = True        # Touch ID 視窗放不下整條指令 ⇒ 一律先出對話框列全文(本人按指紋前要看得到完整指令)
    summary = TA.clean(req.get("summary"), SUMMARY_MAX)
    if not summary:
        return _finish(req, "invalid", env, why="summary_missing")
    req = dict(req, summary=summary, strong=strong,
               seat=req.get("seat") if isinstance(req.get("seat"), str) and _SEAT_RE.match(req["seat"]) else None)
    if not avail:
        return _finish(req, "unavailable", env, why="touchid_unavailable")
    if strong:
        s = ask_strong_fn(req, env=env)
        if not s.get("ok"):
            return _finish(req, "denied", env, why=s.get("why"))
    t = ask(touchid_reason(req, env))
    if not t.get("ok"):
        return _finish(req, "denied", env, why=t.get("why"))
    real = resolve_argv(argv, kind, env)
    if not real or not real[0]:
        return _finish(req, "failed", env, why="exe_missing")
    cwd = req.get("cwd") if isinstance(req.get("cwd"), str) and os.path.isdir(req["cwd"]) else str(Path.home())
    log = done_dir(env) / (req["id"] + ".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    started = int(time.time())
    try:
        with open(log, "w", encoding="utf-8") as fh:
            p = run(real, cwd=cwd, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT, timeout=RUN_TIMEOUT_S)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        return _finish(req, "timeout", env, started=started, log=str(log))
    except OSError as exc:
        return _finish(req, "failed", env, why="run_oserror:%s" % exc.__class__.__name__, log=str(log))
    (notify or _notify)(req, rc, env)
    return _finish(req, "ok", env, rc=rc, started=started, ended=int(time.time()), log=str(log))


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


def run_once(env=None, **kw):
    """把 inbox 處理完(一次一件)。別的指紋視窗開著 ⇒ 停手,留給下一次。回處理了幾件。"""
    n = 0
    for req, bad, _p in _load_inbox(env):
        if bad:
            _finish(req, bad, env, why="unreadable")
            n += 1
            continue
        with TA.one_at_a_time(state_root(env) / "locks" / "touchid-approve.lock") as gate:
            if not gate.held:
                break
            process(req, env=env, **kw)
            n += 1
    return n


# ---- launchd ----

def agent_plist_path(home=None):
    return Path(home or os.path.expanduser("~")) / "Library" / "LaunchAgents" / (AGENT_LABEL + ".plist")


def agent_plist(env=None, python=None):
    e = _env(env)
    inbox = inbox_dir(env)
    log = consent_dir(env) / "agent.log"
    ev = {"HOME": e.get("HOME") or os.path.expanduser("~"),
          "PATH": os.pathsep.join([e.get("PATH") or "/usr/bin:/bin", str(Path(e.get("HOME") or "~").expanduser() / ".local" / "bin")])}
    for k in ("EZ2_AIB_STATE_ROOT", "LANG", "EZ2_AIB_LOCALE"):
        if e.get(k):
            ev[k] = e[k]
    return {"Label": AGENT_LABEL, "ProgramArguments": [python or sys.executable, str(HERE / "consent.py"), "run-once"],
            "WatchPaths": [str(inbox)], "RunAtLoad": True, "ProcessType": "Interactive",
            "EnvironmentVariables": ev, "StandardOutPath": str(log), "StandardErrorPath": str(log)}


def install(env=None, home=None, platform=None, launchctl=None):
    import plistlib
    if (platform or sys.platform) != "darwin":
        return "mac_only"
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
    a.add_argument("--cwd")
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
        req, why = new_request(rest, args.summary, strong=args.strong, seat=args.seat, cwd=args.cwd or os.getcwd(), env=env)
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
        return 0
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
        print(install(env))
        return 0
    if args.cmd == "uninstall":
        print(uninstall())
        return 0
    print(_t("aib.consent.help.usage", {}, "usage: aib consent ask|status|run-once|install|uninstall", env))
    return 2


if __name__ == "__main__":
    sys.exit(main())
