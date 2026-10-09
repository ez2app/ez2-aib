#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""E6.4 記憶金字塔(draft):L0-L3 分層路由+L1 席位記憶存取。

四層(the maintainer 拍板「最主要」;大綱 E6.4):

    L0 帳本(事實)      正本=src/ledger 事件帳。事實只有一種入口:append 入帳。
    L1 席位記憶          跟「席位」不跟 session:flt2 換了幾棒 AI,記憶都在。
                         本模組唯一真寫入的層(<root>/seats/<seat>.jsonl)。
    L2 AIB 系統記憶      repo 編年史(E6.2 產物)+教訓+ADR。寫入=repo 變更,
                         走 commit/審核,本模組只產「提案」,絕不代寫 docs/。
    L3 per-persona 分流  回第二大腦。席位人格綁定決定 route(E5.6 兩投影);
                         gateway 一扇門且 E5.5 前天花板未解鎖,本模組只產
                         write-plan(willWrite=false),絕不碰 gateway。

路由紀律(fail-closed,對齊 E5.6 §1.3:缺判定=最嚴格路徑):
  - scope 不認得 -> hold_for_review(不落任何層,升人審)
  - persona scope 但席位無人格綁定 -> hold_for_review(綁定是 route 的前提)
  - 本模組有寫入能力的只有 L1;其餘層一律回裁決物件讓正主執行。

席位記憶條目形狀(SEAT_MEMORY_SCHEMA 為正本;壞行讀取時進 skipped 不癱瘓):
    {"schema_version": "0.1.0-draft", "seat": "flt2", "kind": "lesson",
     "text": "...", "at_ms": 1786174000000, "source_refs": ["ev_..."]}

席位人格綁定檔(--bindings;E5.4 routeKey 登記完成前 route_key 只是宣告):
    {"schema_version": "0.1.0-draft",
     "bindings": {"flt2": {"persona_uid": "human-main", "route_key": "core.refine"}}}

L3 分流細則(track;E6.4 括號「E5.6 兩投影+gateway 一扇門」的機器化):
route 決定「進不進 L3」(席位綁定是前提);track 決定「進了 L3 走哪一軌」——
依 E5.6 §1.2 的 essenceItem.category 14 類封閉集分軌:
    user_persona_signal -> 風格軌(投影到 conv-distill staging 形狀,E5.1 通道;
                            content-free,route_key=persona.conversation 候 E5.4 登記)
    fact/decision/outcome/expert_knowledge/completed/pending/risk/quote/
    contradiction/unknown -> 知識軌(pending 走 core.inbox 候審;approved 走
                            core.refine,expert_knowledge approved 走
                            product.expert.output;unknown 永遠 pending,fail-closed)
    handoff        -> 特例:既有 core.handoff 通道
    customer_event -> 紅線:core.crm 不可 AI 寫,只產候審卡(hold_for_review)
    event          -> 雙投影:風格軌取 content-free 訊號+知識軌候審(正本在知識軌)
所有 track 裁決一律 will_write=false(gateway 一扇門+E5.5 天花板);
category 不在 14 類 -> hold_for_review(缺判定=最嚴格路徑)。

CLI(真實 exit code:0=成功,2=來源/參數錯誤):
  python3 src/memory/pyramid.py layers
  python3 src/memory/pyramid.py route --scope fact|seat|system|persona \
      [--seat flt2] [--bindings b.json]
  python3 src/memory/pyramid.py track --category fact \
      [--review-status pending|approved] [--seat flt2] [--bindings b.json]
  python3 src/memory/pyramid.py seat-remember --root DIR --seat flt2 \
      --kind lesson|preference|context --text "..." [--now-ms N] \
      [--source-ref ev_...]... [--emit-event]
  python3 src/memory/pyramid.py seat-recall --root DIR --seat flt2 \
      [--kind K] [--limit N]
  python3 src/memory/pyramid.py recall --root DIR --seat flt2 \
      [--chronicle DIR] [--recent 7] [--limit N]

recall=席位開工「入腦包」(commit 036da32 拍板文字的「入腦」面):
一次組裝 L1 席位記憶(全文)+L2 編年史「指標」(近 N 日檔名,唯讀,
不複述內容——內容正本在 chronicle 檔,複述=第二份真相)+L0 指回帳本。
chronicle 目錄缺=誠實標 missing,不炸(開工早於第一次蒸餾是合法狀態)。

