#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""uid_layout_convert.py — 把 Obsidian 第二大腦轉成「uid 版面」(the maintainer 2026-09-26 R7/R8/R9;10-05 令「先寫轉換,一併完成」)。

    資料夾名 = folderId(fld_<模組>_<種類>_<12 hex>)
    筆記檔名 = <uid>.md(uid = ez2-note-time-base36-v1 六碼;既有 uid 一律保留)
    標題與多語顯示名 → <vault>/.ez2/display/labels.json(folderId / uid → {title, i18n})
    路由 → <vault>/.ez2/index/route-key-index.json(folderPath = 改名後的真路徑)
    資料夾身分 → <vault>/.ez2/index/folder-id-index.json(新增 title / legacyPaths / status)

四個子命令,照順序:
    plan   --vault V --contract C [--legacy DIR --legacy-map MAP.json] --out plan.json     只讀,產計畫
    apply  --plan plan.json --backup-dir DIR --confirm-text "我已備份並同意轉成 uid 版面"   寫(先 tar 整庫到庫外)
    verify --vault V                                                                        只讀,驗收
    undo   --receipt receipt.json                                                           退回改名(frontmatter 補的身分留著)

鐵則:
- 既有 uid 不換號;重複 uid 只在「一邊明顯是副本(.bak / 鏡像夾)」時給副本新號並記 duplicate_of,否則列 blocker、兩邊都不改名。
- 不碰 .obsidian / .ez2 / .git / .trash;非 .md 附件只跟著資料夾走,不改名。
- 舊檔名、舊路徑全部進 aliases / legacy_paths / legacyPaths,所以 [[舊名]] 連結仍解得開。
- 寫入前整庫 tar 到庫外;apply 產 receipt(含 undo map)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "cockpit"))
import i18n_lib  # noqa: E402

LOC = i18n_lib.resolve_locale()


def T(key, params=None):
    """給人看的字一律走 i18n(照 tools/release/ship.py);鍵 aib.vault_convert.<key>。"""
    return i18n_lib.t("aib.vault_convert." + key, params, locale=LOC)


UID_EPOCH_MS = 1577836800000          # 2020-01-01T00:00:00Z,與 ez2-ai shared/ez2-note-format.js 同源
UID_RE = re.compile(r"^[0-9a-z]{6}$")
UID_PREFIX_RE = re.compile(r"^([0-9a-z]{6})-(.+)$")
FOLDER_ID_RE = re.compile(r"^fld_[a-z]_[a-z]+_[0-9a-f]{8,12}$")
SKIP_DIRS = {".obsidian", ".ez2", ".git", ".trash", ".DS_Store", "node_modules"}
MANAGED_KEYS = ("uid", "aliases", "title", "orig_name", "uid_scheme", "routeKey", "category_uid",
                "privacy_level", "source_kind", "created", "updated", "ez2fmt", "tags", "legacy_paths",
                "folder_id", "duplicate_of")
CONFIRM_TEXT = T("confirm_text")   # 當前語系的確認句;apply 也接受 zh-TW 那句(文件與測試都用它)
LANGS = ("zh-TW", "en", "ja", "ko")


# ---------------------------------------------------------------- 小工具
def sha12(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def b36(n: int) -> str:
    if n == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out


def mint_uid(ms: int, used: set) -> str:
    n = max(0, (int(ms) - UID_EPOCH_MS) // 1000)
    code = b36(n).rjust(6, "0")
    while code in used or not UID_RE.match(code):
        n += 1
        code = b36(n).rjust(6, "0")
    used.add(code)
    return code


def iso(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms / 1000)) + ".%03dZ" % (ms % 1000)


def module_of(top: str) -> str:
    c = (top[:1] or "x").lower()
    return c if c in "abcdefghijklmnopqrstuvwxyz" else "x"


def yaml_str(s: str) -> str:
    return json.dumps(str(s), ensure_ascii=False)


def yaml_list(items) -> str:
    return "[" + ", ".join(yaml_str(x) for x in items) + "]"


# ---------------------------------------------------------------- frontmatter
FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?", re.S)


def split_frontmatter(text: str):
    m = FM_RE.match(text)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def fm_lines_to_map(fm: str) -> dict:
    """只讀我們關心的單行鍵(uid/title/routeKey/...)。清單類(aliases/tags/legacy_paths)只認單行 [..] 形。"""
    out = {}
    for ln in fm.splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+):\s*(.*)$", ln)
        if m:
            out.setdefault(m.group(1), m.group(2).strip())
    return out


def strip_managed(fm: str) -> list[str]:
    """把要重寫的鍵(含其多行清單區塊)拿掉,保留其他原行。"""
    keep, skip_block = [], False
    for ln in fm.splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+):(.*)$", ln)
        if m:
            key = m.group(1)
            skip_block = key in MANAGED_KEYS and m.group(2).strip() == ""   # 多行清單起頭
            if key in MANAGED_KEYS:
                continue
            keep.append(ln)
        elif skip_block and re.match(r"^\s+-\s", ln):
            continue
        else:
            skip_block = False
            keep.append(ln)
    return keep


def parse_inline_list(v: str) -> list[str]:
    v = (v or "").strip()
    if v.startswith("[") and v.endswith("]"):
        try:
            return [str(x) for x in json.loads(v)]
        except ValueError:
            return [x.strip().strip('"\'') for x in v[1:-1].split(",") if x.strip()]
    if v.startswith('"') or v.startswith("'"):
        return [v.strip('"\'')]
    return [v] if v else []


