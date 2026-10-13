#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""bot 席就緒檢查(Bot 開發 P2,10-11 a seat,the maintainer「最少步驟串成 bot」):開席、席上 /bind 之後自動查一次,
不寫信、不叫醒、不花額度,只讀設定檔;查不過就把原因寫進 status.json 的 health,右欄名字後面直接出現「⚠原因」。

為什麼不是 `aib bot ping --no-send`:那支只查「席活著、有綁對話」,在橋自己裡面兩件一定成立,自己查自己等於沒查。
真正會讓 bot 收不到信、又事先查得出來的,是各家的前置設定:
  GrokBot   名冊裡有這個 agent;這個 agent 接了門鈴(webhook 網址與金鑰,形狀判準同 connect:grokbot_threads.validate_wake)
  ClaudeBot 本機對話:全域 ~/.claude/settings.json 裝了 hook、而且沒有壞掉的舊指令(雲端對話量不到,不判)
  MuseBot   排程制,事先查不到;15 分鐘沒來收信由 bridge_readmarks 標「⚠沒來收信」
  CodexBot  直連 App Server,連得上才開得了席 ⇒ 開起來就是就緒

    python3 bot_ready.py <provider> [<綁定 id>]      印每一項,rc 0 = 就緒、1 = 還差、2 = 用法錯
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 右欄短字(pane-right-ai.sh _BRIDGE_HEALTH_SHORT)用這幾個 kind
KIND_NO_AGENT = "agent-missing"
KIND_NO_DOORBELL = "no-doorbell"
KIND_NO_HOOK = "no-hook"
KIND_NO_MAIL_TOOL = "no-mail-tool"
ENV_MAIL_BIN = "AIB_GROKBOT_MAIL_BIN"   # 尺用:換掉 ~/.local/bin/aib-grokbot-mail 的位置


def _state_root(base=None) -> Path:
    if base:
        return Path(base)
    for key in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        v = os.environ.get(key)
        if v:
            return Path(v).expanduser()
    return Path.home() / ".ez2-aib"


def _row(ok: bool, kind: str, what: str, fix: str = "") -> dict:
    return {"ok": ok, "kind": kind, "what": what, "fix": fix}


def _grokbot(agent: str, base) -> list[dict]:
    import grokbot_threads as gt
    rows = []
    try:
        known = {r["id"] for r in gt.list_agents(10 ** 6)}
    except (gt.RosterSchemaChanged, gt.RosterUnreadable):
        known = None
    if known is not None:
        rows.append(_row(agent in known, KIND_NO_AGENT, "Grok Bot 名冊裡有這個 agent",
                         "名冊裡找不到 %s:在 Grok Bot app 點它一下(或確認沒被刪),再打 /bind" % agent[:8]))
    url, key = gt.read_agent_wake(agent, _state_root(base))
    problems = gt.validate_wake(url, key)
    rows.append(_row(not problems, KIND_NO_DOORBELL, "agent 接了門鈴(webhook)",
                     "沒接門鈴(%s),bot 收不到叫醒:在席上打 /connect(或 aib bot connect grokbot --agent %s),接好就生效,不用重開"
                     % (";".join(problems), agent)))
    # 10-11 a seat:例行任務請 bot 跑的是 ~/.local/bin/aib-grokbot-mail(grokbot_threads.routine_request);這台沒裝 / 連結斷了
    #   ⇒ 門鈴響了、bot 也醒了,但每次收信都默默失敗。tools/aib-install-path 會裝。
    mail = Path(os.environ.get(ENV_MAIL_BIN) or (Path.home() / ".local" / "bin" / "aib-grokbot-mail"))
    rows.append(_row(os.path.isfile(mail) and os.access(mail, os.X_OK), KIND_NO_MAIL_TOOL, "收信工具 aib-grokbot-mail 在",
                     "bot 收信要跑的 %s 不在(或連結斷了):跑 bash tools/aib-install-path 裝上" % mail))
    return rows


def _claude_is_local(agent: str) -> bool | None:
    """這條 Claude 對話在不在這台 Mac:對話檔在本機,或是 AIB 代開的(剛開時對話檔還沒寫)⇒ True;
    兩者都不是(例如走中繼的雲端對話)⇒ None(量不到,不判)。"""
    import claudebot_threads as ct
    if ct.transcript_path(agent):
        return True
    try:
        import claudebot_spawn
        if claudebot_spawn.record_of(agent):
            return True
    except Exception:  # noqa: BLE001
        pass
    return None


def _claudebot(agent: str, claude_settings=None) -> list[dict]:
    """只看全域 ~/.claude/settings.json:倉庫層 .claude/settings.json 那份是給雲端的,本機沒有中繼變數時一律放行零輸出,
    裝了也收不到信(待修清單第 13 件),不能算「裝好了」。"""
    if _claude_is_local(agent) is None:
        return []
    import claudebot_hook as ch
    f = Path(claude_settings) if claude_settings else ch.settings_path()
    try:
        cfg = ch._load_settings(f)
    except (OSError, ValueError):
        return [_row(False, KIND_NO_HOOK, "Claude 那邊裝了 ClaudeBot hook", "讀不懂 %s,看不出 hook 裝了沒" % f)]
    if not ch.installed(cfg):
        return [_row(False, KIND_NO_HOOK, "Claude 那邊裝了 ClaudeBot hook",
                     "沒裝 hook,Claude 收不到信:跑 python3 src/cockpit/claudebot_hook.py install(會先備份 %s),"
                     "再重開那條 Claude 對話(hook 在對話啟動時載入)" % f)]
    stale = ch.stale_commands(cfg)
    if stale:
        return [_row(False, KIND_NO_HOOK, "ClaudeBot hook 能跑", "hook 裝了但會壞:" + ";".join(stale))]
    return [_row(True, KIND_NO_HOOK, "Claude 那邊裝了 ClaudeBot hook")]


