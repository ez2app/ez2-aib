#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib_dm MCP 的唯讀工具(10-11,舊協作板 MCP 搬回 B1;the maintainer 10-11 直令「只加唯讀」)。

三個工具,全部只讀,寫入一律走 aib_dm(本檔沒有任何會改狀態的路徑):
  aib_inbox  某一席的待審框:數量摘要 + 待處理那一頁的前幾筆   ← dm_queue.py list
  aib_team   某一席所在那一艙的團隊狀態:每席角色、CTX、未讀、交接 ← seat-info.py <session_uuid> --fleet
  aib_tasks  某一席的任務:席位檔主任務四欄 + 任務板指派給它的 ← seat_task_fields / seats_dir_lib.tasks_dir
             (帶 project_uid 才另讀計畫樹 tools/project/show.py --json --mine;10-11 實查這台沒有任何計畫樹資料)

★ 都是呼叫既有的唯讀指令(固定 argv、不經 shell、有逾時),不在這裡重寫第二份判定。
★ 席碼:args 給的(5 碼 hex)優先;沒給就用 codex 蓋在 _meta 的 session_id 推本席(seat_address.uuid_tail5);
  兩者都沒有就回錯,不猜。模型自己在 args 裡寫的 session id 不採用(自報身分)。
  aib_team 也一樣(10-11 Codex 實測:原本不收席碼,從艙外叫回一包全空、還標成功;現在認不出艙一律回錯)。
★ 輸出截到 MAX_CHARS,截了會說。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SEAT_RE = re.compile(r"^[0-9a-f]{5}$")
MAX_CHARS = 12000
TIMEOUT_S = 30
INBOX_ROWS = 20

TOOLS = {
    "aib_inbox": {
        "title": "AIB 收件匣(唯讀)",
        "description": "Read-only: an AIB seat's dm inbox — counts (unread / needs decision / human) plus the first rows "
                       "of the waiting tab. Does not mark anything read; to reply use aib_dm.",
        "props": {"seat": "5-char seat code (e.g. 04e0a); omit = the seat this conversation sits in"},
    },
    "aib_team": {
        "title": "AIB 團隊狀態(唯讀)",
        "description": "Read-only: the team in an AIB seat's cabin — each seat's role, captain, context use, unread count, "
                       "handoff.",
        "props": {"seat": "5-char seat code whose cabin to show; omit = the seat this conversation sits in"},
    },
    "aib_tasks": {
        "title": "AIB 任務(唯讀)",
        "description": "Read-only: an AIB seat's tasks — its main-task fields and the task-board cards assigned to it; "
                       "with project_uid also its plan-tree leaves.",
        "props": {"seat": "5-char seat code; omit = the seat this conversation sits in",
                  "project_uid": "optional plan-tree project uid (projects/<uid>/nodes.jsonl)"},
    },
}


def schemas():
    out = []
    for name, t in TOOLS.items():
        props = {k: {"type": "string", "description": v} for k, v in t["props"].items()}
        out.append({"name": name, "title": t["title"], "description": t["description"],
                    "inputSchema": {"type": "object", "properties": props, "additionalProperties": False},
                    "annotations": {"title": t["title"], "readOnlyHint": True}})
    return out


def _seat(args, session_uuid):
    raw = str((args or {}).get("seat") or "").strip().lstrip("@").lower()
    raw = raw.rsplit("/", 1)[-1].lstrip("@")         # 也收 <艙位碼>/<席碼>
    if raw:
        return raw if SEAT_RE.match(raw) else None
    if session_uuid:
        try:
            sys.path.insert(0, str(HERE))
            import seat_address
            code = seat_address.uuid_tail5(session_uuid) or ""
            return code if SEAT_RE.match(code) else None
        except Exception:
            return None
    return None


def _run(argv, env=None):
    """回 (ok, 文字)。逾時、找不到、rc≠0 都如實回,不吞。"""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_S, stdin=subprocess.DEVNULL,
                           env=env or os.environ.copy())
    except subprocess.TimeoutExpired:
        return False, "逾時(%d 秒):%s" % (TIMEOUT_S, Path(argv[1]).name)
    except OSError as exc:
        return False, "跑不起來:%s" % exc
    if p.returncode != 0:
        return False, "rc=%d %s" % (p.returncode, (p.stderr or p.stdout).strip()[-400:])
    return True, p.stdout


def _clip(text):
    if len(text) <= MAX_CHARS:
        return text
    return text[:MAX_CHARS] + "\n…(截斷:原長 %d 字,只回前 %d 字)" % (len(text), MAX_CHARS)


def inbox(seat):
    py = sys.executable or "python3"
    q = str(HERE / "dm_queue.py")
    ok, brief = _run([py, q, "list", "--seat", seat, "--brief"])
    if not ok:
        return False, "收件匣讀不到:" + brief
    ok, rows = _run([py, q, "list", "--seat", seat, "--tab", "waiting"])
    if not ok:
        return False, "收件匣讀不到:" + rows
    try:
        items = json.loads(rows)
    except ValueError:
        return False, "收件匣回的不是 JSON"
    head = items[:INBOX_ROWS] if isinstance(items, list) else items
    more = (len(items) - INBOX_ROWS) if isinstance(items, list) and len(items) > INBOX_ROWS else 0
    out = {"seat": seat, "brief": json.loads(brief), "waiting": head}
    if more:
        out["waiting_more"] = more
    return True, json.dumps(out, ensure_ascii=False)


