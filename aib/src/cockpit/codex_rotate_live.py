# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""把「額度用完」接到換號、只在那一格登入、並另開 luna 新席。

不寫所有席共用的登入檔。整艙預設開；某一席可單獨關掉。
測試要注入假的登入與開席，不要呼叫這台機器上的 codex。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

import usage_accounts as ua
import usage_failover as uf

LOGIN_TIMEOUT_S = 180
READY_TIMEOUT_S = 90
WATCH_SLEEP_S = 2


def shared_codex_home() -> Path:
    home = Path(os.environ.get("HOME") or "").expanduser()
    return (home / ".codex").resolve() if str(home) else Path("/.codex")


def is_shared_codex_home(path: str | Path) -> bool:
    try:
        got = Path(path).resolve()
    except OSError:
        return True
    return got == shared_codex_home()


def login_env(slot_dir: str | Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """登入環境只指向這一格。不讓它落回共用目錄。"""
    folder = Path(slot_dir).resolve()
    if is_shared_codex_home(folder):
        raise RuntimeError("refusing shared codex home")
    env = dict(base or os.environ)
    env["CODEX_HOME"] = str(folder)
    env["BROWSER"] = "/usr/bin/true"
    return env


def _open_url_once(url: str) -> None:
    if os.path.isfile("/usr/bin/open"):
        os.spawnlp(os.P_NOWAIT, "/usr/bin/open", "open", url)


def login_argv(codex_bin: str) -> list[str]:
    return [codex_bin, "login"]


def ready_argv(codex_bin: str) -> list[str]:
    return [codex_bin, "exec", "--model", uf.LUNA_MODEL, "只回 READY"]


def open_argv(codex_bin: str, resume_uuid: str, model: str | None = None) -> list[str]:
    chosen = str(model or uf.LUNA_MODEL).strip() or uf.LUNA_MODEL
    return [codex_bin, "resume", "--model", chosen, resume_uuid]


def _stop(proc: Any) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.kill()
    except OSError:
        pass


def _pull_text(stdout: Any, wait_s: float) -> str:
    """有管道就等一下子；測試用的假輸出沒有 fileno，讀到多少算多少。"""
    if stdout is None:
        return ""
    try:
        fd = stdout.fileno()
    except Exception:
        fd = None
    if fd is not None:
        import select
        ready, _, _ = select.select([fd], [], [], wait_s)
        if not ready:
            return ""
        try:
            piece = os.read(fd, 4096)
        except OSError:
            return ""
        return piece.decode("utf-8", "ignore") if isinstance(piece, bytes) else str(piece or "")
    try:
        piece = stdout.read(4096)
    except Exception:
        return ""
    if isinstance(piece, bytes):
        return piece.decode("utf-8", "ignore")
    return str(piece or "")


def _read_url(proc: Any, clock: Callable[[], float], sleep: Callable[[float], None], budget: float) -> str:
    import re
    deadline = clock() + budget
    blob = ""
    while clock() < deadline:
        blob += _pull_text(getattr(proc, "stdout", None), 0.05)
        match = re.search(r"https://auth\.openai\.com/\S+", blob)
        if match:
            return match.group(0).rstrip(").,;'\"")
        if proc.poll() is not None:
            break
        sleep(0.01)
    return ""


def slot_login(
    slot_dir: str,
    *,
    codex_bin: str = "codex",
    shared_auth: str | None = None,
    popen: Callable[..., Any] | None = None,
    open_url: Callable[[str], None] | None = None,
    announce: Callable[[str], None] | None = None,
    timeout_s: float = LOGIN_TIMEOUT_S,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """在這一格的目錄跑 codex login。共用登入檔若被改到，立刻停。"""
    folder = Path(slot_dir)
    if is_shared_codex_home(folder):
        return {"ok": False, "rejected": True, "detail": "refusing shared home"}
    login_lock = _acquire_lock(os.environ.get("EZ2_AIB_HOME") or str(Path.home() / ".ez2-aib"))
    if login_lock is False:
        return {"ok": False, "busy": True, "detail": "另一個登入還在等"}
    try:
        return _slot_login_locked(
            folder,
            codex_bin=codex_bin,
            shared_auth=shared_auth,
            popen=popen,
            open_url=open_url,
            announce=announce,
            timeout_s=timeout_s,
            clock=clock,
            sleep=sleep,
        )
    finally:
        if login_lock not in (None, False):
            try:
                login_lock.close()
            except OSError:
                pass


def _slot_login_locked(
    folder: Path,
    *,
    codex_bin: str,
    shared_auth: str | None,
    popen: Callable[..., Any] | None,
    open_url: Callable[[str], None] | None,
    announce: Callable[[str], None] | None,
    timeout_s: float,
    clock: Callable[[], float] | None,
    sleep: Callable[[float], None] | None,
) -> dict[str, Any]:
    auth = folder / "auth.json"
    before = auth.read_bytes() if auth.is_file() else b""
    snap = uf._file_snap(shared_auth)
    clock = clock or time.time
    sleep = sleep or time.sleep
    popen = popen or __import__("subprocess").Popen
    try:
        env = login_env(folder)
    except RuntimeError as exc:
        return {"ok": False, "rejected": True, "detail": str(exc)}
    proc = popen(
        login_argv(codex_bin),
        cwd=str(folder),
        env=env,
        stdout=__import__("subprocess").PIPE,
        stderr=__import__("subprocess").STDOUT,
        stdin=__import__("subprocess").DEVNULL,
    )
    url = _read_url(proc, clock, sleep, min(15.0, timeout_s))
    if url and announce:
        announce(url)
    if url:
        # BROWSER 已設成不開頁。這裡只開一次，呼叫端不要再打開。
        opener = open_url or _open_url_once
        try:
            opener(url)
        except Exception:
            pass
    deadline = clock() + timeout_s
    while clock() < deadline:
        if uf._file_snap(shared_auth) != snap:
            _stop(proc)
            return {"ok": False, "rejected": True, "detail": "shared touched", "url": url}
        now_bytes = auth.read_bytes() if auth.is_file() else b""
        if now_bytes and now_bytes != before:
            _stop(proc)
            return {"ok": True, "url": url}
        if proc.poll() is not None:
            break
        sleep(0.05)
    _stop(proc)
    if uf._file_snap(shared_auth) != snap:
        return {"ok": False, "rejected": True, "detail": "shared touched", "url": url}
    return {"ok": False, "url": url, "detail": "login incomplete"}


def interpret_ready(blob: str) -> dict[str, Any]:
    """沒有終端機時不要記成登入失敗，否則這席永遠換不走。"""
    text = str(blob or "")
    if uf.quota_limit_text(text):
        return {"ok": False, "no_quota": True}
    if "READY" in text:
        return {"ok": True}
    lowered = text.lower()
    if "not a terminal" in lowered or not text.strip():
        return {"ok": True, "unverified": True}
    return {"ok": False, "rejected": True, "detail": text[-180:]}


def slot_ready(
    auth_path: str,
    *,
    codex_bin: str = "codex",
    shared_auth: str | None = None,
    popen: Callable[..., Any] | None = None,
    timeout_s: float = READY_TIMEOUT_S,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """只問這一格能不能用 luna 回 READY。不碰共用登入檔。"""
    folder = Path(auth_path).resolve().parent
    if is_shared_codex_home(folder):
        return {"ok": False, "rejected": True, "detail": "refusing shared home"}
    snap = uf._file_snap(shared_auth)
    clock = clock or time.time
    sleep = sleep or time.sleep
    proc = _start_ready(ready_argv(codex_bin), str(folder), login_env(folder), popen)
    blob = ""
    deadline = clock() + timeout_s
    while clock() < deadline:
        if uf._file_snap(shared_auth) != snap:
            _stop(proc)
            return {"ok": False, "rejected": True, "detail": "shared touched"}
        blob += _pull_text(getattr(proc, "stdout", None), 0.05)
        if proc.poll() is not None:
            break
        sleep(0.01)
    _stop(proc)
    if uf._file_snap(shared_auth) != snap:
        return {"ok": False, "rejected": True, "detail": "shared touched"}
    return interpret_ready(blob)


def _start_ready(argv: list[str], cwd: str, env: dict[str, str], popen: Callable[..., Any] | None):
    """有人注入假程式就用它。否則給一個終端機，codex 才肯回答。"""
    if popen is not None:
        return popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=__import__("subprocess").PIPE,
            stderr=__import__("subprocess").STDOUT,
            stdin=__import__("subprocess").DEVNULL,
        )
    import pty
    import subprocess
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        argv, cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave, close_fds=True,
    )
    os.close(slave)
    proc.stdout = os.fdopen(master, "rb", buffering=0)
    return proc


def hub_session_name() -> str:
    """新席開在內層 aihub。外層艙名不是這個視窗所在的 session。"""
    return os.environ.get("EZ2_HUB_SESSION") or "aihub"


def account_id_for_email(email: str) -> str:
    want = str(email or "").strip().lower()
    if not want:
        return ""
    for rec in uf._oauth_accounts_in_order():
        if uf._slot_email(rec) == want:
            return str(rec.get("id") or "")
    return ""


def open_isolated_seat(
    result: dict[str, Any],
    *,
    popen: Callable[..., Any] | None = None,
    tmux_bin: str = "",
    socket: str = "",
    session: str = "",
    codex_bin: str = "codex",
    window_name: str = "",
    target_window: str = "",
) -> dict[str, Any]:
    """續跑只發生在原席。沒有原席編號就停，不另開。不寫共用登入。"""
    del window_name
    seat = result.get("seat") if isinstance(result.get("seat"), dict) else {}
    auth = str(seat.get("auth") or "")
    resume = str(result.get("resume_uuid") or "")
    if not auth or not resume:
        return {"ok": False, "detail": "missing seat"}
    home = str(Path(auth).resolve().parent)
    if is_shared_codex_home(home):
        return {"ok": False, "detail": "refusing shared home"}
    if not str(target_window or "").strip():
        return {"ok": False, "reason": "no_original_seat", "detail": "沒有原席，不另開"}
    if not tmux_bin or not socket or not session:
        return {"ok": False, "detail": "no tmux"}
    where = str(target_window)
    target = "%s:%s" % (session, where)
    commands = [
        [tmux_bin, "-L", socket, "set-option", "-w", "-t", target, "remain-on-exit", "on"],
        [tmux_bin, "-L", socket, "respawn-window", "-k", "-t", target,
         "env", "CODEX_HOME=" + home, *open_argv(codex_bin, resume, str(result.get("model") or ""))],
    ]
    popen = popen or __import__("subprocess").Popen
    argv = commands[-1]
    for cmd in commands:
        proc = popen(cmd, stdout=__import__("subprocess").DEVNULL, stderr=__import__("subprocess").DEVNULL)
        rc = proc.wait() if hasattr(proc, "wait") else 0
        if rc != 0:
            return {"ok": False, "detail": "tmux rc %s" % rc, "argv": cmd}
    return {"ok": True, "where": where, "argv": argv, "codex_home": home}


def handle_quota_text(
    text: str,
    *,
    resume_uuid: str,
    after_id: str | None = None,
    now: float | None = None,
    login: Callable[[str], dict[str, Any]] | None = None,
    ready: Callable[[str], dict[str, Any]] | None = None,
    opener: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    transcript: str | None = None,
    seat_dir: str | None = None,
    memory_note: str = "",
    existing_memory: str = "",
    shared_auth: str | None = None,
    log_dir: str | None = None,
    codex_bin: str = "codex",
    tmux_bin: str = "",
    socket: str = "",
    session: str = "",
    target_window: str = "",
    model: str = "",
    env_source: str = "",
) -> dict[str, Any]:
    """額度用完才換。通過後只在原席接續；沒開成不把登入寫回共用檔。"""
    if seat_dir is None:
        root = os.environ.get("EZ2_AIB_HOME") or str(Path.home() / ".ez2-aib")
        seat_dir = str(Path(root) / "luna-seats" / (str(resume_uuid or "none")[:8]))
    if login is None:
        login = lambda slot: slot_login(slot, codex_bin=codex_bin, shared_auth=shared_auth)
    if ready is None:
        ready = lambda auth: slot_ready(auth, codex_bin=codex_bin, shared_auth=shared_auth)
    if not memory_note:
        memory_note = "從舊帳換過來。還沒做完的工作留在原席。"
    result = uf.on_codex_error(
        text,
        resume_uuid=resume_uuid,
        after_id=after_id,
        now=now,
        login=login,
        ready=ready,
        transcript=transcript,
        seat_dir=seat_dir,
        memory_note=memory_note,
        existing_memory=existing_memory,
        shared_auth=shared_auth,
        log_dir=log_dir,
        model=model or None,
        env_source=env_source or None,
    )
    if not result.get("ok"):
        return result
    if opener is None:
        opener = lambda item: open_isolated_seat(
            item,
            tmux_bin=tmux_bin,
            socket=socket,
            session=session,
            codex_bin=codex_bin,
            target_window=target_window,
        )
    opened = opener(result) or {}
    if not opened.get("ok"):
        failed = dict(result)
        failed["ok"] = False
        failed["reason"] = "seat_not_opened"
        failed["line"] = "驗證過了，原席沒接上"
        failed["open"] = opened
        return failed
    done = dict(result)
    where = str(opened.get("where") or "")
    done["where"] = where
    done["open"] = opened
    done["line"] = "已從 %s 換到 %s，接 %s。原席 %s" % (
        done.get("old_email") or "舊帳",
        done.get("new_email") or "新帳",
        str(done.get("resume_uuid") or "")[:8],
        where or "不明",
    )
    return done


# ---- 判準:額度用完只認「Codex 自己的錯誤項」 --------------------------------
# 真形狀(掃 ~/.codex/sessions 2026 年含字面的 rollout,375/375 筆都是這一個):
#   {"type":"event_msg","payload":{"type":"task_complete",
#     "error":{"message":"You've hit your usage limit. ... try again at Jul 31st, 2026 12:56 AM.",
#              "codex_error_info":"usage_limit_exceeded"}}}
# 同一批真檔裡「含同樣字面但不是 Codex 的錯誤」的有三種,都必須不算:
#   270 筆 event_msg/item_completed(模型自己的 AgentMessage)、3 筆 task_complete 但沒有 error
#   (字面在 last_agent_message)、23 筆 response_item 的 user/assistant 訊息(含 the maintainer 貼的終端機 log)。
#   ⇒ event_msg 一律只看 payload["error"],**不准**拿 payload 本身當後備。
# v0.70.0 #11 的派工單另外寫了 type=error / usage_limit_reached:真檔 0 筆,一併認,但它不是唯一入口
#   (只認那一型等於做一個永遠不觸發的死分支)。
QUOTA_INFO_MARK = "usage_limit"
MAX_QUOTA_LEFTOVER = 1 << 20  # 半行最多留 1MB,避免一行超長把記憶體吃掉


def _quota_from_error(err: Any) -> dict[str, Any] | None:
    """錯誤物件 → 額度用完才回 {"message","info"}。"""
    if isinstance(err, str):
        err = {"message": err}
    if not isinstance(err, dict):
        return None
    info = str(err.get("codex_error_info") or err.get("code") or "")
    msg = str(err.get("message") or "")
    if QUOTA_INFO_MARK in info.lower() or (msg and uf.quota_limit_text(msg)):
        return {"message": msg or info, "info": info}
    return None


def quota_error_from_line(line: str) -> dict[str, Any] | None:
    """一行 rollout jsonl → **Codex 自己的**額度錯誤才回傳;其它一律 None。

    ★ 這個函式就是角色過濾。使用者打的字、模型的敘述、工具輸出即使逐字含
    `usage limit` 也不算 —— 拿掉這一層,the maintainer 在輸入框打「額度用完」就會被換號。
    """
    text = str(line or "").strip()
    if not text.startswith("{"):
        return None
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    kind = str(doc.get("type") or "")
    payload = doc.get("payload")
    if kind == "event_msg":
        if not isinstance(payload, dict):
            return None
        return _quota_from_error(payload.get("error"))
    if kind == "error":
        if isinstance(payload, dict):
            return _quota_from_error(payload.get("error") or payload)
        return _quota_from_error(payload)
    return None


def quota_scan(text: str, *, leftover: str = "") -> tuple[str, str]:
    """(對話檔的新位元組, 上一輪留下的半行) → (命中的錯誤訊息 or "", 這一輪要留的半行)。

    新位元組是按檔案大小切的,錯誤那一行會被切成兩半 ⇒ 沒有換行結尾的尾巴要留到下一輪,
    否則兩半各自都不是合法 json,整條錯誤就靜靜漏掉。
    """
    buf = str(leftover or "") + str(text or "")
    if not buf:
        return "", ""
    if buf.endswith("\n"):
        lines, rest = buf.split("\n")[:-1], ""
    else:
        parts = buf.split("\n")
        rest = parts.pop()
        lines = parts
    for ln in lines:
        got = quota_error_from_line(ln)
        if got:
            return got["message"], rest[-MAX_QUOTA_LEFTOVER:]
    return "", rest[-MAX_QUOTA_LEFTOVER:]


def quota_chunk(text: str, *, leftover: str = "") -> str:
    """從新寫進對話檔的字裡,抽出 **Codex 自己** 的額度用完那一句。舊內容不要傳進來。

    回傳的是原訊息(不是固定字串 `usage_limit`):下游 `uf.on_codex_error` 還會再認一次字面,
    而且只有一個帳號時要從這句話裡讀出重置時刻。
    """
    hit, _rest = quota_scan(text, leftover=leftover)
    if hit and not uf.quota_limit_text(hit):
        # 靠錯誤碼認出來、但訊息本身沒有那些字面(例如哪天 Codex 換成別的語言)⇒
        # 下游 uf.on_codex_error 仍然只認字面,會判 not_quota 而**安靜地不換號**。
        # 補一個標記,讓「認出來了」與「下游收得到」不是兩件事。
        hit = "%s [%s]" % (hit, QUOTA_INFO_MARK)
    return hit


_RESET_CLOCK_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", re.I)


def quota_reset_clock(message: str) -> str:
    """從 Codex 的訊息裡讀出重置時刻,轉 24 小時制 HH:MM;讀不到回空字串(不猜)。

    真訊息長這樣:`... or try again at Jul 31st, 2026 12:56 AM.`
    時區照 Codex 印出來的樣子(它印的是機器本地時間),本函式不做時區換算。
    """
    raw = str(message or "")
    if "try again at" not in raw.lower():
        return ""
    tail = raw[raw.lower().find("try again at"):]
    m = _RESET_CLOCK_RE.search(tail)
    if not m:
        return ""
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), (m.group(3) or "").upper()
    if minute > 59:
        return ""
    if ampm == "AM":
        if hour > 12:
            return ""
        hour = 0 if hour == 12 else hour
    elif ampm == "PM":
        if hour > 12:
            return ""
        hour = 12 if hour == 12 else hour + 12
    if hour > 23:
        return ""
    return "%02d:%02d" % (hour, minute)


