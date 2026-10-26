#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""對話面板核心 C1 / C4 / C5(10-20 Andy「對話面板放核心免費」;@abb89 SPEC-chat-panel 第 1、6 節):

  aib seat input  <席碼> --text … | --file … [--unlock-min N]   把字打進那一席的提示列(Touch ID;過了之後同席免重驗,
                                                  真人 N 分鐘沒再下指令才上鎖;N 預設 window.json / 10)
  aib seat input  <席碼> --text … --queued          艦長室排隊背景送：只在解鎖窗內送、不彈 Touch ID、不延長窗;窗關 ⇒ locked
  aib seat unlock <席碼> [--min N]                 先解鎖不送字(Touch ID;一樣是閒置 N 分鐘才上鎖)
  aib seat lock   <席碼>                           馬上上鎖(不用 Touch ID;上鎖只會更安全)
  aib seat unlocked <席碼>                         只讀：視窗還剩幾秒、閒置幾分鐘上鎖(頁面重整後顯示用)
  aib seat answer <席碼> <key|yes|no> [--ask-id ID] [--text …]   回答那一席正在問的(每次 Touch ID)
  aib seat stop   <席碼> [--hard]                 軟停 = Esc;硬停 = Ctrl-C(每次 Touch ID)

防線(GUIDE-same-user-ai-threat-model):
- 本人證明只認 Touch ID(touchid_approve;視窗那句由核心組、TA.clean);關掉 / 不能用 / 取消 / 逾時一律拒,不退 PIN。
- 呼叫者是 aib 席位(tmux 席位 pane、或帶 Claude / Codex 的對話 id 環境變數)⇒ 直接拒(AI 不准用這條路操控別席)。
- 送鍵前先看畫面:那一席要在提示列等人打字(seat_ask_patterns.at_prompt);在權限框 / 選擇題 / 思考中 / less / vim ⇒ 拒。
- 免重驗視窗(只套 input):Touch ID 過了之後寫 <狀態根>/panel-unlock/<席碼>.json {until, idle_s};
  10-21 Andy「解鎖下指令時沒有時限，真人不下指令之後才倒數」⇒ 視窗內每送出一次就把 until 往後推到 now + idle_s(閒置倒數);**這是本機檔,AI 改得到,只防本人誤操作**,
  不是防線(Andy 10-20 第 39 節 Z 已承認視窗內擋不住);answer / stop 每次都驗。
- 每次動作寫 <狀態根>/logs/seat-panel-actions.jsonl。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import seat_panel as SPN  # noqa: E402
import seat_ask_patterns as SAP  # noqa: E402
import seats_dir_lib  # noqa: E402

