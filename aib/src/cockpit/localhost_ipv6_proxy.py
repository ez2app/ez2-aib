#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""Forward TCP [::1]:PORT -> 127.0.0.1:PORT (Chrome localhost IPv6 → Codex IPv4)."""
from __future__ import annotations

import argparse
import socket
import threading


def pipe(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for x in (a, b):
            try:
                x.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                x.close()
            except OSError:
                pass


def handle(cs: socket.socket, port: int) -> None:
    try:
        us = socket.create_connection(("127.0.0.1", port), timeout=5)
    except OSError:
        try:
            cs.close()
        except OSError:
            pass
        return
    threading.Thread(target=pipe, args=(cs, us), daemon=True).start()
    pipe(us, cs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=1455)
    args = ap.parse_args()
    port = int(args.port)
    srv = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("::1", port))
    srv.listen(50)
    while True:
        cs, _ = srv.accept()
        threading.Thread(target=handle, args=(cs, port), daemon=True).start()


if __name__ == "__main__":
    raise SystemExit(main())
