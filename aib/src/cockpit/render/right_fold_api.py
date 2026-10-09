# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""274 右欄收合/視窗/樹軌 —— 三簽名寫死(RL-030)。

落碼席填本體,不得改名、不得改參數順序。
render_lib 不讀檔:load/save 只給採集端叫。
PAGE 不碰。stdin 不讀。
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile

FOLD_SCHEMA = "aib-right-fold/v1"
SCROLL_SCHEMA = "aib-right-scroll/v1"
ALERT_MARKS = ("🔴", "待真人裁", "卡稿", "額度到")
SUMMARY_ZH = "{n} 席・{m} 做中・{k} 紅"


def _atomic_write(path, obj):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class fold_state(object):
    @staticmethod
    def load(path):
        """回 expanded dict。無檔/壞檔/schema 不對 ⇒ {}。不丟。"""
        try:
            raw = pathlib.Path(path).read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(data, dict):
            return {}
        if data.get("schema") != FOLD_SCHEMA:
            return {}
        expanded = data.get("expanded")
        if not isinstance(expanded, dict):
            return {}
        return dict(expanded)

    @staticmethod
    def save(path, expanded):
        """原子寫 {schema:aib-right-fold/v1, expanded}。"""
        payload = {
            "schema": FOLD_SCHEMA,
            "expanded": dict(expanded or {}),
        }
        _atomic_write(path, payload)

    @staticmethod
    def toggle(expanded, key):
        """回新 dict,不改入參。缺鍵或非 True → True;True → 刪鍵。"""
        out = dict(expanded or {})
        if out.get(key) is True:
            del out[key]
        else:
            out[key] = True
        return out

    @staticmethod
    def is_folded(expanded, key):
        """缺鍵或非 True ⇒ True(收合)。"""
        return (expanded or {}).get(key) is not True


class viewport(object):
    SCHEMA = "aib-right-scroll/v1"

    @staticmethod
    def offset(state):
        """scroll dict → int。缺/schema 不對 ⇒ 0。"""
        if not isinstance(state, dict):
            return 0
        if state.get("schema") != viewport.SCHEMA:
            return 0
        try:
            return int(state.get("offset", 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def tab_offset(state, tab):
        """分頁各自的捲動。ai 沒寫 tabs 時沿用舊的單一 offset。"""
        if not isinstance(state, dict):
            return 0
        tabs = state.get("tabs")
        if isinstance(tabs, dict) and tab in tabs:
            try:
                return max(0, int(tabs[tab]))
            except (TypeError, ValueError):
                return 0
        if tab == "ai":
            return viewport.offset(state)
        return 0

    @staticmethod
    def with_tab(state, tab, offset):
        """寫回某一頁的 offset。ai 頁同時維持舊的頂層 offset，舊讀者不會歸零。"""
        base = dict(state) if isinstance(state, dict) else {}
        if base.get("schema") != viewport.SCHEMA:
            base = {"schema": viewport.SCHEMA, "offset": viewport.offset(base)}
        tabs = base.get("tabs")
        if tab == "ai" and not isinstance(tabs, dict):
            return {"schema": viewport.SCHEMA, "offset": int(offset)}
        tabs = dict(tabs or {})
        tabs[str(tab)] = int(offset)
        base["tabs"] = tabs
        if tab == "ai":
            base["offset"] = int(offset)
        return base

    @staticmethod
    def clamp(offset, n, h):
        """夾進 [0, max(0, n-h)]。"""
        try:
            nn = int(n)
        except (TypeError, ValueError):
            nn = 0
        try:
            hh = int(h)
        except (TypeError, ValueError):
            hh = 0
        if nn < 0:
            nn = 0
        if hh < 1:
            return 0
        try:
            o = int(offset)
        except (TypeError, ValueError):
            o = 0
        max_off = max(0, nn - hh)
        if o < 0:
            return 0
        if o > max_off:
            return max_off
        return o

    @staticmethod
    def slice(rows, offset, h):
        """rows[offset:offset+h],先 clamp。(e)5:h<3 只回「▼ 還有 n 席」一列。"""
        rows = list(rows or ())
        n = len(rows)
        try:
            hh = int(h)
        except (TypeError, ValueError):
            hh = 0
        o = viewport.clamp(offset, n, hh if hh >= 1 else 1)
        if hh < 3:
            rest = max(0, n - o)
            return ["▼ 還有 %d 席" % rest]
        return rows[o:o + hh]

    @staticmethod
    def scrollbar_glyphs(offset, n, h, cell=False):
        """長度 h 的 ░/█ 列;n<=h ⇒ []。"""
        try:
            nn = int(n)
            hh = int(h)
        except (TypeError, ValueError):
            return []
        if hh < 1 or nn <= hh:
            return []
        o = viewport.clamp(offset, nn, hh)
        thumb_h = max(1, int(round(hh * hh / float(max(hh, nn)))))
        span = max(0, hh - thumb_h)
        den = max(1, nn - hh)
        thumb_y = int(round(o / float(den) * span))
        if thumb_y < 0:
            thumb_y = 0
        if thumb_y + thumb_h > hh:
            thumb_y = max(0, hh - thumb_h)
        # 用量頁用一格反白。░/█ 在這台終端機會佔兩格，把整列擠換行。
        if cell:
            thumb = "\033[7m \033[0m"
            track = "\033[48;5;238m \033[0m"
        else:
            thumb, track = "█", "░"
        out = []
        for i in range(hh):
            out.append(thumb if thumb_y <= i < thumb_y + thumb_h else track)
        return out


def tree_rails(rows):
    """與 rows 等長的前綴字串 list。含子列(persona/brain)。

    用 9ae57 0f6bd8ae:上層未完一欄兩格(│ 或空白);本列 ├ 或 ╰。
    一層續軌:╰ 在 index 2;兩層:index 4。艦長主列(level 0 main、無續軌)前綴空。
    """
    if not rows:
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            out.append("")
            continue
        kind = str(row.get("kind") or "main")
        last = bool(row.get("last_sibling"))
        cont = tuple(row.get("ancestors_continue") or ())
        parts = [("│ " if c else "  ") for c in cont]
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        if kind == "main" and level == 0 and not cont:
            out.append("".join(parts))
            continue
        glyph = "╰" if last else "├"
        out.append("".join(parts) + glyph)
    return out


def fold_summary(n, m, k):
    """列尾摘要。契約 zh-TW:`{n} 席・{m} 做中・{k} 紅`。"""
    return SUMMARY_ZH.format(n=n, m=m, k=k)


def should_force_expand(member_texts):
    """(e)1 收合不得藏警報。底下有 🔴/待真人裁/卡稿/額度到 ⇒ True。"""
    for text in member_texts or ():
        s = text if isinstance(text, str) else str(text)
        for mark in ALERT_MARKS:  # 274e1-alert-marks
            if mark in s:
                return True
    return False
