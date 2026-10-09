#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""B18.4:開席單「投了而沒有回執」的缺口,可列舉、可判、母體說得出來。

契約 `docs/criteria/CONTRACT-B18.4-onboard-ack-gap-0829.md`(dev2 `a seat` = `09835cd8`)
臂   `tests/test-b184-onboard-ack-gap.py`(`a seat` = `f0d7ba92` + 13 與校準端)
裁定 cap `a seat` 08-29 18:0x **重判**(原形撤銷)+ 18:0x 三裁 + 22:18 兩裁。

## 為什麼原形被撤銷,而這一支指向第三段

三段閘的老話:**存在 → 被經過 → 響了有人聽**。實測前兩段都有:

    存在   `docs/templates/SEAT-ONBOARD.md` 116 行,回報格式段 51–54、狀態符 enum 67–80 都在
    被經過 `src/cockpit/aib` 開席尾段自動呼 `seat_onboard.py --detach-ack`
    有留痕 帳本 seat-onboard actor 105 筆

**缺的是第三段**:投出去了,沒有人回。這一支就是把那一段變成可列舉、可判。

## ★ 不釘 `stale`,只釘 `no_ack`

`SEAT-ONBOARD.md` 08-29 一天被改四次。模板一改,**全機回執全部變 stale** ——
拿 `stale` 當判準等於「每次改範本紅一輪」,而**恆紅的測試等於沒有測試**。
(同型於 B13:綁住自己 commit 的收據寫不出來。)
⇒ `stale` 照樣列出來(它是「該補投」的訊號),但**不翻 rc**。

## ★★ 母體為空 ⇒ rc 3,不是 rc 0

母體空的時候,「缺口 0」與「掃不到東西」在畫面上**逐字相同**,而前者會讓人停手。
`rc` 是呼叫端唯一一定會看的東西,臂不是 —— **把分辨力放在呼叫端一定會看的那個欄位上**。

## ★★ 母體 = 主艙 + 所有 instances

    <狀態根>/seats/w*.json                  艙名 "main"(`seats_dir_lib` 檔頭:main 對應狀態根本身)
    <狀態根>/instances/<艙>/seats/w*.json   艙名 = 目錄名

**不可以用 `seats_dir()`** —— 它只解得到**一個**艙。實測那條路只看得到 2 席,
而全機是 91 席;漏掉的那些**將來出現的缺口永遠不會被列出來,而清單看起來仍然完整**。

## ★★★ 分類只有一份:`seat_status.onboard_reason`

本檔**不寫第二份判斷**。而且是 `import seat_status` + **呼叫點屬性存取**,
不是 `from seat_status import onboard_reason` —— 兩者在替身之下行為不同(實測):

    from X import f    → 之後改 X.f 對它**沒有作用**(替身無聲失效)
    import X; X.f(...) → 替身生效

★ 屬性呼叫是「單源」這個性質**唯一能被行為測到**的形。
★ 與 `seat_onboard.py` 的 from-import 寫法**刻意分歧**,**不要去「修一致」** ——
  改回慣例寫法會讓臂 05 安靜地失去承重。
