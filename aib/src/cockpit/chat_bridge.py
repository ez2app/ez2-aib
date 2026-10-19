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
  - 問一次(維護者 10-16 14:3x):開艙跑 `auto`,開關檔沒有、主人也沒答過 ⇒ 在終端機問要不要連公開中繼站;
    答要 ⇒ 開關檔;答不要 ⇒ `off-by-owner.json`(同 `aib chat off`,記住不再問);沒人能答 ⇒ 不上線、不記。
    `aib chat on` 拿掉記號。

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
_WORKER = {"proc": None, "started": 0.0, "relays": None}   # 當班才有;寄件工人(venv 的 chat_nostr.py serve)
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


def off_marker(env=None):
    """主人自己 aib chat off 過 ⇒ 留這個檔,開艙的自動開(auto)就不再替他打開(維護者 10-16:預設開、之後可以關)。"""
    return chat_home(env) / "off-by-owner.json"


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
    keep = None                              # 10-18 Andy:接電源時撐著不睡(caffeinate -s,只在 Mac、aib chat awake off 可關)
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
                    import chat_power as CP
                    keep = CP.start_keep_awake(root, chat_home(env))
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
        if keep is not None:
            import chat_power as CP
            CP.stop_keep_awake(root, keep)
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
    if key_err or state == "no_profile":
        # 第 27.1 節:沒設名字與國別 ⇒ 名冊不發、誰都寄不到這台 ⇒ 不連中繼站(不起工人;在跑的停掉)
        stop_worker()
        worker = None
    else:
        import chat_roster as CR
        cur = tuple(CR.relays(env))
        if _WORKER["relays"] is not None and _WORKER["relays"] != cur:
            stop_worker()                       # 第 27.9 節:換了中繼站 ⇒ 工人重連(不等重起間隔)
            _WORKER["started"] = 0.0
        _WORKER["relays"] = cur
        worker = ensure_worker(root, env, now)
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


def _run_nostr(cmd, root, env, timeout=60, extra=(), stdin=None):
    """venv 那一半(chat_nostr.py)當子行程跑。回 (rc, 結果 dict)。venv 不能用 ⇒ (None, {})。
    stdin:要交給它的一行 JSON(主人私鑰只走這裡,不經參數 / 環境變數)。"""
    py = venv_python(env)
    if not py.exists():
        return None, {}
    try:
        r = subprocess.run([str(py), str(HERE / "chat_nostr.py"), cmd] + list(extra) + ["--root", str(root)],
                           capture_output=True, text=True, timeout=timeout, env=dict(env),
                           input=(json.dumps(stdin) + "\n") if stdin is not None else None)
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
    me = "env %s %s %s" % (" ".join(pairs), shlex.quote(sys.executable), shlex.quote(str(HERE / "chat_bridge.py")))
    # 開發文件第 37.3 節(10-18 fishmac 單艙:轉接程式停了、窗格卻還活著 ⇒ 沒人重起):run 結束後開關還開著 ⇒ 等 RESTART_GAP 秒再起;
    #   aib chat off(開關關了)⇒ 迴圈結束、窗格死。版本更新後停掉舊的,下一輪自然載入新程式。
    return "exec sh -c %s" % shlex.quote("while :; do %s run; %s enabled >/dev/null 2>&1 || exit 0; sleep %d; done"
                                         % (me, me, RESTART_GAP))


RESTART_GAP = 5           # 秒;sys.chat 窗的迴圈:run 結束、開關還開著 ⇒ 等這麼久再起(第 37.3 節)


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
    try:
        off_marker(env).unlink()
    except FileNotFoundError:
        pass
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
    if not backup_state(env)["at"]:
        out.write(T("backup_hint") + "\n")             # 第 37.4 節:主人金鑰只在這台,硬碟壞 / 重灌就沒了
    return 0


YES = ("y", "yes", "是", "好", "要", "對", "はい", "うん", "예", "네", "응")


def _ask(prompt, out, timeout):
    """終端機上問一句、等一行。回答案字串;逾時回 None。"""
    import select
    out.write(prompt)
    out.flush()
    r, _w, _x = select.select([sys.stdin], [], [], timeout)
    if not r:
        out.write("\n")
        return None
    return (sys.stdin.readline() or "").strip()


def cmd_auto(a, env, out):
    """開艙時問一次要不要連上公開中繼站(維護者 10-16 14:3x 改裁:「問一次」取代 12:1x 的「預設開不問」)。
    - 答「要」⇒ 寫開關檔(記住);答「不要」或直接 Enter ⇒ 留 off-by-owner.json(跟 aib chat off 一樣記住,不再問)。
    - 沒有人能回答(stdin / stdout 不是終端機:安裝程式、背景、測試)⇒ 一律不上線、也不記,下次有人在時再問。
    - 逾時(AIB_CHAT_ASK_TIMEOUT,預設 60 秒)沒回 ⇒ 先不上線、不記。
    - 已經開著或主人關過 ⇒ 不問。AIB_CHAT_AUTO=0 或 AIB_CHAT_PREINSTALL=0 ⇒ 什麼都不做。
    打開以後:沒設顯示名稱與國別 ⇒ 名冊不發,外人找不到這台;只收本人與好友的信。"""
    if (env.get("AIB_CHAT_AUTO") or "1") == "0" or (env.get("AIB_CHAT_PREINSTALL") or "1") == "0":
        return 0
    if is_enabled(env) or off_marker(env).exists():
        return 0
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return 0
    ans = _ask(T("auto_ask"), out, float(env.get("AIB_CHAT_ASK_TIMEOUT") or 60))
    if ans is None:
        out.write(T("auto_ask_timeout") + "\n")
        return 0
    _mkdir_private(chat_home(env))
    if ans.lower() in YES:
        write_json(str(switch_path(env)), {"v": VERSION, "on": True, "at": int(time.time()), "asked": True})
        os.chmod(switch_path(env), 0o600)
        out.write(T("auto_on") + "\n")
    else:
        write_json(str(off_marker(env)), {"v": VERSION, "at": int(time.time()), "asked": True})
        os.chmod(off_marker(env), 0o600)
        out.write(T("auto_declined") + "\n")
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
    _mkdir_private(chat_home(env))
    write_json(str(off_marker(env)), {"v": VERSION, "at": int(time.time())})   # 開艙不再自動打開
    os.chmod(off_marker(env), 0o600)
    root = bridge_root(env)
    # 第 20.4 節:發空名冊、撤下 10050(發過才需要;沒有 venv 就沒發過)
    import chat_roster as CR
    if CR.published_hash(root) not in (None, "empty"):
        rc, _rec = _run_nostr("publish-empty", root, env, timeout=30)
        out.write(T("off_unpublished" if rc == 0 else "off_unpublish_failed") + "\n")
    import chat_owner_cli as OC
    OC.off_owner_roster(env, out)                          # 主人金鑰加密了 ⇒ 當場輸密語撤主人名冊;不輸一定提示
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
    rec = {
        "enabled": is_enabled(env),
        "running": running,
        "state": st.get("state") if running else ("stopped" if st else None),
        "pid": pid if running else None,
        "instance": st.get("instance") if running else None,
        "at": st.get("at"),
        "heartbeat": GF.bridge_alive(root),
        "venv": venv_state(env),
        "keys": _keys_summary(env),
        "friends": len(_friends(env)),
        "mine": len(_friends(env, "mine")),
        "refused_7d": sum(1 for r in read_refused(env) if time.time() - int(r.get("last_at") or 0) < 7 * 86400),
        "off_by_owner": off_marker(env).exists(),
        "worker": _worker_status(root, running),
        "bridge_root": str(root),
    }
    rec["healthy"], rec["why"] = health(rec)
    rec["backup"] = backup_state(env)
    return rec


