#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""回報裡的內部代號閘(2026-09-08,the maintainer 直令「做成程式,不要只靠記憶」)。

## 它擋什麼

`docs/REPORTING-RULES.md` 第二、三條:**代號不能單獨成為回報內容**;
它可以放在白話說明**後面**當查詢依據。

    不合格   R2 已排除,接著處理 L14。
    合格     登入失敗的問題已修正(R2)。接著處理付款後沒有寄出通知的問題(L14)。

## 為什麼需要一道閘

那份規則 2026-09-07 就存在,`AGENTS.md` 第 2b 項也指著它 —— 而 09-08 那一席
(我)整天沒照做,讓 the maintainer 必須反覆問「所以結果是?」。
★ **同一天還有另外兩個同形實例**:席位記憶裡「母體是用性質圈的還是名字圈的」
與「量測與動作要分兩條指令」,兩條都是我自己寫的、都在載入狀態、都被我違反。
⇒ **規則存在 + 有索引 + 我讀過 ⇒ 仍然擋不住。** 提醒層與硬條件層要各做一次。

## 判準(選會吵的那一種)

代號出現時,必須**至少滿足一件**:
  (a) 被括號包住 —— `(R2)`、`(§82)`、`(B8)`;或
  (b) 同一行中,它**前面**至少有 MIN_PROSE 個中日文字元(先講人話再給代號)。

★ 為什麼不用「同一行有沒有中文」:我的每一行都有中文,那個判準對所有輸入都成立,
  **恆真的判準不帶資訊**。改成「代號**前面**要有白話」才分得出
  「R2 已排除」與「登入失敗已修正(R2)」。

## 不算代號的(必落空端,少一項就會對正確的回報也紅)

* 反引號內的東西:`B8`、`tests/test-b8-machine-rules.py` —— 那是**指令與檔名**,
  規則明說「檔名、提交編號與指令輸出」在協助定位時可以提供。
★ **原本還排除了版本號與 sha,2026-09-08 用刀量過之後拿掉了**:代號家族要求
  **大寫字母**開頭,而 `v0.57.2-alpha.1` 與 `be11c8e6` 根本配不到 —— 那兩條排除
  **任何輸入都不會用到**。一條永遠不會開火的排除,與必要的排除在碼上長得一樣,
  而它會讓下一個人以為那裡有保護。**拿掉比留著誠實。**

★ 2026-09-08 實付:我寫的另一道判準把合法的 `2>&1` 一起擋掉,**對正確的碼也紅**。
  一道沒有被必落空端驗過的判準,比沒有那道判準更糟 —— 它會訓練人繞過它。