V = 1
UNLOCK_MIN_DEFAULT = 10
SESSION_ENVS = ("AIB_RESUME_SESSION_UUID", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")
TEXT_MAX = 64 * 1024
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def T(key, **params):
    import i18n_lib
    return i18n_lib.t("aib.seat.panel." + key, params, locale=i18n_lib.resolve_locale())


def state_root(env=None):
    e = os.environ if env is None else env
    return Path(e.get("EZ2_AIB_STATE_ROOT") or seats_dir_lib.state_root()[0])


def _out(ok, code, action, **fields):
    d = {"v": V, "ok": bool(ok), "seat": code, "action": action, "via": None, "why": None, "detail": ""}
    d.update(fields)
    return d


# ── 誰在叫 ──

def caller_is_seat(env=None):
    """呼叫這支的行程是不是一個 aib 席位(AI):tmux 席位 pane、或殼裡帶著 CLI 的對話 id ⇒ True。
    艦長室伺服器是 spawn_detached + clean_env 起的,這些都沒有。"""
    e = os.environ if env is None else env
    if any(e.get(k) for k in SESSION_ENVS):
        return True
    if e.get("TMUX_PANE") or e.get("TMUX"):
        return True                                         # 在 tmux 裡的都當席位(艦長室伺服器是 clean_env 起的,沒有 TMUX)
    return False


# ── Touch ID ──

def unlock_path(code, env=None):
    return state_root(env) / "panel-unlock" / (code + ".json")


def unlock_left(code, env=None, now=None):
    """免重驗視窗還剩幾秒(0 = 沒有)。只防誤操作:本機檔,AI 改得到。"""
    try:
        rec = json.loads(unlock_path(code, env).read_text(encoding="utf-8"))
        until = int(rec.get("until") or 0)
    except (OSError, ValueError, AttributeError, TypeError):
        return 0
    return max(0, until - int(now or time.time()))


def unlock_via(code, env=None):
    """視窗是誰開的:"touchid"(本人在 Mac 上)/ "remote-grant"(手機 + ez2.page 解鎖單);沒開 / 舊檔沒記 ⇒ None"""
    if unlock_left(code, env) <= 0:
        return None
    try:
        v = json.loads(unlock_path(code, env).read_text(encoding="utf-8")).get("via")
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    return v if v in ("touchid", "remote-grant") else None


def remote_left(code, env=None, now=None):
    """手機那頭看得到的視窗剩幾秒：只認解鎖單開的(@abb89 10-21:本人在 Mac 上 Touch ID 開的不算，免得 ez2.page 登入被偷時趁本人開著視窗打字進來)"""
    return unlock_left(code, env, now) if unlock_via(code, env) == "remote-grant" else 0


def unlock_minutes(env=None):
    e = os.environ if env is None else env
    try:
        p = state_root(e) / "panel-unlock" / "window.json"
        m = int(json.loads(p.read_text(encoding="utf-8")).get("minutes"))
        return max(0, min(60, m))
    except (OSError, ValueError, AttributeError, TypeError):
        return UNLOCK_MIN_DEFAULT


def clamp_minutes(m):
    """解鎖分鐘：0(每次都驗)… 60;不是整數 ⇒ None(用預設)"""
    try:
        return max(0, min(60, int(m)))
    except (TypeError, ValueError):
        return None


def grant_unlock(code, env=None, minutes=None, via=None):
    """via = 這次是誰開的;None = 視窗本來開著就沿用原本的、沒開就算 touchid"""
    from atomic_json import write_json
    m = unlock_minutes(env) if clamp_minutes(minutes) is None else clamp_minutes(minutes)
    via = via or unlock_via(code, env) or "touchid"
    p = unlock_path(code, env)
    p.parent.mkdir(parents=True, exist_ok=True)
    if m <= 0:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        return 0
    write_json(str(p), {"v": V, "seat": code, "until": int(time.time()) + m * 60, "idle_s": m * 60, "at": int(time.time()), "via": via})
    os.chmod(p, 0o600)
    return m * 60


def refresh_unlock(code, env=None, now=None):
    """視窗還開著 ⇒ until 推到 now + idle_s(真人剛下指令,重新閒置倒數);回剩幾秒,沒開 ⇒ 0。只防誤操作:本機檔,AI 改得到。"""
    from atomic_json import write_json
    t = int(now or time.time())
    try:
        p = unlock_path(code, env)
        rec = json.loads(p.read_text(encoding="utf-8"))
        until = int(rec.get("until") or 0)
        idle = int(rec.get("idle_s") or unlock_minutes(env) * 60)
    except (OSError, ValueError, AttributeError, TypeError):
        return 0
    if until <= t or idle <= 0:
        return 0
    rec.update(until=t + idle, idle_s=idle)
    write_json(str(p), rec)
    os.chmod(p, 0o600)
    return idle


def lock_now(code, env=None):
    try:
        unlock_path(code, env).unlink()
        return True
    except FileNotFoundError:
        return False


def prove(reason, env, touchid=None, touchid_available=None, platform=None):
    """本人證明:只認 Touch ID。回 (via, why);why None = 過。"""
    import touchid_approve as TA
    platform = sys.platform if platform is None else platform
    if platform != "darwin":
        return "touchid", "touchid_unavailable"
    if str(env.get("AIB_TOUCHID", "")).strip() == "0":
        return "touchid", "touchid_off"
    can = touchid_available() if touchid_available else TA.available(platform=platform, environ=env)
    if not can:
        return "touchid", "touchid_unavailable"
    root = state_root(env)
    with TA.one_at_a_time(root / "locks" / "touchid-approve.lock") as gate:
        res = (touchid or TA.ask)(reason) if gate.held else {"ok": False, "why": "busy"}
    return "touchid", None if res.get("ok") is True else "touchid_" + str(res.get("why") or "failed").split(":")[0]


def reason_for(action, code, text, minutes=None):
    import touchid_approve as TA
    r = T("touchid_" + action, seat=code, text=TA.clean(text or "", 40))
    if action == "input" and minutes:
        r += T("touchid_unlock_tail", min=minutes)   # 本人按指紋前看得到「之後閒置 N 分鐘才上鎖」
    return r


# ── tmux ──

def _tmux_env():
    env = dict(os.environ)
    env.pop("TMUX", None)
    return env


def _target(rec):
    sock, wid = str(rec.get("socket") or ""), str(rec.get("window_id") or "")
    if not re.match(r"^aihub(?:-[0-9A-Za-z-]+)?$", sock) or not re.match(r"^@\d+$", wid):
        return None, None
    return sock, wid


_TARGET_FIELDS = ("session_uuid", "socket", "window_id", "pid")


def expected_target(rec, expected_session=None, expected_instance=None):
    """Optional browser identity contract, checked before asking for approval."""
    if expected_session is not None and (not expected_session or rec.get("session_uuid") != expected_session):
        return False
    if expected_instance is not None:
        path = rec.get("_file")
        if not expected_instance or not path or Path(path).parent.parent.name != expected_instance:
            return False
    return True


def fresh_target(rec, code, env):
    """Approval authorizes this record only, never a replacement in the same pane."""
    path = rec.get("_file")
    if not path:
        return False
    try:
        current = json.loads(Path(path).read_text(encoding="utf-8"))
        selected = SPN.find_seat(code, str(state_root(env)))
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(current, dict) or current.get("retired") or not selected or selected.get("_file") != path:
        return False
    return all(current.get(k) == rec.get(k) == selected.get(k) for k in _TARGET_FIELDS)


def send_text(rec, text, tmux="tmux", run=subprocess.run):
    """多行用 load-buffer + paste-buffer -p(bracketed paste),單行 send-keys -l;最後 Enter。"""
    import dangerous_seat_actions as DSA
    sock, wid = _target(rec)
    if not sock:
        return False, "no-pane"
    base = [tmux, "-L", sock]
    if "\n" in text:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, prefix="aib-panel-") as fh:
            fh.write(text)
            tmp = fh.name
        try:
            r = run(base + ["load-buffer", "-b", "aib-panel", tmp], capture_output=True, text=True, env=_tmux_env(), stdin=subprocess.DEVNULL, timeout=10)
            if r.returncode != 0:
                return False, "tmux-load-buffer"
            r = run(base + ["paste-buffer", "-p", "-d", "-b", "aib-panel", "-t", wid], capture_output=True, text=True, env=_tmux_env(), stdin=subprocess.DEVNULL, timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    else:
        if DSA.guard_send_keys([text]) != 0:
            return False, "denied-keys"
        r = run(base + ["send-keys", "-t", wid, "-l", text], capture_output=True, text=True, env=_tmux_env(), stdin=subprocess.DEVNULL, timeout=10)
    if r.returncode != 0:
        return False, "tmux-send"
    time.sleep(0.15)
    r = run(base + ["send-keys", "-t", wid, "Enter"], capture_output=True, text=True, env=_tmux_env(), stdin=subprocess.DEVNULL, timeout=10)
    return (r.returncode == 0), ("" if r.returncode == 0 else "tmux-enter")


def send_keys(rec, keys, tmux="tmux", run=subprocess.run):
    sock, wid = _target(rec)
    if not sock:
        return False, "no-pane"
    r = run([tmux, "-L", sock, "send-keys", "-t", wid] + list(keys), capture_output=True, text=True, env=_tmux_env(), stdin=subprocess.DEVNULL, timeout=10)
    return (r.returncode == 0), ("" if r.returncode == 0 else "tmux-send")


def log_action(code, action, env=None, **fields):
    p = state_root(env) / "logs" / "seat-panel-actions.jsonl"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict({"at": int(time.time()), "seat": code, "action": action}, **fields), ensure_ascii=False) + "\n")
        os.chmod(p, 0o600)
    except OSError:
        pass


