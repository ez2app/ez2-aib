#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib update auto:有新的正式版就自動跑 aib update(10-23 Andy「更新版本要自動 aib update」;@74e8b)。

    aib update auto on|off|status [--json]
    python3 auto_update.py check --why seat-open|schedule [--detach] [--json]   (開席 / 排程叫的,一般不用自己打)

設定(每台一份):<狀態根>/state/auto-update.json {"v": 1, "on": true|false, "at": <秒>}。
  沒有檔 = 開(預設開,新裝、舊裝都一樣);只有寫了 "on": false 才算關。
  檔壞掉讀不懂 ⇒ 當成關(不確定本人要不要,就不自動動他的程式);status / doctor 會說「設定檔讀不懂」。
  AIB_AUTO_UPDATE=0 ⇒ 這一次不查也不更新(測試 / 臨時用;不寫設定)。
什麼時候查:
  (a) 開席時(真人終端機開的那一次)背景查一次,不擋開席;同一台 10 分鐘內只查一次。
  (b) macOS:launchd 每 6 小時查一次(com.ez2.aib-auto-update;`aib update auto on` 裝、`off` 拆)。
      預設開而還沒裝排程的舊安裝,開席那次查的時候順手裝上。開發用的 worktree 不裝(樹一刪排程就壞)。
      其他系統只有開席那次。
查什麼(不另找伺服器,跟一行安裝用同一個來源):
  核心:git fetch --tags origin,目標 = 一行安裝程式會裝的那一版(公開來源 = 最新的 v* 標籤;ssh 私有來源 = origin/main)。
  外掛:這份 aib 附的 extensions/ 裡受信鑰匙簽過的包,這台已經裝了、版本比較舊的那幾個。
       (付費外掛 ~/.ez2-aib/plugins 的來源核心不知道 ⇒ 只列出來,不自動換。)
什麼時候才真的更新(有一項不合就這次不做,下次查再試):
  沒人拿著測試租約(aib lease;讀不到也當有人)、沒有推包 / git push / aib test 在跑、
  安裝目錄沒有改過沒提交的檔、是單純快轉(歷史分叉就不動)、在 main(或沒有分支)上、
  不是落後太多(一行安裝會整份備份重裝的那種要本人自己跑 aib update)、距離上次自動更新超過 1 小時。
  開始前自己拿測試租約(--exclusive),做完放掉:更新中途不會有測試插進來。
怎麼更新:
  核心 = 跑這份的 aib update(同一支一行安裝程式:只快轉、不蓋本機改動;它本來就會裝附帶的 mod、叫艦長室換新碼)。
    子行程不帶 AIB_INSTALL_URL(只用內建的官方網址)。
  外掛 = aib_ext.install,跟 aib ext install 同一條路:簽章一定要驗過,不帶 --allow-unsigned;驗不過就不裝。
  最後叫一次 room_reload(艦長室還在跑舊碼才送 /api/reload;一樣就不送)。不重開、不 kill 任何 AI 席。
告訴本人:aib doctor 一行、艦長室事件(room-events.jsonl kind=aib_updated,只帶版號)、aib update auto status。
  紀錄寫 <狀態根>/logs/auto-update.log(只有時間、原因、版號;一行安裝的輸出也接在這裡)。
同帳號 AI(pending/GUIDE-same-user-ai-threat-model):
  AI 席叫 `aib update auto off` 沒關係 —— 只是不再自動更新。`on` 也不用本人證明:它只會跑官方來源的快轉與簽過的外掛,
  不多給任何權力(AI 本來就能自己跑 aib update)。設定檔、排程檔、時間戳都是本機檔,AI 改得到 ⇒ 它們只防誤操作,
  不是防線;真正的保證是「只快轉」(git 自己擋)與「外掛簽章」(受信鑰匙驗)。
