# SPDX-License-Identifier: Apache-2.0
"""Codex 讀帳號被拒時，留在原視窗列出已綁定的帳，點一格就重新登入。"""
from __future__ import annotations

import json
import os
import re
import select
import sys
import time
from pathlib import Path

# the maintainer 09-28「目前太敏感」:畫面上隨便一個 401(行號、別人貼的 log、在聊 HTTP 401)都會跳進選帳。
# 改成要像 Codex 印的錯誤:401 旁邊有 unauthorized,或寫成 status/HTTP/code 401。1401 這種數字照舊不算。
_READ_FAILURE_RE = re.compile(
    r"unauthorized[^0-9\n]{0,40}(?<![0-9])401(?![0-9])"
    r"|(?<![0-9])401(?![0-9])[^0-9\n]{0,40}unauthorized"
    r"|(?:status|http|code)[ :=]*(?<![0-9])401(?![0-9])", re.I)

BACK = "back"            # 選了「回到本對話」
BACK_RC = 3              # 給 provider-launch:接回原對話,不換帳
WAIT_ENV = "AIB_CODEX_RECOVER_WAIT_S"
DEFAULT_WAIT_S = 30      # the maintainer 09-28:預設 30 秒後自動接續下一個
BACK_LABEL = "0) 回到本對話【不輪替登入其他帳號】"


def is_account_read_failure(text: str) -> bool:
    """像 Codex 自己印的 401 才算(見 _READ_FAILURE_RE)。"""
    return _READ_FAILURE_RE.search(str(text or "")) is not None


def wait_seconds() -> int:
    try:
        return max(0, int(os.environ.get(WAIT_ENV) or DEFAULT_WAIT_S))
    except ValueError:
        return DEFAULT_WAIT_S


def bound_accounts() -> list[dict[str, str]]:
    import usage_accounts as ua
    import usage_failover as uf
    rows = []
    counts = cycle_counts()
    for rec in ua.list_accounts(ua.BRAND_CODEX):
        if str(rec.get("kind") or "") != ua.KIND_OAUTH:
            continue
        email = str(rec.get("oauth_account") or "").strip()
        if not email:
            continue
        rows.append({
            "id": str(rec.get("id") or ""),
            "email": email,
            "mark": uf.login_glyph(uf.login_mark(rec)),
            "auth_path": str(rec.get("auth_path") or ""),
            "quota": quota_cells(rec),
            "quota_map": quota_map(rec),
            "updated": updated_label(rec),
            "runs": counts.get(email, 0),
            "quota_ok": "1" if quota_ok(rec) else "0",
            "reset_epoch": "" if earliest_reset(rec) is None else str(earliest_reset(rec)),
        })
    return rows


def mark_spoken_cap(email: str) -> None:
    """Codex 親口說額度到頂。存檔百分比還是 0 也先標掉，不要再自動選它。"""
    import usage_accounts as ua
    want = str(email or "").strip().lower()
    if not want:
        return
    data = ua.load()
    changed = False
    for rec in data.get("accounts") or []:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("oauth_account") or "").strip().lower() != want:
            continue
        rec["spoken_cap"] = True
        changed = True
    if changed:
        ua.save(data)


def earliest_reset(rec: dict) -> float | None:
    usage = rec.get("usage") if isinstance(rec.get("usage"), dict) else {}
    rows = usage.get("rows") if isinstance(usage.get("rows"), list) else []
    found: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = row.get("resets_at")
        try:
            found.append(float(raw))
        except (TypeError, ValueError):
            continue
    return min(found) if found else None


