#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Provider-specific composer facts shared by cockpit readers and DM delivery."""

import re
import sys


LEGACY_PROMPT_CHARS = ("\u276f", "\u203a")

_ALIASES = {
    "antigravity": "agy",
    "github-copilot": "copilot",
}

_DIALECTS = {
    "claude": {
        "composer": "prompt",
        "prompt_chars": LEGACY_PROMPT_CHARS,
        "folded_lines": "additional",
    },
    "codex": {
        "composer": "prompt",
        "prompt_chars": LEGACY_PROMPT_CHARS,
        "folded_lines": None,
    },
    "agy": {
        "composer": "prompt",
        "prompt_chars": (">",),
        "folded_lines": "total",
    },
    "copilot": {
        "composer": "box",
        "prompt_chars": (),
        # ★ 2026-09-02 現量:貼 14 行 → `[Paste #6 - 14 lines]`,數量是**總行數**。
        #   原本寫 None(= 沒有摺疊方言),而它其實有,門檻 11 行。
        #   先前判它「好的」是因為只貼過 5 行,沒到門檻。
        "folded_lines": "total",
        "folded_re": re.compile(
            r"\[Paste #\d+ - (?P<lines>\d+) lines?\]"
            r"|\[Saved pasted content to workspace \((?P<saved_kb>\d+(?:\.\d+)?) KB\) id=\d+\]"
        ),
    },
    # ★ 2026-09-02:grok 上線為 provider 已數日(啟動旗標/session uuid/引擎徽章都補了),
    #   **只有這張表沒補**。後果不是報錯,是 `folded_line_verdict` 回 None →
    #   dm-ctl.sh「Unknown dialects remain stopped」→ 七席可定址但一個字都送不進去,
    #   而 rc 契約回 2(「可安全重送」)—— 重送只會再失敗一次。
    #   佔位符字面是**現量**的(貼 5 行,框內逐字 `[Pasted: 5 lines]`),不是照抄 claude。
    "grok": {
        "composer": "prompt",
        "prompt_chars": LEGACY_PROMPT_CHARS,
        "folded_lines": "total",
        "folded_re": re.compile(
            r"\[Pasted: (?P<lines>\d+) lines?\]"
            r"|\[Pasted: (?P<kb>\d+) KB\]"
            r"|\[Pasted: (?P<mb>\d+(?:\.\d+)?) MB\]"
        ),
        # ★ grok 的輸入行是**畫框**的:`│ ❯ <稿>            │`,右邊有收邊。
        #   claude/codex/agy 的輸入行沒有右邊界,所以分類器「提示符之後每一個
        #   可見字元」的寫法在它們身上是對的;在 grok 身上那個 `│` 會被數成稿,
        #   於是**空框永遠判 draft** → 守衛永遠拒投 → 七席可定址而一個字都送不進。
        "composer_border": True,
        # ★ 2026-09-02 現量:grok 把**自己預載的下一步建議**畫成
        #   `ESC[3m`(斜體)+ 24-bit 暗灰 `38;2;88;88;88`,**沒有 SGR 2**。
        #   分類器的 dim 偵測只認 SGR 2 → 建議被判成真稿 → dm 守衛拒投:
        #   **grok 席只要框裡有 CLI 建議就永遠收不到 dm。**
        #   ★ 這與 codex 那次(`48;2;90;90;90` 裡的裸 2 被讀成 dim)同族而方向相反:
        #     那次把真稿讀成建議(危險),這次把建議讀成真稿(卡死)。
        #     同一個「24-bit 色參數不是 SGR 碼」的坑,兩家一個中一個沒中。
        #   同時現量到的第二個訊號:頁尾字面。真稿時是 `Enter:send`,
        #   有建議時多出 `accept suggestion`。**兩個訊號都要,才敢說是建議** ——
        #   誤判方向必須留在保守那一邊(把建議當真稿只是多等一輪,
        #   把真稿當建議會去按別人沒打完的字)。
        "suggestion_italic": True,
        "suggestion_footer": "accept suggestion",
        # ★ 2026-09-29 a seat 現量(Grok 4.7,席 a seat):**空的**輸入框會畫一句提示字
        #   `│ ❯ Build anything            │`:24-bit 暗灰 `38;2;78;78;78`,沒有斜體、沒有 SGR 2,
        #   頁尾也沒有 `accept suggestion`(它不是建議,按 Tab 不會採用它)。
        #   上面兩條判準都對不上 ⇒ 判成真稿 ⇒ dm 守衛拒投:**這一席框空著的時候誰都寄不進去**
        #   (22:52 newproj 的接手包被擋兩次轉待人處理,NOTICE-composer-blocked-77324)。
        #   兩個訊號都要才算空框,誤判方向留在保守那一邊:
        #     (a) 提示符之後的字**逐字等於**這裡宣告的字面;
        #     (b) 每一個可見字元都畫在暗色上(24-bit 前景色三個分量都不超過上限)。
        #   人真的打了同一句話時是亮色(現量回顯行 `38;2;225;225;225`)⇒ 仍是真稿。
        "empty_placeholders": ("Build anything",),
        "empty_placeholder_ceiling": 120,
        # ★ 第 55 項(09-12):grok TUI 的**瀏覽模式**(訊息樹可展開/收合)底列字面 `←:collapse` / `Enter:open`,
        #   那時輸入行不在畫面上,composer_state 定不出輸入框 → unknown → 引擎擋下,理由與「卡稿」同形;
        #   09-12 三次「排入暫存」全是這一種。標記來自艦長 p7 的巡查紀錄與 §89.8 第 55 列,**不是我擷的畫面**
        #   (當時艙裡沒有活的 grok 席可擷);兩個標記都要在尾端才算,少一個仍是 unknown(保守側)。
        "browse_footer": ("collapse", "Enter:open"),
    },
    # 橋接席:無本機 TUI 摺疊方言;佔位與 codex 同形,避免「開得起來卻送不進去」。
    "grokbot": {
        "composer": "prompt",
        "input_repl": True,
        "prompt_chars": LEGACY_PROMPT_CHARS,
        "folded_lines": None,
    },
    "claudebot": {
        "composer": "prompt",
        "input_repl": True,
        "prompt_chars": LEGACY_PROMPT_CHARS,
        "folded_lines": None,
    },
    # 2026-10-05 musebot:與 grokbot/claudebot 同走 sand_bridge 的 REPL(❯ 提示符);沒有這一條 ⇒ composer_state
    #   恆回 unknown,aib dm 一律擋下(codexbot 09-24 前例)。
    "musebot": {
        "composer": "prompt",
        "input_repl": True,
        "prompt_chars": LEGACY_PROMPT_CHARS,
        "folded_lines": None,
    },
    # codexbot 的輸入列不是 TUI,是 codexbot_bridge.py 的 input("codexbot> "),
    # 逐字就是 `codexbot> `。原本沿用 LEGACY_PROMPT_CHARS(❯ ›)是錯的:橋從來不印
    # 那兩個字元,composer_state 於是恆回 unknown,aib dm 一律擋下 —— 席開得起來、
    # 模型也活著,就是送不進去(09-24 a seat 實測)。同 deepseek 前例:字面只在這個
    # provider 內辨識;不保留 ❯ ›,因為橋沒有任何版本印過它們,留著只會多一種誤判。
    "codexbot": {
        "composer": "prompt",
        "input_repl": True,
        "prompt_chars": ("codexbot>",),
        "folded_lines": None,
        # 回合進行中橋**沒有**提示符行(input() 還沒回來),尾端只有橋的通知
        # `codexbot: 已送出…/思考中…/正在…`。少了這一條,定位端回 None ⇒ unknown
        # ⇒ dm 貼前守衛拒投、送後確認回 rc=3 —— 而那時輸入框其實是空的、
        # 稿已經送進模型。通知行是橋自己印的、只在接受輸入之後出現,是正面證據。
        "busy_markers": ("codexbot: ",),
        # R7(09-25 a seat,a seat 派):橋的畫面上 `codexbot> <字>` 不只出現在活輸入列——
        #   送出後的回顯、通知重畫都會留下同形的行,「尾端最後一個提示符行」會找到舊行而判成 DRAFT
        #   (艙 9260 a seat 16:43 起 14 封 dm 被擋)。宣告 cursor_line ⇒ 呼叫端給了游標列就只看游標那一行。
        #   grokbot 不宣告:sand_bridge 開 UI 時 input("") 沒有提示符,游標行上分不出人打的字與輸出。
        "cursor_line": True,
        # 游標行有字、沒提示符時往上找同一段折行的提示符;碰到空行或這些「橋自己印的」行就停(它們不是人打的稿)。
        #   `codexbot: ` 通知、`codexbot< ` 回話、`dm> ` / `真人(` 送出後改寫過的回顯。
        "cursor_stop": ("codexbot: ", "codexbot< ", "dm> ", "真人("),
    },
    # DeepSeek API-key 席位的 REPL 輸入列逐字是 `DeepSeek> `。它不是通用的 `>`
    # 提示符:只在這個 provider 內辨識，避免把輸出、引文或 diff 誤當輸入框。
    "deepseek": {
        "composer": "prompt",
        "input_repl": True,
        # `you>` 只供已在更新前啟動的行程判讀；新行程一律由 deepseek_cli.py
        # 顯示 `DeepSeek>`。保留它可避免升級當下把活席變成 unknown。
        "prompt_chars": ("DeepSeek>", "you>"),
        "folded_lines": None,
    },
    "openrouter": {
        "composer": "prompt",
        "input_repl": True,
        "prompt_chars": ("OpenRouter>", "you>"),
        "folded_lines": None,
    },
    "typesafe": {
        "composer": "prompt",
        "input_repl": True,
        # 09-30 測試艙 b1af:typesafe 走 openrouter 路由時提示符印成「OpenRouter an internal service>」(typesafe_cli.run_repl),
        #   表上只有 TypeSafe> ⇒ 判不出輸入框(unknown)⇒ 私訊一律被擋。同一支程式兩種字都認。
        "prompt_chars": ("TypeSafe>", "OpenRouter jev>", "you>"),
        "folded_lines": None,
    },
    "openrouterjev": {
        "composer": "prompt",
        "input_repl": True,
        "prompt_chars": ("OpenRouter jev>", "TypeSafe>", "you>"),
        "folded_lines": None,
    },
    "nvidia": {
        "composer": "prompt",
        "input_repl": True,
        # `you>` 只供已在更新前啟動的行程判讀；新行程一律由 nvidia_cli.py
        # 顯示 `DeepSeek>`。保留它可避免升級當下把活席變成 unknown。
        "prompt_chars": ("DeepSeek>", "you>"),
        "folded_lines": None,
    },
}

