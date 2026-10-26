# SPDX-License-Identifier: Apache-2.0
"""Streamable HTTP `/mcp`(JSON 回應模式)。P0 只准綁 loopback;驗 Origin;每請求 Bearer。"""
from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import core
from .tools import TOOLS, Tools

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2025-11-25")
LOOPBACK = ("127.0.0.1", "::1", "localhost")
MAX_REQ = 64 * 1024


def _origin_ok(origin):
    if not origin:
        return True
    from urllib.parse import urlparse
    try:
        return urlparse(origin).hostname in LOOPBACK
    except ValueError:
        return False


def make_handler(tools, auth):
    sessions, slock = set(), threading.Lock()  # 本程序發出的 Mcp-Session-Id;帶了未知 id → 404

    class H(BaseHTTPRequestHandler):
        server_version = "aib-chat-mcp"
        sys_version = ""

        def log_message(self, *a):  # 不寫存取記錄(避免標頭/本文外洩);稽核在 SQLite
            pass

        def _send(self, code, obj=None, headers=None):
            body = b"" if obj is None else json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            if obj is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send(405 if self.path.split("?")[0] == "/mcp" else 404, None, {"Allow": "POST"})

        do_DELETE = do_GET

        def do_POST(self):
            if self.path.split("?")[0] != "/mcp":
                return self._send(404)
            if not _origin_ok(self.headers.get("Origin")):
                return self._send(403, {"error": "origin not allowed"})
            try:
                principal = auth.authenticate(self.headers.get("Authorization"))
            except core.ToolError:
                return self._send(401, {"error": "unauthenticated"},
                                  {"WWW-Authenticate": 'Bearer realm="aib-chat-mcp"'})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._send(400, {"error": "bad length"})
            if n <= 0 or n > MAX_REQ:
                return self._send(413 if n > MAX_REQ else 400, {"error": "bad length"})
            try:
                req = json.loads(self.rfile.read(n))
            except ValueError:
                return self._send(400, {"jsonrpc": "2.0", "id": None,
                                        "error": {"code": -32700, "message": "parse error"}})
            if isinstance(req, list) or not isinstance(req, dict):
                return self._send(400, {"jsonrpc": "2.0", "id": None,
                                        "error": {"code": -32600, "message": "invalid request"}})
            pvh = self.headers.get("MCP-Protocol-Version")
            if pvh and pvh not in PROTOCOL_VERSIONS:
                return self._send(400, {"jsonrpc": "2.0", "id": req.get("id"),
                                        "error": {"code": -32600, "message": "unsupported MCP-Protocol-Version"}})
            sid = self.headers.get("Mcp-Session-Id")
            if sid and req.get("method") != "initialize" and sid not in sessions:
                return self._send(404, {"jsonrpc": "2.0", "id": req.get("id"),
                                        "error": {"code": -32001, "message": "session not found"}})
            if "id" not in req:  # notification
                return self._send(202)
            rid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
            headers = {}
            if method == "initialize":
                pv = params.get("protocolVersion")
                headers["Mcp-Session-Id"] = secrets.token_urlsafe(16)
                with slock:
                    if len(sessions) > 1000:
                        sessions.clear()
                    sessions.add(headers["Mcp-Session-Id"])
                result = {"protocolVersion": pv if pv in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "aib-chat-mcp", "version": core.API_VERSION}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [{"name": n, "description": d, "inputSchema": s}
                                    for n, (s, _sc, d) in TOOLS.items()]}
            elif method == "tools/call":
                if not isinstance(params.get("name"), str) or params.get("name") not in TOOLS:
                    return self._send(200, {"jsonrpc": "2.0", "id": rid,
                                            "error": {"code": -32602, "message": "unknown tool"}})
                try:
                    out = tools.call(principal, params.get("name"), params.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
                              "structuredContent": out, "isError": False}
                except core.ToolError as e:
                    err = {"code": e.code, "message": e.message, "retryable": e.retryable}
                    if e.retry_after is not None:
                        err["retry_after"] = e.retry_after
                        headers["Retry-After"] = str(e.retry_after)
                    result = {"content": [{"type": "text", "text": json.dumps({"error": err})}],
                              "structuredContent": {"error": err}, "isError": True}
            else:
                return self._send(200, {"jsonrpc": "2.0", "id": rid,
                                        "error": {"code": -32601, "message": "method not found"}})
            self._send(200, {"jsonrpc": "2.0", "id": rid, "result": result}, headers)

    return H


def build(host="127.0.0.1", port=47820, store=None, flags=None):
    if host not in LOOPBACK:
        raise SystemExit("P0 只允許本機位址(127.0.0.1);對外通道屬 P1,未開")
    store = store or core.Store()
    tools = Tools(store, flags=flags)
    auth = core.ChainAuth(core.BearerAuth(store), core.OAuthAuth())
    httpd = ThreadingHTTPServer((host, port), make_handler(tools, auth))
    httpd.daemon_threads = True
    return httpd, tools


def serve_background(host="127.0.0.1", port=0, store=None, flags=None):
    httpd, tools = build(host, port, store, flags)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, tools
