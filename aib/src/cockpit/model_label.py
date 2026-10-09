#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""模型名的顯示形(the maintainer 2026-09-05 令:三字碼 CLA 改成顯示目前用的模型)。

★ **唯一正本**。右欄清單(`render_lib`)與抬頭(`tools/hdr/aib-hdr.py`)都讀這一支;
  兩邊各抄一份規則的話,改一邊另一邊不會有任何一格紅 —— 那個病在本倉犯過三次。

★ 資料**早就在量了**,缺的一直只是顯示:`~/.ez2/ctx/<uuid>.pct` 的 `model` 欄。
  09-05 現查 821 檔的分佈 —— claude 401 / codex 362 / agy 42 / grok 15,
  四個引擎各有自己的 statusline 或 signals 寫端,不是只有 claude。

★ the maintainer 的規則(「只要 Opus 5,其他 ai 也是類似這個做法」)= **去掉尾端那段括號修飾**:
      Opus 5 (1M context)   → Opus 5
      Gemini 3.1 Pro (High) → Gemini 3.1 Pro
      GPT-OSS 120B (Medium) → GPT-OSS 120B
      gpt-5.6-sol           → gpt-5.6-sol   ← 不動
  `sol` / `luna` 不是括號修飾,而且它們是**不同的模型**不是同一個模型的變體;
  併掉會讓兩個模型在畫面上同形。修飾與版本的差別就在這裡,不是字數。

★ 量不到的時候回 `None`,呼叫端退回三字碼(CLA/CDX/AGY/GRK)。
  **退回值刻意與正常值長得不一樣**:看到 `CLA` 就是「引擎知道、模型量不到」,
  看到 `Opus 5` 就是「兩個都量到了」。兩者同形的話,statusline 掛掉沒有人會發現。
"""

import re
import unicodedata

#: 尾端括號修飾。只吃**最後一段**且不吃巢狀,避免把 `GPT-OSS 120B (Medium)` 這種
#: 正常形狀以外的東西一起吃掉。
_TRAILING_PAREN = re.compile(r"\s*[\(（][^()（）]*[\)）]\s*$")

#: 量不到時 statusline 寫進 `.pct` 的字面(09-05 現查 821 檔中出現 112 次)。
UNKNOWN_LITERALS = {"", "?", "-", "--", "none", "null", "unknown"}

#: 顯示上限。右欄寬度由內容導出(`required_ai_pane_width`),
#: 沒有上限的話一個異常長的模型名會把整個右欄撐開而**沒有任何一格會紅**。
MAX_CELLS = 20
ELLIPSIS = "…"


def cells(text):
    """顯示格數:CJK 與全形算 2 格。★ 不用 len():用 len() 算出來的寬度只有一半,
    結果是「看起來偏左」而不是報錯 —— 那正是最難查的一種失效。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def display_model(raw):
    """`.pct` 的 `model` 欄 → 畫面上的字;量不到回 `None`。"""
    if raw is None:
        return None
    text = " ".join(str(raw).split())
    if text.lower() in UNKNOWN_LITERALS:
        return None
    text = _TRAILING_PAREN.sub("", text).strip()
    # 橋接席 stub 模型名（sand-bridge / grokbot-bridge）畫面只顯示 provider id；
    # 「-bridge」是實作標籤，不是要給人看的型號。
    if text.endswith("-bridge"):
        stem = text[: -len("-bridge")]
        if stem in {"sand", "grokbot", "codexbot", "claudebot", "musebot"}:
            text = stem
    if not text or text.lower() in UNKNOWN_LITERALS:
        return None
    if cells(text) <= MAX_CELLS:
        return text
    # 超長就截並留 `…`:**截掉要看得見**。靜默截斷與「這個模型就叫這個名字」同形。
    out = ""
    for ch in text:
        if cells(out + ch) > MAX_CELLS - 1:
            break
        out += ch
    return out + ELLIPSIS


def badge_text(raw_model, engine_letter):
    """畫面上那一格:有模型就用模型,沒有就退回三字碼。

    ★ 這裡是**唯一**決定「顯示哪一個」的地方。呼叫端不得自己再判一次 —
      判兩次的兩處遲早會不一致,而不一致的樣子是「有些席顯示模型有些顯示 CLA」,
      看起來像資料來源壞了。
    """
    return display_model(raw_model) or engine_letter


# ══ L9:觀測到的模型 ≠ 開席要求的模型 ══════════════════════════════════════
# 機制先做,**畫面上用哪個字面候 the maintainer**(見 render_lib.MODEL_DRIFT_MARK)。
#
# ★★ 這一段最容易做錯的地方,是先量出來才知道的:
#   `model_requested` 存的是**模型 id**(`claude-opus-5`),`.pct` 的 `model` 存的是
#   **顯示名**(`Opus 5 (1M context)`)。09-05 現查 45 對可配對的資料,
#   直接字串比對會把 **34/45(76%)** 判成漂移,而真正跑錯模型的只有 5 對。
#   ⇒ 沒有正規化的話,這個記號會**永遠亮著**,而永遠亮著的警告不帶資訊
#     (承 cooldown 那條:swap 門檻永遠 warn 所以等級永遠停在 🟡)。
#
# ★ 三態不是兩態。`unknown` 必須與 `same` 分開:現查 114 個席位檔裡
#   **62 個根本沒有 `model_requested`**。把「問不出來」併進「一樣」的話,
#   量不到與確認相同在畫面上逐字相同,而前者其實什麼都沒證。
#   ⇒ 呼叫端只有拿到 DRIFT_DIFFERENT 才可以標記;unknown 一律不標。

