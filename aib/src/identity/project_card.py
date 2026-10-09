#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""專案 uid 卡＋四層形狀(憲法／記憶／工作狀態／接手包)。

設計:docs/DESIGN-persona-autoload-0926.md §10.5–10.6。
本檔純資料結構與驗證,不寫 Vault、不碰席位檔活路。
假 project_uid 可用(測試／草稿);真綁定要等派工寫入口。
"""
from __future__ import annotations

import re
from typing import Any, Mapping, MutableMapping, Optional

# 專案 uid:與計畫樹格 uid 同形(可讀前綴 + 十六進位),長度鬆綁給假 uid。
PROJECT_UID_RE = re.compile(r"^[A-Za-z0-9:._@-]{4,128}$")
# 多倉指紋:路徑或別名 → sha256 hex(可短寫 8–64 以利草稿)。
SHA_RE = re.compile(r"^[0-9a-f]{8,64}$")

LAYER_KEYS = ("constitution", "memory", "work_state", "handoff")
LAYER_ZH = {
    "constitution": "專案憲法",
    "memory": "專案記憶",
    "work_state": "工作狀態",
    "handoff": "接手包",
}


class ProjectCardError(ValueError):
    """形狀不合法。"""


def validate_project_uid(uid: str) -> str:
    text = (uid or "").strip()
    if not PROJECT_UID_RE.fullmatch(text):
        raise ProjectCardError("project_uid 形狀不合法: %r" % (uid,))
    return text


def validate_repo_fingerprints(fps: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(fps, Mapping) or not fps:
        raise ProjectCardError("repo_fingerprints 必為非空對照表")
    out: dict[str, str] = {}
    for k, v in fps.items():
        key = str(k).strip()
        val = str(v).strip().lower()
        if not key or not SHA_RE.fullmatch(val):
            raise ProjectCardError("repo_fingerprints[%r]=%r 不合法" % (k, v))
        out[key] = val
    return out


def empty_constitution() -> dict[str, Any]:
    """專案憲法(最慢):目標、紅線、the maintainer 裁定;新專案初稿目標可空。"""
    return {
        "goals": [],          # list[str]
        "red_lines": [],      # list[str]
        "human_rulings": [],   # list[{uid, text, at}]
        "approved": False,
    }


def empty_memory() -> dict[str, Any]:
    """專案記憶(慢):只增不改;每筆帶來源。"""
    return {
        "entries": [],  # list[{at, source_seat, source_provider, kind, text}]
    }


def empty_work_state() -> dict[str, Any]:
    """工作狀態(快):做到哪、誰在做、待裁。"""
    return {
        "now": "",
        "owners": [],     # list[str] 席碼或角色
        "pending": [],    # list[str]
        "next_step": "",
    }


def empty_handoff_layer() -> dict[str, Any]:
    """接手包層(產出指標):正文由 handoff_pack.render 生成,這裡只掛指紋／預算。"""
    return {
        "persona_uid_used": "",
        "persona_version": "",
        "generated_at": "",
        "ctx_budget_bytes": 0,
        "text_sha8": "",
    }


def empty_four_layers() -> dict[str, Any]:
    return {
        "constitution": empty_constitution(),
        "memory": empty_memory(),
        "work_state": empty_work_state(),
        "handoff": empty_handoff_layer(),
    }


def validate_layer(name: str, layer: Mapping[str, Any]) -> dict[str, Any]:
    if name not in LAYER_KEYS:
        raise ProjectCardError("未知層: %r" % (name,))
    if not isinstance(layer, Mapping):
        raise ProjectCardError("層 %s 必須是物件" % name)
    template = {
        "constitution": empty_constitution,
        "memory": empty_memory,
        "work_state": empty_work_state,
        "handoff": empty_handoff_layer,
    }[name]()
    missing = [k for k in template if k not in layer]
    if missing:
        raise ProjectCardError("層 %s 缺欄: %s" % (name, ",".join(missing)))
    return dict(layer)


def validate_four_layers(layers: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(layers, Mapping):
        raise ProjectCardError("layers 必須是物件")
    out: dict[str, Any] = {}
    for key in LAYER_KEYS:
        if key not in layers:
            raise ProjectCardError("缺層: %s(%s)" % (key, LAYER_ZH[key]))
        out[key] = validate_layer(key, layers[key])  # type: ignore[arg-type]
    return out


def make_project_card(
    project_uid: str,
    *,
    title: str = "",
    repo_fingerprints: Optional[Mapping[str, Any]] = None,
    layers: Optional[Mapping[str, Any]] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """組一張專案 uid 卡(純函式;假 uid 可過驗證)。"""
    uid = validate_project_uid(project_uid)
    fps = validate_repo_fingerprints(repo_fingerprints or {"draft": "0" * 8})
    four = validate_four_layers(layers or empty_four_layers())
    card: dict[str, Any] = {
        "schema": "aib-project-card/v1",
        "project_uid": uid,
        "title": (title or "").strip(),
        "repo_fingerprints": fps,
        "layers": four,
    }
    if extra:
        for k, v in extra.items():
            if k in card:
                raise ProjectCardError("extra 不可覆寫正欄: %r" % (k,))
            card[k] = v
    return card


def validate_project_card(card: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(card, Mapping):
        raise ProjectCardError("專案卡必須是物件")
    if card.get("schema") != "aib-project-card/v1":
        raise ProjectCardError("schema 必須是 aib-project-card/v1")
    return make_project_card(
        str(card.get("project_uid", "")),
        title=str(card.get("title", "")),
        repo_fingerprints=card.get("repo_fingerprints"),  # type: ignore[arg-type]
        layers=card.get("layers"),  # type: ignore[arg-type]
        extra={k: v for k, v in card.items()
               if k not in ("schema", "project_uid", "title",
                            "repo_fingerprints", "layers")},
    )


def project_uid_of(card: Mapping[str, Any]) -> str:
    return validate_project_uid(str(card.get("project_uid", "")))