def health(rec):
    """第 37.3 節:(healthy, why)。沒開 ⇒ ("off", None);開著但沒當班 ⇒ not_running;當班但沒心跳(沒接上中繼站)⇒
    no_relay(還沒發名冊 / 沒金鑰)或 stale_heartbeat;工人回報全部站斷線 ⇒ no_relay。都好 ⇒ (True, None)。"""
    if not rec["enabled"]:
        return "off", None
    if not rec["running"]:
        return False, "not_running"
    if (rec.get("worker") or {}).get("offline"):
        return False, "no_relay"
    if not rec["heartbeat"]:
        return False, "stale_heartbeat" if rec.get("state") == "published" else "no_relay"
    return True, None


BACKUP_NAG_S = 7 * 86400   # 第 37.4 節:開通滿 7 天還沒備份 ⇒ 提醒


def backup_state_path(env=None):
    return chat_home(env) / "backup-state.json"


def backup_state(env=None, now=None):
    """{at: 最後備份時間或 None, due: 該不該提醒}。開通(開關檔)滿 7 天、有主人金鑰、從沒備份 ⇒ due。"""
    now = time.time() if now is None else now
    try:
        at = json.loads(backup_state_path(env).read_text(encoding="utf-8")).get("at")
    except (OSError, ValueError, AttributeError):
        at = None
    try:
        since = switch_path(env).stat().st_mtime
    except OSError:
        since = None
    import nostr_keys as NK
    has_owner = bool((NK.read_map(env).get("owner") or {}).get("npub"))
    return {"at": at, "due": bool(has_owner and not at and since is not None and now - since >= BACKUP_NAG_S)}


def _friends(env, kind="friends"):
    import chat_roster as CR
    return CR.read_friends(env, kind)


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
    if pv["owner"]:
        out.write(T("mine_hint", owner=pv["owner"]) + "\n")
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
    if a.cmd == "publish":                  # 還沒有席金鑰 ⇒ 名冊不會列它,要說清楚(10-17 fishmac:說「已列進」卻 0 席)
        import nostr_keys as NK
        if not any(e.get("status") == "active" and e.get("seat5") == a.value for e in NK.read_map(env)["seats"]):
            out.write(T("publish_no_key", value=a.value) + "\n")
    return 0


def cmd_friend(a, env, out):
    """好友(維護者 10-16 12:1x):只收本人與好友的信,其他拒收。以主人為單位;只存在本機。
    mine(第 27.2 節):我自己其他機器的主人號碼,收到當本人。"""
    import chat_roster as CR
    kind = {"mine": "mine", "block": "blocks"}.get(a.cmd, "friends")
    pre = {"mine": "mine_", "blocks": "block_"}.get(kind, "friend_")
    if a.action is None:
        return cmd_friends(a, env, out)
    if not a.who:
        out.write(T("profile_bad_friend") + "\n")
        return 2
    try:
        if a.action == "add":
            owner, new = CR.add_friend(a.who, a.name, env, kind)
            out.write(T(pre + ("added" if new else "renamed"), owner=owner) + "\n")
        else:
            owner, gone = CR.remove_friend(a.who, env, kind)
            out.write(T(pre + ("removed" if gone else "not_found"), owner=owner) + "\n")
    except CR.RosterError as err:
        out.write(T("profile_" + str(err)) + "\n")
        return 2
    return 0


def cmd_friends(a, env, out):
    import chat_roster as CR
    kind = {"mine": "mine", "block": "blocks"}.get(a.cmd, "friends")
    fr = CR.read_friends(env, kind)
    if getattr(a, "json", False):
        out.write(json.dumps({kind: fr}, ensure_ascii=False) + "\n")
        return 0
    if not fr:
        out.write(T(kind + "_none") + "\n")
    for f in fr:
        row = "friends_row_sent" if f.get("source") == "sent" else "friends_row"
        out.write(T(row, name=f.get("name") or "-", owner=f["owner"]) + "\n")
    if kind == "mine" or not fr:
        import nostr_keys as NK
        me = (NK.read_map(env).get("owner") or {}).get("npub")
        if kind == "mine" and me:
            out.write(T("mine_hint", owner=me) + "\n")
    return 0