DRIFT_SAME = "same"
DRIFT_DIFFERENT = "drift"
DRIFT_UNKNOWN = "unknown"

#: 只去「等級/努力」修飾,不去版本。`sol`/`luna` 是不同模型不是修飾(見檔頭),
#: 所以不在這裡;去掉它們會讓兩個不同的模型判成同一個。
_LEVEL_MODIFIERS = {"high", "low", "medium"}


def normalized_model_key(text, provider=None):
    """把「模型 id」或「顯示名」收斂成可比較的鍵;收斂不出來回 `None`。

    `claude-opus-5` 與 `Opus 5` → `opus5`;`gemini-3.1-pro-high` 與
    `Gemini 3.1 Pro` → `gemini31pro`。

    ★ provider 前綴**兩邊都要去**。只去 id 那一側的話,`grok-4.6` 會收斂成 `46`
      而觀測到的 `grok-4.6` 收斂成 `grok46`,兩者不等 ⇒ 生出一個假漂移。
    ★ 已知限制(寫下來免得下次有人以為它比實際強):像 grok 這種「廠牌+版本」的
      命名,去掉前綴後鍵會退化成純數字(`4.6` → `46`)。同 provider 底下版本號
      相同而模型不同的兩支會撞在一起。現查的四家沒有這種形狀;真的出現時
      要改的是這裡,不是呼叫端各自加特例。
    """
    if text is None:
        return None
    lowered = " ".join(str(text).split()).lower()
    if not lowered or lowered in UNKNOWN_LITERALS:
        return None
    # 去括號修飾(`(1M context)` / `(High)`)——與 display_model 同一件事,
    # 但這裡連句中的括號也去,因為要比的是「哪一個模型」不是「怎麼顯示」。
    lowered = re.sub(r"[\(（][^()（）]*[\)）]", " ", lowered)
    tokens = [t for t in re.split(r"[^a-z0-9.]+", lowered) if t]
    if provider:
        prov = str(provider).strip().lower()
        if prov and tokens and tokens[0] == prov:
            tokens = tokens[1:]
    tokens = [t for t in tokens if t not in _LEVEL_MODIFIERS]
    key = re.sub(r"[.\-]", "", "".join(tokens))
    return key or None


def model_drift_state(requested, observed, provider=None):
    """開席要求的模型 vs provider 觀測到的模型:回 same / drift / unknown。

    ★ fail-closed 的方向在這裡是 **unknown**,不是 drift:
      問不出來就說問不出來。把「讀不到」當成「跑錯了」會生出一整排假記號,
      而那些席其實只是沒有 `model_requested` 這個欄位(現查 62/114)。
    """
    kr = normalized_model_key(requested, provider)
    ko = normalized_model_key(observed, provider)
    if kr is None or ko is None:
        return DRIFT_UNKNOWN
    return DRIFT_SAME if kr == ko else DRIFT_DIFFERENT


def qualified_model_key(text, provider):
    """跨 provider 的模型鍵:`"<provider>:<key>"`;任一邊缺就回 `None`。

    ★★ **R3 存在的理由,是一個現機就成立的碰撞,不是假想的**:
      `normalized_model_key()` 會去掉 provider 前綴(兩邊都去,否則
      `grok-4.6` 對 `grok-4.6` 會判成漂移)。那個設計在**漂移判定**裡是對的 ——
      要求與觀測本來就來自同一席、同一個 provider,前綴一定相同。
      但把同一把尺拿去當**跨 provider 的帳本鍵**就不成立了:
        現查 `gpt-5.6-sol` 同時被 `codex` 與 `copilot` 要求,兩者的裸鍵都是
        `gpt56sol` ⇒ 用裸鍵當 join key 會把兩家的列合成一列。
        構造例更清楚:`grok-4.6` / `codex-4.6` / `agy-4.6` 的裸鍵全是 `46`。
      ⇒ 兩個消費者要的是**兩把尺**,不是同一把。這一支給 R2 路由帳本用;
        漂移判定繼續用裸的那一支。

    ★ 用哪一支的判準寫成一句話:**比較的兩邊是不是保證同一個 provider**。
      是 → `normalized_model_key()`;不是 → 這一支。
    """
    if not provider:
        return None
    key = normalized_model_key(text, provider)
    if key is None:
        return None
    prov = " ".join(str(provider).split()).lower()
    return "%s:%s" % (prov, key) if prov else None