# ---------------------------------------------------------------- 索引
def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_contract(path: str) -> dict:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    routes = {}
    for r in d.get("routes", []):
        if r.get("routeKey") and r.get("folder"):
            routes[r["routeKey"]] = dict(r)
    return routes


# ---------------------------------------------------------------- plan
def build_plan(vault: Path, contract_path: str, legacy: Path | None, legacy_map: dict | None) -> dict:
    vault = vault.resolve()
    routes = load_contract(contract_path)                      # routeKey -> {folder, module, ...}
    folder_to_route = {r["folder"]: k for k, r in routes.items()}
    def under_library(rel_s: str) -> bool:
        return any(rel_s.startswith(lib + "/") for lib in library_olds)
    idx_dir = vault / ".ez2" / "index"
    fid_index = load_json(idx_dir / "folder-id-index.json", {"items": []})
    rk_index = load_json(idx_dir / "route-key-index.json", {"items": []})
    known_fid = {it["folderPath"]: it for it in fid_index.get("items", []) if it.get("folderPath")}
    known_rk = {it["routeKey"]: it for it in rk_index.get("items", [])}
    LIBRARY_RK = "product.expert.agent_library"
    # 角色庫位置:vault 自己的索引優先(已轉過的庫路徑是 folderId),沒有才用契約字面;兩個都存在就兩個都算
    _idx_lib = next((it.get("folderPath") for it in rk_index.get("items", []) if it.get("routeKey") == LIBRARY_RK and (vault / it.get("folderPath", "")).is_dir()), "")
    _con_lib = routes.get(LIBRARY_RK, {}).get("folder", "")
    library_old = _idx_lib or _con_lib
    library_olds = {x for x in (_idx_lib, _con_lib) if x and (vault / x).is_dir()}

    plan = {"schema": "2026-10-05.ez2-uid-layout-plan", "vault": str(vault), "contract": contract_path,
            "generated_at": iso(int(time.time() * 1000)), "legacy_copies": [], "folders": [], "notes": [],
            "blockers": [], "warnings": [], "stats": {}}

    # ---- 1. 舊庫導入(只複製,目的地照對照表) ----
    legacy_targets: dict[str, dict] = {}   # 導入後在 vault 內的相對路徑 -> {legacy_rel, routeKey}
    if legacy and legacy_map:
        legacy = legacy.resolve()
        for zone, spec in legacy_map.items():
            if zone.startswith("_"):
                continue
            src = legacy / zone
            if not src.exists():
                plan["warnings"].append(T("warn_zone_missing", {"zone": zone}))
                continue
            dst_rel = spec["dst"]; rk = spec.get("routeKey", "")
            if src.is_file():
                files = [src]
            else:
                files = [p for p in src.rglob("*") if p.is_file() and not any(part in SKIP_DIRS or part.startswith(".") for part in p.relative_to(src).parts)]
            for p in files:
                rel_in_zone = p.relative_to(src) if src.is_dir() else Path(p.name)
                sub = spec.get("subdir", "")
                dst = Path(dst_rel) / sub / rel_in_zone if sub else Path(dst_rel) / rel_in_zone
                if (vault / dst).exists():
                    plan["warnings"].append(T("warn_dst_exists", {"dst": str(dst)}))
                    continue
                if library_old and str(dst).startswith(library_old + "/"):
                    plan["warnings"].append(T("warn_library_skip", {"dst": str(dst)}))
                    continue
                plan["legacy_copies"].append({"src": str(p), "dst_rel": str(dst)})
                legacy_targets[str(dst)] = {"legacy_rel": str(Path(zone) / rel_in_zone) if src.is_dir() else zone, "routeKey": rk}

    # ---- 2. 資料夾身分 ----
    def all_dirs():
        out = []
        for dp, dn, _ in os.walk(vault):
            dn[:] = sorted(d for d in dn if d not in SKIP_DIRS and not d.startswith("."))
            for d in dn:
                out.append(Path(dp, d).relative_to(vault))
        # 舊庫導入會新建的資料夾
        for dst_rel in legacy_targets:
            parent = Path(dst_rel).parent
            while str(parent) not in ("", "."):
                out.append(parent); parent = parent.parent
        uniq = sorted({str(p) for p in out}, key=lambda s: (s.count("/"), s))
        return [Path(s) for s in uniq]

    def card_uid_of(rel: Path) -> str:
        p = vault / rel / "角色.md"
        if not p.is_file():
            return ""
        fm, _ = split_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        u = (fm_lines_to_map(fm).get("uid") if fm else "") or ""
        u = u.strip().strip('"\'')
        return u if UID_RE.match(u) else ""

    folder_recs: dict[str, dict] = {}
    for rel in all_dirs():
        rel_s = str(rel); top = rel.parts[0]; mod = module_of(top)
        parent_rel = str(rel.parent) if len(rel.parts) > 1 else ""
        if under_library(rel_s):
            _lib = next(lib for lib in library_olds if rel_s.startswith(lib + "/"))
            depth = len(Path(rel_s).parts) - len(Path(_lib).parts)
            cu = card_uid_of(rel) if depth == 1 else ""
            if depth == 1 and UID_RE.match(rel.name):
                fid, rk, kind = rel.name, "", "card"               # 已經是卡片 uid
            elif depth == 1 and cu and not rel.name.startswith("_"):
                fid, rk, kind = cu, "", "card"                     # 卡夾 → 卡片 uid(SOP §2 規則 2)
            else:
                fid, rk, kind = rel.name, "", "keep"               # _結構夾、卡內夾:名字不動
            folder_recs[rel_s] = {"old_rel": rel_s, "folderId": fid, "kind": kind, "routeKey": rk,
                                  "parent_old_rel": parent_rel, "title": rel.name, "level": len(rel.parts)}
            continue
        if FOLDER_ID_RE.match(rel.name):
            fid = rel.name; kind = "route" if "_route_" in fid else ("group" if "_group_" in fid else "sub")
            rk = next((it.get("routeKey", "") for it in fid_index.get("items", []) if it.get("folderId") == fid), "")
            if not rk:
                rk = next((k for k, r in routes.items() if "fld_%s_route_%s" % (mod, sha12(k)) == fid), "")
        elif rel_s in known_fid:
            fid = known_fid[rel_s]["folderId"]; rk = known_fid[rel_s].get("routeKey", "")
            kind = "route" if "_route_" in fid else ("group" if "_group_" in fid else "sub")
        elif rel_s in folder_to_route:
            rk = folder_to_route[rel_s]; fid = "fld_%s_route_%s" % (mod, sha12(rk)); kind = "route"
        elif len(rel.parts) == 1:
            fid = "fld_%s_group_%s" % (mod, sha12(rel_s)); rk = "folder.group.%s.%s" % (mod, sha12(rel_s)[:8]); kind = "group"
        else:
            fid = "fld_%s_sub_%s" % (mod, sha12(rel_s)); rk = ""; kind = "sub"
        folder_recs[rel_s] = {"old_rel": rel_s, "folderId": fid, "kind": kind, "routeKey": rk,
                              "parent_old_rel": parent_rel, "title": rel.name, "level": len(rel.parts)}
    # 新路徑 = 祖先全部換成 folderId
    def new_rel_of_dir(old_rel: str) -> str:
        parts = Path(old_rel).parts; out = []
        for i in range(1, len(parts) + 1):
            out.append(folder_recs[str(Path(*parts[:i]))]["folderId"])
        return "/".join(out)
    for rec in folder_recs.values():
        rec["new_rel"] = new_rel_of_dir(rec["old_rel"])
        rec["parentFolderId"] = folder_recs[rec["parent_old_rel"]]["folderId"] if rec["parent_old_rel"] else ""
        rec["routeKey_inherited"] = rec["routeKey"]
    # 繼承最近祖先的 routeKey(給筆記用)
    def inherited_rk(old_rel: str) -> str:
        p = Path(old_rel)
        while str(p) not in ("", "."):
            rk = folder_recs.get(str(p), {}).get("routeKey", "")
            if rk and not rk.startswith("folder.group."):
                return rk
            p = p.parent
        return ""
    for rec in folder_recs.values():
        rec["routeKey_inherited"] = inherited_rk(rec["old_rel"])
    plan["folders"] = sorted(folder_recs.values(), key=lambda r: (r["level"], r["old_rel"]))

    # ---- 3. 筆記身分 ----
    used: set[str] = set()
    notes_raw = []
    for dp, dn, fn in os.walk(vault):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith(".")]
        rel_dir = str(Path(dp).relative_to(vault))
        if rel_dir in library_olds or under_library(rel_dir):
            continue                                                # 角色庫:自己的 git 倉與指紋規則,不改寫、不改名
        for f in sorted(fn):
            if f.endswith(".md"):
                notes_raw.append((Path(dp, f).relative_to(vault), False, None))
    for dst_rel, meta in legacy_targets.items():
        if dst_rel.endswith(".md"):
            notes_raw.append((Path(dst_rel), True, meta))

    def read_text(rel: Path, is_legacy: bool):
        p = (legacy / legacy_targets[str(rel)]["legacy_rel"]) if is_legacy else (vault / rel)
        raw = p.read_bytes()
        try:
            return raw.decode("utf-8"), p
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace"), p

    first_pass = []
    for rel, is_legacy, meta in notes_raw:
        text, src_path = read_text(rel, is_legacy)
        fm, body = split_frontmatter(text)
        kv = fm_lines_to_map(fm) if fm is not None else {}
        uid = (kv.get("uid") or "").strip().strip('"\'')
        m = UID_PREFIX_RE.match(rel.stem)
        if not UID_RE.match(uid):
            uid = ""
        first_pass.append({"rel": rel, "is_legacy": is_legacy, "meta": meta, "fm": fm, "kv": kv, "uid": uid,
                           "name_uid": m.group(1) if m else "", "mtime_ms": int(src_path.stat().st_mtime * 1000)})
    # 重複 uid 處理
    by_uid: dict[str, list] = {}
    for n in first_pass:
        if n["uid"]:
            by_uid.setdefault(n["uid"], []).append(n)
    def _sha_of(n) -> str:
        p = (legacy / n["meta"]["legacy_rel"]) if n["is_legacy"] else (vault / n["rel"])
        return hashlib.sha256(p.read_bytes()).hexdigest()
    for uid, lst in list(by_uid.items()):
        if len(lst) > 1 and len({_sha_of(n) for n in lst}) == 1:
            # 內容逐 byte 相同 = 同一篇的副本(例如另一台 rsync 過來的):留一份(已在 fld_ 路徑下的優先,否則最舊),其餘 apply 時直接丟
            def _rank(n):
                return (0 if (not n["is_legacy"] and str(n["rel"]).startswith("fld_")) else 1, n["mtime_ms"], str(n["rel"]))
            keep = sorted(lst, key=_rank)[0]
            for n in lst:
                if n is not keep:
                    n["drop_identical"] = True
            by_uid[uid] = [keep]
    def is_copy(n) -> bool:
        s = str(n["rel"])
        return (".bak" in s) or ("_我的狀態/" in s) or ("/backup" in s.lower()) or ("副本" in s) or ("99.Ez2_垃圾桶/" in s) or ("/.trash/" in s) or n["is_legacy"]
    for uid, lst in by_uid.items():
        if len(lst) == 1:
            used.add(uid); continue
        used.add(uid)
        originals = [n for n in lst if not is_copy(n)]
        if len(originals) == 1:
            keep = originals[0]
        else:
            # 真撞號(都不是副本)或全是副本:最舊的那篇留 uid,其餘換新號並打 #ez2/uid-collision 留人審
            keep = sorted(lst, key=lambda n: (n["mtime_ms"], str(n["rel"])))[0]
            plan["warnings"].append(T("warn_uid_collision", {"uid": uid, "count": len(lst), "keep": str(keep["rel"])}))
        for n in lst:
            if n is not keep:
                n["duplicate_of"] = uid; n["uid"] = ""      # 之後補新號
                n["collision"] = (len(originals) != 1)
    # 補號(按 mtime 排序,穩定)
    plan["drops"] = [{"old_rel": str(n["rel"]), "uid": n["uid"], "reason": "identical duplicate"} for n in first_pass if n.get("drop_identical")]
    for n in sorted(first_pass, key=lambda n: (n["mtime_ms"], str(n["rel"]))):
        if n.get("blocked") or n.get("drop_identical"):
            continue
        minted = False
        if not n["uid"]:
            if n["name_uid"] and n["name_uid"] not in used and not n.get("duplicate_of"):
                n["uid"] = n["name_uid"]; used.add(n["uid"])      # 檔名前綴是舊身分,沿用
            else:
                n["uid"] = mint_uid(n["mtime_ms"], used); minted = True
        kv = n["kv"]; rel = n["rel"]
        folder_old = str(rel.parent) if str(rel.parent) != "." else ""
        frec = folder_recs.get(folder_old)
        rk_existing = (kv.get("routeKey") or "").strip().strip('"\'')
        rk = rk_existing or (n["meta"]["routeKey"] if n["meta"] and n["meta"].get("routeKey") else "") or (frec["routeKey_inherited"] if frec else "") or "core.inbox"
        cat_existing = (kv.get("category_uid") or "").strip().strip('"\'')
        cat = cat_existing or ("cat_" + re.sub(r"[^a-z0-9_]", "_", rk.replace(".", "_")))
        title_existing = (kv.get("title") or "").strip().strip('"\'')
        base_title = UID_PREFIX_RE.sub(r"\2", rel.stem) if UID_PREFIX_RE.match(rel.stem) else rel.stem
        title = title_existing or base_title
        aliases = parse_inline_list(kv.get("aliases", ""))
        for a in (n["uid"], rel.stem, base_title):
            if a and a not in aliases:
                aliases.append(a)
        legacy_paths = parse_inline_list(kv.get("legacy_paths", ""))
        orig_name_existing = (kv.get("orig_name") or "").strip().strip('"\'')
        created = (kv.get("created") or "").strip().strip('"\'') or iso(n["mtime_ms"])
        updated = (kv.get("updated") or "").strip().strip('"\'') or iso(n["mtime_ms"])
        source_kind = (kv.get("source_kind") or "").strip().strip('"\'') or ("legacy_obsidian" if (n["is_legacy"] or minted) else "manual")
        privacy = (kv.get("privacy_level") or "").strip().strip('"\'') or "internal"
        tags = parse_inline_list(kv.get("tags", ""))
        if n["is_legacy"] and "#ez2/imported" not in tags:
            tags.append("#ez2/imported")
        if n.get("duplicate_of") and "#ez2/duplicate" not in tags:
            tags.append("#ez2/duplicate")
        if n.get("collision") and "#ez2/uid-collision" not in tags:
            tags.append("#ez2/uid-collision")
        new_dir = frec["new_rel"] if frec else ""
        new_rel = str(Path(new_dir) / (n["uid"] + ".md")) if new_dir else n["uid"] + ".md"
        old_path_for_record = n["meta"]["legacy_rel"] if n["is_legacy"] else str(rel)
        if (n["is_legacy"] or str(rel) != new_rel) and old_path_for_record not in legacy_paths:
            legacy_paths.append(old_path_for_record)          # 只在這一次真的搬了/改名了才記,重跑不長
        plan["notes"].append({
            "old_rel": str(rel), "new_rel": new_rel, "uid": n["uid"], "minted": minted,
            "is_legacy": n["is_legacy"], "legacy_src": (str(legacy / n["meta"]["legacy_rel"]) if n["is_legacy"] else ""),
            "had_frontmatter": n["fm"] is not None, "duplicate_of": n.get("duplicate_of", ""),
            "folder_id": frec["folderId"] if frec else "",
            "fm_set": {"uid": n["uid"], "aliases": aliases, "title": title, "orig_name": orig_name_existing or rel.name,
                       "uid_scheme": "ez2-note-time-base36-v1", "routeKey": rk, "category_uid": cat,
                       "privacy_level": privacy, "source_kind": source_kind, "created": created, "updated": updated,
                       "ez2fmt": 2, "tags": tags, "legacy_paths": legacy_paths}})
    # 新路徑撞檔(同資料夾同 uid)
    seen_new: dict[str, str] = {}
    for nrec in plan["notes"]:
        if nrec["new_rel"] in seen_new:
            plan["blockers"].append({"new_rel": nrec["new_rel"], "paths": [seen_new[nrec["new_rel"]], nrec["old_rel"]], "reason": T("blocker_same_new_rel")})
        seen_new[nrec["new_rel"]] = nrec["old_rel"]
    plan["stats"] = {
        "folders": len(plan["folders"]),
        "folders_route": sum(1 for f in plan["folders"] if f["kind"] == "route"),
        "folders_group": sum(1 for f in plan["folders"] if f["kind"] == "group"),
        "folders_sub": sum(1 for f in plan["folders"] if f["kind"] == "sub"),
        "folders_card": sum(1 for f in plan["folders"] if f["kind"] == "card"),
        "folders_keep": sum(1 for f in plan["folders"] if f["kind"] == "keep"),
        "notes": len(plan["notes"]),
        "notes_minted": sum(1 for n in plan["notes"] if n["minted"]),
        "notes_kept_uid": sum(1 for n in plan["notes"] if not n["minted"]),
        "notes_legacy": sum(1 for n in plan["notes"] if n["is_legacy"]),
        "notes_no_frontmatter_before": sum(1 for n in plan["notes"] if not n["had_frontmatter"]),
        "duplicates_renumbered": sum(1 for n in plan["notes"] if n["duplicate_of"]),
        "legacy_copies": len(plan["legacy_copies"]),
        "blockers": len(plan["blockers"]),
        "drops_identical": len(plan.get("drops", [])),
        "warnings": len(plan["warnings"]),
    }
    return plan


