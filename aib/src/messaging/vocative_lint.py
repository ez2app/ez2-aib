#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""EH.20 §5.3 稱呼位置文法 —— **唯一**一份。

`tests/fixtures/eh20-vocative/check_labels.py` 原本自帶一份;本檔把它抽出來,
由 `a seat` 同批把那支改成 import 這裡(艦長 08:20 裁「文法單源」)。
★ 兩份文法會長成「規格說擋、測資說放行」——那個病 `a seat` 08-22 已經抓過一次
  (pos-04 標放行而頓號在前綴表裡),不要讓它換個地方再長一次。

擋的條件(§五):**收件人數 > 1** 且該字出現在**稱呼位置**。
本檔只回答「有沒有在稱呼位置」;「收件人數 > 1」由呼叫端判 ——
★ 分開的理由:合在一起的話,一支「永遠擋」的實作會讓負向格漂亮通過,
  而正向格(單發訊息)全紅。條件與偵測要分開才驗得出誰壞了。

用法:
    python3 vocative_lint.py --file F     rc=0 放行 / rc=1 命中稱呼位置 / rc=2 用法錯
    python3 vocative_lint.py --selftest   合成樣本校準(先驗探測器,再出答案)
"""
import re
import sys

# ★ 談論這個字用碼位,不把它寫出來(艦規;a seat 因為舉例把字寫出來而送信 rc=2)。
VOCATIVE_CHARS = (chr(0x4F60),)

# ★★ 已知缺口,寫出來而不是偷偷補:第二人稱在中文還有 U+59B3 / U+60A8。
#   本檔**不**啟用它們,理由是 tests/fixtures/eh20-vocative/ 九格**沒有一格**涵蓋它們——
#   啟用等於加一段沒有任何釘子看著的行為,而它的失效方向是誤擋(把合規稿擋掉)。
#   缺口登記在此:要啟用,先補 fixture,再把它們加進 VOCATIVE_CHARS。
UNCOVERED_SECOND_PERSON = (chr(0x59B3), chr(0x60A8))

# §5.3 邊界表:稱呼位置的前綴**逐字列舉**,不寫「等等」。
# 行首以 off == 0 表示;換行/空白/全形空白 + 全形與半形標點兩套。
PREFIX = set("\n 　") | set("，。:;：；？！、（）,.?!()")


def excluded_spans(txt):
    """§5.3 排除層:fenced block / inline code / 行首引言整行。

    ★ 未閉合的 fence **延伸到檔尾**(§5.3 最後一列):於「擋」是 fail-open。
      理由是 §5.1——常誤擋的閘會被人繞過,連真正該擋的那一格一起關掉;
      而漏擋一則的代價是「一則訊息的代名詞指錯人」,可事後更正。
      **兩種錯的可逆性不同,所以 fail 方向不對稱。**
    """
    spans = []
    idx = [m.start() for m in re.finditer(r"```", txt)]
    for i in range(0, len(idx), 2):
        spans.append((idx[i], idx[i + 1] + 3 if i + 1 < len(idx) else len(txt)))
    for m in re.finditer(r"`[^`\n]*`", txt):
        if not any(s <= m.start() < e for s, e in spans):
            spans.append(m.span())
    for m in re.finditer(r"(?m)^>.*$", txt):
        spans.append(m.span())
    return spans


def vocative_hits(txt):
    """回傳所有落在稱呼位置的字元 offset(由小到大)。

    ★ 回 offset 清單而不是 bool:呼叫端要能指出**是哪一處**,
      否則送件人收到「你的稿被擋了」而不知道改哪裡,只會把整段刪掉重寫。
    """
    spans = excluded_spans(txt)
    out = []
    for off, ch in enumerate(txt):
        if ch not in VOCATIVE_CHARS:
            continue
        if any(s <= off < e for s, e in spans):
            continue
        if off == 0 or txt[off - 1] in PREFIX:
            out.append(off)
    return out


def grammar_blocks(txt):
    """check_labels.py 的既有介面名,原樣保留,行為逐字相同。"""
    return bool(vocative_hits(txt))


def _selftest():
    V = VOCATIVE_CHARS[0]
    cases = [
        # (標籤, 文字, 期望擋)
        ("行首", V + "好", True),
        ("句號後", "收到。" + V + "看一下", True),
        ("換行後", "第一行\n" + V + "再看", True),
        ("括號後", "(" + V + ")", True),
        ("頓號後接以該字開頭的詞", "甲、" + V + "們兩個", True),   # ★ 已知假陽性,標成必擋才誠實
        ("inline code", "指令是 `grep -c " + V + "` 這樣", False),
        ("fenced", "```\n" + V + "\n```", False),
        ("逐字引文", "> " + V + "說的那句", False),
        ("詞中", "其" + V + "我我", False),
        ("未閉合 fence 之後", "```\n" + V + "\n沒有收尾", False),
        ("完全沒有那個字", "全部改用 @五碼", False),
    ]
    checks = [(lab, grammar_blocks(t) is want) for lab, t, want in cases]
    # ★ 排除層的自我校準(沿用 check_labels.py 的做法):拿掉排除層之後,
    #   那三格「必須翻紅」——否則排除層根本沒在作用,而全綠看起來一模一樣。
    real = excluded_spans
    try:
        globals()["excluded_spans"] = lambda _t: []
        flipped = [grammar_blocks(t) for lab, t, want in cases
                   if lab in ("inline code", "fenced", "逐字引文", "未閉合 fence 之後")]
    finally:
        globals()["excluded_spans"] = real
    checks.append(("★ 拿掉排除層後那四格必須翻成擋(證明排除層真的在作用)",
                   all(flipped)))
    return checks


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--selftest"]:
        checks = _selftest()
        for lab, ok in checks:
            print("  %s %s" % ("✓" if ok else "✗", lab))
        bad = [lab for lab, ok in checks if not ok]
        print("合成樣本 %d 格,失敗 %d" % (len(checks), len(bad)))
        return 1 if bad else 0
    if argv[:1] == ["--stdin"] and len(argv) == 1:
        # ★ dm-ctl 的多目標閘走這一支:稿在記憶體裡,寫成暫存檔只是多開一個
        #   「清不掉會留下別席的稿」的面。讀不到 stdin 一律 rc=2 —— 呼叫端把
        #   非 0 一律當擋,所以這裡不需要、也不可以有「讀不到就放行」那一格。
        try:
            txt = sys.stdin.read()
        except Exception as err:
            print("讀不到 stdin:%s" % err, file=sys.stderr)
            return 2
    elif argv[:1] == ["--file"] and len(argv) == 2:
        try:
            with open(argv[1], encoding="utf-8") as fh:
                txt = fh.read()
        except OSError as err:
            print("讀不到:%s" % err, file=sys.stderr)
            return 2
    else:
        print("用法:vocative_lint.py --file F | --stdin | --selftest",
              file=sys.stderr)
        return 2
    hits = vocative_hits(txt)
    if not hits:
        return 0
    # ★ 指出是哪幾處,並且**不把那個字印出來**——印出來的話,
    #   任何掃 stdout 的判準都會被這行說明自己餵成假紅(EH.27 同族)。
    print("稱呼位置命中 %d 處,字元 offset:%s"
          % (len(hits), ",".join(str(h) for h in hits)), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
