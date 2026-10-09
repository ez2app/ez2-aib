#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""D2:寫入時強制產生前向連結.

Obsidian 的反向連結面板靠正文裡的 [[uid]]。反向連結是自動的,
前向連結要人或 AI 去寫。至少三種:
  世代更新 → 指前一版
  附件     → 主體指附件
  對話切片 → 指原對話

新增與更新必須走不同的入口、共用同一條合併函式 ——
否則「新增時對、更新時掉」只在第二次寫入才發作,而且不紅.
本模組不寫 vault;呼叫端給治具路徑.
"""
import re

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?", re.S)

GENERATION_KEYS = ("previous_uid", "predecessor_uid", "generation_of")
ATTACHMENT_KEYS = ("attachment_uid",)
ATTACHMENT_LIST_KEYS = ("attachment_uids", "attachments")
CONVERSATION_KEYS = ("conversation_uid", "source_conversation_uid",
                     "source_uid")


def wikilink(uid):
    uid = str(uid or "").strip()
    return "[[%s]]" % uid if uid else ""


def extract_wikilinks(text):
    out, seen = [], set()
    for raw in WIKILINK_RE.findall(text or ""):
        uid = str(raw).strip()
        if uid and uid not in seen:
            out.append(uid)
            seen.add(uid)
    return out


def _one(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    return [text] if text else []


def targets_from_fields(fields):
    """從寫入欄位收集必須指向的 uid。沒有關聯就空,不硬造連結."""
    fields = fields or {}
    out, seen = [], set()

    def add(values):
        for uid in values:
            if uid and uid not in seen:
                out.append(uid)
                seen.add(uid)

    for key in GENERATION_KEYS + CONVERSATION_KEYS + ATTACHMENT_KEYS:
        add(_one(fields.get(key)))
    for key in ATTACHMENT_LIST_KEYS:
        add(_one(fields.get(key)))
    add(_one(fields.get("forward_links")))
    return out


def ensure_forward_links(body, targets):
    """正文裡沒有的 [[uid]] 補上。已有的不重寫、不改順序."""
    out = body if body is not None else ""
    for uid in targets or []:
        uid = str(uid).strip()
        if not uid:
            continue
        if uid in extract_wikilinks(out):
            continue
        if out and not out.endswith("\n"):
            out += "\n"
        out += wikilink(uid) + "\n"
    return out


def write_new_body(body, targets):
    """新增一則筆記的正文:有關聯 uid 就必須出現 [[uid]]."""
    return ensure_forward_links(body, targets)


def write_update_body(existing_body, new_body, targets=None):
    """更新既有筆記:沿用舊 [[連結]] 再合併新目標,不得把連結弄丟."""
    kept = extract_wikilinks(existing_body)
    merged_targets = []
    seen = set()
    for uid in list(kept) + list(targets or []):
        uid = str(uid or "").strip()
        if uid and uid not in seen:
            merged_targets.append(uid)
            seen.add(uid)
    base = new_body if new_body is not None else (existing_body or "")
    return ensure_forward_links(base, merged_targets)


def assert_markdown_has_forward_links(markdown, targets):
    """落檔前的守衛:宣告要指的 uid 必須在正文 [[ ]] 裡."""
    need = [str(t).strip() for t in (targets or []) if str(t).strip()]
    if not need:
        return extract_wikilinks(markdown)
    found = extract_wikilinks(markdown)
    missing = [uid for uid in need if uid not in found]
    if missing:
        raise ValueError("前向連結缺失 %s:Obsidian 反向面板會是空的" % missing)
    return found


def wikilink_resolves(uid, aliases_by_note):
    """[[uid]] 解得開 = 目標的檔名或 aliases 含這個 uid.量測必須把 aliases 算進去."""
    uid = str(uid or "").strip()
    if not uid:
        return False
    for note_id, aliases in (aliases_by_note or {}).items():
        names = [str(note_id)] + [str(a) for a in (aliases or [])]
        if uid in names:
            return True
    return False