# ---------------------------------------------------------------- apply
def render_frontmatter(old_fm: str | None, fm_set: dict, folder_id: str, duplicate_of: str) -> str:
    keep = strip_managed(old_fm) if old_fm else []
    def uid_yaml(u: str) -> str:
        return '"%s"' % u if re.match(r"^[0-9]+(e[0-9]+)?$", u) or re.match(r"^[0-9]*e[0-9]+$", u) else u
    lines = ["---",
             "uid: %s" % uid_yaml(fm_set["uid"]),
             "aliases: %s" % yaml_list(fm_set["aliases"]),
             "title: %s" % yaml_str(fm_set["title"]),
             "orig_name: %s" % yaml_str(fm_set["orig_name"]),
             "uid_scheme: %s" % fm_set["uid_scheme"],
             "routeKey: %s" % fm_set["routeKey"],
             "category_uid: %s" % fm_set["category_uid"],
             "folder_id: %s" % folder_id,
             "privacy_level: %s" % fm_set["privacy_level"],
             "source_kind: %s" % fm_set["source_kind"],
             "created: %s" % fm_set["created"],
             "updated: %s" % fm_set["updated"],
             "ez2fmt: %d" % fm_set["ez2fmt"],
             "tags: %s" % yaml_list(fm_set["tags"]),
             "legacy_paths: %s" % yaml_list(fm_set["legacy_paths"])]
    if duplicate_of:
        lines.append("duplicate_of: %s" % duplicate_of)
    lines += keep
    lines.append("---")
    return "\n".join(lines) + "\n"


