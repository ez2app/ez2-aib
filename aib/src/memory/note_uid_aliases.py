#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""D1:寫入時 uid 一律進 aliases.

Obsidian 的 [[ ]] 認檔名與 aliases,不認 frontmatter 的 uid.
新增與更新必須走不同的入口、共用同一條合併函式 ——
否則「新增時對、更新時掉」只在第二次寫入才發作,而且不紅.
本模組不寫 vault;呼叫端給治具路徑.
"""
import re

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?", re.S)


def parse_alias_list(raw):
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw).strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    out = []
    for part in text.split(","):
        item = part.strip().strip("\"'")
        if item:
            out.append(item)
    return out


def ensure_uid_in_aliases(aliases, uid):
    """寫入時 uid 一律進 aliases."""
    uid = str(uid).strip()
    out = []
    seen = set()
    for item in parse_alias_list(aliases):
        if item not in seen:
            out.append(item)
            seen.add(item)
    if uid not in seen:
        out.insert(0, uid)
    return out


def write_new_frontmatter(uid, aliases=None, **fields):
    """新增一則筆記的 frontmatter:uid 必在 aliases."""
    fm = dict(fields)
    fm["uid"] = uid
    fm["aliases"] = ensure_uid_in_aliases(aliases, uid)
    return fm


def write_update_frontmatter(existing, **fields):
    """更新既有筆記:沿用舊 aliases 再合併 uid,不得把 alias 弄丟."""
    uid = str(existing.get("uid") or fields.get("uid") or "").strip()
    prev = existing.get("aliases")
    merged = dict(existing)
    merged.update(fields)
    merged["uid"] = uid
    merged["aliases"] = ensure_uid_in_aliases(prev, uid)
    return merged


def uid_in_aliases(fm, uid=None):
    uid = str(uid or fm.get("uid") or "").strip()
    if not uid:
        return False
    return uid in parse_alias_list(fm.get("aliases"))


def parse_simple_frontmatter(markdown):
    """只夠 D1:一行一個 key: value.不是通用 YAML."""
    match = _FRONTMATTER.match(markdown or "")
    if not match:
        return {}
    out = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip()[:1] in "#-" or ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


def assert_markdown_uid_in_aliases(markdown, uid=None):
    """落檔前的守衛:有 uid 就必須在 aliases 裡."""
    fm = parse_simple_frontmatter(markdown)
    uid = str(uid or fm.get("uid") or "").strip()
    if not uid:
        raise ValueError("筆記沒有 uid,無法保證 aliases")
    if not uid_in_aliases(fm, uid):
        raise ValueError("uid %s 不在 aliases 裡:Obsidian [[uid]] 解不開" % uid)
    return fm