反重造(E8.3):條目驗證複用 src/ledger/schema_validator.py;事件形狀對齊
schemas/event.schema.json 的 memory.checkpoint;不重造帳本、不重造 gateway。
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SRC / "ledger"))

from schema_validator import SchemaValidator, SchemaValidationError  # noqa: E402

PYRAMID_VERSION = "0.1.0-draft"
_SEAT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

SEAT_MEMORY_KINDS = ["lesson", "preference", "context"]

SEAT_MEMORY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "seat", "kind", "text", "at_ms"],
    "properties": {
        "schema_version": {"const": PYRAMID_VERSION},
        "seat": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{0,31}$"},
        "kind": {"enum": SEAT_MEMORY_KINDS},
        "text": {"type": "string", "minLength": 1, "maxLength": 4000},
        "at_ms": {"type": "integer", "minimum": 0},
        "source_refs": {
            "type": "array", "maxItems": 32,
            "items": {"type": "string", "pattern": "^ev_[0-9a-f]{64}$"},
        },
    },
}

BINDINGS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "bindings"],
    "properties": {
        "schema_version": {"const": PYRAMID_VERSION},
        "bindings": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "additionalProperties": False,
                "required": ["persona_uid", "route_key"],
                "properties": {
                    "persona_uid": {"type": "string", "minLength": 1,
                                    "maxLength": 128},
                    "route_key": {"type": "string", "minLength": 1,
                                  "maxLength": 128,
                                  "pattern": "^[a-z][a-z0-9._-]*$"},
                },
            },
        },
    },
}

# 金字塔正本:每層宣告真相在哪、誰可寫、本模組能做到哪一步
LAYERS = {
    "L0": {
        "name": {"zh-TW": "帳本(事實)", "en": "ledger (facts)"},
        "truth": "src/ledger append-only JSONL",
        "write_path": "ledger.append(draft)",
        "this_module": "refuses_to_write",
    },
    "L1": {
        "name": {"zh-TW": "席位記憶", "en": "seat memory"},
        "truth": "<root>/seats/<seat>.jsonl(跟席位不跟 session)",
        "write_path": "pyramid.py seat-remember",
        "this_module": "writes",
    },
    "L2": {
        "name": {"zh-TW": "AIB 系統記憶", "en": "AIB system memory"},
        "truth": "repo docs/memory/chronicle(E6.2)+教訓+ADR",
        "write_path": "repo 變更走 commit/審核",
        "this_module": "proposes_only",
    },
    "L3": {
        "name": {"zh-TW": "per-persona 分流", "en": "per-persona routing"},
        "truth": "第二大腦(gateway 一扇門;E5.5 前天花板)",
        "write_path": "gateway write-plan(willWrite=false)",
        "this_module": "plans_only",
    },
}

_SCOPE_TO_LAYER = {"fact": "L0", "seat": "L1", "system": "L2", "persona": "L3"}

# -- L3 分流細則(E5.6 §1.2 category 分流表的機器化)------------------------

STYLE_CATEGORY = "user_persona_signal"
KNOWLEDGE_CATEGORIES = [
    "fact", "decision", "outcome", "expert_knowledge", "completed",
    "pending", "risk", "quote", "contradiction", "unknown",
]
SPECIAL_CATEGORIES = ["handoff", "customer_event", "event"]
# 14 類封閉集 = 1 風格 + 10 知識 + 3 特例;新增類必須回 E5.6 設計稿走 ADR
ESSENCE_CATEGORIES = [STYLE_CATEGORY] + KNOWLEDGE_CATEGORIES + SPECIAL_CATEGORIES

_STYLE_TARGET = {
    "shape": "claude-conv-distill-staging.jsonl(E5.1 已驗證通道)",
    "route_key": "persona.conversation",
    "route_key_registered": False,  # E5.4 登記完成前只是宣告
}


def _knowledge_route_key(category, review_status):
    if category == "unknown":
        # E5.6 §1.3:分類不確定一律候審,approved 也不放行(缺判定=最嚴格路徑)
        return "core.inbox", ["unknown_stays_pending"]
    if review_status == "approved":
        if category == "expert_knowledge":
            return "product.expert.output", ["approved_expert_upgrade"]
        return "core.refine", ["approved_shared_knowledge"]
    return "core.inbox", ["pending_review_first"]


