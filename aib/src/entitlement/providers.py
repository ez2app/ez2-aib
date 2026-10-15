#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""provider-launch.sh 母體清單解析（R15 / 包 D）。

開關母體 = provider-launch.sh case "$PROVIDER" 認的值，不手列硬編碼當唯一來源。
解析失敗時回傳與案稿一致的備援常數（測裡對齊）。
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Sequence

# 與 src/cockpit/provider-launch.sh case "$PROVIDER" 行對齊（備援／測對照）
KNOWN_PROVIDERS: tuple[str, ...] = (
    "claude",
    "codex",
    "agy",
    "copilot",
    "grok",
    "deepseek",
    "nvidia",
    "openrouter",
    "typesafe",
    "openrouterjev",
    "sand",
    "grokbot",
    "codexbot",
    "claudebot",   # 09-27 合 origin 64d1d141(ClaudeBot #5)進 provider-launch.sh;備援名單同步(a seat)
    "musebot",     # 2026-10-05 MuseBot 橋接席(Meta Muse 桌面橋;Glint a seat 工單)
)

# ★ the maintainer 2026-10-15 22:5x 裁「所有 AI 都免費開放」(不只 Claude):沒有「免費只限某幾家」的集合了。
#   09-27 的 FREE_ALLOWED_PROVIDERS = {"claude"}(claudebot 等算付費)作廢、刻意刪掉不留別名 ——
#   留著就會有人再拿它擋。要再分免費 / 付費,先問 the maintainer,不要把集合加回來。

_CASE_RE = re.compile(
    r'case\s+"\$PROVIDER"\s+in\s+([a-z0-9_|]+)\)',
    re.MULTILINE,
)


def _repo_root() -> Path:
    # src/entitlement/providers.py → repo root
    return Path(__file__).resolve().parents[2]


def provider_launch_path(repo_root: Optional[Path] = None) -> Path:
    root = repo_root if repo_root is not None else _repo_root()
    return root / "src" / "cockpit" / "provider-launch.sh"


def parse_providers_from_launch(
    path: Optional[Path] = None,
    *,
    repo_root: Optional[Path] = None,
) -> Optional[List[str]]:
    """從 provider-launch.sh 解析 case "$PROVIDER" 清單；失敗回 None。"""
    p = path if path is not None else provider_launch_path(repo_root)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _CASE_RE.search(text)
    if not m:
        return None
    names: List[str] = []
    for part in m.group(1).split("|"):
        name = part.strip()
        if name and name != "*":
            names.append(name)
    return names or None


@lru_cache(maxsize=4)
def list_providers(repo_root_str: Optional[str] = None) -> tuple[str, ...]:
    """有效 provider 清單（tuple，穩定順序）。

    優先解析 provider-launch.sh；失敗則用 KNOWN_PROVIDERS。
    """
    root = Path(repo_root_str) if repo_root_str else _repo_root()
    parsed = parse_providers_from_launch(repo_root=root)
    if parsed:
        return tuple(parsed)
    return KNOWN_PROVIDERS


def clear_provider_cache() -> None:
    list_providers.cache_clear()
