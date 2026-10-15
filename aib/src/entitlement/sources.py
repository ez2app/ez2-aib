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
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .providers import list_providers
from .store import resolve_home
from .tier import get_entitlement

SOURCES_REL = Path("persona") / "sources.json"
SOURCES_VERSION = 1

# 狀態列標示。字樣走四語字典 aib.persona_sources.*;這裡是字典讀不到時的保底(與 zh-TW.json 同字)。
# 10-15 the maintainer 裁「所有 AI 都免費開放」:沒有「不可用(免費)」「付費版可開」這兩句了。
LABEL_ON = "開"
LABEL_OFF = "關"
FALLBACK_ALL_FREE = "所有 AI 都免費開放,哪一家都能開"
MSG_UNKNOWN_PROVIDER = "未知的 provider"
MSG_BAD_ACTION = "用法: 來源 <provider> 開|關（英: source <p> on|off）"


def _t(key, fallback, params=None, env=None):
    """查四語字典;查不到(或回 !!key!!)用保底。字典壞掉不得讓開關壞掉。"""
    try:
        cockpit = str(Path(__file__).resolve().parents[1] / "cockpit")
        if cockpit not in sys.path:
            sys.path.insert(0, cockpit)
        import i18n_lib
        locale = i18n_lib.resolve_locale_source(env=dict(_env_map(env)))[0]
        text = i18n_lib.t(key, params, locale=locale)
    except Exception:  # noqa: BLE001
        text = None
    if not isinstance(text, str) or not text or text == key or (text.startswith("!!") and text.endswith("!!")):
        text = fallback
        for k, v in (params or {}).items():
            text = text.replace("{%s}" % k, str(v))
    return text


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
    """資格允許可開的 provider 清單（穩定順序）。

    10-15 the maintainer 裁「所有 AI 都免費開放」⇒ 不分 free / single / all 一律全部;
    參數照留,呼叫端不用改。
    """
    return list(list_providers(str(repo_root) if repo_root else None))


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

    回傳 (ok, message, exit_code)。所有 AI 都免費(10-15),認得的 provider 一律准開。
    """
    name = (provider or "").strip()
    if not name:
        return False, _t("aib.persona_sources.usage", MSG_BAD_ACTION, env=env), 2

    all_p = list(list_providers(str(repo_root) if repo_root else None))
    if name not in all_p:
        return False, _t("aib.persona_sources.unknown", MSG_UNKNOWN_PROVIDER + ": {name}",
                         {"name": name}, env=env), 2

    obj = read_sources_obj(env=env)
    enabled_map = dict(obj.get("enabled") or {})
    enabled_map[name] = bool(enabled)
    obj = {"version": SOURCES_VERSION, "enabled": enabled_map}
    write_sources_obj(obj, env=env)
    return True, f"{name}: {_state_label(enabled, env)}", 0


def _state_label(on, env=None):
    if on:
        return _t("aib.persona_sources.on", LABEL_ON, env=env)
    return _t("aib.persona_sources.off", LABEL_OFF, env=env)


def format_status(
    env: Optional[Mapping[str, str]] = None,
    *,
    repo_root: Optional[Path] = None,
) -> str:
    """人讀狀態：每 provider 一列(開 / 關),最後一句「所有 AI 都免費」。"""
    all_p = list(list_providers(str(repo_root) if repo_root else None))
    obj = read_sources_obj(env=env)
    enabled_map = obj.get("enabled") or {}

    lines: List[str] = []
    for p in all_p:
        lines.append(f"  {p}: {_state_label(bool(enabled_map.get(p, False)), env)}")
    lines.append(_t("aib.persona_sources.all_free", FALLBACK_ALL_FREE, env=env))
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
