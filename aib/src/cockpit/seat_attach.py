#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib seat attach <席碼> [--json](對話面板 P5「開終端機」鈕;SPEC-chat-panel-formats v0.6):
用 Terminal 開一個新視窗附到那一席的 tmux(attach -t aihub 再 select-window 到那一席的窗)。只看不打字 ⇒ 不經 Touch ID;
只在本人點頁面鈕時叫(AI 席位呼叫一樣擋:它自己就在 tmux 裡,不需要)。外掛不碰 tmux socket。
只在 Mac(Terminal.app);其他平台回 {ok: false, why: "no-terminal"} 並印出可以自己貼的 tmux 指令。
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import seat_act as SA  # noqa: E402
import seat_panel as SPN  # noqa: E402

OSASCRIPT = "/usr/bin/osascript"


def attach_command(rec):
    sock, wid = SA._target(rec)
    if not sock:
        return None
    return "tmux -L %s attach -t aihub \\; select-window -t %s" % (shlex.quote(sock), shlex.quote(wid))


def open_terminal(cmd, run=subprocess.run, osascript=OSASCRIPT):
    """Terminal.app 開新視窗跑 cmd。回 (ok, why)。cmd 只由本檔組(socket / 窗號都驗過字元),再經 JSON 跳脫放進 JXA。"""
    if not os.path.exists(osascript):
        return False, "no-terminal"
    jxa = 'var T = Application("Terminal"); T.activate(); T.doScript(%s); JSON.stringify({ok: true});' % json.dumps(cmd)
    try:
        r = run([osascript, "-l", "JavaScript", "-e", jxa], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "osascript_failed:%s" % exc.__class__.__name__
    return (r.returncode == 0), ("" if r.returncode == 0 else "osascript_rc:%d" % r.returncode)


def do_attach(code, env=None, platform=None, run=subprocess.run, osascript=OSASCRIPT):
    env = dict(os.environ if env is None else env)
    platform = sys.platform if platform is None else platform
    out = {"v": 1, "ok": False, "seat": code, "action": "attach", "why": None, "command": None}
    if not isinstance(code, str) or not re.match(r"^[a-z0-9]{5}$", code):
        out["why"] = "no-seat"
        return out, 2
    if SA.caller_is_seat(env):
        out["why"] = "caller-is-seat"
        return out, 3
    rec = SPN.find_seat(code, str(SA.state_root(env)))
    if rec is None:
        out["why"] = "no-seat"
        return out, 2
    cmd = attach_command(rec)
    if not cmd:
        out["why"] = "no-pane"
        return out, 4
    out["command"] = cmd
    if platform != "darwin":
        out["why"] = "no-terminal"
        return out, 4
    ok, why = open_terminal(cmd, run=run, osascript=osascript)
    out["ok"], out["why"] = ok, (None if ok else why)
    SA.log_action(code, "attach", env, ok=ok, why=why or None)
    return out, (0 if ok else 4)


def main(argv=None, out=sys.stdout):
    ap = argparse.ArgumentParser(prog="aib seat attach")
    ap.add_argument("code")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    res, rc = do_attach(a.code)
    out.write(json.dumps(res, ensure_ascii=False) + "\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