PROMPT_WHYS = ("thinking", "esc-cancel", "draft", "pager", "permission", "no-prompt", "no-screen", "unknown")


def prompt_hint(why):
    """被擋原因的人話(四語 aib.seat.panel.prompt_why_<代碼>);代碼本身照留在 prompt_why。"""
    return T("prompt_why_" + (why if why in PROMPT_WHYS else "unknown").replace("-", "_"))


# ── C1 ──

def do_input(code, text, env=None, screen=None, touchid=None, touchid_available=None, platform=None, tmux="tmux", run=subprocess.run, minutes=None,
             window_only=False, queued=False, expected_session=None, expected_instance=None):
    """window_only = 手機遠端過來的(remote_link):視窗沒開 ⇒ 拒 locked,不在 Mac 上彈 Touch ID(手機那頭沒人按得到)
    queued = 艦長室排隊背景送的(@abb89 10-25):只看本機解鎖窗 unlock_left;窗關 ⇒ 拒 locked、不彈 Touch ID;
             窗內送成功也不延長(只有真人親下的指令才重新閒置倒數)"""
    env = dict(os.environ if env is None else env)
    out = _out(False, code, "input")
    if caller_is_seat(env):
        out["why"] = "caller-is-seat"
        return out, 3
    if not isinstance(text, str) or not text.strip():
        out["why"] = "empty"
        return out, 2
    if len(text) > TEXT_MAX or _CTRL.search(text):
        out["why"] = "bad-text"
        return out, 2
    rec = SPN.find_seat(code, str(state_root(env)))
    if rec is None:
        out["why"] = "no-seat"
        return out, 2
    if not expected_target(rec, expected_session, expected_instance):
        out["why"] = "stale-session"
        return out, 2
    pend, _rc = SPN.pending(code, state_root=str(state_root(env)), screen=screen, env=env)
    if pend.get("ask"):
        out["why"], out["ask_id"] = "asking", pend["ask"]["id"]
        return out, 2
    if pend.get("at_prompt") is not True:
        # detail 是契約不動;原因另放 prompt_why + hint(10-25 Andy:停著卻被說在忙,被擋那次要留紀錄)
        pw = pend.get("prompt_why") or "unknown"
        out["why"], out["detail"] = "not-at-prompt", "busy=%s" % pend.get("busy")
        out["prompt_why"], out["hint"] = pw, prompt_hint(pw)
        log_action(code, "input", env, ok=False, why="not-at-prompt", prompt_why=pw, busy=pend.get("busy"),
                   line=pend.get("prompt_line"), chars=len(text))
        return out, 2
    left = remote_left(code, env) if window_only else unlock_left(code, env)
    m = unlock_minutes(env) if clamp_minutes(minutes) is None else clamp_minutes(minutes)
    reason = reason_for("input", code, text, m)
    out["touchid"] = {"prompt": reason, "wait_s": __import__("touchid_approve").WAIT_S}
    needs_grant = False
    if left > 0:
        out["via"] = "unlock-window"
    elif queued:
        out["why"], out["hint"] = "locked", T("queued_locked", seat=code)
        log_action(code, "input", env, ok=False, why="locked", chars=len(text), queued=True)
        return out, 3
    elif window_only:
        out["why"] = "locked"
        return out, 3
    else:
        out["via"], why = prove(reason, env, touchid, touchid_available, platform)
        if why:
            out["why"] = why
            log_action(code, "input", env, ok=False, why=why, chars=len(text))
            return out, 3
        needs_grant = True
    if not fresh_target(rec, code, env):
        out["why"] = "stale-session"
        return out, 2
    final_pending, _rc = SPN.pending(code, state_root=str(state_root(env)), screen=screen, env=env)
    if final_pending.get("ask") or final_pending.get("at_prompt") is not True:
        out["why"] = "asking" if final_pending.get("ask") else "not-at-prompt"
        return out, 2
    if not fresh_target(rec, code, env):
        out["why"] = "stale-session"
        return out, 2
    if needs_grant:
        left = grant_unlock(code, env, m, via="touchid")
    ok, why = send_text(rec, text, tmux=tmux, run=run)
    if ok and out["via"] == "unlock-window" and not queued:
        left = refresh_unlock(code, env) or left        # 真人剛下指令 ⇒ 閒置倒數重來(排隊背景送的不算)
    out.update(ok=ok, why=(None if ok else why), unlock_left_s=left, chars=len(text))
    extra = {"queued": True} if queued else {}
    log_action(code, "input", env, ok=ok, why=why or None, via=out["via"], chars=len(text), **extra)
    return out, (0 if ok else 4)


