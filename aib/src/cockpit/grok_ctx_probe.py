#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Grok CTX 探針:把 CLI 自己寫的 signals.json 接到艦隊 `.pct` 量尺。

右欄 `ctx_of` / 輪替 `lifecycle.read_machine_ctx` 的唯一機器來源是
`$EZ2_CTX_DIR/<uuid>.pct`。Claude / Codex / AGY 由各家 statusline hook 寫;
grok 開席時**沒有**對應的 statusline 生產者(2026-09-02 現查:
`~/.ez2/ctx` 625 筆裡 grok=0,活艙 grok 列畫 `--`)。

Grok 自己把當窗用量寫在 session 目錄的 `signals.json`:

    contextWindowUsage   0–100 整數(權威百分比)
    contextTokensUsed    當窗 token
    contextWindowTokens  窗大小

本模組只做一件事:找到那份檔,寫成 `ez2-context-gauge-v1`,讓既有讀端
不必為 grok 開第二條取值路徑。

★ 不把 pane-right 改成直接讀 signals.json。同一個量兩套讀法,兩邊漂移
  時兩邊都不會紅(fleet_patrol.py 檔頭已記過這族)。寫進 `.pct` 之後,
  右欄、seat_who、輪替 90% 門檻吃的是同一份。

★ `observed_at` 用 signals.json 的 mtime(UTC Z),不用「探針跑到的現在」。
  用現在去蓋,死掉的 grok 席每 4 秒會被刷成新鮮——stale 守衛等於沒有。
