#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""多引擎真活動探針(08-14 the maintainer「每引擎各自探針」;第三艦長派工)。

**單一真相源**:右欄畫 [X] 與殺窗前重驗閒置,兩處共用本模組——同一個問題
兩份實作遲早分岔成「面板說閒置、殺前說不閒置」(或反過來),那比沒有重驗
更糟(第三艦長硬性條件一)。

五引擎各自的活動跡證(每一條都是實測定源,不是推測):
  claude: <projects>/<uuid>.jsonl                mtime 隨對話推進
  codex : ~/.codex/sessions/**/rollout-*.jsonl   mtime 隨對話推進;配對鍵=
          **meta 首行 payload.session_id**,不是檔名 uuid(檔名是 thread id,
          fork/resume 下必錯——seat-detect.py 檔頭 08-14 實錯已記)
  agy   : ~/.gemini/antigravity-cli/conversations/<uuid>.{db,db-wal,db-shm}
          三者取最新。★不是 presence/<uuid>.lock:實測其 mtime≈開席時刻
          (與席位年齡只差 1.8~3.3 分鐘),拿它當活動會讓工作中的席滿 5 分鐘
          就亮 [X]。★也不是本體 .db 單獨看:conversations 走 SQLite WAL,
          寫入先落 -wal,本體只在 checkpoint 才動(實測有本體比側檔舊 61
          小時的席),只看本體會**高估閒置**=同一類誤綠。
  copilot: ~/.copilot/session-state/<uuid>/events.jsonl
          mtime 隨事件流推進;缺檔仍是 None,不退席位檔自報時間。
  grok  : ~/.grok/sessions/<urlencoded-cwd>/<uuid>/{updates,chat_history,
          events}.jsonl 與 signals.json 取最新 mtime。查找落在
          grok_ctx_probe.session_dir(單一真相源,CTX 探針同路徑)。
  sand/grokbot: ~/.ez2-aib/bridge/<uuid>/{inbox,outbox}/*.json
          訊息 mtime;無訊息則 status.started_epoch。★不用 status 心跳。
  未知引擎 = 無探針 = None(新引擎進艦隊時的預設安全方向)

取不到 = None = **誠實不亮/拒殺**(十九輪審高1 紅線:壞、舊、被竄的席位檔
自報時戳不得點亮破壞性入口;顯示層可以退自報去畫 💤,kill 資格不准退)。

★不採用 tmux #{window_activity} 當替代訊號:第三艦長實測它會被 TUI 自身
重繪刷新(席位檔 12-17 小時舊而 window_activity 只有 0.2 小時),拿它當判準
會讓確認鍵幾乎永遠拒殺,功能等於廢掉。

CLI(殺窗前重驗用;人也可手查):
  activity_probe.py --window @N [--threshold 秒]
  rc:0=閒置達門檻(可釋放)/3=未達門檻或無訊號(拒殺,fail-closed)/2=用法錯
  stdout=單行 JSON {window, provider, idle_s, threshold, killable, source}
"""

import argparse
import glob
import json
import math
import os
import re
import subprocess
import sys
import time

_COCKPIT_DIR = os.path.dirname(os.path.abspath(__file__))
if _COCKPIT_DIR not in sys.path:
    sys.path.insert(0, _COCKPIT_DIR)
import grok_ctx_probe  # noqa: E402  CTX 與活動共用 grok session 目錄查找
from ctx_by_seat import resolve_live_session  # noqa: E402  D41:/clear 後現任 session

# [X] 門檻:與右欄同一常數(EZ2_AIB_SUB_DONE_S 家族語意,the maintainer 08-14 拍板
# 5 分鐘);env 可覆寫供治具,預設不動。
IDLE_KILL_S = int(os.environ.get("EZ2_AIB_IDLE_KILL_S") or 300)
CODEX_LOOKBACK_D = int(os.environ.get("EZ2_AIB_CODEX_LOOKBACK_D") or 7)

_UUID_FULL_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_WID_RE = re.compile(r"@[0-9]{1,10}")

_codex_map = None          # 整幀建一次(非每席一次):401 檔約 96ms


def _projects_root():
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")


def escape_cwd(path):
    """cwd → `~/.claude/projects/` 底下的目錄名。

    ★ 這條規則原本寫在 projects_dir() 裡面(`root.replace("/", "-")`),
      EH.3 要在兩個地方用到,所以抽出來共用 —— **不另寫第二份**。
      抽出來的同時也才驗得動:`/private/tmp/claude-501/-Users-...` 會變成
      `-private-tmp-claude-501--Users-...`,那個連續兩個 `-` 正好是規則的
      指紋(路徑段裡原本就有的 `-` 留著,`/` 變 `-`),實測與磁碟上的目錄名
      逐字相符。
    """
    return (path or "").replace("/", "-")


def projects_dir():
    override = os.environ.get("EZ2_CLAUDE_PROJECTS_DIR")
    if override:
        return override
    root = os.environ.get("EZ2_AIB_ROOT") or ""
    return os.path.join(_projects_root(), escape_cwd(root))


def project_dir_from_cwd(cwd):
    """席位檔的 cwd → 它的專案目錄;cwd 為空回 None。

    ★ EH.3 讀端。與 project_dir_for() 的差別是**它不需要猜**:
      project_dir_for 靠「哪個目錄底下有這個 uuid 的 transcript」反查,
      所以那一席**還沒寫出第一筆 transcript 之前它答不出來**,只能回
      default —— 而 default 可能是錯的,且錯了不報錯。cwd 是開席當下就
      知道的權威值,新席第一秒就答得出來。
    """
    if not cwd:
        return None
    return os.path.join(_projects_root(), escape_cwd(cwd))


def seat_project_dir(rec):
    """席位紀錄 → 專案目錄;沒有 cwd 欄(舊席位檔)回 None,不猜。

    ★ 回 None 是誠實值:EH.4 拿它分專案時,「不知道」要能與「屬於某專案」
      分得開。若這裡退回主艙或 repo 根,每一份舊席位檔都會被歸到同一個
      專案 —— 失效值會長得跟正確值一樣。
    """
    if not isinstance(rec, dict):
        return None
    return project_dir_from_cwd(rec.get("cwd"))


def project_dirs():
    """`~/.claude/projects/` 底下所有專案目錄(絕對路徑)。

    ★ 專案目錄名是由**那個 session 的 cwd** 轉義而來,不是由 repo 決定的。
    """
    base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    try:
        return sorted(os.path.join(base, n) for n in os.listdir(base)
                      if os.path.isdir(os.path.join(base, n)))
    except OSError:
        return []


def project_dir_for(uuid, default=None, cwd_hint=None):
    """這個 session 的 transcript 落在哪個專案目錄;找不到回 default。

    ★★ 為什麼需要它:`projects_dir()` 由 `EZ2_AIB_ROOT` 推導出**單一**目錄,
    而 session 的專案目錄由**它自己的 cwd** 決定。兩者只有在「該席的 cwd 剛好
    等於 repo 根」時才一致。不一致時,那一席的子代理**永遠掃不到**——
    而畫面上看不出任何異狀,它只是沒有第三層。

    2026-08-16 實案:aihub-8 的艦長席 cwd 是家目錄,transcript 在
    `-Users-user`,而面板只掃 `-Users-user-ez2-aib`;同艙另外三席
    cwd 是 repo 所以看得到子代理,艦長席派了兩個卻一個都沒顯示。
    **「有些席看得到、有些看不到」比「全部看不到」更難發現。**

    先試 default(絕大多數情況命中,不用掃);再逐個專案目錄找。
    uuid 全機唯一,所以命中即確定,不需要消歧。
    """
    # ★ EH.3:席位檔帶 cwd 時,它是**權威值**,排在掃描之前試。
    #   掃描是反查(靠 transcript 已經存在),cwd 是正解(開席當下就知道)。
    for base in ([cwd_hint] if cwd_hint else []) + \
            ([default] if default else []) + project_dirs():
        if not base:
            continue
        if os.path.isdir(os.path.join(base, uuid)) or \
                os.path.isfile(os.path.join(base, uuid + ".jsonl")):
            return base
    return default


def seats_dir():
    # EH.2:改走單一真相源 seats_dir_lib(順位 HUB > INSTANCE_HOME > HOME > 主艙)。
    # 舊碼沒看 HUB,在多艙環境下一律退主艙,拿到格式正確的錯答案。
    from seats_dir_lib import seats_dir as _sd   # noqa: E402
    return _sd()


def codex_activity_map(now=None):
    """rollout meta session_id -> 最新 mtime。行程內建一次。"""
    global _codex_map
    if _codex_map is not None:
        return _codex_map
    _codex_map = {}
    now = time.time() if now is None else now
    base = os.environ.get("EZ2_CODEX_SESSIONS_DIR") \
        or os.path.expanduser("~/.codex/sessions")
    cutoff = now - CODEX_LOOKBACK_D * 86400
    cands = []
    for path in glob.glob(os.path.join(base, "*", "*", "*",
                                       "rollout-*.jsonl")):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime >= cutoff:
            cands.append((mtime, path))
    cands.sort(reverse=True)    # 新→舊:首次命中即最新,同源舊檔不覆寫
    for mtime, path in cands:
        try:
            with open(path, encoding="utf-8") as fh:
                meta = json.loads(fh.readline())
        except (OSError, ValueError):
            continue
        payload = meta.get("payload") if isinstance(meta, dict) else None
        sid = payload.get("session_id") if isinstance(payload, dict) else None
        if isinstance(sid, str) and sid not in _codex_map:
            _codex_map[sid] = mtime
    dbg = os.environ.get("EZ2_AIB_PROBE_DEBUG")
    if dbg:
        # 「整幀建一次不是每席一次」的機器判準(13 席就是 13 倍,這種退化
        # review 看不出來)。旗標=落點路徑;未設=零寫入,生產路徑不碰。
        try:
            with open(dbg, "a", encoding="utf-8") as fh:
                fh.write("codex-map-built %d\n" % len(_codex_map))
        except OSError:
            pass
    return _codex_map


def claude_transcript_paths(sess, base=None):
    """該 session 的 transcript 所有候選路徑(uuid 是唯一鍵,不靠目錄縮範圍)。

    ★ 為什麼不能只找一個目錄(08-15 實錯,面板對兩個正在打字的席顯示 💤23h):
    Claude Code 依 **該席的 cwd** 決定 transcript 落在
    `~/.claude/projects/<cwd 轉出的目錄名>/` 底下,而**席位的 cwd 不保證等於
    `EZ2_AIB_ROOT`**——@14/@15 兩席開在家目錄(選單在家目錄呼叫,新席繼承了
    呼叫點的 cwd),transcript 落在 `-Users-user/`,而 `projects_dir()`
    指的是 `-Users-user-ez2-aib/`。於是 `activity_age` 回 None,顯示層
    退回席位檔自報的 `last_used_epoch`(那個欄位從建檔起沒更新過)= 💤 永遠
    停在開席那一刻。**同一個 cwd 缺陷同時造成隱形與誤判**:被寫進交接單當成
    「零 token 殭屍席」的,剛好就是 cwd 被設錯的那兩席。

    三種形狀全部涵蓋,因為 `base` 到底是「projects 根」還是「某一個 project
    目錄」取決於 `EZ2_AIB_ROOT` 有沒有設,**不該由本函式假設**:

      base/<uuid>.jsonl              base 就是那個 project 目錄(治具注入即此形)
      base/*/<uuid>.jsonl            base 是 projects 根(EZ2_AIB_ROOT 未設時)
      dirname(base)/*/<uuid>.jsonl   base 是某個 project 目錄 → 掃它的兄弟

    `glob.escape(sess)` 是因為 sess 未必經過 `_UUID_FULL_RE`(`activity_age`
    是公開入口,可被直接呼叫);uuid 裡沒有 glob 元字元,但**「現在的呼叫端
    剛好都乾淨」不是不轉義的理由**。
    """
    base = projects_dir() if base is None else base
    safe = glob.escape(sess)
    paths = [os.path.join(base, sess + ".jsonl")]
    for root in (base, os.path.dirname(base.rstrip(os.sep))):
        if not root:
            continue
        paths.extend(glob.glob(os.path.join(root, "*", safe + ".jsonl")))
    return paths


def _newest_mtime(paths):
    newest = None
    for path in paths:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if newest is None or mtime > newest:
            newest = mtime
    return newest



# Cursor/Desktop 橋接席(sand/codexbot/grokbot)共用 bridge 信箱。
# 真活動跡證=inbox/outbox 訊息檔 mtime;status.json 的 updated_epoch 是心跳,
# 活著就永遠新鮮,不得當閒置判準(否則 [X] 永不亮或永遠亮)。
BRIDGE_BOT_PROVIDERS = frozenset(("sand", "codexbot", "grokbot", "claudebot", "musebot"))


def bridge_state_root():
    for key in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        v = os.environ.get(key)
        if v:
            return os.path.expanduser(v)
    return os.path.expanduser("~/.ez2-aib")


def bridge_activity_mtime(sess):
    """橋接席真活動 mtime;None=無訊號。

    優先 inbox/outbox 最新訊息檔;都沒有才讀 status.started_epoch。
    刻意忽略 status.json 的 mtime / updated_epoch(心跳)。
    """
    if not sess:
        return None
    root = os.path.join(bridge_state_root(), "bridge", sess)
    if not os.path.isdir(root):
        return None
    paths = []
    for sub in ("inbox", "outbox"):
        folder = os.path.join(root, sub)
        if not os.path.isdir(folder):
            continue
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if name.endswith(".json"):
                paths.append(os.path.join(folder, name))
    newest = _newest_mtime(paths)
    if newest is not None:
        return newest
    status_path = os.path.join(root, "status.json")
    try:
        with open(status_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    started = data.get("started_epoch")
    if isinstance(started, (int, float)) and started > 0:
        return float(started)
    return None


def activity_age(provider, sess, now=None, proj=None):
    """該席真活動距今秒數;None=無訊號(誠實不亮/拒殺,絕不退席位檔自報)。"""
    if not sess:
        return None
    now = time.time() if now is None else now
    if provider == "codex":
        mtime = codex_activity_map(now).get(sess)
    elif provider == "agy":
        base = os.environ.get("EZ2_AGY_CONV_DIR") \
            or os.path.expanduser("~/.gemini/antigravity-cli/conversations")
        mtime = _newest_mtime([os.path.join(base, sess + suffix)
                               for suffix in (".db", ".db-wal", ".db-shm")])
    elif provider == "copilot":
        base = os.environ.get("EZ2_COPILOT_SESSION_STATE_DIR") \
            or os.path.expanduser("~/.copilot/session-state")
        # 09-14(the maintainer:copilot 席 `💤--` 且沒有 [X]):events.jsonl 只在**第一回合之後**才出現,
        # 開席到第一句話之間這一席零訊號 ⇒ 永遠 💤-- 也永遠拿不到 [X](關不掉)。
        # copilot v1.0.83 開席當下就建 workspace.yaml 與 inuse.<pid>.lock(實測 mtime=開席時刻,
        # 退出時 workspace.yaml 會再被寫一次)⇒ 三者取最新:有回合看 events,沒回合看開席那一刻,
        # 「開了 5 分鐘一句話都沒說」就是真閒置,[X] 該亮。
        sdir = os.path.join(base, sess)
        mtime = _newest_mtime(
            [os.path.join(sdir, "events.jsonl"), os.path.join(sdir, "workspace.yaml")]
            + glob.glob(os.path.join(glob.escape(sdir), "inuse.*.lock")))
    elif provider == "claude":
        base = proj if proj is not None else projects_dir()
        # proj 注入能力保留:它仍是第一候選(治具靠它)。新增的是「同一個
        # uuid 也可能落在別的 project 目錄」——見 claude_transcript_paths。
        mtime = _newest_mtime(claude_transcript_paths(sess, base))
    elif provider == "grok":
        mtime = grok_ctx_probe.activity_mtime(sess)
    elif provider in BRIDGE_BOT_PROVIDERS:
        # sand/codexbot/grokbot: 真活動= inbox/outbox 訊息 mtime;
        # 無訊息才退 started_epoch。禁止用 status 心跳(活著會每 0.1s 刷新)。
        mtime = bridge_activity_mtime(sess)
    else:
        return None            # 未知引擎=無探針
    if mtime is None:
        return None
    age = now - mtime
    return age if age >= 0 else None


def session_dirs(proj=None):
    """projects dir 內的 session uuid 集合(根層 <uuid>.jsonl ∪ <uuid>/ 目錄)。

    八輪審中3:<uuid>/ 只在開過子代理後才存在,只掃目錄會讓沒開過子 AI 的
    session 永遠對映不到,故兩源聯集。"""
    base = projects_dir() if proj is None else proj
    found = set()
    try:
        for name in os.listdir(base):
            if len(name) == 36 and name.count("-") == 4:
                found.add(name)
            elif name.endswith(".jsonl") and len(name) == 42 \
                    and name[:-6].count("-") == 4:
                found.add(name[:-6])
    except OSError:
        pass
    return sorted(found)


def map_seat_to_session(seat_id, sessions):
    """席位五碼對 session uuid 的頭或尾;**單一命中才綁**,多重=None
    (誠實:寧可不顯示,也不接別席的真值)。"""
    if not re.fullmatch(r"[0-9a-f]{4,8}", seat_id or ""):
        return None
    hits = [n for n in sessions
            if n.replace("-", "").startswith(seat_id)
            or n.replace("-", "").endswith(seat_id)]
    return hits[0] if len(hits) == 1 else None


def resolve_session(rec, sessions=None, proj=None):
    """席位記錄 → session uuid。**整條鏈的共用入口**(第三艦長二退的教訓):
    單一真相源要涵蓋「視窗→席位→引擎→session→idle」整條鏈,不是只涵蓋
    最後一格 idle 計算——先前面板有五碼 fallback、探針沒有,於是面板亮 [X]
    的席位在殺前重驗被判 no-signal(w2/37a0b 實案:面板 💤58m 亮 [X],探針
    連 provider 都認不出)。
    回 (uuid|None, note|None);note 非 None 表示有壞形狀值得留痕。"""
    if not isinstance(rec, dict):
        return None, None
    raw = rec.get("session_uuid")
    if raw is not None:
        if isinstance(raw, str) and _UUID_FULL_RE.fullmatch(raw):
            return raw, None            # 合法值直接綁(免猜)
        note = "session_uuid invalid, ignored"
    else:
        note = None
    seat = rec.get("seat_id")
    if not isinstance(seat, str):
        return None, note
    if sessions is None:
        sessions = session_dirs(proj)
    return map_seat_to_session(seat, sessions), note


# --- 09-06 D41:配對改走 statusline 的 by-seat 檔 -----------------
#   病:claude 席 /clear 之後 CLI 改寫【另一支 uuid】的 transcript,席位檔登記的那支從此停寫;
#   探針拿登記 uuid 找 mtime ⇒ 讀到一支別的、停寫 47h 的檔,回一個結構完整、rc=0、killable=true 的 JSON。
#   失效值與合法讀數逐字相同,方向是「可以殺」,而右欄 [X] 與殺窗前重驗共用本模組。
#   修:現用 session 由 ~/.ez2/ctx/by-seat/<登記五碼>.json 說;instance/window
#   必須同席。缺檔或壞形狀走登記 uuid 舊路,不把原本可判的值變不可判。
#   grok / codex / agy / copilot 一字不動:它們的配對鍵不是這個。
def live_session_for(provider, wid, registered, seat_cwd=None):
    """整鏈共用:claude 席的活動鍵 = by-seat 現用 session;其他引擎照舊。

    by-seat 缺檔或壞形狀必須走登記 uuid；不得把原本可判的死席變成不可判。
    ``seat_cwd`` 留在相容簽名中，但不再參與身份判定。
    """
    if provider != "claude":
        return registered
    # ★ ctx 目錄**不在這裡讀**:`ctx_by_seat.ctx_dir(root=None)` 自己就會退到
    #   EZ2_CTX_DIR,所以傳與不傳逐字等價;而在本函式讀它會讓本函式同時碰
    #   EZ2_CTX_DIR 與已宣告的 EZ2_AIB_INSTANCE,單源判別式因此把整個函式
    #   收成消費站,test-cabin-read-set / test-cabin-single-source-names 兩支
    #   從 rc0 變 rc1(09-06 發版全套三紅之二)。把讀取留在下游解析鏈裡,
    #   一個名字都不用宣告。判別式本身的缺陷另立缺陷四十七,別在這裡補。
    sid, _rebound, _note = resolve_live_session(
        registered, instance=os.environ.get("EZ2_AIB_INSTANCE"), window=wid)
    # transcript mtime 過期不等于席不活躍:by-seat 沒改登記時,再看窗上現用
    # session(statusline 寫的那份)。缺注入或壞形狀不猜,走登記 uuid 舊路。
    if sid is None or sid == registered:
        other = _live_uuid_from_window(wid)
        if other:
            sid = other
    return sid


def _live_uuid_from_window(wid):
    """窗 → 現用 session uuid;沒有注入或形狀壞回 None。

    治具用 EZ2_AIB_WINDOW_PANES + EZ2_CTX_BY_PANE_DIR,不碰真 tmux。
    """
    raw_map = os.environ.get("EZ2_AIB_WINDOW_PANES") or ""
    raw_dir = os.environ.get("EZ2_CTX_BY_PANE_DIR") or ""
    if not raw_map or not raw_dir:
        return None
    try:
        mapping = json.loads(raw_map)
    except ValueError:
        return None
    if not isinstance(mapping, dict):
        return None
    panes = mapping.get(wid)
    if not isinstance(panes, list) or not panes:
        return None
    pane = panes[0]
    if not isinstance(pane, str) or not pane.startswith("%"):
        return None
    num = pane[1:]
    if not num.isdigit():
        return None
    path = os.path.join(raw_dir, num + ".json")
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    found = rec.get("session_id")
    if isinstance(found, str) and _UUID_FULL_RE.fullmatch(found):
        return found
    return None


def seat_for_window(wid, sdir=None):
    """window_id -> (provider, session_uuid);查不到=(None, None)。

    檔名與記錄內 window_id 交叉驗(與右欄同一道:一輪審中5)。"""
    if not _WID_RE.fullmatch(wid or ""):
        return None, None
    sdir = seats_dir() if sdir is None else sdir
    path = os.path.join(sdir, "w%s.json" % wid.lstrip("@"))
    try:
        rec = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(rec, dict) or rec.get("schema") != "aib-seat/v1":
        return None, None
    if str(rec.get("window_id") or "") != wid:
        return None, None
    # ★走共用的整鏈解析(含五碼 fallback):缺 session_uuid 欄的舊席位檔
    # (如 w2/37a0b)面板靠五碼對得到 session,探針若只認 session_uuid 就會
    # 判 no-signal → 面板亮 [X] 但殺前重驗拒殺,使用者按了沒反應。
    uuid, _note = resolve_session(rec)
    if uuid is None:
        return None, None
    prov = rec.get("provider")
    prov = prov if isinstance(prov, str) else None
    # D41:活動鍵在【這裡】換成 by-seat 現用 session,不是只在 main 換 ——
    #   idle_reaper.py:711 與 main 都走 seat_for_window→activity_age,改在共用入口兩邊才同源;
    #   只改 main 的話右欄/CLI 說 active、殺窗前重驗照舊讀停寫的檔,面板與殺手又會對不上。
    return prov, live_session_for(prov, wid, uuid, rec.get("cwd"))


# ── B10:死席畫面與留影尾端的可貼恢復指令(單一真相源)──────────────────────
# ★ 只有**查證過**的 provider 才給指令。agy 的 `--conversation` 與 copilot 的
#   `--connect` 兩個旗標都存在,但沒有任何依據說本倉的 session_uuid 就是它們要的 ID
#   ——copilot 那個講的還是**遠端** session。印出來會是一行「看起來像恢復指令、
#   貼得下去、而做的是別的事」的東西,**那比誠實說量不到更糟**(cap 12:43)。
#   要補進來,得連依據一起補,不是把旗標抄上來。
_RESUME_TEMPLATES = {
    "codex": "codex resume %s",
    "claude": "claude --resume %s",
}
# ★ codexbot 的恢復不是「用 uuid 重開一顆 CLI」,是**重跑這個窗格原本的開席指令**:原指令本身帶
#   `--session-uuid <同一支>` 與桌面對話綁定,respawn 後席碼不變、綁的對話不變
#   (10-07 00:04 與 00:12 a seat 真機兩次都是這招救回來的)。所以範本吃的是窗 id,不是 uuid。
_RESUME_BY_WINDOW = {
    "codexbot": "tmux respawn-pane -k -t %s",
}
UNKNOWN_RESUME = "\u91cf\u4e0d\u5230"


def resume_command(wid, sdir=None):
    """窗 id → 一行可直接貼上的恢復指令,或 `量不到`。

    ★ uuid **只走** `seat_for_window` → `resolve_session`(含五碼 fallback)。
      自己讀席位檔的失效形狀是「舊席明明有五碼可解,卻印成量不到」——
      **假的量不到看起來像誠實**,而且不會有任何一格變紅。
    ★ 回傳恰一行且永不為空:空字串會讓呼叫端的 `[ -n ... ]` 靜靜跳過,
      那與「這一席沒有恢復方式」在畫面上逐字相同。
    """
    provider, uuid = seat_for_window(wid, sdir)
    by_window = _RESUME_BY_WINDOW.get(provider)
    if by_window is not None and isinstance(uuid, str) and _UUID_FULL_RE.fullmatch(uuid):
        return by_window % wid
    template = _RESUME_TEMPLATES.get(provider)
    if template is None or not isinstance(uuid, str) or not _UUID_FULL_RE.fullmatch(uuid):
        return UNKNOWN_RESUME
    return template % uuid


def _cpu_seconds(raw):
    """ps cumulative CPU time, including macOS hundredths; invalid is unmeasured."""
    match = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)", raw or "")
    if not match:
        return None
    days, hours, minutes, seconds = match.groups()
    value = int(days or 0) * 86400 + int(hours or 0) * 3600 + int(minutes) * 60 + float(seconds)
    return value if math.isfinite(value) and float(seconds) < 60 else None


def _cpu_snapshot():
    """Read the process tree once. Start time distinguishes a reused PID."""
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,lstart=,time="],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    rows = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 8 or not fields[0].isdigit() or not fields[1].isdigit():
            return None  # A partial process table cannot prove an idle subtree.
        cpu = _cpu_seconds(fields[7])
        if cpu is None:
            return None
        rows[int(fields[0])] = (int(fields[1]), " ".join(fields[2:7]), cpu)
    return rows or None


def _cpu_subtree(rows, pid):
    if rows is None or pid not in rows:
        return None
    found = {pid}
    while True:
        children = {p for p, row in rows.items() if row[0] in found}
        if children <= found:
            return {p: rows[p] for p in found}
        found.update(children)


def sample_cpu_activity(panes, interval=1.0):
    """Measure all requested pane subtrees over one shared >=1s interval.

    A quiet interval is not the duration of inactivity. Consumers must still
    obtain that duration from a trusted source before applying a reap threshold.
    Pane polling timestamps are diagnostic only, never a substitute for CPU.
    """
    interval = float(interval)
    if not math.isfinite(interval) or interval < 1.0:
        raise ValueError("CPU sampling interval must be at least one second")
    if not panes:
        return []
    first = _cpu_snapshot()
    started = time.monotonic()
    time.sleep(interval)
    second = _cpu_snapshot()
    elapsed = time.monotonic() - started
    results = []
    for pane in panes:
        raw = str(pane.get("tmux_activity") or "").strip()
        measured = raw.isdigit() and int(raw) > 0
        result = {"signal": "cpu-delta", "state": "unknown",
                  "tmux_activity": "measured" if measured else "unmeasured",
                  "tmux_activity_epoch": int(raw) if measured else None,
                  "cpu_delta_s": None, "sample_s": elapsed}
        pid = pane.get("pid")
        before = _cpu_subtree(first, pid)
        after = _cpu_subtree(second, pid)
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
            before = after = None
        if before is not None and after is not None and elapsed >= 1.0:
            if before[pid][1] != after[pid][1]:
                result["state"] = "unknown"  # PID replacement, not the same CLI.
            elif set(before) != set(after):
                result["state"] = "active"  # Child launch/exit is work too.
            elif any(before[p][:2] != after[p][:2] or after[p][2] < before[p][2]
                     for p in before):
                result["state"] = "unknown"
            else:
                delta = sum(after[p][2] - before[p][2] for p in before)
                result["cpu_delta_s"] = round(delta, 6)
                result["state"] = "active" if delta > 0 else "idle"
        results.append(result)
    return results


def sample_window_cpu(window, socket):
    """Explicit socket/window entry; no ambient tmux client selection."""
    if not os.path.isabs(socket):
        return []
    try:
        proc = subprocess.run(
            [os.environ.get("EZ2_AIB_TMUX_BIN") or "tmux", "-S", socket,
             "list-panes", "-t", window, "-F",
             "#{window_id}\t#{pane_pid}\t#{pane_last_activity}"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode:
        return []
    panes = []
    for line in proc.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 3 or fields[0] != window or not fields[1].isdigit():
            return []
        panes.append({"pid": int(fields[1]), "tmux_activity": fields[2]})
    return sample_cpu_activity(panes)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="activity_probe")
    ap.add_argument("--window", required=True, help="hub 窗 id,如 @12")
    ap.add_argument("--threshold", type=int, default=IDLE_KILL_S,
                    help="閒置門檻秒數(預設 %d)" % IDLE_KILL_S)
    ap.add_argument("--resume-command", action="store_true",
                    help="只印該席的可貼恢復指令(B10);查不到印「量不到」")
    ap.add_argument("--sample-socket", help="sample CPU twice for this absolute tmux socket")
    args = ap.parse_args(argv)
    if not _WID_RE.fullmatch(args.window):
        print("activity_probe: 窗 id 形狀不合:%s" % args.window,
              file=sys.stderr)
        return 2
    if args.resume_command:
        # ★ 資訊型查詢:**永遠 rc=0**,stdout 恰一行,stderr 空。
        #   「量不到」是**結果**不是錯誤;做成非零 rc,呼叫端就會用 `|| true` 吞掉,
        #   而吞掉之後與「印了一行殘缺指令」在畫面上逐字相同。
        print(resume_command(args.window))
        return 0
    if args.sample_socket:
        samples = sample_window_cpu(args.window, args.sample_socket)
        state = ("active" if any(s["state"] == "active" for s in samples) else
                 "idle" if samples and all(s["state"] == "idle" for s in samples) else "unknown")
        print(json.dumps({"window": args.window, "signal": "cpu-delta", "source": state,
                          "tmux_activity": "measured" if samples and all(
                              s["tmux_activity"] == "measured" for s in samples) else "unmeasured",
                          "samples": samples, "killable": False, "idle_s": None}))
        # This reports instantaneous activity only, never permission to reap.
        return 0 if state != "unknown" else 3
    provider, uuid = seat_for_window(args.window)   # claude uuid 已在共用入口換成 by-seat 現用 session
    age = activity_age(provider, uuid)
    killable = age is not None and age >= args.threshold
    if age is None:
        source = "no-signal"       # 對映不到/正本缺/未知引擎
    elif killable:
        source = "idle"
    else:
        source = "active"
    print(json.dumps({"window": args.window, "provider": provider or "?",
                      "idle_s": None if age is None else int(age),
                      "threshold": args.threshold, "killable": killable,
                      "source": source}, ensure_ascii=False))
    return 0 if killable else 3


if __name__ == "__main__":
    sys.exit(main())
