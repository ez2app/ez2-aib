#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""ClaudeBot:Claude app / Claude Code 的**本機**對話清單、占用鎖(設計頁 docs/CLAUDEBOT-DESKTOP.md §3.1)。

綁定單位 = 一條 Claude session(`session_id`,完整 UUID),對應 GrokBot 的 agent、CodexBot 的 thread。
清單來源 = `~/.claude/projects/<專案>/<session>.jsonl`(Claude Code 本機對話紀錄;Desktop app 的本機 session 也寫這裡)。
雲端 session 不在這台 Mac 上;v0.70.0 #16 起改由中繼站的報到名冊列進來(list_sessions_with_cloud),綁法與本機同。

- 只讀:每個檔只讀開頭 READ_HEAD_LINES 行找標題與 cwd,不讀整條對話、不落任何內容。
- 占用判法與 grokbot_threads.held_by 同一種:對橋會拿的那把鎖試一次 LOCK_EX|LOCK_NB;鎖檔不存在 ⇒ 沒被占且不建檔。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

PROVIDER = "claudebot"
BINDINGS_NAME = "claudebot-bindings"
SESSION_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ENV_PROJECTS_DIR = "AIB_CLAUDEBOT_PROJECTS_DIR"   # 尺用:假的 ~/.claude/projects
READ_HEAD_LINES = 200
NAME_MAX = 60
HELD_NAME_UNKNOWN = "另一隻 AIB 席"


def _state_root(base=None) -> Path:
    if base:
        return Path(base)
    for key in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        v = os.environ.get(key)
        if v:
            return Path(v).expanduser()
    return Path.home() / ".ez2-aib"


def cli_account(claude_json=None) -> str:
    """這台 Mac 終端機 `claude` 登入的帳號 email(~/.claude.json 的 oauthAccount.emailAddress);讀不到回 ""。
    10-09 a seat:the maintainer 的 Claude app 與終端機登入不同帳號,在 app 找不到 AIB 代開的對話,查了半天 ⇒ 開席時直接講是哪個帳號。
    只讀 email 一欄,不碰任何憑證。"""
    f = Path(claude_json) if claude_json else Path(os.environ.get("AIB_CLAUDE_JSON") or (Path.home() / ".claude.json"))
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    acct = d.get("oauthAccount") if isinstance(d, dict) else None
    email = acct.get("emailAddress") if isinstance(acct, dict) else None
    return email.strip() if isinstance(email, str) and "@" in email and len(email) < 200 else ""


def projects_dir() -> Path:
    v = (os.environ.get(ENV_PROJECTS_DIR) or "").strip()
    if v:
        return Path(v).expanduser()
    cfg = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    return (Path(cfg).expanduser() if cfg else Path.home() / ".claude") / "projects"


def valid_session_id(session_id: str) -> bool:
    return bool(SESSION_ID_RE.match(session_id or ""))


def _text_of(message) -> str:
    """user 訊息的純文字(content 可能是字串或 block 陣列)。"""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
    return ""


def _one_line(text: str) -> str:
    t = " ".join(str(text or "").split())
    t = re.sub(r"[\x00-\x1f\x7f]", "", t)
    return t[:NAME_MAX]


# 10-07 root a seat:開席選單只列「綁得起來」的對話。
#   量到的(10-07 02:1x,Mini):選單 20 條裡 15 條是 AIB 自己的 claude 席(13 活、2 已退役)、5 條是 `claude -p` 的無頭審查,
#   真正的 Claude app / 終端機對話 0 條 ⇒ a seat 開席時沒有一條能選,只好開成「沒綁、信沒人收」。
#   更糟的是可以選到 AIB 自己的席:選了之後 hook 會把信塞進那一席、把那一席的回覆當成 bot 回覆送出(兩席串線)。
#   判準只看機器寫的事實,不看標題:
#     1. session uuid 出現在任何席位檔(instances/*/seats/**/w*.json、seats/**/w*.json,含 retired/)⇒ AIB 的席,不列;
#     2. transcript 的 entrypoint 是 sdk-cli(`claude -p` 一次性跑完,沒有可以叫醒的對話)⇒ 不列;
#     3. (10-07 a seat)/clear、/resume 之後對話換號,席位檔仍記開席那一號 ⇒ 新號只記在
#        state/handoff/<席碼>.rounds.jsonl 的 session_id(persona_reinject_hook 每次都寫)⇒ 一併當成 AIB 的席。
#        實例:a seat 清空後的 d068e588 曾是清單裡唯一一條,差點被綁成 ClaudeBot。
HEADLESS_ENTRYPOINTS = frozenset(("sdk-cli",))


