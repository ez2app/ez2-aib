#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開席接第二大腦:可接清單 / 各席目前綁的大腦 / --folder 的三種寫法(Andy 11-01 00:15)。

Andy 原話:「為什麼不能直接接已有大腦,或綁目前第一席的第二大腦」。
一步開席(newproj --new-seat --project-uid --persona-card)以前會把 --folder 清空,
頁面建的專案卡又沒有 route_key ⇒ 新席連「新建大腦」都沒綁到資料夾。這支補三件事:

  list_brains   可接的大腦:契約裡 AI 可寫、過得了 check_against_contract 的路由,
                附資料夾名、筆記數、最後更新(只讀;筆記數有掃描上限,超過標 notes_capped)。
  seat_brains   每一席現在綁的 route(讀席位檔 + 身分層 brain_route_key;只讀)。
  resolve_pick  --folder 的寫法 ⇒ 路由代號:
                · 路由代號 / 資料夾路徑 / 資料夾最後一段 —— 交給 new_project.resolve_folder(同一套規則)
                · seat:<艙位碼>/<席碼>  —— 跟那一席同一個大腦(那一席沒綁 ⇒ 拒,不猜)
                · new                   —— 照專案標題在「專家進行中」(product.expert.draft)底下建新夾,
                                           路由代號 proj.<專案 uid 前 12 碼>;同名夾已有就沿用
  create_new    真的建夾(folder-add --route-key + reindex);只在開席確定要走時才呼叫。

