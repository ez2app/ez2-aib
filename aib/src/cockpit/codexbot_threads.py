#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""`aib bot list`:列出 Codex 桌面的對話(uuid ↔ 桌面側欄標題 ↔ 最後更新),並標出哪條已被哪個席綁著。

the maintainer 2026-09-24:「我如何判斷每個對話的 uuid?要怎麼跟 ChatGPT 的對話取得?」
以前要去 ~/.codex/sessions/…/rollout-…-<uuid>.jsonl 撈檔名,沒有標題可對。
app-server 的 `thread/list` 給得出桌面看到的標題,這裡只是把它印成人看得懂的表。
不需要寫入權(thread/list 是唯讀),桌面開著也能查。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import codexbot_bridge as _bridge  # noqa: E402
import seat_address as _seat_address  # noqa: E402
import seats_dir_lib as _seats  # noqa: E402


def default_endpoint() -> str:
    env = os.environ.get("AIB_CODEXBOT_ENDPOINT") or os.environ.get("AIB_CODEXAPP_ENDPOINT")
    if env:
        return env
    home = Path(os.environ.get("EZ2_AIB_HOME") or Path.home() / ".ez2-aib")
    return "unix://" + str(home / "run" / "codexbot" / "app-server.sock")


def _open_rpc(endpoint):
    rpc = _bridge.RPC(_bridge.connect(endpoint), lambda m: None)
    rpc.call("initialize", {"clientInfo": {"name": "ez2_aib_codexbot", "title": "aib bot list", "version": "0.1.0"},
                            "capabilities": {"experimentalApi": True}})
    rpc.notify("initialized")
    return rpc


def list_threads(endpoint: str, limit: int = 20, rpc=None) -> list[dict]:
    """回 [{id, name, updated, cwd}],最後更新新的在前。rpc 可注入(治具)。"""
    own = rpc is None
    rpc = rpc or _open_rpc(endpoint)
    try:
        # server 的 limit 是它自己的順序(不是最後更新);多抓一些、本地按最後更新排完再切。
        res = rpc.call("thread/list", {"limit": max(int(limit) * 4, 20)}, timeout=30) or {}
        rows = []
        for t in (res.get("data") or res.get("threads") or []):
            rows.append({"id": t.get("id") or "", "name": (t.get("name") or t.get("preview") or "").strip(),
                         "updated": int(t.get("updatedAt") or 0), "cwd": t.get("cwd") or ""})
        rows.sort(key=lambda r: r["updated"], reverse=True)
        return rows[:int(limit)]
    finally:
        if own:
            rpc.close()


def thread_exists(endpoint: str, thread_id: str, rpc=None):
    """A4:預設對話還在不在。True/False;連不上 server 回 None(量不到 ≠ 不在)。"""
    own = rpc is None
    try:
        rpc = rpc or _open_rpc(endpoint)
    except Exception:  # noqa: BLE001
        return None
    try:
        rpc.call("thread/read", {"threadId": thread_id, "includeTurns": False}, timeout=15)
        return True
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "no rollout found" in msg or "not found" in msg.lower() or "invalid thread" in msg.lower():
            return False
        return None
    finally:
        if own:
            try:
                rpc.close()
            except Exception:  # noqa: BLE001
                pass


BINDING_SUFFIXES = (".lock", ".header-sent", ".dm.sock")


def bindings_dir(base=None) -> Path:
    if base:
        return Path(base) / "codexbot-bindings"
    root, _src = _seats.state_root()
    return Path(root) / "codexbot-bindings"


def _seat_json_files(instances_root=None, all_cabins=False):
    """席位檔清單。參數或 EZ2_AIB_INSTANCES_ROOT 明寫覆寫優先;沒設才走 HUB 導出,不回退主艙。
    all_cabins(10-12 Bot 開發第 5 件,the maintainer 核准):沒明寫時看狀態根底下**所有艙**(含退役席)再加本艙 ——
    選單「上次綁定」要看得到舊艙綁過的(09-25 搬到 seats_dir_lib 時縮成只看本艙,換艙就全不見)。"""
    root = instances_root or os.environ.get("EZ2_AIB_INSTANCES_ROOT")
    if not root and all_cabins:
        files = []
        for pat in ("*/seats/w*.json", "*/seats/retired/*.json"):
            files.extend(glob.glob(os.path.join(_seats.state_root()[0], "instances", pat)))
        here = Path(_seats.seats_dir())
        files += glob.glob(str(here / "w*.json")) + glob.glob(str(here / "retired" / "*.json"))
        return list(dict.fromkeys(os.path.realpath(f) for f in files))
    if root:
        root = Path(root)
        files = []
        for pat in ("*/seats/w*.json", "*/seats/retired/*.json"):
            files.extend(glob.glob(str(root / pat)))
        return files
    seats = Path(_seats.seats_dir())
    return glob.glob(str(seats / "w*.json")) + glob.glob(str(seats / "retired" / "*.json"))


