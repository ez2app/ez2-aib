# SPDX-License-Identifier: Apache-2.0
"""E2.4 R-30 出站訊息 lint:禁圈號 / 全形英數 / 框線字元。

判準來源:艦長派工令明列的三類(R-30 正本在 ACB 側,抄判準不抄實作)。

**字碼範圍是我依那三個名稱推導的,不是從 ACB 抄來的** —— 我在 ~/.acb 找過,
只找得到「R-30 禁字元」這個名稱,找不到列舉的字元集。所以範圍寫在下面的表裡
攤開給人看,審查者要修正哪一段都改得動;若日後拿到 ACB 正本的字元清單,
這張表就該被那份清單取代,而不是兩邊各留一份。

為什麼要擋這三類:
  圈號 ①②③   在不同終端機/字型下寬度不一,會把對齊的版面撞歪;複製貼上後
               常變成 ?,收訊端看到的和送訊端不同。
  全形英數 ＡＢ１２  同一個字有兩種碼位,grep/比對/去重全部失準(訊息是要被機器讀的)。
  框線字元 ─│┌  ASCII 藝術在等寬假設破掉時整片爛掉,而終端機寬度不是我們能控制的。

刻意**不擋**全形標點(,。「」):中文正文本來就用它們,擋了等於不准講中文。
"""

import sys
import unicodedata

# (code, 名稱, [(起, 迄) ...])  —— 迄含
RANGES = [
    ("circled_number", "圈號/圈圈字", [
        (0x2460, 0x24FF),    # Enclosed Alphanumerics ①-⓿
        (0x3248, 0x324F),    # Circled Number on black square
        (0x3251, 0x325F),    # Circled Number 21-35
        (0x3280, 0x32BF),    # Circled Ideographs / 36-50
        (0x1F100, 0x1F1FF),  # Enclosed Alphanumeric Supplement 🄀-🇿
    ]),
    ("fullwidth_alnum", "全形英數", [
        (0xFF10, 0xFF19),    # ０-９
        (0xFF21, 0xFF3A),    # Ａ-Ｚ
        (0xFF41, 0xFF5A),    # ａ-ｚ
    ]),
    ("box_drawing", "框線字元", [
        (0x2500, 0x257F),    # Box Drawing ─│┌┐└┘├┤┬┴┼
    ]),
    ("block_element", "區塊填充字元", [
        (0x2580, 0x259F),    # Block Elements ▀▄█░▒▓
    ]),
]

# block_element 是我加的第四類(名稱上不屬「框線」,但同樣是拿來畫 ASCII 版面的,
# 破法一模一樣)。獨立成一個 code,審查者要放寬只需拿掉這一條,不動其他三類。

CODES = [r[0] for r in RANGES]