"""
import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
CORE = HERE.parents[1]               # 套件資料夾(aib/)
ROOT = HERE.parents[2]               # 一行安裝的 checkout(有 .git、bin/aib、extensions/)

SETTING = "auto-update.json"
STATUS = "auto-update-status.json"
LOCK = "auto-update.lock"
LOG = "auto-update.log"
LOG_MAX = 256 * 1024
AGENT_LABEL = "com.ez2.aib-auto-update"
INTERVAL_S = 6 * 3600                # 排程:每 6 小時
CHECK_GAP_S = 10 * 60                # 開席那次:10 分鐘內查過就不再查
ATTEMPT_GAP_S = 3600                 # 真的更新:一小時最多一次
UPDATE_TIMEOUT_S = 30 * 60
LEASE_TTL_S = 45 * 60
CLEAN_GAP = 5                        # 跟 install.sh clean_if_far 的 AIB_CLEAN_GAP 預設一樣
_PATH_EXTRA = ("/opt/homebrew/bin", "/usr/local/bin")
_AGENT_ENV_KEEP = ("HOME", "LANG", "EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME", "EZ2_AIB_LOCALE")   # 只帶路徑 / 語系,不帶 token / 開關
_CHILD_ENV_DROP = ("AIB_INSTALL_URL",)   # 自動更新只用內建的官方安裝網址
SSH_HARDENED = "ssh -o BatchMode=yes -o ForwardAgent=no -o ClearAllForwardings=yes"
BUSY_PATTERNS = (
    ("tests", re.compile(r"(?:^|[\s/])aib\s+test(?:\s|$)|tools/verify/test_entry\.py")),
    ("shipping", re.compile(r"(?:^|[\s/])aib\s+ship(?:\s|$)|tools/release/ship\.py")),
    ("push", re.compile(r"(?:^|[\s/])git(?:\s+-[Cc]\s+\S+)*\s+push(?:\s|$)|git-receive-pack|git-send-pack")),
)


def T(key, **params):
    try:
        import i18n_lib
        v = i18n_lib.t("aib.auto_update." + key, params or None, locale=i18n_lib.resolve_locale())
        if v and not v.startswith("!!"):
            return v
    except Exception:  # noqa: BLE001
        pass
    return key


# ───────────────────────────── 檔案 ─────────────────────────────

def state_root(env=None):
    import seats_dir_lib
    return Path(seats_dir_lib.state_root(os.environ if env is None else env)[0])


def setting_path(env=None):
    return state_root(env) / "state" / SETTING


def status_path(env=None):
    return state_root(env) / "state" / STATUS


def log_path(env=None):
    return state_root(env) / "logs" / LOG


def _write_json(path, d):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_setting(env=None):
    """(開不開, 來源)。來源 ∈ default(沒有檔,預設開)/ set(本人設過)/ unreadable(檔壞 ⇒ 當成關)。"""
    p = setting_path(env)
    if not p.exists():
        return True, "default"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "unreadable"
    if not isinstance(d, dict) or not isinstance(d.get("on"), bool):
        return False, "unreadable"
    return d["on"], "set"


def write_setting(on, env=None, now=None):
    _write_json(setting_path(env), {"v": 1, "on": bool(on), "at": int(time.time() if now is None else now)})


def read_status(env=None):
    try:
        d = json.loads(status_path(env).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_status(d, env=None):
    try:
        _write_json(status_path(env), d)
    except OSError:
        pass


def log(msg, env=None, now=None):
    """一行:時間 + 訊息(只放原因、版號;不放任何 token / 網址參數)。寫不出不擋。"""
    p = log_path(env)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > LOG_MAX:
            keep = p.read_bytes()[-LOG_MAX // 2:]
            p.write_bytes(keep[keep.find(b"\n") + 1:])
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() if now is None else now))
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (stamp, msg))
    except OSError:
        pass


@contextlib.contextmanager
def _lock(env=None):
    """同一時間只有一個在查 / 更新;搶不到 ⇒ yield False(這次跳過)。"""
    p = state_root(env) / "state" / LOCK
    p.parent.mkdir(parents=True, exist_ok=True)
    fh = open(p, "a")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


# ───────────────────────────── 排程(launchd) ─────────────────────────────

def agent_plist_path(home=None):
    return Path(home or os.path.expanduser("~")) / "Library" / "LaunchAgents" / (AGENT_LABEL + ".plist")


def agent_plist(env=None, python=None, script=None, log_file=None):
    """LaunchAgent 內容(dict,給 plistlib)。每 INTERVAL_S 秒跑一次 check --why schedule;環境只帶路徑 / 語系。"""
    env = os.environ if env is None else env
    parts = [d for d in (env.get("PATH") or "").split(os.pathsep) if d]
    for extra in (str(Path(env.get("HOME") or os.path.expanduser("~")) / ".local" / "bin"),) + _PATH_EXTRA:
        if extra not in parts:
            parts.append(extra)
    ev = {k: str(env[k]) for k in _AGENT_ENV_KEEP if env.get(k)}
    ev["PATH"] = os.pathsep.join(parts)
    ev.setdefault("HOME", os.path.expanduser("~"))
    logp = str(log_file or log_path(env))
    return {"Label": AGENT_LABEL,
            "ProgramArguments": [python or sys.executable, script or str(HERE / "auto_update.py"), "check", "--why", "schedule"],
            "StartInterval": INTERVAL_S, "RunAtLoad": False, "ProcessType": "Background", "LowPriorityIO": True,
            "Nice": 10, "EnvironmentVariables": ev, "StandardOutPath": logp, "StandardErrorPath": logp}


def _launchctl(*args):
    try:
        p = subprocess.run(["launchctl"] + list(args), capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stderr or p.stdout).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def _git(root, *args, env=None, timeout=30):
    try:
        p = subprocess.run(["git", "-C", str(root)] + list(args), capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=env)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def dev_worktree(root=None):
    """checkout 是開發用的 worktree(不是正本樹)⇒ 回路徑;否則 None。同 cabin_restart / chat_bridge 的判法。"""
    root = root or ROOT
    rc, top = _git(root, "rev-parse", "--show-toplevel")
    rc2, lst = _git(root, "worktree", "list", "--porcelain")
    canon = next((ln[len("worktree "):] for ln in lst.splitlines() if ln.startswith("worktree ")), "")
    if rc or rc2 or not top or not canon:
        return None
    return None if os.path.realpath(top) == os.path.realpath(canon) else top


def agent_installed(home=None):
    return agent_plist_path(home).is_file()


def install_agent(env=None, home=None, platform=None, launchctl=_launchctl, dev_tree=None, plist=None):
    """回 (installed|mac_only|dev_tree|failed, 說明)。只 macOS;開發用 worktree 不裝。"""
    import plistlib
    env = os.environ if env is None else env
    if (platform or sys.platform) != "darwin":
        return "mac_only", ""
    dev = dev_worktree() if dev_tree is None else dev_tree
    if dev and env.get("AIB_ALLOW_FOREIGN_BINDINGS") != "1":
        return "dev_tree", str(dev)
    path = agent_plist_path(home)
    data = plist or agent_plist(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    Path(data["StandardOutPath"]).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        plistlib.dump(data, fh)
    os.chmod(path, 0o600)
    domain = "gui/%d" % os.getuid()
    launchctl("bootout", "%s/%s" % (domain, AGENT_LABEL))       # 舊的先拿掉(沒裝過會失敗,不管)
    rc, err = launchctl("bootstrap", domain, str(path))
    if rc != 0:
        return "failed", err
    return "installed", str(path)


def remove_agent(home=None, launchctl=_launchctl):
    path = agent_plist_path(home)
    launchctl("bootout", "gui/%d/%s" % (os.getuid(), AGENT_LABEL))
    if path.exists():
        path.unlink()
        return True
    return False


# ───────────────────────────── 版本 ─────────────────────────────

def release_ref(root):
    """一行安裝程式會裝的那一版(install.sh release_ref):公開來源 ⇒ 最新 v* 標籤;ssh 私有來源 / 沒有標籤 ⇒ origin/main。"""
    _rc, url = _git(root, "remote", "get-url", "origin")
    if url.startswith(("ssh://", "git@")):
        return "origin/main"
    _rc, tags = _git(root, "tag", "-l", "v[0-9]*", "--sort=-v:refname")
    tag = tags.splitlines()[0].strip() if tags else ""
    return tag or "origin/main"


def ref_version(root, ref):
    for f in ("aib/VERSION", "VERSION"):
        rc, out = _git(root, "show", "%s:%s" % (ref, f))
        if rc == 0 and out:
            return out.splitlines()[0].strip()
    return None


def vnum(v):
    parts = str(v or "").split(".")
    if len(parts) < 3 or not all(p.isdigit() for p in parts[:3]):
        return None
    return tuple(int(p) for p in parts[:3])


def far_behind(old, new, gap=CLEAN_GAP):
    """一行安裝會「整份備份再重裝」的落差(大 / 中版號不同,或同中版號差 gap 個小版以上)⇒ True。量不到 ⇒ False。"""
    o, n = vnum(old), vnum(new)
    if not o or not n or o >= n:
        return False
    return o[:2] != n[:2] or n[2] - o[2] >= gap


def fetch(root, env=None):
    e = dict(os.environ if env is None else env)
    e.update(GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND=SSH_HARDENED)
    rc, _ = _git(root, "fetch", "-q", "--tags", "origin", env=e, timeout=120)
    return rc == 0


def core_plan(root):
    """fetch 之後:{"want", "have", "latest", "behind", "why"}。why ∈ None(可以快轉)/ dirty / diverged / branch / no_ref。"""
    want = release_ref(root)
    _rc, before = _git(root, "rev-parse", "HEAD")
    rc, after = _git(root, "rev-parse", "%s^{commit}" % want)
    plan = {"want": want, "have": ref_version(root, "HEAD"), "latest": ref_version(root, want) if rc == 0 else None,
            "behind": False, "why": None}
    if rc != 0 or not after:
        plan["why"] = "no_ref"
        return plan
    if before == after or _git(root, "merge-base", "--is-ancestor", after, before)[0] == 0:
        return plan                                           # 已在這版、或比正式版還新 ⇒ 不往回退
    plan["behind"] = True
    if _git(root, "status", "--porcelain", "--untracked-files=no")[1]:
        plan["why"] = "dirty"
    elif _git(root, "merge-base", "--is-ancestor", before, after)[0] != 0:
        plan["why"] = "diverged"
    else:
        rc, br = _git(root, "symbolic-ref", "-q", "--short", "HEAD")
        if rc == 0 and br not in ("main", "master"):
            plan["why"] = "branch"
    return plan


# ───────────────────────────── 安不安全 ─────────────────────────────

def proc_rows():
    """[(pid, 命令列)];量不到 ⇒ []。"""
    try:
        p = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=10,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for ln in p.stdout.splitlines():
        pid, _, cmd = ln.strip().partition(" ")
        if pid.isdigit():
            rows.append((int(pid), cmd.strip()))
    return rows


def busy_procs(rows, own=None):
    """正在跑的 aib test / 推包 / git push ⇒ 原因(tests / shipping / push);都沒有 ⇒ None。"""
    own = set(own or (os.getpid(), os.getppid()))
    for pid, cmd in rows:
        if pid in own:
            continue
        for why, rx in BUSY_PATTERNS:
            if rx.search(cmd):
                return why
    return None


def _host_lease():
    import importlib.util
    spec = importlib.util.spec_from_file_location("aib_host_lease_au", str(CORE / "tools" / "governance" / "host_lease.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def lease_busy(env=None, now=None):
    """測試租約:有人拿著 / 讀不到 / 有推包在等 ⇒ 原因;空著或已過期 ⇒ None。"""
    try:
        HL = _host_lease()
        path = HL.lease_path(env)
        state, _cur, _why = HL.status(path, time.time() if now is None else now)
        if state in ("held", "unreadable"):
            return "lease"
        if HL.ship_waiting(path, now):
            return "shipping"
    except Exception:  # noqa: BLE001 —— 量不到就當有人在用(寧可下次再試)
        return "lease"
    return None


def _lease_cmd(action, run_id, env=None):
    tool = str(CORE / "tools" / "governance" / "host_lease.py")
    argv = [sys.executable, tool, action, "--run-id", run_id]
    if action == "acquire":
        argv += ["--owner", "aib-auto-update", "--what", "aib update (auto)", "--pid", str(os.getpid()),
                 "--ttl", str(LEASE_TTL_S), "--exclusive"]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL, env=env)
        return p.returncode
    except (OSError, subprocess.SubprocessError):
        return 1


# ───────────────────────────── 外掛 ─────────────────────────────

def _ext():
    import aib_ext
    return aib_ext


def plugins_behind(base=None):
    """這份 aib 附的 extensions/ 裡受信鑰匙簽過、這台已裝、而且裝的版本比較舊的包。沒簽 / 簽章驗不過的不列(不會裝)。"""
    X = _ext()
    import ext_kinds as K
    base = Path(base) if base else X.bundled_dir()
    out = []
    if not base.is_dir():
        return out
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        try:
            m = json.loads((d / X.MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        kind = K.kind_of_label(str(m.get("kind") or ""))
        if kind not in K.INSTALLABLE or m.get("requires_addon") or not X.signed_ok(m):
            continue
        try:
            dst = X.target(kind, str(m.get("name") or ""))
            have = json.loads((dst / X.MANIFEST).read_text(encoding="utf-8")).get("version")
        except (ValueError, OSError):
            continue
        if X._num(have) < X._num(m.get("version")):
            out.append({"name": m["name"], "kind": kind, "have": have, "want": m.get("version"), "dir": str(d)})
    return out


def install_plugin(folder, out):
    """跟 aib ext install 同一條路;一律驗簽章(不給 --allow-unsigned)。回結束碼。"""
    X = _ext()
    try:
        m = json.loads((Path(folder) / X.MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 3
    if not X.signed_ok(m):
        return 3
    return X.install(folder, allow_unsigned=False, out=out)


def unsourced_plugins(env=None):
    """裝在 <EZ2_AIB_HOME>/plugins 的外掛(付費那種);核心不知道它們的來源 ⇒ 只列名字與版號,不自動換。"""
    e = os.environ if env is None else env
    base = Path(e.get("EZ2_AIB_HOME") or (Path(e.get("HOME") or Path.home()) / ".ez2-aib")) / "plugins"
    out = []
    try:
        dirs = sorted(p for p in base.iterdir() if p.is_dir())
    except OSError:
        return out
    for d in dirs:
        try:
            m = json.loads((d / "plugin.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(m, dict) and m.get("name"):
            out.append({"name": str(m["name"]), "version": str(m.get("version") or "?")})
    return out


# ───────────────────────────── 查 / 更新 ─────────────────────────────

def _child_env(env):
    e = dict(env)
    for k in _CHILD_ENV_DROP:
        e.pop(k, None)
    return e


def run_core_update(env, root=None):
    """跑這份 aib 的 aib update(一行安裝程式:只快轉、不蓋本機改動);輸出接到 auto-update.log。回結束碼。"""
    root = root or ROOT
    lp = log_path(env)
    lp.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(lp, "a", encoding="utf-8") as fh:
            p = subprocess.run([sys.executable, str(root / "bin" / "aib"), "update"], stdin=subprocess.DEVNULL,
                               stdout=fh, stderr=subprocess.STDOUT, env=_child_env(env), cwd=str(root),
                               timeout=UPDATE_TIMEOUT_S)
        return p.returncode
    except (OSError, subprocess.SubprocessError):
        return 1


def _room_reload(env):
    try:
        import room_reload
        return room_reload.reload_room(env).get("result")
    except Exception:  # noqa: BLE001
        return "failed"


def _note_event(old, new, env, now):
    try:
        import room_events
        room_events.note_aib_updated(old, new, env=env, now=now)
    except Exception:  # noqa: BLE001 —— 事件記不進去不影響更新
        pass


def check(env=None, why="schedule", now=None, root=None, platform=None, home=None, launchctl=_launchctl,
          rows=None, do_fetch=fetch, run_update=run_core_update, lease=_lease_cmd, install=install_plugin,
          ext_base=None, room=_room_reload, dev_tree=None, lease_state=lease_busy):
    """查一次,安全就更新。回 {"result": …, …};result 見 RESULTS。可注入的全部是外部動作(測試用)。"""
    env = dict(os.environ if env is None else env)
    root = Path(root or ROOT)
    now = time.time() if now is None else now
    if env.get("AIB_AUTO_UPDATE", "") == "0":
        return {"result": "disabled_env"}
    on, src = read_setting(env)
    if not on:
        return {"result": "unreadable" if src == "unreadable" else "off"}
    res = {"why": why, "at": int(now)}
    dev = dev_worktree(root) if dev_tree is None else dev_tree
    plat = platform or sys.platform
    if why == "seat-open" and plat == "darwin" and not dev and not agent_installed(home):
        res["agent"] = install_agent(env, home=home, platform=plat, launchctl=launchctl, dev_tree=dev)[0]
    with _lock(env) as got:
        if not got:
            return dict(res, result="busy")
        st = read_status(env)

        def done(result, **kw):
            res.update(kw, result=result)
            if result not in ("checked_recently",):
                st.update(last_result=result, last_result_at=int(now), last_why=why)
                for k in ("have", "latest", "plugins_behind"):
                    if k in res:
                        st[k] = res[k]
                write_status(st, env)
                log("why=%s result=%s have=%s latest=%s" % (why, result, res.get("have", "-"), res.get("latest", "-")),
                    env, now)
            return res

        if not (root / ".git").exists():
            return done("not_git")
        if dev:
            return done("dev_tree")
        if why == "seat-open" and now - float(st.get("last_check") or 0) < CHECK_GAP_S:
            return done("checked_recently")
        busy = lease_state(env, now) or busy_procs(proc_rows() if rows is None else rows)
        if busy:
            return done("refused_" + busy)
        if now - float(st.get("last_attempt") or 0) < ATTEMPT_GAP_S:
            return done("too_soon")
        st["last_check"] = int(now)
        if not do_fetch(root, env):
            return done("offline")
        plan = core_plan(root)
        res.update(have=plan["have"], latest=plan["latest"])
        behind = plugins_behind(ext_base) if env.get("AIB_EXT_AUTO", "") != "0" else []
        res["plugins_behind"] = [b["name"] for b in behind]
        if plan["why"] == "no_ref":
            return done("no_ref")
        if not plan["behind"] and not behind:
            return done("up_to_date")
        if plan["behind"]:
            if plan["why"]:
                return done("refused_" + plan["why"])
            gap = env.get("AIB_CLEAN_GAP", "")
            if far_behind(plan["have"], plan["latest"], int(gap) if gap.isdigit() else CLEAN_GAP):
                return done("refused_far_behind")
        rid = "auto-update-%d-%d" % (os.getpid(), int(now))
        if lease("acquire", rid, env) != 0:
            return done("refused_lease")
        st["last_attempt"] = int(now)
        write_status(st, env)
        try:
            old = plan["have"]
            if plan["behind"]:
                rc = run_update(_child_env(env), root)       # 子行程不帶 AIB_INSTALL_URL:只用內建的官方安裝網址
                res["update_rc"] = rc
                if rc != 0:
                    return done("failed", have=ref_version(root, "HEAD"))
            # 核心換了 ⇒ 一行安裝已用**新版**程式跑過 aib ext auto,這支舊碼不再自己裝;核心沒換才在這裡裝落後的外掛
            import io
            buf, plugin_rows = io.StringIO(), []
            for b in ([] if plan["behind"] else behind):
                r = install(b["dir"], buf)
                plugin_rows.append({"name": b["name"], "from": b["have"], "to": b["want"], "rc": r})
                log("plugin %s %s -> %s rc=%s" % (b["name"], b["have"], b["want"], r), env, now)
            res["plugins"] = plugin_rows
            res["room"] = room(env)
        finally:
            lease("release", rid, env)
        new = ref_version(root, "HEAD")
        res["have"] = new
        if plan["behind"] and new and new != old:
            st["last_update"] = {"from": old, "to": new, "at": int(now)}
            _note_event(old, new, env, now)
        if plugin_rows:
            st["last_plugins"] = [dict(p, at=int(now)) for p in plugin_rows]
        bad = [p for p in plugin_rows if p["rc"] != 0]
        return done("plugins_failed" if bad else "updated")


# ───────────────────────────── 給人看 ─────────────────────────────

def _when(epoch):
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(epoch)))
    except (TypeError, ValueError):
        return "?"


def status_payload(env=None, home=None, platform=None):
    on, src = read_setting(env)
    st = read_status(env)
    plat = platform or sys.platform
    return {"on": on, "source": src, "schedule": ("installed" if agent_installed(home) else "missing") if plat == "darwin"
            else "not_mac", "interval_s": INTERVAL_S, "have": st.get("have"), "latest": st.get("latest"),
            "last_check": st.get("last_check"), "last_result": st.get("last_result"),
            "last_result_at": st.get("last_result_at"), "last_update": st.get("last_update"),
            "plugins_behind": st.get("plugins_behind") or [], "last_plugins": st.get("last_plugins") or [],
            "unsourced_plugins": [p["name"] for p in unsourced_plugins(env)], "log": str(log_path(env))}


def status_lines(p):
    out = [T("status.on_" + ("default" if p["source"] == "default" else "set") if p["on"]
             else ("status.unreadable" if p["source"] == "unreadable" else "status.off"))]
    out.append(T("status.schedule_" + p["schedule"], hours=p["interval_s"] // 3600))
    if p.get("have"):
        out.append(T("status.versions", have=p["have"], latest=p.get("latest") or "?", when=_when(p.get("last_check"))))
    lu = p.get("last_update")
    if isinstance(lu, dict) and lu.get("to"):
        out.append(T("status.last_update", old=lu.get("from") or "?", new=lu["to"], when=_when(lu.get("at"))))
    if p.get("last_result"):
        out.append(T("status.last_result", why=T("why." + p["last_result"]), when=_when(p.get("last_result_at"))))
    if p.get("unsourced_plugins"):
        out.append(T("status.unsourced", names=", ".join(p["unsourced_plugins"])))
    out.append(T("status.log", path=p["log"]))
    return out


def doctor_line(env=None):
    """(ok|note, 一行)。aib doctor 用。唯讀。"""
    p = status_payload(env)
    if not p["on"]:
        return "note", T("doctor.unreadable" if p["source"] == "unreadable" else "doctor.off")
    lu = p.get("last_update")
    if isinstance(lu, dict) and lu.get("to"):
        return "ok", T("doctor.updated", new=lu["to"], when=_when(lu.get("at")))
    if p.get("last_result"):
        return "ok", T("doctor.on_last", why=T("why." + p["last_result"]), when=_when(p.get("last_result_at")))
    return "ok", T("doctor.on")


def cmd_on(env=None, out=sys.stdout, home=None, platform=None, launchctl=_launchctl, dev_tree=None):
    write_setting(True, env)
    out.write(T("set_on") + "\n")
    kind, info = install_agent(env, home=home, platform=platform, launchctl=launchctl, dev_tree=dev_tree)
    out.write(T("agent_" + kind, path=info, hours=INTERVAL_S // 3600, err=info) + "\n")
    log("set on agent=%s" % kind, env)
    return 0 if kind in ("installed", "mac_only", "dev_tree") else 1


def cmd_off(env=None, out=sys.stdout, home=None, launchctl=_launchctl):
    write_setting(False, env)
    removed = remove_agent(home, launchctl)
    out.write(T("set_off") + "\n")
    if removed:
        out.write(T("agent_removed") + "\n")
    log("set off", env)
    return 0


def _detach():
    """開席那次:fork 一份到背景、脫離終端機(席位關掉也不會被帶走),父行程立刻回去。"""
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    fd = os.open(os.devnull, os.O_RDWR)
    for n in (0, 1, 2):
        os.dup2(fd, n)


def main(argv=None, out=sys.stdout):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="aib update auto", description=T("help.desc"))
    ap.add_argument("action", nargs="?", default="status", choices=["on", "off", "status", "check"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--why", default="schedule", choices=["schedule", "seat-open", "manual"])
    ap.add_argument("--detach", action="store_true")
    a = ap.parse_args(argv)
    if a.action == "on":
        return cmd_on(out=out)
    if a.action == "off":
        return cmd_off(out=out)
    if a.action == "check":
        if a.detach:
            _detach()
        res = check(why=a.why)
        if a.json:
            out.write(json.dumps(res, ensure_ascii=False) + "\n")
        return 0
    p = status_payload()
    if a.json:
        out.write(json.dumps(p, ensure_ascii=False) + "\n")
    else:
        out.write("\n".join(status_lines(p)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
