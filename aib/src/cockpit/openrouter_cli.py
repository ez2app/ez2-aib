#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""OpenRouter (OpenRouter OpenAI-compat) API-key terminal seat.

AIB opens this like other providers (tmux pane), but auth is OPENROUTER_API_KEY
from the secrets vault / env — not subscription OAuth CLI (ED.4).

OpenAI-compatible Chat Completions via https://api.openrouterrouter.com/v1/
(Claude/Anthropic native base is https://api.openrouterrouter.com — see SOP; this CLI uses OpenAI-compat).
"""
from __future__ import annotations

import repl_readline as _repl_rl
_repl_rl.enable_repl_readline()

import argparse
import json
import os
import time
import sys
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "memory"))
import api_tidy as _api_tidy  # noqa: E402  10-13 依用量自動精煉(API 席每 20 輪)
import urllib.error
import urllib.request
from pathlib import Path
import aib_dm_tool as _aib_dm
import api_persona as _api_persona
import api_model_gauge as _api_model_gauge  # 10-01:右欄顯示這一席現在送出去的模型

DEFAULT_BASE = "https://openrouter.ai/api/v1"  # OpenAI SDK base; Claude native root is without /v1
DEFAULT_MODEL = "openai/gpt-4o-mini"
# Cloudflare 擋 Python-urllib 預設 UA（error 1010）；必須自報身分。
USER_AGENT = "aib-openrouter/0.62 (+Ez2-AIB; OpenRouter OpenAI-compat)"
KEY_NAME = "OPENROUTER_API_KEY"
BASE_ENV = "OPENROUTER_BASE_URL"


def _build_system():
    """回 (DM_SYSTEM, DM_SYSTEM_DM_OFF)。10-14 語系盤查第 6 條:assistant_turn 每一輪照當下語系重組
    (原本 import 時就組好 ⇒ 長駐的 REPL 之後切語系不會跟)。"""
    DM_SYSTEM = {
        "role": "system",
        "content": (
            _aib_dm.GENE_HEAD + "You are an AIB seat. " + _aib_dm.dm_system_snippet() + " "
            "You can assist local development with aib_local (read/search/test/write/git in your own worktree, "
            "same rules as described in the tool). Only call aib_dm when a human or seat gave you a real seat code; "
            "otherwise answer in plain text. "
            "If tool calling is unavailable, emit exactly one line "
            '<aib_tool>{"name":"aib_dm","arguments":{"target":"<對方給你的真實席碼>","message":"..."}}</aib_tool>.'
        ),
    }
    # 09-28 P1 r3:dm 停用後換上這份(不含 aib_dm 說明與 <aib_tool> aib_dm 範本)。
    DM_SYSTEM_DM_OFF = {
        "role": "system",
        "content": (
            _aib_dm.GENE_HEAD + "You are an AIB seat. "
            "You can assist local development with aib_local (read/search/test/write/git in your own worktree, "
            "same rules as described in the tool). " + _aib_dm.DM_DISABLED_NOTE
        ),
    }
    return DM_SYSTEM, DM_SYSTEM_DM_OFF


def __getattr__(name):
    # 舊的模組層名字(測試、外部讀 openrouter_cli.DM_SYSTEM)照樣讀得到,但每次都是當下語系組的
    if name in ('DM_SYSTEM', 'DM_SYSTEM_DM_OFF'):
        return _build_system()[name == 'DM_SYSTEM_DM_OFF']
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


_UI_TR = {}


def _ui(key, **params):
    """畫面字走四語鍵(語系盤查,10-14);每次照 i18n_lib.resolve_locale() 取(長駐程式切語系也跟得上),讀不到印鍵名。"""
    try:
        _cockpit = os.path.dirname(os.path.abspath(__file__))
        if _cockpit not in sys.path:
            sys.path.insert(0, _cockpit)
        import i18n_lib
        loc = i18n_lib.resolve_locale()
        tr = _UI_TR.get(loc)
        if tr is None:
            tr = _UI_TR[loc] = i18n_lib.Translator(loc)
        return tr.t(key, params or None)
    except Exception:  # noqa: BLE001 —— 印不出字不能讓程式停
        return key


def run_aib_dm(target: str, message: str) -> str:
    return _aib_dm.run_aib_dm(target, message)


# §114.4b(the maintainer 09-27 23:4x「所有的 ai 都應該套用這個模式」;23:5x「所有非 Oauth 的 ai…不能就預設成現在改好可用的模式」):
#   與 DeepSeek/NVIDIA 同一套本機開發工具;P3 再抽成 api_repl_core。
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
    # openrouter_cli.py lives in src/cockpit/ → bootstrap is sibling under src/
    here = Path(__file__).resolve().parent
    boot = here.parent / "bootstrap"
    if str(boot) not in sys.path:
        sys.path.insert(0, str(boot))



def openrouter_extra_headers(rows=None):
    """Optional OpenRouter ranking headers (HTTP-Referer / X-Title)."""
    h = {}
    ref = (os.environ.get("OPENROUTER_HTTP_REFERER") or "").strip()
    title = (os.environ.get("OPENROUTER_APP_TITLE") or "").strip()
    if rows:
        if not ref:
            ref = (rows.get("OPENROUTER_HTTP_REFERER") or "").strip()
        if not title:
            title = (rows.get("OPENROUTER_APP_TITLE") or "").strip()
    if ref:
        h["HTTP-Referer"] = ref
    if title:
        h["X-Title"] = title
    return h

def resolve_api_key():
    """Env first, then secrets/api-key-openrouter.env (decrypt if enc.v1.). Never print the value."""
    env_val = (os.environ.get(KEY_NAME) or "").strip()
    if env_val:
        return env_val
    _bootstrap_on_path()
    import aib_secrets as S  # noqa: E402
    rel = S.file_for_name(KEY_NAME) or "api-key-openrouter.env"
    try:
        rows = S.load_file(rel)
    except S.SecretError as err:
        raise SystemExit(_ui("aib.apirepl.vault_unreadable", provider="openrouter", path=rel, err=getattr(err, "reason", err), name=KEY_NAME)) from err
    raw = (rows.get(KEY_NAME) or "").strip()
    if not raw:
        raise SystemExit(_ui("aib.apirepl.key_missing", provider="openrouter", name=KEY_NAME, path=rel))
    try:
        import secret_crypto as C  # noqa: E402
        if C.is_envelope(raw):
            d = S.secrets_dir()
            return C.decrypt_value(raw, KEY_NAME, d)
    except Exception as err:  # noqa: BLE001
        raise SystemExit(_ui("aib.apirepl.decrypt_fail", provider="openrouter")) from err
    return raw


def resolve_base_url():
    """Env OPENROUTER_BASE_URL first, then secrets file; empty → DEFAULT_BASE."""
    env_val = (os.environ.get(BASE_ENV) or "").strip()
    if env_val:
        return env_val
    _bootstrap_on_path()
    import aib_secrets as S  # noqa: E402
    rel = S.file_for_name(KEY_NAME) or "api-key-openrouter.env"
    try:
        rows = S.load_file(rel)
    except Exception:
        return DEFAULT_BASE
    raw = (rows.get(BASE_ENV) or "").strip()
    return raw or DEFAULT_BASE


def session_dir(session_id: str) -> Path:
    root = (
        os.environ.get("EZ2_AIB_STATE_ROOT")
        or os.environ.get("EZ2_AIB_HOME")
        or "~/.ez2-aib"
    )
    path = Path(os.path.expanduser(root)) / "openrouter-sessions" / session_id
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
        fh.write(json.dumps({"role": role, "content": content, "ts": int(time.time())}, ensure_ascii=False) + "\n")   # ts:10-09 重放卡要印時間
    try:
        os.chmod(hist_path, 0o600)
    except OSError:
        pass


def chat_completion(*, base_url: str, api_key: str, model: str, messages, stream: bool, tools=None):
    # Docs 給 SDK 的是 …/v1/；本 CLI 自己加 /v1/chat/completions。
    # 兩邊都收：有尾 /v1 就只加 /chat/completions，避免 /v1/v1/…
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        url = root + "/chat/completions"
    else:
        url = root + "/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "max_tokens": int(os.environ.get("OPENROUTER_MAX_TOKENS") or "2048"),
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    _dev.apply_sampling(payload)   # 觀察 11:AIB_API_TEMPERATURE
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": USER_AGENT,
            **openrouter_extra_headers(),
        },
    )
    return urllib.request.urlopen(req, timeout=120)


def stream_assistant(resp) -> str:
    parts = []
    while True:
        raw = resp.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                data = json.loads(payload)
            except ValueError:
                continue
            choices = data.get("choices") or []
            if not choices:
                continue
            dlt = choices[0].get("delta") or {}
            delta = dlt.get("content") or dlt.get("reasoning_content")
            if delta:
                parts.append(delta)
                sys.stdout.write(delta)
                sys.stdout.flush()
    sys.stdout.write("\n")
    sys.stdout.flush()
    return "".join(parts)


def one_shot_assistant(resp) -> str:
    data = json.loads(resp.read().decode("utf-8"))
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    content = msg.get("content") or msg.get("reasoning_content") or ""
    if not isinstance(content, str):
        content = ""
    sys.stdout.write(content + ("\n" if not content.endswith("\n") else ""))
    sys.stdout.flush()
    return content


def one_shot_message(resp):
    """Return full assistant message dict (content + tool_calls) for tool loops."""
    data = json.loads(resp.read().decode("utf-8"))
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
    DM_SYSTEM, DM_SYSTEM_DM_OFF = _build_system()   # 每一輪照當下語系重組(語系盤查第 6 條)
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
                    if dm_fails >= DM_FAIL_DROP_TOOLS:
                        outcome += "(本回合已失敗 %d 次:接下來不再提供 aib_dm,請直接用文字回答使用者。)" % dm_fails
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
            "Authorization": "Bearer %s" % api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            **openrouter_extra_headers(),
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

def _bc_seat(sess: Path) -> str:
    try:
        from seat_address import uuid_tail5
        return uuid_tail5(sess.name) or ""
    except Exception:  # noqa: BLE001
        return ""


def _bc_replay(sess: Path, kind: str) -> int:
    try:
        import bridge_cards as _bc
        return _bc.replay_history_file(sess, kind)
    except Exception:  # noqa: BLE001
        return 0


def run_repl(*, api_key: str, model: str, base_url: str, sess: Path, messages):
    from bridge_cards import ReplFrame
    _frame = ReplFrame("openrouter", seat=_bc_seat(sess))   # 10-09:標題列帶席碼,跟橋接席的卡一致
    sys.stdout.write(_ui("aib.apirepl.boot", provider="openrouter", value=model, session=sess.name) + "\n" + _aib_dm.dm_boot_banner() + _aib_dm.gene_banner() + "\n")
    sys.stdout.flush()
    _api_model_gauge.record(sess.name, model, "openrouter")  # 開 REPL:蓋掉上一個行程切過的模型
    _bc_replay(sess, "openrouter")                               # 10-09:開席重放 history.jsonl 最近幾則(橋接席已有,API 席補齊)
    while True:
        try:
            _frame.close()
            line = input("OpenRouter> ")
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
            sys.stdout.write(_ui("aib.apirepl.help") + "\n")
            sys.stdout.flush()
            continue
        if text == "/model" or text.startswith("/model "):
            parts = text.split(None, 1)
            if len(parts) == 1:
                sys.stdout.write(_ui("aib.apirepl.current_model", value=model) + "\n")
                sys.stdout.flush()
                continue
            new_m = parts[1].strip()
            if not new_m:
                sys.stdout.write(_ui("aib.apirepl.model_usage") + "\n")
                sys.stdout.flush()
                continue
            model = new_m
            _api_model_gauge.record(sess.name, model, "openrouter")  # /model 切換:右欄跟著變
            sys.stdout.write(_ui("aib.apirepl.model_switched", value=model) + "\n")
            sys.stdout.flush()
            continue
        if text == "/models" or text.startswith("/models "):
            filt = text.split(None, 1)[1].strip().lower() if " " in text else ""
            try:
                ids = list_models(base_url=base_url, api_key=api_key)
            except urllib.error.HTTPError as err:
                detail = err.read().decode("utf-8", errors="replace")[:300]
                sys.stderr.write("openrouter: /models HTTP %s — %s\n" % (err.code, detail))
                continue
            except urllib.error.URLError as err:
                sys.stderr.write(_ui("aib.apirepl.models_net_fail", provider="openrouter", err=err.reason) + "\n")
                continue
            if filt:
                rows = [(i, ts) for i, ts in ids if filt in i.lower()]
            else:
                rows = list(ids)
            if not rows:
                sys.stdout.write(_ui("aib.apirepl.no_models") + "\n")
            else:
                sys.stdout.write(_ui("aib.apirepl.models_head", n=len(rows), detail=_ui("aib.apirepl.models_filter", value=repr(filt)) if filt else "") + "\n")
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
        sys.stdout.write("openrouter> ")
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
            sys.stdout.write("\n" + _ui("aib.apirepl.http_error", provider="openrouter", rc=err.code, detail=detail) + "\n")
            sys.stdout.flush()
            sys.stderr.write("openrouter: API HTTP %s — %s\n" % (err.code, detail))
            messages.pop()
            continue
        except urllib.error.URLError as err:
            sys.stderr.write(_ui("aib.apirepl.net_fail", provider="openrouter", err=err.reason) + "\n")
            messages.pop()
            continue
        except Exception as err:  # noqa: BLE001
            sys.stderr.write(_ui("aib.apirepl.turn_fail", provider="openrouter", err=err) + "\n")
            messages.pop()
            continue
        if content:
            messages.append({"role": "assistant", "content": content})
            append_history(sess, "assistant", content)
            # 10-13 a seat(the maintainer「API 席和 Bot 也要能自動更新」「每 20 輪」):這一席的模型不能跑指令 ⇒ 程式代跑整理記憶
            #   (草稿只帶要改的段 + 最後幾則對話;不帶工具的單次呼叫;填壞就丟、不重試、不擋下一則;src/memory/api_tidy.py)
            # 10-15 CTX 用量表:沒有 hook 機制 ⇒ 外層(本 REPL)每一輪代呼叫 stop 那條路;量不到用量 ⇒ 每 N 輪
            #   (policy 表 unmeasured_tidy_every_turns)回 True ⇒ 代跑整理記憶(src/memory/api_tidy.py turn_end)
            if _api_tidy.turn_end(sess.name):
                sys.stdout.write(_api_tidy.run(lambda p: one_shot_assistant(chat_completion(
                    base_url=base_url, api_key=api_key, model=model, stream=False,
                    messages=_api_tidy.recent(messages) + [{"role": "user", "content": p}]))) + "\n")
                sys.stdout.flush()
            # 10-13 a seat 第 2 層影子續跑說明:這一席跑過 aib test / 推包還沒回報 ⇒ 程式把那段印在回覆下面(不靠模型抄)
            _ship = _api_tidy.ship_segment()
            if _ship:
                sys.stdout.write(_ship + "\n")
                sys.stdout.flush()


def build_parser():
    p = argparse.ArgumentParser(prog="openrouter", description="OpenRouter (OpenRouter OpenAI) API-key AIB seat")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--session-id", default="")
    p.add_argument("--cwd", default="")
    p.add_argument("--role", default="")
    p.add_argument("--mode", choices=("safe", "dgrs"), default="safe",
                   help="dgrs 對 API-key 席無沙箱意義；誠實接受旗標但不改權限模型")
    p.add_argument("--base-url", default=None, help="override OPENROUTER_BASE_URL / secrets")
    p.add_argument("--prompt", default="", help="若給定則單回合後結束（測試用）")
    p.add_argument("--no-stream", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cwd:
        try:
            os.chdir(args.cwd)
        except OSError as err:
            raise SystemExit(_ui("aib.apirepl.cwd_fail", provider="openrouter", path=args.cwd, err=err)) from err
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