def usage_at_cap(rec: dict) -> bool:
    """5 小時、每週、每月任一格到 100% 就算沒有額度。沒讀到的不當成到頂。"""
    usage = rec.get("usage") if isinstance(rec.get("usage"), dict) else {}
    rows = usage.get("rows") if isinstance(usage.get("rows"), list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pct = row.get("used_pct")
        if isinstance(pct, bool) or not isinstance(pct, int) or pct < 100:
            continue
        if _win_label(row.get("window")) in _QUOTA_COLS:
            return True
    return False


def quota_ok(rec: dict) -> bool:
    """打勾只代表登入證明還在。用完、被標成沒額度、或用量到頂，都不算有額度。"""
    import usage_failover as uf
    if rec.get("spoken_cap"):
        reset = earliest_reset(rec)
        if reset is None or time.time() < reset:
            return False
    if usage_at_cap(rec):
        return False
    if uf._is_exhausted(rec):
        return False
    if uf.login_mark(rec) == "no_quota":
        return False
    return True


def cursor_path() -> Path:
    override = os.environ.get("AIB_CODEX_RECOVER_CURSOR")
    if override:
        return Path(override)
    return Path.home() / ".ez2-aib" / "codex-recover-cursor"


def read_cursor() -> str:
    path = cursor_path()
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_cursor(acct_id: str) -> None:
    path = cursor_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(acct_id) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def cursor_reset_passed(rows: list[dict[str, str]], last_id: str, now: float | None = None) -> bool:
    """游標上面（含這一格）有人的重置時間已經過了，就該從最上面再看。同一輪不繞回。"""
    now_f = time.time() if now is None else float(now)
    idx = None
    for i, row in enumerate(rows):
        if str(row.get("id") or "") == last_id:
            idx = i
            break
    if idx is None:
        return False
    for row in rows[: idx + 1]:
        raw = row.get("reset_epoch")
        try:
            reset = float(raw)
        except (TypeError, ValueError):
            continue
        if reset <= now_f:
            return True
    return False


def next_auto(rows: list[dict[str, str]], current_email: str = "") -> dict[str, str] | None:
    """從上次試過的下一列往下，找還有額度的。不繞回上面。剛失敗的信箱跳過。"""
    start = 0
    last = read_cursor()
    if last and cursor_reset_passed(rows, last):
        write_cursor("")
        last = ""
    if last:
        for i, row in enumerate(rows):
            if str(row.get("id") or "") == last:
                start = i + 1
                break
    current = (current_email or "").strip().lower()
    if current in ("", "（沒有）"):
        current = ""
    for row in rows[start:]:
        email = str(row.get("email") or "").strip().lower()
        if current and email == current:
            continue
        if str(row.get("quota_ok") or "") != "1":
            continue
        return row
    return None


def _win_label(win: str) -> str:
    w = str(win or "")
    if w in ("5h", "5小時"):
        return "5小時"
    if w in ("7d", "週", "每1週", "1週", "weekly"):
        return "每1週"
    if w in ("30d", "30天"):
        return "30天"
    return w or "?"


_QUOTA_COLS = ("5小時", "每1週", "30天")


def quota_map(rec: dict) -> dict[str, str]:
    """視窗名稱 → 百分比。沒有的欄不放。"""
    usage = rec.get("usage") if isinstance(rec.get("usage"), dict) else {}
    rows = usage.get("rows") if isinstance(usage.get("rows"), list) else []
    found: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pct = row.get("used_pct")
        if isinstance(pct, bool) or not isinstance(pct, int):
            continue
        found[_win_label(row.get("window"))] = "%d%%" % pct
    return found


def quota_cells(rec: dict) -> str:
    """最後一次問到的額度格。沒有讀數就寫尚無用量。"""
    found = quota_map(rec)
    cells = ["%s %s" % (name, found[name]) for name in _QUOTA_COLS if name in found]
    extra = ["%s %s" % (name, pct) for name, pct in found.items() if name not in _QUOTA_COLS]
    cells.extend(extra)
    return "  ".join(cells) if cells else "尚無用量"


def cycle_counts(path: str | None = None) -> dict[str, int]:
    """每個信箱完成登入循環的次數。沒走完的不算。"""
    import codex_google_login as gl
    log = Path(path) if path else gl.LOG
    counts: dict[str, int] = {}
    if not log.is_file():
        return counts
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("result") or "") != "ok":
            continue
        email = str(row.get("email") or "")
        if email:
            counts[email] = counts.get(email, 0) + 1
    return counts


def align_account_lines(rows: list[dict[str, str]]) -> list[str]:
    """編號、續跑次數、信箱、三種額度、更新時間各自對齊。缺的額度用 —。"""
    if not rows:
        return []
    email_w = max(len(str(row.get("email") or "")) for row in rows)
    run_w = max(len("續跑:%d次" % int(row.get("runs") or 0)) for row in rows)
    lines = []
    for i, row in enumerate(rows, 1):
        found = row.get("quota_map") if isinstance(row.get("quota_map"), dict) else {}
        runs = "續跑:%d次" % int(row.get("runs") or 0)
        parts = [
            "%d" % i,
            runs.ljust(run_w),
            str(row.get("mark") or ""),
            str(row.get("email") or "").ljust(email_w),
        ]
        for name in _QUOTA_COLS:
            pct = str(found.get(name) or "—")
            parts.append("%s %s" % (name, pct.rjust(4)))
        parts.append(str(row.get("updated") or "更新:—"))
        lines.append("  ".join(parts))
    return lines


