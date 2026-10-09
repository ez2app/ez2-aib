#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""289 (c) `aib page chat --autofill "一句描述"`(SPEC-289 §七):用套版 skill(八格風格表的人設 / 首頁那句)+ 用戶那句描述,
生成**只缺的**欄,全進草稿、一次 review_url;發布仍是人到預覽確認頁按。

生成的是什麼(全部是決定性的字串組合,這支 CLI 不呼叫模型——模型呼叫在席位裡的 AI,用量另走 `aib page usage`):
  - profile.bio 缺 ⇒ 描述本句(PAGE 上限 300 UTF-16 單元);profile.status_text 缺 ⇒ 描述的第一個子句(≤40),子句太長就用風格的首頁那句;
    display_name 永遠有值(註冊必填)不生成。
  - text 顆缺 text ⇒ 描述本句;image 顆缺 asset_id / shop 顆缺 image_asset_id ⇒ 示範圖占位:本機畫一張 128×128 單色 PNG(風格色)走 288 上傳,
    asset_id 填回同一顆(update,payload 整份帶回);shop / event 缺標題等**要人給的事實**(商品名、價格、日期)不編,列成「要你自己給」。
  - 已有欄零 ops(F1):有值的欄結構上不進 plan;plan 只產 set_profile + upsert_block(update),零 publish(F2)——這條路上沒有 publish 操作。