def _merge_dir(src: Path, dst: Path, vault: Path, receipt: dict):
    """把 src 的內容併進已存在的 dst:新檔搬過去、同內容檔刪 src 的、同名目錄遞迴;src 清空就移除。回 (merged, kept)。"""
    merged, kept = 0, 0
    for child in sorted(src.iterdir()):
        target = dst / child.name
        if not target.exists():
            os.rename(child, target); merged += 1
        elif child.is_dir() and target.is_dir():
            m, k = _merge_dir(child, target, vault, receipt); merged += m; kept += k
        elif child.is_file() and target.is_file() and hashlib.sha256(child.read_bytes()).hexdigest() == hashlib.sha256(target.read_bytes()).hexdigest():
            child.unlink(); merged += 1; receipt["files_deduped"] = receipt.get("files_deduped", 0) + 1
        else:
            kept += 1; receipt["errors"].append({"path": str(child.relative_to(vault)), "error": T("err_merge_differs", {"dst": str(target.relative_to(vault))})})
    if kept == 0 and not any(src.iterdir()):
        src.rmdir()
    return merged, kept


def write_indexes(vault: Path, fid_items: list, note_labels: dict | None, uid_items: list | None, now: str):
    """從資料夾身分表寫出四份衍生索引:folder-id-index、route-key-index、routes v2(契約第三複本,同步 folderPath)、labels。
    note_labels / uid_items 給 None 表示沿用檔裡既有的(reindex 用)。"""
    idx_dir = vault / ".ez2" / "index"; idx_dir.mkdir(parents=True, exist_ok=True)
    disp_dir = vault / ".ez2" / "display"; disp_dir.mkdir(parents=True, exist_ok=True)
    routes_dir = vault / ".ez2" / "routes"
    by_id = {f["folderId"]: f for f in fid_items}
    rk_items = [{"routeKey": f["routeKey"], "folderId": f["folderId"], "folderPath": f["folderPath"], "module": module_of(f["folderPath"].split("/")[0][4:5] if f["folderPath"].startswith("fld_") else f["folderPath"].split("/")[0]).upper()}
                for f in fid_items if f.get("kind") == "route" and f.get("routeKey")]
    old_labels = load_json(disp_dir / "labels.json", {})
    labels = {"schema": "2026-10-05.ez2-display-labels", "generatedAt": now,
              "folders": {f["folderId"]: {"title": f["title"], "i18n": ((old_labels.get("folders") or {}).get(f["folderId"]) or {}).get("i18n") or {"zh-TW": f["title"], "en": None, "ja": None, "ko": None}} for f in fid_items},
              "notes": note_labels if note_labels is not None else (old_labels.get("notes") or {})}
    (idx_dir / "folder-id-index.json").write_text(json.dumps({"schema_version": "2026-10-05.ez2-folder-id-index", "generatedAt": now, "items": fid_items}, ensure_ascii=False, indent=1), encoding="utf-8")
    (idx_dir / "route-key-index.json").write_text(json.dumps({"schema_version": "2026-10-05.ez2-route-key-index", "generatedAt": now, "items": rk_items}, ensure_ascii=False, indent=1), encoding="utf-8")
    if uid_items is not None:
        (idx_dir / "uid-index.json").write_text(json.dumps({"schema_version": "2026-10-05.ez2-uid-index", "generatedAt": now, "items": uid_items}, ensure_ascii=False, indent=1), encoding="utf-8")
    (disp_dir / "labels.json").write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")
    # routes v2(ez2-ai 外掛第一順位讀的那份):只同步 folderPath / parentFolderId,其餘欄位原樣;索引裡沒有的舊條目保留但標 status
    v2p = routes_dir / "obsidian-routes-v2.json"
    v2 = load_json(v2p, None)
    if isinstance(v2, dict) and isinstance(v2.get("routes"), list):
        seen = set()
        for r in v2["routes"]:
            f = by_id.get(r.get("folderId"))
            if f:
                r["folderPath"] = f["folderPath"]; r["parentFolderId"] = f["parentFolderId"]; r["level"] = f["level"]; seen.add(f["folderId"])
            else:
                r["status"] = "not-on-disk"
        for f in fid_items:
            if f["folderId"] not in seen and f.get("kind") in ("route", "group"):
                v2["routes"].append({"folderId": f["folderId"], "parentFolderId": f["parentFolderId"], "routeKey": f["routeKey"], "folderPath": f["folderPath"], "level": f["level"], "userEditableName": True})
        v2["generatedAt"] = now; v2["uidLayout"] = True
        v2p.write_text(json.dumps(v2, ensure_ascii=False, indent=1), encoding="utf-8")