def do_unlock(code, minutes=None, env=None, touchid=None, touchid_available=None, platform=None, grant=None, keys=None, machine=None):
    """先解鎖不送字(頁面的 🔒 鈕、手機下指令前)。一樣 Touch ID、一樣擋席位呼叫;已經開著 ⇒ 不彈、照新分鐘重算。

    grant = 手機在 ez2.page 驗過本人後、伺服器簽的解鎖單(remote_grant.verify;keys / machine 只給測試注入)。
    有單子 ⇒ 不彈 Touch ID、分鐘照單子(再被本機上限夾);單子不過 ⇒ 拒，不退回 Touch ID(手機那頭沒人按得到)。
    """
    env = dict(os.environ if env is None else env)
    out = _out(False, code, "unlock")
    if caller_is_seat(env):
        out["why"] = "caller-is-seat"
        return out, 3
    if not isinstance(code, str) or SPN.find_seat(code, str(state_root(env))) is None:
        out["why"] = "no-seat"
        return out, 2
    m = unlock_minutes(env) if clamp_minutes(minutes) is None else clamp_minutes(minutes)
    if m <= 0:
        out["why"] = "zero-minutes"
        return out, 2
    if grant is not None:
        import remote_grant
        gm, why = remote_grant.verify(grant, code, env, keys=keys, machine=machine)
        if why:
            out.update(via="remote-grant", why="grant_" + why)
            log_action(code, "unlock", env, ok=False, why=out["why"])
            return out, 3
        out["via"], out["auth"] = "remote-grant", grant.get("auth")
        left = grant_unlock(code, env, gm, via="remote-grant")
        out.update(ok=True, unlock_left_s=left, minutes=gm)
        log_action(code, "unlock", env, ok=True, via="remote-grant", minutes=gm, auth=grant.get("auth"))
        return out, 0
    reason = T("touchid_unlock", seat=code, min=m)
    out["touchid"] = {"prompt": reason, "wait_s": __import__("touchid_approve").WAIT_S}
    if unlock_left(code, env) > 0:
        out["via"] = "unlock-window"
    else:
        out["via"], why = prove(reason, env, touchid, touchid_available, platform)
        if why:
            out["why"] = why
            log_action(code, "unlock", env, ok=False, why=why)
            return out, 3
    left = grant_unlock(code, env, m)
    out.update(ok=True, unlock_left_s=left, minutes=m)
    log_action(code, "unlock", env, ok=True, via=out["via"], minutes=m)
    return out, 0


