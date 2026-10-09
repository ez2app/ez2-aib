#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""交接單固定六格的解析器(第 41 項,含 57)—— 唯讀,不碰 git,不碰活艙。

規格正本:docs/handbook/HANDOFF-FIELDS.md。這裡只做三件事:
  1. 找圍欄 ```handoff-fields(固定層);找不到就退到節標題關鍵字(標題層,給 p7 之前的舊單)。
  2. 六格逐格帶來源行號;缺的格列在 missing,不留空白、不用前一格補。
  3. 「接自誰」= from_seat:圍欄 `接自:` 優先,否則從檔名推,推不出印 量不到。

★ 為什麼標題層要存在:第五棒 p7 是第 41 項的第一個實例,而它寫在圍欄定案之前。
  尺要求 p7 解得出六格 —— 但**新單子沒圍欄一律紅**(--strict-fixed),標題層不是給新單用的。
★ 失效方向刻意選「紅」:六格任一格找不到 ⇒ rc=3;不是交接單 ⇒ rc=2。
  找不到與找錯了在版面上同形,所以每格都印行號,讓人一眼能對回原文。

rc:0 六格齊 / 2 檔不存在或不像交接單 / 3 缺格 / 4 --strict-fixed 而只有標題層
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import sys

_here = pathlib.Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
_fallback = pathlib.Path.home() / "ez2-aib" / "src" / "cockpit"
if str(_fallback) not in sys.path:
    sys.path.append(str(_fallback))
try:
    import i18n_lib  # noqa: E402
except Exception:  # pragma: no cover - 主庫不在時仍要能印 JSON
    i18n_lib = None

LOC = os.environ.get("EZ2_AIB_LOCALE", "zh-TW")
# ★ 字典預設讀**自己這棵樹**的 i18n/(閘跑的是你站的那棵樹那一版);沒有才退 i18n_lib 預設(主庫)。治具可用環境變數指。
_own_i18n = pathlib.Path(__file__).resolve().parents[2] / "i18n"
I18N_DIR = os.environ.get("AIB_HANDOFF_FIELDS_I18N_DIR") or (str(_own_i18n) if _own_i18n.is_dir() else None)


def T(key, params=None):
    if i18n_lib is None:
        return "!!%s!!" % key
    return i18n_lib.t(key, params, locale=LOC, i18n_dir=I18N_DIR)


SCHEMA = "aib-handoff-fields/v1"
RC_OK, RC_USAGE, RC_MISSING, RC_LEGACY_ONLY = 0, 2, 3, 4

# 六格鍵(逐字;規格表第一欄)。順序=輸出順序。
# ★ 鍵的字面住在字典 aib.handoff_fields.key.*,**固定取 zh-TW**:圍欄鍵是規格逐字,不隨顯示語系變;
#   放字典是為了讓工具檔零硬編中文(tools-i18n-ratchet 逐來源計債),不是為了翻譯它。
KEY_SLUGS = ["mainline_sha", "inflight", "waiting_on", "removable_trees", "three_memories", "monitor"]


def _label(slug):
    if i18n_lib is None:
        raise SystemExit("i18n_lib not importable: handoff key labels have no source")
    return i18n_lib.t("aib.handoff_fields.key." + slug, None, locale="zh-TW", i18n_dir=I18N_DIR)


KEYS = [_label(s) for s in KEY_SLUGS]
KEY_FROM = _label("from")
FENCE_OPEN = re.compile(r"^```\s*handoff-fields\s*$")
FENCE_CLOSE = re.compile(r"^```\s*$")
FENCE_SCAN_LINES = 40
KV = re.compile(r"^(?P<k>[^:：]+?)\s*[:：]\s*(?P<v>.+?)\s*$")
FNAME = re.compile(r"HANDOFF-(?P<role>[a-z0-9]+)-(?P<seat>[0-9a-f]{5})-(?P<cabin>[0-9a-z]+)-(?P<date>\d{4})-p(?P<n>\d+)\.md$")
TITLE_FROM = re.compile(r"(?P<role>[a-z]+)@(?P<seat>[0-9a-f]{5})")
SHA = re.compile(r"\b[0-9a-f]{8,40}\b")
HEADING = re.compile(r"^#{1,6}\s+(?P<t>.+?)\s*$")

# 標題層:每格 → (節標題要含的關鍵字們(任一命中), 節內要抓什麼)
# 標題層的節標題關鍵字只拿來比對(不流向任何輸出),所以留字面。
LEGACY = {
    KEYS[0]: (["主線"], "sha"),
    KEYS[1]: (["在飛"], "sha"),
    KEYS[2]: (["等 the maintainer", "等推", "等誰"], "section"),
    KEYS[3]: (["可砍"], "section"),
    KEYS[4]: (["三記憶"], "section"),
    KEYS[5]: (["在飛"], "monitor"),
}
MONITOR_MARKS = ("log", "DONE", "STOP")


def _read(path):
    p = pathlib.Path(path).expanduser()
    if not p.is_file():
        return None, None
    return p, p.read_text(encoding="utf-8", errors="replace").splitlines()


def parse_fixed(lines):
    """固定層:回 (fields, from_line) 或 (None, None)。fields = {鍵: [{"value","line"}]}"""
    start = None
    for i, ln in enumerate(lines[:FENCE_SCAN_LINES]):
        if FENCE_OPEN.match(ln.strip()):
            start = i
            break
    if start is None:
        return None, None
    fields, from_v = {}, None
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if FENCE_CLOSE.match(ln.strip()):
            break
        m = KV.match(ln)
        if not m:
            continue
        k, v = m.group("k").strip(), m.group("v").strip()
        if k == KEY_FROM:
            from_v = {"value": v, "line": j + 1}
        elif k in KEYS:
            fields.setdefault(k, []).append({"value": v, "line": j + 1})
    return fields, from_v


