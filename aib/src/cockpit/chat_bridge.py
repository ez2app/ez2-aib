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

★ 心跳只由接上中繼站的寄件工人(venv 的 chat_nostr.py serve)寫(第 20.3b 節):沒金鑰、沒中繼站時寫了心跳,
  dm-ctl 就會把信交給一個寄不出去的轉接程式(等滿逾時才撤回 rc 2)。沒心跳 ⇒ dm_global_files.bridge_alive() 是 False
  ⇒ 寄件端馬上照實回「全球對談沒開」。當班一上來還會刪掉殘留的 heartbeat.json,免得舊檔讓寄件端誤判。

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
STATES = ("no_keys", "no_profile", "no_relays", "publishing", "published", "stopped")
#   1.2:有金鑰還沒中繼站 = no_relays;1.3:沒設顯示名稱 / 國別 = no_profile、名冊發不出去 = publishing(下一輪再試)、
#   名冊與 10050 都發出去了 = published。心跳由寄件工人寫(接上中繼站才寫)。
WORKER_RESTART_GAP = 30                    # 秒;寄件工人掛了多久後再起(不狂起)
_WORKER = {"proc": None, "started": 0.0}   # 當班才有;寄件工人(venv 的 chat_nostr.py serve)
PUBLISH_EVERY = 60                         # 秒;名冊發失敗時多久再試(第 19.1 節:每站每 10 秒最多 2 則,不能狂發)
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
                _leader_tick(root, env)
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                return 0
            _sleep(tick, stop, every=(lambda: import_inbox(root, env)) if leader else None)
    finally:
        stop_worker()       # 工人跟著當班走(它看到父行程不在也會自己結束)
        os.close(fd)        # 關檔 = 放鎖,排隊的下一支接手


IMPORT_EVERY = 1.0                         # 秒;全球來信匯入佇列(寄件端等 delivered 最多 20 秒,不能等一整輪)


def _sleep(seconds, stop, every=None):
    end, nxt = time.time() + seconds, 0.0
    while not stop["now"] and time.time() < end:
        if every is not None and time.time() >= nxt:
            every()
            nxt = time.time() + IMPORT_EVERY
        time.sleep(min(0.2, max(0.0, end - time.time())))


def import_inbox(root, env):
    """當班才做:inbox → dm 佇列(global_inbox.py,核心規則)。出錯只記種類,不讓轉接程式死。"""
    try:
        import global_inbox
        global_inbox.import_once(root, env)
    except Exception as err:   # noqa: BLE001
        sys.stderr.write("import_inbox:%s\n" % type(err).__name__)


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


def sync_keys(env=None):
    """主人金鑰 + 依活席發 / 轉 / 封存席金鑰(1.2,第 5.3 節)。回 (動作清單, 錯誤鍵或 None)。
    「綁席時由轉接程式產生」:當班每一輪都對一次,新綁的席下一輪就有金鑰。私鑰不經過這裡的回傳值。"""
    import nostr_keys as NK
    try:
        NK.ensure_owner(env)
        return NK.sync_seats(NK.live_seats(env=env), env), None
    except Exception as err:   # noqa: BLE001  金鑰出錯不得讓轉接程式死掉;狀態檔記錯誤種類(不記內容)
        return [], type(err).__name__


def _leader_tick(root, env=None):
    st = read_status(root) or {}
    now = int(time.time())
    swept = st.get("swept_at")
    if not isinstance(swept, int) or now - swept >= SWEEP_EVERY:
        GF.sweep(root, now=now)
        swept = now
    _acts, key_err = sync_keys(env)
    state, pub = ("no_keys", {}) if key_err else publish_roster(root, env, st, now)
    worker = None if key_err else ensure_worker(root, env, now)
    write_status(root, state=state, pid=os.getpid(), instance=st.get("instance"),
                 since=st.get("since") or now, at=now, swept_at=swept, key_error=key_err,
                 publish_tried_at=pub.get("tried_at", st.get("publish_tried_at")), publish_error=pub.get("error"),
                 worker_pid=worker)


