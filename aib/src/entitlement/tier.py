#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""付費資格查詢軸：tier ∈ {free, single, all}。

for_task: §108.320
主軸：產品有可用的付費資格底（free／單家／全開），付費功能有處可查。
與 EZ2_LICENSE／license_plan（basic|advanced）並列，不擴充不合併 enum。
查詢永不回傳憑證原文；查不到／壞檔／無法驗證 → free（回 reason）。
10-15 起所有 AI 都免費(the maintainer 裁);tier 不再決定能用哪幾家 AI,只給加購外掛等付費功能查。

三缺口草稿預設（標 TODO，未裁）：
1. 離線寬限：先 72h、時鐘系統 UTC，候 the maintainer
2. 簽章／發撤：有簽章欄就驗、無就 unavailable→free；真金鑰未接
3. 路徑：~/.ez2-aib/license.json（EZ2_AIB_HOME 可覆寫根）
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, List, Mapping, Optional

from .store import license_exists, read_license_obj, resolve_license_path

TIERS = frozenset(("free", "single", "all"))

# TODO(the maintainer §108.320): 離線寬限時數未裁；草稿預設 72h，時鐘用系統 UTC。
DRAFT_OFFLINE_GRACE = timedelta(hours=72)

_REASON_OK = "ok"
_REASON_MISSING = "missing"
_REASON_INVALID = "invalid"
_REASON_EXPIRED = "expired"
_REASON_UNAVAILABLE = "unavailable"
_REASON_GRACE = "grace"


def _utc_now() -> datetime:
    """系統時鐘，UTC。不做網路校時（草稿）。"""
    return datetime.now(timezone.utc)


def _parse_expires_at(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    # 允許尾端 Z
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def verify_signature(obj: Mapping[str, Any]) -> str:
    """驗簽結果：ok | invalid | unavailable。

    10-08(the maintainer 同意):換成真的 Ed25519 公鑰驗證(signing.verify_object;信任的公鑰在 keys.py)。
    "ed25519:" 開頭的真簽章:對得上信任公鑰才 ok,對不上一律 invalid。
    舊草稿的任意字串:過渡期(keys.DRAFT_ACCEPT_ANY)照舊 ok,過渡結束就 invalid。沒有簽章欄 ⇒ unavailable→free。
    """
    from . import signing
    return signing.verify_object(obj)


def _free_result(reason: str, source_path: str) -> dict:
    return {
        "tier": "free",
        "providers": [],
        "reason": reason,
        "source_path": source_path,
    }


def _normalize_providers(raw: Any) -> Optional[List[str]]:
    if not isinstance(raw, list):
        return None
    out: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            return None
        name = item.strip()
        if not name:
            return None
        out.append(name)
    return out


def get_entitlement(env: Optional[Mapping[str, str]] = None) -> dict:
    """讀資格檔 → {tier, providers, reason, source_path}。

    fail-closed：缺檔／壞 JSON／未知 tier／single 缺 providers／
    無簽章／驗簽失敗／過期且逾寬限 → tier=free。
    永不把 signature 或憑證原文放進回傳。
    """
    path = resolve_license_path(env)
    source_path = str(path)

    exists = license_exists(env)
    obj = read_license_obj(path=path, env=env)

    if obj is None:
        reason = _REASON_INVALID if exists else _REASON_MISSING
        return _free_result(reason, source_path)

    sig_status = verify_signature(obj)
    if sig_status == _REASON_UNAVAILABLE:
        return _free_result(_REASON_UNAVAILABLE, source_path)
    if sig_status == _REASON_INVALID:
        return _free_result(_REASON_INVALID, source_path)

    # 10-08:綁機器 / 線上啟用(欄位都在簽過的授權裡)。沒綁的授權照舊哪台都能用
    from . import binding
    bound, why = binding.check(obj, env)
    if not bound:
        return _free_result(why, source_path)

    tier_raw = obj.get("tier")
    if not isinstance(tier_raw, str):
        return _free_result(_REASON_INVALID, source_path)
    tier = tier_raw.strip().lower()
    if tier not in TIERS:
        return _free_result(_REASON_INVALID, source_path)

    providers: List[str] = []
    if tier == "single":
        normalized = _normalize_providers(obj.get("providers"))
        if normalized is None or len(normalized) == 0:
            # single 必須有 providers 列表；缺則當 free
            return _free_result(_REASON_INVALID, source_path)
        providers = normalized
    elif tier == "all":
        # all：providers 語意由呼叫端當「全開」；本層可回空或檔內列表（若有且合法）
        if "providers" in obj:
            normalized = _normalize_providers(obj.get("providers"))
            if normalized is None:
                return _free_result(_REASON_INVALID, source_path)
            providers = normalized
    # free：providers 空

    # 過期 + 離線寬限（草稿 72h / UTC）
    expires_at = _parse_expires_at(obj.get("expires_at"))
    reason = _REASON_OK
    if expires_at is not None:
        now = _utc_now()
        if now > expires_at:
            grace_end = expires_at + DRAFT_OFFLINE_GRACE
            if now <= grace_end:
                # TODO(the maintainer): 寬限起算點（到期後 vs 最後成功在線）未裁
                reason = _REASON_GRACE
            else:
                return _free_result(_REASON_EXPIRED, source_path)

    return {
        "tier": tier if tier != "free" else "free",
        "providers": providers if tier != "free" else [],
        "reason": reason if tier != "free" else _REASON_OK,
        "source_path": source_path,
    }
