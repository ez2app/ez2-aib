#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""橋接席(claudebot / grokbot / musebot / codexbot)畫面上的對話卡(the maintainer 10-09「OAuth 串接的對話框非常醜,針對每個 AI 品牌優化」)。

以前每一則都是 `claudebot> 文字`、`the maintainer(app)> 文字` 一行接一行,長回覆第二行起連前綴都沒有,分不出誰說的。
現在每一則畫成一張有框的卡:框線顏色、圖示、品牌名依說話者;標題列右邊是時間;內文依終端寬度折行(中文算兩格)。

  ╭─ ✦ Claude · a seat ─────────────────────── 09:52 ─╮
  │ 好,我先看 a seat 收到 v2 之後有沒有已經動手…                 │
  ╰────────────────────────────────────────────────────────────╯

★ 誠實邊界 / 相容:
  - 純函式,沒有 I/O;呼叫端(sand_bridge.DockedUI)決定開不開。**不是 TTY、或 EZ2_AIB_BRIDGE_CARDS=0 ⇒ 不畫卡**,
    照舊印 `標籤> 文字`(測試、日誌、非互動都走舊形狀,既有測試一行不改)。
  - 卡只用在「訊息」(真人 / app / bot 回覆 / 別席 dm / bot 寄出的 dm);`claudebot> …` 忙碌標記、`已放進信箱` 這類通知照舊一行,
    composer_state 的 busy / cursor 判定靠的是那些行,不是訊息。
  - 框線用 ╭│╰ 與 ─;composer_state 會先剝掉 SGR 再看文字,卡裡**永遠不出現 ❯**(提示符只認 ❯),所以不會被當成輸入列。
  - 顏色是 256 色 SGR(跟 dm_queue 的 priority_sgr 同一套);NO_COLOR 或 EZ2_AIB_BRIDGE_COLOR=0 ⇒ 只畫框不上色。
