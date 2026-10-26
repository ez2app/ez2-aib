#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib chat-mcp — AIB 對話 MCP 伺服器(P0 本機版)。

    aib chat-mcp serve [--port 47820]        前景跑(只聽 127.0.0.1)
    aib chat-mcp start|stop|status           背景起停
    aib chat-mcp key new --principal P --seat 艙/席 [--allow 艙/席 ...] [--session UUID] [--scope S ...]
                                             本機測試金鑰(只印一次;庫內只存雜湊;90 天)+ binding
    aib chat-mcp key revoke --principal P    撤銷該 principal 全部金鑰
    aib chat-mcp binding list | revoke BINDING_ID
"""
import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chat_mcp import core, server  # noqa: E402


def _pidfile():
    return core.home_dir() / "server.pid"


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _port_free(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aib chat-mcp")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("serve", "start"):
        p = sub.add_parser(c)
        p.add_argument("--port", type=int, default=47820)
    sub.add_parser("stop")
    sub.add_parser("status")
    k = sub.add_parser("key").add_subparsers(dest="kcmd", required=True)
    kn = k.add_parser("new")
    kn.add_argument("--principal", required=True)
    kn.add_argument("--seat", required=True)
    kn.add_argument("--allow", action="append", default=[])
    kn.add_argument("--session")
    kn.add_argument("--scope", action="append")
    kr = k.add_parser("revoke")
    kr.add_argument("--principal", required=True)
    b = sub.add_parser("binding").add_subparsers(dest="bcmd", required=True)
    b.add_parser("list")
    br = b.add_parser("revoke")
    br.add_argument("binding_id")
    a = ap.parse_args(argv)

    if a.cmd == "serve":
        if not _port_free(a.port):
            print("埠 %d 已被占用" % a.port, file=sys.stderr)
            return 2
        httpd, _ = server.build("127.0.0.1", a.port)
        print("aib-chat-mcp listening http://127.0.0.1:%d/mcp" % a.port, flush=True)
        signal.signal(signal.SIGTERM, lambda *x: (_ for _ in ()).throw(KeyboardInterrupt))
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    if a.cmd == "start":
        pf = _pidfile()
        if pf.exists() and _alive(int(pf.read_text() or 0)):
            print("已在跑 pid %s" % pf.read_text())
            return 0
        if not _port_free(a.port):
            print("埠 %d 已被占用" % a.port, file=sys.stderr)
            return 2
        core.home_dir().mkdir(parents=True, exist_ok=True)
        log = os.fdopen(os.open(str(core.home_dir() / "server.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "ab")
        p = subprocess.Popen([sys.executable, __file__, "serve", "--port", str(a.port)], stdout=log, stderr=log,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        pf.write_text(str(p.pid))
        time.sleep(0.5)
        print("started pid %d" % p.pid if p.poll() is None else "啟動失敗,見 server.log")
        return 0 if p.poll() is None else 1
    if a.cmd == "stop":
        pf = _pidfile()
        if pf.exists() and _alive(int(pf.read_text() or 0)):
            os.kill(int(pf.read_text()), signal.SIGTERM)
            pf.unlink()
            print("stopped")
        else:
            print("未在跑")
        return 0
    if a.cmd == "status":
        pf = _pidfile()
        up = pf.exists() and _alive(int(pf.read_text() or 0))
        print("running pid %s" % pf.read_text() if up else "stopped")
        return 0
    st = core.Store()
    if a.cmd == "key" and a.kcmd == "new":
        if st.active_binding(a.principal) is None:
            st.bind(a.principal, a.seat, a.allow, tuple(a.scope or core.SCOPES), a.session, "cli key new (local test)")
        raw = st.new_key(a.principal)
        print("本機測試金鑰(只顯示這一次,勿貼進聊天):\n" + raw)
        return 0
    if a.cmd == "key" and a.kcmd == "revoke":
        print("revoked %d" % st.revoke_keys(a.principal))
        return 0
    if a.cmd == "binding" and a.bcmd == "list":
        for r in st.q("SELECT binding_id,principal_id,seat,allowlist,scopes,revision,state FROM bindings"):
            print("\t".join(map(str, r)))
        return 0
    if a.cmd == "binding" and a.bcmd == "revoke":
        print("revoked %d" % st.revoke_binding(a.binding_id))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
