#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""輸入框真實狀態 — 分辨「CLI 的下一步建議」與「人真的打了字」(the maintainer 08-18 直令)。

## 病根:CLI 會在空的輸入框裡預先顯示一則下一步建議

那不是殘影、不是過期渲染、也不是壞掉——**它一直都在**。但它在畫面上與人打的字
長得一模一樣,於是整條鏈把它當成「有未送出的草稿」:

    dm-ctl 卡稿守衛   擋掉了艦外五道令,還擋掉 the maintainer 一道 34 分鐘沒送到 dev2 的指令
    classify_box     判成 human_draft + never_press = 永久卡死
    pulse_check      建議文字不會變、CTX 也不動 → 永遠「無反應」

## 判準:SGR 2(dim/faint),一次就準,而且完全不侵入

`capture-pane -e` 保留跳脫碼。the maintainer 親跑的機器證據(08-18 16:51),兩端都驗過:

    建議(該 NO)  '\\x1b[39m❯\\xa0\\x1b[2mW2 開始審\\x1b[0m'          ← 整段包在 ESC[2m
    真稿(該 YES)  '\\x1b[38;5;246m❯\\xa0\\x1b[39mZZ'                  ← 不帶 dim
    空框            '\\x1b[39m❯\\xa0'  /  '\\x1b[38;5;246m❯\\xa0\\x1b[39m'

    → 兩態:**dim = 建議 = 視同空框**(不是卡稿、不是無反應、不擋 dm);
            **非 dim = 真稿**,維持 never_press。

★ 被作廢的前一版設計(留著當記錄):打一個字看回聲的 X 探針。它侵入、會吃掉別人的字、
  還得重複取樣才敢下判斷——而 dim 判定讀一次就準且不碰對方的框。實作它的過程中我
  真的在 dev4@10 的框裡留下過一個 X。**一個要靠重複取樣才敢相信的判準,通常表示
  判準本身選錯了層。**

★ 凍住**不要再用框判**。框只回答「這裡有沒有人打的字」;席位有沒有死要用行程/CPU/
  transcript(activity_probe)去問。今天艦上三個被判卡住的席位(dev2@7、dev4@10、
  qa@6)全部都是建議文字,**沒有任何一個確認凍住的樣本**。

★★ 08-19 23:2x **更正本檔四處「呼叫端 fail-closed」的宣稱——我實測推翻了它。**
   當回合輸出(消費端 `tools/cockpit-ops/aib-dm.sh`,DRAFT_RE=`(^|│)[[:space:]]*[>❯][[:space:]]+[^[:space:]]`):
     模態且定不出輸入框(本模組回 unknown/None)→ fallback 樣式 **不命中 → 放行**
     校準端 真稿 → 命中(擋)  /  空框 → 不命中(放行)   ← 樣式本身有鑑別力
   即:`unknown` 在 shell 端走的是 `*)` 分支 → `_AIB_DM_FALLBACK_GUARD=1` → 舊樣式比對,
   **比不中就送出**。所以本模組回 None 只保證「我不猜」,**不保證下游會擋**。
   ★ 這是本檔第二處「帶著假理由的說明」(第一處是不可達的 MODAL 分支)。修法在
     aib-dm.sh(刀 M 寫入面,不是我的),本檔只把話改成真的:**回 None = 不猜;
     擋不擋由呼叫端決定,而目前的呼叫端會退回較弱的樣式守衛。**

★ 讀**最後一個** ❯ 行,不是第一個:pane 裡常有兩行以 ❯ 開頭,一行是捲軸上的舊回顯、
  一行才是輸入框。讀到舊回顯就是讀一張靜態舊畫面,它當然「怎麼看都不變」——
  qa 一度被誤判成真凍住就是這麼來的。
"""

import os
import re

from composer_dialect import composer_kind, prompt_chars, browse_footer, busy_markers, cursor_line, cursor_stop
from composer_dialect import empty_placeholder_marks
from composer_dialect import input_repl
from composer_dialect import history_border

EMPTY = "empty"            # 框裡沒有內容
SUGGESTION = "suggestion"  # CLI 下一步建議(dim)——視同空框
DRAFT = "draft"            # 人真的打了字(非 dim)——never_press
MODAL = "modal"            # CLI 的模態選單(選項列+確認提示)——擋,但理由不同
BROWSE = "browse"          # 第 55 項:TUI 瀏覽模式(grok 底列 ←:collapse / Enter:open),輸入行不在畫面上——擋,理由是「不是卡稿,回輸入列即可重送」
BROWSE_TAIL_LINES = 3      # 只認尾端幾個非空行的底列;引文區裡的 collapse 字樣不算
HISTORY = "history"        # 10-14 K8:Claude 輸入框在瀏覽輸入歷史(頂線 History n/m),框裡是舊訊息不是草稿——擋,理由是「不是卡稿,回到空框即可重送」
NOT_APPLICABLE = "n/a"     # 這一格本來就沒有輸入框(watch/純 shell)——放行
# ★★ EH.16:`n/a` 與 `unknown` 的**處置相反**(一個放行、一個擋),所以它
#   必須要有**正面證據**,不能靠「畫面上沒看到提示符」推出來:重繪的瞬間、
#   捲動、視窗太窄,都會讓一個真的有 composer 的格子暫時沒有提示符。
#   拿「沒看到」當「沒有」= **失效值等於表示安全的那個值**。
#   → 判準兩個條件同時成立才給 n/a:呼叫端指名的 pane 指令在下面這張白名單裡,
#     **而且**整幅畫面找不到提示符。少任何一個都回 unknown。
# ★ 為什麼 pane 指令是可信的正面證據:tmux 的 `#{pane_current_command}` 追的是
#   **前景**行程 —— 底下真的跑起 claude/codex 時它報的是那個,不是 shell。
NO_COMPOSER_COMMANDS = frozenset((
    "sh", "bash", "zsh", "fish", "dash",
    "watch", "tail", "less", "more", "cat", "top", "htop", "man",
))

# ★ 08-19 加第三態的理由(艦長 23:11 待辦「落點要在必經之路上」):
#   模態選單的選項行本身以 `❯ 1. …` 開頭且不帶 dim,於是舊判準判成 DRAFT。
#   **擋是對的**(裸 Enter 會替對方選一個選項,例如「Stop and wait for limit
#   to reset」= 替它做帳務決定),**但理由錯**:守衛的訊息叫送件人「請對方清稿
#   後重送」,而對方**根本沒有稿可清**——它需要的是有人回答那個選單。
#   當日實測:08-19 守衛開火 21 筆,模態佔多數(@2/@7/@11),佇列態 0 筆。
#   ★ 本模組只負責**把狀態說對**;守衛訊息的措辭在 aib-dm.sh(刀 M 的寫入面)。
# ★★ 判準用**位置**不用字串:只看輸入框所在的尾端區塊。今晚有人把模態三行
#   逐字廣播出去,於是每個收到廣播的窗畫面上都有那些字——**掃全畫面會把
#   「講那件事的話」判成那件事**(08-19 04:2x 全艦實證)。
_MODAL_OPTION = re.compile(r"^\s*❯?\s*\d+\.\s+\S")
_MODAL_CONFIRM = re.compile(r"Enter to confirm|Esc to cancel")

# ★ 只認 ❯:把 '>' 也算進來會讓畫面上任何以 > 開頭的輸出行(引文、diff、日誌)
#   被當成輸入框——定位器一寬,後面每一層判斷都建立在錯的那一行上。
#   舊的 DRAFT_RE 收 '>' 是因為它還有別的約束綁著;這裡的判準只靠這一行,不能鬆。
# ★ 08-22 加 '›'(U+203A):codex CLI 的提示符。加它與當初拒收 '>' 不同——
#   '>' 出現在引文/diff/日誌是常態,'›' 是排版用字元,而且定位器只取畫面尾端的
#   最後一個,不掃全畫面。加它之前 codex 席一律 (None, None) → unknown,後果有四:
#   卡稿守衛對每個 codex 席退回舊樣式比對、unknown 成因表少記一種、
#   `AIB_DM_STRICT_UNKNOWN` 的翻轉條件(unknown 計數歸零)**只要艦上有 codex 席就永遠到不了**、
#   自動補 Enter 對 codex 完全不觸發——而 codex 正是它要救的那一席。(量測 a seat 01:10)
PROMPT_CHARS = prompt_chars()
COMPOSER_TAIL_LINES = 8    # 輸入框必須落在畫面尾端這幾行非空行之內
CURSOR_WRAP_ROWS = 12      # R7:游標行沒提示符時,往上找折行提示符最多幾列
_SGR = re.compile(r"\x1b\[([0-9;]*)m")
_NBSP = "\xa0"
_COPILOT_BOX_TOP = "\u257b"
_COPILOT_BOX_LEFT = "\u2503"
_COPILOT_BOX_BOTTOM = "\u2579"
_COPILOT_MODAL = re.compile(
    r"Do you want to run this command\?|Requesting your permission:|"
    r"Allow access to this file\?",
    re.IGNORECASE,
)


def strip_sgr(text):
    return _SGR.sub("", text or "")


# ── 第 54 項 b:純顏色差不是「還在動」──────────────────────────────────────────
# seat_is_hold 抓兩幀、不同就判 moving(還在動 ⇒ 忙 ⇒ 信不投)。兩幀用 `capture-pane -e` 帶跳脫碼比對,
# 於是**游標閃爍(反白)、狀態列變色、輸入框邊框由灰轉亮**這種只有 SGR 變的幀也算「動」,
# 一席什麼都沒做也永遠忙(顧問 09-12 實測;既有尺用靜態樣本量不到這件事)。
# 判準:剝掉**所有** CSI/OSC 跳脫碼與尾端空白後比可見文字;文字一樣 ⇒ 沒動(純裝飾差),文字不同 ⇒ 動。
# 失效方向:剝得太多會把「真的在生成」看成沒動 ⇒ 貼進正在跑的框(疊稿);所以**只剝跳脫碼與行尾空白**,
# 不剝任何可見字元(轉圈符號、串流出來的字都算動)。
_ANY_CSI = re.compile(r"\x1b\[[0-9;?<>=!]*[ -/]*[@-~]")   # CSI:SGR、游標、清除…全部
_ANY_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")   # OSC(標題等)
_ANY_ESC = re.compile(r"\x1b[()][0-9A-Za-z]|\x1b[=>78cDEHM]")   # 字元集/鍵盤模式/存取游標等單字元序列


def visible_text(screen):
    """畫面 → 只剩可見字元(逐行去行尾空白,保留行序與行數)。純函式。"""
    text = _ANY_OSC.sub("", screen or "")
    text = _ANY_CSI.sub("", text)
    text = _ANY_ESC.sub("", text)
    return "\n".join(ln.rstrip() for ln in text.split("\n")).rstrip("\n")


def frames_differ(first, second):
    """兩幀之間**可見文字**有沒有變。純顏色/游標/邊框色的差 ⇒ False(沒動);任何可見字元變了 ⇒ True。
    None 當空幀。"""
    return visible_text(first) != visible_text(second)


def _provider_prompt_chars(provider):
    return prompt_chars(provider) if provider is not None else PROMPT_CHARS


COPILOT_BOX_SHORT_ROWS = 12   # 舊判準的框高上限(top→bottom 距離);這以內的框逐字照舊判
COPILOT_BOX_MAX_ROWS = 200    # 10-01:長稿撐高的框最多認到這麼高(比任何實際窗格都高;框頂捲出畫面就定不出,照舊 unknown)


def find_copilot_box(screen):
    """Return the last complete Copilot input box at the screen tail.

    ★ 10-01(a seat copilot 現量,3060/7932 位元組單行稿):一行長稿會把輸入框撐成十幾、
      幾十列。舊判準要求框頂與框底**都**在尾端 8 個非空行內、而且框高 ≤ 11 列 ——
      內容超過 5 列的框就定不出來 ⇒ composer_state 回 unknown ⇒
      (a) 貼後「框裡是不是我的稿」量不到、(b) Enter 後「框清了沒」也量不到,
      兩件事一起塌成「不可判」。
      新判準:**框底**在尾端(框頂可以在上面);框高超過舊上限時,中間每一列都必須以 `┃`
      開頭才認(高框的正面證據要更多,不靠「上面某處有一個框頂字」)。舊上限以內的框逐字照舊。
    """
    lines = (screen or "").splitlines()
    plain = [strip_sgr(ln).replace(_NBSP, " ") for ln in lines]
    nonempty = [i for i, ln in enumerate(plain) if ln.strip()]
    tail = set(nonempty[-COMPOSER_TAIL_LINES:]) if nonempty else set()
    tops = [i for i, ln in enumerate(plain)
            if ln.lstrip().startswith(_COPILOT_BOX_TOP)]
    for start in reversed(tops):
        for end in range(start + 2, min(len(lines), start + COPILOT_BOX_MAX_ROWS)):
            if plain[end].lstrip().startswith(_COPILOT_BOX_BOTTOM):
                short = end < start + COPILOT_BOX_SHORT_ROWS
                if short and start in tail and end in tail:
                    return lines[start:end + 1], start, end
                if end in tail and all(
                        plain[k].lstrip().startswith(_COPILOT_BOX_LEFT)
                        for k in range(start + 1, end)):
                    return lines[start:end + 1], start, end
                break
    return None, None, None


def _copilot_box_text(box_lines):
    body = []
    for raw in (box_lines or [])[1:-1]:
        plain = strip_sgr(raw).replace(_NBSP, " ").lstrip()
        if plain.startswith(_COPILOT_BOX_LEFT):
            plain = plain[1:]
        body.append(plain.rstrip())
    return "\n".join(body).strip()


def _copilot_modal_text(screen):
    plain = [strip_sgr(ln).replace(_NBSP, " ")
             for ln in (screen or "").splitlines()]
    nonempty = [ln for ln in plain if ln.strip()]
    region = nonempty[-16:]
    for line in region:
        match = _COPILOT_MODAL.search(line)
        if match:
            return match.group(0)
    return None


def find_composer_line(screen, provider=None):
    """回 (原始行含跳脫碼, 行號) 或 (None, None)。

    ★ 定位兩條都要驗,不是抓第一個符合的:
      (a) 所有 ❯ 行裡的**最後一個**;(b) 它必須落在畫面尾端(狀態列之上)。
      只在捲軸上半部找到 ❯(舊回顯)→ 回 None,不拿舊畫面頂替。"""
    chars = _provider_prompt_chars(provider)
    if not chars:
        return None, None
    lines = (screen or "").splitlines()
    # ★★ 2026-09-02(艦長 a seat 現量,這是三層裡真正先開火的那一層):
    #   `startswith(chars)` 假設輸入行**以提示符開頭**。claude/codex/agy 是這樣;
    #   grok 的輸入行是畫框的 —— `  │ ❯ <稿>          │` —— strip 之後開頭是 `│`,
    #   於是**整條鏈連框在哪都定不出來**,composer_state 回 (None, None) → unknown
    #   → aib-dm.sh 退回樣式守衛 → 「疑有滯留稿」→ 七席一個字都送不進去。
    #   ★ 這一層比另外兩層先開火,而另外兩層的臂會全綠 —— 修對象在下游、
    #     真正擋住的在上游,綠燈量不到。
    _border = _dialect_composer_border(provider)

    def _norm(ln):
        t = strip_sgr(ln).replace(_NBSP, " ").strip()
        if _border and t[:1] in _BORDER_CHARS:
            t = t[1:].lstrip()
        return t

    # 09-30 測試艙 b1af:API 席的 CLI 出錯時印了回覆標籤卻沒換行(`xxx-codex> ` 後面直接接 `Xxx-Codex> `),
    #   提示符不在行首 ⇒ 這一行被略過、往上抓到「已經送出的上一行」⇒ 判成別人的稿 ⇒ 全艙私訊被擋。
    #   多字元提示符(DeepSeek>、OpenRouter> 這種)夠特別,出現在行中也算提示行;單字元的 ❯ › 仍只認行首。
    _long = tuple(ch for ch in chars if len(ch) > 1)
    idx = [i for i, ln in enumerate(lines)
           if _norm(ln).startswith(chars) or (_long and any(c in _norm(ln) for c in _long))]
    if not idx:
        return None, None
    nonempty = [i for i, ln in enumerate(lines) if strip_sgr(ln).strip()]
    tail = set(nonempty[-COMPOSER_TAIL_LINES:]) if nonempty else set()
    last = idx[-1]
    return (lines[last], last) if last in tail else (None, None)


def _after_prompt(raw, provider=None):
    """切掉提示符之前(含提示符與其後的 NBSP)的部分,保留跳脫碼。"""
    chars = _provider_prompt_chars(provider)
    # DeepSeek 的 `you>` 是多字元提示符。它只由 deepseek 方言表提供，
    # 不把裸 `>` 擴成全域規則，以免把對話輸出誤判為輸入框。
    prefixes = tuple(ch for ch in chars if len(ch) > 1)
    if prefixes:
        plain = strip_sgr(raw).replace(_NBSP, " ")
        leading = len(plain) - len(plain.lstrip())
        for prefix in prefixes:
            if plain[leading:].startswith(prefix):
                start = raw.find(prefix)
                if start >= 0:
                    end = start + len(prefix)
                    while end < len(raw) and raw[end] in (_NBSP, " "):
                        end += 1
                    return raw[end:]
        # 提示符不在行首(前面是沒換行的回覆標籤):從**最後一個**提示符之後切(見 _prompt_line 09-30 註)
        best = max(((raw.rfind(p), p) for p in prefixes), default=(-1, ""))
        if best[0] >= 0:
            end = best[0] + len(best[1])
            while end < len(raw) and raw[end] in (_NBSP, " "):
                end += 1
            return raw[end:]
        return ""
    plain_pos, i = 0, 0
    # 逐段掃描:遇到 SGR 就跳過,遇到提示符就從它之後切
    while i < len(raw):
        m = _SGR.match(raw, i)
        if m:
            i = m.end()
            continue
        ch = raw[i]
        if ch in chars and plain_pos == 0:
            j = i + 1
            while j < len(raw) and raw[j] in (_NBSP, " "):
                j += 1
            # 畫框的右收邊不是稿的一部分,同樣要削掉(削法與分類器同源:
            # 找最後一個**可見**字元,因為收邊後面還有 SGR 碼)。
            return _trim_trailing_border(raw[j:], provider)
        if not ch.isspace():
            # ★★ 2026-09-02(現量席 eeb46 / ed17 pm-grok 交件時抓到,我沒抓到):
            #   `plain_pos == 0` 要求提示符是**第一個非空白字元**。畫框式輸入行
            #   `  │ ❯ <稿>   │` 的第一個非空白是左收邊 `│`,於是 plain_pos 先被
            #   加成 1,提示符那格的條件永遠不成立 → 整個函式落到 `return ""`。
            #   ★ 失效是**安靜的**:state 仍判得對(empty/draft 分得出來),
            #     只有**稿的內容**永遠是空字串。任何讀稿內容的下游
            #     (卡稿收據、模態文字、對目標內容的 lint)在 grok 席上都讀到空的。
            #   ★ 這顆是第二方交件時順手量出來的,不在我的 18 格臂裡 ——
            #     我的臂只釘了 state,沒釘 text。**綠的那一半不代表另一半也對。**
            if not (_dialect_composer_border(provider)
                    and plain_pos == 0 and ch in _BORDER_CHARS):
                plain_pos += 1
        i += 1
    return ""


_BORDER_CHARS = ("\u2502", "\u2503", "\u2551", "|")


def _trim_trailing_border(text, provider):
    """畫框式輸入行:削掉行尾那一個收邊字元。非畫框 provider 原樣回。

    ★ 只削**一個**,而且是最後一個可見字元;真稿在它之前還有別的字,
      所以不會把稿削短。誤判方向留在保守那一邊。
    """
    if not _dialect_composer_border(provider):
        return text
    last, i = -1, 0
    while i < len(text):
        m = _SGR.match(text, i)
        if m:
            i = m.end()
            continue
        if not text[i].isspace() and text[i] != _NBSP:
            last = i
        i += 1
    return text[:last] if last >= 0 and text[last] in _BORDER_CHARS else text


def suggestion_marks(provider):
    try:
        from composer_dialect import suggestion_marks as _sm
    except ImportError:
        from .composer_dialect import suggestion_marks as _sm
    return _sm(provider)


def _dialect_composer_border(provider):
    try:
        from composer_dialect import composer_border
    except ImportError:
        from .composer_dialect import composer_border
    return composer_border(provider)


def classify_composer(raw_line, provider=None):
    """一行(含跳脫碼)→ EMPTY | SUGGESTION | DRAFT。

    判準:提示符之後的**每一個可見字元**是不是都在 dim(SGR 2)作用中。
    任何一個可見字元不是 dim → DRAFT。**混合態一律當真稿**,因為誤判方向要偏保守:
    把真稿當建議會去按別人沒打完的字,比把建議當真稿多等一輪嚴重得多。
    唯一例外(10-24):提示符後第一個可見字在反白(SGR 7,游標)裡、其餘每個可見字都 dim、總共至少 3 個
    ⇒ 游標蓋住空框提示字 ⇒ SUGGESTION。
    """
    # ★★ 08-19 修(a seat 三行可重放證據):**不能只掃提示符之後的片段**。
    #   舊寫法 `body = _after_prompt(raw)` 把提示符**之前**的 SGR 狀態整段丟掉,
    #   於是 CLI 若渲染成 `ESC[2m❯ Press up to edit queued messages ESC[0m`
    #   (dim 在 ❯ 之前開啟),dim 資訊在切片時就沒了 → 判成 draft → 守衛擋下
    #   並叫送件人「請對方清稿」,而對方**沒有稿可清**。
    #   我實測兩種渲染順序:dim 在 ❯ 之後(我 23:0x capture 到的)→ suggestion 對;
    #   dim 在 ❯ 之前(pm 餵的)→ draft 錯。**同一個畫面內容,兩種寫法兩個答案。**
    #   修法:從行首開始追 SGR 狀態,只是**過了提示符才開始數可見字元**。
    chars = _provider_prompt_chars(provider)
    raw_line = raw_line or ""
    # ★★ 2026-09-02(艦長 a seat 現量):**畫框式輸入行的右收邊會被數成稿。**
    #   grok 的輸入行是 `│ ❯ <稿>          │`;上面「提示符之後每一個可見字元」
    #   的寫法對 claude/codex/agy 是對的(它們沒有右邊界),對 grok 則讓
    #   **空框永遠判 DRAFT** → dm 守衛永遠回「疑有滯留稿,拒絕投遞」→
    #   七席可定址而一個字都送不進去,且 rc 契約回 2(可安全重送)。
    #   ★ 只削掉**行尾的一個**收邊字元加其後空白:真稿在它之前還有別的字,
    #     所以不會把真稿削成 EMPTY —— 誤判方向仍留在保守那一邊。
    #   ★ 不能對原始行直接 rstrip 再看尾字元:收邊 `│` 後面**還有 SGR 碼**,
    #     rstrip 之後尾字元是 `m` 不是 `│`。要先找出最後一個**可見**字元。
    if _dialect_composer_border(provider):
        _last = -1
        _i = 0
        while _i < len(raw_line):
            _m = _SGR.match(raw_line, _i)
            if _m:
                _i = _m.end()
                continue
            if not raw_line[_i].isspace() and raw_line[_i] != _NBSP:
                _last = _i
            _i += 1
        if _last >= 0 and raw_line[_last] in _BORDER_CHARS:
            raw_line = raw_line[:_last]
    dim, seen, all_dim, past = False, 0, True, False
    # 10-24 新帳號第一次開的 claude 席:空框提示字 `Try "…"` 整句 dim,但游標反白(SGR 7)蓋在第一個字上 ⇒
    #   那一個字不是 dim ⇒ 舊判準判 DRAFT,全球來信 / 開席單一直排隊(會員打一個字才解開)。
    #   只放過「提示符後第一個可見字在反白裡、其餘每個可見字都 dim、而且至少還有 2 個」(數的是 code point,不是字形)。
    #   本判準假設真稿不畫 dim(與既有整句 dim 判斷同一前提)。
    #   已知邊界:單字真稿 + 游標移到開頭 + CLI 仍在後面畫 dim 續字 ⇒ 判 SUGGESTION(極少見;代價是信接在那一個字後面)。
    rev, cursor_first = False, False
    i = 0
    while i < len(raw_line):
        m = _SGR.match(raw_line, i)
        if m:
            # ★★ 08-22:**`38`/`48` 的延伸色參數不是獨立的 SGR 碼。**
            #   `ESC[48;2;90;90;90m`(24-bit 背景)裡那個 `2` 是色彩空間選擇子,
            #   不是 dim。舊寫法直接 split(";") 逐個比對,於是把它讀成「dim 開啟」。
            #   codex 的輸入框行**每一幀都帶** `ESC[48;2;90;90;90m`(真機 @6 capture),
            #   所以加了 `›` 之後每一行都會被判成 dim → suggestion → 視同空框。
            #   ★ 方向是危險的那一邊:真的有人打到一半也會判成 suggestion,
            #     而 suggestion 是「可以按 Enter」。本檔開頭寫著誤判方向要偏保守,
            #     這個解析漏洞剛好把它翻過來。
            #   claude 席沒中是因為它用 `38;5;246`(256 色)——那串裡沒有裸 `2`。
            #   **同一個缺陷,兩家 CLI 一個中一個沒中,所以它躲過了所有既有測試。**
            _codes = [c.strip() or "0" for c in (m.group(1) or "0").split(";")]
            _k = 0
            while _k < len(_codes):
                code = _codes[_k]
                if code in ("38", "48", "58"):
                    # 38/48/58 後面跟 5;<n>(256 色)或 2;<r>;<g>;<b>(24-bit)
                    nxt = _codes[_k + 1] if _k + 1 < len(_codes) else ""
                    _k += 2 + (3 if nxt == "2" else 1 if nxt == "5" else -1)
                    continue
                if code == "2":
                    dim = True
                elif code in ("0", "22"):     # 0=全部重設 22=取消 dim/bold
                    dim = False
                if code == "7":
                    rev = True
                elif code in ("0", "27"):
                    rev = False
                _k += 1
            i = m.end()
            continue
        ch = raw_line[i]
        if not past:
            # 還沒到提示符:只追狀態,不數字元
            prompt = next((p for p in chars if raw_line.startswith(p, i)), None)
            if prompt:
                past = True
                i += len(prompt)
                while i < len(raw_line) and raw_line[i] in (_NBSP, " "):
                    i += 1
                continue
            i += 1
            continue
        if not ch.isspace() and ch != _NBSP:
            seen += 1
            if not dim:
                if seen == 1 and rev:
                    cursor_first = True      # 游標蓋住的第一個字:先不算,看後面是不是整句 dim
                else:
                    all_dim = False
        i += 1
    if not past:
        return EMPTY          # 這行沒有提示符:定位端已擋,這裡不猜
    if seen == 0:
        return EMPTY
    if cursor_first and seen < 3:
        all_dim = False       # 只有游標字 + 1 個 dim:太短,當真稿
    return SUGGESTION if all_dim else DRAFT


def _all_visible_italic(raw):
    """提示符之後的每一個可見字元是不是都在 SGR 3(斜體)作用中。

    ★ 與 dim 那條同構,但**分開一條**:grok 的建議用斜體 + 24-bit 灰,不帶 SGR 2。
      合成一條會讓兩家的失效方向混在一起。
    """
    italic, seen, all_it, past = False, 0, True, 0
    i = 0
    while i < len(raw):
        m = _SGR.match(raw, i)
        if m:
            codes = [c.strip() or "0" for c in (m.group(1) or "0").split(";")]
            k = 0
            while k < len(codes):
                code = codes[k]
                if code in ("38", "48", "58"):
                    nxt = codes[k + 1] if k + 1 < len(codes) else ""
                    k += 2 + (3 if nxt == "2" else 1 if nxt == "5" else -1)
                    continue
                if code == "3":
                    italic = True
                elif code in ("0", "23"):
                    italic = False
                k += 1
            i = m.end()
            continue
        ch = raw[i]
        if not ch.isspace() and ch != _NBSP:
            seen += 1
            if not italic:
                all_it = False
        i += 1
    return seen > 0 and all_it


def _all_visible_darker_than(raw, ceiling):
    """提示符之後的每一個可見字元是不是都畫在**暗色**上:24-bit 前景色三個分量都 ≤ ceiling。

    ★ 顏色要是**量得到的**才算:沒設前景色、被 39/0 重設、256 色(38;5;n)都當「量不到」⇒ 不算暗。
      量不到就不放行 —— 失效方向留在「當成真稿」那一邊。
    """
    fg, seen, all_dark = None, 0, True
    i = 0
    while i < len(raw):
        m = _SGR.match(raw, i)
        if m:
            codes = [c.strip() or "0" for c in (m.group(1) or "0").split(";")]
            k = 0
            while k < len(codes):
                code = codes[k]
                if code == "38":
                    nxt = codes[k + 1] if k + 1 < len(codes) else ""
                    if nxt == "2" and k + 4 < len(codes) and all(c.isdigit() for c in codes[k + 2:k + 5]):
                        fg = tuple(int(c) for c in codes[k + 2:k + 5])
                        k += 5
                    else:
                        fg = None
                        k += 3 if nxt == "5" else 1
                    continue
                if code in ("48", "58"):
                    nxt = codes[k + 1] if k + 1 < len(codes) else ""
                    k += 2 + (3 if nxt == "2" else 1 if nxt == "5" else -1)
                    continue
                if code in ("0", "39"):
                    fg = None
                k += 1
            i = m.end()
            continue
        ch = raw[i]
        if not ch.isspace() and ch != _NBSP:
            seen += 1
            if fg is None or max(fg) > ceiling:
                all_dark = False
        i += 1
    return seen > 0 and all_dark


def looks_modal(screen, provider=None):
    """畫面尾端是不是一個模態選單。

    兩個條件都要(單獨任一個都會誤判):**至少兩個選項行**(`1.` `2.` …,
    其一可帶 ❯ 游標)**且**有確認提示(`Enter to confirm` / `Esc to cancel`)。
    只認尾端 MODAL_TAIL_LINES 個非空行——引文區的模態文字不算。
    """
    if composer_kind(provider) == "box":
        return _copilot_modal_text(screen) is not None
    lines = (screen or "").splitlines()
    _raw, idx = find_composer_line(screen, provider=provider)
    if idx is None:
        return False          # 定不出輸入框:不猜(擋不擋由呼叫端決定,見檔首更正)
    # ★ 「位置」不是「尾端 N 行」——我第一版就是這麼寫的,而引文剛好落在窗內
    #   於是廣播裡的模態三行讓整個窗判成 modal(測試 test_quoted_modal…
    #   當場抓到)。真正的位置判準是**錨在輸入框那一行往下**:模態的選項列
    #   就是輸入框那一行,確認提示在它下面;引文永遠在輸入框**上面**。
    region = [strip_sgr(ln).replace(_NBSP, " ") for ln in lines[idx:]]
    options = sum(1 for ln in region if _MODAL_OPTION.match(ln))
    confirm = any(_MODAL_CONFIRM.search(ln) for ln in region)
    return options >= 2 and confirm


def looks_browse(screen, provider=None):
    """第 55 項:畫面尾端是不是該引擎的瀏覽模式底列(所有標記都在),**而且**定不出輸入框。

    兩個條件都要:輸入行在畫面上 ⇒ 不是瀏覽模式(照常分類);標記少一個 ⇒ 仍是 unknown(保守側:
    誤判成 browse 只是把理由說錯,誤判成 unknown 也只是理由說錯,兩邊都擋,不會多送一個字)。
    """
    marks = browse_footer(provider)
    if not marks:
        return False
    raw, _idx = find_composer_line(screen, provider=provider)
    if raw is not None:
        return False
    tail = [strip_sgr(ln).replace(_NBSP, " ") for ln in (screen or "").splitlines() if strip_sgr(ln).strip()]
    tail = tail[-BROWSE_TAIL_LINES:]
    joined = "\n".join(tail)
    return all(m in joined for m in marks)


def looks_history(screen, provider=None):
    """10-14 K8:目前這個輸入框是不是在瀏覽輸入歷史。

    認的是**最後一個框**的頂線:畫面上最後兩條框線(去掉跳脫碼後以 ─ 開頭)分別是框頂與框底,
    框頂那條帶著方言宣告的字樣(`History n/m`)才算。只看最後一個框 ⇒ 捲軸上半部的舊畫面不算;
    多行那筆舊訊息讓提示符那行掉出尾端 8 行也一樣認得(框頂線還是那條)。"""
    pat = history_border(provider)
    if not pat:
        return False
    rx = re.compile(pat)
    borders = [strip_sgr(ln).replace(_NBSP, " ").strip() for ln in (screen or "").splitlines()]
    borders = [t for t in borders if t.startswith("─")]
    return len(borders) >= 2 and bool(rx.search(borders[-2]))


def unknown_reason(screen, provider=None):
    """定不出輸入框時,**是哪一種**定不出來。定得出來回 None。

    ★ 拆的是**成因**不是決定:兩種 unknown 的處置一樣(都擋),
      但巡查要數得出來哪一種,否則 `AIB_DM_STRICT_UNKNOWN` 的翻轉條件
      (各格 unknown 計數歸零)永遠不知道卡在誰身上。
      `no_prompt_anywhere` 多半是沒有 composer 的格子,
      `prompt_only_in_scrollback` 是真的有框而我看不清楚 —— 兩種要分開追。
    """
    if composer_kind(provider) == "box":
        _box, start, _end = find_copilot_box(screen)
        if start is not None or _copilot_modal_text(screen) is not None:
            return None
        has_box = any(
            strip_sgr(ln).replace(_NBSP, " ").lstrip().startswith(
                (_COPILOT_BOX_TOP, _COPILOT_BOX_LEFT, _COPILOT_BOX_BOTTOM))
            for ln in (screen or "").splitlines())
        return "composer_only_in_scrollback" if has_box else "no_prompt_anywhere"
    raw, _idx = find_composer_line(screen, provider=provider)
    if raw is not None:
        return None
    lines = (screen or "").splitlines()
    has_prompt = any(
        strip_sgr(ln).replace(_NBSP, " ").strip().startswith(
            _provider_prompt_chars(provider))
        for ln in lines)
    return "prompt_only_in_scrollback" if has_prompt else "no_prompt_anywhere"


def cursor_row_state(screen, cursor_y, provider=None):
    """R7(09-25 a seat):只看游標那一行。screen 必須是**可視區** `capture-pane -e -p`(不帶 -S / -J),
    才會與 `#{cursor_y}` 一列對一列;殼的 `$(...)` 會吃掉尾端空列 ⇒ 游標列超出 = 那一列是空的。

    游標行是空的 ⇒ EMPTY(9260 a seat 那一幕:游標在空行,上面是通知與舊回顯)。
    游標行有提示符 ⇒ 照舊用 classify_composer 判那一行(提示符後有非 dim 字 = DRAFT,人真的在打)。
    游標行有字、沒提示符 ⇒ 可能是人打的長稿折到下一列:往上找同一段的提示符(最多 CURSOR_WRAP_ROWS 列),
      碰到空行或 cursor_stop 宣告的「橋自己印的行」就停 ⇒ EMPTY(回合中貼進來的回顯、模型輸出)。
      ★ 這一步是為了不把真人的長稿判成空框——那個方向會毀稿,比多擋一封嚴重。"""
    lines = (screen or "").splitlines()
    chars = _provider_prompt_chars(provider)
    stops = cursor_stop(provider)

    def _shown(i):
        return strip_sgr(lines[i]).replace(_NBSP, " ").strip() if 0 <= i < len(lines) else ""

    if not chars or not _shown(cursor_y):
        return EMPTY, ""
    i = cursor_y
    while i >= 0 and cursor_y - i <= CURSOR_WRAP_ROWS:
        t = _shown(i)
        if t.startswith(chars):
            break
        if not t or (stops and t.startswith(stops)):
            return EMPTY, ""
        i -= 1
    else:
        return EMPTY, ""
    row = lines[i]
    state = classify_composer(row, provider=provider)
    return state, strip_sgr(_after_prompt(row, provider=provider)).replace(_NBSP, " ").strip()


def composer_state(screen, pane_command=None, provider=None, cursor_y=None):
    """畫面(必須是 `capture-pane -e` 的輸出)→ (state, 純文字)。

    定不出輸入框 → (None, None):**不猜**。呼叫端要把「定不出來」與「框是空的」
    當成兩件事——今天整條鏈的病根就是把「我看到的」直接當成「那裡有的」。
    cursor_y(R7):呼叫端量得到游標列、且方言宣告 cursor_line 時,只看游標那一行(cursor_row_state);
    其餘情況一個位元組都不變。
    """
    if cursor_y is not None and cursor_line(provider):
        return cursor_row_state(screen, cursor_y, provider=provider)
    if composer_kind(provider) == "box":
        modal_text = _copilot_modal_text(screen)
        if modal_text is not None:
            return MODAL, modal_text
        box, _start, _end = find_copilot_box(screen)
        if box is None:
            return None, None
        text = _copilot_box_text(box)
        return (DRAFT if text else EMPTY), text

    # 10-14 K8:框在瀏覽輸入歷史 ⇒ HISTORY(擋,理由不是卡稿);排在逐行分類之前 —— 那行字長得像真稿,但它是別席的舊訊息
    if looks_history(screen, provider=provider):
        return HISTORY, None
    raw, _idx = find_composer_line(screen, provider=provider)
    if looks_modal(screen, provider=provider):
        # ★ 模態優先於逐行分類:選項行長得像真稿(非 dim),但它不是稿。
        # ★★ 08-19 更正一句寫錯的說明:初版註解寫「定不出輸入框時**仍然**回
        #   MODAL」,那在初版(尾端 N 行)為真;把判準錨到輸入框那一行之後
        #   **那條路就不可達了**——`looks_modal` 定不出 idx 就回 False,於是
        #   走下面的 `raw is None → (None, None)`(本模組不猜;下游目前會退回
        #   較弱的樣式守衛而**可能放行**,見檔首 23:2x 更正)。
        #   行為沒問題(兩條路都是擋),**錯的是那句說明**:它描述了一個不存在
        #   的分支,而艦長 23:19 逐格複核時把它當成我的設計驗過去了。
        #   ——「帶著假理由的死碼」的孿生型:**帶著假理由的說明**,今晚第四次。
        return MODAL, strip_sgr(_after_prompt(raw, provider=provider)).replace(
            _NBSP, " ").strip()
    if raw is None:
        # 第 55 項:定不出輸入框但尾端是瀏覽模式底列 ⇒ BROWSE(擋,理由是「回輸入列即可」,不是卡稿)
        if looks_browse(screen, provider=provider):
            return BROWSE, None
        # EH.16:兩個條件同時成立才敢說「不適用」,理由見 NO_COMPOSER_COMMANDS 上面。
        if (isinstance(pane_command, str)
                and pane_command.strip().lower().lstrip("-") in NO_COMPOSER_COMMANDS
                and unknown_reason(screen, provider=provider) == "no_prompt_anywhere"):
            return NOT_APPLICABLE, None
        # 2026-09-24(a seat 實測):橋接席回合進行中**沒有**提示符行,尾端只有橋自己印的
        #   忙碌通知(方言 busy_markers 宣告)。那是正面證據:通知只在橋接受輸入之後
        #   才印,此刻輸入框是空的、稿已送進模型 ⇒ EMPTY(貼進來會排在 tty 緩衝,
        #   下一次 input() 讀走,與其他 CLI 忙碌時貼稿排隊同一件事)。
        #   沒宣告 busy_markers 的 provider 一個位元組都不變:照舊 (None, None)。
        _busy = busy_markers(provider)
        if _busy:
            _tail = [strip_sgr(ln).strip() for ln in (screen or "").splitlines() if strip_sgr(ln).strip()]
            if any(ln.startswith(_busy) for ln in _tail[-COMPOSER_TAIL_LINES:]):
                return EMPTY, ""
        return None, None
    state = classify_composer(raw, provider=provider)
    body = _after_prompt(raw, provider=provider)
    # ★★ 2026-09-02:把 DRAFT **升級**成 SUGGESTION,而且兩個訊號都要 ——
    #   (a) 提示符之後每一個可見字元都在斜體作用中(grok 的建議樣式),
    #   (b) 頁尾出現 provider 自己宣告的建議字面(CLI 親口說有建議)。
    #   單靠顏色或單靠頁尾都不夠:誤判方向要偏保守,
    #   **把真稿當成建議 = 去按別人沒打完的字**,比多等一輪嚴重得多。
    if state == DRAFT:
        want_italic, footer = suggestion_marks(provider)
        if want_italic and footer and footer in strip_sgr(screen or ""):
            if _all_visible_italic(body):
                state = SUGGESTION
    # ★ 2026-09-29:空框的提示字(方言宣告 empty_placeholders)。兩個訊號都要:字面逐字相同、全部畫在暗色上。
    if state == DRAFT:
        marks, ceiling = empty_placeholder_marks(provider)
        if marks:
            inner = _trim_trailing_border(body, provider)
            if (strip_sgr(inner).replace(_NBSP, " ").strip() in marks
                    and _all_visible_darker_than(inner, ceiling)):
                return EMPTY, ""
    return (state,
            strip_sgr(body).replace(_NBSP, " ").strip())


_WS = re.compile(r"\s+")   # 與 dm_paste.ENGINE_SPACE 同一把尺(對齊 BSD tr -d '[:space:]')


def repl_submitted(screen, body, provider=None):
    """10-01 長稿(R16 M2):input() 型 REPL 席,Enter 之後畫面上那行「提示符+本稿」是**回音**還是**草稿**。

    現量形狀(deepseek_cli.run_repl):按 Enter ⇒ `DeepSeek> <稿>`(折成幾列)原樣留著,
    下一行印 `deepseek> `(回覆標籤,小寫,不是輸入提示符)等模型想。composer_state 找最後一個
    提示符行 = 那行回音 ⇒ 判 draft;composer-match 說 mine ⇒ 已收下的稿被判卡住(rc 4)。
    判準(兩條都要,缺一回 False = 照舊):
      (a) 從最後一個提示符行往下逐列接起來(去空白),**恰好**接成整張本稿;
      (b) 本稿最後一列**之後**還有非空的列(CLI 已經往下印了東西)。
    草稿還在輸入列時,本稿就是畫面最後幾列,(b) 不成立 ⇒ 照舊判卡住。
    只對方言宣告 input_repl 的 provider 有效;copilot 畫框與各家 TUI 一律 False(行為不變)。
    """
    if not input_repl(provider):
        return False
    want = _WS.sub("", body or "")
    if len(want) < 8:
        return False
    raw, idx = find_composer_line(screen, provider=provider)
    if raw is None:
        return False
    lines = (screen or "").splitlines()
    acc = _WS.sub("", strip_sgr(_after_prompt(raw, provider=provider)).replace(_NBSP, " "))
    k = idx
    while len(acc) < len(want) and k + 1 < len(lines):
        k += 1
        acc += _WS.sub("", strip_sgr(lines[k]).replace(_NBSP, " "))
    if acc != want:
        return False
    return any(strip_sgr(ln).strip() for ln in lines[k + 1:])


def blocks_send(state):
    """該不該擋住代送/投遞。只有**真稿**擋;建議與空框都放行。

    dm-ctl 的卡稿守衛今天擋掉艦外五道令與 the maintainer 一道 34 分鐘沒送達的指令,
    全部是被 CLI 建議文字擋的。定不出輸入框(None)本模組**不表態**——
    `blocks_send(None)` 回 True 只是本函式的立場,**實際擋不擋在呼叫端**;
    現行 aib-dm.sh 收到 unknown 會退回舊樣式守衛,比不中就送出(檔首有實測)。

    ★ MODAL 也擋,而且**擋得比 DRAFT 更該擋**:裸 Enter 會替對方選中游標所在
    的選項。行為不變,變的是呼叫端能說對理由(見 send_block_reason)。"""
    # EH.16:`n/a` = 那一格沒有輸入框,**沒有東西可以毀**,所以放行。
    # 它與 EMPTY 的差別在於「框是空的」vs「根本沒有框」——兩者處置相同、
    # 成因不同,不合併成一個值(合併之後就再也分不出翻轉條件卡在誰身上)。
    return state not in (SUGGESTION, EMPTY, NOT_APPLICABLE)


def send_block_reason(state):
    """擋下時該對送件人說什麼。回 None=不擋。

    ★ 這支存在的理由:**擋對了但理由錯,對讀的人是兩件事**(艦長 23:07)。
    叫人「請對方清稿」而對方沒有稿可清,送件人只會反覆重送或去繞守衛。
    """
    if not blocks_send(state):
        return None
    if state == MODAL:
        return ("目標框裡是**模態選單**(不是草稿):裸 Enter 會替對方選中游標"
                "所在的選項。**沒有稿可清**——需要該席的人自己回答那個選單。")
    if state == BROWSE:
        return ("目標 TUI 在**瀏覽模式**(底列 ←:collapse / Enter:open),輸入行不在畫面上,"
                "**不是卡稿、沒有稿可清**:請該席按 Esc 回到輸入列後重送;這封信沒有進到對方框,可安全重送。")
    if state == HISTORY:
        return ("目標 Claude 的輸入框停在**輸入歷史**(框頂線 History n/m),框裡是別席的舊訊息,"
                "**不是草稿、沒有稿可清**:aib dm 先試著按 ↓ 回到空框,這次沒退出來(畫面在動或按到上限)。"
                "請該席按 ↓ 直到框頂線的 History 消失後重送;這封信沒有進到對方框,可安全重送。")
    if state == DRAFT:
        return ("目標輸入框有**真的**未送出草稿(非 dim):裸 Enter 會把對方"
                "半寫的稿毀掉送出。請對方清稿後重送；若已比對確認這是本次"
                "自己的稿，才可設 AIB_DM_SKIP_DRAFT_GUARD=1 重送。")
    return ("定不出目標的輸入框(捲軸上只有舊回顯等):看不清楚時不動別人的框"
            "(fail-closed)。")


def _main(argv=None):
    """CLI:`composer_state.py --stdin` 讀畫面(capture-pane -e)→ 印 state 一個字。

    給 shell 消費端用(aib-dm.sh 卡稿守衛)。定不出輸入框印 `unknown` 並回 rc=3,
    呼叫端**應該**據此 fail-closed;實測現行呼叫端是退回舊樣式守衛(檔首更正)。
    仍**不印空字串**,免得 shell 的 case 把它當成某一態。
    """
    import sys as _sys
    argv = [] if argv is None else list(argv)
    data = _sys.stdin.read() if (not argv or "--stdin" in argv) else ""
    # EH.16:`--pane-command <cmd>` 是 `n/a` 這一態的**唯一入口**,而它本身
    #   就是相容紅線的解法:今天兩個消費端(aib-dm.sh:212、dm-ctl.sh:602)
    #   都沒有傳它,所以它們**在結構上收不到新字串** —— 新增一態不必靠
    #   消費端同步更新才安全,那正是上面那條紅線要求的。
    pane_cmd = None
    if "--pane-command" in argv:
        _i = argv.index("--pane-command")
        if _i + 1 < len(argv):
            pane_cmd = argv[_i + 1]
    provider = os.environ.get("AIB_DM_TARGET_PROVIDER") or None
    if "--provider" in argv:
        _i = argv.index("--provider")
        if _i + 1 < len(argv):
            provider = argv[_i + 1]
    # R7:`--cursor-y N`(tmux `#{cursor_y}`,0 起算)。量不到或不是數字 ⇒ 不給(行為不變)。
    cursor_y = None
    if "--cursor-y" in argv:
        _i = argv.index("--cursor-y")
        if _i + 1 < len(argv) and argv[_i + 1].isdigit():
            cursor_y = int(argv[_i + 1])
    if "--repl-submitted" in argv:
        # R16 M2:`--repl-submitted <稿檔>` 印 yes / no(rc 0)。讀不到稿檔 ⇒ no(照舊判卡住,不往「已送出」倒)。
        _i = argv.index("--repl-submitted")
        try:
            with open(argv[_i + 1], encoding="utf-8", errors="replace") as _f:
                _body = _f.read()
        except (IndexError, OSError):
            print("no")
            return 0
        print("yes" if repl_submitted(data, _body, provider=provider) else "no")
        return 0
    state, _text = composer_state(data, pane_command=pane_cmd, provider=provider, cursor_y=cursor_y)
    # ★★ 相容紅線(艦長 23:12 界線:第三態只准改理由,不准改准不准按):
    #   消費端 aib-dm.sh:167 的 case 只認 suggestion|empty|draft,**印任何新字串
    #   都會落進 `*)` 去跑 fallback 樣式比對**——而模態不見得比得中那個樣式,
    #   於是「本來擋下的」可能變成「放行」。所以預設輸出**維持舊字彙**
    #   (modal→draft:擋,且擋的方式與上線前逐字元相同);新資訊只在明確
    #   要求時才出現。**新增一態不得靠消費端同步更新才安全。**
    legacy = MODAL not in argv and "--modal-aware" not in argv
    # BROWSE 與 MODAL 同一條相容線:舊字彙下印 draft(擋,且擋法逐字同上線前),新資訊只在 --modal-aware 才出現
    shown = DRAFT if (legacy and state in (MODAL, BROWSE, HISTORY)) else state
    if "--reason" in argv:
        print(send_block_reason(state) or "")
    elif "--composer-text" in argv:
        # ★ 只在明確要求時才出現的第三種輸出(與 --reason 同一條相容線):
        #   印**輸入框那一行的文字**,不印狀態字。預設輸出一個位元組都沒動,
        #   所以 aib-dm.sh:212 / dm-ctl.sh:602 的 case 看不到新字串 ——
        #   「新增一態不得靠消費端同步更新才安全」這條紅線在這裡一樣守著。
        #   用途:判「框裡那張稿是不是我剛貼的」。整張畫面的比對做不到這件事,
        #   因為捲動區可能早就有同一段字(我重送、或對方引述過)——而那正是
        #   aib-dm.sh 的 BEFORE 閘要擋的假綠。**空的輸入框裝不下捲動區的回音**,
        #   所以把比對縮到框內,是加一條正面證據,不是放寬那道閘。
        print(_text if _text is not None else "")
    else:
        print(shown or "unknown")
    # `n/a` 是**答案**不是量測失敗,所以 rc=0 —— 它與 unknown 的 rc=3 分開,
    # 巡查才數得出「還有幾格真的判不出來」。兩個消費端都只讀 stdout 不讀 rc
    # (實查:aib-dm.sh:212 與 dm-ctl.sh:602 都是 `$(… 2>/dev/null)`),
    # 所以改 rc 不動到任何現行決定。
    return 0 if state in (EMPTY, SUGGESTION, DRAFT, MODAL, BROWSE, HISTORY, NOT_APPLICABLE) else 3


if __name__ == "__main__":
    import sys as _s
    _s.exit(_main(_s.argv[1:]))