"""
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import unicodedata

# 品牌:顯示名、圖示、256 色號。**圖示與顏色跟右欄同一張表**(render/render_lib:ENGINE_ICONS、ENGINE_LETTER_COLORS;
# the maintainer 10-09「依照你看到的(右欄)套上去」)。render_lib 載不到(例如外部單獨跑)才用這裡的後備。
_FALLBACK = {
    "claudebot": ("Claude", "✦", 173),   # Claude 橘 #D97757
    "codexbot": ("Codex", "◆", 36),      # ChatGPT 綠 #10A37F
    "grokbot": ("Grok", "✧", 141),       # 紫 #A855F7
    "musebot": ("Muse", "♪", 69),        # Meta 藍 #5B8DEF
}
BRANDS = {
    "human": ("", "👤", 220),             # 黃;顯示名由呼叫端給(the maintainer / the maintainer(app))
    "dm": ("dm", "✉", 39),               # 藍
    "system": ("AIB 系統", "⚙", 245),    # 灰
}
BRAND_NAMES = {"claudebot": "Claude", "codexbot": "Codex", "grokbot": "Grok", "musebot": "Muse",
               # API-key 終端席(另開 AI 選單第三段;the maintainer 10-09「這些都要有」):圖示/色號同右欄
               "deepseek": "DeepSeek", "kiosapi": "KiosAPI", "nvidia": "NVIDIA", "openrouter": "OpenRouter",
               "typesafe": "TypeSafe", "openrouterjev": "OpenRouter jev", "teamo": "Teamo-Codex", "teamoclaude": "Teamo-Claude"}


def _load_engine_table():
    """右欄的圖示與色號:ENGINE_ICONS[provider]、ENGINE_LETTER_COLORS[provider][0][1](256 色)。grok 那種不設色的 ⇒ None(不上色)。"""
    try:
        _rd = os.path.join(os.path.dirname(os.path.abspath(__file__)), "render")   # render_lib 住在 src/cockpit/render/(沒有 __init__)
        if _rd not in sys.path:
            sys.path.append(_rd)
        import render_lib as rl
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for pid, name in BRAND_NAMES.items():
        icon = getattr(rl, "ENGINE_ICONS", {}).get(pid)
        cols = getattr(rl, "ENGINE_LETTER_COLORS", {}).get(pid)
        if not icon:
            continue
        out[pid] = (name, icon, cols[0][1] if cols else None)
    return out


for _pid, _v in _load_engine_table().items():
    BRANDS[_pid] = _v
for _pid, _v in _FALLBACK.items():
    BRANDS.setdefault(_pid, _v)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
MIN_COLS, MAX_COLS = 40, 110


def _w(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else (0 if unicodedata.combining(ch) else 1)


def display_width(s: str) -> int:
    return sum(_w(c) for c in _ANSI.sub("", s))


def wrap(text: str, width: int) -> list:
    """依顯示寬度折行(中文 2 格);保留原本的換行;空行保留。width ≤ 0 ⇒ 不折。"""
    out = []
    for raw in (text or "").split("\n"):
        raw = raw.rstrip("\r")
        if width <= 0 or display_width(raw) <= width:
            out.append(raw)
            continue
        line, w = "", 0
        for ch in raw:
            cw = _w(ch)
            if w + cw > width and line:
                out.append(line)
                line, w = "", 0
            line += ch
            w += cw
        out.append(line)
    return out or [""]


def _sgr(color, enabled: bool) -> str:
    return "\x1b[38;5;%dm" % color if (enabled and color is not None) else ""


def _reset(enabled: bool) -> str:
    return "\x1b[0m" if enabled else ""


def brand_source() -> str:
    """誠實標:圖示/顏色是從右欄的表來的,還是後備表。"""
    return "render_lib" if _load_engine_table() else "fallback"


def color_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    if env.get("NO_COLOR"):
        return False
    return (env.get("EZ2_AIB_BRIDGE_COLOR") or "1").strip().lower() not in ("0", "false", "no", "off")


def cards_enabled(isatty: bool, env=None) -> bool:
    """畫不畫卡:要是 TTY,而且沒被 EZ2_AIB_BRIDGE_CARDS=0 關掉。"""
    env = os.environ if env is None else env
    if not isatty:
        return False
    return (env.get("EZ2_AIB_BRIDGE_CARDS") or "1").strip().lower() not in ("0", "false", "no", "off")


def term_cols(fallback: int = 80) -> int:
    try:
        return int(shutil.get_terminal_size(fallback=(fallback, 24)).columns)
    except Exception:  # noqa: BLE001
        return fallback


def card(kind: str, label: str, text: str, cols: int = 80, when: str = "", color: bool = True, seat: str = "") -> str:
    """一張卡(多行字串,行尾不帶換行)。kind ∈ BRANDS;label = 標題列要顯示的名字(空 ⇒ 用品牌名)。
    seat = 席碼(有就接在名字後面,例如 a seat)。cols = 終端寬度,卡寬在 [MIN_COLS, MAX_COLS] 之間。"""
    name, icon, col = BRANDS.get(kind, BRANDS["system"])
    bot = kind in BRAND_NAMES
    if bot:
        # Claude · a seat;寄 dm 給別席時 label 長這樣「claudebot → 4f8d/3d156」⇒ 接在後面
        title = name + (" · %s@%s" % (kind, seat) if seat else "")
        extra = (label or "")[len(kind):].strip() if (label or "").startswith(kind) else (label or "")
        if extra:
            title += " " + extra
    else:
        title = (label or name) + ("@%s" % seat if seat else "")
    width = max(MIN_COLS, min(MAX_COLS, int(cols or 80))) - 2       # 扣掉左右框
    inner = width - 2                                                # 扣掉 │ 後的一格與右邊一格
    c, r = _sgr(col, color), _reset(color)
    when = when or time.strftime("%H:%M")
    head = " %s %s " % (icon, title)
    tail = " %s " % when
    fill = width - display_width(head) - display_width(tail)        # ╭ 與 ╮ 之間共 width 格(與底框 ─×width 等寬)
    if fill < 1:
        head = head[: max(1, len(head) + fill - 2)] + "… "
        fill = max(1, width - display_width(head) - display_width(tail))
    lines = ["%s╭%s%s%s%s╮%s" % (c, head, "─" * fill, tail, "", r)]
    for ln in wrap(text, inner):
        pad = inner - display_width(ln)
        lines.append("%s│%s %s%s %s│%s" % (c, r, ln, " " * max(0, pad), c, r))
    lines.append("%s╰%s╯%s" % (c, "─" * width, r))
    return "\n".join(lines)


def legacy(label: str, text: str) -> str:
    """舊形狀:`標籤> 文字`(非 TTY / 關掉卡片時用,既有測試照這個認)。"""
    return "%s> %s" % (label, text)


def speaker_kind(provider_id: str) -> str:
    return provider_id if provider_id in BRANDS else "system"


# ── 串流回覆用的「開口框」:API 席的回覆是一個字一個字流出來的,畫不了整張卡;改成先印品牌標題列、回覆照流、
#    下一次要輸入前再印底線。composer 只認提示符(DeepSeek> / you> …)與 ❯,這兩條線不會被當成輸入列。
def frame_top(kind: str, label: str = "", cols: int = 80, when: str = "", color: bool = True, seat: str = "") -> str:
    return card(kind, label, "", cols=cols, when=when, color=color, seat=seat).split("\n")[0]


def frame_bottom(kind: str, cols: int = 80, color: bool = True) -> str:
    return card(kind, "", "", cols=cols, when="-", color=color).split("\n")[-1]


class ReplFrame:
    """給 *_cli.py 的 REPL 用:open() 在模型回覆前印標題列,close() 在下一次提示符前印底線。
    非 TTY / EZ2_AIB_BRIDGE_CARDS=0 ⇒ 兩個方法都不印(畫面與以前逐位元組相同)。"""

    def __init__(self, kind: str, label: str = "", seat: str = "", out=None):
        self.kind, self.label, self.seat = kind, label, seat
        self.out = out
        self._open = False
        try:
            tty = (out or sys.stdout).isatty()
        except (AttributeError, OSError):
            tty = False
        self.enabled = cards_enabled(tty)
        self.color = color_enabled() and tty

    def open(self) -> None:
        if not self.enabled:
            return
        o = self.out or sys.stdout
        o.write(frame_top(self.kind, self.label, cols=term_cols(), color=self.color, seat=self.seat) + "\n")
        o.flush()
        self._open = True

    def close(self) -> None:
        if not (self.enabled and self._open):
            return
        o = self.out or sys.stdout
        # 回覆標籤(`nvidia> `)印了但回覆是空的 / 失敗時,游標還停在那一行:先回行首清掉,底線才不會接在標籤後面
        o.write("\r\x1b[2K" + frame_bottom(self.kind, cols=term_cols(), color=self.color) + "\n")
        o.flush()
        self._open = False



# ── 開席重放(10-09 the maintainer 看了六席截圖:「grokbot 這看起來對,其他的不對」—— 只有橋接席有重放,API 席 / codexbot 開席
#    什麼都沒有,看起來像沒套上)。三種席共用這一份:rows = [(who, text, when)],who 是 "human" 或品牌 kind;
#    空 ⇒ 印一行說「沒有舊訊息可重放」,人才知道功能有開、只是沒東西。只畫,不動任何檔。
REPLAY_N = 5
REPLAY_MAX_CHARS = 300   # 一張重放卡最多幾字:nvidia 席重放的第一則是整份開席信,一張卡 70 多行把畫面塞滿(10-09 真機看到)


def _clip(text: str, limit: int = REPLAY_MAX_CHARS) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…(共 %d 字,只重放前 %d 字)" % (len(text), limit)


def replay_lines(kind: str, rows, *, seat: str = "", human: str = "", cols: int = 0, color: bool = True,
                 n: int = REPLAY_N, source: str = "") -> list:
    """開席時把最近 n 則重放成卡,回要印的行(每張卡一個多行字串)。rows 依時間舊→新;只取最後 n 則。
    who == "human" ⇒ 真人卡(名字 = human);其餘 ⇒ 品牌卡(kind)。when 空 ⇒ 標「重放」而不是現在時間。"""
    rows = [r for r in (rows or []) if r and str(r[1] or "").strip()][-max(0, int(n)):]
    src = ("(%s)" % source) if source else ""
    if not rows:
        return ["— 重開前沒有舊訊息可重放%s —" % src]
    out = ["— 重開前的最近 %d 則(重放,不是新信)%s —" % (len(rows), src)]
    cols = cols or term_cols()
    for who, text, when in rows:
        k = "human" if who == "human" else (who if who in BRANDS else kind)
        label = human if k == "human" else ""
        out.append(card(k, label, _clip(text), cols=cols, when=(when or "重放"), color=color,
                        seat=("" if k == "human" else seat)))
    return out


def print_replay(kind: str, rows, *, seat: str = "", human: str = "", out=None, n: int = REPLAY_N, source: str = "") -> int:
    """*_cli.py / codexbot 開席用:TTY 且沒關卡片才印;回印了幾則(非 TTY ⇒ 0,畫面與以前逐位元組相同)。"""
    o = out or sys.stdout
    try:
        tty = o.isatty()
    except (AttributeError, OSError):
        tty = False
    if not cards_enabled(tty):
        return 0
    lines = replay_lines(kind, rows, seat=seat, human=human, color=(color_enabled() and tty), n=n, source=source)
    o.write("\n".join(lines) + "\n")
    o.flush()
    return max(0, len(lines) - 1)


def history_rows(hist_path, kind: str):
    """API 席的 history.jsonl ⇒ 重放用 [(who, text, when)]。ts(10-09 起 append_history 才寫)有就印 HH:MM,舊紀錄沒有 ⇒ 標「重放」。
    壞行跳過;檔不存在 ⇒ 空。"""
    rows = []
    try:
        lines = Path(hist_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("role") not in ("user", "assistant") or not isinstance(row.get("content"), str):
            continue
        ts = row.get("ts")
        when = time.strftime("%H:%M", time.localtime(ts)) if isinstance(ts, (int, float)) and ts > 0 else ""
        rows.append(("human" if row["role"] == "user" else kind, row["content"], when))
    return rows


def replay_history_file(sess_dir, kind: str, out=None) -> int:
    """*_cli.py 開席用:讀 <sess>/history.jsonl 重放最近幾則(席碼 = session uuid 末五碼,走 seat_address 不自切)。失敗不擋開席。"""
    try:
        from seat_address import uuid_tail5
        from human_attest import human_display_name
        sess_dir = Path(sess_dir)
        return print_replay(kind, history_rows(sess_dir / "history.jsonl", kind), seat=uuid_tail5(sess_dir.name) or "",
                            human=human_display_name(), out=out, source="history.jsonl")
    except Exception:  # noqa: BLE001
        return 0
