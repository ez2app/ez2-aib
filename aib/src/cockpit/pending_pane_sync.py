#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""開著的艙,不用重開就長出 / 收起待審框(收費外掛 pending-review)。

10-25:Pro 先開艙、後裝 pending-review ⇒ 待審框沒長出來。外掛的 --ensure 只在開艙(cockpit-full.sh)跑一次,
開艙後才裝外掛 / 才開 pending_panel 開關就沒人切格。會員不能叫他重開艙,所以:
- aib plugins install 裝好 pending-review ⇒ 每個開著的艙跑一次轉交殼 --ensure
- feature_policy set pending_panel on / off ⇒ 這一艙跑 --ensure / --hide
一律走核心轉交殼 src/cockpit/pane-pending-review.sh(它先載 cockpit-env.sh,外掛那份直接跑會找不到環境);
只帶 EZ2_AIB_INSTANCE + EZ2_AIB_INSTANCE_HOME,session / socket / 狀態夾讓 cockpit-env.sh 自己推導(跟開艙同一套)。
失敗一律不擋(裝外掛 / 改開關照樣算成功),結果回給呼叫端印一行。
"""
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHELL = HERE / "pane-pending-review.sh"
PLUGIN = "pending-review"
FEATURE = "pending_panel"
TIMEOUT_S = 20
# 推導值一律讓 cockpit-env.sh 從艙號算,不吃呼叫端(可能是別艙的席位視窗)身上的
_DERIVED = ("TMUX", "TMUX_PANE", "EZ2_TMUX_SESSION", "EZ2_AIHUB_SOCKET", "EZ2_COCKPIT_STATE_DIR")


def _state_root(env):
    import seats_dir_lib
    return Path(os.path.expanduser(seats_dir_lib.state_root(env)[0]))


def _session_live(inst, env, run):
    e = {k: v for k, v in env.items() if k not in ("TMUX", "TMUX_PANE")}
    try:
        r = run(["tmux", "has-session", "-t", "aib-" + inst], env=e, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def live_cabins(env=None, run=subprocess.run):
    """開著的艙 ⇒ [(艙號, 艙家)]。開著 = 外層 tmux 有 aib-<艙號> 這個 session。"""
    env = dict(os.environ if env is None else env)
    inst_dir = _state_root(env) / "instances"
    try:
        homes = sorted(p for p in inst_dir.iterdir() if p.is_dir())
    except OSError:
        return []
    return [(p.name, str(p)) for p in homes if _session_live(p.name, env, run)]


def this_cabin(env=None):
    """呼叫端所在的艙 ⇒ [(艙號, 艙家)] 或 [](沒艙家)。"""
    env = os.environ if env is None else env
    home = (env.get("EZ2_AIB_INSTANCE_HOME") or "").strip()
    if not home:
        return []
    home = os.path.expanduser(home).rstrip("/")
    return [((env.get("EZ2_AIB_INSTANCE") or "").strip() or os.path.basename(home), home)]


def sync(action, cabins, env=None, run=subprocess.run):
    """action ∈ ensure / hide;cabins = [(艙號, 艙家)] ⇒ [{cabin, ok, rc}]。不丟例外。"""
    assert action in ("ensure", "hide"), action
    base = {k: v for k, v in dict(os.environ if env is None else env).items() if k not in _DERIVED}
    out = []
    for inst, home in cabins:
        e = dict(base, EZ2_AIB_INSTANCE=inst, EZ2_AIB_INSTANCE_HOME=home)
        try:
            r = run(["bash", str(SHELL), "--" + action], env=e, capture_output=True, text=True,
                    stdin=subprocess.DEVNULL, timeout=TIMEOUT_S)
            rc = r.returncode
        except (OSError, subprocess.SubprocessError):
            rc = -1
        out.append({"cabin": inst, "ok": rc == 0, "rc": rc})
    return out


def after_install(name, env=None, run=subprocess.run):
    """aib plugins install 裝好之後叫;不是 pending-review ⇒ 什麼都不做。"""
    if name != PLUGIN:
        return []
    return sync("ensure", live_cabins(env, run), env, run)


def after_switch(feature, on, env=None, run=subprocess.run):
    """feature_policy set 之後叫;只管 pending_panel、只管這一艙(開著才動)。"""
    if feature != FEATURE:
        return []
    env = dict(os.environ if env is None else env)
    cabins = [c for c in this_cabin(env) if _session_live(c[0], env, run)]
    return sync("ensure" if on else "hide", cabins, env, run)