全部只讀,create_new 例外(它寫 Vault 索引,跟手動 folder-add 同一支程式)。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
AIB = HERE.parent.parent
for _p in (AIB / "src" / "persona_route", AIB / "src" / "identity", AIB / "tools" / "vault", AIB / "src" / "cockpit"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

#: 新建大腦掛在哪條契約路由底下(現有 19 個 proj.* 都在這裡;權限照抄它,不另給)
NEW_PARENT_ROUTE = "product.expert.draft"
#: 數筆記的總預算(#469 r1 M5):全部路由合計最多看幾個項目(檔 + 目錄都算)、最多幾秒;用完的標 notes_capped
NOTE_SCAN_MAX = 20000
NOTE_SCAN_SECONDS = 2.0
#: 右欄 🤖 橋接席(pane-right-ai.sh AIBOT_PROVIDERS):不戴 👑
AIBOT_PROVIDERS = frozenset(("sand", "grokbot", "codexbot", "claudebot", "musebot"))
SEAT_PREFIX = "seat:"
NEW_WORD = "new"
_UID_HEX = re.compile(r"^proj:([0-9a-f]{12,})$")


def _vault() -> Optional[str]:
    v = os.environ.get("EZ2_VAULT_DIR")
    if v:
        return v
    try:
        import route_register
        return route_register.default_vault()
    except Exception:  # noqa: BLE001
        return None


def _budget() -> dict:
    return {"left": NOTE_SCAN_MAX, "deadline": time.monotonic() + NOTE_SCAN_SECONDS}


def _note_stats(vault: Optional[str], folder: str, budget: Optional[dict] = None) -> dict:
    """資料夾底下 .md 篇數與最後修改時刻(epoch);讀不到 ⇒ notes=None(不是 0:0 是「量到了,真的沒有」)。
    用 scandir 一層一層走,每個項目(檔或目錄)都扣 budget,時間到也停 ⇒ 大目錄 / 一堆空目錄都拖不住頁面(#469 r1 M5)。"""
    budget = budget if budget is not None else _budget()
    if not vault or not folder:
        return {"notes": None, "updated_epoch": None, "notes_capped": False}
    base = Path(vault) / folder
    if not base.is_dir():
        return {"notes": None, "updated_epoch": None, "notes_capped": False}
    if budget["left"] <= 0 or time.monotonic() > budget["deadline"]:     # r2 M5:預算已用完 ⇒ 不開這條,照實說沒量
        return {"notes": None, "updated_epoch": None, "notes_capped": True}
    n, latest, stack, first = 0, 0.0, [str(base)], True
    while stack:
        d = stack.pop()
        budget["left"] -= 1                         # 開目錄本身也扣(一堆空目錄也拖不住)
        if budget["left"] < 0 or time.monotonic() > budget["deadline"]:
            return {"notes": n, "updated_epoch": int(latest) or None, "notes_capped": True}
        try:
            it = os.scandir(d)
        except OSError:
            if first:                               # 根目錄就開不了 ⇒ 量不到(None),不是 0
                return {"notes": None, "updated_epoch": None, "notes_capped": False}
            continue
        first = False
        with it:
            for e in it:
                budget["left"] -= 1
                if budget["left"] < 0 or time.monotonic() > budget["deadline"]:
                    return {"notes": n, "updated_epoch": int(latest) or None, "notes_capped": True}
                if e.name.startswith("."):
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.name.endswith(".md") and e.is_file(follow_symlinks=False):
                        n += 1
                        latest = max(latest, e.stat(follow_symlinks=False).st_mtime)
                except OSError:
                    pass
    return {"notes": n, "updated_epoch": int(latest) or None, "notes_capped": False}


def list_brains(contract: Any, vault: Optional[str] = None, stats: bool = True) -> list:
    """可接的大腦:契約(含 Vault 自掛)裡過得了 check_against_contract 的路由。依標題排序。"""
    import route_register as R
    routes = contract.get("routes") if isinstance(contract, dict) else None
    if not isinstance(routes, list):
        return []
    vault = vault if vault is not None else _vault()
    budget = _budget()
    out = []
    for r in routes:
        if not isinstance(r, dict):
            continue
        key = str(r.get("routeKey") or "")
        if not key:
            continue
        ok, _code, _detail = R.check_against_contract(key, contract)
        if not ok:
            continue
        literal = str(r.get("folder") or "")
        # #469 r1 M6:uid 版面的真路徑查 Vault 自己的索引(跟寫入端同一支);查不到才用契約字面
        try:
            real = R.resolve_route_folder(key, contract=contract, root=vault) if vault else None
        except Exception:  # noqa: BLE001
            real = None
        uid = _uid_layout(vault)
        folder = real or ("" if uid else literal)    # r2 L6:uid 版面查不到索引 ⇒ 不給舊字面路徑(消費端會當真落點)
        try:
            label = R.route_label(key, root=vault, contract=contract) if vault else ""
        except Exception:  # noqa: BLE001
            label = ""
        title = (str(r.get("title") or "").strip() or (label if label and label != key else "")
                 or literal.rstrip("/").split("/")[-1] or key)
        item = {"route_key": key, "title": title, "folder": folder, "source": r.get("source") or "contract"}
        if stats:
            item.update(_note_stats(vault, folder, budget))
        out.append(item)
    return sorted(out, key=lambda x: (x["source"] != "vault", x["title"]))


def _uid_layout(vault: Optional[str]) -> bool:
    try:
        import route_register as R
        return bool(vault) and R.vault_is_uid_layout(vault)
    except Exception:  # noqa: BLE001
        return False


def _captain_family(role: str) -> bool:
    """跟右欄 is_captain_role 同一條:EZ2_AIB_CAPTAIN_ROLES(預設 root),rootN 屬 root 家族。"""
    roles = set((os.environ.get("EZ2_AIB_CAPTAIN_ROLES") or "root").split())
    r = str(role or "")
    return r in roles or (r.rstrip("0123456789") or r) in roles


def seat_brains(root: Path, cabin: str = "") -> list:
    """每一席現在綁的 route。cabin 給了只列那一艙;沒給列全部艙。讀不到的席 brain_route_key=None。
    crown = 右欄戴 👑 的那一席,判準跟右欄同源(#469 r1 M7):艦長家族或受派、非顧問、seat_status.unassigned_reason 驗得過
    (席位檔讀得懂 + session_uuid 形狀對)、不是 🤖 橋接引擎;受派優先,其次開席早的;每艙至多一席、排最前。
    右欄另會用活窗 @aib_role 覆寫角色,這裡讀不到 tmux,只看席位檔。"""
    import bind_one as BO
    try:
        import seat_status as SS
    except Exception:  # noqa: BLE001
        SS = None
    root = Path(root)
    inst = root / "instances"
    cabins = [cabin] if cabin else sorted(p.name for p in inst.iterdir() if p.is_dir()) if inst.is_dir() else []
    out = []
    for cab in cabins:
        sdir = inst / cab / "seats"
        if not sdir.is_dir():
            continue
        rows = []
        fleets = {}
        for f in sorted(sdir.glob("w*.json")):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict) or not rec.get("seat_id"):
                continue
            code = str(rec.get("session_uuid") or "")[-5:] or str(rec["seat_id"])
            cab_code = cab.rsplit("-", 1)[-1] if cab.count("-") == 2 else cab
            route = None
            try:
                agent = BO.seat_status(root, str(rec["seat_id"])).get("agent") or {}
                route = agent.get("brain_route_key") or None
            except Exception:  # noqa: BLE001  從沒綁過 / 身分層讀不到
                route = None
            assigned = rec.get("captain_assigned") is True
            captain = (_captain_family(rec.get("role")) or assigned) and rec.get("advisor") is not True
            try:
                started = float(rec.get("started_epoch") or 0) or 9999999999.0
            except (TypeError, ValueError):
                started = 9999999999.0
            fleet = str(rec.get("fleet_id") or rec.get("provider") or "1")
            rows.append(({"seat": "%s/%s" % (cab_code, code), "seat_id": rec["seat_id"], "cabin": cab,
                          "window": rec.get("window_id"), "role": rec.get("role"), "captain": bool(captain), "crown": False,
                          "project_uid": rec.get("project_uid"), "brain_route_key": route},
                         (not assigned, started), _crownable(SS, rec, f.name)))
            fleets[id(rows[-1])] = fleet
        # r2 M7:跟右欄一樣先分艦隊、照組序找第一個有可戴冠艦長的組,組內受派優先、其次開席早
        # r3 M7:dict 保留首次出現順序 + sorted 穩定 ⇒ 排序鍵相同的組("1" / "01")跟右欄一樣依出現序,跨程序穩定
        for fl in sorted(dict.fromkeys(fleets[id(r)] for r in rows), key=_fleet_key):
            caps = sorted((r for r in rows if fleets[id(r)] == fl and r[0]["captain"] and r[2]), key=lambda r: r[1])
            if caps:
                caps[0][0]["crown"] = True
                break
        out.extend(sorted((r[0] for r in rows), key=lambda x: not x["crown"]))
    return out


