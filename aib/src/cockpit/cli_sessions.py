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
  ★ 10-28 Andy「下方的載入席位,只要顯示 席位/人格/專案名稱/最新開啟時間 這樣就好」⇒ four_col 列
    (claude / codex / CLI 等非橋接 bot):tail = 「人格 · 專案 · 月-日 時:分」(選單在前面接 ★ 與 @碼),
    使用中標記留在尾端;沒綁 / 讀不到 ⇒ 字典的「未綁」。人格名、專案名只讀既有綁定資料的顯示名,
    不顯示 uid / fld_ 路徑。橋接 bot(load_kind=persona)列這次不動,照舊格式(four_col=false)。
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
import unicodedata
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


def _t(key: str) -> str:
    try:
        import i18n_lib
        return i18n_lib.t(key)
    except Exception:
        return key


def _clean_name(value) -> str:
    """外部字串進選單:與右欄同一套消毒(seat_persona.clean_text),並拒 `@` / `|`(冒充別席、多切一欄)。"""
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        import seat_persona
        text = seat_persona.well_formed_name(seat_persona.clean_text(value, 120))
    except Exception:
        return ""
    return "" if (not text or "@" in text or "|" in text) else text


def _persona_name(code: str, home: str) -> str:
    """席碼 → 綁的人格卡顯示名(cards.json display_name,退 seat-display.json 投影)。讀不到 ⇒ 空。"""
    try:
        uid = seat_fork.old_binding(code, home)["persona_uid"]
        if not uid:
            return ""
        ident = Path(home) / "identity"
        card = ((_load(str(ident / "cards.json")).get("cards") or {}).get(uid) or {})
        name = _clean_name(card.get("display_name") if isinstance(card, dict) else "")
        if name:
            return name
        d = ((_load(str(ident / "seat-display.json")).get("display") or {}).get(code) or {})
        if isinstance(d, dict) and d.get("persona_uid") == uid:
            return _clean_name(d.get("display_name"))
    except Exception:
        pass
    return ""


def _project_name(seat_rec) -> str:
    """席位檔綁的專案卡標題(與右欄同一支 seat_persona.project_title_of)。讀不到 ⇒ 空。"""
    try:
        import seat_persona
        return seat_persona.project_title_of(seat_rec) if seat_rec else ""
    except Exception:
        return ""


# 彈窗一列 = 「 [h] 」5 欄 + 「★ @碼 · 人格 · 專案 · 月-日 時:分 · 使用中(會分出新碼)」。
# 10-28 Andy「使用中保留完整字樣就好,名字縮短沒關係」⇒ 尾巴永遠完整,名字先縮。
# ★ #399 R1(@aeb40):選單開在 display-popup -w POPUP_W_PCT% 裡,open_menu_popup.fit 再截 cols−1 ⇒ 預算要用**彈窗內寬**
#   (終端寬 × 90% − 2 框線),不是終端寬。選單殼量 tmux client_width 換算後用 --popup-cols 交進來;量不到用 80 欄終端的值。
#   一列 = 5 + 2(★,沒有也預留)+ 9(@碼 · )+ 人格 + 3 + 專案 + 3 + 11(時間)[+ 3 + 使用中] ≤ 內寬 − 1,人格 = 專案 = 剩下的一半,
#   上限 NAME_COLS、下限 NAME_MIN(80 欄終端 zh-TW:沒在用的列各 18 欄,使用中的列各 7 欄)。
POPUP_W_PCT = 90              # 與 cli-session-menu.sh 的 display-popup -w 同一個值
POPUP_COLS_DEFAULT = 80 * POPUP_W_PCT // 100 - 2
ROW_FIXED = 5 + 2 + 9 + 3 + 3 + 11
NAME_COLS = 18
NAME_MIN = 3


def _cw(ch: str) -> int:
    """一個字畫幾欄:與 open_menu_popup.fit 同一套(全形 2、組合字元 0、其他 1),兩邊算的才會一樣。"""
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else (0 if unicodedata.combining(ch) else 1)


