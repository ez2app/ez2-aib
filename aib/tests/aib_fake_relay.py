# SPDX-License-Identifier: Apache-2.0
"""本機假 Nostr 中繼站 + 最小用戶端(全球席位對談開發文件第 22 節 1.10、第 23 節)。

為什麼:全球 dm 的自動測試**一律只准連這一個**。10-15 試驗在公開中繼站 15 秒寄 5 則就被 damus 封鎖;
測試或除錯迴圈去打公開站,輕則被封,重則整台機器的 IP 進黑名單,真的全球 dm 也跟著收不到。

只做 NIP-01 的五樣:EVENT / REQ / CLOSE(用戶端→站)與 OK / EOSE(站→用戶端),外加站送 EVENT、NOTICE。
  - 只綁 127.0.0.1(別的位址一律 ValueError,不開)。
  - 只用標準庫:開源版主程式慣例;轉接程式要的 nostr-sdk 在選用外掛自己的 venv 裡,測試不依賴它。
  - 事件 id 照 NIP-01 重算比對,對不上回 OK false「invalid:」;**不驗 schnorr 簽章**(標準庫沒有 secp256k1)。
    簽章、seal 與 rumor 同簽章者這些驗證是轉接程式的事,要在轉接程式的測試裡驗,不能指望這個站替你擋。
  - 可替換事件照 NIP-01 只留最新(kind 0、3、10000–19999 依 pubkey+kind;30000–39999 再加 d 標籤),
    名冊(30078)與收信中繼站清單(10050)的測試才不會讀到舊版;20000–29999 只轉送不存。
  - reject(event) -> str|None:測試要模擬限流/封鎖時傳入,回字串就以 OK false 回該字串。

用法:
    with FakeRelay() as relay:
        c = RelayClient(relay.url)
        c.send(["EVENT", ev]); c.recv()          # ["OK", id, True, ""]
        c.send(["REQ", "s1", {"kinds": [1059], "#p": [pk]}])
        c.recv_until_eose("s1")                   # (事件清單, EOSE)
"""
import base64
import hashlib
import json
import os
import socket
import socketserver
import struct
import threading
import time

LOOPBACK = "127.0.0.1"
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 1 << 20   # 1 MiB;一則 dm 上限 8000 字元,加密包好也遠小於此


def assert_loopback(host):
    """只准 127.0.0.1 / localhost。站與用戶端開連線前都先過這一關。"""
    if host not in (LOOPBACK, "localhost"):
        raise ValueError("假中繼站只連本機 127.0.0.1;測試不准連公開中繼站(給的是 %r)" % (host,))


def accept_key(key):
    return base64.b64encode(hashlib.sha1((key + _GUID).encode("ascii")).digest()).decode("ascii")