#: 右欄組序用的引擎序預設(pane-right-ai.sh _eng_order;EZ2_AIB_ENGINE_ORDER 可覆寫)
ENGINE_ORDER = ("claude codex agy copilot grok deepseek nvidia openrouter typesafe openrouterjev grokbot codexbot "
                "claudebot musebot")


def _fleet_key(k: str) -> tuple:
    """右欄 _grp_key 同一條:EZ2_AIB_FLEET_ORDER > 純數字艦隊(數值序)> 引擎序 > 其餘按名。"""
    order = (os.environ.get("EZ2_AIB_FLEET_ORDER") or "").split()
    eng = (os.environ.get("EZ2_AIB_ENGINE_ORDER") or ENGINE_ORDER).split()
    if k in order:
        return (0, order.index(k), "")
    if k.isdigit():
        return (1, int(k), "")
    return (2, eng.index(k) if k in eng else len(eng), k)


def _crownable(SS, rec: dict, base: str) -> bool:
    if str(rec.get("provider") or "") in AIBOT_PROVIDERS:
        return False
    if SS is None:
        return bool(rec.get("session_uuid"))
    try:
        return SS.unassigned_reason(rec, base) is None
    except Exception:  # noqa: BLE001
        return False


def new_route_key(project_uid: str) -> Optional[str]:
    """新建大腦的路由代號:proj.<專案 uid 前 12 碼>。標題常是中文,轉不出安全代號,改用 uid(不會撞)。"""
    m = _UID_HEX.match(str(project_uid or ""))
    return "proj.%s" % m.group(1)[:12] if m else None


def _parent_folder_id(vault: str, route_key: str) -> Optional[str]:
    try:
        with open(os.path.join(vault, ".ez2", "index", "folder-id-index.json"), encoding="utf-8") as fh:
            items = json.load(fh).get("items") or []
    except (OSError, ValueError, AttributeError):
        return None
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and it.get("routeKey") == route_key and it.get("status", "active") == "active":
            return str(it.get("folderId") or "") or None
    return None


