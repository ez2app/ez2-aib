#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""人格卡的「身分」與「寫作規則(12 維)」兩段(記憶整理第 4 包;docs/DESIGN-memory-tidy-1013.md §4.2 / §8.1 ①)。

純文字處理,不寫檔、不跑指令(寫檔與重新登記指紋在 memory_tidy)。
  ## 身分
  - 性別:女
  - 生日:1996-09-10            ← 年紀、星座不存,讀的時候由生日算(三欄不會對不上)
  - 嗜好:拆收音機、整理接線圖
  ## 寫作規則(12 維)
  - 表達語氣:第一句講能不能用。好:「設計稿寫好了,還不能用」壞:「以下是詳細說明」〔AIB · 10-13 01:05 · a seat · claude-opus-5-5 · 證據:「第一句就是現在能不能用」〕
  (每維最多 3 條,新的在上;本輪沒觀察到的維度原內容不動)

大變動判定(第一艦長人格卡用;the maintainer 10-13:小細節自動,大變動才問):
  動到紅線 / 職責 / 身分;刪掉或改寫 12 維以外的既有句子;同一維一次換超過 1 條;12 維以外改超過 15 行或 20%。
"""
from __future__ import annotations

import datetime
import re

# 人格風格 12 維(名字正本 card_registry.PERSONA_STYLE_DIMS;這裡只用中文名比對卡上的行)
DIMS = ("表達語氣", "決策風格", "風險底線", "產品品味", "工作節奏", "學習方式",
        "客服風格", "行銷風格", "技術治理", "遊戲偏好", "創作偏好", "可分享人格")
DIM_SECTION = "寫作規則(12 維)"
ID_SECTION = "身分"
ID_FIELDS = ("性別", "生日", "嗜好")
UNSET = "未設定"
PER_DIM = 3
BIG_SECTIONS = ("紅線", "職責", ID_SECTION)
BIG_LINES = 15
BIG_RATIO = 0.20
_FM_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n?", re.S)
_H2_RE = re.compile(r"^##(?!#)\s*(.+?)\s*$")
_PROV_RE = re.compile(r"〔[^〕]*〕\s*$")
ZODIAC = ((1, 20, "摩羯座"), (2, 19, "水瓶座"), (3, 21, "雙魚座"), (4, 20, "牡羊座"), (5, 21, "金牛座"),
          (6, 22, "雙子座"), (7, 23, "巨蟹座"), (8, 23, "獅子座"), (9, 23, "處女座"), (10, 24, "天秤座"),
          (11, 23, "天蠍座"), (12, 22, "射手座"), (12, 32, "摩羯座"))


def split(text):
    """→ (frontmatter, [(段名, 內文行列)]);標題前的開頭那段段名是空字串。"""
    m = _FM_RE.match(text or "")
    fm = m.group(0) if m else ""
    body = (text or "")[len(fm):]
    out, name, buf = [], "", []
    for ln in body.split("\n"):
        h = _H2_RE.match(ln)
        if h:
            out.append((name, buf))
            name, buf = h.group(1), []
        else:
            buf.append(ln)
    out.append((name, buf))
    return fm, out


def join(fm, secs):
    parts = []
    for name, lines in secs:
        if name:
            parts.append("## %s" % name)
        parts.extend(lines)
    return fm + "\n".join(parts)


def _section(secs, name):
    for n, lines in secs:
        if (n == name or n.startswith(name + "(") or n.startswith(name + "(")
                or (name == DIM_SECTION and n.startswith("寫作規則"))):
            return lines
    return None


# ---------------------------------------------------------------- 身分
def identity(text):
    """→ {性別, 生日, 嗜好};沒有或空 = 未設定。"""
    _fm, secs = split(text)
    out = {k: UNSET for k in ID_FIELDS}
    for ln in _section(secs, ID_SECTION) or []:
        m = re.match(r"^\s*-\s*(性別|生日|嗜好)\s*[::]\s*(.*)$", ln)
        if m and m.group(2).strip():
            out[m.group(1)] = m.group(2).strip()
    return out


def age_zodiac(birthday, today=None):
    """生日 YYYY-MM-DD → (年紀, 星座);讀不懂 ⇒ (None, None)。"""
    try:
        b = datetime.date.fromisoformat(str(birthday).strip()[:10])
    except ValueError:
        return None, None
    t = today or datetime.date.today()
    age = t.year - b.year - ((t.month, t.day) < (b.month, b.day))
    sign = next(s for mo, d, s in ZODIAC if (b.month, b.day) < (mo, d) or (mo, d) == (12, 32))
    return age, sign


def set_identity(text, values):
    """補上 / 改 身分段(只改給的欄;沒有身分段就加在職責後面、沒有職責就加在最後)。"""
    fm, secs = split(text)
    cur = identity(text)
    cur.update({k: v for k, v in (values or {}).items() if k in ID_FIELDS and v})
    lines = ["- %s:%s" % (k, cur[k]) for k in ID_FIELDS] + [""]
    for i, (n, _l) in enumerate(secs):
        if n == ID_SECTION:
            secs[i] = (n, lines)
            return join(fm, secs)
    at = next((i + 1 for i, (n, _l) in enumerate(secs) if n == "職責"), len(secs))
    secs.insert(at, (ID_SECTION, lines))
    return join(fm, secs)


def identity_line(text, today=None):
    """人格包裡那一行:性別 · 生日(N 歲 · 星座)· 嗜好;全部未設定 ⇒ 空字串。"""
    idt = identity(text)
    if all(v == UNSET for v in idt.values()):
        return ""
    bd = idt["生日"]
    age, sign = age_zodiac(bd, today) if bd != UNSET else (None, None)
    bd_text = "%s(%d 歲 · %s)" % (bd, age, sign) if age is not None else bd
    return "身分:%s · 生日 %s · 嗜好 %s" % (idt["性別"], bd_text, idt["嗜好"])


# ---------------------------------------------------------------- 12 維
def dims(text):
    """→ {維度: [條, …]}(新的在上);沒有這段 ⇒ 全空。"""
    _fm, secs = split(text)
    out = {d: [] for d in DIMS}
    for ln in _section(secs, DIM_SECTION) or []:
        m = re.match(r"^\s*-\s*(\S+?)\s*[::]\s*(.+)$", ln)
        if m and m.group(1) in out:
            out[m.group(1)].append(m.group(2).strip())
    return out


def set_dims(text, new):
    """new = {維度: [新條, …]}:新的放上面、同句去重、每維留 PER_DIM 條;沒給的維度原內容不動。
    回 (新卡文字, 被擠掉的條 [(維度, 條)])。"""
    cur = dims(text)
    dropped = []
    for d, items in (new or {}).items():
        if d not in cur:
            continue
        merged, seen = [], set()
        for it in list(items) + cur[d]:
            it = it.strip()
            key = _PROV_RE.sub("", it).strip()          # 同一條規則(只差〔來源〕)只留最新那條
            if it and key not in seen:
                seen.add(key)
                merged.append(it)
        dropped += [(d, x) for x in merged[PER_DIM:]]
        cur[d] = merged[:PER_DIM]
    lines = ["- %s:%s" % (d, it) for d in DIMS for it in cur[d]] + [""]
    fm, secs = split(text)
    for i, (n, _l) in enumerate(secs):
        if n == DIM_SECTION or n.startswith(DIM_SECTION.split("(")[0]):   # 段名變體也認同一段,不在卡尾再加一段
            keep = [ln for ln in _l if ln.strip() and not _is_dim_line(ln)]
            secs[i] = (n, keep + lines)                    # 不是「- 維度:」的行原樣留著,不悄悄刪
            return join(fm, secs), dropped
    if any(cur[d] for d in DIMS):
        secs.append((DIM_SECTION, lines))
    return join(fm, secs), dropped


def _is_dim_line(ln):
    m = re.match(r"^\s*-\s*(\S+?)\s*[::]", ln)
    return bool(m) and m.group(1) in DIMS


def pack_rules(text):
    """人格包用:每維只取最新一條、拿掉〔來源〕尾巴;沒內容的維度不印。"""
    out = []
    for d, items in dims(text).items():
        if items:
            out.append("- %s:%s" % (d, _PROV_RE.sub("", items[0]).strip()))
    return out


# ---------------------------------------------------------------- 大變動
def big_change(old, new):
    """→ (是不是大變動, 理由清單)。"""
    why = []
    _f1, s_old = split(old)
    _f2, s_new = split(new)
    for name in BIG_SECTIONS:
        a, b = _section(s_old, name), _section(s_new, name)
        if [x.rstrip() for x in (a or []) if x.strip()] != [x.rstrip() for x in (b or []) if x.strip()]:
            if not (name == ID_SECTION and a is None and all(v == UNSET for v in identity(new).values())):
                why.append("動到「%s」" % name)
    old_lines = {ln.rstrip() for n, ls in s_old if n != DIM_SECTION and n not in BIG_SECTIONS for ln in ls if ln.strip()}
    new_lines = {ln.rstrip() for n, ls in s_new if n != DIM_SECTION and n not in BIG_SECTIONS for ln in ls if ln.strip()}
    gone = old_lines - new_lines
    if gone:
        why.append("刪掉或改寫既有句子 %d 句" % len(gone))
    d_old, d_new = dims(old), dims(new)
    for d in DIMS:
        replaced = [x for x in d_old[d] if x not in d_new[d]]
        if len(replaced) > 1:
            why.append("「%s」一次換掉 %d 條" % (d, len(replaced)))
    # 行數 / 比例只算 12 維以外(12 維本來就是每輪會加的,不算大變動;它自己的規則在上面那條)
    skip = (DIM_SECTION,) + BIG_SECTIONS        # 這幾段各有自己的規則(上面),行數不重複算
    a_lines = [x.rstrip() for n, ls in s_old if n not in skip for x in ls if x.strip()]
    b_lines = [x.rstrip() for n, ls in s_new if n not in skip for x in ls if x.strip()]
    changed = len(set(a_lines) ^ set(b_lines))
    if changed > BIG_LINES or (a_lines and changed / max(len(a_lines), 1) > BIG_RATIO):
        why.append("整張卡改了 %d 行" % changed)
    return bool(why), why
