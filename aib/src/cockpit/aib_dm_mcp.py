#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""給 Codex 桌面(ChatGPT App)用的 MCP server:寫入只有一個工具 `aib_dm`。

10-11(the maintainer 直令「只加唯讀」):另掛三個唯讀工具 aib_inbox / aib_team / aib_tasks(aib_mcp_readonly.py),
它們沒有任何會改狀態的路徑;回覆、標已讀、派工一律還是走 aib_dm(= aib dm)。

the maintainer 2026-09-24:桌面對話裡出現生的 `<aib_tool>{…}</aib_tool>` —— 因為桌面模型沒有真的 aib_dm 工具,
只能用文字後備。登記這支到 ~/.codex/config.toml 的 [mcp_servers.aib_dm] 之後,模型呼叫的是真工具,
桌面顯示工具卡,工具本體直接跑 `aib dm … --verify`(aib_dm_tool.run_aib_dm,唯一合法路徑)。

協定:MCP over stdio(每行一則 JSON-RPC 2.0)。只實作 initialize / tools/list / tools/call / ping。
艙與席:桌面 app 起這支時沒有 tmux 環境;從 codexbot.env 拿預設 thread → 找綁著它的活席 → 用它的
socket 當 EZ2_AIHUB_SOCKET(找不到就照 aib_dm_tool.hub_env 的既有規則)。寄件身分由 dm-ctl 推導,不偽填。
AIB_DM_MCP_DRY=1:不真的送,回「會送給誰、什麼」(尺用)。
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_dm_tool as dmt  # noqa: E402
import codexbot_dm_socket as dms  # noqa: E402
import codexbot_rules as rules  # noqa: E402
import aib_mcp_readonly as ro  # noqa: E402

PROTOCOL = "2024-11-05"
LOG_ENV = "AIB_DM_MCP_LOG"          # B3:每筆 tools/call 落這裡(稽核:誰、何時、送給誰、走哪條路、結果)
LOG_DEFAULT = Path.home() / ".ez2-aib" / "logs" / "aib-dm-mcp.jsonl"
TARGETS_ENV = "AIB_DM_MCP_TARGETS"  # B2:逗號分隔的席碼白名單;空 = 不限
RATE_ENV = "AIB_DM_MCP_RATE_PER_MIN"   # B2:每分鐘上限(算的是「真的送出去」的筆數,含失敗;DRY 與被拒不算)
RATE_DEFAULT = 6
SENT_ROUTES = frozenset(("direct", "bridge"))
BRIDGE_SOCKET_ENV = "AIB_DM_MCP_BRIDGE_SOCKET"   # B1:指定橋 socket(尺用;沒設就從 codexbot.env 的預設 thread 算)
NO_DIRECT_ENV = "AIB_DM_MCP_NO_DIRECT"           # B1:=1 橋連不上就拒送,不退回直送(直送身分是 unattested)


