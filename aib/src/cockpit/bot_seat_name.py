#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""bot 在對方 app 裡的名稱後面掛上「目前綁定的 AIB 席碼」(the maintainer 09-25)。

Grok Bot(agent 名稱)與 ChatGPT 桌面(CodexBot 的對話標題)共用這一份規則,不各寫一份。
★ 這支在 grokbot 與 codexbot-seat-name 兩個分支上必須逐位元組相同(a seat PSC 放行條件);改一邊要同一輪改另一邊。

  已綁定:with_seat_tag("AIB", "e2e01")   -> "AIB ( 🔗 a seat )"
  關席後:with_unbound("AIB ( 🔗 a seat )") -> "AIB ( 未綁定AIB席位 )"
  strip_seat_tag 拿掉結尾所有 AIB 加的尾巴,三種都認:
      「( @xxxxx )」(09-25 第一版)、「( 🔗 @xxxxx )」、「( 未綁定AIB席位 )」;半形或全形括號都認。
席碼形狀照 seat_address:4–8 位小寫英數。
"""
from __future__ import annotations

import re

BOUND_ICON = "\U0001F517"          # 🔗 the maintainer 09-25:「已綁定的改成 ( emoji icon @xxxxx )」
UNBOUND_TEXT = "未綁定AIB席位"      # the maintainer 09-25:「關席取消舊碼 顯示 ( 未綁定AIB席位 )」
_OPEN, _CLOSE = "[(\\uFF08]", "[)\\uFF09]"   # 半形 ( ) 或全形 （ ）
SUFFIX_RE = re.compile(
    r"\s*" + _OPEN + r"\s*(?:(?:\S{1,4}\s*)?@[0-9a-z]{4,8}|" + re.escape(UNBOUND_TEXT) + r")\s*" + _CLOSE + r"\s*$")
CODE_RE = re.compile(r"^[0-9a-z]{4,8}$")
MAX_BASE = 60


def strip_seat_tag(name: str) -> str:
    """拿掉結尾所有 AIB 加的尾巴(綁定碼或未綁定標記)。"""
    s = (name or "").strip()
    while True:
        t = SUFFIX_RE.sub("", s).strip()
        if t == s:
            return s
        s = t


def _base(name: str, fallback: str) -> str:
    return strip_seat_tag(name)[:MAX_BASE].strip() or fallback


def with_seat_tag(name: str, code: str, fallback: str = "bot") -> str:
    code = (code or "").strip().lstrip("@").lower()
    if not CODE_RE.match(code):
        raise ValueError("席碼形狀不對:%r" % code)
    return "%s ( %s @%s )" % (_base(name, fallback), BOUND_ICON, code)


def with_unbound(name: str, fallback: str = "bot") -> str:
    return "%s ( %s )" % (_base(name, fallback), UNBOUND_TEXT)
