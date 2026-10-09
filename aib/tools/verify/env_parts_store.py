#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""「環境變了 ⇒ 重跑」說得出是哪幾項(10-06,開發檔 §5 環境指紋說明)。

每個環境指紋第一次被記進池時,把它的逐項原料(shadow_tracer.env_parts:項目名、路徑已換成 <HOME>/<REPO>、
環境變數只有名字 → 值的雜湊)另存一份 <池目錄>/envparts/<指紋>.json。之後判「環境變了」時拿它跟現在的逐項比,
回變了的項目名(不回值)。紀錄列格式不變。
★ 刻意不放進 shadow_tracer.py:那支整份進環境指紋(TRACER_CODE_FILES),改它一個字全部測試都要重跑;
  這裡只是存與比,不影響追蹤本身。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import shadow_tracer as st

ENV_PARTS_DIR = "envparts"


def env_parts_path(env_fp, root=None):
    if not re.fullmatch(r"[0-9a-f]{16,128}", str(env_fp or "")):
        return None
    return Path(root or st.shadow_dir_default()) / ENV_PARTS_DIR / (env_fp + ".json")


def save_env_parts(env_fp, parts, root=None):
    """同一個指紋只寫一次(已有就不動);寫壞不影響驗證。"""
    p = env_parts_path(env_fp, root)
    if p is None or p.exists():
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp.%d" % os.getpid())
        tmp.write_text(json.dumps(parts, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def load_env_parts(env_fp, root=None):
    p = env_parts_path(env_fp, root)
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p is not None else None
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def env_change_names(old, new):
    """兩份逐項原料 → 變了的項目名(env:變數名 / python / executable / node …),不含值。"""
    return [d["part"] for d in st.diff_env_parts(old, new)]