def quota_hold_line(message: str) -> str:
    """只有一個帳號時,席與右欄要看到的那一句。不知道時刻就不寫時刻。"""
    clock = quota_reset_clock(message)
    return ("額度用完,%s 重置" % clock) if clock else "額度用完"


def mark_seat_quota(window_id: str, *, line: str, reset_clock: str = "",
                    now_ms: int | None = None, home: Path | None = None) -> Path | None:
    """把「額度用完」標到席位檔。右欄怎麼畫是 A 包(render_lib)的事,這裡只負責狀態。"""
    path = seat_path(window_id, home)
    if path is None or not path.is_file():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(rec, dict):
        return None
    rec["codex_quota"] = {
        "state": "exhausted",
        "line": str(line or ""),
        "reset_clock": str(reset_clock or ""),
        "at_ms": int(now_ms) if now_ms is not None else int(time.time() * 1000),
    }
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def rotation_readiness(*, after_id: str | None = None,
                       list_accounts: Callable[[], list] | None = None,
                       pick_slot: Callable[[], Any] | None = None,
                       now: float | None = None) -> dict[str, Any]:
    """換號的前置:OAuth 帳號 ≥ 2 **且**真的挑得到下一格。

    只有一個帳號時換號沒有意義,而舊流程會先登出再發現沒得換 ⇒ 席位被登出、選單跳出來等人按。
    """
    if list_accounts is None:
        def list_accounts() -> list:
            return [rec for rec in ua.list_accounts(ua.BRAND_CODEX)
                    if str(rec.get("kind") or "") == ua.KIND_OAUTH]
    accounts = list(list_accounts() or [])
    n = len(accounts)
    if n < 2:
        return {"can_rotate": False, "reason": "single_account", "accounts": n}
    if pick_slot is None:
        now_f = time.time() if now is None else float(now)

        def pick_slot() -> Any:
            # ★ after_id 一定要傳:next_ready_slot 用它把「現在這個(剛用完的)帳號」排除掉。
            #   不傳的話,那個帳號還沒被記成用完 ⇒ 它自己會被算成「可用槽」⇒
            #   算出「可以換」而其實只有它健康,選單就會在不該跳的時候跳。
            return (uf.next_ready_slot(after_id, now_f)
                    or uf.pick_next_oauth_slot(after_id=after_id, now=now_f))
    try:
        slot = pick_slot()
    except Exception:
        slot = None
    if not slot:
        return {"can_rotate": False, "reason": "no_slot", "accounts": n}
    slot_id = str(slot.get("id") or "") if isinstance(slot, dict) else ""
    return {"can_rotate": True, "reason": "ok", "accounts": n, "slot_id": slot_id}


