#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開席選單的資料後端:列出可以「分出新席」的舊 CLI 對話(the maintainer 09-24 H1–H3)。

每一列(H2 指定的語意):角色、席碼/窗、最後活動、第二大腦(沒綁寫「沒有」,不拿別本充數)、
上席資訊(前身最新交接單)、★ 最近(= last_cli_session.resolve 會挑的那條)、
使用中(seat_liveness 判活持有;選它**一樣分出新碼**,但新席會被告知前身還活著)。

母體:所有艙的席位檔(活 + retired)同 provider,加上(claude)本目錄的對話紀錄檔。
同一 uuid 只留一列,依最後活動新→舊,最多 --limit 列。
★ 09-26:**從沒講過話的空白對話不列**(seat_fork.has_conversation)。先啟動後選會留下一堆
  空白對話檔;選到它等於載入一個空的前身(實機:a seat 就是這樣分出來的,前言還寫「接續它沒做完的事」)。
  舊判準是「檔案 ≥ 200 位元組」,而空白檔有 1083 位元組 ⇒ 量錯東西,不是門檻設太低。
★ 09-26 c48b1b:橋接 bot(load_kind=persona)的**未綁**席也列(尾標「未綁」);選了分出新碼、
  不帶人格/交接(carry_binding 本就跳過),不在新增時 auto-bind。

  cli_sessions.py --provider claude --cwd DIR [--seats-dir D] [--role R] [--limit 9] --json
  ⇒ {"supported": bool, "rows": [{"id","code","role","model","bot_name","window","when","brain",
                                  "handoff","live","star","bound","tail"}]}
  bot_name = Grok Bot 側欄顯示名(有 agent 綁定時);空則選單退回 model / 「模型不明」。
  bound = 已連 Grok Bot agent(status.agent_id);選單灰階(dim)。「未綁」標籤仍只看 persona_uid。
  limit 預留 agent 或 persona 已綁席,避免未綁擠掉。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import last_cli_session  # noqa: E402
import seat_fork  # noqa: E402
import seat_liveness  # noqa: E402

# 09-26 the maintainer「其他所有AI都全部要照這樣程式化」:每一家都列;怎麼接上一場紀錄看 seat_fork.load_kind。
#   claude 照舊要有對話紀錄檔才列(它的載入是整段分出);其他家只要人格、第二大腦、交接單、上一場紀錄有一樣就列。


def _load(p: str) -> dict:
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _brain(aib_home: str, uuid: str) -> str:
    try:
        import usage_failover
        folder = usage_failover.bound_brain_folder(aib_home, uuid)
    except Exception:
        folder = ""
    if folder:
        return folder
    try:
        import seat_who
        f = (seat_who._identity(aib_home, "", seat_liveness.tail5(uuid)) or {}).get("brain_folder") or ""
    except Exception:
        f = ""
    return f if f and f not in ("UNRESOLVED", "未綁", "—") else "沒有"


def _has_record_content(provider: str, path: Path | None) -> bool:
    """這份「上一場紀錄」裡**有沒有人講過話**。

    claude:紀錄就是對話檔,問 seat_fork.has_conversation(至少一列 user/assistant)。
    其他家:**只問檔在不在**——它們的紀錄格式各異,而且 grok 一起來就自寫一列 type=user
    (見 9c74340a),拿同一條判準去問會把剛開的席全判成有內容,等於沒守。
    ⇒ 別家的空白紀錄還沒有人在守,這是登記在案的盲區(見交件單 §6)。
    """
    if provider != "claude":
        return bool(path)
    return seat_fork.has_conversation(path)


def _persona_bound(code: str, home: str) -> bool:
    return bool(seat_fork.old_binding(code, home)["persona_uid"])


def _bridge_agent_id(provider: str, uuid: str, home: str) -> str:
    """bridge status 的 agent_id;非 grokbot / 讀不到則空。不讀 secrets。"""
    if provider != "grokbot" or not uuid:
        return ""
    try:
        import bot_status
        st = bot_status.read_status(home, uuid) or {}
    except Exception:
        return ""
    aid = st.get("agent_id") if isinstance(st, dict) else None
    return aid if isinstance(aid, str) and aid else ""


