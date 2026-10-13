#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""分出新席(fork):新開一個獨一無二的席位,載入舊對話、舊人格、舊第二大腦重新運行。

裁定史(碼裡照抄,這是裁定變更不是 bug;全文 docs/DESIGN-seat-fork-unique-0924.md §3):
  09-14 事故復原:接回既有對話 = 同 uuid 同碼(原地復活;本檔不管,見 aib 的 AIB_REVIVE_SESSION_UUID)
  09-21 the maintainer:新開/右鍵新開預設接回原對話,不要空白新 session
  09-24 20:4x the maintainer:開席跳選單列舊席位,30 秒未選 ⇒ 空白開席(推翻 09-21 的「逾時接回」)
  09-24 21:xx the maintainer 澄清:接回 = **新開獨一無二的席位**、載入舊對話/舊人格/舊第二大腦、重新運行
事故:09-21 的實作沿用了 09-14 的同碼機制,同一段對話被 6 個窗用過、2 個活行程共用。

本檔四件事(aib 開席取碼段呼叫;provider-launch 只消費 prepare 產出的兩個值):
  decide_mode   fork(`--resume 舊 --fork-session --session-id 新`)或 pack(對話快滿:不整段載入,
                改把交接單+第二大腦+最近幾輪當第一句)。the maintainer 09-24 Q1:剩不到 30% ⇒ pack
  copy_memory   第二大腦 memory/<舊五碼>/ **複製**到 memory/<新五碼>/(不連結:兩席同寫一份記憶=同一個洞)
  carry_binding 人格卡與大腦路由走 bind_one.py 綁到新席(有帳本、有投影、驗契約;不手改 JSON)
  preamble      新席第一句:你是 @新碼、對話裡的 @舊碼 是前身。只換碼不告知,新席照樣自稱舊碼
                (09-24 @27 就是這樣把派工信的你我寫反)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import seat_liveness  # noqa: E402

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PACK_BELOW_REMAINING = 0.30          # the maintainer 09-24 Q1
SMALL_WINDOW = 200_000
LARGE_WINDOW = 1_000_000


# ── 所有 AI 同一個「載入舊席位」(the maintainer 09-26:「其他所有AI都全部要照這樣程式化」)──────────────
#   人格(carry_binding)與第二大腦(copy_memory)每一家都一樣帶;差別只在「上一場紀錄」怎麼接:
#     claude   原生分出:`--resume 舊 --fork-session --session-id 新`(或對話快滿時打包成第一句)
#     history  API-key 席(本倉自己的 REPL):舊 history.jsonl 複製給新席,最後補一列 system 說明
#     pack     codex/grok/copilot/agy:CLI 不收「分出成新 id」⇒ 不重起,投一則開場說明(含最近幾輪)
#     persona  橋接 bot:對話在桌面 App,沿用它自己的「綁哪條對話」選單;只帶人格與第二大腦
API_SESSIONS = {"deepseek": "deepseek-sessions", "nvidia": "nvidia-sessions",
                "openrouter": "openrouter-sessions", "typesafe": "typesafe-sessions",
                "openrouterjev": "typesafe-sessions"}          # openrouterjev 跑的是 typesafe_cli.py --route openrouter
BOT_PROVIDERS = frozenset(("grokbot", "codexbot", "sand", "claudebot", "musebot"))


def load_kind(provider: str) -> str:
    if provider == "claude":
        return "claude"
    if provider in API_SESSIONS:
        return "history"
    if provider in BOT_PROVIDERS:
        return "persona"
    return "pack"


def api_session_root(aib_home: str) -> Path:
    # 與 *_cli.py 的 session_dir() 同一條優先序
    return Path(os.path.expanduser(os.environ.get("EZ2_AIB_STATE_ROOT") or aib_home or "~/.ez2-aib"))


