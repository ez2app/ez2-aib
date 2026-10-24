#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib idle — 艙級閒置/殘留偵測與收艙(人工決定,或安全名單自動收)。

**為什麼要有這一支**(08-17 實案,不是假想):本機 load 57(10 核),ps 前幾名
都不大;真正吃 CPU 的是每開一個駕駛艙就跟著一套的四支輪詢腳本
(pane-left/right/bottom-tabs.sh、warroom-board.sh,每 0.1s 醒一次),艙 detach
後照跑;演練 harness 開的外殼艙沒有 teardown;tmux socket 被 tmp 清掉之後
server 變成連不上的孤兒。當時 61 支輪詢腳本,只有 8 支是給有人看的畫面刷新。

既有的席位級 [X](pane-right-ai.sh + activity_probe.py)只看「一席」;本支看
「一艙」與「艙外」:

  目標種類(target kind)與判決(verdict):
    inst:<N>       AIB instance(外殼 session aib-N / main=aib-main + 內艙 -L aihub-N)
                   active        有人接著(外殼有 client、或內艙有人直接 attach)或
                                 有席位在工作
                   unknown       detach 但至少一席量不到活動訊號 → **fail-closed 不收**
                   detached_idle detach 且每一席都閒置 ≥ idle_threshold_s 且艙齡 ≥ min_age_s
                   dead_inner    內艙不存在或內艙所有 pane 都已死,只剩外殼在燒
    srv:<sock>     非標準 socket 目錄的 tmux server(演練/測試用 TMUX_TMPDIR)
                   active / young(艙齡未達 min_age_s,可能是跑中的測試)/ test_residue
    orphan:<pid>   tmux server 行程還在、socket 已消失(kill-server 連不上,只能殺 PID)
    script:<pid>   駕駛艙輪詢腳本的父 tmux server 不在了(ppid=1)
    cli:<pid>      不在任何 tmux pane 之下的 claude/codex/agy CLI(Terminal 直開)
                   stray_active / stray_idle(tty 最後輸入 ≥ idle_threshold_s)
                   ★永不自動收:這些是人的終端,裡面可能有未落地的上下文

  三條紅線(與席位級 [X] 同一哲學):
    1. 量不到=不收(unknown 永遠不進安全名單)
    2. 有人接著=不收(--force 才收,且只有人工路徑有 --force)
    3. 只殺自己盤點到的 PID,一律正數 PID,絕不 kill 負號/整個 process group
       (07-30 a server 事故:kill -PGID 打掉整台)
    4. 自動路徑對艙齡 <60s 的目標一律不收(名單 min_age_s 壓不破這個地板):
       剛出生的測試治具本來就沒人接,「無人」對它沒有意義

  收艙順序:內艙 kill-server → 外殼 kill-session → 殘留 PID 逐個 TERM → 重驗
  每一個目標不論收/略過都寫一行到 logs/idle-reaper.jsonl(沒收的要留下為什麼沒收)

CLI:
  aib idle [scan] [--json]                 盤點
  aib idle close <target> [--yes] [--force] 人工收一個目標(預設要按 y)
  aib idle reap [--dry-run]                依安全名單自動收
  aib idle watch [--interval S] [--dry-run] 即時:每 S 秒 scan+reap
  aib idle policy [show|init]              安全名單
  aib idle sweep-dead [--apply]            死 socket 收拾(§88 包 10,2026-09-11):母體 = half_cabin_arm 判 dead
                                           (server 打不到、socket 檔還在)且外層 session 也不在的 hub socket;
                                           預設 dry-run 逐一列「會 rm 什麼」,--apply 才 os.unlink;
                                           艙齡(socket mtime)<60s 不動;只 rm 本輪自己判 dead 的路徑,不掃通配;
                                           每艙一行留痕 <狀態根>/idle-sweep-dead.jsonl
  scan 每個 inst 目標多一欄 cabin_health(healthy / half-born / dead / unmeasurable),
  值由 half_cabin_arm 同一份判準算,不重寫.
  rc:0 正常 / 2 用法或名單缺 / 3 目標拒收(有人接著/unknown)

安全名單 ~/.ez2-aib/idle-policy.json(EZ2_AIB_IDLE_POLICY 可覆寫):
  {"schema":"aib-idle-policy/v1",
   "auto_close":{"test_residue":true,"orphan_server":true,"orphan_script":true,
                 "dead_inner":true,"detached_idle":false},
   "idle_threshold_s":3600,"min_age_s":1800,
   "protect":{"instances":["main"],"sessions":[],"roles":[]}}
  auto_close 沒列的類別一律 false;cli:* 沒有開關(結構上不可自動)。