# claude 家族的字面(codex/agy 沿用)。grok 另有自己的,見上表 folded_re。
_FOLDED_TEXT_RE = re.compile(
    r"\[Pasted text #\d+ \+(?P<lines>\d+) lines?\]"
)


def normalize_provider(provider):
    """Return a canonical provider name, or None for absent/unknown input."""
    if not isinstance(provider, str) or not provider.strip():
        return None
    name = provider.strip().lower()
    name = _ALIASES.get(name, name)
    return name if name in _DIALECTS else None


def composer_kind(provider):
    name = normalize_provider(provider)
    return _DIALECTS[name]["composer"] if name else None


def prompt_chars(provider=None):
    """Prompt characters for one provider; omitted provider keeps legacy behavior."""
    if provider is None:
        return LEGACY_PROMPT_CHARS
    name = normalize_provider(provider)
    return _DIALECTS[name]["prompt_chars"] if name else ()


def folded_line_verdict(provider, placeholder, expected_lines):
    """Return True/False for a known fold dialect, or None if it is not known."""
    name = normalize_provider(provider)
    if not name or not isinstance(expected_lines, int) or expected_lines < 0:
        return None
    mode = _DIALECTS[name]["folded_lines"]
    # 每個方言可自帶字面;沒帶的沿用 claude 家族那一條。
    regex = _DIALECTS[name].get("folded_re") or _FOLDED_TEXT_RE
    match = regex.fullmatch(placeholder or "")
    if not mode or not match:
        return None
    # KB/MB 形現量對不上行數:fullmatch 得中但不給 True(那會讓 verify-fold
    # 把「81 KB」當成 81 行)。回 None=這式不能當行數證明。
    if match.groupdict().get("lines") is None:
        return None
    shown = int(match.group("lines"))
    if mode == "additional":
        return shown + 1 == expected_lines
    if mode == "total":
        return shown == expected_lines
    return None