刀 autofill-ignore-current:不看現值(已有欄也生成)⇒ A1 / A2 紅;autofill-skip-placeholder:占位圖不上傳、asset_id 缺著 ⇒ A2 紅。
"""
from __future__ import annotations

import os
import re
import struct
import tempfile
import zlib

import intake
import styles
import upload as upload_mod

PLACEHOLDER_SIZE = 128
# 八格風格各一個占位底色(示範圖是占位,不是設計;上線前店主換成自己的圖)
PLACEHOLDER_RGB = {"nebula": (63, 44, 117), "mono": (200, 200, 200), "dawn": (233, 150, 96), "forest": (37, 84, 63),
                   "rosegold": (214, 156, 140), "starlight": (222, 214, 196), "pop": (240, 60, 120), "ocean": (18, 74, 120)}
CLAUSE_SPLIT = re.compile(r"[。,,!?!?;;\n]")   # 第一個子句:到句號 / 逗號為止(在線狀態是一小句)


class AutofillError(Exception):
    pass


def placeholder_png(rgb, size=PLACEHOLDER_SIZE):
    """單色 PNG(RGB 8-bit),零外部工具。"""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    row = b"\x00" + bytes(rgb) * size
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(row * size, 9)) + chunk(b"IEND", b"")


def _status_from(description, style):
    first = CLAUSE_SPLIT.split(str(description or "").strip(), 1)[0].strip(" ,,")
    if first and len(first.encode("utf-16-le")) // 2 <= intake.PROFILE_CAPS["status_text"]:
        return intake._cap(first, intake.PROFILE_CAPS["status_text"])
    return intake._cap(style["homepage_line"] if style else "", intake.PROFILE_CAPS["status_text"])


def plan(state, description, required_keys, test_mutation=""):
    """現值(GET /draft 回應體,沒草稿時 blocks 已補成線上)+ 描述 ⇒ (skill_ops, placeholder_blocks, notes)。
    placeholder_blocks = 要占位圖的顆 [(block_id, block_type, payload, field, visibility)],由呼叫端上傳後填 asset 再變 upsert_block(visibility 帶顆原本的:members / hidden 不准被改公開;RL-145 F1)。"""
    description = str(description or "").strip()
    if not description:
        raise AutofillError("--autofill 要給一句描述(店 / 人 / 作品是什麼),空的沒東西可生成")
    profile = intake.profile_of(state, state)      # 沒草稿時 _current_state 已把線上 blocks 補在頂層,profile 也在頂層
    theme = (state.get("appearance") or {}).get("theme_id") if isinstance(state.get("appearance"), dict) else None
    style = styles.for_theme(theme)
    ignore_current = test_mutation == "autofill-ignore-current"
    fields, _gap = intake.missing_profile_fields(profile)
    if ignore_current:
        fields = ["bio", "status_text"]
    ops, notes, placeholders = [], [], []
    prof_op = {"op": "set_profile"}
    if "bio" in fields:
        prof_op["bio"] = intake._cap(description, intake.PROFILE_CAPS["bio"])
    if "status_text" in fields:
        prof_op["status_text"] = _status_from(description, style)
    if len(prof_op) > 1:
        ops.append(prof_op)
        notes.append("生成:%s(來自你的描述%s)" % ("、".join(intake.PROFILE_FIELD_LABELS[k] for k in prof_op if k != "op"), " + 風格 %s 的人設「%s」" % (style["id"], style["persona"]) if style else ""))
    else:
        notes.append("個人檔案的自介 / 在線狀態都已有值,沒動")
    if "display_name" in fields:
        notes.append("要你自己給:名字(註冊必填,這裡不編)")
    for block in intake._blocks_of(state, state):
        if not isinstance(block, dict):
            continue
        btype, bid = block.get("block_type"), block.get("block_id")
        payload = dict(block.get("payload") or {}) if isinstance(block.get("payload"), dict) else {}
        missing = [k for k in required_keys.get(btype, ()) if (ignore_current or k not in payload or payload[k] in (None, ""))]
        if btype == "text" and "text" in missing:
            payload["text"] = intake._cap(description, 2000)
            payload.setdefault("format", "plain")
            ops.append({"op": "upsert_block", "block_id": bid, "block_type": btype, "visibility": block.get("visibility", "public"), "payload": payload})
            notes.append("生成:text 區塊 %s 的內容(來自你的描述)" % bid)
            continue
        visibility = block.get("visibility", "public")
        if btype == "image" and "asset_id" in missing:
            placeholders.append((bid, btype, payload, "asset_id", visibility))
            continue
        if btype == "shop" and (ignore_current or not payload.get("image_asset_id")):
            placeholders.append((bid, btype, payload, "image_asset_id", visibility))
        human = [k for k in missing if not (btype == "shop" and k == "image_asset_id")]
        if human:
            notes.append("要你自己給:%s 區塊 %s 的 %s(商品名 / 價格 / 日期這類事實不編)" % (btype, bid, ", ".join(human)))
    return ops, placeholders, notes


def materialize_placeholders(placeholders, state, api, seat_addr, out, environ=None, tmp_root=None, test_mutation=""):
    """占位圖:每顆一張單色 PNG(風格色)→ 288 上傳 → 該顆的 upsert_block(update,payload 整份帶回 + asset)。回 (ops, notes)。
    刀 autofill-skip-placeholder:不上傳、asset 缺著 ⇒ A2 紅。"""
    if not placeholders or test_mutation == "autofill-skip-placeholder":
        return [], (["占位圖沒上傳(刀)"] if placeholders else [])
    theme = (state.get("appearance") or {}).get("theme_id") if isinstance(state.get("appearance"), dict) else None
    rgb = PLACEHOLDER_RGB.get(theme, (160, 160, 160))
    tmp_dir = tempfile.mkdtemp(prefix="page-autofill-", dir=tmp_root)
    ops, notes = [], []
    try:
        paths = []
        for seq, (bid, btype, payload, field, _visibility) in enumerate(placeholders, 1):
            path = os.path.join(tmp_dir, "%02d-placeholder-%s.png" % (seq, btype))
            with open(path, "wb") as handle:
                handle.write(placeholder_png(rgb))
            paths.append(path)
        ids = {}
        upload_mod.run_upload(api, paths, out, seat_addr, alt="示範圖占位,請換成自己的圖", environ=environ, tmp_root=tmp_root, ids=ids)
        for path, (bid, btype, payload, field, visibility) in zip(paths, placeholders):
            asset_id = ids.get(path)
            if not asset_id:
                notes.append("占位圖上傳失敗,%s 區塊 %s 沒圖" % (btype, bid))
                continue
            filled = dict(payload)
            filled[field] = asset_id
            if btype == "image" and not filled.get("alt"):
                filled["alt"] = "示範圖占位"          # alt 是必填(空字串算缺,RL-099 那道閘會擋整批)
            ops.append({"op": "upsert_block", "block_id": bid, "block_type": btype, "visibility": visibility, "payload": filled})   # 顆原本的 visibility(RL-145 F1)
            notes.append("生成:%s 區塊 %s 的示範圖占位 → %s(上線前換成自己的圖)" % (btype, bid, asset_id))
    finally:
        for name in os.listdir(tmp_dir):
            try:
                os.remove(os.path.join(tmp_dir, name))
            except OSError:
                pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
    return ops, notes