def event_id(ev):
    """NIP-01:sha256([0, pubkey, created_at, kind, tags, content]) 的 hex,JSON 不留空白、不跳脫非 ASCII。"""
    ser = json.dumps([0, ev["pubkey"], ev["created_at"], ev["kind"], ev["tags"], ev["content"]],
                     separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(ser.encode("utf-8")).hexdigest()


def make_event(pubkey, kind, content="", tags=(), created_at=None):
    """測試用:組一個 id 正確、簽章是假的事件(這個站不驗簽章)。"""
    ev = {"pubkey": pubkey, "created_at": int(time.time()) if created_at is None else created_at,
          "kind": kind, "tags": [list(t) for t in tags], "content": content}
    ev["id"] = event_id(ev)
    ev["sig"] = "0" * 128
    return ev


def matches(flt, ev):
    """NIP-01 單一 filter;同一 filter 內各條件 AND,REQ 裡多個 filter 之間 OR。"""
    if "ids" in flt and ev["id"] not in flt["ids"]:
        return False
    if "authors" in flt and ev["pubkey"] not in flt["authors"]:
        return False
    if "kinds" in flt and ev["kind"] not in flt["kinds"]:
        return False
    if "since" in flt and ev["created_at"] < flt["since"]:
        return False
    if "until" in flt and ev["created_at"] > flt["until"]:
        return False
    for key, want in flt.items():
        if len(key) == 2 and key[0] == "#":
            have = {t[1] for t in ev["tags"] if len(t) >= 2 and t[0] == key[1]}
            if not have.intersection(want):
                return False
    return True


def _replace_key(ev):
    k = ev["kind"]
    if k in (0, 3) or 10000 <= k < 20000:
        return (ev["pubkey"], k, None)
    if 30000 <= k < 40000:
        d = next((t[1] for t in ev["tags"] if len(t) >= 2 and t[0] == "d"), "")
        return (ev["pubkey"], k, d)
    return None


def _is_ephemeral(ev):
    return 20000 <= ev["kind"] < 30000


def _check_event(ev):
    """回 None = 形狀對;否則回 OK false 的理由。"""
    try:
        if not (isinstance(ev["id"], str) and isinstance(ev["pubkey"], str) and isinstance(ev["sig"], str)
                and isinstance(ev["created_at"], int) and isinstance(ev["kind"], int)
                and isinstance(ev["content"], str) and isinstance(ev["tags"], list)
                and all(isinstance(t, list) and all(isinstance(x, str) for x in t) for t in ev["tags"])):
            return "invalid: bad event shape"
    except (KeyError, TypeError):
        return "invalid: bad event shape"
    if event_id(ev) != ev["id"]:
        return "invalid: bad event id"
    return None


# ── WebSocket(RFC 6455)最小實作:站與用戶端共用 ──

def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("連線已關閉")
        buf += chunk
    return buf


def _read_frame(sock):
    b1, b2 = _recv_exact(sock, 2)
    fin, opcode, masked, ln = b1 & 0x80, b1 & 0x0F, b2 & 0x80, b2 & 0x7F
    if ln == 126:
        ln = struct.unpack("!H", _recv_exact(sock, 2))[0]
    elif ln == 127:
        ln = struct.unpack("!Q", _recv_exact(sock, 8))[0]
    if ln > MAX_MESSAGE:
        raise ConnectionError("訊息太大")
    mask = _recv_exact(sock, 4) if masked else None
    data = _recv_exact(sock, ln)
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return bool(fin), opcode, bool(masked), data


def _frame(opcode, data, mask):
    head = bytearray([0x80 | opcode])
    m = 0x80 if mask else 0
    n = len(data)
    if n < 126:
        head.append(m | n)
    elif n < 65536:
        head.append(m | 126)
        head += struct.pack("!H", n)
    else:
        head.append(m | 127)
        head += struct.pack("!Q", n)
    if mask:
        key = os.urandom(4)
        head += key
        data = bytes(b ^ key[i % 4] for i, b in enumerate(data))
    return bytes(head) + data


def _read_message(sock, send_ctl, expect_masked):
    """讀一則完整訊息(合併分段);順手回 ping。回 (opcode, bytes);opcode 8 = 對方要關。"""
    parts, first = [], None
    while True:
        fin, opcode, masked, data = _read_frame(sock)
        if expect_masked is not None and masked != expect_masked:
            raise ConnectionError("遮罩方向不對")   # RFC 6455 §5.1:用戶端必遮、站必不遮
        if opcode == 9:
            send_ctl(10, data)
            continue
        if opcode == 10:
            continue
        if opcode == 8:
            return 8, data
        if opcode != 0:
            first = opcode
        parts.append(data)
        if sum(len(p) for p in parts) > MAX_MESSAGE:
            raise ConnectionError("訊息太大")
        if fin:
            return first, b"".join(parts)


class _Conn(socketserver.BaseRequestHandler):
    def setup(self):
        self.subs = {}
        self.lock = threading.Lock()
        self.alive = True

    def send_json(self, msg):
        self._send(1, json.dumps(msg, ensure_ascii=False).encode("utf-8"))

    def _send(self, opcode, data):
        with self.lock:
            if self.alive:
                try:
                    self.request.sendall(_frame(opcode, data, mask=False))
                except OSError:
                    self.alive = False

    def _handshake(self):
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = self.request.recv(4096)
            if not chunk or len(raw) > 16384:
                return False
            raw += chunk
        lines = raw.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
        hdr = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                hdr[k.strip().lower()] = v.strip()
        key = hdr.get("sec-websocket-key")
        if not lines[0].startswith("GET ") or hdr.get("upgrade", "").lower() != "websocket" or not key:
            self.request.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            return False
        self.request.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                              "Sec-WebSocket-Accept: %s\r\n\r\n" % accept_key(key)).encode("ascii"))
        return True

    def handle(self):
        relay = self.server.relay
        if not self._handshake():
            return
        relay._register(self)
        try:
            while True:
                op, data = _read_message(self.request, self._send, expect_masked=True)
                if op == 8:
                    self._send(8, data[:2])
                    return
                if op != 1:
                    self.send_json(["NOTICE", "only text frames"])
                    continue
                relay._on_message(self, data)
        except (ConnectionError, OSError):
            return
        finally:
            self.alive = False
            relay._unregister(self)


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class FakeRelay(object):
    """本機假中繼站。port=0 ⇒ 系統挑空埠;`with` 離開即關站並斷掉所有連線。"""

    def __init__(self, host=LOOPBACK, port=0, reject=None):
        assert_loopback(host)
        self.reject = reject
        self.events = []          # 依收到先後;REQ 回舊事件時另外排序
        self.received = []        # 每一則 EVENT(含被拒、重複)照原樣記下,給測試數「寄了幾次」
        self._conns = set()
        self._lock = threading.Lock()
        self._server = _Server((LOOPBACK if host == "localhost" else host, port), _Conn)
        self._server.relay = self
        self.port = self._server.server_address[1]
        self.url = "ws://%s:%d" % (LOOPBACK, self.port)
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
                                        name="fake-relay-%d" % self.port, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        with self._lock:
            conns = list(self._conns)
        for c in conns:
            c.alive = False
            try:
                c.request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            c.request.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def _register(self, conn):
        with self._lock:
            self._conns.add(conn)

    def _unregister(self, conn):
        with self._lock:
            self._conns.discard(conn)

    def _on_message(self, conn, data):
        try:
            msg = json.loads(data.decode("utf-8"))
        except ValueError:
            return conn.send_json(["NOTICE", "invalid: not JSON"])
        if not isinstance(msg, list) or not msg or not isinstance(msg[0], str):
            return conn.send_json(["NOTICE", "invalid: not a message array"])
        verb = msg[0]
        if verb == "EVENT" and len(msg) == 2 and isinstance(msg[1], dict):
            return self._on_event(conn, msg[1])
        if verb == "REQ" and len(msg) >= 3 and isinstance(msg[1], str) and all(isinstance(f, dict) for f in msg[2:]):
            return self._on_req(conn, msg[1], msg[2:])
        if verb == "CLOSE" and len(msg) == 2 and isinstance(msg[1], str):
            with conn.lock:
                conn.subs.pop(msg[1], None)
            return None
        return conn.send_json(["NOTICE", "unsupported: %s" % verb])

    def _on_event(self, conn, ev):
        eid = ev.get("id") if isinstance(ev.get("id"), str) else ""
        with self._lock:
            self.received.append(ev)
        bad = _check_event(ev)
        if bad:
            return conn.send_json(["OK", eid, False, bad])
        if self.reject:
            why = self.reject(ev)
            if why:
                return conn.send_json(["OK", eid, False, why])
        with self._lock:
            if any(e["id"] == eid for e in self.events):
                dup = True
            else:
                dup = False
                if not _is_ephemeral(ev):
                    rk = _replace_key(ev)
                    if rk is not None:
                        old = [e for e in self.events if _replace_key(e) == rk]
                        if any(o["created_at"] > ev["created_at"] for o in old):
                            conn.send_json(["OK", eid, True, "duplicate: have a newer version"])
                            return None
                        self.events = [e for e in self.events if _replace_key(e) != rk]
                    self.events.append(ev)
            targets = list(self._conns)
        if dup:
            return conn.send_json(["OK", eid, True, "duplicate: already have this event"])
        conn.send_json(["OK", eid, True, ""])
        for c in targets:
            with c.lock:
                hits = [sid for sid, flts in c.subs.items() if any(matches(f, ev) for f in flts)]
            for sid in hits:
                c.send_json(["EVENT", sid, ev])
        return None

    def _on_req(self, conn, sid, filters):
        with self._lock:
            stored = sorted(self.events, key=lambda e: (-e["created_at"], e["id"]))
        out, seen = [], set()
        for f in filters:
            hit = [e for e in stored if matches(f, e)]
            if isinstance(f.get("limit"), int):
                hit = hit[:max(f["limit"], 0)]
            for e in hit:
                if e["id"] not in seen:
                    seen.add(e["id"])
                    out.append(e)
        out.sort(key=lambda e: (-e["created_at"], e["id"]))
        with conn.lock:
            conn.subs[sid] = filters   # 先登記再送舊事件:送的途中進來的新事件不會漏
        for e in out:
            conn.send_json(["EVENT", sid, e])
        conn.send_json(["EOSE", sid])


