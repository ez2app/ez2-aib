#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib seat detect — 席位 session_uuid 程式偵測(ED.13,2026-08-14 the maintainer 直令)。

裁決正本:「不管是手開、你開的,都應該自動程式偵測,不是靠 AI 記憶」。
偵測鏈全部走作業系統可量測事實,零 AI 參與、零自報:

  tmux 活 pane_pid(席位檔存的 pid 會因 respawn 過期,一律現查)
    → pgrep -P 走子孫樹
      → 探針一:ps eww 掃行程 env 的 SESSION/THREAD/CONVERSATION 鍵
      → 探針二(codex):lsof 開檔中的 rollout-*.jsonl,讀 meta 首行
        payload.session_id——**邏輯 id 才是 ctx 收據鍵**;檔名 uuid 是
        thread id,fork/resume 結構下用檔名或時間戳猜必錯(08-14 實錯)
      → 探針五(codex 0.158.0 互動介面,09-29):lsof 落空時改認 state_5.sqlite 內容
        (cwd+開席初始化句+建立時間窗,恰一份才認領)
      → 探針三(agy):lsof ~/.gemini/antigravity-cli/presence/<uuid>.lock
      → 探針四(claude):CLI 無持久開檔無 env uuid,退 transcript 目錄
        建檔時間窗唯一綁定——候選不唯一=誠實不寫(rc 3),絕不猜
      → 探針五(codex,2026-09-26):**被動**讀 rollout meta 首行,不靠 lsof。
        理由是量出來的:codex 0.157.1 append 完就關檔,`lsof -p` 對它零命中
        (探針二因此永遠 none),而檔就在磁碟上。綁定判準與探針四同一支純函式
        `unique_birth_candidate`:meta 的 `cwd` 要等於席位的 cwd、meta 時戳要
        落在席位 started±150s、且**候選唯一**。
        ★ 取的是 `payload.session_id`(邏輯 id),**不是檔名那個 uuid** ——
          檔名是 thread id,fork/resume 下兩者不同,拿檔名猜 08-14 實錯過。

驗證史(08-14 夜):codex 四席+agy 兩席探針值與各席自報/收據逐一相符;
w10(agy pm)連席位 AI 自己都在 env 找不到,探針量出且 CTX 即亮。

CLI:
  seat-detect.py [--all | --window @N] [--apply] [--retry N]
  rc:0=目標席全數已解析(--apply 時=已回填);2=用法/環境錯誤;
     3=有席未解析/候選不唯一/與既存值不一致(不一致絕不覆寫,只報)