MAX_TRANSCRIPT_TAIL = 512 * 1024


def transcript_quota_seen(*, pid: int = 0, transcript: str = "",
                          codex_home: str = "", resume: str = "",
                          tail_bytes: int = MAX_TRANSCRIPT_TAIL,
                          lsof: Callable[[list[str]], str] | None = None) -> bool:
    """對話檔裡有沒有 **Codex 自己的** 額度錯誤。

    給 `provider-launch.sh` 的 pane 監看當第二道確認:pane 上的字看不出是誰打的
    (真檔裡就有 the maintainer 把含那句英文的終端機 log 貼進輸入框),所以 pane 命中之後
    要回到對話檔問一次「那是 Codex 的錯誤項嗎」。查不到一律回 False(寧可不換號)。
    """
    path = str(transcript or "")
    if not path and codex_home and resume:
        # Codex 已經結束就沒有 pid 可以問(離開路徑用這一支):用 codex home + 續接編號找對話檔
        found = find_rollout(codex_home, resume)
        path = str(found) if found else ""
    if not path and pid:
        try:
            path = rollout_from_process_tree(int(pid), lsof or _lsof_text) or ""
        except Exception:
            path = ""
    if not path:
        return False
    # ★ 只讀檔尾:本機真實的 rollout 最大 716.8 MB、484 份超過 5MB(2026-09-27 量的)。
    #   這支會被 launcher 的 pane 監看叫到,整份讀進來會吃掉記憶體與秒數。
    #   額度錯誤是「這一輪剛發生的事」,在檔尾;檔尾視窗外的舊錯誤刻意不算(見尺裡那兩格)。
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > tail_bytes:
                handle.seek(size - tail_bytes)
                blob = handle.read().decode("utf-8", "ignore")
                cut = blob.find("\n")          # 視窗第一行可能被切一半,丟掉
                raw = blob[cut + 1:] if cut >= 0 else ""
            else:
                raw = handle.read().decode("utf-8", "ignore")
    except OSError:
        return False
    for line in raw.splitlines():
        if quota_error_from_line(line):
            return True
    return False


