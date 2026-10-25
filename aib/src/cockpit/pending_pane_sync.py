#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""開著的艙,不用重開就長出 / 收起待審框(收費外掛 pending-review)。

10-25:Pro 先開艙、後裝 pending-review ⇒ 待審框沒長出來。外掛的 --ensure 只在開艙(cockpit-full.sh)跑一次,
開艙後才裝外掛 / 才開 pending_panel 開關就沒人切格。會員不能叫他重開艙,所以:
- aib plugins install 裝好 pending-review ⇒ 每個開著的艙跑一次轉交殼 --ensure
- feature_policy set pending_panel on / off ⇒ 這一艙跑 --ensure / --hide
一律走核心轉交殼 src/cockpit/pane-pending-review.sh(它先載 cockpit-env.sh,外掛那份直接跑會找不到環境);
只帶 EZ2_AIB_INSTANCE + EZ2_AIB_INSTANCE_HOME;呼叫端身上的艙變數(cabin_resolve.sh --env-names-scoped 正本清單)
一律洗掉,session / socket / 狀態夾 / 權杖讓 cockpit-env.sh 自己推導(跟開艙同一套)。
失敗一律不擋(裝外掛 / 改開關照樣算成功);結果放回條 live_cabins 欄,不另印。
一艙一艙同步跑:每艙最多 has-session 5 秒 + 轉交殼 20 秒。
"""
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHELL = HERE / "pane-pending-review.sh"
PLUGIN = "pending-review"
FEATURE = "pending_panel"
TIMEOUT_S = 20
PRIMARY = "main"   # 主艙(cabin_resolve.sh AIB_CABIN_PRIMARY):艙家 = 狀態根
# 推導值一律讓 cockpit-env.sh 從艙號算,不吃呼叫端(可能是別艙的席位視窗)身上的;正本清單讀不到時用這份保底
_WASH_FALLBACK = ("EZ2_AIB_INSTANCE", "EZ2_TMUX_SESSION", "EZ2_AIHUB_SOCKET", "EZ2_AIB_INSTANCE_HOME", "EZ2_AIB_CABIN_NAMED",
                  "EZ2_AIB_INSTANCE_TOKEN", "EZ2_COCKPIT_STATE_DIR", "EZ2_TASKS_DIR", "EZ2_AIB_PANES", "EZ2_SEATS_DIR",
                  "AIB_DM_SOCKET", "AIB_DM_TMUX")


def wash_names(run=subprocess.run):
    """要洗掉的艙變數:cabin_resolve.sh --env-names-scoped(正本)+ TMUX / TMUX_PANE。"""
    names = list(_WASH_FALLBACK)
    try:
        r = run(["bash", str(HERE / "cabin_resolve.sh"), "--env-names-scoped"], capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=5)
        got = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()] if r.returncode == 0 else []
        if got:
            names = got
    except (OSError, subprocess.SubprocessError):
        pass
    return set(names) | {"TMUX", "TMUX_PANE"}


def is_cabin_id(name):
    """新艙號格式 YYYYMMDD-HHMMSS-xxxx(cabin_resolve.sh aib_cabin_is_new_id:恰好兩個「-」)。"""
    return name.count("-") == 2 and all(name.split("-"))


def _state_root(env):
    import seats_dir_lib
    return Path(os.path.expanduser(seats_dir_lib.state_root(env)[0]))


def _session_live(inst, env, run):
    e = {k: v for k, v in env.items() if k not in ("TMUX", "TMUX_PANE")}
    try:
        r = run(["tmux", "has-session", "-t", "=aib-" + inst], env=e,   # 「=」完全比對(-t 預設前綴比對,aib-2 會解到 aib-2026…) capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def live_cabins(env=None, run=subprocess.run):
    """開著的艙 ⇒ [(艙號, 艙家)]。開著 = 外層 tmux 有 aib-<艙號> 這個 session(完全比對)。
    主艙(main,艙家 = 狀態根)+ instances/ 底下新艙號格式的夾;短名舊夾(早期流水號 / main / 2)不收。"""
    env = dict(os.environ if env is None else env)
    root = _state_root(env)
    try:
        homes = sorted(p for p in (root / "instances").iterdir() if p.is_dir() and is_cabin_id(p.name))
    except OSError:
        homes = []
    cabins = [(PRIMARY, str(root))] + [(p.name, str(p)) for p in homes]
    return [c for c in cabins if _session_live(c[0], env, run)]


def this_cabin(env=None):
    """呼叫端所在的艙 ⇒ [(艙號, 艙家)] 或 [](沒艙家)。"""
    env = os.environ if env is None else env
    home = (env.get("EZ2_AIB_INSTANCE_HOME") or "").strip()
    if not home:
        return []
    home = os.path.expanduser(home).rstrip("/")
    inst = (env.get("EZ2_AIB_INSTANCE") or "").strip() or os.path.basename(home)
    if inst != PRIMARY and not is_cabin_id(inst):
        return []
    return [(inst, home)]


def sync(action, cabins, env=None, run=subprocess.run):
    """action ∈ ensure / hide;cabins = [(艙號, 艙家)] ⇒ [{cabin, ok, rc}]。不丟例外。"""
    assert action in ("ensure", "hide"), action
    wash = wash_names(run)
    base = {k: v for k, v in dict(os.environ if env is None else env).items() if k not in wash}
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