# R-30 例外(2026-08-23,a seat 12:34 派;起因是他自己撞上——派工單裡用了樹狀
# 回報格式,整則訊息因此送不出去。**我們定了一個席間送不出去的格式。**)
#
# ★ 鍵是**碼位不是字面**:把偵測器要抓的字元寫進它自己會讀到的檔,是本班記過的坑
#   (談論它就是污染它)。值是理由字串,因為一條沒有理由的例外沒有人敢刪。
# ★ 只放行**一個**字元。實測(design 先量、我獨立複驗):樹狀回報的箭號 U+25B8 在
#   Geometric Shapes(0x25A0–0x25FF),而 box_drawing 只到 0x257F ——**它今天就過**。
#   把它也列進來會得到一條**從出生那天起就走不到**的例外,而滿的清單比空的清單更難查。
# ★ 這張表由 lint() 在**呼叫當下**讀取(見 _allowed),不是 import 當下固化成 frozenset。
#   理由是承重的:突變臂要能在**不改碼**的情況下把它拔掉,而「拔掉會怎樣」是唯一能
#   證明這條例外有承重的方式。固化之後那一臂只能證明「我改了一個變數」。
# ★★ 放行的界線要**兩層**才定義得完(2026-08-23 r3;root 19:38 問「這條分界成立不成立」)
#
# 第一層(必要條件)—— root 20:25 的提案,**成立**:
#     「輕線、單線、用於表達層級結構的連接符」
#   擋掉:雙線、粗線、虛線、十字、斜線,以及混合粗細那一大群。
#   ★ 它們不是畫樹用的,是畫表格與框的,而 the maintainer 原令要擋的正是那種裝飾。
#
# 第二層(充分條件)—— **必須有,否則第一層自己解釋不了 U+2500**:
#     「正典回報格式**實際用到**」(樣本:src/messaging/r30-format-sample.txt)
#   ★ 為什麼第一層不夠:U+2500(橫線)**是**輕線、**是**單線、**也是**層級連接符
#     (分叉後面接的就是它),它通過第一層卻必須被擋。這不是分界沒寫好,是那個問題
#     本來就需要兩個獨立的條件;只寫第一層的話,U+2500 會變成一條講不出理由的例外。
#   ★ 這也是為什麼「刻意不放行」的那兩個要寫在這裡而不是留白:
#       U+2500 橫線     —— 通過第一層,但本樣式用不到(格式寫「分叉+箭號」不接橫線)
#       U+256F 收尾圓角 —— 通過第一層,但本樣式用不到(四個位置沒有它)
#     **用不到的例外就是死條目**,而死條目會讓清單看起來是滿的。
#     寧可將來需要時再加一條**有實例**的,不要現在放一條沒有實例的。
#
# 機器上兩層都可驗(見 docs/reports/probe-r30-allowlist-equals-format-0823.py):
#     第一層:每個例外碼位都必須屬於具名的「輕線單線層級連接符」集合
#     第二層:{ 本表的碼位 } == { 正典樣本裡會被基本規則擋下的碼位 }
# ★ 第二層取代「例外不得超過 N 條」那種數字上限:數字上限每次擴大都要跟著改
#   (釘子今早寫 <=2,同日下午就得改成 3),那不是上限是記錄——與「全庫只有這四行」
#   同一個病:一個沒有人能推導出來的數字。等式版讓「加第五條例外」必須**先把那個字元
#   寫進正典樣本**,那是看得見、要被審的動作,所以「規則被逐次掏空」不可能靜默發生。
#
# ★ 其他框線字元一律不放行的理由(寫下來,否則下一個人會以為只是還沒輪到它):
#   R-30 禁的是**拿框線字元畫 ASCII 版面**——跨終端、跨字型會崩,而崩掉的樣子是
#   「訊息看起來像壞掉的」。放行的這四個是**逐行的連接標記**,一行最多兩個字元,
#   不構成版面。放寬那條規則不是我們任何一席能做的決定,它的來源是 the maintainer 的常設令。

# 第一層的具名集合(Box Drawing 裡的輕線單線層級連接符;不是「整段 0x2500-0x257F」)
LIGHT_TREE_CONNECTORS = {
    0x2502,  # 垂直連線
    0x250C, 0x2510, 0x2514, 0x2518,  # 四個直角
    0x251C, 0x2524, 0x252C, 0x2534, 0x253C,  # 分叉與十字(單線)
    0x2500,  # 橫線 —— 屬於第一層,但本樣式用不到,故不在例外表
    0x256D, 0x256E, 0x256F, 0x2570,  # 四個圓角
}

ALLOW_CODEPOINTS = {
    0x256D: "樹狀回報的開頭圓角;正典格式 r30-format-sample.txt 第一項用它",
    0x251C: "樹狀回報的中間分叉;同上,表示後面還有同層項目",
    0x2570: "樹狀回報的結尾轉角;同上,表示本層最後一項",
    0x2502: "樹狀回報的垂直連線;同上,表示上層尚未結束",
}


def _allowed(cp):
    """呼叫當下讀 ALLOW_CODEPOINTS —— 不快取、不在 import 時固化。"""
    return cp in ALLOW_CODEPOINTS


def _category(cp):
    for code, label, ranges in RANGES:
        for lo, hi in ranges:
            if lo <= cp <= hi:
                return code, label
    return None, None