def updated_label(rec: dict) -> str:
    import usage_accounts as ua
    usage = rec.get("usage") if isinstance(rec.get("usage"), dict) else {}
    ts = usage.get("probed_at") or rec.get("last_probe")
    return "更新:" + ua.format_last_bound_label(ts)


def recent_dialogue(codex_home: str, limit: int = 6) -> list[str]:
    """最近一份對話裡，使用者與 Codex 的短句。沒有就空。"""
    root = Path(codex_home or "") / "sessions"
    if not root.is_dir():
        return []
    files = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return []
    lines: list[str] = []
    for raw in files[0].read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        kind = str(payload.get("type") or obj.get("type") or "")
        role = str(payload.get("role") or "")
        text = payload.get("message") or payload.get("text") or payload.get("content") or ""
        if isinstance(text, dict):
            text = text.get("content") or text.get("text") or ""
        if isinstance(text, list):
            bits = []
            for part in text:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    bits.append(part["text"])
                elif isinstance(part, str):
                    bits.append(part)
            text = " ".join(bits)
        if not isinstance(text, str):
            continue
        text = " ".join(text.split())
        if not text or text.startswith("# AGENTS") or "請只回覆 AIB-READY" in text:
            continue
        who = ""
        if kind in ("user_message", "user") or role == "user":
            who = "你"
        elif kind in ("agent_message", "assistant", "agent") or role == "assistant":
            who = "Codex"
        elif kind == "message" and role == "user":
            who = "你"
        if who:
            lines.append("%s：%s" % (who, text[:80]))
    return lines[-limit:]


def current_default_email() -> str:
    """現在全域預設登入的信箱。沒有就寫沒有。"""
    import usage_accounts as ua
    email = ua.oauth_account_from_auth(ua._codex_auth_path())
    if email:
        return email
    return "（沒有）"


def screen(rows: list[dict[str, str]], dialogue: list[str], default_email: str = "", reason: str = "") -> tuple[str, int]:
    """畫面文字，以及第一個帳號列的 1-based 行號。"""
    why = reason or os.environ.get("AIB_CODEX_RECOVER_REASON") or ""
    if why == "quota":
        lead = "這個帳號的用量到頂了。剛才的對話還在，席位沒有關掉。"
    elif why == "refresh":
        lead = "這個帳的登入證明換不了。剛才的對話還在，席位沒有關掉。"
    else:
        lead = "這個登入讀不到帳號（401）。剛才的對話還在，席位沒有關掉。"
    out = [
        BACK_LABEL,
        "",
        lead,
        "輸入下方數字後按 Enter，或點那一列，就對那一格重新登入。輸入 0 或點最上面那列、或按 Esc 回到本對話。按 q 離開。",
        "",
    ]
    if dialogue:
        out.append("最近對話：")
        out.extend(dialogue)
        out.append("")
    first = len(out) + 1
    if not rows:
        out.append("（還沒有綁定過的帳）")
    out.extend(align_account_lines(rows))
    out.append("")
    out.append("目前預設登入的是：%s" % (default_email or "（沒有）"))
    out.append("請在選擇上方數字、並輸入Enter：")
    return "\n".join(out) + "\n", first


def is_back_click(click_row: int) -> bool:
    """最上面那一列(第 1 行)是「回到本對話」。"""
    return int(click_row) == 1


def account_at(rows: list[dict[str, str]], first_line: int, click_row: int) -> dict[str, str] | None:
    index = int(click_row) - int(first_line)
    if index < 0 or index >= len(rows):
        return None
    return rows[index]


_CLICK = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([mM])")


def clicks_in(buf: str) -> list[int]:
    """滑鼠左鍵按下的行號（1-based）。"""
    found = []
    for match in _CLICK.finditer(buf):
        button = int(match.group(1))
        row = int(match.group(3))
        press = match.group(4) == "M"
        if press and button == 0:
            found.append(row)
    return found


def relogin(acct_id: str) -> str:
    """對這一格重新登入。成功回傳它的目錄。桌面那份登入用完會放回去。"""
    import usage_accounts as ua
    desktop = Path(ua._codex_auth_path())
    backup = desktop.read_bytes() if desktop.is_file() else None
    try:
        return _relogin_into_slot(acct_id, ua)
    finally:
        if backup is not None:
            desktop.parent.mkdir(parents=True, exist_ok=True)
            desktop.write_bytes(backup)
            try:
                os.chmod(desktop, 0o600)
            except OSError:
                pass


