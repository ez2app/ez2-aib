#!/usr/bin/env python3
"""範圍外導引(10-16 維護者裁):席位遇到不在自己範圍的問題,先查既有人格與專案,有就建議重開那個舊席,
都沒有才建議新建。

    aib who-owns <關鍵字或句子> [--json] [--limit N]
        依序查 人格卡(scope_* frontmatter、名稱、正文)→ 專案卡(scope、標題)→ Vault 全文,
        依分數排序並附依據(哪張卡 / 哪篇筆記哪一行);命中時反查最後綁定的席位,給重開指令。
        離開碼:0 有命中(建議重開)、1 沒命中(建議新建)、2 用法或錯誤。
    aib scope set (--persona UID | --project proj:ID) [--sites ..] [--domains ..] [--keywords ..] [--aliases ..] [--replace]
    aib scope show (--persona UID | --project proj:ID) [--json]

範圍欄位(全部選填,舊卡不填不會壞):
    人格卡 = Vault 角色筆記 frontmatter 的平鋪清單 scope_sites / scope_domains / scope_keywords / scope_aliases
             (不用 aliases:那欄放 uid 與標題,是 Obsidian 的)
    專案卡 = card.json 的 "scope": {"sites": [], "domains": [], "keywords": [], "aliases": []}
網域比對:不分大小寫、去 http(s):// 與 www.、子網域算命中上層網域。
重開指令 = 分出新席(09-24 裁:接回 = 新開獨一無二的席位,載入舊對話 / 舊人格):
    AIB_FORK_FROM_SESSION_UUID=<舊 uuid> aib seat open <provider> <mode> <role>
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
for _p in (HERE, SRC / "cockpit", SRC / "memory", SRC / "persona_route"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import i18n_lib  # noqa: E402

FIELDS = ("sites", "domains", "keywords", "aliases")
FM_KEY = {f: "scope_" + f for f in FIELDS}
SCHEMA_VERSION = 1
UNKNOWN = object()

# 分數:同一張卡只取最高的那一項;依據全部列
SCORE = {"domain": 1.0, "subdomain": 0.95, "site": 0.9, "alias": 0.9, "name": 0.85, "keyword": 0.7,
         "title": 0.5, "body": 0.4, "vault_owned": 0.55, "vault": 0.3}
REOPEN_AT = 0.5                      # 命中到這個分數以上才建議重開(全文命中要查得到是哪個人格 / 專案寫的才算)

_TR = {}


def _ui(key, **params):
    loc = i18n_lib.resolve_locale()
    if loc not in _TR:
        _TR[loc] = i18n_lib.Translator(loc)
    return _TR[loc].t(key, params or None)


class WhoOwnsError(Exception):
    pass


# ---------------------------------------------------------------- 正規化
_DOMAIN_RE = re.compile(r"(?:[a-z][a-z0-9+.-]*://)?((?:[a-z0-9¡-￿](?:[a-z0-9¡-￿-]*[a-z0-9¡-￿])?\.)+"
                        r"[a-z¡-￿]{2,})(?::\d+)?(?:[/?#][^\s]*)?", re.I)


def norm_domain(s: str) -> str:
    """'https://WWW.Suntan.com.hk/a?b' → 'suntan.com.hk';不像網域 ⇒ ''。"""
    s = (s or "").strip().lower()
    m = _DOMAIN_RE.fullmatch(s)
    if not m:
        return ""
    d = m.group(1).rstrip(".")
    return d[4:] if d.startswith("www.") and d.count(".") >= 2 else d


def domains_in(text: str) -> list:
    """一句話裡的網域(去重、照出現順序)。"""
    out = []
    for m in _DOMAIN_RE.finditer((text or "").lower()):
        d = norm_domain(m.group(0))
        if d and d not in out:
            out.append(d)
    return out


def domain_hit(q: str, d: str):
    """q 是查詢網域、d 是範圍網域 ⇒ 'domain' | 'subdomain' | None。"""
    d = norm_domain(d) or (d or "").strip().lower()
    if not q or not d:
        return None
    if q == d:
        return "domain"
    if q.endswith("." + d):
        return "subdomain"
    return None


def fold(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().casefold()


def name_tokens(name: str) -> list:
    """卡名拆詞:英數一段、中日韓一段(「清展SEO主管」→ 清展 / seo / 主管),兩字以上才算。"""
    return [t for t in (m.casefold() for m in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+#-]*|[\u3400-\u9fff\uf900-\ufaff]+", name or ""))
            if len(t) >= 2]


def split_list(values) -> list:
    """['a,b', ' c '] → ['a','b','c'](去空、去重、保序)。"""
    out = []
    for v in values or []:
        for part in str(v).split(","):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out


# ---------------------------------------------------------------- 路徑
def state_root() -> Path:
    import seats_dir_lib
    return Path(seats_dir_lib.state_root()[0])


def vault_root():
    try:
        import card_registry
        return Path(card_registry.resolve_vault())
    except Exception:  # noqa: BLE001 —— 沒有 Vault 也能查卡
        return None


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------- frontmatter 的 scope_* 讀寫(只動這幾行)
_FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


def _parse_flow(v: str) -> list:
    v = v.strip()
    if not (v.startswith("[") and v.endswith("]")):
        return [v.strip("\"'")] if v else []
    try:
        got = json.loads(v)
        if isinstance(got, list):
            return [str(x) for x in got if str(x).strip()]
    except ValueError:
        pass
    return [p.strip().strip("\"'") for p in v[1:-1].split(",") if p.strip().strip("\"'")]


def read_fm_scope(text: str) -> dict:
    """角色筆記正文 → {sites, domains, keywords, aliases};認 `key: [a, "b"]` 與 `key:` 下一行起 `  - a`。"""
    out = {f: [] for f in FIELDS}
    m = _FM_RE.match(text or "")
    if not m:
        return out
    lines = m.group(1).splitlines()
    inv = {v: k for k, v in FM_KEY.items()}
    i = 0
    while i < len(lines):
        key, _, val = lines[i].partition(":")
        f = inv.get(key.strip()) if lines[i][:1] not in " \t" else None
        if f:
            if val.strip():
                out[f] = _parse_flow(val)
            else:
                j = i + 1
                while j < len(lines) and re.match(r"^\s+-\s*", lines[j]):
                    item = re.sub(r"^\s+-\s*", "", lines[j]).strip().strip("\"'")
                    if item:
                        out[f].append(item)
                    j += 1
                i = j - 1
        i += 1
    return out


def write_fm_scope(text: str, scope: dict) -> str:
    """把 scope_* 幾行換成新值(空清單 ⇒ 拿掉那行);frontmatter 其他行與正文一個字不動。沒有 frontmatter ⇒ 錯。"""
    m = _FM_RE.match(text or "")
    if not m:
        raise WhoOwnsError(_ui("aib.scope.no_frontmatter"))
    lines = m.group(1).splitlines()
    ours = set(FM_KEY.values())
    kept, i = [], 0
    while i < len(lines):
        key = lines[i].partition(":")[0].strip()
        if lines[i][:1] not in " \t" and key in ours:
            i += 1
            while i < len(lines) and re.match(r"^\s+-\s*", lines[i]):
                i += 1
            continue
        kept.append(lines[i])
        i += 1
    for f in FIELDS:
        vals = scope.get(f) or []
        if vals:
            kept.append("%s: %s" % (FM_KEY[f], json.dumps(vals, ensure_ascii=False)))
    nl = "\r\n" if "\r\n" in m.group(0) else "\n"
    head = "---" + nl + nl.join(kept) + nl + "---" + nl
    return head + text[m.end():]


# ---------------------------------------------------------------- 卡片來源
def persona_entries():
    """cards.json → [{uid, name, code, path, text, scope}];Vault 讀不到的卡只用名稱比。"""
    cards = (_read_json(state_root() / "identity" / "cards.json", {}) or {}).get("cards") or {}
    vault = vault_root()
    out = []
    for uid, row in sorted(cards.items()):
        if not isinstance(row, dict):
            continue
        path, text = None, ""
        rel = row.get("card_path_rel")
        if vault is not None and rel:
            p = (vault / rel).resolve()
            if vault.resolve() in p.parents and p.is_file():
                path = p
                text = p.read_text(encoding="utf-8", errors="replace")
        out.append({"uid": uid, "name": row.get("display_name") or row.get("code") or uid, "code": row.get("code") or "",
                    "rel": rel or "", "path": path, "text": text, "scope": read_fm_scope(text)})
    return out


def project_card_path(uid: str) -> Path:
    return state_root() / "projects" / uid / "card.json"


def project_entries():
    out = []
    for f in sorted(glob.glob(str(state_root() / "projects" / "*" / "card.json"))):
        card = _read_json(f, None)
        if not isinstance(card, dict) or not card.get("project_uid"):
            continue
        raw = card.get("scope") if isinstance(card.get("scope"), dict) else {}
        scope = {k: [str(x) for x in (raw.get(k) or []) if str(x).strip()] for k in FIELDS}
        out.append({"uid": card["project_uid"], "name": card.get("title") or card["project_uid"], "code": "",
                    "rel": str(Path(f).relative_to(state_root())), "path": Path(f), "text": "", "scope": scope})
    return out


# ---------------------------------------------------------------- 比對
def _ev(source, path, line, text, field):
    return {"source": source, "path": path, "line": line, "text": (text or "").strip()[:160], "field": field}


def _scope_line(text: str, key: str, needle: str):
    """依據要附行號:frontmatter 裡 key 那行(或其下的清單項)含 needle 的第一行;找不到 ⇒ (0, '')。"""
    m = _FM_RE.match(text or "")
    if not m or not needle:
        return 0, ""
    lines = m.group(1).splitlines()
    inside = False
    for i, line in enumerate(lines, 2):                     # 第 1 行是 ---
        if line[:1] not in " \t":
            inside = line.partition(":")[0].strip() == key
        if inside and needle.casefold() in line.casefold():
            return i, line
    return 0, ""


def match_card(entry: dict, kind: str, q: str, qdomains: list, needles: list):
    """一張卡 → (分數, 命中欄位, [依據]);沒中 ⇒ None。"""
    hits = []
    src = "card"
    path = entry["rel"]
    sc = entry["scope"]
    key = (lambda f: FM_KEY[f]) if kind == "persona" else (lambda f: "scope." + f)
    for d in sc["domains"]:
        for qd in qdomains:
            how = domain_hit(qd, d)
            if how:
                ln, txt = _scope_line(entry["text"], FM_KEY["domains"], d) if kind == "persona" else (0, d)
                hits.append((SCORE[how], key("domains"), _ev(src, path, ln, txt or d, key("domains"))))
    for s in sc["sites"]:
        sd = norm_domain(s)
        if fold(s) == fold(q) or (sd and any(domain_hit(qd, sd) for qd in qdomains)):
            ln, txt = _scope_line(entry["text"], FM_KEY["sites"], s) if kind == "persona" else (0, s)
            hits.append((SCORE["site"], key("sites"), _ev(src, path, ln, txt or s, key("sites"))))
    for a in sc["aliases"]:
        if fold(a) and (fold(a) == fold(q) or (len(fold(a)) >= 2 and fold(a) in fold(q))):
            ln, txt = _scope_line(entry["text"], FM_KEY["aliases"], a) if kind == "persona" else (0, a)
            hits.append((SCORE["alias"], key("aliases"), _ev(src, path, ln, txt or a, key("aliases"))))
    for k in sc["keywords"]:
        if fold(k) and fold(k) in fold(q):
            ln, txt = _scope_line(entry["text"], FM_KEY["keywords"], k) if kind == "persona" else (0, k)
            hits.append((SCORE["keyword"], key("keywords"), _ev(src, path, ln, txt or k, key("keywords"))))
    names = [entry["name"]] + ([entry["code"]] if entry["code"] else [])
    for n in names:
        if fold(n) and fold(n) == fold(q):
            hits.append((SCORE["name"], "name", _ev(src, path, 0, n, "name")))
        elif fold(n) and len(fold(n)) >= 2 and len(fold(q)) >= 2 and (fold(n) in fold(q) or fold(q) in fold(n)):
            hits.append((SCORE["title"], "name", _ev(src, path, 0, n, "name")))
        else:                                   # 一句話裡出現卡名的大半個詞(清展 + SEO ⇒ 清展SEO主管)
            toks = name_tokens(n)
            got = [t for t in toks if t in fold(q)]
            if len(got) >= 2 and len(got) * 2 >= len(toks):
                hits.append((round(0.6 * len(got) / len(toks), 2), "name", _ev(src, path, 0, n, "name")))
    if entry["text"]:
        body = entry["text"]
        fm = _FM_RE.match(body)
        start = body.count("\n", 0, fm.end()) + 1 if fm else 1
        for i, line in enumerate(body.splitlines()[start - 1:], start):
            if any(n.casefold() in line.casefold() for n in needles):
                hits.append((SCORE["body"], "body", _ev(src, path, i, line, "body")))
                break
    if not hits:
        return None
    hits.sort(key=lambda h: -h[0])
    seen, evidence = set(), []
    for _s, _f, e in hits:
        k = (e["field"], e["line"], e["text"])
        if k not in seen:
            seen.add(k)
            evidence.append(e)
    return hits[0][0], hits[0][1], evidence


def vault_hits(needles: list, limit: int, skip=()):
    """Vault 全文:每篇筆記第一個命中行。筆記 frontmatter 有 persona_uid / project_uid 就帶上(反查席位用)。"""
    vault = vault_root()
    if vault is None or not needles:
        return []
    import card_registry
    lowered = [n.casefold() for n in needles]
    out = []
    for dp, dn, fn in os.walk(vault):
        dn[:] = sorted(d for d in dn if not d.startswith("."))
        for f in sorted(fn):
            if not f.endswith(".md"):
                continue
            p = Path(dp, f)
            if str(p.relative_to(vault)) in skip:            # 人格卡本身已在卡片那段比過
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            low = text.casefold()
            if not any(n in low for n in lowered):
                continue
            fm = card_registry.parse_frontmatter(text) or {}
            for i, line in enumerate(text.splitlines(), 1):
                if any(n in line.casefold() for n in lowered):
                    rel = str(p.relative_to(vault))
                    # aib memory 寫的筆記帶 custom.aib.persona_uid / project_uid(誰寫的)
                    out.append({"uid": fm.get("uid") or p.stem, "name": fm.get("title") or p.stem,
                                "persona_uid": fm.get("custom.aib.persona_uid") or fm.get("persona_uid") or "",
                                "project_uid": fm.get("custom.aib.project_uid") or fm.get("project_uid") or "",
                                "evidence": [_ev("vault", rel, i, line, "body")]})
                    break
            if len(out) >= limit:
                return out
    return out


# ---------------------------------------------------------------- 席位反查
def _seat_records():
    """seat_id → 最新的席位檔紀錄(含已收的),附艙碼。"""
    best = {}
    base = state_root() / "instances"
    for f in glob.glob(str(base / "*" / "seats" / "*.json")) + glob.glob(str(base / "*" / "seats" / "retired" / "*.json")):
        rec = _read_json(f, None)
        if not isinstance(rec, dict) or not rec.get("seat_id"):
            continue
        cabin = Path(f).parts[-3] if Path(f).parent.name == "seats" else Path(f).parts[-4]
        rec = dict(rec, _cabin=cabin.rsplit("-", 1)[-1])
        cur = best.get(rec["seat_id"])
        if cur is None or (rec.get("last_used_epoch") or rec.get("started_epoch") or 0) > (cur.get("last_used_epoch") or cur.get("started_epoch") or 0):
            best[rec["seat_id"]] = rec
    return best


def _last_seat_for_persona(uid: str):
    rows = []
    for f in glob.glob(str(state_root() / "identity" / "seat-continuity" / "*.json")):
        rec = _read_json(f, None)
        if isinstance(rec, dict) and rec.get("persona_uid") == uid and rec.get("seat_id"):
            rows.append((rec.get("updated_wall_ms") or 0, rec["seat_id"], rec.get("session_uuid") or ""))
    if not rows:
        return None
    ms, seat_id, sess = max(rows)
    return {"seat_id": seat_id, "session_uuid": sess,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ms / 1000)) if ms else None}


def _last_seat_for_project(uid: str):
    data = _read_json(state_root() / "state" / "project-seats.json", {}) or {}
    seats = (((data.get("projects") or {}).get(uid) or {}).get("seats")) or {}
    rows = [((rec or {}).get("since") or "", sid) for sid, rec in seats.items()]
    if not rows:
        return None
    since, seat_id = max(rows)
    return {"seat_id": seat_id, "session_uuid": "", "at": since or None}


def seat_info(kind: str, uid: str, records: dict):
    """→ (席位地址或 None, 最後綁定時間或 None, 重開指令或 None)。"""
    hit = _last_seat_for_persona(uid) if kind == "persona" else _last_seat_for_project(uid)
    if not hit:
        return None, None, None
    rec = records.get(hit["seat_id"]) or {}
    sess = hit["session_uuid"] or rec.get("session_uuid") or ""
    try:
        import seat_address
        code = seat_address.uuid_tail5(sess) if sess else ""
    except Exception:  # noqa: BLE001
        code = sess[-5:] if sess else ""
    addr = "%s/%s" % (rec.get("_cabin") or "?", code) if code else None
    cmd = None
    if sess and rec.get("provider") and rec.get("mode") and rec.get("role"):
        cmd = "AIB_FORK_FROM_SESSION_UUID=%s aib seat open %s %s %s" % (sess, rec["provider"], rec["mode"], rec["role"])
    return addr, hit["at"], cmd


# ---------------------------------------------------------------- who-owns
def who_owns(query: str, limit: int = 10) -> dict:
    q = (query or "").strip()
    if not q:
        raise WhoOwnsError(_ui("aib.who_owns.empty_query"))
    qdomains = domains_in(q)
    # 全文找什麼:句子裡的網域;沒有網域時只有短關鍵字才整串找(整句話拿去全文比對找不到東西)
    needles = qdomains or ([q] if len(q) <= 24 and not re.search(r"\s", q) else [])
    records = _seat_records()
    matches = []
    for kind, entries in (("persona", persona_entries()), ("project", project_entries())):
        for e in entries:
            got = match_card(e, kind, q, qdomains, needles)
            if not got:
                continue
            score, field, evidence = got
            seat, at, cmd = seat_info(kind, e["uid"], records)
            matches.append({"kind": kind, "uid": e["uid"], "name": e["name"], "seat": seat, "score": round(score, 2),
                            "field": field, "evidence": evidence, "last_bound": at, "reopen_cmd": cmd})
    owners = {(m["kind"], m["uid"]) for m in matches}
    cards_rel = {e["rel"] for e in persona_entries() if e["rel"]}
    for v in vault_hits(needles, limit=max(limit, 10), skip=cards_rel):
        owner = ("persona", v["persona_uid"]) if v["persona_uid"] else (("project", v["project_uid"]) if v["project_uid"] else None)
        seat, at, cmd = seat_info(owner[0], owner[1], records) if owner else (None, None, None)
        if owner in owners:                              # 那張卡已經列在上面:筆記只當旁證
            seat, at, cmd = None, None, None
        score = SCORE["vault_owned"] if owner and owner not in owners and seat else SCORE["vault"]
        same = next((m for m in matches if m["kind"] == "vault" and owner and m.get("owner") == "%s:%s" % owner), None)
        if same is not None:                             # 同一個作者的筆記併成一筆,依據最多留 3 條
            if len(same["evidence"]) < 3:
                same["evidence"].extend(v["evidence"])
            continue
        matches.append({"kind": "vault", "uid": v["uid"], "name": v["name"], "seat": seat, "score": score,
                        "field": "body", "evidence": v["evidence"], "last_bound": at, "reopen_cmd": cmd,
                        "owner": ("%s:%s" % owner) if owner else None})
    matches.sort(key=lambda m: (-m["score"], {"persona": 0, "project": 1, "vault": 2}[m["kind"]], m["name"]))
    matches = matches[:limit]
    reopen = bool(matches) and matches[0]["score"] >= REOPEN_AT
    title = qdomains[0] if qdomains else q
    return {"version": SCHEMA_VERSION, "query": q, "verdict": "reopen" if reopen else "new", "matches": matches,
            "new_cmd": None if reopen else 'aib newproj --title %s' % json.dumps(title, ensure_ascii=False),
            "new_role_cmd": None if reopen else 'aib newrole --name %s' % json.dumps(title, ensure_ascii=False)}


def render(res: dict) -> str:
    out = [_ui("aib.who_owns.head_reopen" if res["verdict"] == "reopen" else "aib.who_owns.head_new", query=res["query"])]
    for m in res["matches"]:
        out.append(_ui("aib.who_owns.match_line", kind=_ui("aib.who_owns.kind_" + m["kind"]), name=m["name"],
                       uid=m["uid"], score="%.2f" % m["score"], seat=m["seat"] or "-"))
        for e in m["evidence"][:3]:
            out.append("      %s%s  %s" % (e["path"], (":%d" % e["line"]) if e["line"] else "", e["text"]))
        if m["reopen_cmd"]:
            out.append("      " + _ui("aib.who_owns.reopen_hint", cmd=m["reopen_cmd"]))
    if res["verdict"] == "new":
        out.append(_ui("aib.who_owns.new_hint", project=res["new_cmd"], role=res["new_role_cmd"]))
    return "\n".join(out)


# ---------------------------------------------------------------- scope set / show
def _persona_path(uid: str) -> Path:
    for e in persona_entries():
        if e["uid"] == uid:
            if e["path"] is None:
                raise WhoOwnsError(_ui("aib.scope.card_unreadable", uid=uid))
            return e["path"]
    raise WhoOwnsError(_ui("aib.scope.persona_unknown", uid=uid))


def merged(old: dict, new: dict, replace: bool) -> dict:
    out = {}
    for f in FIELDS:
        if f in new:
            base = [] if replace else list(old.get(f) or [])
            for v in new[f]:
                if v not in base:
                    base.append(v)
            out[f] = base
        else:
            out[f] = list(old.get(f) or [])
    return out


def _clean(f: str, vals: list) -> list:
    if f != "domains":
        return vals
    out = []
    for v in vals:
        d = norm_domain(v)
        if not d:
            raise WhoOwnsError(_ui("aib.scope.bad_domain", value=v))
        if d not in out:
            out.append(d)
    return out


def scope_show(persona: str = "", project: str = "") -> dict:
    if persona:
        return {"kind": "persona", "uid": persona, "scope": read_fm_scope(_persona_path(persona).read_text(encoding="utf-8"))}
    card = _read_json(project_card_path(project), None)
    if not isinstance(card, dict) or card.get("project_uid") != project:
        raise WhoOwnsError(_ui("aib.scope.project_unknown", uid=project))
    raw = card.get("scope") if isinstance(card.get("scope"), dict) else {}
    return {"kind": "project", "uid": project, "scope": {k: list(raw.get(k) or []) for k in FIELDS}}


def scope_set(persona: str = "", project: str = "", new: dict | None = None, replace: bool = False,
              rescan=None) -> dict:
    new = {f: _clean(f, v) for f, v in (new or {}).items()}
    if not new:
        raise WhoOwnsError(_ui("aib.scope.nothing_to_set"))
    import memory_archive
    if persona:
        import memory_tidy
        path = _persona_path(persona)
        with memory_tidy._Lock(memory_tidy.tidy_dir() / ("persona-%s.lock" % persona)):   # 跟 aib memory tidy 同一把
            text = path.read_text(encoding="utf-8")
            scope = merged(read_fm_scope(text), new, replace)
            out_text = write_fm_scope(text, scope)
            if out_text == text:
                return {"kind": "persona", "uid": persona, "scope": scope, "changed": False}
            snap = memory_archive.snapshot(path, "persona", persona)
            if snap["status"] == "failed":
                raise WhoOwnsError(_ui("aib.scope.snapshot_failed", error=snap.get("error") or "?"))
            tmp = path.with_name(path.name + ".tmp-%d" % os.getpid())
            tmp.write_text(out_text, encoding="utf-8")
            os.replace(tmp, path)
            # 記進 tidy 的寫入紀錄:別席 collect 看到新指紋知道是程式寫的,不當成手改
            import hashlib
            with open(memory_tidy.tidy_dir() / "writes.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"sha": hashlib.sha256(out_text.encode("utf-8")).hexdigest(), "uid": persona,
                                     "seat": "scope", "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}) + "\n")
        rc = (rescan or _rescan_cards)()
        return {"kind": "persona", "uid": persona, "scope": scope, "changed": True, "cards_rescan": rc}
    import importlib.util
    spec = importlib.util.spec_from_file_location("new_project_for_scope", str(SRC.parent / "tools" / "project" / "new_project.py"))
    NP = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NP)
    path = project_card_path(project)
    with NP._card_lock(path):                                   # 跟 aib newproj / memory tidy 同一把卡鎖
        raw = _read_json(path, None)
        if not isinstance(raw, dict) or raw.get("project_uid") != project:
            raise WhoOwnsError(_ui("aib.scope.project_unknown", uid=project))
        old = raw.get("scope") if isinstance(raw.get("scope"), dict) else {}
        scope = merged(old, new, replace)
        if scope == {k: list(old.get(k) or []) for k in FIELDS}:
            return {"kind": "project", "uid": project, "scope": scope, "changed": False}
        snap = memory_archive.snapshot(path, "project", project)
        if snap["status"] == "failed":
            raise WhoOwnsError(_ui("aib.scope.snapshot_failed", error=snap.get("error") or "?"))
        raw["scope"] = scope
        NP._atomic_write_json(path, raw)
    return {"kind": "project", "uid": project, "scope": scope, "changed": True}


def _rescan_cards():
    """改了角色筆記 ⇒ 重掃卡片索引(指紋跟著變;綁這張卡的席下次開席會看到「卡已更新」)。"""
    vault = vault_root()
    if vault is None:
        return "skipped"
    r = subprocess.run([sys.executable, str(SRC / "memory" / "card_registry.py"), "scan", "--vault", str(vault)],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
    return r.returncode


# ---------------------------------------------------------------- CLI
def main_who(argv) -> int:
    ap = argparse.ArgumentParser(prog="aib who-owns", description=_ui("aib.who_owns.help.desc"))
    ap.add_argument("query", nargs="+", help=_ui("aib.who_owns.help.query"))
    ap.add_argument("--json", action="store_true", help=_ui("aib.who_owns.help.json"))
    ap.add_argument("--limit", type=int, default=10, help=_ui("aib.who_owns.help.limit"))
    args = ap.parse_args(argv)
    try:
        res = who_owns(" ".join(args.query), limit=max(1, args.limit))
    except WhoOwnsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(res, ensure_ascii=False, indent=1) if args.json else render(res))
    return 0 if res["verdict"] == "reopen" else 1


def main_scope(argv) -> int:
    ap = argparse.ArgumentParser(prog="aib scope", description=_ui("aib.scope.help.desc"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("set", "show"):
        p = sub.add_parser(name, help=_ui("aib.scope.help.%s" % name))
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("--persona", help=_ui("aib.scope.help.persona"))
        g.add_argument("--project", help=_ui("aib.scope.help.project"))
        if name == "set":
            for f in FIELDS:
                p.add_argument("--" + f, action="append", help=_ui("aib.scope.help.field_%s" % f))
            p.add_argument("--replace", action="store_true", help=_ui("aib.scope.help.replace"))
        p.add_argument("--json", action="store_true", help=_ui("aib.who_owns.help.json"))
    args = ap.parse_args(argv)
    try:
        if args.cmd == "show":
            res = scope_show(args.persona or "", args.project or "")
        else:
            new = {f: split_list(getattr(args, f)) for f in FIELDS if getattr(args, f)}
            res = scope_set(args.persona or "", args.project or "", new, args.replace)
    except WhoOwnsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        if args.cmd == "set":
            print(_ui("aib.scope.changed" if res["changed"] else "aib.scope.unchanged", uid=res["uid"]))
        for f in FIELDS:
            print("  %-9s %s" % (f, ", ".join(res["scope"][f]) or "-"))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["scope"]:
        return main_scope(argv[1:])
    if argv[:1] == ["who-owns"]:
        return main_who(argv[1:])
    return main_who(argv)


if __name__ == "__main__":
    sys.exit(main())