def do_unlocked(code, env=None):
    env = dict(os.environ if env is None else env)
    left = unlock_left(code, env)
    idle = 0
    if left > 0:
        try:
            idle = int(json.loads(unlock_path(code, env).read_text(encoding="utf-8")).get("idle_s") or 0)
        except (OSError, ValueError, AttributeError, TypeError):
            idle = 0
    return _out(True, code, "unlocked", via="none", unlock_left_s=left, idle_s=idle, default_min=unlock_minutes(env)), 0


REMOTE_WHYS = ("not_bound", "link_stopped", "server_not_ready", "server_unreachable")


def remote_ready(bound, link_running, ready_fn):
    """遙控頁「上線了沒」(@74e8b 聯動表第 1 項):依序看綁了沒 → 本機連線在跑沒 → 伺服器說 ready 沒。
    前面不成立就不打伺服器(server_ready = None)。remote_why 是契約代碼，人話另放 hint。"""
    out = {"remote_ready": False, "remote_why": None, "link_running": bool(link_running), "server_ready": None}
    if not bound:
        out["remote_why"] = "not_bound"
    elif not link_running:
        out["remote_why"] = "link_stopped"
    else:
        out["server_ready"], out["remote_why"] = ready_fn()
        out["remote_ready"] = out["server_ready"] is True and out["remote_why"] is None
    if out["remote_why"]:
        out["hint"] = T("remote_why_" + out["remote_why"])
    return out