class RelayClient(object):
    """最小 WebSocket 用戶端,只連本機。測試與之後轉接程式的單元測試共用。"""

    def __init__(self, url, timeout=5.0):
        if not url.startswith("ws://"):
            raise ValueError("只支援 ws://127.0.0.1:<埠>(給的是 %r)" % (url,))
        hostport = url[len("ws://"):].split("/", 1)[0]
        host, _, port = hostport.rpartition(":")
        assert_loopback(host)
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        self.timeout = timeout
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        self.sock.sendall(("GET / HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (hostport, key)).encode("ascii"))
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("握手時連線被關")
            raw += chunk
        head, self._rest = raw.split(b"\r\n\r\n", 1)
        text = head.decode("latin-1")
        if not text.startswith("HTTP/1.1 101") or ("sec-websocket-accept: %s" % accept_key(key).lower()) not in text.lower():
            raise ConnectionError("握手失敗:%s" % text.split("\r\n", 1)[0])
        if self._rest:
            raise ConnectionError("握手後多出資料")

    def send(self, msg):
        self.sock.sendall(_frame(1, json.dumps(msg, ensure_ascii=False).encode("utf-8"), mask=True))

    def send_raw(self, opcode, data):
        self.sock.sendall(_frame(opcode, data, mask=True))

    def recv(self, timeout=None):
        """收一則站送來的 JSON 訊息;逾時丟 socket.timeout。對方關線回 None。"""
        self.sock.settimeout(self.timeout if timeout is None else timeout)
        op, data = _read_message(self.sock, lambda o, d: self.sock.sendall(_frame(o, d, mask=True)),
                                 expect_masked=False)
        if op == 8:
            return None
        return json.loads(data.decode("utf-8"))

    def recv_until_eose(self, sid, timeout=None):
        events = []
        while True:
            m = self.recv(timeout)
            if m is None:
                raise ConnectionError("等 EOSE 時連線被關")
            if m[0] == "EVENT" and m[1] == sid:
                events.append(m[2])
            elif m[0] == "EOSE" and m[1] == sid:
                return events

    def publish(self, ev, timeout=None):
        """寄一則,回站的 OK 訊息 ["OK", id, accepted, message]。"""
        self.send(["EVENT", ev])
        while True:
            m = self.recv(timeout)
            if m is None:
                raise ConnectionError("等 OK 時連線被關")
            if m[0] == "OK" and m[1] == ev.get("id"):
                return m

    def close(self):
        try:
            self.sock.sendall(_frame(8, struct.pack("!H", 1000), mask=True))
        except OSError:
            pass
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