"""

import json
import os
import re
import time
from urllib.parse import quote

SCHEMA = "ez2-context-gauge-v1"
GAUGE_VERSION = "1.0.0"
SOURCE = "grok_signals"
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_ACTIVITY_FILES = (
    "updates.jsonl", "chat_history.jsonl", "events.jsonl", "signals.json")


def sessions_root():
    """grok session 根。治具用 EZ2_GROK_SESSIONS_DIR 注入,不掃真 ~/.grok。"""
    override = os.environ.get("EZ2_GROK_SESSIONS_DIR")
    if override:
        return override
    home = os.environ.get("GROK_HOME") or os.path.expanduser("~/.grok")
    return os.path.join(home, "sessions")


def _inside(child, parent):
    try:
        child_r = os.path.realpath(child)
        parent_r = os.path.realpath(parent)
    except OSError:
        return False
    rel = os.path.relpath(child_r, parent_r)
    return rel != os.pardir and not rel.startswith(os.pardir + os.sep)


def session_dir(uuid, cwd=None):
    """`~/.grok/sessions/<urlencoded-cwd>/<uuid>/`;找不到回 None。

    cwd 有值時先走確定路徑(席位檔帶 cwd 的常態);沒命中再掃一層子目錄。
    不 rglob:session 樹是「編碼後的 cwd / uuid」,兩層,深掃只是把別的
    uuid 目錄當搜尋空間。
    """
    if not isinstance(uuid, str) or not _UUID_RE.fullmatch(uuid):
        return None
    root = sessions_root()
    if not isinstance(root, str) or not os.path.isabs(root):
        return None
    candidates = []
    if isinstance(cwd, str) and cwd:
        candidates.append(os.path.join(root, quote(cwd, safe=""), uuid))
    try:
        for name in os.listdir(root):
            candidates.append(os.path.join(root, name, uuid))
    except OSError:
        pass
    seen = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if not _inside(path, root):
            continue
        if os.path.isdir(path):
            return path
    return None


def signals_path(uuid, cwd=None):
    folder = session_dir(uuid, cwd=cwd)
    if folder is None:
        return None
    path = os.path.join(folder, "signals.json")
    if os.path.isfile(path) and _inside(path, sessions_root()):
        return path
    return None


def activity_mtime(uuid, cwd=None):
    """該 grok session 目錄裡活動跡證的最新 mtime;缺檔回 None。"""
    folder = session_dir(uuid, cwd=cwd)
    if folder is None:
        return None
    newest = None
    for name in _ACTIVITY_FILES:
        try:
            mtime = os.path.getmtime(os.path.join(folder, name))
        except OSError:
            continue
        if newest is None or mtime > newest:
            newest = mtime
    return newest


def _int_0_100(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and value != int(value):
        return None
    n = int(value)
    if 0 <= n <= 100:
        return n
    return None


def _nonneg_int(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _mtime_utc_z(path):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(path)))


def _tier(pct):
    if pct is None:
        return "unknown"
    if pct >= 95:
        return "crit"
    if pct >= 75:
        return "hot"
    if pct >= 50:
        return "warn"
    return "ok"


def current_model(folder):
    """同一 session 目錄 `summary.json` 的 `current_model_id`(現用模型);讀不到回 None。

    ★ 10-01(the maintainer:grok 換模型後右欄不變)現機實證:`signals.json` 的 `primaryModelId`
      是**這個 session 第一個用的模型**(= `modelsUsed[0]`),換模型之後不會變。
      現查 194 個 grok session,8 個換過模型,8 個全是 primaryModelId=舊、
      summary.json current_model_id=新(且與 updates.jsonl 最後一列 `_meta.modelId` 相同)。
      活席 4b25c:primaryModelId=grok-4.6、current_model_id=grok-4.7。
    """
    if not isinstance(folder, str):
        return None
    path = os.path.join(folder, "summary.json")
    if not _inside(path, sessions_root()):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None
    model = data.get("current_model_id") if isinstance(data, dict) else None
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None


def gauge_from_signals(uuid, data, observed_at, current=None):
    """signals.json 物件 → ez2-context-gauge-v1;量不到回 None。

    `current` = summary.json 的現用模型;有就以它為準(換模型後 primaryModelId 不會跟著變)。
    """
    if not isinstance(data, dict):
        return None
    used = _nonneg_int(data.get("contextTokensUsed"))
    size = _nonneg_int(data.get("contextWindowTokens"))
    pct = _int_0_100(data.get("contextWindowUsage"))
    if pct is None and used is not None and size:
        pct = int(round(min(100.0, max(0.0, used * 100.0 / size))))
    if pct is None:
        return None
    model = current if isinstance(current, str) and current.strip() else data.get("primaryModelId")
    if not isinstance(model, str) or not model.strip():
        model = "grok"
    return {
        "schema": SCHEMA,
        "gauge_version": GAUGE_VERSION,
        "engine": "grok",
        "session_id": uuid,
        "model": model.strip(),
        "pct": pct,
        "used": used,
        "size": size,
        "tier": _tier(pct),
        "authority": "provider_observed",
        "source": SOURCE,
        "observed_at": observed_at,
    }


def _atomic_write(path, text):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp = "%s.tmp-%s-%s" % (path, os.getpid(), int(time.time() * 1000))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    os.rename(tmp, path)


def refresh(uuid, cwd=None, ctx_dir=None):
    """讀 grok signals.json,寫 `$EZ2_CTX_DIR/<uuid>.pct`。回 pct 或 None。

    失敗(找不到檔、壞 JSON、量不到)保持現況:不刪既有 `.pct`,不丟例外。
    右欄下一幀仍走 `ctx_of`;有舊檔就畫舊值,沒有就 `--`。
    """
    path = signals_path(uuid, cwd=cwd)
    if path is None:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        observed = _mtime_utc_z(path)
    except (OSError, ValueError, TypeError):
        return None
    record = gauge_from_signals(uuid, data, observed,
                                current=current_model(os.path.dirname(path)))
    if record is None:
        return None
    if ctx_dir is None:
        ctx_dir = os.environ.get("EZ2_CTX_DIR") or os.path.expanduser("~/.ez2/ctx")
    if not isinstance(ctx_dir, str) or not os.path.isabs(ctx_dir):
        return None
    dest = os.path.join(ctx_dir, uuid + ".pct")
    try:
        _atomic_write(dest, json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return record["pct"]
