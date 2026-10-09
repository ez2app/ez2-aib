#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""關席收尾小幫手(the maintainer 09-25):bot 席不管怎麼死,都把對方 app 的名稱改回「原名 ( 未綁定AIB席位 )」。

為什麼要另一個行程:09-25 真機三次實驗 —— 無終端機時 SIGHUP ⇒ 正常收尾;在 tmux 裡(有終端機)⇒ 收尾沒跑、
連 pane 一起不見。[X] 關窗時終端機一定會消失,殺手也不只 SIGHUP ⇒ 「在自己快死時收尾」不可靠。

做法:
  橋綁定成功後 spawn() 一個小幫手,放在**獨立 session**(pane 的訊號打不到),兩者之間一條管子,
  小幫手只拿讀端、只做一件事:等管子斷。
  · 橋正常結束且自己已改好名 ⇒ 先寫 DONE 再關 ⇒ 小幫手看到 DONE 就走,什麼都不做。
  · 橋突然死(關窗 / 被殺 / 當掉)⇒ 管子斷了卻沒有 DONE ⇒ 小幫手接手收尾。
  動手前先搶那條對話 / 那個 agent 的鎖;搶不到 = 新席已綁上,不動(免得把新席的名字改回未綁定)。

★ 這支在 grokbot 與 codexbot-seat-name 兩個分支上必須逐位元組相同(同 bot_seat_name.py 的 PSC 條件)。
  grok 的收尾用到 sand_bridge / grokbot_threads 的函式,一律延遲 import(codexbot 分支不會走到)。

每一步記進 <狀態根>/logs/bot-closer.jsonl。
尺用:AIB_BOT_CLOSER_CODEX_RECORD=<檔> ⇒ codexbot 收尾不連 app-server,把「本來要送的呼叫」寫進那個檔;
      AIB_BOT_CLOSER_START_DELAY_S=<秒> ⇒ 父行程走了之後先等一下再動手(讓尺能確定「新席先占鎖」的順序)。
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

DONE = b"DONE\n"
GROK_CLOSE_WAIT_S = 45.0
ENV_RECORD = "AIB_BOT_CLOSER_CODEX_RECORD"


def _state_root(explicit=None) -> Path:
    if explicit:
        return Path(explicit)
    for key in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        v = os.environ.get(key)
        if v:
            return Path(v).expanduser()
    return Path.home() / ".ez2-aib"


