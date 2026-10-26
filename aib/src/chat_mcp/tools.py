# SPDX-License-Identifier: Apache-2.0
"""AIB 對話 MCP P0 工具(嚴格 schema、拒未知欄位;每次呼叫重查 binding)。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid

from . import core
from .core import ToolError

S = {"type": "string"}
I = {"type": "integer"}


def _obj(props=None, required=()):
    return {"type": "object", "properties": props or {}, "required": list(required), "additionalProperties": False}


TOOLS = {
    "aib_health": (_obj(), None, "伺服器健康狀態"),
    "aib_whoami": (_obj(), "aib:identity:read", "目前認證身分與 binding"),
    "aib_get_status": (_obj({"seat_id": S}), "aib:seats:read", "席位狀態(預設自身)"),
    "aib_list_seats": (_obj({"cursor": S, "limit": I}), "aib:seats:read", "可見席位"),
    "aib_list_messages": (_obj({"mailbox": {"type": "string", "enum": ["inbox", "outbox"]}, "cursor": S, "limit": I},
                               ["mailbox"]), "aib:messages:read", "列信(純讀,不 claim、不 ack)"),
    "aib_get_message": (_obj({"message_id": S}, ["message_id"]), "aib:messages:read", "讀一則"),
    "aib_send_message": (_obj({"to_seat": S, "body": S, "idempotency_key": S, "thread_id": S,
                               "correlation_id": S, "causation_id": S}, ["to_seat", "body", "idempotency_key"]),
                         "aib:messages:send", "寄信(寄件人由 binding 導出)"),
    "aib_ack_message": (_obj({"message_id": S, "expected_revision": I,
                              "ack_state": {"type": "string", "enum": ["processed", "rejected"]}},
                             ["message_id", "expected_revision", "ack_state"]), "aib:messages:ack", "確認處理"),
    "aib_wait_messages": (_obj({"cursor": S, "timeout_secs": I}), "aib:messages:read", "長輪詢收件匣(≤25 秒)"),
}


def _validate(name, args):
    schema = TOOLS[name][0]
    if not isinstance(args, dict):
        raise ToolError("INVALID_ARGUMENT", "arguments must be an object")
    extra = set(args) - set(schema["properties"])
    if extra:
        raise ToolError("INVALID_ARGUMENT", "unknown field(s): %s" % ",".join(sorted(extra)))
    for k in schema["required"]:
        if k not in args:
            raise ToolError("INVALID_ARGUMENT", "missing field: %s" % k)
    for k, v in args.items():
        p = schema["properties"][k]
        if p["type"] == "string" and not isinstance(v, str):
            raise ToolError("INVALID_ARGUMENT", "%s must be string" % k)
        if p["type"] == "integer" and (not isinstance(v, int) or isinstance(v, bool)):
            raise ToolError("INVALID_ARGUMENT", "%s must be integer" % k)
        if "enum" in p and v not in p["enum"]:
            raise ToolError("INVALID_ARGUMENT", "%s not allowed" % k)


def _msg_row(r, full=False):
    keys = ("message_id", "from_seat", "to_seat", "thread_id", "correlation_id", "causation_id", "origin",
            "hop_count", "state", "revision", "source", "duplicate_possible", "created", "updated", "body")
    d = dict(zip(keys, r))
    body = d.pop("body") or ""
    d["duplicate_possible"] = bool(d["duplicate_possible"])
    d["created_at"], d["updated_at"] = core.now_rfc3339(d.pop("created")), core.now_rfc3339(d.pop("updated"))
    if full:
        d["body"] = body
    else:
        d["summary"] = body[:120]
    return d


COLS = ("message_id,from_seat,to_seat,thread_id,correlation_id,causation_id,origin,hop_count,state,revision,"
        "source,duplicate_possible,created,updated,body")


class Tools:
    def __init__(self, store, limiter=None, adapter=None, flags=None):
        self.store = store
        self.limiter = limiter or core.RateLimiter()
        self.adapter = adapter or core.BridgeAdapter(store)
        self.flags = {"send": True, "ack": True}
        self.flags.update(flags or {})

    # ---- cursor:HMAC 簽章、綁 principal + 查詢條件 ----
    def _cursor_make(self, principal, query, created, mid):
        body = json.dumps({"p": principal, "q": query, "c": created, "m": mid}, separators=(",", ":")).encode()
        sig = hmac.new(self.store.cursor_secret, body, hashlib.sha256).digest()[:16]
        return base64.urlsafe_b64encode(body + sig).decode().rstrip("=")

    def _cursor_read(self, principal, query, cur):
        if not cur:
            return (0.0, "")
        try:
            raw = base64.urlsafe_b64decode(cur + "=" * (-len(cur) % 4))
            body, sig = raw[:-16], raw[-16:]
            ok = hmac.compare_digest(hmac.new(self.store.cursor_secret, body, hashlib.sha256).digest()[:16], sig)
            d = json.loads(body) if ok else None
        except (ValueError, TypeError):
            d = None
        if not d or d.get("p") != principal or d.get("q") != query:
            raise ToolError("INVALID_ARGUMENT", "invalid cursor")
        return (float(d["c"]), str(d["m"]))

    def _limit(self, args):
        lim = args.get("limit", core.LIMIT_DEFAULT)
        if lim < 1 or lim > core.LIMIT_MAX:
            raise ToolError("INVALID_ARGUMENT", "limit must be 1..%d" % core.LIMIT_MAX)
        return lim

    def call(self, principal, name, args, request_id=None):
        request_id = request_id or "req_" + uuid.uuid4().hex[:16]
        if name not in TOOLS:
            raise ToolError("NOT_FOUND", "unknown tool")
        outcome, mid = "ok", None
        try:
            _validate(name, args)
            binding = self.store.active_binding(principal) if name != "aib_health" else None
            if name != "aib_health":
                if not binding:
                    raise ToolError("FORBIDDEN", "no active binding")
                if TOOLS[name][1] not in binding["scopes"]:
                    raise ToolError("FORBIDDEN", "scope not granted")
                kind = "send" if name in ("aib_send_message", "aib_ack_message") else "read"
                self.limiter.hit(principal, kind, core.SEND_PER_MIN if kind == "send" else core.READ_PER_MIN)
            data = getattr(self, "_" + name[4:])(principal, binding, args)
            mid = data.get("message_id") if isinstance(data, dict) else None
            return {"request_id": request_id, "data": data, "observed_at": core.now_rfc3339()}
        except ToolError as e:
            outcome = e.code
            raise
        finally:
            self.store.audit(request_id, principal, name, outcome, mid)

    # ---- 工具實作 ----
    def _health(self, p, b, a):
        return {"api_version": core.API_VERSION, "adapter_version": core.ADAPTER_VERSION, "status": "ok",
                "server_time": core.now_rfc3339()}

    def _whoami(self, p, b, a):
        return {"principal_id": p, "binding_id": b["binding_id"], "seat_id": b["seat"],
                "binding_revision": b["revision"], "granted_scopes": b["scopes"]}

    def _visible_seats(self, b):
        return sorted(set([b["seat"]] + list(b["allowlist"])))

    def _get_status(self, p, b, a):
        seat = a.get("seat_id", b["seat"])
        if seat not in self._visible_seats(b):
            raise ToolError("NOT_FOUND", "not found")
        rows = self.store.q("SELECT revision, updated FROM bindings WHERE seat=? AND state='active'", (seat,))
        pending = self.store.q("SELECT COUNT(*) FROM messages WHERE to_seat=? AND state IN ('queued','received')",
                               (seat,))[0][0] if seat == b["seat"] else None
        return {"seat_id": seat, "state": "bound" if rows else "unbound",
                "revision": rows[0][0] if rows else 0,
                "updated_at": core.now_rfc3339(rows[0][1]) if rows else None,
                "observed_at": core.now_rfc3339(), "stale": False, "unacked": pending}

    def _list_seats(self, p, b, a):
        lim = self._limit(a)
        after = self._cursor_read(p, "seats", a.get("cursor"))[1]
        seats = [s for s in self._visible_seats(b) if s > after]
        page = seats[:lim]
        out = []
        for s in page:
            bound = self.store.q("SELECT updated FROM bindings WHERE seat=? AND state='active'", (s,))
            out.append({"seat_id": s, "display_name": s, "state": "bound" if bound else "unbound",
                        "updated_at": core.now_rfc3339(bound[0][0]) if bound else None})
        nxt = self._cursor_make(p, "seats", 0, page[-1]) if len(seats) > lim else None
        return {"seats": out, "next_cursor": nxt}

    def _query_box(self, p, b, mailbox, cur, lim):
        if mailbox == "inbox":
            self.adapter.sync_inbox(b)
            col = "to_seat"
        else:
            col = "from_seat"
        q = "box:%s:%s" % (mailbox, b["binding_id"])
        c, m = self._cursor_read(p, q, cur)
        rows = self.store.q("SELECT %s FROM messages WHERE %s=? AND (created>? OR (created=? AND message_id>?)) "
                            "ORDER BY created, message_id LIMIT ?" % (COLS, col), (b["seat"], c, c, m, lim + 1))
        more = len(rows) > lim
        rows = rows[:lim]
        if rows:
            last_c, last_m = rows[-1][12], rows[-1][0]
        else:
            last_c, last_m = c, m
        return [_msg_row(r) for r in rows], self._cursor_make(p, q, last_c, last_m), more

    def _list_messages(self, p, b, a):
        msgs, nxt, more = self._query_box(p, b, a["mailbox"], a.get("cursor"), self._limit(a))
        return {"messages": msgs, "next_cursor": nxt, "has_more": more}

    def _get_message(self, p, b, a):
        rows = self.store.q("SELECT %s FROM messages WHERE message_id=? AND (to_seat=? OR from_seat=?)" % COLS,
                            (a["message_id"], b["seat"], b["seat"]))
        if not rows:
            raise ToolError("NOT_FOUND", "not found")  # 不存在與無權限同一錯誤
        return _msg_row(rows[0], full=True)

    def _send_message(self, p, b, a):
        if not self.flags["send"]:
            raise ToolError("UNAVAILABLE", "send disabled")
        to, body, key = a["to_seat"], a["body"], a["idempotency_key"]
        if not core.SEAT_RE.fullmatch(to):
            raise ToolError("INVALID_ARGUMENT", "to_seat must be cabin/seat")
        if not body.strip() or len(body.encode()) > core.BODY_MAX:
            raise ToolError("INVALID_ARGUMENT", "body empty or over 8KB")
        if any(ord(ch) < 32 and ch not in "\n\t" for ch in body):
            raise ToolError("INVALID_ARGUMENT", "control characters not allowed")
        if not (1 <= len(key) <= 128):
            raise ToolError("INVALID_ARGUMENT", "idempotency_key length 1..128")
        if to not in b["allowlist"]:
            raise ToolError("FORBIDDEN", "recipient not allowed")
        bh = core._sha(json.dumps([to, body, a.get("thread_id"), a.get("correlation_id"), a.get("causation_id")]))
        prev = self.store.q("SELECT message_id, body_hash FROM messages WHERE principal_id=? AND binding_id=? "
                            "AND idem_key=?", (p, b["binding_id"], key))
        if prev:
            if prev[0][1] != bh:
                raise ToolError("CONFLICT", "idempotency_key reused with different content")
            return self._get_message(p, b, {"message_id": prev[0][0]}) | {"idempotent_replay": True}
        hop = 0
        if a.get("causation_id"):
            c = self.store.q("SELECT hop_count FROM messages WHERE message_id=? AND (to_seat=? OR from_seat=?)",
                             (a["causation_id"], b["seat"], b["seat"]))
            if not c:
                raise ToolError("NOT_FOUND", "not found")
            hop = (c[0][0] or 0) + 1
        if hop > core.HOP_MAX:
            raise ToolError("FORBIDDEN", "hop limit reached; needs human")
        mid, t = core.new_ulid(), time.time()
        recipient_bound = bool(self.store.q("SELECT 1 FROM bindings WHERE seat=? AND state='active'", (to,)))
        state = "received" if recipient_bound else "queued"
        with self.store.lock:
            try:
                self.store.db.execute(
                    "INSERT INTO messages(message_id,from_seat,to_seat,principal_id,binding_id,idem_key,body_hash,body,"
                    "thread_id,correlation_id,causation_id,origin,hop_count,state,revision,source,created,updated) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'mcp',?,?)",
                    (mid, b["seat"], to, p, b["binding_id"], key, bh, body, a.get("thread_id"),
                     a.get("correlation_id") or mid, a.get("causation_id"), "mcp:" + b["seat"], hop, state, t, t))
            except sqlite3.IntegrityError:
                raise ToolError("CONFLICT", "concurrent send with same key", True)
        if not recipient_bound and self.adapter.dispatch(b, mid, to, body):
            self._set_state(mid, "dispatched")
        return self._get_message(p, b, {"message_id": mid})

    def _set_state(self, mid, state):
        with self.store.lock:
            self.store.db.execute("UPDATE messages SET state=?, revision=revision+1, updated=? WHERE message_id=?",
                                  (state, time.time(), mid))

    def _ack_message(self, p, b, a):
        if not self.flags["ack"]:
            raise ToolError("UNAVAILABLE", "ack disabled")
        with self.store.lock:
            rows = self.store.q("SELECT state, revision FROM messages WHERE message_id=? AND to_seat=?",
                                (a["message_id"], b["seat"]))
            if not rows:
                raise ToolError("NOT_FOUND", "not found")  # 只有收件人可 ack;寄件人也看到 NOT_FOUND
            state, rev = rows[0]
            if state == a["ack_state"]:
                return self._get_message(p, b, {"message_id": a["message_id"]}) | {"idempotent_replay": True}
            if state in ("processed", "rejected"):
                raise ToolError("CONFLICT", "already acknowledged")
            if rev != a["expected_revision"]:
                raise ToolError("CONFLICT", "revision mismatch")
            self._set_state(a["message_id"], a["ack_state"])
        return self._get_message(p, b, {"message_id": a["message_id"]})

    def _wait_messages(self, p, b, a):
        tmo = a.get("timeout_secs", core.WAIT_MAX_SECS)
        if tmo < 0 or tmo > core.WAIT_MAX_SECS:
            raise ToolError("INVALID_ARGUMENT", "timeout_secs must be 0..25")
        self.limiter.wait_enter(p)
        try:
            end = time.time() + tmo
            while True:
                msgs, nxt, more = self._query_box(p, b, "inbox", a.get("cursor"), core.LIMIT_DEFAULT)
                if msgs or time.time() >= end:
                    return {"messages": msgs, "next_cursor": nxt, "has_more": more}
                time.sleep(0.25)
                b = self.store.active_binding(p)
                if not b:
                    raise ToolError("FORBIDDEN", "no active binding")
        finally:
            self.limiter.wait_exit(p)
