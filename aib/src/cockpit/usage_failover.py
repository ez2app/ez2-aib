# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""Codex OAuth slot activate + ordered next-pick (quota failover A).

Activate only swaps ~/.codex/auth.json. Caller must open a *new* Codex seat.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import usage_accounts as ua

AUTH_BAK_KEEP = 5
EIGHT_H = 8 * 3600
FIVE_H = 5 * 3600
LUNA_MODEL = "gpt-5.6-luna"
VERIFY_NAME = "login-verify.json"
ROTATE_LOG_NAME = "codex-rotate.jsonl"


def _oauth_accounts_in_order(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = ua.load() if data is None else data
    out: list[dict[str, Any]] = []
    for rec in data.get("accounts") or []:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("brand") or "") != ua.BRAND_CODEX:
            continue
        if str(rec.get("kind") or "") != ua.KIND_OAUTH:
            continue
        out.append(rec)
    return out


def _slot_email(rec: dict[str, Any]) -> str:
    em = str(rec.get("oauth_account") or "").strip().lower()
    if em:
        return em
    ap = str(rec.get("auth_path") or "")
    if ap:
        return (ua.oauth_account_from_auth(ap) or "").strip().lower()
    return ""


def _is_exhausted(rec: dict[str, Any], now: float | None = None) -> bool:
    if rec.get("quota_hold"):
        return True
    until = rec.get("exhausted_until")
    if until is None or until == "":
        return False
    try:
        until_f = float(until)
    except (TypeError, ValueError):
        return False
    now_f = time.time() if now is None else float(now)
    return now_f < until_f


def _slot_dir(rec: dict[str, Any]) -> Path | None:
    ap = str(rec.get("auth_path") or "").strip()
    if not ap:
        return None
    return Path(ap).parent


def _verify_path(rec: dict[str, Any]) -> Path | None:
    folder = _slot_dir(rec)
    if folder is None:
        return None
    return folder / VERIFY_NAME


def read_login_verify(rec: dict[str, Any]) -> dict[str, Any]:
    path = _verify_path(rec)
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_login_verify(rec: dict[str, Any], payload: dict[str, Any]) -> bool:
    """只寫這一格自己的目錄。"""
    path = _verify_path(rec)
    if path is None:
        return False
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return True


def id_token_exp(rec: dict[str, Any]) -> float | None:
    """這一格身分證明的到期時間。讀不到就當不知道，不拿來改勾。"""
    path = str(rec.get("auth_path") or "").strip()
    if not path or not os.path.isfile(path):
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    raw = str(tokens.get("id_token") or "")
    parts = raw.split(".")
    if len(parts) < 2:
        return None
    seg = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(seg))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return float(payload.get("exp"))
    except (TypeError, ValueError):
        return None


def _login_mark_verify(rec: dict[str, Any], now: float | None = None) -> str:
    """ok=✅  stale=❗  no_quota=❌  relogin=🔄。只看這一格自己的驗證。"""
    now_f = time.time() if now is None else float(now)
    info = read_login_verify(rec)
    result = str(info.get("result") or "")
    if result == "no_quota":
        reset_at = info.get("reset_at")
        try:
            reset_f = float(reset_at)
        except (TypeError, ValueError):
            reset_f = None
        # 沒寫重置時間，或時間還沒到：維持沒額度。時間到了才改成可重登。
        if reset_f is None or now_f < reset_f:
            return "no_quota"
        return "relogin"
    if result == "rejected":
        return "relogin"
    if result == "ok":
        try:
            ok_at = float(info.get("ok_at"))
        except (TypeError, ValueError):
            return "stale"
        if now_f - ok_at <= EIGHT_H:
            return "ok"
        attempted = info.get("attempted_at")
        try:
            attempted_f = float(attempted) if attempted is not None else None
        except (TypeError, ValueError):
            attempted_f = None
        if attempted_f is None or attempted_f <= ok_at:
            return "stale"
        return "relogin"
    return "stale"


def login_mark(rec: dict[str, Any], now: float | None = None) -> str:
    """綠勾對齊 Codex 還能讀帳號的時間。沒有身分證明的舊資料仍用八小時驗證。"""
    now_f = time.time() if now is None else float(now)
    mark = _login_mark_verify(rec, now_f)
    if mark == "no_quota":
        return mark
    exp = id_token_exp(rec)
    if exp is None:
        return mark
    if now_f >= exp:
        return "relogin"
    return "ok"


def login_glyph(mark: str) -> str:
    """帳號列最前面那個符號。對不上就當還沒驗證，不用用量數字冒充。"""
    return {
        "ok": "✅",
        "stale": "❗",
        "no_quota": "❌",
        "relogin": "🔄",
    }.get(str(mark or ""), "❗")


def _quota_blocked(rec: dict[str, Any], now: float) -> bool:
    return login_mark(rec, now) == "no_quota"


