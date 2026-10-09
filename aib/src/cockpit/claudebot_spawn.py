#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""ClaudeBot 一鍵開席自帶對話(the maintainer 2026-10-07 03:1x 令「把 claudebot 整合進來」,選「一鍵開席自帶對話」)。

右鍵開 ClaudeBot 席時若沒選到任何對話,以前就開成「沒綁、信沒人收」(a seat 10-07 01:53)。
現在由 AIB 自己在背景開一條本機 claude 對話並綁上 —— 就是 root a seat 10-07 03:0x 手動做成功的那幾步:

  1. 先產生 session uuid U,在**專用** tmux 伺服器(`tmux -L claudebot`,不是 aihub-*)裡跑 `claude --session-id U`。
     ★ 不能開在 aihub-* 的格子裡:hook 認得那是 AIB 席位窗格,會一律放行(claudebot_hook.in_aib_seat_pane)。
     ★ 環境洗掉 TMUX 與所有 EZ2_*/AIB_* —— 那條對話不是席位,不能繼承開席者的身分。
  2. 回 U 給開席流程 ⇒ export AIB_CLAUDEBOT_SESSION_ID=U ⇒ 橋綁上它(建鎖、寫 agent_id)。
  3. 背景小程式(`kick`)等到橋真的綁上 U,才替使用者送第一句。
     ★ 為什麼要送:門鈴是在 Stop(Claude 每輪結束)時掛的;自動開的對話沒有人說話 ⇒ 沒有第一輪 ⇒ 門鈴沒掛 ⇒
       席位打字叫不醒它。第一輪跑完,門鈴就掛上,inbox 裡的人格包也會在那一輪交給它。
     ★ 為什麼要等綁上才送:先送的話那一輪的 Stop 查不到綁定(鎖還沒建),回覆不會到席上、門鈴也不會掛。

做不到的(照實):
  - 對話資料夾不受信任時 claude 會停在信任提示;kick 偵測到就不送字並留痕,開席照常,要人 attach 進去按一次。
  - 席位關掉時不收掉這條對話(那是使用者的對話,可能還要用);要收:tmux -L claudebot kill-session -t <名>。

紀錄:<state>/logs/claudebot-spawn.jsonl;對照表:<state>/claudebot-bindings/<U>.spawned(JSON:tmux 伺服器、session 名、cwd)。
測試用:AIB_CLAUDEBOT_SPAWN_TMUX 指定 tmux 執行檔、AIB_CLAUDEBOT_SPAWN_CLAUDE 指定 claude 執行檔。
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
TMUX_SERVER = "claudebot"
SESSION_PREFIX = "cb-"
ENV_TMUX = "AIB_CLAUDEBOT_SPAWN_TMUX"
ENV_CLAUDE = "AIB_CLAUDEBOT_SPAWN_CLAUDE"
ENV_OFF = "AIB_CLAUDEBOT_AUTOSPAWN"          # =0 關掉(回到舊行為:沒選就不綁)
ENV_PERMISSION = "AIB_CLAUDEBOT_PERMISSION_MODE"
DEFAULT_PERMISSION = "manual"
PERMISSION_MODES = ("manual", "acceptEdits", "auto", "plan", "dontAsk", "bypassPermissions")
READY_WAIT_S = 20.0
KICK_WAIT_S = 120.0
LOG_NAME = "claudebot-spawn.jsonl"
SCRUB_PREFIXES = ("EZ2_", "AIB_")
SCRUB_EXACT = ("TMUX", "TMUX_PANE")


def _state_root() -> Path:
    for key in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        v = os.environ.get(key)
        if v:
            return Path(v).expanduser()
    return Path.home() / ".ez2-aib"


def log(action: str, **extra) -> None:
    try:
        d = _state_root() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        row = {"ts": round(time.time(), 3), "action": action}
        row.update(extra)
        with (d / LOG_NAME).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def tmux_bin() -> str:
    return (os.environ.get(ENV_TMUX) or "").strip() or os.environ.get("AIB_TMUX_BIN") or shutil.which("tmux") or "tmux"