def ensure_worker(root, env, now):
    """寄件工人(1.7):venv 的 chat_nostr.py serve 常駐,領 outbox、寄、寫回條與心跳。
    心跳只由它寫 ⇒「有心跳」就等於「真的寄得出去」。venv 沒裝 ⇒ 不起(寄件端照實回全球對談沒開)。回工人 pid 或 None。"""
    p = _WORKER["proc"]
    if p is not None and p.poll() is None:
        return p.pid
    py = venv_python(env)
    if not py.exists() or now - _WORKER["started"] < WORKER_RESTART_GAP:
        return None
    log = Path(root) / "worker.log"
    fd = os.open(str(log), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)   # 工人只記錯誤種類,不記內容
    try:
        _WORKER["proc"] = subprocess.Popen([str(py), str(HERE / "chat_nostr.py"), "serve", "--root", str(root)],
                                           stdin=subprocess.DEVNULL, stdout=fd, stderr=fd, env=dict(env))
    finally:
        os.close(fd)
    _WORKER["started"] = now
    return _WORKER["proc"].pid


def stop_worker():
    p = _WORKER["proc"]
    if p is None or p.poll() is not None:
        return
    p.terminate()
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()


def _run_nostr(cmd, root, env, timeout=60, extra=()):
    """venv 那一半(chat_nostr.py)當子行程跑。回 (rc, 結果 dict)。venv 不能用 ⇒ (None, {})。"""
    py = venv_python(env)
    if not py.exists():
        return None, {}
    try:
        r = subprocess.run([str(py), str(HERE / "chat_nostr.py"), cmd] + list(extra) + ["--root", str(root)],
                           capture_output=True, text=True, timeout=timeout, env=dict(env))
    except (OSError, subprocess.SubprocessError) as err:
        return 2, {"why": type(err).__name__}
    try:
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return r.returncode, {"why": "no_output"}


def publish_roster(root, env, st, now):
    """組名冊 → 內容變了就交給 venv 發(最多每 PUBLISH_EVERY 秒試一次)。回 (狀態, {tried_at, error})。"""
    import chat_roster as CR
    try:
        content, dm_seats = CR.build(env)
    except CR.RosterError as err:
        return ("no_profile" if str(err) == "no_profile" else "no_keys"), {}
    h, _changed = CR.write_outgoing(root, content, dm_seats)
    if CR.published_hash(root) == h:
        return "published", {}
    tried = st.get("publish_tried_at")
    if isinstance(tried, int) and now - tried < PUBLISH_EVERY:
        return "publishing", {"tried_at": tried, "error": st.get("publish_error")}
    rc, rec = _run_nostr("publish", root, env)
    if rc is None:
        return "no_relays", {}                       # 沒裝 nostr-sdk(--no-venv):名冊組好了,發不出去
    if rc == 0:
        return "published", {"tried_at": now}
    return "publishing", {"tried_at": now, "error": rec.get("why") or "relays"}


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
    import chat_roster as CR
    # 第 5.8、5.9 節:第一次開一定要有顯示名稱與國別(沒設不開)
    if a.name is not None or a.country is not None:
        try:
            CR.set_profile(a.name, a.country, env)
        except CR.RosterError as err:
            out.write(T("profile_" + str(err)) + "\n")
            return 2
    if not CR.profile_ready(env):
        out.write(T("profile_required") + "\n")
        return 2
    if not a.no_venv and not ensure_venv(env, out):
        out.write(T("venv_failed", pin=NOSTR_SDK_PIN, path=str(chat_home(env) / "venv")) + "\n")
        return 2
    _mkdir_private(chat_home(env))
    write_json(str(switch_path(env)), {"v": VERSION, "on": True, "at": int(time.time())})
    os.chmod(switch_path(env), 0o600)
    out.write(T("on_done") + "\n")
    acts, key_err = sync_keys(env)
    if key_err:
        out.write(T("keys_error", err=key_err) + "\n")
    else:
        import nostr_keys as NK
        pv = NK.public_view(env)
        out.write(T("keys_ready", n=sum(1 for e in pv["seats"] if e["status"] == "active")) + "\n")
    sock = hub_socket(env)
    if sock:
        act, wid = ensure_window(sock, env)
        out.write(T("window_" + act, wid=wid or "-", socket=sock) + "\n")
    else:
        out.write(T("window_no_hub") + "\n")
    out.write(T("public_hint") + "\n")
    return 0