def read_refused(env=None):
    """誰被拒收過(第 27.5 節;轉接程式寫 <bridge root>/refused.json,不含內文)。新的在前。"""
    try:
        cur = json.loads((bridge_root(env) / "refused.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = cur.get("refused") if isinstance(cur, dict) else None
    return [r for r in rows if isinstance(r, dict) and r.get("who")] if isinstance(rows, list) else []


def cmd_refused(a, env, out):
    rows = read_refused(env)
    if a.json:
        out.write(json.dumps({"refused": rows}, ensure_ascii=False) + "\n")
        return 0
    if not rows:
        out.write(T("refused_none") + "\n")
        return 0
    for r in rows:
        when = time.strftime("%m-%d %H:%M", time.localtime(int(r.get("last_at") or 0)))
        out.write(T("refused_row_" + ("verified" if r.get("display_name") else "unknown"), name=r.get("display_name") or "-",
                    country=r.get("country") or "-", who=r["who"], n=r.get("count", 1), when=when,
                    reason=T("refused_reason_" + ("not_friend" if r.get("reason") == "not_friend" else "unverified"))) + "\n")
        if r.get("owner"):
            out.write(T("refused_add_hint", who=r["who"]) + "\n")
    return 0


def _relay_test(a, env, out):
    """aib chat relays test <wss://…>:量一站(連線 / 回 OK / 查詢各幾毫秒)。拋棄式金鑰、一則不存的 ephemeral、一次查詢;
    不連發(公開站會封連發的人,第 19.1 節)。只在人下指令時才連,不是背景自動測。"""
    import chat_roster as CR
    url = (a.url or "").strip().rstrip("/")
    if not CR._relay_ok(url):
        out.write(T("profile_bad_relay") + "\n")
        return 2
    rc, rec = _run_nostr("probe", bridge_root(env), env, timeout=90, extra=[url])
    if rc is None:
        out.write(T("lookup_no_venv", pin=NOSTR_SDK_PIN) + "\n")
        return 2
    if a.json:
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    elif rc == 0:
        out.write(T("relay_test_ok", url=url, c=rec.get("connect_ms"), o=rec.get("ok_ms"), q=rec.get("query_ms")) + "\n")
    else:
        out.write(T("relay_test_bad", url=url, why=rec.get("reason") or rec.get("why") or "-") + "\n")
    return 0 if rc == 0 else 2


def cmd_relays(a, env, out):
    """aib chat relays [add|remove <wss://…> | reset](第 27.9 節)。列出時附各站最近狀態(工人寫的)。"""
    import chat_roster as CR
    if a.action == "test":
        return _relay_test(a, env, out)
    if a.action:
        if a.action in ("add", "remove") and not a.url:
            out.write(T("profile_bad_relay") + "\n")
            return 2
        try:
            CR.set_relays(a.action, a.url, env)
        except CR.RosterError as err:
            out.write(T("profile_" + str(err), n=CR.MAX_RELAYS) + "\n")
            return 2
        out.write(T("relays_saved") + "\n")
    w = _worker_status(bridge_root(env), True) or {}
    seen = w.get("relays") or {}
    cur = CR.relays(env)
    if a.json:
        out.write(json.dumps({"relays": [{"url": u, "status": seen.get(u)} for u in cur]}, ensure_ascii=False) + "\n")
        return 0
    for u in cur:
        st = seen.get(u)
        out.write(T("relays_row_" + ("up" if st == "CONNECTED" else ("down" if st else "unknown")), url=u) + "\n")
    return 0


def _my_published(env):
    """自己公開(列進名冊、金鑰 active)的席 ⇒ [{cabin, seat, addr}]。"""
    import chat_roster as CR
    import nostr_keys as NK
    m = NK.read_map(env)
    owner = (m.get("owner") or {}).get("npub")
    pub = set((CR.read_profile(env) or {}).get("public") or [])
    return [{"cabin": e.get("cabin") or "main", "seat": e["seat5"],
             "addr": "%s/%s/%s" % (owner, e.get("cabin") or "main", e["seat5"])}
            for e in m["seats"] if e.get("status") == "active" and e["seat5"] in pub and owner]


def _tell_requester(root, hit, rec):
    """同意 / 拒絕 ⇒ 寫 delivery 讓工人回條給對方。申請那筆壞了(msgid 不合)⇒ 回 False:照樣同意 / 拒絕,只是通知不到。"""
    try:
        GF.append_delivery(root, hit.get("msgid") or "", rec)
        return True
    except (GF.ContractError, OSError, ValueError):
        return False


def cmd_request(a, env, out):
    """好友申請(第 29.4 節):aib chat request <位址> [--note] [--from 席] ;aib chat request accept|reject <主人 npub>。"""
    import chat_roster as CR
    root = bridge_root(env)
    if a.target in ("accept", "reject"):
        owner = CR.friend_owner(a.who or "")
        hit = GF.pop_request(root, owner) if owner else None
        if not hit:
            out.write(T("request_not_found", owner=owner or (a.who or "-")) + "\n")
            return 2
        if a.target == "accept":
            try:
                CR.add_friend(owner, hit.get("display_name"), env, source="accepted")   # 對方申請、我同意(不是我送的申請)
            except CR.RosterError as err:
                GF.add_request(root, hit, every=0)          # 加不成(例:封鎖中)⇒ 申請放回去,照實說
                out.write(T("profile_" + str(err)) + "\n")
                return 2
            if not _tell_requester(root, hit, {"v": 1, "at": int(time.time()), "state": "delivered"}):
                out.write(T("request_no_notice") + "\n")
            out.write(T("request_accepted", owner=owner) + "\n")
        else:
            if not a.silent and not _tell_requester(root, hit, {"v": 1, "at": int(time.time()), "state": "refused",
                                                                    "reason": "rejected"}):
                out.write(T("request_no_notice") + "\n")
            out.write(T("request_rejected", owner=owner) + "\n")
        return 0
    pub = _my_published(env)
    frm = next((p for p in pub if p["seat"] == a.from_seat), None) if a.from_seat else (pub[0] if pub else None)
    if frm is None:
        out.write(T("request_no_seat") + "\n")
        return 2
    rec = {"v": GF.VERSION, "msgid": GF.new_msgid(), "created_at": int(time.time()), "op": "request",
           "from": {"cabin": frm["cabin"], "seat": frm["seat"], "attested": True}, "to": a.target,
           "content": a.note or ""}
    GF.ensure_dirs(root)
    res = GF.send_and_wait(root, rec, wait=int(env.get("AIB_DM_GLOBAL_WAIT") or GF.WAIT_DEFAULT))
    key = {"held": "request_sent", "delivered": "request_already", "refused": "request_refused"}.get(res["stage"], "request_other")
    out.write(T(key, stage=res["stage"], detail=res["detail"] or "-") + "\n")
    return res["rc"]


def cmd_requests(a, env, out):
    rows = GF.read_requests(bridge_root(env))
    if a.json:
        out.write(json.dumps({"requests": rows}, ensure_ascii=False) + "\n")
        return 0
    if not rows:
        out.write(T("requests_none") + "\n")
    for r in rows:
        when = time.strftime("%m-%d %H:%M", time.localtime(int(r.get("at") or 0)))
        out.write(T("requests_row", when=when, name=r.get("display_name") or "-", country=r.get("country") or "-",
                    note=r.get("note") or "-", owner=r["owner"]) + "\n")
    return 0


def _claim_view(peer, key, name=None):
    """一項證明的驗證結果:verified = True / False / None(還沒驗或平台不能自動驗 ⇒ 只算自稱)。"""
    c = (peer.get("checks") or {}).get(key) or {}
    out = {"verified": c.get("verified"), "at": c.get("at")}
    if name is not None:
        out["name"] = name
    return out


def cmd_social(a, env, out):
    """aib chat social [add <平台> <帳號或網址> [--proof <https>] | remove <平台> [<帳號>]] [--json];
    aib chat nip05 <名@網域> | --clear。寫進名冊,別人照證明去驗(第 32 節)。"""
    import chat_roster as CR
    try:
        if a.cmd == "nip05":
            got = CR.set_nip05(None if a.clear else a.name, env)
            out.write(T("nip05_saved" if got else "nip05_cleared", name=got or "-") + "\n")
            return 0
        if a.action:
            CR.set_social(a.action, a.p, a.v, a.proof, env)
            out.write(T("social_saved") + "\n")
    except CR.RosterError as err:
        out.write(T("profile_" + str(err), n=CR.SOCIALS_MAX, list="/".join(CR.SOCIAL_PLATFORMS)) + "\n")
        return 2
    prof = CR.read_profile(env) or {}
    items = CR.clean_socials(prof.get("socials"))
    if getattr(a, "json", False):
        out.write(json.dumps({"socials": items, "nip05": prof.get("nip05")}, ensure_ascii=False) + "\n")
        return 0
    for x in items:
        out.write(T("social_row", p=x["p"], v=x["v"], proof=x.get("proof") or "-") + "\n")
    if not items:
        out.write(T("social_none") + "\n")
    return 0


def contacts_payload(env=None):
    """第 29.2 節:艦長室通訊錄的第五來源。只讀本機檔、不連網、不含內文。"""
    import chat_roster as CR
    import nostr_keys as NK
    root = bridge_root(env)
    prof = CR.read_profile(env) or {}
    me = {"owner": (NK.read_map(env).get("owner") or {}).get("npub"), "display_name": prof.get("display_name"),
          "country": prof.get("country"), "published": _my_published(env)}
    peers = GF.read_peers(root)
    world = GF.read_world(root)
    import chat_power as CP
    rows = {}

    def row(owner, relation, label=None, source=None, extra=None):
        p = peers.get(owner) or {}
        r = rows.setdefault(owner, {"key": "global:" + owner, "owner": owner, "relation": relation, "label": label,
                                    "display_name": p.get("display_name"), "country": p.get("country"),
                                    "seats": [dict(x, addr="%s/%s/%s" % (owner, x.get("cabin") or "main", x.get("seat")), ai=True)
                                              for x in p.get("seats") or [] if x.get("seat")],
                                    # 第 32 節真人驗證:verified = 手上有驗過簽章的名冊;human = 通訊錄每一筆都是主人(人)
                                    "verified": bool(p.get("roster_at")), "human": True,
                                    "nip05": _claim_view(p, "nip05", p.get("nip05")) if p.get("nip05") else None,
                                    "socials": [dict(x, **_claim_view(p, "social:%s:%s" % (x["p"], x["v"])))
                                                for x in p.get("socials") or []],
                                    "roster_at": p.get("roster_at"), "seen_at": p.get("seen_at"),
                                    "machine": CP.owner_state([c for c in world.values() if c.get("owner") == owner],
                                                              time.time()),
                                    # 第 37.5 節:對方版本與它收不到的功能(版本不知道 ⇒ aib / missing 都是 None)
                                    "aib": CR.clean_version(p.get("aib")), "missing": CR.missing_features(p.get("aib")),
                                    "last_in": p.get("last_in"), "last_out": p.get("last_out"),
                                    "n_in": p.get("n_in", 0), "n_out": p.get("n_out", 0), "n_refused": 0,
                                    "request": None, "source": source})
        for k, v in (extra or {}).items():
            r[k] = v
        return r
    for f in CR.read_friends(env, "blocks"):
        row(f["owner"], "blocked", f.get("name"))
    for f in CR.read_friends(env, "mine"):
        row(f["owner"], "mine", f.get("name"))
    for f in CR.read_friends(env):
        st = (peers.get(f["owner"]) or {}).get("request")
        out_req = f.get("source") == "request" and st != "accepted"
        r = row(f["owner"], "request_out" if out_req else "friend", f.get("name"), f.get("source") or "manual")
        if out_req:
            r["request"] = {"state": st or "pending"}
    for q in GF.read_requests(root):
        r = row(q["owner"], "request_in", None, None, {"request": {"note": q.get("note"), "at": q.get("at")}})
        r["display_name"] = r["display_name"] or q.get("display_name")
        r["country"] = r["country"] or q.get("country")
    for x in read_refused(env):
        if x.get("owner"):
            r = row(x["who"], "refused", None, None, {"n_refused": int(x.get("count") or 0)})
            r["display_name"] = r["display_name"] or x.get("display_name")
            r["country"] = r["country"] or x.get("country")
    # 只往來過、沒有任何關係的人不列(關係只有上面五份名單);拒收的已列在上面
    order = {"request_in": 0, "mine": 1, "friend": 2, "request_out": 3, "refused": 4, "blocked": 5}
    return {"v": 1, "me": me, "contacts": sorted(rows.values(), key=lambda r: (order.get(r["relation"], 9),
                                                                                -(r.get("last_in") or r.get("seen_at") or 0)))}


def refresh_contacts(who, env):
    """aib chat contacts --refresh <npub|all>(10-18 @74e8b):立刻重抓好友 / 我的其他機器的名冊,更新聯絡人快照
    (席位換成名冊目前公開的)。回 [{owner, ok, why}]。"""
    import chat_roster as CR
    root = bridge_root(env)
    owners = [f["owner"] for f in CR.read_friends(env, "mine")] + [f["owner"] for f in CR.read_friends(env)]
    owners = list(dict.fromkeys(owners)) if who == "all" else [who]
    res = []
    for o in owners:
        rc, rec = _run_nostr("roster", root, env, timeout=60, extra=[o])
        if rc is None:
            return [{"owner": o, "ok": False, "why": "no_venv"}]
        if rc == 0 and rec.get("ok"):
            GF.note_peer(root, o, rec.get("display_name"), rec.get("country"), None, rec.get("roster_at"),
                         claims={"nip05": rec.get("nip05"), "aib": rec.get("aib"), "roster_seats": rec.get("roster_seats")})
            res.append({"owner": o, "ok": True, "seats": len(rec.get("roster_seats") or [])})
        else:
            res.append({"owner": o, "ok": False, "why": rec.get("why") or "error"})
    return res


def cmd_contacts(a, env, out):
    if a.refresh:
        for r in refresh_contacts(a.refresh, env):
            if not a.json:
                out.write((T("contacts_refreshed", owner=r["owner"], n=r["seats"]) if r["ok"]
                           else T("contacts_refresh_failed", owner=r["owner"], why=r["why"])) + "\n")
    pl = contacts_payload(env)
    if a.json:
        out.write(json.dumps(pl, ensure_ascii=False) + "\n")
        return 0
    if not pl["contacts"]:
        out.write(T("contacts_none") + "\n")
    for r in pl["contacts"]:
        out.write(T("contacts_row", rel=T("rel_" + r["relation"]), name=r.get("label") or r.get("display_name") or "-",
                    country=r.get("country") or "-", owner=r["owner"], n=len(r["seats"])) + "\n")
    return 0


def share_payload(env=None):
    """第 30.2 節(@74e8b 22:51 要的形狀):{"default", "peers": {npub: 實際等級}};好友與我的其他機器都填好
    (mine 沒設 ⇒ status、好友沒設 ⇒ default)。"""
    import chat_roster as CR
    rec = CR.read_share(env)
    owners = [f["owner"] for f in CR.read_friends(env, "mine")] + [f["owner"] for f in CR.read_friends(env)]
    return {"v": 1, "default": rec["default"], "peers": {o: CR.share_level(o, env) for o in dict.fromkeys(owners)}}


def cmd_share(a, env, out):
    """aib chat share [<主人 npub> off|online|status] [--default off|online|status] [--json]。"""
    import chat_roster as CR
    try:
        if a.default:
            CR.set_share(a.default, None, env)
        if a.who:
            if a.level is None:
                out.write(T("share_need_level") + "\n")
                return 2
            CR.set_share(a.level, a.who, env)
    except CR.RosterError as err:
        out.write(T("profile_" + str(err)) + "\n")
        return 2
    pl = share_payload(env)
    if a.json:
        out.write(json.dumps(pl, ensure_ascii=False) + "\n")
        return 0
    if a.who or a.default:                  # 有改 ⇒ 只回這一筆(@74e8b 22:56:之前會印整份名單,看起來像改到別人)
        if a.default:
            out.write(T("share_default", level=T("share_" + pl["default"])) + "\n")
        if a.who:
            import chat_roster as CR
            o = CR.friend_owner(a.who)
            out.write(T("share_set", owner=o, level=T("share_" + CR.share_level(o, env))) + "\n")
        return 0
    out.write(T("share_default", level=T("share_" + pl["default"])) + "\n")
    for o, lvl in pl["peers"].items():
        out.write(T("share_row", level=T("share_" + lvl), owner=o) + "\n")
    return 0


def world_payload(env=None, now=None):
    """第 30.4–30.5 節:好友 / 我的其他機器分享來的在線狀態 + 我自己這一份(me)。只讀本機檔。
    過期(WORLD_FRESH_S 沒收到)⇒ fresh=false、各席 unknown;不再是好友 / 我的機器的不列。"""
    import chat_roster as CR
    import nostr_keys as NK
    import seat_presence as SP
    now = time.time() if now is None else now
    root = bridge_root(env)
    peers = GF.read_peers(root)
    m = NK.read_map(env)
    prof = CR.read_profile(env) or {}
    pub = _my_published(env)
    me = {"owner": (m.get("owner") or {}).get("npub"), "display_name": prof.get("display_name"),
          "country": prof.get("country"), "seats": []}
    rows = SP.presence([p["seat"] for p in pub], with_status=True, now=now)
    for p, r in zip(pub, rows):
        me["seats"].append(dict(r, cabin=p["cabin"], addr=p["addr"]))
    cabins = []
    for key, c in sorted(GF.read_world(root).items()):
        owner = c.get("owner")
        rel = "mine" if CR.is_mine(owner, env) else ("friend" if CR.is_friend(owner, env) else None)
        if rel is None or CR.is_blocked(owner, env):
            continue
        fresh = now - int(c.get("presence_at") or 0) < GF.WORLD_FRESH_S
        info = peers.get(owner) or {}
        names = {x.get("seat"): x for x in info.get("seats") or []}
        seats = []
        for x in c.get("seats") or []:
            meta = names.get(x.get("seat")) or {}
            seats.append({"seat": x.get("seat"), "name": meta.get("name"), "role": meta.get("role"),
                          "addr": "%s/%s/%s" % (owner, c.get("cabin"), x.get("seat")),
                          "state": x.get("state") if fresh else "unknown",
                          "now": x.get("now") if fresh else None, "step": x.get("step") if fresh else None})
        sp = GF.read_room(root, owner)
        import chat_power as CP
        cabins.append({"owner": owner, "cabin": c.get("cabin"), "relation": rel,
                       "machine": CP.machine_state(c, now),             # 10-18:對方那台醒著 / 在睡 / 離線
                       "space": {"at": sp["at"], "fresh": now - int(sp["at"]) < GF.ROOM_FRESH_S,
                                 "room": bool(sp["snap"].get("rooms"))} if sp else None,
                       "display_name": info.get("display_name"), "country": info.get("country"),
                       "aib": CR.clean_version(info.get("aib")), "missing": CR.missing_features(info.get("aib")),
                       "presence_at": c.get("presence_at"), "fresh": fresh, "seats": seats})
    # 10-19:同一位主人換了艙,舊艙還在 world 裡(分不出是關掉的艙還是睡著的另一台,所以不刪)⇒ 新的排前面,
    # 只看第一格的畫面才不會拿到過期那格、把在線的人顯示成不在線
    cabins.sort(key=lambda x: (not x["fresh"], -int(x["presence_at"] or 0)))
    return {"v": 1, "at": int(now), "me": me, "cabins": cabins}


RECENT_MINUTES = 10


def recent_payload(env=None, now=None, minutes=RECENT_MINUTES):
    """aib chat recent --json(新艦長室視訊螢幕 / 收發室,@74e8b 10-19):最近 N 分鐘的線上 / Telegram 私訊收發。
    只讀 audit(時間、方向、對象、結果),**不含內容**;同一封寄出只留最後一個階段。對象一律換成通訊錄的 contact_key
    (global:<主人 npub> / telegram:<user_id>),認不出的主人不列。"""
    now = time.time() if now is None else now
    since = now - max(1, int(minutes)) * 60
    root = bridge_root(env)
    seat_owner = {}
    for owner, info in GF.read_peers(root).items():
        for x in info.get("seats") or []:
            if isinstance(x, dict) and x.get("npub"):
                seat_owner[x["npub"]] = owner
    rows, out_last = [], {}
    for day in sorted({time.strftime("%Y%m%d", time.localtime(t)) for t in (since, now)}):
        try:
            lines = (root / "audit" / (day + ".jsonl")).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                r = json.loads(ln)
                at = int(r.get("at") or 0)
            except (ValueError, TypeError, AttributeError):
                continue
            if at < since or at > now + 60:
                continue
            d, peer, res = r.get("dir"), str(r.get("peer") or ""), str(r.get("result") or "")[:40]
            if d in ("tg-in", "tg-out") and peer.startswith("telegram:"):
                rows.append({"at": at, "dir": d[3:], "net": "telegram", "contact_key": peer, "result": res})
            elif d == "in":
                owner = seat_owner.get(peer)
                if owner:
                    rows.append({"at": at, "dir": "in", "net": "nostr", "contact_key": "global:" + owner, "result": res})
            elif d == "out":
                owner = (GF.parse_address(peer) or {}).get("owner")
                if owner:
                    out_last[r.get("msgid")] = {"at": at, "dir": "out", "net": "nostr", "contact_key": "global:" + owner,
                                                "result": res}
    rows += list(out_last.values())
    rows.sort(key=lambda x: x["at"])
    return {"v": 1, "at": int(now), "minutes": int(minutes), "rows": rows}


def room_payload(owner, env=None, now=None):
    """aib chat room <主人> --json(第 34.4 節):好友分享來的房間快照 + fresh;不是好友 / 我的機器 ⇒ None。"""
    import chat_roster as CR
    now = time.time() if now is None else now
    if not owner or CR.is_blocked(owner, env) or not (CR.is_friend(owner, env) or CR.is_mine(owner, env)):
        return None
    rec = GF.read_room(bridge_root(env), owner)
    if not rec or not isinstance(rec["snap"].get("rooms"), dict):
        return None
    import room_snapshot as RS
    r = rec["snap"]["rooms"]
    seats = [s for s in (r.get("seats") or []) if isinstance(s, dict)]
    for s in seats:                       # 收端再驗一次(寄件端可能是舊版或亂寫的):use / cell 不合就拿掉
        if "use" in s and not RS.clean_use(s.get("use")):
            s.pop("use")
        if "cell" in s and not RS.clean_cell(s.get("cell")):
            s.pop("cell")
    return {"v": 1, "owner": owner, "room_at": rec["at"], "fresh": now - int(rec["at"]) < GF.ROOM_FRESH_S,
            "ship": r.get("ship") or {}, "cabins": r.get("cabins") or [], "seats": seats,
            "layout": r.get("layout"), "layout_omitted": bool(r.get("layout_omitted")),
            "decor": RS.clean_decor(r.get("decor")),
            "meet": RS.clean_meet(r.get("meet"), {s.get("seat") for s in seats})}


def island_payload(owner, env=None, now=None):
    """aib chat island <主人> --json(第 34 節 v2):好友島的戶外 + 島上走動的人。"""
    import chat_roster as CR
    now = time.time() if now is None else now
    if not owner or CR.is_blocked(owner, env) or not (CR.is_friend(owner, env) or CR.is_mine(owner, env)):
        return None
    rec = GF.read_room(bridge_root(env), owner)
    if not rec:
        return None
    snap = rec["snap"]
    return {"v": 1, "owner": owner, "at": rec["at"], "fresh": now - int(rec["at"]) < GF.ROOM_FRESH_S,
            "island": snap.get("island") or {}, "people": snap.get("people") or [],
            "has_room": isinstance(snap.get("rooms"), dict)}


def cmd_island(a, env, out):
    import chat_roster as CR
    pl = island_payload(CR.friend_owner(a.who or ""), env)
    if a.json:
        out.write(json.dumps(pl, ensure_ascii=False) + "\n")
        return 0 if pl else 1
    if not pl:
        out.write(T("island_none") + "\n")
        return 1
    isl = pl["island"]
    out.write(T("island_head", name=isl.get("name") or "-", owner=(isl.get("owner") or {}).get("name") or "-",
                w=(isl.get("size") or {}).get("w"), h=(isl.get("size") or {}).get("h"), n=len(isl.get("buildings") or [])) + "\n")
    for p in pl["people"]:
        out.write(T("island_person", emoji=p.get("emoji") or "·", seat=p.get("seat"), name=p.get("name") or "-",
                    state=T("state_" + (p.get("state") or "unknown"))) + "\n")
    return 0


def space_check(island=None, room=None, env=None):
    """aib chat space check(第 36.3 節):艦長室存檔前先問 ⇒ {ok, wire_bytes, limit, over, errors}。
    以最大的分享等級(含建築內部、狀態列、掛單)組一份快照來量;給了 --island / --room 就用那份檔代替現有的(不動真的檔)。"""
    import room_snapshot as RS
    errors, given = [], {}
    for name, src, cap in (("island", island, RS.SPACE_RAW_MAX), ("room", room, RS.RAW_MAX)):
        if not src:
            continue
        try:
            b = Path(src).read_bytes()
            x = json.loads(b.decode("utf-8"))
        except (OSError, ValueError):
            errors.append({"field": name, "why": "unreadable"})
            continue
        if len(b) > cap:
            errors.append({"field": name, "why": "too_big_raw", "max": cap})
        given[name] = x if isinstance(x, dict) else {}
    isl = given.get("island")
    if isl is None:
        isl = RS._read_json(RS.island_path(env), RS.SPACE_RAW_MAX)
    errors += RS.problems(isl)
    snap = RS.build(env, with_rooms=True, with_status=True, with_offers=True, island=isl, room=given.get("room")) or {
        "v": 3, "island": RS.clean_island(RS.scrub(isl)), "people": [], "rooms": None}
    wire, full, dropped = RS.encode_info(snap)
    return {"v": 1, "ok": wire is not None and dropped is None and not errors, "wire_bytes": full, "limit": RS.WIRE_MAX,
            "over": "too_big" if wire is None else dropped, "errors": errors}


def cmd_space(a, env, out):
    """aib chat space [<主人> none|island|room] [--default none|island] [--json](第 34 節 v2);
    aib chat space check [--island <檔>] [--room <檔>] --json(第 36.3 節)。"""
    import chat_roster as CR
    if a.who == "check":
        pl = space_check(a.island, a.room, env)
        if a.json:
            out.write(json.dumps(pl, ensure_ascii=False) + "\n")
        else:
            out.write(T("space_check_" + ("ok" if pl["ok"] else "bad"), kb=round(pl["wire_bytes"] / 1024, 1),
                        limit=pl["limit"] // 1024, over=pl["over"] or "-",
                        errors=", ".join(e["field"] for e in pl["errors"]) or "-") + "\n")
        return 0 if pl["ok"] else 1
    try:
        if a.default:
            CR.set_space(a.default, None, env)
        if a.who:
            if a.level is None:
                out.write(T("space_need_level") + "\n")
                return 2
            CR.set_space(a.level, a.who, env)
    except CR.RosterError as err:
        out.write(T("profile_" + str(err)) + "\n")
        return 2
    sp = CR.read_space(env)
    owners = [f["owner"] for f in CR.read_friends(env, "mine")] + [f["owner"] for f in CR.read_friends(env)]
    st = GF.read_space_status(bridge_root(env)) or {}
    pl = {"v": 1, "default": sp["default"], "peers": {o: CR.space_level(o, env) for o in dict.fromkeys(owners)},
          "last_error": {k: st.get(k) for k in ("at", "reason", "wire_bytes", "limit")} if st.get("reason") else None}
    if a.json:
        out.write(json.dumps(pl, ensure_ascii=False) + "\n")
        return 0
    if a.who:
        o = CR.friend_owner(a.who)
        out.write(T("space_set", owner=o, level=T("space_" + CR.space_level(o, env))) + "\n")
        return 0
    out.write(T("space_default", level=T("space_" + pl["default"])) + "\n")
    for o, lvl in pl["peers"].items():
        out.write(T("share_row", level=T("space_" + lvl), owner=o) + "\n")
    return 0


def offers_payload(owner, env=None, now=None):
    """aib chat offers <主人> --json:好友島上的掛單(就是他空間快照 island.offers 那一欄)。"""
    pl = island_payload(owner, env, now)
    if not pl:
        return None
    return {"v": 1, "owner": owner, "at": pl["at"], "fresh": pl["fresh"], "offers": pl["island"].get("offers") or []}


def cmd_offers(a, env, out):
    import chat_roster as CR
    pl = offers_payload(CR.friend_owner(a.who or ""), env)
    if a.json:
        out.write(json.dumps(pl, ensure_ascii=False) + "\n")
        return 0 if pl else 1
    if not pl:
        out.write(T("island_none") + "\n")
        return 1
    for o in pl["offers"]:
        out.write(T("offer_row", id=o["id"], item=o["item"], n=o["n"], unit=o["unit"]) + "\n")
    if not pl["offers"]:
        out.write(T("offer_none") + "\n")
    return 0


def cmd_offer(a, env, out):
    """aib chat offer buy <主人> <id> <n> --unit <單價> | list | host on|off(第 36.2 節)。"""
    import chat_offers as CO
    import chat_roster as CR
    import room_snapshot as RS
    if a.action == "host":
        if a.args not in (["on"], ["off"]):
            out.write(T("offer_usage") + "\n")
            return 2
        on = RS.set_offers_host(a.args[0] == "on", env)
        out.write((json.dumps({"v": 1, "host": on}) if a.json else T("offer_host_" + ("on" if on else "off"))) + "\n")
        return 0
    if a.action == "list":
        pl = CO.offer_list(env)
        if a.json:
            out.write(json.dumps(pl, ensure_ascii=False) + "\n")
            return 0
        for o in pl["offers"]:
            out.write(T("offer_mine_row", id=o["id"], item=o["item"], left=o["left"], n=o["n"], unit=o["unit"]) + "\n")
        for t in pl["trades"][:20]:
            out.write(T("offer_trade_row", role=T("offer_role_" + t["role"]), status=T("offer_st_" + t["status"]),
                        item=t.get("item") or t.get("id") or "-", n=t.get("n"), total=t.get("total") or "-") + "\n")
        return 0
    if len(a.args) != 3 or a.unit is None:
        out.write(T("offer_usage") + "\n")
        return 2
    owner = CR.friend_owner(a.args[0])
    try:
        n = int(a.args[2])
    except ValueError:
        n = 0
    if not owner or not (CR.is_friend(owner, env) or CR.is_mine(owner, env)) or CR.is_blocked(owner, env):
        out.write(T("offer_not_friend") + "\n")
        return 2
    if not (RS._OFFER_ID_RE.match(a.args[1]) and 1 <= n <= 999 and 1 <= a.unit <= 10 ** 6):
        out.write(T("offer_usage") + "\n")
        return 2
    if not GF.bridge_alive(bridge_root(env)):
        out.write(T("offer_bridge_off") + "\n")
        return 2
    seen = next((o for o in (offers_payload(owner, env) or {}).get("offers") or [] if o["id"] == a.args[1]), None)
    rec = CO.buyer_new(env, bridge_root(env), owner, a.args[1], n, a.unit, item=(seen or {}).get("item"))
    out.write((json.dumps({"v": 1, "trade": rec}, ensure_ascii=False) if a.json
               else T("offer_sent", nonce=rec["nonce"], n=n, total=rec["total"])) + "\n")
    return 0


def cmd_send(a, env, out):
    """aib chat send <全球位址> --as-human --cabin <艙碼> [--json];stdin 一行 JSON {"text", "pin"}(艦長室網頁代表真人寄)。
    Mac 彈 Touch ID(pin 不看);非 Mac 才驗 PIN(只走 stdin)。目前只開 --as-human 這一條:席自己寄照舊用 aib dm。"""
    import chat_human as CH
    if not a.as_human or not a.cabin:
        out.write(T("send_usage") + "\n")
        return 2
    try:
        req = json.loads(sys.stdin.readline() or "{}")
    except ValueError:
        req = {}
    req = req if isinstance(req, dict) else {}
    res = CH.send(a.who, req.get("text"), req.get("pin"), a.cabin, env)
    if a.json:
        out.write(json.dumps(res, ensure_ascii=False) + "\n")
    elif res["why"] in ("touchid_off", "touchid_unavailable"):
        out.write(T("send_why_" + res["why"]) + "\n")
    elif res["why"] and res["why"].startswith("touchid_"):
        out.write(T("send_why_touchid", why=res["why"][len("touchid_"):]) + "\n")
    elif res["why"]:
        out.write(T("send_why_" + res["why"]) + "\n")
    else:
        out.write(T("send_done", stage=res["stage"], seat=res["seat"] or "-", detail=res["detail"] or "") + "\n")
    return res["rc"]


def cmd_room(a, env, out):
    import chat_roster as CR
    owner = CR.friend_owner(a.who or "")
    pl = room_payload(owner, env)
    if a.json:
        out.write(json.dumps(pl, ensure_ascii=False) + "\n")
        return 0 if pl else 1
    if not pl:
        out.write(T("room_none") + "\n")
        return 1
    ship = pl["ship"]
    out.write(T("room_head", name=ship.get("name") or "-", sign=ship.get("sign") or "-", line=ship.get("line") or "-") + "\n")
    for s in pl["seats"]:
        out.write(T("room_seat", emoji=s.get("emoji") or "·", seat=s.get("seat"), name=s.get("name") or "-",
                    role=s.get("role") or "-", floor=s.get("floor") or "-", now=s.get("now") or "") + "\n")
    return 0


def cmd_world(a, env, out):
    pl = world_payload(env)
    if a.json:
        out.write(json.dumps(pl, ensure_ascii=False) + "\n")
        return 0
    if not pl["cabins"]:
        out.write(T("world_none") + "\n")
    for c in pl["cabins"]:
        out.write(T("world_cabin", name=c.get("display_name") or "-", country=c.get("country") or "-", cabin=c["cabin"],
                    fresh="" if c["fresh"] else T("world_stale")) + "\n")
        for x in c["seats"]:
            out.write(T("world_seat", seat=x["seat"], name=x.get("name") or "-", state=T("state_" + x["state"]),
                        now=x.get("now") or "") + "\n")
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


def selftest(env, fetch=True):
    """自我檢查(第 27.7 節):只讀、不寄信。回 [(項目, ok, 參數)];ok None = 略過(前一項沒過就查不了)。"""
    import chat_roster as CR
    import nostr_keys as NK
    rec = status_record(env)
    prof = CR.read_profile(env) or {}
    w = rec.get("worker") or {}
    rows = [("switch", rec["enabled"], {}),
            ("profile", bool(prof.get("display_name") and prof.get("country")),
             {"name": prof.get("display_name") or "-", "country": prof.get("country") or "-"}),
            ("venv", rec["venv"] == "ok", {"pin": NOSTR_SDK_PIN}),
            ("bridge", rec["running"], {}),
            ("heartbeat", rec["heartbeat"], {})]
    seen = w.get("relays") or {}
    for u in CR.relays(env):
        rows.append(("relay", (seen.get(u) == "CONNECTED") if seen else None, {"url": u}))
    c = w.get("clock") or {}
    off = c.get("offset")
    rows.append(("clock", None if off is None else not c.get("warn"), {"offset": None if off is None else round(off, 1)}))
    m = NK.read_map(env)
    owner = (m.get("owner") or {}).get("npub")
    pub = set(prof.get("public") or [])
    seats = [e for e in m["seats"] if e.get("status") == "active" and e["seat5"] in pub]
    if not seats:
        rows.append(("published_none", False, {}))
    for e in seats:
        addr = "%s/%s/%s" % (owner, e.get("cabin") or "main", e["seat5"])
        if not (fetch and rec["venv"] == "ok" and rec["heartbeat"]):
            rows.append(("seat", None, {"seat": e["seat5"], "why": "-"}))
            continue
        rc, got = _run_nostr("fetch", bridge_root(env), env, timeout=60, extra=[addr])
        ok = rc == 0 and (got.get("seat") or {}).get("npub") == e["npub"] and bool(got.get("dm_relays"))
        rows.append(("seat", ok, {"seat": e["seat5"], "why": got.get("why") or "-"}))
    return rows


def cmd_selftest(a, env, out):
    rows = selftest(env)
    if a.json:
        out.write(json.dumps([{"item": k, "ok": ok, **p} for k, ok, p in rows], ensure_ascii=False) + "\n")
    else:
        for k, ok, p in rows:
            mark = "✓" if ok else ("–" if ok is None else "✗")
            out.write("%s %s\n" % (mark, T("self_" + k + ("_ok" if ok else ("_skip" if ok is None else "_bad")), **p)))
    bad = [k for k, ok, _p in rows if ok is False]
    if not a.json:
        out.write(T("self_all_ok" if not bad else "self_some_bad", n=len(bad)) + "\n")
    return 0 if not bad else 1


def _passphrase(a, twice):
    """通關密語:--passphrase-stdin 讀一行(腳本用);否則要終端機,人親手輸入(AI 席跑會停在這裡、照實說要人來)。
    不從命令列參數拿(會進 shell 歷史與行程列表)。"""
    if a.passphrase_stdin:
        return sys.stdin.readline().rstrip("\n")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    import getpass
    first = getpass.getpass(T("backup_ask"))
    if twice and getpass.getpass(T("backup_ask_again")) != first:
        return ""
    return first


def backup_paths_file(env=None):
    return chat_home(env) / "backup-paths.json"


def note_backup_path(path, env=None):
    """記下備份檔在哪(只記路徑):設主人密語時拿來試解,同一組就拒(第 37.7 節 K2 第 14.5 節②)。"""
    p = backup_paths_file(env)
    try:
        cur = json.loads(p.read_text(encoding="utf-8"))
        cur = cur if isinstance(cur, list) else []
    except (OSError, ValueError):
        cur = []
    full = str(Path(path).expanduser().resolve())
    cur = [x for x in cur if isinstance(x, str) and x != full][-19:] + [full]
    write_json(str(p), cur)
    os.chmod(str(p), 0o600)


def known_backups(env=None):
    try:
        cur = json.loads(backup_paths_file(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [x for x in cur if isinstance(x, str) and Path(x).is_file()] if isinstance(cur, list) else []


def cmd_backup(a, env, out):
    import chat_backup as CB
    pw = _passphrase(a, twice=a.cmd == "backup")
    if pw is None:
        out.write(T("backup_need_tty") + "\n")
        return 2
    try:
        if a.cmd == "backup":
            n = CB.backup(a.file, pw, env)
            write_json(str(backup_state_path(env)), {"v": VERSION, "at": int(time.time())})   # 第 37.4 節:不記檔名、不記密語
            os.chmod(str(backup_state_path(env)), 0o600)
            out.write(T("backup_done", n=n, file=a.file) + "\n")
            note_backup_path(a.file, env)
        else:
            r = CB.restore(a.file, pw, env, replace=a.replace, chat_on=is_enabled(env))
            out.write(T("restore_done_" + r["mode"], n=r["keys"], owner=r["owner"]) + "\n")
    except CB.BackupError as err:
        out.write(T("backup_err_" + str(err), n=CB.MIN_PASS) + "\n")
        return 2
    finally:
        pw = None
    return 0


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
    out.write(T("status_friends", n=rec["friends"]) + "\n")
    if rec["mine"]:
        out.write(T("status_mine", n=rec["mine"]) + "\n")
    if rec["refused_7d"]:
        out.write(T("status_refused", n=rec["refused_7d"]) + "\n")
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
    import chat_owner_cli as OC
    if OC.owner_status(env)["role"] == "both":
        out.write(T("status_owner_both") + "\n")          # @abb89 10-18 M1:明文與加密的主人金鑰並存
    if rec["healthy"] is False:
        out.write(T("status_unhealthy_" + rec["why"]) + "\n")
    if rec["backup"]["due"]:
        out.write(T("backup_hint") + "\n")
    for line in power_lines(env):
        out.write(line + "\n")
    import chat_pair as CP
    last = CP.last_command(bridge_root(env))
    if last and time.time() - int(last.get("at") or 0) < 86400:     # 第 33.6 節:電腦上看得到「手機下了指令」
        import touchid_approve as TA                                  # @abb89 F6:帳本 AI 改得到 ⇒ 清字元、只供參考
        out.write(T("status_phone_cmd", when=time.strftime("%H:%M", time.localtime(int(last["at"]))),
                    via=TA.clean(last.get("via", ""), 40), head=TA.clean(last.get("head", ""), 40)) + "\n")
    return 0


def power_lines(env):
    """10-18 Andy:這台會不會睡、有沒有撐著;自己的其他機器版本落後(只提醒自己的機器,不對好友)。"""
    import chat_power as CP
    import chat_roster as CR
    root = bridge_root(env)
    lines = []
    info = CP.power_info(root)
    if info["sleep_min"]:
        key = "status_power_kept" if info["kept_awake"] and info["ac"] else (
            "status_power_battery" if info["kept_awake"] else "status_power_sleeps")
        lines.append(T(key, n=info["sleep_min"]))
    mine_ver = CR._aib_version()
    peers = GF.read_peers(root)
    for f in CR.read_friends(env, "mine"):
        v = CR.clean_version((peers.get(f["owner"]) or {}).get("aib"))
        if v and mine_ver and CR.clean_version(mine_ver) and \
                tuple(int(x) for x in v.split(".")) < tuple(int(x) for x in mine_ver.split(".")):
            lines.append(T("status_mine_old", name=f.get("name") or f["owner"][:16], v=v, mine=mine_ver))
    return lines


def cmd_awake(a, env, out):
    """aib chat awake [on|off]:轉接程式當班時,接電源就撐著不睡(caffeinate -s;只在 Mac)。改了下次當班生效。"""
    import chat_power as CP
    if a.value in ("on", "off"):
        CP.set_awake(chat_home(env), a.value == "on")
    on = CP.awake_wanted(chat_home(env))
    out.write(T("awake_" + ("on" if on else "off")) + "\n")
    if a.value and sys.platform == "darwin":
        out.write(T("awake_next_run") + "\n")
    return 0


def main(argv=None, env=None, out=sys.stdout):
    env = os.environ if env is None else env
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["telegram"]:            # 第 39 節:Telegram 互串自己一支(只用標準庫)
        import chat_telegram as TG
        return TG.main(args[1:], env=env, out=out)
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
    fr = sub.add_parser("friend", help=T("help.friend"))
    fr.add_argument("action", choices=("add", "remove"))
    fr.add_argument("who", help=T("help.friend_who"))
    fr.add_argument("--name", help=T("help.friend_name"))
    bl = sub.add_parser("block", help=T("help.block"))
    bl.add_argument("action", nargs="?", choices=("add", "remove"))
    bl.add_argument("who", nargs="?", help=T("help.friend_who"))
    bl.add_argument("--name", help=T("help.friend_name"))
    bl.add_argument("--json", action="store_true")
    rq = sub.add_parser("request", help=T("help.request"))
    rq.add_argument("target", help=T("help.request_target"))
    rq.add_argument("who", nargs="?")
    rq.add_argument("--note", default="", help=T("help.request_note"))
    rq.add_argument("--from", dest="from_seat", help=T("help.request_from"))
    rq.add_argument("--silent", action="store_true", help=T("help.request_silent"))
    rqs = sub.add_parser("requests", help=T("help.requests"))
    rqs.add_argument("--json", action="store_true")
    so = sub.add_parser("social", help=T("help.social"))
    so.add_argument("action", nargs="?", choices=("add", "remove"))
    so.add_argument("p", nargs="?")
    so.add_argument("v", nargs="?")
    so.add_argument("--proof")
    so.add_argument("--json", action="store_true")
    n5 = sub.add_parser("nip05", help=T("help.nip05"))
    n5.add_argument("name", nargs="?")
    n5.add_argument("--clear", action="store_true")
    sh = sub.add_parser("share", help=T("help.share"))
    sh.add_argument("who", nargs="?")
    sh.add_argument("level", nargs="?", choices=("off", "online", "status"))
    sh.add_argument("--default", choices=("off", "online", "status"))
    sh.add_argument("--json", action="store_true")
    il = sub.add_parser("island", help=T("help.island"))
    il.add_argument("who")
    il.add_argument("--json", action="store_true")
    spc = sub.add_parser("space", help=T("help.space"))
    spc.add_argument("who", nargs="?")
    spc.add_argument("level", nargs="?", choices=("none", "island", "room"))
    spc.add_argument("--default", choices=("none", "island"))
    spc.add_argument("--island")
    spc.add_argument("--room")
    spc.add_argument("--json", action="store_true")
    of = sub.add_parser("offer", help=T("help.offer"))
    of.add_argument("action", choices=("buy", "list", "host"))
    of.add_argument("args", nargs="*")
    of.add_argument("--unit", type=int)
    of.add_argument("--json", action="store_true")
    ofs = sub.add_parser("offers", help=T("help.offers"))
    ofs.add_argument("who")
    ofs.add_argument("--json", action="store_true")
    sd = sub.add_parser("send", help=T("help.send"))
    sd.add_argument("who")
    sd.add_argument("--as-human", action="store_true")
    sd.add_argument("--cabin")
    sd.add_argument("--json", action="store_true")
    rm = sub.add_parser("room", help=T("help.room"))
    rm.add_argument("who")
    rm.add_argument("--json", action="store_true")
    rc = sub.add_parser("recent")
    rc.add_argument("--json", action="store_true")
    rc.add_argument("--minutes", type=int, default=RECENT_MINUTES)
    wd = sub.add_parser("world", help=T("help.world"))
    wd.add_argument("--json", action="store_true")
    pr = sub.add_parser("pair", help=T("help.pair"))
    pr.add_argument("action", nargs="?", choices=("start", "confirm", "list", "revoke"))
    pr.add_argument("target", nargs="?", help=T("help.pair_target"))
    pr.add_argument("--seat")
    pr.add_argument("--no-wait", action="store_true")
    pr.add_argument("--json", action="store_true")
    aw = sub.add_parser("awake", help=T("help.awake"))
    aw.add_argument("value", nargs="?", choices=("on", "off"))
    ow = sub.add_parser("owner", help=T("help.owner"))
    ow.add_argument("action", choices=("protect", "status", "repair"))
    ow.add_argument("--json", action="store_true")
    jn = sub.add_parser("join", help=T("help.join"))
    jn.add_argument("owner")
    jn.add_argument("--name", help=T("help.join_name"))
    dv = sub.add_parser("devices", help=T("help.devices"))
    dv.add_argument("action", nargs="?", choices=("list", "add", "revoke", "extend"))
    dv.add_argument("target", nargs="?", help=T("help.devices_target"))
    dv.add_argument("--name", help=T("help.join_name"))
    dv.add_argument("--json", action="store_true")
    ct = sub.add_parser("contacts", help=T("help.contacts"))
    ct.add_argument("--json", action="store_true")
    ct.add_argument("--refresh", metavar="NPUB|all", help=T("help.contacts_refresh"))
    mn = sub.add_parser("mine", help=T("help.mine"))
    mn.add_argument("action", nargs="?", choices=("add", "remove"))
    mn.add_argument("who", nargs="?", help=T("help.mine_who"))
    mn.add_argument("--name", help=T("help.friend_name"))
    mn.add_argument("--json", action="store_true")
    fs = sub.add_parser("friends", help=T("help.friends"))
    fs.add_argument("--json", action="store_true")
    for name in ("backup", "restore"):
        bk = sub.add_parser(name, help=T("help." + name))
        bk.add_argument("file")
        bk.add_argument("--passphrase-stdin", action="store_true", help=T("help.passphrase_stdin"))
        if name == "restore":
            bk.add_argument("--replace", action="store_true", help=T("help.restore_replace"))
    stt = sub.add_parser("selftest", help=T("help.selftest"))
    stt.add_argument("--json", action="store_true")
    rl = sub.add_parser("relays", help=T("help.relays"))
    rl.add_argument("action", nargs="?", choices=("add", "remove", "reset", "test"))
    rl.add_argument("url", nargs="?")
    rl.add_argument("--json", action="store_true")
    rf = sub.add_parser("refused", help=T("help.refused"))
    rf.add_argument("--json", action="store_true")
    au = sub.add_parser("auto", help=T("help.auto"))
    au.add_argument("--quiet", action="store_true")
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
    if a.cmd == "recent":
        out.write(json.dumps(recent_payload(env, minutes=a.minutes), ensure_ascii=False) + "\n")
        return 0
    if a.cmd == "awake":
        return cmd_awake(a, env, out)
    if a.cmd == "pair":
        import chat_pair as CP                          # 第 33 節:主人手機
        return CP.cmd_pair(a, env, out)
    if a.cmd in ("owner", "join", "devices"):
        import chat_owner_cli as OC                     # 第 37.7 節 K2
        return {"owner": OC.cmd_owner, "join": OC.cmd_join, "devices": OC.cmd_devices}[a.cmd](a, env, out)
    if a.cmd == "run":
        return run(env, out=out)
    if a.cmd == "prepare":
        return cmd_prepare(a, env, out)
    if a.cmd == "auto":
        return cmd_auto(a, env, out)
    if a.cmd == "relays":
        return cmd_relays(a, env, out)
    if a.cmd in ("backup", "restore"):
        return cmd_backup(a, env, out)
    if a.cmd == "selftest":
        return cmd_selftest(a, env, out)
    if a.cmd == "refused":
        return cmd_refused(a, env, out)
    if a.cmd in ("friend", "mine", "block"):
        return cmd_friend(a, env, out)
    if a.cmd == "request":
        return cmd_request(a, env, out)
    if a.cmd == "requests":
        return cmd_requests(a, env, out)
    if a.cmd == "contacts":
        return cmd_contacts(a, env, out)
    if a.cmd == "share":
        return cmd_share(a, env, out)
    if a.cmd in ("social", "nip05"):
        if a.cmd == "nip05" and not a.clear and not a.name:
            out.write(T("profile_bad_nip05") + "\n")
            return 2
        return cmd_social(a, env, out)
    if a.cmd == "world":
        return cmd_world(a, env, out)
    if a.cmd == "send":
        return cmd_send(a, env, out)
    if a.cmd == "room":
        return cmd_room(a, env, out)
    if a.cmd == "island":
        return cmd_island(a, env, out)
    if a.cmd == "offer":
        return cmd_offer(a, env, out)
    if a.cmd == "offers":
        return cmd_offers(a, env, out)
    if a.cmd == "space":
        return cmd_space(a, env, out)
    if a.cmd == "friends":
        return cmd_friends(a, env, out)
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
