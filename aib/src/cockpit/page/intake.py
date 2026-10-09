#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""288 `aib page intake`:客戶準備清單(docs/skills/page-intake-checklist.md)+ `--missing` 對已綁的頁逐條印「缺:…」。

清單本身不打網路;`--missing` 讀 GET /blocks 與 GET /draft(profile / appearance / blocks 都在真回應裡,262(f) 起),
不重做契約驗證——缺什麼由這裡看得到的欄判,存不存得進去由伺服器 422 說。

289 (b) 缺項三層(SPEC-289 §二、SPEC-289-2 §一 4):第一層 = 頁現值(有就不缺);第二層 = 候選——GET /draft 頂層 `social_snapshots`
(289 二:LINE / FB 綁定時存的名字 / 頭像 / 狀態句)與各轉接器快照(`--source <url>` 或 `--refetch` 頁上取回顆的 provenance 網址);
第三層 = 問人(印「缺:…」+ 「候選:… 要用嗎?」,沒候選的缺項列成一句「要你自己給」)。`--accept` 才寫:只把**缺的**欄用第一個候選
填進草稿(set_profile 走 PUT /profile ⇒ 草稿),零 publish;現值已有的欄結構上不會出現在候選裡(S1)。
刀 candidates-ignore-current:候選不看現值(已有欄也給)⇒ M1 / M2 紅。
"""
from __future__ import annotations

import os

_HERE = os.path.dirname(os.path.abspath(__file__))
CHECKLIST_REL = os.path.join("docs", "skills", "page-intake-checklist.md")


class IntakeError(Exception):
    pass


def checklist_path(root=None):
    root = root or os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
    return os.path.join(root, CHECKLIST_REL)


def checklist_text(root=None):
    path = checklist_path(root)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise IntakeError("清單檔讀不到 %s:%s" % (path, exc))


def parse_required_table(text):
    """把第 4 節那張表抽成 {型別: (必填欄, …)};★ 記號只給人看,抽掉。尺 I1 用這個對 template_skill.BLOCK_REQUIRED_KEYS。"""
    out = {}
    in_section = False
    for line in text.splitlines():
        if line.startswith("## 4."):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("型別", "") or set(cells[0]) <= set("-"):
            continue
        keys = tuple(k.strip().rstrip("★").strip() for k in cells[1].split(",") if k.strip())
        out[cells[0]] = keys
    return out


def profile_of(blocks_payload, draft_payload):
    profile = {}
    for payload in (blocks_payload, draft_payload):
        if isinstance(payload, dict) and isinstance(payload.get("profile"), dict):
            profile.update({k: v for k, v in payload["profile"].items() if v not in (None, "")})
    return profile


def _blocks_of(blocks_payload, draft_payload):
    if isinstance(draft_payload, dict) and isinstance(draft_payload.get("draft"), dict) \
            and isinstance(draft_payload["draft"].get("blocks"), list):
        return draft_payload["draft"]["blocks"]
    if isinstance(blocks_payload, dict) and isinstance(blocks_payload.get("blocks"), list):
        return blocks_payload["blocks"]
    return []


def required_rows(text):
    """第 4 節那張表,{型別: 整列原文}(給人看的那一行,含 ★ 與例子)。chat 端少必填欄就印這一列。"""
    out = {}
    in_section = False
    for line in text.splitlines():
        if line.startswith("## 4."):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("型別", "") or set(cells[0]) <= set("-"):
            continue
        out[cells[0]] = line.rstrip()
    return out


def missing_op_lines(skill_ops, required_keys, root=None):
    """RL-099 第二點(chat 端 apply 前):每個 upsert_block 的 payload 對必填表,少了就回
    「缺:<型別> 區塊 <id|新> 的 <欄>」+ 清單第 4 節那一列;全齊回 []。★ 一個請求都還沒發就停在這。"""
    lines = []
    rows = None
    for index, op in enumerate(skill_ops or [], 1):
        if not isinstance(op, dict) or op.get("op") != "upsert_block":
            continue
        btype = op.get("block_type")
        payload = op.get("payload") if isinstance(op.get("payload"), dict) else {}
        missing = [key for key in required_keys.get(btype, ()) if key not in payload or payload[key] in (None, "")]
        if not missing:
            continue
        if rows is None:
            rows = required_rows(checklist_text(root))
        lines.append("缺:%s 區塊 %s 的 %s(第 %d 個操作;清單第 4 節)"
                     % (btype, op.get("block_id") or "新", ", ".join(missing), index))
        if rows.get(btype):
            lines.append("  " + rows[btype])
    return lines


def missing_lines(blocks_payload, draft_payload, required_keys):
    """回一串「缺:…」。母體 = 清單五節裡機器看得到的那些(第 3 節風格由 appearance 判;第 5 節圖以 image/shop 顆有沒有圖判)。"""
    lines = []
    profile = profile_of(blocks_payload, draft_payload)
    for field, label in (("display_name", "名字"), ("bio", "一句自介"), ("status_text", "一句在線狀態")):
        if not str(profile.get(field) or "").strip():
            lines.append("缺:%s(第 1 節 %s)" % (label, field))
    socials = profile.get("socials") or profile.get("social_links") or []
    if not socials:
        lines.append("缺:社群連結(第 2 節,建議三個)")
    elif len(socials) < 3:
        lines.append("缺:社群連結還差 %d 個(第 2 節,建議三個)" % (3 - len(socials)))
    appearance = None
    for payload in (draft_payload, blocks_payload):
        if isinstance(payload, dict) and isinstance(payload.get("appearance"), dict):
            appearance = payload["appearance"]
            break
    if not appearance or not appearance.get("template_id"):
        lines.append("缺:風格(第 3 節,八選一)")
    blocks = _blocks_of(blocks_payload, draft_payload)
    if not blocks:
        lines.append("缺:區塊(第 4 節,一顆都沒有)")
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("block_type")
        payload = block.get("payload") if isinstance(block.get("payload"), dict) else {}
        for key in required_keys.get(btype, ()):
            if key not in payload or payload[key] in (None, ""):
                lines.append("缺:%s 區塊 %s 的 %s(第 4 節)" % (btype, block.get("block_id", "?"), key))
        if btype == "image" and not payload.get("asset_id"):
            lines.append("缺圖:image 區塊 %s 待上傳(第 5 節 aib page upload)" % block.get("block_id", "?"))
        if btype == "shop" and not payload.get("image_asset_id"):
            lines.append("缺圖:shop 區塊 %s 的商品圖待上傳(第 5 節 aib page upload)" % block.get("block_id", "?"))
    return lines


# ---- 289 (b) 缺項三層:第二層候選 ---------------------------------------------------------------------------------------------

PROFILE_FIELD_LABELS = {"display_name": "名字", "bio": "一句自介", "status_text": "一句在線狀態"}
PROFILE_CAPS = {"display_name": 80, "bio": 300, "status_text": 40}      # 抄 PAGE validateProfileTarget / safeText 的上限(UTF-16 單元)
SOCIALS_TARGET = 3
SNAPSHOT_PROVIDER_ORDER = ("line", "facebook", "google", "apple", "ig", "threads")
SNAPSHOT_LABELS = {"line": "LINE", "facebook": "Facebook", "google": "Google", "apple": "Apple", "ig": "Instagram", "threads": "Threads"}


def _cap(value, cap):
    """PAGE 收得下的字:走 intake_linkinbio.page_text(去控制字元與 < >、trim、UTF-16 單元上限、grapheme 不切半——ZWJ / 膚色修飾一起留或一起去;RL-145 F2)。
    延後 import:避免 intake 模組載入就把四站轉接器登記進 intake_sources(那是 cli 那一行的事)。"""
    import intake_linkinbio
    return intake_linkinbio.page_text(str(value or ""), cap)


def social_snapshots_of(blocks_payload, draft_payload):
    """GET /draft(或 /blocks)頂層 social_snapshots:{provider: {display_name, avatar_url, status_text, fetched_at, seeded}};沒有 ⇒ {}。"""
    for payload in (draft_payload, blocks_payload):
        if isinstance(payload, dict) and isinstance(payload.get("social_snapshots"), dict):
            return payload["social_snapshots"]
    return {}


def provenance_urls(blocks_payload, draft_payload):
    """頁上取回顆(provenance.url)的來源網址,照顆序去重——`--refetch` 用它們重抓轉接器快照。"""
    urls = []
    for block in _blocks_of(blocks_payload, draft_payload):
        prov = block.get("provenance") if isinstance(block, dict) else None
        url = prov.get("url") if isinstance(prov, dict) else None
        if isinstance(url, str) and url.startswith("https://") and url not in urls:
            urls.append(url)
    return urls


def missing_profile_fields(profile):
    """第一層:現值空的個人檔案欄(照清單第 1 節順序)+ socials 還差幾條。"""
    fields = [f for f in ("display_name", "bio", "status_text") if not str((profile or {}).get(f) or "").strip()]
    socials = (profile or {}).get("socials") or (profile or {}).get("social_links") or []
    return fields, max(0, SOCIALS_TARGET - len(socials))


def candidates(profile, social_snapshots, adapter_snapshots, test_mutation=""):
    """第二層:只替**缺的**欄找候選(第一層現值有 ⇒ 結構上不會給;S1)。回 [{field, value, source}],同一欄照來源順序列
    (社群快照 line → facebook → … 先於轉接器快照,轉接器照給的順序)。socials 候選是「頁上還沒有的平台」,最多補到三條。
    刀 candidates-ignore-current:不看現值(已有欄也給候選)⇒ M1 / M2 紅。"""
    fields, socials_gap = missing_profile_fields(profile)
    if test_mutation == "candidates-ignore-current":
        fields, socials_gap = ["display_name", "bio", "status_text"], SOCIALS_TARGET
    wanted = set(fields)
    out = []
    snaps = social_snapshots if isinstance(social_snapshots, dict) else {}
    ordered = [p for p in SNAPSHOT_PROVIDER_ORDER if p in snaps] + sorted(p for p in snaps if p not in SNAPSHOT_PROVIDER_ORDER)
    for provider in ordered:
        snap = snaps.get(provider) if isinstance(snaps.get(provider), dict) else {}
        label = SNAPSHOT_LABELS.get(provider, provider)
        for field in ("display_name", "status_text"):            # 社群快照沒有 bio(SPEC-289-2 §四:LINE/FB 個人帳號不給)
            value = _cap(snap.get(field), PROFILE_CAPS[field])
            if field in wanted and value:
                out.append({"field": field, "value": value, "source": "%s 的%s" % (label, "名字" if field == "display_name" else "狀態句")})
    existing = {s.get("platform") for s in ((profile or {}).get("socials") or (profile or {}).get("social_links") or []) if isinstance(s, dict)}
    picked = set()
    for url, snapshot in adapter_snapshots or []:
        prof = snapshot.get("profile") if isinstance(snapshot, dict) and isinstance(snapshot.get("profile"), dict) else {}
        source = "%s 的快照" % url
        for field in ("display_name", "bio", "status_text"):
            value = _cap(prof.get(field), PROFILE_CAPS[field])
            if field in wanted and value:
                out.append({"field": field, "value": value, "source": source})
        for social in prof.get("socials") or []:
            if len(picked) >= socials_gap or not isinstance(social, dict):
                break
            platform = social.get("platform")
            if platform and platform not in existing and platform not in picked and isinstance(social.get("url"), str):
                picked.add(platform)
                out.append({"field": "socials", "value": {"platform": platform, "url": social["url"]}, "source": source})
    return out


def candidate_lines(cands, missing_fields, socials_gap):
    """第三層的問句:每個候選一行「候選:…要用嗎?」;沒候選的缺項一句「要你自己給」。"""
    lines = []
    for c in cands:
        if c["field"] == "socials":
            lines.append("  候選:社群連結 %s %s(來自 %s)要加嗎?" % (c["value"]["platform"], c["value"]["url"], c["source"]))
        else:
            lines.append("  候選:%s「%s」可以填%s——要用嗎?" % (c["source"], c["value"], PROFILE_FIELD_LABELS[c["field"]]))
    covered = {c["field"] for c in cands}
    unanswered = [PROFILE_FIELD_LABELS[f] for f in missing_fields if f not in covered]
    if socials_gap and "socials" not in covered:
        unanswered.append("社群連結")
    if unanswered:
        lines.append("  沒有候選,要你自己給:%s" % "、".join(unanswered))
    if cands:
        lines.append("  加 --accept 就把上面的候選寫進草稿(只填缺欄、不蓋已有;發布仍是你到預覽確認頁按)")
    return lines


def accept_ops(cands, profile):
    """--accept:每個缺欄取第一個候選 ⇒ 一顆 set_profile;socials = 頁上既有 + 候選(PUT 是整份取代,所以要帶既有的)。沒東西 ⇒ []。"""
    op = {"op": "set_profile"}
    for c in cands:
        if c["field"] != "socials" and c["field"] not in op:
            op[c["field"]] = c["value"]
    added = [c["value"] for c in cands if c["field"] == "socials"]
    if added:
        existing = [dict(s) for s in ((profile or {}).get("socials") or (profile or {}).get("social_links") or []) if isinstance(s, dict)]
        op["socials"] = [{"platform": s.get("platform"), "url": s.get("url")} for s in existing] + added
    return [op] if len(op) > 1 else []
