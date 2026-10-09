#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""資格憑證檔的路徑解析與安全讀取。

for_task: §108.320
與 src/bootstrap/license_plan.py（EZ2_LICENSE / basic|advanced）並列，不合併。
本層只讀 ~/.ez2-aib/license.json（可用 EZ2_AIB_HOME 覆寫根目錄以便測）。
永不把憑證原文寫進回傳、log、例外訊息。
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional

LICENSE_FILENAME = "license.json"
DEFAULT_HOME_REL = ".ez2-aib"


def _env_map(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    if env is None:
        return os.environ
    return env


def resolve_home(env: Optional[Mapping[str, str]] = None) -> Path:
    """狀態根：EZ2_AIB_HOME > ~/.ez2-aib。

    與 identity / bind_one 同一慣例；僅覆寫根目錄以便測試，不另發明第二路徑語意。
    """
    e = _env_map(env)
    raw = e.get("EZ2_AIB_HOME")
    if isinstance(raw, str) and raw.strip():
        return Path(os.path.expanduser(raw.strip())).resolve()
    return (Path.home() / DEFAULT_HOME_REL).resolve()


def resolve_license_path(env: Optional[Mapping[str, str]] = None) -> Path:
    """執行期路徑：<home>/license.json。"""
    return resolve_home(env) / LICENSE_FILENAME


def _is_suspicious_symlink(path: Path) -> bool:
    """盡量穩健：若最終目標逃出 home 根，視為可疑（讀取端當失敗）。"""
    try:
        if not path.is_symlink():
            return False
    except OSError:
        return True
    try:
        home = path.parent.resolve()
        target = path.resolve()
        # license.json 必須落在 home 根下（同層檔名）
        return target.parent != home or target.name != LICENSE_FILENAME
    except OSError:
        return True


def read_license_obj(
    path: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[dict]:
    """安全讀 license.json → dict；缺檔／權限／壞 JSON／可疑 symlink → None。

    失敗時不附檔案原文。呼叫端用 get_entitlement 的 reason 區分 missing/invalid。
    """
    p = path if path is not None else resolve_license_path(env)
    try:
        if not p.exists():
            return None
    except OSError:
        return None

    try:
        if _is_suspicious_symlink(p):
            return None
    except OSError:
        return None

    try:
        st = p.lstat()
    except OSError:
        return None

    # 拒絕非常規檔（目錄、裝置）；symlink 已在上面檢查目標
    if stat.S_ISLNK(st.st_mode):
        try:
            st = p.stat()
        except OSError:
            return None
    if not stat.S_ISREG(st.st_mode):
        return None

    # 過大當壞檔（防意外塞入）；草稿上限 256 KiB
    if st.st_size > 256 * 1024:
        return None

    try:
        raw = p.read_bytes()
    except OSError:
        return None

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None

    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return None

    if not isinstance(obj, dict):
        return None
    return obj


def license_exists(env: Optional[Mapping[str, str]] = None) -> bool:
    p = resolve_license_path(env)
    try:
        return p.exists()
    except OSError:
        return False
