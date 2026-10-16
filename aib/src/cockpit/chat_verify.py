#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""線上對話的真人驗證(開發文件第 32 節,Andy 10-16 23:4x):對方名冊宣告的 NIP-05 與社群帳號,照證明去驗。只用標準庫。

- NIP-05:https://<網域>/.well-known/nostr.json?name=<名> 的 names[<名>] == 對方主人公鑰 ⇒ True;抓得到但不對 ⇒ False;抓不到 ⇒ None。
- 社群:平台在 SOCIAL_AUTO(github / web / youtube / threads)、有證明網址 ⇒ 頁裡有他的主人 npub = True、沒有 = False;
  x / fb / ig / line 要登入才看得到、或抓不到 ⇒ None(只算自稱,畫面寫「自稱」不寫「沒過」)。
- 只驗好友、我的其他機器、等我同意的申請;每項一天最多一次、每輪最多 PER_ROUND 項;5 秒逾時、最多讀 200 KB、只准 https。
  (抓證明頁會讓那個網站看到這台的 IP ⇒ 不對陌生人抓。)
結果寫進 peers.json 的 checks(dm_global_files.note_check);核心 aib chat contacts 讀出來給艦長室。
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import chat_roster as CR  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402

EVERY_S = 86400
PER_ROUND = 5
TIMEOUT_S = 5
MAX_BYTES = 200 * 1024


def http_get(url, timeout=TIMEOUT_S):
    """抓一頁。回 (http 狀態, 內容);連不上回 (None, "")。只准 https(測試設 AIB_CHAT_VERIFY_ALLOW_LOCAL=1 才准 http://127.0.0.1)。"""
    import urllib.error
    import urllib.request
    local = os.environ.get("AIB_CHAT_VERIFY_ALLOW_LOCAL") == "1" and url.startswith("http://127.0.0.1:")
    if not (url.startswith("https://") or local):
        return None, ""
    req = urllib.request.Request(url, headers={"User-Agent": "aib-chat-verify/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(MAX_BYTES).decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        err.close()
        return err.code, ""
    except Exception:   # noqa: BLE001
        return None, ""


def check_nip05(name, owner_npub):
    local, _, domain = (name or "").partition("@")
    if not local or not domain:
        return None
    base = os.environ.get("AIB_CHAT_NIP05_BASE") or "https://%s" % domain      # 測試換成本機假站
    code, body = http_get("%s/.well-known/nostr.json?name=%s" % (base, local))
    if code != 200:
        return None
    try:
        names = json.loads(body).get("names") or {}
    except (ValueError, AttributeError):
        return False
    return names.get(local) == NK.npub_to_hex(owner_npub)


def check_social(item, owner_npub):
    if not isinstance(item, dict) or item.get("p") not in CR.SOCIAL_AUTO or not item.get("proof"):
        return None
    code, body = http_get(item["proof"])
    if code != 200:
        return None
    return owner_npub in body


def verify_round(root, now=None, env=None):
    """驗一輪,回驗了幾項。"""
    now = time.time() if now is None else now
    owners = set(f["owner"] for f in CR.read_friends(env)) | set(f["owner"] for f in CR.read_friends(env, "mine")) \
        | set(r["owner"] for r in GF.read_requests(root))
    todo = []
    for owner, p in GF.read_peers(root).items():
        if owner not in owners or CR.is_blocked(owner, env):
            continue
        checks = p.get("checks") or {}
        items = ([("nip05", p["nip05"])] if p.get("nip05") else []) + \
            [("social:%s:%s" % (x.get("p"), x.get("v")), x) for x in p.get("socials") or [] if isinstance(x, dict)]
        for key, item in items:
            if now - int((checks.get(key) or {}).get("at") or 0) >= EVERY_S:
                todo.append((owner, key, item))
    for owner, key, item in todo[:PER_ROUND]:
        ok = check_nip05(item, owner) if key == "nip05" else check_social(item, owner)
        GF.note_check(root, owner, key, ok, now=now)
    return len(todo[:PER_ROUND])