def find_record(provider: str, uuid: str, aib_home: str = "", config_dir: Path | None = None) -> Path | None:
    """那一席「上一場紀錄」的檔。找不到回 None(不拿別席的充數)。"""
    if not UUID_RE.fullmatch(uuid or ""):
        return None
    if provider == "claude":
        return find_transcript(uuid, config_dir)
    hits: list[str] = []
    if provider == "codex":
        root = os.environ.get("EZ2_CODEX_SESSIONS_ROOT") or os.path.join(
            os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "sessions")
        hits = glob.glob(os.path.join(root, "**", "rollout-*-%s.jsonl" % uuid), recursive=True)
    elif provider == "grok":
        hits = glob.glob(os.path.expanduser("~/.grok/sessions/*/%s/chat_history.jsonl" % uuid))
    elif provider == "copilot":
        hits = glob.glob(os.path.expanduser("~/.copilot/session-state/%s/events.jsonl" % uuid))
    elif provider in API_SESSIONS:
        p = api_session_root(aib_home) / API_SESSIONS[provider] / uuid / "history.jsonl"
        hits = [str(p)] if p.is_file() else []
    return Path(sorted(hits, key=os.path.getmtime)[-1]) if hits else None


def _flat_text(c) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(p.get("text", "") for p in c if isinstance(p, dict) and isinstance(p.get("text"), str))
    if isinstance(c, dict):
        return _flat_text(c.get("content") if "content" in c else c.get("text"))
    return ""


def record_turns(provider: str, path: Path | None) -> list[tuple[str, str]]:
    """(user|assistant, 文字) 依時間序。各家格式不同;認不得的列略過,不猜。"""
    if path is None:
        return []
    out = []
    for d in _rows(path):
        role, text = "", ""
        if provider == "claude":
            if d.get("isMeta") or d.get("isSidechain"):
                continue
            role, text = d.get("type") or "", _text_of(d.get("message"))
        elif provider == "codex":
            pl = d.get("payload") if isinstance(d.get("payload"), dict) else {}
            if d.get("type") == "response_item" and pl.get("type") == "message":
                role, text = pl.get("role") or "", _flat_text(pl.get("content"))
        elif provider == "copilot":
            t = d.get("type") or ""
            data = d.get("data") if isinstance(d.get("data"), dict) else {}
            if t == "user.message":
                role, text = "user", _flat_text(data.get("content") or data)
            elif t == "assistant.message":
                role, text = "assistant", _flat_text(data.get("content") or data)
        else:                                   # grok chat_history、API-key history.jsonl
            role = d.get("role") or d.get("type") or ""
            text = _flat_text(d.get("content") if "content" in d else d.get("message"))
        if role in ("user", "assistant") and text.strip():
            out.append((role, text))
    return out


def record_model(provider: str, path: Path | None) -> str:
    """那一場最後用的模型(the maintainer 09-26:選單每一列最左邊要帶上一次的 AI 模型)。讀不到回 ""。"""
    if path is None:
        return ""
    last = ""
    for d in _rows(path):
        m = ""
        if provider == "claude":
            if d.get("isSidechain"):                       # 子代理那一列:模型可能不同,不算這一席的(10-10)
                continue
            msg = d.get("message") if isinstance(d.get("message"), dict) else {}
            m = msg.get("model") or ""
            if m.startswith("<"):                          # <synthetic> 不是模型
                m = ""
        elif provider == "codex":
            if d.get("type") == "turn_context" and isinstance(d.get("payload"), dict):
                m = d["payload"].get("model") or ""
        elif provider == "copilot":
            data = d.get("data") if isinstance(d.get("data"), dict) else {}
            if d.get("type") == "assistant.message":
                m = data.get("model") or ""
            elif d.get("type") == "session.model_change":
                m = data.get("newModel") or data.get("model") or ""
        else:                                              # grok;API-key 席的紀錄不記模型(退回席位檔)
            m = d.get("model_id") or d.get("model") or ""
        if isinstance(m, str) and m:
            last = m
    if provider == "grok":
        last = _grok_event_model(path) or last
    return last


