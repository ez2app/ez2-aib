# SPDX-License-Identifier: Apache-2.0
"""同品牌多憑證（先 Codex）+ AI用量標題連點解鎖。

隱藏門：3 秒內點標題「─ 用量 ─…」≥3 下 → unlocked。
解鎖後可新增 Codex OAuth（預設）或 Token Key；每筆分開列、可重驗。
"""
from __future__ import annotations

try:
    import usage_subtabs as _usage_subtabs
except Exception:  # pragma: no cover
    _usage_subtabs = None  # type: ignore
import sys

from pathlib import Path

import hashlib
import json
import os
import subprocess
import time
import uuid
from typing import Any


def format_probed_ago(ts: int | float | None, now: int | float | None = None) -> str:
    """對齊量不到列的「量於 … 前」；有讀數時也用同一套字樣。

    <1 分 → 剛剛；<1 時 → Nm 前；否則 Nh 前（與 stale_reason_text 的小時粒度一致）。
    無時刻 → 空字串（呼叫端決定要不要畫）。
    """
    try:
        n = int(ts or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return ""
    try:
        import time as _time
        now_i = int(now if now is not None else _time.time())
    except (TypeError, ValueError):
        return ""
    age = max(0, now_i - n)
    if age < 60:
        return "量於剛剛"
    if age < 3600:
        return "量於 %sm 前" % max(1, age // 60)
    return "量於 %sh 前" % max(1, age // 3600)


def format_last_bound_label(ts: int | float | None) -> str:
    """Asia/Taipei MM-DD HH:MM:SS（給「更新:」前綴用）; empty → —"""
    try:
        n = int(ts or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return "—"
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(n, ZoneInfo("Asia/Taipei")).strftime("%m-%d %H:%M:%S")
    except Exception:
        try:
            from datetime import datetime, timezone, timedelta
            return datetime.fromtimestamp(n, timezone(timedelta(hours=8))).strftime("%m-%d %H:%M:%S")
        except Exception:
            return "—"


def oauth_account_from_auth(auth_path: str) -> str:
    """Read display account from Codex OAuth auth.json (id_token email/name). Never returns tokens."""
    import base64
    import json
    try:
        data = json.loads(Path(auth_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    tok = ((data.get("tokens") or {}) if isinstance(data, dict) else {}).get("id_token") or ""
    if not isinstance(tok, str) or tok.count(".") < 2:
        return ""
    try:
        pad = "=" * (-len(tok.split(".")[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(tok.split(".")[1] + pad))
    except (ValueError, OSError):
        return ""
    if not isinstance(payload, dict):
        return ""
    email = payload.get("email")
    if isinstance(email, str) and "@" in email and len(email) < 120:
        return email.strip()
    name = payload.get("name")
    if isinstance(name, str) and name.strip() and len(name) < 80:
        return name.strip()
    return ""

BRAND_CODEX = "codex"
KIND_OAUTH = "oauth"
KIND_TOKEN = "token"
TAP_WINDOW_S = 3.0
TAP_NEED = 3
ACTION_HEADER_TAP = "usage-header-tap"
ACTION_ADD_OAUTH = "usage-add-codex-oauth"
ACTION_ADD_TOKEN = "usage-add-codex-token"
ACTION_REPROBE_ACCT = "usage-reprobe-account"
ACTION_REMOVE_ACCT = "usage-remove-account"


def _usage_accounts_file_override() -> str:
    # 獨立函式:這是路徑覆寫,不是艙變數。和艙家寫在同一個函式裡,
    # 艙清單掃描會把它算成換艙時也要清的名字。
    return os.environ.get("EZ2_USAGE_ACCOUNTS_FILE") or ""


def _state_path() -> str:
    override = _usage_accounts_file_override()
    if override:
        return override
    home = os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib")
    # Prefer live cabin cockpit dir when present.
    cockpit = os.environ.get("EZ2_COCKPIT_STATE_DIR")
    if cockpit:
        return os.path.join(cockpit, "usage-accounts.json")
    return os.path.join(home, "state", "usage-accounts.json")


def _default() -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema": 1,
        "unlocked": False,
        "taps": [],
        "accounts": [],
    }
    if _usage_subtabs is not None:
        _usage_subtabs.ensure_store_defaults(doc)
    return doc


def load() -> dict[str, Any]:
    path = _state_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return _default()
        data.setdefault("schema", 1)
        data.setdefault("unlocked", False)
        data.setdefault("taps", [])
        data.setdefault("accounts", [])
        if not isinstance(data["taps"], list):
            data["taps"] = []
        if not isinstance(data["accounts"], list):
            data["accounts"] = []
        if _usage_subtabs is not None:
            _usage_subtabs.ensure_store_defaults(data)
        return data
    except FileNotFoundError:
        return _default()
    except (OSError, ValueError, json.JSONDecodeError):
        return _default()


def save(data: dict[str, Any]) -> None:
    path = _state_path()
    os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def record_header_tap(now: float | None = None) -> dict[str, Any]:
    """Record one title tap. Unlock when ≥TAP_NEED within TAP_WINDOW_S."""
    now = time.time() if now is None else float(now)
    data = load()
    taps = [float(t) for t in data.get("taps") or [] if isinstance(t, (int, float))]
    taps = [t for t in taps if now - t <= TAP_WINDOW_S]
    taps.append(now)
    data["taps"] = taps[-8:]
    newly = False
    if len(taps) >= TAP_NEED and not data.get("unlocked"):
        data["unlocked"] = True
        if _usage_subtabs is not None:
            _usage_subtabs.on_unlock(data)
        newly = True
        data["taps"] = []
    save(data)
    return {"unlocked": bool(data.get("unlocked")), "taps": len(data.get("taps") or []), "newly_unlocked": newly}


def set_unlocked(flag: bool) -> None:
    data = load()
    data["unlocked"] = bool(flag)
    if not flag:
        data["taps"] = []
    save(data)


def list_accounts(brand: str = BRAND_CODEX) -> list[dict[str, Any]]:
    out = []
    for rec in load().get("accounts") or []:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("brand") or "") != brand:
            continue
        out.append(dict(rec))
    return out


def _codex_auth_path() -> str:
    override = os.environ.get("EZ2_CODEX_AUTH_PATH")
    if override:
        return override
    return os.path.expanduser("~/.codex/auth.json")


def _auth_fingerprint(path: str | None = None) -> str:
    """Stable fingerprint: size + email + content hash (mtime is too fragile)."""
    path = path or _codex_auth_path()
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return ""
    email = (oauth_account_from_auth(path) or "").strip().lower()
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return "size:%d:email:%s:sha:%s" % (len(raw), email or "-", digest)



def add_codex_oauth(label: str = "") -> dict[str, Any]:
    """Snapshot current ~/.codex/auth.json into a named OAuth slot."""
    src = _codex_auth_path()
    if not os.path.isfile(src):
        raise FileNotFoundError("找不到 ~/.codex/auth.json；請先在 Codex／ChatGPT 桌面登入 OAuth")
    data = load()
    acct_id = uuid.uuid4().hex[:12]
    home = os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib")
    slot_dir = os.path.join(home, "usage-accounts", "codex", acct_id)
    os.makedirs(slot_dir, mode=0o700, exist_ok=True)
    dst = os.path.join(slot_dir, "auth.json")
    with open(src, "rb") as fh:
        raw = fh.read()
    with open(dst, "wb") as fh:
        fh.write(raw)
    os.chmod(dst, 0o600)
    label = (label or "").strip() or acct_id[:6]
    rec = {
        "id": acct_id,
        "brand": BRAND_CODEX,
        "kind": KIND_OAUTH,
        "label": label,
        "auth_path": dst,
        "fingerprint": _auth_fingerprint(dst),
        "oauth_account": oauth_account_from_auth(dst),
        "created_at": int(time.time()),
        "last_probe": None,
        "usage": None,
    }
    data.setdefault("accounts", []).append(rec)
    data["unlocked"] = True
    save(data)
    return rec


def add_codex_token(token: str, label: str = "") -> dict[str, Any]:
    token = (token or "").strip()
    if len(token) < 8:
        raise ValueError("Token Key 太短")
    data = load()
    acct_id = uuid.uuid4().hex[:12]
    home = os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib")
    slot_dir = os.path.join(home, "usage-accounts", "codex", acct_id)
    os.makedirs(slot_dir, mode=0o700, exist_ok=True)
    tok_path = os.path.join(slot_dir, "token.env")
    with open(tok_path, "w", encoding="utf-8") as fh:
        fh.write("CODEX_API_KEY=%s\n" % token.replace("\n", ""))
    os.chmod(tok_path, 0o600)
    label = (label or "").strip() or acct_id[:6]
    rec = {
        "id": acct_id,
        "brand": BRAND_CODEX,
        "kind": KIND_TOKEN,
        "label": label,
        "token_path": tok_path,
        "token_suffix": token[-4:],
        "created_at": int(time.time()),
        "last_probe": None,
        "usage": None,
    }
    data.setdefault("accounts", []).append(rec)
    data["unlocked"] = True
    save(data)
    return rec



def _codex_bin() -> str:
    for cand in (
        os.environ.get("CODEX_BIN") or "",
        "/opt/homebrew/bin/codex",
        "/usr/local/bin/codex",
        os.path.expanduser("~/.local/bin/codex"),
    ):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return "codex"



def _wait_tcp_listen_v6(host: str, port: int, timeout_s: float = 8.0) -> bool:
    import socket
    deadline = time.time() + float(timeout_s)
    while time.time() < deadline:
        try:
            s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            s.settimeout(0.3)
            s.connect((host, int(port)))
            s.close()
            return True
        except OSError:
            time.sleep(0.1)
    return False


def _wait_tcp_listen(host: str, port: int, timeout_s: float = 8.0) -> bool:
    import socket
    deadline = time.time() + float(timeout_s)
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _spawn_localhost_ipv6_proxy(port: int = 1455) -> int | None:
    """Chrome 常連 [::1]:port；codex login 只聽 127.0.0.1。轉發 ::1 → 127.0.0.1。"""
    import socket
    import subprocess

    try:
        probe = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("::1", int(port)))
        except OSError:
            probe.close()
            return None
        probe.close()
    except OSError:
        return None

    proxy_py = str(Path(__file__).resolve().with_name("localhost_ipv6_proxy.py"))
    proc = subprocess.Popen(
        [sys.executable, proxy_py, "--port", str(int(port))],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            s.settimeout(0.3)
            s.connect(("::1", int(port)))
            s.close()
            return int(proc.pid)
        except OSError:
            if proc.poll() is not None:
                return None
            time.sleep(0.05)
    return int(proc.pid)


def start_codex_device_login() -> dict[str, Any]:
    """啟動瀏覽器 OAuth（`codex login`，免裝置識別碼）；回傳 url／pid。

    仍沿用 login-start CLI 名稱。遠端／無頭環境才需要 `codex login --device-auth`。
    Chrome 回呼 localhost 常走 IPv6，會再起 ::1→127.0.0.1 轉發。
    """
    import re
    import subprocess
    import tempfile

    log_fd, log_path = tempfile.mkstemp(prefix="codex-browser-login-", suffix=".log")
    os.close(log_fd)
    bin_path = _codex_bin()
    log_fh = open(log_path, "w", encoding="utf-8")
    # macOS 的 Codex 用系統介面開瀏覽器，不吃 BROWSER=，擋不住。
    # 這裡仍設 BROWSER，只讓會遵守它的環境不要自己開。
    # Darwin 上 mouse-ctl 不再另外 open，避免同一個授權網址開成兩個大頁。
    env = os.environ.copy()
    env["BROWSER"] = "/usr/bin/true"
    proc = subprocess.Popen(
        [bin_path, "login"],
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=env,
    )
    ansi = re.compile(r"\[[0-9;]*m")
    url = ""
    text = ""
    for _ in range(80):
        time.sleep(0.25)
        try:
            raw = Path(log_path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            raw = ""
        text = ansi.sub("", raw)
        m_url = re.search(r"https://auth\.openai\.com/oauth/authorize\S+", text)
        if not m_url:
            m_url = re.search(r"https://auth\.openai\.com/codex/device\S*", text)
        if m_url:
            url = m_url.group(0).rstrip(").,;'\"")
            break
        if proc.poll() is not None:
            break
    if not url:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            log_fh.close()
        except OSError:
            pass
        raise RuntimeError("拿不到 Codex 瀏覽器登入 URL；請確認 `codex login` 可用")

    if not _wait_tcp_listen("127.0.0.1", 1455, timeout_s=8.0):
        try:
            proc.kill()
        except OSError:
            pass
        raise RuntimeError("Codex 登入伺服器未在 127.0.0.1:1455 起來")
    proxy_pid = _spawn_localhost_ipv6_proxy(1455)
    # 再開瀏覽器前確認 [::1] 已可連（避免 Chrome 走 IPv6 被拒）
    if proxy_pid and not _wait_tcp_listen_v6("::1", 1455, timeout_s=3.0):
        # 轉發沒起來就別開頁，避免使用者看到拒絕連線
        try:
            proc.kill()
        except OSError:
            pass
        try:
            os.kill(int(proxy_pid), 9)
        except OSError:
            pass
        raise RuntimeError("IPv6 localhost 轉發未就緒（::1:1455）")

    m_code = re.search(r"([A-Z0-9]{4,5}-[A-Z0-9]{4,8})", text)
    code = m_code.group(1) if m_code else ""
    return {
        "ok": True,
        "url": url,
        "code": code,
        "mode": "browser" if "oauth/authorize" in url else "device",
        "pid": int(proc.pid),
        "proxy_pid": int(proxy_pid) if proxy_pid else 0,
        "log": log_path,
        "prev_fingerprint": _auth_fingerprint(),
        "prev_email": oauth_account_from_auth(_codex_auth_path()) if os.path.isfile(_codex_auth_path()) else "",
    }

def cancel_codex_device_login(pid: int, proxy_pid: int = 0) -> dict[str, Any]:
    killed = []
    for p in (int(pid or 0), int(proxy_pid or 0)):
        if p <= 0:
            continue
        try:
            os.kill(p, 15)
        except OSError as exc:
            return {"ok": False, "reason": str(exc), "pid": p, "killed": killed}
        time.sleep(0.2)
        try:
            os.kill(p, 9)
        except OSError:
            pass
        killed.append(p)
    return {"ok": True, "killed": killed, "cancelled": True}

def _pid_running(pid: int) -> bool:
    """行程還在跑。殭屍不算：macOS 上 kill(pid, 0) 對殭屍仍成功，父行程會一直等。"""
    pid = int(pid or 0)
    if pid <= 0:
        return False
    try:
        waited, _status = os.waitpid(pid, os.WNOHANG)
        if waited:
            return False
    except ChildProcessError:
        pass
    except OSError:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        stat = subprocess.check_output(["ps", "-o", "stat=", "-p", str(pid)], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return True
    return not stat.startswith("Z")


def wait_codex_device_login(pid: int, prev_fingerprint: str = "", timeout_s: float = 300.0, log_path: str = "") -> dict[str, Any]:
    """等裝置登入結束（行程結束、auth 變了，或記錄寫了成功），回傳新 email／指紋。"""
    import time as _time

    pid = int(pid or 0)
    deadline = _time.time() + float(timeout_s)
    auth = _codex_auth_path()
    try:
        start_mtime = os.path.getmtime(auth)
    except OSError:
        start_mtime = 0.0
    alive = True
    while _time.time() < deadline:
        alive = _pid_running(pid) if pid > 0 else False
        fp = _auth_fingerprint()
        try:
            mtime = os.path.getmtime(auth)
        except OSError:
            mtime = 0.0
        changed = bool(
            (prev_fingerprint and fp and fp != prev_fingerprint)
            or (mtime > start_mtime + 0.05)
        )
        logged_in = False
        if log_path and os.path.isfile(log_path):
            try:
                tail = Path(log_path).read_text(encoding="utf-8", errors="ignore")[-4000:]
            except OSError:
                tail = ""
            logged_in = "Successfully logged in" in tail
        if changed or logged_in:
            email = oauth_account_from_auth(auth) if os.path.isfile(auth) else ""
            return {"ok": True, "email": email, "fingerprint": fp, "changed": changed or logged_in, "alive": alive}
        if pid > 0 and not alive:
            email = oauth_account_from_auth(auth) if os.path.isfile(auth) else ""
            return {"ok": bool(changed or logged_in), "email": email, "fingerprint": fp, "changed": changed, "alive": False}
        _time.sleep(0.5)
    return {"ok": False, "reason": "timeout", "alive": alive}


def rebind_after_device_login(acct_id: str | None = None, prev_fingerprint: str = "") -> dict[str, Any]:
    """登入完成後加綁：若指紋沒變則回 login_unchanged（不當 already_bound 唬弄）。"""
    fp = _auth_fingerprint()
    if prev_fingerprint and fp == prev_fingerprint:
        email = oauth_account_from_auth(_codex_auth_path()) if os.path.isfile(_codex_auth_path()) else ""
        return {
            "ok": False,
            "_rebind_action": "login_unchanged",
            "_rebind_message": "登入後桌面 auth 沒變（仍是 %s）。請在瀏覽器換成另一個 ChatGPT 帳再完成授權。" % (email or "?"),
            "_rebind_email": email,
        }
    out = rebind_codex_oauth(acct_id)
    out["ok"] = out.get("_rebind_action") not in ("already_bound", "login_unchanged")
    return out



def _note_rebind_login(acct_id: str) -> None:
    """重綁問到用量才把這一格記成綠勾。問失敗不改舊標記。"""
    import usage_failover as uf
    rec = None
    for item in load().get("accounts") or []:
        if isinstance(item, dict) and str(item.get("id") or "") == str(acct_id):
            rec = item
            break
    if rec is None:
        return
    usage = rec.get("usage") if isinstance(rec.get("usage"), dict) else {}
    try:
        probed = float(usage.get("probed_at") or 0)
    except (TypeError, ValueError):
        probed = 0.0
    when = probed if probed > 0 else time.time()
    if usage.get("status") == "ok" and usage.get("rows"):
        uf._record_ok(rec, when)
        uf.mark_exhausted(str(acct_id), until_epoch=None, hold=False)
    elif usage.get("status") == "revoked":
        uf._record_rejected(rec, when)


def _probe_and_note_rebind(acct_id: str) -> None:
    try:
        probe_account(str(acct_id), mode="wham")
    except Exception:
        return
    _note_rebind_login(acct_id)


def rebind_codex_oauth(acct_id: str | None = None) -> dict[str, Any]:
    """加綁目前桌面 OAuth：不蓋掉既有槽。

    流程假設操作者已在 Codex／ChatGPT 登入「要加的帳」。
    - 若目前指紋已在某一槽 → 不新建、不覆蓋，只重驗那槽
    - 若點到的槽是空的／無指紋 → 填進該槽（仍不碰其他槽）
    - 若點到的槽已有「不同」帳 → 另開新槽保存目前登入，舊槽原樣保留
    - 否則一律新增一槽
    """
    src = _codex_auth_path()
    if not os.path.isfile(src):
        raise FileNotFoundError("找不到 ~/.codex/auth.json；請先在 Codex／ChatGPT 登入要加綁的帳")
    fp = _auth_fingerprint()
    email = oauth_account_from_auth(src)
    data = load()

    # already bound? compare SLOT FILE to desktop (stored fingerprint can lie)
    email_l = (email or "").strip().lower()
    for a in data.get("accounts") or []:
        if not isinstance(a, dict) or a.get("kind") != KIND_OAUTH:
            continue
        slot_path = str(a.get("auth_path") or "")
        slot_fp_file = _auth_fingerprint(slot_path) if slot_path and os.path.isfile(slot_path) else ""
        same_file = bool(fp and slot_fp_file and fp == slot_fp_file)
        slot_email = str(a.get("oauth_account") or "").strip().lower()
        if not slot_email and slot_path:
            slot_email = (oauth_account_from_auth(slot_path) or "").strip().lower()
        same_email = bool(email_l and slot_email and email_l == slot_email)
        stale_meta = bool(
            fp
            and a.get("fingerprint")
            and a.get("fingerprint") == fp
            and not same_file
        )
        if same_file:
            _probe_and_note_rebind(str(a.get("id")))
            data = load()
            for b in data.get("accounts") or []:
                if isinstance(b, dict) and b.get("id") == a.get("id"):
                    out = dict(b)
                    out["_rebind_action"] = "already_bound"
                    out["_rebind_message"] = "目前桌面登入仍是 %s，這個信箱已經綁過了；請先在 Codex 換成另一個帳再點 🔄" % (email or slot_email or "?")
                    out["_rebind_email"] = email or slot_email
                    return out
            out = dict(a)
            out["_rebind_action"] = "already_bound"
            out["_rebind_message"] = "目前桌面登入仍是 %s，這個信箱已經綁過了；請先在 Codex 換成另一個帳再點 🔄" % (email or "?")
            out["_rebind_email"] = email
            return out
        if same_email or stale_meta:
            # the maintainer 2026-09-22: 同信箱重登／指紋欄過期 → 就地更新 auth 快照
            dst = slot_path
            if not dst:
                home = os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib")
                slot_dir = os.path.join(home, "usage-accounts", "codex", str(a.get("id")))
                os.makedirs(slot_dir, mode=0o700, exist_ok=True)
                dst = os.path.join(slot_dir, "auth.json")
                a["auth_path"] = dst
            with open(src, "rb") as fh:
                raw = fh.read()
            os.makedirs(os.path.dirname(dst) or ".", mode=0o700, exist_ok=True)
            with open(dst, "wb") as fh:
                fh.write(raw)
            os.chmod(dst, 0o600)
            a["fingerprint"] = fp
            a["oauth_account"] = email or oauth_account_from_auth(dst)
            a["usage"] = None
            a["last_probe"] = None
            a["rebound_at"] = int(time.time())
            save(data)
            _probe_and_note_rebind(str(a.get("id")))
            data = load()
            for b in data.get("accounts") or []:
                if isinstance(b, dict) and b.get("id") == a.get("id"):
                    out = dict(b)
                    out["_rebind_action"] = "refreshed_tokens"
                    out["_rebind_message"] = "已更新 %s 的登入快照（同信箱重登）" % (email or slot_email or "?")
                    out["_rebind_email"] = email or slot_email
                    return out
            out = dict(a)
            out["_rebind_action"] = "refreshed_tokens"
            out["_rebind_email"] = email or slot_email
            return out

    def _write_auth_into(rec: dict[str, Any]) -> dict[str, Any]:
        dst = str(rec.get("auth_path") or "")
        if not dst:
            home = os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib")
            slot_dir = os.path.join(home, "usage-accounts", "codex", str(rec.get("id")))
            os.makedirs(slot_dir, mode=0o700, exist_ok=True)
            dst = os.path.join(slot_dir, "auth.json")
            rec["auth_path"] = dst
        with open(src, "rb") as fh:
            raw = fh.read()
        os.makedirs(os.path.dirname(dst) or ".", mode=0o700, exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(raw)
        os.chmod(dst, 0o600)
        rec["fingerprint"] = fp
        rec["oauth_account"] = email or oauth_account_from_auth(dst)
        rec["usage"] = None
        rec["last_probe"] = None
        rec["rebound_at"] = int(time.time())
        return rec

    # fill empty clicked slot if safe
    if acct_id:
        rec = None
        for a in data.get("accounts") or []:
            if isinstance(a, dict) and a.get("id") == acct_id:
                rec = a
                break
        if rec is not None and rec.get("kind") == KIND_OAUTH:
            old_fp = str(rec.get("fingerprint") or "")
            # empty / no fingerprint → fill this slot only
            if not old_fp:
                _write_auth_into(rec)
                save(data)
                _probe_and_note_rebind(acct_id)
                data = load()
                for b in data.get("accounts") or []:
                    if isinstance(b, dict) and b.get("id") == acct_id:
                        out = dict(b)
                        out["_rebind_action"] = "filled_slot"
                        return out
            # different account already in this slot → do NOT overwrite; add new
            # (fall through)

    # the maintainer 09-22: 信箱已在列表 → 只更新該列時間／狀態，不另開新槽
    if email:
        for a in data.get("accounts") or []:
            if not isinstance(a, dict) or a.get("kind") != KIND_OAUTH:
                continue
            slot_email = str(a.get("oauth_account") or "").strip().lower()
            if not slot_email:
                slot_path = str(a.get("auth_path") or "")
                if slot_path:
                    slot_email = (oauth_account_from_auth(slot_path) or "").strip().lower()
            if slot_email and slot_email == email:
                _write_auth_into(a)
                save(data)
                _probe_and_note_rebind(str(a.get("id")))
                data = load()
                for b in data.get("accounts") or []:
                    if isinstance(b, dict) and b.get("id") == a.get("id"):
                        out = dict(b)
                        out["_rebind_action"] = "refreshed_tokens"
                        out["_rebind_message"] = "已更新既有槽 %s 的時間／狀態" % (email,)
                        out["_rebind_email"] = email
                        return out
                out = dict(a)
                out["_rebind_action"] = "refreshed_tokens"
                out["_rebind_email"] = email
                return out

    # 新信箱只走 [+] OAuth 新增。🔄 不另開一列，也不改掉這一列原來的帳。
    return {
        "ok": False,
        "_rebind_action": "use_add_for_new_email",
        "_rebind_message": "這是名單裡沒有的信箱 %s。請用 [+] OAuth 新增，這一列沒有改。" % (email or "?"),
        "_rebind_email": email or "",
    }


def _listed_oauth_email(rec: dict[str, Any]) -> str:
    email = str(rec.get("oauth_account") or "").strip().lower()
    if email:
        return email
    path = str(rec.get("auth_path") or "")
    if path and os.path.isfile(path):
        return (oauth_account_from_auth(path) or "").strip().lower()
    return ""


def add_oauth_if_new(prev_fingerprint: str = "") -> dict[str, Any]:
    """[+] 專用。名單裡已有這個信箱就不新增，也不改舊列。"""
    src = _codex_auth_path()
    if not os.path.isfile(src):
        return {
            "ok": False,
            "_add_action": "no_auth",
            "_add_message": "找不到登入檔，沒有新增",
        }
    fp = _auth_fingerprint()
    email = (oauth_account_from_auth(src) or "").strip()
    email_l = email.lower()
    if prev_fingerprint and fp == prev_fingerprint:
        return {
            "ok": False,
            "_add_action": "login_unchanged",
            "_add_message": "登入沒有換帳（仍是 %s），沒有新增" % (email or "原本的帳"),
            "_add_email": email,
        }
    if not email_l:
        return {
            "ok": False,
            "_add_action": "no_email",
            "_add_message": "讀不到信箱，沒有新增",
        }
    for rec in load().get("accounts") or []:
        if not isinstance(rec, dict) or rec.get("kind") != KIND_OAUTH:
            continue
        if _listed_oauth_email(rec) == email_l:
            return {
                "ok": False,
                "_add_action": "already_listed",
                "_add_message": "這個信箱已經在名單裡",
                "_add_email": email,
                "id": rec.get("id"),
            }
    added = add_codex_oauth(label=email.split("@")[0][:12])
    try:
        _probe_and_note_rebind(str(added.get("id")))
    except Exception:
        pass
    for rec in load().get("accounts") or []:
        if isinstance(rec, dict) and rec.get("id") == added.get("id"):
            out = dict(rec)
            out["ok"] = True
            out["_add_action"] = "added"
            out["_add_message"] = "已新增：%s" % email
            out["_add_email"] = email
            return out
    added = dict(added)
    added["ok"] = True
    added["_add_action"] = "added"
    added["_add_message"] = "已新增：%s" % email
    added["_add_email"] = email
    return added



def remove_account(acct_id: str) -> bool:
    data = load()
    before = len(data.get("accounts") or [])
    data["accounts"] = [a for a in (data.get("accounts") or [])
                        if not (isinstance(a, dict) and a.get("id") == acct_id)]
    save(data)
    return len(data["accounts"]) < before


def _normalize_usage_rows(rows: list | None) -> list[dict[str, Any]]:
    """Keep only window + used_pct for the panel."""
    out: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        window = str(row.get("window") or "").strip()
        if not window:
            continue
        try:
            pct = int(row.get("used_pct"))
        except (TypeError, ValueError):
            continue
        out.append({"window": window, "used_pct": max(0, min(100, pct))})
    return out


def _probe_matches_desktop(rec: dict[str, Any]) -> bool:
    """Match by fingerprint OR same OAuth email as current desktop auth."""
    slot_fp = str(rec.get("fingerprint") or "")
    live_fp = _auth_fingerprint()
    if slot_fp and live_fp and slot_fp == live_fp:
        return True
    slot_email = str(rec.get("oauth_account") or "").strip().lower()
    if not slot_email:
        ap = str(rec.get("auth_path") or "")
        if ap:
            slot_email = (oauth_account_from_auth(ap) or "").strip().lower()
    live_email = (oauth_account_from_auth(_codex_auth_path()) or "").strip().lower()
    return bool(slot_email and live_email and slot_email == live_email)


def probe_account(acct_id: str, mode: str = "auto") -> dict[str, Any]:
    """Best-effort usage snapshot for one slot.

    mode:
      - "wham": ChatGPT wham/usage with this slot auth (only on rebind)
      - "session": desktop-match only; local Codex session (cheap; active seat)
      - "auto": desktop match → session; else keep last rebind snapshot (never wipe)

    Non-active slots must not be wham-polled every chat — that burns tokens.
    """
    data = load()
    rec = None
    for a in data.get("accounts") or []:
        if isinstance(a, dict) and a.get("id") == acct_id:
            rec = a
            break
    if rec is None:
        raise KeyError(acct_id)
    now = int(time.time())
    mode = (mode or "auto").strip().lower()
    if mode not in ("auto", "wham", "session"):
        mode = "auto"
    prev = rec.get("usage") if isinstance(rec.get("usage"), dict) else {}

    def _keep_snapshot(detail: str) -> dict[str, Any]:
        if prev.get("status") == "ok" and prev.get("rows"):
            out = dict(prev)
            out["detail"] = detail
            # 保留上次真正量到的時刻（勿被 keep 沖掉）
            if prev.get("probed_at") and not out.get("probed_at"):
                out["probed_at"] = prev.get("probed_at")
            return out
        return {
            "status": "needs_switch",
            "detail": detail,
            "rows": list(prev.get("rows") or []),
            "probed_at": prev.get("probed_at"),
        }

    usage: dict[str, Any] = {"status": "unknown", "detail": ""}
    if rec.get("kind") == KIND_OAUTH:
        auth_path = str(rec.get("auth_path") or "")
        if mode == "wham":
            try:
                from codex_wham_usage import fetch_wham_rows, enrich_with_reset_credits
                got = enrich_with_reset_credits(
                    fetch_wham_rows(auth_path, try_refresh=True), auth_path)
                if got.get("ok") and got.get("rows"):
                    usage = {
                        "status": "ok",
                        "detail": "wham",
                        "rows": _normalize_usage_rows(got.get("rows") or []),
                        "plan_type": got.get("plan_type") or "",
                        "email": got.get("email") or "",
                        "reset_credits": got.get("reset_credits")
                        or {"available_count": 0, "credits": []},
                    }
                elif got.get("revoked"):
                    usage = {
                        "status": "revoked",
                        "detail": "登入已失效，請再點 🔄",
                        "rows": list(prev.get("rows") or []),
                    }
                elif _probe_matches_desktop(rec):
                    from usage_probe import read_codex
                    rows = _normalize_usage_rows(read_codex() or [])
                    usage = {
                        "status": "ok" if rows else "empty",
                        "detail": "live-fallback",
                        "rows": rows,
                    }
                else:
                    usage = {
                        "status": "error",
                        "detail": str(got.get("error") or "wham_failed")[:120],
                        "rows": list(prev.get("rows") or []),
                    }
            except Exception as exc:  # noqa: BLE001
                usage = {
                    "status": "error",
                    "detail": str(exc)[:120],
                    "rows": list(prev.get("rows") or []),
                }
        elif mode == "session" or (mode == "auto" and _probe_matches_desktop(rec)):
            try:
                from usage_probe import read_codex
                rows = _normalize_usage_rows(read_codex() or [])
                usage = {
                    "status": "ok" if rows else "empty",
                    "detail": "live",
                    "rows": rows,
                }
                live_fp = _auth_fingerprint()
                if live_fp:
                    rec["fingerprint"] = live_fp
                if not rec.get("oauth_account"):
                    ap = auth_path or _codex_auth_path()
                    rec["oauth_account"] = oauth_account_from_auth(ap)
            except Exception as exc:  # noqa: BLE001
                usage = _keep_snapshot("live-error:%s" % str(exc)[:80])
        else:
            usage = _keep_snapshot("snapshot")
    elif rec.get("kind") == KIND_TOKEN:
        usage = {
            "status": "bound",
            "detail": "Token …%s（用量 API 下一刀）" % (rec.get("token_suffix") or "????"),
        }
    # 只有這次真的量到窗資料才刷新 probed_at；keep/error 沿用舊值
    if usage.get("status") in ("ok", "empty") and usage.get("rows") is not None:
        if usage.get("detail") in ("wham", "live", "live-fallback"):
            usage["probed_at"] = now
        elif not usage.get("probed_at") and prev.get("probed_at"):
            usage["probed_at"] = prev.get("probed_at")
    elif prev.get("probed_at") and not usage.get("probed_at"):
        usage["probed_at"] = prev.get("probed_at")
    rec["last_probe"] = now
    rec["usage"] = usage
    if usage.get("status") == "ok" and isinstance(usage.get("rows"), list) and usage["rows"]:
        capped = False
        for row in usage["rows"]:
            if not isinstance(row, dict):
                continue
            pct = row.get("used_pct")
            if isinstance(pct, int) and not isinstance(pct, bool) and pct >= 100:
                capped = True
                break
        if not capped:
            rec.pop("spoken_cap", None)
    save(data)
    return usage

def move_account(acct_id: str, direction: int) -> dict[str, Any]:
    """同 brand+kind 內上下移一格。direction: -1=上、+1=下。"""
    acct_id = str(acct_id or "").strip()
    if not acct_id:
        return {"ok": False, "reason": "missing_id"}
    try:
        direction = int(direction)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "bad_direction"}
    if direction not in (-1, 1):
        return {"ok": False, "reason": "bad_direction"}
    data = load()
    accounts = [a for a in (data.get("accounts") or []) if isinstance(a, dict)]
    idx = None
    for i, a in enumerate(accounts):
        if str(a.get("id") or "") == acct_id:
            idx = i
            break
    if idx is None:
        return {"ok": False, "reason": "not_found"}
    kind = accounts[idx].get("kind")
    brand = accounts[idx].get("brand")
    peers = [
        i for i, a in enumerate(accounts)
        if a.get("kind") == kind and a.get("brand") == brand
    ]
    pos = peers.index(idx)
    new_pos = pos + direction
    if new_pos < 0 or new_pos >= len(peers):
        return {
            "ok": False,
            "reason": "edge",
            "id": acct_id,
            "index": pos,
            "count": len(peers),
        }
    j = peers[new_pos]
    accounts[idx], accounts[j] = accounts[j], accounts[idx]
    data["accounts"] = accounts
    save(data)
    return {
        "ok": True,
        "id": acct_id,
        "from": pos,
        "to": new_pos,
        "count": len(peers),
    }


def dedupe_oauth_by_email(*, archive: bool = True) -> dict[str, Any]:
    """同一 OAuth email 只留一槽。

    保留優先序：目前作用中 → 最近啟用 → 較新建立 → 有用量資料。
    其餘預設寫入 archived_accounts（可還原），不刪磁碟 auth 檔。
    """
    data = load()
    active_id = str(data.get("active_codex_oauth_id") or "").strip()
    tokens: list[dict[str, Any]] = []
    oauth_rows: list[dict[str, Any]] = []
    for a in data.get("accounts") or []:
        if not isinstance(a, dict):
            continue
        if a.get("kind") != KIND_OAUTH:
            tokens.append(a)
            continue
        em = str(a.get("oauth_account") or "").strip().lower()
        if not em and a.get("auth_path"):
            em = (oauth_account_from_auth(str(a.get("auth_path"))) or "").strip().lower()
            if em:
                a["oauth_account"] = em
        oauth_rows.append(a)

    def _score(rec: dict[str, Any]) -> tuple:
        rid = str(rec.get("id") or "")
        usage = rec.get("usage") if isinstance(rec.get("usage"), dict) else {}
        status = str(usage.get("status") or "")
        has_usage = 1 if status in ("live", "empty", "ok") else 0
        return (
            1 if rid and rid == active_id else 0,
            int(rec.get("last_activated_at") or 0),
            int(rec.get("created_at") or 0),
            has_usage,
        )

    by_email: dict[str, list[dict[str, Any]]] = {}
    no_email: list[dict[str, Any]] = []
    for a in oauth_rows:
        em = str(a.get("oauth_account") or "").strip().lower()
        if not em:
            no_email.append(a)
            continue
        by_email.setdefault(em, []).append(a)

    keep: list[dict[str, Any]] = list(tokens)
    archived_now: list[dict[str, Any]] = []
    kept_ids: list[str] = []
    for em, rows in by_email.items():
        rows_sorted = sorted(rows, key=_score, reverse=True)
        winner = rows_sorted[0]
        keep.append(winner)
        kept_ids.append(str(winner.get("id") or ""))
        for dup in rows_sorted[1:]:
            item = dict(dup)
            item["archived_at"] = int(time.time())
            item["archive_reason"] = "dedupe_oauth_by_email"
            item["archive_kept_id"] = str(winner.get("id") or "")
            archived_now.append(item)
    keep.extend(no_email)

    order = {
        str(a.get("id")): i
        for i, a in enumerate(data.get("accounts") or [])
        if isinstance(a, dict)
    }
    keep.sort(key=lambda a: order.get(str(a.get("id") or ""), 10**9))

    if archive and archived_now:
        prev = data.get("archived_accounts")
        if not isinstance(prev, list):
            prev = []
        drop = {str(x.get("id") or "") for x in archived_now}
        prev = [
            x for x in prev
            if not (isinstance(x, dict) and str(x.get("id") or "") in drop)
        ]
        data["archived_accounts"] = prev + archived_now

    data["accounts"] = keep
    keep_ids_set = {str(a.get("id") or "") for a in keep}
    if active_id and active_id not in keep_ids_set:
        for item in archived_now:
            if str(item.get("id") or "") == active_id:
                kid = str(item.get("archive_kept_id") or "")
                if kid:
                    data["active_codex_oauth_id"] = kid
                break
    save(data)
    return {
        "ok": True,
        "kept": len(keep),
        "kept_ids": kept_ids,
        "archived": [str(x.get("id") or "") for x in archived_now],
        "emails": sorted(by_email.keys()),
        "archive": bool(archive),
    }


def panel_payload() -> dict[str, Any]:
    """Shape consumed by the usage renderer."""
    data = load()
    accounts = []
    backfilled = False
    kind_seq = {}
    import usage_failover as _uf
    for rec in list_accounts(BRAND_CODEX):
        u = rec.get("usage") if isinstance(rec.get("usage"), dict) else {}
        raw_label = str(rec.get("label") or rec.get("id") or "")
        for prefix in ("oauth-", "token-"):
            if raw_label.startswith(prefix):
                raw_label = raw_label[len(prefix):]
                break
        oauth_acct = str(rec.get("oauth_account") or "").strip()
        if not oauth_acct and rec.get("kind") == KIND_OAUTH and rec.get("auth_path"):
            oauth_acct = oauth_account_from_auth(str(rec.get("auth_path")))
            if oauth_acct:
                rec["oauth_account"] = oauth_acct
                backfilled = True
        live = (u.get("detail") == "live") or (
            u.get("status") == "ok" and bool(u.get("rows"))
        )
        kind = str(rec.get("kind") or "")
        kind_seq[kind] = kind_seq.get(kind, 0) + 1
        seq = kind_seq[kind]
        if kind == KIND_OAUTH:
            slot_mark = _uf.login_mark(rec)
            slot_glyph = _uf.login_glyph(slot_mark)
        else:
            slot_mark = ""
            slot_glyph = ""
        # the maintainer 09-22: 已有列只刷新時間／狀態 — 取 rebound／probe 較新者
        bound_ts = 0
        for key in ("rebound_at", "created_at"):
            try:
                bound_ts = max(bound_ts, int(rec.get(key) or 0))
            except (TypeError, ValueError):
                pass
        try:
            bound_ts = max(bound_ts, int(rec.get("last_probe") or 0))
        except (TypeError, ValueError):
            pass
        try:
            bound_ts = max(bound_ts, int(u.get("probed_at") or 0))
        except (TypeError, ValueError):
            pass
        accounts.append({
            "id": rec.get("id"),
            "kind": rec.get("kind"),
            "label": raw_label,
            "seq": seq,
            "seq_label": "%03d" % seq,
            "status": "live" if live else "idle",
            "status_label": ("可用" if live else ("快照" if (isinstance(u, dict) and u.get("status") == "ok" and u.get("rows")) else ("需重登" if (isinstance(u, dict) and u.get("status") == "revoked") else "失效"))),
            "detail": u.get("detail") or ("live" if live else "idle"),
            "rows": u.get("rows") or [],
            "plan_type": str(u.get("plan_type") or "").strip(),
            "reset_credits": (u.get("reset_credits")
                              if isinstance(u.get("reset_credits"), dict)
                              else {"available_count": 0, "credits": []}),
            "bound": live,
            "login_mark": slot_mark,
            "login_glyph": slot_glyph,
            "oauth_account": oauth_acct,
            "last_bound_at": bound_ts,
            "last_bound_label": format_last_bound_label(bound_ts),
            "last_probe": int(rec.get("last_probe") or 0) or None,
            "probed_at": (int(u.get("probed_at") or 0) or int(rec.get("last_probe") or 0) or None),
            "probed_ago": format_probed_ago(
                u.get("probed_at") or rec.get("last_probe")),
            "observed_age_s": (lambda _ts: (
                (int(__import__("time").time()) - int(_ts))
                if _ts else None))(u.get("probed_at") or rec.get("last_probe")),
            "can_move_up": seq > 1,
            "can_move_down": False,  # filled below
        })
    # can_move_down from kind totals
    totals = dict(kind_seq)
    for a in accounts:
        k = str(a.get("kind") or "")
        a["can_move_down"] = int(a.get("seq") or 0) < int(totals.get(k) or 0)
    if backfilled:
        save(data)
    failover = data.get("failover") if isinstance(data.get("failover"), dict) else {}
    _sub = {}
    if _usage_subtabs is not None:
        try:
            _usage_subtabs.ensure_store_defaults(data)
            _sub = _usage_subtabs.panel_subtab_fields(data) or {}
        except Exception:
            _sub = {}
    preview = os.environ.get("EZ2_USAGE_SUBTAB_PREVIEW") or ""
    if preview in ("usage", "codex", "用量"):
        _sub = dict(_sub)
        _sub["subtab"] = "usage" if preview == "用量" else preview
    return {
        "unlocked": bool(data.get("unlocked")),
        "subtab": _sub.get("subtab", data.get("subtab") or "usage"),
        "failover_enabled": bool(_sub.get("failover_enabled", False)),
        "subtabs": _sub.get("subtabs") or [],
        "brand": BRAND_CODEX,
        "accounts": accounts,
        "active_codex_oauth_id": data.get("active_codex_oauth_id") or "",
        "failover_auto_enabled": bool(failover.get("auto_enabled")),
        "hint": "[X]刪除｜🚀 切成主用｜🔀 自動接續 🔄 重新登入｜👆👇 調整順序",
    }



def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="usage_accounts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_tap = sub.add_parser("tap")
    p_tap.add_argument("--now", type=float, default=None)
    sub.add_parser("unlock")
    sub.add_parser("lock")
    p_st = sub.add_parser("subtab")
    p_st.add_argument("name")
    sub.add_parser("list")
    p_oauth = sub.add_parser("add-oauth")
    p_oauth.add_argument("--label", default="")
    p_tok = sub.add_parser("add-token")
    p_tok.add_argument("--token", required=True)
    p_tok.add_argument("--label", default="")
    p_rm = sub.add_parser("remove")
    p_rm.add_argument("id")
    p_rb = sub.add_parser("rebind")
    p_rb.add_argument("id", nargs="?", default=None)
    p_pr = sub.add_parser("probe-matching", help="probe OAuth slots matching desktop")
    sub.add_parser("probe")
    p_pr.add_argument("id")
    p_mu = sub.add_parser("move-up")
    p_mu.add_argument("id")
    p_md = sub.add_parser("move-down")
    p_md.add_argument("id")
    sub.add_parser("login-start")
    p_lc = sub.add_parser("login-cancel")
    p_lc.add_argument("pid", type=int)
    p_lc.add_argument("--proxy-pid", type=int, default=0)
    p_lw = sub.add_parser("login-wait")
    p_lw.add_argument("pid", type=int)
    p_lw.add_argument("--prev-fp", default="")
    p_lw.add_argument("--timeout", type=float, default=300.0)
    p_ral = sub.add_parser("rebind-after-login")
    p_ral.add_argument("id", nargs="?", default=None)
    p_ral.add_argument("--prev-fp", default="")
    p_new = sub.add_parser("add-new-after-login")
    p_new.add_argument("--prev-fp", default="")
    p_dd = sub.add_parser("dedupe")
    p_dd.add_argument("--hard", action="store_true", help="刪除重複而不封存")
    p_act = sub.add_parser("activate")
    p_act.add_argument("id")
    p_an = sub.add_parser("activate-next")
    p_an.add_argument("--after", default=None)
    sub.add_parser("panel-json")
    args = ap.parse_args(argv)
    if args.cmd == "tap":
        print(json.dumps(record_header_tap(args.now), ensure_ascii=False))
        return 0
    if args.cmd == "unlock":
        set_unlocked(True); print("unlocked"); return 0
    if args.cmd == "subtab":
        return _cli_subtab([args.name])
    if args.cmd == "lock":
        set_unlocked(False); print("locked"); return 0
    if args.cmd == "list":
        print(json.dumps(list_accounts(), ensure_ascii=False, indent=2)); return 0
    if args.cmd == "add-oauth":
        print(json.dumps(add_codex_oauth(args.label), ensure_ascii=False, indent=2)); return 0
    if args.cmd == "add-token":
        print(json.dumps(add_codex_token(args.token, args.label), ensure_ascii=False, indent=2)); return 0
    if args.cmd == "remove":
        print("ok" if remove_account(args.id) else "missing"); return 0
    if args.cmd == "login-start":
        print(json.dumps(start_codex_device_login(), ensure_ascii=False, indent=2)); return 0
    if args.cmd == "login-cancel":
        print(json.dumps(cancel_codex_device_login(args.pid, getattr(args, "proxy_pid", 0) or 0), ensure_ascii=False, indent=2)); return 0
    if args.cmd == "login-wait":
        print(json.dumps(wait_codex_device_login(args.pid, args.prev_fp, args.timeout), ensure_ascii=False, indent=2)); return 0
    if args.cmd == "rebind-after-login":
        print(json.dumps(rebind_after_device_login(args.id, args.prev_fp), ensure_ascii=False, indent=2)); return 0
    if args.cmd == "add-new-after-login":
        print(json.dumps(add_oauth_if_new(args.prev_fp), ensure_ascii=False, indent=2)); return 0
    if args.cmd == "rebind":
        print(json.dumps(rebind_codex_oauth(args.id), ensure_ascii=False, indent=2)); return 0
    if args.cmd == "probe-matching":
        out = []
        for rec in list_accounts(BRAND_CODEX):
            if str(rec.get("kind") or "") != KIND_OAUTH:
                continue
            if not _probe_matches_desktop(rec):
                continue
            aid = str(rec.get("id") or "")
            out.append({"id": aid, "usage": probe_account(aid)})
        print(json.dumps({"probed": len(out), "accounts": out}, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "probe":
        print(json.dumps(probe_account(args.id), ensure_ascii=False, indent=2)); return 0
    if args.cmd == "move-up":
        print(json.dumps(move_account(args.id, -1), ensure_ascii=False, indent=2)); return 0
    if args.cmd == "move-down":
        print(json.dumps(move_account(args.id, 1), ensure_ascii=False, indent=2)); return 0
    if args.cmd == "dedupe":
        hard = bool(getattr(args, "hard", False))
        print(json.dumps(dedupe_oauth_by_email(archive=not hard), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "activate":
        from usage_failover import activate_codex_oauth_slot
        print(json.dumps(activate_codex_oauth_slot(args.id), ensure_ascii=False, indent=2)); return 0
    if args.cmd == "activate-next":
        from usage_failover import activate_next_codex_oauth
        print(json.dumps(activate_next_codex_oauth(after_id=args.after), ensure_ascii=False, indent=2)); return 0
    if args.cmd == "panel-json":
        print(json.dumps(panel_payload(), ensure_ascii=False, indent=2)); return 0
    return 2


def _cli_subtab(argv=None):
    """usage_accounts.py subtab usage|codex — set store subtab via load/save."""
    import argparse as _ap
    import json as _json

    p = _ap.ArgumentParser(prog="usage_accounts subtab")
    p.add_argument("subtab", choices=["usage", "codex", "用量"])
    ns = p.parse_args(argv)
    if _usage_subtabs is None:
        raise SystemExit("usage_subtabs missing")
    doc = load()
    _usage_subtabs.ensure_store_defaults(doc)
    _usage_subtabs.set_subtab(doc, ns.subtab)
    # 右欄外層每 0.1 秒只讀這一行。帳號大檔一變它不會醒。先寫這行，存檔可以晚一點。
    try:
        side = os.path.join(os.path.dirname(_state_path()), "usage-subtab")
        tmp = side + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(str(doc.get("subtab") or "usage") + "\n")
        os.replace(tmp, side)
    except OSError:
        pass
    save(doc)
    print(_json.dumps(_usage_subtabs.panel_subtab_fields(doc), ensure_ascii=False))
    return 0


def is_failover_enabled(doc=None):
    """PLAN failover B gate — True only when usage-accounts unlocked."""
    if doc is None:
        try:
            doc = load()
        except Exception:
            doc = {}
    if _usage_subtabs is None:
        return bool((doc or {}).get("unlocked"))
    return _usage_subtabs.failover_enabled((doc or {}).get("unlocked"))


def refuse_auto_failover_if_locked(doc=None, reason="auto-switch"):
    """Call before activate-next / auto-switch. Raises RuntimeError if locked."""
    if doc is None:
        try:
            doc = load()
        except Exception:
            doc = {}
    if _usage_subtabs is None:
        if not bool((doc or {}).get("unlocked")):
            raise RuntimeError("failover B refused: usage-accounts locked (%s)" % reason)
        return
    ok, msg = _usage_subtabs.guard_auto_failover(
        unlocked=bool((doc or {}).get("unlocked")), reason=reason
    )
    if not ok:
        raise RuntimeError(msg or ("failover B refused: %s" % reason))



if __name__ == "__main__":
    raise SystemExit(main())