def track_route(category, review_status="pending", persona_uid=None):
    """L3 軌道裁決(純函數)。一律 will_write=false(gateway 一扇門+E5.5 天花板)。"""
    if category not in ESSENCE_CATEGORIES:
        return {"track": None, "action": "hold_for_review", "will_write": False,
                "reasons": ["unknown_category:%s" % category]}
    base = {"will_write": False, "persona_uid": persona_uid}
    if category == STYLE_CATEGORY:
        return dict(base, track="style", action="project_to_persona_staging",
                    target=dict(_STYLE_TARGET),
                    reasons=["content_free_signal_only",
                             "route_key_pending_E5.4"])
    if category == "handoff":
        return dict(base, track="knowledge", action="gateway_write_plan",
                    target={"route_key": "core.handoff"},
                    reasons=["existing_handoff_channel"])
    if category == "customer_event":
        return dict(base, track="knowledge", action="hold_for_review",
                    target={"route_key": "core.crm"},
                    reasons=["core.crm_not_ai_writable",
                             "red_line_review_card_only"])
    if category == "event":
        knowledge_key, knowledge_reasons = _knowledge_route_key(
            "fact", review_status)
        return dict(base, track="dual", action="dual_projection",
                    target={"style": dict(_STYLE_TARGET),
                            "knowledge": {"route_key": knowledge_key}},
                    reasons=["knowledge_holds_canonical_copy",
                             "style_gets_content_free_projection"]
                            + knowledge_reasons)
    route_key, reasons = _knowledge_route_key(category, review_status)
    return dict(base, track="knowledge", action="gateway_write_plan",
                target={"route_key": route_key}, reasons=reasons)


class PyramidError(Exception):
    """來源檔存在但不可信,或參數不合法 —— fail-closed。"""


def die(message):
    print("pyramid: %s" % message, file=sys.stderr)
    sys.exit(2)


def _validate(instance, schema, label):
    try:
        SchemaValidator(schema).validate(instance)
    except SchemaValidationError as err:
        raise PyramidError("%s rejected: %s" % (label, err))


def load_bindings(path):
    """綁定檔:缺檔=無綁定(誠實);存在但壞 raise(壞宣稱不可信)。"""
    if path is None:
        return {}
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError as err:
        raise PyramidError("bindings file is not valid JSON: %s" % err)
    _validate(data, BINDINGS_SCHEMA, "bindings file")
    return data["bindings"]


CEILING_REASON = "ceiling_until_E5.5_owner_adapter"


def _ceiling_state_module():
    """src/fleet_identity/ceiling_state —— 天花板狀態的單一判讀入口。

    延後匯入(比照 decision_inbox 的同慣例):匯不到就讓它炸,
    **天花板讀不到不得退成「當作可以寫」**。
    """
    path = str(_SRC / "fleet_identity")
    if path not in sys.path:
        sys.path.insert(0, path)
    return __import__("ceiling_state")


def _ceiling_allows_write():
    return bool(_ceiling_state_module().ceiling_fields()["formal_eligible"])


def CEILING_SITE(env=None, source=None, adapter=None):
    """判準 H2 的掛鉤:本處**當下宣告的**天花板狀態。有這個屬性代表本處已翻。

    ★ 與 route() 走同一個 ceiling_fields,所以「入口讀到的」與「裁決裡真的寫的」
    不會是兩個答案。
    """
    return _ceiling_state_module().ceiling_fields(
        env=env, source=source, adapter=adapter)


def route(scope, seat=None, bindings=None):
    """記憶項 scope -> 分層裁決(純函數)。不認得=hold_for_review。"""
    bindings = bindings or {}
    layer = _SCOPE_TO_LAYER.get(scope)
    if layer is None:
        return {"layer": None, "action": "hold_for_review",
                "reasons": ["unknown_scope:%s" % scope]}
    if layer == "L0":
        return {"layer": "L0", "action": "append_to_ledger",
                "target": LAYERS["L0"]["truth"],
                "reasons": ["facts_have_one_door"]}
    if layer == "L1":
        if not seat or not _SEAT_RE.match(seat):
            return {"layer": "L1", "action": "hold_for_review",
                    "reasons": ["seat_required_for_seat_scope"]}
        return {"layer": "L1", "action": "write_seat_file",
                "target": "seats/%s.jsonl" % seat,
                "reasons": ["seat_memory_follows_seat_not_session"]}
    if layer == "L2":
        return {"layer": "L2", "action": "propose_repo_change",
                "target": "docs/memory/(經 commit/審核,本模組不代寫)",
                "reasons": ["system_memory_is_repo_governed"]}
    # L3:席位人格綁定決定 route;無綁定=最嚴格路徑
    if not seat or seat not in bindings:
        return {"layer": "L3", "action": "hold_for_review",
                "reasons": ["persona_scope_needs_seat_binding"
                            + ("" if seat else ":no_seat")]}
    bound = bindings[seat]
    # ★ PKG-CEILING-FLIP 乙件(判準 qa3/criteria-ceiling-flip-0827 更正六,範圍裁定 (甲)):
    #   `will_write` 與那條 ceiling reason 由**寫死**改成向 fleet_identity/ceiling_state 現查導出。
    #   owner source 未設定時導出的結果與舊值逐字相同(False + 帶 ceiling reason)——
    #   翻的是「從寫死變成可測」,不是「從否變成是」。
    #   ★ 本批**只翻這一處**;`track_route` 明寫未翻,由 B5 釘子臂釘住(資料面與散文面各一)。
    eligible = _ceiling_allows_write()
    reasons = ["gateway_is_the_only_door"]
    if not eligible:
        reasons.append(CEILING_REASON)
    return {"layer": "L3", "action": "gateway_write_plan",
            "target": {"route_key": bound["route_key"],
                       "persona_uid": bound["persona_uid"]},
            "will_write": eligible,
            "reasons": reasons}


