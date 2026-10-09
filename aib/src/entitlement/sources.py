#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""人格粹煉來源開關（DESIGN §4.4 / 包 D）。

開關存 ~/.ez2-aib/persona/sources.json（EZ2_AIB_HOME 覆寫根）。
有效開啟 = 使用者開 ∧ 資格允許。
給 C：get_enabled_sources / is_source_enabled。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .providers import FREE_ALLOWED_PROVIDERS, list_providers
from .store import resolve_home
from .tier import get_entitlement

SOURCES_REL = Path("persona") / "sources.json"
SOURCES_VERSION = 1

# 狀態列標示
LABEL_ON = "開"
LABEL_OFF = "關"
LABEL_UNAVAILABLE_FREE = "不可用(免費)"
MSG_PAID_REQUIRED = "付費版可開"
MSG_PAID_REQUIRED_DESIGN = "這一家要付費版才能開"
MSG_UNSIGNED_FREE = "無簽章 = 免費"
MSG_UNKNOWN_PROVIDER = "未知的 provider"
MSG_BAD_ACTION = "用法: 來源 <provider> 開|關（英: source <p> on|off）"


def resolve_sources_path(env: Optional[Mapping[str, str]] = None) -> Path:
    return resolve_home(env) / SOURCES_REL


def _env_map(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    if env is None:
        return os.environ
    return env


def read_sources_obj(
    path: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> dict:
    """讀 sources.json；缺檔／壞 JSON → {"version":1,"enabled":{}}。"""
    p = path if path is not None else resolve_sources_path(env)
    try:
        if not p.exists():
            return {"version": SOURCES_VERSION, "enabled": {}}
    except OSError:
        return {"version": SOURCES_VERSION, "enabled": {}}
    try:
        raw = p.read_text(encoding="utf-8")
        obj = json.loads(raw)
    except (OSError, ValueError, TypeError, UnicodeDecodeError):
        return {"version": SOURCES_VERSION, "enabled": {}}
    if not isinstance(obj, dict):
        return {"version": SOURCES_VERSION, "enabled": {}}
    enabled = obj.get("enabled")
    if not isinstance(enabled, dict):
        enabled = {}
    clean: Dict[str, bool] = {}
    for k, v in enabled.items():
        if isinstance(k, str) and k.strip() and isinstance(v, bool):
            clean[k.strip()] = v
    return {"version": SOURCES_VERSION, "enabled": clean}


def write_sources_obj(
    obj: Mapping[str, Any],
    env: Optional[Mapping[str, str]] = None,
    path: Optional[Path] = None,
) -> Path:
    """原子寫入 sources.json。"""
    p = path if path is not None else resolve_sources_path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SOURCES_VERSION,
        "enabled": dict(obj.get("enabled") or {}),
    }
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    # atomic replace
    fd, tmp_name = tempfile.mkstemp(
        dir=str(p.parent), prefix=".sources-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, p)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return p


def allowed_providers_for_entitlement(
    entitlement: Mapping[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> List[str]:
    """依 tier 回傳「資格允許可開」的 provider 清單（穩定順序）。"""
    all_p = list(list_providers(str(repo_root) if repo_root else None))
    tier = entitlement.get("tier")
    if tier == "all":
        return all_p
    if tier == "single":
        bought = entitlement.get("providers") or []
        allowed = set(FREE_ALLOWED_PROVIDERS)
        if isinstance(bought, list):
            for name in bought:
                if isinstance(name, str) and name.strip():
                    allowed.add(name.strip())
        return [p for p in all_p if p in allowed]
    # free（含缺檔／無簽／壞檔）
    return [p for p in all_p if p in FREE_ALLOWED_PROVIDERS]


def is_provider_allowed(
    provider: str,
    entitlement: Optional[Mapping[str, Any]] = None,
    env: Optional[Mapping[str, str]] = None,
    *,
    repo_root: Optional[Path] = None,
) -> bool:
    e = entitlement if entitlement is not None else get_entitlement(env)
    return provider in set(allowed_providers_for_entitlement(e, repo_root=repo_root))


def is_source_enabled(
    provider: str,
    env: Optional[Mapping[str, str]] = None,
    *,
    repo_root: Optional[Path] = None,
) -> bool:
    """使用者開 ∧ 資格允許 → True；否則 False（給 C：關閉／不可用 = 0 筆）。"""
    name = (provider or "").strip()
    if not name:
        return False
    e = get_entitlement(env)
    if not is_provider_allowed(name, e, env, repo_root=repo_root):
        return False
    obj = read_sources_obj(env=env)
    return bool(obj.get("enabled", {}).get(name, False))


def get_enabled_sources(
    env: Optional[Mapping[str, str]] = None,
    *,
    repo_root: Optional[Path] = None,
) -> List[str]:
    """合併 sources.json 與 entitlement 後的有效開啟集（穩定順序）。"""
    all_p = list(list_providers(str(repo_root) if repo_root else None))
    return [p for p in all_p if is_source_enabled(p, env, repo_root=repo_root)]


def set_source(
    provider: str,
    enabled: bool,
    env: Optional[Mapping[str, str]] = None,
    *,
    repo_root: Optional[Path] = None,
) -> Tuple[bool, str, int]:
    """開／關一個來源。

    回傳 (ok, message, exit_code)。
    免費態對不在 FREE_ALLOWED_PROVIDERS 裡的「開」⇒ 拒、印付費提示、rc≠0；不靜默、不偷開。
    """
    name = (provider or "").strip()
    if not name:
        return False, MSG_BAD_ACTION, 2

    all_p = list(list_providers(str(repo_root) if repo_root else None))
    if name not in all_p:
        return False, f"{MSG_UNKNOWN_PROVIDER}: {name}", 2

    e = get_entitlement(env)
    allowed = set(allowed_providers_for_entitlement(e, repo_root=repo_root))

    if enabled and name not in allowed:
        # 免費／未買：明講，不偷開
        msg = MSG_PAID_REQUIRED
        # 設計稿原文亦可接受；狀態頁統一用短句，拒開用短句
        return False, msg, 1

    obj = read_sources_obj(env=env)
    enabled_map = dict(obj.get("enabled") or {})
    enabled_map[name] = bool(enabled)
    obj = {"version": SOURCES_VERSION, "enabled": enabled_map}
    write_sources_obj(obj, env=env)
    state = LABEL_ON if enabled else LABEL_OFF
    return True, f"{name}: {state}", 0


def _unsigned_free_label(entitlement: Mapping[str, Any]) -> Optional[str]:
    """無檔／無簽章 ⇒ 狀態明標「無簽章 = 免費」。"""
    reason = entitlement.get("reason")
    tier = entitlement.get("tier")
    if tier != "free":
        return None
    if reason in ("missing", "unavailable", "invalid", "expired"):
        return MSG_UNSIGNED_FREE
    return None


def format_status(
    env: Optional[Mapping[str, str]] = None,
    *,
    repo_root: Optional[Path] = None,
) -> str:
    """人讀狀態：每 provider 一列；單家態加升級提示。"""
    e = get_entitlement(env)
    all_p = list(list_providers(str(repo_root) if repo_root else None))
    allowed = set(allowed_providers_for_entitlement(e, repo_root=repo_root))
    obj = read_sources_obj(env=env)
    enabled_map = obj.get("enabled") or {}

    lines: List[str] = []
    unsigned = _unsigned_free_label(e)
    tier = e.get("tier", "free")
    reason = e.get("reason", "")
    head = f"資格: {tier}"
    if unsigned:
        head += f"（{unsigned}）"
    elif reason and reason != "ok":
        head += f"（reason={reason}）"
    lines.append(head)

    on_count = 0
    for p in all_p:
        if p not in allowed:
            label = LABEL_UNAVAILABLE_FREE
            # 對非允許：若使用者曾寫開，仍顯示不可用（有效=關）
        elif enabled_map.get(p, False):
            label = LABEL_ON
            on_count += 1
        else:
            label = LABEL_OFF
        lines.append(f"  {p}: {label}")

    if tier == "single":
        # 已開 N 家,升級全開可再開 M 家
        m = len(all_p) - on_count
        if m < 0:
            m = 0
        lines.append(f"已開 {on_count} 家,升級全開可再開 {m} 家")
    elif tier == "free":
        # 提示：免費只 FREE_ALLOWED_PROVIDERS 那幾家
        # 免費可開的那幾家用解析名單的順序印(不是集合的隨機順序)
        free_here = [p for p in all_p if p in FREE_ALLOWED_PROVIDERS]
        locked = len(all_p) - len(free_here)
        lines.append(
            f"免費態：僅 {'／'.join(free_here)} 可開；另有 {locked} 家{MSG_PAID_REQUIRED}"
        )

    return "\n".join(lines) + "\n"


def parse_source_argv(argv: List[str]) -> Tuple[str, Optional[List[str]]]:
    """解析 CLI argv → (command, rest)。

    支援中英：
      來源|source <provider> 開|關|on|off
      狀態|status
    """
    if not argv:
        return "help", None
    cmd = argv[0]
    rest = argv[1:]
    if cmd in ("狀態", "status"):
        return "status", rest
    if cmd in ("來源", "source"):
        return "source", rest
    if cmd in ("-h", "--help", "help"):
        return "help", rest
    return "unknown", argv
