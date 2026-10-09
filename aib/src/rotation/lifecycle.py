# SPDX-License-Identifier: Apache-2.0
"""E6.1 席位生命週期協定:pause / resume / handoff 的共用前置。

E6.3 已有 checkpoint 與交棒(rotation.py);本模組補的是**協定層**——
「什麼時候該做什麼」與「暫停後怎麼原樣接回」,讓 pause/resume/handoff
三個動詞共用同一份可驗證狀態,而不是各自土法煉鋼。

三條設計約束(來自實戰,不是憑空):
  1. **狀態=檔案,不是記憶**:席位隨時可能整個消失(08-09 aihub server
     一死四席全滅)。lifecycle 狀態與 checkpoint 一樣落 append-only 檔,
     重開即可續。
  2. **門檻分級而非二值**:CTX 到 90% 才交接太晚——真實節奏是
     ok → should_checkpoint(60%)→ should_converge(85%)→ must_handoff(90%)。
     每級有明確「該做什麼」,呼叫端(patrol/hook/artifact)照級行動。
  3. **resume 必須帶回陷阱**:恢復時若不重述 checkpoint 的 traps,
     接手者會重踩前人踩過的坑——resume 沒有 checkpoint 可依即 fail-closed。

CLI:
    lifecycle.py stage --ctx-pct 62               # 回報分級與建議動作(自報)
    lifecycle.py stage --uuid U [--ctx-pct N]     # G1-10:有機器 CTX 用機器值(標 machine),
                                                  #   否則退回 --ctx-pct(標 self_reported),兩者皆無 rc=2
    lifecycle.py pause --root R --seat s --reason "..." [--checkpoint-required]
    lifecycle.py resume --root R --seat s         # 回最新 checkpoint 摘要+陷阱
    lifecycle.py status --root R --seat s
"""

import argparse
import calendar
import json
import os
import fcntl
import hashlib
import re
import stat
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ledger"))

from rotation import (  # noqa: E402
    ADVISOR_STOP_CTX, CHECKPOINT_SCHEMA, DEFAULT_ROTATE_THRESHOLD,
    RotationError, _validate, checkpoints_file,
)
import ctx_policy  # noqa: E402  (EH.28 門檻單一資料源)