def seat_file(root, seat):
    return Path(root) / "seats" / ("%s.jsonl" % seat)


def seat_remember(root, seat, kind, text, at_ms, source_refs=None):
    """L1 唯一寫入點:schema 驗過才落盤(append 一行 canonical JSON)。"""
    entry = {"schema_version": PYRAMID_VERSION, "seat": seat, "kind": kind,
             "text": text, "at_ms": at_ms}
    if source_refs:
        entry["source_refs"] = list(source_refs)
    _validate(entry, SEAT_MEMORY_SCHEMA, "seat memory entry")
    target = seat_file(root, seat)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")) + "\n")
    return entry


def seat_recall(root, seat, kind=None, limit=None):
    """讀席位記憶:壞行/不合 schema 行進 skipped(回饋不閘門),不癱瘓。"""
    target = seat_file(root, seat)
    entries, skipped = [], []
    if target.is_file():
        with open(target, "r", encoding="utf-8") as fh:
            for index, line in enumerate(fh):
                line = line.rstrip("\n")
                if not line:
                    skipped.append({"index": index, "reason": "blank_line"})
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    skipped.append({"index": index, "reason": "unparseable"})
                    continue
                try:
                    _validate(entry, SEAT_MEMORY_SCHEMA, "entry")
                except PyramidError as err:
                    skipped.append({"index": index,
                                    "reason": "schema_invalid:%s" % err})
                    continue
                if kind is None or entry["kind"] == kind:
                    entries.append(entry)
    if limit is not None:
        entries = entries[-limit:]
    return {"seat": seat, "entries": entries, "skipped": skipped}


_CHRONICLE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


def chronicle_pointers(chronicle_dir, recent):
    """L2 編年史指標(唯讀):近 recent 日的檔名清單,不讀內容。
    目錄缺=誠實標 missing(開工早於第一次蒸餾是合法狀態)。"""
    if chronicle_dir is None:
        return {"chronicle_dir": None, "missing": True, "dates": []}
    directory = Path(chronicle_dir)
    if not directory.is_dir():
        return {"chronicle_dir": str(chronicle_dir), "missing": True, "dates": []}
    dates = sorted(p.name[:-3] for p in directory.iterdir()
                   if p.is_file() and _CHRONICLE_RE.match(p.name))
    return {"chronicle_dir": str(chronicle_dir), "missing": False,
            "dates": dates[-recent:] if recent else dates}


def recall(root, seat, chronicle_dir=None, recent=7, kind=None, limit=None):
    """席位開工入腦包:L1 全文+L2 指標+L0 指回帳本(純讀取組裝)。"""
    return {
        "seat": seat,
        "layers": {
            "L0": {"truth": LAYERS["L0"]["truth"],
                   "note": "事實查詢走 projections/verify_chain,不在此複述"},
            "L1": seat_recall(root, seat, kind=kind, limit=limit),
            "L2": chronicle_pointers(chronicle_dir, recent),
            "L3": {"note": "分流是寫方向(track);入腦包不回讀第二大腦"},
        },
    }


