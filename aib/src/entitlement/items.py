#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""單買 / 單項訂閱(@abb89 10-25 23:2x 定欄位;Andy 23:2x「同時支援單買、或單訂閱使用期限」)。

同一張簽章授權(license.json)多一個 `items` 陣列,每筆:
  {sku, kind: buy|rent, expires_at(buy 為 null、rent 必填 UTC), machine_id, tenant, license_id}
簽章範圍含 items 整段(signing.canonical 蓋整個物件,改一個字整張驗不過)。

判斷只有一支:
  allowed(sku, free_at) = 方案等級 ≥ free_at(該 sku 標的「免費套用」等級,由外掛 offers.json 帶,核心只認 sku 字串)
                          OR items 裡有該 sku 且(buy 或 rent 未過期)且綁這台機器。
任一不成立 ⇒ 不給;rent 過期 / 方案到期 ⇒ 外掛照退回規則(保留可看可結案可匯出、不能新增 / 套用),核心只回 reason。
fail-closed:沒簽章 / 驗不過 / 寫錯 kind / rent 缺 expires_at / machine_id 不是這台 / license_id 對不上 ⇒ 整筆 item 當不存在。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from . import device, signing
from .plan import RANK, current
from .store import read_license_obj, resolve_license_path
from .tier import _parse_expires_at

KINDS = ("buy", "rent")
SKU_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _valid_item(raw: Any, license_id: str, did: Optional[str], now: datetime) -> Optional[dict]:
    """一筆 item 合不合;不合回 None(當不存在),不猜、不修。"""
    if not isinstance(raw, Mapping):
        return None
    sku = raw.get("sku")
    kind = raw.get("kind")
    if not isinstance(sku, str) or not SKU_RE.fullmatch(sku) or kind not in KINDS:
        return None
    if raw.get("license_id") != license_id:
        return None
    if did is None or raw.get("machine_id") != did:
        return None
    tenant = raw.get("tenant")
    if not isinstance(tenant, str) or not tenant.strip():
        return None
    exp_raw = raw.get("expires_at")
    if kind == "buy":
        if exp_raw is not None:
            return None
        return {"sku": sku, "kind": kind, "expires_at": None, "active": True}
    exp = _parse_expires_at(exp_raw)
    if exp is None:
        return None
    return {"sku": sku, "kind": kind, "expires_at": exp.isoformat(), "active": now <= exp}


def entitled_items(env: Optional[Mapping[str, str]] = None, now: Optional[datetime] = None,
                   raw_machine: Optional[str] = None) -> dict:
    """license.json 裡驗得過、綁這台的 items ⇒ {reason, items: [{sku, kind, expires_at, active}]}。
    授權本身驗不過 ⇒ items 空、reason 說為什麼(整段不信)。"""
    now = now or datetime.now(timezone.utc)
    obj = read_license_obj(path=resolve_license_path(env), env=env)
    if obj is None:
        return {"reason": "missing", "items": []}
    if signing.verify_object(obj) != signing.OK:
        return {"reason": "invalid", "items": []}
    raw_items = obj.get("items")
    if raw_items is None:
        return {"reason": "none", "items": []}
    if not isinstance(raw_items, list):
        return {"reason": "invalid", "items": []}
    lid = obj.get("license_id")
    if not isinstance(lid, str) or not lid:
        return {"reason": "invalid", "items": []}
    did = device.device_id(lid, raw_machine)
    out = [it for it in (_valid_item(r, lid, did, now) for r in raw_items) if it]
    return {"reason": "ok", "items": out}


def allowed(sku: str, free_at: Optional[str] = None, env: Optional[Mapping[str, str]] = None,
            now: Optional[datetime] = None, raw_machine: Optional[str] = None) -> dict:
    """這個 sku 現在能不能用 ⇒ {allowed, source: plan|buy|rent|none, plan, reason, expires_at, trial, left_days, was}。
    free_at = 外掛 offers.json 標的「這級以上免費套用」;沒給 / 寫錯 ⇒ 不走方案這條(只看單買 / 單訂閱)。
    trial = 走方案那條而且方案是試用;left_days = 到期前還有幾天(無期限 None);was = 以前有、現在沒了:plan-expired / rent-expired。
    試用只有方案級(plan-trial.json),沒有單件試用。"""
    now = now or datetime.now(timezone.utc)

    def out(allowed_, source, p_, reason, expires_at, trial=False, was=None):
        exp = _parse_expires_at(expires_at) if expires_at else None
        left = max(0, (exp - now).days) if exp else None
        return {"allowed": allowed_, "source": source, "plan": p_, "reason": reason, "expires_at": expires_at,
                "trial": bool(trial), "left_days": left, "was": was}

    if not isinstance(sku, str) or not SKU_RE.fullmatch(sku):
        return out(False, "none", "free", "bad-sku", None)
    p = current(env, now, raw_machine)
    need = RANK.get(free_at.strip().lower()) if isinstance(free_at, str) else None
    if need is not None and RANK[p["plan"]] >= need:
        return out(True, "plan", p["plan"], p["reason"], p["expires_at"], trial=p.get("trial"))
    ent = entitled_items(env, now, raw_machine)
    mine = [it for it in ent["items"] if it["sku"] == sku]
    for it in mine:
        if it["active"]:
            return out(True, it["kind"], p["plan"], "ok", it["expires_at"])
    if mine:   # 有這件但過期(只會是 rent)
        return out(False, "none", p["plan"], "rent-expired", mine[0]["expires_at"], was="rent-expired")
    if need is not None:
        was = "plan-expired" if p["reason"] == "expired" else None
        return out(False, "none", p["plan"], "plan-too-low", None, was=was)
    reason = "not-owned" if ent["reason"] in ("ok", "none") else ent["reason"]
    return out(False, "none", p["plan"], reason, None)
