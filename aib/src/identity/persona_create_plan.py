#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""EH.4(3) 沒命中不直接建卡 —— 只產一張「新建人格卡」的計畫,人核可才落 vault。

設計稿 `docs/reports/DESIGN-auto-persona-binding-0821.md` §8.4(四條守則)/ §8.5 / §九.1。
a seat 08-22 14:27 裁:§8.4.4 **不直接建**(只 plan 不 apply,人核可才落 vault),所以 §8.5
「自動寫 vault 與 §七 vault 不可亂動牴觸」**因此不發生**;§九.1 新卡內容由誰寫候 the maintainer,
本檔只建**骨架**(frontmatter 標記 + 空白正文),不寫任何角色內容。

★ 四條守則怎麼落(§8.4):
  1. 自帶標記:frontmatter `source: created` + `created_by: @<五碼>` + `created_at`;計畫本身同欄。
     ★ 機器生的卡一定比人寫的薄,沒有標記三天後分不出;顯示面已備好 `~` 前綴
     (`DESIGN-SPEC-persona-source-three-states.md`)。
  2. 冪等:先查索引(命中→不產計畫,回 already_indexed;conflicts→不產,回 conflict_needs_human);
     同鍵已有計畫檔 → **回既有那張**(uid 不重發),不產第二張。
  3. route_key 撞檢:預設 **不填** route(§四-4 route 不猜,留給人);若呼叫端給了 `--route-key`,
     先過契約現查(`route_register.check_against_contract`),再掃索引+bindings 的 route 全集,
     撞到既有的就拒(兩席寫同一個 vault 資料夾是內容互相覆蓋)。
  4. 不直接建:`executes_now: False`;本檔唯一會寫的是
     `<state_root>/identity/persona-create-plans/<key-sha12>.json`(`atomic_json.write_json`),
     落點先過 `brain_write_plan.refuse_vault_output`(計畫檔不准落在 vault 裡)。
     真的落 vault 要三件都到:E5.3 人核可收據(write_receipt)、E5.5 owner adapter、
     交接單 §七 vault 那一格由 the maintainer 明文修訂——三件都不在本檔。

★ 計畫裡附一筆 `brain_write_plan_item`(brain_write_plan.build_plan 吃的 item 形狀:
  category=expert_knowledge、review_status=pending、privacy_class=internal、title/text=骨架),
  讓核可之後的那一段不必重想形狀;它現在 pending、無收據,build_plan 會在閘二/閘三把它擋下
  ——那是對的,不是缺陷。

用法:
    persona_create_plan.py plan --project <cwd> --role <role> --seat <五碼> [--route-key K]
                                 [--state-root R] [--contract SPEC] [--dry-run] [--json]
