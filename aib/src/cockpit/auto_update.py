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
  核心:git fetch --tags origin,目標 = 一行安裝程式會裝的那一版(公開來源 = 最新的 v* 標籤;私有來源(git@ 開頭)= origin/main)。
  外掛:(1) 這份 aib 附的 extensions/ 裡受信鑰匙簽過的包;(2) 付費外掛(<狀態根>/plugins)⇒ aib-plugins 倉的 main
       (10-23 @abb89 裁)。兩種都只看這台已經裝了、版本比較舊、而且簽章驗得過(aib_ext.signed_ok)的那幾個。
       aib-plugins 取法跟手動裝一樣:把倉裡那個資料夾原樣拿出來交給 aib ext install(這裡用 git fetch 到
       <狀態根>/cache/auto-update/aib-plugins.git,再 git archive 出那一個資料夾);來源網址 AIB_AUTO_UPDATE_PLUGIN_SOURCE
       可換(測試 / 鏡像用 —— 換成什麼都一樣要過簽章)。
  不自動更新清單(設定檔 exclude):預設 hostguard(這台的 hostguard 是正式站部署來源,換版要主人點頭)。
       `aib update auto exclude add|remove|list <外掛>`;拿掉預設那幾個要本人 Touch ID(只擋正式入口;設定檔本身 AI 改得到,
       真正的保護仍是簽章)。清單裡的外掛,自動更新這支不會換(status 會列出來);但附帶的外掛(extensions/)在核心
       更新時,一行安裝程式的 aib ext auto 照舊會換成新簽的那份 —— 那條不看這份清單。hostguard 不在 extensions/,不受影響。
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
import io
import shutil
import stat
import tarfile
import tempfile
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
_CHILD_ENV_DROP = ("AIB_INSTALL_URL",)
DEFAULT_EXCLUDE = ("hostguard",)     # 10-23 @abb89:這台的 hostguard = 正式站部署來源,換版要主人點頭
PLUGIN_SOURCE = "git@github.com:ez2app/aib-plugins.git"   # 付費外掛的簽章來源(main);AIB_AUTO_UPDATE_PLUGIN_SOURCE 可換
PLUGIN_BRANCH = "main"   # 自動更新只用內建的官方安裝網址
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


