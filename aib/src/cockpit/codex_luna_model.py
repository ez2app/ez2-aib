# SPDX-License-Identifier: Apache-2.0
"""開席沒指定型號時，從 Codex 本機模型清單挑版本號最大的 gpt-*-luna。"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

_SLUG = re.compile(r"^gpt-(\d+(?:\.\d+)*)-luna$")


def _cache_path(cache_path: str | None) -> str:
    if cache_path is not None:
        return cache_path
    env = os.environ.get("EZ2_CODEX_MODELS_CACHE")
    if env is not None:
        return env
    return os.path.expanduser("~/.codex/models_cache.json")


def listed_luna_slugs(cache_path: str | None = None) -> list[str]:
    """有列出來的 gpt-*-luna，版本號由新到舊。藏起來的不算。"""
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
        if not matched:
            continue
        version = tuple(int(part) for part in matched.group(1).split("."))
        found.append((version, slug))
    found.sort(reverse=True)
    return [slug for _version, slug in found]


def latest_luna_slug(cache_path: str | None = None) -> str:
    """回傳有列出來的最新 Luna。清單讀不到、或沒有這種型號時回空字串。"""
    slugs = listed_luna_slugs(cache_path)
    return slugs[0] if slugs else ""


def previous_luna_slug(current: str, cache_path: str | None = None) -> str:
    """目前這一版的上一版。沒有更舊的、或目前這版不在清單裡，回空字串。"""
    slugs = listed_luna_slugs(cache_path)
    try:
        index = slugs.index(str(current or ""))
    except ValueError:
        return ""
    if index + 1 >= len(slugs):
        return ""
    return slugs[index + 1]


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