def cmd_prepare(a, env, out):
    """先備好全球對談,但**不上線**(維護者 10-16:安裝時就裝好 Nostr 的程式、第一艦長開席就有身分):
    裝 venv + nostr-sdk(--keys-only 略過)、產生主人金鑰、替已綁人格的席發席金鑰。全部只在本機:
    不連中繼站、不發名冊、不開開關。要上線仍是 aib chat on --name … --country …(會公開顯示名稱與國別)。
    AIB_CHAT_PREINSTALL=0 ⇒ 什麼都不做。任何一步失敗只回報,rc 0(不擋安裝與開艙)。"""
    if (env.get("AIB_CHAT_PREINSTALL") or "1") == "0":
        return 0
    if not a.keys_only:
        if venv_state(env) == "ok":
            out.write(T("prepare_venv_ok", pin=NOSTR_SDK_PIN) + "\n")
        elif ensure_venv(env, out):
            out.write(T("prepare_venv_ok", pin=NOSTR_SDK_PIN) + "\n")
        else:
            out.write(T("prepare_venv_failed", pin=NOSTR_SDK_PIN) + "\n")
    acts, key_err = sync_keys(env)
    if key_err:
        out.write(T("keys_error", err=key_err) + "\n")
    elif not a.quiet or acts:
        import nostr_keys as NK
        pv = NK.public_view(env)
        out.write(T("prepare_keys", n=sum(1 for e in pv["seats"] if e["status"] == "active")) + "\n")
    if not is_enabled(env) and not a.quiet:
        out.write(T("prepare_next") + "\n")
    return 0


def cmd_off(a, env, out):
    try:
        switch_path(env).unlink()
    except FileNotFoundError:
        pass
    root = bridge_root(env)
    # 第 20.4 節:發空名冊、撤下 10050(發過才需要;沒有 venv 就沒發過)
    import chat_roster as CR
    if CR.published_hash(root) not in (None, "empty"):
        rc, _rec = _run_nostr("publish-empty", root, env, timeout=30)
        out.write(T("off_unpublished" if rc == 0 else "off_unpublish_failed") + "\n")
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
        "keys": _keys_summary(env),
        "worker": _worker_status(root, running),
        "bridge_root": str(root),
    }


