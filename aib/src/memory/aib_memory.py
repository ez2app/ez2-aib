#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib memory —— AI 席把工作記憶寫成第二大腦(Obsidian)裡的一則筆記(the maintainer 09-30)。

the maintainer 09-30:「應該要用 uuid 寫到 aib 對應的資料夾裡,然後用 obs 的多種屬性/分類/標簽多維度的寫,標題才是用來顯示的」;
「主要邏輯只能是全部 uid 類似的、有關顯示或字數的全用 title、標簽、分類英文、但要有 i18n 顯示對應內容」。

所以:
  * **認東西一律用 uid**:這則筆記的 uid(ez2-ai note-format 正本產的 6 碼)、席位 uid(session_uuid)、
    專案 uid(proj:…)、人格卡 uid(6 碼)。程式找記憶、串記憶只看 uid;標題只拿來顯示。
  * **屬性多維度**:routeKey / category_uid / privacy_level / source_kind / review_status / tags(英文)+
    custom.aib.*(kind、seat_uid、project_uid、persona_uid、related_uids)。
  * **英文標籤與分類,顯示走 i18n**:aib.memory.kind.<kind>、aib.memory.tag.<tag 的最後一段>、aib.category.<category_uid>。
  * **不重寫規則**:筆記長相與驗證全交給 ez2-ai 的 note-format 正本(src/memory/note_format_runner.js)。
  * **放哪裡**:路由代號決定資料夾(契約現查、必須 aiWritable、分類必須已登記);預設 core.handoff
    (C80-系統記憶交接)。資料夾只是存放處,歸屬靠 custom.aib.* 裡的 uid。
  * **AI 自己的工作記憶 ≠ 審核過的知識**:一律 review_status=pending、created_by_ai=true,不冒充核准;
    知識要進正式的第二大腦仍走 brain_write_plan(要人的核准收據)。
  * 目標已存在一律拒(不覆寫;更新 = 新寫一則,related_uids 指回舊的)。

  aib memory write --kind progress|decision|next|handoff|note --title T (--body B | --body-file F)
                   [--tags a,b/c] [--about uid,uid] [--seat <艙位碼>/<席碼>] [--project proj:…]
                   [--route core.handoff] [--dry-run] [--json]
  aib memory list  --about <uid> [--kind K] [--limit N] [--json]        找跟這個 uid 有關的記憶(新→舊)
  aib memory show  --uid <筆記 uid>                                     印全文
  aib memory brief --about <uid> [--limit N]                            給換席用的一段文字(最新幾則)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
