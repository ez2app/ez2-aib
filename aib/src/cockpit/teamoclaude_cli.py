#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Teamo-Claude (TeamoRouter Anthropic-native) API-key terminal seat.

AIB opens this like other providers (tmux pane). Auth is TEAMO_API_KEY
(shared with Teamo-Codex) or TEAMCLAUDE_API_KEY — not OAuth CLI (ED.4).

Anthropic Messages API via https://api.teamorouter.com/v1/messages
(OpenAI-compat seat is provider id teamo / Teamo-Codex).
"""
from __future__ import annotations

import repl_readline as _repl_rl
_repl_rl.enable_repl_readline()

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
import aib_dm_tool as _aib_dm
import api_persona as _api_persona
import api_model_gauge as _api_model_gauge  # 10-01:右欄顯示這一席現在送出去的模型

DEFAULT_BASE = "https://api.teamorouter.com"  # Anthropic native root
DEFAULT_MODEL = "claude-sonnet-4-6"
# Cloudflare 擋 Python-urllib 預設 UA（error 1010）；必須自報身分。
USER_AGENT = "aib-teamoclaude/0.62 (+Ez2-AIB; TeamoRouter Anthropic)"
KEY_NAME = "TEAMO_API_KEY"
BASE_ENV = "TEAMCLAUDE_BASE_URL"



DM_TOOL = _aib_dm.DM_TOOL
DM_SYSTEM = {
    "role": "system",
    "content": (
        _aib_dm.GENE_HEAD + "You are an AIB seat named Teamo-Claude. " + _aib_dm.dm_system_snippet() + " "
        "Only call aib_dm when a human or seat gave you a real seat code; otherwise answer in plain text. "
        "For local development use aib_local (operations read/read_chunk/search/test/propose/write/git in your own worktree; "
        "write needs read + propose first). Tool calling is not available here, so emit exactly one line per step: "
        '<aib_tool>{"name":"aib_local","arguments":{"operation":"read","path":"<相對路徑>"}}</aib_tool> or '
        '<aib_tool>{"name":"aib_dm","arguments":{"target":"<對方給你的真實席碼>","message":"..."}}</aib_tool>.'
    ),
}

# 09-28 P1 r3:dm 停用後換上這份(不含 aib_dm 說明與 <aib_tool> aib_dm 範本;aib_local 文字協定照留)。
DM_SYSTEM_DM_OFF = {
    "role": "system",
    "content": (
        _aib_dm.GENE_HEAD + "You are an AIB seat named Teamo-Claude. "
        "For local development use aib_local (operations read/read_chunk/search/test/propose/write/git in your own worktree; "
        "write needs read + propose first). Tool calling is not available here, so emit exactly one line per step: "
        '<aib_tool>{"name":"aib_local","arguments":{"operation":"read","path":"<相對路徑>"}}</aib_tool>. '
        + _aib_dm.DM_DISABLED_NOTE
    ),
}


def run_aib_dm(target: str, message: str) -> str:
    return _aib_dm.run_aib_dm(target, message)


# §114.4b(the maintainer 09-27 23:4x/23:5x):與 DeepSeek/NVIDIA 同一套本機開發工具。Anthropic 原生端點本殼不送工具定義,
#   所以走文字工具協定 <aib_tool>{"name":"aib_local",...}</aib_tool>。
import deepseek_cli as _dev  # noqa: E402
LOCAL_TOOL = _dev.LOCAL_TOOL
TOOL_ROUNDS = _dev.TOOL_ROUNDS
DM_FAIL_DROP_TOOLS = 2
DM_HAMMER_STOP = 3      # 停用後仍硬打 dm(文字或正式)這麼多次 ⇒ 結束這一回合(不再耗回合)


def enforce_dm_truth(content: str, dm_outcomes) -> str:
    return _aib_dm.enforce_dm_truth(content, dm_outcomes)


def text_tool_call(content):
    return _aib_dm.text_tool_call(content)


def _bootstrap_on_path():
    # teamo_cli.py lives in src/cockpit/ → bootstrap is sibling under src/
    here = Path(__file__).resolve().parent
    boot = here.parent / "bootstrap"
    if str(boot) not in sys.path:
        sys.path.insert(0, str(boot))


def resolve_api_key():
    """TEAMCLAUDE_API_KEY / TEAMO_API_KEY env, then secret files (shared Teamo key OK)."""
    for name in ("TEAMCLAUDE_API_KEY", "TEAMO_API_KEY"):
        env_val = (os.environ.get(name) or "").strip()
        if env_val:
            return env_val
    _bootstrap_on_path()
    import aib_secrets as S  # noqa: E402
    last_err = None
    for rel, key in (
        ("api-key-teamoclaude.env", "TEAMCLAUDE_API_KEY"),
        ("api-key-teamoclaude.env", "TEAMO_API_KEY"),
        ("api-key-teamo.env", "TEAMO_API_KEY"),
    ):
        try:
            rows = S.load_file(rel)
        except S.SecretError as err:
            last_err = err
            continue
        raw = (rows.get(key) or "").strip()
        if not raw:
            continue
        try:
            import secret_crypto as C  # noqa: E402
            if C.is_envelope(raw):
                return C.decrypt_value(raw, key, S.secrets_dir())
        except Exception as err:  # noqa: BLE001
            raise SystemExit("teamoclaude: 金鑰解密失敗") from err
        return raw
    raise SystemExit(
        "teamoclaude: 缺少 TEAMO_API_KEY（或 TEAMCLAUDE_API_KEY）。"
        "可與 Teamo-Codex 共用 ~/.ez2-aib/secrets/api-key-teamo.env，"
        "或寫 api-key-teamoclaude.env / export TEAMO_API_KEY=…"
        + (("（讀櫃：%s）" % getattr(last_err, "reason", last_err)) if last_err else "")
    )


def resolve_base_url():
    """TEAMCLAUDE_BASE_URL / TEAMO_CLAUDE_BASE_URL / secrets; empty → DEFAULT_BASE."""
    for name in (BASE_ENV, "TEAMO_CLAUDE_BASE_URL"):
        env_val = (os.environ.get(name) or "").strip()
        if env_val:
            return env_val.rstrip("/")
    _bootstrap_on_path()
    import aib_secrets as S  # noqa: E402
    for rel, key in (
        ("api-key-teamoclaude.env", BASE_ENV),
        ("api-key-teamo.env", "TEAMO_CLAUDE_BASE_URL"),
    ):
        try:
            rows = S.load_file(rel)
        except Exception:
            continue
        raw = (rows.get(key) or "").strip()
        if raw:
            return raw.rstrip("/")
    return DEFAULT_BASE


def session_dir(session_id: str) -> Path:
    root = (
        os.environ.get("EZ2_AIB_STATE_ROOT")
        or os.environ.get("EZ2_AIB_HOME")
        or "~/.ez2-aib"
    )
    path = Path(os.path.expanduser(root)) / "teamoclaude-sessions" / session_id
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def load_history(path: Path):
    hist_path = path / "history.jsonl"
    messages = []
    if not hist_path.is_file():
        return messages
    for line in hist_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("role") in ("user", "assistant", "system"):
            content = row.get("content")
            if isinstance(content, str):
                messages.append({"role": row["role"], "content": content})
    return messages


def append_history(path: Path, role: str, content: str):
    hist_path = path / "history.jsonl"
    with open(hist_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"role": role, "content": content}, ensure_ascii=False) + "\n")
    try:
        os.chmod(hist_path, 0o600)
    except OSError:
        pass


def chat_completion(*, base_url: str, api_key: str, model: str, messages, stream: bool, tools=None):
    """Anthropic Messages API. `tools` ignored (DM via /dm + text-tool fallback)."""
    root = base_url.rstrip("/")
    url = root + ("/messages" if root.endswith("/v1") else "/v1/messages")
    system = None
    wire = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            chunk = msg.get("content") or ""
            system = (system + chr(10) + chunk) if system else chunk
            continue
        if role in ("user", "assistant"):
            wire.append({"role": role, "content": msg.get("content") or ""})
    payload = {
        "model": model,
        "messages": wire,
        "max_tokens": 8192,
        "stream": stream,
    }
    if system:
        payload["system"] = system
    _dev.apply_sampling(payload)   # 觀察 11:AIB_API_TEMPERATURE
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    return urllib.request.urlopen(req, timeout=180)


def stream_assistant(resp) -> str:
    parts = []
    while True:
        raw = resp.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").strip()
        if not line or line.startswith(":") or line.startswith("event:"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        typ = data.get("type")
        if typ == "content_block_delta":
            delta = data.get("delta") or {}
            if delta.get("type") == "text_delta":
                piece = delta.get("text") or ""
                if piece:
                    parts.append(piece)
                    sys.stdout.write(piece)
                    sys.stdout.flush()
        elif typ == "message_stop":
            break
    sys.stdout.write(chr(10))
    sys.stdout.flush()
    return "".join(parts)


def one_shot_assistant(resp) -> str:
    data = json.loads(resp.read().decode("utf-8"))
    content = data.get("content")
    parts = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
    elif isinstance(content, str):
        parts.append(content)
    text_out = "".join(parts)
    if text_out:
        sys.stdout.write(text_out if text_out.endswith(chr(10)) else text_out + chr(10))
        sys.stdout.flush()
    return text_out


def one_shot_message(resp):
    """Return full assistant message dict (content + tool_calls) for tool loops.

    09-28 修:本席的 chat_completion 打的是 Anthropic Messages API(回 content 區塊,沒有 choices),
    原本只讀 OpenAI 的 choices ⇒ 工具迴圈永遠拿到空字串(互動與單回合都一樣)。兩種格式都收;
    Anthropic 回覆沒有原生 tool_calls,工具走文字 <aib_tool> 後備(本席 docstring 本來就這樣設計)。"""
    data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data.get("content"), (list, str)) and not data.get("choices"):
        content = data.get("content")
        if isinstance(content, list):
            text = "".join(b.get("text") or "" for b in content if isinstance(b, dict) and b.get("type") == "text")
        else:
            text = content
        return {"role": "assistant", "content": text or "", "tool_calls": []}
    choices = data.get("choices") or []
    if not choices:
        return {"role": "assistant", "content": ""}
    msg = choices[0].get("message") or {}
    return {
        "role": "assistant",
        "content": msg.get("content") or "",
        "tool_calls": msg.get("tool_calls") or [],
    }


def assistant_turn(*, base_url: str, api_key: str, model: str, messages) -> str:
    """One model turn with native aib_dm tool calls (non-stream)."""
    wire = [_api_persona.with_persona(DM_SYSTEM)] + list(messages)   # 09-30:人格每一輪都在系統說明裡
    dm_outcomes = []
    workspace = _dev.LocalWorkspace(os.getcwd(), worktree_envs=("AIB_API_WORKTREE",))
    dm_fails = 0
    for attempt in range(TOOL_ROUNDS):
        if dm_fails >= DM_FAIL_DROP_TOOLS:
            wire[0] = _api_persona.with_persona(DM_SYSTEM_DM_OFF)     # 停用後不再教它寫 <aib_tool> aib_dm
        tools = ([_aib_dm.dm_tool(), LOCAL_TOOL] if dm_fails < DM_FAIL_DROP_TOOLS
                 else [LOCAL_TOOL] if dm_fails < DM_FAIL_DROP_TOOLS + 2 else None)   # 停用後還硬打 ⇒ 連工具都不給
        try:
            resp = chat_completion(
                base_url=base_url, api_key=api_key, model=model,
                messages=_dev.compact_wire(wire), stream=False, tools=tools)
        except Exception as err:  # noqa: BLE001
            return _dev.turn_error(err)   # 觀察 10:標出失敗種類
        with resp:
            reply = one_shot_message(resp)
        content = reply.get("content") or ""
        calls = reply.get("tool_calls") or []
        if not calls:
            explicit = text_tool_call(content)
            if explicit and explicit.get("name") == "aib_dm":
                args = explicit.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                # 文字 <aib_tool> 路徑與正式 tool_calls 同一套停用判斷(09-28 P1 r2:只擋正式路徑時,
                #   工具收回後模型改寫文字就照送,一輪 40 則)。
                if dm_fails >= DM_FAIL_DROP_TOOLS:
                    dm_fails += 1
                    if dm_fails >= DM_FAIL_DROP_TOOLS + DM_HAMMER_STOP:
                        break
                    outcome = "工具未執行：aib_dm 已停用,請直接用文字回答使用者,不要再寫 <aib_tool> aib_dm。"
                else:
                    outcome = run_aib_dm(args.get("target", ""), args.get("message", ""))
                    dm_outcomes.append(outcome)
                    if outcome.startswith("DM 未送出"):
                        dm_fails += 1
                        if dm_fails >= DM_FAIL_DROP_TOOLS:
                            outcome += "(本回合已失敗 %d 次:接下來不再提供 aib_dm,請直接用文字回答使用者,不要再寫 <aib_tool> aib_dm。)" % dm_fails
                wire.append({"role": "assistant", "content": content})
                wire.append({"role": "user", "content": "aib_dm result: " + outcome})
                continue
            if explicit and explicit.get("name") == "aib_local":
                args = explicit.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                outcome = workspace.execute(args if isinstance(args, dict) else {})
                wire.append({"role": "assistant", "content": content})
                wire.append({"role": "user", "content": "aib_local result: " + outcome[:6000]})
                continue
            final = _dev.evidence_note(workspace, enforce_dm_truth(content, dm_outcomes))
            sys.stdout.write(final + ("\n" if not final.endswith("\n") else ""))
            sys.stdout.flush()
            return final
        wire.append({
            "role": "assistant",
            "content": content,
            "tool_calls": calls,
        })
        for call in calls:
            if dm_fails >= DM_FAIL_DROP_TOOLS + DM_HAMMER_STOP:
                # 09-28 P1 r3c Codex 中 2:同一批裡達到硬打上限後,後面的呼叫(含 aib_local 寫檔/git)一律不執行;
                #   每個呼叫仍回一則 tool 結果(API 要求每個 tool_call_id 都有回應)。
                wire.append({"role": "tool", "tool_call_id": call.get("id") or ("call-%d" % attempt),
                             "content": "工具未執行：這一回合已停止(aib_dm 停用後仍一直要求送訊息),同一批剩下的工具呼叫都不執行。"})
                continue
            fn = call.get("function") or {}
            raw_args = fn.get("arguments") or {}
            if isinstance(raw_args, dict):
                args = raw_args
            else:
                try:
                    args = json.loads(raw_args)
                except (TypeError, ValueError):
                    args = {}
            if fn.get("name") == "aib_dm" and dm_fails >= DM_FAIL_DROP_TOOLS:
                dm_fails += 1              # 已停用還硬打:不執行、不跑 aib dm
                outcome = "工具未執行：aib_dm 已停用,請直接用文字回答使用者。"
            elif fn.get("name") == "aib_dm":
                outcome = run_aib_dm(args.get("target", ""), args.get("message", ""))
                dm_outcomes.append(outcome)
                if outcome.startswith("DM 未送出"):
                    dm_fails += 1
            elif fn.get("name") == "aib_local":
                outcome = workspace.execute(args)
            else:
                outcome = "工具未執行：不支援的工具。"
            wire.append({
                "role": "tool",
                "tool_call_id": call.get("id") or ("call-%d" % attempt),
                "content": outcome,
            })
        if dm_fails >= DM_FAIL_DROP_TOOLS + DM_HAMMER_STOP:
            break     # 停用後仍硬打正式 dm:不送,也別耗光回合(09-28 P1 r3 Codex 中)
    stop = ("aib_dm 已停用,模型仍一直要求送訊息;這一回合先停。請直接用文字再問一次。"
            if dm_fails >= DM_FAIL_DROP_TOOLS + DM_HAMMER_STOP
            else "工具回合用完(%d 次),這件還沒做完;請回我「繼續」,我接著做。" % TOOL_ROUNDS)
    final = _dev.evidence_note(workspace, enforce_dm_truth(stop, dm_outcomes))
    sys.stdout.write(final + "\n")
    sys.stdout.flush()
    return final



def _model_created_ts(row) -> int:
    """Best-effort listing/update epoch from OpenAI-compat model row."""
    if not isinstance(row, dict):
        return 0
    for key in ("updated_at", "updated", "created_at", "created"):
        raw = row.get(key)
        if raw is None or raw == "":
            continue
        if isinstance(raw, (int, float)):
            return int(raw)
        if isinstance(raw, str):
            s = raw.strip()
            if s.isdigit():
                return int(s)
            # ISO-ish
            try:
                from datetime import datetime
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                return int(datetime.fromisoformat(s).timestamp())
            except Exception:
                continue
    return 0


def _fmt_model_day(ts: int) -> str:
    """YYYY-MM-DD in local tz; placeholder when provider sends a bogus epoch."""
    if not ts or ts < 1_500_000_000:  # before ~2017-07 → treat as unreliable
        return "—"
    try:
        from datetime import datetime
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return "—"

def list_models(*, base_url: str, api_key: str):
    """GET /v1/models — newest-first rows as (id, created_ts).

    Sort by created/updated desc; ties keep provider order (not A–Z).
    Accept base with or without /v1.
    """
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        url = root + "/models"
    else:
        url = root + "/v1/models"
    req = urllib.request.Request(
        url,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    rows = []
    for idx, row in enumerate(payload.get("data") or []):
        mid = row.get("id") if isinstance(row, dict) else None
        if not (isinstance(mid, str) and mid.strip()):
            continue
        mid = mid.strip()
        ts = _model_created_ts(row)
        rows.append((ts, idx, mid))
    # newest first; same ts → original API order
    rows.sort(key=lambda r: (-r[0], r[1]))
    # de-dupe by id keeping first (newest / earliest API index)
    seen = set()
    out = []
    for ts, _idx, mid in rows:
        if mid in seen:
            continue
        seen.add(mid)
        out.append((mid, ts))
    return out

def run_repl(*, api_key: str, model: str, base_url: str, sess: Path, messages):
    from bridge_cards import ReplFrame
    _frame = ReplFrame("teamoclaude")
    sys.stdout.write(
        "Teamo-Claude · model=%s · session=%s\n"
        "（API key；/models [關鍵字] 列模型（新→舊＋日期）、/model <id> 切換、/model 看現用；"
        "Ctrl-D /exit /quit 結束）\n"
        "%s\n"
        % (model, sess.name, _aib_dm.dm_boot_banner() + _aib_dm.gene_banner())
    )
    sys.stdout.flush()
    _api_model_gauge.record(sess.name, model, "teamoclaude")  # 開 REPL:蓋掉上一個行程切過的模型
    while True:
        try:
            _frame.close()
            line = input("Teamo-Claude> ")
        except EOFError:
            sys.stdout.write("\n")
            break
        except KeyboardInterrupt:
            sys.stdout.write("\n")
            break
        text = line.strip()
        if not text:
            continue
        if text in ("/exit", "/quit", "/q"):
            break
        if text in ("/help", "/?"):
            sys.stdout.write(
                "指令：/models [關鍵字]  ·  /model <id> 切換  ·  /model 現用  ·  "
                "/dm <艙位碼/席碼> <訊息>  ·  /exit 結束\n"
            )
            sys.stdout.flush()
            continue
        if text == "/model" or text.startswith("/model "):
            parts = text.split(None, 1)
            if len(parts) == 1:
                sys.stdout.write("現用模型：%s\n" % model)
                sys.stdout.flush()
                continue
            new_m = parts[1].strip()
            if not new_m:
                sys.stdout.write("用法：/model <id>\n")
                sys.stdout.flush()
                continue
            model = new_m
            _api_model_gauge.record(sess.name, model, "teamoclaude")  # /model 切換:右欄跟著變
            sys.stdout.write("已切換模型 → %s（之後回合都用這個）\n" % model)
            sys.stdout.flush()
            continue
        if text == "/models" or text.startswith("/models "):
            filt = text.split(None, 1)[1].strip().lower() if " " in text else ""
            try:
                ids = list_models(base_url=base_url, api_key=api_key)
            except urllib.error.HTTPError as err:
                detail = err.read().decode("utf-8", errors="replace")[:300]
                sys.stderr.write("teamoclaude: /models HTTP %s — %s\n" % (err.code, detail))
                continue
            except urllib.error.URLError as err:
                sys.stderr.write("teamoclaude: /models 連線失敗 — %s\n" % err.reason)
                continue
            if filt:
                rows = [(i, ts) for i, ts in ids if filt in i.lower()]
            else:
                rows = list(ids)
            if not rows:
                sys.stdout.write("（沒有符合的模型）\n")
            else:
                sys.stdout.write(
                    "可選模型 %d 個%s（新→舊；右側為上架／更新日期）：\n"
                    % (len(rows), ("（含 %r）" % filt) if filt else "")
                )
                for mid, ts in rows:
                    mark = " *" if mid == model else ""
                    sys.stdout.write(
                        "  %s  %s%s\n" % (mid, _fmt_model_day(ts), mark)
                    )
            sys.stdout.flush()
            continue

        slash = _aib_dm.parse_dm_slash(text)
        if slash:
            target, body = slash
            outcome = run_aib_dm(target, body)
            sys.stdout.write(outcome + "\n")
            sys.stdout.flush()
            continue
        _frame.open()   # 10-09 對話框:品牌標題列(非 TTY 不印)
        messages.append({"role": "user", "content": text})
        append_history(sess, "user", text)
        sys.stdout.write("teamoclaude> ")
        sys.stdout.flush()
        try:
            content = assistant_turn(
                base_url=base_url, api_key=api_key, model=model,
                messages=messages)
        except urllib.error.HTTPError as err:
            raw = err.read().decode("utf-8", errors="replace")
            detail = raw[:400]
            if "cloudflare" in raw.lower() or "error code: 1010" in raw or "<!doctype html>" in raw.lower():
                detail = (
                    "Cloudflare 擋了請求（常是 UA／IP）。本 CLI 已帶 User-Agent；"
                    "若仍 403，多半是額度或儀表板限制。原始片段：%s"
                    % raw[:160].replace("\n", " ")
                )
            elif raw.lstrip().startswith("{"):
                try:
                    ej = json.loads(raw)
                    em = (ej.get("error") or {}).get("message") or raw[:200]
                    detail = em
                except Exception:
                    pass
            sys.stderr.write("teamoclaude: API HTTP %s — %s\n" % (err.code, detail))
            messages.pop()
            continue
        except urllib.error.URLError as err:
            sys.stderr.write("teamoclaude: 連線失敗 — %s\n" % err.reason)
            messages.pop()
            continue
        except Exception as err:  # noqa: BLE001
            sys.stderr.write("teamoclaude: 回合失敗 — %s\n" % err)
            messages.pop()
            continue
        if content:
            messages.append({"role": "assistant", "content": content})
            append_history(sess, "assistant", content)


def build_parser():
    p = argparse.ArgumentParser(prog="teamoclaude", description="Teamo-Claude (TeamoRouter Anthropic) API-key AIB seat")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--session-id", default="")
    p.add_argument("--cwd", default="")
    p.add_argument("--role", default="")
    p.add_argument("--mode", choices=("safe", "dgrs"), default="safe",
                   help="dgrs 對 API-key 席無沙箱意義；誠實接受旗標但不改權限模型")
    p.add_argument("--base-url", default=None, help="override TEAMCLAUDE_BASE_URL / secrets")
    p.add_argument("--prompt", default="", help="若給定則單回合後結束（測試用）")
    p.add_argument("--no-stream", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cwd:
        try:
            os.chdir(args.cwd)
        except OSError as err:
            raise SystemExit("teamoclaude: 無法進入目錄 %s: %s" % (args.cwd, err)) from err
    api_key = resolve_api_key()
    base_url = args.base_url or resolve_base_url()
    sid = args.session_id.strip() or "anon"
    _api_persona.set_session(sid)   # 09-30:這一席綁的人格(鍵是 sid 尾五碼)
    sess = session_dir(sid)
    messages = load_history(sess)
    if args.prompt:
        messages.append({"role": "user", "content": args.prompt})
        append_history(sess, "user", args.prompt)
        # 觀察 1(09-28):單回合也走開發工具迴圈(讀檔、改檔、跑測試),與 deepseek 一致;
        # 排程與每日實測才叫得動它做事。--no-stream 仍接受(工具迴圈本來就不串流)。
        content = assistant_turn(base_url=base_url, api_key=api_key, model=args.model, messages=messages)
        if content:
            sys.stdout.write(content + ("" if content.endswith("\n") else "\n"))
            sys.stdout.flush()
        if content:
            append_history(sess, "assistant", content)
        return 0
    run_repl(
        api_key=api_key, model=args.model, base_url=base_url,
        sess=sess, messages=messages)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
