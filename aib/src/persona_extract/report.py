# SPDX-License-Identifier: Apache-2.0
"""SOP §六 六節輸出與停止點(L5-04)。

輸入=maker 準備的 sections JSON(候選文字由 maker 填,AIB 只驗形狀與紀律);
輸出=docs/handoff/persona-candidates-<code>-<YYYYMMDD>.local.json(不進 git)+ 六節 md(stdout)。
第一行印 content_hash(canonical json sha256);expires_at=+14 天;完成即停(§七不歸這裡)。
"""

import datetime
import hashlib
import json
import os

import gates
import ledgers
import writeguard

FRESH_DAYS = 14
B_EMPTY_LINE = "B:無(首煉輪,依計數法全部進 C)"
F_TEMPLATE = ("本輪提取無候選。已讀來源:{sources};watchlist 新增 {n} 筆;"
              "原因:{reason}")
F_REASONS = ("證據不足", "全部單一事件", "全部狀態未定")


class ReportError(Exception):
    pass


def _canonical(doc):
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(doc):
    return hashlib.sha256(_canonical(doc).encode("utf-8")).hexdigest()


def build(sections, persona_code, now_ms, watchlist_added, self_round=False):
    """sections={A_keep, retire_review, candidates[], D_diff[], E_unknown[], sources[],
    empty_reason?, self_mistakes?[]} → 完整包 dict。丟 ReportError=一 byte 不落盤。"""
    if not isinstance(sections, dict):
        raise ReportError("sections 必須是物件")
    retire_review = (sections.get("retire_review") or "").strip()
    if not retire_review:
        raise ReportError("A 節必含 retire 檢視結果一行(「沒有要退」和「沒看」不能長得一樣)")
    cands = sections.get("candidates") or []
    b_list, c_list, invalid = [], [], []
    for c in cands:
        ok13, problems = gates.validate_candidate_13(c)
        if not ok13:
            invalid.append({"candidate": c.get("candidate_uid") or "?", "problems": problems})
            continue
        dest = gates.candidate_destination(c)
        (b_list if dest == "B" else c_list).append(c)
    if invalid:
        raise ReportError("候選 13 欄不齊,退回 maker 補齊(unknown 是合法值,缺欄不是):%s"
                          % json.dumps(invalid, ensure_ascii=False)[:500])
    for c in b_list:
        if c.get("anchor_grade") != "machine":
            raise ReportError("descriptive-anchor 候選不得進 B(SOP §五)")
    for i, c in enumerate(c_list):
        rc = c.get("watchlist_add_rc")
        if not isinstance(rc, int) or isinstance(rc, bool):
            raise ReportError("C 節第 %d 項缺 watchlist_add_rc(候觀察項必須同步 watchlist add 並貼 exit code,"
                              "否則學費只留在對話裡)" % (i + 1))
    if self_round:
        mistakes = sections.get("self_mistakes") or []
        bad = [m for m in mistakes
               if not (isinstance(m, dict) and (m.get("what") or "").strip()
                       and gates.pointer_kind(m.get("pointer")))]
        if not mistakes or bad:
            raise ReportError("自煉輪候選包必含「本輪我做錯的事」節,逐項附可驗指標;"
                              "空或指標不可驗=不完整退回(SOP §十)")
    empty_handed = not cands
    f_section = None
    if empty_handed:
        reason = sections.get("empty_reason")
        if reason not in F_REASONS:
            raise ReportError("空手輪 F 節需 empty_reason ∈ %s(空手是合法交付,但要說得出為什麼)"
                              % (F_REASONS,))
        f_section = F_TEMPLATE.format(sources="、".join(sections.get("sources") or ["(未列)"]),
                                      n=watchlist_added, reason=reason)
    doc = {
        "schema": "aib-persona-candidates/v1",
        "sop": "PERSONA-EXTRACT-SOP-v3.5",
        "persona_code": persona_code,
        "created_at_ms": now_ms,
        "expires_at_ms": now_ms + FRESH_DAYS * 86400_000,
        "self_round": bool(self_round),
        "A_keep": {"kept": sections.get("A_keep") or [], "retire_review": retire_review},
        "B_candidates": b_list,
        "B_note": None if b_list else B_EMPTY_LINE,
        "C_watchlist": c_list,
        "D_diff": sections.get("D_diff") or [],
        "E_unknown": sections.get("E_unknown") or [],
        "F_empty_handed": f_section,
        "self_mistakes": sections.get("self_mistakes") or [],
        "watchlist_added_with_pointer": watchlist_added,
        "sources": sections.get("sources") or [],
    }
    doc["content_hash"] = content_hash({k: v for k, v in doc.items() if k != "content_hash"})
    return doc


def render_md(doc):
    L = ["content_hash=%s" % doc["content_hash"], "",
         "# 候選包 %s(%s)" % (doc["persona_code"], "自煉輪" if doc["self_round"] else "提取輪"), ""]
    L += ["## A 已完成且可保留", "- retire 檢視:%s" % doc["A_keep"]["retire_review"]]
    L += ["- %s" % x for x in doc["A_keep"]["kept"]]
    L += ["", "## B 本 session 新候選(machine-anchor 且 ≥2 事件群)"]
    if doc["B_candidates"]:
        L += ["- %s → %s" % (c["candidate_uid"], c["target_field"]) for c in doc["B_candidates"]]
    else:
        L += [doc["B_note"]]
    L += ["", "## C 仍不能寫入(候觀察,附 watchlist add rc)"]
    L += ["- %s(add rc=%d)" % (c["candidate_uid"], c["watchlist_add_rc"]) for c in doc["C_watchlist"]] or ["(無)"]
    L += ["", "## D 差異表"] + (["- %s" % x for x in doc["D_diff"]] or ["(無)"])
    L += ["", "## E unknown"] + (["- %s" % x for x in doc["E_unknown"]] or ["(無)"])
    if doc["F_empty_handed"]:
        L += ["", "## F 本輪無候選", doc["F_empty_handed"]]
    if doc["self_round"]:
        L += ["", "## 本輪我做錯的事"]
        L += ["- %s — %s" % (m["what"], m["pointer"]) for m in doc["self_mistakes"]]
    L += ["", "(完成即停:§七 任何一步都要人類逐項核准後另行執行。)"]
    return "\n".join(L) + "\n"


def out_path(repo_root, persona_code, now_ms):
    day = datetime.datetime.utcfromtimestamp(now_ms / 1000).strftime("%Y%m%d")
    return os.path.join(repo_root, "docs", "handoff",
                        "persona-candidates-%s-%s.local.json" % (persona_code, day))


def write_package(doc, repo_root):
    path = out_path(repo_root, doc["persona_code"], doc["created_at_ms"])
    with writeguard.open_for_write(path, "x", repo_root=repo_root, encoding="utf-8") as fh:
        fh.write(_canonical(doc) + "\n")
    return path


def inspect(path, now_ms):
    """0=valid / 4=hash 不符 / 5=形壞 / 3=過保鮮期(對齊 candidate 工具 exit 語意層級)。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return 5, {"why": "unreadable"}
    if not isinstance(doc, dict) or doc.get("schema") != "aib-persona-candidates/v1":
        return 5, {"why": "bad_schema"}
    body = {k: v for k, v in doc.items() if k != "content_hash"}
    if content_hash(body) != doc.get("content_hash"):
        return 4, {"why": "hash_mismatch"}
    if now_ms >= doc.get("expires_at_ms", 0):
        return 3, {"why": "expired", "expires_at_ms": doc.get("expires_at_ms")}
    return 0, {"why": "valid", "persona_code": doc.get("persona_code")}