def resolve_provider(seat_provider=None, tmux_provider=None):
    """Reconcile the seat-file and tmux provider sources without guessing."""
    seat_raw = seat_provider.strip() if isinstance(seat_provider, str) else ""
    tmux_raw = tmux_provider.strip() if isinstance(tmux_provider, str) else ""
    seat = normalize_provider(seat_raw) if seat_raw else None
    tmux = normalize_provider(tmux_raw) if tmux_raw else None
    detail = "席位檔=%s tmux=%s" % (seat_raw or "缺", tmux_raw or "缺")
    if (seat_raw and seat is None) or (tmux_raw and tmux is None):
        return {"ok": False, "provider": None, "source": "非法", "detail": detail}
    if seat and tmux and seat != tmux:
        return {"ok": False, "provider": None, "source": "不一致", "detail": detail}
    if seat and tmux:
        return {"ok": True, "provider": seat, "source": "席位檔+tmux",
                "detail": detail}
    if seat:
        return {"ok": True, "provider": seat, "source": "席位檔", "detail": detail}
    if tmux:
        return {"ok": True, "provider": tmux, "source": "tmux", "detail": detail}
    return {"ok": True, "provider": None, "source": "legacy", "detail": detail}


def _main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) == 3 and argv[0] == "resolve-provider":
        result = resolve_provider(argv[1], argv[2])
        print(result["provider"] or "")
        print(result["source"])
        print(result["detail"])
        return 0 if result["ok"] else 4
    if len(argv) != 4 or argv[0] != "verify-fold":
        print("usage: composer_dialect.py verify-fold PROVIDER EXPECTED PLACEHOLDER | "
              "resolve-provider SEAT_PROVIDER TMUX_PROVIDER",
              file=sys.stderr)
        return 2
    try:
        expected = int(argv[2])
    except ValueError:
        return 2
    verdict = folded_line_verdict(argv[1], argv[3], expected)
    if verdict is None:
        return 2
    if verdict:
        print("provider=%s folded-lines=match expected=%d" %
              (normalize_provider(argv[1]), expected))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())