def mark_exhausted(
    acct_id: str,
    until_epoch: float | None = None,
    reason: str = "",
    hold: bool = False,
) -> dict[str, Any]:
    data = ua.load()
    for rec in data.get("accounts") or []:
        if not (isinstance(rec, dict) and str(rec.get("id") or "") == str(acct_id)):
            continue
        if until_epoch is None and not hold:
            rec.pop("exhausted_until", None)
            rec.pop("last_limit_error", None)
            rec.pop("quota_hold", None)
        elif until_epoch is None and hold:
            rec["quota_hold"] = True
            if reason:
                rec["last_limit_error"] = str(reason)[:200]
        else:
            rec["exhausted_until"] = float(until_epoch)
            rec.pop("quota_hold", None)
            if reason:
                rec["last_limit_error"] = str(reason)[:200]
        ua.save(data)
        return {
            "ok": True,
            "id": acct_id,
            "exhausted_until": rec.get("exhausted_until"),
            "quota_hold": bool(rec.get("quota_hold")),
        }
    return {"ok": False, "reason": "not_found", "id": acct_id}


def next_ready_slot(after_id: str | None, now: float | None = None) -> dict[str, Any] | None:
    """從某一格後面找八小時內已登入成功的帳。需重登的先跳過。"""
    now_f = time.time() if now is None else float(now)
    slots = _oauth_accounts_in_order()
    if not slots:
        return None
    start = 0
    if after_id:
        for i, rec in enumerate(slots):
            if str(rec.get("id") or "") == str(after_id):
                start = i + 1
                break
    n = len(slots)
    for off in range(n):
        rec = slots[(start + off) % n]
        if after_id and str(rec.get("id") or "") == str(after_id):
            continue
        if _is_exhausted(rec, now_f) or _quota_blocked(rec, now_f):
            continue
        ap = str(rec.get("auth_path") or "")
        if not ap or not os.path.isfile(ap):
            continue
        if login_mark(rec, now_f) != "ok":
            continue
        if not _slot_email(rec):
            continue
        return dict(rec)
    return None