def default_thread(secrets_file=None):
    sf = Path(secrets_file or os.environ.get("AIB_CODEXBOT_SECRETS_FILE") or Path.home() / ".ez2-aib" / "secrets" / "codexbot.env")
    try:
        for line in sf.read_text(encoding="utf-8").splitlines():
            if line.startswith("AIB_CODEXBOT_THREAD_ID="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        return ""
    return ""


def bridge_socket(env=None):
    """B1:橋的 dm socket 路徑;檔不在回 None(橋沒開、或這條對話沒有活席)。"""
    env = os.environ if env is None else env
    p = env.get(BRIDGE_SOCKET_ENV)
    if not p:
        thread = default_thread(env.get("AIB_CODEXBOT_SECRETS_FILE"))
        if not thread:
            return None
        base = Path(env.get("EZ2_AIB_STATE_ROOT") or env.get("EZ2_AIB_HOME") or Path.home() / ".ez2-aib")
        p = dms.socket_path(base, thread)
    p = Path(p)
    return p if p.exists() else None


META_LOG_ENV = "AIB_DM_MCP_META_LOG"   # R2(09-25):initialize / tools/call 帶了什麼 _meta(只記鍵與 id 類的值,不記訊息內容)
META_LOG_DEFAULT = Path.home() / ".ez2-aib" / "logs" / "aib-dm-mcp-meta.jsonl"
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _meta_view(o, depth=0):
    """_meta 的安全視圖:保留結構;字串只留 uuid 形或 64 字以內的,其餘只記長度。量的是「有沒有對話識別」,不是內容。"""
    if depth > 4:
        return "<太深>"
    if isinstance(o, dict):
        return {str(k): _meta_view(v, depth + 1) for k, v in list(o.items())[:40]}
    if isinstance(o, list):
        return [_meta_view(v, depth + 1) for v in o[:5]]
    if isinstance(o, str):
        return o if (_UUID_RE.match(o) or len(o) <= 64) else "<字串 %d 字>" % len(o)
    return o if isinstance(o, (int, float, bool)) or o is None else "<%s>" % type(o).__name__


def log_meta(event, params, path=None):
    """R2 第一步「量」:桌面 app 到底有沒有告訴我們是哪條對話在呼叫。寫不進去不擋送信。"""
    params = params if isinstance(params, dict) else {}
    rec = {"event": event, "epoch": round(time.time(), 3), "pid": os.getpid(), "ppid": os.getppid(),
           "params_keys": sorted(k for k in params if k != "arguments"),
           "meta": _meta_view(params.get("_meta")),
           "codex_env": {k: _meta_view(v) for k, v in os.environ.items() if k.startswith("CODEX_")}}
    if event == "initialize":
        rec["client_info"] = _meta_view(params.get("clientInfo"))
    try:
        target = Path(path or os.environ.get(META_LOG_ENV) or META_LOG_DEFAULT)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _state_base(env=None):
    env = os.environ if env is None else env
    return Path(env.get("EZ2_AIB_STATE_ROOT") or env.get("EZ2_AIB_HOME") or Path.home() / ".ez2-aib")


def live_bridges(env=None):
    """R2:現在活著的 codexbot 橋 {桌面 thread id: 它的 dm socket}。
    活 = 席位檔 provider=codexbot、沒有 exit_reason、pid 活著、而且 socket 檔在。
    ★ 席位目錄跟著狀態根走(EZ2_AIB_INSTANCES_ROOT 沒設就用 <狀態根>/instances),尺注入 tmp 時不會讀到真機。"""
    env = os.environ if env is None else env
    base = _state_base(env)
    root = Path(env.get("EZ2_AIB_INSTANCES_ROOT") or base / "instances")
    out = {}
    for f in sorted(root.glob("*/seats/w*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("provider") != "codexbot" or d.get("exit_reason") or not d.get("desktop_thread_id"):
            continue
        try:
            os.kill(int(d.get("pid")), 0)
        except (TypeError, ValueError, ProcessLookupError):
            continue
        except PermissionError:
            pass
        sock = dms.socket_path(base, d["desktop_thread_id"])
        if sock.exists():
            out[d["desktop_thread_id"]] = sock
    return out


def _strings(o, depth=0):
    if depth > 6:
        return
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _strings(v, depth + 1)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v, depth + 1)


def caller_session_uuid(meta):
    """codex 在每次 tools/call 的 _meta 裡蓋的 `x-codex-turn-metadata.session_id`。
    它與席位檔的 session_uuid 是同一個值(09-27 逐字元比對過),所以它是**這通呼叫來自哪一席**
    的證據。★ 只認 codex 蓋的那一格,不看模型自己在 args 裡寫了什麼 —— 後者是自報身分。
    取不到回空字串,不猜;下游對不到活席也只會維持不具名。"""
    if not isinstance(meta, dict):
        return ""
    turn = meta.get("x-codex-turn-metadata")
    if not isinstance(turn, dict):
        return ""
    sid = turn.get("session_id")
    return sid.strip() if isinstance(sid, str) else ""


def caller_thread(meta, live):
    """R2:_meta 裡**任何一個字串值**剛好等於某隻活橋綁的 thread id ⇒ 那就是呼叫者。
    不必先知道桌面把它放在哪個鍵;只認正向命中,沒命中就回 None(不猜)。"""
    for v in _strings(meta):
        if v in live:
            return v
    return None


def log_path():
    return Path(os.environ.get(LOG_ENV) or LOG_DEFAULT)


def log_call(record, path=None):
    """B3:一筆一行 JSON,永遠追加;寫不進去不影響送(稽核不能反過來擋業務,但會在回覆裡說)。回 True=寫成功。"""
    path = Path(path or log_path())
    row = dict(record)
    row.setdefault("epoch", time.time())
    row.setdefault("iso", time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(row["epoch"])))
    row.setdefault("pid", os.getpid())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def recent_sent(path=None, window_secs=60.0, now=None):
    """B2 的節流母體:日誌最後 window_secs 內、route 在 SENT_ROUTES 的筆數。讀不到 = 0(讀不到日誌不該把工具鎖死)。"""
    path = Path(path or log_path()); now = time.time() if now is None else now
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-500:]
    except OSError:
        return 0
    n = 0
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("route") in SENT_ROUTES and (now - float(row.get("epoch") or 0)) <= window_secs:
            n += 1
    return n


