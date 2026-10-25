#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""一則 dm 的「原話」一行:任務區「正在做」的原話列 / 未拆解前 24 字、收件匣總覽每則一行摘要共用的單一入口。

公開入口:original_line(msg) -> (狀態, 文字)。無副作用(只讀 RELAY 檔,不寫任何東西),import 本檔不動 sys.path。
狀態三個名字各自一個來源(不合併,呼叫端要分得出來):
  OK               取到了,文字非空
  EMPTY            信本身沒有非空行(或只有被剝掉的路由頭 / 回報抬頭)
  RELAY_UNREADABLE 是長訊指標信,但指標檔讀不到 —— 呼叫端印「原話量不到」,**不印路徑、不印指標那行**

取法(a seat 09-15 04:04 裁「第二版 + 2b」= a seat P.3 保留 the maintainer 令):
  a. dm_relay.relay_path_of(msg)(10-08 從 pending_edit 搬來) 判是不是指標信;是 ⇒ 讀 RELAY 檔。
     ★ 不用 pending_edit.load_source:它讀失敗回原文,與「不是指標信」同形。
  b. 第一個非空行若是路由頭 `[… → …]`:以「·」分開的欄裡含「the maintainer 令」(且不含 →)的留下,接在原話前;
     其餘整段剝。路由頭後同一行有字取後面,獨佔一行取下一個非空行。
  c. (剝完路由頭後的)第一個非空行若是回報抬頭 `YYYY-MM-DD HH:MM 台北` ⇒ 跳過,取下一個非空行。
  d. 沒有箭頭的 `[the maintainer 令 · …]` 標籤不是路由頭 ⇒ 連標籤一起原樣印。
形狀分布(09-15 轉送檔 103 份第一行):路由頭獨佔一行 56 / 同行接正文 34 / 回報抬頭 11 / [the maintainer 令] 2。
★ 那是 AI 之間的信;真人信(164b hook 落的)的形狀還沒有樣本。
"""
from __future__ import annotations

import re
from pathlib import Path

OK = "ok"
EMPTY = "empty"
RELAY_UNREADABLE = "relay_unreadable"

ROUTE_HEAD_RE = re.compile(r"^\[[^\]]*→[^\]]*\]")
REPORT_HEAD_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} 台北")
FIELD_SEP_RE = re.compile(r"\s*·\s*")
HUMAN_ORDER = "the maintainer 令"


def wait_text(row):
    """進行中的「等什麼」:row["wait"] 是 dict 且 text 有字 ⇒ 回壓成一行的字，否則 ""。
    計數(dm_queue tabs --counts 的 read_without_wait)跟畫面(render_lib 的 ⏳ 尾巴)共用這一支，同一判斷(#370 r2 L-new3)。"""
    w = row.get("wait") if isinstance(row, dict) else None
    return " ".join(str(w.get("text") or "").split()) if isinstance(w, dict) else ""


def _relay_path_of(msg):
    import dm_relay  # noqa: WPS433  10-08:指標信格式搬到核心(訊息待審做成收費外掛,沒裝時這裡仍要認得)
    return dm_relay.relay_path_of(msg)


def original_line(msg):
    """回 (狀態, 文字);狀態 ∈ OK / EMPTY / RELAY_UNREADABLE。文字是單行、已 strip,不截寬(截寬歸呼叫端)。"""
    raw = msg if isinstance(msg, str) else ""
    path = _relay_path_of(raw)
    if path:
        import dm_relay  # noqa: WPS433
        if not dm_relay.relay_file_ok(path):      # #370 r1 M2:只讀長訊目錄裡的 RELAY-dm-*.txt
            return RELAY_UNREADABLE, ""
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return RELAY_UNREADABLE, ""
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return EMPTY, ""
    i, keep = 0, ""
    head = ROUTE_HEAD_RE.match(lines[0])
    if head:
        kept = [f.strip() for f in FIELD_SEP_RE.split(head.group(0)[1:-1]) if HUMAN_ORDER in f and "→" not in f]
        keep = ("[%s] " % " · ".join(kept)) if kept else ""
        rest = lines[0][head.end():].strip()
        if rest:
            lines[0] = rest
        else:
            i = 1
    if i < len(lines) and REPORT_HEAD_RE.match(lines[i]):
        i += 1
    if i >= len(lines):
        return (OK, keep.strip()) if keep else (EMPTY, "")
    return OK, keep + lines[i]
