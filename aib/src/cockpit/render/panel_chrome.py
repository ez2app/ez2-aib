#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""對話區 / 即時框圓角殼 —— 與待審同一套 ╭╮╰╯ 字面。

09-20：正文只留上下蓋（左右空白佔位）；暴板主因是 hub-pad，不是開側。

產品(2026-09-20):對話區與待審區之後可分開授權販售,畫面必須是同一產品族
(圓角框 + 右緣捲動 + [≡N]),不能各畫各的。
"""
from __future__ import annotations

from render_lib import (
    PENDING_TL, PENDING_TR, PENDING_BL, PENDING_BR, PENDING_H, PENDING_V,
    PENDING_UP, PENDING_DOWN, PENDING_TRACK, PENDING_THUMB, PENDING_PAD_L, PENDING_PAD_R, PENDING_INNER,
    visible_width, trunc, edge_dot, edge_cell, is_edge_continuation, is_necessary_edge_item, strip_leading_edge_dot, _pending_wrap, rewrite_human_prompt,
)

try:
    from render_lib import _pending_frame, _pending_hot
except ImportError:  # pragma: no cover
    def _pending_frame(s):
        return s

    def _pending_hot(s):
        return s

DIALOG_TITLE = "AIB 私訊對話區"
REALTIME_TITLE = "AIB 回應顯示區"
INPUT_TITLE = "AIB 訊息輸入區"
DIALOG_TRACK = "┊"  # 與邊框 │ 區隔，避免看起來像雙豎線
DIALOG_EMPTY = "（私訊對話區尚無內容）"
REALTIME_EMPTY = "（回應顯示區尚無內容）"
INPUT_EMPTY = "（在下方輸入區打字）"


DIALOG_HEIGHTS = (1, 3, 5, 10)
REALTIME_HEIGHTS = (30, 40, 50)
DEFAULT_DIALOG_HEIGHT = 1
DEFAULT_REALTIME_HEIGHT = 40
DEFAULT_INPUT_HEIGHT = 5


def _height_of(value, choices, default):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n in choices:
        return n
    lo, hi = choices[0], choices[-1]
    if n < lo:
        return lo
    if n > hi:
        return hi
    lower = [h for h in choices if h <= n]
    return lower[-1] if lower else default


def dialog_height_of(value):
    """吸附到 1/3/5/10；非法回 1。"""
    return _height_of(value, DIALOG_HEIGHTS, DEFAULT_DIALOG_HEIGHT)


def dialog_height_next(value):
    cur = dialog_height_of(value)
    try:
        i = DIALOG_HEIGHTS.index(cur)
    except ValueError:
        i = 0
    return DIALOG_HEIGHTS[(i + 1) % len(DIALOG_HEIGHTS)]


def realtime_height_of(value):
    """吸附到 30/40/50；非法回 40。"""
    return _height_of(value, REALTIME_HEIGHTS, DEFAULT_REALTIME_HEIGHT)


def realtime_height_next(value):
    cur = realtime_height_of(value)
    try:
        i = REALTIME_HEIGHTS.index(cur)
    except ValueError:
        i = 0
    return REALTIME_HEIGHTS[(i + 1) % len(REALTIME_HEIGHTS)]


def _gutter_glyphs(n, offset, visible):
    """照抄待審 pending_gutter_glyphs：█ / ▲ / ▼ / │；n<=vis 不畫。"""
    n = int(n or 0)
    vis = max(1, int(visible))
    if n <= vis:
        return [""] * vis
    offset = max(0, min(int(offset), max(0, n - vis)))
    thumb_slot = int(round((offset / max(1, n - vis)) * (vis - 1)))
    more_above = offset > 0
    more_below = offset + vis < n
    out = []
    for i in range(vis):
        if i == thumb_slot:
            out.append(PENDING_THUMB)
        elif i == 0 and more_above:
            out.append(PENDING_UP)
        elif i == vis - 1 and more_below:
            out.append(PENDING_DOWN)
        else:
            out.append(PENDING_TRACK)
    return out



def render_chrome_box(title, body_lines, width, size_mark=None, offset=0,
                      empty_text="", hot_action=None, identity="", seat="",
                      max_height=None, shine=None, edge_shine=None,
                      open_bottom=False, toggle_action=None, toggle_mark=None):
    width = max(16, int(width or 16))
    edge_phase = edge_shine if edge_shine is not None else shine
    pad_l = " " * PENDING_PAD_L
    pad_r = " " * PENDING_PAD_R
    inner_sp = " " * PENDING_INNER
    # 與待審同一套：外白 + │ + 內白 + 內容(+捲動軌) + 內白 + │ + 外白
    # 內文起點 = PAD_L + 1 + INNER，與 #xxx 的 # 對齊
    box_inner = width - PENDING_PAD_L - PENDING_PAD_R - 2
    if box_inner < 8:
        box_inner = 8
        width = box_inner + PENDING_PAD_L + PENDING_PAD_R + 2
    inner = box_inner  # 底線 ─ 長度（兩 │ 之間）
    body = [str(x) for x in (body_lines or [])]
    if size_mark is None:
        mark = None
    else:
        try:
            _m = int(size_mark)
        except (TypeError, ValueError):
            _m = DEFAULT_DIALOG_HEIGHT
        # 呼叫端已依區夾過；此處只認兩邊階梯，避免回應區 30/40/50 被對話階梯吸成 10
        if _m in DIALOG_HEIGHTS or _m in REALTIME_HEIGHTS:
            mark = _m
        else:
            mark = dialog_height_of(_m)

    left_bits = [_pending_frame(PENDING_TL + PENDING_H), " "]
    col = PENDING_PAD_L + visible_width(PENDING_TL + PENDING_H) + 1
    links = []

    def add(disp, plain=None, action=None):
        nonlocal col
        plain = plain if plain is not None else disp
        w = visible_width(plain)
        if action:
            links.append({
                "start": col, "end": col + w,
                "action": action, "seat": str(seat or ""),
            })
        left_bits.append(disp)
        col += w

    # 標題與 [≡N] 都可點循環高度（只點 ≡ 太窄，使用者常點到標題）
    _cycle = (hot_action or "dialog-visible-cycle") if mark is not None else None
    add(_pending_hot("[%s]" % title), "[%s]" % title, _cycle)
    if mark is not None:
        add(" ")
        add(
            _pending_hot("[≡%d]" % mark), "[≡%d]" % mark,
            _cycle,
        )
    # OAuth 成對開關：標題旁 [−]/[+]（api_key 席不傳 toggle_action）
    if toggle_action:
        add(" ")
        _tm = toggle_mark if toggle_mark is not None else "[−]"
        add(_pending_hot(_tm), _tm, toggle_action)
    if identity:
        tok = " " + str(identity).strip()[:12]
        add(tok, tok)

    left_s = "".join(left_bits)
    right = _pending_frame(PENDING_TR)
    # fill 用 ─ 補到整行可見寬 = width(ANSI 不佔寬)
    used = PENDING_PAD_L + visible_width(left_s) + visible_width(PENDING_TR) + PENDING_PAD_R
    fill_n = max(0, width - used)
    top = pad_l + left_s + _pending_frame(PENDING_H * fill_n) + right + pad_r

    if not body:
        body = [empty_text or ""]
    vis = mark if mark is not None else max(1, min(3, len(body)))
    if max_height is not None:
        try:
            vis = max(1, min(vis, int(max_height)))
        except (TypeError, ValueError):
            pass
    chrome = (PENDING_PAD_L + 1 + PENDING_INNER
              + PENDING_INNER + 1 + PENDING_PAD_R)
    # 先估一輪 item_w（暫不當捲軌）；展開後若需要捲軌再重算一次
    item_w = max(8, width - chrome)
    visual = []  # (text, continued, necessary)
    prev_logic = None
    for raw in body:
        s = rewrite_human_prompt(str(raw))
        if not s.strip():
            visual.append(("", True, False))  # 空列不點
            prev_logic = None
            continue
        s = strip_leading_edge_dot(s)
        need = is_necessary_edge_item(s)
        soft = is_edge_continuation(prev_logic, s, item_w)
        parts = _pending_wrap(s, item_w) if s else [""]
        for j, part in enumerate(parts):
            visual.append((part, bool(soft or j > 0), bool(need and j == 0 and not soft)))
        prev_logic = s

    gutter_on = len(visual) > vis
    gutter_w = 2 if gutter_on else 0
    item_w = max(8, width - chrome - gutter_w)
    # 有捲軌時欄變窄，再折一次，避免續行又被截斷
    if gutter_w:
        visual = []
        prev_logic = None
        for raw in body:
            s = rewrite_human_prompt(str(raw))
            if not s.strip():
                visual.append(("", True, False))  # 空列不點
                prev_logic = None
                continue
            s = strip_leading_edge_dot(s)
            need = is_necessary_edge_item(s)
            soft = is_edge_continuation(prev_logic, s, item_w)
            parts = _pending_wrap(s, item_w) if s else [""]
            for j, part in enumerate(parts):
                visual.append((part, bool(soft or j > 0), bool(need and j == 0 and not soft)))
            prev_logic = s
        gutter_on = len(visual) > vis
        gutter_w = 2 if gutter_on else 0
        item_w = max(8, width - chrome - gutter_w)

    offset = max(0, min(int(offset or 0), max(0, len(visual) - vis)))
    window = visual[offset:offset + vis]
    while len(window) < vis:
        window.append(("", True, False))
    glyphs = _gutter_glyphs(len(visual), offset, vis)

    lines = [top]
    rowmap = [{"line": 0, "kind": "chrome-title", "links": links}]
    more_above = offset > 0
    more_below = offset + vis < len(visual)
    for i, (row, continued, necessary) in enumerate(window):
        content = trunc(row, item_w)
        pad = " " * max(0, item_w - visible_width(content))
        body_links = []
        gutter_ch = ""
        if gutter_on:
            g = glyphs[i] if i < len(glyphs) else PENDING_TRACK
            g_base = PENDING_PAD_L + 1 + PENDING_INNER + item_w
            if vis == 1:
                if more_above and more_below:
                    gutter_ch = _pending_hot(PENDING_UP) + _pending_hot(PENDING_DOWN)
                    body_links += [
                        {"start": g_base, "end": g_base + 1,
                         "action": "dialog-scroll-up", "seat": str(seat or "")},
                        {"start": g_base + 1, "end": g_base + 2,
                         "action": "dialog-scroll-down", "seat": str(seat or "")},
                    ]
                elif more_below:
                    gutter_ch = " " + _pending_hot(PENDING_DOWN)
                    body_links.append({
                        "start": g_base + 1, "end": g_base + 2,
                        "action": "dialog-scroll-down", "seat": str(seat or ""),
                    })
                elif more_above:
                    gutter_ch = " " + _pending_hot(PENDING_UP)
                    body_links.append({
                        "start": g_base + 1, "end": g_base + 2,
                        "action": "dialog-scroll-up", "seat": str(seat or ""),
                    })
                else:
                    gutter_ch = "  "
            else:
                g_action = None
                if i == 0 and more_above:
                    g_action = "dialog-scroll-up"
                elif i == vis - 1 and more_below:
                    g_action = "dialog-scroll-down"
                elif g == PENDING_TRACK and more_above and i < vis // 2:
                    g_action = "dialog-scroll-up"
                elif g == PENDING_TRACK and more_below and i >= vis // 2:
                    g_action = "dialog-scroll-down"
                g_col = g_base + 1
                if g_action:
                    body_links.append({
                        "start": g_col, "end": g_col + 1,
                        "action": g_action, "seat": str(seat or ""),
                    })
                if g in (PENDING_UP, PENDING_DOWN):
                    gutter_ch = " " + _pending_hot(g)
                elif g == PENDING_THUMB:
                    gutter_ch = " " + g
                else:
                    gutter_ch = " " + g
        # 只留上下蓋；左右空白佔位。• 在框線內側（╭ 右邊一格）；顏色靜態綠／灰，避免整框重畫閃爍。
        # 禁止再切 hub-pad 畫 •（會跑出彎曲線外且閃爍）。
        # 左缘：PAD_L + （原 │ 位空白）+ • ；右缘：捲軌 + INNER + （原 │ 位空白）+ PAD_R
        # 必須與上方 chrome 寬度式一致，否則整行超寬被 trunc 後 … 會跑到 ┊／█ 右邊、╮ 被裁掉。
        mid = (
            pad_l + " " + edge_cell(edge_phase, continued=continued, necessary=necessary)
            + content + pad + gutter_ch + inner_sp
            + " " + pad_r
        )
        # 超寬時只縮正文，保住捲軌在最右（… 在 ┊／█ 左邊）
        if visible_width(mid) > width:
            overflow = visible_width(mid) - width
            keep = max(0, visible_width(content) - overflow)
            content = trunc(content, keep)
            pad = " " * max(0, item_w - visible_width(content))
            mid = (
                pad_l + " " + edge_cell(edge_phase, continued=continued, necessary=necessary)
                + content + pad + gutter_ch + inner_sp
                + " " + pad_r
            )
            if visible_width(mid) > width:
                # 最後手段：仍超寬就從右裁 pad_r，绝不 trunc 掉 gutter
                while visible_width(mid) > width and mid.endswith(" "):
                    mid = mid[:-1]
        lines.append(mid)
        rowmap.append({
            "line": i + 1, "kind": "chrome-body", "offset": offset + i,
            "links": body_links,
        })
    # 抬頭超寬：減 ─ 填縫，绝不 trunc 掉右上 ╮
    if visible_width(lines[0]) > width:
        over = visible_width(lines[0]) - width
        # 重建 top：縮 fill
        fill_n2 = max(0, fill_n - over)
        lines[0] = pad_l + left_s + _pending_frame(PENDING_H * fill_n2) + right + pad_r
        if visible_width(lines[0]) > width:
            # 仍超：從左標題側不再動，右側保 ╮，必要時縮 pad_r
            while visible_width(lines[0]) > width and lines[0].endswith(" "):
                lines[0] = lines[0][:-1]
    if not open_bottom:
        bottom = pad_l + _pending_frame(PENDING_BL + PENDING_H * inner + PENDING_BR) + pad_r
        if visible_width(bottom) > width:
            bottom = trunc(bottom, width)
        lines.append(bottom)
        rowmap.append({"line": len(lines) - 1, "kind": "chrome-foot"})
    return lines, rowmap





def render_dialog_menu_bar(width):
    """對話區圓框正上方的選單列：先空一行，再右對齊「選單」。

    右緣對齊下方待審功能列最後字（刪除）——與 _inbox_tabs_line 同一條右界：
    width - PENDING_PAD_R - len(" ─╮")，不是貼整窗右緣。
    """
    width = max(16, int(width or 16))
    blank = " " * width
    label = "選單"
    lw = visible_width(label)
    # 與待審五 tab 右緣同一尺（見 render_lib._inbox_tabs_line）
    right = width - PENDING_PAD_R - visible_width(" " + PENDING_H + PENDING_TR)
    right = max(lw, min(width, right))
    pad = max(0, right - lw)
    row = (" " * pad) + label
    # 右側補空白到整列寬，避免 tmux 殘字
    if visible_width(row) < width:
        row = row + (" " * (width - visible_width(row)))
    if visible_width(row) > width:
        row = trunc(row, width)
    lines = [blank, row]
    rowmap = [
        {"line": 0, "kind": "dialog-menu-pad", "links": []},
        {"line": 1, "kind": "dialog-menu", "links": []},
    ]
    return lines, rowmap



def render_chrome_header(title, width, size_mark=None, hot_action=None,
                         identity="", seat="", borderless=False,
                         toggle_action=None, toggle_mark=None):
    """只畫一條抬頭（╭─ [標題] [≡N] ─╮），不畫正文、不畫底線。

    給 hub 欄頂用：下方接真 AI 活窗，再畫完整小框會多出一堆橫線＋假正文。
    borderless=True：保標題／[≡N]，不畫 ╭─╮（下半色塊用）。
    """
    width = max(16, int(width or 16))
    pad_l = " " * PENDING_PAD_L
    pad_r = " " * PENDING_PAD_R
    if size_mark is None:
        mark = None
    else:
        try:
            _m = int(size_mark)
        except (TypeError, ValueError):
            _m = DEFAULT_DIALOG_HEIGHT
        # 呼叫端已依區夾過；此處只認兩邊階梯，避免回應區 30/40/50 被對話階梯吸成 10
        if _m in DIALOG_HEIGHTS or _m in REALTIME_HEIGHTS:
            mark = _m
        else:
            mark = dialog_height_of(_m)

    def _edge(ch):
        if borderless:
            return " " * visible_width(ch)
        return _pending_frame(ch)

    left_bits = [_edge(PENDING_TL + PENDING_H), " "]
    col = PENDING_PAD_L + visible_width(PENDING_TL + PENDING_H) + 1
    links = []

    def add(disp, plain=None, action=None):
        nonlocal col
        plain = plain if plain is not None else disp
        w = visible_width(plain)
        if action:
            links.append({
                "start": col, "end": col + w,
                "action": action, "seat": str(seat or ""),
            })
        left_bits.append(disp)
        col += w

    # 標題與 [≡N] 都可點循環高度（只點 ≡ 太窄，使用者常點到標題）
    _cycle = (hot_action or "dialog-visible-cycle") if mark is not None else None
    add(_pending_hot("[%s]" % title), "[%s]" % title, _cycle)
    if mark is not None:
        add(" ")
        add(
            _pending_hot("[≡%d]" % mark), "[≡%d]" % mark,
            _cycle,
        )
    if toggle_action:
        add(" ")
        _tm = toggle_mark if toggle_mark is not None else "[−]"
        add(_pending_hot(_tm), _tm, toggle_action)
    if identity:
        tok = " " + str(identity).strip()[:12]
        add(tok, tok)

    left_s = "".join(left_bits)
    right = _edge(PENDING_TR)
    used = PENDING_PAD_L + visible_width(left_s) + visible_width(PENDING_TR) + PENDING_PAD_R
    fill_n = max(0, width - used)
    top = pad_l + left_s + _edge(PENDING_H * fill_n) + right + pad_r
    # 超寬只縮 ─，保住右邊 ╮
    while visible_width(top) > width and fill_n > 0:
        fill_n -= 1
        top = pad_l + left_s + _edge(PENDING_H * fill_n) + right + pad_r
    while visible_width(top) > width and top.endswith(" "):
        top = top[:-1]
    rowmap = [{"line": 0, "kind": "chrome-header", "links": links}]
    return [top], rowmap


def render_dialog_header(width, size_mark=1, identity="", seat=""):
    return render_chrome_header(
        DIALOG_TITLE, width, size_mark=size_mark,
        hot_action="dialog-visible-cycle",
        identity=identity, seat=seat,
    )




def render_realtime_header(width, size_mark=40, identity="", seat="", shine=None,
                           show_hub_io_toggle=False, hub_io_open=True):
    """回應顯示區頂蓋（完整狀態框請用 render_realtime_status_box）。"""
    tog = "hub-io-toggle" if show_hub_io_toggle else None
    mark = "[−]" if hub_io_open else "[+]"
    return render_chrome_header(
        REALTIME_TITLE, width, size_mark=size_mark,
        hot_action="realtime-visible-cycle",
        identity=identity, seat=seat,
        toggle_action=tog, toggle_mark=mark,
    )


def render_realtime_footer(width):
    """回應顯示區底蓋（輸入區請用 render_input_footer）。"""
    width = max(16, int(width or 16))
    pad_l = " " * PENDING_PAD_L
    pad_r = " " * PENDING_PAD_R
    inner = width - PENDING_PAD_L - PENDING_PAD_R - 2
    if inner < 8:
        inner = 8
        width = inner + PENDING_PAD_L + PENDING_PAD_R + 2
    bot = pad_l + _pending_frame(PENDING_BL + PENDING_H * inner + PENDING_BR) + pad_r
    if visible_width(bot) > width:
        bot = trunc(bot, width)
    return [bot], [{"line": 0, "kind": "chrome-foot"}]




def _ctx_human_tokens(n):
    if n is None:
        return "?"
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "?"
    if n >= 1000000:
        v = n / 1000000.0
        s = ("%.0f" if v >= 10 else "%.1f") % v
        return s.rstrip("0").rstrip(".") + "M"
    if n >= 1000:
        return "%dk" % int(round(n / 1000.0))
    return str(n)


def _ctx_tier(pct):
    if pct is None:
        return "unknown"
    try:
        p = int(pct)
    except (TypeError, ValueError):
        return "unknown"
    if p >= 95:
        return "crit"
    if p >= 75:
        return "hot"
    if p >= 50:
        return "warn"
    return "ok"


_CTX_ICONS = {
    "ok": "💧", "warn": "⚠️", "hot": "🌡️", "crit": "🔥", "unknown": "❓",
}


def _ctx_bar(pct, tier, cells=10):
    """對齊 statusline head 風格：[━━💧─────]。"""
    icon = _CTX_ICONS.get(tier, "💧")
    if pct is None:
        return "[" + ("─" * cells) + "]"
    try:
        p = max(0, min(100, int(pct)))
    except (TypeError, ValueError):
        return "[" + ("─" * cells) + "]"
    n = max(0, min(cells, int(round(p / 100.0 * cells))))
    if n <= 0:
        return "[" + icon + ("─" * (cells - 1)) + "]"
    done = max(0, n - 1)
    return "[" + ("━" * done) + icon + ("─" * (cells - done - 1)) + "]"


def load_ctx_gauge_for_active_seat(sock=""):
    """讀 aihub 焦點席對應 ~/.ez2/ctx/<uuid>.pct；失敗回精簡 dict。"""
    import json, os, glob, subprocess
    sock = (sock or os.environ.get("EZ2_AIHUB_SOCKET") or "").strip()
    if not sock:
        return None
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    try:
        out = subprocess.check_output(
            ["tmux", "-L", sock, "list-windows", "-t", "aihub",
             "-F", "#{window_active}\t#{window_name}\t#{@aib_provider}\t#{window_id}"],
            stderr=subprocess.DEVNULL, text=True, timeout=1, env=env,
        )
    except Exception:
        return None
    active = None
    for ln in out.splitlines():
        parts = ln.strip().split("\t")
        if len(parts) >= 3 and parts[0] == "1":
            active = {
                "name": parts[1],
                "provider": parts[2],
                "window_id": parts[3] if len(parts) > 3 else "",
            }
            break
    if not active:
        return None
    home = (os.environ.get("EZ2_AIB_INSTANCE_HOME") or "").strip()
    if not home:
        tag = sock[6:] if sock.startswith("aihub-") else ""
        if tag:
            home = os.path.expanduser("~/.ez2-aib/instances/" + tag)
    session = None
    model = None
    provider = (active.get("provider") or "").strip()
    wid = (active.get("window_id") or "").strip()
    seat_dir = os.path.join(home, "seats") if home else ""
    if seat_dir and os.path.isdir(seat_dir):
        by_prov = None
        for path in glob.glob(os.path.join(seat_dir, "*.json")):
            try:
                d = json.load(open(path, encoding="utf-8"))
            except Exception:
                continue
            sw = str(d.get("window_id") or "").strip()
            prov = str(d.get("provider") or "").strip()
            sid = d.get("session_uuid") or d.get("session_id")
            if wid and sw and (sw == wid or sw.lstrip("@") == wid.lstrip("@")):
                session = sid
                model = d.get("model") or d.get("model_requested")
                provider = prov or provider
                break
            if prov and provider and prov == provider and by_prov is None:
                by_prov = d
        if session is None and by_prov is not None:
            session = by_prov.get("session_uuid") or by_prov.get("session_id")
            model = model or by_prov.get("model") or by_prov.get("model_requested")
            provider = str(by_prov.get("provider") or provider)
    label = model or provider or active.get("name") or "?"
    if not session:
        return {"model": label, "pct": None, "used": None, "size": None, "tier": "unknown"}
    pct_path = os.path.expanduser("~/.ez2/ctx/%s.pct" % session)
    if not os.path.isfile(pct_path):
        return {"model": label, "pct": None, "used": None, "size": None,
                "tier": "unknown", "session_id": session}
    try:
        g = json.load(open(pct_path, encoding="utf-8"))
    except Exception:
        return {"model": label, "pct": None, "used": None, "size": None,
                "tier": "unknown", "session_id": session}
    pct = g.get("pct")
    try:
        pct = int(pct) if pct is not None else None
    except (TypeError, ValueError):
        pct = None
    return {
        "model": g.get("model") or label,
        "pct": pct,
        "used": g.get("used"),
        "size": g.get("size"),
        "tier": g.get("tier") or _ctx_tier(pct),
        "session_id": session,
        "engine": g.get("engine"),
    }


def format_ctx_foot_line(width, gauge=None, sock=""):
    """輸入框下、艦長上：Model | 💧 Ez2 CTX n% [bar] used/size。"""
    if gauge is None:
        gauge = load_ctx_gauge_for_active_seat(sock=sock) or {}
    model = str((gauge or {}).get("model") or "?")
    pct = (gauge or {}).get("pct")
    tier = (gauge or {}).get("tier") or _ctx_tier(pct)
    icon = _CTX_ICONS.get(tier, "❓")
    if pct is None:
        body = "%s | %s Ez2 CTX ?" % (model, icon)
    else:
        body = "%s | %s Ez2 CTX %d%% %s" % (
            model, icon, int(pct), _ctx_bar(pct, tier))
        used, size = (gauge or {}).get("used"), (gauge or {}).get("size")
        if used is not None and size:
            body += " %s/%s" % (_ctx_human_tokens(used), _ctx_human_tokens(size))
    pad = " " * int(PENDING_PAD_L)
    line = pad + "  " + body
    if visible_width(line) > width:
        line = trunc(line, width)
    pad_n = max(0, int(width) - visible_width(line))
    return line + (" " * pad_n)


def format_captain_line(width, marks=""):
    """艦長列：比 ╭/╰ 多縮 2 格，與對話 • 齊。掛在輸入區下方。"""
    pad = " " * int(PENDING_PAD_L)
    marks = (marks or "").strip()
    body = ("艦長: " + marks).rstrip() if marks else "艦長:"
    inner = max(8, int(width) - int(PENDING_PAD_L) - 3)
    body = trunc(body, inner)
    line = pad + "  " + body
    if visible_width(line) > width:
        line = trunc(line, width)
    pad_n = max(0, int(width) - visible_width(line))
    return line + (" " * pad_n)


def fetch_captain_marks(sock=""):
    import os, subprocess
    sock = (sock or os.environ.get("EZ2_AIHUB_SOCKET") or "").strip()
    if not sock:
        return ""
    try:
        env = {k: v for k, v in os.environ.items() if k != "TMUX"}
        wins = subprocess.run(
            ["tmux", "-L", sock, "list-windows", "-t", "aihub",
             "-F", "#{?#{==:#{@aib_captain},1}, #{@aib_captain_icon} #{@aib_persona}@#{@aib_seat} ,}"],
            capture_output=True, text=True, timeout=1, env=env,
        )
        return "".join(x for x in (wins.stdout or "").splitlines() if x.strip()).strip()
    except Exception:
        return ""


def render_input_header(width, size_mark=1, identity="", seat="", shine=None,
                        show_hub_io_toggle=False, hub_io_open=True, notice=""):
    """訊息輸入區頂蓋（正文=下方 hub 打字框）：╭─ [標題] [≡N] ─╮。
    notice(10-10 第 4 項):非空就接在標題後面(例「✍ 輸出中,先別打中文」),空字串=跟以前逐字同。"""
    tog = "hub-io-toggle" if show_hub_io_toggle else None
    mark = "[−]" if hub_io_open else "[+]"
    title = INPUT_TITLE if not notice else "%s · %s" % (INPUT_TITLE, notice)
    return render_chrome_header(
        title, width, size_mark=size_mark,
        hot_action="input-visible-cycle",
        identity=identity, seat=seat,
        borderless=False,
        toggle_action=tog, toggle_mark=mark,
    )


def render_input_footer(width):
    """訊息輸入區底蓋，貼在 hub 下方（待審頂、艦長之上）：╰────╯。"""
    width = max(16, int(width or 16))
    pad_l = " " * PENDING_PAD_L
    pad_r = " " * PENDING_PAD_R
    inner = width - PENDING_PAD_L - PENDING_PAD_R - 2
    if inner < 8:
        inner = 8
        width = inner + PENDING_PAD_L + PENDING_PAD_R + 2
    bot = pad_l + _pending_frame(PENDING_BL + PENDING_H * inner + PENDING_BR) + pad_r
    if visible_width(bot) > width:
        bot = trunc(bot, width)
    return [bot], [{"line": 0, "kind": "chrome-foot"}]


def render_realtime_status_box(rows, width, size_mark=40, identity="", seat="",
                               shine=None, edge_shine=None, max_height=None,
                               open_bottom=False, show_hub_io_toggle=False,
                               hub_io_open=True):
    """回應顯示區完整圓框：顯示 hub 同步來的訊息（輸入區只留打字）。
    open_bottom=True：不畫底 ╰─╯（下方接著輸入區標題時用，避免多一條橫線）。"""
    mark = realtime_height_of(size_mark) if size_mark is not None else DEFAULT_REALTIME_HEIGHT
    cap = max_height if max_height is not None else mark
    try:
        cap_i = max(1, int(cap or mark))
    except (TypeError, ValueError):
        cap_i = max(1, int(mark))
    body = [str(r) for r in (rows or []) if str(r).strip()][-cap_i:]
    # 補空列撐滿 cap，讓輸入頂蓋能貼在 frame 底（空白不落在輸入標題下）
    if len(body) < cap_i:
        body = body + [""] * (cap_i - len(body))
    tog = "hub-io-toggle" if show_hub_io_toggle else None
    tmark = "[−]" if hub_io_open else "[+]"
    return render_chrome_box(
        REALTIME_TITLE, body, width, size_mark=mark,
        empty_text=REALTIME_EMPTY, hot_action="realtime-visible-cycle",
        identity=identity, seat=seat,
        shine=shine, edge_shine=edge_shine, max_height=cap,
        open_bottom=open_bottom,
        toggle_action=tog, toggle_mark=tmark,
    )


def render_realtime_box(rows, width, size_mark=40, identity="", seat="", shine=None, edge_shine=None):
    mark = realtime_height_of(size_mark)
    body = [str(r) for r in (rows or [])][-max(1, mark):]
    return render_chrome_box(
        REALTIME_TITLE, body, width, size_mark=mark,
        empty_text=REALTIME_EMPTY, hot_action="realtime-visible-cycle",
        identity=identity, seat=seat, shine=shine, edge_shine=edge_shine,
        max_height=mark,
    )


def render_dialog_box(rows, width, size_mark=1, offset=0, identity="", seat="",
                      max_height=None, shine=None, edge_shine=None):
    return render_chrome_box(
        DIALOG_TITLE, [str(r) for r in (rows or [])], width,
        size_mark=size_mark, offset=offset,
        empty_text=DIALOG_EMPTY, hot_action="dialog-visible-cycle",
        identity=identity, seat=seat, max_height=max_height, shine=shine,
    )


def stack_column_panels(realtime_lines, realtime_map,
                        dialog_lines, dialog_map,
                        pending_lines, pending_map,
                        order="dialog_above"):
    """上私訊對話、中訊息指令、下待審。空框不疊。

    order 只影響對話／待審誰在指令區的上或下；指令區固定夹在中間。
    """
    out_l, out_m = [], []

    def take(lines, mmap):
        nonlocal out_l, out_m
        if not lines:
            return
        off = len(out_l)
        out_l.extend(lines)
        for row in (mmap or []):
            if not isinstance(row, dict):
                continue
            rr = dict(row)
            try:
                rr["line"] = int(row.get("line") or 0) + off
            except (TypeError, ValueError):
                rr["line"] = off
            out_m.append(rr)

    first, second = (
        ("dialog", "pending") if order == "dialog_above"
        else ("pending", "dialog")
    )
    bag = {
        "dialog": (dialog_lines, dialog_map),
        "pending": (pending_lines, pending_map),
    }
    take(*bag[first])
    take(realtime_lines, realtime_map)  # 中：回應顯示區
    take(*bag[second])
    return out_l, out_m


def render_hub_io_reopen_strip(width, identity="", seat=""):
    """OAuth 席把回應+輸入成對關掉後的一列重開條：[+] 回應+輸入。"""
    return render_chrome_header(
        "回應+輸入", width, size_mark=None,
        hot_action=None, identity=identity, seat=seat,
        toggle_action="hub-io-toggle", toggle_mark="[+]",
    )