def stale_bindings(existing_ids, base=None) -> list[Path]:
    """G2:codexbot-bindings/ 裡記號檔(.lock/.header-sent/.dm.sock)的對話已不在 server 清單 ⇒ 陳舊。只列,不刪。"""
    d = bindings_dir(base)
    if not d.is_dir():
        return []
    keep = set(existing_ids)
    out = []
    for f in sorted(d.iterdir()):
        for suf in BINDING_SUFFIXES:
            if f.name.endswith(suf):
                tid = f.name[: -len(suf)]
                if tid not in keep:
                    out.append(f)
                break
    return out


def gc_bindings(stale: list[Path]) -> tuple[list[Path], list[Path]]:
    """G2:刪陳舊記號。.lock 被活橋 flock 著的不刪(對話不在清單裡但橋還活著 ⇒ 交給它自己收)。回 (刪了, 跳過)。"""
    import fcntl
    removed, skipped = [], []
    for f in stale:
        try:
            if f.name.endswith(".lock"):
                with f.open("a+") as fh:
                    try:
                        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        skipped.append(f); continue
            f.unlink(); removed.append(f)
        except OSError:
            skipped.append(f)
    return removed, skipped


def cabin_of(seat_file) -> str:
    """席位檔在哪一艙:instances/<艙>/seats/… 的 <艙>;不在 instances 底下(舊主艙)⇒ ""。"""
    parts = Path(str(seat_file)).parts
    idx = [i for i, p in enumerate(parts) if p == "instances"]
    return parts[idx[-1] + 1] if idx and idx[-1] + 2 < len(parts) else ""


def current_cabin() -> str:
    """本艙 id(由 seats_dir_lib 導出的席位目錄反推;認不出 ⇒ "")。"""
    return cabin_of(Path(_seats.seats_dir()) / "x.json")


def last_bound_map(instances_root: str | None = None, provider: str = "codexbot") -> dict[str, int]:
    """thread_id → 上次綁過它的席的起席 epoch(最大);細節見 last_bound_where。"""
    return {k: v[0] for k, v in last_bound_where(instances_root, provider).items()}


def last_bound_where(instances_root: str | None = None, provider: str = "codexbot") -> dict[str, tuple[int, str]]:
    """thread_id → 上次綁過它的席(含退役席)的起席 epoch(最大)。the maintainer 09-24:選單尾端要帶「上次綁定的月日時分(舊席位)」。

    10-09 a seat:以前只讀 desktop_thread_id(只有 codexbot 寫)⇒ claudebot 選單永遠沒有「上次」字樣。
    claudebot 讀席位檔 claude_session_id(開席那一刻),再加橋的 status.json agent_id(席上 /bind 只寫那裡)。
    grokbot/musebot 席位檔沒有綁定欄,只讀橋的 status.json agent_id(信箱橋三家都寫這一欄;哪幾家問 bot_caps)。
    10-12(Bot 開發第 5 件 + 第 15 件第 2 步,the maintainer 核准):回 thread_id → (epoch, 那一席在哪一艙);沒明寫根 ⇒ 看所有艙;
    開席那條(席位檔欄)與橋現在綁的(bot_binding.bound_id,codexbot 也算席上 /bind 換的)都算。"""
    import bot_binding
    out: dict[str, tuple[int, str]] = {}
    field = SEAT_BIND_FIELD.get(provider, "")
    for f in _seat_json_files(instances_root, all_cabins=True):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("provider") != provider:
            continue
        ts = int(d.get("started_epoch") or d.get("last_used_epoch") or 0)
        ids = [d.get(field)] if field else []
        ids.append(bot_binding.bound_id(provider, d.get("session_uuid"), d)[0])
        for tid in ids:
            if isinstance(tid, str) and tid and ts > out.get(tid, (0, ""))[0]:
                out[tid] = (ts, cabin_of(f))
    return out


# 席位檔裡記「這一席綁哪條」的欄位(aib 開席時寫);grokbot/musebot 只寫在橋的 status.json。
#   10-11 a seat(第 15 件第 1 步):對照表只留一份,在 bot_binding。
try:
    from bot_binding import SEAT_FIELD as SEAT_BIND_FIELD
except ImportError:  # pragma: no cover —— 單獨拷走這支時的退路
    SEAT_BIND_FIELD = {"codexbot": "desktop_thread_id", "claudebot": "claude_session_id"}