def policy_check(target, path=None, now=None, env=None):
    """B2:回 None = 放行;回字串 = 拒絕理由(印給模型看、也落日誌)。白名單看席碼(去 @);上限看 recent_sent。"""
    env = os.environ if env is None else env
    code = (target or "").strip().lstrip("@").lower()
    seat = code.rsplit("/", 1)[-1].lstrip("@")      # 09-28 目標是 <艙位碼>/<席碼>;白名單可寫整個地址或只寫席碼
    allow = [x.strip().lstrip("@").lower() for x in (env.get(TARGETS_ENV) or "").split(",") if x.strip()]
    if allow and code not in allow and seat not in allow:
        return "DM 未送出:目標 @%s 不在 MCP 白名單(%s=%s)。" % (code, TARGETS_ENV, ",".join(allow))
    try:
        limit = int(env.get(RATE_ENV) or RATE_DEFAULT)
    except ValueError:
        limit = RATE_DEFAULT
    if limit > 0:
        n = recent_sent(path, now=now)
        if n >= limit:
            return "DM 未送出:MCP 路徑最近一分鐘已送 %d 則,達上限 %d(%s)。稍後再送或請人調高。" % (n, limit, RATE_ENV)
    return None


def resolve_hub_from_binding(secrets_file=None, instances_root=None):
    """codexbot.env 的預設 thread → 綁著它的活 codexbot 席 → 它的 socket。找不到回 ''。"""
    sf = Path(secrets_file or os.environ.get("AIB_CODEXBOT_SECRETS_FILE") or Path.home() / ".ez2-aib" / "secrets" / "codexbot.env")
    thread = ""
    try:
        for line in sf.read_text(encoding="utf-8").splitlines():
            if line.startswith("AIB_CODEXBOT_THREAD_ID="):
                thread = line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        return ""
    if not thread:
        return ""
    root = Path(instances_root or os.environ.get("EZ2_AIB_INSTANCES_ROOT") or Path.home() / ".ez2-aib" / "instances")
    for f in glob.glob(str(root / "*" / "seats" / "w*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("provider") != "codexbot" or d.get("exit_reason") or d.get("desktop_thread_id") != thread:
            continue
        try:
            os.kill(int(d.get("pid")), 0)
        except (TypeError, ValueError, ProcessLookupError):
            continue
        except PermissionError:
            pass
        return d.get("socket") or ""
    return ""


def tool_schema():
    fn = dmt.dm_tool()["function"]
    # title:桌面卡片顯示用(the maintainer 09-24「AIB DM 要大寫」;沒有 title 時桌面把 aib_dm 變成 "Aib dm")。name 仍是機器 id aib_dm。
    return {"name": fn["name"], "title": "AIB DM", "description": fn["description"], "inputSchema": fn["parameters"],
            "annotations": {"title": "AIB DM"}}


def call_tool(name, args, runner=None, log=None, meta=None):
    if name in ro.TOOLS:      # 唯讀三工具:不經節流/白名單(那是管「送出去」的),也不落送信稽核日誌
        return ro.call(name, args, caller_session_uuid(meta))
    if name != dmt.DM_TOOL_NAME:
        return {"content": [{"type": "text", "text": "unknown tool: %s" % name}], "isError": True}
    args = args or {}
    target, message = str(args.get("target") or ""), str(args.get("message") or "")
    base = {"tool": name, "target": target, "message_len": len(message), "message_head": message[:80]}
    def finish(route, text, ok):
        logged = log_call(dict(base, route=route, ok=bool(ok), outcome_tail=text[-200:]), log)
        if not logged:
            text += "(稽核日誌寫不進去:%s)" % log_path()
        return {"content": [{"type": "text", "text": text}], "isError": not ok}
    if os.environ.get("AIB_DM_MCP_DRY") == "1":
        return finish("dry", "DRY: 會送給 %s:%s" % (args.get("target"), args.get("message")), True)
    reason = policy_check(target, log)
    if reason:
        return finish("rejected", reason, False)
    # B1:先走橋(dm 從席 pane 出發,抬頭是 @席碼);橋不在才直送(抬頭 unattested),嚴格模式則拒送。
    # R2(09-25,多隻 CodexBot):以前一律走 codexbot.env「預設那條」的橋 ⇒ 第二隻說的話用第一隻的名義送出(真機實測)。
    #   現在:_meta 認得出呼叫的對話 ⇒ 走那一隻的橋;認不出、又有兩隻以上活著 ⇒ 拒送(fail-closed),
    #   不再默認成某一隻。只有一隻時行為不變。指定了 AIB_DM_MCP_BRIDGE_SOCKET(尺用)⇒ 照指定,不掃活橋。
    if os.environ.get(BRIDGE_SOCKET_ENV):
        sock = bridge_socket()
    else:
        live = live_bridges()
        caller = caller_thread(meta, live)
        base["caller_thread"] = caller or ""
        base["live_bridges"] = len(live)
        if caller:
            sock = live[caller]
        elif len(live) >= 2:
            return finish("rejected", "DM 未送出:現在有 %d 隻 CodexBot 同時在線,這個工具分不出是哪一條桌面對話在呼叫;"
                          "為了不用別隻的名義送出,這則不送。請改在那一隻的 AIB 席上用 /dm <@席> <訊>。" % len(live), False)
        else:
            sock = bridge_socket()
    if sock is not None:
        try:
            reply = dms.send_via_bridge(sock, target, message)
        except (OSError, ValueError) as exc:
            if os.environ.get(NO_DIRECT_ENV) == "1":
                return finish("rejected", "DM 未送出:橋 socket 連不上(%s)且 %s=1 不退回直送。" % (str(exc)[:80], NO_DIRECT_ENV), False)
            prefix = "橋 socket 連不上(%s),改直送(寄件身分 unattested):" % str(exc)[:80]
        else:
            return finish("bridge", str(reply.get("outcome") or ""), bool(reply.get("ok")))
    elif os.environ.get(NO_DIRECT_ENV) == "1":
        return finish("rejected", "DM 未送出:找不到橋的 dm socket(這條對話沒有活的 codexbot 席)且 %s=1 不退回直送。" % NO_DIRECT_ENV, False)
    else:
        prefix = ""
    if not os.environ.get("EZ2_AIHUB_SOCKET"):
        hub = resolve_hub_from_binding()
        if hub:
            os.environ["EZ2_AIHUB_SOCKET"] = hub
    # 直送這條路沒有橋來蓋寄件身分 ⇒ 把 codex 蓋在 _meta 的 session_id 當證據帶下去。
    # ★ 先刪後設:模型若自己在 args 裡塞了這個鍵,一律以伺服器量到的值為準(空值=刪掉),
    #   否則這個入口會從「證據」變成「自填身分」。
    args = {k: v for k, v in args.items() if k != "_claim_session_uuid"} if isinstance(args, dict) else args
    _claim = caller_session_uuid(meta)
    if _claim and isinstance(args, dict):
        args["_claim_session_uuid"] = _claim
        base["claim_session_uuid"] = _claim
    result = (runner or dmt.handle_dm_tool_call)(name, args) or ""
    ok = "rc=0" in result and "未送出" not in result
    return finish("direct", prefix + result, ok)


def handle(msg):
    method = msg.get("method"); mid = msg.get("id")
    if method == "initialize":
        log_meta("initialize", msg.get("params"))
        return {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": msg.get("params", {}).get("protocolVersion") or PROTOCOL,
                "capabilities": {"tools": {}}, "serverInfo": {"name": "aib_dm", "version": "0.1.0"},
                "instructions": rules.mcp_instructions(dmt.dm_system_snippet())}}   # D1:橋接規矩也在這裡
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [tool_schema()] + ro.schemas()}}
    if method == "tools/call":
        p = msg.get("params") or {}
        log_meta("tools/call", p)
        return {"jsonrpc": "2.0", "id": mid, "result": call_tool(p.get("name"), p.get("arguments"), meta=p.get("_meta"))}
    if method and method.startswith("notifications/"):
        return None
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}}


def main():
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        resp = handle(msg)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + "\n"); out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
