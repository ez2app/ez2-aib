# SPDX-License-Identifier: Apache-2.0
"""AIB 對話 MCP 伺服器 P0 核心(設計稿 SPEC-aib-chat-mcp-1027 v4 §15/§16)。

- 儲存:SQLite(獨立檔,0600);訊息保存 90 天;稽核不存本文。
- 認證抽象層:Authenticator 介面;P0 只有 BearerAuth(本機測試金鑰,只存 sha256 雜湊,90 天到期,可撤銷);
  OAuthAuth 留介面,P0 一律 UNAUTHENTICATED。
- binding:principal ↔ 固定 seat,收件 allowlist、scopes、revision、state(active|revoked),每請求重查(不快取)。
- 訊息:伺服器產 ULID message_id;唯一鍵 principal+binding+idempotency_key;寄件人一律由 binding 導出(無 from 輸入)。
  狀態 accepted→queued→dispatched→received→processed|rejected|failed;ack 帶 expected_revision。
- 檔案橋 adapter:**只讀**掃 bridge/<session>/inbox 與 inbox/claimed(不 rename、不呼叫 mailbox.pull);
  送出預設不碰檔案橋(旗標 AIB_CHAT_MCP_BRIDGE_DISPATCH=1 才寫寄件匣,格式同 aib-sand-bridge-msg/v1)。
- 無 shell、無任意路徑、無廣播工具。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path

API_VERSION = "aib-chat-mcp/0.1-p0"
ADAPTER_VERSION = "bridge-ro/1"
RETENTION_SECS = 90 * 86400
KEY_TTL_SECS = 90 * 86400
SEND_PER_MIN = 20
READ_PER_MIN = 120
WAIT_CONCURRENT = 2
WAIT_MAX_SECS = 25
HOP_MAX = 4
BODY_MAX = 8 * 1024
LIMIT_DEFAULT, LIMIT_MAX = 20, 100
SCOPES = ("aib:identity:read", "aib:seats:read", "aib:messages:read",
          "aib:messages:send", "aib:messages:ack", "aib:events:subscribe")
SEAT_RE = __import__("re").compile(r"[0-9a-z]{4}/[0-9a-z]{5}")
ERROR_CODES = ("UNAUTHENTICATED", "FORBIDDEN", "NOT_FOUND", "INVALID_ARGUMENT",
               "CONFLICT", "RATE_LIMITED", "UNAVAILABLE")


class ToolError(Exception):
    def __init__(self, code, message, retryable=False, retry_after=None):
        assert code in ERROR_CODES
        super().__init__(message)
        self.code, self.message, self.retryable, self.retry_after = code, message, retryable, retry_after


def now_rfc3339(t=None):
    t = time.time() if t is None else t
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".%03dZ" % int((t % 1) * 1000)


_CROCK = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ulid_lock = threading.Lock()
_ulid_last = [0, 0]


def new_ulid():
    """單調 ULID(同毫秒內遞增)。"""
    with _ulid_lock:
        ms = int(time.time() * 1000)
        if ms <= _ulid_last[0]:
            ms = _ulid_last[0]
            rnd = _ulid_last[1] + 1
        else:
            rnd = int.from_bytes(os.urandom(10), "big")
        _ulid_last[:] = [ms, rnd]
    n = (ms << 80) | (rnd & ((1 << 80) - 1))
    return "".join(_CROCK[(n >> (5 * i)) & 31] for i in reversed(range(26)))


def home_dir():
    return Path(os.environ.get("AIB_CHAT_MCP_HOME") or (Path.home() / ".ez2-aib" / "chat-mcp"))


def bridge_root():
    return Path(os.environ.get("AIB_CHAT_MCP_BRIDGE_ROOT") or (Path.home() / ".ez2-aib" / "bridge"))


def _sha(s):
    return hashlib.sha256(s.encode() if isinstance(s, str) else s).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS keys(key_hash TEXT PRIMARY KEY, principal_id TEXT NOT NULL, created REAL, expires REAL, revoked REAL);
CREATE TABLE IF NOT EXISTS bindings(binding_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL, seat TEXT NOT NULL,
  session_uuid TEXT, allowlist TEXT NOT NULL, scopes TEXT NOT NULL, revision INTEGER NOT NULL, state TEXT NOT NULL,
  evidence TEXT, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS messages(message_id TEXT PRIMARY KEY, from_seat TEXT, to_seat TEXT NOT NULL,
  principal_id TEXT, binding_id TEXT, idem_key TEXT, body_hash TEXT, body TEXT, thread_id TEXT, correlation_id TEXT,
  causation_id TEXT, origin TEXT, hop_count INTEGER, state TEXT NOT NULL, revision INTEGER NOT NULL, source TEXT NOT NULL,
  src_ref TEXT, duplicate_possible INTEGER DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL,
  UNIQUE(principal_id, binding_id, idem_key), UNIQUE(src_ref));
CREATE INDEX IF NOT EXISTS ix_to ON messages(to_seat, created, message_id);
CREATE INDEX IF NOT EXISTS ix_from ON messages(from_seat, created, message_id);
CREATE TABLE IF NOT EXISTS audit(ts REAL, request_id TEXT, principal_id TEXT, tool TEXT, outcome TEXT, message_id TEXT);
"""