def do_remote(state, env=None, max_min=None, allow=None, touchid=None, touchid_available=None, platform=None, link_ctl=None):
    """手機遠端解鎖總開關(remote_grant.remote_cfg)。on 要本機 Touch ID、擋席位呼叫;off / status 不問。
    一鍵:on 之後順手在背景起 aib seat remote link(還沒綁 ez2.page 就不起、回 link_why);off 一起停。link_ctl 只給測試注入。"""
    import remote_grant
    import remote_link
    ctl = link_ctl or remote_link
    env = dict(os.environ if env is None else env)
    out = {"ok": False, "action": "remote", "state": state}
    if state == "on":
        if caller_is_seat(env):
            out["why"] = "caller-is-seat"
            return out, 3
        out["via"], why = prove(T("touchid_remote_on"), env, touchid, touchid_available, platform)
        if why:
            out["why"] = why
            return out, 3
        cfg = remote_grant.set_remote(True, env, max_min=max_min, allow=allow)
        out["link_pid"], out["link_why"] = ctl.start_bg(env)
    elif state == "off":
        cfg = remote_grant.set_remote(False, env)
        ctl.stop_bg(env)
    elif state == "status":
        cfg = remote_grant.remote_cfg(env)
    else:
        out["why"] = "bad-state"
        return out, 2
    mid, accts = remote_grant.this_machine(env)
    out.update(ok=True, on=cfg["on"], max_min=cfg["max_min"], allow=cfg["allow"], bound=bool(mid and accts),
               machine_fp=mid, link=bool(ctl.running(env)),
               keys=len(remote_grant.TRUSTED_GRANT_KEYS))
    out.update(_bind_info(env))
    if state == "status":
        ready = getattr(ctl, "server_ready", None)
        out.update(remote_ready(out["bound"], out["link"], (lambda: ready(env)) if ready else (lambda: (None, None))))
    return out, 0


def _bind_info(env):
    """充電座燈號(@abb89 ③、@9f808 days_left):綁到這台那一頁的 slug / bind_expires_at / days_left;沒綁 ⇒ 三個 None。"""
    page_dir = os.path.join(HERE, "page")
    if page_dir not in sys.path:
        sys.path.insert(0, page_dir)
    try:
        import bind_bg
        info = bind_bg.bound_here(env)
    except Exception:  # noqa: BLE001  讀不到 = 灰燈,不讓 status 掛
        info = None
    return info or {"slug": None, "bind_expires_at": None, "days_left": None}


def do_lock(code, env=None):
    env = dict(os.environ if env is None else env)
    out = _out(True, code, "lock", via="none", unlock_left_s=0)
    out["was_open"] = lock_now(code, env)
    log_action(code, "lock", env, ok=True)
    return out, 0


# ── C4 ──

def _answer_keys(ask, choice, free_text):
    """回答 ⇒ 要送的鍵;算不出 ⇒ None。permission:1/2/3 或 yes / no;question:數字(1–9)或 --text 自填。"""
    opts = ask.get("options") or []
    keys = [o["key"] for o in opts]
    if ask.get("type") == "permission":
        if choice == "yes":
            return [keys[0]] if keys else None
        if choice == "no":
            return [keys[-1]] if keys else None
        return [choice] if choice in keys else None
    if free_text:
        return ["__text__"]
    if choice == "yes" and keys:
        return [keys[0], "Enter"]
    return [choice, "Enter"] if choice in keys else None


