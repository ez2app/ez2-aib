#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""AIB 對話 MCP P0:驗收 A–E 可本機驗的部分(只用 mktemp;不碰真 ~/.ez2-aib)。"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from chat_mcp import core, server  # noqa: E402

A, B, C = "b962/b81c8", "b962/8f067", "b962/49136"
SESS_B = "e32847d6-5a8e-481b-aa67-6d6dc438f067"


class P0(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-chat-mcp-test-")
        self._saved_sd = os.environ.pop("AIB_CHAT_MCP_SECRETS_DIR", None)  # NIT-6:不受外部環境影響
        self.bridge = Path(self.tmp) / "bridge"
        os.environ["AIB_CHAT_MCP_BRIDGE_ROOT"] = str(self.bridge)
        os.environ.pop("AIB_CHAT_MCP_BRIDGE_DISPATCH", None)
        self.store = core.Store(Path(self.tmp) / "home")
        self.store.bind("grok", A, [B, C])
        self.store.bind("muse", B, [A], session_uuid=SESS_B)
        self.store.bind("dot", C, [A])
        self.store.bind("outsider", "b962/zzzzz", [], scopes=("aib:identity:read",))
        self.k = {p: self.store.new_key(p) for p in ("grok", "muse", "dot", "outsider")}
        self.httpd, self.tools = server.serve_background(port=0, store=self.store)
        self.url = "http://127.0.0.1:%d/mcp" % self.httpd.server_address[1]
        self.n = 0

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self._saved_sd is not None:
            os.environ["AIB_CHAT_MCP_SECRETS_DIR"] = self._saved_sd

    def rpc(self, who, method, params=None, token=None, headers=None):
        self.n += 1
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if who or token:
            h["Authorization"] = "Bearer " + (token or self.k[who])
        h.update(headers or {})
        req = urllib.request.Request(self.url, json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method,
                                                           "params": params or {}}).encode(), h)
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.status, json.loads(r.read() or b"null"), dict(r.headers)
        except urllib.error.HTTPError as e:
            e.close()
            return e.code, None, dict(e.headers)

    def call(self, who, name, args=None):
        st, body, _ = self.rpc(who, "tools/call", {"name": name, "arguments": args or {}})
        self.assertEqual(st, 200)
        sc = body["result"]["structuredContent"]
        return sc.get("error") or sc["data"], body["result"]["isError"]

    def ok(self, who, name, args=None):
        d, err = self.call(who, name, args)
        self.assertFalse(err, d)
        return d

    def err(self, who, name, args=None):
        d, err = self.call(who, name, args)
        self.assertTrue(err, d)
        return d

    def send(self, who="grok", to=B, body="hi", key="k1", **kw):
        return self.ok(who, "aib_send_message", dict(to_seat=to, body=body, idempotency_key=key, **kw))

    # ---- B 工具發現 / 認證 ----
    def test_initialize_and_list(self):
        st, body, h = self.rpc("grok", "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                      "clientInfo": {"name": "t", "version": "0"}})
        self.assertEqual(st, 200)
        self.assertIn("Mcp-Session-Id", h)
        names = {t["name"] for t in self.rpc("grok", "tools/list")[1]["result"]["tools"]}
        self.assertEqual(names, {"aib_health", "aib_whoami", "aib_get_status", "aib_list_seats", "aib_list_messages",
                                 "aib_get_message", "aib_send_message", "aib_ack_message", "aib_wait_messages"})
        self.assertFalse(any(x in n for n in names for x in ("shell", "exec", "path", "broadcast")))

    def test_unauthenticated_and_origin(self):
        self.assertEqual(self.rpc(None, "tools/list")[0], 401)
        self.assertEqual(self.rpc(None, "tools/list", token="aibk_wrong")[0], 401)
        self.assertEqual(self.rpc("grok", "tools/list", headers={"Origin": "https://evil.example"})[0], 403)
        self.store.revoke_keys("dot")
        self.assertEqual(self.rpc("dot", "tools/list")[0], 401)

    def test_only_hash_stored(self):
        raw = self.k["grok"]
        self.assertNotIn(raw.encode(), (Path(self.tmp) / "home" / "chat.db").read_bytes())

    def test_whoami_and_binding_revoke(self):
        d = self.ok("muse", "aib_whoami")
        self.assertEqual((d["principal_id"], d["seat_id"]), ("muse", B))
        self.store.revoke_binding(d["binding_id"])
        self.assertEqual(self.err("muse", "aib_whoami")["code"], "FORBIDDEN")

    def test_scope_and_strict_schema(self):
        self.assertEqual(self.err("outsider", "aib_list_messages", {"mailbox": "inbox"})["code"], "FORBIDDEN")
        self.assertEqual(self.err("grok", "aib_whoami", {"x": 1})["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.err("grok", "aib_send_message", {"to_seat": B, "body": "x", "idempotency_key": "a",
                                                               "from": "b962/8f067"})["code"], "INVALID_ARGUMENT")

    # ---- C 唯讀隔離 ----
    def test_read_twice_does_not_move_bridge_files(self):
        inbox = self.bridge / SESS_B / "inbox"
        (inbox / "claimed").mkdir(parents=True)
        f = inbox / "1-a.json"
        f.write_text(json.dumps({"schema": "aib-sand-bridge-msg/v1", "from": "human", "text": "hello",
                                 "created_epoch": 1}))
        (inbox / "claimed" / "0-b.json").write_text(json.dumps({"text": "old", "created_epoch": 0}))
        r1 = self.ok("muse", "aib_list_messages", {"mailbox": "inbox"})
        r2 = self.ok("muse", "aib_list_messages", {"mailbox": "inbox"})
        self.assertTrue(f.exists())
        self.assertEqual(sorted(p.name for p in inbox.iterdir()), ["1-a.json", "claimed"])
        self.assertEqual([m["message_id"] for m in r1["messages"]], [m["message_id"] for m in r2["messages"]])
        self.assertEqual(len(r1["messages"]), 2)
        self.assertTrue(all(m["duplicate_possible"] for m in r1["messages"]))

    def test_cross_principal_message_and_cursor(self):
        m = self.send()
        self.assertEqual(self.err("dot", "aib_get_message", {"message_id": m["message_id"]})["code"], "NOT_FOUND")
        self.assertEqual(self.err("dot", "aib_get_message", {"message_id": "01NOPE"})["code"], "NOT_FOUND")
        cur = self.ok("muse", "aib_list_messages", {"mailbox": "inbox"})["next_cursor"]
        self.assertEqual(self.err("dot", "aib_list_messages", {"mailbox": "inbox", "cursor": cur})["code"],
                         "INVALID_ARGUMENT")
        self.assertEqual(self.err("muse", "aib_list_messages", {"mailbox": "outbox", "cursor": cur})["code"],
                         "INVALID_ARGUMENT")
        self.assertEqual(self.err("muse", "aib_list_messages", {"mailbox": "inbox", "cursor": cur[:-2] + "AA"})
                         ["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.ok("muse", "aib_list_messages", {"mailbox": "inbox", "cursor": cur})["messages"], [])

    def test_cross_seat_status_hidden(self):
        self.assertEqual(self.err("dot", "aib_get_status", {"seat_id": B})["code"], "NOT_FOUND")
        self.assertEqual(self.ok("dot", "aib_get_status")["seat_id"], C)

    def test_pagination(self):
        for i in range(5):
            self.send(key="p%d" % i, body="m%d" % i)
        p1 = self.ok("muse", "aib_list_messages", {"mailbox": "inbox", "limit": 3})
        p2 = self.ok("muse", "aib_list_messages", {"mailbox": "inbox", "limit": 3, "cursor": p1["next_cursor"]})
        self.assertTrue(p1["has_more"])
        self.assertEqual([m["summary"] for m in p1["messages"] + p2["messages"]], ["m%d" % i for i in range(5)])

    # ---- D 寫入防重 / 寄件人不可偽造 ----
    def test_sender_from_binding(self):
        m = self.send()
        self.assertEqual((m["from_seat"], m["to_seat"]), (A, B))
        got = self.ok("muse", "aib_get_message", {"message_id": m["message_id"]})
        self.assertEqual(got["from_seat"], A)
        self.assertEqual(len(m["message_id"]), 26)

    def test_idempotency(self):
        m1 = self.send(key="same")
        m2 = self.send(key="same")
        self.assertEqual(m1["message_id"], m2["message_id"])
        self.assertTrue(m2["idempotent_replay"])
        self.assertEqual(self.err("grok", "aib_send_message", {"to_seat": B, "body": "other",
                                                               "idempotency_key": "same"})["code"], "CONFLICT")
        self.send(who="dot", to=A, key="same")  # 不同 principal 同鍵互不影響

    def test_recipient_allowlist(self):
        self.assertEqual(self.err("dot", "aib_send_message", {"to_seat": B, "body": "x", "idempotency_key": "z"})
                         ["code"], "FORBIDDEN")

    def test_hop_limit(self):
        m = self.send(key="h0")
        who = ["muse", "grok"]
        for i in range(1, 5):
            frm = who[(i - 1) % 2]
            m = self.send(who=frm, to=A if frm == "muse" else B, key="h%d" % i, causation_id=m["message_id"])
            self.assertEqual(m["hop_count"], i)
        self.assertEqual(self.err("muse", "aib_send_message", {"to_seat": A, "body": "x", "idempotency_key": "h5",
                                                               "causation_id": m["message_id"]})["message"],
                         "hop limit reached; needs human")

    def test_rate_limit_retry_after(self):
        for i in range(core.SEND_PER_MIN):
            self.send(key="r%d" % i)
        d = self.err("grok", "aib_send_message", {"to_seat": B, "body": "x", "idempotency_key": "r-last"})
        self.assertEqual(d["code"], "RATE_LIMITED")
        self.assertGreater(d["retry_after"], 0)

    def test_bridge_untouched_by_default(self):
        self.send(who="muse", to=A, key="b1")
        self.assertFalse((self.bridge / SESS_B / "outbox").exists())

    # ---- E 明確確認 ----
    def test_ack_rules(self):
        m = self.send()
        self.assertEqual(m["state"], "received")
        self.assertEqual(self.err("grok", "aib_ack_message", {"message_id": m["message_id"], "expected_revision": 1,
                                                              "ack_state": "processed"})["code"], "NOT_FOUND")
        self.assertEqual(self.err("muse", "aib_ack_message", {"message_id": m["message_id"], "expected_revision": 9,
                                                              "ack_state": "processed"})["code"], "CONFLICT")
        r = self.ok("muse", "aib_ack_message", {"message_id": m["message_id"], "expected_revision": 1,
                                                "ack_state": "processed"})
        self.assertEqual((r["state"], r["revision"]), ("processed", 2))
        again = self.ok("muse", "aib_ack_message", {"message_id": m["message_id"], "expected_revision": 1,
                                                    "ack_state": "processed"})
        self.assertTrue(again["idempotent_replay"])
        self.assertEqual(self.err("muse", "aib_ack_message", {"message_id": m["message_id"], "expected_revision": 2,
                                                              "ack_state": "rejected"})["code"], "CONFLICT")

    def test_read_does_not_ack(self):
        m = self.send()
        self.ok("muse", "aib_list_messages", {"mailbox": "inbox"})
        self.assertEqual(self.ok("muse", "aib_get_message", {"message_id": m["message_id"]})["state"], "received")

    def test_wait_messages(self):
        t = time.time()
        r = self.ok("muse", "aib_wait_messages", {"timeout_secs": 1})
        self.assertEqual(r["messages"], [])
        self.assertGreaterEqual(time.time() - t, 0.9)
        self.send()
        r = self.ok("muse", "aib_wait_messages", {"cursor": r["next_cursor"], "timeout_secs": 5})
        self.assertEqual(len(r["messages"]), 1)
        self.assertEqual(self.err("muse", "aib_wait_messages", {"timeout_secs": 26})["code"], "INVALID_ARGUMENT")

    def test_audit_has_no_body(self):
        self.send(body="SECRET-BODY-123")
        self.assertFalse(any("SECRET-BODY-123" in str(r) for r in self.store.q("SELECT * FROM audit")))

    def test_non_loopback_refused(self):
        with self.assertRaises(SystemExit):
            server.build("0.0.0.0", 0, self.store)

    # ---- R1 MINOR 修補 ----
    def _raw(self, body, headers):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        try:
            c.putrequest("POST", "/mcp")
            for k, v in headers.items():
                c.putheader(k, v)
            c.endheaders()
            if body:
                c.send(body)
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    def test_r1_m1_bad_content_length(self):
        st, _ = self._raw(b"", {"Authorization": "Bearer " + self.k["grok"], "Content-Length": "abc"})
        self.assertEqual(st, 400)

    def test_r1_m2_one_seat_one_principal(self):
        with self.assertRaises(ValueError):
            self.store.bind("evil", B)
        self.store.revoke_binding(self.store.active_binding("muse")["binding_id"])
        self.assertTrue(self.store.bind("muse2", B))

    def test_r1_m3_key_new_conflict(self):
        import contextlib
        import io
        from chat_mcp import cli_main
        os.environ["AIB_CHAT_MCP_HOME"] = str(Path(self.tmp) / "cli-home")
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli_main.main(["key", "new", "--principal", "p1", "--seat", A]), 0)
                self.assertEqual(cli_main.main(["key", "new", "--principal", "p1", "--seat", A]), 0)
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                self.assertEqual(cli_main.main(["key", "new", "--principal", "p1", "--seat", B]), 2)
            self.assertIn("參數不同", err.getvalue())
        finally:
            os.environ.pop("AIB_CHAT_MCP_HOME", None)

    def test_r1_m4_protocol_errors(self):
        st, body, _ = self.rpc("grok", "tools/call", {"name": "aib_nope", "arguments": {}})
        self.assertEqual((st, body["error"]["code"]), (200, -32602))
        st, body, _ = self.rpc("grok", "tools/call", {"arguments": {}})
        self.assertEqual(body["error"]["code"], -32602)
        st, _, _ = self.rpc("grok", "ping", headers={"MCP-Protocol-Version": "1999-01-01"})
        self.assertEqual(st, 400)
        st, _, _ = self.rpc("grok", "ping", headers={"MCP-Protocol-Version": "2025-06-18"})
        self.assertEqual(st, 200)
        st, _, h = self.rpc("grok", "initialize", {"protocolVersion": "2025-06-18"})
        sid = {k.lower(): v for k, v in h.items()}["mcp-session-id"]
        self.assertEqual(self.rpc("grok", "ping", headers={"Mcp-Session-Id": sid})[0], 200)
        self.assertEqual(self.rpc("grok", "ping", headers={"Mcp-Session-Id": "bogus"})[0], 404)

    def test_r1_m5_wait_poll_and_mtime(self):
        self.assertGreaterEqual(core.WAIT_POLL_SECS, 1.0)
        inbox = self.bridge / SESS_B / "inbox"
        inbox.mkdir(parents=True)
        (inbox / "1-a.json").write_text(json.dumps({"text": "a", "created_epoch": 1}))
        b = self.store.active_binding("muse")
        ad = core.BridgeAdapter(self.store, self.bridge)
        self.assertEqual(ad.sync_inbox(b), 1)
        calls = []
        orig = Path.glob
        Path.glob = lambda self_, pat: calls.append(1) or orig(self_, pat)
        try:
            self.assertEqual(ad.sync_inbox(b), 0)
        finally:
            Path.glob = orig
        self.assertEqual(calls, [])  # 目錄沒變:不重掃

    def test_r1_m6_secret_location(self):
        sd = Path(self.tmp) / "secrets" / "chat-mcp"
        self.assertTrue((sd / "cursor.secret").exists())
        self.assertFalse((Path(self.tmp) / "home" / "cursor.secret").exists())
        self.assertEqual((sd / "cursor.secret").stat().st_mode & 0o777, 0o600)
        os.environ["AIB_CHAT_MCP_SECRETS_DIR"] = str(Path(self.tmp) / "alt")
        try:
            core.Store(Path(self.tmp) / "home2")
            self.assertTrue((Path(self.tmp) / "alt" / "cursor.secret").exists())
        finally:
            os.environ.pop("AIB_CHAT_MCP_SECRETS_DIR", None)

    def test_r1b_3_periodic_purge(self):
        old = time.time() - core.RETENTION_SECS - 10
        self.store.db.execute("INSERT INTO audit VALUES(?,?,?,?,?,?)", (old, "r", "grok", "t", "ok", None))
        self.assertFalse(self.store.maybe_purge())  # 剛清過,一小時內不再清
        self.assertEqual(self.store.q("SELECT COUNT(*) FROM audit WHERE ts<?", (old + 1,))[0][0], 1)
        self.assertTrue(self.store.maybe_purge(time.time() + core.PURGE_INTERVAL_SECS + 1))
        self.assertEqual(self.store.q("SELECT COUNT(*) FROM audit WHERE ts<?", (old + 1,))[0][0], 0)

    def test_r1b_4_session_lru_evicts_oldest(self):
        saved = server.MAX_SESSIONS
        server.MAX_SESSIONS = 2
        try:
            def init():
                req = urllib.request.Request(self.url, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                             "params": {"protocolVersion": "2025-06-18"}}).encode(),
                                             headers={"Authorization": "Bearer " + self.k["grok"],
                                                      "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
                with urllib.request.urlopen(req) as r:
                    return r.headers["Mcp-Session-Id"]

            def ping(sid):
                req = urllib.request.Request(self.url, data=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}).encode(),
                                             headers={"Authorization": "Bearer " + self.k["grok"], "Mcp-Session-Id": sid,
                                                      "MCP-Protocol-Version": "2025-06-18",
                                                      "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
                try:
                    with urllib.request.urlopen(req) as r:
                        return r.status
                except urllib.error.HTTPError as e:
                    return e.code
            s1, s2 = init(), init()
            self.assertEqual(ping(s1), 200)  # s1 變最近用過
            s3 = init()  # 淘汰最舊的 s2,不是整批清空
            self.assertEqual(ping(s1), 200)
            self.assertEqual(ping(s3), 200)
            self.assertEqual(ping(s2), 404)
        finally:
            server.MAX_SESSIONS = saved

    def test_r1_m7_bridge_sender_untrusted(self):
        inbox = self.bridge / SESS_B / "inbox"
        inbox.mkdir(parents=True)
        (inbox / "1-a.json").write_text(json.dumps({"from": "b962/b81c8", "text": "spoof", "created_epoch": 1}))
        r = self.ok("muse", "aib_list_messages", {"mailbox": "inbox"})
        self.assertEqual([(m["from_seat"], m["to_seat"]) for m in r["messages"]], [("bridge:unknown", B)])


if __name__ == "__main__":
    unittest.main(verbosity=1)
