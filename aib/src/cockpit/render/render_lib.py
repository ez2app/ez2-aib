# SPDX-License-Identifier: Apache-2.0
"""ANSI 感知的終端機渲染工具(E2.11)。

存在理由:指揮艙的版面靠空白對齊,而 `len(s)` 對終端機而言是錯的三次方——
跳脫碼佔 0 格卻算進長度、CJK 與 emoji 佔 2 格卻只算 1、截斷會把顏色的重置碼
切掉害後面整片畫面染色。demo 期間三支 pane 各自抄了一份只處理 CJK 的
trunc,連跳脫碼都沒考慮;遷正本後這裡是唯一實作,並由黃金幀逐字看守。

三條契約:
  1. 跳脫碼零寬(CSI/SGR/OSC 都是),永遠原樣保留 —— 它們不佔格但有意義。
  2. 東亞寬字(W/F)兩格,組合附加符號零格,其餘一格。
  3. 截斷後若還有未關閉的 SGR,補上重置碼 —— 顏色不得溢出到後面的內容。

純度:本模組不讀時鐘、不讀環境、不做 I/O。畫面資料一律由呼叫端傳入,
黃金幀才可能逐字重現(tests/test-golden-frames.py 有機械檢查看守這條)。
"""

import re
import sys
import unicodedata

# CSI(含 SGR)與 OSC;兩者都零寬
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")

RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
CLAUDE_ORANGE = "\x1b[38;5;173m"
CODEX_GREY = "\x1b[38;5;252m"
GREY = "\x1b[90m"

# 面板寬度的合理上限。超過就當成「這不是真的終端寬度,是探測」——
# `required_ai_pane_width()` 會用 sys.maxsize 呼叫渲染器量自然寬度。
MAX_SANE_PANE_W = 400
ELLIPSIS = "…"
# 閒置釋放尾標同時承擔畫面與欄寬。若兩處各留一份字面,尾標加長時
# 畫面會變、resize 下限卻不變,失效仍是靜默擠壓。
IDLE_KILL_LABEL = "[X]"
SOFT_KILL_LABEL = "(x)"              # 有 💤（含 💤--）但未達硬關門檻時的軟關入口
_X_CELLS = 4                          # " [X]" 佔 4 格;開席中/載入中列截寬時預留


def _opening_row_with_x(text):
    """開席中/載入中那一列尾端掛 [X](09-14 the maintainer:卡住要能隨時中斷)。"""
    return "%s %s" % (text, IDLE_KILL_LABEL)


def _kill_span_at_end(text, label=None):
    lab = IDLE_KILL_LABEL if label is None else label
    w = visible_width(text)
    kw = visible_width(lab)
    span = {"start": w - kw, "end": w}
    if lab == SOFT_KILL_LABEL:
        span["soft"] = True             # 10-24:軟關入口,hit_test → mouse-ctl 帶 --soft-since
    return span


def seat_kill_tail(ai, activity_text=""):
    """列尾關席標：全符合 → 只 [X]；有 💤（含 --）未達門檻 → (x)；否則空。

    the maintainer 2026-09-21：有 zzz 就顯示 (x)；達硬關門檻只顯示 [X]、不並排 (x)。
    """
    if ai.get("idle_kill"):
        return " " + IDLE_KILL_LABEL, IDLE_KILL_LABEL
    if str(activity_text or "").startswith("💤"):
        return " " + SOFT_KILL_LABEL, SOFT_KILL_LABEL
    return "", ""
DMQ_SEND_NOW = "[立刻送出]"
DMQ_EDIT = "[編輯]"
DMQ_DELETE = "[刪除]"
DMQ_MOVE = "[上下移動]"

# ---- 引擎品牌正本(08-14 the maintainer 兩連裁:三字縮寫+每字母品牌配色) ------------
# 縮寫 2→3 字:CLA/CDX/AGY/CPT(提案值,the maintainer 眼驗可改字);gemini 併 AGY
# 家族。配色=每字母 (R,G,B,256近似) 資料化一處:真色走 24-bit SGR 38;2,
# 偵測不到真色能力(COLORTERM 含 truecolor/24bit;EZ2_AIB_TRUECOLOR=0/1
# 可強制)退 256 近似。AGY=Gemini 漸層三字一色階(A 藍→G 紫→Y 紅);
# CLA=Anthropic 陶土 #D97757(維持 173 基調);CDX=OpenAI 灰(維持 252);
# CPT=Copilot 藍 #0078D4 候用。SGR 零寬,行寬與 rowmap 不歪(E2.11 判準)。
ENGINE_LETTERS = {
    "claude": "CLA",
    "codex": "CDX",
    "gemini": "AGY",
    "agy": "AGY",
    "copilot": "CPT",
    "grok": "GRK",
    "deepseek": "DSK",
    "nvidia": "NVD",
    "openrouter": "ORR",
    "typesafe": "TSF",
    "openrouterjev": "ORJ",
    "sand": "SND",
    "codexbot": "BOT",
    "grokbot": "GBT",
    "claudebot": "CBT",
    "musebot": "MSB",
}
# R6 引擎徽章 icon(the maintainer 08-30 眼驗選定;字碼由本機 JetBrainsMonoNerdFont cmap 量出,不是查表)。
# ★ 前三個是真品牌標;後兩個**不是**:md-alpha_a_box(方框 A)、md-alpha_x_box(方框 X)是**語意選字**,
#   字型裡沒有 Antigravity / xAI 官方標——下一個人不要把它們當官方標。
# ★ 2026-09-01 換字(the maintainer 眼驗指出):agy 原本是 cod-magnet(磁=反重力語意)。換 md-alpha_a_box 的理由
#   有三,都可反證:(1) Antigravity 的標本來就是一個 A,磁鐵要看註解才知道在指什麼;
#   (2) 換後 agy/grok 成為 alpha_a_box / alpha_x_box **同家族相鄰字碼**,一眼看得出這兩個是同一類
#       「無官方標的語意選字」,而 cod-magnet 混在 cod- 前綴的三個真品牌標裡會被誤讀成第四個真標;
#   (3) 寬度不變(三態仍 3/1/4),配色照吃 Gemini 漸層首色 #4285F4。
#   ★ 這**不是**升級成官方標:字型裡仍然沒有 Antigravity 的標(2026-09-01 對 Nerd Fonts 3.5.0
#     的 12,256 個字形掃 post 表,grok/xai/antigrav 三個關鍵字**零命中**)。換的是選得比較準的語意字,
#     不是換到真標。再開條件=Nerd Fonts 上游收錄 Antigravity / xAI 官方標。
# ★ Nerd Font 私有區這幾個字碼在 east_asian_width 是 N/A,產品 char_width 量 1 格(穩定單欄);
#   不用 emoji 的理由就是 emoji 是「模糊寬」字,同一字在不同終端佔 1 或 2 欄。
# ★ R8:grok 的 icon。定義排在 ENGINE_ICONS **之前** —— 表裡引用它,
#   放在表後會是 NameError(而那個錯在 import 時就炸,不會安靜)。
GROK_ICON = "\U0001d54f"   # 𝕏 MATHEMATICAL DOUBLE-STRUCK CAPITAL X(the maintainer 10-09 定:xAI 無字型標,用 𝕏;舊 md-alpha_x_box 退役)
SAND_ICON = "\U000f0b1c"   # md-alpha_s_box U+F0B1C 語意選字(Sand/Cursor 無官方標)
CODEXBOT_ICON = "\uec81"  # OpenAI 標（Desktop bridge；Cursor 橋 id 已刪）
GROKBOT_ICON = GROK_ICON  # the maintainer 10-09:Grok 品牌一律 𝕏(以前與 sand 同標;字母 GBT 仍區分 ≠ grok CLI)
CLAUDEBOT_ICON = "\uec82"  # cod-claude Anthropic 官方標(與 claude CLI 同標;字母 CBT 區分)
MUSEBOT_ICON = "\u221e"  # ∞ INFINITY(the maintainer 10-09 定:Meta 的無限環;Nerd Font 無 Meta 官方標。east_asian_width=A,終端設 ambiguous=wide 會占 2 格)
AGY_ICON = "\U000f0b08"    # md-alpha_a_box U+F0B08 語意選字(Antigravity 無可用字型標;與 GROK_ICON 同家族)
DEEPSEEK_ICON = "\U000f0b0b"  # md-alpha_d_box U+F0B0B 語意選字(DeepSeek 無 Nerd Font 官方標)
NVIDIA_ICON = "\uec19"  # cod-chip
TYPESAFE_ICON = ""  # fa-shield
OPENROUTER_ICON = ""  # fa-globe
ENGINE_ICONS = {
    "claude": "\uec82",    # cod-claude   U+EC82 Anthropic 官方標
    "codex": "\uec81",     # cod-openai   U+EC81 OpenAI 官方標
    "copilot": "\uec1e",   # cod-copilot  U+EC1E Copilot 官方標
    "gemini": AGY_ICON,    # md-alpha_a_box U+F0B08(AGY 舊鍵,R7 退役另件——兩鍵同值是已知缺陷)
    "agy": AGY_ICON,       # md-alpha_a_box U+F0B08 語意選字(Antigravity 無官方標)
    "grok": GROK_ICON,     # md-alpha_x_box U+F0B1F 語意選字(xAI 無可用字型標)
    "deepseek": DEEPSEEK_ICON,
    "nvidia": NVIDIA_ICON,
    "openrouter": OPENROUTER_ICON,
    "typesafe": TYPESAFE_ICON,
    "openrouterjev": TYPESAFE_ICON,   # 同一套 System One 問答介面,鑰是 OpenRouter 的
    "sand": SAND_ICON,     # md-alpha_s_box U+F0B1C 語意選字(Sand/Cursor 橋接)
    "codexbot": CODEXBOT_ICON,
    "grokbot": GROKBOT_ICON,
    "claudebot": CLAUDEBOT_ICON,
    "musebot": MUSEBOT_ICON,
}
BADGE_MODES = ("both", "icon", "letters")
# ★ 右欄 pane 寬的**單一預設值**(the maintainer 2026-09-02 眼驗裁:27→28,讓「🎭 第二艦長 [解人格]」
#   這種四字人格名在預設寬下完整)。渲染器拿到的是 pane 寬減 1(pane-fit 保留一格),
#   所以 render_ai_list* 的預設 width = DEFAULT_PANE_W - 1。
#   活艙的真寬由 required_ai_pane_width 發布、cockpit-resize.sh 套用,本值只在
#   EZ2_PANE_W 缺席(治具/直呼)時生效;pane-right-ai.sh 從這裡 import,不另寫一份 27/28。
DEFAULT_PANE_W = 28
# ★ AR-3(the maintainer 2026-09-02 眼驗):人格/大腦兩行各自的 icon,與 ENGINE_ICONS 同一張表的
#   鄰居(單源;不得在別處再寫一次字面)。兩者都是 2 格寬(visible_width 量)才對得齊;
#   the maintainer 可換字。舊 📁 字面在這兩行退役,folder 路徑文字照舊。
ATTR_ICONS = {"persona": "\U0001f3ad", "brain": "\U0001f9e0"}   # 🎭 / 🧠
# 人格/大腦兩行的動作字面(zh-TW 預設)。渲染器**不讀 i18n**(黃金幀純度紀律),
# 由 pane-right-ai.sh 從 i18n `aib.seat.*` 五鍵注入 data["labels"];沒注入就走這裡。
# ★ 本表必須與 i18n/zh-TW.json 逐字相等 —— 臂 tests/test-tree-rails-ar-0902.py 對帳,
#   兩處分岔時紅。[解綁] 字面退役(AR-3 第 3 條):人格行 [解人格]、專案行 [換專案]。
# ★ R16(the maintainer 2026-09-26 裁):畫面用字「人格(第一大腦)」「專案(第二大腦)」;括號版只出現在
#   選單標題與開席第一次說明(i18n aib.bindmenu.* / aib.onboard.brain_intro),右欄這五個字面用短字。
# ★ 10-17 the maintainer 裁:大腦行按鈕改「換第二大腦(專案)」「綁第二大腦(專案)」(標題讓位、按鈕留著)。
#   識別字不動:本表的鍵、ATTR_ICONS 的 "brain"、路由 core.* 全部照舊 —— 改的只有看得見的字。
DEFAULT_LABELS = {
    "persona_unbind": "解人格",
    "persona_bind": "綁人格",
    "brain_change": "換第二大腦(專案)",
    "brain_bind": "綁第二大腦(專案)",
    "unbound": "未綁",
}
_AGY_GRADIENT = (((66, 133, 244), 69),     # A=#4285F4 藍
                 ((155, 114, 203), 135),   # G=#9b72cb 紫
                 ((217, 101, 112), 168))   # Y=#d96570 紅
ENGINE_LETTER_COLORS = {
    "claude": (((217, 119, 87), 173),) * 3,    # #D97757 × 3
    "codex": (((208, 208, 208), 252),) * 3,    # 灰 × 3
    "gemini": _AGY_GRADIENT,
    "agy": _AGY_GRADIENT,
    "copilot": (((0, 120, 212), 32),) * 3,     # #0078D4 × 3(候用)
    # ★ grok **不設色**(RULING-R6 §R8,the maintainer 眼驗定):xAI 的標是黑白單色、隨底反轉,
    #   挑任何 hex 都會在另一種底上錯。空 tuple 走 render_engine_badge 的
    #   `if not cols: return label` 那條路 = 終端預設前景(深底白、淺底黑)。
    #   ★ 用空 tuple 而不是「不放這個鍵」:三張表鍵集必須相等,缺鍵是紅。
    "grok": (),
    # DeepSeek 藍 #4D6BFE；256≈69
    "deepseek": (((77, 107, 254), 69),) * 3,
    "nvidia": (((118, 185, 0), 70),) * 3,  # NVIDIA green #76B900
    "openrouter": (((16, 185, 129), 70),) * 3,  # emerald #10B981
    "typesafe": (((20, 184, 166), 73),) * 3,  # teal #14B8A6 TypeSafe
    "openrouterjev": (((16, 185, 129), 35),) * 3,  # emerald #10B981(OpenRouter 色);256≈35,與 openrouter(70)/codexbot(36) 分開
    "sand": (),
    # Desktop codexbot（舊 codexapp）: ChatGPT 綠 #10A37F；256≈36
    "codexbot": (((16, 163, 127), 36),) * 3,
    # Cursor grokbot: 紫 #A855F7；256≈141（與無色 grok CLI 分開）
    "grokbot": (((168, 85, 247), 141),) * 3,
    # Claude app 橋接席:Claude 橘 #D97757;256≈173(與無色 claude CLI 分開)
    "claudebot": (((217, 119, 87), 173),) * 3,
    # Meta Muse 橋接席:Meta 藍 #5B8DEF;256≈69(與 DeepSeek 同 256 碼,真色不同;the maintainer 可換)
    "musebot": (((91, 141, 239), 69),) * 3,
}
# 舊二字表的單色概念保留為「首字母色」供任何殘存單色消費處(如 golden
# 測試)參照;主要消費=render_engine_badge 的逐字上色。
# ★渲染器純度(黃金幀前提):本模組**不讀環境**——真色能力由採集端偵測
# (COLORTERM/EZ2_AIB_TRUECOLOR)後以 data["truecolor"]/參數注入,缺=256。
# ★ R8:`if cols` 是必要的 —— grok 不設色,它的值是空 tuple,
#   而這裡取 `cols[0][1]`。少了這個條件,**本模組在 import 當下就 IndexError**,
#   整個 render_lib 載不進來。★ 而 tests/test-provider-registry.py 用正則讀原始碼、
#   從不 import 本模組,所以那一批臂對這個炸法是**全綠的**——我就是這樣撞到它的。
ENGINE_COLORS = {
    eng: ("\x1b[38;5;%dm" % cols[0][1])
    for eng, cols in ENGINE_LETTER_COLORS.items() if cols
}


def strip_ansi(s):
    """去掉所有跳脫碼,留下純可見字元。"""
    return _ANSI_RE.sub("", s)


# ---- E2.11 codex 審「高-1」(2026-08-22):動態資料不得帶終端控制字元 -------------
# 渲染器自己加的 SGR(DIM/GREY/品牌色)要保留且零寬——那是 trunc() 刻意留 CSI 的理由。
# 但**資料**(persona 名、路徑、代理標籤、role、seat…)來自席位檔/transcript/採集器,
# 其中任何 ESC/OSC/C0/C1 都會直接寫進 pane:OSC 52 動剪貼簿、清屏、游標移動、換行/tab
# 讓 rowmap 與畫面錯位。修法放在**資料進入點**(三支 render_* 的 data 參數),不放在
# trunc():trunc 同時服務渲染器自己的有色字串,在那裡剝會把品牌色一起剝掉。
# 清法:ESC 開頭的 CSI/OSC 整段拿掉;其餘 C0(含 \n \r \t)、DEL、C1 逐字拿掉;
# 字串以外的值(數字/布林/None)原樣;dict/list 遞迴;鍵名不動(鍵名是程式寫的)。
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f\x80-\x9f]")
_ESC_ANY_RE = re.compile(r"\x1b\[[0-9;:?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\\\)?|\x1b[@-Z\\\\-_]|\x1b")


def clean_text(s):
    """資料字串 → 去掉所有終端控制序列與控制字元;非字串原樣回傳。"""
    if not isinstance(s, str):
        return s
    if "\x1b" in s:
        s = _ESC_ANY_RE.sub("", s)
    return _CTRL_RE.sub("", s)


