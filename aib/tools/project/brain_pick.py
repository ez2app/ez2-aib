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
for _p in (AIB / "src" / "persona_route", AIB / "src" / "identity", AIB / "tools" / "vault"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

#: 新建大腦掛在哪條契約路由底下(現有 19 個 proj.* 都在這裡;權限照抄它,不另給)
NEW_PARENT_ROUTE = "product.expert.draft"
#: 數筆記最多看幾個檔(大資料夾不拖慢頁面;超過就標 notes_capped)
NOTE_SCAN_MAX = 5000
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


def _note_stats(vault: Optional[str], folder: str) -> dict:
    """資料夾底下 .md 篇數與最後修改時刻(epoch);讀不到 ⇒ notes=None(不是 0:0 是「量到了,真的沒有」)。"""
    if not vault or not folder:
        return {"notes": None, "updated_epoch": None, "notes_capped": False}
    base = Path(vault) / folder
    if not base.is_dir():
        return {"notes": None, "updated_epoch": None, "notes_capped": False}
    n, latest, seen = 0, 0.0, 0
    try:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                seen += 1
                if seen > NOTE_SCAN_MAX:
                    return {"notes": n, "updated_epoch": int(latest) or None, "notes_capped": True}
                if fn.endswith(".md"):
                    n += 1
                    try:
                        latest = max(latest, os.stat(os.path.join(dirpath, fn)).st_mtime)
                    except OSError:
                        pass
    except OSError:
        return {"notes": None, "updated_epoch": None, "notes_capped": False}
    return {"notes": n, "updated_epoch": int(latest) or None, "notes_capped": False}


def list_brains(contract: Any, vault: Optional[str] = None, stats: bool = True) -> list:
    """可接的大腦:契約(含 Vault 自掛)裡過得了 check_against_contract 的路由。依標題排序。"""
    import route_register as R
    routes = contract.get("routes") if isinstance(contract, dict) else None
    if not isinstance(routes, list):
        return []
    vault = vault if vault is not None else _vault()
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
        folder = str(r.get("folder") or "")
        title = str(r.get("title") or "").strip() or folder.rstrip("/").split("/")[-1] or key
        item = {"route_key": key, "title": title, "folder": folder, "source": r.get("source") or "contract"}
        if stats:
            item.update(_note_stats(vault, folder))
        out.append(item)
    return sorted(out, key=lambda x: (x["source"] != "vault", x["title"]))


def _captain_family(role: str) -> bool:
    """跟右欄 is_captain_role 同一條:EZ2_AIB_CAPTAIN_ROLES(預設 root),rootN 屬 root 家族。"""
    roles = set((os.environ.get("EZ2_AIB_CAPTAIN_ROLES") or "root").split())
    r = str(role or "")
    return r in roles or (r.rstrip("0123456789") or r) in roles


def seat_brains(root: Path, cabin: str = "") -> list:
    """每一席現在綁的 route。cabin 給了只列那一艙;沒給列全部艙。讀不到的席 brain_route_key=None。
    crown = 右欄戴 👑 的那一席(艦長家族或受派、非顧問、有 session_uuid;受派優先,其次開席早的),每艙至多一席、排最前。"""
    import bind_one as BO
    root = Path(root)
    inst = root / "instances"
    cabins = [cabin] if cabin else sorted(p.name for p in inst.iterdir() if p.is_dir()) if inst.is_dir() else []
    out = []
    for cab in cabins:
        sdir = inst / cab / "seats"
        if not sdir.is_dir():
            continue
        rows = []
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
            rows.append(({"seat": "%s/%s" % (cab_code, code), "seat_id": rec["seat_id"], "cabin": cab,
                          "window": rec.get("window_id"), "role": rec.get("role"), "captain": bool(captain), "crown": False,
                          "project_uid": rec.get("project_uid"), "brain_route_key": route},
                         (not assigned, started), bool(rec.get("session_uuid"))))
        caps = sorted((r for r in rows if r[0]["captain"] and r[2]), key=lambda r: r[1])
        if caps:
            caps[0][0]["crown"] = True
        out.extend(sorted((r[0] for r in rows), key=lambda x: not x["crown"]))
    return out


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
        vault = _vault()
        if not vault:
            return None, None, T("aib.newproj.brain_new_no_vault")
        parent = _parent_folder_id(vault, NEW_PARENT_ROUTE)
        if not parent:
            return None, None, T("aib.newproj.brain_new_no_parent", {"route": NEW_PARENT_ROUTE})
        title = str((np.load_card(root, project_uid) or {}).get("title") or project_uid).replace(":", " ").strip()
        return key, {"create": {"vault": vault, "parent": parent, "title": title, "route_key": key}}, None
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


def create_new(plan: dict) -> dict:
    """照 resolve_pick 給的 plan 建夾 + reindex。回 folder_add 的結果;失敗丟 SystemExit / OSError 由呼叫端接。"""
    import uid_layout_convert as U
    c = plan["create"]
    vault = Path(c["vault"])
    res = U.folder_add(vault, c["parent"], c["title"], False, c["route_key"])
    U.reindex(vault)
    return res


def cli_list(root: Path, cabin: str = "", stats: bool = True) -> dict:
    import bind_one as BO
    contract = BO.load_contract(BO.default_contract_spec())
    if contract is None:
        return {"ok": False, "why": "contract_unavailable", "brains": [], "seats": seat_brains(root, cabin)}
    return {"ok": True, "brains": list_brains(contract, stats=stats), "seats": seat_brains(root, cabin),
            "new_parent_route": NEW_PARENT_ROUTE, "at": int(time.time())}
