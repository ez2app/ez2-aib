#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""全球對談轉接程式的骨架:`aib chat on|off|status` 與常駐的 `run`(開發文件第 20 節;第 22.1 節 PR-5 = 第 1 期 1.1)。

Andy 10-15 22:5x 裁:轉接程式與 `aib chat` 放**開源核心**(不做成要 `aib ext install` 的外掛)。
核心這一支只用標準庫;連 Nostr 的那一段(之後的 1.2 起)才用 `~/.ez2-aib/chat/venv` 裡的 nostr-sdk,
主程式不 import nostr_sdk(第 20.3 節)。

一台一支、每艙一個窗:
  - 轉接程式是**一台機器一支**(所有席共用一個連線池)⇒ `<bridge root>/bridge.lock` 用 flock 搶;
  - 每一艙開艙時都開一個 `sys.chat` 窗跑 `run`:搶到鎖的當班,其他艙的排隊(每輪再試一次),
    當班的那支死了,鎖跟著放掉,下一支自動接手。
  - 開關檔 `~/.ez2-aib/chat/enabled.json`:`aib chat off` 刪掉它,當班與排隊的都在下一輪自己結束。

★ 這一版**不寫心跳**(第 20.3b 節):還沒有金鑰與中繼站,寫了心跳 dm-ctl 就會把信交給一個寄不出去的轉接程式
  (等滿逾時才撤回 rc 2)。不寫 ⇒ dm_global_files.bridge_alive() 是 False ⇒ 寄件端馬上照實回「全球對談沒開」。
  當班一上來還會刪掉殘留的 heartbeat.json,免得舊檔讓寄件端誤判。

檔案位置(都跟 dm_global_files 的契約同一個根,`AIB_CHAT_BRIDGE_DIR` 可改,測試用):
  <bridge root>/bridge.lock    flock
  <bridge root>/status.json    當班寫:state / pid / instance / since / at / swept_at(第 20.5 節的前身)
  ~/.ez2-aib/chat/enabled.json 開關(`AIB_CHAT_HOME` 可改)
  ~/.ez2-aib/chat/venv/        nostr-sdk 的 venv(`aib chat on` 第一次建;`--no-venv` 先不裝)