def load_checkpoints(root, seat):
    """該席全部合法 checkpoint,依 at_ms 排序(壞行跳過=回饋不閘門)。

    rotation.latest_checkpoint 需要指定 task;生命週期協定要的是「這個席位
    最後留下什麼」,跨任務,所以在此薄薄補一層而非改動 E6.3 既有 API。"""
    target = _safe_seat_dir(root, seat) / "checkpoints.jsonl"
    entries = []
    if target.is_file():
        # 中1(部分):與 lifecycle 狀態檔一致地拒 symlink;逐層 dirfd/openat
        # 的完整方案列為已知限制(見 docs 與契約 notes)。
        fd = os.open(str(target), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    _validate(entry, CHECKPOINT_SCHEMA, "checkpoint")
                except (ValueError, RotationError):
                    continue
                entries.append(entry)
    # 排序鍵含檔內出現順序(codex 審中5):at_ms 由呼叫端提供,單一未來時戳
    # 會永久壓過後續正常 checkpoint;append 順序是不可偽造的第二鍵。
    ordered = sorted(enumerate(entries), key=lambda pair: (pair[1]["at_ms"], pair[0]))
    return [entry for _, entry in ordered]

LIFECYCLE_VERSION = "0.1.0-draft"

# 分級門檻(ctx_pct 下界 -> 級別);高到低比對,第一個命中即該級。
# ★ v2(the maintainer 2026-08-17 拍板艦長生命週期):在既有 checkpoint/converge/handoff
#   之間補進「立儲/共治/退位/顧問停止」四級,補掉 README:274 懸置的顧問期裁決。
#   既有三級的門檻與語意刻意保留(60/85/90),只在其間與其上加級——舊呼叫端
#   拿到的級名不變,新呼叫端才看得到新級。門檻由高到低必須嚴格遞減。
# EH.28(艦長 09:16 裁):七階全部改讀 policy/ctx-thresholds.json。
# ★ 為什麼不是「留著這份 tuple、只把數字換成常數」:那樣加一階要記得改兩處,
#   而漏掉的那一次**不會有紅燈** —— 少一階不打紅任何東西,只是那一階從此
#   不再觸發,而畫面照樣是綠的。所以整份由 policy 產,tuple 不再手寫。
# ★ `claude_final`(97)刻意不在這裡:它不是段位,是 clear 前的一則建議,
#   由下面 FINAL_CLEAR_ADVICE_FLOOR 那一段承擔(原註解已說明,語意未改)。
STAGES = ctx_policy.stages_descending()

# ---- 97:clear 前的最後一動作(the maintainer 08-18 21:1x 常設規則)---------------------
# 原話:「97% 如果是 claude 就自問『如果 clear,有什麼是下一個 AI 不知道的?』,
#        然後自行最後一次更新多記憶,然後執行 compact。」
#
# ★ 它在 96(advisor_stop:寫遺詔→發新艦長→自行 compact→轉世)**之後、compact 之前**。
# ★★ **限 claude**,而且這個條件要**機器判不是註解**:只有 claude 有 compact 這個動作,
#    codex/agy 沒有——對它們套 97 等於叫它們做一件做不到的事,而且會讓面板/偵測器
#    對著一個永遠達不到的段位報紅。非 claude 一律退回 96 的行為。
#    實作方式刻意是「換一張表」而不是「在判定裡加 if」:預設(engine=None)拿到的表
#    與改動前**逐字相同**,既有呼叫端行為零變化;97 只有明確說出自己是 claude 的人看得到。
# ★★ 設計改版(the maintainer 21:3x,推翻了「進 STAGES」這個前提):
#     「這個 97% 的要做成一個**獨立的功能**,**可以讓我覺得適當時就執行**,
#       **有可能我在 85% 就會開始做了**。」
#   → 進 STAGES = 到了門檻才觸發;他要的是**他判斷時就能執行,不綁 CTX**。
#   所以這裡**只留知識與建議**:叫得出名字、說得出三步、判得出引擎適不適用,
#   但**不代表任何自動動作**。真正的執行入口是另一支獨立指令(派 a seat)。
#   ★ `STAGES` 與 `stage_for` 因此**一字未動**——97 不是段位,是一則建議。
# EH.28:綁自己的鍵 actions.final_clear_advice_floor,**不綁 claude_final(97)**
# —— 「從幾 % 開始建議」與「最後一動作在幾 %」是兩件事,兩個鍵各自存在。
FINAL_CLEAR_ADVICE_FLOOR = ctx_policy.action("final_clear_advice_floor")
CLAUDE_ENGINES = ("claude",)
STAGE_ACTIONS = {
    "ok": {
        "zh-TW": "照常工作",
        "en": "continue working",
    },
    "scout_successor": {
        "zh-TW": "物色繼任者:在同艦主管中挑一位登記為儲君(不得是自己)",
        "en": "scout a successor: register one same-fleet lead as heir (not self)",
    },
    "should_checkpoint": {
        "zh-TW": "打一次 checkpoint(備而不交),含陷阱節",
        "en": "take a checkpoint (kept, not handed over) including traps",
    },
    "co_rule": {
        "zh-TW": "硬性完整總結一次,並讓繼任者開始協助處理事項(共治)",
        "en": "forge one full summary; heir begins co-handling work (co-rule)",
    },
    "should_converge": {
        "zh-TW": "停止接新工,把手上的收斂到可交付",
        "en": "stop taking new work; converge what you hold",
    },
    "must_handoff": {
        "zh-TW": "退位成顧問:交棒鍛造+接手者三句驗收,原主管晉升艦長",
        "en": "abdicate to advisor: forge handoff + heir's 3-sentence acceptance; deputy promoted",
    },
    "advisor_stop": {
        "zh-TW": "顧問停止:對自己做 clear 總結遺詔→發新艦長→自行 compact→轉世為新主管",
        "en": "advisor stop: write clear testament -> deliver to new captain -> compact -> return as new lead",
    },
    "final_clear_prep": {
        # 三步寫成**可執行**的動作,不是心法:每一步都指得出要跑什麼、落在哪裡。
        "zh-TW": ("交棒前置——clear 前最後一動作(限 claude,三步依序;"
                  "**這是建議不是觸發**,何時真的跑由人決定):"
                  "① 自問「如果現在 clear,有什麼是下一個 AI 不知道的?」——"
                  "答案必須落進 testament 帳的「## 下一個 AI 不會知道的事」節"
                  "(rotation.py testament --final-clear);**沒落檔的自問等於沒發生**;"
                  "② 最後一次更新三記憶(AI 記憶 / AIB 記憶 / Obsidian);"
                  "③ 執行 compact"),
        "en": ("final pre-clear action (claude only, in order): "
               "(1) ask 'what would the next AI not know if I cleared now?' and land the "
               "answer in the testament ledger section '下一個 AI 不會知道的事' "
               "(an unrecorded self-question did not happen); "
               "(2) last update of the three memories; (3) run compact"),
    },
}

def is_claude_engine(engine):
    """引擎條件的**唯一**判準。大小寫不敏感;拿不到=不是 claude(fail-closed:
    不確定就不要求一個做不到的動作)。"""
    return isinstance(engine, str) and engine.strip().lower() in CLAUDE_ENGINES


def final_clear_advice(ctx_pct, engine=None, floor=FINAL_CLEAR_ADVICE_FLOOR):
    """交棒前置(clear 前最後一動作)要不要**建議**跑一次。純函式。

    回 {"suggest", "floor", "engine_applicable", "action", "why"}。
    ★ **只建議,不觸發**:回 suggest=True 不代表任何自動動作,也不改變 stage_for 的結果。
      執行入口是獨立指令(the maintainer 21:3x:「可以讓我覺得適當時就執行」),由人決定何時按。
    ★ 引擎條件仍是機器判:只有 claude 有 compact 這個動作。非 claude 回 suggest=False——
      **不是報一個永遠達不到的段位**,那會讓面板/偵測器對著做不到的事一直亮。
    """
    if isinstance(ctx_pct, bool) or not isinstance(ctx_pct, int) \
            or not 0 <= ctx_pct <= 100:
        raise RotationError("ctx_pct must be an integer 0..100: %r" % (ctx_pct,))
    applicable = is_claude_engine(engine)
    over = ctx_pct >= floor
    if not applicable:
        why = "非 claude 引擎(%r):沒有 compact 這個動作,不建議也不報段位" % (engine,)
    elif not over:
        why = "ctx %d%% 未達建議線 %d%%" % (ctx_pct, floor)
    else:
        why = "ctx %d%% ≥ %d%%:可考慮跑交棒前置(何時真的跑由人決定)" % (ctx_pct, floor)
    return {"suggest": applicable and over, "floor": floor,
            # EH.28 一刀半(艦長 09:31;a seat 的 K7 量到 stages.claude_final 零讀者):
            # ★ 先講一件量到的事:**碼裡從來沒有 97**。裁文說「那段 97 改讀 policy」,
            #   而 `97` 在本檔只出現在註解與 the maintainer 的原話裡;函式實際用的是 floor(85)。
            #   所以這裡不是「把寫死的 97 換成讀取」,是**讓那個鍵第一次有讀者**。
            # ★ 為什麼要有讀者:一個沒有人讀的鍵,會讓「policy 鍵集 == 讀者鍵集」
            #   那條判準的一半恆真 —— 它永遠不會因為那個鍵壞掉而紅。
            #   **沒開火不代表性質守得住,代表沒有任何一格在看它。**(艦長 09:29 進帳)
            # ★ 行為零變化:本欄只是把「最後一動作在幾 %」誠實地講出來,
            #   suggest / floor / engine_applicable 三欄逐字未動。
            "final_at": ctx_policy.stage("claude_final"),
            "engine_applicable": applicable,
            "action": STAGE_ACTIONS["final_clear_prep"] if applicable else None,
            "why": why}


_SEAT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

LIFECYCLE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "seat", "state", "at_ms", "reason"],
    "properties": {
        "forced": {"type": "boolean"},
        "schema_version": {"const": LIFECYCLE_VERSION},
        "seat": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{0,31}$"},
        "state": {"type": "string", "enum": ["paused", "active"]},
        "at_ms": {"type": "integer", "minimum": 0},
        "reason": {"type": "string", "minLength": 1, "maxLength": 300},
        "checkpoint_at_ms": {"type": "integer", "minimum": 0},
        "checkpoint_fingerprint": {"type": "string",
                                   "pattern": "^[0-9a-f]{64}$"},
    },
}