def pick_next_oauth_slot(
    *,
    after_id: str | None = None,
    now: float | None = None,
    skip_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    data = ua.load()
    slots = _oauth_accounts_in_order(data)
    if not slots:
        return None
    skip_ids = set(skip_ids or ())
    now_f = time.time() if now is None else float(now)

    start = 0
    aid = str(after_id or "").strip()
    if not aid:
        aid = str(data.get("active_codex_oauth_id") or "").strip()
    current_email = ""
    found_current = False
    if aid:
        for i, rec in enumerate(slots):
            if str(rec.get("id") or "") == aid:
                start = i + 1
                current_email = _slot_email(rec)
                found_current = True
                break

    # 現在這格、以及跟它同一個信箱的格子，都不能當下一格。
    seen_emails: set[str] = set()
    if current_email:
        seen_emails.add(current_email)
    n = len(slots)
    for off in range(n):
        rec = slots[(start + off) % n]
        rid = str(rec.get("id") or "")
        if found_current and rid == aid:
            continue
        if not rid or rid in skip_ids:
            continue
        if rec.get("disabled"):
            continue
        ap = str(rec.get("auth_path") or "")
        if not ap or not os.path.isfile(ap):
            continue
        if _is_exhausted(rec, now_f) or _quota_blocked(rec, now_f):
            continue
        em = _slot_email(rec)
        if em and em in seen_emails:
            continue
        if em:
            seen_emails.add(em)
        return dict(rec)
    return None


def activate_codex_oauth_slot(acct_id: str) -> dict[str, Any]:
    acct_id = str(acct_id or "").strip()
    if not acct_id:
        return {"ok": False, "reason": "missing_id"}
    data = ua.load()
    rec = None
    for a in data.get("accounts") or []:
        if isinstance(a, dict) and str(a.get("id") or "") == acct_id:
            rec = a
            break
    if rec is None:
        return {"ok": False, "reason": "not_found", "id": acct_id}
    if str(rec.get("kind") or "") != ua.KIND_OAUTH:
        return {"ok": False, "reason": "not_oauth", "id": acct_id}
    src = str(rec.get("auth_path") or "")
    if not src or not os.path.isfile(src):
        return {"ok": False, "reason": "missing_slot_auth", "id": acct_id, "auth_path": src}
    try:
        raw = Path(src).read_bytes()
    except OSError as exc:
        return {"ok": False, "reason": "read_failed", "id": acct_id, "detail": str(exc)[:120]}
    if not raw.strip():
        return {"ok": False, "reason": "empty_slot_auth", "id": acct_id}

    dest = ua._codex_auth_path()
    dest_dir = os.path.dirname(dest) or "."
    os.makedirs(dest_dir, mode=0o700, exist_ok=True)
    backup_path = ""
    if os.path.isfile(dest):
        ts = time.strftime("%Y%m%d-%H%M%S")
        backup_path = "%s.aib-bak-%s" % (dest, ts)
        try:
            Path(backup_path).write_bytes(Path(dest).read_bytes())
            os.chmod(backup_path, 0o600)
        except OSError as exc:
            return {"ok": False, "reason": "backup_failed", "detail": str(exc)[:120]}
        try:
            baks = sorted(
                [p for p in Path(dest_dir).glob(Path(dest).name + ".aib-bak-*") if p.is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for old in baks[AUTH_BAK_KEEP:]:
                try:
                    old.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    tmp = dest + ".aib-activate-tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(raw)
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
    except OSError as exc:
        try:
            if os.path.isfile(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        return {"ok": False, "reason": "write_failed", "detail": str(exc)[:120]}

    email = _slot_email(rec) or (ua.oauth_account_from_auth(dest) or "")
    if email and not rec.get("oauth_account"):
        rec["oauth_account"] = email
    fp = ua._auth_fingerprint(dest)
    if fp:
        rec["fingerprint"] = fp
    rec["last_activated_at"] = int(time.time())
    # 只清掉「換上去的這一格」自己的用盡。別格的記號留著。
    rec.pop("exhausted_until", None)
    rec.pop("quota_hold", None)
    data["active_codex_oauth_id"] = acct_id
    failover = data.get("failover") if isinstance(data.get("failover"), dict) else {}
    failover = dict(failover)
    failover.setdefault("auto_enabled", False)
    failover["last_event"] = {
        "at": int(time.time()),
        "kind": "activate",
        "id": acct_id,
        "email": email,
    }
    data["failover"] = failover
    ua.save(data)
    return {
        "ok": True,
        "id": acct_id,
        "email": email,
        "fingerprint": fp,
        "backup_path": backup_path,
        "dest": dest,
        "hint": "已切帳；請開*新* Codex 席（勿沿用舊 session）",
    }



# usage-codex-subtab-0922: PLAN failover B — call usage_accounts.refuse_auto_failover_if_locked(doc) before automatic switch; manual activate from Codex sub-tab stays allowed when unlocked.
def activate_next_codex_oauth(*, after_id: str | None = None) -> dict[str, Any]:
    # usage-codex-subtab-0922 gate
    try:
        from usage_accounts import refuse_auto_failover_if_locked
        refuse_auto_failover_if_locked(reason='def activate_next_codex_oauth(*, after_i')
    except ImportError:
        pass
    data = ua.load()
    if after_id is None:
        after_id = str(data.get("active_codex_oauth_id") or "") or None
    picked = pick_next_oauth_slot(after_id=after_id)
    if picked is None:
        return {
            "ok": False,
            "reason": "none_available",
            "after_id": after_id,
            "hint": "沒有下一可用 OAuth 槽（或皆額度耗盡／缺 auth）",
        }
    result = activate_codex_oauth_slot(str(picked.get("id") or ""))
    result["picked_from"] = after_id
    return result


_RESUME_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def resume_launch_args(resume_uuid: str) -> list[str]:
    """新席要接回時的參數。編號不合法就回空，呼叫端不得開席。"""
    text = str(resume_uuid or "").strip().lower()
    if not _RESUME_RE.match(text):
        return []
    return ["resume", text]


def resume_id_from_seat(seat: dict[str, Any] | None) -> str:
    """席位檔上的完整編號。只有 pending、或缺、或形狀不對，都回空。"""
    if not isinstance(seat, dict):
        return ""
    raw = seat.get("session_uuid")
    if not isinstance(raw, str) or not raw.strip():
        return ""
    text = raw.strip().lower()
    if not _RESUME_RE.match(text):
        return ""
    return text


def default_memory_paths(code: str, aib_home: str, handoff_path: str = "") -> list[str]:
    """這一席自己的、共用的那本、交接那一頁。頁的路徑由呼叫端給。"""
    home = Path(aib_home)
    paths: list[str] = []
    code = str(code or "").strip()
    if code:
        paths.append(str(home / "memory" / code / "context.md"))
    paths.append(str(home / "memory" / "root" / "context.md"))
    if str(handoff_path or "").strip():
        paths.append(str(handoff_path))
    return paths


def resume_launch_env(resume_uuid: str) -> dict[str, str]:
    """跟現有開席入口同一形：環境變數帶編號，參數是 resume。不啟動。"""
    launch = resume_launch_args(resume_uuid)
    if not launch:
        return {}
    return {"AIB_RESUME_SESSION_UUID": launch[1]}


def format_cabin_line(result: dict[str, Any]) -> str:
    """艙裡只留一行。失敗寫原因，不寫內部錯誤。"""
    if result.get("ok"):
        return "已從 %s 換到 %s，接 %s。原席待接" % (
            result.get("old_email") or "舊帳",
            result.get("new_email") or "新帳",
            str(result.get("resume_uuid") or "")[:8],
        )
    reason = str(result.get("reason") or "")
    text = {
        "no_resume_id": "這席沒有可接回的編號",
        "none_available": "沒有下一格可用，沒有換帳",
        "logout_failed": "登出失敗，沒有換帳",
        "switch_failed": "已登出，登入檔沒換成",
        "auto_off": "自動換帳沒開",
    }.get(reason, "沒有換成")
    return text


def auto_handoff_enabled(data: dict[str, Any] | None = None) -> bool:
    """沒寫開關就是開。只有明寫 false 才把整艙關掉。單一席的關閉另外記在席位檔。"""
    data = ua.load() if data is None else data
    failover = data.get("failover") if isinstance(data.get("failover"), dict) else {}
    if "auto_enabled" not in failover:
        return True
    return bool(failover.get("auto_enabled"))


def _message_bits(payload: dict[str, Any]) -> str:
    text = payload.get("content") or payload.get("text") or payload.get("message") or ""
    if isinstance(text, dict):
        text = text.get("content") or text.get("text") or ""
    if isinstance(text, list):
        bits = []
        for part in text:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                bits.append(part["text"])
            elif isinstance(part, str):
                bits.append(part)
        text = "\n".join(bits)
    if not isinstance(text, str):
        return ""
    return text.strip()


def dialogue_excerpt(rollout: str | Path, limit: int = 8) -> list[str]:
    """ez2 對話只留使用者和 Codex 的短句。開發者提示和開席白句不要。"""
    path = Path(rollout)
    if not path.is_file():
        return []
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        if str(payload.get("type") or "") != "message":
            continue
        role = str(payload.get("role") or "")
        if role == "user":
            who = "你"
        elif role == "assistant":
            who = "Codex"
        else:
            continue
        text = " ".join(_message_bits(payload).split())
        if not text or "請只回覆 AIB-READY" in text or text.startswith("<"):
            continue
        lines.append("%s：%s" % (who, text[:240]))
    return lines[-limit:]


def prepare_continue_pack(
    codex_home: str,
    aib_home: str,
    resume_uuid: str = "",
) -> dict[str, Any]:
    """把舊 resume、ez2 對話、第二大腦抄到交接目錄。不把舊編號拿去 resume。"""
    home = Path(codex_home or "")
    sessions = home / "sessions"
    files = sorted(sessions.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime) if sessions.is_dir() else []
    wanted = str(resume_uuid or "").strip().lower()
    rollout = None
    if wanted:
        for path in files:
            if wanted in path.name.lower():
                rollout = path
                break
    if rollout is None and files:
        rollout = files[-1]
    dest_root = Path(aib_home or "") / "handoff"
    stamp = wanted or (rollout.stem if rollout else "none")
    dest = dest_root / stamp
    dest.mkdir(parents=True, mode=0o700, exist_ok=True)
    resume_copy = ""
    if rollout is not None and rollout.is_file():
        target = dest / "resume.jsonl"
        target.write_bytes(rollout.read_bytes())
        resume_copy = str(target)
    excerpt = dialogue_excerpt(rollout) if rollout else []
    dialogue_path = dest / "ez2-dialogue.md"
    dialogue_path.write_text(
        ("\n".join(excerpt) + "\n") if excerpt else "沒有讀到 ez2 對話。\n",
        encoding="utf-8",
    )
    brain = bound_brain_folder(aib_home, wanted)
    last_ask = ""
    for line in reversed(excerpt):
        if line.startswith("你："):
            last_ask = line[2:]
            break
    recent = "\n".join(excerpt[-6:]) if excerpt else "沒有讀到最近幾輪。"
    prompt = (
        "這是接席。舊帳用量到頂。不要 resume 舊編號，也不要整份讀舊對話。\n"
        "立刻接著做，不要先問我。\n"
        "最近幾輪：\n%s\n"
        "最後要做的是：%s\n"
        "第二大腦：%s"
        % (
            recent,
            last_ask or "看上面最近幾輪",
            brain or "這一席沒有大腦",
        )
    )
    return {
        "prompt": prompt,
        "resume": resume_copy,
        "dialogue": str(dialogue_path),
        "brain": [brain] if brain else [],
        "last_ask": last_ask,
    }


def bound_brain_folder(aib_home: str, resume_uuid: str) -> str:
    """只認這一席綁定的大腦目錄。沒綁就空字串，不拿共用筆記充數。"""
    code = str(resume_uuid or "").replace("-", "")[-5:]
    if len(code) < 5:
        return ""
    try:
        import seat_who
        ident = seat_who._identity(aib_home, "", code)
    except Exception:
        return ""
    folder = str((ident or {}).get("brain_folder") or "")
    if not folder or folder in ("UNRESOLVED", "未綁", "—"):
        return ""
    path = Path(folder).expanduser()
    if not path.exists():
        return ""
    return str(path)


def unfinished_note(paths: list[str], limit: int = 400) -> dict[str, Any]:
    """三處記憶只取短段。缺檔不算失敗。"""
    parts: list[str] = []
    missing: list[str] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            missing.append(str(path))
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            missing.append(str(path))
            continue
        piece = _short_unfinished(text)
        if piece:
            parts.append(piece)
        else:
            missing.append(str(path))
    note = "\n".join(parts).strip()
    if len(note) > limit:
        note = note[:limit].rstrip()
    return {"text": note, "missing": missing}


def _short_unfinished(text: str) -> str:
    lines = text.splitlines()
    grabbed: list[str] = []
    capture = False
    for line in lines:
        if "還沒做完" in line or "未完成" in line:
            capture = True
            continue
        if not capture:
            continue
        if line.startswith("#") and grabbed:
            break
        if line.strip():
            grabbed.append(line.strip())
        if len(grabbed) >= 5:
            break
    if grabbed:
        return " ".join(grabbed)
    buf: list[str] = []
    for line in lines:
        if not line.strip():
            if buf:
                break
            continue
        if line.startswith("#"):
            continue
        buf.append(line.strip())
        if len(buf) >= 3:
            break
    return " ".join(buf)


def run_quota_handoff(
    *,
    resume_uuid: str,
    logout: Callable[[], int],
    after_id: str | None = None,
    until_epoch: float | None = None,
    automatic: bool = False,
    memory_paths: list[str] | None = None,
) -> dict[str, Any]:
    """留下編號、確認有下一格、才登出、再換檔。不開真的 Codex。

    automatic=True 時，failover.auto_enabled 沒開就什麼都不做。
    """
    if automatic and not auto_handoff_enabled():
        return {"ok": False, "reason": "auto_off", "did_logout": False}
    launch = resume_launch_args(resume_uuid)
    if not launch:
        return {"ok": False, "reason": "no_resume_id", "did_logout": False}
    data = ua.load()
    aid = str(after_id or data.get("active_codex_oauth_id") or "").strip() or None
    old_email = ""
    if aid:
        for rec in data.get("accounts") or []:
            if isinstance(rec, dict) and str(rec.get("id") or "") == aid:
                old_email = _slot_email(rec)
                break
    picked = pick_next_oauth_slot(after_id=aid)
    if picked is None:
        return {
            "ok": False,
            "reason": "none_available",
            "did_logout": False,
            "resume_uuid": launch[1],
        }
    try:
        logout_rc = int(logout())
    except Exception as exc:
        return {
            "ok": False,
            "reason": "logout_failed",
            "did_logout": False,
            "detail": str(exc)[:120],
        }
    if logout_rc != 0:
        return {
            "ok": False,
            "reason": "logout_failed",
            "did_logout": False,
            "logout_rc": logout_rc,
        }
    if aid:
        mark_exhausted(
            aid,
            until_epoch=until_epoch,
            reason="usage_limit",
            hold=until_epoch is None,
        )
    switched = activate_codex_oauth_slot(str(picked.get("id") or ""))
    note = unfinished_note(list(memory_paths or []))
    if not switched.get("ok"):
        return {
            "ok": False,
            "reason": "switch_failed",
            "did_logout": True,
            "resume_uuid": launch[1],
            "detail": switched.get("reason") or "",
            "hint": "已登出，登入檔沒換成。不要開新席。",
        }
    hint = "已換帳。新席用這個編號接回。舊席留著，不再派工。"
    if not note.get("text"):
        hint = "記憶沒讀到，只接回舊對話。" + hint
    ready = {
        "ok": True,
        "reason": "ready",
        "did_logout": True,
        "resume_uuid": launch[1],
        "launch_args": launch,
        "old_id": aid or "",
        "new_id": switched.get("id") or "",
        "new_email": switched.get("email") or "",
        "backup_path": switched.get("backup_path") or "",
        "old_email": old_email,
        "memory_text": note.get("text") or "",
        "memory_missing": note.get("missing") or [],
        "launch_env": resume_launch_env(launch[1]),
        "hint": hint,
    }
    ready["line"] = format_cabin_line(ready)
    return ready


def handoff_from_seat(
    seat: dict[str, Any] | None,
    *,
    logout: Callable[[], int],
    code: str = "",
    aib_home: str = "",
    handoff_path: str = "",
    after_id: str | None = None,
    until_epoch: float | None = None,
    automatic: bool = False,
) -> dict[str, Any]:
    """從席位檔拿編號。沒有編號就不登出。記憶路徑用三處固定位置。"""
    resume_uuid = resume_id_from_seat(seat)
    paths = default_memory_paths(code, aib_home, handoff_path) if aib_home or handoff_path or code else None
    result = run_quota_handoff(
        resume_uuid=resume_uuid,
        logout=logout,
        after_id=after_id,
        until_epoch=until_epoch,
        automatic=automatic,
        memory_paths=paths,
    )
    if "line" not in result:
        result["line"] = format_cabin_line(result)
    return result


def luna_launch_args(resume_uuid: str, model: str | None = None) -> list[str]:
    launch = resume_launch_args(resume_uuid)
    if not launch:
        return []
    chosen = str(model or LUNA_MODEL).strip() or LUNA_MODEL
    return ["resume", "--model", chosen, launch[1]]


def _file_snap(path: str | Path | None) -> tuple[int, bytes] | None:
    if not path:
        return None
    file = Path(path)
    if not file.is_file():
        return None
    return (file.stat().st_mtime_ns, file.read_bytes())


def _find_account(acct_id: str) -> dict[str, Any] | None:
    for rec in ua.load().get("accounts") or []:
        if isinstance(rec, dict) and str(rec.get("id") or "") == str(acct_id):
            return rec
    return None


def _record_no_quota(rec: dict[str, Any], now: float) -> None:
    write_login_verify(rec, {
        "result": "no_quota",
        "attempted_at": now,
        "reset_at": now + FIVE_H,
        "ok_at": read_login_verify(rec).get("ok_at"),
    })
    mark_exhausted(str(rec.get("id") or ""), until_epoch=now + FIVE_H, reason="usage_limit")


def _record_ok(rec: dict[str, Any], now: float) -> None:
    write_login_verify(rec, {
        "result": "ok",
        "ok_at": now,
        "attempted_at": now,
    })


def _record_rejected(rec: dict[str, Any], now: float) -> None:
    write_login_verify(rec, {
        "result": "rejected",
        "attempted_at": now,
    })


def _set_top_level_assignment(text: str, key: str, value: str) -> str:
    """把設定放在檔案最外層。貼在最後一個表格裡，Codex 會當成那個表格的欄位。"""
    import re
    line_re = re.compile(r"^[ \t]*%s[ \t]*=" % re.escape(key))
    kept: list[str] = []
    in_table = False
    replaced = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_table = True
        if line_re.match(line):
            if not in_table and not replaced:
                kept.append("%s = %s" % (key, value))
                replaced = True
            continue
        kept.append(line)
    if not replaced:
        insert_at = 0
        for i, line in enumerate(kept):
            if line.strip().startswith("["):
                insert_at = i
                break
        else:
            insert_at = len(kept)
        block = ["%s = %s" % (key, value)]
        if insert_at < len(kept) and kept[insert_at].strip():
            block.append("")
        kept[insert_at:insert_at] = block
    out = "\n".join(kept)
    if out and not out.endswith("\n"):
        out += "\n"
    return out


def ensure_project_trusted(codex_home: str | Path, project_dir: str | Path) -> bool:
    """讓這一格的 Codex 信任工作目錄。沒寫進它自己的設定，開新帳就會再問一次。"""
    folder = Path(codex_home)
    if not folder.is_dir():
        return False
    try:
        shown = str(Path(project_dir).resolve()).replace('"', '\\"')
    except OSError:
        return False
    cfg = folder / "config.toml"
    text = cfg.read_text(encoding="utf-8") if cfg.is_file() else ""
    header = '[projects."%s"]' % shown
    if header in text:
        return False
    if text and not text.endswith("\n"):
        text += "\n"
    if text and not text.endswith("\n\n"):
        text += "\n"
    text += header + '\ntrust_level = "trusted"\n'
    cfg.write_text(text, encoding="utf-8")
    return True


def install_resume_env(dest: str | Path, source: str | Path) -> None:
    """把使用者的設定和外掛連過來。登入檔仍只用這一格自己的。關掉更新詢問。"""
    folder = Path(dest)
    origin = Path(source)
    if not origin.is_dir() or not folder.is_dir():
        return
    try:
        if folder.resolve() == origin.resolve():
            return
    except OSError:
        return
    for name in ("plugins", "skills"):
        src = origin / name
        dst = folder / name
        if src.exists() and not dst.exists():
            dst.symlink_to(src)
    cfg_src = origin / "config.toml"
    cfg_dst = folder / "config.toml"
    if cfg_dst.exists():
        text = cfg_dst.read_text(encoding="utf-8")
    elif cfg_src.is_file():
        text = cfg_src.read_text(encoding="utf-8")
    else:
        text = ""
    text = _set_top_level_assignment(text, "check_for_update_on_startup", "false")
    shown = str(folder.resolve()).replace('"', '\\"')
    if shown not in text:
        text += '\n[projects."%s"]\ntrust_level = "trusted"\n' % shown
    cfg_dst.write_text(text, encoding="utf-8")


def assemble_luna_seat(
    dest: str | Path,
    auth_src: str | Path,
    transcript: str | Path | None,
    memory_note: str,
    existing_memory: str = "",
) -> dict[str, Any]:
    """新席只收登入、一個對話檔、加在後面的短記憶。"""
    folder = Path(dest)
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    auth_out = folder / "auth.json"
    auth_out.write_bytes(Path(auth_src).read_bytes())
    os.chmod(auth_out, 0o600)
    copied = ""
    if transcript:
        src = Path(transcript)
        if src.is_file():
            sess = folder / "sessions"
            sess.mkdir(mode=0o700, exist_ok=True)
            target = sess / src.name
            target.write_bytes(src.read_bytes())
            copied = str(target)
            parts = src.parts
            if "sessions" in parts:
                idx = len(parts) - 1 - list(reversed(parts)).index("sessions")
                rel = Path(*parts[idx + 1:])
                if rel != Path(src.name):
                    nested = sess / rel
                    nested.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                    nested.write_bytes(src.read_bytes())
            for other in sess.iterdir():
                if other != target and other.is_file():
                    other.unlink()
    note = str(memory_note or "").strip()
    prior = str(existing_memory or "").rstrip()
    body = prior
    if note:
        body = (prior + "\n" + note).strip() if prior else note
    (folder / "seat-memory.md").write_text(body + ("\n" if body else ""), encoding="utf-8")
    return {"auth": str(auth_out), "transcript": copied, "memory": str(folder / "seat-memory.md")}


def rotate_log_path(log_dir: str | None = None) -> Path | None:
    """換號紀錄放在給定的艙目錄。沒給就不寫，避免測試碰到真艙。"""
    folder = str(log_dir or "").strip()
    if not folder:
        return None
    return Path(folder) / ROTATE_LOG_NAME


def append_rotate_log(result: dict[str, Any], now: float, log_dir: str | None = None) -> str:
    """只往後加一行。不蓋掉舊的。"""
    path = rotate_log_path(log_dir)
    if path is None:
        return ""
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    resume = str(result.get("resume_uuid") or "")
    row = {
        "time": now,
        "local": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "old_email": result.get("old_email") or "",
        "new_email": result.get("new_email") or "",
        "ok": bool(result.get("ok")),
        "reason": result.get("reason") or "",
        "resume": resume[:8],
        "model": result.get("model") or LUNA_MODEL,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return str(path)


def _finish_rotate(result: dict[str, Any], now: float, log_dir: str | None, write_log: bool) -> dict[str, Any]:
    if write_log:
        append_rotate_log(result, now, log_dir)
    return result


def run_luna_rotate(
    *,
    resume_uuid: str,
    after_id: str | None = None,
    now: float | None = None,
    automatic: bool = False,
    login: Callable[[str], dict[str, Any]] | None = None,
    ready: Callable[[str], dict[str, Any]] | None = None,
    transcript: str | None = None,
    seat_dir: str | None = None,
    memory_note: str = "",
    existing_memory: str = "",
    shared_auth: str | None = None,
    log_dir: str | None = None,
    model: str | None = None,
    env_source: str | None = None,
) -> dict[str, Any]:
    """額度用完後換到下一格。沿用原席模型；沒寫才用 luna。不寫共用登入檔。"""
    if automatic and not auto_handoff_enabled():
        return {"ok": False, "reason": "auto_off", "did_logout": False, "line": "自動換帳沒開"}
    now_f = time.time() if now is None else float(now)

    def finish(result: dict[str, Any]) -> dict[str, Any]:
        return _finish_rotate(result, now_f, log_dir, True)
    launch = luna_launch_args(resume_uuid, model)
    if not launch:
        return finish({
            "ok": False,
            "reason": "no_resume_id",
            "did_logout": False,
            "line": "這席沒有可接回的編號",
        })
    shared_before = _file_snap(shared_auth)
    data = ua.load()
    aid = str(after_id or data.get("active_codex_oauth_id") or "").strip() or None
    old = _find_account(aid) if aid else None
    if old is not None:
        _record_no_quota(old, now_f)
    picked = next_ready_slot(aid, now_f)
    if picked is None:
        picked = pick_next_oauth_slot(after_id=aid, now=now_f)
    if picked is None:
        return finish({
            "ok": False,
            "reason": "none_available",
            "did_logout": False,
            "resume_uuid": launch[3],
            "old_email": _slot_email(old) if old else "",
            "line": "沒有下一格可用，沒有換帳",
        })
    mark = login_mark(picked, now_f)
    if mark != "ok":
        if login is None or ready is None:
            return finish({
                "ok": False,
                "reason": "needs_relogin",
                "did_logout": False,
                "new_id": picked.get("id") or "",
                "old_email": _slot_email(old) if old else "",
                "new_email": _slot_email(picked),
                "line": "下一格要先重登",
            })
        slot = str(_slot_dir(picked) or "")
        auth_path = str(picked.get("auth_path") or "")
        before = Path(auth_path).read_bytes() if auth_path and os.path.isfile(auth_path) else b""
        try:
            outcome = login(slot) or {}
        except Exception as exc:
            outcome = {"ok": False, "detail": str(exc)[:120]}
        if _file_snap(shared_auth) != shared_before:
            return finish({"ok": False, "reason": "shared_touched", "did_logout": False, "line": "共用登入被改到，已停"})
        if outcome.get("busy"):
            fallback = next_ready_slot(str(picked.get("id") or ""), now_f)
            if fallback is None:
                return finish({
                    "ok": False,
                    "reason": "login_busy",
                    "did_logout": False,
                    "old_email": _slot_email(old) if old else "",
                    "new_email": _slot_email(picked),
                    "resume_uuid": launch[3],
                    "line": "另一個登入還在等，這次不換",
                })
            picked = fallback
        else:
            after = Path(auth_path).read_bytes() if auth_path and os.path.isfile(auth_path) else b""
            fresh = _find_account(str(picked.get("id") or "")) or picked
            login_failed = bool(outcome.get("rejected")) or not outcome.get("ok") or after == before
            if login_failed:
                _record_rejected(fresh, now_f)
                fallback = next_ready_slot(str(picked.get("id") or ""), now_f)
                if fallback is None:
                    return finish({
                        "ok": False,
                        "reason": "rejected" if outcome.get("rejected") else "login_incomplete",
                        "did_logout": False,
                        "old_email": _slot_email(old) if old else "",
                        "new_email": _slot_email(picked),
                        "resume_uuid": launch[3],
                        "line": "需重登" if outcome.get("rejected") else "登入沒完成",
                    })
                picked = fallback
            else:
                probe = ready(auth_path) or {}
                if _file_snap(shared_auth) != shared_before:
                    return finish({"ok": False, "reason": "shared_touched", "did_logout": False, "line": "共用登入被改到，已停"})
                if probe.get("no_quota"):
                    _record_no_quota(fresh, now_f)
                    return finish({
                        "ok": False,
                        "reason": "no_quota",
                        "did_logout": False,
                        "old_email": _slot_email(old) if old else "",
                        "new_email": _slot_email(picked),
                        "resume_uuid": launch[3],
                        "line": "登入後沒有額度",
                    })
                if not probe.get("ok"):
                    _record_rejected(fresh, now_f)
                    return finish({
                        "ok": False,
                        "reason": "rejected",
                        "did_logout": False,
                        "old_email": _slot_email(old) if old else "",
                        "new_email": _slot_email(picked),
                        "resume_uuid": launch[3],
                        "line": "需重登",
                    })
                if not probe.get("unverified"):
                    _record_ok(fresh, now_f)
                    picked = _find_account(str(picked.get("id") or "")) or picked
    if seat_dir:
        packed = assemble_luna_seat(
            seat_dir,
            str(picked.get("auth_path") or ""),
            transcript,
            memory_note,
            existing_memory,
        )
        if env_source:
            install_resume_env(seat_dir, env_source)
    else:
        packed = {}
    if _file_snap(shared_auth) != shared_before:
        return finish({"ok": False, "reason": "shared_touched", "did_logout": False, "line": "共用登入被改到，已停"})
    old_email = _slot_email(old) if old else ""
    new_email = _slot_email(picked)
    ready_out = {
        "ok": True,
        "reason": "ready",
        "did_logout": False,
        "resume_uuid": launch[3],
        "launch_args": launch,
        "model": launch[2],
        "old_email": old_email,
        "new_email": new_email,
        "new_id": picked.get("id") or "",
        "seat": packed,
        "line": "已從 %s 換到 %s，接 %s。原席待接" % (old_email or "舊帳", new_email or "新帳", launch[3][:8]),
    }
    return finish(ready_out)


# Codex 開場那句「You have 3 usage limit resets available. Run /usage to use one.」
# 意思是「還有額度重置可用」,跟到頂相反,但它含 usage limit 字面。
# ★ 剔掉這句再比對,不是「看到它就放行」:同一段文字裡可能同時有這句通知與真的到頂句,
#   放行式會連真到頂一起漏掉,而漏掉是無聲的(席位就這樣被判成有額度而不換號)。
#   只剔到 available,不吃後面的字,免得把同一行後半段真的到頂句一起吃掉。
_QUOTA_RESET_NOTICE = re.compile(
    r"(?:you have\s+)?\d*\s*usage\s+limit\s+resets?\s+available")


def quota_limit_text(text: str) -> bool:
    """★ 先把所有空白(含換行)正規化成單一空白,再剔除、再比對。

    整合席 a seat 2026-09-26 17:5x 抓到、pm 覆量確認:語料是**畫面**
    (`capture-pane`),而畫面會折行。那句通知被折在 `usage limit` 與 `resets`
    之間時,剔除比對不到 ⇒ 通知又被當成額度到頂,**原本的缺陷整個回來**。
    同一件事的反面也成立:真的到頂句被折行時,原本的判準抓不到它
    ⇒ 席位停在死畫面而沒有人換號(靜、更難發現)。
    ⇒ 正規化是兩個方向同時修,不是只修誤判那一邊。
    換行換成空白(不是刪掉):只有原本相鄰的字會接起來,那正是折行的定義。
    """
    raw = re.sub(r"\s+", " ", str(text or "").lower())
    raw = _QUOTA_RESET_NOTICE.sub(" ", raw)
    return ("usage limit" in raw) or ("usage_limit" in raw) or ("額度用完" in raw)


def on_codex_error(text: str, **kwargs: Any) -> dict[str, Any]:
    """只有明確額度用完才進入輪替，而且自動開關預設關。"""
    if not quota_limit_text(text):
        return {"ok": False, "reason": "not_quota", "did_logout": False, "line": "不是額度用完"}
    kwargs["automatic"] = True
    if "log_dir" not in kwargs:
        kwargs["log_dir"] = os.environ.get("EZ2_COCKPIT_STATE_DIR") or ""
    return run_luna_rotate(**kwargs)