"""
import argparse
import fcntl
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dm_global_files as GF   # noqa: E402  契約:根目錄、清舊檔、心跳格式都用它的,不另寫一份
from atomic_json import write_json   # noqa: E402

VERSION = 1
NOSTR_SDK_PIN = "nostr-sdk==0.45.1"        # 第 20.3 節:版本釘死,升版要跑驗收 6 的冒名格
TICK_DEFAULT = 10                          # 秒;跟心跳間隔同一個節奏(dm_global_files:每 10 秒)
SWEEP_EVERY = 3600                         # 秒;清舊檔(receipts / delivery / bad 超過 7 天)
WINDOW_ROLE = "sys.chat"
WINDOW_NAME = "chat"
STATES = ("no_keys", "stopped")            # 1.1 只會有這兩種;1.2 起加 connecting / online / offline
SOCKET_RE = re.compile(r"^aihub(-[0-9A-Za-z-]+)?$")


def T(key, **params):
    try:
        import i18n_lib
        return i18n_lib.t("aib.chat." + key, params or None, locale=i18n_lib.resolve_locale())
    except Exception:   # noqa: BLE001  字典壞了也要講得出話:退回鍵名
        return "aib.chat." + key + (" " + json.dumps(params, ensure_ascii=False) if params else "")


# ── 位置 ──

def bridge_root(env=None):
    return GF.default_root(env)


def chat_home(env=None):
    env = os.environ if env is None else env
    explicit = env.get("AIB_CHAT_HOME")
    if explicit:
        return Path(explicit)
    return Path(env.get("HOME") or os.path.expanduser("~")) / ".ez2-aib" / "chat"


def switch_path(env=None):
    return chat_home(env) / "enabled.json"


def venv_python(env=None):
    return chat_home(env) / "venv" / "bin" / "python"


def is_enabled(env=None):
    try:
        rec = json.loads(switch_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(rec, dict) and rec.get("v") == VERSION and rec.get("on") is True


def _mkdir_private(d):
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)


# ── 狀態檔 ──

def read_status(root):
    try:
        rec = json.loads((Path(root) / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and rec.get("v") == VERSION else None


def write_status(root, **fields):
    rec = {"v": VERSION}
    rec.update(fields)
    write_json(str(Path(root) / "status.json"), rec)
    os.chmod(Path(root) / "status.json", 0o600)


def pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_is_bridge(pid):
    """這個 pid 真的是轉接程式(命令列有 chat_bridge.py run)?pid 被回收給別的程式時不能亂送信號。"""
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "chat_bridge.py" in out and " run" in out


# ── 常駐 ──

class _Stop(Exception):
    pass


def _try_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def run(env=None, tick=None, max_ticks=None, out=sys.stdout):
    """常駐迴圈。回 rc:0 = 開關關了或收到 SIGTERM 正常結束。max_ticks 只給測試用(跑幾輪就停)。"""
    env = os.environ if env is None else env
    tick = float(env.get("AIB_CHAT_TICK") or TICK_DEFAULT) if tick is None else tick
    root = GF.ensure_dirs(bridge_root(env))
    stop = {"now": False}

    def _on_term(_sig, _frm):
        stop["now"] = True
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)

    fd = os.open(str(root / "bridge.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    leader, said_standby, ticks = False, False, 0
    try:
        while True:
            if stop["now"] or not is_enabled(env):
                if leader:
                    st = read_status(root) or {}
                    write_status(root, state="stopped", pid=os.getpid(), instance=st.get("instance"),
                                 since=st.get("since"), at=int(time.time()), swept_at=st.get("swept_at"))
                out.write(T("run_stopped") + "\n")
                out.flush()
                return 0
            if not leader:
                leader = _try_lock(fd)
                if leader:
                    _become_leader(root, env, out)
                elif not said_standby:
                    st = read_status(root) or {}
                    out.write(T("run_standby", pid=st.get("pid", "?"), instance=st.get("instance") or "?") + "\n")
                    out.flush()
                    said_standby = True
            if leader:
                _leader_tick(root)
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                return 0
            _sleep(tick, stop)
    finally:
        os.close(fd)        # 關檔 = 放鎖,排隊的下一支接手


def _sleep(seconds, stop):
    end = time.time() + seconds
    while not stop["now"] and time.time() < end:
        time.sleep(min(0.2, max(0.0, end - time.time())))


def _become_leader(root, env, out):
    # 還沒有金鑰 ⇒ 不寫心跳;殘留的舊心跳刪掉,寄件端才會照實回「全球對談沒開」
    try:
        (Path(root) / "heartbeat.json").unlink()
    except FileNotFoundError:
        pass
    now = int(time.time())
    write_status(root, state="no_keys", pid=os.getpid(), instance=env.get("EZ2_AIB_INSTANCE") or None,
                 since=now, at=now, swept_at=None)
    out.write(T("run_leader", pid=os.getpid()) + "\n")
    out.flush()


def _leader_tick(root):
    st = read_status(root) or {}
    now = int(time.time())
    swept = st.get("swept_at")
    if not isinstance(swept, int) or now - swept >= SWEEP_EVERY:
        GF.sweep(root, now=now)
        swept = now
    write_status(root, state="no_keys", pid=os.getpid(), instance=st.get("instance"),
                 since=st.get("since") or now, at=now, swept_at=swept)


# ── sys.chat 窗 ──

def hub_socket(env=None):
    """這一艙的 tmux socket 名:EZ2_AIHUB_SOCKET,沒有就從 $TMUX 認 aihub 形的。認不到回 None。"""
    env = os.environ if env is None else env
    for cand in (env.get("EZ2_AIHUB_SOCKET") or "", os.path.basename((env.get("TMUX") or "").split(",")[0])):
        if cand and SOCKET_RE.match(cand):
            return cand
    return None


def window_command(env=None):
    env = os.environ if env is None else env
    keep = ["HOME", "PATH", "LANG", "EZ2_AIB_INSTANCE", "EZ2_AIB_INSTANCE_HOME", "EZ2_AIHUB_SOCKET", "EZ2_AIB_ROOT",
            "EZ2_AIB_LOCALE", "AIB_CHAT_HOME", "AIB_CHAT_BRIDGE_DIR", "AIB_CHAT_TICK"]
    pairs = ["%s=%s" % (k, shlex.quote(env[k])) for k in keep if env.get(k)]
    return "exec env %s %s %s run" % (" ".join(pairs), shlex.quote(sys.executable), shlex.quote(str(HERE / "chat_bridge.py")))


def ensure_window(socket, env=None, tmux="tmux"):
    """艙裡有 sys.chat 窗 ⇒ 活著不動、死了 respawn;沒有才開。回 (動作, window_id);動作:kept / respawned / created / failed。"""
    def tx(*args):
        return subprocess.run([tmux, "-L", socket] + list(args), capture_output=True, text=True, timeout=10)
    cmd = window_command(env)
    r = tx("list-windows", "-t", "aihub", "-F", "#{window_id} #{@aib_role} #{pane_dead}")
    if r.returncode != 0:
        return "failed", None
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == WINDOW_ROLE:
            wid = parts[0]
            if parts[-1] == "1":
                ok = tx("respawn-pane", "-k", "-t", wid, cmd).returncode == 0
                return ("respawned" if ok else "failed"), wid
            return "kept", wid
    r = tx("new-window", "-d", "-t", "aihub", "-n", WINDOW_NAME, "-P", "-F", "#{window_id}", cmd)
    wid = r.stdout.strip()
    if r.returncode != 0 or not wid.startswith("@"):
        return "failed", None
    tx("set-option", "-w", "-t", wid, "@aib_role", WINDOW_ROLE)
    tx("set-option", "-w", "-t", wid, "remain-on-exit", "on")
    return "created", wid


# ── venv ──

def venv_state(env=None):
    """ok = venv 在、import 得到 nostr_sdk;missing = 還沒建;broken = 建了但 import 不到。"""
    py = venv_python(env)
    if not py.exists():
        return "missing"
    r = subprocess.run([str(py), "-c", "import nostr_sdk"], capture_output=True, timeout=60)
    return "ok" if r.returncode == 0 else "broken"


def ensure_venv(env=None, out=sys.stdout):
    """建 venv 並裝釘死版本的 nostr-sdk(要網路,約 3 分鐘)。回 True = 能用。"""
    home = chat_home(env)
    _mkdir_private(home)
    if venv_state(env) == "ok":
        return True
    out.write(T("venv_installing", pin=NOSTR_SDK_PIN) + "\n")
    out.flush()
    if not venv_python(env).exists():
        if subprocess.run([sys.executable, "-m", "venv", str(home / "venv")]).returncode != 0:
            return False
    r = subprocess.run([str(venv_python(env)), "-m", "pip", "install", "--disable-pip-version-check", "-q", NOSTR_SDK_PIN])
    return r.returncode == 0 and venv_state(env) == "ok"


# ── 子指令 ──

def cmd_on(a, env, out):
    if not a.no_venv and not ensure_venv(env, out):
        out.write(T("venv_failed", pin=NOSTR_SDK_PIN, path=str(chat_home(env) / "venv")) + "\n")
        return 2
    _mkdir_private(chat_home(env))
    write_json(str(switch_path(env)), {"v": VERSION, "on": True, "at": int(time.time())})
    os.chmod(switch_path(env), 0o600)
    out.write(T("on_done") + "\n")
    sock = hub_socket(env)
    if sock:
        act, wid = ensure_window(sock, env)
        out.write(T("window_" + act, wid=wid or "-", socket=sock) + "\n")
    else:
        out.write(T("window_no_hub") + "\n")
    out.write(T("no_keys_yet") + "\n")
    return 0


def cmd_off(a, env, out):
    try:
        switch_path(env).unlink()
    except FileNotFoundError:
        pass
    root = bridge_root(env)
    st = read_status(root) or {}
    pid = st.get("pid")
    if st.get("state") != "stopped" and pid_alive(pid) and _pid_is_bridge(pid):
        os.kill(pid, signal.SIGTERM)
        end = time.time() + 5
        while time.time() < end and pid_alive(pid):
            time.sleep(0.1)
    out.write(T("off_done") + "\n")
    return 0


def status_record(env):
    root = bridge_root(env)
    st = read_status(root) or {}
    pid = st.get("pid")
    running = st.get("state") not in (None, "stopped") and pid_alive(pid)
    return {
        "enabled": is_enabled(env),
        "running": running,
        "state": st.get("state") if running else ("stopped" if st else None),
        "pid": pid if running else None,
        "instance": st.get("instance") if running else None,
        "at": st.get("at"),
        "heartbeat": GF.bridge_alive(root),
        "venv": venv_state(env),
        "bridge_root": str(root),
    }


def cmd_status(a, env, out):
    rec = status_record(env)
    if a.json:
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return 0
    out.write(T("status_switch_" + ("on" if rec["enabled"] else "off")) + "\n")
    if rec["running"]:
        out.write(T("status_running", pid=rec["pid"], instance=rec["instance"] or "-") + "\n")
    else:
        out.write(T("status_not_running") + "\n")
    out.write(T("status_venv_" + rec["venv"], pin=NOSTR_SDK_PIN) + "\n")
    out.write(T("status_heartbeat_" + ("yes" if rec["heartbeat"] else "no")) + "\n")
    return 0


def main(argv=None, env=None, out=sys.stdout):
    env = os.environ if env is None else env
    p = argparse.ArgumentParser(prog="aib chat", description=T("help.description"))
    sub = p.add_subparsers(dest="cmd")
    on = sub.add_parser("on", help=T("help.on"))
    on.add_argument("--no-venv", action="store_true", help=T("help.no_venv"))
    sub.add_parser("off", help=T("help.off"))
    st = sub.add_parser("status", help=T("help.status"))
    st.add_argument("--json", action="store_true")
    sub.add_parser("run", help=T("help.run"))
    ew = sub.add_parser("ensure-window", help=T("help.ensure_window"))
    ew.add_argument("--socket")
    sub.add_parser("enabled", help=T("help.enabled"))
    a = p.parse_args(argv)
    if a.cmd in (None, "status"):
        if a.cmd is None:
            a.json = False
        return cmd_status(a, env, out)
    if a.cmd == "on":
        return cmd_on(a, env, out)
    if a.cmd == "off":
        return cmd_off(a, env, out)
    if a.cmd == "run":
        return run(env, out=out)
    if a.cmd == "enabled":
        return 0 if is_enabled(env) else 1
    if a.cmd == "ensure-window":
        sock = a.socket or hub_socket(env)
        if not sock or not SOCKET_RE.match(sock):
            out.write(T("window_no_hub") + "\n")
            return 2
        act, wid = ensure_window(sock, env)
        out.write(T("window_" + act, wid=wid or "-", socket=sock) + "\n")
        return 0 if act != "failed" else 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
