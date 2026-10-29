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
  核心:git fetch --tags origin,目標 = 最新的 v* 標籤(沒有標籤 = origin/main);標籤一定要在 origin/main 的歷史上,
       不是就 refused_source。不是從公開版裝的(私有倉、ssh 自己的倉、fork)⇒ refused_source,自動路徑不動。
  外掛:(1) 這份 aib 附的 extensions/ 裡受信鑰匙簽過的包 ⇒ 會換;(2) 付費外掛(<狀態根>/plugins)⇒ 只跟 aib-plugins 倉的 main
       比版號、**只列名不換**(10-25 @abb89 審:本機外掛只由維護席裝,簽過 ≠ 准上正式機),status / doctor 提示本人自己
       aib ext install。兩種都只看這台已經裝了、版本比較舊、而且簽章驗得過(aib_ext.signed_ok)的那幾個。
       aib-plugins 用 git fetch 到 <狀態根>/cache/auto-update/aib-plugins.git;來源網址 AIB_AUTO_UPDATE_PLUGIN_SOURCE 可換(測試 / 鏡像用)。
  不自動更新清單(設定檔 exclude):預設 hostguard(這台的 hostguard 是正式站部署來源,換版要主人點頭)。
       `aib update auto exclude add|remove|list <外掛>`;拿掉預設那幾個要本人 Touch ID(只擋正式入口;設定檔本身 AI 改得到,
       真正的保護仍是簽章)。清單裡的外掛,自動更新這支不會換(status 會列出來);但附帶的外掛(extensions/)在核心
       更新時,一行安裝程式的 aib ext auto 照舊會換成新簽的那份 —— 那條不看這份清單。hostguard 不在 extensions/,不受影響。
什麼時候才真的更新(有一項不合就這次不做,下次查再試):
  沒人拿著測試租約(aib lease;讀不到也當有人)、沒有推包 / git push / aib test 在跑、
  安裝目錄沒有改過沒提交的檔、是單純快轉(歷史分叉就不動)、在 main(或沒有分支)上、
  不是落後太多(大 / 中版號不同或差 CLEAN_GAP 個小版以上;門檻固定、不讀環境 —— 那種要本人自己跑 aib update)、
  距離上次自動更新超過 1 小時。
  只有租約 / aib test 佔著時,「查」那半(fetch、比版號、看外掛目錄、寫狀態)照做,換檔等下次;排程檔每 30 分醒一次
  (--why tick),上次查完了就滿 6 小時才再查,上次被擋就 30 分後再試(d 張 10-29)。
  開始前自己拿測試租約(--exclusive),做完放掉:更新中途不會有測試插進來。
怎麼更新:
  核心 = 跑已 fetch 的那個標籤裡的 install.sh(git show <標籤>:install.sh;不從網站下載),--dir 這份 checkout:
    只快轉、不蓋本機改動、會裝附帶的 mod。子行程只帶白名單環境,另明帶 AIB_CLEAN_GAP=NO_CLEAN_GAP(永遠不整份搬目錄重裝);
    自己一個行程群組,逾時整群組砍掉再放租約。換成功後照 aib update 的收尾:新版 hook ensure-all、轉接程式 restart。
  附帶外掛 = aib_ext.install,跟 aib ext install 同一條路:簽章一定要驗過,不帶 --allow-unsigned;驗不過就不裝。
  最後叫一次 room_reload(艦長室還在跑舊碼才送 /api/reload;一樣就不送)。不重開、不 kill 任何 AI 席。
告訴本人:aib doctor 一行、艦長室事件(room-events.jsonl kind=aib_updated,只帶版號)、aib update auto status。
  紀錄寫 <狀態根>/logs/auto-update.log(只有時間、原因、版號;一行安裝的輸出也接在這裡)。
同帳號 AI(pending/GUIDE-same-user-ai-threat-model):
  AI 席叫 `aib update auto off` 沒關係 —— 只是不再自動更新。`on` 也不用本人證明:它只會跑官方來源的快轉與簽過的附帶外掛,
  不多給任何權力(AI 本來就能自己跑 aib update)。設定檔、排程檔、時間戳都是本機檔,AI 改得到 ⇒ 它們只防誤操作,
  不是防線;真正的保證是「只快轉」(git 自己擋)與「外掛簽章」(受信鑰匙驗)。
