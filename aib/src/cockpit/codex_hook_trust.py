#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Codex 新掛點自動信任(the maintainer 10-14 02:3x 直令,a seat 派工)。

Codex 的 hooks.json 掛點要在 /hooks 信任過才會跑;信任記在 <CODEX_HOME>/config.toml:
  [hooks.state."<hooks.json 絕對路徑>:<事件蛇形>:<組序>:<handler 序>"]
  trusted_hash = "sha256:…"
算法照 openai/codex(hooks/src/engine/discovery.rs hook_hash + config/src/fingerprint.rs version_for_toml):
每個 handler 單獨算 {event_name, matcher(有才放), hooks:[正規化後的 handler]} ⇒ 鍵排序、無空白 JSON ⇒ sha256。
10-14 在 Mini 上對現有 9 筆實算,9/9 跟 Codex 自己寫的相同。

只處理 AIB 自己裝的掛點(指令裡有 OURS 其中一個檔名),別的不碰;不用 --dangerously-bypass-hook-trust(那是全開)。
沒被信任 ⇒ Mac 彈窗列出事件與指令 [同意][不要],30 秒沒按 = 同意;同意才寫(先備份 config.toml),
不要 ⇒ 不寫、記一筆、下次開席再問;跳不出視窗 ⇒ 不寫(沒有人看過就不替人同意)。AIB_CODEX_HOOK_TRUST=0 關。

