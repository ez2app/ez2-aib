#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""EH.4(2) 開席比對產候選 —— 機器只**建議**,綁定由人按;候選帶理由與分數,落狀態根。

設計稿 `docs/reports/DESIGN-auto-persona-binding-0821.md` §四 / §8.4 / §九.2。
a seat 08-22 14:27 裁:預設「建議」不自動綁(綁定決定那一席往後產出寫進 vault 哪個
資料夾,不可逆);14:35 追加兩條硬律:**候選不得只靠 role 猜;無 cwd 就無候選(與索引同律)**。

★ 什麼算候選、什麼只是提示(這一條是本檔的主體):
  候選(candidates)—— 必須有**這個專案**的證據:
    1. 索引命中 `(project, role)`(persona_index §8.3):score 1.0,reason index_hit。
    2. 索引 conflicts 裡同鍵的每一組(同專案同 role 曾綁過不同的卡):score 0.5、
       `needs_human: true`——那是「人要裁」,不是「機器有答案」,列出來是為了不讓人重查。
  提示(hints)—— **只靠 role** 的東西,依 root 14:35 不得當候選,但值得讓按鍵的人看見:
    a. 卡的 `code` 逐字等於 role(§四-2 字面證據);
    b. 母體 `by_role`(跨專案、含無 cwd 的舊席位)裡該 role 被綁到的卡,計數 ≥ MIN_POPULATION
       (§四-3 母體下限;常數在此,測試釘得住)。
    提示不帶分數、不帶 route_key 建議(§四-4 route 不猜),欄位 `not_a_candidate_reason` 逐字
    寫「role_only_basis」——讓三天後讀這個檔的人分得出「機器有專案證據」與「機器只看到 role」。

★ 永遠不綁:輸出的 `decision` 恆為 "suggest"、`auto_bind` 恆為 False(常數,不是參數)。
  本檔的 suggest 是純讀端：不寫 bindings / agents / 席位檔 / identity /
  vault / ledger，候選文件只從 stdout 回傳。

★ 沒有候選時 `next` = "create_plan"(第 (3) 件:只產 brain_write_plan 計畫不 apply,人核可才落);
  `hints` 仍照給——人在決定「新建」前應該看得到「別的專案裡這個 role 綁過什麼」。

用法:
    persona_candidates.py suggest --project <cwd> --role <role> [--seat <五碼>] [--state-root R] [--dry-run] [--json]