def _relogin_into_slot(acct_id: str, ua) -> str:
    started = ua.start_codex_device_login()
    email = ""
    for rec in ua.list_accounts(ua.BRAND_CODEX):
        if str(rec.get("id") or "") == str(acct_id):
            email = str(rec.get("oauth_account") or "")
            break
    if email:
        import threading
        import codex_google_login
        threading.Thread(target=codex_google_login.drive, args=(email,), daemon=True).start()
    print("瀏覽器會開登入頁。登完這支會自己繼續，不必按完成。", flush=True)
    ua.wait_codex_device_login(
        int(started.get("pid") or 0),
        str(started.get("prev_fingerprint") or ""),
        300.0,
        str(started.get("log") or ""),
    )
    ua.rebind_after_device_login(acct_id, str(started.get("prev_fingerprint") or ""))
    for rec in ua.list_accounts(ua.BRAND_CODEX):
        if str(rec.get("id") or "") == str(acct_id):
            path = str(rec.get("auth_path") or "")
            if path:
                return str(Path(path).resolve().parent)
    raise RuntimeError("重登後找不到這一格")


def format_bound_list() -> str:
    """給 aib list-codex。其他 AI 以後用同一欄位，各自換資料來源。"""
    rows = bound_accounts()
    if not rows:
        return "還沒有綁定過的 Codex 帳。\n"
    return "\n".join(align_account_lines(rows)) + "\n"


def read_key(timeout: float | None) -> str | None:
    """空字串代表時間到。None 代表輸入結束。"""
    if timeout is not None:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return ""
    chunk = sys.stdin.read(1)
    if chunk == "":
        return None
    return chunk


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--list" in argv:
        sys.stdout.write(format_bound_list())
        return 0
    result = ""
    if "--result" in argv:
        result = argv[argv.index("--result") + 1]
    home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    rows = bound_accounts()
    dialogue = recent_dialogue(home)
    default_email = current_default_email()
    if (os.environ.get("AIB_CODEX_RECOVER_REASON") or "") == "quota":
        mark_spoken_cap(default_email)
        rows = bound_accounts()
    text, first = screen(rows, dialogue, default_email, os.environ.get("AIB_CODEX_RECOVER_REASON") or "")
    sys.stdout.write("\033[H\033[J")
    sys.stdout.write(text)
    sys.stdout.write("\033[?1000h\033[?1006h")
    auto = next_auto(rows, default_email)
    wait_s = wait_seconds()

    def countdown_line(left: int) -> str:
        return ("\r\033[K%d 秒後自動接續第 %d 列 %s。打數字改選,0 或 Esc 回到本對話,q 離開。"
                % (left, rows.index(auto) + 1, auto["email"]))
    if auto is None:
        sys.stdout.write("下面沒有還有額度的帳。請自己選,0 或 Esc 回到本對話,或按 q 離開。\n")
    else:
        sys.stdout.write(countdown_line(wait_s))
    sys.stdout.flush()
    buf = ""
    typed = ""
    picked = None
    deadline = time.monotonic() + wait_s if auto is not None else None
    while True:
        if picked is None:
            timeout = None
            if deadline is not None and not typed and not buf:
                timeout = max(0.0, min(1.0, deadline - time.monotonic()))
            chunk = read_key(timeout)
            if chunk == "" and deadline is None:
                continue                                         # 沒在倒數(人動過或沒有可接的帳):不自己換帳
            if chunk == "":
                left = int(round(deadline - time.monotonic()))
                if left > 0:
                    sys.stdout.write(countdown_line(left))      # 每秒更新倒數
                    sys.stdout.flush()
                    continue
                picked = auto
                deadline = None
                sys.stdout.write("\n")
            elif chunk is None:
                return 2
            else:
                if chunk == "\x1b" and not buf:
                    nxt = read_key(0.05)                         # 單按 Esc = 回到本對話;滑鼠序列會緊接著來
                    if nxt in ("", "\n", "\r"):            # 終端機一般模式下 Esc 要等 Enter 才送出
                        sys.stdout.write("\n回到本對話,不換帳。\n")
                        sys.stdout.flush()
                        return BACK_RC
                    chunk += nxt or ""
                if deadline is not None:
                    deadline = None                              # 動了就停止倒數,由人決定
                    sys.stdout.write("\r\033[K")
                    sys.stdout.flush()
                buf += chunk
                if chunk in ("\n", "\r"):
                    if typed in ("q", "Q"):
                        return 2
                    if typed == "0":
                        sys.stdout.write("\n回到本對話,不換帳。\n")
                        sys.stdout.flush()
                        return BACK_RC
                    if not typed.isdigit():
                        typed = ""
                        deadline = None
                        sys.stdout.write("\n請輸入數字後按 Enter。\n請在選擇上方數字、並輸入Enter：")
                        sys.stdout.flush()
                        continue
                    picked = account_at(rows, first, first + int(typed) - 1)
                    typed = ""
                    deadline = None
                    if picked is None:
                        sys.stdout.write("\n沒有這個數字。\n請在選擇上方數字、並輸入Enter：")
                        sys.stdout.flush()
                        continue
                elif chunk in ("q", "Q") and not typed:
                    return 2
                elif chunk.isdigit():
                    typed += chunk
                    deadline = None
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                    continue
                else:
                    found = clicks_in(buf)
                    if not found:
                        continue
                    if is_back_click(found[-1]):
                        sys.stdout.write("\n回到本對話,不換帳。\n")
                        sys.stdout.flush()
                        return BACK_RC
                    picked = account_at(rows, first, found[-1])
                    buf = ""
                    deadline = None
                    if picked is None:
                        continue
        write_cursor(str(picked["id"]))
        try:
            slot = relogin(picked["id"])
        except Exception as exc:
            sys.stdout.write("\n這格沒登成：%s\n" % exc)
            sys.stdout.flush()
            picked = None
            auto = next_auto(rows, "")
            if auto is None:
                deadline = None
                sys.stdout.write("下面沒有還有額度的帳。請自己選，或按 q 離開。\n請在選擇上方數字、並輸入Enter：")
                sys.stdout.flush()
                continue
            auto_n = rows.index(auto) + 1
            sys.stdout.write("改綁第 %d 列 %s。%d 秒後開始(0 或 Esc 回到本對話)。\n" % (auto_n, auto["email"], wait_s))
            sys.stdout.flush()
            deadline = time.monotonic() + wait_s
            continue
        if result:
            Path(result).write_text(slot + "\n", encoding="utf-8")
            return 0
        return launch_after_login(slot)


