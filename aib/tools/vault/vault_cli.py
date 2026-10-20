#!/usr/bin/env python3
"""aib vault —— uid 庫的日常維護(10-16 維護者裁;受 aib 授權管理,加購項目 obsidian-labels,授權失效就不跑)。

    aib vault newpost --title 標題 [--folder folderId] [--body 正文] [--meta-json JSON] [--appendable] [--vault V] [--json]
        照 uid 版面的格式新增一篇筆記(<uid>.md + frontmatter),登記到 uid-index 與 labels;
        Obsidian 的 obsidian-labels 外掛「新增筆記」按鈕呼叫的就是這個。不給 --folder ⇒ 收件匣(core.inbox)。
        (0.1.25)--meta-json:白名單欄位(META_TYPES)寫進 frontmatter;核心管的欄位(uid / routeKey / aliases / created…)
        一律不准給;privacy_level 只收 public / internal / restricted / sensitive 且只能比預設(internal)高;
        字串裡的控制字元(含換行、U+2028/2029)、方向字元、零寬字清掉;型別不對 / 不認得的鍵 / 壞 UTF-8 ⇒ 整筆拒。
        --appendable:frontmatter 標 append_route = 這篇的 routeKey,之後才准 aib vault append。
    aib vault append --uid U (--body 正文 | --body-file 檔|-) [--vault V] [--json]
        (0.1.25)把正文接到既有那篇的尾巴、更新 updated 與索引。只准 newpost --appendable 建的、
        而且還在同一條寫入路由底下的那篇(append_route = 目前所在位置最近的路由夾,含 folder-add --route-key 的子夾);
        檔內 uid 要等於 --uid;單次正文上限 64 KB。
        注意:這些標記只防用錯,擋不住同帳號的程式直接改檔。
    aib vault doctor [--fix --backup-dir DIR] [--vault V] [--json]
        找出不合 uid 版面的東西,預設只列不改;--fix 先把要動的檔打包到庫外再修:
          · 在 Obsidian 手動建的資料夾(不是 fld_ 名)⇒ 改成 folderId 並登記(標題 = 原名)
          · 手動建的筆記(例「未命名.md」)⇒ 補 uid、改名 <uid>.md,原名留在 title / aliases,
            正文裡 [[原名]] 這種連結改成 [[uid|原名]]
          · 在 Obsidian 只改名 / 搬走(索引記的舊路徑已不在、全庫只有這篇帶這個 uid)⇒ 當搬家:沿用原 uid、改回 <uid>.md;
            兩篇帶同一個 uid(複製出來的)⇒ 換新 uid,並拿掉 append_route 與 record_id(複本不是那筆紀錄)
          · 檔名是 uid 但 frontmatter 沒有 uid ⇒ 補上
          · 索引裡有、硬碟上已不在的資料夾 ⇒ 從索引拿掉
          · uid 重複、檔名與 frontmatter 的 uid 不一致 ⇒ 只列出來,要人看
          · frontmatter 的 updated 是「本機時間 + 空格」(2026-10-07 00:12:16,舊外掛 update-time-on-edit 寫的)
            ⇒ 照本機時區換成 ISO(2026-10-06T16:12:16.000Z);只改那一行,不動正文與其他欄位
          · updated 只有日期、或沒有秒與時區(2026-08-10T18:48)⇒ 換算要猜,只在 notices 列數量,不改、不算問題
          · (0.1.18)uid 筆記沒登記在 uid-index / labels(例 aib memory write 舊版寫的)⇒ 補登記
          · (0.1.18)筆記在 Obsidian 被搬走:uid-index / labels 的路徑、frontmatter folder_id 過期 ⇒ 改成現在的位置
            (routeKey 是寫入語意,不跟著位置改;跟所在路由不同的只在 notices 列)
          · (0.1.18)labels.json 的標題跟 frontmatter title 不同 ⇒ 以 frontmatter 為準改 labels(使用者在屬性改標題的情況)
          · (0.1.18)aliases 裡沒有 title ⇒ 補進去(Obsidian 快速切換只比對檔名與別名,沒有就用標題找不到)
    aib vault mkdir --title 名稱 [--parent folderId] [--vault V] [--json]
        新增 uid 資料夾並登記(Obsidian 的 obsidian-labels「新增資料夾」按鈕呼叫的就是這個)
    aib vault title <路徑…> [--stdin] [--vault V] [--json]
        (0.1.29)只讀:把 Vault 裡的路徑(fld_xxx/…/<uid>.md)換成標題路徑(06-工作區 Work/…/筆記標題),
        附 obsidian://open 連結;對話面板用它把紀錄裡的路徑顯示成看得懂的名字。絕對路徑或 Vault 內相對路徑都收,
        Vault 外的回 in_vault false。標題只從 labels.json 取,不開筆記本身(不讀正文)。--stdin 每行一個路徑。
        這支不驗 obsidian-labels 授權(對話面板是免費核心);下面的離開碼 3 只適用其他子指令。
離開碼:0 沒問題 / 已修好、1 有問題(只列沒修)、2 用法或錯誤、3 沒有授權。
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import math
import os
import re
import sys
import tarfile
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                      # = aib/
sys.path.insert(0, str(HERE))
import uid_layout_convert as U  # noqa: E402

ADDON = "obsidian-labels"                   # 維護者 10-16 裁:Obsidian 外掛與 aib vault 共用一個加購項目
LINK_RE = re.compile(r"\[\[([^\[\]|#^]+)((?:#[^\[\]|]*)?)(\|[^\[\]]*)?\]\]")
# 0.1.25 newpost --meta-json 的白名單:鍵 ⇒ 型別。不在這裡的鍵(含核心自己管的 uid / routeKey / aliases / created…)一律拒。
META_TYPES = {"record_id": str, "record_type": str, "record_at": str, "level": str, "origin": str, "ai_memory": str,
              "privacy_level": str, "people": list, "projects": list, "tasks": list, "source": list}
META_ENUM = {"origin": ("internal", "external"), "ai_memory": ("keep", "skip")}
PRIVACY_RANK = {"public": 0, "internal": 1, "restricted": 2, "sensitive": 3}   # 跟 brain_write_plan.PRIVACY_ORDER 同一套(測試 I2 釘住)
PRIVACY_DEFAULT = "internal"
# 控制字元(含換行)、方向字元、零寬字、行 / 段分隔(U+2028/2029:splitlines 會在這裡斷行,能偽造 frontmatter 鍵)
INVISIBLE_RE = re.compile("[\x00-\x1f\x7f-\x9f\u00ad\u061c\u180e\u200b-\u200f\u2028-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
APPEND_KEY = "append_route"
APPEND_MAX = 64 * 1024
TITLE_MAX = 1000                            # aib vault title 一次最多幾個路徑


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


@contextlib.contextmanager
def _locked(vault: Path):
    """aib vault 的 newpost / append / mkdir / doctor --fix 互相排隊(都會讀改寫索引)。
    只防兩支 aib vault 同時寫;Obsidian 或其他程式直接改檔不受這把鎖管。鎖檔不截斷、不跟符號連結。"""
    d = Path(vault) / ".ez2"
    d.mkdir(exist_ok=True)
    fd = os.open(str(d / ".write.lock"), os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _clean(s: str) -> str:
    return INVISIBLE_RE.sub("", s)          # 單獨的代理字元由 _newpost 整段 encode 時擋(建檔前)


def check_meta(meta) -> dict:
    """--meta-json ⇒ 清過的 {鍵: 值};不認得的鍵、型別不對、列舉值不對、privacy_level 比預設低 ⇒ VaultError(整筆拒)。"""
    if not isinstance(meta, dict):
        raise VaultError(T("err_meta_not_object"))
    out = {}
    for k, v in meta.items():
        want = META_TYPES.get(k)
        if want is None:
            raise VaultError(T("err_meta_key", {"key": k, "allowed": ", ".join(META_TYPES)}))
        if want is str:
            if not isinstance(v, str):
                raise VaultError(T("err_meta_want_string", {"key": k}))
            v = _clean(v)
        elif k == "source":
            if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
                raise VaultError(T("err_meta_want_objects", {"key": k}))
            items = []
            for x in v:
                one = {}
                for kk, vv in x.items():
                    if isinstance(vv, bool) or not isinstance(vv, (str, int, float)) or (isinstance(vv, float) and not math.isfinite(vv)):
                        raise VaultError(T("err_meta_want_objects", {"key": k}))
                    one[_clean(str(kk))] = _clean(vv) if isinstance(vv, str) else vv
                items.append(one)
            v = items
        else:
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise VaultError(T("err_meta_want_strings", {"key": k}))
            v = [_clean(x) for x in v]
        if k in META_ENUM and v not in META_ENUM[k]:
            raise VaultError(T("err_meta_enum", {"key": k, "value": v, "ok": ", ".join(META_ENUM[k])}))
        if k == "privacy_level":
            if v not in PRIVACY_RANK:
                raise VaultError(T("err_meta_enum", {"key": k, "value": v, "ok": ", ".join(PRIVACY_RANK)}))
            if PRIVACY_RANK[v] < PRIVACY_RANK[PRIVACY_DEFAULT]:
                raise VaultError(T("err_meta_privacy_lower", {"value": v, "base": PRIVACY_DEFAULT}))
        out[k] = v
    return out


def _with_extra(fm: str, extra: list) -> str:
    """render_frontmatter 的結果(以「---\n」結尾)⇒ 在結尾那行前補上 extra 行。"""
    head, tail = fm[:-len("---\n")], "---\n"
    return head + "".join(ln + "\n" for ln in extra) + tail


# ---------------------------------------------------------------- newpost
def newpost(vault: Path, title: str, folder: str = "", body: str = "", now_ms: int | None = None,
            meta=None, appendable: bool = False) -> dict:
    title = re.sub(r"\s+", " ", (title or "").strip()) or T("untitled")
    vault = Path(vault).resolve()
    meta = check_meta(meta) if meta is not None else {}
    with _locked(vault):
        return _newpost(vault, title, folder, body, now_ms, meta, appendable)


def _newpost(vault: Path, title: str, folder: str, body: str, now_ms, meta: dict, appendable: bool) -> dict:
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
        raise VaultError(U.T("err_dir_missing_at", {"path": str(d)}))
    ms = int(now_ms if now_ms is not None else time.time() * 1000)
    used = {i.get("uid") for i in uidx} | {p.stem for p in vault.rglob("*.md") if U.UID_RE.match(p.stem)}
    uid = U.mint_uid(ms, used)
    path = d / (uid + ".md")
    fs = _fm_set(uid, title, frec["folderId"], by_id, ms, "")
    if "privacy_level" in meta:
        fs["privacy_level"] = meta["privacy_level"]
    fm = U.render_frontmatter(None, fs, frec["folderId"], "")   # 新筆記沒有原名
    extra = ["%s: %s" % (k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items() if k != "privacy_level"]
    if appendable:
        extra.append("%s: %s" % (APPEND_KEY, _append_route(frec["folderId"], by_id)))
    if extra:
        fm = _with_extra(fm, extra)
    text = fm + ("\n" + body.rstrip() + "\n" if body.strip() else "\n")
    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError:
        raise VaultError(T("err_text_not_utf8", {"key": "title / body / --meta-json"}))
    with open(path, "xb") as fh:                          # 不覆蓋:同名就錯
        try:
            fh.write(data)
        except BaseException:
            fh.close()
            path.unlink()
            raise
    rel = str(path.relative_to(vault))
    uidx.append({"uid": uid, "path": rel})
    notes_lab[uid] = {"title": title, "path": rel}
    _save(vault, fid, uidx, notes_lab)
    return {"uid": uid, "path": rel, "abs_path": str(path), "title": title, "folderId": frec["folderId"]}


# ---------------------------------------------------------------- append
def _append_route(folder_id: str, by_id: dict) -> str:
    """最近一層「有寫入路由」的夾(轉換時的路由夾,或 folder-add --route-key 掛了代號的子夾)的 routeKey;都沒有 ⇒ core.inbox。
    比 _route_of 細:同一條上層路由底下兩個掛代號的子夾之間搬檔,也算換了路由。"""
    cur = by_id.get(folder_id)
    while cur:
        if U.is_routed(cur):
            return cur["routeKey"]
        cur = by_id.get(cur.get("parentFolderId") or "")
    return "core.inbox"


def append(vault: Path, uid: str, body: str, now_ms: int | None = None) -> dict:
    """正文接到 uid 那篇尾巴;只准 newpost --appendable 建、還在同一條路由底下的那篇。更新 updated 與索引。"""
    vault = Path(vault).resolve()
    if not isinstance(body, str) or not body.strip():
        raise VaultError(T("err_append_empty"))
    try:
        size = len(body.encode("utf-8"))
    except UnicodeEncodeError:
        raise VaultError(T("err_text_not_utf8", {"key": "--body"}))
    if size > APPEND_MAX:
        raise VaultError(T("err_append_too_big", {"size": size, "max": APPEND_MAX}))
    with _locked(vault):
        fid, uidx, notes_lab = _load(vault)
        hit = next((i for i in uidx if i.get("uid") == uid), None)
        path = vault / hit["path"] if hit else None
        if path is None or not path.is_file():
            raise VaultError(T("err_append_not_found", {"uid": uid}))
        text = path.read_text(encoding="utf-8")
        fm, rest = U.split_frontmatter(text)
        kv = U.fm_lines_to_map(fm or "")
        mark = kv.get(APPEND_KEY, "").strip().strip("\"'")
        by_id = {i["folderId"]: i for i in fid}
        by_path = {i["folderPath"]: i for i in fid}
        frec = by_path.get(str(Path(hit["path"]).parent))
        here = _append_route(frec["folderId"], by_id) if frec else ""
        if kv.get("uid", "").strip().strip("\"'") != uid:          # 索引指錯篇 ⇒ 不寫
            raise VaultError(T("err_append_not_found", {"uid": uid}))
        if not mark:
            raise VaultError(T("err_append_not_appendable", {"uid": uid}))
        if mark != here:
            raise VaultError(T("err_append_route_moved", {"uid": uid, "mark": mark, "here": here or "?"}))
        ms = int(now_ms if now_ms is not None else time.time() * 1000)
        fm2 = UPDATED_LINE_RE.sub("updated: " + U.iso(ms), fm, count=1)
        new = "---\n" + fm2 + "\n---\n" + (rest.rstrip("\n") + "\n\n" if rest.strip() else "\n") + body.rstrip("\n") + "\n"
        tmp = path.with_name("." + path.name + ".tmp")
        try:
            tmp.write_bytes(new.encode("utf-8"))
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
        _save(vault, fid, uidx, notes_lab)
    return {"uid": uid, "path": hit["path"], "bytes": size}


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


def _uid_notes(vault: Path) -> dict:
    """uid 檔名的筆記 ⇒ {uid: 相對路徑};跳過 .ez2 / .obsidian 等。同 uid 兩份(verify 會另外報)取先看到的。"""
    out = {}
    for dp, dn, fn in os.walk(vault):
        dn[:] = sorted(x for x in dn if x not in U.SKIP_DIRS and not x.startswith("."))
        for f in sorted(fn):
            if f.endswith(".md") and U.UID_RE.match(f[:-3]):
                out.setdefault(f[:-3], str(Path(dp, f).relative_to(vault)))
    return out


def _fm_title(kv: dict) -> str:
    return (kv.get("title") or "").strip().strip("\"'")


def _sync_scan(vault: Path) -> dict:
    """0.1.18:索引 / 標題 / 別名跟筆記本身對不上的地方。筆記(frontmatter 與實際位置)為準。"""
    _fid, uidx, notes_lab = _load(vault)
    by_uid = {i.get("uid"): i for i in uidx}
    out = {"md_not_indexed": [], "md_index_stale": [], "md_title_out_of_sync": [], "md_alias_missing_title": []}
    for uid, rel in _uid_notes(vault).items():
        fm, _body = U.split_frontmatter((vault / rel).read_text(encoding="utf-8", errors="replace"))
        kv = U.fm_lines_to_map(fm) if fm is not None else {}
        title = _fm_title(kv)
        parent = Path(rel).parent.name
        here = parent if U.FOLDER_ID_RE.match(parent) else ""
        it, lab = by_uid.get(uid), notes_lab.get(uid) or {}
        if it is None:
            out["md_not_indexed"].append({"uid": uid, "path": rel, "title": title})
        else:
            fid_fm = (kv.get("folder_id") or "").strip().strip("\"'")
            if it.get("path") != rel or (lab and lab.get("path") not in (None, rel)) or (fid_fm and here and fid_fm != here):
                out["md_index_stale"].append({"uid": uid, "path": rel, "old_path": it.get("path"),
                                              "folder_id": here, "old_folder_id": fid_fm})
        if title and lab.get("title") and lab.get("title") != title:
            out["md_title_out_of_sync"].append({"uid": uid, "path": rel, "title": title, "label": lab["title"]})
        if title and fm is not None and title not in U.fm_list(fm, "aliases"):
            out["md_alias_missing_title"].append({"uid": uid, "path": rel, "title": title})
    return out


def _set_key_line(fm: str, key: str, line: str, after: str = "title") -> str:
    """frontmatter 的 key 換成一行 line(原本是單行或區塊寫法都換掉);沒有這個 key ⇒ 插在 after 那行後面(沒有就最前面)。"""
    lines = fm.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"^([A-Za-z0-9_.\-]+):(.*)$", ln)
        if m and m.group(1) == key:
            j = i + 1
            if U._is_block_start(m.group(2).strip(), lines, i):
                while j < len(lines) and (U.BLOCK_ITEM_RE.match(lines[j]) or not lines[j].strip()
                                          or lines[j].lstrip().startswith("#")):
                    j += 1
            return "\n".join(lines[:i] + [line] + lines[j:])
    at = next((i + 1 for i, ln in enumerate(lines) if re.match(r"^%s:" % re.escape(after), ln)), 0)
    return "\n".join(lines[:at] + [line] + lines[at:])


def _rewrite_fm(vault: Path, rel: str, edit) -> bool:
    """只改 frontmatter(edit(fm) ⇒ 新 fm),正文一個位元組不動;沒有 frontmatter ⇒ 不動、回 False。"""
    p = vault / rel
    text = p.read_text(encoding="utf-8")
    m = U.FM_RE.match(text)
    if not m:
        return False
    new_fm = edit(m.group(1))
    if new_fm == m.group(1):
        return False
    p.write_text(text[:m.start(1)] + new_fm + text[m.end(1):], encoding="utf-8")
    return True


def _problems(vault: Path) -> dict:
    res = U.verify(vault, cards_path=str(vault / ".ez2" / "no-cards-check.json"))
    keys = ("dirs_not_folder_id", "md_not_uid_name", "md_no_uid", "md_uid_mismatch", "uid_duplicates", "index_paths_missing",
            "route_paths_missing")
    out = {k: res.get(k) or [] for k in keys}
    out["md_updated_local_time"] = _updated_scan(vault)[0]
    out.update(_sync_scan(vault))
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
    out = _doctor(vault, False, None, now_ms)
    if not fix or out["ok"]:
        return out
    _backup_ok(Path(vault).resolve(), backup_dir)          # 備份位置不合 ⇒ 先錯,鎖檔都不建(庫裡一個位元組都不動)
    with _locked(Path(vault).resolve()):                  # 拿到鎖之後重掃一次再修
        return _doctor(vault, True, backup_dir, now_ms)


def _backup_ok(vault: Path, backup_dir: Path | None) -> Path:
    if backup_dir is None:
        raise VaultError(U.T("err_backup_required"))
    backup_dir = backup_dir.resolve()
    if backup_dir == vault or vault in backup_dir.parents:
        raise VaultError(U.T("err_backup_inside"))
    return backup_dir


def _doctor(vault: Path, fix: bool = False, backup_dir: Path | None = None, now_ms: int | None = None) -> dict:
    vault = Path(vault).resolve()                 # 捷徑解開再比(/var → /private/var),「備份放庫裡」才擋得住
    found = _problems(vault)
    out = {"vault": str(vault), "problems": found, "fixed": [], "left": [], "backup": None,
           "ok": not any(found.values()),
           "notices": {"md_updated_ambiguous": _updated_scan(vault)[1], "timezone": time.strftime("%z")}}
    if not fix or out["ok"]:
        return out
    backup_dir = _backup_ok(vault, backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    tgz = backup_dir / ("doctor-%s.tgz" % time.strftime("%Y%m%d-%H%M%S"))
    touched = sorted(set(found["dirs_not_folder_id"]) | set(found["md_not_uid_name"]) | set(found["md_no_uid"])
                     | {e["path"] for e in found["md_updated_local_time"]}
                     | {e["path"] for e in found["md_index_stale"] if e["old_folder_id"]}
                     | {e["path"] for e in found["md_alias_missing_title"]})
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
    carriers = _uid_carriers(vault)
    for rel in dict.fromkeys(cur["md_not_uid_name"] + cur["md_no_uid"]):   # 兩份清單都有的(檔名與 uid 都缺)只處理一次
        p = vault / rel
        text = p.read_text(encoding="utf-8", errors="replace")
        old_fm, body = U.split_frontmatter(text)
        kv = U.fm_lines_to_map(old_fm) if old_fm is not None else {}
        named_uid = U.UID_RE.match(p.stem) is not None
        have = (kv.get("uid") or "").strip().strip("\"'")
        if named_uid:
            uid = p.stem
        elif U.UID_RE.match(have) and (have not in used or _moved_alone(vault, have, uidx, carriers)):
            uid = have                              # 沒人用過,或只是改名 / 搬家(舊路徑已不在、全庫只有這篇帶它)⇒ 沿用
        else:
            uid = U.mint_uid(int(p.stat().st_mtime * 1000), used)
        used.add(uid)
        if old_fm is not None and uid != have:      # 換了新 uid(例 Obsidian 複製出來的那篇)⇒ 不是那筆紀錄、也不是 newpost 建的那篇
            old_fm = "\n".join(ln for ln in old_fm.splitlines()
                                if not ln.startswith((APPEND_KEY + ":", "record_id:")))
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
    # 6) 0.1.18 索引 / 標題 / 別名跟筆記同步(筆記為準;前面幾步改過名的用改名後的路徑重掃)
    out["fixed"] += _fix_sync(vault)
    after = _problems(vault)
    for k in ("uid_duplicates", "md_uid_mismatch"):
        for item in after[k]:
            out["left"].append({"kind": k, "item": item})
    out["problems_after"] = after
    out["ok"] = not any(v for k, v in after.items())
    return out


def _uid_carriers(vault: Path) -> dict:
    """全庫每個 uid 有幾篇帶著(檔名或 frontmatter uid,同一篇只算一次)。"""
    n: dict = {}
    for dp, dn, fn in os.walk(vault):
        dn[:] = [x for x in dn if x not in U.SKIP_DIRS and not x.startswith(".")]
        for f in fn:
            if not f.endswith(".md"):
                continue
            p = Path(dp, f)
            fm, _b = U.split_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
            have = (U.fm_lines_to_map(fm).get("uid", "") if fm is not None else "").strip().strip("\"'")
            for u in {x for x in (p.stem, have) if U.UID_RE.match(x)}:
                n[u] = n.get(u, 0) + 1
    return n


def _moved_alone(vault: Path, uid: str, uidx: list, carriers: dict) -> bool:
    """改名 / 搬家:索引記的舊路徑已不存在,而且全庫只有一篇帶這個 uid ⇒ True。兩篇都帶(真的複製)⇒ False。"""
    olds = [i.get("path") for i in uidx if i.get("uid") == uid]
    return carriers.get(uid, 0) == 1 and all(not o or not (vault / o).exists() for o in olds)


def _fix_sync(vault: Path) -> list:
    sync = _sync_scan(vault)
    fixed = []
    for e in sync["md_alias_missing_title"]:
        def add_alias(fm, e=e):
            items = U.fm_list(fm, "aliases")
            return _set_key_line(fm, "aliases", "aliases: " + U.yaml_list(items + [e["title"]]))
        if _rewrite_fm(vault, e["path"], add_alias):
            fixed.append({"kind": "alias", "path": e["path"], "title": e["title"]})
    for e in sync["md_index_stale"]:
        if e["folder_id"] and e["old_folder_id"] and e["old_folder_id"] != e["folder_id"]:
            if _rewrite_fm(vault, e["path"], lambda fm, e=e: _set_key_line(fm, "folder_id", "folder_id: " + e["folder_id"])):
                fixed.append({"kind": "folder_id", "path": e["path"], "old": e["old_folder_id"], "new": e["folder_id"]})
    fid, uidx, notes_lab = _load(vault)
    by_uid = {i.get("uid"): i for i in uidx}
    todo = {}                                                     # 同一篇只處理一次(先登記 > 路徑 > 標題)
    for kind, key in (("index_add", "md_not_indexed"), ("index_path", "md_index_stale"), ("label_title", "md_title_out_of_sync")):
        for e in sync[key]:
            todo.setdefault(e["uid"], (kind, e))
    for kind, e in todo.values():
        fm, _b = U.split_frontmatter((vault / e["path"]).read_text(encoding="utf-8", errors="replace"))
        kv = U.fm_lines_to_map(fm) if fm is not None else {}
        title = _fm_title(kv) or e["uid"]
        parent = Path(e["path"]).parent.name
        rec = by_uid.get(e["uid"])
        if rec is None:
            rec = {"uid": e["uid"]}
            uidx.append(rec)
            by_uid[e["uid"]] = rec
        rec.update({"path": e["path"], "title": title,
                    "routeKey": (kv.get("routeKey") or "").strip().strip("\"'") or rec.get("routeKey") or "",
                    "folderId": parent if U.FOLDER_ID_RE.match(parent) else rec.get("folderId", "")})
        lab = dict(notes_lab.get(e["uid"]) or {})
        old_title = lab.get("title")
        i18n = dict(lab.get("i18n") or {})
        if not i18n.get("zh-TW") or i18n.get("zh-TW") == old_title:
            i18n["zh-TW"] = title
        lab.update({"title": title, "path": e["path"], "i18n": i18n})
        notes_lab[e["uid"]] = lab
        fixed.append({"kind": kind, "path": e["path"], "uid": e["uid"]})
    if todo:
        _save(vault, fid, uidx, notes_lab)
    return fixed


def mkdir(vault: Path, title: str, parent: str = "") -> dict:
    """新增 uid 資料夾(包 uid_layout_convert.folder_add;同一個父夾已有同名 ⇒ 回原本那個)。"""
    try:
        with _locked(Path(vault).resolve()):
            return U.folder_add(Path(vault).resolve(), parent or "", title)
    except SystemExit as exc:
        raise VaultError(str(exc.code))


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


# ---------------------------------------------------------------- 手機授權
MOBILE_LICENSE = ".ez2/license.json"


def mobile_license(vault: Path, remove: bool = False, env=None) -> dict:
    """把這台的授權複製一份到 <vault>/.ez2/license.json(10-20 維護者選方案 A):手機上的 Obsidian 標題外掛
    沒有 aib,隨 Vault 同步拿到這份、自己驗簽章。只收受信鑰匙簽的、沒綁機器的授權(手機驗不了機器碼);
    內容原樣照抄(簽章蓋的是內容,不是排版)。remove ⇒ 從 Vault 拿掉(手機下次檢查就關)。
    授權檔跟著 Vault 走:Vault 給誰、授權就給誰,這跟把授權檔交給對方一樣。"""
    dest = Path(vault) / MOBILE_LICENSE
    with _locked(vault):
        if remove:
            if dest.is_symlink() or dest.exists():
                dest.unlink()
                return {"path": MOBILE_LICENSE, "removed": True}
            return {"path": MOBILE_LICENSE, "removed": False}
        sys.path.insert(0, str(ROOT / "src"))
        from entitlement.signing import PREFIX, verify_object, OK
        from entitlement.store import read_license_obj
        obj = read_license_obj(env=env)
        if not isinstance(obj, dict):
            raise VaultError(T("err_mobile_no_license"))
        if not str(obj.get("signature") or "").startswith(PREFIX) or verify_object(obj) != OK:
            raise VaultError(T("err_mobile_unsigned"))
        if obj.get("devices") is not None or obj.get("activation_url"):
            raise VaultError(T("err_mobile_bound"))
        text = json.dumps(obj, ensure_ascii=False, indent=1) + "\n"
        if dest.is_symlink():
            raise VaultError(T("err_mobile_symlink", {"path": MOBILE_LICENSE}))
        same = dest.is_file() and dest.read_text(encoding="utf-8") == text
        if not same:
            tmp = dest.with_name(".license.json.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, dest)
        return {"path": MOBILE_LICENSE, "changed": not same, "expires_at": obj.get("expires_at")}
# ---------------------------------------------------------------- title(只讀)
def _label(entry, fallback: str) -> str:
    """labels 一筆 ⇒ 目前語系的名字(沒翻 ⇒ title);拿掉方向字元 / 零寬字(面板照字顯示,不讓標題偽裝成別的路徑)。"""
    if not isinstance(entry, dict):
        return fallback
    i18n = entry.get("i18n") if isinstance(entry.get("i18n"), dict) else {}
    name = INVISIBLE_RE.sub("", str(i18n.get(U.LOC) or entry.get("title") or "")).strip()
    return name or fallback


def titles(vault: Path, paths: list) -> list:
    """路徑 ⇒ 標題路徑。只讀 labels.json;Vault 外 / 往上跳出 Vault 的一律 in_vault false、不給標題。"""
    if len(paths) > TITLE_MAX:
        raise VaultError(T("err_vtitle_too_many", {"count": len(paths), "max": TITLE_MAX}))
    lab = U.load_json(vault / ".ez2/display/labels.json", {})
    folders = lab.get("folders") if isinstance(lab.get("folders"), dict) else {}
    notes = lab.get("notes") if isinstance(lab.get("notes"), dict) else {}
    out = []
    for raw in paths:
        raw = str(raw)
        s = os.path.expanduser(raw.strip())
        if os.path.isabs(s):
            try:                                          # realpath:Vault 裡的符號連結指到外面 ⇒ 算外面
                rel = Path(os.path.realpath(s)).relative_to(vault).as_posix()
            except ValueError:
                out.append({"path": raw, "in_vault": False})
                continue
        else:
            rel = os.path.normpath(s).replace(os.sep, "/") if s else "."
            if rel == ".." or rel.startswith("../"):
                out.append({"path": raw, "in_vault": False})
                continue
        rel = "" if rel == "." else rel
        parts = rel.split("/") if rel else []
        names, kind, ident = [], "other", ""
        for n, seg in enumerate(parts):
            last = n == len(parts) - 1
            if last and seg.endswith(".md") and seg[:-3] in notes:
                names.append(_label(notes[seg[:-3]], seg))
                kind, ident = "note", seg[:-3]
            elif seg in folders:
                names.append(_label(folders[seg], seg))
                if last:
                    kind, ident = "folder", seg
            else:
                names.append(seg)
        item = {"path": raw, "in_vault": True, "rel": rel, "title_path": "/".join(names),
                "title": names[-1] if names else vault.name, "kind": kind if parts else "vault", "id": ident}
        if not (parts and parts[0].startswith(".")):     # .ez2 / .obsidian 在 Obsidian 裡打不開,不給連結
            item["obsidian"] = "obsidian://open?vault=%s&file=%s" % (urllib.parse.quote(vault.name, safe=""),
                                                                    urllib.parse.quote(rel, safe=""))
        out.append(item)
    return out


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="aib vault", description=T("help_desc"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("newpost", help=T("help_newpost"))
    n.add_argument("--title", default="", help=T("help_title"))
    n.add_argument("--folder", default="", help=T("help_folder"))
    n.add_argument("--body", default="", help=T("help_body"))
    n.add_argument("--meta-json", default=None, help=T("help_meta_json"))
    n.add_argument("--appendable", action="store_true", help=T("help_appendable"))
    a = sub.add_parser("append", help=T("help_append"))
    a.add_argument("--uid", required=True, help=T("help_append_uid"))
    ab = a.add_mutually_exclusive_group()
    ab.add_argument("--body", default="", help=T("help_body"))
    ab.add_argument("--body-file", default="", help=T("help_body_file"))
    d = sub.add_parser("doctor", help=T("help_doctor"))
    d.add_argument("--fix", action="store_true", help=T("help_fix"))
    d.add_argument("--backup-dir", help=U.T("help_folder_backup"))
    k = sub.add_parser("mkdir", help=T("help_mkdir"))
    k.add_argument("--title", required=True, help=T("help_mkdir_title"))
    k.add_argument("--parent", default="", help=U.T("help_folder_parent"))
    m = sub.add_parser("mobile-license", help=T("help_mobile_license"))
    m.add_argument("--remove", action="store_true", help=T("help_mobile_remove"))
    t = sub.add_parser("title", help=T("help_vtitle"))
    t.add_argument("paths", nargs="*", help=T("help_vtitle_paths"))
    t.add_argument("--stdin", action="store_true", help=T("help_vtitle_stdin"))
    for p in (n, d, k, a, m, t):
        p.add_argument("--vault", default="", help=T("help_vault"))
        p.add_argument("--json", action="store_true", help=T("help_json"))
    args = ap.parse_args(argv)
    # title 只讀 labels.json、給免費的對話面板用 ⇒ 不驗 obsidian-labels(10-20 @abb89 裁);其餘子指令照舊要授權
    ok, why = (True, "free") if args.cmd == "title" else licensed()
    if not ok:
        msg = {"licensed": False, "addon": ADDON, "why": why}
        print(json.dumps(msg) if args.json else T("err_not_licensed", {"addon": ADDON, "why": why}),
              file=sys.stdout if args.json else sys.stderr)
        return 3
    try:
        vault = resolve_vault(args.vault)
        if args.cmd == "title":
            paths = list(args.paths)
            if args.stdin:
                paths += [ln for ln in sys.stdin.read().splitlines() if ln.strip()]
            if not paths:
                raise VaultError(T("err_vtitle_none"))
            res = titles(vault, paths)
            if args.json:
                print(json.dumps({"vault": vault.name, "items": res}, ensure_ascii=False))
            else:
                for it in res:
                    print("%s\t%s" % (it["path"], it["title_path"] if it["in_vault"] else T("vtitle_outside")))
            return 0
        if args.cmd == "mkdir":
            res = mkdir(vault, args.title, args.parent)
            print(json.dumps(res, ensure_ascii=False) if args.json else T("mkdir_done", {"title": res["title"], "path": res["folderPath"]}))
            return 0
        if args.cmd == "mobile-license":
            res = mobile_license(vault, args.remove)
            if args.json:
                print(json.dumps(res, ensure_ascii=False))
            else:
                print(T("mobile_removed" if res.get("removed") else ("mobile_absent" if args.remove else "mobile_done"),
                        {"path": res["path"], "expires": res.get("expires_at") or "-"}))
            return 0
        if args.cmd == "append":
            body = args.body
            if args.body_file:                            # 最多讀上限 + 1 位元組,超過的交給 append 拒(不把 /dev/zero 讀到底)
                if args.body_file == "-":
                    raw = sys.stdin.buffer.read(APPEND_MAX + 1) if hasattr(sys.stdin, "buffer") else sys.stdin.read(APPEND_MAX + 1).encode("utf-8")
                else:
                    with open(args.body_file, "rb") as fh:
                        raw = fh.read(APPEND_MAX + 1)
                if len(raw) > APPEND_MAX:
                    raise VaultError(T("err_append_too_big", {"size": "> %d" % APPEND_MAX, "max": APPEND_MAX}))
                try:
                    body = raw.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    raise VaultError(T("err_text_not_utf8", {"key": "--body-file"}))
            res = append(vault, args.uid, body)
            print(json.dumps(res, ensure_ascii=False) if args.json else T("append_done", {"path": res["path"], "bytes": res["bytes"]}))
            return 0
        if args.cmd == "newpost":
            meta = None
            if args.meta_json is not None:
                try:
                    meta = json.loads(args.meta_json)
                except ValueError:
                    raise VaultError(T("err_meta_bad_json"))
            res = newpost(vault, args.title, args.folder, args.body, meta=meta, appendable=args.appendable)
            print(json.dumps(res, ensure_ascii=False) if args.json else T("newpost_done", {"title": res["title"], "path": res["path"]}))
            return 0
        res = doctor(vault, args.fix, Path(args.backup_dir) if args.backup_dir else None)
    except (VaultError, SystemExit, OSError, UnicodeError) as exc:
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