退出碼:0 唯一候選 · 1 衝突候裁或無候選(含 hints 與 next) · 2 輸入不合/無 cwd。
"""
import argparse
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
for _p in ("identity", "memory", "cockpit"):
    if str(_SRC / _p) not in sys.path:
        sys.path.insert(0, str(_SRC / _p))

import persona_index as pi            # noqa: E402
import binding_projection as bp       # noqa: E402

SCHEMA_VERSION = "aib-persona-candidates/v1"
DECISION = "suggest"          # 恆定:機器只建議(§九.2,root 08-22 14:27 裁)
AUTO_BIND = False             # 恆定:不自動綁
MIN_POPULATION = 2            # §四-3:母體下限(一次隨手決定不成全艦規則);測試釘這個值
SCORE_INDEX_HIT = 1.0
SCORE_CONFLICT = 0.5
NOT_CANDIDATE_ROLE_ONLY = "role_only_basis"
NEXT_CREATE_PLAN = "create_plan"
NEXT_HUMAN_PICK = "human_pick"
REFUSE_NO_CWD = "seat_has_no_cwd"


class CandidateError(Exception):
    """輸入不合(fail-closed,零寫入)。"""


def load_card_codes(cards_path):
    """cards.json 原檔的 `code` 欄(投影那一支的正規化表會丟掉它,所以這裡**只補 code**,
    形狀驗證仍交給 load_card_registry)。"""
    try:
        with open(str(cards_path), encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as err:
        raise CandidateError("cards.json 讀不動:%s" % err)
    cards = raw.get("cards") if isinstance(raw, dict) else None
    if not isinstance(cards, dict):
        raise CandidateError("cards.json 形狀不合:缺 cards 物件")
    return {uid: (e.get("code") if isinstance(e, dict) else None) for uid, e in cards.items()}


# ---- 核心(純函數) -----------------------------------------------------------

def suggest(index_doc, cards, card_codes, project, role, seat=None, now_ms=None):
    """(索引文件, 正規化卡表, 卡 code 表, project, role) → 候選文件。**純函數**。

    無 project(=席位無 cwd)→ 回 refused 文件(零候選、零提示):與索引同律,
    不退回 role 當鍵——否則第二個專案的同 role 席會拿到第一個專案的卡當候選,且不報錯。
    """
    if not role or not isinstance(role, str):
        raise CandidateError("缺 role")
    base = {"schema_version": SCHEMA_VERSION, "project": project or None, "role": role,
            "seat": seat, "generated_wall_ms": int(now_ms) if now_ms is not None else None,
            "decision": DECISION, "auto_bind": AUTO_BIND}
    if not project or not isinstance(project, str):
        return dict(base, refused=REFUSE_NO_CWD, candidates=[], hints=[], next=None,
                    note="無 cwd 就無候選(root 08-22 14:35;與 persona_index 同律)")
    key = pi.index_key(project, role)
    cands = []
    hit = (index_doc.get("index") or {}).get(key)
    if hit:
        cands.append({"persona_uid": hit["persona_uid"], "route_key": hit.get("route_key"),
                      "display_name": hit.get("display_name"), "score": SCORE_INDEX_HIT,
                      "needs_human": False, "reasons": ["index_hit"],
                      "evidence": {"index_key": key, "seats": (hit.get("evidence") or {}).get("seats", []),
                                   "binding_key_kinds": (hit.get("evidence") or {}).get("binding_key_kinds", [])}})
    conflict = (index_doc.get("conflicts") or {}).get(key)
    if conflict:
        for c in conflict.get("candidates", []):
            card = cards.get(c["persona_uid"]) or {}
            cands.append({"persona_uid": c["persona_uid"], "route_key": c.get("route_key"),
                          "display_name": card.get("display_name"), "score": SCORE_CONFLICT,
                          "needs_human": True, "reasons": ["conflict_same_project_role"],
                          "evidence": {"index_key": key, "seats": c.get("seats", [])}})
    cands.sort(key=lambda c: (-c["score"], c["persona_uid"], c["route_key"] or ""))

    hints = []
    for uid, code in sorted(card_codes.items()):
        if code and code == role:
            card = cards.get(uid) or {}
            hints.append({"persona_uid": uid, "display_name": card.get("display_name"),
                          "basis": "card_code_equals_role", "code": code,
                          "card_default_route_key": card.get("route_key"),   # 只展示,不當建議(§四-4)
                          "not_a_candidate_reason": NOT_CANDIDATE_ROLE_ONLY})
    pop = (index_doc.get("by_role") or {}).get(role) or []
    total = sum(int(e.get("count") or 0) for e in pop)
    for e in pop:
        n = int(e.get("count") or 0)
        if n < MIN_POPULATION:
            continue
        card = cards.get(e["persona_uid"]) or {}
        hints.append({"persona_uid": e["persona_uid"], "display_name": card.get("display_name"),
                      "basis": "role_population", "count": n, "total": total,
                      "route_key_seen": e.get("route_key"),                 # 別人用過的,不是建議
                      "seats": e.get("seats", []),
                      "not_a_candidate_reason": NOT_CANDIDATE_ROLE_ONLY})
    nxt = NEXT_HUMAN_PICK if cands else NEXT_CREATE_PLAN
    return dict(base, index_key=key, candidates=cands, hints=hints, next=nxt,
                basis={"min_population": MIN_POPULATION,
                       "index_generated_wall_ms": index_doc.get("generated_wall_ms")})


def run(project, role, seat=None, state_root=None, index_path=None, cards_path=None,
        now_ms=None, write=True):
    """讀索引與卡表 → suggest。``write`` 只為 API 相容保留，suggest 恆零寫。"""
    root, _how = bp.resolve_state_root(state_root)
    idx_p = Path(index_path) if index_path else root.joinpath(*pi.INDEX_REL)
    cards_p = Path(cards_path) if cards_path else root.joinpath(*pi.CARDS_REL)
    if not idx_p.is_file():
        raise CandidateError("索引不存在:%s(先 persona_index.py build)" % idx_p)
    try:
        with open(str(idx_p), encoding="utf-8") as fh:
            index_doc = json.load(fh)
    except (OSError, ValueError) as err:
        raise CandidateError("索引讀不動:%s" % err)
    if index_doc.get("schema_version") != pi.SCHEMA_VERSION:
        raise CandidateError("索引 schema_version 不合:%r" % index_doc.get("schema_version"))
    cards = pi.load_cards(cards_p)
    codes = load_card_codes(cards_p)
    doc = suggest(index_doc, cards, codes, project, role, seat=seat, now_ms=now_ms)
    if doc.get("refused"):
        raise CandidateError("%s:席位沒有 cwd,不產候選(零寫入)" % doc["refused"])
    return doc, None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="persona_candidates.py", description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=["suggest"])
    ap.add_argument("--project", help="席位檔的 cwd;沒有就沒有候選")
    ap.add_argument("--role", required=True)
    ap.add_argument("--seat", help="五碼,只記錄在文件裡")
    ap.add_argument("--state-root"); ap.add_argument("--index"); ap.add_argument("--cards")
    ap.add_argument("--now-ms", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        doc, out = run(args.project, args.role, seat=args.seat, state_root=args.state_root,
                       index_path=args.index, cards_path=args.cards, now_ms=args.now_ms,
                       write=not args.dry_run)
    except (CandidateError, pi.IndexError_) as err:
        sys.stderr.write("persona_candidates: %s\n" % err)
        return 2
    if args.json:
        print(json.dumps(dict(doc, out=str(out) if out else None), ensure_ascii=False, sort_keys=True))
    else:
        print("candidates=%d hints=%d next=%s decision=%s auto_bind=%s%s"
              % (len(doc["candidates"]), len(doc["hints"]), doc["next"], doc["decision"],
                 doc["auto_bind"], (" -> %s" % out) if out else " (read-only)"))
        for c in doc["candidates"]:
            print("  cand %.2f %s %s route=%s %s" % (c["score"], c["persona_uid"], c.get("display_name"),
                                                   c.get("route_key"), ",".join(c["reasons"])))
        for h in doc["hints"]:
            print("  hint %s %s (%s; %s)" % (h["persona_uid"], h.get("display_name"), h["basis"],
                                            h["not_a_candidate_reason"]))
    if any(candidate.get("needs_human") is True for candidate in doc["candidates"]):
        return 1
    return 0 if doc["candidates"] else 1


if __name__ == "__main__":
    sys.exit(main())