def do_answer(code, choice, ask_id=None, free_text=None, env=None, screen=None, touchid=None, touchid_available=None, platform=None,
              tmux="tmux", run=subprocess.run, grant=None, grant_keys=None, machine=None, expected_session=None, expected_instance=None):
    """grant = 手機那頭的回答單(remote_grant.verify_answer:綁這一題 + 這個選項，一次一張);沒有就照舊每次 Touch ID"""
    env = dict(os.environ if env is None else env)
    out = _out(False, code, "answer")
    if caller_is_seat(env):
        out["why"] = "caller-is-seat"
        return out, 3
    rec = SPN.find_seat(code, str(state_root(env)))
    if rec is None:
        out["why"] = "no-seat"
        return out, 2
    if not expected_target(rec, expected_session, expected_instance):
        out["why"] = "stale-session"
        return out, 2
    pend, _rc = SPN.pending(code, state_root=str(state_root(env)), screen=screen, env=env)
    ask = pend.get("ask")
    if not ask:
        out["why"] = "no-ask"
        return out, 2
    if ask_id and ask_id != ask["id"]:
        out["why"], out["ask_id"] = "stale", ask["id"]
        return out, 2
    if free_text and (len(free_text) > TEXT_MAX or _CTRL.search(free_text)):
        out["why"] = "bad-text"
        return out, 2
    keys = _answer_keys(ask, str(choice), free_text)
    if not keys:
        out["why"], out["options"] = "bad-choice", [o["key"] for o in ask.get("options") or []]
        return out, 2
    label = next((o.get("label") for o in ask.get("options") or [] if o["key"] == keys[0]), free_text or choice)
    reason = reason_for("answer", code, "%s → %s" % (ask.get("prompt", "")[:20], label))
    out["touchid"] = {"prompt": reason, "wait_s": __import__("touchid_approve").WAIT_S}
    if grant is not None:
        import remote_grant
        _ok, gwhy = remote_grant.verify_answer(grant, code, ask["id"], choice, free_text, env, keys=grant_keys, machine=machine, question=ask)
        out["via"], why = "remote-grant", (None if _ok else "grant_" + gwhy)
    else:
        out["via"], why = prove(reason, env, touchid, touchid_available, platform)      # 每次都驗,不吃免重驗視窗
    if why:
        out["why"] = why
        log_action(code, "answer", env, ok=False, why=why, ask_id=ask["id"])
        return out, 3
    if not fresh_target(rec, code, env):
        out["why"] = "stale-session"
        return out, 2
    final_pending, _rc = SPN.pending(code, state_root=str(state_root(env)), screen=screen, env=env)
    if final_pending.get("ask") != ask:
        out["why"] = "stale"
        return out, 2
    if not fresh_target(rec, code, env):
        out["why"] = "stale-session"
        return out, 2
    if keys == ["__text__"]:
        ok, why = send_text(rec, free_text, tmux=tmux, run=run)
    else:
        ok, why = send_keys(rec, keys, tmux=tmux, run=run)
    out.update(ok=ok, why=(None if ok else why), answered=ask["id"], keys=keys)
    log_action(code, "answer", env, ok=ok, why=why or None, ask_id=ask["id"], keys=keys)
    return out, (0 if ok else 4)


# ── C5 ──

def do_stop(code, hard=False, env=None, touchid=None, touchid_available=None, platform=None, tmux="tmux", run=subprocess.run, window_only=False, expected_session=None, expected_instance=None):
    """window_only = 手機遠端過來的：視窗開著 ⇒ 不彈 Touch ID 直接停;沒開 ⇒ 拒 locked"""
    env = dict(os.environ if env is None else env)
    out = _out(False, code, "stop")
    if caller_is_seat(env):
        out["why"] = "caller-is-seat"
        return out, 3
    rec = SPN.find_seat(code, str(state_root(env)))
    if rec is None:
        out["why"] = "no-seat"
        return out, 2
    if not expected_target(rec, expected_session, expected_instance):
        out["why"] = "stale-session"
        return out, 2
    doing = None
    path = SPN.transcript_path(rec)
    if path:
        try:
            ev, _p, _q, _e, _s = SPN.events_from_jsonl(path, offset=max(0, os.path.getsize(path) - 512 * 1024))
            last = next((e for e in reversed(ev) if e["kind"] in ("tool_call", "assistant")), None)
            doing = {"kind": last["kind"], "text": last["text"][:120]} if last else None
        except OSError:
            doing = None
    reason = reason_for("stop_hard" if hard else "stop", code, (doing or {}).get("text") or "")
    out["touchid"] = {"prompt": reason, "wait_s": __import__("touchid_approve").WAIT_S}
    if window_only:
        if remote_left(code, env) <= 0:
            out["why"] = "locked"
            return out, 3
        out["via"], why = "unlock-window", None
    else:
        out["via"], why = prove(reason, env, touchid, touchid_available, platform)
    if why:
        out["why"] = why
        log_action(code, "stop", env, ok=False, why=why, hard=hard)
        return out, 3
    if not fresh_target(rec, code, env):
        out["why"] = "stale-session"
        return out, 2
    ok, why = send_keys(rec, ["C-c"] if hard else ["Escape"], tmux=tmux, run=run)
    out.update(ok=ok, why=(None if ok else why), hard=hard, doing=doing)
    log_action(code, "stop", env, ok=ok, why=why or None, hard=hard)
    return out, (0 if ok else 4)