def pre_rotate_guard(window_id: str, *, message: str, after_id: str | None = None,
                     env: dict[str, str] | None = None,
                     readiness: dict[str, Any] | None = None, pack_on: bool | None = None,
                     seat_on: bool | None = None,
                     mark: Callable[..., Any] | None = None,
                     home: Path | None = None) -> dict[str, Any] | None:
    """換號前的四道閘。回 None = 可以往下走;回 dict = 就停在這裡(而且繼續看守)。

    次序有意義:**帳號不足那一道要排在選單前面** —— 只有一個帳號時連選單都不該跳。
    """
    env = os.environ if env is None else env
    if pack_on is None:
        pack_on = pack_rotate_enabled()
    if not pack_on:
        return {"ok": False, "reason": "pack_off", "continue_watch": True,
                "did_logout": False, "line": "Codex 套組關著,不自動換號"}
    if readiness is None:
        readiness = rotation_readiness(after_id=after_id)
    if not readiness.get("can_rotate"):
        line = quota_hold_line(message)
        clock = quota_reset_clock(message)
        try:
            if mark is None:
                # 直接呼叫,不包一層叫 mark 的閉包:到達性掃描器會把那個閉包算成「生產頂」,
                # 而區域閉包從測試外面根本叫不到 ⇒ 入口格永遠算不出來(D17 紅過一次)。
                mark_seat_quota(window_id, line=line, reset_clock=clock, home=home)
            else:
                mark(window_id=window_id, line=line, reset_clock=clock,
                     reason=str(readiness.get("reason") or ""))
        except Exception:
            pass
        return {"ok": False, "reason": "quota_hold", "continue_watch": True, "did_logout": False,
                "accounts": readiness.get("accounts"), "hold_reason": readiness.get("reason"),
                "reset_clock": clock, "line": line}
    if str(env.get("AIB_CODEX_QUOTA_MENU") or "") == "1":
        return {"ok": False, "reason": "quota_menu", "continue_watch": True,
                "did_logout": False, "line": "額度用完改由這一席的選單處理"}
    if window_id:
        if seat_on is None:
            seat_on = seat_auto_rotate_enabled(window_id)
        if not seat_on:
            return {"ok": False, "reason": "seat_off", "continue_watch": True,
                    "did_logout": False, "line": "這一席的自動換號關著"}
    return None