def clean_data(value):
    """遞迴清理 data 裡的每個字串值(dict/list/tuple);其他型別原樣。"""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return {k: clean_data(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_data(v) for v in value]
    if isinstance(value, tuple):
        return tuple(clean_data(v) for v in value)
    return value


def char_width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def visible_width(s):
    """終端機實際佔用的格數(跳脫碼不算)。"""
    return sum(char_width(c) for c in strip_ansi(s))


def _has_open_sgr(s):
    """字串結尾是否還有未關閉的顏色/樣式。"""
    open_ = False
    for m in _SGR_RE.finditer(s):
        params = m.group(1)
        # `\x1b[m` 與 `\x1b[0m` 都是重置
        open_ = not (params == "" or all(p in ("", "0") for p in params.split(";")))
    return open_


# ── C1.3(2026-09-12,大綱 §89.11.1):等開席單回執那一格要講真話 ─────────────
# the maintainer 09-12 12:5x 看右欄:一席讀序 3 分 51 秒期間那列只畫「開席中 · 等開席單回執」,
# 被讀成「沒帶出正確席位」。要的不是把守望改快,是那一列在等回執期間說得出三件事:
#   投了多久(帳本 outbound 的 hlc.wall_ms 到現在)/ 對方有沒有在動(活動探針的
#   transcript 年齡)/ 逾時才寫逾時(那一態由 opening_attempt_state 的 failed 承擔,
#   本函式不猜)。量不到就各自回 None / "unknown",採集端配 i18n 印「量不到」。
# ★ 本函式是純函式:不讀時鐘、不讀檔、不讀 i18n(黃金幀決定性的前提)。
# ★ 「在動」量的是那一席 transcript 最近一次被寫距今幾秒,不是 pane 有沒有新輸出:
#   面板每幀重採集不留狀態,量不到 pane 的差分;而 CLI 在讀檔時 pane 本來就不捲。
#   門檻 60 秒是人定的,出處寫在這裡,將來有分布可以換掉。
ONBOARD_WAIT_ACTIVE_S = 60


def onboard_wait_state(now_s, sent_ms, activity_age_s, active_s=ONBOARD_WAIT_ACTIVE_S):
    """→ {"waited_s": int|None, "activity": "active"|"idle"|"unknown", "age_s": int|None}。

    waited_s None = 投遞時間量不到(帳本沒 hlc、非數字、或時鐘倒退);
    activity "unknown" = 沒有活動訊號(探針回 None),不猜成沒動。"""
    waited = None
    try:
        span = float(now_s) - float(sent_ms) / 1000.0
        if span >= 0:
            waited = int(span)
    except (TypeError, ValueError):
        waited = None
    age = None
    try:
        if activity_age_s is not None:
            age = int(float(activity_age_s))
    except (TypeError, ValueError):
        age = None
    if age is None or age < 0:
        activity, age = "unknown", None
    elif age <= active_s:
        activity = "active"
    else:
        activity = "idle"
    return {"waited_s": waited, "activity": activity, "age_s": age}  # C13-wait-state-on


def trunc(s, width, ellipsis=ELLIPSIS):
    """截到指定格數;會超過時保留 width-1 格再補省略號。

    跳脫碼原樣保留且不佔格;截斷後若還有未關閉的 SGR,補 RESET
    (不補的話後面整行都會染到那個顏色 —— 這是 demo 版沒處理的洞)。
    """
    if width <= 0:
        return ""
    if visible_width(s) <= width:
        return s
    budget = width - visible_width(ellipsis)
    out, used, i = [], 0, 0
    while i < len(s):
        m = _ANSI_RE.match(s, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        cw = char_width(s[i])
        if used + cw > budget:
            break
        out.append(s[i])
        used += cw
        i += 1
    out.append(ellipsis)
    result = "".join(out)
    if _has_open_sgr(result):
        result += RESET
    return result


def pad(s, width):
    """右補空白到指定格數(已超寬則原樣,截斷是 trunc 的事)。"""
    return s + " " * max(0, width - visible_width(s))


def fit(s, width):
    """恰好 width 格:先截後補。版面每一格都可預測。"""
    return pad(trunc(s, width), width)


def frame(lines, cols=80, rows=24):
    """把任意行數的內容規格化成 rows x cols 的固定畫面。

    行數超過時保留 rows-1 行並以省略號行收尾 —— 靜默吃掉內容會讓畫面說謊,
    留一行可見的記號讓人知道還有東西沒顯示。
    """
    out = [fit(ln, cols) for ln in lines[:rows]]
    if len(lines) > rows:
        out = out[:rows - 1] + [fit(" " + ELLIPSIS + " 還有 %d 行" % (len(lines) - rows + 1), cols)]
    while len(out) < rows:
        out.append(" " * cols)
    return out


def render_frame(lines, cols=80, rows=24):
    """frame() 的字串版(黃金幀檔案格式:行尾 \\n,含尾行)。"""
    return "\n".join(frame(lines, cols, rows)) + "\n"


# -- 三個面板的純渲染函數 --------------------------------------------------
# 資料一律由呼叫端傳入(pane 腳本負責採集、測試傳固定假資料),
# 這裡只負責把資料變成畫面。

def _age_mark(age_minutes):
    """心跳新鮮度標記(採 demo 的 15 分/1 小時分界)。"""
    if age_minutes < 15:
        return GREEN + "🟢" + RESET
    if age_minutes < 60:
        return YELLOW + "🟡" + RESET
    return GREY + "⚫" + RESET


def render_persona(data, width=20):
    """人格面板(對應舊 demo 的 left-persona 渲染段)。"""
    data = clean_data(data)            # E2.11:資料進入點清控制字元
    lines = [DIM + " 人格" + RESET, ""]
    lines.append(" " + trunc(data.get("persona") or "(未綁定)", width - 2))
    lines.append(" uid %s" % (data.get("persona_uid") or "--"))
    if data.get("runtime_available"):
        lines.append(" 事件 %d" % data.get("event_count", 0))
        lines.append(" 訊號 %d 種" % data.get("signal_count", 0))
        lines.append("")
        for sig in (data.get("signals") or [])[:3]:
            lines.append(" " + trunc(sig.get("label", "?"), width - 2))
            lines.append("   %d%% x%d" % (sig.get("strength_percent", 0), sig.get("count", 0)))
        lines.append("")
        lines.append(DIM + " 更新 %s" % (data.get("updated_at") or "--") + RESET)
    else:
        lines.append(GREY + " (runtime store 無資料)" + RESET)
    staged = data.get("staged_total")
    if staged is None:
        lines.append(GREY + " 候審 0(管線休眠)" + RESET)
    else:
        lines.append(" 候審 %d 筆(staging)" % staged)
        lines.append(" -> " + trunc(data.get("staged_target") or "主人格", width - 4))
    return lines


def render_brain(data, width=22):
    """專案(第二大腦)分類樹(對應舊 demo 的 left-brain 渲染段)。

    R16(the maintainer 2026-09-26):畫面抬頭從「第二大腦」改「專案」——同一個畫面不能兩種叫法
    (左欄標籤已是「專案樹」)。函式名、資料鍵、route 模組碼全部不動。
    """
    data = clean_data(data)            # E2.11:資料進入點清控制字元
    if not data.get("registry_available", True):
        return [DIM + " 專案" + RESET, "",
                GREY + " (route registry 不可用)" + RESET,
                GREY + " 設 EZ2_ROUTE_REGISTRY" + RESET]
    expanded = data.get("expanded", False)
    MOD = {"C": "核心", "P": "專家", "F": "流程", "G": "智囊", "A": "Ez2"}
    lines = [DIM + " 專案 %s" % ("(開)" if expanded else "(合)") + RESET, ""]
    for m in ["C", "P", "F", "G", "A"]:
        routes = [r for r in data.get("routes", []) if r.get("module") == m]
        if not routes:
            continue
        writable = sum(1 for r in routes if r.get("ai_writable"))
        lines.append(" %s %s %d路 ✏%d" % (m, MOD.get(m, m), len(routes), writable))
        if not expanded:
            continue
        for r in routes[:6]:
            folder = (r.get("folder") or "").split("/")[-1]
            pen = "✏" if r.get("ai_writable") else "·"
            lock = "🔒" if r.get("data_level") in ("機密", "敏感") else ""
            n = r.get("note_count")
            n = "-" if n is None else str(n)
            lines.append("  ╰%s %s %s%s" % (pen, trunc(folder, 12), lock, n))
        if len(routes) > 6:
            lines.append(GREY + "   …共%d路" % len(routes) + RESET)
    lines.append("")
    lines.append(DIM + " Alt+o 開合 ✏=AI可寫" + RESET)
    return lines


def render_tab_bar(labels, active, hint="", cabin_id=None, width=None):
    """底部分頁列;回傳 (文字, 可點區域)。

    畫面與點擊落點必須同源 —— 分開算的話,標籤一改寬度,滑鼠就會點到隔壁那格
    (而且沒有任何測試會失敗)。spans 是 [{index, start, end}],以顯示格為單位,
    end 不含(半開區間)。cabin_id 在右端,點擊複製艙號。
    """
    parts, spans, col = [], [], 0
    for i, label in enumerate(labels):
        cell = ("[%s]" % label) if i == active else (" %s " % label)
        w = visible_width(cell)
        spans.append({"index": i, "start": col, "end": col + w})
        parts.append(cell)
        col += w
        if i < len(labels) - 1:
            parts.append(" ")
            col += 1
    text = "".join(parts)
    if hint:
        text += "   " + DIM + hint + RESET
    if cabin_id:
        badge = str(cabin_id)
        bw = visible_width(badge)
        used = visible_width(text)
        try:
            pane_w = int(width) if width is not None else 0
        except (TypeError, ValueError):
            pane_w = 0
        pad = 2
        if pane_w > used + bw + 1:
            pad = pane_w - used - bw
        text += (" " * pad) + badge
        start = used + pad
        spans.append({"index": -1, "start": start, "end": start + bw,
                      "kind": "cabin", "cabin": badge})
    return text, spans


def resolve_engine_badge(provider, fallback="--", truecolor=False, mode="letters",
                         icon_available=True, label_override=None):
    """R6:回 {"text","mode","fallback_reason"}——徽章文字、**實際**用的態、退回理由(None=沒退)。

    icon_available 是採集端注入的能力(與 truecolor 同形:函式不自己查字型、不讀環境);
    要 icon 而字型不可用 ⇒ 退 letters,fallback_reason="font_missing"(具名,落進 rowmap detail 可數)。
    mode 不認得 ⇒ letters,fallback_reason="unknown_mode"。未知 provider ⇒ text=fallback、mode 照算。"""
    if mode not in BADGE_MODES:
        effective, reason = "letters", "unknown_mode"
    elif mode != "letters" and not icon_available:
        effective, reason = "letters", "font_missing"
    else:
        effective, reason = mode, None
    text = render_engine_badge(provider, fallback, truecolor=truecolor, mode=effective,
                               icon_available=True, label_override=label_override)
    return {"text": text, "mode": effective, "fallback_reason": reason}


def render_engine_badge(provider, fallback="--", truecolor=False, mode="letters",
                        icon_available=True, label_override=None):
    """品牌徽章,逐字上品牌色(08-14 兩連裁;R6 加 icon 三態)。

    truecolor 由呼叫端注入(渲染器不讀環境,黃金幀決定性):True=24-bit
    SGR,False=256 近似。SGR 零寬:visible_width(badge) 恆等於可見字數,
    行寬/截斷/rowmap 不受染色影響(E2.11 網格判準)。
    未知 provider=fallback 原樣無色(誠實),**不論 mode**。

    R6:mode ∈ BADGE_MODES——letters=字母(3 格,函式預設:落檔/被引用的輸出鎖此態)、
    icon=只圖(1 格)、both=圖+**一格空白**+字母(5 格)。
    ★ 那一格空白是 the maintainer 2026-09-02 眼驗直令(「太近了,要空一隔」):Nerd Font 的圖示
      畫得比字母的字腔滿(NF 版 1000/1000 em vs 字母 500/730),緊貼時圖與 C 會黏成一團。
      空白**算在可見寬裡**(both 4->5),不是零寬——required_ai_pane_width 從實際渲染導出,
      會自己跟上;裁定 RULING-R6 §三的 both=4 已同步改為 5。
    mode 由採集端(engine_badge_mode.resolve_badge_mode)
    判後注入;**本模組不讀環境**。icon 用首字母的品牌色;無色表的 provider 走終端預設前景
    (grok 的「不設色」就是這條路)。mode 不認得 ⇒ 當 letters(往標準模式失效)。"""
    provider = str(provider or "").strip().lower()
    label = ENGINE_LETTERS.get(provider)
    if label is None:
        return fallback or "--"
    # ★ the maintainer 09-05 令:三字碼改成顯示模型名(Opus 5 / gpt-5.6-sol / …)。
    #   規則的正本在 `src/cockpit/model_label.py`,**由採集端算好再傳進來** ——
    #   本模組維持零相依、不讀檔、不讀環境(黃金幀決定性),與 engine_badge_mode 同一條紀律。
    #   量不到模型時 label_override 是 None ⇒ 原樣退回三字碼,而那個退回值
    #   **刻意與正常值長得不一樣**:看到 CLA 就是「引擎知道、模型量不到」。
    override = str(label_override).strip() if label_override else ""
    if mode not in BADGE_MODES or not icon_available:
        mode = "letters"                       # 字型不可用(採集端注入)⇒ 退字母;理由由 resolve_engine_badge 具名
    icon = ENGINE_ICONS.get(provider) if mode != "letters" else None
    if mode == "icon" and not icon:
        mode = "letters"                       # 沒登記 icon 的 provider 退字母(採集端已擋,這裡是第二道)
    if override:
        label = override
    cols = ENGINE_LETTER_COLORS.get(provider)
    # both = 圖 + 一格空白 + 字母(the maintainer 09-02 直令)。icon/letters 兩態不加空白。
    gap = " " if (mode == "both" and icon) else ""
    visible = ((icon or "") if mode != "letters" else "") + gap + (label if mode != "icon" else "")
    if not cols:
        return visible
    tc = bool(truecolor)

    def sgr(rgb256):
        (r, g, b), c256 = rgb256
        return ("\x1b[38;2;%d;%d;%dm" % (r, g, b)) if tc else ("\x1b[38;5;%dm" % c256)

    parts = []
    if icon:
        parts.append(sgr(cols[0]) + icon)
    if gap:
        parts.append(gap)                      # 分隔空白不上色:它不屬於任何一個字母
    if mode != "icon":
        if override:
            # ★★ 整串用品牌**主色**,不走逐字 zip:`cols` 只有 3 個顏色,
            #   `zip("Opus 5", cols)` 會**靜默只吐前 3 個字**——畫面上是
            #   「Opu」,而那看起來像模型名就叫這個,不像掉字。
            #   臂 test-model-badge.py::test_long_model_label_is_not_truncated_by_zip 釘著。
            parts.append(sgr(cols[0]) + label)
        else:
            for ch, col in zip(label, cols):
                parts.append(sgr(col) + ch)
    return "".join(parts) + RESET


# ---- CTX 色帶正本(08-14 the maintainer 直令第七件) --------------------------------
# 「跟對話框下方一樣有顏色,但不用有進度條」——沿 Ez2 CTX 儀表,不另立
# 第二真相源:門檻/色值抄自 ~/.claude/ez2-ctx-core.js 的 THRESHOLDS
# {warn:50,hot:75,crit:95} 與 TIER_COLOR(其上游正本=desktop-shell
# context-usage.js);活艙中欄 57%=38;5;220 實測對齊。只染數字不畫條;
# ?(過期)與 --(無值)不上色維持灰調;truecolor 由呼叫端注入(純度)。
CTX_TIERS = (
    (95, ((255, 107, 107), 203)),   # crit 紅 #ff6b6b
    (75, ((255, 159, 67), 215)),    # hot  橙 #ff9f43
    (50, ((255, 212, 0), 220)),     # warn 黃 #ffd400
    (0, ((88, 166, 255), 75)),      # ok   藍 #58a6ff
)


# ★ 09-07:沒有 by-seat 橋時那一格畫的字。刻意不是數字也不是 `--`:
#   `--` 的既有語意是「完全沒有值」,而這裡是「有值但它是凍的」——
#   兩者的處置不同(前者要去查為什麼沒有,後者去跟那一席講一句話就會更新)。
AWAITING_CTX_LABEL = "待更新"


def ctx_badge(pct, truecolor=False):
    """CTX% 上色(SGR 零寬,行寬不變);pct 非法=原樣灰調不猜帶。"""
    try:
        p = int(pct)
    except (TypeError, ValueError):
        return str(pct)
    for floor, ((r, g, b), c256) in CTX_TIERS:
        if p >= floor:
            sgr = ("\x1b[38;2;%d;%d;%dm" % (r, g, b)) if truecolor \
                else ("\x1b[38;5;%dm" % c256)
            return "%s%d%%%s" % (sgr, p, RESET)
    return "%d%%" % p


def ctx_sgr(pct, truecolor=False):
    """CTX_TIERS 的 SGR 前綴;非法 pct 回空字串(不上色,不猜帶)。"""
    try:
        p = int(pct)
    except (TypeError, ValueError):
        return ""
    for floor, ((r, g, b), c256) in CTX_TIERS:
        if p >= floor:
            return ("\x1b[38;2;%d;%d;%dm" % (r, g, b)) if truecolor \
                else ("\x1b[38;5;%dm" % c256)
    return ""


BAR_FILLED = "\u25b0"   # ▰
BAR_EMPTY = "\u25b1"    # ▱
BAR_CELLS = 10
USAGE_FALLBACK_NOTE = "B at width < A natural; measured in tests/test-usage-panel.py"


def format_probed_age(age_s):
    """量得到列「量於…前」：<1分 → 量於剛剛；<1h → 量於 Nm 前；否則 量於 Nh 前。"""
    try:
        age = int(age_s)
    except (TypeError, ValueError):
        return ""
    if age < 0:
        return ""
    if age < 60:
        return "量於剛剛"
    if age < 3600:
        return "量於 %sm 前" % max(1, age // 60)
    return "量於 %sh 前" % max(1, age // 3600)


def format_expired_age(age_s):
    """量不到列年齡：<1h → Nm；否則 Nh。無法判斷回空字串。"""
    try:
        age = int(age_s)
    except (TypeError, ValueError):
        return ""
    if age < 0:
        return ""
    if age < 3600:
        return "%dm" % max(1, age // 60)
    return "%dh" % max(1, age // 3600)


def usage_bar(pct):
    try:
        p = int(pct)
    except (TypeError, ValueError):
        p = 0
    p = max(0, min(100, p))
    filled = max(0, min(BAR_CELLS, int(round(p / 10.0))))
    return BAR_FILLED * filled + BAR_EMPTY * (BAR_CELLS - filled)


# ---- AI任務區進度條色(the maintainer 09-15 02:5x;a seat 裁 A:色值與 SGR 只住這裡)------------
# 已完成段依百分比上色:<34 橘、34–66 黃、≥67 綠、100 亮綠;▱ 暗灰;百分比數字與已完成段同色。
# 格子數與填法沿用 usage_bar(不另算第二份)。color ∈ None / "256" / "truecolor",由呼叫端注入(純度);
# None(NO_COLOR、非終端、呼叫端沒給)⇒ 零跳脫碼。色值集中在這一處,眼驗後改這裡。
PROGRESS_TIERS = (
    (100, ((0, 255, 135), 48)),     # 完成 亮綠
    (67, ((63, 185, 80), 34)),      # 綠
    (34, ((255, 212, 0), 220)),     # 黃
    (0, ((255, 159, 67), 208)),     # 橘
)
PROGRESS_EMPTY_COLOR = ((88, 88, 88), 240)   # ▱ 暗灰
PROGRESS_COLOR_MODES = ("256", "truecolor")


def _progress_fg(spec, color):
    (r, g, b), c256 = spec
    if color == "truecolor":
        return "\x1b[38;2;%d;%d;%dm" % (r, g, b)
    return "\x1b[38;5;%dm" % c256


def progress_sgr(pct, color=None):
    """PROGRESS_TIERS 的 SGR 前綴;color 不是 256/truecolor 或 pct 非法 ⇒ 空字串(純文字,不猜帶)。"""
    if color not in PROGRESS_COLOR_MODES:
        return ""
    try:
        p = max(0, min(100, int(pct)))
    except (TypeError, ValueError):
        return ""
    for floor, spec in PROGRESS_TIERS:
        if p >= floor:
            return _progress_fg(spec, color)
    return ""


def progress_pct(pct, color=None):
    """「NN%」,與已完成段同色;pct 非法印「?%」不猜 0。"""
    try:
        p = max(0, min(100, int(pct)))
    except (TypeError, ValueError):
        return "?%"
    sgr = progress_sgr(p, color)
    return "%s%d%%%s" % (sgr, p, RESET) if sgr else "%d%%" % p


def progress_bar(pct, color=None):
    """`▰▱▱▱▱▱▱▱▱▱ 10%`。color=None ⇒ 與 usage_bar(pct) + " " + progress_pct(pct) 逐位元組相同。"""
    bar = usage_bar(pct)
    sgr = progress_sgr(pct, color)
    if not sgr:
        return bar + " " + progress_pct(pct)
    filled = bar.count(BAR_FILLED)
    empty = BAR_CELLS - filled
    parts = []
    if filled:                                   # 哪一段沒有格子就不印那一段的色碼(0% 與 100% 不留空的 SGR)
        parts.append(sgr + BAR_FILLED * filled)
    if empty:
        parts.append(_progress_fg(PROGRESS_EMPTY_COLOR, color) + BAR_EMPTY * empty)
    return "".join(parts) + RESET + " " + progress_pct(pct, color)


# ---- AI任務區「正在做」大綱樹的色(a seat 09-15 03:0x 畫法第 7 條;色值集中這一處)-----------------
# ▶ 錨點行亮白粗體;🔄 黃、✅ 暗綠、🟡 灰;大項目(◆)行跟進度條同色。無色時靠符號分得開,所以這裡只加色不加字。
OUTLINE_ANCHOR_COLOR = ((255, 255, 255), 231)
OUTLINE_STATE_COLORS = {
    "in_progress": ((255, 212, 0), 220),    # 🔄 黃
    "done": ((63, 140, 80), 65),            # ✅ 暗綠
    "pending": ((138, 138, 138), 245),      # 🟡 灰
}


def outline_paint(text, kind, color=None, pct=None):
    """kind ∈ "head"(◆ 行,同進度條色,要給 pct)/ "anchor"(▶ 行,亮白粗體)/ status-enum 鍵名。
    color 不是 256/truecolor、或 kind 沒有對應色 ⇒ 原樣回傳(零跳脫碼)。"""
    if color not in PROGRESS_COLOR_MODES:
        return text
    if kind == "head":
        sgr = progress_sgr(pct, color)
    elif kind == "anchor":
        sgr = BOLD + _progress_fg(OUTLINE_ANCHOR_COLOR, color)
    elif kind in OUTLINE_STATE_COLORS:
        sgr = _progress_fg(OUTLINE_STATE_COLORS[kind], color)
    else:
        sgr = ""
    return "%s%s%s" % (sgr, text, RESET) if sgr else text


def compact_age(age_minutes):
    """Use one age format for main and child AI: Nm below 1h, then Nh."""
    try:
        age_minutes = max(0, int(age_minutes))
    except (TypeError, ValueError):
        age_minutes = 0
    return "%dm" % age_minutes if age_minutes < 60 else "%dh" % (age_minutes // 60)


def activity_state(age_minutes):
    """活動符號+時間:`🔧2m` / `💤6m` / `💤--`。

    ★ 主 AI 列與子代理列**共用這一支**。原本只有主列有符號,子代理列只印
    時間(08-14「顏色即狀態」的裁示),而 2026-08-16 the maintainer 螢幕實見:
    「子 AI 變黑時沒有 zzz 的閒置符號」——黑球與 💤 表達的不是同一件事
    (黑=收工/離線是**生命週期**,💤=多久沒動是**活動度**),兩者並存才讀得出來。

    ★ 兩處共用一支而不是各寫一份:兩份等值的判斷今天等值、明天改一邊就分岔,
    而分岔的症狀是「同一種閒置在兩層顯示不同」——那種 bug 沒有人會去比對。

    age_minutes 顯式 None = 採集端拿不到真活動訊號,顯示 `--` 不編數字
    (舊版對無訊號席位印「開席到現在」,把密集工作中的席位畫成 💤21h)。
    """
    if age_minutes is None:
        return "💤--"
    return ("🔧%s" if age_minutes < 5 else "💤%s") % compact_age(age_minutes)


# 子 AI 的「已停」圓點。★ the maintainer 2026-08-16:「黑色太明顯,改成深灰色」。
# 選 🌑 而不是把 ⚫ 包灰:**emoji 通常忽略 SGR 前景色**,包了不一定看得出來;
# 🌑 本身就是暗色,不依賴終端機支援。
# 也不選 ● / ⬤ / ◼ ——它們是**單格**(EAW A/N),換上去整片網格會歪;
# ⚫ 與 🌑 同為雙格單 codepoint,換掉零版面風險(實測 char_width 皆 2)。
CHILD_DIM_DOT = "🌑"

# ---- EH.4 圖示字彙的**單一定義處**(守衛與面板讀同一個鍵)------------------
# ★ 為什麼常數放這裡而不是各自寫一份:今晚 handover_probe 的形狀就是兩端各自
#   fallback(寫端預設 pm、讀端預設 unbound),同一個缺失變數兩個預設值,於是
#   那個閘從來沒有開火過。字彙屬於顯示層,所以定義在顯示層,**別處一律 import**。
PERSONA_CREATED_LEGEND_KEY = "aib.legend.persona_created"

#: L9:觀測到的模型 ≠ 開席要求的模型時,掛在引擎徽章後面的小記號。
#: ★★ **字面候 the maintainer 裁**(cap2 09-05「機制先做、字面候 the maintainer」)。改的時候只改這一行 ——
#:   全倉只有這一個字面載體,臂 `test-model-drift-mark.py::test_mark_literal_has_one_carrier`
#:   釘著;第二個載體一出現那一格就紅(承缺陷七判例:兩份剛好一樣,直到某一天不一樣)。
#: ★ 判定機制不在這裡:`src/cockpit/model_label.py:model_drift_state()` 是唯一正本,
#:   由採集端算好、以 `ai["model_drift"]` 布林值傳進來 —— render_lib 維持零相依。
# ★ 為什麼不是 `*`:`seat_status.ONBOARD_MARK` 已經是 `*`(現查)。同一個字元兩個意思,
#   而畫面上只會有一段解釋 —— 那正是 08-22 `ONBOARD_MARK` 換字元那件事的成因。
#   臂 `test-onboard-mark-char.py::test_p1_the_new_mark_collides_with_nothing` 當場擋下我,
#   它的已佔用集合由碼與四語圖例現算(不手抄),所以我加的圖例也會被它讀進去。
#   `≠` 現查不在集合裡,而且語意就是「觀測 ≠ 要求」。
MODEL_DRIFT_MARK = "\u2260"          # ≠
MODEL_REQUESTED_MARK = "\u25cc"      # ◌:開席要求的模型,尚未觀測到(09-14)
# 橋接席沒有 CLI statusline 觀測模型；model_requested 就是顯示名，不掛 ◌。
AIBOT_PROVIDERS = frozenset(("sand", "grokbot", "codexbot", "claudebot", "musebot"))
MODEL_DRIFT_LEGEND_KEY = "aib.legend.model_drift"


# 就緒探測用的最小卡:一張帶 `~` 的機器新建卡。**不是治具是判準的一部分**——
# 它必須走真的 `render_ai_list_with_map`,因為要證的性質只在渲染結果上為真。
_LEGEND_PROBE_AGENT = {
    "mark": "👤", "provider": "claude", "engine": "CLA", "name": "@lgnd0",
    "ctx_percent": None, "ctx_stale": False, "age_minutes": 1,
    "hub_window": "@0", "idle_kill": False, "subs": [],
    "persona_display": "圖例探針", "persona_source": "created",
}


def persona_created_legend_ready(tr):
    """`~`(機器新建)的圖例那一格接上了沒有。**量的是渲染輸出,不是字典。**

    ★★ 2026-08-22 更正(`docs/reports/DEFECT-legend-readiness-is-a-proxy-0822.md`):
      舊版判準是 `tr.t(key) != "!!key!!"` —— 那是「i18n 有這個鍵」,一個**代理指標**。
      `g3` 合入後鍵有了、渲染端仍是空的,於是它回 True、寫端守衛放行,
      而畫面上沒有任何東西解釋 `~` —— **那正是那道守衛存在的唯一理由**。
      代理指標在條件沒被滿足時取到了「表示正常」的那個值,而它分不出來。

    現在的判準分兩段,缺一不可:
      **必要**:字典查得到而且非空(沒有文字就沒有東西可畫)。
      ★ 「非空」不只是防禦性寫法,它是舊判準的**第二個洞**(`a seat` 02:30 值驅動量到):
        `tr.t(key) != "!!key!!"` 對一個**空白字串**的翻譯回 True → 放行,
        而畫得出來的是空的。我當時把它寫成新判準的必要條件,**沒有把它列成舊判準的洞** ——
        覆蓋到與知道要覆蓋是兩件事,只有後者會在下一次改動時被保住,所以它現在是一格判準
        (`tools/probes/eh4-write-guard.py` 的 `f-blank-legend-text-still-blocks`)。
      **充分**:把那段文字交給渲染器,它**真的出現在輸出裡**。

    ★ 第二段同時是 `(b)` 那一段還沒寫的渲染端的**契約**:渲染器收 `data["legend"]`
      裡的字串並把它畫出來。契約寫在判準裡,所以它不會變成一句沒人驗的散文。

    fail-closed:渲染炸掉、輸出讀不到、文字沒出現,三者都回 False(擋)。
    「我看不到規則」不可以被當成「規則說可以」。
    """
    key = PERSONA_CREATED_LEGEND_KEY
    text = tr.t(key)
    if text == "!!%s!!" % key or not str(text).strip():
        return False
    try:
        lines, _rowmap = render_ai_list_with_map(
            {"agents": [dict(_LEGEND_PROBE_AGENT)],
             "legend": {"persona_created": text}}, width=33)
        rendered = "\n".join(strip_ansi(line) for line in lines)
    except Exception:
        return False
    return text in rendered


def model_drift_legend_ready(tr):
    """L9 記號的圖例接上了沒有。**量的是渲染輸出,不是字典。**

    形狀刻意與 `persona_created_legend_ready` 逐條相同 —— 那一支的判準是
    2026-08-22 修過一次才對的(`DEFECT-legend-readiness-is-a-proxy-0822`):
      舊判準 `tr.t(key) != "!!key!!"` 量的是「i18n 有這個鍵」,一個**代理指標**;
      鍵有了而渲染端仍是空的時候它回 True,於是畫面上出現一個沒有人解釋的記號。
    所以這裡照它的兩段:
      **必要**:字典查得到、而且不是空白(沒有文字就沒有東西可畫)。
      **充分**:把那段文字交給渲染器,它**真的出現在輸出裡**。

    ★ 我不是「參考」那支,是**沿用它的判準**:同一個病在同一個檔已經犯過一次,
      新的記號沒有理由重新發明一個比較弱的尺。
    fail-closed:渲染炸掉、讀不到、文字沒出現,三者都回 False(擋)。
    """
    key = MODEL_DRIFT_LEGEND_KEY
    text = tr.t(key)
    if text == "!!%s!!" % key or not str(text).strip():
        return False
    probe = dict(_LEGEND_PROBE_AGENT)
    probe.pop("persona_display", None)
    probe.pop("persona_source", None)
    probe["model"] = "Opus 5"
    probe["model_drift"] = True
    try:
        lines, _rowmap = render_ai_list_with_map(
            {"agents": [probe], "legend": {"model_drift": text}}, width=33)
        rendered = "\n".join(strip_ansi(line) for line in lines)
    except Exception:
        return False
    # 兩件都要:圖例文字畫得出來,而且記號真的掛上去了。
    # 只驗前者的話,圖例在而記號沒畫 —— 那是「解釋了一個畫面上不存在的東西」。
    return text in rendered and MODEL_DRIFT_MARK in rendered


def child_activity(age_minutes, explicit=None):
    """Three child-AI states, preferring verified provider lifecycle."""
    try:
        age_minutes = max(0, int(age_minutes))
    except (TypeError, ValueError):
        age_minutes = 0
    if explicit == "working":
        return GREEN + "🟢" + RESET, "工作"
    if explicit == "idle":
        return "🟡", "閒置"
    if explicit == "offline":
        return CHILD_DIM_DOT, "離線"
    if explicit == "done":
        # the maintainer 08-14 拍板:子 AI 剛收工顯示黑球一段時間(誠實的「做完了」,
        # 與 offline 離線語意分開)
        return CHILD_DIM_DOT, "收工"
    # Pure-renderer fallback keeps fixtures/providers without lifecycle
    # evidence deterministic; the production Codex collector always supplies
    # an explicit value.
    if age_minutes < 2:
        return GREEN + "🟢" + RESET, "工作"
    if age_minutes < 60:
        return "🟡", "閒置"
    return CHILD_DIM_DOT, "離線"


USAGE_LINK_ACTION = "open-usage-page"
USAGE_HEADER_TAP_ACTION = "usage-header-tap"
USAGE_ADD_CODEX_OAUTH_ACTION = "usage-add-codex-oauth"
USAGE_ADD_CODEX_TOKEN_ACTION = "usage-add-codex-token"
USAGE_REPROBE_ACCOUNT_ACTION = "usage-reprobe-account"
USAGE_REBIND_CODEX_OAUTH_ACTION = "usage-rebind-codex-oauth"
USAGE_REDEEM_RESET_ACTION = "usage-redeem-reset"
USAGE_MOVE_ACCOUNT_UP_ACTION = "usage-move-account-up"
USAGE_MOVE_ACCOUNT_DOWN_ACTION = "usage-move-account-down"
USAGE_ACTIVATE_ACCOUNT_ACTION = "usage-activate-account"
USAGE_ACTIVATE_NEXT_ACTION = "usage-activate-next"
USAGE_REMOVE_ACCOUNT_ACTION = "usage-remove-account"

USAGE_REPROBE_ACTION = "reprobe-usage"



# --- usage-codex-subtab-0922: sub-tab row under tz/datetime ---
def _emit_usage_subtab_row(emit, links, unlocked, subtab):
    """Reveal 用量|Codex strip only when unlocked. Display-width accurate hits."""
    try:
        import usage_subtabs as _us
    except Exception:
        return
    row = _us.format_subtab_row(unlocked=bool(unlocked), active_subtab=subtab or "usage")
    if not row:
        return
    hit_links = []
    for sp in _us.subtab_link_spans(row, unlocked=True, active_subtab=subtab or "usage"):
        try:
            link = {
                "start": int(sp["col"]),
                "end": int(sp["col"]) + int(sp["len"]),
                "action": sp.get("action") or "usage-subtab",
                "subtab": sp["subtab"],
                "arg": sp.get("arg") or sp["subtab"],
            }
            hit_links.append(link)
            if isinstance(links, list):
                links.append(link)
        except Exception:
            pass
    emit(row, links=hit_links or None)

def _emit_usage_block(data, emit, width, on_body=None):
    """右欄第 2 頁「AI用量」的正文(09-12 前住在 1.AI 頁最後一席之後、圖例之前)。
    無列=整塊不出現(不是畫空;空頁的那一行由 render_usage_view_with_map 補)。無 window,不入 rowmap。"""
    block = data.get("usage")
    if not isinstance(block, dict):
        return
    rows = block.get("rows")
    title = block.get("title")
    if not isinstance(rows, list) or not rows or not title:
        return
    # the maintainer 09-06 13:2x 裁:「用量 如果量不到就隱藏,只顯示量得到的」。資料層(rows_for_render)
    # 仍給整個列網格——機器、尺、刀讀它;畫面只畫 status=ok 的列,一列都沒有 ⇒ 整塊不出現。
    # (推翻 L5b §2.1/§2.2 的渲染半;「量不到:原因」那句從此只在資料層可見。)
    # the maintainer 09-06 13:5x 第八裁:**過期**(reason_code=stale)的列照樣顯示,後面帶可點的 [重驗]。
    # 「量到」= status 不是 unmeasurable 且 used_pct 是真的 int(bool 不算);不另立函式名,
    # 到達性掃描器把新 def 當新層符號要配刀,而它只是這一段的謂詞。
    _measured = (lambda row: row.get("status") != "unmeasurable"
                 and isinstance(row.get("used_pct"), int)
                 and not isinstance(row.get("used_pct"), bool))
    # the maintainer 09-06 16:3x 第十八裁:「重驗的是要顯示的」—— 探針失敗(probe_failed,例如卡在信任框/登入牆)
    # 的列也顯示並帶 [重驗],人才知道要去那家登入;其他量不到仍隱藏。
    rows = [row for row in rows if _measured(row) or row.get("reason_code") in ("stale", "probe_failed")]
    # the maintainer 09-27 09:49/10:0x 裁(v0.70.0 #1):用量條**常駐**,「預設就應該直接出現,不需點任何東西」;
    #   09-23 的「收起、點箭頭展開十秒」整套拆掉,這裡不再看任何鍵,採集端寫什麼都畫條。
    _bars = True
    try:
        from usage_accounts import panel_payload as _ua_panel
        _ua = _ua_panel()
        if not isinstance(_ua, dict):
            _ua = {}
        _ua_unlocked = bool(_ua.get("unlocked"))
        _ua_subtab = str(_ua.get("subtab") or "usage")
        _ua_active = str(_ua.get("active_codex_oauth_id") or "")
    except Exception:
        _ua = {}
        _ua_unlocked = False
        _ua_subtab = "usage"
        _ua_active = ""
    # 沒寫 codex_pack＝照舊畫（舊測試、套組預設開）。右欄每一幀讀開關。
    # 關掉就當成還沒解鎖：不畫 用量|Codex、不畫帳號列，只留原本的用量。
    _pack_on = data.get("codex_pack", True) is not False
    if not _pack_on:
        _ua_unlocked = False
    # usage-codex-subtab-0922:
    # - locked: show all engines as before
    # - unlocked + 用量: keep non-codex; for codex keep only active binding row(s)
    # - unlocked + Codex: hide overview engine rows (accounts panel owns Codex page)
    if _ua_unlocked:
        try:
            import usage_subtabs as _usf
            if not _usf.should_emit_overview(unlocked=True, subtab=_ua_subtab):
                rows = [row for row in rows if str(row.get("engine") or "") != "codex"]
                # On Codex sub-tab, suppress overview body entirely (panel below).
                rows = []
            else:
                # Overview: non-codex unchanged; codex → active only if we can identify.
                kept = []
                for row in rows:
                    eng = str(row.get("engine") or "")
                    if eng != "codex":
                        kept.append(row)
                        continue
                    # Prefer explicit active markers on the row; else keep first codex only.
                    rid = str(row.get("account_id") or row.get("id") or "")
                    if _ua_active and rid and rid == _ua_active:
                        kept.append(row)
                    elif not _ua_active:
                        # no active id → keep a single codex row (first)
                        if not any(str(k.get("engine") or "") == "codex" for k in kept):
                            kept.append(row)
                    elif row.get("is_active") or row.get("active") or row.get("bound"):
                        kept.append(row)
                if not any(str(k.get("engine") or "") == "codex" for k in kept):
                    # fallback: first codex row if any
                    for row in rows:
                        if str(row.get("engine") or "") == "codex":
                            kept.append(row)
                            break
                rows = kept
                # 作用中 Codex：用 usage_accounts 最新 wham 窗（有 % 就畫條），取代總覽舊探針「量不到」列
                try:
                    _fresh = []
                    _aid = str(_ua_active or "")
                    _pick = None
                    for _a in (_ua.get("accounts") or []):
                        if not isinstance(_a, dict):
                            continue
                        if _aid and str(_a.get("id") or "") == _aid:
                            _pick = _a
                            break
                        if not _aid and (_a.get("status") == "live" or _a.get("bound")):
                            _pick = _a
                            break
                    if _pick is None:
                        for _a in (_ua.get("accounts") or []):
                            if isinstance(_a, dict) and (_a.get("rows") or []):
                                _pick = _a
                                break
                    _win_lab = {
                        "5h": "5小時", "5小時": "5小時",
                        "7d": "每1週", "週": "每1週", "每1週": "每1週",
                        "1週": "每1週", "weekly": "每1週", "30d": "30天",
                    }
                    if isinstance(_pick, dict):
                        for _wr in (_pick.get("rows") or []):
                            if not isinstance(_wr, dict):
                                continue
                            _pct = _wr.get("used_pct")
                            if isinstance(_pct, bool) or not isinstance(_pct, int):
                                continue
                            if _pct < 0 or _pct > 100:
                                continue
                            _w = str(_wr.get("window") or _wr.get("win") or "")
                            _lab = _win_lab.get(_w) or (_w if _w else "用量")
                            _ago = str(_pick.get("probed_ago") or "").strip()
                            if not _ago:
                                try:
                                    from usage_accounts import format_probed_ago as _fpa
                                    _ago = _fpa(_pick.get("probed_at") or _pick.get("last_probe"))
                                except Exception:
                                    _ago = ""
                            _fresh.append({
                                "engine": "codex",
                                "status": "ok",
                                "used_pct": int(_pct),
                                "label": _lab,
                                "label_short": _lab,
                                "account_id": str(_pick.get("id") or ""),
                                "is_active": True,
                                "bound": True,
                                "reset_text": _ago,
                            })
                    if _fresh:
                        rows = [r for r in rows
                                if str(r.get("engine") or "") != "codex"] + _fresh
                except Exception:
                    pass
        except Exception:
            rows = [row for row in rows if str(row.get("engine") or "") != "codex"]
    if not rows and not _ua_unlocked:
        return
    reprobe_token = str(block.get("reprobe") or "[\u91cd\u9a57]")
    prev_engine = None
    truecolor = bool(data.get("truecolor"))
    crit = ctx_sgr(100, truecolor)
    if width > MAX_SANE_PANE_W:
        head = "\u2500 %s \u2500" % title
    else:
        core = "\u2500 %s " % title
        fill = max(1, width - visible_width(core))
        head = core + ("\u2500" * fill)
    # 隱藏門：點標題列累計（mouse-ctl → usage_accounts.tap）；≥3/3s 解鎖同品牌多憑證。
    emit(DIM + head + RESET, links=[{
        "start": 0,
        "end": visible_width(head),
        "action": USAGE_HEADER_TAP_ACTION,
    }])
    # the maintainer 09-06 14:5x 第十三裁:標題下第一行=現在時刻(資料層排好的字串,渲染器不讀時鐘),再空一行。
    # the maintainer 09-10 15:0x 第十七裁:兩行 —— 區域(tz_text)在上、日期時間(now_text)在下,各自置中。
    for _line in (block.get("tz_text"), block.get("now_text")):
        if not _line:
            continue
        # the maintainer 09-06 15:1x 第十四裁:時刻那行置中(寬度超出正常面板就不算,左貼一格)。
        _t = str(_line)
        _padl = max(1, (width - visible_width(_t)) // 2) if width <= MAX_SANE_PANE_W else 1
        emit(DIM + (" " * _padl) + _t + RESET)
    emit("")   # 第十一裁/第十三裁:空一行(rowmap 不入)

    # usage-codex-subtab-0922: 解鎖後才畫 用量|Codex（預設停在用量）
    try:
        from usage_accounts import panel_payload as _ua_panel_st
        _pst = _ua_panel_st() or {}
    except Exception:
        _pst = {}
    _un_st = bool(_pst.get("unlocked")) and _pack_on
    _sub_st = str(_pst.get("subtab") or "usage")
    _links_st = []
    _emit_usage_subtab_row(emit, _links_st, _un_st, _sub_st)
    # 子頁簽列與下方內容空一行（用量總覽列／Codex 多帳面板都適用）
    if _un_st:
        emit("")
    # the maintainer 09-06 13:2x 裁:品牌名左方要有 LOGO。徽章與席列同源(render_engine_badge),
    # mode/字型能力由採集端注入:icon 可用 ⇒ 只圖(1 格)+ 一格空白;不可用 ⇒ 三字碼。
    _mode = data.get("engine_badge_mode", "letters")
    _avail = data.get("engine_icon_available")
    labels = []
    for row in rows:
        engine = str(row.get("engine") or "")
        ok_icon = (_avail.get(engine, True) if isinstance(_avail, dict) else bool(_avail)) \
            if _avail is not None else True
        badge = render_engine_badge(engine, fallback="", truecolor=truecolor,
                                    mode=("icon" if _mode in ("icon", "both") else "letters"),
                                    icon_available=ok_icon)
        # the maintainer 09-06 15:2x 第十五裁:有徽章就只留 LOGO + 種類(label_short),品牌名不印;沒徽章退完整 label。
        label = str(row.get("label") or "")
        short = str(row.get("label_short") or "") or label
        labels.append((badge + " " + short) if badge else label)
    label_w = max([visible_width(x) for x in labels] or [1])
    for row, label in zip(rows, labels):
        # the maintainer 09-06 14:2x 第十裁:「AI 品牌與品牌之間要有個空隔」——換家時先空一行(rowmap 不入)。
        _eng = str(row.get("engine") or "")
        if prev_engine is not None and _eng != prev_engine:
            emit("")
        prev_engine = _eng
        # L5b(缺陷二十六):量不到的列**不得被吃掉**——舊碼 int() 失敗就 continue,
        # 「那一列不在」與「那一家本來就不該畫」逐字相同。量不到列:DIM 灰階空條 + 0% + 過期：Nm/Nh [重驗]。
        pct = row.get("used_pct")
        if not _measured(row):
            # 量不到：與量得到同版式，整列灰階「標籤  ▱… 0%  過期：Nm/Nh [重驗]」
            # LOGO/標籤可點開用量頁；[重驗] 可點重探。
            engine = str(row.get("engine") or "")
            page = {"start": 1, "end": 1 + visible_width(label),
                    "action": USAGE_LINK_ACTION, "engine": engine}
            can_reprobe = row.get("reprobe") is not False
            reason = str(row.get("reason") or "").strip()
            code = str(row.get("reason_code") or "")
            # 探針失敗要讓人看見原因（例如卡在信任框），不要改寫成「過期：?」。
            # 舊列已經寫「過期 N 小時」時，原句留下；沒有句子才用秒數排「過期：Nh」。
            if code == "probe_failed" and reason:
                expired_tail = reason
            elif reason and not row.get("observed_age_s"):
                expired_tail = reason
            else:
                age_txt = format_expired_age(row.get("observed_age_s"))
                if not age_txt:
                    try:
                        _h = row.get("hours")
                        if _h is None and reason:
                            import re as _re
                            _m = _re.search(r"(\d+)\s*(?:h|小時)", reason)
                            if _m:
                                _h = int(_m.group(1)) * 3600
                        if _h is not None and not isinstance(_h, bool):
                            _hi = int(_h)
                            if _hi < 1000:
                                age_txt = "%dh" % max(1, _hi)
                            else:
                                age_txt = format_expired_age(_hi)
                    except (TypeError, ValueError):
                        age_txt = ""
                if not age_txt:
                    age_txt = "?"
                expired_tail = "過期：%s" % age_txt
            _rt = str(row.get("reset_text") or "").strip()
            if _rt and _rt not in expired_tail:
                expired_tail = "%s · %s" % (expired_tail, _rt)
            if can_reprobe and reprobe_token not in expired_tail:
                expired_tail = expired_tail + " " + reprobe_token
            pad = max(1, label_w - visible_width(label) + 2)
            # 過期仍儘量顯示上次／下界％（Grok/Copilot 上限打滿常見），無則 0
            _hint = row.get("lower_bound_pct")
            if not (isinstance(_hint, int) and not isinstance(_hint, bool)):
                _hint = row.get("last_used_pct")
            has_hint = isinstance(_hint, int) and not isinstance(_hint, bool) and 0 <= _hint <= 100
            _gray = "\033[90m"
            # 沒有上次的百分比就不要畫 0%：那和「量到 0」長得一樣，過期列的測試也不准出現 %。
            if has_hint:
                bar0 = usage_bar(_hint) if _bars else ""
                body = "%s %d%%  %s" % (bar0, _hint, expired_tail)
            else:
                bar0 = ""
                body = expired_tail
            head_plain = " %s%s%s" % (label, " " * pad, body)
            one = " %s%s%s%s%s" % (label, " " * pad, _gray, body, RESET)

            def _rp(text):
                if not can_reprobe:
                    return []
                w = visible_width(text)
                return [{"start": w - visible_width(reprobe_token), "end": w,
                         "action": USAGE_REPROBE_ACTION, "engine": engine}]

            if width > MAX_SANE_PANE_W or visible_width(head_plain) <= width:
                emit(one, links=[page] + _rp(head_plain))
            else:
                head_only = " %s%s%s%s%s" % (
                    label, " " * pad, _gray,
                    (bar0 + (" %d%%" % _hint) if has_hint else ""), RESET)
                emit(head_only, links=[page])
                second_plain = "   " + expired_tail
                if visible_width(second_plain) > width:
                    keep = max(1, width - visible_width(" " + reprobe_token) - 1)
                    second_plain = "   " + ("過期：%s" % age_txt)[:keep] + "…" + (
                        (" " + reprobe_token) if can_reprobe else "")
                emit(_gray + second_plain + RESET, links=_rp(second_plain) or None)
            continue
        reset_text = str(row.get("reset_text") or "")
        credit_text = str(row.get("reset_credit_text") or "")
        # 各引擎量得到列都帶「量於…前」；若 reset_text 已是量於字樣（Codex fresh）不重複
        probed = format_probed_age(row.get("observed_age_s"))
        if reset_text.startswith("量於"):
            probed = reset_text
            reset_text = ""
        tail = " · ".join(x for x in (probed, reset_text, credit_text) if x)
        capped = bool(row.get("capped")) or pct >= 100
        pad = max(1, label_w - visible_width(label) + 2)
        bar = usage_bar(pct) if _bars else ""
        # the maintainer 09-06 12:2x 裁:每列一律「標籤  ▰▱▱▱▱▱▱▱▱▱ 8%」。一行放不下就把重置文字
        # 換到**下一行縮排**,不再退成無條的 B 版面(舊碼窄於 A 自然寬就丟條——40 格活艙
        # 十列只剩 Copilot 一列有條,因為只有它沒有重置文字)。第二行 DIM,超寬截「…」。
        links = [{"start": 1, "end": 1 + visible_width(label),
                  "action": USAGE_LINK_ACTION, "engine": str(row.get("engine") or "")}]
        head_plain = " %s%s%s %d%%" % (label, " " * pad, bar, pct)
        one_line = head_plain + ("  " + tail if tail else "")
        # the maintainer 09-06 15:2x 第十五裁(取代第十二裁):左方只留 LOGO 後,日期與重置放得下就同一行;
        # 放不下才換到第二行縮排(第三裁:條永遠在)。
        fits = width > MAX_SANE_PANE_W or visible_width(one_line) <= width
        second = "   " + tail
        if tail and width <= MAX_SANE_PANE_W and visible_width(second) > width:
            second = second[:max(1, width - 1)] + "\u2026"
        if capped and crit:
            if fits:
                emit(crit + one_line + RESET, links=links)
            else:
                emit(crit + head_plain + RESET, links=links)
                if tail:
                    emit(crit + second + RESET)
            continue
        sgr = "" if capped else ctx_sgr(pct, truecolor)
        head = head_plain if not sgr else " %s%s%s %s%d%%%s" % (
            label, " " * pad, sgr + bar + RESET, sgr, pct, RESET)
        if fits:
            emit(head + ("  " + tail if tail else ""), links=links)
        else:
            emit(head, links=links)
            if tail:
                emit(DIM + second + RESET)
    emit("")   # the maintainer 09-06 14:4x 第十一裁:圖例上方空一行(用量塊有畫才空)
    if on_body:
        on_body()
    # usage-codex-subtab-0922: accounts panel only on Codex sub-tab
    try:
        import usage_subtabs as _us_gate
        _ua2 = (data or {}).get("usage_accounts") or {}
        if not isinstance(_ua2, dict):
            _ua2 = {}
        try:
            from usage_accounts import panel_payload as _ua_panel
            _panel = _ua_panel() or {}
        except Exception:
            _panel = {}
        _un2 = _pack_on and bool(_panel.get("unlocked") or _ua2.get("unlocked") or (data or {}).get("unlocked"))
        _st2 = (
            (_panel.get("subtab") if isinstance(_panel, dict) else None)
            or _ua2.get("subtab")
            or (data or {}).get("subtab")
            or "usage"
        )
        if _us_gate.should_emit_accounts_panel(unlocked=_un2, subtab=_st2):
            _buf = []
            def _cap(line, links=None, **kw):
                _buf.append(line)
                return emit(line, links=links, **kw) if links is not None else emit(line, **kw)
            try:
                _emit_usage_accounts_panel(_cap, width, _bars)
            except TypeError:
                _emit_usage_accounts_panel(emit, width, _bars)
                _buf = ["ok"]
            if not _buf:
                emit(DIM + " （尚無 Codex 帳號；點下方 [+] OAuth 新增）" + RESET)
    except Exception:
        try:
            emit(DIM + " （Codex 面板載入失敗）" + RESET)
        except Exception:
            pass


# Codex 帳號頁的按鈕說明列。按鈕之間的分隔:前兩個用「｜」,自動接續與重新登入之間是空白。
OAUTH_LEGEND_TOKENS = ("[X]刪除", "🚀 切成主用", "🔀 自動接續", "🔄 重新登入", "👆👇 調整順序")
OAUTH_LEGEND_SEPS = ("｜", "｜", " ", "｜")


def _wrap_oauth_legend(width, tokens=OAUTH_LEGEND_TOKENS, seps=OAUTH_LEGEND_SEPS):
    """按鈕說明照欄寬換行;回每一行(都帶前導 1 空白,與上方用量列對齊)。
    一顆按鈕不拆;單顆就比欄寬還寬時獨佔一行(交給 emit 截)。欄寬不合理時不換行。"""
    try:
        w = int(width)
    except (TypeError, ValueError):
        w = 0
    wrap = 2 <= w <= MAX_SANE_PANE_W
    lines, cur = [], " "
    for i, tok in enumerate(tokens):
        piece = tok if not cur.strip() else seps[i - 1] + tok
        if wrap and cur.strip() and visible_width(cur + piece) > w:
            lines.append(cur)
            cur = " " + tok
        else:
            cur += piece
    lines.append(cur)
    return lines


def _emit_usage_accounts_panel(emit, width, bars=True):
    """解鎖後：① 用量按 OAuth email 分組 ② [OAuth] 槽位列。列首符號看這一格的登入驗證。"""
    try:
        from usage_accounts import panel_payload
        panel = panel_payload()
    except Exception:
        return
    if not isinstance(panel, dict) or not panel.get("unlocked"):
        return

    def _win_label(win):
        w = str(win or "")
        if w in ("5h", "5小時"):
            return "5小時"
        if w in ("7d", "週", "每1週", "1週", "weekly"):
            return "每1週"
        return w or "?"

    oauth_accts = [a for a in (panel.get("accounts") or [])
                   if isinstance(a, dict) and a.get("kind") == "oauth"]
    token_accts = [a for a in (panel.get("accounts") or [])
                   if isinstance(a, dict) and a.get("kind") == "token"]

    # ① usage grouped by email (OAuth only); same email → prefer live slot rows
    by_email = {}
    for acct in oauth_accts:
        email = str(acct.get("oauth_account") or "").strip() or "（未讀到 email）"
        by_email.setdefault(email, []).append(acct)
    first_email = True
    for email, group in by_email.items():
        # 與總覽一致：子頁簽下只空一格；帳號塊之間才再空一行（首塊不再多空）
        if first_email:
            first_email = False
        else:
            emit("")
        pick = next((a for a in group if a.get("bound") or a.get("status") == "live"), group[0])
        plan = str(pick.get("plan_type") or "").strip()
        if plan:
            emit(" " + email + " [AI方案：" + plan + "]")
        else:
            emit(" " + email)
        rows = pick.get("rows") if isinstance(pick.get("rows"), list) else []
        if rows:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                win = _win_label(row.get("window"))
                pct = row.get("used_pct")
                _ago = str(pick.get("probed_ago") or "").strip()
                if isinstance(pct, int) and not isinstance(pct, bool):
                    # 與總覽量得到同版式：色階條 + % + 量於…前。條收起時只留百分比。
                    sgr = ctx_sgr(pct, False)
                    if bars:
                        bar = usage_bar(pct)
                        if sgr:
                            core = "%s %s%d%%%s" % (sgr + bar + RESET, sgr, pct, RESET)
                        else:
                            core = "%s %d%%" % (bar, pct)
                    elif sgr:
                        core = "%s%d%%%s" % (sgr, pct, RESET)
                    else:
                        core = "%d%%" % pct
                    line = "  %s  %s" % (win, core)
                    if _ago:
                        line = "%s · %s" % (line, _ago)
                    elif pick.get("observed_age_s") is not None:
                        # 年齡由採集端算好。這裡再讀時鐘，黃金畫面就無法逐字重現。
                        try:
                            _ago2 = format_probed_age(pick.get("observed_age_s"))
                        except (TypeError, ValueError):
                            _ago2 = ""
                        if _ago2:
                            line = "%s · %s" % (line, _ago2)
                else:
                    # 與總覽量不到同版式：強制灰空條 + 0% + 過期：Nm/Nh
                    _age_txt = ""
                    try:
                        if pick.get("observed_age_s") is not None:
                            _age_txt = format_expired_age(pick.get("observed_age_s"))
                    except (TypeError, ValueError):
                        _age_txt = ""
                    _gray = "\033[90m"
                    body = ("%s 0%%" % usage_bar(0)) if bars else "0%"
                    if _age_txt:
                        body = "%s  過期：%s" % (body, _age_txt)
                    else:
                        body = "%s  過期：?" % body
                    line = "  %s  %s%s%s" % (win, _gray, body, RESET)
                if width <= MAX_SANE_PANE_W and visible_width(line) > width:
                    line = trunc(line, width)
                emit(line)
        else:
            st = str(pick.get("status_label") or ("可用" if pick.get("bound") else "失效"))
            line = "  （%s · 尚無用量列）" % st
            if width <= MAX_SANE_PANE_W and visible_width(line) > width:
                line = trunc(line, width)
            emit(DIM + line + RESET)
                # Codex 使用重置額度：有可用才畫；[使用重置] 只給主登入
        try:
            from reset_credits_view import (
                format_reset_lines, redeem_link_span, should_show_reset_block,
            )
            _rc = pick.get("reset_credits") if isinstance(pick.get("reset_credits"), dict) else None
            if _rc is None and isinstance(pick.get("usage"), dict):
                _rc = pick["usage"].get("reset_credits")
            _aid = str(pick.get("id") or "")
            _active_now = str(panel.get("active_codex_oauth_id") or "")
            _is_active = bool(_aid) and _aid == _active_now
            if should_show_reset_block(_rc):
                for _ln in format_reset_lines(_rc, is_active=_is_active, indent="  "):
                    _links = None
                    if _is_active and "[使用重置]" in _ln:
                        _span = redeem_link_span(_ln, indent="  ")
                        if _span:
                            _links = [{
                                "start": int(_span["col"]),
                                "end": int(_span["col"]) + int(_span["len"]),
                                "action": _span["action"],
                                "account_id": _aid,
                            }]
                    # 有熱區的列不要包 DIM：ANSI 會讓 mouse hit 的 start/end 偏掉
                    if _links:
                        emit(_ln, links=_links)
                    else:
                        emit(DIM + _ln + RESET)
        except Exception:
            pass

    # ② slot list — 左縮排與上方用量列同（前導 1 空白）
    emit("")
    emit(" [OAuth]")
    # v0.70.0 #10(the maintainer 09-27 10:0x「如果右方沒有多個 Codex 帳號,就不應該出現自動輪替重登帳號」):
    #   只有一個帳號 ⇒ 圖例只剩刪除與重新登入,每列只留 🔄 與 [X],不掛切換/接續/調序熱區;
    #   兩個以上照舊,並把自動接續畫成開/關(資料早有 failover_auto_enabled,只是沒畫)。
    # 欄寬不夠時照按鈕自動換行(the maintainer 09-26 12:3x「字要能自動換行」):一個按鈕不拆成兩行,
    # 放不下就整顆移到下一行;🔀 的點擊區跟著它落在哪一行重算。欄寬夠時輸出與原本逐字相同。
    multi = len(oauth_accts) >= 2
    if multi:
        # 開/關是獨立一小顆、緊貼在 🔀 後面(分隔字是空字串):窄欄時按鈕本身不會因為多了三格而超寬,
        # 點擊區仍只蓋「🔀 自動接續」那一顆。
        next_tok = "🔀 自動接續"
        state_tok = "(開)" if panel.get("failover_auto_enabled") else "(關)"
        i = OAUTH_LEGEND_TOKENS.index(next_tok)
        legend_tokens = OAUTH_LEGEND_TOKENS[:i + 1] + (state_tok,) + OAUTH_LEGEND_TOKENS[i + 1:]
        legend_seps = OAUTH_LEGEND_SEPS[:i] + ("",) + OAUTH_LEGEND_SEPS[i:]
    else:
        next_tok = ""
        legend_tokens = ("[X]刪除", "🔄 重新登入")
        legend_seps = ("｜",)
    for _leg in _wrap_oauth_legend(width, legend_tokens, legend_seps):
        hdr_links = []
        _ni = _leg.find(next_tok) if next_tok else -1
        if _ni >= 0:
            hdr_links.append({
                "start": visible_width(_leg[:_ni]),
                "end": visible_width(_leg[:_ni]) + visible_width(next_tok),
                "action": USAGE_ACTIVATE_NEXT_ACTION,
            })
        emit(_leg, links=hdr_links or None)
    emit("")

    oauth_n = len(oauth_accts)
    active_id = str(panel.get("active_codex_oauth_id") or "")
    for i, acct in enumerate(oauth_accts):
        seq_label = str(acct.get("seq_label") or ("%03d" % (i + 1)))
        acct_id = str(acct.get("id") or "")
        mark = str(acct.get("login_glyph") or "")
        if mark not in ("✅", "❗", "❌", "🔄", "需重登"):
            mark = "❗"
        # 正在用的那格用黃底，不再寫「使用中」。火箭仍是切換鈕。
        is_active = bool(acct_id) and acct_id == active_id
        email = str(acct.get("oauth_account") or "").strip() or "—"
        bound_label = str(acct.get("last_bound_label") or "").strip() or "—"
        can_up = bool(acct.get("can_move_up")) if "can_move_up" in acct else (i > 0)
        can_down = bool(acct.get("can_move_down")) if "can_move_down" in acct else (i + 1 < oauth_n)
        #  mark 001 |🚀|🔄|👆|👇|更新:…| email |[X]  （前導空白對齊上方用量）
        head = " %s %s |" % (mark, seq_label)
        activate = "🚀"
        rebind = "🔄"
        up = "👆"
        down = "👇"
        delete = "[X]"
        btn_sep = "|"
        # 窄欄：列尾 [X] 必留。先保住 suffix，再縮 email；仍不夠就拿掉 👆👇。
        suffix = " |" + delete
        def _prefix(with_move: bool) -> str:
            mid = [head, activate, btn_sep, rebind] if multi else [head, rebind]
            if with_move and multi:
                mid += [btn_sep, up, btn_sep, down]
            mid.append("|更新:%s| " % bound_label)
            return "".join(mid)
        prefix = _prefix(True)
        if width <= MAX_SANE_PANE_W and visible_width(prefix) + visible_width(suffix) + 1 > width:
            prefix = _prefix(False)
        if width <= MAX_SANE_PANE_W:
            budget = width - visible_width(prefix) - visible_width(suffix)
            if budget < 1:
                # 最後手段：縮短更新時間標籤
                prefix = "".join([head, activate, btn_sep, rebind, "| "] if multi else [head, rebind, "| "])
                budget = max(1, width - visible_width(prefix) - visible_width(suffix))
            email_disp = email if visible_width(email) <= budget else trunc(email, budget)
        else:
            email_disp = email
        body = prefix + email_disp + suffix
        if width <= MAX_SANE_PANE_W and visible_width(body) > width:
            # 絕對不砍 [X]：必要時從 email 側再削
            keep = visible_width(suffix)
            body = trunc(body[: max(0, len(body) - len(delete))], max(1, width - keep)) + delete
        links = []
        if acct_id:
            cursor = visible_width(head)
            if multi:
                links.append({
                    "start": cursor,
                    "end": cursor + visible_width(activate),
                    "action": USAGE_ACTIVATE_ACCOUNT_ACTION,
                    "account_id": acct_id,
                })
                cursor += visible_width(activate) + visible_width(btn_sep)
            links.append({
                "start": cursor,
                "end": cursor + visible_width(rebind),
                "action": USAGE_REBIND_CODEX_OAUTH_ACTION,
                "account_id": acct_id,
                "oauth_account": email,
            })
            cursor += visible_width(rebind) + visible_width(btn_sep)
            # 只有 prefix 還留著調序鍵時才掛 hit
            if multi and up in prefix:
                if can_up:
                    links.append({
                        "start": cursor,
                        "end": cursor + visible_width(up),
                        "action": USAGE_MOVE_ACCOUNT_UP_ACTION,
                        "account_id": acct_id,
                    })
                cursor += visible_width(up) + visible_width(btn_sep)
                if can_down:
                    links.append({
                        "start": cursor,
                        "end": cursor + visible_width(down),
                        "action": USAGE_MOVE_ACCOUNT_DOWN_ACTION,
                        "account_id": acct_id,
                    })
            del_start = visible_width(body) - visible_width(delete)
            if del_start >= 0:
                links.append({
                    "start": del_start,
                    "end": del_start + visible_width(delete),
                    "action": USAGE_REMOVE_ACCOUNT_ACTION,
                    "account_id": acct_id,
                    "oauth_account": email,
                    "seq_label": seq_label,
                })
        if is_active:
            if width <= MAX_SANE_PANE_W:
                gap = width - visible_width(body)
                if gap > 0:
                    body += " " * gap
            body = "\x1b[30;103m" + body + RESET
        emit(body, links=links or None)

    # 帳號列與 [+] 之間空一行（與上方用量／圖例節奏一致）
    emit("")
    add_oauth = " [+] OAuth 新增"
    emit(add_oauth, links=[{
        "start": 1, "end": visible_width(add_oauth),
        "action": USAGE_ADD_CODEX_OAUTH_ACTION,
    }])
    if len(oauth_accts) == 1:
        emit(DIM + " 再加一個帳號才有自動接續" + RESET)   # v0.70.0 #10:說明為什麼沒有 🚀/🔀/👆👇

    if token_accts:
        emit("")
        emit(" [Token]")
        token_n = len(token_accts)
        for i, acct in enumerate(token_accts):
            seq_label = str(acct.get("seq_label") or ("%03d" % (i + 1)))
            label = str(acct.get("label") or acct.get("id") or "?")
            live = bool(acct.get("bound")) or str(acct.get("status") or "") == "live" or str(acct.get("status_label") or "") == "可用"
            mark = "✅" if live else "❌"
            can_up = bool(acct.get("can_move_up")) if "can_move_up" in acct else (i > 0)
            can_down = bool(acct.get("can_move_down")) if "can_move_down" in acct else (i + 1 < token_n)
            # ✅ 001 label | ↑ | ↓
            head = " %s %s %s | " % (mark, seq_label, label)
            up = "↑"
            down = "↓"
            sep = " | "
            body = head + up + sep + down
            if width <= MAX_SANE_PANE_W and visible_width(body) > width:
                body = trunc(body, width)
            acct_id = str(acct.get("id") or "")
            links = []
            if acct_id:
                cursor = visible_width(head)
                if can_up:
                    links.append({
                        "start": cursor,
                        "end": cursor + visible_width(up),
                        "action": USAGE_MOVE_ACCOUNT_UP_ACTION,
                        "account_id": acct_id,
                    })
                cursor += visible_width(up) + visible_width(sep)
                if can_down:
                    links.append({
                        "start": cursor,
                        "end": cursor + visible_width(down),
                        "action": USAGE_MOVE_ACCOUNT_DOWN_ACTION,
                        "account_id": acct_id,
                    })
            emit(body, links=links or None)
    add_token = " [+] Token Key"
    emit(add_token, links=[{
        "start": 1, "end": visible_width(add_token),
        "action": USAGE_ADD_CODEX_TOKEN_ACTION,
    }])

    # 最底那列說明留白。按鈕意思不再寫在畫面上。
    if str(panel.get("hint") or "").strip():
        emit("")


def _agent_level(ai):
    """E2.19 層級樹:讀 agent 的顯示層級(0-2),渲染端只認這個資料欄,
    不知艦隊政治(誰掛誰由採集端算)。缺欄/壞值=0——**向後相容鎖**:
    無 level 的既有治具與呼叫端,輸出必須與層級化前逐位元組相同。"""
    try:
        return max(0, min(2, int(ai.get("level") or 0)))
    except (TypeError, ValueError):
        return 0


def emit_dm_queue_rows(items, emit, width, indent, seat):
    """一席的排序暫存列。內文裁到剩餘寬度;資料帶全文,這裡只裁畫面。"""
    if not items or not seat:
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        pri = str(item.get("priority_label") or "一般")
        sgr = str(item.get("priority_sgr") or "")
        when = str(item.get("when") or "")
        sender = str(item.get("sender") or "未知")
        body = " ".join(str(item.get("msg") or "").split())
        col = 0
        parts = []
        links = []

        def add(text, action=None):
            nonlocal col
            parts.append(text)
            w = visible_width(text)
            if action and item_id:
                link = {
                    "start": col, "end": col + w, "action": action,
                    "seat": seat, "item": item_id,
                }
                if str(item.get("rev", "")).isdigit():
                    link["rev"] = int(item["rev"])   # 第 36 項:這一幀的修訂號跟著按鈕走
                links.append(link)
            col += w

        add(indent)
        add((sgr + pri + RESET) if sgr else pri)
        add(" ")
        add(when)
        add(" ")
        add("↑", "dm-queue-up")
        add("↓", "dm-queue-down")
        add(" ")
        add(sender)
        add(" ")
        tail = DMQ_SEND_NOW + DMQ_EDIT + DMQ_DELETE
        remain = width - col - visible_width(tail)
        add(trunc(body, max(1, remain)))
        add(DMQ_SEND_NOW, "dm-queue-send-now")
        add(DMQ_EDIT, "dm-queue-edit")
        add(DMQ_DELETE, "dm-queue-delete")
        line = "".join(parts)
        if visible_width(line) > width:
            line = trunc(line, width)
            links = [lk for lk in links if lk.get("end", 0) <= width]
        emit(line, links=links or None)


PENDING_VISIBLE = 1
PENDING_VISIBLE_CHOICES = (1, 3, 5, 10)
PENDING_THUMB = "█"
PENDING_UP = "▲"
PENDING_DOWN = "▼"
PENDING_TRACK = "┊"  # 09-20：勿用 │，否則看起來像右邊框
PENDING_PAD_L = 1
PENDING_PAD_R = 1
PENDING_INNER = 1
PENDING_TL = "╭"
PENDING_TR = "╮"
PENDING_BL = "╰"
PENDING_BR = "╯"
PENDING_H = "─"
PENDING_V = "│"
EDGE_DOT = "•"  # 開側後正文左緣；寬 1
PENDING_TITLE = "AIB 訊息待審區"
DIALOG_TITLE = "AIB 私訊對話區"
REALTIME_TITLE = "AIB 回應顯示區"
DIALOG_EMPTY = "（私訊對話區尚無內容）"
REALTIME_EMPTY = "（回應顯示區尚無內容）"

PENDING_GLOBAL_TITLE = "AIB 全域訊息區"
PENDING_GLOBAL_BTN = "[全域]"
PENDING_EMPTY = "沒有待審訊息"
FEATURE_ZONE_TITLE = "Ez2 AIB 功能區"
FEATURE_TIER_PLUS = "plus"
FEATURE_TIER_MAX = "max"
FEATURE_TIER_ULTRA = "ultra"
# 檔次字樣接在功能區名後面;光澤掃過整段,含符號。
FEATURE_ZONE_BADGE = {
    FEATURE_TIER_PLUS: " Plus",
    FEATURE_TIER_MAX: " ꧁ ༺ ~~Max~~༻ ꧂",
    # ★ 09-12 發版全套抓到:原本的 Ultra 徽章中段(꧁ ༺ 兩空白 ♡)逐位元含報表分隔線核心(那三個字元帶空白),
    #   讓 render_lib 成為那個字面的第二個執行期來源(test-report-status-enum-arms 的單源臂只准
    #   status-enum.json 有它)。檔次徽章與報表分隔線是兩件事,拿掉中段的 ༺/༻ 讓它不再同形;
    #   test-dm-sort-queue 釘的是 ♡ / ~~Ultra~~ / ꧂ 三段,仍在。
    #   09-14 the maintainer 令 ꧁ 後改兩格:報表核心是「꧁ ༺ 兩空白 ♡」,這裡沒有 ༺,不同形(單源臂的 core 不命中)。
    FEATURE_TIER_ULTRA: " ꧁  ♡ ~~Ultra~~ ♡ ꧂",
}
# 光澤一圈幾秒。渲染器不讀時鐘;畫圈把 0..1 進度傳進來。
# 一圈仍 18 秒;亮帶通過字面比「十八秒勻速走完」快 N 倍,多的時間兩頭休息。
FEATURE_SHINE_PERIOD_S = 18.0
FEATURE_SHINE_SPEED_MUL = 2.5
FEATURE_SHINE_SPEED_BY_TIER = {
    "": FEATURE_SHINE_SPEED_MUL,
    FEATURE_TIER_PLUS: 3.5,
    FEATURE_TIER_MAX: 5.0,
    FEATURE_TIER_ULTRA: 4.5,   # 09-14 the maintainer:紫光再慢(7.0 → 5.5 → 4.5)
}
FEATURE_SHINE_DIM = "\x1b[38;5;250m"
FEATURE_SHINE_SOFT = "\x1b[38;5;252m"
FEATURE_SHINE_LIFT = "\x1b[38;5;253m"
FEATURE_SHINE_MID = "\x1b[38;5;223m"
FEATURE_SHINE_WARM = "\x1b[38;5;229m"
FEATURE_SHINE_HIGH = "\x1b[38;5;230m"
FEATURE_SHINE_PEAK = "\x1b[1m\x1b[38;5;231m"
FEATURE_SHINE_RAMP = (
    FEATURE_SHINE_DIM,
    FEATURE_SHINE_SOFT,
    FEATURE_SHINE_LIFT,
    FEATURE_SHINE_MID,
    FEATURE_SHINE_WARM,
    FEATURE_SHINE_HIGH,
    FEATURE_SHINE_PEAK,
)
# Max 檔次:亮帶走強化橙,其餘檔次仍用上面奶油白。
FEATURE_SHINE_ORANGE_LIFT = "\x1b[38;5;215m"
FEATURE_SHINE_ORANGE_MID = "\x1b[38;5;208m"
FEATURE_SHINE_ORANGE_WARM = "\x1b[38;5;214m"
FEATURE_SHINE_ORANGE_HIGH = "\x1b[38;5;220m"
FEATURE_SHINE_ORANGE_PEAK = "\x1b[1m\x1b[38;5;222m"
FEATURE_SHINE_ORANGE_RAMP = (
    FEATURE_SHINE_DIM,
    FEATURE_SHINE_ORANGE_LIFT,
    FEATURE_SHINE_ORANGE_MID,
    FEATURE_SHINE_ORANGE_WARM,
    FEATURE_SHINE_ORANGE_HIGH,
    FEATURE_SHINE_ORANGE_PEAK,
)
# Ultra 檔次:紫光。
FEATURE_SHINE_PURPLE_LIFT = "\x1b[38;5;147m"
FEATURE_SHINE_PURPLE_MID = "\x1b[38;5;141m"
FEATURE_SHINE_PURPLE_WARM = "\x1b[38;5;135m"
FEATURE_SHINE_PURPLE_HIGH = "\x1b[38;5;183m"
FEATURE_SHINE_PURPLE_PEAK = "\x1b[1m\x1b[38;5;225m"
FEATURE_SHINE_PURPLE_RAMP = (
    FEATURE_SHINE_DIM,
    FEATURE_SHINE_PURPLE_LIFT,
    FEATURE_SHINE_PURPLE_MID,
    FEATURE_SHINE_PURPLE_WARM,
    FEATURE_SHINE_PURPLE_HIGH,
    FEATURE_SHINE_PURPLE_PEAK,
)
FEATURE_SHINE_RAMP_BY_TIER = {
    "": FEATURE_SHINE_RAMP,
    FEATURE_TIER_PLUS: FEATURE_SHINE_RAMP,
    FEATURE_TIER_MAX: FEATURE_SHINE_ORANGE_RAMP,
    FEATURE_TIER_ULTRA: FEATURE_SHINE_PURPLE_RAMP,
}
FEATURE_TAB_PENDING = PENDING_TITLE
FEATURE_TAB_TASKS = "AI任務區"
FEATURE_TABS = (FEATURE_TAB_PENDING, FEATURE_TAB_TASKS)
FEATURE_TASKS_EMPTY = "沒有進行中的任務"
FEATURE_TASKS_HEAD = "任務"
FEATURE_TASKS_MORE = "還有 %d 件未顯示"

# ---- 第 35 項(10c)高度預算 —— §89.7.1 接口四 `h` 的唯一實作 ----------------
# ★ 三個常數只准有這一份字面。pane-pending-review.sh 與 mouse-ctl.sh 都從這裡問,
#   不自己再寫一次 8(tests/test-feature-zone-interface-34.py A7 守著;
#   tests/test-pending-height-10c.py 守「兩支殼都沒有自己的 8」)。
CONV_MIN = 8          # 中央對話至少留幾列
CHROME = 3            # 功能區框自己的列:功能區名 1 + 圓角抬頭 1 + 下框 1
GLOBAL_CHROME = 2     # 全域下框沒有功能區名那一列:圓角抬頭 1 + 下框 1
EDIT_MIN = 6          # 編輯框至少幾列(pending_edit.py 自己 max(4, h),6 是可用的下限)
EDIT_PREF = 12        # 編輯框想要幾列(原 mouse-ctl 的 12,搬來這裡成唯一一份)
# 夾不住時給人看的那一句(接口四規則 2:具名、帶兩個數,不得無聲消失)。
HEIGHT_TOO_SHORT = "視窗太矮,功能區要 %d 列、現在只有 %d 列"


def feature_tasks_lines_wanted(tasks):
    """任務頁想畫幾列內容(不含 CHROME)。接口四規則 4:一件畫幾列由畫的人回報。

    與 render_feature_tasks_box 的畫法逐格同源:標題列 1;沒有進行中 → 空句 1;
    有 → 「進行中:」1 + 每件 1 列(帶 title 再 +1)。
    """
    rows = [t for t in (tasks or []) if isinstance(t, dict)]
    terminal = {"done", "cancelled", "closed", "abandoned"}
    running = [t for t in rows if str(t.get("status") or "") not in terminal]
    if not running:
        return 2
    n = 2
    for task in running:
        n += 2 if str(task.get("title") or "") else 1
    return n


def feature_height_budget(wh, editor_open=False, feature_tab=0, global_open=False,
                          want_pending=PENDING_VISIBLE, want_global=0,
                          want_tasks=0, edit_pref=EDIT_PREF):
    """把中央那一欄的列數分給:編輯框 / 中央對話 / 待審框 / 全域下框 / 任務頁。

    純函式:不讀時鐘、不讀環境、不碰 tmux。`wh` 由呼叫端量(pending_height.column_height)。

    保留優先(艦長 a seat 09-12 02:43 裁,取代 §89.7 原句):
        編輯框 > 中央對話 ≥ CONV_MIN > 待審列表 > 全域框 > 任務區
    也就是空間不夠時先縮任務區、再縮全域、再縮待審列表;編輯框與中央 8 列不縮,
    連編輯框都放不下 ⇒ editor=0(呼叫端據此**暫拒開編輯**,不是開一個更矮的)。

    單位一律是**列**(接口四規則 4);每一格回的是那一區「整框含框線」的列數:
      editor        編輯框列數(0 = 放不下 / 沒開)
      pending       待審框列數(含 CHROME);pending_rows = 內容列
      global        全域下框列數(含 GLOBAL_CHROME);global_rows = 內容列
      tasks         任務頁列數(含 CHROME)
      middle        中央對話拿到的列數(= wh - editor - 功能區總列)
      total         功能區總列(pending+global 或 tasks)
      fit           功能區畫不畫得出來(False ⇒ 呼叫端印 HEIGHT_TOO_SHORT)
      need          畫得出一列內容至少要幾列(給那句訊息用)
      short         [(區, 想要, 拿到)] —— 誰被縮了;空 = 沒夾。呼叫端據此留痕(規則 5)。

    不變式(tests/test-pending-height-10c.py 逐條釘):
      每格 ≥ 0;editor + middle + total == wh;
      fit ⇒ middle ≥ CONV_MIN;editor 只會是 0 或 [EDIT_MIN, edit_pref]。
    """
    try:
        wh = max(0, int(wh))
    except (TypeError, ValueError):
        wh = 0
    try:
        feature_tab = 1 if int(feature_tab) == 1 else 0
    except (TypeError, ValueError):
        feature_tab = 0
    edit_pref = max(EDIT_MIN, int(edit_pref or EDIT_PREF))
    short = []
    editor = 0
    if editor_open:
        room = wh - CONV_MIN
        editor = min(edit_pref, room) if room >= EDIT_MIN else 0
        if editor < edit_pref:
            short.append(("editor", edit_pref, editor))
    avail = max(0, wh - CONV_MIN - editor)
    pending = glob = tasks = 0
    if feature_tab == 1:
        want = CHROME + max(1, int(want_tasks or 0))
        if avail >= CHROME + 1:
            tasks = min(want, avail)
        if tasks < want:
            short.append(("tasks", want, tasks))
    else:
        want = CHROME + max(1, int(want_pending or 0))
        if avail >= CHROME + 1:
            pending = min(want, avail)
        if pending < want:
            short.append(("pending", want, pending))
        gw = int(want_global or 0)
        if global_open and gw > 0:
            gwant = GLOBAL_CHROME + gw
            rest = avail - pending
            if rest >= GLOBAL_CHROME + 1:
                glob = min(gwant, rest)
            if glob < gwant:
                short.append(("global", gwant, glob))
    total = pending + glob + tasks
    need = CONV_MIN + editor + CHROME + 1
    return {
        "wh": wh,
        "editor": editor,
        "middle": wh - editor - total,
        "pending": pending,
        "pending_rows": max(0, pending - CHROME),
        "global": glob,
        "global_rows": max(0, glob - GLOBAL_CHROME),
        "tasks": tasks,
        "total": total,
        "fit": total > 0,
        "need": need,
        "short": short,
    }
PENDING_COUNT_MARK = "待審"
PENDING_TEST_MARK = "測"
PENDING_DELIVERED_DIVIDER = "── ⬆⬆⬆ 已私訊但AI未讀 ── ⬇⬇⬇ 下方AI已讀/未進行 ──"   # 待審頁上下段分隔列(the maintainer 09-15 02:4x 定字;上=排隊中還沒貼進對方對話,下=已貼進對方對話;不算則數、不能點)
PENDING_NO_TEST_MARK = "- "   # 非測試信的佔位:與「測」同為 2 欄寬
# ★ 重送迴圈修法(艦長 09-12 14:06 補:承重格不是附帶):投遞回 3/4 的信轉 hold_human 之後,失效方向從
#   「吵(每幀重貼)」變成「靜(停在佇列等人)」。所以待審框抬頭要畫「待人處理 N・最早 HH:MM」,
#   有 1 則就必含那個數字、0 則不畫;尺釘在 test-dm-sort-queue.py。狀態字面與 dm_queue.HOLD_STATUS 同一個字,
#   那支尺也釘兩邊相等(渲染器不 import dm_queue)。
PENDING_HOLD_STATUS = "hold_human"
# ★ the maintainer 09-30 15:3x(收件匣 A 項 4):「待人處理·rcN·時刻」改成「人審」—— rc 碼真人看不懂,
#   時刻還是錯的(列上拿不到 held_at 就退回建立時刻的尾五字 = 分:秒)。原因與停住的時刻改放在
#   點選那一則時展開的說明行(_inbox_hold_note),列上與抬頭只留兩個字。
PENDING_HOLD_LABEL = "人審"
# 與 dm_queue.PRIORITY_SGR 同色。渲染器不 import 那支(零 I/O);對齊由測試守。
PENDING_PRI_SGR = {
    "urgent": "\x1b[38;5;196m",
    "important": "\x1b[38;5;208m",
}


# ── 第 70 項 P4:待審區五 tab、角標、真人列、未讀常駐(接口 IFACE-dm-queue-v2 §4;OPT §五 §七 7.6)──
# 渲染器仍是純函式:tab 的列全部由 dm_queue.tabs_payload 排好給進來,這裡不排序、不讀檔、不讀 i18n。
# tab 鍵與順序必須與 dm_queue.TABS 逐字相等(尺對帳;渲染器不 import dm_queue)。
INBOX_TABS = ("waiting", "read", "done", "archived", "deleted")
INBOX_TAB_LABEL = {"waiting": "待審", "read": "進行中", "done": "完成", "archived": "備存", "deleted": "刪除"}   # read 頁顯示名 the maintainer 09-15 02:4x 改「進行中」(狀態碼仍是 read)
INBOX_TAB_SHORT = {"waiting": "待", "read": "進", "done": "完", "archived": "存", "deleted": "刪"}
INBOX_UPDATE_MARK = "[更新]"        # 這一則取代了同主題的舊版(is_update);排最上由佇列端排
INBOX_DECIDE_MARK = "[要裁]"        # kind=decide
INBOX_EXPIRED_MARK = "[過期]"       # expires 已到(標記,不攔截:第 40 項)
INBOX_TRIAGE_TAG_MAX = 12           # 歸納標籤最寬 12 欄([更99·最新]=11);the maintainer 09-30 17:2x,字面由 dm_triage 定
INBOX_GLOBAL_MARK = "[外部]"                 # 全球來信、寄件人已驗證(名冊對得上)
INBOX_GLOBAL_STRANGER_MARK = "[外部·未驗證]"  # 全球來信、陌生或未驗證:放行前看清楚(維護者 10-16)
INBOX_HOLD_MARK = "[人審]"   # the maintainer 09-30 15:3x:列上只留兩個字;原因+停住時刻在點選後的說明行(_inbox_hold_note)
# 點選一則人審信時展開的原因(白話,the maintainer 09-30 15:3x)。鍵 = hold_rc;不在表上的碼走 hold_why 關鍵字,再不行就「原因不明」。
INBOX_HOLD_REASON = {
    3: "已送出但沒確認對方收到",
    4: "字進了框但沒送出或判斷不出",
    1: "內容被送信檢查擋下,重送也不會過",
}
INBOX_HOLD_REASON_DEAD = "對方的窗已經關了"
INBOX_HOLD_REASON_ATTEMPTS = "重送三次都失敗"
INBOX_HOLD_REASON_EXPIRED = "超過期限"
INBOX_HOLD_REASON_UNKNOWN = "原因不明,先看內容再決定"
INBOX_HOLD_NOTE_FMT = "人審原因:%s"
INBOX_HOLD_NOTE_AT_FMT = "人審原因:%s(%s 停住)"
# review r14 M1(畫面):說明行擠不出自己一行時改放進列上(緊接 [人審] 角標):停住時刻在前,窄寬先截原因尾巴
INBOX_HOLD_INLINE_AT_FMT = "%s 停住·%s"
INBOX_HOLD_INLINE_AT_ONLY_FMT = "%s 停住"
INBOX_UNREAD_FMT_SHORT = "[待審%d]"   # 抬頭角標字面 the maintainer 09-15 02:5x 改「待審」(數值仍是 badges.unread:待審頁扣掉待人處理與過期)
INBOX_ERROR_PREFIX = "佇列讀不到:"  # errors 非空 ⇒ 框內第一列印它,不得當成空(病 3)
INBOX_TOMB_FMT = "刪除·%s·%s·%s"     # deleted tab:誰・何時・原因(墓碑三欄)
# 真人列底色:面板背景 235,深一階 237(the maintainer 09-12 14:3x 裁:灰階、字照常、不加 emoji 與框線)。
HUMAN_ROW_BG = "\x1b[48;5;237m"
HUMAN_ROW_BG_RESET = "\x1b[49m"


def _hhmm_of(epoch):
    """epoch → 台北 HH:MM;量不到回空字串(不印 00:00)。純函式:時區用固定 +8,不讀本機時區。"""
    try:
        t = int(epoch)
    except (TypeError, ValueError):
        return ""
    if t <= 0:
        return ""
    t += 8 * 3600
    return "%02d:%02d" % ((t // 3600) % 24, (t // 60) % 60)


def inbox_tab_index(value):
    """state/pending-inbox-tab 的值 → 0..4;認不得一律 0(待審)。"""
    try:
        i = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return i if 0 <= i < len(INBOX_TABS) else 0


def inbox_tab_key(index):
    """0..4 → tab 鍵名(waiting/read/…);認不得回 waiting。"""
    return INBOX_TABS[inbox_tab_index(index)]


def inbox_tabs_tokens(inbox, selected, short=False):
    """五個 tab 的字面(不帶色):[(key, text)]。待審那格帶筆數與最早時刻,其餘只帶筆數;0 則不印數。"""
    tabs = (inbox or {}).get("tabs") or {}
    out = []
    for key in INBOX_TABS:
        rec = tabs.get(key) or {}
        try:
            n = int(rec.get("n") or 0)
        except (TypeError, ValueError):
            n = 0
        label = INBOX_TAB_SHORT[key] if short else INBOX_TAB_LABEL[key]
        # 09-15 the maintainer:tab 上只要數字。最舊時刻已在抬頭的 [未讀N·HH:MM] 角標,不在 tab 列重複。
        text = label + (str(n) if n else "")
        out.append((key, text))
    return out


def _shine_segments(segments, phase, speed_mul=None, ramp=None):
    """_shine_title 的多段版:同一道光依可見欄位連續掃過每一段(數學同 _shine_title)。
    segments=[(text, base)];base=None ⇒ 沒照到時用 ramp[0](同 _shine_title);其餘 base 是沒照到時的原樣式("" = 不上色)。"""
    ramp = ramp or FEATURE_SHINE_RAMP
    try:
        phase = float(phase)
    except (TypeError, ValueError):
        phase = None
    if phase is not None and phase != phase:  # NaN
        phase = None
    marks = []
    pos = 0.0
    for text, base in segments:
        for ch in text:
            w = visible_width(ch)
            if w < 0:
                w = 0
            marks.append((ch, pos + (w / 2.0), base))
            pos += w
    total = pos
    radius = 5.2
    center = None
    if phase is not None and total > 0:
        span = total + radius * 3.2
        mul = FEATURE_SHINE_SPEED_MUL
        if speed_mul is not None:
            try:
                mul = float(speed_mul)
            except (TypeError, ValueError):
                mul = FEATURE_SHINE_SPEED_MUL
        if mul < 1.0:
            mul = 1.0
        travel = span * mul
        pad = (travel - span) / 2.0
        center = (phase % 1.0) * travel - radius * 1.5 - pad
    n = len(ramp)
    parts = []
    last = None
    for ch, mid, base in marks:
        idx = 0
        if center is not None:
            dist = mid - center
            if dist < 0:
                dist = -dist
            if dist < radius:
                x = 1.0 - dist / radius
                t = x * x * (3.0 - 2.0 * x)
                idx = int(t * (n - 1) + 0.5)
                if idx < 0:
                    idx = 0
                elif idx >= n:
                    idx = n - 1
        sgr = ramp[idx] if (idx > 0 or base is None) else base
        if sgr != last:
            parts.append(RESET + sgr)   # 亮帶最亮一級帶粗體:換色前先 RESET,粗體才不會拖到後面的字
            last = sgr
        parts.append(ch)
    parts.append(RESET)
    return "".join(parts)


def _shine_tabs_line(title_w, tabs_col, toks, selected, shine, feature_tier=None):
    """閃光迴圈用的線 0:光從功能區名一路掃過五個 tab(同一條軌跡,the maintainer 09-14 要「閃光延伸到 tab」)。
    沒被照到的字保留原樣 —— 選中的 tab 黃粗、其餘灰;只換顏色不換字,欄位與命中區和靜態那一幀逐格相同。"""
    tier = feature_tier_of(feature_tier)
    title = feature_zone_label(tier)
    indent = max(0, title_w - visible_width(title))
    segs = [(" " * indent, ""), (title, None), (" " * max(0, tabs_col - title_w), "")]
    for i, (_key, text) in enumerate(toks):
        if i:
            segs.append(("  ", ""))
        segs.append((text, (YELLOW + BOLD) if i == selected else GREY))
    mul = FEATURE_SHINE_SPEED_BY_TIER.get(tier, FEATURE_SHINE_SPEED_MUL)
    ramp = FEATURE_SHINE_RAMP_BY_TIER.get(tier, FEATURE_SHINE_RAMP)
    return _shine_segments(segs, shine, speed_mul=mul, ramp=ramp)


def _inbox_tabs_line(width, seat, inbox, selected, shine=None, feature_tier=None, tabs_shine=False):
    """功能區名那一行(線 0)右半畫五 tab;選中的用黃粗、其餘灰;每個 tab 一個命中區 action=inbox-tab。
    放這一行的理由:抬頭那行在寬 66 已經滿了,而這一行右邊 40 多格一直是空的 —— 高度預算(第 35 項)一格不動。
    放不下長形就用短形;短形也放不下就只畫功能區名(不截半個 tab)。"""
    base = _pending_banner(width, shine=shine, feature_tier=feature_tier)
    title_w = visible_width(base.rstrip())
    links = []
    for short in (False, True):
        toks = inbox_tabs_tokens(inbox, selected, short=short)
        plain = "  ".join(t for _, t in toks)
        # 右緣對齊下方抬頭列的身分字尾(@碼最後一個字),不是整列寬:抬頭那一行在身分後面固定還有
        # " ─╮" 與右留白(09-14 各寬實測恆 4 格),貼整列寬會讓 [刪除] 比 @碼多凸出去。
        right = width - PENDING_PAD_R - visible_width(" " + PENDING_H + PENDING_TR)
        need = title_w + 2 + visible_width(plain)
        if need > right:
            continue
        col = right - visible_width(plain)
        tabs_col = col
        parts = [base.rstrip(), " " * (col - title_w)]
        for i, (key, text) in enumerate(toks):
            if i:
                parts.append("  ")
                col += 2
            w = visible_width(text)
            if seat:
                links.append({"start": col, "end": col + w, "action": "inbox-tab",
                              "seat": seat, "tab": i})
            parts.append(_pending_hot(text) if i == selected else GREY + text + RESET)
            col += w
        line = "".join(parts)
        if tabs_shine and shine is not None:
            # 只有 20ms 閃光迴圈開這個;整幀重畫仍畫靜態 tab(畫面快照與測試不隨時鐘變)
            line = _shine_tabs_line(title_w, tabs_col, toks, selected, shine, feature_tier=feature_tier)
        if visible_width(line) > width:
            line = trunc(line, width)
        return line, links
    return base, links


def _inbox_row_marks(item):
    """列上的角標(不帶色),順序固定:要裁 → 更新 → 待人處理 → 過期 → 歸納標籤。沒有就空清單。

    ★ the maintainer 09-30 17:2x 歸納標籤([重複×N] / [重複] / [通知] / [更NN] / [更NN·最新]):
      dm_queue.tabs_payload 讀 triage.json 放進 row["triage_tag"],這裡只照抄(純函式,不讀檔)。
      只收 `[...]` 形狀、最多 INBOX_TRIAGE_TAG_MAX 欄,別的值一律不畫(寧可少一個標籤,不讓怪字撐爆列寬)。"""
    marks = []
    g = item.get("global")
    if isinstance(g, dict):
        # 全球來信(10-16 維護者:放行鈕照留、加醒目提醒):外部席一律標,陌生 / 未驗證放最前面
        marks.append(INBOX_GLOBAL_MARK if g.get("verified") else INBOX_GLOBAL_STRANGER_MARK)
    if str(item.get("kind") or "") == "decide":
        marks.append(INBOX_DECIDE_MARK)
    if item.get("is_update"):
        marks.append(INBOX_UPDATE_MARK)
    if str(item.get("status") or "") == PENDING_HOLD_STATUS:
        marks.append(INBOX_HOLD_MARK)
    if item.get("expired_now"):
        marks.append(INBOX_EXPIRED_MARK)
    tri = item.get("triage_tag")
    if (isinstance(tri, str) and tri.startswith("[") and tri.endswith("]")
            and visible_width(tri) <= INBOX_TRIAGE_TAG_MAX):
        marks.append(tri)
    return marks


def _hold_note_inline(meta_head, meta_tail, item, one_line, width):
    """人審原因擠不出自己的一行時,放進信的那一列 ⇒ 整段 left(width = 這一列左半可用寬)。

    排法:編號/優先/角標(meta_head,含 [人審])+ 灰字「HH:MM 停住·<原因>」+「 · 」+ 時間寄件人(meta_tail)+ 正文。
    ★ 原因排在時間寄件人之前:100 欄寬、右邊四個鈕 32 欄時,接在寄件人後面只剩十來欄,
      原因與停住時刻都會被截成「人審原因:已…」(review r15 實量)。截的時候先被截掉的是正文與寄件人。"""
    reason, at = _inbox_hold_parts(item)
    note = (INBOX_HOLD_INLINE_AT_FMT % (at, reason)) if at else reason
    room = max(1, width - visible_width(meta_head))
    # ★ review r15 M2(畫面):窄的時候(≡1、寬 60 左右,右邊四鈕 32 欄)整句放不下 ⇒ 至少留「HH:MM 停住」,
    #   不要截成「2…」這種碎片;連它都放不下才照截。
    if at and visible_width(note) > room:
        note = INBOX_HOLD_INLINE_AT_ONLY_FMT % at
    return meta_head + GREY + trunc(note + " · " + meta_tail + one_line, room) + RESET


def _inbox_hold_parts(item):
    """人審信的(白話原因, 停住 HH:MM 或空字串);不是人審信回 ("", "")。判讀順序見 _inbox_hold_note。"""
    if str(item.get("status") or "") != PENDING_HOLD_STATUS:
        return "", ""
    why = str(item.get("hold_why") or "")
    rc = item.get("hold_rc")
    try:
        rc = int(rc) if rc is not None else None
    except (TypeError, ValueError):
        rc = None
    if "pane_dead" in why:
        reason = INBOX_HOLD_REASON_DEAD
    elif why.startswith("已到期"):
        reason = INBOX_HOLD_REASON_EXPIRED
    elif "已重送" in why or rc == 2:
        reason = INBOX_HOLD_REASON_ATTEMPTS
    else:
        reason = INBOX_HOLD_REASON.get(rc, INBOX_HOLD_REASON_UNKNOWN)
    return reason, _hhmm_of(item.get("held_at"))


def _inbox_hold_note(item):
    """點選一則人審信時展開的那一行(不帶色):`人審原因:<白話>(HH:MM 停住)`;不是人審信回空字串。

    ★ 判讀順序:窗死 / 到期 / 重送滿次這三種 hold_why 自帶的理由優先(它們的 hold_rc 是空的或是 2),
      其次才看 hold_rc(3 / 4 / 1)。停住時刻用 held_at(dm_queue._row 09-30 起帶這一欄),
      量不到就不印時刻 —— 不退回建立時刻(那正是舊標籤印錯的原因)。"""
    reason, at = _inbox_hold_parts(item)
    if not reason:
        return ""
    return (INBOX_HOLD_NOTE_AT_FMT % (reason, at)) if at else (INBOX_HOLD_NOTE_FMT % reason)


def _inbox_unread_tok(inbox, budget=None):
    """抬頭常駐的未讀角標;0 則不畫;預算不夠退短形、再不夠不畫(不截半個)。"""
    badges = (inbox or {}).get("badges") or {}
    try:
        n = int(badges.get("unread") or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return ""
    # 09-15 the maintainer:角標只要數字([待審N]),不帶最早時刻;放不下就不畫,不截半個
    form = INBOX_UNREAD_FMT_SHORT % n
    if budget is None or visible_width(form) + 1 <= budget:
        return _pending_hot(form)
    return ""


# ── 第 70 項 P6 前半:系統通知列(OPT §一病 4、§二 5、§五 8)──────────────────────────────
# cooldown 等系統通知不進 dm 佇列,落 state/system-notices.jsonl;待審框**頂一行**讀它:不占待審列、不算進
# 待審 N、沒有動作鈕。只畫最新一則且未過期的(TTL 由呼叫端決定,預設 30 分);畫面印父行程與參數,
# 分不出的印「量不到」——通知本來就是「讓人看得到有事」,不是「叫人做決定」。
NOTICE_MARK = "⚙"
NOTICE_TTL_S = 1800
NOTICE_UNMEASURED = "量不到是不是治具"


def pick_notice(rows, now, ttl_s=NOTICE_TTL_S):
    """純函式:從 system-notices 列(dict 清單)挑最新且未過期的一則;沒有回 None。壞列跳過。"""
    best = None
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        try:
            ts = int(r.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts <= 0 or ts > int(now) + 60 or int(now) - ts > int(ttl_s):
            continue
        if best is None or ts >= int(best.get("ts") or 0):
            best = r
    return best


def notice_text(notice):
    """一則通知 → 一行純文字(不帶色):`⚙ HH:MM 資源狀態:<need> — <誰在跑(父行程 參數)>`。"""
    if not isinstance(notice, dict):
        return ""
    when = _hhmm_of(notice.get("ts")) or "--:--"
    need = [str(x) for x in (notice.get("need") or []) if str(x).strip()]
    head = "%s %s %s:%s" % (NOTICE_MARK, when, "資源狀態" if notice.get("source") == "cooldown" else str(notice.get("source") or "系統"),
                            ";".join(need) if need else "(沒有需要決定的事)")
    culprits = notice.get("culprits")
    if culprits is None:
        tail = NOTICE_UNMEASURED
    elif not culprits:
        tail = ""
    else:
        bits = []
        for c in culprits[:3]:
            if not isinstance(c, dict):
                continue
            parent = " ".join(str(c.get("parent_command") or "").split())
            toks = str(c.get("command") or "").split()
            # 只印**參數**(--check、假版號一看就分得出):直譯器與腳本名跟 name 重複,寬 100 放不下兩份
            opt = [t for t in toks if t.startswith("-")]
            if opt:
                args = " ".join(toks[toks.index(opt[0]):])
            else:
                args = toks[-1].rsplit("/", 1)[-1] if toks else ""   # 不 import os:渲染器零 I/O(golden-frames 釘著)
            ptoks = parent.split()
            parent_short = " ".join(ptoks[:3]) if ptoks else ""
            bits.append("%s pid%s %s(父:%s)" % (c.get("name") or "?", c.get("pid") or "?",
                                                 args[:40] or "無參數", parent_short[:20] or "量不到"))
        if len(culprits) > 3:
            bits.append("…共 %d 個" % len(culprits))
        tail = "、".join(bits)
    return head + (" — " + tail if tail else "")


def _pending_priority_key(item):
    raw = ""
    if isinstance(item, dict):
        raw = str(item.get("priority") or item.get("priority_label") or "")
    key = raw.strip().lower()
    if key in ("urgent", "緊急"):
        return "urgent"
    if key in ("important", "重要"):
        return "important"
    return "other"


def pending_count_tag(items, box_open):
    """右欄:【待審N】;有緊急/重要才接 /緊急n /重要n。關著或沒信=空字。"""
    if not box_open:
        return ""
    if not isinstance(items, list):
        return ""
    n = urgent = important = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        n += 1
        kind = _pending_priority_key(it)
        if kind == "urgent":
            urgent += 1
        elif kind == "important":
            important += 1
    if n <= 0:
        return ""
    chunks = [YELLOW, "【", PENDING_COUNT_MARK, str(n)]
    if urgent:
        chunks.extend(["/", PENDING_PRI_SGR["urgent"], "緊急%d" % urgent, YELLOW])
    if important:
        chunks.extend(["/", PENDING_PRI_SGR["important"], "重要%d" % important,
                       YELLOW])
    chunks.extend(["】", RESET])
    return "".join(chunks)


def _pending_frame(ch):
    return GREY + ch + RESET


def _pending_outline(ch, borderless=False):
    """圓角框線；borderless 時改同寬空白（保點擊欄位對齊）。"""
    if borderless:
        return " " * visible_width(ch)
    return _pending_frame(ch)


def _pending_hot(text):
    """待審框可點記號:與 [≡N] 同一套黃加粗。"""
    return YELLOW + BOLD + text + RESET


def _pending_wrap(text, width):
    """依可見欄寬折行。換行當硬斷,其餘依字寬。空輸入回 [""]。"""
    width = max(1, int(width or 1))
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if raw == "":
        return [""]
    out = []
    for para in raw.split("\n"):
        if para == "":
            out.append("")
            continue
        buf = ""
        for ch in para:
            trial = buf + ch
            if visible_width(trial) <= width:
                buf = trial
                continue
            if buf:
                sp = buf.rfind(" ")
                if sp > 0:
                    out.append(buf[:sp])
                    buf = buf[sp + 1:] + ch
                    continue
                out.append(buf)
            if visible_width(ch) > width:
                out.append(trunc(ch, width))
                buf = ""
            else:
                buf = ch
        if buf:
            out.append(buf)
    return out or [""]


def pending_visible_of(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return PENDING_VISIBLE
    if n in PENDING_VISIBLE_CHOICES:
        return n
    return PENDING_VISIBLE


def pending_visible_next(value):
    cur = pending_visible_of(value)
    i = PENDING_VISIBLE_CHOICES.index(cur)
    return PENDING_VISIBLE_CHOICES[(i + 1) % len(PENDING_VISIBLE_CHOICES)]


def pending_gutter_glyphs(n, offset, focus, visible=PENDING_VISIBLE):
    """三格軌道。█ 取代同格的 ▲/▼。起點 █│▼,終點 ▲█│。"""
    n = int(n or 0)
    vis = max(1, int(visible))
    if n <= vis:
        return [""] * vis
    offset = max(0, min(int(offset), max(0, n - vis)))
    focus = max(0, min(int(focus), max(0, n - 1)))
    more_above = offset > 0
    more_below = offset + vis < n
    thumb_slot = focus - offset
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


PENDING_HOLD_SHORT = "審"      # 抬頭擠不下時的最短形「[審N]」:筆數一定要在,承重格不因框窄消失


def _pending_hold_forms(n, oldest=""):
    """由長到短的兩種字面(不帶色):[人審N] / [審N]。0 則回空清單。

    ★ the maintainer 09-30 15:3x(收件匣 A 項 4):抬頭不再印「最早 HH:MM」(那是建立時刻不是停住時刻,真人看了會誤判);
      停住時刻改在點選那一則時的說明行。oldest 參數留著只為相容呼叫端,不再使用。"""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return []
    forms = []
    forms.append("[%s%d]" % (PENDING_HOLD_LABEL, n))
    forms.append("[%s%d]" % (PENDING_HOLD_SHORT, n))
    return forms


def _pending_hold_tok(n, oldest="", budget=None):
    """人審 N。0 則回空字串(不畫)。

    ★ 抬頭寬度預算(開發 A 09-12 14:2x 審 fc19c424 退回的理由):抬頭只會縮席碼、不會縮別的 token,
      全形 22 格一放進去,寬 ≤72 的框連 [≡N] 按鈕、席碼、右上角 ╮ 都被截掉(本艙右欄 66 寬實量)。
      budget=None 沿用全形(相容);給了預算就挑放得下的最長一形,都放不下仍畫最短形「[審N]」——
      筆數是承重格,寧可擠掉席碼也不能沒有它。"""
    forms = _pending_hold_forms(n, oldest)
    if not forms:
        return ""
    pick = forms[0]
    if budget is not None:
        pick = forms[-1]
        for f in forms:
            if visible_width(" %s " % f) <= budget:
                pick = f
                break
    return PENDING_PRI_SGR["urgent"] + BOLD + " %s " % pick + RESET


def _pending_hold_summary(rows):
    """(held_count, oldest_hhmm):從資料層的 status 欄算,渲染器不讀佇列。"""
    held = [r for r in (rows or []) if isinstance(r, dict) and r.get("status") == PENDING_HOLD_STATUS]
    if not held:
        return 0, ""
    def _ts(r):
        try:
            return int(r.get("ts") or 0)
        except (TypeError, ValueError):
            return 0
    oldest = min(held, key=_ts)
    when = str(oldest.get("when") or "")
    # 只認尾巴「HH:MM:SS」的形;不合形就不印「最早」那半(印怪片段比不印更糟)
    hhmm = when[-8:-3] if re.search(r"\d\d:\d\d:\d\d$", when) else ""
    return len(held), hhmm


def _pending_new_tok(n):
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return ""
    # 新到提示用緊急同色,不要跟黃按鈕混成同一套。
    return (PENDING_PRI_SGR["urgent"] + BOLD + " [+%d新] " % n + RESET)


def feature_tier_of(value):
    """plus/max/ultra;既有進階授權當 plus。其餘未註冊。不讀授權檔。"""
    if value is None:
        return ""
    key = str(value).strip().lower()
    if key in FEATURE_ZONE_BADGE:
        return key
    if key == "advanced":
        return FEATURE_TIER_PLUS
    return ""


def feature_zone_label(feature_tier=None):
    """功能區名 + 該檔次符號字樣。"""
    tier = feature_tier_of(feature_tier)
    return FEATURE_ZONE_TITLE + FEATURE_ZONE_BADGE.get(tier, "")


def _shine_title(text, phase, speed_mul=None, ramp=None):
    """由左而右的緩光。phase 在 [0,1);一圈走完再從頭。不讀時鐘。"""
    try:
        phase = float(phase)
    except (TypeError, ValueError):
        return BOLD + text + RESET
    if phase != phase:  # NaN
        return BOLD + text + RESET
    phase = phase % 1.0
    marks = []
    pos = 0.0
    for ch in text:
        w = visible_width(ch)
        if w < 0:
            w = 0
        marks.append((ch, pos + (w / 2.0)))
        pos += w
    total = pos
    if total <= 0:
        return BOLD + text + RESET
    radius = 5.2
    span = total + radius * 3.2
    mul = FEATURE_SHINE_SPEED_MUL
    if speed_mul is not None:
        try:
            mul = float(speed_mul)
        except (TypeError, ValueError):
            mul = FEATURE_SHINE_SPEED_MUL
    if mul < 1.0:
        mul = 1.0
    travel = span * mul
    pad = (travel - span) / 2.0
    center = phase * travel - radius * 1.5 - pad
    if not ramp:
        ramp = FEATURE_SHINE_RAMP
    n = len(ramp)
    parts = []
    last = None
    for ch, mid in marks:
        dist = mid - center
        if dist < 0:
            dist = -dist
        t = 0.0
        if dist < radius:
            x = 1.0 - dist / radius
            t = x * x * (3.0 - 2.0 * x)
        idx = int(t * (n - 1) + 0.5)
        if idx < 0:
            idx = 0
        elif idx >= n:
            idx = n - 1
        sgr = ramp[idx]
        if sgr != last:
            parts.append(sgr)
            last = sgr
        parts.append(ch)
    parts.append(RESET)
    return "".join(parts)



# 左緣 • 呼吸：獨立週期／色階，禁止改 FEATURE_SHINE_*（Plus 功能區光澤）。
# 讀 FEATURE_SHINE_DIM 等字面當色票 OK；週期與相位加速不得綁 FEATURE_SHINE_PERIOD_S。
EDGE_SHINE_PERIOD_S = 3.0
# GPT 風格：灰＝靜態；綠＝必要項目運作中呼吸（24-bit 真色）
EDGE_GREY = "\x1b[38;2;142;142;160m"
EDGE_GREEN_DIM = "\x1b[38;2;20;110;75m"
EDGE_GREEN_MID = "\x1b[38;2;25;160;110m"
EDGE_GREEN_HIGH = "\x1b[38;2;25;195;125m"
EDGE_GREEN_PEAK = "\x1b[1m\x1b[38;2;46;220;145m"
EDGE_SHINE_RAMP = (
    EDGE_GREY,
    EDGE_GREEN_DIM,
    EDGE_GREEN_MID,
    EDGE_GREEN_HIGH,
    EDGE_GREEN_PEAK,
    EDGE_GREEN_HIGH,
    EDGE_GREEN_MID,
)

# hub／工具噪音：不是必要項目，左緣不畫點
_EDGE_NOISE = re.compile(
    r"^(?:"
    r"Ran\s|Working\b|Ask Codex|for shortcuts|Esc to interrupt|esc to interrupt|"
    r"Thought\b|Thinking\b|Reading\b|Running\b|"
    r"[│└┌├─┃┏┓]|"
    r"\(??(?:ctrl|⌘)\+|"
    r"git\s|python3?\s|bash\s|npm\s|rg\s"
    r")",
    re.IGNORECASE,
)

# 必要項目開頭：真人／席位提示符、待審條目標記
_EDGE_NECESSARY = re.compile(
    r"^(?:"
    r"你>|"
    r"[A-Za-z][\w.-]{0,23}>|"  # the maintainer> / grokbot> / seat id>（不含 › 提示列）
    r"艦長:|"
    r"#\d{3}\b|"
    r"\[待審|"
    r"\[開席"
    r")"
)


def is_necessary_edge_item(line):
    """左緣點只給必要項目開頭；工具軌跡／空列不畫。"""
    s = str(line or "").lstrip()
    if s.startswith(EDGE_DOT):
        s = s[len(EDGE_DOT):].lstrip()
    if not s:
        return False
    if _EDGE_NOISE.match(s):
        return False
    return bool(_EDGE_NECESSARY.match(s))


def strip_leading_edge_dot(line):
    """去掉正文自帶左緣 •，避免再畫一顆變成 ••。"""
    s = str(line or "")
    i = 0
    while i < len(s) and s[i] == " ":
        i += 1
    if s[i:].startswith(EDGE_DOT):
        return s[:i] + s[i + len(EDGE_DOT):].lstrip()
    return s


def edge_dot(shine=None, active=True):
    """左緣 •：GPT 綠／灰（靜態，不呼吸動畫）。

    動畫會讓 hub-frame 整框（含「回應顯示區」頂蓋）每拍重畫而閃爍，
    且亮點容易看起來像跑出彎曲線外。改為：
      - active／有 shine → 固定綠
      - 否則 → 固定灰
    shine 參數保留相容；數值不再驅動色階。
    """
    if not active or shine is None:
        return EDGE_GREY + EDGE_DOT + RESET
    return EDGE_GREEN_MID + EDGE_DOT + RESET


def edge_cell(shine=None, active=True, continued=False, necessary=True):
    """左緣一格：必要項目首行才畫 •；折行／非必要＝空白（寬仍 1）。"""
    if continued or not necessary:
        return " "
    return edge_dot(shine, active=active)


_EDGE_MSG_START = re.compile(
    r"^(?:"
    r"你>|"
    r"[^\s>]{1,24}>|"  # the maintainer> / deepseek> / 真人名>
    r"❯|›|> |"
    r"艦長:|选單|選單|"
    r"#\d{3}"
    r")"
)


def is_edge_continuation(prev, cur, full_w):
    """框／hub 軟折行續行？prev 幾乎滿寬且 cur 不像新訊息開頭。

    full_w 可能是對話框欄寬；hub 截圖常在更窄處就折，所以門檻取
    min(full_w, 觀測合理上限) 與「夠長」兩者較寬鬆的那側。
    """
    if prev is None:
        return False
    cur_s = str(cur or "")
    prev_s = str(prev or "")
    if not cur_s.strip():
        return False
    if _EDGE_MSG_START.match(cur_s.lstrip()):
        return False
    try:
        fw = max(1, int(full_w))
    except (TypeError, ValueError):
        fw = 1
    pw = visible_width(prev_s.rstrip())
    # hub 常見折在 ~70–100；對話框 item_w 更大時不能要求「貼滿 item_w」
    threshold = min(fw - 1, max(56, fw * 3 // 4))
    return pw >= threshold



def rewrite_human_prompt(line, name=None):
    """把回顯前綴「你>」換成真人名（舊 hub 截圖也能對）。"""
    s = str(line or "")
    if name is None:
        try:
            import sys as _sys
            from pathlib import Path as _P
            _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
            from human_attest import human_display_name as _hdn
            name = _hdn()
        except Exception:
            name = "你"
    name = str(name or "你").strip() or "你"
    if s.startswith("你>"):
        return name + ">" + s[2:]
    # 已是「Name>」不重複改
    return s


def _pending_banner(width, shine=None, feature_tier=None):
    """功能區名稱在圓角框上方,左緣對齊 [AIB 訊息待審區] 裡面的 A。"""
    width = max(16, int(width or 16))
    indent = (PENDING_PAD_L
              + visible_width(PENDING_TL + PENDING_H + " ")
              + visible_width("["))
    tier = feature_tier_of(feature_tier)
    title = feature_zone_label(tier)
    if shine is None:
        lit = BOLD + title + RESET
    else:
        mul = FEATURE_SHINE_SPEED_BY_TIER.get(tier, FEATURE_SHINE_SPEED_MUL)
        ramp = FEATURE_SHINE_RAMP_BY_TIER.get(tier, FEATURE_SHINE_RAMP)
        lit = _shine_title(title, shine, speed_mul=mul, ramp=ramp)
    text = (" " * indent) + lit
    pad = width - visible_width(text)
    if pad > 0:
        text += " " * pad
    if visible_width(text) > width:
        text = trunc(text, width)
    return text


def _pending_header_footer(width, seat, identity, size_mark, span, new_count=0,
                           feature_tab=0, show_global=False, kind="pending",
                           hold_count=0, hold_oldest="", inbox=None,
                           borderless=False):
    """圓角框抬頭:頁簽 + 待審 1/N / [≡N]。功能區名在框外。
    borderless=True：不畫 ╭╮╰╯／橫線，只留標題與熱點（下半色塊）。"""
    width = max(16, int(width or 16))
    pad_l = " " * PENDING_PAD_L
    pad_r = " " * PENDING_PAD_R
    box_inner = width - PENDING_PAD_L - PENDING_PAD_R - 2
    try:
        feature_tab = 1 if int(feature_tab) == 1 else 0
    except (TypeError, ValueError):
        feature_tab = 0
    ident = " ".join(str(identity or "").split()) or ("@%s" % seat)
    seat_tok = " %s " % ident
    cap = 2
    header_links = []
    col = PENDING_PAD_L + 2
    left = [_pending_outline(PENDING_TL + PENDING_H, borderless), " "]
    col += 1

    def add(text, plain=None, action=None, tab=None):
        nonlocal col
        vis = plain if plain is not None else text
        w = visible_width(vis)
        if action and seat:
            link = {"start": col, "end": col + w, "action": action, "seat": seat}
            if tab is not None:
                link["tab"] = tab
            header_links.append(link)
        left.append(text)
        col += w

    if kind == "global":
        add(_pending_hot("[%s]" % PENDING_GLOBAL_TITLE),
            "[%s]" % PENDING_GLOBAL_TITLE)
        if span:
            add("  %s" % span)
        add(" ")
        add(_pending_hot("[≡%d]" % int(size_mark)),
            "[≡%d]" % int(size_mark),
            "pending-global-visible-cycle")
        add(" ")
    else:
        for i, label in enumerate(FEATURE_TABS):
            if i:
                add(" ")
            if i == feature_tab:
                add(_pending_hot("[%s]" % label), "[%s]" % label,
                    "feature-tab", i)
            else:
                add(GREY + label + RESET, label, "feature-tab", i)
        if feature_tab == 1 and inbox is not None:
            # 第 70 項 P4:任務區頁簽的抬頭也掛未讀——F 切到任務區仍看得到有信(新信不搶頁的另一半)
            _u_budget = box_inner - (col - PENDING_PAD_L - 2) - min(10, visible_width(seat_tok)) - cap
            unread_tok = _inbox_unread_tok(inbox, budget=_u_budget)
            if unread_tok:
                add(" ")
                add(unread_tok, action="pending-new")
        if feature_tab == 0:
            if span:
                add("  %s" % span)
            new_tok = _pending_new_tok(new_count)
            if new_tok:
                add(new_tok, action="pending-new")
            if inbox is not None:
                # 未讀常駐優先於右上角的席位字:席位字最多讓到 10 格(下面 gap<1 時本來就會截它),
                # 否則寬 66 一有 emoji+型號的身分字,未讀角標就永遠畫不出來。
                _u_after = visible_width(" [≡%d] " % int(size_mark)) + (
                    visible_width(PENDING_GLOBAL_BTN + " ") if show_global else 0)
                _u_budget = box_inner - (col - PENDING_PAD_L - 2) - _u_after - min(10, visible_width(seat_tok)) - cap
                unread_tok = _inbox_unread_tok(inbox, budget=_u_budget)
                if unread_tok:
                    add(" ")
                    add(unread_tok, action="pending-new")
            # 預算 = 框內寬 − 目前已用 − 後面固定要畫的([≡N]、全域鈕)− 席碼 − 右上角
            _after = visible_width(" [≡%d] " % int(size_mark)) + (
                visible_width(PENDING_GLOBAL_BTN + " ") if show_global else 0)
            _budget = box_inner - (col - PENDING_PAD_L - 2) - _after - visible_width(seat_tok) - cap
            hold_tok = _pending_hold_tok(hold_count, hold_oldest, budget=_budget)
            if hold_tok:
                add(hold_tok)
            add(" ")
            add(_pending_hot("[≡%d]" % int(size_mark)),
                "[≡%d]" % int(size_mark),
                "pending-visible-cycle")
            add(" ")
            if show_global:
                add(_pending_hot(PENDING_GLOBAL_BTN), PENDING_GLOBAL_BTN,
                    "pending-global-toggle")
                add(" ")
    left_w = col - PENDING_PAD_L - 2
    gap = box_inner - left_w - visible_width(seat_tok) - cap
    if gap < 1:
        seat_tok = trunc(seat_tok, max(2, box_inner - left_w - cap - 1))
        if not seat_tok.endswith(" "):
            seat_tok = trunc(seat_tok, max(1, visible_width(seat_tok) - 1)) + " "
        gap = box_inner - left_w - visible_width(seat_tok) - cap
    rule = PENDING_H * max(0, gap)
    top = (pad_l + "".join(left) + _pending_outline(rule, borderless) + seat_tok
           + _pending_outline(PENDING_H + PENDING_TR, borderless) + pad_r)
    if visible_width(top) > width:
        top = trunc(top, width)
        header_links = [lk for lk in header_links if lk.get("end", 0) <= width]
    bottom = (pad_l + _pending_outline(PENDING_BL + PENDING_H * box_inner
                                     + PENDING_BR, borderless) + pad_r)
    if visible_width(bottom) > width:
        bottom = trunc(bottom, width)
    return top, bottom, header_links, pad_l, pad_r, box_inner


def render_pending_box(rows, width, seat, offset=0, focus=-1,
                       visible=PENDING_VISIBLE, identity="", size_mark=None,
                       max_height=None, new_count=0, feature_tab=0,
                       shine=None, edge_shine=None, feature_tier=None, show_global=False,
                       inbox=None, inbox_tab=0, notice=None):
    """Ez2 AIB 功能區·待審頁簽:圓角淡框 + 頁簽 + 可見 1/3/5/10 列。

    沒有席位回 ([], [])。沒有列仍畫空框(抬頭換身分),不沿用上一席的舊列。
    focus=-1=還沒點到任何一列,不畫動作按鈕。
    點到的那則若一行放不下,在按鈕下列出全文(受 max_height 限制,留給對話約 8 列)。
    identity=右邊即時席位字(錨/引擎/型號/@碼);空則只寫 @席位。
    size_mark=上框 ≡N 顯示的偏好(1/3/5/10);空則跟 visible。
    inbox=dm_queue.tabs_payload 的整包(第 70 項 P4):給了就畫五 tab 那一行、抬頭未讀角標、列上角標、
      真人列底色、errors 列;列從 inbox["tabs"][選中 tab]["rows"] 取,**rows 參數被忽略**。沒給=舊行為一字不變。
    inbox_tab=選中的 tab 索引(0..4;來自 state/pending-inbox-tab)。
    notice=系統通知一則(pick_notice 挑好的 dict;第 70 項 P6):畫在框內第一列(錯誤列之前),不算進 N、沒有鈕。
    """
    edge_phase = edge_shine if edge_shine is not None else shine
    inbox_tab = inbox_tab_index(inbox_tab)
    inbox_key = INBOX_TABS[inbox_tab]
    inbox_errors = []
    if inbox is not None:
        tabs = (inbox or {}).get("tabs") or {}
        rows = list((tabs.get(inbox_key) or {}).get("rows") or [])
        inbox_errors = [e for e in ((inbox or {}).get("errors") or []) if isinstance(e, dict)]
        if inbox_errors:
            # 病 3:壞檔不進 rows,但畫面要說;做成一列假項目排最前,不能退成「沒有待審訊息」
            reasons = sorted({str(e.get("reason") or "?") for e in inbox_errors})
            rows = [{"id": "", "index": 0, "priority": "normal", "priority_label": "",
                     "when": "", "sender": "", "msg": INBOX_ERROR_PREFIX + "%s(%d 筆)" % (",".join(reasons), len(inbox_errors)),
                     "rev": "", "status": "error", "_inbox_error": True}] + rows
            # ★ review r15 M1(畫面):focus/offset 是 dm_queue.resolve_view 對「不含錯誤列」的列算的;錯誤列塞到最前面
            #   ⇒ 跟通知列/分隔列一樣往下推一格。以前沒推 ⇒ 亮起來與四個鈕帶的是上一則(點 #002 按刪除,刪到 #001)。
            if focus >= 0:
                focus += 1
            if offset > 0:
                offset += 1
        # 非待審 tab 的列可以點選、雙擊開編輯(focus/offset 由 dm_queue.resolve_view(tab=) 依該 tab 算);
        # 四個動作鈕仍只在待審 tab 出現(下方 focused 那一行守著)。
    if inbox is not None and inbox_key == "waiting":
        # 09-15 the maintainer「分開」:dm_queue 已把已送達排在下段;在第一則已送達前插一列分隔,
        # 做法同通知列 —— 不算則數、沒有命中區,焦點/窗口落在它之後的往下推一列。
        _p = next((k for k, r in enumerate(rows or [])
                   if isinstance(r, dict) and not r.get("_inbox_error")
                   and str(r.get("status") or "") == "delivered"), None)
        if _p is not None:
            rows = list(rows[:_p]) + [{"id": "", "index": 0, "priority": "normal", "priority_label": "",
                                       "when": "", "sender": "", "msg": PENDING_DELIVERED_DIVIDER, "rev": "",
                                       "status": "separator", "_inbox_error": True,
                                       "_separator": True}] + list(rows[_p:])
            if focus >= _p:
                focus += 1
            if offset > _p:
                offset += 1
    if isinstance(notice, dict):
        rows = [{"id": "", "index": 0, "priority": "normal", "priority_label": "", "when": "", "sender": "",
                 "msg": notice_text(notice), "rev": "", "status": "notice", "_inbox_error": True,
                 "_notice": True}] + list(rows or [])
        if focus >= 0:
            focus += 1
        if offset > 0:
            offset += 1
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    n = len(rows)
    if not seat:
        return [], []
    width = max(16, int(width or 16))
    # 第 35 項:預算在這裡夾,不在 resize 那一步夾(外面沒有人會替它夾)。
    # 連 CHROME + 1 列內容都放不下 ⇒ 不畫半個框,呼叫端印 HEIGHT_TOO_SHORT。
    if max_height is not None:
        try:
            max_height = int(max_height)
        except (TypeError, ValueError):
            max_height = None
    clamped = False
    if max_height is not None:
        if max_height < CHROME + 1:
            return [], []
        if max(1, int(visible)) > max_height - CHROME:
            visible = max_height - CHROME
            clamped = True
    mark = size_mark if size_mark is not None else visible
    if n == 0:
        banner_links = []
        if inbox is not None:
            banner, banner_links = _inbox_tabs_line(width, seat, inbox, inbox_tab, shine=shine, feature_tier=feature_tier)
        else:
            banner = _pending_banner(width, shine=shine, feature_tier=feature_tier)
        top, bottom, header_links, pad_l, pad_r, box_inner = _pending_header_footer(
            width, seat, identity, mark, "0/0", feature_tab=feature_tab,
            show_global=show_global, inbox=inbox)
        pad_l = " " * PENDING_PAD_L
        pad_r = " " * PENDING_PAD_R
        inner_sp = " " * PENDING_INNER
        chrome = (PENDING_PAD_L + 1 + PENDING_INNER
                  + PENDING_INNER + 1 + PENDING_PAD_R)
        item_w = max(8, width - chrome)
        empty_text = PENDING_EMPTY
        if inbox is not None and inbox_key != "waiting":
            empty_text = "沒有%s的訊息" % INBOX_TAB_LABEL[inbox_key]
        # ≡N 鎖框內列高：空匣也要撐滿 visible 列（與私訊 ≡N 同理），勿只畫 1 列正文
        # 再讓 pane 底下透出牆紙看起來像「沒有列高」。
        vis = max(1, int(visible))
        mids = []
        for i in range(vis):
            if i == 0:
                msg = trunc(empty_text, item_w)
                pad = item_w - visible_width(msg)
                body = msg + (" " * max(0, pad))
            else:
                body = " " * item_w
            mid = (pad_l + " " + edge_cell(edge_phase, necessary=False)
                   + body + inner_sp + " " + pad_r)
            if visible_width(mid) > width:
                mid = trunc(mid, width)
            mids.append(mid)
        lines = [banner, top] + mids + [bottom]
        rowmap = [
            {"line": 0, "links": banner_links},
            {"line": 1, "links": header_links},
        ] + [{"line": 2 + i, "links": []} for i in range(vis)] + [
            {"line": 2 + vis, "links": []},
        ]
        return lines, rowmap
    vis = max(1, int(visible))
    width = max(16, int(width or 16))
    offset = max(0, min(int(offset), max(0, n - vis)))
    try:
        focus = int(focus)
    except (TypeError, ValueError):
        focus = -1
    if focus < 0 or focus >= n:
        focus = -1
    if focus >= 0:
        if focus < offset:
            offset = focus
        if focus >= offset + vis:
            offset = focus - vis + 1
    shown = rows[offset:offset + vis]
    start = offset + 1
    end = offset + len(shown)
    pad_l = " " * PENDING_PAD_L
    pad_r = " " * PENDING_PAD_R
    inner_sp = " " * PENDING_INNER
    # 外白 + │ + 內白 + 內容(+捲動軌) + 內白 + │ + 外白
    chrome = (PENDING_PAD_L + 1 + PENDING_INNER
              + PENDING_INNER + 1 + PENDING_PAD_R)
    gutter_on = n > vis
    # 捲動軌前留一格,按鈕不貼著 █
    gutter_w = 2 if gutter_on else 0
    item_w = max(8, width - chrome - gutter_w)
    content_x = PENDING_PAD_L + 1 + PENDING_INNER
    box_inner = width - PENDING_PAD_L - PENDING_PAD_R - 2

    # 抬頭一律「第幾則/共幾則」(1/3),不要視窗範圍 1–3/3。系統通知列不算則數(它不是信)。
    # 通知/錯誤/分隔列都不算則數:編號與「第幾/共幾」只數真的信(09-15:有通知列時編號從 #002 起跳)
    _pseudo_before = [0]
    for _r in rows:
        _pseudo_before.append(_pseudo_before[-1] + (1 if _r.get("_inbox_error") else 0))
    _notice_rows = _pseudo_before[-1]
    if focus >= 0:
        cur = focus + 1
    else:
        cur = start
    _cur_real = cur - (_pseudo_before[focus] if focus >= 0 else _pseudo_before[max(0, cur - 1)])
    _real_total = max(0, n - _notice_rows)
    # 只有通知/錯誤列、沒有真的信 ⇒ 抬頭 0/0,不是 1/0(10-01 the maintainer:待審 0 則卻顯示 1/0)
    span = "%d/%d" % ((max(1, _cur_real) if _real_total else 0), _real_total)
    mark_n = pending_visible_of(size_mark if size_mark is not None else visible)
    if clamped:
        mark_n = vis   # 接口四 h-D:被夾時抬頭印實際列數,不印偏好值
    banner_links = []
    if inbox is not None:
        banner, banner_links = _inbox_tabs_line(width, seat, inbox, inbox_tab, shine=shine, feature_tier=feature_tier)
    else:
        banner = _pending_banner(width, shine=shine, feature_tier=feature_tier)
    hold_n, hold_oldest = _pending_hold_summary(rows)
    top, _hdr_bottom, header_links, pad_l, pad_r, box_inner = _pending_header_footer(
        width, seat, identity, mark_n, span, new_count=new_count,
        feature_tab=feature_tab, show_global=show_global,
        hold_count=hold_n, hold_oldest=hold_oldest, inbox=inbox)
    if focus >= 0:
        header_links.append({
            "start": PENDING_PAD_L, "end": width - PENDING_PAD_R,
            "action": "pending-unfocus", "seat": seat,
        })
    lines = [banner, top]
    rowmap = [
        {"line": 0, "links": banner_links},
        {"line": 1, "links": header_links},
    ]
    thumb_at = focus if focus >= 0 else offset
    glyphs = pending_gutter_glyphs(n, offset, thumb_at, vis)
    actions = (DMQ_MOVE, "dm-queue-move"), (
        DMQ_SEND_NOW, "dm-queue-send-now"), (
        DMQ_EDIT, "dm-queue-edit"), (DMQ_DELETE, "dm-queue-delete")

    for i, item in enumerate(shown):
        drain_i = offset + i
        focused = drain_i == focus
        item_id = str(item.get("id") or "")
        idx = int(item.get("index") or (drain_i + 1 - _pseudo_before[drain_i]))
        num = "#%03d" % idx   # 09-15 the maintainer:編號一律三位數 #001
        if focused:
            num = BOLD + num + RESET
        pri = str(item.get("priority_label") or "一般")
        sgr = str(item.get("priority_sgr") or "")
        pri_tok = (sgr + pri + RESET) if sgr else pri
        when = str(item.get("when") or "")
        sender = str(item.get("sender") or "未知")
        raw_msg = str(item.get("msg") or "")
        is_error_row = bool(item.get("_inbox_error"))
        is_human = inbox is not None and str(item.get("sender_kind") or "") == "human"
        if inbox is not None and inbox_key == "deleted" and not is_error_row:
            raw_msg = (INBOX_TOMB_FMT % (item.get("deleted_by") or "-", _hhmm_of(item.get("deleted_at")) or "-",
                                        item.get("deleted_why") or "-")) + " " + raw_msg
        one_line = " ".join(raw_msg.split())
        if is_error_row:
            bits = []
        else:
            bits = [num, pri_tok]
            # 09-15 the maintainer:「測」欄沒有就放 -,補到與「測」同寬,後面的欄才對得齊
            bits.append(PENDING_TEST_MARK if str(item.get("origin") or "") == "test"
                        else PENDING_NO_TEST_MARK)
            if inbox is not None:
                bits.extend(_pending_hot(m) for m in _inbox_row_marks(item))
            bits.extend([when, sender])
        meta = (" ".join(bits) + " ") if bits else ""
        # 人審說明放進列上時要插在角標之後、時間寄件人之前(_hold_note_inline)
        meta_head = (" ".join(bits[:-2]) + " ") if bits else ""
        # ★ review r15 M2(畫面):人審說明放進列上時只留「編號 + [人審]」當頭(優先、測、其他角標讓位),
        #   說明緊接 [人審];以前排在 [過期]/歸納標籤之後,寬 60–100 時停住時刻被四個鈕擠掉看不到。
        hold_head = ((" ".join([bits[0], _pending_hot(INBOX_HOLD_MARK)]) + " ")
                     if (bits and inbox is not None) else meta_head)
        meta_tail = (" ".join(bits[-2:]) + " ") if bits else ""
        # 焦點列的動作鈕依 tab:待審=四個;已讀/完成/備存=[編輯][刪除](移動與立刻送出對已處理的信
        # 沒有意義,狀態表也不讓 read→claimed);已刪除=沒有鈕;錯誤列永遠沒有鈕。
        if inbox is None or (inbox_key == "waiting"
                             and str(item.get("status") or "queued") in ("queued", PENDING_HOLD_STATUS)):
            # 排隊中=四鈕照舊;待人處理也給四鈕 —— 立刻送出 = 真人放回佇列再送(09-15 the maintainer 選 2)
            row_actions = actions
        elif inbox_key == "waiting":
            # 待審頁上已送達/待人處理/貼稿中的信:上下移動改排待審頁顯示順序(dm_queue.waiting_move_result);
            # 立刻送出要 queued→claimed,對它不成立 ⇒ [上下移動][編輯][刪除](09-15 the maintainer:「他不是在待審嗎?」)
            row_actions = ((DMQ_MOVE, "dm-queue-move"), (DMQ_EDIT, "dm-queue-edit"), (DMQ_DELETE, "dm-queue-delete"))
        elif inbox_key == "deleted":
            row_actions = ()
        else:
            row_actions = ((DMQ_EDIT, "dm-queue-edit"), (DMQ_DELETE, "dm-queue-delete"))
        focused = focused and bool(row_actions) and not is_error_row
        tail_w = visible_width("".join(a[0] for a in row_actions)) if focused else 0
        wrap_lines = []
        # the maintainer 09-30 15:3x(收件匣 A 項 4):點選一則人審信 ⇒ 正文下面多一行灰字說原因與停住時刻。
        hold_note = _inbox_hold_note(item) if (focused and not is_error_row) else ""
        if focused:
            need = visible_width(meta) + visible_width(one_line) + tail_w
            # ★ review r14 M1(畫面):待審框的實際高度剛好 = 框線 3 + 可見列數(pane 給的 max_height)。
            #   人審說明行能用的是「框裡沒有信的空白列」—— 底下補空白列的迴圈會少補同樣列數,框不會變高。
            #   舊算法把空白列也扣掉 ⇒ 在 pane 裡永遠多不出一行、說明行從來沒畫出來過。
            #   一列空白都沒有(列表滿了)才放進這一列(_hold_note_inline);原因與停住時刻不能整個看不見。
            note_room = 24 if max_height is None else max(0, int(max_height) - 3 - max(0, len(shown) - 1) - 1)
            if need <= item_w and not hold_note:
                left = meta + one_line
            elif need <= item_w:
                left = meta + one_line
                if note_room >= 1:
                    wrap_lines = [GREY + trunc(hold_note, item_w) + RESET]
                else:
                    left = _hold_note_inline(hold_head, meta_tail, item, one_line, item_w - tail_w)
            else:
                left = meta
                wrap_lines = _pending_wrap(raw_msg, item_w)
                others = max(0, len(shown) - 1)
                pads = max(0, vis - len(shown))
                if max_height is None:
                    cap = 24
                else:
                    cap = max(1, int(max_height) - 3 - others - pads)
                extra_cap = max(0, cap - 1)
                if extra_cap <= 0:
                    wrap_lines = []
                    remain = item_w - visible_width(left) - tail_w
                    if hold_note and note_room >= 1:
                        # review r14 M1(畫面):正文折行沒地方,但框裡有空白列 ⇒ 正文截在這一列、說明行用一列空白
                        left = left + trunc(one_line, max(1, remain))
                        wrap_lines = [GREY + trunc(hold_note, item_w) + RESET]
                    elif hold_note:
                        # review r14 M1(畫面):一列空白都沒有 ⇒ 人審原因放進這一列
                        left = _hold_note_inline(hold_head, meta_tail, item, one_line, item_w - tail_w)
                    else:
                        left = left + trunc(one_line, max(1, remain))
                elif len(wrap_lines) > extra_cap:
                    wrap_lines = wrap_lines[:extra_cap]
                    wrap_lines[-1] = trunc(wrap_lines[-1], item_w)
                if hold_note and extra_cap == 1:
                    # 只多得出一行:給說明行,正文退回第一行截斷(與 extra_cap<=0 那條同寫法)
                    remain = item_w - visible_width(left) - tail_w
                    left = left + trunc(one_line, max(1, remain))
                    wrap_lines = [GREY + trunc(hold_note, item_w) + RESET]
                elif hold_note and extra_cap > 1:
                    # 說明行排在正文折行之前,總高度不超過 extra_cap(超過就從正文尾巴讓出一行)
                    wrap_lines = [GREY + trunc(hold_note, item_w) + RESET] + wrap_lines[:extra_cap - 1]
        else:
            remain = item_w - visible_width(meta) - tail_w
            left = meta + trunc(one_line, max(1, remain))
        pad = item_w - visible_width(left) - tail_w
        if pad < 0:
            left = trunc(left, item_w - tail_w)
            pad = item_w - visible_width(left) - tail_w
        col = content_x + visible_width(left) + max(0, pad)
        links = []
        right = ""
        if focused:
            chunks = []
            for text, action in row_actions:
                w = visible_width(text)
                link = {
                    "start": col, "end": col + w, "action": action,
                    "seat": seat, "item": item_id,
                }
                if str(item.get("rev", "")).isdigit():
                    link["rev"] = int(item["rev"])   # 第 36 項:這一幀的修訂號跟著按鈕走
                links.append(link)
                chunks.append(_pending_hot(text))
                col += w
            right = "".join(chunks)
        body_line = left + (" " * max(0, pad)) + right
        if is_human:
            # 真人列:整列內容(含補白)套底色,字照常;RESET 會清掉底色,所以逐段補回
            body_line = HUMAN_ROW_BG + body_line.replace(RESET, RESET + HUMAN_ROW_BG) + HUMAN_ROW_BG_RESET
        if item.get("_notice") or item.get("_separator"):
            body_line = GREY + body_line + RESET      # 系統通知列:灰,與待審列一眼分得開
        gutter_ch = ""
        if gutter_on:
            more_above = offset > 0
            more_below = offset + vis < n
            g = glyphs[i] if i < len(glyphs) else PENDING_TRACK
            g_base = content_x + item_w
            # 只顯 1 列時不畫 █:它會貼在 [刪除] 旁邊、同一套黃,看起來像第五個按鈕。
            if vis == 1:
                if more_above and more_below:
                    gutter_ch = _pending_hot(PENDING_UP) + _pending_hot(PENDING_DOWN)
                    links.append({
                        "start": g_base, "end": g_base + 1,
                        "action": "pending-scroll-up", "seat": seat,
                    })
                    links.append({
                        "start": g_base + 1, "end": g_base + 2,
                        "action": "pending-scroll-down", "seat": seat,
                    })
                elif more_below:
                    gutter_ch = " " + _pending_hot(PENDING_DOWN)
                    links.append({
                        "start": g_base + 1, "end": g_base + 2,
                        "action": "pending-scroll-down", "seat": seat,
                    })
                elif more_above:
                    gutter_ch = " " + _pending_hot(PENDING_UP)
                    links.append({
                        "start": g_base + 1, "end": g_base + 2,
                        "action": "pending-scroll-up", "seat": seat,
                    })
                else:
                    gutter_ch = "  "
            else:
                g_action = None
                if i == 0 and more_above:
                    g_action = "pending-scroll-up"
                elif i == vis - 1 and more_below:
                    g_action = "pending-scroll-down"
                g_col = g_base + 1
                if g_action:
                    links.append({
                        "start": g_col, "end": g_col + 1,
                        "action": g_action, "seat": seat,
                    })
                if g in (PENDING_UP, PENDING_DOWN):
                    gutter_ch = " " + _pending_hot(g)
                elif g == PENDING_THUMB:
                    gutter_ch = " " + g
                else:
                    gutter_ch = " " + GREY + g + RESET
        if focused:
            # 點同一則第二次收合。只蓋本文,不蓋右側四個按鈕(先掛的按鈕仍先命中)。
            body_end = content_x + item_w - tail_w
            if body_end > content_x:
                links.append({
                    "start": content_x, "end": body_end,
                    "action": "pending-unfocus", "seat": seat,
                })
        elif item_id:
            links.append({
                "start": content_x, "end": content_x + item_w,
                "action": "pending-focus", "seat": seat, "item": item_id,
            })
        line = (pad_l + " " + edge_cell(edge_phase, necessary=False)
                + body_line + gutter_ch + inner_sp
                + " " + pad_r)
        if visible_width(line) > width:
            line = trunc(line, width)
            links = [lk for lk in links if lk.get("end", 0) <= width]
        lines.append(line)
        rowmap.append({"line": len(lines) - 1, "links": links})
        gutter_pad = " " * visible_width(gutter_ch)
        for extra in wrap_lines:
            extra_pad = item_w - visible_width(extra)
            extra_body = extra + (" " * max(0, extra_pad))
            extra_line = (pad_l + " " + edge_cell(edge_phase, continued=True, necessary=False)
                          + extra_body + gutter_pad + inner_sp
                          + " " + pad_r)
            if visible_width(extra_line) > width:
                extra_line = trunc(extra_line, width)
            extra_links = []
            if focused:
                extra_links.append({
                    "start": content_x, "end": content_x + item_w,
                    "action": "pending-unfocus", "seat": seat,
                })
            lines.append(extra_line)
            rowmap.append({"line": len(lines) - 1, "links": extra_links})

    # 選 5/10 但佇列較少時仍把框撐到那個高度,空列不點擊。
    # 前兩列是功能區名+圓角抬頭。
    while len(lines) - 2 < vis:
        blank = " " * (item_w + gutter_w)
        line = (pad_l + " " + edge_cell(edge_phase, necessary=False)
                + blank + inner_sp + " " + pad_r)
        if visible_width(line) > width:
            line = trunc(line, width)
        lines.append(line)
        rowmap.append({"line": len(lines) - 1, "links": []})

    bottom = (pad_l + _pending_outline(PENDING_BL + PENDING_H * box_inner
                                     + PENDING_BR, False) + pad_r)
    if visible_width(bottom) > width:
        bottom = trunc(bottom, width)
    lines.append(bottom)
    rowmap.append({"line": len(lines) - 1, "links": []})
    return lines, rowmap


def global_gate(viewer_seat, captain, open_wanted):
    """👑 對得上才畫 [全域];對不上或沒 👑 → 關按鈕與下框。

    回 (show_btn, draw_open, persist_open)。呼叫端負責寫回狀態檔。
    空列由 render_global_box 自己不畫,本函式不管有沒有信。
    """
    view = str(viewer_seat or "").strip().lstrip("@").rstrip("*")
    cap = str(captain or "").strip().lstrip("@").rstrip("*")
    if not view or not cap or cap != view:
        return False, False, False
    on = bool(open_wanted)
    return True, on, on


def stack_pending_and_global(pending_lines, pending_map, global_lines, global_map):
    """同一 review pane 上下兩框;空下框不疊。"""
    pl = list(pending_lines or [])
    pm = list(pending_map or [])
    gl = list(global_lines or [])
    gm = list(global_map or [])
    if not gl:
        return pl, pm
    off = len(pl)
    out_m = list(pm)
    for row in gm:
        if not isinstance(row, dict):
            continue
        rr = dict(row)
        try:
            rr["line"] = int(row.get("line") or 0) + off
        except (TypeError, ValueError):
            rr["line"] = off
        out_m.append(rr)
    return pl + gl, out_m


def render_global_box(rows, width, viewer_seat, identity="",
                      visible=PENDING_VISIBLE, size_mark=None, max_height=None,
                      shine=None, edge_shine=None):
    """全艙待審下框:標題 AIB 全域訊息區。空列不畫。"""
    edge_phase = edge_shine if edge_shine is not None else shine
    # max_height(第 35 項):整框含框線不得超過它;低於 GLOBAL_CHROME + 1 ⇒ 不畫。
    # 被夾到比偏好少時,抬頭 [≡N] 印**實際**列數(接口四 h-D),不印偏好值。
    # ★ 上面那一行 docstring 是 10b 還原刀的針(test_z_restore_knife_render_global_box_reds_entry
    #   要它恰好出現一次),不要改成多行。
    items = [r for r in (rows or []) if isinstance(r, dict)]
    if not items or not viewer_seat:
        return [], []
    width = max(16, int(width or 16))
    vis = max(1, int(visible))
    clamped = False
    if max_height is not None:
        try:
            mh = int(max_height)
        except (TypeError, ValueError):
            mh = None
        if mh is not None:
            if mh < GLOBAL_CHROME + 1:
                return [], []
            if vis > mh - GLOBAL_CHROME:
                vis = mh - GLOBAL_CHROME
                clamped = True
    n = len(items)
    shown = items[:vis]
    start = 1
    cur = start
    span = "%d/%d" % (cur, n)
    mark_n = pending_visible_of(size_mark if size_mark is not None else visible)
    if clamped:
        mark_n = vis
    top, bottom, header_links, pad_l, pad_r, box_inner = _pending_header_footer(
        width, viewer_seat, identity, mark_n, span, kind="global")
    inner_sp = " " * PENDING_INNER
    chrome = (PENDING_PAD_L + 1 + PENDING_INNER
              + PENDING_INNER + 1 + PENDING_PAD_R)
    item_w = max(8, width - chrome)
    lines = [top]
    rowmap = [{"line": 0, "links": header_links}]
    for i, item in enumerate(shown):
        idx = int(item.get("index") or (i + 1))
        num = "#%03d" % idx   # 09-15 the maintainer:編號一律三位數 #001
        pri = str(item.get("priority_label") or "一般")
        sgr = str(item.get("priority_sgr") or "")
        pri_tok = (sgr + pri + RESET) if sgr else pri
        when = str(item.get("when") or "")
        sender = str(item.get("sender") or "未知")
        owner = str(item.get("seat") or "")
        bits = [num, pri_tok]
        # 09-15 the maintainer:「測」欄沒有就放 -,補到與「測」同寬,後面的欄才對得齊
        bits.append(PENDING_TEST_MARK if str(item.get("origin") or "") == "test"
                    else PENDING_NO_TEST_MARK)
        if owner:
            bits.append("@%s" % owner)
        bits.extend([when, sender])
        meta = " ".join(bits) + " "
        raw_msg = str(item.get("msg") or "")
        one_line = " ".join(raw_msg.split())
        remain = item_w - visible_width(meta)
        left = meta + trunc(one_line, max(1, remain))
        pad = item_w - visible_width(left)
        if pad < 0:
            left = trunc(left, item_w)
            pad = item_w - visible_width(left)
        body = left + (" " * max(0, pad))
        line = (pad_l + " " + edge_cell(edge_phase, necessary=False)
                + body + inner_sp + " " + pad_r)
        if visible_width(line) > width:
            line = trunc(line, width)
        lines.append(line)
        rowmap.append({"line": len(lines) - 1, "links": []})
    bot = (pad_l + _pending_outline(PENDING_BL + PENDING_H * box_inner
                                  + PENDING_BR, False) + pad_r)
    if visible_width(bot) > width:
        bot = trunc(bot, width)
    lines.append(bot)
    rowmap.append({"line": len(lines) - 1, "links": []})
    return lines, rowmap


FEATURE_ZONE_MORE = "還有 %d 列未顯示"


def feature_zone_item_width(width):
    """任務區頁簽一列能放幾格內容(給 38 產生器當 width 用,與框內寬同源)。"""
    width = max(16, int(width or 16))
    chrome = (PENDING_PAD_L + 1 + PENDING_INNER
              + PENDING_INNER + 1 + PENDING_PAD_R)
    return max(8, width - chrome)


def render_feature_zone_box(width, seat, identity="", body_lines=None,
                            max_height=None, shine=None, edge_shine=None, feature_tier=None, inbox=None):
    """功能區第二頁簽(AI任務區)的框:外框與待審同一套,正文由呼叫端給(第 39 項)。

    body_lines 是 38 產生器 `render_lines(seat, width, max_lines)` 吐的純文字列
    (每格自帶來源與時刻);本函式只負責框線、頁簽列、截斷與高度 —— **不讀任何資料**,
    沙盒任務卡在這裡沒有入口(第 50 項:零張)。
    max_height 以列計(含 CHROME);超過就少列並補「還有 N 列未顯示」;低於 CHROME+1 不畫。
    """
    edge_phase = edge_shine if edge_shine is not None else shine
    if not seat:
        return [], []
    width = max(16, int(width or 16))
    banner = _pending_banner(width, shine=shine, feature_tier=feature_tier)
    # inbox(第 70 項 P4):只用來在抬頭掛「未讀 N·HH:MM」——人在看任務區時也看得到有信,而頁不切
    top, bottom, header_links, pad_l, pad_r, box_inner = _pending_header_footer(
        width, seat, identity, 3, "", feature_tab=1, inbox=inbox)
    inner_sp = " " * PENDING_INNER
    item_w = feature_zone_item_width(width)

    def inner(text):
        msg = trunc(str(text), item_w)
        pad = item_w - visible_width(msg)
        body = msg + (" " * max(0, pad))
        line = (pad_l + " " + edge_cell(edge_phase, necessary=False)
                + body + inner_sp + " " + pad_r)
        if visible_width(line) > width:
            line = trunc(line, width)
        return line

    body_src = [str(x) for x in (body_lines or [])]
    body_cap = None
    if max_height is not None:
        try:
            body_cap = int(max_height) - CHROME
        except (TypeError, ValueError):
            body_cap = None
        if body_cap is not None and body_cap < 1:
            return [], []
    if body_cap is not None and len(body_src) > body_cap:
        keep = max(0, body_cap - 1)
        hidden = len(body_src) - keep
        body_src = body_src[:keep] + [FEATURE_ZONE_MORE % hidden]
    if not body_src:
        body_src = [FEATURE_TASKS_EMPTY]
    body = [inner(x) for x in body_src]
    lines = [banner, top] + body + [bottom]
    rowmap = [
        {"line": 0, "links": []},
        {"line": 1, "links": header_links},
    ]
    for i in range(2, len(lines)):
        rowmap.append({"line": i, "links": []})
    return lines, rowmap


def render_feature_tasks_box(width, seat, identity="", tasks=None,
                             max_height=None, shine=None, edge_shine=None, feature_tier=None):
    """(舊)功能區第二頁簽畫沙盒任務卡。第 39 項起產品不再呼叫它 —— 頁簽正文改由
    38 產生器供給(render_feature_zone_box);留著只為既有尺與到達性刀,不是入口。"""
    edge_phase = edge_shine if edge_shine is not None else shine
    if not seat:
        return [], []
    width = max(16, int(width or 16))
    banner = _pending_banner(width, shine=shine, feature_tier=feature_tier)
    top, bottom, header_links, pad_l, pad_r, box_inner = _pending_header_footer(
        width, seat, identity, 3, "", feature_tab=1)
    inner_sp = " " * PENDING_INNER
    item_w = feature_zone_item_width(width)

    def inner(text):
        msg = trunc(text, item_w)
        pad = item_w - visible_width(msg)
        body = msg + (" " * max(0, pad))
        line = (pad_l + " " + edge_cell(edge_phase, necessary=False)
                + body + inner_sp + " " + pad_r)
        if visible_width(line) > width:
            line = trunc(line, width)
        return line

    rows = [t for t in (tasks or []) if isinstance(t, dict)]
    terminal = {"done", "cancelled", "closed", "abandoned"}
    running = [t for t in rows if str(t.get("status") or "") not in terminal]
    done_n = sum(1 for t in rows if str(t.get("status") or "") in terminal)
    # 第 35 項:預算數的是**列**不是件數(§89.7.1 h-3:一件帶標題畫 2 列,原本
    # cap 數件數 ⇒ max_height=32 吐 45 列)。body_cap = 內容可用列;None = 不夾。
    body_cap = None
    if max_height is not None:
        try:
            body_cap = int(max_height) - CHROME
        except (TypeError, ValueError):
            body_cap = None
        if body_cap is not None and body_cap < 1:
            return [], []
    body = [inner("%s %d/%d" % (FEATURE_TASKS_HEAD, done_n, len(rows)))]
    if not running:
        if body_cap is None or body_cap >= 2:
            body.append(inner(FEATURE_TASKS_EMPTY))
    elif body_cap is not None and body_cap < 3:
        # 連「進行中:」+ 一件都放不下:只說還有幾件沒畫,不畫半件。
        if body_cap >= 2:
            body.append(inner(FEATURE_TASKS_MORE % len(running)))
    else:
        body.append(inner("進行中:"))
        shown_n = 0
        for task in running:
            title = str(task.get("title") or "")
            need = 2 if title else 1
            if body_cap is not None:
                remain = body_cap - len(body)
                last = shown_n == len(running) - 1
                # 不是最後一件時要替「還有 N 件未顯示」那一列留位子
                if need > remain or (not last and need + 1 > remain):
                    break
            tid = str(task.get("id") or "?")
            st = str(task.get("status") or "unknown")
            who = str(task.get("assignee") or "-")
            body.append(inner("* %s  %s  %s" % (tid, st, who)))
            if title:
                body.append(inner("  %s" % title))
            shown_n += 1
        if shown_n < len(running):
            body.append(inner(FEATURE_TASKS_MORE % (len(running) - shown_n)))
    lines = [banner, top] + body + [bottom]
    rowmap = [
        {"line": 0, "links": []},
        {"line": 1, "links": header_links},
    ]
    for i in range(2, len(lines)):
        rowmap.append({"line": i, "links": []})
    return lines, rowmap


# ── 274 右欄艦隊收合(a)+ 視窗與捲軸(b)+(e)1/2/5/7:對席位樹那一段做後處理 ─────────────
# 規格 docs/spec/RIGHT-FLEET-FOLD-v1.md;三簽名住 right_fold_api(不改名)。
# 摺疊列 = 每支艦隊的 level 0 席(👑 現任艦長;無艦長組 = 佔位列,鍵 fleet:<i>)。
# 收合:只留摺疊列,成員/🎭🧠 子列/子代理/軌一起藏,列尾摘要「n 席・m 做中・k 紅」。
# 視窗:席位樹那段畫 rows[offset:offset+H],最右 1 欄捲軸 ░/█(N≤H 不畫)。
# rowmap:摺疊列多帶 fold=<鍵>;每列帶 epoch(採集端給)——點擊前驗同一幀((e)2)。
FOLD_GLYPH_FOLDED = "▸"
FOLD_GLYPH_OPEN = "▾"
FOLD_ACTION_TOGGLE = "fold-toggle"
FOLD_ACTION_EXPAND_ALL = "fold-expand-all"
FOLD_ACTION_COLLAPSE_ALL = "fold-collapse-all"
FOLD_ACTION_SCROLL = "fold-scroll"
FOLD_MEMBER_LEVEL_MAX = 2


def _fold_key_of(ai, index):
    """摺疊鍵:艦長列 = 畫面上 @五碼(name 尾的 @xxxxx,與 dm 位址同源);沒有就 seat_id;
    佔位列 = fleet:<index>。"""
    if ai.get("placeholder"):
        return "fleet:%d" % index
    name = str(ai.get("name") or "")
    m = re.search(r"@([0-9a-z]{5})\b", name)
    if m:
        return m.group(1)
    seat = str(ai.get("seat_id") or "")
    return seat or ("fleet:%d" % index)


def _member_is_working(ai):
    """m 做中 = 非閒置且非開席中(規格 §1)。閒置以 activity_state 的 💤 認,不新造狀態。"""
    if ai.get("onboard_loading") or ai.get("opening_attempt_text"):
        return False
    try:
        age = float(ai.get("age_minutes") or 0)
    except (TypeError, ValueError):
        age = 0.0
    return not activity_state(age).startswith("💤")


def apply_right_fold(lines, rowmap, owners, agents, opts, width, region_start, region_end):
    """收合 + 視窗後處理。opts 不是 dict 或 enabled 不為 True ⇒ 原樣回(逐位元組)。"""
    if not isinstance(opts, dict) or opts.get("enabled") is not True:
        return lines, rowmap
    try:
        from right_fold_api import fold_state, viewport, fold_summary, should_force_expand  # noqa: E402
    except ImportError:                                            # 呼叫端沒把 render/ 放進路徑
        from render.right_fold_api import fold_state, viewport, fold_summary, should_force_expand  # noqa: E402
    expanded = opts.get("expanded") if isinstance(opts.get("expanded"), dict) else {}
    epoch = int(opts.get("epoch") or 0)
    levels = [_agent_level(a) for a in agents]
    # 艦隊區塊:level 0 的席 h 到下一個 level 0 之前
    heads = [i for i, lvl in enumerate(levels) if lvl == 0]
    # 每列 → 席;每席 → 它的第一列(主列)
    first_line = {}
    for li in range(region_start, region_end):
        o = owners[li] if li < len(owners) else None
        if o is not None and o not in first_line:
            first_line[o] = li
    keep = [True] * len(lines)
    head_glyph = {}          # line index → ▸/▾
    head_summary = {}        # line index → 摘要
    fold_keys = {}           # line index → key
    for hi, h in enumerate(heads):
        end = heads[hi + 1] if hi + 1 < len(heads) else len(agents)
        key = _fold_key_of(agents[h], h)
        members = [i for i in range(h + 1, end) if not agents[i].get("placeholder")]
        block_lines = [li for li in range(region_start, region_end)
                       if owners[li] is not None and h <= owners[li] < end]
        block_texts = [strip_ansi(lines[li]) for li in block_lines]
        folded = fold_state.is_folded(expanded, key)
        if folded and should_force_expand(block_texts):      # (e)1 要人點的列永遠不藏
            folded = False
        hl = first_line.get(h)
        if hl is None:
            continue
        fold_keys[hl] = key
        head_glyph[hl] = FOLD_GLYPH_FOLDED if folded else FOLD_GLYPH_OPEN
        if folded:
            n = len(members)
            m = sum(1 for i in members if _member_is_working(agents[i]))
            k = sum(1 for i in members if i in first_line and "🔴" in strip_ansi(lines[first_line[i]]))
            fmt = opts.get("summary_fmt")
            head_summary[hl] = (fmt.format(n=n, m=m, red=k) if isinstance(fmt, str) and fmt
                                else fold_summary(n, m, k))
            for li in block_lines:
                if li != hl:
                    keep[li] = False
    # 摺疊列改寫:第 1 欄摺疊符(取代左緣那 1 格空白),列尾摘要(截寬只吃名字那一段以外的東西:直接 fit)
    out_lines = []
    out_map = []
    old_to_new = {}
    for li, line in enumerate(lines):
        if not keep[li]:
            continue
        text = line
        if li in head_glyph:
            plain = strip_ansi(text)
            glyph = head_glyph[li]
            # 左緣那 1 格空白換成摺疊符(ANSI 前綴裡沒有空白,replace 第一個空白就是它);寬度不變
            text = text.replace(" ", glyph, 1) if plain.startswith(" ") else glyph + text
            if li in head_summary:
                summary = " · " + head_summary[li]
                base = visible_width(strip_ansi(text))
                if base + visible_width(summary) > width:
                    text = trunc(text, max(1, width - visible_width(summary)))
                text = text + summary
        old_to_new[li] = len(out_lines)
        out_lines.append(text)
    for entry in rowmap:
        li = entry.get("line")
        if li not in old_to_new:
            continue
        e = dict(entry)
        e["line"] = old_to_new[li]
        if li in fold_keys:
            e["fold"] = fold_keys[li]
        e["epoch"] = epoch
        out_map.append(e)
    # 視窗:席位樹那段(區塊內剩下的列)畫 offset..offset+H-1;其他列不動
    new_region_start = old_to_new.get(region_start)
    if new_region_start is None:
        for li in range(region_start, region_end):
            if li in old_to_new:
                new_region_start = old_to_new[li]
                break
    region_lines = [old_to_new[li] for li in range(region_start, region_end) if li in old_to_new]
    if not region_lines:
        return out_lines, out_map
    rs, re_ = region_lines[0], region_lines[-1] + 1
    n = re_ - rs
    try:
        total = int(opts.get("height") or 0)
    except (TypeError, ValueError):
        total = 0
    # H = 可用總高 − 席位樹以外的列(上方狀態列/下方圖例)− 標題鈕列;沒給高度 ⇒ 全畫(不裁)
    has_title = isinstance(opts.get("expand_all"), str) and isinstance(opts.get("collapse_all"), str) and bool(heads)
    h = (total - (len(out_lines) - n) - (1 if has_title else 0)) if total >= 1 else n
    if h < 1:
        h = 1
    offset = viewport.clamp(viewport.offset({"schema": viewport.SCHEMA, "offset": opts.get("offset", 0)}), n, h)
    if h < 3 and n > h:
        window = viewport.slice(out_lines[rs:re_], offset, h)      # (e)5:矮窗只印「▼ 還有 n 席」
        visible_old = set()
    else:
        window = out_lines[rs:rs + n][offset:offset + h]
        visible_old = set(range(rs + offset, rs + offset + len(window)))
    bar = viewport.scrollbar_glyphs(offset, n, h) if h >= 3 else []
    # ★ 探寬度那趟(required_ai_pane_width 用 width=sys.maxsize)不填充:與圖例置中同一條紀律(MAX_SANE_PANE_W),
    #   填充本來就不計入自然寬度;不擋的話 fit() 補 4.6×10^18 個空白 ⇒ MemoryError,採集端每一幀退成
    #   「清單暫時無法取得」(09-17 a seat 走採集端才抓到;render_lib 內用 width=40 的尺看不到)。
    #   仍注入 right_fold 去量:摺疊列摘要與捲軸欄要算進自然寬,否則 pane-fit 會少算那一截。
    _fill = width <= MAX_SANE_PANE_W
    if bar:
        window = [(fit(w, max(1, width - 1)) if _fill else w) + bar[i] if i < len(bar) else w for i, w in enumerate(window)]
    # 標題列兩鈕(規格 §1):`[全部展開] [全部收合]`,link spans 帶 action;放在視窗之上、不捲。
    title = []
    title_map = []
    if isinstance(opts.get("expand_all"), str) and isinstance(opts.get("collapse_all"), str) and heads:
        a = "[" + opts["expand_all"] + "]"
        b = "[" + opts["collapse_all"] + "]"
        row = " " + a + " " + b
        title = [fit(row, width) if _fill else row]
        title_map = [{"line": rs, "epoch": epoch, "links": [
            {"start": 1, "end": 1 + visible_width(a), "action": FOLD_ACTION_EXPAND_ALL},
            {"start": 2 + visible_width(a), "end": 2 + visible_width(a) + visible_width(b), "action": FOLD_ACTION_COLLAPSE_ALL}]}]
    final_lines = out_lines[:rs] + title + window + out_lines[re_:]
    final_map = []
    shift_after = len(title) + len(window) - n
    rs_win = rs + len(title)
    for e in out_map:
        li = e["line"]
        if li < rs:
            final_map.append(e)
        elif li >= re_:
            e2 = dict(e); e2["line"] = li + shift_after; final_map.append(e2)
        elif li in visible_old:
            e2 = dict(e); e2["line"] = rs_win + (li - rs - offset); final_map.append(e2)
    final_map.extend(title_map)
    if bar:
        # 捲軸點擊區:每一視窗列最右 1 欄;thumb 上 = 不動、上方 = −H、下方 = +H(hit_test 認 zone)
        for i, glyph in enumerate(bar):
            zone = "thumb" if glyph == "█" else ("up" if i < bar.index("█") else "down")
            final_map.append({"line": rs_win + i, "bar": {"col": max(0, width - 1), "zone": zone, "page": h}, "epoch": epoch})
    return final_lines, final_map


def render_ai_list_with_map(data, width=DEFAULT_PANE_W - 1):
    # 預設寬 26→27(08-14 三字縮寫裁令「行寬/截斷全鏈重算」):引擎欄
    # 2→3 格,不加寬會吃掉名字一格(a seat→pm@773…,身份截斷)。
    """render_ai_list 的本體:同時回傳每一行對應哪個 AI 的 hub 視窗。

    列表與行對映由同一次迴圈產生 —— 兩邊分開寫就會在改版時悄悄錯開,
    使用者點第三列卻跳到第二個 AI,而畫面看起來完全正常。
    """
    data = clean_data(data)            # E2.11:資料進入點清控制字元(含 agents/legend)
    rowmap = []
    # 274:每一列屬於哪一席(agents 索引;席位樹以外的列 = None)。收合/視窗後處理用它切艦隊區塊,
    #   與畫面同一次迴圈產生(第二份清單會在改版時悄悄錯開,與 rowmap 同一條紀律)。
    _owners = []
    _cur_agent = [None]

    def emit(line, window=None, detail=None, kill=None, spans=None, links=None):
        _owners.append(_cur_agent[0])
        if links and not window:
            # the maintainer 09-06 13:3x/13:5x 裁:用量列點 LOGO/品牌名開該家用量頁、點 [重驗] 重探。
            # 無 window 的列不跳窗,只帶 link spans(start/end 與畫面同一次算出);
            # hit_test 只認 open-usage-page / reprobe-usage 兩個 verb。
            rowmap.append({"line": len(lines), "links": [dict(s) for s in links]})
        if window:
            entry = {"line": len(lines), "window": window}
            if detail:
                entry["detail"] = dict(detail)
            if kill:
                # [X] 精準命中區(08-14 the maintainer 直令):只有這三格觸發釋放,
                # 行內其他處=既有跳窗——破壞性入口第二道安全欄
                entry["kill"] = dict(kill)
            if spans:
                # L2-03(D-3):第二/三行的 [綁定人格]/[綁定大腦]/[解綁] 精準命中區,
                # 與 kill 同一種機制(start/end 由**同一次渲染**算出,與畫面同源);
                # 行內其他處仍是跳窗。每個 span 帶 action + seat(seat_id),
                # hit_test 原樣回傳,mouse-ctl 依 action 開選單/執行。
                entry["spans"] = [dict(s) for s in spans]
            rowmap.append(entry)
        lines.append(line)

    def _tail_span(text, token, action, seat):
        """token 剛好在 text 尾端時給出它的 [start,end) 欄位區;不在(被截掉)=None。
        寬度用 visible_width 算(中文兩格),與 [X] 的算法同源。"""
        if not seat or not text.endswith(token):
            return None
        _w = visible_width(text)
        return {"start": _w - visible_width(token), "end": _w,
                "action": action, "seat": seat}

    lines = []
    # ★ 2026-09-04 the maintainer 令:拿掉「AI(主+子)」抬頭。上面已經是機器狀態列,
    #   再放一行只說「這裡是 AI 清單」的字是多的 —— 右欄寸土寸金,一行 = 少看一席。
    #   ★ 保留下面那個空行:它是狀態列與第一席之間的分隔,拿掉會黏在一起。
    emit("")
    # 08-14 the maintainer 拍板提案 A「完整樹軌」(AskUser 收據,preview 原樣):
    # 手足 ├、末位 ╰、上層未完 │ 續軌,子代理層同規則——孤懸 ╰ 沒有脊柱
    # 是「拉長很怪」的根源。軌道欄位沿眼驗裁令:成員軌 col2(👑 中線)。
    # ★ 子代理軌:08-14 定 col4(成員 icon 中線),**08-21 眼驗再退一層**
    #   → level 0 col4、level 1 col6,且 level 0 子列 col2 補脊柱。
    #   「成員 icon 中線」那個理由已不成立,新理由見 _sub_prefix 旁註解。
    #   production 層級只有 0/1;level 2 的
    # 續軌近似以縮排承載(clamp 上限,無真實資料形)。
    # 08-14 丟席 bug 根因:此處原有 [:6] 上古六席上限——真機 9 窗被**靜默**
    # 截 3(恰為排尾端的非現任艦長×2+末位 crew),違 fail-visible。上限拆除:
    # 顯示裁切交 pane-fit(視覺裁不丟資料),rowmap 完整。
    _levels = [_agent_level(a) for a in (data.get("agents") or [])]

    def _continues_at(i, lvl):
        """席 i 之後、回到更淺層之前,還有沒有同為 lvl 層的席
        (= lvl 層的脊柱在席 i 底下要不要續畫)。"""
        for j in range(i + 1, len(_levels)):
            if _levels[j] < lvl:
                return False
            if _levels[j] == lvl:
                return True
        return False

    def _sibling_continues(i):
        # ★ 09-02 起看「回到更淺層之前還有沒有同層」而不只看下一席:
        #   成員底下接了組員(更深一層)再接下一個成員時,舊式只看下一席會把
        #   第一個成員畫成 ╰,而它下面明明還有手足。
        return _continues_at(i, _levels[i])

    # ── 樹軌幾何(AR/AR-2/AR-3,the maintainer 2026-09-02 眼驗三裁;工單 WO-AR-subagent-corner)──
    # 左緣 1 格空白不動。欄位由一條公式算,不寫死:
    #   席列軌欄  _seat_col(L):level 0 無軌(icon 起 col1);level 1 軌 col1;
    #             level L≥2 軌 = 上一層的屬性欄(1 + 3(L−1))
    #   屬性/子 AI 軌欄 _attr_col(L):艦長 col3(席列 +2);成員 = 席列軌 +3
    #   「屬性與席位永不同欄」(AR-3 第 1 條):艦長屬性 col3 vs 成員席列 col1。
    # 所有 ├/╰ 軌後補一個 `-`(AR-2):席列 `├-`/`╰-`、屬性列 `├-▸`/`╰-▸`、
    # 子 AI 列 `├--⚙`/`╰--⚙`(AR:彎角自帶;虛線=附屬物語意保留,08-21 裁)。
    # 寬度規則(艦長 a6b2e 09-02 15:3x 裁,取代 AR 第 1 條):⚙ 前綴恰為「軌 + 兩個 -」。
    def _seat_col(lvl):
        return 0 if lvl == 0 else 1 + 3 * (lvl - 1)

    def _attr_col(lvl):
        return 3 if lvl == 0 else _seat_col(lvl) + 3

    def _under_base(i, lvl):
        """席 i(lvl 層)底下每一列(屬性/子 AI)在軌字元之前的前綴:寬恰 _attr_col(lvl),
        脊柱穿過的欄位畫 │,其餘空白。艦長底下接成員 ⇒ 脊柱落在成員席列的軌欄 col1。"""
        cols = [" "] * _attr_col(lvl)
        if lvl == 0:
            if i + 1 < len(_levels) and _levels[i + 1] > 0:
                cols[1] = "│"
        else:
            for a in range(1, lvl + 1):
                if _continues_at(i, a):
                    cols[_seat_col(a)] = "│"
        return "".join(cols)

    def _spacer(i, lvl, deeper):
        """席位之間的間隔行:脊柱落在下一列的軌欄,不掛在半空中(08-16 the maintainer 螢幕實見)。"""
        n = _seat_col(lvl + 1) + 1 if deeper else _attr_col(lvl)
        cols = [" "] * max(n, 1)
        for a in range(1, lvl + 1):
            if _continues_at(i, a):
                cols[_seat_col(a)] = "│"
        if deeper:
            cols[_seat_col(lvl + 1)] = "│"
        return "".join(cols).rstrip()

    _labels = dict(DEFAULT_LABELS)
    for _k, _v in (data.get("labels") or {}).items():
        if _k in _labels and _v:
            _labels[_k] = str(_v)

    agents = data.get("agents") or []
    # ★★ EH.4 修法(a seat 審查格 2):`~` 與解釋它的圖例**同一個來源、算一次**。
    #   原本 :659 只看 persona_source、:864 只看 legend,兩處互不參照 ——
    #   不變式寫在註解裡而沒有寫在碼裡,於是它只靠上游寫端守衛成立;
    #   而守衛在**寫入路徑**上、這裡在**讀取路徑**上,守衛之前就存在的卡
    #   會畫出一個沒有人解釋的標記。抬到這裡之後,兩個消費者結構上同生共死。
    _legend_created = (data.get("legend") or {}).get("persona_created")
    # ★★ L9 沿用上面那條 EH.4 教訓的**結構**,不只是它的結論:
    #   記號與解釋它的圖例在同一個地方算一次,沒有圖例就不畫記號。
    #   分成兩處算的話,不變式只活在註解裡,而註解不會在改動時變紅。
    _legend_drift = (data.get("legend") or {}).get("model_drift")
    _legend_req = (data.get("legend") or {}).get("model_requested")
    _req_marked = False
    # 真的有列掛上記號了嗎。圖例只在**畫面上真的出現記號**時才加 ——
    # 反向的那一半:解釋一個畫面上不存在的東西,同樣是雜訊。
    _drift_marked = False
    # ── 右欄兩行的顯示開關(the maintainer 2026-09-05:`aib ai role on/off` / `aib ai sb on/off`)
    #    採集端把設定讀成 data["ai_view"];**渲染器不讀檔**——與
    #    engine_badge_mode / model 同一條紀律(黃金幀要決定性,render_lib 零相依)。
    # ★★ 缺鍵 = 兩個都畫。這不是防禦性寫法,是判準:舊治具與黃金幀的
    #    render_data 裡沒有 `ai_view`,而「沒有設定檔」必須等於今天的畫面。
    #    若這裡預設成 False,缺檔就會少畫兩行,而**沒有任何一格會紅**
    #    (黃金幀會紅,但那時人會去改黃金幀而不是改這裡)。
    _ai_view = data.get("ai_view") or {}
    _show_role = _ai_view.get("role", True) is not False
    _show_sb = _ai_view.get("sb", True) is not False
    # E2.19 四裁3:無現任艦長=佔位列(「留下位置」明示可指派),不入 rowmap
    # =天然不可點。兩形:全域 no_captain_label(單艦隊舊形,治具相容)/
    # 引擎分組後=agents 內嵌 placeholder 偽列(各引擎組頂各一,採集端排)。
    # 第 49 項:三燈常駐右欄(等人裁/偏了/沒檢查),在清單最上一行;**不看 pending_box_open、不看頁簽**。
    #   字由採集端組好(i18n 在那邊);有任何一燈 >0 用黃色(亮),否則灰;量不到的燈印 ? 並帶具名原因。
    #   缺鍵 = 不畫(黃金幀與舊治具的 render_data 沒有這個鍵,畫面必須與今天相同)。
    _lamps = data.get("lamps")
    if isinstance(_lamps, dict) and _lamps.get("text"):
        _lamps_colour = YELLOW if _lamps.get("lit") else GREY
        emit(_lamps_colour + " " + trunc(str(_lamps["text"]), max(1, width - 1)) + RESET)
    if data.get("no_captain_label"):
        emit(GREY + " " + str(data["no_captain_label"]) + RESET)
        if agents:
            emit("")
    _region_start = len(lines)
    for index, ai in enumerate(agents):
        _cur_agent[0] = index
        if ai.get("placeholder"):
            emit(GREY + " " + str(ai["placeholder"]) + RESET)
            if index < len(agents) - 1 and _agent_level(agents[index + 1]) == 0:
                emit("")
            continue
        window = ai.get("hub_window")
        age = ai.get("age_minutes", 0)
        mark = ai.get("mark") or "👤"
        provider = ai.get("provider")
        engine = ai.get("engine", "--")
        _oa = str(ai.get("opening_attempt_text") or "")
        # 09-14 開席成功五項判準(the maintainer「右方顯示正確的席位資訊=開席成功」):開席中/載入中那一列
        # 畫面只有一行黃字,但 rowmap 要把五項(席碼/角色/引擎模型/人格/席位 id)機器可讀地
        # 帶上,aib 才判得出「右欄畫出的是這一席、而且資訊對」。值全部是採集端算好的,這裡只落檔。
        _seat_fields = {
            "name": str(ai.get("name") or ""),
            "engine": str(engine or "--"),
            "model": ai.get("model") or None,
            "model_requested": ai.get("model_requested") or None,   # 10-05:開席中那列也落要求值,rowmap 與畫面對得了帳
            "role": str(ai.get("role") or ""),
            "persona": str(ai.get("persona_display") or ""),
            "seat": str(ai.get("seat_id") or ""),
        }
        if _oa:
            # O2:有碼表的開席。列上要留席位碼,不得再用 Q 的「載入中」把 @五碼蓋掉。
            # ★ 09-14 pm 18:1x 裁(the maintainer 18:0x「右方顯示正確的席位資訊」=人眼看到):開席中那一列
            #   改成**一行五項**「👑 CLA Opus 5 a seat · 開席中 · 階段」——段位/引擎模型/席碼/(綁了才有)人格,
            #   後面接原本的階段文。缺項印「?」不留空(段位缺=❓ 由採集端給;模型缺=引擎退三字碼)。
            #   這句蓋過先前眼驗的純黃字版。
            _oa_badge = resolve_engine_badge(
                provider, engine, truecolor=data.get("truecolor"),
                mode=data.get("engine_badge_mode", "letters"),
                icon_available=(data.get("engine_icon_available") or {}).get(
                    str(provider or "").strip().lower(), True),
                # 10-05 the maintainer 21:5x「綁定完應該叫完整名稱 musebot 而不只有 MSB」:橋接席沒有 .pct 觀測模型,
                # 要求名就是顯示名(與下面正常列同一條規則),開席中/未確認那一列也不得退回三字碼。
                label_override=(ai.get("model")
                                or ((ai.get("model_requested") or None)
                                    if str(provider or "") in AIBOT_PROVIDERS else None)))
            _oa_name = _seat_fields["name"] or "?"
            _oa_rest = _oa[len(_oa_name):].lstrip() if _oa.startswith(_oa_name) else _oa
            _oa_persona = (" 🎭" + _seat_fields["persona"]) if _seat_fields["persona"] else ""
            _oa_line = "%s %s %s%s · %s" % (mark or "?", _oa_badge["text"], _oa_name, _oa_persona, _oa_rest)
            # the maintainer 19:4x:開席中/載入中卡住要能隨時中斷 ⇒ 列尾掛 [X](點了走「中斷開席」確認,不做閒置重驗)
            _oa_text = _opening_row_with_x(YELLOW + " " + trunc(_oa_line, max(1, width - 1 - _X_CELLS)) + RESET)
            emit(_oa_text, window, dict(_seat_fields, kind="main", label=_oa, status="pending",
                                        engine_badge_mode=_oa_badge["mode"],
                                        engine_badge_fallback=_oa_badge["fallback_reason"],
                                        mark=mark or "?"),
                 kill=_kill_span_at_end(_oa_text))
            if index < len(agents) - 1:
                emit("")
            continue
        # 對齊 the maintainer 08-03 拍板的引擎品牌正本：CL=Anthropic 磚橘，
        # CD=OpenAI 亮灰。只染縮寫，不把後面名稱整行染色。
        # ANSI 不佔寬度，所以截斷與點擊對映不會偏移。
        _avail = (data.get("engine_icon_available") or {})
        _model = ai.get("model") or None       # 採集端已用 model_label.display_model 正規化
        # 09-14:觀測值缺、但開席有要求 ⇒ 畫「要求◌」(三態第三態;圖例同進退,沒圖例就不畫)。
        _req = ai.get("model_requested") or None
        _shown_model = _model
        if _model is None and _req and _legend_req:
            # 橋接席：要求名=顯示名，永不「等待觀測」(無 .pct model)。
            if str(provider or "") in AIBOT_PROVIDERS:
                _shown_model = _req
            else:
                _shown_model = "%s%s" % (_req, MODEL_REQUESTED_MARK)
                _req_marked = True
        _badge = resolve_engine_badge(
            provider, engine, truecolor=data.get("truecolor"),
            mode=data.get("engine_badge_mode", "letters"),
            icon_available=_avail.get(str(provider or "").strip().lower(), True),
            label_override=_shown_model)
        engine_label = _badge["text"]
        # ── L9:觀測 ≠ 要求 的小記號 ────────────────────────────────────────
        # 判定在採集端(model_label.model_drift_state,唯一正本),這裡只收布林。
        # ★ 三個條件缺一不畫,而且**順序就是理由**:
        #   1. 真的是 True —— unknown/None 不畫。「問不出來」不可以畫成「跑錯了」,
        #      現查 114 個席位檔有 62 個根本沒有 model_requested。
        #   2. 有模型名可標 —— 退回三字碼(CLA)時本來就是「模型量不到」,
        #      在量不到的東西上標「跟要求的不一樣」是自相矛盾。
        #   3. 有圖例 —— 沒有人解釋的記號等於雜訊(EH.4 那條的同一半)。
        if ai.get("model_drift") is True and _model and _legend_drift:
            engine_label = "%s%s" % (engine_label, MODEL_DRIFT_MARK)
            _drift_marked = True
        ctx = ai.get("ctx_percent")
        if ctx is None:
            ctx_plain = ctx_txt = "--"        # 無值灰調不上色(第七件規格2)
        elif ai.get("ctx_awaiting"):
            # ★★ 09-07(the maintainer 回報「下方 ctx% 與右方 ctx% 又不一樣了」):
            #   這一席拿不到 by-seat 橋 ⇒ 上面那個 pct 讀的是**席位檔登記 uuid**
            #   那一份,而它**凍在 /clear 或 compact 換掉對話 id 的那一刻**。
            #   實測:57b15 / 405d8 clear 兩小時,statusline 是 0%,右欄卻還畫 72%/68%。
            #   ★ 橋由那一席自己的 statusline 寫,而 statusline 只在渲染時跑 ——
            #     一個安靜的席不會產生新讀數,所以這裡**修不到值,只能修說法**。
            #   ⇒ 不畫那個凍住的數字:它與真值在畫面上逐字相同,而它是騙人的。
            #     畫「待更新」並說得出要做什麼才會變(同一列的 💤 已經在講多久沒動)。
            ctx_plain = ctx_txt = AWAITING_CTX_LABEL
        else:
            _stale = "?" if ai.get("ctx_stale") else ""
            # BD(09-03):↻=這格 CTX 讀自 pane 現任 session(/clear 後 uuid 已換),
            # 不是席位檔登記的那一個;與 ? 同樣落在 RESET 之後不上色。
            _stale += "↻" if ai.get("session_rebound") else ""
            ctx_plain = "%d%%%s" % (ctx, _stale)
            # 數字染色帶;? 落在 RESET 之後=不上色維持灰調
            ctx_txt = ctx_badge(ctx, data.get("truecolor")) + _stale
        # age_minutes 顯式 None = 採集端拿不到真活動訊號。★不得編數字:
        # 舊版此處對無訊號席位印的是「開席到現在」,於是一個正在密集工作的
        # 席位被畫成 💤21h(08-15 the maintainer 實例)。判準與**同一列的 CTX** 對齊——
        # 無值就顯示 `--`,兩欄不再一個誠實一個說謊。
        # 缺鍵仍走 0(治具/黃金相容:`.get(k, 0)` 只在鍵不存在時給 0,
        # 鍵存在而值為 None 時回 None,兩者刻意分開)。
        state = activity_state(age)
        info = "%s %s" % (ctx_txt, state)
        main_name = str(ai.get("name") or "?")
        main_detail = {
            "kind": "main",
            "label": main_name,
            "engine": str(engine or "--"),   # 落檔鎖 letters(採集端給的三字母;不是即時徽章)
            "model": _model,                                # 09-05:畫面上顯示的模型名(None=量不到,畫面退三字碼)
            "engine_badge_mode": _badge["mode"],            # R6:即時畫面實際用的態
            "engine_badge_fallback": _badge["fallback_reason"],  # R6:退回理由(font_missing/None),可數
            "context": ctx_plain,   # rowmap/詳情卡=純文字,不夾 SGR
            "status": state,
            # ★ 2026-08-21:段位/角色圖示一併落進 rowmap。
            #   **加這一欄不是為了 rowmap 自己用,是為了消滅第二份實作。**
            #   標題列(title_beat.py)原本自己判段位,而它是純函數、看不到
            #   別席,所以只能給「保守值」——實測後果:艙裡只有一個 root 時
            #   右欄畫 👑、標題畫 ⚓,**同一席同一瞬間兩個畫面說相反的話**。
            #   ⚓ 的語意是「第二位起」,唯一的艦長戴它不是保守是錯的。
            #   👑 的真規則住在 pane-right-ai.sh(全域唯一、只給第一艦隊
            #   首位、未編制永不戴冠、captain_assigned 單獨不算數),那條
            #   規則不該被抄第二份——本欄讓標題**讀那條規則算出來的結果**,
            #   而不是重算一次。讀真來源,不抄字面。
            "mark": mark,
            # 09-14 開席成功五項判準(the maintainer「右方顯示正確的席位資訊=開席成功」)要從
            # rowmap 機器讀:②角色字面、④人格名(畫面第二行 🎭 那個值,原樣不截)。
            # 兩欄都是採集端算好的值,這裡只落檔,不另算。
            "role": str(ai.get("role") or ""),
            "persona": str(ai.get("persona_display") or ""),
            "model_requested": ai.get("model_requested") or None,   # 09-14:要求值,不冒充觀測值
        }
        # L2-03:綁定入口要知道「哪一席」。seat_id 由 pane-right-ai 從席位檔給
        # (與第一行 @五碼 同源);沒有就沒有入口(見下:_seat 空 → 不發 span)。
        _seat = str(ai.get("seat_id") or "")
        if _seat:
            main_detail["seat"] = _seat
        level = _agent_level(ai)
        # ★ 這一席底下還有沒有更深的一層(=它的成員)。子代理的末位符號、
        #   席位之間的間隔行都要看它,所以只算一次、多處共用。
        _deeper_next = (index + 1 < len(_levels)
                        and _levels[index + 1] > _levels[index])
        # 樹軌:手足 ├/末位 ╰,軌後補 `-`(AR-2);level 0 無軌,只留左緣 1 格。
        _rail = "├" if _sibling_continues(index) else "╰"
        lead = " " if level == 0 else " " * _seat_col(level) + _rail + "- "
        # the maintainer 08-14 活艙眼驗拍板:主 AI 一席一行,永不折行。
        # 關席尾標:idle_kill → 只 [X]；有 💤（含 💤--）未達門檻 → (x)；兩者不並排。
        # the maintainer 2026-09-21。
        xtag, _kill_lab = seat_kill_tail(ai, state)
        _q_items = ai.get("dm_queue") if isinstance(ai.get("dm_queue"), list) else []
        qtag = pending_count_tag(_q_items, bool(data.get("pending_box_open")))
        qtag_plain = strip_ansi(qtag)
        qtag_gap = (" " + qtag) if qtag else ""
        qtag_plain_gap = (" " + qtag_plain) if qtag_plain else ""
        # AR-3:截寬只吃名字;[X]/(x)、百分比、活動符、【待審N】永遠在列尾。
        fixed_width = visible_width("%s%s %s  %s%s%s" %
                                    (lead, mark, engine_label, info,
                                     qtag_plain_gap, xtag))
        # v0.70.0 第 8/9 條:`*`(開席單未回)與「額度用完」是另開欄位 name_suffix,接在名字後面;
        #   先扣掉它的寬度再截名字,後綴永遠看得到。label(rowmap)仍是純 name,不帶後綴。
        _nsuf = str(ai.get("name_suffix") or "")
        if visible_width(_nsuf) < max(1, width - fixed_width):
            name = trunc(main_name, max(1, width - fixed_width - visible_width(_nsuf))) + _nsuf
        else:
            # 第三審 r3:欄寬連後綴都放不下 ⇒ 名字+後綴一起截到可用寬度,不撐爆整列(列尾 [X]/用量要留著)
            name = trunc(main_name + _nsuf, max(1, width - fixed_width))
        text = "%s%s %s %s%s %s%s" % (lead, mark, engine_label, name,
                                      qtag_gap, info, xtag)
        kill_span = None
        if xtag and _kill_lab:
            kill_span = _kill_span_at_end(text, label=_kill_lab)
        emit(text, window, main_detail, kill=kill_span)
        # ── 屬性列(人格 🎭 / 大腦 🧠;設計正本 docs/reports/three-line-rows-20260815/,
        #    AR-3 改版 2026-09-02)
        # ★ 只有主 AI 有這兩行:the maintainer 明令「子代理不綁人格」。不用 level 判——顧問與
        #   組員在 1-2 層但仍是主 AI;子代理走下面 subs 那條路,根本不經過這裡。
        # ★ AR-3 第 2 條「未綁摺一行」:沒綁人格 ⇒ 只畫一行 `╰-▸ 🎭 未綁 [綁定]`
        #   (有子 AI 時 `├-▸`);綁了人格才展開第二行 🧠(大腦未綁 ⇒ `🧠 未綁 [綁大腦]`)。
        #   「未綁」仍**標出來**才知道是「還沒綁」而不是「壞了」(seat-detect.py:76 同一價值觀);
        #   只是不再為它多佔一行。
        # ★ AR-3 第 3 條:兩個動作字面分開——人格行 [解人格]、大腦行 [換大腦];[解綁] 退役。
        #   [換大腦] 走 change-brain-menu(選單第一項固定「解除綁定」→ 既有 unbind-brain 確認,
        #   艦長 a6b2e 09-02 15:3x 裁:解綁入口不得消失)。
        # ★★ 未編制(❓)的席位**不畫屬性列,連入口方框也不畫**(the maintainer 2026-08-16 螢幕裁定):
        #    ❓ 說的是「這一席是誰,驗不出來」,而綁定的前提正是「認得出是誰」。
        #    用 `unassigned` 欄位判,不嗅 ❓ 字元(真相源在 seat_status)。
        _pd = ai.get("persona_display")
        _pf = ai.get("persona_folder")
        _pt = ai.get("project_title")
        subs = ai.get("subs") or []
        _shown = subs[:3]
        _has_overflow = len(subs) > 3
        _ub = _under_base(index, level)
        _attr_rows = []   # (icon, text, is_path, token, action)
        # `aib ai role/sb on|off`(ai_view)兩個開關各自獨立:role 關只收人格列,sb 關只收大腦列。
        # AR 摺行規則(the maintainer 09-02 裁):沒人格時只有一行「🎭 未綁 [綁定]」,大腦列要綁了人格才展開——
        # 所以 role 關且人格未綁 ⇒ 這一席底下沒有屬性列(不是缺陷,是摺行)。
        if not ai.get("unassigned"):
            if _pd:
                if _show_role:
                    # EH.4 三態標記:機器新建的薄卡前綴 `~`,且只在圖例注入時標
                    # (標記與解釋它的圖例必須同時上畫面);只標 created 不標 human——
                    # persona_source 可以不存在,不存在的語意是「未標示」。
                    _src = ai.get("persona_source")
                    _pd_marked = (("~%s" % _pd) if (_src == "created" and _legend_created)
                                  else _pd)
                    _tok = "[%s]" % _labels["persona_unbind"]
                    _attr_rows.append((ATTR_ICONS["persona"], "%s %s" % (_pd_marked, _tok),
                                       False, _tok, "unbind-persona"))
                if _show_sb:
                    if _pt:
                        # 09-29 a seat:這一席綁了專案卡 ⇒ 這一行畫專案標題(R16:專案 = 第二大腦)。
                        #   標題不是路徑 ⇒ 太長時從尾端讓位(與人格名同一條規則),[換專案] 留著。
                        #   同時也綁了路由資料夾時以專案卡為準;資料夾仍在 [換專案] 的選單裡換得到。
                        _tok = "[%s]" % _labels["brain_change"]
                        _attr_rows.append((ATTR_ICONS["brain"], "%s %s" % (_pt, _tok),
                                           False, _tok, "change-brain-menu"))
                    elif _pf:
                        _tok = "[%s]" % _labels["brain_change"]
                        _attr_rows.append((ATTR_ICONS["brain"], "%s %s" % (_pf, _tok),
                                           True, _tok, "change-brain-menu"))
                    else:
                        _tok = "[%s]" % _labels["brain_bind"]
                        _attr_rows.append((ATTR_ICONS["brain"], "%s %s" % (_labels["unbound"], _tok),
                                           False, _tok, "bind-brain-menu"))
            elif _show_role:
                _tok = "[%s]" % _labels["persona_bind"]
                _attr_rows.append((ATTR_ICONS["persona"], "%s %s" % (_labels["unbound"], _tok),
                                   False, _tok, "bind-persona-menu"))
        for _ri, (_icon, _txt, _is_path, _tok, _action) in enumerate(_attr_rows):
            # 軌:這一席底下還有東西(下一個屬性列或子 AI)⇒ ├,否則 ╰。
            _arail = "├" if (_ri < len(_attr_rows) - 1 or _shown) else "╰"
            _pre = "%s%s-▸ %s " % (_ub, _arail, _icon)
            _avail = max(1, width - visible_width(_pre))
            if visible_width(_txt) > _avail:
                if _is_path:
                    # 路徑從**前面**截:尾端才認得出是哪張卡(設計正本實測,從後面截
                    # 十七席看起來一模一樣);動作字面在尾端一併保住。
                    while _txt and visible_width("…" + _txt) > _avail:
                        _txt = _txt[1:]
                    _txt = "…" + _txt
                else:
                    # ★ AR-3 第 4 條同一精神用在屬性列:讓位的是**名字**,尾端的動作字面
                    #   ([解人格]/[綁定]…)是這一行唯一可點的東西,不得先死。
                    #   預設寬 27 下「第二艦長 [解人格]」正好差 1 格——舊式從尾端截會
                    #   截成 `[解人…`,入口跟著消失(09-02 三案例實渲染看到)。
                    _body = _txt[:-len(_tok)].rstrip()
                    _txt = "%s %s" % (trunc(_body, max(1, _avail - visible_width(_tok) - 1)), _tok)
                    if visible_width(_txt) > _avail and visible_width(_tok) <= _avail:
                        # 10-17 [換第二大腦(專案)] 18 格:預設寬下連一個字的名字都擠不下 ⇒ 名字整個讓位,只留入口
                        _txt = _tok
                    if visible_width(_txt) > _avail:
                        # 連字面本身都放不下:回到尾端截,入口誠實消失
                        # (span 由下面 _tail_span 從**同一行**算,不會指到被檔尾截寬吃掉的位置)。
                        _txt = trunc(_txt, _avail)
            _line = _pre + _txt
            # L2-03:尾端方框=點擊區(rowmap spans,與 [X] 同機制,同一次渲染算出);
            # 被截掉就沒有入口,不硬給。
            _sp = _tail_span(_line, _tok, _action, _seat)
            emit(_line, window, main_detail, spans=[_sp] if _sp else None)
        # ── 子 AI 列:`├--⚙`/`╰--⚙`(AR:彎角落在該席自己屬性列的同一欄,09-02
        #    「子 AI 鏈結跟隨主 AI」裁定;虛線=附屬物,08-21 三輪裁定保留)
        for _si, sub in enumerate(_shown):
            sage = sub.get("age_minutes", 0)
            dot, activity = child_activity(sage, sub.get("activity"))
            sub_uuid = str(sub.get("uuid") or sub.get("id") or "?")
            # the maintainer 08-14 二裁:顏色即狀態——列上只留 圓點+uuid末五/任務+運作時間;
            # 狀態詞與任務描述進詳情卡。
            child_label = trunc(str(sub.get("label") or sub_uuid[-5:]),
                                max(1, width - 15 - 2 * level))
            child_detail = {
                "kind": "child",
                "label": child_label,
                "engine": str(engine or "--"),
                "context": ("%s · %s" % (main_name, sub.get("desc"))
                            if sub.get("desc") else "parent %s" % main_name),
                "status": "%s %s" % (activity, compact_age(sage)),
            }
            # ★★ 「末位」= 這一欄底下真的沒有東西了;溢出列自己是視覺末位。
            # ★ the maintainer 09-30 15:4x 裁:一個子 AI ⇒ ╰--⚙;兩個以上 ⇒ 前面 ├--⚙、最下面 ╰--⚙,
            #   **艦長底下有成員也一樣**。舊規則(工單 AR 不變量 5)在有成員時硬畫 ├,
            #   但 09-02 AR-3 之後艦長的子 AI 在 _attr_col(0)=col3、成員主幹在 col1,兩欄不同 ⇒
            #   col3 的 ├ 底下什麼都沒接,是懸空的線(the maintainer 截圖指出)。樹不斷的責任在 col1 主幹。
            # ★ 成員層(level≥1)不變:它的子 AI 欄 _attr_col(L) == 下一層的席列軌欄 _seat_col(L+1),
            #   底下有組員時那一欄真的往下續,├ 是對的。
            _crail = "╰" if (_si == len(_shown) - 1
                             and not _has_overflow
                             and (level == 0 or not _deeper_next)) else "├"
            _sub_prefix = "%s%s--⚙" % (_ub, _crail)
            # ★ 子代理的活動符號跟著**它自己的圓點**走(SUB_FRESH_S 120 秒),不另立門檻。
            _sub_state = "%s%s" % ("🔧" if activity == "工作" else "💤",
                                   compact_age(sage))
            emit("%s %s %s %s" %
                 (_sub_prefix, dot, child_label, _sub_state), window,
                 child_detail)
        if _has_overflow:
            # 超出上限誠實顯示 +N,不靜默截斷;列點=跳主席;溢出列=視覺末位,掛 ╰
            emit("%s╰--⚙ +%d" % (_ub, len(subs) - 3), window, {
                "kind": "child", "label": "+%d" % (len(subs) - 3),
                "engine": str(engine or "--"),
                "context": "parent %s" % main_name,
                "status": "overflow",
            })
        # 待審改畫在對話區底下的 Ez2 待審框,右欄艦隊列表不再畫暫存列。
        # 間隔行用 main 的 _spacer(接脊柱)。
        if index < len(agents) - 1:
            emit(_spacer(index, level, _deeper_next))
    if not agents:
        emit(GREY + " (無活動)" + RESET)
    emit("")
    # the maintainer 09-12 15:3x 令:用量塊從這裡搬到右欄第 2 頁「AI用量」置頂(render_usage_view_with_map),
    _cur_agent[0] = None
    _region_end = len(lines)
    # 1.AI 這頁不再畫 —— 席位樹多出的高度就是搬家的理由。data['usage'] 在這條路上刻意不讀:
    # 帶不帶 usage,本函式的畫面與 rowmap 逐位元組相同(tests/test-usage-panel.py 釘著)。
    # ★ EH.4 圖例(the maintainer 08-22 眼驗選丁,四塊真輸出對照後):四項**重新分配成兩行**。
    #   量出來的理由,不是排版偏好:
    #     單行四項 visible_width = 38,而可用寬 = 面板寬 − 1、預設 DEFAULT_PANE_W=28 → 27(09-02 前 27 → 26)。
    #     **原本三項那一行就已經是 27 > 26,今天就在截**;再塞第四項只會讓
    #     新加的那一項**第一個被截掉**(行尾先死),等於加了沒加,而且靜默。
    #   兩行寬度:第一行 16、第二行 21 → 33/27/24 欄全完整(20 欄第二行仍截,已知)。
    # ── 圖例:一行(the maintainer 2026-09-04 令:併成一行,不顯示 CTX=實測)───────────
    # ★ 拿掉的是 `CTX=實測`:那一格的值本來就寫在每一列的 `%` 後面,
    #   圖例再解釋一次「沒有問號就是實測」是把預設狀態也拿出來講,佔一行不划算。
    # ★ **保住原本的不變式**:標記與解釋它的圖例必須同時上畫面。所以下面每一段
    #   都維持「有注入才加、有標記才加」——合併成一行不等於無條件全印。
    #   漏掉這一點的話,`~` 與 `*` 會出現在列上而沒有人知道它是什麼意思。
    # ★ 超寬由本函式尾端既有的截寬處理(rowmap 索引行號,截寬不動行號);
    #   窄面板會先截掉行尾那幾項 —— 這是既有行為,合併只是讓它從「少一行」
    #   變成「少幾個字」,而**兩者都是靜默的**,已知未修。
    # ★★ 順序不是排版偏好,是**不變式的落地**:併成一行之後,超寬時被截掉的是**行尾**,
    #   所以「有標記就必須有解釋」的那幾項要排在**最前面**。
    #   實測(tests/test-pane-right-ai.py::test_marked_and_explained_travel_together):
    #   把 `?=過期` 排在最前面時,W=21 會把 `~=機器新建` 截掉,而列上那個 `~` 還在
    #   ⇒ 畫面出現一個沒有人解釋的符號,**而且不會有任何一格紅**(那支臂就是為此存在的)。
    #   `?=過期` 與 `❓=未編制` 排最後:它們解釋的是常見狀態,截掉的代價小。
    _lg_parts = []
    _lg = _legend_created
    # 呼叫端注入,渲染器不自己讀 i18n(本檔既有紀律:不讀環境,黃金幀決定性)
    if _lg:
        _lg_parts.append(_lg)
    # L9 `*`:與 `~` 同一群(有標記就必須有解釋)⇒ 排在 `?=過期` 之前,
    # 窄面板截行尾時最後才輪到它們。條件是**真的有列標了**,不是「有圖例文字」。
    if _legend_drift and _drift_marked:
        _lg_parts.append(_legend_drift)
    if _legend_req and _req_marked:
        _lg_parts.append(_legend_req)
    _lg_ob = (data.get("legend") or {}).get("onboard_pending")
    if _lg_ob and data.get("onboard_marked"):
        _lg_parts.append(_lg_ob)
    # ★ `❓=未編制` 已由 the maintainer 2026-09-04 令移除。留一句記錄它的代價,不是反對:
    #   ❓ 這個標記**仍然會出現在列上**,而現在畫面上沒有東西解釋它。
    #   `~` 與 `*` 有「標記與圖例同進退」的臂守著(所以它們仍是條件式的);
    #   ❓ 沒有那支臂 —— 也就是說這個取捨不會有任何一格轉紅,只會在某天有人問
    #   「這個問號是什麼意思」。寫在這裡,讓那一天有出處可查。
    _lg_parts.append("?=過期")
    # ★ 置中(the maintainer 2026-09-04 令)。用 visible_width 不用 len():CJK 一個字兩格,
    #   用 len() 算出來的 pad 只有一半,結果就是「看起來偏左」而不是報錯。
    _lg_text = "  ".join(_lg_parts)
    # ★★ 置中要先擋住「探寬度」那條路:`required_ai_pane_width()` 用
    #   `width=sys.maxsize` 呼叫本函式去量自然寬度(見該函式)。不擋的話
    #   `(maxsize - 文字寬)//2` 會生出 4.6×10^18 個空白 —— 渲染當場死,
    #   而右欄畫的是「清單暫時無法取得」,**看起來像資料來源壞了,不像剛改的這一行**。
    #   ★ 用小寬度直接測 render_ai_list_with_map 是綠的:那條路徑根本沒被走到。
    #   ★ 順帶:填充本來就不該計入自然寬度,所以探寬度時不填充是對的,不只是防呆。
    _lg_pad = 0
    if width <= MAX_SANE_PANE_W:
        _lg_pad = (width - visible_width(_lg_text)) // 2
    emit(DIM + (" " * _lg_pad if _lg_pad > 0 else " ") + _lg_text + RESET)
    if data.get("label_collision"):
        emit(DIM + " !=UUID末五碼衝突" + RESET)
    # E2.11 codex 審「高-3」(2026-08-22):width 要是硬保證。任何一行超寬都在這裡截到 width
    # (rowmap 索引的是行號,截寬不動行號,畫面與落點仍同源);下游(pane-fit)就不需要再裁,
    # 再裁才是「畫面與 rowmap 不再同源」的來源。正常資料這一行是 no-op——golden 幀不變。
    lines = [trunc(ln, width) if visible_width(ln) > width else ln for ln in lines]
    # 274 右欄艦隊收合 + 視窗:只在採集端注入 data["right_fold"](enabled True)時才動;
    #   沒注入 ⇒ 下面這一行是 no-op,畫面與 rowmap 逐位元組不變((e)7 開關關=今天的畫面)。
    #   放在圖例之後:H 要扣掉席位樹以外的每一列,圖例也在其中。
    lines, rowmap = apply_right_fold(lines, rowmap, _owners, agents, data.get("right_fold"),
                                     width, _region_start, _region_end)
    return lines, rowmap


def _pane_floor(natural):
    """右欄「該多寬」的**唯一**下限來源:自然寬 + pane-fit 保留的一格,最低 24。

    ★ 為什麼要有這一支(2026-09-12 live38):`required_ai_pane_width` 與
    `required_usage_pane_width` 原本**各自寫一份逐字相同的下限式**(就是本支最後那一行)。
    ★★ 這段說明刻意**不把那一行的字面抄進來** —— 抄了它就會被下面第 2 點那把刀的計數看見
    (那把刀數的是整份原文、**不剝註解與 docstring**),而「說明裡多一份」與「碼裡多一份」
    在它的計數上長得一模一樣。
    兩份的後果有兩層:
      1. `.right-pane-required-width` 這一個發布值變成**兩個生產者**,而消費端只有一個;
      2. `tests/test-cockpit-resize-b1-acceptance.py::test_99` 那把還原刀**用這一行的字面當錨**
         並斷言「恰好出現一次」⇒ 第二份一出現,斷言先擋、**刀根本沒落**(pass1/pass2 同紅,候選不上 main)。
    ⇒ 兩個頁簽的下限都走這一支;錨回到一次,而且刀落下來時**同時蓋住兩個生產者**。
    """
    return max(24, natural + 1)


def required_ai_pane_width(data):
    """回右欄完整畫面自然寬 + pane-fit 保留的一格,最低 24。

    用同一支 render_ai_list_with_map 在不受限寬度下實際排一次,所以身份、
    CTX、活動狀態、尾標與多位元組字寬都只由真畫面導出。sys.maxsize 不是
    版面常數,只是表達「本次不要截斷」;回值仍只取實際輸出的最長列。
    """
    lines, _rowmap = render_ai_list_with_map(data, width=sys.maxsize)
    natural = max([visible_width(line) for line in lines] or [0])
    return _pane_floor(natural)


def render_usage_view_with_map(data, width=DEFAULT_PANE_W - 1):
    """右欄第 2 頁「AI用量」(the maintainer 09-12 15:3x 令:用量從 1.AI 頁尾搬到自己的頁、置頂;
    舊的沙盒任務段收掉)。

    用量塊本身一行不改 —— 走同一支 _emit_usage_block,列上 LOGO/[重驗] 的 link spans
    與 1.AI 時代同源同形(rowmap 只有 links 條目、無 window),hit_test 兩個 verb 照認。
    ★ 一列都畫不出來時**不留白頁**:印 data['usage']['empty_text'](採集端配 i18n 注入,
    渲染器不讀字典)。白頁與「這一格壞了」在畫面上逐字相同,而用量頁沒有別的內容可以
    證明渲染器還活著。缺 empty_text(舊治具/舊採集端)印 '?',不猜字。
    """
    data = clean_data(data)
    rowmap = []
    lines = []

    def emit(line, window=None, detail=None, kill=None, spans=None, links=None):
        if links:
            rowmap.append({"line": len(lines), "links": [dict(s) for s in links]})
        lines.append(line)

    emit("")   # 頁簽列與標題之間的分隔,與 1.AI 頁「狀態列與第一席之間空一行」同形
    before = len(lines)
    body_at = {"n": None}

    def _mark_body():
        body_at["n"] = len(lines)

    _emit_usage_block(data, emit, width, on_body=_mark_body)
    render_usage_view_with_map.body_start = body_at["n"]
    if len(lines) == before:
        block = data.get("usage")
        text = str((block or {}).get("empty_text") or "?") if isinstance(block, dict) else "?"
        emit(DIM + " " + text + RESET)
    # 箭頭不畫在這裡。收起時由 fit_usage_viewport 放在捲軸最上、最下。
    lines = [trunc(ln, width) if visible_width(ln) > width else ln for ln in lines]
    return lines, rowmap


# 一顆實心三角。這台會把它畫成兩格寬。不要再拼成三顆。
USAGE_BAR_UP = "\u25b2"
USAGE_BAR_DOWN = "\u25bc"
USAGE_ARROW_COLS = 2
USAGE_ARROW_STEP = 3
USAGE_ARROW_ON = "\x1b[97m"
USAGE_ARROW_END = "\x1b[93m"


def _usage_arrow(kind, bright):
    """一列、一顆。能動是亮白。到頂或到底都變黃。"""
    color = USAGE_ARROW_ON if bright else USAGE_ARROW_END
    tri = USAGE_BAR_UP if kind == "up" else USAGE_BAR_DOWN
    return color + tri + RESET


def _clip_entry_before_bar(entry, bar_col):
    """按鈕的可點範圍停在捲軸左邊。捲軸那一欄只負責捲，不再刪帳或切帳。"""
    links = entry.get("links")
    if not isinstance(links, list):
        return entry
    kept = []
    for link in links:
        if not isinstance(link, dict):
            continue
        try:
            start, end = int(link["start"]), int(link["end"])
        except (TypeError, ValueError, KeyError):
            continue
        if end > bar_col:
            end = bar_col
        if start < end:
            clipped = dict(link)
            clipped["start"] = start
            clipped["end"] = end
            kept.append(clipped)
    out = dict(entry)
    if kept:
        out["links"] = kept
    else:
        out.pop("links", None)
    return out


def fit_usage_viewport(lines, rowmap, *, header_rows, total_rows, offset, width,
                       bar_arrows=False, arrow_tab="usage"):
    """用量頁：標題留著，下面的帳號列捲動，最右一欄是捲軸。

    裝得下就不畫捲軸。列上的按鈕跟著可見的那一列走。
    total_rows 是這一頁能畫的列數（已扣掉上方分頁列）。
    bar_arrows：不畫捲軸。最上、最下各一顆三角。到頂、到底都變黃。
    點一下走三列，才不會一下跳到頭。
    """
    lines = list(lines or [])
    rowmap = list(rowmap or [])
    try:
        header_rows = int(header_rows)
        total_rows = int(total_rows)
        width = int(width)
    except (TypeError, ValueError):
        return lines, rowmap
    if header_rows < 0 or header_rows > len(lines) or total_rows < 1 or width < 2:
        return lines, rowmap
    body = lines[header_rows:]
    room = total_rows - header_rows
    want_arrows = bool(bar_arrows) and room >= 2 and width <= MAX_SANE_PANE_W
    if room < 1 or (len(body) <= room and not want_arrows):
        return lines, rowmap
    try:
        from right_fold_api import viewport
    except ImportError:
        from render.right_fold_api import viewport
    if len(body) <= room:
        start = 0
        window = list(body) + [""] * (room - len(body))
        bar = []
    else:
        start = viewport.clamp(offset, len(body), room)
        window = body[start:start + room]
        # 有箭頭就不畫捲軸。1.AI 沒傳這個旗標，捲軸維持原樣。
        bar = [] if want_arrows else (
            viewport.scrollbar_glyphs(start, len(body), room, cell=True) if room >= 3 else [])
    # 捲軸是一格反白空格，不是方塊字。方塊字在這台終端機上會被畫成兩格，把整列擠換行。
    at_top = start == 0
    at_bottom = start + room >= len(body)
    # 箭頭貼這一頁的右上角和右下角，不貼在帳號區的第一列。
    # 用量頁的標題和數字在上面，帳號在下面；貼在帳號第一列會落在半腰。
    arrow_cols = USAGE_ARROW_COLS if want_arrows and width >= 4 else 0
    bar_cols = 1 if bar else 0
    if bar and width <= MAX_SANE_PANE_W:
        window = [fit(row, max(1, width - bar_cols)) + bar[i] for i, row in enumerate(window)]
    out = lines[:header_rows] + window
    mapped = []
    for entry in rowmap:
        if not isinstance(entry, dict):
            continue
        try:
            li = int(entry.get("line"))
        except (TypeError, ValueError):
            continue
        if li < header_rows:
            if arrow_cols and li in (0, len(out) - 1):
                entry = _clip_entry_before_bar(dict(entry), width - arrow_cols)
            mapped.append(entry)
            continue
        bi = li - header_rows
        if start <= bi < start + room:
            moved = dict(entry)
            moved["line"] = header_rows + (bi - start)
            if bar:
                moved = _clip_entry_before_bar(moved, max(1, width - bar_cols))
            elif arrow_cols and (header_rows + (bi - start)) in (0, len(out) - 1):
                moved = _clip_entry_before_bar(moved, width - arrow_cols)
            mapped.append(moved)
    if arrow_cols and out:
        gutter = width - arrow_cols
        out[0] = fit(out[0], gutter) + _usage_arrow("up", not at_top)
        if len(out) > 1:
            out[-1] = fit(out[-1], gutter) + _usage_arrow("down", not at_bottom)
        arrow_col = width - arrow_cols
        pull = USAGE_ARROW_STEP
        tab_name = arrow_tab if arrow_tab in ("ai", "usage", "left") else "usage"   # left = 3.離艙(09-30)
        for line_no, direction in ((0, "up"), (len(out) - 1, "down")):
            mapped.append({
                "line": line_no,
                "usage_scroll": direction,
                "scroll_tab": tab_name,
                "rows": pull,
                "start": arrow_col,
                "end": width,
            })
    elif bar:
        thumb = next((i for i, glyph in enumerate(bar) if "\033[7m" in glyph), 0)
        bar_col = max(0, width - bar_cols)
        for i, glyph in enumerate(bar):
            if "\033[" not in glyph:
                continue
            zone = "thumb" if "\033[7m" in glyph else ("up" if i < thumb else "down")
            mapped.append({
                "line": header_rows + i,
                "bar": {"col": bar_col, "zone": zone, "page": room},
            })
    return out, mapped


def required_usage_pane_width(data):
    """用量頁的自然寬 + pane-fit 保留的一格,最低 24。

    ★ 下限與 `required_ai_pane_width` 走**同一支** `_pane_floor` —— 那句「同一把尺」以前只寫在
    這行 docstring 裡,而碼是兩份逐字相同的複本(live38 就是被那第二份弄紅的)。
    """
    lines, _rowmap = render_usage_view_with_map(data, width=sys.maxsize)
    natural = max([visible_width(line) for line in lines] or [0])
    return _pane_floor(natural)


def render_ai_list(data, width=DEFAULT_PANE_W - 1):
    """主AI+子AI 合併看板(對應舊 demo 的 right-ai 渲染段)。

    只是 render_ai_list_with_map 的畫面部分 —— 刻意不另寫一份迴圈,
    否則行對映與畫面遲早錯開。"""
    lines, _ = render_ai_list_with_map(data, width)
    return lines


RENDERERS = {
    "persona": render_persona,
    "brain": render_brain,
    "ai-list": render_ai_list,
}

