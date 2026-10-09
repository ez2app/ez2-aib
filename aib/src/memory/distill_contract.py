#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""E5.2 蒸餾契約:一則粹煉條目長什麼樣,以及**什麼會被拒收**。

規格正本 `docs/reports/SPEC-distill-golden-sample-0821.md`(§二 三段形狀、§三 landing 型別)。
黃金樣本 `tests/fixtures/distill-golden/`(pos-* 必收、neg-* 必拒)。

★★ 這支檔**刻意不評分**。規格 §四 逐字寫「評分不由作者做」,而我是黃金樣本的作者;
   自己寫的評分器套在自己寫的樣本上,量到的是我自己的口味不是規則的品質。
   機器在這裡只做兩件事:**形狀**與**landing 的型別**。
   「這個原因是不是真的」「這條規則是不是重複」由非作者盲審 —— 那是人的一格,
   而把它寫成程式碼會讓一個假的綠燈出現在最重要的地方。

★★★ 已知覆蓋缺口(具名,不假裝沒有):
   - `rule` / `why` 的期許詞檢查是**詞表**,換個講法就漏得掉(neg-04、neg-09
     刻意留在樣本裡讓審者看見這條檢查的強度)。它擋掉最常見的那一種,不宣稱擋得完。
   - 黃金樣本由我一個人選,**萃煉器漏掉的那一類我剛好也看不見**(規格 §六-3
     `a seat` 被問到的正是這件事)。所以 neg-* 存在的理由不只是「多測一面」:
     只有正例的黃金集合量不到誤收,而**誤收沒有紅燈**。
"""

import json
import os
import re

LANDING_TYPES = ("gate", "command", "swap")

# 期許詞:填了等於沒填的那一類。**弱檢,見檔頭。**
HEDGE = ("小心", "注意", "留意", "記得", "盡量", "儘量", "多加", "謹慎",
         "be careful", "make sure", "remember to", "try to")

REQUIRED = ("rule", "why", "landing")


class ContractError(Exception):
    """拒收。訊息要指名**哪一格**不合,不只說『不合格』。"""


def _hedge_only(text):
    """有期許詞,而且拿掉之後幾乎什麼都不剩(弱檢)。

    ★★ 第一版寫成「拿掉期許詞之後長度 < 6 就拒」,**沒有先檢查有沒有期許詞** ——
      於是它其實量的是**長度**,而 `已推上去了`(5 個字、一句完全具體的字面)
      當場被自己的黃金樣本第一格打回來。
      **一個代理指標在大部分情況下剛好對,所以每次全綠都像做完了。**
      現在兩個條件都要成立才拒:有期許詞 **且** 拿掉之後沒剩下什麼。
    """
    hits = [h for h in HEDGE if h in text]
    if not hits:
        return False
    stripped = text
    for h in hits:
        stripped = stripped.replace(h, "")
    # 標點與空白不算內容
    stripped = re.sub(r"[\s、,,。.;;::「」『』()()!!??]+", "", stripped)
    return len(stripped) < 6


def check_landing(landing, repo_root="."):
    if not isinstance(landing, dict):
        raise ContractError(
            "landing 必須是有型別的物件,不是 %s —— 自由文字正是規格 §三 要擋的"
            % type(landing).__name__)
    kind = landing.get("type")
    if kind not in LANDING_TYPES:
        raise ContractError("landing.type=%r 不在三型 %s 之內;三型之外一律拒收"
                            % (kind, "|".join(LANDING_TYPES)))
    if kind == "gate":
        path = landing.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ContractError("landing(gate) 缺 path")
        full = os.path.join(repo_root, path)
        if not os.path.exists(full):
            raise ContractError(
                "landing(gate) 指名的 %s **不存在** —— 指一支不存在的閘,"
                "與沒有 landing 等價,而它在紙面上看起來已經有閘了" % path)
        needle = landing.get("must_contain")
        if isinstance(needle, str) and needle:
            with open(full, encoding="utf-8", errors="replace") as fh:
                if needle not in fh.read():
                    raise ContractError(
                        "landing(gate) 的 must_contain=%r 在 %s 裡找不到"
                        % (needle, path))
    elif kind == "command":
        cmd = landing.get("cmd")
        if not isinstance(cmd, str) or not cmd.strip():
            raise ContractError("landing(command) 的 cmd 是空的 —— 不可跑")
        if "expect" not in landing and "expect_rc" not in landing:
            raise ContractError(
                "landing(command) 缺 expect/expect_rc —— **一條沒有期望值的指令"
                "跑完永遠成功,它量的是零**")
    else:  # swap
        a, b = landing.get("instead_of"), landing.get("write")
        for name, v in (("instead_of", a), ("write", b)):
            if not isinstance(v, str) or not v.strip():
                raise ContractError("landing(swap) 缺 %s" % name)
        if a.strip() == b.strip():
            raise ContractError(
                "landing(swap) 兩端相同 —— 恆等的替換對是格式正確的空值")
        for name, v in (("instead_of", a), ("write", b)):
            if _hedge_only(v):
                raise ContractError(
                    "landing(swap) 的 %s 只有期許詞(%r),不是具體字面【弱檢】"
                    % (name, v))
    return kind


def check_entry(entry, repo_root="."):
    """一則條目 → landing 型別。不合即 raise ContractError(**fail-closed**)。"""
    if not isinstance(entry, dict):
        raise ContractError("條目不是物件")
    for field in REQUIRED:
        v = entry.get(field)
        if field == "landing":
            continue
        if not isinstance(v, str) or not v.strip():
            raise ContractError(
                "缺 %s。三段(規則/為什麼/landing)缺一即拒 —— 沒有原因的規則"
                "沒辦法被判斷是否還適用,它只會被照抄到不適用的地方" % field)
    if _hedge_only(entry["rule"]):
        raise ContractError("rule 只有期許詞,沒有可執行的動作【弱檢】")
    return check_landing(entry["landing"], repo_root=repo_root)


def check_file(path, repo_root="."):
    with open(path, encoding="utf-8") as fh:
        return check_entry(json.load(fh), repo_root=repo_root)


def main(argv):
    """對一批檔逐一判,rc=0 全收 / rc=2 有拒收(逐檔點名,不只報數)。"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--expect", choices=("accept", "reject"), default="accept",
                    help="這批**應該**被收還是被拒;不符即 rc=2。"
                         "★ 有了它,負例集合才量得到誤收 —— 只跑正例的話,"
                         "一個什麼都收的契約也會全綠")
    args = ap.parse_args(argv)
    bad = []
    for f in args.files:
        try:
            kind = check_file(f, repo_root=args.repo_root)
            got, detail = "accept", "landing=%s" % kind
        except ContractError as err:
            got, detail = "reject", str(err)
        except (OSError, ValueError) as err:
            got, detail = "error", str(err)
        mark = "ok " if got == args.expect else "BAD"
        if got != args.expect:
            bad.append(f)
        print("%s %-8s %-58s %s" % (mark, got, os.path.basename(f), detail))
    print("expect=%s 共 %d 件,不符 %d 件" % (args.expect, len(args.files), len(bad)))
    return 2 if bad else 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