def _grokbot_agent_names() -> dict[str, str]:
    """agent_id → Grok Bot 側欄底名(去掉 AIB 加的「( 🔗 @碼 )」尾巴)。讀不到名冊就空表。"""
    try:
        import bot_seat_name
        import grokbot_threads
        out = {}
        for r in grokbot_threads.list_agents(10 ** 6):
            aid = r.get("id")
            if not isinstance(aid, str) or not aid:
                continue
            raw = (r.get("name") or "").strip()
            base = bot_seat_name.strip_seat_tag(raw).strip() if raw else ""
            out[aid] = base or raw
        return out
    except Exception:
        return {}


def _bot_display_name(provider: str, uuid: str, home: str, agent_names: dict[str, str] | None = None) -> str:
    """這一席在對方 app 側欄的顯示名。只走既有 bridge status + 名冊;不讀 secrets。"""
    aid = _bridge_agent_id(provider, uuid, home)
    if not aid:
        return ""
    names = agent_names if agent_names is not None else _grokbot_agent_names()
    return (names.get(aid) or "").strip()


def _pick_limited(loadable: list[dict], limit: int, home: str, provider: str = "") -> list[dict]:
    """未連(新→舊)在前;已連 agent / 已綁 persona 在後並預留格數。

    預留:優先塞下**全部** agent-linked(灰階列),再至少 1 格給 persona-only;
    與舊的 max(1, limit//3) 取大,避免未綁把 a seat 等擠掉。
    """
    limit = max(0, int(limit))
    for c in loadable:
        code = seat_liveness.tail5(c["id"])
        persona = _persona_bound(code, home)
        agent = bool(_bridge_agent_id(provider, c["id"], home))
        c["_persona_bound"] = persona
        c["_agent_linked"] = agent
        c["_bound"] = agent or persona  # 預留用:兩邊任一算「要留」
    unbound = sorted((c for c in loadable if not c["_bound"]), key=lambda c: -c["_when"])
    agents = sorted((c for c in loadable if c["_agent_linked"]), key=lambda c: -c["_when"])
    persona_only = sorted(
        (c for c in loadable if c["_bound"] and not c["_agent_linked"]), key=lambda c: -c["_when"])
    bound = agents + persona_only
    if not bound:
        return unbound[:limit]
    need = len(agents) + (1 if persona_only else 0)
    reserve = min(len(bound), max(max(1, limit // 3), need), limit)
    picked = unbound[: limit - reserve] + bound[:reserve]
    if len(picked) < limit:
        seen = {c["id"] for c in picked}
        for c in bound + unbound:
            if len(picked) >= limit:
                break
            if c["id"] not in seen:
                picked.append(c)
                seen.add(c["id"])
    return picked


def collect(provider: str, cwd: str = "", aib_home: str | None = None, seats_dir: str = "",
            role: str = "", limit: int = 9, procs=None, config_dir: Path | None = None) -> dict:
    if not provider:
        return {"supported": False, "rows": []}
    home = aib_home if aib_home is not None else seat_liveness.default_aib_home()
    procs = seat_liveness.proc_table() if procs is None else procs
    cands: dict[str, dict] = {}
    dirs = seat_liveness.all_seat_dirs(home)
    if seats_dir and os.path.realpath(seats_dir) not in {os.path.realpath(d) for d in dirs}:
        dirs.append(seats_dir)
    for sd in dirs:
        for f in glob.glob(os.path.join(sd, "w*.json")) + glob.glob(os.path.join(sd, "retired", "*.json")):
            s = _load(f)
            u = s.get("session_uuid") or ""
            if s.get("provider") != provider or not seat_fork.UUID_RE.fullmatch(u):
                continue
            if cwd and s.get("cwd") and os.path.realpath(s["cwd"]) != os.path.realpath(cwd):
                continue
            t = seat_fork.find_record(provider, u, home, config_dir)
            when = os.path.getmtime(t) if t else os.path.getmtime(f)
            prev = cands.get(u)
            if prev is None or when >= prev["_when"]:
                cands[u] = {"id": u, "role": s.get("role") or "?", "window": s.get("window_id") or "",
                            "model": s.get("model_requested") or s.get("model") or (prev or {}).get("model", ""),
                            "_when": when, "_has_transcript": _has_record_content(provider, t)}
    if provider == "claude" and cwd:
        proj = last_cli_session._claude_project_dir(cwd)
        if config_dir is not None:
            proj = Path(config_dir) / "projects" / proj.name
        for f in glob.glob(str(proj / "*.jsonl")):
            u = Path(f).stem
            if u in cands or not seat_fork.UUID_RE.fullmatch(u):
                continue
            if not seat_fork.has_conversation(Path(f)):
                continue
            cands[u] = {"id": u, "role": "?", "window": "", "model": "", "_when": os.path.getmtime(f),
                        "_has_transcript": True}
    try:
        star = last_cli_session.resolve(provider, seats_dir=seats_dir, role=role, cwd=cwd, aib_home=home)
    except Exception:
        star = ""

    def loadable(c):
        if c["_has_transcript"]:
            return True
        if provider == "claude":
            return False
        # the maintainer 09-26 c48b1b: 橋接 bot(grokbot/codexbot/sand)未綁席也要出現在開席選單,
        # 讓使用者選來綁;選了走現有 fork(新碼),無人格則 carry_binding 跳過,不 auto-bind。
        if seat_fork.load_kind(provider) == "persona":
            return True
        code = seat_liveness.tail5(c["id"])
        return (os.path.isdir(os.path.join(home, "memory", code))
                or bool(seat_fork.old_binding(code, home)["persona_uid"])
                or bool(seat_fork.latest_handoff(code, home)))
    agent_names = _grokbot_agent_names() if provider == "grokbot" else {}
    rows = _pick_limited([c for c in cands.values() if loadable(c)], limit, home, provider)
    for r in rows:
        u = r["id"]
        r["code"] = seat_liveness.tail5(u)
        # 模型:紀錄裡最後用的優先(實際跑的),退回席位檔的 model_requested(開席時要的)
        r["model"] = seat_fork.record_model(provider, seat_fork.find_record(provider, u, home, config_dir)) \
            or r.get("model") or ""
        r["bot_name"] = _bot_display_name(provider, u, home, agent_names)
        r["when"] = time.strftime("%m-%d %H:%M", time.localtime(r.pop("_when")))
        r.pop("_has_transcript", None)
        r["brain"] = _brain(home, u)
        h = seat_fork.latest_handoff(r["code"], home)
        r["handoff"] = os.path.basename(h) if h else "無"
        r["live"] = seat_liveness.is_held(seat_liveness.holders(u, aib_home=home, procs=procs))
        r["star"] = (u == star)
        # persona:右欄同判;「未綁」標籤只看這個。bound/dim = 已連 Grok Bot agent(the maintainer 09-26 灰階語意)。
        persona = bool(r.pop("_persona_bound", _persona_bound(r["code"], home)))
        agent = bool(r.pop("_agent_linked", bool(_bridge_agent_id(provider, u, home))))
        r.pop("_bound", None)
        r["bound"] = agent
        extras = []
        if not persona and seat_fork.load_kind(provider) == "persona":
            extras.append("未綁")
        if r["live"]:
            extras.append("使用中(會分出新碼)")
        r["tail"] = "%s %s · 大腦:%s · 交接:%s%s" % (
            r["role"], r["when"], r["brain"], r["handoff"],
            "".join(" · " + e for e in extras))
    return {"supported": True, "rows": rows, "star": star}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cli_sessions.py")
    ap.add_argument("--provider", required=True)
    ap.add_argument("--cwd", default="")
    ap.add_argument("--seats-dir", default="")
    ap.add_argument("--role", default="")
    ap.add_argument("--limit", type=int, default=9)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    d = collect(a.provider, cwd=a.cwd, seats_dir=a.seats_dir, role=a.role, limit=a.limit)
    if a.json:
        print(json.dumps(d, ensure_ascii=False))
    else:
        for r in d["rows"]:
            print("%s@%s %s%s" % ("★ " if r["star"] else "  ", r["code"], r["tail"], ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
