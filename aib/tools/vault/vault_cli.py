#!/usr/bin/env python3
"""aib vault —— uid 庫的日常維護(10-16 維護者裁;受 aib 授權管理,加購項目 obsidian-labels,授權失效就不跑)。

    aib vault newpost --title 標題 [--folder folderId] [--body 正文] [--vault V] [--json]
        照 uid 版面的格式新增一篇筆記(<uid>.md + frontmatter),登記到 uid-index 與 labels;
        Obsidian 的 obsidian-labels 外掛「新增筆記」按鈕呼叫的就是這個。不給 --folder ⇒ 收件匣(core.inbox)。
    aib vault doctor [--fix --backup-dir DIR] [--vault V] [--json]
        找出不合 uid 版面的東西,預設只列不改;--fix 先把要動的檔打包到庫外再修:
          · 在 Obsidian 手動建的資料夾(不是 fld_ 名)⇒ 改成 folderId 並登記(標題 = 原名)
          · 手動建的筆記(例「未命名.md」)⇒ 補 uid、改名 <uid>.md,原名留在 title / aliases,
            正文裡 [[原名]] 這種連結改成 [[uid|原名]]
          · 檔名是 uid 但 frontmatter 沒有 uid ⇒ 補上
          · 索引裡有、硬碟上已不在的資料夾 ⇒ 從索引拿掉
          · uid 重複、檔名與 frontmatter 的 uid 不一致 ⇒ 只列出來,要人看
          · frontmatter 的 updated 是「本機時間 + 空格」(2026-10-07 00:12:16,舊外掛 update-time-on-edit 寫的)
            ⇒ 照本機時區換成 ISO(2026-10-06T16:12:16.000Z);只改那一行,不動正文與其他欄位
          · updated 只有日期、或沒有秒與時區(2026-08-10T18:48)⇒ 換算要猜,只在 notices 列數量,不改、不算問題
離開碼:0 沒問題 / 已修好、1 有問題(只列沒修)、2 用法或錯誤、3 沒有授權。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                      # = aib/
sys.path.insert(0, str(HERE))
import uid_layout_convert as U  # noqa: E402

ADDON = "obsidian-labels"                   # 維護者 10-16 裁:Obsidian 外掛與 aib vault 共用一個加購項目
LINK_RE = re.compile(r"\[\[([^\[\]|#^]+)((?:#[^\[\]|]*)?)(\|[^\[\]]*)?\]\]")


def T(key, params=None):
    return U.i18n_lib.t("aib.vault_cli." + key, params, locale=U.LOC)


class VaultError(Exception):
    pass


# ---------------------------------------------------------------- 授權
def licensed():
    """(有沒有授權, 原因);判準跟 aib 載入付費外掛、aib plugins licensed 同一個(aib_plugins.licensed)。"""
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    try:
        import aib_plugins
        return aib_plugins.licensed({"requires_addon": ADDON})
    except Exception as exc:  # noqa: BLE001 —— 授權模組壞掉 ⇒ 當沒授權
        return False, "license-error:%s" % type(exc).__name__


def resolve_vault(explicit=""):
    raw = explicit or os.environ.get("EZ2_VAULT_DIR") or os.environ.get("EZ2_VAULT_ROOT") or ""
    if not raw:
        raise VaultError(T("err_no_vault"))
    v = Path(os.path.expanduser(raw)).resolve()
    if not (v / ".ez2" / "index" / "folder-id-index.json").is_file():
        raise VaultError(T("err_not_uid_vault", {"vault": str(v)}))
    return v


# ---------------------------------------------------------------- 共用
def _load(vault: Path):
    fid = json.loads((vault / ".ez2/index/folder-id-index.json").read_text(encoding="utf-8"))["items"]
    uidx = U.load_json(vault / ".ez2/index/uid-index.json", {"items": []}).get("items") or []
    lab = U.load_json(vault / ".ez2/display/labels.json", {})
    return fid, uidx, dict(lab.get("notes") or {})


def _save(vault: Path, fid, uidx, notes_lab):
    U.write_indexes(vault, fid, notes_lab, uidx, time.strftime("%Y-%m-%d %H:%M:%S"))


def _route_of(folder_id: str, by_id: dict) -> str:
    """最近的路由祖先的 routeKey;都不是路由 ⇒ core.inbox(跟轉換時同一條規則)。"""
    cur = by_id.get(folder_id)
    while cur:
        if cur.get("kind") == "route" and cur.get("routeKey"):
            return cur["routeKey"]
        cur = by_id.get(cur.get("parentFolderId") or "")
    return "core.inbox"


def _fm_set(uid, title, folder_id, by_id, ms, orig_name, source_kind="manual", old_kv=None):
    old_kv = old_kv or {}
    rk = (old_kv.get("routeKey") or "").strip().strip("\"'") or _route_of(folder_id, by_id)
    cat = (old_kv.get("category_uid") or "").strip().strip("\"'") or ("cat_" + re.sub(r"[^a-z0-9_]", "_", rk.replace(".", "_")))
    aliases = [uid] + ([title] if title and title != uid else [])
    return {"uid": uid, "aliases": aliases, "title": title, "orig_name": orig_name, "uid_scheme": "ez2-note-time-base36-v1",
            "routeKey": rk, "category_uid": cat, "privacy_level": (old_kv.get("privacy_level") or "internal").strip("\"'"),
            "source_kind": (old_kv.get("source_kind") or source_kind).strip("\"'"),
            "created": (old_kv.get("created") or "").strip("\"'") or U.iso(ms), "updated": U.iso(ms),
            "ez2fmt": 2, "tags": [], "legacy_paths": []}


def _inbox(fid) -> dict:
    by_id = {i["folderId"]: i for i in fid}
    hit = next((i for i in fid if i.get("routeKey") == "core.inbox" and i.get("status", "active") == "active"), None)
    hit = hit or next((i for i in fid if i.get("level") == 1 and "Inbox" in (i.get("title") or "")), None)
    if hit is None:
        raise VaultError(T("err_no_inbox"))
    return by_id[hit["folderId"]]


# ---------------------------------------------------------------- newpost
def newpost(vault: Path, title: str, folder: str = "", body: str = "", now_ms: int | None = None) -> dict:
    title = re.sub(r"\s+", " ", (title or "").strip()) or T("untitled")
    vault = Path(vault).resolve()
    fid, uidx, notes_lab = _load(vault)
    by_id = {i["folderId"]: i for i in fid}
    if folder:
        if folder not in by_id:
            raise VaultError(U.T("err_folder_unknown", {"folder": folder}))
        frec = by_id[folder]
    else:
        frec = _inbox(fid)
    d = vault / frec["folderPath"]
    if not d.is_dir():
        raise VaultError(U.T("err_dir_missing"))
    ms = int(now_ms if now_ms is not None else time.time() * 1000)
    used = {i.get("uid") for i in uidx} | {p.stem for p in vault.rglob("*.md") if U.UID_RE.match(p.stem)}
    uid = U.mint_uid(ms, used)
    path = d / (uid + ".md")
    fm = U.render_frontmatter(None, _fm_set(uid, title, frec["folderId"], by_id, ms, ""), frec["folderId"], "")   # 新筆記沒有原名
    text = fm + ("\n" + body.rstrip() + "\n" if body.strip() else "\n")
    with open(path, "x", encoding="utf-8") as fh:          # 不覆蓋:同名就錯
        fh.write(text)
    rel = str(path.relative_to(vault))
    uidx.append({"uid": uid, "path": rel})
    notes_lab[uid] = {"title": title, "path": rel}
    _save(vault, fid, uidx, notes_lab)
    return {"uid": uid, "path": rel, "abs_path": str(path), "title": title, "folderId": frec["folderId"]}


# ---------------------------------------------------------------- doctor
ISO_Z_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z")
LOCAL_TIME_RE = re.compile(r"([\"']?)(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\1")
UPDATED_LINE_RE = re.compile(r"(?m)^updated:[ \t]*(.*?)[ \t]*$")


def _local_to_iso(value: str) -> str:
    """「2026-10-07 00:12:16」照本機時區 ⇒ UTC 的 ISO(毫秒補 .000,跟 note-format 寫的同形)。"""
    return U.iso(int(time.mktime(time.strptime(value, "%Y-%m-%d %H:%M:%S")) * 1000))


def _updated_scan(vault: Path) -> tuple[list, list]:
    """frontmatter updated 不是 ISO 的筆記 ⇒ (能確定換算的 [{path, old, new}], 要猜的 [{path, old}])。"""
    fixable, guess = [], []
    for dp, dn, fn in os.walk(vault):
        dn[:] = sorted(x for x in dn if x not in U.SKIP_DIRS and not x.startswith("."))
        for f in sorted(fn):
            if not f.endswith(".md"):
                continue
            p = Path(dp, f)
            fm, _body = U.split_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
            m = UPDATED_LINE_RE.search(fm or "")
            if not m or not m.group(1) or ISO_Z_RE.fullmatch(m.group(1)):
                continue
            rel = str(p.relative_to(vault))
            lm = LOCAL_TIME_RE.fullmatch(m.group(1))
            if lm:
                fixable.append({"path": rel, "old": m.group(1), "new": _local_to_iso(lm.group(2))})
            else:
                guess.append({"path": rel, "old": m.group(1)})
    return fixable, guess


def _problems(vault: Path) -> dict:
    res = U.verify(vault, cards_path=str(vault / ".ez2" / "no-cards-check.json"))
    keys = ("dirs_not_folder_id", "md_not_uid_name", "md_no_uid", "md_uid_mismatch", "uid_duplicates", "index_paths_missing",
            "route_paths_missing")
    out = {k: res.get(k) or [] for k in keys}
    out["md_updated_local_time"] = _updated_scan(vault)[0]
    return out


def _fix_updated(vault: Path, entry: dict) -> bool:
    """只換 frontmatter 那一行 updated;讀到的值跟掃描時不同(期間有人改過)⇒ 不動、回 False。"""
    p = vault / entry["path"]
    text = p.read_text(encoding="utf-8")
    fm, body = U.split_frontmatter(text)
    m = UPDATED_LINE_RE.search(fm or "")
    if not m or m.group(1) != entry["old"]:
        return False
    new_fm = fm[:m.start()] + "updated: " + entry["new"] + fm[m.end():]
    head = text[:len(text) - len(body)]
    p.write_text(head.replace(fm, new_fm, 1) + body, encoding="utf-8")
    return True


def _rewrite_links(vault: Path, old_stem: str, uid: str, skip: Path) -> int:
    """正文裡 [[原名]] / [[原名#標題]] / [[原名|顯示]] ⇒ [[uid…|顯示或原名]];只改完全同名的。回改了幾處。"""
    n = 0
    for dp, dn, fn in os.walk(vault):
        dn[:] = [x for x in dn if x not in U.SKIP_DIRS and not x.startswith(".")]
        for f in fn:
            p = Path(dp, f)
            if not f.endswith(".md") or p == skip:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            if old_stem not in text:
                continue
            count = [0]

            def sub(m):
                if U._nfc(m.group(1).strip()) != U._nfc(old_stem):
                    return m.group(0)
                count[0] += 1
                return "[[%s%s%s]]" % (uid, m.group(2) or "", m.group(3) or "|" + old_stem)
            new = LINK_RE.sub(sub, text)
            if count[0]:
                p.write_text(new, encoding="utf-8")
                n += count[0]
    return n


def doctor(vault: Path, fix: bool = False, backup_dir: Path | None = None, now_ms: int | None = None) -> dict:
    vault = Path(vault).resolve()                 # 捷徑解開再比(/var → /private/var),「備份放庫裡」才擋得住
    found = _problems(vault)
    out = {"vault": str(vault), "problems": found, "fixed": [], "left": [], "backup": None,
           "ok": not any(found.values()),
           "notices": {"md_updated_ambiguous": _updated_scan(vault)[1], "timezone": time.strftime("%z")}}
    if not fix or out["ok"]:
        return out
    if backup_dir is None:
        raise VaultError(U.T("err_backup_required"))
    backup_dir = backup_dir.resolve()
    if backup_dir == vault or vault in backup_dir.parents:
        raise VaultError(U.T("err_backup_inside"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    tgz = backup_dir / ("doctor-%s.tgz" % time.strftime("%Y%m%d-%H%M%S"))
    touched = sorted(set(found["dirs_not_folder_id"]) | set(found["md_not_uid_name"]) | set(found["md_no_uid"])
                     | {e["path"] for e in found["md_updated_local_time"]})
    with tarfile.open(tgz, "w:gz") as tar:                   # 要動的檔 + 索引,原樣打包
        for rel in touched + [".ez2/index/folder-id-index.json", ".ez2/index/uid-index.json", ".ez2/display/labels.json"]:
            if (vault / rel).exists():
                tar.add(str(vault / rel), arcname=rel)
    out["backup"] = str(tgz)
    ms = int(now_ms if now_ms is not None else time.time() * 1000)
    # 1) 索引裡有、硬碟上沒有的資料夾 ⇒ 拿掉(由上往下:父夾拿掉時子夾一起)
    fid, _u, _l = _load(vault)
    for rel in sorted(found["index_paths_missing"], key=len):
        it = next((i for i in fid if i["folderPath"] == rel), None)
        if it and not (vault / rel).exists():
            try:
                U.folder_retire(vault, it["folderId"], None)
                out["fixed"].append({"kind": "index_path_missing", "path": rel})
            except SystemExit as exc:
                out["left"].append({"kind": "index_path_missing", "path": rel, "why": str(exc.code)})
            fid, _u, _l = _load(vault)
    # 2) 手建資料夾 ⇒ 改成 folderId、登記(由上往下,每改一個重新算路徑)
    for _ in range(len(found["dirs_not_folder_id"]) + 1):
        dirs = _problems(vault)["dirs_not_folder_id"]
        if not dirs:
            break
        rel = sorted(dirs, key=lambda r: (len(Path(r).parts), r))[0]
        try:
            out["fixed"].append(dict(_adopt_folder(vault, rel), kind="folder"))
        except VaultError as exc:
            out["left"].append({"kind": "folder", "path": rel, "why": str(exc)})
            break
    # 3) 手建筆記 ⇒ 補 uid、改名、改連結;4) uid 名但缺 frontmatter uid ⇒ 補
    fid, uidx, notes_lab = _load(vault)
    by_id = {i["folderId"]: i for i in fid}
    used = {i.get("uid") for i in uidx} | {p.stem for p in vault.rglob("*.md") if U.UID_RE.match(p.stem)}
    cur = _problems(vault)
    for rel in dict.fromkeys(cur["md_not_uid_name"] + cur["md_no_uid"]):   # 兩份清單都有的(檔名與 uid 都缺)只處理一次
        p = vault / rel
        text = p.read_text(encoding="utf-8", errors="replace")
        old_fm, body = U.split_frontmatter(text)
        kv = U.fm_lines_to_map(old_fm) if old_fm is not None else {}
        named_uid = U.UID_RE.match(p.stem) is not None
        have = (kv.get("uid") or "").strip().strip("\"'")
        uid = p.stem if named_uid else (have if U.UID_RE.match(have) and have not in used else U.mint_uid(int(p.stat().st_mtime * 1000), used))
        used.add(uid)
        title = (kv.get("title") or "").strip().strip("\"'") or (p.stem if not named_uid else uid)
        parent = p.parent.name if U.FOLDER_ID_RE.match(p.parent.name) else ""
        fs = _fm_set(uid, title, parent, by_id, ms, kv.get("orig_name", "").strip("\"'") or p.name, old_kv=kv)
        fs["created"] = (kv.get("created") or "").strip("\"'") or U.iso(int(p.stat().st_mtime * 1000))
        fs["tags"] = U.fm_list(old_fm, "tags") if old_fm else []
        new_text = U.render_frontmatter(old_fm, fs, parent, "") + body
        dst = p.with_name(uid + ".md")
        if dst != p and dst.exists():
            out["left"].append({"kind": "note", "path": rel, "why": "exists:%s" % dst.name})
            continue
        p.write_text(new_text, encoding="utf-8")
        links = 0
        if dst != p:
            os.replace(p, dst)
            links = _rewrite_links(vault, p.stem, uid, dst)
        new_rel = str(dst.relative_to(vault))
        uidx = [i for i in uidx if i.get("uid") != uid] + [{"uid": uid, "path": new_rel}]
        notes_lab[uid] = {"title": title, "path": new_rel}
        out["fixed"].append({"kind": "note", "path": rel, "new_path": new_rel, "uid": uid, "links_rewritten": links})
    _save(vault, fid, uidx, notes_lab)
    # 5) updated「本機時間 + 空格」⇒ ISO(只改那一行;筆記在前面幾步被改名的,用改名後的路徑)
    for e in _updated_scan(vault)[0]:
        if _fix_updated(vault, e):
            out["fixed"].append({"kind": "updated", "path": e["path"], "old": e["old"], "new": e["new"]})
        else:
            out["left"].append({"kind": "updated", "path": e["path"], "why": "changed"})
    after = _problems(vault)
    for k in ("uid_duplicates", "md_uid_mismatch"):
        for item in after[k]:
            out["left"].append({"kind": k, "item": item})
    out["problems_after"] = after
    out["ok"] = not any(v for k, v in after.items())
    return out


def _adopt_folder(vault: Path, rel: str) -> dict:
    """手建的資料夾 rel ⇒ 改名成 folderId 並登記;標題 = 原名。父夾必須已經是登記過的 folderId 或庫根。"""
    p = vault / rel
    parent_rel = str(Path(rel).parent) if str(Path(rel).parent) != "." else ""
    fid, uidx, notes_lab = _load(vault)
    by_path = {i["folderPath"]: i for i in fid}
    by_id = {i["folderId"]: i for i in fid}
    par = by_path.get(parent_rel) if parent_rel else None
    if parent_rel and par is None:
        raise VaultError(U.T("err_folder_unknown", {"folder": parent_rel}))
    title = U._nfc(p.name)
    if par:
        legacy = U._legacy_of(par, by_id) + "/" + title
        mod = par["folderId"].split("_")[1]
        new_id, kind, rk, level = "fld_%s_sub_%s" % (mod, U.sha12(legacy)), "sub", "", int(par.get("level") or 1) + 1
    else:
        legacy = title
        mod = U.module_of(title)
        new_id, kind, rk, level = "fld_%s_group_%s" % (mod, U.sha12(legacy)), "group", "folder.group.%s.%s" % (mod, U.sha12(legacy)[:8]), 1
    if new_id in by_id:
        raise VaultError(U.T("err_folder_id_taken", {"folder": new_id}))
    new_path = (par["folderPath"] + "/" if par else "") + new_id
    if (vault / new_path).exists():
        raise VaultError(U.T("err_folder_id_taken", {"folder": new_id}))
    os.replace(p, vault / new_path)
    fid.append({"folderId": new_id, "routeKey": rk, "folderPath": new_path, "parentFolderId": par["folderId"] if par else "",
                "level": level, "title": title, "legacyPaths": [legacy], "status": "active", "kind": kind})
    # 裡面的筆記路徑跟著變:uid-index / labels 的 path 換前綴
    old_prefix, new_prefix = rel + "/", new_path + "/"
    for it in uidx:
        if str(it.get("path", "")).startswith(old_prefix):
            it["path"] = new_prefix + it["path"][len(old_prefix):]
    for v in notes_lab.values():
        if isinstance(v, dict) and str(v.get("path", "")).startswith(old_prefix):
            v["path"] = new_prefix + v["path"][len(old_prefix):]
    _save(vault, fid, uidx, notes_lab)
    return {"path": rel, "new_path": new_path, "folderId": new_id, "title": title}


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="aib vault", description=T("help_desc"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("newpost", help=T("help_newpost"))
    n.add_argument("--title", default="", help=T("help_title"))
    n.add_argument("--folder", default="", help=T("help_folder"))
    n.add_argument("--body", default="", help=T("help_body"))
    d = sub.add_parser("doctor", help=T("help_doctor"))
    d.add_argument("--fix", action="store_true", help=T("help_fix"))
    d.add_argument("--backup-dir", help=U.T("help_folder_backup"))
    for p in (n, d):
        p.add_argument("--vault", default="", help=T("help_vault"))
        p.add_argument("--json", action="store_true", help=T("help_json"))
    args = ap.parse_args(argv)
    ok, why = licensed()
    if not ok:
        msg = {"licensed": False, "addon": ADDON, "why": why}
        print(json.dumps(msg) if args.json else T("err_not_licensed", {"addon": ADDON, "why": why}),
              file=sys.stdout if args.json else sys.stderr)
        return 3
    try:
        vault = resolve_vault(args.vault)
        if args.cmd == "newpost":
            res = newpost(vault, args.title, args.folder, args.body)
            print(json.dumps(res, ensure_ascii=False) if args.json else T("newpost_done", {"title": res["title"], "path": res["path"]}))
            return 0
        res = doctor(vault, args.fix, Path(args.backup_dir) if args.backup_dir else None)
    except (VaultError, SystemExit) as exc:
        if isinstance(exc, SystemExit) and not isinstance(exc.code, str):
            raise
        print(str(exc.code if isinstance(exc, SystemExit) else exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(T("doctor_ok") if res["ok"] else T("doctor_found"))
        for k, v in (res.get("problems_after") or res["problems"]).items():
            if v:
                print("  %s: %d" % (k, len(v)))
                for item in v[:5]:
                    print("    - %s" % (item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)))
        for f in res["fixed"]:
            print("  ✓ %s" % json.dumps(f, ensure_ascii=False))
        amb = (res.get("notices") or {}).get("md_updated_ambiguous") or []
        if amb:
            print("  " + T("doctor_updated_ambiguous", {"count": len(amb)}))
        if res["backup"]:
            print("  " + T("doctor_backup", {"path": res["backup"]}))
        if not res["ok"] and not args.fix:
            print(T("doctor_hint_fix"))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
