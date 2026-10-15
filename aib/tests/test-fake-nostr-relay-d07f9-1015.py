#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""本機假 Nostr 中繼站自測(全球席位對談開發文件第 22 節 1.10、第 23 節)。

假中繼站在 tests/aib_fake_relay.py;全球 dm 之後的自動測試一律只連它,不連公開中繼站
(10-15 試驗 15 秒寄 5 則就被 damus 封鎖)。這支先把站本身驗對,之後的測試紅了才不會是站的錯。

R1–R3 連線與守門:只綁 127.0.0.1、握手照 RFC 6455、用戶端拒連非本機。
E1–E4 EVENT/OK:收下、重複、id 對不上、reject 模擬限流。
Q1–Q5 REQ/EOSE/CLOSE:舊事件新到舊再 EOSE、各種 filter、limit、即時轉送、CLOSE 後不再送。
P1–P2 可替換事件(名冊 30078、收信清單 10050 用得到)與短暫事件。
W1–W3 WebSocket 細節:ping/pong、大訊息(64 位元長度)、亂送的東西回 NOTICE 不斷線。
K1   刀測:拿掉用戶端的本機守門 ⇒ R3 變紅(連的是 TEST-NET 192.0.2.1,不是任何中繼站)。
"""
import hashlib
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
import aib_fake_relay as fr  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402

PK_A = "a" * 64
PK_B = "b" * 64
PK_R = "c" * 64


class Base(unittest.TestCase):
    def setUp(self):
        self.relay = fr.FakeRelay().start()
        self.addCleanup(self.relay.stop)

    def client(self):
        c = fr.RelayClient(self.relay.url, timeout=3.0)
        self.addCleanup(c.close)
        return c

    def assertSilent(self, c, wait=0.3):
        with self.assertRaises(socket.timeout):
            c.recv(timeout=wait)


class Guard(unittest.TestCase):
    def test_r1_binds_loopback_only(self):
        with fr.FakeRelay() as relay:
            self.assertEqual(relay._server.server_address[0], "127.0.0.1")
            self.assertTrue(relay.url.startswith("ws://127.0.0.1:"))
        for host in ("0.0.0.0", "192.168.1.10", "relay.damus.io", ""):
            with self.assertRaises(ValueError):
                fr.FakeRelay(host=host)

    def test_r2_handshake_accept_key(self):
        # RFC 6455 §1.3 的範例值
        self.assertEqual(fr.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")
        with fr.FakeRelay() as relay:
            s = socket.create_connection(("127.0.0.1", relay.port), timeout=3)
            s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")   # 不是 websocket 升級
            self.assertTrue(s.recv(100).startswith(b"HTTP/1.1 400"))
            s.close()
            c = fr.RelayClient(relay.url)
            c.close()

    def test_r3_client_refuses_non_loopback(self):
        # 刀測(K1)拿掉守門後這裡會真的試連 ⇒ 只放連不到任何站的位址:TEST-NET(RFC 5737)排第一、.invalid(RFC 2606)
        for url in ("ws://192.0.2.1:9", "ws://relay.example.invalid:80", "wss://relay.example.invalid"):
            with self.assertRaises(ValueError) as cm:
                fr.RelayClient(url, timeout=0.5)
            self.assertIn("只", str(cm.exception))


class Events(Base):
    def test_e1_accept(self):
        c = self.client()
        ev = fr.make_event(PK_A, 1059, "x", [["p", PK_R]])
        self.assertEqual(c.publish(ev), ["OK", ev["id"], True, ""])
        self.assertEqual([e["id"] for e in self.relay.events], [ev["id"]])

    def test_e2_duplicate(self):
        c = self.client()
        ev = fr.make_event(PK_A, 1059, "x")
        c.publish(ev)
        ok = c.publish(ev)
        self.assertTrue(ok[2])
        self.assertTrue(ok[3].startswith("duplicate:"))
        self.assertEqual(len(self.relay.events), 1)
        self.assertEqual(len(self.relay.received), 2)

    def test_e3_bad_id_and_shape(self):
        c = self.client()
        ev = fr.make_event(PK_A, 1, "x")
        ev["content"] = "tampered"
        ok = c.publish(ev)
        self.assertFalse(ok[2])
        self.assertTrue(ok[3].startswith("invalid:"))
        bad = dict(fr.make_event(PK_A, 1, "x"), kind="1")
        self.assertFalse(c.publish(bad)[2])
        self.assertEqual(self.relay.events, [])
        # NIP-01 的 id 序列化:非 ASCII 原樣、不留空白
        ev2 = fr.make_event(PK_A, 14, "已讀", [["aib-v", "1"]], created_at=1)
        want = hashlib.sha256(('[0,"%s",1,14,[["aib-v","1"]],"已讀"]' % PK_A).encode("utf-8")).hexdigest()
        self.assertEqual(ev2["id"], want)

    def test_e4_reject_hook(self):
        self.relay.reject = lambda ev: "rate-limited: slow down" if ev["content"] == "spam" else None
        c = self.client()
        self.assertEqual(c.publish(fr.make_event(PK_A, 1, "spam"))[2:], [False, "rate-limited: slow down"])
        self.assertTrue(c.publish(fr.make_event(PK_A, 1, "fine"))[2])
        self.assertEqual([e["content"] for e in self.relay.events], ["fine"])


class Requests(Base):
    def _seed(self, c):
        evs = [fr.make_event(PK_A, 1059, "a1", [["p", PK_R]], created_at=100),
               fr.make_event(PK_B, 1059, "b1", [["p", PK_R]], created_at=200),
               fr.make_event(PK_A, 1059, "a2", [["p", PK_B]], created_at=300),
               fr.make_event(PK_A, 1, "note", created_at=400)]
        for e in evs:
            c.publish(e)
        return evs

    def test_q1_stored_newest_first_then_eose(self):
        c = self.client()
        self._seed(c)
        c.send(["REQ", "s", {"kinds": [1059]}])
        self.assertEqual([e["content"] for e in c.recv_until_eose("s")], ["a2", "b1", "a1"])

    def test_q2_filters(self):
        c = self.client()
        evs = self._seed(c)
        cases = [
            ({"#p": [PK_R]}, ["b1", "a1"]),
            ({"authors": [PK_A], "kinds": [1059]}, ["a2", "a1"]),
            ({"since": 200, "until": 300}, ["a2", "b1"]),
            ({"ids": [evs[0]["id"]]}, ["a1"]),
            ({"#p": [PK_R], "authors": [PK_B]}, ["b1"]),
            ({"#e": ["none"]}, []),
        ]
        for i, (flt, want) in enumerate(cases):
            sid = "q%d" % i
            c.send(["REQ", sid, flt])
            self.assertEqual([e["content"] for e in c.recv_until_eose(sid)], want, flt)
        # 多個 filter 之間 OR,不重複
        c.send(["REQ", "or", {"kinds": [1]}, {"authors": [PK_A], "kinds": [1]}, {"#p": [PK_B]}])
        self.assertEqual([e["content"] for e in c.recv_until_eose("or")], ["note", "a2"])

    def test_q3_limit(self):
        c = self.client()
        self._seed(c)
        c.send(["REQ", "l", {"kinds": [1059], "limit": 2}])
        self.assertEqual([e["content"] for e in c.recv_until_eose("l")], ["a2", "b1"])

    def test_q4_live_forwarding(self):
        sub, pub = self.client(), self.client()
        sub.send(["REQ", "live", {"kinds": [1059], "#p": [PK_R]}])
        self.assertEqual(sub.recv_until_eose("live"), [])
        hit = fr.make_event(PK_A, 1059, "for R", [["p", PK_R]])
        miss = fr.make_event(PK_A, 1059, "for B", [["p", PK_B]])
        pub.publish(miss)
        pub.publish(hit)
        self.assertEqual(sub.recv(), ["EVENT", "live", hit])
        self.assertSilent(sub)

    def test_q5_close_stops_forwarding(self):
        sub, pub = self.client(), self.client()
        sub.send(["REQ", "x", {"kinds": [1059]}])
        sub.recv_until_eose("x")
        sub.send(["CLOSE", "x"])
        # 用同一條線再 REQ 一次當作「CLOSE 已處理」的界線(同一條線上的訊息依序處理)
        sub.send(["REQ", "y", {"kinds": [7]}])
        sub.recv_until_eose("y")
        pub.publish(fr.make_event(PK_A, 1059, "after close"))
        self.assertSilent(sub)

    def test_q6_same_sid_replaces(self):
        c = self.client()
        c.send(["REQ", "s", {"kinds": [1]}])
        c.recv_until_eose("s")
        c.send(["REQ", "s", {"kinds": [2]}])
        c.recv_until_eose("s")
        pub = self.client()
        pub.publish(fr.make_event(PK_A, 1, "kind1"))
        self.assertSilent(c)
        k2 = fr.make_event(PK_A, 2, "kind2")
        pub.publish(k2)
        self.assertEqual(c.recv(), ["EVENT", "s", k2])


class Replaceable(Base):
    def test_p1_replaceable_keeps_latest(self):
        c = self.client()
        old = fr.make_event(PK_A, 10050, "", [["relay", "ws://old"]], created_at=100)
        new = fr.make_event(PK_A, 10050, "", [["relay", "ws://new"]], created_at=200)
        c.publish(old)
        c.publish(new)
        stale = c.publish(fr.make_event(PK_A, 10050, "", [["relay", "ws://stale"]], created_at=150))
        self.assertTrue(stale[3].startswith("duplicate:"))
        c.send(["REQ", "r", {"kinds": [10050], "authors": [PK_A]}])
        self.assertEqual([e["id"] for e in c.recv_until_eose("r")], [new["id"]])
        # 30078 名冊:依 d 標籤各留最新
        r1 = fr.make_event(PK_A, 30078, "v1", [["d", "aib-roster"]], created_at=100)
        r2 = fr.make_event(PK_A, 30078, "v2", [["d", "aib-roster"]], created_at=200)
        other = fr.make_event(PK_A, 30078, "other", [["d", "something-else"]], created_at=50)
        for e in (r1, r2, other):
            c.publish(e)
        c.send(["REQ", "n", {"kinds": [30078], "#d": ["aib-roster"]}])
        self.assertEqual([e["content"] for e in c.recv_until_eose("n")], ["v2"])
        c.send(["REQ", "all", {"kinds": [30078]}])
        self.assertEqual(sorted(e["content"] for e in c.recv_until_eose("all")), ["other", "v2"])

    def test_p2_ephemeral_forwarded_not_stored(self):
        sub, pub = self.client(), self.client()
        sub.send(["REQ", "e", {"kinds": [20001]}])
        sub.recv_until_eose("e")
        ev = fr.make_event(PK_A, 20001, "presence")
        self.assertTrue(pub.publish(ev)[2])
        self.assertEqual(sub.recv(), ["EVENT", "e", ev])
        self.assertEqual(self.relay.events, [])


class Wire(Base):
    def test_w1_ping_pong(self):
        c = self.client()
        c.send_raw(9, b"hb")
        fin, op, masked, data = fr._read_frame(c.sock)
        self.assertEqual((op, masked, data), (10, False, b"hb"))

    def test_w2_large_message(self):
        c = self.client()
        ev = fr.make_event(PK_A, 1059, "x" * 70000)   # > 65535 ⇒ 64 位元長度欄
        self.assertTrue(c.publish(ev)[2])
        c.send(["REQ", "big", {"ids": [ev["id"]]}])
        self.assertEqual(c.recv_until_eose("big")[0]["content"], "x" * 70000)

    def test_w3_garbage_gets_notice_and_connection_survives(self):
        c = self.client()
        for raw in (b"not json", b"{}", b'["AUTH","x"]', b'["EVENT"]'):
            c.send_raw(1, raw)
            m = c.recv()
            self.assertEqual(m[0], "NOTICE", raw)
        self.assertTrue(c.publish(fr.make_event(PK_A, 1, "still alive"))[2])

    def test_w4_stop_closes_clients(self):
        c = fr.RelayClient(self.relay.url, timeout=2.0)
        self.relay.stop()
        try:
            got = c.recv(timeout=2.0)
        except (ConnectionError, OSError):
            got = None
        self.assertIsNone(got)
        c.sock.close()
        with self.assertRaises(OSError):
            fr.RelayClient(self.relay.url, timeout=1.0)
        self.relay = fr.FakeRelay().start()   # 讓 addCleanup 的 stop 有東西可停


class Knife(unittest.TestCase):
    def test_k1_without_client_guard_r3_goes_red(self):
        needle = "        assert_loopback(host)\n        self.sock = socket.create_connection("
        cut = "        self.sock = socket.create_connection("
        name = Path(__file__).name
        with knife_copy(REPO, "tests/aib_fake_relay.py", needle, cut, tests=(name,)) as root:
            r = subprocess.run([sys.executable, str(root / "tests" / name), "Guard.test_r3_client_refuses_non_loopback"],
                               cwd=str(root), capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