def launch_after_login(slot: str, environ=None, run=None) -> int:
    """登入完成後開這一格。10-28(@aeb40 工作單 B):以前直接 exec 最新預設型號，被拒就停住;
    現在走 codex_model_fallback:自動挑的型號被拒就照退版表退一版重試(上限、紀錄、席位檔跟著寫)。
    手指定型號的席(AIB_CODEX_MODEL_EXPLICIT=1)照席位檔那一版開、不退版。"""
    import subprocess
    import codex_daemon_flag
    import codex_luna_model
    import codex_model_fallback as fb
    environ = dict(os.environ if environ is None else environ)
    run = subprocess.run if run is None else run
    window = fb.own_window(environ, run)
    seat_model, seat_source = "", ""
    path = fb.seat_file(environ, window)
    if path is not None:
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            seat_model = str(rec.get("model_requested") or "").strip()
            seat_source = str(rec.get("requested_source") or "")
        except (OSError, ValueError, AttributeError):
            seat_model, seat_source = "", ""
    # 手指定:環境帶 AIB_CODEX_MODEL_EXPLICIT,或席位檔記 requested_source=flag(原地復活後環境可能沒帶,#400 審查 Medium)。
    explicit = bool(seat_model) and (environ.get("AIB_CODEX_MODEL_EXPLICIT") == "1" or seat_source == "flag")
    if explicit:
        model = seat_model
        sys.stdout.write("\n登入完成，照這一席指定的型號 %s 開這一格。\n" % model)
    else:
        model = codex_luna_model.latest_default_slug() or "gpt-6.1-terra"
        sys.stdout.write("\n登入完成，正在用最新的預設型號開這一格。\n")
    sys.stdout.flush()
    bin_path = environ.get("CODEX_BIN") or "codex"
    env = {**environ, "CODEX_HOME": slot}
    flags = codex_daemon_flag.flag_args(bin_path)

    def argv_for(m: str) -> list[str]:
        return [bin_path, "--dangerously-bypass-approvals-and-sandbox", *flags, "--model", m]
    rc, used, _steps = fb.launch_with_fallback(
        argv_for, model, env, auto=not explicit, run=run, window=window,
        errfile=environ.get("AIB_CODEX_LUNA_ERRFILE") or "")
    if not explicit and seat_model and used != seat_model and not _steps:
        # 自動挑的席、沒退版但跟席位檔不同(席位檔是舊預設):照實際開的那一版寫回，復活時才對得上。
        #   手指定的席不寫回(使用者明講的型號不偷換)。
        fb.record(seat_model, used, window=window, reason="recover_login", environ=env)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