def stage_for(ctx_pct):
    """CTX 百分比 -> (級別, 雙語動作)。純函式,好測也好被別人重用。"""
    # bool 是 int 的子類:True 會被當 1% 放行(qa T1,艦長裁收)。
    if isinstance(ctx_pct, bool) or not isinstance(ctx_pct, int) \
            or not 0 <= ctx_pct <= 100:
        raise RotationError("ctx_pct must be an integer 0..100: %r" % (ctx_pct,))
    for floor, name in STAGES:
        if ctx_pct >= floor:
            return name, STAGE_ACTIONS[name]
    raise RotationError("unreachable stage for ctx_pct=%r" % (ctx_pct,))


# 機器 CTX 過期門檻:與 pane-right-ai.sh:236 ctx_of() 同語意(6h=21600s)。
# 各留一份常數,形狀相同由測試看守(避免 import 綁死跨層)。
CTX_STALE_S = 21600
# 未來時戳上界(G1-00 D4,qa 對抗審查):observed_at 比 now 晚超過此秒數=無效。
# 沒有上界時,一個寫成 2099 年的 .pct 會**永遠新鮮**——「時鐘略快」與
# 「偽造未來值」在讀端分不出來,所以只給時鐘漂移留 5 分鐘。
CTX_FUTURE_SLACK_S = 300
# hook 產物的 schema 名(ez2-context-gauge-v1);讀端核此欄與 session_id,
# 是**防誤用/防混入**(別的 JSON 誤放進 ctx 目錄、拷錯 uuid 的檔),不是硬隔離。
CTX_GAUGE_SCHEMA = "ez2-context-gauge-v1"
# .pct 合理大小上界:hook 寫的是一行 JSON(<1KB);超過就不是它寫的。
CTX_FILE_MAX_BYTES = 65536
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# 時戳只收 UTC:`YYYY-MM-DDTHH:MM:SS[.fff…](Z|+00:00)`。
# ★ G1-00 D1(艦長裁定 fail-closed):無尾碼/其他 offset 一律無效,**不猜本地**。
#   51982ab 的「無 Z 走本地」在負時區實測會把 12h 舊值判成新鮮(fail-open),
#   而 hook 從來只寫 Z——放寬只服務手寫測資,代價是真環境的一個洞。
_OBSERVED_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|\+00:00)")