def claude_bin() -> str:
    return (os.environ.get(ENV_CLAUDE) or "").strip() or shutil.which("claude") or ""


def session_name(sid: str) -> str:
    return SESSION_PREFIX + sid[:8]


def attach_command(sid: str) -> str:
    return "tmux -L %s attach -t %s" % (TMUX_SERVER, session_name(sid))


def clean_env(base: dict | None = None) -> dict:
    """那條對話不是席位:洗掉 TMUX 與所有 EZ2_*/AIB_*,只留一般使用者環境。"""
    env = dict(os.environ if base is None else base)
    for k in list(env):
        if k in SCRUB_EXACT or k.startswith(SCRUB_PREFIXES):
            env.pop(k, None)
    return env


def _record_path(sid: str) -> Path:
    return _state_root() / "claudebot-bindings" / (sid + ".spawned")


def record_of(sid: str) -> dict | None:
    try:
        d = json.loads(_record_path(sid).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _capture(tmux: str, name: str) -> str:
    try:
        r = subprocess.run([tmux, "-L", TMUX_SERVER, "capture-pane", "-p", "-t", name],
                           capture_output=True, text=True, timeout=5, env=clean_env(), stdin=subprocess.DEVNULL)
        return r.stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def pane_state(text: str) -> str:
    """claude 的畫面現在停在哪:ready(輸入框)/ trust(信任提示)/ ""(還沒起來)。"""
    low = text.lower()
    if "trust" in low and ("folder" in low or "files" in low):
        return "trust"
    if "❯" in text:
        return "ready"
    return ""


def start(cwd: str, wait_s: float = READY_WAIT_S) -> tuple[str, str]:
    """開一條對話。回 (session uuid, 錯誤句);失敗時 uuid 是空字串。"""
    claude = claude_bin()
    if not claude:
        return "", "找不到 claude 指令(PATH 裡沒有),沒辦法自動開對話"
    tmux = tmux_bin()
    sid = str(uuid.uuid4())
    name = session_name(sid)
    cwd = cwd if cwd and os.path.isdir(cwd) else str(Path.home())
    # 10-07 the maintainer 令:代開的對話預設「動手前先問」(manual)。它收的信可能來自別席,文字規則只是第一層,真正的防線是權限模式
    #   (docs/CLAUDEBOT-DESKTOP.md §3.5)。等批准時席上狀態列會亮「⚠ 在等你批准」(claudebot_live 讀 tmux 畫面)。
    #   AIB_CLAUDEBOT_PERMISSION_MODE 可改(acceptEdits / auto / plan / dontAsk / bypassPermissions)。
    mode = (os.environ.get(ENV_PERMISSION) or DEFAULT_PERMISSION).strip()
    if mode not in PERMISSION_MODES:
        mode = DEFAULT_PERMISSION
    cmd = "exec %s --session-id %s --permission-mode %s" % (shlex.quote(claude), sid, mode)
    try:
        r = subprocess.run([tmux, "-L", TMUX_SERVER, "new-session", "-d", "-s", name, "-c", cwd, "-x", "160", "-y", "45", cmd],
                           capture_output=True, text=True, timeout=15, env=clean_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", "起不了 tmux(%s)" % type(exc).__name__
    if r.returncode != 0:
        return "", "tmux 開不起來 rc=%d:%s" % (r.returncode, (r.stderr or "").strip()[:160])
    rec = {"session_id": sid, "tmux_server": TMUX_SERVER, "tmux_session": name, "cwd": cwd,
           "started_epoch": int(time.time())}
    try:
        p = _record_path(sid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass
    deadline = time.time() + max(0.0, wait_s)
    state = ""
    while time.time() < deadline:
        state = pane_state(_capture(tmux, name))
        if state:
            break
        time.sleep(0.5)
    log("started", session=sid[:8], tmux_session=name, cwd=cwd, pane=state or "unknown")
    return sid, ""


def bound_code(sid: str) -> str:
    """哪一隻活 claudebot 席綁著 sid(橋的 status.json agent_id);沒有回空字串。"""
    for st in sorted((_state_root() / "bridge").glob("*/status.json")):
        try:
            d = json.loads(st.read_text(encoding="utf-8"))
            if d.get("provider") != "claudebot" or d.get("agent_id") != sid or d.get("state") == "stopped":
                continue
            os.kill(int(d.get("pid")), 0)
        except (OSError, ValueError, TypeError):
            continue
        return str(d.get("code") or "")
    return ""


def first_prompt(code: str) -> str:  # 開頭的 [AIB] 讓 hook 把它記成「AIB 系統」(claudebot_hook.on_prompt)
    # 10-07 真機:舊版寫「信箱裡若有系統信先照它做」⇒ Claude 自己跑 aib dm-queue 去查,在 manual 權限模式一開場就卡在批准。
    #   信本來就由 hook 附在這一則/下一則裡(UserPromptSubmit 附上待收的信),不用它去查 ⇒ 明說不用執行任何指令。
    return ("[AIB] 這條對話已接到 AIB 席 @%s(ClaudeBot)。席上打的字與 AIB 系統信(含人格包)會由 hook 自動附在訊息裡交給你,"
            "你不用自己去查、這一則不用執行任何指令。讀完附上的內容,只回「就緒」。" % code)


def kick(sid: str, wait_s: float = KICK_WAIT_S, poll_s: float = 1.0) -> str:
    """等橋綁上 sid,再替使用者送第一句。回 sent / timeout / trust / no-record / send-failed。"""
    rec = record_of(sid)
    if not rec:
        log("kick", session=sid[:8], result="no-record")
        return "no-record"
    tmux, name = tmux_bin(), str(rec.get("tmux_session") or session_name(sid))
    deadline = time.time() + max(0.0, wait_s)
    code = ""
    while time.time() < deadline:
        code = bound_code(sid)
        if code and pane_state(_capture(tmux, name)) == "ready":
            break
        time.sleep(poll_s)
    else:
        result = "trust" if pane_state(_capture(tmux, name)) == "trust" else "timeout"
        log("kick", session=sid[:8], result=result)
        return result
    try:
        a = subprocess.run([tmux, "-L", TMUX_SERVER, "send-keys", "-t", name, "-l", first_prompt(code)],
                           capture_output=True, timeout=5, env=clean_env(), stdin=subprocess.DEVNULL)
        time.sleep(0.4)
        b = subprocess.run([tmux, "-L", TMUX_SERVER, "send-keys", "-t", name, "Enter"],
                           capture_output=True, timeout=5, env=clean_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        log("kick", session=sid[:8], result="send-failed")
        return "send-failed"
    result = "sent" if a.returncode == 0 and b.returncode == 0 else "send-failed"
    log("kick", session=sid[:8], result=result, seat=code)
    return result


def spawn_kicker(sid: str) -> None:
    """背景跑 kick(脫離開席流程;開席不等它)。"""
    try:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "kick", sid],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, env=dict(os.environ))
    except OSError as exc:
        log("kick", session=sid[:8], result="spawn-failed", err=type(exc).__name__)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="claudebot_spawn.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("start", help="開一條對話,stdout 印 session uuid(人話走 stderr),並在背景等綁定後送第一句")
    st.add_argument("--cwd", default=os.getcwd())
    st.add_argument("--no-kick", action="store_true")
    kk = sub.add_parser("kick")
    kk.add_argument("session_id")
    at = sub.add_parser("attach-cmd")
    at.add_argument("session_id")
    args = ap.parse_args(argv)
    if args.cmd == "start":
        sid, err = start(args.cwd)
        if not sid:
            print("aib: claudebot 自動開對話失敗:%s" % err, file=sys.stderr)
            return 1
        if not args.no_kick:
            spawn_kicker(sid)
        print(sid)
        return 0
    if args.cmd == "kick":
        return 0 if kick(args.session_id) == "sent" else 1
    print(attach_command(args.session_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
