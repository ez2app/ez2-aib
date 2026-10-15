#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""人格卡跟席的 bot 種類對不上 ⇒ 綁卡時警告(10-12 the maintainer 核准 Bot 開發第 16 件,a seat)。只警告:不擋綁、不改卡。

實例:a seat 是 ClaudeBot 席,卻綁到「新建AI席工程師」(e0dba6,MuseBot 卡),卡上寫「沒 shell、dm 只走 outbox」,
跟這一席的實際能力衝突。

卡沒有「給哪一家 bot」的欄位,照下面順序認(認不出 ⇒ None,不警告,不猜):
  1. 卡的 code 以 bot id 開頭(例 musebot-eng);
  2. 正文第一句「你是…」只點名**一家** bot(menu_label 或 id;兩家以上 = 工具人格,例 Bot 接線員)。
bot 名單一律從 providers.json(bot_caps.registry_bots)來,不另寫一份。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_COCKPIT = Path(__file__).resolve().parents[1] / "cockpit"
if str(_COCKPIT) not in sys.path:
    sys.path.insert(0, str(_COCKPIT))

import bot_caps  # noqa: E402


def bot_labels(providers=None):
    """{bot id: menu_label};來源 providers.json 的 menu_group=="bot"。"""
    path = Path(providers) if providers else _COCKPIT / "providers.json"
    d = json.loads(path.read_text(encoding="utf-8"))
    ps = d.get("providers", d)
    items = ps.items() if isinstance(ps, dict) else ((p.get("id"), p) for p in ps)
    bots = bot_caps.registry_bots(path)
    return {k: str(v.get("menu_label") or k) for k, v in items if k in bots and isinstance(v, dict)}


def _first_identity_line(text):
    body = text or ""
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end >= 0:
            body = body[end + 4:]
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("你是"):
            return line
        if line and not line.startswith("#"):
            return ""          # 第一段不是「你是…」⇒ 不從正文猜
    return ""


def card_bot_kind(entry, text="", labels=None):
    """人格卡(cards.json 的一筆 + 正文)→ 它寫給哪一家 bot;認不出 ⇒ None。"""
    labels = labels if labels is not None else bot_labels()
    code = str((entry or {}).get("code") or "").lower()
    for bot in sorted(labels):
        if re.match(r"%s(?![a-z0-9])" % re.escape(bot), code):
            return bot
    line = _first_identity_line(text).lower()
    if not line:
        return None
    named = {bot for bot, label in labels.items()
             if re.search(r"(?<![a-z0-9])(%s|%s)(?![a-z0-9])" % (re.escape(bot), re.escape(label.lower())), line)}
    return named.pop() if len(named) == 1 else None


def raw_entry(cards_path, uid):
    """cards.json 裡這張卡**原本那一筆**(含 code、card_path_rel)。
    bind_one 拿到的 entry 是 binding_projection 正規化過的,只剩 uid / 名稱 / 指紋 —— 拿它判斷永遠認不出(10-13 實查)。
    讀不到回 None。"""
    try:
        cards = json.loads(Path(cards_path).read_text(encoding="utf-8")).get("cards") or {}
    except (OSError, ValueError, AttributeError):
        return None
    row = cards.get(uid) if isinstance(cards, dict) else None
    return row if isinstance(row, dict) else None


def card_text(cards_path, entry):
    """Vault 根 + 這筆的 card_path_rel → 正文;讀不到回空字串。
    Vault 根先看現在的 EZ2_VAULT_DIR / EZ2_VAULT_ROOT,再退回 cards.json 記的 source.vault_root
    (10-16:那是掃描當時的絕對路徑,庫搬家後就指空)。"""
    try:
        stored = (json.loads(Path(cards_path).read_text(encoding="utf-8")).get("source") or {}).get("vault_root")
        rel = (entry or {}).get("card_path_rel")
        if not rel:
            return ""
        live = os.environ.get("EZ2_VAULT_DIR") or os.environ.get("EZ2_VAULT_ROOT")
        for root in (live, stored):
            if not root:
                continue
            root = Path(os.path.expanduser(root))
            path = (root / rel).resolve()
            if root.resolve() in path.parents and path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        return ""
    except (OSError, ValueError, AttributeError):
        return ""


def fit_warning(seat_provider, entry, text="", labels=None):
    """席的 provider 跟卡認出來的 bot 不同 ⇒ {seat_provider, card_kind, card_uid, display_name};否則 None。
    卡是 bot 卡、席是一般席(例 claude)也算不合;卡認不出 ⇒ None。"""
    kind = card_bot_kind(entry, text, labels)
    seat_provider = str(seat_provider or "")
    if kind is None or kind == seat_provider:
        return None
    return {"seat_provider": seat_provider or "?", "card_kind": kind,
            "card_uid": (entry or {}).get("uid") or (entry or {}).get("persona_uid"),
            "display_name": (entry or {}).get("display_name")}
