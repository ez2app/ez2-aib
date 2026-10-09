#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""dm 貼稿佔位符文法 + rc5 回復狀態機(SPEC-dm-auto-enter-0821)。

本檔存在的理由,是「把補 Enter 程式化」這件事需要一個**兩端共用**的佔位符
文法。08-21 一晚人補了十幾次 Enter,每一次跑的都是同一套機械程序;把它寫下來
之前必須先解決一個更基本的問題:**同一個畫面,不同的解析器會給出不同的答案。**

## 兩種方言(兩者都是機器取得的,不是設想)

    claude   [Pasted text #83]                 —— 無數量
             [Pasted text #92 +3 lines]        —— 數量=行數,K+1 == 稿件行數
    codex    [Pasted Content 1282 chars]       —— 數量=字元數

★ codex 那一串是從**它自己的二進位檔**取出來的,不是我推的:
  `strings @openai/codex-darwin-arm64/.../bin/codex | grep -i pasted` 吐出
  `[Pasted Content ` 與 ` chars]` 兩段(Rust 格式字串的兩半),下一筆字串是
  `tui/src/bottom_pane/prompt_args.rs`。a seat 的釘子從另一個方向獨立寫出
  同一個字面——**兩臂互相不知情而一致,這才是它可以當判準的理由。**

## ★★ 為什麼判準要放在 AFTER 的**總數**上,而不是「新增了幾枚」

引擎的 BEFORE 解析器只認得 claude 方言(`aib-dm.sh:168`)。如果本檔把 AFTER
的方言擴寬、而 BEFORE 那一端沒有跟著寬,結果**不是漏看,是假陽性**:框裡本來
就有的一枚 codex 佔位符,在 BEFORE 眼裡不存在、在 AFTER 眼裡是新的
→ 「新增恰一枚」成立 → 自動按下別人的 Enter。

  ★ 一個守衛的兩條腿用不同的尺量,比兩條腿都短更危險。

修法不是去改引擎(那是別人的檔),是**換一個不依賴 BEFORE 解析寬度的判準**:

    (1) BEFORE 必須「已知」且「是空的」(null 與 [] 不得壓成同一格)
    (2) AFTER 的佔位符**總數**必須恰為 1

BEFORE 若因方言盲區而漏看了一枚舊佔位符,那一枚仍然會出現在 AFTER 的總數裡
→ 總數 2 → 停手。**盲區被另一條腿蓋住了,而不是靠「應該不會發生」。**

## rc(SPEC §三:rc=2 現在說謊)

    2   真的一個字都沒進(貼之前就被擋下,且目標端可觀測地零新增)—— 可安全重送
    3   已送出但未確認(Enter 按了,回讀不確定)—— 勿重貼
    4   進了沒送出,或框內狀態不可判 —— **絕不重送**

★ 2 與 4 的處置**相反**,所以它們不能共用一個值;這正是 08-21 晚上咬了架構
  總管三次的那一格。
"""

import json
import os
import re
import sys

# ★ 文法是**資料**不是散落的正則:三個字面在同一個表裡,加方言只加一列。
#   claude 的帶 +K 那式必須排在裸式前面(交替是最左優先,裸式會先吃掉前綴)。
PLACEHOLDER_RE = re.compile(
    r"\[Pasted text #(?P<cid>\d+) \+(?P<lines>\d+) lines?\]"
    r"|\[Pasted text #(?P<bare>\d+)\]"
    r"|\[Pasted Content (?P<chars>\d+) chars\]"
    # ★ 2026-09-02 現量(艦長 a seat,90b5 艙 dev-grok 那格貼 38 行):
    #   grok 印 `[Pasted: 38 lines]` —— 沒有 `text`、沒有 `#N`、數量是**總行數**。
    #   缺這一列的後果不是報錯:回讀找不到佔位符 → 「已貼但無跡象」→ rc=3,
    #   而稿**真的已經在框裡**。收據說沒跡象,畫面上有 38 行。
    r"|\[Pasted: (?P<grok_lines>\d+) lines?\]"
    # ★ 2026-09-02 現量(offhub b6fdd,艙 ed17 pane %5,paste-buffer -p):
    #   10000 位元組仍是 `[Pasted: 132 lines]`;10240 起換成 `[Pasted: 10 KB]`。
    #   顯示值現查=floor(bytes/1000),中間有空白,單位大寫 KB。
    #   1000804 位元組 → `[Pasted: 1.0 MB]`;500000 仍是 `[Pasted: 500 KB]`。
    #   KB/MB **對不上行數**,認得之後自動 Enter 走具名停手(見 decide kb-fold)。
    r"|\[Pasted: (?P<grok_kb>\d+) KB\]"
    r"|\[Pasted: (?P<grok_mb>\d+(?:\.\d+)?) MB\]"
    # ★ 2026-09-02 現量(現量席 eeb46 交件 + 艦長在 90b5 qa2-copilot 那格複量):
    #   copilot 印 `[Paste #6 - 14 lines]` —— **`Paste` 沒有 d**、`#N` 之後是
    #   空白短橫空白、數量是**總行數**。與 claude 的 `[Pasted text #N +K lines]`
    #   三處都不同,舊表一字不中。
    #   ★ 為什麼之前沒人發現:copilot 的摺疊門檻是 **11 行**,
    #     而先前的可投遞性現量只貼了 5 行 —— 沒摺,自然也沒有佔位符要認。
    #     **「量過而且是綠的」與「量到的那一段剛好不會壞」是兩件事。**
    r"|\[Paste #(?P<cp_id>\d+) - (?P<cp_lines>\d+) lines?\]"
    # ★ 2026-09-02 AE 現量(qa2-copilot):32KB 起整句換形
    #   `[Saved pasted content to workspace (32.0 KB) id=1]`
    #   16KB 仍是 `[Paste #N - M lines]`;16–32 之間與 128KB 以上本席未獨立量到
    #   (上限具名:正則不設死上限,理智檢查 AIB_DM_MAX_BYTES 仍擋超大貼)。
    #   N.0 KB 是近似值,對不上行數也不對得準 bytes → 自動 Enter 具名停手。
    r"|\[Saved pasted content to workspace \((?P<cp_saved_kb>\d+(?:\.\d+)?) KB\) id=(?P<cp_saved_id>\d+)\]"
)

# 引擎那兩條 shell grep 是**另一份**字面,本常數是它在 Python 這端的對應。
# ★ 2026-09-02:原註解用「逐字複製自 <檔>:<行號>」的寫法指過去,而那一行早已不是那段了
#   —— 引用會腐爛,而腐爛的引用會被逐格複核當成設計驗過去(本庫的舊傷)。
#   ⇒ 改成不寫行號,由 test-composer-dialect-grok-c8dea.py 的 test_44
#     **現查兩端接受的字面集合是否相同**;字面不同就是債,而債要看得見。
ENGINE_BEFORE_RE = (r"\[Pasted text #[0-9]+( \+[0-9]+ lines?)?\]"
                    r"|\[Pasted: [0-9]+ lines?\]"
                    r"|\[Pasted: [0-9]+ KB\]"
                    r"|\[Pasted: [0-9]+(?:\.[0-9]+)? MB\]"
                    r"|\[Paste #[0-9]+ - [0-9]+ lines?\]"
                    r"|\[Saved pasted content to workspace \([0-9]+(?:\.[0-9]+)? KB\) id=[0-9]+\]")

# 佇列態標記:兩個字面都是實地觀測到的(codex 的 `tab to queue message`、
# claude 的 `Press up to edit queued messages`)。★ 佇列是**正常成功**不是失敗。
QUEUE_MARKERS = ("tab to queue message", "Press up to edit queued messages")

# ★ 2026-09-02 現量(offhub b6fdd,艙 ed17,grok 4.6,idle pane %6,
#   通道與引擎相同:tmux paste-buffer -p):
#   詞首 U+0040(貼上後可選空白之後的第一個 token,或逐字打)會打開
#   grok TUI 的 fuzzy file picker。選單蓋在框上方,計數行現查為
#   `─N/M─`(U+2500 畫框線 + 命中/總數)。Enter 會選中反白檔,整份稿被吃掉。
#   現查不觸發:email(`user@x.com`)、CJK 緊貼(`碼@hello`)、
#   正文中間且前面已有別的 token 的單行。
#   現查可擋:U+0040 後插 U+200B(ZWSP)。反斜線與雙寫 U+0040 仍開選單。
# ★ 兩條腿:投遞前 shield(防開選單)+貼後 looks_file_mention_picker(選單已開
#   就停手)。任一條單獨失效時另一條還會吵;兩條都失效才會安靜送出。
_AT = "\u0040"
_ZWSP = "\u200b"
_PICKER_BLOCK_RE = re.compile(r"─\d+/\d+─\s*\n\s*❯\s+\S+", re.MULTILINE)
_TOKEN_AT_RE = re.compile(r"(^|[\s])" + _AT, re.MULTILINE)


def looks_file_mention_picker(text):
    """貼後畫面是不是 grok 檔案提及選單。現量塊:計數行下一行是 `❯ 路徑`。"""
    return bool(_PICKER_BLOCK_RE.search(text or ""))


def shield_token_at_signs(text):
    """詞首 U+0040 後面插入 ZWSP,讓 grok TUI 不開選單。email/緊貼不變。"""
    if not text:
        return text
    return _TOKEN_AT_RE.sub(r"\1" + _AT + _ZWSP, text)


ENGINE_SPACE = re.compile(r"\s+")   # 對齊 BSD tr -d '[:space:]'(UTF-8: iswspace)
MIN_FP = 8          # 指紋短於這個長度就不算證據(短指紋容易恆真)
FP_LEN = 24         # 與引擎 aib-dm.sh 的頭指紋同長


def head_fingerprint(text, n=FP_LEN):
    """本稿正文去空白後的前 n 個字元。與引擎頭指紋同一種取法。

    ★ 取法要跟引擎一致是**刻意**的:兩邊判的是同一件事,不該有兩套文法。
      (引擎那份用 bash 切;這裡是第二個實作,未來兩者應收斂成一份 —— 已登記在
       交件單的盲區欄,本次不改引擎。)
    """
    # ★ 空白類別要跟引擎 `tr -d '[:space:]'` **在這台上的實際行為**一致:
    #   macOS 的 BSD tr 在 UTF-8 locale 走 iswspace,連全形空白 U+3000 都吃;
    #   python 的 \s 對 str 也是 Unicode 空白(含 U+3000),所以用 \s 對得齊。
    #   (GNU tr 是逐位元組,不吃 U+3000 —— 換平台這裡會漂;對照尺
    #    tests/test-dm-fingerprint-parity-9a2be.py 逐字比對兩邊,漂了先紅。)
    return ENGINE_SPACE.sub("", text or "")[:n]


# ★ 10-01(a seat copilot 現量):畫框型輸入框把一行長稿折成好幾列,每列前面多一個框線字
#   (copilot `┃`、grok `│`)。去空白之後框線字夾在稿中間,跨折行點的指紋就比不到。
#   只從**畫面/框內**那一側剝(稿的指紋取法不動,與引擎對照尺一致)。
BOX_BORDERS = re.compile("[\u2503\u2502]")


def screen_norm(text):
    """畫面或框內文字 → 去空白、去框線字。與引擎 AFTER/BEFORE_NS 的剝法一致。"""
    return BOX_BORDERS.sub("", ENGINE_SPACE.sub("", text or ""))


def tail_fingerprint(text, n=FP_LEN):
    """本稿去空白後的最後 n 個字元(引擎尾指紋同一種取法)。"""
    return ENGINE_SPACE.sub("", text or "")[-n:]


def composer_is_exactly_mine(composer_text, body):
    """框裡**恰好就是**本稿(去空白、去框線後逐字相等)。

    ★ 給「Enter 之後框沒清,我可不可以再按一次」用:Enter 送出的是**整個框**,
      所以只有整框恰好等於我貼的那份,才證得出再按一次不會替別人送出半寫的字。
      含有(而非等於)不夠 —— 別人可能在我的稿後面接著打字。
    """
    want = ENGINE_SPACE.sub("", body or "")
    if len(want) < MIN_FP:
        return False
    return screen_norm(composer_text) == BOX_BORDERS.sub("", want)


def landed_on_screen(screen, body):
    """貼後畫面上看不看得見本稿的頭或尾指紋(去空白、去框線後)。

    ★ 用途是**擋「當成沒進去」**,不是證明送達:看得見 ⇒ 稿(或它的回音)在對方畫面上,
      這時不准再排一份、不准給「可安全再投」。指紋若其實是捲動區的舊回音,
      誤判方向是「不再投」—— 那是便宜的方向(少一份),不是疊稿。
    """
    scr = screen_norm(screen)
    for fp in (head_fingerprint(body), tail_fingerprint(body)):
        if len(fp) >= MIN_FP and fp in scr:
            return True
    return False


LONG_PASTE_BYTES = 1024   # 與 dm-ctl `_LP_KB >= 1`、引擎 `_land_kb >= 1` 同一條線


def composer_holds_draft(composer_text, body, long_paste=None):
    """輸入框那一行裡,逐字含不含本稿的頭指紋。

    ★ R16 m2:下面那條 10-01 的放寬(尾指紋、剝框線)**只給長稿**(≥1KB,或呼叫端明說 long_paste=True)。
      短稿逐字是舊判準 —— 「1KB 以下行為與從前一樣」是這輪修法的契約。

    ★ 10-01:長稿在框裡可能只露出尾段(框有高度上限、會捲),所以頭**或尾**指紋都算;
      框線字一併剝掉(copilot 的框每列前面都有 `┃`)。

    ★ 為什麼比的是**框內**不是整張畫面:整張畫面含捲動區,而捲動區可能早就
      有同一段字(我重送、或對方引述過我的句子)—— 那正是引擎 BEFORE 閘要擋
      的假綠。**空的輸入框裝不下捲動區的回音**,所以把比對縮到框內是
      *加一條正面證據*,不是放寬那道閘。
    ★ 指紋太短就回 False(不是 True):短字串在任何框裡都可能恆真,而恆真的
      判準拿來按別人的 Enter 是最壞的一種。缺證據一律倒向不按。
    """
    fp = head_fingerprint(body)
    if len(fp) < MIN_FP:
        return False
    if long_paste is None:
        long_paste = len((body or "").encode("utf-8")) >= LONG_PASTE_BYTES
    if not long_paste:
        return fp in ENGINE_SPACE.sub("", composer_text or "")
    box = screen_norm(composer_text)
    if fp in box:
        return True
    tail = tail_fingerprint(body)
    return len(tail) >= MIN_FP and tail in box


def parse(text):
    """畫面文字 → 佔位符清單(依出現順序,**保留重複**)。

    ★ 回 list 不回 set:兩枚一模一樣的佔位符是「兩枚」。用 set 會把
      SPEC N2(新增兩枚以上必須停手)那一格靜默壓成 1 枚而通過。
    """
    out = []
    for m in PLACEHOLDER_RE.finditer(text or ""):
        if m.group("lines") is not None:
            out.append({"raw": m.group(0), "dialect": "claude",
                        "quantity": "lines", "value": int(m.group("lines"))})
        elif m.group("bare") is not None:
            out.append({"raw": m.group(0), "dialect": "claude",
                        "quantity": "none", "value": None})
        elif m.group("chars") is not None:
            out.append({"raw": m.group(0), "dialect": "codex",
                        "quantity": "chars", "value": int(m.group("chars"))})
        elif m.group("grok_lines") is not None:
            out.append({"raw": m.group(0), "dialect": "grok",
                        "quantity": "lines",
                        "value": int(m.group("grok_lines"))})
        elif m.group("grok_kb") is not None:
            out.append({"raw": m.group(0), "dialect": "grok",
                        "quantity": "kb",
                        "value": int(m.group("grok_kb"))})
        elif m.group("grok_mb") is not None:
            out.append({"raw": m.group(0), "dialect": "grok",
                        "quantity": "mb",
                        "value": float(m.group("grok_mb"))})
        elif m.group("cp_lines") is not None:
            out.append({"raw": m.group(0), "dialect": "copilot",
                        "quantity": "lines",
                        "value": int(m.group("cp_lines"))})
        elif m.group("cp_saved_kb") is not None:
            out.append({"raw": m.group(0), "dialect": "copilot",
                        "quantity": "saved_kb",
                        "value": float(m.group("cp_saved_kb"))})
        else:
            # ★ 2026-09-02:這裡原本是 `else: → codex`。加 grok 那一列時,
            #   新方言**天生落進 codex 分支**,int(None) 當場炸。炸是好的;
            #   若 codex 分支剛好不取值,新方言就會被**默默標成 codex**。
            #   ⇒ 文法表加一列必須同時加一個具名分支;沒人認得就具名地吵。
            raise ValueError(
                "dm_paste:佔位符文法表有這一式,parse 卻沒有對應分支:%r"
                % (m.group(0),))
    return out


def normalize_before(raw):
    """引擎印的那一行 → 三態(null / [] / 清單)。

    ★ 三態不可互相坍塌:`null`=沒抓到(儀器不通,必須停手)、`[]`=抓到了而框是
      空的(可以往前走)。舊碼把空字串也映成 null,於是**正常送信每次都是 null**,
      尊重規格的下游永遠不能往前走 → 下游只好學會忽略 null → 真正沒抓到那次
      也跟著過。**一個永遠會紅的閘會訓練所有人忽略紅。**
    """
    if raw in ("__aib_absent__", "unknown", "", None):
        return None                       # 引擎沒報這一行=沒抓到
    if raw.startswith("NONE"):
        return []                         # 哨兵:抓到了,框內無佔位符
    items = [p["raw"] for p in parse(raw)]
    # 分不出來的時候要說分不出,不要說沒有:舊引擎的 `[(無)]` 解不出型別 → null。
    return items or None


def decide(before, after_text, after_capture_ok, expected_chars, expected_lines,
           draft_guard_on=True, before_composer="unknown", composer_mine=None):
    """rc5 之後該不該補 Enter。→ dict(action, rc, stage, detail, ...)

    action: enter(送 Enter) / stop(停手,絕不重送) / unverified(維持已送未驗)
    """
    def verdict(action, rc, stage, detail, **extra):
        out = {"action": action, "rc": rc, "stage": stage, "detail": detail,
               "n_after": None if not after_capture_ok else len(parse(after_text)),
               "expected_chars": expected_chars, "expected_lines": expected_lines}
        out.update(extra)
        return out

    # 0. 我自己的量測失敗 → 分不出,停手(**取不到畫面不等於畫面乾淨**)
    if not after_capture_ok:
        return verdict("stop", 4, "undecidable",
                       "貼後回讀失敗:取不到畫面不等於畫面乾淨,不補 Enter、亦不得重送")

    # 0.4 grok 檔案提及選單(2026-09-02 現量)。必須在佔位符判定之前:
    #   選單開著時框裡仍可能出現摺疊佔位符 → 舊判準會 action=enter、收據
    #   宣稱送到,而 Enter 實際選中反白檔、整份稿被吃掉。
    #   ★ 失效若安靜=收據說 delivered。所以這條紅在「有佔位符仍必須停」。
    if looks_file_mention_picker(after_text):
        return verdict("stop", 4, "mention-picker",
                       "貼後畫面是 grok 檔案提及選單:稿可能已被選單吃掉,"
                       "不補 Enter、不得宣稱送到")

    # ★★ 0.5 貼前守衛被關掉 = 「框是空的」這條腿沒有儀器(a seat N1b / a seat 同時提)
    #   `before` 只由引擎的**佔位符正則**產生,它看不見「別人打到一半的純文字」。
    #   看得見純文字的是引擎 4b 的卡稿守衛(composer_state),而
    #   AIB_DM_SKIP_DRAFT_GUARD=1 會把它整個關掉。
    #   → 那個旗標一開,`before == []` 的意思就從「框是空的」退化成
    #     「框裡沒有佔位符」——**同一個值,兩個意思,而我原本讀的是強的那個。**
    #   ★ 自動 Enter 的爆炸半徑與人不同:同一次漏報,以前是貼到別人稿上而人
    #     看得到,現在是貼上去**並且直接送出**(a seat 原話)。所以這裡不放行。
    if not draft_guard_on:
        return verdict("stop", 4, "guard-disabled",
                       "AIB_DM_SKIP_DRAFT_GUARD=1:唯一看得見純文字草稿的守衛已被關掉,"
                       "貼前是否為空無從證明,不自動 Enter")

    # ★★★ 0.6「框是空的」必須有**正面證據**,不得由 before_placeholders == [] 推出來
    #   (a seat 釘 N1c;a seat 真投遞獨立指同一處)。
    #   `before == []` 只證明「框裡沒有**佔位符**」——佔位符正則看不見純文字。
    #   看得見的是 composer_state,而它對 codex 的**空框**
    #   (`› Ask Codex to do anything`)回 unknown,對 `❯ ` 才回 empty。
    #   → 於是「守衛開著」也不夠:codex 席 + 有人正在打字 = 引擎判 unknown →
    #     走 fallback 樣式比對 → 比不中就往下貼(STRICT_UNKNOWN 預設關)→
    #     我這裡若仍憑 `[]` 就會按下別人的 Enter。
    #   ★ 這一條是 N1b 的**去前提版**:N1b 以為前提是「守衛被關掉」,
    #     而真正的前提只是「沒有人正面看見那個框是空的」。
    #     **一個缺陷的前提條件被寫得比實際窄,修法就會只蓋住它的一半。**
    if before_composer not in ("empty", "suggestion"):
        # ★ 訊息要說「這不是你做錯了」(a seat 01:05 提):收到 rc=4 的人第一反應
        #   是回去找自己哪裡寫錯。unknown 幾乎一定是「那一席是 codex」——
        #   composer_state 只認得 `❯`,對 codex 的 `›` 定位不到輸入框。
        #   **一個不告訴你下一步的錯誤碼,會讓每個收到它的人各自重查一次同一件事。**
        why = ("(這一席的輸入框 composer_state 認不出來——幾乎一定是 codex 席:"
               "它的提示符是 `›` 而判定器只認得 `❯`。**本路徑對它不觸發不是你做錯了**;"
               "請人工 capture 比對是自己的稿再補 Enter)"
               if before_composer == "unknown" else "")
        return verdict("stop", 4, "before-not-empty",
                       "貼前輸入框狀態=%s:沒有正面證據證明貼之前那個框是空的"
                       "(`沒有佔位符` 不等於 `是空的`),不自動 Enter%s"
                       % (before_composer, why))

    # 1. BEFORE 三態:null=儀器不通
    if before is None:
        return verdict("stop", 4, "undecidable",
                       "貼前狀態未知(before_placeholders=null):沒有對照就證不出這枚是我造成的")

    # 2. BEFORE 非空=框裡本來就有東西,不是我的可以送的
    if before:
        return verdict("stop", 4, "foreign-draft",
                       "貼前框內已有佔位符(%s):別人的稿不由我按 Enter" % " ".join(before))

    found = parse(after_text)
    queued = any(mk in after_text for mk in QUEUE_MARKERS)

    # 3. 一枚都沒有:貼進去了卻看不到跡象。**不宣稱可重送**——維持已送未驗。
    #    ★ 這一格與 SPEC §一表格第三列(0 個 → rc=2 可安全重送)有出入,是刻意的:
    #      §三 給 rc=2 的三個例子(R-30 擋 / 目標不明 / 死窗)**全是貼之前就被擋**;
    #      而走到這裡 `paste-buffer` 已經 rc=0 成功了,「框是空的」至少有兩種世界
    #      ——(a) 對方 CLI 未就緒整段吞掉(重送安全)、(b) 已被送出(重送=兩份)。
    #      分不出來就不給「可安全重送」這個承諾。既有釘子
    #      test_unverified_transport_maps_to_rc3 也正好釘在這一格。
    #      **這一列候 a seat 裁,不是我自己放寬的。**
    if not found:
        # ★★ 2026-09-24(the maintainer 直令「強化 dm 之後的 enter」):這一格原本一律不按,
        #   而**短稿根本不產生佔位符** —— claude/codex 只對長貼印
        #   `[Pasted …]`,一行短句是純文字進框,佔位符正則看不見(檔頭早有這句)。
        #   於是「送短句」在結構上永遠落到這裡:稿明明在對方框裡、看得見,
        #   引擎卻說「無任何跡象」而不按 Enter。09-24 對 a seat 實測重現:
        #   貼前 composer=empty、畫面最後一行逐字是我的稿、rc 仍 3。
        #
        #   ★ 這裡**不是**把檢查拿掉,是補一條原本不存在的正面證據:
        #     貼前 composer=empty(0.6 那道閘已經要求的正面證據)
        #     ∧ 貼後**框內那一行**逐字含本稿頭指紋
        #     ⇒ 框裡那張稿只可能是我剛貼的。
        #   為什麼引擎的頭指紋路徑救不了這一格:它比的是**整張畫面含捲動區**,
        #   我重送同一句、或對方引述過我的句子,指紋就已在 BEFORE 裡 ⇒ 閘正確地
        #   否決 ⇒ 沒有後備。**空的輸入框裝不下捲動區的回音**,所以把同一個
        #   指紋縮到框內量,恰好繞過那個假綠而不削弱它。
        #   ★ 缺證據一律倒向不按:composer_mine 非 True(含 None=沒量到)就走舊路。
        if composer_mine is True and before_composer == "empty":
            return verdict("enter", 0, "queued" if queued else "delivered",
                           "貼後框內無佔位符(短稿不產生佔位符),但貼前 composer=empty "
                           "且框內逐字含本稿頭指紋 ⇒ 框裡是我剛貼的稿",
                           queued=queued)
        return verdict("unverified", 3, "sent-unverified",
                       "貼後框內無任何佔位符:已貼但無跡象,**勿重貼**(疊稿風險),"
                       "請 capture 確認框空後才重送", queued=queued)

    # 4. 總數必須恰一(見檔頭:這一條同時蓋住 BEFORE 解析器的方言盲區)
    if len(found) != 1:
        return verdict("stop", 4, "ambiguous",
                       "貼後框內有 %d 枚佔位符,分不出哪一枚是我的:停手,絕不重送"
                       % len(found), queued=queued)

    ph = found[0]

    # 5. 數量必須命中**工具自己算出來的**集合
    #    ★★ 不准寫死 45。08-21 同一席同一晚:稿 1237 → 框 1282(差 45,含抬頭)、
    #      稿 1163 → 框 1163(差 0)。兩筆連對就被當成定值,而白名單第三條建在
    #      它上面——**那個差值是抬頭長度,是結構不是常數**;抬頭由本工具自己組,
    #      長度它自己算得出來,不該讓使用者去推。
    if ph["quantity"] == "chars":
        if ph["value"] in expected_chars:
            return verdict("enter", 0, "queued" if queued else "delivered",
                           "佔位符字元數 %d 命中本工具算出的集合 %s"
                           % (ph["value"], expected_chars),
                           placeholder=ph["raw"], queued=queued)
        return verdict("stop", 4, "size-mismatch",
                       "佔位符字元數 %d 不在本工具算出的集合 %s:停手,絕不重送"
                       % (ph["value"], expected_chars),
                       placeholder=ph["raw"], queued=queued)

    if ph["quantity"] == "lines":
        if ph["value"] + 1 == expected_lines:
            return verdict("enter", 0, "queued" if queued else "delivered",
                           "佔位符 +%d 行,K+1 命中稿件 %d 行"
                           % (ph["value"], expected_lines),
                           placeholder=ph["raw"], queued=queued)
        return verdict("stop", 4, "size-mismatch",
                       "佔位符 +%d 行 vs 稿件 %d 行:停手,絕不重送"
                       % (ph["value"], expected_lines),
                       placeholder=ph["raw"], queued=queued)

    if ph["quantity"] in ("kb", "mb", "saved_kb"):
        # ★ KB/MB 形驗不了行數。候選 (a) 用 bytes 誤差自動 Enter:
        #   失效安靜面=1000/1024 或 CJK 字元≠位元組對錯,誤按別人的稿。
        #   候選 (b) 認得就具名停手:失效若正則漏了,才會再靜默。
        #   採 (b):大稿正是最不該猜的時候;巡查看到 kb-fold 而不是「閒置」。
        return verdict("stop", 4, "kb-fold",
                       "佔位符是大小單位形(%s %s):對不上行數,不自動 Enter"
                       % (ph["quantity"], ph["raw"]),
                       placeholder=ph["raw"], queued=queued)

    # 6. 裸 `[Pasted text #83]`:**沒有任何數量可比**。
    #    ★ `#N` 遞增只是弱證據——它是條件一的子集不是替代,擋不住別席在同窗
    #      剛貼了一份。人在 08-21 那十幾次裡也**從來沒有**在這種形狀上按過。
    return verdict("stop", 4, "no-quantity",
                   "佔位符 %s 不帶行數/字元數,無正面證據證明那是我的稿:停手"
                   % ph["raw"], placeholder=ph["raw"], queued=queued)


def repress_verdict(before, after_raws, confirm_text, confirm_capture_ok,
                    placeholder):
    """Enter 按過而框還沒清:這一枚可不可以**由我**再按一次?→ dict(action, reason, detail)

    action: ``repress``(可以再按一次) / ``stop``(不再按,維持現行處置)

    正本 FINDING sha a18445c9 第 1 條 + 艦長 a6b2e 09-04 加的必要條件。三條腿,
    少一條都不准按:

      (甲) 這一枚**在貼前不存在**(艦長加的那條)。
      (乙) 這一枚**貼後出現過**——我按的必須是我造成的那個東西。
      (丙) **現在**框裡的佔位符恰好就是這一枚(集合相等)。

    ★ 誠實話,寫在碼裡而不是只寫在收據裡:(甲) 在**現行呼叫鏈**上是被上游蘊含的
      —— `decide` 要求 ``before == []`` 才會走到 Enter,所以走到這裡時它必然成立。
      **它今天擋不到任何一個真實輸入**,它的臂因此是單元格不是整合格
      (`tests/test-dm-repress-own-draft-c4.py::test_d_unit_*`)。留著的理由是:
      上游那條 ``before == []`` 哪天被放寬,這把鎖還在。把一條恆真的判準寫成
      「臂證明了它在守」才是缺陷;寫下它現在恆真不是。

    ★ 現在真的擋得到東西的是 (丙):第一次 Enter 與回讀之間,別席可能貼進來一枚。
      Enter 送出的是**整個框**,所以那時再按一次就是替別人送出他沒寫完的稿。
    """
    if not confirm_capture_ok:
        return {"action": "stop", "reason": "confirm-capture-failed",
                "detail": "按完的回讀失敗:取不到畫面不等於畫面乾淨,不重按"}
    if not placeholder:
        return {"action": "stop", "reason": "no-placeholder",
                "detail": "沒有一枚具名的佔位符可以指認,不重按"}
    if before is None:
        return {"action": "stop", "reason": "before-unknown",
                "detail": "貼前狀態未知:沒有對照就證不出這枚是我造成的,不重按"}
    if placeholder in list(before):
        return {"action": "stop", "reason": "existed-before-paste",
                "detail": "這一枚在貼前就已經在框裡(%s):不是本次自己貼的,"
                          "重按等於幫別人按 Enter" % placeholder}
    if placeholder not in list(after_raws or []):
        return {"action": "stop", "reason": "not-in-after",
                "detail": "這一枚不在貼後那次回讀裡:證不出它是本次貼進去的,不重按"}
    now = [p["raw"] for p in parse(confirm_text)]
    if not now:
        return {"action": "stop", "reason": "gone",
                "detail": "框裡已經沒有這一枚了:沒有卡住的稿要重按"}
    if now != [placeholder]:
        return {"action": "stop", "reason": "foreign-joined",
                "detail": "框裡現在是 %s,不只有我那一枚(%s):Enter 送的是整個框,"
                          "重按會把別人的稿一起送出" % (" ".join(now), placeholder)}
    return {"action": "repress", "reason": "own-draft-stuck",
            "detail": "本次自己貼的那一枚(%s)在 Enter 之後仍在框內:摺疊未處理完、"
                      "Enter 被吃掉的形狀,重按一次" % placeholder}


def residual(before, after_text, after_capture_ok):
    """非 rc5 的失敗路徑:目標端**可觀測地**零新增嗎?→ int 或 None(=分不出)。

    ★ rc=2 的承諾是「可安全重送」。給出這個承諾之前必須**看過目標端**——
      只信 lint 或血緣閘的收據不算:那是寫在我這一端的紙,不是對方框裡的事實
      (SPEC §五 N5 逐字要求「不能只信 lint 回報」)。
    ★ 分不出的時候回 None,呼叫端一律落 rc=4:**「分不出」與「零」不共用出口。**
    """
    if not after_capture_ok:
        return None
    found = [p["raw"] for p in parse(after_text)]
    if before is None:
        # 沒有貼前對照:只能用總數。框裡本來就有的舊佔位符會被算進來,
        # 方向是保守的(多算 → 落 rc=4 不重送),不是放行。
        return len(found)
    left = list(before)
    extra = 0
    for raw in found:
        if raw in left:
            left.remove(raw)      # 多重集差:兩枚一樣的只抵銷一枚
        else:
            extra += 1
    return extra


def main(argv):
    if len(argv) < 2:
        print("用法:dm_paste.py normalize-before <raw> | decide <args...> | "
              "raws | repress <args...> | residual <args...> | "
              "looks-mention-picker | shield-at",
              file=sys.stderr)
        return 2
    cmd = argv[1]
    if cmd == "looks-mention-picker":
        return 0 if looks_file_mention_picker(sys.stdin.read()) else 1
    if cmd == "shield-at":
        sys.stdout.write(shield_token_at_signs(sys.stdin.read()))
        return 0
    if cmd == "normalize-before":
        raw = argv[2] if len(argv) > 2 else ""
        print(json.dumps(normalize_before(raw), ensure_ascii=False))
        return 0
    if cmd == "decide":
        # decide <before_raw> <after_capture_rc> <expected_chars_csv> <expected_lines>
        # 畫面文字走 stdin(零轉義面,與引擎 load-buffer 同紀律)。
        before_raw, cap_rc, chars_csv, lines = argv[2], argv[3], argv[4], argv[5]
        guard_on = (argv[6] if len(argv) > 6 else "1") != "0"
        before_cs = argv[7] if len(argv) > 7 else "unknown"
        after_text = sys.stdin.read()
        expected = sorted({int(x) for x in chars_csv.split(",") if x.strip()})
        # ★ 新證據走**環境變數指到的檔**,不走 argv,也不佔用 stdin:
        #   (a) stdin 已經給畫面了,第二份輸入就得發明分隔符;
        #   (b) 框內文字與正文都可能含引號/換行/@,走 argv 要轉義,而本鏈
        #       早有「長字串走檔不走 argv」的疤。
        #   兩個變數**都沒設就完全照舊**(composer_mine=None → 走原本那條),
        #   所以舊呼叫端一個位元組都不用動,fail-closed 也原樣保留。
        composer_mine = None
        _ct_f = os.environ.get("AIB_DM_AE_COMPOSER_TEXT_FILE") or ""
        _bd_f = os.environ.get("AIB_DM_AE_BODY_FILE") or ""
        if _ct_f and _bd_f:
            try:
                with open(_ct_f, encoding="utf-8", errors="replace") as _f:
                    _ct = _f.read()
                with open(_bd_f, encoding="utf-8", errors="replace") as _f:
                    _bd = _f.read()
                composer_mine = composer_holds_draft(_ct, _bd)
            except OSError:
                composer_mine = None   # 讀不到=沒量到,不是「不是我的」
        out = decide(normalize_before(before_raw), after_text, cap_rc == "0",
                     expected, int(lines), draft_guard_on=guard_on,
                     before_composer=before_cs, composer_mine=composer_mine)
        print(json.dumps(out, ensure_ascii=False))
        return 0
    if cmd == "raws":
        # 畫面走 stdin → 佔位符 raw 的 JSON 陣列(給 shell 端當中間值,
        # 免得一次要餵兩張畫面進同一個 stdin 而得發明分隔符)。
        print(json.dumps([p["raw"] for p in parse(sys.stdin.read())],
                         ensure_ascii=False))
        return 0
    if cmd == "repress":
        # repress <before_raw> <after_raws_json> <placeholder> <confirm_capture_rc>
        # 按完的畫面走 stdin。
        before_raw, after_json, ph, cap_rc = argv[2], argv[3], argv[4], argv[5]
        try:
            after_raws = json.loads(after_json)
        except (TypeError, ValueError):
            after_raws = None
        out = repress_verdict(normalize_before(before_raw), after_raws,
                              sys.stdin.read(), cap_rc == "0", ph)
        print(json.dumps(out, ensure_ascii=False))
        return 0
    if cmd == "landed":
        # landed <body_file> ; 畫面走 stdin → 印 yes / no(rc 0)。讀不到稿檔 ⇒ yes
        #   (分不出就往「不再投」那邊倒:這支的答案只被拿來擋重排,不拿來按 Enter)。
        try:
            with open(argv[2], encoding="utf-8", errors="replace") as _f:
                body = _f.read()
        except (IndexError, OSError):
            print("yes")
            return 0
        print("yes" if landed_on_screen(sys.stdin.read(), body) else "no")
        return 0
    if cmd == "composer-match":
        # composer-match <body_file> ; 框內文字走 stdin → 印 exact / mine / no。
        #   讀不到稿檔 ⇒ no(這支的 exact 會被拿來再按一次 Enter,缺證據一律倒向不按)。
        try:
            with open(argv[2], encoding="utf-8", errors="replace") as _f:
                body = _f.read()
        except (IndexError, OSError):
            print("no")
            return 0
        box = sys.stdin.read()
        if composer_is_exactly_mine(box, body):
            print("exact")
        elif composer_holds_draft(box, body, long_paste=True):   # 只有長稿路徑叫這支
            print("mine")
        else:
            print("no")
        return 0
    if cmd == "residual":
        # residual <before_raw> <after_capture_rc>  ; 畫面走 stdin
        before_raw, cap_rc = argv[2], argv[3]
        n = residual(normalize_before(before_raw), sys.stdin.read(), cap_rc == "0")
        print("unknown" if n is None else n)
        return 0
    print("未知子命令:%s" % cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