"""

import argparse
import calendar

# ★ EH.17 甲:末五碼取法收進 seat_address.uuid_tail5 一處。本檔原本自己算,
#   而三處算法**不等價**(見該函式 docstring)。同目錄兄弟模組,不動 sys.path。
import os as _os_eh17
import sys as _sys_eh17
_HERE_EH17 = _os_eh17.path.dirname(_os_eh17.path.abspath(__file__))
if _HERE_EH17 not in _sys_eh17.path:
    _sys_eh17.path.insert(0, _HERE_EH17)
from seat_address import uuid_tail5  # noqa: E402
import glob
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import activity_probe  # noqa: E402
import half_cabin_arm  # noqa: E402  §88 包 10:艙健康四判準的單一來源,只讀
import i18n_lib  # noqa: E402
import seat_uuid_backfill  # noqa: E402  ED.13 定期回填(2026-08-22)
import seat_exit  # noqa: E402  10-09 a seat D2:每輪補退出痕(含已沒有 watch 的死艙)

UID = os.getuid()
HOME = os.path.expanduser("~")
# ED.1(2026-08-22):EZ2_AIB_MAIN_HOME 是本檔自己的名字,沒有回退到全庫的狀態根
#   (EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME)——注入根時本檔的心跳/日誌/鎖仍落真家目錄。
#   保留本檔名字為最高優先(test-idle-reaper 治具用它),再回退到共同的根。
AIB_MAIN_HOME = os.environ.get("EZ2_AIB_MAIN_HOME") \
    or os.environ.get("EZ2_AIB_STATE_ROOT") or os.environ.get("EZ2_AIB_HOME") \
    or os.path.join(HOME, ".ez2-aib")
POLICY_PATH = os.environ.get("EZ2_AIB_IDLE_POLICY") \
    or os.path.join(AIB_MAIN_HOME, "idle-policy.json")
LOG_PATH = os.environ.get("EZ2_AIB_IDLE_LOG") \
    or os.path.join(AIB_MAIN_HOME, "logs", "idle-reaper.jsonl")
TMUX = os.environ.get("EZ2_AIB_TMUX_BIN") or shutil.which("tmux") or "tmux"

# ── BL(2026-09-04):常駐 watch 也要自癒 ──────────────────────────────────────
# 根因與面板三支同一個(BF,見 src/cockpit/pane-tick.sh 檔頭):行程在啟動當下就把碼
# 讀進記憶體,檔案後來怎麼改都不影響已經在跑的那一份。實測本機六個 watch 全部啟動於
# 一天以上之前 ⇒ 這幾天所有落地的修法,活著的收艙器一格都沒吃到,而畫面、心跳、報表
# 全部正常 —— 失效形狀是「完全看不出來」。
# 取法與 BF 一致但**不能共用**:那支是 bash 函式(`pane_reexec_if_changed`),而且它的
# exec 不帶引數;本支的模式住在 argv 裡(`watch --interval …`),所以 argv 要原樣帶回。
# 失效側同樣選「不重跑」:指紋量不到(檔被刪、stat 失敗)一律當沒變 —— 舊碼繼續跑是貴,
# exec 進一個不存在的檔是死。
REEXEC_LOG = os.environ.get("EZ2_PANE_REEXEC_LOG") \
    or os.path.join(AIB_MAIN_HOME, "logs", "pane-reexec.log")


def _reexec_interval():
    """節流秒數。非法值退預設,不靜默失敗成 0(0 = 每輪都問,那是測試才要的)。"""
    raw = os.environ.get("EZ2_IDLE_REEXEC_INTERVAL")
    if raw is None or not raw.strip().isdigit():
        return 30
    return int(raw.strip())


_REEXEC_PATHS = None
_REEXEC_FP = ""
_REEXEC_NEXT = 0.0


def reexec_watch_paths():
    """要盯的檔:本檔 + 啟動當下已載入、且住在同一個目錄樹裡的產品模組。

    ★ 名單**只算一次**:若每輪重算,後來才 import 進來的模組會讓指紋自己變,
      「碼換了」與「多 import 了一支」在指紋上逐字相同,於是自癒會無故開火。
    ★ 只盯本檔會漏掉真正的失效面:activity_probe / seat_uuid_backfill 改了而本檔沒改時,
      這個行程照樣跑著舊的那一份。
    """
    global _REEXEC_PATHS
    if _REEXEC_PATHS is None:
        here = os.path.dirname(os.path.abspath(__file__))
        found = {os.path.abspath(__file__)}
        for mod in list(sys.modules.values()):
            f = getattr(mod, "__file__", None)
            if not f:
                continue
            f = os.path.abspath(f)
            if f.startswith(here + os.sep) and f.endswith(".py"):
                found.add(f)
        _REEXEC_PATHS = sorted(found)
    return _REEXEC_PATHS


def reexec_fingerprint():
    """mtime:size 串起來。**少一個檔就回空**,不是跳過它繼續算 —— 跳過的話,
    檔被刪時指紋照樣「變了」,下一步就 exec 進一個不存在的檔(BF 臂 d 抓到過)。"""
    out = []
    for path in reexec_watch_paths():
        try:
            st = os.stat(path)
        except OSError:
            return ""
        out.append("%d:%d" % (int(st.st_mtime), st.st_size))
    return "|".join(out)


def reexec_log(script, old, new):
    """一次重跑寫一行 JSON(與 pane-tick.sh 同一個檔、同一組欄位,讀的人只有一份格式)。
    ★ 沒有留痕的自癒與沒有自癒在畫面上逐字相同(BF-3 判例)。寫不進去不得讓 watch 死。"""
    try:
        os.makedirs(os.path.dirname(REEXEC_LOG), exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "pid": os.getpid(),
               "pane": os.environ.get("TMUX_PANE", ""), "script": script,
               "old": old, "new": new}
        with open(REEXEC_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        return False
    return True


def reexec_if_changed(argv=None, now=None):
    """指紋變了就 exec 自己;argv 原樣帶回(模式住在 argv 裡,掉引數等於換了一支程式)。

    回傳 False = 這一輪沒重跑。真的重跑時本函式不會回來(os.execv 之後就是新的程式)。
    """
    global _REEXEC_FP, _REEXEC_NEXT
    now = time.monotonic() if now is None else now
    if now < _REEXEC_NEXT:
        return False
    _REEXEC_NEXT = now + _reexec_interval()
    fp = reexec_fingerprint()
    if not fp:                      # 量不到 = 當沒變(失效側選舊碼繼續跑)
        return False
    if not _REEXEC_FP:
        _REEXEC_FP = fp
        return False
    if fp == _REEXEC_FP:
        return False
    me = os.path.abspath(__file__)
    if not os.path.isfile(me):      # 指紋與 exec 之間的窗口:那一瞬間被刪一樣會死
        return False
    reexec_log(me, _REEXEC_FP, fp)
    print("aib idle watch: 腳本已更新,重跑自己(%s)" % me, file=sys.stderr)
    sys.stderr.flush()
    sys.stdout.flush()
    args = list(sys.argv[1:] if argv is None else argv)
    os.execv(sys.executable, [sys.executable, me] + args)
    return False                    # execv 不回來;留著讓「它回來了」不會靜靜往下走

MAIN_SESSION = "aib-main"    # 主艙外殼 session(與艙解析器同一字)
MAIN_HUB = "aihub"

COCKPIT_SCRIPT_RE = re.compile(
    r"/src/cockpit/(pane-[a-z-]+|warroom-board|hub-lobby)\.sh(\s|$)")
CLI_RE = re.compile(r"(^|/)(claude|codex|agy|antigravity|copilot)(\s|$)")
INSTANCE_RE = re.compile(r"[a-z0-9-]+\Z")  # cabin_resolve.sh's accepted name alphabet.
_CPU_HISTORY = {}  # This watch's observed work; never infer an hour from one quiet second.

RESIDUE_CLASSES = ("test_residue", "orphan_server", "orphan_script", "dead_inner")
IDLE_CLASSES = ("detached_idle",)
AUTO_CLASSES = RESIDUE_CLASSES + IDLE_CLASSES     # cli 永不在此
AUTO_MIN_AGE_FLOOR_S = 60                          # 自動路徑硬地板,名單壓不破
WATCH_ARGS_RE = re.compile(r"(idle_reaper|menu_pick)\.py\s+watch(\s|$)")   # 常駐窗自我辨識(10-13 加 sys.menu 選單自動選窗)
LOCK_PATH = os.environ.get("EZ2_AIB_IDLE_LOCK") \
    or os.path.join(AIB_MAIN_HOME, "idle-reaper.lock")

# ---- W1 監控的監控(the maintainer 08-18 直令)---------------------------------------
# 病根實案:一個偽裝成輪詢腳本的 tmux server 被手動殺掉,連帶兩艙外殼。
# **若被殺的是 watch,它會靜靜死掉,而畫面上什麼都不會變**——沒有心跳的監控
# 等於沒有監控:它的「綠」只代表沒有人去看,不代表它還活著。
# ★ 心跳是 **per-instance** 的,不是全域一份(dev5 實測發現的缺陷,艦外原稿寫
#   `~/.ez2-aib/watch-heartbeat.json`):本機 `~/.ez2-aib/instances/` 下有 11 個
#   instance,每個艙各跑自己的 `aib idle watch`。共用一個檔=最後寫的人贏,於是
#   「instance 13 的 watch 死了」會被 instance 2 剛寫的心跳蓋成綠——**這正是本單
#   要消滅的那種假綠,只是換了一個地方長出來**。落點跟著 instance home 走;
#   main 的 instance home 就是 ~/.ez2-aib,所以 main 的路徑與原稿完全一致。
AIB_INSTANCE_HOME = os.environ.get("EZ2_AIB_INSTANCE_HOME") or AIB_MAIN_HOME
HEARTBEAT_PATH = os.environ.get("EZ2_AIB_WATCH_HEARTBEAT") \
    or os.path.join(AIB_INSTANCE_HOME, "watch-heartbeat.json")
HEARTBEAT_SCHEMA = "aib-watch-heartbeat/v1"
# 逾時 = 幾個輪詢間隔。心跳自己帶 interval_s,查驗器據此換算——**不寫死 600s**,
# 否則一個 interval=1800 的 watch 會被永遠判死,而 interval=30 的死了半小時才看得出來。
HEARTBEAT_STALE_ROUNDS = 2
HEARTBEAT_FALLBACK_INTERVAL_S = 300    # 心跳沒帶 interval 時的保守值(= watch 預設)
# 未來時戳容差:與 rotation/lifecycle 同一條(時鐘漂移給 5 分鐘,偽造的未來值不得永遠新鮮)。
HEARTBEAT_FUTURE_SLACK_S = 300


def aib_instance():
    """本艙 instance 名;與 cockpit-env.sh:61 同一個預設(未設=main)。"""
    return os.environ.get("EZ2_AIB_INSTANCE") or "main"


def _utc_iso(now_ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ms / 1000.0))


def parse_utc_ms(text):
    """`YYYY-MM-DDTHH:MM:SS[.fff](Z|+00:00)` -> epoch ms;其餘回 None。

    ★ 只收 UTC、且用 `calendar.timegm` 不用 `time.mktime`(51982ab 那個坑):
      mktime 把 UTC 時戳當本地時間解,在 +8 會把 8 小時內的新鮮心跳判成過期,
      在負時區則反過來 fail-open —— 一個「看起來還活著」的死 watch 是本單要擋的東西本身。
    形狀與 rotation/lifecycle.parse_observed_utc 相同,由測試看守兩邊一致(不 import 綁死)。"""
    if not isinstance(text, str):
        return None
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})"
                     r"(?:\.\d{1,9})?(?:Z|\+00:00)", text.strip())
    if not m:
        return None
    try:
        return int(calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0)) * 1000)
    except (ValueError, OverflowError):
        return None


def write_heartbeat(round_n, last_scan_rc, interval_s, mode=None, error=None,
                    path=None, now_ms=None, pid=None):
    """每輪寫一次心跳(原子)。回 (True, None) 或 (False, 原因)。

    ★ 心跳寫失敗**不得讓 watch 死**,但也不得靜默:寫不進去時查驗器會判 stale,
      方向是誤報而不是漏報——這道機件的失效方向必須是「吵」,不是「安靜」。
    """
    path = path or HEARTBEAT_PATH
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    doc = {"schema": HEARTBEAT_SCHEMA, "instance": aib_instance(),
           "pid": int(pid if pid is not None else os.getpid()),
           "at_ms": now_ms, "observed_at": _utc_iso(now_ms),
           "round": int(round_n), "interval_s": int(interval_s),
           "last_scan_rc": int(last_scan_rc), "last_mode": mode,
           "last_error": (str(error)[:200] if error else None)}
    blob = json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n"
    tmp = None
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".watch-heartbeat.", dir=os.path.dirname(os.path.abspath(path)))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)                     # 原子換名:讀者永遠看到完整的一份
        tmp = None
        dfd = os.open(os.path.dirname(os.path.abspath(path)), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
        return True, None
    except OSError as err:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False, str(err)


def read_heartbeat(path=None):
    """回 (doc, 原因);讀不到/形不對回 (None, 原因)。壞檔不當成「還活著」。"""
    path = path or HEARTBEAT_PATH
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError) as err:
        return None, "unreadable:%s" % err
    if not isinstance(doc, dict) or doc.get("schema") != HEARTBEAT_SCHEMA:
        return None, "schema_mismatch"
    if not isinstance(doc.get("at_ms"), int) or isinstance(doc.get("at_ms"), bool):
        at_ms = parse_utc_ms(doc.get("observed_at"))   # 舊心跳沒有 at_ms 時的後備
        if at_ms is None:
            return None, "no_timestamp"
        doc["at_ms"] = at_ms
    if not isinstance(doc.get("pid"), int) or isinstance(doc.get("pid"), bool):
        return None, "no_pid"
    return doc, None


def pid_alive(pid):
    """行程是否還在。EPERM=在(是別人的行程),ESRCH=不在。"""
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False


def ancestor_pids(pid=None, limit=32):
    """自己與所有祖先 pid;拿不到就只回自己(不猜)。"""
    pid = int(pid if pid is not None else os.getpid())
    chain, seen = [pid], {pid}
    for _ in range(limit):
        res = subprocess.run(["ps", "-o", "ppid=", "-p", str(chain[-1])],
                             capture_output=True, text=True)
        if res.returncode != 0:
            break
        try:
            ppid = int(res.stdout.strip())
        except ValueError:
            break
        if ppid <= 1 or ppid in seen:
            break
        chain.append(ppid)
        seen.add(ppid)
    return chain


def watch_status(now_ms=None, path=None, self_chain=None, stale_s=None, alive=None,
                 expect_instance=None):
    """W1 查驗器:watch 還活著嗎?回 {"ok","rc","reasons","heartbeat","stale_s"}。

    rc:0=新鮮 / 3=心跳缺檔・逾時・pid 不在(監控已失效)/ 2=**自己驗自己**(見下)。

    ★★ 紅線(艦外指名):**查驗器不得由 watch 自己跑**。一個由 watch 行程自己執行的
      查驗,在 watch 死掉的那一刻也一起死了——它永遠只會回報「我還活著」,
      而那正是它唯一不可能觀察到的狀態。所以偵測到自己(或自己的祖先)就是心跳裡那個
      pid 時,一律 rc=2 拒答,不給一個看起來像綠的答案。
    """
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    doc, why = read_heartbeat(path)
    if doc is None:
        return {"ok": False, "rc": 3, "reasons": ["heartbeat_%s" % why],
                "heartbeat": None, "stale_s": None}
    interval = doc.get("interval_s")
    if not isinstance(interval, int) or isinstance(interval, bool) or interval <= 0:
        interval = HEARTBEAT_FALLBACK_INTERVAL_S
    limit = int(stale_s if stale_s is not None else interval * HEARTBEAT_STALE_ROUNDS)
    # 交叉核 instance:拿到的心跳必須是**我們問的那個艙**的。路徑走錯/環境變數沒帶
    # 對時,讀到別艙的新鮮心跳會給出一個完全錯誤的綠(與 read_machine_ctx 核
    # session_id 是同一條紀律:檔名說是誰、內容也要說是同一個誰)。
    want = expect_instance if expect_instance is not None else aib_instance()
    if want is not False and doc.get("instance") != want:
        return {"ok": False, "rc": 3,
                "reasons": ["instance_mismatch:%s!=%s" % (doc.get("instance"), want)],
                "heartbeat": doc, "stale_s": None}
    chain = self_chain if self_chain is not None else ancestor_pids()
    if doc["pid"] in set(chain):
        return {"ok": False, "rc": 2, "reasons": ["self_check_refused"],
                "heartbeat": doc, "stale_s": limit}
    reasons = []
    age_s = (now_ms - doc["at_ms"]) / 1000.0
    if age_s > limit:
        reasons.append("stale:%.0fs>%ds" % (age_s, limit))
    if -age_s > HEARTBEAT_FUTURE_SLACK_S:
        reasons.append("future_timestamp:%.0fs" % (-age_s))
    is_alive = alive(doc["pid"]) if alive else pid_alive(doc["pid"])
    if not is_alive:
        reasons.append("pid_gone:%s" % doc["pid"])
    if doc.get("last_scan_rc") not in (0, None):
        reasons.append("last_scan_rc:%s" % doc["last_scan_rc"])
    return {"ok": not reasons, "rc": 3 if reasons else 0, "reasons": reasons,
            "heartbeat": doc, "stale_s": limit, "age_s": round(age_s, 1)}
DEFAULT_POLICY = {
    "schema": "aib-idle-policy/v1",
    "auto_close": {"test_residue": True, "orphan_server": True,
                   "orphan_script": True, "dead_inner": True,
                   "detached_idle": False},
    "idle_threshold_s": 3600,
    "min_age_s": 1800,
    "protect": {"instances": ["main"], "sessions": [], "roles": []},
}

_T = {}   # 語系 → Translator(每種語系字典只載一次)


def t(key, **params):
    # 10-14 語系盤查第 26 條:aib idle watch 是長駐行程,原本 Translator 只建一次 ⇒ 切語系後一直講舊語言;
    #   改成每次照 resolve_locale() 取(只讀一兩個小設定檔),語系變了換那一份字典。
    loc = i18n_lib.resolve_locale()
    tr = _T.get(loc)
    if tr is None:
        tr = _T[loc] = i18n_lib.Translator(loc)
    return tr.t(key, {k: str(v) for k, v in params.items()})


# ---------------------------------------------------------------- 快照(真系統)
def _run(cmd, timeout=10, why=None):
    """跑一支外部指令。回 stdout(成功)或 None(任何失效)。

    ★★ `why`:傳一個 dict 進來,失效時填 `why["reason"]` 三態
      ``oserror`` / ``timeout`` / ``rc``。**回傳值刻意不變**——
      既有呼叫端(`tmux_query` 那一族)不需要分辨,改它們的契約只會擴大爆炸半徑。
      需要分辨的呼叫端自己帶一個 dict 進來。
    ★ 為什麼要能分辨(判準 SE5):三種失效壓成同一個 `None` 之後,
      呼叫端**連「是哪一種失敗」都拿不到**,而其中一種(逾時)與
      「真的沒有東西」在下游逐字同形。
    """
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        if why is not None:
            why["reason"] = "timeout"
        return None
    except OSError:
        if why is not None:
            why["reason"] = "oserror"
        return None
    if p.returncode != 0:
        if why is not None:
            why["reason"] = "rc"
        return None
    return p.stdout


def parse_etime(s):
    """ps etime `[[dd-]hh:]mm:ss` 與 macOS time `mm:ss.xx` → 秒;壞形狀回 None。"""
    m = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)(?:\.\d+)?", (s or "").strip())
    if not m:
        return None
    d, h, mi, se = m.groups()
    return int(d or 0) * 86400 + int(h or 0) * 3600 + int(mi) * 60 + int(se)


def collect_procs(why=None):
    """全機行程表。★ `why` 同 `_run`:呼叫端要分辨失效種類就帶一個 dict 進來。

    ★★★ 這裡的 `(out or "")` 是本族的核心:`ps` 逾時 -> `out=None` -> 空字典,
      而**「ps 逾時」與「機器上沒有任何行程」在呼叫端逐字相同**。
      回傳值仍是字典(不改契約),**能不能分辨由 `why` 帶出去**。
    """
    out = _run(["ps", "-Ao", "pid=,ppid=,etime=,time=,tty=,args="], why=why)
    procs = {}
    for line in (out or "").splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        procs[pid] = {"pid": pid, "ppid": ppid, "age_s": parse_etime(parts[2]),
                      "cpu_s": parse_etime(parts[3]), "tty": parts[4],
                      "args": parts[5]}
    return procs


def std_sock_dir():
    base = os.environ.get("TMUX_TMPDIR") or "/tmp"
    return os.path.realpath(os.path.join(base, "tmux-%d" % UID))


def _is_tmux(p):
    return os.path.basename(p["args"].split(None, 1)[0]) == "tmux"


def tmux_server_pids(procs):
    """tmux server = comm 是 tmux 且已被 launchd 收養(ppid=1)。"""
    return sorted(p["pid"] for p in procs.values() if _is_tmux(p) and p["ppid"] == 1)


def sockets_of(pids):
    """server pid → socket 路徑;lsof 一次查全部。

    回 None = **查詢方式失效**(沒 lsof / lsof 跑不動),與「查到了但這台沒 socket」
    (孤兒)是相反的處置:前者停用孤兒判定,後者才是孤兒。兩者混成同一個空 dict
    會讓「工具缺席」變成「每台 server 都是孤兒」→ reap 一輪殺光所有 tmux。"""
    if not pids:
        return {}
    lsof = shutil.which("lsof") or next((p for p in ("/usr/sbin/lsof", "/usr/bin/lsof") if os.access(p, os.X_OK)), None)   # 10-11:Mac 的 lsof 在 /usr/sbin,PATH 不一定有
    if not lsof:
        return None
    out = _run([lsof, "-a", "-U", "-p", ",".join(str(p) for p in pids), "-Fpn"],
               timeout=20)
    if out is None:
        return None
    found, cur = {}, None
    for line in out.splitlines():
        if line.startswith("p"):
            cur = int(line[1:])
        elif line.startswith("n") and cur is not None:
            path = line[1:].split("->")[0].strip()
            if "/tmux-%d/" % UID in path and cur not in found:
                found[cur] = path
    return found


def tmux_query(sock, *args):
    return _run([TMUX, "-S", sock] + list(args), timeout=5)


def collect_server(sock, pid):
    sess = tmux_query(sock, "list-sessions", "-F",
                      "#{session_name}\t#{session_attached}\t#{session_created}\t#{session_activity}")
    if sess is None:
        return None
    server = {"pid": pid, "sock": sock,
              "std": os.path.realpath(os.path.dirname(sock)) == std_sock_dir(),
              "sessions": {}, "clients": []}
    for line in sess.splitlines():
        f = line.split("\t")
        if len(f) < 4:
            continue
        server["sessions"][f[0]] = {"name": f[0], "attached": int(f[1] or 0),
                                    "created": int(f[2] or 0),
                                    "activity": int(f[3] or 0), "windows": {}}
    panes = tmux_query(sock, "list-panes", "-a", "-F",
                       "#{session_name}\t#{window_id}\t#{window_name}\t#{pane_id}\t"
                       "#{pane_pid}\t#{pane_dead}\t#{pane_tty}\t#{pane_current_command}\t"
                       "#{pane_last_activity}")
    for line in (panes or "").splitlines():
        f = line.split("\t")
        if len(f) < 8 or f[0] not in server["sessions"]:
            continue
        win = server["sessions"][f[0]]["windows"].setdefault(
            f[1], {"id": f[1], "name": f[2], "panes": []})
        win["panes"].append({"id": f[3], "pid": int(f[4] or 0), "dead": f[5] == "1",
                             "tty": f[6], "cmd": f[7],
                             "tmux_activity": f[8] if len(f) > 8 else None})
    clients = tmux_query(sock, "list-clients", "-F", "#{client_tty}\t#{client_session}")
    for line in (clients or "").splitlines():
        f = line.split("\t")
        if len(f) >= 2:
            server["clients"].append({"tty": f[0], "session": f[1]})
    return server


def instance_ids():
    ids = ["main"]
    root = os.path.join(AIB_MAIN_HOME, "instances")
    try:
        ids += sorted((n for n in os.listdir(root) if n != "main" and INSTANCE_RE.fullmatch(n)
                       and os.path.isdir(os.path.join(root, n))
                       and not os.path.islink(os.path.join(root, n))),
                      key=lambda n: (0, int(n)) if n.isdigit() else (1, n))
    except OSError:
        pass
    return ids


def glob_sockets():
    """lsof 退路:標準目錄 + 常見的 tmp 下 TMUX_TMPDIR 形狀;socket → server pid
    由 tmux 自己回答(display -p #{pid}),不猜。"""
    pats = [os.path.join(std_sock_dir(), "*")]
    for base in ("/private/tmp", "/tmp", os.environ.get("TMPDIR") or ""):
        if base:
            pats += [os.path.join(base, "*", "tmux-%d" % UID, "*"),
                     os.path.join(base, "*", "tmux", "tmux-%d" % UID, "*")]
    found = {}
    for pat in pats:
        for sock in glob.glob(pat):
            out = _run([TMUX, "-S", sock, "display-message", "-p", "#{pid}"], timeout=5)
            try:
                pid = int((out or "").strip())
            except ValueError:
                continue
            found.setdefault(pid, os.path.realpath(sock))
    return found


def _sample_inner_activity(servers, procs):
    """One shared CPU interval for CLI panes, excluding the outer pollers/watch.

    A missing tmux timestamp always takes this same measured CPU path. Remember
    recent work so a subsequent quiet sample cannot revive an ancient transcript
    age. This is only a cap: missing transcript duration remains unmeasured.
    """
    global _CPU_HISTORY
    panes, keys = [], []
    for server in servers:
        hub = os.path.basename(server["sock"])
        prefix = MAIN_HUB + "-"
        if not server["std"] or not (hub == MAIN_HUB or
                (hub.startswith(prefix) and INSTANCE_RE.fullmatch(hub[len(prefix):]))):
            continue
        for session in server["sessions"].values():
            for window in session["windows"].values():
                for pane in window["panes"]:
                    if pane["dead"] or pane["pid"] not in procs or is_watch_pane(procs, pane["pid"]):
                        continue
                    panes.append(pane)
                    keys.append((server["sock"], pane["id"], pane["pid"]))
    samples = activity_probe.sample_cpu_activity(panes)
    now, history = time.monotonic(), {}
    for pane, key, sample in zip(panes, keys, samples):
        age = procs[pane["pid"]].get("age_s")
        previous = _CPU_HISTORY.get(key)
        last_busy = previous[1] if previous else None
        replaced = previous and (age is None or previous[0] is None or age < previous[0])
        if sample["state"] != "idle" or replaced:
            last_busy = now
        sample["idle_cap_s"] = None if last_busy is None else max(0.0, now - last_busy)
        pane["activity_sample"] = sample
        history[key] = (age, last_busy)
    _CPU_HISTORY = history


def collect_snapshot():
    # ★★★ 判準 SE1:真值條件要涵蓋**所有**能讓 procs/servers 為空的路徑,
    #   不是只涵蓋其中一條。這裡有兩條:
    #     (a) `ps` 失效(oserror/timeout/rc) -> procs 空 -> servers 空
    #     (b) `lsof` 失效 -> socks 為 None -> 孤兒判定不成立
    #   舊版只問 (b),於是 (a) 發生時 `orphan_detection` **仍然是 True** ——
    #   **那個欄位回答的是「sockets_of 有沒有失敗」,而它宣稱回答的是
    #   「這次偵測有沒有效」。命題與量法不同軸。**(判準 §3)
    why = {}
    procs = collect_procs(why=why)
    procs_error = why.get("reason")
    procs_trusted = procs_error is None
    spids = tmux_server_pids(procs)
    socks = sockets_of(spids)
    orphan_detection = (socks is not None) and procs_trusted
    if socks is None:
        socks = glob_sockets()
    servers, orphans = [], []
    for pid in spids:
        sock = socks.get(pid)
        srv = collect_server(sock, pid) if sock and os.path.exists(sock) else None
        if srv is not None:
            servers.append(srv)
        elif orphan_detection:
            orphans.append(pid)          # lsof 說它沒 socket=真孤兒
        # else:查詢方式失效,誠實不判(不進 orphans,也不進 servers)
    # ★★ 判準 SE2:三態不是兩態 —— 「查得到且真的空」與「查不到」必須可分辨。
    #   舊版兩者共用 `servers=0`;現在由 `procs_trusted` 分開,
    #   並保留 `procs_error` 讓呼叫端知道是三種失效的哪一種。
    _sample_inner_activity(servers, procs)
    return {"now": time.time(), "procs": procs, "servers": servers,
            "procs_trusted": procs_trusted, "procs_error": procs_error,
            "orphan_server_pids": orphans, "orphan_detection": orphan_detection,
            "instances": instance_ids(), "std_sock_dir": std_sock_dir()}


def _pol_int(policy, key, default):
    """名單裡的整數欄;★0 是合法值(治具/立即收),不能用 `or default` 吃掉。"""
    v = policy.get(key)
    if v is None:
        return default
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return v if v >= 0 else default


# ---------------------------------------------------------------- 分類(純函數)
def descendants(procs, root, acc=None):
    acc = set() if acc is None else acc
    for p in procs.values():
        if p["ppid"] == root and p["pid"] not in acc:
            acc.add(p["pid"])
            descendants(procs, p["pid"], acc)
    return acc


def pane_subtree(procs, server):
    pids = set()
    for s in server["sessions"].values():
        for w in s["windows"].values():
            for pane in w["panes"]:
                if pane["pid"]:
                    pids.add(pane["pid"])
                    descendants(procs, pane["pid"], pids)
    return pids


def is_watch_pane(procs, pane_pid):
    """pane 本體或其子孫是 `idle_reaper.py watch` = 常駐監看窗。"""
    if not pane_pid or pane_pid not in procs:
        return False
    for pid in {pane_pid} | descendants(procs, pane_pid):
        p = procs.get(pid)
        if p and WATCH_ARGS_RE.search(p["args"]):
            return True
    return False


def _find_server(snap, sock_name, std=True):
    for s in snap["servers"]:
        if s["std"] == std and os.path.basename(s["sock"]) == sock_name:
            return s
    return None


def _human_attached(outer_sess, inner_srv):
    """外殼有 client=有人;內艙 client 的 tty 不屬於外殼任何 pane=有人直接 attach。"""
    if outer_sess and outer_sess["attached"] > 0:
        return True
    if not inner_srv:
        return False
    outer_ttys = set()
    if outer_sess:
        for w in outer_sess["windows"].values():
            for p in w["panes"]:
                outer_ttys.add(p["tty"])
    return any(c["tty"] not in outer_ttys for c in inner_srv["clients"])


def _seat_rows(snap, inner_srv, seats_dir, threshold):
    rows = []
    if not inner_srv:
        return rows
    for s in inner_srv["sessions"].values():
        for wid, w in s["windows"].items():
            for pane in w["panes"]:
                # 08-18 常駐 watch 窗(cockpit-full 建,@aib_role=sys.watch)不是席:
                # 靠 pane 子樹裡的 argv 自我辨識(零 tmux 呼叫、無格式注入面),
                # 不然它 idle=None 會讓整艙永遠 unknown。
                if is_watch_pane(snap["procs"], pane["pid"]):
                    continue
                alive = (not pane["dead"]) and pane["pid"] in snap["procs"]
                prov, uuid = activity_probe.seat_for_window(wid, seats_dir)
                # 10-10 a seat:行程表已在 snap 裡,交給探針找背景分身,不再多叫一次 ps(本檔的尺數 ps 次數)。
                idle = activity_probe.activity_age(prov, uuid, snap["now"], procs=snap["procs"]) if alive else None
                sample = pane.get("activity_sample") or {}
                activity_state = sample.get("state", "unknown")
                if activity_state == "active":
                    idle = 0
                elif activity_state != "idle":
                    idle = None
                elif idle is not None and sample.get("idle_cap_s") is not None:
                    idle = min(idle, sample["idle_cap_s"])
                role = seat_id = None
                try:
                    with open(os.path.join(seats_dir, "w%s.json" % wid.lstrip("@")),
                              encoding="utf-8") as fh:
                        rec = json.load(fh)
                    if isinstance(rec, dict):
                        role = rec.get("role") if isinstance(rec.get("role"), str) else None
                        seat_id = rec.get("seat_id") if isinstance(rec.get("seat_id"), str) else None
                except (OSError, ValueError):
                    pass
                # 身份欄給效能頁「role@seat五碼+uuid 末五碼」用(the maintainer 08-17 直令):
                # uuid 只給 activity_probe 解析成功的那個(與 idle 同源),不另讀席位檔猜。
                rows.append({"window": wid, "name": w["name"], "pid": pane["pid"],
                             "alive": alive, "provider": prov, "role": role,
                             "seat_id": seat_id,
                             "signal": sample.get("signal", "cpu-delta"),
                             "activity_state": activity_state,
                             "tmux_activity": sample.get("tmux_activity", "unmeasured"),
                             "tmux_activity_epoch": sample.get("tmux_activity_epoch"),
                             "cpu_delta_s": sample.get("cpu_delta_s"),
                             "sample_s": sample.get("sample_s"),
                             "uuid_tail5": uuid_tail5(uuid),
                             "idle_s": None if idle is None else int(idle),
                             "idle_ok": alive and activity_state == "idle"
                             and idle is not None and idle >= threshold})
    return rows


def classify_instance(snap, inst, policy):
    threshold = _pol_int(policy, "idle_threshold_s", 3600)
    min_age = _pol_int(policy, "min_age_s", 1800)
    outer_name = MAIN_SESSION if inst == "main" else "aib-%s" % inst
    hub_name = MAIN_HUB if inst == "main" else "aihub-%s" % inst
    seats_dir = os.path.join(AIB_MAIN_HOME, "seats") if inst == "main" \
        else os.path.join(AIB_MAIN_HOME, "instances", inst, "seats")
    default = _find_server(snap, "default", std=True)
    outer = default["sessions"].get(outer_name) if default else None
    inner = _find_server(snap, hub_name, std=True)
    if not outer and not inner:
        return None
    procs = snap["procs"]
    outer_pids = set()
    if outer:
        for w in outer["windows"].values():
            for p in w["panes"]:
                if p["pid"]:
                    outer_pids.add(p["pid"])
                    descendants(procs, p["pid"], outer_pids)
    scripts = [pid for pid in outer_pids
               if pid in procs and COCKPIT_SCRIPT_RE.search(procs[pid]["args"])]
    script_cpu = sum((procs[p]["cpu_s"] or 0) for p in scripts)
    seats = _seat_rows(snap, inner, seats_dir, threshold)
    ages = []
    if inner and inner["pid"] in procs:
        ages.append(procs[inner["pid"]]["age_s"])
    if outer and outer["created"]:
        ages.append(int(snap["now"] - outer["created"]))
    ages = [a for a in ages if a is not None]
    age = min(ages) if ages else None
    human = _human_attached(outer, inner)
    live_seats = [s for s in seats if s["alive"]]
    if human:
        verdict = "active"
    elif not inner or not live_seats:
        verdict = "dead_inner"
    elif any(s["idle_s"] is None for s in live_seats):
        verdict = "unknown"
    elif all(s["idle_ok"] for s in live_seats) and age is not None and age >= min_age:
        verdict = "detached_idle"
    else:
        verdict = "active"
    # O2:開席還沒結束的艙,有限 grace 內不當 detach 收掉(人還在等畫面)。
    if verdict == "detached_idle":
        inst_home = os.path.dirname(seats_dir)
        try:
            from seat_status import instance_opening_in_progress
            if instance_opening_in_progress(inst_home):
                verdict = "active"
        except Exception:
            pass
    kill_pids = sorted(outer_pids | (pane_subtree(procs, inner) if inner else set()))
    return {"id": "inst:%s" % inst, "kind": "inst", "verdict": verdict,
            "instance": inst, "outer": outer_name if outer else None,
            "hub": hub_name if inner else None,
            "human_attached": human, "age_s": age,
            "seats": seats, "scripts": scripts, "script_cpu_s": int(script_cpu),
            "roles": sorted({s["role"] for s in seats if s["role"]}),
            "steps": ([("kill-server", inner["sock"])] if inner else [])
                     + ([("kill-session", default["sock"], outer_name)] if outer else []),
            "kill_pids": kill_pids}


def classify_ephemeral(snap, policy):
    """非標準 socket 目錄的 server,以「同一個 TMUX_TMPDIR」為一組。"""
    min_age = _pol_int(policy, "min_age_s", 1800)
    groups = {}
    for s in snap["servers"]:
        if s["std"]:
            continue
        key = os.path.dirname(s["sock"])           # .../tmux-501
        groups.setdefault(key, []).append(s)
    out = []
    procs = snap["procs"]
    for key, servers in sorted(groups.items()):
        pane_ttys, clients, pids, ages, alive = set(), [], set(), [], 0
        for s in servers:
            pids.add(s["pid"])
            pids |= pane_subtree(procs, s)
            clients += s["clients"]
            if s["pid"] in procs and procs[s["pid"]]["age_s"] is not None:
                ages.append(procs[s["pid"]]["age_s"])
            for sess in s["sessions"].values():
                for w in sess["windows"].values():
                    for p in w["panes"]:
                        pane_ttys.add(p["tty"])
                        if not p["dead"] and p["pid"] in procs:
                            alive += 1
        human = any(c["tty"] not in pane_ttys for c in clients)
        age = max(ages) if ages else None
        if human:
            verdict = "active"
        elif age is not None and age < min_age and alive:
            verdict = "young"
        else:
            verdict = "test_residue"
        out.append({"id": "srv:%s" % key, "kind": "srv", "verdict": verdict,
                    "human_attached": human, "age_s": age,
                    "servers": [s["sock"] for s in servers], "alive_panes": alive,
                    "steps": [("kill-server", s["sock"]) for s in servers],
                    "kill_pids": sorted(pids)})
    return out


def classify_orphans(snap):
    procs = snap["procs"]
    out = []
    for pid in snap["orphan_server_pids"]:
        if pid not in procs:
            continue
        kids = descendants(procs, pid)
        out.append({"id": "orphan:%d" % pid, "kind": "orphan", "verdict": "orphan_server",
                    "human_attached": False, "age_s": procs[pid]["age_s"],
                    "args": procs[pid]["args"][:80],
                    "steps": [], "kill_pids": sorted({pid} | kids)})
    covered = set()
    for s in snap["servers"]:
        covered |= pane_subtree(procs, s)
    servers = set(tmux_server_pids(procs))
    for pid, p in procs.items():
        # ★tmux server 自己的 argv 帶著它 exec 的腳本路徑(new-session ... exec ...
        #   pane-left-tabs.sh),ppid 也是 1——不排除會把每台 server 都當孤兒腳本。
        if pid in servers or _is_tmux(p):
            continue
        if p["ppid"] == 1 and COCKPIT_SCRIPT_RE.search(p["args"]) and pid not in covered:
            out.append({"id": "script:%d" % pid, "kind": "script",
                        "verdict": "orphan_script", "human_attached": False,
                        "age_s": p["age_s"], "args": p["args"][:80],
                        "steps": [], "kill_pids": sorted({pid} | descendants(procs, pid))})
    return out


def _tty_idle(tty, now):
    if not tty or tty == "??":
        return None
    try:
        return int(now - os.stat("/dev/" + tty).st_atime)
    except OSError:
        return None


def classify_stray_cli(snap, policy):
    threshold = _pol_int(policy, "idle_threshold_s", 3600)
    procs = snap["procs"]
    covered = set()
    for s in snap["servers"]:
        covered |= pane_subtree(procs, s)
    for pid in snap["orphan_server_pids"]:
        covered |= descendants(procs, pid)
    out = []
    for pid, p in sorted(procs.items()):
        if pid in covered or not CLI_RE.search(p["args"]) or p["tty"] == "??":
            continue
        # 只認 CLI 本體(父不是同名 CLI):子 helper 行程跟著本體走
        parent = procs.get(p["ppid"])
        if parent and CLI_RE.search(parent["args"]) and parent["tty"] == p["tty"]:
            continue
        idle = _tty_idle(p["tty"], snap["now"])
        verdict = "stray_idle" if idle is not None and idle >= threshold else "stray_active"
        out.append({"id": "cli:%d" % pid, "kind": "cli", "verdict": verdict,
                    "human_attached": False, "age_s": p["age_s"], "tty": p["tty"],
                    "idle_s": idle, "args": p["args"][:80],
                    "steps": [], "kill_pids": sorted({pid} | descendants(procs, pid))})
    return out


def classify(snap, policy):
    targets = []
    for inst in snap["instances"]:
        tgt = classify_instance(snap, inst, policy)
        if tgt:
            targets.append(tgt)
    targets += classify_ephemeral(snap, policy)
    targets += classify_orphans(snap)
    targets += classify_stray_cli(snap, policy)
    return targets


# ---------------------------------------------------------------- 安全名單
def load_policy(path=POLICY_PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            pol = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise SystemExit("idle-policy unreadable: %s (%s)" % (path, e))
    if not isinstance(pol, dict) or pol.get("schema") != "aib-idle-policy/v1":
        raise SystemExit("idle-policy schema mismatch: %s" % path)
    return pol


def auto_decision(target, policy):
    """回 (可自動收?, 原因)。名單只會縮小,不會放大:cli 永不、unknown 永不、
    有人接著永不、protect 命中永不、類別沒開永不。"""
    v = target["verdict"]
    if target["kind"] == "cli":
        return False, "cli_never_auto"
    if target.get("human_attached"):
        return False, "human_attached"
    if v not in AUTO_CLASSES:
        return False, "verdict:%s" % v
    # ★自動路徑的硬地板(08-17 真機 e2e 實錯):名單 min_age_s 設 0 測自己的治具,
    #   同一輪把別的 session 正在跑的 test-eye-ui-launch 治具 server 也收了。
    #   剛出生的東西沒有「無人接」的意義——測試治具本來就沒有人接。名單可以
    #   放寬到 60s,不能再低;人工 close 不受此限(人看得到那是什麼)。
    if target.get("age_s") is None or target["age_s"] < AUTO_MIN_AGE_FLOOR_S:
        return False, "too_young_for_auto"
    if not (policy.get("auto_close") or {}).get(v, False):
        return False, "class_disabled:%s" % v
    prot = policy.get("protect") or {}
    if target["kind"] == "inst":
        if target["instance"] in (prot.get("instances") or []):
            return False, "protected_instance"
        if target.get("outer") in (prot.get("sessions") or []):
            return False, "protected_session"
        if set(target.get("roles") or []) & set(prot.get("roles") or []):
            return False, "protected_role"
    return True, "ok"


# ---------------------------------------------------------------- 執行
def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


ARGV_KEEP = 200


def argv_of(pid):
    """殺之前問一次這個 PID 現在是什麼(ps -o args=),回字串;查不到回 ""。
    為什麼:10-09 18:48 reaper 把 7019/7020 當孤兒伺服器殺了,事後只剩 PID,查不到它們是什麼
    (a seat 10-10 交接建議三)。紀錄只截前 ARGV_KEEP 字,夠認人、不塞爆 log。"""
    try:
        out = subprocess.run(["ps", "-o", "args=", "-p", str(int(pid))], capture_output=True,
                             text=True, timeout=5, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    return out.strip()[:ARGV_KEEP]


DRAFT_TAIL_LINES = 40
DRAFTS_DIR = os.environ.get("EZ2_AIB_IDLE_DRAFTS") or os.path.join(AIB_MAIN_HOME, "pending")


def save_pane_tails(target, out_dir=None, now=None):
    """收艙前把要收的每個 tmux 伺服器裡每個窗格的最後 DRAFT_TAIL_LINES 行存成一個檔(10-12 a seat):
    10-11 關 eda4 艙時,6 席輸入框裡打好沒送出的字跟著消失,找不回來。輸入框就在窗格最底下,存尾巴就存到了。
    只讀(list-panes / capture-pane),不動任何東西。回檔案路徑;沒有窗格或存不了 ⇒ None(不擋收艙,呼叫端照常收)。"""
    socks = [st[1] for st in target.get("steps") or [] if st[0] == "kill-server"]
    chunks = []
    for sock in socks:
        out = _run([TMUX, "-S", sock, "list-panes", "-a", "-F",
                    "#{pane_id}\t#{session_name}\t#{window_id}\t#{window_name}"], timeout=5)
        if out is None:
            continue
        for ln in out.splitlines():
            parts = ln.split("\t")
            if len(parts) != 4:
                continue
            text = _run([TMUX, "-S", sock, "capture-pane", "-p", "-t", parts[0], "-S", "-%d" % DRAFT_TAIL_LINES],
                         timeout=5)
            if text is None:
                continue
            body = "\n".join(text.rstrip("\n").splitlines()[-DRAFT_TAIL_LINES:])
            chunks.append("===== %s %s %s(%s)\n%s\n" % (sock.rsplit("/", 1)[-1], parts[2], parts[3], parts[1], body))
    if not chunks:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(target.get("id") or "target"))
    base = os.path.join(out_dir or DRAFTS_DIR, "CLOSED-PANES-%s-%s" % (safe, stamp))
    try:
        os.makedirs(os.path.dirname(base), exist_ok=True)
        for n in range(100):                      # 同一秒同名(不同目標清洗後撞名)⇒ 加 -N,不覆寫(審查)
            path = base + ("-%d" % n if n else "") + ".txt"
            try:                                  # 一建立就是 0600:輸入框可能有人打到一半的東西,只給自己看(審查:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # 先寫再 chmod 有一段可被別人讀)
            except FileExistsError:
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("".join(chunks))
            return path
    except OSError:
        return None
    return None


def execute(target, dry_run=False, grace_s=1.5):
    """回 dict(steps_run, killed, survivors, argv, drafts)。只碰 target 自己盤點到的 PID。
    argv:{"<pid>": "<殺之前量到的指令列>"},dry-run 也量(讓 --dry-run 的紀錄說得出會殺到什麼)。
    drafts:真的收之前存下的窗格尾巴檔(save_pane_tails);dry-run 不存。"""
    ran, killed, argv = [], [], {}
    drafts = None if dry_run else save_pane_tails(target)
    for step in target["steps"]:
        if step[0] == "kill-server":
            cmd = [TMUX, "-S", step[1], "kill-server"]
        else:
            cmd = [TMUX, "-S", step[1], "kill-session", "-t", step[2]]
        ran.append(" ".join(shlex.quote(c) for c in cmd))
        if not dry_run:
            subprocess.run(cmd, capture_output=True, timeout=10)
    if not dry_run and target["steps"]:
        time.sleep(grace_s)
    for pid in target["kill_pids"]:
        if pid <= 1:
            continue                              # 紅線:絕不碰 0/1/負數
        if not _pid_alive(pid):
            continue
        argv[str(pid)] = argv_of(pid)             # 先記它是什麼,再動手(dry-run 也記)
        if dry_run:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except (ProcessLookupError, PermissionError):
            pass
    survivors = []
    if not dry_run and target["kill_pids"]:
        time.sleep(grace_s)
        survivors = [p for p in target["kill_pids"] if p > 1 and _pid_alive(p)]
    return {"steps_run": ran, "killed": killed, "survivors": survivors, "argv": argv, "drafts": drafts}


def log_line(record, path=LOG_PATH):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- 顯示
def _product_version():
    """經 aib 啟動時吃 EZ2_AIB_VERSION;獨立執行時退回讀 repo 的 VERSION 檔。"""
    v = os.environ.get("EZ2_AIB_VERSION")
    if v:
        return v
    try:
        with open(os.path.join(HERE, "..", "..", "VERSION"), encoding="utf-8") as fh:
            return fh.read().strip() or "?"
    except OSError:
        return "?"


def _fmt_age(s):
    if s is None:
        return "-"
    if s < 3600:
        return "%dm" % (s // 60)
    if s < 86400:
        return "%.1fh" % (s / 3600)
    return "%.1fd" % (s / 86400)


def _detail(tg):
    if tg["kind"] == "inst":
        health = tg.get("cabin_health")
        live = [s for s in tg["seats"] if s["alive"]]
        # 量不到畫「?」不畫 0m:None 是「沒有訊號」,不是「剛剛動過」。
        seat_txt = ",".join(
            "%s(%s)" % (s["role"] or s["name"] or s["window"],
                        "?" if s["idle_s"] is None else _fmt_age(s["idle_s"]))
            for s in live) or "-"
        return "%s | health=%s | scripts=%d(cpu %s) | %s" % (
            tg["outer"] or "-", health or "-", len(tg["scripts"]),
            _fmt_age(tg["script_cpu_s"]), seat_txt)
    if tg["kind"] == "srv":
        return "%d server, %d live pane" % (len(tg["servers"]), tg["alive_panes"])
    if tg["kind"] == "cli":
        return "%s idle=%s %s" % (tg["tty"], _fmt_age(tg.get("idle_s")), tg["args"])
    return tg.get("args", "")


def render_table(targets, policy):
    lines = [t("aib.idle.title", version=_product_version(), n=len(targets))]
    # 目標 ID 不截斷:它就是 `aib idle close <target>` 要貼的那一串。
    width = max([12] + [len(tg["id"]) for tg in targets])
    lines.append("  %-*s %-14s %-6s %-5s %s" % (
        width, t("aib.idle.col.target"), t("aib.idle.col.verdict"), t("aib.idle.col.age"),
        t("aib.idle.col.auto"), t("aib.idle.col.detail")))
    for tg in targets:
        ok, why = auto_decision(tg, policy)
        lines.append("  %-*s %-14s %-6s %-5s %s" % (
            width, tg["id"], t("aib.idle.verdict." + tg["verdict"]), _fmt_age(tg["age_s"]),
            "yes" if ok else "no", _detail(tg)))
    lines.append(t("aib.idle.footer"))
    return "\n".join(lines)


# ---------------------------------------------------------------- 艙健康(§88 包 10)
CABIN_HEALTH_UNMEASURABLE = "unmeasurable"


def cabin_health_rows():
    """half_cabin_arm 的四判準逐艙列;量不到(socket 目錄不存在等)回 None,不回空 list.

    ★ 空 list 與「量不到」在下游同形(每艙都變 unmeasurable 看起來像「都沒量」),
      所以 None 另走一條:呼叫端印一句具名告警.判準一個字都不在這裡,全在 half_cabin_arm.scan.
    """
    try:
        return half_cabin_arm.scan(half_cabin_arm.Path(std_sock_dir()),
                                   half_cabin_arm.Path(os.path.join(AIB_MAIN_HOME, "instances")))
    except (half_cabin_arm.InputError, OSError):
        return None


def annotate_cabin_health(targets, rows):
    """每個 inst 目標加 cabin_health;rows 為 None 一律 unmeasurable.回被標的目標數."""
    by_id = half_cabin_arm.health_by_id(rows) if rows is not None else {}
    n = 0
    for tg in targets:
        if tg["kind"] != "inst":
            continue
        tg["cabin_health"] = by_id.get(tg["instance"], CABIN_HEALTH_UNMEASURABLE)
        n += 1
    return n


SWEEP_DEAD_LOG_NAME = "idle-sweep-dead.jsonl"


def sweep_dead_log_path():
    """留痕落本檔的狀態根 AIB_MAIN_HOME(EZ2_AIB_MAIN_HOME > EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib),
    與 policy / 日誌 / 鎖同一個根,不自己讀艙名.

    ★ 不 import seats_dir_lib 來取狀態根:tests/test-seats-dir-resolution.py 的債表把「有沒有
      import seats_dir_lib」當「席位目錄改走 HUB 導出了」的訊號,本檔的席位目錄仍是手組的
      (classify_instance),一 import 就讓那把尺誤判成已修而紅.根的鏈與 seats_dir_lib.state_root 同序.
    """
    return os.path.join(AIB_MAIN_HOME, SWEEP_DEAD_LOG_NAME)


def plan_sweep_dead(rows, now=None):
    """從 half_cabin_arm 的列算出「這一輪會動什麼」.回 list of dict(每艙一筆,含 action 與 why).

    只有 verdict == dead 的列會進 rm 候選;其餘列**不進本函式的輸出**(它們是 scan/reap 的事).
    四條紅線在 dead 列上的對應:
      · 艙齡 <60s 不動(socket mtime 起算;量不到也不動)
      · 外層 session 還在 ⇒ 不動(那是 inst 目標,走 aib idle close / reap 的 dead_inner)
      · tmux 找不到 ⇒ 「server 打不到」不可信,整輪 unmeasurable,一個都不動
      · 只 rm 這一輪列出來的那條路徑,不掃通配
    """
    now = time.time() if now is None else now
    tmux_ok = shutil.which("tmux") is not None
    dead = [r for r in rows if r["verdict"] == half_cabin_arm.DEAD]
    plan = []
    for r in dead:
        sock = r["socket"]
        age = half_cabin_arm.socket_age_s(half_cabin_arm.Path(sock), now)
        outer_name = r["outer_session"][1]
        outer_alive = r["outer_session"][0] == half_cabin_arm.OK
        if not tmux_ok:
            action, why = "skip", "tmux_missing_unmeasurable"
        elif age is None or age < AUTO_MIN_AGE_FLOOR_S:
            action, why = "skip", "too_young_for_auto"
        elif outer_alive:
            action, why = "skip", "outer_session_alive"
        else:
            action, why = "rm", "ok"
        plan.append({"id": r["id"], "socket": sock, "age_s": age, "outer_session": outer_name,
                     "outer_alive": outer_alive,
                     "criteria": {k: {"status": r[k][0], "detail": r[k][1]}
                                  for k in ("socket_perm", "outer_session", "seats_dir", "ai_child")},
                     "action": action, "why": why})
    return plan


def cmd_sweep_dead(args, policy):
    rows = cabin_health_rows()
    if rows is None:
        print(t("aib.idle.sweep_dead.unmeasurable", path=std_sock_dir()), file=sys.stderr)
        return 2
    plan = plan_sweep_dead(rows)
    apply = bool(args.apply)
    log_path = sweep_dead_log_path()
    removed = skipped = 0
    for item in plan:
        record = dict(item, ts=_utc_iso(int(time.time() * 1000)), apply=apply)
        if item["action"] == "rm":
            if apply:
                try:
                    os.unlink(item["socket"])
                    record["result"] = "removed" if not os.path.lexists(item["socket"]) else "still_there"
                except OSError as e:
                    record["result"] = "unlink_failed:%s" % e
                removed += record["result"] == "removed"
                print(t("aib.idle.sweep_dead.removed", id=item["id"], path=item["socket"],
                        session=item["outer_session"], status=record["result"]))
            else:
                record["result"] = "dry-run"
                print(t("aib.idle.sweep_dead.would_remove", id=item["id"], path=item["socket"],
                        v4=_fmt_age(item["age_s"]), session=item["outer_session"]))
        else:
            skipped += 1
            record["result"] = "skipped"
            print(t("aib.idle.sweep_dead.skipped", id=item["id"], path=item["socket"], why=item["why"]))
        log_line(record, path=log_path)
    print(t("aib.idle.sweep_dead.summary", n=len(plan), v1=sum(1 for i in plan if i["action"] == "rm"),
            v2=removed, v3=skipped, mode="apply" if apply else "dry-run", path=log_path))
    return 0


# ---------------------------------------------------------------- 子命令
def cmd_scan(args, policy):
    snap = collect_snapshot()
    targets = classify(snap, policy)
    health = cabin_health_rows()
    if health is None:
        print(t("aib.idle.sweep_dead.unmeasurable", path=std_sock_dir()), file=sys.stderr)
    annotate_cabin_health(targets, health)
    if args.json:
        slim = [{k: v for k, v in tg.items() if k not in ("steps",)} for tg in targets]
        for tg, s in zip(targets, slim):
            s["auto"], s["auto_reason"] = auto_decision(tg, policy)
        print(json.dumps(slim, ensure_ascii=False, indent=1))
    else:
        print(render_table(targets, policy))
    return 0


def cmd_close(args, policy):
    snap = collect_snapshot()
    targets = {tg["id"]: tg for tg in classify(snap, policy)}
    tg = targets.get(args.target)
    if not tg:
        print(t("aib.idle.close.not_found", target=args.target), file=sys.stderr)
        return 2
    if tg["human_attached"] and not args.force:
        print(t("aib.idle.close.refused_attached", target=args.target), file=sys.stderr)
        log_line({"ts": int(time.time()), "mode": "manual", "target": tg["id"],
                  "verdict": tg["verdict"], "action": "refused", "reason": "human_attached"})
        return 3
    print(t("aib.idle.close.plan", target=tg["id"], verdict=t("aib.idle.verdict." + tg["verdict"])))
    for step in tg["steps"]:
        print("    tmux -S %s %s" % (step[1], " ".join(step[0:1] + tuple(step[2:]))))
    print("    kill (TERM): %s" % (" ".join(str(p) for p in tg["kill_pids"]) or "-"))
    if not args.yes:
        try:
            ans = input(t("aib.idle.close.confirm", target=tg["id"]))
        except EOFError:
            ans = ""
        if ans.strip().lower() not in ("y", "yes"):
            print(t("aib.idle.close.aborted"))
            log_line({"ts": int(time.time()), "mode": "manual", "target": tg["id"],
                      "verdict": tg["verdict"], "action": "aborted", "reason": "user"})
            return 0
    res = execute(tg, dry_run=args.dry_run)
    log_line({"ts": int(time.time()), "mode": "dry" if args.dry_run else "manual",
              "target": tg["id"], "verdict": tg["verdict"], "action": "closed",
              "forced": bool(args.force), **res})
    print(t("aib.idle.close.done", target=tg["id"], n=len(res["killed"]),
            survivors=len(res["survivors"])))
    if res.get("drafts"):
        print(t("aib.idle.close.drafts_saved", path=res["drafts"]))
    return 0


def reap_once(policy, dry_run=False, out=sys.stdout):
    snap = collect_snapshot()
    targets = classify(snap, policy)
    closed = 0
    for tg in targets:
        ok, why = auto_decision(tg, policy)
        if not ok:
            log_line({"ts": int(time.time()), "mode": "auto", "target": tg["id"],
                      "verdict": tg["verdict"], "action": "skipped", "reason": why})
            continue
        res = execute(tg, dry_run=dry_run)
        closed += 1
        log_line({"ts": int(time.time()), "mode": "dry" if dry_run else "auto",
                  "target": tg["id"], "verdict": tg["verdict"], "action": "closed",
                  "reason": why, **res})
        print(t("aib.idle.reap.closed", target=tg["id"],
                verdict=t("aib.idle.verdict." + tg["verdict"]),
                n=len(res["killed"]) if not dry_run else len(tg["kill_pids"]),
                dry=" (dry-run)" if dry_run else ""), file=out)
    if not closed:
        print(t("aib.idle.reap.none", n=len(targets)), file=out)
    return len(targets), closed


def cmd_reap(args, policy):
    if policy is None:
        print(t("aib.idle.policy.missing", path=POLICY_PATH), file=sys.stderr)
        return 2
    reap_once(policy, dry_run=args.dry_run)
    return 0


def _try_lock():
    """全機單飛鎖:多艙各有一個 watch 窗,同一輪只准一個真的 reap。
    拿不到=別艙的 watch 正在收,本輪只掃不收。回 fd 或 None。"""
    import fcntl
    try:
        os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
        fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        return None


def watch_round(policy, dry_run=False, out=sys.stdout):
    """一輪:重繪儀表板(scan 表)+ 依名單收(有名單且拿到鎖才收)。
    回 (targets, closed, mode) mode∈{'reap','dry','scan_only','locked'}。"""
    snap = collect_snapshot()
    targets = classify(snap, policy or DEFAULT_POLICY)
    out.write("\033[H\033[J")
    out.write(time.strftime("%Y-%m-%d %H:%M:%S") + "  " + render_table(targets, policy or DEFAULT_POLICY) + "\n")
    # ED.13:每輪順手回填本艙缺 uuid 的席。放在名單/鎖之前:回填不依賴收艙名單
    # (policy None 也做),也不該被別艙的 reap 鎖擋住;失敗不得讓這一輪的收艙泡湯。
    try:
        uuid_backfill_tick(out)
    except Exception as exc:                      # noqa: BLE001
        out.write("aib idle watch: 本輪 uuid 回填失敗(仍存活):%s\n" % exc)
    try:
        exit_sweep_tick(out)
    except Exception as exc:                      # noqa: BLE001
        out.write("aib idle watch: 本輪退出痕補記失敗(仍存活):%s\n" % exc)
    try:
        cabin_snapshot_tick()
    except Exception as exc:                      # noqa: BLE001
        out.write("aib idle watch: 本輪艙快照失敗(仍存活):%s\n" % exc)
    if policy is None:
        out.write(t("aib.idle.watch.scan_only", path=POLICY_PATH) + "\n")
        out.flush()
        return targets, 0, "scan_only"
    fd = _try_lock()
    if fd is None:
        out.write(t("aib.idle.watch.locked") + "\n")
        out.flush()
        return targets, 0, "locked"
    try:
        closed = 0
        for tg in targets:
            ok, why = auto_decision(tg, policy)
            if not ok:
                log_line({"ts": int(time.time()), "mode": "auto", "target": tg["id"],
                          "verdict": tg["verdict"], "action": "skipped", "reason": why})
                continue
            res = execute(tg, dry_run=dry_run)
            closed += 1
            log_line({"ts": int(time.time()), "mode": "dry" if dry_run else "auto",
                      "target": tg["id"], "verdict": tg["verdict"], "action": "closed",
                      "reason": why, **res})
            out.write(t("aib.idle.reap.closed", target=tg["id"],
                        verdict=t("aib.idle.verdict." + tg["verdict"]),
                        n=len(res["killed"]) if not dry_run else len(tg["kill_pids"]),
                        dry=" (dry-run)" if dry_run else "") + "\n")
    finally:
        os.close(fd)                       # 關 fd 即釋放 flock
    out.write(t("aib.idle.watch.tick", ts=time.strftime("%H:%M:%S"), n=len(targets),
                closed=closed) + "\n")
    out.flush()
    return targets, closed, "dry" if dry_run else "reap"


EXIT_SWEEP_DISABLE_ENV = "EZ2_AIB_EXIT_SWEEP"
EXIT_SWEEP_LOCK = "exit-sweep.lock"


def _unexited_seat_files(sdir):
    """席位檔裡還沒有 exited_at_epoch 的那幾份(讀不懂的不算,留給 seat_exit 自己報壞檔)。"""
    out = []
    for raw in sorted(glob.glob(os.path.join(sdir, "w*.json"))):
        try:
            with open(raw, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and "exited_at_epoch" not in rec:
            out.append(raw)
    return out


def cabin_snapshot_tick():
    """10-09 a seat:watch 每輪順手把「在線的艙與每艙的席」寫進這次開機的快照(aib restart 停電/重開機復原用)。
    按開機分檔,重開機後新艙的 watch 不會蓋掉停電前那一份;多艙的 watch 同一分鐘只寫一次;
    沒有在線的艙不寫。不印任何東西(沒事不刷屏)。關閉:EZ2_AIB_RESTART_SNAPSHOT=0。回寫入路徑或 None。"""
    import cabin_restart
    if os.environ.get(cabin_restart.SNAPSHOT_DISABLE_ENV) == "0":
        return None
    return cabin_restart.write_snapshot()


def exit_sweep_tick(out=sys.stdout):
    """10-09 a seat(D2):watch 每輪順手替「窗已不在 / 行程已不在」的席補退出痕。

    病:`seat_exit.py record` 掛在 pane 指令尾巴,窗被 kill(含面板 [X])時它沒機會跑;
      全庫沒有任何地方自動呼叫 `seat_exit.py sweep`。於是死席的席位檔永遠看起來是活人。
      10-09 實量:15 個死艙 27 席沒有退出痕;任何「列出現有席位」的工具會把墓碑當人
      (歸隊腳本第一版照「未標退出」選人,列出 40 餘席要復活,版面與正確答案同形)。
    做什麼:
      ① 本艙:有未標退出的席才跑 sweep(本艙的 watch 管本艙,同 uuid_backfill_tick)。
      ② 死艙:hub 伺服器已不在的艙**沒有自己的 watch**,沒人會替它補 ⇒ 由活艙代補。
         多個活艙的 watch 可能同一輪都想代補 ⇒ 用一把非阻塞鎖,拿不到就這輪不做死艙。
         hub 還活著的別艙一律不碰(那是它自己 watch 的事;跨艙寫會撞同一輪)。
    ★ 判活沿用 seat_exit.sweep 自己的規則:窗不在 ⇒ window-gone;hub 打不到 ⇒ 退回看 pid,
      pid 還活著就**不**標(保守;pid 被重用只會少標不會多標)。
    ★ 便宜閘:所有席都已標退出的艙不開子行程;穩態一輪零 tmux 呼叫。
    ★ 畫面只在真的補了才印一行;沒補不印(同 09-05 那條紀律:「沒事」不得與「有事」同形地刷屏)。
    ★ 關閉:EZ2_AIB_EXIT_SWEEP=0。回 {艙尾四碼: 補了幾席} 或 None(沒做)。"""
    if os.environ.get(EXIT_SWEEP_DISABLE_ENV) == "0":
        return None
    hub = seat_uuid_backfill.hub_from_env()
    if not hub or not hub.startswith("aihub-"):
        return None                          # 主艙 `aihub` 不在 instances/ 下;不猜
    own = hub[len("aihub-"):]
    root = os.path.join(AIB_MAIN_HOME, "instances")
    jobs = []
    own_sdir = os.path.join(root, own, "seats")
    if os.path.isdir(own_sdir) and _unexited_seat_files(own_sdir):
        jobs.append((own, own_sdir))
    dead = []
    for sdir in sorted(glob.glob(os.path.join(root, "*", "seats"))):
        cab = os.path.basename(os.path.dirname(sdir))
        if cab == own or not _unexited_seat_files(sdir):
            continue
        if seat_exit.hub_windows("aihub-" + cab) is not None:
            continue                         # 活艙:歸它自己的 watch
        dead.append((cab, sdir))
    lock_fh = None
    if dead:
        import fcntl
        lock_dir = os.path.join(AIB_MAIN_HOME, "state")
        os.makedirs(lock_dir, exist_ok=True)
        lock_fh = open(os.path.join(lock_dir, EXIT_SWEEP_LOCK), "a")
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            jobs.extend(dead)
        except OSError:
            lock_fh.close()
            lock_fh = None                   # 別艙這一輪在代補;下一輪再說
    filled = {}
    try:
        import contextlib
        import io
        for cab, sdir in jobs:
            before = len(_unexited_seat_files(sdir))
            ns = argparse.Namespace(seats_dir=sdir, instance_home=None, hub="aihub-" + cab)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                seat_exit.sweep(ns)
            n = before - len(_unexited_seat_files(sdir))
            if n > 0:
                filled[cab[-4:]] = n
    finally:
        if lock_fh is not None:
            lock_fh.close()
    if filled:
        out.write("aib idle watch: 補記退出痕 " + "、".join(
            "%s×%d" % (k, v) for k, v in sorted(filled.items())) + "\n")
        out.flush()
    return filled


def uuid_backfill_tick(out=sys.stdout, now=None):
    """ED.13(2026-08-22):watch 每輪順手回填本艙缺 `session_uuid` 的席。

    為什麼掛在這裡:這是全機唯一一個「席活著就會一直醒來」的迴圈。開席尾段的
    `seat-detect --retry 5` 與 `aib <N>sits` 的 10 輪都有上限,過了上限那席
    就永遠缺 uuid(右欄戴 ❓),而 artifact 往往晚個一兩分鐘就有了
    (實測 agy 席 88 秒)。
    ★ 只回填**本艙**(hub socket → instance → 本檔 classify_instance 用的同一條
      AIB_MAIN_HOME/instances/<N>/seats 路徑,與收艙看的是同一份登記簿):每艙
      各有一支 watch,跨艙寫會撞上別艙 watch 的同一輪;沒有 HUB 就不猜、不做。
      不走 seats_dir_lib 的理由:它以 HOME 導出,而本檔全部以 AIB_MAIN_HOME
      (EZ2_AIB_MAIN_HOME 可注入)導出——兩套在測試治具下會指到不同地方,
      tests/test-idle-reaper.py 只壓 EZ2_AIB_MAIN_HOME,走 HOME 會摸到活艙。
    ★ 不在 flock 之內:那把鎖是「全機同一輪只准一艙真的收艙」,回填只填
      自己艙的空欄、冪等,不該被別艙的 reap 擋住。
    ★ 便宜閘在 backfill_round 裡:沒有席缺 uuid 的那一輪零子行程。
    ★ 關閉:EZ2_AIB_UUID_BACKFILL=0(測試治具/人工停用);預設開。
    回 result dict 或 None(沒做)。"""
    if os.environ.get(seat_uuid_backfill.DISABLE_ENV) == "0":
        return None
    hub = seat_uuid_backfill.hub_from_env()
    if not hub:
        return None
    inst = hub[len("aihub-"):] if hub.startswith("aihub-") else ""
    if not inst:
        return None                          # 主艙 `aihub` 不在 instances/ 下;不猜
    sdir = os.path.join(AIB_MAIN_HOME, "instances", inst, "seats")
    if not os.path.isdir(sdir):
        return None
    res = seat_uuid_backfill.backfill_round(hub, sdir, now=now)
    line = seat_uuid_backfill.summarize(res)
    if line:
        out.write(line + "\n")
        out.flush()
    # 日誌只記「有動作」與「探測器沒跑完」:overdue 是持續狀態,每輪記一行會淹掉真事件;
    # 畫面(上一行)每輪都有,要看現況看畫面。
    # ★ 09-05 實錯:skipped(沒有席缺 uuid,零子行程)的那一輪 rc 也是 None,
    #   於是每 5 分鐘記一行 `detect_incomplete filled=[] pending=[]` ——
    #   「沒東西要跑」與「跑了沒跑完」在日誌上逐字同形。一天 288 行假警報,
    #   真的沒跑完那一次會被淹掉。skipped 一律不記(summarize 對它也回 None,同一條紀律)。
    if res.get("skipped"):
        return res
    if res.get("filled") or res.get("rc") is None:
        log_line({"ts": int(time.time()), "mode": "auto", "target": "uuid:%s" % hub,
                  "verdict": "uuid_backfill", "action": "filled" if res.get("filled") else "detect_incomplete",
                  "reason": line or "", "filled": [n for n, _, _ in res.get("filled", [])],
                  "pending": [n for n, _, _ in res.get("pending", [])],
                  "overdue": [n for n, _, _ in res.get("overdue", [])]})
    return res


def cmd_watch(args, policy):
    """常駐(lobby 窗):沒有名單也要活著——只掃不收,並把「為什麼不收」印在畫面上。
    節奏:預設 300s 一輪,下限 30s(這是收艙器,不能自己變成輪詢病灶)。
    每輪(watch_round 內)順手回填缺 uuid 的席(uuid_backfill_tick;ED.13)。"""
    interval = max(30, int(args.interval))
    rounds = 0
    # 艙的自動接回守衛(10-10 the maintainer 第 1 項):5 秒一拍的 daemon 執行緒,跟著 watch 活、跟著 watch 重生。
    # 起不來只印一行,不影響 watch;邏輯與開關見 cabin_guard.py 檔頭。
    try:
        import cabin_guard  # noqa: E402
        _g_ok, _g_why = cabin_guard.start_thread()
        print("aib idle watch: %s" % _g_why, file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print("aib idle watch: cabin-guard 沒起來(仍存活):%s" % exc, file=sys.stderr)
    # 記憶體暴衝守門(10-24 20:34 卡死實案):1 秒一拍、零 fork;N 艙只一條在掃(鎖)。邏輯與開關見 memguard.py 檔頭。
    try:
        import memguard  # noqa: E402
        _m_ok, _m_why = memguard.start_thread()
        print("aib idle watch: %s" % _m_why, file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print("aib idle watch: memguard 沒起來(仍存活):%s" % exc, file=sys.stderr)
    while True:
        # BL:碼換了就重跑自己(每輪頂端問一次,受 EZ2_IDLE_REEXEC_INTERVAL 節流)。
        # 放在 watch_round 之前:重跑掉的那一輪不會留心跳,而重跑後第一輪馬上補一個 ——
        # 中間空掉半輪比讓查驗器讀到「舊碼寫的心跳」誠實。
        reexec_if_changed()
        # W1:每輪都要留心跳,**包含掃描自己炸掉的那一輪**——一個活著但每輪都拋例外的
        # watch 與死掉的 watch 在畫面上一樣安靜,查驗器要分得出來(last_scan_rc)。
        scan_rc, mode, err = 0, None, None
        try:
            _targets, _closed, mode = watch_round(policy, dry_run=args.dry_run)
        except Exception as exc:                      # noqa: BLE001 常駐迴圈不得因一輪失敗而死
            scan_rc, err = 2, exc
            print("aib idle watch: 本輪掃描失敗(仍存活):%s" % exc, file=sys.stderr)
        rounds += 1
        ok, why = write_heartbeat(rounds, scan_rc, interval, mode=mode, error=err)
        if not ok:
            # 寫不進去不讓 watch 死,但要吵:查驗器接下來會判 stale(誤報 > 漏報)
            print("aib idle watch: 心跳寫入失敗:%s" % why, file=sys.stderr)
        if args.max_rounds and rounds >= args.max_rounds:
            return 0
        # BL:睡覺切片。整段睡下去的話,300 秒一輪的 watch 最慢要五分鐘才吃到新碼,
        # 而**沒人看的那幾艙正是最需要自己吃到新碼的**(BF-2 在面板那側踩過同一格)。
        # 切片只改「多久問一次碼變了沒」,**不改輪次節拍**:interval 一秒都沒少。
        slept = 0.0
        while slept < interval:
            reexec_if_changed()            # 真的重跑就不會回來
            step = min(float(max(1, _reexec_interval())), interval - slept)
            time.sleep(step)
            slept += step
        policy = load_policy()             # 名單可在常駐期間 init/修改,每輪重讀


def cmd_watch_status(args):
    """`aib idle watch-status`:watch 還活著嗎。rc 0 新鮮 / 3 已失效 / 2 自己驗自己被拒。

    ★ 這支**故意不接 policy**:它回答的是「監控本身在不在」,與收艙名單無關;
      吃了名單就會多一條「名單壞掉 → 查驗器也壞掉」的耦合,而那正是它該獨立於的東西。"""
    st = watch_status(path=args.path, stale_s=args.stale_s)
    if args.json:
        print(json.dumps(st, ensure_ascii=False, sort_keys=True))
        return st["rc"]
    hb = st["heartbeat"] or {}
    if st["rc"] == 2:
        print("aib idle watch-status: 拒絕自己驗自己(本行程就是心跳裡的 pid %s)——"
              "請從別的窗跑" % hb.get("pid"), file=sys.stderr)
        return 2
    if st["ok"]:
        print("watch 存活:instance=%s pid=%s 第 %s 輪,%.0fs 前(門檻 %ss)"
              % (hb.get("instance"), hb.get("pid"), hb.get("round"),
                 st.get("age_s", 0), st["stale_s"]))
        return 0
    print("aib idle watch-status: 監控已失效 —— %s(心跳 %s)"
          % (", ".join(st["reasons"]), args.path or HEARTBEAT_PATH), file=sys.stderr)
    return 3


def cmd_policy(args, policy):
    if args.action == "init":
        if os.path.exists(POLICY_PATH) and not args.force:
            print(t("aib.idle.policy.exists", path=POLICY_PATH), file=sys.stderr)
            return 2
        os.makedirs(os.path.dirname(POLICY_PATH), exist_ok=True)
        with open(POLICY_PATH, "w", encoding="utf-8") as fh:
            json.dump(DEFAULT_POLICY, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print(t("aib.idle.policy.written", path=POLICY_PATH))
        return 0
    if policy is None:
        print(t("aib.idle.policy.missing", path=POLICY_PATH), file=sys.stderr)
        return 2
    print(json.dumps(policy, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aib idle", add_help=True)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("scan"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("close")
    p.add_argument("target"); p.add_argument("--yes", "-y", action="store_true")
    p.add_argument("--force", action="store_true"); p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("reap"); p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("watch"); p.add_argument("--interval", default=300, type=int)
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--max-rounds", default=0, type=int)
    p = sub.add_parser("watch-status")
    p.add_argument("--json", action="store_true")
    p.add_argument("--stale-s", type=int, default=None,
                   help="逾時秒數(預設=心跳自帶 interval_s × %d)" % HEARTBEAT_STALE_ROUNDS)
    p.add_argument("--path", default=None, help="心跳檔路徑(預設 $EZ2_AIB_WATCH_HEARTBEAT)")
    p = sub.add_parser("policy"); p.add_argument("action", nargs="?", default="show",
                                                 choices=["show", "init"])
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("sweep-dead"); p.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd is None:
        args.cmd, args.json = "scan", False
    policy = load_policy()
    effective = policy if policy is not None else DEFAULT_POLICY
    if args.cmd == "scan":
        return cmd_scan(args, effective)
    if args.cmd == "close":
        return cmd_close(args, effective)
    if args.cmd == "reap":
        return cmd_reap(args, policy)
    if args.cmd == "watch":
        return cmd_watch(args, policy)
    if args.cmd == "watch-status":
        return cmd_watch_status(args)
    if args.cmd == "policy":
        return cmd_policy(args, policy)
    if args.cmd == "sweep-dead":
        return cmd_sweep_dead(args, effective)
    ap.print_usage()
    return 2


if __name__ == "__main__":
    sys.exit(main())