def aib_seat_sessions(base=None) -> set[str]:
    """所有席位檔登記過的 session uuid(活的、退役的、各艙的都算),加上清空/接回後換的新號。讀不到的檔跳過。"""
    root = _state_root(base)
    out: set[str] = set()
    pats = ("instances/*/seats/**/w*.json", "seats/**/w*.json")
    for pat in pats:
        for f in root.glob(pat):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            u = d.get("session_uuid") if isinstance(d, dict) else None
            if isinstance(u, str) and valid_session_id(u):
                out.add(u)
    for f in root.glob("state/handoff/*.rounds.jsonl"):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            u = d.get("session_id") if isinstance(d, dict) else None
            if isinstance(u, str) and valid_session_id(u):
                out.add(u)
    return out


def is_aib_seat_session(session_id: str, base=None) -> bool:
    return valid_session_id(session_id) and session_id in aib_seat_sessions(base)


def describe(path: Path) -> dict | None:
    """一條對話的 {id, name, updated, cwd, entrypoint};不是對話(沒有任何 user 文字)回 None。"""
    sid = path.stem
    if not valid_session_id(sid):
        return None
    title = first_user = cwd = entrypoint = ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= READ_HEAD_LINES:
                    break
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                if not cwd and isinstance(d.get("cwd"), str):
                    cwd = d["cwd"]
                if not entrypoint and isinstance(d.get("entrypoint"), str):
                    entrypoint = d["entrypoint"]
                kind = d.get("type")
                if kind in ("custom-title", "summary") and not title:
                    title = d.get("customTitle") or d.get("title") or d.get("summary") or ""
                elif kind == "user" and not first_user and not d.get("isMeta"):
                    t = _text_of(d.get("message"))
                    if t and not t.lstrip().startswith("<"):   # 跳過系統包起來的東西(指令輸出、提醒)
                        first_user = t
        updated = int(path.stat().st_mtime)
    except OSError:
        return None
    if not (title or first_user):
        return None
    return {"id": sid, "name": _one_line(title or first_user), "updated": updated, "cwd": cwd, "entrypoint": entrypoint}


# 10-07 a seat:綁上之後,別席的 dm 會叫醒這條對話 ⇒ 它用什麼權限模式,決定那封信能不能直接讓它改檔、跑指令。
#   AIB 代開的對話預設 manual(claudebot_spawn);用 /bind 綁的是人自己開的對話,可能是全部放行。
#   對話檔每一則 user 都帶 "permissionMode",取最後一筆 = 現在的模式(10-07 真機:bypassPermissions / auto / default 三種都見到)。
PERMISSION_TAIL_BYTES = 512 * 1024
_PERM_RE = re.compile(r'"permissionMode"\s*:\s*"([A-Za-z]+)"')
# 不問就動手 ⇒ 綁之前要人多打一個 ! 確認;會自己判斷 ⇒ 只提醒。其餘(default、plan、manual)照常。
PERMISSION_DANGER = frozenset(("bypassPermissions", "dontAsk"))
PERMISSION_CAUTION = frozenset(("auto", "acceptEdits"))
PERMISSION_WORDS = {"bypassPermissions": "全部放行", "dontAsk": "不問就做", "auto": "自動判斷", "acceptEdits": "改檔不問",
                    "default": "動手前先問", "manual": "動手前先問", "plan": "只規劃"}


