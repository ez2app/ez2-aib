#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib 擴充的八種去向(10-14 the maintainer「除了要轉成外掛的,也要有轉成佈景、轉成 skill 之類的」「全都要做」)。單一來源:
發公開版的去向檢查(tools/oss/publish_public.py)、建擴充包(tools/oss/plugin_kit.py)、使用者裝擴充(aib ext)都讀這裡。

去向           放哪裡                         會不會跑程式
core     核心       公開版本體                     —
plugin   外掛       ~/.ez2-aib/plugins/<名>       會(新指令 / 新服務;開發中或要授權)
theme    佈景       ~/.ez2-aib/themes/<名>        不會(只有配色資料)
skill    skill      ~/.ez2-aib/skills/<名>        會(給 AI 的做法,可附腳本)
mod      mod        ~/.ez2-aib/mods/<名>          會(掛在 Claude Code 介面:畫面、掛勾、狀態列)
persona  角色卡     ~/.ez2-aib/personas/<名>      不會(職責、紅線、知識域)
policy   設定預設   ~/.ez2-aib/policy/<名>        不會(門檻數字、規則檔)
maintainer 維護者   兩邊都不帶                    —

一支檔的去向怎麼來(先到先算):
  1. 檔頭前 15 行寫 `aib-kind: <去向>`(任何註解寫法都行:# // <!-- -->)—— 新檔建的時候就標
  2. tools/oss/kinds.json 的 {路徑: 去向} —— JSON / 圖片這類寫不了註解的檔,或不想為了標記改動舊檔(改舊檔會讓讀它的測試全部重跑)
  3. 擴充包說明檔 tools/oss/plugins/<名>.json 列的 files、core、engines ⇒ 說明檔的 kind(沒寫 = plugin);指到資料夾 ⇒ 底下每支都算
  4. 路徑慣例:themes/ ⇒ theme、skills/ ⇒ skill、tools/claude-mods/ ⇒ mod、policy/ ⇒ policy
  都沒有 ⇒ 不知道(去向檢查會要人判)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

KINDS = {
    "core": {"zh": "核心", "aliases": ("開源", "核心", "core"), "home": None, "code": None},
    "plugin": {"zh": "外掛", "aliases": ("外掛", "plugin"), "home": "plugins", "code": True},
    "theme": {"zh": "佈景", "aliases": ("佈景", "theme"), "home": "themes", "code": False},
    "skill": {"zh": "skill", "aliases": ("skill",), "home": "skills", "code": True},
    "mod": {"zh": "mod", "aliases": ("mod", "Claude Code mod"), "home": "mods", "code": True},
    "persona": {"zh": "角色卡", "aliases": ("角色卡", "persona"), "home": "personas", "code": False},
    "policy": {"zh": "設定預設", "aliases": ("設定預設", "policy"), "home": "policy", "code": False},
    "maintainer": {"zh": "維護者", "aliases": ("維護者", "私有", "maintainer"), "home": None, "code": None},
}
INSTALLABLE = tuple(k for k, v in KINDS.items() if v["home"])        # 能用 aib ext 裝的
NOT_PUBLIC = ("plugin", "maintainer")                               # 不准出現在公開版本體的
PATH_RULES = (("themes/", "theme"), ("skills/", "skill"), ("tools/claude-mods/", "mod"), ("policy/", "policy"))
MARK_RE = re.compile(r"aib-kind:\s*([A-Za-z一-鿿 ]+?)\s*(?:-->|\*/|$)", re.M)
MARK_LINES = 15
PERSONA_FILE = "角色.md"                                  # 角色卡本體(aib newrole 建的那份)
PERSONA_HEADINGS = ("## 職責", "## 紅線", "## 知識域")     # 角色卡一定要有的三段


def kind_of_label(text):
    """「外掛 — 理由」「theme」「私有」⇒ 去向代碼;認不得 ⇒ None。開頭比對,後面可接理由。"""
    t = (text or "").strip()
    for key, v in KINDS.items():
        for a in sorted(v["aliases"], key=len, reverse=True):
            if t == a or t.startswith(a + " ") or t.startswith(a + "—") or t.startswith(a + "(") \
                    or t.startswith(a + "(") or t.lower() == a.lower():
                return key
    return None


def labels():
    """給人看的清單:「核心 / 外掛 / 佈景 / skill / mod / 角色卡 / 設定預設 / 維護者」。"""
    return " / ".join(v["zh"] for v in KINDS.values())


def marker_kind(path):
    """檔頭前 MARK_LINES 行的 `aib-kind: <去向>`;沒有、讀不了、寫錯 ⇒ None。"""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            head = "".join(next(fh, "") for _ in range(MARK_LINES))
    except OSError:
        return None
    m = MARK_RE.search(head)
    return kind_of_label(m.group(1)) if m else None


def load_index(repo):
    try:
        d = json.loads((Path(repo) / "tools" / "oss" / "kinds.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): kind_of_label(str(v)) for k, v in (d.get("files") or {}).items() if kind_of_label(str(v))}


def spec_kinds(repo):
    """{私有倉路徑: 去向},從 tools/oss/plugins/*.json 的 files / core / engines 來。指到資料夾的存成「路徑/」(kind_of 照前綴比);
    core / engines 是 10-14 多家 AI 接頭加的欄位 —— 沒算進來時接頭底下標不了 aib-kind 的 json / SKILL.md 發版會被列成沒寫去向。"""
    out = {}
    for spec in sorted((Path(repo) / "tools" / "oss" / "plugins").glob("*.json")):
        try:
            d = json.loads(spec.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        k = kind_of_label(str(d.get("kind") or "plugin")) or "plugin"
        srcs = [v for f in ("files", "core", "engines") for v in ((d.get(f) or {}).values() if isinstance(d.get(f), dict) else [])]
        for src in srcs:
            src = str(src).rstrip("/")
            out[src + "/" if (Path(repo) / src).is_dir() else src] = k
    return out


def kind_of(repo, rel, index=None, specs=None, file=None):
    """(去向, 從哪來:marker / index / spec / path)或 (None, None)。rel 是私有倉路徑;file 給了就讀那支(例如匯出樹裡的)。"""
    k = marker_kind(file or (Path(repo) / rel))
    if k:
        return k, "marker"
    index = load_index(repo) if index is None else index
    if rel in index:
        return index[rel], "index"
    specs = spec_kinds(repo) if specs is None else specs
    if rel in specs:
        return specs[rel], "spec"
    for pre in sorted((p for p in specs if p.endswith("/")), key=len, reverse=True):   # 說明檔指到資料夾:最長前綴勝
        if rel.startswith(pre):
            return specs[pre], "spec"
    for pre, k in PATH_RULES:
        if rel.startswith(pre):
            return k, "path"
    return None, None


def main(argv):
    """python3 src/cockpit/ext_kinds.py which <路徑…>:印每支檔的去向與從哪來(在倉根跑)。"""
    if len(argv) >= 2 and argv[0] == "which":
        repo = Path.cwd()
        index, specs = load_index(repo), spec_kinds(repo)
        for rel in argv[1:]:
            k, src = kind_of(repo, rel, index=index, specs=specs)
            print("%s\t%s\t%s" % (rel, KINDS[k]["zh"] if k else "?", src or "—"))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