for p in (HERE, SRC / "identity", SRC / "cockpit", SRC / "persona_route"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

KINDS = ("progress", "decision", "next", "handoff", "note")
DEFAULT_ROUTE = "core.handoff"
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9/_-]*$")
PROJECT_RE = re.compile(r"^proj:[0-9a-f]{16}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
NOTE_UID_RE = re.compile(r"^[a-z0-9]{6}$")


class MemoryError_(Exception):
    pass


def aib_home() -> Path:
    """狀態根,走單源 seats_dir_lib.state_root(EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib)。
    本檔會跨艙找席位檔(instances/*/seats),與 seat_who 同一條:跨艙掃描仍共用單源的狀態根。"""
    import seats_dir_lib
    return Path(seats_dir_lib.state_root()[0])


def index_path() -> Path:
    return aib_home() / "memory-index" / "notes.jsonl"


def translator():
    try:
        from i18n_lib import Translator
        return Translator()
    except Exception:  # noqa: BLE001
        return None


def display(key: str, fallback: str) -> str:
    """英文鍵 ⇒ 使用者語系的顯示字;字典沒有就顯示英文原字(不猜翻譯)。"""
    tr = translator()
    if tr is None:
        return fallback
    try:
        out = tr.t(key)
    except Exception:  # noqa: BLE001
        return fallback
    return fallback if (not out or out.startswith("!!") or out == key) else out


# ---------------------------------------------------------------- 這一席是誰(全部 uid)
def seat_context(seat: str = "") -> dict:
    """{"seat_uid": session_uuid, "code"(席碼), "cabin", "persona_uid", "project_uid", "route_key"}。量不到的欄位留空。"""
    rec, cabin = {}, ""
    if seat:
        m = re.fullmatch(r"([a-z0-9]{1,4})/@?([0-9a-z]{5})", seat)
        if not m:
            raise MemoryError_("--seat 要寫 <艙位碼>/<席碼>")
        cabin, code = m.groups()
        for f in sorted((aib_home() / "instances").glob("*-%s/seats/w*.json" % cabin)):
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if str(r.get("session_uuid") or "").endswith(code):
                rec = r
                break
    else:
        import persona_reinject_hook as H
        home = H._instance_home()
        wid = H._window_id()
        if home and wid:
            cabin = home.name.rsplit("-", 1)[-1][-4:]
            try:
                rec = json.loads((home / "seats" / ("w%s.json" % wid.lstrip("@"))).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                rec = {}
    uuid = str(rec.get("session_uuid") or "")
    out = {"seat_uid": uuid if UUID_RE.fullmatch(uuid) else "", "code": uuid[-5:] if uuid else "",
           "cabin": cabin, "persona_uid": "", "project_uid": str(rec.get("project_uid") or ""), "route_key": "",
           # 10-13 記憶整理第 3 包:每筆更新記「哪個模型、哪一席」;讀不到留空,由呼叫端寫「讀不到」
           "seat_id": str(rec.get("seat_id") or ""), "model": str(rec.get("model_requested") or rec.get("model") or "")}
    if uuid:
        code = uuid[-5:]
        for name, key in (("bindings.json", "bindings"), ("seat-display.json", "display")):
            try:
                doc = json.loads((aib_home() / "identity" / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            b = (doc.get(key) or {}).get(code)
            if isinstance(b, dict):
                out["persona_uid"] = out["persona_uid"] or str(b.get("persona_uid") or "")
                out["route_key"] = out["route_key"] or str(b.get("route_key") or "")
                out["project_uid"] = out["project_uid"] or str(b.get("project_uid") or "")
    if out["project_uid"] and not PROJECT_RE.fullmatch(out["project_uid"]):
        out["project_uid"] = ""
    return out


# ---------------------------------------------------------------- 寫
def _contract_route(route_key: str) -> tuple[dict, str]:
    """(契約裡那一條, 分類 uid)。不能寫 ⇒ raise,帶原因碼。"""
    import brain_write_plan as B
    probe = B.probe_route_writability(route_key)
    if not probe.get("ok"):
        raise MemoryError_("路由 %s 不能寫:%s" % (route_key, ",".join(probe.get("codes") or ["unknown"])))
    from route_register import load_contract
    route = B.route_of(load_contract(None), route_key)      # 與探針讀同一份契約(同一個預設來源)
    if not route or not route.get("aiWritable"):
        raise MemoryError_("路由 %s 在契約裡不是 AI 可寫" % route_key)
    return route, str((probe.get("checked") or {}).get("category_uid") or "")


def _used_uids() -> list[str]:
    try:
        return [json.loads(ln).get("uid") for ln in index_path().read_text(encoding="utf-8").splitlines() if ln.strip()]
    except (OSError, ValueError):
        return []


def build_note(kind, title, body, tags, about, ctx, route_key, supersedes="", model=""):
    import brain_write_plan as B
    if kind not in KINDS:
        raise MemoryError_("--kind 只能是 %s" % "|".join(KINDS))
    title = " ".join(str(title or "").split())
    if not title:
        raise MemoryError_("--title 必填(只拿來顯示)")
    if not str(body or "").strip():
        raise MemoryError_("內容是空的")
    clean_tags = ["ez2/aib/memory", "ez2/aib/memory/%s" % kind]
    for t in tags:
        t = t.strip().lstrip("#").lower()
        if not t:
            continue
        if not TAG_RE.fullmatch(t):
            raise MemoryError_("標籤要英文小寫(a-z 0-9 / _ -):%r" % t)
        clean_tags.append(t)
    related = []
    for u in [ctx.get("project_uid"), ctx.get("persona_uid"), ctx.get("seat_uid")] + list(about):
        u = str(u or "").strip()
        if u and u not in related:
            related.append(u)
    route, category_uid = _contract_route(route_key)
    extra = {
        "custom.aib.memory_kind": kind,
        "custom.aib.seat_uid": ctx.get("seat_uid") or "",
        "custom.aib.project_uid": ctx.get("project_uid") or "",
        "custom.aib.persona_uid": ctx.get("persona_uid") or "",
        "custom.aib.related_uids": related,
        "custom.aib.supersedes": supersedes or "",
        "custom.aib.model": model or "",
        "custom.aib.seat": ctx.get("code") or "",
    }
    extra = {k: v for k, v in extra.items() if v not in ("", [])}
    B._assert_custom_prefixed(extra)
    links = "\n".join("- [[%s]]" % u for u in related if NOTE_UID_RE.fullmatch(u))
    full_body = body.rstrip() + ("\n\n## related\n" + links if links else "")
    modules, _shas, _rev = B._resolve_modules_dir(None, None, B.DEFAULT_REV, B.PINS_DEFAULT)
    out = B.call_runner(modules, {
        "route": route,
        "note": {"created_at_ms": int(time.time() * 1000), "title": title, "privacy_level": "internal",
                 "source_kind": "ai_chat", "review_status": "pending", "created_by_ai": True,
                 "tags": clean_tags, "extra_frontmatter": extra, "body": full_body},
        "used_uids": _used_uids(),
        "want_content": True,
    })
    ok, code = B.gate_note_format(out, route_key)
    if not ok:
        raise MemoryError_("筆記格式檢查沒過:%s" % code)
    return out, route, category_uid, clean_tags, related


def write(kind, title, body, tags=(), about=(), seat="", project="", route_key="", dry_run=False,
          supersedes="", model="") -> dict:
    import brain_write_plan as B
    ctx = seat_context(seat)
    if project:
        if not PROJECT_RE.fullmatch(project):
            raise MemoryError_("--project 要寫 proj:<16 碼>")
        ctx["project_uid"] = project
    route_key = route_key or DEFAULT_ROUTE
    out, route, category_uid, tags_out, related = build_note(kind, title, body, list(tags), list(about), ctx, route_key,
                                                             supersedes, model)
    note = out["note"]
    vault = B.vault_root()
    if vault is None:
        raise MemoryError_("這台量不到第二大腦(EZ2_VAULT_DIR)")
    import route_register as RR
    uid_layout = RR.vault_is_uid_layout(str(vault))
    rel = RR.resolve_route_folder(route_key, contract={"routes": [route]}, root=str(vault))
    if rel is None and uid_layout:
        # uid 版面查不到索引 ⇒ 停;退回契約字面(中文舊名)再 mkdir 會把轉換前的資料夾建回來
        raise MemoryError_("第二大腦是 uid 版面,但索引裡找不到 %s 的資料夾;先跑轉換工具 reindex,不寫" % route_key)
    folder = Path(vault) / (rel or route["folder"])
    file_name = (note["uid"] + ".md") if uid_layout else note["file_name"]
    target = folder / file_name
    # 10-16:索引不存絕對路徑(庫一搬家就失效、讀的人還會靜默拿到空);存 uid + 相對 Vault 的路徑當提示,讀時現查
    # 10-17:標題記正本清洗後、檔內那一份(cleanTitle 會拿掉 : / # 等檔名不安全字);舊 runner 沒回 ⇒ 退回原字
    rec = {"uid": note["uid"], "title": note.get("title") or " ".join(title.split()), "kind": kind, "tags": tags_out,
           "category_uid": category_uid, "route_key": route_key, "related_uids": related,
           "seat_uid": ctx.get("seat_uid"), "project_uid": ctx.get("project_uid"),
           "persona_uid": ctx.get("persona_uid"), "rel": os.path.relpath(str(target), str(vault)),
           "created_ms": int(time.time() * 1000),
           # 10-13 第 3 包:索引也記取代關係(seat_brief 讀索引不讀 frontmatter)
           "supersedes": supersedes or "", "model": model or "", "seat": ctx.get("code") or ""}
    if dry_run:
        return dict(rec, path=str(target), dry_run=True)
    if target.exists():
        raise MemoryError_("目標已存在,不覆寫:%s" % target)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp-%d" % os.getpid())
    tmp.write_text(out["markdown"], encoding="utf-8")
    os.replace(tmp, target)
    index_path().parent.mkdir(parents=True, exist_ok=True)
    with open(index_path(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return dict(rec, path=str(target))          # 給人看的完整路徑只在回傳裡,不進索引


# ---------------------------------------------------------------- 讀(全部靠 uid)
def entries(index: Path | None = None) -> list[dict]:
    try:
        lines = Path(index or index_path()).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("uid"):
            out.append(r)
    return out


def related_to(uid: str, kind: str = "", limit: int = 20, skip_superseded: bool = False,
               index: Path | None = None) -> list[dict]:
    rows = entries(index)
    gone = {r.get("supersedes") for r in rows if r.get("supersedes")} if skip_superseded else set()
    hits = [r for r in rows if r.get("uid") not in gone]   # 只有開席帶回(brief)跳過被取代的;手動 list 照列
    hits = [r for r in hits
            if uid in (r.get("related_uids") or []) or uid in (r.get("seat_uid"), r.get("project_uid"),
                                                              r.get("persona_uid"), r.get("uid"))]
    if kind:
        hits = [r for r in hits if r.get("kind") == kind]
    hits.sort(key=lambda r: r.get("created_ms") or 0, reverse=True)
    return hits[:max(1, limit)]


_VAULT_FILES: dict = {}
_REWALKED: set = set()


def _inside(root: Path, rel) -> "Path | None":
    """root/rel 解開後還在 root 裡才回;rel 不是字串、絕對路徑、../ 跑出去 ⇒ None。"""
    if not isinstance(rel, str) or not rel:
        return None
    try:
        p = (root / rel).resolve()
        return p if root.resolve() in p.parents else None
    except (OSError, ValueError):
        return None


def _vault_files(root: Path) -> dict:
    """Vault 裡每個 .md 的檔名 → 路徑(跳過 . 開頭的資料夾);同一次執行只走一遍。"""
    key = str(root)
    if key not in _VAULT_FILES:
        names: dict = {}
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if not d.startswith(".")]
            for f in fn:
                if f.endswith(".md"):
                    names.setdefault(f, Path(dp) / f)
        _VAULT_FILES[key] = names
    return _VAULT_FILES[key]


def _uid_index_path(root: Path, uid: str):
    try:
        items = json.loads((root / ".ez2" / "index" / "uid-index.json").read_text(encoding="utf-8")).get("items") or []
    except (OSError, ValueError, AttributeError):
        return None
    hit = next((it.get("path") for it in items if isinstance(it, dict) and it.get("uid") == uid), None)
    return _inside(root, hit)


def resolve_note_path(rec: dict, vault=None) -> Path:
    """索引一筆 → 這則筆記現在在哪。認 uid,不認寫入當時的絕對路徑(10-16:庫搬家後舊路徑會靜默讀成空)。
    順序:相對提示 rel → Vault 的 uid-index → 舊格式的絕對 path → 在 Vault 裡找 <uid>.md / 舊檔名。
    都找不到 ⇒ MemoryError_,不給空字串。"""
    import brain_write_plan as B
    uid = str(rec.get("uid") or "")
    root = Path(vault) if vault else B.vault_root()
    legacy = Path(rec["path"]) if rec.get("path") else None
    cands = []
    if root is not None:
        hint = _inside(root, rec.get("rel"))
        if hint is not None:
            cands.append(hint)
        hit = _uid_index_path(root, uid)
        if hit is not None:
            cands.append(hit)
    if legacy is not None:
        cands.append(legacy)
    for c in cands:
        if c.is_file():
            return c
    if root is not None and root.is_dir():
        # 舊版面的檔名是 <uid>-<標題>.md ⇒ 也找相對提示與舊路徑的檔名(筆記在 Obsidian 裡被拖到別夾時用得上)
        wanted = ([uid + ".md"] if NOTE_UID_RE.fullmatch(uid) else []) + \
            ([Path(rec["rel"]).name] if isinstance(rec.get("rel"), str) and rec.get("rel") else []) + \
            ([legacy.name] if legacy else [])
        for fresh in (False, True):            # 長駐行程的快取可能沒有後寫的筆記 ⇒ 沒中就重走一遍,
            if fresh:                          #   但每個庫每個行程最多重走一次(開席 hook 有 10 秒上限)
                if str(root) in _REWALKED:
                    break
                _REWALKED.add(str(root))
                _VAULT_FILES.pop(str(root), None)
            names = _vault_files(root)
            for name in wanted:
                if name in names and names[name].is_file():
                    return names[name]
    raise MemoryError_(display("aib.memory.cli.file_missing",
                               "note {uid} is in the memory index but its file is not in the vault ({vault})")
                       .replace("{uid}", uid).replace("{vault}", str(root or "-")))


def note_text(rec: dict) -> str:
    """這則的內文(frontmatter 之後)。檔案找不到 ⇒ MemoryError_(不靜默給空)。"""
    path = resolve_note_path(rec)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MemoryError_("%s: %s" % (path, exc))
    parts = text.split("---", 2)
    return parts[2].strip() if len(parts) == 3 else text


def brief(uid: str, limit: int = 5) -> str:
    """換席用:跟這個 uid 有關的最新幾則,每則標題(顯示用)+種類(i18n)+uid+內文。"""
    rows = related_to(uid, limit=limit, skip_superseded=True)   # 10-13 第 3 包:開席帶回不帶已被取代的進度
    if not rows:
        return ""
    out = ["[AIB 記憶] 跟 %s 有關的最新 %d 則(新→舊;標題只供顯示,找東西用 uid)" % (uid, len(rows))]
    for r in rows:
        k = display("aib.memory.kind.%s" % r.get("kind"), r.get("kind") or "")
        out.append("── %s · %s · [[%s]]" % (k, r.get("title"), r.get("uid")))
        try:
            body = note_text(r)
        except MemoryError_ as exc:            # 找不到要講出來,不讓開席帶回靜默少一段
            out.append("(%s)" % exc)
            continue
        if body:
            out.append(body[:1200])
    return "\n".join(out)


def pointer_lines(uid: str, limit: int = 5, index: Path | None = None) -> list[str]:
    """接手包用(10-15 H1):跟這個 uid 有關的最新幾則,只給 種類 · 標題 · uid · 怎麼讀(不帶內文,吃不了多少預算)。
    交接(handoff)排最前面,其餘新→舊;已被取代的不列。讀不到索引回空。"""
    rows = related_to(uid, limit=max(limit, 20), skip_superseded=True, index=index)
    rows.sort(key=lambda r: (r.get("kind") != "handoff", -(r.get("created_ms") or 0)))
    out = []
    for r in rows[:max(1, limit)]:
        k = display("aib.memory.kind.%s" % r.get("kind"), r.get("kind") or "")
        out.append("%s · %s · uid %s → aib memory show --uid %s" % (k, r.get("title"), r.get("uid"), r.get("uid")))
    return out


def seat_brief(session_uuid: str, limit: int = 3) -> str:
    """換席用:這一席該帶回的記憶 —— 綁了專案就用專案 uid,沒綁就用這一席自己的 uid。讀不到回空字串。"""
    uuid = str(session_uuid or "")
    if not UUID_RE.fullmatch(uuid):
        return ""
    project = ""
    for f in sorted((aib_home() / "instances").glob("*/seats/w*.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if r.get("session_uuid") == uuid:
            project = str(r.get("project_uid") or "")
            break
    about = project if PROJECT_RE.fullmatch(project) else uuid
    return brief(about, limit)


USAGE_COMMANDS = (("write", "write"), ("list", "list"), ("show", "show"), ("brief", "brief"),
                  ("snapshot", "snapshot"), ("archive-sweep", "archive_sweep"), ("tidy", "tidy"))


def _help(key: str) -> str:
    return display("aib.memory.help." + key, key)


def memory_usage(prog: str = "aib memory") -> str:
    """`aib memory` 不帶子指令時的友善用法(各子指令一行說明,走 i18n)。"""
    lines = [_help("usage_title").replace("aib memory", prog, 1), "", _help("description"), "",
             _help("usage_commands")]
    width = max(len(c) for c, _ in USAGE_COMMANDS)
    lines += ["  %-*s  %s" % (width, c, _help(k)) for c, k in USAGE_COMMANDS]
    lines += ["", _help("usage_more")]
    return "\n".join(lines)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] in (["snapshot"], ["archive-sweep"]):   # 記憶整理第 1 包(10-13):備存與 90 天
        import memory_archive
        return memory_archive.main(args)
    if args[:1] == ["tidy"]:                             # 記憶整理第 3 包(10-13):整理記憶
        import memory_tidy
        return memory_tidy.main(args[1:])
    prog = os.environ.get("AIB_MEMORY_PROG") or "aib memory"
    if not args or args[0] in ("help",):      # 10-15:不帶子指令 ⇒ 友善用法,不印 argparse 原始錯誤
        print(memory_usage(prog), file=sys.stderr)
        return 2
    H = _help
    ap = argparse.ArgumentParser(prog=prog, description=H("description"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write", help=H("write"), description=H("write"))
    w.add_argument("--kind", required=True, choices=KINDS, help=H("kind"))
    w.add_argument("--title", required=True, help=H("title"))
    w.add_argument("--body", default="", help=H("body"))
    w.add_argument("--body-file", default="", help=H("body_file"))
    w.add_argument("--tags", default="", help=H("tags"))
    w.add_argument("--about", default="", help=H("about"))
    w.add_argument("--seat", default="", help=H("seat"))
    w.add_argument("--project", default="", help=H("project"))
    w.add_argument("--route", default="", help=H("route"))
    w.add_argument("--dry-run", action="store_true", help=H("dry_run"))
    w.add_argument("--json", action="store_true", help=H("json"))
    ls = sub.add_parser("list", help=H("list"), description=H("list"))
    ls.add_argument("--about", required=True, help=H("about"))
    ls.add_argument("--kind", default="", help=H("only_kind"))
    ls.add_argument("--limit", type=int, default=20, help=H("limit"))
    ls.add_argument("--json", action="store_true", help=H("json"))
    sh = sub.add_parser("show", help=H("show"), description=H("show"))
    sh.add_argument("--uid", required=True, help=H("uid"))
    br = sub.add_parser("brief", help=H("brief"), description=H("brief"))
    br.add_argument("--about", required=True, help=H("about"))
    br.add_argument("--limit", type=int, default=5, help=H("limit"))
    a = ap.parse_args(argv)
    try:
        if a.cmd == "write":
            body = a.body
            if a.body_file:
                body = Path(a.body_file).read_text(encoding="utf-8")
            rec = write(a.kind, a.title, body, [t for t in a.tags.split(",") if t.strip()],
                        [u for u in a.about.split(",") if u.strip()], a.seat, a.project, a.route, a.dry_run)
            if a.json:
                print(json.dumps(rec, ensure_ascii=False, indent=1))
            else:
                print("%s %s · %s · [[%s]] → %s" % (display("aib.memory.cli.dry_run", "(dry run)") if a.dry_run
                                                    else display("aib.memory.cli.written", "written"),
                                                    display("aib.memory.kind.%s" % rec["kind"], rec["kind"]),
                                                    rec["title"], rec["uid"], rec["path"]))
        elif a.cmd == "list":
            rows = related_to(a.about, a.kind, a.limit)
            if a.json:
                print(json.dumps(rows, ensure_ascii=False, indent=1))
            for r in ([] if a.json else rows):
                print("%s  %-6s %s  %s" % (time.strftime("%m-%d %H:%M", time.localtime((r.get("created_ms") or 0) / 1000)),
                                           r["uid"], display("aib.memory.kind.%s" % r.get("kind"), r.get("kind")),
                                           r.get("title")))
        elif a.cmd == "show":
            rec = next((r for r in entries() if r.get("uid") == a.uid), None)
            if not rec:
                print("aib memory: %s" % display("aib.memory.cli.not_found", "no note with id {uid}").replace("{uid}", a.uid),
                      file=sys.stderr)
                return 3
            print(note_text(rec))           # 找不到 ⇒ MemoryError_ ⇒ 下面印原因、回 2
        elif a.cmd == "brief":
            print(brief(a.about, a.limit))
    except MemoryError_ as exc:
        print("aib memory: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