def _width(text: str) -> int:
    return sum(_cw(ch) for ch in text)


def _name_cols(live_label: str, popup_cols: int = POPUP_COLS_DEFAULT) -> int:
    """這一列人格、專案各給幾欄。live_label = 這一列尾端的使用中字樣(沒在用 = 空)。"""
    rest = popup_cols - 1 - ROW_FIXED - ((3 + _width(live_label)) if live_label else 0)
    return max(NAME_MIN, min(NAME_COLS, rest // 2))


def _fit(text: str, cols: int) -> str:
    """依欄寬截到 cols 欄,截了補「…」(寬度算法同 _cw)。"""
    if _width(text) <= cols:
        return text
    w, out = 0, []
    for ch in text:
        if w + _cw(ch) > cols - 1:
            break
        out.append(ch)
        w += _cw(ch)
    return "".join(out).rstrip() + "…"


def four_tail(persona: str, project: str, when: str, live_label: str = "",
              popup_cols: int = POPUP_COLS_DEFAULT) -> str:
    """「人格 · 專案 · 月-日 時:分[ · 使用中(會分出新碼)]」:名字依彈窗內寬截短,使用中字樣永遠完整。"""
    cols = _name_cols(live_label, popup_cols)
    return "%s · %s · %s%s" % (_fit(persona, cols), _fit(project, cols), when,
                               " · " + live_label if live_label else "")


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
        import bot_binding   # 10-11 第 15 件第 1 步:判準走同一支
        st = bot_status.read_status(home, uuid) or {}
        return bot_binding.bound_id(provider, uuid, status=st)[0] if st else ""
    except Exception:
        return ""


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
            role: str = "", limit: int = 9, procs=None, config_dir: Path | None = None,
            popup_cols: int = POPUP_COLS_DEFAULT) -> dict:
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
            retired = os.path.basename(os.path.dirname(f)) == "retired"
            # #399 R1:同一 uuid 同時有活席檔與 retired 檔、時間一樣(都看同一份對話紀錄)⇒ 活席檔優先(專案名才是現在的)
            if prev is None or when > prev["_when"] or (when == prev["_when"] and prev.get("_retired") and not retired):
                cands[u] = {"id": u, "role": s.get("role") or "?", "window": s.get("window_id") or "",
                            "model": s.get("model_requested") or s.get("model") or (prev or {}).get("model", ""),
                            "_seat": s, "_retired": retired, "_when": when,
                            "_has_transcript": _has_record_content(provider, t)}
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
        r.pop("_retired", None)
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
        seat_rec = r.pop("_seat", None)
        r["four_col"] = seat_fork.load_kind(provider) != "persona"
        if r["four_col"]:
            unbound, live = _t("aib.climenu.unbound"), _t("aib.climenu.live")
            r["persona_name"] = _persona_name(r["code"], home)
            r["project_name"] = _project_name(seat_rec)
            r["tail"] = four_tail(r["persona_name"] or unbound, r["project_name"] or unbound, r["when"],
                                  live if r["live"] else "", popup_cols)
            continue
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
    ap.add_argument("--popup-cols", type=int, default=0)   # 選單彈窗內寬(殼量);0 / 不合理 ⇒ 80 欄終端的值
    a = ap.parse_args(argv)
    pc = a.popup_cols if 20 <= a.popup_cols <= 1000 else POPUP_COLS_DEFAULT
    d = collect(a.provider, cwd=a.cwd, seats_dir=a.seats_dir, role=a.role, limit=a.limit, popup_cols=pc)
    if a.json:
        print(json.dumps(d, ensure_ascii=False))
    else:
        for r in d["rows"]:
            sep = " · " if r.get("four_col") else " "
            print("%s@%s%s%s" % ("★ " if r["star"] else "  ", r["code"], sep, r["tail"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
