#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""B1(2026-09-24,大綱 §101.4 第 2 波):桌面模型的 aib_dm 從**席**發出,身分可證。

問題:桌面 app 起的 MCP server 沒有 tmux 祖先鏈,它跑 `aib dm` 時 dm-ctl 只能把寄件人推成 `unattested:aib-dm-mcp`,
別席看不出是哪個 codexbot 席。修法:橋(跑在席 pane 裡的子行程)開一條本機 unix socket 收單,MCP 把 (target, message)
交給橋,橋在自己的行程裡跑 aib_dm_tool.run_aib_dm ⇒ dm-ctl 從 TMUX_PANE/行程鏈認出 @席碼。

契約(一連線一單,雙方各一行 JSON,UTF-8,\\n 結尾,單行 ≤ 64 KiB):
  請求 {"v": 1, "target": "@五碼", "message": "正文"}
  回覆 {"v": 1, "ok": true|false, "outcome": "DM 指令 rc=…:…"}      # outcome 原封是 run_aib_dm 的回字
  錯誤 {"v": 1, "ok": false, "error": "bad_request|too_large|runner_failed", "outcome": "<人話>"}
socket 路徑:<state root>/codexbot-bindings/<桌面 thread uuid>.dm.sock(每條對話一條;MCP 用 codexbot.env 的預設 thread 找)。
權限 0600;橋起來先清掉陳舊 socket 檔,收席時 unlink。
"""
from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path

VERSION = 1
MAX_LINE = 64 * 1024
SOCKET_SUFFIX = ".dm.sock"


# macOS 的 AF_UNIX 路徑上限:sun_path 104 bytes 含結尾 NUL ⇒ 最多 103(Linux 108 ⇒ 107;取嚴的那個)。
MAX_SOCKET_PATH = 103


def check_path_len(path):
    """10-09 a seat:路徑超過上限就先丟清楚的 OSError(幾個 byte、上限、哪個路徑)。
    以前 bind 只丟 OSError,橋只印「dm socket 開不起來(OSError)」⇒ 看不出是路徑太長;
    test-grokbot-identity / -mailbox 就這樣在主線上恆紅了兩天。正式環境 ~/.ez2-aib/<p>-bindings/<uuid>.dm.sock
    約 79 + 帳號名長度;帳號名超過約 24 字或狀態根放得很深時會撞到。"""
    n = len(os.fsencode(str(path)))
    if n > MAX_SOCKET_PATH:
        raise OSError("socket path is %d bytes, over the macOS limit of %d (use a shorter state root / "
                      "EZ2_AIB_STATE_ROOT): %s" % (n, MAX_SOCKET_PATH, path))


def socket_path(base, thread_id):
    return Path(base) / "codexbot-bindings" / (str(thread_id) + SOCKET_SUFFIX)


def outcome_ok(outcome):
    """跟 aib_dm_mcp 同一條判準:rc=0 且沒有「未送出」。"""
    return "rc=0" in (outcome or "") and "未送出" not in (outcome or "")


def _read_line(conn, limit=MAX_LINE):
    buf = bytearray()
    while len(buf) <= limit:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buf += chunk
        if b"\n" in chunk:
            break
    if len(buf) > limit:
        raise ValueError("too_large")
    return bytes(buf).split(b"\n", 1)[0].decode("utf-8")


def handle_request(raw, runner):
    """一行請求 → 一行回覆(dict)。runner(target, message) -> outcome 字串。純函式,尺直接打。"""
    try:
        req = json.loads(raw)
    except ValueError:
        return {"v": VERSION, "ok": False, "error": "bad_request", "outcome": "DM 未送出:請求不是 JSON。"}
    if not isinstance(req, dict) or req.get("v") != VERSION or not isinstance(req.get("target"), str) or not isinstance(req.get("message"), str):
        return {"v": VERSION, "ok": False, "error": "bad_request", "outcome": "DM 未送出:請求要有 v=1、target、message(字串)。"}
    try:
        outcome = runner(req["target"], req["message"]) or ""
    except Exception as exc:  # runner 炸了不能把橋炸掉
        return {"v": VERSION, "ok": False, "error": "runner_failed", "outcome": "DM 未送出:橋端送件失敗(%s)。" % str(exc)[:120]}
    return {"v": VERSION, "ok": outcome_ok(outcome), "outcome": outcome}


class DmSocketServer:
    """橋端。start() 之後在背景執行緒收單;stop() 收掉並 unlink。"""

    def __init__(self, path, runner, notice=lambda text: None):
        self.path, self.runner, self.notice = Path(path), runner, notice
        self.sock = None
        self.stop_event = threading.Event()
        self.thread = None
        self.served = 0

    def start(self):
        check_path_len(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.unlink()          # 陳舊 socket 檔(上一個橋被殺)
        except FileNotFoundError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self.sock.listen(8)
        self.sock.settimeout(0.5)
        self.thread = threading.Thread(target=self._loop, name="codexbot-dm-socket", daemon=True)
        self.thread.start()
        return self.path

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._serve(conn)
            except Exception as exc:
                self.notice("codexbot: dm socket 服務單失敗:%s" % str(exc)[:120])
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _serve(self, conn):
        conn.settimeout(60)
        try:
            raw = _read_line(conn)
        except ValueError:
            reply = {"v": VERSION, "ok": False, "error": "too_large", "outcome": "DM 未送出:請求超過 64 KiB。"}
        else:
            reply = handle_request(raw, self.runner)
        self.served += 1
        conn.sendall((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))

    def stop(self):
        self.stop_event.set()
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        if self.thread is not None:
            self.thread.join(timeout=2)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def send_via_bridge(path, target, message, timeout=50.0):
    """MCP 端。連不上就丟 OSError(呼叫端決定要不要退回直送)。回覆 dict。"""
    wire = (json.dumps({"v": VERSION, "target": target, "message": message}, ensure_ascii=False) + "\n").encode("utf-8")
    if len(wire) > MAX_LINE:
        return {"v": VERSION, "ok": False, "error": "too_large", "outcome": "DM 未送出:請求超過 64 KiB。"}
    check_path_len(path)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(path))
        s.sendall(wire)
        raw = _read_line(s)
    reply = json.loads(raw)
    if not isinstance(reply, dict) or reply.get("v") != VERSION:
        raise OSError("bridge replied with an unknown shape")
    return reply