用法:codex_hook_trust.py ensure [--codex-home DIR] [--yes] [--json]
      codex_hook_trust.py status [--codex-home DIR] [--json]
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
for _p in (HERE, SRC / "identity"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

OURS = ("menu_pick.py", "auto_distill_hook.py")
POPUP_TIMEOUT_S = 30

EVENT_KEY = {
    "PreToolUse": "pre_tool_use", "PermissionRequest": "permission_request", "PostToolUse": "post_tool_use",
    "PreCompact": "pre_compact", "PostCompact": "post_compact", "SessionStart": "session_start",
    "SessionEnd": "session_end", "UserPromptSubmit": "user_prompt_submit", "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop", "Stop": "stop", "Interrupt": "interrupt",
}
SESSION_END_DEFAULT_TIMEOUT_S, SESSION_END_MAX_TIMEOUT_S = 1, 3   # codex hooks/src/events/session_end.rs
NO_MATCHER = ("Stop", "UserPromptSubmit", "Interrupt")             # events/common.rs matcher_pattern_for_event
CONTEXT_LIMIT_EVENTS = ("PreToolUse", "PostToolUse", "SessionStart", "UserPromptSubmit", "SubagentStart")
DEFAULT_CONTEXT_LIMIT = 2500                                       # output_spill.rs DEFAULT_HOOK_OUTPUT_TOKEN_LIMIT


def _say(key, params=None):
    """使用者看得到的字走 i18n(這裡沒有真人訊息可看語言 ⇒ 用這台的語系設定);i18n 壞了退回鍵名。"""
    try:
        import i18n_lib
        return i18n_lib.t(key, params or {})
    except Exception:  # noqa: BLE001
        return key


def codex_home(arg=None):
    """Codex 只在有設 CODEX_HOME 時轉成實際路徑(utils/home-dir canonicalize);沒設就用 $HOME/.codex 原樣
    ⇒ 鍵裡的路徑要跟它一樣(審查第 1 輪 3)。"""
    raw = arg or os.environ.get("CODEX_HOME")
    return Path(os.path.realpath(raw)) if raw else Path(os.path.expanduser("~/.codex"))


def _timeout(event, value):
    if event in ("SessionEnd", "Interrupt"):
        v = SESSION_END_DEFAULT_TIMEOUT_S if value is None else value
        return min(max(int(v), 1), SESSION_END_MAX_TIMEOUT_S)
    return max(int(600 if value is None else value), 1)


def hook_hash(event, matcher, handler):
    """一個 command handler 的信任碼(跟 Codex 算的一樣)。不是 command 型 ⇒ None(AIB 只裝 command)。"""
    if not isinstance(handler, dict) or handler.get("type") != "command" or event not in EVENT_KEY:
        return None
    if not str(handler.get("command", "")).strip():
        return None                                   # Codex 直接略過空指令,不用信任
    # 照 discovery.rs 正規化:commandWindows 一律拿掉;additionalContextLimit 只有五種事件留、等於預設也拿掉;
    # Stop / UserPromptSubmit / Interrupt 的 matcher 一律當沒有(審查第 1 輪 2)
    norm = {"type": "command", "command": str(handler.get("command", "")),
            "async": bool(handler.get("async", False)), "timeout": _timeout(event, handler.get("timeout"))}
    if handler.get("statusMessage") is not None:
        norm["statusMessage"] = handler["statusMessage"]
    limit = handler.get("additionalContextLimit")
    if event in CONTEXT_LIMIT_EVENTS and limit is not None and limit != DEFAULT_CONTEXT_LIMIT:
        norm["additionalContextLimit"] = limit
    ident = {"event_name": EVENT_KEY[event], "hooks": [norm]}
    if matcher is not None and event not in NO_MATCHER:
        ident["matcher"] = matcher
    blob = json.dumps(ident, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def is_ours(handler):
    cmd = str((handler or {}).get("command", "")) if isinstance(handler, dict) else ""
    return any(name in cmd for name in OURS)


def our_hooks(home):
    """hooks.json 裡 AIB 自己裝的 handler ⇒ [{key, hash, event, command}]。讀不到 ⇒ []。"""
    p = home / "hooks.json"
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    hooks = cfg.get("hooks") if isinstance(cfg, dict) else None
    out = []
    for event, groups in (hooks or {}).items() if isinstance(hooks, dict) else ():
        if event not in EVENT_KEY or not isinstance(groups, list):
            continue
        for gi, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            for hi, handler in enumerate(group.get("hooks") or []):
                if not is_ours(handler):
                    continue
                h = hook_hash(event, group.get("matcher"), handler)
                if h:
                    out.append({"key": "%s:%s:%d:%d" % (p, EVENT_KEY[event], gi, hi), "hash": h,
                                "event": event, "command": handler.get("command", "")})
    return out


def trusted(home):
    """config.toml 裡每個鍵的 trusted_hash。檔不在 ⇒ {};讀壞了 ⇒ None(不碰)。"""
    p = home / "config.toml"
    if not p.exists():
        return {}
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    state = ((data.get("hooks") or {}).get("state") or {})
    return {k: (v or {}).get("trusted_hash") for k, v in state.items() if isinstance(v, dict)}


def untrusted(home):
    have = trusted(home)
    if have is None:
        return None
    return [h for h in our_hooks(home) if have.get(h["key"]) != h["hash"]]


def _toml_key(key):
    return json.dumps(key, ensure_ascii=False)       # TOML 基本字串跟 JSON 字串的跳脫相容


def _set_hashes(text, items):
    """在 config.toml 文字裡設好每一筆 trusted_hash;同一節的其他鍵(enabled…)照留。回新文字。"""
    lines = text.splitlines()
    for it in items:
        header = "[hooks.state.%s]" % _toml_key(it["key"])
        line = "trusted_hash = %s" % _toml_key(it["hash"])
        try:
            start = next(i for i, l in enumerate(lines) if l.strip() == header)
        except StopIteration:
            if lines and lines[-1].strip():
                lines.append("")
            lines += [header, line]
            continue
        end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        hit = next((i for i in range(start + 1, end) if lines[i].split("=", 1)[0].strip() == "trusted_hash"), None)
        if hit is None:
            lines.insert(start + 1, line)
        else:
            lines[hit] = line
    return "\n".join(lines) + "\n"


def can_write(home, items):
    """先在記憶體裡改一次、用 tomllib 驗:改得出正確結果才跳窗(寫法認不得 ⇒ 不問、不留備份;審查第 1 輪 4)。"""
    p = home / "config.toml"
    try:
        old = p.read_text(encoding="utf-8") if p.exists() else ""
        data = tomllib.loads(_set_hashes(old, items))
    except (OSError, ValueError):
        return False
    state = (data.get("hooks") or {}).get("state") or {}
    return all((state.get(it["key"]) or {}).get("trusted_hash") == it["hash"] for it in items)


def write_trust(home, items, now=None):
    """先備份再寫;權限照原檔(config.toml 常是 600)、是連結就寫到它指的檔;寫完 tomllib 重讀驗,不對就還原。
    回 (ok, 備份路徑或原因)。"""
    link = home / "config.toml"
    p = Path(os.path.realpath(link))
    old = p.read_text(encoding="utf-8") if p.exists() else ""
    bak = None
    if p.exists():                                     # 備份放 CODEX_HOME(不跟著連結進 dotfiles 倉;審查第 2 輪)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        bak = Path("%s.bak-aib-hook-trust-%s-%d" % (link, stamp, os.getpid()))
        n = 1
        while bak.exists():
            bak, n = Path("%s.bak-aib-hook-trust-%s-%d-%d" % (link, stamp, os.getpid(), n)), n + 1
        shutil.copy2(p, bak)
    new = _set_hashes(old, items)
    tmp = p.with_name(p.name + ".aib-tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(new)
    if p.exists():
        shutil.copymode(p, tmp)
    os.replace(tmp, p)
    have = trusted(home)
    if have is None or any(have.get(it["key"]) != it["hash"] for it in items):
        if bak:
            shutil.copy2(bak, p)
        else:
            p.unlink(missing_ok=True)
        return False, "verify"
    return True, str(bak or "")


def _log(rec):
    root = Path(os.environ.get("EZ2_AIB_STATE_ROOT") or os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib"))
    try:
        d = root / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "codex-hook-trust.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(rec, ts=time.time()), ensure_ascii=False) + "\n")
    except OSError:
        pass


def ask(items, runner=None):
    """彈窗問一次。回 agree / refuse / no_popup。30 秒沒按 = 同意(the maintainer 10-14)。"""
    import bindme
    lines = "\n".join("• %s: %s" % (it["event"], it["command"][:160]) for it in items)
    agree, refuse = _say("codex_trust.agree"), _say("codex_trust.refuse")
    btn, how = bindme.mac_dialog(_say("codex_trust.title"), _say("codex_trust.body", {"items": lines}),
                                 [refuse, agree], agree, refuse, timeout=POPUP_TIMEOUT_S, runner=runner)
    if how == "no_menu" or btn is None:
        return "no_popup"
    return "agree" if btn == agree else "refuse"


def ensure(home=None, yes=False, runner=None):
    """裝完新掛點後叫。回 {result, n, backup?}:result = off / unreadable / none / unsupported / trusted / refused / no_popup / failed。"""
    if os.environ.get("AIB_CODEX_HOOK_TRUST") == "0":
        return {"result": "off", "n": 0}
    home = codex_home(home)
    todo = untrusted(home)
    if todo is None:
        return {"result": "unreadable", "n": 0}
    if not todo:
        return {"result": "none", "n": 0}
    if not can_write(home, todo):
        _log({"home": str(home), "keys": [t["key"] for t in todo], "answer": "unsupported"})
        return {"result": "unsupported", "n": len(todo)}
    answer = "agree" if yes else ask(todo, runner)
    rec = {"home": str(home), "keys": [t["key"] for t in todo], "answer": answer}
    if answer != "agree":
        _log(rec)
        return {"result": "refused" if answer == "refuse" else "no_popup", "n": len(todo)}
    ok, info = write_trust(home, todo)
    _log(dict(rec, ok=ok, backup=info))
    return {"result": "trusted" if ok else "failed", "n": len(todo), "backup": info}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="codex-hook-trust")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("ensure", "status"):
        sp = sub.add_parser(name)
        sp.add_argument("--codex-home")
        sp.add_argument("--json", action="store_true")
        if name == "ensure":
            sp.add_argument("--yes", action="store_true", help="不跳窗直接信任(測試 / 真人已在終端機同意)")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        home = codex_home(a.codex_home)
        todo = untrusted(home)
        res = {"result": "unreadable" if todo is None else ("none" if not todo else "untrusted"),
               "n": len(todo or []), "keys": [t["key"] for t in todo or []]}
    else:
        res = ensure(a.codex_home, yes=a.yes)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    elif res["result"] not in ("none", "off"):
        print(_say("codex_trust.result.%s" % res["result"], {"n": res["n"]}))
    return 0 if res["result"] in ("none", "off", "trusted", "untrusted") else 1


if __name__ == "__main__":
    sys.exit(main())