def watch_rollout(
    *,
    parent_alive: Callable[[], bool],
    read_new: Callable[[], str],
    on_quota: Callable[[str], dict[str, Any]],
    sleep: Callable[[float], None],
    max_steps: int | None = None,
) -> dict[str, Any]:
    steps = 0
    pending = ""
    tail = ""
    while parent_alive():
        if not pending:
            pending, tail = quota_scan(read_new() or "", leftover=tail)
        if pending:
            result = on_quota(pending) or {}
            if result.get("continue_watch"):
                if result.get("reason") != "busy":
                    pending = ""
            else:
                return result
        steps += 1
        if max_steps is not None and steps >= max_steps:
            return {"ok": False, "reason": "watch_idle", "did_logout": False, "line": "還沒看到額度用完"}
        sleep(WATCH_SLEEP_S)
    return {"ok": False, "reason": "seat_gone", "did_logout": False, "line": "原席已結束"}


def resume_from_rollout(path: str | Path) -> str:
    """對話檔檔名最後那段就是續接編號。開席時沒帶編號也能用。"""
    name = Path(path).name
    if not name.endswith(".jsonl") or len(name) < 36 + len(".jsonl"):
        return ""
    tail = name[: -len(".jsonl")][-36:]
    if not uf.resume_launch_args(tail):
        return ""
    return tail.lower()