def bound_map(secrets_file: str | None = None, instances_root: str | None = None) -> dict[str, list[str]]:
    """thread_id → 誰綁著:['預設(codexbot.env)', '@五碼(活席)', …]。"""
    out: dict[str, list[str]] = {}
    sf = Path(secrets_file or os.environ.get("AIB_CODEXBOT_SECRETS_FILE") or Path.home() / ".ez2-aib" / "secrets" / "codexbot.env")
    try:
        for line in sf.read_text(encoding="utf-8").splitlines():
            if line.startswith("AIB_CODEXBOT_THREAD_ID="):
                out.setdefault(line.split("=", 1)[1].strip().strip("'\""), []).append("預設(codexbot.env)")
    except OSError:
        pass
    for f in _seat_json_files(instances_root):
        if "/retired/" in f.replace("\\", "/"):
            continue
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("provider") != "codexbot" or d.get("exit_reason"):
            continue
        import bot_binding   # 10-12 第 15 件第 2 步(the maintainer 核准):現在綁哪條走同一支(席上 /bind 後席位檔是舊的)
        tid = bot_binding.bound_id("codexbot", d.get("session_uuid"), d)[0]
        if not tid:
            continue
        pid = d.get("pid")
        try:
            os.kill(int(pid), 0)
        except (TypeError, ValueError, ProcessLookupError):
            continue
        except PermissionError:
            pass
        code = (d.get("session_uuid") or "")[-5:]
        out.setdefault(tid, []).append("@%s(活席 %s)" % (code or "?", d.get("window_id") or ""))
    return out


HELD_NAME_UNKNOWN = "(占用者名字查不到)"


def live_codex_seat_threads(base=None) -> dict[str, str]:
    """10-07 a seat(各 bot 比對):AIB 自己**活著的** codex 席正在用的對話 {thread_id: 席碼}。

    codex CLI 席的席位檔 session_uuid 就是那段 Codex 對話的 id(與桌面 thread id 同一個號;lsof 讀 rollout 拿到)。
    以前只靠 seat_liveness 判,而它對 AIB 開的 codex 席是瞎的(見 held_by 來源二的註解)⇒ 選單會把 AIB 自己 codex 席
    的對話列成「沒人占」,綁上就兩席串線。claudebot 早有同一件事(claudebot_threads.aib_seat_sessions)。
    只算活的(席位檔 pid 還在、不在 retired/):退役席那段對話沒人在用,接手不會串線。讀不到的檔跳過。"""
    root = Path(base) if base else bindings_dir(base).parent
    out: dict[str, str] = {}
    for pat in ("instances/*/seats/w*.json", "seats/w*.json"):
        for f in root.glob(pat):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(d, dict) or d.get("provider") != "codex":
                continue
            u = str(d.get("session_uuid") or "")
            try:
                uuid.UUID(u)
            except ValueError:
                continue
            try:
                pid = int(d.get("pid") or 0)
            except (ValueError, TypeError):
                continue
            if pid <= 0:
                continue
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            except OSError:
                pass            # 別的使用者的行程(權限)= 還活著
            out[u.lower()] = str(d.get("seat_id") or _seat_address.uuid_tail5(u) or "")   # 五碼取法單源(test-seat-address)
    return out


def held_by(thread_id: str, base=None, instances_root: str | None = None) -> list[str]:
    """P6(09-25,多隻 CodexBot):這條桌面對話現在是不是被另一隻**活橋**占著。回空 list = 沒被占。

    ★ 判準用橋自己那把鎖,不另造一份:codexbot_bridge 啟動時對 `codexbot-bindings/<thread>.lock`
      取 `LOCK_EX|LOCK_NB`,拿不到就丟「this desktop thread already has a live AIB bridge」。
      這裡用同一個檔、同一種 flock 先問一次 ⇒ 開席前的回答與橋啟動時的回答必然一致。
      (席位檔 pid 活不活是**另一個來源**,會跟鎖不同步;它只拿來報占用者叫什麼名字。)
    ★ 鎖檔不存在 ⇒ 沒被占,而且**不建檔**(探針不該留下它在量的東西)。
    ★ 探的那一瞬間本行程會短暫拿到鎖;若橋剛好在同一刻啟動,橋會失敗一次——與 gc_bindings 同一個取捨。
    """
    import fcntl
    names: list[str] = []
    # 來源一:另一隻 AIB 橋(codexbot)——橋自己那把 flock。
    lock = bindings_dir(base) / (thread_id + ".lock")
    if lock.exists():
        with lock.open("r") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                names += [n for n in bound_map(instances_root=instances_root).get(thread_id, [])
                          if not n.startswith("預設(")] or [HELD_NAME_UNKNOWN]
            else:
                fcntl.flock(fh, fcntl.LOCK_UN)
    # 來源二:CLI 席(例如本艙的 codex 席)正在用同一段對話——它不拿上面那把鎖,flock 看不到它。
    #   P6 上線前真機實測:codex CLI 席 a seat 自己的對話出現在 CodexBot 選單裡,顯示成「沒人占、可以選」。
    #   判準委給 seat_liveness(全機「誰持有這段對話」的唯一來源),不在這裡再寫一份。
    #   ★ 已知:seat_liveness 目前把 `codex resume --旗標 … <uuid>` 的第一個旗標當成 uuid ⇒ 對 AIB 開的
    #     codex 席是瞎的;已通報擁有者 a seat。那邊修好,這裡自動擋得到,不用再改。
    # 來源三(10-07 a seat):AIB 自己活著的 codex 席(席位檔 session_uuid)——不等 seat_liveness 修好。
    seat = live_codex_seat_threads(base).get(str(thread_id).lower())
    if seat:
        names.append("AIB codex 席 @" + seat)
    import seat_liveness as _sl
    h = _sl.holders(thread_id, aib_home=str(bindings_dir(base).parent))
    if _sl.is_held(h):
        names.append("CLI 席 " + (_sl.describe(h) or HELD_NAME_UNKNOWN))
    return names


