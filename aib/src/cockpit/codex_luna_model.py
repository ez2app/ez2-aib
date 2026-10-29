# SPDX-License-Identifier: Apache-2.0
"""開席沒指定型號時，從 Codex 本機模型清單挑版本號最大的 gpt-*-terra;清單沒有 Terra 才挑 gpt-*-luna。

10-28 Andy:Codex 席預設改 GPT-6.1 Terra。檔名沿用(呼叫端多)。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

#: 預設家族優先序:前一家清單裡一個都沒有才換下一家。
FAMILIES = ("terra", "luna")
_SLUG = re.compile(r"^gpt-(\d+(?:\.\d+)*)-(terra|luna)$")


def _cache_path(cache_path: str | None) -> str:
    if cache_path is not None:
        return cache_path
    env = os.environ.get("EZ2_CODEX_MODELS_CACHE")
    if env is not None:
        return env
    return os.path.expanduser("~/.codex/models_cache.json")


def listed_slugs(family: str, cache_path: str | None = None) -> list[str]:
    """有列出來的 gpt-*-<family>，版本號由新到舊。藏起來的不算。"""
    path = _cache_path(cache_path)
    if not path:
        return []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    found: list[tuple[tuple[int, ...], str]] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        if item.get("visibility") not in (None, "list"):
            continue
        slug = str(item.get("slug") or "")
        matched = _SLUG.match(slug)
        if not matched or matched.group(2) != family:
            continue
        version = tuple(int(part) for part in matched.group(1).split("."))
        found.append((version, slug))
    found.sort(reverse=True)
    return [slug for _version, slug in found]


def latest_default_slug(cache_path: str | None = None) -> str:
    """依 FAMILIES 回傳第一個有列出型號的家族的最新版。清單讀不到、或都沒有時回空字串。"""
    for family in FAMILIES:
        slugs = listed_slugs(family, cache_path)
        if slugs:
            return slugs[0]
    return ""


def previous_default_slug(current: str, cache_path: str | None = None) -> str:
    """同家族裡目前這一版的上一版;已是最舊的 Terra 就退最新的 Luna。目前這版不在清單裡回空字串。"""
    current = str(current or "")
    matched = _SLUG.match(current)
    if not matched or matched.group(2) not in FAMILIES:
        return ""
    family = matched.group(2)
    slugs = listed_slugs(family, cache_path)
    try:
        index = slugs.index(current)
    except ValueError:
        return ""
    if index + 1 < len(slugs):
        return slugs[index + 1]
    for later in FAMILIES[FAMILIES.index(family) + 1:]:
        older = listed_slugs(later, cache_path)
        if older:
            return older[0]
    return ""


def model_rejected(text: str) -> bool:
    """模型不被這次登入接受。額度用完、讀帳號被拒，都不是換模型能解的。

    ★ 「是不是額度用完」**委派** `usage_failover.quota_limit_text`,不在這裡養第四份
      子字串判準(整合席 a seat 2026-09-26 18:1x 抓到:這裡原本是
      `if "usage limit" in raw`,於是 codex 一邊印開場那句
      `You have N usage limit resets available.` 一邊拒絕模型時,`model_rejected` 回 False
      ⇒ **自動退到舊版模型靜靜不會發生**)。
      可達條件:`AIB_CODEX_LUNA_AUTO=1` —— 而那個變數是 `aib:1456` 在「codex 席 + 套組開著 +
      沒有顯式 --model」時自己帶上的,所以套組開著就會走到,不是潛伏。
      延後匯入:`usage_failover` 會拉進 `usage_accounts`,而本檔被 provider-launch 用
      一行 `python3 -c` 呼叫,沒必要每次都付那個匯入成本。
    """
    raw = str(text or "").lower()
    import usage_failover as _uf          # 判準單源,見上
    if _uf.quota_limit_text(raw) or "upgrade to plus" in raw:
        return False
    if "account/read" in raw or "unauthorized" in raw or "401" in raw:
        return False
    if "not supported" in raw and "model" in raw:
        return True
    if "unknown model" in raw or "invalid model" in raw:
        return True
    return False