def team(seat, session_uuid=""):
    """seat 有給 ⇒ 用它最新席位檔的 session_uuid 去問;沒給 ⇒ 用 _meta 的 session_uuid。認不出艙就回錯,不回空殼。"""
    if seat:
        f = _seat_file(seat)
        try:
            session_uuid = json.loads(f.read_text(encoding="utf-8")).get("session_uuid") if f else ""
        except (OSError, ValueError):
            session_uuid = ""
        if not session_uuid:
            return False, "找不到 %s 的席位檔(或席位檔沒記 session_uuid),認不出它在哪一艙,所以不猜。" % seat
    if not session_uuid:
        return False, "要哪一席?帶 seat(5 碼席碼,例 04e0a);這條對話認不出是哪一席,所以不猜。"
    argv = [sys.executable or "python3", str(REPO / "tools" / "claude-mods" / "aib-boards" / "bin" / "seat-info.py"),
            session_uuid, "--fleet"]
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE", "AIB_RESUME_SESSION_UUID")}
    ok, text = _run(argv, env=env)     # 只准用對話 id 對席位檔;不准退回「呼叫端在哪個 tmux 窗格」去猜(10-11 實測猜成別席)
    if not ok:
        return False, "團隊狀態讀不到:" + text
    try:
        d = json.loads(text)
    except ValueError:
        return False, "團隊狀態回的不是 JSON"
    if not d.get("cabin"):
        return False, "認不出%s在哪一艙(席位檔對不上這條對話),所以不猜。" % (seat or "這條對話")
    keep = ("cabin", "cabin_code", "code", "role", "captain", "captain_addr", "roster", "fleet", "err")
    return True, json.dumps({k: d.get(k) for k in keep}, ensure_ascii=False)


def _seat_file(seat):
    """席碼 → 最新的那份席位檔(同碼多艙時取 started_epoch 最大的)。找不到回 None。"""
    sys.path.insert(0, str(HERE))
    import seat_address
    from seats_dir_lib import state_root
    root = Path(os.environ.get("EZ2_AIB_INSTANCES_ROOT") or Path(state_root()[0]) / "instances")
    best = None
    for f in root.glob("*/seats/w*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if seat_address.code_of(d) != seat:
            continue
        key = float(d.get("started_epoch") or 0)
        if best is None or key > best[0]:
            best = (key, f)
    return best[1] if best else None


def tasks(seat, project_uid=""):
    sys.path.insert(0, str(HERE))
    from seats_dir_lib import tasks_dir
    import seat_task_fields as stf
    out = {"seat": seat}
    f = _seat_file(seat)
    if f is None:
        out["task_fields"] = None
        out["task_fields_why"] = "找不到這一席的席位檔"
    else:
        try:
            out["task_fields"] = stf.read_seat_task_fields(json.loads(f.read_text(encoding="utf-8")))
        except Exception as exc:          # 讀壞了要說,不當成沒任務
            out["task_fields"] = None
            out["task_fields_why"] = "席位檔讀不到:%s" % str(exc)[:120]
    board, cards = Path(tasks_dir()), []
    for c in sorted(board.glob("*.json")):
        try:
            d = json.loads(c.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        who = str(d.get("assignee") or "").lstrip("@").rsplit("/", 1)[-1].lstrip("@").lower()
        if who == seat:
            cards.append({k: d.get(k) for k in ("id", "title", "status", "priority", "updated_at")})
    out["board"] = cards
    out["board_dir"] = str(board)
    if project_uid:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", project_uid):
            return False, "project_uid 只能英數、點、底線、連字號"
        ok, text = _run([sys.executable or "python3", str(REPO / "tools" / "project" / "show.py"),
                         "--repo-root", str(REPO), "--project-uid", project_uid, "--json", "--mine", "--owner", seat])
        if not ok:
            return False, "計畫樹讀不到:" + text
        try:
            out["plan_tree"] = json.loads(text)
        except ValueError:
            out["plan_tree"] = text
    return True, json.dumps(out, ensure_ascii=False)


def call(name, args, session_uuid=""):
    """回 MCP tools/call 的 result。不認得的名字回 None(交給呼叫端)。"""
    if name not in TOOLS:
        return None
    if name == "aib_team":
        raw = str((args or {}).get("seat") or "").strip()
        seat = _seat(args, "") if raw else ""
        if raw and not seat:
            return {"content": [{"type": "text", "text": "seat 要 5 碼席碼(例 04e0a),所以不猜。"}], "isError": True}
        ok, text = team(seat, session_uuid)
    else:
        seat = _seat(args, session_uuid)
        if not seat:
            return {"content": [{"type": "text", "text": "要哪一席?帶 seat(5 碼席碼,例 04e0a);"
                                 "這條對話認不出是哪一席,所以不猜。"}], "isError": True}
        ok, text = inbox(seat) if name == "aib_inbox" else tasks(seat, str((args or {}).get("project_uid") or "").strip())
    return {"content": [{"type": "text", "text": _clip(text)}], "isError": not ok}