def checkpoint_event_draft(entry, sig, ai_id):
    """席位記憶寫入 -> memory.checkpoint 帳本草稿(可直接餵 ledger.append)。"""
    return {
        "kind": "memory.checkpoint",
        "actor": {"ai_id": ai_id, "sig": sig, "kind": "system", "engine": "AIB"},
        "body": {
            "summary": {
                "zh-TW": "席位記憶入庫:%s/%s" % (entry["seat"], entry["kind"]),
                "en": "seat memory recorded: %s/%s" % (entry["seat"],
                                                       entry["kind"]),
            },
            "subject": {"kind": "memory", "id": "seat:%s" % entry["seat"]},
        },
    }


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="pyramid",
        description="Ez2 AIB 記憶金字塔:L0-L3 分層路由+席位記憶(E6.4)")
    parser.add_argument("command",
                        choices=["layers", "route", "track",
                                 "seat-remember", "seat-recall", "recall"])
    parser.add_argument("--scope", help="route 用:fact|seat|system|persona")
    parser.add_argument("--category", help="track 用:essence category(14 類)")
    parser.add_argument("--review-status", choices=["pending", "approved"],
                        default="pending", help="track 用:條目審核狀態")
    parser.add_argument("--seat", help="席位名(如 flt2;跟席位不跟 session)")
    parser.add_argument("--bindings", help="席位人格綁定檔路徑")
    parser.add_argument("--root", help="席位記憶根目錄")
    parser.add_argument("--kind", help="條目種類 lesson|preference|context")
    parser.add_argument("--text", help="seat-remember 用:條目內容")
    parser.add_argument("--now-ms", type=int, default=None,
                        help="條目時刻(測試注入;預設牆鐘)")
    parser.add_argument("--source-ref", action="append", default=[],
                        help="來源事件 ev_...(可重複)")
    parser.add_argument("--limit", type=int, default=None,
                        help="seat-recall/recall 用:只取最新 N 筆")
    parser.add_argument("--chronicle", help="recall 用:編年史目錄(E6.2 產物)")
    parser.add_argument("--recent", type=int, default=7,
                        help="recall 用:編年史指標取近 N 日(預設 7)")
    parser.add_argument("--emit-event", action="store_true",
                        help="seat-remember 後輸出 memory.checkpoint 事件草稿")
    parser.add_argument("--sig", default="pyramid@0e64/AIB",
                        help="事件草稿 actor 顯示簽名")
    parser.add_argument("--ai-id", default="aib:localhost:memory-pyramid",
                        help="事件草稿 actor 完整身份")
    args = parser.parse_args(argv)

    if args.command == "layers":
        sys.stdout.write(_dump({"version": PYRAMID_VERSION, "layers": LAYERS}))
        return 0

    if args.command == "route":
        if not args.scope:
            die("route 需要 --scope")
        try:
            bindings = load_bindings(args.bindings)
        except PyramidError as err:
            die(str(err))
        sys.stdout.write(_dump(route(args.scope, seat=args.seat,
                                     bindings=bindings)))
        return 0

    if args.command == "track":
        if not args.category:
            die("track 需要 --category")
        try:
            bindings = load_bindings(args.bindings)
        except PyramidError as err:
            die(str(err))
        persona_uid = None
        if args.seat and args.seat in bindings:
            persona_uid = bindings[args.seat]["persona_uid"]
        sys.stdout.write(_dump(track_route(args.category,
                                           review_status=args.review_status,
                                           persona_uid=persona_uid)))
        return 0

    # seat-remember / seat-recall 共同前置
    if not args.root:
        die("%s 需要 --root" % args.command)
    if not args.seat or not _SEAT_RE.match(args.seat):
        die("%s 需要合法 --seat(%s)" % (args.command, _SEAT_RE.pattern))

    if args.command == "seat-remember":
        if not args.kind or not args.text:
            die("seat-remember 需要 --kind 與 --text")
        now_ms = args.now_ms if args.now_ms is not None else int(time.time() * 1000)
        try:
            entry = seat_remember(args.root, args.seat, args.kind, args.text,
                                  now_ms, source_refs=args.source_ref)
        except PyramidError as err:
            die(str(err))
        if args.emit_event:
            sys.stdout.write(_dump(checkpoint_event_draft(entry, args.sig,
                                                          args.ai_id)))
        else:
            sys.stdout.write(_dump(entry))
        return 0

    if args.command == "recall":
        sys.stdout.write(_dump(recall(args.root, args.seat,
                                      chronicle_dir=args.chronicle,
                                      recent=args.recent, kind=args.kind,
                                      limit=args.limit)))
        return 0

    result = seat_recall(args.root, args.seat, kind=args.kind, limit=args.limit)
    sys.stdout.write(_dump(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