def parse_observed_utc(observed):
    """`observed_at` 字串 -> epoch ms;只收 UTC(Z / +00:00),其餘回 None。純函式。"""
    if not isinstance(observed, str):
        return None
    m = _OBSERVED_RE.fullmatch(observed.strip())
    if not m:
        return None
    try:
        parts = tuple(int(x) for x in m.groups())
        return int(calendar.timegm(parts + (0, 0, 0)) * 1000)
    except (ValueError, OverflowError):
        return None


def read_machine_ctx(uuid, ctx_dir=None, now_ms=None):
    """機器實測 CTX 的**唯一**合法來源:$EZ2_CTX_DIR/<uuid>.pct(hook 產)。

    ★ 為什麼要有這支、且自動輪替只能吃它:rotation 現行 `--ctx-pct` 是**呼叫者
      自報**,而顧問期改 CTX 制之後,自報值等於「AI 自己決定何時可以退場/轉世」
      ——那正是防偽造原設計(拒絕自訂顧問期)要擋的。機器值來自 Claude Code
      statusline/hook 寫的 `context_window.used_percentage`。
    ★ 誠實邊界(G1-00 D2,qa 對抗審查):.pct 與 AI 席位跑在**同一個 uid**,
      席位程序寫得動它;本函式的 schema/session_id/路徑檢查是**防誤用、防混入**
      (別的 JSON、拷錯 uuid 的檔、相對路徑指到別處),**不是硬隔離**。真隔離要
      靠寫端搬到別的 uid 或簽章,列第二刀。

    回 dict{pct, stale, stale_reason, source:'machine', observed_ms} 或 **None**。
    None(=席位沒有可用的機器 CTX,交 rotation_mode 判 manual_only):
      - uuid 格式錯 / ctx_dir 非絕對路徑 / 檔不存在 / 不是 regular file(FIFO
        會把讀者掛死——D3)/ 過大 / 非 JSON 物件 / schema 或 session_id 不符 /
        pct 越界。
    回傳但 stale=True(值在但不能驅動自動轉換,呼叫端仍可誠實顯示):
      - observed_at 缺失或非 UTC 格式(stale_reason=no_observed_at/unparseable)
      - 距 now ≥ CTX_STALE_S(expired)
      - 比 now 晚超過 CTX_FUTURE_SLACK_S(future,D4)
    """
    if not isinstance(uuid, str) or not _UUID_RE.fullmatch(uuid):
        return None
    if ctx_dir is None:
        ctx_dir = os.environ.get("EZ2_CTX_DIR") or \
            os.path.expanduser("~/.ez2/ctx")
    if not isinstance(ctx_dir, str) or not os.path.isabs(ctx_dir):
        return None  # 相對路徑=看呼叫者 cwd 決定讀到誰的檔,拒
    path = os.path.join(ctx_dir, uuid + ".pct")
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        # D3:FIFO/裝置/目錄都不是 hook 寫的檔;FIFO 沒有 O_NONBLOCK 時 open
        # 就掛死、有了 O_NONBLOCK 讀到 EOF 也是假空檔——先看型別再讀。
        if not stat.S_ISREG(info.st_mode) or info.st_size > CTX_FILE_MAX_BYTES:
            return None
        blob = b""
        while len(blob) <= CTX_FILE_MAX_BYTES:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            blob += chunk
        data = json.loads(blob.decode("utf-8"))
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)
    if not isinstance(data, dict):
        return None
    if data.get("schema") != CTX_GAUGE_SCHEMA:
        return None
    if data.get("session_id") != uuid:
        return None  # 檔名 uuid 與內容 session_id 不符=拷錯/混入
    pct = data.get("pct")
    if not isinstance(pct, int) or isinstance(pct, bool) or not 0 <= pct <= 100:
        return None
    observed = data.get("observed_at")
    ref_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    observed_ms = parse_observed_utc(observed)
    if observed is None:
        stale, reason = True, "no_observed_at"
    elif observed_ms is None:
        stale, reason = True, "unparseable"
    elif observed_ms - ref_ms > CTX_FUTURE_SLACK_S * 1000:
        stale, reason = True, "future"
    elif ref_ms - observed_ms >= CTX_STALE_S * 1000:
        stale, reason = True, "expired"
    else:
        stale, reason = False, None
    return {"pct": pct, "stale": stale, "stale_reason": reason,
            "source": "machine", "observed_ms": observed_ms}