def transcript_path(session_id: str, base_dir=None) -> Path | None:
    if not valid_session_id(session_id):
        return None
    root = Path(base_dir) if base_dir else projects_dir()
    try:
        hits = sorted(root.glob("*/%s.jsonl" % session_id), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    return hits[0] if hits else None


def permission_mode(session_id: str, base_dir=None) -> str:
    """這條對話現在的權限模式;量不到回空字串(量不到 ≠ 安全,呼叫端要照「不知道」講)。只讀檔尾。"""
    p = transcript_path(session_id, base_dir)
    if p is None:
        return ""
    try:
        with p.open("rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            fh.seek(max(0, size - PERMISSION_TAIL_BYTES))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    found = _PERM_RE.findall(tail)
    return found[-1] if found else ""


def permission_risk(mode: str) -> str:
    """danger / caution / ok / unknown。"""
    if not mode:
        return "unknown"
    if mode in PERMISSION_DANGER:
        return "danger"
    if mode in PERMISSION_CAUTION:
        return "caution"
    return "ok"


def permission_label(mode: str) -> str:
    return PERMISSION_WORDS.get(mode, mode) if mode else "權限不明"


def list_sessions(limit: int = 20, base_dir=None, base=None, include_aib: bool = False) -> list[dict]:
    """本機 Claude 對話,最近的在前。同一個 id 出現在兩個專案夾(不該發生)只取較新的。
    預設跳過 AIB 自己的席與無頭對話(見 HEADLESS_ENTRYPOINTS 上方說明),跳過的不佔名額;include_aib=True 才全列(除錯用)。"""
    root = Path(base_dir) if base_dir else projects_dir()
    rows: dict[str, dict] = {}
    skip = set() if include_aib else aib_seat_sessions(base)
    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:          # 列的當下被刪了
            return 0.0
    try:
        files = list(root.glob("*/*.jsonl"))
    except OSError:
        return []
    files.sort(key=_mtime, reverse=True)
    for p in files:
        if len(rows) >= max(0, int(limit)):
            break
        if not valid_session_id(p.stem) or p.stem in rows or p.stem in skip:
            continue
        row = describe(p)
        if row and (include_aib or row.get("entrypoint") not in HEADLESS_ENTRYPOINTS):
            rows[row["id"]] = row
    out = list(rows.values())
    out.sort(key=lambda x: (-x["updated"], x["id"]))
    return out[: max(0, int(limit))]


CLOUD_NAME_PREFIX = "☁ 雲端"


def list_sessions_with_cloud(limit: int = 20, base_dir=None, base=None) -> list[dict]:
    """開席選單用:本機對話 + 中繼站 24h 內報到過的雲端 session(名字帶「☁ 雲端 · 分支 · 目錄」,cloud=True)。
    同一個 id 兩邊都有(不該發生)以雲端報到為準。中繼模組讀不到就只列本機,不擋選單。"""
    rows = {r["id"]: r for r in list_sessions(limit, base_dir, base)}
    try:
        import claudebot_relay as _relay
        cloud = _relay.list_cloud_sessions(base)
    except Exception:  # noqa: BLE001
        cloud = []
    for d in cloud:
        sid = str(d.get("session_id") or "")
        if not valid_session_id(sid):
            continue
        cwd = str(d.get("cwd") or "")
        bits = [CLOUD_NAME_PREFIX, str(d.get("branch") or "").strip() or "?", Path(cwd).name if cwd else ""]
        rows[sid] = {"id": sid, "name": _one_line(" · ".join(b for b in bits if b)), "updated": int(float(d.get("last_seen") or 0)),
                     "cwd": cwd, "cloud": True}
    out = list(rows.values())
    out.sort(key=lambda x: (-x["updated"], x["id"]))
    return out[: max(0, int(limit))]


def seat_project_label(code: str, base=None) -> str:
    """席碼 → 標題用的「項目名稱」(the maintainer 09-27 21:1x「對話最上方顯示主要開發項目」,答「是」:主任務 > 綁的專案 > 資料夾)。
    主任務:席位檔 task_uid 目前全艦都是空的、也還沒有 uid→標題的查法 ⇒ 這一版從「綁的專案」開始:
      identity/seat-display.json 的 folder(R16 第二大腦,例「C-Ez2 Core系統底層/C80-系統記憶交接」)取最後一段。
    量不到回 ""(呼叫端退回資料夾名)。"""
    if not isinstance(code, str) or not code:
        return ""
    try:
        with open(_state_root(base) / "identity" / "seat-display.json", encoding="utf-8") as fh:
            d = (json.load(fh).get("display") or {}).get(code) or {}
    except (OSError, ValueError, AttributeError):
        return ""
    folder = d.get("folder") if isinstance(d, dict) else None
    if not isinstance(folder, str) or not folder.strip():
        return ""
    return _one_line(folder.strip().rstrip("/").split("/")[-1])


def lock_path(session_id: str, base=None) -> Path:
    if not valid_session_id(session_id):
        raise ValueError("Claude session id 形狀不對(要完整小寫 UUID)")
    return _state_root(base) / BINDINGS_NAME / (session_id + ".lock")


def held_by_names(session_id: str, base=None) -> list[str]:
    out = []
    for st in sorted((_state_root(base) / "bridge").glob("*/status.json")):
        try:
            d = json.loads(st.read_text(encoding="utf-8"))
            if d.get("provider") != PROVIDER or d.get("agent_id") != session_id or d.get("state") == "stopped":
                continue
            os.kill(int(d.get("pid")), 0)
        except (OSError, ValueError, TypeError):
            continue
        out.append("@%s" % (d.get("code") or "?"))
    return out


def held_by(session_id: str, base=None) -> list[str]:
    """這條 Claude 對話是不是被一隻活 claudebot 席占著。空 list = 沒被占。"""
    import fcntl
    lock = lock_path(session_id, base)
    if not lock.exists():
        return []
    with lock.open("r") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return held_by_names(session_id, base) or [HELD_NAME_UNKNOWN]
        fcntl.flock(fh, fcntl.LOCK_UN)
    return []


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["cli-account"]:
        print(cli_account())
        sys.exit(0)
    print("usage: claudebot_threads.py cli-account", file=sys.stderr)
    sys.exit(2)
