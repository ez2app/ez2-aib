#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""回報裡「省多少」的數字閘(2026-10-08,the maintainer:「不能只靠記憶,如何程式化」「把回報省多少格式也一併程式化」)。

## 它擋什麼

回報散文段(摘要、進度說明、反思、腳註)裡**講省了多少 / 降到多少 / 只要多久**的句子,
數字要嘛是量過的(句子裡寫「實測」),要嘛照實標成推估(「推估」「預計」「估計」)。兩樣都沒有 ⇒ 硬擋。

    不合格   多數能從 1 小時降到十幾分鐘
    不合格   可能只要幾分鐘
    合格     推估:重讀舊記錄可省到約 5 分鐘(未實測)
    合格     省下約 2.4 小時(實測,10-07 13:20 那一輪)
    不合格   省下約 2.4 小時(實測)            ← 10-08 起:實測要說哪一輪(時間),查得回統計紀錄

## 為什麼需要

10-07 深夜我兩次把沒量過的數字講成結果(「降到十幾分鐘」實際量了只有 2/60 個提交省得到;
「語言檔害整套重跑」是照工具輸出轉述)。規則寫在 REPORTING-RULES 第五條附近已經很久,一樣擋不住
—— 跟 jargon_gate 同一個理由:提醒層與硬條件層要各做一次。

## 範圍

只看散文段(jargon_gate.prose_lines,同一個範圍定義,不抄第二份):樹狀區塊的每一項本來就要帶憑據與指令。
數字認阿拉伯數字與中文數字(十幾、幾、兩、半)—— 第一版只認阿拉伯數字,上面那兩句真實的錯話都沒擋住。
"""
import re

try:
    from jargon_gate import prose_lines
except Exception:  # noqa: BLE001 - 單獨載入時(治具)退回全篇
    def prose_lines(text):
        return list(enumerate(text.splitlines(), 1))

_NUM = r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百兩幾半]+)"
_UNIT = r"(?:秒|分鐘|小時|天|%|倍)"
CLAIM_RE = re.compile(r"(?:省|少等|降到|降為|縮短|快了?|只要|只花)[^。;;\n]{0,24}?" + _NUM + r"\s*(?:多)?" + _UNIT)
LABEL_RE = re.compile(r"實測|推估|預計|估計|估算")
GUESS_RE = re.compile(r"推估|預計|估計|估算")
# 10-08:標「實測」的要說是哪一輪(時間),查得回統計紀錄(aib ship stats --at "MM-DD HH:MM");只寫「實測」兩個字誰都能打
WHEN_RE = re.compile(r"\d{1,2}:\d{2}")


def _bad(line):
    if not CLAIM_RE.search(line):
        return False
    if not LABEL_RE.search(line):
        return True
    return not GUESS_RE.search(line) and not WHEN_RE.search(line)


def offenders(text: str):
    """[(行號, 行)]:散文段裡有省多少的數字、卻沒標實測也沒標推估的行;或標了實測卻沒說哪一輪(時間)的行。"""
    return [(no, line) for no, line in prose_lines(text) if _bad(line)]


def check(text: str):
    """回 (ok, 訊息)。"""
    bad = offenders(text)
    if not bad:
        return True, ""
    out = ["回報裡有**沒標實測、也沒標推估的「省多少」數字**(the maintainer 10-08:省多少要程式化,不靠記憶):"]
    for no, line in bad[:5]:
        out.append("  第 %d 行 %r" % (no, line[:70]))
    out.append("  改法:量過的寫「實測」並說哪一輪的時間(例「實測,10-07 13:20 那一輪」,查證用 aib ship stats --at);沒量過的寫「推估」。")
    out.append("  例:「推估:可省到約 5 分鐘(未實測)」而不是「只要幾分鐘」。")
    return False, "\n".join(out)
