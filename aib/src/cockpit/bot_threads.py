#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""通用的「本地對話 AI 的對話清單」:`aib bot list [--provider P|--all]`。

the maintainer 2026-09-24:「這個 list 程式也應該列出其他的例如 grok 或未來的本地對話 ai」。
一個 provider 一個後端(`BACKENDS`);沒後端的 provider 明說「這家沒有可列的對話」,不猜、不空白。
右鍵開席的 30 秒選單(bot-thread-menu.sh)也吃這個 JSON,所以加一家新的本地對話 AI = 這裡加一個後端。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import claudebot_threads as _claude  # noqa: E402
import codexbot_threads as _codex  # noqa: E402
import grokbot_threads as _grok  # noqa: E402
import musebot_threads as _muse  # noqa: E402

# provider → (list_fn(limit) -> rows, 說明)。rows 每列 {id, name, updated, cwd}。
BACKENDS = {
    "codexbot": (lambda limit: _codex.list_threads(_codex.default_endpoint(), limit), "Codex 桌面(ChatGPT App)的對話"),
    # 09-25 a seat(a seat 派):右鍵開 grokbot 席要選綁哪個 agent。清單=app 本機名冊(剛建的 agent 要在 app 點一下才會寫進來)。
    "grokbot": (lambda limit: _grok.list_agents(limit), "Grok Bot.app 的 agent(本機名冊)"),
    # 09-26 ClaudeBot:本機 Claude 對話(~/.claude/projects)+ 09-27 #16:中繼站報到過的雲端 session(☁ 雲端 · 分支 · 目錄)
    "claudebot": (lambda limit: _claude.list_sessions_with_cloud(limit), "Claude 對話:本機 session / claude CLI + 雲端(向中繼報到過的)"),
    # 10-11 a seat(Bot 開發 P3):MuseBot 也跳選單。Muse 沒有本機名冊 ⇒ 只列得出 AIB 綁過的(musebot_threads.history,
    #   與開席沿用 pick_default、席上 /bind 清單同一份);第一次綁新的一段:選單選「先開席」,席上打 /bind <id>。
    "musebot": (lambda limit: _muse_rows(limit), "綁過的 Muse 對話(Muse 沒有本機名冊,只列得出 AIB 綁過的)"),
}
UNSUPPORTED = {
    "sand": "sand(Cursor 橋)沒有可列的桌面對話",
}


def _muse_rows(limit: int) -> list:
    return [{"id": conv, "name": "Muse 對話 " + conv[:20], "updated": int(mt), "cwd": ""}
            for conv, mt in _muse.history()[: max(0, int(limit))]]


def _held_by(provider: str, item_id: str) -> list:
    """P6 占用判準一家一個:codexbot=桌面對話的鎖,grokbot=agent 的鎖(都是橋自己那把 flock)。"""
    if provider == "grokbot":
        return _grok.held_by(item_id)
    if provider == "claudebot":
        return _claude.held_by(item_id)
    if provider == "musebot":
        # 10-09 a seat:以前 musebot 落到最後一行 codex 的判準(DIAG-bot-compare §1 記過的陷阱);開席前占用關卡要用它自己的鎖
        return _muse.held_by(item_id)
    return _codex.held_by(item_id)


def ensure_codexbot_server() -> str:
    """F3:`aib bot list` 在 server 沒起時自動 ensure(以前只提示人去跑)。回 endpoint;起不來丟例外。"""
    import codexbot_server as _srv
    return _srv.ensure_server()


def list_with_ensure(provider: str, limit: int, backends=None, ensure=None) -> dict:
    """F3:第一次連不上就 ensure 一次再試;第二次還不行才回「連不上」的 note。"""
    try:
        return list_for(provider, limit, backends)
    except Exception as first:  # noqa: BLE001
        if provider != "codexbot":
            return {"provider": provider, "supported": True, "rows": [], "note": "連不上 %s 的服務:%s" % (provider, str(first)[:160])}
        try:
            (ensure or ensure_codexbot_server)()
            out = list_for(provider, limit, backends)
            out["note"] = (out.get("note") or "") + "(server 剛由 aib bot list 自動起來)"
            return out
        except Exception as exc:  # noqa: BLE001
            return {"provider": provider, "supported": True, "rows": [], "note": "連不上 %s 的服務,自動起 server 也失敗:%s" % (provider, str(exc)[:160])}