def resolve_pick(np, root: Path, contract: Any, value: str, project_uid: str) -> tuple:
    """--folder 的值 ⇒ (route_key, plan, 錯誤)。plan 是 None(接現有)或 {"create": {...}}(要建新夾)。"""
    T = np.T
    v = str(value or "").strip()
    if not v:
        return None, None, None
    if v.lower() == NEW_WORD:
        key = new_route_key(project_uid)
        if not key:
            return None, None, T("aib.newproj.brain_new_bad_uid", {"uid": project_uid})
        for r in (contract or {}).get("routes") or []:
            if isinstance(r, dict) and r.get("routeKey") == key:      # 先前建過(或重跑):直接接
                return key, None, None
        import route_register as R
        ok, code, detail = R.check_against_contract(NEW_PARENT_ROUTE, contract)   # #469 r1 H1:上層不准寫 ⇒ 開席前就擋,不建夾
        if not ok:
            return None, None, T("aib.newproj.folder_rejected", {"key": NEW_PARENT_ROUTE, "code": code, "detail": detail})
        vault = _vault()
        if not vault:
            return None, None, T("aib.newproj.brain_new_no_vault")
        parent = _parent_folder_id(vault, NEW_PARENT_ROUTE)
        if not parent:
            return None, None, T("aib.newproj.brain_new_no_parent", {"route": NEW_PARENT_ROUTE})
        title = str((np.load_card(root, project_uid) or {}).get("title") or project_uid).replace(":", " ").strip()
        plan = {"create": {"vault": vault, "parent": parent, "title": title, "route_key": key}}
        # #469 r1 M2:標題不合法 / 同名夾已掛別的代號 —— folder_add 自己的預演(不寫檔)先跑一次,開席前就擋
        try:
            import uid_layout_convert as U
            U.folder_add(Path(vault), parent, title, True, key)
        except SystemExit as exc:
            return None, None, T("aib.newproj.brain_new_bad_title", {"title": title, "err": exc.code})
        except (OSError, ValueError, KeyError) as exc:
            return None, None, T("aib.newproj.brain_new_bad_title", {"title": title, "err": exc})
        return key, plan, None
    if v.lower().startswith(SEAT_PREFIX):
        addr = v[len(SEAT_PREFIX):].strip()
        seat_id, _rec, _inst, err = np._resolve_seat(root, addr)
        if err:
            return None, None, err
        import bind_one as BO
        try:
            route = (BO.seat_status(root, seat_id).get("agent") or {}).get("brain_route_key") or ""
        except Exception:  # noqa: BLE001
            route = ""
        if not route:
            return None, None, T("aib.newproj.brain_seat_unbound", {"seat": addr})
        return route, None, None
    key, _folder, ferr = np.resolve_folder(contract, v)
    if ferr:
        return None, None, ferr
    return key, None, None


class ReindexWarning(Exception):
    """夾建好、代號也登記了(folder_add 自己寫索引),只是事後的 reindex 沒跑成:可以照常綁,只要提醒。"""


def create_new(plan: dict) -> dict:
    """照 resolve_pick 給的 plan 建夾 + reindex。整段握 Vault 的寫入鎖(vault_cli._locked;#469 r1/r2 M4),兩個專案同時建
    不會互蓋索引(鎖跟 aib vault 寫入端共用)。folder_add 失敗丟 SystemExit / OSError;reindex 失敗丟 ReindexWarning(夾已在)。"""
    import uid_layout_convert as U
    import vault_cli as VC
    c = plan["create"]
    vault = Path(c["vault"])
    with VC._locked(vault):                         # r2 M4:跟 aib vault mkdir / newpost / doctor --fix 同一把 .ez2/.write.lock
        res = U.folder_add(vault, c["parent"], c["title"], False, c["route_key"])
        try:
            U.reindex(vault)
        except (SystemExit, OSError, ValueError) as exc:
            raise ReindexWarning(str(getattr(exc, "code", None) or exc)) from exc
    return res


def cli_list(root: Path, cabin: str = "", stats: bool = True) -> dict:
    import bind_one as BO
    contract = BO.load_contract(BO.default_contract_spec())
    if contract is None:
        return {"ok": False, "why": "contract_unavailable", "brains": [], "seats": seat_brains(root, cabin)}
    return {"ok": True, "brains": list_brains(contract, stats=stats), "seats": seat_brains(root, cabin),
            "new_parent_route": NEW_PARENT_ROUTE, "at": int(time.time())}
