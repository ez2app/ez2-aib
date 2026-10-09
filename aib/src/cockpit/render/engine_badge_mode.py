# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""R6 引擎徽章的**採集端**判定:三態 mode 與字型缺字退回(裁定 RULING-R6-engine-badge-icons-0830)。

★ 這一支住在採集端,可以讀 env 與字型檔;render_lib 不讀環境(黃金幀決定性),mode 由呼叫端注入。
★ 三條裁定:
  1. mode ∈ {both, icon, letters},預設 both;**只有即時 TTY 可以 both/icon**——落檔、貼給別席、進交接單一律 letters
     (930/292 事故:兩個人看到不同畫面用同一句話討論)。「即時 TTY」由 bash 包裝層 `[ -t 1 ]` 判後以
     EZ2_AIB_BADGE_TTY=1 注入;採集端 python 的 stdout 被重導到檔案,自己 isatty() 永遠是 False,不能拿來判。
  2. 字型缺字要退回 letters **並且說出來**:靜默退會讓「字型沒裝」與「這一席沒有引擎」同形。
     退回的理由由本函式回傳,呼叫端印到 stderr(.err 檔)。
  3. 字型探針讀 TTF 的 cmap(format 4/12),不用 fontTools(未安裝);字型檔由 EZ2_AIB_NERD_FONT 指定,
     預設掃 ~/Library/Fonts/*NerdFont*-Regular.ttf;找不到字型檔=缺字,不是綠。
"""
import glob
import os
import struct

BADGE_MODES = ("both", "icon", "letters")
MODE_ENV = "EZ2_AIB_ENGINE_BADGE"
TTY_ENV = "EZ2_AIB_BADGE_TTY"
FONT_ENV = "EZ2_AIB_NERD_FONT"
DEFAULT_FONT_GLOB = "~/Library/Fonts/*NerdFont*-Regular.ttf"


def ttf_codepoints(path):
    """回該 TTF cmap 裡所有字碼的集合(format 4 與 12);讀不懂回空集合(=缺字方向)。"""
    cps = set()
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        num_tables = struct.unpack(">H", data[4:6])[0]
        cmap_off = None
        for i in range(num_tables):
            rec = 12 + 16 * i
            tag = data[rec:rec + 4]
            if tag == b"cmap":
                cmap_off = struct.unpack(">I", data[rec + 8:rec + 12])[0]
                break
        if cmap_off is None:
            return cps
        n_sub = struct.unpack(">H", data[cmap_off + 2:cmap_off + 4])[0]
        for i in range(n_sub):
            rec = cmap_off + 4 + 8 * i
            off = struct.unpack(">I", data[rec + 4:rec + 8])[0]
            sub = cmap_off + off
            fmt = struct.unpack(">H", data[sub:sub + 2])[0]
            if fmt == 4:
                segx2 = struct.unpack(">H", data[sub + 6:sub + 8])[0]
                seg = segx2 // 2
                ends = struct.unpack(">%dH" % seg, data[sub + 14:sub + 14 + segx2])
                starts_off = sub + 16 + segx2
                starts = struct.unpack(">%dH" % seg, data[starts_off:starts_off + segx2])
                for s, e in zip(starts, ends):
                    if s == 0xFFFF:
                        continue
                    cps.update(range(s, e + 1))
            elif fmt == 12:
                ngroups = struct.unpack(">I", data[sub + 12:sub + 16])[0]
                for g in range(ngroups):
                    go = sub + 16 + 12 * g
                    s, e, _gid = struct.unpack(">III", data[go:go + 12])
                    cps.update(range(s, e + 1))
    except (OSError, struct.error, ValueError):
        return set()
    return cps


def font_paths(environ=None):
    e = os.environ if environ is None else environ
    forced = e.get(FONT_ENV)
    if forced:
        return [forced]
    return sorted(glob.glob(os.path.expanduser(DEFAULT_FONT_GLOB)))


def missing_glyphs(codepoints, paths):
    """回 (缺的字碼列表, 用了哪個字型檔);沒有字型檔 ⇒ 全缺、檔名 None。"""
    if not paths:
        return sorted(codepoints), None
    path = paths[0]
    have = ttf_codepoints(path)
    return sorted(cp for cp in codepoints if cp not in have), path


def resolve_badge_mode(environ=None, *, icon_codepoints, tty=None, paths=None):
    """回 (mode, reason)。reason 非空 = 退回了 letters 且要說出來。

    - requested = EZ2_AIB_ENGINE_BADGE(預設 both;不認得的值當 both 並在 reason 說)
    - tty=False(或 EZ2_AIB_BADGE_TTY != "1")⇒ letters(會被引用的輸出鎖標準模式)
    - 字型檔缺 / 任一 icon 字碼不在 cmap ⇒ letters + 理由(含缺的字碼)
    """
    e = os.environ if environ is None else environ
    requested = (e.get(MODE_ENV) or "both").strip().lower()
    note = ""
    if requested not in BADGE_MODES:
        note = "%s=%r 不認得,當 both;" % (MODE_ENV, requested)
        requested = "both"
    if tty is None:
        tty = (e.get(TTY_ENV) == "1")
    if not tty:
        return "letters", ""                       # 非即時 TTY:鎖標準模式,不是退回,不用出聲
    if requested == "letters":
        return "letters", ""
    missing, path = missing_glyphs(set(icon_codepoints), font_paths(e) if paths is None else paths)
    if path is None:
        return "letters", note + "字型缺:找不到 Nerd Font 檔(%s 或 %s),徽章退回字母" % (FONT_ENV, DEFAULT_FONT_GLOB)
    if missing:
        return "letters", note + "字型缺字 %s(%s),徽章退回字母" % (
            ",".join("U+%04X" % cp for cp in missing), os.path.basename(path))
    return requested, note


def badge_inputs(environ=None, *, icons, tty=None, paths=None):
    """採集端一次算好要注入渲染器的三樣:{"mode","icon_available":{provider:bool},"reason"}。

    icons = render_lib.ENGINE_ICONS。mode=requested(非 TTY 鎖 letters);icon_available 逐 provider
    (字碼不在 cmap 或字型檔缺 ⇒ False);reason 只在有 provider 退回時非空(呼叫端印 stderr)。
    退回本身由渲染器 resolve_engine_badge 執行並在 rowmap detail 具名(font_missing)。"""
    e = os.environ if environ is None else environ
    requested = (e.get(MODE_ENV) or "both").strip().lower()
    note = ""
    if requested not in BADGE_MODES:
        note = "%s=%r 不認得,當 both;" % (MODE_ENV, requested)
        requested = "both"
    if tty is None:
        tty = (e.get(TTY_ENV) == "1")
    if not tty:
        return {"mode": "letters", "icon_available": {p: True for p in icons}, "reason": ""}
    cps = {ord(ch) for ch in icons.values()}
    missing, path = missing_glyphs(cps, font_paths(e) if paths is None else paths)
    available = {p: (ord(ch) not in missing) for p, ch in icons.items()}
    if requested != "letters" and not all(available.values()):
        lost = sorted(p for p, ok in available.items() if not ok)
        if path is None:
            note += "字型缺:找不到 Nerd Font 檔(%s 或 %s),%s 徽章退回字母" % (FONT_ENV, DEFAULT_FONT_GLOB, ",".join(lost))
        else:
            note += "字型缺字 %s(%s),%s 徽章退回字母" % (
                ",".join("U+%04X" % cp for cp in missing), os.path.basename(path), ",".join(lost))
    return {"mode": requested, "icon_available": available, "reason": note}