def _grok_event_model(path: Path) -> str:
    """10-11:grok chat_history 的 model_id 是後端組建名(grok-4.6-build),`grok -m` 不收;
    同資料夾 events.jsonl 每回合 turn_started 記的才是選用名(grok-4.6;中途換模型也記得到)。沒有就回 ""。"""
    ev = Path(path).with_name("events.jsonl")
    if not ev.is_file():
        return ""
    last = ""
    for d in _rows(ev):
        m = d.get("model_id") if d.get("type") == "turn_started" else ""
        if isinstance(m, str) and m:
            last = m
    return last


REVIVE_LAYER_RECORD = "record"          # 對話紀錄最後用的
REVIVE_LAYER_SEAT_FILE = "seat_file"    # 舊席位檔開席時要求的


def seat_record_for(uuid: str, aib_home: str) -> dict:
    """登記簿裡這個 uuid 最後一份席位檔(所有艙的活席登記簿 + retired/);沒有回 {}。
    「最後」以 started_epoch 判,沒有就用檔案 mtime。只讀,不碰持有判定(那是 seat_liveness 的事)。"""
    if not UUID_RE.fullmatch(uuid or "") or not aib_home:
        return {}
    best, best_when = {}, -1.0
    for sd in seat_liveness.all_seat_dirs(aib_home):
        for f in glob.glob(os.path.join(sd, "w*.json")) + glob.glob(os.path.join(sd, "retired", "*.json")):
            try:
                with open(f, encoding="utf-8") as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict) or rec.get("session_uuid") != uuid:
                continue
            try:
                when = float(rec.get("started_epoch") or 0) or os.path.getmtime(f)
            except (TypeError, ValueError, OSError):
                when = 0.0
            if when >= best_when:
                best, best_when = rec, when
    return best


def revive_model(provider: str, uuid: str, aib_home: str, config_dir: Path | None = None) -> tuple[str, str]:
    """原地復活沒帶 --model 時該開哪一個模型(the maintainer 10-10:用戶把原席復活要拿回**原來在用的**,不是 providers.json 預設)。

    三層,回 (模型, 來源層):
      ① 對話紀錄最後用的(席內 /model 換過也在這裡;AIB 自己的檔案不記這件事)→ (m, "record")
      ② 舊席位檔 model_requested(開席時要求的)                              → (m, "seat_file")
      ③ 兩層都沒有 → ("", "")——呼叫端自己退到 providers.json 預設,並且要印出來退到了哪一層。
    10-10 實踩:a seat 席內換到 Fable 5.1,同碼復活後無聲變回 Opus 5(預設),席碼/人格/CTX 全對只有模型錯。"""
    m = record_model(provider, find_record(provider, uuid, aib_home, config_dir))
    if m:
        return m, REVIVE_LAYER_RECORD
    rec = seat_record_for(uuid, aib_home)
    m = rec.get("model_requested") if isinstance(rec, dict) else ""
    if isinstance(m, str) and m.strip():
        return m.strip(), REVIVE_LAYER_SEAT_FILE
    return "", ""


def excerpt_any(provider: str, path: Path | None, n: int = 6, width: int = 400) -> list[str]:
    if provider == "claude":
        return excerpt(path, n, width)
    turns = []
    for role, text in record_turns(provider, path):
        text = " ".join(text.split())
        if not text or text.startswith("<"):
            continue
        turns.append(("你:" if role == "user" else "前身:") + (text[:width] + ("…" if len(text) > width else "")))
    return turns[-n:]


def tail5(u: str) -> str:
    return seat_liveness.tail5(u)


def claude_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"))


def find_transcript(uuid: str, config_dir: Path | None = None) -> Path | None:
    if not UUID_RE.fullmatch(uuid or ""):
        return None
    hits = glob.glob(str((config_dir or claude_config_dir()) / "projects" / "*" / (uuid + ".jsonl")))
    return Path(sorted(hits, key=os.path.getmtime)[-1]) if hits else None