def reindex(vault: Path) -> dict:
    """只重寫衍生索引(含 routes v2),不碰筆記、不改名;資料夾身分來自既有 folder-id-index。"""
    vault = vault.resolve()
    fid = load_json(vault / ".ez2" / "index" / "folder-id-index.json", {"items": []}).get("items", [])
    if not fid:
        raise SystemExit(T("err_no_folder_index"))
    missing = [f["folderPath"] for f in fid if not (vault / f["folderPath"]).is_dir()]
    write_indexes(vault, fid, None, None, time.strftime("%Y-%m-%d %H:%M:%S"))
    v2 = load_json(vault / ".ez2" / "routes" / "obsidian-routes-v2.json", {})
    return {"folders": len(fid), "paths_missing": missing, "routes_v2": len(v2.get("routes", [])) if isinstance(v2, dict) else None}


def apply_plan(plan: dict, backup_dir: Path, confirm_text: str) -> dict:
    accepted = {CONFIRM_TEXT, i18n_lib.t("aib.vault_convert.confirm_text", None, locale="zh-TW")}
    if confirm_text not in accepted:
        raise SystemExit(T("err_confirm_mismatch", {"text": CONFIRM_TEXT}))
    if plan["blockers"]:
        raise SystemExit(T("err_blockers", {"count": len(plan["blockers"])}))
    vault = Path(plan["vault"])
    backup_dir = backup_dir.resolve()
    if str(backup_dir).startswith(str(vault.resolve())):
        raise SystemExit(T("err_backup_inside"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tar_path = backup_dir / ("vault-before-uid-layout-%s.tgz" % stamp)
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(str(vault), arcname=vault.name)
    receipt = {"schema": "2026-10-05.ez2-uid-layout-receipt", "vault": str(vault), "backup_tar": str(tar_path),
               "started_at": iso(int(time.time() * 1000)), "legacy_copied": 0, "frontmatter_written": 0,
               "files_renamed": [], "dirs_renamed": [], "errors": []}

    # 0. 同內容副本先丟(計入 files_deduped)
    for d in plan.get("drops", []):
        p = vault / d["old_rel"]
        if p.is_file():
            p.unlink(); receipt["files_deduped"] = receipt.get("files_deduped", 0) + 1
    # 1. 舊庫導入
    for c in plan["legacy_copies"]:
        dst = vault / c["dst_rel"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copy2(c["src"], dst); receipt["legacy_copied"] += 1

    # 2. frontmatter(在舊路徑上寫)
    for n in plan["notes"]:
        p = vault / n["old_rel"]
        try:
            raw = p.read_bytes(); text = raw.decode("utf-8", errors="replace")
        except OSError as err:
            receipt["errors"].append({"path": n["old_rel"], "error": str(err)}); continue
        old_fm, body = split_frontmatter(text)
        new_text = render_frontmatter(old_fm, n["fm_set"], n["folder_id"], n["duplicate_of"]) + (body if old_fm is not None else "\n" + text if not text.startswith("\n") else text)
        tmp = p.with_name(p.name + ".tmp-uidlayout")
        tmp.write_text(new_text, encoding="utf-8"); os.replace(tmp, p)
        receipt["frontmatter_written"] += 1

    # 3. 檔案改名(在舊資料夾裡)
    for n in plan["notes"]:
        src = vault / n["old_rel"]
        dst = src.with_name(n["uid"] + ".md")
        if src == dst:
            continue
        if dst.exists():
            if hashlib.sha256(src.read_bytes()).hexdigest() == hashlib.sha256(dst.read_bytes()).hexdigest():
                src.unlink(); receipt.setdefault("files_deduped", 0); receipt["files_deduped"] += 1; continue
            receipt["errors"].append({"path": n["old_rel"], "error": T("err_dst_exists_differs", {"name": dst.name})}); continue
        os.rename(src, dst); receipt["files_renamed"].append([str(src.relative_to(vault)), str(dst.relative_to(vault))])

    # 4. 資料夾改名(最深的先)
    for f in sorted(plan["folders"], key=lambda r: (-r["level"], r["old_rel"])):
        # 父層還是舊名(因為由深到淺),所以用 舊父路徑 + 新 basename
        old = vault / f["old_rel"]
        if not old.exists():
            receipt["errors"].append({"path": f["old_rel"], "error": T("err_dir_missing")}); continue
        new = old.with_name(f["folderId"])
        if new == old:
            continue
        if new.exists():
            # 目的夾已在(例如另一台已轉好再 rsync 過來):遞迴併入,同名檔內容相同就丟舊的,不同就留著報錯
            merged, kept = _merge_dir(old, new, vault, receipt)
            receipt.setdefault("dirs_merged", []).append([str(old.relative_to(vault)), str(new.relative_to(vault)), merged, kept])
            continue
        os.rename(old, new); receipt["dirs_renamed"].append([str(old.relative_to(vault)), str(new.relative_to(vault))])

    # 5. 索引、顯示名、狀態、undo
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    fid_items = []
    for f in plan["folders"]:
        prev = {}
        for it in load_json(vault / ".ez2" / "index" / "folder-id-index.json", {"items": []}).get("items", []):
            if it.get("folderId") == f["folderId"]:
                prev = it; break
        legacy = list(dict.fromkeys((prev.get("legacyPaths") or []) + ([f["old_rel"]] if f["old_rel"] != f["new_rel"] else [])))
        fid_items.append({"folderId": f["folderId"], "routeKey": f["routeKey"], "folderPath": f["new_rel"],
                          "parentFolderId": f["parentFolderId"], "level": f["level"], "title": f["title"],
                          "legacyPaths": legacy, "status": "active", "kind": f["kind"]})
    note_labels = {n["uid"]: {"title": n["fm_set"]["title"], "i18n": {"zh-TW": n["fm_set"]["title"], "en": None, "ja": None, "ko": None}, "path": n["new_rel"]} for n in plan["notes"]}
    uid_items = [{"uid": n["uid"], "path": n["new_rel"], "title": n["fm_set"]["title"], "routeKey": n["fm_set"]["routeKey"], "folderId": n["folder_id"]} for n in plan["notes"]]
    write_indexes(vault, fid_items, note_labels, uid_items, now)
    ms_path = vault / ".ez2" / "migration-state.json"
    ms = load_json(ms_path, {})
    ms.update({"updated_at": now, "mode": "uid_layout", "formal_rename": True, "folder_moves": False, "uid_layout_applied_at": now,
               "uid_layout_receipt": str((vault / ".ez2" / "migration" / ("uid-layout-receipt-%s.json" % stamp)).relative_to(vault))})
    ms_path.write_text(json.dumps(ms, ensure_ascii=False, indent=1), encoding="utf-8")
    mig_dir = vault / ".ez2" / "migration"; mig_dir.mkdir(parents=True, exist_ok=True)
    receipt["finished_at"] = iso(int(time.time() * 1000))
    rpath = mig_dir / ("uid-layout-receipt-%s.json" % stamp)
    rpath.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.copy2(rpath, backup_dir / rpath.name)
    receipt["receipt_path"] = str(rpath)
    return receipt


# ---------------------------------------------------------------- undo
def undo(receipt_path: Path) -> dict:
    r = json.loads(receipt_path.read_text(encoding="utf-8"))
    vault = Path(r["vault"]); out = {"dirs_restored": 0, "files_restored": 0, "errors": []}
    for old, new in reversed(r["dirs_renamed"]):          # 淺的先回(反向)
        src, dst = vault / new, vault / old
        try:
            os.rename(src, dst); out["dirs_restored"] += 1
        except OSError as err:
            out["errors"].append({"dir": new, "error": str(err)})
    for old, new in reversed(r["files_renamed"]):
        src, dst = vault / new, vault / old
        try:
            os.rename(src, dst); out["files_restored"] += 1
        except OSError as err:
            out["errors"].append({"file": new, "error": str(err)})
    out["note"] = T("undo_note", {"backup": r["backup_tar"]})
    return out


# ---------------------------------------------------------------- verify
def verify(vault: Path) -> dict:
    vault = vault.resolve()
    res = {"vault": str(vault), "dirs_total": 0, "dirs_not_folder_id": [], "md_total": 0, "md_not_uid_name": [],
           "md_uid_mismatch": [], "md_no_uid": [], "uid_duplicates": [], "index_ok": {}, "ok": False}
    uids: dict[str, list] = {}
    rk = load_json(vault / ".ez2" / "index" / "route-key-index.json", {"items": []})
    lib_new = next((it["folderPath"] for it in rk.get("items", []) if it.get("routeKey") == "product.expert.agent_library"), None)
    def in_library(rel: str) -> bool:
        return bool(lib_new) and (rel == lib_new or rel.startswith(lib_new + "/"))
    fid0 = load_json(vault / ".ez2" / "index" / "folder-id-index.json", {"items": []})
    registered_keep = {it["folderPath"] for it in fid0.get("items", []) if it.get("kind") == "keep"}
    for dp, dn, fn in os.walk(vault):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith(".")]
        rel_dir = str(Path(dp).relative_to(vault))
        for d in dn:
            res["dirs_total"] += 1
            rel_d = str(Path(dp, d).relative_to(vault))
            if in_library(rel_dir):
                depth = len(Path(rel_d).parts) - len(Path(lib_new).parts)
                if depth == 1 and not (UID_RE.match(d) or d.startswith("_") or rel_d in registered_keep):
                    res["dirs_not_folder_id"].append(rel_d)
                elif depth == 1 and rel_d in registered_keep:
                    res.setdefault("library_dirs_without_card_uid", []).append(rel_d)
                continue
            if not FOLDER_ID_RE.match(d):
                res["dirs_not_folder_id"].append(rel_d)
        if in_library(rel_dir):
            continue
        for f in fn:
            if not f.endswith(".md"):
                continue
            res["md_total"] += 1
            rel = str(Path(dp, f).relative_to(vault))
            stem = f[:-3]
            if not UID_RE.match(stem):
                res["md_not_uid_name"].append(rel)
            fm, _ = split_frontmatter(Path(dp, f).read_text(encoding="utf-8", errors="replace"))
            kv = fm_lines_to_map(fm) if fm is not None else {}
            u = (kv.get("uid") or "").strip().strip('"\'')
            if not UID_RE.match(u):
                res["md_no_uid"].append(rel)
            else:
                uids.setdefault(u, []).append(rel)
                if UID_RE.match(stem) and u != stem:
                    res["md_uid_mismatch"].append(rel)
    res["uid_duplicates"] = [{"uid": k, "paths": v} for k, v in uids.items() if len(v) > 1]
    idx = vault / ".ez2" / "index"
    for name in ("folder-id-index.json", "route-key-index.json", "uid-index.json"):
        d = load_json(idx / name, None)
        res["index_ok"][name] = bool(d and d.get("items"))
    fid = load_json(idx / "folder-id-index.json", {"items": []})
    missing = [it["folderPath"] for it in fid.get("items", []) if not (vault / it["folderPath"]).is_dir()]
    res["index_paths_missing"] = missing
    rk = load_json(idx / "route-key-index.json", {"items": []})
    res["route_paths_missing"] = [it["routeKey"] for it in rk.get("items", []) if not (vault / it["folderPath"]).is_dir()]
    v2 = load_json(vault / ".ez2" / "routes" / "obsidian-routes-v2.json", None)
    res["routes_v2_stale"] = [r.get("folderId") for r in (v2.get("routes") if isinstance(v2, dict) else []) or [] if r.get("status") != "not-on-disk" and not (vault / str(r.get("folderPath", ""))).is_dir()]
    lab = load_json(vault / ".ez2" / "display" / "labels.json", {})
    res["labels"] = {"folders": len(lab.get("folders", {})), "notes": len(lab.get("notes", {}))}
    res["ok"] = (not res["dirs_not_folder_id"] and not res["md_not_uid_name"] and not res["md_uid_mismatch"]
                 and not res["md_no_uid"] and not res["uid_duplicates"] and all(res["index_ok"].values())
                 and not missing and not res["route_paths_missing"] and not res["routes_v2_stale"])
    return res


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=T("desc"), formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("--vault", required=True); p.add_argument("--contract", required=True)
    p.add_argument("--legacy"); p.add_argument("--legacy-map"); p.add_argument("--out", required=True)
    a = sub.add_parser("apply"); a.add_argument("--plan", required=True); a.add_argument("--backup-dir", required=True); a.add_argument("--confirm-text", required=True)
    v = sub.add_parser("verify"); v.add_argument("--vault", required=True)
    u = sub.add_parser("undo"); u.add_argument("--receipt", required=True)
    ri = sub.add_parser("reindex"); ri.add_argument("--vault", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "plan":
        lm = json.loads(Path(args.legacy_map).read_text(encoding="utf-8")) if args.legacy_map else None
        plan = build_plan(Path(args.vault), args.contract, Path(args.legacy) if args.legacy else None, lm)
        Path(args.out).write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"out": args.out, "stats": plan["stats"], "blockers": plan["blockers"][:5], "warnings": plan["warnings"][:5]}, ensure_ascii=False, indent=1))
        return 0 if not plan["blockers"] else 3
    if args.cmd == "apply":
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        rec = apply_plan(plan, Path(args.backup_dir), args.confirm_text)
        print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in rec.items()}, ensure_ascii=False, indent=1))
        return 0 if not rec["errors"] else 4
    if args.cmd == "verify":
        res = verify(Path(args.vault))
        print(json.dumps({k: (v if not isinstance(v, list) else (v if len(v) <= 8 else v[:8] + ["…+%d" % (len(v) - 8)])) for k, v in res.items()}, ensure_ascii=False, indent=1))
        return 0 if res["ok"] else 5
    if args.cmd == "undo":
        print(json.dumps(undo(Path(args.receipt)), ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "reindex":
        print(json.dumps(reindex(Path(args.vault)), ensure_ascii=False, indent=1))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