def composer_border(provider):
    """這個 provider 的輸入行有沒有右收邊(畫框)。未知 provider 一律 False。"""
    name = normalize_provider(provider)
    return bool(_DIALECTS[name].get("composer_border")) if name else False


def empty_placeholder_marks(provider):
    """(空框提示字的字面們, 暗色上限) —— 沒宣告的 provider 回 ((), 0)。"""
    name = normalize_provider(provider)
    if not name:
        return (), 0
    d = _DIALECTS[name]
    marks = d.get("empty_placeholders") or ()
    return tuple(m for m in marks if isinstance(m, str) and m), int(d.get("empty_placeholder_ceiling") or 0)


def input_repl(provider):
    """10-01 長稿(R16 M2):輸入列是 Python ``input()`` 的 REPL(API 席 CLI、橋接席 sand/codexbot 橋)。

    這種輸入列**裝不下一張多列草稿**:按 Enter 之後,剛送出的那一行會原樣留在畫面上當回音
    (`DeepSeek> <稿>`),下一行才是 CLI 自己印的東西(`deepseek> ` 回覆標籤、`真人> …`、
    `codexbot: 已送出…`)。框判定器會把那行回音讀成「提示符後面有字 = 草稿」。
    宣告這一格的 provider,呼叫端才可以用框判定器的 REPL 回音判準(repl_submitted)認「回音後面還有輸出 = 已送出」。
    未宣告 ⇒ False(copilot 畫框、claude/codex/grok TUI 一個位元組都不變)。"""
    return bool(_DIALECTS.get(normalize_provider(provider), {}).get("input_repl"))


def cursor_line(provider):
    """R7:這個 provider 的活輸入列就是游標那一行(呼叫端給了游標列時才用)。未宣告 ⇒ False(行為不變)。"""
    return bool(_DIALECTS.get(normalize_provider(provider), {}).get("cursor_line"))


def cursor_stop(provider):
    """R7:往上找折行提示符時要停下的行首(橋自己印的輸出)。"""
    return tuple(_DIALECTS.get(normalize_provider(provider), {}).get("cursor_stop") or ())


def busy_markers(provider):
    """回合進行中、沒有提示符行時,尾端會出現的「引擎自己印的忙碌通知」前綴;
    沒宣告的 provider 回空 tuple(⇒ 定不出輸入框就維持 unknown,不猜)。"""
    return tuple(_DIALECTS.get(normalize_provider(provider), {}).get("busy_markers") or ())


def browse_footer(provider):
    """瀏覽模式的底列標記(全部都要出現才算);沒有這種模式的引擎回空 tuple。"""
    return tuple(_DIALECTS.get(normalize_provider(provider), {}).get("browse_footer") or ())


def suggestion_marks(provider):
    """(要不要斜體, 頁尾字面) —— 沒宣告的 provider 回 (False, None)。"""
    name = normalize_provider(provider)
    if not name:
        return False, None
    d = _DIALECTS[name]
    return bool(d.get("suggestion_italic")), d.get("suggestion_footer")
