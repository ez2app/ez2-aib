# SPDX-License-Identifier: Apache-2.0
"""滑鼠命中判定(E2.13)。

純函數:給一次點擊的座標與「上一次畫面畫了什麼」的落點檔,回傳要做的動作。
不碰 tmux、不讀時鐘、不猜 —— 座標之外的判斷全部來自渲染當下寫下的落點檔
(tab-spans.json / ai-rowmap.json),所以點擊永遠作用在使用者實際看到的那一幀,
而不是我們以為畫面長什麼樣。

三種結果:
  {"action": "set-tab",    "tab": N}        點在底部分頁列的某個標籤上
  {"action": "focus-hub",  "window": "..."} 點在 AI 列表的某個 AI(含其子 agent 行)
  {"action": "passthrough"}                 沒點到任何互動目標

passthrough 是預設值。判不出來就不動作 —— 滑鼠是使用者的手,寧可沒反應,
也不要跳去一個他沒要去的地方。
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import frame_snapshot as FS  # noqa: E402

PASSTHROUGH = {"action": "passthrough"}
# 274 右欄收合/捲動(規格 docs/spec/RIGHT-FLEET-FOLD-v1.md):verb 字面與 render_lib 同一份
FOLD_TOGGLE_ACTION = "fold-toggle"
FOLD_SCROLL_ACTION = "fold-scroll"
FOLD_ALL_ACTIONS = frozenset(("fold-expand-all", "fold-collapse-all"))
LEFTOVER_CLOSE_ACTION = "leftover-close"          # leftover_seats.CLOSE_ACTION
LEFTOVER_CLOSE_ALL_ACTION = "leftover-close-all"  # leftover_seats.CLOSE_ALL_ACTION
LEFTOVER_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}\|@\d{1,6}$")   # <艙 id>|@<窗>
FOLD_KEY_RE = re.compile(r"^(?:[0-9a-z]{5}|fleet:[0-9]{1,4})$")


def on_usage_arrow(rowmap, y, x):
    """右緣上下箭頭。按下時不要把滑鼠序列送進窗格，也不要當成捲軸拖。"""
    try:
        y = int(y)
        x = int(x)
    except (TypeError, ValueError):
        return False
    for entry in rowmap or []:
        if not isinstance(entry, dict) or entry.get("line") != y:
            continue
        if entry.get("usage_scroll") not in ("up", "down"):
            continue
        try:
            if int(entry.get("start")) <= x < int(entry.get("end")):
                return True
        except (TypeError, ValueError, KeyError):
            continue
    return False


def on_scrollbar(rowmap, y, x):
    """這一點是不是畫出來的捲軸欄。thumb 也算，呼叫端不得再把序列送進窗格。"""
    try:
        y = int(y)
        x = int(x)
    except (TypeError, ValueError):
        return False
    for entry in rowmap or []:
        if not isinstance(entry, dict) or entry.get("line") != y:
            continue
        bar = entry.get("bar")
        if not isinstance(bar, dict):
            continue
        try:
            if x >= int(bar["col"]):
                return True
        except (TypeError, ValueError, KeyError):
            continue
    return False


def scrollbar_lines(rowmap):
    lines = []
    for entry in rowmap or []:
        if isinstance(entry, dict) and isinstance(entry.get("bar"), dict):
            try:
                lines.append(int(entry["line"]))
            except (TypeError, ValueError):
                continue
    return lines


def scrollbar_target_offset(rowmap, y, limit):
    """把游標的列對到捲動範圍。最上是 0，最下是 limit。"""
    lines = scrollbar_lines(rowmap)
    if not lines:
        return None
    try:
        y = int(y)
        limit = max(0, int(limit))
    except (TypeError, ValueError):
        return None
    top, bottom = min(lines), max(lines)
    span = bottom - top
    if span < 1:
        return 0 if y <= top else limit
    frac = (y - top) / float(span)
    if frac < 0:
        frac = 0.0
    elif frac > 1:
        frac = 1.0
    return int(round(frac * limit))


def hit_tabs(spans, x):
    """spans=[{index,start,end}](半開區間,顯示格為單位);x=pane 內 0 起算的欄。"""
    try:
        x = int(x)
    except (TypeError, ValueError):
        return PASSTHROUGH
    for s in spans or []:
        if s.get("start", 0) <= x < s.get("end", 0):
            if s.get("kind") == "cabin":
                return {"action": "copy-cabin", "cabin": str(s.get("cabin") or "")}
            try:
                return {"action": "set-tab", "tab": int(s["index"])}
            except (TypeError, ValueError):
                return PASSTHROUGH
    return PASSTHROUGH


def hit_ai_rows(rowmap, y, x=None):
    """rowmap=[{line,window[,kill]}];y=pane 內 0 起算的列。

    08-14 the maintainer 直令[X]:kill span(恰三格)內的點=閒置釋放;span 外
    照舊跳窗——面板首個破壞性點擊,精準命中即第二道安全欄(第一道=
    閒置門檻,工作中根本沒有 span)。
    """
    try:
        y = int(y)
    except (TypeError, ValueError):
        return PASSTHROUGH
    # 用量頁右緣的上下箭頭：點上往上拉、點下往下拉。先於標題的隱藏門。
    for r in rowmap or []:
        if r.get("line") != y:
            continue
        direction = r.get("usage_scroll")
        if direction not in ("up", "down"):
            continue
        try:
            xi = int(x)
            if int(r.get("start")) <= xi < int(r.get("end")):
                rows = int(r.get("rows") or 6)
                if rows < 1:
                    rows = 6
                tab_name = r.get("scroll_tab") or "usage"
                if tab_name not in ("ai", "usage", "left"):
                    tab_name = "usage"
                return {"action": FOLD_SCROLL_ACTION, "dir": direction,
                        "rows": rows, "tab": tab_name}
        except (TypeError, ValueError, KeyError):
            continue
    # 274 捲軸:最右 1 欄與席列同一行,先看它(同一行的席列在後面會回 focus-hub);
    #   thumb 上不動、上方 −H、下方 +H(rowmap 給 zone,這裡不重算)
    for r in rowmap or []:
        if r.get("line") == y and isinstance(r.get("bar"), dict):
            try:
                xi = int(x)
                bar = r["bar"]
                if xi >= int(bar["col"]):
                    zone = str(bar.get("zone") or "")
                    if zone in ("up", "down"):
                        return {"action": FOLD_SCROLL_ACTION, "dir": zone, "rows": int(bar.get("page") or 0),
                                "epoch": int(r.get("epoch") or 0)}
                    return PASSTHROUGH
            except (TypeError, ValueError, KeyError):
                return PASSTHROUGH
    for r in rowmap or []:
        links = r.get("links")
        if r.get("line") == y and isinstance(links, list) and not r.get("window"):
            # 用量列:只有 link span 內算命中,其餘 passthrough(這種列沒有窗可跳)。
            try:
                xi = int(x)
            except (TypeError, ValueError):
                return PASSTHROUGH
            for link in links:
                if not isinstance(link, dict):
                    continue
                try:
                    ls, le = int(link["start"]), int(link["end"])
                except (TypeError, ValueError, KeyError):
                    continue
                if not (ls <= xi < le):
                    continue
                engine = _safe_text(link.get("engine"), 16)
                action = link.get("action")
                if action == LEFTOVER_CLOSE_ACTION:                 # 09-30 右欄第 3 頁「離艙」的 <X>
                    key = _safe_text(link.get("key"), 80)
                    if LEFTOVER_KEY_RE.fullmatch(key):
                        return {"action": LEFTOVER_CLOSE_ACTION, "key": key,
                                "label": _safe_text(link.get("label"), 48)}
                    return PASSTHROUGH
                if action == LEFTOVER_CLOSE_ALL_ACTION:             # 同頁的 <全收>
                    try:
                        count = max(0, int(link.get("count") or 0))
                    except (TypeError, ValueError):
                        count = 0
                    return {"action": LEFTOVER_CLOSE_ALL_ACTION, "count": count}
                if action == USAGE_LINK_ACTION and USAGE_PAGES.get(engine):
                    return {"action": USAGE_LINK_ACTION, "engine": engine,
                            "url": USAGE_PAGES[engine]}
                if action == USAGE_REPROBE_ACTION and engine in REPROBE_ENGINES:
                    return {"action": USAGE_REPROBE_ACTION, "engine": engine}
                if action == USAGE_HEADER_TAP_ACTION:
                    return {"action": USAGE_HEADER_TAP_ACTION}
                if action == USAGE_SUBTAB_ACTION:
                    sub = _safe_text(link.get("arg") or link.get("subtab"), 16)
                    if sub in ("usage", "codex", "用量"):
                        if sub == "用量":
                            sub = "usage"
                        return {"action": USAGE_SUBTAB_ACTION, "subtab": sub}
                if action == USAGE_ADD_CODEX_OAUTH_ACTION:
                    return {"action": USAGE_ADD_CODEX_OAUTH_ACTION}
                if action == USAGE_ADD_CODEX_TOKEN_ACTION:
                    return {"action": USAGE_ADD_CODEX_TOKEN_ACTION}
                if action == USAGE_REPROBE_ACCOUNT_ACTION:
                    acct = _safe_text(link.get("account_id"), 32)
                    if acct:
                        return {"action": USAGE_REPROBE_ACCOUNT_ACTION, "account_id": acct}
                if action == USAGE_REBIND_CODEX_OAUTH_ACTION:
                    acct = _safe_text(link.get("account_id"), 32)
                    if acct:
                        out = {"action": USAGE_REBIND_CODEX_OAUTH_ACTION, "account_id": acct}
                        em = _safe_text(link.get("oauth_account"), 120)
                        if em:
                            out["oauth_account"] = em
                        return out
                if action == USAGE_MOVE_ACCOUNT_UP_ACTION:
                    acct = _safe_text(link.get("account_id"), 32)
                    if acct:
                        return {"action": USAGE_MOVE_ACCOUNT_UP_ACTION, "account_id": acct}
                if action == USAGE_MOVE_ACCOUNT_DOWN_ACTION:
                    acct = _safe_text(link.get("account_id"), 32)
                    if acct:
                        return {"action": USAGE_MOVE_ACCOUNT_DOWN_ACTION, "account_id": acct}
                if action == USAGE_ACTIVATE_ACCOUNT_ACTION:
                    acct = _safe_text(link.get("account_id"), 32)
                    if acct:
                        return {"action": USAGE_ACTIVATE_ACCOUNT_ACTION, "account_id": acct}
                if action == USAGE_ACTIVATE_NEXT_ACTION:
                    return {"action": USAGE_ACTIVATE_NEXT_ACTION}
                if action == USAGE_REMOVE_ACCOUNT_ACTION:
                    acct = _safe_text(link.get("account_id"), 32)
                    if acct:
                        out = {"action": USAGE_REMOVE_ACCOUNT_ACTION, "account_id": acct}
                        em = _safe_text(link.get("oauth_account"), 120)
                        if em:
                            out["oauth_account"] = em
                        seq = _safe_text(link.get("seq_label"), 8)
                        if seq:
                            out["seq_label"] = seq
                        return out

                if action in QUEUE_ACTIONS:
                    seat = _safe_text(link.get("seat"), 8)
                    item = _safe_text(link.get("item"), 32)
                    if SEAT_RE.fullmatch(seat) and ITEM_RE.fullmatch(item):
                        return {"action": action, "seat": seat, "item": item}
                if action in FOLD_ALL_ACTIONS:                      # 274 標題列兩鈕
                    return {"action": action, "epoch": int(r.get("epoch") or 0)}
            return PASSTHROUGH
        if r.get("line") == y and r.get("window"):
            kill = r.get("kill")
            if isinstance(kill, dict):
                try:
                    xi = int(x)
                    ks, ke = int(kill["start"]), int(kill["end"])
                except (TypeError, ValueError, KeyError):
                    xi = None
                else:
                    if ks <= xi < ke and r.get("kill_pending"):
                        # 連點去抖(08-14 真人實證:列要等下一幀才消失,
                        # 沒回饋就再按,@8 被連按四次)。已按下待確認的列
                        # =**完全不可點**(不是改跳窗:破壞性入口附近不給
                        # 意外動作);下一幀 rowmap 重發自然解除。
                        return PASSTHROUGH
                    if ks <= xi < ke:
                        return _kill_action(r, kill) if _kill_target_ok(r, kill) else PASSTHROUGH
            # L2-03:第二/三行的綁定入口(spans,與 kill 同機制:精準命中才算,
            # 行內其他處照舊跳窗)。action 白名單在這裡收口——rowmap 是渲染
            # 產物,但 mouse-ctl 只認這四個 verb,別的一律當沒點到。
            for s in r.get("spans") or []:
                if not isinstance(s, dict):
                    continue
                try:
                    xi = int(x)
                    ss, se = int(s["start"]), int(s["end"])
                except (TypeError, ValueError, KeyError):
                    continue
                if ss <= xi < se and s.get("action") in BIND_ACTIONS:
                    seat = _safe_text(s.get("seat"), 8)
                    if not SEAT_RE.fullmatch(seat):
                        continue
                    detail = r.get("detail")
                    detail = detail if isinstance(detail, dict) else {}
                    return {"action": str(s["action"]),
                            "window": str(r["window"]),
                            "seat": seat,
                            "label": _safe_text(detail.get("label"), 48)}
            # 274:摺疊列([X] 與綁定 span 之外)⇒ 切換收合,不跳窗(規格 §1、驗收 C1)
            fold = r.get("fold")
            if isinstance(fold, str) and FOLD_KEY_RE.fullmatch(fold):
                return {"action": FOLD_TOGGLE_ACTION, "key": fold, "window": str(r["window"]),
                        "epoch": int(r.get("epoch") or 0)}
            return {"action": "focus-hub", "window": str(r["window"])}
    return PASSTHROUGH


# the maintainer 09-06 13:3x 裁:用量列點 LOGO/品牌名 ⇒ 開該家用量頁。表在這裡收口(rowmap 只帶 engine,
# URL 不從渲染產物來);沒登記的家一律當沒點到。claude/codex 是 the maintainer 截圖的頁,其餘三家是
# 該家帳務/用量入口的現行頁,改頁在這張表改。
USAGE_PAGES = {
    "claude": "https://claude.ai/settings/usage",
    "codex": "https://chatgpt.com/#settings/Usage",
    "copilot": "https://github.com/settings/billing/summary",
    "agy": "https://antigravity.google/",
    "grok": "https://grok.com/",
}
USAGE_LINK_ACTION = "open-usage-page"
# the maintainer 09-06 13:5x 裁:過期列的 [重驗] ⇒ 只對那一家重探。白名單=會起 CLI 的三家。
USAGE_REPROBE_ACTION = "reprobe-usage"
USAGE_SUBTAB_ACTION = "usage-subtab"  # usage-codex-subtab-0922
REPROBE_ENGINES = ("claude", "codex", "agy", "copilot", "grok")
USAGE_HEADER_TAP_ACTION = "usage-header-tap"
USAGE_ADD_CODEX_OAUTH_ACTION = "usage-add-codex-oauth"
USAGE_ADD_CODEX_TOKEN_ACTION = "usage-add-codex-token"
USAGE_REPROBE_ACCOUNT_ACTION = "usage-reprobe-account"
USAGE_REBIND_CODEX_OAUTH_ACTION = "usage-rebind-codex-oauth"
USAGE_MOVE_ACCOUNT_UP_ACTION = "usage-move-account-up"
USAGE_MOVE_ACCOUNT_DOWN_ACTION = "usage-move-account-down"
USAGE_ACTIVATE_ACCOUNT_ACTION = "usage-activate-account"
USAGE_ACTIVATE_NEXT_ACTION = "usage-activate-next"
USAGE_REMOVE_ACCOUNT_ACTION = "usage-remove-account"

# AR-3(2026-09-02):[換大腦] 走 change-brain-menu —— 同一個大腦選單層,但選單第一項固定
# 「解除綁定」(→ 既有 unbind-brain 確認);[綁大腦] 仍走 bind-brain-menu(沒綁過的不列解除項)。
BIND_ACTIONS = ("bind-persona-menu", "bind-brain-menu", "change-brain-menu",
                "unbind-persona", "unbind-brain")
SEAT_RE = re.compile(r"[0-9a-z]{4,8}")
ITEM_RE = re.compile(r"q_[0-9a-f]{16}")
QUEUE_ACTIONS = (
    "dm-queue-send-now", "dm-queue-edit", "dm-queue-delete",
    "dm-queue-up", "dm-queue-down", "dm-queue-move",
)
PENDING_ACTIONS = QUEUE_ACTIONS + (
    "pending-focus", "pending-unfocus",
    "pending-scroll-up", "pending-scroll-down",
    "pending-visible-cycle", "dialog-visible-cycle", "realtime-visible-cycle", "input-visible-cycle", "dialog-scroll-up", "dialog-scroll-down", "pending-new",
    "pending-global-toggle", "pending-global-visible-cycle", "hub-io-toggle",
    "feature-tab",
    "inbox-tab",          # 第 70 項 P4:待審區五 tab(線 0 右半)的命中區;tab 0..4
)
TASK_ZONE_FOCUS = "task-zone-focus"   # 第 68 項:任務頁本體(沒有 link 的地方)點下去 ⇒ 進 task-nav 表


def hit_pending_rows(rowmap, y, x=None):
    """待審框落點:只認 PENDING_ACTIONS,不走跳窗/[X]/綁定。"""
    try:
        y = int(y)
        xi = int(x)
    except (TypeError, ValueError):
        return PASSTHROUGH
    for r in rowmap or []:
        if not isinstance(r, dict):
            continue
        if r.get("line") != y and r.get("y") != y:
            continue
        for link in r.get("links") or []:
            if not isinstance(link, dict):
                continue
            try:
                ls, le = int(link["start"]), int(link["end"])
            except (TypeError, ValueError, KeyError):
                continue
            if not (ls <= xi < le):
                continue
            action = link.get("action")
            if action not in PENDING_ACTIONS:
                continue
            seat = _safe_text(link.get("seat"), 8)
            if not SEAT_RE.fullmatch(seat):
                continue
            if action == "feature-tab":
                try:
                    tab = int(link.get("tab"))
                except (TypeError, ValueError):
                    continue
                if tab not in (0, 1):
                    continue
                return {"action": action, "seat": seat, "tab": tab}
            if action == "inbox-tab":
                try:
                    tab = int(link.get("tab"))
                except (TypeError, ValueError):
                    continue
                if tab not in (0, 1, 2, 3, 4):     # 與 render_lib.INBOX_TABS 五格同長;越界不猜
                    continue
                return {"action": action, "seat": seat, "tab": tab}
            if action in ("pending-unfocus", "pending-scroll-up",
                          "pending-scroll-down", "pending-visible-cycle", "dialog-visible-cycle", "realtime-visible-cycle", "input-visible-cycle",
                          "pending-new", "pending-global-toggle",
                          "pending-global-visible-cycle", "hub-io-toggle"):
                return {"action": action, "seat": seat}
            item = _safe_text(link.get("item"), 32)
            if not ITEM_RE.fullmatch(item):
                continue
            out = {"action": action, "seat": seat, "item": item}
            # 第 36 項:那一幀畫的修訂號一起帶走(確認回呼用);不是整數就不帶,不猜
            rev = _safe_text(link.get("rev"), 12)
            if rev.isdigit():
                out["rev"] = int(rev)
            return out
        return PASSTHROUGH
    return PASSTHROUGH



HUB_FRAME_ACTIONS = (
    "dialog-visible-cycle", "realtime-visible-cycle", "input-visible-cycle",
    "hub-io-toggle",
    "dialog-scroll-up",
    "dialog-scroll-down",
)


def hit_hub_frame_rows(rowmap, y, x=None):
    """hub-frame 落點：對話區 [≡N] / 捲軸。不強制 seat（對話框本身無席碼）。"""
    try:
        y = int(y)
        xi = int(x)
    except (TypeError, ValueError):
        return PASSTHROUGH
    for r in rowmap or []:
        if not isinstance(r, dict):
            continue
        if r.get("line") != y and r.get("y") != y:
            continue
        for link in r.get("links") or []:
            if not isinstance(link, dict):
                continue
            try:
                ls, le = int(link["start"]), int(link["end"])
            except (TypeError, ValueError, KeyError):
                continue
            if not (ls <= xi < le):
                continue
            action = link.get("action")
            if action not in HUB_FRAME_ACTIONS:
                continue
            seat = _safe_text(link.get("seat"), 8)
            out = {"action": action}
            if seat and SEAT_RE.fullmatch(seat):
                out["seat"] = seat
            return out
        return PASSTHROUGH
    return PASSTHROUGH


def _feature_tab_of(layout):
    try:
        return 1 if int(str(layout.get("pending_feature_tab") or "0").strip()) == 1 else 0
    except (TypeError, ValueError):
        return 0


# 雙向覆寫 / 零寬字元:選單標題會照字面畫出來,U+202E 能把「dev1」畫成反向、零寬字能讓兩個標題看起來一樣。
#   ZWJ(U+200D)留著:組合表情符號要靠它,單獨出現也畫不出東西。
_INVISIBLE = frozenset([0x200B, 0x200C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
                        0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0x2066, 0x2067, 0x2068, 0x2069, 0xFEFF])


def _safe_text(value, limit=96):
    text = str(value or "")
    text = "".join("" if ord(ch) in _INVISIBLE else (ch if ch >= " " and ch != "\x7f" else " ") for ch in text)
    return " ".join(text.split())[:limit]


def _kill_action(r, kill):
    """08-14 the maintainer 新令:[X] 改彈確認選單(推翻同日單擊直殺)。選單標題要讓人看清**殺的是哪一個**,
    故命中回傳帶身分欄(label=角色NN@席位五碼、status=💤閒置);兩欄取自同一 rowmap 條目的 detail,
    與畫面同源(不另查、不另猜)。外部字串走既有 _safe_text 消毒後才進選單。"""
    detail = r.get("detail")
    detail = detail if isinstance(detail, dict) else {}
    return {"action": "kill-idle",
            "window": str(r["window"]),
            "label": _safe_text(detail.get("label"), 48),
            "status": _safe_text(detail.get("status"), 16),
            # 10-24:(x) 軟關(未達 [X] 門檻)—— 執行端改問「選單開出後有沒有再動」
            "soft": kill.get("soft") is True}


WINDOW_ID_RE = re.compile(r"@[0-9]{1,10}")      # 跟產生端 pane-right-ai.sh 的 WID_RE 同寬


def _kill_target_ok(r, kill):
    """左鍵 [X] 與右鍵「關閉這一席」同一套前提:畫面上真的有那一格(start < end)、窗 id 是 tmux 的 @數字。"""
    try:
        if not int(kill["start"]) < int(kill["end"]):
            return False
    except (TypeError, ValueError, KeyError):
        return False
    return bool(WINDOW_ID_RE.fullmatch(str(r.get("window"))))


def hit_ai_close(rowmap, y):
    """10-24 @abb89:右鍵「關閉這一席」。只給畫面上那一列本來就有 [X] / (x) 的席(rowmap 有 kill),
    回跟點 [X] 一模一樣的動作 ⇒ 走同一個確認選單與 release-confirmed 閘;不另開一條關法。
    已按下待確認的列(kill_pending)一樣不可點。"""
    try:
        y = int(y)
    except (TypeError, ValueError):
        return PASSTHROUGH
    for r in rowmap or []:
        if r.get("line") != y or not r.get("window"):
            continue
        kill = r.get("kill")
        if not isinstance(kill, dict) or r.get("kill_pending"):
            return PASSTHROUGH
        return _kill_action(r, kill) if _kill_target_ok(r, kill) else PASSTHROUGH
    return PASSTHROUGH


def hit_ai_detail(rowmap, y):
    """Return the detail attached by the same render pass as the clicked row."""
    try:
        y = int(y)
    except (TypeError, ValueError):
        return PASSTHROUGH
    for row in rowmap or []:
        if row.get("line") != y or not row.get("window"):
            continue
        source = row.get("detail")
        if not isinstance(source, dict):
            return PASSTHROUGH
        detail = {key: _safe_text(source.get(key))
                  for key in ("kind", "label", "engine", "context", "status")}
        if not detail["label"]:
            return PASSTHROUGH
        return {
            "action": "show-ai-menu",
            "window": _safe_text(row["window"], 128),
            "detail": detail,
        }
    return PASSTHROUGH


PERF_TAB_INDEX = 3          # pane-bottom-tabs labels 序:0指令/1進度/2對話/3效能
PERF_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:/._\-]{0,63}")


def hit_perf_rows(doc, y, x, visible):
    """效能頁 [X](E 面板第二片,the maintainer 08-18 核准)。doc=perf-spans.json
    ({schema,lines,spans},perf_tab_fmt --spans 落盤,座標=cell);
    y=快取行號(呼叫端已扣表頭);visible=本幀實際畫出的行數——**渲染端
    head 的同源數字**。檔案裡有、畫面上沒有(被 head 裁掉)=隱形點擊區,
    一律 PASSTHROUGH;visible 不明(舊渲染器沒寫)同樣 fail closed。
    verb 只有兩個:perf-close(單目標,id 過白名單 regex 才放行)/
    perf-reap(聚合,不帶 id——全關走 reap 同一道閘,判準不在點擊層)。"""
    if not isinstance(doc, dict) or doc.get("schema") != "aib-perf-spans/v1":
        return PASSTHROUGH
    try:
        y, x, visible = int(y), int(x), int(visible)
    except (TypeError, ValueError):
        return PASSTHROUGH
    if not 0 <= y < visible:
        return PASSTHROUGH
    for sp in doc.get("spans") or []:
        if not isinstance(sp, dict) or sp.get("line") != y:
            continue
        try:
            ss, se = int(sp["x_span"][0]), int(sp["x_span"][1])
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        if not ss <= x < se:
            continue
        if sp.get("kind") == "agg":
            ids = [i for i in (sp.get("ids") or [])
                   if isinstance(i, str) and PERF_ID_RE.fullmatch(i)]
            if not ids:
                return PASSTHROUGH
            return {"action": "perf-reap", "count": len(ids)}
        tid = sp.get("id")
        if not (isinstance(tid, str) and PERF_ID_RE.fullmatch(tid)):
            return PASSTHROUGH
        return {"action": "perf-close", "id": tid,
                "name": _safe_text(sp.get("name"), 24)}
    return PASSTHROUGH


def _perf_active(layout):
    try:
        return int(layout.get("bottom_tab")) == PERF_TAB_INDEX
    except (TypeError, ValueError):
        return False


def hit_test(role, x, y, layout, cabin=None, now=None):
    """layout={"tab_spans": [...], "ai_rowmap": [...], "perf_spans": {...},
    "bottom_tab": int, "perf_visible": int, "pending_snapshot": {...}}

    第 37 項:待審框的點擊先過兩道門(frame_snapshot.frame_guard):跨艙不執行、剛重畫要重看;
    放行的命中帶那一幀的 epoch 與 cabin(確認回呼留痕用)。"""
    if role == "tabs":
        # y=0 恆為分頁列;效能頁作用中時 y>=1 先問 perf spans(精準三格),
        # 沒命中維持既有行為(hit_tabs 不看 y,內容區照舊可切分頁)。
        if _perf_active(layout):
            try:
                yy = int(y)
            except (TypeError, ValueError):
                yy = -1
            if yy >= 1:
                act = hit_perf_rows(layout.get("perf_spans"), yy - 1, x,
                                    layout.get("perf_visible"))
                if act is not PASSTHROUGH:
                    return act
        return hit_tabs(layout.get("tab_spans"), x)
    if role == "ai-list":
        return hit_ai_rows(layout.get("ai_rowmap"), y, x)
    if role == "hub-frame":
        return hit_hub_frame_rows(layout.get("hub_frame_rowmap"), y, x)
    if role == "pending-review":
        snap = layout.get("pending_snapshot")
        guard = FS.frame_guard(snap, cabin=cabin, now=now)
        if guard:
            return guard
        act = hit_pending_rows(layout.get("pending_rowmap"), y, x)
        if act is PASSTHROUGH and _feature_tab_of(layout) == 1:
            # 任務頁上點到沒有 link 的地方:給 task-nav 焦點(d 才有地方按),不是什麼都不做
            return {"action": TASK_ZONE_FOCUS}
        if act is not PASSTHROUGH and isinstance(snap, dict):
            act = dict(act)
            try:
                act["epoch"] = int(snap.get("epoch") or 0)
            except (TypeError, ValueError):
                act["epoch"] = 0
            act["cabin"] = _safe_text(snap.get("cabin"), 64)
        return act
    return PASSTHROUGH


def _load_text(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        # 落點檔不存在或壞掉:當作沒點到,不猜、不炸
        return None


def _main(argv):
    role = x = y = state_dir = None
    intent = "click"
    cabin = None
    now = None
    i = 0
    while i < len(argv) - 1:
        if argv[i] == "--role":
            role = argv[i + 1]
        elif argv[i] == "--x":
            x = argv[i + 1]
        elif argv[i] == "--y":
            y = argv[i + 1]
        elif argv[i] == "--state-dir":
            state_dir = argv[i + 1]
        elif argv[i] == "--intent":
            intent = argv[i + 1]
        elif argv[i] == "--cabin":
            cabin = argv[i + 1]
        elif argv[i] == "--now":
            try:
                now = float(argv[i + 1])
            except ValueError:
                now = None
        else:
            i += 1
            continue
        i += 2
    if intent == "scrollbar":
        if state_dir is None:
            return 2
        rowmap = _load("%s/ai-rowmap.json" % state_dir)
        return 0 if on_scrollbar(rowmap if isinstance(rowmap, list) else [], y, x) else 1
    if intent == "usage-arrow":
        if state_dir is None:
            return 2
        rowmap = _load("%s/ai-rowmap.json" % state_dir)
        return 0 if on_usage_arrow(rowmap if isinstance(rowmap, list) else [], y, x) else 1
    if intent == "scrollbar-offset":
        if state_dir is None:
            return 2
        rowmap = _load("%s/ai-rowmap.json" % state_dir)
        if not isinstance(rowmap, list):
            rowmap = []
        limit = 0
        try:
            with open("%s/.right-scroll-limit.json" % state_dir, encoding="utf-8") as fh:
                limits = json.load(fh)
        except (OSError, ValueError):
            limits = {}
        tab = "ai"
        try:
            with open("%s/right-tab" % state_dir, encoding="utf-8") as fh:
                tab = {"1": "usage", "3": "left"}.get(fh.read().strip(), "ai")  # 右欄狀態檔:1=2.AI用量、3=3.離艙(09-30)、其他=1.AI
        except OSError:
            pass
        if isinstance(limits, dict) and tab in limits:
            try:
                limit = max(0, int(limits[tab]))
            except (TypeError, ValueError):
                limit = 0
        target = scrollbar_target_offset(rowmap, y, limit)
        if target is None:
            return 1
        print(target)
        return 0
    if not role or state_dir is None:
        print("usage: hit_test.py --role tabs|ai-list|pending-review|hub-frame --x N --y N "
              "--state-dir DIR [--intent click|context|close]",
              file=sys.stderr)
        return 2
    layout = {
        "tab_spans": _load("%s/tab-spans.json" % state_dir),
        "ai_rowmap": _load("%s/ai-rowmap.json" % state_dir),
        "pending_rowmap": _load("%s/pending-rowmap.json" % state_dir),
        "hub_frame_rowmap": _load("%s/hub-frame-rowmap.json" % state_dir),
        "perf_spans": _load("%s/perf-spans.json" % state_dir),
        "bottom_tab": _load("%s/bottom-tab" % state_dir),
        "perf_visible": _load("%s/perf-visible-lines" % state_dir),
        "pending_snapshot": FS.read_snapshot(state_dir),
        "pending_feature_tab": _load_text("%s/pending-feature-tab" % state_dir),
    }
    if intent == "context" and role == "ai-list":
        action = hit_ai_detail(layout.get("ai_rowmap"), y)
    elif intent == "close" and role == "ai-list":
        action = hit_ai_close(layout.get("ai_rowmap"), y)
    elif intent == "click":
        action = hit_test(role, x, y, layout, cabin=cabin, now=now)
    else:
        action = PASSTHROUGH
    print(json.dumps(action, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