def _sections(lines):
    """[(標題, 起行索引, 迄行索引)];沒有標題的檔回空表。"""
    heads = [(i, HEADING.match(ln).group("t")) for i, ln in enumerate(lines) if HEADING.match(ln)]
    out = []
    for n, (i, t) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        out.append((t, i, end))
    return out


def parse_legacy(lines):
    fields = {}
    secs = _sections(lines)
    for key, (words, mode) in LEGACY.items():
        for title, a, b in secs:
            if not any(w in title for w in words):
                continue
            body = lines[a + 1:b]
            if mode == "sha":
                for off, ln in enumerate(body):
                    m = SHA.search(ln)
                    if m:
                        fields.setdefault(key, []).append({"value": m.group(0), "line": a + 2 + off})
                        break
            elif mode == "monitor":
                hits = [(off, ln) for off, ln in enumerate(body) if any(w in ln for w in MONITOR_MARKS)]
                if len({w for _, ln in hits for w in MONITOR_MARKS if w in ln}) == len(MONITOR_MARKS):
                    fields.setdefault(key, []).append({
                        "value": " ; ".join(ln.strip()[:120] for _, ln in hits), "line": a + 2 + hits[0][0]})
            else:
                text = " ".join(ln.strip() for ln in body if ln.strip())
                if text:
                    fields.setdefault(key, []).append({"value": text[:300], "line": a + 2})
    return fields


BATON = re.compile(r"第\s*([一二三四五六七八九十\d]+)\s*棒\s*→")
CN_NUM = {c: i + 1 for i, c in enumerate("一二三四五六七八九")}
CN_NUM["十"] = 10


def _baton_of(text):
    """從「第五棒 → 第六棒」取**交出**的那一棒;取不到回 None(a seat 要求:整數或 null,不給字串)。"""
    m = BATON.search(text or "")
    if not m:
        return None
    x = m.group(1)
    return int(x) if x.isdigit() else CN_NUM.get(x)


def from_seat(path, from_v, lines):
    r = _from_seat(path, from_v, lines)
    if "baton" not in r:
        r["baton"] = _baton_of(from_v["value"] if from_v else "") or _baton_of(lines[0] if lines else "")
    return r


def _from_seat(path, from_v, lines):
    if from_v:
        m = TITLE_FROM.search(from_v["value"])
        if m:
            return {"seat": m.group("seat"), "role": m.group("role"), "source": "fence", "line": from_v["line"]}
    m = FNAME.search(path.name)
    if m:
        return {"seat": m.group("seat"), "role": m.group("role"), "source": "filename",
                "cabin": m.group("cabin"), "date": m.group("date"), "baton": int(m.group("n"))}
    for i, ln in enumerate(lines[:5]):
        m = TITLE_FROM.search(ln)
        if m:
            return {"seat": m.group("seat"), "role": m.group("role"), "source": "title", "line": i + 1}
    return {"seat": None, "role": None, "source": "unmeasured", "why": T("aib.handoff_fields.why_no_seat")}


def analyze(path):
    p, lines = _read(path)
    if p is None:
        return {"schema": SCHEMA, "path": str(path), "error": "not-found"}, RC_USAGE
    fields, from_v = parse_fixed(lines)
    layer = "fixed"
    if fields is None:
        fields, layer = parse_legacy(lines), "legacy"
        if not fields and not any("交接" in ln for ln in lines[:5]):
            return {"schema": SCHEMA, "path": str(p), "error": "not-a-handoff"}, RC_USAGE
    missing = [k for k in KEYS if k not in fields]
    out = {
        "schema": SCHEMA, "path": str(p), "layer": layer,
        "from": from_seat(p, from_v, lines), "fields": {k: fields.get(k, []) for k in KEYS},
        "missing": missing,
        "measured_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    out["from_seat"] = out["from"]["seat"]
    return out, (RC_MISSING if missing else RC_OK)


def render_human(res):
    if "error" in res:
        return [T("aib.handoff_fields.error", {"path": res["path"], "why": res["error"]})]
    lines = [T("aib.handoff_fields.head", {"path": res["path"], "mode": res["layer"],
                                            "seat": res["from_seat"] or T("aib.handoff_fields.unmeasured")})]
    for k in KEYS:
        items = res["fields"][k]
        if not items:
            lines.append(T("aib.handoff_fields.missing", {"key": k}))
            continue
        for it in items:
            lines.append(T("aib.handoff_fields.row", {"key": k, "value": it["value"], "line": it["line"]}))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=T("aib.handoff_fields.cli_desc"))
    ap.add_argument("path")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict-fixed", action="store_true", help=T("aib.handoff_fields.cli_strict_help"))
    a = ap.parse_args(argv)
    res, rc = analyze(a.path)
    if rc == RC_OK and a.strict_fixed and res.get("layer") == "legacy":
        rc = RC_LEGACY_ONLY
        res["strict_fixed"] = "legacy-only"
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("\n".join(render_human(res)))
    if rc == RC_MISSING:
        print(T("aib.handoff_fields.rc_missing", {"keys": ", ".join(res["missing"])}), file=sys.stderr)
    elif rc == RC_LEGACY_ONLY:
        print(T("aib.handoff_fields.rc_legacy"), file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
