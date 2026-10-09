#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""「長訊已落檔」指標信的格式(10-08 從 pending_edit 搬到核心:訊息待審做成收費外掛後,核心的 dm_text 仍要認得指標信)。"""
import re

RELAY_RE = re.compile(
    r"^長訊已落檔\((\d+) 位元組(?:,來自 @[0-9a-z]+)?\),請讀:(.+)\s*$"
)


def relay_path_of(msg):
    m = RELAY_RE.match(str(msg or "").strip())
    if not m:
        return None
    path = m.group(2).strip()
    if not path.startswith("/"):
        return None
    return path
