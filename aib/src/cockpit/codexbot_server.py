#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Start or reuse AIB's local Codex app-server on a private Unix socket."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time


class ServerError(RuntimeError):
    pass


def _accepting(path: Path) -> bool:
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.25)
    try:
        probe.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _atomic_text(path: Path, text: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".codexbot-server-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def server_dir() -> Path:
    override = os.environ.get("AIB_CODEXBOT_SERVER_DIR")
    if override:
        return Path(override).expanduser()
    state = Path(os.environ.get("EZ2_AIB_HOME") or Path.home() / ".ez2-aib")
    return state / "run" / "codexbot"


UNLOAD_DELAY_ENV = "AIB_CODEXBOT_UNLOAD_DELAY_SECS"
UNLOAD_DELAY_DEFAULT = 5
SERVER_CWD_ENV = "AIB_CODEXBOT_SERVER_CWD"     # 治具用;正常不設 ⇒ 家目錄
META_NAME = "app-server.json"                 # {pid, started_epoch, cwd}:健康檢查與橋重連要看


def server_cwd() -> Path:
    """App Server 的工作目錄:**固定家目錄**,不繼承呼叫者的。

    2026-10-07 00:0x a seat 真機:server 10-05 從 ~/wt-vault-convert-c79aa 開席者的工作樹起來,
    那棵樹後來被砍;server 活著、socket 通、席 status 說 connected,但任何 thread/resume 都回
    「failed to load configuration: No such file or directory」—— codex 以 cwd 讀專案層設定,cwd 不在就死。
    以前 Popen 沒給 cwd= ⇒ 誰開席就繼承誰的樹(與「開席入口指錯樹」同一族)。家目錄不會被砍。
    """
    override = os.environ.get(SERVER_CWD_ENV, "").strip()
    return Path(override).expanduser() if override else Path.home()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_cwd(pid: int):
    """行程的工作目錄;量不到回 None(macOS 沒有 /proc,走 lsof -Fn)。"""
    proc_link = Path("/proc") / str(pid) / "cwd"
    if proc_link.exists():
        try:
            return str(proc_link.resolve())
        except OSError:
            return None
    lsof = shutil.which("lsof") or next((p for p in ("/usr/sbin/lsof", "/usr/bin/lsof") if os.access(p, os.X_OK)), None)   # 10-11:Mac 的 lsof 在 /usr/sbin,PATH 不一定有
    if not lsof:
        return None
    try:
        out = subprocess.run([lsof, "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.stdout.splitlines():
        if line.startswith("n") and len(line) > 1:
            return line[1:]
    return None


def _read_pid(root: Path):
    try:
        raw = (root / "app-server.pid").read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except (OSError, ValueError):
        return None


def read_meta(root: Path | None = None) -> dict:
    root = root or server_dir()
    try:
        data = json.loads((root / META_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def server_health(root: Path | None = None) -> dict:
    """現況一格:{pid, alive, cwd, cwd_exists, accepting, healthy}。量不到的欄位是 None,不猜。
    healthy = socket 通 且 (量不到 cwd 或 cwd 還在)。cwd 量不到不判死:判死要有證據。"""
    root = root or server_dir()
    sock = root / "app-server.sock"
    pid = _read_pid(root)
    alive = _pid_alive(pid) if pid else None
    cwd = _pid_cwd(pid) if (pid and alive) else None
    cwd_exists = (os.path.isdir(cwd) if cwd else None)
    accepting = _accepting(sock)
    return {"pid": pid, "alive": alive, "cwd": cwd, "cwd_exists": cwd_exists,
            "accepting": accepting, "socket": str(sock),
            "healthy": bool(accepting and cwd_exists is not False)}


def _stop(pid: int, grace: float = 5.0) -> None:
    """關掉舊 server:TERM,等 grace 秒,還在就 KILL。"""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def unload_delay_args() -> list[str]:
    """`-c thread_unload_delay_secs=N`:讓這個 server 學會**放手**。

    2026-09-24 a seat 實測:沒有它,席 /quit、thread/unsubscribe、客戶端斷線之後,
    server 仍把 thread 留在記憶體(thread/loaded/list 還列著)⇒ 桌面 ChatGPT 永遠拿不回
    寫入權(畫面停在「已在另一個應用程式中開啟」)。帶了 5 秒:unsubscribe +6 秒 loaded 清空,
    桌面按「重試」就能接回。同一條 thread 只准一個寫入者是 OpenAI 那端的規則;這裡只是
    讓 AIB 這一端不要抱著不放。=0 關掉(治具/除錯用)。
    """
    raw = os.environ.get(UNLOAD_DELAY_ENV, "").strip()
    try:
        secs = int(raw) if raw else UNLOAD_DELAY_DEFAULT
    except ValueError:
        secs = UNLOAD_DELAY_DEFAULT
    if secs <= 0:
        return []
    return ["-c", "thread_unload_delay_secs=%d" % secs]


def ensure_server(codex_bin: str = "") -> str:
    root = server_dir()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    sock = root / "app-server.sock"
    lock_path = root / "start.lock"
    with lock_path.open("a+") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        if _accepting(sock):
            # 活著不等於健康:工作目錄被砍的 server socket 照通、thread/resume 必死(見 server_cwd)。
            # 只在**量得到 cwd 且它真的不在**時才重起;量不到就當好的,不殺沒證據的行程。
            health = server_health(root)
            if health["cwd_exists"] is False and health["pid"]:
                print("codexbot-server: 舊 server(pid %s)的工作目錄已不在(%s),重起" % (health["pid"], health["cwd"]),
                      file=sys.stderr, flush=True)
                _stop(health["pid"])
            else:
                return "unix://" + str(sock)
        if sock.exists() or sock.is_symlink():
            sock.unlink()
        executable = codex_bin or os.environ.get("AIB_CODEXBOT_CODEX_BIN", "") or shutil.which("codex")
        if not executable:
            raise ServerError("找不到 codex CLI，無法啟動本機 App Server")
        log_path = root / "app-server.log"
        log = log_path.open("ab", buffering=0)
        try:
            cwd = server_cwd()
            process = subprocess.Popen(
                [executable, *unload_delay_args(), "app-server", "--listen", "unix://" + str(sock)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, close_fds=True, cwd=str(cwd),
            )
        finally:
            log.close()
        _atomic_text(root / "app-server.pid", str(process.pid) + "\n")
        _atomic_text(root / META_NAME, json.dumps({"pid": process.pid, "started_epoch": time.time(),
                                                   "cwd": str(cwd)}) + "\n")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if _accepting(sock):
                return "unix://" + str(sock)
            rc = process.poll()
            if rc is not None:
                raise ServerError(f"Codex App Server 啟動失敗(rc={rc})；詳見 {log_path}")
            time.sleep(0.1)
        process.terminate()
        raise ServerError(f"Codex App Server 啟動逾時；詳見 {log_path}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("ensure", "health"))
    parser.add_argument("--codex-bin", default="")
    args = parser.parse_args(argv)
    if args.command == "health":
        health = server_health()
        print(json.dumps(health, ensure_ascii=False, sort_keys=True))
        return 0 if health["healthy"] else 2
    try:
        print(ensure_server(args.codex_bin))
        return 0
    except ServerError as exc:
        print(f"codexbot-server: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