"""
from __future__ import annotations

import re

#: 代號家族。逐族列舉而不是「大寫字母+數字」,因為後者會吃掉 `py3.12`、`M1`、`UTF8`。
CODE = re.compile(
    r"(?:§\d+(?:\.\d+)*"                       # §82 §77.6
    r"|\b(?:EG|ED|EH|E)\d+(?:\.\d+)*[a-z]?\b"  # EG.8 ED.11 E2.4
    r"|\b(?:R|D|L|B|G|T|U|Q|K|C|A)\d+[a-z]?\b" # R2 D38 L14 B8 T1a
    r")"
)
#: 代號前面至少要有幾個中日文字元才算「先講了人話」。
MIN_PROSE = 6

_CJK = re.compile(r"[぀-ヿ一-鿿]")
_BACKTICK = re.compile(r"`[^`]*`")


def _mask(line: str) -> str:
    """把「不算代號」的區段換成等長空白,位置因此不變(位置要拿來算前文長度)。"""
    def blank(m):
        return " " * (m.end() - m.start())
    return _BACKTICK.sub(blank, line)


#: 樹狀列的行首:框線/樹枝/狀態符/空白。它後面第一個 token 是**項目編號**不是代號。
_TREE_HEAD = re.compile(
    r"^[\s\u00a0│├╰╭─▸✅🔄🟡⏸🔴⛔]*"
)


def _skip_item_id(masked: str) -> str:
    """把樹狀列的**項目編號**遮掉(`G2.1`、`R1.3`)。

    ★ 2026-09-08 這一格是被**這個倉自己的範例回報**逼出來的:
      `tools/report/report-input.example.json` 用 `G2.1` 當項目編號,
      而 `G2.1 狀態符抽成單源` **不是**規則要擋的東西 —— 編號後面**就是**白話標題。
      我第一版沒有這一格,於是那道閘把倉裡既有的合法範例判紅。
      ⇒ **一道會擋到正確輸入的閘,比沒有那道閘更糟**:它會訓練人繞過它。
      這是同一天第二次踩(前一次是把合法的 `2>&1` 一起擋掉)。
    """
    head = _TREE_HEAD.match(masked)
    if not head or head.end() == 0:
        return masked
    rest = masked[head.end():]
    m = CODE.match(rest)
    if not m:
        return masked
    return masked[:head.end()] + " " * (m.end() - m.start()) + rest[m.end():]


#: 四段分隔線(進度/摘要/反思/收官)。它之後的內容才是**人當句子讀**的散文。
_SEP = re.compile(r"~~.*~~")


def prose_lines(text: str):
    """只回**散文段**的行(第一條分隔線之後)。

    ★ 範圍是選出來的,不是省事:規則二、三舉的例子全是句子。
      樹狀區塊裡那兩種東西**不是**規則要擋的:
        - 項目編號(`G2.1`)—— 它後面緊接著就是白話標題;
        - 憑據續行(`四臂 A0/B1/C1/D1`)—— 規則明說「檔名、提交編號與指令輸出,
          在協助定位問題或審查修改時提供」,臂名正是那一類。
    ★ 三次校準都是被**倉裡既有的正確輸入**逼出來的(範例回報、憑據續行、治具標題),
      不是我想出來的。一道會擋到正確輸入的閘比沒有那道閘更糟 —— 它會訓練人繞過它。
    """
    lines = text.splitlines()
    starts = [i for i, l in enumerate(lines) if _SEP.search(l) and "♡" in l]
    if not starts:
        return list(enumerate(lines, 1))          # 不是七段報 ⇒ 全篇都算散文
    return [(i + 1, l) for i, l in enumerate(lines)
            if i > starts[0] and not (_SEP.search(l) and "♡" in l)]


def offenders(text: str):
    """回 [(行號, 行內容, 代號)];空 list = 沒有裸代號。"""
    out = []
    for i, raw in prose_lines(text):
        masked = _skip_item_id(_mask(raw))
        for m in CODE.finditer(masked):
            before = masked[:m.start()]
            after = masked[m.end():]
            # (a) 括號包住:左右緊鄰括號即可
            if before.rstrip().endswith(("(", "（")) and after.lstrip().startswith((")", "）")):
                continue
            # (b) 前面有夠長的白話
            if len(_CJK.findall(before)) >= MIN_PROSE:
                continue
            out.append((i, raw.strip(), m.group(0)))
    return out


# ══ 規則第三條:自創術語 ══════════════════════════════════════════
#
# ★★ 2026-09-09 23:5x the maintainer 問「棘輪母體又是什麼」而發現的:
#   本檔的 docstring 說它守「第二、三條」,而實作**只有第二條**(裸代號)。
#   第三條(自創術語)零實作 —— 規則寫成橫向的、閘只蓋一道,
#   而那正是本庫今天已經記過兩次的形狀。
#
# ★ 名單從**規則自己的表格**導出,不是我另外想的:
#   `docs/REPORTING-RULES.md` 第三條列了「量測臂、入口格、還原刀、尺主、候選轉綠」,
#   其餘是同一天現查倉裡實際高頻的同族詞(母體 1950 次、臂 7121 次、收據 2945 次…)。
#
# ★★ 為什麼**不擋碼與文件、只擋回報**:這些詞在 tests/ 與 docs/ 裡是**正確的工作語言**,
#   它們讓判準寫得精確。要擋的是「拿它們去對**使用者**講話」。
#   ⇒ 本閘只在回報路徑上開火,與第二條同一個入口。
#
# ★ 判準與第二條一致:**術語前面要有白話**,或**被括號包住當補充**。
#   一律禁用是錯的 —— 那會讓人改寫成更模糊的說法,而不是更清楚的說法。
#: 術語 → 字典裡的 slug。**這張表是唯一的名單**:JARGON 從它導出,白話從 i18n 的
#: aib.termdef.<slug> 讀,第二大腦那則筆記也是從同一份字典匯出的 —— 三處一個源。
#: ★ 加一個詞 = 在這裡加一列 + 四語字典各加 aib.term./aib.termdef.(test 釘著兩邊要對齊)。
TERM_SLUG = {
    "量測臂": "measuring_arm", "入口格": "entry_cell", "還原刀": "restore_knife",
    "尺主": "ruler_owner", "候選轉綠": "candidate_green",
    "母體": "population", "棘輪": "ratchet", "落刀": "knife_drop", "刀下": "under_knife",
    "必落空端": "must_miss_end", "必紅": "must_red", "假綠": "false_green",
    "消費者": "consumer", "分辨器": "discriminator", "到達性": "reachability",
    "恆真": "vacuous", "單源": "single_source", "咽喉": "chokepoint", "開火": "fires",
    # ★ 09-10 the maintainer 一晚問了五次「這是什麼意思」,五個都不在名單上:
    "驗證": "witness", "收據": "receipt", "疊稿": "stacked_draft",
    "越界": "out_of_scope", "待決": "pending_decision",
}
JARGON = tuple(TERM_SLUG)
JARGON_RE = re.compile("|".join(re.escape(w) for w in JARGON))


# ══ 白話註解 [註N](2026-09-10 the maintainer:「強化『沒學過的人看不看得懂』變成程式」)═════
#
# ★★ 名單型的閘只擋得住我**想得到**的詞。09-10 一晚 the maintainer 問了五次「這是什麼意思」,
#   五個詞(消費者/驗證/閘/疊稿/越界)一個都不在名單上 —— 而我那天正在蓋這道閘。
#   ⇒ 要一個**寫的人自己標**的機制:任何詞後面接 `[註N]`,最下面「白話解譯:」
#      給 `[註N] 詞 = 白話`。這樣名單外的詞也有路可走,而且讀的人在**同一頁**就查得到。
#
# ★ 四個方向都要擋,少一個就是「規則寫了而閘只蓋一半」(09-09 一天三次的形狀):
#   (1) 名單上的詞裸用、沒有 [註N]、前面也沒白話      → 擋(原第三條)
#   (2) 文裡有 [註N] 而最下面沒有 [註N] 的解釋        → 擋(標了等於沒標)
#   (3) 最下面有 [註N] 而文裡沒人用                   → 擋(死註解,與「寫了規則沒人執行」同形)
#   (4) 解釋本身還在用名單上的詞                       → 擋(拿術語解釋術語)
#
# ★ 為什麼註解區放在收尾段最底下、不開第五段:分隔線字面是規格定的(「規格是正本,
#   不各存副本」),33 支測試釘著四段結構。沒有註解時輸出逐位元組不變,舊回報不受影響。
NOTE_MARK = re.compile(r"\[註(\d+)\]")
_I18N_DIR = __import__("pathlib").Path(__file__).resolve().parents[2] / "i18n"
_FALLBACK_HEAD = "【名詞解釋】"   # ★ 不能用框線字元 ─:R-30 禁字會擋整份回報


_I18N_LIB = []


def _locale(locale=None):
    """語系來源只認 i18n_lib.resolve_locale(明給 > env > 設定檔 > zh-TW)。照檔案路徑載入那一支,不動 sys.path;
    載不到才退回「明給的值(限四語原樣)或 zh-TW」。結果一定是四語之一 ⇒ 拼進檔名也安全。"""
    import sys
    try:
        if not _I18N_LIB:
            mod = sys.modules.get("i18n_lib")
            if mod is None:
                import importlib.util as _ilu
                spec = _ilu.spec_from_file_location("_jargon_gate_i18n_lib",
                                                    str(__import__("pathlib").Path(__file__).with_name("i18n_lib.py")))
                mod = _ilu.module_from_spec(spec)
                spec.loader.exec_module(mod)
            _I18N_LIB.append(mod)
        return _I18N_LIB[0].resolve_locale(locale)
    except Exception:  # noqa: BLE001 —— 閘壞不准癱瘓算繪;退路也只收四語,怪值不拼進檔名
        return locale if locale in ("zh-TW", "en", "ja", "ko") else "zh-TW"


def load_terms(locale=None):
    """回 {slug: (詞, 白話)} —— 直接讀 i18n/<locale>.json,不經 i18n_lib,所以不動 sys.path。
    ★ 讀不到 ⇒ 回空 dict 並讓呼叫端自己決定;這裡不吞也不擋(閘壞不准癱瘓算繪)。"""
    import json
    loc = _locale(locale)
    try:
        d = json.loads((_I18N_DIR / ("%s.json" % loc)).read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for k, v in d.items():
        if k.startswith("aib.term."):
            slug = k[len("aib.term."):]
            out[slug] = (v, d.get("aib.termdef." + slug, ""))
    return out


def notes_head(locale=None):
    """「名詞解釋」那一行的字面,從字典的 aib.notes.header 讀;讀不到用內建同一字面。"""
    import json
    loc = _locale(locale)
    try:
        return json.loads((_I18N_DIR / ("%s.json" % loc)).read_text(encoding="utf-8"))["aib.notes.header"]
    except Exception:
        return _FALLBACK_HEAD


NOTES_HEAD = notes_head()
#: 定義行:`[註N] 詞:白話`(term-notes 用半形冒號)—— 也吃 = ＝ :,四種都是「詞 分隔 白話」。
NOTE_DEF = re.compile(r"^\s*\[註(\d+)\]\s*(.+?)\s*[:：=＝]\s*(.+?)\s*$")


_ALL_HEADS = {_FALLBACK_HEAD, NOTES_HEAD} | {
    notes_head(l) for l in ("zh-TW", "en", "ja", "ko")}


def parse_notes(text: str):
    """回 (定義 dict{N: (詞, 白話)}, 定義所在行號 set, 錯誤 list)。

    定義區 = 「白話解譯:」那一行之後到檔尾。同一個 N 定義兩次算錯 —— 第二份會
    靜靜蓋掉第一份,而畫面上看不出來。
    """
    lines = text.splitlines()
    heads = [i for i, l in enumerate(lines) if l.strip() in _ALL_HEADS]
    if not heads:
        return {}, set(), []
    defs, at, errors = {}, set(), []
    for i in range(heads[0] + 1, len(lines)):
        m = NOTE_DEF.match(lines[i])
        if not m:
            if lines[i].strip():
                errors.append("第 %d 行 %r 在「%s」之後,但不是 [註N] 詞 = 白話 的形狀"
                              % (i + 1, lines[i].strip()[:40], NOTES_HEAD))
            continue
        n = int(m.group(1))
        if n in defs:
            errors.append("[註%d] 定義了兩次(第二份會靜靜蓋掉第一份)" % n)
        defs[n] = (m.group(2), m.group(3))
        at.add(i + 1)
    return defs, at, errors


def _annotated(masked: str, end: int, defs: dict):
    """術語結尾在 end;它所在的**整個詞**(連續中日文字)結尾緊接 [註N] 且 N 有定義 → 算已解釋。
    ★ 「驗證器[註1]」:術語是「驗證」,但人標註解會標在「器」後面 —— 第一版只認緊接術語,
      對這種正確寫法也紅(test_05 抓到的)。"""
    j = end
    while j < len(masked) and _CJK.match(masked[j]):
        j += 1
    m = NOTE_MARK.match(masked, j)
    return bool(m) and int(m.group(1)) in defs


def jargon_offenders(text: str):
    """回 [(行號, 行內容, 術語)];空 list = 沒有裸術語。

    ★ 判準與 `offenders`(第二條)刻意一致:前面有 MIN_PROSE 個白話字,
      或被括號包住,就算已經解釋過。不一致的話,同一份回報會有兩套標準。
    ★ 第三條路(09-10):術語緊接 `[註N]` 且最下面有 [註N] 的白話 → 算已解釋。
      定義行自己那個詞不算裸用(它就是在定義它);但定義行的**白話那一半**照樣掃。
    """
    defs, def_lines, _ = parse_notes(text)
    # 整份裡已經「詞[註N]」過的詞:後面再裸用算已解釋(像註腳,標一次就夠)
    annotated_words = {m.group(1) for m in re.finditer(
        r"(%s)[぀-ヿ一-鿿]*\[註(\d+)\]" % JARGON_RE.pattern, text) if int(m.group(2)) in defs}
    out = []
    for i, raw in prose_lines(text):
        masked = _mask(raw)          # 反引號內的不算 —— 那是在談論這個詞
        if i in def_lines:
            # 只掃「=」右邊的白話;左邊那個詞就是被定義的對象
            eq = max(masked.find("="), masked.find("＝"), masked.find(":"), masked.find("："))
            masked = " " * (eq + 1) + masked[eq + 1:]
        for m in JARGON_RE.finditer(masked):
            before = masked[:m.start()]
            after = masked[m.end():]
            if before.rstrip().endswith(("(", "（")) and after.lstrip().startswith((")", "）")):
                continue
            if len(_CJK.findall(before)) >= MIN_PROSE:
                continue
            if _annotated(masked, m.end(), defs):
                continue
            if m.group(0) in annotated_words:
                continue
            out.append((i, raw.strip(), m.group(0)))
    return out


def note_offenders(text: str):
    """[註N] 的四個失效方向裡的 (2)(3) 與定義區格式錯;回 list[str],空 = 沒問題。

    (4)「解釋本身用術語」由 jargon_offenders 對定義行右半邊掃出來,不在這裡重做。
    """
    defs, def_lines, errors = parse_notes(text)
    used = {}
    for i, raw in enumerate(text.splitlines(), 1):
        if i in def_lines:
            continue
        for m in NOTE_MARK.finditer(_mask(raw)):
            used.setdefault(int(m.group(1)), []).append(i)
    for n, where in sorted(used.items()):
        if n not in defs:
            errors.append("第 %d 行用了 [註%d],但最下面「%s」沒有它的白話"
                          % (where[0], n, NOTES_HEAD))
    for n in sorted(defs):
        if n not in used:
            errors.append("最下面定義了 [註%d] %s,但全文沒有任何地方用到它(死註解)"
                          % (n, defs[n][0]))
    return errors


def check(text: str):
    """回 (ok, 訊息)。訊息逐行點名,並附規則第幾條 —— 被擋的人要知道去讀哪一句。"""
    bad = offenders(text)
    jbad = jargon_offenders(text)
    nbad = note_offenders(text)
    if not bad and not jbad and not nbad:
        return True, ""
    lines = []
    if bad:
        lines.append("回報裡有**單獨出現的內部代號**(REPORTING-RULES 第二條):")
        for no, line, code in bad[:5]:
            lines.append("  第 %d 行 %r ← %s" % (no, line[:60], code))
        lines.append("  改法:先講那是什麼問題,代號放在括號裡當查詢依據。")
        lines.append("  例:「登入失敗的問題已修正(R2)」而不是「R2 已排除」。")
    if jbad:
        if lines:
            lines.append("")
        lines.append("回報裡有**沒解釋過的自創術語**(REPORTING-RULES 第三條):")
        for no, line, word in jbad[:5]:
            lines.append("  第 %d 行 %r ← 「%s」" % (no, line[:60], word))
        lines.append("  改法:直接描述實際動作。規則第三條有對照表,例如:")
        lines.append("    「還原刀有紅」→「刻意放回原本的錯誤寫法後,測試確實失敗」")
        lines.append("    「母體圈錯」→「這次要檢查的範圍圈錯了」")
        lines.append("  ★ 要保留術語就先講白話再放括號:「檢查的範圍(母體)」。")
        lines.append("  ★ 或者在詞後面接 [註N],並在最下面「%s」給 [註N] 詞:白話(aib-report.py 會自動做)。" % NOTES_HEAD)
    if nbad:
        if lines:
            lines.append("")
        lines.append("回報的 [註N] 註解對不上(REPORTING-RULES 第三條之二):")
        for e in nbad[:5]:
            lines.append("  " + e)
    return False, "\n".join(lines)


# ══ 小工具:找出哪些詞可以標 [註N],並替你起草最下面那一塊 ═══════════════
def suggest(text: str):
    """印出每一個裸術語與建議的 [註N],再印一份可直接貼的「白話解譯:」草稿。
    白話那一欄先從 REPORTING-RULES 的對照表抄,抄不到的留 <請填白話>。"""
    hits = jargon_offenders(text)
    if not hits:
        return "沒有裸術語;不需要註解。"
    order, seen = [], {}
    for _, _, w in hits:
        if w not in seen:
            seen[w] = len(order) + 1
            order.append(w)
    out = ["建議在這些詞後面接 [註N]:"]
    for no, line, w in hits:
        out.append("  第 %d 行 %r:「%s」→「%s[註%d]」" % (no, line[:40], w, w, seen[w]))
    out.append("")
    out.append("最下面貼這一塊(白話欄要用沒學過的人看得懂的話):")
    out.append(NOTES_HEAD)
    for w in order:
        out.append("[註%d] %s:%s" % (seen[w], w, plain_for(w) or "<字典裡沒有,先補 aib.term./aib.termdef.>"))
    return "\n".join(out)


def plain_for(word, locale=None):
    """術語的白話,從字典 aib.termdef.<slug> 來;不在字典就回 None(不要自己編)。"""
    slug = TERM_SLUG.get(word)
    if not slug:
        return None
    return (load_terms(locale).get(slug) or (None, None))[1] or None


def auto_annotate(text, order, locale=None, done=None):
    """把 text 裡名單上的術語(反引號外)接上 [註N];N 照 order 裡的出現順序。
    回改寫後的文字;order 是跨欄位共用的 list,呼叫端拿它印最下面那一塊。
    ★ **每個詞整份只掛第一次**(done 集合跨欄位共用):「核對收據[註1]核對收據[註1]…」
      比沒掛更難讀。後面再出現的算已解釋 —— jargon_offenders 同一條規則。
    ★ 已經接了 [註N] 的不重複接;定義區那幾行不動。"""
    terms = load_terms(locale)
    if done is None:
        done = set()

    def sub(m):
        w = m.group(0)
        slug = TERM_SLUG.get(w)
        if not slug or slug not in terms or slug in done:
            return w
        if slug not in order:
            order.append(slug)
        done.add(slug)
        return "%s[註%d]" % (w, order.index(slug) + 1)

    out_lines = []
    for raw in text.splitlines(keepends=True):
        pieces = re.split(r"(`[^`]*`)", raw)
        for i, piece in enumerate(pieces):
            if i % 2 == 0:
                pieces[i] = re.sub(JARGON_RE.pattern + r"(?![぀-ヿ一-鿿]*\[註\d+\])", sub, piece)
        out_lines.append("".join(pieces))
    return "".join(out_lines)


def notes_block(order, locale=None):
    """最下面那一塊的行:標題 + `[註N] 詞:白話`。order 為空回 []。"""
    if not order:
        return []
    terms = load_terms(locale)
    lines = [notes_head(locale)]
    for n, slug in enumerate(order, 1):
        name, definition = terms.get(slug, (slug, ""))
        lines.append("[註%d] %s:%s" % (n, name, definition))
    return lines



if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "suggest":
        import pathlib
        print(suggest(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")))
    elif len(sys.argv) >= 3 and sys.argv[1] == "check":
        import pathlib
        ok, msg = check(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
        print("OK" if ok else msg)
        raise SystemExit(0 if ok else 1)
    else:
        print("用法:jargon_gate.py suggest <回報檔>   找出可以標 [註N] 的詞並起草白話解譯")
        print("      jargon_gate.py check   <回報檔>   跑第二、三條與註解對應檢查")
        raise SystemExit(2)
