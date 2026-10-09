#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""總進度只能從大綱長出來,不能從工作日誌長出來。

★ 為什麼有這支檔(2026-09-09 13:1x,the maintainer 當面指出):
  我交了一份「總進度 75%」的回報,而那 75% 是**我自己做過的事**的完成率。
  真正被派的大綱 A–G **27 項只完成 4 項(15%)**。
  ⇒ 我的回報樹是**用「我做過什麼」圈母體**的,所以
    **沒有人碰過的項目在那份回報上結構性地看不見** —— 它們不是被標成未完成,
    是根本沒有出現。而一份沒有它們的回報,看起來仍然完整。

★ 修法不是「記得補上」,是**換一個母體**:
  樹從大綱生成,狀態再套上去。沒碰過的項目自然出現為 `[ ]`,
  因為它們在大綱裡,不在我的日誌裡。

★ 三道閘,各擋一種「看起來完整」的表:
  rc=3  大綱有的項目在看板上沒有列 ⇒ 它會從回報裡消失
  rc=4  看板上有、大綱沒有的列 ⇒ 那是大綱外的包,必須標成例外並寫理由
  rc=5  ★ 看板用一列蓋掉多項(例如 `B1–B4` 擠成一列)
        ⇒ **個別狀態量不到**,而那一列看起來像已經回答過了

