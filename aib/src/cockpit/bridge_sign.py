# SPDX-License-Identifier: Apache-2.0
"""檔案橋寄件來源簽章(2026-10-27,Andy 令「檔案橋匯入的信要能辨識寄件人」;設計稿 SPEC-aib-chat-mcp-1027 §17)。

誰簽:musebot_outbox_drainer。它不信檔內 from,寄件席由「信在哪一席的 bridge/<session>/outbox」推得
(status.json 的 cabin/code,橋自己寫的),送達後在 delivered/ 那份信加保留欄位 `_aib_sig`。
誰驗:chat_mcp 匯入收件匣時,拿 dm 抬頭的寄件席 + 正文,對上一筆驗得過的 `_aib_sig` 才認寄件席;否則 bridge:unknown。

簽的內容(HMAC-SHA256,本機密鑰):版本、寄件席、收件席、message_id、正文 sha256、時間(秒)。
密鑰:<state root>/secrets/bridge-sign.key(資料夾 0700、檔 0600,首次由簽章端自動產生);
環境變數 AIB_BRIDGE_SIGN_KEY_FILE 可覆寫路徑。驗章端只讀不產;權限比 0600 寬就拒用(簽、驗都不做)。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import stat
from pathlib import Path

SIG_FIELD = "_aib_sig"
SIG_VERSION = "aib-bridge-sig/v1"
KEY_ENV = "AIB_BRIDGE_SIGN_KEY_FILE"
MAX_SKEW_SECS = 600  # 簽章時間與收件匣信的時間最多差 10 分鐘(含 drainer 重試)


def key_path(state_root=None) -> Path:
    env = (os.environ.get(KEY_ENV) or "").strip()
    if env:
        return Path(env).expanduser()
    base = state_root or os.environ.get("EZ2_AIB_STATE_ROOT") or os.environ.get("EZ2_AIB_HOME") or str(Path.home() / ".ez2-aib")
    return Path(base).expanduser() / "secrets" / "bridge-sign.key"


def _perm_ok(p: Path) -> bool:
    try:
        st = p.stat()
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and (st.st_mode & 0o077) == 0 and st.st_uid == os.getuid()


def load_key(state_root=None, create=False):
    """回金鑰 bytes;不在(且不建)或權限太寬回 None。create=True 只給簽章端用。"""
    p = key_path(state_root)
    if not p.exists():
        if not create:
            return None
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(p.parent, 0o700)
        except OSError:
            return None
        try:
            fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        except OSError:
            return None
        else:
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_hex(32) + "\n")
    if not _perm_ok(p):
        return None
    try:
        raw = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return raw.encode() if len(raw) >= 32 else None


def body_hash(body: str) -> str:
    return hashlib.sha256((body or "").strip().encode("utf-8")).hexdigest()


def _mac(key: bytes, from_seat, to_seat, message_id, bhash, ts) -> str:
    msg = "\n".join((SIG_VERSION, str(from_seat), str(to_seat), str(message_id), str(bhash), str(int(ts))))
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).hexdigest()


def make_sig(key: bytes, from_seat: str, to_seat: str, message_id: str, body: str, ts) -> dict:
    bh = body_hash(body)
    return {"v": SIG_VERSION, "from_seat": from_seat, "to_seat": to_seat, "message_id": str(message_id),
            "body_sha256": bh, "ts": int(ts), "hmac": _mac(key, from_seat, to_seat, message_id, bh, ts)}


def verify_sig(key, rec) -> bool:
    """常數時間比較;任何欄位缺/型別錯一律 False。"""
    if not key or not isinstance(rec, dict) or rec.get("v") != SIG_VERSION:
        return False
    try:
        want = _mac(key, rec["from_seat"], rec["to_seat"], rec["message_id"], rec["body_sha256"], int(rec["ts"]))
        got = rec["hmac"]
    except (KeyError, TypeError, ValueError):
        return False
    return isinstance(got, str) and hmac.compare_digest(want, got)