"""

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# ★ 屬性呼叫的前提:import 模組本身,不是 import 裡面那支函式。理由見檔頭。
import seat_status                                          # noqa: E402
import seats_dir_lib                                        # noqa: E402

CABIN_MAIN = "main"          # `seats_dir_lib` 檔頭的既有詞彙,不是本檔造的
RC_CLEAN, RC_GAP, RC_UNDETERMINED = 0, 1, 3


class Undetermined(Exception):
    """判不出來。**不是 0 也不是 1** —— 三態不得互相塌陷。"""


def _seat_onboard():
    """延遲載入:`seat_onboard` 會 import 本檔(`list_missing`),模組層互 import 會成環。

    ★ 走 `sys.modules` 的一般 import —— **不用 `spec_from_file_location` 另載一份**:
      另載會產生第二個模組物件,讓替身臂靜靜失效(實測 `other is mod` = False)。
    """
    import seat_onboard
    return seat_onboard


def _iter_seat_files(state_root):
    """(艙名, 席位檔路徑) —— 主艙 + 所有 instances,一個都不能少。"""
    import glob
    main_dir = os.path.join(state_root, "seats")
    for path in sorted(glob.glob(os.path.join(main_dir, "w*.json"))):
        yield CABIN_MAIN, path
    inst_root = os.path.join(state_root, "instances")
    for cabin in sorted(os.listdir(inst_root)) if os.path.isdir(inst_root) else []:
        seats = os.path.join(inst_root, cabin, "seats")
        if not os.path.isdir(seats):
            continue                    # 艙目錄在而沒有 seats/:不是席,不是錯
        for path in sorted(glob.glob(os.path.join(seats, "w*.json"))):
            yield cabin, path


def _population(state_root):
    """回 [{"cabin","window","seat_id"}]。讀不動的席位檔跳過,但**要能被數出來**。"""
    rows, unreadable = [], 0
    for cabin, path in _iter_seat_files(state_root):
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            unreadable += 1
            continue
        seat_id = rec.get("seat_id")
        if not isinstance(seat_id, str) or not seat_id:
            unreadable += 1
            continue
        rows.append({"cabin": cabin,
                     "window": rec.get("window_id"),
                     "seat_id": seat_id})
    return rows, unreadable


def _index(ledger_path):
    """帳本 → (index, bad_lines)。**解析器向 `seat_onboard.index_events` 借,不寫第二份。**

    ★ 整檔壞行 ⇒ `Undetermined`:那與「全席從未投遞」在資料上同形,
      而處置完全相反(去修帳本 / 去補投)。
    """
    if not os.path.isfile(ledger_path):
        raise Undetermined("帳本不存在:%s" % ledger_path)
    try:
        with open(ledger_path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as e:
        raise Undetermined("帳本讀不起來(%r)" % (e,))
    nonempty = [ln for ln in lines if ln.strip()]
    index, bad = _seat_onboard().index_events(nonempty)
    if nonempty and bad == len(nonempty):
        raise Undetermined(
            "帳本 %d 列全部解不開 —— 那與「全席從未投遞」在資料上同形,"
            "而處置相反(修帳本 vs 補投)" % bad)
    return index, bad


def _template_sha(template_path=None, template_sha=None):
    if template_sha:
        return template_sha
    if template_path:
        try:
            with open(template_path, "rb") as fh:
                return hashlib.sha256(fh.read()).hexdigest()
        except OSError as e:
            raise Undetermined("模板讀不到 %s(%r)" % (template_path, e))
    sha = _seat_onboard().current_template_sha()
    if not sha:
        raise Undetermined("讀不到現行開席單模板")
    return sha


def ack_gap(state_root=None, ledger_path=None, template_sha=None):
    """回四格分類 + 母體 + 壞行數。母體為空 ⇒ `Undetermined`(見檔頭)。

    ★ 分類**一律** `seat_status.onboard_reason(entry, sha)`;本檔不判。
    """
    if state_root is None:
        state_root = seats_dir_lib.state_root()[0]
    if ledger_path is None:
        ledger_path = _seat_onboard().default_ledger_path()
    sha = _template_sha(template_sha=template_sha)

    population, unreadable = _population(state_root)
    if not population:
        raise Undetermined(
            "母體為空:%s 底下(主艙與所有 instances)找不到任何席位檔 —— "
            "這不是「沒有缺口」,是沒有東西可以判" % state_root)

    index, bad = _index(ledger_path)
    out = {"template_sha": sha, "population": population, "bad_lines": bad,
           "unreadable_seat_files": unreadable,
           "no_ack": [], "stale": [], "never": [], "acked_current": [],
           # ★★ 帳本上是 no_ack、而**全機找不到席位檔**的席。它們補投不到,
           #   所以不進 `no_ack`(那是「可處置」的清單,rc 也綁在它上面);
           #   但**也不能就這樣消失** —— 母體用席位檔當來源,天生看不見它們,
           #   而看不見的那些正是「乾淨而不完整的清單」的入口。
           #   實測 08-29:帳本端 no_ack 11 席,其中 **6 席全機零個席位檔**
           #   (只有 5 席落得進 `no_ack`)。少了這一格,那 6 席永遠不會有人提起。
           "no_ack_unreachable": []}
    bucket = {seat_status.ONBOARD_NO_ACK: "no_ack",
              seat_status.ONBOARD_STALE: "stale",
              seat_status.ONBOARD_NEVER: "never",
              None: "acked_current"}
    # ★ 先記下「帳本說 no_ack、但母體裡沒有」的那些:母體是席位檔,而席位檔會被刪。
    seen = {row["seat_id"] for row in population}
    for seat_id, entry in sorted(index.items()):
        if seat_id in seen:
            continue
        if seat_status.onboard_reason(entry, sha) == seat_status.ONBOARD_NO_ACK:
            out["no_ack_unreachable"].append(
                {"cabin": None, "window": None, "seat_id": seat_id,
                 "reason": seat_status.ONBOARD_NO_ACK})

    for row in population:
        # ★ 屬性呼叫,不是 from-import:這一行就是「單源可被行為測到」的那個形。
        reason = seat_status.onboard_reason(index.get(row["seat_id"]), sha)
        if reason not in bucket:
            # ★★ 有人在 `seat_status` 加了第四態而沒接上這裡。
            #   **裸 KeyError 會被讀成「產品壞了」;沉默地丟掉那一席會被讀成「沒問題」。**
            #   兩個都不對 —— 這是「判不出來」:我認得的格子裡沒有它。
            #   ★ 這一格是刻意留的:本檔 08-29 現查有三個消費端在列舉這個 enum
            #     (本檔的 bucket、`pane-right-ai.sh` 的 should_mark_onboard、
            #      `seat_onboard.py` 的 --missing want 集合),
            #     **而另外兩個漏接第四態的表現都是靜默的**(面板不標、預設不補投)。
            #     至少讓這一個出聲。
            raise Undetermined(
                "分類器回了本檔不認得的態 %r(席 %s)—— "
                "有人加了新態而沒接上消費端;本檔不猜,也不把它丟掉"
                % (reason, row["seat_id"]))
        item = dict(row)
        item["reason"] = reason
        out[bucket[reason]].append(item)
    return out


def _fmt(row):
    return "%-10s %-5s %s" % (row.get("cabin") or "?",
                              row.get("window") or "?", row["seat_id"])


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="onboard_gap",
        description="列出開席單「投了而沒有回執」的席。rc 0 無缺口 / 1 有 / 3 判不出來。")
    ap.add_argument("--state-root", default=None)
    ap.add_argument("--ledger", default=None)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--template", default=None, help="拿這個檔算模板 sha(臂用)")
    g.add_argument("--template-sha", default=None, help="直接給模板 sha(臂用)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        sha = _template_sha(args.template, args.template_sha)
        got = ack_gap(state_root=args.state_root, ledger_path=args.ledger,
                      template_sha=sha)
    except Undetermined as e:
        sys.stderr.write("onboard_gap: 判不出來 —— %s\n" % e)
        sys.stderr.write("  ★ 這不是「沒有缺口」,是「沒判成」。rc=3 與 0/1 是三件事。\n")
        return RC_UNDETERMINED

    if args.json:
        # ★ 總計不讓兩端各自加一次:機器端與人讀端加總分岔的那一天,不會有任何一格叫。
        got["no_ack_total"] = len(got["no_ack"]) + len(got["no_ack_unreachable"])
        print(json.dumps(got, ensure_ascii=False))
    else:
        print("模板 %s  母體 %d 席(主艙 + 所有 instances)"
              % (got["template_sha"][:12], len(got["population"])))
        print("帳本壞行 %d;讀不動的席位檔 %d"
              % (got["bad_lines"], got["unreadable_seat_files"]))

        print("回執停在舊模板 %d 席(**不是缺陷**:模板一改就全體如此,不翻 rc)"
              % len(got["stale"]))
        print("從未投遞 %d 席;已回執(現行模板)%d 席"
              % (len(got["never"]), len(got["acked_current"])))
        # ★★ 總計行(裁定 B184a §三.1,承重):三個數字綁在同一行,而且 A+B==N。
        #   為什麼非有不可:先前 5 與 6 分兩行印,**中間沒有任何東西說「5 是子集」**。
        #   讀的人記住的是 rc 綁的那個 5,而 5 看起來就是全部 ——
        #   **B18.4 要修的病,原封不動長在它自己的輸出上。**
        a = len(got["no_ack"])
        b = len(got["no_ack_unreachable"])
        print("\n帳上缺回執 %d 席 = 可補投 %d + 補投不到 %d" % (a + b, a, b))
        if got["no_ack"]:
            print("\n可補投(現行母體裡找得到席位檔):")
            for row in got["no_ack"]:
                print("  " + _fmt(row))
        if got["no_ack_unreachable"]:
            # ★ 這些席**只有這一格提得到**:母體以席位檔為來源,而它們的席位檔已經不在。
            #   rc 不綁它們(補投不到,不是可處置項),但它們要被算進總計行的 N。
            print("\n補投不到(全機找不到席位檔,只能當歷史):")
            for row in got["no_ack_unreachable"]:
                print("  %-10s %-5s %s" % ("—", "—", row["seat_id"]))
        if not got["no_ack"] and not got["no_ack_unreachable"]:
            print("  (無)")
    return RC_GAP if got["no_ack"] else RC_CLEAN


if __name__ == "__main__":
    sys.exit(main())