def _worker_status(root, running):
    """寄件 / 收件工人寫的 worker-status.json(1.9);太舊(工人沒在跑)回 None。"""
    try:
        w = json.loads((Path(root) / "worker-status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not running or not isinstance(w, dict) or time.time() - int(w.get("at") or 0) > 60:
        return None
    return w


def _keys_summary(env):
    import nostr_keys as NK
    pv = NK.public_view(env)
    return {"owner": pv["owner"], "seats": sum(1 for e in pv["seats"] if e["status"] == "active")}


def cmd_keys(a, env, out):
    """只印公開的東西(npub、席、艙、狀態);私鑰不經過這裡。"""
    import nostr_keys as NK
    pv = NK.public_view(env)
    if a.json:
        out.write(json.dumps(pv, ensure_ascii=False) + "\n")
        return 0
    out.write(T("keys_owner", npub=pv["owner"] or "-") + "\n")
    for e in pv["seats"]:
        if e["status"] == "active" or a.all:
            out.write(T("keys_row_" + ("active" if e["status"] == "active" else "retired"),
                        seat=e["seat5"], cabin=e["cabin"] or "-", uid=e["persona_uid"], npub=e["npub"]) + "\n")
    return 0


def cmd_profile(a, env, out):
    import chat_roster as CR
    try:
        if a.cmd == "name":
            CR.set_profile(name=a.value, env=env)
        elif a.cmd == "country":
            CR.set_profile(country=a.value, env=env)
        else:
            CR.set_public(a.value, a.cmd == "publish", env)
    except CR.RosterError as err:
        out.write(T("profile_" + str(err)) + "\n")
        return 2
    out.write(T("profile_saved_" + a.cmd, value=a.value) + "\n")
    return 0


def cmd_roster(a, env, out):
    """印自己的名冊內容(還沒簽的那份;公開資訊)。"""
    import chat_roster as CR
    try:
        content, _seats = CR.build(env)
    except CR.RosterError as err:
        out.write(T("profile_" + str(err)) + "\n")
        return 2
    if a.json:
        out.write(json.dumps(content, ensure_ascii=False) + "\n")
        return 0
    out.write(T("roster_head", name=content["display_name"], country=content["country"],
                n=sum(len(c["seats"]) for c in content["cabins"])) + "\n")
    for c in content["cabins"]:
        for s_ in c["seats"]:
            out.write(T("roster_row", cabin=c["cabin"], seat=s_["seat"], name=s_.get("name") or "-", npub=s_["npub"]) + "\n")
    out.write(T("roster_relays", relays=", ".join(content["dm_relays"])) + "\n")
    return 0


def cmd_lookup(a, env, out):
    """查對方的全球位址(第 7.3 節 --dry-run 的底):抓名冊、驗簽、找席、抓 10050;不寄。"""
    rc, rec = _run_nostr("fetch", bridge_root(env), env, timeout=60, extra=[a.addr])
    if rc is None:
        out.write(T("lookup_no_venv", pin=NOSTR_SDK_PIN) + "\n")
        return 2
    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return 0 if rc == 0 else 2


def cmd_status(a, env, out):
    rec = status_record(env)
    if a.json:
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return 0
    out.write(T("status_switch_" + ("on" if rec["enabled"] else "off")) + "\n")
    if rec["running"]:
        out.write(T("status_running", pid=rec["pid"], instance=rec["instance"] or "-") + "\n")
        if rec["state"] in ("no_keys", "no_profile", "no_relays", "publishing", "published"):
            out.write(T("status_roster_" + rec["state"]) + "\n")
    else:
        out.write(T("status_not_running") + "\n")
    out.write(T("status_venv_" + rec["venv"], pin=NOSTR_SDK_PIN) + "\n")
    out.write(T("status_keys", n=rec["keys"]["seats"], owner=rec["keys"]["owner"] or "-") + "\n")
    w = rec.get("worker")
    if w:
        out.write(T("status_offline" if w.get("offline") else "status_relays", up=w.get("up", 0), total=w.get("total", 0)) + "\n")
        t = w.get("today") or {}
        out.write(T("status_today", sent=t.get("sent", 0), received=t.get("received", 0), failed=t.get("failed", 0),
                    pending=w.get("pending", 0)) + "\n")
        c = w.get("clock") or {}
        if c.get("offset") is None:
            out.write(T("status_clock_unknown") + "\n")
        else:
            out.write(T("status_clock_warn" if c.get("warn") else "status_clock_ok", offset=c["offset"]) + "\n")
    out.write(T("status_heartbeat_" + ("yes" if rec["heartbeat"] else "no")) + "\n")
    return 0


def main(argv=None, env=None, out=sys.stdout):
    env = os.environ if env is None else env
    p = argparse.ArgumentParser(prog="aib chat", description=T("help.description"))
    sub = p.add_subparsers(dest="cmd")
    on = sub.add_parser("on", help=T("help.on"))
    on.add_argument("--no-venv", action="store_true", help=T("help.no_venv"))
    on.add_argument("--name", help=T("help.name"))
    on.add_argument("--country", help=T("help.country"))
    for cmd in ("name", "country", "publish", "hide"):
        sp = sub.add_parser(cmd, help=T("help." + cmd))
        sp.add_argument("value")
    rs = sub.add_parser("roster", help=T("help.roster"))
    rs.add_argument("--json", action="store_true")
    lk = sub.add_parser("lookup", help=T("help.lookup"))
    lk.add_argument("addr")
    sub.add_parser("off", help=T("help.off"))
    st = sub.add_parser("status", help=T("help.status"))
    st.add_argument("--json", action="store_true")
    sub.add_parser("run", help=T("help.run"))
    ks = sub.add_parser("keys", help=T("help.keys"))
    ks.add_argument("--json", action="store_true")
    ks.add_argument("--all", action="store_true", help=T("help.keys_all"))
    ew = sub.add_parser("ensure-window", help=T("help.ensure_window"))
    ew.add_argument("--socket")
    sub.add_parser("enabled", help=T("help.enabled"))
    pp = sub.add_parser("prepare", help=T("help.prepare"))
    pp.add_argument("--keys-only", action="store_true", help=T("help.keys_only"))
    pp.add_argument("--quiet", action="store_true")
    a = p.parse_args(argv)
    if a.cmd in (None, "status"):
        if a.cmd is None:
            a.json = False
        return cmd_status(a, env, out)
    if a.cmd == "on":
        return cmd_on(a, env, out)
    if a.cmd == "off":
        return cmd_off(a, env, out)
    if a.cmd == "keys":
        return cmd_keys(a, env, out)
    if a.cmd in ("name", "country", "publish", "hide"):
        return cmd_profile(a, env, out)
    if a.cmd == "roster":
        return cmd_roster(a, env, out)
    if a.cmd == "lookup":
        return cmd_lookup(a, env, out)
    if a.cmd == "run":
        return run(env, out=out)
    if a.cmd == "prepare":
        return cmd_prepare(a, env, out)
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