def claude_hook_state(claude_settings=None) -> str:
    """全域 settings.json 裡 ClaudeBot hook 的狀態:ok / missing / stale / unreadable。"""
    import claudebot_hook as ch
    f = Path(claude_settings) if claude_settings else ch.settings_path()
    try:
        cfg = ch._load_settings(f)
    except (OSError, ValueError):
        return "unreadable"
    if not ch.installed(cfg):
        return "missing"
    return "stale" if ch.stale_commands(cfg) else "ok"


def ensure_claude_hook(claude_settings=None) -> tuple:
    """P5(10-11 a seat,the maintainer 點頭):全域沒裝或裝了會壞 ⇒ 自動跑 claudebot_hook.py install(它自己先備份 settings.json)。
    回 (動了沒, 成功沒, 一句話)。讀不懂設定檔 ⇒ 不動(install 也會拒絕覆寫),照實說。
    AIB_CLAUDEBOT_AUTO_HOOK=0 關掉。"""
    import subprocess
    state = claude_hook_state(claude_settings)
    if state == "ok":
        return False, True, "ClaudeBot hook 已經裝好"
    if state == "unreadable":
        return False, False, "看不懂 Claude 的 settings.json,沒動它;請自己跑 python3 src/cockpit/claudebot_hook.py install"
    if (os.environ.get("AIB_CLAUDEBOT_AUTO_HOOK") or "1") == "0":
        return False, False, "ClaudeBot hook %s;自動安裝關著(AIB_CLAUDEBOT_AUTO_HOOK=0)" % ("沒裝" if state == "missing" else "會壞")
    cmd = [sys.executable, str(HERE / "claudebot_hook.py"), "install"]
    if claude_settings:
        cmd += ["--settings", str(claude_settings)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return True, False, "自動安裝 ClaudeBot hook 沒跑完(%s)" % type(exc).__name__
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    if r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        return True, False, "自動安裝 ClaudeBot hook 失敗:" + (err[-1][:200] if err else "rc=%s" % r.returncode)
    return True, True, ("已自動%s ClaudeBot hook:" % ("安裝" if state == "missing" else "修好")) + (lines[0] if lines else "")


def check(provider: str, agent: str, base=None, claude_settings=None) -> list[dict]:
    """回每一項 {ok, kind, what, fix}。沒綁(agent 空)不查:那是另一種狀態(右欄已有「⚠沒綁對話」)。
    任何一項自己壞掉(讀檔例外)都不擋開席:那一項略過。"""
    if not agent:
        return []
    try:
        if provider == "grokbot":
            return _grokbot(agent, base)
        if provider == "claudebot":
            return _claudebot(agent, claude_settings)
    except Exception:  # noqa: BLE001 —— 就緒檢查是附加的,自己壞了不能讓橋停
        return []
    return []


def first_problem(rows: list[dict]):
    return next((r for r in rows if not r["ok"]), None)


def summary(provider: str, rows: list[dict]) -> str:
    """一行人話給席上看。沒有可查的項目 ⇒ 空字串(不印)。"""
    if not rows:
        return ""
    bad = [r for r in rows if not r["ok"]]
    if not bad:
        return "%s: ✓ 就緒(%s;沒寫信、沒花額度)" % (provider, "、".join(r["what"] for r in rows))
    return "%s: ⚠ 還沒就緒 —— %s" % (provider, ";".join(r["fix"] for r in bad))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["ensure-claude-hook"]:
        changed, ok, msg = ensure_claude_hook()
        if changed or not ok:          # 本來就裝好 ⇒ 不吵
            print("aib: claudebot " + msg, file=sys.stderr)
        return 0 if ok else 1
    if not argv or argv[0] not in ("grokbot", "claudebot", "musebot", "codexbot"):
        print("用法:bot_ready.py <grokbot|claudebot|musebot|codexbot> [<綁定 id>] | ensure-claude-hook", file=sys.stderr)
        return 2
    if argv[0] in ("grokbot", "claudebot") and len(argv) < 2:
        print("用法:%s 要給綁定 id(agent id / Claude 對話 uuid)" % argv[0], file=sys.stderr)
        return 2
    rows = check(argv[0], argv[1] if len(argv) > 1 else "")
    for r in rows:
        print(("✓ " if r["ok"] else "✗ ") + (r["what"] if r["ok"] else r["fix"]))
    if not rows:
        print("%s 事先沒有可查的設定(%s)" % (argv[0], "排程制,15 分鐘沒來收信右欄會標 ⚠" if argv[0] == "musebot"
                                             else "連得上才開得了席" if argv[0] == "codexbot"
                                             else "雲端對話或量不到"))
    return 1 if first_problem(rows) else 0


if __name__ == "__main__":
    sys.exit(main())