def tail_label(thread_id: str, bound: dict[str, list[str]], last_bound: dict[str, int], provider: str = "codexbot",
               where: dict[str, tuple[int, str]] | None = None, here: str | None = None) -> str:
    """列尾:「上次 MM-DD HH:MM · <bot>」+ 誰綁著。選單與 aib bot list 同一個字面來源。
    where/here(10-12 Bot 開發第 5 件):上次綁它的席不在本艙 ⇒ 時間後面加「(舊艙 <艙位碼>)」。"""
    parts = []
    ts = last_bound.get(thread_id)
    if ts:
        parts.append("上次 " + time.strftime("%m-%d %H:%M", time.localtime(ts)))
        cabin = ((where or {}).get(thread_id) or (0, ""))[1]
        if cabin and here and cabin != here:   # 認不出本艙(不在任何艙裡跑)⇒ 不標,免得每一條都變舊艙
            parts.append("(舊艙 %s)" % cabin.rsplit("-", 1)[-1][-4:])
    parts.append("· " + provider)
    b = bound.get(thread_id) or []
    if b:
        parts.append("[" + " ".join(b) + "]")
    return " ".join(parts)


def render(rows: list[dict], bound: dict[str, list[str]], now=None, last_bound: dict[str, int] | None = None, provider: str = "codexbot",
           where: dict[str, tuple[int, str]] | None = None, here: str | None = None) -> str:
    now = time.time() if now is None else now
    last_bound = last_bound if last_bound is not None else {}
    lines = ["%-36s  %-11s  %-44s  %s" % ("thread uuid", "最後更新", "桌面側欄標題", "上次綁定 · bot [誰綁著]")]
    for r in rows:
        when = time.strftime("%m-%d %H:%M", time.localtime(r["updated"])) if r["updated"] else "-"
        name = r["name"][:44]
        lines.append("%-36s  %-11s  %-44s  %s" % (r["id"], when, name,
                                                   tail_label(r["id"], bound, last_bound, provider, where, here)))
    lines.append("")
    lines.append(BIND_HINTS.get(provider, BIND_HINTS["codexbot"]))
    return "\n".join(lines)


# 10-07 root a seat:以前三家都印 codexbot 的綁法(`aib bot list --provider claudebot` 底下叫你跑 codexbot 指令)。
BIND_HINTS = {
    "codexbot": "綁法:aib bot connect codexbot --thread <uuid>;或開席時 aib codexbot dgrs root --thread <uuid>(只這一席)。",
    "grokbot": "綁法:aib bot connect grokbot --agent <agentId>,再右鍵開 GrokBot 席(或 AIB_GROKBOT_AGENT_ID=<agentId> aib grokbot dgrs root --again)。",
    "claudebot": "綁法:右鍵開 ClaudeBot 席在選單點一條;或 AIB_CLAUDEBOT_SESSION_ID=<uuid> aib claudebot dgrs root --again。"
                 "AIB 自己的席與 claude -p 一次性對話不列(綁了會兩席串線/叫不醒)。",
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="aib bot list")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--endpoint", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    endpoint = args.endpoint or default_endpoint()
    try:
        rows = list_threads(endpoint, args.limit)
    except Exception as exc:  # noqa: BLE001
        print("aib bot list: 連不上 Codex App Server(%s):%s\n先跑一次 aib bot connect codexbot --thread <uuid>,或 python3 src/cockpit/codexbot_server.py ensure" % (endpoint, str(exc)[:160]), file=sys.stderr)
        return 2
    bound = bound_map(); lb = last_bound_map()
    if args.json:
        for r in rows:
            r["bound"] = bound.get(r["id"], []); r["last_bound"] = lb.get(r["id"], 0); r["provider"] = "codexbot"
        print(json.dumps(rows, ensure_ascii=False))
        return 0
    print(render(rows, bound, last_bound=lb))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