"""
import argparse
import contextlib
import io
import stat
import fcntl
import json
import os
import random
import re
import signal
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
INTERVAL_S = 6 * 3600                # 完整查一次的間隔:6 小時
RETRY_S = 30 * 60                    # 排程醒來的間隔:上次沒查完 / 被擋 ⇒ 30 分後再試(d 張 10-29;以前碰上租約就等 6 小時)
SETTLED = ("up_to_date", "updated", "no_change")   # 上次是這幾種 ⇒ 排程滿 INTERVAL_S 才再查
#   只等租約的那幾種:fetch / 比版號 / 看目錄照做(不換檔,不必拿租約);會碰同一個 checkout 的(本人 aib update、推包、git push)
#   照舊整輪不查
CHECK_WHILE_BUSY = ("lease", "tests")
CHECK_GAP_S = 10 * 60                # 開席那次:10 分鐘內查過就不再查
ATTEMPT_GAP_S = 3600                 # 真的更新:一小時最多一次
UPDATE_TIMEOUT_S = 30 * 60
LEASE_TTL_S = 45 * 60
CLEAN_GAP = 5                        # 跟 install.sh clean_if_far 的 AIB_CLEAN_GAP 預設一樣;父行程固定用這個(不讀環境)
NO_CLEAN_GAP = 1000000               # 子行程明帶 AIB_CLEAN_GAP=這個:自動路徑永遠不整份搬目錄重裝(太遠在父行程就 refused_far_behind)
POST_TIMEOUT_S = 120                 # 換完之後的收尾(hook ensure-all / 轉接程式重啟)每支最多等這麼久
PUBLIC_MARK = "github.com/ez2app/ez2-aib.git"   # 跟 aib/cli.py PUBLIC_MARK 一樣:公開版安裝程式才有這一串
_PATH_EXTRA = ("/opt/homebrew/bin", "/usr/local/bin")
_AGENT_ENV_KEEP = ("HOME", "LANG", "EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME", "EZ2_AIB_LOCALE")   # 只帶路徑 / 語系,不帶 token / 開關
# 跑 aib update 的子行程只帶白名單:開席那條會整份繼承真人終端機的環境,而 install.sh 認 AIB_PACKAGE_* / AIB_ALLOWED_SIGNERS /
#   AIB_SOURCE / AIB_INSTALL_URL…(會換安裝程式、換發版公鑰、換來源),bash 也吃 BASH_ENV ⇒ 一律不帶，只留路徑 / 語系 / 代理 / ssh-agent
_CHILD_ENV_KEEP = _AGENT_ENV_KEEP + ("PATH", "TMPDIR", "TERM", "USER", "LOGNAME", "SSH_AUTH_SOCK",
                                     "http_proxy", "https_proxy", "no_proxy", "all_proxy",
                                     "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "AIB_EXT_AUTO")
_CHILD_ENV_KEEP_PREFIX = ("LC_",)
DEFAULT_EXCLUDE = ("hostguard",)     # 10-23 @abb89:這台的 hostguard = 正式站部署來源,換版要主人點頭
PLUGIN_SOURCE = "git@github.com:ez2app/aib-plugins.git"   # 付費外掛的簽章來源(main);AIB_AUTO_UPDATE_PLUGIN_SOURCE 可換
PLUGIN_BRANCH = "main"   # 自動更新只用內建的官方安裝網址
SSH_HARDENED = "ssh -o BatchMode=yes -o ForwardAgent=no -o ClearAllForwardings=yes"
BUSY_PATTERNS = (
    ("tests", re.compile(r"(?:^|[\s/])aib\s+test(?:\s|$)|tools/verify/test_entry\.py")),
    ("updating", re.compile(r"(?:^|[\s/])aib\s+update(?!\s+auto)(?:\s|$)|aib-install")),   # 本人手動跑的 aib update(自動更新自己是 aib update auto)
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
_STR_KEYS = ("have", "latest", "last_why", "plugin_source", "plugin_via", "plugin_problem")
_LIST_KEYS = ("plugins_behind", "plugins_manual_behind", "plugins_excluded", "plugins_unsigned", "plugins_modified", "plugins_missing")
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
    if isinstance(d.get("plugin_via_url"), str) and len(d["plugin_via_url"]) <= 200:
        out["plugin_via_url"] = safe_url(d["plugin_via_url"])      # 狀態檔誰都改得到 ⇒ 讀回再清一次
    for k in _LIST_KEYS:
        v = d.get(k)
        if isinstance(v, list):
            out[k] = [x for x in v if isinstance(x, str)][:50]
    if "plugins_manual_behind" not in out and isinstance(d.get("plugins_paid_behind"), list):   # 0.1.59 前的鍵名,升級後第一次讀照認
        out["plugins_manual_behind"] = [x for x in d["plugins_paid_behind"] if isinstance(x, str)][:50]
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
    """LaunchAgent 內容(dict,給 plistlib)。每 RETRY_S 秒跑一次 check --why tick(要不要真的查由 check 看 INTERVAL_S);
    環境只帶路徑 / 語系。"""
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
            "ProgramArguments": [python or sys.executable, script or str(HERE / "auto_update.py"), "check", "--why", "tick"],
            "StartInterval": RETRY_S, "RunAtLoad": False, "ProcessType": "Background", "LowPriorityIO": True,
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
                           stdin=subprocess.DEVNULL, env=_child_env(os.environ) if env is None else env)   # 席位帶來的 GIT_DIR 等不帶
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


def agent_stale(home=None):
    """已裝的排程檔是舊間隔(0.1.63 前每 6 小時醒一次)⇒ True,開席時重裝一次換成 RETRY_S。讀不懂也算舊。"""
    import plistlib
    try:
        with open(agent_plist_path(home), "rb") as fh:
            d = plistlib.load(fh)
        return d.get("StartInterval") != RETRY_S or (d.get("ProgramArguments") or [])[-1:] != ["tick"]
    except Exception:  # noqa: BLE001
        return True


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


def remove_agent(home=None, launchctl=_launchctl, platform=None):
    path = agent_plist_path(home)
    if (platform or sys.platform) == "darwin":       # Linux 沒有 launchctl
        launchctl("bootout", "gui/%d/%s" % (os.getuid(), AGENT_LABEL))
    if path.exists():
        path.unlink()
        return True
    return False


# ───────────────────────────── 版本 ─────────────────────────────

PUBLIC_SOURCE = "https://github.com/ez2app/ez2-aib.git"   # 跟 install.sh PUBLIC_SOURCE 一樣


def norm_src(url):
    """照 install.sh _norm_src:git@ / ssh://(含 :22)/ https / http / git:// 一律化成 https://<主機>/<擁有者>/<倉庫>(小寫);
    本機路徑照舊。git 全域 insteadOf 把 https 換成 git@ 時，同一個倉庫仍算同一個來源。"""
    x = (url or "").strip()
    x = x[:-1] if x.endswith("/") else x
    x = x[:-4] if x.endswith(".git") else x
    if re.match(r"^git@[^:]+:", x):
        host, rest = x[4:].split(":", 1)
        x = host + "/" + rest
    elif re.match(r"^(ssh|https?|git)://", x):
        x = x.split("://", 1)[1]
        x = x.split("@", 1)[1] if "@" in x.split("/", 1)[0] else x
        x = x.replace(":22/", "/", 1)
    else:
        return x
    return ("https://" + x).lower()


def public_install(root):
    """這份是不是從公開版裝的。子行程不帶 AIB_SOURCE ⇒ install.sh 一律以公開來源判 same_source;不是公開版 ⇒ 它會回 2。"""
    _rc, url = _git(root, "remote", "get-url", "origin")
    return norm_src(url) == norm_src(PUBLIC_SOURCE)


def release_ref(root):
    """一行安裝程式會裝的那一版(install.sh release_ref):子行程只帶白名單 ⇒ 來源一律是公開版 ⇒ 最新 v* 標籤;
    沒有標籤 ⇒ origin/main。(origin 看起來像 ssh 不算:insteadOf 會把 https 寫成 git@,install.sh 照樣裝標籤)"""
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
    e = _child_env(os.environ if env is None else env)
    e.update(GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND=SSH_HARDENED)
    rc, _ = _git(root, "fetch", "-q", "--tags", "origin", env=e, timeout=120)
    return rc == 0


HOLD_FILE = "AUTO_UPDATE_HOLD"     # 官方公開倉 main 上的固定檔:有 ⇒ 全部會員的自動更新先停(維護者出事時一個 commit 喊停)


def hold_on(root):
    """fetch 成功之後才看：遠端追蹤分支 origin/main 上有沒有暫停記號。只會讓更新停，不讀本機檔 / 環境變數(沒有「強制更新」這回事)。"""
    return _git(root, "cat-file", "-e", "refs/remotes/origin/main:" + HOLD_FILE)[0] == 0


def core_plan(root):
    """fetch 之後:{"want", "have", "latest", "behind", "why"}。why ∈ None(可以快轉)/ dirty / diverged / branch / no_ref。"""
    want = release_ref(root)
    _rc, before = _git(root, "rev-parse", "HEAD")
    rc, after = _git(root, "rev-parse", "%s^{commit}" % want)
    plan = {"want": want, "have": ref_version(root, "HEAD"), "latest": ref_version(root, want) if rc == 0 else None,
            "behind": False, "why": None, "head": before}
    if rc != 0 or not after:
        plan["why"] = "no_ref"
        return plan
    if before == after or _git(root, "merge-base", "--is-ancestor", after, before)[0] == 0:
        return plan                                           # 已在這版、或比正式版還新 ⇒ 不往回退
    plan["behind"] = True
    if not public_install(root):
        plan["why"] = "source"                                # 私有 / 自己的倉:install.sh same_source 一定擋，別每小時去撞
    elif want != "origin/main" and _git(root, "merge-base", "--is-ancestor", after, "refs/remotes/origin/main")[0] != 0:
        plan["why"] = "source"                                # 標籤不在官方 main 的歷史上(被另外推的標籤)⇒ 不裝
    elif _git(root, "status", "--porcelain", "--untracked-files=no")[1]:
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


def keep_dirs(m):
    return _ext().keep_dirs(m)


def local_changes(b):
    """已裝那份跟它自己 manifest 的 files 比(aib_ext.local_changes);keep 夾以來源新版 manifest 為準。量不到 ⇒ ["?"]。"""
    X = _ext()
    try:
        d = X.target(b["kind"], b["name"])
    except (KeyError, ValueError):
        return ["?"]
    src = _read_obj(Path(b["dir"]) / X.MANIFEST) if b.get("dir") else b.get("manifest")
    return X.local_changes(d, src)


def install_plugin(folder, out):
    """跟 aib ext install 同一條路;一律驗簽章(不給 --allow-unsigned)。回結束碼。
    自動路徑另外守兩件事(aib_ext.install_keeping):keep 夾搬到新版裡;啟用狀態照原樣(停用的不會被自動打開)。"""
    X = _ext()
    import ext_kinds as K
    m = _read_obj(Path(folder) / X.MANIFEST)
    if m is None or not X.signed_ok(m):
        return 3
    try:
        dst = X.target(K.kind_of_label(str(m.get("kind") or "")), str(m.get("name") or ""))
    except ValueError:
        return 3
    return X.install_keeping(folder, m, dst, out=out)


def plugin_cache(env=None):
    return state_root(env) / "cache" / "auto-update" / "aib-plugins.git"


def fetch_plugin_source(env=None):
    """aib-plugins 的 main 抓到本機快取(bare);回快取路徑,抓不到 ⇒ None。不帶任何認證參數、不開互動提示。"""
    env = dict(os.environ if env is None else env)
    src = env.get("AIB_AUTO_UPDATE_PLUGIN_SOURCE") or PLUGIN_SOURCE
    cache = plugin_cache(env)
    e = _child_env(env)
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
            behind.append({"name": d.name, "kind": "plugin", "have": have, "want": m.get("version"), "source": "aib-plugins",
                           "manifest": m})
    return behind, skipped, unsigned


# 付費外掛落後:先看 ez2.page 目錄(10-25 @abb89 定案;#273 A-M3 不變 = 只列名、不自動裝)
#   只有 plugin_unsupported / plugin_unreachable(伺服器沒這組端點 / 連不到)才退回 aib-plugins git;
#   目錄形狀不對 / 其他碼 ⇒ 停在這裡(不列、不退回、不裝);包的 sha256 / 簽章由本人跑 aib plugins install 時驗
CATALOG_FALLBACK = ("plugin_unsupported", "plugin_unreachable")
CATALOG_REJECTED = ("plugin_catalog_invalid",)   # 目錄驗證沒過 ⇒ 狀態寫 rejected;其他非退回碼 ⇒ incomplete(這次沒查完)


def _catalog_mod():
    p = str(ROOT / "aib" / "tools" / "verify") if (ROOT / "aib" / "tools" / "verify").is_dir() else str(CORE / "tools" / "verify")
    if p not in sys.path:
        sys.path.insert(0, p)
    import plugin_catalog
    return plugin_catalog


def safe_url(u):
    """寫進狀態 / 紀錄的來源網址:去掉帳密、查詢參數、;參數、片段,只留可印 ASCII、最多 200 字(紀錄不放 token / 網址參數)。
    scp 寫法(user@host:path)也去掉 @ 前面。"""
    from urllib.parse import urlsplit
    u = "".join(c for c in str(u or "") if " " < c < "\x7f")
    if "://" in u:
        try:
            p = urlsplit(u)
            u = "%s://%s%s" % (p.scheme, p.netloc.rpartition("@")[2], p.path.split(";", 1)[0])
        except ValueError:
            u = "?"
    else:                                             # 只有 user@host:path 這種 scp 寫法才去 @ 前;本機路徑裡的 @ 留著(#349 r2 L4)
        if re.match(r"^[^/\s@]+@[^/\s:]+:", u):      # user@host:path / user:pass@host:path;開頭是 / 的本機路徑不算
            u = u.partition("@")[2]
        u = re.split(r"[?#;]", u, maxsplit=1)[0]
    return u[:200] or "?"


def catalog_url(env=None):
    """實際用的目錄網址(EZ2_PAGE_API_BASE 可換 —— 防線是包簽章,不是網址;狀態 / doctor 照實寫出來)。"""
    env = os.environ if env is None else env
    return safe_url(_catalog_mod()._public_api(env).base_url)


CATALOG_DEADLINE_S = 60      # 自動路徑整輪付費檢查上限(@abb89 #349 r1 裁 2);超過 = plugin_unreachable(照兩碼規則退回 git)


def catalog_behind(env, excluded=(), deadline_s=None, api=None):
    """這台已裝的付費外掛(<狀態根>/plugins)在 ez2.page 目錄有比較新的版本 ⇒ 列名(#273 A-M3 只列名、不自動裝)。
    @abb89 #349 r1 裁 1:自動路徑只比目錄版本號,不下載、不驗包 —— 驗證留給本人跑 aib plugins install(那邊驗不過就停)。
    目錄只抓一次、整輪最多 deadline_s 秒。回 {"via": "catalog" | "fallback", "behind", "skipped", "missing", "unsigned", "problem"}:
    missing = 已裝、目錄沒列的付費外掛;沒裝付費外掛 ⇒ 不連網、via catalog。"""
    X = _ext()
    import ext_kinds as K
    PC = _catalog_mod()
    out = {"via": "catalog", "behind": [], "skipped": [], "missing": [], "unsigned": [], "problem": None}
    home = state_root(env) / K.KINDS["plugin"]["home"]
    try:
        dirs = sorted(p for p in home.iterdir() if p.is_dir() and not p.name.endswith(".installing"))
    except OSError:
        return out
    mine = []
    for d in dirs:
        v = (_read_obj(d / X.MANIFEST) or {}).get("version")
        if isinstance(v, str) and PC.VER_RE.fullmatch(v):
            mine.append((d.name, v))
        else:                                          # 1.0.0-rc1 這類 ⇒ 跳過,不讓整輪(連核心更新)掛掉(#349 r1 H1)
            log("plugin %s skipped: installed version is not x.y.z" % d.name, env)
    if not mine:
        return out
    limit = CATALOG_DEADLINE_S if deadline_s is None else deadline_s
    api = api or PC._public_api(env)
    # 整輪硬上限 = limit:api 的總期限 + 它自己的 2 秒寬限(JOIN_GRACE_S)加起來不超過 limit;socket 逾時那個欄位 api 內部
    #   固定取 max(timeout, 60),改它沒用(#349 r2 L1)
    api.total_s = max(1, limit - getattr(api, "JOIN_GRACE_S", 2))
    t0 = time.monotonic()
    entries, err = PC.catalog(env, api)
    if not err and time.monotonic() - t0 > limit:
        err = PC.fail("plugin_unreachable", 4)
    if err:
        code = err[0].get("detail")
        return dict(out, via="fallback" if code in CATALOG_FALLBACK else "catalog", problem=code)
    for name, have in mine:
        e = PC.pick(entries, name)
        if e is None:
            out["missing"].append(name)
        elif PC.vkey(e["version"]) <= PC.vkey(have):
            continue                                   # 不比這台新 ⇒ 不動(不降版)
        elif name in excluded:
            out["skipped"].append(name)
        else:
            out["behind"].append({"name": name, "kind": "plugin", "have": have, "want": e["version"], "source": "catalog"})
    return out


def _catalog_safe(catalog, env, excluded):
    """check() 用:目錄這步拋什麼都收成 plugin_catalog_error —— 這輪付費沒查、不退回 git、不擋核心更新(#349 r1 H1)。"""
    try:
        cat = catalog(env, excluded)
        url = catalog_url(env) if cat.get("via") == "catalog" else None
        return cat, url
    except Exception as exc:  # noqa: BLE001
        log("plugin catalog error: %s" % type(exc).__name__, env)
        return {"via": "catalog", "behind": [], "skipped": [], "missing": [], "unsigned": [],
                "problem": "plugin_catalog_error"}, None   # 沒網址就不印「查的是目錄:?」(#349 r2 L2)


# ───────────────────────────── 查 / 更新 ─────────────────────────────

def _child_env(env):
    # git / install.sh 的 ssh 一律加固:install.sh 只在 SOURCE 是 git@ / ssh:// 才自己 export,insteadOf 改寫成 ssh 時會漏
    e = {k: str(v) for k, v in env.items() if k in _CHILD_ENV_KEEP or k.startswith(_CHILD_ENV_KEEP_PREFIX)}
    e.update(GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND=SSH_HARDENED)
    e["AIB_EXT_AUTO_GUARD"] = "1"     # 新版的 aib ext auto 不換使用者改過的附帶外掛、keep 夾與啟用狀態照原樣
    return e


def installer_at(root, ref):
    """已 fetch 的標籤(或 origin/main)裡的 install.sh 內容;不是公開版安裝程式 ⇒ None。自動路徑不從網站下載安裝程式。"""
    rc, text = _git(root, "show", "%s:install.sh" % ref)
    return text if rc == 0 and PUBLIC_MARK in text else None


def _run_group(argv, fh, env, cwd, timeout, popen=subprocess.Popen, killpg=os.killpg):
    """自己一個行程群組跑;逾時整群組先 TERM、10 秒後 KILL(不留孫行程佔著租約)。回結束碼,逾時 124、起不來 1。"""
    try:
        p = popen(argv, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT, env=env, cwd=str(cwd),
                  start_new_session=True)
    except OSError:
        return 1
    try:
        return p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        for sig, wait_s in ((signal.SIGTERM, 10), (signal.SIGKILL, 10)):
            try:
                killpg(p.pid, sig)
            except OSError:
                pass
            try:
                p.wait(timeout=wait_s)
                break
            except subprocess.TimeoutExpired:
                continue
        return 124


def run_core_update(env, root=None, ref=None, run=_run_group):
    """用已 fetch 的 ref 裡的 install.sh 更新這份 checkout(只快轉、不蓋本機改動);輸出接到 auto-update.log。回結束碼。
    子行程只帶白名單 + AIB_CLEAN_GAP=NO_CLEAN_GAP(永遠不整份重裝)。換成功後照 aib update 的收尾:用**新版**的
    auto_distill_hook ensure-all 補 hook、chat_bridge restart 換掉舊碼轉接程式(失敗不影響結束碼;艦長室在 check 裡重載)。"""
    root = Path(root or ROOT)
    script = installer_at(root, ref or release_ref(root))
    if not script:
        log("installer: %s has no open-source install.sh" % (ref or "?"), env)
        return 5
    e = dict(_child_env(env), AIB_CLEAN_GAP=str(NO_CLEAN_GAP))
    fh = _open_log(env)
    with (fh or open(os.devnull, "w")) as fh:
        rc = run(["/bin/bash", "-c", script, "aib-install", "--dir", str(root)], fh, e, root, UPDATE_TIMEOUT_S)
        if rc == 0:
            for tool, arg in (("src/identity/auto_distill_hook.py", "ensure-all"), ("src/cockpit/chat_bridge.py", "restart")):
                path = root / "aib" / tool
                if path.is_file():
                    run([sys.executable, str(path), arg], fh, e, root, POST_TIMEOUT_S)
    return rc


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
          catalog=catalog_behind):
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
    if why == "seat-open" and plat == "darwin" and not dev and (not agent_installed(home) or agent_stale(home)):
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
                for k in ("have", "latest", "plugins_behind", "plugins_manual_behind", "plugins_excluded", "plugins_unsigned",
                          "plugins_modified", "plugins_missing", "plugin_source", "plugin_via", "plugin_via_url", "plugin_problem"):
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
        if why == "tick":                   # 排程檔每 RETRY_S 醒一次:上次查完了 ⇒ 滿 INTERVAL_S 才再查(不寫紀錄)
            if now - float(st.get("last_check") or 0) < INTERVAL_S - 60 and st.get("last_result") in SETTLED:
                return done("checked_recently")
            why = res["why"] = "schedule"
        busy = lease_state(env, now) or busy_procs(proc_rows() if rows is None else rows)
        if busy and busy not in CHECK_WHILE_BUSY:
            return done("refused_" + busy)
        if now - float(st.get("last_attempt") or 0) < ATTEMPT_GAP_S:
            return done("too_soon")
        st["last_check"] = int(now)
        if not do_fetch(root, env):
            return done("offline")
        if hold_on(root):
            return done("paused")        # 官方倉 main 放了暫停記號 ⇒ 核心、外掛全部不換
        plan = core_plan(root)
        res.update(have=plan["have"], latest=plan["latest"])
        excluded = read_excludes(env)
        behind = [b for b in plugins_behind(ext_base) if b["name"] not in excluded] \
            if env.get("AIB_EXT_AUTO", "") != "0" else []
        cat, cat_url = _catalog_safe(catalog, env, excluded)
        res["plugin_problem"] = cat["problem"] or ""
        res["plugins_missing"] = list(cat.get("missing") or [])
        if cat["via"] == "catalog":
            res.update(plugin_via="catalog", plugin_via_url=cat_url,
                       plugin_source="rejected" if cat["problem"] in CATALOG_REJECTED
                       else "incomplete" if cat["problem"] else "ok")
            paid, skipped, unsigned = cat["behind"], cat["skipped"], cat["unsigned"]
        else:                                          # 只有伺服器沒這組端點 / 連不到才走到這裡
            cache = plugin_fetch(env)
            res.update(plugin_via="git", plugin_via_url=safe_url(env.get("AIB_AUTO_UPDATE_PLUGIN_SOURCE") or PLUGIN_SOURCE),
                       plugin_source="ok" if cache else "offline")
            paid, skipped, unsigned = paid_behind(cache, excluded, env) if cache else ([], [], [])
        log("plugin source via=%s url=%s problem=%s" % (res["plugin_via"], res["plugin_via_url"], cat["problem"] or "-"),
            env, now)
        # 付費 / 會員外掛只列名、不自動換(本機外掛只由維護席裝;簽過 ≠ 准上正式機)⇒ 狀態 / doctor 提示本人
        #   (鍵名 0.1.60 起 plugins_manual_behind:目錄上 access=member 的也會列進來,不只付費;#349 r1 L 收尾)
        res["plugins_manual_behind"] = [b["name"] for b in paid]
        if paid:                            # 看紀錄就知道哪幾個在等本人裝(d 張 10-29;以前只在 status / doctor 看得到)
            log("plugins_manual_behind=%s" % ",".join(b["name"] for b in paid), env, now)
        # 外掛資料夾裡有使用者 / 執行期自己的檔(改過、多出來;外掛宣告的 keep 夾除外)⇒ 自動路徑不換，交本人 aib ext install
        modified = [b["name"] for b in behind if local_changes(b)]
        behind = [b for b in behind if b["name"] not in modified]
        res.update(plugins_behind=[b["name"] for b in behind], plugins_excluded=skipped, plugins_unsigned=unsigned,
                   plugins_modified=modified)
        if plan["why"] == "no_ref":
            return done("no_ref")
        if not plan["behind"] and not behind:
            return done("up_to_date")       # 只有付費外掛落後也算 up_to_date(列在 plugins_manual_behind,不自動換)
        if plan["behind"]:
            if plan["why"]:
                return done("refused_" + plan["why"])
            if far_behind(plan["have"], plan["latest"], CLEAN_GAP):
                return done("refused_far_behind")
        if busy:                            # 租約 / 測試佔著:查過了、狀態寫好,換檔等下次(RETRY_S 後)
            return done("refused_" + busy)
        rid = "auto-update-%d-%d" % (os.getpid(), int(now))
        if lease("acquire", rid, env) != 0:
            return done("refused_lease")
        st["last_attempt"] = int(now)
        write_status(st, env)
        try:
            old = plan["have"]
            if plan["behind"]:
                rc = run_update(_child_env(env), root, plan["want"])   # 跑已 fetch 的 ref 裡的 install.sh;子行程只帶白名單
                res["update_rc"] = rc
                if rc != 0:
                    return done("failed", have=ref_version(root, "HEAD"))
                if _git(root, "rev-parse", "HEAD")[1] == plan.get("head"):
                    return done("no_change", have=ref_version(root, "HEAD"))   # 跑完 rc 0 但沒動 ⇒ 不記成 updated(1 小時內不再跑)
            # 核心換了 ⇒ 一行安裝已用**新版**程式跑過 aib ext auto,這支舊碼不再自己裝;核心沒換才在這裡裝落後的外掛
            buf, plugin_rows = io.StringIO(), []
            #   付費外掛也一樣等下一次查(新版外掛可能要新版核心;下一次是新碼在跑)
            for b in ([] if plan["behind"] else behind):
                r = install(str(b["dir"]), buf)
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
            "plugins_behind": st.get("plugins_behind") or [], "plugins_manual_behind": st.get("plugins_manual_behind") or [],
            "last_plugins": st.get("last_plugins") or [],
            "excluded": read_excludes(env), "plugins_excluded": st.get("plugins_excluded") or [],
            "plugins_unsigned": st.get("plugins_unsigned") or [], "plugins_modified": st.get("plugins_modified") or [],
            "plugins_missing": st.get("plugins_missing") or [],
            "plugin_source": st.get("plugin_source"), "plugin_via": st.get("plugin_via"),
            "plugin_via_url": st.get("plugin_via_url"), "plugin_problem": st.get("plugin_problem") or None,
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
    if p.get("plugins_manual_behind"):
        out.append(T("status.manual_behind", names=", ".join(p["plugins_manual_behind"])))
    if p.get("plugins_unsigned"):
        out.append(T("status.unsigned", names=", ".join(p["plugins_unsigned"])))
    if p.get("plugins_modified"):
        out.append(T("status.modified", names=", ".join(p["plugins_modified"])))
    if p.get("plugin_via") and p.get("plugin_via_url"):   # 目錄那步拋例外 ⇒ 沒網址 ⇒ 不印這行(#349 r2 L2)
        out.append(T("status.plugin_via_" + ("catalog" if p["plugin_via"] == "catalog" else "git"), url=p["plugin_via_url"]))
    if p.get("plugin_via") == "git" and p.get("plugin_problem") == "plugin_unsupported":
        out.append(T("status.plugin_unsupported"))   # 正式站還沒開外掛目錄 ⇒ 講清楚不是這台設定錯(d 張 10-29)
    if p.get("plugin_source") == "offline":
        out.append(T("status.plugin_source_offline"))
    if p.get("plugin_source") == "rejected":
        out.append(T("status.plugin_rejected", code=p.get("plugin_problem") or "?"))
    if p.get("plugin_source") == "incomplete":
        out.append(T("status.plugin_incomplete", code=p.get("plugin_problem") or "?"))
    if p.get("plugins_missing"):
        out.append(T("status.plugins_missing", names=", ".join(p["plugins_missing"])))
    if p.get("clock_skew"):
        out.append(T("status.clock_skew"))
    out.append(T("status.log", path=p["log"]))
    return out


def doctor_line(env=None, now=None):
    """(ok|note, 一行)。aib doctor 用。唯讀。"""
    p = status_payload(env, now=now)
    if not p["on"]:
        return "note", T("doctor.unreadable" if p["source"] == "unreadable" else "doctor.off")
    if p.get("plugin_source") == "rejected":
        return "note", T("doctor.plugin_rejected", code=p.get("plugin_problem") or "?", url=p.get("plugin_via_url") or "?")
    if p.get("plugin_source") == "incomplete":
        return "note", T("doctor.plugin_incomplete", code=p.get("plugin_problem") or "?", url=p.get("plugin_via_url") or "?")
    if p.get("plugins_manual_behind"):
        return "note", T("doctor.manual_behind", names=", ".join(p["plugins_manual_behind"])) + \
            (" · " + T("doctor.via", url=p["plugin_via_url"]) if p.get("plugin_via_url") else "")
    if p.get("plugins_missing"):                      # 只有「目錄沒列」也要提醒,不算 ok(#349 r2 L3)
        return "note", T("doctor.plugins_missing", names=", ".join(p["plugins_missing"]))
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


def cmd_off(env=None, out=sys.stdout, home=None, launchctl=_launchctl, platform=None):
    write_setting(False, env)
    removed = remove_agent(home, launchctl, platform)
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
        shown = __import__("approve_gate").reason("auto_update.exclude", {"name": name}, env=env)   # 高風險、一次一按
        _via, why = prove(shown, env, platform=platform)
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


SEAT_OPEN_WAIT_S = (60, 90)   # 開席那次 detach 後先等一下再查:席位剛開、AI 還在載入,別跟開席搶 CPU / 網路


def _detach_now():
    _detach()                    # 呼叫時才查(測試換成不 fork 的)


def _run_action(a, out):
    if a.action == "on":
        return cmd_on(out=out, launchctl=_launchctl)      # 呼叫時才查模組的 _launchctl(測試換成假的)
    if a.action == "off":
        return cmd_off(out=out, launchctl=_launchctl)
    if a.action == "exclude":
        return cmd_exclude(a.sub or "list", a.name, out=out, as_json=a.json)
    return None


def main(argv=None, out=sys.stdout, sleep=time.sleep, wait_s=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="aib update auto", description=T("help.desc"))
    ap.add_argument("action", nargs="?", default="status", choices=["on", "off", "status", "check", "exclude"])
    ap.add_argument("sub", nargs="?", choices=["add", "remove", "list"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--why", default="schedule", choices=["schedule", "tick", "seat-open", "manual"])
    ap.add_argument("--detach", action="store_true")
    a = ap.parse_args(argv)
    # --json:每條路結束只印一個 JSON(人看的字收進 lines;例外也包成一個 JSON,不印 traceback)
    if a.json and not (a.action == "exclude" and (a.sub or "list") == "list"):
        buf = io.StringIO()
        try:
            rc = _run_action(a, buf)
            if rc is None and a.action == "check":
                if a.detach:
                    _detach_now()
                    if a.why == "seat-open":
                        sleep(wait_s if wait_s is not None else random.uniform(*SEAT_OPEN_WAIT_S))
                res, rc = check(why=a.why), 0
            elif rc is None:
                res, rc = status_payload(), 0
            else:
                res = {"action": a.action if a.action != "exclude" else "exclude_" + (a.sub or "list"), "rc": rc,
                       "lines": [ln for ln in buf.getvalue().splitlines() if ln.strip()]}
        except Exception as exc:  # noqa: BLE001
            res, rc = {"action": a.action, "result": "error", "error": type(exc).__name__}, 1
        out.write(json.dumps(res, ensure_ascii=False) + "\n")
        return rc
    rc = _run_action(a, out)
    if rc is not None:
        return rc
    if a.action == "check":
        if a.detach:
            _detach_now()
            if a.why == "seat-open":
                sleep(wait_s if wait_s is not None else random.uniform(*SEAT_OPEN_WAIT_S))
        check(why=a.why)
        return 0
    out.write("\n".join(status_lines(status_payload())) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