def find_rollout(codex_home: str | Path, resume: str) -> Path | None:
    root = Path(codex_home)
    text = str(resume or "").strip().lower()
    if not text or not root.is_dir():
        return None
    hits = [p for p in root.glob("sessions/**/rollout-*%s*.jsonl" % text) if p.is_file()]
    if not hits:
        return None
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0]


def child_pids(pid: int, runner: Callable[[list[str]], str] | None = None) -> list[int]:
    run = runner or _pgrep_text
    text = run(["pgrep", "-P", str(pid)]) or ""
    found = []
    for line in text.split():
        if line.isdigit():
            found.append(int(line))
    return found


def rollout_from_process_tree(
    pid: int,
    runner: Callable[[list[str]], str],
    children: Callable[[int], list[int]] | None = None,
) -> str:
    """對話檔常常開在裡層程式，不是外面那個。兩層都看。"""
    kids = children or (lambda one: child_pids(one))
    seen: list[int] = []
    stack = [int(pid)]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.append(cur)
        found = rollout_from_lsof(cur, runner)
        if found:
            return found
        stack.extend(kids(cur))
    return ""


def _pane_pid(tmux_bin: str, socket: str, session: str, window_id: str) -> int:
    if not tmux_bin or not socket or not session or not window_id:
        return 0
    import subprocess
    try:
        proc = subprocess.run(
            [tmux_bin, "-L", socket, "display-message", "-p", "-t", "%s:%s" % (session, window_id), "#{pane_pid}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    text = (proc.stdout or "").strip()
    return int(text) if text.isdigit() else 0


def _rearm_after_respawn(result: dict[str, Any], window_id: str, old_pid: int) -> None:
    """原席程序被換掉後，看守要改盯新程序。"""
    home = str((result.get("open") or {}).get("codex_home") or "")
    if not home:
        auth = str((result.get("seat") or {}).get("auth") or "")
        home = str(Path(auth).parent) if auth else ""
    pid = _pane_pid(
        os.environ.get("AIB_TMUX_BIN") or "tmux",
        os.environ.get("EZ2_AIHUB_SOCKET") or "",
        hub_session_name(),
        window_id,
    )
    resume = str(result.get("resume_uuid") or "")
    if not pid or pid == int(old_pid or 0) or not home or not resume:
        return
    arm_main(pid, resume, home, window_id=window_id)


def model_from_seat(window_id: str, home: Path | None = None) -> str:
    path = seat_path(window_id, home)
    if path is None or not path.is_file():
        return ""
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(rec, dict):
        return ""
    return str(rec.get("model_requested") or rec.get("model") or "").strip()


def session_uuid_from_seat(window_id: str, home: Path | None = None) -> str:
    path = seat_path(window_id, home)
    if path is None or not path.is_file():
        return ""
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(rec, dict):
        return ""
    text = str(rec.get("session_uuid") or "")
    if not uf.resume_launch_args(text):
        return ""
    return text.lower()


def rollout_from_lsof(pid: int, runner: Callable[[list[str]], str]) -> str:
    text = runner(["lsof", "-p", str(pid), "-Fn"]) or ""
    for line in text.splitlines():
        if "rollout-" in line and line.endswith(".jsonl"):
            path = line[1:] if line.startswith("n") else line
            if "rollout-" in path:
                return path.strip()
    return ""


def instance_home() -> Path | None:
    raw = os.environ.get("EZ2_AIB_INSTANCE_HOME") or ""
    if raw:
        return Path(raw)
    inst = os.environ.get("EZ2_AIB_INSTANCE") or ""
    if inst:
        return Path.home() / ".ez2-aib" / "instances" / inst
    return None


def seat_path(window_id: str, home: Path | None = None) -> Path | None:
    text = str(window_id or "").strip()
    if not text.startswith("@") or not text[1:].isdigit():
        return None
    root = home if home is not None else instance_home()
    if root is None:
        return None
    return Path(root) / "seats" / ("w%s.json" % text[1:])


def seat_auto_rotate_enabled(window_id: str, home: Path | None = None) -> bool:
    """沒寫就是開。只有這一席的檔案明寫 false 才關。"""
    path = seat_path(window_id, home)
    if path is None or not path.is_file():
        return True
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    if not isinstance(rec, dict) or "codex_auto_rotate" not in rec:
        return True
    return bool(rec.get("codex_auto_rotate"))


def set_seat_auto_rotate(window_id: str, enabled: bool, home: Path | None = None) -> Path:
    path = seat_path(window_id, home)
    if path is None or not path.is_file():
        raise FileNotFoundError(window_id or "(no window)")
    rec = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rec, dict):
        raise ValueError("seat file is not an object")
    rec["codex_auto_rotate"] = bool(enabled)
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _acquire_lock(log_dir: str | None):
    folder = str(log_dir or "").strip()
    if not folder:
        return None
    path = Path(folder)
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    fh = open(path / "codex-rotate.lock", "a", encoding="utf-8")
    try:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, ImportError):
        fh.close()
        return False
    return fh


