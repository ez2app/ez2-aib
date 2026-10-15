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
from wrappers import rt

FRESH_DAYS = 14
B_EMPTY_LINE = "B:無(首煉輪,依計數法全部進 C)"
F_TEMPLATE = ("本輪提取無候選。已讀來源:{sources};watchlist 新增 {n} 筆;"
              "原因:{reason}")
F_REASONS = ("證據不足", "全部單一事件", "全部狀態未定")
# ★ B_EMPTY_LINE / F_TEMPLATE / F_REASONS 是**寫進候選包的資料**(進 content_hash、F_REASONS 是 maker 傳進來的列舉值),
#   不隨語系變:同一份 sections 在任何語系產出的包要逐 byte 相同。只有 render_md 印給人看的那一份照語系翻
#   (F 節由 _F_RE 從包裡那句拆回 sources / n / reason 再照語系重組;拆不開就原樣印)。
_F_RE = None


def _f_display(text):
    global _F_RE
    import re
    if _F_RE is None:
        pat = re.escape(F_TEMPLATE)
        for name, rx in (("sources", "(?P<sources>.*)"), ("n", r"(?P<n>\d+)"), ("reason", "(?P<reason>.*)")):
            pat = pat.replace(re.escape("{%s}" % name), rx)
        _F_RE = re.compile("^%s$" % pat, re.S)
    m = _F_RE.match(text or "")
    if not m:
        return text
    reason = m.group("reason")
    if reason in F_REASONS:
        reason = rt("report.reason.%d" % (F_REASONS.index(reason) + 1))
    srcs = [rt("report.sources_unlisted") if x == "(未列)" else x for x in m.group("sources").split("、")]
    return rt("report.md_f_text", sources=rt("root.list_sep").join(srcs), n=m.group("n"), reason=reason)


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
        raise ReportError(rt("report.sections_not_object"))
    retire_review = (sections.get("retire_review") or "").strip()
    if not retire_review:
        raise ReportError(rt("report.retire_required"))
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
        raise ReportError(rt("report.candidates_incomplete",
                             detail=json.dumps(invalid, ensure_ascii=False)[:500]))
    for c in b_list:
        if c.get("anchor_grade") != "machine":
            raise ReportError(rt("report.descriptive_not_b"))
    for i, c in enumerate(c_list):
        rc = c.get("watchlist_add_rc")
        if not isinstance(rc, int) or isinstance(rc, bool):
            raise ReportError(rt("report.c_missing_rc", n=i + 1))
    if self_round:
        mistakes = sections.get("self_mistakes") or []
        bad = [m for m in mistakes
               if not (isinstance(m, dict) and (m.get("what") or "").strip()
                       and gates.pointer_kind(m.get("pointer")))]
        if not mistakes or bad:
            raise ReportError(rt("report.self_mistakes_required"))
    empty_handed = not cands
    f_section = None
    if empty_handed:
        reason = sections.get("empty_reason")
        if reason not in F_REASONS:
            raise ReportError(rt("report.empty_reason_required", reasons=str(F_REASONS)))
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
         rt("report.md_title", code=doc["persona_code"],
            kind=rt("report.kind_self") if doc["self_round"] else rt("report.kind_extract")), ""]
    none = rt("report.md_none")
    L += [rt("report.md_a"), rt("report.md_retire", text=doc["A_keep"]["retire_review"])]
    L += ["- %s" % x for x in doc["A_keep"]["kept"]]
    L += ["", rt("report.md_b")]
    if doc["B_candidates"]:
        L += ["- %s → %s" % (c["candidate_uid"], c["target_field"]) for c in doc["B_candidates"]]
    else:
        L += [rt("report.md_b_empty") if doc["B_note"] == B_EMPTY_LINE else doc["B_note"]]
    L += ["", rt("report.md_c")]
    L += [rt("report.md_c_row", uid=c["candidate_uid"], rc="%d" % c["watchlist_add_rc"])
          for c in doc["C_watchlist"]] or [none]
    L += ["", rt("report.md_d")] + (["- %s" % x for x in doc["D_diff"]] or [none])
    L += ["", "## E unknown"] + (["- %s" % x for x in doc["E_unknown"]] or [none])
    if doc["F_empty_handed"]:
        L += ["", rt("report.md_f"), _f_display(doc["F_empty_handed"])]
    if doc["self_round"]:
        L += ["", rt("report.md_self")]
        L += ["- %s — %s" % (m["what"], m["pointer"]) for m in doc["self_mistakes"]]
    L += ["", rt("report.md_stop")]
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
