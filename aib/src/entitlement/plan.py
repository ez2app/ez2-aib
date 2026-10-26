#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""方案級別查詢軸：plan ∈ {free, go, plus, pro, ultra}(10-25 the maintainer 定四級、23:1x 加 Go 成五級;@abb89 裁另開一軸)。

跟 tier.py(free / single / all,給加購外掛查)並列，不擴充、不合併 enum;外掛只問這裡(或 aib license plan --json),不自己讀檔。
級別只認簽章授權(同帳號 AI 威脅模型):本機檔只是存放處，不是依據。

兩個來源，取級別高的:
  license.json       正式授權(信任公鑰簽);多帶 "plan" 欄。過期照 tier.py 的離線寬限草稿(72h)。
  plan-trial.json    試用授權(伺服器發;用過紀錄只在伺服器)。一定要 "trial": true、有 issued_at / expires_at;
                     不給寬限，到期就是 free。可由信任公鑰或試用鑰(keys.TRUSTED_TRIAL_KEYS)簽;
                     試用鑰只能簽試用、天數不超過 TRIAL_MAX_DAYS,就算外洩也簽不出正式級別。
付費級一定要綁機器(binding.check 回 bound / activated);沒綁 ⇒ free(reason=unbound)。
fail-closed:缺檔 / 壞檔 / 沒簽章 / 驗不過 / 寫錯級別名 / 過期 ⇒ free(回 reason);永不回傳簽章或憑證原文。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from . import binding, keys, signing
from .store import read_license_obj, resolve_home, resolve_license_path
from .tier import DRAFT_OFFLINE_GRACE, _parse_expires_at

PLANS = ("free", "go", "plus", "pro", "ultra")   # 10-25 23:1x Andy 加 Go(Free 與 Plus 之間);空間大小也照這五級
RANK = {p: i for i, p in enumerate(PLANS)}
TRIAL_FILENAME = "plan-trial.json"
TRIAL_MAX_DAYS = 7   # 10-25 the maintainer:試用 7 天(試用鑰簽得出的上限也是這個)


def trial_path(env: Optional[Mapping[str, str]] = None):
    return resolve_home(env) / TRIAL_FILENAME


def _free(reason: str, source: str) -> dict:
    return {"plan": "free", "reason": reason, "expires_at": None, "trial": False, "machine_bound": False, "source": source}


def _signer(obj: Mapping[str, Any], allow_trial_key: bool) -> str:
    """main | trial | invalid | unavailable"""
    st = signing.verify_object(obj)
    if st == signing.OK:
        return "main"
    if allow_trial_key and keys.TRUSTED_TRIAL_KEYS and signing.verify_object(obj, key_list=keys.TRUSTED_TRIAL_KEYS, draft=False) == signing.OK:
        return "trial"
    return "unavailable" if st == "unavailable" else "invalid"


def evaluate(obj: Optional[Mapping[str, Any]], source: str, is_trial_file: bool, env: Optional[Mapping[str, str]] = None,
             now: Optional[datetime] = None, raw_machine: Optional[str] = None, exists: bool = False) -> dict:
    if obj is None:
        return _free("invalid" if exists else "missing", source)
    who = _signer(obj, allow_trial_key=is_trial_file)
    if who in ("invalid", "unavailable"):
        return _free(who, source)
    raw = obj.get("plan")
    if raw is None and not is_trial_file:
        return _free("ok", source)   # 只帶外掛加購的舊授權:沒寫級別 = free
    plan = raw.strip().lower() if isinstance(raw, str) else None
    if plan not in RANK:
        return _free("invalid", source)
    if plan == "free":
        return _free("ok", source)
    trial = obj.get("trial") is True
    issued, expires = _parse_expires_at(obj.get("issued_at")), _parse_expires_at(obj.get("expires_at"))
    if is_trial_file or who == "trial":
        if not trial or issued is None or expires is None:
            return _free("invalid", source)
        if who == "trial" and expires - issued > timedelta(days=TRIAL_MAX_DAYS):
            return _free("invalid", source)   # 試用鑰簽超過上限 ⇒ 不認
    bound, why = binding.check(obj, env, raw_machine=raw_machine, now=now)
    if not bound:
        return _free(why, source)
    if why == "unbound":
        return _free("unbound", source)   # 付費級一定要綁機器
    t = now or datetime.now(timezone.utc)
    reason = "ok"
    if expires is not None and t > expires:
        if is_trial_file or trial or t > expires + DRAFT_OFFLINE_GRACE:
            return _free("expired", source)
        reason = "grace"
    return {"plan": plan, "reason": reason, "expires_at": expires.isoformat() if expires else None, "trial": trial,
            "machine_bound": True, "source": source}


def current(env: Optional[Mapping[str, str]] = None, now: Optional[datetime] = None, raw_machine: Optional[str] = None) -> dict:
    """{plan, reason, expires_at, trial, machine_bound, source};正式 / 試用取級別高的(同級取正式)。"""
    lp = resolve_license_path(env)
    main = evaluate(read_license_obj(path=lp, env=env), "license", False, env, now, raw_machine, exists=lp.exists())
    tp = trial_path(env)
    tri = evaluate(read_license_obj(path=tp, env=env), "trial", True, env, now, raw_machine, exists=tp.exists()) if tp.exists() else None
    if tri and RANK[tri["plan"]] > RANK[main["plan"]]:
        return tri
    if main["plan"] == "free" and main["reason"] == "missing" and tri:
        return tri   # 沒正式授權時，試用那份的原因比較有用(例:expired)
    return main


def at_least(name: str, env: Optional[Mapping[str, str]] = None, now: Optional[datetime] = None, raw_machine: Optional[str] = None) -> bool:
    """現在的級別 ≥ name?name 寫錯 ⇒ False(要求當最嚴)"""
    need = RANK.get(name.strip().lower()) if isinstance(name, str) else None
    if need is None:
        return False
    return RANK[current(env, now, raw_machine)["plan"]] >= need