def _log(root: Path, **row) -> None:
    try:
        d = root / "logs"
        d.mkdir(parents=True, exist_ok=True)
        row = dict({"ts": int(time.time()), "pid": os.getpid()}, **row)
        with (d / "bot-closer.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


class Closer:
    """橋端握著的那一頭。done() = 我自己收好了,小幫手不用動。"""

    def __init__(self, wfd: int, proc):
        self.wfd, self.proc = wfd, proc

    def done(self) -> None:
        if self.wfd is None:
            return
        try:
            os.write(self.wfd, DONE)
        except OSError:
            pass
        self.close()

    def close(self) -> None:
        if self.wfd is not None:
            try:
                os.close(self.wfd)
            except OSError:
                pass
            self.wfd = None


def spawn(kind: str, params: dict, state_root=None):
    """起小幫手。失敗回 None(收尾小幫手是附加功能,起不來不擋開席)。"""
    root = _state_root(state_root)
    try:
        r, w = os.pipe()            # 兩端預設都不可繼承;只有讀端用 pass_fds 交給小幫手
        (root / "logs").mkdir(parents=True, exist_ok=True)
        err = open(root / "logs" / "bot-closer.err", "a", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                [sys.executable, str(HERE / "bot_closer.py"), "watch", "--kind", kind, "--fd", str(r),
                 "--params", json.dumps(params, ensure_ascii=False), "--state-root", str(root)],
                stdin=subprocess.DEVNULL, stdout=err, stderr=err, pass_fds=(r,),
                start_new_session=True, close_fds=True)
        finally:
            err.close()
            os.close(r)
    except OSError as exc:
        _log(root, kind=kind, event="spawn_failed", error=str(exc)[:200])
        return None
    _log(root, kind=kind, event="spawned", child=proc.pid, parent=os.getpid())
    return Closer(w, proc)


def _wait_parent(fd: int) -> bool:
    """讀到 EOF 為止。回 True = 父行程說了 DONE。"""
    buf = b""
    while True:
        try:
            chunk = os.read(fd, 64)
        except InterruptedError:
            continue
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
    return DONE in buf


def _try_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh


def closer_marker(root: Path, tid: str) -> Path:
    """「這把對話鎖是我這個收尾小幫手在拿」的記號檔(內容=小幫手 pid)。

    09-25 a seat(艙 9260 a seat 重開實見):tmux respawn-pane -k 之後,舊橋的小幫手拿著對話鎖改名,
    新橋比它早 2 秒起來 ⇒ 撞鎖 rc=2 直接退出。新橋看這個記號分辨「同一席的小幫手在收尾(等它)」
    與「別席的活橋占著(照舊立刻擋)」。★ 先寫記號再拿鎖、先放鎖再刪記號 ⇒ 鎖被小幫手拿著的每一刻記號都在。"""
    return root / "codexbot-bindings" / (tid + ".closer")


def _write_marker(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
        tmp.write_text("%d\n" % os.getpid(), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _drop_marker(path: Path) -> None:
    try:
        if path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            path.unlink()
    except (OSError, ValueError):
        pass


def _mark_status(path: Path, **updates) -> None:
    try:
        d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        d = {}
    d.update(updates)
    tmp = path.with_name(path.name + ".closer.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def close_codexbot(p: dict, root: Path) -> str:
    import bot_seat_name
    tid, session = p["thread_id"], p.get("session_id") or ""
    marker = closer_marker(root, tid)
    _write_marker(marker)                # 先寫記號再拿鎖(見 closer_marker)
    lock = _try_lock(root / "codexbot-bindings" / (tid + ".lock"))
    if lock is None:
        _drop_marker(marker)
        return "skipped:new_seat_holds_thread"
    try:
        record = os.environ.get(ENV_RECORD)
        if record:                                   # 尺用:不連 app-server
            cur = p.get("record_current_name") or ""
            unbound = bot_seat_name.with_unbound(cur, fallback="CodexBot")
            with open(record, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"method": "thread/name/set", "params": {"threadId": tid, "name": unbound}},
                                    ensure_ascii=False) + "\n")
            result = "renamed(record)"
        else:
            import codexbot_threads
            rpc = codexbot_threads._open_rpc(p["endpoint"])
            try:
                th = rpc.call("thread/read", {"threadId": tid, "includeTurns": False}, timeout=15)["thread"]
                cur = str(th.get("name") or "").strip()
                unbound = bot_seat_name.with_unbound(cur, fallback="CodexBot")
                if unbound == cur:
                    result = "already_unbound"
                else:
                    rpc.call("thread/name/set", {"threadId": tid, "name": unbound}, timeout=15)
                    result = "renamed"
            finally:
                rpc.close()
        if session:
            st = root / "bridge" / session / "status.json"
            try:
                state = json.loads(st.read_text(encoding="utf-8")).get("state")
            except (OSError, ValueError):
                state = None
            if state not in (None, "stopped", "failed"):
                _mark_status(st, state="stopped", closed_by="bot_closer")   # 橋突然死時狀態檔會停在 connected
        return result
    finally:
        lock.close()
        _drop_marker(marker)             # 先放鎖再刪記號


def close_grokbot(p: dict, root: Path) -> str:
    import bot_seat_name
    import grokbot_threads
    agent, session = p["agent_id"], p["session_id"]
    lock = _try_lock(grokbot_threads.lock_path(agent, root))
    if lock is None:
        return "skipped:new_seat_holds_agent"
    try:
        try:
            current = {r["id"]: r["name"] for r in grokbot_threads.list_agents(10 ** 6)}.get(agent)
        except (grokbot_threads.RosterSchemaChanged, grokbot_threads.RosterUnreadable):
            current = None
        seat = root / "bridge" / session
        st = seat / "status.json"
        if current is None:
            _mark_status(st, state="stopped", closed_by="bot_closer")
            return "skipped:roster_unreadable"
        wanted = bot_seat_name.with_unbound(current)
        if wanted == current:
            _mark_status(st, state="stopped", closed_by="bot_closer")
            return "already_unbound"
        # bot 收信時要找得到「活席」:由小幫手頂替(pid 換成自己、狀態 closing)
        _mark_status(st, state="closing", pid=os.getpid(), agent_id=agent, closed_by="bot_closer")
        inbox = seat / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        ts = time.time()
        name = "%013d-aib-system.json" % int(ts * 1000)
        code = st.exists() and json.loads(st.read_text(encoding="utf-8")).get("code") or session[-5:]
        (inbox / (name + ".tmp")).write_text(json.dumps({
            "schema": "aib-sand-bridge-msg/v1", "from": "aib-system", "to": "cursor", "provider": "grokbot",
            "code": code, "session_uuid": session,
            "text": "[AIB 系統] 這一席 @%s 已關閉。請用 update_state(target: profile) 把你在 Grok Bot app 裡的"
                    "名稱改成「%s」。改好不用回覆。" % (code, wanted),
            "created_epoch": int(ts)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(inbox / (name + ".tmp"), inbox / name)
        os.environ["AIB_GROKBOT_AGENT_ID"] = agent
        import sand_bridge
        res = sand_bridge.wake_cursor_chat(session, code, "[AIB 系統] 關席改名", "grokbot")
        _log(root, kind="grokbot", event="rang", status=res.get("status"), reason=res.get("reason"))
        try:
            wait_s = max(0.0, float(os.environ.get("AIB_GROKBOT_CLOSE_WAIT_S") or GROK_CLOSE_WAIT_S))
        except ValueError:
            wait_s = GROK_CLOSE_WAIT_S
        claimed = inbox / "claimed" / name
        deadline = time.time() + wait_s
        while time.time() < deadline and not claimed.exists():
            time.sleep(0.2)
        _mark_status(st, state="stopped", closed_by="bot_closer")
        return "asked_bot" if claimed.exists() else "bot_did_not_collect"
    finally:
        lock.close()


def close_claudebot(p: dict, root: Path) -> str:
    """10-09 a seat(the maintainer 令):claudebot 席死了 ⇒ 把那條 Claude 對話的標題改回「項目名稱 ( 未綁定AIB席位 )」。
    不用請誰改:標題就寫在本機 transcript(claudebot_hook.release_title,與綁定時 ensure_title 同一處)。
    動手前先搶那條對話的鎖;搶不到 = 新席已綁上,不動(同 grokbot/codexbot)。"""
    import claudebot_hook
    import claudebot_threads
    sid, session = p["session_id_claude"], p["session_id"]
    seat = root / "bridge" / session
    # 先看這一席有沒有真的替它改過標題(hook 的 claude-titled 記著席碼);沒改過就什麼都不碰(不搶鎖、不動 status)。
    #   橋沒經過 hook 的情形(測試沙箱、對話從沒說過話)都走這條,不會跟下一隻搶那把鎖。
    try:
        parts = (seat / claudebot_hook.TITLE_MARK).read_text(encoding="utf-8").strip().split("\t")
        titled = len(parts) > 1 and bool(parts[1])
    except OSError:
        titled = False
    if not titled:
        return "skipped:never_titled"
    lock = _try_lock(claudebot_threads.lock_path(sid, root))
    if lock is None:
        return "skipped:new_seat_holds_session"
    try:
        done = claudebot_hook.release_title(sid, seat)
        _mark_status(seat / "status.json", state="stopped", closed_by="bot_closer")
        return "title_unbound" if done else "skipped:no_transcript"
    finally:
        lock.close()


CLOSERS = {"codexbot": close_codexbot, "grokbot": close_grokbot, "claudebot": close_claudebot}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bot_closer.py")
    sub = ap.add_subparsers(dest="cmd")
    w = sub.add_parser("watch")
    w.add_argument("--kind", choices=tuple(CLOSERS), required=True)
    w.add_argument("--fd", type=int, required=True)
    w.add_argument("--params", required=True)
    w.add_argument("--state-root", default="")
    args = ap.parse_args(argv)
    if args.cmd != "watch":
        ap.print_help(sys.stderr)
        return 2
    root = _state_root(args.state_root or None)
    params = json.loads(args.params)
    done = _wait_parent(args.fd)
    if done:
        _log(root, kind=args.kind, event="parent_done")
        return 0
    _log(root, kind=args.kind, event="parent_gone")
    try:   # 尺用:讓「新席先占住鎖」的順序可以確定(正式環境不設 = 0)
        time.sleep(max(0.0, float(os.environ.get("AIB_BOT_CLOSER_START_DELAY_S") or 0)))
    except ValueError:
        pass
    try:
        result = CLOSERS[args.kind](params, root)
    except Exception as exc:  # noqa: BLE001 — 收尾失敗只留痕
        _log(root, kind=args.kind, event="close_failed", error="%s: %s" % (type(exc).__name__, str(exc)[:200]))
        return 1
    _log(root, kind=args.kind, event="closed", result=result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