def rotation_mode(uuid, ctx_dir=None, now_ms=None):
    """這個席位能不能進**自動**輪替?回 (mode, ctx_or_None)。

    ★ fail-closed(the maintainer 2026-08-17 明示):只有拿到**非 stale 的機器 CTX** 才
      'auto';拿不到或過期一律 'manual_only'——不猜、不用自報值頂替。自報值只
      能走人工流程(operator 明知自己在報)。
    """
    ctx = read_machine_ctx(uuid, ctx_dir=ctx_dir, now_ms=now_ms)
    if ctx is None or ctx["stale"]:
        return "manual_only", ctx
    return "auto", ctx


def _safe_seat_dir(root, seat):
    """所有路徑入口的唯一守門(codex 審高1:驗證只在 CLI 等於沒驗)。

    seat 必須合格式,且解析後的目錄必須真的落在 root 之下——`../escape`
    與絕對路徑片段都在此擋下,Python API 與 CLI 共用同一道門。"""
    if not isinstance(seat, str) or not _SEAT_RE.match(seat):
        raise RotationError("invalid seat: %r" % (seat,))
    base = Path(root).resolve()
    target = (base / seat).resolve()
    if target != base and base not in target.parents:
        raise RotationError("seat path escapes root: %r" % (seat,))
    return target