def _raw_setting(env=None):
    try:
        d = json.loads(setting_path(env).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_setting(on, env=None, now=None):
    d = _raw_setting(env)
    d.update(v=1, on=bool(on), at=int(time.time() if now is None else now))
    d["exclude"] = read_excludes(env)
    _write_json(setting_path(env), d)


def read_excludes(env=None):
    """不自動更新的外掛名單。設定檔沒寫過 exclude(或讀不懂)⇒ 預設 DEFAULT_EXCLUDE。"""
    v = _raw_setting(env).get("exclude")
    if not isinstance(v, list):
        return list(DEFAULT_EXCLUDE)
    return sorted({x for x in v if isinstance(x, str) and _ext().name_ok(x)})


def write_excludes(names, env=None, now=None):
    d = _raw_setting(env)
    on, src = read_setting(env)
    d.update(v=1, on=on if src != "unreadable" else False, at=int(time.time() if now is None else now))
    d["exclude"] = sorted(set(names))
    _write_json(setting_path(env), d)


CLOCK_SKEW_S = 5 * 60               # 時間戳比現在晚超過這麼多 ⇒ 當成時鐘跳過,不算數
_TS_KEYS = ("last_check", "last_attempt", "last_result_at")
_STR_KEYS = ("have", "latest", "last_why", "plugin_source")
_LIST_KEYS = ("plugins_behind", "plugins_excluded", "plugins_unsigned")
_RESULT_RX = re.compile(r"[a-z_]{1,40}")


def _num_ok(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e12


def clean_status(d, now=None):
    """狀態檔內容 ⇒ (清過的 dict, 有沒有時鐘跳過)。型別不對的欄位當作沒有;比現在晚超過 CLOCK_SKEW_S 的時間戳也丟掉
    (時鐘曾經往前跳過,不然之後每次都 too_soon 到真的時間追上)。狀態檔是本機檔、誰都改得到 ⇒ 這裡只求不當掉、不卡死。"""
    now = time.time() if now is None else now
    out, skew = {}, False
    if not isinstance(d, dict):
        return out, skew
    for k in _TS_KEYS:
        v = d.get(k)
        if _num_ok(v):
            if v > now + CLOCK_SKEW_S:
                skew = True
            else:
                out[k] = int(v)
    for k in _STR_KEYS:
        if isinstance(d.get(k), str) and len(d[k]) <= 64:
            out[k] = d[k]
    if isinstance(d.get("last_result"), str) and _RESULT_RX.fullmatch(d["last_result"]):
        out["last_result"] = d["last_result"]
    for k in _LIST_KEYS:
        v = d.get(k)
        if isinstance(v, list):
            out[k] = [x for x in v if isinstance(x, str)][:50]
    lu = d.get("last_update")
    if isinstance(lu, dict) and isinstance(lu.get("to"), str) and _num_ok(lu.get("at")):
        out["last_update"] = {"from": lu.get("from") if isinstance(lu.get("from"), str) else None, "to": lu["to"],
                              "at": int(lu["at"])}
    lp = d.get("last_plugins")
    if isinstance(lp, list):
        out["last_plugins"] = [{k: x.get(k) for k in ("name", "from", "to", "rc", "at")} for x in lp
                               if isinstance(x, dict) and isinstance(x.get("name"), str)][:50]
    if d.get("clock_skew") is True or skew:
        out["clock_skew"] = True
    return out, skew


def read_status(env=None, now=None):
    """清過的狀態(見 clean_status)。讀不到 / 不是 JSON ⇒ {}。"""
    try:
        d = json.loads(status_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return clean_status(d, now)[0]


def write_status(d, env=None):
    try:
        _write_json(status_path(env), d)
    except OSError:
        pass


def _open_log(env=None):
    """附加模式開紀錄檔;紀錄檔是連結 / 不是一般檔 ⇒ None(不寫,也不寫穿到連結指的檔)。
    太大 ⇒ 只讀最後 LOG_MAX/2,寫到暫存檔再 os.replace(換掉的是路徑本身)。"""
    p = log_path(env)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            st = None
        if st is not None and not stat.S_ISREG(st.st_mode):
            return None
        if st is not None and st.st_size > LOG_MAX:
            fd = os.open(str(p), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as fh:
                fh.seek(-(LOG_MAX // 2), os.SEEK_END)
                keep = fh.read()
            tmp = p.with_name(p.name + ".tmp.%d" % os.getpid())
            tmp.write_bytes(keep[keep.find(b"\n") + 1:])
            os.replace(tmp, p)
        fd = os.open(str(p), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        return os.fdopen(fd, "a", encoding="utf-8")
    except OSError:
        return None


def log(msg, env=None, now=None):
    """一行:時間 + 訊息(只放原因、版號;不放任何 token / 網址參數)。寫不出不擋。"""
    fh = _open_log(env)
    if fh is None:
        return
    try:
        with fh:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() if now is None else now))
            fh.write("%s %s\n" % (stamp, msg))
    except OSError:
        pass


@contextlib.contextmanager
def _lock(env=None):
    """同一時間只有一個在查 / 更新;搶不到 ⇒ yield False(這次跳過)。"""
    p = state_root(env) / "state" / LOCK
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fh = open(p, "a")
    except OSError:                  # 鎖檔位置是資料夾 / 權限壞 ⇒ 不知道別人在不在跑 ⇒ 這次不做
        yield False
        return
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
        m = _read_obj(d / X.MANIFEST)
        if m is None:
            continue
        kind = K.kind_of_label(str(m.get("kind") or ""))
        if kind not in K.INSTALLABLE or m.get("requires_addon") or not X.signed_ok(m):
            continue
        try:
            dst = X.target(kind, str(m.get("name") or ""))
        except ValueError:
            continue
        have = (_read_obj(dst / X.MANIFEST) or {}).get("version")
        if have is None:
            continue
        if X._num(have) < X._num(m.get("version")):
            out.append({"name": m["name"], "kind": kind, "have": have, "want": m.get("version"), "dir": str(d)})
    return out


def _read_obj(path):
    """JSON 檔 ⇒ dict;讀不到 / 不是 JSON / 不是物件(例:清單)⇒ None。"""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def install_plugin(folder, out):
    """跟 aib ext install 同一條路;一律驗簽章(不給 --allow-unsigned)。回結束碼。"""
    X = _ext()
    m = _read_obj(Path(folder) / X.MANIFEST)
    if m is None or not X.signed_ok(m):
        return 3
    return X.install(folder, allow_unsigned=False, out=out)


def plugin_cache(env=None):
    return state_root(env) / "cache" / "auto-update" / "aib-plugins.git"


def fetch_plugin_source(env=None):
    """aib-plugins 的 main 抓到本機快取(bare);回快取路徑,抓不到 ⇒ None。不帶任何認證參數、不開互動提示。"""
    env = dict(os.environ if env is None else env)
    src = env.get("AIB_AUTO_UPDATE_PLUGIN_SOURCE") or PLUGIN_SOURCE
    cache = plugin_cache(env)
    e = dict(env, GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND=SSH_HARDENED)
    if not (cache / "HEAD").exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        if _git(cache.parent, "init", "-q", "--bare", cache.name, env=e)[0] != 0:
            return None
    rc, _ = _git(cache, "fetch", "-q", "--no-tags", src, "+refs/heads/%s:refs/heads/%s" % (PLUGIN_BRANCH, PLUGIN_BRANCH),
                 env=e, timeout=180)
    return cache if rc == 0 else None


def _source_manifest(cache, name):
    rc, out = _git(cache, "show", "%s:%s/aib-ext.json" % (PLUGIN_BRANCH, name))
    if rc != 0:
        return None
    try:
        m = json.loads(out)
    except ValueError:
        return None
    return m if isinstance(m, dict) else None


def paid_behind(cache, excluded=(), env=None):
    """這台已裝的付費外掛(<狀態根>/plugins)在 aib-plugins main 有比較新、簽章驗得過的版本 ⇒ 列出來。
    回 (落後清單, 被排除的清單, 簽章沒過的清單)。"""
    X = _ext()
    import ext_kinds as K
    home = state_root(env) / K.KINDS["plugin"]["home"]
    behind, skipped, unsigned = [], [], []
    try:
        dirs = sorted(p for p in home.iterdir() if p.is_dir() and not p.name.endswith(".installing"))
    except OSError:
        return behind, skipped, unsigned
    for d in dirs:
        mine = _read_obj(d / X.MANIFEST)
        if mine is None:
            if (d / X.MANIFEST).exists():
                log("plugin %s skipped: installed manifest is not a JSON object" % d.name, env)
            continue
        have = mine.get("version")
        m = _source_manifest(cache, d.name) if cache else None
        if not m or m.get("name") != d.name or K.kind_of_label(str(m.get("kind") or "")) != "plugin":
            continue
        if X._num(have) >= X._num(m.get("version")):
            continue
        if d.name in excluded:
            skipped.append(d.name)
        elif not X.signed_ok(m):
            unsigned.append(d.name)
        else:
            behind.append({"name": d.name, "kind": "plugin", "have": have, "want": m.get("version"), "source": "aib-plugins"})
    return behind, skipped, unsigned


def stage_paid(cache, name, env=None):
    """git archive 出 aib-plugins main 的那一個資料夾到暫存處;回資料夾路徑(失敗 ⇒ None)。路徑跑出去 / 連結一律拒。"""
    rc = subprocess.run(["git", "-C", str(cache), "archive", "--format=tar", PLUGIN_BRANCH, name],
                        capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    if rc.returncode != 0:
        return None
    base = state_root(env) / "cache" / "auto-update"
    try:
        base.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="stage-", dir=str(base)))   # 每次新建、名字隨機:不沿用任何既有路徑 / 連結
    except OSError:
        return None
    try:
        with tarfile.open(fileobj=io.BytesIO(rc.stdout)) as tf:
            for mem in tf.getmembers():
                parts = Path(mem.name).parts
                if not parts or parts[0] != name or ".." in parts or Path(mem.name).is_absolute() \
                        or not (mem.isfile() or mem.isdir()):
                    shutil.rmtree(stage, ignore_errors=True)
                    log("plugin %s skipped: archive has a path outside the folder or a link" % name, env)
                    return None
            tf.extractall(str(stage), **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))   # 上面已逐項檢查:只有一般檔與資料夾、都在 <name>/ 底下
    except (OSError, tarfile.TarError) as exc:     # 磁碟滿 / 包壞掉 ⇒ 不留半個資料夾,這次不裝
        shutil.rmtree(stage, ignore_errors=True)
        log("plugin %s skipped: extract failed (%s)" % (name, type(exc).__name__), env)
        return None
    return stage / name


# ───────────────────────────── 查 / 更新 ─────────────────────────────

def _child_env(env):
    e = dict(env)
    for k in _CHILD_ENV_DROP:
        e.pop(k, None)
    return e


def run_core_update(env, root=None):
    """跑這份 aib 的 aib update(一行安裝程式:只快轉、不蓋本機改動);輸出接到 auto-update.log。回結束碼。"""
    root = root or ROOT
    fh = _open_log(env)
    try:
        with (fh or open(os.devnull, "w")) as fh:
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
          ext_base=None, room=_room_reload, dev_tree=None, lease_state=lease_busy, plugin_fetch=fetch_plugin_source,
          stage=stage_paid):
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
        try:
            raw = json.loads(status_path(env).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        st, skew = clean_status(raw, now)
        st.pop("clock_skew", None)           # 只記這一次有沒有看到;下一次正常就清掉
        if skew:
            st["clock_skew"] = res["clock_skew"] = True
            log("clock_skew: status timestamps in the future were ignored", env, now)

        def done(result, **kw):
            res.update(kw, result=result)
            if result not in ("checked_recently",) or skew:
                st.update(last_result=result, last_result_at=int(now), last_why=why)
                for k in ("have", "latest", "plugins_behind", "plugins_excluded", "plugins_unsigned", "plugin_source"):
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
        excluded = read_excludes(env)
        behind = [b for b in plugins_behind(ext_base) if b["name"] not in excluded] \
            if env.get("AIB_EXT_AUTO", "") != "0" else []
        cache = plugin_fetch(env)
        res["plugin_source"] = "ok" if cache else "offline"
        paid, skipped, unsigned = paid_behind(cache, excluded, env) if cache else ([], [], [])
        behind += paid
        res.update(plugins_behind=[b["name"] for b in behind], plugins_excluded=skipped, plugins_unsigned=unsigned)
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
            buf, plugin_rows = io.StringIO(), []
            #   付費外掛也一樣等下一次查(新版外掛可能要新版核心;下一次是新碼在跑)
            for b in ([] if plan["behind"] else behind):
                folder = b.get("dir") or stage(cache, b["name"], env)
                try:
                    r = install(str(folder), buf) if folder else 3
                finally:
                    if folder and not b.get("dir"):
                        shutil.rmtree(Path(folder).parent, ignore_errors=True)   # stage_paid 建的 stage-* 整個拿掉
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


def status_payload(env=None, home=None, platform=None, now=None):
    on, src = read_setting(env)
    st = read_status(env, now)
    plat = platform or sys.platform
    return {"on": on, "source": src, "schedule": ("installed" if agent_installed(home) else "missing") if plat == "darwin"
            else "not_mac", "interval_s": INTERVAL_S, "have": st.get("have"), "latest": st.get("latest"),
            "last_check": st.get("last_check"), "last_result": st.get("last_result"),
            "last_result_at": st.get("last_result_at"), "last_update": st.get("last_update"),
            "plugins_behind": st.get("plugins_behind") or [], "last_plugins": st.get("last_plugins") or [],
            "excluded": read_excludes(env), "plugins_excluded": st.get("plugins_excluded") or [],
            "plugins_unsigned": st.get("plugins_unsigned") or [], "plugin_source": st.get("plugin_source"),
            "clock_skew": bool(st.get("clock_skew")), "log": str(log_path(env))}


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
    out.append(T("status.excluded", names=", ".join(p["excluded"]) or T("exclude.none")))
    if p.get("plugins_excluded"):
        out.append(T("status.excluded_behind", names=", ".join(p["plugins_excluded"])))
    if p.get("plugins_unsigned"):
        out.append(T("status.unsigned", names=", ".join(p["plugins_unsigned"])))
    if p.get("plugin_source") == "offline":
        out.append(T("status.plugin_source_offline"))
    if p.get("clock_skew"):
        out.append(T("status.clock_skew"))
    out.append(T("status.log", path=p["log"]))
    return out


def doctor_line(env=None, now=None):
    """(ok|note, 一行)。aib doctor 用。唯讀。"""
    p = status_payload(env, now=now)
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


def cmd_exclude(action, name=None, env=None, out=sys.stdout, as_json=False, prove=None, platform=None):
    """exclude add|remove|list。拿掉預設清單裡的(hostguard)要本人 Touch ID;加、列不用。"""
    env = os.environ if env is None else env
    cur = read_excludes(env)
    if action == "list":
        out.write((json.dumps({"exclude": cur, "default": list(DEFAULT_EXCLUDE)}, ensure_ascii=False) if as_json
                   else T("exclude.list", names=", ".join(cur) or T("exclude.none"))) + "\n")
        return 0
    if not name or not _ext().name_ok(name):
        out.write(T("exclude.bad_name", name=name or "") + "\n")
        return 2
    if action == "add":
        if name in cur:
            out.write(T("exclude.already", name=name) + "\n")
            return 0
        write_excludes(cur + [name], env)
        out.write(T("exclude.added", name=name) + "\n")
        log("exclude add %s" % name, env)
        return 0
    if name not in cur:
        out.write(T("exclude.not_in", name=name) + "\n")
        return 0
    if name in DEFAULT_EXCLUDE:
        if prove is None:
            import seat_act
            prove = seat_act.prove
        _via, why = prove(T("exclude.touchid_reason", name=name), env, platform=platform)
        if why:
            out.write(T("exclude.needs_touchid", name=name, why=why) + "\n")
            return 2
    write_excludes([n for n in cur if n != name], env)
    out.write(T("exclude.removed", name=name) + "\n")
    log("exclude remove %s" % name, env)
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
    ap.add_argument("action", nargs="?", default="status", choices=["on", "off", "status", "check", "exclude"])
    ap.add_argument("sub", nargs="?", choices=["add", "remove", "list"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--why", default="schedule", choices=["schedule", "seat-open", "manual"])
    ap.add_argument("--detach", action="store_true")
    a = ap.parse_args(argv)
    if a.action == "on":
        return cmd_on(out=out)
    if a.action == "off":
        return cmd_off(out=out)
    if a.action == "exclude":
        return cmd_exclude(a.sub or "list", a.name, out=out, as_json=a.json)
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