def pack_rotate_enabled() -> bool:
    """帳號套組關掉時不看守、也不在額度用完時換號。讀不到開關就維持開。"""
    try:
        import codex_pack
        return bool(codex_pack.enabled())
    except Exception:
        return True


def arm_main(
    parent_pid: int,
    resume: str,
    codex_home: str,
    *,
    window_id: str = "",
    fork: Callable[[], int] | None = None,
) -> int:
    """整艙沒關、這一席也沒關，才分出看守。不擋住開席。"""
    if not pack_rotate_enabled():
        return 0
    if os.environ.get("AIB_CODEX_AUTO_ROTATE") == "0":
        return 0
    if not uf.auto_handoff_enabled():
        return 0
    if window_id and not seat_auto_rotate_enabled(window_id):
        return 0
    fork = fork or os.fork
    pid = fork()
    if pid != 0:
        return 0
    try:
        os.setsid()
    except OSError:
        pass
    # 只改 sys.stdout 不會關掉繼承來的 fd。開席那條管子會被看守握到逾時。
    try:
        devnull = os.open(os.devnull, os.O_RDWR)
        os.dup2(devnull, 0)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        if devnull > 2:
            os.close(devnull)
    except OSError:
        pass
    try:
        _watch_parent(parent_pid, resume, codex_home, window_id)
    finally:
        os._exit(0)