def lifecycle_path(root, seat):
    return _safe_seat_dir(root, seat) / "lifecycle.jsonl"


def _open_state_fd(path):
    """開狀態檔:拒 symlink、驗 owner、既有檔權限過寬即收緊(codex 審中4)。"""
    flags = (os.O_RDWR | os.O_CREAT | os.O_APPEND
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        fd = os.open(str(path), flags, 0o600)
    except OSError as err:
        raise RotationError("lifecycle state unreadable: %s (%s)" % (path, err))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RotationError("lifecycle state must be a regular file: %s" % path)
        if info.st_uid != os.getuid():
            raise RotationError("lifecycle state owner mismatch: %s" % path)
        if stat.S_IMODE(info.st_mode) & 0o077:
            os.fchmod(fd, 0o600)  # 既有檔案權限過寬:收緊而非沿用
        return fd
    except Exception:
        os.close(fd)
        raise


def _record_fingerprint(record):
    """記錄指紋=該筆 lifecycle 記錄的 canonical sha256(CAS 的 generation)。"""
    if record is None:
        return "genesis"
    canon = json.dumps(record, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _transition(root, seat, record, expect_states=None, expect_fingerprint=None):
    """狀態轉移=鎖內 compare-and-swap(codex 審高4)。

    併發 pause/resume 若各自「先讀舊狀態再追加」,最終狀態取決於寫入順序;
    這裡在 flock 內重讀當下最後一筆再決定放不放行,轉移才是可驗證的。"""
    _validate(record, LIFECYCLE_SCHEMA, "lifecycle record")
    path = lifecycle_path(root, seat)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = _open_state_fd(path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        current = _last_record_from_fd(fd)
        if expect_states is not None:
            state = (current or {}).get("state", "active")
            if state not in expect_states:
                raise RotationError(
                    "illegal transition: seat %r is %r, expected one of %s"
                    % (seat, state, sorted(expect_states)))
        if expect_fingerprint is not None:
            # 高1(ABA):只看 state 會讓「resume A -> pause B」之間的舊 resume
            # 通過並錯誤啟用 B。指紋把轉移綁在「我讀到的那一筆」上。
            actual = _record_fingerprint(current)
            if actual != expect_fingerprint:
                raise RotationError(
                    "lifecycle changed under us (ABA guard): expected %s, found %s"
                    % (expect_fingerprint[:12], actual[:12]))
        line = json.dumps(record, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
        os.lseek(fd, 0, os.SEEK_END)
        payload = (line + "\n").encode("utf-8")
        view = memoryview(payload)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)
    return record


def _last_record_from_fd(fd):
    """鎖內讀最後一筆合法記錄;壞行跳過(與 checkpoint 一致的容忍策略,中3)。"""
    os.lseek(fd, 0, os.SEEK_SET)
    blob = b""
    while True:
        chunk = os.read(fd, 1 << 16)
        if not chunk:
            break
        blob += chunk
    last = None
    for line in blob.decode("utf-8", "replace").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            _validate(entry, LIFECYCLE_SCHEMA, "lifecycle record")
        except (ValueError, RotationError):
            continue
        last = entry
    return last


def current_state(root, seat):
    """最後一筆**合法**記錄;壞行跳過(中3:與 checkpoint 一致的容忍策略),
    無記錄=active(預設可工作)。"""
    path = lifecycle_path(root, seat)
    if not path.exists():
        return {"seat": seat, "state": "active", "at_ms": None,
                "reason": "no lifecycle record"}
    fd = _open_state_fd(path)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        last = _last_record_from_fd(fd)
    finally:
        os.close(fd)
    return last or {"seat": seat, "state": "active", "at_ms": None,
                    "reason": "no valid lifecycle record"}


def pause(root, seat, reason, now_ms=None, forced=False):
    """暫停席位。require_checkpoint=True 時,沒有 checkpoint 就拒絕暫停。

    理由:無 checkpoint 的暫停等於「什麼都沒留就消失」,resume 時無從接回
    ——這正是 08-09 四席全滅時,有 checkpoint 的席位能續、沒有的只能重來。
    """
    cps = load_checkpoints(root, seat)
    # 中2:單一開關。forced 是唯一的繞過入口,且必然留稽核痕跡——
    # 舊的 require_checkpoint 參數可在不設 forced 時繞過契約,已移除。
    if forced and not str(reason).startswith("admin:"):
        # 中1:繞過契約必須留痕且看得出是誰在繞——理由要自報 admin: 前綴,
        # 事件本身帶 forced=true,審計時一眼可篩。
        raise RotationError(
            "forced pause requires an admin-prefixed reason "
            "(e.g. --reason 'admin: 事故處置 …')")
    if not forced and not cps:
        raise RotationError(
            "seat %r has no checkpoint: refusing to pause "
            "(a pause without a checkpoint cannot be resumed)" % seat)
    record = {
        "schema_version": LIFECYCLE_VERSION,
        "seat": seat,
        "state": "paused",
        "at_ms": int(now_ms if now_ms is not None else time.time() * 1000),
        "reason": reason,
    }
    if cps:
        record["checkpoint_at_ms"] = int(cps[-1]["at_ms"])
        # 高2:同 at_ms 的兩筆 checkpoint 會讓「取第一筆」恢復到錯的工作;
        # 指紋(含 task 與全部內容)不可碰撞。
        record["checkpoint_fingerprint"] = _record_fingerprint(cps[-1])
    if forced:
        record["forced"] = True
    return _transition(root, seat, record, expect_states={"active"})


def resume(root, seat, now_ms=None):
    """恢復席位,並回傳「接回工作所需的最小事實」。

    沒有 checkpoint 就 fail-closed:resume 的價值全在於帶回陷阱與未竟項,
    否則只是把旗標翻回來,接手者照樣重踩坑。
    """
    cps = load_checkpoints(root, seat)
    if not cps:
        raise RotationError(
            "seat %r has no checkpoint to resume from (fail-closed)" % seat)
    state = current_state(root, seat)
    if state.get("state") != "paused":
        # 高2:未 pause 或已 active 的席位不得 resume,否則轉移不是狀態機
        raise RotationError(
            "seat %r is %r, only a paused seat can resume"
            % (seat, state.get("state", "active")))
    # 高3:接回 pause 當下綁定的那一份,不是「全域最新」——pause 後若有別的
    # 任務又寫了 checkpoint,取最新會恢復到不相干的工作。
    bound_fp = state.get("checkpoint_fingerprint")
    bound_ms = state.get("checkpoint_at_ms")
    latest = None
    if bound_fp is not None:
        for entry in cps:
            if _record_fingerprint(entry) == bound_fp:
                latest = entry
                break
        if latest is None:
            raise RotationError(
                "checkpoint bound by pause (fingerprint %s) is missing "
                "(fail-closed)" % bound_fp[:12])
    elif bound_ms is not None:  # 舊記錄相容:無指紋時退回時間比對
        for entry in cps:
            if int(entry["at_ms"]) == int(bound_ms):
                latest = entry
                break
        if latest is None:
            raise RotationError(
                "checkpoint bound by pause (at_ms=%s) is missing (fail-closed)"
                % bound_ms)
    else:
        latest = cps[-1]
    _validate(latest, CHECKPOINT_SCHEMA, "checkpoint")
    record = {
        "schema_version": LIFECYCLE_VERSION,
        "seat": seat,
        "state": "active",
        "at_ms": int(now_ms if now_ms is not None else time.time() * 1000),
        "reason": "resume from checkpoint",
        "checkpoint_at_ms": int(latest["at_ms"]),
    }
    _transition(root, seat, record, expect_states={"paused"},
                expect_fingerprint=_record_fingerprint(state
                                                       if state.get("at_ms") is not None
                                                       else None))
    stage, action = stage_for(int(latest["ctx_pct"]))
    return {
        "seat": seat,
        "state": "active",
        "resumed_from_ms": int(latest["at_ms"]),
        "task": latest["task"],
        "ctx_pct": latest["ctx_pct"],
        "stage": stage,
        "action": action,
        "not_done": list(latest["not_done"]),
        "traps": list(latest["traps"]),
        "next_step": latest["next_step"],
    }


def status(root, seat):
    state = current_state(root, seat)
    cps = load_checkpoints(root, seat)
    out = {"seat": seat, "state": state.get("state", "active"),
           "since_ms": state.get("at_ms"), "reason": state.get("reason"),
           "checkpoint_count": len(cps)}
    if cps:
        latest = cps[-1]
        stage, action = stage_for(int(latest["ctx_pct"]))
        out.update({"latest_checkpoint_ms": int(latest["at_ms"]),
                    "task": latest["task"], "ctx_pct": latest["ctx_pct"],
                    "stage": stage, "action": action,
                    "trap_count": len(latest["traps"])})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lifecycle",
                                 description="Ez2 AIB 席位生命週期協定(E6.1)")
    ap.add_argument("command",
                    choices=["stage", "pause", "resume", "status"])
    ap.add_argument("--root")
    ap.add_argument("--seat")
    ap.add_argument("--ctx-pct", type=int)
    ap.add_argument("--reason")
    ap.add_argument("--now-ms", type=int)
    ap.add_argument("--uuid", help="stage 用:席位 session uuid,自動取機器 CTX")
    ap.add_argument("--force-admin", action="store_true",
                    help="管理用:無 checkpoint 仍暫停;需 --reason 以 'admin:' 開頭,"
                         "記錄帶 forced=true 供審計(預設拒絕)")
    args = ap.parse_args(argv)

    try:
        if args.command == "stage":
            # G1-10:機器值優先(非 stale),否則自報並標明,兩者皆無 rc=2。
            mode, machine = ("manual_only", None)
            if args.uuid:
                mode, machine = rotation_mode(args.uuid, now_ms=args.now_ms)
            if mode == "auto":
                pct, source = machine["pct"], "machine"
            elif args.ctx_pct is not None:
                pct, source = args.ctx_pct, "self_reported"
            else:
                raise RotationError(
                    "stage requires --ctx-pct or a uuid with fresh machine ctx "
                    "(rotation_mode=%s)" % mode)
            name, action = stage_for(pct)
            print(json.dumps({"ctx_pct": pct, "ctx_source": source,
                              "rotation_mode": mode, "seat": args.seat,
                              "stage": name, "action": action},
                             ensure_ascii=False, sort_keys=True))
            return 0
        if not args.root or not args.seat:
            raise RotationError("%s requires --root and --seat" % args.command)
        if not _SEAT_RE.match(args.seat):
            raise RotationError("invalid seat: %r" % args.seat)
        if args.command == "pause":
            if not args.reason:
                raise RotationError("pause requires --reason")
            rec = pause(args.root, args.seat, args.reason, args.now_ms,
                        forced=args.force_admin)
            print(json.dumps(rec, ensure_ascii=False, sort_keys=True))
        elif args.command == "resume":
            print(json.dumps(resume(args.root, args.seat, args.now_ms),
                             ensure_ascii=False, sort_keys=True))
        else:
            print(json.dumps(status(args.root, args.seat),
                             ensure_ascii=False, sort_keys=True))
        return 0
    except RotationError as err:
        print("lifecycle: %s" % err, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