退出碼:0 有計畫(新建或既有) · 1 不該建(已索引 / 衝突候裁) · 2 輸入不合或撞檢不過(零寫入)
"""
import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
for _p in ("identity", "memory", "cockpit", "persona_route"):
    if str(_SRC / _p) not in sys.path:
        sys.path.insert(0, str(_SRC / _p))

import persona_index as pi                                  # noqa: E402
import binding_projection as bp                             # noqa: E402
import card_registry as cr                                  # noqa: E402
from atomic_json import write_json                          # noqa: E402
from route_register import check_against_contract, load_contract  # noqa: E402
from brain_write_plan import refuse_vault_output, WritePlanError  # noqa: E402

PLAN_RECORD = "persona-create-plan-v1"
PLANS_REL = ("identity", "persona-create-plans")
SOURCE_CREATED = "created"
CARD_FILE = cr.CARD_FILE                   # 角色.md
LIBRARY_ROUTE_KEY = cr.LIBRARY_ROUTE_KEY   # product.expert.agent_library
EXECUTES_NOW = False                       # 恆定:只 plan 不 apply
ITEM_CATEGORY = "expert_knowledge"
ITEM_REVIEW_STATUS = "pending"             # 人核可前就是 pending;build_plan 閘二會擋,那是對的
ITEM_PRIVACY = "internal"
UID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"   # card_registry.UID_RE [0-9a-z]{6}
REFUSE_ALREADY_INDEXED = "already_indexed"
REFUSE_CONFLICT = "conflict_needs_human"
REFUSE_NO_CWD = "seat_has_no_cwd"
NEEDS_HUMAN = ("dir_name", "body", "route_key")   # §九.1:骨架以外全部由人補
TZ_TAIPEI = timezone(timedelta(hours=8))


class CreatePlanError(Exception):
    """輸入不合 / 撞檢不過(fail-closed,零寫入)。"""


class CreatePlanRefused(Exception):
    """不該建(已索引或衝突候裁)。"""


# ---- 小工具 -----------------------------------------------------------------

def plan_path(state_root, project, role):
    key = pi.index_key(project, role)
    return Path(state_root).joinpath(*PLANS_REL) / ("%s.json" % hashlib.sha256(key.encode("utf-8")).hexdigest()[:12])


def new_uid(taken, rng=secrets.choice, tries=64):
    """6 碼 [0-9a-z],避開既有 uid(cards.json + 既有計畫)。避不開=錯,不硬發。"""
    for _ in range(tries):
        uid = "".join(rng(UID_ALPHABET) for _ in range(6))
        if uid not in taken:
            return uid
    raise CreatePlanError("uid 連續 %d 次撞號,不硬發" % tries)


def proposed_dir_name(project, role=None):
    """角色庫的目錄名=display_name 的來源(card_registry.display_name_of)。這裡只給**建議**,
    列在 needs_human 裡。

    ★ 08-22 23:5x 大綱 v1.29 §二十六之七 由 the maintainer 的使用場景定案:**目錄名 = `<專案>`,不含 role**。
      理由(場景原話推得):同一類開發/專案中每個 AI 開場讀**同一份人格**,role 只決定顯示與綁定,
      不決定資料夾——`<role>-<project>` 會讓同專案的四席各自長出一份人格,正是場景要避免的。
      這一格原本是 a944ce6 的 dry-run 輸出**默默定案**的(pm 二方審 §二 抓出來掛「候 the maintainer」),
      至此由裁定關閉;`role` 參數保留只為呼叫相容,**不參與命名**。

    ★ 名字過不了既有那兩道(`card_registry.has_unsafe_chars` = 控制字元/命令替換/引號類,
      與 `DISPLAY_NAME_MAX` = 24)就回 `None`,**不退回 role**:`dir_name` 本來就在
      `NEEDS_HUMAN` 裡,回 None 讓 `would_write_rel` 一併變 None——「機器算不出來、等人填」
      與「機器算出一個 role 名」在下游長得完全不同,後者會讓人以為那是決定過的。
      ★ 這兩道是**沿用**的,不是本次新增:`:` `*` 這類字元兩版都判為安全,別把本函式讀成路徑消毒。
    """
    base = os.path.basename((project or "").rstrip("/"))
    if not base or cr.has_unsafe_chars(base) or len(base) > cr.DISPLAY_NAME_MAX:
        return None
    return base


def skeleton_markdown(uid, role, seat, created_ms):
    """骨架:frontmatter 標記 + 空正文。**不寫內容**(§九.1 候 the maintainer)。"""
    dt = datetime.fromtimestamp(created_ms / 1000.0, tz=TZ_TAIPEI)
    return (
        "---\n"
        "uid: \"%s\"\n"
        "code: %s\n"
        "source: %s\n"
        "created_by: \"@%s\"\n"
        "created_at: %s\n"
        "updated: %s\n"
        "---\n"
        "# %s(機器新建的骨架 —— 內容候人補)\n\n"
        "> 本卡由 EH.4 自動新建流程產生,frontmatter 的 source 標記為機器新建。角色的判準與邊界**由人寫**;\n"
        "> 補完後把 `source` 改成 `human`(DESIGN-SPEC-persona-source-three-states §五)。\n"
        % (uid, role, SOURCE_CREATED, seat, dt.isoformat(timespec="seconds"),
           dt.strftime("%Y-%m-%d %H:%M"), role)
    )


def route_keys_in_use(index_doc, bindings):
    """既有 route_key 全集(§8.4.3 撞檢母體):索引條目 + bindings.json。"""
    used = set()
    for e in (index_doc.get("index") or {}).values():
        if e.get("route_key"):
            used.add(e["route_key"])
    for b in bindings.values():
        if b.get("route_key"):
            used.add(b["route_key"])
    return used


# ---- 核心(純函數) -----------------------------------------------------------

def make_plan(index_doc, cards, bindings, project, role, seat, route_key=None, contract=None,
              existing=None, now_ms=None, uid_rng=secrets.choice):
    """→ 計畫 dict。**純函數**(落檔在 run)。冪等:`existing` 給既有計畫就原樣回它。

    拒絕順序:無 cwd → 已索引 → 衝突 → 撞檢。每一條都在任何 uid 發出之前。
    """
    if not role or not isinstance(role, str):
        raise CreatePlanError("缺 role")
    if not seat or not isinstance(seat, str):
        raise CreatePlanError("缺 seat(五碼;created_by 要寫誰建的)")
    if not project or not isinstance(project, str):
        raise CreatePlanError("%s:席位沒有 cwd,不產計畫(與索引/候選同律)" % REFUSE_NO_CWD)
    key = pi.index_key(project, role)
    if key in (index_doc.get("index") or {}):
        raise CreatePlanRefused("%s:%s 已在索引裡,不新建" % (REFUSE_ALREADY_INDEXED, key))
    if key in (index_doc.get("conflicts") or {}):
        raise CreatePlanRefused("%s:%s 在索引 conflicts 裡,先讓人裁" % (REFUSE_CONFLICT, key))
    if existing is not None:
        if existing.get("record") == PLAN_RECORD and existing.get("index_key") == key:
            return dict(existing, idempotent_hit=True)
        raise CreatePlanError("同鍵既有計畫檔形狀不合(record=%r key=%r),不覆寫"
                              % (existing.get("record"), existing.get("index_key")))
    # §8.4.3 撞檢(只在呼叫端指定 route 時;預設不填=不猜)
    route_check = {"route_key": route_key, "checked": False}
    if route_key is not None:
        ok, code, detail = check_against_contract(route_key, contract)
        route_check.update(checked=True, contract_code=code)
        if not ok:
            raise CreatePlanError("route_key %s 過不了契約現查(%s):%s" % (route_key, code, detail))
        used = route_keys_in_use(index_doc, bindings)
        if route_key in used:
            raise CreatePlanError("route_key_collision:%s 已被既有綁定/索引使用——兩席寫同一個資料夾"
                                  "是內容互相覆蓋,不是顯示問題" % route_key)
        route_check["collision"] = False
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    taken = set(cards.keys())
    uid = new_uid(taken, rng=uid_rng)
    md = skeleton_markdown(uid, role, seat, now_ms)
    library_folder = None
    try:
        library_folder = cr.library_folder_of(contract) if contract else None
    except cr.CardRegistryError:
        library_folder = None
    dir_name = proposed_dir_name(project)          # §二十六之七:`<專案>`,不含 role
    item = {
        "item_uid": "persona-create:%s" % uid,
        "category": ITEM_CATEGORY,
        "review_status": ITEM_REVIEW_STATUS,
        "privacy_class": ITEM_PRIVACY,
        "title": "%s(%s)" % (role, dir_name or "資料夾名待人填"),
        "text": md,
        "content_sha256": hashlib.sha256(md.encode("utf-8")).hexdigest(),
    }
    return {
        "record": PLAN_RECORD,
        "index_key": key, "project": project, "role": role,
        "persona_uid": uid,
        "source": SOURCE_CREATED, "created_by": "@%s" % seat, "created_at_ms": now_ms,
        "route_key": route_key,                  # 預設 None:不猜,人填
        "route_check": route_check,
        "proposed_dir_name": dir_name,
        "library_route_key": LIBRARY_ROUTE_KEY,
        "library_folder": library_folder,        # 契約讀不到就 None(不猜)
        "would_write_rel": (str(Path(library_folder) / dir_name / CARD_FILE)
                            if (library_folder and dir_name) else None),
        "skeleton_markdown_sha256": item["content_sha256"],
        "skeleton_markdown": md,
        "brain_write_plan_item": item,
        "executes_now": EXECUTES_NOW,
        "needs_human": list(NEEDS_HUMAN),
        "apply_requires": ["E5.3 human approval receipt (write_receipt) for this item",
                           "E5.5 owner adapter unlock",
                           "HANDOFF §七 vault 那一格由 the maintainer 明文修訂(設計稿 §8.5)"],
        "next": "human_review",
    }


# ---- 落檔 -------------------------------------------------------------------

def run(project, role, seat, route_key=None, state_root=None, index_path=None, cards_path=None,
        bindings_path=None, contract_spec=None, now_ms=None, write=True, uid_rng=secrets.choice):
    root, _how = bp.resolve_state_root(state_root)
    idx_p = Path(index_path) if index_path else root.joinpath(*pi.INDEX_REL)
    cards_p = Path(cards_path) if cards_path else root.joinpath(*pi.CARDS_REL)
    bind_p = Path(bindings_path) if bindings_path else root.joinpath(*pi.BINDINGS_REL)
    if not idx_p.is_file():
        raise CreatePlanError("索引不存在:%s(先 persona_index.py build;冪等靠它)" % idx_p)
    try:
        with open(str(idx_p), encoding="utf-8") as fh:
            index_doc = json.load(fh)
    except (OSError, ValueError) as err:
        raise CreatePlanError("索引讀不動:%s" % err)
    if index_doc.get("schema_version") != pi.SCHEMA_VERSION:
        raise CreatePlanError("索引 schema_version 不合:%r" % index_doc.get("schema_version"))
    try:
        cards = pi.load_cards(cards_p)
        bindings = pi.load_bindings(bind_p)
    except pi.IndexError_ as err:
        raise CreatePlanError(str(err))
    contract = load_contract(contract_spec)   # 讀不到回 None;只有指定 route 時才是硬前提
    if route_key is not None and contract is None:
        raise CreatePlanError("指定了 --route-key 但路由契約讀不到(--contract / EZ2_ROUTE_REGISTRY);"
                              "看不到規則不等於沒有規則,拒")
    out = plan_path(root, project or "", role or "")
    existing = None
    if out.is_file():
        try:
            with open(str(out), encoding="utf-8") as fh:
                existing = json.load(fh)
        except (OSError, ValueError) as err:
            raise CreatePlanError("既有計畫檔讀不動,不覆寫:%s" % err)
    plan = make_plan(index_doc, cards, bindings, project, role, seat, route_key=route_key,
                     contract=contract, existing=existing, now_ms=now_ms, uid_rng=uid_rng)
    if write and not plan.get("idempotent_hit"):
        try:
            refuse_vault_output(out)          # 閘〇:計畫檔不准落在 vault 裡
        except WritePlanError as err:
            raise CreatePlanError(str(err))
        out.parent.mkdir(parents=True, exist_ok=True)
        write_json(str(out), plan)
    return plan, out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="persona_create_plan.py", description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=["plan"])
    ap.add_argument("--project"); ap.add_argument("--role", required=True)
    ap.add_argument("--seat", required=True, help="五碼(created_by)")
    ap.add_argument("--route-key")
    ap.add_argument("--state-root"); ap.add_argument("--index"); ap.add_argument("--cards"); ap.add_argument("--bindings")
    ap.add_argument("--contract", help="路由契約(檔案或 git: 規格;預設 EZ2_ROUTE_REGISTRY)")
    ap.add_argument("--now-ms", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        plan, out = run(args.project, args.role, args.seat, route_key=args.route_key,
                        state_root=args.state_root, index_path=args.index, cards_path=args.cards,
                        bindings_path=args.bindings, contract_spec=args.contract,
                        now_ms=args.now_ms, write=not args.dry_run)
    except CreatePlanRefused as err:
        sys.stderr.write("persona_create_plan: %s\n" % err)
        return 1
    except CreatePlanError as err:
        sys.stderr.write("persona_create_plan: %s\n" % err)
        return 2
    if args.json:
        print(json.dumps(dict(plan, out=str(out)), ensure_ascii=False, sort_keys=True))
    else:
        print("plan %s uid=%s key=%s route=%s executes_now=%s needs_human=%s -> %s%s"
              % ("(既有)" if plan.get("idempotent_hit") else "(新)", plan["persona_uid"], plan["index_key"],
                 plan["route_key"], plan["executes_now"], ",".join(plan["needs_human"]),
                 out, "" if not args.dry_run else " (dry-run 未落檔)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
