#!/usr/bin/env python3
# aib-kind: mod
"""aib-seat-hooks 的鉤子(設計稿 §25):給 Codex / Antigravity 這種裝不了 Claude 畫面外掛的席,在開席與每回合送出問題時
把席位身份寫進上下文。外掛會被複製到各家快取,不能用相對路徑找 repo ⇒ 先看 EZ2_AIB_ROOT,再從 PATH 上的 aib 反查。
argv[1] = 事件名;Claude / Codex 格式:SessionStart / UserPromptSubmit,stdin 帶 session_id,回 hookSpecificOutput.additionalContext;
Antigravity 格式(argv 帶 --agy):SessionStart / PreInvocation,stdin 帶 conversationId,回 injectSteps(開席 systemMessage 留在對話裡,每次呼叫模型前 ephemeralMessage 只留這一回合)。
任何失敗都安靜結束 rc 0(鉤子不能擋住對話);Claude Code 裡不做事(aib-boards 已經寫了身份,不寫兩份)。"""
import json
import os
import shutil
import subprocess
import sys


def _ok(r):
    return bool(r) and os.path.isfile(os.path.join(r, "tools", "claude-mods", "aib-boards", "tui", "identity.mjs"))


def repo_root():
    r = os.environ.get("EZ2_AIB_ROOT")
    if _ok(r):
        return r
    # 試用期(還沒上主線)用狀態根 config/aib-seat-hooks.json 的 repo 指到工作樹;上主線後刪掉這個檔
    home = os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib")
    try:
        with open(os.path.join(home, "config", "aib-seat-hooks.json"), encoding="utf-8") as f:
            r = json.load(f).get("repo")
        if _ok(r):
            return r
    except (OSError, ValueError, AttributeError):
        pass
    a = shutil.which("aib")
    if a:
        p = os.path.realpath(a)                       # .../src/cockpit/aib
        r = os.path.dirname(os.path.dirname(os.path.dirname(p)))
        if _ok(r):
            return r
    return None


def note(engine, event, what):
    """每次執行記一行(狀態根 logs/aib-seat-hooks.log):鉤子有沒有被叫到、量到沒,只看各家的對話看不出來。寫不了就算了。"""
    try:
        import time
        home = os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib")
        os.makedirs(os.path.join(home, "logs"), exist_ok=True)
        with open(os.path.join(home, "logs", "aib-seat-hooks.log"), "a", encoding="utf-8") as f:
            f.write("%s %s %s %s\n" % (time.strftime("%m-%d %H:%M:%S"), engine, event, what))
    except OSError:
        pass


def main():
    args = [a for a in sys.argv[1:] if a != "--agy"]
    agy = "--agy" in sys.argv[1:]
    event = args[0] if args else ("PreInvocation" if agy else "UserPromptSubmit")
    if os.environ.get("CLAUDECODE"):
        return 0
    try:
        ev = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        ev = {}
    engine = "agy" if agy else "codex"
    root = repo_root()
    if not root:
        note(engine, event, "no-repo")
        return 0
    tui = os.path.join(root, "tools", "claude-mods", "aib-boards", "tui")
    node = shutil.which("node") or "/opt/homebrew/bin/node"
    try:
        r = subprocess.run([node, "--no-warnings", "--import", os.path.join(tui, "reg.mjs"), os.path.join(tui, "identity.mjs"),
                            str(ev.get("conversationId") if agy else ev.get("session_id") or "")], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.TimeoutExpired) as e:
        note(engine, event, "fail:%s" % type(e).__name__)
        return 0
    text = r.stdout.strip()
    if r.returncode != 0 or not text:
        note(engine, event, "fail:rc=%s" % r.returncode)
        return 0
    note(engine, event, "ok:%d" % len(text))
    if agy:
        step = {"systemMessage": {"systemMessage": text}} if event == "SessionStart" else {"ephemeralMessage": text}
        print(json.dumps({"injectSteps": [step]}, ensure_ascii=False))
    else:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
