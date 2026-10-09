#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""從 EZ2_LICENSE 讀方案。缺檔、讀不到、簽章/JSON 壞掉 → basic。

正本是 secrets/ez2.env 的 EZ2_LICENSE,不另立 edition.json。
值不得進 log、例外、回傳以外的任何輸出。
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

_BOOT = Path(__file__).resolve().parent
if str(_BOOT) not in sys.path:
    sys.path.insert(0, str(_BOOT))
import aib_secrets as S  # noqa: E402

BASIC = "basic"
ADVANCED = "advanced"
# 付費檔次都至少是進階版:Plus/Max/Ultra 只差功能區符號與閃光(render_lib FEATURE_TIER_*),
# 不是比 advanced 少功能。只認 advanced 的話,客戶升 Max/Ultra 反而被當 basic ⇒ 待審區/任務區/全域框
# 整個關掉,連要跟著方案走的閃光都看不到(the maintainer 09-14 23:xx)。未知字串仍落 basic。
ADVANCED_PLANS = frozenset((ADVANCED, "plus", "max", "ultra"))


def _b64(raw):
    pad = "=" * ((4 - len(raw) % 4) % 4)
    text = raw.replace("-", "+").replace("_", "/") + pad
    return base64.b64decode(text.encode("ascii"), validate=False)


def _plan_of_obj(obj):
    if not isinstance(obj, dict):
        return None
    val = obj.get("plan")
    if val is None:
        val = obj.get("edition")
    if not isinstance(val, str):
        return None
    return val.strip().lower() or None


def _signature_ok(blob):
    """10-08(the maintainer 同意):以前完全不驗簽章(自己編一段就是進階版)。現在走 entitlement.signing:
    EdDSA 真簽章要對得上信任公鑰;舊的沒簽章授權只在過渡期(keys.DRAFT_ACCEPT_ANY)照舊認。驗證模組載不到 ⇒ 不認(往基本版倒)。"""
    try:
        src = str(_BOOT.parent)
        if src not in sys.path:
            sys.path.insert(0, src)
        from entitlement import signing
    except Exception:  # noqa: BLE001
        return False
    return signing.verify_jws(blob) == signing.OK


def _bound_here(obj):
    """10-08:授權裡寫了綁機器 / 線上啟用 ⇒ 這台要對得上(跟 license.json 同一套,entitlement.binding)。沒寫 ⇒ 哪台都行。"""
    if not isinstance(obj, dict) or (obj.get("devices") is None and not obj.get("activation_url")):
        return True
    try:
        from entitlement import binding
    except Exception:  # noqa: BLE001
        return False
    return binding.check(obj)[0]


def plan_from_blob(blob):
    """blob → plan 字串;解不出、或簽章不過回 None。永不回傳授權原文。"""
    if not isinstance(blob, str) or not blob.strip():
        return None
    if not _signature_ok(blob):
        return None
    blob = blob.strip()
    parts = blob.split(".")
    candidates = []
    if len(parts) == 3:
        candidates.append(parts[1])
    candidates.append(blob)
    for piece in candidates:
        try:
            data = _b64(piece)
        except (ValueError, TypeError):
            continue
        try:
            obj = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        plan = _plan_of_obj(obj)
        if plan and not _bound_here(obj):
            return None
        if plan:
            return plan
    return None


def raw_plan(env=None):
    """授權 JSON 的 plan 字串(小寫);解不出回 None。不改 basic/advanced 開關。"""
    try:
        rows = S.load_file("ez2.env", env)
    except S.SecretError:
        return None
    raw = (rows or {}).get("EZ2_LICENSE") or ""
    return plan_from_blob(raw)


def license_plan(env=None):
    """basic 或缺授權=基本版;plan 屬 ADVANCED_PLANS(advanced/plus/max/ultra)才是進階版。"""
    try:
        rows = S.load_file("ez2.env", env)
    except S.SecretError:
        return BASIC
    raw = (rows or {}).get("EZ2_LICENSE") or ""
    plan = plan_from_blob(raw)
    if plan in ADVANCED_PLANS:
        return ADVANCED
    return BASIC


def is_advanced(env=None):
    return license_plan(env) == ADVANCED