# ── CLI ──

def main(argv=None, out=sys.stdout):
    ap = argparse.ArgumentParser(prog="aib seat")
    sub = ap.add_subparsers(dest="cmd")
    i = sub.add_parser("input")
    i.add_argument("code")
    i.add_argument("--text")
    i.add_argument("--file")
    i.add_argument("--unlock-min", type=int)
    i.add_argument("--queued", action="store_true", help=T("help_queued"))
    u = sub.add_parser("unlock")
    u.add_argument("code")
    u.add_argument("--min", type=int)
    u.add_argument("--grant", help="伺服器簽的解鎖單 JSON 檔;- = 從 stdin 讀")
    rm = sub.add_parser("remote")
    rm.add_argument("state", choices=("on", "off", "status", "link"))
    rm.add_argument("--once", action="store_true", help="link:只收一輪(測試 / 除錯)")
    rm.add_argument("--max-min", type=int)
    rm.add_argument("--allow", help="passkey,line,password 的子集")
    lk = sub.add_parser("lock")
    lk.add_argument("code")
    st = sub.add_parser("unlocked")
    st.add_argument("code")
    a = sub.add_parser("answer")
    a.add_argument("code")
    a.add_argument("choice")
    a.add_argument("--ask-id")
    a.add_argument("--text")
    s = sub.add_parser("stop")
    s.add_argument("code")
    s.add_argument("--hard", action="store_true")
    for p in (i, a, s):
        p.add_argument("--expected-session")
        p.add_argument("--expected-instance")
    for p in (i, a, s, u, lk, st, rm):
        p.add_argument("--json", action="store_true")
    ns = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    if ns.cmd == "input":
        text = ns.text
        if ns.file:
            try:
                text = Path(ns.file).read_text(encoding="utf-8")
            except OSError:
                out.write(json.dumps(_out(False, ns.code, "input", why="bad-file"), ensure_ascii=False) + "\n")
                return 2
        res, rc = do_input(ns.code, text, minutes=ns.unlock_min, queued=ns.queued, expected_session=ns.expected_session, expected_instance=ns.expected_instance)
    elif ns.cmd == "unlock":
        grant = None
        if ns.grant:
            try:
                raw = sys.stdin.read() if ns.grant == "-" else Path(ns.grant).read_text(encoding="utf-8")
                grant = json.loads(raw)
            except (OSError, ValueError):
                out.write(json.dumps(_out(False, ns.code, "unlock", why="bad-file"), ensure_ascii=False) + "\n")
                return 2
        res, rc = do_unlock(ns.code, ns.min, grant=grant)
    elif ns.cmd == "remote" and ns.state == "link":
        import remote_link
        res, rc = remote_link.main_link(once=ns.once, out=out)
    elif ns.cmd == "remote":
        res, rc = do_remote(ns.state, max_min=ns.max_min, allow=ns.allow.split(",") if ns.allow else None)
    elif ns.cmd == "lock":
        res, rc = do_lock(ns.code)
    elif ns.cmd == "unlocked":
        res, rc = do_unlocked(ns.code)
    elif ns.cmd == "answer":
        res, rc = do_answer(ns.code, ns.choice, ask_id=ns.ask_id, free_text=ns.text, expected_session=ns.expected_session, expected_instance=ns.expected_instance)
    elif ns.cmd == "stop":
        res, rc = do_stop(ns.code, hard=ns.hard, expected_session=ns.expected_session, expected_instance=ns.expected_instance)
    else:
        ap.print_usage(sys.stderr)
        return 2
    out.write(json.dumps(res, ensure_ascii=False) + "\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