def suggest(ch):
    """建議替代字;想不出安全替代就回 None(由人決定,不亂改內容)。"""
    cp = ord(ch)
    if 0xFF10 <= cp <= 0xFF19 or 0xFF21 <= cp <= 0xFF3A or 0xFF41 <= cp <= 0xFF5A:
        return unicodedata.normalize("NFKC", ch)          # ＡＢ１ -> AB1
    if 0x2460 <= cp <= 0x2473:                            # ①-⑳
        return "%d." % (cp - 0x2460 + 1)
    if 0x2474 <= cp <= 0x2487:                            # ⑴-⒇
        return "(%d)" % (cp - 0x2474 + 1)
    if 0x2488 <= cp <= 0x249B:                            # ⒈-⒛
        return "%d." % (cp - 0x2488 + 1)
    if 0x24B6 <= cp <= 0x24E9:                            # Ⓐ-ⓩ
        return unicodedata.normalize("NFKC", ch)
    return None


def lint(text):
    """回違規清單:[{code,label,char,codepoint,line,col,suggestion}]。

    line/col 都從 1 起算(給人看的座標);col 以字元計,不是顯示格。
    """
    findings = []
    for lineno, line in enumerate(str(text or "").split("\n"), 1):
        for col, ch in enumerate(line, 1):
            if _allowed(ord(ch)):
                continue
            code, label = _category(ord(ch))
            if code:
                findings.append({
                    "code": code,
                    "label": label,
                    "char": ch,
                    "codepoint": "U+%04X" % ord(ch),
                    "line": lineno,
                    "col": col,
                    "suggestion": suggest(ch),
                })
    return findings


def autofix(text):
    """把有安全替代的字換掉;沒有替代的原樣留下(由人決定怎麼改)。

    回 (修好的文字, 還沒解決的違規數)。刻意不刪除框線字元 ——
    自動刪字會改動作者的原意,那不是 lint 該做的事。
    """
    out = []
    for ch in str(text or ""):
        # ★ 例外的一致性要由構造保證,不要靠巧合:今天 U+2570 沒有替代字,
        #   所以 autofix 本來就不會動它(實測 suggest -> None)。但那是**碰巧**——
        #   哪天有人給它加一個替代字,autofix 就會把一個 lint 已經放行的字換掉,
        #   而 lint 說可以、autofix 說要改,兩邊都不會紅。
        if _allowed(ord(ch)):
            out.append(ch)
            continue
        code, _ = _category(ord(ch))
        out.append(suggest(ch) if code and suggest(ch) else ch)
    fixed = "".join(out)
    return fixed, len(lint(fixed))


def format_findings(findings, locale=None):
    """人話報告(雙語走 i18n;字典不可用時退回內建標籤,守門者不因翻譯失聲)。"""
    try:
        sys.path.insert(0, __file__.rsplit("/", 2)[0] + "/cockpit")
        from i18n_lib import Translator
        tr = Translator(locale=locale)

        def label_of(code):
            t = tr.t("aib.r30.%s" % code)
            return code if t.startswith("!!") else t
    except Exception:
        def label_of(code):
            return next((r[1] for r in RANGES if r[0] == code), code)

    lines = []
    for f in findings:
        sug = ("  -> 建議:%s" % f["suggestion"]) if f["suggestion"] else ""
        lines.append("  %d:%d %s %s(%s)%s" % (
            f["line"], f["col"], f["char"], label_of(f["code"]), f["codepoint"], sug))
    return "\n".join(lines)


def _main(argv):
    text = None
    do_fix = False
    as_json = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--text" and i + 1 < len(argv):
            text = argv[i + 1]; i += 2
        elif a == "--stdin":
            text = sys.stdin.read(); i += 1
        elif a == "--fix":
            do_fix = True; i += 1
        elif a == "--json":
            as_json = True; i += 1
        else:
            print("usage: r30_lint.py (--text T | --stdin) [--fix] [--json]",
                  file=sys.stderr)
            return 2
    if text is None:
        print("usage: r30_lint.py (--text T | --stdin) [--fix] [--json]",
              file=sys.stderr)
        return 2

    if do_fix:
        fixed, remaining = autofix(text)
        sys.stdout.write(fixed)
        if not fixed.endswith("\n"):
            sys.stdout.write("\n")
        return 1 if remaining else 0

    findings = lint(text)
    if as_json:
        import json
        print(json.dumps(findings, ensure_ascii=False))
    elif findings:
        print("R-30 lint 擋下 %d 處:" % len(findings), file=sys.stderr)
        print(format_findings(findings), file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