★ 分母一律是大綱項數。**不得用「我完成幾件」當分母** —— 那正是 75% 的來源。
"""

import argparse
import json
import os
import pathlib
import re
import sys

# ★ 字串一律走既有的 i18n 正本(i18n/{zh-TW,en,ja,ko}.json + src/cockpit/i18n_lib.py)。
#   不在本檔內建第二套字串表 —— 今天已經因為「造第二個載體」付過一次學費。
# ★ parents[2] 才是庫根(parents[0]=dispatch、parents[1]=tools)。今天第三次算錯這一層,
#   而算錯的後果是**載到主庫那份 i18n**、印出 `!!key!!` —— 那個顯眼的失效值是 i18n_lib 的功勞,
#   它沒有靜默退回英文,否則我會以為翻譯生效了。
# ★★ 順序是承重的(33f17 2026-09-09 23:3x 抓到):連續兩次 insert(0) 會讓
#   **後 insert 的排前面** ⇒ 主庫會贏本樹,而 i18n_lib 的字典根綁 __file__
#   ⇒ 載到主庫那份就讀主庫的 i18n/*.json,本樹的新鍵不存在。
#   ★ 我這裡沒中是**運氣**:主庫那條本來就在 sys.path,第二次 insert 被跳過。
#   ⇒ 本樹用 insert(0)、fallback 用 append。
_here = pathlib.Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
_fallback = pathlib.Path.home() / "ez2-aib" / "src" / "cockpit"
if str(_fallback) not in sys.path:
    sys.path.append(str(_fallback))
import i18n_lib  # noqa: E402

LOC = "zh-TW"   # 由 main() 依 --locale 覆寫;模組層給值免得 import 時炸


def T(key, params=None):
    """★ 唯一的字串入口。第一版有些呼叫**跨行**沒被改到 locale,
    於是它們偷偷靠 EZ2_AIB_LOCALE 這條間接路 —— 刀砍掉 LOC 也不痛不癢,
    紅集合是空的。⇒ 收成一個入口,locale 只在這裡決定一次。"""
    return i18n_lib.t(key, params, locale=LOC)
RC_OK = 0
RC_MISSING_FROM_BOARD = 3
RC_OFF_OUTLINE_ROW = 4
RC_COLLAPSED_ROW = 5
RC_NO_OWNER = 6

ITEM_RE = re.compile(r"^\*\*([A-G]\d+[a-z]?)\s+(.+?)\*\*", re.M)
# ★★ 13:2x the maintainer:「為什麼有 ⬜?正式辨認,徹底根除」——
#   而第一版把兩件完全不同的事壓成同一個 ⬜:
#     (甲) 依賴還沒滿足 ⇒ **本來就不能開始**,不是失職
#     (乙) 依賴都滿足了而沒人做 ⇒ **這才是要根除的那一種**
#   壓在一起就永遠找不出根因:分母裡混著 13 個不可能開工的項,
#   於是「85% 沒做」看起來像全面停擺,而真正空著的只有 9 項。
#   ⇒ 依賴從大綱那一行現查(`依賴 X`),不另建一份圖 —— 第二份圖會分岔。
DEP_RE = re.compile(r"依賴\s*([A-G][0-9a-z]*(?:\s*[、,,]\s*[A-G][0-9a-z]*)*)")
# ★★ 13:3x:`[x]` 原本同時是「席位說做完了」與「主導席複驗過」。
#   兩者在看板上逐字相同,而只有後者能進完成數 —— 今天所有 ✅ 都是我自己重跑過的,
#   但看板上看不出那個差別。⇒ 拆成兩個記號,而 percent 只數 VERIFIED。
VERIFIED_MARKS = {"[x]"}      # 主導席複驗過
CLAIMED_MARKS = {"[c]"}       # 席位宣稱完成,尚未複驗 —— 不計入完成數
DONE_MARKS = VERIFIED_MARKS

# ★★ 13:2x the maintainer:「你要有正確的 icon 而不是一個白色方塊 ⬜」——
#   我在聊天裡手畫表格時自創了 `⬜`,而它**不在單源碼系裡**。
#   合法的只有七個,正本是 tools/report/status-enum.json。
#   ★ 自創符不只是格式問題:報表的硬擋對**認不得的符是跳過**,
#     所以那一行的縮排/層級/孤兒檢查會**一起失效** —— 它是一把繞過整組檢查的萬用鑰匙。
#   ★ 成因:聊天不經過渲染器 ⇒ 單源管不到我手打的字。
#     修法不是「記得查表」,是**讓這支工具直接印出正本的符**,我不再有機會挑。
#
# 大綱看板有自己的四個記號(§14.0:[ ] 未開始 / [~] 在飛 / [x] 完成 / [!] 卡住待裁),
# 那是**另一個介面的合法詞彙**,不是第二份權威。兩者之間的對應寫在這裡一次:
KIND_TO_ENUM_KEY = {
    "done": "done",
    "claimed": "in_progress",     # 宣稱完成待複驗 —— 不得用 done 的符
    "ready": "pending",            # 依賴都滿足了而沒人做 —— 這才是要根除的那一種
    "blocked": "waiting_prereq",   # 上游沒完成,本來就不能開始,不是失職
    "suspended": "not_doing",      # 裁定暫緩:刻意不做
    "dep_unreadable": "blocked",   # 讀不到那一行 = 分不出,不得混進上面任何一類
}


def load_marks(enum_path=None):
    """符一律從正本讀。★ 讀不到就紅,**不得有 fallback 預設值** ——
    那個檔自己寫著:fallback 就是失效值等於安全值。"""
    cands = [enum_path] if enum_path else []
    cands += [pathlib.Path(__file__).resolve().parents[2] / "tools" / "report" / "status-enum.json",
              pathlib.Path.home() / "ez2-aib" / "tools" / "report" / "status-enum.json"]
    for c in cands:
        if c and pathlib.Path(c).exists():
            return json.loads(pathlib.Path(c).read_text())["marks"], str(c)
    raise SystemExit("outline-progress: %s" % i18n_lib.t("aib.outline.err_no_enum"))
SUSPENDED_MARKS = {"[-]"}
# ★ 字元類要含**大寫**:第一版漏了,於是 `A2 / A3` 這種列根本沒被解析到,
#   被誤判成「這兩項沒有列」而不是「一列蓋多項」。兩種都會叫,但診斷不同 ——
#   而診斷錯的那一種會讓人去**補列**,而不是去**拆列**。臂抓到的。
BOARD_ROW_RE = re.compile(r"^\|\s*\**([A-Ga-z0-9/–\-— ]*?)\**\s*\|\s*`?(\[.\])`?", re.M)
# ★★ 合議 layers-0909 補件(1):每一格要有 owner。
#   理由是三席一致指出的那一格 —— 分層之後**同一格會有兩個人維護**,
#   而先改的會被後改的蓋掉,**且不會紅**。所以「誰有權改這一格」必須是單數且寫下來。
#   ★ `——` 這種佔位符**不算 owner**:它與「還沒指派」同形,而那正是要分開的兩件事。
BOARD_OWNER_RE = re.compile(
    r"^\|\s*\**([A-Ga-z0-9/–\-— ]*?)\**\s*\|\s*`?\[.\]`?[^|]*\|([^|]*)\|", re.M)
PLACEHOLDER_OWNERS = {"", "—", "——", "-", "--", "?", "待定", "TBD", "tbd"}
# 一列蓋多項:`A3 / A4`、`B1–B4`、`D–G`
COLLAPSED_RE = re.compile(r"[/–—]|\bB1-B4\b")


def deps_of(md: str, code: str):
    """從大綱那一行現查依賴。★ 讀不到那一行要說出來,不得回空集合 ——
    空集合的意思是「沒有依賴、可立刻開工」,而那與「我沒讀到」相反。"""
    m = re.search(r"^\*\*%s\s+.*$" % re.escape(code), md, re.M)
    if not m:
        return None
    d = DEP_RE.search(m.group(0))
    if not d:
        return []
    return re.split(r"\s*[、,,]\s*", d.group(1).strip())


def classify(outline, marks, md):
    """每一項分四類。★ ready 的定義是「依賴全部 [x]」,不是「看起來可以做」。"""
    done = {c for c, _ in outline if marks.get(c) in DONE_MARKS}
    out = {}
    for code, _ in outline:
        mk = marks.get(code)
        if mk in VERIFIED_MARKS:
            out[code] = ("done", [])
            continue
        if mk in CLAIMED_MARKS:
            # ★ 宣稱完成不是完成:它在依賴上也**不得**當成滿足,
            #   否則下游會以一個沒被驗過的東西為基礎往前走。
            out[code] = ("claimed", [])
            continue
        if mk in SUSPENDED_MARKS:
            out[code] = ("suspended", [])
            continue
        ds = deps_of(md, code)
        if ds is None:
            # ★ 讀不到那一行 ⇒ 不猜,單獨一類。它與「沒有依賴」相反。
            out[code] = ("dep_unreadable", [])
            continue
        unmet = [d for d in ds if d not in done]
        out[code] = ("ready", []) if not unmet else ("blocked", unmet)
    return out


def parse(md: str):
    outline = ITEM_RE.findall(md)
    board_block = md.split("## 14.0.5", 1)
    board = []
    if len(board_block) > 1:
        seg = board_block[1].split("### ", 1)[0]
        board = BOARD_ROW_RE.findall(seg)
    return outline, board


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="總進度從大綱生成,不從工作日誌生成")
    ap.add_argument("spec")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--require-owner", action="store_true",
                    help="沒有 owner 的格要紅(合議 layers-0909 補件 1)")
    ap.add_argument("--locale", help="zh-TW / en / ja / ko;不給就照環境")
    ap.add_argument("--status-enum", help="符的正本;預設找 tools/report/status-enum.json")
    args = ap.parse_args(argv)

    global ENUM_MARKS, LOC
    # ★★ 第一版我寫成 EZ2_LOCALE,而正本吃的是 EZ2_AIB_LOCALE ——
    #   後果是四個語系印出**同一種語言**,而四次執行全部 rc=0。
    #   這是最危險的一種假綠:失效時它照樣產出看起來完整的輸出。
    #   ⇒ 改成把 locale **明給** t(),不靠環境變數這條間接的路。
    if args.locale:
        os.environ["EZ2_AIB_LOCALE"] = args.locale
    LOC = i18n_lib.resolve_locale(args.locale)
    ENUM_MARKS, enum_src = load_marks(args.status_enum)
    md = pathlib.Path(args.spec).read_text()
    outline, board = parse(md)
    if not outline:
        sys.stderr.write("outline-progress: %s\n" % T("aib.outline.err_no_outline"))
        return 2

    owners = {}
    for label, own in BOARD_OWNER_RE.findall(md.split("## 14.0.5", 1)[1].split("### ", 1)[0]
                                             if "## 14.0.5" in md else ""):
        owners[label.strip()] = own.strip().strip("`").strip()
    marks, collapsed = {}, []
    for label, mark in board:
        label = label.strip()
        if COLLAPSED_RE.search(label):
            collapsed.append(label)
            continue
        marks[label] = mark

    kinds = classify(outline, marks, md)
    rows, counts = [], {}
    for code, title in outline:
        m = marks.get(code)
        kind, unmet = kinds[code]
        own = owners.get(code, "")
        rows.append({"code": code, "title": title, "mark": m or "[?]",
                     "owner": own,
                     "owner_is_placeholder": own in PLACEHOLDER_OWNERS,
                     "on_board": m is not None, "kind": kind, "unmet_deps": unmet,
                     # ★ icon 由正本查出來,不是這裡挑的。
                     "icon": ENUM_MARKS[KIND_TO_ENUM_KEY[kind]],
                     "enum_key": KIND_TO_ENUM_KEY[kind]})
        counts[m or "[?]"] = counts.get(m or "[?]", 0) + 1

    total = len(outline)
    done = counts.get("[x]", 0)
    off_outline = [l for l in marks if l not in {c for c, _ in outline}]

    no_owner = [r["code"] for r in rows
                if r["on_board"] and r["owner_is_placeholder"]
                and r["kind"] not in ("done", "suspended")]
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r["code"])
    out = {
        "denominator_is": T("aib.outline.denominator"),
        "locale": LOC,
        "marks_source": enum_src,
        # ★ 這一欄是本工具最重要的:沒有它,「可以做而沒人做」與
        #   「上游還沒完成」會被壓成同一個未完成,而只有前者是要根除的。
        "ready_now": by_kind.get("ready", []),
        "blocked_by_dependency": by_kind.get("blocked", []),
        "suspended_by_ruling": by_kind.get("suspended", []),
        # ★ 這一欄不得併進 done:席位說完成與主導席驗過完成,在看板上逐字相同。
        "claimed_awaiting_verification": by_kind.get("claimed", []),
        "dependency_line_unreadable": by_kind.get("dep_unreadable", []),
        "total_outline_items": total,
        "done": done,
        "percent": round(100.0 * done / total, 1),
        "by_mark": counts,
        "rows": rows,
        "collapsed_rows": collapsed,
        "rows_not_in_outline": off_outline,
        "rows_without_owner": no_owner,
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(T("aib.outline.summary", {
            "trunk": T("aib.outline.trunk_delegated"),
            "total": total, "verified": done, "percent": out["percent"],
            "denominator": T("aib.outline.denominator")}))
        print("★ " + T("aib.outline.kinds", {
            "ready": len(out["ready_now"]),
            "blocked": len(out["blocked_by_dependency"]),
            "suspended": len(out["suspended_by_ruling"]),
            "unreadable": len(out["dependency_line_unreadable"])}))
        if out["claimed_awaiting_verification"]:
            print(T("aib.outline.claimed_awaiting_verify",
                             {"items": ", ".join(out["claimed_awaiting_verification"])}))
        for r in rows:
            tail = ("← " + T("aib.outline.waiting_for",
                                      {"deps": ",".join(r["unmet_deps"])})) if r["unmet_deps"] else ""
            print("  %s %-4s %-14s %s %s"
                  % (r["icon"], r["code"], r["kind"], r["title"], tail))

    rc = RC_OK
    missing = [r["code"] for r in rows if not r["on_board"]]
    if missing:
        sys.stderr.write("outline-progress: %s\n"
                         % T("aib.outline.err_missing_rows",
                                      {"items": ", ".join(missing)}))
        rc = max(rc, RC_MISSING_FROM_BOARD)
    if off_outline:
        sys.stderr.write("outline-progress: %s\n"
                         % T("aib.outline.err_off_outline",
                                      {"items": ", ".join(off_outline)}))
        rc = max(rc, RC_OFF_OUTLINE_ROW)
    if no_owner and args.require_owner:
        sys.stderr.write("outline-progress: %s\n"
                         % T("aib.outline.err_no_owner", {"items": ", ".join(no_owner)}))
        rc = max(rc, RC_NO_OWNER)
    if collapsed:
        sys.stderr.write("outline-progress: %s\n"
                         % T("aib.outline.err_collapsed",
                                      {"items": ", ".join(collapsed)}))
        rc = max(rc, RC_COLLAPSED_ROW)
    return rc


if __name__ == "__main__":
    sys.exit(main())