def _rows(path: Path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    yield d
    except OSError:
        return


def has_conversation(path: Path | None) -> bool:
    """這份對話紀錄裡**有沒有人講過話**(至少一列 user/assistant)。

    09-26 實機:先啟動後選會留下一堆從沒講過話的空白對話檔(1 KB 左右,只有 mode/
    permission-mode/system 那幾列)。它們照樣落在 ~/.claude/projects/ 裡、照樣被席位檔登記,
    只用檔案大小是分不出來的(實測 a seat = 1083 位元組、0 列對話;另一支 165 KB 也只有 1 列)。
    ⇒ 「能不能當舊席位載入」一律問這一條,不要問大小。與 aib 的 aib_fork_respawn 同判準。
    """
    if path is None:
        return False
    for d in _rows(Path(path)):
        if d.get("type") in ("user", "assistant"):
            return True
    return False


def context_usage(path: Path) -> dict:
    """最後一筆 assistant usage 估已用 context。

    窗口從模型名推不出來(實測 09-24:model 寫 claude-opus-5,用量 86 萬 = 1M 窗口)。
    規則:用量 > 20 萬 ⇒ 一定是 1M;≤ 20 萬 ⇒ **保守當 20 萬**(1M 席在 14–20 萬會被判成快滿、
    改帶交接包——多保守一次,不要 fork 出一個一開席就被迫壓縮的席)。AIB_FORK_CONTEXT_WINDOW 可蓋。
    """
    used = 0
    for d in _rows(path):
        m = d.get("message")
        if d.get("type") == "assistant" and isinstance(m, dict) and isinstance(m.get("usage"), dict):
            u = m["usage"]
            total = sum(int(u.get(k) or 0) for k in
                        ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            if total:
                used = total
    try:
        window = int(os.environ.get("AIB_FORK_CONTEXT_WINDOW") or 0)
    except ValueError:
        window = 0
    if window <= 0:
        window = LARGE_WINDOW if used > SMALL_WINDOW else SMALL_WINDOW
    return {"used": used, "window": window, "ratio": (used / window) if window else 1.0}


def decide_mode(old_uuid: str, config_dir: Path | None = None) -> dict:
    path = find_transcript(old_uuid, config_dir)
    if path is None:
        return {"mode": "pack", "reason": "找不到前身的對話紀錄檔,無從整段載入", "transcript": ""}
    use = context_usage(path)
    if 1 - use["ratio"] < PACK_BELOW_REMAINING:
        return {"mode": "pack", "transcript": str(path), "usage": use,
                "reason": "前身對話已用 %d/%d(剩 %.0f%%,門檻 %d%%),整段載入會一開席就被迫壓縮"
                          % (use["used"], use["window"], max(0, 1 - use["ratio"]) * 100, PACK_BELOW_REMAINING * 100)}
    return {"mode": "fork", "transcript": str(path), "usage": use, "reason": "前身對話剩餘足夠,整段載入"}


def copy_memory(old5: str, new5: str, aib_home: str, stamp: str) -> dict:
    src = Path(aib_home) / "memory" / old5
    dst = Path(aib_home) / "memory" / new5
    if not src.is_dir():
        return {"memory": "none", "detail": "前身 memory/%s/ 不存在" % old5}
    if dst.exists():
        return {"memory": "kept", "detail": "memory/%s/ 已存在,不覆蓋" % new5, "path": str(dst)}
    if src.is_symlink():
        src = src.resolve()
    shutil.copytree(src, dst, symlinks=False)
    ctx = dst / "context.md"
    if ctx.is_file():
        body = ctx.read_text(encoding="utf-8", errors="replace")
        ctx.write_text("<!-- 分自 @%s 於 %s:這是前身記憶的**複本**,不是連結;前身的在 memory/%s/,不要寫回去 -->\n%s"
                       % (old5, stamp, old5, body), encoding="utf-8")
    return {"memory": "copied", "path": str(dst), "from": str(src)}


def _load(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def old_binding(old5: str, aib_home: str) -> dict:
    b = (_load(Path(aib_home) / "identity" / "bindings.json").get("bindings") or {}).get(old5) or {}
    if not b.get("persona_uid"):
        # 09-29 a seat:bindings.json 只列「人格與資料夾都綁了」的席;只綁人格的席只在 seat-display.json
        #   (真機 46 席裡 24 席是這樣)。以前這裡查不到就說「前身沒綁人格」⇒ 分出新席時人格被丟掉。
        d = (_load(Path(aib_home) / "identity" / "seat-display.json").get("display") or {}).get(old5) or {}
        if isinstance(d, dict) and d.get("persona_uid"):
            b = {"persona_uid": d.get("persona_uid"), "route_key": d.get("route_key") or ""}
    return {"persona_uid": b.get("persona_uid") or "", "route_key": b.get("route_key") or ""}


def carry_binding(old5: str, new_seat_id: str, new_uuid: str, aib_home: str, dry: bool = False) -> dict:
    """人格卡 + 大腦路由綁到新席。走 bind_one.py(唯一合法寫入器);前身沒綁 ⇒ 跳過,不拿別本充數。"""
    b = old_binding(old5, aib_home)
    if not b["persona_uid"]:
        return {"binding": "skipped", "detail": "前身 @%s 沒綁人格" % old5}
    cmd = [sys.executable, str(HERE.parent / "identity" / "bind_one.py"), "bind",
           "--seat", new_seat_id, "--card", b["persona_uid"],
           "--actor", "seat-fork@%s/aib" % tail5(new_uuid), "--actor-kind", "system",
           "--state-root", aib_home]
    if b["route_key"]:
        cmd += ["--brain", b["route_key"]]
    if dry:
        cmd.append("--dry-run")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return {"binding": "bound" if r.returncode == 0 else "failed", "rc": r.returncode,
            "persona_uid": b["persona_uid"], "route_key": b["route_key"],
            "detail": (r.stderr or r.stdout).strip()[-400:]}


def _text_of(msg) -> str:
    c = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return ""


def excerpt(path: Path | None, n: int = 6, width: int = 400) -> list[str]:
    if path is None:
        return []
    turns = []
    for d in _rows(path):
        if d.get("isMeta") or d.get("isSidechain"):
            continue
        t = d.get("type")
        if t not in ("user", "assistant"):
            continue
        text = _text_of(d.get("message")).strip()
        if not text or text.startswith("<"):          # 工具結果、系統提示、指令回顯
            continue
        text = " ".join(text.split())
        turns.append(("你:" if t == "user" else "前身:") + (text[:width] + ("…" if len(text) > width else "")))
    return turns[-n:]


def latest_handoff(old5: str, aib_home: str) -> str:
    hits = [p for p in glob.glob(os.path.join(aib_home, "pending", "*%s*.md" % old5))
            if os.path.basename(p).startswith(("HANDOFF", "SUMMARY"))]
    return max(hits, key=os.path.getmtime) if hits else ""


def predecessor_unread(old5: str) -> int | None:
    """前身佇列裡還沒讀的 dm 數。讀不到回 None(不寫一個編出來的 0)。

    不搬信:佇列有 rev/憑據規矩,搬家會多一條寫入路徑;改成告訴新席去哪讀(09-24 設計 §5 dm 轉信)。
    新寄給舊碼的信,dm-ctl 在舊碼沒有活席時會轉給後繼(seat_liveness.successors)。
    """
    try:
        import dm_queue
        return int((dm_queue.tabs_payload(old5).get("badges") or {}).get("unread") or 0)
    except Exception:
        return None


NOTICE_HEAD = "[AIB 分出新席]"   # ★ 不用「開席通知」:那是系統 dm 的寄件者標籤(codexbot_bridge SYSTEM_SENDER_LABELS)

# ── dm 位址一律 <艙位碼>/<席碼>(the maintainer 09-28「舊的停用」;`aib dm` 入口對只寫席碼的寫法 rc=2、一個字都沒送)──
#   開場說明是新席**第一句就一定讀到**的指令,比記憶檔優先:這裡印出只寫席碼的 `aib dm`,等於教它一個保證被擋的寫法
#   (09-29 a seat 實測:`aib dm --resolve-only 1857a` rc=2、`997f/7fb31` rc=0)。
#   ⇒ 艙位碼查得到才印完整位址;查不到就講去哪裡查,**不退回只寫席碼**(退回=失效值長得跟正常值一樣)。
#   拼法正本在 dm_address.py(format_address / cabin_code),本檔不另寫。
ADDR_UNKNOWN_HINT = "艙位碼查不到;它寄來的 dm 抬頭 IP:位址/艙位碼/名@席碼 裡就有,只寫席碼的舊寫法會被擋"


def _dm_address_mod():
    try:
        import dm_address
        return dm_address
    except Exception:
        return None


def predecessor_cabins(held: dict) -> list[str]:
    """前身登記在哪幾艙(艙位碼,排序去重)。只用活席查詢(seat_liveness.registry_holders)回來的 `cabin` 欄:
    那一欄是席位檔自己寫的 cabin_id,或它所在艙目錄的名字,查詢本身已走 HUB 推導的席位目錄。
    ★ 不在這裡自己拆席位檔路徑:那等於再寫一支席位目錄解析器(tests/test-seats-dir-resolution.py 守的就是這件,
      v0.69.4 發版全套 09-29 抓到)。不是艙 id 形狀的值(舊主目錄 `.ez2-aib`、治具)一律當「不知道」,不猜。"""
    da = _dm_address_mod()
    if da is None:
        return []
    out = set()
    for r in (held or {}).get("registry") or []:
        inst = str(r.get("cabin") or "")
        if not re.fullmatch(da.INSTANCE_RE, inst):
            continue
        code = da.cabin_code(inst)
        if code:
            out.add(code)
    return sorted(out)


def dm_target(code: str, cabin: str) -> str:
    """完整 dm 位址;艙位碼空或拼不出 ⇒ 回空字串(呼叫端改印「去哪裡查」,不得印只寫席碼的寫法)。"""
    da = _dm_address_mod()
    if da is None or not cabin:
        return ""
    try:
        return da.format_address(code, cabin)
    except ValueError:
        return ""


def preamble(old_uuid: str, new_uuid: str, mode: str, aib_home: str, window: str = "",
             old_alive: bool = False, transcript: Path | None = None, brain: str = "",
             unread: int | None = None, memory: str = "copied", provider: str = "claude",
             model: str = "", own_cabin: str = "", old_cabins: list[str] | tuple = ()) -> str:
    o, n = tail5(old_uuid), tail5(new_uuid)
    where = "(窗 %s)" % window if window else ""
    mine = dm_target(n, own_cabin)
    # ★ 第二行固定只佔一行:seat_load.insert_persona_pack 以「前兩行 = 身分行 + dm 位址行」切段
    lines = [
        "%s 你是新席 @%s%s,從 @%s 分出。" % (NOTICE_HEAD, n, where, o),
        ("- 你的 dm 位址是 %s,署名是 @%s。" % (mine, n) if mine else
         "- 你的署名是 @%s(dm 位址 = 艙位碼/%s;艙位碼在你收到的 dm 抬頭第二段)。" % (n, n))
        + "對話紀錄或記憶裡的「我是 @%s」說的是前身,不是你。" % o,
    ]
    # ★ 照 copy_memory 的實際結果寫(09-24 實機:前身沒有記憶夾,舊版照樣寫「已複製」——假話)
    if memory == "copied":
        lines.append("- 第二大腦已複製到 memory/%s/;前身的是 memory/%s/,不要寫回去。" % (n, o))
    elif memory == "kept":
        lines.append("- memory/%s/ 本來就有,沒有覆蓋;前身的是 memory/%s/,不要寫回去。" % (n, o))
    else:
        lines.append("- 前身沒有第二大腦(memory/%s/ 不存在),沒有東西可複製;你的記憶寫在 memory/%s/。" % (o, n))
    if brain:
        lines.append("- 綁定的大腦目錄:%s" % brain)
    handoff = latest_handoff(o, aib_home)
    if old_alive:
        targets = [t for t in (dm_target(o, c) for c in old_cabins) if t]
        if len(targets) == 1:
            how = "aib dm %s" % targets[0]
        elif targets:
            how = "aib dm 下列之一:%s(它登記在不只一艙)" % "、".join(targets)
        else:
            how = "aib dm <艙位碼>/%s(%s)" % (o, ADDR_UNKNOWN_HINT)
        lines.append("- 前身 @%s **還活著**,是另一個席。不要接著做它手上的工作(會做重工);要協調就 %s。" % (o, how))
        tail = "先回一句確認你的席碼,然後等指示。"
    else:
        lines.append("- 前身 @%s 已經不在,你接續它沒做完的事。" % o)
        tail = "先回一句確認你的席碼,讀交接單,再接著做。"
    if handoff:
        lines.append("- 前身最新交接單:%s" % handoff)
    if unread and not old_alive:
        lines.append("- 前身佇列還有 %d 則未讀 dm:python3 src/cockpit/dm_queue.py tabs --seat %s"
                     "(之後寄給 @%s 的信會自動轉給你)" % (unread, o, o))
    if model:
        lines.append("- 前身用的模型:%s(%s)" % (model, provider))
    if mode == "pack" and provider == "claude":
        lines.append("- 前身對話已接近上限,沒有整段載入。最近幾輪:")
        lines += ["  " + t for t in (excerpt(transcript) or ["(讀不到)"])]
    elif mode == "pack":
        lines.append("- 上一場紀錄:%s" % (transcript or "(這一家的對話紀錄找不到或讀不了,只帶人格、第二大腦、交接單)"))
        got = excerpt_any(provider, transcript)
        if got:
            lines.append("- 上一場最近幾輪:")
            lines += ["  " + t for t in got]
    elif mode == "history":
        lines.append("- 上一場對話已整段接上(從 @%s 的紀錄複製過來)。" % o)
    elif mode == "persona":
        lines.append("- 對話本身沿用這個 bot 綁的桌面對話(不另外載入);人格與第二大腦已帶過來。")
    lines.append(tail)
    return "\n".join(lines)


def prepare(old_uuid: str, new_uuid: str, aib_home: str, window: str = "", state_dir: str = "",
            config_dir: Path | None = None, now: float | None = None, provider: str = "claude",
            model: str = "") -> dict:
    if not (UUID_RE.fullmatch(old_uuid or "") and UUID_RE.fullmatch(new_uuid or "")):
        raise SystemExit("seat_fork: --from/--to 要是 uuid")
    if old_uuid == new_uuid:
        raise SystemExit("seat_fork: 新舊 uuid 相同——那是原地復活,不是分出新席")
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now or time.time()))
    kind = load_kind(provider)
    if kind == "claude":
        dec = decide_mode(old_uuid, config_dir)
        model = record_model("claude", find_transcript(old_uuid, config_dir)) or model
    else:
        rec = find_record(provider, old_uuid, aib_home, config_dir)
        mode = "history" if (kind == "history" and rec) else ("persona" if kind == "persona" else "pack")
        model = record_model(provider, rec) or model          # 前身實際用的模型;參數只當退路
        dec = {"mode": mode, "transcript": str(rec) if rec else "",
               "reason": {"history": "API-key 席:複製上一場紀錄", "persona": "橋接 bot:對話沿用桌面對話",
                          "pack": "此家 CLI 不收分出:投開場說明(含最近幾輪)"}[mode]}
    mem = copy_memory(tail5(old_uuid), tail5(new_uuid), aib_home, stamp)
    held = seat_liveness.holders(old_uuid, aib_home=aib_home)
    alive = seat_liveness.is_held(held)
    da = _dm_address_mod()
    own_cabin = da.cabin_code(os.environ.get("EZ2_AIB_INSTANCE") or "") if da else ""
    try:
        import usage_failover
        brain = usage_failover.bound_brain_folder(aib_home, old_uuid)
    except Exception:
        brain = ""
    text = preamble(old_uuid, new_uuid, dec["mode"], aib_home, window, alive,
                    Path(dec["transcript"]) if dec.get("transcript") else None, brain,
                    None if alive else predecessor_unread(tail5(old_uuid)), mem["memory"],
                    provider=provider, model=model, own_cabin=own_cabin,
                    old_cabins=predecessor_cabins(held) if alive else [])
    sd = Path(state_dir or os.path.join(aib_home, "state"))
    sd.mkdir(parents=True, exist_ok=True)
    pf = sd / ("fork-preamble-%s.txt" % new_uuid)
    pf.write_text(text + "\n", encoding="utf-8")
    return {"mode": dec["mode"], "reason": dec["reason"],
            "fork_from": old_uuid if dec["mode"] in ("fork", "history") else "",
            "kind": kind, "record": dec.get("transcript") or "",
            "preamble_file": str(pf), "old_alive": alive, "memory": mem}


def copy_history(provider: str, old_uuid: str, new_uuid: str, aib_home: str, preamble_file: str = "") -> dict:
    """API-key 席:舊 history.jsonl 複製給新席,最後補一列 system(你是新席、從誰分出)。
    新席那份**已經有對話**(user/assistant 列)⇒ 不動,回 has_conversation。"""
    if provider not in API_SESSIONS:
        return {"history": "unsupported"}
    src = find_record(provider, old_uuid, aib_home)          # 前身沒有紀錄 ⇒ 只寫說明那一列(不貼進提示符)
    dst_dir = api_session_root(aib_home) / API_SESSIONS[provider] / new_uuid
    dst = dst_dir / "history.jsonl"
    if dst.is_file() and any(r.get("role") in ("user", "assistant") for r in _rows(dst)):
        return {"history": "has_conversation", "path": str(dst)}
    dst_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    body = src.read_text(encoding="utf-8", errors="replace") if src is not None else ""
    if body and not body.endswith("\n"):
        body += "\n"
    note = Path(preamble_file).read_text(encoding="utf-8").strip() if preamble_file else ""
    if note:
        body += json.dumps({"role": "system", "content": note}, ensure_ascii=False) + "\n"
    tmp = dst.with_suffix(".jsonl.tmp")
    tmp.write_text(body, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, dst)
    return {"history": "copied" if src is not None else "note_only", "from": str(src or ""), "path": str(dst)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="seat_fork.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--from", dest="old", required=True)
    p.add_argument("--to", dest="new", required=True)
    p.add_argument("--window", default="")
    p.add_argument("--aib-home", default="")
    p.add_argument("--state-dir", default="")
    p.add_argument("--provider", default="claude")
    p.add_argument("--model", default="")
    h = sub.add_parser("copy-history")
    h.add_argument("--provider", required=True)
    h.add_argument("--from", dest="old", required=True)
    h.add_argument("--to", dest="new", required=True)
    h.add_argument("--aib-home", default="")
    h.add_argument("--preamble-file", default="")
    b = sub.add_parser("bind-carry")
    b.add_argument("--from", dest="old", required=True)
    b.add_argument("--to", dest="new", required=True)
    b.add_argument("--seat-id", required=True)
    b.add_argument("--aib-home", default="")
    b.add_argument("--dry-run", action="store_true")
    rm = sub.add_parser("revive-model")        # 10-10:原地復活該開哪個模型(三層;aib 內部用 -c 直接呼叫同一個函數)
    rm.add_argument("--provider", required=True)
    rm.add_argument("--uuid", required=True)
    rm.add_argument("--aib-home", default="")
    a = ap.parse_args(argv)
    home = a.aib_home or seat_liveness.default_aib_home()
    if a.cmd == "revive-model":
        m, layer = revive_model(a.provider, a.uuid, home)
        print(json.dumps({"model": m, "layer": layer}, ensure_ascii=False))
        return 0
    if a.cmd == "prepare":
        print(json.dumps(prepare(a.old, a.new, home, a.window, a.state_dir, provider=a.provider,
                                 model=a.model), ensure_ascii=False))
        return 0
    if a.cmd == "copy-history":
        r = copy_history(a.provider, a.old, a.new, home, a.preamble_file)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r["history"] in ("copied", "note_only") else 1
    r = carry_binding(tail5(a.old), a.seat_id, a.new, home, dry=a.dry_run)
    print(json.dumps(r, ensure_ascii=False))
    return 1 if r["binding"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