class Store:
    def __init__(self, home=None):
        self.home = Path(home) if home else home_dir()
        self.home.mkdir(parents=True, exist_ok=True)
        os.chmod(self.home, 0o700)
        self.path = self.home / "chat.db"
        new = not self.path.exists()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        if new:
            os.chmod(self.path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()
        sp = self.home / "cursor.secret"
        if not sp.exists():
            fd = os.open(str(sp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.write(fd, secrets.token_bytes(32))
            os.close(fd)
        self.cursor_secret = sp.read_bytes()
        self.purge()

    def q(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def purge(self):
        cut = time.time() - RETENTION_SECS
        with self.lock:
            self.db.execute("DELETE FROM messages WHERE created < ?", (cut,))
            self.db.execute("DELETE FROM audit WHERE ts < ?", (cut,))

    def audit(self, request_id, principal, tool, outcome, message_id=None):
        # 稽核只記誰、哪個工具、結果、訊息編號;不存本文
        with self.lock:
            self.db.execute("INSERT INTO audit VALUES(?,?,?,?,?,?)",
                            (time.time(), request_id, principal, tool, outcome, message_id))

    # ---- 金鑰與 binding(只由本機 CLI 呼叫,不開成 MCP 工具) ----
    def new_key(self, principal_id, ttl=KEY_TTL_SECS):
        raw = "aibk_" + secrets.token_urlsafe(32)
        t = time.time()
        with self.lock:
            self.db.execute("INSERT INTO keys VALUES(?,?,?,?,NULL)", (_sha(raw), principal_id, t, t + ttl))
        return raw

    def revoke_keys(self, principal_id):
        with self.lock:
            return self.db.execute("UPDATE keys SET revoked=? WHERE principal_id=? AND revoked IS NULL",
                                   (time.time(), principal_id)).rowcount

    def bind(self, principal_id, seat, allowlist=(), scopes=SCOPES, session_uuid=None, evidence=""):
        if not SEAT_RE.fullmatch(seat) or any(not SEAT_RE.fullmatch(a) for a in allowlist):
            raise ValueError("seat 必須是 艙位碼/席碼")
        bad = [s for s in scopes if s not in SCOPES]
        if bad:
            raise ValueError("未知 scope: %s" % bad)
        t = time.time()
        with self.lock:
            if self.q("SELECT 1 FROM bindings WHERE principal_id=? AND state='active'", (principal_id,)):
                raise ValueError("此 principal 已有有效 binding;先 revoke")
            bid = "bnd_" + new_ulid()
            self.db.execute("INSERT INTO bindings VALUES(?,?,?,?,?,?,1,'active',?,?,?)",
                            (bid, principal_id, seat, session_uuid, json.dumps(sorted(set(allowlist))),
                             json.dumps(list(scopes)), evidence, t, t))
        return bid

    def revoke_binding(self, binding_id):
        with self.lock:
            return self.db.execute("UPDATE bindings SET state='revoked', revision=revision+1, updated=? "
                                   "WHERE binding_id=? AND state='active'", (time.time(), binding_id)).rowcount

    def active_binding(self, principal_id):
        rows = self.q("SELECT binding_id,principal_id,seat,session_uuid,allowlist,scopes,revision FROM bindings "
                      "WHERE principal_id=? AND state='active'", (principal_id,))
        if not rows:
            return None
        r = rows[0]
        return {"binding_id": r[0], "principal_id": r[1], "seat": r[2], "session_uuid": r[3],
                "allowlist": json.loads(r[4]), "scopes": json.loads(r[5]), "revision": r[6]}


# ---------------- 認證抽象層 ----------------
class Authenticator:
    def authenticate(self, authorization_header):  # -> principal_id or raise ToolError
        raise NotImplementedError


class BearerAuth(Authenticator):
    def __init__(self, store):
        self.store = store

    def authenticate(self, header):
        if not header or not header.startswith("Bearer "):
            raise ToolError("UNAUTHENTICATED", "missing bearer token")
        rows = self.store.q("SELECT principal_id, expires, revoked FROM keys WHERE key_hash=?",
                            (_sha(header[7:].strip()),))
        if not rows or rows[0][2] is not None or rows[0][1] < time.time():
            raise ToolError("UNAUTHENTICATED", "invalid or expired token")
        return rows[0][0]


class OAuthAuth(Authenticator):
    """P2 介面:OAuth 2.1 + PKCE S256 + resource/audience 驗證之 access token → principal。P0 未實作。"""

    def authenticate(self, header):
        raise ToolError("UNAUTHENTICATED", "oauth not available in P0")


class ChainAuth(Authenticator):
    def __init__(self, *auths):
        self.auths = auths

    def authenticate(self, header):
        last = ToolError("UNAUTHENTICATED", "unauthenticated")
        for a in self.auths:
            try:
                return a.authenticate(header)
            except ToolError as e:
                last = e
        raise last


# ---------------- 限流 ----------------
class RateLimiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.hits = {}
        self.waits = {}

    def hit(self, principal, kind, limit):
        t = time.time()
        with self.lock:
            arr = [x for x in self.hits.get((principal, kind), []) if t - x < 60]
            if len(arr) >= limit:
                self.hits[(principal, kind)] = arr
                raise ToolError("RATE_LIMITED", "rate limit exceeded", True, max(1, int(60 - (t - arr[0])) + 1))
            arr.append(t)
            self.hits[(principal, kind)] = arr

    def wait_enter(self, principal):
        with self.lock:
            if self.waits.get(principal, 0) >= WAIT_CONCURRENT:
                raise ToolError("RATE_LIMITED", "too many concurrent waits", True, 5)
            self.waits[principal] = self.waits.get(principal, 0) + 1

    def wait_exit(self, principal):
        with self.lock:
            self.waits[principal] = max(0, self.waits.get(principal, 1) - 1)


# ---------------- 檔案橋 adapter(只讀 + 選用寄出) ----------------
class BridgeAdapter:
    def __init__(self, store, root=None):
        self.store, self.root = store, Path(root) if root else bridge_root()

    def _session_dir(self, session_uuid):
        if not session_uuid or not __import__("re").fullmatch(r"[0-9a-f-]{36}", session_uuid):
            return None
        d = self.root / session_uuid
        return d if d.is_dir() else None

    def sync_inbox(self, binding):
        """把該席檔案橋收件匣(含 claimed/)的信只讀匯入;不 rename、不刪、不呼叫 mailbox.pull。"""
        d = self._session_dir(binding.get("session_uuid"))
        if not d:
            return 0
        n = 0
        for sub in (d / "inbox", d / "inbox" / "claimed"):
            if not sub.is_dir():
                continue
            for f in sorted(sub.glob("*.json")):
                try:
                    raw = f.read_bytes()
                    msg = json.loads(raw)
                except (OSError, ValueError):
                    continue
                text = msg.get("text") if isinstance(msg, dict) else None
                if not isinstance(text, str) or not text:
                    continue
                ref = "bridge:" + _sha(binding["session_uuid"] + "\0" + f.name + "\0" + _sha(raw))
                t = float(msg.get("created_epoch") or f.stat().st_mtime)
                with self.store.lock:
                    try:
                        self.store.db.execute(
                            "INSERT INTO messages(message_id,from_seat,to_seat,body_hash,body,origin,hop_count,state,"
                            "revision,source,src_ref,duplicate_possible,created,updated) "
                            "VALUES(?,?,?,?,?,?,0,'received',1,'bridge',?,1,?,?)",
                            (new_ulid(), "bridge:" + str(msg.get("from") or "unknown")[:40], binding["seat"],
                             _sha(text), text[:64 * 1024], "file-bridge", ref, t, time.time()))
                        n += 1
                    except sqlite3.IntegrityError:
                        pass
        return n

    def dispatch(self, binding, message_id, to_seat, body):
        """旗標開才寫寄件匣(由既有 drainer 送);預設不碰檔案橋。回傳是否已交出。"""
        if os.environ.get("AIB_CHAT_MCP_BRIDGE_DISPATCH") != "1":
            return False
        d = self._session_dir(binding.get("session_uuid"))
        if not d:
            return False
        out = d / "outbox"
        out.mkdir(exist_ok=True)
        payload = {"schema": "aib-sand-bridge-msg/v1", "provider": "musebot", "from": "aib-chat-mcp",
                   "to": to_seat, "text": body, "message_id": message_id, "created_epoch": int(time.time())}
        tmp = out / (".%s.tmp" % message_id)
        tmp.write_text(json.dumps(payload, ensure_ascii=False))
        os.replace(tmp, out / ("%d-%s.json" % (int(time.time() * 1000), message_id)))
        return True