def _watch_parent(parent_pid: int, resume: str, codex_home: str, window_id: str = "") -> None:
    home = Path(codex_home)
    shared = str(home / "auth.json")
    log_dir = os.environ.get("EZ2_COCKPIT_STATE_DIR") or ""
    if not log_dir:
        inst = os.environ.get("EZ2_AIB_INSTANCE_HOME") or ""
        if inst:
            log_dir = str(Path(inst) / "cockpit")
    email = ""
    if os.path.isfile(shared):
        try:
            email = ua.oauth_account_from_auth(shared) or ""
        except Exception:
            email = ""
    after_id = account_id_for_email(email)
    offset = {"n": 0}
    resume_box = {"id": resume if uf.resume_launch_args(resume) else ""}
    rollout = {"path": find_rollout(home, resume_box["id"])}

    def parent_alive() -> bool:
        try:
            os.kill(int(parent_pid), 0)
            return True
        except OSError:
            return False

    def read_new() -> str:
        if rollout["path"] is None:
            if not resume_box["id"] and window_id:
                resume_box["id"] = session_uuid_from_seat(window_id)
            found = find_rollout(home, resume_box["id"])
            if found is None and parent_pid:
                guessed = rollout_from_process_tree(parent_pid, _lsof_text)
                if guessed:
                    found = Path(guessed)
            rollout["path"] = found
            if found is not None and found.is_file():
                offset["n"] = found.stat().st_size
                return ""
        path = rollout["path"]
        if path is None or not path.is_file():
            return ""
        data = path.read_bytes()
        if len(data) <= offset["n"]:
            return ""
        chunk = data[offset["n"]:].decode("utf-8", "ignore")
        offset["n"] = len(data)
        return chunk

    def on_quota(chunk: str) -> dict[str, Any]:
        # 四道閘(套組 / 帳號夠不夠 / 選單 / 這一席開關)都在 pre_rotate_guard 裡,
        # 次序有意義:只有一個帳號時連選單都不跳,只在席與右欄標「額度用完,HH:MM 重置」。
        held = pre_rotate_guard(window_id, message=chunk, after_id=after_id or None)
        if held is not None:
            return held
        lock = _acquire_lock(log_dir)
        if lock is False:
            return {
                "ok": False, "reason": "busy", "continue_watch": True,
                "did_logout": False, "line": "另一席正在換",
            }
        try:
            transcript = str(rollout["path"]) if rollout["path"] else None
            resume_uuid = resume_box["id"] if uf.resume_launch_args(resume_box["id"]) else ""
            if not resume_uuid and rollout["path"] is not None:
                resume_uuid = resume_from_rollout(rollout["path"])
            result = handle_quota_text(
                chunk,
                resume_uuid=resume_uuid,
                after_id=after_id or None,
                transcript=transcript,
                shared_auth=shared,
                log_dir=log_dir or None,
                tmux_bin=os.environ.get("AIB_TMUX_BIN") or "tmux",
                socket=os.environ.get("EZ2_AIHUB_SOCKET") or "",
                session=hub_session_name(),
                target_window=window_id,
                model=model_from_seat(window_id),
                env_source=str(Path.home() / ".codex"),
            )
            if result.get("ok") and window_id:
                _rearm_after_respawn(result, window_id, parent_pid)
            return result
        finally:
            if lock is not None:
                try:
                    lock.close()
                except OSError:
                    pass

    watch_rollout(parent_alive=parent_alive, read_new=read_new, on_quota=on_quota, sleep=time.sleep)


def _pgrep_text(argv: list[str]) -> str:
    return _lsof_text(argv)


def _lsof_text(argv: list[str]) -> str:
    import subprocess
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout or ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codex_rotate_live")
    sub = parser.add_subparsers(dest="cmd")
    arm = sub.add_parser("arm")
    arm.add_argument("--parent-pid", type=int, required=True)
    arm.add_argument("--resume", default="")
    arm.add_argument("--codex-home", required=True)
    arm.add_argument("--window", default="")
    seat = sub.add_parser("seat")
    seat.add_argument("window_id")
    seat.add_argument("state", choices=("on", "off"))
    seen = sub.add_parser("quota-seen", help="對話檔裡有沒有 Codex 自己的額度錯誤(給 launcher 確認用)")
    seen.add_argument("--pid", type=int, default=0)
    seen.add_argument("--transcript", default="")
    seen.add_argument("--codex-home", default="")
    seen.add_argument("--resume", default="")
    args = parser.parse_args(argv)
    if args.cmd == "arm":
        return arm_main(args.parent_pid, args.resume, args.codex_home, window_id=args.window)
    if args.cmd == "quota-seen":
        return 0 if transcript_quota_seen(pid=args.pid, transcript=args.transcript,
                                          codex_home=args.codex_home, resume=args.resume) else 1
    if args.cmd == "seat":
        try:
            path = set_seat_auto_rotate(args.window_id, args.state == "on")
        except (FileNotFoundError, ValueError) as exc:
            print("找不到這一席: %s" % exc, file=sys.stderr)
            return 2
        print("%s 自動換號 %s" % (args.window_id, "開" if args.state == "on" else "關"))
        print(path)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
