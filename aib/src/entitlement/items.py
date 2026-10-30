#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""單買 / 單項訂閱(@abb89 10-25 23:2x 定欄位;Andy 23:2x「同時支援單買、或單訂閱使用期限」)。

同一張簽章授權(license.json)多一個 `items` 陣列,每筆:
  {sku, kind: buy|rent|trial, expires_at(buy 為 null、rent / trial 必填 UTC), machine_id, tenant, license_id}
簽章範圍含 items 整段(signing.canonical 蓋整個物件,改一個字整張驗不過)。

判斷只有一支:
  allowed(sku, free_at) = 方案等級 ≥ free_at(該 sku 標的「免費套用」等級,由外掛 offers.json 帶,核心只認 sku 字串)
                          OR items 裡有該 sku 且(buy、或 rent / trial 未過期)且綁這台機器。
任一不成立 ⇒ 不給;rent 過期 / 方案到期 ⇒ 外掛照退回規則(保留可看可結案可匯出、不能新增 / 套用),核心只回 reason。
fail-closed:沒簽章 / 驗不過 / 寫錯 kind / rent、trial 缺 expires_at / machine_id 不是這台 / license_id 對不上 ⇒ 整筆 item 當不存在。
方案級試用(plan-trial.json)另在 plan.py;這裡的 trial 是單件。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from . import device, signing
from .plan import RANK, current
from .store import read_license_obj, resolve_license_path
from .tier import _parse_expires_at

KINDS = ("buy", "rent", "trial")   # trial = 單件試用 n 日(Andy 10-25「每項可設試用 n 日」;伺服器發、同 rent 要 expires_at)
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


def _decide(sku: Any, free_at: Any, now: datetime, plan_now, ent_now) -> dict:
    """單件判斷本體:allowed() 與 allowed_many() 共用這一支(批次不另寫判斷 ⇒ 不會單件拒、批次給)。
    plan_now / ent_now = 取 current() / entitled_items() 的函式;用到才叫(方案夠就不讀 items,跟原本一樣)。"""

    def out(allowed_, source, p_, reason, expires_at, trial=False, was=None):
        exp = _parse_expires_at(expires_at) if expires_at else None
        left = max(0, (exp - now).days) if exp else None
        return {"allowed": allowed_, "source": source, "plan": p_, "reason": reason, "expires_at": expires_at,
                "trial": bool(trial), "left_days": left, "was": was}

    if not isinstance(sku, str) or not SKU_RE.fullmatch(sku):
        return out(False, "none", "free", "bad-sku", None)
    p = plan_now()
    need = RANK.get(free_at.strip().lower()) if isinstance(free_at, str) else None
    if need is not None and RANK[p["plan"]] >= need:
        return out(True, "plan", p["plan"], p["reason"], p["expires_at"], trial=p.get("trial"))
    ent = ent_now()
    mine = [it for it in ent["items"] if it["sku"] == sku]
    for it in mine:
        if it["active"]:
            return out(True, it["kind"], p["plan"], "ok", it["expires_at"])
    if mine:   # 有這件但過期(rent 或 trial)
        kind = mine[0]["kind"]
        return out(False, "none", p["plan"], "%s-expired" % kind, mine[0]["expires_at"], was="%s-expired" % kind)
    if need is not None:
        was = "plan-expired" if p["reason"] == "expired" else None
        return out(False, "none", p["plan"], "plan-too-low", None, was=was)
    reason = "not-owned" if ent["reason"] in ("ok", "none") else ent["reason"]
    return out(False, "none", p["plan"], reason, None)


def allowed(sku: str, free_at: Optional[str] = None, env: Optional[Mapping[str, str]] = None,
            now: Optional[datetime] = None, raw_machine: Optional[str] = None) -> dict:
    """這個 sku 現在能不能用 ⇒ {allowed, source: plan|buy|rent|none, plan, reason, expires_at, trial, left_days, was}。
    free_at = 外掛 offers.json 標的「這級以上免費套用」;沒給 / 寫錯 ⇒ 不走方案這條(只看單買 / 單訂閱)。
    trial = 走方案那條而且方案是試用;left_days = 到期前還有幾天(無期限 None);was = 以前有、現在沒了:plan-expired / rent-expired / trial-expired。
    source=trial 是單件試用(items kind trial);方案級試用走 source=plan + trial=True。"""
    now = now or datetime.now(timezone.utc)
    return _decide(sku, free_at, now, lambda: current(env, now, raw_machine),
                   lambda: entitled_items(env, now, raw_machine))


def allowed_many(pairs, env: Optional[Mapping[str, str]] = None, now: Optional[datetime] = None,
                 raw_machine: Optional[str] = None) -> list:
    """一批 (sku, free_at) ⇒ 每筆 allowed() 的結果,順序照輸入、重複照回(開艙說明 c 張:2F 一頁問 26 件)。
    同一個 now、current() / entitled_items() 各最多讀一次 ⇒ 整批出自同一份授權快照,不會一半新一半舊。
    free_at 不是字串也不是 None ⇒ 那筆當 bad-sku(單件 CLI 只會給字串),其他筆照算。"""
    now = now or datetime.now(timezone.utc)
    memo: dict = {}

    def once(key, fn):
        def get():
            if key not in memo:
                memo[key] = fn()
            return memo[key]
        return get

    plan_now = once("plan", lambda: current(env, now, raw_machine))
    ent_now = once("ent", lambda: entitled_items(env, now, raw_machine))
    res = []
    for sku, free_at in pairs:
        if free_at is not None and not isinstance(free_at, str):
            sku = None
        res.append(_decide(sku, free_at, now, plan_now, ent_now))
    return res