死窗席位檔=對帳報告只報不刪(裁決:清理是人的決定)。
"""

import argparse
import glob
import json
import os
import shutil
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import atomic_json                                # noqa: E402  席位檔多寫者,與 aib 同一把鎖
import seat_address                              # noqa: E402  合法位址導出單源(B15)
import seats_dir_lib                              # noqa: E402  席位目錄單源(EH.2/ED.1)
from activity_probe import escape_cwd             # noqa: E402  cwd→projects 目錄名單源

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ENV_KEYS = ("SESSION", "THREAD", "CONVERSATION")
BIRTH_WINDOW_S = 150   # claude transcript 建檔時間窗(探針四);codex 探針五沿用同一個窗
CODEX_SCAN_CAP = 500   # 探針五一輪最多讀幾個 meta 首行;撞到上限要出聲,不可靜靜截斷
# ED.13 契約(qa 判準 tests/test-ed13-uuid-pending.py,艦長 12:35 登記格):
#   「新開任一引擎席,席位檔必有 session_uuid,否則必有 session_uuid_pending: <reason>」
#   兩鍵**互斥**:量到就填 uuid 並清 pending;量不到就寫 pending 理由、絕不碰 uuid。
PENDING_KEY = "session_uuid_pending"


# ---- 純函式探針(單元測試對象;不碰行程/檔案系統)-------------------------

def parse_rollout_session_id(first_line):
    """codex rollout meta 首行 → 邏輯 session_id;壞行/缺欄=None。"""
    try:
        meta = json.loads(first_line)
    except (ValueError, TypeError):
        return None
    payload = meta.get("payload")
    if not isinstance(payload, dict):
        return None
    sid = payload.get("session_id")
    return sid if isinstance(sid, str) and UUID_RE.fullmatch(sid) else None


def parse_rollout_meta(first_line):
    """codex rollout meta 首行 → (session_id, cwd, ts_epoch);任一欄不成立=None。

    session_id 走 `parse_rollout_session_id`(同一支,不另寫第二個解析)。
    """
    import datetime
    sid = parse_rollout_session_id(first_line)
    if not sid:
        return None
    try:
        meta = json.loads(first_line)
    except (ValueError, TypeError):
        return None
    payload = meta.get("payload") if isinstance(meta, dict) else None
    if not isinstance(payload, dict):
        return None
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return None
    raw = payload.get("timestamp") or meta.get("timestamp")
    try:
        epoch = datetime.datetime.fromisoformat(
            str(raw).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None
    return sid, cwd, epoch


def codex_window_candidate(rows, started_epoch, cwd, window_s=BIRTH_WINDOW_S,
                           complete=False):
    """探針五的判準(純函式)。rows=[(sid, cwd, ts_epoch)]。

    回 (uuid|None, reason)。reason 一律非空:量不到的**理由**要看得見,
    「沒有候選」與「候選不唯一」是兩件事,混成一句下一棒就查錯方向。

    ★ `complete=False`(採集沒看完)一律不解析,而且擋在**判準這一層**不是呼叫端:
      唯一性是**全稱結論**,在沒看完的清單上成立不代表在全集上成立。
      qa a seat 2026-09-26 實證:第 501 份裡的第二候選會讓「唯一」變成一個
      **錯的身份**,而錯身份長得跟對的一模一樣。原本這一格寫在呼叫端、排在
      `if u` 之後 ⇒ 先回 resolved 就再也走不到它。判斷順序寫錯一次就是一個錯身份,
      所以改成參數。
    ★ 預設 `False`(qa a seat 17:2x 覆核指出:原本預設 True,而我在這裡寫「漏傳也
      更嚴」—— 那句是錯的,漏傳反而是寬的那一邊)。預設值一律站在嚴的那一側:
      **失效值不得等於表示安全的那個值**;漏傳的後果是「一直量不到」並且理由
      逐字說得出來(吵、看得見),不是一個看起來正常的身份(靜、致命)。
    """
    if not complete:
        return None, "codex 對話檔沒有掃完,唯一性是全稱結論、證不了,誠實不寫"
    if not isinstance(cwd, str) or not cwd:
        return None, "席位檔沒有 cwd,codex 對話檔無從比對,誠實不寫"
    same_cwd = [(ts, sid) for sid, c, ts in rows if c == cwd]
    if not same_cwd:
        return None, ("codex 對話檔裡沒有 cwd=%s 的候選,誠實不寫" % cwd)
    hit = unique_birth_candidate(same_cwd, started_epoch, window_s)
    if hit:
        return hit, ""
    inwin = [sid for ts, sid in same_cwd if abs(ts - started_epoch) < window_s]
    if not inwin:
        return None, ("codex 對話檔 %d 個候選都不在 started±%ds 內,誠實不寫"
                      % (len(same_cwd), window_s))
    return None, ("codex 對話檔在 started±%ds 內有 %d 個候選(不唯一),誠實不寫"
                  % (window_s, len(inwin)))


def uuid_from_lock_path(path):
    """agy presence lock 路徑 → uuid;非 lock 路徑=None。"""
    if "/antigravity-cli/presence/" not in path or not path.endswith(".lock"):
        return None
    m = UUID_RE.fullmatch(os.path.basename(path)[:-len(".lock")])
    return m.group(0) if m else None


_FOREIGN_ENV = ("GROK", "CLAUDE", "AGY", "GEMINI")


def _env_key_ok(name, provider):
    """Codex 席不收別的引擎留在環境裡的編號。"""
    if provider != "codex":
        return True
    upper = name.upper()
    if any(tok in upper for tok in _FOREIGN_ENV):
        return False
    return "CODEX" in upper


def uuid_from_env_text(ps_text, provider=None):
    """ps eww 輸出 → (uuid, 'env:鍵名') 或 None。"""
    for key in ENV_KEYS:
        for m in re.finditer(r"(\w*%s\w*)=(\S+)" % key, ps_text):
            if not _env_key_ok(m.group(1), provider):
                continue
            u = UUID_RE.search(m.group(2))
            if u:
                return u.group(0), "env:" + m.group(1)
    return None


def unique_birth_candidate(births, started_epoch, window_s=BIRTH_WINDOW_S):
    """[(birth_epoch, uuid)] 中落在 started±window 的唯一者;不唯一=None。

    唯一性是硬門檻:兩個 session 同窗開檔時寧可不綁——錯綁比空值更糟
    (右欄會拿別席的 CTX 當自己的,假資料比缺資料毒)。"""
    hits = [u for b, u in births if abs(b - started_epoch) < window_s]
    return hits[0] if len(hits) == 1 else None


# ---- 採集(impure;薄殼,不含判斷)---------------------------------------

def _run(argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True).stdout
    except FileNotFoundError:
        return ""


def _lsof_bin():
    """先看 PATH(治具可塞假 lsof);找不到再回退絕對路徑。
    macOS 真 lsof 在 /usr/sbin,B8 PATH 常只有 /usr/bin:/bin。"""
    which = shutil.which("lsof")
    if which:
        return which
    for cand in ("/usr/sbin/lsof", "/usr/bin/lsof"):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return "lsof"


def live_pane_pids(hub):
    out = {}
    text = _run(["env", "-u", "TMUX", "tmux", "-L", hub, "list-panes",
                 "-a", "-F", "#{window_id}|#{pane_pid}"])
    for line in text.splitlines():
        wid, _, pid = line.partition("|")
        if wid and pid:
            out[wid] = pid
    return out


def descendants(pid):
    out, todo = [], [str(pid)]
    while todo:
        kids = _run(["pgrep", "-P", todo.pop()]).split()
        out += kids
        todo += kids
    return out


def _rollout_sid_from_lsof(pid):
    for line in _run([_lsof_bin(), "-p", str(pid), "-Fn"]).splitlines():
        path = line[1:]
        if "/.codex/sessions/" in path and "rollout-" in path \
                and path.endswith(".jsonl"):
            try:
                with open(path, encoding="utf-8") as fh:
                    sid = parse_rollout_session_id(fh.readline())
            except OSError:
                sid = None
            if sid:
                return sid
    return None


def probe_process(pid, provider=None):
    """單一行程跑探針一~三;回 (uuid, source) 或 None。

    Codex 先讀自己正在開的對話檔。環境裡別的引擎的編號不算。
    """
    if provider == "codex":
        sid = _rollout_sid_from_lsof(pid)
        if sid:
            return sid, "lsof:codex-rollout-meta"
        hit = uuid_from_env_text(_run(["ps", "eww", "-p", str(pid)]), provider="codex")
        return hit
    hit = uuid_from_env_text(_run(["ps", "eww", "-p", str(pid)]))
    if hit:
        return hit
    for line in _run([_lsof_bin(), "-p", str(pid), "-Fn"]).splitlines():
        path = line[1:]
        u = uuid_from_lock_path(path)
        if u:
            return u, "lsof:agy-presence-lock"
        if "/.codex/sessions/" in path and "rollout-" in path \
                and path.endswith(".jsonl"):
            try:
                with open(path, encoding="utf-8") as fh:
                    sid = parse_rollout_session_id(fh.readline())
            except OSError:
                sid = None
            if sid:
                return sid, "lsof:codex-rollout-meta"
    return None


def claude_projects_dir():
    override = os.environ.get("EZ2_CLAUDE_PROJECTS_DIR")
    if override:
        return override
    root = os.environ.get("EZ2_AIB_ROOT") or os.getcwd()
    return os.path.join(os.path.expanduser("~"), ".claude", "projects",
                        escape_cwd(root))


def projects_dir_for_seat(rec):
    """探針四要掃的 transcript 目錄——**以席位檔的 cwd 為準**(EH.3)。

    ★ ED.13 回填(2026-08-22):原本只有一個全域目錄(EZ2_AIB_ROOT 導出),
      它只在「那一席的 cwd 剛好等於 repo 根」時才對。開席當下兩者通常相同
      (seats_batch 把 cwd 釘在 aib 那棵樹),但回填是**事後**跑的——
      從 `aib idle watch` 的 lobby 窗跑,它的 EZ2_AIB_ROOT 是整艙共用的那個,
      而席位各有各的 cwd。用錯目錄的失效形狀是「候選為空 → none → 永遠
      pending」,不報錯。
    ★ 測試的 EZ2_CLAUDE_PROJECTS_DIR 覆寫仍然最大(治具要能注入)。
    """
    override = os.environ.get("EZ2_CLAUDE_PROJECTS_DIR")
    if override:
        return override
    cwd = rec.get("cwd") if isinstance(rec, dict) else None
    if isinstance(cwd, str) and cwd:
        return os.path.join(os.path.expanduser("~"), ".claude", "projects",
                            escape_cwd(cwd))
    return claude_projects_dir()


def transcript_births(projects_dir):
    births = []
    for f in glob.glob(os.path.join(projects_dir, "*.jsonl")):
        m = UUID_RE.fullmatch(os.path.basename(f)[:-len(".jsonl")])
        if not m:
            continue
        st = os.stat(f)
        births.append((getattr(st, "st_birthtime", st.st_mtime), m.group(0)))
    return births


def codex_sessions_dir():
    """codex 對話檔根。與 activity_probe / usage_probe / codex_ctx_probe 同一對鍵,
    不在這裡發明第三種取法(治具用 EZ2_CODEX_SESSIONS_DIR 注入)。"""
    override = os.environ.get("EZ2_CODEX_SESSIONS_DIR")
    if override:
        return override
    home = os.environ.get("CODEX_HOME") or os.path.join(
        os.path.expanduser("~"), ".codex")
    return os.path.join(home, "sessions")


def codex_rollout_rows(started_epoch, sessions_dir=None, window_s=BIRTH_WINDOW_S,
                       cap=None):
    """探針五的採集(impure 薄殼)。回 (rows, truncated)。

    只翻 started 那一天與它前後一天的日期目錄(codex 的版面是 <Y>/<M>/<D>/),
    ★ 不是為了省時間才縮範圍,是為了讓「翻過哪些」講得出來:
      全域 glob 會隨歷史長大,而它變慢的那天不會有任何一格轉紅。
    撞到 cap 時回 truncated=True,由呼叫端把它寫成理由——**靜靜截斷會讓
    「沒有候選」與「沒看完」在席位檔裡長得一模一樣**。
    """
    # 預設值寫成 None 再現讀常數:寫 `cap=CODEX_SCAN_CAP` 會在 def 當下綁死,
    # 治具改了模組常數卻沒有作用 —— 那種測試會綠得很像真的。
    cap = CODEX_SCAN_CAP if cap is None else cap
    base = sessions_dir or codex_sessions_dir()
    days = set()
    for offset in (-86400, 0, 86400):
        lt = time.localtime(float(started_epoch) + offset)
        days.add(os.path.join(base, "%04d" % lt.tm_year, "%02d" % lt.tm_mon,
                              "%02d" % lt.tm_mday))
    paths = []
    for day in sorted(days):
        paths.extend(sorted(glob.glob(os.path.join(day, "rollout-*.jsonl"))))
    truncated = len(paths) > cap
    rows = []
    for path in paths[:cap]:
        try:
            with open(path, encoding="utf-8") as fh:
                meta = parse_rollout_meta(fh.readline())
        except OSError:
            continue
        if meta:
            rows.append((meta[0], meta[1], meta[2]))
    return rows, truncated


def seats_dir():
    """席位登記簿解析——**委派 seats_dir_lib(EH.2 單源;ED.1 狀態根可注入)**。

    ★ 2026-08-21 本檔曾自己寫一份(只認 env、沒有 HUB 導出那段),與 aib 對
      「我在哪個艙」給出不同答案,讓一席永遠拿不到身份、右欄永遠戴 ❓;反向更
      危險:化石檔沒有 uuid 時會把這艘艦隊偵測到的 uuid 寫進另一支艦隊的席位檔。
      那段歷史與判準留在 seats_dir_lib 的 docstring,這裡不再養第二份。
    ★ 2026-08-22 ED.1:HUB 導出的 instances/<N>/seats 也改掛在狀態根之下
      (EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib),注入根時不再退回真家目錄。
    """
    return seats_dir_lib.seats_dir()


# ---- 寫入(只此兩處;全部走 atomic_json,與 aib 種檔/心跳同一把目錄鎖)-----
# ★ 2026-08-22 前這裡是「讀→改→寫 `<檔>.tmp`→rename」:固定中繼檔名、無鎖、
#   無 fsync——正是 atomic_json.py 檔頭列的三個病,而席位檔是多寫者(艦長列
#   心跳每 5 秒寫一次)。回填從 watch 迴圈定期跑之後,撞寫的機率從「開席
#   那幾秒」變成「一直」,所以一併收進同一把鎖。

def _apply_uuid(path, uuid, source):
    """只填空欄;鎖內再判一次(鎖外那次判斷可能已經過期)。"""
    def mutate(rec):
        if not isinstance(rec, dict) or not rec.get("schema"):
            return rec
        if rec.get("session_uuid"):
            return rec                      # 別人先填了——不覆寫(紅線)
        rec["session_uuid"] = uuid
        rec["session_uuid_source"] = source
        rec.pop(PENDING_KEY, None)          # 互斥:有 uuid 就不是 pending
        facts = rec.get("uuid_detect") if isinstance(rec.get("uuid_detect"), dict) else {}
        facts["resolved_epoch"] = int(time.time())
        facts["last_status"] = "resolved"
        facts["last_reason"] = source
        rec["uuid_detect"] = facts
        return rec
    try:
        atomic_json.update_json(path, mutate)
    except atomic_json.AtomicWriteError as exc:
        print("seat-detect: 寫不進 %s:%s" % (os.path.basename(path), exc),
              file=sys.stderr)


def _note_attempt(path, status, reason):
    """量不到時:寫 session_uuid_pending=<理由>(契約鍵)+ 留痕 uuid_detect
    {attempts, first/last_epoch, last_status, last_reason}。只在 --apply。"""
    now = int(time.time())

    def mutate(rec):
        if not isinstance(rec, dict) or not rec.get("schema"):
            return rec
        if rec.get("session_uuid"):
            return rec                      # 已有 uuid 就不是「量不到」(互斥,P1c)
        rec[PENDING_KEY] = reason or status # 非空理由;seats_report/人讀得到「為什麼還沒有」
        facts = rec.get("uuid_detect") if isinstance(rec.get("uuid_detect"), dict) else {}
        facts["attempts"] = int(facts.get("attempts") or 0) + 1
        facts.setdefault("first_epoch", now)
        facts["last_epoch"] = now
        facts["last_status"] = status
        facts["last_reason"] = reason
        rec["uuid_detect"] = facts
        return rec
    try:
        atomic_json.update_json(path, mutate)
    except atomic_json.AtomicWriteError as exc:
        print("seat-detect: 留痕寫不進 %s:%s" % (os.path.basename(path), exc),
              file=sys.stderr)


# ---- 主流程 -----------------------------------------------------------------

def copilot_open_session_candidate(started_epoch, path=None):
    """openedAt 落在 started±90s 的唯一 uuid;非唯一/檔壞/缺=None。"""
    import datetime
    path = path or os.path.join(os.path.expanduser("~"),
                                ".copilot", "open-sessions-state.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    hits = []
    for uid, meta in data.items():
        if not UUID_RE.fullmatch(str(uid)):
            continue
        opened = (meta or {}).get("openedAt") if isinstance(meta, dict) else None
        try:
            ts = datetime.datetime.fromisoformat(
                str(opened).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            continue
        if abs(ts - float(started_epoch)) <= 90:
            hits.append(uid)
    return hits[0] if len(hits) == 1 else None


# 探針五(codex,09-29):codex 0.158.0 互動介面把對話交給常駐 app-server daemon 寫,
# 席位行程樹底下沒有任何開著的 rollout 檔(lsof 探針落空,實測 pid 66651 才是持有者)。
# 改認「內容」:~/.codex/state_5.sqlite 的 threads 表。四個條件**全部**成立才認領,
# 判準同 claude 出生窗與 copilot open-sessions:多候選=誠實不寫(錯綁比空值毒)。
CODEX_INIT_PREFIX = "這是 AIB 新席初始化"   # 正本:provider-launch.sh 的 INITIAL_PROMPT
CODEX_CLAIM_WINDOW_S = 90


def codex_claim_candidates(rows, cwd, started_epoch, window_s=CODEX_CLAIM_WINDOW_S):
    """rows=[{id,cwd,created_at,first_user_message,originator,archived,rollout_path}]
    → 同時符合 (cwd 相同 / 首句是開席初始化句 / created_at 在 started±window /
    未封存 / 互動介面 codex-tui) 的 thread id 清單(可能 0、1、多個)。純函式。"""
    hits = []
    if not cwd:
        return hits
    for r in rows:
        try:
            if r.get("cwd") != cwd or r.get("archived"):
                continue
            if r.get("originator") != "codex-tui":
                continue
            if not str(r.get("first_user_message") or "").startswith(CODEX_INIT_PREFIX):
                continue
            if abs(float(r.get("created_at")) - float(started_epoch)) >= window_s:
                continue
        except (TypeError, ValueError):
            continue
        hits.append(r)
    return hits


def codex_state_rows(db_path=None):
    """唯讀取 threads 表(mode=ro,不寫 codex 自己的庫);讀不了=None(呼叫端寫明原因)。"""
    import sqlite3
    db_path = db_path or os.environ.get("EZ2_CODEX_STATE_DB") or os.path.join(
        os.path.expanduser("~"), ".codex", "state_5.sqlite")
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=2)
        try:
            cur = con.execute(
                "select id, cwd, created_at, first_user_message, originator, "
                "archived, rollout_path from threads")
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            con.close()
    except sqlite3.Error:
        return None


def codex_claim_session_id(rec, rows):
    """回 (uuid|None, 理由)。命中恰一份才回值;值取 rollout meta 的邏輯 session_id
    (與 lsof 探針同一口徑),讀不到或與 thread id 不一致=不寫。"""
    if rows is None:
        return None, "codex 對話庫讀不了,誠實不寫"
    hits = codex_claim_candidates(rows, rec.get("cwd"), rec.get("started_epoch") or 0)
    if not hits:
        return None, "codex 對話庫沒有符合(cwd+開席初始化句+時間窗)的對話,誠實不寫"
    if len(hits) > 1:
        return None, "codex 對話庫有 %d 份候選同時符合,誠實不寫" % len(hits)
    row = hits[0]
    try:
        with open(row.get("rollout_path") or "", encoding="utf-8") as fh:
            sid = parse_rollout_session_id(fh.readline())
    except OSError:
        sid = None
    if not sid:
        return None, "候選對話檔讀不到邏輯 session_id,誠實不寫"
    if sid != row.get("id"):
        return None, "候選對話檔的邏輯 id 與庫內 id 不同(疑似 fork/resume),誠實不寫"
    return sid, "codex-state-db-claim"


def claimed_elsewhere(sdir, this_file, uuid):
    """同一個 session_uuid 已經登記在**別的**席位檔上 ⇒ 回那一檔的檔名;沒有 ⇒ None。

    09-14 實例(艙 d1dd @5/@6):兩個 copilot 席 6 秒內連開,copilot 只開了一個 session,
    兩席各自跑偵測時「openedAt 落在 started±90s 且唯一」都成立 ⇒ 兩席寫進同一個 uuid,
    右欄兩列同一個五碼 a seat。「唯一」只在候選集合內看,沒看別席已經拿走了沒有。
    寫錯 uuid 比缺 uuid 毒(seat_status 檔頭同句),所以被別席登記過的候選一律不寫。
    """
    if not uuid:
        return None
    for other in sorted(glob.glob(os.path.join(sdir, "w*.json"))):
        if os.path.abspath(other) == os.path.abspath(this_file):
            continue
        try:
            with open(other, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("session_uuid") == uuid:
            return os.path.basename(other)
    return None


def detect_seat(rec, panes, births):
    """單席偵測;回 (status, uuid|None, source)。status∈resolved/dead/none。"""
    wid = rec.get("window_id")
    pid = panes.get(wid)
    if not pid:
        return "dead", None, "窗不在活集合"
    for cand in [pid] + descendants(pid):
        hit = probe_process(cand, rec.get("provider"))
        if hit:
            return "resolved", hit[0], hit[1]
    if rec.get("provider") == "codex":
        # 探針五(09-26):codex 0.157.1 append 完就關檔,探針二的 lsof 零命中,
        # 而對話檔就在磁碟上。判準與 claude 出生窗同一支純函式。
        started = rec.get("started_epoch") or 0
        rows, truncated = codex_rollout_rows(started)
        u, why = codex_window_candidate(rows, started, rec.get("cwd"),
                                        complete=not truncated)
        if u:
            return "resolved", u, "codex-rollout-meta-window"
        # ★ 09-30:對話檔那一關沒過,**再問 codex 自己的對話庫**(ca88bc364 的認領)。
        #   兩包各自加了一條 codex 分支,合進 main 後這裡先 return,下面那條認領永遠走不到
        #   (test-seat-detect falls_through_to_claim 抓到)。認領有自己的唯一性判準;
        #   兩關都沒過才誠實不寫,理由兩關都講。
        sid, claim_why = codex_claim_session_id(rec, codex_state_rows())
        if sid:
            return "resolved", sid, claim_why
        return "none", None, "%s;%s" % (why, claim_why) if claim_why else why
    if rec.get("provider") == "claude":
        u = unique_birth_candidate(births, rec.get("started_epoch") or 0)
        if u:
            return "resolved", u, "claude-transcript-birth-window"
        return "none", None, "transcript 候選不唯一或缺,誠實不寫"
    if rec.get("provider") == "copilot":
        # 探針四(copilot,08-29):~/.copilot/open-sessions-state.json 記
        # {uuid: {openedAt}};lsof 探不到(它不長開 session-state 檔)。
        # 判準同 claude 出生窗:openedAt 落在席位 started±90s 且**唯一**才寫,
        # 多候選=誠實不寫(寫錯 uuid 比缺 uuid 毒——seat_status 檔頭同句)。
        u = copilot_open_session_candidate(rec.get("started_epoch") or 0)
        if u:
            return "resolved", u, "copilot-open-sessions-window"
        return "none", None, "copilot open-sessions 候選不唯一或缺,誠實不寫"
    return "none", None, "無訊號"


def _ui(key, **params):
    """畫面字走四語鍵;每次照 i18n_lib.resolve_locale() 取,讀不到印鍵名(不擋程式)。"""
    try:
        import i18n_lib
        return i18n_lib.t(key, params or None, locale=i18n_lib.resolve_locale())
    except Exception:  # noqa: BLE001
        return key


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aib seat detect", description=_ui("aib.seat_detect.help.desc"))
    ap.add_argument("--all", action="store_true", help=_ui("aib.seat_detect.help.all"))
    ap.add_argument("--window", help=_ui("aib.seat_detect.help.window"))
    ap.add_argument("--apply", action="store_true", help=_ui("aib.seat_detect.help.apply"))
    ap.add_argument("--retry", type=int, default=1, help=_ui("aib.seat_detect.help.retry"))
    args = ap.parse_args(argv)
    if bool(args.all) == bool(args.window):
        print(_ui("aib.seat_detect.need_target"), file=sys.stderr)
        return 2

    sdir = seats_dir()
    if not os.path.isdir(sdir):
        print("seat-detect: 席位目錄不存在:%s" % sdir, file=sys.stderr)
        return 2
    hub = os.environ.get("EZ2_AIHUB_SOCKET", "aihub")
    births_by_dir = {}

    def births_for(rec):
        d = projects_dir_for_seat(rec)
        if d not in births_by_dir:
            births_by_dir[d] = transcript_births(d)
        return births_by_dir[d]

    unresolved = 0
    for attempt in range(max(1, args.retry)):
        panes = live_pane_pids(hub)
        unresolved = 0
        rows = []
        for sf in sorted(glob.glob(os.path.join(sdir, "w*.json"))):
            try:
                rec = json.load(open(sf, encoding="utf-8"))
            except (OSError, ValueError) as err:
                rows.append((os.path.basename(sf), "bad", None,
                             "席位檔壞:%s" % err))
                unresolved += 1
                continue
            if args.window and rec.get("window_id") != args.window:
                continue
            cur = rec.get("session_uuid")
            status, uuid, source = detect_seat(rec, panes, births_for(rec))
            # 09-14(the maintainer 19:2x:claude 席選單晚 13 秒才跳):開席時已由 aib 發好 uuid 的席,
            # 偵測無訊號時**不算未解、不重試**——--retry 5 是給「還沒有 uuid」的席等 CLI 開檔用的,
            # 對已有 uuid 的席等 12 秒只是白等(偵測到了也只能 ok/mismatch,不會改寫)。
            if cur and status == "none":
                status, uuid, source = "resolved", cur, "已有 uuid(偵測無訊號,不重試)"
            if status == "resolved" and not cur:
                _taken = claimed_elsewhere(sdir, sf, uuid)
                if _taken:
                    status, uuid, source = "none", None, (
                        "候選 %s 已登記在 %s(同一 uuid 不得兩席共用),誠實不寫" % (uuid[:13], _taken))
            foreign_env = (
                rec.get("provider") == "codex"
                and str(rec.get("session_uuid_source") or "").startswith("env:")
                and not str(rec.get("session_uuid_source") or "").startswith("env:CODEX")
            )
            if status == "resolved" and cur and cur != uuid and foreign_env \
                    and str(source or "").startswith("lsof:codex"):
                if args.apply:
                    def _fix(rec, _uuid=uuid, _source=source):
                        if not isinstance(rec, dict):
                            return rec
                        rec["session_uuid"] = _uuid
                        rec["session_uuid_source"] = _source
                        facts = rec.get("uuid_detect") if isinstance(rec.get("uuid_detect"), dict) else {}
                        facts["resolved_epoch"] = int(time.time())
                        facts["last_status"] = "replaced-foreign-env"
                        facts["last_reason"] = _source
                        rec["uuid_detect"] = facts
                        return rec
                    try:
                        atomic_json.update_json(sf, _fix)
                    except atomic_json.AtomicWriteError as exc:
                        print("seat-detect: 寫不進 %s:%s" % (os.path.basename(sf), exc),
                              file=sys.stderr)
                    rows.append((os.path.basename(sf), "replaced", uuid, source))
                else:
                    rows.append((os.path.basename(sf), "mismatch", uuid,
                                 "既存 %s 來自別的引擎,應改成對話檔 %s" % (cur[:13], uuid[:13])))
                continue
            if status == "resolved":
                if cur and cur != uuid:
                    rows.append((os.path.basename(sf), "mismatch", uuid,
                                 "既存 %s ≠ 偵測 %s(%s)——不覆寫,請人裁"
                                 % (cur[:13], uuid[:13], source)))
                    unresolved += 1
                elif not cur and args.apply:
                    _apply_uuid(sf, uuid, source)
                    # B15:atomic_json 寫失敗會具名出聲,但不能只靠那句判成功。
                    # 寫後重讀持久值,再交合法位址單源驗證；寫入被吞、壞 JSON、
                    # 或 session_uuid 仍導不出位址都屬未解析,不可自報 applied。
                    try:
                        with open(sf, encoding="utf-8") as fh:
                            persisted = json.load(fh)
                    except (OSError, ValueError):
                        persisted = None
                    if seat_address.code_of(persisted) is None:
                        name = os.path.basename(sf)
                        print("seat-detect: 座位碼量不到:%s"
                              "(session_uuid 已回填但 canonical code 無法導出)" % name,
                              file=sys.stderr)
                        rows.append((name, "unresolved", None,
                                     "持久值無法導出合法位址"))
                        unresolved += 1
                    else:
                        rows.append((os.path.basename(sf), "applied", uuid, source))
                else:
                    # --apply 是開席的落檔路徑；已有值也必須能由同一把尺導出。
                    # 純偵測模式只回觀測,不把持久化契約混進來。
                    if args.apply and seat_address.code_of(rec) is None:
                        name = os.path.basename(sf)
                        print("seat-detect: 座位碼量不到:%s"
                              "(session_uuid 已回填但 canonical code 無法導出)" % name,
                              file=sys.stderr)
                        rows.append((name, "unresolved", None,
                                     "持久值無法導出合法位址"))
                        unresolved += 1
                    else:
                        rows.append((os.path.basename(sf), "ok", uuid, source))
            elif status == "dead":
                # 對帳報告:只報不刪(清理是人的決定)
                rows.append((os.path.basename(sf), "dead", None, source))
                if not cur:
                    unresolved += 1
            else:
                rows.append((os.path.basename(sf), "none", None, source))
                unresolved += 1
        if unresolved == 0 or attempt == max(1, args.retry) - 1:
            break
        time.sleep(3)

    # ★ ED.13(2026-08-22):--apply 時,量不到的席也要留痕。
    #   沒有這段,「aib 開席尾段那次 retry 5 失敗被 `|| true` 吞掉」與
    #   「從來沒人對這席跑過偵測」在席位檔裡長得一模一樣;回填器與人
    #   都無從分辨「還在等」與「早就沒人看了」。只寫事實(次數/時間/
    #   最後一次的理由),pending/overdue 由讀的人拿 started_epoch 自己算。
    if args.apply:
        for name, status, uuid, source in rows:
            if status == "none":            # 死窗的化石檔不碰(清理是人的決定,改 mtime 只會誤導)
                _note_attempt(os.path.join(sdir, name), status, source)

    for name, status, uuid, source in rows:
        print("%s %s %s %s" % (name, status, (uuid or "-")[:13], source))
    return 0 if unresolved == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