def list_for(provider: str, limit: int = 20, backends=None) -> dict:
    """回 {"provider", "rows", "supported", "note"}。rows 只在 supported 時有內容。"""
    backends = BACKENDS if backends is None else backends
    fn = backends.get(provider)
    if fn is None:
        return {"provider": provider, "supported": False, "rows": [],
                "note": UNSUPPORTED.get(provider, "%s 還沒有對話清單後端(在 bot_threads.BACKENDS 加一個)" % provider)}
    rows = fn[0](limit) if isinstance(fn, tuple) else fn(limit)
    return {"provider": provider, "supported": True, "rows": rows, "note": fn[1] if isinstance(fn, tuple) else ""}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="aib bot list")
    ap.add_argument("--provider", default="codexbot")
    ap.add_argument("--all", action="store_true", help="每一家都列(含沒後端的,會明說)")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--exists", metavar="UUID", help="A4:只問這條對話還在不在(印 yes/no/unknown)")
    ap.add_argument("--gc", action="store_true", help="G2:刪掉對話已不在的綁定記號(.lock/.header-sent/.dm.sock;活橋鎖著的不刪)")
    ap.add_argument("--held", metavar="UUID", help="P6:這條對話是不是已被另一隻活橋占著(rc 0=沒被占 / 3=被占 / 4=量不到)")
    args = ap.parse_args(argv)
    if args.exists:
        r = _codex.thread_exists(_codex.default_endpoint(), args.exists)
        print("yes" if r is True else ("no" if r is False else "unknown"))
        return 0
    if args.held:
        # P6(09-25,多隻 CodexBot):開席前用**橋自己那把 flock** 問一次,不連 server、不列清單。
        #   rc 三態分開:0=沒被占、3=被占、4=量不到 —— 量不到 ≠ 沒被占,也 ≠ 被占(呼叫端自己決定怎麼處置)。
        try:
            holders = _held_by(args.provider, args.held)
        except Exception as exc:  # noqa: BLE001 —— 任何一個來源量不到都回 4,不當成「沒被占」
            print("unknown %s" % str(exc)[:160])
            return 4
        if holders:
            print("held " + " ".join(holders))
            return 3
        print("free")
        return 0
    providers = sorted(set(BACKENDS) | set(UNSUPPORTED)) if args.all else [args.provider]
    out = [list_with_ensure(p, args.limit) for p in providers]
    bound = _codex.bound_map()
    if args.json:
        here = _codex.current_cabin()
        for entry in out:
            where = _codex.last_bound_where(provider=entry["provider"])   # 10-12:所有艙;舊艙的標「舊艙」
            lb = {k: v[0] for k, v in where.items()}
            for r in entry["rows"]:
                r["bound"] = bound.get(r["id"], []); r["last_bound"] = lb.get(r["id"], 0); r["provider"] = entry["provider"]
                r["last_bound_cabin"] = (where.get(r["id"]) or (0, ""))[1]
                r["tail"] = _codex.tail_label(r["id"], bound, lb, entry["provider"], where, here)   # 選單直接用這個字面
                r["held_by"] = _held_by(entry["provider"], r["id"]) if entry["provider"] in ("codexbot", "grokbot", "claudebot", "musebot") else []   # P6:選單把被占的列變不可選
        print(json.dumps(out if args.all else out[0], ensure_ascii=False))
        return 0
    for entry in out:
        print("== %s ==" % entry["provider"])
        if not entry["supported"] or not entry["rows"]:
            print("  " + (entry["note"] or "(沒有對話)"))
            continue
        where = _codex.last_bound_where(provider=entry["provider"])
        print(_codex.render(entry["rows"], bound, last_bound={k: v[0] for k, v in where.items()}, provider=entry["provider"],
                            where=where, here=_codex.current_cabin()))
        if entry.get("note"):
            print("  " + entry["note"])
    # G2:綁定記號 vs server 全清單(這裡的 rows 只有 limit 條,要另外抓全清單才不會誤判)
    codex_entry = next((e for e in out if e["provider"] == "codexbot" and e["supported"] and e["rows"]), None)
    if codex_entry is not None:
        try:
            all_ids = {r["id"] for r in _codex.list_threads(_codex.default_endpoint(), 1000)}
        except Exception:  # noqa: BLE001
            all_ids = None
        if all_ids is not None:
            stale = _codex.stale_bindings(all_ids)
            if stale and args.gc:
                removed, skipped = _codex.gc_bindings(stale)
                print("  綁定記號已清 %d 個(對話已不在)%s" % (len(removed), (";%d 個活橋鎖著沒動" % len(skipped)) if skipped else ""))
            elif stale:
                print("  綁定記號的對話已不在:%d 個(aib bot list --gc 清掉):%s" % (len(stale), " ".join(f.name for f in stale[:5])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
